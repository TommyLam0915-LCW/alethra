# Alethra 研究方法論

**版本**: 1.1
**引擎版本**: 1.1.0
**日期**: 2026-10-11

---

## 1. 概述

Alethra 的回測引擎旨在提供確定性、可重現、可審計的策略研究環境。同一份策略規格（StrategySpec）搭配同一份資料，必須產生完全相同的結果。

### 核心原則

1. **確定性** — 相同輸入 → 相同輸出（config_hash + dataset_checksum 驗證）
2. **信號-執行分離** — 信號在 day t 收盤產生，執行在 day t+1 開盤
3. **成本透明** — 滑價與佣金獨立計算，記錄於 16 欄交易分類帳
4. **Benchmark 對齊** — 基準曲線使用與策略完全相同的執行假設

### 重要聲明

Alethra 是研究工具，使投資研究更具可檢驗性。它不應使歷史結果看起來比實際更確定。所有結果僅供研究參考，不代表未來績效。

---

## 2. 執行時序模型

### 信號到執行

```
Day t          Day t+1
┌──────────┐   ┌──────────┐
│ 觀察收盤 │──→│ 開盤執行 │
│ 產生信號 │   │ 計算成本 │
└──────────┘   └──────────┘
```

訊號可在交易日 t 收市後，使用當日已完成的收市價計算；但最早只會在下一個可交易日 t+1 的開市價模擬執行。

### 分類帳欄位定義

| 欄位 | 定義 |
|------|------|
| signal_date | 策略規則產生交易訊號的日期。初始配置可為 null（無先前訊號事件）。 |
| observed_price | 解讀訊號時的市場價格。對於收盤價規則，通常為 day t 收盤價。初始配置可為 null。 |
| execution_date | 模擬執行的日期。通常為 signal_date 後的下一個可交易日。 |
| execution_price_before_costs | 含滑價但未含佣金的執行市場價格。 |
| trade_date | 等於 execution_date。不存在模糊的重複日期欄位。 |

### Buy-and-Hold 初始配置例外

對於初始 buy-and-hold 配置：
- `signal_date` 為 null（無先前訊號事件）
- `observed_price` 為首日開盤價（用於正確計算滑價）
- `execution_date` 為第一個可交易日
- 買入以配置的佣金和滑價執行
- `rationale` 說明：「Initial allocation at first tradable session」

### 強制期末平倉

對於最終平倉：
- 所有策略在資料結束時若有持倉，均強制平倉
- 平倉使用最後一個有效收盤價
- `rationale` 明確標示為「Forced end-of-period liquidation」或「Forced exit: end of data with open position」
- 此為資料邊界強制行為，非訊號驅動

### 價格慣例

| 方向 | 觀察價格 | 執行價格 |
|------|----------|----------|
| BUY（訊號驅動） | day t+1 開盤 | open × (1 + slippage_rate) |
| BUY（初始配置） | day 0 開盤 | open × (1 + slippage_rate) |
| SELL（buy-and-hold 期末） | day T 收盤 | close × (1 - slippage_rate) |
| SELL（SMA cross-down） | day t+1 開盤 | open × (1 - slippage_rate) |
| SELL（固定持有期到期） | 持有期末日收盤 | close × (1 - slippage_rate) |
| SELL（強制平倉） | 末日收盤 | close × (1 - slippage_rate) |

滑價方向設計為對策略不利：買入價格偏高，賣出價格偏低。

### 佣金模型

```
commission = gross_amount × commission_rate
gross_amount = quantity × execution_price
```

BUY 時：`net_cash_change = -(gross + commission)`
SELL 時：`net_cash_change = gross - commission`

### 持倉大小

Phase 1 僅使用整股（whole shares），不支持小數股。最大可買股數：

```
max_shares = floor(cash / (execution_price × (1 + commission_rate)))
```

---

## 3. 策略模板

### 3.1 Buy and Hold

最簡單的基準策略：

1. `signal_date = null`（無訊號事件）
2. 在資料首日的開盤買入最大可負擔整股數
3. 持有至資料末日，以收盤價賣出
4. 期末賣出的 `rationale` 為「Forced end-of-period liquidation at final valid close」

**適用場景**: 建立被動投資基準線

### 3.2 SMA Crossover

移動平均線交叉策略：

**參數**:
- `short_window`: 短期 SMA 天數（如 5）
- `long_window`: 長期 SMA 天數（如 10）

**信號規則**:
- **Cross-up**（短均線從下穿越長均線）→ BUY 信號
- **Cross-down**（短均線從上穿越長均線）→ SELL 信號

**執行**:
- 信號在 day t 收盤確認（SMA 計算使用 day t 及之前的收盤價）
- 在 day t+1 開盤執行
- `signal_date` < `execution_date`（無同日前瞻執行）
- 每次 BUY 使用全部可用資金
- 資料結束時若有持倉，強制以末日收盤價平倉

