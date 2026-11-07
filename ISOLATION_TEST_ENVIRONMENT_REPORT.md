# 隔離測試環境完成報告

**日期：** 2026-11-07  
**最新提交：** `376380e` → `live1-impl`  
**狀態：** 安全閘門與 migration 修正完成，全面接入與驗收重新執行待完成

---

## 一、已完成項目

### 1. 安全閘門強化 ✓

**檔案：** `backend/test_safety_gate.py`

**修正：**
- `verify_test_database()` 強制檢查 `TEST_MODE=true`
- 不在白名單 → **拒絕**（非 warning）
- Superuser/elevated permissions → **拒絕**（非 warning）
- Host/port 必須匹配 `ALLOWED_INSTANCES`
- 密碼永不回傳或記錄
- 連線後驗證實際資料庫身分

**白名單：**
```python
ALLOWED_TEST_DATABASES = {
    'ai_investment_lab_test',
    'alethra_test',
    'test_ai_investment_lab',
    'alethra_owner_test',
}
```

### 2. 負向測試 ✓

**檔案：** `backend/tests/test_safety_gate_negative.py`

**結果：** 13 個測試全部通過
```
test_rejects_missing_test_mode ✓
test_rejects_wrong_test_mode ✓
test_rejects_missing_database_url ✓
test_rejects_production_database ✓
test_rejects_unknown_database ✓
test_rejects_wrong_host ✓
test_rejects_wrong_port ✓
test_rejects_superuser ✓
test_rejects_createdb_permission ✓
test_rejects_createrole_permission ✓
test_rejects_bypassrls_permission ✓
test_rejects_database_mismatch ✓
test_does_not_return_password ✓
```

**證明：** 安全閘門真正拒絕不合規配置，非僅警告。

### 3. Migration 007 ✓

**檔案：** `backend/migrations/versions/007_drop_old_source_constraints.py`

**修正：**
- 刪除 migration 005 建立的舊全表唯一約束：
  - `uq_decision_records_source_trade_type`
  - `uq_paper_positions_source_trade`
- 保留 migration 006 的新 portfolio 範圍約束：
  - `uq_decision_records_source_per_portfolio`
  - `uq_paper_positions_source_per_portfolio`

**驗證：**
```
Migration 006 後：4 個約束（2 舊 + 2 新）
Migration 007 後：2 個約束（僅 portfolio 範圍）✓
```

### 4. 有限權限測試角色 ✓

**角色名稱：** `alethra_test_runtime`

**權限：**
```
rolsuper = f         (非 superuser)
rolcreatedb = f      (不能建立資料庫)
rolcreaterole = f    (不能建立角色)
rolbypassrls = f     (不能繞過 RLS)
rolcanlogin = t      (可以登入)
```

**真實連線證據：**
```bash
$ TEST_MODE=true \
  DATABASE_URL=postgresql://alethra_test_runtime:***@localhost:5432/ai_investment_lab_test \
  python test_safety_gate.py

✓ Safety gate PASSED — all checks succeeded
  Database: ai_investment_lab_test
  User: alethra_test_runtime
  Server: ::1:5432
```

### 5. 隔離測試資料庫 ✓

**資料庫：** `ai_investment_lab_test`

**狀態：**
- 全新建立（drop + create）
- 遷移至 `drop_old_source_constraints`
- 無生產資料
- 可安全丟棄

---

## 二、未完成項目

### 1. 全面強制接入 ✗

**現況：** 安全閘門函式已強化，但未接入所有寫入入口

**需要接入：**
- [ ] pytest conftest.py（已有部分檢查，但未使用 test_safety_gate）
- [ ] debug/reconciliation scripts
- [ ] migration runner
- [ ] 其他會寫 DB 的測試工具

**風險：** 目前規則要求「所有測試必須呼叫」，但技術上未強制執行

### 2. 統一連線工廠 ✗

**現況：** conftest.py 與 app 使用不同的 DATABASE_URL 解析

**需要：**
- 建立統一的 `get_test_connection()` 工廠
- 所有入口使用同一份經驗證的連線設定
- 不回退到共用 .env 的主資料庫

### 3. 真實入口負向測試 ✗

**現況：** 13 個單元測試使用 mock

**需要：**
- 直接執行真實測試入口（pytest、debug scripts）
- 給予錯誤配置（主資料庫、未知資料庫、錯端點、superuser、缺少 TEST_MODE）
- 證明在任何 DDL/DML 前停止

### 4. Migration 完整驗證 ✗

**現況：** 已驗證 007 刪除舊約束

**需要：**
- 驗證兩條路徑：
  1. 空白 DB → migration head
  2. 含 005/006 舊約束的 DB → 007
- 實際查詢 `pg_constraint` 確認
- 測試同 portfolio 重複拒絕、不同 portfolio 允許
- 記錄 downgrade 限制

### 5. 重新執行驗收 #137-144 ✗

**現況：** 未執行

