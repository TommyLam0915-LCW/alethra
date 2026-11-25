"""Per-fill export of a formal research run, driven over real HTTP.

Every state an export can be asked about is produced the way the platform
produces it: an experiment through ``/api/v1/experiments/``, a run through
``/api/v1/versions/{id}/runs`` on the stored-data lane with the real engine, a
cancellation through ``/api/v1/runs/{id}/cancel``. Nothing here inserts a
transaction row by hand to make an export look right.

What is asserted is the part a row count cannot express: that the four states
which have no fills — failed, cancelled, still computing, completed without a
transaction record — are answered differently from the one state that genuinely
filled nothing. An export that flattened those into an empty file would turn a
crash into evidence about a strategy.

Persistence, restart and account isolation are exercised at this layer only
(SQLite, whose test engine shares one connection through ``StaticPool``, so
"another session" is not an independent PostgreSQL connection here). A
PostgreSQL run and a browser are separate evidence levels and are not claimed.
"""

import csv
import io
from datetime import date, timedelta
from uuid import UUID

import pytest

from app.engine.attribution import annotate_and_attribute
from app.engine.fill_math import BUY
from app.engine.strategies import generate_transaction
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from app.models.user import User
from app.providers.data_provider import OHLCVBar
from app.services import research_runs as research_run_service
from app.services.market_data_storage import MarketDataStorage
from app.services.research_runs import execute_backtest_run
from app.services.run_trades import (
    ENGINE_TRANSACTION_KEYS,
    PROVENANCE_KEYS,
    _csv_cell,
)
from tests.conftest import TestSession

_SPEC = {
    "template_type": "buy_and_hold",
    "asset_ids": ["US:AAPL"],
    "frequency": "1d",
    "start_date": "2024-01-02",
    "end_date": "2024-03-15",
    "initial_cash": 100000.0,
    "commission_rate": 0.001,
    "slippage_rate": 0.0,
}

# A real signal that never fires on 55 bars: the long window needs more bars
# than are stored, so the engine completes the run having filled nothing.
_NO_FILL_SPEC = dict(
    _SPEC,
    template_type="sma_crossover",
    signal_parameters={"short_window": 5, "long_window": 200},
)

_PROVIDER = "synthetic"
_OWNER_EMAIL = "test@alethra.io"

# The response envelope frontend/src/lib/runExport.ts declares as
# RunTradesExport. Both ends pin against this list.
_ENVELOPE_KEYS = {
    "run_id",
    "experiment_id",
    "experiment_version_id",
    "version_number",
    "owner_id",
    "status",
    "export_basis",
    "basis_note",
    "trade_count",
    "columns",
    "provenance",
    "trades",
}


def _weekdays(start: date, count: int):
    days = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _bars(days, base_price=150.0):
    return [
        OHLCVBar(
            date=day,
            open=base_price + i,
            high=base_price + i + 2,
            low=base_price + i - 1,
            close=base_price + i + 1,
            volume=1_000_000,
        )
        for i, day in enumerate(days)
    ]


