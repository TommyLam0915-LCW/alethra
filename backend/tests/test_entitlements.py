import pytest
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from app.models.quota import QuotaCounter
from app.models.pilot import PilotEntitlement
from app.auth.entitlements import (
    FREE_PLAN_ENTITLEMENT,
    get_entitlement,
    check_entitlement_active,
    get_quota_used,
    consume_quota,
    check_quota_available,
    require_entitlement,
)
from tests.conftest import TestSession
from app.lib.timeutil import utcnow


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


class TestEntitlementChecks:
    def test_no_entitlement_falls_back_to_free_plan(self, db):
        """Users without an entitlement row get FREE_PLAN_ENTITLEMENT limits."""
        user_id = uuid4()
        assert get_entitlement(db, user_id) is None
        # Within free plan limit (5 research runs)
        assert check_quota_available(db, user_id, "research_run") is True

    def test_active_entitlement(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(
            user_id=user_id,
            pilot_access=True,
            research_run_limit=10,
            expiry_date=utcnow() + timedelta(days=30),
        )
        db.add(ent)
        db.flush()
        assert check_entitlement_active(ent) is True

    def test_expired_entitlement(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(
            user_id=user_id,
            pilot_access=True,
            expiry_date=utcnow() - timedelta(days=1),
        )
        db.add(ent)
        db.flush()
        assert check_entitlement_active(ent) is False

    def test_disabled_entitlement(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(
            user_id=user_id,
            pilot_access=False,
        )
        db.add(ent)
        db.flush()
        assert check_entitlement_active(ent) is False


class TestQuotaConsumption:
    def test_consume_quota_creates_counter(self, db):
        user_id = uuid4()
        used = consume_quota(db, user_id, "research_run")
        assert used == 1

    def test_consume_quota_increments(self, db):
        user_id = uuid4()
        consume_quota(db, user_id, "research_run")
        used = consume_quota(db, user_id, "research_run")
        assert used == 2

    def test_get_quota_used_zero_initially(self, db):
        user_id = uuid4()
        assert get_quota_used(db, user_id, "research_run") == 0

    def test_check_quota_available_within_limit(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=5)
        db.add(ent)
        db.flush()
        assert check_quota_available(db, user_id, "research_run")

    def test_check_quota_exceeded(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=2)
        db.add(ent)
        db.flush()
        consume_quota(db, user_id, "research_run")
        consume_quota(db, user_id, "research_run")
        assert not check_quota_available(db, user_id, "research_run")

    def test_zero_limit_means_no_allowance(self, db):
        """limit == 0 means 'no allowance' — should reject."""
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=0)
        db.add(ent)
        db.flush()
        assert check_quota_available(db, user_id, "research_run") is False

    def test_unknown_quota_type_raises_value_error(self, db):
        """Unknown quota_type must raise ValueError, not silently pass."""
        user_id = uuid4()
        with pytest.raises(ValueError, match="Unknown quota_type"):
            check_quota_available(db, user_id, "bogus_type")

    def test_unknown_quota_type_consume_raises(self, db):
        user_id = uuid4()
        with pytest.raises(ValueError, match="Unknown quota_type"):
            consume_quota(db, user_id, "bogus_type")

    def test_unknown_quota_type_get_used_raises(self, db):
        user_id = uuid4()
        with pytest.raises(ValueError, match="Unknown quota_type"):
            get_quota_used(db, user_id, "bogus_type")

    def test_quota_isolation_between_users(self, db):
        user_a = uuid4()
        user_b = uuid4()
        consume_quota(db, user_a, "research_run")
        consume_quota(db, user_a, "research_run")
        assert get_quota_used(db, user_a, "research_run") == 2
        assert get_quota_used(db, user_b, "research_run") == 0


class TestFreePlanEntitlement:
    def test_no_entitlement_within_free_limit_allowed(self, db):
        user_id = uuid4()
        assert get_entitlement(db, user_id) is None
        assert check_quota_available(db, user_id, "research_run") is True

    def test_no_entitlement_exceeds_free_limit_rejected(self, db):
        user_id = uuid4()
        free_limit = FREE_PLAN_ENTITLEMENT["research_run_limit"]
        # Exhaust free-plan quota
        for _ in range(free_limit):
            consume_quota(db, user_id, "research_run")
        # Now should be rejected
        assert check_quota_available(db, user_id, "research_run") is False

    def test_free_plan_export_limit(self, db):
        user_id = uuid4()
        free_export = FREE_PLAN_ENTITLEMENT["export_limit"]
        for _ in range(free_export):
            consume_quota(db, user_id, "export")
        assert check_quota_available(db, user_id, "export") is False

    def test_free_plan_unknown_type_raises(self, db):
        user_id = uuid4()
        with pytest.raises(ValueError):
            check_quota_available(db, user_id, "nonexistent")

    def test_free_plan_constants_exist(self):
        """Ensure FREE_PLAN_ENTITLEMENT covers all known quota types."""
        from app.auth.entitlements import QUOTA_TYPE_MAP
        for quota_type, field in QUOTA_TYPE_MAP.items():
            assert field in FREE_PLAN_ENTITLEMENT
            assert FREE_PLAN_ENTITLEMENT[field] >= 0


class TestAtomicConsume:
    def test_consume_multiple_times(self, db):
        user_id = uuid4()
        for i in range(1, 11):
            used = consume_quota(db, user_id, "research_run")
            assert used == i

    def test_consume_rejects_non_positive_amount(self, db):
        user_id = uuid4()
        with pytest.raises(ValueError, match="positive integer"):
            consume_quota(db, user_id, "research_run", amount=0)
        with pytest.raises(ValueError, match="positive integer"):
            consume_quota(db, user_id, "research_run", amount=-1)


class TestRequireEntitlement:
    def test_require_entitlement_no_entitlement_within_free_limit(self, db):
        from app.models.user import User
        user = User(id=uuid4(), email="test@example.com", display_name="Test", password_hash="x")
        db.add(user)
        db.flush()

        dep = require_entitlement(quota_type="research_run")
        result = dep(db=db, current_user=user)
        assert result.id == user.id

    def test_require_entitlement_no_entitlement_exceeds_free_limit(self, db):
        from app.models.user import User
        user = User(id=uuid4(), email="test2@example.com", display_name="Test", password_hash="x")
        db.add(user)
        db.flush()

        free_limit = FREE_PLAN_ENTITLEMENT["research_run_limit"]
        for _ in range(free_limit):
            consume_quota(db, user.id, "research_run")

        dep = require_entitlement(quota_type="research_run")
        with pytest.raises(HTTPException) as exc_info:
            dep(db=db, current_user=user)
        assert exc_info.value.status_code == 429

    def test_require_entitlement_expired_raises(self, db):
        from app.models.user import User
        user = User(id=uuid4(), email="test3@example.com", display_name="Test", password_hash="x")
        db.add(user)
        db.flush()

        ent = PilotEntitlement(
            user_id=user.id,
            pilot_access=True,
            expiry_date=utcnow() - timedelta(days=1),
        )
        db.add(ent)
        db.flush()

        dep = require_entitlement(quota_type="research_run")
        with pytest.raises(HTTPException) as exc_info:
            dep(db=db, current_user=user)
        assert exc_info.value.status_code == 403

    def test_require_entitlement_quota_exceeded_raises(self, db):
        from app.models.user import User
        user = User(id=uuid4(), email="test4@example.com", display_name="Test", password_hash="x")
        db.add(user)
        db.flush()

        ent = PilotEntitlement(user_id=user.id, research_run_limit=1)
        db.add(ent)
        db.flush()

        consume_quota(db, user.id, "research_run")

        dep = require_entitlement(quota_type="research_run")
        with pytest.raises(HTTPException) as exc_info:
            dep(db=db, current_user=user)
        assert exc_info.value.status_code == 429

    def test_require_entitlement_zero_limit_rejected(self, db):
        """Zero limit should be rejected even without prior consumption."""
        from app.models.user import User
        user = User(id=uuid4(), email="test5@example.com", display_name="Test", password_hash="x")
        db.add(user)
        db.flush()

        ent = PilotEntitlement(user_id=user.id, research_run_limit=0)
        db.add(ent)
        db.flush()

        dep = require_entitlement(quota_type="research_run")
        with pytest.raises(HTTPException) as exc_info:
            dep(db=db, current_user=user)
        assert exc_info.value.status_code == 429
