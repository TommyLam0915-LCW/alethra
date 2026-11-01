# 研究流程說明：run、策略設定、基準與模型類型

這份說明回答五個常見問題，全部依據目前系統實際提供的介面與 API 行為撰寫。文中若出現 API 路徑，是給需要直接呼叫介面的使用者參考；一般使用者在網頁上操作即可，不需要自己組 request。

---

## 一、即時回測與可追溯 run 有甚麼分別？證據應該引用哪一個？

系統同時存在兩種回測方式，用途不同。

**即時回測（ad-hoc）**
- 路徑：`POST /api/v1/experiments/{experiment_id}/versions/{version_id}/backtest`
- 呼叫後立即拿到這次計算的結果，直接回傳給你。
- 它 **不會** 在資料庫留下一筆 run 記錄。也就是說，這次計算沒有專屬的 run id，之後無法再從系統把它「叫回來」逐項核對。
- 這種方式適合臨時看一眼，但 **不適合當作研究證據**。

**可追溯 run（durable run）**
- 路徑：`POST /api/v1/versions/{version_id}/runs`
- 每呼叫一次，會在系統建立一筆持久的 run 記錄，回傳內容包含：
  - `id`（這次 run 的專屬編號）
  - `experiment_version_id`（用的是哪一個策略版本）
  - `dataset_snapshot_id`、`dataset_checksum`（用的是哪一份資料快照）
  - `engine_version`（用哪一版引擎計算）
  - `config_hash`（這次設定的指紋）
  - `status`、`metrics`、`warnings`、`methodology_json`、`result_json`、`error_details`、`started_at`、`completed_at`
- 因為它有 run id、版本、資料快照、引擎版本與設定指紋，所以之後可以重現與核對。

**目前網頁介面的「Run backtest」按鈕，建立的就是可追溯 run**（呼叫 `POST /api/v1/versions/{version_id}/runs`），研究頁與修訂面板都是如此。即時回測端點目前只在 API 層提供，網頁上沒有對應按鈕。

**證據應該怎麼引用？** 一律引用可追溯 run，而不要引用即時回測的結果。具體做法：

1. 記下 run 的 `id`。
2. 需要核對時，用 `GET /api/v1/runs/{run_id}` 取回同一筆 run。
3. 需要列出某個研究的所有 run，用 `GET /api/v1/experiments/{experiment_id}/runs`。
4. 需要比較兩次 run，用 `GET /api/v1/runs/compare?run_a=<id>&run_b=<id>`。這個比較端點要求兩筆 run 來自 **同一個研究**。
5. 需要把比較保存成研究紀錄，用 `POST /api/v1/saved-comparisons`，必填 `experiment_id`、`run_a_id`、`run_b_id`、`purpose`，`notes` 可選。

一個實用的引用格式是：**run id + 策略版本 id + config_hash**。三者一起，別人才能確認你指的是哪一次計算、用哪份設定。

---

## 二、自訂 `strategy_spec` 現在如何提交與重用？

**提交（建立研究時）**
- 路徑：`POST /api/v1/experiments/`，body 使用 `ExperimentCreate`，必填 `title` 與 `strategy_spec`。
- `strategy_spec` 的必填欄位：
  - `template_type`
  - `asset_ids`
  - `start_date`、`end_date`
  - `initial_cash`

**目前支援的策略模板只有三種：**

| `template_type` | 會讀取的參數 |
|---|---|
| `buy_and_hold` | 無額外參數，買入後持有 |
| `sma_crossover` | `signal_parameters.short_window`、`signal_parameters.long_window`（兩者皆必填） |
| `price_change_fixed_hold` | `signal_parameters.lookback_days`、`.threshold_pct`（0.01–0.50）、`.holding_days`（整數 1–120） |

**重用（衍生新版本）**
- 路徑：`POST /api/v1/experiments/{experiment_id}/revisions`，body 使用 `RevisionCreate`：
  - 必填：`base_version_id`、`strategy_spec`、`change_reason`
  - 選填：`expectation`、`request_id`
- 修訂會 **從既有版本衍生一個新版本**，原本的版本內容不會被改寫。
- 系統會拒絕兩種沒有意義的修訂：與來源完全相同的設定，或系統內已存在一模一樣的設定。因為它們不會產生新證據，只會多一筆重複記錄。

