# 拒絕日誌與資料血緣（最小接線）

更新日期：2026-10-29
範圍：工作線 R-D。本文記錄已接線的拒絕日誌契約、推論到評估的血緣鏈、實際驗證證據，以及本輪**未**修補的殘餘缺口。

## 1. 狀態摘要

| 項目 | 狀態 | 證據 |
| --- | --- | --- |
| 拒絕（風控）日誌：被拒訂單留存 `rejected` 列並寫入稽核事件 | 已實作、測試通過、執行期可觀察 | `backend/tests/test_paper_portfolio.py::TestRefusedFillStaysOnTheRecord`；執行期 `order_rejected` 共 8 筆 |
| 拒絕（成交階段）日誌：回滾後以獨立操作身份留存 | 已實作、測試通過；執行期尚未發生 | 同上（`order_rejected_before_persist`）；執行期 0 筆 |
| 規則觸發之成交／阻擋日誌 | 已實作、既有測試通過 | `rule_order_executed` / `rule_order_blocked` 由 `_book_rule_trade` 發出 |
| 推論 → 模型成品 → 訓練任務 → 資料集與範圍 → 特徵與參數 → 評估 | 已實作，並在執行期完成一次真實端到端驗證 | `.local/rd-live-lineage-20261029.log`、`.local/rd-live-inference-20261029.log` |
| 拒絕日誌的產品讀取介面 | 部分：風控拒絕見於交易日誌與 CSV；回滾拒絕僅存於稽核事件（見 §5 G4） | `paper_portfolio.py` 交易／決策日誌；稽核事件目前僅 `experiments/{id}/timeline` 可讀 |
| 模擬決策 → 訂單／成交外鍵 | 未接（見 §5 G1） | 目前僅能經稽核事件酬載關聯 |
| 人類決策記錄 → 訂單外鍵 | 未接（設計為 record_only，見 §5 G2） | 執行期 `human_decision_events` 0 筆 |
| AI 作為稽核行為者（`actor_type="ai"`） | 未接（見 §5 G3） | 執行期行為者僅 human / system / api_key |

## 2. 拒絕日誌契約

所有事件皆經 `emit_event(...)` 寫入 `audit_events`，欄位固定包含 `entity_type`、`entity_id`、`event_type`、`actor_id`、`actor_type`、`rules_locked`、`data_cutoff_at`、`recorded_before_outcome`（由 `EVENTS_BEFORE_OUTCOME` 決定）與 `payload_json`。

### 2.1 風控拒絕（訂單建立階段）

- 事件：`order_rejected`
- 資料列：`paper_orders` 保留一筆 `status=rejected`、`risk_check_status=failed` 的訂單
- 酬載：`order_id`、`decision_id`（匯總決策記錄，如適用）、`idempotency_key`、`rejection_code`、`asset_id`、`side`、`quantity`、原因訊息
- 特點：拒絕本身有可追溯的資料列身份；同一 `idempotency_key` 不會重複建立。

### 2.2 成交階段拒絕（回滾後留存）

- 事件：`order_rejected_before_persist`
- 情境：風控通過、但成交扣款失敗（例如現金不足支付滑價與手續費）
- 處理：訂單列、決策列與現金異動在同一交易中回滾；**拒絕事件以獨立交易提交**
- 酬載：`operation_id`（每次請求唯一的 UUID）、`order_id=null`、`decision_id=null`（資料列已回滾，故不假造 ID）、`reason`、`reason_code`（`insufficient_cash`／`insufficient_position`）、`idempotency_key`、`actor_type`、`execution_date`
- 特點：拒絕原因具機器可讀代碼，不依賴英文字串重新推導；`order_id` 明確為 null，避免指向不存在的資料列。

### 2.3 規則觸發（模擬重放 → 模擬帳戶）

