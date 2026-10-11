# Phase 1 Manual Acceptance Test Script

**Date**: ___________
**Tester**: ___________
**Engine Version**: Backtest 1.1.0 / Simulation 1.0.0

---

## Prerequisites

- Backend, frontend, and database running locally
- Test account registered or available

---

## Test 1: Service Startup

| Step | Expected Result | Actual Result | Pass/Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| Start database (`docker-compose up db -d`) | PostgreSQL running | | | |
| Start backend (`uvicorn app.main:app --reload --port 8000`) | Backend on :8000, no errors | | | |
| Start frontend (`npm run dev`) | Frontend on :3000, no errors | | | |
| Visit http://localhost:8000/docs | Swagger UI loads | | | |

---

## Test 2: Branding

| Step | Expected Result | Actual Result | Pass/Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| Visit http://localhost:3000 | Landing page shows "Alethra" brand | | | |
| Check tagline | "Ideas, tested. Decisions, yours." displayed | | | |
| Check disclaimer | "研究工具，非投資建議" or equivalent visible | | | |

---

## Test 3: Demo Data Labelling

| Step | Expected Result | Actual Result | Pass/Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| View demo assets list | Assets prefixed DEMO- (DEMO-ALPHA, DEMO-BETA, DEMO-BENCHMARK) | | | |
| Check data description | Labelled as demo/simulated data | | | |
| Create any experiment | Results include "Demo data only" warning | | | |

---

## Test 4: Buy-and-Hold Experiment

| Step | Expected Result | Actual Result | Pass/Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| Register/login | Auth succeeds | | | |
| Create experiment with buy_and_hold template, DEMO-ALPHA | Experiment created | | | |
| Run backtest (commission=0.001, slippage=0.001) | Run completes, status=completed | | | |
| Check initial BUY transaction | signal_date=null, execution_date=first date, rationale="Initial allocation at first tradable session" | | | |
| Check final SELL transaction | side=SELL, shares_after=0, rationale contains "Forced end-of-period liquidation" | | | |
| Check commission > 0 | total_commissions > 0 | | | |
| Check slippage > 0 | total_slippage_cost > 0 | | | |
| Check final_equity > 0 | final_equity is positive number | | | |
| Check benchmark curve exists | benchmark_equity_curve has same length as daily_equity_curve | | | |
| Check annualized_return_pct | Returns dict with value=null and reason mentioning 252 | | | |
| Check annualized_volatility_pct | Returns dict with value=null and reason mentioning 252 | | | |
| Check warnings | Contains "Demo data only" and "Zero commission/slippage" warnings as appropriate | | | |
| Check disclaimer | Research disclaimer visible on results | | | |

---

## Test 5: Experiment Versioning

| Step | Expected Result | Actual Result | Pass/Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| Create new version with changed commission (e.g. 0.005) | Version number increases | | | |
| Check original version | Original version config_hash unchanged | | | |
| Check new version | New version has different config_hash | | | |
| Run both versions | Each run records its own config_hash and engine_version=1.1.0 | | | |
| Compare results | Different commissions produce different final_equity | | | |

---

## Test 6: SMA Crossover Experiment

| Step | Expected Result | Actual Result | Pass/Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| Create SMA experiment (DEMO-BETA, short=5, long=10) | Experiment created | | | |
| Run backtest | Run completes | | | |
| Check signal-driven BUY transactions | signal_date < execution_date for all signal-driven trades | | | |
| Check signal-driven SELL transactions | signal_date < execution_date for all signal-driven trades | | | |
| No same-day look-ahead | No signal-driven trade has signal_date == execution_date | | | |
| Check force exit if present | Last SELL rationale mentions "Forced exit" if position open at end | | | |

---

## Test 7: Price Change Fixed Hold Experiment

| Step | Expected Result | Actual Result | Pass/Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| Create price-change experiment (DEMO-ALPHA, lookback=3, threshold=0.05, holding=5) | Experiment created | | | |
| Run backtest | Run completes | | | |
| Check BUY signal timing | signal_date < execution_date for buy trades | | | |
| Check holding period exit | SELL after holding_days sessions | | | |
| Check forced exit warning | If data ends with position, warning present | | | |

