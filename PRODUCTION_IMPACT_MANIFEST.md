# Production Database Impact Manifest

**Date:** 2026-11-07  
**Database:** ai_investment_lab (localhost:5432)  
**Status:** Read-only inventory complete  
**Migration version:** add_source_pinning

---

## Executive Summary

**Total entities discovered:**
- 44 paper_trading_runs (37 owner, 7 test agent)
- 44 paper_portfolios
- 58 decision_records
- 26 paper_positions
- 32 paper_trading_daily_metrics
- 58 paper_orders
- 15 users (4 real, 2 test, 9 debug)

**Critical finding:** Owner created 25 runs marked `live_trading` on 2026-11-06 12:14-12:29. These are NOT test data.

---

## 1. User Attribution

### Real Users (Owner's accounts)
| ID | Email | Created | Status |
|---|---|---|---|
| 96f9ea41-5a7a-403c-8d9b-decdc8cb54a5 | tommylam0915@gmail.com | 2026-11-05 01:13 | Active, not admin |
| 0809830a-f7ee-45b6-b3ba-4d4d8552bf67 | owner@alethra.local | 2026-11-05 12:12 | Active |
| c839e673-e74a-4ff7-a564-9dd5b92a9af5 | tester@alethra.local | 2026-11-05 12:12 | Active |
| b1af1539-dc2e-48b6-8bc3-8a2416b71e67 | customer@alethra-demo.com | 2026-11-06 11:25 | Active |

### Test Users (Pre-existing)
| ID | Email | Created | Purpose |
|---|---|---|---|
| 7d52d7c4-4cf7-4372-a0ef-75aa7bc75501 | test@example.com | 2026-11-05 16:04 | General testing |
| d7c41b94-3c75-4247-8772-a558d8482dd2 | uitest@example.com | 2026-11-05 16:14 | UI testing |

### Debug Users (Created by test agent on 2026-11-06)
| ID | Email | Created | Attribution |
|---|---|---|---|
| 1b28590e-1451-4d28-bec4-22018aa71aeb | debug_1b28590e@test.com | 2026-11-06 20:26 | **Test agent** |
| 01136320-6d12-4f03-bce9-b157d7fa4e00 | debug_01136320@test.com | 2026-11-06 20:26 | **Test agent** |
| f5a98977-2f31-4493-b46a-c850b87d7371 | debug_f5a98977@test.com | 2026-11-06 20:27 | **Test agent** |
| 39d5c4e6-1b74-4171-911c-37ff5ed799b6 | debug_39d5c4e6@test.com | 2026-11-06 20:27 | **Test agent** |
| a5d2e1b8-77a7-4bda-a703-98248cd7b7b4 | debug_a5d2e1b8@test.com | 2026-11-06 20:28 | **Test agent** |
| fd29acce-673a-4461-9c57-6371a6f4bae0 | debug_fd29acce@test.com | 2026-11-06 23:02 | **Test agent** |
| 8c2ff7d2-9f9e-4010-9074-a750490f29e1 | debug_8c2ff7d2@test.com | 2026-11-06 23:02 | **Test agent** |
| 71dd9b09-48fb-4537-b853-8e5a49cbfa10 | test-71dd9b09...@example.com | 2026-11-06 23:04 | **Test agent** |
| 8a486ce1-f7d5-4472-b0f5-bebe8362e1ce | test-8a486ce1...@example.com | 2026-11-06 23:04 | **Test agent** |

**Confidence:** Debug users are confirmed test agent creation (pattern: debug_<uuid>@test.com, created during test sessions).

---

## 2. Paper Trading Runs — Complete Attribution

### Category A: Owner's Historical Simulation (12 runs)
**Time:** 2026-11-06 12:04-12:05  
**Owner:** tommylam0915@gmail.com  
**Run type:** historical_simulation  
**Status:** Mixed (some completed, some running)

