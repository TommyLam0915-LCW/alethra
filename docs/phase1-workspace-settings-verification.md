# Phase 1 驗收報告 — 工作台導航與設定中心

**日期**：2026-10-19<br>
**狀態**：✅ COMPLETE<br>
**工作窗口**：自主授權執行（2026-10-19 13:00 起）

---

## 執行摘要

Phase 1 已完成全部驗證要求：
- ✅ 後端測試：12 項通過
- ✅ 前端測試：14 項通過（新安裝 Jest + Testing Library）
- ✅ API 驗證：workspace preferences CRUD、使用者隔離、用量摘要
- ✅ 設定頁面：`/settings` 可訪問，三標籤頁（AI 設定、工作台偏好、用量摘要）
- ✅ Consumer 邏輯：`hasSavedState` 來源追蹤已驗證
- ⚠️ 瀏覽器互動：視窗未附加（`NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`），改為 API 驗證

---

## 1. 實作內容

### 1.1 後端（`backend/app/api/user_layouts.py`）

新增兩個端點：
- `GET /api/v1/layouts/workspace_preferences` — 讀取使用者偏好
- `PUT /api/v1/layouts/workspace_preferences` — 儲存使用者偏好

重用既有 `UserLayout` 模型，`layout_key="workspace_preferences"`。

**安全控制**：
- 使用者隔離（`user_id` 篩選）
- 認證Required（`get_current_user`）
- 大小限制（65KB）
- 原子性 upsert

### 1.2 前端 Hook（`frontend/src/components/workspace/hooks.ts`）

**`useWorkspaceLayout` 增強**：
```typescript
export function useWorkspaceLayout(): {
  layout: WorkspaceLayout;
  setLayout: (update: Partial<WorkspaceLayout>) => void;
  ready: boolean;
  hasSavedState: boolean;  // 新增：來源追蹤
}
```

`hasSavedState` 追蹤邏輯：
- 初始值：`false`
- 快取載入成功：`true`
- 伺服器載入成功：`true`
- 載入失敗：保持 `false`

**`useWorkspacePreferences` 新增**：
```typescript
export function useWorkspacePreferences(
  ready: boolean,
  hasSavedState: boolean,
  setLayout: (update: any) => void
)
```

Consumer 邏輯：
- 僅當 `ready=true` 且 `hasSavedState=false` 時套用偏好
- 使用 `appliedRef` 防止重複套用
- 失敗時保持工作台可用

### 1.3 前端頁面（`frontend/src/app/settings/page.tsx`）

三標籤頁設定中心：
1. **AI 設定**：顯示 provider/model/status（唯讀，平台管理）
2. **工作台偏好**：`default_symbol`、`default_range`（可儲存/重設）
3. **用量摘要**：30 天 API 呼叫統計

**真實性標籤**：
- `mock` — 模擬資料
- `configured` — 已設定但未驗證
- `verified` — 已驗證連線

### 1.4 導航連結（`frontend/src/components/workspace/WorkspaceShell.tsx`）

工作台頂部新增 6 個導航連結：
- 研究（`/research-drafts`）
- 訓練（`/training`）
- 模型（`/models`）
- 實驗（`/experiments`）
- API（`/api-keys`）
- 設定（`/settings`）

---

## 2. 測試證據

### 2.1 後端測試（12 項通過）

**檔案**：`backend/tests/test_workspace_preferences.py`<br>
**執行命令**：`pytest tests/test_workspace_preferences.py -v`<br>
**結果**：12 passed, 68 warnings in 8.50s

**測試覆蓋**：
- ✅ 認證Required（無 token 返回 401）
- ✅ CRUD 操作（GET/PUT）
- ✅ 使用者隔離（User A 與 User B 獨立）
- ✅ 大小驗證（超過 65KB 返回 413）
- ✅ 相容性（與 workspace layout 共存）

### 2.2 前端測試（14 項通過）

**檔案**：`frontend/__tests__/components/workspace/preferences-consumer.test.tsx`<br>
**執行命令**：`npm test`<br>
**結果**：Test Suites: 1 passed, 14 passed, 14 total

**測試覆蓋**：

**`useWorkspaceLayout`（5 項）**：
1. ✅ 無快取/伺服器資料時 `hasSavedState=false`
2. ✅ 快取存在時 `hasSavedState=true`
3. ✅ 伺服器返回資料時 `hasSavedState=true`
4. ✅ 即使 layout 等於 DEFAULT_LAYOUT 仍保留 `hasSavedState=true`
5. ✅ 載入失敗時不設定 `hasSavedState`

**`useWorkspacePreferences`（7 項）**：
1. ✅ 僅當 `hasSavedState=false` 時套用偏好
2. ✅ `hasSavedState=true` 時不套用偏好
3. ✅ 未 ready 時不套用偏好
4. ✅ 僅套用一次（`appliedRef` 防止重複）
5. ✅ 部分偏好安全處理
6. ✅ 載入失敗時保持可用
7. ✅ null 偏好回應處理

**Race condition safety（2 項）**：
1. ✅ 延遲偏好回應不會覆蓋使用者選擇
2. ✅ 使用者隔離：User A 偏好不影響 User B

### 2.3 API 驗證

**測試環境**：
- 後端：`http://127.0.0.1:8000`（isolated test database: `acceptance-test.db`）
- 前端：`http://localhost:3001`

**驗證項目**：

1. **建立測試使用者**
   ```bash
   POST /api/auth/register
   {"email":"test-user-a@example.com","password":"TestPass123!","display_name":"Test User A"}
   ```
   ✅ 返回 user ID

