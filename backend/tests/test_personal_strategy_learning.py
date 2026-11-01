"""Personal strategy learning: review → snapshot → trainer → version → API.

Every bar, decision and label in this module is generated deterministically
inside the test and is a SYNTHETIC FIXTURE. These tests prove the engineering
chain works and that it refuses what it must refuse. They do not prove that a
real owner's model is effective, that it predicts profit, or that it improves
with use — no real user data is involved anywhere.
"""
import hashlib
import json
import math
import uuid
from datetime import date, datetime, time, timedelta

import pytest

from tests.conftest import TestSession
from app.models.decision_label import DecisionLabelState
from app.models.human_decision import (
    HumanDecisionAction,
    HumanDecisionEvent,
    HumanDecisionExecutionStatus,
)
from app.models.market_data import MarketDataBar, MarketDataMetadata
from app.models.model_research import ModelEvaluation, ModelVersion
from app.models.personal_dataset import PersonalizationDatasetSnapshot
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.simulation import SimulationSession, SimulationStatus
from app.models.training import TrainingJob, TrainingJobStatus
from app.models.user import User
from app.services.decision_labels import record_label

INSTRUMENT = "US:AAPL"
DECISION_START_INDEX = 30
DECISION_STEP = 2
DECISION_DAYS = 41
DECISIONS_PER_DAY = 5
TOTAL_DECISIONS = DECISION_DAYS * DECISIONS_PER_DAY  # 205


def _business_days(start: date, count: int):
    days = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


BARS = _business_days(date(2024, 1, 1), 140)


def _price(i: int) -> float:
    return 100.0 + 0.05 * i + 6.0 * math.sin(i / 4.0) + 0.25 * (i % 7)


def _seed_bars(db, instrument: str = INSTRUMENT, start: int = 0, end: int = len(BARS)):
    for i in range(start, end):
        day = BARS[i]
        close = _price(i)
        db.add(
            MarketDataBar(
                instrument_id=instrument,
                provider_id="synthetic",
                bar_date=day,
                bar_timestamp=datetime.combine(day, datetime.min.time()),
                open=close - 0.4,
                high=close + 0.8,
                low=close - 0.9,
                close=close,
                volume=1_000_000 + (i % 5) * 10_000,
            )
        )


@pytest.fixture(autouse=True)
def _synthetic_market(setup_db):
    """Deterministic synthetic OHLCV series, labelled as synthetic.

    The metadata row matters: it is what makes the trained artifact carry the
    ``synthetic_market_features`` warning instead of presenting fixture data as
    real market history.
    """
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


def _user_id(email: str = "test@alethra.io") -> uuid.UUID:
    db = TestSession()
    try:
        return db.query(User).filter(User.email == email).one().id
    finally:
        db.close()


