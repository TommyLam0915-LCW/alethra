# 加密貨幣上游請求預算收緊（2026-10-31）

本文件記錄「先完成 A（上游預算控制），才推進 B（paper 研究整合）」的 A 段結果。
A 段只處理一件事：把 Kraken 公開 REST 的呼叫上限由「只作報告」變成「送出前必先
原子保留，否則拒絕」。本輪**沒有發出任何新的 Kraken 請求**，所有行為以 stub
transport、已保存回應及 disposable PostgreSQL 驗證。

## 0. 狀態分層（互不代替）

| 狀態 | 意思 | 本輪 |
| --- | --- | --- |
| IMPLEMENTED | 程式碼已完成並接線 | 是 |
| OFFLINE_VERIFIED | 以 stub transport／已保存回應／SQLite 驗證 | 是 |
| PG_VERIFIED | 以 disposable PostgreSQL 驗證計數器原子性 | 是 |
| LIVE_VERIFIED | 以真實 Kraken 帳戶／端點驗證 | **否（本輪 0 次請求）** |

## 1. 18 次 vs 原定 6 次的差距（原始紀錄，不修改）

上一輪的原始 log 保持原樣，不刪不改：

- `.local/evidence-crypto-live-20261031/acceptance-run.log` 最後一行：
  `18 passed, 0 failed, series requests=6`
- `.local/evidence-crypto-live-20261031/browser-acceptance.json`：
  `passed: 18`、`series_requests_observed: 6`

差距如實記錄如下：

| 項目 | 數字 |
| --- | --- |
| 本輪原定上游預算 | 6 次 Kraken REST 呼叫 |
| 上一輪實際上游呼叫 | **18 次** |
| 超出 | **12 次** |

成因（已確認的工程事實）：

1. 舊 `KrakenProvider._request_count` 只是一個**程序內報告數字**，沒有任何拒絕對話。
   上限 6 從未被程式執行，所以第 7 次及之後照樣開 socket。
2. 「6 次 series 請求」是**瀏覽器自己發出的 HTTP 請求數**，不是上游呼叫數。
   一次 series 讀取可能牽涉多個上游端點（`OHLC`、`AssetPairs`、`Time`），
   重試與 UI 觸發的讀取亦各自開 socket。**不會**把 UI 請求改名為「不計數」來
   讓 18 變成 6；兩者本來就是不同層的量。
3. 舊設計沒有「驗證 run」的概念，重啟、開新分頁或多程序都可以各自累加，
   不存在任何跨程序的共同上限。

本輪修正針對上述三點，而不是修改數字或措辭。

## 2. 設計：所有上游 HTTP 經同一可計數入口

`backend/app/services/upstream_budget.py` 是唯一的預算閘門：

- `KrakenProvider._request` 是 adapter 唯一的上游出口。`OHLC`、`Ticker`、
  `Time`、`AssetPairs`、重試、backfill、UI 觸發讀取全部經過它，因此全部可計數。
- 閘門在 **socket 開啟前**呼叫 `reserve()`；被拒即拋
  `KrakenBudgetExceededError`，不會退避重試、不會改用快取冒充新資料。
- 計數器存放於共享 `rate_limit_counters` 表，同一資料庫的所有程序共用同一份
  預算；重啟不會清零。
- 三個計數器在同一個交易內保留（hour、day、verify run）。任何一層不足即
  rollback 全部，**被拒的呼叫不消耗任何額度**。
- 保留用單一原子語句：
  `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE request_count < :limit RETURNING`。
  同一語句在 PostgreSQL 與 SQLite（≥3.35）皆為並發安全的條件遞增。

### 2.1 兩個獨立預算層

| 層 | 身份／類別 | 窗口 | 用途 |
| --- | --- | --- | --- |
| provider（時） | `upstream:kraken_rest` / `kraken_upstream_hour` | 每 UTC 小時 | 保護帳戶長期配額 |
| provider（日） | `upstream:kraken_rest` / `kraken_upstream_day` | 每 UTC 日 | 保護帳戶每日配額 |
| verification run | `upstream:kraken_verify:<run_id>` / `kraken_verify_run` | 固定 `1970-01-01` sentinel | 單次驗證 run 的硬上限 |

重點：

- **provider 未超限不代表 run 可以繼續**。兩層分開保留，任何一層拒絕都生效。
- run 計數器的窗口是固定 sentinel，**不會隨時鐘翻頁**。只有 operator 明確
  更改 `CRYPTO_VERIFY_RUN_ID` 才會取得新的額度；重啟、新分頁、新程序都不會。
- `cleanup_old_counters` 明確保留 `kraken_verify_run` 類別；否則定期清理會
  把 run 計數器刪走，等於憑空再發一份額度。

### 2.2 快取與讀取放大

- 價格回應沿用 2 秒 in-process 快取；`AssetPairs`／`Time` 改為 1 小時 metadata
  快取（`METADATA_CACHE_TTL_SECONDS = 3600`）。同一持倉／同一頁面的重複讀取
  不會不受控地放大上游呼叫。
- 客戶端要求不同 `limit` 但實際上游參數相同時，快取鍵相同，仍只算一次。
- 快取只影響「是否真的開 socket」；被拒時不寫任何合成資料。

### 2.3 拒絕時的回應

`/market/crypto/*` 讀取被拒時回 HTTP 503，`detail` 內含：

| 欄位 | 意思 |
| --- | --- |
| `reason` | `budget_exhausted` |
| `denied_layer` | `provider_hour` / `provider_day` / `verification_run` |
| `budget` | 三層的已用／上限（真實用量，不是 0） |
| `stored_available` | `/market/crypto/stored`，舊資料仍可讀 |
| `retry_safe` | `false` |