---

## Test 8: Invalid Parameters

| Step | Expected Result | Actual Result | Pass/Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| SMA with short_window >= long_window | Server returns error, run_status=error | | | |
| Price change with lookback_days=0 | Server returns error, run_status=error | | | |
| Invalid asset ID | Server returns error with "Unsupported" message | | | |
| initial_cash <= 0 | Server returns error | | | |

---

## Test 9: Disclaimer Visibility

| Step | Expected Result | Actual Result | Pass/Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| Results page | Disclaimer visible ("研究工具，非投資建議" or equivalent) | | | |
| Landing page | Research disclaimer present | | | |

---

## Test 10: Research Event Ledger

| Step | Expected Result | Actual Result | Pass/Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| Create experiment | Timeline shows 3 events: experiment_created, dataset_selected, strategy_template_selected | | | |
| Check event metadata | All creation events have recorded_before_outcome=true, actor_type=human, data_cutoff_at set | | | |
| Update hypothesis | Timeline includes hypothesis_updated event with payload | | | |
| Update falsification conditions | Timeline includes falsification_condition_added event | | | |
| Change strategy_spec without change_reason | API returns 400 error | | | |
| Change strategy_spec with change_reason | New version created, timeline has parameter_changed + strategy_version_created | | | |
| Run backtest | Timeline has research_run_started (before outcome) + research_run_completed (after outcome) | | | |
| Check warning events | Each warning has warning_generated event with actor_type=system | | | |
| Lock experiment | Timeline has strategy_locked event with rules_locked=true | | | |
| Run on locked experiment | Run events have rules_locked=true | | | |
| Add research note | Note created, timeline has research_note_added event | | | |
| Compare versions | Diff shows changed fields with old/new values | | | |
| Export experiment | Export includes experiment, version_history, runs, events, notes, disclaimer, methodology | | | |
| Timeline privacy | Other user gets 404 when accessing timeline | | | |

---

## Test 11: Strategy Locking

| Step | Expected Result | Actual Result | Pass-Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| Create experiment, add strategy_spec | Experiment created with version | | | |
| Lock experiment via PUT scope={"status": "locked"} | experiment.status = "locked", version.locked_at set | | | |
| Try to modify strategy_spec on locked experiment | API returns 400 "Experiment is locked" | | | |
| Try to create new version after lock | API returns 400 "Cannot create new version after lock" | | | |
| Check version metadata | locked_by, strategy_config_hash, dataset_cutoff_at, engine_version all set | | | |
| Check timeline | strategy_locked event with rules_locked=true | | | |

---

## Test 12: Forward Simulation Lifecycle

| Step | Expected Result | Actual Result | Pass-Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| Lock experiment first | Experiment locked | | | |
| Create simulation session (POST /simulations/versions/{version_id}) | Session created, status=pending | | | |
| Try to create simulation on unlocked version | API returns 400 "Version must be locked" | | | |
| Advance simulation (POST /simulations/{id}/advance) | Status → active, step_number=1, snapshot_date=start_date | | | |
| Advance again | step_number=2, snapshot_date > previous, decision recorded | | | |
| Check decision metadata | snapshot_date, data_cutoff_at, engine_version, decision_type, paper_cash, paper_position, paper_equity all present | | | |
| Pause simulation | Status → paused | | | |
| Resume simulation | Status → active | | | |
| Advance until no more snapshots | Status → completed, force-exit if position open | | | |
| Try to advance completed session | API returns 400 "simulation complete" | | | |

---

## Test 13: Simulation Privacy and History

