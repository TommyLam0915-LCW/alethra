# 交付報告：續研流程、可操作 409、訓練 lane 政策、:8100 更新

日期：2026-11-02
分支：`feat/strategy-revision-and-research`（本地提交，未推送、未開 PR）
範圍：本輪 owner 選定的四項——FB-20 續研流程、`:8100` 更新至 HEAD、錯誤訊息與文件指引、訓練 lane 政策調整。

本報告只陳述已執行並可重現的結果；未執行與未驗證項目集中在 §6。

---

## 1. 交付內容

### 1.1 FB-20：把任一版本續研成新實驗

**問題**：鎖定實驗的 `current_version_id` 是 replay／simulation 的綁定；它的修訂版本是血緣記錄，不能成為現行版本，因此舊有流程在「鎖定後再修訂」時，修訂版本永遠無法鎖定、回測或模擬。

**做法（加法式）**：

- 新增 `POST /api/v1/experiments/{experiment_id}/versions/{version_id}/continue`，回 `201` 與新實驗。
- 新實驗複製 `strategy_spec`（深拷貝，移除 `user_approved_at`）、`hypothesis`、`scope`、`visibility`；標題加 ` (continued)`（上限 500 字元）。
- 新實驗建立 `version_number = 1`、未鎖定的版本，`assumptions.continued_from` 記錄來源實驗／版本／版本號。
- `experiments` 新增兩個可空血緣欄位（外鍵 `ON DELETE SET NULL`）與對應 migration `c3d4e5f6a7b8`。
- 來源實驗、來源版本、現行指標與既有事件全部不改寫。
- 寫入 `experiment_created`、`experiment_continued`、`dataset_selected` 三個事件。

**前端入口**：

- 鎖定實驗的標題列新增「Continue in new experiment」（`data-testid="exp-continue-current"`）。
- 版本歷史每一列亦有同一動作（`data-testid="exp-continue-version-{n}"`）。
- 新實驗頁顯示血緣註記與來源連結；來源頁在版本歷史說明「修訂版本不能原地鎖定」。

### 1.2 409 由死路改為可操作指引

`lock` 仍只接受現行版本（契約不變），但 `detail` 現在說明原因並直接給出續研端點路徑：

```
Only the experiment's current version can be locked. A revision of a locked
experiment is a lineage record and cannot move the current pointer; to carry
this version's research into a new lockable experiment, POST
/api/v1/experiments/{experiment_id}/versions/{version_id}/continue
```

原有測試對 “current version” 的斷言保留；新增測試斷言路徑確實出現在訊息中。

### 1.3 訓練 lane 政策：分開報告，不降門檻

`GET /api/v1/ml/capabilities` 新增 `training_policy`（`policy_version = training-lanes-v1`）：

| lane | 用途 | 門檻（未變） | 同意 | 發布狀態 |
| --- | --- | --- | --- | --- |
| `personal_model_release` | 發布用個人模型 | 200 已確認決策／30 工作階段／60 決策時點／每動作 30／特徵覆蓋 0.95 | `personalization-training-v1`，本人簽署 | `NOT_APPROVED` |
| `agent_candidate_evaluation` | 代理候選評估（非發布） | 20 候選／10 時序分組 | `agent-strategy-learning-v1`，本人簽署 | `NOT_APPROVED` |

- `release_thresholds_unchanged = true`；兩條 lane 都是 `activation_available = false`。
- 讀取政策**不會**建立同意列；`owner_signed_required = true`。
- `/training` 頁面顯示兩條 lane 的門檻、同意狀態與授權布林值。
- candidate profile 只開啟程式層開關（`AGENT_STRATEGY_SCREENING_ENABLED`、`AGENT_STRATEGY_OWNER_CONSENT_MODE`），它們不是同意。
- 本輪沒有簽署任何同意，也沒有降低 200 人門檻或以代理代簽。

### 1.4 `:8100` 更新至 HEAD

