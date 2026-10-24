"""Commission data-path verification on isolated PostgreSQL.

Requires: ALETHRA_PG_TEST=1 and DATABASE_URL=postgresql://user@localhost:55432/alethra_test
"""
import os
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from tests.conftest import TestSession, TEST_ENGINE
from app.models.user import User
from app.models.paper_portfolio import PaperPortfolio
from app.models.execution import OrderIntent, IntentStatus, IntentSide, IntentOrderType
from app.services.execution_agent_api import agent_report_fill
from app.lib.timeutil import utcnow
from app.auth.jwt import get_password_hash


# Skip all tests in this module unless running on PostgreSQL
pytestmark = pytest.mark.skipif(
    TEST_ENGINE.dialect.name != "postgresql",
    reason="PostgreSQL-specific tests: set ALETHRA_PG_TEST=1 to run on isolated PostgreSQL"
)


@pytest.fixture
def pg_setup():
    """Create user, portfolio, and intent for testing."""
    session = TestSession()
    user = User(
        id=uuid.uuid4(),
        email=f'data-test-{uuid.uuid4().hex[:8]}@example.com',
        password_hash=get_password_hash('test-password'),
        display_name='Data Test',
        is_active=True,
        is_admin=False,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(user)
    session.flush()

    portfolio = PaperPortfolio(
        id=uuid.uuid4(),
        owner_id=user.id,
        initial_virtual_cash=100000.0,
        current_virtual_cash=100000.0,
        created_at=utcnow(),
    )
    session.add(portfolio)
    session.flush()

    intent = OrderIntent(
        id=uuid.uuid4(),
        user_id=user.id,
        portfolio_id=portfolio.id,
        intent_id=f'data-test-{uuid.uuid4().hex[:8]}',
        symbol='DATA-TEST:AAPL',
        side=IntentSide.BUY,
        quantity=100,
        order_type=IntentOrderType.MARKET,
        status=IntentStatus.submitted,
        authorization_token='test-token',
        expires_at=datetime(2099, 1, 1),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(intent)
    session.commit()

    yield user, intent

    session.close()


class TestCommissionDataPath:
    def test_omitted_commission_is_null(self, pg_setup):
        user, intent = pg_setup
        session = TestSession()

        fill = agent_report_fill(
            session,
            intent_id=intent.intent_id,
            user_id=user.id,
            quantity=10,
            price=150.0,
            fill_time=utcnow(),
            broker_ref=f'DATA-NULL-{uuid.uuid4().hex[:8]}',
        )
        session.commit()

        assert fill.commission is None

        row = session.execute(
            text("SELECT commission FROM fill_records WHERE id = :id"),
            {"id": fill.id}
        ).fetchone()
        assert row[0] is None
        session.close()

    def test_explicit_zero_commission(self, pg_setup):
        user, intent = pg_setup
        session = TestSession()

        fill = agent_report_fill(
            session,
            intent_id=intent.intent_id,
            user_id=user.id,
            quantity=10,
            price=150.0,
            fill_time=utcnow(),
            broker_ref=f'DATA-ZERO-{uuid.uuid4().hex[:8]}',
            commission=0.0,
        )
        session.commit()

        assert fill.commission == 0.0

        row = session.execute(
            text("SELECT commission FROM fill_records WHERE id = :id"),
            {"id": fill.id}
        ).fetchone()
        assert row[0] == 0.0
        session.close()

    def test_nonzero_commission(self, pg_setup):
        user, intent = pg_setup
        session = TestSession()

        fill = agent_report_fill(
            session,
            intent_id=intent.intent_id,
            user_id=user.id,
            quantity=10,
            price=150.0,
            fill_time=utcnow(),
            broker_ref=f'DATA-FEE-{uuid.uuid4().hex[:8]}',
            commission=1.50,
        )
        session.commit()

        assert fill.commission == 1.50
        session.close()

    def test_idempotent_re_report_preserves_commission(self, pg_setup):
        user, intent = pg_setup
        session = TestSession()
        now = utcnow()
        broker_ref = f'DATA-IDEM-{uuid.uuid4().hex[:8]}'

        fill1 = agent_report_fill(
            session, intent_id=intent.intent_id, user_id=user.id,
            quantity=10, price=150.0, fill_time=now,
            broker_ref=broker_ref, commission=2.50,
        )
        session.commit()

        fill2 = agent_report_fill(
            session, intent_id=intent.intent_id, user_id=user.id,
            quantity=10, price=150.0, fill_time=now,
            broker_ref=broker_ref, commission=2.50,
        )
        session.commit()

        assert fill1.id == fill2.id
        assert fill2.commission == 2.50
        session.close()

    def test_sql_null_vs_zero_distinction(self, pg_setup):
        user, intent = pg_setup
        session = TestSession()
        now = utcnow()

        f_null = agent_report_fill(
            session, intent_id=intent.intent_id, user_id=user.id,
            quantity=10, price=150.0, fill_time=now,
            broker_ref=f'DATA-SQL-NULL-{uuid.uuid4().hex[:8]}',
        )
        f_zero = agent_report_fill(
            session, intent_id=intent.intent_id, user_id=user.id,
            quantity=10, price=150.0, fill_time=now,
            broker_ref=f'DATA-SQL-ZERO-{uuid.uuid4().hex[:8]}',
            commission=0.0,
        )
        f_fee = agent_report_fill(
            session, intent_id=intent.intent_id, user_id=user.id,
            quantity=10, price=150.0, fill_time=now,
            broker_ref=f'DATA-SQL-FEE-{uuid.uuid4().hex[:8]}',
            commission=3.75,
        )
        session.commit()

        rows = session.execute(
            text("SELECT broker_ref, commission, commission IS NULL as is_null FROM fill_records WHERE broker_ref LIKE 'DATA-SQL-%' ORDER BY broker_ref")
        ).fetchall()

        by_ref = {r[0]: (r[1], r[2]) for r in rows}

        null_val, null_is_null = by_ref[f_null.broker_ref]
        assert null_is_null is True

        zero_val, zero_is_null = by_ref[f_zero.broker_ref]
        assert zero_is_null is False
        assert zero_val == 0.0

        fee_val, fee_is_null = by_ref[f_fee.broker_ref]
        assert fee_is_null is False
        assert fee_val == 3.75

        session.close()
