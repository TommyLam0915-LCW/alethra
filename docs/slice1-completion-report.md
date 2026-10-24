# Slice 1 實作完成報告

**日期**: 2026-10-22  
**狀態**: 基礎設施完成，待驗收

## 已完成項目

### 1. 後端資料模型擴展

#### ResearchRun 模型擴展
- **檔案**: `backend/app/models/research_run.py`
- **變更**: 新增兩個 JSONB 欄位
  - `daily_equity_curve`: 儲存每日帳務紀錄（日期、現金餘額、持倉數量、估值價格、總淨值）
  - `transactions`: 儲存交易明細（執行日期、動作、數量、價格、手續費、現金變動、持倉後數量）

#### 遷移檔案
- **檔案**: `backend/migrations/versions/a1b2c3d4e5f6_add_daily_equity_and_transactions.py`
- **功能**: 為 research_runs 資料表新增兩個欄位
- **依賴**: 接在 `8616c0c9aa3e` (dataset binding) 之後

### 2. API 層更新

#### runs.py 更新
- **檔案**: `backend/app/api/runs.py`
- **變更**: 在 `create_run` 函數中，將回測結果的 `daily_equity_curve` 和 `transactions` 儲存到資料庫
- **程式碼**:
  ```python
  run.daily_equity_curve = result.get("daily_equity_curve")
  run.transactions = result.get("transactions")
  ```

#### schemas.py 更新
- **檔案**: `backend/app/api/schemas.py`
- **變更**: `ResearchRunResponse` 新增兩個欄位
  - `daily_equity_curve: Optional[list]`
  - `transactions: Optional[list]`

### 3. 前端介面實作

#### 資料集管理頁面（已完成）
- **檔案**: `frontend/src/app/experiments/[id]/data/page.tsx`
- **功能**:
  - 檢視可用資料集
  - 選取資料集
  - 綁定至目前實驗版本
  - 顯示綁定詳情（來源、時區、日期範圍、缺口備註）

#### 回測詳細結果頁面（新建）
- **檔案**: `frontend/src/app/experiments/[id]/runs/[runId]/page.tsx`
- **功能**:
  - 顯示摘要指標（總報酬、最終淨值、最大回撤、交易次數）
  - 分頁檢視：
    - **每日帳務**: 表格顯示每日現金餘額、持倉數量、估值價格、總淨值
    - **交易明細**: 表格顯示每筆交易的執行日期、動作（買入/賣出）、數量、價格、手續費、現金變動、持倉後數量
  - 方法論說明（引擎版本、策略模板、執行慣例）
  - 風險提醒（歷史資料不代表未來績效）

#### 實驗詳情頁更新
- **檔案**: `frontend/src/app/experiments/[id]/page.tsx`
- **變更**:
  1. 新增「管理資料」按鈕，導向 `/experiments/[id]/data`
  2. 回測結果區新增「查看詳細每日帳務與交易明細 →」連結
  3. 執行歷史列表項目可點擊，導向該次執行的詳細頁面
  4. 滑鼠懸停時背景色變化，提示可點擊

## 資料流向

```
回測引擎 (backtest.py)
  ↓
run_backtest() 回傳
  - daily_equity_curve: List[Dict]
  - transactions: List[Dict]
  ↓
API 層 (runs.py)
  - 儲存至 ResearchRun.daily_equity_curve
  - 儲存至 ResearchRun.transactions
  ↓
資料庫 (PostgreSQL)
  - research_runs 資料表
  ↓
前端 API 呼叫
  - api.runs.list(experimentId)
  ↓
前端頁面 (runs/[runId]/page.tsx)
  - 顯示每日帳務表格
  - 顯示交易明細表格
```

## 驗收步驟

### 前置作業
1. 執行資料庫遷移：
   ```bash
   cd backend
   alembic upgrade head
   ```

2. 啟動後端伺服器：
   ```bash
   uvicorn app.main:app --reload
   ```

3. 啟動前端開發伺服器：
   ```bash
   cd frontend
   npm run dev
   ```

### 測試情境

#### 情境 1: 資料集綁定
1. 登入系統
2. 建立或選取一個實驗
3. 點擊「管理資料」按鈕
4. 選取一個資料集並綁定
5. 確認綁定詳情正確顯示

#### 情境 2: 回測執行與詳細檢視
1. 回到實驗詳情頁
2. 點擊「執行回測」按鈕
3. 等待回測完成
4. 點擊「查看詳細每日帳務與交易明細 →」連結
5. 確認進入詳細結果頁面
6. 切換「每日帳務」和「交易明細」分頁
7. 確認表格資料正確顯示
8. 確認方法論說明正確

#### 情境 3: 執行歷史導航
1. 回到實驗詳情頁
2. 在「執行歷史」區點擊任一次執行
3. 確認進入該次執行的詳細頁面
4. 確認資料正確

## 技術細節

### 每日帳務資料結構
```typescript
interface DailyEquity {
  date: string;              // "YYYY-MM-DD"
  cash_balance: number;      // 現金餘額
  position_qty: number;      // 持倉數量
  valuation_price: number;   // 估值價格（收盤價）
  equity: number;            // 總淨值 = cash_balance + position_qty * valuation_price
}
```

### 交易明細資料結構
```typescript
interface Transaction {
  date: string;              // 信號日期
  execution_date: string;    // 執行日期
  action: string;            // "BUY" 或 "SELL"
  quantity: number;          // 交易數量
  price: number;             // 成交價格
  commission: number;        // 手續費
  net_cash_change: number;   // 現金變動
  shares_after: number;      // 交易後持倉
}
```

## 已知限制

1. **資料來源**: 目前使用示範資料（demo data），非即時市場資料
2. **外部服務**: Twelve Data API 整合待憑證配置
3. **效能**: 大量交易紀錄可能需要分頁優化
4. **圖表**: 目前僅表格顯示，未實作視覺化圖表

## 下一步

### Slice 1 完成條件
- [x] 真實資料引用基礎設施（dataset binding）
- [x] 固定資料引用（dataset_id 綁定）
- [x] 回測執行（已支援）
- [x] 每日紀錄儲存（daily_equity_curve）
- [x] 瀏覽器查閱（詳細結果頁面）

### 接續工作
1. **Slice 2**: 人類重放會話 → 模擬執行 → 決策日誌
2. **Slice 3**: 真實 AI 分析 → 個人模型訓練 → 推論

## 檔案清單

### 新增檔案
- `backend/migrations/versions/a1b2c3d4e5f6_add_daily_equity_and_transactions.py`
- `frontend/src/app/experiments/[id]/runs/[runId]/page.tsx`

### 修改檔案
- `backend/app/models/research_run.py`
- `backend/app/api/runs.py`
- `backend/app/api/schemas.py`
- `frontend/src/app/experiments/[id]/page.tsx`

### 已存在（前次實作）
- `backend/app/api/datasets.py`
- `backend/migrations/versions/8616c0c9aa3e_add_dataset_binding.py`
- `frontend/src/app/experiments/[id]/data/page.tsx`

## 驗證狀態

- **語法檢查**: ✅ Python 檔案編譯通過
- **型別定義**: ✅ TypeScript 介面已定義
- **資料流向**: ✅ 從引擎到前端完整追蹤
- **UI 導航**: ✅ 新增連結與按鈕

## 待驗收項目

- [ ] 資料庫遷移執行
- [ ] 後端 API 測試
- [ ] 前端頁面渲染
- [ ] 端到端流程測試
- [ ] 瀏覽器接受度測試
