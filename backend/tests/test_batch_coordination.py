"""Contract tests for the write-batch coordination registry.

These cover the "another agent already did this batch" defence: one designated
executor per customer batch, read-back before completion, no overwrite of a
finished result, and a deployment gate that refuses while any batch is still
claimed.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.auth.jwt import get_password_hash
from app.models.operation_batch import OperationBatch
from app.models.user import User
from app.services.batch_coordination import (
    BatchConflict,
    abandon_batch,
    batch_status,
    claim_batch,
    complete_batch,
    deployment_precheck,
)
from tests.conftest import TestSession

KEY = "paper-batch-2026-10-24"


def _owner(email=None):
    db = TestSession()
    try:
        user = User(
            email=email or f"batch-{uuid.uuid4().hex[:10]}@alethra.io",
            password_hash=get_password_hash("testpass123"),
            display_name="Batch Owner",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


def _conflict(callable_):
    with pytest.raises(BatchConflict) as exc:
        callable_()
    return exc.value


def _claim(owner_id, executor="agent-a", key=KEY, **kwargs):
    db = TestSession()
    try:
        return claim_batch(db, owner_id=owner_id, batch_key=key, executor=executor, **kwargs)
    finally:
        db.close()


def _complete(owner_id, result, executor="agent-a", key=KEY):
    db = TestSession()
    try:
        return complete_batch(
            db, owner_id=owner_id, batch_key=key, executor=executor, result=result
        )
    finally:
        db.close()


def _abandon(owner_id, reason="operator cancelled", executor="agent-a", key=KEY):
    db = TestSession()
    try:
        return abandon_batch(
            db, owner_id=owner_id, batch_key=key, executor=executor, reason=reason
        )
    finally:
        db.close()


RESULT = {
    "objects": [{"type": "paper_order", "id": "order-1"}],
    "caps_consumed": {"orders": 1},
}


def test_first_claim_is_claimed_and_second_executor_is_refused():
    owner = _owner()
    first = _claim(owner, executor="agent-a")
    assert first["outcome"] == "claimed"
    assert first["status"] == "claimed"

    conflict = _conflict(lambda: _claim(owner, executor="agent-b"))
    assert conflict.code == "batch_in_progress"
    assert conflict.details["batch"]["executor"] == "agent-a"
    assert conflict.details["batch"]["id"] == first["id"]


def test_same_executor_reclaim_is_idempotent():
    owner = _owner()
    first = _claim(owner, executor="agent-a")
    again = _claim(owner, executor="agent-a")
    assert again["outcome"] == "already_claimed"
    assert again["id"] == first["id"]


def test_completion_requires_read_back_objects_and_caps():
    owner = _owner()
    _claim(owner, executor="agent-a")

    missing_objects = _conflict(
        lambda: _complete(owner, {"caps_consumed": {"orders": 1}})
    )
    assert missing_objects.code == "result_objects_required"

    missing_caps = _conflict(
        lambda: _complete(owner, {"objects": [{"type": "paper_order", "id": "o"}]})
    )
    assert missing_caps.code == "result_caps_required"

    # A refused completion must not have closed the batch.
    status = batch_status(TestSession(), owner_id=owner, batch_key=KEY)
    assert status["status"] == "claimed"


def test_second_completion_returns_original_result_without_overwriting():
    owner = _owner()
    _claim(owner, executor="agent-a")

    done = _complete(owner, RESULT)
    assert done["outcome"] == "completed"
    assert done["result"]["objects"] == RESULT["objects"]
    assert done["result"]["caps_consumed"] == RESULT["caps_consumed"]

    replay = _complete(
        owner,
        {
            "objects": [{"type": "paper_order", "id": "order-2"}],
            "caps_consumed": {"orders": 9},
        },
    )
    assert replay["outcome"] == "already_completed"
    assert replay["result"]["objects"] == RESULT["objects"]
    assert replay["result"]["caps_consumed"] == {"orders": 1}


def test_other_executor_cannot_complete_a_claimed_batch():
    owner = _owner()
    _claim(owner, executor="agent-a")
    conflict = _conflict(
        lambda: _complete(owner, RESULT, executor="agent-b")
    )
    assert conflict.code == "batch_claimed_by_other_executor"


def test_completion_of_an_unclaimed_batch_is_refused():
    owner = _owner()
    conflict = _conflict(lambda: _complete(owner, RESULT))
    assert conflict.code == "batch_not_claimed"


def test_abandon_releases_the_claim_but_refuses_reuse():
    owner = _owner()
    _claim(owner, executor="agent-a")

    abandoned = _abandon(owner, reason="no writes were issued")
    assert abandoned["outcome"] == "abandoned"
    assert abandoned["status"] == "abandoned"
    assert abandoned["abandon_reason"] == "no writes were issued"

    conflict = _conflict(lambda: _claim(owner, executor="agent-b"))
    assert conflict.code == "batch_abandoned"

    # Abandoning again is a read, not an error.
    again = _abandon(owner)
    assert again["outcome"] == "already_abandoned"

    # A completed batch cannot be abandoned.
    _claim(owner, executor="agent-a", key="other-key")
    _complete(owner, RESULT, key="other-key")
    done = _abandon(owner, key="other-key")
    assert done["outcome"] == "already_completed"


def test_deployment_precheck_blocks_while_claimed_and_clears_after():
    owner = _owner()
    _claim(owner, executor="agent-a", key="predeploy-claimed")

    db = TestSession()
    try:
        blocked = deployment_precheck(db)
    finally:
        db.close()
    assert blocked["safe_to_deploy"] is False
    assert blocked["registry_present"] is True
    assert blocked["in_progress_count"] == 1
    assert blocked["in_progress"][0]["batch_key"] == "predeploy-claimed"

    _complete(owner, RESULT, key="predeploy-claimed")

    db = TestSession()
    try:
        clear = deployment_precheck(db)
    finally:
        db.close()
    assert clear["safe_to_deploy"] is True
    assert clear["in_progress_count"] == 0
    assert clear["counts"]["completed"] == 1


def test_abandoned_batch_does_not_block_deployment():
    owner = _owner()
    _claim(owner, executor="agent-a", key="predeploy-abandoned")
    _abandon(owner, key="predeploy-abandoned")

    db = TestSession()
    try:
        precheck = deployment_precheck(db)
    finally:
        db.close()
    assert precheck["safe_to_deploy"] is True
    assert precheck["counts"]["abandoned"] == 1


def test_batch_keys_are_scoped_to_one_owner():
    owner_a = _owner()
    owner_b = _owner()

    _claim(owner_a, executor="agent-a")
    other = _claim(owner_b, executor="agent-b")

    assert other["outcome"] == "claimed"
    conflict = _conflict(lambda: _claim(owner_a, executor="agent-c"))
    assert conflict.code == "batch_in_progress"


def test_claim_requires_an_executor_and_a_key():
    owner = _owner()
    assert _conflict(lambda: _claim(owner, executor="  ")).code == "executor_required"
    assert _conflict(lambda: _claim(owner, key="   ")).code == "batch_key_required"


def test_duplicate_insert_is_rejected_by_the_database():
    owner = _owner()
    db = TestSession()
    try:
        db.add(
            OperationBatch(
                owner_id=owner, batch_key="dup", executor="agent-a", status="claimed"
            )
        )
        db.commit()
        db.add(
            OperationBatch(
                owner_id=owner, batch_key="dup", executor="agent-b", status="claimed"
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_claim_recovers_when_a_concurrent_insert_wins(monkeypatch):
    """A lost insert race re-reads the winner instead of failing."""
    import app.services.batch_coordination as coord

    owner = _owner()
    real_find = coord._find
    calls = {"n": 0}

    def find_missing_first(db, owner_id, key):
        # Pretend the row did not exist on the look-up immediately before the
        # insert, then make it visible to the recovery read.
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_find(db, owner_id, key)

    winner_db = TestSession()
    try:
        coord.claim_batch(
            winner_db, owner_id=owner, batch_key=KEY, executor="agent-a"
        )
    finally:
        winner_db.close()

    monkeypatch.setattr(coord, "_find", find_missing_first)
    db = TestSession()
    try:
        conflict = _conflict(
            lambda: coord.claim_batch(
                db, owner_id=owner, batch_key=KEY, executor="agent-b"
            )
        )
        assert conflict.code == "batch_in_progress"
        assert conflict.details["batch"]["executor"] == "agent-a"
    finally:
        db.close()


def test_predeploy_on_a_pre_migration_database_is_reported_not_guessed():
    """A DB without the table cannot hold a claim; say so explicitly."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        report = deployment_precheck(db)
    finally:
        db.close()
    assert report["safe_to_deploy"] is True
    assert report["registry_present"] is False
    assert report["in_progress_count"] == 0
    assert "migration" in report["note_zh"]


def test_batch_status_is_none_until_claimed():
    owner = _owner()
    db = TestSession()
    try:
        assert batch_status(db, owner_id=owner, batch_key="absent") is None
    finally:
        db.close()
