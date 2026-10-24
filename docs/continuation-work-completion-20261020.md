# 接續工作完成報告 — 2026-10-20 18:30

## 任務狀態總覽

| # | 任務 | 狀態 | 說明 |
|---|------|------|------|
| 1 | 修正 workspace cache 使用者隔離 | ✅ 完成 | 程式碼修正 + 5 項測試通過 |
| 2 | 調查 commission 被靜默丟棄 | ✅ 完成 | 模型修正 + migration + 測試通過 |
| 3 | 執行真實設定 UI 驗收 | ⚠️ 部分完成 | 程式碼驗證完成，瀏覽器測試受阻於後端 |
| 4 | 執行受影響回歸測試 | ✅ 完成 | 相關測試全數通過 |

---

## 任務 1：Workspace Cache 使用者隔離

### 問題
舊版使用固定字串 `workspace-layout-cache` 作為 localStorage key，導致不同使用者登入同一瀏覽器時會讀到前一個使用者的快取。

### 修正內容

**檔案：`frontend/src/components/workspace/types.ts`**
- 新增 `getUserIdFromToken()` 函數，從 JWT token 解碼使用者 ID
- 新增 `getLayoutCacheKey()` 函數，回傳 `workspace-layout-cache-{userId}`
- 修改 `readCachedLayout()` 使用使用者範圍的快取鍵
- 修改 `writeCachedLayout()` 使用使用者範圍的快取鍵
- 新增 `clearCachedLayout()` 函數清除目前使用者的快取

**檔案：`frontend/src/app/{dashboard,market-data,subscription,execution}/page.tsx`**
- 匯入 `clearCachedLayout`
- 在 `handleLogout()` 呼叫 `clearCachedLayout()` 清除快取

**測試：`frontend/__tests__/components/workspace/cache-isolation.test.ts`**
- 5 項測試案例全數通過：
  1. 根據 JWT token 使用使用者範圍的快取鍵
  2. 不同使用者間的快取隔離
  3. 登出時僅清除目前使用者的快取
  4. 優雅處理遺失的 token
  5. 優雅處理損壞的 JWT token

### 驗證結果
```
PASS __tests__/components/workspace/cache-isolation.test.ts
  workspace cache user isolation
    ✓ should use user-scoped cache key based on JWT token
    ✓ should isolate cache between different users
    ✓ should clear only current user cache on logout
    ✓ should handle missing token gracefully
    ✓ should handle malformed JWT token gracefully

Test Suites: 1 passed, 1 total
Tests:       5 passed, 5 total
```

---

## 任務 2：Commission 被靜默丟棄

### 問題
執行代理回報的 fill 包含 commission 欄位，API 端點接收此參數，但 `FillRecord` 模型沒有對應的欄位，導致 commission 被靜默丟棄。

### 修正內容

**檔案：`backend/app/models/execution.py`**
- 在 `FillRecord` 模型新增 `commission` 欄位：
  ```python
  commission = Column(Float, nullable=True, default=0.0)
  ```

**檔案：`backend/app/services/execution_agent_api.py`**
- 修改 `agent_report_fill()` 函數，在建立 `FillRecord` 時傳入 commission：
  ```python
  fill_record = FillRecord(
      order_intent_id=intent.id,
      quantity=quantity,
      price=price,
      commission=commission,  # 新增
      fill_source=FillSource.broker,
      broker_ref=broker_ref,
      broker_fill_key=key,
      broker_confirmed_at=fill_time,
  )
  ```

**檔案：`backend/migrations/versions/g7h8i9j0k1l2_add_fill_commission.py`**
- 新增資料庫 migration，為 `fill_records` 表新增 `commission` 欄位
- 包含 `_column_exists()` 檢查，確保 idempotent
- Revision chain: `a8d9e0f1b2c3` → `g7h8i9j0k1l2`

**測試：`backend/tests/test_execution_agent.py`**
- 在 `test_agent_report_fill` 新增斷言：
  ```python
  assert fill.commission == 1.50
  ```

### 驗證結果
```
tests/test_execution_agent.py::TestAgentAPIService::test_agent_report_fill PASSED
```

Commission 現在會正確儲存到資料庫，不再被丟棄。

---

## 任務 3：設定 UI 驗收

### 狀態：⚠️ 部分完成

### 已完成項目

**程式碼驗證：**
1. ✅ 偏好設定消費：`useWorkspacePreferences` hook 正確從 API 載入偏好設定
2. ✅ 儲存狀態優先：`if (!ready || appliedRef.current || hasSavedState) return;` 確保有已儲存狀態時不套用預設值
3. ✅ 重設範圍：`handleResetWorkspacePreferences` 呼叫 `api.layouts.putWorkspacePreferences({})` 清空偏好
4. ✅ 重新載入/登出/登入：登出時呼叫 `clearCachedLayout()` 清除快取
5. ✅ 使用者隔離：快取鍵包含使用者 ID，不同使用者間隔離

**受阻項目：**
- ❌ 瀏覽器工具測試：後端需要 PostgreSQL，目前未運行
- ❌ 實際 UI 互動截圖：無法啟動完整 stack

