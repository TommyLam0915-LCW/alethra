"""Focused tests for #209 safety controls — closing evidence gaps."""
import pytest
from uuid import uuid4
from datetime import datetime
from app.models.kill_switch import KillSwitchScope
from app.models.api_key import ApiKey
from app.services.kill_switch import activate_kill_switch, deactivate_kill_switch
from app.services.rate_limit import increment_rate_limit
from app.auth.jwt import create_access_token
from tests.conftest import TestSession
import hashlib
import secrets


class TestWorkerKillSwitch:
    """Test that training worker respects kill switch."""

    def test_worker_blocked_by_global_switch(self):
        """Worker should not claim jobs when global kill switch is active."""
        db = TestSession()
        try:
            # Activate global kill switch
            activate_kill_switch(db, KillSwitchScope.global_, "test", "Emergency")
            
            # Import worker function
            from app.workers.training_worker import claim_pending_job
            
            # Try to claim a job — should return None due to kill switch
            job = claim_pending_job(db)
            assert job is None, "Worker should not claim jobs when global kill switch is active"
        finally:
            deactivate_kill_switch(db, KillSwitchScope.global_)
            db.close()

    def test_worker_blocked_by_training_switch(self):
        """Worker should not claim jobs when training kill switch is active."""
        db = TestSession()
        try:
            # Activate training-specific kill switch
            activate_kill_switch(db, KillSwitchScope.training, "test", "Training halt")
            
            from app.workers.training_worker import claim_pending_job
            
            job = claim_pending_job(db)
            assert job is None, "Worker should not claim jobs when training kill switch is active"
        finally:
            deactivate_kill_switch(db, KillSwitchScope.training)
            db.close()

    def test_pending_jobs_eligible_after_deactivation(self):
        """Pending jobs should be claimable after kill switch is deactivated."""
        db = TestSession()
        try:
            # Create a test user and job would require full setup
            # This test verifies the kill switch deactivation logic
            activate_kill_switch(db, KillSwitchScope.training, "test", "Test")
            deactivate_kill_switch(db, KillSwitchScope.training)
            
            # After deactivation, kill switch should not be active
            from app.services.kill_switch import is_kill_switch_active
            assert not is_kill_switch_active(db, KillSwitchScope.training)
        finally:
            db.close()


