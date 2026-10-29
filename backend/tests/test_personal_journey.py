"""Personal-model journey: the five questions, worker visibility, recovery.

The journey endpoint is the read-only contract that answers "what am I
researching, what has been done, what came out of it, what is still missing,
what is next" without the caller assembling eligibility codes themselves.

Every bar, decision and label here is a SYNTHETIC FIXTURE generated inside the
test. These tests prove the engineering state is described truthfully; they do
not prove model effectiveness or profitability.

Worker liveness is exercised through the real heartbeat writer and the real
status reader. The queue path uses the real worker entry point, not a stub.
"""

import json
import os
import uuid
from datetime import datetime, timedelta

import pytest

from app.lib.timeutil import utcnow
from app.models.market_data import MarketDataMetadata
from app.models.training import TrainingJob, TrainingJobStatus
from app.services.personal_journey import STALE_CLAIM_SECONDS, _worker_status
from app.services.personal_training import PERSONAL_TRAINING_TYPE
from app.workers.training_worker import write_heartbeat
from tests.conftest import TestSession
from tests.test_personal_strategy_learning import (
    BARS,
    INSTRUMENT,
    _build_snapshot,
    _create_experiment,
    _grant_consent,
    _personal_artifacts,
    _run_worker_job,
    _seed_bars,
    _seed_confirmed_decisions,
    _user_id,
)


@pytest.fixture(autouse=True)
def _synthetic_market(setup_db):
    db = TestSession()
    try:
        _seed_bars(db)
        db.add(
            MarketDataMetadata(
                instrument_id=INSTRUMENT,
                provider_id="synthetic",
                data_nature="synthetic",
                first_bar_date=BARS[0],
                last_bar_date=BARS[-1],
                total_bars=len(BARS),
            )
        )
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture(autouse=True)
def _no_worker_heartbeat(monkeypatch):
    """Default every test to "worker not observed" unless it opts in."""
    monkeypatch.delenv("TRAINING_WORKER_HEARTBEAT_PATH", raising=False)
    yield


