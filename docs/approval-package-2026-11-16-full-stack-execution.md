# 全棧執行包 — 2026-11-16（§三：一份可批准的整体執行包）

本包只彙總**已量過**的事實與**已存在**的程式／腳本；不重新盤點 repo，不重寫既有計畫。
所有數值取得時間：2026-11-16（UTC 時間戳見各段引用）。凡未執行者一律標 `NOT RUN`，未能判定者標 `UNKNOWN`，
不以 revision 名稱代替內容審查，也不以本機路徑＋hash 代替腳本內容。

批准分兩項列在同一包：**執行批准**（§9.1）與 **Git 批准**（§9.2）。兩者互不含預設授權。

---

## 1. 版本

| 項目 | 實測值 |
|---|---|
| repo | `/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra`（`git rev-parse --show-toplevel`）|
| branch | `live1-impl` |
| 本地 HEAD | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` |
| 遠端 SHA | `git ls-remote origin live1-impl` → `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（**相同**，無分岔、無落後）|
| 遠端 | `origin` 與 `github` 皆 `https://github.com/TommyLam0915-LCW/alethra.git`（同一倉庫兩個名字）|
| 未提交 | `git diff --name-only` 39 檔、`git ls-files --others --exclude-standard` 22 檔（2026-11-16 量；本 checkout 由兩個平行 session 共用，此數會移動，因此本包列**路徑歸屬**而非總數）|

**本 session 改動／新增的路徑**（其餘未提交路徑屬平行 session 或先前輪次，本包不主張其內容）：

- 修改：`backend/tests/test_acquisition_hook.py`、`backend/tests/test_ownership_integration.py`、
  `backend/tests/test_env_hygiene_contract.py`、`EXECUTION_RECORD_20261109.md`、`.gitignore`、
  `frontend/playwright.config.ts`、`start_e2e_research_flow.sh`、
  `frontend/e2e/` 下 **11 個 spec**（§6 枚舉修復：`cross-experiment-comparison`、`crypto-research-markers`、
  `intraday-rule-flow`、`order-lifecycle-flow`、`research-record`、`robustness-study`、`rule-triggered-flow`、
  `strategy-authoring-and-lock`、`strategy-revision`、`unified-research-flow`、`workspace-replay-backend`）
- 新增（未追蹤）：`run_r2.sh`、`backend/.env.test.example`、`backend/scripts/check_env_hygiene.py`、
  `backend/tests/test_env_hygiene_contract.py`、`backend/migrations/versions/e1f2a3b4c5d6_*.py`、
  `backend/migrations/versions/f2a3b4c5d6e7_*.py`、`frontend/e2e/runtime.ts`

**執行時固定程式版本**（`run_tests.sh` 每次執行的 fingerprint 行 + 實測）：

- 後端：`backend/.venv/bin/python` = **3.12.16**，`pytest 8.3.3`，`sqlalchemy 2.0.36`
- Alembic 唯一 head：**`f2a3b4c5d6e7`**（`python -m alembic heads`，exit 0）
- 前端：`next 14.2.18`、`@playwright/test 1.64.0`；node 二進位有三個 —
  `~/.local/bin/node`＝**18.20.8**（Playwright 拒絕：requires Node ≥ 20，實測）、`node20`、`node22`
- Playwright CLI 一律以 `~/.local/bin/node22` 執行（本包所有前端指令皆如此寫）

---

## 2. 依賴（本節推翻了 handoff 的一條舊宣稱）

**(a) PostgreSQL：不需要安裝。** 本機已有 wheel 內建的 PG 16.2 二進位：

```
alethra/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin/{initdb,postgres,pg_ctl,psql}
$ initdb --version  ->  initdb (PostgreSQL) 16.2        (EXIT=0)
```

- **修正記錄**：`docs/handoffs/2026-11-14-desktop-checkpoint.md:772` 與 `:796` 宣稱「這台筆電目前沒有任何 PostgreSQL 二進位」、
  「記憶中的 `<ROOT>/.local/pgvenv` 不存在」。該測量看的是 `$ROOT/.local/pgvenv`，實際位置是
  **`$ROOT/alethra/.local/pgvenv`**，而且 `ENVIRONMENT_VERIFICATION_REPORT.md:16-17`（已追蹤文件）本來就寫了同一條二進位路徑。
  兩處陳述與 repo 內既存文件衝突，本包以實測（上面那行 `initdb --version`）解決，不採信任何一側的記憶。
- 驅動：**`psycopg2 2.9.11`** 存在 `backend/.venv`（Python 3.12）— 以 import 實測；`psycopg`／`asyncpg`／`pg8000` 皆 `ModuleNotFoundError`。
  應用在 `backend/app/database.py:19` 用 `create_engine(settings.database_url)`，`postgresql://` 走 psycopg2，因此 PG lane 的相依已齊全。
- **不改動任何既有環境**：不 `pip install`、不建新 venv、不碰 `backend/.venv`、不用 brew／Docker／sudo、不升級任何套件。
  （先前「離線無法取得 arm64 psycopg2 wheel」的建構障礙已不相關 — 它已經在 `backend/.venv` 裡了。）

**(b) 瀏覽器：不需要下載。** Playwright 內建瀏覽器**不存在**（`~/Library/Caches/ms-playwright` 為空目錄；
`node22 .../cli.js install --dry-run` 列出「將會安裝」chromium 156.0.8078.4 等到該目錄），但系統 Chrome 可直接驅動：

```
$ node22 -e "chromium.launch({channel:'chrome'})"  ->  CHROME_CHANNEL_OK "157.0.8103.51"  (page title round-trip OK)
```

- 因此本包把瀏覽器安裝批准**降為零**：改用 `/Applications/Google Chrome.app`（Chrome 157.0.8103.51）。
- 配套程式改動（已完成，內容見 §4c）：`frontend/playwright.config.ts` 新增 `E2E_BROWSER_CHANNEL`，
  未設定時 `channel: undefined` 保持原 bundled-chromium 行為，**不改變任何現況**。

**(c) Docker：不存在**（`which docker` → not found）。既有 tier-B e2e 棧 `start_e2e_research_flow.sh` 以
`docker compose -p $PROJECT -f docker-compose.e2e.yml`（:59）建棧，故**在本機無法執行**。
本包選的棧是 wheel 二進位路線（`run_r2.sh`），不是 compose 路線。

---

## 3. DB：固定目標與隔離

**固定目標**（`run_r2.sh` 常數，非推定）：

| 鍵 | 值 |
|---|---|
| host / port | `127.0.0.1` / `5432`（guard 實測：`lsof` listeners 0、`pgrep -f [p]ostgres` 0 筆）|
| database | `alethra_test` |
| runtime role | `alethra_r2` = `CREATE ROLE … LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS`（run_r2.sh:178）|
| provisioning role | `r2_bootstrap`（initdb 超級使用者，**只**從 `postgresql://r2_bootstrap@127.0.0.1:5432/postgres` 建 role／建 DB；應用層從不以它連線）|
| PGDATA | `$ROOT/.r2/pgdata`（實測不存在；`require_absent` 已存在即 FAIL，不覆寫、不復用、不刪除）|
| socket | `-c unix_socket_directories=/tmp` |
| auth | `--auth=trust` 只用於 loopback → **沒有密碼存在**，無憑證可洩漏或挪用 |
| RUN_URL | `postgresql://alethra_r2@127.0.0.1:5432/alethra_test` |

**target guard 契約 — 已用 `bash run_r2.sh guard` 實測通過**（2026-11-16，exit 0，兩次重跑輸出除時間戳外逐字相同）：

```
whitelist         ['ai_investment_lab_test', 'alethra_owner_test', 'alethra_test', 'test_ai_investment_lab']
blacklist         ['ai_investment_lab', 'postgres', 'template0', 'template1']
allowed instances [('127.0.0.1', 5432), ('::1', 5432), ('localhost', 5432)]
target            127.0.0.1:5432/alethra_test as alethra_r2
check_db_env.analyse -> postgresql | postgresql: 127.0.0.1:5432/alethra_test | errors: none
static gate: PASS
```

`guard` 是唯讀的：不安裝、不啟動、不連線。它同時印出 `backend/.env` 生效值，這條是本包的真實風險來源：

```
.effective settings with .env in force: database=localhost:5432/ai_investment_lab provider=twelvedata
live_trading=True … auth_bypass=True
```

也就是說 `.env` 預設會把**黑名單 production DB、twelvedata 外呼 provider、live_trading=true、auth bypass**帶進來。
本包所有寫入階段的環境都**逐項顯式覆寫**（§5），絕不依賴 `.env`。

**隔離（既有叢群保持原樣）**：`alethra/.local/pgdata-16` 是一個**完整且正常關閉**的 PG 16 叢群
（`pg16.log` 末行 `2026-11-08 19:06:58 LOG: database system is shut down`；無 `postmaster.pid` → 未在跑）。
依 `ENVIRONMENT_VERIFICATION_REPORT.md:21-30`，它內含 `ai_investment_lab`（黑名單；15 個真實使用者、3,660 筆
`strategy_study_trades`、2026-11-06 誤寫入的 44 筆 runs／58 筆 decisions）、`ai_investment_lab_test`、
`alethra_smoke_mb13`、`live2_tz_test`、`mb13_gate_selftest`、`postgres`、兩個 template。
本包**不啟動它、不連線、不改 role、不 initdb 在它的資料目錄**，也不動 `backend/.venv` 之外的任何 venv。
新增的防護（§4d）：`stage_provision` 對任何落在 `.local/pgdata` 下的 PGDATA 路徑直接 FAIL。

其他 DB 一律隔離：`alethra_owner_test`（第二帳號需要時才用）、`ai_investment_lab*`（黑名單／未批准）、
`runtime.db`／`research.db` 等 SQLite 檔不作為本包證據來源。

---

## 4. Migration：DAG、head 與三支 tail 的完整內容

> **本節是摘要；全文在附件 A。**
> `docs/approval-package-2026-11-16-attachment-A-script-content.md` 以快照方式嵌入
> 三支 tail migration（`d0e1f2a3b4c5`／`e1f2a3b4c5d6`／`f2a3b4c5d6e7`）**加** 六支執行腳本
> （`run_tests.sh`、`skip_budget.py`、`run_evidence.py`、`check_env_hygiene.py`、`check_migration_tail.py`、
> `run_r2.sh`）的**完整檔案內容**，每支附**行數、位元組數、完整 SHA256 與 git 追蹤狀態**，
> 並記錄產生時的 `HEAD` 與髒檔案摘要。附件由 `/tmp/render_attachment_A.py` 從工作樹**當場產生**，
> 不手抄；被嵌入的檔案一改，附件即為過期快照，重新產生而不是手改附件。
> 附件自己是**規則式遮罩後**的內容（本輪 7 處：`run_r2.sh` 的 4 個 `password` 字面值＋`check_env_hygiene.py`
> docstring 裡的 3 個 URL 內嵌口令舉例佔位；規則、逐檔位置與鍵名列在附件末「機密處理」，§15.9 有分項說明），
> SHA256 與行數仍量自未遮罩的原檔。
> 附件自己的摘要可用 `shasum -a 256 docs/approval-package-2026-11-16-attachment-A-script-content.md` 復測
> （本輪量得 156,654 bytes、`4047d87a79b990e439377b268143679d9d199aefcc57baadc13f8a16cbc21582`，
> 2026-11-17 12:4x 快照）。
> —— 這一節存在的原因是 owner 2026-11-17 五：「已有執行腳本就提供內容供審閱，不要只給本機路徑及 hash」；
> 「hash 只識別版本，不代替內容審閱」。**下列摘要不取代附件。**

**DAG（以工具為準，不以自寫 regex 為準）**：`alembic heads` → **單一 head `f2a3b4c5d6e7`**；
`alembic history` 共 **54** 條 revision，含 2 個 branchpoint 與 1 個 mergepoint（`add_state_version -> a1b2c3d4e5f7 (mergepoint)`），
無重複 revision id。先前一輪用自寫 regex 走 DAG 誤報「29 個 head」，該結論已作廢 — 教訓是工具才是權威。
**2026-11-17 重測同一組斷言**（`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m alembic heads|history`，不連 DB、不寫檔）：
heads 仍是單一一條 `f2a3b4c5d6e7 (head)`、exit 0；`history` 仍是 **54** 行；標記行仍是
`:38` mergepoint ＋ `:41`／`:49` 兩個 branchpoint；`migrations/versions/*.py` 檔數 **54**（與 history 一一對應）。

**(a) `d0e1f2a3b4c5_add_quota_reservation_run_link.py`（191 行，已追蹤）**

- 內容：`quota_reservations.research_run_id`（nullable UUID）＋ FK `fk_quota_reservation_run_unit`→`research_runs(id)` `ondelete='RESTRICT'`
  ＋ `ix_quota_reservation_run_id`（partial：`research_run_id IS NOT NULL`）
  ＋ `uq_quota_reservation_run_unit`（partial：`quota_type = 'research_run' AND research_run_id IS NOT NULL`）。
- 政策：嚴格 additive-only；`_found()` 先量 table／column／FK／index，**任何**自身物件已存在即 `RuntimeError`，且不變更任何東西；
  不修復、不 drop-recreate。`downgrade` 只丟自己加的，發現未知 index 或多於一條 FK 即拒絕。
- 不 backfill：既有列 `research_run_id` 保持 NULL＝誠實的 UNKNOWN，未歸因的額度留給人判斷，不自動釋放。
- 部署相容性：可跑在 live 表上（nullable column）； predicate 與 `app/models/quota.py` 以同一字串宣告，由
  `tests/test_research_run_admission.py` 釘住相等（text-level 保證，**不是**資料庫結果）。
- 狀態：**NOT RUN against PostgreSQL**。

**(b) `e1f2a3b4c5d6_add_runstatus_cancelled.py`（95 行，未追蹤）**

- 內容：PG-only `ALTER TYPE runstatus ADD VALUE IF NOT EXISTS 'cancelled'`；SQLite 下是 VARCHAR，故直接 return（no-op）。
- 政策：`runstatus` 型別不存在 → 拒絕；已含 `cancelled` → 拒絕（不假設別人放進去的是同一語意）。
- 影響：新值只增加、不重新解釋既有值；`status IN ('pending','running')` 兩處寫入謂詞不變 → 被取消的列對遲到的寫入者天然關閉。
- 部署相容性：`ADD VALUE` 在交易 commit 後才對其他 session 可見，正好是開始寫該值的程式上線之時。
- `downgrade`：PostgreSQL **無法**自 enum 移除值，故只印出保留標籤的說明；程式碼的讀取能力隨 code 退回，不隨 type。
- 狀態：**NOT RUN against PostgreSQL**。

**(c) `f2a3b4c5d6e7_add_research_run_idempotency.py`（158 行，未追蹤）**

- 內容：`research_runs.idempotency_key`（String(128), nullable）＋ `idempotency_request_hash`（String(64), nullable）
  ＋ `CREATE UNIQUE INDEX uq_research_run_version_idempotency ON research_runs (experiment_version_id, idempotency_key) WHERE idempotency_key IS NOT NULL`。
- scope 是安全性的全部：唯一索引以 `(version, key)` 為鍵而非 key  alone — version 透過 `experiment_versions` 唯一隸屬一個 owner
  （`research_runs` 本身沒有 `owner_id`），全表唯一會讓 A 帳號的 key 對上 B 帳號的 run；partial 讓無 key 的列（過去每一列、
  以及日後每次刻意重跑）保持原樣且可重複插入。
- 不 backfill、不對 hash 加唯一性（兩個 key 描述同一請求合法）、不加單 key index（每次查詢都是 `(version, key)`）。
- 同一 refuse-on-partial 政策；predicate 字串與 `app/models/research_run.py` 共享並由測試釘住。
- 狀態：**NOT RUN against PostgreSQL**；測試用的物件是 `Base.metadata.create_all` 建的，**不是**由這個檔案建的。

**(d) 本輪修掉的 migration 相依缺陷 — `start_e2e_research_flow.sh` 的兩處硬編 head**（修改，已驗證）

實測發現該腳本把 head 抄成常數、且**抄舊了 16 個 revision**：

```sh
EXPECTED_ALEMBIC_HEAD="z0a1b2c3d4e5"                                   # 實際 head 是 f2a3b4c5d6e7
grep -q "Running upgrade y9z0a1b2c3d4 -> z0a1b2c3d4e5" backend-upgrade.log   # FRESH 路徑
[ "$head_after" = "$EXPECTED_ALEMBIC_HEAD" ] || fail "..."                    # RESUME 路徑 :447
```

後果：RESUME 對一個**已被本工作所定義**的 schema 直接 abort；FRESH 要求一條現行 upgrade 不可能印出的 log 行。
修法是把值**派生自本 checkout 的 migration tree**，不再手抄：

```sh
derive_alembic_head() {          # 由 alembic 自己的 ScriptDirectory 讀，拒絕 branched tree
  … "$py" - "$ROOT/backend/alembic.ini" "$ROOT/backend/migrations" <<'PY'
heads = ScriptDirectory.from_config(cfg).get_heads()
if len(heads) != 1: sys.exit("this checkout has %d alembic heads …")
print(heads[0])
PY
}
EXPECTED_ALEMBIC_HEAD="$(derive_alembic_head)" || fail "no expected alembic head …"
```

FRESH 路徑的 log grep 改為**讀 DB 自己的蓋章**（比對字串比對結構更強）：

```sh
head_fresh="$(db_sql "$db_id" "select version_num from alembic_version")"
[ "$head_fresh" = "$EXPECTED_ALEMBIC_HEAD" ] || fail "fresh upgrade ended on '$head_fresh' …"
```

已驗證（本機，唯讀，不碰 Docker／不建 DB）：`bash -n` exit 0；
`bash start_e2e_research_flow.sh --show-head` → `EXPECTED_ALEMBIC_HEAD=f2a3b4c5d6e7`、exit 0、**不建立 `.local/e2e`**；
`E2E_PYTHON=/nonexistent/python bash … --show-head` → 印出兩條拒絕訊息、exit 1。
`len(heads)!=1` 那條分支在本機**未實際觸發**（要造出雙 head 樹才會走到）＝NOT RUN。

**失敗處理**（全部三支＋e2e pin）：任一步失敗即保留 `$ROOT/.r2/pgdata`、`$ROOT/.r2/evidence/*` 與 `$ROOT/.r2-package/*`，
停在**第一條** trace；不 DROP 重建、不補 grant、不刪 PGDATA、不重試、不就地改測試把它變綠。

---

## 5. 服務：精確命令、身分、外呼控制、上限、status 與停止

**只選驗收所需的一組棧**：wheel 版 PG（`run_r2.sh`）＋ uvicorn（loopback:8010）＋一個 `next dev`（loopback）。
既有 `start_candidate_stack.sh`（DB 54321／backend 8101／frontend 3101）與 `start_e2e_research_flow.sh`（compose）**都不納入本包**，
避免同時維護三套棧。

| 階段 | 命令（`run_r2.sh` 內）| 寫入？|
|---|---|---|
| guard | `bash run_r2.sh guard` | 唯讀，**已執行**（exit 0）|
| install | `bash run_r2.sh install` | 現況為「解析本機二進位＋寫 `.r2-package/pgbin`」；只有磁碟上找不到 PG 時才會建 venv／pip install |
| provision | `bash run_r2.sh provision` | 建 PGDATA、啟動 postmaster、建 role／建 DB（**需執行批准**）|
| gate | `bash run_r2.sh gate` | `TEST_MODE=true DATABASE_URL=$RUN_URL python -c "verify_test_database()"`，被拒即停在結構寫入之前 |
| migrate | `bash run_r2.sh migrate` | `cd backend && PYTHONDONTWRITEBYTECODE=1 DATABASE_URL=$RUN_URL python -m alembic upgrade head` ＋ `alembic current`（**需批准**）|
| tests | `bash run_r2.sh tests` | `./backend/scripts/run_tests.sh pg tests/test_research_run_recovery.py tests/test_two_phase_quota.py tests/test_ownership_integration.py -p no:warnings -q -rf`（**需批准**）|
| timeprobe | `bash run_r2.sh timeprobe` | 同 version 提交 3 次、輪詢 `research_runs.status`；從未見 in-flight 列則印 `INSUFFICIENT_EVIDENCE` 並 exit 3，**完全不發信號** |
| api-loop | `bash run_r2.sh api-loop` | `cd backend && env <12 個顯式變數> $BACKEND_PY -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --log-level info`，PID 記 `$ROOT/.r2-package/uvicorn.pid`（**需批准**）|
| restart-verify | `bash run_r2.sh restart-verify` | 只對本腳本記錄且核對過 command line 的 pid 發信號 |
| stop / status | `bash run_r2.sh stop` / `status` | `pg_ctl stop -m fast`（**絕不 `-m immediate`**）＋ TERM 自己記錄且核對過的 uvicorn pid |

**api-loop 生效環境（逐項覆寫，理由見 §3 的 `.env` 實測）**：
`PYTHONDONTWRITEBYTECODE=1`、`DATABASE_URL=$RUN_URL`、`DATA_PROVIDER=demo`、`ENABLE_LEARNING_SCHEDULER=false`、
`AUTO_COLLECTION_ENABLED=false`、`ENABLE_LIVE_PAYMENTS=false`、`ENABLE_EXTERNAL_NOTIFICATIONS=false`、
`LIVE_TRADING_ENABLED=false`、`RESEARCH_AUTH_BYPASS_ENABLED=false`、`CANDIDATE_PASSWORDLESS_MODE=false`、
`RESEARCH_RUN_RECOVERY_ENABLED=true`、`RESEARCH_RUN_STALE_AFTER_SECONDS=5`（設定，非程式改動；`config.py:125` 預設 900）。

**外呼控制**：`DATA_PROVIDER=demo`＝純 in-process 資料（`app/data/demo_data.py`），付款／通知／學習排程／自動收集全 false，
不給任何券商憑證，不啟 daemon。
**注意（不掩蓋）**：`LIVE_TRADING_ENABLED` 在 repo 內無讀取點（§21.4 B7a），寫成 false **不構成「已隔絕實盤」的證據**；
本包的實盤隔絕來自「不起 daemon、不給券商憑證、provider 為 in-process demo、目標 DB 為本包自建的可丟棄 DB」。

**時間／資源上限**：`urlopen` timeout 30／60、`thread.join(timeout=60)`、SIGKILL 階段 30 秒 deadline；無 CPU／memory cgroup（macOS 無此機制）→ 標 **NOT AVAILABLE**，
以「單工作進程＋loopback＋demo provider」代替資源隔離宣稱。

**前端 UI 棧**（`next dev`，需要執行批准；命令已依 §5a 修正）：

```
cd frontend && NEXT_PUBLIC_API_URL=http://127.0.0.1:8010 \
  ~/.local/bin/node22 ./node_modules/.bin/next dev -H 127.0.0.1 -p 3099
E2E_BROWSER_CHANNEL=chrome E2E_FRONTEND_BASE=http://127.0.0.1:3099 \
  E2E_BACKEND_BASE=http://127.0.0.1:8010 E2E_API_BASE=http://127.0.0.1:8010 \
  ~/.local/bin/node22 node_modules/@playwright/test/cli.js test --reporter=list
```

（命令形態沿用 repo 既存寫法 `start_candidate_stack.sh:446`；port 3099 取自 tier-B specs 的既有預設，避免與 acceptance env 的 3001 撞。
`E2E_EMAIL`／`E2E_PASSWORD` **故意不帶**：帶著它們就會一次啟動 69 個 tier-B 案例，見 §6a。）

### 5a. E5 前端的三個接線缺口（本輪實測發現；初稿的命令會失敗在第一個 API 呼叫）

發現方式是讀碼＋`grep`＋`lsof`，**沒有**啟動任何服務，因此三條的狀態都停在「靜態證據」，
不是「已修正並驗證」。（owner 2026-11-17 五 要求先列清連線對象，這三條就是那份清單的必要前提。）

1. **瀏覽器打的是 `NEXT_PUBLIC_API_URL`，不是測試程序的變數。**
   `frontend/src/lib/api.ts:11` = `process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'`
   （`frontend/next.config.js:9-11` 只是在 server 進程把它再包一層），而本包的 uvicorn 在 **8010**
   （`run_r2.sh:54` `API_PORT="8010"`）。初稿的 `next dev` 沒帶這個變數 → 瀏覽器會去打 **8000**。
   實測（2026-11-17 10:30±，`lsof -nP -iTCP:<port> -sTCP:LISTEN`）：8000／8010／3001／3099 **皆無 listener**，
   所以此刻的後果是「每個要後端的斷言 ECONNREFUSED」；但只要平行 session 在 8000 起了另一個後端，
   同一條命令就會讓瀏覽器去操作**別人啟動的棧**而且照樣綠 —— 共機環境下這是現實風險，不是假設。
   → 已在上方命令補上 `NEXT_PUBLIC_API_URL=http://127.0.0.1:8010`。
2. **後端 CORS 不放行 3099。** `app/config.py:52` `cors_origins: str = "http://localhost:3000"`；
   `run_r2.sh` 的 `API_ENV`（:294-309，12 個顯式變數）經 `grep -n CORS_ORIGINS run_r2.sh` **0 命中**。
   瀏覽器 origin 為 `http://127.0.0.1:3099`，而 `localhost` 與 `127.0.0.1` 在 CORS 是**不同** origin，
   所以修好第 1 條之後仍會被 preflight 擋下。待辦（改 `.sh`，不在本輪固定狀態執行中落地）：
   `API_ENV+=( "CORS_ORIGINS=http://127.0.0.1:3099" )` 並將該值一併印進 identity。
3. **spec 自己直連後端的預設埠是 8001／8099，也不是 8010。**
   `method-review.spec.ts:3`、`research-drafts.spec.ts:17`、`strategy-preflight-feedback.spec.ts:12` 皆
   `process.env.E2E_BACKEND_BASE ?? 'http://localhost:8001'`；`model-lifecycle.spec.ts` 與
   `paper-order-safety.spec.ts` 的字面預設是 `127.0.0.1:8099`／`localhost:8099`。
   → E5 必須**一次**把 `E2E_BACKEND_BASE`、`E2E_API_BASE`（`candidate-passwordless.spec.ts:47`，未帶則該檔 2 個案例自動 skip）、
   `E2E_PERSONAL_BACKEND` 全部帶上，否則「能被枚舉」會被誤讀成「能被執行」。

**不需要任何真實秘密**：`app/config.py:48` 的 `app_env` 預設 `development`，而加固區塊（`config.py:182`
「CORS_ORIGINS must not be '*'」等）只在 `is_production_like`（:153-155，`app_env in ("production","staging")`）才執行。
因此本包的棧用開發預設 JWT 金鑰即可啟動 —— 這正是它**必須**維持 loopback＋可丟棄 DB 的理由，
也是 §14 D4（`is_production_like` 只認兩個字串，其他 APP_ENV 值會靜默關掉整段加固）還掛著的原因。

---

## 6. 實際驗收：案例、預期結果與證據

每案列「由使用者完成的動作」而非直接 seed；seed 只用於**資料**（demo bars／manifest），不用於**產品能力**
（額度、終態、稽核、匯出、隔離一律走 API 與 DB 讀帳）。證據檔統一落在 `$ROOT/.r2/evidence/`。

| # | 案例 | 預期結果 | 證據 |
|---|---|---|---|
| V1 | 兩帳號各自 `POST /api/auth/register` → `login` 取真 JWT（不用 bypass） | 兩個 201／200 + 兩個不同 owner id | `evidence/accounts.json` |
| V2 | authoring → revision → lock：A 建立 experiment／version 並 lock | lock 後同一 version 的能力契約拒绝改寫（409／403，以實際碼記錄） | `evidence/authoring.log` |
| V3 | 明確資料來源：`GET` data-manifest → 以該 dataset 建立 version | 回應含 grain／provenance／checksum，且 `DATA_PROVIDER=demo` 無外呼 | `evidence/manifest.json` |
| V4 | durable admission：`POST /api/v1/versions/{id}/runs`（runs.py:46）→ 讀 `research_runs`＋`quota_reservations` | 非終態列**在請求運算期間已存在**（durable admission 的直接可觀察後果），且 `research_run_id` 歸因到該 reservation | `evidence/admission.log` |
| V5 | 併發接納：同 version 同時 2 請求 | 僅 1 個 unit 計費；另一個取得 409／既有列，`quota_counters.used` 只 +1 | `evidence/concurrency.log` |
| V6 | **idempotency**：帶 `Idempotency-Key` 提交 → 同 key 重發 → 換 key 提交 → 送空白 key | 同 key 重發回**同一 run id 且不再計費**；換 key＝新的刻意重跑；空白 key＝400（`runs.py:149-168`）。`uq_research_run_version_idempotency` 為背書 | `evidence/idempotency.log` |
| V7 | 取消：`POST /api/v1/runs/{id}/cancel`（runs.py:861）| 終態 `cancelled`；`status IN ('pending','running')` 謂詞使後到寫入者無法覆寫 | `evidence/cancel.log` |
| V8 | 中斷／恢復：timeprobe 若看見 `pending|running` 列，才對**自己記錄的 pid** 發 SIGKILL；重啟後掃 stale | 超過 5 秒視窗收斂為 `research_run_failed` 且 payload `interrupted: true`（`services/research_runs.py:519-530`）；`reserved` 歸零以**讀帳本**為準 | `evidence/restart-verify.log` |
| V8b | 若從未看見 in-flight 列 | **`INSUFFICIENT_EVIDENCE` ＋ exit 3，且完全不發信號**（結構性結果，非毫秒 demo 的副作用；補 B1 資料或 B3 worker 都不會改變，能改變的只有 B2 durable admission） | `evidence/timeprobe.log` |
| V9 | 帳戶隔離：B 讀 A 的 run → **404**（`api/runs.py:691-707` ownership filter）；B 對 A 的 version 發 run → **403**（:68-73）且回應不得含 A 的 run id | 兩個不同的碼分別記錄，不混寫 | `evidence/isolation.log` |
| V10 | 結果與匯出：`GET /api/v1/runs/{id}`（:765）與 `GET /api/v1/runs/{id}/trades.csv`（:838）| CSV 逐筆成交列與 DB reads 逐欄一致；跨帳號取別人 CSV＝404 | `evidence/export.csv` |
| V11 | audit 一致性：`audit_events` 對本 run 的事件計數，以及**全帳號** `research_run_failed` 事件數 | 事件存在且 `entity_id` 比對用 `r.id::text`（`audit_events.entity_id` 是 `String(36)`，`entity_id = r.id` 在 PG 是型別錯誤） | `evidence/audit.log` |
| V12 | 正式 UI 流程（瀏覽器）：真實 Chrome 驅動 tier-A specs（21 檔，走 `page.route` 攔截，不需要後端）＋本輪 4 檔 node 契約 | 正常流程可走通、錯誤狀態有畫面；**先決條件已滿足**：原本讓整組無法枚舉的 load-time throw 已修，`--list` 實測 312 tests／32 files、exit 0，見下方「本層才浮出的缺陷」 | `frontend/playwright-report/` |
| V13 | env 入口四件套：`check`／`start`／`status`／`verify`／`stop`（`start_acceptance_env.sh`、`candidate_status.sh`、`stop_acceptance_env.sh`）| 每條以精確命令＋實測退出碼記錄；`--purge` **不在**日常停止內 | `evidence/env-entries.log` |

**本層浮出的缺陷 — e2e 整組無法枚舉（本輪已修，實測在下方）**

發現時的實測（2026-11-16）：

```
$ node22 node_modules/@playwright/test/cli.js test --list
Error: Missing E2E_EMAIL / E2E_PASSWORD. …   at cross-experiment-comparison.spec.ts:42
…（另外 10 個檔案同樣的 load-time throw）
Total: 0 tests in 0 files
```

32 個 spec 檔有 **11 個在 module load 就 throw**（憑證不足即中止），而 Playwright 只要有任何一個 spec 在載入時拋錯就
**放棄整組列舉**，於是連 21 個不需要憑證的 tier-A spec 也一枚都列不出來。這與本輪剛修好的後端是同一形態
（一個 fail-closed guard 的 raise 點把其他人全部擋掉），差別只在 raise 點落在 import 時期。

根因不是「檢查太嚴」而是**同一個 22 行 loader 被複製了 11 份，且把「跑需要的東西」放在「載入需要的東西」時期**。
修法（已執行，非批准項目）：抽出單一共用模組 `frontend/e2e/runtime.ts`（69 行），把問題拆成兩個時點 —

* `runtimeAvailable()`：collection 時期呼叫，**永不 throw**；
* `loadRuntime()`：run 時期在 `test.beforeAll` 內呼叫，**仍然 throw**（沒有憑證就執行 tier-B，
  等同對一首沒人啟動的棧做斷言）。

每個 spec 的改動只有四行，11 份 loader 複本全數刪除：

```ts
let runtime: Runtime;
test.skip(!runtimeAvailable(), TIER_B_SKIP);
test.beforeAll(() => { runtime = loadRuntime(); });
```

修後實測：

| 測量 | 結果 |
|---|---|
| `node22 node_modules/@playwright/test/cli.js test --list` | **Total: 312 tests in 32 files**，exit 0（原本 0 tests in 0 files）|
| `node22 ./node_modules/typescript/bin/tsc --noEmit` | **exit 0，0 行輸出**（含 11 個改寫檔＋新模組＋config 改動）|
| `git diff --stat`（13 檔：11 spec＋`playwright.config.ts`＋`start_e2e_research_flow.sh`）| **118 insertions(+), 347 deletions(-)**；另新增 `frontend/e2e/runtime.ts` 69 行（未追蹤）|
| loader 變體核對 | 11 份只有兩種，唯一差異是 3 份不含 `backendBase` → 共用版保留該欄，那 3 份讀不到它亦無影響 |

**計數出處（自我修正）**：本文件初稿寫「326 個 `test()`」，那是我用
`grep -oE "test(\.skip|\.only|\.fixme)?\("` 自行加的，把 `test.skip(` 的 14 個呼叫點當成了測試。
以工具為準：Playwright 自己報 **312 tests in 32 files**；原始檔內 `^\s*test(` 呼叫點亦為 **312**，
另有 35 個 `test.skip(`（多為 test 內部的條件跳過，不增加測試數）。兩者相符，326 作廢。

**仍未驗證（NOT RUN）**：`test.skip(...)` 與 `beforeAll` 的實際執行行為**尚未被觀察過** —
要看到它需要一個可達的 frontend URL（config 的 `webServer.url` 在無棧時會讓執行直接失敗，走不到 skip 訊息）。
E5 的 `next dev` 會是第一個真實觀察點；本節能宣稱的只有「能被列舉」與「能通過型別檢查」。


### 6a. 312 個瀏覽器案例到底會連到什麼（owner 2026-11-17 五 要求：啟動前先列清）

**量測方式**：全部唯讀，未啟動任何服務、未開瀏覽器、未外呼。
案例數以工具為準 —— `node22 node_modules/@playwright/test/cli.js test --list` →
**Total: 312 tests in 32 files**、exit 0（2026-11-17 10:25Z 前後重測，與 §6 前表同數）。
連線對象由逐檔靜態掃描得出（`https?://` 字面值、`.goto(`、`.route(`、`waitForResponse(`、
`fetch(`、`spawn(Sync)(`、`*DB_URL`、`process.env.*`），掃描器放在 `/tmp/e2e_section6a.py`
而**不進 repo**：本輪固定狀態全量正在執行，`tree_digest` 只看 `*.py`（`run_tests.sh:328-334`），
放進去就會讓那一次量測不再是固定樹。

**四條彙總結論（先給答案，再給逐檔表）**

1. **零外呼。** 32 個 spec 加 `e2e/runtime.ts` 裡每一個絕對 URL 都是 loopback，非 loopback 的 URL
   字面值 **0 個**。實測出現過的 host 只有 `localhost:{3001,8001,8099}` 與
   `127.0.0.1:{3002,8098,8099,3100}` 再加 `localhost:3099` —— 全部是本機 port 的字面預設，
   其含義與必須一次性覆寫的理由見 §5a。
2. **零券商連線。** `broker` 一詞在 e2e 內只出現在 spec 自己偽造、再由 `page.route` 回給前端的
   fixture 欄位（例 `execution-fill-source.spec.ts:45-46`、`:80-82`）。沒有任何 spec 引用券商 SDK、
   主機名稱或券商憑證；`ibkr`／`alpaca`／`coinbase`／`binance` 在整個 e2e 目錄 **0 命中**。
3. **零付費 provider 出口。** spec 內不出現 provider API host 或 provider key 環境變數；
   市場資料來自被攔截的 `/api/v1/...` 回應，或來自後端自己的 `DATA_PROVIDER=demo`（in-process）。
   `kraken`／`twelvedata` 字樣只是 instrumentation 與 UI 文案（`crypto-live-research.spec.ts`、
   `intraday-incremental.spec.ts`），不是連線目標。
4. **需要的「秘密」只有測試帳號密碼，且可全部自造。** 需要提供的只有
   `E2E_EMAIL`／`E2E_PASSWORD`（11 檔 tier-B，經 `E2E_CRED_FILE` 讀取，`runtime.ts:31-42`）、
   `ACCEPTANCE_PASSWORD_A`／`ACCEPTANCE_PASSWORD_B`、`E2E_MARKER_EMAIL`／`_PASSWORD`、
   `E2E_FIRST_USE_EMPTY/THIN_EMAIL`／`_PASSWORD`。這些都是接受度帳號，不是任何真實使用者或機構的憑證，
   且一律可由本包自己用 `POST /api/auth/register` 建立。**另有 4 處寫死的退回密碼**
   （`method-review.spec.ts:8`、`research-drafts.spec.ts:22`、`:27`、`strategy-preflight-feedback.spec.ts:17`）
   —— 這是既有事實，本檔不重複其值（見下方「掃描器範圍缺口」）。

