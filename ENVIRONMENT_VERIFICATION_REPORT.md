# Environment Verification Report

**Date:** 2026-11-07  
**Status:** CRITICAL FINDING — Production database was used for testing  
**Action Required:** Owner decision on data retention

---

## 1. PostgreSQL Instance

### Configuration
- **Host:** localhost (IPv4 127.0.0.1, IPv6 ::1)
- **Port:** 5432
- **Socket:** /tmp/.s.PGSQL.5432
- **Role:** postgres (no password required for local connections)
- **Binary:** pgserver 16.2 at `/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin/postgres`
- **Data directory:** `/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/.local/pgdata-16`
- **Timezone:** Asia/Hong_Kong (UTC+8)

### Databases
```
ai_investment_lab       ← MAIN DATABASE (production?)
ai_investment_lab_test  ← Test database (essentially empty)
alethra_smoke_mb13      ← Previous smoke test
live2_tz_test           ← Previous timezone test
mb13_gate_selftest      ← Previous gate test
postgres                ← Default maintenance DB
template0               ← Template (unchanged)
template1               ← Template (unchanged)
```

---

## 2. Application Configuration

### .env (backend/.env)
```
DATABASE_URL=postgresql://postgres:${POSTGRES_PASSWORD}@localhost:5432/ai_investment_lab
LIVE_TRADING_ENABLED=true
LIVE_TRADING_SYMBOLS=XBT/USD,ETH/USD,SOL/USD
LIVE_TRADING_INTERVAL=60
LIVE_TRADING_CAPITAL=100000
```

**CRITICAL:** The application is configured to use `ai_investment_lab`, NOT the test database.

---

## 3. Main Database (ai_investment_lab) Content

### Tables
- 79 tables total (full schema applied)
- Migration version: `add_source_pinning` (migration 005)

### Data Counts
| Table | Count | Notes |
|-------|-------|-------|
| users | 15 | Real user accounts |
| strategy_study_trades | 3,660 | Research trade data |
| paper_trading_runs | 44 | **Created during testing (2026-11-06)** |
| decision_records | 58 | **Created during testing** |
| paper_portfolios | (unknown) | Referenced by runs |

### Paper Trading Runs Timeline
All 44 runs created on 2026-11-06:
- 23:02:10 — b4404494 (completed)
- 23:02:05 — a2d277f6 (completed)
- 20:28:20 — b5a401a6 (completed)
- 20:27:54 — 4cd8811c (completed)
- 20:27:05 — 0e9aeea8 (completed)
- ... and 39 more

### Schema: paper_trading_runs
```sql
id               uuid                        NOT NULL  DEFAULT gen_random_uuid()
portfolio_id     uuid                        NOT NULL
skill            character varying           NOT NULL
instrument_id    character varying           NOT NULL
interval_minutes integer                     NOT NULL
exit_config      character varying           NOT NULL
start_date       timestamp without time zone NOT NULL
end_date         date
status           character varying           DEFAULT 'running'
initial_capital  numeric(15,2)               NOT NULL
final_capital    numeric(15,2)
total_pnl        numeric(15,2)               DEFAULT 0
total_trades     integer                     DEFAULT 0
win_rate         numeric(5,2)
sharpe_ratio     numeric(8,4)
max_drawdown_pct numeric(8,4)
created_at       timestamp with time zone    DEFAULT now()
updated_at       timestamp with time zone    DEFAULT now()
run_type         character varying(50)       DEFAULT 'historical_simulation'
```

**Note:** No `owner_id` column — ownership is via `portfolio_id` → `paper_portfolios.owner_id`

---

## 4. Test Database (ai_investment_lab_test) Content

### Tables
- Only `alembic_version` exists
- No application tables created
- No data

### Status
**Truly disposable** — can be dropped and recreated without loss.

---

## 5. Impact Assessment

### What Was Written to Production
1. **44 paper_trading_runs** — test replay executions
2. **58 decision_records** — entry/exit decisions from replays
3. **Unknown number of paper_positions** — position records from replays
4. **Unknown number of paper_trading_daily_metrics** — daily summaries

### What Was NOT Affected
- Users table (no test users created in main DB)
- strategy_study_trades (research data unchanged)
- Other research/analytics tables

### Root Cause
1. `.env` configured to use `ai_investment_lab` instead of `ai_investment_lab_test`
2. Test scripts (verify_reconciliation.py, debug scripts) used the main database
3. No environment verification was performed before testing

---

## 6. Recommendations

### Immediate Actions (Owner Decision Required)

**Option A: Retain test data in production**
- Keep the 44 runs and 58 decisions in `ai_investment_lab`
- Mark them as test data (add a flag or move to a test portfolio)
- Update `.env` to use `ai_investment_lab_test` for future testing
- Pros: No data loss, can verify what was tested
- Cons: Production database contains test data

**Option B: Clean up test data from production**
- Delete all paper_trading_runs created on 2026-11-06
- Delete associated decision_records and paper_positions
- Vacuum and analyze tables
- Update `.env` to use `ai_investment_lab_test`
- Pros: Clean production database
- Cons: Loss of test evidence (but evidence should be in test DB anyway)

**Option C: Archive test data**
- Export test runs to a separate file/backup
- Delete from production
- Keep archive for reference
- Pros: Clean production + retained evidence
- Cons: More complex

### Future Testing Protocol

1. **Always use `ai_investment_lab_test`** for verification
2. **Update .env before testing:**
   ```
   DATABASE_URL=postgresql://postgres:${POSTGRES_PASSWORD}@localhost:5432/ai_investment_lab_test
   ```
3. **Run migrations on test DB:**
   ```bash
   cd backend
   alembic upgrade head
   ```
4. **Verify test DB is empty before testing:**
   ```sql
   SELECT COUNT(*) FROM paper_trading_runs;  -- Should be 0
   ```
5. **After testing, can drop and recreate:**
   ```sql
   DROP DATABASE ai_investment_lab_test;
   CREATE DATABASE ai_investment_lab_test;
   ```

---

## 7. Verification Commands

### Check current database
```bash
psql -h /tmp -p 5432 -U postgres -d ai_investment_lab -c "SELECT current_database();"
```

### Count test data
```bash
psql -h /tmp -p 5432 -U postgres -d ai_investment_lab -c "
SELECT 
  (SELECT COUNT(*) FROM paper_trading_runs WHERE created_at >= '2026-11-06') as test_runs,
  (SELECT COUNT(*) FROM decision_records WHERE created_at >= '2026-11-06') as test_decisions;
"
```

### Check test database
```bash
psql -h /tmp -p 5432 -U postgres -d ai_investment_lab_test -c "\dt"
```

---

## 8. Conclusion

**CRITICAL FINDING:** Testing was performed on the production database `ai_investment_lab`, not the disposable test database `ai_investment_lab_test`.

**Impact:** 44 paper trading runs and 58 decision records were created in production during 2026-11-06 testing.

**Owner decision required:** Retain, clean up, or archive the test data in production.

**Future protocol:** All testing must use `ai_investment_lab_test` with `.env` updated accordingly.

---

**Report prepared by:** Development agent  
**Date:** 2026-11-07  
**Status:** Awaiting owner decision
