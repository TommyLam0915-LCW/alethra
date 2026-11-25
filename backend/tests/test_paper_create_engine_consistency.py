"""What POST /paper-trading/create actually does with the fields it is given.

A paper account is the object the whole customer flow hangs from: the owner
names a skill, an instrument, a grain, an exit config and a capital, and the
platform is supposed to run *that* account, trace it to *that* strategy version,
and let *that* lineage be read back. Until this round none of it was measured on
the offline lane, because the create route answered 500 ``creation_failed`` here
for a reason that had nothing to do with the request: the engine passed its
Decimal capital into raw SQL untyped, and sqlite3 refuses a Decimal bind
("Error binding parameter: type 'decimal.Decimal' is not supported"). The route
now declares each money bind with its own column type (Float on
``paper_portfolios``, Numeric(15, 2) on ``paper_trading_runs``), which is what
PostgreSQL already did implicitly, so the propagation below is provable offline
instead of being asserted from a read of the INSERT.

The nodes are ordered the way the request is:

  P1  instrument / grain / capital / exit config are stored and come back
  P2  the lineage fields a caller names are the values the product's own
      lineage query reads, and the version the decision may claim
  P3  a dataset that contradicts the version is refused before a row exists
  P4  the called engine works inside the caller's transaction and never commits
      it -- measured from a second connection, which is the only place a real
      commit would show
  P5  the disclosed visible-face limits: what the formal reads do *not* carry,
      and that the process engine is built once from the first URL
  P6  the account the create route wrote is one the rest of the product can
      name -- the account list, the skill-unit ceiling, the skill-unit door a
      model selection passes, and the owner's own manual order
  P7  a row the pre-typing writers left behind, in the spelling it was left in,
      is still named by the reads that carry the read tolerance
  P8  the version door's second look at a run row its index cannot match, in
      both directions

Time range and costs are not in this file's passing set: ``days_back`` is a
replay argument, the replay's own execution needs ``SELECT ... FOR UPDATE`` in
raw SQL and so is a PostgreSQL measurement, and commission and slippage are
engine constants that no formal request can set. Each is written as a refusal or
a NOT RUN below rather than folded into a green count.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.lib.uuid_bind import as_id_text, to_uuid, uuid_binds
from app.models.dataset import Dataset, SourceType
from app.models.experiment import Experiment, ExperimentStatus, ResearchType
from app.models.experiment_version import ExperimentVersion
from app.models.model_research import ModelResearchRecord
from app.models.paper_portfolio import DecisionRecord, PaperPortfolio
from app.models.paper_trading_run import PaperTradingRun
from app.models.user import User
from app.services.paper_trading import PaperTradingEngine, get_engine
from tests.conftest import TEST_ENGINE, TestSession

INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _account(client, tag):
    """Register through the formal route and log in; return (id, headers)."""
    email = f"papercreate_{tag}@alethra.io"
    registered = client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
            "email": email,
            "password": "testpass123",
            "display_name": f"create {tag}",
            "user_type": "individual",
        },
    )
    assert registered.status_code in (200, 201), registered.text
    login = client.post(
        "/api/auth/login", json={"email": email, "password": "testpass123"}
    )
    assert login.status_code == 200, login.text
    with TestSession() as db:
        owner_id = str(
            db.query(User).filter(User.email == email).one().id
        )
    return owner_id, {"Authorization": f"Bearer {login.json()['access_token']}"}


def _frozen_version(db, owner_id, *, dataset=None, title="Owned strategy"):
    """The owner's own frozen experiment version, on ``dataset`` when given."""
    uid = uuid.UUID(str(owner_id))
    experiment = Experiment(
        owner_id=uid,
        title=title,
        hypothesis="Test",
        research_type=ResearchType.quantitative,
        status=ExperimentStatus.locked,
    )
    db.add(experiment)
    db.flush()
    version = ExperimentVersion(
        experiment_id=experiment.id,
        version_number=1,
        created_by=uid,
        strategy_spec={"test": True},
        dataset_id=dataset.id if dataset is not None else None,
        locked_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(version)
    db.flush()
    return version


def _dataset(db, name):
    row = Dataset(
        name=name, provider="twelvedata-demo", source_type=SourceType.demo
    )
    db.add(row)
    db.flush()
    return row


def _create(client, headers, **over):
    body = {
        "skill": "fisherman",
        "instrument_id": INSTRUMENT,
        "interval_minutes": 60,
        "exit_config": "exit_atr_2x",
        "initial_capital": 100000.0,
    }
    body.update(over)
    return client.post("/api/v1/paper-trading/create", headers=headers, json=body)


def _uuid(value):
    """Parse whichever UUID spelling this lane stored (dashed text or hex)."""
    return None if value is None else uuid.UUID(str(value))


def _fresh_reader():
    """An independent connection to the same database the product writes.

    A second engine is the point: on this lane every test session shares one
    StaticPool connection, so only a connection that never wrote the row can
    tell a committed row from one that is merely flushed inside a transaction.
    """
    return create_engine(get_settings().database_url)


def _counts(engine):
    with engine.connect() as conn:
        return (
            conn.execute(text("SELECT COUNT(*) FROM paper_portfolios")).scalar(),
            conn.execute(text("SELECT COUNT(*) FROM paper_trading_runs")).scalar(),
        )


def _run_row(engine, run_id):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT skill, instrument_id, interval_minutes, exit_config, "
                "initial_capital, status, strategy_version_id, dataset_id, "
                "portfolio_id "
                "FROM paper_trading_runs WHERE id = :id"
            ).bindparams(*uuid_binds("id")),
            {"id": to_uuid(run_id)},
        ).fetchone()


