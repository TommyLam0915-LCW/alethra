# 盤中行情：後端控制的 REST 增量更新（2026-10-31）

本輪採用方案（1）：REST 輪詢。上游請求只由後端發出，前端只讀取後端資料與狀態；
沒有新增 WebSocket、tick 串流或任何依賴。以下狀態嚴格分開陳述，互不代替：

| 狀態 | 意思 |
| --- | --- |
| IMPLEMENTED | 程式碼已完成，含契約、儲存、API、前端與測試 |
| OFFLINE_VERIFIED | 以假 provider 及純函式測試在離線環境驗證行為 |
| BROWSER_VERIFIED（fixture） | 真實瀏覽器操作真實頁面，但畫面 K 線為人工插入的固定資料 |
| AUTHORIZED_PROVIDER_VERIFIED | 以真實帳戶驗證權限與 interval；**BLOCKED**（本機無 `TWELVEDATA_API_KEY`） |
| NEW_BAR_OBSERVED | 以真實 provider 觀察到新收盤 K 線；**BLOCKED**（同上） |

本輪真實 provider 請求數：**0**。畫面上所有 K 線均為測試 fixture，已在文件與畫面
（誠實聲明區、資料性質）標示，不得當成行情資料。

## 1. 為什麼不是前端 `setInterval`

瀏覽器只呼叫後端；是否真的向上游請求，由後端決定：

- 每個 (provider, instrument, interval) 有 `threading.Lock`，同一程序內並行讀取會
  合併成一次請求（N 個分頁不會產生 N 次上游流量）。
- 最短更新間隔 60 秒（`_MIN_REFRESH_SECONDS`），未到期回 `cache_fresh`。
- 程序內滾動預算：每小時 60 次、每日 400 次（可設定），用完回 `budget_exhausted`。
- 失敗退避：`429` 依 `Retry-After`（上限 300 秒）；其他錯誤指數退避（30→300 秒）。
- 凍結類狀態（`disabled`、`not_configured`、`unverified_interval`、
  `symbol_not_mapped`、`budget_exhausted` 等）前端不再自動輪詢，需使用者明確重新檢查。

限制需如實說明：鎖與預算是**程序範圍**。多 worker 部署時每個 worker 各自有預算，
本輪維持單 worker 的前提；若要橫向擴展，需改為共享儲存（未實作）。

## 2. Provider 契約

- `DataProvider` 新增：`supports_intraday()`、`declared_intraday_intervals()`、
  `get_intraday_bars(symbol, interval, *, outputsize)`；`IntradayBar.timestamp` 為 UTC
  開盤時間（naive，與儲存一致）。
- `ProviderRateLimitError(retry_after_seconds)`：實作必須拋出此錯誤，不得在配額窗口內重試。
- Twelve Data adapter：宣告 `1min / 5min / 15min / 30min`（只限可落在 epoch 網格的
  週期；`1h` 刻意不支援）。請求固定 `timezone=UTC`、`order=ASC`；若回應的
  `meta.interval` 與請求不符，直接報錯而不是把 5 分鐘棒標成 1 分鐘。
- 429 立即拋出、不重試；adapter 狀態回報 `rate_limited_retry_after_seconds`、
  `intraday_observed_intervals`（只有真實成功回應才記錄）。

**adapter 支援 ≠ 帳戶有權限。** 只有 operator 以實際帳戶確認後，把週期寫入
`INTRADAY_VERIFIED_INTERVALS`，後端才會發出真實請求；否則一律回
`unverified_interval`，不以 adapter 能力冒充權限。

## 3. 設定（全部可由環境變數覆寫）

| 設定 | 預設 | 說明 |
| --- | --- | --- |
| `INTRADAY_ENABLED` | `false` | 關閉時零上游請求 |
| `INTRADAY_DEFAULT_INTERVAL` | `1min` | 僅限 1/5/15/30min |
| `INTRADAY_REFRESH_SECONDS` | `60` | 後端最短間隔，低於 60 以 60 計 |
| `INTRADAY_OUTPUTSIZE` | `390` | 1–5000 |
| `INTRADAY_INSTRUMENTS` | `US:AAPL` | 本輪只接受美股 |
| `INTRADAY_VERIFIED_INTERVALS` | 空 | 空白＝完全不請求真實資料 |
| `INTRADAY_MAX_REQUESTS_PER_HOUR` / `_PER_DAY` | `60` / `400` | 程序範圍預算 |
| `INTRADAY_BACKOFF_BASE_SECONDS` / `_MAX_SECONDS` | `30` / `300` | 錯誤退避 |

金鑰只存在後端設定（`TWELVEDATA_API_KEY`）；不回傳、不寫入前端 bundle、不進
localStorage。`not_configured` 狀態只說明缺少設定，錯誤字串經 `safe_error_message`
清理。

## 4. 閘門順序

`disabled` → `not_configured` → `provider_not_intraday_capable` →
`interval_not_declared` → `unverified_interval` → `backoff` → `cache_fresh` →
`budget_exhausted` → `provider_unavailable` → `symbol_not_mapped` → 請求。

