"""G3-4: a run whose stored series is gone stays not recomputable.

The evidence lane holds two completed runs that claim the same instrument, the
same provider ``warehouse-a``, the same 2026-08-01..2026-10-31 window and the
same 184-bar count, and record two different ``dataset_checksum`` values. That
table now holds **zero** rows for that provider in either lane database, and no
digest recipe reproduces either recorded checksum. Neither run has a
``dataset_snapshot_id`` — they finished before the registry existed.

So the numbers in those rows are measurements of a series nobody can point at
again. The owner's ruling is that they stay that way: not recomputable, not
performance evidence, and no snapshot minted afterwards to make the history
look whole. That is a product rule, not a reporting habit, so it is pinned
here as one.

Three things are asserted, in this order:

* the verdict is **measured** — it changes when the stored rows change, and
  only then, which is what makes it a reading of the table rather than a label
  copied off the run;
* the verdict **cannot be bought** — no read of it writes, and rows appearing
  under the recorded provider do not turn an absent series green;
* the verdict **reaches the surfaces** that would otherwise present a run's
  metrics as comparable performance.

The last section is the backfill gate. It counts, across all production source,
every place that assigns a snapshot to an existing run, and refuses a number
other than the one site that writes it inside the completion transaction.

Identities are namespaced to this file (``SUFFIX``). The harness database is a
file that outlives a pytest session and already holds ``US:AAPL`` rows written
by other lanes, so an instrument or provider shared with them would make these
counts describe someone else's data.
"""

from __future__ import annotations

import ast
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import event

from app.api.schemas import ResearchRunResponse
from app.auth.jwt import get_password_hash
from app.engine.backtest import _compute_stored_checksum, _stored_bars_to_dataframe
from app.models.dataset import DatasetSnapshot
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar
from app.models.research_run import ResearchRun, RunStatus
from app.models.user import User
from app.providers.data_provider import OHLCVBar
from app.services.market_data_storage import DAILY_INTERVAL_MINUTES, MarketDataStorage
from app.services.research_runs import execute_backtest_run
from app.services.run_reproducibility import (
    LEGS_DISAGREE,
    NO_RECORDED_CHECKSUM,
    NO_RECORDED_COVERAGE,
    NO_RECORDED_PROVIDER,
    NO_RECORDED_WINDOW,
    NO_RESULT_PAYLOAD,
    NOT_READ_FROM_STORAGE,
    NOT_REPRODUCIBLE,
    REPRODUCIBLE,
    STORED_ROW_COUNT_CHANGED,
    STORED_SERIES_ABSENT,
    STORED_SERIES_CHANGED,
    STORED_SERIES_REPRODUCED,
    UNVERIFIABLE,
    assess_run_reproducibility,
)
from tests.conftest import TestSession

SUFFIX = uuid.uuid4().hex[:8]
ASSET = f"US:REPRO-{SUFFIX}"
SECOND_ASSET = f"US:REPRO2-{SUFFIX}"
GONE_PROVIDER = f"repro-gone-{SUFFIX}"
LIVE_PROVIDER = f"repro-live-{SUFFIX}"
WINDOW = ("2026-08-03", "2026-08-07")
FOUR_H = 240
SPEC = {
    "template_type": "buy_and_hold",
    "asset_ids": [ASSET],
    "frequency": "1d",
    "start_date": WINDOW[0],
    "end_date": WINDOW[1],
    "initial_cash": 100000.0,
    "commission_rate": 0.001,
    "slippage_rate": 0.0,
}

#: The phrase ``compare_runs`` adds when one of the two runs cannot be
#: re-derived. Every refusal assertion below looks for it.
REFUSAL_PHRASE = "market_data_bars 現有列重算"


def _weekdays(start: date, count: int):
    days, cursor = [], start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _bars(days, asset=ASSET, base_price=150.0):
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
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def _digest(db, asset=ASSET, provider=LIVE_PROVIDER, window=WINDOW):
    """The engine's own digest over the rows now stored for one series.

    Taken with the engine's converter and the engine's recipe, so the fixtures
    assert what the reader will compute rather than what this file guesses. The
    provider is bound because that is what a run's coverage records; leaving it
    out would let another provider's rows into the digest.
    """
    db.flush()
    rows = (
        db.query(MarketDataBar)
        .filter(
            MarketDataBar.instrument_id == asset,
            MarketDataBar.provider_id == provider,
        )
        .all()
    )
    return _compute_stored_checksum(
        {asset: _stored_bars_to_dataframe(rows)},
        [asset],
        window[0],
        window[1],
    )


