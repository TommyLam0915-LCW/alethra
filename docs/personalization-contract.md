# 個人化模型契約（Capability C）

版本：2026-11-02（修訂見 §10） · 狀態：**已接線**（`/api/v1/ml/capabilities` 回 `AVAILABLE`；訓練、快照與推論端點存在）；模型發布仍為 `NOT_APPROVED`，效果未經證實
適用範圍：以個別 owner 的已確認決策資料訓練模型。本文件同時是實作契約與資格門檻的依據；程式實作位於
`backend/app/services/personalization.py`、`backend/app/api/personalization.py`。

---

## 1. 三種能力必須分開標示

| 能力 | 內容 | 現況 | 不應被說成 |
| --- | --- | --- | --- |
| A 市場資料模型 | 以 OHLCV 技術特徵預測指定市場目標（次日漲跌方向） | 已提供（`/training` ML 模型訓練） | 個人化、學習用戶偏好 |
| B 策略規則檢查 | 以已保存的規則檢查操作是否符合計劃（風控、掛單、出場規則） | 已提供（手動／規則委託、風控拒絕原因） | 權重學習、個人化模型 |
| C 個人化模型 | 以明確同意、可追溯的個人決策資料訓練，學習該用戶在特定策略下的決策偏好 | **尚未建立**：無 trainer、無消費端 | A 或 B 的別名 |

在本輪（R-C）中，C 只交付「資格檢查 + 同意範圍」。任何畫面或 API 都不得因 A/B 已可用而宣稱 C 已完成。

---

## 2. 下一階段的預測任務（執行前寫定，尚未執行）

- **任務**：在特定策略版本下，於決策時點預測「該用戶接下來最可能確認的動作」
  （`buy` / `sell` / `hold`），用於提示與研究回顧，不用於自動下單。
- **不是**：保證市場走勢、不是自動交易、不是跨用戶通用模型。
- **消費端（尚不存在）**：只有當產品真的把預測回饋到使用者流程（例如決策面板提示）時，
  才可宣稱個人化模型已交付。未有消費端前不得產生預測。

## 3. 標籤（Label）

只有同時滿足以下條件的決策事件才算一個可用標籤：

1. 由 owner 明確記錄動作（`human_decision_events.action`）。
2. 該事件有填寫理由（`rationale` 非空）。
3. 該事件未被後續修訂取代（`supersedes_event_id` 指到它的事件存在時，原事件不計）。
4. 該事件所屬的策略版本在有效同意範圍內。

規則：

- **不把「沒有操作」自動標成 HOLD。** 系統不生成事件；沒有記錄就是沒有標籤。
- **不把虧損當錯誤、獲利當正確。** 標籤是「用戶確認了什麼動作」，不是結果好壞。
- **不跨用戶混合。** 查詢一律以 `owner_id` 過濾；`personalization_eligibility` 只回傳呼叫者自己的統計。
- **不把未來放進特徵。** 決策時點 `server_snapshot_date` 必須 ≤ 該工作階段的資料截止日
  `server_data_cutoff_at`，且該時點必須有已儲存的行情。

## 4. 特徵（下一階段使用；本輪不使用）

- 決策時可知：決策時點與之前的 OHLCV 衍生特徵、目前持倉與現金狀態、已保存策略規則的參數、
  該策略版本識別、決策時點序號。
- 不得使用：決策後的價格、事後評價、其他用戶資料、任何在決策時點不可得的資訊。
- 特徵窗口與 label boundary 必須在實作前以測試固定。

## 5. 同意（Consent）

同意記錄獨立於資料保存與 AI 脈絡 opt-in：

- 資料表：`personalization_consents`（`purpose = personalization_training`）。
- 範圍：`scope_json.strategy_version_ids`，必須是呼叫者擁有的策略版本，且至少一個。
- 授予：`PUT /api/v1/ml/personalization/consent`，需 `confirm = true`；新授予會撤銷舊的有效同意。
- 撤銷：`DELETE /api/v1/ml/personalization/consent`；撤銷後該批資料立即不計入資格。
- 只有「作用中（`revoked_at IS NULL`）且列出該策略版本」的同意才會解鎖事件。

明確規則：

- 保存研究記錄、加入追蹤清單、把 AI 記憶設為 `approved_for_personal_ai` 或任何其他 opt-in，
  **都不是**訓練同意。
- 授予同意不代表資料足夠：資格檢查仍會逐項拒絕不足的資料。

## 6. 資格門檻與拒絕原因

常數（`backend/app/services/personalization.py`）：

| 項目 | 門檻 |
| --- | --- |
| 同意範圍內、具理由、未撤回的決策 | ≥ 200 筆 |
| 不同工作階段 | ≥ 30 個 |
| 不同決策時點 | ≥ 60 個 |
| 每個動作類別（buy / sell / hold） | ≥ 30 筆 |
| 決策時點行情覆蓋 | ≥ 95%（`covered / considered`） |
| 未來資料洩漏 | 必須為 0 |

