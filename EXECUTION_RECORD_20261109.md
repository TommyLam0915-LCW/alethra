# 真實歷史資料正式API閉環執行紀錄 — 2026-11-09

**分支**: live1-impl  
**HEAD**: 38c9750a09e3ff5bdfda1970c4dbc8335515d7c6  
**工作目錄**: /Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto  
**執行時間**: 2026-11-09 16:54-17:00 HKT (08:54-09:00 UTC)  
**執行者**: 直接會話（Qoder desktop）

---

## 一、環境與 Migration

### PostgreSQL 隔離測試 DB

- **Host**: localhost
- **Port**: 5432
- **Database**: alethra_test_isolated_20261109
- **User**: alethra_test
- **Password**: test_isolated_pwd_20261109（測試用，非生產）
- **性質**: 全新、可棄式、本機測試 DB
- **非**: production 或原研究 DB

### Driver 與連線

- **原始問題**: `ModuleNotFoundError: No module named 'psycopg'`
- **requirements.txt 指定**: `psycopg2-binary==2.9.10`（編譯失敗）
- **解決方案**: 安裝 `psycopg[binary]` 3.3.8（psycopg 版本 3）
- **SQLAlchemy URL**: `postgresql+psycopg://alethra_test:test_isolated_pwd_20261109@localhost:5432/alethra_test_isolated_20261109`
- **Driver 選擇**: psycopg (v3)，非 psycopg2

### Alembic Migration

- **命令**: `DATABASE_URL="..." python3 -m alembic upgrade head`
- **結果**: ✓ 成功（54 revisions 全部套用）
- **Current**: c9d0e1f2a3b4
- **Head**: c9d0e1f2a3b4
- **一致**: ✓

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

## 二、真實資料來源與限制

### 搜索範圍

1. **alethra-customer-evidence repo** (/Users/tommylam0915/Documents/Qoder/2026-10-31/e2935e5f)
   - 搜索: `live2b_warehouse_bars_1440m.json`
   - 結果: **不存在**
   - 相關檔案: `mb6m_after_deploy_state.json`, `mb8_research_lane.json`
   - 內容: CRYPTO:KRAKEN:BTC-USD **配置元數據**（intervals, strategies）
   - **無實際 OHLCV bar 資料**

2. **alethra-integration-auto repo** (/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto)
   - 搜索: `*warehouse*`, `*live2b*`, `*.json` with OHLCV structure
   - 結果: **不存在**
   - `.local/evidence/` 目錄: **不存在**

3. **Git 歷史**
   - 搜索: `*warehouse*`, `*live2b*`
   - 結果: **無相關 commit**

### 結論

- **真實歷史資料**: **尚未 harvest 或未保存**
- **live2b_warehouse_bars_1440m.json**: **不存在**
- **可用資料**: 僅 synthetic demo data（DEMO-ALPHA, DEMO-BETA, DEMO-BENCHMARK）
- **限制**: 本輪無法使用真實資料，改以 demo 資料驗證 API 閉環

### 資料來源盤點

| 來源 | 性質 | 狀態 | 限制 |
|------|------|------|------|
| Demo data (DEMO-*) | Synthetic | ✓ 可用 | 非真實市場資料 |
| Real synthetic (US:AAPL 等) | Synthetic | ✓ 可用 | 非真實市場資料 |
| Live crypto (Kraken BTC-USD) | Live | 未測試 | 需網路 + 預算閘門 |
| 真實歷史資料 | Real | **不存在** | 需 harvest 或外部來源 |

---

## 三、API 閉環

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

## 四、帳本與 Metrics

### 績效指標

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

### PnL 對帳

```json
{
  "consistent": true,
  "difference": 0.0,
  "equity_net_pnl": 8564.845,
  "realized_net_pnl": 8564.845
}
```

✓ 權益淨損益 = 實現淨損益，無差異

### Round Trip Summary

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

### 空交易與未定義指標

- ✓ 空交易: 無（2 筆交易）
- ✓ 未定義指標: 正確回傳 `null` + `reason`（非偽裝零值）
  - `annualized_return_pct`: null（少於 252 天）
  - `average_realized_r`: null（無 stop 定義）
  - `annualized_volatility_pct`: null（少於 252 天）

---

## 五、紙上交易與風控

**狀態**: **PARTIAL — 未測試**

### 原因

1. 本輪目標為「真實歷史資料 API 閉環」，但真實資料不存在
2. 改以 demo 資料完成 API 閉環驗證
3. 紙上交易（paper trading）與風控（risk control）為獨立功能，需額外測試

### 缺口

- 未測試: `POST /api/v1/crypto-paper/portfolios/{id}/orders`
- 未測試: 重複事件不重複成交
- 未測試: 風控拒絕無交易副作用
- 未測試: 中斷後恢復可對帳

### 建議

紙上交易與風控應作為獨立測試輪次，使用：
1. Live crypto paper trading endpoint（需 Kraken 連線）
2. 或歷史重播模擬（需 paper executor 支援離線重播）

---

## 六、GitHub 保存狀態

### 本機狀態

```
分支: live1-impl
HEAD: 38c9750a09e3ff5bdfda1970c4dbc8335515d7c6
Remote: origin/live1-impl

Untracked files:
  backend/test-final-verify.db-journal
  backend/venv312/
  verify_inference_usage.py
```

