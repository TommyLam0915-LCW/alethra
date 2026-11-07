# Gate 1 Delivery Evidence — SHA fec7c9b

**Date:** 2026-11-07  
**Local SHA:** `fec7c9b8ab71fb382c30d6b419cea13e1843b9c6`  
**Remote SHA:** `890ef4c2f584eaffa2dfe30dc7f9f185b22b8b06` (live1-impl, not updated with local changes)

---

## 一、Collect-Only 真實清單（17 tests）

```
tests/test_paper_trading_cancel_atomic.py::TestSessionFactoryContract::test_check_cancel_requested_calls_factory
tests/test_paper_trading_cancel_atomic.py::TestSessionFactoryContract::test_cancel_run_calls_factory_on_not_found
tests/test_paper_trading_cancel_atomic.py::TestSessionFactoryContract::test_multiple_operations_reuse_factory
tests/test_paper_trading_cancel_atomic.py::TestSessionFactoryContract::test_legacy_database_url_creates_working_factory
tests/test_paper_trading_cancel_atomic.py::TestSessionFactoryContract::test_no_parameters_falls_back_to_global_with_warning
tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_phase1_cancel_requested_running_state
tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_phase2_worker_completes_cancel
tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_cancel_requested_blocks_execution_acquisition
tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_double_cancel_idempotent
tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_cancel_after_completed_rejected
tests/test_paper_trading_cancel_atomic.py::TestCancelConvergence::test_cancel_queued_completes_immediately
tests/test_paper_trading_cancel_atomic.py::TestCancelConvergence::test_cancel_pending_completes_immediately
tests/test_paper_trading_cancel_atomic.py::TestCancelConvergence::test_cancel_failed_completes_immediately
tests/test_paper_trading_cancel_atomic.py::TestCompletionFailureProtection::test_completion_handler_respects_cancel
tests/test_paper_trading_cancel_atomic.py::TestCompletionFailureProtection::test_failure_handler_respects_cancel
tests/test_paper_trading_cancel_atomic.py::TestRaceConditionsWithBarrier::test_concurrent_cancel_with_barrier
tests/test_paper_trading_cancel_atomic.py::TestRaceConditionsWithBarrier::test_cancel_vs_completion_race
```

**總計：** 17 tests
- TestSessionFactoryContract: 5 tests（無 DB）
- TestTwoPhaseCancelContract: 5 tests（需 DB）
- TestCancelConvergence: 3 tests（需 DB）
- TestCompletionFailureProtection: 2 tests（需 DB）
- TestRaceConditionsWithBarrier: 2 tests（需 DB）

---

## 二、Implemented / Tested / Pending / Blocked

### ✓ Implemented（代碼完成）

1. **Two-phase cancel contract**
   - Phase 1: cancel_requested=TRUE, status unchanged for running
   - Phase 2: worker confirms stopped, sets status='cancelled'
   - queued/pending/failed complete immediately (no worker)

2. **Execution acquisition protection**
   - Rejects cancel_requested=TRUE
   - WHERE clause: `AND cancel_requested = FALSE`

3. **Completion handler protection**
   - Does not override cancel status
   - WHERE clause: `AND cancel_requested = FALSE`

4. **Failure handler protection**
   - Does not override cancel status
   - WHERE clause: `AND cancel_requested = FALSE`

5. **Worker terminal state handling**
   - Exception after cancel_requested checks flag
   - Sets status='cancelled' if cancel_requested=TRUE
   - Sets status='failed' if cancel_requested=FALSE
   - Prevents permanent 'running' state

6. **Session factory injection**
   - PaperTradingEngine accepts session_factory parameter
   - All 6 SessionLocal() calls replaced with self.session_factory()
   - Service actually uses injected factory (not cosmetic)

7. **Target guard**
   - Verifies DB/role/server before any write
   - Fails closed if mismatch
   - No fallback URLs

8. **Fixture/cleanup**
   - Uses real schema (all required columns)
   - Registers finalizer early (request.addfinalizer)
   - Uses new session for cleanup
   - Uses independent connection for zero-residual verification
   - Correct FK columns (decision_records.paper_portfolio_id)

9. **Migration compatibility**
   - Single head (c9d0e1f2a3b4)
   - Linear chain: rm_residual → drop_old → add_sacrificial → c9d0e1f2a3b4
   - Honest analysis (no unproven guarantees)

### ✓ Tested（本地驗證，無 DB）

**5 contract tests pass:**
```bash
$ python3 -m pytest tests/test_paper_trading_cancel_atomic.py::TestSessionFactoryContract -v
======================== 5 passed in 0.68s =========================
```

1. test_check_cancel_requested_calls_factory ✓
2. test_cancel_run_calls_factory_on_not_found ✓
3. test_multiple_operations_reuse_factory ✓
4. test_legacy_database_url_creates_working_factory ✓
5. test_no_parameters_falls_back_to_global_with_warning ✓

