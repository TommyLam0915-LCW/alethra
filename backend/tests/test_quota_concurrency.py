"""PostgreSQL concurrency tests for quota operations (P0-B).

Verifies that quota reservation, settlement, and release are atomic
and safe under concurrent access using real PostgreSQL with multiple
connections/sessions.

These tests require a disposable PostgreSQL container.
Set ALETHRA_TEST_POSTGRES_URL environment variable to enable.
"""
import pytest
import os
import threading
import time
from uuid import uuid4
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.user import User
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.pilot import PilotEntitlement
from app.auth.entitlements import reserve_quota, settle_reservation, release_reservation
from app.lib.timeutil import utcnow


POSTGRES_URL = os.environ.get(
    "ALETHRA_TEST_POSTGRES_URL",
    "postgresql://test:test@localhost:5434/alethra_test"
)

# Check if PostgreSQL is available at module import time
try:
    _test_engine = create_engine(POSTGRES_URL)
    with _test_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    _test_engine.dispose()
except Exception:
    pytest.skip("PostgreSQL not available (set ALETHRA_TEST_POSTGRES_URL)", allow_module_level=True)


@pytest.fixture(scope="module")
def pg_engine():
    """Create PostgreSQL engine for concurrency tests."""
    engine = create_engine(POSTGRES_URL, pool_size=10, max_overflow=20)
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        pytest.skip(f"Failed to create tables: {e}")
    yield engine
    try:
        Base.metadata.drop_all(engine)
    except Exception:
        # Ignore teardown errors (e.g., circular dependencies)
        pass
    engine.dispose()


@pytest.fixture
def test_user_with_limit(pg_engine):
    """Create a test user with quota limit=2 for concurrency tests."""
    Session = sessionmaker(bind=pg_engine)
    session = Session()

    user_id = uuid4()
    user = User(
        id=user_id,
        email=f"concurrent-{uuid4()}@example.com",
        password_hash="hashed",
        created_at=utcnow(),
    )
    session.add(user)
    session.flush()  # Flush user to database before creating entitlement (FK constraint)

    entitlement = PilotEntitlement(
        user_id=user_id,
        ai_usage_limit=2,  # Limit of 2 for testing races
        pilot_access=True,
    )
    session.add(entitlement)
    session.commit()
    session.close()

    yield user_id

    # Cleanup
    session = Session()
    session.query(QuotaReservation).filter(QuotaReservation.user_id == user_id).delete()
    session.query(QuotaCounter).filter(QuotaCounter.user_id == user_id).delete()
    session.query(PilotEntitlement).filter(PilotEntitlement.user_id == user_id).delete()
    session.query(User).filter(User.id == user_id).delete()
    session.commit()
    session.close()