# ---------------------------------------------------------------------------
# P1 — the declared contract fields enter the row and answer through the reads
# ---------------------------------------------------------------------------


def test_declared_instrument_grain_and_capital_return_through_the_formal_reads(
    client
):
    """The run the owner asked for is the run the owner is told about.

    ``GET /status`` and ``GET /history`` are the product's own read paths; the
    stored row is checked from a second connection so a value that only reached
    the response object would not pass.
    """
    owner_id, headers = _account(client, "p1")
    reader = _fresh_reader()
    try:
        created = _create(client, headers)
        assert created.status_code == 200, created.text
        payload = created.json()
        assert payload["status"] == "queued"

        status_read = client.get(
            f"/api/v1/paper-trading/status/{payload['run_id']}", headers=headers
        )
        assert status_read.status_code == 200, status_read.text
        shown = status_read.json()
        assert shown["run_id"] == payload["run_id"]
        assert shown["skill"] == "fisherman"
        assert shown["instrument_id"] == INSTRUMENT
        assert float(shown["initial_capital"]) == 100000.0

        history = client.get("/api/v1/paper-trading/history", headers=headers)
        assert history.status_code == 200, history.text
        entries = [r for r in history.json() if r["run_id"] == payload["run_id"]]
        assert len(entries) == 1, history.json()
        # grain: the interval the request named is what the list answers
        assert entries[0]["interval_minutes"] == 60

        stored = _run_row(reader, payload["run_id"])
        assert stored is not None, "the response named a run the table does not have"
        assert (stored.skill, stored.instrument_id) == ("fisherman", INSTRUMENT)
        assert stored.interval_minutes == 60
        assert stored.exit_config == "exit_atr_2x"
        assert float(stored.initial_capital) == 100000.0
        assert stored.status == "queued"
        # Same account on both sides, stated as the spelling the API answers in
        # rather than the characters this lane happens to keep in the column.
        assert as_id_text(stored.portfolio_id) == payload["portfolio_id"]
    finally:
        reader.dispose()


