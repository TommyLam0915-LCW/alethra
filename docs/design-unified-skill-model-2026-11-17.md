# 統一技能模型設計（Unified Skill Model）

**日期**：2026-11-17  
**狀態**：DESIGN — migration 已撰寫，未執行（待批准）  
**輸入**：§16.6 A2 盤點結果（10 類：6 已隔離、1 半、3 未關聯、1 未驗證）

---

## 1. 設計目標

§三A 要求每個技能單位（skill unit）能追溯完整鏈：

```
owner → skill → strategy/version → run/data → decisions/orders/fills 
  → training snapshots/jobs/artifacts → enabled status/risk limits/stop state
```

現有問題（§16.6）：
- **策略參數版本**：`paper_trading_runs` 無 `strategy_version_id` 鍵
- **資料來源**：只在研究側（`ExperimentVersion.dataset_id`），執行側無鍵
- **訓練側 4 項**：全部以 `owner_id` 為鍵，不隨單位隔離
- **決策版本驗證**：`linked_experiment_version_id` 無 FK、未驗證

---

## 2. 設計原則

1. **重用現有表**：不新建平行 skill 抽象，擴充 `paper_portfolios` 與 `paper_trading_runs`
2. **關聯而非嵌入**：訓練側資料仍以自己的表存在，但加 `skill_unit_id` FK 指向 portfolio
3. **驗證在入口**：`linked_experiment_version_id` 在 POST 時驗證 owner + status + consistency
4. **可選欄位**：所有新增鍵皆 `nullable`，既存資料不需回填（保持 `UNKNOWN` 語意）

---

## 3. Schema 變更

### 3.1 `paper_trading_runs` 新增兩鍵

```sql
ALTER TABLE paper_trading_runs
  ADD COLUMN strategy_version_id UUID NULL,
  ADD COLUMN dataset_id UUID NULL;
```

- `strategy_version_id`：指向 `experiment_versions.id`，表示此 run 使用的策略參數版本
- `dataset_id`：指向 `experiment_versions.dataset_id` 的來源資料集（冗餘但方便查詢）

**不設 FK 約束**：既存 run 無法回填，設 FK 會阻擋插入。改由入口驗證。

### 3.2 `agent_strategies` 加 `skill_unit_id`

```sql
ALTER TABLE agent_strategies
  ADD COLUMN skill_unit_id UUID NULL REFERENCES paper_portfolios(id);
```

- 表示此策略配置屬於哪個技能單位
- `nullable`：既存策略可能跨多單位或尚未分配

### 3.3 `personal_model_selections` 加 `skill_unit_id`

```sql
ALTER TABLE personal_model_selections
  ADD COLUMN skill_unit_id UUID NULL REFERENCES paper_portfolios(id);
```

- 表示此模型選擇是為哪個技能單位做的
- `nullable`：既存選擇可能尚未分配

### 3.4 `training_jobs` 加 `skill_unit_id`

```sql
ALTER TABLE training_jobs
  ADD COLUMN skill_unit_id UUID NULL REFERENCES paper_portfolios(id);
```

- 表示此訓練任務是為哪個技能單位做的
- `nullable`：既存任務可能尚未分配

### 3.5 `model_artifacts` 加 `skill_unit_id`

```sql
ALTER TABLE model_artifacts
  ADD COLUMN skill_unit_id UUID NULL REFERENCES paper_portfolios(id);
```

- 表示此 artifact 是為哪個技能單位產生的
- `nullable`：既存 artifact 可能尚未分配

---

## 4. 入口驗證契約

### 4.1 `POST /api/v1/paper-portfolio/{portfolio_id}/decisions`

現有行為（`app/api/paper_portfolio.py:942`）：
```python
decision = DecisionRecord(
    paper_portfolio_id=portfolio_id,
    linked_experiment_version_id=payload.linked_experiment_version_id,
    ...
)
```

**修正**：若 `payload.linked_experiment_version_id` 不為 None，必須驗證：

1. **存在性**：`experiment_versions.id == payload.linked_experiment_version_id`
2. **Owner 相符**：`experiment_versions.owner_id == current_user.id`
3. **狀態可用**：`experiment_versions.status IN ('approved', 'deployed')`（不接受 'draft'/'failed'）
4. **Skill/Run 一致性**（若 run 已有 `strategy_version_id`）：
   - 查詢 `paper_trading_runs WHERE portfolio_id == portfolio_id`
   - 若 run 的 `strategy_version_id` 不為 None，必須等於 `payload.linked_experiment_version_id`
   - 此驗證確保決策使用的版本與 run 配置的版本一致

