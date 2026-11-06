"""Paper trading execution engine.

Implements real-time paper trading based on backtested strategy signals.
Supports both historical replay (Option A) and live market data (Option B).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, date, timezone
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
                "instrument_id": instrument_id,
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
        """Execute paper trading using historical data replay.

        Event-driven simulation: exits are processed before entries at the
        same timestamp, so released capital is available for new positions
        only after the exit settles.
        """
        if days_back is None or days_back <= 0:
            raise ValueError(f"days_back must be a positive integer, got {days_back!r}")

        db = SessionLocal()
        try:
            # Get run details
            run = db.execute(
                text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id},
            ).fetchone()

            if not run:
                raise ValueError(f"Run {run_id} not found")

            # Idempotency protection: reject re-run if already completed or in progress
            if run.status == "completed":
                raise ValueError(
                    f"Run {run_id} already completed. Cannot re-run historical replay. "
                    f"Create a new run instead."
                )
            if run.status == "running":
                raise ValueError(
                    f"Run {run_id} is currently running. Concurrent replays not allowed."
                )

            skill = run.skill
            instrument = run.instrument_id
            interval = run.interval_minutes
            initial_capital = float(run.initial_capital)

            logger.info(f"Starting historical replay for {skill} on {instrument}")

            # Compute UTC cutoff in Python — never embed bind param in INTERVAL string
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

            # Get historical trades for this skill/instrument/interval/user
            # Use rule_id (stable identifier) instead of rule_label_zh (display name)
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
                  AND t.rule_id = :rule_id
                  AND r.user_id = :user_id
                  AND t.entry_time >= :cutoff
                ORDER BY t.entry_time ASC
            """),
                {
                    "instrument": instrument,
                    "interval": interval,
                    "rule_id": skill,  # skill parameter now contains rule_id, not display name
                    "user_id": user_id,
                    "cutoff": cutoff,
                },
            ).fetchall()

            logger.info(f"Found {len(trades)} historical trades to replay")

            # Mark run as 'running' to prevent concurrent replays
            db.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'running', updated_at = NOW()
                WHERE id = :id
            """),
                {"id": run_id},
            )
            db.commit()

            # Build event list: each trade produces an entry event and an exit event.
            # Events are sorted by timestamp; at the same timestamp exits come first
            # so released capital is available before new entries.
            ENTRY = "entry"
            EXIT = "exit"
            events = []
            for idx, trade in enumerate(trades):
                entry_time, exit_time, entry_price, exit_price, net_pnl, direction, quantity, exit_reason = trade
                events.append((entry_time, ENTRY, idx))
                events.append((exit_time, EXIT, idx))

            # Sort: by timestamp first, then exits before entries at same time
            events.sort(key=lambda e: (e[0], 0 if e[1] == EXIT else 1))

            # Simulation state — credible ledger with separate cash and position tracking
            available_cash = Decimal(str(initial_capital))
            position_value = Decimal("0")  # Total value locked in positions
            active_positions = {}  # idx -> position dict
            executed_trades = []
            daily_pnl = {}
            event_log = []

            # Track all cash flows for reconciliation (trading activities only, not initial deposit)
            cash_flows = []  # List of (time, flow_type, amount) for audit

            for event_time, event_type, idx in events:
                trade = trades[idx]
                entry_time, exit_time, entry_price, exit_price, orig_net_pnl, direction, quantity, exit_reason = trade

                if event_type == EXIT and idx in active_positions:
                    # Process exit: return position value + settle PnL to available cash
                    pos = active_positions.pop(idx)
                    trade_quantity = Decimal(str(pos["quantity"]))
                    is_long = pos["is_long"]
                    entry_price = Decimal(str(pos["entry_price"]))
                    exit_price_dec = Decimal(str(exit_price))

                    # Calculate realized PnL
                    if is_long:
                        realized_pnl = trade_quantity * (exit_price_dec - entry_price)
                    else:
                        realized_pnl = trade_quantity * (entry_price - exit_price_dec)

                    # Return position value to available cash
                    position_cost = trade_quantity * entry_price
                    available_cash += position_cost + realized_pnl
                    position_value -= position_cost
                    
                    cash_flows.append((event_time, "exit_proceeds", position_cost + realized_pnl))

                    # Create exit decision, order, and fill (mirror entry logic)
                    exit_decision_id = str(uuid.uuid4())
                    exit_order_id = str(uuid.uuid4())
                    exit_fill_id = str(uuid.uuid4())
                    exit_side = "SELL" if is_long else "BUY"  # Close position: sell long, buy short

                    # Create exit decision record
                    db.execute(
                        text("""
                        INSERT INTO decision_records (
                            id, owner_id, actor_type, decision_type, decision_time,
                            rationale, paper_portfolio_id
                        )
                        VALUES (
                            :id, :owner_id, 'paper_trading', 'exit', :time,
                            :rationale, :portfolio_id
                        )
                    """),
                        {
                            "id": exit_decision_id,
                            "owner_id": user_id,
                            "time": exit_time,
                            "rationale": f"Exit {exit_reason or 'signal'}: realized PnL ${realized_pnl:.2f}",
                            "portfolio_id": run.portfolio_id,
                        },
                    )

                    # Create exit order
                    db.execute(
                        text("""
                        INSERT INTO paper_orders (
                            id, portfolio_id, decision_id, asset_id, side, quantity,
                            order_type, created_at, status, actor_type
                        )
                        VALUES (
                            :id, :portfolio_id, :decision_id, :asset_id, :side, :quantity,
                            'market', :exit_time, 'filled', 'paper_trading'
                        )
                    """),
                        {
                            "id": exit_order_id,
                            "portfolio_id": run.portfolio_id,
                            "decision_id": exit_decision_id,
                            "asset_id": instrument,
                            "side": exit_side,
                            "quantity": float(trade_quantity),
                            "exit_time": exit_time,
                        },
                    )

                    # Create exit fill
                    # For exits: gross_amount is the proceeds (positive for cash inflow)
                    # net_cash_change = gross_amount - commission - slippage
                    exit_gross = float(trade_quantity) * float(exit_price)
                    exit_commission = 0.0  # TODO: implement commission model
                    exit_slippage = 0.0  # TODO: implement slippage model
                    exit_net = exit_gross - exit_commission - exit_slippage

                    db.execute(
                        text("""
                        INSERT INTO paper_fills (
                            id, paper_order_id, execution_date, observed_price,
                            execution_price_before_costs, slippage_amount, commission_amount,
                            quantity, gross_amount, net_cash_change, simulated_fill_reason, bar_timestamp
                        )
                        VALUES (
                            :id, :order_id, :exec_date, :obs_price,
                            :exec_price, :slippage, :commission,
                            :quantity, :gross, :net, 'exit_signal', :timestamp
                        )
                    """),
                        {
                            "id": exit_fill_id,
                            "order_id": exit_order_id,
                            "exec_date": exit_time.strftime("%Y-%m-%d"),
                            "obs_price": float(exit_price),
                            "exec_price": float(exit_price),
                            "slippage": exit_slippage,
                            "commission": exit_commission,
                            "quantity": float(trade_quantity),
                            "gross": exit_gross,
                            "net": exit_net,
                            "timestamp": exit_time,
                        },
                    )

                    # Update PaperPosition: close position and record realized PnL
                    db.execute(
                        text("""
                        UPDATE paper_positions
                        SET closed_at = :closed_at,
                            realized_pnl = :realized_pnl,
                            status = 'closed'
                        WHERE id = :position_id
                    """),
                        {
                            "closed_at": exit_time,
                            "realized_pnl": realized_pnl,
                            "position_id": pos["position_id"],
                        },
                    )

                    # Update PaperPortfolio: adjust current_virtual_cash
                    # Cash change = exit proceeds (gross) - entry cost (already deducted)
                    # We'll update the DB at the end of replay (batch update)

                    exit_date = exit_time.date() if isinstance(exit_time, datetime) else exit_time
                    if exit_date not in daily_pnl:
                        daily_pnl[exit_date] = {"pnl": 0, "trades": 0, "wins": 0}
                    daily_pnl[exit_date]["pnl"] += realized_pnl
                    daily_pnl[exit_date]["trades"] += 1
                    if realized_pnl > 0:
                        daily_pnl[exit_date]["wins"] += 1

                    executed_trades.append({
                        "entry_time": pos["entry_time"].isoformat(),
                        "exit_time": exit_time.isoformat(),
                        "net_pnl": realized_pnl,
                    })

                    event_log.append({
                        "time": event_time.isoformat(),
                        "event": "exit",
                        "trade_idx": idx,
                        "active_positions": len(active_positions),
                        "available_cash": float(available_cash),
                        "position_value": float(position_value),
                        "realized_pnl": float(realized_pnl),
                    })

                elif event_type == ENTRY and idx not in active_positions:
                    # Process entry: check capacity and exposure limits
                    total_equity = available_cash + position_value
                    
                    if len(active_positions) >= max_positions:
                        event_log.append({
                            "time": event_time.isoformat(),
                            "event": "entry_blocked",
                            "trade_idx": idx,
                            "active_positions": len(active_positions),
                            "equity": float(total_equity),
                            "reason": "max_positions",
                        })
                        continue

                    # Exposure limit checks (engineering defaults for isolated simulation)
                    # Check equity > 0
                    if total_equity <= 0:
                        event_log.append({
                            "time": event_time.isoformat(),
                            "event": "entry_blocked",
                            "trade_idx": idx,
                            "equity": float(total_equity),
                            "reason": "equity_non_positive",
                        })
                        continue

                    # Check price is valid
                    if float(entry_price) <= 0:
                        event_log.append({
                            "time": event_time.isoformat(),
                            "event": "entry_blocked",
                            "trade_idx": idx,
                            "price": float(entry_price),
                            "reason": "invalid_price",
                        })
                        continue

                    # Check available cash is sufficient
                    proposed_position_value = float(total_equity) * position_size_pct
                    if Decimal(str(proposed_position_value)) > available_cash:
                        event_log.append({
                            "time": event_time.isoformat(),
                            "event": "entry_blocked",
                            "trade_idx": idx,
                            "proposed_value": proposed_position_value,
                            "available_cash": float(available_cash),
                            "reason": "insufficient_cash",
                        })
                        continue

                    # Calculate current gross exposure (sum of absolute position values)
                    current_exposure = sum(
                        abs(Decimal(str(pos["quantity"])) * Decimal(str(pos["entry_price"])))
                        for pos in active_positions.values()
                    )

                    proposed_exposure = current_exposure + Decimal(str(proposed_position_value))

                    # Check single asset exposure limit (10% of equity)
                    single_asset_limit = total_equity * Decimal("0.10")
                    if Decimal(str(proposed_position_value)) > single_asset_limit:
                        event_log.append({
                            "time": event_time.isoformat(),
                            "event": "entry_blocked",
                            "trade_idx": idx,
                            "proposed_value": proposed_position_value,
                            "limit": float(single_asset_limit),
                            "reason": "single_asset_exposure_limit",
                        })
                        continue

                    # Check total gross exposure limit (100% of equity)
                    total_exposure_limit = total_equity
                    if proposed_exposure > total_exposure_limit:
                        event_log.append({
                            "time": event_time.isoformat(),
                            "event": "entry_blocked",
                            "trade_idx": idx,
                            "current_exposure": float(current_exposure),
                            "proposed_exposure": float(proposed_exposure),
                            "limit": float(total_exposure_limit),
                            "reason": "total_exposure_limit",
                        })
                        continue

                    # All checks passed, proceed with entry
                    entry_position_value = Decimal(str(proposed_position_value))
                    entry_price_dec = Decimal(str(entry_price))
                    trade_quantity = entry_position_value / entry_price_dec
                    is_long = (direction or "long").lower() in ["long", "buy"]
                    side = "BUY" if is_long else "SELL"

                    # Deduct position cost from available cash
                    available_cash -= entry_position_value
                    position_value += entry_position_value
                    cash_flows.append((event_time, "entry_cost", -entry_position_value))

                    order_id = str(uuid.uuid4())
                    fill_id = str(uuid.uuid4())
                    decision_id = str(uuid.uuid4())

                    # Create decision record
                    db.execute(
                        text("""
                        INSERT INTO decision_records (
                            id, owner_id, actor_type, decision_type, decision_time,
                            rationale, paper_portfolio_id
                        )
                        VALUES (
                            :id, :owner_id, 'paper_trading', 'entry', :time,
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
                            "quantity": float(trade_quantity),
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
                            "quantity": float(trade_quantity),
                            "gross": float(trade_quantity) * float(entry_price),
                            "net": -float(trade_quantity) * float(entry_price),
                            "timestamp": entry_time,
                        },
                    )

                    # Create PaperPosition record in DB
                    position_id = str(uuid.uuid4())
                    db.execute(
                        text("""
                        INSERT INTO paper_positions (
                            id, portfolio_id, asset_id, quantity, average_entry_price,
                            opened_at, status
                        )
                        VALUES (
                            :id, :portfolio_id, :asset_id, :quantity, :entry_price,
                            :opened_at, 'open'
                        )
                    """),
                        {
                            "id": position_id,
                            "portfolio_id": run.portfolio_id,
                            "asset_id": instrument,
                            "quantity": float(trade_quantity) if is_long else -float(trade_quantity),
                            "entry_price": float(entry_price),
                            "opened_at": entry_time,
                        },
                    )

                    active_positions[idx] = {
                        "position_id": position_id,
                        "order_id": order_id,
                        "entry_time": entry_time,
                        "exit_time": exit_time,
                        "entry_price": float(entry_price_dec),
                        "exit_price": float(exit_price),
                        "quantity": float(trade_quantity),
                        "is_long": is_long,
                    }

                    event_log.append({
                        "time": event_time.isoformat(),
                        "event": "entry",
                        "trade_idx": idx,
                        "active_positions": len(active_positions),
                        "available_cash": float(available_cash),
                        "position_value": float(position_value),
                        "quantity": float(trade_quantity),
                    })

            # Close out any positions still open at end (shouldn't happen with complete trade data)
            for idx, pos in list(active_positions.items()):
                logger.warning(f"Position {idx} still open at end of replay — forcing close")
                trade = trades[idx]
                exit_time = trade[1]
                exit_price = Decimal(str(pos["exit_price"]))
                trade_quantity = Decimal(str(pos["quantity"]))
                entry_price = Decimal(str(pos["entry_price"]))
                
                if pos["is_long"]:
                    realized_pnl = trade_quantity * (exit_price - entry_price)
                else:
                    realized_pnl = trade_quantity * (entry_price - exit_price)
                
                # Return position value + PnL to available cash
                position_cost = trade_quantity * entry_price
                available_cash += position_cost + realized_pnl
                position_value -= position_cost
                
                cash_flows.append((exit_time, "forced_exit_proceeds", position_cost + realized_pnl))
                
                executed_trades.append({
                    "entry_time": pos["entry_time"].isoformat(),
                    "exit_time": exit_time.isoformat(),
                    "net_pnl": float(realized_pnl),
                })
            active_positions.clear()

            # Reconciliation check: beginning cash + net flows = ending cash
            total_equity = available_cash + position_value
            net_cash_flows = sum(flow[2] for flow in cash_flows)
            expected_equity = Decimal(str(initial_capital)) + net_cash_flows
            
            # Allow small floating point tolerance
            reconciliation_diff = abs(total_equity - expected_equity)
            if reconciliation_diff > Decimal("0.01"):
                logger.error(
                    f"RECONCILIATION FAILED: equity={total_equity}, expected={expected_equity}, "
                    f"diff={reconciliation_diff}, flows={cash_flows}"
                )
                raise ValueError(
                    f"Cash reconciliation failed: equity {total_equity} != expected {expected_equity} "
                    f"(diff: {reconciliation_diff})"
                )
            
            logger.info(f"✓ Cash reconciliation passed: equity={total_equity}, diff={reconciliation_diff}")

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
            total_pnl = total_equity - Decimal(str(initial_capital))
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
                    "final": float(total_equity),
                    "pnl": float(total_pnl),
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
                {"capital": float(total_equity), "id": run.portfolio_id},
            )

            db.commit()

            logger.info(
                f"Completed historical replay: {total_trades} trades, ${total_pnl:.2f} PnL"
            )

            return {
                "run_id": run_id,
                "status": "completed",
                "total_trades": total_trades,
                "total_pnl": float(total_pnl),
                "win_rate": win_rate,
                "final_capital": float(total_equity),
                "reconciliation_status": "passed",
                "reconciliation_diff": float(reconciliation_diff),
                "executed_trades": executed_trades,
                "event_log": event_log,
            }

        except Exception as exc:
            db.rollback()
            # Mark run as 'failed' to allow retry
            try:
                db.execute(
                    text("""
                    UPDATE paper_trading_runs
                    SET status = 'failed', updated_at = NOW()
                    WHERE id = :id
                """),
                    {"id": run_id},
                )
                db.commit()
            except Exception:
                logger.exception("Failed to update run status to 'failed'")
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
                "instrument_id": run.instrument_id,
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