def test_a_second_account_never_sees_another_accounts_run_in_its_own_reads(client):
    """The declared identity is the filter, not a courtesy.

    Same create path, two accounts: the second one's ``/history`` lists nothing,
    and reading the first account's run by id answers 404 rather than data.
    """
    owner_a, first_headers = _account(client, "p2a")
    owner_b, second_headers = _account(client, "p2b")

    created = _create(client, first_headers)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]

    assert client.get("/api/v1/paper-trading/history", headers=second_headers).json() == []
    refused = client.get(
        f"/api/v1/paper-trading/status/{run_id}", headers=second_headers
    )
    assert refused.status_code == 404, refused.text
    assert refused.json()["detail"]["reason"] == "not_found"
    # positive control: the owner does read the same row, so the 404 above is
    # the ownership check and not a missing row or a wrong id
    assert (
        client.get(f"/api/v1/paper-trading/status/{run_id}", headers=first_headers).status_code
        == 200
    )


# ---------------------------------------------------------------------------
# P2 — the lineage a caller names is what the product's lineage query reads
# ---------------------------------------------------------------------------


def test_the_named_version_and_its_dataset_are_the_values_the_run_carries(client):
    """strategy_version_id travels, and dataset_id is taken from the version."""
    owner_id, headers = _account(client, "p3")
    reader = _fresh_reader()
    try:
        with TestSession() as db:
            dataset = _dataset(db, "P3 dataset")
            version = _frozen_version(db, owner_id, dataset=dataset)
            version_id, dataset_id = str(version.id), str(dataset.id)
            db.commit()

        created = _create(client, headers, strategy_version_id=version_id)
        assert created.status_code == 200, created.text
        run_id = created.json()["run_id"]

        stored = _run_row(reader, run_id)
        assert _uuid(stored.strategy_version_id) == uuid.UUID(version_id)
        # dataset_id defaulted from the version, not dropped
        assert _uuid(stored.dataset_id) == uuid.UUID(dataset_id)
    finally:
        reader.dispose()


def test_the_run_binding_is_the_version_a_decision_may_claim(client):
    """The row written here is read by the decision door, in both directions.

    ``POST /paper-portfolio/{id}/decisions`` compares its claim against
    ``paper_trading_runs.strategy_version_id``, so the same account answers for
    both: naming the run's version is admitted, naming a different frozen
    version of the same owner is refused and echoes the id this create wrote.
    """
    owner_id, headers = _account(client, "p4")
    with TestSession() as db:
        dataset = _dataset(db, "P4 dataset")
        bound = _frozen_version(db, owner_id, dataset=dataset, title="Bound")
        other = _frozen_version(db, owner_id, dataset=dataset, title="Other")
        bound_id, other_id = str(bound.id), str(other.id)
        db.commit()

    created = _create(client, headers, strategy_version_id=bound_id)
    assert created.status_code == 200, created.text
    body = created.json()

    agreed = client.post(
        f"/api/v1/paper-portfolio/{body['portfolio_id']}/decisions",
        headers=headers,
        json={
            "decision_type": "no_action",
            "actor_type": "human",
            "linked_experiment_version_id": bound_id,
        },
    )
    assert agreed.status_code == 201, agreed.text
    assert agreed.json()["linked_experiment_version_id"] == bound_id

    refused = client.post(
        f"/api/v1/paper-portfolio/{body['portfolio_id']}/decisions",
        headers=headers,
        json={
            "decision_type": "no_action",
            "actor_type": "human",
            "linked_experiment_version_id": other_id,
        },
    )
    assert refused.status_code == 400, refused.text
    detail = refused.json()["detail"]
    assert detail["reason"] == "version_mismatch"
    # both ids echoed: the refusal is against the value the create wrote
    assert detail["run_version"] == bound_id
    assert detail["decision_version"] == other_id

    with TestSession() as db:
        assert db.query(DecisionRecord).filter(
            DecisionRecord.paper_portfolio_id == uuid.UUID(body["portfolio_id"])
        ).count() == 1, "the refused decision still wrote a row"


