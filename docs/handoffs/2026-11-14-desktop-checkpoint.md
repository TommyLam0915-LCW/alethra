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

## 19. §10.3 前端：RevisionPanel 的支援性判斷改與後端契約對齊（本節為該輪唯一保存）

本節記錄 2026-11-14 那一輪「僅本機修改」的前端交付，並由本節的 commit 保存。修改範圍只有三個檔案加本文件；`backend/app/api/experiments.py`（§18）在本輪完全未被觸碰，後端仍是支援性與拒絕的唯一權威。

### 19.1 檔案與逐位元組狀態

| 檔案 | 行數 | sha256（前 12） |
| --- | --- | --- |
| `frontend/src/lib/revisionSpec.ts`（新） | 115 | `c14c8649c3b3` |
| `frontend/tests/revisionSpec.test.mjs`（新） | 158 | `32dc0bec2160` |
| `frontend/src/components/experiments/RevisionPanel.tsx`（840 → 768，`+6/−78`） | 768 | `0cb19b4e580b` |

`frontend/tsconfig.json`、`frontend/package.json`、`frontend/package-lock.json`、`backend/requirements.txt` 的最終 diff 為空。需要交代的中途副作用：`npm run build` 曾自動在 `frontend/tsconfig.json` 的 `include` 追加 `".next-build-20261114/types/**/*.ts"`（那是本輪自己引入的 scratch dist 目錄），已用 `git restore --source=HEAD -- frontend/tsconfig.json` 還原，scratch 目錄已刪除；所有度量（typecheck／build／unit）都在該自動編輯之前取得，因此不受影響。

### 19.2 helper 搬移與兩項有意義行為變更

搬移（非行為變更）：`SPEC_DEFAULTS`、`IGNORED_FIELDS`、`canonicalSpec`、`flatten`、`diffSpecs`、`isUnsupportedSpec` 從 `RevisionPanel.tsx` 移到 `src/lib/revisionSpec.ts` 並 `export`。以 unified diff 逐行比對過：除 `export` 關鍵字與新增的 `timeframe` 條目（見 19.2b）外內容相同。這個「純函式放 `src/lib/`」的模式是倉庫既有的做法（`src/lib/strategyPreflight.ts`），不是為本輪新造的結構。元件在 `RevisionPanel.tsx:146` 呼叫搬移後的 `isUnsupportedSpec(base.strategy_spec)`。

有意義行為變更只有兩項：

- **(a) presence + truthiness → 值導向。** 舊 guard `if (spec.session || spec.timeframe)`（`RevisionPanel.tsx` 舊檔）只要 key 存在就把版本顯示為不支援。新實作（`revisionSpec.ts:39-63`）用 `REVISION_KEY_CAPABILITY`：`timeframe` 只有在 `typeof value === 'string' && value.trim().toLowerCase() === '1d'` 時被授權。因此合法明示 `"1d"`（含 `' 1d '`、`'1D'`）的來源版本現在按鈕可用、表單可開、不顯示 unsupported banner——這是本輪要修的東西。
- **(b) 非法值一律阻擋，且 `1d` 不豁免其他欄位。** `timeframe` 為 `null`/`""`/`"  "`/`0`/`1`/`true`/`'daily'`/`'5m'`/`'1h'`/`'4h'`/`'15m'`/`'30m'` 時仍阻擋，reason 點名 `timeframe` 並保留「backend 不會降為日線」的措辭；`frequency !== '1d'` 仍是第一個阻塞條件（`revisionSpec.ts:98-105`），`5m` + 明示 `1d` 的組合仍阻擋；`timeframe: '1d'` 與 `session` 同時存在時仍阻擋且 reason 點名 `session`。`"1d"` 沒有取得豁免其他不支援欄位的能力。

### 19.3 `session: null` 改為拒絕 = 與後端對齊，不是前端自創限制

舊前端以 truthiness 讀 spec，所以 `session: null` 被當成「未宣告」→ 允許修訂；§10.2 的後端 guard（`backend/app/api/experiments.py:714-724`）以值判斷，`session` 對**任何**值（含 `null`）回 400 並說明不會自動降級為日線。新前端把 `session: () => false`（`revisionSpec.ts:53`）寫成「沒有任何值可由這個表單授權」，於是 `session: null` 在 UI 顯示為不支援。

這是把**必定發生的後端拒絕**往前挪到 UI，而不是用前端驗證取代後端拒絕：後端那條 400 完全沒改、本輪也沒重跑 §18 那批後端測試（owner 明確要求不重跑）。代價要誠實寫出來：手上若已有 `session: null` 的來源版本，先前能開表單、填完後收到 400；現在直接不开表單。使用者看到的訊息從「錯誤」變成「這個版本不支援修訂，請建立受支援的日線實驗」。

### 19.4 80 pass 的組成與完整可重跑命令

`cd frontend && node22 --test 'tests/*.test.mjs'` → **80 tests / 80 pass / 0 fail / 0 skipped / exit 0**。

| 檔案 | pass |
| --- | --- |
| `tests/revisionSpec.test.mjs`（本輪新增） | 15 |
| `tests/userErrors.test.mjs` | 27 |
| `tests/displayHelpers.test.mjs` | 15 |
| `tests/benchmarkState.test.mjs` | 7 |
| `tests/apiRequestCorrelation.test.mjs` | 6 |
| `tests/strategyPreflight.test.mjs` | 6 |
| `tests/feedbackDraft.test.mjs` | 4 |
| 合計 | **80** |

15 條新增測試：11 條行為（直接 import `src/lib/revisionSpec.ts`，見 19.7）＋ 4 條接線斷言。加總核對：65（改動前基線，同一命令在同一機器取得）＋ 15 = 80。

命令必須用 glob 形式 `tests/*.test.mjs`。`node22 --test tests/` 會報 `Cannot find module '.../frontend/tests'`（這個 Node 版本把目錄參數當模組 import），所以别把它寫成資料夾形式。

### 19.5 typecheck / build / lint（皆在最終位元組狀態取得）

| 檢查 | 命令 | 結果 |
| --- | --- | --- |
| 型別 | `npm run typecheck`（`tsc --noEmit`） | **0 errors，exit 0** |
| 建置 | `NEXT_DEV_DIST_DIR=.next-build-20261114 npm run build` | `✓ Compiled successfully`，**exit 0** |
| Lint | `npx eslint src/lib/revisionSpec.ts src/components/experiments/RevisionPanel.tsx tests/revisionSpec.test.mjs` | **無輸出，exit 0** |

`NEXT_DEV_DIST_DIR` 是 `frontend/next.config.js` 既有的 per-runtime dist 隔离開關（`distDir: process.env.NEXT_DEV_DIST_DIR || '.next'`），用它只是為了不覆寫既有 `.next`，沒有引入新框架，也没有啟動 dev server。

### 19.6 撤回：「原本已有 11 個型別錯誤」是錯的

我在那一輪中曾回報「`tsc --noEmit` 原本就有 11 條 `TS18047`」以及「`next build` 在 `src/app/experiments/[id]/page.tsx:141:24` 中止」，並據此暗示前端倉庫原本是紅的。**這個判斷錯誤，在此正式撤回；那 11 條錯誤與 `page.tsx` 中止都不是倉庫既有狀態。**

原因就是我自己的臨時操作：為了讓 `npm ci` 可逆，我把舊依賴樹改名留在 repo 內變成 `frontend/node_modules.pre-ci-20261114/`。`frontend/tsconfig.json` 是 `include: ["**/*.ts", "**/*.tsx", ...]` 而 `exclude` 只有 `["node_modules"]`；點開頭目錄被 tsc 的 glob 隱式跳過，但 `node_modules.pre-ci-20261114` 不是點開頭，於是那棵樹裡的 `next`／`@types` 原始檔被納入編譯 program，11 條 `TS18047` 全部來自那裡。把該目錄移出 repo（現在在 `~/.local/share/alethra-frontend-node_modules.pre-ci-20261114`）之後，同一命令得到 0 errors、build exit 0。

教訓已反映在本節的 pre-commit 檢查：repo 內目前沒有任何 `node_modules*`／`.next*` 備份或 scratch 產物（`git status --porcelain --ignored=matching -unormal frontend` 只列出被 gitignore 的 `node_modules`、`.next`、`tsconfig.tsbuildinfo`）。

### 19.7 靜態接線不是 mount/render 證據

- 11 條行為測試是直接 `import` 產品模組 `src/lib/revisionSpec.ts` 的真實函式呼叫——測的就是元件實際 call 的那條 function，不是測試檔自己重寫一份邏輯。
- 4 條接線測試是**靜態**的：用 `readFileSync` 讀 `RevisionPanel.tsx` 原始檔字串，assert (i) import 自 `@/lib/revisionSpec` 的四個符號、(ii) 檔內已無這些 helper 的本地副本、(iii) `unsupported.blocked` 仍綁在 `openForm()` 的 early return（:174）、`disabled`（:347）與 banner（:358）、(iv) 舊的 `if (spec.session || spec.timeframe)` 已不存在。
- 這**不等於**任何執行期證據：沒有 React mount、沒有 props 實際傳遞、沒有點擊真的開出表單、也沒有驗證 submit payload 真的帶來源的 `timeframe: '1d'`。現有 `node:test` harness 無 DOM／JSX 能力，做不到這一層；`e2e/strategy-revision.spec.ts` 只點 `revision-open`（:174）與看 `revision-preview`（:178/:186），完全沒有 unsupported／timeframe／session 的斷言。要補這一層就是下一節的方案，尚未執行。

### 19.8 `diffSpecs` 認得新增的 `timeframe`，不等於表單有 timeframe 控件

`SPEC_FIELDS` 只多了 `'timeframe'` 一項，作用是讓 diff／change kind 能把「來源未宣告 → draft 明示 `1d`」正確標為 `changed_fields = ['timeframe']`、`strategy_only`，因此不會像 owner 要求的那樣顯示成「無變更」；同期對照：兩邊都同樣宣告 `1d` 時只剩 `commission_rate`，完全未改的 legacy spec 得到 `[]`。