| Run ID | Skill | Status | Trades | PnL |
|---|---|---|---|---|
| cac008cc-c6b1-4506-b574-2e7ce87fb739 | 短期超買超賣 | completed | 0 | 0.00 |
| 027b5605-be58-4666-bbc2-df5e151556d7 | K線內套利 | completed | 0 | 0.00 |
| 47068100-0c64-4423-9e91-19ff5feb0646 | 布林帶均值回歸 | running | 0 | 0.00 |
| b3771b9b-f376-4ce0-b5bc-cc8bf40b13c4 | 短期超買超賣 | completed | 0 | 0.00 |
| 6f803e96-3996-48e0-a6ef-eac51b31acfd | K線內套利 | completed | 0 | 0.00 |
| 58ffca8a-1962-4c58-b9a7-bbb58646e27a | 布林帶均值回歸 | running | 0 | 0.00 |
| d0e8fce5-795c-43a4-9190-21be93cad001 | 短期超買超賣 | running | 0 | 0.00 |
| 39cc7f3d-63a0-4424-b693-bdcb2950f944 | K線內套利 | completed | 0 | 0.00 |
| 9b95feab-e172-44b0-b188-62765afd8e61 | 布林帶均值回歸 | running | 0 | 0.00 |
| df57c547-3373-414d-ab38-18282a10f3f2 | 短期超買超賣 | completed | 3 | 3896.48 |
| f3dbb275-0d29-4328-b84d-72e610ab460d | K線內套利 | completed | 0 | 0.00 |
| 6604070d-c3bc-4faf-aa97-265bed4496e9 | 布林帶均值回歸 | completed | 3 | 3725.33 |

**Attribution:** Owner's legitimate use. **DO NOT DELETE.**

### Category B: Owner's Live Trading Attempts (25 runs)
**Time:** 2026-11-06 12:14-12:29  
**Owner:** tommylam0915@gmail.com  
**Run type:** live_trading  
**Status:** All running (no completion)

| Run ID | Skill | Created |
|---|---|---|
| 34602be3-226e-4077-ba37-ccbb70e961fe | 短期超買超賣 | 12:14:33 |
| bdcf96f6-271c-43bc-9d9a-a9436cb358d0 | K線內套利 | 12:14:33 |
| 530b87f4-5a72-4a3e-984d-c48afd3fb762 | 布林帶均值回歸 | 12:14:33 |
| 0a8ae592-3fdd-42af-bdb1-dcbe69c890c4 | RSI 極值回歸 | 12:14:33 |
| 5c66b73b-be49-4377-b2fb-8670b75c4164 | K線內套利 | 12:14:33 |
| 0a958ae7-f9a3-4cb0-8037-de79b818a98f | 短期超買超賣 | 12:16:39 |
| 94c18b43-78e1-480c-a352-33ba8d54801b | K線內套利 | 12:16:39 |
| d180932e-ee6b-4d80-aa79-906f482685f6 | 布林帶均值回歸 | 12:16:39 |
| 5ded7d12-b4b5-44dd-a16a-c417a9913e45 | RSI 極值回歸 | 12:16:39 |
| 7c24c4e8-b525-40c7-a5bb-79070da0b873 | K線內套利 | 12:16:39 |
| 26d9e2f7-425d-4eba-b5c3-17d0fa9c2c26 | 短期超買超賣 | 12:18:20 |
| 87597162-fab8-4708-bf78-63df116e76d2 | K線內套利 | 12:18:20 |
| 68ddddf0-63a7-462d-8379-f57a303c7e1b | 布林帶均值回歸 | 12:18:20 |
| e2f04ae8-7d5d-4e90-b13b-245d343896bb | RSI 極值回歸 | 12:18:20 |
| b8b9a043-a8c8-41df-960f-84333c2b1142 | K線內套利 | 12:18:20 |
| 7d8fb513-574b-4537-9003-19c89f4a74dc | 短期超買超賣 | 12:20:51 |
| a25fefd8-f5c4-46e4-b020-dfba4c55bef1 | K線內套利 | 12:20:51 |
| 193cc6d7-b7cd-4050-8d00-2e943bca3a4c | 布林帶均值回歸 | 12:20:51 |
| 91e9f95c-ff59-4d44-8bf3-26bea7658973 | RSI 極值回歸 | 12:20:51 |
| 56a1d491-65cc-4751-ac9e-beb4646a96f4 | K線內套利 | 12:20:51 |
| 502995c2-9ce8-452c-b4fb-f8af1b946f7e | 短期超買超賣 | 12:29:25 |
| 19c14546-e9b2-473b-a439-dfff20a16a8f | K線內套利 | 12:29:25 |
| ce2c7a33-f78e-4b07-8901-d0e8524db97d | 布林帶均值回歸 | 12:29:25 |
| 52dbf5fe-21bc-485f-9fb1-7b971ebd1761 | RSI 極值回歸 | 12:29:25 |
| c5e40d44-3799-4393-8ae8-31380d36b686 | K線內套利 | 12:29:25 |