def _journey(client, headers, version_id):
    resp = client.get(
        "/api/v1/ml/personal/journey",
        headers=headers,
        params={"strategy_version_id": str(version_id)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _step(body, key):
    matches = [step for step in body["steps"] if step["key"] == key]
    assert len(matches) == 1, body["steps"]
    return matches[0]


def test_fresh_version_answers_five_questions_and_next_step(client, auth_headers):
    _, version_id = _create_experiment(client, auth_headers)

    body = _journey(client, auth_headers, version_id)

    assert [step["key"] for step in body["steps"]] == [
        "researching",
        "done",
        "result",
        "missing",
        "next",
    ]
    assert body["next_action"]["kind"] == "grant_consent"
    assert body["next_action"]["href"] == "#personalization-eligibility"
    assert body["missing"][0]["code"] == "CONSENT_MISSING"
    assert _step(body, "missing")["state"] == "blocked"
    assert _step(body, "result")["state"] == "empty"
    assert body["counts"]["usable_rows"] == 0
    assert body["counts"]["minimum_usable_rows"] == 200
    assert body["worker"]["state"] == "unknown"
    assert body["not_claimed"]


def test_missing_data_is_quantified_in_decisions_and_classes(client, auth_headers):
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(
        owner_id, uuid.UUID(version_id), days=2, per_day=2, actions=("buy",)
    )
    _grant_consent(client, auth_headers, version_id)

    body = _journey(client, auth_headers, version_id)

    codes = [item["code"] for item in body["missing"]]
    assert "INSUFFICIENT_CONFIRMED_DECISIONS" in codes
    assert "SINGLE_CLASS_DATASET" in codes
    insufficient = body["missing"][codes.index("INSUFFICIENT_CONFIRMED_DECISIONS")]
    assert "仲欠 196 筆可用決策" in insufficient["text_zh"]
    assert "現有 4 筆可用" in insufficient["text_zh"]
    single_class = body["missing"][codes.index("SINGLE_CLASS_DATASET")]
    assert "只有 1 個動作類別" in single_class["text_zh"]
    assert "買入 4 筆" in single_class["text_zh"]
    assert body["next_action"]["kind"] == "review_decisions"
    assert insufficient["text_zh"] in body["next_action"]["detail_zh"]
    assert "模擬工作區" in body["next_action"]["detail_zh"]
    # The next step must point at a real product entry, not require an id paste.
    assert body["next_action"]["href"].startswith("/experiments/")
    assert body["next_action"]["href"].endswith("/simulation")
    assert body["strategy"]["experiment_id"] in body["next_action"]["href"]


def test_journey_walks_snapshot_job_model_and_active_version(
    client, auth_headers, tmp_path, monkeypatch
):
    _personal_artifacts(tmp_path, monkeypatch)
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)

    body = _journey(client, auth_headers, version_id)
    assert body["next_action"]["kind"] == "create_snapshot"
    assert body["next_action"]["href"] == "#pl-preview"
    assert body["missing"][0].get("clear") is True

    snapshot = _build_snapshot(client, auth_headers, version_id)
    body = _journey(client, auth_headers, version_id)
    assert body["next_action"]["kind"] == "start_training"
    assert body["next_action"]["href"] == "#pl-training"
    assert body["counts"]["snapshots"] == 1
    assert str(snapshot["snapshot_id"])[:8] in _step(body, "result")["summary_zh"]

    started = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": snapshot["snapshot_id"]},
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    body = _journey(client, auth_headers, version_id)
    assert body["next_action"]["kind"] == "wait_worker"
    assert body["next_action"]["href"] is None
    assert body["counts"]["training_jobs"] == 1
    assert body["worker"]["pending_jobs"] == 1
    assert "worker 未確認在運行" in body["worker"]["waiting_reason_zh"]
    assert body["jobs"][0]["job_id"] == job_id

    _run_worker_job(uuid.UUID(job_id))

    db = TestSession()
    try:
        job = db.query(TrainingJob).filter(TrainingJob.id == uuid.UUID(job_id)).one()
        assert job.status is TrainingJobStatus.completed
        model_version_id = job.best_params["model_version_id"]
    finally:
        db.close()

    body = _journey(client, auth_headers, version_id)
    assert body["next_action"]["kind"] == "activate_model"
    assert body["next_action"]["href"] == "#pl-models"
    assert body["counts"]["model_versions"] == 1
    assert body["worker"]["pending_jobs"] == 0
    result = _step(body, "result")
    assert "版本 1" in result["summary_zh"]
    assert str(body["jobs"][0]["model_version_id"]) == model_version_id

    activated = client.post(
        f"/api/v1/ml/personal/models/{model_version_id}/activate",
        headers=auth_headers,
    )
    assert activated.status_code == 200, activated.text
    body = _journey(client, auth_headers, version_id)
    assert body["next_action"]["kind"] == "predict"
    assert body["next_action"]["href"] == "#pl-predict"
    assert "使用中版本" in json.dumps(_step(body, "result")["facts"], ensure_ascii=False)


def test_failed_and_reclaimed_jobs_are_reported_without_losing_data(
    client, auth_headers
):
    owner_id = _user_id()
    experiment_id, version_id = _create_experiment(client, auth_headers)

    db = TestSession()
    try:
        db.add(
            TrainingJob(
                user_id=owner_id,
                experiment_id=uuid.UUID(experiment_id),
                status=TrainingJobStatus.completed,
                parameter_grid={},
                strategy_spec_snapshot={
                    "training_type": PERSONAL_TRAINING_TYPE,
                    "strategy_version_id": str(version_id),
                    "snapshot_id": str(uuid.uuid4()),
                    "claimed_at": (utcnow() - timedelta(minutes=12)).isoformat(),
                    "reclaimed_at": (utcnow() - timedelta(minutes=10)).isoformat(),
                },
                created_at=utcnow() - timedelta(minutes=20),
            )
        )
        db.add(
            TrainingJob(
                user_id=owner_id,
                experiment_id=uuid.UUID(experiment_id),
                status=TrainingJobStatus.failed,
                parameter_grid={},
                strategy_spec_snapshot={
                    "training_type": PERSONAL_TRAINING_TYPE,
                    "strategy_version_id": str(version_id),
                    "snapshot_id": str(uuid.uuid4()),
                },
                error_details="訓練中止：artifact 無法寫入。",
                created_at=utcnow() - timedelta(minutes=5),
            )
        )
        db.commit()
    finally:
        db.close()

    body = _journey(client, auth_headers, version_id)
    codes = [item["code"] for item in body["missing"]]
    assert "TRAINING_JOB_FAILED" in codes
    assert "artifact 無法寫入" in body["missing"][codes.index("TRAINING_JOB_FAILED")]["text_zh"]
    assert "JOB_RECLAIMED" in codes
    reclaimed = body["missing"][codes.index("JOB_RECLAIMED")]
    assert "沒有遺失資料" in reclaimed["text_zh"]


def test_journey_is_owner_scoped(client, auth_headers):
    _, version_id = _create_experiment(client, auth_headers)
    payload = {
        "email": "journey-other@alethra.io",
        "password": "otherpass123",
        "display_name": "Other",
        "user_type": "individual",
    }
    client.post("/api/auth/register", json=payload)
    login = client.post(
        "/api/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.get(
        "/api/v1/ml/personal/journey",
        headers=other_headers,
        params={"strategy_version_id": str(version_id)},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "strategy_version_not_found"


def test_worker_status_reports_offline_stale_and_idle(tmp_path, monkeypatch):
    pending = TrainingJob(id=uuid.uuid4(), status=TrainingJobStatus.pending)
    path = tmp_path / "worker-heartbeat.json"
    monkeypatch.setenv("TRAINING_WORKER_HEARTBEAT_PATH", str(path))

    offline = _worker_status([pending], [])
    assert offline["state"] == "offline"
    assert "worker 啟動後會自動處理" in offline["waiting_reason_zh"]

    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "state": "idle",
                "beat_at": (utcnow() - timedelta(seconds=600)).isoformat(),
                "poll_interval_seconds": 2,
            }
        ),
        encoding="utf-8",
    )
    stale = _worker_status([pending], [])
    assert stale["state"] == "stale"
    assert "心跳已過期" in stale["label_zh"]
    assert "未確認在運行" in stale["waiting_reason_zh"]

    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "state": "idle",
                "beat_at": utcnow().isoformat(),
                "poll_interval_seconds": 2,
            }
        ),
        encoding="utf-8",
    )
    idle = _worker_status([pending], [])
    assert idle["state"] == "idle"
    assert "下次輪詢會處理" in idle["waiting_reason_zh"]

    stale_claim = TrainingJob(
        id=uuid.uuid4(),
        status=TrainingJobStatus.running,
        strategy_spec_snapshot={
            "training_type": PERSONAL_TRAINING_TYPE,
            "claimed_at": (utcnow() - timedelta(seconds=STALE_CLAIM_SECONDS + 60)).isoformat(),
        },
    )
    body = _worker_status([], [stale_claim])
    assert "worker 重啟後會自動排回隊列" in body["waiting_reason_zh"]