表單 UI 沒有新增任何 timeframe 下拉或輸入。`draftSpec` 仍從 `{ ...spec }` 展開（`RevisionPanel.tsx`），所以來源既有的 `1d` 宣告被原樣帶進 submit payload；使用者無法在 UI 把日線版本改成 intraday，也沒有「加一個 timeframe 欄位」的新能力。

### 19.9 本機附加的 Node 版本與實際執行方式

這台機器預設 `node` 是 **v18.20.8**，不支援 TS type stripping，所以 `node --test tests/*.test.mjs` 在既有 6 個單元檔中的 5 個上直接失敗（它們 import `.ts` 產品模組）。本輪**附加**安裝官方 Node **v22.23.5**：下載後先比對官方 sha256（`d0c68c321161bb2c6483efea9a343010dea8e5e7f60d73c6a7eba140016d662f`）通過才解壓，只以 alias `node22`／`npm22`／`npx22` 暴露；預設 `node`／`npm` 未改、PATH 未改、無 brew／sudo／系統層安裝。

因此 19.4 的命令**只在「本機存在 `node22` 別名」這個前提下成立**。別把它當成跨機器通用工具寫進 CI 或其他文件；換機器時要改用絕對路徑或先在專案內固定工具鏈。

依賴恢復是依 lockfile 的 `npm ci`，未升級任何版本、未重寫 lockfile；執行前先審過攻擊面：`frontend/package.json` 沒有 root lifecycle script、`package-lock.json` 中 `hasInstallScript` 標記的 package 數量為 **0**、playwright 的 postinstall script 為空——所以不會有任何服務或瀏覽器被自動啟動／下載。

### 19.10 Node 20 CI 目前未執行這條單元 lane

`.github/workflows/frontend-build.yml` 固定 Node **20**，只跑 `npm ci` + `npm run build`；`browser-acceptance.yml` 亦為 Node 20，跑 Playwright e2e。兩者都**沒有** `node --test` 步驟，`frontend/package.json` 也沒有可被 CI 撿起的 unit-test script。

所以本節 80 pass 是**純本機**結果；不得宣稱 CI 已通過或已覆蓋新增測試——CI 對這 15 條新測試的真實狀態是 **NOT RUN**。讓 CI 涵蓋需要另批工作（workflow 引入 Node ≥ 22.6 與 `node --test` 步驟），本輪未批准、未做。

### 19.11 本節保存範圍與仍 NOT RUN 的事

本節的 commit 只含 19.1 的三個檔案加上本文件的 tail-only 追加。以下全部維持 **NOT RUN**，且本輪未聲稱完成：瀏覽器驗收、前端 dev server 或任何服務啟動、後端／PostgreSQL 連線或測試、`GRANT`／`CREATE ROLE`／`CREATE DATABASE`、Alembic migration、真實 import、真實帳號註冊／登入、任何行情 provider 外呼、部署、依賴版本升級。

下一步只交付「最小 UI 驗證方案」（能不能沿用既有 Playwright、以頁面級 API mock 驗 RevisionPanel 而毋須啟動後端或連 DB），方案本身不在本節執行，也尚未寫入本文件。

## 20. UI_BROWSER_BLOCKED：瀏覽器 lane 受阻於本機 OS，不是平台前置條件

### 20.1 首次錯誤（原文，不美化）

以本機釘住的 Playwright 1.64.0（`package-lock.json` 中 `playwright`/`playwright-core`/`@playwright/test` 皆 1.64.0）與實際解析出的 Node 20 執行檔 `/Users/tommylam0915/.local/share/node-v20.20.2-darwin-arm64/bin/node` 直接執行倉庫內 CLI（未用 `npx`，避免另抓一個 Playwright 版本）：

| 命令 | 實際 exit | 輸出原文 |
| --- | --- | --- |
| `node …/node_modules/@playwright/test/cli.js install chromium` | **1** | `Failed to install browsers` / `Error: ERROR: Playwright does not support chromium on mac13-arm64` |
| `… install chromium-headless-shell` | **1** | `Error: ERROR: Playwright does not support chromium-headless-shell on mac13-arm64` |
| `… install chromium --only-shell` | **1** | 同上（headless-shell 變體） |

本機 OS 為 macOS 13.3（`sw_vers -productVersion` = 13.3，Darwin 22.4.0，arm64）。`~/Library/Caches/ms-playwright/` 在安裝前後都只有 `.links`，**沒有任何 browser 二進位**。因此 Playwright 1.64.0 的 Chromium（revision 1248，`node_modules/playwright-core/browsers.json`）在此平台無可用建置。

自我更正：第一次我把命令接在 `| tail -25` 之後並讀 `$?`，那是 `tail` 的 exit code 0，掩蓋了真實的 1；上表是重跑後以 `> file 2>&1; echo $?` 取得的真實值。

### 20.2 本輪刻意未做的替代方案（超出已批准範圍）

本機確實裝有 `Google Chrome 157.0.8103.51`，Playwright 也能以 `channel: 'chrome'` 驅動它，但：

- 該測試 runner **沒有** `--channel` CLI 選項（`test --help` 只有 `--browser/--config/--project/--headed`），要改用就必須新增暫時 config；
- 批准的是「安裝本機已釘住 Playwright 版本所需的 Chromium」，換成使用者系統上的另一個瀏覽器等於改變證據的瀏覽器身份（Chrome 157 ≠ CI 的 ubuntu Chromium 1248），不是同一項工作；
- 升級 Playwright 才能取得 mac13 可用建置 → 明確禁止（不改依賴／lockfile）。

依「若必須超出上述範圍，停止並列出具體原因」，本輪**停止瀏覽器執行**，未啟動 dev server、未佔用任何埠、未修改 `playwright.config.ts`／`package.json`／lockfile，`frontend/e2e/revision-support-guard.spec.ts` **尚未建立**（所以倉庫裡沒有半成品可被誤當成已通過）。§10.3 的 UI mount/render 缺口維持 NOT RUN；離線證據仍以 §19 那 80 條單元測試為準。

### 20.3 已核實的規格（下一条代理可直接照寫 spec，毋須重新調查）

- 需要批准項：`playwright install` 在 mac13 不可能 → 需改在受支援主机／CI 執行，或由 owner 核准「以系統 Chrome + 暫時 config」這條不同的路。
- 前端啟動：`npm run dev` = `next dev -H 127.0.0.1`（僅 loopback）；本輪實測 port 3000/3001/8000/8099 **全部空閒**（`lsof -nP -iTCP:<port> -sTCP:LISTEN` 無列）→ 沒有覆蓋他人服務、也沒有讓 Next 自動改埠的風險。
- 環境對齊：`frontend/playwright.config.ts` 的 `use.baseURL` 與 `webServer.url` **都**讀 `E2E_FRONTEND_BASE`（default `http://localhost:3001`），`webServer.command` 只是 `echo` + `reuseExistingServer: true`（不會自己啟動服務）→ 設一次環境變數即可讓探測目標與實測目標同一，不需改 config。
- 隔離設計（比上一輪計畫更緊）：把 `NEXT_PUBLIC_API_URL` 指向前端自己的 loopback origin（`next.config.js` 的 `env` 會把它注入 client bundle），於是在同源的 API 呼叫在 route 層就能被攔截。同源非 `/api/` 請求 `route.continue()`；其他一律記錄最小診斷（method + pathname，不含 query／header）並 `route.abort('blockedbyclient')`，結尾 assert 未預期清單為空。先登 catch-all、再登各 mock（Playwright 後登記者優先），如此才不會被 catch-all 吞掉。
- 本頁面 mount 時會發出的端點（全部必須 mock，否則同源 `/api/` 請求會被 abort 而使案例可見地失敗）：`GET /api/v1/experiments/{id}`、`…/runs`、`…/versions`、`…/timeline`、`…/notes`、`…/method-review`、`…/saved-comparisons`、`…/robustness`、`…/record`；動作為 `POST /api/v1/experiments/{id}/revisions`（`RevisionPanel.tsx:269`，body 含 `base_version_id`/`strategy_spec`/`change_reason`/`expectation`/`request_id`）、`POST /api/v1/versions/{version_id}/runs`（:295）、`POST /api/v1/experiments/{id}/revisions/{version_id}/outcome`（:323）。
- revision 中繼資料的位置：`version.assumptions.revision`（`RevisionPanel.tsx:605`），不是獨立端點 → fixture 必須把它放在版本列上。
- 無旁路：本倉前端**沒有** Next route handler（`src/app/**/route.ts` 不存在）、沒有 `pages/`、沒有 `middleware.ts`、`next.config.js` 無 rewrites/redirects、`src/` 內無 `new WebSocket`、無 `serviceWorker.register`；實驗詳情頁為 `'use client'` 且 fetch 全在 `useEffect` → 無 server-side 直連後端的路徑。WebSocket 仍只有 dev server 自己的 HMR；用 `context.on('websocket')` 記錄並 assert 只出現 `/_next/`，加上 `test.use({ serviceWorkers: 'block' })`。
- 憑證：只用明顯假的字面值種 `localStorage.token`（401 會觸發 `authorizedFetch` 的 renew 重送；mock 用 200/403 就不會走那條）。
- 未覆蓋且不得宣稱：瀏覽器 lane 之外，本輪也沒有任何「真實 API／DB e2e」證據。

## 21. 平台主線：聚焦盤點、第一條垂直切片與 R1（研究流程的重複執行＋中斷恢復）

owner 指示把瀏覽器安裝從「整個平台的前置條件」降為「一條驗證的阻塞」，並回答：哪一條正式流程最接近「可重複執行且自動留存紀錄」，最小缺口與下一個工作包。本節是那份盤點＋第一個工作包的執行紀錄；不是新的設計文件。

### 21.1 盤點（流程環節 → 正式入口 → 實作位置 → 證據標記）

