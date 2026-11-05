"""The study log keeps one aware-UTC clock per row (PostgreSQL only).

``strategy_study_runs.fetched_at`` is written from an aware UTC datetime, while
``created_at`` came from a naive one. Postgres labels a naive value with the
*session* TimeZone, so on a +08 session the two columns of the same row were
eight hours apart — enough to mis-anchor any improvement loop that sorts the
journal by ``created_at``.

SQLite cannot show this: it has no session TimeZone. The module therefore runs
only against PostgreSQL and pins the zone for its own session, so the hazard is
reproduced deterministically wherever the suite happens to run.
"""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

if not os.environ.get("ALETHRA_TEST_DB", "").startswith("postgresql"):
    pytest.skip(
        "PostgreSQL not configured (run via scripts/run_tests.sh pg)",
        allow_module_level=True,
    )

from sqlalchemy import text

from tests.conftest import TestSession
from app.data.crypto_instruments import KRAKEN_BTC_USD
from app.lib.timeutil import utcnow, utcnow_aware
from app.models.strategy_study import StrategyStudyRun
from app.models.user import User

PINNED_ZONE = "Asia/Shanghai"
PINNED_OFFSET = timedelta(hours=8)


@pytest.fixture
def pinned_session():
    """A test session whose Postgres TimeZone is the +08 one that caused this."""
    with TestSession() as db:
        db.execute(text(f"SET TIME ZONE '{PINNED_ZONE}'"))
        yield db
        db.rollback()


def _new_user(db) -> str:
    user = User(
        id=uuid4(),
        email=f"tz-{uuid4().hex[:8]}@example.test",
        password_hash="test-only",
        display_name="tz test",
    )
    db.add(user)
    db.flush()
    return str(user.id)


def _run_row(db, user_id: str, *, fetched_at: datetime, created_at=None) -> StrategyStudyRun:
    anchor = datetime(2026, 11, 4, 4, 0, tzinfo=timezone.utc)
    run = StrategyStudyRun(
        user_id=user_id,
        research_version="test",
        instrument_id=KRAKEN_BTC_USD.instrument_id,
        interval_minutes=1440,
        provider_id="kraken",
        data_nature="live",
        bar_count=1,
        first_open_time=anchor,
        last_open_time=anchor,
        fetched_at=fetched_at,
        settings_json={},
        walk_forward_json={},
        bars_json=[],
    )
    if created_at is not None:
        run.created_at = created_at
    db.add(run)
    db.flush()
    db.refresh(run)
    return run


def test_created_at_and_fetched_at_agree_under_a_non_utc_session(pinned_session):
    user_id = _new_user(pinned_session)
    fetched = utcnow_aware()
    run = _run_row(pinned_session, user_id, fetched_at=fetched)

    assert run.created_at.tzinfo is not None, "created_at must come back tz-aware"
    assert run.fetched_at.tzinfo is not None
    assert abs(run.created_at - run.fetched_at) < timedelta(seconds=5), (
        f"created_at={run.created_at} fetched_at={run.fetched_at} differ by "
        f"{run.created_at - run.fetched_at}"
    )
    pinned_session.rollback()


def test_the_naive_default_really_was_eight_hours_out(pinned_session):
    """Guard the guard: this harness must still see the old defect.

    Without it the assertion above would also pass on a UTC session, where the
    bug was never visible, and would prove nothing.
    """
    user_id = _new_user(pinned_session)
    fetched = utcnow_aware()
    run = _run_row(pinned_session, user_id, fetched_at=fetched, created_at=utcnow())

    drift = run.created_at - run.fetched_at
    assert abs(abs(drift) - PINNED_OFFSET) < timedelta(seconds=5), (
        f"expected a ~{PINNED_OFFSET} drift from the naive default, got {drift}; "
        f"session TimeZone is not {PINNED_ZONE}"
    )
    pinned_session.rollback()


def test_trade_rows_use_the_same_aware_clock(pinned_session):
    user_id = _new_user(pinned_session)
    fetched = utcnow_aware()
    run = _run_row(pinned_session, user_id, fetched_at=fetched)

    from app.models.strategy_study import StrategyStudyTrade

    trade = StrategyStudyTrade(
        run_id=run.id,
        trade_index=0,
        fold=1,
        rule_id="rsi_midline_reversal",
        exit_id="fixed_exit",
        direction="long",
        exit_reason="signal_flip",
        outcome="earn",
        signal_time=fetched,
        entry_time=fetched,
        exit_time=fetched + timedelta(days=1),
        entry_price=1,
        exit_price=2,
        quantity=1,
        gross_pnl=1,
        commission=0,
        slippage_cost=0,
        total_cost=0,
        net_pnl=1,
        cash_after=1,
        bars_held=1,
        pnl_reason_zh="測試",
        pnl_reason_en="test",
    )
    pinned_session.add(trade)
    pinned_session.flush()
    pinned_session.refresh(trade)

    assert abs(trade.created_at - fetched) < timedelta(seconds=5)
    assert trade.created_at.tzinfo is not None
    pinned_session.rollback()