def test_a_dataset_that_contradicts_the_version_is_refused_before_a_row_exists(
    client
):
    """Compatibility is checked outside the engine, and nothing is half-written."""
    owner_id, headers = _account(client, "p5")
    with TestSession() as db:
        dataset = _dataset(db, "P5 version dataset")
        other = _dataset(db, "P5 other dataset")
        version = _frozen_version(db, owner_id, dataset=dataset)
        version_id, wrong_dataset = str(version.id), str(other.id)
        right_dataset = str(dataset.id)
        db.commit()

    with TestSession() as db:
        portfolios_before = db.query(PaperPortfolio).count()
        runs_before = db.query(PaperTradingRun).count()

    refused = _create(
        client, headers, strategy_version_id=version_id, dataset_id=wrong_dataset
    )
    assert refused.status_code == 400, refused.text
    detail = refused.json()["detail"]
    assert detail["reason"] == "dataset_mismatch"
    # the refusal names both sides of the contradiction it found
    assert detail["version_dataset"] == right_dataset
    assert detail["request_dataset"] == wrong_dataset

    with TestSession() as db:
        db.expire_all()
        assert db.query(PaperPortfolio).count() == portfolios_before
        assert db.query(PaperTradingRun).count() == runs_before


# ---------------------------------------------------------------------------
# P4 — the caller keeps its transaction boundary
# ---------------------------------------------------------------------------


def test_the_called_engine_flushes_into_the_callers_transaction_and_never_commits(
    client,
):
    """``session=db`` means the caller owns the commit, measured on the wire.

    The engine documents that it commits only when it opened the session itself.
    A spy on ``commit`` could pass while the rows were durable anyway, so this
    reads the table from a second connection: invisible before the caller
    commits, visible after, and gone for good when the caller rolls back.
    """
    url = get_settings().database_url
    engine = PaperTradingEngine(database_url=url)
    reader = _fresh_reader()
    try:
        before = _counts(reader)

        owner_id, _headers = _account(client, "p6")
        db = TestSession()
        try:
            first = engine.create_portfolio(
                user_id=owner_id,
                skill="fisherman",
                instrument_id=INSTRUMENT,
                interval_minutes=60,
                exit_config="exit_atr_2x",
                session=db,
            )
            # flushed, not committed: another connection cannot see it
            assert _counts(reader) == before, (
                "the engine committed a session it was handed"
            )
            db.commit()
            # positive control: the same measurement now sees exactly that run,
            # so the step above was a commit boundary and not a stale reader
            assert _counts(reader) == (before[0] + 1, before[1] + 1)

            second = engine.create_portfolio(
                user_id=owner_id,
                skill="fisherman",
                instrument_id=INSTRUMENT,
                interval_minutes=120,
                exit_config="exit_atr_2x",
                session=db,
            )
            db.rollback()
            assert _counts(reader) == (before[0] + 1, before[1] + 1), (
                "a rolled-back caller transaction still left the engine's write"
            )
        finally:
            db.close()

        assert first["run_id"] != second["run_id"]
        assert _run_row(reader, first["run_id"]) is not None
        assert _run_row(reader, second["run_id"]) is None
    finally:
        reader.dispose()


def test_the_http_create_commits_once_and_leaves_a_durable_pair(client):
    """The route owns the boundary for its own session, and the row survives.

    Same reader connection as above: after a 200, the portfolio and the run are
    both visible to a transaction that never wrote them.
    """
    owner_id, headers = _account(client, "p7")
    reader = _fresh_reader()
    try:
        before = _counts(reader)
        created = _create(client, headers)
        assert created.status_code == 200, created.text
        body = created.json()
        assert _counts(reader) == (before[0] + 1, before[1] + 1)

        # and the writes are one transaction's worth: the run's portfolio
        # exists, so there is no window with a run and no account
        stored = _run_row(reader, body["run_id"])
        with reader.connect() as conn:
            portfolio = conn.execute(
                text("SELECT owner_id, initial_virtual_cash FROM paper_portfolios "
                     "WHERE id = :id").bindparams(*uuid_binds("id")),
                {"id": to_uuid(body["portfolio_id"])},
            ).fetchone()
        assert portfolio is not None
        assert str(portfolio.owner_id).replace("-", "") == owner_id.replace("-", "")
        assert float(portfolio.initial_virtual_cash) == 100000.0
        # Identity, not the characters a given lane renders it in: the text lane
        # stores 32 hex characters, PostgreSQL hands back a UUID object whose
        # ``str()`` is the dashed 36. Either way it has to be the same account the
        # response named.
        assert _uuid(stored.portfolio_id) == _uuid(body["portfolio_id"])
    finally:
        reader.dispose()


