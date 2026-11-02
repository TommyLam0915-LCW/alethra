# 加密貨幣研究模式 — 第一切片（2026-10-30）

本文件記錄本輪新增之「加密貨幣研究模式」第一切片：只接**一個交易所、一個交易對**嘅公開唯讀行情，用於本機／內部研究及模擬交易。目的係令第一階段可以暫時唔依賴 LSEG／IBKR，同時保留「價格必須帶有交易所同報價資產」嘅身份規則。

## 1. 範圍同邊界

| 項目 | 本輪狀態 |
| --- | --- |
| 交易所 | Kraken（單一） |
| 交易對 | BTC/USD 現貨（`CRYPTO:KRAKEN:BTC-USD`） |
| 行情類型 | 只讀公開行情（Time / OHLC / Ticker / AssetPairs），無帳戶金鑰 |
| 下單 | **不支援**（`orders_supported: false`） |
| 對外展示／收費 | **未清權**（`NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION`） |
| WebSocket 即時推送 | 未實作（本輪用 REST 輪詢） |
| Paper execution 接線 | 未實作（本輪只提供行情同儲存） |
| 前端圖表／UI | 未實作 |
| 資料庫 migration | 已備 `s3t4u5v6w7x8`，**未在任何 shared runtime 執行** |

所有價格都唔會出現「無來源」嘅情況：每個 instrument id 內含交易所（`CRYPTO:<VENUE>:<BASE>-<QUOTE>`），報價資產由 `Currency` 決定，USDT 唔會被當成 USD（`BTC-USDT` 以 USD 建立會直接報錯）。

## 2. 工程要求對照

| 要求 | 實作位置 | 驗證 |
| --- | --- | --- |
| 標的身份必須記錄交易所 | `app/providers/instruments.py` 的 `Instrument.instrument_id`、`create_crypto_pair`、`parse_crypto_instrument_id` | `tests/test_crypto_instrument_identity.py` |
| 幣別同精度 | `create_crypto_pair`（price/quantity precision、min qty/notional）；`KrakenProvider.get_trading_specs` 執行時重讀 `AssetPairs` | 同上 + `test_kraken_provider_offline.py` |
| forming vs completed K 線 | `app/services/crypto_series.py`：`is_final = open_time + interval <= now`；`bars_for_strategy` 只含已完成 | `test_crypto_series_integrity.py`、`test_kraken_provider_offline.py` |
| 去重 | 同一時間戳保留最後一筆並記錄 `duplicate_timestamps` | `test_crypto_series_integrity.py` |
| 補洞／斷層 | 列出 `gaps` 同精確 `missing_intervals`，**不以插值補洞** | 同上 |
| 陳舊偵測 | 以 K 線間隔（`max(60, interval*2*60)`）而非日曆計算 | 同上 |
| 無合成資料替代 | provider/API 失敗一律回 `KrakenUnavailableError` → HTTP 503 `unavailable`，附 `no_fallback` 說明 | `test_crypto_api.py` |
| 可追溯性 | 每條 series 帶 `fingerprint`、`provenance`、`fetched_at`；儲存只收已完成 K 線 | `test_crypto_api.py` |
| 分數成交量 | 新增 `volume_decimal NUMERIC(30,12)`；crypto 列 `volume` 為 NULL、精確值入 `volume_decimal` | `test_crypto_api.py`、`store_candles` |

## 3. 新增 API（前綴 `/api/v1`）

| 方法 | 路徑 | 權限 | 用途 |
| --- | --- | --- | --- |
| GET | `/market/crypto` | 公開 | 宣告 lane、pair、條款狀態、provider 狀態 |
| GET | `/market/crypto/instruments` | 公開 | 列出已宣告交易對及交易規格 |
| GET | `/market/crypto/series` | 公開 | 即時讀交易所，回 completed + forming（forming 標明唔入策略） |
| POST | `/market/crypto/snapshot` | admin + 限流 | 將**已完成** K 線寫入資料庫 |
| GET | `/market/crypto/stored` | 公開 | 讀回已儲存 K 線及 metadata |

`/series` 回應同時帶 `unavailability_policy`：無法連線時回錯誤同空 series，絕不以 demo／合成 K 線替代。

## 4. 資料庫變更

`migrations/versions/s3t4u5v6w7x8_add_market_data_volume_precision.py`（`down_revision = r2s3t4u5v6w7`）：

- `market_data_bars.volume` 改為 nullable（既有股票成交量整數值不變）；
- 新增 `volume_decimal NUMERIC(30,12)`（crypto 分數成交量）。

Downgrade 會將 `volume` 還原為 NOT NULL，故**不可在有 NULL volume 嘅情況下執行**。本輪**未對任何 shared runtime 跑此 migration**；SQLite 測試經 `Base.metadata.create_all` 由模型建立，唔經 migration。

## 5. 驗證結果（本輪）

- 新增離線測試共 **74 項全數通過**（fake transport，無 socket）：
  `tests/test_crypto_instrument_identity.py`、`tests/test_crypto_series_integrity.py`、`tests/test_kraken_provider_offline.py`、`tests/test_crypto_api.py`。
- 受影響既有測試 **164 項全數通過**：
  `tests/test_instruments.py`、`tests/test_providers.py`、`tests/test_market_pipeline.py`、`tests/test_price_view_labeling.py`。
- 執行解譯器：`/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend/venv/bin/python`（3.12.3），SQLite 臨時 DB。
- 本輪**未**執行 full suite、**未**執行 `next build`、**未**新增依賴（符合既有資源／build gate）。

## 6. 可達性（本機實測，時間點為 2026-10-30 較早時段）

> 以下為本 session 較早時段對 Kraken 公開端點嘅唯讀量測結果。為遵守本輪「不真實外部服務」限制，**本輪測試未再重發外部請求**；數字屬先前實測，非本輪重新驗證。

| 端點 | 結果 |
| --- | --- |
| `GET /0/public/Time` | HTTP 200，約 0.32s |
| `GET /0/public/Ticker?pair=XBTUSD` | HTTP 200，約 0.059s，BTC/USD ≈ 77669.3 |
| `GET /0/public/OHLC?pair=XBTUSD&interval=1` | HTTP 200，57,485 bytes，721 行（最後一行為形成中 K 線） |
| `GET /0/public/AssetPairs?pair=XBTUSD` | HTTP 200，561 bytes |

形成中 K 線嘅假設同時由該次量測印證：最後一行時間戳為當下分鐘、成交量極小、count 為 1，與 Kraken 文件所述一致。

## 7. 未完成／後續

1. 前端即時圖表同 workspace 接線（需先凍結 UI 方向）。
2. Paper execution 接線：明確 fill／fee 假設，唔可以「顯示價當成交價」。（2026-11-02 狀態更新：已完成，見 `crypto-live-paper-pricing-2026-11-02.md`；其餘項目未變。）
3. WebSocket 斷線重連／補洞（REST 輪詢已具 integrity 層，可重用）。
4. 對外發布前必須完成：交易所條款核對、多用戶再分發、儲存／下載／回放、模型訓練及衍生資料使用、地區限制。
5. `s3t4u5v6w7x8` 於 shared runtime 執行（需 owner 另行批准，本輪不做）。
