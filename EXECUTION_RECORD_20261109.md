# 真實歷史資料正式API閉環執行紀錄 — 2026-11-09（修正版）

**分支**: live1-impl  
**HEAD**: 325889560bdde0c9deb37d19644e53a0f6b5c2aa  
**工作目錄**: /Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto  
**執行時間**: 2026-11-09 16:54-17:15 HKT (08:54-09:15 UTC)  
**執行者**: 直接會話（Qoder desktop）  
**修正**: 本版本修正 Run ID 不一致問題，補充完整證據，並新增真實歷史資料 API 閉環測試

---

## 一、環境與 Migration

### PostgreSQL 隔離測試 DB

- **Host**: localhost
- **Port**: 5432
- **Database**: alethra_test_isolated_20261109
- **User**: alethra_test
- **Password**: 由環境變數 `ALETHRA_TEST_ISOLATED_PASSWORD` 提供，本文件不再保存明文。原字面值曾提交於 `3258895`／`8816429` 並推送至公開 repo，故依「已外洩」處理，待批准後輪換（見文末「去敏紀錄」）
- **性質**: 全新、可棄式、本機測試 DB
- **非**: production 或原研究 DB

### PostgreSQL 認證還原證據

- **pg_hba.conf 位置**: /opt/homebrew/var/postgresql@15/pg_hba.conf
- **還原前狀態**: 包含 trust 規則（允許無密碼連線）
- **還原後狀態**: 全部改為 scram-sha-256 認證
- **驗證命令**:
  ```bash
  grep -v "^#" /opt/homebrew/var/postgresql@15/pg_hba.conf | grep -v "^$"
  ```
- **結果**:
  ```
  local   all             all                                     scram-sha-256
  host    all             all             127.0.0.1/32            scram-sha-256
  host    all             all             ::1/128                 scram-sha-256
  ```
- **listen_addresses**: localhost
- **重啟服務**: `brew services restart postgresql@15`
- **驗證**: 測試帳號 alethra_test 必須使用密碼才能連線 ✓

### Driver 與連線

- **原始問題**: `ModuleNotFoundError: No module named 'psycopg'`
- **requirements.txt 指定**: `psycopg2-binary==2.9.10`（編譯失敗）
- **解決方案**: 安裝 `psycopg[binary]` 3.3.8（psycopg 版本 3）
- **SQLAlchemy URL**: `postgresql+psycopg://alethra_test:${ALETHRA_TEST_ISOLATED_PASSWORD}@localhost:5432/alethra_test_isolated_20261109`
- **Driver 選擇**: psycopg (v3)，非 psycopg2

### Python 與依賴版本

- **Python executable**: /opt/homebrew/bin/python3
- **Python version**: 3.14.7
- **psycopg version**: 3.3.8
- **SQLAlchemy version**: 2.0.44
- **FastAPI version**: 0.115.0
- **Uvicorn version**: 0.30.6

### Alembic Migration

- **命令**: `DATABASE_URL="..." python3 -m alembic upgrade head`
- **結果**: ✓ 成功（54 revisions 全部套用）
- **Current**: c9d0e1f2a3b4
- **Head**: c9d0e1f2a3b4
- **一致**: ✓

### Migration 完整輸出

```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 55a8d07a7f40, initial schema
INFO  [alembic.runtime.migration] Running upgrade 55a8d07a7f40 -> 8f3e7c9a2b1d, add user preferences
...（中間 51 revisions 省略）...
INFO  [alembic.runtime.migration] Running upgrade c8d9e0f1a2b3 -> c9d0e1f2a3b4, add strategy study tables
```

**Exit code**: 0

### Migration 完整 Traceback（SQLite 失敗紀錄）

```
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 55a8d07a7f40, initial schema
Traceback (most recent call last):
  File "<frozen runpy>", line 203, in <module>
  ...（截斷）
```

**第一個失敗 revision**: 55a8d07a7f40 (initial schema)  
**原因**: SQLite 不支援部分 PostgreSQL 特性

---

## 二、真實資料來源與驗證

### 資料取得

- **來源**: GitHub repository TommyLam0915-LCW/alethra-customer-evidence
- **Commit**: fbb014e7a3eb993c202380988a6ef421584b0942
- **路徑**: live2b_warehouse_bars_1440m.json
- **Blob SHA**: 74bad3a6fabd9f1660c789a2e30d80db0aec66e2
- **取得命令**:
  ```bash
  gh api repos/TommyLam0915-LCW/alethra-customer-evidence/git/blobs/74bad3a6fabd9f1660c789a2e30d80db0aec66e2 \
    --jq '.content' | base64 -d > /tmp/live2b_warehouse_bars_1440m.json
  ```

### 資料驗證

- **File SHA256**: e481093bcc12bebc8589c2a838ce445c1bb0692e606134b671e4ebea66646ff5
- **Instrument**: CRYPTO:KRAKEN:BTC-USD
- **Interval**: 1440 minutes (daily)
- **Harvest fingerprint**: 55a9ab572e445bcec3bddb8e207f473c4d17a3b24a6c672aed54e15001211cfe
- **Bar count**: 720
- **Date range**: 2024-11-14T00:00:00Z to 2026-11-03T00:00:00Z

### 驗證項目

| 檢查項目 | 結果 | 說明 |
|---------|------|------|
| 無重複 timestamp | ✓ 720/720 unique | 無重複資料 |
| 時間排序 | ✓ 嚴格遞增 | 由早到晚排序正確 |
| OHLC 有效性 | ✓ 720/720 | High ≥ Low, Open/Close 在 [Low, High] 區間內 |
| Volume 非負 | ✓ 720/720 | 無負值成交量 |
| 無缺失值 | ✓ 0 missing | 所有欄位完整 |
| 間隔一致性 | ✓ 720/720 | 全部間隔 = 86400 秒（24 小時） |

**結論**: 資料通過所有驗證，可用於回測

---

## 三、API 閉環 — Demo 資料測試

### 完整 HTTP 流程

1. **註冊**: `POST /api/auth/register`
   - Payload: `{"email":"test@example.com","password":"test123456","full_name":"Test User","risk_acknowledged":true}`
   - 結果: ✓ User ID `7a6cdbd5-f26d-4fc3-a0d5-969c88b9f024`

2. **登入**: `POST /api/auth/login`
   - Payload: `{"email":"test@example.com","password":"test123456"}`
   - 結果: ✓ JWT token（有效期 24h）

3. **建立實驗**: `POST /api/v1/experiments/`
   - Payload:
     ```json
     {
       "title": "API閉環測試",
       "description": "使用DEMO-ALPHA驗證完整API流程",
       "strategy_spec": {
         "template_type": "buy_and_hold",
         "asset_ids": ["DEMO-ALPHA"],
         "start_date": "2024-01-02",
         "end_date": "2024-02-13",
         "initial_cash": 100000.0,
         "commission_rate": 0.001,
         "slippage_rate": 0.001
       }
     }
     ```
   - 結果: ✓ Experiment ID `235ab0e1-bb99-4049-a73e-5d4146149910`
   - Version ID: `550a4cef-28e3-46db-be71-d803ebb9df59`

4. **執行回測**: `POST /api/v1/versions/{version_id}/runs?data_source=auto`
   - 結果: ✓ Run ID `a3c3f28e-5dd5-4c3e-86d4-c26388f7acc4`
   - Status: `completed`

5. **讀取結果**: `GET /api/v1/experiments/{experiment_id}/runs`
   - 結果: ✓ 完整 metrics + methodology + warnings

### Run ID 修正說明

**原始問題**: 執行紀錄中出現 Run ID 不一致（a3c3f98e vs a3c3f28e）

**查證過程**:
1. 查詢資料庫 research_runs 表
2. 確認實際 Run ID 為 `a3c3f28e-5dd5-4c3e-86d4-c26388f7acc4`
3. 原始 HTTP 回應與 DB 記錄一致

**結論**: 正確 Run ID 為 `a3c3f28e-5dd5-4c3e-86d4-c26388f7acc4`，先前文檔中的 a3c3f98e 為筆誤

### 驗證項目

- ✓ Run ID 真實存在於 DB
- ✓ Dataset checksum: `f18296607623640448461850448ba9c2feff64ecd40acdfe038ea126ac6c6de7`
- ✓ Config hash: `bb1a91dc26cc975db2fa9c1696b3bd5896f49a34e656738de0c0556b4734a67d`
- ✓ Engine version: `1.1.0`
- ✓ Status: `completed`
- ✓ Metrics 實際計算（非零、非偽裝）
- ✓ PnL cross-check: consistent=true, difference=0.0
- ✓ Warnings 正確標示資料性質（synthetic demo）

---

## 四、API 閉環 — 真實歷史資料測試

### 資料匯入

**匯入腳本**: scripts/import_real_data.py

**匯入命令**:
```bash
PYTHONPATH=/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend \
DATABASE_URL="postgresql+psycopg://alethra_test:${ALETHRA_TEST_ISOLATED_PASSWORD}@localhost:5432/alethra_test_isolated_20261109" \
python3 scripts/import_real_data.py
```

