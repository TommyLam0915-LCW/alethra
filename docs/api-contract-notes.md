# API 契約備註（P3）

更新日期：2026-11-01
範圍：模擬組合生命週期、429 的實際分類、`workspace_id`、兩組 `model_type`、模型研究匯出鏈、資料來源標示。

本文只陳述**現時程式碼實際做出**的行為，並附上來源位置。未實作或未接線之處集中於 §7，不以推測代替。

---

## 1. 模擬組合生命週期：pause / resume / retire

狀態列舉為 `active` / `paused` / `retired` / `completed`（`backend/app/models/paper_portfolio.py`）。`completed` 目前只存在於列舉與防禦分支，應用程式碼沒有任何路徑把它寫入。

### 1.1 狀態轉換

| 操作 | 端點 | 允許的前置狀態 | 拒絕 | 事件 |
| --- | --- | --- | --- | --- |
| 暫停 | `POST /api/v1/paper-portfolio/{id}/pause` | `active` | 其他狀態 → 400 `Can only pause an active portfolio` | `portfolio_paused` |
| 恢復 | `POST /api/v1/paper-portfolio/{id}/resume` | `paused` | 其他狀態 → 400 `Can only resume a paused portfolio` | `portfolio_resumed` |
| 退場 | `POST /api/v1/paper-portfolio/{id}/retire` | `active` 或 `paused` | `retired` / `completed` → 400 `Cannot retire a '<status>' portfolio` | `portfolio_retired`，酬載 `{"final_cash": <decimal>}` |

三條路由都需要 `write` API key scope，並經 `_get_portfolio_with_ownership` 做本人擁有權檢查。

### 1.2 暫停（可逆）

- 直接下單線：買入被風控以 `portfolio_paused` 拒絕；賣出不受影響。
- 重放執行器：已排隊的**買入**保持 `pending`，回報 blocker `portfolio_paused`，可在下一個執行視窗成交（以該視窗價格，不是暫停期間的價格）。
- 恢復後回到 `active`，被暫停的意圖在下一個視窗恢復可執行。

### 1.3 退場（終止）

- 設定 `status=retired` 與 `closed_at`，發出 `portfolio_retired`；**不清算持倉，亦不取消待處理訂單**。
- 待處理訂單此後永不成交：`execute_due_orders` 在到期判斷之後、暫停分支之前攔截 `retired`/`completed`，訂單維持 `pending`，回報 blocker `portfolio_retired`（或 `portfolio_completed`），不產生成交、不移動現金，並在 `manual_orders_blocked` 事件的 `blocked[].blocker` 留下同一代碼。
- `execution_view` 在計算 `next_window` 之前就回傳終止分支，`waiting.reason` 明確說明「retirement is terminal」，避免客戶由「沒有成交」自行推斷原因。
- 新訂單：模擬下單與直接下單線均回 400，訊息為 `The bound paper portfolio is '<status>' and cannot accept new orders. Bind a portfolio that is still active.`
- 執行層 `paper_execution` 對 `retired`/`completed` 兩個方向都拒絕（「not even an exit」）。

回歸：`backend/tests/test_manual_decision_books_linked_portfolio.py::TestCancelExpiryAndBlockers::test_a_retired_account_never_fills_the_order_it_left_pending`（同檔另有暫停→恢復的對照測試）。

---

## 2. 429 的實際分類

429 不是單一語意。HTTP 層只有一個狀態碼，客戶端必須讀 `detail`：`detail` 為**物件**者帶機器可讀 `error` 或 `reason`；`detail` 為**字串**者屬每身份限流。