拒絕原因代碼：

| 代碼 | 意義 |
| --- | --- |
| `NO_RECORDED_DECISIONS` | 完全沒有記錄任何人工決策 |
| `CONSENT_MISSING` | 沒有有效的訓練同意 |
| `CONSENT_SCOPE_EMPTY` | 同意存在但沒有列出任何策略版本 |
| `INSUFFICIENT_CONFIRMED_DECISIONS` | 可用標籤數不足 |
| `INSUFFICIENT_DISTINCT_SESSIONS` | 工作階段數不足 |
| `INSUFFICIENT_DISTINCT_SNAPSHOT_DATES` | 決策時點數不足 |
| `INSUFFICIENT_CLASS_COVERAGE` | 某一動作類別樣本不足 |
| `UNLABELLED_WITHOUT_REASON` | 同意範圍內有決策未填理由，不能作為標籤 |
| `OUTSIDE_CONSENT_SCOPE` | 有決策不在同意範圍內，未計入 |
| `INSUFFICIENT_FEATURE_COVERAGE` | 決策時點缺少已儲存行情 |
| `FUTURE_DATA_LEAKAGE` | 決策時點超過資料截止日 |
| `INVALID_SNAPSHOT_CONTEXT` | 缺少可用的決策時點或標的 |

未定義的比率（例如 `considered = 0`）一律回傳 `null`，不得以 `0.0` 代替。

## 7. 品質評估協定（實作時必須先寫定、不得事後調整）

- **時間切分**：依 `server_snapshot_date` 排序，訓練 / 驗證 / 測試不得重疊；同一工作階段不得跨切分。
- **baseline**：多數類別分類器，以及「永遠 HOLD」的退化基準。未勝過 baseline 即標示「未達效果驗證」。
- **指標**：macro-F1 與各類 precision/recall，另列混淆矩陣；`accuracy` 單獨不足以宣稱有效。
- **前處理**：scaler/transformer 只 fit 訓練段；測試資料不得反覆調參。
- **資料量與限制**：標明 owner 數（1）、策略版本數、工作階段數、時點數與資料來源性質（合成／延遲／真實）。
- **接受方式**：預先登記門檻（例如 macro-F1 相對 baseline 的提升量），未達門檻就寫「未達效果驗證」，
  不得以 `accuracy > 0` 當作有效證據。

## 8. 本輪現況（2026-10-29）

- `GET /api/v1/ml/capabilities` 回傳 A/B 為 `AVAILABLE`，C 為 `NOT_IMPLEMENTED`。
- `GET /api/v1/ml/personalization/eligibility` 對目前 e2e owner 回傳 `NOT_ELIGIBLE`：
  已記錄決策 0 筆、無有效同意；拒絕原因為 `NO_RECORDED_DECISIONS` 與 `CONSENT_MISSING`。
- `/training` 頁面顯示三種能力與資格檢查結果；未建立同意時不提供授予以外的任何訓練入口。

## 9. 明確不在本輪範圍

- 個人化 trainer、artifact 版本、推論與消費端 UI。
- 以市場資料模型或規則檢查代替個人化結果。
- 為了讓資格通過而生成、補齊或模擬任何決策標籤。

---

## 10. 修訂：訓練 lane 政策（2026-11-02，`training-lanes-v1`）

本節只新增「兩條 lane 分開報告」的表面，**不修改** §3–§7 的標籤、特徵、同意與門檻。

| lane | 用途 | 門檻 | 同意 | 發布狀態 |
| --- | --- | --- | --- | --- |
| `personal_model_release` | 發布用個人模型 | 200／30／60／每動作 30／覆蓋 0.95（與 §6 相同） | `personalization-training-v1`，本人簽署、密碼登入工作階段 | `NOT_APPROVED` |
| `agent_candidate_evaluation` | 代理候選評估（非發布） | 20 候選／10 時序分組 | `agent-strategy-learning-v1`，本人簽署 | `NOT_APPROVED` |

- 回應位置：`GET /api/v1/ml/capabilities` 的 `training_policy`（`policy_version = training-lanes-v1`）；`release_thresholds_unchanged = true`，兩條 lane 都是 `activation_available = false`。
- 兩條 lane 互不替代：代理候選評估的機器反事實結果不能當作個人化模型的發布資格；反之亦然。
- 讀取政策不會建立同意列。環境開關（`AGENT_STRATEGY_SCREENING_ENABLED`、`AGENT_STRATEGY_OWNER_CONSENT_MODE`）只決定程式能否執行，不構成同意。
- `/training` 頁面顯示兩條 lane 的門檻、同意狀態與授權布林值；本輪 candidate 帳號仍為 `consent.status = not_configured`、`training_granted = false`、`snapshot_granted = false`。
- 沒有以「降低 200 人門檻」換取可訓練性；任何新門檻都必須另立 version 並經 owner 批准。