| 環節 | 正式入口 | 實作 | 證據標記 |
|---|---|---|---|
| 建立與版本化策略 | `POST /api/v1/experiments/`、`…/revisions` | `api/experiments.py:791-796`（未鎖 base ⇒ 修訂成為 current） | 已實作並有執行證據（HTTP＋SQLite，`test_experiment_revisions.py` 20 passed） |
| 比較結果、修訂、再執行 | `GET /runs/compare`、`POST …/runs` | `api/runs.py:109-129`（`COMPARE_FIELDS`／`FIELD_DEFAULTS`） | 已實作並有執行證據（`test_durable_run_customer_path.py`） |
| 選擇可追溯資料集 | `GET /api/v1/datasets`（僅登入，無 ownership 範圍）；建立 dataset／snapshot 需 admin（`api/datasets.py:111-116`、`:209-215`）；run 端點只有 `data_source`／`provider_id` | `models/research_run.py:22` `dataset_snapshot_id` **從不指派** | 缺少或有缺陷（backlog B1） |
| 建立研究任務 | `POST /api/v1/versions/{id}/runs` | `api/runs.py:82-93` → `services/research_runs.py:82`（request 內同步執行） | 已實作並有執行證據 |
| 執行並查看進度 | `GET /api/v1/runs/{id}` | 有 `status`／`started_at`，無 heartbeat、無 progress 欄位 | 已實作但未驗證（長任務進度不可觀察，backlog B3） |
| 自動保存決策／成交／風控 | `GET /api/v1/journal/…`、run 的 `result_json` | 研究：`transactions`／`round_trips`＋`emit_event`；paper／simulation 有真實列；`live_trading_daemon.py:96-188` 有缺陷 | 部分有證據（研究層）；實盤層不在本輪授權內 |
| 中斷後恢復 | 同一個 POST（延遲修復）＋啟動掃描 | 本輪 R1：`reclaim_abandoned_research_runs()` | 已實作，SQLite／HTTP 層有執行證據；PG、真實 process kill 未驗證 |
| 冪等／重複提交 | 同一個 POST | R1 的 409；training 有 `Idempotency-Key`（`job_control.py`）；robustness 有 `request_id`（indexed，非 unique） | 部分：串接重複已擋；真同時並發仍需 partial unique index（B2） |
| 匯出經授權資料 | `GET /api/v1/journal/trades.csv`（跨 run）、`GET /api/v1/strategy-lab/research/runs/{run_id}/trades.csv`（單 run） | `api/journal.py:111-143`，`:127` 以 `user_id=current_user.id` 限定本人；`api/strategy_lab.py:500-513` 先過 `_require_owned_study` | 已實作（export quota 的 PG 層未驗）。正式 backtest run 目前沒有「單 run CSV」端點，只有 journal 全域匯出 |
| UI 表單行為 | RevisionPanel | 接線與 80 條單元測試 | 受環境阻擋：`UI_BROWSER_BLOCKED`（§20） |

### 21.2 R1 改了什麼（不新增平行 demo 流程，全部落在正式路徑）

1. `backend/app/services/research_runs.py`
   - `INTERRUPTION_DETAIL`（:42，2026-11-15 收窄）：中斷終態給使用者的文字。舊版暗示「程序已停止、額度已歸還」，本輪測得兩件事都不一定成立，現文只宣稱「超過設定的 stale 視窗仍未終態 ⇒ 沒有結果記錄，可重跑」，並明寫「執行它的那條程序**未確認已停止**、它預留的研究額度**可能仍被持有**，要把這 run 当成未計費前必須先讀帳本」。「超過时限」與「確認程序死亡」是兩個陳述，目前只有前者有證據。
   - `_reservation_is_open()`（:303）：直接讀 `quota_reservations.status` 欄位，理由同 `job_control.cancel_requested_at`——identity map 會漏掉別個 process 已提交的狀態。配套的 `_status_in_db()` 已刪除：run 的終態不再靠「先讀再看」判斷，改由寫入本身的 WHERE 條件回答（見下）。
   - 完成寫入（:163-201）：終態是**條件式** `UPDATE … WHERE id = :id AND status IN ('pending','running')`，`published = rowcount`；`published != 1` 就丟棄結果、不覆寫終態、若 reservation 仍 open 則釋放後提交並回讀（:203-213）。結算前先看 reservation 是否仍 `reserved`（:215-226），已被釋放就記 log 並讓該 run **不計費**，不改寫帳本。異常路徑同樣是條件式翻轉，`flipped != 1` 表示別人已關結，直接採它的結果（:265-284）。
   - `find_live_research_run()`（:316）、`_release_orphaned_research_reservations()`（:328）、`reclaim_abandoned_research_runs()`（:407）。
   - 額度釋放判準（本輪修正，2026-11-15）：`quota_reservations` **沒有任何欄位連到 run**（`app/models/quota.py:38-44` 只有 id／user／quota_type／amount／status／created_at／settled_at），所以「這筆 open reservation 屬於哪個 run」在 schema 層是 **UNKNOWN**，不得宣稱時間排序能推定歸屬。現行規則因此只有一條：**該 owner 只要還有任何非終態 run，就一筆都不釋放**（:360-375）；等到沒有 live run 了，該使用者所有未被 robustness study 認領的 `research_run` open 列才全部可證明為孤兒（:377-404）。先前版本以「每個 live run 拥有其 `started_at` 之前最新那筆 open reservation」按 start 升序配對，並在文件裡寫「只会少放不會多放」——那是錯的：兩條 run 的 start 與 reserve 交錯（reserve 與蓋戳記之間没有任何時間界）時，配對會把孤兒列認給後跑的 run、再把後跑 run 真正的 reservation 當成剩餘列釋放，結果該 run 完成時 `settle_reservation()` raise（`entitlements.py:377-378`）、**執行成功卻不計費**，帳本與實際執行對不上。這條測式就在 `test_a_live_run_makes_no_reservation_provably_orphaned`。得不到的好處（少放）只是額度晚一點歸還；多放的代價是漏帳——所以選保守那一邊。真正的修法要 schema（§21.4 B2）。
   - 釋放範圍排除已被 `RobustnessStudy.authorization_reservation_id` 認領的額度：study 整份計畫只計一個授權單位，`_finalize_authorization` 對非 `reserved` 列會 raise，本輪不去碰它的帳。
   - 審計沿用既有 `research_run_failed` 事件（前端 `EVENT_LABELS` 已认识），不另創第二套日誌詞彙。
2. `backend/app/api/runs.py:88-99`：ownership 檢查之後（帳號 B 學不到 A 的 run id）若該 version 有**已提交**的非終態 run ⇒ 先對該 version 做一次延遲恢復；仍是 live ⇒ **409**＋run id，而不是新增第二列與第二筆額度預留。409 已寫進 OpenAPI（`:39`）。本輪修正的兩個過度宣稱：(a) 這是**可見性**檢查、不是鎖，也**不是跨交易互斥**——正常請求交易在運算期間沒有任何已提交的 run 列（見 §21.2a），所以「擋不住尚未提交的 in-flight 嘗試」不是併發窗口問題，而是那個狀態在 DB 裡根本不存在；(b) 真正的跨程序序列化點在 `reserve_quota` 的 `FOR UPDATE`（`auth/entitlements.py:287-326`，鎖 lifetime `QuotaCounter` 或 `User` 列，再鎖所有 `status='reserved'` 列，持有整個引擎期間）：第二條 POST 會先通過 409 檢查（看不到第一條的交易），然後在那幾把行鎖上等待，解鎖後**把同一 version 再跑一次並再計費一次**。PG 測試層 `lock_timeout='15s'`（`backend/tests/conftest.py:170`）會把這個等待轉成錯誤，不是轉成序列化保證。要讓 409 變成鎖需要 §21.4 B2 階段 3 的 partial unique index（未實作、未批准）。
3. `backend/app/config.py:124-125`：`research_run_recovery_enabled=False`、`research_run_stale_after_seconds=900`（沿用 training lane 的 900 慣例；名稱為 env 可覆寫之正式設定，非硬編碼）。
4. `backend/app/main.py:78-108`：`stop_orphaned_research_runs()` 啟動掃描，依 flag 才執行、只 UPDATE、失敗記 log 而不阻止 API 啟動（符合 `main.py:15-18`「啟動不得改結構」）。本輪撤回兩個宣稱：(a) 「多個 worker 同時掃描的安全性來自條件式 UPDATE＋rowcount」——rowcount 作為贏家判據這件事只在**同一條連線／同一 Session 的測試層**被驗證過（SQLite `StaticPool` 共用一條 DBAPI 連線，跨事務交錯在本層物理上不可能，`backend/tests/conftest.py:133-137`）；PG 跨程序是否如宣稱，**未測量、不宣稱**，也沒有 advisory lock 或共用鎖協定。(b) 「下一次啟動會自動把預留額度釋放乾淨」——不成立：`reclaim_abandoned_research_runs` 只對**本次真的翻成終態的那幾位 owner** 呼叫 `_release_orphaned_research_reservations`（`services/research_runs.py:407` 之後的路徑），所以「没有任何 run 列配對的 open reservation」（正常流程崩潰的實際結果，見 §21.2a）不在啟動掃描的掃描範圍，不會被自動歸還。
5. `backend/tests/test_research_run_recovery.py`（16 條）。**證據範圍（本輪新增，先講清楚再列條目）**：这一檔裡每一個 `running`+`reserved` 配對都是 seed 寫进去的（`_seed_attempt`），因為正常請求路徑不產生這個狀態（§21.2a）；這些測試綁的是「若該狀態存在，reclaim／release 依什麼規則下決定」，**不是**「正常崩潰會漏額度」的宣稱。條目：乾淨 run 不留 open reservation（settle 在已提交狀態是 reserved→used 的原子搬移）；預設視窗取自設定；恢復關結中斷 run 並歸還額度（回讀用獨立連線）；冪等＋不碰新近 run；**有 live run 時沒有任何 reservation 可被證明是孤兒**（含交錯配對反例、Study 認領列不動、其他 quota_type 不動）；ORM 直接指派會把終態列翻回非終態（characterization，解釋為何必須用陳述式 UPDATE）與條件式發布寫入無法翻回（`published == 0`、`result_json is None`）；reserve 後、首次 commit 前死掉 ⇒ 不留 run 列也不留 open reservation（這條是正常路徑的真實測量，也是 §21.2a 的證據之一）；POST 於 in-flight ⇒ 409 且只有一列／一笔額度（seed 狀態，非請求路徑）；中斷後 POST ⇒ 先恢復再跑新 run（只計費新的一次）；已完成不阻擋獨立重跑；帳號 B 拿到 403 且看不到 run id；晚到的寫入不得讓已恢復的 run 復活或結算；啟動掃描的 flag 兩態（off 時真的不寫）；study 認領的列不動。