前端（`frontend/src/lib/cryptoBars.ts`、`frontend/src/app/market-data/crypto/page.tsx`）：
收到 `budget_exhausted` 時顯示「Updates paused (local budget)」，**停止自動輪詢**，
保留畫面上最後收到的已完成 K 線並清楚標示；使用者可按「Check now」發出一次
有界讀取。不會以模擬或合成更新填補。

## 3. 設定（全部可由環境變數覆寫）

| 設定 | 預設 | 說明 |
| --- | --- | --- |
| `CRYPTO_MAX_REQUESTS_PER_HOUR` | `60` | provider 每小時上限，須 ≥1 |
| `CRYPTO_MAX_REQUESTS_PER_DAY` | `400` | provider 每日上限，須 ≥ 每小時 |
| `CRYPTO_VERIFY_RUN_ID` | 空 | 驗證 run 名稱；`CRYPTO_VERIFY_MAX_REQUESTS>0` 時必填 |
| `CRYPTO_VERIFY_MAX_REQUESTS` | `0` | run 硬上限；`0` 代表不啟用 run 層 |

`app/config.py` 的 `_validate_crypto_budget_bounds()` 在設定載入時檢查：
時／日上限為正、日 ≥ 時、run 上限非負、啟用 run 上限時必須有名稱。設定錯誤
在啟動時直接報錯，不會靜默以 0 或不設限運行。

## 4. 驗證結果

### 4.1 離線（SQLite，stub transport）

指令（在 `backend/`）：

```
/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend/venv/bin/python \
  -m pytest tests/test_upstream_budget.py -q
```

結果：**13 passed, 1 skipped**（skip 者為 PostgreSQL-only 測試）。

覆蓋（`tests/test_upstream_budget.py`）：

1. 第 7 次被拒，且回報真實用量；
2. provider 與 run 兩層分開，provider 未超限仍會被 run 拒絕；
3. provider 時上限在無 run 上限時同樣生效；
4. 拒絕時 rollback，其他計數器不變；
5. 新小時窗口不會重置 run 計數器；
6. `cleanup_old_counters` 不刪 run 計數器；
7. 未啟用 run 上限時不建立 run 計數器列；
8. 被拒的呼叫不開 socket（transport 呼叫次數不變）；
9. 失敗後重試會再保留一次額度（清除 2 秒快取後）；
10. 不同 `limit` 的重複讀取共用一次上游呼叫；
11. `AssetPairs` metadata 在兩次讀取間命中快取；
12. `budget_exhausted` 回 HTTP 503，而 `/stored` 仍可讀；
13. 子程序（模擬重啟／第二程序）與主程序共用同一 run 預算；期望 `[(3,3),(3,6),(0,6)]`；
14. PostgreSQL-only：16 執行緒並發保留，最終恰好 6 次成功，計數器停在 6。

### 4.2 並發（disposable PostgreSQL）

在一個即棄的本機 PostgreSQL 15 叢集（`/tmp` 新 initdb，port 55439，
`LC_ALL=C LANG=C`）上執行同一檔案：

```
TEST_ENGINE=postgresql ALETHRA_TEST_POSTGRES_URL=<disposable db> \
  .../venv/bin/python -m pytest tests/test_upstream_budget.py -q
```

結果：**14 passed**（包括 16 執行緒並發測試）。完成後已停止該叢集、刪除 `/tmp`
資料目錄，並確認 port 55439 已釋放。**SQLite 不被當作跨程序原子性的證據**。

### 4.3 受影響既有測試（回歸）

以同一解譯器在 SQLite 上執行：

```
tests/test_kraken_provider_offline.py
tests/test_crypto_api.py
tests/test_crypto_instrument_identity.py
tests/test_crypto_series_integrity.py
tests/test_upstream_budget.py
tests/test_safety_controls_focused.py
tests/test_intraday_rest_refresh.py
tests/test_auth_rate_limit.py
```

結果：**133 passed, 1 skipped**。

> 執行注意：`tests/test_intraday_refresh.py` 並不存在；實際檔名為
> `tests/test_intraday_rest_refresh.py`。第一次以錯誤檔名收集會得到 0 項並提早結束，
> 不能當作通過。

### 4.4 前端型別檢查

`frontend/` 下 `npx tsc --noEmit`：**exit 0**。本輪**未**執行 `next build`，
亦未執行 full suite（維持既定 light/build gate）。

## 5. 已知限制

- 預算閘門依賴共享資料庫。同一資料庫的多個程序共用上限；若未來各程序指向
  不同資料庫，則各自計算，需改為共享儲存才可跨資料庫約束。
- `upstream_budget` 的 `AllowAllBudgetGate` 只存在於離線 adapter 測試；
  應用程式經 `crypto_registry.get_crypto_provider()` 注入真正的資料庫閘門，
  不會以 no-op 上線。
- 前端「Updates paused (local budget)」只經型別檢查與單元層推理；本輪沒有
  真實耗盡情境的瀏覽器截圖（本輪不發真實請求，無法自然觸發）。

## 6. 本輪真實上游請求數

**0 次**。本輪所有 Kraken 相關驗證均使用 stub transport 或已保存回應；
沒有 Ticker、OHLC、Time、AssetPairs 或任何私人端點請求，亦無 WebSocket。

## 7. 未完成／下一步

1. B 段：crypto paper 研究垂直切片（沿用既有 paper engine／ledger）。
2. 若日後要再做真實驗證，必須以**新的明確範圍**與**新的 run id** 進行，不能把
   舊的 6 次預算當作可以反覆重置。
3. 多資料庫／多 worker 部署若需要單一全域上限，需把計數器移到共享儲存
   （本輪未實作）。