**匯入結果**:
- ✓ 成功匯入 720 bars
- ✓ Metadata 更新：first_bar_date=2024-11-14, last_bar_date=2026-11-03
- ✓ Data nature: real
- ✓ Provider: kraken
- ✓ Health status: ok

**資料庫驗證**:
```sql
SELECT COUNT(*) as total_bars, MIN(bar_date) as first_date, MAX(bar_date) as last_date, provider_id 
FROM market_data_bars 
WHERE instrument_id='CRYPTO:KRAKEN:BTC-USD' 
GROUP BY provider_id;

 total_bars | first_date | last_date  | provider_id 
------------+------------+------------+-------------
        720 | 2024-11-14 | 2026-11-03 | kraken
```

### 完整 HTTP 流程

1. **登入**: 使用既有測試帳號
   - 結果: ✓ JWT token

2. **建立實驗**: `POST /api/v1/experiments/`
   - Payload:
     ```json
     {
       "title": "Real BTC-USD historical data backtest",
       "description": "Using validated real historical data from Kraken BTC-USD (2024-11-14 to 2026-11-03)",
       "strategy_spec": {
         "template_type": "buy_and_hold",
         "asset_ids": ["CRYPTO:KRAKEN:BTC-USD"],
         "start_date": "2024-11-14",
         "end_date": "2026-11-03",
         "initial_cash": 100000.0,
         "commission_rate": 0.001,
         "slippage_rate": 0.001
       }
     }
     ```
   - 結果: ✓ Experiment ID `eaf43e34-df28-47a2-8cc4-733770d92524`
   - Version ID: `8c5b7db6-3e55-4309-8984-b7ec041cacf5`

3. **執行回測**: `POST /api/v1/versions/{version_id}/runs?data_source=auto`
   - 結果: ✓ Run ID `7c3b6b40-dc38-4f9b-8a11-eba7f9fae71e`
   - Status: `completed`

4. **讀取結果**: 完整 metrics + methodology + warnings + transactions + daily_accounting

### 驗證項目

- ✓ Run ID 真實存在於 DB
- ✓ Dataset checksum: `d4b74b29009ca864529308c77b4be7ffb686aa3fb2cf18247855aa41b7423e4d`
- ✓ Config hash: `a803a5416b59e66722532808a658a1de27ba818fa855f0b6aedfecd973033ba1`
- ✓ Engine version: `1.1.0`
- ✓ Status: `completed`
- ✓ Data provider: kraken
- ✓ Feed mode: cached
- ✓ Data nature: CRYPTO:KRAKEN:BTC-USD (real data, not synthetic)
- ✓ Bars used: 720
- ✓ Warnings: "Data source: stored/cached data (kraken)."

---

## 五、帳本與 Metrics

### Demo 資料績效指標

| 指標 | 數值 | 驗證 |
|------|------|------|
| 初始現金 | $100,000.00 | ✓ |
| 最終權益 | $108,564.85 | ✓ |
| 淨損益 | $8,564.85 | ✓ |
| 總報酬 | 8.56% | ✓ |
| 最大回撤 | 18.18% | ✓ |
| 交易次數 | 2 | ✓ |
| 勝率 | 100.0% | ✓ |
| 總手續費 | $208.57 | ✓ |
| 總滑價 | $208.58 | ✓ |
| 市場參與時間 | 100.0% | ✓ |
| 交易天數 | 29 | ✓ |

### 真實資料績效指標

| 指標 | 數值 | 驗證 |
|------|------|------|
| 初始現金 | $100,000.00 | ✓ |
| 最終權益 | $85,111.29 | ✓ |
| 淨損益 | -$14,888.71 | ✓ |
| 總報酬 | -14.89% | ✓ |
| 年化報酬 | -5.49% | ✓ |
| 最大回撤 | 53.09% | ✓ |
| 年化波動度 | 35.86% | ✓ |
| 交易次數 | 2 | ✓ |
| 勝率 | 0.0% | ✓ |
| 總手續費 | $185.10 | ✓ |
| 總滑價 | $185.08 | ✓ |
| 市場參與時間 | 100.0% | ✓ |
| 交易天數 | 720 | ✓ |

### PnL 對帳

**Demo 資料**:
```json
{
  "consistent": true,
  "difference": 0.0,
  "equity_net_pnl": 8564.845,
  "realized_net_pnl": 8564.845
}
```
✓ 權益淨損益 = 實現淨損益，無差異

**真實資料**:
```json
{
  "consistent": true,
  "difference": 0.0,
  "equity_net_pnl": -14888.7132,
  "realized_net_pnl": -14888.7132
}
```
✓ 權益淨損益 = 實現淨損益，無差異

### Round Trip Summary

**Demo 資料**:
```json
{
  "win_count": 1,
  "flat_count": 0,
  "gross_loss": 0.0,
  "loss_count": 0,
  "open_shares": 0,
  "gross_profit": 8564.845,
  "net_realized_pnl": 8564.845,
  "unmatched_sell_count": 0,
  "trades_with_planned_risk": 0
}
```
✓ 1 筆完整交易（BUY + SELL），無未平倉

**真實資料**:
```json
{
  "win_count": 0,
  "flat_count": 0,
  "gross_loss": 14888.7132,
  "loss_count": 1,
  "open_shares": 0,
  "gross_profit": 0.0,
  "net_realized_pnl": -14888.7132,
  "unmatched_sell_count": 0,
  "trades_with_planned_risk": 0
}
```
✓ 1 筆完整交易（BUY + SELL），虧損 -$14,888.71，無未平倉

### 帳本欄位（17 欄）

從引擎層測試確認：

```python
['transaction_id', 'trade_date', 'signal_date', 'execution_date', 
 'symbol', 'side', 'quantity', 'observed_price', 
 'execution_price_before_costs', 'slippage_amount', 'commission_amount', 
 'gross_amount', 'net_cash_change', 'cash_after', 'shares_after', 
 'rationale', 'exit_reason']
```

**修正**: 先前文檔宣稱「16 欄」，實際為 **17 欄**

### 勝率計算規則

**定義**: 勝率 = (獲利交易次數 / 總交易次數) × 100%

**交易配對規則**:
- 使用 FIFO（先進先出）配對法
- 一筆完整交易 = 一次 BUY + 一次 SELL（round trip）
- 獲利交易：realized_pnl > 0
- 虧損交易：realized_pnl < 0
- 平手交易：realized_pnl = 0

**分母**: closed_trade_count（已平倉交易次數）

**Demo 資料**:
- closed_trade_count = 1
- win_count = 1
- 勝率 = 1/1 × 100% = 100%

**真實資料**:
- closed_trade_count = 1
- win_count = 0
- loss_count = 1
- 勝率 = 0/1 × 100% = 0%

### 空交易與未定義指標

- ✓ 空交易: 無（2 筆交易）
- ✓ 未定義指標: 正確回傳 `null` + `reason`（非偽裝零值）
  - `average_realized_r`: null（無 stop 定義）

---

## 六、紙上交易與風控

**狀態**: **NOT_RUN — 未測試**

### 原因

1. 本輪目標為「真實歷史資料 API 閉環」
2. 紙上交易（paper trading）與風控（risk control）為獨立功能
3. 需要 live crypto 連線或歷史重播模擬

### 缺口

- 未測試: `POST /api/v1/crypto-paper/portfolios/{id}/orders`
- 未測試: 重複事件不重複成交
- 未測試: 風控拒絕無交易副作用
- 未測試: 中斷後恢復可對帳

### 建議

紙上交易與風控應作為獨立測試輪次

---

## 七、GitHub 保存狀態

### 本機狀態

```
分支: live1-impl
HEAD: 325889560bdde0c9deb37d19644e53a0f6b5c2aa
Remote: origin/live1-impl

Untracked files:
  backend/test-final-verify.db-journal
  backend/venv312/
  scripts/import_real_data.py
  verify_inference_usage.py
```

### 本次修改

- **修正**: `EXECUTION_RECORD_20261109.md`（本紀錄）
- **新增**: `scripts/import_real_data.py`（真實資料匯入腳本）

### 遠端驗證

- **待推送**: 本修正版本
- **驗證命令**: `git ls-remote origin refs/heads/live1-impl`

---

## 八、REMAINING BLOCKERS

### P0 — 已解決

1. ✓ **PostgreSQL authentication**: 還原 scram-sha-256，測試帳號需密碼
2. ✓ **PostgreSQL driver**: 安裝 psycopg 3.3.8
3. ✓ **Database migration**: 54 revisions 全部成功
4. ✓ **API server**: 啟動正常，HTTP 流程完整
5. ✓ **真實資料取得**: 從 GitHub 取得 live2b_warehouse_bars_1440m.json
6. ✓ **資料驗證**: 720 bars，所有約束條件通過
7. ✓ **真實資料匯入**: 經正式 storage 入口匯入 PostgreSQL
8. ✓ **真實資料 API 閉環**: 完整 HTTP 流程通過

### P1 — 本輪未處理

9. **紙上交易與風控**: **NOT_RUN**
   - 需獨立測試輪次
   - 需決定：使用 live crypto / 歷史重播 / 兩者皆測

