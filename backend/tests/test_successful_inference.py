"""Engineering inference through the formal personal-model endpoint.

What this file proves
---------------------
A request through ``POST /api/v1/ml/personal/models/{version_id}/predict``
clears the real authorization gate, reads real daily bars through the real
``features_as_of`` selection rules, loads a real artifact with the real
loader, predicts, writes a durable usage row and returns its id — and that id
is then consumable by decision lineage.

What this file does NOT prove
-----------------------------
The training job is a *test fixture* in a completed state. Nothing here ran
through the training worker, so this is not evidence that a real training run
produced the artifact, and it says nothing about market validity of any model.

Fixture facts the code actually requires
----------------------------------------
``personal_dataset.FEATURE_LOOKBACK_WINDOWS = [5, 10, 20]`` and
``MIN_FEATURE_BARS = 22``; a daily bar is readable only from
``bar_date + 1 day, 00:00 UTC``; selection keeps midnight-timestamped rows from
one provider. The artifact's ``feature_names`` are whatever
``build_feature_matrix`` returns for those windows — 14 names, sorted — so the
fixture derives them instead of inventing them.
"""

import hashlib
import json
import math
import os
import shutil
import uuid
from datetime import date, datetime, time, timedelta, timezone

import joblib
import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from app.ml.features import build_feature_matrix
from app.ml.personal_trainer import (
    PERSONAL_ARTIFACTS_DIR,
    PERSONAL_TASK_TYPE,
    load_personal_model,
)
from app.models.billing import UsageRecord
from app.models.market_data import MarketDataBar
from app.models.model_research import ModelResearchRecord, ModelType, ModelVersion
from app.models.paper_portfolio import DecisionRecord, PaperPortfolio, PortfolioStatus
from app.models.personal_model_selection import PersonalModelSelection
from app.models.training import TrainingJob, TrainingJobStatus
from app.services.personal_dataset import (
    FEATURE_LOOKBACK_WINDOWS,
    MIN_FEATURE_BARS,
    daily_bar_available_at,
    features_as_of,
)
from tests.conftest import TestSession

# Test-only instrument and window. Bars are engineering inputs: their prices are
# a deterministic sine-plus-drift series, not market data from any venue.
TEST_INSTRUMENT = "TEST-ENGINEERING-ASSET"
TEST_PROVIDER = "demo"
FIRST_BAR_DATE = date(2024, 1, 1)
N_BARS = 30  # comfortably above MIN_FEATURE_BARS after the close-time filter
AS_OF_DATE = (FIRST_BAR_DATE + timedelta(days=N_BARS)).isoformat()  # 2024-01-31