def test_concurrent_reservations_with_limit(pg_engine, test_user_with_limit):
    """P0-B: Two concurrent reservations with limit=2, each requesting 2 units.

    At most one should succeed. This tests the atomicity of the check-and-reserve
    operation under concurrent access.
    """
    results = {"success": 0, "failed": 0, "errors": []}
    lock = threading.Lock()
    barrier = threading.Barrier(2)  # Synchronize both threads to start at the same time

    def try_reserve(thread_id):
        Session = sessionmaker(bind=pg_engine)
        session = Session()
        try:
            barrier.wait()  # Synchronize start
            reservation = reserve_quota(session, test_user_with_limit, "ai_usage", amount=2)
            session.commit()
            with lock:
                results["success"] += 1
        except Exception as e:
            with lock:
                results["failed"] += 1
                if "quota_exceeded" not in str(e):
                    results["errors"].append(f"Thread {thread_id}: {e}")
        finally:
            session.close()

    threads = [threading.Thread(target=try_reserve, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results["success"] <= 1, f"At most 1 reservation should succeed, got {results['success']}"
    assert results["failed"] >= 1, f"At least 1 should fail due to quota limit"
    assert len(results["errors"]) == 0, f"Unexpected errors: {results['errors']}"

    # Verify final state: exactly 2 units reserved (if one succeeded)
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    reserved = session.query(QuotaReservation).filter(
        QuotaReservation.user_id == test_user_with_limit,
        QuotaReservation.status == "reserved",
    ).all()
    total_reserved = sum(r.amount for r in reserved)
    assert total_reserved <= 2, f"Total reserved should be <= 2, got {total_reserved}"
    session.close()


def test_concurrent_settlement_same_reservation(pg_engine, test_user_with_limit):
    """P0-B: Two threads try to settle the same reservation concurrently.

    Only one should succeed. The other should fail because the reservation
    is no longer in 'reserved' status.
    """
    # Create a reservation first
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    reservation = reserve_quota(session, test_user_with_limit, "ai_usage", amount=1)
    session.commit()
    reservation_id = reservation.id
    session.close()

    results = {"success": 0, "failed": 0, "errors": []}
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def try_settle(thread_id):
        Session = sessionmaker(bind=pg_engine)
        session = Session()
        try:
            barrier.wait()  # Synchronize start
            settle_reservation(session, reservation_id)
            session.commit()
            with lock:
                results["success"] += 1
        except Exception as e:
            with lock:
                results["failed"] += 1
                if "not in 'reserved' status" not in str(e):
                    results["errors"].append(f"Thread {thread_id}: {e}")
        finally:
            session.close()

    threads = [threading.Thread(target=try_settle, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results["success"] == 1, f"Exactly 1 settlement should succeed, got {results['success']}"
    assert results["failed"] == 1, f"Exactly 1 should fail, got {results['failed']}"
    assert len(results["errors"]) == 0, f"Unexpected errors: {results['errors']}"

    # Verify counter: should be exactly 1, not 2
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    counter = session.query(QuotaCounter).filter(
        QuotaCounter.user_id == test_user_with_limit,
        QuotaCounter.quota_type == "ai_usage",
    ).first()
    assert counter is not None
    assert counter.used == 1, f"Counter should be 1 (not double-settled), got {counter.used}"
    session.close()


def test_concurrent_settle_and_release(pg_engine, test_user_with_limit):
    """P0-B: One thread settles, another releases the same reservation.

    Only one should succeed. Final state should be consistent.
    """
    # Create a reservation
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    reservation = reserve_quota(session, test_user_with_limit, "ai_usage", amount=1)
    session.commit()
    reservation_id = reservation.id
    session.close()

    results = {"settled": 0, "released": 0, "errors": []}
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def try_settle():
        Session = sessionmaker(bind=pg_engine)
        session = Session()
        try:
            barrier.wait()
            settle_reservation(session, reservation_id)
            session.commit()
            with lock:
                results["settled"] += 1
        except Exception as e:
            with lock:
                if "not in 'reserved' status" not in str(e):
                    results["errors"].append(f"Settle: {e}")
        finally:
            session.close()

    def try_release():
        Session = sessionmaker(bind=pg_engine)
        session = Session()
        try:
            barrier.wait()
            release_reservation(session, reservation_id)
            session.commit()
            with lock:
                results["released"] += 1
        except Exception as e:
            with lock:
                if "not in 'reserved' status" not in str(e):
                    results["errors"].append(f"Release: {e}")
        finally:
            session.close()

    t1 = threading.Thread(target=try_settle)
    t2 = threading.Thread(target=try_release)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    total = results["settled"] + results["released"]
    assert total == 1, f"Exactly one operation should succeed, got {total}"
    assert len(results["errors"]) == 0, f"Unexpected errors: {results['errors']}"

    # Verify final state
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    reservation = session.query(QuotaReservation).filter(
        QuotaReservation.id == reservation_id
    ).first()
    assert reservation.status in ("settled", "released")

    counter = session.query(QuotaCounter).filter(
        QuotaCounter.user_id == test_user_with_limit,
        QuotaCounter.quota_type == "ai_usage",
    ).first()

    if reservation.status == "settled":
        assert counter is not None and counter.used == 1
    else:  # released
        # Counter may not exist or used=0
        if counter is not None:
            assert counter.used == 0

    session.close()


def test_reservation_counter_creation_race(pg_engine):
    """P0-B: Two concurrent reservations when no counter exists yet.

    Both should succeed in creating the counter atomically without
    duplicate key errors or lost updates.
    """
    # Create a fresh user with no counter
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    user_id = uuid4()
    user = User(
        id=user_id,
        email=f"race-{uuid4()}@example.com",
        password_hash="hashed",
        created_at=utcnow(),
    )
    session.add(user)
    session.flush()  # Flush user to database before creating entitlement (FK constraint)
    entitlement = PilotEntitlement(
        user_id=user_id,
        ai_usage_limit=10,
        pilot_access=True,
    )
    session.add(entitlement)
    session.commit()
    session.close()

    results = {"success": 0, "errors": []}
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def try_reserve(thread_id):
        Session = sessionmaker(bind=pg_engine)
        session = Session()
        try:
            barrier.wait()
            reservation = reserve_quota(session, user_id, "ai_usage", amount=1)
            settle_reservation(session, reservation.id)
            session.commit()
            with lock:
                results["success"] += 1
        except Exception as e:
            with lock:
                results["errors"].append(f"Thread {thread_id}: {e}")
        finally:
            session.close()

    threads = [threading.Thread(target=try_reserve, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results["success"] == 2, f"Both reservations should succeed, got {results['success']}"
    assert len(results["errors"]) == 0, f"Unexpected errors: {results['errors']}"

    # Verify counter: should be exactly 2
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    counter = session.query(QuotaCounter).filter(
        QuotaCounter.user_id == user_id,
        QuotaCounter.quota_type == "ai_usage",
    ).first()
    assert counter is not None
    assert counter.used == 2, f"Counter should be 2, got {counter.used}"
    session.close()

    # Cleanup
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    session.query(QuotaReservation).filter(QuotaReservation.user_id == user_id).delete()
    session.query(QuotaCounter).filter(QuotaCounter.user_id == user_id).delete()
    session.query(PilotEntitlement).filter(PilotEntitlement.user_id == user_id).delete()
    session.query(User).filter(User.id == user_id).delete()
    session.commit()
    session.close()
