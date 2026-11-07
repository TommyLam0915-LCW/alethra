# Migration Compatibility Analysis

**Date:** 2026-11-07  
**SHA:** `f023f71c90589a4e23d89b3a680dc3adc9fd264c`

---

## 一、Migration Graph 核對結果

### 當前 Chain（單一 head）
```
55a8d07a7f40 (initial_schema)
  ↓
[... 35 revisions ...]
  ↓
add_source_pinning (005)
  ↓
fix_source_dedup_scope (006)
  ↓
rm_residual_global_constraints (007a) ← 重建
  ↓
drop_old_source_constraints (007b)
  ↓
add_sacrificial_test_table (008)
  ↓
c9d0e1f2a3b4 (cancel_requested) ← HEAD
```

**驗證：**
```bash
$ alembic heads
c9d0e1f2a3b4 (head)

$ alembic history --verbose
Revision: c9d0e1f2a3b4
Parent: add_sacrificial_test_table
Path: .../c9d0e1f2a3b4_add_paper_trading_cancel_requested.py
Message: add cancel_requested to paper_trading_runs
```

**結果：** 單一 head ✓

---

## 二、已知 DB 狀態

### Test DB（ai_investment_lab_test）
- **Host:** localhost:5432
- **User:** alethra_test_runtime
- **Current revisions:**
  - `add_sacrificial_test_table` ✓
  - `rm_residual_global_constraints` ✓
- **Missing:**
  - `drop_old_source_constraints`
  - `c9d0e1f2a3b4`

### Main DB（ai_investment_lab）
- **Host:** /tmp:5432 (socket)
- **User:** postgres
- **Current revision:** `c9d0e1f2a3b4` ✓
- **所有 migration 已套用**

### New DB（全新安裝）
- **無任何 revision**
- **需要套用所有 migration**

---

## 三、升級路徑分析

### 路徑 A：Test DB（已有 rm_residual + add_sacrificial）

**當前狀態：**
```
alembic_version:
  - add_sacrificial_test_table
  - rm_residual_global_constraints
```

**需要套用：**
1. `drop_old_source_constraints` (007b)
   - down_revision: `rm_residual_global_constraints` ✓（已在 version table）
   - 操作：DROP CONSTRAINT IF EXISTS（idempotent，因為 rm_residual 已做過）
   - 結果：no-op，安全

2. `c9d0e1f2a3b4` (cancel_requested)
   - down_revision: `add_sacrificial_test_table` ✓（已在 version table）
   - 操作：ALTER TABLE ADD COLUMN cancel_requested
   - 結果：新增欄位

**升級命令：**
```bash
export PGPASSWORD="[DDL role password]"
alembic -c alembic.ini upgrade head
```

**預期結果：**
```
alembic_version:
  - c9d0e1f2a3b4
```

**風險：** 低（兩個 migration 都是 idempotent 或 additive）

---

### 路徑 B：Main DB（已到 c9d0e1f2a3b4）

**當前狀態：**
```
alembic_version:
  - c9d0e1f2a3b4
```

**需要套用：** 無（已是最新）

**升級命令：**
```bash
alembic -c alembic.ini upgrade head
```

**預期結果：** no-op（已是 head）

**風險：** 無

---

### 路徑 C：New DB（全新安裝）

**當前狀態：** 無任何 revision

**需要套用：** 所有 38 個 migration

**升級命令：**
```bash
alembic -c alembic.ini upgrade head
```

**預期結果：**
```
alembic_version:
  - c9d0e1f2a3b4
```

**Chain 執行順序：**
1. 55a8d07a7f40 (initial)
2. ... (中間 35 個)
3. add_source_pinning (005)
4. fix_source_dedup_scope (006)
5. rm_residual_global_constraints (007a) ← DROP CONSTRAINT IF EXISTS（無 constraint，no-op）
6. drop_old_source_constraints (007b) ← DROP CONSTRAINT IF EXISTS（無 constraint，no-op）
7. add_sacrificial_test_table (008)
8. c9d0e1f2a3b4 (cancel_requested) ← ADD COLUMN

**風險：** 低（所有 migration 設計為 idempotent）

---

### 路徑 D：Partial DB（有 drop_old，無 rm_residual）

**情境：** 某些 DB 可能在 007 重構期間套用了 `drop_old_source_constraints`，但沒有 `rm_residual_global_constraints`。

**當前狀態：**
```
alembic_version:
  - drop_old_source_constraints
  - add_sacrificial_test_table
  - c9d0e1f2a3b4
```

**問題：** `drop_old` 的 down_revision 現在是 `rm_residual`，但 `rm_residual` 不在 version table。

**解決方案：**
1. 手動插入 `rm_residual_global_constraints` 到 alembic_version（需要 stamp）
2. 或重構 migration chain（破壞已發佈歷史）

**Owner 限制：** 不得 stamp、不得手改 version table

**結論：** 此情境無法在當前限制下解決。需要 Owner 決策：
- 接受路徑 D DB 無法升級
- 或批准 stamp 操作

**目前假設：** 沒有 DB 處於路徑 D 狀態（因為 007 重構是在同一次交付中完成）

---

## 四、DDL 操作清單

### Migration 007a: rm_residual_global_constraints