狀態：`new_bar`、`updated`、`no_change`、`no_data`、`rate_limited`、
`upstream_error`、`read_only`、`idle`。`no_data` 不是成功；任何失敗都保留上次
已知資料並標示 `is_stale`，不以合成價格代替。

## 5. 時間與 bar 正確性

- 新表 `market_data_intraday_bars`（additive migration
  `t4u5v6w7x8y9`），身份為 `(instrument_id, provider_id, timeframe, bar_timestamp)`。
- 與 `market_data_bars` 分表：鎖定的研究資料集/實驗版本結構上不可能被盤中輪詢改寫。
- 同一時間戳的未完成棒就地更新同一列；已收盤棒不再改寫，任何「已收盤→未完成」
  的回退會被拒絕（`skipped_forming_regression`）。
- 是否收盤由伺服器時鐘決定，不信任 provider 標記；`forming` 與 `completed` 分開回傳，
  `forming_excluded_from_strategy = true`。
- 未知成交量存 `NULL`（不以 0 冒充）。
- 缺口只在**正規交易時段內**計算；收盤時間之後才判定 stale，週末不算斷線。
- `data_as_of`（最新已收盤棒時間）與 `last_attempt_at` / `last_success_at`
  （請求時間）分開呈現。

### 本輪修正：無假日曆時不宣稱開市

原 `market_session_view` 在「交易日、時段內、但假日曆為空」時回 `open`，與畫面上
「假日曆未配置：交易日的休市一律顯示為未知，不強行判定開市」互相矛盾。已改為：

- 週末 → `closed_weekend`；平日且為已設定假日 → `closed_holiday`（新增標籤「假日休市」，
  原邏輯會誤標為週末）；
- 假日曆完整或 24 小時市場 → `open` / `closed`；
- 假日曆為空、位於正規時段內 → `unknown_holiday_calendar`；時段外 → `closed`（安全陳述）。

## 6. API

| 路由 | 權限 | 說明 |
| --- | --- | --- |
| `GET /api/v1/market/intraday/capability` | 無需登入 | 能力與目前預算，不花請求 |
| `GET /api/v1/market/intraday/series` | 登入 + 限流 600/窗口 | 讀取儲存資料；可能觸發一次有界刷新 |
| `POST /api/v1/market/intraday/refresh` | `require_admin` + 限流 30/窗口 | 明確刷新，同一有界路徑 |

回應固定帶 `data_nature="unknown"`、`is_realtime=false`、
`realtime_evidence="none_verified"`、
`data_rights="INTERNAL_TEST_ONLY_NOT_CLEARED_FOR_REDISTRIBUTION"`、
`market_calendar_complete` 與繁中資料權利聲明。擁有 API key 不等於可對外展示行情。

## 7. 前端

- 重用 lightweight-charts；`planChartUpdate` 只在身份改變、視窗左移或清空時
  `setData`，其餘以 `series.update()` 只動尾端棒。`setData` 前擷取並還原
  logical range，使用者縮放不會被每分鐘重置；永不自動 `fitContent` 跳回右端，
  另設「回到最新」按鈕。
- 切換標的/週期先清空舊資料，並以 request token 丟棄過期回應，不會顯示上一支標的的 K 線。
- 分頁隱藏或使用者暫停時不安排下一次讀取；「立即檢查」仍可發一次有界讀取。
- 狀態以繁中標籤與說明呈現（例如 `unverified_interval` 標為權限缺口而非成功、
  `budget_exhausted` 說明是本機保護）。未完成棒以淡色顯示並註明不納入策略輸入。
- 時間軸註明為 UTC、交易所時區未換算。

## 8. 已驗證與未驗證

### OFFLINE_VERIFIED

`backend/tests/test_intraday_rest_refresh.py`（30 個測試，以 stub provider）涵蓋：
同一棒就地更新、已收盤不可回退、重複/亂序不重複插入、身份隔離、不觸碰研究
資料表、未知成交量為 NULL、關閉時零上游、缺 key 的 `not_configured`、adapter
閘門分離、未映射標的先拒絕、provider 符號映射、並行讀取合併為一次、預算上限、
429 退避、逾時保留舊資料並標 stale、`new_bar` 與 `no_change` 區分、forming
分離、空回應為 `no_data`、capability 誠實、缺 key 不回顯、series 需登入、
不支援週期 400、disabled 狀態、資料時間與請求時間分離、refresh 僅 admin、
provider 狀態不含秘密，以及 4 個交易時段狀態（含無假日曆不宣稱開市、假日與週末
分開）。

前端純函式測試 `frontend/e2e/intraday-incremental.spec.ts`（16 個）涵蓋排序/去重、
增量與重置決策、身份丟棄、隱藏/暫停/退避的輪詢延遲、凍結狀態、狀態與會話標籤、
完整性摘要。`npx tsc --noEmit` 無錯誤。

### BROWSER_VERIFIED（fixture）