def _create_experiment(client, headers, title="Personal learning"):
    resp = client.post(
        "/api/v1/experiments/",
        headers=headers,
        json={
            "title": title,
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": [INSTRUMENT],
                "start_date": "2024-01-02",
                "end_date": "2024-06-28",
                "initial_cash": 100000,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], body["current_version_id"]


def _new_session(db, owner_id, version_id, day) -> uuid.UUID:
    """A real simulation session bound to the owner's experiment version.

    ``human_decision_events.simulation_session_id`` is NOT NULL with a real FK
    on PostgreSQL, so fixtures must create the session they reference rather
    than inventing an id. One session per event also keeps the dataset
    builder's source-dedupe keys distinct, exactly as a distinct decision
    would be.
    """
    snapshot_day = day if isinstance(day, date) else date.fromisoformat(day)
    session = SimulationSession(
        experiment_version_id=uuid.UUID(str(version_id)),
        owner_id=owner_id,
        initial_virtual_cash=100_000.0,
        started_at=datetime.combine(snapshot_day, time(8, 30)),
        data_cutoff_at=snapshot_day.isoformat(),
        status=SimulationStatus.active,
    )
    db.add(session)
    db.flush()
    return session.id


def _add_event(
    owner_id,
    version_id,
    *,
    action="buy",
    snapshot_date=None,
    cutoff=None,
    instrument=INSTRUMENT,
    created_at=None,
    key=None,
):
    db = TestSession()
    try:
        day = snapshot_date or BARS[DECISION_START_INDEX].isoformat()
        event = HumanDecisionEvent(
            owner_id=owner_id,
            simulation_session_id=_new_session(db, owner_id, version_id, day),
            idempotency_key=key or uuid.uuid4().hex,
            action=HumanDecisionAction(action),
            quantity=10.0 if action != "hold" else None,
            rationale="synthetic fixture decision",
            server_strategy_version_id=version_id,
            server_instrument_id=instrument,
            server_step_number=1,
            server_snapshot_date=day,
            server_data_cutoff_at=cutoff or day,
            execution_status=HumanDecisionExecutionStatus.record_only,
            actor_type="human",
            created_at=created_at or datetime(2024, 1, 2, 9, 0),
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event.id
    finally:
        db.close()


def _seed_confirmed_decisions(
    owner_id,
    version_id,
    *,
    days=DECISION_DAYS,
    per_day=DECISIONS_PER_DAY,
    actions=("buy", "sell"),
):
    """205 deterministic confirmed decisions, alternating buy/sell."""
    db = TestSession()
    ids = []
    try:
        author = db.query(User).filter(User.id == owner_id).one()
        index = 0
        for d in range(days):
            bar_index = DECISION_START_INDEX + d * DECISION_STEP
            day = BARS[bar_index]
            for j in range(per_day):
                event = HumanDecisionEvent(
                    owner_id=owner_id,
                    simulation_session_id=_new_session(db, owner_id, version_id, day),
                    idempotency_key=uuid.uuid4().hex,
                    action=HumanDecisionAction(actions[index % len(actions)]),
                    quantity=10.0,
                    rationale="synthetic fixture decision",
                    server_strategy_version_id=version_id,
                    server_instrument_id=INSTRUMENT,
                    server_step_number=d + 1,
                    server_snapshot_date=day.isoformat(),
                    server_data_cutoff_at=day.isoformat(),
                    execution_status=HumanDecisionExecutionStatus.record_only,
                    actor_type="human",
                    created_at=datetime.combine(day, time(9, 0))
                    + timedelta(minutes=j),
                )
                db.add(event)
                db.flush()
                record_label(
                    db,
                    event=event,
                    author=author,
                    state="confirmed",
                    action_label=None,
                    note=None,
                    exclusion_reason=None,
                )
                ids.append(event.id)
                index += 1
        db.commit()
        return ids
    finally:
        db.close()


def _grant_consent(client, headers, version_id):
    resp = client.put(
        "/api/v1/ml/personalization/consent",
        headers=headers,
        json={"strategy_version_ids": [version_id], "confirm": True},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _build_snapshot(client, headers, version_id):
    resp = client.post(
        "/api/v1/ml/personal/snapshots",
        headers=headers,
        json={"strategy_version_id": str(version_id)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _second_user_headers(client):
    payload = {
        "email": "other-owner@alethra.io",
        "password": "otherpass123",
        "display_name": "Other Owner",
        "user_type": "individual",
    }
    client.post("/api/auth/register", json=payload)
    login = client.post(
        "/api/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _artifact_sha256(tmp_dir, artifact_id):
    digest = hashlib.sha256()
    for name in ("model.joblib", "scaler.joblib"):
        with open(f"{tmp_dir}/{artifact_id}/{name}", "rb") as fh:
            digest.update(fh.read())
    return digest.hexdigest()


def _personal_artifacts(tmp_path, monkeypatch):
    path = tmp_path / "personal-artifacts"
    path.mkdir()
    monkeypatch.setattr(
        "app.ml.personal_trainer.PERSONAL_ARTIFACTS_DIR", str(path)
    )
    return path


def _run_worker_job(job_id):
    """Execute one claimed job through the real worker code path."""
    from app.workers import training_worker

    db = TestSession()
    try:
        training_worker.run_claimed_job(db, job_id)
    finally:
        db.close()


def _quota_state(owner_id, quota_type="ai_usage"):
    db = TestSession()
    try:
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == owner_id,
                QuotaCounter.quota_type == quota_type,
                QuotaCounter.period == "lifetime",
            )
            .first()
        )
        reservations = (
            db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == owner_id,
                QuotaReservation.quota_type == quota_type,
            )
            .all()
        )
        return {
            "used": counter.used if counter else 0,
            "statuses": [row.status for row in reservations],
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. A confirmed label changes what the snapshot preview reports.
# ---------------------------------------------------------------------------


def test_review_confirmation_updates_snapshot_preview(client, auth_headers):
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    day = BARS[DECISION_START_INDEX].isoformat()
    first = _add_event(owner_id, uuid.UUID(version_id), snapshot_date=day)
    second = _add_event(owner_id, uuid.UUID(version_id), snapshot_date=day, action="sell")
    third = _add_event(owner_id, uuid.UUID(version_id), snapshot_date=day, action="hold")

    listing = client.get(
        f"/api/v1/decision-review?strategy_version_id={version_id}",
        headers=auth_headers,
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["summary"] == {
        "total_current_decisions": 3,
        "confirmed": 0,
        "excluded": 0,
        "unverified_provenance": 0,
        "unreviewed": 3,
    }
    assert all(row["usable_for_training"] is False for row in body["decisions"])
    assert all(row["label"] is None for row in body["decisions"])

    preview = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_id},
    ).json()
    assert preview["included_count"] == 0
    assert preview["exclusion_breakdown"] == {"unconfirmed": 3}

    confirmed = client.post(
        f"/api/v1/decision-review/{first}/labels",
        headers=auth_headers,
        json={"state": "confirmed"},
    )
    assert confirmed.status_code == 201, confirmed.text
    label = confirmed.json()
    assert label["state"] == "confirmed"
    assert label["action_label"] == "buy"
    assert label["revision_number"] == 1
    assert label["recorded_action"] == "buy"

    preview = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_id},
    ).json()
    assert preview["included_count"] == 1
    assert preview["exclusion_breakdown"] == {"unconfirmed": 2}
    assert preview["class_distribution"] == {"buy": 1}

    excluded = client.post(
        f"/api/v1/decision-review/{second}/labels",
        headers=auth_headers,
        json={"state": "excluded", "exclusion_reason": "test_trade"},
    )
    assert excluded.status_code == 201, excluded.text
    assert excluded.json()["exclusion_reason"] == "test_trade"

    preview = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_id},
    ).json()
    assert preview["included_count"] == 1
    assert preview["exclusion_breakdown"] == {"unconfirmed": 1, "excluded": 1}

    detail = client.get(f"/api/v1/decision-review/{first}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["decision"]["recorded_action"] == "buy"
    assert detail.json()["decision"]["revision_count"] == 1

    # The recorded decision itself is never rewritten by a review.
    db = TestSession()
    try:
        event = db.query(HumanDecisionEvent).filter(HumanDecisionEvent.id == first).one()
        assert event.action is HumanDecisionAction.buy
        assert db.query(HumanDecisionEvent).count() == 3
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. An exclusion or a label revision produces a new snapshot; the old one stays.
# ---------------------------------------------------------------------------


def test_exclusion_and_revision_produce_new_snapshot(client, auth_headers):
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    event_ids = _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)

    first = _build_snapshot(client, auth_headers, version_id)
    assert first["included_count"] == TOTAL_DECISIONS
    assert first["excluded_count"] == 0

    excluded = client.post(
        f"/api/v1/decision-review/{event_ids[0]}/labels",
        headers=auth_headers,
        json={"state": "excluded", "exclusion_reason": "data_entry_error"},
    )
    assert excluded.status_code == 201, excluded.text
    revised = client.post(
        f"/api/v1/decision-review/{event_ids[1]}/labels",
        headers=auth_headers,
        json={
            "state": "confirmed",
            "action_label": "hold",
            "note": "修正：當時應該保持觀望",
        },
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["revision_number"] == 2
    assert revised.json()["action_overridden"] is True
    assert revised.json()["supersedes_label_id"] is not None

    second = _build_snapshot(client, auth_headers, version_id)
    assert second["snapshot_id"] != first["snapshot_id"]
    assert second["included_count"] == TOTAL_DECISIONS - 1
    assert second["exclusion_breakdown"] == {"excluded": 1}
    assert second["checksum"] != first["checksum"]

    old = client.get(
        f"/api/v1/ml/personal/snapshots/{first['snapshot_id']}", headers=auth_headers
    )
    assert old.status_code == 200
    assert old.json()["checksum"] == first["checksum"]
    assert old.json()["included_count"] == TOTAL_DECISIONS
    assert len(old.json()["rows_preview"]) == 5

    db = TestSession()
    try:
        rows = db.query(PersonalizationDatasetSnapshot).all()
        assert len(rows) == 2
        assert {row.checksum for row in rows} == {
            first["checksum"],
            second["checksum"],
        }
        # The revision is append-only: two label rows for that decision.
        from app.models.decision_label import DecisionLabel

        revisions = (
            db.query(DecisionLabel)
            .filter(DecisionLabel.decision_event_id == event_ids[1])
            .order_by(DecisionLabel.revision_number)
            .all()
        )
        assert [r.revision_number for r in revisions] == [1, 2]
        recorded = (
            db.query(HumanDecisionEvent)
            .filter(HumanDecisionEvent.id == event_ids[1])
            .one()
        )
        # Revision 1 confirmed the action the owner actually recorded.
        assert revisions[0].action_label == recorded.action.value
        assert revisions[1].action_label == "hold"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. Another account cannot see or touch this owner's data.
# ---------------------------------------------------------------------------


def test_cross_account_isolation_refuses_access(client, auth_headers):
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    event_ids = _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)
    snapshot = _build_snapshot(client, auth_headers, version_id)

    other = _second_user_headers(client)

    listing = client.get(
        f"/api/v1/decision-review?strategy_version_id={version_id}", headers=other
    )
    assert listing.status_code == 200
    assert listing.json()["decisions"] == []

    assert client.get(
        f"/api/v1/decision-review/{event_ids[0]}", headers=other
    ).status_code == 404
    assert client.post(
        f"/api/v1/decision-review/{event_ids[0]}/labels",
        headers=other,
        json={"state": "confirmed"},
    ).status_code == 404

    preview = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=other,
        json={"strategy_version_id": version_id},
    )
    assert preview.status_code == 400
    assert preview.json()["detail"]["code"] == "strategy_version_not_found"

    assert client.get("/api/v1/ml/personal/snapshots", headers=other).json() == {
        "snapshots": []
    }
    assert client.get(
        f"/api/v1/ml/personal/snapshots/{snapshot['snapshot_id']}", headers=other
    ).status_code == 404

    job = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=other,
        json={"snapshot_id": snapshot["snapshot_id"]},
    )
    assert job.status_code == 400
    assert job.json()["detail"]["code"] == "snapshot_not_found"

    model = client.get("/api/v1/ml/personal/models", headers=other).json()
    assert model == {"models": []}