| # | 類別 | 觸發位置 | `detail` 形狀 | 回應標頭 | 何時可重試 |
| --- | --- | --- | --- | --- | --- |
| 1 | 配額預留不足 | `reserve_quota`（`backend/app/auth/entitlements.py`） | `{error: "quota_exceeded", quota_type, limit, used, reserved}` | 無 | 配額週期（免費方案為 lifetime）更新後 |
| 2 | 使用前配額檢查 | `require_entitlement`（同上） | `{error: "quota_exceeded", quota_type, limit, used}`（無 `reserved`） | 無 | 同上；另：授權過期／停用是 **403** `Entitlement expired or disabled.` |
| 3 | 每 IP 每 bucket 限流 | `rate_limit_dependency`（`backend/app/auth/rate_limit.py`） | 字串 `Rate limit exceeded. Please try again later.` | `X-RateLimit-Limit/Remaining/Reset`、`Retry-After` | 依 `Retry-After` |
| 4 | 每使用者／每 API key 每小時限流 | `rate_limit` / `safety_control`（`backend/app/auth/safety.py`） | 字串 `Rate limit exceeded for <route_category> (n/limit requests per hour)` 或 `... for API key (...)` | `X-RateLimit-Limit`、`X-RateLimit-Remaining`；**無** `Retry-After` | 下一個小時視窗 |
| 5 | Kraken 場館限流 | `crypto_market._crypto_failure`（`backend/app/api/crypto_market.py`） | `{available: false, provider: "kraken", detail, no_fallback, reason: "venue_rate_limited"}` | 無 | 場館允許後；儲存 bar 仍可經 `/market/crypto/stored` 讀取（該欄位只在 `budget_exhausted` 分支加註，不在本分支） |
| 6 | 穩健性研究配額前置檢查 | `backend/app/api/robustness.py` | `{error: "insufficient_quota", quota_type: "research_run", required, remaining, message}` | 無 | 減少嘗試次數或提升配額後 |

要點：

- 第 4 類是**兩層同時檢查**：先查使用者層，再查 API key 層；輪換 API key 不會繞過使用者上限。
- 同一使用者的配額（第 1、2 類）以 `used + reserved` 計算；`reserved` 表示已預留但未結算的研究執行。
- 503 與 429 不同：`budget_exhausted`（場館預算用盡）與 kill switch 都是 503，不應以「太頻繁」向客戶解釋。
- 429 回應不含 secret 或 DSN；第 5 類附帶的例外文本經 `safe_error_message`（`backend/app/lib/error_safety.py`）截短並去除路徑。

---

## 3. `workspace_id` 的實際含義

- 只存在於 `experiments.workspace_id`：可為 NULL 的外鍵，指向 `workspaces.id`（`backend/app/models/experiment.py:34`）。
- 出現在 API 回應 `ExperimentResponse.workspace_id`（`backend/app/api/schemas.py:120`）。
- 沒有任何授權或查詢範圍使用它：所有實驗讀寫仍以 `owner_id == current_user.id` 為界；前端亦無引用。
- `backend/app/services/db_upgrade.py:60` 只在升級順序清單中提到 `workspaces` 表，與該欄位語意無關。

結論：`workspace_id` 現在是**資料欄位**，不是租戶／權限邊界。文件或 UI 不得把它描述成隔離機制。

---

## 4. 兩組 `model_type`（不同物件，不合併）

| 物件 | 位置 | 允許值 | 寫入者 |
| --- | --- | --- | --- |
| 模型研究版本 `ModelVersion.model_type` | `backend/app/models/model_research.py` `ModelType` | `rule_based`、`prompt_based_research_assistant`、`future_external_model_reference`、`personal_decision_model` | 版本建立路由（前三個）與個人化訓練服務（`personal_decision_model`） |
| 訓練任務請求 `model_type` | `backend/app/api/ml_training.py` | `logistic_regression`、`gradient_boosting`、`baseline` | 客戶發起訓練時 |
| 個人化訓練器參數 `model_type` | `backend/app/ml/personal_trainer.py` | `logistic_regression`、`baseline` | 個人化訓練工作 |

映射關係：

- `ModelVersionCreate` / `ModelVersionFromStrategy` 的正則只接受前三個值（`backend/app/api/schemas.py`）；`personal_decision_model` 不是客戶可自由建立的選項，而是個人化訓練完成後由服務寫入（`backend/app/services/personal_training.py`）。
- `personal_decision_model` 版本的 `model_specification_json.training_job_id` 指向一個訓練任務，該任務的 `model_type` 是 `logistic_regression` 或 `baseline`。
- 因此第一個欄位回答「這是哪一類研究成品」，第二個欄位回答「用哪個演算法訓練」。把兩者合併成單一列舉會丟失這個區別。

---

## 5. 模型研究記錄 → 版本 → 匯出