- 事件：`rule_order_executed`（成交）／`rule_order_blocked`（風控或資金阻擋）
- 酬載：`simulation_session_id`、`step_number`、`applied`（含 `order_id`、`decision_id`、`fill_id`、`status`、`reason_code`）
- 特點：重放決策與模擬帳戶的成交以事件酬載對應；`decision_records.simulation_session_id` 另提供資料列層級的會話綁定。

## 3. 推論資料血緣鏈

鏈路：**推論 → 模型成品 → 訓練任務 → 資料集與範圍 → 特徵與參數 → 評估**

| 環節 | 落地位置 | 關鍵欄位 |
| --- | --- | --- |
| 推論 | `usage_records.metadata_json`；API 回應 | `model_id`、`training_job_id`、`model_instrument_id`、`data_nature`、`bars_count`、`prediction_type`；回應新增 `training_job_id` |
| 模型成品 | `backend/app/ml/artifacts/<model_id>/metadata.json` | `training_job_id`（訓練時寫入，非事後掃描）、`instrument_id`、`date_range`、`feature_names`、`n_train`／`n_val`／`n_test` |
| 訓練任務 | `training_jobs` | `strategy_spec_snapshot`（`instrument_id`、`start_date`、`end_date`、`n_bars`、`data_source`、`data_nature`）、`parameter_grid`、`results.model_id`、`results.metrics`、`results.split` |
| 資料集與範圍 | 任務快照（請求範圍）＋成品 `date_range`（實際可用範圍） | 兩者分開保留，避免把「請求範圍」誤當「實際訓練範圍」 |
| 特徵與參數 | 成品 `feature_names`；任務 `parameter_grid`、`results.feature_names` | 兩處一致，可由任一方向回推 |
| 評估 | `training_jobs.results.metrics`（train／val／test／baseline_test） | 逐分割指標保留在任務結果中 |

### 3.1 執行期驗證（2026-10-29，:8099，合成儲存資料）

- 真實訓練任務：`POST /api/v1/ml/train`（US:AAPL、gradient_boosting、2024-01-02..2024-12-31）→ 任務 `6fbb47eb-98f0-4a3a-92c9-ef20cb0746ad`，由 `python -m app.workers.training_worker` 執行完成。
- 成品：`346d38ed-b93c-4d59-a5f3-aaa922ba6f5f`，14 個特徵，分割 160／34／35，實際可用範圍 2024-02-01..2024-12-30，任務快照記錄請求範圍 2024-01-02..2024-12-31、251 根 bar、來源 `stored`、性質 `synthetic`。
- 反向一致：任務 `results.model_id` 等於成品 ID；`parameter_grid.model_type` 等於成品 `model_type`；評估欄位為 `f1, split, recall, accuracy, n_samples, precision, positive_rate`。
- 推論：`predict-latest` 回應含 `model_id` 與 `training_job_id`，`data_nature=synthetic`，預測為 2025-12-31、SELL、機率 0.4106；額度由 6 結算為 7（`reserved=0`）。
- 結論僅限**合成儲存資料**；非即時市場資料，亦未驗證真實券商或外部資料源。

## 4. 驗證方式

```bash
# 拒絕日誌與血緣（隔離 SQLite，逐次建立暫存 DB）
ALETHRA_TEST_DB=sqlite:///./test-rd.db \
  <venv>/bin/python -m pytest tests/test_paper_portfolio.py tests/test_ml_pipeline.py \
  tests/test_ml_worker_integration.py tests/test_human_decision_recording.py -q

# 執行期：只讀血緣走查（登入、模型列表／詳情、訓練任務）
node .local/rd_live_lineage_20261029.mjs

# 執行期：推論血緣與額度結算（會寫入一筆 usage_record）
node .local/rd_live_inference_20261029.mjs
```

2026-10-29 結果：`112 passed`（exit 0）；執行期血緣走查 14 項通過；推論檢查 7 項通過。證據檔：`.local/rd-tests-20261029.log`、`.local/rd-live-lineage-20261029.log`、`.local/rd-live-inference-20261029.log`、`.local/rd-live-worker-20261029.log`、`.local/rd-live-train-submit-20261029.log`、`.local/rd-live-db-lineage-20261029.log`。