# ---------------------------------------------------------------------------
# 4. Consent must exist, be active and cover the strategy version.
# ---------------------------------------------------------------------------


def test_consent_mismatch_blocks_snapshot_build(client, auth_headers):
    owner_id = _user_id()
    _, version_a = _create_experiment(client, auth_headers, "Consent A")
    _, version_b = _create_experiment(client, auth_headers, "Consent B")
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_a))

    preview = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_a},
    ).json()
    codes = {reason["code"] for reason in preview["reasons"]}
    assert "CONSENT_MISSING" in codes
    assert preview["can_build"] is False
    assert preview["consent"] == {
        "id": None,
        "consent_version": None,
        "granted_at": None,
        "in_scope": False,
    }

    refused = client.post(
        "/api/v1/ml/personal/snapshots",
        headers=auth_headers,
        json={"strategy_version_id": version_a},
    )
    assert refused.status_code == 400
    assert refused.json()["detail"]["code"] == "consent_missing"

    _grant_consent(client, auth_headers, version_a)

    out_of_scope = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_b},
    ).json()
    codes = {reason["code"] for reason in out_of_scope["reasons"]}
    assert "CONSENT_SCOPE_MISMATCH" in codes
    assert out_of_scope["consent"]["in_scope"] is False
    assert out_of_scope["can_build"] is False

    in_scope = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_a},
    ).json()
    assert in_scope["can_build"] is True
    assert in_scope["consent"]["in_scope"] is True
    assert in_scope["included_count"] == TOTAL_DECISIONS


# ---------------------------------------------------------------------------
# 5. Too little data / one class is refused — in the product AND in the trainer.
# ---------------------------------------------------------------------------


def test_insufficient_and_single_class_are_refused(client, auth_headers):
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    db = TestSession()
    try:
        author = db.query(User).filter(User.id == owner_id).one()
        day = BARS[DECISION_START_INDEX].isoformat()
        for i in range(10):
            event = HumanDecisionEvent(
                owner_id=owner_id,
                simulation_session_id=_new_session(db, owner_id, version_id, day),
                idempotency_key=uuid.uuid4().hex,
                action=HumanDecisionAction.buy,
                quantity=10.0,
                rationale="synthetic fixture decision",
                server_strategy_version_id=uuid.UUID(version_id),
                server_instrument_id=INSTRUMENT,
                server_step_number=1,
                server_snapshot_date=day,
                server_data_cutoff_at=day,
                execution_status=HumanDecisionExecutionStatus.record_only,
                actor_type="human",
                created_at=datetime(2024, 1, 2, 9, 0) + timedelta(minutes=i),
            )
            db.add(event)
            db.flush()
            record_label(
                db,
                event=event,
                author=author,
                state="confirmed",
                action_label=None,
                note=None,
                exclusion_reason=None,
            )
        db.commit()
    finally:
        db.close()

    _grant_consent(client, auth_headers, version_id)

    too_small = client.post(
        "/api/v1/ml/personal/snapshots",
        headers=auth_headers,
        json={"strategy_version_id": version_id},
    )
    assert too_small.status_code == 400
    assert too_small.json()["detail"]["code"] == "insufficient_confirmed_decisions"

    # Same shape, but 200 decisions all of one class → single class refusal.
    db = TestSession()
    try:
        author = db.query(User).filter(User.id == owner_id).one()
        for d in range(DECISION_DAYS):
            day = BARS[DECISION_START_INDEX + d * DECISION_STEP]
            for j in range(5):
                if d == 0 and j < 5:
                    continue  # 10 already exist; only top up to 205
                event = HumanDecisionEvent(
                    owner_id=owner_id,
                    simulation_session_id=_new_session(db, owner_id, version_id, day),
                    idempotency_key=uuid.uuid4().hex,
                    action=HumanDecisionAction.buy,
                    quantity=10.0,
                    rationale="synthetic fixture decision",
                    server_strategy_version_id=uuid.UUID(version_id),
                    server_instrument_id=INSTRUMENT,
                    server_step_number=d + 1,
                    server_snapshot_date=day.isoformat(),
                    server_data_cutoff_at=day.isoformat(),
                    execution_status=HumanDecisionExecutionStatus.record_only,
                    actor_type="human",
                    created_at=datetime.combine(day, time(9, 0))
                    + timedelta(minutes=j),
                )
                db.add(event)
                db.flush()
                record_label(
                    db,
                    event=event,
                    author=author,
                    state="confirmed",
                    action_label=None,
                    note=None,
                    exclusion_reason=None,
                )
        db.commit()
    finally:
        db.close()

    single_class = client.post(
        "/api/v1/ml/personal/snapshots",
        headers=auth_headers,
        json={"strategy_version_id": version_id},
    )
    assert single_class.status_code == 400
    assert single_class.json()["detail"]["code"] == "single_class_dataset"

    # The trainer refuses the same cases when called directly, so the product
    # guard is not the only thing standing between bad data and a model.
    from app.ml.personal_trainer import PersonalTrainingRefused, train_personal_model

    rows = [
        {
            "decision_event_id": f"e{i}",
            "decided_at": f"2024-02-{i + 1:02d}",
            "features": {"f1": float(i)},
            "action_label": "buy",
        }
        for i in range(19)
    ]
    with pytest.raises(PersonalTrainingRefused) as exc:
        train_personal_model(
            rows,
            ["f1"],
            owner_id=str(owner_id),
            snapshot_id="s",
            snapshot_checksum="c",
            strategy_version_id=version_id,
        )
    assert exc.value.code == "insufficient_rows"

    rows = [
        {
            "decision_event_id": f"e{i}",
            "decided_at": f"2024-02-{i + 1:02d}",
            "features": {"f1": float(i)},
            "action_label": "buy",
        }
        for i in range(40)
    ]
    with pytest.raises(PersonalTrainingRefused) as exc:
        train_personal_model(
            rows,
            ["f1"],
            owner_id=str(owner_id),
            snapshot_id="s",
            snapshot_checksum="c",
            strategy_version_id=version_id,
        )
    assert exc.value.code == "single_class_dataset"

    rows[0]["features"] = {}
    rows[0]["action_label"] = "sell"
    with pytest.raises(PersonalTrainingRefused) as exc:
        train_personal_model(
            rows,
            ["f1"],
            owner_id=str(owner_id),
            snapshot_id="s",
            snapshot_checksum="c",
            strategy_version_id=version_id,
        )
    assert exc.value.code == "missing_features"