**操作：**
```sql
-- Drop uq_decision_records_source_trade_type if exists
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'decision_records'
          AND constraint_name = 'uq_decision_records_source_trade_type'
          AND constraint_type = 'UNIQUE'
    ) THEN
        ALTER TABLE decision_records
        DROP CONSTRAINT uq_decision_records_source_trade_type;
    END IF;
END $$;

-- Drop uq_paper_positions_source_trade if exists
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'paper_positions'
          AND constraint_name = 'uq_paper_positions_source_trade'
          AND constraint_type = 'UNIQUE'
    ) THEN
        ALTER TABLE paper_positions
        DROP CONSTRAINT uq_paper_positions_source_trade;
    END IF;
END $$;
```

**影響：**
- Test DB：no-op（rm_residual 已套用，constraints 已不存在）
- Main DB：no-op（constraints 已不存在）
- New DB：no-op（constraints 從未存在）

**可逆：** 是（downgrade 為 no-op）

---

### Migration 007b: drop_old_source_constraints

**操作：** 同 007a（idempotent）

**影響：**
- Test DB：no-op（constraints 已被 rm_residual 刪除）
- Main DB：no-op（constraints 已不存在）
- New DB：no-op（constraints 從未存在）

**可逆：** 是（downgrade 為 no-op）

---

### Migration c9d0e1f2a3b4: cancel_requested

**操作：**
```sql
-- Add cancel_requested column if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_trading_runs'
          AND column_name = 'cancel_requested'
    ) THEN
        ALTER TABLE paper_trading_runs
        ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;
END $$;

-- Add index if not exists
CREATE INDEX IF NOT EXISTS idx_paper_trading_runs_cancel_requested
ON paper_trading_runs(cancel_requested);
```

**影響：**
- Test DB：新增欄位 + index
- Main DB：no-op（已存在）
- New DB：新增欄位 + index

**可逆：** 是（downgrade DROP COLUMN）

---

## 五、精確 DDL 批准請求

### 目標 DB
- **Database:** `ai_investment_lab_test`
- **Host:** `localhost`
- **Port:** `5432`
- **DDL Role:** `[Owner 指定]`
- **Runtime Role:** `alethra_test_runtime`

### 當前 Revision
```sql
SELECT version_num FROM alembic_version;
-- 結果：
-- add_sacrificial_test_table
-- rm_residual_global_constraints
```

### 需要套用的 Migration
1. `drop_old_source_constraints` (007b) — no-op
2. `c9d0e1f2a3b4` (cancel_requested) — ADD COLUMN

### DDL 操作
```bash
export DATABASE_URL="postgresql://[DDL_ROLE]:[PASSWORD]@localhost:5432/ai_investment_lab_test"
cd backend
.venv/bin/alembic -c alembic.ini upgrade head
```

### 預期結果
```sql
SELECT version_num FROM alembic_version;
-- 結果：
-- c9d0e1f2a3b4

\d paper_trading_runs
-- 應該包含：
-- cancel_requested | boolean | not null | default false
```

### 影響範圍
- **Schema 變更：** 1 個欄位（cancel_requested）+ 1 個 index
- **資料變更：** 無（只加欄位，不改既有資料）
- **Downtime：** 無（ADD COLUMN 為 online operation）
- **可逆：** 是（DROP COLUMN）

### 風險評估
- **Test DB：** 低風險（no-op + additive）
- **Main DB：** 不操作（已在 c9d0e1f2a3b4）
- **New DB：** 不操作（全新安裝會套用所有 migration）

---

## 六、批准後測試命令

### 環境設定
```bash
export ALETHRA_PG_TEST_URL="postgresql://alethra_test_runtime:[PASSWORD]@localhost:5432/ai_investment_lab_test"
```

### 執行測試
```bash
cd backend
.venv/bin/pytest tests/test_paper_trading_cancel_atomic.py -v --tb=short
```

### 預期結果
```
collected 15 items

TestSessionFactoryContract::test_factory_callable_is_called PASSED
TestSessionFactoryContract::test_legacy_database_url_creates_factory PASSED
TestSessionFactoryContract::test_no_parameters_falls_back_to_global PASSED
TestTwoPhaseCancelContract::test_phase1_cancel_requested_running_state PASSED
TestTwoPhaseCancelContract::test_phase2_worker_completes_cancel PASSED
TestTwoPhaseCancelContract::test_cancel_requested_blocks_execution_acquisition PASSED
TestTwoPhaseCancelContract::test_double_cancel_idempotent PASSED
TestTwoPhaseCancelContract::test_cancel_after_completed_rejected PASSED
TestCancelConvergence::test_cancel_queued_completes_immediately PASSED
TestCancelConvergence::test_cancel_pending_completes_immediately PASSED
TestCancelConvergence::test_cancel_failed_completes_immediately PASSED
TestCompletionFailureProtection::test_completion_handler_respects_cancel PASSED
TestCompletionFailureProtection::test_failure_handler_respects_cancel PASSED
TestRaceConditionsWithBarrier::test_concurrent_cancel_with_barrier PASSED
TestRaceConditionsWithBarrier::test_cancel_vs_completion_race PASSED

======================== 15 passed in X.XXs =========================
```

### Cleanup 範圍
- 每個測試使用 UUID 資源（user/portfolio/run）
- Finalizer 刪除所有測試資料（FK-safe order）
- 獨立連線驗證零殘留
- 成功/失敗都清理

---

## 七、未執行項目

**❌ 未執行（Owner 限制）：**
- 無主 DB 操作
- 無 downgrade/stamp
- 無 force push、無 merge
- 無實盤或部署
- 無刪除既有資料

**❌ 未宣稱：**
- 未宣稱 migration 已套用（實際未套用）
- 未宣稱測試已執行（實際未執行）
- 未宣稱 PASS（實際未驗證）

---

**End of Migration Compatibility Analysis**