### 21.2a 正常研究流程的交易邊界（2026-11-15 逐點測量，唯讀）

一筆 `POST /api/v1/versions/{id}/runs` 從头到尾只有**一條 Session、一個交易**：`app/database.py:24-29`（`get_db` 於請求結束才 `close()`，無 commit）→ `api/runs.py:101` 直call `execute_backtest_run`。

| 位置 | 動作 | 交易效果 |
|---|---|---|
| `services/research_runs.py:111-118` | `ResearchRun(status=pending)` add＋`flush()` | 只在**本交易內**可見 |
| `:129-139` `emit_event` | `services/__init__.py:37-63` 只有 add＋`flush()` | 不提交 |
| `:141-143` `reserve_quota` | `auth/entitlements.py:287-326` `FOR UPDATE`（lifetime `QuotaCounter`；不存在時改鎖 `User` 列 :302-312）＋鎖所有 `reserved` 列，INSERT＋`flush()`（:353）。**整個檔案零 commit／零 rollback** | 拿到行鎖、不提交 |
| `:146-148` | `status=running`＋`started_at`＋`flush()` | 全 repo **唯一**寫 `running` 的點，仍不提交 |
| `:151+` `run_backtest` | `engine/backtest.py:265` 起，整檔 `commit/rollback/flush` 計數 **0**；經同一 `db_session` 讀資料 | 運算期間無任何持久化 |
| `:196-203` → `:258` | 條件式終態 `UPDATE`＋settle＋audit，然後**第一次** `db.commit()` | run／event／額度／帳本**同時**可見 |
| `:262-300`（異常） | release／條件翻轉／audit，第一次 commit 在 `:284` 或 `:300` | 同上，終態與提交同批 |

四個回答：

1. **引擎開始運算前，run 與 reservation 皆未持久化。** `pending`→`running` 只存在於這條未提交交易裡；`SELECT` 別條連線看不到任何一列。
2. **正常流程不可達「已提交的 `running`＋`reserved`」。** 能持久化 `running` 的程式路徑不存在（唯一寫點 :146，其後第一個 commit 一定先寫終態）。可持久化的非終態狀態只有兩類：(a) 舊 build 留下或未來 durable admission 寫入的列；(b) robustness study lane——`api/robustness.py:361-383` 先 reserve、以條件式 `update(...).where(authorization_reservation_id.is_(None))` 的 rowcount 認領、**再 `db.commit()`** 才執行（cell 走 `charge_quota=False`，`services/robustness.py:728-730`）。這一類 reservation 是已提交的，且由 study 認領、不是 run。
3. **另一條獨立連線在運算期間看到的是：零。** 沒有 run 列、沒有 event、沒有 reservation 列；它**看得到**的只有那幾把行鎖——第二條 POST 因此先通過 409 檢查（無列可查），再阻塞在 `reserve_quota` 的 `FOR UPDATE` 上，解鎖後把同一 version 再跑一次、再計費一次。
4. **同 Session 的 reclaim 測試不能外推為跨程序行為。** SQLite 層 `StaticPool`（`backend/tests/conftest.py:133-137`）讓所有 Session 共用一條 DBAPI 連線，`FOR UPDATE` 在 SQLite 是 no-op，交錯物理上不可能；這些測試綁的是「给定已提交的 seed 狀態，規則怎麼下決定」，不是「崩潰會漏額度」。

**因此撤回**：「正常引擎崩潰必然留下永久 reservation」（崩潰實際留下的是**零列**——連「曾經嘗試過」都沒有記錄，故無從回收）；「409 已保護正常 in-flight 提交」（正常 in-flight 在 DB 裡不可見，409 無從拒絕）。保留 seeded-state 測試，範圍如上。

### 21.3 R1 的執行證據（layer 要寫明）

命令（SQLite layer，非 PG 結果；`<paths>` 以實際 13 檔展開，不使用 placeholder）：

```
cd /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra && \
DATA_PROVIDER=demo ALETHRA_PYTHON=$PWD/backend/.venv/bin/python \
./backend/scripts/run_tests.sh sqlite \
  tests/test_research_run_recovery.py \
  tests/test_robustness_studies.py \
  tests/test_durable_run_customer_path.py \
  tests/test_adhoc_backtest_contract.py \
  tests/test_backtest_and_replay_share_economics.py \
  tests/test_experiment_record.py \
  tests/test_experiment_revisions.py \
  tests/test_schema_incompatible_readiness.py \
  tests/test_two_phase_quota.py \
  tests/test_quota_concurrency.py \
  tests/test_entitlements.py \
  tests/test_api.py \
  tests/test_study_skill_labels.py \
  -p no:warnings -q -rf
```

- **修改後實際跑過的兩條（本輪最終程式，`research_runs.py` mtime 17:58:54／測試檔 18:07:25）**：
  - `tests/test_research_run_recovery.py`：**16 passed in 11.91s，EXIT=0**（`/tmp/rec5.log`，18:07:45）。
  - `tests/test_robustness_studies.py tests/test_two_phase_quota.py`：**52 passed in 21.69s，EXIT=0**（`/tmp/rec6.log`，18:10:21）——這兩檔是改動直接觸及的額度結算／study 認領路徑。
- 直接相關回歸集（上面 13 檔）：**219 passed, 1 skipped in 114.16s，EXIT=0**，但這筆測量的記錄時間早於本輪 17:58 的釋放判準修正（寫入 §21.3 時的檔案 mtime 為 13:40:55），因此**對現有程式是過期證據**，不得宣稱「目前程式 219 綠」。依指示本輪不重跑該集合；要把它閉環就是上面那條指令，一次執行、失敗停在第一條 trace。那 1 條 skip 已定位：`tests/test_quota_concurrency.py:38` 的 module-level `pytest.skip("PostgreSQL not available (set ALETHRA_TEST_POSTGRES_URL)")`——`run_tests.sh:139` 在 sqlite 層刻意 unset 該變數，所以這是**分層設計**，不是失敗。
- `git diff --check` 乾淨（EXIT=0，2026-11-15 18:1x 重測）。首次 12 條全紅的原因（以 str id 查 UUID 欄）與 study 案例的取值順序錯誤已修正，教訓寫在測試檔註解。
- SQLite 層的邊界（不宣稱）：`tests/conftest.py` 用 `StaticPool`，整個 process 共用同一條 DBAPI 連線，所以這層**沒有真正的跨交易並發**；它能證明的是陳述式形態與 rowcount 判定（誰取得終態）、不釋放無歸屬列的決策，以及「條件式 UPDATE 不會把終態翻回」。同一事務在 PostgreSQL 的鎖等待／EvalPlanQual 重求值行為屬 R2。
- 未驗證層（明確標記，不宣稱）：PostgreSQL（見下一條測量）、真實 process kill＋重啟的整合證據、瀏覽器 UI（`UI_BROWSER_BLOCKED`）。帳戶隔離的 PG 端：`tests/test_ownership_integration.py` 是 **paper-trading lane**（portfolio／daily-report／replay／stop，見 `:1-12`），不是研究 run lane；它 import 時就用 `ALETHRA_TEST_DB` 做安全檢查（`:34-70`：必須 PG、必須 localhost、DB 名須含 `test`、拒絕 production 名稱）。研究 run 的雙帳號隔離在 PG 層要由 `tests/test_research_run_recovery.py` 自己跑（`conftest.py:31`／`:158` 會依 `ALETHRA_TEST_DB` 换成真正的 PG engine），尚未執行。
- PG 層本輪的实际測量（2026-11-15，取代過去「pgvenv 已存在」的記憶）：`which postgres psql pg_ctl initdb` 全 not found、`backend/.venv` 內 `import pgserver` 為 `ModuleNotFoundError`、`pgrep -fl postgres` 無輸出。也就是這台筆電目前**沒有任何 PostgreSQL 二進位或執行中的 server**，`run_tests.sh pg`（`backend/scripts/run_tests.sh:80-129`）会在 `ALETHRA_PG_TEST_URL` 檢查就 exit 2，而不是測試失敗。PG／重啟／雙帳號三層要閉環，需要先取得 §21.5 的那一包批准（安裝＋啟動＋可丟棄 DB），不是再跑一次 SQLite 就能補上。

- R2 之前的最後一次 focused 測量（本輪文案修正後，SQLite layer）：`tests/test_research_run_recovery.py` **16 passed**，兩次執行分別為 `11.67s`（文案修正後）與 `11.70s`（最終 409 detail 改成「committed run… not proof it is computing」之後），EXIT=0，2026-11-15。本輪只改文案／docstring／註解，未改控制流，因此**未**重跑 13 檔 219 條集合（依指示）。全 repo 无任何地方斷言舊 409 字串（`grep -rn "still running as run" backend/ frontend/src` → 無輸出），只有 `test_research_run_recovery.py:568` 斷言狀態碼。
- 遠端狀態（2026-11-15 以**唯讀 fetch** 取得，非 `ls-remote` 推定）：本機 HEAD `6a220427f5cfefd29f1a6b80890d550416ac723a`，`github/live1-impl` = `b08418f2fc593791afb004134f46b3335b9ac8f4`；`git rev-list --left-right --count HEAD...FETCH_HEAD` = **`0 1`** ⇒ HEAD 是遠端的祖先，**0 ahead／1 behind，無分岔、可 ff**。那唯一一條遠端專有 commit 只動 `backend/tests/test_experiment_revisions.py` 與 `backend/tests/test_strategy_preflight.py`，與本輪 6 個未提交檔案**零重疊**。本輪不 merge／rebase／checkout／push（未批准），未提交工作原樣保留，因此**不再把遠端差異標 UNKNOWN**。

