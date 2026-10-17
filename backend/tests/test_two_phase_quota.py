"""Tests for two-phase quota flow: reserve → settle / release."""
import pytest
from uuid import uuid4

from fastapi import HTTPException
from app.models.pilot import PilotEntitlement
from app.models.quota import QuotaCounter, QuotaReservation
from app.auth.entitlements import (
    FREE_PLAN_ENTITLEMENT,
    reserve_quota,
    settle_reservation,
    release_reservation,
    check_quota_available,
    get_quota_used,
    consume_quota,
    _get_reserved,
)
from tests.conftest import TestSession


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


class TestReserveQuota:
    def test_reserve_creates_reservation(self, db):
        user_id = uuid4()
        reservation = reserve_quota(db, user_id, "research_run")
        assert reservation.status == "reserved"
        assert reservation.amount == 1
        assert reservation.quota_type == "research_run"
        assert reservation.user_id == user_id

    def test_reserve_reduces_available_quota(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=3)
        db.add(ent)
        db.flush()

        assert check_quota_available(db, user_id, "research_run") is True
        reserve_quota(db, user_id, "research_run")
        reserve_quota(db, user_id, "research_run")
        reserve_quota(db, user_id, "research_run")
        assert check_quota_available(db, user_id, "research_run") is False

    def test_reserve_exceeds_limit_raises_429(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=2)
        db.add(ent)
        db.flush()

        reserve_quota(db, user_id, "research_run")
        reserve_quota(db, user_id, "research_run")

        with pytest.raises(HTTPException) as exc_info:
            reserve_quota(db, user_id, "research_run")
        assert exc_info.value.status_code == 429
        detail = exc_info.value.detail
        assert detail["error"] == "quota_exceeded"
        assert detail["used"] == 0
        assert detail["reserved"] == 2

    def test_reserve_free_plan_within_limit(self, db):
        user_id = uuid4()
        free_limit = FREE_PLAN_ENTITLEMENT["research_run_limit"]
        for _ in range(free_limit):
            reserve_quota(db, user_id, "research_run")
        assert check_quota_available(db, user_id, "research_run") is False

    def test_reserve_free_plan_exceeds_raises_429(self, db):
        user_id = uuid4()
        free_limit = FREE_PLAN_ENTITLEMENT["research_run_limit"]
        for _ in range(free_limit):
            reserve_quota(db, user_id, "research_run")
        with pytest.raises(HTTPException) as exc_info:
            reserve_quota(db, user_id, "research_run")
        assert exc_info.value.status_code == 429

    def test_reserve_unknown_type_raises(self, db):
        user_id = uuid4()
        with pytest.raises(ValueError, match="Unknown quota_type"):
            reserve_quota(db, user_id, "bogus_type")

    def test_reserve_non_positive_amount_raises(self, db):
        user_id = uuid4()
        with pytest.raises(ValueError, match="positive integer"):
            reserve_quota(db, user_id, "research_run", amount=0)
        with pytest.raises(ValueError, match="positive integer"):
            reserve_quota(db, user_id, "research_run", amount=-1)


class TestSettleReservation:
    def test_settle_increments_used(self, db):
        user_id = uuid4()
        reservation = reserve_quota(db, user_id, "research_run")
        new_used = settle_reservation(db, reservation.id)
        assert new_used == 1
        assert get_quota_used(db, user_id, "research_run") == 1

    def test_settle_marks_reservation_settled(self, db):
        user_id = uuid4()
        reservation = reserve_quota(db, user_id, "research_run")
        settle_reservation(db, reservation.id)

        db.refresh(reservation)
        assert reservation.status == "settled"
        assert reservation.settled_at is not None

    def test_settle_multiple_reservations(self, db):
        user_id = uuid4()
        r1 = reserve_quota(db, user_id, "research_run")
        r2 = reserve_quota(db, user_id, "research_run")
        r3 = reserve_quota(db, user_id, "research_run")

        settle_reservation(db, r1.id)
        settle_reservation(db, r2.id)
        settle_reservation(db, r3.id)

        assert get_quota_used(db, user_id, "research_run") == 3

    def test_settle_already_settled_raises(self, db):
        user_id = uuid4()
        reservation = reserve_quota(db, user_id, "research_run")
        settle_reservation(db, reservation.id)

        with pytest.raises(ValueError, match="not in 'reserved' status"):
            settle_reservation(db, reservation.id)

    def test_settle_released_raises(self, db):
        user_id = uuid4()
        reservation = reserve_quota(db, user_id, "research_run")
        release_reservation(db, reservation.id)

        with pytest.raises(ValueError, match="not in 'reserved' status"):
            settle_reservation(db, reservation.id)

    def test_settle_nonexistent_raises(self, db):
        fake_id = uuid4()
        with pytest.raises(ValueError, match="Reservation not found"):
            settle_reservation(db, fake_id)

    def test_settle_creates_counter_if_missing(self, db):
        user_id = uuid4()
        assert get_quota_used(db, user_id, "research_run") == 0

        reservation = reserve_quota(db, user_id, "research_run")
        settle_reservation(db, reservation.id)

        counter = db.query(QuotaCounter).filter(
            QuotaCounter.user_id == user_id,
            QuotaCounter.quota_type == "research_run",
        ).first()
        assert counter is not None
        assert counter.used == 1