### 本次修改

- **無**: 本輪未修改任何程式碼
- **新增**: `EXECUTION_RECORD_20261109.md`（本紀錄，未提交）

### 遠端驗證

- **未推送**: 本輪無新 commit
- **遠端 HEAD**: `38c9750a`（與本機一致）
- **驗證命令**: `git ls-remote origin refs/heads/live1-impl`（未執行，因無新 commit）

---

## 七、剩餘 BLOCKERS

### P0 — 已解決

1. ✓ **PostgreSQL driver**: 安裝 psycopg 3.3.8
2. ✓ **Database migration**: 54 revisions 全部成功
3. ✓ **API server**: 啟動正常，HTTP 流程完整

### P1 — 本輪未處理

4. **真實歷史資料**: **不存在**
   - `live2b_warehouse_bars_1440m.json` 未找到
   - 需決定：harvest 新資料 / 使用外部來源 / 繼續使用 synthetic
   - **BLOCKER**: 無法驗證真實資料閉環

5. **紙上交易與風控**: **未測試**
   - 需獨立測試輪次
   - 需決定：使用 live crypto / 歷史重播 / 兩者皆測

6. **前端 UI**: **未測試**
   - 需 `npm run dev` + 瀏覽器測試
   - 需決定：是否納入本輪範圍

### P2 — 已知限制

7. **帳本欄位數不一致**: 文檔宣稱 16 欄，實際 17 欄
   - 需更新 README.md 及相關文檔

8. **依賴版本未精確記錄**: `pip install -q` 未輸出版本
   - 建議：使用 `pip freeze > requirements-actual.txt` 記錄

9. **Live crypto 未測試**: 需網路 + 預算閘門
   - 需決定：是否在本輪測試

---

## 結論

### 達成項目

1. ✓ **ENGINE_SMOKE_PASS**: 引擎層三策略執行通過
2. ✓ **FULL_STACK_PASS**: API 層完整流程通過（HTTP → DB → Engine → HTTP）
3. ✓ **PostgreSQL Migration**: 54 revisions 成功套用
4. ✓ **API 閉環**: 註冊 → 登入 → 實驗 → 回測 → 結果讀取
5. ✓ **Metrics 驗證**: PnL 對帳一致，無偽裝零值
6. ✓ **帳本完整性**: 17 欄完整，交易明細可追溯

### 未達成項目

1. ✗ **REAL_DATA_NOT_YET_VALIDATED**: 真實歷史資料不存在，無法驗證
2. ✗ **紙上交易與風控**: 未測試（獨立功能）
3. ✗ **前端 UI**: 未測試

### 狀態標籤

- **ENGINE_SMOKE_PASS**: ✓
- **FULL_STACK_PASS**: ✓（demo 資料）
- **REAL_DATA_NOT_YET_VALIDATED**: ✗（資料不存在）
- **PAPER_TRADING_NOT_TESTED**: ✗（未納入本輪）

### 誠實限制

- 本輪使用 **synthetic demo data**，非真實市場資料
- 策略盈利（+8.56%）**不代表可實盤**
- 真實歷史資料 **不存在**，需另行 harvest 或取得
- 紙上交易與風控 **未測試**，需獨立輪次
- 前端 UI **未測試**

### 下一步建議

1. **決定真實資料來源**: Harvest / 外部 provider / 繼續 synthetic
2. **獨立測試紙上交易**: 使用 live crypto 或歷史重播
3. **更新文檔**: 修正帳本欄位數（16 → 17）
4. **記錄依賴版本**: `pip freeze > requirements-actual.txt`

---

**附件**:
- API 回應原始 JSON: 本會話終端機輸出
- Migration log: `/tmp/alembic-migration.log`（54 revisions）
- API server log: `/tmp/alethra-api-pg.log`
- PostgreSQL 配置: `pg_hba.conf` 已還原（scram-sha-256）

**命令紀錄**:
```bash
# PostgreSQL 啟動
brew services start postgresql@15

# 測試 DB 建立
psql -h localhost -U postgres -d postgres << 'SQL'
CREATE USER alethra_test WITH PASSWORD 'test_isolated_pwd_20261109';
CREATE DATABASE alethra_test_isolated_20261109 OWNER alethra_test;
SQL

# Migration
DATABASE_URL="postgresql+psycopg://alethra_test:test_isolated_pwd_20261109@localhost:5432/alethra_test_isolated_20261109" \
  python3 -m alembic upgrade head

# API 啟動
PYTHONPATH=/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend \
DATABASE_URL="postgresql+psycopg://alethra_test:test_isolated_pwd_20261109@localhost:5432/alethra_test_isolated_20261109" \
  python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8100

# API 測試
curl -X POST http://127.0.0.1:8100/api/auth/register ...
curl -X POST http://127.0.0.1:8100/api/auth/login ...
curl -X POST http://127.0.0.1:8100/api/v1/experiments/ ...
curl -X POST http://127.0.0.1:8100/api/v1/versions/{id}/runs ...
curl http://127.0.0.1:8100/api/v1/experiments/{id}/runs ...
```

**Exit codes**: 全部 0（成功）

**UTC 時間**: 2026-11-09 08:54-09:00
