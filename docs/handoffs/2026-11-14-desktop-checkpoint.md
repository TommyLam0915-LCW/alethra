# 換機 Checkpoint 交接紀錄 — 2026-11-14

**用途**: 本檔案是舊桌面交給新桌面的唯一交接依據。新桌面上第一個代理**必須先讀本檔**再動手。
**性質**: 這是 **checkpoint / WIP 保存**，不是可上線版本，也不代表 PostgreSQL 已驗證。

---

## 1. 基準與實際修改範圍

| 項目 | 值 |
|---|---|
| Repository | `TommyLam0915-LCW/alethra` |
| Remote 名稱 | `origin` → `https://github.com/TommyLam0915-LCW/alethra.git`（本次已核對實際 URL） |
| 分支 | `live1-impl` |
| 上一個基準 HEAD | `8e2c4bc05b10a15b7f54e2be3fbe70f4813c565f` |
| 本次 checkpoint 前遠端狀態 | `git ls-remote` 查得 `8e2c4bc05b10a15b7f54e2be3fbe70f4813c565f`，與本機 HEAD 一致（0 ahead / 0 behind，無分岔） |
| 本機未提交變更 | 6 個 tracked 修改 + 2 個新測試檔（全部在下表列出，並已纳入本次 commit） |

### 本次提交的檔案及其 sha256（提交前工作區狀態）

| 路徑 | sha256 |
|---|---|
| `backend/app/services/market_data_storage.py` | `837c8e35b366169be47ba355fb8d26428e1ab915c47f24159b58449669bf0e6f` |
| `backend/app/api/simulations.py` | `e6cde0685920c56a7e954f8eaee84e1bd67506055483b4debc0b82bbca7ac32d` |
| `backend/app/api/schemas.py` | `e6a6a96119badbe783d9c27ea4ca15cd549268850e1620b9cf0e89688b379966` |
| `scripts/import_real_data.py` | `fafc3b18f77ddfda6b09fa1bdbdb9d457de5264083e8c62fa7b1bfa4ab5bc196` |
| `backend/tests/test_daily_replay_reads_stored_bars.py` | `7e4180196da14209a51e9e906308ebb218ba8999ba24130fa6407b7094f7795a` |
| `backend/tests/test_import_real_data_guard.py` | `49442c293a4e7bf465b09e4148aa6042fba82e7688f4aa95694cfb9edb96f924` |
| `backend/tests/test_market_data_grain_contract.py`（新） | `357dd648fe2f2251977cc6d10488568dd264af58a3f922f51b00a46f0f02fd4f` |
| `backend/tests/test_daily_grain_http_authoring_roundtrip.py`（新） | `4049988193be859b92231419bd3edbf690358bdf2a551ffb48dcda8b7d739ff8` |
| `docs/handoffs/2026-11-14-desktop-checkpoint.md`（本檔） | — |

`git diff --check`：clean（exit 0，無 whitespace 錯誤）。

---

## 2. Grain slice 做了什麼（範圍只到離線層）

實作主線是「日線 replay 只能讀 1440 分鐘的柱」，並把 `timeframe` 宣告從正式 HTTP 請求一路帶到讀取層：

1. **儲存層**（`market_data_storage.py`）：列身分改為 `(instrument_id, provider_id, interval_minutes, bar_timestamp)` 四欄鍵，讓 `240@00:00` 與 `1440@00:00` 可共存且互不覆寫；`interval_minutes` 可為 NULL，NULL 列永不參與去重。
2. **讀取層**（`simulations.py`）：`_load_stored_ohlcv` 與 `_observed_bar_close` 接受嚴格日線 grain 參數；`_declared_daily_interval_minutes(spec)` 只在 spec **自己宣告** `timeframe == "1d"`（容忍大小寫與前後空白）時回傳 `1440`，否則回傳 `None` 維持舊的無 grain 讀取。
3. **匯入層**（`scripts/import_real_data.py`）：日線匯入明確帶 1440；錯目標資料庫 fail-closed 閘保留。
4. **HTTP 接線**（`schemas.py`）：見下節。

---

## 3. `timeframe` 經 HTTP authoring 被丟棄的問題 — 已修到什麼程度

**根因**：`StrategySpec`（pydantic v2 預設 `extra='ignore'`）沒有宣告 `timeframe` 欄位，所以 `POST /experiments` 帶 `timeframe="1d"` 時，該欄在請求驗證階段被**靜默丟棄**，持久化的 `strategy_spec` 裡根本沒有這個 key，後面的 1440 過濾永遠不會被觸發。

**已修（本輪，只動已批准的 `backend/app/api/schemas.py`）**：
- 新增 `timeframe: Optional[str] = None` 欄位；
- `field_validator` 只接受 `"1d"`（去空白、不分大小寫後正規化為 `"1d"`），其他值（`5m`、`4h`、`1d,5m`、空字串）一律 `ValueError` → HTTP 422；
- `model_serializer(mode="wrap")` 在**未宣告**時把 key 從 dump 中移除，避免寫入 `timeframe: null` 毒化 `str(spec.get("timeframe", "1d"))` 的舊讀取者。

因此本輪已達成：明確 `timeframe="1d"` → 驗證通過 → 持久化進 `ExperimentVersion.strategy_spec` → lock 後保留（lock 只複製 `config_hash`→`strategy_config_hash`，不重新序列化）→ 建立 session 與 advance 時以 1440 過濾。

**尚未修（超出批准清單，等 owner 批准，見 §10）**：
- 兩條既有 preflight 契約測試由 `unsupported` 變成 `invalid`（這是本輪 `schemas.py` 改動造成的**行為回退**，尚未修復，也刻意沒有越界去改 `strategy_templates.py`）。
- `experiments.py` 的修訂（revision）守衛仍以「key 是否存在」判斷，會把合法的 `timeframe="1d"` 來源版本一并拒絕。

---

## 4. F1 / F2 / F3 裁決的完成與未完成