def test_worker_status_reports_a_fresh_stopped_or_failed_heartbeat(tmp_path, monkeypatch):
    """A just-stopped worker must never read as idle standby.

    The stop/error heartbeat is fresh by definition (age < stale window), so the
    age check alone would fall through to "idle" and a queued job would look
    like it will be picked up by a worker that has already exited.
    """
    pending = TrainingJob(id=uuid.uuid4(), status=TrainingJobStatus.pending)
    path = tmp_path / "worker-heartbeat.json"
    monkeypatch.setenv("TRAINING_WORKER_HEARTBEAT_PATH", str(path))

    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "state": "stopped",
                "beat_at": utcnow().isoformat(),
                "poll_interval_seconds": 2,
            }
        ),
        encoding="utf-8",
    )
    stopped = _worker_status([pending], [])
    assert stopped["state"] == "offline"
    assert "已停止" in stopped["label_zh"]
    assert "worker 未確認在運行" in stopped["waiting_reason_zh"]

    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "state": "error",
                "beat_at": utcnow().isoformat(),
                "poll_interval_seconds": 2,
                "error": "boom",
            }
        ),
        encoding="utf-8",
    )
    errored = _worker_status([], [])
    assert errored["state"] == "error"
    assert "boom" in errored["detail_zh"]


def test_heartbeat_writer_is_atomic_and_self_describing(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "heartbeat.json"
    monkeypatch.setenv("TRAINING_WORKER_HEARTBEAT_PATH", str(path))

    write_heartbeat("working", current_job_id="job-123", reclaimed=1)

    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["pid"] == os.getpid()
    assert body["state"] == "working"
    assert body["current_job_id"] == "job-123"
    assert body["reclaimed"] == 1
    assert datetime.fromisoformat(body["beat_at"]) is not None
    assert list(path.parent.iterdir()) == [path], "temporary file must not be left behind"


def test_journey_makes_no_quota_charge(client, auth_headers):
    """Read-only contract: opening the journey must not consume ai_usage."""
    from app.models.quota import QuotaCounter

    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id), days=1, per_day=1)
    _grant_consent(client, auth_headers, version_id)

    _journey(client, auth_headers, version_id)
    _journey(client, auth_headers, version_id)

    db = TestSession()
    try:
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == owner_id,
                QuotaCounter.quota_type == "ai_usage",
            )
            .first()
        )
        assert counter is None or counter.used == 0
    finally:
        db.close()


def test_journey_does_not_mutate_snapshots_or_decisions(client, auth_headers):
    """Repeated reads leave the dataset and review counts identical."""
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(
        owner_id, uuid.UUID(version_id), days=2, per_day=2, actions=("buy", "sell")
    )
    _grant_consent(client, auth_headers, version_id)

    first = _journey(client, auth_headers, version_id)
    second = _journey(client, auth_headers, version_id)

    assert first["counts"] == second["counts"]
    assert first["class_distribution"] == second["class_distribution"]
    assert first["next_action"]["kind"] == second["next_action"]["kind"] == "review_decisions"
    assert second["counts"]["snapshots"] == 0