## 5. 殘餘缺口（本輪未修補）

### G1 模擬決策與訂單／成交之間沒有外鍵

- 現況：`simulation_decisions` 只記錄重放本身的決策與模擬帳戶快照；模擬帳戶的成交落在 `decision_records`／`paper_orders`／`paper_fills`。兩者之間沒有 `decision_id` 或 `order_id` 欄位，關聯只存在於 `rule_order_executed`／`rule_order_blocked` 事件的酬載（`applied.decision_id`、`applied.order_id`）與 `decision_records.simulation_session_id`。
- 影響：可由事件酬載與會話＋步數重建對應，但無法用單一 SQL join 保證一對一；若日後事件未寫入或酬載格式改變，關聯會斷。
- 未修補原因：需要新增欄位與回填既有資料的遷移；在尚未確認歷史列可唯一對應前，不以猜測回填。
- 建議關閉方式：新增可空 `simulation_decision_id`／`order_id` 欄位與唯一索引，先只對新資料寫入，歷史列維持 null 並另做對帳報告。

### G2 人類決策記錄與訂單沒有連結（且目前不執行）

- 現況：`human_decision_events.execution_status` 只有 `record_only`；API 只記錄決策，不建立訂單。手動模擬單另走 `paper_portfolio` 的 `DecisionRecord`＋`PaperOrder`，與人類決策記錄之間只有會話、標的、時間的可推測關聯，沒有外鍵。
- 影響：若未來允許由人類決策直接下單，目前沒有欄位承載「這張單源自哪一筆人類決策」。執行期 `human_decision_events` 為 0 筆，尚無資料可回填。
- 建議關閉方式：執行功能落地時同步新增 `order_id`／`execution_decision_id` 欄位並在同一交易寫入。

### G3 AI 從未被指派為稽核行為者

- 現況：稽核事件實際出現的 `actor_type` 為 `human`（990）、`system`（97）、`api_key`（3）；沒有任何 `ai`。AI 助理訊息與 AI 草稿不寫稽核事件。`DecisionRecord` 雖有 `ai_draft`／`human_approved_ai_draft` 枚舉，但沒有產品路徑會設定它。
- 影響：目前所有可稽核的動作都由人類、系統或 API 金鑰發起；「AI 建議了什麼、誰核准」在稽核層不存在，只有對話訊息本身。
- 未修補原因：AI 草稿尚無下單或修改策略的權限，先接線 AI 行為者會製造沒有對應動作的事件類型。
- 建議關閉方式：先定義 AI 草稿的產品動作（例如產生策略草稿），再以 `actor_type="ai"`＋`human_approved_ai_draft` 的核准事件成對寫入。

### G4 部分拒絕沒有產品讀取介面

- 現況：風控拒絕會留存一筆 `rejected` 訂單，該訂單會出現在交易／決策日誌與 CSV 匯出中，因此可被使用者看見；但**成交階段回滾拒絕**（`order_rejected_before_persist`）沒有資料列，只寫入 `audit_events`，而目前只有 `GET /api/v1/experiments/{id}/timeline` 會讀稽核事件。模擬帳戶與模擬會話沒有對應的拒絕列表 API 或 UI。
- 影響：最需要解釋的一類拒絕（錢扣不下去、訂單整個回滾）在產品內不可見，使用者只看到當次請求的錯誤訊息；營運檢視需直接查資料庫。
- 建議關閉方式：新增唯讀端點（例如 `GET /api/v1/paper-portfolio/{id}/refusals`），回傳 `order_rejected` 與 `order_rejected_before_persist` 事件，並在決策日誌旁呈現；只讀既有事件，不改寫入路徑。

## 6. 明確不在本輪範圍

- 真實券商下單、真實付款、外部資料傳輸。
- 以即時市場資料驗證血緣數值。
- PostgreSQL 併發壓測（本輪皆為單一請求的序列驗證）。
- 歷史資料回填與資料列修復（G1、G2）。
