# Alethra UI/UX 設計原型 — 視覺審核指南

**日期**：2026-10-27  
**狀態**：等待 Owner 審核  
**目的**：提供三個設計方向的視覺原型，供 Owner 比較與選擇

---

## 快速開始

### 1. 開啟視覺原型

三個 HTML 原型文件可直接在瀏覽器開啟：

```bash
# 方向 A：Trading Platform Standard（深色、密集、專業）
open docs/design-prototypes/direction-a-trading-platform.html

# 方向 B：Research-First Professional Workspace（淺色、卡片、研究導向）⭐ 推薦
open docs/design-prototypes/direction-b-research-first.html

# 方向 C：Minimalist Research Tool（極簡、散文式、閱讀優先）
open docs/design-prototypes/direction-c-minimalist.html
```

### 2. 閱讀決策補充文件

```bash
open docs/design-decision-supplement.md
```

包含：
- 研究首頁指標計算定義與論證
- 響應式規格（Desktop/Tablet/Mobile）
- 核心術語表（18 個高頻詞）
- 實作計劃（無預設時程，按 Phase 估計）
- 精簡後的 4 個核心決策項目

### 3. 比較三個方向

每個原型包含相同內容，方便公平比較：
- **Login Page**：Desktop + Mobile
- **Research Page**：Desktop + Mobile（策略與實驗列表）
- **Workspace**：Desktop（三欄佈局）
- **Portfolio**：Desktop（持倉列表）

**相同內容**：
- 4 個實驗（AAPL 均線突破、TSLA 波動率、NVDA 動能、騰訊長線）
- 5 個觀察名單標的（AAPL、TSLA、NVDA、0700.HK、BTC）
- 3 個持倉（AAPL、TSLA、NVDA）
- 相同數據（勝率 62.5%、收益 +18.3%、總資產 $118,342）

**相同 Viewport**：
- Desktop：1200px 寬度（或 1024px for Login）
- Mobile：375px 寬度

**相同資料狀態**：
- 實驗狀態：1 已鎖定、1 探索中、1 草稿、1 已鎖定
- 持倉方向：全部多頭
- 盈虧：AAPL +6.85%、TSLA -1.51%、NVDA +10.52%

---

## 三個方向對比

### 方向 A：Trading Platform Standard

**視覺風格**：
- 深色主題（#0b0e11 背景）
- 密集資訊排列
- 等寬數字字體（monospace）
- 固定三欄比例（15%/55%/30%）

**優點**：
- 符合專業交易平台習慣（Binance、TradingView）
- 資訊密度高，適合進階用戶
- 價格/盈虧用顏色突出顯示

**缺點**：
- 學習曲線較陡
- 不適合新手用戶
- Mobile 體驗有限

**適用場景**：
- 目標用戶為有經驗的交易者
- 強調交易功能而非研究
- 未來可能發展為完整交易平台

---

### 方向 B：Research-First Professional Workspace ⭐ 推薦

**視覺風格**：
- 淺色主題（#f8f9fb 背景）
- 卡片式表面（elevation with shadows）
- 清晰資訊層級
- 彈性三欄佈局（可折疊研究面板）

**優點**：
- 符合 Alethra 定位（研究、測試、模擬交易）
- 強調研究流程（實驗 → 回測 → 證據）
- Context bar 顯示當前實驗/模式/回放狀態
- 研究面板整合策略規格/證據/回放設定
- 響應式支援完整（Desktop/Tablet/Mobile）

**缺點**：
- 資訊密度略低於方向 A
- 需要更多垂直空間

**適用場景**：
- 目標用戶為研究導向的投資者
- 強調「檢驗投資想法」的產品主張
- 未來可能發展為研究协作平台

**推薦理由**：
1. 符合 Alethra 產品定位（研究 > 交易）
2. 視覺風格現代、專業、易於接受
3. 實作成本可控（沿用現有 AppShell）
4. 可與 TradingView、Robinhood Legend 的專業用戶期待接軌

---

### 方向 C：Minimalist Research Tool

**視覺風格**：
- 極淺色主題（#fafafa 背景）
- 大量留白
- 散文式內容寬度（720px max）
- 極簡導航（無邊框按鈕）

**優點**：
- 閱讀體驗最佳
- 視覺干擾最少
- 適合專注研究與寫作
- 實作最簡單

**缺點**：
- 資訊密度最低
- 不適合快速交易操作
- 可能顯得功能不足

**適用場景**：
- 目標用戶為研究員/分析師
- 強調閱讀與寫作體驗
- 產品定位為研究筆記工具而非交易平台

---

## 需要 Owner 決定的 4 個事項

### 決策 1：設計方向選擇
- **選項**：A / B / C
- **建議**：B（Research-First）
- **影響**：整體視覺風格與資訊架構

### 決策 2：研究首頁指標顯示
- **選項**：
  - A. 顯示勝率/收益（含說明）
  - B. 僅顯示實驗數量與狀態
  - C. 不顯示指標卡片
- **建議**：A（提供脈絡，明確標示為模擬結果）
- **影響**：用戶對研究進展的理解
- **詳情**：見 `docs/design-decision-supplement.md` §1

### 決策 3：核心術語採用
- **選項**：
  - A. 全盤採用 18 個核心術語
  - B. 部分採用（指定調整項目）
  - C. 暫不採用
- **建議**：A（一致性是專業產品的基礎）
- **影響**：產品術語一致性與專業感
- **詳情**：見 `docs/design-decision-supplement.md` §3

### 決策 4：實作優先級
- **選項**：
  - A. 完整實作 Phase 1-5（18-20 天）
  - B. 優先 Phase 1-3（8-12 天），Phase 4-5 延後
  - C. 僅 Phase 1-2（5-8 天）
- **建議**：B（先完成核心頁面）
- **影響**：專案時程與資源分配
- **詳情**：見 `docs/design-decision-supplement.md` §4

---

## 文件清單

### 視覺原型（3 個 HTML 文件）
- `direction-a-trading-platform.html` — 深色專業交易平台風格
- `direction-b-research-first.html` — 淺色研究導向風格 ⭐
- `direction-c-minimalist.html` — 極簡散文式風格

### 決策文件（2 個 Markdown 文件）
- `design-decision-supplement.md` — 指標論證、響應式規格、術語表、實作計劃
- `design-proposal-owner-review.md` — Owner 審核摘要（前次會議產出）

### 研究文件（1 個 Markdown 文件）
- `design-research-evidence-matrix.md` — 競品研究證據矩陣（7 平台、18 樣本）

### 完整提案（1 個 Markdown 文件）
- `design-proposal-v1.md` — 完整設計提案（含競品研究、診斷、三個方向、85 個術語）

---

## 重要提醒

### 本輪不執行
- ❌ 不修改產品 CSS/組件/後端
- ❌ 不提交/推送/合併/部署
- ❌ 不啟動任何 Phase 實作

### 本輪僅交付
- ✅ 視覺原型（HTML 文件）
- ✅ 決策依據（Markdown 文件）
- ✅ 等待 Owner 審核與決策

### 決策後行動
- Owner 選擇方向與選項後，再進入 Phase 1 實作
- Phase 1 實作前需 Owner 明確批准
- 實作過程中嚴格遵循批准的規格

---

## 聯繫與反饋

如有任何問題或需要調整：
1. 標註具體的決策編號（決策 1-4）
2. 說明偏好的選項（A/B/C）
3. 提供調整建議（若有）

**等待 Owner 回覆後再繼續。**

---

*文件結束*