# ---------------------------------------------------------------------------
# 6. Synthetic fixture: a real fit, a real artifact, a real reload, a real predict.
# ---------------------------------------------------------------------------


def test_synthetic_fixture_real_fit_save_load_predict(tmp_path, monkeypatch):
    from app.ml.personal_trainer import (
        load_personal_model,
        predict_personal,
        train_personal_model,
    )
    from app.ml.predictor import load_model as load_market_model

    artifacts = _personal_artifacts(tmp_path, monkeypatch)

    rows = []
    for i in range(60):
        signal = float(i) - 29.5
        rows.append(
            {
                "decision_event_id": f"fixture-{i:03d}",
                "decided_at": (datetime(2024, 1, 2) + timedelta(days=i)).isoformat(),
                "features": {"f1": signal, "f2": (i % 3) - 1.0},
                "action_label": "buy" if signal >= 0 else "sell",
            }
        )

    result = train_personal_model(
        rows,
        ["f1", "f2"],
        owner_id="fixture-owner",
        snapshot_id="fixture-snapshot",
        snapshot_checksum="fixture-checksum",
        strategy_version_id="fixture-strategy",
        market_data_nature={INSTRUMENT: ["synthetic"]},
    )

    assert result["task_type"] == "personal_decision_mimicry"
    assert result["reload_verified"] is True
    assert set(result["classes"]) == {"buy", "sell"}
    assert result["metrics"]["test"]["n_samples"] > 0
    assert result["metrics"]["test"]["accuracy"] > 0.8
    assert "baseline_test" in result["metrics"]
    assert "synthetic_market_features" in result["warnings"]

    model, scaler, metadata, feature_names = load_personal_model(
        result["model_id"], owner_id="fixture-owner"
    )
    assert feature_names == ["f1", "f2"]
    assert metadata["wording_zh"] == "模型估計你在此策略下的決策選擇"
    assert metadata["not_for"] == [
        "市場方向預測",
        "交易是否正確或有利潤",
        "投資建議",
    ]
    assert metadata["split"]["kind"] == "chronological_holdout"
    assert metadata["artifact_sha256"] == _artifact_sha256(
        artifacts, result["model_id"]
    )

    with pytest.raises(PermissionError):
        load_personal_model(result["model_id"], owner_id="someone-else")

    prediction = predict_personal(
        result["model_id"],
        {"f1": 12.0, "f2": 0.0},
        owner_id="fixture-owner",
    )
    assert prediction["predicted_action"] == "buy"
    assert abs(sum(prediction["action_probabilities"].values()) - 1.0) < 0.01

    # A personal action model must never be loadable by the OHLCV loader.
    with pytest.raises((FileNotFoundError, ValueError)):
        load_market_model(result["model_id"])

    with open(artifacts / result["model_id"] / "metadata.json") as fh:
        on_disk = json.load(fh)
    assert on_disk["artifact_scope"] == "personal_decision_model"
    assert on_disk["disclaimer_zh"].startswith("分數只反映模型模仿")


# ---------------------------------------------------------------------------
# 7. One identity from job → artifact → version → evaluation → API.
# ---------------------------------------------------------------------------


def test_job_artifact_version_api_identity_chain(
    client, auth_headers, tmp_path, monkeypatch
):
    artifacts = _personal_artifacts(tmp_path, monkeypatch)
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)
    snapshot = _build_snapshot(client, auth_headers, version_id)

    started = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": snapshot["snapshot_id"]},
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    assert started.json()["training_type"] == "personal_decision_model"

    _run_worker_job(uuid.UUID(job_id))

    db = TestSession()
    try:
        job = db.query(TrainingJob).filter(TrainingJob.id == uuid.UUID(job_id)).one()
        assert job.status is TrainingJobStatus.completed
        version = (
            db.query(ModelVersion)
            .filter(ModelVersion.id == uuid.UUID(job.best_params["model_version_id"]))
            .one()
        )
        evaluation = (
            db.query(ModelEvaluation)
            .filter(ModelEvaluation.model_version_id == version.id)
            .one()
        )
    finally:
        db.close()

    spec = version.model_specification_json
    assert spec["artifact_sha256"] == _artifact_sha256(artifacts, spec["artifact_id"])
    assert spec["snapshot_id"] == snapshot["snapshot_id"]
    assert spec["snapshot_checksum"] == snapshot["checksum"]
    assert spec["training_job_id"] == job_id
    assert spec["n_rows"] == TOTAL_DECISIONS
    assert evaluation.metrics_json["split"]["kind"] == "chronological_holdout"
    assert evaluation.metrics_json["baseline"] is not None

    models = client.get("/api/v1/ml/personal/models", headers=auth_headers)
    assert models.status_code == 200, models.text
    listed = models.json()["models"]
    assert len(listed) == 1
    model = listed[0]
    assert model["model_version_id"] == str(version.id)
    assert model["artifact_id"] == spec["artifact_id"]
    assert model["artifact_sha256"] == spec["artifact_sha256"]
    assert model["snapshot_id"] == snapshot["snapshot_id"]
    assert model["training_job_id"] == job_id
    assert model["is_active"] is False
    assert model["evaluation"]["metrics"]["split"]["kind"] == "chronological_holdout"

    activated = client.post(
        f"/api/v1/ml/personal/models/{version.id}/activate", headers=auth_headers
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["is_active"] is True

    as_of = BARS[120].isoformat()
    prediction = client.post(
        f"/api/v1/ml/personal/records/{model['record_id']}/predict",
        headers=auth_headers,
        json={"instrument_id": INSTRUMENT, "as_of_date": as_of},
    )
    assert prediction.status_code == 200, prediction.text
    body = prediction.json()
    assert body["resolved_version"]["model_version_id"] == str(version.id)
    assert body["resolved_version"]["resolved_from_active_alias"] is True
    assert body["artifact_sha256"] == spec["artifact_sha256"]
    assert body["snapshot"]["snapshot_id"] == snapshot["snapshot_id"]
    assert body["training_job_id"] == job_id
    assert body["predicted_action"] in spec["classes"]
    assert body["model_estimate_zh"] == "模型估計你在此策略下的決策選擇"
    assert "市場方向預測" in body["not_for"]
    assert body["as_of"]["feature_bar_date"] <= as_of

    # The immutable-version endpoint resolves the same identity explicitly.
    explicit = client.post(
        f"/api/v1/ml/personal/models/{version.id}/predict",
        headers=auth_headers,
        json={"instrument_id": INSTRUMENT, "as_of_date": as_of},
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["resolved_version"]["resolved_from_active_alias"] is False
    assert (
        explicit.json()["predicted_action"] == body["predicted_action"]
    )

    # One settled training unit plus two settled predictions.
    quota = _quota_state(owner_id)
    assert quota["used"] == 3
    assert set(quota["statuses"]) == {"settled"}
    assert len(quota["statuses"]) == 3


# ---------------------------------------------------------------------------
# 8. Later market data cannot change features of an earlier decision.
# ---------------------------------------------------------------------------


def test_future_data_does_not_change_earlier_features(client, auth_headers):
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    last_decision_bar = DECISION_START_INDEX + (DECISION_DAYS - 1) * DECISION_STEP
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)
    snapshot = _build_snapshot(client, auth_headers, version_id)

    from app.services.personal_dataset import features_as_of

    as_of = BARS[last_decision_bar].isoformat()
    db = TestSession()
    try:
        before = features_as_of(db, INSTRUMENT, as_of)
    finally:
        db.close()
    assert before[3] is None
    assert before[2] <= as_of

    # Append bars strictly after every decision date with absurd prices, and
    # add an absurd extra provider row in the future as well.
    db = TestSession()
    try:
        for i in range(last_decision_bar + 1, len(BARS)):
            close = _price(i) * 1000.0
            day = BARS[i]
            db.add(
                MarketDataBar(
                    instrument_id=INSTRUMENT,
                    provider_id="synthetic-future",
                    bar_date=day,
                    bar_timestamp=datetime.combine(day, datetime.min.time()),
                    open=close,
                    high=close * 1.1,
                    low=close * 0.9,
                    close=close,
                    volume=9_999_999,
                )
            )
        db.commit()
        after = features_as_of(db, INSTRUMENT, as_of)
    finally:
        db.close()

    assert after[0] == before[0]
    assert after[1] == before[1]
    assert after[2] == before[2]

    rebuilt = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_id},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["checksum"] == snapshot["checksum"]