def _owner(db) -> User:
    """The harness identity, created when no earlier test has made it.

    The API surface tests below read runs back as this user, so it is the same
    one ``auth_headers`` authenticates.
    """
    user = db.query(User).filter(User.email == "test@alethra.io").one_or_none()
    if user is None:
        user = User(
            email="test@alethra.io",
            password_hash=get_password_hash("testpass123"),
            display_name="Test User",
        )
        db.add(user)
        db.commit()
    return user


def _version(db, spec):
    """One experiment and one version, the way the durable lane holds them."""
    user = _owner(db)
    experiment = Experiment(
        owner_id=user.id,
        title=f"reproducibility {uuid.uuid4()}",
        hypothesis="A stored run can still be read back.",
    )
    db.add(experiment)
    db.flush()
    version = ExperimentVersion(
        experiment_id=experiment.id,
        version_number=1,
        strategy_spec=spec,
        change_reason="fixture",
        created_by=user.id,
    )
    db.add(version)
    db.commit()
    return version


def _run_row(
    db,
    *,
    checksum=None,
    coverage=None,
    source="stored",
    window=WINDOW,
    snapshot=None,
    result_extra=None,
) -> ResearchRun:
    """One finished run, written the way a legacy row arrives: numbers, no proof.

    These rows are fixtures of the *reader* under test. The product never
    writes a run this way — it writes one at completion, through the
    transaction that registers the snapshot, which is exactly the asymmetry
    ``TestNoBackfillPath`` holds shut.
    """
    version = _version(db, SPEC)
    run = ResearchRun(
        experiment_version_id=version.id,
        dataset_snapshot_id=snapshot,
        dataset_checksum=checksum,
        engine_version="1.1.0",
        config_hash="0" * 64,
        status=RunStatus.completed,
        metrics={"total_return_pct": 4.2, "total_trades": 3},
        result_json={
            "data_source_used": source,
            "start_date": window[0],
            "end_date": window[1],
            "dataset_coverage": coverage,
            **(result_extra or {}),
        },
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(run)
    db.commit()
    return run


def _coverage(provider=GONE_PROVIDER, asset=ASSET, bar_count=184, **extra):
    entry = {
        "provider_id": provider,
        "provider_ids": [provider],
        "bar_count": bar_count,
        "first_bar_date": WINDOW[0],
        "last_bar_date": WINDOW[1],
        "data_nature": "live",
        "source": "stored",
    }
    entry.update(extra)
    return {asset: entry}


def _store(db, provider, days, *, asset=ASSET, base_price=150.0,
           grain=DAILY_INTERVAL_MINUTES):
    """Write one series through the product's own storage path.

    The grain is part of the write, as it is in the harvest path: a 1440 series
    and a 240 series over the same midnights are two rows each, which is the
    shape that gave one window two digests.
    """
    MarketDataStorage(db).store_bars(
        asset, provider, _bars(days, asset, base_price), "live",
        interval_minutes=grain,
    )
    db.commit()


def _real_stored_run(db, *, provider=LIVE_PROVIDER, asset=ASSET):
    """A run computed by the product itself over bars stored in this database."""
    days = _weekdays(date.fromisoformat(WINDOW[0]), 5)
    _store(db, provider, days, asset=asset)
    user = _owner(db)
    version = _version(db, dict(SPEC, asset_ids=[asset]))
    run = execute_backtest_run(
        db,
        user=user,
        version=version,
        experiment=version.experiment,
        data_source="stored",
        provider_id=provider,
        actor_type="test",
        # Billing is decided by admission and is not what this file tests; the
        # run row, its checksum and its coverage are written either way.
        charge_quota=False,
    )
    assert run.result_json is not None, "the run published no result payload"
    return run


# -- the verdict is measured ------------------------------------------------


class TestVerdictIsMeasured:
    def test_a_run_whose_provider_rows_are_gone_is_not_reproducible(self, db):
        """The 184-bar shape, read today."""
        run = _run_row(
            db,
            checksum="f" * 64,
            coverage=_coverage(provider=GONE_PROVIDER, bar_count=184),
        )
        assert db.query(MarketDataBar).filter(
            MarketDataBar.provider_id == GONE_PROVIDER
        ).count() == 0

        verdict = assess_run_reproducibility(db, run)

        assert verdict.status == NOT_REPRODUCIBLE
        assert verdict.reason == STORED_SERIES_ABSENT
        assert not verdict.snapshot_registered
        leg = verdict.legs[0]
        assert (leg.stored_rows, leg.recorded_rows) == (0, 184)
        assert leg.recomputed_checksum is None
        assert "no rows remain stored" in verdict.detail

    def test_rows_appearing_under_the_provider_do_not_make_it_reproducible(self, db):
        """A different series is not the missing one.

        This is the case a backfill would exploit: put rows under the recorded
        provider, in the recorded window and in the recorded *count*, and a
        check that only looks for rows goes green. The verdict stays red
        because it compares digests, not existence.
        """
        days = _weekdays(date.fromisoformat(WINDOW[0]), 5)
        run = _run_row(
            db,
            checksum="f" * 64,
            coverage=_coverage(provider=GONE_PROVIDER, bar_count=len(days)),
        )
        assert assess_run_reproducibility(db, run).status == NOT_REPRODUCIBLE

        _store(db, GONE_PROVIDER, days)

        verdict = assess_run_reproducibility(db, run)
        assert verdict.status == NOT_REPRODUCIBLE
        assert verdict.reason == STORED_SERIES_CHANGED
        assert verdict.legs[0].stored_rows == len(days)
        assert verdict.legs[0].recomputed_checksum != "f" * 64

    def test_a_run_that_prices_off_storage_today_reproduces_its_digest(self, db):
        run = _real_stored_run(db)
        recorded = run.result_json["dataset_checksum"]

        verdict = assess_run_reproducibility(db, run)

        assert verdict.status == REPRODUCIBLE
        assert verdict.reason == STORED_SERIES_REPRODUCED
        assert verdict.legs[0].recomputed_checksum == recorded
        assert verdict.legs[0].stored_rows == verdict.legs[0].recorded_rows
        # The asymmetry a legacy row cannot cross: the writer that read these
        # rows registered them, so this run has a snapshot to cite as well.
        assert run.dataset_snapshot_id is not None
        assert verdict.snapshot_registered

    def test_an_overwritten_price_flips_the_verdict(self, db):
        """Coverage for the previous test: the green reading is sensitive.

        Without this pair, ``reproducible`` would only mean the two sides of
        one comparison agree with each other, whatever the rows say.
        """
        run = _real_stored_run(db)
        assert assess_run_reproducibility(db, run).status == REPRODUCIBLE

        row = (
            db.query(MarketDataBar)
            .filter(MarketDataBar.provider_id == LIVE_PROVIDER)
            .order_by(MarketDataBar.bar_date)
            .first()
        )
        row.close = float(row.close) + 1000.0
        db.commit()

        verdict = assess_run_reproducibility(db, run)
        assert verdict.status == NOT_REPRODUCIBLE
        assert verdict.reason == STORED_SERIES_CHANGED
        assert verdict.legs[0].recomputed_checksum != run.dataset_checksum

    def test_a_second_grain_at_the_same_dates_is_caught_as_a_count_change(self, db):
        """Five daily rows read as five, then a four-hour series lands beside them.

        The recorded count no longer matches the rows, and the digest taken
        across both grains is not the daily one. The count is reported first
        because it is the number the run itself claimed.
        """
        days = _weekdays(date.fromisoformat(WINDOW[0]), 5)
        _store(db, LIVE_PROVIDER, days)
        run = _run_row(
            db,
            checksum=_digest(db),
            coverage=_coverage(provider=LIVE_PROVIDER, bar_count=len(days)),
        )
        assert assess_run_reproducibility(db, run).status == REPRODUCIBLE

        _store(db, LIVE_PROVIDER, days, base_price=900.0, grain=FOUR_H)

        verdict = assess_run_reproducibility(db, run)
        assert verdict.status == NOT_REPRODUCIBLE
        assert verdict.reason == STORED_ROW_COUNT_CHANGED
        assert (verdict.legs[0].stored_rows, verdict.legs[0].recorded_rows) == (10, 5)

    @pytest.mark.parametrize(
        "kwargs,expected_status,expected_reason",
        [
            ({"coverage": None, "checksum": "f" * 64},
             UNVERIFIABLE, NO_RECORDED_COVERAGE),
            ({"checksum": None, "coverage": None}, UNVERIFIABLE, NO_RECORDED_CHECKSUM),
            ({"checksum": "f" * 64, "coverage": _coverage(), "source": "fetched"},
             UNVERIFIABLE, NOT_READ_FROM_STORAGE),
            ({"checksum": "f" * 64, "coverage": _coverage(), "window": ("", "")},
             UNVERIFIABLE, NO_RECORDED_WINDOW),
            ({"checksum": "f" * 64,
              "coverage": {ASSET: {"provider_id": None, "bar_count": 5}}},
             UNVERIFIABLE, NO_RECORDED_PROVIDER),
        ],
    )
    def test_an_uncheckable_run_is_never_reported_as_reproducible(
        self, db, kwargs, expected_status, expected_reason
    ):
        run = _run_row(db, **kwargs)
        verdict = assess_run_reproducibility(db, run)
        assert verdict.status == expected_status
        assert verdict.reason == expected_reason
        assert verdict.status != REPRODUCIBLE

    def test_a_run_with_no_result_payload_at_all_is_unverifiable(self, db):
        run = _run_row(db, checksum="f" * 64, coverage=_coverage())
        run.result_json = None
        db.commit()

        verdict = assess_run_reproducibility(db, run)

        assert verdict.status == UNVERIFIABLE
        assert verdict.reason == NO_RESULT_PAYLOAD


class TestLegsCombine:
    def test_one_absent_leg_makes_a_multi_instrument_run_not_reproducible(self, db):
        days = _weekdays(date.fromisoformat(WINDOW[0]), 5)
        _store(db, LIVE_PROVIDER, days)
        coverage = {
            ASSET: _coverage(provider=LIVE_PROVIDER, bar_count=5)[ASSET],
            SECOND_ASSET: _coverage(
                provider=GONE_PROVIDER, asset=SECOND_ASSET, bar_count=5
            )[SECOND_ASSET],
        }
        run = _run_row(db, checksum=_digest(db), coverage=coverage)

        verdict = assess_run_reproducibility(db, run)

        assert verdict.status == NOT_REPRODUCIBLE
        assert verdict.reason == LEGS_DISAGREE
        assert len(verdict.legs) == 2
        assert [leg.status for leg in verdict.legs] == [REPRODUCIBLE, NOT_REPRODUCIBLE]
        assert GONE_PROVIDER in verdict.detail or SECOND_ASSET in verdict.detail

    def test_one_uncheckable_leg_overshadows_a_verified_one(self, db):
        days = _weekdays(date.fromisoformat(WINDOW[0]), 5)
        _store(db, LIVE_PROVIDER, days)
        coverage = {
            ASSET: _coverage(provider=LIVE_PROVIDER, bar_count=5)[ASSET],
            SECOND_ASSET: {"bar_count": 5, "provider_ids": []},
        }
        run = _run_row(db, checksum=_digest(db), coverage=coverage)

        verdict = assess_run_reproducibility(db, run)

        assert verdict.status == UNVERIFIABLE
        assert verdict.reason == LEGS_DISAGREE


# -- the verdict cannot be bought -------------------------------------------


class TestVerdictWritesNothing:
    @staticmethod
    def _listener(sink):
        def hook(connector, cursor, statement, parameters, context, executemany):
            head = statement.lstrip()[:6].upper()
            if head in ("INSERT", "UPDATE", "DELETE"):
                sink.append(statement)
        return hook

    def test_reading_the_verdict_emits_no_write_statement(self, db):
        run = _run_row(
            db, checksum="f" * 64, coverage=_coverage(provider=GONE_PROVIDER)
        )
        db.expire_all()

        written = []
        hook = self._listener(written)
        event.listen(db.bind, "before_cursor_execute", hook)
        try:
            verdict = assess_run_reproducibility(db, run)
        finally:
            event.remove(db.bind, "before_cursor_execute", hook)

        assert written == [], f"the read path wrote: {written}"
        assert verdict.status == NOT_REPRODUCIBLE

    def test_a_non_reproducible_run_never_gains_a_snapshot_from_a_read(self, db):
        """The backfill this ruling forbids, tested as an absence of effect.

        A snapshot minted after the fact would name a series the platform did
        not witness being read. ``dataset_registry`` can regenerate a
        deterministic id from a manifest, which is precisely why the read path
        must not be allowed to call it: the id would look earned.
        """
        run = _run_row(
            db, checksum="f" * 64, coverage=_coverage(provider=GONE_PROVIDER)
        )
        before = (run.dataset_snapshot_id, db.query(DatasetSnapshot).count())

        for _ in range(3):
            verdict = assess_run_reproducibility(db, run)
            assert verdict.status == NOT_REPRODUCIBLE
            assert not verdict.snapshot_registered

        db.expire_all()
        after = (run.dataset_snapshot_id, db.query(DatasetSnapshot).count())
        assert after == before
        assert run.dataset_snapshot_id is None


# -- the surfaces -----------------------------------------------------------


class TestApiSurfaces:
    def test_get_run_reports_the_verdict_next_to_the_metrics(self, db, client, auth_headers):
        run = _run_row(
            db, checksum="f" * 64, coverage=_coverage(provider=GONE_PROVIDER, bar_count=184)
        )
        resp = client.get(f"/api/v1/runs/{run.id}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["metrics"]["total_return_pct"] == 4.2
        assert body["reproducibility"]["status"] == NOT_REPRODUCIBLE
        assert body["reproducibility"]["reason"] == STORED_SERIES_ABSENT
        assert body["reproducibility"]["snapshot_registered"] is False

    def test_a_reproducible_run_is_reported_green_on_the_same_route(self, db, client, auth_headers):
        """The other side of the field, or this route only ever says red."""
        run = _real_stored_run(db)
        resp = client.get(f"/api/v1/runs/{run.id}", headers=auth_headers)
        assert resp.status_code == 200, resp.text

        body = resp.json()

        assert body["reproducibility"]["status"] == REPRODUCIBLE
        assert body["reproducibility"]["snapshot_registered"] is True

    def test_list_runs_reports_the_verdict_for_every_row(self, db, client, auth_headers):
        gone = _run_row(
            db, checksum="f" * 64, coverage=_coverage(provider=GONE_PROVIDER)
        )
        resp = client.get(
            f"/api/v1/experiments/{gone.experiment_version.experiment_id}/runs",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert [row["id"] for row in rows] == [str(gone.id)]
        assert rows[0]["reproducibility"]["status"] == NOT_REPRODUCIBLE

    def test_the_response_model_carries_the_field(self):
        """A dropped field would still render 200: the schema is the contract."""
        assert "reproducibility" in ResearchRunResponse.model_fields


class TestComparisonRefusesToRankIt:
    def _compare(self, client, auth_headers, run_a, run_b):
        resp = client.get(
            "/api/v1/runs/compare",
            params={"run_a": str(run_a.id), "run_b": str(run_b.id)},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_two_runs_off_missing_series_are_not_a_fair_comparison(
        self, db, client, auth_headers
    ):
        """The recorded pair: one claimed coverage, two digests, no rows.

        Compared, they look like an A/B test of a strategy. They are a record
        of two reads of a series that cannot be re-read, which the response
        says instead of leaving ``fair_comparison`` to the field matchers.
        """
        a = _run_row(db, checksum="a" * 64,
                     coverage=_coverage(provider=GONE_PROVIDER, bar_count=184))
        b = _run_row(db, checksum="b" * 64,
                     coverage=_coverage(provider=GONE_PROVIDER, bar_count=184))

        body = self._compare(client, auth_headers, a, b)

        assert body["comparability"]["fair_comparison"] is False
        citing = [
            line for line in body["limitations"]
            if STORED_SERIES_ABSENT in line and "績效證據" in line
        ]
        assert len(citing) == 2, body["limitations"]
        # Two sentences that do not say which run they refuse are one sentence
        # too many to act on, and a reader cannot tell which side lost its rows.
        assert sorted(line.split()[1] for line in citing) == sorted(
            [str(a.id), str(b.id)]
        ), citing

    def test_a_reproducible_pair_adds_no_refusal(self, db, client, auth_headers):
        """Positive control: the refusal is about series, not about existing."""
        first = _real_stored_run(db)
        second = _real_stored_run(db)

        body = self._compare(client, auth_headers, first, second)

        refusing = [line for line in body["limitations"] if REFUSAL_PHRASE in line]
        assert refusing == [], refusing
        assert body["comparability"]["fair_comparison"] is True

    def test_one_reproducible_side_still_blocks_the_comparison(
        self, db, client, auth_headers
    ):
        first = _real_stored_run(db)
        assert assess_run_reproducibility(db, first).status == REPRODUCIBLE
        legacy = _run_row(db, checksum="f" * 64,
                          coverage=_coverage(provider=GONE_PROVIDER))

        body = self._compare(client, auth_headers, first, legacy)

        assert body["comparability"]["fair_comparison"] is False
        citing = [line for line in body["limitations"] if REFUSAL_PHRASE in line]
        assert len(citing) == 1, citing


# -- the backfill gate ------------------------------------------------------

BACKEND = Path(__file__).resolve().parent.parent / "app"

#: The one transaction allowed to point a run at a snapshot: its own
#: completion, where the writer is the process that read the rows.
SNAPSHOT_WRITE_ALLOWLIST = {"app/services/research_runs.py"}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _snapshot_assignments(root: Path = BACKEND):
    """Every ``<expr>.dataset_snapshot_id = ...`` in production source.

    Read from the AST rather than a text search so that a comparison
    (``==``), a keyword argument in a constructor, and an assignment are told
    apart — the three shapes a backfill could hide among.
    """
    found = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "dataset_snapshot_id"
                ):
                    found.append(
                        f"{path.relative_to(root.parent)}:{node.lineno} "
                        f"target={ast.unparse(target.value)}"
                    )
    return found


def _enclosing_function(path: Path, lineno: int) -> str:
    """The function that holds a reported line, from the same kind of parse."""
    holders = [
        node.name
        for node in ast.walk(_parse(path))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= lineno <= node.end_lineno
    ]
    return holders[-1] if holders else "<module>"


def _constructor_snapshot_kwargs(root: Path = BACKEND):
    """``ResearchRun(dataset_snapshot_id=...)`` — a backfill written at birth."""
    hits = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Call):
                continue
            if ast.unparse(node.func).split(".")[-1] != "ResearchRun":
                continue
            for kw in node.keywords:
                if kw.arg == "dataset_snapshot_id":
                    hits.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    return hits


def _split_site(site: str):
    rel_path, rest = site.split(":", 1)
    return rel_path, int(rest.split()[0])


class TestNoBackfillPath:
    def test_only_the_completion_transaction_writes_a_snapshot_onward(self):
        assignments = _snapshot_assignments()

        # Coverage first: a walk that read nothing would report zero offenders.
        assert assignments, "the walk found no snapshot assignment at all"
        offenders = [
            site for site in assignments
            if site.split(":")[0] not in SNAPSHOT_WRITE_ALLOWLIST
        ]
        assert offenders == [], (
            "something outside the completion transaction can attach a "
            "snapshot to a run: " + "; ".join(offenders)
        )

    def test_the_allowed_site_is_inside_the_terminal_writer(self):
        """The allowlist is not a size-1 assertion that would pass on anything."""
        assignments = _snapshot_assignments()
        assert len(assignments) == 1, assignments
        rel_path, lineno = _split_site(assignments[0])
        assert rel_path == "app/services/research_runs.py", rel_path

        assert _enclosing_function(BACKEND.parent / rel_path, lineno) == "execute_backtest_run"

    def test_the_walk_cites_a_line_that_is_an_assignment(self):
        """Not a stale address: the line number it reports is the write itself."""
        rel_path, lineno = _split_site(_snapshot_assignments()[0])
        line = (
            (BACKEND.parent / rel_path)
            .read_text(encoding="utf-8")
            .splitlines()[lineno - 1]
        )
        assert line.strip().startswith("run.dataset_snapshot_id ="), line

    def test_no_run_is_born_with_a_snapshot_passed_in(self):
        assert _constructor_snapshot_kwargs() == []

    def test_the_gate_fires_on_a_planted_backfill(self, tmp_path):
        """Positive control: a repair script that mints history is caught."""
        offender = tmp_path / "app" / "services" / "repair.py"
        offender.parent.mkdir(parents=True)
        offender.write_text(
            "def backfill(db, run, snapshot_id):\n"
            "    run.dataset_snapshot_id = snapshot_id\n"
            "    run.dataset_checksum = run.dataset_checksum\n",
            encoding="utf-8",
        )
        innocent = tmp_path / "app" / "api" / "reader.py"
        innocent.parent.mkdir(parents=True)
        innocent.write_text(
            "def read(run, snapshot_id):\n"
            "    return run.dataset_snapshot_id == snapshot_id\n"
            "def build():\n"
            "    return ResearchRun(dataset_snapshot_id=None)\n",
            encoding="utf-8",
        )
        root = tmp_path / "app"

        found = _snapshot_assignments(root)
        births = _constructor_snapshot_kwargs(root)

        assert len(found) == 1, found
        assert "repair.py" in found[0]
        # The two shapes that are not backfills stay out of the assignment count.
        assert "reader.py" not in " ".join(found), "a comparison is not a write"
        assert len(births) == 1 and "reader.py" in births[0]