### 21.4 backlog（一處一價，不按文件數計完成度）

- **B1 資料集追溯**：`dataset_snapshot_id` 指派＋非 admin 的資料集選擇入口；否則「可追溯的資料集」仍需開發代理手動 seed。
- **B2 額度↔run 聯結（最小 migration 提案，三階段，先不實作）**：`quota_reservations` 沒有指向 run 的欄位（`app/models/quota.py:38-44`），所以歸屬只能推定，本輪因此改成「有 live run 就一筆都不放」。**階段 1（只有這個就夠回答 Q1/Q2/Q4）**：新增 `quota_reservations.research_run_id UUID NULL REFERENCES research_runs(id)`＋`(user_id, quota_type, status)` 已在的 index 夠用；寫入點只有兩處——`services/research_runs.py:141-143` reserve 後立即把 `run.id` 填進去。**本輪補充（依 §21.2a）**：光有 linkage 不夠——現行事務在引擎返回前不提交，所以「把 `run.id` 填進 reservation」若留在同一條未提交交易裡，崩潰後兩列一起消失，reclaim 依然無列可掃。durable admission 的定義因此是**「linkage ＋ 在引擎啟動前先 commit 一次」兩件事一起**，shape 直接沿用已被實測的 study lane（`api/robustness.py:361-383`：reserve → 條件式 claim 的 rowcount → `db.commit()` → 才執行；`services/robustness.py:728`：cell 在 run 前先提交）。`api/robustness.py:361-383` 已有反向連結（`authorization_reservation_id`）不動。判準變成讀欄位：`status='reserved' AND research_run_id IS NOT NULL AND 該 run 已終態` ⇒ 釋放；`research_run_id IS NULL` 的歷史列**永遠保持 UNKNOWN、不自動釋放**，只在 summary 裡報數量。migration 只有一支、additive（ADD COLUMN + 一個 index），不回填、不刪、不改既有值；`down_revision` 於撰寫時從 `backend/migrations/versions` 實際讀取（現況 52 支），不在文件裡硬編。**階段 2（回答 Q5）**：`research_runs.lease_expires_at`（或 `heartbeat_at`）讓「慢」與「死」可區分；代價是執行端必須定期續約，也就是 B3。**階段 3（回答 Q6/Q7 的併發缺口）**：`research_runs` 上的 partial unique index `(experiment_version_id) WHERE status IN ('pending','running')`，讓重複提交由 DB 拒絕而不是靠 check-then-act。**順序不可交換**：階段 3 會直接擋住 robustness study 的多 cell（同 version 的多列 run 是否允許同時存在未經測量），必須先量過 study lane 才提，否則會把 B4 那條 lane 打斷。三階段都需要 migration 批准（未批准）。
- **B3 進度可觀察**：研究 lane 同步執行，長任務只有 `running`；需要 heartbeat／progress（或改派到 worker）。
- **B4 robustness study 的同型洩漏**：study 卡 `running` 時其額度不釋放（本輪刻意排除，避免打斷 `_finalize_authorization`）。
- **B5 實盤 daemon 缺陷**：`live_trading_daemon.py:96-188` 的 `actor_type`、`risk_check` 與佣金問題仍在；屬實盤授權範圍，另行提案。
- **B6 UI lane**：`revision-support-guard.spec.ts` 規格在 §20.3，需跑在受支援主机。
- **B7 `backend/.env` 是啟動危害（2026-11-15 實測）**：該檔現含 `DATABASE_URL=postgresql://…@localhost:5432/ai_investment_lab`（`ai_investment_lab` 在 `test_safety_gate.py:33-38` 的黑名單內）、`DATA_PROVIDER=twelvedata`、`RESEARCH_AUTH_BYPASS_ENABLED=true`、`CANDIDATE_PASSWORDLESS_MODE=true`、`LIVE_TRADING_ENABLED=true`。`app/config.py:430` 用 `env_file=".env"`（相對 CWD），所以**任何在 `backend/` 底下起的程序都繼承它**；OS 層變數優先（實測 `DATABASE_URL` 覆寫成功，provider／bypass 仍取自 .env，除非逐項覆寫）。兩個需要澄清的事實：(a) `LIVE_TRADING_ENABLED` 全 repo 只有 `config.py:128` 定義、**沒有任何讀取點**，所以它不是實盤閘門，不能拿來當作「已隔絕實盤」的證據；(b) 真正會咬人的是 `DATABASE_URL` 指到黑名單 DB——目前只靠 target guard 擋下。修法：本機 `.env` 改指可丟棄 DB 或改用 `.env.example`＋啟動時斷言，另案批准。

### 21.5 下一個工作包：R2（PG／重啟／帳戶隔離閉環）——可精確批准的執行包

環境固定為本機，不使用任何生產或共享資料，`alethra` 倉庫不 commit／不 push（未批准）。**撰寫本包時一個階段都沒有執行**：未 pip install、未 initdb、未起 server、未建 role/DB、未 migration、未跑 PG 測試、未起 uvicorn、未發信號、未清理。下面每一格都是「先量過、再寫死」，數值取得時間 2026-11-15。

**(a) 環境事實（實測，非推定）**

- `uname -srm` = `Darwin 22.4.0 arm64`；`/opt/homebrew` 不存在；`docker` not found；`sudo` 在 `/usr/bin/sudo`（本包不使用）。
- `backend/.venv/bin/python` = **3.12.16**，`sys.executable` = `<ROOT>/alethra/backend/.venv/bin/python`。pgserver **尚未存在於任何本機解釋器**：`backend/.venv/bin/python -c "import pgserver"` → `ModuleNotFoundError`（實測）。
- **port 5432 目前無人監聽**（`lsof -nP -iTCP:5432 -sTCP:LISTEN` EXIT=1、0 列）、**無 postgres 程序**（`pgrep -fl postgres` EXIT=1、0 列）。所以 5432 可佔用而不碰任何既有集群；執行日若這兩條改變，`guard` 會先拒絕而不是搶佔。
- 記憶中的 `<ROOT>/.local/pgvenv` **不存在**（`ls -d` 實測 No such file）→ 沒有現成 PG 可複用，也沒有可挪用的憑證。
- 本包全部工作路徑**預先不存在**（實測：`ls -d <ROOT>/.r2{,/pgdata,/evidence,/venv}` 皆 No such file）。`$ROOT` = `/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592`，**在 `alethra/` 倉庫之外**；每個寫入階段前都呼叫 `require_absent`，已存在即 FAIL（不覆寫、不复用、不刪除）。

**(b) 目標由 guard 決定，先核對再安裝**

`backend/test_safety_gate.py` 白名單 :25-30（`ai_investment_lab_test`／`alethra_test`／`test_ai_investment_lab`／`alethra_owner_test`）、黑名單 :33-38（`ai_investment_lab`、`postgres`、`template0`、`template1`）、`ALLOWED_INSTANCES` :41-45 **只接受 port 5432** 於 `localhost`／`127.0.0.1`／`::1`；`scripts/run_tests.sh:89` 另要求 DB 名以 `alethra_` 起頭。兩者的交集只有 `alethra_test` 與 `alethra_owner_test`，本包固定 **`alethra_test` @ `127.0.0.1:5432`**。
`guard` 階段（唯讀、可重複跑）在任何寫入前做四件事：印出 port／程序／PGDATA／pin 解析事實；:5432 已被占用即 FAIL；把 `postgresql://alethra_r2@127.0.0.1:5432/alethra_test` 送進 `check_db_env.analyse()` 做**離線乾跑**並斷言 DB 名同時落在白名單、不在黑名單、port=5432；印出 `backend/.env` 實際生效的設定，讓「.env 會帶進什麼」在被看見之後才決定要不要往下。**不改 allowlist、不讀任何既有 DB。**

**(c) 三個身分，互不重疊**

- **provisioning**：`r2_bootstrap`＝`initdb` 的集群超級使用者（`-U r2_bootstrap --auth=trust --no-sync`）。只從 `psql postgresql://r2_bootstrap@127.0.0.1:5432/postgres` 執行建 role／建 DB 兩條語句；**應用層絕不以它連線**（`verify_test_database()` 拒絕特權角色，`test_safety_gate.py:125-145`）。
- **runtime**：`alethra_r2` = `CREATE ROLE … LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS`；`CREATE DATABASE alethra_test OWNER alethra_r2`；僅對**這個** DB 的 `GRANT ALL ON SCHEMA public TO alethra_r2`。GRANT 範圍＝本包自建集群內、本包自建 role，不是對既有集群的授權，也不動 role 層級。`trust` 只套用在 loopback，因此**沒有密碼存在**，無憑證可挪用；provision 結束會 `SHOW hba_file` 並把內容留在 evidence 裡佐證。
- **測試與 API 連線**：一律 `postgresql://alethra_r2@127.0.0.1:5432/alethra_test`（腳本內 `RUN_URL`，:41）。

**(d) 安裝：版本已解析到檔層級，不是猜**

- 位置：新建 `$ROOT/.r2/venv`（`$BACKEND_PY -m venv`）。**`backend/.venv` 不動、不往裡裝任何东西。**
- `R2_PGSERVER_PIN` 空值即 FAIL（:136-138）；不假設版本號。PyPI 實測（2026-11-15，HTTP 200）最新為 **pgserver 0.1.4**，與本機 3.12／arm64 匹配的檔是 `pgserver-0.1.4-cp312-cp312-macosx_11_0_arm64.whl`，**9,822,137 bytes、SHA256 `780fa89f26a960cca0215caf471e70848dd8597bd8ceaeba7faf42170278980c`**（上傳 2024-06-08）。批准時就是批准這個 pin＋這個 SHA256；`pip` 實際解析結果以 `pip freeze` 輸出為準。
- 直譯層相依（`requires_dist` 實測）只有 `fasteners>=0.19`、`platformdirs>=4.0.0`、`psutil>=5.9.0`，三者寫入範圍也只有 `.r2/venv`。
- **PG binary 來源＝wheel 內建**（無 brew、無系統 PG、無 Docker）。腳本不用記憶中的 API 名稱定位，而是 `find $VENV -type f -name initdb -path '*/bin/*'` 解析 bin 目錄、寫進 `$ROOT/.r2-package/pgbin`，解析不到就 FAIL（:150-154），並把 `initdb --version` 的**實測輸出**記進 `install.log`。README badge 宣稱捆包 PostgreSQL 16.2（本次實測取到該 badge 字串），**但版本判定一律以 `initdb --version` 為準**，宣稱與實測分開記錄。