**Attribution:** Owner's live trading attempts. **DO NOT DELETE — requires owner decision.**

**Critical question:** Were these connected to real Kraken? Need to check:
- crypto_paper_executions (0 rows — no actual executions)
- External API logs
- WebSocket connections

### Category C: Test Agent Data (7 runs)
**Time:** 2026-11-06 20:26-23:02  
**Owner:** debug_*@test.com users  
**Run type:** historical_simulation  
**Skill:** test_rule (not a real skill)

| Run ID | Owner Email | Trades | PnL | Created |
|---|---|---|---|---|
| 0f78e568-e065-4a80-aacd-ee89543dbf3d | debug_1b28590e@test.com | 2 | 504.90 | 20:26:33 |
| 7b97b16f-0b75-4316-8aa0-29d69d5f5e96 | debug_01136320@test.com | 2 | 504.90 | 20:26:51 |
| 0e9aeea8-d18e-4b54-9256-da68f792567e | debug_f5a98977@test.com | 2 | 504.90 | 20:27:05 |
| 4cd8811c-c14e-462c-9083-307f05bee483 | debug_39d5c4e6@test.com | 5 | 1927.77 | 20:27:54 |
| b5a401a6-a12e-44a9-9b30-9751768a23f7 | debug_a5d2e1b8@test.com | 5 | 1927.77 | 20:28:20 |
| a2d277f6-5e8f-4c04-ab2e-a35277602982 | debug_fd29acce@test.com | 5 | 1772.66 | 23:02:05 |
| b4404494-ece8-4458-b83e-7a600f432625 | debug_8c2ff7d2@test.com | 5 | 1772.66 | 23:02:10 |

**Attribution:** Confirmed test agent creation. **CANDIDATE FOR CLEANUP.**

---

## 3. Related Data Attribution

### Decision Records (58 total)
- Linked to runs via source_run_id
- Category A runs: ~12 decisions (estimated)
- Category B runs: 0 decisions (no trades executed)
- Category C runs: ~46 decisions (7 runs × ~6-7 decisions each)

### Paper Positions (26 total)
- Opened by Category A and C runs
- Category B: 0 positions (no trades)
- Need to query: `SELECT source_trade_id, portfolio_id FROM paper_positions` to attribute

### Paper Orders (58 total)
- Linked to decisions via decision_id
- Same attribution as decision records

### Daily Metrics (32 total)
- Linked to runs via run_id
- Attribution follows run ownership

---

## 4. Schema Changes — Migrations Applied

**Current version:** add_source_pinning

**Migrations applied (inferred from version):**
1. Initial schema (users, portfolios, trades, etc.)
2. Paper trading tables (paper_trading_runs, paper_positions, etc.)
3. Source pinning columns (add_source_pinning)

**Schema changes by test agent:**
- Migration 005_add_source_pinning.py was modified after initial application
- Unique constraints added: uq_decision_records_source_trade_type, uq_paper_positions_source_trade
- **Issue:** Migration modified post-application creates audit gap

**Need to verify:**
- Compare migration files to actual schema
- Check if any test migrations were applied to production
- Document all constraint/index changes

---

## 5. Active Processes & Connections

**PostgreSQL:** Running (PID 1811)
- Binary: pgserver 16.2
- Data: /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/.local/pgdata-16
- Listening: localhost:5432, /tmp/.s.PGSQL.5432

**Application processes:** NONE
- No Python/Node Alethra processes running
- No live trading daemons active
- No scheduler processes

**WebSocket connections:** Cannot verify from database alone
- Need to check application logs
- Need to check Kraken API connection history

---

## 6. Data Classification Summary

| Category | Runs | Portfolios | Users | Classification | Action |
|---|---|---|---|---|---|
| A: Owner historical | 12 | 12 | 1 | **Confirmed legitimate** | RETAIN |
| B: Owner live attempts | 25 | 25 | 1 | **Owner data, unknown status** | RETAIN (owner decision) |
| C: Test agent | 7 | 7 | 9 | **Confirmed test data** | CLEANUP (after approval) |
| Pre-existing test | 0 | 0 | 2 | **Legacy test users** | UNKNOWN (owner decision) |

