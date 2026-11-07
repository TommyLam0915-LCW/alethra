"""Paper trading execution engine.

Implements real-time paper trading based on backtested strategy signals.
Supports both historical replay (Option A) and live market data (Option B).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, date, timezone
from decimal import Decimal
from typing import Optional, Callable

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.database import SessionLocal
from app.services.paper_trading_exceptions import (
    AuthorizationError,
    ResourceNotFoundError,
    InvalidStateError,
)

logger = logging.getLogger(__name__)


class PaperTradingEngine:
    """Executes paper trades based on strategy signals."""

    def __init__(
        self,
        database_url: Optional[str] = None,
        session_factory: Optional[Callable[[], Session]] = None,
    ):
        """Initialize engine with real dependency injection.
        
        Args:
            database_url: Database URL (legacy, creates default sessionmaker)
            session_factory: Callable that returns Session instances (preferred)
        
        If session_factory is provided, it takes precedence over database_url.
        If neither is provided, falls back to global SessionLocal (not recommended).
        """
        if session_factory is not None:
            self.session_factory = session_factory
        elif database_url is not None:
            # Legacy path: create sessionmaker from URL
            engine = create_engine(database_url)
            self.session_factory = sessionmaker(bind=engine)
        else:
            # Fallback to global (should not be used in tests)
            logger.warning("PaperTradingEngine using global SessionLocal - not isolated")
            self.session_factory = SessionLocal
        
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
        db = self.session_factory()
        try:
            # Create portfolio
            portfolio_id = str(uuid.uuid4())
            portfolio_name = f"Live Paper Test - {skill} - {instrument_id}"

            db.execute(
                text("""
                INSERT INTO paper_portfolios (id, owner_id, currency, initial_virtual_cash, current_virtual_cash, status, created_at)
                VALUES (:id, :owner_id, 'USD', :initial, :current, 'active', CURRENT_TIMESTAMP)
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
                    CURRENT_TIMESTAMP, 'queued', :initial, 0, 0
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
                "status": "queued",
            }

        except Exception as exc:
            db.rollback()
            logger.exception("Failed to create portfolio")
            raise
        finally:
            db.close()

    def check_cancel_requested(self, run_id: str) -> bool:
        """Check if cancellation has been requested for a run.

        Workers should call this periodically during long-running operations
        to detect cancel requests and abort gracefully.

        Args:
            run_id: The run ID to check

        Returns:
            True if cancel was requested, False otherwise
        """
        db = self.session_factory()
        try:
            result = db.execute(
                text("""
                SELECT cancel_requested, status
                FROM paper_trading_runs
                WHERE id = :id
            """),
                {"id": run_id},
            ).fetchone()

            if not result:
                return False

            cancel_requested, status = result

            # Cancel is effective if flag is set OR status is already cancelled
            return bool(cancel_requested) or status == "cancelled"

        finally:
            db.close()

    def execute_historical_replay(
        self,
        run_id: str,
        user_id: str,
        days_back: int = 30,
        position_size_pct: float = 0.10,
        max_positions: int = 3,
        commission_rate: Decimal = Decimal("0.001"),  # 0.1% commission
        slippage_rate: Decimal = Decimal("0.0005"),   # 0.05% slippage
    ) -> dict:
        """Execute paper trading using historical data replay.

        Event-driven simulation: exits are processed before entries at the
        same timestamp, so released capital is available for new positions
        only after the exit settles.

        Args:
            run_id: The paper trading run ID
            user_id: The user ID (must match run owner)
            days_back: Number of days to look back for historical trades
            position_size_pct: Position size as percentage of equity (e.g., 0.10 = 10%)
            max_positions: Maximum number of concurrent positions
            commission_rate: Commission rate as decimal (e.g., 0.001 = 0.1%)
            slippage_rate: Slippage rate as decimal (e.g., 0.0005 = 0.05%)
        """
        if days_back is None or days_back <= 0:
            raise ValueError(f"days_back must be a positive integer, got {days_back!r}")

        # Validate commission and slippage rates are non-negative Decimals
        if not isinstance(commission_rate, Decimal) or commission_rate < 0:
            raise ValueError(f"commission_rate must be a non-negative Decimal, got {commission_rate!r}")
        if not isinstance(slippage_rate, Decimal) or slippage_rate < 0:
            raise ValueError(f"slippage_rate must be a non-negative Decimal, got {slippage_rate!r}")

        db = self.session_factory()
        acquired_execution = False
        try:
            # Get run details
            run = db.execute(
                text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id},
            ).fetchone()

            if not run:
                raise ResourceNotFoundError("Run", run_id)

            # Cross-user rejection: validate ownership via portfolio
            portfolio = db.execute(
                text("SELECT owner_id FROM paper_portfolios WHERE id = :id"),
                {"id": run.portfolio_id},
            ).fetchone()

            if not portfolio or str(portfolio.owner_id) != user_id:
                owner_id = str(portfolio.owner_id) if portfolio else "unknown"
                raise AuthorizationError(user_id, "run", run_id, owner_id)

            # Idempotency protection: reject re-run if already completed or cancelled
            if run.status in ("completed", "cancelled"):
                raise InvalidStateError("Run", run_id, run.status, ["queued", "pending", "failed"])

            skill = run.skill
            instrument = run.instrument_id
            interval = run.interval_minutes
            initial_capital = float(run.initial_capital)

            logger.info(f"Starting historical replay for {skill} on {instrument}")

            # Compute UTC cutoff in Python — never embed bind param in INTERVAL string
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

            # Set as_of BEFORE query: only replay trades fully completed before this timestamp
            as_of = datetime.now(timezone.utc)

            # Get historical trades for this skill/instrument/interval/user
            # Use rule_id (stable identifier) instead of rule_label_zh (display name)
            # Include source pinning fields: trade_id, run_id, rule_id, exit_id, data_version
            # Constrain by as_of: only trades where exit_time <= as_of (completed before replay start)
            trades = db.execute(
                text("""
                SELECT
                    t.id as source_trade_id,
                    t.entry_time,
                    t.exit_time,
                    t.entry_price,
                    t.exit_price,
                    t.net_pnl,
                    t.direction,
                    t.quantity,
                    t.exit_reason,
                    t.run_id as source_run_id,
                    t.rule_id as source_rule_id,
                    t.exit_id as source_exit_id,
                    r.research_version as source_data_version
                FROM strategy_study_trades t
                JOIN strategy_study_runs r ON t.run_id = r.id
                WHERE r.instrument_id = :instrument
                  AND r.interval_minutes = :interval
                  AND t.rule_id = :rule_id
                  AND r.user_id = :user_id
                  AND t.entry_time >= :cutoff
                  AND t.exit_time <= :as_of
                ORDER BY t.entry_time ASC
            """),
                {
                    "instrument": instrument,
                    "interval": interval,
                    "rule_id": skill,  # skill parameter now contains rule_id, not display name
                    "user_id": user_id,
                    "cutoff": cutoff,
                    "as_of": as_of,
                },
            ).fetchall()

            logger.info(f"Found {len(trades)} historical trades to replay")

            # Long-only enforcement: reject any short positions
            for trade in trades:
                direction = trade.direction or "long"
                if direction.lower() not in ["long", "buy"]:
                    raise ValueError(
                        f"Long-only mode: cannot replay short trade {trade.source_trade_id} "
                        f"with direction={direction!r}. Paper trading currently supports long positions only."
                    )

            # Atomic conditional UPDATE: only acquire execution rights if status allows
            # AND cancel has not been requested. PostgreSQL re-checks WHERE condition on
            # concurrent updates, so this prevents race between cancel and execution acquisition.
            result = db.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'running', updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND status IN ('queued', 'pending', 'failed')
                  AND cancel_requested = FALSE
                RETURNING id
            """),
                {"id": run_id},
            )
            acquired_row = result.fetchone()

            if not acquired_row:
                # Another task acquired execution rights, or status is not allowed
                raise InvalidStateError(
                    "Run", run_id, run.status,
                    ["queued", "pending", "failed"]
                )

            acquired_execution = True
            db.commit()

            # Build event list: each trade produces an entry event and an exit event.
            # Events are sorted by timestamp; at the same timestamp exits come first
            # so released capital is available before new entries.
            ENTRY = "entry"
            EXIT = "exit"
            events = []
            for idx, trade in enumerate(trades):
                # trade tuple: (source_trade_id, entry_time, exit_time, entry_price, exit_price,
                #               net_pnl, direction, quantity, exit_reason, source_run_id,
                #               source_rule_id, source_exit_id, source_data_version)
                entry_time = trade.entry_time
                exit_time = trade.exit_time
                events.append((entry_time, ENTRY, idx))
                events.append((exit_time, EXIT, idx))

            # Sort: by timestamp first, then exits before entries at same time
            events.sort(key=lambda e: (e[0], 0 if e[1] == EXIT else 1))

            # Simulation state — credible ledger with separate cash and position tracking
            # All calculations use Decimal for precision
            available_cash = Decimal(str(initial_capital))
            position_value = Decimal("0")  # Total value locked in positions
            active_positions = {}  # idx -> position dict
            executed_trades = []
            daily_pnl = {}
            event_log = []

            # Track all cash flows for reconciliation (trading activities only, not initial deposit)
            cash_flows = []  # List of (time, flow_type, amount) for audit

            # Cancel check counter - check every 100 events to avoid excessive DB queries
            event_counter = 0
            CANCEL_CHECK_INTERVAL = 100

            for event_time, event_type, idx in events:
                # Periodic cancel check
                event_counter += 1
                if event_counter % CANCEL_CHECK_INTERVAL == 0:
                    if self.check_cancel_requested(run_id):
                        logger.info(f"Cancel requested detected at event {event_counter}, stopping worker")
                        # Roll back any uncommitted work
                        db.rollback()
                        # Worker sets status='cancelled' after confirming it stopped
                        # This is the atomic transition from "cancel requested" to "cancel completed"
                        db.execute(
                            text("""
                            UPDATE paper_trading_runs
                            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id AND status = 'running'
                        """),
                            {"id": run_id},
                        )
                        db.commit()
                        return {
                            "run_id": run_id,
                            "status": "cancelled",
                            "message": "Worker stopped due to cancel request",
                            "events_processed": event_counter,
                        }

                trade = trades[idx]
                # Unpack trade tuple with source pinning fields
                (source_trade_id, entry_time, exit_time, entry_price, exit_price,
                 orig_net_pnl, direction, quantity, exit_reason, source_run_id,
                 source_rule_id, source_exit_id, source_data_version) = trade

                # Convert prices and quantity to Decimal for precision
                entry_price_dec = Decimal(str(entry_price))
                exit_price_dec = Decimal(str(exit_price))
                trade_quantity_dec = Decimal(str(quantity))

                if event_type == EXIT and idx in active_positions:
                    # Process exit: return position value + settle PnL to available cash
                    pos = active_positions.pop(idx)
                    trade_quantity = Decimal(str(pos["quantity"]))
                    is_long = pos["is_long"]
                    entry_price = Decimal(str(pos["entry_price"]))
                    exit_price_dec = Decimal(str(exit_price))
                    # Retrieve entry costs stored at entry time for full round-trip accounting
                    entry_commission_stored = Decimal(str(pos["entry_commission"]))
                    entry_slippage_stored = Decimal(str(pos["entry_slippage"]))
                    entry_total_costs_stored = Decimal(str(pos["entry_total_costs"]))

                    # Calculate realized PnL using Decimal precision (gross, before any costs)
                    if is_long:
                        realized_pnl = trade_quantity * (exit_price_dec - entry_price)
                    else:
                        realized_pnl = trade_quantity * (entry_price - exit_price_dec)

                    # Apply exit commission and slippage
                    exit_gross_dec = trade_quantity * exit_price_dec
                    exit_commission = exit_gross_dec * commission_rate
                    exit_slippage = exit_gross_dec * slippage_rate
                    exit_total_costs = exit_commission + exit_slippage

                    # Full round-trip net PnL: gross - entry costs - exit costs
                    full_net_pnl = realized_pnl - entry_total_costs_stored - exit_total_costs

                    # Return position value + net PnL to available cash.
                    # Entry costs were already deducted from cash at entry time;
                    # exit costs are deducted here from the gross proceeds.
                    position_cost = trade_quantity * entry_price
                    available_cash += position_cost + realized_pnl - exit_total_costs
                    position_value -= position_cost

                    # Track cash flows: gross proceeds, then separate exit costs (for audit)
                    cash_flows.append((event_time, "exit_proceeds_gross", position_cost + realized_pnl))
                    cash_flows.append((event_time, "exit_commission", -exit_commission))
                    cash_flows.append((event_time, "exit_slippage", -exit_slippage))

                    # Create exit decision, order, and fill (mirror entry logic)
                    exit_decision_id = str(uuid.uuid4())
                    exit_order_id = str(uuid.uuid4())
                    exit_fill_id = str(uuid.uuid4())
                    exit_side = "SELL" if is_long else "BUY"  # Close position: sell long, buy short

                    # Create exit decision record with source pinning
                    db.execute(
                        text("""
                        INSERT INTO decision_records (
                            id, owner_id, actor_type, decision_type, decision_time,
                            rationale, paper_portfolio_id, source_trade_id, source_run_id,
                            source_rule_id, source_exit_config, source_data_version,
                            cutoff_timestamp, as_of_timestamp
                        )
                        VALUES (
                            :id, :owner_id, 'system', 'exit', :time,
                            :rationale, :portfolio_id, :source_trade_id, :source_run_id,
                            :source_rule_id, :source_exit_config, :source_data_version,
                            :cutoff, :as_of
                        )
                    """),
                        {
                            "id": exit_decision_id,
                            "owner_id": user_id,
                            "time": exit_time,
                            "rationale": f"Exit {exit_reason or 'signal'}: gross PnL ${realized_pnl:.2f}, net PnL ${full_net_pnl:.2f}",
                            "portfolio_id": run.portfolio_id,
                            "source_trade_id": source_trade_id,
                            "source_run_id": source_run_id,
                            "source_rule_id": source_rule_id,
                            "source_exit_config": source_exit_id,
                            "source_data_version": source_data_version,
                            "cutoff": cutoff,
                            "as_of": as_of,
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

                    # Create exit fill with non-zero costs
                    # For exits: gross_amount is the proceeds (positive for cash inflow)
                    # net_cash_change = gross_amount - commission - slippage
                    exit_gross = trade_quantity * exit_price_dec
                    exit_commission = exit_gross * commission_rate
                    exit_slippage = exit_gross * slippage_rate
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
                            "obs_price": exit_price_dec,
                            "exec_price": exit_price_dec,
                            "slippage": exit_slippage,
                            "commission": exit_commission,
                            "quantity": trade_quantity,
                            "gross": exit_gross,
                            "net": exit_net,
                            "timestamp": exit_time,
                        },
                    )

                    # Update PaperPosition: close position and record full round-trip realized PnL
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
                            "realized_pnl": full_net_pnl,
                            "position_id": pos["position_id"],
                        },
                    )

                    # Update PaperPortfolio: adjust current_virtual_cash
                    # Cash change = exit proceeds (net) - entry cost (already deducted)
                    # We'll update the DB at the end of replay (batch update)

                    exit_date = exit_time.date() if isinstance(exit_time, datetime) else exit_time
                    if exit_date not in daily_pnl:
                        daily_pnl[exit_date] = {"pnl": 0, "trades": 0, "wins": 0}
                    daily_pnl[exit_date]["pnl"] += full_net_pnl
                    daily_pnl[exit_date]["trades"] += 1
                    if full_net_pnl > 0:
                        daily_pnl[exit_date]["wins"] += 1

                    executed_trades.append({
                        "entry_time": pos["entry_time"].isoformat(),
                        "exit_time": exit_time.isoformat(),
                        "gross_pnl": float(realized_pnl),
                        "entry_commission": float(entry_commission_stored),
                        "entry_slippage": float(entry_slippage_stored),
                        "exit_commission": float(exit_commission),
                        "exit_slippage": float(exit_slippage),
                        "net_pnl": float(full_net_pnl),
                    })

                    event_log.append({
                        "time": event_time.isoformat(),
                        "event": "exit",
                        "trade_idx": idx,
                        "active_positions": len(active_positions),
                        "available_cash": float(available_cash),
                        "position_value": float(position_value),
                        "gross_pnl": float(realized_pnl),
                        "net_pnl": float(full_net_pnl),
                        "entry_costs": float(entry_total_costs_stored),
                        "exit_costs": float(exit_total_costs),
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
                    proposed_position_value = total_equity * Decimal(str(position_size_pct))
                    if proposed_position_value > available_cash:
                        event_log.append({
                            "time": event_time.isoformat(),
                            "event": "entry_blocked",
                            "trade_idx": idx,
                            "proposed_value": float(proposed_position_value),
                            "available_cash": float(available_cash),
                            "reason": "insufficient_cash",
                        })
                        continue

                    # Calculate current gross exposure (sum of absolute position values)
                    current_exposure = sum(
                        abs(Decimal(str(pos["quantity"])) * Decimal(str(pos["entry_price"])))
                        for pos in active_positions.values()
                    )

                    proposed_exposure = current_exposure + proposed_position_value

                    # Check single asset exposure limit (10% of equity)
                    single_asset_limit = total_equity * Decimal("0.10")
                    if proposed_position_value > single_asset_limit:
                        event_log.append({
                            "time": event_time.isoformat(),
                            "event": "entry_blocked",
                            "trade_idx": idx,
                            "proposed_value": float(proposed_position_value),
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
                    entry_position_value = proposed_position_value
                    entry_price_dec = Decimal(str(entry_price))
                    trade_quantity = entry_position_value / entry_price_dec
                    is_long = (direction or "long").lower() in ["long", "buy"]
                    side = "BUY" if is_long else "SELL"

                    # Apply entry commission and slippage (non-zero costs)
                    entry_commission = entry_position_value * commission_rate
                    entry_slippage = entry_position_value * slippage_rate
                    entry_total_costs = entry_commission + entry_slippage

                    # Deduct position cost and entry costs from available cash
                    available_cash -= entry_position_value
                    available_cash -= entry_total_costs
                    position_value += entry_position_value
                    cash_flows.append((event_time, "entry_cost", -entry_position_value))
                    cash_flows.append((event_time, "entry_commission", -entry_commission))
                    cash_flows.append((event_time, "entry_slippage", -entry_slippage))

                    order_id = str(uuid.uuid4())
                    fill_id = str(uuid.uuid4())
                    decision_id = str(uuid.uuid4())

                    # Create decision record with source pinning
                    db.execute(
                        text("""
                        INSERT INTO decision_records (
                            id, owner_id, actor_type, decision_type, decision_time,
                            rationale, paper_portfolio_id, source_trade_id, source_run_id,
                            source_rule_id, source_exit_config, source_data_version,
                            cutoff_timestamp, as_of_timestamp
                        )
                        VALUES (
                            :id, :owner_id, 'system', 'entry', :time,
                            :rationale, :portfolio_id, :source_trade_id, :source_run_id,
                            :source_rule_id, :source_exit_config, :source_data_version,
                            :cutoff, :as_of
                        )
                    """),
                        {
                            "id": decision_id,
                            "owner_id": user_id,
                            "time": entry_time,
                            "rationale": f"Paper trading entry from source trade {source_trade_id}",
                            "portfolio_id": run.portfolio_id,
                            "source_trade_id": source_trade_id,
                            "source_run_id": source_run_id,
                            "source_rule_id": source_rule_id,
                            "source_exit_config": source_exit_id,
                            "source_data_version": source_data_version,
                            "cutoff": cutoff,
                            "as_of": as_of,
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
                            :exec_price, :slippage, :commission,
                            :quantity, :gross, :net, 'entry_signal', :timestamp
                        )
                    """),
                        {
                            "id": fill_id,
                            "order_id": order_id,
                            "exec_date": entry_time.strftime("%Y-%m-%d"),
                            "obs_price": entry_price_dec,
                            "exec_price": entry_price_dec,
                            "slippage": entry_slippage,
                            "commission": entry_commission,
                            "quantity": trade_quantity,
                            "gross": entry_position_value,
                            "net": -(entry_position_value + entry_total_costs),
                            "timestamp": entry_time,
                        },
                    )

                    # Create PaperPosition record in DB with Decimal precision
                    position_id = str(uuid.uuid4())
                    db.execute(
                        text("""
                        INSERT INTO paper_positions (
                            id, portfolio_id, asset_id, quantity, average_entry_price,
                            opened_at, status, source_trade_id
                        )
                        VALUES (
                            :id, :portfolio_id, :asset_id, :quantity, :entry_price,
                            :opened_at, 'open', :source_trade_id
                        )
                    """),
                        {
                            "id": position_id,
                            "portfolio_id": run.portfolio_id,
                            "asset_id": instrument,
                            "quantity": trade_quantity,
                            "entry_price": entry_price_dec,
                            "opened_at": entry_time,
                            "source_trade_id": source_trade_id,
                        },
                    )

                    active_positions[idx] = {
                        "position_id": position_id,
                        "order_id": order_id,
                        "entry_time": entry_time,
                        "exit_time": exit_time,
                        "entry_price": entry_price_dec,
                        "exit_price": exit_price_dec,
                        "quantity": trade_quantity,
                        "is_long": is_long,
                        "source_trade_id": source_trade_id,
                        "entry_commission": entry_commission,
                        "entry_slippage": entry_slippage,
                        "entry_total_costs": entry_total_costs,
                    }

                    event_log.append({
                        "time": event_time.isoformat(),
                        "event": "entry",
                        "trade_idx": idx,
                        "active_positions": len(active_positions),
                        "available_cash": float(available_cash),
                        "position_value": float(position_value),
                        "quantity": float(trade_quantity),
                        "commission": float(entry_commission),
                        "slippage": float(entry_slippage),
                    })

            # Close out any positions still open at end (shouldn't happen with complete trade data)
            for idx, pos in list(active_positions.items()):
                logger.warning(f"Position {idx} still open at end of replay — forcing close")
                trade = trades[idx]
                exit_time = trade[1]
                exit_price = Decimal(str(pos["exit_price"]))
                trade_quantity = Decimal(str(pos["quantity"]))
                entry_price = Decimal(str(pos["entry_price"]))
                entry_total_costs_stored = Decimal(str(pos["entry_total_costs"]))

                if pos["is_long"]:
                    realized_pnl = trade_quantity * (exit_price - entry_price)
                else:
                    realized_pnl = trade_quantity * (entry_price - exit_price)

                # Full net PnL: gross - entry costs (no exit costs applied for forced close)
                forced_net_pnl = realized_pnl - entry_total_costs_stored

                # Return position value + gross PnL to available cash
                # (entry costs were already deducted at entry time)
                position_cost = trade_quantity * entry_price
                available_cash += position_cost + realized_pnl
                position_value -= position_cost

                cash_flows.append((exit_time, "forced_exit_proceeds", position_cost + realized_pnl))

                executed_trades.append({
                    "entry_time": pos["entry_time"].isoformat(),
                    "exit_time": exit_time.isoformat(),
                    "gross_pnl": float(realized_pnl),
                    "entry_costs": float(entry_total_costs_stored),
                    "net_pnl": float(forced_net_pnl),
                    "forced": True,
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

            # Final commit protection: lock the run row to prevent cancel interference
            # SELECT FOR UPDATE ensures no concurrent cancel can change status during commit
            locked_run = db.execute(
                text("""
                SELECT id, status, cancel_requested
                FROM paper_trading_runs
                WHERE id = :id
                FOR UPDATE
            """),
                {"id": run_id},
            ).fetchone()

            # Check if cancel was requested while we were processing
            if locked_run and locked_run.cancel_requested:
                logger.info(f"Cancel requested detected during final commit, worker stopping")
                db.rollback()
                # Worker sets status='cancelled' after confirming it stopped
                db.execute(
                    text("""
                    UPDATE paper_trading_runs
                    SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id AND status = 'running'
                """),
                    {"id": run_id},
                )
                db.commit()
                return {
                    "run_id": run_id,
                    "status": "cancelled",
                    "message": "Worker stopped during finalization due to cancel request",
                    "events_processed": len(events),
                }

            # Store daily metrics
            cumulative_pnl = 0
            for trade_date, metrics in sorted(daily_pnl.items()):
                cumulative_pnl += metrics["pnl"]
                win_rate = (metrics["wins"] / metrics["trades"] * 100) if metrics["trades"] > 0 else 0

                db.execute(
                    text("""
                    INSERT INTO paper_trading_daily_metrics (
                        id, run_id, date, trades_count, wins, losses,
                        daily_pnl, cumulative_pnl, drawdown_pct
                    )
                    VALUES (
                        :metric_id, :run_id, :date, :trades, :wins, :losses,
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
                        "metric_id": str(uuid.uuid4()),
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

            # Final completion: only set status='completed' if cancel was NOT requested.
            # If cancel was requested, worker already set status='cancelled' in the cancel check block.
            # This prevents completion handler from overriding cancel status.
            db.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'completed',
                    end_date = CURRENT_DATE,
                    final_capital = :final,
                    total_pnl = :pnl,
                    total_trades = :trades,
                    win_rate = :wr,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND cancel_requested = FALSE
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
                SET current_virtual_cash = :capital, closed_at = CURRENT_TIMESTAMP
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
            # Only mark run as 'failed' if we acquired execution rights AND cancel was NOT requested.
            # If cancel was requested, worker already set status='cancelled' in the cancel check block.
            # This prevents failure handler from overriding cancel status.
            if acquired_execution:
                try:
                    db.execute(
                        text("""
                        UPDATE paper_trading_runs
                        SET status = 'failed', updated_at = CURRENT_TIMESTAMP
                        WHERE id = :id AND cancel_requested = FALSE
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

    def cancel_run(self, run_id: str, user_id: str) -> dict:
        """Request cancellation of a paper trading run.

        Cancel contract: Sets cancel_requested flag atomically.
        Worker detects flag and stops gracefully, then sets status='cancelled'.

        This distinguishes:
        - Cancel REQUESTED (flag set, worker still processing)
        - Cancel COMPLETED (worker stopped, status='cancelled')

        Args:
            run_id: The run ID to cancel
            user_id: The user ID (must match run owner)

        Returns:
            Dict with cancel request result

        State transitions:
        - cancel_requested=FALSE, status='running' → cancel_requested=TRUE, status='running' (request accepted)
        - Worker detects flag → stops → status='cancelled' (cancel completed)
        - If status already 'completed'/'cancelled' → reject (409 Conflict)
        """
        db = self.session_factory()
        try:
            # Get run and validate ownership
            run = db.execute(
                text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id},
            ).fetchone()

            if not run:
                raise ResourceNotFoundError("Run", run_id)

            # Cross-user rejection: validate ownership via portfolio
            portfolio = db.execute(
                text("SELECT owner_id FROM paper_portfolios WHERE id = :id"),
                {"id": run.portfolio_id},
            ).fetchone()

            if not portfolio or str(portfolio.owner_id) != user_id:
                owner_id = str(portfolio.owner_id) if portfolio else "unknown"
                raise AuthorizationError(user_id, "run", run_id, owner_id)

            # Check if run can be cancelled (reject if already terminal)
            if run.status in ("completed", "cancelled"):
                raise InvalidStateError("Run", run_id, run.status, ["queued", "pending", "running", "failed"])

            # Convergence policy: queued/pending/failed have no worker, so cancel completes immediately.
            # Only 'running' state uses two-phase contract (worker detects flag and completes cancel).
            if run.status in ("queued", "pending", "failed"):
                # No worker running, complete cancel immediately
                result = db.execute(
                    text("""
                    UPDATE paper_trading_runs
                    SET status = 'cancelled',
                        cancel_requested = TRUE,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id AND status IN ('queued', 'pending', 'failed')
                      AND cancel_requested = FALSE
                    RETURNING id
                """),
                    {"id": run_id},
                )

                cancelled_row = result.fetchone()

                if not cancelled_row:
                    # Another cancel succeeded or status changed
                    current_run = db.execute(
                        text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
                        {"id": run_id},
                    ).fetchone()

                    if current_run and current_run.status == "cancelled":
                        # Idempotent: already cancelled
                        db.commit()
                        return {
                            "run_id": run_id,
                            "status": "cancelled",
                            "cancel_requested": True,
                            "message": "Run already cancelled",
                        }
                    else:
                        # Status changed to running or completed by another operation
                        db.commit()
                        raise InvalidStateError(
                            "Run", run_id, current_run.status if current_run else "unknown",
                            ["queued", "pending", "failed"]
                        )

                db.commit()
                return {
                    "run_id": run_id,
                    "status": "cancelled",
                    "cancel_requested": True,
                    "message": "Cancel completed immediately (no worker running)",
                }

            # Running state: two-phase contract
            # Atomic conditional UPDATE: set cancel_requested flag
            # Do NOT change status yet - worker will set status='cancelled' after stopping
            # PostgreSQL re-checks WHERE condition on concurrent updates, preventing race
            result = db.execute(
                text("""
                UPDATE paper_trading_runs
                SET cancel_requested = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND status = 'running'
                  AND cancel_requested = FALSE
                RETURNING id
            """),
                {"id": run_id},
            )

            cancelled_row = result.fetchone()

            if not cancelled_row:
                # Another cancel request succeeded, or status changed to terminal
                current_run = db.execute(
                    text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
                    {"id": run_id},
                ).fetchone()

                if current_run:
                    if current_run.cancel_requested:
                        # Cancel already requested, return success (idempotent)
                        return {
                            "run_id": run_id,
                            "status": current_run.status,
                            "cancel_requested": True,
                            "message": "Cancel already requested, worker will stop gracefully",
                        }
                    else:
                        # Status changed to terminal
                        raise InvalidStateError(
                            "Run", run_id, current_run.status,
                            ["queued", "pending", "running", "failed"]
                        )
                else:
                    raise ResourceNotFoundError("Run", run_id)

            db.commit()

            logger.info(f"Cancel requested for run {run_id}, worker will stop gracefully")

            return {
                "run_id": run_id,
                "status": run.status,  # Status unchanged - still running/queued/etc
                "cancel_requested": True,
                "message": "Cancel request accepted, worker will stop gracefully",
            }

        except Exception as exc:
            db.rollback()
            logger.exception("Failed to request cancel")
            raise
        finally:
            db.close()

    def execute_synthetic_replay(
        self,
        events: list[dict],
        initial_capital: float = 100000.0,
        max_positions: int = 5,
        position_size_pct: float = 0.10,
        commission_rate: float = 0.0,
        slippage_rate: float = 0.0,
    ) -> dict:
        """Execute paper trading using synthetic events (for testing).

        This method is designed for unit tests. It accepts a list of trade events
        directly, without querying the database. Returns the full ledger including
        orders, fills, positions, and daily PnL.

        Args:
            events: List of trade event dicts with keys:
                - idx: trade index
                - event_type: "entry" or "exit"
                - event_time: datetime
                - instrument_id: str
                - rule_id: str
                - exit_config: str
                - quantity: float
                - price: float
            initial_capital: Starting capital
            max_positions: Maximum concurrent positions
            position_size_pct: Max position size as % of capital
            commission_rate: Commission as fraction of gross (e.g., 0.001 = 0.1%)
            slippage_rate: Slippage as fraction of price (e.g., 0.0005 = 0.05%)

        Returns:
            Dict with keys: orders, fills, positions, daily_pnl, event_log,
            initial_capital, final_capital
        """
        ENTRY = "entry"
        EXIT = "exit"

        # Sort events: by time, exits before entries at same time
        sorted_events = sorted(events, key=lambda e: (e["event_time"], 0 if e["event_type"] == EXIT else 1))

        # Simulation state
        current_capital = initial_capital
        active_positions = {}  # idx -> position dict
        orders = []
        fills = []
        positions = []
        daily_pnl = {}
        event_log = []

        for event in sorted_events:
            event_time = event["event_time"]
            event_type = event["event_type"]
            idx = event["idx"]
            instrument_id = event["instrument_id"]
            quantity = event["quantity"]
            price = event["price"]

            if event_type == EXIT and idx in active_positions:
                # Process exit
                pos = active_positions.pop(idx)
                trade_quantity = pos["quantity"]
                is_long = pos["is_long"]
                entry_price = pos["entry_price"]

                # Calculate PnL (for reporting only, not for cash flow)
                if is_long:
                    realized_pnl = trade_quantity * (price - entry_price)
                else:
                    realized_pnl = trade_quantity * (entry_price - price)

                # Create exit order
                exit_order_id = str(uuid.uuid4())
                exit_side = "SELL" if is_long else "BUY"
                exit_order = {
                    "id": exit_order_id,
                    "side": exit_side,
                    "quantity": trade_quantity,
                    "status": "filled",
                    "created_at": event_time,
                }
                orders.append(exit_order)

                # Create exit fill with costs
                # Cash flow: we receive money from selling
                exit_gross = trade_quantity * price
                exit_commission = exit_gross * commission_rate
                exit_slippage = exit_gross * slippage_rate
                exit_net = exit_gross - exit_commission - exit_slippage

                # Update capital with net cash flow (not realized_pnl separately)
                current_capital += exit_net

                exit_fill_id = str(uuid.uuid4())
                exit_fill = {
                    "id": exit_fill_id,
                    "order_id": exit_order_id,
                    "position_id": pos["id"],
                    "quantity": -trade_quantity,  # Negative for exit
                    "price": price,
                    "gross_amount": exit_gross,
                    "commission_amount": exit_commission,
                    "slippage_amount": exit_slippage,
                    "net_cash_change": exit_net,
                    "realized_pnl": realized_pnl,
                    "execution_date": event_time.strftime("%Y-%m-%d"),
                }
                fills.append(exit_fill)

                # Update position
                pos["status"] = "closed"
                pos["closed_at"] = event_time
                pos["realized_pnl"] = realized_pnl

                # Track daily PnL
                exit_date = event_time.date()
                if exit_date not in daily_pnl:
                    daily_pnl[exit_date] = {"pnl": 0, "trades": 0, "wins": 0}
                daily_pnl[exit_date]["pnl"] += realized_pnl
                daily_pnl[exit_date]["trades"] += 1
                if realized_pnl > 0:
                    daily_pnl[exit_date]["wins"] += 1

                event_log.append({
                    "time": event_time.isoformat(),
                    "event": "exit",
                    "trade_idx": idx,
                    "active_positions": len(active_positions),
                    "capital": current_capital,
                    "realized_pnl": realized_pnl,
                })

            elif event_type == ENTRY and idx not in active_positions:
                # Check capacity
                if len(active_positions) >= max_positions:
                    event_log.append({
                        "time": event_time.isoformat(),
                        "event": "entry_blocked",
                        "trade_idx": idx,
                        "active_positions": len(active_positions),
                        "capital": current_capital,
                        "reason": "max_positions",
                    })
                    continue

                # Check equity > 0
                if current_capital <= 0:
                    event_log.append({
                        "time": event_time.isoformat(),
                        "event": "entry_blocked",
                        "trade_idx": idx,
                        "active_positions": len(active_positions),
                        "capital": current_capital,
                        "reason": "equity_non_positive",
                    })
                    continue

                # Calculate position value
                proposed_position_value = quantity * price

                # Check single asset exposure limit
                single_asset_limit = current_capital * position_size_pct
                if proposed_position_value > single_asset_limit:
                    event_log.append({
                        "time": event_time.isoformat(),
                        "event": "entry_blocked",
                        "trade_idx": idx,
                        "active_positions": len(active_positions),
                        "capital": current_capital,
                        "reason": "single_asset_exposure_limit",
                        "proposed_value": proposed_position_value,
                        "limit": single_asset_limit,
                    })
                    continue

                # Check total gross exposure limit (100% of equity)
                current_exposure = sum(
                    abs(pos["quantity"] * pos["entry_price"])
                    for pos in active_positions.values()
                )
                proposed_exposure = current_exposure + proposed_position_value
                total_exposure_limit = current_capital * 1.0
                if proposed_exposure > total_exposure_limit:
                    event_log.append({
                        "time": event_time.isoformat(),
                        "event": "entry_blocked",
                        "trade_idx": idx,
                        "active_positions": len(active_positions),
                        "capital": current_capital,
                        "reason": "total_exposure_limit",
                        "proposed_exposure": proposed_exposure,
                        "limit": total_exposure_limit,
                    })
                    continue

                # Process entry
                is_long = quantity > 0
                trade_quantity = abs(quantity)

                # Create entry order
                entry_order_id = str(uuid.uuid4())
                entry_order = {
                    "id": entry_order_id,
                    "side": "BUY" if is_long else "SELL",
                    "quantity": trade_quantity,
                    "status": "filled",
                    "created_at": event_time,
                }
                orders.append(entry_order)

                # Create entry fill with costs
                entry_gross = trade_quantity * price
                entry_commission = entry_gross * commission_rate
                entry_slippage = entry_gross * slippage_rate
                entry_net = -(entry_gross + entry_commission + entry_slippage)  # Negative for cash outflow

                entry_fill_id = str(uuid.uuid4())
                entry_fill = {
                    "id": entry_fill_id,
                    "order_id": entry_order_id,
                    "position_id": None,  # Will be set after position created
                    "quantity": trade_quantity if is_long else -trade_quantity,
                    "price": price,
                    "gross_amount": entry_gross,
                    "commission_amount": entry_commission,
                    "slippage_amount": entry_slippage,
                    "net_cash_change": entry_net,
                    "execution_date": event_time.strftime("%Y-%m-%d"),
                }
                fills.append(entry_fill)

                # Create position
                position_id = str(uuid.uuid4())
                position = {
                    "id": position_id,
                    "instrument_id": instrument_id,
                    "quantity": trade_quantity,
                    "entry_price": price,
                    "is_long": is_long,
                    "status": "open",
                    "opened_at": event_time,
                    "closed_at": None,
                    "realized_pnl": 0.0,
                }
                positions.append(position)
                active_positions[idx] = position

                # Link fill to position
                entry_fill["position_id"] = position_id

                # Deduct cash
                current_capital += entry_net

                event_log.append({
                    "time": event_time.isoformat(),
                    "event": "entry",
                    "trade_idx": idx,
                    "active_positions": len(active_positions),
                    "capital": current_capital,
                })

        return {
            "orders": orders,
            "fills": fills,
            "positions": positions,
            "daily_pnl": daily_pnl,
            "event_log": event_log,
            "initial_capital": initial_capital,
            "final_capital": current_capital,
        }

    def generate_daily_report(self, run_id: str, report_date: Optional[date] = None) -> dict:
        """Generate daily performance report for a run."""
        if report_date is None:
            report_date = date.today()

        db = self.session_factory()
        try:
            # Get run details
            run = db.execute(
                text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id},
            ).fetchone()

            if not run:
                raise ResourceNotFoundError("Run", run_id)

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
        db = self.session_factory()
        try:
            run = db.execute(
                text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id},
            ).fetchone()

            if not run:
                raise ResourceNotFoundError("Run", run_id)

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