- 停止舊服務，套用 migration（`v6w7x8y9z0a1` → `c3d4e5f6a7b8`），以現行 HEAD 重啟 backend、worker 與 frontend。
- 更新前已備份 candidate DB：`.local/candidate/backups/alethra_personal_e2e_candidate_20261029-pre-c3d4e5f6a7b8-20261102T231553+0800.dump`（mode 600）。
- 升級後 11 類物件數量不變（experiments 14、versions 19、audit 216、decisions 620、users 10、runs 11、sessions 5、paper_orders 1、consents 14、jobs 6、models 6）。
- 同一 migration 亦在全新 disposable PostgreSQL 上由零建到 head，確認兩個新欄位存在；該 disposable DB 已刪除。

---

## 2. 提交與檔案

| 提交 | 內容 |
| --- | --- |
| `aaf2267` | 續研端點、血緣欄位與 migration、409 指引、訓練 lane 政策、前端入口、測試 |
| `e896068` | 修正 migration revision id 重複（`b2c3d4e5f6a7` → `c3d4e5f6a7b8`） |
| `00c3a61` | 修正 PostgreSQL 才暴露的個人化測試 fixture 外鍵缺漏 |
| `7ee123e` | 鎖定實驗標題列顯示續研入口 |
| 本文件提交 | 同步 API 契約與個人化契約 |

主要檔案：

- `backend/app/api/experiments.py`
- `backend/app/api/schemas.py`
- `backend/app/models/experiment.py`
- `backend/migrations/versions/c3d4e5f6a7b8_add_experiment_continuation.py`
- `backend/tests/test_experiment_continuation.py`
- `backend/app/services/personalization.py`
- `backend/app/api/personalization.py`
- `backend/tests/test_personalization.py`
- `frontend/src/lib/api.ts`
- `frontend/src/app/experiments/[id]/page.tsx`
- `frontend/src/components/training/PersonalizationPanel.tsx`
- `start_candidate_stack.sh`
- `docs/api-contract-notes.md`
- `docs/personalization-contract.md`

---

## 3. 驗證證據

### 3.1 後端測試（SQLite，2026-11-02 重跑）

```
tests/test_experiment_continuation.py  7 passed
tests/test_personalization.py         14 passed
合計                                   21 passed
```

覆蓋：409 指引、血緣、標題、單一未鎖定 v1、來源不變、續研後可鎖定且只產生一次 `experiment_continued`、任一自有版本可續研、跨實驗 404、跨用戶 404；個人化政策兩條 lane、門檻未變、讀取政策不建立同意、只有環境開關不足以授權。

### 3.2 PostgreSQL

- migration 已在 candidate DB 做升級驗證，並在全新 disposable DB 做從零建置驗證；兩者都到 `c3d4e5f6a7b8`。
- 個人化測試曾在 disposable PostgreSQL（`alethra_r496_fb20_20261102`，已刪除）通過；期間發現並修正 fixture 未建立 `SimulationSession` 的外鍵缺漏（`00c3a61`），斷言沒有放寬。

### 3.3 部署後實測（`:8100`）

`GET /health/ready`：

```json
{"status":"ready","checks":{"database":{"status":"connected","latency_ms":9.08},
 "schema":{"status":"compatible","alembic_revision":"c3d4e5f6a7b8"},
 "redis":{"status":"not_configured"}}}
```

OpenAPI 指紋（本輪前端提交後重測，backend 未再改動）：

| 指標 | 前一 HEAD | 現 HEAD |
| --- | --- | --- |
| paths | 231 | **232** |
| GET/POST/PUT/PATCH/DELETE ops | 272 | **273** |
| 位元組 | 未記錄 | **318,322** |
| sha256 | `9690487…` | **`387bac0e1f51003acc19e1957cd3de8ea27b5df068abfe40336677f0e17dce9d`** |

`/openapi.json` 已包含續研路徑。`capabilities` 回傳四個能力類別與 `training_policy`，內容與 §1.3 一致。

### 3.4 瀏覽器實測（headless Chromium，真實點擊）

腳本：`.local/candidate/ui-check.js`；結果 **18/18 passed**。