**(e) migration／測試層**

- `gate`：`cd backend && TEST_MODE=true DATABASE_URL=$RUN_URL python -c "verify_test_database()"`；被拒就停在結構寫入之前（:192-199）。
- `migrate`：`cd backend && PYTHONDONTWRITEBYTECODE=1 DATABASE_URL=$RUN_URL python -m alembic upgrade head` 再加 `alembic current`。用既有 revisions（`backend/migrations/versions` 現況 52 支），**不新增 migration 檔**；失敗後不重試、不重建、不刪 PGDATA，首次 trace 留在 `$ROOT/.r2/evidence/migrate.log`。
- `tests`：`cd alethra && DATA_PROVIDER=demo ALETHRA_PYTHON=$BACKEND_PY ALETHRA_PG_TEST_URL=$RUN_URL ./backend/scripts/run_tests.sh pg tests/test_research_run_recovery.py tests/test_two_phase_quota.py tests/test_ownership_integration.py -p no:warnings -q -rf`。三檔契約不同、不可混讀：**recovery（16 條）＝研究 lane 判定**；**two_phase_quota（31 條）＝真 MVCC 下的兩階段額度記帳**（SQLite 那一層用 `StaticPool`，同一條 DBAPI 連線，跨事務併發本來就測不到，這一檔才是它的地盤）；**ownership_integration（10 條）＝paper-trading 隔離**，其模組層自我檢查（`ALETHRA_TEST_DB` 必須是 PG、host 限 loopback、DB 名含 `test`）在此目標通過，但它的結果**不是研究 lane 證據**。

**(f) API 啟動與 SIGKILL 的精確契約**

- 命令：`cd backend && env <下面 12 個變數> $BACKEND_PY -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --log-level info`，PID 記 `$ROOT/.r2-package/uvicorn.pid`。
- 生效環境（逐項覆寫，理由是 `backend/.env` 會把 `DATA_PROVIDER=twelvedata`、`RESEARCH_AUTH_BYPASS_ENABLED=true`、`CANDIDATE_PASSWORDLESS_MODE=true` 和黑名單 `DATABASE_URL` 帶進來，見 §21.4 B7）：`PYTHONDONTWRITEBYTECODE=1`、`DATABASE_URL=$RUN_URL`、`DATA_PROVIDER=demo`、`ENABLE_LEARNING_SCHEDULER=false`、`AUTO_COLLECTION_ENABLED=false`、`ENABLE_LIVE_PAYMENTS=false`、`ENABLE_EXTERNAL_NOTIFICATIONS=false`、`LIVE_TRADING_ENABLED=false`、`RESEARCH_AUTH_BYPASS_ENABLED=false`、`CANDIDATE_PASSWORDLESS_MODE=false`、`RESEARCH_RUN_RECOVERY_ENABLED=true`、`RESEARCH_RUN_STALE_AFTER_SECONDS=5`。最後一項是設定不是程式改動（`config.py:125` 預設 900），目的讓重啟後的掃描在幾秒內可觀察，並隨結果一起印出。**注意：`LIVE_TRADING_ENABLED` 全 repo 無讀取點（§21.4 B7a），寫成 false 不構成「已隔絕實盤」的證據；這裡的實盤隔絕來自「不起 daemon、不給任何券商憑證、provider 為 in-process demo」。**
- 關閉的無關面向：學習排程、自動收集、付款、外部通知皆 false；`DATA_PROVIDER=demo` 為純 in-process 資料（`app/data/demo_data.py`，`TRADE_DATES`、`DEMO-ALPHA` 已實測存在）→ 無外呼。兩個測試用免登變數显式關掉，A／B 各別走 `/api/auth/register`＋`/api/auth/login` 拿真 JWT。
- **SIGKILL 階段**：先跑 `timeprobe`（可行性閘門，不是裝飾）——同一 version 提交 3 次，每次輪詢 `research_runs.status`，記錄「是否有任一次看見 `running`」。**本輪修正對此閘門的解讀（§21.2a）**：即使換成很大的工作負載也**不該**看見 `running`——正常請求在運算期間沒有任何已提交的 run 列，獨立連線看到的數目恆為零，所以 `INSUFFICIENT_EVIDENCE` 是**結構性結果、不是毫秒級 demo 的副作用**；補 B1 資料集或 B3 worker 都不會改變这一点，能改變的是 B2 的 durable admission。demo 工作負載只有 3 個資產 × 30 根 K 棒；**若從未觀察到 in-flight 列，腳本以 exit 3 印 `INSUFFICIENT_EVIDENCE` 並且完全不发信号**（:382 附近；這是本包預留的誠實失敗出口）。
- 只有在 DB 出現 `status IN ('pending','running')` 的該列之後，才對**本腳本自己記錄的 uvicorn pid** 發 `SIGKILL`；`api_stop_recorded`（:266-277）在 TERM 之前先核對 `ps -o command=` 含 `uvicorn app.main:app`，不符就完全不碰該 pid。任何其他程序一律不發信號。
- 重啟後驗收（`restart-verify` 印出的欄位，全部以被殺列的 **owner** 為範圍）：`killed_run`、`status`、`started_at`、`completed_at`、`error_head`、`owner`、`open_reservations_same_owner`、`audit_events_for_this_run`、`owner_lifetime_used`、`research_run_failed_events_all_accounts`、`stale_after_seconds`。**依 §21.2a，本輪把預期改成實際可達的樣子**：正常請求被 SIGKILL 時整條未提交交易消失，因此預期是**查不到該 run 列**（腳本於列缺席時印出並 `SystemExit`，不假裝收斂）、該 owner 的 `open_reservations` 為 **0**（不是「已歸還」，是**從未存在**）、`quota_counters.used` 未增加（同樣是從未計費，不是回收成功）。只有在 DB 真的存在被殺的 `running` 列時（seed 或未來 durable admission），「超過 stale 視窗後再掃一次變成終態 `research_run_failed`＋payload `interrupted: true`，`services/research_runs.py:519-530`」這條收斂才是被测量的東西；屆時 `reserved` 是否歸零仍以**讀帳本**為準，不預設成功。對跨帳號的預期回應碼：**B `GET /api/v1/runs/{A 的 run}` = 404**（`api/runs.py:691-707` 的 ownership filter 查不到就 404，不是 403），**B `POST /api/v1/versions/{A 的 version}/runs` = 403**（:68-73）且回應內容不得含 A 的 run id。403 與 404 是兩個不同的答案，本包分別記錄、不混寫。
- 已知會在本層才浮出的風險（先講明，不事後找台階）：`audit_events.entity_id` 是 `String(36)`（`app/models/audit_event.py:14`）而 `research_runs.id` 是 UUID，任何 `entity_id = r.id` 的比較在 PG 會是型別錯誤——本包的查詢已改成 `r.id::text`；同型問題若出現在正式碼，會在 PG 層爆掉，屬本包要抓的東西之一。

**(g) 失敗即保存現場（不清理）**

- 任一步失敗：保留 `$ROOT/.r2/pgdata`、`$ROOT/.r2/evidence/*`、`$ROOT/.r2-package/*`，停止在**第一條** trace；不迴圈重跑、不重試、不重建 DB、不改測試把它變綠、不就地改程式。
- 正常結束：`pg_ctl stop -m fast`（**絕不使用 `-m immediate`**），只 TERM 本腳本記錄且核對過命令行的 uvicorn pid。
- **DB、PGDATA、evidence 全數留存；清理（含刪目錄、drop role/DB）是另一個批准包，本包不含任何刪除動作。**

**(h) 執行腳本身分（本次只撰寫、未執行）**

- 路徑：`/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/.r2-package/run_r2.sh`
- **685 行、29,985 bytes、SHA256 `00ccc1ebc8ee1c151d471674e8bf754b8417a6bb17a296b19a9e46517e917b26`**（2026-11-15 量得；任何字元改動都必須重新計算此值，不得沿用）
- 靜態檢查：`bash -n` EXIT=0；6 段 `R2PY` heredoc 全部 `ast.parse` 通過（8／14／7／101／153／79 行）。
- 階段（一次批准、依序執行）：`guard` → `install` → `provision` → `gate` → `migrate` → `tests` → `timeprobe` → `api-loop` → `restart-verify` → `stop`；`status` 為唯讀觀察。呼叫方式：`R2_PGSERVER_PIN=0.1.4 bash /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/.r2-package/run_r2.sh guard`（第一階段的完整命令，無佔位符）。

本包之後仍剩的真實缺口（另案，不混入）：B2 需要正式 migration 檔（`quota_reservations`↔run 聯結欄位或 partial unique index）；B1 資料集追溯；B3 進度可觀察——**B1／B3 未補之前，真實 SIGKILL 視窗可能不可觀察，`timeprobe` 會以 `INSUFFICIENT_EVIDENCE` 停下。**


---

## 22. Round-13（2026-11-15）：durable admission 最小 slice

本節取代 §21.2a／§21.5(f) 中「正常流程在運算期間沒有已提交 run 列」的描述；該兩節保留為**歷史測量**（它們記錄的是 2026-11-14 的程式狀態，且是本輪改動的依據），不再作為現行行為。

### 22.1 基準與保全（只讀＋ff，無 commit／push）

