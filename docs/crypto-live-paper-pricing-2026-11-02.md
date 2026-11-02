# 加密貨幣即時行情 → 模擬成交接線（2026-11-02）

本文件記錄 `CRYPTO:KRAKEN:BTC-USD` 由「交易所已完成 K 線」到「模擬紙上成交」的實際接線與本輪證據。**全程只讀公開行情，沒有向 Kraken 送出任何訂單，也沒有讀取任何帳戶金鑰。**

## 1. 範圍同邊界

- 只處理一個交易對：`CRYPTO:KRAKEN:BTC-USD`；只支援 1m 同 5m 兩個 paper 週期。
- 行情來源為 Kraken 公開 REST OHLC；每次 capture 只發 **1 次**上游讀取，經共用預算閘門（預設 60 次／小時、400 次／日）。
- 成交係**模擬**：手續費 0.1% 同滑價 0.1% 為系統假設，不是用戶的 Kraken 費率；不會送到交易所。
- 對外再分發狀態維持 `NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION`，未因本輪改動而改變。

## 2. 後端契約（前綴 `/api/v1`）

| 方法 | 路徑 | 權限 | 行為 |
| --- | --- | --- | --- |
| POST | `/crypto-paper/quote/capture` | 登入 + write scope + 限流 | 讀一次 Kraken OHLC，將**已完成** K 線寫入 quote 合約（`crypto_quote_bars`），回傳筆數、fingerprint、最新已完成 K 線 |
| GET | `/crypto-paper/quote` | 登入 | 用 300 秒窗口內、同一 fingerprint 的已完成 K 線報價，唔寫入任何資料 |
| POST | `/crypto-paper/portfolios/{id}/orders` | 登入（組合須屬本人） | 以同一報價成交，寫入 order／fill／receipt／ledger，並記錄決策 |
| GET | `/crypto-paper/portfolios/{id}/executions` | 登入（組合須屬本人） | 讀回成交記錄（JSON／CSV） |

`capture` 回應重點：

- `forming_excluded`：形成中 K 線在 recorder 之前已被移除，所以 `skipped_forming` 正常會是 0；判斷有無排除形成中 K 線應讀 `forming_excluded`。
- `data_nature: "live"`、`price_source: "kraken:public_rest:completed_bars"`。
- 交易所失敗時回 `crypto_failure` 映射（預算耗盡 503、429、venue 不可達 503 等），**不會**以 demo／合成 K 線替代。

報價與收據都帶同一 `snapshot_fingerprint`；收據另有 `ledger.mirror = "paper_portfolios/paper_fills float 4dp"`，即收據保留全精度，共用 ledger 以 4 位小數鏡像。

## 3. 前端變更

`CryptoPaperPanel` 新增 **Fetch latest venue bars**（`data-testid="crypto-paper-capture"`）：

- 只發一次 capture；成功後顯示 stored／refreshed 筆數、價格來源、最新已完成 K 線、`forming_excluded`、quote window 同 fingerprint；**不會**自動下單。
- capture 會令舊報價失效（必須重新 Preview），避免用過期價格成交。
- 行情來源標籤（`Completed venue bar`／`Simulated fill from a completed venue bar`）現在**兩種來源都會顯示**；fixture 仍保留原有警告標籤。此前 live 情況只有 payload 有標籤、畫面沒有顯示，屬本輪修正。

## 4. 本輪真實證據

執行環境：candidate stack `127.0.0.1:8100`／`:3100`，schema `c3d4e5f6a7b8`，以 candidate owner 免密 session 操作。

API 旅程（`.local/evidence/2026-11-02-live-crypto/04-live-paper-fill.json`，12 項檢查全過）：

- capture：`status=captured`、`updated=60`、`forming_excluded=true`、fingerprint `0217bff2…`、最新已完成 K 線 `2026-11-02T15:48:00Z`（close `77775.4`）。
- quote：BUY `0.00127287 BTC`，observed `77775.4`、execution `77853.2`、gross `99.09700268`、commission `0.09909700`、slippage `0.09902929`、net cash `-99.19609968`，`data_nature=live`、`price_source=kraken:public_rest:completed_bars`。
- order：HTTP 201，`order 9d86cb92…`、`fill 57a1e120…`、`receipt 080440c0…`；收據 net cash 與 quote 完全一致；ledger 4dp 鏡像 `-99.1961`，與收據相差 ≤ 0.0001；組合現金按 ledger net 扣減。

瀏覽器旅程（`.local/evidence/2026-11-02-live-crypto/ui/`，15 項檢查全過，0 console error、0 個 5xx）：

1. 免密進入 `/market-data/crypto`，面板與組合選擇器正常；
2. 按 **Fetch latest venue bars**，畫面顯示 live 來源、最新已完成 K 線、forming 已排除；
3. Preview quote 顯示 `Completed venue bar`、fingerprint，且沒有 fixture 標籤；
4. Place simulated order 顯示 `Simulated fill from a completed venue bar`、order／fill／receipt、ledger mirror、成交後現金。
5. 截圖：`01-paper-panel.png`、`02-capture.png`、`03-quote.png`、`04-receipt.png`。

## 5. 驗證

- 後端 targeted pytest：`test_crypto_paper_api.py`、`test_crypto_paper_service.py`、`test_crypto_api.py`、`test_upstream_budget.py`、`test_crypto_series_integrity.py` → **90 passed, 1 skipped**（skip 為 PostgreSQL-only 預算原子性測試，SQLite 不模擬）。
- 前端 `npx tsc --noEmit` → exit 0。
- 瀏覽器：15/15 檢查通過。
- 未執行 full suite、未執行 `next build`（沿用本機資源 gate）。

## 6. 誠實限制

- 這是一條**模擬交易**接線，不是實盤；沒有真實成交、沒有付費、沒有對外部署、沒有新增排程、分支僅存在本地。
- 手續費／滑價為假設值；結果不能用來推論真實執行成本。
- 只涵蓋單一交易對與 1m／5m；300 秒報價窗口以外的報價會被拒絕。
- 長期歷史仍受 Kraken 每次約 720 根 K 線的限制，不能靠 `since` 向前翻頁。
- 同一輪的 BTC 日線策略研究（`02-research-runs.json`）兩條 SMA 策略在該窗口分別為 -17.76% 與 -4.61%，**未證明任何策略優勢**；本文件只證明資料與模擬成交接線可用。
- 個人化模型仍未獲批准：無新同意、無 200 筆標籤，本輪未執行訓練或推論。
- 對外再分發仍未授權；任何公開或收費展示前須先核對交易所條款。

## 7. 未完成

1. WebSocket 或增量補洞（目前 REST 輪詢已具 integrity 層）。
2. 更多交易對／週期（需先擴充 instrument registry 同測試）。
3. 交易所條款與再分發授權（產品化前置）。
4. 在 disposable PostgreSQL 重跑本輪 capture／quote／order 的併發與 idempotency 測試。