真實 Next dev（`:3000`）載入本頁，API 經隔離後端（`:8291`、可丟棄 SQLite、
無 provider key）轉發；DB 內為 `seed.py` 插入的合成棒。30/30 斷言通過：

1. 能力卡誠實：已啟用、無已驗證週期、允許標的、非即時、假日曆未配置、
   provider 錯誤明確為缺少 key；
2. 狀態膠囊為「未設定供應商」且說明缺少 API 金鑰，無成功假象；
3. 初始 3 根棒、資料截至 14:33；誠實聲明與上游請求數可見；圖表 canvas 已渲染
   （時段顯示「休市狀態未知（無假日曆）」，與修正後一致）；
4. 插入第 4 根後按「立即檢查」→ 顯示 4 根、資料截至 14:34，後端讀取 1–2 次；
5. 切換 `US:MSFT` → 空狀態、0 根、資料時間為「—（尚無已收盤 K 線）」，
   不殘留 AAPL 資料；
6. 暫停 → 顯示未安排下一次讀取；「立即檢查」僅一次讀取且不重新開啟輪詢；
7. localStorage 無 provider key、DOM 無 key 名稱；未登入導向 `/login`；
8. 閒置 3 秒無自發讀取；`/market-data` 有進入本頁的連結；
9. 全程後端 series 讀取 4 次（初始/追加/切換/暫停手動），上游 provider 請求 0 次。

證據：本機 `evidence/intraday-rest-2026-10-31/`（不在版本庫內；3 張截圖、log、
結果 JSON、腳本、fixture seeder、series 快照、`SHA256SUMS`）。
此輪在時段標籤修正後重新執行，截圖與最終程式碼一致。舊版截圖已被同名的重跑結果取代。

已知雜訊：`/market-data` 頁的 `/api/instruments` 在隔離後端回 500（`DATA_PROVIDER=twelvedata`
而無 key），屬既有頁面行為，不在本輪範圍，未被斷言。

### BLOCKED

- **AUTHORIZED_PROVIDER_VERIFIED**：本機沒有 `TWELVEDATA_API_KEY`，無法確認帳戶可用的
  市場/週期、延遲與配額。未發出任何真實請求。`capability` 與 `series` 在隔離環境
  正確回報 `not_configured`，未以 demo 假裝成功。
- **NEW_BAR_OBSERVED**：同上；無真實 provider 回應，無法觀察新收盤棒。
- 使用者縮放/捲動雖由設計與單元測試保證（僅 `update()`、還原 logical range），
  但未做像素級瀏覽器斷言；資料更新後維持右緣跟隨是 lightweight-charts 預設行為，
  未逐格驗證。
- 多 worker、跨程序預算與鎖未驗證（本輪單 worker）。
- 假日曆未配置，任何交易日的開市/休市只能顯示未知；未接真實交易日曆。

## 9. 啟用真實驗證的條件（需 operator）

1. 在金鑰管理處設定 `TWELVEDATA_API_KEY`（不得出現在聊天、前端或 log）；
2. 以實際帳戶確認可用市場與週期後，設定 `INTRADAY_VERIFIED_INTERVALS`（例如 `1min`）；
3. 設定 `INTRADAY_ENABLED=true`、`INTRADAY_INSTRUMENTS=US:AAPL`；
4. 有界驗證：單一標的、單一週期、預設 60 秒、最多 6 次上游請求（含失敗/重試），
   達預算即停，不開放式輪詢；若市場休市不得聲稱觀察到新收盤棒。

在此之前，本功能對外只應描述為「已實作、離線與 fixture 瀏覽器驗證、真實 provider 未驗證」。

## 10. 變更檔案

修改：

- `backend/app/api/__init__.py`
- `backend/app/config.py`
- `backend/app/models/__init__.py`
- `backend/app/models/market_data.py`
- `backend/app/providers/data_provider.py`
- `backend/app/providers/twelvedata_provider.py`
- `backend/app/services/market_data_storage.py`
- `backend/app/services/rate_limit.py`
- `frontend/src/app/market-data/page.tsx`
- `frontend/src/lib/api.ts`

新增：

- `backend/app/api/intraday_market.py`
- `backend/app/services/intraday_refresh.py`
- `backend/app/services/intraday_series.py`
- `backend/migrations/versions/t4u5v6w7x8y9_add_market_data_intraday_bars.py`
- `backend/tests/test_intraday_rest_refresh.py`
- `frontend/e2e/intraday-incremental.spec.ts`
- `frontend/src/app/market-data/intraday/page.tsx`
- `frontend/src/components/market/IntradayChart.tsx`
- `frontend/src/lib/intradayBars.ts`

## 11. 重跑指令

```bash
# 後端 focused tests（驗收直譯器）
cd backend && PYTHONPATH="$PWD" <venv>/bin/python -m pytest tests/test_intraday_rest_refresh.py -q

# 前端純函式測試（需可達的 dev server，僅作 baseURL）
cd frontend && E2E_FRONTEND_BASE=http://localhost:3000 npx playwright test e2e/intraday-incremental.spec.ts --project=chromium
cd frontend && npx tsc --noEmit
```