**逐檔表**（層代號：L0 不需服務／L1 部分攔截／L1g 有自衛 skip／L2 必須有後端且無守衛／L3 tier-B 憑證）


| spec 檔 | 案例 | 層 | `.goto` | `.route` | `waitForResponse` | `test.skip` | spawn | 直連 DB | 必須可達 / 沒有時的結果 |
|---|---|---|---|---|---|---|---|---|---|
| `crypto-live-research.spec.ts` | 26 | L0 | 0 | 0 | 0 | 0 | — | — | 不起瀏覽器、不連服務（`global.fetch` stub 或純匯入函式） |
| `intraday-incremental.spec.ts` | 16 | L0 | 0 | 0 | 0 | 0 | — | — | 不起瀏覽器、不連服務（`global.fetch` stub 或純匯入函式） |
| `api-fetch.spec.ts` | 14 | L0 | 0 | 0 | 0 | 0 | — | — | 不起瀏覽器、不連服務（`global.fetch` stub 或純匯入函式） |
| `manual-decision-retry.spec.ts` | 8 | L0 | 0 | 0 | 0 | 0 | — | — | 不起瀏覽器、不連服務（`global.fetch` stub 或純匯入函式） |
| `workspace.spec.ts` | 51 | L1 | 53 | 47 | 1 | 0 | — | — | 部分攔截：未覆蓋的 `/api/...` 會真打後端 → 逐案 **UNRESOLVED** |
| `paper-order-safety.spec.ts` | 10 | L1 | 3 | 1 | 8 | 0 | — | — | 部分攔截：未覆蓋的 `/api/...` 會真打後端 → 逐案 **UNRESOLVED** |
| `subscription-states.spec.ts` | 10 | L1 | 13 | 41 | 0 | 0 | — | — | 部分攔截：未覆蓋的 `/api/...` 會真打後端 → 逐案 **UNRESOLVED** |
| `method-review.spec.ts` | 9 | L1 | 10 | 3 | 0 | 0 | — | — | 部分攔截：未覆蓋的 `/api/...` 會真打後端 → 逐案 **UNRESOLVED** |
| `execution-fill-source.spec.ts` | 4 | L1 | 2 | 1 | 0 | 0 | — | — | 部分攔截：未覆蓋的 `/api/...` 會真打後端 → 逐案 **UNRESOLVED** |
| `candidate-passwordless.spec.ts` | 13 | L1g | 9 | 2 | 2 | 15 | — | — | 有自衛 `test.skip` 守衛（15 處） |
| `panel-load-recovery.spec.ts` | 8 | L1g | 1 | 13 | 0 | 1 | — | — | 有自衛 `test.skip` 守衛（1 處） |
| `inference-and-admin-entry.spec.ts` | 7 | L1g | 2 | 7 | 0 | 1 | — | — | 有自衛 `test.skip` 守衛（1 處） |
| `silent-failure-surfaces.spec.ts` | 5 | L1g | 1 | 9 | 0 | 1 | — | — | 有自衛 `test.skip` 守衛（1 處） |
| `first-use-journey.spec.ts` | 4 | L1g | 4 | 4 | 0 | 4 | Y | Y | 有自衛 `test.skip` 守衛（4 處） |
| `backbone-workflows.spec.ts` | 23 | L2 | 24 | 0 | 0 | 0 | — | — | **需可達後端**；無守衛 → 沒棧時 **fail**，不是 skip |
| `research-drafts.spec.ts` | 14 | L2 | 19 | 0 | 0 | 0 | — | — | **需可達後端**；無守衛 → 沒棧時 **fail**，不是 skip |
| `risk-journal-and-price-view.spec.ts` | 6 | L2 | 6 | 0 | 5 | 0 | — | — | **需可達後端**；無守衛 → 沒棧時 **fail**，不是 skip |
| `model-lifecycle.spec.ts` | 5 | L2 | 2 | 0 | 0 | 0 | — | — | **需可達後端**；無守衛 → 沒棧時 **fail**，不是 skip |
| `personal-api-trade-visibility.spec.ts` | 5 | L2 | 5 | 0 | 0 | 0 | — | — | **需可達後端**；無守衛 → 沒棧時 **fail**，不是 skip |
| `strategy-preflight-feedback.spec.ts` | 3 | L2 | 4 | 0 | 0 | 0 | — | — | **需可達後端**；無守衛 → 沒棧時 **fail**，不是 skip |
| `personal-strategy-learning.spec.ts` | 2 | L2 | 3 | 0 | 15 | 2 | Y | Y | **需可達後端**；無守衛 → 沒棧時 **fail**，不是 skip |
| `order-lifecycle-flow.spec.ts` | 10 | L3 | 5 | 0 | 4 | 1 | — | — | 無 `E2E_EMAIL`/`E2E_PASSWORD` → 整檔 **skip**（`runtime.ts:47-51`） |
| `robustness-study.spec.ts` | 8 | L3 | 2 | 0 | 0 | 1 | — | — | 無 `E2E_EMAIL`/`E2E_PASSWORD` → 整檔 **skip**（`runtime.ts:47-51`） |
| `unified-research-flow.spec.ts` | 8 | L3 | 4 | 0 | 3 | 1 | — | — | 無 `E2E_EMAIL`/`E2E_PASSWORD` → 整檔 **skip**（`runtime.ts:47-51`） |
| `strategy-authoring-and-lock.spec.ts` | 7 | L3 | 3 | 1 | 2 | 1 | — | — | 無 `E2E_EMAIL`/`E2E_PASSWORD` → 整檔 **skip**（`runtime.ts:47-51`） |
| `cross-experiment-comparison.spec.ts` | 6 | L3 | 8 | 1 | 2 | 1 | — | — | 無 `E2E_EMAIL`/`E2E_PASSWORD` → 整檔 **skip**（`runtime.ts:47-51`） |
| `intraday-rule-flow.spec.ts` | 6 | L3 | 3 | 0 | 1 | 1 | — | — | 無 `E2E_EMAIL`/`E2E_PASSWORD` → 整檔 **skip**（`runtime.ts:47-51`） |
| `research-record.spec.ts` | 6 | L3 | 3 | 0 | 0 | 1 | — | — | 無 `E2E_EMAIL`/`E2E_PASSWORD` → 整檔 **skip**（`runtime.ts:47-51`） |
| `strategy-revision.spec.ts` | 6 | L3 | 3 | 0 | 0 | 1 | — | — | 無 `E2E_EMAIL`/`E2E_PASSWORD` → 整檔 **skip**（`runtime.ts:47-51`） |
| `rule-triggered-flow.spec.ts` | 5 | L3 | 3 | 0 | 1 | 1 | — | — | 無 `E2E_EMAIL`/`E2E_PASSWORD` → 整檔 **skip**（`runtime.ts:47-51`） |
| `workspace-replay-backend.spec.ts` | 4 | L3 | 5 | 0 | 2 | 1 | — | — | 無 `E2E_EMAIL`/`E2E_PASSWORD` → 整檔 **skip**（`runtime.ts:47-51`） |
| `crypto-research-markers.spec.ts` | 3 | L3 | 2 | 0 | 0 | 1 | — | — | 無 `E2E_EMAIL`/`E2E_PASSWORD` → 整檔 **skip**（`runtime.ts:47-51`） |
案例總數 = 312 檔數 = 32
分層案例數 = {'L0': 64, 'L2': 58, 'L1g': 37, 'L3': 69, 'L1': 84}

**分層案例數（同一組數字，可相加核回 312）**：L0 64 ＋ L1 84 ＋ L1g 37 ＋ L2 58 ＋ L3 69 ＝ **312**。

**兩檔的 blast radius 明顯大於其他 30 檔**，必須單獨批准、單獨執行：

- `first-use-journey.spec.ts`（4 案）在 `:62-66` 以 `spawnSync` 起一支 **python 子行程**執行
  `backend/scripts/seed_candidate_scenarios.py`，並把 `DATABASE_URL` 直接塞進它的環境（值來自
  `E2E_FIRST_USE_DB_URL`）。這繞過了 API，直接寫入「該變數指到的那個資料庫」。
- `personal-strategy-learning.spec.ts`（2 案）在 `:116-120` 起 `seed_e2e_personal_learning.py`，
  並在 `:132-136` 以 `spawn` 長期啟動 **`python -m app.workers.training_worker 1`**（stdout 匯入
  `E2E_PERSONAL_WORKER_LOG`）。這是整套 e2e 唯一會自己拉起背景 worker 的 spec。
  → 因此這 6 案的前置條件是：`*DB_URL` 必須指向本包自建的可丟棄 DB（§3），且 worker pid 由該 spec
  自己記錄、自己收尾；本包不對任何其他 pid 發信號（§7）。

**啟動政策（回應「不可因可枚舉就一次啟動所有案例」）**

- 已可執行、且**不需任何批准**的：只有 `--list`（上表來源，exit 0，不啟動 webServer）。
- **目前一案都跑不起來**，包括 L0 的 64 案：`playwright.config.ts:33-37` 的 `webServer.url` 綁
  `FRONTEND_URL`，`command` 只是 `echo`、`reuseExistingServer: true`；沒有棧時 Playwright 會在
  等待該 URL 時逾時退出，連 skip 訊息都走不到（§6 末段早已記錄同一現象，並列為 NOT RUN）。
  這不是缺陷清單上的新項目，而是「312 可枚舉」與「312 可執行」之間的結構差距。
- 建議的執行順序（每一步都是獨立批准，前一步不停即不進下一步）：
  **E5-a** 起前端＋後端 → 跑 L0 64 案（驗 webServer 解除後不碰服務）；
  **E5-b** L1＋L1g 共 121 案（只碰本機 origin，未攔截路徑會落到本包後端）；
  **E5-c** L2 58 案（需本包後端＋本包自造的兩個接受度帳號）；
  **E5-d** L3 69 案（另需 `E2E_CRED_FILE`）；
  **E5-e** 上述兩檔 6 案（另需 `spawnSync`／worker 許可，單獨批准）。
- **時間成本：NOT MEASURED** —— 本輪沒有任何一檔 e2e 實際執行過，因此不對 312 案的耗時給估計。
- L1 的 84 案裡「哪些 `/api/...` 路徑未被 `page.route` 涵蓋」是**逐案 UNRESOLVED**：靜態掃描只能證明
  該檔有攔截、有.goto，不能證明覆蓋完整。這要由 E5-b 執行時的 trace 定案，不在這裡宣稱。

**掃描器範圍缺口（老實登記，不在本輪修）**：上列 4 處寫死的接受度密碼位於**已追蹤的 `.ts`** 檔內，
而 `check_env_hygiene.py` 的 assignment scope 只覆蓋設定檔副檔名（`SETTINGS_SUFFIXES`），
TypeScript 不在其中 → 這 4 個字面值目前是 gate 的盲點，不是 gate 豁免的例外。
修法（等本輪固定狀態執行結束後）：把 `.ts`/`.tsx` 納入 assignment scope，並把現有 4 處以
鍵名＋位置＋值類別登記進 debt ledger（內容綁定 digest，絕不輸出值）。
在此之前，§9.2 的秘密檢查範圍宣稱必須照 scanner 自己的說法讀：它覆蓋**追蹤＋未追蹤的設定檔**，
不覆蓋程式碼裡的寫死退回值。

---

## 7. 故障注入

- **只對本輪自建程序發信號**：`api_stop_recorded`（run_r2.sh:266-277）在發信號前先核對
  `ps -o command=` 含 `uvicorn app.main:app`，不符即**完全不碰該 pid**；任何其他程序一律不發信號。
- 不對共享 DB 注入故障：目標 DB 為本包自建的 `alethra_test`；黑名單 DB 與 `.local/pgdata-16` 叢群不連線、不 kill。
- 不對其他 session 的程序注入故障（本 checkout 由兩個平行 session 共用，pid 一律限 `run_r2.sh` 自己記錄的那個）。
- 注入時點：僅在 DB 真的出現 `status IN ('pending','running')` 的該列之後；看不見即 exit 3（V8b）。
- 失敗即保存現場：保留 PGDATA／evidence／package，停在第一條 trace，不重試、不重建、不刪。

---

## 8. 保存與發布

**待提交檔案（逐檔 stage，絕不 `git add -A`）**

| 檔 | 動作 | 內容性質 |
|---|---|---|
| `backend/tests/test_acquisition_hook.py` | commit | 一行 import 修正（讓 42 條可收集）|
| `backend/tests/test_ownership_integration.py` | commit | sqlite lane skip；四條 raise 全部保留且已實測仍生效 |
| `backend/tests/test_env_hygiene_contract.py`、`backend/scripts/check_env_hygiene.py`、`backend/.env.test.example`、`.gitignore` | commit | §四 長期根因三件套＋契約（22 條）|
| `backend/migrations/versions/e1f2a3b4c5d6_*.py`、`f2a3b4c5d6e7_*.py` | commit | 終態 `cancelled`、request-loss idempotency 結構（**NOT RUN on PG**）|
| `frontend/playwright.config.ts`、`start_e2e_research_flow.sh`、`run_r2.sh` | commit | 瀏覽器 channel 閘、alembic head 派生、執行包腳本本身 |
| `frontend/e2e/runtime.ts`（新增 69 行）＋ 上述 **11 個 spec** | commit | §6 枚舉修復：共用 tier-B loader，載入時期不 throw、run 時期仍 fail-closed（`--list` 0→312）|
| `EXECUTION_RECORD_20261109.md`、`docs/approval-package-2026-11-16-full-stack-execution.md` | commit | 記錄與本包 |
| `docs/approval-package-2026-11-16-attachment-A-script-content.md`（新增）| commit | 附件 A：三支 tail migration ＋六支執行腳本的**全文**（規則式機密遮罩後；§4 開頭說明其來源與重產方式）|
| `.github/workflows/secret-hygiene.yml`（新增，未追蹤）| commit（**G1–G3 未批前不生效**）| 秘密 gate 進 CI：**不需真實秘密、不啟任何服務、無外呼** —— scanner 只用 stdlib（實測其 import 集合為 `__future__/dataclasses/hashlib/pathlib/re/subprocess/sys/typing/urllib`），因此這個 workflow **沒有** `pip install` 步驟；`permissions: contents: read`、`timeout-minutes: 5`、全檔不出現 `secrets.` 引用（實測 grep 0 命中）|

**秘密檢查的兩個範圍（owner 2026-11-17 六：「未追蹤但將加入提交的程式／設定檔，亦須納入提交前秘密檢查；只掃 tracked files 不足以覆蓋新檔」）**：
`check_env_hygiene.py` 現行有 `tracked`（`git ls-files`）與 `commit`（index ∪ `git ls-files --others --exclude-standard`）兩個範圍，
後者由 `--pre-commit` 選取。本輪實測：`tracked` 範圍 **830** 個追蹤文字檔 → 5 條 finding、RC 0；
`--pre-commit` 範圍 **866** 個文字檔（15 個設定檔）→ **同一組** 5 條 finding、RC 0 —— 也就是新增的未追蹤檔
（含上列兩個新檔）目前**不帶任何超出 debt ledger 的字面值**。仍然成立的兩個盲點寫在 scanner 自己的文件字串裡：
被 `.gitignore` 忽略的檔不在任何範圍內（有契約節點釘住此事實）、`git add -f` 仍可繞過；
以及**程式碼副檔名不在 assignment 範圍**（§6a「掃描器範圍缺口」記的 4 處寫死退回密碼即屬此項）。

**秘密檢查**：`git diff --quiet -- backend/.env.test` rc=0（檔內容本輪未被改動）；
`.gitignore` 已含 `backend/.env.test` 忽略規則；本包與所有 evidence 只含**鍵名／種類／長度／sha256 前 10 字／相等判定**，
不含祕密值、不含匹配片段、不含含祕密的 diff。
`start_e2e_research_flow.sh` 的憑證由 `openssl rand` 生成進 git-ignored 的 `.local/e2e-credentials.env`（mode 600，由腳本自身檢查），
**不進 Git**。提交前逐檔 `git diff --cached` 讀一次，若任何看起來像祕密的東西被 stage，立即停止。

**已知限制（提交資訊必須帶著走）**
1. 三支 tail migration **從未在 PostgreSQL 上執行過**；測試物件由 `Base.metadata.create_all` 產生，不是由 migration 檔產生。
2. predicate 相等由測試以**文字**比對釘住 — 這是兩個檔案的保證，不是資料庫結果。
3. `e1f2a3b4c5d6` 的 `cancelled` 標籤在 PG 上**不可回退**（enum 值無法移除）。
4. e2e tier-B 棧在本機不可用（無 Docker）；tier-B specs 需要憑證與 `alethra_e2e*` DB（該名不在 pytest guard 白名單，
   只由 e2e seed 的前置閘管 `start_e2e_research_flow.sh:79-81` 要求，且**永遠不會**拿它跑 pytest lane）。
5. 環境上限缺失（無 CPU/memory 隔離），以單進程＋loopback＋in-process provider 代替。
6. `alethra/backend/postgres` 是一個 0 位元組的未追蹤怪檔（今日 11:54 出現，非本 session 所為，原因 UNKNOWN）；
   本包**不提交、不刪除**，留給 owner 判斷（同型 `zsh` 未加引號 redirect 產生 0 位元組檔的慣用錯誤）。

**GitHub 目標**：`origin` = `https://github.com/TommyLam0915-LCW/alethra.git`，branch `live1-impl`，
起點 SHA `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（與本地 HEAD 相同）。
推送後以 `git ls-remote origin live1-impl` 核對**完整 40 字元** SHA；未核對前一律標「LOCAL COMMITTED / NOT PUSHED」。
不 force-push、不 rebase 已推送內容、不重寫歷史。

---

## 9. 批准分項

### 9.1 執行批准（每項可單獨批准或拒否；未獲批准者維持 NOT RUN）

- **E1｜PG 啟動（無安裝）**：`bash run_r2.sh install && bash run_r2.sh provision`
  → 寫 `$ROOT/.r2/pgdata`（實測不存在）、以 wheel 內建 PG 16.2 起 `127.0.0.1:5432`、建 `alethra_r2`＋`alethra_test`。
  不 pip install、不建新 venv、不碰 `.local/pgdata-16`。
- **E2｜migration**：`bash run_r2.sh gate && bash run_r2.sh migrate`
  → 對 `alethra_test` 跑完整 54 支鏈到 `f2a3b4c5d6e7`；三支 tail 首次在真 PG 上執行；失敗即保存首次 trace。
- **E3｜PG 測試層**：`bash run_r2.sh tests`
  → recovery 26／two_phase_quota 31／ownership_integration 10（以 `run_tests.sh pg` 的實印為準，非本包宣稱）。
  **硬性前置：E2 必須先完成。** PG 層的測試檔**自己不建 schema** —— 例：`test_quota_concurrency.py:19-22`
  的模組開頭明文記錄它**移除了** `Base.metadata.create_all`／`drop_all`，改由
  `:112` 的 `create_verified_session_factory(db_url)` 連到**既存**結構。因此 E2 未跑時 E3 的失敗是
  `UndefinedTable` 之類的結構性錯誤，**不是**產品缺陷；這一行存在的理由就是不會讓那種失敗被讀成後者。
- **E4｜服務＋故障注入**：`bash run_r2.sh timeprobe && api-loop`（必要時 `restart-verify`）
  → 只對自記 pid 發 SIGKILL；V4–V11 在此閉環。
- **E5｜瀏覽器層（已按 §6a 拆成五個可分開批准的子項，不再是一整包）**：
  `next dev` on `127.0.0.1:3099`（**必須**帶 `NEXT_PUBLIC_API_URL=http://127.0.0.1:8010`，見 §5a 第 1 條）
  ＋ `E2E_BROWSER_CHANNEL=chrome`，用既存 Chrome，無瀏覽器下載、無系統改動。
  先決條件已滿足：§6 的枚舉缺陷本輪已修，`--list` 實測 **312 tests／32 files、exit 0**（修前 0 tests／0 files）。
  但「可枚舉」不等於「可執行」：`playwright.config.ts:33-37` 的 `webServer.url` 讓**任何**一案在無棧時先逾時，
  所以 E5-a 之前 312 案全部維持 NOT RUN，且**不一次啟動全部案例**（owner 2026-11-17 五）。

  | 子項 | 對象（§6a 分層） | 案例 | 額外前置 | 停止線 |
  |---|---|---|---|---|
  | E5-a | L0 純程式層 4 檔 | 64 | 前端可達（只為解除 `webServer` 逾時）＋後端已在 8010 | 任一案需要網路才能綠 → 立即停，記錄該案 |
  | E5-b | L1＋L1g 共 12 檔 | 121 | 同 E5-a；未攔截路徑會落到本包後端 | 出現非 loopback 請求 → 停＋記錄 URL（值不含查詢字串） |
  | E5-c | L2 無守衛需後端 8 檔 | 58 | 兩個本包自造的接受度帳號（`POST /api/auth/register`，非既有憑證） | 連線拒絕 → 停，不改測試、不降斷言 |
  | E5-d | L3 tier-B 11 檔 | 69 | 另需 `E2E_CRED_FILE`（本包自己產生並寫入 `.local/`，不進 git） | 缺憑證即整檔 skip＝如實記錄，不視為通過 |
  | E5-e | `first-use-journey`＋`personal-strategy-learning` | 6 | 另需批准 `spawnSync` python 子行程與**長駐 training worker**；`*DB_URL` 必須指向 §3 的可丟棄 DB | 子行程超出本包自記 pid → 停，不 kill 任何非自記程序（§7） |

  時間成本 **NOT MEASURED**：本輪從未執行過任何一檔 e2e，不對耗時給估計。
- **E6｜正常停止**：`bash run_r2.sh stop`（`pg_ctl stop -m fast` ＋ TERM 自記 uvicorn pid）。破壞性 `--purge` 不在本包。

### 9.2 Git 批准

- **G1｜提交**：§8 表列路徑，逐檔 `git add`，不含 `backend/.env.test`、不含 venv／node_modules／evidence。
- **G2｜推送**：`live1-impl` → `origin`，推後 `git ls-remote` 核對完整 SHA 並回報實際值。
- **G3｜`git rm --cached backend/.env.test`**：把它從版本追蹤移除（檔留在磁碟、已被 .gitignore 忽略）。
  這是**變更 Git 索引中已追蹤檔**的獨立項目，需要單獨批准，不隨 G1 預設通過。
- **不在批准範圍**（§四 明列需另外精確批准，本包不順勢執行）：既有憑證的失效／輪替、更動其他服務、Git 歷史重寫。

---

## 10. 矩陣摘要與 NOT RUN 清單（§一／§六 用）

> **本表是該輪（authoritative 3462 item）的快照，未追改。**之後兩次全量（3479 item 復測、目前 collected=3480）的身份、結果與 gate 缺陷記錄在 §11.10-7；本輪**只測量、未修改**的 10 項產品判定在 §12。判讀能力面時以 §11.10-7 ＋ §12 為準，本表用來對照「哪一列在哪一輪變了」。

| 能力面 | 已實作 | 已執行通過 | 失敗 | 受阻 | NOT RUN |
|---|---|---|---|---|---|
| 帳號、權限與隔離 | 是（後端 ownership filter＋JWT）| sqlite lane：31 admission／26 recovery／62 前端 node 契約 | — | 真 PG 雙帳號隔離需 E1–E4 | PG 隔離、瀏覽器雙帳號 |
| authoring／revision／lock／能力契約 | 是 | sqlite lane（同上）| — | — | 端到端（E4）|
| 資料來源／grain／provenance | 是（manifest endpoint 16 條契約）| sqlite 16／16 | — | 真 PG dataset 選擇 | PG＋UI 選取 |
| durable admission／quota／終態／audit | 是 | sqlite 31＋26；兩階段記帳 31（SQLite 同連線，跨事務併發測不到）| — | MVCC 下的真併發**只有 PG 能測** | E2–E4 |
| request-loss idempotency | 是（header 閘＋service＋index 檔）| sqlite lane 契約 | — | index 從未在 PG 生效 | E2、V6 |
| 明確取消 | 是（`POST /runs/{id}/cancel`＋`cancelled`）| sqlite；前端 runCancellation 11／11 | 遺留觀察：study 從未執行時，取消不產生 audit event | enum 值需 E2 | PG |
| 中斷／恢復政策 | 是（fail-closed recovery）| sqlite 26／26 | — | SIGKILL 需 E4 | E4、V8／V8b |
| 結果／決策／成交記錄與匯出 | 是（`/runs/{id}/trades.csv`）| sqlite run_trades_export 17／17 | — | PG 層 | E4、V10 |
| 正式 UI 流程與錯誤狀態 | 32 specs／**312 tests 可枚舉**（本輪修好 load-time throw）；4 個 node 契約檔通過 | 僅 node 層 62 條＋`--list`／`tsc --noEmit` 兩條閘 | 原缺陷「整組無法枚舉」已修並實測（0→312）| 需 E5（`next dev`＋Chrome）| 全部 browser 案例執行 |
| env check／start／status／verify／stop | 是（F 輪腳本＋20 條 `test_env_entries`）| sqlite 20／20 | — | 真實 start/status/verify/stop 尚未驗收（不標 F 全面完成）| E6 |
| 可重現環境、依賴安全、GitHub 保存 | 是（本包 §2／§3；env-hygiene 掃描器 22 條契約）| 掃描器 CLI exit 0，5 findings／ledger 相符 | — | **掃描器尚未接進 `run_tests.sh`／CI**（自承缺口）；Git 寫入需 G1–G3 | 接線後端到端 |

**NOT RUN（誠實清單）**
1. 任何 PG 寫入：install／provision／migrate／gate／tests／api-loop（只有 `guard` 跑過，唯讀）。
2. 三支 tail migration 在 PostgreSQL 上的實際套用與 `alembic current` 核對。
3. 真實 SIGKILL／重啟收斂（timeprobe 尚未跑，V8b 是預留的誠實出口）。
4. 瀏覽器操作任何一個 spec（枚舉先決條件已滿足，**執行**仍需 E5 的 `next dev`＋`E2E_BROWSER_CHANNEL=chrome`）。
5. `start_e2e_research_flow.sh` 的 FRESH／RESUME 全路徑（本機無 Docker）；只有 `--show-head` 與派生閘被實測。
6. `derive_alembic_head` 的雙 head 拒絕分支。
7. 任何 Git 寫入（commit／push／`rm --cached`）— 未獲 G1–G3。
8. `backend/.env.test` 中既有字面值是否**仍然有效**＝**UNKNOWN**（驗證需未批准的 DB 連線或已簽名的券商呼叫）。

---

## 11. sqlite lane 基線分解（§二 要求：根因一次拆完，不逐缺陷請示）

### 11.1 起點基線（本輪之前）

| 項目 | 值 |
|---|---|
| 命令 | `TMPDIR=/tmp/alethra-sqlite-test-dir DATA_PROVIDER=demo bash ./backend/scripts/run_tests.sh sqlite -q -p no:warnings --tb=line -rf` |
| 結果 | **78 failed, 3167 passed, 88 skipped, 66 errors**, 1903.63s, exit 1 |
| 現場 | `/tmp/full_sqlite_20261116b.txt`（未覆寫，保留） |
| lane fingerprint | `python 3.12.16 \| pytest 8.3.3 \| sqlalchemy 2.0.36` |
| DB | 每 PID 新檔 `/tmp/alethra-sqlite-test-dir/alethra-sqlite-test-<pid>.db` |

**方法學教訓（影響所有先前歸因）**：`-rf` **不列 setup error**。66 errors 在 baseline b 的短摘要中完全不存在，因此本輪之前對 errors 的任何「未歸因」判斷都是工具輸出造成的，不是測試造成的。本輪起改用 `-rfE`（或 `-ra`）。

### 11.2 本輪修掉的**可移植**缺陷（非 PG 特有）

| # | 缺陷 | 位置 | 影響 | 實測結果 |
|---|---|---|---|---|
| 1 | 13 條離線測試結構性死亡：patch 模組全域 `SessionLocal`，卻用 `"postgresql://test"` 建構 engine → patch 從未被打開，psycopg2 先死 | `tests/test_replay_credibility.py`、`tests/test_position_timing_bugs.py`（改寫）＋新增 `tests/replay_fake.py` | 「已實作」被誤讀為「已驗證」的來源 | 13 failed → **21 passed**（17＋4），RC=0 |
| 2 | `cancel_requested`（BOOLEAN NOT NULL DEFAULT false）只存在 migration `c9d0e1f2a3b4`，不在 ORM model → sqlite `create_all` 建出的表無法被 service 的 raw SQL 查詢 | `backend/app/models/paper_trading_run.py` | model↔migration 漂移（PG 上也漂移，只是被既有 DB 掩蓋） | `no such column: cancel_requested` 錯誤群消失 |
| 3 | fixture INSERT 引用 `paper_portfolios.updated_at`，該欄在 model 與初始 schema migration（`55a8d07a7f40`）**兩邊都不存在** | `tests/test_formal_path_integration.py:136-147` | 在 PostgreSQL 上同樣會失敗 | 該檔不再因此報錯 |
| 4 | 30 處 `'{}'::jsonb` / `'[]'::jsonb` inline cast（10 支 fixture 檔）：JSONB 欄位接受裸 JSON 字串 literals，PG 不需要 cast | `tests/{test_owner_verification,test_real_concurrency,test_http_api_success_chain,api_test_fixtures,test_regression_contracts,test_paper_trading_cancel_atomic,test_ledger_reconciliation,test_retry_failure_validation,test_decimal_precision_regression,test_source_dedup_scope}.py` | `unrecognized token: ":"` 把 7 條 setup error 變成噪音，掩蓋真正的 PG-only 前提 | 全 repo `tests/` 剩餘 `::jsonb`：**0** |
| 5 | `test_regression_contracts.py` 用裸名 `from test_target_guard import ...`，只在 `test_paper_trading_cancel_atomic.py:71` 已把 `tests/` 插入 `sys.path` 時才解析得到 | `tests/test_regression_contracts.py:16`（＋ fixture 內 :30） | **檔案順序相依**：子集執行直接 `Interrupted: 1 error during collection`（RC=2），全量執行卻看不見 | 改為 `from tests.test_target_guard import ...`；單獨執行可收集（5 tests collected） |

### 11.3 我本輪**自己引入又移除**的缺陷（不藏）

在 11.2(3) 的編輯中，我把說明文字以 `#` 開頭寫進 `text("""...""")` 的 SQL 字串**內部**。`#` 不是 SQL 註解（兩邊都不是）：PostgreSQL 用 `--`／`/* */`，SQLite 直接 `unrecognized token: "#"`。結果 5 條 formal-path 測試從 `no such column` 換成 parse error，且在 sqlite lane 連鎖拖出 `database is locked`（fixture 的 `finally` 清理在 `db.commit()` 前拋錯 → session 未 `close()` → 寫鎖外洩給後續測試）。已把註解移到 `db.execute()` 之外並以 `.venv/bin/python` 全 repo 掃描確認**只有這 1 處** SQL 字串內含 `#`。此缺陷從未提交。

### 11.4 標記為 PostgreSQL-only 的群（附**實測**理由，非推測）

sqlite 無法證成的前提共三類，皆已在 repo 既有 idiom（`tests/test_manual_decision_books_linked_portfolio.py:139-141`）上表達，理由字串各自不同：

| 群 | 檔 | 實測 blocker | 標記 | 前 → 後 |
|---|---|---|---|---|
| A | `test_replay_status_management.py` | `near "FOR": syntax error` @ `app/services/paper_trading.py:874` | module | 5 failed → 5 skipped |
| A | `test_formal_path_integration.py` | UUID/Decimal 型別綁定＋`FOR UPDATE` | module | 5 errors → 5 skipped |
| A | `test_ledger_reconciliation.py` | `create_portfolio()` raw SQL 綁 `Decimal` @ `paper_trading.py:74`＋`FOR UPDATE` | module | 5 errors → 5 skipped |
| A | `test_owner_verification.py` | 同上（param 3 Decimal） | module | 2 errors → 2 skipped |
| A | `test_decimal_precision_regression.py` | NUMERIC 精確度＋seed `Decimal('1000.0')` | **class**（`TestDecimalPrecisionRegression`）| 1 error → 1 skipped；同檔 `TestExposureLimitBoundary` **3 passed 保留在 lane** |
| A | `test_retry_failure_validation.py` | 全部 3 條都走 `create_portfolio`＋replay | module | 3 errors → 3 skipped |
| A | `test_real_concurrency.py` | barrier 併發需要真 MVCC | module | 1 error → 1 skipped |
| A | `test_http_api_success_chain.py` | POST portfolios → 同一 Decimal bind（產品回應 `creation_failed`）| module | 1 failed → 1 skipped |
| A | `test_source_dedup_scope.py` | 兩條都以 `create_portfolio` 開跑 | module | 2 failed → 2 skipped |
| B | `test_regression_contracts.py` | fixture fail-closed：無 `ALETHRA_PG_TEST_URL` 不fallback＋驗證 target | module | 5 errors → 5 skipped |
| C（待標） | `test_connection_role_verification.py`（5）、`test_data_isolation.py`（3，`_test_sacrificial_`）、`test_cleanup_verification.py`（2，同表）、`test_privilege_enforcement.py`（1／7：`SQLSTATE 42501`，sqlite 無 role 概念，`CREATE succeeded`）、`test_target_guard.py`（2／61：guard 探針用 `current_database()`）、`test_vertical_path.py`（1／3：斷言 `current_database()`／`current_user`） | 角色、權限、`current_database()`、sacrificial 表皆為 PG 專屬 | 3 module＋3 per-test | 尚未套標記（避免在權威測量進行中改樹） |

> 注意 C 群的粒度：`test_target_guard.py` 61 條中只有 2 條是 PG-only，`test_privilege_enforcement.py` 7 條中只有 1 條。整檔標記會**刪掉真實 lane 覆蓋**，故這两檔只能 per-test 標。

**skip 不是 pass（本輪的帳務影響必須被看見）**：`run_tests.sh:218` 固定 `exec "$PYTHON" -m pytest -rs "$@"`，所以每條 skip 的理由在**任何** lane run 都會印出，不需要额外旗標（本輪 `fpi_r4` 的 `SKIPPED [n] …: <reason>` 即為證據）。但腳本**沒有**「skip 數量不得無故成長」的閘：本輪把 33 條 failed/error 變成 skipped，lane 的 skipped 總數因此上升，而 passed 總數只在 11.2(1) 的 13→21 處上升。**判讀規則**：一個能力面若只出現在 skipped 清單，其狀態是 NOT RUN（PG），不是已執行通過；對應驗收在 §9.1 的 E3，不在本 gate。

> **勘誤（2026-11-16 §11.7 第 3 點撤回本段首句）**：「在任何 lane run 都會印出」為 false。pytest 只認最後一個 `-r`，呼叫端尾端的 `-rfE` 會蓋掉腳本的 `-rs`，實測該全量日誌印出 **0** 條 `SKIPPED`。原始句子保留不改寫；修正後的行為（`-rfEs` 附加於呼叫端之後，另有 skip 成長的判讀仍屬缺口）由 `backend/tests/test_lane_entry_contract.py` 第一條測試鎖住。

### 11.5 「real-DB paper-trading 垂直路徑在 sqlite 結構性不可跑」是一條 lane 事實，不是缺陷清單

三個不可迴避的 PG 依賴同時存在於**產品碼**與 worker 路徑：
1. `backend/app/services/paper_trading.py:874` — `SELECT id, status, cancel_requested FROM paper_trading_runs WHERE id = :id FOR UPDATE`（SQLite 無此語法）；
2. `backend/app/services/paper_trading.py:74`、`:90` — raw SQL 綁 `Decimal`（只有 psycopg2 適應）；
3. 併發取得權依賴真 MVCC。

本輪**刻意不**為了讓離線 lane 變綠而改造產品 SQL（型別化 bindparam 會把 NUMERIC 換成 REAL 語意，讓「精確度」宣稱在錯的 lane 上變色），也**不**移除 `FOR UPDATE`（那是取消／重入併發安全的根據）。此垂直路徑的驗收因此全部歸 §9.1 的 **E3（PG 測試）**，不是離線 gate。

### 11.6 本輪撤回的一條錯誤歸因

先前把 `test_ml_pipeline.py` 的 19 條與 `test_learning_api.py` 的 6 條讀成「sklearn／PyJWT 未安裝」。實測否定：
- `.venv/bin/python -c "from sklearn.preprocessing import StandardScaler"` → **成功**（scikit-learn 1.9.1，`site-packages/sklearn/__init__.py`，`.so` 檔俱在）；
- `tests/test_learning_api.py` 現況第 51 行已是 `from jose import jwt`（ jose 3.3.0 已裝；該檔註解已說明 PyJWT 不在需求也不 needed），單獨執行 **13 passed**。

所以 baseline b 中這兩群的成因是：(a) 該基線對 `test_learning_api.py` 的行號引用的是**已被修掉**的內容；(b) ml_pipeline 的 `ImportError: unknown location`＋`assert 'pending' == 'completed'`＋`NoneType not subscriptable` 屬**同一次執行內的狀態干擾**，需以檔案順序 bisect 定因，不能歸咎環境。**方法**：對 `tests/test_ml_pipeline.py` 先單獨執行，再與候選前序檔成對執行，找出把 sklearn 留在半初始化狀態的那一支。

### 11.7 authoritative fixed-state 全量測量與節點級帳（§一 要求：不要只交總數）

#### 1. 測量身分（每一欄都可重核）

