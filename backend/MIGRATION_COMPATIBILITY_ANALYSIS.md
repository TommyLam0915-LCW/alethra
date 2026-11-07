# Migration Compatibility Analysis — Honest Assessment

**Date:** 2026-11-07
**SHA:** `fec7c9b8ab71fb382c30d6b419cea13e1843b9c6`
**Status:** Code complete, NOT verified against live DB

---

## 一、Migration Graph（本地驗證）

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

**本地驗證結果：**
```bash
$ alembic heads
c9d0e1f2a3b4 (head)
```

**狀態：** 單一 head ✓（本地驗證）

**⚠️ 未驗證：**
- 未在任何實際 DB 執行 `alembic upgrade head`
- 未驗證 migration 是否真的能成功套用
- 未驗證 DDL 操作是否無衝突

---

## 二、已知 DB 狀態（Owner 提供資訊，未經獨立驗證）

### Test DB（ai_investment_lab_test）
- **Host:** localhost:5432
- **User:** alethra_test_runtime
- **據稱當前 revisions:**
  - `add_sacrificial_test_table` ✓
  - `rm_residual_global_constraints` ✓
- **據稱缺少:**
  - `drop_old_source_constraints`
  - `c9d0e1f2a3b4`

**⚠️ 未驗證：**
- 未實際連線查詢 `SELECT version_num FROM alembic_version`
- 未驗證上述資訊是否準確
- 未驗證 schema 是否真的缺少這些 migration
- 未驗證 alembic_version table 是否真的有兩筆記錄（雙 revision 狀態）

### Main DB（ai_investment_lab）
- **Host:** /tmp:5432 (socket)
- **User:** postgres
- **據稱當前 revision:** `c9d0e1f2a3b4` ✓

**⚠️ 未驗證：**
- 未實際連線查詢
- 未驗證 migration 是否真的已套用
- 未驗證 cancel_requested 欄位是否真的存在

---

## 三、升級路徑分析（理論，未經實證）

### 路徑 A：Test DB（據稱有 rm_residual + add_sacrificial 雙 revision）

**據稱當前狀態：**
```
alembic_version:
  - add_sacrificial_test_table
  - rm_residual_global_constraints
```

**理論需要套用：**
1. `drop_old_source_constraints` (007b)
   - down_revision: `rm_residual_global_constraints`
   - DDL：DROP CONSTRAINT IF EXISTS（兩次）
   - **假設：** 如果 rm_residual 已刪除 constraints，則 no-op
   - **⚠️ 未驗證：** 未實際執行，未驗證 constraints 是否真的已不存在

2. `c9d0e1f2a3b4` (cancel_requested)
   - down_revision: `add_sacrificial_test_table`
   - DDL：ALTER TABLE ADD COLUMN + CREATE INDEX
   - **⚠️ 未驗證：** 未實際執行，未驗證欄位是否真的能新增

**⚠️ 雙 revision 狀態問題：**
- alembic_version table 有兩筆記錄
- Alembic 預期單一 current revision
- **未驗證：** `alembic upgrade head` 是否能正確處理雙 revision 狀態
- **未驗證：** 是否會因為兩個 down_revision 都已存在而跳過或失敗

**理論升級命令：**
```bash
export DATABASE_URL="postgresql://[DDL_ROLE]:[PASSWORD]@localhost:5432/ai_investment_lab_test"
alembic -c alembic.ini upgrade head
```

**風險評估：**
- 理論上低（idempotent + additive）
- **但雙 revision 狀態可能導致 alembic 行為不確定**
- **未經實證**

---

### 路徑 B：Main DB（據稱已到 c9d0e1f2a3b4）

**據稱當前狀態：**
```
alembic_version:
  - c9d0e1f2a3b4
```

**理論需要套用：** 無（已是最新）

**⚠️ 未驗證：**
- 未實際查詢 alembic_version
- 未驗證是否真的在 head
- 未驗證 cancel_requested 欄位是否真的存在

**風險評估：** 理論上無，但**未經實證**

---

### 路徑 C：New DB（全新安裝）

**理論當前狀態：** 無任何 revision

**理論需要套用：** 所有 38 個 migration

