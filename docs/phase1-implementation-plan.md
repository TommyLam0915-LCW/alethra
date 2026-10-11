# Phase 1 Implementation Plan — Final Status

**Date**: 2026-10-11
**Engine Version**: 1.1.0

---

## Phase 1 Status

**Feature Complete — Local Prototype, Demo Data Only**

---

## 1. Implemented Architecture

- **Frontend**: Next.js 14 + TypeScript, centralized brand config (`brand.ts`)
- **Backend**: FastAPI + Python, SQLAlchemy ORM, Pydantic validation
- **Database**: PostgreSQL via Docker Compose, schema managed by `create_all()`
- **Auth**: JWT bearer tokens, bcrypt password hashing
- **Tests**: pytest, 38 passing (29 engine + 9 API)
- **Brand**: Centralized in `brand.ts` (frontend) and `brand.py` (backend)

---

## 2. What Is Implemented

### Strategy Templates (3)

| Template | Description | Validation |
|----------|-------------|------------|
| buy_and_hold | Buy max whole shares at first open, hold to final close | initial_cash > 0, valid asset |
| sma_crossover | SMA(short) / SMA(long) cross signals | short_window 2-60, long_window 3-200, short < long |
| price_change_fixed_hold | Price drop triggers buy, fixed holding period | lookback 1-60, threshold 0.01-0.50, holding 1-120 |

### Backtest Engine

- Deterministic execution (config_hash + dataset_checksum)
- 16-field transaction ledger per trade
- Signal-execution separation (day t close → day t+1 open)
- Buy-and-hold initial allocation with `signal_date = null`
- Forced end-of-period liquidation with explicit rationale
- Whole-share sizing (no fractional shares)
- Commission and slippage modeling (fixed rate)
- Execution-consistent benchmark (Model A)
- Daily equity curve tracking
- Time-in-market calculation
- Comprehensive warnings system

### Benchmark (Model A — Execution-Consistent)

The benchmark uses:
- Same initial cash
- Same first-day open-price entry timing
- Same whole-share sizing
- Same commission rate
- Same slippage rate
- Same final liquidation convention
- Same data calendar

`benchmark_total_return_pct` uses `initial_cash` as base for direct comparison with strategy `total_return_pct`.

### Metrics

| Metric | Condition |
|--------|-----------|
| total_return_pct | Always computed |
| max_drawdown_pct | Always computed |
| benchmark_total_return_pct | When benchmark available |
| excess_return_pct | When benchmark available |
| annualized_return_pct | Unavailable if trading_days < 252 |
| annualized_volatility_pct | Unavailable if trading_days < 252 |
| total_commissions | Always computed |
| total_slippage_cost | Always computed |
| total_trades | Always computed |
| time_in_market_pct | Always computed |

### Demo Data

| Asset | Description | Trading Days | Date Range |
|-------|-------------|-------------|------------|
| DEMO-ALPHA | Simulated growth stock | 29 (missing 2024-01-18) | 2024-01-02 ~ 2024-02-14 |
| DEMO-BETA | Simulated volatile stock | 30 | 2024-01-02 ~ 2024-02-14 |
| DEMO-BENCHMARK | Simulated benchmark index | 30 | 2024-01-02 ~ 2024-02-14 |

All demo data is deterministically generated, not real market data.

### API

- Auth: `/api/auth/register`, `/api/auth/login`, `/api/auth/me`
- Brand: `GET /api/v1/brand`
- Demo assets: `GET /api/v1/demo-assets`, `GET /api/v1/demo-assets/{asset_id}/prices`
- Experiments: CRUD at `/api/v1/experiments/`
- Versions: nested under experiments
- Runs: `POST /api/v1/versions/{version_id}/runs`, `GET /api/v1/runs/{run_id}`
- Export: `GET /api/v1/experiments/{id}/export`

### Data Models

- Experiment (name, description)
- ExperimentVersion (strategy_spec, config_hash, version_number)
- ResearchRun (metrics, warnings, engine_version, config_hash, dataset_checksum)
- DatasetSnapshot (checksum, asset_ids, date_range)
- ResearchNote (model defined, UI not implemented)
- AuditEvent (model defined, UI not implemented)

### Test Suite

- 29 engine tests: 3 strategy templates, transaction ledger, benchmark Model A compliance, signal-execution separation, annualization guards, config hash, dataset checksum, metrics, engine version
- 9 API tests: brand, demo assets, auth, experiment CRUD, run execution

---

## 3. What Is Demo/Mock Only

- All market data (DEMO-ALPHA, DEMO-BETA, DEMO-BENCHMARK)
- No real securities data
- No live market feeds
- No data provider integration

---

## 4. What Is Not Implemented

- Strategy visualization (charts)
- Experiment comparison (side-by-side)
- ResearchNote UI
- AuditEvent UI
- Alembic migrations
- Rate limiting
- Caching layer
- Mobile responsive design

---

## 5. Explicit Non-Goals (Out of Scope for Phase 1)

- AI/LLM integration
- Live market data
- Payments/billing
- Forward simulation
- Team collaboration
- Production deployment
- Real securities data
- Multi-asset portfolios
- Strategy optimization
- Short selling
- Leverage
- Options, futures, forex, crypto

---

## 6. Engine Version History

| Version | Changes |
|---------|---------|
| 1.0.0 | Initial implementation |
| 1.1.0 | Benchmark changed from Model B (frictionless close-price) to Model A (execution-consistent); benchmark return base changed to initial_cash; buy-and-hold signal_date set to null for initial allocation; annualization guards updated to require 252 trading days |

Runs generated with engine version 1.0.0 are not directly comparable to runs generated with 1.1.0 without rerunning.

---

## 7. Verification

**Test command**: `cd backend && venv/bin/python -m pytest tests/ -v`
**Test count**: 38 passed (29 engine + 9 API)
**Date verified**: 2026-10-11

Manual acceptance: see `docs/phase1-manual-acceptance.md`.