# ---------------------------------------------------------------------------
# 9. Labels have no forward horizon; decision-time cutoffs are enforced.
# ---------------------------------------------------------------------------


def test_label_boundary_has_no_forward_horizon(client, auth_headers):
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)

    # A decision recorded with a snapshot later than its data cutoff is refused
    # as future leakage instead of being silently trained on.
    _add_event(
        owner_id,
        uuid.UUID(version_id),
        snapshot_date="2024-07-01",
        cutoff="2024-06-01",
    )
    preview = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_id},
    ).json()
    assert preview["exclusion_breakdown"] == {"future_leakage": 1}

    snapshot = _build_snapshot(client, auth_headers, version_id)
    started = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": snapshot["snapshot_id"]},
    )
    assert started.status_code == 202, started.text
    _run_worker_job(uuid.UUID(started.json()["job_id"]))

    db = TestSession()
    try:
        snapshot_row = db.query(PersonalizationDatasetSnapshot).filter(
            PersonalizationDatasetSnapshot.id == uuid.UUID(snapshot["snapshot_id"])
        ).one()
        for row in snapshot_row.rows_json:
            assert row["feature_bar_date"] <= row["snapshot_date"]
            assert row["data_cutoff_at"] >= row["snapshot_date"]
        version = db.query(ModelVersion).one()
        split = version.model_specification_json["split"]
        assert split["label_horizon_crossing"] is False
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 10. The hold-out windows are disjoint and are not repeated evidence.
# ---------------------------------------------------------------------------


def test_holdout_windows_are_disjoint_not_repeated(client, auth_headers, tmp_path, monkeypatch):
    _personal_artifacts(tmp_path, monkeypatch)
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)
    snapshot = _build_snapshot(client, auth_headers, version_id)
    started = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": snapshot["snapshot_id"]},
    )
    assert started.status_code == 202, started.text
    _run_worker_job(uuid.UUID(started.json()["job_id"]))

    db = TestSession()
    try:
        version = db.query(ModelVersion).one()
        evaluation = db.query(ModelEvaluation).one()
    finally:
        db.close()

    split = version.model_specification_json["split"]
    train, val, test = split["train"], split["val"], split["test"]
    assert split["kind"] == "chronological_holdout"
    assert split["selection_source"] == "fixed_hyperparameters_per_model_type"
    assert split["test_windows_overlap"] is False
    assert train["size"] + val["size"] + test["size"] == TOTAL_DECISIONS
    assert split["boundaries"]["train_end_index"] == train["size"] - 1
    assert split["boundaries"]["val_end_index"] == train["size"] + val["size"] - 1
    # Disjoint: no event appears in two segments.
    assert train["end_event_id"] != val["start_event_id"]
    assert val["end_event_id"] != test["start_event_id"]
    assert train["end_decided_at"] <= val["start_decided_at"]
    assert val["end_decided_at"] <= test["start_decided_at"]

    stored = evaluation.metrics_json
    assert stored["split"] == split
    assert stored["n_rows"] == TOTAL_DECISIONS
    # The artifact must admit the synthetic provenance of its features.
    assert "synthetic_market_features" in version.model_specification_json["warnings"]


# ---------------------------------------------------------------------------
# 11. Failures and cancellations never consume the reserved quota unit.
# ---------------------------------------------------------------------------


def test_failed_and_cancelled_jobs_release_quota(client, auth_headers, tmp_path, monkeypatch):
    _personal_artifacts(tmp_path, monkeypatch)
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)
    snapshot = _build_snapshot(client, auth_headers, version_id)

    # (a) Queued, then cancelled before any worker claims it.
    queued = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": snapshot["snapshot_id"]},
    )
    assert queued.status_code == 202, queued.text
    queued_id = queued.json()["job_id"]
    assert _quota_state(owner_id) == {"used": 0, "statuses": ["reserved"]}

    cancelled = client.post(
        f"/api/v1/training/jobs/{queued_id}/cancel", headers=auth_headers
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert _quota_state(owner_id) == {"used": 0, "statuses": ["released"]}

    # (b) Consent revoked between queueing and execution → refuse, release.
    second = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": snapshot["snapshot_id"]},
    )
    assert second.status_code == 202, second.text
    second_id = uuid.UUID(second.json()["job_id"])
    revoked = client.delete(
        "/api/v1/ml/personalization/consent", headers=auth_headers
    )
    assert revoked.status_code == 200, revoked.text

    _run_worker_job(second_id)
    db = TestSession()
    try:
        job = db.query(TrainingJob).filter(TrainingJob.id == second_id).one()
        assert job.status is TrainingJobStatus.failed
        error = json.loads(job.error_details)
        assert error["code"] == "consent_revoked_before_training"
        assert db.query(ModelVersion).count() == 0
        assert db.query(ModelEvaluation).count() == 0
    finally:
        db.close()
    assert _quota_state(owner_id)["used"] == 0
    assert _quota_state(owner_id)["statuses"] == ["released", "released"]

    # (c) Cancelled while running (cancel endpoint marks cancelled_at) → the
    # trainer must not train and the worker must release the reservation.
    _grant_consent(client, auth_headers, version_id)
    third = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": snapshot["snapshot_id"]},
    )
    assert third.status_code == 202, third.text
    third_id = uuid.UUID(third.json()["job_id"])

    from app.workers.training_worker import claim_pending_job

    db = TestSession()
    try:
        claimed = claim_pending_job(db)
        assert claimed is not None and claimed.id == third_id
    finally:
        db.close()

    running_cancel = client.post(
        f"/api/v1/training/jobs/{third_id}/cancel", headers=auth_headers
    )
    assert running_cancel.status_code == 200, running_cancel.text
    assert running_cancel.json()["status"] == "running"

    _run_worker_job(third_id)
    db = TestSession()
    try:
        job = db.query(TrainingJob).filter(TrainingJob.id == third_id).one()
        assert job.status is TrainingJobStatus.cancelled
        assert job.completed_at is not None
    finally:
        db.close()
    assert _quota_state(owner_id) == {
        "used": 0,
        "statuses": ["released", "released", "released"],
    }