| 裁決 | 內容 | 離線實作 | 真實 PG 驗證 |
|---|---|---|---|
| **F1** | 四欄鍵隔離不同明示 grain；`240@00:00` 與 `1440@00:00` 必須共存、不得互覆寫；只拒絕重複鍵異常與 legacy-NULL 衝突；不自動 backfill | 已完成（storage 層 + `test_market_data_grain_contract.py` 離線合成列覆蓋） | **NOT RUN** |
| **F2** | 本輪明示日线路徑只讀 1440；legacy NULL／未宣告行為另案測試；不順手擴大 4h 行為變更 | 已完成（reader + HTTP authoring 兩層都有回歸；未宣告者行為位元不變） | **NOT RUN** |
| **F3** | 匯入器明確帶 1440，錯目標閘保留；來源／hash／內容驗證與 `data_nature` 語意為獨立聲稱，hash 相符不等於市場真相 | 離線完成（`test_import_real_data_guard.py`） | **NOT RUN**（本輪未獲准執行 import） |

---

## 5. 證據層級：正式 API 回歸 vs 直接 ORM seed 回歸

本 repo 現在刻意把兩層證據**分成兩個檔案**，不可互相取代：

| 層級 | 檔案 | 證明的範圍 | 弱點（為何不夠） |
|---|---|---|---|
| **T1 正式 HTTP authoring round-trip（最高層，本輪新增）** | `backend/tests/test_daily_grain_http_authoring_roundtrip.py` | `POST /experiments` → stored version → `PUT .../lock` → `POST /simulations/versions/{vid}` → `advance`，全程走真實 route 與序列化路徑；證明請求體的 `1d` 宣告**沒被 schema 丟棄**，且實際成交價與游標只取 1440 列 | 仍是 in-process `TestClient` + SQLite，不是已部署服務 |
| **T2 直接 seed／spy（讀取層回歸）** | `backend/tests/test_daily_replay_reads_stored_bars.py` | 繞過 authoring，直接把 `timeframe` 寫在版本列上，驗證 reader 只選 1440、且呼叫端有傳入 grain 過濾 | **無法發現 schema 丟欄位**：如果 `StrategySpec` 把 key 丟了，這個檔案照樣全綠。這正是上一輪被判定「不足以為 HTTP round-trip 證據」的原因 |
| **T3 儲存契約（列身分與去重）** | `backend/tests/test_market_data_grain_contract.py` | 四欄鍵共存、NULL 不參與去重、重複鍵拒絕 | 不涉及 HTTP |

兩層都用**同一組合成列**：同一天同時存在 1440、240、NULL 三種 grain，240／NULL 插在最後，因此若 reader 漏掉 grain 過濾，報出的價格會來自非日線列而立刻可見。全部資料皆為合成 fixture，無真實行情。

---

## 6. 已執行測試：精確命令、結果、對應程式狀態

Runner 皆為 `backend/scripts/run_tests.sh`（已入版控），SQLite 層，離線、無網路外呼、無服務啟動。
通用命令形式（在 repo root 執行）：

```bash
ALETHRA_PYTHON=$PWD/backend/.venv/bin/python \
  ./backend/scripts/run_tests.sh sqlite <backend 相對路徑...> -p no:warnings -q
```

### 6.1 2026-11-14（本次 checkpoint 當天重跑，程式狀態＝上表 sha256）

| 命令（paths 部分） | 結果 | exit |
|---|---|---|
| `sqlite tests/test_daily_grain_http_authoring_roundtrip.py` | `12 passed in 8.20s` | 0 |
| `sqlite tests/test_daily_grain_http_authoring_roundtrip.py tests/test_daily_replay_reads_stored_bars.py` | `25 passed in 14.43s` | 0 |

### 6.2 2026-11-13（grain HTTP 接線完成當下，`schemas.py`＝`e6a6a961…`、`simulations.py`＝`e6cde068…`）

| 組 | 範圍 | 結果 | exit |
|---|---|---|---|
| Group A（grain 全套 + 新層級，18 paths） | 直接相關回歸 | `212 passed in 73.73s` | 0 |
| Group B（既有相容，9 paths：lock／revisions／continuation／run-comparison／api／health／preflight 等） | 相容性 | `2 failed, 123 passed, 15 skipped in 82.75s` | 1 |
| HEAD 對照（把 `schemas.py` 暫時換回 `git show HEAD:...` 只跑那 2 條） | 歸因 | `2 passed in 1.45s` | 0 |

Group B 的 2 條紅燈（**本輪已知失敗，未修，因檔案不在批准清單**）：
- `tests/test_strategy_preflight.py::test_intraday_spec_is_unsupported_on_the_batch_path`
- `tests/test_strategy_spec_contract.py::test_preflight_separates_unsupported_from_invalid`

兩者都是 `assert 'invalid' == 'unsupported'`；HEAD 對照證明紅燈由本輪 `schemas.py` 改動造成，不是既有損壞。修復方案見 §10，**尚未實施**。

### 6.3 狀態對應說明（避免把舊數字當現況）

- `schemas.py` 目前的 sha256 與 11-13 mutation 後「還原記錄」中的 `e6a6a961…` 相同；Group A／Group B 是在該內容下跑的。檔案 mtime（12:29）晚於 Group A（12:27），該次寫入是 mutation 還原動作的結果，內容 hash 未變。新桌面上若要完全移除這個推論縫隙，重跑一次 Group A 即可（約 74 秒）。
- 本輪**沒有**為 checkpoint 重跑整套已完成離線測試（遵守指示）。

---

## 7. 舊證據只代表「當時狀態」，不得覆蓋後續變更

下列數字是**上一輪（grain storage/reader/importer 修補，HTTP 接線之前）**量到的，對應的 `schemas.py` 還沒有 `timeframe` 欄位，因此**不可**宣稱覆蓋目前工作區：

| 舊證據 | 數值 | 量測時的狀態 |
|---|---|---|
| patch 前基準 | `175 passed, 373 warnings in 56.23s`（16 paths） | `8e2c4bc` 乾淨Checkout，無 grain patch |
| patch 後離線回歸 | `200 passed in 63.62s`（17 paths） | grain patch 完成、**HTTP 接線之前** |
| 還原後子集 | `53 passed in 15.21s` | 同上，mutation 還原後 |
| mutants M1–M7 | 各自 `failed`（6 條不同子集），全部被殺 | 同上 |

本輪（HTTP 接線）另外用 3 條**最小**突變驗證新層非空轉（遵守「不追求大量 mutation」）：
- H1「宣告被丟棄」→ `6 failed, 6 passed`
- H2「未宣告時持久化 `null`」→ `1 failed, 11 passed`
- H3「grain 過濾被拿掉」→ `3 failed, 9 passed`
三次突變後來源檔 hash 皆以位元組還原（`e6a6a961…` / `e6cde068…`）。