- checkpoint：`/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/.r1-checkpoint-20261115/`（repo 外）。`r1-uncommitted.diff` 67,653 B、SHA256 `0f821078ff97c4b5deb3603052eb0e41855772a443f1650c421e60dc132a16d9`；`test_research_run_recovery.py.untracked` 28,326 B、SHA256 `d450446e1be157581e0d444443e311621c9eed2dbf4aacdec6f1ad8bea6bbb87`；`MANIFEST.sha256`＋`RESTORE.md` 一併保存。未刪除、未 reset、未 stash drop。
- 本輪差異另存同目錄：`r3-durable-admission-v2.diff`（8 個檔：5 個已追蹤檔的 `git diff HEAD` ＋ 3 個未追蹤新檔的 `git diff --no-index /dev/null`，後者以 `git apply` 重現；完整大小與 SHA256 見交付訊息）。**可恢復性已實測**：`git archive HEAD` 匯出到 `/tmp/r3-restore-check` → `git apply --check` 通過 → 套用後 8 檔 SHA256 與工作區逐檔相同。先前那份 `r3-durable-admission.diff`（161,494 B、`830886d357bc9c7f255f6bf978db0ee654321c60c91a4327c10e26a5418d6765`）生於本節早期草稿瞬間，缺少後來追加的內容，現已 **過期**，保留為歷史證據、不再作為交付基準。
- 逐檔 SHA256 只在交付訊息中给出，本檔**不**記錄自己的 hash：追加這一行就會改變本檔，而本檔又是 patch 的一部分——一個檔無法容納包含它自己的 patch 的 fingerprint。7 個非本檔檔案的 SHA256 於交付訊息逐條列出，且都以「先固定 tree、後算 hash、再算 patch」的順序產生。
- 未追蹤新檔在 checkpoint 前後都未進 index（`git diff --cached --name-only` 兩端皆空）。
- 基準實測：本機 `6a22042`／遠端 `b08418f`／0 ahead 1 behind → 保存差異後 `git merge --ff-only` 到 `b08418f`（遠端那筆只動 `test_experiment_revisions.py`、`test_strategy_preflight.py`，patch 仍可套）。本輪**未 commit、未 push**。

### 22.2 三段交易（現行正式路徑）

| 段 | 內容 | 結束於 |
|---|---|---|
| admission | `SELECT … FOR UPDATE` version 列 → **在此鎖下重查** active run → run 列(`running`,`started_at`) → `reserve_quota` → linkage → `research_run_started` | `commit()`（引擎尚未讀一根 K 棒）|
| execution | `run_backtest` 讀資料、寫入為零 | 無交易 |
| terminal | 條件式 `WHERE status IN ('pending','running')` 終態 ＋ 結果 ＋ 已聯結單元的 settle／release ＋ `research_run_completed|failed` | 一個 `commit()` |

- 接納失敗（409／linkage 拒絕／audit 故障）走 `except BaseException: rollback(); raise` ⇒ 不留半套 run／reservation／audit。
- 結果側故障：先 `db.rollback()` 丟掉部分 completed 寫入，再於**另一個**交易寫 failed＋release＋audit；若連這件事也失敗 ⇒ rollback、log、**re-raise**（不回傳成功、不留假終態）。
- 條件式終態 rowcount 只回答「我有沒有發布這列」，不作為整套帳本交易安全的宣稱。

### 22.3 協定選擇：共同 version 列鎖（非 partial unique index）

- 選 `experiment_versions` 列鎖＋鎖下重查。**先更正本節原先的理由**（原寫「recovery 暫停期間唯一索引會讓該 version 永久硬擋」，暗示這是索引特有的缺點）：**「未終態的 active 列會挡住後續接納」是兩種排他設計共同的狀態政策**——列鎖＋鎖下重查與 partial unique index 都會在同一個中斷狀態上持續拒絕，差別只在於拒絕在哪裡決定（應用的重查可讀訊息與可測試；索引違例在插入時才抛出、錯誤訊息較難稳定），不在於會不會卡住。真正支援列鎖的理由只有兩個：(a) 拒絕條件與 409 訊息在同一處、可用離線測試逐一綁定；(b) study cell 的列形狀從未對著該索引量過，不未經測量就改寫接納語意。解除擋住的方式對兩者相同：一個終態寫入。
- 記錄於 `services/research_runs.py:LOCK_ORDER = "experiment_versions -> research_runs -> quota_counters/users -> quota_reservations"`。取得順序不反轉的依據：study  CLAIM 路徑（`api/robustness.py:361-383`）在 `reserve_quota` 之後、呼叫 cell 之前已 `commit()`，因此沒有任何交易同時持 quota 鎖再取 version 鎖。
- 拒絕範圍是 **version**（沿用 R1 語意，未擅自縮小成 config_hash）。已知後果：一個被中斷的 cell 會連同該 version 的其他 cell 一起擋住，直到有終態或被 lease／逾時政策處理。

### 22.4 額度聯結

- `quota_reservations.research_run_id`（additive、nullable、FK→`research_runs.id` `ondelete=RESTRICT`）＋ partial index（`IS NOT NULL`）＋ partial UNIQUE `uq_quota_reservation_run_unit`（`quota_type='research_run' AND research_run_id IS NOT NULL`）＝「一個 run 一個計費單位」由約束實屬。
- 結算／釋放一律经 `_linked_reservation()` 讀帳本；NULL＝**UNKNOWN**，不按時間推定、不回填、不由 run 結算。`charge_quota=False` 的 cell 沒有聯結 ⇒ 結構上碰不到 study 的 `authorization_reservation_id`。
- RESTRICT 的理由：刪 run 不得順手刪掉帳本證據。
- migration 檔僅**撰寫**：`migrations/versions/d0e1f2a3b4c5_add_quota_reservation_run_link.py`（`down_revision='c9d0e1f2a3b4'`，原頭）。**未對任何 PG 執行 upgrade／downgrade。**
- 原稿用的 id `e5f6a7b8c9d0` 與既有 `e5f6a7b8c9d0_add_intent_cancel_requested.py` **撞號**（Alembic 以 `DuplicateRevision` 拒絕載入，會讓之後每一次 `alembic upgrade` 直接掛掉，不是分支）。已改用驗證過未佔用的 `d0e1f2a3b4c5`，並加兩項離線 gate：`test_the_linkage_revision_is_unique_and_is_the_only_head`（掃全文找重複 id、斷言唯一 head 與 parent）與 `test_the_linkage_migration_and_the_model_declare_one_contract`（migration 常數對 `QuotaReservation.__table__` 的 index where clause、`unique`、FK target 與 `ondelete` 逐項相同）。本機直譯器**未安裝 alembic**，所以 gate 讀 migration 原始文字而非 import 它。
- 升段不再是靜默跳過：既有欄位缺 FK、索引缺 WHERE 條件、predicate 項缺字、該 UNIQUE 卻不 UNIQUE，都會 **raise**；`upgrade()` 內無 UPDATE／DELETE／回填（測試斷言這一點）。

### 22.5 recovery 改為 fail-closed（不是刪除）

- `POST /versions/{id}/runs` 的預檢＋reclaim 已移除；`main.py` 的 `stop_orphaned_research_runs` startup handler 刪除；`research_run_recovery_enabled` 設定欄位刪除（死 knob 會重演 `LIVE_TRADING_ENABLED` 的假可操作性，§21.4 B7a）。`research_run_stale_after_seconds=900` 保留，現由拒絕執行的 pass 讀取，用來決定**列出**哪些候選，不決定它對任何列做什麼。
- `reclaim_abandoned_research_runs` 現在是唯讀：回傳 `{refused: True, reason, candidate_run_ids, run_ids: [], released_reservation_ids: []}`，函式體內已無 `.update(`／`RunStatus.failed`／`emit_event`／`db.commit()`（由 `test_no_formal_path_calls_the_recovery_pass_and_it_cannot_write` 掃該函式原始碼綁定）。「已停用」不再被回報成「成功回收」。
- `_release_orphaned_research_reservations(db, *, user_id, closed_run_ids=())` 只認證明：釋放條件是 `research_run_id IN :closed_run_ids`（呼叫者自己剛寫掉的那些 run），無證明即回傳 `[]`；NULL 聯結＝UNKNOWN 永不釋放；study 的單元因 `research_run_id` 為 NULL 而**結構上**碰不到，原先維護特例清單的程式碼已刪除。
- **重啟前仍缺**：lease／heartbeat 或操作者明確決定（誰寫終態、以什麼證據）。本輪不实作。
- R2 包（`.r2-package/run_r2.sh`，SHA256 `00ccc1eb…`）的 `RESEARCH_RUN_RECOVERY_ENABLED=true` 現為孤立環境變數（pydantic 預設忽略，實測不影響載入），其 timeprobe／SIGKILL 預期已被 22.2 取代；依指示**不擴寫該腳本**。

### 22.6 直接驗證（sqlite 離線 lane）

命令：`TMPDIR=/tmp/alethra-sqlite-test-dir bash ./backend/scripts/run_tests.sh sqlite <files> --tb=no -q -p no:warnings`