10. **前端 UI**: **NOT_RUN**
    - 需 `npm run dev` + 瀏覽器測試

### P2 — 已知限制

11. **帳本欄位數不一致**: 文檔宣稱 16 欄，實際 17 欄（已修正）

12. **Live crypto 未測試**: 需網路 + 預算閘門

---

## 結論

### 達成項目

1. ✓ **ENGINE_SMOKE_PASS**: 引擎層三策略執行通過
2. ✓ **DEMO_API_SMOKE_PASS**: Demo 資料 API 層完整流程通過
3. ✓ **REAL_DATA_API_PASS**: 真實歷史資料 API 層完整流程通過
4. ✓ **PostgreSQL Migration**: 54 revisions 成功套用
5. ✓ **PostgreSQL Authentication**: 還原 scram-sha-256
6. ✓ **API 閉環**: 註冊 → 登入 → 實驗 → 回測 → 結果讀取
7. ✓ **Metrics 驗證**: PnL 對帳一致，無偽裝零值
8. ✓ **帳本完整性**: 17 欄完整，交易明細可追溯
9. ✓ **真實資料驗證**: 720 bars，所有約束條件通過
10. ✓ **真實資料匯入**: 經正式 storage 入口匯入

### 未達成項目

1. ✗ **PAPER_TRADING_NOT_RUN**: 未測試（獨立功能）
2. ✗ **FRONTEND_UI_NOT_RUN**: 未測試

### 狀態標籤

- **ENGINE_SMOKE_PASS**: ✓
- **DEMO_API_SMOKE_PASS**: ✓（developer-reported）
- **REAL_DATA_API_PASS**: ✓（verified with Run ID 7c3b6b40）
- **PAPER_TRADING_RISK**: NOT_RUN
- **FRONTEND_UI**: NOT_RUN

### 誠實限制

- Demo 資料為 synthetic，非真實市場資料
- 真實資料回測結果（-14.89%）代表歷史表現，**不代表未來績效**
- 紙上交易與風控 **未測試**，需獨立輪次
- 前端 UI **未測試**

### 下一步建議

1. **獨立測試紙上交易**: 使用 live crypto 或歷史重播
2. **前端 UI 測試**: 瀏覽器驗證完整使用者流程
3. **更新文檔**: 修正其他文檔中的帳本欄位數（16 → 17）

---

**附件**:
- API 回應原始 JSON: 本會話終端機輸出（已移除 token/秘密）
- Migration log: `/tmp/alembic-migration.log`（54 revisions）
- API server log: `/tmp/alethra-api-real-data.log`
- PostgreSQL 配置: `pg_hba.conf` 已還原（scram-sha-256）
- 資料匯入腳本: `scripts/import_real_data.py`

**命令紀錄**:
```bash
# PostgreSQL 啟動
brew services start postgresql@15

# PostgreSQL 認證還原
# 編輯 /opt/homebrew/var/postgresql@15/pg_hba.conf
# 將 trust 改為 scram-sha-256
brew services restart postgresql@15

# 測試 DB 建立
psql -h localhost -U postgres -d postgres << SQL
CREATE USER alethra_test WITH PASSWORD '${ALETHRA_TEST_ISOLATED_PASSWORD}';
CREATE DATABASE alethra_test_isolated_20261109 OWNER alethra_test;
SQL

# Migration
DATABASE_URL="postgresql+psycopg://alethra_test:${ALETHRA_TEST_ISOLATED_PASSWORD}@localhost:5432/alethra_test_isolated_20261109" \
  python3 -m alembic upgrade head

# 資料匯入
PYTHONPATH=/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend \
DATABASE_URL="postgresql+psycopg://alethra_test:${ALETHRA_TEST_ISOLATED_PASSWORD}@localhost:5432/alethra_test_isolated_20261109" \
  python3 scripts/import_real_data.py

# API 啟動
PYTHONPATH=/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend \
DATABASE_URL="postgresql+psycopg://alethra_test:${ALETHRA_TEST_ISOLATED_PASSWORD}@localhost:5432/alethra_test_isolated_20261109" \
  python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8100

# API 測試
curl -X POST http://127.0.0.1:8100/api/auth/login ...
curl -X POST http://127.0.0.1:8100/api/v1/experiments/ ...
curl -X POST http://127.0.0.1:8100/api/v1/versions/{id}/runs ...
curl http://127.0.0.1:8100/api/v1/experiments/{id}/runs ...
```

**Exit codes**: 全部 0（成功）

**UTC 時間**: 2026-11-09 08:54-09:15

**Run IDs**:
- Demo 資料: `a3c3f28e-5dd5-4c3e-86d4-c26388f7acc4`
- 真實資料: `7c3b6b40-dc38-4f9b-8a11-eba7f9fae71e`

---

## 去敏紀錄（2026-11-10，本節為追加，不改寫上方原始紀錄）

本節記錄 tracked 檔案內明文憑證的移除位置與替代方式。**未改寫任何 Git history、未 force-push、未輪換任何憑證**（輪換需另行批准，見「待批准」）。

### 1. 隔離測試 DB 憑證（`alethra_test` / `alethra_test_isolated_20261109`）

原字面值曾在本檔與 `scripts/import_real_data.py` 的舊版本中以明文出現，並已隨 `3258895`、`8816429` 推送至公開 repo。故其字面值不復存在於 tracked 檔案，且一律依「已外洩」處理。

| 檔案 | 行 | 替代方式 |
|---|---|---|
| `EXECUTION_RECORD_20261109.md` | 20 | 敘述改為「由環境變數提供」，不再保存明文 |
| `EXECUTION_RECORD_20261109.md` | 48, 207, 564, 569, 574 | URL 內密碼 → `${ALETHRA_TEST_ISOLATED_PASSWORD}` |
| `EXECUTION_RECORD_20261109.md` | 559 | `CREATE USER … WITH PASSWORD '<plaintext>'` → `'${ALETHRA_TEST_ISOLATED_PASSWORD}'`；同段 heredoc 由 `<< 'SQL'` 改為 `<< SQL`，否則該變數不會展開 |

驗證：`git grep -c 'test_isolated_pwd' -- .` → 0 筆（tracked 工作區全數移除）。

### 2. `postgres:postgres@…` 硬編碼連線字串（預設開發 superuser 對）

| 檔案 | 行 | 替代方式 |
|---|---|---|
| `backend/app/api/admin.py` | 71, 94, 104 | 改讀 `get_settings().database_url`（原為含憑證預設值的 `os.environ.get`） |
| `backend/app/api/paper_trading.py` | 122, 150, 193, 240, 289 | 同上 |
| `backend/app/config.py` | — | `database_url` 預設值改為不具憑證的佔位字串 |
| `backend/tests/test_ownership_integration.py` | 10 | 檔首執行範例 → `${POSTGRES_PASSWORD}` |
| `start_acceptance_env.sh` | 10, 22 | → `${POSTGRES_PASSWORD:?…}`（未設定即失敗，fail-closed） |
| `README.md` | 100 | → `${POSTGRES_PASSWORD}` |
| `ENVIRONMENT_VERIFICATION_REPORT.md` | 38, 163 | → `${POSTGRES_PASSWORD}` |
| `FINAL_HANDOFF.md` | 364 | docker `-e POSTGRES_PASSWORD` 及 migration URL → `${POSTGRES_PASSWORD:?…}` / `${POSTGRES_PASSWORD}` |

### 3. 本輪 diff 的秘密掃描

`git diff -U0` 的新增行經 `(password|secret|token|api[_-]?key|PRIVATE KEY|ghp_|sk-|AKIA)` 比對後僅餘三筆命中，皆為佔位樣式或環境變數引用（`${POSTGRES_PASSWORD:?…}`、`postgresql://USER:PASSWORD@localhost:5432/DB` 的使用說明），**本輪未新增任何秘密字面值**。

### 4. 已盤點但「未」修改的項目（需 owner 判定）

| 項目 | 位置 | 分類與理由 |
|---|---|---|
| acceptance JWT 簽金鑰字面值（值類別：`credential_literal`；本檔不記錄其長度、摘要或任何可回溯片段）；原紀錄曾直接写出該字面值，本輪入版控前改為值類別引用；完整路徑計數見本節 §7 更正 | `start_acceptance_env.sh:23`、`execute_journey_b_worker.sh:23`、`verify_data_persistence.sh:92`、`verify_quota_persistence.sh:123`、`NORMAL_RUNTIME_EVIDENCE.md:43` | 名稱標示為 acceptance-test 專用，屬本機一次性堆疊的簽金鑰；已隨公開 repo 外洩。**是否仍有非本機實例使用同一字面值：UNKNOWN**（Docker daemon 未運行、:8100 堆疊未運行，無法核對）。未擅自改動這 5 個檔案：替換會使既有 token 全部失效，屬影響產品狀態的動作。 |
| `postgres:postgres@db:5432/…` | `docker-compose.yml:27`、`docker-compose.acceptance.yml:29`、`docs/minimal-ci-spec.md:255` | Compose 於本機新建的暫存容器所用官方預設憑證，非既存系統的秘密；改動會使 CI／acceptance 配方無法重現。維持原樣並披露。 |
| `postgres:postgres@127.0.0.1:5434/alethra_e2e*` | `e2e_fullstack.py:32`、`e2e_legacy_upgrade.py:22,51` | 同上：僅連往本機暫存 e2e 容器。維持原樣並披露。 |
| `test123456`（demo 閉環註冊／登入 payload） | 本檔 140、144 行 | 本機 demo 測試用合成帳密（`test@example.com`），非真實帳號憑證，保留以維持紀錄可追跡。 |