**Migration graph verified:**
```bash
$ alembic heads
c9d0e1f2a3b4 (head)
```

### ⚠️ Pending（需要 DB 批准）

**12 DB tests not executed:**
- TestTwoPhaseCancelContract (5 tests)
- TestCancelConvergence (3 tests)
- TestCompletionFailureProtection (2 tests)
- TestRaceConditionsWithBarrier (2 tests)

**Reason:** ALETHRA_PG_TEST_URL not set, requires Owner DDL approval

**Real replay tests not implemented:**
- Actual execute_historical_replay calls
- cancel-first / completion-first scenarios
- cancel vs failure scenarios
- polling boundary / final commit protection
- ledger and cash consistency

**Barrier test assertions not precise:**
- Currently: assert success_count >= 1
- Should: assert all threads verified and stopped

### ✗ Blocked（需要 Owner 決策）

**DDL approval required:**
- Execute `alembic upgrade head` on test DB
- Verify migration success
- Set ALETHRA_PG_TEST_URL environment variable
- Execute 12 DB tests

**DB state verification required:**
- Query alembic_version on test DB
- Confirm dual-revision state
- Verify schema matches expectations

**Owner restrictions:**
- No main DB operations
- No downgrade/stamp
- No force push/merge
- No manual alembic_version table changes

---

## 三、精確 DDL / Runtime Role / DB / Migration / Cleanup

### DDL Role
- **Name:** `[Owner 指定]`
- **Purpose:** Execute migration DDL operations
- **Privileges:** CREATE, ALTER, DROP on test DB

### Runtime Role
- **Name:** `alethra_test_runtime`
- **Purpose:** Execute tests (SELECT, INSERT, UPDATE, DELETE)
- **Privileges:** DML on test tables

### Target DB
- **Name:** `ai_investment_lab_test`
- **Host:** `localhost`
- **Port:** `5432`
- **Socket:** `/tmp/.s.PGSQL.5432`

### Migration Path
**Current state (據稱，未驗證):**
```
alembic_version:
  - add_sacrificial_test_table
  - rm_residual_global_constraints
```

**Target state:**
```
alembic_version:
  - c9d0e1f2a3b4
```

**Migrations to apply:**
1. `drop_old_source_constraints` (007b)
   - DDL: DROP CONSTRAINT IF EXISTS (2x)
   - Risk: Unknown (not verified)

2. `c9d0e1f2a3b4` (cancel_requested)
   - DDL: ALTER TABLE ADD COLUMN + CREATE INDEX
   - Risk: Unknown (not verified)

**Command (not executed):**
```bash
export DATABASE_URL="postgresql://[DDL_ROLE]:[PASSWORD]@localhost:5432/ai_investment_lab_test"
cd backend
.venv/bin/alembic -c alembic.ini upgrade head
```

### Cleanup Scope
**Per-test resources:**
- 1 user (UUID)
- 1 portfolio (UUID)
- 1 run (UUID)

**Cleanup order (FK-safe):**
1. paper_fills (child of paper_orders)
2. paper_orders (FK: portfolio_id)
3. paper_positions (FK: portfolio_id)
4. decision_records (FK: paper_portfolio_id)
5. paper_trading_daily_metrics (FK: run_id)
6. paper_trading_runs (FK: portfolio_id)
7. paper_portfolios (FK: owner_id)
8. users

**Verification:**
- New independent connection
- COUNT(*) = 0 for all tables
- Assert zero residual

---

## 四、未證實保證（已移除）

**不再宣稱：**
- ~~no-op~~（未驗證）
- ~~可逆~~（未驗證）
- ~~無 downtime~~（未驗證）
- ~~所有 migrations idempotent~~（未驗證）
- ~~低風險~~（未驗證）
- ~~migration 已套用~~（實際未執行）
- ~~DB 測試已執行~~（實際未執行）
- ~~17 tests pass~~（實際只有 5 個通過）

---

## 五、誠實評估

**✓ 已驗證（本地）：**
- 5 個契約測試通過
- Migration graph 單一 head
- Session factory 注入正確
- Target guard 邏輯實作
- Fixture/cleanup 修正
- Worker 終態處理
- Migration 相容分析（誠實版）

**✗ 未驗證（需要 DB）：**
- 12 個 DB 測試
- 實際 migration 執行
- Fixture/cleanup 實際運作
- Target guard 實際效果
- Worker 終態實際行為
- 實際 replay 測試

**⚠️ 重要提醒：**
- 所有 DB 狀態資訊來自 Owner，未經獨立驗證
- 所有 migration DDL 操作未經實際執行
- 所有風險評估為理論推導，未經實證
- 雙 revision 狀態可能導致 alembic 行為不確定

---

**End of Delivery Evidence — SHA fec7c9b**

**Next steps:**
1. Owner decides: approve DDL or verify DB state first
2. If approved: execute migration, set env var, run DB tests
3. If issues: debug and fix before proceeding
4. Update this document with actual results