---

## 8. NOT RUN 範圍（這些**沒有**證據，新桌面不可聲稱已完成）

- PostgreSQL 測試（含本機 disposable DB、既有 PG runner）— NOT RUN
- `CREATE ROLE` / `CREATE DATABASE` / `GRANT` — 未執行、未批准
- `scripts/import_real_data.py` 對任何 DB 的實際寫入 — NOT RUN
- Alembic migration 套用到任何實際 DB — NOT RUN（本 slice 未新增 migration）
- 啟動服務（uvicorn / docker compose / :8100 / :3099）— NOT RUN、未批准
- 真實帳號註冊／登入／瀏覽器驗收 — NOT RUN
- 行情外呼（Twelve Data / Kraken / 任何 provider）— NOT RUN，且明確禁止重新 harvest
- 以真實 Kraken 1440 柱跑通的 end-to-end session lane — NOT RUN（DB A 無資料，見 §9）

---

## 9. DB A 已知狀態（2026-11-13 批准的一次性 admin 唯讀輪，13 queries，exit 0）

- 目標 DB：`ai_investment_lab_test`（本機 Homebrew PostgreSQL 15，`127.0.0.1:5432`，`server_version_num=150019`）。
- Schema 頭：`alembic_version.version_num = c9d0e1f2a3b4`，與 `8e2c4bc` 的 repo 單一 head 相同 → **無 schema drift**。
- 資料：`market_data_bars` 對 `CRYPTO:KRAKEN:BTC-USD` **0 列**（任何 provider）；`market_data_metadata` 0 列。四欄重複、跨 grain 同時間戳、NULL vs typed、同日多筆等探針各回 0 列 → 這是「**無資料可驗**」，**不是**「資料乾淨」。其他 instrument 是否有柱：UNKNOWN（掃描範圍未批准）。
- 權限：執行用 role `alethra_test_runtime` 只有 DB 的 CONNECT 與 schema `public` 的 USAGE；`market_data_bars`、`market_data_metadata`、`alembic_version` 仍為 `<default>`（僅 owner 可見）→ 該 lane 目前**讀不到也寫不進**這三張表。
- 結論：stored-bar session 主線卡在 **資料＋授權**，不卡在**這一輪**的程式。F1–F3 的 PG 回歸必須跑在 **disposable 已遷移 DB**，不得跑在 DB A。
- 未解的衝突（屬 owner 裁決，不是代理工作）：獨立匯入器身分需要新憑證素材，與「不建立新 passfile」的原則相撞。

---

## 10. 待批：出批准清單之外的**精確位置及必要差異**（尚未實施）

owner 指示「先列出精確位置及必要差異，不要順手擴大實驗 API 重構」。以下三處**只列不動**。

### 10.1 `backend/app/api/strategy_templates.py`（殺掉 §6.2 的 2 條紅燈）

`POST /api/v1/strategy-specs/preflight` 目前先做 `StrategySpec.model_validate`（`:440-447`），再做 intraday 分支（`:449-464`）。`5m` 現在會先撞驗證器 → 回 `invalid`，違反該 endpoint 自己發布的「intraday 屬於 `unsupported`」契約（描述見 `:413-419`）。

必要差異（純搬移 + 一個運算式改寫，不新增行數）：把 `:449-464` 整段移到 `:440` 的 `try:` **之前**，並將 `_result("unsupported", parsed.template_type, …)` 改為 `_result("unsupported", template_type if isinstance(template_type, str) else None, …)`（因為搬移後 `parsed` 尚未存在，寫法與既有 `:433-438` 分支一致）。

注意範圍限制：`INTRADAY_TIMEFRAMES = {"1m","5m","15m","30m","1h"}`（`app/engine/intraday.py:56`）。重新排序只會讓這**五個**字串回到 `unsupported`；`"4h"` 從來不是成員，搬移後仍回 `invalid`（這是正當分類，非缺陷，但報告必須講清楚）。

另需同檔兩處補充（否則對外契約會出現不實陳述）：
- `_EXECUTED_FIELDS`（`:169-181`）加入 `"timeframe"`；
- `_FIELD_SEMANTICS`（`:183-202`）加一列事實描述，例如：宣告日線 grain；只有 `1d` 在本路徑可執行，reader 據此以 1440 過濾。
（理由是引擎**確實**讀這個 key：`app/engine/backtest.py`、`app/api/simulations.py`；預設文字 `Stored; the engine does not read it.`（`:322`）會變成不實陳述。）

### 10.2 `backend/app/api/experiments.py:707-719`（修訂守衛）

現行以 key **是否存在**判定：`INTRADAY_KEYS = frozenset({"session","timeframe"})`（`:577`）＋ `if intraday_keys or base_canonical is None: raise 400`。後果：經正式 API 建立、合法宣告 `timeframe="1d"` 的版本，**反而無法被修訂**。

必要差異（值導向，保留 fail-closed 精神）：

```python
base_spec = base.strategy_spec or {}
offending = sorted(
    key for key in set(base_spec) & INTRADAY_KEYS
    if key != "timeframe"
    or str(base_spec[key]).strip().lower() != "1d"
)
base_canonical = _canonical_spec(base_spec)
if offending or base_canonical is None:
    unsupported = ", ".join(offending) if offending else "非日線頻率"
    ...
```

效果：`session` 存在仍拒絕；`timeframe` 只有在值不是正規 `1d` 時拒絕（`None`、`5m` 等維持拒絕，不放寬）；合法 `1d` 來源版本可修訂。

已核對**不需要**改的路徑：`:68` 建立時的 dump、`:199` lock、`:460-495` continuation（deep dict copy 保留 key）。
需一併留意的**次級效應**：`_classify_change(base_canonical, spec_dict)`（`:606-621`）比對 `_canonical_spec()`（`:580-590`）輸出。對「舊版未宣告 → 新版宣告 `1d`」的修訂，會把 `timeframe` 列為 `changed_fields` 之一（`strategy_only`）。這是**老實**的分類（spec 位元組確實改變），但如果 owner 希望「隱含日線 → 明示日線」不算研究變更，必須**明講**並在 `_canonical_spec` 或 `IGNORED_CHANGE_FIELDS` 做一個有意的決定；不得默認。