**重要限制（請務必了解）**
- `strategy_spec` 內有些欄位會被儲存，但 **目前引擎並不會依據它們改變交易邏輯**，包括 `entry_rule`、`exit_rule`、`sizing_rule`、`execution_timing`。
- 目前真正會被引擎執行的，是：
  - 三種模板各自的參數（見上表）
  - `risk_parameters` 內的停損／目標價設定（`stop_price`、`stop_pct`、`target_price`、`target_pct`）
  - `commission_rate` 與 `slippage_rate`（範圍 0 至 0.05）
  - `benchmark_id`（影響基準比較指標，見下節）
- 因此「自訂 strategy_spec」在現階段的意思是：在這三種模板的範圍內設定參數，加上成本、風險計畫與基準；**不是**用 spec 裡的欄位自由發明新的交易邏輯。若要新增第四種策略邏輯，需要的是產品功能開發，而不是在 spec 填字。

---

## 三、基準（benchmark）為 null／缺失是甚麼意思？有甚麼限制？

基準是在 `strategy_spec.benchmark_id` 指定的。它的狀態有三種，意義完全不同：

| 情況 | 系統行為 | 你應該怎麼理解 |
|---|---|---|
| `benchmark_id` 為 null（未設定） | 不計算任何基準比較，相關基準指標為 null | **「沒有設定基準」**，不是「基準回報 0%」 |
| 已設定，但該日期範圍沒有資料 | 在 run 的 `warnings` 寫入 `Benchmark <id> unavailable for this date range.`，基準指標保持 null | 設定了但要用的那段時間拿不到資料 |
| 已設定且有資料 | 計算基準權益曲線並產生基準比較指標 | 可正常比較 |

**兩個容易誤會的地方：**

1. **研究層級的 `benchmark` 欄位只是標籤。** `ExperimentCreate.benchmark`（一個文字欄位）不會驅動引擎的基準計算。真正影響指標的只有 `strategy_spec.benchmark_id`。若只在研究層級填了 benchmark 名稱，run 的基準指標仍然是 null。
2. **系統沒有「現金」基準物件。** 未設定基準時，你不能用 null 當作「打敗現金」或「打敗 0%」的證明。null 就是「沒有比較對象」。系統的方法檢查也會把「未設定基準」與「已設定但指標缺失」分開陳述。

換句話說：**基準指標為 null 時，不能宣稱策略跑贏或跑輸任何東西**，只能說「這次沒有可用的基準比較」。

---

## 四、訓練類型與模型版本類型是甚麼關係？

它們是 **兩條不同的流程、兩組不同的分類**，目前沒有自動對應關係。

**訓練流程（會產出訓練好的模型產物）**
- 路徑：`POST /api/v1/ml/train`
- `model_type` 只能是：`logistic_regression`、`gradient_boosting`、`baseline`
- 訓練完成後產生的是可用的模型產物，屬於個人 ML 模型，可在 `GET /api/v1/ml/models` 查到，並透過同前綴的 predict 端點使用。

**模型研究紀錄的版本（描述模型如何被記錄與引用）**
- 模型研究版本的 `model_type` 只能是：`rule_based`、`prompt_based_research_assistant`、`future_external_model_reference`
- 這是另一套分類，用來描述「這個模型版本在研究記錄中屬於哪一類」。

**兩者沒有層對層的自動轉換。** 訓練出一個 logistic regression 模型，不會自動變成一筆 `rule_based` 的模型研究版本；反過來也一樣。若要把某個研究策略版本納入模型研究紀錄，用的是專門的「從策略建立版本」流程（`POST /api/v1/model-research/{record_id}/versions/from-strategy`，以 `rule_based` 表示），而不是靠改變訓練類型。

因此，請不要把兩組名稱互相比對或假設對應。它們回答的是不同問題：一組是「你訓練了哪種模型」，另一組是「這筆模型研究版本屬於哪個記錄類型」。

---

## 五、基本面／因子資料目前確認到甚麼程度？

**結論：`fundamental` 目前只是一個研究類型的標籤，不代表系統已有可用的基本面或因子資料來源。**

具體說明：
- `research_type` 欄位接受 `quantitative` 或 `fundamental`。填入 `fundamental` 只是把這筆研究標記為基本面類型。
- 目前檢查過的資料供應層（demo、LSEG、Twelve Data、Kraken）提供的都是 **OHLCV 價格資料（開高低收量）**，沒有基本面報表資料。
- 系統目前沒有提供基本面資料端點，也沒有提供因子（factor）資料端點。
- 因此，選了 `fundamental` **不會** 讓你取得基本面或因子資料，也不會改變回測能使用的資料種類。