- `tests/test_research_run_admission.py` **20 passed**（原 17 項＋兩項 migration gate＋`test_only_the_admission_constructs_a_run_row`：全 `app/` 掃描只有 `models/research_run.py` 的 class 語句與 `services/research_runs.py` 一處構造 run 列，該處在持 version 列鎖的 `_admit_research_run` 內，兩個正式入口（`api/runs.py:create_run`、`services/robustness.py:_run_cell`）都經 `execute_backtest_run` 抵達。引擎邊界量到的是 `in_transaction()` 進引擎 `False`、出引擎 `True` ⇒ execution **不寫入但不是沒有交易**，本節先前「無交易」的說法已更正）。
- `tests/test_research_run_recovery.py` **17 passed**（本輪把 11 項改寫為「斷言什麼都沒動」：refuse 而非 close、NULL 聯結永不釋放、釋放只認 `closed_run_ids`、舊測試中「掃掉 4 個未聯結單位」的危險行為改為正面控制組對比；新增 1 項把「有證明的釋放」拆成 linkage 正例與 UNKNOWN 反例）。
- 直接相關集合＝20 個碰 `QuotaReservation(`／`reserve_quota`／`POST .../runs` 的檔案，同一命令 `DATA_PROVIDER=demo`：**修改版 322 passed、12 skipped（PG-only）、0 failed；HEAD 乾淨匯出（`git archive` → `/tmp/alethra-head-r14`，同一 `.env`，SHA256 相同）285 passed、12 skipped**。差異恰好是新檔的 37 項（20＋17），無既有測試由通過轉失敗。
- `test_robustness_studies.py` 7 項＋`test_experiment_revisions.py` 1 項失敗**非本輪造成，也非缺資料造成**：`.env` 的 `DATA_PROVIDER=twelvedata` 讓這些用 `DEMO-ALPHA` 的測試走 provider 分支，`app/engine/backtest.py:375` 以 `data_provider.get_available_symbols()` 檢查符號、TwelveData 名單內沒有它，遂抛 `BacktestRefused: Unsupported asset: DEMO-ALPHA`。以明確 `DATA_PROVIDER=demo` 重跑（未 seed 本機資料、未改 `.env`、未動斷言）：基準與修改版皆 **41 passed／0 failed**；不加 override 則修改版仍是同樣 8 個 id、**33 passed／8 failed**。
- **NOT RUN（證據層級不同，未宣稱）**：PG 任何測試、migration 執行、跨交易／跨程序競態、真 SIGKILL、瀏覽器。SQLite `StaticPool` 共用一條連線，`FOR UPDATE` 在此為 no-op ⇒ 本輪證明的是提交邊界位置與決策規則，不是互斥實效；「另一個 Session」不是獨立 PG 連線。

## 23. Round-14／15（2026-11-15／16，本節為 2026-11-16 的測量）：同一取得權、release 入口移除、直譯器固定

§22 全部保留為**歷史測量**（其 20／17 passed 記錄的是當時程式狀態與當時那次執行的依據），但 §22.5 中「`_release_orphaned_research_reservations` 只認證明（`closed_run_ids`）」一條**自本節起過期**：該入口已不存在。

### 23.1 釋放入口是刪除，不是加狀態查詢

- `_release_orphaned_research_reservations` 從 `services/research_runs.py` 移除。理由不是「沒人用」而是它無法被允許：它能收到的唯一「證明」是一個 caller 傳進來的 run id 清單，那是**對另一段交易的聲稱**，不是那段交易发生了什麼。前一代的測試自己就示範了這件事——它釋放了一個自己從未關閉的 run 的單位。
- `reclaim_abandoned_research_runs` 仍是唯讀拒絕（`{refused: True, reason, candidate_run_ids, run_ids: [], released_reservation_ids: []}`），並由掃描 gate 綁定「`settle_reservation(`／`release_reservation(` 在整個模組中只有一個擁有者：`execute_backtest_run`」；同檔另綁定模組原始碼中不得再出現 `closed_run_ids`。
- NULL 聯結＝UNKNOWN：不按時間推定、不回填、不由任何晚到的 writer 結算或釋放。study 的 `authorization_reservation_id` 因無聯結而結構上不可達。
- 本輪未新增 lease／heartbeat／worker／request-id 系統。

### 23.2 終態與帳本遵守同一取得權

- 順序改掉：**條件式終態 UPDATE 先**，其 rowcount 才是動帳本的資格。`published != 1`／`flipped != 1` 兩者都 rollback、讀取既有已提交狀態（run 狀態＋ attributed 單位狀態）、把具體不一致寫入 log、回傳贏家的列；輸家不結算、不釋放、不新增自己的終態事件。已終態但帳本不一致者**回報**，不由晚到 writer 偷修。
- `charge_quota=True` 的結算契約：必須找到「同一 run＋同一 owner＋`research_run`」的聯結單位且其仍為 `reserved`。聯結缺失、或單位已是 released／settled ⇒ 抛 `RuntimeError`，該異常經 rollback 使 completed 寫入作廢，run 以該原因收為 `failed`；**不 commit completed**。
- `charge_quota=False`：保留 study cell 無聯結的合法行為；若外部硬把一個單位歸屬到這個未計費的 run，同樣拒絕（不結算它、也不釋放它）。
- 結算或 audit 中途抛例外：既有行為不變（部分 completed 作廢、單位由關閉它的那個 writer 釋放、原因寫入 `error_details`），本輪補的是「原因文字必須真的落到列上」的斷言。

### 23.3 migration：嚴格前置條件

- `migrations/versions/d0e1f2a3b4c5_add_quota_reservation_run_link.py`，`revision='d0e1f2a3b4c5'`、`down_revision='c9d0e1f2a3b4'`（原頭、唯一 head）。
- 政策：預期本 revision 的物件**全都不存在**。先檢查全部前置（`quota_reservations`／`research_runs` 兩表都在；欄位、FK、`ix_quota_reservation_research_run`、`uq_quota_reservation_run_unit` 皆未出現），任一已存在即 raise 並回報具體名稱與「Nothing was changed」——**不補修、不刪欄重跑**，因為它無法知道一個自己沒建立的狀態在強制什麼 predicate／RESTRICT 行為／歸屬。通過檢查後才依序建立：nullable UUID 欄 → `ondelete='RESTRICT'` FK → 兩個 partial index（ predicate 字串寫在常數裡，與 `app/models/quota.py` 的 `postgresql_where`／`sqlite_where` 由測試逐項比對）。
- 刪除的是「宣稱 exact、實際 substring matching」的 verifier（`_verify_index`／`_where_clause`／`_terms_of`／`_verify_foreign_key`）與 create-if-absent 行為。
- 仍只是**撰寫**：未對任何 PostgreSQL 執行 `upgrade`／`downgrade`。

### 23.4 實際直譯器與最終測試結果

- runner 的解釋器由 `scripts/run_tests.sh:30` 決定：`PYTHON=${ALETHRA_PYTHON:-python3}`。本機**未設定** `ALETHRA_PYTHON` 時實測選到 `/Library/Frameworks/Python.framework/Versions/3.11/bin/python3`：Python 3.11.3、SQLAlchemy **2.1.8**、pytest 9.2.0、無 alembic ⇒ **不符合** `backend/requirements.txt`（`sqlalchemy==2.0.36`、`pytest==8.3.3`、`fastapi==0.115.0`、`pydantic==2.9.2`、`alembic==1.14.0`）。判定依據是 runner 印出的 `interpreter:` 行與該直譯器自身的 `sys.executable`／metadata，不是 shell 提示或名稱。
- `backend/.venv/bin/python` 可執行且逐項符合 requirements：`sys.executable` = `…/alethra/backend/.venv/bin/python`、Python 3.12.16、SQLAlchemy 2.0.36、pytest 8.3.3、fastapi 0.115.0、pydantic 2.9.2、alembic 1.14.0。
- 顯式指定後的實測（未安裝／升級任何套件、未改 requirements）：

  ```
  TMPDIR=/tmp/alethra-sqlite-test-dir DATA_PROVIDER=demo \
    ALETHRA_PYTHON=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend/.venv/bin/python \
    bash ./backend/scripts/run_tests.sh sqlite tests/test_research_run_admission.py \
      tests/test_research_run_recovery.py --tb=short -q -p no:warnings
  interpreter: …/backend/.venv/bin/python
  40 passed in 28.15s
  exit code 0
  ```
- 同兩檔在系統 3.11／SQLAlchemy 2.1.8 那次的 `40 passed in 28.77s`（exit 0）**保留為該環境的結果**，不冒稱 .venv 結果，也不覆蓋 §22.6 的數字。

### 23.5 Row→UUID 修正及其證據範圍

- 實測：兩個直譯器（SQLAlchemy 2.0.36 與 2.1.8）對單欄 ORM query `session.query(Model.id).one_or_none()` 都回傳 **`Row`**（不是標量）；`session.query(Model.id).scalar()` 兩邊都回傳 `UUID`。
- 後果：把 `Row` 當成 uuid 綁進 `WHERE quota_reservations.id = ?` 得到 `sqlalchemy.exc.StatementError: (builtins.AttributeError) hex`，也就是 `_reservation_is_open()` 在两种環境都會炸。第一次實跑這批檔案時是 **21 failed／19 passed**。
- 修正：`_linked_reservation()` 刻意保留 `one_or_none()`（多於一個單位是約束被破壞，必須 raise）但回傳 `row[0]`；`_committed_state()` 兩處同樣拆值。`_reservation_is_open()` 的 `.scalar()` 未改語意。
- 證據範圍：這是**第一次真正執行**這些 helper——舊測試只在 `is None` 比較上使用它們，值從未進入 bind，所以 2.1.8 的差異一直不可見。本輪**沒有**在 .venv 重現「修正前」的失敗（那需要另一次 mutation 執行，本輪指示是停止新增 mutation）；可宣稱的是修正後兩條直譯器 lane 都 40 passed。

### 23.6 尚未驗證與已知後果

- NOT RUN：PG 任何執行、`alembic upgrade`／`downgrade`、FK `RESTRICT` 行為（SQLite lane `PRAGMA foreign_keys=OFF`，只在原始碼與模型層意圖核對）、行鎖／跨連線／跨程序互斥（`StaticPool` 共用一條連線，`FOR UPDATE` 為 no-op；測試中的「另一個 session」不是獨立 PG 連線）、真實 SIGKILL 中斷後的最終狀態。
- **被中斷的 active run 會持續擋住該 version**：列保持 `running`、單位保持 `reserved`、後續接納一律 409，直到有終態被寫入。解除它需要 lease／heartbeat 或操作者明確決定，本輪不实作、也不以逾時猜測。
- **回應遺失的 idempotency 尚未實作**：沒有任何持久 key（未儲存 request 形狀、無 Idempotency-Key lane），因此 client 重提交會被當成**新工作**接納並結算**新單位**。`test_a_resubmitted_request_is_a_new_run_not_a_replay` 把這個缺口 pin 住，而不是掩蓋它。