# ---------------------------------------------------------------------------
# P5 — the disclosed visible face, pinned so it cannot drift silently
# ---------------------------------------------------------------------------


def test_the_formal_reads_still_do_not_carry_lineage_or_exit_config(client):
    """What a customer can see about their own run, stated as a set.

    ``strategy_version_id``, ``dataset_id`` and ``exit_config`` are stored and
    are used by the lineage door, but no paper-trading read endpoint returns
    them, so the owner cannot audit which version an account runs from that
    account's own screens. The assertions here are the gap, written so that
    adding a field to either response must be a deliberate edit: the key sets
    are exact, and the stored values are read from the row in the same node to
    show the fields exist and are populated.
    """
    owner_id, headers = _account(client, "p8")
    reader = _fresh_reader()
    try:
        with TestSession() as db:
            dataset = _dataset(db, "P8 dataset")
            version = _frozen_version(db, owner_id, dataset=dataset)
            version_id = str(version.id)
            db.commit()

        created = _create(client, headers, strategy_version_id=version_id)
        assert created.status_code == 200, created.text
        run_id = created.json()["run_id"]

        shown = client.get(
            f"/api/v1/paper-trading/status/{run_id}", headers=headers
        ).json()
        assert set(shown) == {
            "run_id", "status", "skill", "instrument_id", "initial_capital",
            "current_capital", "total_pnl", "total_trades", "win_rate",
            "start_date", "end_date",
        }
        listed = client.get("/api/v1/paper-trading/history", headers=headers).json()
        assert set(listed[0]) == {
            "run_id", "skill", "instrument_id", "interval_minutes", "status",
            "initial_capital", "final_capital", "total_pnl", "total_trades",
            "win_rate", "start_date", "end_date", "run_type",
        }

        stored = _run_row(reader, run_id)
        assert str(stored.strategy_version_id).replace("-", "") == version_id.replace("-", "")
        assert stored.dataset_id is not None
        assert stored.exit_config == "exit_atr_2x"
    finally:
        reader.dispose()


def test_the_process_engine_is_built_from_the_first_url_it_is_given(monkeypatch):
    """``get_engine`` caches, so a settings change mid-process does not repoint it.

    Recorded because the approval package names the engine as the object that
    talks to the database: the route passes ``get_settings().database_url`` on
    every request, and the first caller's URL is the one that is kept.
    """
    import app.services.paper_trading as module

    monkeypatch.setattr(module, "_engine", None)
    try:
        first = get_engine(get_settings().database_url)
        assert get_engine("postgresql+psycopg2://nobody:nobody@nowhere:5432/nope") is first
        assert first.session_factory is not None
    finally:
        monkeypatch.setattr(module, "_engine", None)


# ---------------------------------------------------------------------------
# P6 — the account the create route wrote is one the rest of the product can
#      name: the list, the ceiling, the skill-unit door, the owner's own order
# ---------------------------------------------------------------------------
#
# These four nodes exist because of what measuring P1 exposed. The create route
# writes ``paper_portfolios`` with raw SQL and stores ``str(uuid4())`` — the
# dashed 36-character form — while an ORM identity comparison on a UUID column
# renders 32 characters with no dashes. On PostgreSQL both bind strings parse to
# the same ``uuid``, so the difference cannot show. On this lane the column is
# text and the two spellings are different strings, so the account the create
# route made was invisible to every read that used an identity key: it was
# missing from the account list the skill-account picker reads, it was not
# counted against the ≤N skill-unit ceiling, the model-selection door answered
# ``skill_unit_not_found`` for it, and a manual order on it was refused as a
# portfolio that does not exist.


