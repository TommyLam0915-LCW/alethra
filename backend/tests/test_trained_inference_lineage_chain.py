"""Train → activate → predict → decide → export as one line a caller can walk.

What this file proves
---------------------
One node takes a strategy version through the *formal* training entry, lets the
real worker fit and write the artifact, has the version row name those bytes,
switches one skill account onto it, asks the formal predict endpoint (real
loader, no injected model) for an inference, and then makes the owner's paper
decision name that inference *and* the frozen strategy version the model was
trained from — finally reading the same identity back from the decisions door
and the decision-history export.

The last two nodes extend the same chain across the other two doors that write a
decision for one account: the replay (whose version half comes from its own
session, not from the request) and the rule engine. All three persistent results
are read back side by side under one owner, one skill account and one frozen
strategy version, which is where "the engine attached somebody else's estimate"
would have to show up if it ever did.

The other lineage tests in this repo each cover one half:
``test_personal_strategy_learning.py::test_job_artifact_version_api_identity_chain``
stops at the prediction body and never reads ``inference_id``;
``test_successful_inference.py`` starts from an artifact and a version row the
fixture wrote itself. No node before this one crossed the seam, so nothing here
is a re-run of a passing test — the seam is what is under proof.

What this file does NOT prove
-----------------------------
The bars, decisions and labels are the deterministic synthetic fixture imported
from ``test_personal_strategy_learning``; the fit is real sklearn over fixture
rows. This is engineering-chain evidence only. It says nothing about whether any
model predicts anything, and it is not a PostgreSQL, live-service or browser
journey.
"""

import shutil
import uuid

import pytest

from tests.conftest import TestSession
from app.models.billing import UsageRecord
from app.models.model_research import ModelVersion
from app.models.paper_portfolio import DecisionRecord
from app.models.training import TrainingJob, TrainingJobStatus
from tests.test_manual_decision_books_linked_portfolio import (
    _advance,
    _replay_events,
    _submit,
    decision_history_csv_rows,
    decision_history_json,
    journal_json,
)
from tests.test_personal_strategy_learning import (  # noqa: F401  (autouse fixture re-registered here)
    BARS,
    INSTRUMENT,
    _artifact_sha256,
    _build_snapshot,
    _create_experiment,
    _grant_consent,
    _open_skill_unit,
    _personal_artifacts,
    _run_worker_job,
    _seed_confirmed_decisions,
    _select_for_skill,
    _synthetic_market,
    _user_id,
)

AS_OF = BARS[120].isoformat()

DECISION_BODY = {
    "decision_type": "entry",
    "actor_type": "human",
    "rationale": "依今日模型估計記錄的決策（測試，不主張報酬）",
    "expected_outcome": "讀回與匯出保留同一 inference 來源",
    "expected_timeframe_days": 7,
    "self_reported_confidence": "medium",
}


def _train_through_formal_entries(client, auth_headers, tmp_path, monkeypatch):
    """Legs 1-3: strategy version → formal training entry → artifact → version row.

    Nothing in here is staged: the job is started by the API and executed by
    ``training_worker.run_claimed_job``, so the artifact bytes and the
    ``model_versions`` row are both produced by the product's own code.
    """
    artifacts = _personal_artifacts(tmp_path, monkeypatch)
    owner_id = _user_id()
    experiment_id, strategy_version_id = _create_experiment(client, auth_headers)
    # The freeze is its own action, and a decision may only name a version that
    # has one — so the chain that wants to name it has to take this step too.
    locked = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{strategy_version_id}/lock",
        headers=auth_headers,
        json={"confirm": True, "reason": "測試：凍結後才能被決策綁定"},
    )
    assert locked.status_code == 200, locked.text
    _seed_confirmed_decisions(owner_id, uuid.UUID(strategy_version_id))
    _grant_consent(client, auth_headers, strategy_version_id)
    snapshot = _build_snapshot(client, auth_headers, strategy_version_id)

    started = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": snapshot["snapshot_id"]},
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    _run_worker_job(uuid.UUID(job_id))

    db = TestSession()
    try:
        job = db.query(TrainingJob).filter(TrainingJob.id == uuid.UUID(job_id)).one()
        assert job.status is TrainingJobStatus.completed, job.error_details
        version = (
            db.query(ModelVersion)
            .filter(ModelVersion.id == uuid.UUID(job.best_params["model_version_id"]))
            .one()
        )
        spec = dict(version.model_specification_json)
        model_version_id = str(version.id)
        record_id = str(version.model_research_record_id)
    finally:
        db.close()

    # Leg 3 is only bound if the row names bytes that are on disk right now.
    assert spec["strategy_version_id"] == strategy_version_id
    assert spec["snapshot_id"] == snapshot["snapshot_id"]
    assert spec["training_job_id"] == job_id
    assert spec["artifact_sha256"] == _artifact_sha256(artifacts, spec["artifact_id"])

    return {
        "artifacts": artifacts,
        "owner_id": owner_id,
        "experiment_id": experiment_id,
        "strategy_version_id": strategy_version_id,
        "model_version_id": model_version_id,
        "record_id": record_id,
        "spec": spec,
    }