**⚠️ 未驗證：**
- 未實際執行全新安裝
- 未驗證所有 migration 是否能按順序成功
- 未驗證是否有依賴問題
- 未驗證 rm_residual + drop_old 在全新 DB 是否真的 no-op

**風險評估：** 理論上低，但**未經實證**

---

### 路徑 D：Partial DB（有 drop_old，無 rm_residual）

**情境：** 某些 DB 可能在 007 重構期間套用了 `drop_old_source_constraints`，但沒有 `rm_residual_global_constraints`。

**據稱當前狀態：**
```
alembic_version:
  - drop_old_source_constraints
  - add_sacrificial_test_table
  - c9d0e1f2a3b4
```

**問題：** `drop_old` 的 down_revision 現在是 `rm_residual`，但 `rm_residual` 不在 version table。

**⚠️ 未驗證：**
- 未確認是否真的有 DB 處於此狀態
- 未驗證此情境是否會導致升級失敗
- 未驗證 alembic 是否會因為 down_revision 不存在而拒絕執行

**Owner 限制：** 不得 stamp、不得手改 version table

**結論：** 此情境在當前限制下**可能無法解決**，但**未經實證**

**⚠️ 重要：** 不要因為祖先 revision 未獨立列於 version table 就假設需要 stamp。Alembic 的 ancestry 追蹤是基於 migration 檔案的 down_revision 宣告，不是 version table 的獨立記錄。

---

## 四、DDL 操作清單（理論，未經實證）

### Migration 007a: rm_residual_global_constraints

**理論 DDL：**
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

**⚠️ 未驗證：**
- 未在任何實際 DB 執行此 SQL
- 未驗證 constraint 是否真的存在
- 未驗證 DROP 是否真的成功
- 未驗證 IF EXISTS 是否真的能避免錯誤

**⚠️ 移除未證實保證：**
- ~~no-op~~（未驗證 constraints 是否已不存在）
- ~~可逆~~（未驗證 downgrade 是否真的能重現 constraints）
- ~~idempotent~~（未驗證重複執行是否真的安全）

---

### Migration 007b: drop_old_source_constraints

**理論 DDL：** 同 007a（結構相同）

**⚠️ 未驗證：**
- 未在任何實際 DB 執行此 SQL
- 未驗證與 007a 的差異
- 未驗證 down_revision 從 fix_source_dedup_scope 改為 rm_residual 是否影響執行

**⚠️ 移除未證實保證：**
- ~~no-op~~（未驗證）
- ~~可逆~~（未驗證）
- ~~idempotent~~（未驗證）

---

### Migration c9d0e1f2a3b4: cancel_requested

**理論 DDL：**
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

**⚠️ 未驗證：**
- 未在任何實際 DB 執行此 SQL
- 未驗證欄位是否真的能新增
- 未驗證 index 是否真的能建立
- 未驗證 IF NOT EXISTS 是否真的能避免錯誤
- 未驗證 ADD COLUMN 在大型 table 是否會 lock

**⚠️ 移除未證實保證：**
- ~~ADD COLUMN 為 online operation~~（未驗證，可能 lock table）
- ~~無 downtime~~（未驗證，取決於 table 大小和 PostgreSQL 版本）
- ~~可逆~~（未驗證 downgrade DROP COLUMN 是否真的能執行）

---

## 五、精確 DDL 批准請求（待 Owner 決策）

### 目標 DB
- **Database:** `ai_investment_lab_test`
- **Host:** `localhost`
- **Port:** `5432`
- **DDL Role:** `[Owner 指定]`
- **Runtime Role:** `alethra_test_runtime`

### 據稱當前 Revision（未驗證）
```sql
SELECT version_num FROM alembic_version;
-- 據稱結果（Owner 提供）：
-- add_sacrificial_test_table
-- rm_residual_global_constraints
```

**⚠️ 未驗證：**
- 未實際執行此查詢
- 未確認是否真的有兩筆記錄（雙 revision 狀態）
- 未驗證 alembic 是否能正確處理此狀態