| 項目 | 實測值 |
|---|---|
| cwd | `/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend`（腳本內 `cd`）|
| 命令 | `TMPDIR=/tmp/alethra-sqlite-test-dir DATA_PROVIDER=demo bash ./backend/scripts/run_tests.sh sqlite -q -p no:warnings --tb=line -rfE`（**無 path 參數＝全量**；外層 `echo "FULL_SUITE_EXIT=$?"`）|
| 命令是否由日誌保存 | **否**。該命令由日誌格式重組：`-q`（每 item 一個進度字元、無逐項名稱）、摘要含 38 條 `ERROR` 行（故 `-r` 帶 `E`）、無 `===== WARNINGS =====` 區塊、`--tb=line` 單行回溯 53 條。這是**本節第 3 點**記錄的工具缺口，不是推測的捷徑。|
| HEAD | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` |
| diff 身分 | 執行時 **91 條髒路徑**（未提交）；本節撰寫時 92（其後新增 `backend/tests/test_lane_entry_contract.py`）。HEAD＋髒路徑清單＝可重現身分；**未提交**＝目前只有本機可重現。|
| provider 設定 | `DATA_PROVIDER=demo`（離線、無外呼）；`JWT_SECRET_KEY` 由 lane 每次生成 |
| runner fingerprint | `python 3.12.16 \| pytest 8.3.3 \| sqlalchemy 2.0.36`（由 `.venv/bin/python` 印出，非路徑推測）|
| DB | `sqlite: /tmp/alethra-sqlite-test-dir/alethra-sqlite-test-73635.db`（每 PID 新檔；preflight 行已印於日誌）|
| 收集數（同一樹）| `--collect-only` **3397 tests collected**（三次重測 2.62／2.64／2.70s 皆同一數）|
| 結果 | **47 failed, 3199 passed, 118 skipped, 38 errors**；deselected **0** |
| exit code | `FULL_SUITE_EXIT=1`（pytest 1＝有失敗；非工具錯誤）|
| 執行時間 | **1826.73s (0:30:26)** |
| 原始日誌 | `/tmp/full_sqlite_20261116c.txt`（46,999 bytes，mtime 22:48；**未覆寫**，並存 `/tmp/full_sqlite_20261116b.txt` 起點基線 95,671 bytes）|
| 樹在執行期間是否被改 | **否**。執行期間未編輯被測程式或測試內容；本節的工具修正（見 2.）在測量**之後**，因此 47／3199／118／38 屬於**修正前**的樹，不是混雜狀態的結果。|

#### 2. 這條日誌原本不可核對的兩個工具缺陷（已修，並有契約測試）

| 缺陷 | 實測機制 | 後果 | 修正與驗證 |
|---|---|---|---|
| lane 的 `-rs` 被呼叫端蓋掉 | pytest 只實作**最後一個** `-r`：呼叫端尾端的 `-rfE` 使腳本原本的 `-rs` 失效 | 全量日誌中 `SKIPPED` 出現 **0 次**，118 條 skip 無逐條理由 → 「每個 changed-to-skip node 列理由」無法從日誌交出去 | `run_tests.sh` 改為把自身 `-rfEs` **附加在 `"$@"` 之後**（`backend/scripts/run_tests.sh:247`）。實測：帶 `-rfE` 的呼叫現在仍印出 `SKIPPED [1] tests/test_owner_verification.py:142: owner verification over real rows requires a PostgreSQL backend` |
| `-q` 吃掉收集數 | `-q` 抑制 `N tests collected` 標頭 | 日誌無法與它所跑的樹對帳（3397 只能靠另外跑一次 `--collect-only` 取得）| lane 在執行前自行做 `--collect-only` 並印 `selection on this tree: …`；**選空即拒**（RC=2），另印 `command:`／`cwd:`／`git:` 三行身分。實測 RC=0 印出 `20 tests collected`；`-k no_such_item` 印 `no tests collected (2 deselected)` 且 **RC=2 拒跑** |

新增契約檔 `backend/tests/test_lane_entry_contract.py`（**9 passed，5.38s，RC=0**，sqlite lane）把上述全部鎖住：skip 條目化在呼叫端給 `-rfE` 時仍在、收集數行、空選拒跑、未知 layer 拒跑、`ALETHRA_SQLITE_DB` 相對路徑拒跑、`TMPDIR` 不存在拒跑、`ALETHRA_PYTHON` 不可執行拒跑、`pg` 無 `ALETHRA_PG_TEST_URL` 拒跑、命令/cwd/git 三行身分。**此前套件從未執行過 `run_tests.sh` 本身**（`grep` 全 tests/ 找不到任何測試引用腳本的 `refusing:` 字串）。

#### 3. 撤回 §11.4 的一條宣稱（追加，不改寫原文）

§11.4 第 478 行寫「`run_tests.sh:218` 固定 `-rs`，所以每條 skip 的理由在**任何** lane run 都會印出，不需要額外旗標」。**該句為 false**，且被本節第 2 點的第一個缺陷直接否證：同一支腳本在 `-rfE` 呼叫下印出 **0** 條 `SKIPPED`。正確說法是：理由只在「無人後置 `-r`」時印出，而這不是腳本所能保證的性質。原始句子保留在此以免把歷史改寫成今天的樣子；修正後的行為由 `test_lane_entry_contract.py` 的第一條測試負責。

同一輪的方法學教訓（適用於所有以 `-q` 產出的日誌）：**`-q` 之下的進度字元流本身是一條可核對的記錄**。第 5 點的算術就是用進度字元做的，不是用第二天的 `--collect-only` 猜的。

#### 4. 節點級對帳（不是總數對總數）

先把两份日誌都換成 node id 再比對：baseline 用 `-rf`，摘要**不含** setup error，因此 66 errors 只存在為 66 行 `=== ERROR at setup/teardown of X ===` 標題；以 `--collect-only` 的 3390 個 `tests/` 開頭 id（另 7 個 id 在 `backend/` 根目錄）把 Class.test 名稱還原成 node id：**歧義 0、無法對應 0**。

| 帳目 | 節點數 |
|---|---|
| baseline 壞 id（78 FAILED 摘要 ＋ 62 setup-error ＋ 4 teardown-error 標題還原）| **140** |
| fixed 壞 id（47 FAILED ＋ 38 ERROR 摘要）| **85** |
| resolved（baseline 壞、fixed 不壞）| **55** = 22 已通過 ＋ 30 改 skip ＋ 3 改名 |
| still bad | **85** |
| **introduced（只在 fixed 壞）** | **0** |

**本輪先前交過一份「38 條 regression」的對比，那是錯的**：它只比對兩份日誌的 FAILED 摘要行，把 baseline 用 `-rf` 隱掉的 66 條 errors 當成不存在，因此把 38 條「一直存在但未被列出」的 error 讀成「新引入」。上表的 0 才是節點級重組後的值。

30 條 changed-to-skip 的**逐節點帳**（原先結果 → 現在 → 日誌逐字理由 → 對應 PG 命令 → 仍未執行）。產生方式：把 55 條 resolved id 交回修正後的 lane 重測一次（`/tmp/resolved52_remeasure.txt`，**22 passed／30 skipped，8.56s，RC=0**），理由字串取自該次 `SKIPPED` 行本身；節點 id 由 AST 的 def 行號對應，**30/30 的 def 行 == 日誌報出行**，未匹配 0 條。

| # | 節點 | 原先結果 | 現在（本 lane 實測） | skip 理由（日誌逐字） | 對應 PG 命令 | 仍未執行 |
|---|---|---|---|---|---|---|
| 1 | `test_decimal_precision_regression.py／TestDecimalPrecisionRegression／test_all_5_trades_execute_after_precision_fix` | ERROR at setup | skipped | NUMERIC exactness and the replay row lock require a PostgreSQL backend | `run_tests.sh pg tests/test_decimal_precision_regression.py::TestDecimalPrecisionRegression::test_all_5_trades_execute_after_precision_fix` | NOT RUN（E1–E3 未批准） |
| 2 | `test_formal_path_integration.py／TestFormalPathIntegration／test_cash_flow_reconciles` | ERROR at setup | skipped | formal path needs PostgreSQL: psycopg2 type adaptation and FOR UPDATE row lock | `run_tests.sh pg tests/test_formal_path_integration.py::TestFormalPathIntegration::test_cash_flow_reconciles` | NOT RUN（E1–E3 未批准） |
| 3 | `test_formal_path_integration.py／TestFormalPathIntegration／test_daily_pnl_matches_trades` | ERROR at teardown | skipped | formal path needs PostgreSQL: psycopg2 type adaptation and FOR UPDATE row lock | `run_tests.sh pg tests/test_formal_path_integration.py::TestFormalPathIntegration::test_daily_pnl_matches_trades` | NOT RUN（E1–E3 未批准） |
| 4 | `test_formal_path_integration.py／TestFormalPathIntegration／test_entry_and_exit_fills_both_created` | ERROR at setup | skipped | formal path needs PostgreSQL: psycopg2 type adaptation and FOR UPDATE row lock | `run_tests.sh pg tests/test_formal_path_integration.py::TestFormalPathIntegration::test_entry_and_exit_fills_both_created` | NOT RUN（E1–E3 未批准） |
| 5 | `test_formal_path_integration.py／TestFormalPathIntegration／test_entry_and_exit_orders_both_created` | ERROR at teardown | skipped | formal path needs PostgreSQL: psycopg2 type adaptation and FOR UPDATE row lock | `run_tests.sh pg tests/test_formal_path_integration.py::TestFormalPathIntegration::test_entry_and_exit_orders_both_created` | NOT RUN（E1–E3 未批准） |
| 6 | `test_formal_path_integration.py／TestFormalPathIntegration／test_positions_created_and_closed` | ERROR at teardown | skipped | formal path needs PostgreSQL: psycopg2 type adaptation and FOR UPDATE row lock | `run_tests.sh pg tests/test_formal_path_integration.py::TestFormalPathIntegration::test_positions_created_and_closed` | NOT RUN（E1–E3 未批准） |
| 7 | `test_http_api_success_chain.py／TestHTTPSuccessChain／test_full_api_chain_with_nonzero_trades` | FAILED | skipped | success chain runs the real paper-trading vertical, which needs PostgreSQL | `run_tests.sh pg tests/test_http_api_success_chain.py::TestHTTPSuccessChain::test_full_api_chain_with_nonzero_trades` | NOT RUN（E1–E3 未批准） |
| 8 | `test_ledger_reconciliation.py／TestLedgerReconciliation／test_all_positions_closed_after_replay` | ERROR at setup | skipped | ledger over real rows requires a PostgreSQL backend | `run_tests.sh pg tests/test_ledger_reconciliation.py::TestLedgerReconciliation::test_all_positions_closed_after_replay` | NOT RUN（E1–E3 未批准） |
| 9 | `test_ledger_reconciliation.py／TestLedgerReconciliation／test_cash_reconciliation_from_fills` | ERROR at setup | skipped | ledger over real rows requires a PostgreSQL backend | `run_tests.sh pg tests/test_ledger_reconciliation.py::TestLedgerReconciliation::test_cash_reconciliation_from_fills` | NOT RUN（E1–E3 未批准） |
| 10 | `test_ledger_reconciliation.py／TestLedgerReconciliation／test_cross_user_isolation` | ERROR at setup | skipped | ledger over real rows requires a PostgreSQL backend | `run_tests.sh pg tests/test_ledger_reconciliation.py::TestLedgerReconciliation::test_cross_user_isolation` | NOT RUN（E1–E3 未批准） |
| 11 | `test_ledger_reconciliation.py／TestLedgerReconciliation／test_fill_completeness_every_trade_has_entry_and_exit` | ERROR at setup | skipped | ledger over real rows requires a PostgreSQL backend | `run_tests.sh pg tests/test_ledger_reconciliation.py::TestLedgerReconciliation::test_fill_completeness_every_trade_has_entry_and_exit` | NOT RUN（E1–E3 未批准） |
| 12 | `test_ledger_reconciliation.py／TestLedgerReconciliation／test_pnl_matches_hand_calculation` | ERROR at setup | skipped | ledger over real rows requires a PostgreSQL backend | `run_tests.sh pg tests/test_ledger_reconciliation.py::TestLedgerReconciliation::test_pnl_matches_hand_calculation` | NOT RUN（E1–E3 未批准） |
| 13 | `test_owner_verification.py／TestOwnerVerification／test_user_a_can_execute_own_run` | ERROR at setup | skipped | owner verification over real rows requires a PostgreSQL backend | `run_tests.sh pg tests/test_owner_verification.py::TestOwnerVerification::test_user_a_can_execute_own_run` | NOT RUN（E1–E3 未批准） |
| 14 | `test_owner_verification.py／TestOwnerVerification／test_user_b_cannot_execute_user_a_run` | ERROR at setup | skipped | owner verification over real rows requires a PostgreSQL backend | `run_tests.sh pg tests/test_owner_verification.py::TestOwnerVerification::test_user_b_cannot_execute_user_a_run` | NOT RUN（E1–E3 未批准） |
| 15 | `test_real_concurrency.py／TestRealConcurrency／test_barrier_synchronized_concurrent_replay` | ERROR at setup | skipped | concurrent acquisition requires real MVCC and a PostgreSQL backend | `run_tests.sh pg tests/test_real_concurrency.py::TestRealConcurrency::test_barrier_synchronized_concurrent_replay` | NOT RUN（E1–E3 未批准） |
| 16 | `test_regression_contracts.py／TestFailedCommitNoSuccessSignal／test_rollback_does_not_signal_success` | ERROR at setup | skipped | commit/hook contracts run against a verified PostgreSQL test target | `run_tests.sh pg tests/test_regression_contracts.py::TestFailedCommitNoSuccessSignal::test_rollback_does_not_signal_success` | NOT RUN（E1–E3 未批准） |
| 17 | `test_regression_contracts.py／TestFailedCommitNoSuccessSignal／test_successful_commit_signals_success` | ERROR at setup | skipped | commit/hook contracts run against a verified PostgreSQL test target | `run_tests.sh pg tests/test_regression_contracts.py::TestFailedCommitNoSuccessSignal::test_successful_commit_signals_success` | NOT RUN（E1–E3 未批准） |
| 18 | `test_regression_contracts.py／TestReturningNotConsumed／test_update_returning_preserved_through_hook` | ERROR at setup | skipped | commit/hook contracts run against a verified PostgreSQL test target | `run_tests.sh pg tests/test_regression_contracts.py::TestReturningNotConsumed::test_update_returning_preserved_through_hook` | NOT RUN（E1–E3 未批准） |
| 19 | `test_regression_contracts.py／TestSessionHookIsolation／test_hooks_isolated_per_session` | ERROR at setup | skipped | commit/hook contracts run against a verified PostgreSQL test target | `run_tests.sh pg tests/test_regression_contracts.py::TestSessionHookIsolation::test_hooks_isolated_per_session` | NOT RUN（E1–E3 未批准） |
| 20 | `test_regression_contracts.py／TestSourceQuerySelectsFixture／test_historical_query_selects_created_trade` | ERROR at setup | skipped | commit/hook contracts run against a verified PostgreSQL test target | `run_tests.sh pg tests/test_regression_contracts.py::TestSourceQuerySelectsFixture::test_historical_query_selects_created_trade` | NOT RUN（E1–E3 未批准） |
| 21 | `test_replay_status_management.py／TestReplayStatusManagement／test_completed_run_rejected_no_side_effects` | FAILED | skipped | replay row lock and concurrent acquisition require a real PostgreSQL backend | `run_tests.sh pg tests/test_replay_status_management.py::TestReplayStatusManagement::test_completed_run_rejected_no_side_effects` | NOT RUN（E1–E3 未批准） |
| 22 | `test_replay_status_management.py／TestReplayStatusManagement／test_concurrent_replay_only_one_succeeds` | FAILED | skipped | replay row lock and concurrent acquisition require a real PostgreSQL backend | `run_tests.sh pg tests/test_replay_status_management.py::TestReplayStatusManagement::test_concurrent_replay_only_one_succeeds` | NOT RUN（E1–E3 未批准） |
| 23 | `test_replay_status_management.py／TestReplayStatusManagement／test_failed_run_can_be_retried` | FAILED | skipped | replay row lock and concurrent acquisition require a real PostgreSQL backend | `run_tests.sh pg tests/test_replay_status_management.py::TestReplayStatusManagement::test_failed_run_can_be_retried` | NOT RUN（E1–E3 未批准） |
| 24 | `test_replay_status_management.py／TestReplayStatusManagement／test_queued_run_can_be_replayed` | FAILED | skipped | replay row lock and concurrent acquisition require a real PostgreSQL backend | `run_tests.sh pg tests/test_replay_status_management.py::TestReplayStatusManagement::test_queued_run_can_be_replayed` | NOT RUN（E1–E3 未批准） |
| 25 | `test_replay_status_management.py／TestReplayStatusManagement／test_running_run_rejected_no_side_effects` | FAILED | skipped | replay row lock and concurrent acquisition require a real PostgreSQL backend | `run_tests.sh pg tests/test_replay_status_management.py::TestReplayStatusManagement::test_running_run_rejected_no_side_effects` | NOT RUN（E1–E3 未批准） |
| 26 | `test_retry_failure_validation.py／TestRetryAndFailure／test_failed_replay_can_be_retried` | ERROR at setup | skipped | retry and rollback semantics are proven over the real replay worker, which needs PostgreSQL | `run_tests.sh pg tests/test_retry_failure_validation.py::TestRetryAndFailure::test_failed_replay_can_be_retried` | NOT RUN（E1–E3 未批准） |
| 27 | `test_retry_failure_validation.py／TestRetryAndFailure／test_injected_failure_causes_rollback` | ERROR at setup | skipped | retry and rollback semantics are proven over the real replay worker, which needs PostgreSQL | `run_tests.sh pg tests/test_retry_failure_validation.py::TestRetryAndFailure::test_injected_failure_causes_rollback` | NOT RUN（E1–E3 未批准） |
| 28 | `test_retry_failure_validation.py／TestRetryAndFailure／test_retry_after_success_has_no_side_effects` | ERROR at setup | skipped | retry and rollback semantics are proven over the real replay worker, which needs PostgreSQL | `run_tests.sh pg tests/test_retry_failure_validation.py::TestRetryAndFailure::test_retry_after_success_has_no_side_effects` | NOT RUN（E1–E3 未批准） |
| 29 | `test_source_dedup_scope.py／TestSourceDedupScope／test_different_runs_can_use_same_source_trade` | ERROR at setup | skipped | source dedup scope is proven over the real paper-trading vertical, which needs PostgreSQL | `run_tests.sh pg tests/test_source_dedup_scope.py::TestSourceDedupScope::test_different_runs_can_use_same_source_trade` | NOT RUN（E1–E3 未批准） |
| 30 | `test_source_dedup_scope.py／TestSourceDedupScope／test_same_run_cannot_duplicate_source_trade` | ERROR at teardown | skipped | source dedup scope is proven over the real paper-trading vertical, which needs PostgreSQL | `run_tests.sh pg tests/test_source_dedup_scope.py::TestSourceDedupScope::test_same_run_cannot_duplicate_source_trade` | NOT RUN（E1–E3 未批准） |

理由背後的**實測**結構性前提（§11.5 的三項產品碼依賴，非推測）：

| 理由（逐字） | 節點數 | 實測結構性前提 |
|---|---|---|
| formal path needs PostgreSQL: psycopg2 type adaptation and FOR UPDATE row lock | 5 | `FOR UPDATE` @ `paper_trading.py:874`＋`Decimal` bind @ `:74`/`:90` |
| ledger over real rows requires a PostgreSQL backend | 5 | `FOR UPDATE` @ `paper_trading.py:874`＋`Decimal` bind @ `:74` |
| commit/hook contracts run against a verified PostgreSQL test target | 5 | fixture 依設計拒絕非 PG target（`test_regression_contracts.py`） |
| replay row lock and concurrent acquisition require a real PostgreSQL backend | 5 | `FOR UPDATE` @ `paper_trading.py:874`＋真 MVCC |
| retry and rollback semantics are proven over the real replay worker, which needs PostgreSQL | 3 | `FOR UPDATE`＋`Decimal`（replay worker 全路徑） |
| owner verification over real rows requires a PostgreSQL backend | 2 | `FOR UPDATE` @ `paper_trading.py:874`＋`Decimal` bind |
| source dedup scope is proven over the real paper-trading vertical, which needs PostgreSQL | 2 | `Decimal` bind @ `paper_trading.py:74`（`create_portfolio`） |
| NUMERIC exactness and the replay row lock require a PostgreSQL backend | 1 | `NUMERIC` 精確度＋replay `FOR UPDATE` |
| success chain runs the real paper-trading vertical, which needs PostgreSQL | 1 | `FOR UPDATE`＋`Decimal`（POST portfolios→replay 垂直路徑） |
| concurrent acquisition requires real MVCC and a PostgreSQL backend | 1 | 真 MVCC（barrier 併發） |

resolved 中「真的修好」的 22 條（sqlite lane 現況通過，非 skip）：

| 檔 | 條數 |
|---|---|
| `test_learning_api.py` | 6 |
| `test_replay_credibility.py` | 6 |
| `test_position_timing_bugs.py` | 4 |
| `test_api_fixture_lifecycle.py` | 3 |
| `test_fixture_failure_injection.py` | 2 |
| `test_env_entries.py` | 1 |

#### 5. 算術閉合：3402 個 outcome unit 對 3397 個 item（+5 已定因）

摘要行把 47＋3199＋118＋38 加成 **3402**，而同一樹收集 **3397**。差異不是遺漏的節點，而是 pytest 的帳務規則：collection 級的 skip（`pytest.skip(..., allow_module_level=True)`）由 `_pytest/terminal.py:736-742` 计進 `stats["skipped"]` 但**不產生 item、也不寫進度字元**。以進度字元還原兩份日誌（中斷列的前綴一併計入）：

baseline: items(progress chars)=3394 .=3167 F=78 E=66 s=83 | summary units=3399 | units-items=5 (=collection-level skips 5)
fixed: items(progress chars)=3397 .=3199 F=47 E=38 s=113 | summary units=3402 | units-items=5 (=collection-level skips 5)

兩邊都恰好 **+5**，且該 5 就是 repo 內 `allow_module_level` 的 5 支檔：
`tests/test_crypto_bar_harvest_pg.py:18`、`tests/test_crypto_paper_pg.py:28`、`tests/test_ownership_integration.py:48`、`tests/test_quota_concurrency.py:38`、`tests/test_strategy_study_timestamps_pg.py:23`（AST 計 test def 共 **23 條**）。
因此：`units = items + collection-level skips` 在兩份日誌同時成立（3399 = 3394 + 5；3402 = 3397 + 5），先前記為「已量測但未解的 +5」**已閉合**，不需再跑第二次全量。

**同一事實的反面必須記錄（§一：不可用 module skip 一併移走離線覆蓋）**：這 5 支檔以 collection 級 skip 把 23 條 def 全部移出 lane，其中 `test_ownership_integration.py`（10 條）的 skip 條件寫著「refuses a non-PostgreSQL target by design」，`test_quota_concurrency.py`（5 條）則以「連線探測丟任何例外→跳過」為條件（`except Exception` 形態，屬**寬鬆**判定）。這 23 條之內**是否有可在離線驗證的 target-guard／權限拒絕斷言**，本節尚未逐條判定，列為 #150 的必查項：module skip 只能覆蓋「結構上需要 PG」的那部分，其余必須留在 lane。

#### 6. still-bad 85 條的歸屬（不再逐缺陷請示，也不把它當成環境問題）

85 = 47 FAILED ＋ 38 ERROR。38 條 error 的**訊息本身**已分類（只數 `sqlite3.OperationalError` 行以免重複計）：**32 條 `no such function: current_database`**（全在 `test_paper_trading_cancel_atomic.py`，其 32 條一併吻合）＋ **6 條 `no such table: _test_sacrificial_`**（全在 `test_privilege_enforcement.py`）。32＋6＝38，無剩餘未分類 error。

| 檔 | FAILED | ERROR | 計 |
|---|---|---|---|
| `test_paper_trading_cancel_atomic.py` | 0 | 32 | 32 |
| `test_ml_pipeline.py` | 19 | 0 | 19 |
| `test_privilege_enforcement.py` | 1 | 6 | 7 |
| `test_connection_role_verification.py` | 5 | 0 | 5 |
| `test_api_login_with_fixture.py` | 3 | 0 | 3 |
| `test_data_isolation.py` | 3 | 0 | 3 |
| `test_pattern_analysis.py` | 3 | 0 | 3 |
| `test_cleanup_verification.py` | 2 | 0 | 2 |
| `test_fixture_failure_injection.py` | 2 | 0 | 2 |
| `test_target_guard.py` | 2 | 0 | 2 |
| `test_trial_passwordless.py` | 2 | 0 | 2 |
| `test_api_fixture_lifecycle.py` | 1 | 0 | 1 |
| `test_candidate_passwordless.py` | 1 | 0 | 1 |
| `test_journey_b_model_training.py` | 1 | 0 | 1 |
| `test_registration_risk_acknowledgement.py` | 1 | 0 | 1 |
| `test_vertical_path.py` | 1 | 0 | 1 |
| **合計** | **47** | **38** | **85** |

| 歸屬 | 節點 | 本節判定 |
|---|---|---|
| C 群（PG 角色／權限／`current_database()`／sacrificial 表）| `test_paper_trading_cancel_atomic` **32／32**（見下方探針根因）、`test_privilege_enforcement` **7／7**（實測：6 條 setup error `no such table: _test_sacrificial_`，第 7 條 `test_limited_role_cannot_create_table` FAILED 於 `CREATE succeeded`，且 7 條皆斷言 `e.orig.pgcode == '42501'`＝psycopg2 專屬屬性）、`test_connection_role_verification` 5、`test_data_isolation` 3、`test_cleanup_verification` 2、`test_target_guard` 2（**61 條中的 2**）、`test_vertical_path` 1（3 條中的 1）| 尚未套標記（避免在權威測量進行中改樹）→ #150。**勘誤 §11.4 的「1／7」**：該檔 7 條全部是 PG-role 專屬，整檔標記不會減少離線覆蓋；反之 `test_target_guard.py` 61 條中只有 2 條（`current_database()` 探針），**該檔只能 per-test 標**，整檔標記會移走 59 條真實離線斷言。 |
| 程序內污染（§二）| `test_ml_pipeline` 19、`test_journey_b_model_training` 1、`test_pattern_analysis` 3 | 已排除「sklearn／PyJWT 未裝」（§11.6）；待以 minimal-prefix／file-order bisect 定因 → #153 |
| 真失敗（需產品／測試判斷）| `test_api_login_with_fixture` 3、`test_trial_passwordless` 2、`test_candidate_passwordless` 1、`test_api_fixture_lifecycle` 1、`test_fixture_failure_injection` 2、`test_registration_risk_acknowledgement` 1（**9 條，全為 FAILED**）| 逐條根因尚未在本節判定 → §二／#153。**勘誤：先前寫「`test_paper_trading_cancel_atomic` 的 32 條…屬 C 群與污染的混合，需先分開」為不精確，已由實測替換。** |

**38 條 error 的逐條歸屬（本節以腳本重新拆解，非推算）**：固定狀態日誌 `ERROR` 摘要按檔只兩檔—`test_paper_trading_cancel_atomic.py` **32**＋`test_privilege_enforcement.py` **6**＝38＝該 run 的 error 總數，無第四檔。逐一取出每個 `ERROR at setup of …` 段落後的第一條 `E` 行，結果為同質兩類：

| 條數 | setup error 的逐字理由 | 根因位置 |
|---|---|---|
| 32 | `sqlite3.OperationalError: no such function: current_database`（SQL 為 `SELECT current_database(), current_user, inet_server_addr(), inet_server_port()`）| `tests/test_target_guard.py:233-239` `verify_test_target()` **無條件**發出該 PG 專屬四欄探針；`test_paper_trading_cancel_atomic.py:96` 的 `session_factory` fixture 經 `create_verified_session_factory()`（`tests/test_target_guard.py:347`）在 setup 階段呼叫它，故該檔每一條測試在進入本體前即失敗 |
| 6 | `sqlite3.OperationalError: no such table: _test_sacrificial_` | `tests/test_privilege_enforcement.py:17-33` 的 `fixture_data` 直接寫 sacrificial 表，該表只由 `migrations/versions/008_add_sacrificial_test_table.py` 建立；SQLite lane 未跑 migration |

`verify_test_target()` 沒有 SQLite 分支（對照 `tests/conftest.py:100` 有 `_IS_SQLITE` 判斷），這是本節新記錄的**測試層缺陷**而非環境現象：32 條即使有 SQLite 也永遠進不了被測行為。它同時不改變「這 32 條結構上仍需要 PG」的結論—`app/services/paper_trading.py:874` 的 `FOR UPDATE` 與 `:74`/`:90` 的 `Decimal` bind 在 SQLite 無對應語意（§11.4 已量測）—但**標記必須標在探針呼叫點或該檔 per-test**，不能把「函式不存在」當成「需要 PG」的證據寫進帳。

#### 7. PG lane 的 DB 名稱前提互斥（實測；E1–E3 之外新記錄的阻塞點）

**事實一（出現範圍，逐檔計算）**：`ai_investment_lab_test` 出現在 **10 個 tracked `.py` 檔、共 46 處**；放寬到所有 tracked 檔型別為 **15 個檔**（另含 `backend/.env.test` 與 `GATE1_DELIVERY_EVIDENCE.md`、`GATE1_STATUS_REPORT.md`、`GATE1_TEST_BATCH_PROPOSAL.md`、`MIGRATION_COMPATIBILITY_ANALYSIS.md`）。逐檔計數（`git ls-files -z '*.py' | xargs -0 grep -c ai_investment_lab_test`）：

| 檔 | 次數 |
|---|---|
| `tests/test_paper_trading_cancel_atomic.py` | 12 |
| `tests/test_connection_role_verification.py` | 10 |
| `tests/test_safety_gate_negative.py` | 8 |
| `tests/test_target_guard.py` | 7 |
| `tests/test_import_real_data_guard.py` | 2 |
| `tests/test_ownership_integration.py` | 2 |
| `test_safety_gate.py`（backend 根）| 1 |
| `tests/test_api_login_with_fixture.py` | 1 |
| `tests/test_privilege_enforcement.py` | 1 |
| `tests/test_vertical_path.py` | 1 |

其中**直接比較斷言只有 9 處**（其餘為 URL 字串、docstring 或白名單成員）：

```
tests/test_connection_role_verification.py:30,47,63,87,128
tests/test_paper_trading_cancel_atomic.py:1859
tests/test_privilege_enforcement.py:117
tests/test_safety_gate_negative.py:187
tests/test_vertical_path.py:22
```

**事實二（兩套入口規則互斥）**：本 lane 對 PG 有兩個各自獨立的名稱規則——

- `scripts/run_tests.sh:130` 設定 `ALETHRA_TEST_REQUIRED_DB_PREFIX`（預設 `alethra_`），由 `scripts/check_db_env.py:116-120` 在 preflight 檢查；不合規即 `scripts/run_tests.sh:135-138` **exit 3，pytest 根本不啟動**。
- `test_safety_gate.py:25-30` 是四個名稱的**精確白名單**，由 `:215` 檢查；不在名單即 `TestDatabaseGateError`。

以兩個規則**自己的函式**離線計算（未啟動 PostgreSQL、未建 DB、未連 socket；只呼叫 `analyse()` 與 `parse_database_url()`）：

| `ALETHRA_PG_TEST_URL` 的 DB 名 | preflight（prefix `alethra_`）| safety gate 白名單 | 9 條斷言 `== "ai_investment_lab_test"` |
|---|---|---|---|
| `ai_investment_lab_test` | **REJECT**：逐字 `database 'ai_investment_lab_test' does not start with the required disposable prefix 'alethra_'` | PASS | PASS |
| `alethra_test` | OK | PASS | **FAIL** |
| `alethra_owner_test` | OK | PASS | **FAIL** |
| 其他任何 `alethra_*`（例 `alethra_pg_disposable_1`）| OK | **REJECT** | **FAIL** |

**判定**：三欄沒有任何一列同時為綠。能同時通過批准入口（preflight＋gate）的名稱只有 `alethra_test` 與 `alethra_owner_test`，而這兩個名稱必使上述 9 條斷言失敗；改用 `ai_investment_lab_test` 則在 preflight 就被拒（exit 3）。所以**「PG lane 全綠」不是批准 E1–E3 就能達成的狀態**：這是程式層缺陷，屬 #150 範圍，在此記錄一次後繼續執行，不再拆成一輪請示。
修正方向（不在此實作，因權威量測剛結束、需先讓 §11.7 的帳穩定）：讓斷言比對 **lane 自己宣告的邊界**——讀 `ALETHRA_TEST_REQUIRED_DB_PREFIX` 或直接使用 `verify_test_target()` 已解析出的實際 DB 名——而不是把名稱釘死在 legacy 庫。此方向**不放寬任何拒絕條件**：`FORBIDDEN_DATABASES`（`test_safety_gate.py:33-38`）、`ALLOWED_INSTANCES`（`:41-45`）與「不降 target guard」的語意必須維持，且改動要由 `tests/test_safety_gate_negative.py` 逐條鎖住（該檔已有 8 處引用，是現成的驗收點）。

**事實三（同一次離線計算另外發現的兩點，皆未修）**：

1. `test_safety_gate.py:70-71` 只接受 scheme `postgresql`／`postgres`。實測 `postgresql+psycopg2://r:p@localhost:5432/alethra_test` → preflight **OK**、gate **scheme-reject**（SQLAlchemy 慣用的 driver 後綴會在 step 3 拋 `Invalid scheme: postgresql+psycopg2`，即失敗發生在 gate 內而不是任何測試內）。批准 PG 時 `ALETHRA_PG_TEST_URL` 必須刻意使用無後綴形式。
2. `parse_database_url` 的 `parsed.hostname or 'localhost'`（`:75`）與 `parsed.port or 5432`（`:76`）會把**未寫 host 的 unix-socket URL**（實測 `postgresql://r:p@/alethra_test?host=/tmp/…`）填成 `('localhost', 5432)`，step 6 = **PASS**。亦即 step 6 對 socket 連線沒有辨識力——這是該 gate 的既有盲點，本節只記錄，不借此擴大批准範圍。

#### 8. 本節不是什麼

1. **不是 PG 結果**：本 lane 未啟動 PostgreSQL、未建 role/DB、未跑 migration（E1–E3 未批准）；30 條 changed-to-skip 的狀態一律是 **NOT RUN**，不是已執行通過。
2. **不是產品 PASS**：跳過 PG-only 不構成套件通過，全套 sqlite 綠燈亦不構完整平台驗收；`47 failed` 與 `38 errors` 仍在同一帳上。
3. **未改被測程式**：47／3199／118／38 屬於修正前的樹；本節的第 2、3 點是工具與文件變更，其效應只體現在「下一次的日誌可以直接核對」。
4. 本節的 55 條重測（8.56s）是** focused 子集**，不是全量重跑；owner「量測完成前不開另一套重複全量」的限制未被破壞。
5. 第 7 點的矩陣是**離線計算**：只在 lane 的 venv 內呼叫 `scripts/check_db_env.analyse()` 與 `test_safety_gate.parse_database_url()`，未啟動 PostgreSQL、未建 role/DB、未連任何 socket、未跑 migration。它證明的是「兩套名稱規則互斥」，不是任何 PG 執行結果；第 7 點事實三的兩行同樣是純函式輸出。

### 11.8 §二 測試污染：源頭修正、前綴 bisect、前後向回歸，以及另外三類被污染掩蓋的缺陷（追加；不改寫 §11.7）

§11.7 第 6 點留下兩句「待判定」：程序內污染 23 條（ml_pipeline 19＋journey_b 1＋pattern_analysis 3）與「真失敗」9 條。本節把這 32 條全部定因並修正，同時把 85 條 still-bad 收斂成**已修／PG-only／真失敗**三類，算術閉合到 85。所有修正都落在污染源或共享 harness 本身，**沒有**使用改測試順序、加 retry、整檔 skip、conftest 全域清 cache 或刪除斷言。

#### 1. 本節每一筆數字的測量身分