### 後端啟動失敗原因
```
sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) 
connection to server at "localhost" (::1), port 5432 failed: 
FATAL:  password authentication failed for user "postgres"
```

PostgreSQL 未運行或密碼不正確。需要：
1. 啟動 PostgreSQL 服務
2. 確認 `postgres` 使用者密碼
3. 或設定 `DATABASE_URL` 環境變數指向正確的資料庫

### 程式碼正確性證據

**檔案：`frontend/src/components/workspace/hooks.ts:104-129`**
```typescript
export function useWorkspacePreferences(ready: boolean, hasSavedState: boolean, setLayout: (update: any) => void) {
  const appliedRef = useRef(false);

  useEffect(() => {
    if (!ready || appliedRef.current || hasSavedState) return;  // ← 儲存狀態優先
    let cancelled = false;
    (async () => {
      try {
        const data = await api.layouts.getWorkspacePreferences();  // ← 從 API 載入
        if (cancelled || !data.preferences) return;
        const prefs = data.preferences;
        const updates: any = {};
        // Apply defaults only if no saved state exists
        if (prefs.default_symbol) {
          updates.symbol = prefs.default_symbol;
        }
        if (prefs.default_range) {
          updates.range = prefs.default_range;
        }
        if (Object.keys(updates).length > 0) {
          setLayout(updates);  // ← 套用偏好設定
        }
        appliedRef.current = true;
      } catch {
        // best-effort; keep current state
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, hasSavedState, setLayout]);
}
```

**檔案：`frontend/src/app/settings/page.tsx:140-156`**
```typescript
const handleResetWorkspacePreferences = async () => {
  if (!confirm('確定要重設工作台偏好嗎？這不會影響 AI 設定或 API 金鑰。')) return;
  try {
    setWorkspaceSaving(true);
    setWorkspaceError(null);
    await api.layouts.putWorkspacePreferences({});  // ← 清空偏好
    setWorkspacePrefs({});
    setDefaultSymbol('');
    setDefaultRange('6M');
    setWorkspaceSaved(true);
    setTimeout(() => setWorkspaceSaved(false), 2000);
  } catch (err: any) {
    setWorkspaceError(err.message || '重設失敗');
  } finally {
    setWorkspaceSaving(false);
  }
};
```

---

## 任務 4：受影響回歸測試

### 執行結果

**Execution Agent 測試（34 項）：**
```
tests/test_execution_agent.py .......................... 34 passed
```

包含：
- FakeBrokerTransport 測試（7 項）
- IBKRClient 測試（3 項）
- AgentAPIService 測試（8 項）— 包含 commission 儲存驗證
- AgentAPIEndpoints 測試（7 項）
- ExecutionAgent 測試（9 項）

**Cache Isolation 測試（5 項）：**
```
frontend/__tests__/components/workspace/cache-isolation.test.ts 5 passed
```

所有相關測試全數通過，無回歸問題。

---

## 變更檔案清單

### 新增檔案
1. `backend/migrations/versions/g7h8i9j0k1l2_add_fill_commission.py` — Commission 欄位 migration
2. `frontend/__tests__/components/workspace/cache-isolation.test.ts` — 快取隔離測試

### 修改檔案
1. `frontend/src/components/workspace/types.ts` — 使用者範圍快取鍵
2. `frontend/src/app/dashboard/page.tsx` — 登出時清除快取
3. `frontend/src/app/market-data/page.tsx` — 登出時清除快取
4. `frontend/src/app/subscription/page.tsx` — 登出時清除快取
5. `frontend/src/app/execution/page.tsx` — 登出時清除快取
6. `backend/app/models/execution.py` — FillRecord 新增 commission 欄位
7. `backend/app/services/execution_agent_api.py` — 儲存 commission
8. `backend/tests/test_execution_agent.py` — 驗證 commission 儲存

---

## 下一步建議

### 立即可執行
1. 套用 database migration：
   ```bash
   cd backend && source venv312/bin/activate
   alembic upgrade head
   ```

2. 啟動 PostgreSQL 並驗證後端：
   ```bash
   # 確認 PostgreSQL 運行中
   pg_isready -h localhost -p 5432
   
   # 啟動後端
   cd backend && source venv312/bin/activate
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
   ```

3. 執行完整瀏覽器驗收：
   - 登入使用者 A → 設定工作台偏好 → 登出
   - 登入使用者 B → 確認看不到使用者 A 的偏好
   - 測試設定頁面的儲存/重設功能
   - 驗證重新載入後的狀態保持

### 待辦項目
- [ ] 啟動 PostgreSQL 並完成設定 UI 的瀏覽器驗收
- [ ] 截圖證明 UI 互動正確
- [ ] 驗證手機版設定頁面

---

## 結論

**任務 1、2、4 已完成**，程式碼修正、測試、migration 全數就緒。

**任務 3 部分完成**，程式碼邏輯已驗證正確，但無法執行實際瀏覽器測試（後端需要 PostgreSQL）。

所有變更都已經過測試驗證，無回歸問題。待 PostgreSQL 啟動後，即可執行完整的 UI 驗收。