### 10.3 `frontend/src/components/experiments/RevisionPanel.tsx:99`（**只列，不建議現在動**）

`if (spec.session || spec.timeframe)` 是 presence 導向的前端鏡像，同樣會擋掉合法 `1d`。修正需要與 10.2 同批，且**瀏覽器驗收本輪未批准**，所以只能算在後續工作包裡。

---

## 11. 未上 GitHub 的本機檔案（**必須手動搬移或按固定來源重建**）

`git push` **不會**帶走下列內容。這些是舊桌面上的本機檔案。

### 11.1 Evidence 目錄（皆在 `~/Backups/`，目錄 mode 700／0700）

| 路徑 | 用途 | 檔案數 | 體積 | 搬移判定 |
|---|---|---|---|---|
| `~/Backups/alethra-grain-http-20261113-run1/` | 本輪 HTTP 接線群組 A/B、H1–H3 突變腳本與輸出、`schemas.py` 突變前／後副本 | 11 | 228 KB | 需要搬（離線可重跑腳本＋原始輸出，屬可複現證據）。內含本機絕對路徑，**不得**直接上公開 repo |
| `~/Backups/alethra-grain-patch-20261113-run1/` | 上一輪 storage/reader/importer patch：175/200/53 原始輸出、M1–M7、`probe_prepatch.py`、`run-mutations.sh`、三個 `.postpatch` 來源副本 | 19 | 244 KB | 需要搬（同上） |
| `~/Backups/alethra-db-a-admin-readonly-20261113-run1/` | DB A 一次性 admin 唯讀輪的 13 組 `.sql`／`.out`／`.err` ＋ `query-status.txt` | 40 | 108 KB | **不搬**、不上 GitHub：屬管理身分查詢產物。必要結論已寫進 §9；如需保留，僅在本機封存 |
| `~/Backups/alethra-kraken-1440m-download-20261113-run1/` | 已下載的真實 payload 與下載驗證 log | 7 | 188 KB | **可從固定來源重建**，見 §11.2；不需整體搬移，但 `verify.log` 建議搬 |
| `~/Backups/alethra-p0-probe-20261112-run1/` | P0 探針（含 `secret-scan.txt`） | 98 | 412 KB | 不搬；含掃描報告，非本輪工作依賴 |
| `~/Backups/alethra-pytest-batch1-20261112-run1/`、`-prep/`、`alethra-pytest-batch2-20261112-run1/` | A/B pytest 批次證據（batch2 含 `passfile-record.txt`、`secret-scan*.txt`、`secret_scan.py`） | 12／4／22 | 108／32／164 KB | **不上 GitHub**（含 passfile 紀錄）。已入版控的 commit `8e2c4bc` 承載其結論 |
| `~/Backups/alethra-pg15/` | PG15 admin 復原紀錄（含 `secret-state.txt`、`secret-fingerprints.txt`） | — | — | **不上 GitHub**；本機憑證相關，原地保留 |
| `~/Backups/alethra-save-20261112-commit-8e2c4bc.txt` | 上次保存紀錄（2,423 bytes） | 1 | 2.4 KB | 建議搬（小型純文字，記錄 commit 範圍） |

**含敏感檔名的項目（一律排除在 commit／公開 repo 之外）**：
`alethra-pytest-batch2-20261112-run1/passfile-record.txt`、同目錄 `secret-scan*.txt` 與 `secret_scan.py`、`alethra-p0-probe-20261112-run1/secret-scan.txt`、`alethra-pg15/**/secret-state.txt`、`alethra-pg15/**/secret-fingerprints.txt`。本檔只記錄檔名與體積層級，不記錄其內容。

### 11.2 已下載的真實 payload（**不重新 harvest、不重新下載、不刪**）

| 欄位 | 值 |
|---|---|
| 本機路徑 | `~/Backups/alethra-kraken-1440m-download-20261113-run1/live2b_warehouse_bars_1440m.json` |
| bytes | `57781` |
| sha256 | `e481093bcc12bebc8589c2a838ce445c1bb0692e606134b671e4ebea66646ff5` |
| Git blob | `74bad3a6fabd9f1660c789a2e30d80db0aec66e2`（size 57781，由 `tree.json` 查得） |
| 來源 repo | `TommyLam0915-LCW/alethra-customer-evidence` |
| 來源 commit | `fbb014e7a3eb993c202380988a6ef421584b0942` |
| **repo 相對路徑**（本次自 `tree.json` 查出並記錄） | `live2b_warehouse_bars_1440m.json`（repo 根，非子目錄） |
| 搬移需求 | **可從固定來源重建**：依 pinned commit／blob 從 evidence repo 取回即可；不需安全搬移 binary，也**不得**回 venue 重新 harvest |
| 同目錄 240 柱檔 | `tree.json` 亦記錄 `live2b_warehouse_bars_240m.json`（blob `4d8277372301e840d1881522a38cfc811a26f524`，56,289 bytes），本機**未**下載該檔 |

**封裝日期尚未釐清**：該 payload 的封存日期在現有證據中**未被證實**，因此本檔不將其記為 2026-10-31（亦不記為任何其他日期）。需要日期時，要從 blob 內容或 evidence repo 的 manifest 另行查證。

### 11.3 `/tmp` 下的一次性腳本

`/tmp/command-a.sh`、`/tmp/command-b.sh`：本次檢查**已不存在**（ABSENT，非「已搬移」）。若後續需要其內容，只能從對應 evidence 目錄的 log 重建，不得宣稱仍持有原件。

---

## 12. 新桌面上第一個具體開發任務

**任務**：取得批准後，修復 §10.1 造成的 2 條紅燈，並讓 preflight 對外契約重新自洽。

精確起手式（新桌面先做 1–3，第 4 項要**先取得批准**，因為 `strategy_templates.py` 與 `experiments.py` 不在既有批准清單內）：