| Step | Expected Result | Actual Result | Pass-Fail | Evidence |
|------|----------------|---------------|-----------|----------|
| User A creates simulation | Session owned by User A | | | |
| User B tries to GET session | API returns 403 "Not authorized" | | | |
| User B tries to advance session | API returns 403 | | | |
| Retire a simulation (POST /simulations/{id}/retire) | Status → retired | | | |
| GET decisions on retired session | Decision history still accessible | | | |
| Check simulation events in timeline | simulation_created, simulation_advanced, simulation_completed/retired all present | | | |
| Check all simulation events have rules_locked=true | Every event has rules_locked=true | | | |

---

## Test 14: Paper Portfolio Lifecycle

| Step | Expected Result | Pass-Fail | Evidence |
|------|----------------|-----------|----------|
| Create paper portfolio (POST /paper-portfolio with initial_virtual_cash=10000) | 201 Created, status=active, current_virtual_cash=10000 | | |
| GET /paper-portfolio | Portfolio appears in list | | |
| Place BUY order (DEMO-ALPHA, qty=10, with rationale + expected_outcome) | 201 Created, order status=filled, fill has commission > 0 and slippage > 0 | | |
| GET positions | Open position for DEMO-ALPHA with correct quantity | | |
| Verify cash decreased | current_virtual_cash < 10000 (deducted gross + commission) | | |
| Place SELL order for same asset (qty=10) | 201 Created, position closes, realized_pnl recorded | | |
| Record no_action decision (POST /decisions with decision_type=no_action) | 201 Created, decision_type=no_action | | |

---

## Test 15: Paper Portfolio Risk Controls and Expectations

| Step | Expected Result | Pass-Fail | Evidence |
|------|----------------|-----------|----------|
| Create portfolio, then pause it | Status → paused | | |
| Try to place order on paused portfolio | 400 "Risk check failed: Portfolio is paused" | | |
| Resume portfolio | Status → active | | |
| Try entry decision without expected_outcome | 400 "Expected outcome required" | | |
| Create entry decision with all required fields | 201, outcome_status=pending | | |
| Evaluate outcome (POST /decisions/{id}/outcome) | 201, decision.outcome_status → evaluated | | |
| Try to evaluate same decision again | 400 "Decision already evaluated" | | |
| Create override request | 201, decision_type=override_request, risk_flags.is_override=true | | |

---

## Test 16: Paper Portfolio Privacy and Immutability

| Step | Expected Result | Pass-Fail | Evidence |
|------|----------------|-----------|----------|
| User A creates portfolio | Owned by User A | | |
| User B tries to GET portfolio | 403 "Not authorized" | | |
| User B tries to GET positions | 403 "Not authorized" | | |
| Retire portfolio | Status → retired, closed_at set | | |
| Try to place order on retired portfolio | 400 "Cannot create order for 'retired' portfolio" | | |
| Try to pause retired portfolio | 400 "Can only pause an active portfolio" | | |
| Try to retire again | 400 "Cannot retire a 'retired' portfolio" | | |
| GET decisions on retired portfolio | Decision history still accessible | | |
| Verify disclaimer banner on paper-portfolio page | Disclaimer text visible: "Paper trading records simulated execution..." | | |

---

## Summary

| Category | Tests | Passed | Failed |
|----------|-------|--------|--------|
| Service Startup | 4 | | |
| Branding | 3 | | |
| Demo Data | 3 | | |
| Buy-and-Hold | 12 | | |
| Versioning | 5 | | |
| SMA Crossover | 5 | | |
| Price Change | 4 | | |
| Invalid Params | 4 | | |
| Disclaimer | 2 | | |
| Event Ledger | 14 | | |
| Strategy Locking (3A) | 6 | | |
| Simulation Lifecycle (3A) | 10 | | |
| Simulation Privacy (3A) | 7 | | |
| Paper Portfolio Lifecycle (3B) | 7 | | |
| Paper Portfolio Risk Controls (3B) | 8 | | |
| Paper Portfolio Privacy (3B) | 9 | | |
| **Total** | **103** | | |

---

**Manual acceptance complete**: ☐ Yes ☐ No
**Date completed**: ___________
**Notes**: ___________