class TestReleaseReservation:
    def test_release_marks_released(self, db):
        user_id = uuid4()
        reservation = reserve_quota(db, user_id, "research_run")
        release_reservation(db, reservation.id)

        db.refresh(reservation)
        assert reservation.status == "released"
        assert reservation.settled_at is not None

    def test_release_restores_available_quota(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=2)
        db.add(ent)
        db.flush()

        r1 = reserve_quota(db, user_id, "research_run")
        r2 = reserve_quota(db, user_id, "research_run")
        assert check_quota_available(db, user_id, "research_run") is False

        release_reservation(db, r1.id)
        assert check_quota_available(db, user_id, "research_run") is True

    def test_release_does_not_increment_used(self, db):
        user_id = uuid4()
        reservation = reserve_quota(db, user_id, "research_run")
        release_reservation(db, reservation.id)
        assert get_quota_used(db, user_id, "research_run") == 0

    def test_release_already_released_raises(self, db):
        user_id = uuid4()
        reservation = reserve_quota(db, user_id, "research_run")
        release_reservation(db, reservation.id)

        with pytest.raises(ValueError, match="not in 'reserved' status"):
            release_reservation(db, reservation.id)

    def test_release_settled_raises(self, db):
        user_id = uuid4()
        reservation = reserve_quota(db, user_id, "research_run")
        settle_reservation(db, reservation.id)

        with pytest.raises(ValueError, match="not in 'reserved' status"):
            release_reservation(db, reservation.id)

    def test_release_nonexistent_raises(self, db):
        fake_id = uuid4()
        with pytest.raises(ValueError, match="Reservation not found"):
            release_reservation(db, fake_id)


class TestReservedTracking:
    def test_get_reserved_sum(self, db):
        user_id = uuid4()
        reserve_quota(db, user_id, "research_run")
        reserve_quota(db, user_id, "research_run")
        assert _get_reserved(db, user_id, "research_run") == 2

    def test_reserved_excludes_settled(self, db):
        user_id = uuid4()
        r1 = reserve_quota(db, user_id, "research_run")
        reserve_quota(db, user_id, "research_run")
        settle_reservation(db, r1.id)
        assert _get_reserved(db, user_id, "research_run") == 1

    def test_reserved_excludes_released(self, db):
        user_id = uuid4()
        r1 = reserve_quota(db, user_id, "research_run")
        reserve_quota(db, user_id, "research_run")
        release_reservation(db, r1.id)
        assert _get_reserved(db, user_id, "research_run") == 1

    def test_check_quota_accounts_for_reservations(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=3)
        db.add(ent)
        db.flush()

        consume_quota(db, user_id, "research_run")
        assert check_quota_available(db, user_id, "research_run") is True

        reserve_quota(db, user_id, "research_run")
        assert check_quota_available(db, user_id, "research_run") is True

        reserve_quota(db, user_id, "research_run")
        assert check_quota_available(db, user_id, "research_run") is False


class TestTwoPhaseFlowIntegration:
    def test_successful_operation_settles(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=5)
        db.add(ent)
        db.flush()

        reservation = reserve_quota(db, user_id, "research_run")
        assert _get_reserved(db, user_id, "research_run") == 1
        assert get_quota_used(db, user_id, "research_run") == 0

        settle_reservation(db, reservation.id)
        assert _get_reserved(db, user_id, "research_run") == 0
        assert get_quota_used(db, user_id, "research_run") == 1

    def test_failed_operation_releases(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=5)
        db.add(ent)
        db.flush()

        reservation = reserve_quota(db, user_id, "research_run")
        assert _get_reserved(db, user_id, "research_run") == 1

        release_reservation(db, reservation.id)
        assert _get_reserved(db, user_id, "research_run") == 0
        assert get_quota_used(db, user_id, "research_run") == 0
        assert check_quota_available(db, user_id, "research_run") is True

    def test_mixed_settle_and_release(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=5)
        db.add(ent)
        db.flush()

        r1 = reserve_quota(db, user_id, "research_run")
        r2 = reserve_quota(db, user_id, "research_run")
        r3 = reserve_quota(db, user_id, "research_run")

        settle_reservation(db, r1.id)
        release_reservation(db, r2.id)
        settle_reservation(db, r3.id)

        assert get_quota_used(db, user_id, "research_run") == 2
        assert _get_reserved(db, user_id, "research_run") == 0
        assert check_quota_available(db, user_id, "research_run") is True

    def test_reservation_isolation_between_users(self, db):
        user_a = uuid4()
        user_b = uuid4()

        reserve_quota(db, user_a, "research_run")
        reserve_quota(db, user_a, "research_run")

        assert _get_reserved(db, user_a, "research_run") == 2
        assert _get_reserved(db, user_b, "research_run") == 0
        assert check_quota_available(db, user_b, "research_run") is True
