"""Paper trading execution engine.

Implements real-time paper trading based on backtested strategy signals.
Supports both historical replay (Option A) and live market data (Option B).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, date
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal

logger = logging.getLogger(__name__)


class PaperTradingEngine:
    """Executes paper trades based on strategy signals."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.active_runs: dict[str, dict] = {}

    def create_portfolio(
        self,
        user_id: str,
        skill: str,
        instrument_id: str,
        interval_minutes: int,
        exit_config: str,
        initial_capital: Decimal = Decimal("100000.00"),
    ) -> dict:
        """Create a new paper trading portfolio and run."""
        db = SessionLocal()
        try:
            # Create portfolio
            portfolio_id = str(uuid.uuid4())
            portfolio_name = f"Live Paper Test - {skill} - {instrument_id}"

            db.execute(
                text("""
                INSERT INTO paper_portfolios (id, owner_id, currency, initial_virtual_cash, current_virtual_cash, status, created_at)
                VALUES (:id, :owner_id, 'USD', :initial, :current, 'active', NOW())
            """),
                {
                    "id": portfolio_id,
                    "owner_id": user_id,
                    "initial": initial_capital,
                    "current": initial_capital,
                },
            )

            # Create run
            run_id = str(uuid.uuid4())
            db.execute(
                text("""
                INSERT INTO paper_trading_runs (
                    id, portfolio_id, skill, instrument_id, interval_minutes, exit_config,
                    start_date, status, initial_capital, total_pnl, total_trades
                )
                VALUES (
                    :id, :portfolio_id, :skill, :instrument, :interval, :exit_config,
                    NOW(), 'running', :initial, 0, 0
                )
            """),
                {
                    "id": run_id,
                    "portfolio_id": portfolio_id,
                    "skill": skill,
                    "instrument": instrument_id,
                    "interval": interval_minutes,
                    "exit_config": exit_config,
                    "initial": initial_capital,
                },
            )

            db.commit()

            logger.info(f"Created paper trading portfolio {portfolio_id} for {skill}")

            return {
                "portfolio_id": portfolio_id,
                "run_id": run_id,
                "skill": skill,
                "instrument": instrument_id,
                "interval_minutes": interval_minutes,
                "initial_capital": float(initial_capital),
                "status": "running",
            }

        except Exception as exc:
            db.rollback()
            logger.exception("Failed to create portfolio")
            raise
        finally:
            db.close()

    def execute_historical_replay(
        self,
        run_id: str,
        user_id: str,
        days_back: int = 30,
        position_size_pct: float = 0.10,
        max_positions: int = 3,
    ) -> dict:
        """Execute paper trading using historical data replay."""
        db = SessionLocal()
        try:
            # Get run details
            run = db.execute(
                text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id},
            ).fetchone()

            if not run:
                raise ValueError(f"Run {run_id} not found")

            skill = run.skill
            instrument = run.instrument_id
            interval = run.interval_minutes
            initial_capital = float(run.initial_capital)

            logger.info(f"Starting historical replay for {skill} on {instrument}")

            # Get historical trades for this skill/instrument/interval
            trades = db.execute(
                text("""
                SELECT
                    t.entry_time,
                    t.exit_time,
                    t.entry_price,
                    t.exit_price,
                    t.net_pnl,
                    t.direction,
                    t.quantity,
                    t.exit_reason
                FROM strategy_study_trades t
                JOIN strategy_study_runs r ON t.run_id = r.id
                WHERE r.instrument_id = :instrument
                  AND r.interval_minutes = :interval
                  AND t.rule_label_zh = :skill
                  AND t.entry_time >= NOW() - INTERVAL '365 days'
                ORDER BY t.entry_time ASC
            """),
                {"instrument": instrument, "interval": interval, "skill": skill},
            ).fetchall()

            logger.info(f"Found {len(trades)} historical trades to replay")

            # Simulate trading
            current_capital = initial_capital
            positions = []
            executed_trades = []
            daily_pnl = {}

            for trade in trades:
                entry_time, exit_time, entry_price, exit_price, net_pnl, direction, quantity, exit_reason = trade

                # Check if we can open position
                if len(positions) >= max_positions:
                    continue

                # Calculate position size
                position_value = current_capital * position_size_pct
                trade_quantity = position_value / float(entry_price)

                # Execute entry
                order_id = str(uuid.uuid4())
                fill_id = str(uuid.uuid4())
                decision_id = str(uuid.uuid4())

                # Map direction to order side enum
                side = "BUY" if (direction or "long").lower() in ["long", "buy"] else "SELL"

                # Create decision record first (required by foreign key)
                db.execute(
                    text("""
                    INSERT INTO decision_records (
                        id, owner_id, actor_type, decision_type, decision_time,
                        rationale, paper_portfolio_id
                    )
                    VALUES (
                        :id, :owner_id, 'human', 'entry', :time,
                        'Paper trading signal', :portfolio_id
                    )
                """),
                    {
                        "id": decision_id,
                        "owner_id": user_id,
                        "time": entry_time,
                        "portfolio_id": run.portfolio_id,
                    },
                )

                db.execute(
                    text("""
                    INSERT INTO paper_orders (
                        id, portfolio_id, decision_id, asset_id, side, quantity,
                        order_type, created_at, status, actor_type
                    )
                    VALUES (
                        :id, :portfolio_id, :decision_id, :asset_id, :side, :quantity,
                        'market', :entry_time, 'filled', 'paper_trading'
                    )
                """),
                    {
                        "id": order_id,
                        "portfolio_id": run.portfolio_id,
                        "decision_id": decision_id,
                        "asset_id": instrument,
                        "side": side,
                        "quantity": trade_quantity,
                        "entry_time": entry_time,
                    },
                )

                db.execute(
                    text("""
                    INSERT INTO paper_fills (
                        id, paper_order_id, execution_date, observed_price,
                        execution_price_before_costs, slippage_amount, commission_amount,
                        quantity, gross_amount, net_cash_change, simulated_fill_reason, bar_timestamp
                    )
                    VALUES (
                        :id, :order_id, :exec_date, :obs_price,
                        :exec_price, 0, 0,
                        :quantity, :gross, :net, 'signal', :timestamp
                    )
                """),
                    {
                        "id": fill_id,
                        "order_id": order_id,
                        "exec_date": entry_time.strftime("%Y-%m-%d"),
                        "obs_price": float(entry_price),
                        "exec_price": float(entry_price),
                        "quantity": trade_quantity,
                        "gross": trade_quantity * float(entry_price),
                        "net": -trade_quantity * float(entry_price),
                        "timestamp": entry_time,
                    },
                )

                positions.append(
                    {
                        "order_id": order_id,
                        "entry_time": entry_time,
                        "exit_time": exit_time,
                        "entry_price": float(entry_price),
                        "exit_price": float(exit_price),
                        "quantity": trade_quantity,
                        "net_pnl": float(net_pnl),
                    }
                )

                # Simulate exit
                current_capital += float(net_pnl)

                # Track daily PnL
                exit_date = exit_time.date() if isinstance(exit_time, datetime) else exit_time
                if exit_date not in daily_pnl:
                    daily_pnl[exit_date] = {"pnl": 0, "trades": 0, "wins": 0}

                daily_pnl[exit_date]["pnl"] += float(net_pnl)
                daily_pnl[exit_date]["trades"] += 1
                if float(net_pnl) > 0:
                    daily_pnl[exit_date]["wins"] += 1

                executed_trades.append(
                    {
                        "entry_time": entry_time.isoformat(),
                        "exit_time": exit_time.isoformat(),
                        "net_pnl": float(net_pnl),
                    }
                )

            # Store daily metrics
            cumulative_pnl = 0
            for trade_date, metrics in sorted(daily_pnl.items()):
                cumulative_pnl += metrics["pnl"]
                win_rate = (metrics["wins"] / metrics["trades"] * 100) if metrics["trades"] > 0 else 0

                db.execute(
                    text("""
                    INSERT INTO paper_trading_daily_metrics (
                        run_id, date, trades_count, wins, losses,
                        daily_pnl, cumulative_pnl, drawdown_pct
                    )
                    VALUES (
                        :run_id, :date, :trades, :wins, :losses,
                        :daily_pnl, :cum_pnl, 0
                    )
                    ON CONFLICT (run_id, date) DO UPDATE SET
                        trades_count = EXCLUDED.trades_count,
                        wins = EXCLUDED.wins,
                        losses = EXCLUDED.losses,
                        daily_pnl = EXCLUDED.daily_pnl,
                        cumulative_pnl = EXCLUDED.cumulative_pnl
                """),
                    {
                        "run_id": run_id,
                        "date": trade_date,
                        "trades": metrics["trades"],
                        "wins": metrics["wins"],
                        "losses": metrics["trades"] - metrics["wins"],
                        "daily_pnl": metrics["pnl"],
                        "cum_pnl": cumulative_pnl,
                    },
                )

            # Update run with final results
            total_trades = len(executed_trades)
            total_pnl = current_capital - initial_capital
            win_rate = (
                sum(1 for t in executed_trades if t["net_pnl"] > 0) / total_trades * 100
                if total_trades > 0
                else 0
            )

            db.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'completed',
                    end_date = CURRENT_DATE,
                    final_capital = :final,
                    total_pnl = :pnl,
                    total_trades = :trades,
                    win_rate = :wr,
                    updated_at = NOW()
                WHERE id = :id
            """),
                {
                    "final": current_capital,
                    "pnl": total_pnl,
                    "trades": total_trades,
                    "wr": win_rate,
                    "id": run_id,
                },
            )

            # Update portfolio
            db.execute(
                text("""
                UPDATE paper_portfolios
                SET current_virtual_cash = :capital, closed_at = NOW()
                WHERE id = :id
            """),
                {"capital": current_capital, "id": run.portfolio_id},
            )

            db.commit()

            logger.info(
                f"Completed historical replay: {total_trades} trades, ${total_pnl:.2f} PnL"
            )

            return {
                "run_id": run_id,
                "status": "completed",
                "total_trades": total_trades,
                "total_pnl": total_pnl,
                "win_rate": win_rate,
                "final_capital": current_capital,
                "executed_trades": executed_trades,
            }

        except Exception as exc:
            db.rollback()
            logger.exception("Historical replay failed")
            raise
        finally:
            db.close()

    def generate_daily_report(self, run_id: str, report_date: Optional[date] = None) -> dict:
        """Generate daily performance report for a run."""
        if report_date is None:
            report_date = date.today()

        db = SessionLocal()
        try:
            # Get run details
            run = db.execute(
                text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id},
            ).fetchone()

            if not run:
                raise ValueError(f"Run {run_id} not found")

            # Get daily metrics
            metrics = db.execute(
                text("""
                SELECT * FROM paper_trading_daily_metrics
                WHERE run_id = :id AND date = :date
            """),
                {"id": run_id, "date": report_date},
            ).fetchone()

            # Get portfolio
            portfolio = db.execute(
                text("SELECT * FROM paper_portfolios WHERE id = :id"),
                {"id": run.portfolio_id},
            ).fetchone()

            report = {
                "date": report_date.isoformat(),
                "run_id": run_id,
                "skill": run.skill,
                "instrument_id": run.instrument_id,
                "portfolio": {
                    "starting_capital": float(run.initial_capital),
                    "current_value": float(portfolio.current_virtual_cash) if portfolio else 0,
                    "daily_pnl": float(metrics.daily_pnl) if metrics else 0,
                    "total_pnl": float(run.total_pnl) if run.total_pnl else 0,
                },
                "trades": {
                    "count": metrics.trades_count if metrics else 0,
                    "wins": metrics.wins if metrics else 0,
                    "losses": metrics.losses if metrics else 0,
                    "win_rate": (metrics.wins / metrics.trades_count * 100) if metrics and metrics.trades_count > 0 else 0,
                },
                "risk": {
                    "max_drawdown_pct": float(run.max_drawdown_pct) if run.max_drawdown_pct else 0,
                    "sharpe_7d": float(metrics.sharpe_7d) if metrics and metrics.sharpe_7d else 0,
                },
            }

            return report

        finally:
            db.close()

    def get_status(self, run_id: str) -> dict:
        """Get current status of a paper trading run."""
        db = SessionLocal()
        try:
            run = db.execute(
                text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id},
            ).fetchone()

            if not run:
                raise ValueError(f"Run {run_id} not found")

            portfolio = db.execute(
                text("SELECT * FROM paper_portfolios WHERE id = :id"),
                {"id": run.portfolio_id},
            ).fetchone()

            return {
                "run_id": run_id,
                "status": run.status,
                "skill": run.skill,
                "instrument": run.instrument_id,
                "initial_capital": float(run.initial_capital),
                "current_capital": float(portfolio.current_virtual_cash) if portfolio else 0,
                "total_pnl": float(run.total_pnl) if run.total_pnl else 0,
                "total_trades": run.total_trades,
                "win_rate": float(run.win_rate) if run.win_rate else 0,
                "start_date": run.start_date.isoformat() if run.start_date else None,
                "end_date": run.end_date.isoformat() if run.end_date else None,
            }

        finally:
            db.close()


# Global engine instance
_engine: Optional[PaperTradingEngine] = None


def get_engine(database_url: str) -> PaperTradingEngine:
    """Get or create the paper trading engine."""
    global _engine
    if _engine is None:
        _engine = PaperTradingEngine(database_url)
    return _engine