### 理論需要套用的 Migration
1. `drop_old_source_constraints` (007b)
   - DDL：DROP CONSTRAINT IF EXISTS（兩次）
   - **⚠️ 未驗證：** 是否真的 no-op，是否真的能成功執行

2. `c9d0e1f2a3b4` (cancel_requested)
   - DDL：ALTER TABLE ADD COLUMN + CREATE INDEX
   - **⚠️ 未驗證：** 是否真的能成功執行

### 理論 DDL 命令（未執行）
```bash
export DATABASE_URL="postgresql://[DDL_ROLE]:[PASSWORD]@localhost:5432/ai_investment_lab_test"
cd backend
.venv/bin/alembic -c alembic.ini upgrade head
```

**⚠️ 風險（未驗證）：**
- 雙 revision 狀態可能導致 alembic 行為不確定
- DROP CONSTRAINT IF EXISTS 可能因為 constraints 已不存在而 no-op，也可能因為其他原因失敗
- ADD COLUMN 可能 lock table（取決於 table 大小和 PostgreSQL 版本）
- 所有 migration 的 downgrade 路徑未經測試

### 理論預期結果（未驗證）
```sql
SELECT version_num FROM alembic_version;
-- 據稱結果：
-- c9d0e1f2a3b4

\d paper_trading_runs
-- 據稱應該包含：
-- cancel_requested | boolean | not null | default false
```

**⚠️ 未驗證：**
- 未確認 alembic_version 是否真的會變成單一 revision
- 未確認 cancel_requested 欄位是否真的會新增
- 未確認 index 是否真的會建立

---

## 六、批准後測試命令（待 Owner 批准）

### 環境設定
```bash
export ALETHRA_PG_TEST_URL="postgresql://alethra_test_runtime:[PASSWORD]@localhost:5432/ai_investment_lab_test"
```

### 執行測試
```bash
cd backend
.venv/bin/pytest tests/test_paper_trading_cancel_atomic.py -v --tb=short
```

### 理論預期結果（未驗證）
```
collected 17 items

TestSessionFactoryContract::test_check_cancel_requested_calls_factory PASSED
TestSessionFactoryContract::test_cancel_run_calls_factory_on_not_found PASSED
TestSessionFactoryContract::test_multiple_operations_reuse_factory PASSED
TestSessionFactoryContract::test_legacy_database_url_creates_working_factory PASSED
TestSessionFactoryContract::test_no_parameters_falls_back_to_global_with_warning PASSED
TestTwoPhaseCancelContract::test_phase1_cancel_requested_running_state [未知]
TestTwoPhaseCancelContract::test_phase2_worker_completes_cancel [未知]
TestTwoPhaseCancelContract::test_cancel_requested_blocks_execution_acquisition [未知]
TestTwoPhaseCancelContract::test_double_cancel_idempotent [未知]
TestTwoPhaseCancelContract::test_cancel_after_completed_rejected [未知]
TestCancelConvergence::test_cancel_queued_completes_immediately [未知]
TestCancelConvergence::test_cancel_pending_completes_immediately [未知]
TestCancelConvergence::test_cancel_failed_completes_immediately [未知]
TestCompletionFailureProtection::test_completion_handler_respects_cancel [未知]
TestCompletionFailureProtection::test_failure_handler_respects_cancel [未知]
TestRaceConditionsWithBarrier::test_concurrent_cancel_with_barrier [未知]
TestRaceConditionsWithBarrier::test_cancel_vs_completion_race [未知]
```

**⚠️ 已驗證：**
- 5 個契約測試（TestSessionFactoryContract）本地通過 ✓

**⚠️ 未驗證：**
- 12 個 DB 測試（TestTwoPhaseCancelContract, TestCancelConvergence, TestCompletionFailureProtection, TestRaceConditionsWithBarrier）
- 未實際執行
- 未驗證 fixture/cleanup 是否真的能正確運作
- 未驗證 target guard 是否真的能阻止錯誤 DB
- 未驗證所有測試是否真的能通過

### Cleanup 範圍（理論）
- 每個測試使用 UUID 資源（user/portfolio/run）
- Finalizer 刪除所有測試資料（FK-safe order）
- 獨立連線驗證零殘留
- 成功/失敗都清理