# ---------------------------------------------------------------------------
# 12. Switching the active version resolves the version actually served.
# ---------------------------------------------------------------------------


def test_switching_active_version_resolves_correct_version(
    client, auth_headers, tmp_path, monkeypatch
):
    artifacts = _personal_artifacts(tmp_path, monkeypatch)
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    event_ids = _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)

    first_snapshot = _build_snapshot(client, auth_headers, version_id)
    first_job = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": first_snapshot["snapshot_id"]},
    )
    assert first_job.status_code == 202, first_job.text
    _run_worker_job(uuid.UUID(first_job.json()["job_id"]))

    excluded = client.post(
        f"/api/v1/decision-review/{event_ids[0]}/labels",
        headers=auth_headers,
        json={"state": "excluded", "exclusion_reason": "outside_strategy"},
    )
    assert excluded.status_code == 201, excluded.text
    second_snapshot = _build_snapshot(client, auth_headers, version_id)
    second_job = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": second_snapshot["snapshot_id"]},
    )
    assert second_job.status_code == 202, second_job.text
    _run_worker_job(uuid.UUID(second_job.json()["job_id"]))

    models = client.get("/api/v1/ml/personal/models", headers=auth_headers).json()[
        "models"
    ]
    assert len(models) == 2
    by_number = {model["version_number"]: model for model in models}
    v1, v2 = by_number[1], by_number[2]
    assert v1["artifact_id"] != v2["artifact_id"]
    assert v1["snapshot_id"] == first_snapshot["snapshot_id"]
    assert v2["snapshot_id"] == second_snapshot["snapshot_id"]
    assert v2["n_rows"] == TOTAL_DECISIONS - 1
    assert v1["snapshot_id"] != v2["snapshot_id"]

    as_of = BARS[120].isoformat()
    record_id = v1["record_id"]
    payload = {"instrument_id": INSTRUMENT, "as_of_date": as_of}

    assert client.post(
        f"/api/v1/ml/personal/models/{v1['model_version_id']}/activate",
        headers=auth_headers,
    ).status_code == 200
    served_v1 = client.post(
        f"/api/v1/ml/personal/records/{record_id}/predict",
        headers=auth_headers,
        json=payload,
    )
    assert served_v1.status_code == 200, served_v1.text
    assert served_v1.json()["resolved_version"]["model_version_id"] == v1["model_version_id"]
    assert served_v1.json()["artifact_sha256"] == v1["artifact_sha256"]
    assert served_v1.json()["snapshot"]["snapshot_id"] == first_snapshot["snapshot_id"]

    assert client.post(
        f"/api/v1/ml/personal/models/{v2['model_version_id']}/activate",
        headers=auth_headers,
    ).status_code == 200
    served_v2 = client.post(
        f"/api/v1/ml/personal/records/{record_id}/predict",
        headers=auth_headers,
        json=payload,
    )
    assert served_v2.status_code == 200, served_v2.text
    assert served_v2.json()["resolved_version"]["model_version_id"] == v2["model_version_id"]
    assert served_v2.json()["artifact_sha256"] == v2["artifact_sha256"]
    assert served_v2.json()["snapshot"]["snapshot_id"] == second_snapshot["snapshot_id"]

    # An explicit version request is served as-is, never silently swapped.
    explicit = client.post(
        f"/api/v1/ml/personal/models/{v1['model_version_id']}/predict",
        headers=auth_headers,
        json=payload,
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["resolved_version"]["model_version_id"] == v1["model_version_id"]
    assert explicit.json()["resolved_version"]["resolved_from_active_alias"] is False

    # Two settled trainings plus three settled predictions.
    quota = _quota_state(owner_id)
    assert quota["used"] == 5
    assert set(quota["statuses"]) == {"settled"}
    assert len(quota["statuses"]) == 5
    assert _artifact_sha256(artifacts, v1["artifact_id"]) == v1["artifact_sha256"]


# ---------------------------------------------------------------------------
# 13. Sampling contract: a bar is usable only after it closed.
# ---------------------------------------------------------------------------


def test_same_day_bar_not_visible_before_its_close():
    """A daily bar never enters the features before its session has closed.

    The daily replay records a decision on day D after that day's session, and
    the legacy date-only fallback records it at the start of D. Both must see
    only bars up to D-1; the next-day instant is the first that may see D.
    """
    from app.services.personal_dataset import features_as_of

    day = BARS[100]
    previous = BARS[99]
    db = TestSession()
    try:
        legacy = features_as_of(db, INSTRUMENT, day.isoformat())
        pre_close = features_as_of(
            db, INSTRUMENT, day.isoformat(), as_of_instant=datetime.combine(day, time(10, 0))
        )
        post_close = features_as_of(
            db,
            INSTRUMENT,
            day.isoformat(),
            as_of_instant=datetime.combine(day + timedelta(days=1), time.min),
        )
    finally:
        db.close()

    assert legacy[3] is None and pre_close[3] is None and post_close[3] is None
    # Date-only and pre-close decisions must not read the same day's bar.
    assert legacy[2] == previous.isoformat()
    assert pre_close[2] == previous.isoformat()
    # Non-vacuous boundary: once D has closed, D's own bar is the latest input.
    assert post_close[2] == day.isoformat()
    assert post_close[0] != pre_close[0]


def test_same_day_bar_mutation_cannot_change_decision_features():
    """Mutating the not-yet-closed bar cannot change a pre-close decision."""
    from app.services.personal_dataset import features_as_of

    day = BARS[100]
    cutoff = datetime.combine(day, time(10, 0))
    after_close = datetime.combine(day + timedelta(days=1), time.min)

    db = TestSession()
    try:
        before = features_as_of(db, INSTRUMENT, day.isoformat(), as_of_instant=cutoff)
        before_after_close = features_as_of(
            db, INSTRUMENT, day.isoformat(), as_of_instant=after_close
        )
        bar = (
            db.query(MarketDataBar)
            .filter(
                MarketDataBar.instrument_id == INSTRUMENT,
                MarketDataBar.provider_id == "synthetic",
                MarketDataBar.bar_date == day,
                MarketDataBar.bar_timestamp == datetime.combine(day, datetime.min.time()),
            )
            .one()
        )
        bar.open = bar.high = bar.low = bar.close = 1_000_000.0
        bar.volume = 1
        # A later bar from the same provider must not leak either.
        future_day = BARS[-1] + timedelta(days=3)
        db.add(
            MarketDataBar(
                instrument_id=INSTRUMENT,
                provider_id="synthetic",
                bar_date=future_day,
                bar_timestamp=datetime.combine(future_day, datetime.min.time()),
                open=2_000_000.0,
                high=2_000_000.0,
                low=2_000_000.0,
                close=2_000_000.0,
                volume=1,
            )
        )
        db.commit()

        after = features_as_of(db, INSTRUMENT, day.isoformat(), as_of_instant=cutoff)
        after_close_changed = features_as_of(
            db, INSTRUMENT, day.isoformat(), as_of_instant=after_close
        )
    finally:
        db.close()

    assert after[0] == before[0]
    assert after[2] == before[2] == BARS[99].isoformat()
    # The same mutation is visible exactly where the contract allows it, which
    # proves the invariance above is not vacuous.
    assert after_close_changed[0] != before_after_close[0]
    assert after_close_changed[2] == day.isoformat()


def test_same_day_bar_mutation_does_not_change_a_built_snapshot(client, auth_headers):
    """End-to-end: the row set of a built snapshot ignores an unclosed bar."""
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)

    preview = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_id},
    ).json()
    assert preview["sampling_contract"]["version"] == "as-of-bar-close-v1"
    assert preview["sampling_contract"]["as_of_rule"] == "bar_close_time"
    assert preview["decision_instant_sources"] == {
        "legacy_date_start": TOTAL_DECISIONS
    }
    snapshot = _build_snapshot(client, auth_headers, version_id)
    assert snapshot["included_count"] == TOTAL_DECISIONS
    assert snapshot["sampling_contract"]["version"] == "as-of-bar-close-v1"

    db = TestSession()
    try:
        stored = (
            db.query(PersonalizationDatasetSnapshot)
            .filter(
                PersonalizationDatasetSnapshot.id == uuid.UUID(snapshot["snapshot_id"])
            )
            .one()
        )
        # Every fixture decision is date-only, so D's own bar is never used.
        assert all(row["feature_bar_date"] < row["snapshot_date"] for row in stored.rows_json)
        assert all(row["decision_instant_source"] == "legacy_date_start" for row in stored.rows_json)

        last_bar_day = BARS[DECISION_START_INDEX + (DECISION_DAYS - 1) * DECISION_STEP]
        bar = (
            db.query(MarketDataBar)
            .filter(
                MarketDataBar.instrument_id == INSTRUMENT,
                MarketDataBar.provider_id == "synthetic",
                MarketDataBar.bar_date == last_bar_day,
                MarketDataBar.bar_timestamp
                == datetime.combine(last_bar_day, datetime.min.time()),
            )
            .one()
        )
        bar.open = bar.high = bar.low = bar.close = 9_999_999.0
        db.commit()
    finally:
        db.close()

    rebuilt = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_id},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["checksum"] == snapshot["checksum"]
    assert rebuilt.json()["included_count"] == TOTAL_DECISIONS