@pytest.fixture()
def stored_dataset():
    db = TestSession()
    try:
        days = _weekdays(date(2024, 1, 2), 55)
        MarketDataStorage(db).store_bars(
            "US:AAPL", _PROVIDER, _bars(days), "synthetic"
        )
    finally:
        db.close()
    return days


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def _headers(client, email, password="export-pass-123"):
    client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
            "email": email,
            "password": password,
            "display_name": "Export Tester",
            "user_type": "individual",
        },
    )
    login = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create_experiment(client, headers, spec=None):
    resp = client.post(
        "/api/v1/experiments/",
        json={
            "title": "Trade export slice",
            "hypothesis": "A run's fills must leave the platform with their identity.",
            "strategy_spec": spec or _SPEC,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return UUID(resp.json()["current_version_id"])


def _run(client, headers, version_id):
    resp = client.post(
        f"/api/v1/versions/{version_id}/runs",
        params={"data_source": "stored", "provider_id": _PROVIDER},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _completed_run(client, auth_headers, spec=None):
    version_id = _create_experiment(client, auth_headers, spec)
    run = _run(client, auth_headers, version_id)
    assert run["status"] == "completed", run
    return run


class _ProcessDied(BaseException):
    """Not an ``Exception``: the lane's own failure handler never runs."""


def _interrupted_attempt(client, auth_headers, db, monkeypatch, version_id):
    """A committed ``running`` row whose process stopped — no outcome published."""
    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == version_id
    ).one()
    experiment = db.query(Experiment).filter(
        Experiment.id == version.experiment_id
    ).one()
    user = db.query(User).filter(User.email == _OWNER_EMAIL).one()

    def _die(*args, **kwargs):
        raise _ProcessDied()

    monkeypatch.setattr(research_run_service, "run_backtest", _die)
    with pytest.raises(_ProcessDied):
        execute_backtest_run(
            db,
            user=user,
            version=version,
            experiment=experiment,
            data_source="stored",
            provider_id=_PROVIDER,
        )
    db.rollback()
    run = db.query(ResearchRun).filter(
        ResearchRun.experiment_version_id == version_id
    ).one()
    assert run.status == RunStatus.running
    return run


def _stored_run(run_id):
    fresh = TestSession()
    try:
        return fresh.query(ResearchRun).filter(ResearchRun.id == run_id).one()
    finally:
        fresh.close()


# ── 一、a completed run: the fills, in the engine's own words ────────────────


def test_a_completed_run_exports_every_fill_it_recorded(
    client, auth_headers, stored_dataset
):
    run = _completed_run(client, auth_headers)

    resp = client.get(f"/api/v1/runs/{run['id']}/trades", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    stored = _stored_run(UUID(run["id"])).result_json
    assert payload["export_basis"] == "completed_with_trades"
    assert payload["trade_count"] == len(stored["transactions"])
    assert payload["trades"], "the stored lane recorded fills, so rows must exist"
    assert payload["status"] == "completed"


def test_the_export_keeps_the_engine_field_names_and_drops_none(
    client, auth_headers, stored_dataset
):
    """Renaming or silently dropping a fill column is a data change, not a view."""
    run = _completed_run(client, auth_headers)
    payload = client.get(
        f"/api/v1/runs/{run['id']}/trades", headers=auth_headers
    ).json()
    stored = _stored_run(UUID(run["id"])).result_json

    first_recorded = stored["transactions"][0]
    columns = set(payload["columns"])
    for key in first_recorded:
        assert key in columns, f"the engine wrote {key}; the export must not lose it"

    row = payload["trades"][0]
    for key, value in first_recorded.items():
        assert row[key] == value, f"{key} must export the stored value unchanged"

    # The journal lane's vocabulary is not borrowed here: those names describe a
    # different cash convention, and a shared name would imply a shared number.
    for journal_only in ("net_pnl", "trade_index", "cost_ratio_pct", "rule_id"):
        assert journal_only not in columns


def test_every_exported_row_carries_the_identity_of_the_run_that_produced_it(
    client, auth_headers, stored_dataset
):
    run = _completed_run(client, auth_headers)
    payload = client.get(
        f"/api/v1/runs/{run['id']}/trades", headers=auth_headers
    ).json()
    stored = _stored_run(UUID(run["id"]))

    provenance = payload["provenance"]
    assert provenance["run_id"] == run["id"]
    assert provenance["config_hash"] == stored.config_hash
    assert provenance["engine_version"] == stored.engine_version
    assert provenance["dataset_checksum"] == run["dataset_checksum"]
    assert provenance["window_start"] == stored.result_json["start_date"]
    assert provenance["window_end"] == stored.result_json["end_date"]
    assert provenance["data_nature"] == stored.result_json["data_nature"]
    assert provenance["data_provider"] == stored.result_json["data_provider"]
    assert provenance["experiment_id"] == payload["experiment_id"]
    assert provenance["version_number"] == payload["version_number"]

    for row in payload["trades"]:
        for key in PROVENANCE_KEYS:
            assert row[key] == provenance[key], (
                f"{key} must be stamped on every row, not only on the envelope"
            )


def test_the_csv_route_and_the_json_route_answer_from_one_decision(
    client, auth_headers, stored_dataset
):
    run = _completed_run(client, auth_headers)
    payload = client.get(
        f"/api/v1/runs/{run['id']}/trades", headers=auth_headers
    ).json()

    resp = client.get(f"/api/v1/runs/{run['id']}/trades.csv", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert (
        f'filename="run-{run["id"]}-trades.csv"'
        in resp.headers["content-disposition"]
    )

    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0] == payload["columns"], "the header is the JSON route's own column list"
    assert len(rows) - 1 == payload["trade_count"]
    assert rows[1][rows[0].index("run_id")] == run["id"]


# ── 二、the states that have no fills are not one empty file ─────────────────


def test_a_run_that_filled_nothing_is_an_empty_export_not_a_failure(
    client, auth_headers, stored_dataset
):
    run = _completed_run(client, auth_headers, _NO_FILL_SPEC)

    payload = client.get(
        f"/api/v1/runs/{run['id']}/trades", headers=auth_headers
    ).json()
    assert payload["export_basis"] == "completed_without_trades"
    assert payload["trade_count"] == 0
    assert payload["trades"] == []
    assert "nothing was filled" in payload["basis_note"]

    csv_resp = client.get(
        f"/api/v1/runs/{run['id']}/trades.csv", headers=auth_headers
    )
    assert csv_resp.status_code == 200, csv_resp.text
    assert list(csv.reader(io.StringIO(csv_resp.text)))[1:] == []


def test_a_failed_run_is_refused_because_it_produced_no_evidence(
    client, auth_headers, stored_dataset, monkeypatch
):
    def explode(spec, *args, **kwargs):
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(research_run_service, "run_backtest", explode)
    version_id = _create_experiment(client, auth_headers)
    run = _run(client, auth_headers, version_id)
    assert run["status"] == "failed", run

    for path in (f"/api/v1/runs/{run['id']}/trades", f"/api/v1/runs/{run['id']}/trades.csv"):
        resp = client.get(path, headers=auth_headers)
        assert resp.status_code == 409, (path, resp.text)
        detail = resp.json()["detail"]
        assert detail["error"] == "run_failed"
        assert detail["run_status"] == "failed"
        assert "no fills to export" in detail["message"]


def test_a_cancelled_run_is_refused_and_not_read_as_zero_trades(
    client, auth_headers, stored_dataset, db, monkeypatch
):
    version_id = _create_experiment(client, auth_headers)
    run = _interrupted_attempt(client, auth_headers, db, monkeypatch, version_id)
    assert client.post(
        f"/api/v1/runs/{run.id}/cancel", headers=auth_headers
    ).status_code == 200

    for path in (f"/api/v1/runs/{run.id}/trades", f"/api/v1/runs/{run.id}/trades.csv"):
        resp = client.get(path, headers=auth_headers)
        assert resp.status_code == 409, (path, resp.text)
        detail = resp.json()["detail"]
        assert detail["error"] == "run_cancelled"
        assert detail["run_status"] == "cancelled"
        assert "/cancel" in detail["message"], "the refusal says how it got here"


def test_a_run_still_computing_is_refused_as_not_finished(
    client, auth_headers, stored_dataset, db, monkeypatch
):
    version_id = _create_experiment(client, auth_headers)
    run = _interrupted_attempt(client, auth_headers, db, monkeypatch, version_id)

    resp = client.get(f"/api/v1/runs/{run.id}/trades", headers=auth_headers)
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "run_not_finished"
    assert detail["run_status"] == "running"
    assert client.get(
        f"/api/v1/runs/{run.id}/trades.csv", headers=auth_headers
    ).status_code == 409


def test_a_completed_row_with_no_transaction_record_is_not_an_empty_export(
    client, auth_headers, stored_dataset, db
):
    """The save-failure state, imposed on a stored row.

    The formal writer always sets ``transactions``, so this row cannot be made
    by the product path; it is the row a legacy or damaged store holds. The
    export is required to distinguish it from a run that genuinely filled
    nothing, which is exactly the distinction the product cannot make afterwards.
    """
    run = _completed_run(client, auth_headers)
    row = db.query(ResearchRun).filter(ResearchRun.id == UUID(run["id"])).one()
    row.result_json = {"data_provider": _PROVIDER, "start_date": "2024-01-02"}
    db.commit()

    for path in (f"/api/v1/runs/{run['id']}/trades", f"/api/v1/runs/{run['id']}/trades.csv"):
        resp = client.get(path, headers=auth_headers)
        assert resp.status_code == 409, (path, resp.text)
        detail = resp.json()["detail"]
        assert detail["error"] == "result_not_recorded"
        assert "on evidence the record does not contain" in detail["message"]


# ── 三、the export reads, and only reads ────────────────────────────────────


def test_the_export_writes_nothing_to_the_run_the_ledger_already_settled(
    client, auth_headers, stored_dataset
):
    run = _completed_run(client, auth_headers)
    before = _stored_run(UUID(run["id"]))
    snapshot = (
        before.status,
        before.completed_at,
        before.result_json,
        before.metrics,
    )

    client.get(f"/api/v1/runs/{run['id']}/trades", headers=auth_headers)
    client.get(f"/api/v1/runs/{run['id']}/trades.csv", headers=auth_headers)

    after = _stored_run(UUID(run["id"]))
    assert (
        after.status,
        after.completed_at,
        after.result_json,
        after.metrics,
    ) == snapshot


# ── 四、two accounts ─────────────────────────────────────────────────────────


def test_another_account_gets_the_same_answer_as_a_run_that_does_not_exist(
    client, auth_headers, stored_dataset
):
    run = _completed_run(client, auth_headers)
    stranger = _headers(client, "stranger-export@alethra.io")
    missing = str(UUID(int=0))

    for path in ("trades", "trades.csv"):
        foreign = client.get(f"/api/v1/runs/{run['id']}/{path}", headers=stranger)
        absent = client.get(f"/api/v1/runs/{missing}/{path}", headers=stranger)
        assert foreign.status_code == 404, (path, foreign.text)
        assert absent.status_code == 404, (path, absent.text)
        assert foreign.json() == absent.json(), (
            "a run id must not become an oracle for another account"
        )
        owner = client.get(f"/api/v1/runs/{run['id']}/{path}", headers=auth_headers)
        assert owner.status_code == 200, (path, owner.text)


def test_a_stranger_cannot_reach_the_rows_that_belong_to_the_owner(
    client, auth_headers, stored_dataset
):
    run = _completed_run(client, auth_headers)
    stranger = _headers(client, "second-stranger@alethra.io")

    foreign = client.get(f"/api/v1/runs/{run['id']}/trades", headers=stranger)
    assert "transaction_id" not in foreign.text
    assert run["id"] not in foreign.text

    own = client.get(f"/api/v1/runs/{run['id']}/trades", headers=auth_headers).json()
    assert own["trade_count"] > 0
    assert own["trades"][0]["transaction_id"]


# ── 五、contract drift is caught at the source, not in the export ────────────


def test_the_column_list_is_what_the_fill_record_actually_carries():
    """Both writers of a stored fill, measured together, not described apart.

    ``generate_transaction`` builds the leg and ``annotate_and_attribute``
    stamps the plan and the matched result onto the same row before the run
    stores it. The export's column list has to name exactly that union: a new
    field breaks here, before it can go missing from a customer's CSV.
    """
    fill = generate_transaction(
        signal_date="2024-01-02",
        execution_date="2024-01-03",
        symbol="US:AAPL",
        side=BUY,
        quantity=10,
        observed_price=150.0,
        execution_price=150.1,
        slippage_rate=0.0,
        commission_rate=0.001,
        cash_before=100000.0,
        shares_after=10,
        rationale="test fill",
    )
    annotate_and_attribute(
        [fill],
        strategy_spec={"risk_parameters": {}},
        config_hash="abc",
        actor="strategy",
    )
    assert set(fill) == set(ENGINE_TRANSACTION_KEYS), (
        "ENGINE_TRANSACTION_KEYS must name exactly the keys the two writers put "
        "on a stored fill; the export adds no column of its own and loses none"
    )


def test_the_response_carries_exactly_the_fields_the_frontend_types_declare(
    client, auth_headers, stored_dataset
):
    """The other end of ``frontend/src/lib/runExport.ts``.

    ``frontend/tests/runTradeExport.test.mjs`` parses that interface against the
    same list, so a field renamed on one side breaks a lane instead of quietly
    becoming a column nobody reads or a fact nobody displays.
    """
    run = _completed_run(client, auth_headers)
    body = client.get(
        f"/api/v1/runs/{run['id']}/trades", headers=auth_headers
    ).json()

    assert set(body) == _ENVELOPE_KEYS
    assert set(body["provenance"]) == set(PROVENANCE_KEYS)
    assert body["columns"] == list(ENGINE_TRANSACTION_KEYS) + list(PROVENANCE_KEYS)
    for row in body["trades"]:
        assert set(row) == set(body["columns"]), (
            "every row carries exactly the columns the header names"
        )


def test_a_stored_fill_introduces_no_column_the_export_did_not_name(
    client, auth_headers, stored_dataset
):
    run = _completed_run(client, auth_headers)
    stored = _stored_run(UUID(run["id"])).result_json
    for row in stored["transactions"]:
        assert set(row) <= set(ENGINE_TRANSACTION_KEYS), (
            f"{sorted(set(row) - set(ENGINE_TRANSACTION_KEYS))} is on a stored "
            "fill but not in the export's canonical list"
        )


def test_both_export_routes_are_registered_on_the_runs_router():
    from app.api import runs as runs_api

    registered = {r.path for r in runs_api.router.routes}
    assert "/runs/{run_id}/trades" in registered, sorted(registered)
    assert "/runs/{run_id}/trades.csv" in registered, sorted(registered)


def test_a_cell_that_looks_like_a_formula_cannot_be_executed_by_a_spreadsheet():
    assert _csv_cell("=SUM(A1)") == "'=SUM(A1)"
    assert _csv_cell("+1") == "'+1"
    assert _csv_cell("@cmd") == "'@cmd"
    assert _csv_cell("\tx") == "'\tx"
    # A number is still a number: escaping it would break the column it lands in.
    assert _csv_cell(-1.25) == "-1.25"
    assert _csv_cell(None) == ""
    assert _csv_cell(True) == "true"
