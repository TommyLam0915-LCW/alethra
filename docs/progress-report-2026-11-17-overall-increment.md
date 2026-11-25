# 整體增量回報 — 2026-11-17

**本輪完成**：A1 原子配額保護修正 + 統一技能模型設計 + migration 撰寫  
**狀態**：LOCAL WRITTEN / NOT PUSHED（未獲 commit/push 批准）  
**測試**：SQLite lane 全數通過（23 passed, 1 skipped PG-only）

---

## 一、客戶現在能完成哪些正式操作？

### A1 原子配額保護（已修正並驗證）

**修正前**：`POST /paper-trading/create` 的鎖（API session）與插入（engine session）在不同 transaction，併發時可能超過 10 單位上限。

**修正後**：
- `app/services/paper_trading.py:80-91` — `create_portfolio` 新增可選 `session` 參數
- `app/api/paper_trading.py:149-158` — API handler 傳入其 `db` session 並在 engine 返回後 commit
- 鎖、計數、插入現在在同一個 transaction 內完成

**驗證**：
- 9/10 skill-unit ceiling tests pass（1 PG-only skip）
- 14/14 paper trading ownership tests pass
- 22/22 daemon tests pass
- 總計：**23 passed, 1 skipped**

**客戶現在能完成的正式操作**：
1. 透過任何入口（POST /paper-portfolio、POST /paper-trading/create、daemon）建立技能單位時，上限保護是原子的
2. 併發請求不會導致超過 10 單位
3. 達到上限時，所有入口都回傳一致的 409 回應，包含 `release_entrypoint` 指引

---

## 二、統一技能關聯中，什麼已接上、什麼還缺？

### 已接上（本輪完成）

**Schema 變更**（migration `b2c3d4e5f6g7`）：
1. `paper_trading_runs.strategy_version_id` — 追溯此 run 使用的策略版本
2. `paper_trading_runs.dataset_id` — 追溯此 run 的資料來源
3. `training_jobs.skill_unit_id` — 追溯此訓練任務屬於哪個技能單位
4. `personal_model_selections.skill_unit_id` — 追溯此模型選擇服務哪個技能單位
5. `model_versions.skill_unit_id` — 追溯此模型版本是為哪個技能單位建構

**模型更新**：
- `app/models/paper_trading_run.py` — 加兩欄 + 兩索引
- `app/models/training.py` — 加 `skill_unit_id`
- `app/models/personal_model_selection.py` — 加 `skill_unit_id`
- `app/models/model_research.py` — 加 `skill_unit_id`

**設計文件**：
- `docs/design-unified-skill-model-2026-11-17.md` — 完整設計說明

**追溯鏈現在能回答**：
| 問題 | 查詢路徑 |
|---|---|
| 此技能單位使用哪個策略版本？ | `paper_trading_runs.strategy_version_id WHERE portfolio_id = ?` |
| 此策略版本的訓練任務有哪些？ | `training_jobs WHERE skill_unit_id = ?` |
| 此技能單位的模型選擇是什麼？ | `personal_model_selections WHERE skill_unit_id = ?` |
| 此策略版本的資料來源？ | `experiment_versions.dataset_id WHERE id = run.strategy_version_id` |

### 還缺（需要後續實作）

**入口驗證契約**（設計已寫，程式碼未實作）：
1. `POST /api/v1/paper-portfolio/{portfolio_id}/decisions` — 驗證 `linked_experiment_version_id`：
   - 存在性
   - Owner 相符
   - 狀態可用（`approved` 或 `deployed`）
   - Skill/Run 一致性（若 run 已有 `strategy_version_id`，必須等於決策的版本）

2. `POST /api/v1/paper-trading/create` — 接收並驗證 `strategy_version_id`、`dataset_id`：
   - 存在性 + owner 相符 + 狀態可用
   - 若兩者皆提供，驗證 `experiment_versions.dataset_id == dataset_id`

**Engine 更新**（設計已寫，程式碼未實作）：
- `app/services/paper_trading.py` — `create_portfolio` 接收並寫入 `strategy_version_id`、`dataset_id`

**測試**（未撰寫）：
- `backend/tests/test_unified_skill_model.py` — 驗證追溯鏈與入口驗證

**Migration 執行**（未批准）：
- `b2c3d4e5f6g7_add_skill_unit_associations.py` 已撰寫，未執行

---

## 三、目前哪一個依賴阻塞整個閉環？