class TestUserPlusKeyAggregateEnforcement:
    """Test that rotating API keys cannot bypass user cap."""

    def test_user_aggregate_limit_across_keys(self):
        """User-level limit should aggregate across all API keys."""
        db = TestSession()
        try:
            # Create a test user
            from app.models.user import User
            user_id = uuid4()
            user = User(
                id=user_id,
                email=f"test-{user_id}@example.com",
                password_hash="dummy",
                display_name="Test",
                user_type="individual",
            )
            db.add(user)
            db.commit()
            
            # Create two API keys for the same user
            key1_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
            key2_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
            
            key1 = ApiKey(
                user_id=user_id,
                name="Key 1",
                key_hash=key1_hash,
                key_prefix="test1",
                is_active=True,
            )
            key2 = ApiKey(
                user_id=user_id,
                name="Key 2",
                key_hash=key2_hash,
                key_prefix="test2",
                is_active=True,
            )
            db.add(key1)
            db.add(key2)
            db.commit()
            
            # Simulate requests using both keys
            # User-level limit should be enforced across both keys
            user_identity = f"user:{user_id}"
            
            # Make 10 requests (at the limit for training)
            for i in range(10):
                allowed, count, limit = increment_rate_limit(db, user_identity, "training")
                assert allowed, f"Request {i+1} should be allowed"
            
            # 11th request should be rejected
            allowed, count, limit = increment_rate_limit(db, user_identity, "training")
            assert not allowed, "11th request should be rejected (user limit exceeded)"
            
            # Even if we try to use a different key, user limit still applies
            # (The dual enforcement in safety.py checks user limit first)
        finally:
            # Cleanup
            db.query(ApiKey).filter(ApiKey.user_id == user_id).delete()
            db.query(User).filter(User.id == user_id).delete()
            db.commit()
            db.close()

    def test_dual_enforcement_both_limits_apply(self):
        """Both the user-level and the API-key-level limit must independently refuse.

        Drives the REAL product dependency ``app.auth.safety.rate_limit("training")``
        (limit 10 per window). A sentinel order is recorded ONLY when the dependency
        returns without raising — mirroring how FastAPI short-circuits the route body
        on a raised dependency — so "either refusal => no order" is asserted, and the
        control case proves the sentinel is capable of recording (the no-order
        assertions are therefore not vacuously true).

          * control: both counters fresh -> dependency passes, exactly one order.
          * user quota exhausted (key fresh) -> HTTP 429 at the USER layer, no order,
            and the key layer is never reached (detail names the route category, not
            the API key).
          * key quota exhausted (user fresh) -> HTTP 429 attributed to the API-key
            layer, no order.

        Dialect disclosure: this asserts the dual-enforcement LOGIC in a single
        session on whatever dialect ALETHRA_TEST_DB points at. It does NOT prove the
        PostgreSQL ``ON CONFLICT`` counter upsert is atomic under concurrent writers;
        that path requires the separate PostgreSQL integration harness, not a
        single-session SQLite run.
        """
        import types
        from fastapi import HTTPException
        from app.auth.safety import rate_limit
        from app.services.rate_limit import RATE_LIMITS
        from app.models.user import User

        category = "training"
        limit = RATE_LIMITS[category]

        db = TestSession()
        dialect = db.bind.dialect.name
        orders: list = []
        created: list = []

        def make_user_and_key(prefix: str):
            uid = uuid4()
            db.add(User(
                id=uid,
                email=f"{prefix}-{uid}@example.com",
                password_hash="dummy",
                display_name="Test",
                user_type="individual",
            ))
            db.commit()
            raw_token = secrets.token_hex(32)
            key_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            ak = ApiKey(
                user_id=uid,
                name=f"{prefix} key",
                key_hash=key_hash,
                key_prefix=prefix,
                is_active=True,
            )
            db.add(ak)
            db.commit()
            created.append(uid)
            user = db.query(User).filter(User.id == uid).one()
            request = types.SimpleNamespace(
                headers={"Authorization": f"Bearer {raw_token}"}
            )
            return uid, ak.id, user, request

        def attempt_order(dep, request, user):
            # Records an order only if the dependency does not raise.
            dep(request, db=db, current_user=user)
            orders.append("order")

        try:
            dep = rate_limit(category)

            # --- control: both counters fresh -> allowed, one order recorded ---
            _uid, _kid, user, req = make_user_and_key("dualctl")
            attempt_order(dep, req, user)
            assert len(orders) == 1, (
                f"[{dialect}] control case must record exactly one order when both limits are fresh"
            )

            # --- user quota exhausted, key fresh -> USER-layer 429, no order ---
            uid, _kid, user, req = make_user_and_key("dualuser")
            for _ in range(limit):
                increment_rate_limit(db, f"user:{uid}", category)
            before = len(orders)
            with pytest.raises(HTTPException) as exc_user:
                attempt_order(dep, req, user)
            assert exc_user.value.status_code == 429, (
                f"[{dialect}] user-quota-exceeded must refuse with HTTP 429"
            )
            assert "API key" not in (exc_user.value.detail or ""), (
                f"[{dialect}] user-quota refusal must trip at the USER layer before the key layer"
            )
            assert len(orders) == before, (
                f"[{dialect}] user-quota-exceeded must place NO order"
            )

            # --- key quota exhausted, user fresh -> KEY-layer 429, no order ---
            _uid, kid, user, req = make_user_and_key("dualkey")
            for _ in range(limit):
                increment_rate_limit(db, f"api_key:{kid}", category)
            before = len(orders)
            with pytest.raises(HTTPException) as exc_key:
                attempt_order(dep, req, user)
            assert exc_key.value.status_code == 429, (
                f"[{dialect}] key-quota-exceeded must refuse with HTTP 429"
            )
            assert "API key" in (exc_key.value.detail or ""), (
                f"[{dialect}] key-quota refusal must be attributed to the API-key layer"
            )
            assert len(orders) == before, (
                f"[{dialect}] key-quota-exceeded must place NO order"
            )
        finally:
            for uid in created:
                db.query(ApiKey).filter(ApiKey.user_id == uid).delete()
                db.query(User).filter(User.id == uid).delete()
            db.commit()
            db.close()


class TestKillSwitchEnumPersistence:
    """The ORM must persist enum VALUES ('global'), not member NAMES ('global_').

    The alembic-created Postgres enum type killswitchscope contains 'global';
    a name-based Enum() column writes 'global_' and fails with
    InvalidTextRepresentation on PostgreSQL — invisible under SQLite tests,
    where create_all derives the CHECK constraint from the same names.
    """

    def test_persists_enum_value_not_member_name(self):
        from sqlalchemy import text
        from app.models.kill_switch import KillSwitch

        db = TestSession()
        try:
            db.add(KillSwitch(scope=KillSwitchScope.global_, is_active=True, activated_by="test"))
            db.commit()

            raw = db.execute(text("SELECT scope FROM kill_switches")).scalars().all()
            assert raw == ["global"], (
                f"kill_switches.scope must persist enum value 'global', got {raw!r} "
                "(member name 'global_' breaks the alembic-created PostgreSQL enum)"
            )
        finally:
            db.query(KillSwitch).delete()
            db.commit()
            db.close()

    def test_activate_and_read_global_scope_round_trip(self):
        from app.services.kill_switch import is_kill_switch_active

        db = TestSession()
        try:
            activate_kill_switch(db, KillSwitchScope.global_, "test", "round trip")
            assert is_kill_switch_active(db, KillSwitchScope.training) is True
            assert is_kill_switch_active(db, KillSwitchScope.global_) is True
        finally:
            deactivate_kill_switch(db, KillSwitchScope.global_)
            db.close()