def _register_and_login(client, email):
    client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
            "email": email,
            "password": "testpass123",
            "display_name": email.split("@")[0],
            "user_type": "individual",
        },
    )
    login = client.post(
        "/api/auth/login", json={"email": email, "password": "testpass123"}
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _synthetic_close(i: int) -> float:
    """Deterministic, strictly positive series with non-degenerate returns."""
    return round(120.0 + 9.0 * math.sin(i / 2.3) + i * 0.85, 6)


def seed_daily_bars(db, *, instrument_id=TEST_INSTRUMENT, provider_id=TEST_PROVIDER,
                    n_bars=N_BARS, first_bar=FIRST_BAR_DATE):
    """Write ``n_bars`` completed daily bars that the as-of rules can actually see.

    Each row is stamped at midnight of its own ``bar_date`` (the grain marker
    ``_daily_bars_visible_by`` selects on) and carries ``interval_minutes=1440``
    so it is never confused with an intraday row.
    """
    bars = []
    for i in range(n_bars):
        bar_date = first_bar + timedelta(days=i)
        close = _synthetic_close(i)
        bar = MarketDataBar(
            instrument_id=instrument_id,
            provider_id=provider_id,
            bar_date=bar_date,
            bar_timestamp=datetime.combine(bar_date, time.min),
            open=round(close * 0.994, 6),
            high=round(close * 1.021, 6),
            low=round(close * 0.978, 6),
            close=close,
            volume=1_000_000 + i * 7_331,
            interval_minutes=1440,
        )
        db.add(bar)
        bars.append(bar)
    db.flush()
    return bars


def real_feature_names(db, instrument_id=TEST_INSTRUMENT, as_of_date=AS_OF_DATE):
    """The feature contract as the production pipeline actually computes it."""
    features, names, bar_date, exclusion = features_as_of(db, instrument_id, as_of_date)
    assert exclusion is None, f"feature build refused: {exclusion}"
    assert features is not None and names
    return names, features


def real_feature_rows(db, names, instrument_id=TEST_INSTRUMENT):
    """Feature vectors taken through ``features_as_of`` at successive decision dates.

    Each call re-runs the production selection and warm-up rules, so the rows
    carry the same dimension and ordering the endpoint will later produce.
    """
    rows = []
    for offset in range(MIN_FEATURE_BARS, N_BARS + 1):
        as_of = (FIRST_BAR_DATE + timedelta(days=offset)).isoformat()
        features, got_names, _, exclusion = features_as_of(
            db, instrument_id, as_of
        )
        if exclusion is None and features is not None and got_names == names:
            rows.append([features[name] for name in names])
    assert len(rows) >= 4, f"only {len(rows)} feature rows available for training"
    return rows


def write_engineering_artifact(owner_id, feature_names, training_rows,
                               labels=("hold", "buy", "sell")):
    """Save a loadable personal artifact and return (model_id, artifact_sha256).

    ``training_rows`` comes from the same ``build_feature_matrix`` output the
    loader will be asked to score, so dimension, order and scaler all agree with
    the production feature contract.
    """
    model_id = uuid.uuid4().hex
    model_dir = os.path.join(PERSONAL_ARTIFACTS_DIR, model_id)
    os.makedirs(model_dir, exist_ok=True)

    matrix = np.asarray(training_rows, dtype=float)
    # Cycle the label deterministically so every class is present for predict_proba.
    y = np.array([labels[i % len(labels)] for i in range(matrix.shape[0])])

    scaler = StandardScaler().fit(matrix)
    model = LogisticRegression(max_iter=500).fit(scaler.transform(matrix), y)

    joblib.dump(model, os.path.join(model_dir, "model.joblib"))
    joblib.dump(scaler, os.path.join(model_dir, "scaler.joblib"))

    digest = hashlib.sha256()
    for filename in ("model.joblib", "scaler.joblib"):
        with open(os.path.join(model_dir, filename), "rb") as fh:
            digest.update(fh.read())
    artifact_sha256 = digest.hexdigest()

    with open(os.path.join(model_dir, "feature_names.json"), "w") as fh:
        json.dump(feature_names, fh)

    metadata = {
        "model_id": model_id,
        "task_type": PERSONAL_TASK_TYPE,
        "artifact_scope": "personal_decision_model",
        "owner_id": str(owner_id),
        "user_id": str(owner_id),
        "artifact_sha256": artifact_sha256,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "model_type": "logistic_regression",
        "classes": list(labels),
        "feature_names": list(feature_names),
        "wording_zh": "工程測試產物，非訓練流程產出。",
        "not_for": ["市場方向預測", "投資建議"],
        "disclaimer_zh": "此 artifact 由測試 fixture 直接寫入，未經過訓練 worker。",
    }
    with open(os.path.join(model_dir, "metadata.json"), "w") as fh:
        json.dump(metadata, fh, indent=2, ensure_ascii=False)

    return model_id, artifact_sha256


def _create_skill_unit(db, owner_id):
    portfolio = PaperPortfolio(
        owner_id=owner_id,
        currency="USD",
        initial_virtual_cash=100000.0,
        current_virtual_cash=100000.0,
        status=PortfolioStatus.active,
    )
    db.add(portfolio)
    db.flush()
    return portfolio


def _complete_training_job(db, owner_id, artifact_sha256):
    """A completed general TrainingJob — the personal-model family, not agent strategy."""
    job = TrainingJob(
        user_id=owner_id,
        status=TrainingJobStatus.completed,
        parameter_grid={"training_type": "personal_decision_model"},
        results={"artifact_sha256": artifact_sha256},
        completed_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(job)
    db.flush()
    return job


def _personal_version(db, owner_id, spec, record=None):
    if record is None:
        record = ModelResearchRecord(
            owner_id=owner_id,
            title="Engineering personal model",
            purpose="personal_decision",
        )
        db.add(record)
        db.flush()

    version = ModelVersion(
        model_research_record_id=record.id,
        version_number=len(record.versions) + 1,
        model_type=ModelType.personal_decision_model,
        model_specification_json=spec,
    )
    db.add(version)
    db.flush()

    if record.active_version_id is None:
        record.active_version_id = version.id
    db.flush()
    return record, version


def _select(db, owner_id, version, record, skill_unit_id):
    selection = PersonalModelSelection(
        owner_id=owner_id,
        skill_unit_id=skill_unit_id,
        record_id=record.id,
        version_id=version.id,
        action="select",
        rationale="engineering fixture selection",
    )
    db.add(selection)
    db.flush()
    return selection


@pytest.fixture
def personal_env(client):
    """Owner + skill unit + market bars + loadable artifact + completed job + selection.

    The artifact directory is removed afterwards so a leaked artifact cannot be
    picked up by a later test.
    """
    from app.models.user import User

    email = f"infer-{uuid.uuid4().hex[:8]}@test.com"
    headers = _register_and_login(client, email)
    db = TestSession()
    artifacts = []
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        seed_daily_bars(db)
        feature_names, _ = real_feature_names(db)
        training_rows = real_feature_rows(db, feature_names)

        model_id, artifact_sha256 = write_engineering_artifact(
            user.id, feature_names, training_rows
        )
        artifacts.append(model_id)
        job = _complete_training_job(db, user.id, artifact_sha256)
        spec = {
            "task_type": PERSONAL_TASK_TYPE,
            "artifact_id": model_id,
            "artifact_sha256": artifact_sha256,
            "feature_names": feature_names,
            "training_job_id": str(job.id),
            "training_type": "personal_decision_model",
        }
        record, version = _personal_version(db, user.id, spec)
        portfolio = _create_skill_unit(db, user.id)
        _select(db, user.id, version, record, portfolio.id)
        db.commit()

        yield {
            "user": user,
            "headers": headers,
            "db": db,
            "portfolio": portfolio,
            "version": version,
            "record": record,
            "job": job,
            "spec": spec,
            "model_id": model_id,
            "artifact_sha256": artifact_sha256,
            "feature_names": feature_names,
            "training_rows": training_rows,
            "artifacts": artifacts,
        }
    finally:
        for created_id in artifacts:
            shutil.rmtree(
                os.path.join(PERSONAL_ARTIFACTS_DIR, created_id), ignore_errors=True
            )
        db.close()


def add_trained_version(env):
    """A second, independently trained version under the same model record.

    Its own artifact on disk, its own completed training job, its own checksum —
    so "the skill switched to this one" is a fact about two real models.
    """
    db, user = env["db"], env["user"]
    model_id, checksum = write_engineering_artifact(
        user.id, env["feature_names"], env["training_rows"]
    )
    env["artifacts"].append(model_id)
    job = _complete_training_job(db, user.id, checksum)
    _, version = _personal_version(
        db,
        user.id,
        {
            "task_type": PERSONAL_TASK_TYPE,
            "artifact_id": model_id,
            "artifact_sha256": checksum,
            "feature_names": env["feature_names"],
            "training_job_id": str(job.id),
            "training_type": "personal_decision_model",
        },
        record=env["record"],
    )
    db.commit()
    return version


def test_feature_contract_matches_production_pipeline(personal_env):
    """The fixture's names are the pipeline's names, and the bar floor is real."""
    assert FEATURE_LOOKBACK_WINDOWS == [5, 10, 20]
    assert MIN_FEATURE_BARS == max(FEATURE_LOOKBACK_WINDOWS) + 2 == 22

    names = personal_env["feature_names"]
    assert len(names) == 14
    assert names == sorted(names)

    db = personal_env["db"]
    rows = (
        db.query(MarketDataBar)
        .filter(MarketDataBar.instrument_id == TEST_INSTRUMENT)
        .all()
    )
    assert len(rows) == N_BARS

    cutoff = datetime.combine(date.fromisoformat(AS_OF_DATE), time.min)
    visible = [r for r in rows if daily_bar_available_at(r.bar_date) <= cutoff]
    assert len(visible) >= MIN_FEATURE_BARS


def test_artifact_loads_through_the_real_loader(personal_env):
    """The real loader, not a mock, reads the fixture artifact and agrees on identity."""
    model, scaler, metadata, feature_names = load_personal_model(
        personal_env["model_id"], owner_id=str(personal_env["user"].id)
    )
    assert metadata["task_type"] == PERSONAL_TASK_TYPE
    assert metadata["artifact_sha256"] == personal_env["artifact_sha256"]
    assert feature_names == personal_env["feature_names"]
    assert scaler is not None and model is not None

    with pytest.raises(PermissionError):
        load_personal_model(personal_env["model_id"], owner_id=str(uuid.uuid4()))


def test_successful_inference_through_formal_endpoint(client, personal_env):
    """Formal entry point returns a prediction and a durable, readable inference id."""
    db = personal_env["db"]
    version = personal_env["version"]
    portfolio = personal_env["portfolio"]

    response = client.post(
        f"/api/v1/ml/personal/models/{version.id}/predict",
        headers=personal_env["headers"],
        json={
            "instrument_id": TEST_INSTRUMENT,
            "as_of_date": AS_OF_DATE,
            "skill_unit_id": str(portfolio.id),
        },
    )
    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {response.text}"
    )

    body = response.json()
    assert body["predicted_action"] in ("hold", "buy", "sell")
    probs = body["action_probabilities"]
    assert set(probs) == {"hold", "buy", "sell"}
    assert abs(sum(probs.values()) - 1.0) < 1e-3
    assert body["resolved_version"]["model_version_id"] == str(version.id)
    assert body["resolved_version"]["record_id"] == str(personal_env["record"].id)

    inference_id = uuid.UUID(body["inference_id"])

    # The same identity is what the usage ledger holds.
    db.expire_all()
    usage = db.query(UsageRecord).filter(UsageRecord.id == inference_id).first()
    assert usage is not None
    assert usage.user_id == personal_env["user"].id
    assert usage.model == str(version.id)

    meta = usage.metadata_json
    assert meta["prediction_type"] == "personal_decision"
    assert meta["skill_unit_id"] == str(portfolio.id)
    assert meta["model_version_id"] == str(version.id)
    assert meta["artifact_id"] == personal_env["model_id"]
    assert meta["artifact_sha256"] == personal_env["artifact_sha256"]
    assert meta["training_job_id"] == str(personal_env["job"].id)
    assert meta["instrument_id"] == TEST_INSTRUMENT
    assert meta["as_of_date"] == AS_OF_DATE


def test_inference_id_is_consumable_by_decision_lineage(client, personal_env):
    """A decision records the inference id, and the read-back keeps that identity."""
    db = personal_env["db"]
    version = personal_env["version"]
    portfolio = personal_env["portfolio"]

    first = client.post(
        f"/api/v1/ml/personal/models/{version.id}/predict",
        headers=personal_env["headers"],
        json={
            "instrument_id": TEST_INSTRUMENT,
            "as_of_date": AS_OF_DATE,
            "skill_unit_id": str(portfolio.id),
        },
    )
    assert first.status_code == 200, first.text
    inference_a = uuid.UUID(first.json()["inference_id"])

    created = client.post(
        f"/api/v1/paper-portfolio/{portfolio.id}/decisions",
        headers=personal_env["headers"],
        json={
            "decision_type": "entry",
            "actor_type": "ai_draft",
            "rationale": "依模型估計建立的草稿決策",
            "expected_outcome": "測試記錄用途，不主張報酬",
            "linked_inference_id": str(inference_a),
        },
    )
    assert created.status_code == 201, created.text
    decision_id = uuid.UUID(created.json()["id"])
    assert created.json()["linked_inference_id"] == str(inference_a)

    # A second inference must not move the first decision's source.
    second = client.post(
        f"/api/v1/ml/personal/models/{version.id}/predict",
        headers=personal_env["headers"],
        json={
            "instrument_id": TEST_INSTRUMENT,
            "as_of_date": AS_OF_DATE,
            "skill_unit_id": str(portfolio.id),
        },
    )
    assert second.status_code == 200, second.text
    inference_b = uuid.UUID(second.json()["inference_id"])
    assert inference_b != inference_a

    db.expire_all()
    stored = db.query(DecisionRecord).filter(DecisionRecord.id == decision_id).one()
    assert stored.linked_inference_id == inference_a

    fetched = client.get(
        f"/api/v1/paper-portfolio/{portfolio.id}/decisions",
        headers=personal_env["headers"],
    )
    assert fetched.status_code == 200, fetched.text
    match = [d for d in fetched.json() if d["id"] == str(decision_id)]
    assert match and match[0]["linked_inference_id"] == str(inference_a)


def test_inference_refused_for_a_different_skill_unit(client, personal_env):
    """Selection is existential per skill: another skill unit cannot borrow it."""
    db = personal_env["db"]
    other = _create_skill_unit(db, personal_env["user"].id)
    db.commit()

    response = client.post(
        f"/api/v1/ml/personal/models/{personal_env['version'].id}/predict",
        headers=personal_env["headers"],
        json={
            "instrument_id": TEST_INSTRUMENT,
            "as_of_date": AS_OF_DATE,
            "skill_unit_id": str(other.id),
        },
    )
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["reason_code"] == "no_skill_unit_selection"
    assert detail["skill_unit_id"] == str(other.id)


def test_inference_refused_when_training_job_not_completed(client, personal_env):
    """A completed-job requirement is real: a running job cannot authorize inference."""
    db = personal_env["db"]
    job = db.query(TrainingJob).filter(TrainingJob.id == personal_env["job"].id).one()
    job.status = TrainingJobStatus.running
    db.commit()

    response = client.post(
        f"/api/v1/ml/personal/models/{personal_env['version'].id}/predict",
        headers=personal_env["headers"],
        json={
            "instrument_id": TEST_INSTRUMENT,
            "as_of_date": AS_OF_DATE,
            "skill_unit_id": str(personal_env["portfolio"].id),
        },
    )
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["reason_code"] == "training_job_not_completed"
    assert detail["context"]["job_status"] == "running"


def test_inference_refused_on_artifact_checksum_drift(client, personal_env):
    """Spec checksum must equal the job's recorded artifact checksum."""
    db = personal_env["db"]
    job = db.query(TrainingJob).filter(TrainingJob.id == personal_env["job"].id).one()
    job.results = {"artifact_sha256": "0" * 64}
    db.commit()

    response = client.post(
        f"/api/v1/ml/personal/models/{personal_env['version'].id}/predict",
        headers=personal_env["headers"],
        json={
            "instrument_id": TEST_INSTRUMENT,
            "as_of_date": AS_OF_DATE,
            "skill_unit_id": str(personal_env["portfolio"].id),
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["reason_code"] == "artifact_mismatch"


def test_inference_refused_for_other_owners_training_job(client, personal_env):
    """The training job must belong to the requesting owner."""
    db = personal_env["db"]
    job = db.query(TrainingJob).filter(TrainingJob.id == personal_env["job"].id).one()
    job.user_id = uuid.uuid4()
    db.commit()

    response = client.post(
        f"/api/v1/ml/personal/models/{personal_env['version'].id}/predict",
        headers=personal_env["headers"],
        json={
            "instrument_id": TEST_INSTRUMENT,
            "as_of_date": AS_OF_DATE,
            "skill_unit_id": str(personal_env["portfolio"].id),
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["reason_code"] == "training_job_not_found"


def test_personal_authorization_does_not_require_agent_activation(personal_env):
    """The personal gate must not read the agent-strategy approval table.

    This is the regression for the mis-wiring where personal inference was
    refused with ``no_approval`` because it demanded an
    AgentModelActivationApproval — a row that belongs to the agent-strategy
    family and is produced by a different trainer, job table and artifact type.
    """
    from app.models.agent_strategy import AgentModelActivationApproval
    from app.services.model_activation import check_model_version_inference_authorization

    db = personal_env["db"]
    assert db.query(AgentModelActivationApproval).count() == 0

    result = check_model_version_inference_authorization(
        db,
        personal_env["user"].id,
        personal_env["version"].id,
        personal_env["spec"],
        personal_env["portfolio"].id,
    )
    assert result["authorized"] is True, result
    assert result["reason_code"] is None
    assert result["training_job_id"] == str(personal_env["job"].id)
    assert result["skill_unit_id"] == str(personal_env["portfolio"].id)
    # The old contract reported an approval id; the personal contract has none.
    assert "approval_id" not in result


# ---------------------------------------------------------------------------
# The same door, reached without an HTTP request.
#
# A daily model service is the one caller that must run the formal inference
# from a worker, where no Request and no caller headers exist. These nodes pin
# that a worker-shaped call cannot bypass the selection gate, that a refusal
# costs nothing, and that its ledger row names a service as its origin instead
# of borrowing the owner's.
# ---------------------------------------------------------------------------


def _service_predict(env, *, version=None, skill_unit_id=None):
    """Call the formal runner the way a scheduler would: db plus ids, no Request."""
    from app.api.personal_models import PredictRequest, _run_personal_prediction

    db = env["db"]
    return _run_personal_prediction(
        db,
        version=env["version"] if version is None else version,
        record=env["record"],
        body=PredictRequest(
            instrument_id=TEST_INSTRUMENT,
            as_of_date=AS_OF_DATE,
            skill_unit_id=env["portfolio"].id if skill_unit_id is None else skill_unit_id,
        ),
        via="service_tick",
        current_user=env["user"],
        resolved_from="skill_unit_selection",
    )


def test_a_worker_shaped_call_answers_the_same_as_the_owner_button(client, personal_env):
    """No Request object is needed to get the same prediction and the same evidence."""
    db = personal_env["db"]
    version = personal_env["version"]

    button = client.post(
        f"/api/v1/ml/personal/models/{version.id}/predict",
        headers=personal_env["headers"],
        json={
            "instrument_id": TEST_INSTRUMENT,
            "as_of_date": AS_OF_DATE,
            "skill_unit_id": str(personal_env["portfolio"].id),
        },
    )
    assert button.status_code == 200, button.text
    tick = _service_predict(personal_env)

    assert tick["predicted_action"] == button.json()["predicted_action"]
    assert tick["action_probabilities"] == button.json()["action_probabilities"]
    assert tick["resolved_version"]["model_version_id"] == str(version.id)

    button_row = db.query(UsageRecord).filter(
        UsageRecord.id == uuid.UUID(button.json()["inference_id"])
    ).first()
    tick_row = db.query(UsageRecord).filter(
        UsageRecord.id == uuid.UUID(tick["inference_id"])
    ).first()
    assert button_row is not None and tick_row is not None

    # The two calls are told apart by exactly one thing: who asked.
    assert button_row.metadata_json["via"] == "jwt"
    assert tick_row.metadata_json["via"] == "service_tick"
    for key in (
        "prediction_type",
        "model_version_id",
        "artifact_id",
        "artifact_sha256",
        "training_job_id",
        "skill_unit_id",
        "instrument_id",
        "as_of_date",
    ):
        assert tick_row.metadata_json[key] == button_row.metadata_json[key], key


def test_a_worker_shaped_call_cannot_reach_a_version_the_skill_did_not_select(client, personal_env):
    """Moving the gate inside the runner closed the one route that could skip it."""
    from fastapi import HTTPException

    db = personal_env["db"]
    other_skill = _create_skill_unit(db, personal_env["user"].id)
    db.commit()

    def _rows():
        db.expire_all()
        return db.query(UsageRecord).filter(
            UsageRecord.user_id == personal_env["user"].id
        ).count()

    before = _rows()
    with pytest.raises(HTTPException) as exc:
        _service_predict(personal_env, skill_unit_id=other_skill.id)

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "inference_not_authorized"
    assert exc.value.detail["skill_unit_id"] == str(other_skill.id)
    # Nothing was predicted, so nothing was billed and nothing was recorded.
    assert _rows() == before


def test_a_superseded_version_is_refused_when_named_and_admitted_when_current(client, personal_env):
    """A service run cannot keep using the version the owner already replaced."""
    from fastapi import HTTPException

    db = personal_env["db"]
    stale = personal_env["version"]
    current = add_trained_version(personal_env)
    _select(db, personal_env["user"].id, current, personal_env["record"], personal_env["portfolio"].id)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        _service_predict(personal_env, version=stale)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "inference_not_authorized"

    served = _service_predict(personal_env, version=current)
    assert served["resolved_version"]["model_version_id"] == str(current.id)


def test_an_inference_the_service_ran_is_a_valid_decision_source(client, personal_env):
    """A suggestion the service produced can carry the owner's recorded decision."""
    db = personal_env["db"]
    tick = _service_predict(personal_env)

    created = client.post(
        f"/api/v1/paper-portfolio/{personal_env['portfolio'].id}/decisions",
        headers=personal_env["headers"],
        json={
            "decision_type": "entry",
            "actor_type": "human",
            "rationale": "Recorded against the estimate the service produced today",
            "expected_outcome": "Test outcome",
            "expected_timeframe_days": 7,
            "self_reported_confidence": "medium",
            "linked_inference_id": tick["inference_id"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["linked_inference_id"] == tick["inference_id"]

    row = db.query(UsageRecord).filter(
        UsageRecord.id == uuid.UUID(tick["inference_id"])
    ).first()
    assert row.metadata_json["via"] == "service_tick"


def test_a_recorded_decision_is_not_a_trade_journal_row(client, personal_env):
    """The exported journal carries order-bearing lineage, not every decision.

    ``GET /{id}/decisions`` returns a decision made without an intent, and the
    inference behind it survives there. The trade journal is built from the
    account's orders (``app/api/paper_portfolio.py:1467-1537``), so the same
    decision is absent from both export formats. This pins which door is
    authoritative for lineage: an owner who exported a journal after recording a
    thesis with no order would otherwise conclude the thesis was never made.
    """
    version = personal_env["version"]
    portfolio = personal_env["portfolio"]

    served = client.post(
        f"/api/v1/ml/personal/models/{version.id}/predict",
        headers=personal_env["headers"],
        json={
            "instrument_id": TEST_INSTRUMENT,
            "as_of_date": AS_OF_DATE,
            "skill_unit_id": str(portfolio.id),
        },
    )
    assert served.status_code == 200, served.text
    inference_id = served.json()["inference_id"]

    created = client.post(
        f"/api/v1/paper-portfolio/{portfolio.id}/decisions",
        headers=personal_env["headers"],
        json={
            "decision_type": "entry",
            "actor_type": "human",
            "rationale": "Recorded against a live inference, with no intent to trade",
            "expected_outcome": "Test outcome",
            "expected_timeframe_days": 7,
            "self_reported_confidence": "medium",
            "linked_inference_id": inference_id,
        },
    )
    assert created.status_code == 201, created.text
    decision_id = created.json()["id"]

    listed = client.get(
        f"/api/v1/paper-portfolio/{portfolio.id}/decisions",
        headers=personal_env["headers"],
    )
    assert listed.status_code == 200, listed.text
    mine = [d for d in listed.json() if d["id"] == decision_id]
    assert len(mine) == 1
    assert mine[0]["linked_inference_id"] == inference_id

    as_json = client.get(
        f"/api/v1/paper-portfolio/{portfolio.id}/journal?format=json",
        headers=personal_env["headers"],
    )
    assert as_json.status_code == 200, as_json.text
    assert as_json.json()["row_count"] == 0
    assert as_json.json()["records"] == []

    as_csv = client.get(
        f"/api/v1/paper-portfolio/{portfolio.id}/journal?format=csv",
        headers=personal_env["headers"],
    )
    assert as_csv.status_code == 200, as_csv.text
    csv_lines = [ln for ln in as_csv.text.splitlines() if ln.strip()]
    assert len(csv_lines) == 1, as_csv.text
    assert "linked_inference_id" in csv_lines[0]
    assert decision_id not in as_csv.text

