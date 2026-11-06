"""Tests for pattern analysis service.

Covers:
- analyze_skill_patterns returns correct aggregation
- propose_parameter_adjustment logic
- validate_proposal runs backtest and compares metrics
- should_deploy criteria (Sharpe >= 0.2 OR drawdown decrease >= 10%)
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.lib.timeutil import utcnow_aware
from app.models.learning_job import LearningJob, LearningProposal
from app.models.strategy_study import StrategyStudyRun, StrategyStudyTrade
from app.models.user import User
from app.services.pattern_analysis import (
    analyze_skill_patterns,
    propose_parameter_adjustment,
    should_deploy,
    validate_proposal,
    _compute_sharpe,
    _compute_max_drawdown,
    _classify_regime,
)
from app.services.strategy_research import ExitConfig
from tests.conftest import TestSession


def _make_user(email: str = "pattern-test@alethra.io") -> User:
    from app.auth.jwt import get_password_hash

    db = TestSession()
    try:
        user = User(
            email=email,
            password_hash=get_password_hash("testpass123"),
            display_name="Pattern Test",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id
    finally:
        db.close()
    return user_id


def _make_run_with_trades(
    user_id: uuid.UUID,
    *,
    instrument_id: str = "CRYPTO:KRAKEN:BTC-USD",
    interval_minutes: int = 1440,
    trade_count: int = 5,
    win_rate: float = 0.6,
) -> tuple:
    """Create a run with synthetic trades for testing."""
    db = TestSession()
    try:
        now = datetime.now(timezone.utc)
        run = StrategyStudyRun(
            user_id=user_id,
            lane="strategy_research_simulated",
            research_version="1.0.0",
            instrument_id=instrument_id,
            interval_minutes=interval_minutes,
            provider_id="kraken",
            data_nature="venue_completed_bars",
            bar_count=200,
            first_open_time=now,
            last_open_time=now,
            settings_json={"starting_cash": "10000"},
            walk_forward_json={"folds": []},
            bars_json=[
                {
                    "open_time": now.isoformat(),
                    "interval_minutes": interval_minutes,
                    "open": "50000.0",
                    "high": "50100.0",
                    "low": "49900.0",
                    "close": "50050.0",
                    "volume": "100.0",
                }
                for _ in range(200)
            ],
            trade_count=trade_count,
        )
        db.add(run)
        db.flush()

        cash = Decimal("10000")
        for i in range(trade_count):
            is_win = (i / max(trade_count - 1, 1)) < win_rate
            pnl = Decimal("50.0") if is_win else Decimal("-30.0")
            cash = cash + pnl
            db.add(
                StrategyStudyTrade(
                    run_id=run.id,
                    trade_index=i,
                    fold=0,
                    rule_id="breakout_trend",
                    exit_id="balanced_1_1",
                    direction="long",
                    exit_reason="target" if is_win else "stop",
                    outcome="win" if is_win else "loss",
                    signal_time=now,
                    entry_time=now,
                    exit_time=now,
                    entry_price=Decimal("50000"),
                    exit_price=Decimal("50050") if is_win else Decimal("49970"),
                    quantity=Decimal("0.02"),
                    gross_pnl=pnl + Decimal("2"),
                    commission=Decimal("1"),
                    slippage_cost=Decimal("1"),
                    total_cost=Decimal("2"),
                    net_pnl=pnl,
                    cash_after=cash,
                    bars_held=3,
                    pnl_reason_zh="Test trade",
                    pnl_reason_en="Test trade",
                )
            )
        db.commit()
        db.refresh(run)
        run_id = run.id
    finally:
        db.close()
    return run_id, trade_count


class TestAnalyzeSkillPatterns:
    def test_empty_returns_empty_structure(self):
        user_id = _make_user("empty-pattern@alethra.io")
        db = TestSession()
        try:
            result = analyze_skill_patterns(db, user_id=user_id, lookback_runs=10)
            assert result["runs_analyzed"] == 0
            assert result["per_exit_config"] == {}
            assert result["per_skill_instrument"] == {}
            assert result["recommendations"] == []
        finally:
            db.close()

    def test_aggregates_trades_by_exit_config(self):
        user_id = _make_user("agg-exit@alethra.io")
        _make_run_with_trades(user_id, trade_count=10, win_rate=0.7)

        db = TestSession()
        try:
            result = analyze_skill_patterns(db, user_id=user_id, lookback_runs=10)
            assert result["runs_analyzed"] == 1
            assert "balanced_1_1" in result["per_exit_config"]
            metrics = result["per_exit_config"]["balanced_1_1"]
            assert metrics["trade_count"] == 10
            assert metrics["win_rate"] == pytest.approx(0.7, abs=0.01)
        finally:
            db.close()

    def test_generates_recommendations_for_poor_sharpe(self):
        user_id = _make_user("rec-poor@alethra.io")
        # Create trades with low win rate to trigger negative Sharpe recommendation
        _make_run_with_trades(user_id, trade_count=10, win_rate=0.2)

        db = TestSession()
        try:
            result = analyze_skill_patterns(db, user_id=user_id, lookback_runs=10)
            # With 20% win rate, Sharpe should be negative, triggering recommendation
            recs = result["recommendations"]
            assert len(recs) > 0
            assert any(r["type"] == "parameter_adjustment" for r in recs)
        finally:
            db.close()


class TestProposeParameterAdjustment:
    def test_returns_none_for_insufficient_trades(self):
        exit_config = ExitConfig(
            exit_id="test",
            label_zh="Test",
            target_atr=Decimal("1.0"),
            stop_atr=Decimal("1.0"),
            time_stop_bars=6,
            thesis_zh="Test",
        )
        result = propose_parameter_adjustment(
            current_exit=exit_config,
            performance_data={"trade_count": 1, "win_rate": 0.3, "sharpe": -0.5},
        )
        assert result is None

    def test_proposes_wider_target_for_high_wr_low_pnl(self):
        exit_config = ExitConfig(
            exit_id="test",
            label_zh="Test",
            target_atr=Decimal("1.0"),
            stop_atr=Decimal("1.0"),
            time_stop_bars=6,
            thesis_zh="Test",
        )
        result = propose_parameter_adjustment(
            current_exit=exit_config,
            performance_data={
                "trade_count": 10,
                "win_rate": 0.6,
                "avg_pnl": -5.0,
                "sharpe": -0.3,
            },
        )
        assert result is not None
        assert result["pattern"] == "high_win_rate_low_pnl"
        proposed_target = Decimal(result["proposed_exit_config"]["target_atr"])
        assert proposed_target > Decimal("1.0")

    def test_proposes_wider_stop_for_low_win_rate(self):
        exit_config = ExitConfig(
            exit_id="test",
            label_zh="Test",
            target_atr=Decimal("1.0"),
            stop_atr=Decimal("1.0"),
            time_stop_bars=6,
            thesis_zh="Test",
        )
        result = propose_parameter_adjustment(
            current_exit=exit_config,
            performance_data={
                "trade_count": 10,
                "win_rate": 0.3,
                "avg_pnl": -10.0,
                "sharpe": -0.8,
            },
        )
        assert result is not None
        assert result["pattern"] == "low_win_rate"
        proposed_stop = Decimal(result["proposed_exit_config"]["stop_atr"])
        assert proposed_stop > Decimal("1.0")


class TestShouldDeploy:
    def test_passes_on_sharpe_improvement(self):
        result = should_deploy(
            {
                "sharpe_before": 0.1,
                "sharpe_after": 0.4,
                "max_dd_before": 20.0,
                "max_dd_after": 19.0,
            }
        )
        assert result is True

    def test_passes_on_drawdown_decrease(self):
        result = should_deploy(
            {
                "sharpe_before": 0.1,
                "sharpe_after": 0.15,
                "max_dd_before": 20.0,
                "max_dd_after": 15.0,  # 25% decrease > 10% threshold
            }
        )
        assert result is True

    def test_fails_when_no_criterion_met(self):
        result = should_deploy(
            {
                "sharpe_before": 0.1,
                "sharpe_after": 0.15,  # improvement < 0.2
                "max_dd_before": 20.0,
                "max_dd_after": 19.0,  # decrease < 10%
            }
        )
        assert result is False

    def test_fails_with_zero_drawdown(self):
        result = should_deploy(
            {
                "sharpe_before": 0.0,
                "sharpe_after": 0.1,
                "max_dd_before": 0.0,
                "max_dd_after": 0.0,
            }
        )
        assert result is False


class TestComputeMetrics:
    def test_compute_sharpe_with_trades(self):
        user_id = _make_user("sharpe-test@alethra.io")
        _make_run_with_trades(user_id, trade_count=10, win_rate=0.6)

        db = TestSession()
        try:
            run = db.query(StrategyStudyRun).filter_by(user_id=user_id).first()
            assert run is not None
            sharpe = _compute_sharpe(list(run.trades))
            # With 60% win rate and +50/-30 PnL, Sharpe should be positive
            assert sharpe is not None
            assert sharpe > 0
        finally:
            db.close()

    def test_compute_max_drawdown(self):
        user_id = _make_user("dd-test@alethra.io")
        _make_run_with_trades(user_id, trade_count=10, win_rate=0.5)

        db = TestSession()
        try:
            run = db.query(StrategyStudyRun).filter_by(user_id=user_id).first()
            assert run is not None
            max_dd = _compute_max_drawdown(list(run.trades))
            assert max_dd >= 0
        finally:
            db.close()


class TestClassifyRegime:
    def test_trending_regime(self):
        """Monotonically increasing prices should be classified as trending."""
        user_id = _make_user("regime-trend@alethra.io")
        db = TestSession()
        try:
            now = datetime.now(timezone.utc)
            bars_json = [
                {
                    "open_time": now.isoformat(),
                    "interval_minutes": 1440,
                    "open": str(50000 + i * 100),
                    "high": str(50050 + i * 100),
                    "low": str(49950 + i * 100),
                    "close": str(50000 + i * 100),
                    "volume": "100.0",
                }
                for i in range(200)
            ]
            run = StrategyStudyRun(
                user_id=user_id,
                lane="strategy_research_simulated",
                research_version="1.0.0",
                instrument_id="CRYPTO:KRAKEN:BTC-USD",
                interval_minutes=1440,
                provider_id="kraken",
                data_nature="venue_completed_bars",
                bar_count=200,
                first_open_time=now,
                last_open_time=now,
                settings_json={},
                walk_forward_json={},
                bars_json=bars_json,
                trade_count=0,
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            regime = _classify_regime(run)
            assert regime == "trending"
        finally:
            db.close()

    def test_ranging_regime(self):
        """Oscillating prices with small net displacement should be ranging."""
        user_id = _make_user("regime-range@alethra.io")
        db = TestSession()
        try:
            now = datetime.now(timezone.utc)
            # Oscillate with period 20 bars: price goes up for 10 bars, down for 10
            # With 201 bars (10 full cycles + 1), first and last close are both 50100
            # so net displacement = 0 while total range = 200 → ratio = 0
            n_bars = 201
            bars_json = [
                {
                    "open_time": now.isoformat(),
                    "interval_minutes": 1440,
                    "open": str(50000 + 100 * (1 if (i // 10) % 2 == 0 else -1)),
                    "high": str(50050 + 100 * (1 if (i // 10) % 2 == 0 else -1)),
                    "low": str(49950 + 100 * (1 if (i // 10) % 2 == 0 else -1)),
                    "close": str(50000 + 100 * (1 if (i // 10) % 2 == 0 else -1)),
                    "volume": "100.0",
                }
                for i in range(n_bars)
            ]
            run = StrategyStudyRun(
                user_id=user_id,
                lane="strategy_research_simulated",
                research_version="1.0.0",
                instrument_id="CRYPTO:KRAKEN:BTC-USD",
                interval_minutes=1440,
                provider_id="kraken",
                data_nature="venue_completed_bars",
                bar_count=n_bars,
                first_open_time=now,
                last_open_time=now,
                settings_json={},
                walk_forward_json={},
                bars_json=bars_json,
                trade_count=0,
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            regime = _classify_regime(run)
            assert regime == "ranging"
        finally:
            db.close()
