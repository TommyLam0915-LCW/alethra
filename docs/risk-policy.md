# 風控政策（Risk Policy）

本文件說明 Alethra 三個會寫入 paper portfolio 的通道共用的一套准入政策，以及第
四個不寫入 paper portfolio 的通道。實作在
`backend/app/services/risk_policy.py`，由各通道在建立任何委託之前呼叫。

## 1. 通道與寫入範圍

| 通道 | 入口 | 是否寫入 paper portfolio | 政策呼叫點 |
| --- | --- | --- | --- |
| direct | `POST /paper-portfolio/{id}/orders` | 是（提交即成交） | `paper_portfolio.create_order` |
| session | `POST /simulations/{id}/manual-decision`（buy/sell） | 是（先 pending，推進後成交） | `simulations._submit_manual_order` |
| rule | `advance_simulation` 內由鎖定策略觸發 | 是（訊號 bar 即成交） | `paper_execution.apply_rule_decision` |
| offline intent | `alethra_broker_transport` | 否 | 不適用（本政策不涵蓋券商路徑） |

任何一個通道都不會因為切換入口而放寬檢查：政策只實作一次，各通道傳入自己的
決策時點資料。

## 2. 數值定義（逐項）

| 項目 | 定義 | 來源 |
| --- | --- | --- |
| `max_position_pct` | 單筆新進場買入金額 ÷ 組合價值，預設 `1.0`（100%） | `DEFAULT_RISK_POLICY` |
| 組合價值 | 帳戶現金 ＋ Σ（未平倉數量 × 決策時點價格） | `risk_policy.value_open_positions` |
| 決策時點價格（direct） | 最新已存放 bar 的收盤價（亦即該筆成交使用的 bar） | `paper_portfolio._latest_available_bar` |
| 決策時點價格（session / rule） | 決策 bar 的收盤價（session）／replay 記錄的 `observed_price`（rule），找不到時回溯至 ≤ 決策日的最新存放 bar | `_observed_bar_close`、`RiskInputs.price` |
| `max_daily_entries` | 每個決策日最多 10 筆「進場」 | `DEFAULT_RISK_POLICY` |
| 每日計數的日期鍵（direct） | UTC 提交日（`created_at` 落在 UTC 當日） | `count_entries_on_utc_date` |
| 每日計數的日期鍵（session / rule） | 決策 bar 的 `data_cutoff_at` | `count_entries` |
| 允許標的清單（direct） | schema 已驗證的 `DEMO-` 商品目錄；政策可再收窄 | `PaperOrderCreate.asset_id` pattern |
| 允許標的清單（session / rule） | 鎖定版本 `strategy_spec.asset_ids`；政策可再收窄，通道不可放寬 | `lane_allowed_instruments` |
| 未平倉估值 | 以決策時點價格計；無法定價的標的列為 `unpriced_assets`，不以 0 代入 | `value_open_positions` |

這些數字不讀環境變數，也不由使用者請求覆寫。

## 3. 准入順序與拒絕代碼

`evaluate_risk` 依序檢查，回傳 `RiskDecision(allowed, code, message, facts)`：

1. `unsupported_side` — 方向不是 BUY/SELL。
2. `portfolio_retired` / `portfolio_completed` — 退役或完成的帳戶不接受任何新委託。
3. **賣出（平倉）**：`quantity_exceeds_position` 之外一律放行。平倉不受暫停、
   允許清單、每日次數、持倉上限限制；持股與訂單狀態檢查仍由其餘層負責。
4. `portfolio_paused` — 帳戶暫停時拒絕新的進場（買入）。
5. `instrument_not_allowed` — 標的不在（政策 ∩ 通道）允許清單內。
6. `daily_entry_limit` — 決策日的進場次數已達上限。
7. `risk_price_unavailable` — 上限低於 100% 且無法取得決策價格或組合估值。
8. `position_limit` — 新進場金額超過組合價值的上限比例。
9. 全部通過才回傳 `allowed=True`，並把決策時點的數字放入 `facts`。

`max_position_pct = 1.0` 時上限不可能綁定一筆由現金支付的買入（買入金額最多等於
現金），因此不需要估值；這是政策本身的算術結論，不是容差。低於 100% 時若缺價格
或組合估值，一律拒絕，不會標為 `passed`。

## 4. 行為契約

- **提交時已知的硬性違規會被明確拒絕，不會產生可執行的 pending 委託。**
  - direct：寫入一列 `status=rejected`、`risk_check_status=failed` 的委託，並發出
    `order_rejected` 稽核事件；HTTP 400 的 `detail` 為字串
    `Risk check failed [<code>]: <message>`。
  - session：回傳 `order=None`、`rejection.reason=<code>`，
    `decision_type=blocked_action`；決策列保留拒絕原因，但不建立委託。
  - rule：在決策 journal 記錄 `risk_flags.blocked_reason=<code>`，不建立委託。
- **合法委託維持原有生命週期。** session 仍等待下一個可用 bar，成交時重新核定
  實際現金、持股與當時價格；direct 仍在同一請求內成交；rule 仍在訊號 bar 成交。
- **`risk_check_status` 只會在通過政策後寫入 `passed`。** 模型欄位沒有預設值，
  四個建立 `PaperOrder` 的位置都顯式指定。欄位在 schema 上仍可為 NULL（初始
  migration 建立時即為 `nullable=True`、無 server default），NULL 代表「未記錄」
  而不是通過；正常執行不會產生 NULL，因此本政策不需要新增 migration。
- **同一 idempotency key 的重試不會多耗一格每日額度。** 每個通道都先做冪等查詢，
  再評估政策；direct 的每日計數只算非 rejected 的買入。
- **pending 與 cancelled 的進場都會計入每日額度**（額度已被承諾）；
  rejected 不計入（它從未成為意圖）。
- **取消不受任何進場限制阻擋。** 取消只撤回尚未成交的意圖，不建立曝險。
- **成交時檢查不變。** 現金不足與賣超仍由 `book_fill` 在成交時拒絕；本政策沒有
  為 pending 委託預留現金或曝險，`pending` 不代表資金已被鎖定。

## 5. 併發

- rule 通道在評估進場政策前，先對 portfolio 列取 `FOR UPDATE`，讓兩個同時推進的
  replay 不能同時花掉每日額度的最後一格。
- session 通道不取 portfolio 列鎖（避免與 `advance_simulation` 形成反向鎖序）；
  同一 session 的並行提交由 `state_version` 樂觀鎖序列化，落敗者回 409 且整筆回滾。
- direct 通道原本已在建立前鎖定 portfolio 列。

## 6. 已知限制

- 沒有預留（reservation）帳本：pending 委託不佔用現金或持倉上限，直到成交時才
  重新核定。
- direct 通道的價格來源是內建合成 demo 資料，不是真實行情；本政策不改變這一點。
- 本政策只管 paper portfolio 的准入，不涉及券商下單路徑，也不解除任何券商
  containment。
- 回測（純研究，不寫 portfolio）不套用本政策，歷史結果不因本政策而改變。