def test_the_created_account_appears_in_the_account_list_the_product_reads(client):
    """POST /paper-trading/create writes the row; GET /paper-portfolio names it.

    The list is the product's own account surface (workspace, journal and the
    skill-account selector all call it), so an account missing from it is an
    account the owner cannot reach again.
    """
    _, headers = _account(client, "p6a")
    created = _create(client, headers)
    assert created.status_code == 200, created.text

    listed = client.get("/api/v1/paper-portfolio", headers=headers)
    assert listed.status_code == 200, listed.text
    ids = {row["id"] for row in listed.json()}
    assert created.json()["portfolio_id"] in ids, listed.json()


def test_the_ceiling_counts_accounts_written_by_the_create_route(client, monkeypatch):
    """A cap is only a cap if the counter sees every account it covers.

    With a ceiling of one, the second create must be refused and must report
    that one slot is held. An identity-key count sees only the dashed-less
    spelling, so the second create used to be admitted and report ``held`` 0 —
    a ceiling that the entry point which also writes a run did not obey.
    """
    _, headers = _account(client, "p6b")
    monkeypatch.setattr(get_settings(), "paper_skill_unit_max_per_user", 1)

    first = _create(client, headers)
    assert first.status_code == 200, first.text

    second = _create(client, headers)
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["error"] == "skill_unit_limit_reached"
    assert detail["held_skill_units"] == 1