1. `git fetch origin && git checkout live1-impl && git pull --ff-only`，確認 HEAD 為本次 checkpoint commit。
2. 重跑 T1＋T2 兩檔（§6.1 第二條命令，約 15 秒），確認基準綠燈與本檔 §6.1 一致。
3. 重跑那 2 條紅燈，確認失敗訊息仍是 `assert 'invalid' == 'unsupported'`（若已不同，表示狀態有變，先回報再動手）。
4. **提出並取得批准**後才實施 §10.1 的搬移＋兩個 mapping 補充；同批或另批處理 §10.2 的值導向守衛；實施後重跑 Group A 與 Group B（命令見 §6）並回報 exit code。
5. **不要**在這個任務裡順手做：前端 `RevisionPanel.tsx`、瀏覽器驗收、PG 測試、import、服務啟動（這些都未批准）。

---

## 13. 新桌面依賴恢復（版本、環境變數**名稱**、步驟；不含任何秘密值）

### 13.1 已驗證可用的本機工具鏈（舊桌面實測）

| 項目 | 版本 |
|---|---|
| OS / arch | macOS (Darwin), arm64 |
| Git | 2.44.0 |
| Node.js | v26.5.0 |
| npm | 11.17.0 |
| backend venv Python | **Python 3.12.3**（實測 `backend/.venv/bin/python -V`） |
| uv | 0.13.7 (Homebrew, aarch64-apple-darwin) |
| Shell | zsh |

### 13.2 後端依賴

```bash
cd backend
uv venv .venv                     # 或 python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
`backend/requirements.txt` 已入版控。需要 `scikit-learn`、`fastapi`、`sqlalchemy`、`pandas`、`numpy`、`psycopg2-binary` 等（完整清單以該檔為準）。

### 13.3 前端依賴

```bash
cd frontend
npm ci        # frontend/package.json 與 frontend/package-lock.json 皆已入版控
```
注意：repo 根目錄**沒有** `package.json`，所以根目錄的 `package-lock.json` 是誤產物，未納入版本控制（見 §14）。

### 13.4 環境變數（**只列名稱**；值一律由 owner 在本機提供，不得進 repo、commit、聊天或 log）

| 變數名稱 | 用途 | 本輪是否需要 |
|---|---|---|
| `DATABASE_URL` | 後端 SQLAlchemy 連線 | 本輪離線測試**不需要** |
| `JWT_SECRET_KEY` | 簽章金鑰 | 本輪不需要 |
| `ALETHRA_PYTHON` | `backend/scripts/run_tests.sh` 使用的直譯器絕對路徑 | **需要**（§6 命令已帶） |
| `ALETHRA_TEST_POSTGRES_URL` | 既有 PG runner 的 disposable DB 連線 | 本輪 NOT RUN，不需要 |
| `ALETHRA_PG_TEST_URL` | 部分 PG-only 測試的跳過判定來源 | 本輪 NOT RUN，不需要 |
| `PGPASSFILE` | libpq 憑證檔路徑（受保護 runtime 用） | 本輪不需要、未建立新檔 |
| `TWELVEDATA_API_KEY` / `STRIPE_API_KEY` / Kraken 相關金鑰 | provider 外呼 | 本輪明確**禁止**外呼，不需要 |

離線跑 grain 回歸**只需要** `ALETHRA_PYTHON`；不連 DB、不需任何憑證。

### 13.5 SQLite 測試 runner

```bash
cd <repo-root>
ALETHRA_PYTHON=$PWD/backend/.venv/bin/python \
  ./backend/scripts/run_tests.sh sqlite <backend-relative test paths…> -p no:warnings -q