一句話：**schema 裡有 `fundamental` 這個值，不等於有可用的基本面資料。** 若你的研究需要基本面或因子資料，這屬於尚未提供的功能，而不是設定問題。

---

## 六、用量如何計算？哪些操作會扣額度？

系統有兩本獨立的用量帳，互不相通：

| 用量類型 | 對應欄位 | 由甚麼消耗 |
|---|---|---|
| `research_run` | `research_run_limit` | 可追溯 run、穩健性研究的每一個執行格 |
| `ai_usage` | `ai_usage_limit` | AI 助理、模型推論、ML 訓練與個人模型工作 |

**會消耗 `research_run` 的操作**
- `POST /api/v1/versions/{version_id}/runs`：**每呼叫一次就是一次執行、扣一次額度**。這個端點沒有冪等鍵，所以同一版本連按兩次、或 timeout 後重試，都會產生兩個不同的 run id，各扣一次。重試之前，先看 `GET /api/v1/experiments/{experiment_id}/runs` 確認上一次是否其實已經成功。
- 穩健性研究（robustness study）：每個尚未完成的執行格，扣一次。**已經完成的格，重跑研究時不會再扣**，系統會沿用既有結果。

**不會消耗 `research_run` 的操作**
- 即時回測（`POST /api/v1/experiments/{experiment_id}/versions/{version_id}/backtest`）：計算後直接回傳，不入庫、不扣額度，但結果不能引用（見第一節）。
- 讀取 run、列出 run、比較 run、儲存比較、匯出、加筆記等唯讀或記錄操作。

**扣款時機（reserve → settle）**
1. 開始執行時先「保留」一個額度（reserved）。
2. 只有當結果完整寫入 run 記錄（status 為 `completed`）時，才真正「結算」成已用（used）。
3. 執行失敗、或引擎拒絕計算時，保留額度會被釋放，**用量不變**；該次 run 仍會留下一筆 `failed` 記錄與 `error_details`，方便核對原因。

也就是說：**失敗不會扣你額度，但重複提交、重試會。**

**目前本地系統的預設免費額度**
沒有 entitlement 紀錄的帳號，使用 `lifetime` 期間計算：

| 用量類型 | 免費額度 |
|---|---|
| `research_run` | 5 |
| `ai_usage` | 10 |

**如何查目前用量**
- `GET /api/v1/billing/usage/quota?quota_type=research_run_limit&period=lifetime`
- 回傳包含 `limit`、`used`（已結算）、`reserved`（進行中、尚未結算）、`remaining`（已扣掉 used 與 reserved）、`unlimited`、`source`（`entitlement` 或 `free_plan`）。
- 額度不足時，建立可追溯 run 會回 `429`，訊息會指出是配額不足，而不是資料或策略有問題。

---

## 快速對照表

| 問題 | 目前的答案 |
|---|---|
| 回測結果要引用哪個？ | 引用可追溯 run 的 `id`（`POST /versions/{version_id}/runs` 產生），不要引用即時回測 |
| 網頁「Run backtest」會留下記錄嗎？ | 會，它建立的是可追溯 run |
| 自訂策略可以自由寫邏輯嗎？ | 不行；只能在三種模板內設參數，`entry_rule`／`exit_rule`／`sizing_rule`／`execution_timing` 目前不被執行 |
| 基準 null 代表跑輸 0% 嗎？ | 不是；代表沒有設定基準，不能宣稱贏或輸 |
| 研究層級的 benchmark 名稱會影響指標嗎？ | 不會；只有 `strategy_spec.benchmark_id` 會 |
| 訓練模型會自動變成模型研究版本嗎？ | 不會；兩套 `model_type` 沒有自動對應 |
| 選 `fundamental` 就有基本面資料嗎？ | 沒有；目前只有 OHLCV 價格資料，尚無基本面／因子資料源 |
| 即時回測會扣額度嗎？ | 不會；它不入庫、不扣 `research_run` |
| 同一個 run 按兩次會扣兩次嗎？ | 會；端點沒有冪等鍵，每次呼叫都是新的 run、各扣一次 |
| 執行失敗會扣額度嗎？ | 不會；保留的額度會釋放，但會留下 failed 記錄 |

---

*本文依據目前本地系統的 API 與引擎行為撰寫。若之後引擎開始執行更多 `strategy_spec` 欄位，或加入了基本面／因子資料源，這份說明需要同步更新。*