**限制**: 需要至少 `long_window + 1` 天的資料

### 3.3 Price Change Fixed Hold

價格跌幅觸發策略：

**參數**:
- `lookback_days`: 回望期天數
- `threshold_pct`: 跌幅門檻（如 0.05 = 5%）
- `holding_days`: 固定持有期天數

**信號規則**:
- 計算 `(close[t] - close[t - lookback_days]) / close[t - lookback_days]`
- 若跌幅 ≤ -threshold_pct → BUY 信號
- 買入後持有固定天數，以收盤價賣出

**執行**:
- 信號在 day t 收盤確認
- 在 day t+1 開盤買入
- `signal_date` < `execution_date`（無同日前瞻執行）
- 持有 `holding_days` 個交易日後，以收盤價賣出
- 資料結束時若有持倉，強制平倉

---

## 4. 交易分類帳

每筆交易記錄 16 個欄位：

| 欄位 | 說明 |
|------|------|
| transaction_id | UUID 唯一識別 |
| trade_date | 成交日期（等於 execution_date） |
| signal_date | 信號產生日期（初始配置為 null） |
| execution_date | 執行日期 |
| symbol | 資產代號 |
| side | BUY / SELL |
| quantity | 股數 |
| observed_price | 觀察價格 |
| execution_price_before_costs | 含滑價的成交價 |
| slippage_amount | 滑價成本（絕對值） |
| commission_amount | 佣金 |
| gross_amount | 成交總額 |
| net_cash_change | 淨現金變動 |
| cash_after | 交易後現金餘額 |
| shares_after | 交易後持股數 |
| rationale | 交易原因說明 |

---

## 5. 績效指標

### 基本指標

- **total_return_pct**: `(final_equity - initial_cash) / initial_cash × 100`
- **max_drawdown_pct**: 從最高點到最低點的最大跌幅

### 年化指標與不可用條件

- **annualized_return_pct**: 當 `trading_days < 252` 時返回不可用（`{value: null, reason: ...}`）
- **annualized_volatility_pct**: 當 `trading_days < 252` 時返回不可用

年化報酬在數學上可以對短於一年的期間計算，但 Alethra 不對少於 252 個交易日的樣本顯示年化數值，因為短期間年化可能誇大表面績效。

不可用的指標以 `{value: null, reason: "..."}` 表示，不以零表示。

### Benchmark 相關

- **benchmark_total_return_pct**: `(benchmark_final_equity - initial_cash) / initial_cash × 100`
- **excess_return_pct**: `total_return_pct - benchmark_total_return_pct`

### 成本指標

- **total_commissions**: 累計佣金
- **total_slippage_cost**: 累計滑價成本
- **total_trades**: 交易次數

### 時間指標

- **time_in_market_pct**: 持有狀態的交易日佔比
- **trading_days**: 總交易日數

---

## 6. Benchmark 模型

### Model A — 執行一致基準（Phase 1 使用）

Benchmark 使用與策略完全相同的執行假設：

- 相同初始現金（initial_cash）
- 相同首日開盤買入時機
- 相同整股（whole-share） sizing
- 相同佣金率（commission_rate）
- 相同滑價率（slippage_rate）
- 相同期末平倉慣例
- 相同資料日曆

「基準使用與策略相同的初始資金、進場時機、整股規模、交易成本假設及期末平倉慣例模擬。此為執行一致基準，非理論指數報酬。」

`benchmark_total_return_pct` 以 `initial_cash` 為基礎計算，確保與策略 `total_return_pct` 可直接比較。`excess_return_pct` 反映策略與同等條件基準之間的差異。

---

## 7. 確定性保證

### Config Hash

```
config_hash = SHA-256(JSON.stringify(strategy_spec, sorted_keys, compact_separators))
```

相同的策略規格產生相同的 config_hash，可用於：
- 驗證兩次回測使用相同參數
- 審計追蹤
- 版本比對

### Dataset Checksum

```
dataset_checksum = SHA-256(serialized OHLC data for all assets × dates)
```

確保回測使用的資料未被修改。任何資料變動都會改變 checksum。

### 版本識別

| 識別碼 | 用途 |
|--------|------|
| engine_version | 引擎邏輯版本（如 1.1.0）；方法論變動時遞增 |
| config_hash | 策略參數指紋 |
| dataset_checksum | 資料集指紋 |

不同 engine_version 的執行結果不可直接比較，需重新執行以獲得一致基準。

---

## 8. 警告機制

引擎在以下情況產生警告：

- 佣金率為零（`Zero commission`）
- 滑價率為零（`Zero slippage`）
- 資料結束時有未平倉位（`Data ended with open position`）
- 現金不足以買入 1 股（`Insufficient cash`）
- 資料不足（`Not enough data`）
- 短期間樣本（`Short sample period`）
- 僅使用示範資料（`Demo data only`）

警告不中斷回測，但記錄於結果中供審視。

---

## 9. 資料限制

### 示範資料