**Total confirmed test data:**
- 7 runs
- 7 portfolios
- 9 debug users
- ~46 decisions (estimated)
- ~20 positions (estimated)
- ~46 orders (estimated)
- ~7 daily metrics (estimated)

---

## 7. Evidence Gaps

**Cannot determine from database alone:**
1. Were Category B runs connected to real Kraken?
2. What external API calls were made?
3. Were there any actual live trades executed?
4. What research data was used?
5. Were there any data exports or downloads?

**Need additional investigation:**
- Application logs (if any exist)
- Kraken API connection logs
- Network connection history
- File system artifacts (exports, backups)

---

## 8. Recommendations

### Immediate (no approval needed)
1. **Do not delete any data** — all runs require owner decision
2. **Do not modify .env** — preserve current configuration
3. **Do not run migrations** — schema is at known state
4. **Document this manifest** — commit as evidence

### Short-term (owner approval required)
1. **Investigate Category B** — determine if live trading actually occurred
2. **Propose cleanup scope** — exact IDs for Category C data
3. **Archive plan** — where to store test data before deletion
4. **Test environment** — build isolated PostgreSQL for future testing

### Long-term (after cleanup)
1. **Re-run verification** — on isolated test database
2. **Update delivery report** — with correct evidence
3. **Complete acceptance** — tasks #137-144 on clean environment

---

## 9. Verification Commands

### Count by category
```sql
-- Category A: Owner historical
SELECT COUNT(*) FROM paper_trading_runs 
WHERE portfolio_id IN (
  SELECT id FROM paper_portfolios 
  WHERE owner_id = '96f9ea41-5a7a-403c-8d9b-decdc8cb54a5'
) AND run_type = 'historical_simulation' AND created_at < '2026-11-06 12:10';

-- Category B: Owner live
SELECT COUNT(*) FROM paper_trading_runs 
WHERE portfolio_id IN (
  SELECT id FROM paper_portfolios 
  WHERE owner_id = '96f9ea41-5a7a-403c-8d9b-decdc8cb54a5'
) AND run_type = 'live_trading';

-- Category C: Test agent
SELECT COUNT(*) FROM paper_trading_runs 
WHERE portfolio_id IN (
  SELECT id FROM paper_portfolios 
  WHERE owner_id LIKE ANY(ARRAY[
    '1b28590e-1451-4d28-bec4-22018aa71aeb'::uuid,
    '01136320-6d12-4f03-bce9-b157d7fa4e00'::uuid,
    'f5a98977-2f31-4493-b46a-c850b87d7371'::uuid,
    '39d5c4e6-1b74-4171-911c-37ff5ed799b6'::uuid,
    'a5d2e1b8-77a7-4bda-a703-98248cd7b7b4'::uuid,
    'fd29acce-673a-4461-9c57-6371a6f4bae0'::uuid,
    '8c2ff7d2-9f9e-4010-9074-a750490f29e1'::uuid
  ])
);
```

### Export for archive
```bash
# Export Category C runs
psql -h /tmp -p 5432 -U postgres -d ai_investment_lab -c "
COPY (
  SELECT ptr.*, pp.owner_id, u.email
  FROM paper_trading_runs ptr
  JOIN paper_portfolios pp ON pp.id = ptr.portfolio_id
  JOIN users u ON u.id = pp.owner_id
  WHERE u.email LIKE 'debug_%@test.com'
) TO '/tmp/category_c_runs.csv' WITH CSV HEADER;
"
```

---

## 10. Conclusion

**Impact scope:**
- 7 runs confirmed as test agent data (Category C)
- 37 runs are owner data (Categories A + B)
- 25 owner runs marked live_trading — status unknown
- Schema at known migration version
- No active application processes

**Next steps:**
1. Owner decides on Category B (live trading attempts)
2. Owner approves Category C cleanup scope
3. Build isolated test environment
4. Re-run verification on clean database

**Manifest prepared by:** Development agent  
**Date:** 2026-11-07  
**Status:** Awaiting owner review and approval