**失敗回應**：
- 不存在或 owner 不符：`404 {"reason": "version_not_found"}`
- 狀態不可用：`400 {"reason": "version_not_usable", "status": ...}`
- 一致性失敗：`400 {"reason": "version_mismatch", "run_version": ..., "decision_version": ...}`

### 4.2 `POST /api/v1/paper-trading/create`

現有行為：接收 `skill`（自由文字）、`instrument_id`、`interval_minutes`、`exit_config`。

**擴充**：request body 新增可選欄位：

```python
class CreatePortfolioRequest(BaseModel):
    skill: str
    instrument_id: str
    interval_minutes: int
    exit_config: str
    initial_capital: Decimal
    strategy_version_id: Optional[UUID] = None  # 新增
    dataset_id: Optional[UUID] = None  # 新增
```

**驗證**（若 `strategy_version_id` 不為 None）：
1. 存在性 + owner 相符 + 狀態可用（同 4.1）
2. 若 `dataset_id` 也不為 None，驗證 `experiment_versions.dataset_id == dataset_id`

**寫入**：傳入 `engine.create_portfolio(..., strategy_version_id=..., dataset_id=...)`，存入 `paper_trading_runs`。

---

## 5. 追溯查詢

完成後，可回答以下問題：

| 問題 | 查詢路徑 |
|---|---|
| 此技能單位使用哪個策略版本？ | `paper_trading_runs.strategy_version_id WHERE portfolio_id = ?` |
| 此策略版本的訓練任務有哪些？ | `training_jobs WHERE skill_unit_id = ?` |
| 此技能單位的模型選擇是什麼？ | `personal_model_selections WHERE skill_unit_id = ?` |
| 此決策使用的版本與 run 一致嗎？ | 入口驗證已保證 |
| 此策略版本的資料來源？ | `experiment_versions.dataset_id WHERE id = run.strategy_version_id` |

---

## 6. 實作步驟

1. **撰寫 migration 檔**（本輪完成）：
   - `backend/migrations/versions/b2c3d4e5f6g7_add_skill_unit_associations.py`
   - 包含上述 5 個 ALTER TABLE
2. **更新模型**（本輪完成）：
   - `app/models/paper_trading_run.py`：加 `strategy_version_id`、`dataset_id`
   - `app/models/training.py`：加 `skill_unit_id`
   - `app/models/personal_model_selection.py`：加 `skill_unit_id`
   - `app/models/model_research.py`：加 `skill_unit_id`
3. **更新入口驗證**（本輪完成）：
   - `app/api/paper_portfolio.py`：POST decisions 驗證 `linked_experiment_version_id`
   - `app/api/paper_trading.py`：POST create 接收並驗證 `strategy_version_id`、`dataset_id`
4. **更新 engine**（本輪完成）：
   - `app/services/paper_trading.py`：`create_portfolio` 接收並寫入新版本鍵
5. **撰寫測試**（本輪完成）：
   - `backend/tests/test_unified_skill_model.py`：驗證追溯鏈與入口驗證
6. **執行 migration**（**未批准，不執行**）
7. **PG 測試**（**未批准，不執行**）

---

## 7. 與 A1 的關係

A1（技能單位上限）與本設計互不衝突：
- A1 的 `assert_slot_available` 鎖定 `users` 行，計算 `paper_portfolios` 數量
- 本設計新增的 `skill_unit_id` FK 指向 `paper_portfolios.id`，不影響 A1 的計數邏輯
- 兩者共用同一個 `paper_portfolios` 表作為技能單位的載體

---

## 8. 向後相容

- 所有新增欄位皆 `nullable`，既存資料不需回填
- 入口驗證只在欄位不為 None 時觸發，既存 client 不受影響
- Migration 可安全執行於既有 DB，不會阻擋既存操作

---

## 9. 未涵蓋範圍

以下項目**不**在本設計範圍內，需要額外批准：

1. **風險限制每單位覆寫**：§16.6 判定為「半」，需要 `portfolio_risk_overrides` 表
2. **訓練側資料的自動分配**：本設計只加 FK，不自動將既存訓練任務分配給技能單位
3. **前端 UI 顯示追溯資訊**：本設計只處理後端資料模型與入口驗證

---

## 10. 批准請求

本設計需要以下批准（**本訊息不新增，僅列為未來批准項目**）：

- **E-migration**：執行 `a1b2c3d4e5f6_add_skill_unit_associations.py`
- **E-pg-test**：執行 PG 測試驗證 FK 約束與入口驗證
- **G-commit**：提交 migration 檔與模型變更

本輪僅完成程式碼撰寫與離線測試（SQLite lane）。
