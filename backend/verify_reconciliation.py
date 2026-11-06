#!/usr/bin/env python3
"""Independent reconciliation: DB readback vs hand calculation.

This script verifies the paper trading ledger by:
1. Running a replay with known trades
2. Reading all data back from DB
3. Independently calculating expected values
4. Comparing DB values vs hand calculations

If all match, the ledger is correct.
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.database import SessionLocal
from app.services.paper_trading import PaperTradingEngine


def setup_test_data(db, user_id):
    """Create test strategy run with 3 known trades."""
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    
    # Create strategy study run
    db.execute(
        text("""
        INSERT INTO strategy_study_runs (
            id, user_id, lane, research_version, instrument_id, interval_minutes,
            provider_id, data_nature, bar_count, first_open_time, last_open_time,
            settings_json, walk_forward_json, bars_json, trade_count, created_at
        ) VALUES (
            :id, :user_id, 'strategy_research_simulated', 'v1.0', 'TEST/USD', 60,
            'test_provider', 'simulated', 100, :now, :now,
            '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 3, :now
        )
    """),
        {"id": run_id, "user_id": user_id, "now": now},
    )
    
    # Seed 3 non-overlapping trades with known values
    trades = [
        # Trade 0: Win
        {
            "idx": 0,
            "entry_time": now - timedelta(days=10),
            "exit_time": now - timedelta(days=10) + timedelta(hours=1),
            "entry_price": Decimal("100.00"),
            "exit_price": Decimal("110.00"),
            "quantity": Decimal("100.0"),
            "direction": "long",
        },
        # Trade 1: Loss
        {
            "idx": 1,
            "entry_time": now - timedelta(days=9),
            "exit_time": now - timedelta(days=9) + timedelta(hours=1),
            "entry_price": Decimal("102.00"),
            "exit_price": Decimal("97.00"),
            "quantity": Decimal("100.0"),
            "direction": "long",
        },
        # Trade 2: Win
        {
            "idx": 2,
            "entry_time": now - timedelta(days=8),
            "exit_time": now - timedelta(days=8) + timedelta(hours=1),
            "entry_price": Decimal("104.00"),
            "exit_price": Decimal("114.00"),
            "quantity": Decimal("100.0"),
            "direction": "long",
        },
    ]
    
    for trade in trades:
        trade_id = str(uuid.uuid4())
        gross_pnl = trade["quantity"] * (trade["exit_price"] - trade["entry_price"])
        commission_rate = Decimal("0.001")
        slippage_rate = Decimal("0.0005")
        
        entry_value = trade["quantity"] * trade["entry_price"]
        exit_value = trade["quantity"] * trade["exit_price"]
        entry_costs = entry_value * (commission_rate + slippage_rate)
        exit_costs = exit_value * (commission_rate + slippage_rate)
        net_pnl = gross_pnl - entry_costs - exit_costs
        
        db.execute(
            text("""
            INSERT INTO strategy_study_trades (
                id, run_id, trade_index, rule_id, rule_label_zh, exit_id, exit_label_zh,
                direction, exit_reason, outcome, signal_time, entry_time, exit_time,
                entry_price, exit_price, quantity, gross_pnl, commission, slippage_cost,
                total_cost, net_pnl, cash_after, bars_held, pnl_reason_zh, pnl_reason_en, created_at
            ) VALUES (
                :id, :run_id, :idx, 'test_rule', 'Test Rule', 'test_exit', 'Test Exit',
                'long', 'signal', :outcome, :entry_time, :entry_time, :exit_time,
                :entry_price, :exit_price, :quantity, :gross_pnl, :commission, :slippage,
                :total_cost, :net_pnl, 100000.0, 1, 'Trade', 'Trade', :now
            )
        """),
            {
                "id": trade_id,
                "run_id": run_id,
                "idx": trade["idx"],
                "outcome": "win" if net_pnl > 0 else "loss",
                "entry_time": trade["entry_time"],
                "exit_time": trade["exit_time"],
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "quantity": trade["quantity"],
                "gross_pnl": gross_pnl,
                "commission": entry_costs + exit_costs,
                "slippage": Decimal("0"),
                "total_cost": entry_costs + exit_costs,
                "net_pnl": net_pnl,
                "now": now,
            },
        )
    
    db.commit()
    return run_id, trades


def hand_calculate_expected(trades, initial_capital, commission_rate, slippage_rate):
    """Independently calculate expected final equity from trade list."""
    available_cash = Decimal(str(initial_capital))
    position_size_pct = Decimal("0.10")
    
    expected_trades = []
    
    for trade in trades:
        entry_price = trade["entry_price"]
        exit_price = trade["exit_price"]
        quantity = trade["quantity"]
        
        # Calculate position size (10% of equity)
        equity = available_cash  # Simplified: assume no other positions
        position_value = equity * position_size_pct
        trade_quantity = position_value / entry_price
        
        # Entry costs
        entry_commission = position_value * commission_rate
        entry_slippage = position_value * slippage_rate
        entry_total_costs = entry_commission + entry_slippage
        
        # Deduct from cash
        available_cash -= position_value
        available_cash -= entry_total_costs
        
        # Exit
        exit_value = trade_quantity * exit_price
        exit_commission = exit_value * commission_rate
        exit_slippage = exit_value * slippage_rate
        exit_total_costs = exit_commission + exit_slippage
        
        # Gross PnL
        gross_pnl = trade_quantity * (exit_price - entry_price)
        
        # Full net PnL
        full_net_pnl = gross_pnl - entry_total_costs - exit_total_costs
        
        # Return to cash
        available_cash += position_value + gross_pnl - exit_total_costs
        
        expected_trades.append({
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": trade_quantity,
            "entry_costs": entry_total_costs,
            "exit_costs": exit_total_costs,
            "gross_pnl": gross_pnl,
            "full_net_pnl": full_net_pnl,
            "cash_after": available_cash,
        })
    
    return available_cash, expected_trades


def verify_db(db, run_id, expected_final_cash, expected_trades):
    """Read back from DB and compare with expected values."""
    # Get run
    run = db.execute(
        text("SELECT * FROM paper_trading_runs WHERE id = :id"),
        {"id": run_id},
    ).fetchone()
    
    if not run:
        print("❌ Run not found")
        return False
    
    # Get portfolio
    portfolio = db.execute(
        text("SELECT * FROM paper_portfolios WHERE id = :id"),
        {"id": run.portfolio_id},
    ).fetchone()
    
    # Get fills
    fills = db.execute(
        text("""
        SELECT f.*, o.decision_id, d.decision_type
        FROM paper_fills f
        JOIN paper_orders o ON f.paper_order_id = o.id
        JOIN decision_records d ON o.decision_id = d.id
        WHERE o.portfolio_id = :portfolio_id
        ORDER BY f.execution_date, d.decision_type
    """),
        {"portfolio_id": run.portfolio_id},
    ).fetchall()
    
    # Get positions
    positions = db.execute(
        text("SELECT * FROM paper_positions WHERE portfolio_id = :portfolio_id"),
        {"portfolio_id": run.portfolio_id},
    ).fetchall()
    
    # Get decisions
    decisions = db.execute(
        text("""
        SELECT * FROM decision_records
        WHERE paper_portfolio_id = :portfolio_id
        ORDER BY decision_time
    """),
        {"portfolio_id": run.portfolio_id},
    ).fetchall()
    
    print("\n" + "=" * 80)
    print("DB READBACK VERIFICATION")
    print("=" * 80)
    
    # Verify counts
    entry_fills = [f for f in fills if f.decision_type == "entry"]
    exit_fills = [f for f in fills if f.decision_type == "exit"]
    
    print(f"\n✓ Fills: {len(fills)} total ({len(entry_fills)} entry, {len(exit_fills)} exit)")
    print(f"✓ Positions: {len(positions)} (all closed: {all(p.status == 'closed' for p in positions)})")
    print(f"✓ Decisions: {len(decisions)} ({len([d for d in decisions if d.decision_type == 'entry'])} entry, {len([d for d in decisions if d.decision_type == 'exit'])} exit)")
    
    # Verify final cash
    db_final_cash = Decimal(str(portfolio.current_virtual_cash))
    cash_diff = abs(db_final_cash - expected_final_cash)
    
    print(f"\n{'=' * 80}")
    print("CASH RECONCILIATION")
    print(f"{'=' * 80}")
    print(f"Expected final cash: ${expected_final_cash:.2f}")
    print(f"DB final cash:       ${db_final_cash:.2f}")
    print(f"Difference:          ${cash_diff:.6f}")
    
    if cash_diff < Decimal("0.01"):
        print("✓ PASS: Cash matches within $0.01 tolerance")
    else:
        print("❌ FAIL: Cash mismatch exceeds tolerance")
        return False
    
    # Verify per-trade calculations
    print(f"\n{'=' * 80}")
    print("PER-TRADE VERIFICATION")
    print(f"{'=' * 80}")
    
    for idx, (expected, position) in enumerate(zip(expected_trades, positions)):
        db_net_pnl = Decimal(str(position.realized_pnl or 0))
        expected_net_pnl = expected["full_net_pnl"]
        pnl_diff = abs(db_net_pnl - expected_net_pnl)
        
        print(f"\nTrade {idx}:")
        print(f"  Entry: ${expected['entry_price']:.2f} → Exit: ${expected['exit_price']:.2f}")
        print(f"  Quantity: {expected['quantity']:.4f}")
        print(f"  Entry costs: ${expected['entry_costs']:.2f}")
        print(f"  Exit costs:  ${expected['exit_costs']:.2f}")
        print(f"  Gross PnL:   ${expected['gross_pnl']:.2f}")
        print(f"  Expected net PnL: ${expected_net_pnl:.2f}")
        print(f"  DB net PnL:       ${db_net_pnl:.2f}")
        print(f"  Difference:       ${pnl_diff:.6f}")
        
        if pnl_diff < Decimal("0.01"):
            print(f"  ✓ PASS")
        else:
            print(f"  ❌ FAIL")
            return False
    
    # Verify source pinning
    print(f"\n{'=' * 80}")
    print("SOURCE PINNING VERIFICATION")
    print(f"{'=' * 80}")
    
    decisions_with_source = [d for d in decisions if d.source_trade_id is not None]
    print(f"\n✓ Decisions with source_trade_id: {len(decisions_with_source)}/{len(decisions)}")
    
    if len(decisions_with_source) == len(decisions):
        print("✓ PASS: All decisions have source pinning")
    else:
        print("❌ FAIL: Some decisions missing source pinning")
        return False
    
    return True


def cleanup(db, user_id, run_id, strategy_run_id):
    """Clean up test data."""
    print("\nCleaning up test data...")
    
    # Get portfolio_id
    run = db.execute(
        text("SELECT portfolio_id FROM paper_trading_runs WHERE id = :id"),
        {"id": run_id},
    ).fetchone()
    
    if run:
        portfolio_id = run.portfolio_id
        
        # Delete in correct order (respecting foreign keys)
        db.execute(text("DELETE FROM paper_fills WHERE paper_order_id IN (SELECT id FROM paper_orders WHERE portfolio_id = :pid)"), {"pid": portfolio_id})
        db.execute(text("DELETE FROM paper_orders WHERE portfolio_id = :pid"), {"pid": portfolio_id})
        db.execute(text("DELETE FROM paper_positions WHERE portfolio_id = :pid"), {"pid": portfolio_id})
        db.execute(text("DELETE FROM decision_records WHERE paper_portfolio_id = :pid"), {"pid": portfolio_id})
        db.execute(text("DELETE FROM paper_trading_daily_metrics WHERE run_id = :rid"), {"rid": run_id})
        db.execute(text("DELETE FROM paper_trading_runs WHERE id = :rid"), {"rid": run_id})
        db.execute(text("DELETE FROM paper_portfolios WHERE id = :pid"), {"pid": portfolio_id})
    
    # Delete strategy study data
    db.execute(text("DELETE FROM strategy_study_trades WHERE run_id = :rid"), {"rid": strategy_run_id})
    db.execute(text("DELETE FROM strategy_study_runs WHERE id = :rid"), {"rid": strategy_run_id})
    
    db.commit()
    print("✓ Cleanup complete")


def main():
    print("=" * 80)
    print("INDEPENDENT RECONCILIATION TEST")
    print("=" * 80)
    
    db = SessionLocal()
    user_id = str(uuid.uuid4())
    run_id = None
    
    try:
        # Create test user
        db.execute(
            text("""
            INSERT INTO users (id, email, password_hash, is_active, is_admin, created_at, updated_at)
            VALUES (:id, :email, 'test', true, false, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO NOTHING
        """),
            {"id": user_id, "email": f"test-{user_id}@example.com"},
        )
        db.commit()
        
        print(f"\n1. Setting up test data for user: {user_id}")
        strategy_run_id, trades = setup_test_data(db, user_id)
        print(f"   ✓ Created strategy run: {strategy_run_id}")
        print(f"   ✓ Seeded {len(trades)} trades")
        
        # Create paper trading portfolio and run
        print(f"\n2. Creating paper trading portfolio...")
        engine = PaperTradingEngine(os.environ.get("DATABASE_URL", "postgresql://localhost/ai_investment_lab"))
        initial_capital = 100000.0
        commission_rate = Decimal("0.001")
        slippage_rate = Decimal("0.0005")
        
        portfolio_result = engine.create_portfolio(
            user_id=user_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="test_exit",
            initial_capital=Decimal(str(initial_capital)),
        )
        
        run_id = portfolio_result["run_id"]
        print(f"   ✓ Created portfolio: {portfolio_result['portfolio_id']}")
        print(f"   ✓ Created run: {run_id}")
        
        # Run replay
        print(f"\n3. Running paper trading replay...")
        
        result = engine.execute_historical_replay(
            run_id=run_id,
            user_id=user_id,
            days_back=30,
            position_size_pct=0.10,
            max_positions=3,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
        )
        
        print(f"   ✓ Replay completed: {result['total_trades']} trades, ${result['total_pnl']:.2f} PnL")
        
        # Hand calculate expected values
        print(f"\n4. Hand-calculating expected values...")
        expected_final_cash, expected_trades = hand_calculate_expected(
            trades, initial_capital, commission_rate, slippage_rate
        )
        print(f"   ✓ Expected final cash: ${expected_final_cash:.2f}")
        
        # Verify DB
        print(f"\n5. Verifying DB against expected values...")
        success = verify_db(db, run_id, expected_final_cash, expected_trades)
        
        if success:
            print(f"\n{'=' * 80}")
            print("✓ ALL CHECKS PASSED")
            print(f"{'=' * 80}")
            print("\nThe ledger is correct:")
            print("- Cash reconciliation matches")
            print("- Per-trade PnL calculations match")
            print("- Source pinning complete")
        else:
            print(f"\n{'=' * 80}")
            print("❌ VERIFICATION FAILED")
            print(f"{'=' * 80}")
            sys.exit(1)
        
    finally:
        if run_id:
            cleanup(db, user_id, run_id, strategy_run_id)
        db.close()


if __name__ == "__main__":
    main()