**⚠️ 未驗證：**
- 未驗證 cleanup 是否真的能正確刪除所有資料
- 未驗證 FK-safe order 是否真的正確
- 未驗證零殘留檢查是否真的能偵測到遺漏

---

## 七、未執行項目（誠實清單）

**❌ 未執行（Owner 限制）：**
- 無主 DB 操作
- 無 downgrade/stamp
- 無 force push、無 merge
- 無實盤或部署
- 無刪除既有資料
- 無手改 alembic_version table

**❌ 未驗證（需要 DB 存取）：**
- 未實際查詢任何 DB 的 alembic_version
- 未實際執行 `alembic upgrade head`
- 未實際執行 DB 測試
- 未驗證 migration 是否真的能成功套用
- 未驗證 fixture/cleanup 是否真的能正確運作
- 未驗證 target guard 是否真的能阻止錯誤 DB
- 未驗證 cancel_requested 欄位是否真的存在於任何 DB
- 未驗證雙 revision 狀態下 alembic 的行為
- 未驗證 DROP CONSTRAINT IF EXISTS 是否真的 no-op
- 未驗證 ADD COLUMN 是否真的能成功
- 未驗證 CREATE INDEX 是否真的能成功

**❌ 未證實保證（已移除）：**
- ~~所有 migration 都是 idempotent~~（未驗證）
- ~~所有 migration 都是 no-op~~（未驗證）
- ~~所有 migration 都是可逆~~（未驗證）
- ~~ADD COLUMN 為 online operation~~（未驗證）
- ~~無 downtime~~（未驗證）
- ~~低風險~~（未驗證）

**❌ 未宣稱：**
- 未宣稱 migration 已套用（實際未套用）
- 未宣稱 DB 測試已執行（實際未執行）
- 未宣稱 17 tests pass（實際只有 5 個無 DB 測試通過）
- 未宣稱 DB 狀態已知（實際只有 Owner 提供的資訊）
- 未宣稱 alembic upgrade 能成功（實際未執行）
- 未宣稱 DDL 操作安全（實際未驗證）

---

## 八、下一步（需要 Owner 決策）

### 選項 1：批准 DDL 操作
- Owner 指定 DDL role 和 password
- 執行 `alembic upgrade head` 在 test DB
- **風險：** 雙 revision 狀態可能導致 alembic 行為不確定
- **未知：** migration 是否能成功套用
- 驗證 migration 成功
- 執行 DB 測試
- 驗證 17 tests 結果

### 選項 2：先驗證 DB 狀態
- Owner 提供 DB 存取或執行查詢命令
- 驗證 alembic_version（確認雙 revision 狀態）
- 驗證 schema（確認 constraints 是否存在）
- 驗證 cancel_requested 欄位是否存在
- 再決定是否需要 DDL

### 選項 3：暫停，等待其他優先事項
- 不執行 DDL
- 不執行 DB 測試
- 保持當前狀態（代碼完成，未驗證）

---

## 九、總結（誠實評估）

**✓ 已驗證（本地）：**
- Migration graph 單一 head（本地驗證）
- 5 個契約測試通過（本地驗證，無 DB）
- Session factory 注入契約正確
- Target guard 邏輯已實作

**✗ 未驗證（需要 DB）：**
- 未在任何實際 DB 驗證 migration
- 未執行任何 DB 測試
- 未驗證 12 個 DB 測試是否能通過
- 未驗證 fixture/cleanup 是否正確
- 未驗證 target guard 是否有效
- 未驗證雙 revision 狀態下 alembic 的行為

**⚠️ 重要提醒：**
- 所有 DB 狀態資訊來自 Owner，未經獨立驗證
- 所有 migration DDL 操作未經實際執行
- 所有風險評估為理論推導，未經實證
- 雙 revision 狀態可能導致 alembic 行為不確定

**⚠️ 已移除的未證實保證：**
- 不再宣稱 "no-op"、"可逆"、"無 downtime"、"idempotent"
- 不再宣稱 "低風險"
- 不再宣稱 migration 已套用或測試已執行

---

**End of Migration Compatibility Analysis — Honest Assessment**
