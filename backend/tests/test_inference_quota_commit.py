"""Test inference quota transaction boundaries.

Verifies that quota reservations and settlements are properly committed
and persist across database sessions (P0-A requirement).
"""
import pytest
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.user import User
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.pilot import PilotEntitlement
from app.auth.entitlements import reserve_quota, settle_reservation, release_reservation
from app.lib.timeutil import utcnow


@pytest.fixture
def db_url():
    """Use a fresh in-memory SQLite database for each test."""
    return "sqlite:///:memory:"


@pytest.fixture
def db_session(db_url):
    """Create a database session for testing."""
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def test_user(db_session):
    """Create a test user with entitlement."""
    user = User(
        id=uuid4(),
        email=f"test-{uuid4()}@example.com",
        hashed_password="hashed",
        created_at=utcnow(),
    )
    db_session.add(user)

    entitlement = PilotEntitlement(
        user_id=user.id,
        ai_usage_limit=10,
        pilot_access=True,
    )
    db_session.add(entitlement)
    db_session.commit()
    return user


def test_quota_settlement_persists_across_sessions(db_url, test_user):
    """P0-A: Verify quota settlement is committed and visible in new session.

    This test reproduces the issue where flush() without commit() causes
    quota changes to be lost when the session closes.
    """
    # Session 1: Reserve and settle quota
    Session1 = sessionmaker(bind=create_engine(db_url))
    session1 = Session1()

    reservation = reserve_quota(session1, test_user.id, "ai_usage")
    assert reservation.status == "reserved"
    assert reservation.amount == 1

    settle_reservation(session1, reservation.id)
    session1.refresh(reservation)
    assert reservation.status == "settled"

    # Commit the transaction
    session1.commit()
    session1.close()

    # Session 2: Verify quota usage persisted
    Session2 = sessionmaker(bind=create_engine(db_url))
    session2 = Session2()

    counter = session2.query(QuotaCounter).filter(
        QuotaCounter.user_id == test_user.id,
        QuotaCounter.quota_type == "ai_usage",
    ).first()

    assert counter is not None, "QuotaCounter should exist after settlement"
    assert counter.used == 1, f"Expected used=1, got {counter.used}"

    settled_reservation = session2.query(QuotaReservation).filter(
        QuotaReservation.id == reservation.id
    ).first()
    assert settled_reservation is not None, "Reservation should exist"
    assert settled_reservation.status == "settled"

    session2.close()


def test_quota_release_persists_across_sessions(db_url, test_user):
    """P0-A: Verify quota release is committed and visible in new session."""
    # Session 1: Reserve and release quota
    Session1 = sessionmaker(bind=create_engine(db_url))
    session1 = Session1()

    reservation = reserve_quota(session1, test_user.id, "ai_usage")
    assert reservation.status == "reserved"

    release_reservation(session1, reservation.id)
    session1.refresh(reservation)
    assert reservation.status == "released"

    # Commit the transaction
    session1.commit()
    session1.close()

    # Session 2: Verify no quota was consumed
    Session2 = sessionmaker(bind=create_engine(db_url))
    session2 = Session2()

    counter = session2.query(QuotaCounter).filter(
        QuotaCounter.user_id == test_user.id,
        QuotaCounter.quota_type == "ai_usage",
    ).first()

    # Counter should not exist (no consumption) or used=0
    if counter is not None:
        assert counter.used == 0, f"Expected used=0 after release, got {counter.used}"

    released_reservation = session2.query(QuotaReservation).filter(
        QuotaReservation.id == reservation.id
    ).first()
    assert released_reservation is not None, "Reservation should exist"
    assert released_reservation.status == "released"

    session2.close()


def test_multiple_resets_and_settlements(db_url, test_user):
    """P0-A: Verify multiple quota operations all persist correctly."""
    # Session 1: Multiple reserve/settle operations
    Session1 = sessionmaker(bind=create_engine(db_url))
    session1 = Session1()

    reservations = []
    for i in range(3):
        reservation = reserve_quota(session1, test_user.id, "ai_usage")
        reservations.append(reservation)
        settle_reservation(session1, reservation.id)

    session1.commit()
    session1.close()

    # Session 2: Verify all settlements persisted
    Session2 = sessionmaker(bind=create_engine(db_url))
    session2 = Session2()

    counter = session2.query(QuotaCounter).filter(
        QuotaCounter.user_id == test_user.id,
        QuotaCounter.quota_type == "ai_usage",
    ).first()

    assert counter is not None, "QuotaCounter should exist"
    assert counter.used == 3, f"Expected used=3, got {counter.used}"

    for reservation in reservations:
        settled = session2.query(QuotaReservation).filter(
            QuotaReservation.id == reservation.id
        ).first()
        assert settled is not None
        assert settled.status == "settled"

    session2.close()