echo "EXIT=${pipestatus[1]}"     # zsh；bash 用 ${PIPESTATUS[0]}
```
Runner 會自行在 temp 目錄建 SQLite 檔，並明確提示「this run is not a PostgreSQL result」。

### 13.6 本機 PostgreSQL 15（只在**另案批准後**才用）

Homebrew `postgresql@15`，由 launchd `KeepAlive` 託管 —— **不得 kill**、不得刪 `postmaster.pid`、不得改 `pg_hba` 為 trust、不得 `initdb`／刪 data／重裝。連線一律走既有受保護 runtime 身分與 `PGPASSFILE`，不使用管理身分跑 app/API。

### 13.7 既有交接參考

- `docs/handoff/NEW_LAPTOP_RESTORE_2026-11-09.md`（上一次換機，含前端 build 失敗與 npm audit 待辦）
- `docs/handoff/TRAVEL_HANDOFF.md`
- `AGENTS.md`（交付／提交／推送驗證規則）

---

## 14. 刻意**未**提交的未追蹤檔案（保留在本機，逐項理由）

| 路徑 | bytes | sha256（前 16） | 判定與理由 |
|---|---|---|---|
| `.qoder/settings.local.json` | 2,677 | `bf33dc607f7c8dc9` | 不提交：機器本地 IDE 權限設定，含本機路徑，屬個人環境配置 |
| `acceptance-results.json` | 7,389 | `4c9be97ccad0798c` | 不提交：2026-10-12 瀏覽器驗收跑的原始產物（audit artifact），非本輪工作；提交會混入與程式無關的歷史輸出 |
| `backend/tests/test_c1_c4_safety_cases.py.bak` | 12,880 | `b644ce60db2765e9` | 不提交：既有 tracked 測試檔的重複備份，無內容價值 |
| `backend/tests/test_c1_c4_safety_cases.py.bak2` | 12,921 | `c8af9858fcf02b25` | 不提交：同上 |
| `package-lock.json`（repo 根） | 87 | `7ea7402cd734f55f` | 不提交：內容僅 `{"name":"d705b961", … "packages":{}}`，且根目錄**無** `package.json` → 誤產物，非真實 lockfile。真正的 lockfile 是 `frontend/package-lock.json`（已 tracked，本輪未改） |

以上皆**保留在原位**，未刪除、未移動（遵守「不要為了讓 git status 乾淨而刪檔」）。

---

## 15. 安全聲明

- 本次提交前已對**將提交的實際內容**做秘密檢查：diff 的 added lines 與兩條新測試檔全文，對 `password|secret|api_key|token|bearer|PRIVATE KEY|DSN|AKIA|ghp_|xox[baprs]-|本機絕對路徑|帳號名` 等模式**零命中**。
- 既有 tracked 內容中屬**合成 fixture** 的測試用 DSN（`test_import_real_data_guard.py` 內 `not-a-real-secret@localhost`）與占位字串（`import_real_data.py` 的 `USER:PASSWORD` 說明文字）為既有內容、本輪未新增，且非真實憑證。
- 本檔不包含任何秘密值、DSN、密碼、token 或 passfile 內容。
- 管理身分查詢產物（DB A dir、`alethra-pg15/**`、`passfile-record.txt`）一律**未**納入提交，並已在 §11 標為不可上公開 repo。

---

## 16. 一句話狀態

Grain slice 的 storage／reader／importer 與**正式 HTTP `timeframe` 持久化**已在 SQLite 離線層實作並通過 T1 層回歸（12 passed），但（a）preflight 的 `unsupported` 契約由本輪改動產生 2 條已知紅燈尚未修，（b）修訂守衛仍會拒絕對外合法宣告 `1d` 的版本，（c）所有 PostgreSQL／grant／import／服務／真實資料／瀏覽器層**完全未驗證**。本 commit 是 checkpoint，不是完成版。

---

## 17. 本機接手增量（2026-11-14，新桌面）— §10.1 preflight 契約修復

本節是**追加**記錄，不覆寫 §1–§16 的任何歷史數字；§6／§7 屬舊桌面的量測，本機未重跑整套已完成項目，因此那些數字仍只代表舊桌面當時狀態。

### 17.1 接手基準與取得過程（皆為本機實測）

| 項目 | 值 |
|---|---|
| repo root | `~/Documents/Qoder/2026-11-03/ac51e592/alethra`（本機絕對路徑依本檔慣例不記錄） |
| OS | Darwin 22.4.0（macOS 13.3）arm64，host `ChakdeMac-mini.local` |
| `git remote get-url origin` | `https://github.com/TommyLam0915-LCW/alethra.git`（與 §1 相符） |
| `git remote get-url github` | 同一 URL（本 checkout 有兩個同 URL remote） |
| 接手起點 | `live1-impl` @ `dd948f4`，**0 ahead / 13 behind**，working tree 乾淨，`stash@{0}`／`stash@{1}` 未動 |
| `git ls-remote origin live1-impl` | `920a9c52d5bce7944ca07ffb4c6e1eb945d042fc` |
| 更新方式 | `git merge --ff-only github/live1-impl` → HEAD = `920a9c52…`，與 checkpoint 逐字相同 |
| 未使用 | 無 merge commit、無 rebase、無 force-push、無 `reset --hard`、無 `git clean`、未改任何 fetch／upstream 配置 |

配置事實（供下一個代理避免誤判）：本 checkout 的 `remote.origin.fetch` refspec 只映射 `feat/strategy-revision-and-research`，所以 `origin/live1-impl` 這個 remote-tracking ref 在本機**不存在**；`live1-impl` 的遠端狀態一律以 `git ls-remote` 直接查詢，或走同 URL 的 `github` ref。

### 17.2 本輪修改（3 檔）

程式差異（`backend/app/api/strategy_templates.py`）：
1. `preflight_strategy_spec`：把 intraday grain 分支**移到** `StrategySpec.model_validate` 之前，與既有 `template_type` 的 unsupported 分支同優先序 —— 根因是 `schemas.py` 的 `timeframe` 驗證器只收 `1d`，`5m` 會先撞驗證器而誤報 `invalid`。
2. 搬移後的 `template_type` 由 `parsed.template_type` 改為 `template_type if isinstance(template_type, str) else None`（搬移後 `parsed` 尚未存在，寫法與既有分支一致）。
3. `_EXECUTED_FIELDS` 加入 `"timeframe"`；`_FIELD_SEMANTICS` 新增 `timeframe` 列。依據是本輪查證：`app/engine/backtest.py:287` 與 `app/api/simulations.py:244-260` 確實讀這個 key，沿用預設文字「Stored; the engine does not read it.」會成為不實陳述。

測試差異：
- `tests/test_strategy_preflight.py`：新增 `test_declared_daily_timeframe_is_valid_and_an_unknown_grain_is_invalid`（釘住宣告 `1d` 仍 `valid`、`4h` 仍屬 `invalid`，未擴大 intraday 能力）。
- `tests/test_strategy_spec_contract.py`：把 `frequency`、`timeframe` 納入 `executed_by_engine is True` 的斷言集合。
- 兩條原紅燈的斷言**未刪除、未跳過、未削弱**。

修復後工作區內容（提交前）：

| 路徑 | sha256 |
|---|---|
| `backend/app/api/strategy_templates.py` | `d90edcf76eede819787c84d39446abb59441e0766cdda5604ec5433df7c290f1` |
| `backend/tests/test_strategy_preflight.py` | `b4cbe49bdd1ab09da3e5ff4a34906bb9d1ab93cd89b273c2510b91f9cdd1d914` |
| `backend/tests/test_strategy_spec_contract.py` | `35d980493849e05f102322504183e6cbbe58ce2ae796deee4a065e0a7e968afb` |

### 17.3 `DATA_PROVIDER=demo` 是本離線 lane 的必要配置

- 本機 `backend/.env` 設 `DATA_PROVIDER=twelvedata`，`app/services/research_runs.py:133` 會先解析 provider；因此**在未修改的乾淨 checkout 上**，`tests/test_experiment_revisions.py::TestCreateRevision::test_revision_can_be_researched_directly` 就會以 `BacktestRefused: Unsupported asset: DEMO-ALPHA` 失敗（`app/engine/backtest.py:372-377`）。這是**配置差異**，不是程式回退； attribution 方式：以 `920a9c52` 的乾淨工作樹跑同一條測試，照樣紅。
- 該拒絕發生在取用行情**之前**，所以這一輪沒有任何 provider 外呼。
- 本輪所有正式結果一律加 `DATA_PROVIDER=demo` 前綴，讓 lane 與舊桌面的離線基準等價。
- 對照用工作樹的陷阱：`git worktree` 不含未追蹤的 `backend/.env`／DB 檔／`.venv`，直接拿來當「基準」等於換了一套配置；必須先對齊 `.env` 才可比較。

### 17.4 可重跑的完整命令（14 paths，於 repo root）

```bash
DATA_PROVIDER=demo ALETHRA_PYTHON=$PWD/backend/.venv/bin/python \
  ./backend/scripts/run_tests.sh sqlite \
  tests/test_strategy_preflight.py \
  tests/test_strategy_spec_contract.py \
  tests/test_daily_grain_http_authoring_roundtrip.py \
  tests/test_daily_replay_reads_stored_bars.py \
  tests/test_market_data_grain_contract.py \
  tests/test_import_real_data_guard.py \
  tests/test_stored_market_data.py \
  tests/test_experiment_version_lock.py \
  tests/test_experiment_revisions.py \
  tests/test_experiment_continuation.py \
  tests/test_run_comparison.py \
  tests/test_intraday_simulation_api.py \
  tests/test_intraday_opening_range.py \
  tests/test_intraday_rest_refresh.py \
  -p no:warnings -q -rf
echo "EXIT=${pipestatus[1]}"
```

直譯器為本機 `backend/.venv/bin/python`（Python 3.12.16；`backend/requirements.txt` 在這 13 個 commit 未變更，`pip check` 無破損）。

### 17.5 結果（同一 lane、同一命令的前後對照）

| 狀態 | 範圍 | 結果 | exit |
|---|---|---|---|
| `920a9c52` 基準 | 僅 T1＋T2 兩檔 | `25 passed in 14.49s` | 0 |
| `920a9c52` 乾淨（未修） | 上述 14 paths（`DATA_PROVIDER=demo`） | `2 failed, 203 passed, 15 skipped in 84.28s` | 1 |
| 本輪修復後（最終程式） | 同上 14 paths | `206 passed, 15 skipped in 86.00s` | 0 |
| 本輪修復後（最終程式） | 4 條節點級目標 | `4 passed in 2.53s`，4 條皆 `PASSED` | 0 |

206 = 203 + 轉綠的 2 條 + 本輪新增 1 條，加總自洽。修復前的兩條失敗訊息仍是 `assert 'invalid' == 'unsupported'`。
突變檢查 M1（把 `timeframe` 從 `_EXECUTED_FIELDS` 移除）→ `test_every_spec_field_states_whether_the_engine_reads_it` 失敗（`assert False is True`），證明新增斷言會真的偵測到；還原後檔案 sha256 與上表逐位元組相同。

### 17.6 15 條 skip 的代表意義

**skip 不等於 PostgreSQL 通過**。15 條全來自 `ALETHRA_PG_DATABASE_URL` 未設定的跳過判定（主要落在 `tests/test_intraday_simulation_api.py`）。本機 PG 相關變數（`ALETHRA_PG_TEST_URL`／`ALETHRA_TEST_POSTGRES_URL`）皆 UNSET，PG 層維持 **ENV_BLOCKED / NOT RUN**。

本輪未執行、也未聲稱完成：PostgreSQL 測試、`CREATE ROLE`／`CREATE DATABASE`／`GRANT`、Alembic migration、真實 import、服務啟動、真實帳號註冊／登入／瀏覽器驗收、任何 provider 外呼、以真實 Kraken 1440 柱跑通的 session 閉環。

### 17.7 仍未完成（下一步）

- **§10.2**：`backend/app/api/experiments.py:707-719` 的修訂守衛仍以「`timeframe` key 是否存在」拒絕，導致經正式 API 合法宣告 `1d` 的版本無法被修訂。本節只記錄，尚未實施。
- **§10.3**：`frontend/src/components/experiments/RevisionPanel.tsx:99` 的 presence 導向鏡像；且本機 `frontend/node_modules` 仍是 `package.json` 變更（`c2a3db5`／`73709f6`）之前的狀態，動前端前需 `npm ci`。
- **真實資料閉環**：§9 的資料＋授權阻礙未解。
- **舊桌面 evidence 未搬移**：本機 `find ~` 查證 `~/Backups/alethra-grain-http-20261113-run1`、`alethra-grain-patch-20261113-run1`、`alethra-kraken-1440m-download-20261113-run1`、`alethra-p0-probe-20261112-run1`、`alethra-pytest-batch1/2-20261112-run1`、`alethra-pg15` **全部 ABSENT**，`alethra-customer-evidence` 亦未在本機 clone。因此 §6／§7 的原始輸出與 H1–H3／M1–M7 腳本**無法在本機覆核**；本節引用它們時只作為舊桌面歷史，不作為本機量測。真實 1440 payload 依 §11.2 的 pinned commit／blob 可重建，本輪未下載、未 harvest。

## 18. 本機接手增量（2026-11-14，新桌面）— §10.2 修訂守衛改為值判定

本節是**追加**記錄，不覆寫 §1–§17 的任何歷史數字。

### 18.1 程式差異（1 檔、1 個 hunk：`@@ -705,10 +705,23 @@`）

`backend/app/api/experiments.py:708-727`（`create_revision`）由「`timeframe`／`session` key 是否存在」改為**依值判定**：

- `timeframe` 值經 `strip().lower()` 後等於 `1d` → 不列入 `offending`，進入既有修訂流程；經正式 API 合法宣告 `1d` 的版本不再被自己的守衛擋掉。
- `5m`／`1h`／`4h`／`None`／`""`／任何 `session` → 仍由**值**觸發 400，狀態碼與 detail 文字未變。**不用 truthiness** 把非法值當成「未宣告」。
- key 完全不存在（legacy 版本）→ 走原本 `_canonical_spec` 路徑，未宣告行為與本輪之前相同。
- lock、版本隔離、擁有權、quota、event、重複設定拒絕、research 觸發邏輯零改動；未擴大 intraday 能力，也未繞過正式 API。

| 路徑 | sha256 |
|---|---|
| `backend/app/api/experiments.py` | `26350f70cb2357b2ac7dd4a5e833ec4a022ff9c63fa8efd7c437a88ae4468a57` |
| `backend/tests/test_experiment_revisions.py` | `ea5328930251c8643d756a274576904f3bacb12e86fc6b5eee1fe1abe47dca15` |

### 18.2 四條新增回歸證明（`tests/test_experiment_revisions.py:445-600`，class `TestDeclaredDailyTimeframeRevisions`）

四條都走正式 API（`POST /api/v1/experiments/` → `POST /api/v1/experiments/{id}/revisions`），不以 ORM seed 代替整條驗收：

| # | 測試 | 斷言 |
|---|---|---|
| 1 | `test_declared_1d_source_version_is_revisable` :477 | 先確認持久化的 `timeframe == "1d"`，再取 201；`changed_fields == ["commission_rate"]`、`change_kind == "strategy_only"`、`is_current is True`、`version_number == 2` |
| 2 | `test_revision_keeps_the_timeframe_declaration` :498 | 新版本 spec 仍宣告 `1d`（並帶上新成本），來源版本 spec 未被改寫 |
| 3 | `test_undeclared_to_declared_1d_is_a_strategy_change_not_a_no_op` :518 | 來源未宣告（無 `timeframe` key）時，只宣告 `1d` 的修訂 → `changed_fields == ["timeframe"]`、`strategy_only`、新版本 `config_hash` 不同於來源、該新版本 id 的 `ResearchRun` 列數 `== 0` |
| 4 | `test_guard_boundaries_are_not_loosened` :562 | `5m/1h/4h/None/""` 與 `session={"timeframe":"5m"}` 各自 400（detail 含「不會自動降級」），最後版本數仍為 1 |

第 4 條的 base 列以 ORM 寫入，因為這些值無法經正式 API 產生（`app/api/schemas.py` 的 `timeframe` 驗證器先擋）；它只證明守衛本身，不代替第 1–3 條的驗收路徑，手法與既有 `test_intraday_source_version_is_refused_not_downgraded` :304 一致。

**Owner 產品決定（2026-11-14）已固化在測試中**：「未宣告 → 明示 `1d`」保留為 `strategy_only` 變更，不是 no-op、不是純展示變更——它可能把 legacy 未標註的混合讀取改為嚴格 1440 分鐘柱，因此不得沿用舊研究結果並冒稱等價。沿用既有研究失效／重研究機制，未另造新系統。

### 18.3 `is_current` 的既有契約（本輪未改，記錄以免下一個代理誤讀）

未鎖定的來源版本被修訂後，`app/api/experiments.py:796` 仍把 `experiment.current_version_id` 移到新版本，回應 `is_current is True`；既有 `tests/test_experiment_revisions.py:94` 已釘住同一行為。宣告 `1d` 不改變這件事，lock 分支也未觸碰。

### 18.4 證據範圍（**不得擴大解讀**）

第 3 條證明的只有兩件事：(a) 新版本自身的 `ExperimentVersion.config_hash` 不同於來源；(b) 以該新版本 id 為 `experiment_version_id` 的 `ResearchRun` 列數為 0。其機制依據是 `app/services/research_runs.py:92-98`——每次建立 run 時以 `compute_config_hash(spec)` 寫入該列。

這**不等於**已驗證所有下游研究結果使用路徑：本輪未逐一查證其他讀 `config_hash`／`ResearchRun` 的消費端（run 比對、匯出、學習／建議通道、前端展示等）在「未宣告 → 明示 `1d`」情境下是否全部正確失效並要求重新研究。那些路徑狀態為 **NOT RUN**，不可引用本節宣稱已完成。

### 18.5 可重跑的完整命令（12 paths，於 repo root）

```bash
DATA_PROVIDER=demo ALETHRA_PYTHON=$PWD/backend/.venv/bin/python \
  ./backend/scripts/run_tests.sh sqlite \
  tests/test_experiment_revisions.py \
  tests/test_experiment_version_lock.py \
  tests/test_experiment_continuation.py \
  tests/test_experiment_record.py \
  tests/test_durable_run_customer_path.py \
  tests/test_model_version_lifecycle.py \
  tests/test_run_comparison.py \
  tests/test_strategy_preflight.py \
  tests/test_strategy_spec_contract.py \
  tests/test_daily_grain_http_authoring_roundtrip.py \
  tests/test_daily_replay_reads_stored_bars.py \
  tests/test_market_data_grain_contract.py \
  -p no:warnings -q -rf -rs
echo "EXIT=${pipestatus[1]}"
```

選檔依據（本機實測）：`grep -rln '/revisions' tests/*.py` 命中 7 檔，其中 6 檔真正 POST 實驗修訂端點（`test_durable_run_customer_path.py:152`、`test_experiment_continuation.py:75`、`test_experiment_record.py:50`、`test_experiment_version_lock.py:219`、`test_model_version_lifecycle.py:48` 與 `test_experiment_revisions.py`）；`test_research_drafts.py:197` 是 `/research-drafts/{id}/revisions` 的不同語意，故不列入。`test_personalization.py`／`test_personal_strategy_learning.py` 只命中 `revisions` 字樣、非該端點，同樣不列入。其餘 6 檔為 §17.4 中與 grain／preflight／contract 直接相關的 lane。

### 18.6 結果（同一離線 lane、`DATA_PROVIDER=demo`）

| 範圍 | 結果 | exit |
|---|---|---|
| 上述 12 paths（最終程式） | `161 passed in 99.39s (0:01:39)`、0 failed、0 skipped（`-rs` 無 skip 行可印） | 0 |
| `tests/test_experiment_revisions.py` 單檔（最終程式） | `20 passed in 13.97s`（既有 16 條 + 新增 4 條） | 0 |

非空洞性檢查於交付前已執行，本輪不再新增變體：M1 還原為 presence 拒絕 → `2 failed, 18 passed`（恰好是證明 1、2，其餘 18 條仍綠，表示沒有既有測試依賴「拒絕 `1d`」）；M2 完全移除值檢查 → `2 failed, 18 passed`（既有 intraday 測試＋證明 4 一起紅）；M3 把 `timeframe` 塞進 `IGNORED_CHANGE_FIELDS` → `1 failed, 19 passed`（恰好是證明 3）。三個變體之後都以 sha256 驗證逐位元組還原為上表的兩個 hash。

這是「直接相關回歸集合」，**不是 full suite**。本機未執行、也未聲稱完成：PostgreSQL 連線／測試、`CREATE ROLE`／`CREATE DATABASE`／`GRANT`、Alembic migration、真實 import、服務啟動、真實帳號註冊／登入、瀏覽器驗收、任何 provider 外呼、以真實 Kraken 1440 柱跑通的 session 閉環 —— 全部維持 **NOT RUN**（與 §8、§17.6 相同）。

### 18.7 仍未完成

- **§10.3**：`frontend/src/components/experiments/RevisionPanel.tsx` 的 presence 導向鏡像——本轮批准開始處理，交付前該檔尚未修改，前端結果另節記錄。
- §10.2 未涵蓋的子效應（例如 `_classify_change` 對其他欄位的標註語義、§18.4 列出的下游消費端）維持未實施／NOT RUN。
- 真實資料閉環與 §11／§17.7 未搬移的 `~/Backups/*` evidence 目錄狀態不變。