共同身份（每次 run 由 `run_tests.sh` 自行印在日誌前 10 行）：cwd `…/alethra/backend`；HEAD `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（working tree dirty）；lane fingerprint `python 3.12.16 | pytest 8.3.3 | sqlalchemy 2.0.36`；`DATA_PROVIDER=demo`、`TMPDIR=/tmp/alethra-sqlite-test-dir`；layer sqlite；interpreter `backend/.venv/bin/python`。

| # | selection | 結果（日誌逐字） | 原始日誌 | 用途 |
|---|---|---|---|---|
| R1 | `tests/test_ml_pipeline.py` | `54 tests collected` → **54 passed in 28.87s** | `/tmp/mlp_alone.txt` | 19 條不是 ml_pipeline 自己的缺陷 |
| R2 | collection 順序前 57 檔 ＋ `tests/test_ml_pipeline.py`（修正前） | **34 failed, 914 passed, 9 skipped in 420.08s**；其中 `FAILED tests/test_ml_pipeline.py` **19** 條 | `/tmp/mlp_prefix_57.txt` | 前綴存在 ⇒ 19 條出現；這是「有前綴才壞」的直接證據 |
| R3 | `test_explicit_provider_selection.py tests/test_ml_pipeline.py`（修正前） | **18 failed, 41 passed**（5＋54＝59 items） | **未存檔** | 最小前綴＝1 檔污染源 |
| R4 | R3 ＋ `test_provider_isolation.py`（修正前） | **19 failed**（第 19 條為 `unknown location` sklearn 指紋） | **未存檔** | 第二個污染源獨立可辨 |
| R5 | `test_explicit_provider_selection.py`／`test_provider_isolation.py` 各自單獨（修正後） | **5 passed in 2.44s**／**1 passed in 2.30s** | `/tmp/pol_fix_test_explicit_provider_selection.py.txt`、`/tmp/pol_fix_test_provider_isolation.py.txt` | 污染源自身仍通過：修的是接線，不是刪掉測試 |
| R6 | R3（修正後） | **59 passed in 27.36s** | `/tmp/mlp_prefix_1.txt` | 前向回歸（污染源在前）綠 |
| R7 | R4（修正後） | **60 passed in 27.26s** | `/tmp/mlp_prefix_2.txt` | 兩污染源同時在前也綠 |
| R8 | collection 順序前 114 檔 ＋ `tests/test_ml_pipeline.py`（污染修正完成、第 3/4 類 UUID 與 env 修正**之前**） | **17 failed, 1914 passed, 52 skipped in 889.09s**；`FAILED …test_ml_pipeline.py` **0** 條 | `/tmp/mlp_prefix_114.txt` | 污染類已消失；留下的 17 條全部可指名歸屬（第 4 點） |
| R9 | `tests/test_pattern_analysis.py`（修正前） | **3 failed, 11 passed in 4.70s** | `/tmp/alone_test_pattern_analysis.py.txt` | **單獨run即壞** ⇒ §11.7 把它歸入「污染」是錯的，本節改正 |
| R10 | `tests/test_process_state_isolation.py`（5 條版） | **5 passed in 72.98s** | `/tmp/psi_run1.txt` | 前後向回歸與兩條 AST gate 當時即綠 |
| R11 | 9 檔污染批次（R3/R4 兩個最小前綴的受害檔＋learning/scheduler/c1_c4/api_keys/pilot） | **213 passed in 112.32s** | **未存檔** | 批次合測無交互效應 |
| R12 | `test_process_state_isolation.py`(7) ＋ `test_trial_passwordless.py` ＋ `test_candidate_passwordless.py` | **79 passed in 87.43s** | **未存檔** | env 類修正 ＋ 新增兩條 gate 一起綠 |
| R13 | `tests/test_api_fixture_lifecycle.py` ＋ `tests/test_fixture_failure_injection.py` | **8 passed, 1 skipped in 3.57s**（修正前 3 failed, 5 passed, 1 skipped） | **未存檔** | UUID bind 類：正控制由虛轉實 |

**披露**：R3、R4、R11、R12、R13 當時只讀 stdout、未存原始日誌，因此這五筆是**本輪執行記錄中的數字，不是今天可重開的檔案**。它們的效應不靠自身存檔成立：R1/R2/R6/R7/R8/R9/R10 有日誌可重開，且最終狀態由 §11.9 的權威全量日誌涵蓋（全量必然重跑這五筆的全部節點）。這是 §一「不要只交總數」與「原始日誌位置」兩項要求下必須留下的差異，不把它寫成已存檔。

另一筆必須記錄的失敗嘗試：`/tmp/mlp_prefix114.txt`（23:35）試圖跑 114 檔前綴，被入口**拒絕**（`selection on this tree: no tests collected`、`refusing: the selection matched no tests`、RC=2），原因是我把 `test_explicit_provider_selection.py`（在 backend 根）與 `tests/…` 混在同一 selection 卻用錯 cwd。它不是量測結果，不得計入任何帳；真正的 K=114 數值是 R8。

#### 2. 四個污染源與源頭修正

| 類 | 污染源（修正前的程式碼形態） | 受害節點 | 源頭修正 | 位置 |
|---|---|---|---|---|
| A | 於 module import 建立常駐 `create_engine('sqlite:///:memory:')`＋`Base.metadata.create_all`＋`app.dependency_overrides[get_db] = override_get_db`＋`client = TestClient(app)` | ml_pipeline 19（`TestMLAPIEndpoints`／`TestModelOwnership`／`TestInferenceUsageTracking`）| 收進 `_provider_selection_stack()` contextmanager：進入時裝 override、`finally` 以**先前的 dict 內容**還原（`saved_overrides = dict(...)` → `clear()` → `update(saved_overrides)`），不是 `clear()` 了事 | [backend/test_explicit_provider_selection.py:43](file:///Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend/test_explicit_provider_selection.py#L43)、`:65-71`、使用點 `:77`／`:300` |
| B | 於 module import 把 7 個空 `sklearn.*` 塞進 `sys.modules`（註解寫「Mock sklearn to avoid import errors」，但 scikit-learn 1.9.1 已安裝） | journey_b 1＋（R2 的第 19 條）| 刪除 stub，改用真正的套件；engine 移入 `_fresh_session()`，exit 時 `db.close()`＋`engine.dispose()` | [backend/test_provider_isolation.py](file:///Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend/test_provider_isolation.py)（diff 顯示 7 行 `sys.modules[…]` 全數移除） |
| C | 被測產品之外的**環境**污染：`app/config.py:428` `ConfigDict(env_file=".env")` 使 `backend/.env:12-14`（`CANDIDATE_PASSWORDLESS_MODE=TRUE`＋兩組帳號＋60 分鐘）自動併入**任何**裸 `Settings(**values)`；而 candidate validator 先於 trial 執行（`app/config.py:158`→`:159`），所以 trial 的 gate 實際由 candidate 的旗標回答 | trial 2＋candidate 1 | 測試改用本 repo 已有的 hermetic 慣例 `Settings(_env_file=None, **values)`（先例 `tests/test_config.py:14`），並由 AST gate 拒絕「`Settings(...)` 呼叫未帶 `_env_file`」的形態，使此類不可能再被悄悄引入 | [backend/tests/test_trial_passwordless.py:47](file:///Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend/tests/test_trial_passwordless.py#L47)、`:143`；[backend/tests/test_candidate_passwordless.py:44](file:///Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend/tests/test_candidate_passwordless.py#L44)、`:165` |
| D | 共享 harness `tests/api_test_fixtures.py` 用裸 `text()` 綁 UUID：SQLite 端 `postgresql.UUID(as_uuid=True)` 以 **32-hex 小寫**儲存（實測 `5d16ecee37b8…`），而 `str(uuid4())` 是 36 字元含連字號 ⇒ `WHERE id = :user_id` **永不匹配** | api_fixture_lifecycle 1＋fixture_failure_injection 2，以及**被掩蓋的正控制**（`assert count == 1` 之前觀察的是「查不到」而不是「清乾淨」） | `_uuid_sql(sql, *bind_names)`：`bindparam(name, type_=UUID(as_uuid=True))`，值改傳 `uuid.UUID`；此寫法在兩個 dialect 都正確（實測：`ProgrammingError` 只出現在未型別化的 `uuid.UUID` bind） | [backend/tests/api_test_fixtures.py:18](file:///Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend/tests/api_test_fixtures.py#L18)、`_uid_sql` `:260`、`:277` |

另有一批**同形態但較小**的程序狀態外洩，各自在其 owner module 內以 teardown 修正，未集中到 conftest（集中清理正是 §二 禁止的「全域清 cache」形態）：

1. `get_settings` 是 `lru_cache`，因此是程序狀態而非測試狀態 → [backend/tests/test_providers.py:8](file:///Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend/tests/test_providers.py#L8) 的 autouse fixture 在進入與退出各 `cache_clear()` 一次。**這與「全域清 cache 繞過」的區別**：它只包在讀 cache 的該模組周圍、前後都清，因此它還原的是自己污染的狀態；「全域清 cache 繞過」指在 conftest 無條件清空以掩蓋別人留下的髒值，本輪未採那種形態。該檔 4 條測試原本的 `cache_clear()`（`:218/:234/:244/:249`）保留，因為它們測的是 cache 行為本身。
2. `_PROVIDERS` 直接 `del` → `try/finally` ＋ `pop(…, None)`；失敗時不再留下註冊項。
3. 共享 `asyncio` event loop 常駐 → module-scoped autouse `_close_shared_loop()`（candidate `:58`、trial `:61`）。
4. APScheduler 執行緒在斷言失敗時不被 `shutdown` → 斷言移入 `try`、`shutdown(wait=False)` 放 `finally`（`tests/test_learning_scheduler.py`）。
5. `TestClient`／engine／臨時 DB 目錄在 module 結束後仍佔用 → module-scoped autouse `_release_module_harness`（close、`engine.dispose()`、`shutil.rmtree(..., ignore_errors=True)`，`tests/test_c1_c4_safety_cases.py`）。
6. module import 期間寫 `os.environ["DATABASE_URL"] = "sqlite:///./test.db"`（`tests/test_api_keys.py`、`tests/test_pilot.py`）—該值在整個 session 內汙染之後任何讀 `DATABASE_URL` 的程式 → 整行刪除（兩檔已無其他 `os.` 用法，`import os` 一併移除）。
7. module import 期間 `sys.path.insert(...)`（`tests/test_safety_gate_negative.py` 模組層＋4 段被生成的 `-c` 片段，片段內 `os.path.abspath('__file__')` 本身也是錯用）→ 移除；`tests/test_e2e_shared_metadata.py`、`tests/test_candidate_identity.py`、`tests/test_env_entries.py` 需要 scripts 目錄者改為 **import 後立即 `sys.path.remove(...)`**，由新 gate 檔的 `:203` 強制配對。
8. `backend/tests/test_paper_trading_cancel_atomic.py` 的 `sys.path.insert(0, os.path.dirname(__file__))` 被我當作冗餘刪除，實際是**載重**的：它支撐該檔 **27** 條裸 sibling import（`from test_target_guard import …`、`from test_cleanup_utils import …`，兩個模組都在 `tests/` 內，而 `tests/__init__.py` 存在 ⇒ pytest 只把 `backend` 插入 `sys.path`）。後果由權威量測的第一次嘗試抓出（§11.9 attempt A：`ModuleNotFoundError: No module named 'test_target_guard'`、RC=2）。源頭修正是把 27 條改成套件限定形態 `from tests.test_target_guard import …`／`from tests.test_cleanup_utils import …`（腳本化改寫，`rewritten_import_lines = 27`、殘留裸 import 0、`py_compile` OK、`--collect-only` **53 tests collected**），而不是把 `sys.path` 洩漏放回原位——後者會被自己的 gate（`:203`）拒絕，而且會讓同一模組以 `test_target_guard` 與 `tests.test_target_guard` 兩個身份各存一份狀態。

#### 3. 「不靠改測試順序掩蓋洩漏」的可執行證據

`tests/test_process_state_isolation.py` 現有 **7** 條，全部在 lane 內以 `subprocess` 呼叫 `scripts/run_tests.sh` 自己跑前綴，因此回歸是**可執行**的，不是文件串：

| def | 行 | 性質 |
|---|---|---|
| `test_polluter_first_order_leaves_the_api_victims_passing` | `:94` | 前向：污染源排在前，受害節點必須綠（R3 的最小前綴） |
| `test_sklearn_users_import_the_installed_package_after_the_stub_module` | `:108` | 前向：B 類；並斷言 `unknown location` 不得出現在日誌 |
| `test_reverse_order_also_passes` | `:119` | **反向**：受害在先、污染源在後（§二 明定要「failed set 與 reverse order」兩向） |
| `test_the_stack_restores_the_override_dict_even_when_the_body_raises` | `:130` | teardown 在 `finally`、且還原**先前 dict** 而非 `clear()`；本體故意拋錯 |
| `test_no_test_module_writes_shared_state_at_import` | `:152` | AST gate：整個 `backend/` 的 module 層 `…dependency_overrides[…] =`／`sys.modules[…] =`／`environ[…] =` 一律拒絕 |
| `test_no_test_module_leaves_a_directory_on_sys_path` | `:203` | AST gate：module 層 `sys.path` insert/append 必須有配對 remove |
| `test_the_environment_the_suite_measures_is_the_environment_it_documents` | `:241` | AST gate：任何缺 `_env_file` keyword 的 `Settings(...)` 呼叫節點一律拒絕 |

這條 gate 的設計過程本身有一筆必須記錄的教訓：我第一版把 `get_settings()` 與 `Settings.Config` 也列入拒絕對象，而那**不可能通過**——`get_settings()` 不接受 `_env_file` keyword。依「gate 必須真的能綠」的既有規範，範圍收斂為「字面 `Settings(...)` 呼叫」；同時第一次實作把 `_env_file=None` 藏在 values dict 裡，AST 看不到 keyword，因此 R12 首跑是 **1 failed**（正是這條 gate 抓到自己的盲點），改寫成 keyword 形態後才是 R12 的 79 passed。

#### 4. §11.7 第 6 點 85 條 still-bad 的最終歸屬（29 已修 ＋ 55 PG-only ＋ 1 真失敗 ＝ 85）

| 歸屬 | 條數 | 細分（實測） |
|---|---|---|
| **已修** | **29** | A 類 ml_pipeline 19 ＋ B 類 journey_b 1 ＋ 契約不符 pattern_analysis 3（R9 證明單獨亦壞，非污染）＋ D 類 UUID 3（lifecycle 1／failure_injection 2）＋ C 類 env 3（trial 2／candidate 1） |
| **PG-only（待 #150 標記）** | **55** | 38 條 ERROR：cancel_atomic 32（`no such function: current_database`）＋ privilege_enforcement 6（`no such table: _test_sacrificial_`）；17 條 FAILED：connection_role_verification 5、data_isolation 3、api_login_with_fixture 3、cleanup_verification 2、target_guard 2、privilege_enforcement 1、vertical_path 1。38＋17＝55，與 R8 之後仍壞的集合一致 |
| **真失敗（不得向測試靠攏）** | **1** | `test_registration_risk_acknowledgement.py:89` `assert 'unknown' == 'human'`：這是 provenance 不變量衝突（註冊來源的實際值與測試假設值不一致）。本輪**不修**，並記錄：把產品寫成 `human` 或把斷言放寬成 `unknown` 都屬於 §三 的來源判定，需 owner 裁決，不屬於污染範圍，因此不列入「已修」 |

R8 的 17 條即上表「PG-only 17」減去尚未修的 C/D 類 6 條，再加 lifecycle 1 條 —逐檔對照為：`api_fixture_lifecycle 1`、`api_login_with_fixture 3`、`candidate_passwordless 1`、`cleanup_verification 2`、`connection_role_verification 5`、`data_isolation 3`、`fixture_failure_injection 2` ＝ **17**，與日誌逐字一致。

#### 5. #150 標記範圍的最終清單（由本節新增的兩條實測限制收斂）

| 檔 | 該檔 items | 需標 | 標記形態 | 依據 |
|---|---|---|---|---|
| `test_paper_trading_cancel_atomic.py` | **53**（§11.8 實測 collect-only） | 32 | **只能標在探針呼叫點或 per-test** | 32 條因 `verify_test_target()` 無 SQLite 分支而死在 setup；**其餘 21 條在 SQLite 是真的斷言**，整檔標記會移走 21 條離線覆蓋（§11.7 第 6 點的「1／7」勘誤屬 `test_privilege_enforcement.py`，兩檔教訓相反，必須分開處理） |
| `test_privilege_enforcement.py` | 7 | 7 | 整檔（module-level） | 7 條全為 PG-role 專屬：6 條 `no such table: _test_sacrificial_`、第 7 條斷言 `e.orig.pgcode == '42501'`（psycopg2 專屬屬性） |
| `test_connection_role_verification.py` | 5 | 5 | 整檔 | 斷言 `current_user`／`current_database()` |
| `test_data_isolation.py` | 3 | 3 | 整檔 | `_test_sacrificial_` 表 |
| `test_cleanup_verification.py` | 2 | 2 | 整檔 | 同上 |
| `test_target_guard.py` | 61 | 2 | **只能 per-test** | 其餘 59 條是離線 target-guard 覆蓋，§一 明文「不可用 module skip 一併移走」 |
| `test_vertical_path.py` | 3 | 1 | **只能 per-test** | `test_safety_gate_enforced` |
| `test_api_login_with_fixture.py` | — | 3 | per-test | 產品 raw SQL 的 `Decimal` bind（`app/services/paper_trading.py:74`、`:90`）＋ dashed-UUID 比對在 SQLite 無對應語意 |

仍在 #150 範圍、本節未解決的兩件事：(a) 5 支 `allow_module_level` skip 檔背後的 **23** 條 def 逐條判定，結果見下面第 6 點；(b) §11.7 第 7 點的 **9** 處 DB 名稱斷言與兩套入口規則互斥。兩者都不准以放寬 `FORBIDDEN_DATABASES`／`ALLOWED_INSTANCES` 或降 target guard 作為修法。

#### 6. §一 硬性要求的 23 條 module-skip def 逐條判定（本節以產品碼與 def 行號判定，非推算）

| 檔（skip 條件形態） | def | 結構性 PG 前提（實測位置） | 判定 |
|---|---|---|---|
| `test_crypto_bar_harvest_pg.py`（`ALETHRA_TEST_DB` 名稱前綴） | 3 | 產品碼用 PG dialect upsert：[backend/app/services/crypto_bar_harvest.py:28](file:///Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend/app/services/crypto_bar_harvest.py#L28) `from sqlalchemy.dialects.postgresql import insert as pg_insert`、`:83-84` `on_conflict_do_nothing(...)` | module skip **成立** |
| `test_crypto_paper_pg.py`（名稱前綴） | 2 | `threading`＋`Thread(` 競爭 `submit_manual_order`，需要真 MVCC 行鎖 | module skip **成立** |
| `test_strategy_study_timestamps_pg.py`（名稱前綴） | 3 | fixture `SET TIME ZONE`（SQLite 無 session TimeZone） | module skip **成立** |
| `test_ownership_integration.py`（`sqlite → skip`，其余非 PG 目標仍 raise） | 10 | 其中 **8** 條經 `_create_portfolio_via_api` → `POST /api/v1/paper-trading/create` → `Decimal` bind（`app/services/paper_trading.py:74`、`:90`）＝結構性 PG；**2** 條不需要該垂直路徑：`test_nonexistent_run_returns_404_not_500`（`:175`）與 `test_verify_run_ownership_raises_for_nonexistent`（`:259`），兩者只用 `_make_user` ＋ 一個不存在 run id | **module skip 過度**：這 2 條正是 §一 不可一併移走的「離線可驗權限拒絕／錯誤狀態」覆蓋 → #150 需把它們從 module skip 下放為可跑節點（其餘 8 條 per-test 標） |
| `test_quota_concurrency.py`（連線探測 `except Exception → skip`，寬鬆） | 5 | `pg_engine`＋14 處 `threading`／`Thread(` 競爭 `reserve_quota`＝結構性 PG | module skip 對 4 條成立；另需修兩處**被 skip 掩蓋的真實缺陷**：(i) [backend/tests/test_quota_concurrency.py:59](file:///Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend/tests/test_quota_concurrency.py#L59) `def test_user_with_limit(...)` 內含 `yield`，它是**以 `test_` 命名的 fixture**，既被 collection 成 item（`:95`、`:144`、`:201`、`:284` 又把它當 fixture 參數使用）；(ii) import 期的寬鬆 `except Exception` 讓「PG 配置錯誤」與「PG 不存在」都顯示成 skip，違反 fail-closed 語意 |

`test_ownership_integration.py` 的 2 條另有一筆必須同時記錄的**虛通風險**：「nonexistent run」在 UUID bind 形態錯誤時（D 類，見第 2 點）同樣回傳 not-found，因此斷言可能觀察的是綁定失敗而非所有權檢查。#150 若把它們放回 lane，必須並附一條**正控制**（同一查詢以真實存在的 run id 應回傳 200／不拋錯），否則該 2 條只是把虛通從 PG lane 搬進 SQLite lane。


#### 6. 本節不是什麼

1. **不是權威量測**：本節全部是 focused 子集與前綴 run；固定狀態的權威全量在 §11.9（第一次嘗試因本節第 2 點第 8 項的自引入 collection 缺陷而被入口拒絕，RC=2，未產出任何節點結果）。
2. **不是 PG 結果**：未啟動 PostgreSQL、未建 role/DB、未跑 migration、未啟動服務、未 commit/push；55 條 PG-only 節點的狀態仍是 **NOT RUN**，把它們標成 skip 不構成任何執行證據。
3. **不是產品 PASS**：29 條修的是測試／harness／環境依賴，1 條真失敗仍開放；「全套 SQLite 綠」不等於平台驗收。
4. **未使用被禁止的繞道**：未改 collection 順序、未加 retry、未新增整檔 skip、未在 conftest 全域清 cache、未刪除任何斷言。反向回歸由 gate 自己跑子程序，不靠調整檔名或 `-p` 參數達成。
5. 五筆未存檔數字（R3/R4/R11/R12/R13）已在第 1 點如實標示，不後補路徑、不寫成已存檔；它們的節點由 §11.9 全量日誌重新覆蓋。

---

### 11.9 固定狀態權威全量帳（§一 要求逐項交付；追加，不改寫 §11.7／§11.8）

本節是「目前 authoritative full-suite」那一次量測的完整帳，不是第二次、也不是重跑。§11.8 的 focused／前綴數字全部被本節的同一棵樹重新涵蓋。

#### 1. 測量身分（每一欄都可重算，命令為逐字保存而非重組）

| 項目 | 實測值 |
|---|---|
| 命令（逐字，來自 shell 記錄）| `TMPDIR=/tmp/alethra-sqlite-test-dir DATA_PROVIDER=demo bash ./backend/scripts/run_tests.sh sqlite -q -p no:warnings --tb=line -rfE > /tmp/full_sqlite_20261117b.txt 2>&1; echo "FULL_SUITE_EXIT=$?" >> …; date -u +"run_end_utc=…" >> …` |
| 實際 pytest argv | `.venv/bin/python -m pytest -q -p no:warnings --tb=line -rfE -rfEs`（`ps` 直讀；`-rfEs` 由 `run_tests.sh:257` 附加，即 §11.7 第 2 點的修正生效）|
| 啟動 cwd | `…/alethra`（腳本 `:217` 再 `cd "$BACKEND"`）；日誌 `cwd:` 欄為 `/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend` |
| HEAD | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（日誌 `git:` 欄自行印出）|
| dirty diff 身分 | `git status --porcelain` **110 條路徑**，存 `/tmp/fullrun17b_dirty_before.txt`；執行結束後重測存 `/tmp/fullrun17b_dirty_after.txt`，`diff` **完全相同**（無新增路徑、無刪除、無狀態字元改變）|
| python 樹摘要（可重算）| `bash /tmp/tree_digest.sh` → `sha256` of（每個 `.py` 一行 `<sha256>  <repo 相對路徑>`，`LC_ALL=C` 依路徑排序，整份再 sha256；排除 `backend/.venv/`、gitignore 的 `.local/`（本機 PG16＋工具 venv，816M）、`node_modules`、`.git`）＝**`a015292ccf9df01917cf4ae61bd220d8b0dc8ef11870f812f4746a7fe6cc1c42`，files=502**。**00:49（執行中）與 01:05（執行結束後、任何編輯之前）兩次測得同一值**；另有 `find … ! -path './backend/.venv/*' ! -path './.local/*' -name '*.py' -newermt '2026-11-17 00:33:00'` 回報 **0 個檔案** ⇒ 00:33 啟動至 01:05 結束之間被測程式與測試內容未被改動。**這是固定版本結果，不是混合狀態結果。** |
| 摘要工具的依賴 | 只讀日誌與 `shasum`／`find`，不在被測樹內（`/tmp/suite_ledger.py`、`/tmp/tree_digest.sh`）|
| runner fingerprint | `python 3.12.16 \| pytest 8.3.3 \| sqlalchemy 2.0.36`（日誌印出，非路徑推測）|
| interpreter | `/Users/…/alethra/backend/.venv/bin/python`（未落回系統 python）|
| provider 設定 | `DATA_PROVIDER=demo`（離線、無付費外呼）；`JWT_SECRET_KEY` 由 lane 每次生成 |
| TMPDIR／DB | `TMPDIR=/tmp/alethra-sqlite-test-dir`；`preflight ok: sqlite: /tmp/alethra-sqlite-test-dir/alethra-sqlite-test-33387.db`（每 PID 新檔；pid 33387＝本次 pytest 行程）|
| 收集數（同一棵樹、同一腳本）| 日誌 `selection on this tree: 3415 tests collected in 2.78s` |
| **結果** | **19 failed, 3245 passed, 118 skipped, 38 errors in 1893.59s (0:31:33)**；deselected **0**（全日誌 `grep -c deselected` ＝ 0）|
| exit code | `FULL_SUITE_EXIT=1`（pytest 1＝有失敗節點；非工具錯誤。腳本跑綠才會是 0）|
| 執行時間與結束戳 | 1893.59s；`run_end_utc=2026-11-16T17:05:11Z`（＝HKT 01:05:11）；啟動 00:33 |
| 原始日誌 | `/tmp/full_sqlite_20261117b.txt`（58,304 bytes，mtime 11-17 01:05，**未覆寫、未刪除**）|
| 派生帳本 | `/tmp/ledger_17b.txt`（26,522 bytes）＝ `python3 /tmp/suite_ledger.py /tmp/full_sqlite_20261117b.txt /tmp/inventory_20261117b.txt` 的輸出（身分／摘要／逐檔 bad／118 條 skip 理由／進度字元算術／節點級 bad 清單／對 inventory 反查）|
| 節點清單 | `/tmp/inventory_20261117b.txt`（339,606 bytes，修正 import 後重跑的 `--collect-only`，3415 條 id）|

**第一次嘗試（attempt A）必須記為拒絕，不是量測**：`/tmp/full_sqlite_20261117a_REFUSED_collect_error.txt`（4,322 bytes）印 `selection on this tree: 3362 tests collected, 1 error in 3.23s`、`ImportError while importing test module '…/tests/test_paper_trading_cancel_atomic.py'`、`ModuleNotFoundError: No module named 'test_target_guard'`、`Interrupted: 1 error during collection`、`5 skipped, 1 error in 2.87s`、`FULL_SUITE_EXIT=2`、`run_end_utc=2026-11-16T16:30:43Z`。它**沒有任何節點結果**，因此不得計入任何對帳。成因是本輪之前我自己移除 `sys.path.insert(0, os.path.dirname(__file__))` 時，讓該檔 27 條「同目錄裸 import」失去來源；修法是在源頭把它們改為套件限定 `from tests.test_target_guard import …`／`from tests.test_cleanup_utils import …`（不恢復那條 sys.path 洩漏——我自己寫的 gate `test_process_state_isolation.py:203` 會拒絕它），改後 `--collect-only` 實測 53 tests collected、`py_compile` 通過。

#### 2. 算術閉合：3420 個 outcome unit ＝ 3415 個 item ＋ 5 個 module-level skip

| 來源 | 值 |
|---|---|
| 進度字元（`-q` 本身即記錄）| `.=3245  F=19  s=113  E=38` → **items=3415** |
| 腳本自印收集數 | `3415 tests collected` → **完全相等，差 0** |
| 摘要 unit | 19＋3245＋118＋38 = **3420** |
| `units − items` | **+5** |
| 那 5 個的身分 | 5 支 `allow_module_level` skip 檔：`test_crypto_bar_harvest_pg.py:16`、`test_crypto_paper_pg.py:26`、`test_ownership_integration.py:45`、`test_quota_concurrency.py:38`、`test_strategy_study_timestamps_pg.py:21`（日誌各印 1 條 SKIPPED，理由為 module 級字串）|
| 獨立證明它們貢獻 0 item | 對這 5 檔單獨跑 `--collect-only`：4 檔列在 short summary 的 SKIPPED、第 5 檔因我這次直呼未設 `ALETHRA_TEST_DB` 而依設計 `raise RuntimeError("SAFETY: ALETHRA_TEST_DB environment variable is required…")`（`test_ownership_integration.py:82`），最後一行是 **`no tests collected, 1 error`** ⇒ 5 檔 0 item，22 條 def（3＋2＋10＋4＋3）不在 3415 之內 |

同一條 identity 在 §11.7（3402＝3397＋5）與本節（3420＝3415＋5）都成立，差值同為 5，說明這 5 個 module-level skip 在两棵樹之間沒有變動。

#### 3. 57 條 bad 節點的逐檔與逐節點明細（不是總數）

| 檔 | items | FAILED | ERROR | bad 節點的實測成因（日誌 `--tb=line` 單行回溯逐字） |
|---|---|---|---|---|
| `tests/test_paper_trading_cancel_atomic.py` | **53** | 1 | **32** | 32 條為 setup 錯誤：`sqlite3.OperationalError … no such function: current_database`（`session_factory`→`create_verified_session_factory`→`verify_test_target` 的探針是 PG 專屬）。1 條 FAILED 為 `TestTargetGuard::test_get_test_db_url_fails_closed`：`test_paper_trading_cancel_atomic.py:1917: NameError: name 'os' is not defined`——**本輪引入的回歸，見第 5 點**，不是 PG-only |
| `tests/test_privilege_enforcement.py` | 7 | 1 | 6 | 6 條 `sqlite3.OperationalError … no such table: _test_sacrificial_`；1 條 `:94: Failed: Expected ProgrammingError with SQLSTATE 42501, but CREATE succeeded`（SQLite 無 role 概念）|
| `tests/test_connection_role_verification.py` | 5 | 5 | 0 | 4 條 `no such column: current_user`；1 條 `:82: assert 503 == 200` |
| `tests/test_api_login_with_fixture.py` | — | 3 | 0 | `Error binding parameter 3: type 'decimal.Decimal' is not supported`（產品 `paper_trading.py:74`/`:90` 的 raw INSERT 綁 Decimal）＋ `assert 500 == 200` |
| `tests/test_data_isolation.py` | 3 | 3 | 0 | `no such table: _test_sacrificial_` |
| `tests/test_cleanup_verification.py` | 2 | 2 | 0 | `no such table: _test_sacrificial_` |
| `tests/test_target_guard.py` | 61 | 2 | 0 | `:1197: Failed: Got OperationalError (connection failure) instead of TargetGuardError (guard rejection): … no such function: current_database`；`sqlalchemy.exc.ArgumentError: Invalid SQLite URL: sqlite://user:***@//tmp/…db`（憑證洩漏偵測路徑把 SQLite URL 當 PG Shape 解析）|
| `tests/test_vertical_path.py` | 3 | 1 | 0 | `sqlalchemy.exc…`（safety gate 的 `current_database()`／`current_user` 斷言）|
| `tests/test_registration_risk_acknowledgement.py` | — | 1 | 0 | `:89: AssertionError: assert 'unknown' == 'human'`——provenance 衝突，**不得向測試靠攏**（§11.8 第 4 點第 3 列）|
| **合計** | | **19** | **38** | 57 條全部能在 `/tmp/inventory_20261117b.txt` 的 3415 條 id 中找到（ledger 第 7 節：**bad nodes absent from inventory = 0**），無幽靈節點、無重新命名造成的假象 |

#### 4. failed→fixed 與 failed→PG-only 分開對帳（§七 第 3 點要求）

以 §11.7 的 85 條 still-bad 為起點、本節 57 條為終點，逐類對帳：

| 類 | 條數 | 由 → 到 | 證據 |
|---|---|---|---|
| **failed→fixed** | **29** | 47 條 FAILED 中的 29 條 → 現在 PASSED | 程序內污染 19（`test_ml_pipeline.py`，§11.8 第 2 點 A 類；本節日誌中該檔 **0 條** bad）＋ journey_b 1 ＋ 契約不符 pattern_analysis 3（R9 證明單獨亦壞）＋ UUID bind 3（lifecycle 1／failure_injection 2）＋ env 3（trial 2／candidate 1）。逐檔在日誌中已不在 bad 清單 |
| **failed→PG-only（仍 NOT RUN）** | **55** | 38 條 ERROR ＋ 17 條 FAILED → **狀態不變**（尚未套 skip 標記，#150 範圍）| 17 條 FAILED ＝ connection_role 5、data_isolation 3、api_login 3、cleanup 2、target_guard 2、privilege 1、vertical_path 1；38 條 ERROR ＝ cancel_atomic 32＋privilege 6。與 §11.8 第 4 點逐檔一致，**沒有新增也沒有消失** |
| **真失敗（開放）** | **1** | `test_registration_risk_acknowledgement.py:89` 前＝後 | `'unknown' == 'human'`；修哪一邊屬 owner 的來源判定，本輪不修 |
| **本輪自己引入（回歸）** | **1** | 前＝PASS → 後＝FAILED | `TestTargetGuard::test_get_test_db_url_fails_closed`，成因見第 5 點；**不列入 PG-only，也不列入已修**（修正在量測之後，屬另一筆帳）|
| 新增節點 | **+18 items** | 3397 → 3415 | ＝ §11.8 第 1 點披露的節點帳（`test_process_state_isolation.py` 5→7、`test_candidate_passwordless.py` TestDisabledByDefault 3→4、`test_lane_entry_contract.py` 新增等；減去 1 條被改名移走的 node）|

**閉合檢核（加法必須自己成立）**：
- FAILED：47 − 29（fixed）＋ 1（本輪回歸）= **19** ✔ 與日誌逐字相同
- ERROR：38 ＋ 0 = **38** ✔
- PASSED：3199 ＋ 29（fixed）＋ 18（新增節點）− 1（本輪回歸）= **3245** ✔
- SKIPPED：118 ＋ 0 = **118** ✔
- unit：3402 ＋ 18 = **3420** ✔（＝19＋3245＋118＋38）
五個方向全部對齊，沒有任何一欄需要靠「推定」补齊。

#### 5. 本輪引入的那一條回歸，與它的源頭修正（不藏）

- **現場**：`tests/test_paper_trading_cancel_atomic.py:1917` 使用 `os.environ.pop("ALETHRA_PG_TEST_URL", …)`，但模組頂層**沒有 `import os`**。
- **成因（由 `git diff HEAD` 逐字重建）**：髒 diff 同時刪掉兩行——`-import os` 與 `-sys.path.insert(0, os.path.dirname(__file__))`。刪掉 sys.path 洩漏是正確的（它是 §11.8 第 2 點第 8 項那個自引入 collection 缺陷的來源），但 `import os` 是被連帶刪掉的**承重-wall**：該檔另 2 處 `os.` 使用仍在。
- **為何之前看不見**：在 §11.7 的樹裡，這一節點所屬的 class 因裸 import 而在 collection／setup 階段就死掉，其 body 從未執行到 `os.environ`；修正 import 之後 body 才第一次被跑到，於是暴露出第二個更早就存在的缺口。教訓：**修掉一個遮蔽層，會露出下一層**，所以權威量測必須在修正之後重跑，不能沿用子集結論。
- **修正與實測**：把 `import os` 加回 stdlib import 區塊（`tests/test_paper_trading_cancel_atomic.py:33` 之前，未動任何其他行）。修正前對照＝本節日誌的 1 條 FAILED；修正後實測 `run_tests.sh sqlite tests/test_paper_trading_cancel_atomic.py::TestTargetGuard -q -p no:warnings --tb=line -rfE` → **`9 passed in 1.57s`，RC=0**（同一棵樹，改後 `bash /tmp/tree_digest.sh` ＝ `9a6866f1968c694b63626c573dd8e7f70a82b417fe5c72b29d47794d5a857105`，files 仍 502、dirty 路徑仍同一 110 條）。
- **同一類缺陷的連帶搜尋（實測，非推測）**：對所有髒 `.py` 檢查「diff 刪了 `import os` 但檔內仍使用 `os.`」→ 只有本檔命中（`import_os_now=0 uses_os=2`）；另外 4 檔（`test_explicit_provider_selection.py`、`test_provider_isolation.py`、`tests/test_api_keys.py`、`tests/test_pilot.py`）同樣刪了 `import os`，但 `uses_os=0`，屬乾淨刪除。

#### 6. 118 條 skip 的逐條理由與 changed-to-skip 閉合

`-rfEs` 修正後，**每一條 skip 的逐字理由第一次直接存在於權威日誌裡**（§11.7 時代的全量日誌印出 0 條）。分組（25 種字串理由、28 支檔案）：

| 條數 | 逐字理由（截斷至 150 字元） |
|---|---|
| 24 | `requires a disposable PostgreSQL database (ALETHRA_PG_DATABASE_URL)` |
| 18 | `live-stack probe: ALETHRA_LIVE_PROBES is not set to 1: these probes write bars, sessions and paper orders into whatever database the API uses, so they only run on an explicitly provided disposable sta` |
| 15 | `requires a disposable PostgreSQL database (ALETHRA_PG_DATABASE_URL); the intraday per-bar replay must be proven on PostgreSQL, not SQLite` |
| 11 | `row-level concurrency requires PostgreSQL (set ALETHRA_TEST_DB)` |
| 5 | `formal path needs PostgreSQL: psycopg2 type adaptation and FOR UPDATE row lock` |
| 5 | `ledger over real rows requires a PostgreSQL backend` |
| 5 | `commit/hook contracts run against a verified PostgreSQL test target` |
| 5 | `replay row lock and concurrent acquisition require a real PostgreSQL backend` |
| 5 | `ALETHRA_PG_TEST_URL not set or doesn't use socket connection` |
| 4 | `row-lock serialisation requires a real PostgreSQL backend` |
| 3 | `PostgreSQL not configured (run via scripts/run_tests.sh pg)` |
| 3 | `retry and rollback semantics are proven over the real replay worker, which needs PostgreSQL` |
| 2 | `owner verification over real rows requires a PostgreSQL backend` |
| 2 | `source dedup scope is proven over the real paper-trading vertical, which needs PostgreSQL` |
| 1 ×10 | `…this module refuses a non-PostgreSQL target by design`／`PostgreSQL not available (set ALETHRA_TEST_POSTGRES_URL)`／`NUMERIC exactness and the replay row lock…`／`success chain runs the real paper-trading vertical…`／`row locks are only enforced by PostgreSQL; SQLite cannot show them`／`concurrent acquisition requires real MVCC…`／`ALETHRA_PG_TEST_URL not set`／`…doesn't contain expected DB name`／`…has no query params`／`atomicity under contention is a PostgreSQL claim…` |
| **1** | **`BLOCKED: Cannot intercept cleanup commit without complex mocking`**（`tests/test_fixture_failure_injection.py:178`）——**不是 PG 前提**，是一條自承的 mock 缺口；見第 7 點 |
| **合計** | **118** |

**changed-to-skip 節點逐條（§一 第二項要求：原先結果／具體 PG 前提／現在 skip 原因／對應 PG 命令／仍未執行狀態）**：本輪標記的閉合是 **30 條**，即 §11.4 表的 A 群 25 ＋ B 群 5；每一條在權威日誌都有對應的逐字 SKIPPED 行：

| # | 節點（檔:行） | 原先結果（§11.4 實測） | PG 前提 | 現在 skip 理由（日誌逐字） | 仍未執行 |
|---|---|---|---|---|---|
| 1–5 | `test_replay_status_management.py:101/127/158/194/241` | 5 failed（`near "FOR": syntax error` @ `app/services/paper_trading.py:874`）| `SELECT … FOR UPDATE` 行鎖 | `replay row lock and concurrent acquisition require a real PostgreSQL backend` | NOT RUN |
| 6–10 | `test_formal_path_integration.py:211/245/279/309/340` | 5 errors（UUID／Decimal 綁定＋`FOR UPDATE`）| psycopg2 型別 адапテーション＋行鎖 | `formal path needs PostgreSQL: psycopg2 type adaptation and FOR UPDATE row lock` | NOT RUN |
| 11–15 | `test_ledger_reconciliation.py:211/293/350/447/500` | 5 errors（`create_portfolio()` 綁 `Decimal` @ `paper_trading.py:74`）| Decimal bind＋行鎖 | `ledger over real rows requires a PostgreSQL backend` | NOT RUN |
| 16–17 | `test_owner_verification.py:142/245` | 2 errors（param 3 Decimal）| Decimal bind | `owner verification over real rows requires a PostgreSQL backend` | NOT RUN |
| 18 | `test_decimal_precision_regression.py:138` | 1 error（class 級；同檔 `TestExposureLimitBoundary` 3 條**仍在 lane**）| NUMERIC 精確度 | `NUMERIC exactness and the replay row lock require a PostgreSQL backend` | NOT RUN |
| 19–21 | `test_retry_failure_validation.py:231/283/328` | 3 errors | 真 replay worker＋create_portfolio | `retry and rollback semantics are proven over the real replay worker, which needs PostgreSQL` | NOT RUN |
| 22 | `test_real_concurrency.py:190` | 1 error | barrier 併發需真 MVCC | `concurrent acquisition requires real MVCC and a PostgreSQL backend` | NOT RUN |
| 23 | `test_http_api_success_chain.py:225` | 1 failed（產品回應 `creation_failed`）| 同一 Decimal bind | `success chain runs the real paper-trading vertical, which needs PostgreSQL` | NOT RUN |
| 24–25 | `test_source_dedup_scope.py:110/197` | 2 failed | 兩條都以 `create_portfolio` 開跑 | `source dedup scope is proven over the real paper-trading vertical, which needs PostgreSQL` | NOT RUN |
| 26–30 | `test_regression_contracts.py:54/141/187/230/277` | 5 errors | fixture fail-closed：無 `ALETHRA_PG_TEST_URL` 不 fallback，並驗證 target | `commit/hook contracts run against a verified PostgreSQL test target` | NOT RUN |

**對應 PG 命令（30 條同一條，但今天已知無法全綠）**：`ALETHRA_PG_TEST_URL='postgresql://<role>:<pw>@localhost:5432/alethra_test' TMPDIR=… bash ./backend/scripts/run_tests.sh pg`。**它目前被 §11.7 第 7 點的 DB 名稱互斥卡住**：能過 preflight＋safety gate 的名稱只有 `alethra_test`／`alethra_owner_test`，而這兩個名稱必使 9 條 `== "ai_investment_lab_test"` 斷言失敗——所以「跑 PG 命令」不等於「30 條會綠」，該互斥屬 #150，未獲批准（E1–E3）前也不會執行。

**勘誤（追加，不改寫 §11.4 第 478 行原文）**：該行寫「本輪把 **33** 條 failed/error 變成 skipped」。本節以 §11.4 自己的逐列加總（A 群 5+5+5+2+1+3+1+1+2＝25、B 群 5）與權威日誌的逐字 SKIPPED 行雙重核對，實得 **30** 條；那 3 條差额無法由任何日誌重建，屬手算錯誤，以 30 為準。原始句子保留。

#### 7. 19 條「不是 PG 前提」的 skip（必頌單獨可見）

| 條數 | 理由 | 性質 | 處置 |
|---|---|---|---|
| 18 | `live-stack probe: ALETHRA_LIVE_PROBES is not set to 1: these probes write bars, sessions and paper orders into whatever database the API uses…`（`test_c1_c4_complete.py` 4、`test_c1_c4_http.py` 4，另 10 條在同類 live-stack 檔）| 需要**已啟動且明確指定的堆疊**（E4/E5 範疇），不是 PostgreSQL 型別前提；預設關閉是正確的 fail-closed 設計 | 記錄一次；開啟需 `ALETHRA_LIVE_PROBES=1` ＋已批准運行的堆疊，本輪未啟動任何服務 |
| 1 | `BLOCKED: Cannot intercept cleanup commit without complex mocking`（`test_fixture_failure_injection.py:178`）| **既非 PG 也非堆疊前提**：是 mock 能力缺口。它與 §11.8 第 4 點「已修」的 D 類 2 條同檔，容易被誤認為已處理 | 保留為開放項；若日後以「整檔 PG-only」為由順帶移走同檔其他離線斷言，即違反 §一「不可用 module skip 一併移走」|

其餘 **99 條**的理由字串都含 PostgreSQL／PG 環境前提（型適變、行鎖、NUMERIC、MVCC、`current_database()`、`ALETHRA_PG_*` 未設）——包含 `test_target_guard.py` 的 8 條（5 條 `doesn't use socket connection`＋3 條 `ALETHRA_PG_TEST_URL not set…`），它們是 **PG 環境閘門的負控制**，不是產品能力證據。

#### 8. 撤回 §11.8 第 6 點的一筆歸因（追加，不改寫原文）

§11.8 第 6 點 `test_quota_concurrency.py` 那列寫「**5**」條 def，並稱 `:59` 的 `def test_user_with_limit(pg_engine)`「內含 `yield`，它是**以 `test_` 命名的 fixture**，既被 collection 成 item」。**後半句為 false**：實測（本機離線最小反例：一個模組含 `@pytest.fixture def test_alpha(): yield 1` 與 `def test_beta(test_alpha)`）→ pytest `--collect-only` 回報 **`1 test collected`**，只有 `test_beta`；fixture 標記的 `test_*` 名稱**不會**被收集成 item。權威日誌也一致：`grep quota_concurrency` 在 `/tmp/inventory_20261117b.txt` 只有 **1 行**（module 級 SKIPPED），而該檔的 def 實為 **4 條**（`:95`、`:144`、`:201`、`:284`），不是 5 條。因此 #150 對該檔的真實剩餘工作只有兩件：(i) 把以 `test_` 開頭的 fixture 改名（避免依賴 pytest 這條不成文行為），(ii) 把 import 期寬鬆的 `except Exception → skip` 改成 fail-closed（「PG 配置錯誤」現在也顯示成 skip）。原文保留，此處更正。

#### 9. 本節不是什麼

1. **不是產品 PASS**：19 條 FAILED 中 17 條是 PG-only（尚未標記）、1 條 provenance 真失敗、1 條為本輪引入並已在源頭修好（修正在量測**之後**，故本節的 19 屬測量時的樹）；3245 條通過不構平台驗收。
2. **不是 PG 結果**：未啟動 PostgreSQL、未建 role/DB、未跑 migration、未啟動任何服務或瀏覽器、未 commit／push；55 條 PG-only 節點＝**NOT RUN**，30 條 changed-to-skip 節點＝**NOT RUN**。
3. **不是第二次全量**：本輪只有這一次權威全量（attempt A 為入口拒絕，未產結果）。§11.8 的 focused／前綴數字未被重跑取代，也未被本節覆寫。
4. **未改測試順序、未加 retry、未新增整檔 skip、未刪斷言、未放寬 `FORBIDDEN_DATABASES`／`ALLOWED_INSTANCES`／target guard**；本節唯一對被測樹的編輯是第 5 點那一行 `import os`（發生在日誌寫完之後）。
5. **依賴未動**：本輪未安裝、未升級任何套件；lane 用的仍是既有 `backend/.venv`（python 3.12.16／pytest 8.3.3／sqlalchemy 2.0.36）；`.local/`（本機 PG16 與工具 venv）未被啟動、未被復用、未被改動。
6. **保存狀態**：本節所有日誌在 `/tmp`（非持久媒體、未進 Git）；HEAD 未動，因此「可重現」目前只在本機成立——Git 保存需 G1–G3 批准。

---

### 11.10 權威 fixed-state 全量帳（#150 之後）＋ changed-to-skip 逐節點帳（追加；不改寫 §11.7／§11.8／§11.9）

本節是 §一「收齊量測」那一節的交付：同一棵樹、同一次全量的**完整命令、cwd、HEAD、dirty 身分、
runner fingerprint、provider 設定、收集／通過／失敗／error／skip／deselect、exit code、執行時間與
原始日誌位置**，以及 75 條 changed-to-skip 節點的**逐條**原先結果、PG 前提、現在 skip 原因、對應 PG
命令與仍未執行狀態。所有數字由 `/tmp/gen_1110_table.py`（sha256
`c2b210919747cdfba2e964f8d10a5bc3dcbb536e05929f1135f7094fe4658a57`）從三份原始檔重算，不是由本節
逐字键入：§11.9 日誌（bad 節點身分）、權威日誌（183 條 skip 句）、`marked-junit.xml`（75 條 skip 的
**節點 id**）。

#### 1. 測量身分（每一欄都可從日誌前 11 行重讀）

| 欄 | 值（日誌逐字，非重組）|
|---|---|
| 實際命令 | `TMPDIR=/tmp/alethra-sqlite-test-dir DATA_PROVIDER=demo bash ./backend/scripts/run_tests.sh sqlite` — 日誌 `command:` 欄為 `./backend/scripts/run_tests.sh sqlite `，**未帶任何 pytest 參數**；`-rfEs` 由腳本附加（`backend/scripts/run_tests.sh`）|
| 啟動 cwd | `/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra`，腳本再 `cd` 進 `backend`；日誌 `cwd:` 欄＝`…/alethra/backend` |
| HEAD | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（日誌 `git:` 欄自行印出，working tree dirty）|
| dirty diff 身分（啟動前）| `git status --porcelain` **124 條路徑**，sha256 `83accd44d9951abb8c38fc7f9fdcde8084a69ac8961300a40068a87139249836`；存 `/tmp/alethra-test-logs/full-suite-identity.txt`（含 `utc=2026-11-16T18:05:59Z`）|
| dirty diff 身分（結束後）| **未另存 snapshot**。當時在場記錄為「相同」，但本節不把它列為實測欄位；前後雙測由第 7 小節的復測承擔（那份才有 before/after 兩份檔案）|
| runner fingerprint | `python 3.12.16 \| pytest 8.3.3 \| sqlalchemy 2.0.36`（日誌第 3 行印出，非路徑推測）|
| interpreter | `/Users/…/alethra/backend/.venv/bin/python`（未落回系統 python）|
| provider 設定 | `DATA_PROVIDER=demo`（離線、無外呼）；`JWT_SECRET_KEY` 由 lane 每次生成 |
| TMPDIR／DB | `TMPDIR=/tmp/alethra-sqlite-test-dir`；`preflight ok: sqlite: /tmp/alethra-sqlite-test-dir/alethra-sqlite-test-74960.db`（每 PID 新檔，pid 74960＝本次 pytest 行程）|
| 收集 | **3462 tests collected in 2.94s**（日誌 `selection on this tree:` 欄）|
| 結果 | **1 failed, 3281 passed, 183 skipped, 9843 warnings**；**0 error**、**0 deselected**（`grep deselected` ＝ 0 筆命中）|
| exit code | `1`（日誌最後一行 `exit_code=1`；唯一 FAILED 是 provenance，見 §11.9 第 4 點與本節第 5 小節）|
| 執行時間 | `1970.77s (0:32:50)` |
| 原始日誌 | `/tmp/alethra-test-logs/full-suite-20261116.log`，109,947 bytes，sha256 `da95b1ec5baae9692f51f4dbcec921725eccaf0f47e3d9e10194853980c6411a`（未覆寫、未截斷；`/tmp` 非持久媒體，見第 8 小節）|
| 對照日誌 | §11.9 那輪：`/tmp/full_sqlite_20261117b.txt`（58,304 bytes，sha256 `54c703cf166d938899851cd3f8c762a0ab86c764d797517086959f3ac6e6b1db`）|

#### 2. 算術閉合：3465 個 outcome unit ＝ 3462 個 item ＋ 3 個 module-level skip

`1 failed ＋ 3281 passed ＋ 183 skipped ＝ 3465` 個 outcome unit，比 `3462` 個 item 多 **3**。差額不
是回報方式不同，而是**有 3 個模組在 collection 階段就被整檔跳過**：它們的 item 從未進入 `collected`
計數，但每一檔仍回報 **1 個 skipped unit**。這 3 檔由日誌逐字指出（`_pg.py` 三檔，舊的
`except Exception → pytest.skip(allow_module_level=True)` 形式）：

```
SKIPPED [1] tests/test_crypto_bar_harvest_pg.py:16: PostgreSQL not configured (run via scripts/run_tests.sh pg)
SKIPPED [1] tests/test_crypto_paper_pg.py:26: PostgreSQL not configured (run via scripts/run_tests.sh pg)
SKIPPED [1] tests/test_strategy_study_timestamps_pg.py:21: PostgreSQL not configured (run via scripts/run_tests.sh pg)
```

§11.9 那輪的同一個差額是 **5**（`3420 ＝ 3415 ＋ 5`）：除上述 3 檔外，`test_ownership_integration.py:45`
與 `test_quota_concurrency.py:38` 當時也是整檔跳過；#150 把這兩檔拆成節點級閘門，item 因此進入
`collected`（＋14）、module 單位因此消失（−2）。這一項就是 §一「每個 changed-to-skip node 列…」背後的
機動：整檔 skip 會把「沒跑」藏在 collection 裡，而不是藏在 skip 清單裡。

**與 §11.9 的逐欄差額（全部對得上）：**

| 欄 | §11.9 | 權威輪 | Δ |
|---|---|---|---|
| item（collected）| 3415 | 3462 | ＋47 |
| module-level skip 單位 | 5 | 3 | −2 |
| outcome unit | 3420 | 3465 | ＋45 |
| FAILED | 19 | 1 | −18 |
| ERROR | 38 | 0 | −38 |
| SKIPPED | 118 | 183 | ＋65 |
| PASSED | 3245 | 3281 | ＋36 |

`Δunit ＝ −18 −38 ＋65 ＋36 ＝ ＋45 ＝ Δitem(＋47) ＋ Δmodule(−2)` ✔。

**Δitem ＋47 不是剩項**：本節以兩份保存下來的 collection 清單逐檔相減得到它（`/tmp/alethra-test-logs/ids-old.txt`
＝§11.9 樹的 3415 個 item、`ids-new.txt`＝權威樹的 3462 個 item，兩者皆由
`--collect-only -q` 在同一棵樹產生；新增 48 個、移除 1 個 ⇒ 淨 ＋47 ✔）：

| Δ | 檔 | 由哪一項工作解釋 |
|---|---|---|
| ＋16 | `tests/test_postgresql_only_marks.py` | #150 的共用 PG 前提 gate 契約與回歸證明（16 條全新節點）|
| ＋10 | `tests/test_ownership_integration.py` | 該檔 §11.9 是**整檔跳過（0 item）**；拆成節點級後 10 條 item 進入 collection（6 條 PG-only ＋ 4 條離線回補）|
| ＋10 | `tests/test_migration_tail_contract.py` | #158：expected migration 尾端鏈固定 ＋ 分岔樹拒收路徑第一次被跑到 |
| ＋5 | `tests/test_replay_credibility.py` | #151（double 以 `spec=Session` 真正 fail-closed）＋ #156（完成寫入帶 source-state 條件）|
| ＋4 | `tests/test_quota_concurrency.py` | 同 ownership：整檔跳過拆成節點級，4 條 item 進入 collection |
| ＋2／−1 | `tests/test_safety_gate_negative.py` | `TestEnvVarConflict::test_conftest_aliases_single_env_var` 被拆成 `…_aliases_postgresql_env_var` 與 `…_does_not_alias_sqlite_into_the_postgresql_name` 兩條——這是 §11.8 conftest 別名污染源頭的直接回歸：舊的那條只檢查「有別名」，新的兩條分別檢查「別名只對真 PostgreSQL 發生」與「SQLite 不得被冒充」|
| ＋1 | `tests/test_research_run_admission.py` | #155：index race 分支現在要比對 fingerprint，不能為「另一個請求」決定結果 |

被移除的那 1 條（`test_conftest_aliases_single_env_var`）不是刪斷言：它被上面那兩條取代，且新斷言比
舊的窄（舊的那條在 §11.8 修好污染源頭之後已經無從判定「別名到哪一個名字」）。

**183 條 skip 的三類分解**（由 `backend/scripts/skip_budget.py` 對同一份日誌量測，不是本節分類）：
`pg_only=164`、`live_stack=18`、`other=1`，`164＋18＋1＝183` ✔。`pg_only` 那 164 條再拆為
**75 條帶 `PG-ONLY／PG-SETUP [檔:行]` 標記**（＝第 3 小節的對象）＋ **86 條舊式原因句**（型別適應、
行鎖、NUMERIC、MVCC、`current_database()`、`ALETHRA_PG_*` 未設）＋ **3 條整檔句**（上列 `_pg.py`），
`75＋86＋3＝164` ✔（後兩組由 `skip_budget.py::classify()` 的 `PGISH` 規則命中，非人工判讀）。
18 條 live-stack probe 與 1 條 `BLOCKED`（mock 能力缺口）依 §11.9 第 7 點繼續單獨可見，
**沒有**被 PG-only 標記吸收。

#### 3. changed-to-skip 75 條：逐節點的「原先結果／PG 前提／現在 skip 原因／PG 命令／仍未執行狀態」

§一 要求這五欄**逐節點**列出。先講清四件事，表格才不會被誤讀：

1. **「現在 skip 原因」的共有部分**是 `tests/postgresql_only.py::skip_reason()` 印出的同一句（只有
   `prerequisite` 與 `[檔:行]` 逐節點不同）。權威日誌的 75 條完全一致，逐字一例：

   ```
   PG-ONLY [tests/test_target_guard.py:1228]: drives the real guard/factory error path with an
   encoded password. ALETHRA_PG_TEST_URL is unset or is not a PostgreSQL URL, so this node has no
   database to run against -- NOT RUN, not passed. To execute it: ALETHRA_PG_TEST_URL=
   '<postgresql://…/alethra_test…>' bash ./backend/scripts/run_tests.sh pg <node ids>
   ```

2. **「對應 PG 命令」**就是句尾那一條，逐字相同、只在 `<node ids>` 處帶該節點自己的 id。本節不逐列重印
   同一條命令 75 次；要重印時每列可直接由 id 代入。該命令**本輪未執行**（§六：PG 啟動、建 role/DB、
   migration、PG lane 皆未批准）。
3. **`[檔:行]` 有兩種身分**：節點級閘門（`requires_postgresql()` 在 def 內被叫）印出**該節點自己**的
   行；autouse 閘門印出**閘門自己**的行，因此多條節點會共用同一個 `[檔:行]`。下表是權威日誌的實際
   分佈（`uniq -c` 量測，非推估）：

   | 條數 | `[檔:行]` | 該行是什麼 |
   |---|---|---|
   | 32 | `tests/test_paper_trading_cancel_atomic.py:105` | autouse 閘門（本檔 32 條共用）|
   | 12 | `tests/test_target_guard.py:{994,1016,1042,1064,1081,1101,1118,1159,1228,1255,1281,1304}` | 逐節點行（12 條各自一個）|
   | 7 | `tests/test_privilege_enforcement.py:33` | autouse 閘門 |
   | 6 | `tests/test_ownership_integration.py:{209,219,231,243,278,302}` | 逐節點行（6 條各自一個）|
   | 5 | `tests/test_connection_role_verification.py:34` | autouse 閘門 |
   | 4 | `tests/test_quota_concurrency.py:50` | autouse 閘門 |
   | 3 | `tests/test_api_login_with_fixture.py:42` | autouse 閘門 |
   | 3 | `tests/test_data_isolation.py:33` | autouse 閘門 |
   | 2 | `tests/test_cleanup_verification.py:29` | autouse 閘門 |
   | 1 | `tests/test_vertical_path.py:26` | def 內閘門（同檔另一條 `test_simple_query` 仍離線跑）|

   19 條（target_guard 12 ＋ ownership 6 ＋ vertical_path 1）的 `[檔:行]` 本身就是節點身分；其餘 56 條
   由 `marked-junit.xml` 的 **class＋test 名稱**取得節點身分，`[檔:行]` 只指出它從哪一道閘門被跳過。
   這也是第 5 小節反向回歸的精度上限：兩向可證明「每一道閘門的跳過**數量**與 id 集合相同」，而不是
   56 條各自獨立比對行號。
4. **「仍未執行狀態」75 條全部相同**：`NOT RUN（PG lane 未啟動；E1／E2／E3 未批准）`。skip 不是通過，
   也不是「已驗證不需要」。

前提代碼（P1…P21）在表格之後逐條列出全文；同一句前提在多檔共用時不重複計算。

以下 75 列由 `/tmp/gen_1110_table.py` 產生（輸出檔 sha256 見第 1 小節；「§11.9 原來結果」欄的由來見
同檔文件串：FAILED／ERROR 由 §11.9 短摘要的節點名直讀；8 條 SKIPPED 由舊 bracket 行號與現在 bracket
行號的**單調最小漂移配對**得到，平均漂移 5.4 行，並以兩條自帶判別力的舊原因句
（`…doesn't contain expected DB name` → `test_integration_wrong_db_name_rejected_before_write`、
`…has no query params` → `test_credential_leakage_sensitive_query_params`）校驗；配對不自洽時生成器
`sys.exit` 而不是猜。10 條「模組級 skip」由「舊日誌印出的行號在該檔第一條 `def test_` 之上」判定，
即該模組當時產出 0 item。剩下 2 條在 §11.9 既不在 bad 清單、也不在 skip 清單 ⇒ 當時是**通過**）：

| # | changed-to-skip 節點 id | §11.9 原來結果 | 現在 skip 句的 `[檔:行]` | PG 前提 |
|---|---|---|---|---|
| 1 | `tests/test_api_login_with_fixture.py::TestRealAPILoginWithFixture::test_full_api_chain_with_cost_reconciliation` | FAILED | `tests/test_api_login_with_fixture.py:42` | P18 |
| 2 | `tests/test_api_login_with_fixture.py::TestRealAPILoginWithFixture::test_login_endpoint_returns_valid_token` | FAILED | `tests/test_api_login_with_fixture.py:42` | P18 |
| 3 | `tests/test_api_login_with_fixture.py::TestRealAPILoginWithFixture::test_post_replay_proactive_rollback_cleanup` | FAILED | `tests/test_api_login_with_fixture.py:42` | P18 |
| 4 | `tests/test_cleanup_verification.py::test_cleanup_on_assertion_failure` | FAILED | `tests/test_cleanup_verification.py:29` | P20 |
| 5 | `tests/test_cleanup_verification.py::test_explicit_cleanup_after_commit_and_failure` | FAILED | `tests/test_cleanup_verification.py:29` | P20 |
| 6 | `tests/test_connection_role_verification.py::test_all_sessions_consistent` | FAILED | `tests/test_connection_role_verification.py:34` | P16 |
| 7 | `tests/test_connection_role_verification.py::test_api_get_db_uses_runtime_role` | FAILED | `tests/test_connection_role_verification.py:34` | P16 |
| 8 | `tests/test_connection_role_verification.py::test_http_request_uses_runtime_role` | FAILED | `tests/test_connection_role_verification.py:34` | P16 |
| 9 | `tests/test_connection_role_verification.py::test_sessionlocal_uses_runtime_role` | FAILED | `tests/test_connection_role_verification.py:34` | P16 |
| 10 | `tests/test_connection_role_verification.py::test_testsession_uses_runtime_role` | FAILED | `tests/test_connection_role_verification.py:34` | P16 |
| 11 | `tests/test_data_isolation.py::test_isolation_step1_insert_data` | FAILED | `tests/test_data_isolation.py:33` | P19 |
| 12 | `tests/test_data_isolation.py::test_isolation_step2_verify_cleanup` | FAILED | `tests/test_data_isolation.py:33` | P19 |
| 13 | `tests/test_data_isolation.py::test_isolation_step3_cleanup_if_needed` | FAILED | `tests/test_data_isolation.py:33` | P19 |
| 14 | `tests/test_ownership_integration.py::TestHTTPOwnershipIsolation::test_multiple_runs_per_user_isolated` | 模組級 skip（0 item，未被收集） | `tests/test_ownership_integration.py:302` | P15 |
| 15 | `tests/test_ownership_integration.py::TestHTTPOwnershipIsolation::test_rejection_has_no_side_effects` | 模組級 skip（0 item，未被收集） | `tests/test_ownership_integration.py:278` | P15 |
| 16 | `tests/test_ownership_integration.py::TestHTTPOwnershipIsolation::test_user_can_read_own_run_status` | 模組級 skip（0 item，未被收集） | `tests/test_ownership_integration.py:209` | P15 |
| 17 | `tests/test_ownership_integration.py::TestHTTPOwnershipIsolation::test_user_cannot_read_other_users_daily_report` | 模組級 skip（0 item，未被收集） | `tests/test_ownership_integration.py:231` | P15 |
| 18 | `tests/test_ownership_integration.py::TestHTTPOwnershipIsolation::test_user_cannot_read_other_users_run_status` | 模組級 skip（0 item，未被收集） | `tests/test_ownership_integration.py:219` | P15 |
| 19 | `tests/test_ownership_integration.py::TestHTTPOwnershipIsolation::test_user_cannot_stop_other_users_run` | 模組級 skip（0 item，未被收集） | `tests/test_ownership_integration.py:243` | P15 |
| 20 | `tests/test_paper_trading_cancel_atomic.py::TestBatchAReverseOwnership::test_cross_user_cleanup_both_directions_abort` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 21 | `tests/test_paper_trading_cancel_atomic.py::TestBatchBMinimalIntegration::test_acquisition_cancel_synchronization_with_exception_injection` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 22 | `tests/test_paper_trading_cancel_atomic.py::TestCancelConvergence::test_cancel_failed_completes_immediately` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 23 | `tests/test_paper_trading_cancel_atomic.py::TestCancelConvergence::test_cancel_pending_completes_immediately` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 24 | `tests/test_paper_trading_cancel_atomic.py::TestCancelConvergence::test_cancel_queued_completes_immediately` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 25 | `tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_aborts_on_cross_user_decision_order` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 26 | `tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_detects_residual_fills` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 27 | `tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_detects_residual_orders` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 28 | `tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_helper_rollback_on_injected_failure` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 29 | `tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_user2_does_not_affect_user1_data` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 30 | `tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_with_setup_failure_still_deletes_partial` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 31 | `tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_normal_cleanup_deletes_all_resources` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 32 | `tests/test_paper_trading_cancel_atomic.py::TestCompletionFailureProtection::test_completion_handler_respects_cancel` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 33 | `tests/test_paper_trading_cancel_atomic.py::TestCompletionFailureProtection::test_failure_handler_respects_cancel` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 34 | `tests/test_paper_trading_cancel_atomic.py::TestRaceConditionsWithBarrier::test_cancel_vs_completion_race` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 35 | `tests/test_paper_trading_cancel_atomic.py::TestRaceConditionsWithBarrier::test_concurrent_cancel_with_barrier` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 36 | `tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_cancel_first_during_replay_polling` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 37 | `tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_cancel_vs_failure_race_during_replay` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 38 | `tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_completion_first_before_cancel_check` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 39 | `tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_final_commit_protection_respects_cancel` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 40 | `tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_ledger_and_cash_consistency_after_cancel` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 41 | `tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_polling_boundary_exactly_100_events` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 42 | `tests/test_paper_trading_cancel_atomic.py::TestThreadPrecision::test_all_threads_terminated_after_barrier` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 43 | `tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_cancel_after_completed_rejected` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 44 | `tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_cancel_requested_blocks_execution_acquisition` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 45 | `tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_double_cancel_idempotent` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 46 | `tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_phase1_cancel_requested_running_state` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 47 | `tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_phase2_worker_completes_cancel` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 48 | `tests/test_paper_trading_cancel_atomic.py::TestWorkerAtomicTerminalState::test_cancel_committed_before_exception_sets_cancelled` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 49 | `tests/test_paper_trading_cancel_atomic.py::TestWorkerAtomicTerminalState::test_failure_committed_before_cancel_sets_failed` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 50 | `tests/test_paper_trading_cancel_atomic.py::TestWorkerAtomicTerminalState::test_terminal_state_commit_failure_preserves_running` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 51 | `tests/test_paper_trading_cancel_atomic.py::TestWorkerAtomicTerminalState::test_zero_row_update_when_not_running` | ERROR | `tests/test_paper_trading_cancel_atomic.py:105` | P1 |
| 52 | `tests/test_privilege_enforcement.py::test_limited_role_can_delete` | ERROR | `tests/test_privilege_enforcement.py:33` | P14 |
| 53 | `tests/test_privilege_enforcement.py::test_limited_role_can_insert` | ERROR | `tests/test_privilege_enforcement.py:33` | P14 |
| 54 | `tests/test_privilege_enforcement.py::test_limited_role_can_select` | ERROR | `tests/test_privilege_enforcement.py:33` | P14 |
| 55 | `tests/test_privilege_enforcement.py::test_limited_role_can_update` | ERROR | `tests/test_privilege_enforcement.py:33` | P14 |
| 56 | `tests/test_privilege_enforcement.py::test_limited_role_cannot_alter_table` | ERROR | `tests/test_privilege_enforcement.py:33` | P14 |
| 57 | `tests/test_privilege_enforcement.py::test_limited_role_cannot_create_table` | FAILED | `tests/test_privilege_enforcement.py:33` | P14 |
| 58 | `tests/test_privilege_enforcement.py::test_limited_role_cannot_drop_table` | ERROR | `tests/test_privilege_enforcement.py:33` | P14 |
| 59 | `tests/test_quota_concurrency.py::test_concurrent_reservations_with_limit` | 模組級 skip（0 item，未被收集） | `tests/test_quota_concurrency.py:50` | P17 |
| 60 | `tests/test_quota_concurrency.py::test_concurrent_settle_and_release` | 模組級 skip（0 item，未被收集） | `tests/test_quota_concurrency.py:50` | P17 |
| 61 | `tests/test_quota_concurrency.py::test_concurrent_settlement_same_reservation` | 模組級 skip（0 item，未被收集） | `tests/test_quota_concurrency.py:50` | P17 |
| 62 | `tests/test_quota_concurrency.py::test_reservation_counter_creation_race` | 模組級 skip（0 item，未被收集） | `tests/test_quota_concurrency.py:50` | P17 |
| 63 | `tests/test_target_guard.py::test_credential_leakage_authority_password_in_exception` | **PASSED（假通過）** | `tests/test_target_guard.py:1228` | P10 |
| 64 | `tests/test_target_guard.py::test_credential_leakage_connection_failure` | FAILED | `tests/test_target_guard.py:1304` | P13 |
| 65 | `tests/test_target_guard.py::test_credential_leakage_encoded_password_in_exception` | **PASSED（假通過）** | `tests/test_target_guard.py:1255` | P11 |
| 66 | `tests/test_target_guard.py::test_credential_leakage_sensitive_query_params` | SKIPPED（僅改寫歸因句） | `tests/test_target_guard.py:1281` | P12 |
| 67 | `tests/test_target_guard.py::test_guard_rejection_after_successful_connection` | FAILED | `tests/test_target_guard.py:1159` | P9 |
| 68 | `tests/test_target_guard.py::test_integration_conflicting_host_params_rejected` | SKIPPED（僅改寫歸因句） | `tests/test_target_guard.py:1101` | P7 |
| 69 | `tests/test_target_guard.py::test_integration_empty_host_rejected` | SKIPPED（僅改寫歸因句） | `tests/test_target_guard.py:1064` | P5 |
| 70 | `tests/test_target_guard.py::test_integration_no_host_param_rejected` | SKIPPED（僅改寫歸因句） | `tests/test_target_guard.py:1081` | P6 |
| 71 | `tests/test_target_guard.py::test_integration_verified_factory_no_session_on_failure` | SKIPPED（僅改寫歸因句） | `tests/test_target_guard.py:1118` | P8 |
| 72 | `tests/test_target_guard.py::test_integration_wrong_db_name_rejected_before_write` | SKIPPED（僅改寫歸因句） | `tests/test_target_guard.py:994` | P2 |
| 73 | `tests/test_target_guard.py::test_integration_wrong_socket_dir_rejected` | SKIPPED（僅改寫歸因句） | `tests/test_target_guard.py:1016` | P3 |
| 74 | `tests/test_target_guard.py::test_integration_wrong_socket_port_rejected` | SKIPPED（僅改寫歸因句） | `tests/test_target_guard.py:1042` | P4 |
| 75 | `tests/test_vertical_path.py::test_safety_gate_enforced` | FAILED | `tests/test_vertical_path.py:26` | P21 |

* **P1** — holds real rows across sessions, takes real FOR UPDATE row locks and commits or rolls them back, none of which the SQLite file provides
* **P2** — connects for real and expects verify_test_target to reject a database name that is not the approved one
* **P3** — opens a Unix-socket connection and expects an unapproved socket directory to be refused before any write
* **P4** — opens a Unix-socket connection and expects a wrong port to be refused
* **P5** — parses the URL for real and rejects an empty host= parameter
* **P6** — parses the URL for real and rejects the absence of a host= parameter
* **P7** — parses the URL for real and rejects two conflicting host= parameters
* **P8** — proves that a failed verification returns no session, which needs a driver that can attempt the connection at all
* **P9** — connects successfully to the 'postgres' database and is rejected by the guard afterwards -- a refusal needs a server that answers first
* **P10** — drives the real guard/factory error path, whose message must be masked
* **P11** — drives the real guard/factory error path with an encoded password
* **P12** — drives the real guard/factory error path with a sensitive query param
* **P13** — fails a real connection and asserts the driver's error text carries no password -- the URL shape is the subject, so a SQLite file will not do
* **P14** — enforces GRANT/REVOKE on a named role and expects SQLSTATE 42501, which only a real PostgreSQL role model can refuse
* **P15** — POST /api/v1/paper-trading/create has to produce the portfolio and run whose ownership is then checked. That endpoint passes Decimal money values into raw SQL (app/api/paper_trading.py:132 -> app/services/paper_trading.py:74-109) and the sqlite3 driver on this lane refuses a Decimal bind, so it answers 500 creation_failed and there is nothing to isolate
* **P16** — asks the server which role and database the connection landed on, which SQLite is not able to answer
* **P17** — holds two real sessions against one quota row at the same moment; the SQLite lane runs every session through one shared connection, so it has no second writer to race with
* **P18** — drives the formal portfolio API, whose Decimal money bindings the SQLite driver in this lane refuses
* **P19** — writes to the migration-created _test_sacrificial_ table with ON CONFLICT, neither of which exists on the SQLite lane
* **P20** — commits to and reads back the migration-created _test_sacrificial_ table over independent connections
* **P21** — asks the server for current_database()/current_user and expects a CREATE TABLE to be refused by role permissions

#### 4. failed→fixed 與 failed→PG-only 分開對帳（§七 第 3 點）

§11.9 的 bad 節點共 **57** 條（19 FAILED ＋ 38 ERROR，全部在短摘要裡有名有姓）。它們在權威輪的去向由
`/tmp/gen_1110_table.py` 的同一份對帳算出，**不靠總數相減**：

| 去向 | 條數 | 身分 |
|---|---|---|
| FAILED → **PG-only NOT RUN** | 17 | 即第 3 小節表中 `prior=FAILED` 的 17 列（privilege 1、connection_role 5、data_isolation 3、api_login 3、cleanup 2、vertical_path 1、target_guard 2）|
| ERROR → **PG-only NOT RUN** | 38 | `prior=ERROR` 的 38 列＝`test_paper_trading_cancel_atomic.py` 32 條（舊症狀 `no such function: current_database`）＋ `test_privilege_enforcement.py` 6 條（舊症狀 GRANT/REVOKE 相關的 SQLSTATE 42501 在 SQLite 上根本不會發生）|
| FAILED → **PASSED（真修好）** | 1 | `tests/test_paper_trading_cancel_atomic.py::TestTargetGuard::test_get_test_db_url_fails_closed`——§11.9 第 5 點那條**由本輪引入**的回歸，源頭修正是把被連帶刪掉的 `import os` 加回（`tests/test_paper_trading_cancel_atomic.py:33`），非改斷言、非改順序 |
| FAILED → **仍然 FAILED** | 1 | `tests/test_registration_risk_acknowledgement.py::test_an_acknowledged_registration_records_one_audit_event`——provenance 真失敗（`assert 'unknown' == 'human'`）。依既定判定**不得朝測試方向修**；保留為 owner 判項（#157）|
| **合計** | **57** | 19 ＝ 17＋1＋1 ✔，38 ＝ 38＋0 ✔ |

**另有一筆方向相反、必須單獨講的帳**：75 條 marked skip 裡有 **2 條在 §11.9 是 PASSED**——
`tests/test_target_guard.py::test_credential_leakage_authority_password_in_exception`（現在 `:1228`）與
`…_encoded_password_in_exception`（現在 `:1255`）。生成器把它們判為 PASSED 的理由是消滅法：它們不在
§11.9 的 19 條 FAILED、不在 38 條 ERROR，也不在那 8 條有 bracket 行號的 SKIPPED 之中。

為什麼那是**假通過**（本節以本機離線最小重測證明，不是推測）：這兩條把
`user:FakePassword123@` 注入 URL，再斷言「例外訊息裡不得出現該字串」。在 SQLite lane 上
`create_engine()` 連產品閘門都沒進到，SQLAlchemy 自己先拒收 URL，而且**它的訊息已把密碼遮成
`***`**——同一棵樹、同一個 venv 實測：

```
type: ArgumentError
msg:  Invalid SQLite URL: sqlite://user:***@//tmp/…/probe.db
literal present in message: False
```

於是 `assert "FakePassword123" not in error_msg` 在**什麼都沒驗證**的情況下變綠（而且該 `except (TargetGuardError,
OperationalError, Exception)` 寬到把 `ArgumentError` 也當作預期待過——這是 #157 已記錄的空轉圈處理程式，兩者是
同一個缺口的兩個面）。本輪把它們改成 `postgresql_test_url(...)` 前提閘門 ⇒ 由「假綠」變成**老實的
NOT RUN**，並寫明該在 PG lane 跑。方向要講清楚：**skip 變多是誠實度變高，不是產品能力變好**，這一對
不會被算進任何 PASS。

#### 5. 污染修正的直接回歸：同一集合正序與反序各跑一次（§二 硬性要求）

| 項目 | 正序 | 反序 |
|---|---|---|
| selection | 12 檔（`test_postgresql_only_marks.py` … `test_safety_gate_negative.py`）| 同 12 檔，argv 順序反轉 |
| collected | 180 | 180 |
| 結果 | **105 passed, 75 skipped**, 0 failed, 0 error | **105 passed, 75 skipped**, 0 failed, 0 error |
| 時間 | 61.19s | 61.30s |
| 日誌 | `/tmp/alethra-test-logs/gating-forward.log` | `/tmp/alethra-test-logs/gating-reverse.log` |
| skip 身分清單 | `/tmp/alethra-test-logs/skips-forward.txt`（75 行）| `/tmp/alethra-test-logs/skips-reverse.txt`（75 行）|

`diff` 兩份 `[檔:行]` 多重集：**完全相同**（本節重跑該 `diff` 得 `IDENTICAL`）。搭配上面第 3 小節的
gate 分佈表可知：19 條（target_guard 12 ＋ ownership 6 ＋ vertical_path 1）的 skip 身分在兩向都是
**逐節點行號相同**；其餘 56 條的兩向回歸強度是「**每一道閘門的跳過數量與類別完全相同**」，而非 56
個獨立行號比對。這是 §二「原失敗集合及反向順序須有直接回歸」在本輪能達到的精度，超過這個強度的
宣稱本節不寫。

四類污染源（`sys.path` 插入、`os.environ` 殘留、模組級 import 探針、conftest 把 SQLite 路徑別名成
PostgreSQL 名字）的源頭修正與逐條判定在 §11.8 第 2 點；本節只補一句：**修正後沒有靠任何一項繞過手段**
——未改測試順序（反序那一輪是**驗證**，不是修正）、未加 retry／`-p no:randomly`、未新增整檔 skip（反而把
2 檔的整檔 skip 拆成節點級）、未刪斷言（唯一方向改變是上面那 2 條把假綠改成誠實 skip）、未全域清
cache、未放寬 `FORBIDDEN_DATABASES`／target guard。

#### 6. 量測之後才發生的事（不混進上面任何數字）

權威輪（3462 item）結束後至本節撰寫前，樹上另有四組改動。它們**不影響上面任何一欄**，但影響
下一輪的可比性，所以逐組列明：

| # | 改動 | 檔案 | 節點帳（實測）|
|---|---|---|---|
| 1 | #159：sqlite lane 拿到**可執行跳過上限**。`skip_budget.py`（新增）對日誌做 census，類別拆 `pg_only／live_stack／other`；上限由 `write-budget` 從真日誌生成（含來源日誌 sha256 與 `git:` 行），不是人填的數字 | `backend/scripts/skip_budget.py`、`backend/tests/skip_budget.json`（生成）、`backend/scripts/run_tests.sh` | `tests/test_lane_entry_contract.py` 9 → **22**（＋13）|
| 2 | #154：秘密帳本改為**內容束縛**。`TRACKED_DEBT` 每條带值派生摘要，`reconcile()` 新增第四種拒收（`changed`：帳本上的位置被原地改掉不得仍算豁免）；`Finding` 不再印長度、摘要或片段；docstring 寫明掃描器支援範圍與**看不到的範圍（git 歷史不在內）**；五條回歸證明入測試 | `backend/scripts/check_env_hygiene.py`、`backend/tests/test_env_hygiene_contract.py` | 22 → **26**（＋4）|
| 3 | 3 個 `*_pg.py` 的模組級閘門由「裸 `except Exception` → skip」改為 `postgresql_available()` 判定 ＋ `skip_reason()` 全文（理由句不再只說「PostgreSQL not configured」，而是带 `[檔:行]`、前提、PG 命令與 NOT RUN）| `tests/test_crypto_bar_harvest_pg.py:22`、`tests/test_crypto_paper_pg.py:31`、`tests/test_strategy_study_timestamps_pg.py:26` | 0 item → 0 item（仍是模組級，故 collection 不變；但理由句現在可核對）|
| 4 | 文件去指紋：`EXECUTION_RECORD_20261109.md` 裡殘留的 4 處 `sha256[:10]` ＋長度（acceptance JWT 簽金鑰、`TOKEN-CHARSET` 擴散值、`.bak` 合成密碼／bcrypt 雜湊、`postgres` 出廠預設值）改為**值類別**敘述，並留下「移除指紋不改變該值早已進入公開歷史」與 `UNKNOWN` 判定不變 | `EXECUTION_RECORD_20261109.md`（文件，非被測樹）| 0 |

兩檔合同 48 條節點（22＋26）在上線前已用真 lane 驗過：
`bash ./backend/scripts/run_tests.sh sqlite -q -p no:warnings --tb=line tests/test_lane_entry_contract.py tests/test_env_hygiene_contract.py`
⇒ `selection on this tree: 48 tests collected`、**`48 passed in 14.14s`**、`RC=0`，且日誌尾端如設計印出
`skip census of …` 與 `this run selected specific tests, so the whole-suite ceiling does not apply`
（子集跑**只回報不判定**——否則任何 focused run 都會被全量上限誤判）。

`Δitem` 由 3462 → **3479**（＋17），且**逐檔對帳**（`--collect-only -q` 清單相減，
`/tmp/alethra-test-logs/ids-post159.txt`，3479 行）：＋13 `test_lane_entry_contract.py`、＋4
`test_env_hygiene_contract.py`、**移除 0 條**。這一項本輪差點算錯，值得記下來：第一次對帳用
「直接叫 `.venv/bin/python -m pytest --collect-only`」而非走 lane，得到 3469 並顯示 `test_ownership_integration.py`
少了 10 條——實測原因不是節點消失，而是那樣叫**沒有 lane 設定的 `ALETHRA_TEST_DB`**，該檔的
fail-closed 閘門（`tests/test_ownership_integration.py:89`）在 collection 階段就 `RuntimeError: SAFETY: ALETHRA_TEST_DB environment variable is required
for integration tests`，整個模組從清單裡被拿走。**教訓**：離開 lane 的 collect-only 不是同一個量測，
之後所有 id 清單一律由 `run_tests.sh` 產生。

---

#### 7. 復測（#159＋#154 落地之後的 full-suite）——含本輪抓出的三個 gate 缺陷

owner 一：「本次全套完成後保存：完整命令、cwd、HEAD＋dirty diff 身分／runner fingerprint 與 provider 設定／收集／通過／失敗／error／skip／deselect 數／exit code、執行時間及原始日誌位置」「不要只交總數」。下表**每一欄都逐字取自保存下來日誌的前 10 行與尾端**，不是事後重組。

**7.1 測量身分與結果**

| 欄位 | 實測值 | 出處（可重算）|
|---|---|---|
| 完整命令 | `./backend/scripts/run_tests.sh sqlite -q -p no:warnings --tb=line -rfE` | console 日誌第 7 行 `command:`（lane 自己印的，見 7.3 缺陷 3）|
| cwd | `/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend` | 同上第 8 行 |
| HEAD＋dirty 身分 | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a (working tree dirty: run 'git status --porcelain' for the diff identity')` | 同上第 9 行 |
| 開跑前的工作樹指紋 | `utc=2026-11-16T18:05:59Z`、`porcelain_sha256=83accd44d9951abb8c38fc7f9fdcde8084a69ac8961300a40068a87139249836`、`dirty_paths=124` | `/tmp/alethra-test-logs/full-suite-identity.txt`（sha256 `6d4e1235ba41a5db2aeb911ae8ed3e14518183ee6960f305916a33708d9ab068`）|
| runner fingerprint 與 provider | `python 3.12.16 \| pytest 8.3.3 \| sqlalchemy 2.0.36`；`DATA_PROVIDER=demo`、`lane=sqlite`、`TMPDIR=/tmp/alethra-sqlite-test-dir` | 同檔 `fingerprint=`／`provider=` 行，與日誌第 3 行 `lane fingerprint:` 相同 |
| 收集 | `3479 tests collected in 3.19s` | 日誌第 10 行 `selection on this tree:` |
| 通過／失敗／跳過 | **3298 passed / 1 failed / 183 skipped** | 日誌尾端 `1 failed, 3298 passed, 183 skipped in 1987.33s (0:33:07)` |
| error／deselect | **0／0**（日誌摘要行不出現 `error` 與 `deselected` 兩欄）；`-q` 下以 `grep -c "^SKIPPED"` 覆核＝**183** 條逐條理由 | console 尾行＋實測 grep |
| 執行時間 | `1987.33s`＝0:33:07 | 同上 |
| exit code | **未單獨存檔**（見下方「本節不是什麼」第 5 點）。lane 結尾為 `exit "$RC"`、`RC=${PIPESTATUS[0]}`，且 `tests/test_lane_entry_contract.py` 有 `returncode == 1`／`== 2` 的傳導契約 ⇒「1 failed ⇒ RC=1」是**可推得但本輪未直接量測**的，如實標為推論，不寫成實測 | `backend/scripts/run_tests.sh` 末段＋合同行號 233/143-176 |
| 原始日誌 | console `/tmp/alethra-test-logs/full-suite-20261117-console.log`（63,193 bytes、sha256 `4ee969b1208c6d7ded54e73b80d1301bf040762b3dab0e9d6d75ed91d214ff55`、NUL=0）；lane `/tmp/alethra-test-logs/full-suite-20261117.log`（61,978 bytes、sha256 `4e4100b721968b7940d030059ebf36e4b1c706d860551daf1143fafc65b7dade`、**NUL=4733**）| `shasum -a 256`＋`LC_ALL=C tr -d -c '\000' < f \| wc -c` 重算 |

唯一的 FAILED 節點身份（不是新缺陷，與 §11.10-1、§12.4 同一條）：
`tests/test_registration_risk_acknowledgement.py::test_an_acknowledged_registration_records_one_audit_event`（console 日誌第 85 行 `FAILED`）。

**7.2 混合狀態宣告（owner 一 最後一點：實際被改動就如實標）**

1. **被測程式碼在 33 分鐘內未被改動**：HEAD 全程 `5a34e3fe`，且本節撰寫前 `git status --porcelain` 仍報同一 dirty 旗標。
2. **但共享 log 路徑在量測期間被污染**：一個並行的 focused run 用了同一個 `ALETHRA_LANE_LOG`，`tee` 的首次寫入從 byte 0 截斷，把 lane 檔頭改成另一條 run 的 `34 passed in 23.19s`，並留下 4733 個 NUL（所以 `grep` 把該檔當成 binary）。權威結果因此**以 console 副本為準**；lane 副本保留為缺陷現場，**不刪、不修**（owner 五「不刪現場」）。兩份的可核對部分實測相同：`^SKIPPED` 各 183 條、FAILED 行同一節點（lane 檔需 `grep -a`）。
3. 因此本輪結果的正確身分是：**固定被測碼（HEAD 5a34e3fe、`.py` 產品碼與既有測試斷言未動）＋共享 log 路徑受污染**，不是「完全乾淨的 authoritative」。7.3 的修正之後，同一缺陷在程式面上已不可能重演。
4. 量測**之後**才有 7.3 的三個修正（lane／budget 腳本＋該檔合同），以及 `tests/skip_budget.json` 的重釘。所以「7.1 的 1 failed／3298 passed／183 skipped」是**修正前樹**的數字；它們不會因為那些修正而改變（那些檔案只新增合同節點，不改任何被跳過或已通過的產品測試），但下一輪全量的 collected 一定不同（7.4 已把這件事做成會拒絕的 gate）。

**7.3 本輪抓出並已修正至源頭的三個 gate 缺陷（不是產品缺陷）**

| # | 實測徵兆 | 根因 | 修正 | 回歸證據 |
|---|---|---|---|---|
| 1 | 全量跳過上限**靜默關閉**：這明明是全量 run，尾端卻印 `this run selected specific tests, so the whole-suite ceiling does not apply` | 選取偵測逐字元比對參數，把 `-p no:warnings` 的**值** `no:warnings` 當成「點名的測試路徑」 | `run_tests.sh` 改以「該參數（去掉 `::node` 之後）在本檔是否存在」判定：`[ -e "${arg%%::*}" ]` | 三條 CLI 實測：舊式 `-p no:warnings` → 不再被視為選取；`tests/x.py` 真路徑 → 仍被視為選取；空選取 → 仍 exit 2 拒絕 |
| 2 | `-q` 日誌沒有 collected 數 ⇒ `skip_budget.py` 的 `collected_from()` 找不到 `selection on this tree:` 行 ⇒ fail-closed，上限無從套用 | 身份只印到 console，日誌本身不自述身分 | `emit_identity` 把 command/cwd/git/selection 4 行**寫進日誌檔本身**（先寫 per-process `.partial`，再 `mv -f` 發布 ⇒ 同一路徑的兩條 run 不再互相截斷）| 新增合同 `test_the_lane_log_carries_the_identity_without_needing_the_console`：跑 `sqlite -q <stub>`，斷言**日誌檔內**恰各 1 行 `command: `/`cwd: `/`git: <40 字元 SHA>`/`selection on this tree:`（含 `2 tests collected`）|
| 3 | **我自己的修正引入的回歸**：`command:` 行變成不帶參數（`run_tests.sh sqlite ` 尾端空白）| 把 `echo "command: $0 $MODE $*"` 搬進 function 後，`$*` 變成**該函式**的參數 | 在頂層捕獲 `LANE_ARGS="$*"`，函式內只用 `$LANE_ARGS` | 由**既有**契約 `test_the_lane_records_the_command_cwd_and_revision_it_ran` 抓紅並留存現場：`/tmp/alethra-test-logs/lane-selftest-fixed.log`（sha256 `68ede91845bdc881e8efc360849859788312b973dde2d10b02ccece8484eae48`）→ `1 failed, 21 passed in 9.05s`、`tests/test_lane_entry_contract.py:110: AssertionError`。這是 gate 按設計起作用，不是運氣 |

**7.4 上限重釘的可執行證明（先拒絕、後生成、再放行）**

1. 舊 budget（`collected=3462`）面對本輪日誌：`refused: this whole-suite run collected 3479 tests but the budget pins 3462 …` ⇒ **RC=2**。實測方式記一筆教訓（同一失誤第二次發生）：第一次用 `… | tail -12; echo RC=$?` 拿到的是 `tail` 的 0，改成導向檔案再取 `$?` 才拿到真正的 2。
2. `write-budget` 從真日誌生成：`wrote tests/skip_budget.json: collected=3479 total=183 pg_only=164 live_stack=18 other=1`，檔內 `source_log=/tmp/alethra-test-logs/full-suite-20261117-console.log`、`source_log_sha256=4ee969b1…`、`source_git_line=5a34e3fe… (working tree dirty…)`、`generated_by="backend/scripts/skip_budget.py <lane log> write-budget"`（原寫的是 `--write-budget`，一個跑不起的形式，已修正）。
3. 本節撰寫前重跑 `check`：RC=**0**、`within budget; headroom pg_only=0 live_stack=0 other=0 total=0` ⇒ 之後**任何一條新增 skip 都會讓對應類別溢出**，沒有隱藏餘量。
4. 新增合同節點後的即時後果（不是推算，是剛跑的）：`run_tests.sh sqlite --collect-only -q -p no:warnings` ⇒ `3480 tests collected in 3.13s`、**RC=2**、`refused: this whole-suite run collected 3480 tests but the budget pins 3479: the selection changed … Re-measure and regenerate the budget from a real lane log.`（日誌 `/tmp/alethra-sqlite-test-dir/alethra-lane-7086.log`）⇒ 下一輪全量必須**先跑完整套、再從自己的日誌重釘**，不能沿用 3479。

**7.5 修正後的定向回歸**

`bash ./backend/scripts/run_tests.sh sqlite tests/test_lane_entry_contract.py tests/test_postgresql_only_marks.py tests/test_env_hygiene_contract.py -p no:warnings --tb=line`
⇒ `65 passed in 37.44s`、RC=0（23＋16＋26；lane 檔 `ef4766ed6c02e650d7dd88fe2b059f8fbc628300e994770a7c0742bdef8c85b8`，前 4 行即身份與 `selection on this tree: 65 tests collected`）。分類也正確：這次**真的**給了路徑，所以 `… not enforced` 是對的判定，而非 7.3 缺陷 1 的誤判。

**未覆蓋（誠實登記）**：缺陷 1 的修正**沒有** shell 級合同測試。忠實的斷言需要一次全量（33 分鐘）才有意義；把 `[ -e … ]` 用 Python 重寫一遍只是偽覆蓋。所以以「缺陷現場日誌（console 尾行）＋ 7.3 表的三條 CLI 實測」代替，並列為已知缺口，不宣稱已覆蓋。

#### 8. 本節不是什麼

1. 7.1 **不是產品 PASS**：1 條真失敗仍在原處，183 條 skip（164 PG-only／18 live-stack／1 other）從未在 PostgreSQL 上執行；SQLite 全綠也不是完整平台 PASS。
2. 7.4 的上限只證明「skip 不增加、選集不靜默漂移」，**不證明**被跳過的那 164 條能力存在。
3. 7.3 的三個修正只動 `run_tests.sh`、`skip_budget.py`、`skip_budget.json`（生成物）與 `test_lane_entry_contract.py`（新增合同）：**未動任何 `app/` 產品碼，未動任何既有測試的斷言或順序，未新增 module-level skip**。
4. 本節不新增任何批准：PG／Docker 啟動、role/DB 建立、migration、Chrome／服務啟動、SIGKILL、憑證輪替、歷史重寫、commit／push 仍全部待批。
5. exit code 是 7.1 表中唯一「以推論補齊」的欄位，已標明其來源與限制。
6. 3479 vs 3462 的 ＋17 逐檔對帳在 #### 6，本節不重複計算；本節新增的 ＋1（→3480）是 7.3 缺陷 2 的合同。

---

## 12. 記錄用：本輪**只測量、未修改**的產品判定（10 項）

owner 2026-11-16 二：「內部可以排序，但不要每完成一個 helper 或一組測試就停止」「不要再把每一個相關缺陷拆成一輪 owner 請示」；同時五：「未取得工具或 owner 所需確認前，不執行……」。這 10 項落在兩者之間：**修改它們都會改變被測產品或共享 DB 的行為**，因此本節把它們一次交付為**記錄＋精確選項＋每個選項的確切副作用**，不是逐條開工請示，也不是已批准改動。每一項的檔案與行號都在本輪（HEAD `5a34e3fe`、工作樹凍結期間）重新讀過，不是引用舊記錄。

**本節的讀法**：「目前實際行為」= 程式碼寫死了什麼；「因此不能宣稱」= 能力矩陣中該列必須維持 NOT RUN／失敗，即使離線 gate 全綠；「判定」= owner 要選的分支，選完才進入代碼輪。

### 12.1 paper trading 的終態沒有 audit event

- 實測位置：終態寫入在 `backend/app/services/paper_trading.py:362`（worker 確認停止後的 `cancelled`）、`:892`（replay 最終階 cancel 分支）、`:959`（`completed`）、`:1113`（formal cancel 的 `cancelled`）。
- 目前實際行為：`backend/app/services/paper_trading.py` 與 `backend/app/api/paper_trading.py` 兩個檔案都不引用 `AuditEvent`，也不呼叫 `emit_event`（實測 `grep` 只出現兩註解：`:343`、`:420` 的「for audit」註記，無寫入）。同一 repo 的 portfolio 層**有**事件：`backend/app/api/paper_portfolio.py:510`（`portfolio_created`）、`:667`／`:742`（`order_rejected*`）、`:765`（`order_created`）、`:937`（`decision_recorded`）、`:1017`（`outcome_evaluated`）、`:1065`（`override_requested`）、`:1105`／`:1141`／`:1182`（`portfolio_paused`／`resumed`／`retired`），表本身在 `backend/app/models/audit_event.py:10`。
- 因此不能宣稱：「run 的 completed／cancelled／failed 有可追溯終態」。有人、有時間、有 before/after 的那層只覆蓋 portfolio 與 order，不覆蓋 run 生命週期；矩陣「durable admission、quota、終態與 audit」這列對 paper lane 仍是**部分實作**。
- 判定（二選一或皆拒）：
  1. 在四個終態寫入處各加一條 `emit_event(...)`，與狀態 UPDATE 同一 transaction → 副作用：每次 run 結束多一列 `audit_events`，既有斷言「run 結束後 DB 狀態等於這些表」的清理測試需同步更新。
  2. 明確把 audit 範圍縮為「portfolio／order 層」，在文件與 UI 說明 run 終態不在 audit 內 → 副作用：無程式改動，但「終態與 audit」列必須寫成 NOT RUN，不得寫成已完成。

### 12.2 paper trading 完全不在 quota reserve／settle 協議內

- 實測位置：兩階段協議定義在 `backend/app/auth/entitlements.py:19-27`（docstring：reserve → settle／release），實際插入 reservation 在 `:346-353`；使用者為 `backend/app/api/ai_assistant.py:512`、`backend/app/api/robustness.py:203`／`:393`、`backend/app/api/personal_models.py:562-580`、`backend/app/api/ml_training.py:373-476`、`backend/app/api/inference.py:15`。
- 目前實際行為：`backend/app/api/paper_trading.py`、`backend/app/services/paper_trading.py` 對 `reserve_quota`／`settle_reservation`／`release_reservation`／`entitlements` 的引用數＝**0**（實測 `grep` 無命中）。
- 因此不能宣稱：產品契約的「配额」與「最多 10 個 skill account」套在 paper trading 上。研究／ML／inference 會計量，開 paper run 不會——一個用戶可以無限並行 paper run 而不動到任何計數。矩陣「durable admission、quota」這列在 paper lane 是**未實作**。
- 判定：
  1. paper run 建立走同一 `reserve_quota`／settle／release（需要一個新的 `quota_type`，並決定取消與失敗時 release 的歸屬）→ 副作用：既有 paper 建立測試全部要加 quota 前置；migration 不需要（`quota_reservations` 表已在），但 `QUOTA_TYPE_MAP`（同檔）需新增鍵。
  2. 在產品上明確宣告 paper trading 不計量（並把該事實寫進 UI／文件）→ 副作用：無程式改動，但矩陣該列要標為「刻意不適用」，不是「已完成」。

### 12.3 `status='reserved'` 的 reservation 永不過期（懸掛占用）

- 實測位置：占用總和 `_get_reserved()` 在 `backend/app/auth/entitlements.py:175-182`——條件只有 `user_id`／`quota_type`／`status == "reserved"`，**無時間界限**；另一處同條件在 `:320-326`（`reserve_quota` 檢查可用量）。釋放只能由仍活著且仍持有 id 的呼叫端觸發：`release_reservation()` 定義於 `:452-475`。回報端也把它算成已用：`backend/app/services/billing.py:546-555`（註解自己寫「In-flight reservations consume allowance exactly as they do in reserve_quota()」）。
- 目前實際行為：全 repo 沒有把過舊 `reserved` 轉為 `released`／`expired` 的掃描（實測查 `expire`／`stale`／`orphan`／`settled_at`：`billing.py:390` 處理的是 stale **webhook**，與 reservation 無關）。因此請求在 reservation commit 之後、settle／release 之前死掉（程序被殺、worker 未重試、client 斷線），該單位會**永久**占用額度，且在 `/ml/entitlement` 與 billing 回報裡顯示為已消耗。
- 因此不能宣稱：「request-loss 之後額度不會被錯誤消耗」。#144 關掉的是 idempotency 重複計費，不是這一條。矩陣「request-loss idempotency」與「中斷／恢復政策」都受此項影響。
- 判定：
  1. 加一條有時間界限的過期規則（誰跑、門檻多久、到期後是 `released` 還是新增 `expired` 狀態）→ 副作用：這是**會動共享 DB 資料**的行為變更，需要 PostgreSQL 環境實際驗證；也必須決定過期與「run 其實還在跑」如何區分（`research_run_id` 連結在 `backend/app/services/research_runs.py:805-825` 已提供一條可判定路徑：已連結的 unit 不應由時間規則釋放）。
  2. 保持現狀但把「reserved 永不釋放」寫成已知限制並在 UI 顯示 `reserved` 明細 → 副作用：無程式改動，用戶需自行以支援途請求釋放（目前也沒有該途徑，仍需決定）。

### 12.4 provenance：`unknown` 是正確記錄，測試的 `human` 是錯誤期待（全套唯一殘留 FAILED）

- 實測位置：失敗節點 `tests/test_registration_risk_acknowledgement.py::test_an_acknowledged_registration_records_one_audit_event`，斷言在 `backend/tests/test_registration_risk_acknowledgement.py:89`（`assert event.actor_type == "human"`）。權威日誌 `/tmp/alethra-test-logs/full-suite-20261116.log:855` 記為該輪**唯一** 1 條 FAILED。
- 目前實際行為：`backend/app/api/auth.py:89` 寫 `actor_type=request_actor_provenance(request)`；`backend/app/auth/provenance.py:30-37` 只在請求憑證帶 `pwd` 認證方法（`:27`）時回 `human`，否則 `unknown`，且模組文件 `:14-16` 明文「Unknown stays unknown: it is never upgraded to `human` by inference」。註冊當下**沒有已驗證的登入 session**（用戶才被建立），所以 `unknown` 是依憑證判定的結果。
- 因此不能宣稱：可以「把產品改成讓測試變綠」。依長期不變量（provenance：絕不宣稱超過憑證所證明者），把 production 推向 `human` 會直接違反該檔自己的契約。
- 判定：
  1. 改測試期待為 `unknown`，並保留一條正控制（經密碼登入後寫出的事件才是 `human`）→ 副作用：該節點轉 PASS，但語意改變必須寫進測試註解，否則下次又被人「修回去」。
  2. 讓註冊流程真的經過一個已驗證的密碼憑證（例如先登入再確認告知）→ 副作用：這是**產品流程變更**，會影響註冊端點契約與前端步驟，需另行批准。

### 12.5 `get_status` 在 SQLite 上是 500，而且會把內部例外文字回傳（本輪新量測）

- 實測位置：`backend/app/services/paper_trading.py:1552-1555` 用 `text("SELECT * FROM paper_trading_runs …")` 取得列，`:1575` 呼叫 `run.start_date.isoformat()`；欄位宣告在 `backend/app/models/paper_trading_run.py:24`（`DateTime(timezone=False)`）。路由端 `backend/app/api/paper_trading.py:178-183` 有一條 `except Exception` → `HTTP_500` 且 `detail["message"] = str(exc)`。
- 目前實際行為（本輪以**零副作用**的 in-memory 反例量測，非推論）：經 `text()` 的回傳列不套用 ORM 型別轉換，SQLite 驅動交回**字串** `'2026-11-16 12:00:00.000000'`，`end_date` 同為 `'2026-11-16'`；對它呼叫 `.isoformat()` 得到 `AttributeError: 'str' object has no attribute 'isoformat'`。PostgreSQL（psycopg2）交回的是 `datetime`／`date`，同一行程式在 PG 上正常。
- 因此不能宣稱：(a) 該端點在「非 PG 後端」可用；(b) 500 回應体是安全的——它把內部例外文字（此例為 Python 屬性錯誤訊息）放進 `detail.message`，與 `backend/tests/test_error_response_safety.py` 對其他端點的約定不一致。
- 判定：
  1. 把 `:1552` 的 `SELECT *` 改為具型別查詢（ORM／`db.get(...)` 或 explicit columns），並在路由端把非預期例外收斂成不帶內部文字的通用訊息 → 副作用：同時修正 500 根因與洩漏面；需一併檢查使用同一 `SELECT *` 模式的姊妹路徑。
  2. 宣告 paper trading 僅支援 PostgreSQL（把 SQLite 視為不該出現的部署）→ 副作用：需在啟動入口 fail-closed（不是靠測試跳過），且本輪 sqlite lane 的綠燈不再被解讀為該能力可用。

### 12.6 測試用的 target guard 住在測試檔，不在產品；身份字面值散在 9 處

- 實測位置：`verify_test_target()` 唯一定義在 `backend/tests/test_target_guard.py:205`（被 `backend/tests/test_paper_trading_cancel_atomic.py:1803`、`:1818`、`:1833`、`:1848`、`:1863`、`:1881`、`:1896`、`:1911`、`:2578` 匯入使用）。產品側只有 resource ownership：`backend/app/api/paper_trading.py:33` `_verify_run_ownership`。DB／role／host／port 的字面值散在：`backend/test_safety_gate.py:26`、`backend/tests/test_safety_gate_negative.py:90`、`:120`、`:203`、`:209`、`:216`、`:222-223`、`:239`、`:245`、`backend/tests/test_postgresql_only_marks.py:167`、`:345`、`backend/tests/test_paper_trading_cancel_atomic.py:1808-1906` 各列。
- 目前實際行為：「連到哪個 DB 才算被批准」由測試程式自己判定，且同一身份在多個檔案重複硬編碼；真正的執行期入口（app 啟動、migration、服務）沒有同一條檢查可用。
- 因此不能宣稱：「target guard 是產品的安全邊界」。它是**測試側的自律**，換一個入口（直接跑腳本、手動 alembic、另一支服務）就不經過它。矩陣「可重現環境」與「權限與隔離」的該部分要標為受阻。
- 判定：
  1. 把批准目標（DB 名／role／host／port）收斂成**單一設定來源**（一個 config 鍵或一個 allowlist 檔），測試與運行入口都讀它 → 副作用：上述 9 處要改寫；§11.9 已聲明的約束仍在——**不得因此放寬** `FORBIDDEN_DATABASES`／`ALLOWED_INSTANCES`，收斂必須是同嚴格度或更嚴格，且需一條「設定錯誤時 fail-closed 而非預設放行」的回歸證明。
  2. 維持現狀並在文件寫明「target guard 僅約束測試途徑」→ 副作用：無程式改動，但任何「實戰不會連到生產 DB」的宣稱都要降為測試范围。

### 12.7 3 條秘密洩漏測試的 except 子句過寬，在 SQLite 上是**空斷言**

- 實測位置：`backend/tests/test_target_guard.py:1239`、`:1266`、`:1293` 皆為 `except (TargetGuardError, OperationalError, Exception) as e:`（實測該行文字：`TargetGuardError`、`OperationalError` 與 `Exception` 在同一個 tuple 裡——後者吃掉前兩者之外的任何異常）。
- 目前實際行為：斷言只做「訊息裡不含該密碼／敏感參數」。本輪量測到：SQLite 下這些呼叫拋出的是 SQLAlchemy 的 `ArgumentError: Invalid SQLite URL: sqlite://user:***@…`——驅動**自己把密碼段遮成 `***`**，因此「不含原值」永遠成立，測試在與 target guard 無關的路徑上通過。（實測：`"FakePassword123" in str(msg)` → `False`。）這是 §11.10 第 4 點那 2 條 PASSED→skip 的同一機制。
- 因此不能宣稱：「已證明 target guard 不會洩漏密碼」。現有三條只證明「例外訊息不含我給的字串」，而在 SQLite 上該條件由驅動的遮罩行為而非 guard 保證。
- 判定：
  1. 把三條改為 fail-closed：先斷言**確實拋出了 `TargetGuardError`**（而不是任何 Exception），再斷言其訊息不含秘密；其他異常不得算通過 → 副作用：該改動必須在原語意下重新量測（SQLite 可能直接變 FAILED，那時要的是標 PG-only 或補一條離線可跑的 guard 正控制，不得用整檔 skip 移走）。
  2. 保留寬 except，但新增一條「guard 被繞過時測試必紅」的自我證明 → 副作用：需設計一條能真正注入繞過的回歸，否則又是自我宣稱。

### 12.8 並行配額測試的 teardown 用 `Base.metadata.drop_all`，且吞掉所有錯誤

- 實測位置：`backend/tests/test_quota_concurrency.py:53`（`Base.metadata.create_all(engine)`，上方註解自己寫「schema is what these tests are about」）、`:55-60`（`try: Base.metadata.drop_all(engine)` / `except Exception: pass`，註解為「Ignore teardown errors (e.g., circular dependencies)」）。`Base` 來源：同檔 `:25` `from app.database import Base`。
- 目前實際行為：`Base.metadata` 是**全域** metadata，`drop_all` 對該連線可見的全部表下手，不只是本 fixture 建立的 4 張；發生失敗（含外鍵循環）時完全靜默。
- 因此不能宣稱：「PG 輪的清理不傷害其他測試」，也不能宣稱「residue 為零」。這直接抵觸長期規則（cleanup 不得跳過、須以獨立連線驗證零殘留），而且與 §11.10 第 3 點那 4 條 `test_quota_concurrency.py` skip 疊加：它們在 PG 上真的跑時，**會先抹掉同一 DB 裡其他模組的表**。
- 判定：
  1. 改為只 drop 本測試建立的表（具名 `drop(tables=[...])`）、teardown 錯誤上浮、並以獨立連線斷言該 test DB 只剩預期表 → 副作用：需要 PG 環境才能真正驗證；在本輪（PG 未批准啟動）只能寫成代碼＋NOT RUN。
  2. 讓每個 PG 並行測試用**專屬 DB**（而非共享 test DB）→ 副作用：改變 `ALETHRA_PG_TEST_URL` 的契約（需建庫權限，屬 owner 五「不建 role／DB」未批准範圍），且 §11.9 的 target-guard 負控制需同步重寫。

### 12.9 `is_production_like` 只在兩個精確字串上為真；其他 APP_ENV 值會**靜默關掉**整段生產加固檢查

- 實測位置：`backend/app/config.py:48`（`app_env: str = "development"`，無 `Literal`／validator／大小寫正規化）、`:153-154`（`return self.app_env in ("production", "staging")`）、`:168-169`（`if not self.is_production_like: return self`）。另有六處 `if self.is_production_like or self.app_env != "development":`——`:239`、`:282`、`:302`、`:340`、`:370`、`:384`。
- 目前實際行為：
  1. `JWT_SECRET_KEY` 弱預設／長度、`CORS_ORIGINS='*'`、provider 與 live payments 等檢查（`:169-215`）只在 `APP_ENV` **正好等於** `production` 或 `staging` 時執行。寫成 `prod`／`Production`／`PREPROD`／未設定但設了正式 DB 時，全部跳過——這是 fail-**open**，與 owner「不降 target guard」的精神相抵触的同類構造。
  2. 六處的 `is_production_like` 是**死項**：第二個條件（`app_env != "development"`）已涵蓋第一個（production／staging 必然 `!= "development"`），該 property 在這些分支裡永遠不可能是決定者。
- 因此不能宣稱：「環境設定錯誤會被啟動拒絕」。矩陣「env check」該列需維持受阻。
- 判定：
  1. 把 `app_env` 收斂為受限集合（未列出的值直接 `ValueError`），加固檢查改寫為「凡不是明確 `development`／`test` 皆套用」→ 副作用：**所有現有部署檔**需同步；萬一有人用 `productionn` 這類拼錯值，行為從「靜默跳過」變成「啟動失敗」，這是可預期的中斷，需在發布前告知。
  2. 保持 `app_env` 自由字串，但把六處死項刪除、並將 `:168` 的條件改為 fail-closed 黑名單 → 副作用：修改幅度較小，但拼錯值仍可能被當成 development（需決定是否接受）。

### 12.10 replay 最終階 cancel 分支回報 `cancelled`，但沒有檢查寫入是否落地

- 實測位置：`backend/app/services/paper_trading.py:874-900`——`:874` `SELECT … FOR UPDATE` 鎖列，`:885` `if locked_run and locked_run.cancel_requested:`，`:887` `db.rollback()`，`:889-896` `UPDATE paper_trading_runs SET status = 'cancelled' … WHERE id = :id AND status = 'running'`（**未讀 rowcount**），`:897` `db.commit()`，`:898-900` 回傳 `{"status": "cancelled", …}`；路由 `backend/app/api/paper_trading.py:232` 以 `response_model=ReplayResponse`（欄位定義 `:106-113`）直接送出。
- 目前實際行為：條件式 UPDATE 的謀詞是 `status = 'running'`。若該列已被其他寫入者轉為 `failed`／`cancelled`／`completed`，UPDATE 媒合 0 列、狀態不變，但函數仍然回傳 `status="cancelled"`，客戶端收到一個**未被任何一行資料證實**的終態。同一檔的完成分支已有正確寫法（#156：`:978-984` 讀 `completion.rowcount == 0` 就 rollback 並上拋 `InvalidStateError`）。
- 因此不能宣稱：「取消的終態一律可追溯到實際寫入」。矩陣「明確的取消／中斷／恢復政策」與「可追溯終態」在 replay 取消路徑上有一條未閉合的差異；且 `ReplayResponse` 的 `status: str` 無受控集合，無法區分「我取消了」與「早就已是其他終態」。
- 判定：
  1. 完成分支的寫法搬到取消分支（檢查 rowcount；0 則以實際狀態上拋，不回報 cancelled）→ 副作用：該路徑的回應從 200 變 409（`app/api/paper_trading.py:264-268` 已有對應處理器），前端需處理新的 conflict 狀態；既有假設「取消最終階必得 cancelled」的測試需同步修正。
  2. 保留回傳，但把 `ReplayResponse` 擴為同時帶「請求結果」與「DB 實際終態」兩個欄位 → 副作用：API 契約變更（前端與匯出需讀新欄），需一條「兩欄不一致時 UI 不得宣稱已取消」的回歸。

### 12.11 本節不是什麼

1. **未修改任何一項**。以上 10 處在本輪沒有產生一行產品或測試代碼改動；工作樹在量測期間凍結（§11.10 第 1 點的身分快照即為證明）。
2. **不是產品 PASS**，也不是 PG 結果：10 項裡有 4 項（12.3、12.5、12.8、12.10）的最終驗證都需要 PostgreSQL 或已運行的服務，而那些操作 owner 尚未批准。
3. **不是新的批准範圍**：本訊息不新增 PG／Docker 啟動、建 role／DB、migration、服務／瀏覽器啟動、SIGKILL、憑證輪替、歷史重寫、commit／push 或部署的授權。
4. **不是逐缺陷開工請示**：10 項一次交付，每項附「精確改法＋該改法的確切副作用」，owner 可以在同一份回覆裡選完所有分支；未被選擇的項維持記錄狀態，不會再被拆成新一輪。
5. **未輸出任何秘密值**：12.7 提及的密碼為測試合成佔位值，本節只引用其在既有日誌中已出現的遮罩形態，不重 print 原值、長度或摘要。

---

## 13. 證據帳本修正（owner 2026-11-17 一；追加，不改寫 §11.7～§11.10 與 §12 原文）

owner 一：「目前回報：3479 collected，但 3298 passed + 183 skipped + 1 failed = 3482。直接使用現存原始 pytest summary、JUnit、collection 清單與 census 輸出查明：哪些數字來自不同執行、是否含重試／重複紀錄、classifier 是否錯配或重算。」

**修正的是陳述方式，不是任何一個數字。** 本節每個值都在撰寫當下對**現存**產物重算，命令逐字列出，任何人在同一檔案上重跑同一條 `grep` 即可覆核。所有量測對象都是**歷史那一輪**（`/tmp/alethra-test-logs/full-suite-20261117-console.log`），不是目前工作樹。

### 13.1 3479 與 3482 都對，但它們計的是不同的東西

| 量 | 實測值 | 計數物件 | 出處（逐字可重算） |
|---|---|---|---|
| collected | **3479** | **item（節點）** | `grep -a collected full-suite-20261117-console.log` → `selection on this tree: 3479 tests collected in 3.19s` |
| summary 三欄 | **3298 passed / 1 failed / 183 skipped** | item ＋ **collection 級 skip unit** 混在同一行 | `tail` → `1 failed, 3298 passed, 183 skipped in 1987.33s (0:33:07)` |
| `^SKIPPED` 行 | **183** | skip unit | `grep -ac '^SKIPPED'` → 183 |
| 其中 collection 級（整檔閘門） | **3** | 非 item | 該 3 行的位置是模組自身第 21／30／25 行的 `pytest.skip()`：`tests/test_crypto_bar_harvest_pg.py:21`、`tests/test_crypto_paper_pg.py:30`、`tests/test_strategy_study_timestamps_pg.py:25` |
| 證明這 3 條不在 3479 內 | **0 個 node id** | item 清單 | 對 3479 行的收集清單 `grep -c test_crypto_bar_harvest_pg / test_crypto_paper_pg / test_strategy_study_timestamps_pg` → **0, 0, 0**（整檔在 collection 就被拿走，從不產生 item） |
| item 級 skip | **180** | item | 183 − 3 = 180，與 `grep -a '^SKIPPED' \| grep -vE '^SKIPPED \[1\] tests/[a-z0-9_]+_pg\.py:[0-9]+:' \| wc -l` → 180 相同 |

因此兩個算式同時成立，而且只差的那 3 就是那 3 條整檔閘門：

- **item 面**：3298 passed ＋ 1 failed ＋ 180 skipped ＝ **3479** ＝ collected node id 數。
- **outcome unit 面**：3479 item ＋ 3 collection 級 skip ＝ **3482** ＝ summary 三欄之和。

`run_tests.sh` 的帳本節就是按這個對象拆分印出的（`skip categories ... total N = item X + collection-level Y`），所以同一雙計數在**目前的 lane** 裡不會再被讀成單一排總數；歷史那輪沒有這段輸出，因為它跑在 #159 落地前。

### 13.2 三個被owner點名的可能原因，逐一量測後排除

| 假設 | 實測 | 判定 |
|---|---|---|
| 數字來自**不同執行** | collected 行與 summary 行在同一檔、同一 run（console 日誌第 10 行與尾端），檔 sha256 `4ee969b1208c6d7ded54e73b80d1301bf040762b3dab0e9d6d75ed91d214ff55` | **不成立**：同一輪 |
| 含**重試／重複紀錄** | `grep -acE '^FAILED'` → **1**（唯一那條 provenance 節點）；`grep -ac 'rerun\|xdist'` → **0**（未裝 rerunfailures、未開並行 distribution） | **不成立** |
| **classifier 錯配或重算** | census 三類 `pg_only=164 / live_stack=18 / other=1`，和為 **183** ＝ `^SKIPPED` 行數；`backend/tests/skip_budget.json` 現值 `collected=3479 total=183 pg_only=164 live_stack=18 other=1`，`provenance.source_log_sha256` 即上列 console。**勘誤（2026-11-17 12:1x 重釘，見 §15.3）**：該檔現值為 `collected=3548`、`source_log` 改為該 run 的 `pytest.log`（sha `1ecd8014…`），`total=183 pg_only=164 live_stack=18 other=1` 四項未變；上列 3479 是本節撰寫當時的檔內值，保留不改寫 | **不成立**：分類加總精確等於 skip unit 數，沒有重複計數 |

三個都排除之後，剩下的解釋只有 §13.1 那個：**計數物件不同**。這是我的陳述缺陷（把 unit 總數與 item 總數並列卻未標對象），不是Reporter 算錯，也不是資料不一致。

### 13.3 撤回的是「節點級可對帳」這層讀法，保留總量恆等式

1. **保留**：§11.9.2、§11.10.2 的「算術閉合」作為**總量**陳述仍然正確（3420＝3415＋5；3465＝3462＋3；3482＝3479＋3），本節不努那些行，只在此標明其對象是 *outcome unit*，不是 *node*。
2. **撤回**：這些舊節**不能**被讀成「每條 skip／每個結果都已綁到确切 node id」。歷史那輪根本沒有可綁定的產物：
   - `/tmp` 下**不存在**該 run 的 `junit.xml` 或 `collect.txt`（實測：`find /tmp -name junit.xml` 於 2026-11-16 18:00～11-17 00:00 區間 **0 筆**；沒有任何 junit 帶 `tests="34xx"`；無 `collect.txt` 的 id 數超過 500）。
   - 日誌的 `SKIPPED` 行印的是 **skip 語句的位置**，不是節點 id：183 個 unit 只對應 **166 個唯一位置**；其中 `tests/postgresql_only.py:154` 一個位置就蓋了 **12** 條、`:119` 蓋了 **7** 條 ⇒ 這 **19 條**連「屬於哪一支檔」都無法由日誌區分。
   - 其餘 164 個位置唯一，但位置仍是「陳述在哪一列」而非「哪一個節點」。由 file:line 反推節點身分屬於推測映射，依長期規則（「不要用新舊行號位置推算節點身份」）**不得**登記為已核實歷史結果。
3. **因此**：歷史那輪的 183 條 skip 的節點身分**全部標 UNRESOLVED**，包括 §11.10 第 3 點那張 changed-to-skip 表中由位置推得的列——該表作為「語句與前提的清單」仍可用，作為「節點帳」不可引用。

### 13.4 確切的 UNRESOLVED 清單（不藏在總數後面）

| # | UNRESOLVED 項目 | 數量 | 為什麼無法核實 |
|---|---|---|---|
| U1 | 歷史 run 的每一條 skip 對應哪個 node id | 183（含 161 條位置唯一者） | 無 junit／collect 產物；日誌只給 skip 語句位置 |
| U2 | 其中連所屬模組都無法由日誌判定的 | 19 | 共用 helper 呼叫點（`postgresql_only.py:154` ×12、`:119` ×7） |
| U3 | 歷史 run 的 exit code | 1 | 見 §13.6，未單獨保存；不因「有 1 條 failed 所以必為 1」補寫 |
| U4 | 3482 這個和數在舊文裡被當成 item 總數的所有引用 | 全部 | §13.1 的對象釐清之前，那些句子無對象；一律改讀為 outcome unit |

從本輪起，節點身分**只有**兩個來源會被寫進帳本：run 目錄的 `collect.txt`（每行一個 node id）與 `junit.xml` 的 `classname`＋`name`。兩者在目前 lane 每次 run 都產生——實測一例：run `sqlite-20261117T020355Z-61248` 的 junit 帶 `classname="tests.test_quota_concurrency" name="test_this_module_runs_no_schema_ddl"` 這類成對欄位，可直接還原 node id。收集清單裡有 3 條參數化 id 內含空白（`grep -vnE '^[^[:space:]]+\.py(::[^[:space:]]+)*$' ids-post159.txt` → 第 437、1127、3158 行），這是 pytest 對 param id 的正確輸出，不是資料瑕疵；對帳時以整行字串比對，不按空白切分。

### 13.5 破損日誌保留，且可證明沒有被覆寫

owner 一：「保留損壞日誌，不覆寫、不刪除。舊 full-suite 標為歷史量測。」重測結果與 §11.10 第 7.1 節當時記下的值**逐欄相同**，因此該檔自量測以來未被改動：

| 檔案 | bytes | NUL | sha256 | 身分 |
|---|---|---|---|---|
| `/tmp/alethra-test-logs/full-suite-20261117.log` | 61,978 | **4,733**（`LC_ALL=C tr -d -c '\000' < f \| wc -c`） | `4e4100b721968b7940d030059ebf36e4b1c706d860551daf1143fafc65b7dade` | **缺陷現場**：被並行 focused run 以 `tee` 從 byte 0 截斷者。保留、不刪、不修 |
| `/tmp/alethra-test-logs/full-suite-20261117-console.log` | 63,193 | 0 | `4ee969b1208c6d7ded54e73b80d1301bf040762b3dab0e9d6d75ed91d214ff55` | 該輪可核對部分的主來源（skip_budget.json 的 `source_log`） |
| `/tmp/alethra-test-logs/ids-post159.txt` | 3,479 行 | — | `aaad93e1283dfc2fce71ed39c3814db46223671fbdebb0dc017e6f105b75fe21` | 該輪 item 清單（由 lane 產生）；§13.1 的 0-node-id 比對即對它做 |
| `/tmp/alethra-test-logs/full-suite-20261116.log` | 109,947 | — | （未在本節重算） | 更早一輪的歷史量測，同樣不覆寫 |

**「舊 full-suite 標為歷史量測」的具體含義**：§11.9／§11.10／§13.1 那幾組數字描述的是 HEAD `5a34e3fe` 加當時 dirty 集合的那棵樹。目前工作樹已在其後加入 §13.7 列出的改動（產品碼與測試碼都有），所以**任何**那些數字都不再是「目前工作樹的最終驗收」；最終驗收只由 #169 那一次固定狀態全量產生，且必須自帶 run 目錄＋junit＋exit-code.txt＋收集清單。

### 13.6 exit code 維持「未直接保存」

owner 一：「exit code 維持『未直接保存』，不冒充目前工作樹最終驗收。」實測：console 與 lane 兩份日誌都沒有單獨的 exit code 記錄檔，`/tmp/alethra-test-logs/` 內亦無 `exit-code.txt`。因此歷史那輪的 exit code **就是未知**，不是 1。我可以說「lane 以 `exit "$RC"`、`RC=${PIPESTATUS[0]}` 結束，且有合同測試驗證 1 failed ⇒ 傳導 1」，但那是對**目前** lane 代碼的描述，不能倒推成該 run 的測量值；本節保持其為 U3。目前 lane 已把 exit code 寫進 run 目錄（實測例：`sqlite-20261117T020355Z-61248/exit-code.txt` ＝ `0`，與 `evidence gate: ledger closes against this run's own artifacts` 同輪），之後的輪不再依賴推論。

### 13.7 §12 那十項在 §12.11 之後的狀態（§12.11「未修改任何一項」只對它那輪成立）

| 項 | 主題 | 現在狀態 | 直接回歸證據（都是本輪實跑，非推論） |
|---|---|---|---|
| 12.1 | paper lane 終態無 audit event | **仍為 owner 裁決**（未選分支） | 無程式改動；見 §14 裁決包 D1 |
| 12.2 | paper trading 不在 quota reserve／settle 內 | **仍為 owner 裁決**（計量政策未定） | 無程式改動；見 §14 裁決包 D2 |
| 12.3 | `status='reserved'` 永不過期（懸掛占用） | **仍為 owner 裁決**，且依 owner 三「不以年齡證明執行者已死亡，不擅自加入 sweep／釋放 quota」——方案以完整 lease／身份／終態契約提出 | 無程式改動；見 §14 裁決包 D3 |
| 12.4 | provenance：`unknown` 是正確記錄，測試的 `human` 是錯期待 | **已修正源頭**（改文件與測試，不改產品判定） | `bash ./backend/scripts/run_tests.sh sqlite tests/test_registration_risk_acknowledgement.py` ⇒ **8 passed**、`exit-code: 0`、`evidence gate: ledger closes`（run `sqlite-20261117T020355Z-61248`）。連同 provenance 鄰居 4 檔：`tests/test_decision_provenance.py`、`test_synthetic_provenance_labeling.py`、`test_paper_provenance.py` ⇒ **43 passed**、RC 0（run `sqlite-20261117T020422Z-61542`） |
| 12.5 | `get_status` 在 SQLite 上 500 且回傳內部例外文字 | **已修**（#164） | 既有直接回歸通過；環境與入口合同 5 檔本輪 **108 passed**、RC 0（run `sqlite-20261117T020539Z-62196`） |
| 12.6 | target guard 只住在測試檔、身分字面值散佈 | **已修**（#99／#158；本輪再加 2 條離線節點） | 同上 108 passed 那輪含 `test_target_guard.py` 合同 |
| 12.7 | 3 條秘密洩漏測試的 except 子句過寬＝空斷言 | **已修**（fail-closed ＋ 6 條新離線節點） | `tests/test_error_safety*.py` 於 108 passed 那輪內 |
| 12.8 | 並行配額 teardown 用全域 `drop_all` 且吞錯 | **已修**：該模組**不再執行任何 DDL**，engine 一律經 target guard，清理按已保存 id 並以獨立連線驗證零殘留；4 條 AST 閘門把這些做成離線可回歸的契約，PG 那 4 條改逐節點 skip（不再整檔移走離線覆蓋） | `bash ./backend/scripts/run_tests.sh sqlite tests/test_quota_concurrency.py` ⇒ **4 passed ＋ 4 skipped（pg_only）**、collected 8、RC 0；配額鄰居 4 檔 ⇒ **72 passed ＋ 4 skipped**、collected 76、RC 0 |
| 12.9 | `is_production_like` fail-open | **仍為 owner 裁決**（牽動所有部署檔） | 無程式改動；見 §14 裁決包 D4 |
| 12.10 | replay 終局 cancel 不檢查寫入是否落地 | **已修**：`app/services/paper_trading.py:927` 讀 `cancelled.rowcount == 0` → rollback 並上拋 `InvalidStateError`（與 `:1015` 完成分支、`:389` formal-cancel 同一形態） | 該檔生命週期測試在 108 passed 那輪內 |

### 13.8 本節不是什麼

1. **不是新的全量量測**：本節沒有任何一個新 run 的總數；它只是把既有產物重讀一遍，並把「對象」寫清楚。新的權威數字來自 #169 那一次固定狀態全量，屆時取代的不是這些歷史值，而是「目前工作樹」那欄。
2. **不是把 3482 改成 3479**（或相反）：兩個數都留在帳上，各自標明計數物件。
3. **未刪、未覆寫、未重印任何檔案**：§13.5 的四個檔位於原路徑、原 bytes；`skip_budget.json` 未手改（仍待 #169 從真日誌重釘）。
4. **未動被測產品碼**：本節只動文件；§13.7 表中「已修」的碼是先前各輪交付的改動，本節只登記其狀態與證據位置。
5. **不是批准**：PG／Docker 啟動、建 role／DB、migration、服務與瀏覽器啟動、SIGKILL、憑證輪替、歷史重寫、`git rm --cached`、commit、push 仍全部待批。
6. **未輸出秘密**：本節引用的識別只有 sha256、路徑、位元組數與 NUL 數；沒有任何值、片段或低熵指紋。

---

## 14. 只留給 owner 的產品裁決（一次集中；owner 2026-11-17 四）

§12 那十項裡，六項已在本輪（或先前輪次）以程式修正收掉並在 §13.7 登記證據；剩下**四項半**不是工程缺陷，
而是產品政策：同一個技術事實，選 A 或選 B 都合理，後果卻由使用者承擔。本節把它們一次列完，
每項固定五欄 —— **現況（含 file:line）／使用者看得到的差異／建議與理由／不裁決時的預設安全行為／影響面
（quota、audit、migration、UI 各一行）**。owner 三 明定的「已明定的照做」不重複列在這裡。

**本節不新增任何批准**，也不因某項「建議」而預先動工：五項在得到裁決前都維持現況＋如實標記。

### D1｜paper run 的終態要不要進 audit（§12.1）

| 欄 | 內容 |
|---|---|
| 現況 | 四個終態寫入點不發事件：`app/services/paper_trading.py:362`、`:892`、`:959`、`:1113`；同 repo 的 portfolio 層有 10 條事件（`app/api/paper_portfolio.py:510/667/742/765/937/1017/1065/1105/1141/1182`）。 |
| 使用者看得到的差異 | 選「要」→ 「我的第 N 次 paper run 在何時、被誰、從什麼狀態變成 cancelled」在 UI 的時間軸與外匯 CSV 裡可追證；選「不要」→ run 層只能靠 `run.status` 的**當下值**，看不到誰在什麼時候寫下它。 |
| 建議與理由 | **建議選「要」**（四點各一條 `emit_event`，與狀態 UPDATE 同一 transaction）。理由：產品契約把「AI 建議、使用者執行」放在核心，而使用者要能複核的是**決策鏈**，不是最終 flag；portfolio 層已經有事件，run 層沒有會讓同一份 audit 檔出現兩種可信度。 |
| 不裁決時 | 保持現狀不改碼，矩陣該列寫「部分實作（portfolio／order 有，run 無）」，**不寫**已完成。 |
| 影響面 | quota：無。audit：`audit_events` 每次 run 結束 +1～4 列，清理／殘留測試需同步。migration：**不需要**（表與欄位已在，`app/models/audit_event.py:10`）。UI：時間軸多出 run 級事件，需決定要不要顯示（預設顯示全部既有事件類型，不做過濾）。 |

### D2｜paper trading 要不要計入配額（§12.2）

| 欄 | 內容 |
|---|---|
| 現況 | 兩階段協議在 `app/auth/entitlements.py:19-27`（插入 `:346-353`），使用者是 AI／robustness／personal_models／ml_training／inference 五條路徑；`app/api/paper_trading.py` 與 `app/services/paper_trading.py` 對 `reserve_quota`／`settle_reservation`／`release_reservation` 的引用數 **0**。 |
| 使用者看得到的差異 | 選「計量」→ 同時開多少 paper run 會被上限擋下（與「每人最多 10 個 skill account」同一套帳）；選「不計量」→ paper run 可無限並行，只有真實成本路徑被計量。 |
| 建議與理由 | **建議選「不計量，但明確宣告」**。理由：配額存在的目的是約束**會產生外部成本**的動作；paper 是 in-process 模擬，把它納入計量會把「使用者練習」與「使用者消耗」混在同一個數字裡，反而讓帳本更難解讀。但這個事實必須寫在 UI／文件，不能靠用戶自己發現。 |
| 不裁決時 | 維持現狀（不計量）＋矩陣該列標「刻意不適用（待 owner 確認）」，**不得**寫成已完成。 |
| 影響面 | quota：若改選「計量」需新增 `quota_type` 鍵（`QUOTA_TYPE_MAP`）並決定取消／失敗時的 release 歸屬。audit：無直接影響（終態 audit 是 D1）。migration：**不需要**。UI：不計量則要在 paper 頁面註明「不佔用配額」；計量則要建立 run 前要檢查並顯示剩餘量。 |

### D3｜`reserved` reservation 的 lease／身份／終態契約（§12.3）

| 欄 | 內容 |
|---|---|
| 現況 | 占用總和只看 `status == "reserved"`，無時間界限（`app/auth/entitlements.py:175-182`、`:320-326`）；釋放只能由仍活著且仍持有 id 的呼叫端觸發（`:452-475`）；billing 也把它算成已消耗（`app/services/billing.py:546-555`）。全 repo 無任何過期掃描。 |
| 使用者看得到的差異 | 程序在 commit reservation 之後、settle 之前死掉 → 該單位**永久**占用，`/ml/entitlement` 與帳單視圖顯示已消耗，使用者無法自行救回（目前也沒有支援途徑）。 |
| 建議與理由 | **建議採「有身份的 lease」而不是「按年齡掃」**（owner 三 已明定：不以年齡證明執行者已死亡，不擅自加 sweep／釋放 quota）。具體輪廓：(1) reservation 建立時就寫入持有者身分（哪一支 run／哪一個 worker lease），已由 `d0e1f2a3b4c5` 把 `research_run_id` 連上（`app/services/research_runs.py:805-825` 是既有可判定路徑）；(2) 終態由**同一持有者**負責 settle／release，且終態寫入是原子條件 UPDATE（§12.10 同形）；(3) 「過期」只在能**正面證明**持有者不存在時成立（例如 run 已是終態卻仍有 open reservation —— 這是身份判定，不是時間判定），且預設動作是**標記＋暴露**，不是自動釋放；(4) 未連結 run 的舊 reservation 一律留在帳上並顯示為 UNKNOWN，不自動歸零。 |
| 不裁決時 | 完全不動（現狀＝永久占用），並把它寫成已知限制。**不自動釋放、不加 sweep、不改 threshold**。 |
| 影響面 | quota：核心影響項；任何釋放規則都直接改變可用量。audit：建議連帶記錄 release/expired 事件，否則釋放本身不可追。migration：需要給 `quota_reservations` 加持有者／lease 欄位與（若要區分）新狀態值 → **新增加一支 tail migration**，且需 PG 實測。UI：帳單／额度頁要能顯示「N 單位由身分不明持有」，並決定是否開放使用者請求釋放。 |

### D4｜`APP_ENV` 拼錯時生產加固靜默失效（§12.9）

| 欄 | 內容 |
|---|---|
| 現況 | `app/config.py:48` 的 `app_env` 是自由字串（無 `Literal`／validator／大小寫正規化）；`:153-154` 只在正好等於 `production`／`staging` 時視為生產；`:168-169` 未通過即跳過 `:169-215` 全部加固檢查。另有六處 `is_production_like or app_env != "development"`（`:239`、`:282`、`:302`、`:340`、`:370`、`:384`），其中 `is_production_like` 恆為死項。 |
| 使用者看得到的差異 | 部署者寫 `prod`／`Production`／`PREPROD` 時，JWT 弱預設、`CORS_ORIGINS='*'`、live payments 等檢查**全部不跑**，服務照樣起 —— 出問題時負責任的是被跳過的那段檢查。 |
| 建議與理由 | **建議：`app_env` 收斂為受限集合，加固條件改「凡不是明確 `development`／`test` 皆套用」**（fail-closed）。理由：這是本包所有「不降 guard」要求裡少數仍然 fail-open 的位置；未列值從「靜默跳過」變成「啟動失敗」是可預期且**可観測**的中斷，比反向錯誤好。 |
| 不裁決時 | 不動碼。但**本包的 E1–E6 一律以 `APP_ENV=development`＋loopback＋可丟棄 DB 執行**，並在 §5a 明文記錄該棧因此**不走**生產加固路徑 —— 也就是 D4 的影響在本包執行範圍內不可觀察，不得用本包結果宣稱 D4 已解決。 |
| 影響面 | quota：無。audit：無。migration：不需要。UI：無（啟動期行為）。**部署**：所有現有部署檔需同步；拼錯值者會從能跑變成不能跑 —— 這是**發布前要告知的行為變更**，不是 bug。 |

### D5｜註冊告知事件在「首次密碼登入」時要不要重發（§12.4 剩下的那半）

| 欄 | 內容 |
|---|---|
| 現況 | 註冊當下的告知確認寫成 `actor_type="unknown"`（`app/api/auth.py:89` → `app/auth/provenance.py:30-37`；註冊時沒有已驗證憑證，`unknown` 是依憑證判定的正確結果）。本輪已把**測試期待**從 `human` 改為 `unknown` 並補上正控制（`backend/tests/test_registration_risk_acknowledgement.py:117`、`:181`），文件同步修正（`docs/api-contract-notes.md` §7）。 |
| 使用者看得到的差異 | 重發 → 第一次用密碼登入時，帳本上多一條 `human` 的確認事件，代表「這個通道確實驗證過的人又確認了一次」；不重發 → 該承諾只有一條 `unknown` 紀錄，無論他後來登入幾次。 |
| 建議與理由 | **建議選「不重發」**。理由：`provenance` 模組文件 `:14-16` 自己寫著 unknown 永不因推論升級；重發雖然合規（新事件確實由 `pwd` 通道寫出），但它補的是**記錄密度**而不是**身分可信度**，而且會誘使後續把「有 human 事件」當成同意過兩次的證據。若 owner 想要「登入後再確認」，那是一個新產品流程（§12.4 選項 2），不該由 audit 事件順帶實作。 |
| 不裁決時 | 保持現狀（不重發），且**既有 `unknown` 列一律不改寫**（已記錄於 `docs/api-contract-notes.md` §7）。 |
| 影響面 | quota：無。audit：選重發則每次首次登入 +1 列。migration：不需要。UI：選重發則要在登入流程插入一次真實確認步驟（前端＋契約變更，另行批准）。 |

### 14.1 本節不是什麼

1. **不是工程待辦**：§12 六項已修的工程缺陷不在這裡重複；這裡只有「選哪邊都合理、後果由使用者承擔」的五項。
2. **不是批准**：任何一項得到裁決後仍需**分開**的執行批准（尤其 D3 需要新 migration 與 PG 實測）。
3. **不預先動工**：五項在裁決前都維持現況；本輪已實作的 §12 修正不依賴其中任何一項。
4. **不靠本包驗證**：D4 在 `APP_ENV=development` 的執行範圍內結構上不可觀察（見該欄），不得以 E1–E6 綠燈代替。

---

## 15. 固定狀態完整 SQLite 驗證一次，以及 selection／ceiling 的重新釘選（owner 2026-11-17 一＋二＋七）

owner 七：「先工程修正與直接回歸，再一次固定狀態完整 SQLite 驗證……不要每改一份文件就重跑 30 分鐘全套。」本節就是那**一次**驗證的全貌：一輪 34 分 30 秒的全量 run（`/tmp/alethra-test-logs/final-20261117/run`）、它的四類原始產物、由它重釘的 selection manifest 與 skip ceiling、兩個方向的反向對照，以及**在撰寫本節的過程中才被抓出來的第五個缺陷 E**。因此本節的結論不是「現況下全套綠燈」，而是：**digest `72dcb3d1…` 的樹上全套綠燈且帳本閉合；現況樹 `43b905bc…` 尚未做過全量**（§15.7）。

### 15.1 被測對象的身份，以及「固定狀態」實際上固定了什麼

`run/identity.txt` 15 行全錄（914 B，`775581a33ea0449cb7e49682c002b31f7db2980dcb1e5b43af2cfc7ac4a8f1bb`）：

```
command: ./backend/scripts/run_tests.sh sqlite 
cwd: /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend
git: 5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a (working tree dirty: run 'git status --porcelain' for the diff identity')
porcelain: 137 path(s) sha256 77b537488da0047ffde2c780a00dc5bffb5bc812988ca8feeadfe1f13516b7b1
interpreter: /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend/.venv/bin/python
lane fingerprint: python 3.12.16 | pytest 8.3.3 | sqlalchemy 2.0.36
target: sqlite /tmp/alethra-sqlite-test-dir/alethra-sqlite-test-8270.db
provider: DATA_PROVIDER=demo
layer: sqlite
selection-kind: whole-suite
tree-digest-before: 72dcb3d1b3368a922b7401800f54d20836a7b55cf1c2cb3b828d81fa49625443
collected-node-ids: 3548
selection on this tree: 3548 tests collected in 3.46s
tree-digest-after: 72dcb3d1b3368a922b7401800f54d20836a7b55cf1c2cb3b828d81fa49625443
exit-code: 0
```

| 欄 | 它是什麼、不是什麼 |
|---|---|
| `porcelain` | `git status --porcelain` **文字輸出**（狀態字＋路徑）的 sha256 與路徑數（137），不是內容雜湊。**勘誤（本輪重測）**：本節初稿寫「`-uall` 為 136，兩者不可混用」，那個 136 無法重現，方向也說反了——`-uall` 把未追蹤**目錄**展開成逐檔，路徑數只可能 ≥ 精簡口徑。現況樹上兩者實測相等（`--porcelain` 與 `--porcelain -uall` 都是 138 條、同一個 digest `16e707f1…`），因為此刻沒有任何被折疊起來的未追蹤目錄；只有在出現該類目錄的樹上，兩個口徑才會給出不同的數字與不同的 digest，屆時 identity 記的是哪一個就必須照那一個比對。 |
| `tree-digest-before/after` | `run_tests.sh:345-348` 的 `find . -name '*.py'` 逐檔內容雜湊。**只覆蓋 `.py`**：`run_tests.sh` 本身、`collected_nodes.txt`、`skip_budget.json` 都在這個固定範圍之外，所以 §15.2 另外記它們的位元組數＋sha256。兩值相等 ⇒ 這 34:30 之內沒有任何 `.py` 被改動。 |
| `command`／`selection-kind` | 呼叫端沒有加 `-q`／`-r`／`--tb`，也就是 owner 二要求的「`selection-kind` 取自 CLI 語意」而非從 verbose 旗標猜。 |
| `exit-code: 0` | pytest **自己**的狀態，由 `run_tests.sh:435` 的 `RC=${PIPESTATUS[0]}` 取得、`:436` 在任何 gate 之前寫入 `exit-code.txt`——gate 事後拒收不會把 0 改成非 0，反之 gate 綠燈也不會把非 0 洗成 0。 |

### 15.2 這一輪量到了什麼（每個數字都註明計數對象與出處）

| 數字 | 計數對象 | 出處（原話，非推算） |
|---|---|---|
| **3,548** | collected **item**（node id） | `run/collect.txt` 內含 `::` 的行數；`identity.txt` 第 13 行 `3548 tests collected in 3.46s` |
| **3,551** | junit **outcome unit** | `junit.xml` 的 `<testsuite tests="3551" skipped="183" failures="0" errors="0">` |
| **＝3548＋3** | unit 與 item 的差 | 3 條 **collection 級** module skip：`tests.test_crypto_bar_harvest_pg`、`tests.test_crypto_paper_pg`、`tests.test_strategy_study_timestamps_pg`（整檔在 collection 就被拿走，永不產生 item） |
| **3,368** | passed item | `run/pytest.log` 最後一行 `3368 passed, 183 skipped, 9945 warnings in 2070.43s (0:34:30)` |
| **183** | skipped **unit** | 同一行；＝ item 面 180 ＋ collection 面 3 |
| **pg_only 164／live_stack 18／other 1** | skip 分類 | `skip_budget.py … check` 的 console census（對 `^SKIPPED` 行）與 `run_evidence.py` 對 junit 的分類**各算一次**，兩邊同為 164/18/1，和為 183 |
| **other=1 的具體位置** | 那 1 條不屬於任何 lane 邊界的略過 | `tests/test_fixture_failure_injection.py:178: BLOCKED: Cannot intercept cleanup commit without complex mocking` |
| **0／0／0／0** | unresolved record／duplicate node id／collected 但無紀錄／紀錄無 id | `run_evidence.py … ledger` 末四行 |
| **2070.43s（34:30）** | 本 run 的 pytest 時距 | summary 行；**不是** wall clock，也不是上輪 1983.19s 的複用 |

原始產物（本 run 未加 `-q`，也未指定 `--tb`，故為 pytest 預設的 `--tb=long`；`failures=0` 意味著日誌裡也沒有 traceback 可存——1,016 行是逐節點輸出加上 `:832` 起由 `-rfEs` 強制的 short test summary）：

| 檔 | 大小 | 行 | sha256 |
|---|---|---|---|
| `identity.txt` | 914 B | 15 | `775581a33ea0449cb7e49682c002b31f7db2980dcb1e5b43af2cfc7ac4a8f1bb` |
| `collect.txt` | 352,732 B | 3,769 | `b743bab73abd5971b10b14a905d4a6dfc1981176ff5cfe4cf44be7819e20eeed` |
| `pytest.log` | 109,948 B | 1,016 | `1ecd80140dc7258293cfae0db0ab4f36ed313904f039c37755f4b0ae07a81426` |
| `junit.xml` | 598,632 B | 1 | `9e596288283979711a2a2f7d8e45dbe94d11d793c30c813de9d474b2db4cf52c` |
| `exit-code.txt` | 2 B | 1（`0`） | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` |

`collect.txt` 的 3,769 行減 3,548 個 id ＝ **221 行收集器副作用**（warnings summary：22 條 `pydantic/_internal/_fields.py:132`、22 條 `warnings.warn(...)`、`on_event` 棄用提示等）。這個差被如實記錄，不是 id 數的對照分母。

### 15.3 selection 漂移：＋70／−1，以及「移除會被看見」的證據

比對基準是**前一次全量的 3,479 個 exact node id**（`/tmp/alethra-test-logs/ids-post159.txt`，3,479 行，sha256 `aaad93e1283dfc2fce71ed39c3814db46223671fbdebb0dc017e6f105b75fe21`；其來源日誌第 10 行 `selection on this tree: 3479 tests collected in 3.19s`）。以 exact node id 逐條比對：

```
prior census 3479 -> this run 3548  (+70 / -1)
  +19   tests/test_run_evidence_ledger.py
  +15   tests/test_lane_entry_contract.py
  +10   tests/test_env_hygiene_contract.py
  +7    tests/test_ownership_integration.py
  +6    tests/test_error_response_safety.py
  +4    tests/test_quota_concurrency.py
  +4    tests/test_replay_credibility.py
  +3    tests/test_registration_risk_acknowledgement.py
  +2    tests/test_target_guard.py
  -1    tests/test_env_hygiene_contract.py::test_the_two_compose_files_are_the_whole_ledger  (REMOVED)
```

3,479 條裡有 **3,478 條仍被收集**；唯 1 消失的就是 §13.7 那項**刻意改名**的節點（舊名宣稱「兩個 compose 檔就是全部帳本」，第 4 條規則上線後該宣稱不再成立，已由 `test_the_ledger_names_every_position_the_tree_produces` 取代）。這一段的 diff digest ＝ `9a3b1de6186ee12db7965dc35e80ebcadcd08900e185c654178a5771519b9aa2`（`/tmp/review15/ack-vs-prior.txt` 全 71 行逐條讀過）。

**必須分清的兩個 digest**（不然讀者會以為工具看到了 70）：

| digest | 誰算的 | 內容 |
|---|---|---|
| `437a7e7f89fabf151cb955654b3590db1b8f5692f6e9ec0b747aac4f912d6237` | `run_evidence.py pin` **自己**印的 | `selection diff vs pinned: -0 +3548`——因為 `backend/tests/collected_nodes.txt` 這支檔**此前不存在**，對 pin 而言整個選擇都是「新增」。這是唯一被 `--ack-diff` 接受的口徑。 |
| `9a3b1de6…` | 本節作者另算 | 前次 3,479 id 清單與本次 3,548 的實際 −1/+70；用來證明「移除會被抓到」，**不是** pin 會認可的憑證。 |

釘選後立刻可重現性檢查（同一 run dir、同一產物，未重跑全量）：再次 `pin` 印出 `selection diff vs pinned: -0 +0`、空 body 的 digest `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（＝SHA-256 of 空字串），manifest 覆寫後 sha 仍是 `59102f8a0d9a1f2da7dad27a6d744f629de5603fd8a67126f964f39bd01e1a26`——**逐位元組冪等**。

釘選產物：

| 檔 | 大小 | 內容 | sha256 |
|---|---|---|---|
| `backend/tests/collected_nodes.txt` | 336,978 B | 3,548 個 exact node id（排序、一行一個） | `59102f8a0d9a1f2da7dad27a6d744f629de5603fd8a67126f964f39bd01e1a26` |
| `backend/tests/skip_budget.json` | 2,166 B（重釘後） | `collected=3548 total=183 pg_only=164 live_stack=18 other=1` ＋ provenance（run dir、exit code、五份產物 sha、identity 全欄） | `b3165be4fae9c861bc7a576b6fcb4df5875835266599fd554afe4ab4765b7afd` |

### 15.4 這一輪抓出的五個基礎設施缺陷：現場、根因、修正位置、回歸節點

現場是同一份 21-failure 全量日誌（`/tmp/alethra-test-logs/fixed-state-20261117/`，`command: … sqlite -q -p no:warnings --tb=line -rfE`，`21 failed, 3339 passed, 183 skipped in 1983.19s`，`exit-code: 1`）。該日誌用 `--tb=line`，所以**逐節點的原因字串沒有被保留**；歸因因此來自其後的focused 重跑（下表），不是從那份日誌推算。21 條分佈：`test_lane_entry_contract.py` 16（含 4 條 `[quiet0..3]`）、`test_process_state_isolation.py` 3、`test_env_entries.py` 1、`test_postgresql_only_marks.py` 1。

| # | 根因（不是症狀） | 修正位置 | 回歸節點（exact node id，全部在 15.3 的釘選清單內） |
|---|---|---|---|
| **A** | 巢狀 lane 呼叫繼承外層 run 的 `ALETHRA_RUN_DIR`／`ALETHRA_LANE_LOG`／`ALETHRA_SKIP_BUDGET`／`ALETHRA_COLLECTED_MANIFEST`，於是「跑一次腳本」的測試把自己寫進正在被測的那個 run dir | `run_tests.sh:343` 在派生 pytest 前 `unset` 四個座標；run dir 仍以 exclusive mkdir 建立，既有目錄一律 exit 2 拒絕（`:309`–`:326`） | `tests/test_lane_entry_contract.py::test_two_runs_cannot_reach_each_others_output`、`::test_the_lane_keeps_its_output_coordinates_out_of_the_suite`、`::test_an_existing_run_directory_is_refused_and_left_untouched`、`::test_an_explicit_run_directory_without_a_parent_is_refused_not_invented` |
| **B** | collection 級 skip 的 junit 元素把句子放成 **Python tuple repr**，而引號形狀會隨句子內容改變（PG-ONLY 句子都含所有格 `PostgreSQL's`，Python 因此改用 `"`）；用固定引號解析會讓 console census 與 junit census **各說各話** | `run_evidence.py:109` `collection_reason()`（改走 `ast.literal_eval`，與引號無關）＋ `:139` `skip_reason()`；兩份 census 一律**交叉檢查而非擇一** | `tests/test_run_evidence_ledger.py::TestLedgerRefuses::test_the_two_censuses_are_crosschecked_instead_of_one_being_chosen`、`::test_a_collection_level_skip_keeps_its_category_in_both_censuses`、`::test_a_new_excuse_cannot_hide_in_the_pg_only_ceiling` |
| **C** | `-q` 合法地不印 `N tests collected` 標頭，而被測腳本的 census 只認標頭，於是一個完美的安靜 run 被當成「沒有量測」；同時 `write-budget` 沒有同一條退路，安靜 run 的 ceiling **根本無法重生** | `skip_budget.py:172`（check）與 `:124`（write-budget）都改從 `identity.txt` 讀數量，並把來源寫進 provenance（`:235` 新增 `--identity`）；`run_tests.sh:473` 一律附上 `--identity "$RUN_DIR/identity.txt"` | `tests/test_lane_entry_contract.py::TestSkipCeiling::test_a_quiet_transcript_is_read_from_the_identity_artifact`、`tests/test_run_evidence_ledger.py::TestLedgerRefuses::test_a_missing_artifact_is_a_refusal_not_an_empty_ledger` |
| **D** | node id 以 `::` 切分，但**參數本身可以含 `::`**（`test_loopback_hosts[::1]`），於是一個節點被當成兩個、或對不上 junit | `run_evidence.py:62` `split_node()`（只切前兩段；docstring `:65` 記的就是這個案例） | `tests/test_run_evidence_ledger.py::TestLedgerRefuses::test_a_parameter_that_contains_the_separator_is_still_one_node`、`::TestLedgerCloses::test_a_root_level_suite_id_is_not_prefixed_to_make_it_match` |
| **E**（寫本節時由既有 contract 抓出） | 三件事同一根源——把內部實作細節當成對外證據：(1) `identity.txt` 的 `selection-kind` 印的是 shell 哨兵 `YES`；(2) `run_evidence.py pin` 寫出的 ceiling 沒有 `source_log_sha256`，`test_the_ceiling_is_generated_and_regenerates_identically` 立刻 `KeyError`；(3) `write-budget` 缺 `--identity`，安靜 run 的 ceiling 無法重生 | `run_tests.sh:291-292`＋`:396`（`SELECTION_KIND`＝`whole-suite`／`explicit-selection`）；`run_evidence.py:541-542`（pin 的 provenance 補 `source_log`＋該 log 的 sha256）；`skip_budget.py:124-132`（write-budget 的 identity fallback）、`:153`（記 `collected_from`）、`:235`（新增 `--identity`） | `tests/test_lane_entry_contract.py::test_selection_kind_comes_from_cli_semantics_not_from_a_missing_dash`（斷言由「不含 whole-suite」改成**逐字比對三種口徑**）、`::TestSkipCeiling::test_the_ceiling_is_generated_and_regenerates_identically`（見下） |

「脆弱總數」的另一項（非缺陷而是同輪清理）：`test_postgresql_only_marks.py` 的 `test_sqlite_lane_still_collects_every_node_of_the_module` 原本斷言 `"10 tests collected"`，該模組現定義 17 個節點，因此它每次有人新增節點就會紅——正是 owner 二禁止的「靠每次接受最新總數」。現改為 `test_postgresql_only_marks.py:37` `_defined_node_ids()` 以 `ast` 讀該檔**自己定義的 node id**，再逐一檢查是否可收集（參數化不展開，所以這是不會隨他人改動而漂移的陳述）。

### 15.5 check → pin → 再 check：實際輸出與 RC（含兩個反向對照）

全部對**同一個 run dir、同一批未變的產物**執行，沒有第二次全量：

| 步 | 指令（節略絕對前綴） | RC | 關鍵輸出 |
|---|---|---|---|
| 1 | `run_evidence.py …/final-20261117/run ledger` | **0** | `evidence gate: ledger closes…`（15.2 全部欄位） |
| 2 | `run_evidence.py … pin --manifest-out backend/tests/collected_nodes.txt --budget-out backend/tests/skip_budget.json` | **2** | `refused: re-pinning requires --ack-diff <the digest above>`＋`selection diff vs pinned: -0 +3548`＋digest `437a7e7f…` |
| 3 | 同上 `--ack-diff 437a7e7f…` | **0** | `pinned … (3548 node ids) and … (total=183 pg_only=164 live_stack=18 other=1)` |
| 4 | `skip_budget.py …/pytest.log check --budget … --identity …/identity.txt` | **0** | `skip census … 183 units pg_only=164 live_stack=18 other=1`、`budget … collected=3548 total=183`、`within budget; headroom pg_only=0 live_stack=0 other=0 total=0` |
| 5 | `run_evidence.py … check --budget … --manifest …` | **0** | `evidence gate: ledger closes against this run's own artifacts` |
| 6 | 反向對照 A：從 manifest 的**複本**（`/tmp/negctl15/`）拿掉 1 個已釘 id | **1** | `1 node(s) are collected but not pinned; the pinned selection is re-written only by \`pin --ack-diff\`, never by accepting the newest total: tests/test_env_hygiene_contract.py::test_the_ledger_names_every_position_the_tree_produces` |
| 7 | 反向對照 B：在複本裡加回 1 個**已不存在**的 id（15.3 那個 −1） | **1** | `1 pinned node(s) are no longer collected (removed, renamed or made uncollectable): tests/test_env_hygiene_contract.py::test_the_two_compose_files_are_the_whole_ledger` |

headroom 全 0 是**量測結果**，不是設定：ceiling 由這輪自己的 census 重釘，所以沒有多餘空間；日後任何新增 skip 都會直接越線。步驟 6／7 存在的理由：一個「能過」的 gate 若「不可能拒」就不算 gate。兩個反向對照都只寫 `/tmp` 下的複本，釘選檔本身未被觸碰（步驟 3 之後 sha 仍為 `59102f8a…`）。

### 15.6 本節沒有聲稱的事（與仍然留著的 UNRESOLVED）

1. **不重算 3479／3482**：那兩個數字的對象釐清在 §13.1，§13.4 的 UNRESOLVED 清單照舊有效。本節只是把「新的 3,548 id 帳本」放進同一套規則裡跑過一次。
2. **歷史量測不改寫**：舊 run dir（`fixed-state-20261117/`、`full-suite-20261117-console.log`）原封不動，其 exit code 仍依 §13.6 記為「未直接保存」；本輪新 run 有自己的 `exit-code.txt`。
3. **不是 PG／服務／browser 驗收**：這一輪只有 SQLite lane、`DATA_PROVIDER=demo`、可丟棄 DB（`alethra-sqlite-test-8270.db`）、無外呼。`pg_only=164` 那 164 個節點在這一輪**依然 NOT RUN**，不得以本節綠燈宣稱它們跑過。
4. **不是 F 全面完成**：F 現況維持 §14 前的敘述——程式／離線 gate 已驗證，真實 start/status/verify/stop 尚未驗收。
5. **E1–E6、G1–G3 未獲批准，也未執行**：本節沒啟動 PG、沒跑 migration、沒開瀏覽器、沒 `git rm --cached`、沒 commit、沒 push。
6. **不輸出秘密**：15.2／15.3 的 sha256 都是**檔案內容**雜湊（腳本與 id 清單），不是任何憑證值；4 條前端密碼 fallback 仍以「位置＋值類別＋digest」登記在 `check_env_hygiene.py` 的 `TRACKED_DEBT`，其值、骨架與低熵指紋一律不入檔。

### 15.7 量測之後樹上變了的東西（如實標記，不是把綠燈搬到現況頭上）

15.1 的綠燈屬於 digest `72dcb3d1…`。本節撰寫期間（量測之後）為缺陷 E 動了 4 個檔（全部為 `.py`／`.sh`，因此**tree digest 改變**），並重釘了 2 個產物：

| 檔 | 大小 | sha256（現況） | 本節期間的改動 |
|---|---|---|---|
| `backend/scripts/run_tests.sh` | 21,313 B | `eb2fdefd5a38a4ff0d7593831c2fc776cd5ed9acf6e082c93c44a118ba39d3b2` | `:291-292` 新增 `SELECTION_KIND`；`:396` 不再把 shell 哨兵寫進 identity |
| `backend/scripts/skip_budget.py` | 11,061 B | `d1da8e23305ad8f395e5b284b38d91c8f370b0870a5a63ae969630e531775d7c` | `:124-132` write-budget 的 identity fallback；`:153` provenance 記 `collected_from`；`:235` 新 `--identity` |
| `backend/scripts/run_evidence.py` | 26,566 B | `83570fb1f738e6a9ea139d60170db9dbd063e867203cb15b78fbaf36602b10bf` | `:541-542` pin 的 provenance 補 `source_log`＋`source_log_sha256` |
| `backend/tests/test_lane_entry_contract.py` | 31,884 B | `b41e929c77e323d39d7db26108b2c479f19bf266a57f0c83c30f19fc345ded74` | `:360` 重生性檢查改「五個數字相等＋源日誌 sha 驗證＋口徑逐字比對」；`:560` selection-kind 逐字化 |
| `backend/tests/collected_nodes.txt` | 336,978 B | `59102f8a0d9a1f2da7dad27a6d744f629de5603fd8a67126f964f39bd01e1a26` | **新增**（3,548 個 id） |
| `backend/tests/skip_budget.json` | 2,166 B | `b3165be4fae9c861bc7a576b6fcb4df5875835266599fd554afe4ab4765b7afd` | 由 `write-budget` 口徑改為 `pin` 口徑（含 source log 雜湊） |

現況身份：`tree-digest 43b905bc17560fc5214297e45cc2cb4bc760dba4c04bc2618171fda2ffb864e3`（before＝after），`porcelain: 138 path(s) sha256 16e707f15d16bda2c7d565c152eac94b9593e058d75daa9a41199ee8ed17ef1d`（137→138 ＝ `collected_nodes.txt` 這支新檔）。

已對**現況**做過的實測（都是 focused，不是全量）：

- `run_tests.sh sqlite tests/test_lane_entry_contract.py tests/test_run_evidence_ledger.py` ⇒ `56 passed, 93 warnings in 59.29s`、`exit-code: 0`、`evidence gate: ledger closes against this run's own artifacts`。
- 單節點 `…::test_selection_kind_comes_from_cli_semantics_not_from_a_missing_dash` ⇒ `1 passed in 17.10s`（改斷言前同一節點曾以 `AssertionError: … 'explicit-selection' == 'explicit-selection (collect-only)'` 紅過一次，該失誤現場保留在 `/tmp/alethra-sqlite-test-dir/alethra-runs/sqlite-20261117T040935Z-29301/`）。
- `run_tests.sh sqlite --collect-only -q` ⇒ `collected-node-ids: 3548`、`selection-kind: whole-suite (collect-only)`，且與釘選清單相比 **added 0 / removed 0**——即 E 的修正沒有改變 selection，3,548 這份 manifest 對現況樹仍然成立。
- 兩個 writer 口徑一致性：`skip_budget.py …/pytest.log write-budget --out /tmp/negctl15/regen.json --identity …/identity.txt` ⇒ `collected=3548 total=183 pg_only=164 live_stack=18 other=1`，與 pin 寫入的五個數字**逐欄相等**（`write-budget` 在此之前對同一日誌是直接 `refused`）。
- 本節定稿前把三個 read-only gate 在同一批產物上**重算一次**（12:3x，非抄錄 15.5）：`run_evidence.py … ledger` RC 0、`run_evidence.py … check --budget … --manifest …` RC 0（`evidence gate: ledger closes against this run's own artifacts`）、`skip_budget.py … check` RC 0（`within budget; headroom pg_only=0 live_stack=0 other=0 total=0`）；印出的 12 欄數字與 15.2 逐字相同。這批重算只讀 `/tmp` 產物與兩份釘選檔，未重跑 pytest。

**尚未做**：在 `43b905bc…` 上重跑那 34:30 全量。依 owner 七「不要每改一份文件就重跑 30 分鐘全套」，本輪把這次全量留到 E 批次（CORS origin 接線，#171）收完之後一次執行；在那之前，任何「現況全套綠燈」的說法都不成立。

### 15.8 本節不是什麼

1. **不是第二次量測**：只有一輪全量（2070.43s），其餘都是同一批產物上的 gate 重算與 focused 回歸。
2. **不是「把總數接受成新釘選」**：ceiling 與 manifest 只能由 `pin --ack-diff` 移動，且 §15.5 步驟 6／7 證明它對兩個方向的漂移都會拒收。
3. **不是 E1–E6／G1–G3 的替代**：PG、migration、瀏覽器、git 寫入一律未動。
4. **不是 owner 裁決的輸入**：§14 那五項（D1–D5）不因本節任何數字改變狀態；15.2 的 `other=1`（`test_fixture_failure_injection.py:178` 的 `BLOCKED`）是工程待辦，不是產品政策選擇。
5. **不宣稱 fixed-state 涵蓋腳本自身**：`tree_digest` 只看 `.py`，所以 `run_tests.sh` 的身份由 15.7 的 bytes＋sha256 承擔；這是既有工具的邊界，本節把它寫明而不是假裝它不存在。

### 15.9 第六個基礎設施缺陷：附件 A 宣稱「不含秘密」而實際上嵌入了秘密

這是重產附件（#166）時抓到的，性質與 15.4 的 A–E 同類：**文件的自我宣稱與它承載的內容不一致**，而且這次錯的是宣稱。

| 項 | 實測 |
|---|---|
| 缺陷 | 10:2x 那版附件 A（144,352 B，sha256 前綴 `2af6ac2b…`）末節寫「本附件不含任何秘密值……沒有寫死的口令」，但同一檔第 2929／2934／3027／3031 行逐字嵌入了 `run_r2.sh` 的 `password` 字面值 **4 次**（該腳本 391／396／489／493 行）。值本身在本文件任何位置都未出現，也不該出現。 |
| 根因 | 產生器 `/tmp/render_attachment_A.py` 只做「逐字嵌入＋量測」，機密段落是**手寫的一句宣稱**，沒有規則、沒有檢查。掃描器 `check_env_hygiene.py` 也抓不到：它的三條賦值 regex（`:176`–`:178`）錨在行首（`^(?:export\s+)?NAME=`），而該腳本的 literals 縮排在 heredoc 的 Python 塊內，且形狀是 `"password": "…"`（JSON 式，非 `NAME=value`）——所以 9 條 findings 裡沒有它。 |
| 修正 | 產生器改為**規則式遮罩**兩種形狀：鍵名含 `PASSWORD/PASSWD/SECRET/TOKEN/API_KEY/ACCESS_KEY/PRIVATE_KEY` 的帶引號值，以及 `scheme://user:口令@host` 的內嵌口令。遮罩以鍵名與行號登記，值、值的骨架、值指紋一律不寫。寫檔前用**同一組規則**重掃渲染結果，仍有未遮罩形狀就 `return 1` 且不落盤（實測觸發過三次：第一次 4 處是真洩漏，第二次與第三次各 1 處，都是產生器**自己的說明文字**寫出被遮形狀所致——第二次改寫成 `口令`，第三次是把 URL 形狀寫成 `user:…@host` 的縮寫仍被同條 regex 命中，改寫為「`user:` 之後、`@` 之前那一段」。三次的處置都是改文字，**沒有放寬規則**）。第三次重產時順把「標記出現次數＝遮罩處數＋1」做成寫檔前的算式檢查（`content.count(MASK) != total_masked + 1` 即拒寫），實測印出 `7 / 8`，所以拿 `grep -c` 複核的人不會看到一個無法解釋的數字。 |
| 附件現況 | 156,654 B、3,478 行、sha256 `4047d87a79b990e439377b268143679d9d199aefcc57baadc13f8a16cbc21582`（產生器當場印出，非抄錄）；索引的「遮罩」欄逐檔計數為 `check_env_hygiene.py` 3、`run_r2.sh` 4、其餘 0，總數 7（4 key-literal＋3 url）。這 7 處**不全是秘密**：`run_r2.sh` 的 4 處是腳本裡的真口令字面值，`check_env_hygiene.py` 的 3 處在該檔 docstring 的 `scheme://user:pass@host` 舉例上（源檔 `:20`、`:57`、`:351`）；該檔的正則式**定義**未被遮罩，附件 1982–1998 行仍可逐字審其規則。遮罩採刻意過遮，因為「哪一處才算秘密」的判斷本身就是這起缺陷的來源。附件以同組 regex 復測未遮罩形狀：0 處。 |
| 未修（如實登記） | 掃描器對「縮排的 JSON 式 `"password": "…"`」仍無覆蓋，這是 #168 的範圍，不在本輪動它：放寬規則會改 findings 總數，而 findings 總數被 `tests/test_env_hygiene_contract.py` 的 12 條契約與提交帳釘住，必須與那些斷言同輪改、同輪重量。 |
| 對 fixed-state 的影響 | 零：本輪改的是 `/tmp/render_attachment_A.py`（不在 repo）與兩份 `.md`，`tree_digest` 只看 `.py`，現況 digest 仍是 `43b905bc17560fc5214297e45cc2cb4bc760dba4c04bc2618171fda2ffb864e3`（本節撰寫後以 `run_tests.sh:345-350` 同一條管線重算證實）。 |
| 復測該 digest 的**必要 CWD** | 那條管線是 `find .`，而 `run_tests.sh:237` 先 `cd "$BACKEND"`，所以 `tree_digest` 的根是 `alethra/backend`。**從 `alethra/` 跑同一條管線得到的是 `42c4cc1b…`，那是路徑字串不同的結果，不是漂移。** 本輪先踩過這個誤判（一度把 `4daa5ed5…`／`42c4cc1b…` 當成樹變了），以 `stat` 逐檔核對 `.py` 最新 mtime 停在 12:14:27（那是 §15 量測之前的既有狀態）後才排除。**How to apply：** 任何人複核 fixed-state，命令必須是 `cd alethra/backend && find . -name '*.py' -type f -not -path './.venv/*' -not -path './venv/*' -not -path '*/__pycache__/*' -not -path './node_modules/*' 2>/dev/null \| LC_ALL=C sort \| xargs shasum -a 256 \| shasum -a 256`。 |
| 覆蓋缺口 | 該產生器住在 `/tmp`、不在測試範圍，所以**沒有契約測試能抓這件事**。把它放進 repo 會移動 tree digest、使 15.1 的量測失效，因此本輪維持外置，並把缺陷留在文件裡而不是假稱有 gate。 |

## 16. A1／A1b：技能單位上限由後端執行，以及 daemon 部分寫入失敗的誠實敘事（owner 2026-11-17 三・A／四／五）

本節是 §三A 的第一個實作單元。**只動離線可驗證的部分**：PG、migration、瀏覽器、git 寫入一律未動。A2 的隔離載體盤點（16.6）在這一輪**只測量、未修改**。

### 16.1 §三A「限制須由後端執行，不只靠前端」的程式內容

技能單位 ＝ `paper_portfolios` 那一列（它承載自己的虛擬資金，並經 `portfolio_id` 挂住持倉／訂單／成交／決策）。上限 10 由一支共用守門員在**每個會寫該表的入口**執行：

| 檔 | 狀態 | 內容 |
|---|---|---|
| `backend/app/services/skill_units.py` | **新增**，104 行／4,254 B | `held_slot_count()`、`assert_slot_available()`、`SkillUnitLimitReached.detail()`、`SLOT_HOLDING_STATUSES = (active, paused)`、`RELEASE_ENTRYPOINT` |
| `backend/app/config.py` | 修改 | `paper_skill_unit_max_per_user: int = 10` ＋ `_validate_paper_skill_unit_limit()`（０ 直接拒啟動，且訊息點名環境變數） |
| `backend/app/api/paper_portfolio.py` | 修改 | `POST /api/v1/paper-portfolio` 在 simulation-session 404 檢查之後呼叫守門員；滿額 → **409 `skill_unit_limit_reached`**，body 帶 `limit`／`held_skill_units`／`release_entrypoint`／`message` |
| `backend/app/api/paper_trading.py` | 修改（A1 兩塊 hunk） | `POST /api/v1/paper-trading/create` 同一守門員，且刻意放在該端點的 catch-all **之外**：滿額是 409，不是 500 `creation_failed` |
| `backend/app/services/live_trading_daemon.py` | 修改 | `_create_portfolios()` 逐技能呼叫守門員；滿額記入 `ceiling_refusals` 並 `continue`，與「策略不認識」分開 |
| `backend/tests/test_skill_unit_ceiling.py` | **新增**，343 行／13,241 B、10 節點 | 順序案例（10 收／第 11 拒且不寫任何列）、retire 釋放而 pause 不釋放、帳號間不互相影響、拒絕不動餘額、三個入口同答 409、設定為 0 拒啟動、真 MVCC 併發節點（PG-only） |

入口完整性不是宣稱而是枚舉結果：`grep -rn "INSERT INTO paper_portfolios\|PaperPortfolio(" app` 在現況樹得到 4 行，其中 `app/models/paper_portfolio.py:96` 是 class 宣告（不是寫入點），其餘 **恰好三處**寫入點——`app/api/paper_portfolio.py:516`（ORM）、`app/services/paper_trading.py:98`（raw SQL，唯一呼叫者是 `app/api/paper_trading.py:149`）、`app/services/live_trading_daemon.py:283`——三處現在都先經過同一守門員。另以 `grep -rln "live_trading_daemon\|LivePaperTradingDaemon" tests` 確認該 daemon 只有兩支測試檔 consumes（`test_skill_unit_ceiling.py`、`test_recommendation_logic_repairs.py`），所以 16.4 的受影響群組就是這兩支，不需要重跑 499 節點那輪。

釋放路徑對一般使用者可達：`retire_portfolio()`（`app/api/paper_portfolio.py:1184-1195`）用的是 `get_current_user` ＋ `_get_portfolio_with_ownership`，沒有 admin 捷徑，所以 409 的 `release_entrypoint` 不是死路。

前端不需改：`src/lib/api.ts:48-71 formatErrorDetail()` 對 object detail 先取 `obj.message`，而 `OrderPreviewPanel.tsx:369-380 call()` 把 `err.message` 放進可見錯誤面板——409 body 的 `message` 因此直接可讀。（該面板只在尚無 active 單位時顯示建立鈕，這正是「只靠前端」的例子；現在限制在後端。）

### 16.2 量測（全部 `sqlite` 層，命令皆 `cd alethra && TMPDIR=<每次新目錄> DATA_PROVIDER=demo bash ./backend/scripts/run_tests.sh sqlite <節點>`）

| 輪 | run dir | 數字 | exit code | tree digest（before＝after） |
|---|---|---|---|---|
| 受影響整合群組，修改前 | `sqlite-20261117T052356Z-80894` | collected 499／passed 467／skipped 30（pg_only 30）／**failed 2** | 1 | `86cad73ac177…0197` |
| 受影響整合群組，修改後 | `/tmp/alethra-r172c/alethra-runs/sqlite-20261117T053239Z-83975` | collected 499／passed **469**／skipped 30／**failed 0**，`evidence gate: ledger closes` | 0 | `66a819576f38…6015` |
| A1＋A1b 直接回歸 | `sqlite-20261117T054357Z-89379` | collected 32／passed 31／skipped 1（pg_only）／failed 0 | 0 | `22a3eb458a3f…af39` |

群組的選取不是憑空：以 `grep` 求出所有 POST 到建 portfolio 入口的測試檔（27 支）＋ 所有呼叫 `daemon._create_portfolios()` 的檔案（`tests/test_recommendation_logic_repairs.py`），共 31 支檔案、499 個節點。

**這三輪的身份關係要講明，不能讓讀者自己推**：499 節點那輪測的是 `66a81957…`，它**在 A1b 之前**；A1b 之後的最終內容是 `22a3eb45…`（16.2 第三輪，以及本節撰寫後以 15.9 那條必要 CWD 管線重算一次得到的同一值）。兩者之間的差只落在 `app/services/live_trading_daemon.py` 與 `tests/test_recommendation_logic_repairs.py` 兩檔（`git status --porcelain` 的**路徑集合**在兩輪逐字相同：144 path(s) sha256 `3441a282fcf35b5bba7f04ae3c16e3006bf5e67bd136a3461d3954d6d088b925`；porcelain 雜湊的是「哪些路徑髒」而不是內容，所以它相同、digest 卻不同並不矛盾）。因為該 daemon 模組的 consume 端只有那兩支測試檔（實測見 16.1），A1b 的受影響群組就是 16.2 第三輪那 32 個節點；499 那輪不必為它重跑，但**也不能把它讀成最終內容的全群組綠燈**——最終內容的 full-group 綠燈要等 §15.7 預留的那一次全量。

### 16.3 failed → fixed（owner 六要求與 PG-only 分開列）

兩節點在 16.2 第一輪紅：`TestLiveTradingDaemonValidationOrdering::test_daemon_validates_before_db_write`、`TestDaemonStartupState::test_daemon_reports_actual_validated_set`。

- 根因（實測，非推測）：`TypeError: '>=' not supported between instances of 'MagicMock' and 'int'`，位置 `app/services/skill_units.py:102`。該兩支測試用 `MagicMock()` 假冒 DB session；`_create_portfolios()` 現在會真的發出計數查詢，Mock 回傳 Mock，比較炸掉，再被 daemon 的 `except Exception` 吞掉 → `created 0`。**改的是測試替身，不是守門員**：兩處各補 `mock_db.query.return_value.filter.return_value.scalar.return_value = 0`，讓替身回答「目前沒有佔用槽位」，與真實空帳戶一致。
- 這兩紅同時暴露一個先前不可見的缺陷（16.4）。

### 16.4 A1b：daemon 的部分寫入會把「已存在的帳簿列」敘述成「全部沒通過驗證」

讀 `live_trading_daemon.py` 時抓到的**既有**缺陷（不是 A1 引入的）：迴圈每成功一個技能就 `db.commit()`（源檔 `:324`），後一輪炸掉時 `:334-350` 的 `rollback()` 只能收回尚未提交的那一筆，卻 `return 0`；`start()` 看到 0 就中止並印出 `All requested skills were rejected during validation.`。結果是**兩個方向都不實**：驗證其實通過了，而且資料庫裡已經有 `status='active'` 的帳戶與 `status='running'` 的 run 在無人駕駛的狀態下留著。

修正沿該檔**已有的**逐技能 fail-soft 契約（驗證拒 `:239`、上限拒 `:270-277` 都是「跳過這個、繼續下一個」），不另創政策：

- `except` 分支改為記錄 `creation_failure`（寫入失敗的那個 key）與 `unattempted_skills`（驗證通過但因中斷而從未嘗試的 key，已排除上限拒絕的），並 `return created_count`（真實存在的單位數）。
- `start()` 中止分支現在區分三種結果：寫入失敗／槽位已滿／驗證全拒；標題從 `No valid portfolios created` 改為 `No skill unit opened`（前一句在寫入失敗的情境下是錯的）。
- 部分成功時另印一條：哪個技能沒開成、幾個從未被嘗試、已開的會繼續服務、重啟可重試其餘。
- 新增 `tests/test_recommendation_logic_repairs.py::TestDaemonCreationFailureDisclosure` 兩節點（該檔本輪 ＋104／−1 行，含 16.3 的兩處替身修正）。

**敏感度以負面控制實測**（不是推理）：

| 暫時還原舊行為 | run dir | 結果 |
|---|---|---|
| `return created_count` → `return 0` | `/tmp/alethra-r174neg/alethra-runs/sqlite-20261117T054307Z-88709` | `1 failed, 1 passed`、exit 1；失敗訊息 `…the write failed on skill 2 and got 0` |
| 去掉 `if self.creation_failure:` 分支（保留欄位） | `/tmp/alethra-r174neg2/alethra-runs/sqlite-20261117T054329Z-89057` | `1 failed`、exit 1；捕到的日誌逐字含舊句 `All requested skills were rejected during validation.`，正是該斷言要擋的東西 |

兩次暫時改動之後把檔還原，還原後的 digest **逐字回到** `22a3eb458a3fd3301d6e15699f594e89ca884bae79ab9591c08de6c0a274af39`（與 16.2 第三輪相同），即「改動已完全收回」是可核對的，不是宣稱。

### 16.5 提交前秘密掃描：結果，以及不可歸給掃描器的部分

命令：`cd alethra/backend && ./.venv/bin/python scripts/check_env_hygiene.py --pre-commit --all` → `9 finding(s) across 869 commit text files`、`matches the ledger: 9 commit literal(s), 9 expected, no new credential`、**RC 0**。9 條 findings 全數落在既有 ledger 例外（`docker-compose*.yml` 5 條、`frontend/e2e/*.spec.ts` 4 條），**沒有一條在本次 7 個新／改檔**。

但「RC 0」不能全部歸給掃描器，必須講清楚：§15.9 已登記該工具的語法邊界——縮排的 JSON 式 `"password": "…"` 不在它的三條 `NAME=value` 規則之內（#168 的範圍）。本次新檔確實有這種形狀的行，逐條清單：

| 位置 | 內容 | 判定 |
|---|---|---|
| `tests/test_skill_unit_ceiling.py:27`、`:33` | `"password": "testpass123"` | 測試用一次性帳號的 fixture 口令，與本 repo 既有約定一致（`grep -rhoE '"password": "[^"]{0,24}' tests/*.py` 量測得 `testpass123` 69 處／`otherpass123` 32 處）；非秘密，但**與那些既有行同樣落在掃描器的盲區**，所以「未報出」不等於掃描器證明它乾淨 |
| `tests/test_skill_unit_ceiling.py:271` | `"password_hash": "not-a-credential"` | 明確的佔位字串（PG-only 節點自己 INSERT `users` 行用），不是憑證值 |

因此本節的誠實結論是：**A1 新檔沒有引入任何可憑證化的值，但這個結論的根據是逐行清單與既有約定，不是 RC 0。** 放寬規則會移動 findings 總數，而該總數被 `tests/test_env_hygiene_contract.py` 的契約與提交帳釘住，必須與那些斷言同輪改、同輪重量——仍屬 #168，不在本輪動它。

### 16.6 A2 盤點（第一輪，只測量）：技能單位作為「關聯與隔離載體」目前能承載哪幾類

§三A 要求每個單位各自關聯並隔離 10 類狀態。以模型欄位與入口檢查為證據，不以其名字為證據：

| 類別 | 現在的鍵 | 證據 | 判定 |
|---|---|---|---|
| 虛擬資金 | 單位自己的行 | `app/models/paper_portfolio.py:96-107` | **已隔離** |
| 持倉 | `portfolio_id` FK | `:127` | **已隔離** |
| 訂單與成交 | `portfolio_id` FK ＋冪等 unique | `:149`、`uq_paper_order_portfolio_idempotency` | **已隔離** |
| 執行意圖／成交紀錄 | `portfolio_id` FK | `app/models/execution.py:42` | **已隔離** |
| 決策日誌 | `paper_portfolio_id`（**nullable**） | `app/models/paper_portfolio.py:212-214`，註明舊列無法回溯歸屬 | **已隔離（新列）**；舊列刻意不填空 |
| 停止狀態 | 單位 `status`，且被執行入口實際檢查 | `app/services/risk_policy.py:194-217`（retired／completed／paused 拒新單）、`app/services/order_lifecycle.py:62`（排隊單以 `portfolio_paused` 阻住）、`app/services/crypto_paper.py:770`；由 `tests/test_paper_portfolio.py:60-81` 與 `tests/test_manual_decision_books_linked_portfolio.py:929-961` 在本輪群組中綠燈覆蓋 | **真正可用**，不是只有欄位 |
| 風險限制 | 計算以單位為範圍，**上限值是全域一份** | `risk_policy.py:231/242/280/304/334` 依 `portfolio.id` 計算；但 `get_policy()`（`:104-106`）回傳單一 `DEFAULT_RISK_POLICY`，無每單位覆寫 | **半**：算得對，改不了每單位的數值 |
| 策略參數版本 | 執行列**沒有**版本鍵 | `app/models/paper_trading_run.py:18-40` 只有 `skill`（自由文字）／`instrument_id`／`interval_minutes`／`exit_config`，無 `strategy_version_id` | **未關聯** |
| 決策上的版本 | 欄位存在但**無 FK、值由 client 供給且未驗證** | `app/models/paper_portfolio.py:219`（`linked_experiment_version_id`，`nullable`、無 ForeignKey）；寫入點 `app/api/paper_portfolio.py:942` 直接收 `payload.linked_experiment_version_id` | **未驗證**：可填一個不存在或別人帳戶的 UUID |
| 資料來源 | 只在研究側（`ExperimentVersion.dataset_id`），執行側無鍵 | `app/models/experiment_version.py:36-40`（`# Dataset binding fields` 起於 `:36`，`dataset_id` 在 `:37`）；`paper_trading_runs` 無對應欄位 | **未關聯** |
| 訓練快照／訓練任務／model artifact／啟用版本 | 全部以 `owner_id` 為鍵，非單位 | `app/models/agent_strategy.py:47/163/213/254/312`、`app/models/personal_model_selection.py:46/69`、`app/models/training.py:23` | **共用帳戶層**，不隨單位隔離 |
| 單位間隔離（同名冒充） | 每單位自己的行與 cash | 16.1 上限 ＋ `tests/test_skill_unit_ceiling.py` 的 per-account 節點 | **已執行** |

結論（§三A 視角）：10 類裡 **6 類真隔離、1 類半、3 類未關聯、1 類未驗證**。要補齊「策略參數版本／資料來源／訓練側 4 項」需要新的欄位或關聯表，即 **migration**——依 §七，未獲批准不做；這一項與 §14 的 D1–D5 同屬需要擴大批准的集中項目。不需要 migration 就能修的，是上面「決策的版本 UUID 未驗證」那一條：在入口以本帳戶實際存在的 `experiment_versions` 行比對後才接受，這是 §四授權內的入口正確性修正，已排為下一單元。

### 16.7 本節不是什麼

1. **不是 PG 結果**：16.2 三輪全在 `sqlite` 層；`held_slot_count` 的併發正確性只有那個 PG-only 節點能證明，它在這三輪都是 `skipped`，驗證目的地寫在節點自己的 docstring 與 `tests/postgresql_only.py` 的 `PG_LANE_COMMAND`。
2. **不是全套綠燈**：31 支檔案 499 節點是**受影響整合群組**，不是 3,548 節點的全量。全量仍依 §15.7 的預留，等 #171（acceptance launcher 的 CORS origin）收完一次跑。
3. **沒有移動 ceiling／manifest**：新增 12 支節點（10＋2）會讓下一次全量的 collected 不再是 3,548；依 owner「測試數量 gate 不能自動接受最新總數」，本節**不重新釘選**，那必須由 `pin --ack-diff` 在同一輪全量之後做。
4. **不含前端變更**：16.1 末段只證明 409 的 `message` 沿現有 `formatErrorDetail()` 可抵達使用者；沒有改任何 `.tsx`，也沒有開瀏覽器。
5. **A2 是盤點不是交付**：16.6 的缺口一欄都沒動手；其中三類卡在 migration（需要批准），一類不卡（下一輪修）。