def test_the_create_lane_account_passes_the_skill_unit_door(client):
    """The ownership check a model selection makes accepts the created account.

    ``GET /records/{id}/selections`` runs the same ``_owned_skill_unit`` check
    that ``select_version`` runs before it writes a skill-scoped selection, so
    this measures the door the selection has to pass. What else a selection
    demands of a version — an eligible personal artifact — is gated by the
    learning tests and is not re-proved here.
    """
    owner_id, headers = _account(client, "p6c")
    with TestSession() as db:
        record = ModelResearchRecord(
            owner_id=uuid.UUID(str(owner_id)),
            title="P6 record",
            purpose="personal_decision",
        )
        db.add(record)
        db.flush()
        record_id = str(record.id)
        db.commit()

    created = _create(client, headers)
    assert created.status_code == 200, created.text
    portfolio_id = created.json()["portfolio_id"]

    answer = client.get(
        f"/api/v1/ml/personal/records/{record_id}/selections"
        f"?skill_unit_id={portfolio_id}",
        headers=headers,
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["skill_unit_id"] == portfolio_id
    # Nothing has been chosen for this account yet, and the answer says so
    # rather than falling back to the record-wide alias.
    assert body["skill_active_version_id"] is None


def test_the_owner_can_execute_a_manual_trade_on_the_created_account(client):
    """The account the create route made is one the owner can trade by hand.

    ``POST /{portfolio_id}/orders`` re-reads the account under a row lock and
    then writes its cash, so it is the first hop of the owner-executes-every-trade
    flow. This node used to be skipped on the SQLite lane: the create route stored
    the account's key as dashed text, the ORM UPDATE that follows the lock carried
    the 32-character identity key, matched nothing and raised ``StaleDataError``
    (measured 2026-11-18). The writers now bind the column's own UUID type, so the
    row a create made is the row the lock loads and the row its write addresses --
    which is what makes the hop measurable here instead of only on PostgreSQL.
    The row lock itself is still a PostgreSQL behaviour; what is proved on this
    lane is that the identity of the row survives the whole path.
    """
    _, headers = _account(client, "p6d")
    created = _create(client, headers)
    assert created.status_code == 200, created.text
    portfolio_id = created.json()["portfolio_id"]

    order = client.post(
        f"/api/v1/paper-portfolio/{portfolio_id}/orders",
        headers=headers,
        json={
            "asset_id": "DEMO-ALPHA",
            "side": "BUY",
            "quantity": 1,
            "order_type": "market",
            "rationale": "owner executes this trade",
            "expected_outcome": "validation only",
            "self_reported_confidence": "high",
        },
    )
    assert order.status_code == 201, order.text
    body = order.json()
    assert str(body["portfolio_id"]) == portfolio_id
    assert body["actor_type"] == "human"


# ---------------------------------------------------------------------------
# P7 — a row the untyped writers left behind is still one the reads can name
# ---------------------------------------------------------------------------
#
# P6 covers accounts created from here on, now that the bind carries the column's
# own type. ``app/lib/uuid_spelling.py`` exists for the other half: rows that are
# *already* stored as dashed 36-character text, which is what
# ``POST /api/v1/paper-trading/create`` and the live trading daemon wrote until
# then. Nothing else in this file can distinguish that tolerance -- every other
# row here is produced by the normalised writer -- so the row below is inserted
# the way the old writer inserted it (an untyped ``str(uuid4())`` reaching a text
# column) and every assertion is the product's own read. The insert stands in for
# a row a fresh lane cannot otherwise produce; the measurement is that each limb
# of the tolerance has its own node -- ``stored_as`` for the three set-shaped
# reads, ``dashed_text`` for the one-row ownership read.
#
# What this section deliberately does *not* claim: that such an account can be
# traded. The order path re-reads the account through the ORM's own identity key
# (``app/services/order_lifecycle.py:278``), which renders the typed spelling, and
# no node here measures what that does to a dashed row.


def _legacy_dashed_account(owner_id):
    """One portfolio row in the spelling the pre-typing create route produced."""
    portfolio_id = uuid.uuid4()
    with TestSession() as db:
        db.execute(
            text(
                "INSERT INTO paper_portfolios (id, owner_id, currency, "
                "initial_virtual_cash, current_virtual_cash, status, created_at) "
                "VALUES (:id, :owner_id, 'USD', 100000.0, 100000.0, 'active', "
                "CURRENT_TIMESTAMP)"
            ),
            {"id": str(portfolio_id), "owner_id": str(owner_id)},
        )
        db.commit()
    return str(portfolio_id)


def test_an_account_left_in_the_dashed_spelling_is_still_in_the_account_list(client):
    owner_id, headers = _account(client, "p7a")
    legacy_id = _legacy_dashed_account(owner_id)

    listed = client.get("/api/v1/paper-portfolio", headers=headers)
    assert listed.status_code == 200, listed.text
    ids = {row["id"] for row in listed.json()}
    assert legacy_id in ids, listed.json()


def test_the_ceiling_counts_an_account_left_in_the_dashed_spelling(client, monkeypatch):
    """A slot a legacy row holds is a slot the ceiling has to refuse on."""
    owner_id, headers = _account(client, "p7b")
    _legacy_dashed_account(owner_id)
    monkeypatch.setattr(get_settings(), "paper_skill_unit_max_per_user", 1)

    answer = _create(client, headers)
    assert answer.status_code == 409, answer.text
    detail = answer.json()["detail"]
    assert detail["error"] == "skill_unit_limit_reached"
    assert detail["held_skill_units"] == 1


def test_an_account_left_in_the_dashed_spelling_answers_its_own_detail_read(client):
    """The one-row ownership read gets the same second chance as the list.

    ``GET /paper-portfolio/{id}`` is what the account panel opens with, and its
    fallback is a separate limb from the set-matching one: it only runs after the
    indexed identity comparison found nothing.
    """
    owner_id, headers = _account(client, "p7c")
    legacy_id = _legacy_dashed_account(owner_id)

    answer = client.get(f"/api/v1/paper-portfolio/{legacy_id}", headers=headers)
    assert answer.status_code == 200, answer.text
    assert str(answer.json()["id"]) == legacy_id


def test_a_dashed_spelling_account_passes_the_skill_unit_door(client):
    owner_id, headers = _account(client, "p7d")
    legacy_id = _legacy_dashed_account(owner_id)
    with TestSession() as db:
        record = ModelResearchRecord(
            owner_id=uuid.UUID(str(owner_id)),
            title="P7 record",
            purpose="personal_decision",
        )
        db.add(record)
        db.flush()
        record_id = str(record.id)
        db.commit()

    answer = client.get(
        f"/api/v1/ml/personal/records/{record_id}/selections"
        f"?skill_unit_id={legacy_id}",
        headers=headers,
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["skill_unit_id"] == legacy_id


# ---------------------------------------------------------------------------
# P8 — the version door's second look at a run row its index cannot match
# ---------------------------------------------------------------------------
#
# ``POST /paper-portfolio/{id}/decisions`` refuses a version claim that
# contradicts what the account is actually running. It asks two ways
# (``app/api/paper_portfolio.py:998-1014``): the indexed ORM comparison first,
# then a raw-SQL look that strips the dashes off the stored ``portfolio_id``.
# ``test_the_run_binding_is_the_version_a_decision_may_claim`` exercises the
# first look only, because its run row was written by an ORM writer and is
# therefore findable by it. On this lane the second look is the only limb that
# can answer for a run row left as dashed text -- which is the spelling the
# pre-typing create route and the live daemon produced. Nothing measured that
# limb before, so a build that dropped it entirely would have kept every other
# node in this file green.
#
# The two nodes insert that run row the way the old writer inserted it and read
# it back through the product's own decision route, once contradicting it and
# once agreeing with it: the first dies if the second look is removed (the claim
# would go uncontradicted and be accepted), the second dies if the comparison is
# removed or inverted (an agreeing claim would be refused). Neither one is
# evidence about PostgreSQL, where the indexed comparison matches a native uuid
# in either spelling and the second look is never reached -- and no PG run was
# performed for this round.


def _legacy_dashed_run(portfolio_id, version_id):
    """One run row in the spelling the pre-typing writers produced."""
    with TestSession() as db:
        db.execute(
            text(
                "INSERT INTO paper_trading_runs (id, portfolio_id, "
                "strategy_version_id, skill, instrument_id, interval_minutes, "
                "exit_config, start_date, status, cancel_requested, "
                "initial_capital) VALUES (:id, :portfolio_id, :version_id, "
                "'legacy', 'DEMO-ALPHA', 60, '{}', CURRENT_TIMESTAMP, "
                "'completed', 0, 100000.0)"
            ),
            {
                "id": str(uuid.uuid4()),
                "portfolio_id": str(portfolio_id),
                "version_id": str(version_id),
            },
        )
        db.commit()


def _decision(client, headers, portfolio_id, version_id):
    return client.post(
        f"/api/v1/paper-portfolio/{portfolio_id}/decisions",
        headers=headers,
        json={
            "decision_type": "no_action",
            "actor_type": "human",
            "linked_experiment_version_id": version_id,
        },
    )


def test_a_run_row_left_in_the_dashed_spelling_still_contradicts_a_claim(client):
    owner_id, headers = _account(client, "p8a")
    legacy_id = _legacy_dashed_account(owner_id)
    with TestSession() as db:
        bound = _frozen_version(db, owner_id, title="P8 bound")
        other = _frozen_version(db, owner_id, title="P8 other")
        bound_id, other_id = str(bound.id), str(other.id)
        db.commit()
    _legacy_dashed_run(legacy_id, bound_id)

    refused = _decision(client, headers, legacy_id, other_id)
    assert refused.status_code == 400, refused.text
    detail = refused.json()["detail"]
    assert detail["reason"] == "version_mismatch"
    assert detail["run_version"] == bound_id
    assert detail["decision_version"] == other_id

    with TestSession() as db:
        assert db.query(DecisionRecord).filter(
            DecisionRecord.paper_portfolio_id == uuid.UUID(legacy_id)
        ).count() == 0, "the refused decision still wrote a row"


def test_a_run_row_left_in_the_dashed_spelling_admits_the_claim_it_names(client):
    """The same row, the same second look, no contradiction: the hop is allowed.

    Without this half, a door that refuses whenever the fallback finds any row
    would pass the node above for the wrong reason.
    """
    owner_id, headers = _account(client, "p8b")
    legacy_id = _legacy_dashed_account(owner_id)
    with TestSession() as db:
        bound = _frozen_version(db, owner_id, title="P8 bound")
        bound_id = str(bound.id)
        db.commit()
    _legacy_dashed_run(legacy_id, bound_id)

    agreed = _decision(client, headers, legacy_id, bound_id)
    assert agreed.status_code == 201, agreed.text
    assert agreed.json()["linked_experiment_version_id"] == bound_id