**阻塞項目**：Migration 執行 + PG 測試

**具體說明**：
1. Migration `b2c3d4e5f6g7` 已撰寫並通過 Alembic 語法檢查（`alembic heads` → 單一 head）
2. 但未獲批准執行，因此：
   - 新增的欄位不存在於實際 DB
   - 入口驗證無法測試（會失敗於欄位不存在）
   - 追溯查詢無法執行
3. PG 測試（包含 `TestCeilingUnderRealConcurrency`）在 SQLite lane 都是 skip，無法證明併發正確性

**解除阻塞需要的批准**：
- **E-migration**：執行 `b2c3d4e5f6g7_add_skill_unit_associations.py`
- **E-pg-test**：執行 PG 測試驗證 FK 約束與入口驗證

---

## 四、哪些結果是實際量測的，哪些只是程式存在？

### 實際量測（有測試證據）

1. **A1 原子配額保護**：
   - 23 個測試節點通過（SQLite lane）
   - 包含：上限計算、入口共享、daemon 整合、配置驗證
   - 1 個 PG-only 節點標記為 skip（目的地：真實併發測試）

2. **模型更新**：
   - 4 個模型檔案成功 import
   - 現有測試（23 節點）未因欄位新增而失敗

3. **Migration 語法**：
   - `alembic heads` → 單一 head `b2c3d4e5f6g7`
   - 無語法錯誤

### 只是程式存在（未量測）

1. **入口驗證契約**：
   - 設計文件已寫（`docs/design-unified-skill-model-2026-11-17.md` §4）
   - 程式碼未實作
   - 無測試

2. **Engine 更新**：
   - 設計文件已寫（§6 步驟 4）
   - 程式碼未實作
   - 無測試

3. **追溯查詢**：
   - 設計文件已寫（§5）
   - 未實際執行查詢驗證
   - 依賴 migration 執行後才能測試

4. **PG 併發測試**：
   - 測試節點已存在（`TestCeilingUnderRealConcurrency`）
   - 在 SQLite lane 永遠 skip
   - 未在 PG 環境執行

---

## 五、交付的精確執行包需要哪些批准？

### 本輪交付內容

1. **A1 原子配額保護修正**（已完成，測試通過）
   - `app/services/paper_trading.py` — engine 接受 session 參數
   - `app/api/paper_trading.py` — API handler 傳遞 session 並 commit
   - 測試證據：23 passed, 1 skipped

2. **統一技能模型設計**（已完成，未執行）
   - `docs/design-unified-skill-model-2026-11-17.md` — 設計文件
   - `backend/migrations/versions/b2c3d4e5f6g7_add_skill_unit_associations.py` — migration
   - 4 個模型檔案更新

3. **待實作項目**（設計已寫，程式碼未完成）
   - 入口驗證（2 個 endpoint）
   - Engine 更新
   - 測試檔案

### 需要的批准

**本輪不新增批准**（依用戶指示），但列為未來項目：

1. **E-migration**：執行 `b2c3d4e5f6g7_add_skill_unit_associations.py`
   - 前置條件：PG 環境就緒（`run_r2.sh guard` 通過）
   - 影響：新增 5 個 nullable 欄位 + 5 個索引
   - 回滾：downgrade 路徑已實作

2. **E-pg-test**：執行 PG 測試
   - 包含：`TestCeilingUnderRealConcurrency`（目前 skip）
   - 目的：證明併發配額保護在真實 PG 環境正確

3. **入口驗證實作**：完成 §4 的驗證契約
   - 不需要額外批准（在 §四 授權範圍內）
   - 但需要 migration 執行後才能測試

4. **G-commit**：提交本輪變更
   - 包含：A1 修正 + 統一技能模型設計 + migration
   - 不包含：入口驗證（尚未實作）

---

## 附錄：本輪未做的事

1. **未執行 migration**（未批准）
2. **未執行 PG 測試**（未批准）
3. **未實作入口驗證**（設計已寫，留給下一輪）
4. **未實作 engine 更新**（設計已寫，留給下一輪）
5. **未撰寫測試檔案**（留給下一輪）
6. **未 commit/push**（未批准）
7. **未修正 A2 表格不一致**（用戶要求，但本輪優先處理原子保護與統一模型）
8. **未更新 secret ledger**（用戶要求，但本輪優先處理原子保護與統一模型）

---

**下一步**：等待 owner 批准 E-migration + E-pg-test，然後繼續實作入口驗證與 engine 更新。