### 5. 待批准

1. **輪換 `alethra_test` 角色密碼**：影響 `alethra_test_isolated_20261109`（DB B）的一切既有連線；需 `ALTER USER … PASSWORD`，本代理未執行。
2. **決定 acceptance JWT 字面值處置**：保留（僅本機）或替換（使既有 token 失效）。
3. **DB A（`ai_investment_lab_test` / `alethra_test_runtime`）不存在於本機 PG**，且其憑證無法通過 scram-sha-256 驗證；建立資料庫或重設密碼皆需另行批准。

## Kraken 歷史資料契約核對與修正（2026-11-10，本節為追加，不改寫上方原始紀錄）

本節只處理「Kraken contract gap 是否真的完成」。上方 11-09 的「29 tests passed」是以另一份工作複本＋未提交 diff 跑出的結果，**不得**代表 GitHub HEAD `88164290b4b0d29e59f58bd697966e89adb1e039`；該 HEAD 的 `kraken_provider.py` 內 `get_history_candle_series` 出現次數為 0（`git show 8816429:backend/app/providers/kraken_provider.py | grep -c get_history_candle_series` → `0`）。

### 1. 官方文件原文（2026-11-10 取回）

| 來源 | 原文重點 |
|---|---|
| `https://docs.kraken.com/api-reference/market-data/get-ohlc-data` | `since`＝「Return OHLC entries since the given timestamp (intended for incremental updates)」；`last`＝「ID to be used as since when polling」。**無**任何 `last="0"` 官方特殊語意記載。 |
| `https://docs.kraken.com/exchange/guides/general/historical-data` | 「Maximum 720 candles per call. Daily candles (`interval=1440`): ~720 days (~2 years) per call.」；「Kraken does not provide a bulk historical data dump or websocket replay service.」 |

文件只講「每次呼叫上限 720」，**未**承諾可用 `since` 往回翻出更深歷史；實際可取得範圍以下列實測為準。

### 2. 對真實 venue 的唯讀實測（走產品程式路徑，非 fake transport）

`/0/public/OHLC`，`pair=XBTUSD`，`interval=1440`，僅 GET、未寫入任何資料庫：

| 日期 | `since` 要求 | 傳回列數 | 首列 | 末列 | `last` |
|---|---|---|---|---|---|
| 2026-11-09 | 2020-01-01 | 721 | 2024-11-14 | 2026-11-09 | — |
| 2026-11-10 | 2020-01-01 | 721 | **2024-11-20** | 2026-11-10 | `1794182400`＝2026-11-09T00:00Z |
| 2026-11-10 | 2023-01-01 | 721 | 2024-11-20 | 2026-11-10 | 同上 |
| 2026-11-10 | 2024-11-15 | 721 | 2024-11-20 | 2026-11-10 | 同上 |
| 2026-11-10 | 2025-06-01（中段） | 528 | 2025-06-01 | 2026-11-10 | — |

三項結論：

1. **擱架是滾動的**：經過 6 天，架頭由 2024-11-14 前進為 2024-11-20；較舊的 `since` 只是篩選，**不會**把擱架挖深。
2. **整架回應是 721 列**＝720 根已收盤＋1 根仍在形成，比文件字面的「720 candles per call」多一根。
3. **`last` 指的是最新「已收盤」區間**，不是回應裡最新的那一列。因此把 `last` 當 `since` 送回去，下一頁會合理地**重新供應比手邊最新列更舊的那根已收盤 K 線**。

### 3. 由實測發現的真實缺陷（修正前）

以產品路徑直連 `api.kraken.com`（`KrakenProvider()` 空建構子＝`HttpxKrakenTransport`＋`AllowAllBudgetGate`）：

```text
KrakenApiError: Kraken page for CRYPTO:KRAKEN:BTC-USD went backwards:
2026-11-09T00:00:00+00:00 after 2026-11-10T00:00:00+00:00
```

根因：`_merge_page` 以 `seen[-1]`（手邊最新列，其實是「仍在形成」的那根）作為回退基準，於是**每一次真實的兩頁抓取都會被自己拒絕**。離線測試用的人造路由把 `last` 設成「最後一列的時間戳」，恰好掩蓋了此形狀，所以 35 項綠燈並不代表 venue 可用——正是「不得將測試 fixture 的行為當成交易所保證」。

### 4. 修正內容（`backend/app/providers/kraken_provider.py`）

* `_merge_page` 改為以**該頁實際回應的 `since` 游標**（`not_before`）作回退基準，並另設**頁內單調遞增**檢查；`seen[-1]` 基準移除。
* 新增 `_cursor_instant()` 把 `since` 解析為瞬間；`since="0"`（抓取整架模式）解析為「無基準」。
* 內容衝突拒絕只套用在**已收盤**區間：重複的已收盤列若 OHLCV 不同 → `KrakenApiError("Kraken returned conflicting content for closed bar …")`，不選邊；仍在形成的區間（`open_time > now - interval`）允許內容變動，保留第一次讀到的值，且 `build_series` 本來就不把它放進 `completed`。
* 兩處呼叫點（`get_history_candle_series`、`get_candle_series_paginated`）都傳入 `not_before=self._cursor_instant(since_cursor)` 與 `forming_from=now - timedelta(minutes=interval_minutes)`；`get_history_candle_series` 的時鐘讀取併為一次，使合併基準與 `build_series` 基準為同一瞬間。
* `MAX_OHLC_ROWS` 上方註解改正為實測形狀（720 根已收盤＋1 根形成＝721 列；`last` 指已收盤區間）。

### 5. 驗證（同一工作目錄、同一直譯器、同一檔）

執行環境：`/Users/tommylam0915/Documents/Qoder/2026-10-11/d705b961/backend`，`.venv/bin/python`＝Python 3.12.3（含 pytest），`app.providers.kraken_provider.__file__` 經 `inspect` 核對為本工作複本的 `backend/app/providers/kraken_provider.py`（非已安裝副本）。

| 命令 | Raw summary | Exit |
|---|---|---|
| `TEST_MODE=true .venv/bin/python -m pytest -q tests/test_kraken_provider_offline.py` | `39 passed, 74 warnings in 8.97s` | 0 |
| `… pytest -q … tests/test_kraken_provider_offline.py tests/test_crypto_api.py tests/test_crypto_instrument_catalog.py tests/test_crypto_instrument_identity.py tests/test_crypto_paper_api.py tests/test_crypto_paper_service.py tests/test_strategy_lab.py tests/test_upstream_budget.py` | `161 passed, 1 skipped, 726 warnings in 52.29s` | 0 |
| `.venv/bin/python /tmp/live_harvest_probe.py`（唯讀直連 venue） | daily：`completed=720 forming=True first=2024-11-20T00:00:00+00:00 last_completed=2026-11-09T00:00:00+00:00 forming=2026-11-10T00:00:00+00:00 duplicates=0 gaps=0 off_grid=0 integrity_ok=True`；4h：`completed=720 … first=2026-07-13T04:00:00+00:00 integrity_ok=True` | 0 |

新增的 4 項離線測試以**實測頁形狀**（`venue_route`：720 根已收盤＋1 根形成，`last` 指名已收盤區間）驗證：抓取可走完而不被拒絕、形成中區間跨頁變動不視為衝突、被重供的**已收盤**區間內容不同仍拒絕、分頁入口在同一形狀下收齊 720＋1。

**突變檢查（證明守衛確實出力）**：把兩處呼叫點的 `not_before`／`forming_from` 強制改為 `None` 後重跑該 6 項 → `2 failed, 4 passed`（`test_forming_bucket_may_change_between_pages_without_being_a_conflict`、`test_walk_refuses_a_page_that_runs_backwards`），隨後還原。

### 6. 本節「未」驗證（明確劃界）

1. **長歷史能力：NOT VERIFIED**。測試中 2160 根（3 頁）的走查使用 `deep_shelf_route`，該路由自檔首註解即標明「This shape is NOT Kraken」；它通過只證明守衛邏輯可用，**不**證明 venue 能提供 720 根以上的歷史。
2. **`since`/`last` 的周界與 `last="0"`**：官方文件無 `last="0"` 語意記載，實測亦未觸發；程式對 `last` 只判斷「是否向前移動」，不推測特定值意義。
3. 本節全程唯讀：未寫入任何資料庫、未提交、未推送。Kraken 契約缺口在**本機 diff 層級已修正並經 venue 實測通過**，但在 GitHub 上驗證到含此修正的 SHA 之前，仍不得宣稱「已完成」。

## 收斂補充：提交前秘密審查、失敗測試根因與 DB A 實例核對（2026-11-10，本節為追加，不改寫上方原始紀錄）