Phase 1 使用 DEMO-ALPHA、DEMO-BETA、DEMO-BENCHMARK 三個模擬資產：
- 確定性生成的模擬資料
- 不代表任何真實資產
- OHLC 日線資料
- 日期範圍：2024-01-02 ~ 2024-02-14（約 30 個交易日）

### 不處理的事項

- 無股息、配息、除權
- 無股票分割
- 無停牌、漲跌停
- 無下市/退市
- 無存活者偏差保護
- 無流動性限制
- 無真實市場驗證

---

## 10. 限制與假設

1. **僅支援日線** — 不支援日內交易或更高頻率
2. **僅支援做多** — 不支援做空、融券
3. **單一資產** — 每次回測僅針對單一標的
4. **無槓桿** — 不使用保證金
5. **無股息** — 不處理配息、除權
6. **無限流動性** — 假設任何數量都能以指定價格成交
7. **無漲跌停** — 不處理價格限制
8. **示範資料** — Phase 1 使用模擬資料，非真實市場資料
9. **重複測試風險** — 反覆測試同一組資料可能產生過擬合，使歷史結果看起來比實際更可靠
10. **不保證未來獲利** — 歷史回測結果不代表未來績效

---

## 11. 前瞻模擬（Phase 3A）

### 核心原則

策略鎖定後，模擬引擎以逐日釋放快照方式驗證策略在未知數據上的表現。關鍵不變量：**策略規則在後續數據可見之前已固定。**

### 鎖定機制

實驗鎖定時（`scope.status = "locked"`）：

1. `strategy_spec` 不可修改（API 返回 400）
2. 不可建立新版本（API 返回 400）
3. 記錄 `locked_at`、`locked_by`、`strategy_config_hash`、`dataset_cutoff_at`、`engine_version`

### 快照釋放

- 模擬從 `start_date` 開始，按交易日順序逐日釋放
- 每日釋放僅提供截至該日的 OHLCV 數據
- 不可跳過或重複釋放
- 釋放完 `end_date`（= `dataset_cutoff_at`）後，模擬完成

### 決策類型

| 類型 | 說明 |
|------|------|
| no_action | 無信號觸發，不操作 |
| simulated_entry | 買入信號執行（以次日開盤價 + 滑價 + 佣金） |
| simulated_exit | 賣出信號執行（以次日開盤價 - 滑價 - 佣金） |
| forced_exit | 數據結束時強制平倉（以最後收盤價） |
| blocked_action | 操作被阻止（如現金不足） |

### 強制期末平倉

當所有快照釋放完畢且仍有持倉時：
- 以最後可用收盤價強制平倉
- 記錄為 `forced_exit` 決策
- `rationale` 標示為 "Forced exit: simulation complete with open position"
- 然後標記 session 為 completed

### 方法論狀態

| 狀態 | 說明 |
|------|------|
| historical_test_complete | 歷史回測完成 |
| locked_for_simulation | 已鎖定，可進行前瞻模擬 |
| forward_observation | 前瞻模擬進行中 |
| evidence_insufficient | 證據不足 |
| retired | 已終止（決策歷史保留） |

### 模擬引擎版本

- 模擬引擎版本：`SIMULATION_ENGINE_VERSION = "1.0.0"`
- 回測引擎版本：`ENGINE_VERSION = "1.1.0"`
- 模擬使用回測引擎的策略邏輯，但以逐日步驟方式運行

---

## 12. 虛擬投資組合與決策追蹤（Phase 3B）

### 核心原則

鎖定策略並完成前瞻模擬後，用戶可建立虛擬投資組合記錄研究決策。關鍵不變量：**預期結果必須在 qualifying action 前記錄；覆寫決策與鎖定策略分開追蹤。**

### 行為規則

1. **固定虛擬資金** — 初始資金不可追加或重置
2. **風險控制** — 可配置的持倉比例、每日單量、允許資產限制
3. **無操作記錄** — 系統記錄 `no_action` 決策，避免暗示頻繁操作
4. **先有預期再有結果** — entry/exit 決策必須包含預期結果、時間框架、否決條件
5. **覆寫分離** — 覆寫請求不修改鎖定策略，獨立記錄
6. **無遊戲化** — 無排行榜、徽章、社交功能

### 成交模擬

- 買入：執行價格 = 開盤價 × (1 + 滑價率)
- 賣出：執行價格 = 開盤價 × (1 - 滑價率)
- 預設滑價率 0.1%，佣金率 0.1%
- 現金不可為負

### 重要聲明

> Paper trading records simulated execution under stated assumptions. It does not reproduce real-money psychology, full market liquidity, partial fills, exchange outages, market impact, or actual brokerage execution.

詳見 `docs/paper-trading-limitations.md`。

---

**文件版本**: 1.3
**引擎版本**: Backtest 1.1.0 / Simulation 1.0.0 / Paper Portfolio 1.0.0
**最後更新**: 2026-10-11