def _activate_select_and_predict(client, auth_headers, chain):
    """Legs 4-6: activate → skill selection → the formal predict endpoint's own loader."""
    activated = client.post(
        f"/api/v1/ml/personal/models/{chain['model_version_id']}/activate",
        headers=auth_headers,
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["is_active"] is True

    chain["skill_unit_id"] = _open_skill_unit(client, auth_headers)
    _select_for_skill(
        client, auth_headers, chain["model_version_id"], chain["skill_unit_id"]
    )

    served = client.post(
        f"/api/v1/ml/personal/models/{chain['model_version_id']}/predict",
        headers=auth_headers,
        json={
            "instrument_id": INSTRUMENT,
            "as_of_date": AS_OF,
            "skill_unit_id": chain["skill_unit_id"],
        },
    )
    assert served.status_code == 200, served.text
    body = served.json()
    assert body["resolved_version"]["model_version_id"] == chain["model_version_id"]
    chain["inference_id"] = body["inference_id"]
    return chain


@pytest.fixture()
def trained_chain(client, auth_headers, tmp_path, monkeypatch):
    chain = _train_through_formal_entries(client, auth_headers, tmp_path, monkeypatch)
    try:
        yield _activate_select_and_predict(client, auth_headers, chain)
    finally:
        shutil.rmtree(chain["artifacts"], ignore_errors=True)


def _freeze_a_second_version(client, auth_headers):
    """Another owned, frozen strategy version with no model behind it."""
    experiment_id, version_id = _create_experiment(client, auth_headers, title="另一套策略")
    locked = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{version_id}/lock",
        headers=auth_headers,
        json={"confirm": True, "reason": "測試：作為被冒稱的版本"},
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["locked_at"] is not None
    return version_id


def test_the_inference_row_names_the_strategy_version_it_was_trained_from(client, trained_chain):
    """The billing row alone must be able to say which strategy version served it.

    Without this, no door can reconcile the version a decision names with the
    model execution it claims as its origin, and the decision-history export
    would be silent about it too.
    """
    db = TestSession()
    try:
        usage = (
            db.query(UsageRecord)
            .filter(UsageRecord.id == uuid.UUID(trained_chain["inference_id"]))
            .one()
        )
        metadata = usage.metadata_json
    finally:
        db.close()

    assert metadata["model_version_id"] == trained_chain["model_version_id"]
    assert metadata["artifact_sha256"] == trained_chain["spec"]["artifact_sha256"]
    assert metadata["training_job_id"] == trained_chain["spec"]["training_job_id"]
    assert metadata["strategy_version_id"] == trained_chain["strategy_version_id"]


def test_a_trained_models_inference_answers_a_decision_naming_its_frozen_version(
    client, auth_headers, trained_chain
):
    """Legs 1-7 and 9: one identity survives create, read-back and the export."""
    body = dict(DECISION_BODY)
    body["linked_inference_id"] = trained_chain["inference_id"]
    body["linked_experiment_version_id"] = trained_chain["strategy_version_id"]

    created = client.post(
        f"/api/v1/paper-portfolio/{trained_chain['skill_unit_id']}/decisions",
        headers=auth_headers,
        json=body,
    )
    assert created.status_code == 201, created.text
    decision_id = uuid.UUID(created.json()["id"])
    assert created.json()["linked_inference_id"] == trained_chain["inference_id"]
    assert (
        created.json()["linked_experiment_version_id"]
        == trained_chain["strategy_version_id"]
    )

    db = TestSession()
    try:
        stored = (
            db.query(DecisionRecord)
            .filter(DecisionRecord.id == decision_id)
            .one()
        )
        assert stored.linked_inference_id == uuid.UUID(trained_chain["inference_id"])
        assert stored.owner_id == trained_chain["owner_id"]
        assert str(stored.paper_portfolio_id) == trained_chain["skill_unit_id"]
        assert (
            stored.linked_experiment_version_id
            == uuid.UUID(trained_chain["strategy_version_id"])
        )
    finally:
        db.close()

    listed = client.get(
        f"/api/v1/paper-portfolio/{trained_chain['skill_unit_id']}/decisions",
        headers=auth_headers,
    )
    assert listed.status_code == 200, listed.text
    mine = [d for d in listed.json() if d["id"] == str(decision_id)]
    assert len(mine) == 1
    assert mine[0]["linked_inference_id"] == trained_chain["inference_id"]

    history = client.get(
        f"/api/v1/paper-portfolio/{trained_chain['skill_unit_id']}/decision-history?format=json",
        headers=auth_headers,
    )
    assert history.status_code == 200, history.text
    rows = [
        r for r in history.json()["records"] if r["decision_id"] == str(decision_id)
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["linked_inference_id"] == trained_chain["inference_id"]
    assert row["inference_source_state"] == "present"
    assert row["inference_model_version_id"] == trained_chain["model_version_id"]
    assert row["linked_experiment_version_id"] == trained_chain["strategy_version_id"]

    as_csv = [
        r
        for r in decision_history_csv_rows(
            client, auth_headers, trained_chain["skill_unit_id"]
        )
        if r["decision_id"] == str(decision_id)
    ]
    assert len(as_csv) == 1
    assert as_csv[0]["linked_inference_id"] == trained_chain["inference_id"]
    assert as_csv[0]["inference_model_version_id"] == trained_chain["model_version_id"]


def test_an_origin_that_cannot_name_its_version_cannot_be_claimed_with_one(
    client, auth_headers
):
    """Absence is not agreement: an origin that cannot say is refused, not passed.

    The row below is shaped like what the predict route wrote before it recorded
    ``strategy_version_id``, so this is the compatible-read path the door has to
    answer for without inventing a version nobody claimed.
    """
    from app.models.billing import UsageRecord
    from app.models.paper_portfolio import PaperPortfolio

    experiment_id, strategy_version_id = _create_experiment(
        client, auth_headers, title="無來源版本"
    )
    locked = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{strategy_version_id}/lock",
        headers=auth_headers,
        json={"confirm": True, "reason": "測試：凍結後才可被命名"},
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["locked_at"] is not None
    skill_unit_id = _open_skill_unit(client, auth_headers)

    db = TestSession()
    try:
        portfolio = db.query(PaperPortfolio).filter(
            PaperPortfolio.id == uuid.UUID(skill_unit_id)
        ).one()
        origin = UsageRecord(
            user_id=portfolio.owner_id,
            subscription_id=None,
            endpoint="/api/v1/ml/personal/models/{version_id}/predict",
            model=None,
            tokens_used=0,
            cost=0,
            metadata_json={
                "prediction_type": "personal_decision",
                "via": "test_lane",
                "skill_unit_id": str(portfolio.id),
                "instrument_id": INSTRUMENT,
                "as_of_date": AS_OF,
            },
        )
        db.add(origin)
        db.commit()
        inference_id = str(origin.id)
    finally:
        db.close()

    body = dict(DECISION_BODY)
    body["linked_inference_id"] = inference_id
    body["linked_experiment_version_id"] = strategy_version_id
    created = client.post(
        f"/api/v1/paper-portfolio/{skill_unit_id}/decisions",
        headers=auth_headers,
        json=body,
    )
    assert created.status_code == 400, created.text
    detail = created.json()["detail"]
    assert detail["reason"] == "inference_version_unnamed"
    assert detail["decision_version"] == strategy_version_id

    db = TestSession()
    try:
        assert (
            db.query(DecisionRecord)
            .filter(DecisionRecord.linked_inference_id == uuid.UUID(inference_id))
            .count()
            == 0
        )
    finally:
        db.close()


def test_a_decision_naming_a_version_the_inference_was_not_trained_from_is_refused(
    client, auth_headers, trained_chain
):
    """The two halves of the version context have to be reconciled at the door.

    Each side is verified alone today: the named version is this owner's own
    frozen one, and the inference is this owner's own personal-model prediction
    for this skill account. Nothing compares them, so a decision can attach a
    model execution to a strategy the model never saw.
    """
    other_version_id = _freeze_a_second_version(client, auth_headers)

    body = dict(DECISION_BODY)
    body["linked_inference_id"] = trained_chain["inference_id"]
    body["linked_experiment_version_id"] = other_version_id

    created = client.post(
        f"/api/v1/paper-portfolio/{trained_chain['skill_unit_id']}/decisions",
        headers=auth_headers,
        json=body,
    )
    assert created.status_code == 400, created.text
    detail = created.json()["detail"]
    assert detail["reason"] == "inference_version_mismatch"
    assert detail["inference_strategy_version_id"] == trained_chain["strategy_version_id"]
    assert detail["decision_version"] == other_version_id

    db = TestSession()
    try:
        assert (
            db.query(DecisionRecord)
            .filter(DecisionRecord.linked_inference_id == uuid.UUID(trained_chain["inference_id"]))
            .count()
            == 0
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# The same chain asked of the other two doors that write a decision row.
# ---------------------------------------------------------------------------

SESSION_CASH = 100_000.0
PROVIDER = "synthetic"


def _start_replay(client, headers, strategy_version_id):
    """A replay running the frozen strategy version the artifact was fit from.

    Deliberately no account yet: the bars consumed before the ledger is attached
    move only the replay's own clock, so the account below starts flat and a
    human intent can still be honoured.
    """
    started = client.post(
        f"/api/v1/simulations/versions/{strategy_version_id}",
        headers=headers,
        json={"initial_virtual_cash": SESSION_CASH, "provider_id": PROVIDER},
    )
    assert started.status_code == 201, started.text
    return started.json()["id"]


def _bind_account(client, headers, session_id):
    """Attach the settlement ledger, through the portfolio door a customer uses.

    The row this returns is one account asked of three writers: the skill unit the
    model is selected and predicted for, the paper desk's account, and the ledger
    the replay settles into.
    """
    created = client.post("/api/v1/paper-portfolio", headers=headers, json={
        "initial_virtual_cash": SESSION_CASH,
        "currency": "USD",
        "simulation_session_id": session_id,
    })
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _predict_for(client, headers, chain, skill_unit_id):
    served = client.post(
        f"/api/v1/ml/personal/models/{chain['model_version_id']}/predict",
        headers=headers,
        json={
            "instrument_id": INSTRUMENT,
            "as_of_date": AS_OF,
            "skill_unit_id": skill_unit_id,
        },
    )
    assert served.status_code == 200, served.text
    return served.json()["inference_id"]


def _account_decisions(client, headers, portfolio_id):
    resp = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_trained_inference_originates_a_replay_order_and_survives_into_both_stores(
    client, auth_headers, trained_chain
):
    """The writable side of the replay's version leg, on a model that was really fit.

    ``tests/test_manual_decision_books_linked_portfolio.py`` proves both doors
    against usage rows its fixtures wrote; this node asks the replay about an
    origin a real training run recorded, on a session whose own version is the one
    that run was trained from. The submission writes two rows, so the claim is
    about both of them — the account's decision and the replay's own ledger row —
    rather than whichever one a response happens to echo.
    """
    session_id = _start_replay(client, auth_headers, trained_chain["strategy_version_id"])
    assert _advance(client, auth_headers, session_id).status_code == 200
    portfolio_id = _bind_account(client, auth_headers, session_id)

    _select_for_skill(
        client, auth_headers, trained_chain["model_version_id"], portfolio_id
    )
    inference_id = _predict_for(client, auth_headers, trained_chain, portfolio_id)

    submitted = _submit(
        client, auth_headers, session_id, action="buy", quantity=20,
        linked_inference_id=inference_id,
    )
    assert submitted.status_code == 200, submitted.text

    # One submission writes three rows in three tables: the replay's own
    # per-step decision (which is the ``decision`` the response echoes), the
    # account's ledger row and the press's event row. They do not share an id, so
    # the ledger row is found by the lineage it carries rather than by guessing
    # which of the three a response happened to render.
    record_rows = [
        row for row in _account_decisions(client, auth_headers, portfolio_id)
        if row["linked_inference_id"] == inference_id
    ]
    assert len(record_rows) == 1, record_rows
    record = record_rows[0]
    decision_id = record["id"]
    assert record["actor_type"] == "human"
    assert record["linked_experiment_version_id"] == trained_chain["strategy_version_id"]

    events = _replay_events(session_id)
    assert [e["linked_inference_id"] for e in events] == [inference_id], events
    assert events[0]["server_strategy_version_id"] == trained_chain["strategy_version_id"]

    journal = journal_json(client, auth_headers, portfolio_id)["records"]
    assert [r["decision_id"] for r in journal] == [decision_id], journal
    assert journal[0]["linked_inference_id"] == inference_id
    assert journal[0]["linked_experiment_version_id"] == trained_chain["strategy_version_id"]

    history = decision_history_json(client, auth_headers, portfolio_id)["records"]
    row = [r for r in history if r["decision_id"] == decision_id][0]
    assert row["inference_source_state"] == "present"
    assert row["inference_model_version_id"] == trained_chain["model_version_id"]
    assert row["inference_strategy_version_id"] == trained_chain["strategy_version_id"]
    assert row["linked_experiment_version_id"] == trained_chain["strategy_version_id"]

    as_csv = [
        r
        for r in decision_history_csv_rows(client, auth_headers, portfolio_id)
        if r["decision_id"] == decision_id
    ][0]
    assert as_csv["linked_inference_id"] == inference_id
    assert as_csv["inference_model_version_id"] == trained_chain["model_version_id"]


def test_the_engine_writes_to_the_same_account_without_taking_the_origin(
    client, auth_headers, trained_chain
):
    """Paper desk, replay and rule engine, under one owner, account and version.

    Three doors record into one ``decision_records`` table, and the lineage means
    something different in each: two of them were told which model execution the
    human acted on, and the third fires from the strategy's own rules with nobody
    naming an origin. The account here has an inference available to attach —
    selected for it, predicted for it, named by both human doors below — so if any
    write path filled the column from "whatever this skill got last", it would show
    up here rather than passing unnoticed.
    """
    session_id = _start_replay(client, auth_headers, trained_chain["strategy_version_id"])
    portfolio_id = _bind_account(client, auth_headers, session_id)
    _select_for_skill(
        client, auth_headers, trained_chain["model_version_id"], portfolio_id
    )
    inference_id = _predict_for(client, auth_headers, trained_chain, portfolio_id)

    # The clock runs before anyone states an intent, with the account already
    # attached: whatever that step books belongs to the strategy's rules alone.
    # Going first also means the human's sell below has a position to act on, and
    # that the engine row was written on a path that was never handed an inference
    # it could attach.
    for _ in range(2):
        advanced = _advance(client, auth_headers, session_id)
        assert advanced.status_code == 200, advanced.text

    desk_body = dict(DECISION_BODY)
    desk_body["linked_inference_id"] = inference_id
    desk = client.post(
        f"/api/v1/paper-portfolio/{portfolio_id}/decisions",
        headers=auth_headers,
        json=desk_body,
    )
    assert desk.status_code == 201, desk.text
    assert desk.json()["linked_experiment_version_id"] == (
        trained_chain["strategy_version_id"]
    )

    replay = _submit(
        client, auth_headers, session_id, action="sell",
        linked_inference_id=inference_id,
    )
    assert replay.status_code == 200, replay.text

    rows = _account_decisions(client, auth_headers, portfolio_id)
    humans = [r for r in rows if r["actor_type"] == "human"]
    systems = [r for r in rows if r["actor_type"] == "system"]
    assert len(humans) == 2, [(r["id"], r["actor_type"]) for r in rows]
    desk_id = desk.json()["id"]
    replay_id = [r["id"] for r in humans if r["id"] != desk_id][0]
    for row in humans:
        assert row["linked_inference_id"] == inference_id, row
        assert row["linked_experiment_version_id"] == (
            trained_chain["strategy_version_id"]
        ), row
    assert systems, (
        "the engine recorded no decision on this bar, so the comparison below "
        "would be an empty set agreeing with itself"
    )
    for row in systems:
        assert row["linked_inference_id"] is None, row
        assert row["linked_experiment_version_id"] == (
            trained_chain["strategy_version_id"]
        ), row

    history = {
        r["decision_id"]: r
        for r in decision_history_json(client, auth_headers, portfolio_id)["records"]
    }
    assert history[desk_id]["inference_source_state"] == "present"
    assert history[replay_id]["inference_source_state"] == "present"
    for row in systems:
        cell = history[row["id"]]
        assert cell["inference_source_state"] == "none", cell
        assert cell["linked_inference_id"] is None, cell
        assert cell["inference_model_version_id"] is None, cell
        assert cell["inference_strategy_version_id"] is None, cell