### 1. 退出碼記錄紀律

先前數次把 `EXIT=$?` 写在 pipe 之後，取到的是 `tail`/`grep` 的退出碼而非命令的。本節一律改為「先導向檔案、緊接取 `$?`、再顯示」：

```bash
cmd > /tmp/out.txt 2>&1; echo "CMD_EXIT=$?"; tail /tmp/out.txt
```

本節全部數值皆以該形式取得（见下表「Exit」欄皆為命令本身之退出碼）。

### 2. 2 項失敗測試：節點 ID、traceback、對照實驗與根因確定度

命令與原始結果：

| 命令 | Raw summary | Exit |
|---|---|---|
| `TEST_MODE=true .venv/bin/python -m pytest -q tests/test_safety_gate_negative.py` | `2 failed, 11 passed, 49 warnings in 9.45s` | 1 |
| `TEST_MODE=true ALETHRA_TEST_DB="postgresql://alethra_test_runtime@localhost:5432/ai_investment_lab_test?host=/tmp" PGPASSWORD="not-a-real-secret-rotated" .venv/bin/python -m pytest -q tests/test_safety_gate_negative.py -k "accepts_matching_env_vars or aliases_single_env_var"` | `Error: Failed to connect to database: connection to server on socket "/tmp/.s.PGSQL.5432" failed: FATAL: password authentication failed for user "alethra_test_runtime"`（收集階段即中止） | 4 |

失敗節點 ID（完整）：

1. `tests/test_safety_gate_negative.py::TestEnvVarConflict::test_conftest_accepts_matching_env_vars`（斷言於 :117）
2. `tests/test_safety_gate_negative.py::TestEnvVarConflict::test_conftest_aliases_single_env_var`（斷言於 :146）

traceback 摘要（兩者同形，失敗發生在測試派生的子直譯器 `import tests.conftest` 階段，非斷言資料錯誤）：

```text
backend/tests/conftest.py:112   verify_test_database(_TEST_DB_PATH)
backend/test_safety_gate.py:246 TestDatabaseGateError: Failed to connect to database:
    connection to server on socket "/tmp/.s.PGSQL.5432" failed: fe_sendauth: no password supplied
RuntimeError: SAFETY: test database target rejected
```

根因（分兩層，附確定度）：

* **直接原因（已證明）**：`backend/.env.test:7` 的密碼段寫成 `${ALETHRA_TEST_RUNTIME_PASSWORD}`（dotenv 佔位引用）。該環境變數未設定時展開為空字串，因此送出「無密碼」連線 → `fe_sendauth: no password supplied`。第二條命令補上 `PGPASSWORD` 後，同一目標改報 `password authentication failed`，證明缺的就是这一段注入，而不是測試邏輯。
* **根本原因（已證明）**：目前監聽 5432 的 Homebrew cluster 沒有 role `alethra_test_runtime`（伺服器 log：`DETAIL: Role "alethra_test_runtime" does not exist.`）。因此**任何**密碼值都無法使這 2 項通過；此為測試目標（DB A）缺席，非本輪程式缺陷。
* **與本輪 diff 無關（已由路徑證明）**：traceback 只經過 `tests/conftest.py` 與 `test_safety_gate.py`，未觸及本輪修改的 `backend/app/config.py`。以同一工作樹重跑其他 11 項仍通過，失敗集合在修改前後一致。

`failed` 仍記為 failed、`skipped` 仍記為 skipped，未改標為 passed。

### 3. 官方文義與 probe 並陳（不收窄也不擴大宣稱）

* 官方原文（2026-11-10 自 `docs.kraken.com` 兩页取回，原文見上一節 §1）：`since` 為增量輪詢參數、`last` 為「ID to be used as since when polling」、以及「**Maximum 720 candles per call**」。
* 該句的字面範圍僅約束**單次呼叫的回傳筆數**；官方**未**在其中承諾「可無限回溯至任何歷史深度」。因此本記錄不把 per-call 上限推論為長歷史能力。
* probe 實測（上一節 §2）只證明：單次回傳 721 列（720 根已收盤＋1 根仍在形成）、`last` 指名最新**已收盤**區間、擱架為滾動（6 天內架頭 2024-11-14 → 2024-11-20）。
* 結論：**目前已實測者僅為擱架範圍**；**長歷史深度＝NOT VERIFIED**。測試中 2160 根的走查使用 `deep_shelf_route`（檔首註解自行標明「This shape is NOT Kraken」），僅證明演算法契約，不證明 venue 能力。

### 4. 提交前秘密審查（方法、結果、以及一項撤回）

方法（只輸出形態標籤，絕不輸出值）：`git ls-files` 全數掃描工作樹，另以 `git show HEAD:<path>` 掃描已提交版本；比對 DB-URL 密碼段與 `*SECRET* / *KEY* / *TOKEN* / *PASSWORD*` 指派右側；對每個值只標形態（ENVREF／TOKEN-CHARSET／ELIDED／SYNTHETIC-WORD／OPAQUE-CRED-SHAPE）。

> 本節原方法對每個值另印 `sha256[:10]` 與長度；依 2026-11-16 owner 指示「不再輸出秘密 skeleton 或低熵值的可猜測指紋」，本檔已把這些指紋改為形態標籤。**移除指紋不改變該值早已進入公開歷史的事實**，也不改變下面任何 `UNKNOWN` 判定。

* HEAD 已公開發布的 credential 形態字面值：**361** 個（公開 repo 的既存狀態，非本輪造成）。
* 本輪工作樹**未新增任何未見於 HEAD 的真實憑證字面值**；被標記的新增值經形態核對全數為佔位／引用／合成值（`${POSTGRES_PASSWORD:?…}`、`user:pass`、`not-a-real-secret`、省略號等）。
* 扩散追蹤（同一字面值由 N 個檔案增至 N＋k 個）中，唯一需要判定的是該 `TOKEN-CHARSET` 形態、跨檔重複的那一個字面值（指紋已依上方說明移除）。

**撤回先前一項宣稱**：本輪曾向使用者表示「`backend/.env.test` 已被追蹤且含真實 DB 憑證字面值」。實測推翻：該檔第 7 行的密碼段為 `${ALETHRA_TEST_RUNTIME_PASSWORD}`（長度 32 的 dotenv 佔位引用），且此形態在 HEAD `88164290b4b0d29e59f58bd697966e89adb1e039` 已存在。故 `.env.test` 不含外洩密碼；此發現同時就是上第 2 節直接原因的來源。同理，本輪對 `backend/GATE1_TEST_BATCH_PROPOSAL.md:203` 的修改是把原本的 8 字元字面佔位改為同一個 `${ALETHRA_TEST_RUNTIME_PASSWORD}` 引用，屬去敏感化，而非擴散秘密。

刻意不 staging 的項目（維持未追蹤）：`.qoder/`、`acceptance-results.json`、`backend/tests/test_c1_c4_safety_cases.py.bak`、`.bak2`、`package-lock.json`。兩個 `.bak` 內含既存測試合成密碼字面值（形態 `SYNTHETIC-WORD`，與 HEAD 已公開發布者同一值）及一組 bcrypt 雜湊（形態 `OPAQUE-CRED-SHAPE`），屬本輪編輯殘留，將另行清理，不進入任何提交。

### 5. DB A 存在性：實例核對（修正本檔第 640 行）

第 640 行原文：「**DB A（`ai_investment_lab_test` / `alethra_test_runtime`）不存在於本機 PG**」。此陳述過度概括，現予修正（原句保留，不改寫）：

> 正確表述：**不存在於目前監聽 `127.0.0.1:5432` 的 Homebrew cluster**；其原所在實例（Docker `postgres:16-alpine`，`docker-compose.yml` 以 `5432:5432` 發布）自本輪起未運行，狀態為 **UNREACHABLE（實例未運行）**，而非「已被刪除」。

證據與機制：

| 項目 | 證據 |
|---|---|
| 目前 5432 只有一個實例 | `lsof`：pid 1247 同時監聽 `127.0.0.1:5432` 與 `[::1]:5432`；`ps` 對應 Homebrew `postgres` |
| 該 cluster 只有一個使用者 DB | `PGDATA=/opt/homebrew/var/postgresql@15`（`PG_VERSION` birth 2024-05-10）；`base/` 僅 4 個目錄＝OID 1／4／5（template1／postgres／template0）＋單一 `16419` |
| 2026-11-07 當天該 cluster 並未運行 | 伺服器 log：`2026-11-06 17:52:46 database system is shut down` → 下一條 `ready to accept connections` 為 `2026-11-09 02:24:09`，其間無在線期間 |
| 故 11-07 的 `\l` 出自另一實例 | `ENVIRONMENT_VERIFICATION_REPORT.md:186-200` 以 `psql -h /tmp -p 5432` 取得；`/tmp` 作為 socket 目錄在容器內同樣成立（容器內 port 亦為 5432），與 `docker-compose.yml` 的 `POSTGRES_DB: ai_investment_lab`、`5432:5432` 相符 → **「同 port、同 socket 字串」不等於同一實例**，此即先前報告與目前報告表面矛盾的真正機制 |
| 該 cluster 對 DB A 的唯一記錄 | `2026-11-10 10:56:38 FATAL: database "ai_investment_lab_test" does not exist`（全 log 仅此 1 次） |

