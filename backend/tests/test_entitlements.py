import pytest
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from app.models.quota import QuotaCounter
from app.models.pilot import PilotEntitlement
from app.auth.entitlements import (
    get_entitlement,
    check_entitlement_active,
    get_quota_used,
    consume_quota,
    check_quota_available,
    require_entitlement,
)
from tests.conftest import TestSession


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


class TestEntitlementChecks:
    def test_no_entitlement_means_no_restriction(self, db):
        user_id = uuid4()
        assert get_entitlement(db, user_id) is None
        assert check_quota_available(db, user_id, "research_run")

    def test_active_entitlement(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(
            user_id=user_id,
            pilot_access=True,
            research_run_limit=10,
            expiry_date=datetime.utcnow() + timedelta(days=30),
        )
        db.add(ent)
        db.flush()
        assert check_entitlement_active(ent) is True

    def test_expired_entitlement(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(
            user_id=user_id,
            pilot_access=True,
            expiry_date=datetime.utcnow() - timedelta(days=1),
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

    def test_unknown_quota_type_always_available(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=0)
        db.add(ent)
        db.flush()
        assert check_quota_available(db, user_id, "unknown_type")

    def test_zero_limit_means_unlimited(self, db):
        user_id = uuid4()
        ent = PilotEntitlement(user_id=user_id, research_run_limit=0)
        db.add(ent)
        db.flush()
        assert check_quota_available(db, user_id, "research_run")

    def test_quota_isolation_between_users(self, db):
        user_a = uuid4()
        user_b = uuid4()
        consume_quota(db, user_a, "research_run")
        consume_quota(db, user_a, "research_run")
        assert get_quota_used(db, user_a, "research_run") == 2
        assert get_quota_used(db, user_b, "research_run") == 0


class TestRequireEntitlement:
    def test_require_entitlement_no_entitlement_passes(self, db):
        from app.models.user import User
        user = User(id=uuid4(), email="test@example.com", display_name="Test", password_hash="x")
        db.add(user)
        db.flush()

        dep = require_entitlement(quota_type="research_run")
        result = dep(db=db, current_user=user)
        assert result.id == user.id

    def test_require_entitlement_expired_raises(self, db):
        from app.models.user import User
        user = User(id=uuid4(), email="test@example.com", display_name="Test", password_hash="x")
        db.add(user)
        db.flush()

        ent = PilotEntitlement(
            user_id=user.id,
            pilot_access=True,
            expiry_date=datetime.utcnow() - timedelta(days=1),
        )
        db.add(ent)
        db.flush()

        dep = require_entitlement(quota_type="research_run")
        with pytest.raises(HTTPException) as exc_info:
            dep(db=db, current_user=user)
        assert exc_info.value.status_code == 403

    def test_require_entitlement_quota_exceeded_raises(self, db):
        from app.models.user import User
        user = User(id=uuid4(), email="test@example.com", display_name="Test", password_hash="x")
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
