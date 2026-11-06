"""Live paper trading daemon.

Orchestrates real-time trading using Kraken WebSocket data,
signal generation, and paper order execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import text

from app.database import SessionLocal
from app.services.kraken_websocket import KrakenBar, KrakenWebSocketClient
from app.services.signal_generator import SignalGenerator, SignalType, TradingSignal

logger = logging.getLogger(__name__)


class LivePaperTradingDaemon:
    """Live paper trading daemon for continuous operation."""

    def __init__(
        self,
        symbols: list[str],
        skills: list[dict],
        interval_seconds: int = 60,
        initial_capital: float = 100000.0,
    ):
        """
        Args:
            symbols: List of Kraken symbol pairs (e.g., ["XBT/USD", "ETH/USD"])
            skills: List of skill configurations
            interval_seconds: Bar interval in seconds
            initial_capital: Starting capital per portfolio
        """
        self.symbols = symbols
        self.skills = skills
        self.interval_seconds = interval_seconds
        self.initial_capital = initial_capital

        self.running = False
        self.ws_client: Optional[KrakenWebSocketClient] = None
        self.signal_generators: dict[str, SignalGenerator] = {}
        self.portfolio_ids: dict[str, str] = {}
        self.run_ids: dict[str, str] = {}

        # Performance tracking
        self.total_trades = 0
        self.total_pnl = 0.0

    def on_bar(self, bar: KrakenBar):
        """Callback when a new bar closes."""
        symbol = bar.symbol

        # Update all signal generators for this symbol
        for skill_config in self.skills:
            if skill_config["instrument_id"].endswith(symbol.replace("/", "-")):
                key = f"{skill_config['skill']}_{symbol}"
                if key in self.signal_generators:
                    gen = self.signal_generators[key]
                    gen.on_bar(bar)

                    # Generate signal
                    sig = gen.generate_signal()
                    if sig:
                        asyncio.create_task(self._execute_signal(sig, skill_config))

    async def _execute_signal(self, sig: TradingSignal, skill_config: dict):
        """Execute trading signal by creating paper orders."""
        logger.info(
            f"📊 Signal: {sig.signal_type.value} {sig.symbol} @ ${sig.price:.2f} "
            f"| {sig.reason}"
        )

        db = SessionLocal()
        try:
            key = f"{skill_config['skill']}_{sig.symbol}"
            run_id = self.run_ids.get(key)
            portfolio_id = self.portfolio_ids.get(key)

            if not run_id or not portfolio_id:
                logger.error(f"No run/portfolio found for {key}")
                return

            # Create decision record
            decision_id = str(uuid.uuid4())
            db.execute(
                text("""
                INSERT INTO decision_records (
                    id, owner_id, actor_type, decision_type, decision_time,
                    rationale, paper_portfolio_id
                )
                VALUES (
                    :id, :owner_id, 'live_trading', :decision_type, :time,
                    :rationale, :portfolio_id
                )
            """),
                {
                    "id": decision_id,
                    "owner_id": skill_config["user_id"],
                    "decision_type": sig.signal_type.value,
                    "time": sig.timestamp,
                    "rationale": sig.reason,
                    "portfolio_id": portfolio_id,
                },
            )

            # Create paper order
            order_id = str(uuid.uuid4())
            side = "BUY" if sig.signal_type == SignalType.ENTRY_LONG else "SELL"
            quantity = (self.initial_capital * 0.1) / sig.price  # 10% position size

            db.execute(
                text("""
                INSERT INTO paper_orders (
                    id, portfolio_id, decision_id, asset_id, side, quantity,
                    order_type, created_at, status, actor_type
                )
                VALUES (
                    :id, :portfolio_id, :decision_id, :asset_id, :side, :quantity,
                    'market', :time, 'filled', 'live_trading'
                )
            """),
                {
                    "id": order_id,
                    "portfolio_id": portfolio_id,
                    "decision_id": decision_id,
                    "asset_id": skill_config["instrument_id"],
                    "side": side,
                    "quantity": quantity,
                    "time": sig.timestamp,
                },
            )

            # Create paper fill with slippage
            fill_id = str(uuid.uuid4())
            slippage = sig.price * 0.001  # 0.1% slippage
            exec_price = sig.price + slippage if side == "BUY" else sig.price - slippage

            db.execute(
                text("""
                INSERT INTO paper_fills (
                    id, paper_order_id, execution_date, observed_price,
                    execution_price_before_costs, slippage_amount, commission_amount,
                    quantity, gross_amount, net_cash_change, simulated_fill_reason, bar_timestamp
                )
                VALUES (
                    :id, :order_id, :exec_date, :obs_price,
                    :exec_price, :slippage, 0,
                    :quantity, :gross, :net, 'live_signal', :timestamp
                )
            """),
                {
                    "id": fill_id,
                    "order_id": order_id,
                    "exec_date": sig.timestamp.strftime("%Y-%m-%d"),
                    "obs_price": sig.price,
                    "exec_price": exec_price,
                    "slippage": slippage,
                    "quantity": quantity,
                    "gross": quantity * exec_price,
                    "net": -quantity * exec_price if side == "BUY" else quantity * exec_price,
                    "timestamp": sig.timestamp,
                },
            )

            # Update run metrics
            self.total_trades += 1
            db.execute(
                text("""
                UPDATE paper_trading_runs
                SET total_trades = total_trades + 1,
                    updated_at = NOW()
                WHERE id = :id
            """),
                {"id": run_id},
            )

            db.commit()
            logger.info(f"✅ Order executed: {order_id}")

        except Exception as exc:
            db.rollback()
            logger.exception(f"Failed to execute signal: {exc}")
        finally:
            db.close()

    def _create_portfolios(self) -> int:
        """Create paper trading portfolios for each skill.
        
        Validates strategy support BEFORE creating any DB records to avoid
        orphaned portfolios/runs for unknown strategies.
        
        Returns:
            Number of successfully validated and created portfolios.
        """
        # Phase 1: Validate all skills before any DB writes
        validated_skills = []
        for skill_config in self.skills:
            skill = skill_config["skill"]
            instrument_id = skill_config["instrument_id"]
            interval_minutes = skill_config["interval_minutes"]
            rule_id = skill_config.get("rule_id")
            
            # Extract symbol from instrument_id
            symbol = instrument_id.split(":")[-1].replace("-", "/")
            
            # Validate strategy support by attempting to create SignalGenerator
            try:
                gen = SignalGenerator(
                    symbol=symbol,
                    rule_id=rule_id,
                    skill=skill,
                    interval_minutes=interval_minutes,
                )
                validated_skills.append((skill_config, symbol, gen))
                logger.info(f"✅ Validated strategy: {skill} ({rule_id}) on {symbol}")
            except ValueError as e:
                logger.error(f"❌ Rejected unknown strategy: {skill} ({rule_id}) — {e}")
                continue
        
        if not validated_skills:
            logger.error("No valid skills after validation. Aborting portfolio creation.")
            return 0
        
        # Report actual validated set
        validated_set = [f"{skill}_{symbol}" for skill_config, symbol, gen in validated_skills 
                        for skill in [skill_config["skill"]]]
        logger.info(f"📋 Validated skill set: {', '.join(validated_set)}")
        
        # Phase 2: Create DB records only for validated skills
        db = SessionLocal()
        created_count = 0
        try:
            for skill_config, symbol, gen in validated_skills:
                skill = skill_config["skill"]
                instrument_id = skill_config["instrument_id"]
                interval_minutes = skill_config["interval_minutes"]
                exit_config = skill_config["exit_config"]
                user_id = skill_config["user_id"]
                
                # Create portfolio
                portfolio_id = str(uuid.uuid4())
                db.execute(
                    text("""
                    INSERT INTO paper_portfolios (
                        id, owner_id, currency, initial_virtual_cash, 
                        current_virtual_cash, status, created_at
                    )
                    VALUES (
                        :id, :owner_id, 'USD', :initial, :current, 'active', NOW()
                    )
                """),
                    {
                        "id": portfolio_id,
                        "owner_id": user_id,
                        "initial": self.initial_capital,
                        "current": self.initial_capital,
                    },
                )

                # Create run
                run_id = str(uuid.uuid4())
                db.execute(
                    text("""
                    INSERT INTO paper_trading_runs (
                        id, portfolio_id, run_type, skill, instrument_id, 
                        interval_minutes, exit_config, start_date, status, 
                        initial_capital, total_pnl, total_trades
                    )
                    VALUES (
                        :id, :portfolio_id, 'live_trading', :skill, :instrument,
                        :interval, :exit_config, NOW(), 'running', :initial, 0, 0
                    )
                """),
                    {
                        "id": run_id,
                        "portfolio_id": portfolio_id,
                        "skill": skill,
                        "instrument": instrument_id,
                        "interval": interval_minutes,
                        "exit_config": exit_config,
                        "initial": self.initial_capital,
                    },
                )

                db.commit()

                # Store IDs
                key = f"{skill}_{symbol}"
                self.portfolio_ids[key] = portfolio_id
                self.run_ids[key] = run_id
                self.signal_generators[key] = gen
                created_count += 1

                logger.info(f"✅ Created portfolio for {skill} on {symbol}")

        except Exception as exc:
            db.rollback()
            logger.exception(f"Failed to create portfolios: {exc}")
            return 0
        finally:
            db.close()
        
        return created_count

    async def start(self):
        """Start the live trading daemon."""
        logger.info("=" * 80)
        logger.info("🚀 STARTING LIVE PAPER TRADING DAEMON")
        logger.info("=" * 80)
        logger.info(f"Symbols: {', '.join(self.symbols)}")
        logger.info(f"Skills: {len(self.skills)}")
        logger.info(f"Bar interval: {self.interval_seconds}s")
        logger.info(f"Initial capital: ${self.initial_capital:,.2f}")
        logger.info("")

        self.running = True

        # Create portfolios
        created_count = self._create_portfolios()
        
        # Fail closed: if no portfolios created, don't start WebSocket
        if created_count == 0:
            logger.error("=" * 80)
            logger.error("❌ DAEMON STARTUP ABORTED: No valid portfolios created")
            logger.error("=" * 80)
            logger.error("All requested skills were rejected during validation.")
            logger.error("Cannot start WebSocket without active portfolios.")
            self.running = False
            return
        
        logger.info(f"✅ Successfully created {created_count} portfolio(s)")
        logger.info(f"📊 Active skill set: {', '.join(sorted(self.portfolio_ids.keys()))}")
        logger.info("")

        # Start WebSocket client
        self.ws_client = KrakenWebSocketClient(
            symbols=self.symbols,
            on_bar=self.on_bar,
            interval_seconds=self.interval_seconds,
        )

        try:
            await self.ws_client.start()
        except KeyboardInterrupt:
            logger.info("\n🛑 Shutting down gracefully...")
            await self.stop()
        except Exception as exc:
            logger.exception(f"Daemon error: {exc}")
            await self.stop()
            raise

    async def stop(self):
        """Stop the live trading daemon."""
        self.running = False

        if self.ws_client:
            await self.ws_client.stop()

        # Update all runs to stopped
        db = SessionLocal()
        try:
            for run_id in self.run_ids.values():
                db.execute(
                    text("""
                    UPDATE paper_trading_runs
                    SET status = 'stopped',
                        end_date = NOW(),
                        updated_at = NOW()
                    WHERE id = :id
                """),
                    {"id": run_id},
                )
            db.commit()
        finally:
            db.close()

        logger.info("🛑 Live trading daemon stopped")
        logger.info(f"Total trades: {self.total_trades}")
        logger.info(f"Total PnL: ${self.total_pnl:,.2f}")


def main():
    """Main entry point for the daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("/tmp/live_trading_daemon.log"),
        ],
    )

    # Load configuration from environment
    symbols = os.environ.get("LIVE_TRADING_SYMBOLS", "XBT/USD,ETH/USD,SOL/USD").split(",")
    interval = int(os.environ.get("LIVE_TRADING_INTERVAL", "60"))
    capital = float(os.environ.get("LIVE_TRADING_CAPITAL", "100000"))

    # Get top profitable skills from database
    db = SessionLocal()
    try:
        results = db.execute(
            text("""
            SELECT 
                t.rule_id,
                t.rule_label_zh as skill,
                r.instrument_id,
                r.interval_minutes,
                t.exit_id,
                r.user_id
            FROM strategy_study_trades t
            JOIN strategy_study_runs r ON t.run_id = r.id
            GROUP BY t.rule_id, t.rule_label_zh, r.instrument_id, r.interval_minutes, t.exit_id, r.user_id
            HAVING SUM(t.net_pnl) > 0
            ORDER BY SUM(t.net_pnl) DESC
            LIMIT 5
        """)
        ).fetchall()

        skills = [
            {
                "rule_id": r[0],
                "skill": r[1],
                "instrument_id": r[2],
                "interval_minutes": r[3],
                "exit_config": r[4],
                "user_id": str(r[5]),
            }
            for r in results
        ]

        logger.info(f"Loaded {len(skills)} profitable skills from database")

    finally:
        db.close()

    if not skills:
        logger.error("No profitable skills found. Exiting.")
        sys.exit(1)

    # Create and start daemon
    daemon = LivePaperTradingDaemon(
        symbols=symbols,
        skills=skills,
        interval_seconds=interval,
        initial_capital=capital,
    )

    # Handle shutdown signals
    def signal_handler(signum, frame):
        logger.info(f"\nReceived signal {signum}, shutting down...")
        asyncio.create_task(daemon.stop())

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run daemon
    asyncio.run(daemon.start())


if __name__ == "__main__":
    main()