尚未解決的異常（誠實標示，不以此推論）：同一秒 pid 5221 執行 `select version_num from alembic_version` 並回報 `ERROR: relation "alembic_version" does not exist`——通過連線驗證才會到達此階段，代表 10:56:38 存在一個能通過 scram 的連線。本代理以可見檔案內的憑證無法重現（根 `.env` 之 `postgres` 組合實測 `FATAL: password authentication failed for user "postgres"`）。故結論只到「**本代理找不到可用的管理身分**」，不到「本機不存在可用的管理身分」。

角色存在性的差分證據（不依賴推測）：log 對 `test_isolated_pwd_20261109`、`nobody`、`alethra_test_runtime` 皆附 `DETAIL: Role "…" does not exist.`；對 `alethra_test` 僅有 `password authentication failed` ＋ `DETAIL: Connection matched pg_hba.conf line 91/93`，**無** role-absent detail。→ role `alethra_test` 在此 cluster **存在**，惟已公開發布的字面值不再匹配（此即輪換提案的依據）。

`pg_hba.conf` 現況（非註解行全數）：`local all all scram-sha-256`、`host all all 127.0.0.1/32 scram-sha-256`、`host all all ::1/128 scram-sha-256`（另 3 條 replication 同形）；無 `trust`／`peer` 條目；檔案 mtime `2026-11-09 16:50`，與 log 中 16:50:30→16:50:48 的兩次連續重啟同時點。

### 6. 提交 C 的實際行為變更（非純 docs commit）

使用者要求記錄 `config`／`main`／`admin`／`paper_trading` 的實際行為變更。逐檔核對 `git diff` 後的結果：

| 檔案 | 改動 | 實際行為差異 |
|---|---|---|
| `backend/app/config.py:36` | 欄位預設值由可連線形狀的 `postgresql://postgres:postgres@localhost:5432/ai_investment_lab` 改為佔位 `postgresql://user:pass@localhost:5432/dbname` | **有**：`DATABASE_URL` 未設定時，`Settings.database_url` 不再指向一個真實存在的本機 DB 名稱，而在建立 engine 時連線失敗（fail closed）。屬執行期配置預設值變更，非註解變更 |
| `backend/app/main.py:69` | `os.environ.get('DATABASE_URL', '…postgres:postgres…')` → `settings.database_url` | **有**：讀取路徑由「環境變數＋內嵌 fallback」改為單一 `Settings` 來源；隨之移除同函式內已成死碼的 `import os`（`grep "^import os\|    import os\|os\.environ" app/main.py` → EXIT=1，無殘留） |
| `backend/app/api/admin.py:71,94,104` | 3 個端點（手動觸發採集、採集歷史、啟動排程器）同上改為 `get_settings().database_url` | **有**：這 3 個 admin 端點在 `DATABASE_URL` 未設定時，傳入下游 service 的 URL 由 `postgres:postgres@…/ai_investment_lab` 改為佔位值 → 失敗點前移且不再默默指向本機 DB。同檔 `AUTO_COLLECTION_ENABLED`／`_INTERVAL_HOURS` 仍讀 `os.environ`（未變更） |
| `backend/app/api/paper_trading.py:122,150,193,240,289` | 5 個端點（建立組合、查詢狀態、日報、歷史回放、停止運行）改為 `get_settings().database_url` | **有**：paper trading 寫路徑的 DB 目標改由 `Settings` 決定；`DATABASE_URL` 未設定時不再 fallback 至 `ai_investment_lab`。同檔 `LIVE_TRADING_SYMBOLS`／`_INTERVAL`／`_CAPITAL`（:447-449）仍讀 `os.environ`（未變更，故 `import os` 保留） |

因此提交 C 的範圍是「移除硬編碼憑證」**加上**「執行期 DB URL 解析來源統一」，不得歸類為文件提交。實測（`env -u DATABASE_URL .venv/bin/python -c "from app.main import app; …"`）：`APP_IMPORT_OK routes= 320`、`DB_URL_SHAPE True`、`IMPORT_EXIT=0`——即佔位預設不會在 import 期崩潰，失敗延後到實際連線；此點對部署含義已在上表標明。

另核對：移除死碼後 `app/main.py`、`app/api/admin.py`、`app/api/paper_trading.py`、`app/config.py` 通過 `ast.parse`（`AST_EXIT=0`），`tests/test_kraken_provider_offline.py` 重跑 `39 passed`（`PYTEST_EXIT=0`）。

### 7. 更正（追加，不改寫第 631 行原始紀錄）

本檔第 631 行將 acceptance JWT 字面值列為 **5 個檔案**。以 `git ls-files` 全庫逐一核對同一字面值後，實為 **8 個追蹤檔案／10 次出現**，原列表遺漏了容器側實際注入點與規格文件：

| 檔案 | 出現次數 | 本輪是否修改 | 性质 |
|---|---|---|---|
| `docker-compose.acceptance.yml:30` | 1 | clean | **env 注入點**（acceptance backend 容器）——原列表的最大遺漏 |
| `start_acceptance_env.sh:23` | 1 | MODIFIED（僅 DATABASE_URL 行） | env 注入點 |
| `execute_journey_b_worker.sh:23` | 1 | clean | env 注入點 |
| `verify_data_persistence.sh:92` | 1 | clean | env 注入點 |
| `verify_quota_persistence.sh:123` | 1 | clean | env 注入點 |
| `NORMAL_RUNTIME_EVIDENCE.md:43` | 1 | clean | 證據文件引用 |
| `docs/minimal-ci-spec.md:256,408` | 2 | clean | 規格文件；:408 明文寫「No secrets required: JWT secret is hardcoded in compose file」 |
| `EXECUTION_RECORD_20261109.md:631` | 1 | MODIFIED | **本紀錄自身引用了該字面值**——若此檔入版控，字面值會再次進入歷史；提交前需決定是否以佔位符取代 |

全部 10 次皆為 **HS256 簽金鑰**（`JWT_SECRET_KEY`／compose `JWT_ALGORITHM: HS256`），非已簽發的 bearer token，亦非測試 fixture token。

相鄰的第二類硬編碼 DB 憑證（同一輪盤點中一併發現，第 631 行未涵蓋）：`postgresql://test:test@localhost:5434/alethra_acceptance` 出現在 `backend/tests/test_quota_concurrency.py`、`execute_journey_b_worker.sh:22`、`verify_data_persistence.sh:91`、`verify_quota_persistence.sh:122`、`NORMAL_RUNTIME_EVIDENCE.md:42` 共 5 個檔案。目前 `lsof -nP -iTCP:5434 -sTCP:LISTEN` 無監聽（`LSOF_EXIT=1`、0 列），即該 acceptance DB 未運行，因此這些字面值目前無可用的連線目標；「是否仍有非本機實例使用」維持 **UNKNOWN**。

### 8. 本節未驗證／未執行

1. 未 commit、未 push、未輪換任何憑證、未建立或刪除任何資料庫、未修改 `pg_hba.conf`、未重啟服務。
2. Docker 卷冊內容未核對（daemon 未運行），因此「DB A 在該卷冊中仍完整」為 **NOT VERIFIED**。
3. OID `16419` 對應哪個 DB 名稱：NOT VERIFIED（需一次通過驗證的唯讀連線）。
4. 證據檔案為本機暫存（`/tmp`），非 repo 產物：`sg_baseline.txt` `558f950b36999272`、`sg_withpw.txt` `f756dd2fc6548482`、`secret_delta.txt` `1b3dbf9f94742b4c`、`shape.txt` `39c9abbc8674bb29`、`pg_readonly.out` `f3e47efd3bb3a74a`、`cred_scope.txt` `4e99c9e6e56e809e`、`secinv_worktree.txt` `b42eb5029098460d`、`kraken_final.txt` `86f18603a0784b4b`、`guard_final.txt` `b5778aab8b262607`、`failed_two.txt` `5e5fbdb2cf567a6e`、`live_harvest_probe.py` `e14d2861c51bc600`（皆為 sha256 前 16 字元）。

## `backend/.env.test` 五欄揭露與長期根因的三個產物（2026-11-16，本節為追加，不改寫上方原始紀錄）

本节只輸出**鍵名、長度、雜湊與形態標籤**。不含秘密值、不含匹配片段、不含含秘密的 diff。上一節 §4 已撤回「`.env.test` 外洩真實密碼」之宣稱，該撤回仍然有效，本節不重複該錯誤。

### 1. 五欄揭露

**(a) 受影響鍵名（實測：該檔 29 行、13 個指派，行號 1–29）**

`DATABASE_URL`、`LIVE_TRADING_ENABLED`、`RESEARCH_AUTH_BYPASS_ENABLED`、`ENABLE_LEARNING_SCHEDULER`、`AUTO_COLLECTION_ENABLED`、`DATA_PROVIDER`、`TEST_MODE`、`TEST_ENVIRONMENT`、`KRAKEN_WS_URL`、`KRAKEN_API_KEY`、`KRAKEN_API_SECRET`、`TEST_USER_EMAIL`、`TEST_ADMIN_EMAIL`。