def test_server_records_market_clock_decision_instant():
    """The API writes the market-clock instant, not a bare date."""
    from types import SimpleNamespace
    from app.api.human_decisions import _decision_instant

    daily = SimpleNamespace(
        timeframe=None, current_bar_timestamp=None, current_snapshot_date="2024-03-05"
    )
    assert _decision_instant(daily, {}) == datetime(2024, 3, 6)

    intraday = SimpleNamespace(
        timeframe="5m",
        current_bar_timestamp=datetime(2024, 3, 5, 14, 30),
        current_snapshot_date="2024-03-05",
    )
    assert _decision_instant(intraday, {}) == datetime(2024, 3, 5, 14, 35)

    not_started = SimpleNamespace(
        timeframe=None, current_bar_timestamp=None, current_snapshot_date=None
    )
    assert _decision_instant(not_started, {"start_date": "2024-03-05"}) == datetime(
        2024, 3, 5
    )


# ---------------------------------------------------------------------------
# 14. Sample identity: one raw decision, one row, one valid label revision.
# ---------------------------------------------------------------------------


def test_duplicate_source_event_is_counted_once(client, auth_headers):
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    event_ids = _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)

    baseline = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_id},
    ).json()
    assert baseline["included_count"] == TOTAL_DECISIONS

    # The same raw decision submitted a second time: identical session, step,
    # instrument, snapshot, action, quantity and rationale, with an earlier
    # recording time so the reviewed original stays the representative row.
    db = TestSession()
    try:
        original = (
            db.query(HumanDecisionEvent)
            .filter(HumanDecisionEvent.id == event_ids[0])
            .one()
        )
        author = db.query(User).filter(User.id == owner_id).one()
        duplicate = HumanDecisionEvent(
            owner_id=owner_id,
            simulation_session_id=original.simulation_session_id,
            idempotency_key=uuid.uuid4().hex,
            action=original.action,
            quantity=original.quantity,
            rationale=original.rationale,
            server_strategy_version_id=original.server_strategy_version_id,
            server_instrument_id=original.server_instrument_id,
            server_step_number=original.server_step_number,
            server_snapshot_date=original.server_snapshot_date,
            server_data_cutoff_at=original.server_data_cutoff_at,
            execution_status=HumanDecisionExecutionStatus.record_only,
            actor_type=original.actor_type,
            created_at=original.created_at - timedelta(minutes=30),
        )
        db.add(duplicate)
        db.flush()
        record_label(
            db,
            event=duplicate,
            author=author,
            state="confirmed",
            action_label=None,
            note=None,
            exclusion_reason=None,
        )
        duplicate_id = duplicate.id
        db.commit()
    finally:
        db.close()

    preview = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=auth_headers,
        json={"strategy_version_id": version_id},
    ).json()
    assert preview["duplicate_source_events_ignored"] == 1
    assert preview["included_count"] == TOTAL_DECISIONS
    assert preview["total_current_decisions"] == TOTAL_DECISIONS
    assert preview["checksum"] == baseline["checksum"]

    snapshot = _build_snapshot(client, auth_headers, version_id)
    assert snapshot["included_count"] == TOTAL_DECISIONS

    db = TestSession()
    try:
        stored = (
            db.query(PersonalizationDatasetSnapshot)
            .filter(
                PersonalizationDatasetSnapshot.id == uuid.UUID(snapshot["snapshot_id"])
            )
            .one()
        )
        kept = [
            row
            for row in stored.rows_json
            if row["decision_event_id"] in {str(event_ids[0]), str(duplicate_id)}
        ]
        assert len(kept) == 1
        assert kept[0]["decision_event_id"] == str(event_ids[0])
    finally:
        db.close()