**需要：**
在隔離測試資料庫重新執行：
- [ ] #137: 正式登入/API 鏈路
- [ ] #138: 真正並行測試
- [ ] #139: 失敗回滾驗證
- [ ] #140: 帳本收斂（五筆精度）
- [ ] #141: 獨立對帳
- [ ] #142: 跨用戶拒絕與停止契約
- [ ] #143: Migration 驗證
- [ ] #144: 交付證據

---

## 三、生產資料狀態

**未執行任何清理或處置**

| 分類 | 數量 | 狀態 | 處置 |
|------|------|------|------|
| Category A (owner historical) | 12 runs | 保留 | 不修改 |
| Category B (owner live_trading) | 25 runs | 保留 | 待 owner 決定 |
| Category C (test agent) | 7 runs | 保留 | 清理候選，待批准 |
| Debug users | 9 users | 保留 | 待精確歸因 |

**完整 manifest：** 見 `PRODUCTION_IMPACT_MANIFEST.md`

---

## 四、環境配置

### 測試設定

**檔案：** `backend/.env.test`
```
DATABASE_URL=postgresql://alethra_test_runtime:***@localhost:5432/ai_investment_lab_test
LIVE_TRADING_ENABLED=false
TEST_MODE=true
ENABLE_LEARNING_SCHEDULER=false
AUTO_COLLECTION_ENABLED=false
```

**注意：** 
- 未修改共用 `.env`
- 密碼透過環境變數注入，不寫入版本庫

### 測試角色

**Runtime 角色：** `alethra_test_runtime`
- 密碼：透過環境變數 `ALETHRA_TEST_RUNTIME_PASSWORD` 注入
- 權限：最小 DML（SELECT, INSERT, UPDATE, DELETE）
- 限制：非 superuser，無 DDL 權限

**Migration 角色：** `postgres`（僅用於 migration 執行）
- 需要 DDL 權限建立/修改 schema
- 不應在 runtime 使用
- 密碼：不記錄，按本地安全管理

---

## 五、安全證據

### 1. 安全閘門拒絕證據

**測試命令：**
```bash
# 拒絕生產資料庫
TEST_MODE=true DATABASE_URL=postgresql://postgres:***@localhost:5432/ai_investment_lab \
  python test_safety_gate.py
# 結果：TestDatabaseGateError: FORBIDDEN: Database 'ai_investment_lab' is in the production blacklist

# 拒絕未知資料庫
TEST_MODE=true DATABASE_URL=postgresql://postgres:***@localhost:5432/unknown_db \
  python test_safety_gate.py
# 結果：TestDatabaseGateError: REJECTED: Database 'unknown_db' is not in the test whitelist

# 拒絕缺少 TEST_MODE
DATABASE_URL=postgresql://alethra_test_runtime:***@localhost:5432/ai_investment_lab_test \
  python test_safety_gate.py
# 結果：TestDatabaseGateError: TEST_MODE must be 'true' for test operations
```

### 2. 真實連線通過證據

**測試命令：**
```bash
TEST_MODE=true \
DATABASE_URL=postgresql://alethra_test_runtime:***@localhost:5432/ai_investment_lab_test \
python test_safety_gate.py
```

**輸出：**
```
✓ Safety gate PASSED — all checks succeeded
  Database: ai_investment_lab_test
  User: alethra_test_runtime
  Server: ::1:5432
```

### 3. 角色權限證據

**查詢：**
```sql
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolcanlogin
FROM pg_roles
WHERE rolname = 'alethra_test_runtime';
```

**結果：**
```
alethra_test_runtime | f | f | f | f | t
```

---

## 六、下一步

### 優先順序

1. **全面強制接入**（估計 2-3 小時）
   - 接入 pytest conftest
   - 接入 debug/reconciliation scripts
   - 建立統一連線工廠

2. **真實入口負向測試**（估計 1 小時）
   - 執行 pytest 給錯誤配置
   - 執行 debug scripts 給錯誤配置
   - 證明全部在寫入前停止

3. **Migration 完整驗證**（估計 1 小時）
   - 兩條路徑測試
   - Schema 查詢驗證
   - 行為測試（同 portfolio 拒絕、不同允許）

4. **重新執行 #137-144**（估計 4-6 小時）
   - 在隔離環境完整驗收
   - 記錄所有命令與結果
   - 提供完整證據

### 不執行項目

- ✗ 生產資料清理（等待 owner 批准）
- ✗ 修改共用 .env
- ✗ EMA 變更驗證（獨立交付）
- ✗ 部署或啟動實盤

---

## 七、結論

**已完成：**
- 安全閘門從 warning 改為 rejection
- 13 個負向測試證明真正拒絕
- Migration 007 修正約束問題
- 有限權限測試角色建立
- 隔離測試資料庫準備完成

**未完成：**
- 全面強制接入所有寫入入口
- 真實入口負向測試
- Migration 完整驗證
- 重新執行驗收 #137-144

**狀態：** 防護函式已強化，全面接入與驗收重新執行待完成。

**生產資料：** 完全未修改，等待 owner 決定。

---

**報告準備：** Development agent  
**日期：** 2026-11-07  
**SHA：** `376380e` (remote), 本地可能有未提交變更