- `/training`：`training-policy` 區塊、兩條 lane、`NOT_APPROVED`、200/30/60、20/10、授權布林值、「門檻沒有因本次更新調低」全部可見。
- `/experiments/bd9ac04d-62eb-5fa9-abe9-671f13328ba9`（locked）：標題列與版本歷史都有「Continue in new experiment」；版本歷史說明修訂不能原地鎖定。
- 實際點擊一次續研：導向新實驗 `ffb9e3a3-f774-47f7-8021-15505040d984`，標題 `E2E 個人策略學習（合成，自動化） (continued)`，狀態 `draft`，血緣指向 `bd9ac04d…` / `a96d06a6-3228-5011-baa8-c4a79985d0b1`，v1 未鎖定，事件為 `experiment_created`、`experiment_continued`、`dataset_selected`。
- 截圖：`.local/candidate/shot-experiment-header.png`、`shot-experiment-versions.png`、`shot-training-policy.png`；全頁截圖另有 `ui-check-*.png`。

### 3.5 本次產生的資料（已披露，未刪除）

- 上述續研實驗是本輪 UI 驗證時以真實點擊建立，屬產品正常寫入，**保留不刪**。
- 該帳號可見實驗數由 1 增至 2（原 locked 實驗 + 新續研 draft）。
- 沒有解鎖、改寫或刪除任何既有實驗、版本、事件或結果。
- 新實驗未被鎖定或模擬。

---

## 4. 客戶如何使用

1. 打開一個已鎖定的研究，標題列按「Continue in new experiment」。
2. 系統建立新實驗（v1 未鎖定），血緣與原假設一併帶入。
3. 在新實驗修改參數、回測，確認後再鎖定及進行 replay／simulation。
4. 原實驗保持凍結，仍是 replay 的基準。
5. 在 `/training` 查看兩條訓練 lane 的門檻與授權狀態；需要訓練時，由本人以密碼登入並簽署對應同意。

---

## 5. 本輪發現並修正的缺陷

| 缺陷 | 影響 | 修正 |
| --- | --- | --- |
| migration revision id 與 `add_quota_reservations` 重複（`b2c3d4e5f6a7`） | Alembic 報 “present more than once”／multiple heads；SQLite 測試因由 metadata 建表而不會發現 | 改名為 `c3d4e5f6a7b8`，確認 single head，提交 `e896068` |
| 個人化測試在 PostgreSQL 觸發 `human_decision_events_simulation_session_id_fkey` | PG 才暴露的 fixture 缺漏，5 個測試失敗 | fixture 建立被引用的 `SimulationSession`，提交 `00c3a61` |
| 續研入口只藏在預設收合的版本歷史內 | 客戶在鎖定實驗頁看不到續研路徑 | 標題列加入同一動作，提交 `7ee123e` |

---

## 6. 未執行項與限制

- **沒有簽署任何同意**：candidate 帳號 `consent.status = not_configured`；`training_granted = false`、`snapshot_granted = false`。代理沒有代簽、沒有回填日期。
- **沒有降低門檻**：200/30/60/30/0.95 與 20/10 全部維持；`release_thresholds_unchanged = true`。
- **發布仍未批准**：兩條 lane 都是 `NOT_APPROVED`、`activation_available = false`。
- **本輪沒有執行訓練、快照或推論**：交付的是流程、政策表面與授權閘門，不是模型效果。
- **沒有實盤、付費、公開發佈或排程**。
- **沒有向 `:8111`／`:3111` 發請求，也沒有使用 owner 一次性連結**；客戶代理的邊界未被觸及。
- **沒有 admin／candidate session 冒充**；所有驗證均為候選擁有者身份。
- **沒有重建資料庫或刪除 volume**；只刪除本輪自建的兩個 disposable PostgreSQL DB。
- **UI 驗證不等於市場效果**：來源研究本身是合成 E2E 資料；本輪 18/18 證明介面與續研鏈路可用，不證明任何策略有效或可獲利。
- 分支仍為本地提交，未推送、未開 PR；發佈需 owner 另行批准。