def test_only_latest_label_revision_reaches_the_dataset(client, auth_headers):
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    event_ids = _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)

    revised = client.post(
        f"/api/v1/decision-review/{event_ids[1]}/labels",
        headers=auth_headers,
        json={"state": "confirmed", "action_label": "hold", "note": "修正為觀望"},
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["revision_number"] == 2

    snapshot = _build_snapshot(client, auth_headers, version_id)
    # A label revision replaces the label identity; it does not add a sample.
    assert snapshot["included_count"] == TOTAL_DECISIONS

    db = TestSession()
    try:
        stored = (
            db.query(PersonalizationDatasetSnapshot)
            .filter(
                PersonalizationDatasetSnapshot.id == uuid.UUID(snapshot["snapshot_id"])
            )
            .one()
        )
        rows = [
            row
            for row in stored.rows_json
            if row["decision_event_id"] == str(event_ids[1])
        ]
        assert len(rows) == 1
        assert rows[0]["action_label"] == "hold"
        assert rows[0]["label_revision"] == 2
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 15. Same-instant groups never straddle a train/val/test boundary.
# ---------------------------------------------------------------------------


def test_grouped_split_keeps_same_instant_groups_whole():
    from app.ml.personal_trainer import grouped_temporal_split

    group_sizes = [3, 3, 3, 3, 3, 3, 3]
    train, val, test, groups = grouped_temporal_split(group_sizes, 0.7, 0.15)

    assert train == list(range(15))
    assert val == [15, 16, 17]
    assert test == [18, 19, 20]
    assert groups == {"train": 5, "val": 1, "test": 1}
    # A row-level split would cut at index 14, inside the fifth group; all of
    # that group stays in train here and the segments start on group edges.
    assert int(sum(group_sizes) * 0.7) == 14
    for segment in (train, val, test):
        assert segment == list(range(segment[0], segment[-1] + 1))
        assert segment[0] % 3 == 0
        assert len(segment) % 3 == 0


def test_trained_split_is_group_aware(client, auth_headers, tmp_path, monkeypatch):
    _personal_artifacts(tmp_path, monkeypatch)
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)
    snapshot = _build_snapshot(client, auth_headers, version_id)

    started = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": snapshot["snapshot_id"]},
    )
    assert started.status_code == 202, started.text
    _run_worker_job(uuid.UUID(started.json()["job_id"]))

    db = TestSession()
    try:
        split = db.query(ModelVersion).one().model_specification_json["split"]
    finally:
        db.close()

    assert split["group_aware"] is True
    assert split["group_key"] == "decision_instant"
    # The fixture records five decisions per decision day, so there are 41
    # groups and every segment boundary falls on a whole group.
    assert split["n_groups"] == DECISION_DAYS
    assert split["groups_per_segment"] == {"train": 29, "val": 6, "test": 6}
    assert split["train"]["size"] == 145
    assert split["val"]["size"] == 30
    assert split["test"]["size"] == 30
    assert (split["boundaries"]["train_end_index"] + 1) % DECISIONS_PER_DAY == 0
    assert (split["boundaries"]["val_end_index"] + 1) % DECISIONS_PER_DAY == 0
    # Row-level splitting at 205 * 0.7 = 143.5 would have split a same-instant
    # group between training and validation.
    assert 205 * 0.7 % DECISIONS_PER_DAY != 0


# ---------------------------------------------------------------------------
# 16. A built snapshot is frozen against later label edits.
# ---------------------------------------------------------------------------


def test_snapshot_rows_frozen_against_later_label_edits(client, auth_headers):
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    event_ids = _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)

    snapshot = _build_snapshot(client, auth_headers, version_id)
    detail_before = client.get(
        f"/api/v1/ml/personal/snapshots/{snapshot['snapshot_id']}", headers=auth_headers
    ).json()

    db = TestSession()
    try:
        stored = (
            db.query(PersonalizationDatasetSnapshot)
            .filter(
                PersonalizationDatasetSnapshot.id == uuid.UUID(snapshot["snapshot_id"])
            )
            .one()
        )
        rows_before = json.loads(json.dumps(stored.rows_json, sort_keys=True))
    finally:
        db.close()

    excluded = client.post(
        f"/api/v1/decision-review/{event_ids[0]}/labels",
        headers=auth_headers,
        json={"state": "excluded", "exclusion_reason": "outside_strategy"},
    )
    assert excluded.status_code == 201, excluded.text

    detail_after = client.get(
        f"/api/v1/ml/personal/snapshots/{snapshot['snapshot_id']}", headers=auth_headers
    ).json()
    assert detail_after == detail_before

    db = TestSession()
    try:
        stored = (
            db.query(PersonalizationDatasetSnapshot)
            .filter(
                PersonalizationDatasetSnapshot.id == uuid.UUID(snapshot["snapshot_id"])
            )
            .one()
        )
        assert json.loads(json.dumps(stored.rows_json, sort_keys=True)) == rows_before
        from app.models.decision_label import DecisionLabel

        latest = (
            db.query(DecisionLabel)
            .filter(DecisionLabel.decision_event_id == event_ids[0])
            .order_by(DecisionLabel.revision_number.desc())
            .first()
        )
        assert latest.state is DecisionLabelState.excluded
    finally:
        db.close()

    # The edit is real: a new snapshot reflects it, and the old one does not.
    rebuilt = _build_snapshot(client, auth_headers, version_id)
    assert rebuilt["snapshot_id"] != snapshot["snapshot_id"]
    assert rebuilt["included_count"] == TOTAL_DECISIONS - 1
    assert rebuilt["checksum"] != snapshot["checksum"]


# ---------------------------------------------------------------------------
# 17. Preprocessing is fit on the train segment only.
# ---------------------------------------------------------------------------


def test_scaler_is_fit_on_training_segment_only(tmp_path, monkeypatch):
    from app.ml.personal_trainer import load_personal_model, train_personal_model

    _personal_artifacts(tmp_path, monkeypatch)
    rows = [
        {
            "decision_event_id": f"scaler-{i:03d}",
            "decided_at": (datetime(2024, 1, 2) + timedelta(days=i)).isoformat(),
            "features": {"f1": float(i), "f2": float(i % 2)},
            "action_label": "buy" if i < 20 else "sell",
        }
        for i in range(40)
    ]
    result = train_personal_model(
        rows,
        ["f1", "f2"],
        owner_id="scaler-owner",
        snapshot_id="scaler-snapshot",
        snapshot_checksum="scaler-checksum",
        strategy_version_id="scaler-strategy",
    )
    assert result["split"]["boundaries"]["train_end_index"] == 27
    assert result["split"]["train"]["size"] == 28

    model, scaler, metadata, feature_names = load_personal_model(
        result["model_id"], owner_id="scaler-owner"
    )
    assert feature_names == ["f1", "f2"]
    # Mean of f1 over the train segment 0..27, not over all 40 rows.
    assert scaler.mean_[0] == pytest.approx(13.5)
    assert scaler.mean_[0] != pytest.approx(19.5)
    assert metadata["split"]["boundaries"]["train_end_index"] == 27
    assert metadata["split"]["group_aware"] is True