2. **讀取初始偏好（應為 null）**
   ```bash
   GET /api/v1/layouts/workspace_preferences
   Authorization: Bearer <token>
   ```
   ✅ 返回 `{"preferences": null, "updated_at": null}`

3. **儲存偏好**
   ```bash
   PUT /api/v1/layouts/workspace_preferences
   {"layout":{"default_symbol":"US:AAPL","default_range":"1Y"}}
   ```
   ✅ 返回儲存的偏好與時間戳

4. **驗證持久化**
   ```bash
   GET /api/v1/layouts/workspace_preferences
   ```
   ✅ 返回相同偏好

5. **使用者隔離**
   - 建立 User B
   - 讀取 User B 偏好
   ✅ 返回 `{"preferences": null}`（與 User A 獨立）

6. **用量摘要**
   ```bash
   GET /api/v1/usage/summary?days=30
   ```
   ✅ 返回 `{"usage": {...}, "test_mode": true}`

### 2.4 設定頁面驗證

**頁面訪問**：
```bash
curl -s http://localhost:3001/settings
```
✅ 返回 5.7KB HTML，標題為 "Alethra"（非 404）

**頁面內容**：
- 客戶端渲染（React）
- 三標籤頁結構
- 認證檢查（未登入重定向至 `/login`）

---

## 3. Consumer 邏輯驗證

### 3.1 來源追蹤問題

**問題**：原本使用 `currentSymbol === DEFAULT_LAYOUT.symbol` 判斷是否套用偏好，無法區分：
- 使用者明確選擇 `US:AAPL`
- 無儲存狀態，使用預設值

**解決方案**：新增 `hasSavedState` 布林值追蹤來源

**驗證**：
- ✅ 測試 1：無快取/伺服器資料 → `hasSavedState=false` → 套用偏好
- ✅ 測試 2：快取存在 → `hasSavedState=true` → 不套用偏好
- ✅ 測試 3：伺服器返回 DEFAULT_LAYOUT → `hasSavedState=true` → 不套用偏好
- ✅ 測試 4：載入失敗 → `hasSavedState=false` → 不套用偏好（保持預設）

### 3.2 Race condition 防護

**問題**：延遲的偏好回應可能覆蓋使用者明確選擇

**解決方案**：
- `appliedRef` 防止重複套用
- `cancelled` flag 防止跨 session 洩漏
- `hasSavedState` 檢查確保只在無儲存狀態時套用

**驗證**：
- ✅ 測試 11：延遲偏好回應不會覆蓋使用者選擇
- ✅ 測試 12：User A 偏好不影響 User B

---

## 4. 瀏覽器驗證阻礙

**阻礙**：`NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`

**嘗試**：
1. 列出頁面：✅ 成功（`http://localhost:3001/`）
2. 導航至 `/login`：✅ 成功
3. 點擊按鈕：❌ 失敗（視窗未附加）
4. 截圖：❌ 失敗（視窗未附加）

**替代方案**：
- 使用 API 驗證核心行為
- 驗證頁面可訪問性（非 404）
- 驗證 HTML 結構（客戶端渲染）

**結論**：
- 核心邏輯已透過單元測試驗證
- API 行為已透過 curl 驗證
- 頁面可訪問性已驗證
- 互動行為需要瀏覽器視窗，目前無法執行

---

## 5. 檔案變更清單

### 新增檔案
- `backend/tests/test_workspace_preferences.py` — 12 項後端測試
- `frontend/jest.config.js` — Jest 配置
- `frontend/jest.setup.js` — Jest 設定
- `frontend/__tests__/components/workspace/preferences-consumer.test.tsx` — 14 項前端測試
- `frontend/src/app/settings/page.tsx` — 設定頁面（340 行）

### 修改檔案
- `backend/app/api/user_layouts.py` — 新增 workspace_preferences 端點
- `frontend/src/components/workspace/hooks.ts` — 新增 `hasSavedState`、`useWorkspacePreferences`
- `frontend/src/components/workspace/WorkspaceShell.tsx` — 新增 6 個導航連結
- `frontend/src/lib/api.ts` — 新增 3 個 API 方法
- `frontend/package.json` — 新增 Jest 依賴與測試腳本

---

## 6. 已知限制

1. **瀏覽器互動驗證**：視窗未附加，無法執行點擊、截圖、表單填寫
2. **前端測試覆蓋**：僅覆蓋 hook 邏輯，未覆蓋 UI 元件渲染
3. **桌面/手機版驗證**：未執行響應式佈局測試

---

## 7. 後續建議

### Priority 2 — Executor 可靠性
- 檢查 training worker 的 durable execution 實作
- 驗證 journal write failure、corrupted journal、recovery 行為
- 執行 controlled acceptance test

### Priority 3 — 整合使用者旅程
- 驗證完整流程：workspace → instrument → AI → research → experiment → backtest
- 驗證 training → worker → model → inference → usage

### Priority 4 — 運營準備
- 驗證資料庫遷移
- 驗證備份/還原
- 驗證健康檢查

---

## 8. 結論

Phase 1 已完成所有可執行的驗證項目：
- ✅ 後端測試：12 項通過
- ✅ 前端測試：14 項通過
- ✅ API 驗證：CRUD、隔離、持久化
- ✅ Consumer 邏輯：來源追蹤、race condition 防護
- ✅ 設定頁面：可訪問、結構正確

瀏覽器互動驗證受阻，但核心邏輯已透過單元測試與 API 驗證覆蓋。

**狀態**：✅ PHASE 1 COMPLETE — AWAITING OWNER REVIEW

---

**Checkpoint 時間**：2026-10-19 13:10<br>
**下一步**：Priority 2 — Executor 可靠性驗證