其中 **3 個鍵值為空**（實測形態 `^[A-Z_]+=$`）：`KRAKEN_WS_URL`、`KRAKEN_API_KEY`、`KRAKEN_API_SECRET` ⇒ 該檔**不含任何交易所憑證**，因此不屬於「broker API key 外洩」這一類。`DATABASE_URL` 的密碼段為 dotenv 佔位引用（見上方 §2），非字面值。

**(b) 是否曾進 Git 歷史（實測：`git log --format=%h -- backend/.env.test` → 恰好 2 個提交）**

以本節第 2 小節的同一個分類器逐提交重跑，只印出 `(key, kind, value_length)`：

| 提交 | 該檔指派數 | 分類器結果 |
|---|---|---|
| `5af8521` "Add isolated test environment with fail-closed safety gate" | 13 | `[('DATABASE_URL', 'literal_url_password', 68)]` |
| `77e84ca` "Complete vertical path: safety gate enforced + privilege separation proven" | 13 | `NONE` |

⇒ **字面值只進過歷史一次**，且在下一次提交即被替換為 `${ALETHRA_TEST_RUNTIME_PASSWORD}` 引用；目前 HEAD 与工作樹皆乾净。
該字面值不是一個私有秘密：它與**公開文件層級的出廠預設口令**逐位元組相同。判定方式寫成可重跑的比較，且**不輸出秘密值本身**：

```python
pw = <5af8521 blob 的 DATABASE_URL userinfo 密碼段>          # 只留在變數內
candidate = "pos" + "gres"                                    # 拼接，避免值進入指令歷史
sha256(pw) == sha256(candidate)  →  EQUAL: True
```

以上只有一個輸出：`EQUAL: True`。原紀錄在此另印 `sha256[:10] = a942b37ccf` 與 `len = 8`；依 2026-11-16 owner 指示「不再輸出秘密 skeleton 或低熵值的可猜測指紋」，本檔已移除——對一個字典字詞級的值，摘要指紋與印出值本身在攻擊者端是同一件事（猜一次、比對即可驗證），所以指紋並未比值多一層保護，只是多一條可被反向確認的線索。上面那行拼接式仍是本節的揭露事實來源，移除指紋不縮小它的曝露面。

同一比較另以兩條獨立解析路徑各跑一次（掃描器的 `_url_password()` 與手寫 `split("//"/"@"/":")`），兩條路徑皆得 `EQUAL: True`。⇒ 任何讀過本 repo 或讀過 PostgreSQL 安裝文件的人本來就會嘗試它；它的曝露面不取決於 `.env.test` 這一檔。本節不重複該字串。

**(c) 是否為有效或共用秘密 —— 判定：`UNKNOWN`（不猜測有效，也不猜測無害）**

- 「該字面值在**任何目前存在的實例**上仍能連線」：`UNKNOWN`。要把它降為「無效」需做一次對未批准 DB 的連線嘗試，或在已批准 lane 之外簽名呼叫 — 兩者皆被 §五 禁止（「不碰未批准的舊 DB／身份」「不做付費外呼」）。
- 可用的**正向**間接證據只有一條，且不自動推出結論：本機 `127.0.0.1:5432` cluster 的 log（上一節 §5 已記錄）**從未**成功以該字面值連線，且 `2026-11-10 10:56:38` 出現 `database "ai_investment_lab_test" does not exist`。那證明該 DB 名稱在此 cluster 不存在，不證明這個字面值在他處无效。
- 「是否為**共用**秘密（同一字串跨多個 role／主機重複使用）」：`UNKNOWN`。本專案既有的全庫掃描只能指出「同一公開預設字串在 repo 內以 N 個檔案出現」，無從判斷實際部署是否沿用。
- 「`alethra_test` 這個 role 的密碼是否已輪換過」：上一節已實測該 cluster 回報 `password authentication failed` ＋ `pg_hba.conf` line 91/93 相符、**無** role-absent detail ⇒ 該公開預設字面值**已不再匹配**此 cluster 中的 `alethra_test`，這是「某處已輪換」的差分證據，但輪換時間與覆蓋範圍 `UNKNOWN`。

**(d) 哪些身份／服務可能受影響（實測讀取路徑）**

| 事實 | 實測方式與行號 |
|---|---|
| **無任何程式載入 `backend/.env.test`** | `app/config.py:428` 為 `model_config = ConfigDict(env_file=".env", …)` — 只有 `.env`，無 `.env.test`；全庫對字串 `env.test` 的引用經逐一核對，落在**本節三個新產物**、`.gitignore` 註解與**四份文件**（`EXECUTION_RECORD`、`docs/handoff/TRAVEL_HANDOFF.md:316`、`ISOLATION_TEST_ENVIRONMENT_REPORT.md:191`、`docs/handoffs/2026-11-14-desktop-checkpoint.md:1049-1059`），無一為載入點 |
| `backend/scripts/run_tests.sh` 自行組環境，不 source 該檔 | `:99` `export JWT_SECRET_KEY`（執行期產生）、`:126-131` 與 `:204-205` 導出 `DATABASE_URL`／`ALETHRA_TEST_DB` 等，全部來自 `ALETHRA_PG_TEST_URL`／`TMPDIR`，非 dotenv |
| 該檔 13 個鍵**各自的真實消費者的確存在**（但在別處注入） | 旗標讀取點：`app/config.py`、`api/admin.py`、`services/auto_collection.py`、`services/kraken_websocket.py`、`tests/test_research_run_*`；DB 目標由 `check_db_env.analyse` 前置檢查把關（`run_tests.sh:111-112`、`:160-163`） |

⇒ 因此受影響面是**「repo 讀者可見的歷史字串」＋「未來有人把真實值寫進這個被追蹤的檔案」**，不是「執行期有一條載入洩漏路徑」。第二項才是可預防的風險，也是第 2 小節三個產物的用途。

**(e) 建議補救與確切副作用（分項，全部未執行）**

| # | 動作 | 確切副作用 | 狀態 |
|---|---|---|---|
| 1 | 本節三個產物入版控：`git add backend/.env.test.example backend/scripts/check_env_hygiene.py backend/tests/test_env_hygiene_contract.py .gitignore` | 新增 4 條路徑；`.gitignore` 改 1 處；不改任何既有檔案內容；不影響執行期（無程式讀這些檔） | **需批准**（git 寫入） |
| 2 | `git rm --cached backend/.env.test` | **停止追蹤未來的複製**；保留工作檔不刪（故本機測試環境不受影響）；**不移除 `5af8521` 的 blob**，該物件仍在歷史中；此提交會使「`backend/.env.test` 從 HEAD 消失」成為公開 diff 的一行 | **需批准** |
| 3 | 歷史重寫（`filter-repo` ＋ force-push）以移除 `5af8521` 的 blob | 改寫**共享歷史**、使他人 clone 全部失效、需協調 force-push；且該字面值本身是公開預設，清除所得的保護近乎為零 | **不建議；明確未批准項目**（§五：不 force-push） |
| 4 | 若 owner 判定 `alethra_test` 或其他 role 需輪換 | 所有以舊值連線的腳本／文件引用同時失效，需逐檔改注入；需一次對該 cluster 的寫入授權 | **需批准，且與 1–2 分開批准** |
| 5 | 相鄰既存問題（本輪**未**修）：`docker-compose.yml:28` 的 `JWT_SECRET_KEY` 值是一段「change-me」指示文字，即**部署時會以預設簽金鑰上線**（fail-open） | 屬部署金鑰治理，非本節範圍；已列入 §三(5) 服務段，避免它被當成已修 | 僅記錄 |

### 2. 長期根因的三個產物（依 §四「以測試設定模板、忽略規則及 fail-closed 輸入契約處理」）

1. **模板**：`backend/.env.test.example`（44 行、13 鍵同序）。唯一與實體檔的差異是**故意**的 — `:22` 使用
   `postgresql://alethra_test_runtime:${ALETHRA_TEST_RUNTIME_PASSWORD:?set ALETHRA_TEST_RUNTIME_PASSWORD before sourcing}@localhost:5432/ai_investment_lab_test`，
   即帶 `:?` 的 fail-closed 注入；`backend/.env.test` 目前用的是**裸 `${VAR}` 形式**，未設定時展開為空字串 — 那正是上一節 §2 兩項失敗的直接原因。檔首註解明言：無任何 app module、test 或 script source 這兩個檔，實際設環境的是 `scripts/run_tests.sh`。
2. **忽略規則**：`.gitignore:17-22`（5 行註解＋`:22` 規則）。實測 `git check-ignore -v --no-index backend/.env.test` → `.gitignore:22:.env.test`；模板本身 rc=1（必須保持可提交）。
   規則在路徑仍被追蹤時是 **inert** 的，這句話寫在註解裡，並由 `test_tracked_state_is_reported_as_measured_not_assumed`（`backend/tests/test_env_hygiene_contract.py:310`）依**實際** `git ls-files` 結果分支，因此「已忽略」永遠無法只憑這行被宣稱。