| 環節 | 端點／落地 | 檢查 |
| --- | --- | --- |
| 建立匯出 | `POST /api/v1/model-research-records/{record_id}/exports` | 記錄不存在 404；非本人 403；版本不存在 404；版本不屬於該記錄 404 `Version does not belong to this record` |
| 內容組裝 | `ModelResearchExport.package_json` | 版本清單、評估、模型卡、本人已確認的個人記憶；`manifest.json` 帶 `package_checksum`、`included_files`、`data_cutoff_at`、`disclaimer`、`excluded_capabilities` |
| 稽核 | `audit_events` | `export_generated`，酬載 `record_id`、`version_id`、`include_private_notes` |
| 讀取 | `GET /api/v1/exports/{export_id}` | 非本人 403 |
| 下載 | `GET /api/v1/exports/{export_id}/download` | 非本人 403；狀態非 `completed` 400；`package_json` 缺失 400 |

未支援：沒有「列出某記錄的所有匯出」端點。呼叫方（目前 UI）需自行保留匯出 id；重啟或換裝置後無法由記錄反查舊匯出。此為已知缺口，不是授權問題。

---

## 6. `data_provider` 與 `feed_mode` 的標示

回測引擎（`backend/app/engine/backtest.py`）按實際取數路徑填寫，不以設定值假設：

| 取數路徑 | `data_provider` | `feed_mode` |
| --- | --- | --- |
| `stored`（只讀已儲存 bar） | `Stored` | `cached` |
| `provider`（實際呼叫供應商） | 供應商自報名稱 | 供應商自報 `feed_mode` |
| `auto` 且無 `data_provider` 注入（Demo 後備） | `Demo (Synthetic)` | `offline` |
| 執行失敗（`_error_result`） | `null` | `null` |

- 失敗時為 `null`（即 unknown），不是空字串、不是 `demo`、不是 0。「唔知」保持未知。
- `data_nature` 逐資產標示 synthetic / stored / live，不與 `feed_mode` 互相取代。
- 儲存型研究執行把三個值寫入 `ResearchRun.result_json`；比較端讀回 `result_json.data_provider`，缺值時在 `limitations` 列出「部分資料來源資訊缺失：data_provider」，不填預設值（`backend/app/api/runs.py`）。
- Ad-hoc 回測以同一引擎回應 `data_provider`/`feed_mode`，但不落庫（`persisted: false`），因此不可當作之後可引用的來源記錄。

---

## 7. 註冊：風險披露確認（`POST /api/auth/register`）

- `UserCreate` 新增 `risk_acknowledged: bool`（預設 `false`）。伺服器**在查詢 email 是否存在之前**檢查：缺欄位或 `false` 一律回 `400`，且不建立帳戶。拒絕訊息不區分「已註冊」與「未註冊」的 email，避免洩漏帳戶存在與否。
- 成功註冊寫入一筆 append-only `AuditEvent`：`entity_type="user"`、`event_type="risk_disclosure_acknowledged"`、`actor_type="human"`、`payload` 為 `{disclosure_path: "/legal", disclosure_as_of_date: "2026-11-01", acknowledged: true}`。**不是**新的 user 欄位，因此不可按 user 直接查欄位；要查需讀事件表。
- 回應只含公開帳戶欄位（`id`、`email`、`display_name`、`user_type`、`created_at`、`is_active`、`is_admin`）；不含密碼雜湊、token、金鑰。
- 前端 `/login` 的註冊表單有必勾的披露核取方塊（連往 `/legal`），未勾時送出按鈕停用且不發請求；伺服器端檢查才是真正的門檻。

---

## 8. 本機測試入口（candidate session）的範圍

- `/api/auth/candidate-session` 是**開發／驗收用的本機測試入口**，不是客戶登入方式；`/login` 面板已明示此點。
- 前端只有在 localStorage 已存在顯式 `candidate_mode` 時才會自動續期；一般 email／密碼流程不會寫入 `candidate_mode`，因此不會觸發自動續期。
- 本地儲存的 `candidate_mode` 只是顯示狀態，改寫或偽造都無法擴權：後端仍要求本機 candidate 模式、loopback 來源與 allowlist 內的角色。

---

## 9. 本輪未改動的部分

- 不重寫任何歷史 run、訂單、事件或血緣欄位。
- 不套用 pending migration；不重建資料庫；不以空值或 0 填補缺欄位。
- 429 分類表描述現況，未改動任何限流門檻或回應形狀。
- `workspace_id` 未加授權範圍；改變其語意屬新功能，需另行批准。
- 匯出無列表端點；新增端點須經批准，不是本次交付的一部分。