3. **fail-closed 輸入契約**：`backend/scripts/check_env_hygiene.py`（397 行）＋ 22 項契約測試 `backend/tests/test_env_hygiene_contract.py`（351 行）。三條規則各有明確作用域：
   * **provider-token 形態**（Stripe `sk_live`、AWS `AKIA`、PEM header、HS256 JWT…）在任何文字檔都算，不需金鑰名；
   * `credential_literal`：金鑰名符合 `SECRET_KEY_SHAPE` **且**值不是注入（`${VAR}`／`$VAR`／`$(…)`）、不是純數字、不是指示型佔位語（`your-*`／`change-me…`／`<…>`／`xxx`）；
   * `literal_url_password`：URL userinfo 有字面密碼段。此規則**先於**金鑰名判定，否則原缺陷形態（`DATABASE_URL=…:pass@…`，金鑰名並不含 PASSWORD）會看不見 — 由 `:83 test_the_original_defect_shape_is_refused` 釘住。
   `urlsplit` 会在 `${VAR:?message}` 的 `?` 處切斷 authority，因此 `_authority()` 以括號深度**手動解析**（`:137 test_the_url_parse_follows_the_spec_it_needs_to` 涵蓋 `:?message`、`-x` 預設、以及 RFC 3986 邊界：未編碼 `/` 的 userinfo 不可尋、編碼 `%2F` 必須被尋到）。
   輸出面**只有** `path:line: key -> kind (len=N)`；`:188 test_a_finding_never_carries_the_value_it_found` 斷言值不出現在 finding 與 verdict 字串中。

### 3. 此 gate 能否失敗：5 次變異實測（每次立即還原）

退出碼紀律同 §收斂補充 1（先導向檔案、緊接取 `$?`）。

| # | 變異 | 結果（原樣，不簡化） | 還原後 |
|---|---|---|---|
| M1 | 把測試用的合成密碼標記貼進 `backend/.env.test` 的 `DATABASE_URL` | `REFUSED — NEW literals not in the ledger: backend/.env.test:DATABASE_URL(literal_url_password)`；`MUTATED_CLI_EXIT=2`；`5 failed, 17 passed` | `grep -c` 標記 = 0；`CLI_EXIT=0`；22 passed |
| M2 | 刪除 `.gitignore:22` 規則本身（保留註解） | `1 failed, 21 deselected`，失敗點 `test_env_hygiene_contract.py:304`（`test_gitignore_keeps_a_filled_in_copy_out_of_the_index` 的 `ignored.returncode == 0`）`AssertionError` | `cmp` 與備份相同 rc=0；**22 passed in 5.01s** |
| M3 | 把模板 `:?set …` 改回裸 `${ALETHRA_TEST_RUNTIME_PASSWORD}` | 第一次跑 `-k "fail_closed or mirrors_the_tracked"` → `1 failed, 1 passed, 20 deselected`：**通過的那一項正是 `test_template_mirrors_the_tracked_test_settings_file`** ⇒ fail-closed 測試獨立成立，不是靠金鑰同名順帶擋下。重跑（行號修正後）`-k fail_closed` → `1 failed, 21 deselected`，失敗點 `:291`，斷言訊息即被檢出的形態 `AssertionError: ${ALETHRA_TEST_RUNTIME_PASSWORD}` | 檔內 `:?set ALETHRA` 計數回到 1，`cmp` rc=0；**22 passed** |
| M4 | 自 `TRACKED_DEBT`（`:329-334`）刪除 `docker-compose.acceptance.yml / JWT_SECRET_KEY` 一條（树面未動） | `M4_CLI_EXIT=2` ＋ `REFUSED — NEW literals not in the ledger: docker-compose.acceptance.yml:JWT_SECRET_KEY(credential_literal)`；測試端 `2 failed, 20 passed`：`:216 assert 5 == 4`（可加性）、`:222` REFUSED 訊息 | `CLI_EXIT=0`；`matches the ledger: 5 tracked literal(s), 5 expected, no new credential`；**22 passed** |
| M5 | 把 `SETTINGS_SUFFIXES`（`:115`）擴充含 `.py`（作用域過窄的反向） | `M5_CLI_EXIT=2`、`137 finding(s) across 830 tracked text files (503 configuration files in assignment scope): credential_literal=128, literal_url_password=9`；`M5_TEST_EXIT=1`、`4 failed, 18 passed`：`:166 assert not True`、`:216 assert 137 == 5`、`:222` REFUSED、`:229` 檔名集合 ≠ 兩個 compose 檔 | `cmp` 相同 rc=0；`SETTINGS_SUFFIXES` 回到 7 個副檔名；`CLI_EXIT=0`，5 findings |

變異方向對應到三個失敗面：**新增字面值**（M1、M4）、**帳本過期**（M4 的反向由 `:237 test_the_ledger_refuses_a_new_literal_and_a_stale_entry` 以顯式 `debt=` 參數直接斷言 `resolved` 欄，無需改動工作樹）、**作用域過窄／過寬**（M2、M3、M5）。
另修一個自己留下的錯誤：該測試的斷言訊息原寫「the real ledger still expects six」— 帳本在把 `change-me` 改判為佔位後已從 6 條降為 5 條，訊息屬手抄過期值。現改為由 `TRACKED_DEBT` 求和 derive（`:247-250`），與本專案「不手抄衍生值」的紀律一致。

### 4. 計數的誠實分解（不同規則，不是同一數字在縮小）

| 數字 | 規則 | 是否同一件事 |
|---|---|---|
| **5** | 本節規則對**整個追蹤樹**的判定：`credential_literal=3` + `literal_url_password=2`，全在 `docker-compose.yml:8,27` 與 `docker-compose.acceptance.yml:10,29,30` | 是本節的帳本 |
| 137 | 同一規則但把 `.py` 納入作用域（M5） | **不是** — 128 條為 `password_hash=get_password_hash(...)`、`allow_credentials = True`、`api_key=<pydantic 欄位預設>` 這一類**不是環境設定**的指派 |
| 168 / 27 | 本輪掃描器的兩次早期草稿與一次中間草稿 | **不是** — 規則尚未收斂前，無參考價值，不引為結論 |
| 361 | 上一節 §4 的既存狀態計數（HEAD 中所有 credential 形態字面值） | 比較大的規則；本節**未**宣稱它下降，也未宣稱它與 5 有關 |

「5」的對象是「此契約目前擋住的、屬於**部署設定檔**的字面憑證」；「361」的對象是「repo 內一切形態像秘密的字串」。兩者不可相加、不可互相取代。

### 5. 本節未驗證／未執行

1. **未 commit、未 push。** 本節只声明可穩定的部分，不抄一個會被平行 session 移動的總數：`backend/.env.test` **位元未動**（`git diff --quiet -- backend/.env.test` → rc=0，`git diff --stat` 無輸出），本節三個產物 `backend/.env.test.example`、`backend/scripts/check_env_hygiene.py`、`backend/tests/test_env_hygiene_contract.py` 在 `git status --porcelain` 中為 `??`（未追蹤），`.gitignore` 與本紀錄為 `M`。故第 1 小節 (e) 的動作 1、2 全部待批准。
2. **未輪換任何密碼、未連線任何 DB、未啟動任何服務、未修改 `pg_hba.conf`、未刪任何 PGDATA**。M1–M5 全部對**本輪自建的本機檔案**操作並以 `/tmp/{envtest,gitignore,template,scanner}.orig` 還原，`cmp` 逐檔驗證 rc=0。
3. `UNKNOWN`（本節不假裝已解）：(i) 任何**運轉中**實例是否使用該公開預設字串；(ii) 它是否跨 role／主機共用；(iii) `alethra_test` 輪換的時點與覆蓋範圍；(iv) Docker 卷冊內 DB A 是否仍完整（daemon 未運行）。
4. 本節測試結果之原始輸出（本機暫存，非 repo 產物）：`m2.txt`、`m2r.txt`、`m2b.txt`、`m3.txt`、`m3r.txt`、`m3b.txt`、`m4cli.txt`、`m4t.txt`、`m4r.txt`、`m4r2.txt`、`m5cli.txt`、`m5t.txt`、`m5r.txt`、`final_hygiene.txt`、`final2.txt`（皆在 `/tmp`）。`b` 兩檔是 M2／M3 在修正斷言訊息後的重跑，目的在取得**目前**的失敗行號（`:304`、`:291`）而非推算位移。合跑實測：`test_env_hygiene_contract.py + test_db_env_contract.py + test_env_entries.py` → `52 passed in 17.25s`，lane 指紋 `python 3.12.16 | pytest 8.3.3 | sqlalchemy 2.0.36`。
5. 本節尚未做的兩件事（不藏）：(i) 掃描器**尚未**接進 `run_tests.sh` 或任何 CI 入口 — 目前只由那 22 項契約測試在 pytest lane 內執行，因此「提交前自動擋下」還不成立，需在 §三(8) 批准時一併決定接入點；(ii) 未對 `HEAD` 的 361 條廣口徑字串做任何處置，也未宣稱本節使它下降。
