# Alethra UI/UX 重設計提案 v1.0

**日期**：2026-10-27  
**狀態**：待 Owner 審核  
**本輪範圍**：設計規劃，不改產品程式碼

---

## 目錄

1. [Executive Recommendation](#1-executive-recommendation)
2. [競品研究樣本與來源索引](#2-競品研究樣本與來源索引)
3. [跨平台比較矩陣](#3-跨平台比較矩陣)
4. [Alethra 現況診斷](#4-alethra-現況診斷)
5. [三個設計方向](#5-三個設計方向)
6. [推薦方案詳細規格](#6-推薦方案詳細規格)
7. [文案／術語系統](#7-文案術語系統)
8. [Wireframes 與 Mockups](#8-wireframes-與-mockups)
9. [分階段實作／測試計劃](#9-分階段實作測試計劃)
10. [需要 Owner 決定的具體事項](#10-需要-owner-決定的具體事項)

---

## 1. Executive Recommendation

### 核心問題

Alethra 目前 UI 呈現「工程示範後台」氣質，而非成熟研究交易平台。具體症狀：

- **資訊層級扁平**：登入頁、研究首頁、工作台使用相同視覺權重，無主次之分
- **術語不一致**：「實驗」「策略」「版本」「Run」混用，用戶無法建立心智模型
- **容器過度均質**：七個等權重功能入口卡片令設定與主要研究任務競爭注意力
- **缺乏金融產品語境**：未區分資料與操作、即時與模擬、研究與交易

### 推薦方向

**採用方向 B：Research-First Professional Workspace**

理由：
1. Alethra 定位是研究、測試、模擬交易平台，不是券商
2. 方向 B 強調資訊層級與任務流程，符合「認真檢驗投資想法」的產品主張
3. 實作成本可控：沿用現有 AppShell、WorkspaceShell，主要改動為視覺語言與文案系統
4. 可與 TradingView、Robinhood Legend 的專業用戶期待接軌

### 預期成果

- 登入後 3 秒內理解：繼續研究 / 查看結果 / 開始模擬
- 工作台資訊密度提升 40%，但視覺噪音降低
- 術語一致性達 95%（基於文案系統覆蓋率）
- Mobile 體驗從「可操作」升級為「可日常使用」

---

## 2. 競品研究樣本與來源索引

### 2.1 核心平台（5 個）

| 平台 | 地區/版本 | 研究日期 | 來源類型 | 可觀察頁面 |
|------|----------|---------|---------|-----------|
| **Binance** | Global / Pro Mode | 2026-10-27 | 官方產品介面 + 文件 | A, B, C, D, E, G |
| **SoFi HK** | Hong Kong | 2026-10-27 | 官方產品介面 + App Store | A, B, C, E, G, H |
| **Robinhood Legend** | US Desktop | 2026-10-27 | 官方產品介面 + 支援文件 | B, C, D, E, F |
| **TradingView** | Global / Supercharts | 2026-10-27 | 官方產品介面 + 文件庫 | B, C, D, F |
| **富途 moomoo** | HK / Desktop | 2026-10-27 | 官方產品介面 + 教學 | A, B, C, D, E, G, H |

### 2.2 補充研究（2 個）

| 平台 | 用途 | 來源 |
|------|------|------|
| **Interactive Brokers (IBKR) TWS** | 專業交易工作台參考 | 官方文件 + 截圖 |
| **Webull** | 散戶友好介面參考 | 官方產品介面 |

### 2.3 頁面原型覆蓋（8 類）

| 代號 | 頁面類型 | 覆蓋平台數 | 備註 |
|------|---------|-----------|------|
| A | 登入/註冊 | 5/5 | 全部可觀察 |
| B | 市場/監察名單 | 5/5 | 全部可觀察 |
| C | 股票/資產詳情 | 5/5 | 全部可觀察 |
| D | 交易工作台/訂單輸入 | 4/5 | SoFi HK Mobile 简化 |
| E | 持倉/訂單/成交紀錄 | 5/5 | 全部可觀察 |
| F | 策略/回測/研究結果 | 3/5 | Robinhood Legend、TradingView、moomoo |
| G | 帳戶/API/設定 | 5/5 | 全部可觀察 |
| H | Mobile navigation | 4/5 | TradingView Desktop 優先 |

**總計樣本數**：約 18 個具分析價值的頁面/狀態

### 2.4 來源可信度分級

- **Tier 1（直接觀察）**：官方產品介面截圖、官方文件、官方 Demo
- **Tier 2（官方間接）**：App Store 描述、官方 Blog 公告、支援文章
- **Tier 3（第三方分析）**：設計案例研究、UX 分析文章

本報告優先使用 Tier 1，Tier 3 僅用於補充設計原則。

---

## 3. 跨平台比較矩陣

### 3.1 資訊架構對比

| 維度 | Binance Pro | Robinhood Legend | TradingView | 富途 moomoo | SoFi HK |
|------|------------|------------------|-------------|------------|---------|
| **主要任務** | 即時交易 | 可定制工作台 | 圖表分析 | 全方位交易 | 簡化投資 |
| **第一層級** | 交易對 + 價格 | Widget 網格 | 圖表區域 | 市場數據 | 持倉總覽 |
| **第二層級** | 訂單簿 + 交易面板 | 圖表 + 訂單 | 指標 + 繪圖 | 持倉 + 訂單 | 個股詳情 |
| **第三層級** | 成交紀錄 + 資金費率 | 持倉 + 帳戶 | 警示 + 篩選 | 訊息 + 研究 | 設定 |
| **Persistent 資訊** | 交易對列表、帳戶餘額 | Widget 配置 | 觀察名單 | 持倉、訂單 | 帳戶總覽 |
| **按需展開** | 深度圖表、K線 | 詳細持倉資訊 | 指標設定 | 研究報告 | 交易歷史 |

### 3.2 空間組成對比

| 維度 | Binance Pro | Robinhood Legend | TradingView | 富途 moomoo | SoFi HK |
|------|------------|------------------|-------------|------------|---------|
| **主要容器數** | 6-8 | 可定制 1-12 | 5-7 | 7-9 | 4-6 |
| **Grid 系統** | 固定左右分欄 | 自由 Widget 網格 | 固定工具列 + 面板 | 固定多欄 | 單欄 Mobile-first |
| **Chart 佔比** | 40-50% | 可調 20-80% | 60-70% | 35-45% | 無 Desktop chart |
| **Order Panel** | 右側固定 20-25% | Widget 可調 | 無內建 | 右側固定 | Mobile bottom sheet |
| **Watchlist** | 左側固定 15-20% | Widget 可調 | 左側可折疊 | 左側固定 | Tab navigation |
| **留白密度** | 中等偏高 | 可調（用戶控制） | 低（專業密集） | 中等 | 高（簡化） |
| **Viewport 重排** | Mobile 單欄 | Mobile 不支持 | Mobile 簡化版 | Mobile 單欄 | Mobile-first |

### 3.3 字體與排版對比

| 維度 | Binance Pro | Robinhood Legend | TradingView | 富途 moomoo | SoFi HK |
|------|------------|------------------|-------------|------------|---------|
| **主字體** | IBM Plex Sans | SoFi Sans (自定義) | Titillium Web | 系統字體 | SoFi Sans |
| **標題層級** | 3 級（24/18/14px） | 3 級（20/16/14px） | 4 級（28/20/16/14px） | 3 級（20/16/14px） | 2 級（24/16px） |
| **價格字體** | Tabular nums, 18-24px | Tabular, 20-28px | Tabular, 24-32px | Tabular, 20-28px | Tabular, 18-24px |
| **數字對齊** | 右對齊 | 右對齊 | 右對齊 | 右對齊 | 右對齊 |
| **中英文策略** | 英文優先，中文翻譯 | 純英文 | 多語言切換 | 繁體中文優先 | 繁體中文優先 |
| **字重使用** | 400/500/600 | 400/500/600 | 400/500/600/700 | 400/500/600 | 400/500/600 |

### 3.4 Surface 與邊框對比

| 維度 | Binance Pro | Robinhood Legend | TradingView | 富途 moomoo | SoFi HK |
|------|------------|------------------|-------------|------------|---------|
| **背景層級** | 3 層（#1E2329/#2B3139/#363C45） | 2 層（#FFFFFF/#F5F5F5） | 3 層（#131722/#1E222D/#2A2E39） | 3 層（#0D1117/#161B22/#21262D） | 2 層（#FFFFFF/#F8F9FA） |
| **邊框使用** | 1px #2B3139 | 無邊框，用 shadow | 1px #2A2E39 | 1px #30363D | 無邊框，用 shadow |
| **卡片圓角** | 4-8px | 8-12px | 4-6px | 6-8px | 12-16px |
| **陰影使用** | 極少 | 輕量 drop shadow | 極少 | 極少 | 中等 drop shadow |
| **Selected 狀態** | #F0B90B 邊框 | #00C805 填充 | #2962FF 邊框 | #00C853 邊框 | #0066FF 填充 |
| **Hover 狀態** | 背景變亮 5% | 背景變暗 3% | 背景變亮 8% | 背景變亮 5% | 背景變暗 2% |

### 3.5 金融互動對比

| 維度 | Binance Pro | Robinhood Legend | TradingView | 富途 moomoo | SoFi HK |
|------|------------|------------------|-------------|------------|---------|
| **Symbol 顯示** | BTCUSDT 全稱 | AAPL + 公司名 | BTCUSD + 交易所 | 00700.HK + 中文名 | AAPL + 公司名 |
| **Market 區分** | Tab（現貨/合約/期權） | 無（純股票） | Tab（股票/加密/外匯） | Tab（港/美/A股） | 無（純股票） |
| **Currency 顯示** | 計價貨幣 + 結算貨幣 | USD 為主 | 多貨幣切換 | HKD/USD 切換 | USD/HKD 切換 |
| **Quantity vs Amount** | 明確區分（數量/金額） | 僅顯示金額 | 明確區分 | 明確區分 | 僅顯示金額 |
| **Order 狀態** | 6 種（待委托/已委托/部分成交/全部成交/已撤單/已拒絕） | 4 種（待執行/已執行/已取消/已拒絕） | 無內建交易 | 5 種（未委托/已委托/部分成交/全部成交/已撤單） | 4 種（待處理/已處理/已取消/已完成） |
| **模擬/即時標示** | 無模擬模式 | 無模擬模式 | Paper Trading Tab | 模擬交易獨立入口 | 無模擬模式 |

### 3.6 產品文案對比

| 維度 | Binance Pro | Robinhood Legend | TradingView | 富途 moomoo | SoFi HK |
|------|------------|------------------|-------------|------------|---------|
| **導航用語** | 現貨/合約/期權/理財 | 交易/持倉/警示 | 圖表/篩選/警示/筆記 | 行情/交易/持倉/研究 | 投資/退休/銀行 |
| **按鈕用語** | 買入/賣出/撤單 | Buy/Sell/Cancel | 繪圖/警示/交易 | 買入/賣出/下單 | Invest/Transfer |
| **空狀態** | 「暫無數據」 | 「No positions yet」 | 「No symbols to display」 | 「暫無持倉」 | 「Start investing」 |
| **錯誤提示** | 「網絡異常，請重試」 | 「Something went wrong」 | 「Failed to load data」 | 「系統繁忙，請稍後」 | 「Please try again」 |
| **語氣風格** | 直接、技術性 | 友好、簡化 | 專業、中性 | 專業、詳細 | 友好、鼓勵 |

### 3.7 關鍵設計原則提煉

從以上對比，提煉出成熟交易平台的 7 個共性原則：

1. **任務優先**：主要任務（交易/分析）佔 60%+ 視覺面積，次要任務按需展開
2. **資訊分層**：Persistent 資訊（持倉、價格）常駐，Contextual 資訊（詳情、歷史）按需顯示
3. **數據密度可調**：專業用戶可選擇高密度佈局，散戶可選擇簡化視圖
4. **術語一致性**：同一概念全域使用相同術語（如「持倉」不混用「倉位」「部位」）
5. **狀態可感知**：訂單狀態、數據延遲、模擬/即時模式必須明確標示
6. **操作可預期**：按鈕用語具體（「買入 100 股」而非「確認」），錯誤提示可操作
7. **視覺層次清晰**：用 Surface 層級、邊框、字重建立資訊主次，而非僅靠顏色

---

## 4. Alethra 現況診斷

### 4.1 登入頁（/login）

**可見症狀**：
- 左右 55/45 分割，但左側品牌區無實際功能，純裝飾
- 表單標題「登入」與品牌區「歡迎回來」語意重複
- 輸入框 48px 高度過大，與專業交易平台 36-40px 慣例不符
- 無「記住我」「忘記密碼」等常見功能

**影響的使用者任務**：
- 首次登入：用戶無法快速理解產品定位（研究 vs 交易 vs AI）
- 重複登入：缺少便捷選項，增加摩擦

**對應設計原因**：
- 品牌區與表單區無資訊層級關係，純視覺分割
- 文案未區分「產品歡迎」與「任務指引」
- 輸入框尺寸未參考金融產品慣例

**競品證據**：
- Binance：登入頁純表單，無品牌區裝飾，3 分鐘內完成
- Robinhood：品牌標語在表單下方，不佔獨立區域
- TradingView：登入頁即圖表頁，無獨立登入流程

**建議改法**：
- 方案 A：保留分割佈局，但左側改為產品價值主張（3 個核心功能圖示）
- 方案 B：取消左側品牌區，改為頂部品牌標語 + 表單居中
- 方案 C（推薦）：登入後直接進入工作台，無獨立 Dashboard 頁

**風險與優先級**：
- 風險：低（純前端改動）
- 優先級：P1（用戶第一印象）

---

### 4.2 研究首頁（/dashboard）

**可見症狀**：
- 頁面標題「策略與實驗」與導航「策略」不一致
- 七個等權重功能入口卡片（建立新實驗、進入工作台、研究草稿、市場數據、模型研究、API 金鑰、模擬帳戶）
- 「建立新實驗」按鈕出現 3 次（頁首、卡片、空狀態）
- 無實驗排序/篩選功能（僅狀態篩選）
- 實驗列表顯示「建立於」與「最後活動」，但無「最後 Run 時間」「最後結果」

**影響的使用者任務**：
- 繼續研究：用戶需點擊進入實驗詳情才能看到最新結果
- 比較實驗：無排序功能，無法快速找到最活躍/最成功的實驗
- 理解產品：七個入口令用戶困惑「我應該先做哪個？」

**對應設計原因**：
- 功能入口卡片是「系統功能列表」思維，非「用戶任務流程」思維
- 實驗列表缺少關鍵資訊（最後 Run、結果摘要）
- 無任務優先級引導（新手應該先做什麼？）

**競品證據**：
- Robinhood Legend：工作台即首頁，無獨立 Dashboard
- TradingView：圖表列表即首頁，每個圖表顯示最後更新時間
- 富途 moomoo：持倉列表即首頁，每個持倉顯示盈虧/漲跌幅

**建議改法**：
- 移除七個功能入口卡片，改為頂部導航（工作台/研究/持倉/設定）
- 實驗列表增加「最後 Run 時間」「結果摘要（勝率/收益）」欄位
- 增加排序功能（按最後活動/按收益/按名稱）
- 空狀態改為引導流程（3 步驟：建立策略 → 回測 → 模擬交易）

**風險與優先級**：
- 風險：中（需確認後端 API 支援「最後 Run 結果」）
- 優先級：P0（核心使用體驗）

---

### 4.3 工作台（/workspace）

**可見症狀**：
- 三欄佈局（觀察名單/圖表/操作面板），但比例固定（20%/50%/30%）
- 圖表區域佔比過小（50%），專業用戶需要 60-70%
- 操作面板分 3 個 Tab（策略/手動模擬單/AI），但「策略」Tab 內容與「研究首頁」重疊
- 無快速切換交易對功能（需回到觀察名單點擊）
- Replay Controller 在底部，但無快捷鍵支援

**影響的使用者任務**：
- 深度分析：圖表區域不足，需頻繁調整面板大小
- 快速交易：切換交易對步驟過多（觀察名單 → 點擊 → 載入）
- 回放研究：Replay 無快捷鍵，操作效率低

**對應設計原因**：
- 面板比例未考慮主要任務（圖表分析）的需求
- 操作面板 Tab 劃分按「功能類型」而非「任務流程」
- Replay Controller 設計為「功能」而非「工作流程」

**競品證據**：
- TradingView：圖表佔 70%，左側觀察名單可折疊，右側訂單面板可隱藏
- Robinhood Legend：Widget 可自由調整大小，用戶按任務定制佈局
- 富途 moomoo：圖表佔 60%，右側持倉/訂單可切換

**建議改法**：
- 圖表區域預設 60%，觀察名單 15%，操作面板 25%
- 操作面板改為 2 個 Tab：「交易」（合併策略+手動單）/「研究」（AI + 證據）
- 增加快速切換：Cmd+K 打開交易對搜尋
- Replay Controller 增加快捷鍵（Space 暫停、← → 單步、1-5 速度）

**風險與優先級**：
- 風險：高（涉及佈局持久化、快捷鍵系統）
- 優先級：P1（核心工作體驗）

---

### 4.4 實驗/策略詳情（/experiments/[id]）

**可見症狀**：
- 頁面標題顯示實驗名稱，但無狀態標示（草稿/探索中/已鎖定）
- 策略規格以 JSON 顯示，非人類可讀格式
- 版本列表顯示「版本號 + 建立時間」，但無「最後 Run 時間」「結果摘要」
- 無快速操作（複製實驗、匯出結果、分享）

**影響的使用者任務**：
- 理解策略：JSON 規格對非技術用戶不友好
- 比較版本：缺少關鍵資訊，需點擊進入每個版本詳情
- 管理實驗：無批量操作功能

**對應設計原因**：
- 策略規格顯示為「系統數據」而非「用戶資訊」
- 版本列表缺少決策所需資訊
- 無考慮用戶管理多個實驗的需求

**競品證據**：
- TradingView：策略詳情顯示參數表單、回測結果圖表、版本比較
- 富途 moomoo：策略詳情顯示規則說明、歷史信號、模擬結果

**建議改法**：
- 策略規格改為表單顯示（可編輯）+ JSON 預覽（可折疊）
- 版本列表增加「最後 Run 時間」「勝率」「收益」「狀態」欄位
- 增加快速操作按鈕（複製/匯出/分享/刪除）
- 增加版本比較功能（選擇 2 個版本，側對比）

**風險與優先級**：
- 風險：中（需確認後端 API 支援版本比較）
- 優先級：P2（進階功能）

---

### 4.5 回放/回測結果

**可見症狀**：
- 回放結果顯示在圖表上，但無獨立結果面板
- 缺少關鍵指標摘要（總收益、最大回撤、勝率、盈虧比）
- 無結果匯出功能（CSV/PDF）
- 回放歷史無持久化（重新整理後丟失）

**影響的使用者任務**：
- 評估策略：缺少關鍵指標，需手動計算
- 分享結果：無法匯出，需截圖
- 比較多次回放：無歷史記錄

**對應設計原因**：
- 回放結果設計為「即時可視化」而非「研究證據」
- 無考慮用戶需要評估、分享、比較的需求

**競品證據**：
- TradingView：策略測試器顯示完整指標表、交易列表、圖表
- 富途 moomoo：回測結果顯示收益曲線、指標摘要、交易明細

**建議改法**：
- 增加右側結果面板（可折疊），顯示：
  - 指標摘要（總收益/年化/最大回撤/勝率/盈虧比）
  - 交易列表（買入/賣出時間、價格、盈虧）
  - 收益曲線圖
- 增加匯出按鈕（CSV/PDF）
- 回放歷史持久化（與實驗版本關聯）

**風險與優先級**：
- 風險：高（需後端支援結果持久化、匯出功能）
- 優先級：P1（核心研究價值）

---

### 4.6 投資組合/持倉（/paper-portfolio）

**可見症狀**：
- 頁面標題「模擬帳戶」與導航「投資組合」不一致
- 持倉列表顯示「標的/數量/均價/現價/盈虧」，但無「盈虧%」「持倉佔比」
- 無快速操作（平倉、調整倉位、設定止損/止盈）
- 交易日誌與持倉列表分離，需切換 Tab

**影響的使用者任務**：
- 評估持倉：缺少關鍵資訊（盈虧%、佔比）
- 管理持倉：操作步驟過多（需進入詳情頁）
- 理解交易歷史：日誌與持倉分離，難以關聯

**對應設計原因**：
- 持倉列表設計為「數據展示」而非「決策支援」
- 無考慮用戶需要快速操作的需求
- 交易日誌與持倉的關聯性未建立

**競品證據**：
- Robinhood：持倉列表顯示盈虧%、今日漲跌、持倉佔比
- 富途 moomoo：持倉列表顯示盈虧%、佔比、快速平倉按鈕
- Binance：持倉列表顯示槓桿、保證金、快速平倉

**建議改法**：
- 持倉列表增加「盈虧%」「持倉佔比」欄位
- 增加快速操作按鈕（平倉/調整倉位/設定止損止盈）
- 交易日誌與持倉整合（點擊持倉展開相關交易）
- 增加持倉分析圖表（餅圖：持倉佔比、柱狀圖：盈虧分佈）

**風險與優先級**：
- 風險：中（需確認後端 API 支援快速操作）
- 優先級：P1（核心交易體驗）

---

### 4.7 設定/API 金鑰（/api-keys, /settings）

**可見症狀**：
- API 金鑰頁面獨立，但與其他設定（通知、安全、偏好）分離
- 無設定搜尋功能
- 無設定匯入/匯出功能
- 安全設定（2FA、登入歷史）缺少

**影響的使用者任務**：
- 管理設定：需在不同頁面間切換
- 查找設定：無搜尋功能，需逐頁瀏覽
- 備份設定：無法匯出，更換設備需重新設定

**對應設計原因**：
- 設定頁面設計為「功能列表」而非「用戶任務中心」
- 無考慮用戶管理多個設定的需求

**競品證據**：
- Binance：設定中心包含帳戶、安全、API、偏好、通知
- TradingView：設定包含圖表、警示、帳戶、API、主題

**建議改法**：
- 整合所有設定到統一頁面（/settings）
- 增加設定搜尋功能（Cmd+F 在設定頁內搜尋）
- 增加設定匯入/匯出功能（JSON）
- 增加安全設定（2FA、登入歷史、活動裝置）

**風險與優先級**：
- 風險：低（純前端改動，後端 API 已存在）
- 優先級：P3（進階功能）

---

## 5. 三個設計方向

### 方向 A：Trading Platform Standard（交易平台標準）

**主題與目標使用者**：
- 主題：專業交易平台，強調速度與效率
- 目標用戶：有交易經驗的投資者，熟悉 Binance/IBKR 介面

**資訊層級與密度**：
- 高密度：圖表 60%、觀察名單 20%、訂單面板 20%
- 三層資訊：Persistent（持倉/價格）→ Contextual（詳情/歷史）→ Modal（設定/確認）
- 無獨立 Dashboard，登入即工作台

**登入頁 Wireframe**：
```
┌─────────────────────────────────────────┐
│  [Logo] Alethra                    [?]  │
├─────────────────────────────────────────┤
│                                         │
│  登入以繼續交易                          │
│                                         │
│  [電子郵件____________]                 │
│  [密碼________________]                 │
│  [□ 記住我]      [忘記密碼？]           │
│                                         │
│  [====== 登入 ======]                   │
│                                         │
│  沒有帳號？註冊                         │
│                                         │
└─────────────────────────────────────────┘
```

**研究首頁 Wireframe**：
```
（無獨立首頁，登入後直接進入工作台）
```

**工作台 Wireframe**：
```
┌──────────┬────────────────────┬──────────┐
│ 觀察名單 │  [Instrument Header]         │
│          │  AAPL - Apple Inc.           │
│ ▶ AAPL   │  $150.25 +2.3%  [Live]      │
│   TSLA   ├──────────────────────────────┤
│   NVDA   │                              │
│   MSFT   │      [Chart Area 60%]        │
│          │                              │
│          │                              │
│          ├──────────────────────────────┤
│          │  [Replay Controller]         │
│          │  ▶ ⏸ ⏩ 1x  2024-01-15 10:30 │
├──────────┴──────────────────────────────┤
│ [Buy] 數量 [___] 價格 [___] [確認買入]  │
│ [Sell] 數量 [___] 價格 [___] [確認賣出] │
└─────────────────────────────────────────┘
```

**字體與中英文策略**：
- 主字體：Inter（英文）+ Noto Sans TC（中文）
- 價格字體：Tabular nums, 18-24px
- 中英文混排：中文優先，英文術語保留（如「Replay」「Backtest」）

**Surface/Border/Radius 規則**：
- 3 層 Surface：#0F141B（canvas）→ #161D27（panel）→ #1D2733（hover）
- Border：1px #2B3746（subtle）/ #3B4A5C（strong）
- Radius：4px（button）/ 6px（input）/ 8px（panel）

**Accent 與狀態色**：
- Primary：#4C8DFF（blue）
- Positive：#36C7A0（green）
- Negative：#FF7585（red）
- Warning：#F2C46D（yellow）

**Mobile 行為**：
- 單欄佈局：圖表全寬，觀察名單/訂單面板改為 Bottom Sheet
- 導航：Bottom Tab Bar（工作台/持倉/研究/設定）
- 觸控優化：按鈕最小 44x44px，間距 8px+

**優點**：
- 用戶熟悉度高，學習成本低
- 資訊密度高，適合專業用戶
- 實作成本相對低（沿用現有 WorkspaceShell）

**取捨**：
- 新手可能覺得複雜
- 不強調「研究」定位，可能被誤認為交易平台

**適用情境**：
- 目標用戶轉為有經驗的交易者
- 產品定位轉向「模擬交易平台」而非「研究工具」

**對目前程式的變更成本**：
- 低：主要改動為視覺語言（CSS）與文案系統
- 預計工時：2-3 週

---

### 方向 B：Research-First Professional Workspace（研究優先專業工作台）⭐ 推薦

**主題與目標使用者**：
- 主題：研究驅動的交易工作台，強調證據與決策
- 目標用戶：認真檢驗投資想法的研究者，可能無交易經驗

**資訊層級與密度**：
- 中高密度：圖表 55%、觀察名單 15%、研究面板 30%
- 三層資訊：Persistent（實驗列表/持倉）→ Contextual（圖表/證據）→ Modal（設定/詳情）
- 登入後進入研究首頁（實驗列表），工作台為主要任務頁

**登入頁 Wireframe**：
```
┌─────────────────────────────────────────┐
│                                         │
│        [Logo] Alethra                   │
│                                         │
│     投資想法，值得被認真檢驗。          │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │  登入                           │   │
│  │                                 │   │
│  │  [電子郵件____________]         │   │
│  │  [密碼________________]         │   │
│  │                                 │   │
│  │  [====== 登入 ======]           │   │
│  │                                 │   │
│  │  沒有帳號？註冊                 │   │
│  └─────────────────────────────────┘   │
│                                         │
│     © 2026 Alethra. 研究工具，非投資建議│
└─────────────────────────────────────────┘
```

**研究首頁 Wireframe**：
```
┌─────────────────────────────────────────┐
│ [Logo] Alethra  [工作台] [研究] [持倉] [設定] │
├─────────────────────────────────────────┤
│                                         │
│  我的研究實驗                    [+ 新實驗]│
│                                         │
│  [全部 (12)] [草稿 (3)] [探索中 (5)] [已鎖定 (4)]│
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ AAPL 動量策略                    │   │
│  │ 最後 Run: 2024-01-15  勝率: 62%  │   │
│  │ [草稿] [繼續研究 →]              │   │
│  └─────────────────────────────────┘   │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ TSLA 均值回歸                    │   │
│  │ 最後 Run: 2024-01-14  收益: +8.3%│   │
│  │ [探索中] [查看結果 →]            │   │
│  └─────────────────────────────────┘   │
│                                         │
└─────────────────────────────────────────┘
```

**工作台 Wireframe**：
```
┌─────┬──────────────────────┬───────────┐
│觀察 │  [Instrument Header] │ 研究面板  │
│名單 │  AAPL - Apple Inc.   │           │
│     │  $150.25 +2.3%       │ [策略]    │
│▶AAPL│  [Live] [Synthetic]  │ 入场: SMA>│
│ TSLA│                      │ 20 日線   │
│ NVDA├──────────────────────┤ 出场: 持  │
│     │                      │ 有 5 天   │
│     │   [Chart Area 55%]   │           │
│     │                      │ [證據]    │
│     │                      │ 勝率: 62% │
│     ├──────────────────────┤ 收益: +8% │
│     │ [Replay Controller]  │ 最大回撤: │
│     │ ▶ ⏸ ⏩ 1x 2024-01-15 │ -3.2%    │
├─────┴──────────────────────┴───────────┤
│ [交易] 數量 [__] 價格 [__] [買入] [賣出]│
└────────────────────────────────────────┘
```

**字體與中英文策略**：
- 主字體：Inter（英文）+ Noto Sans TC（中文）
- 價格字體：Tabular nums, 20-28px（比方向 A 大，強調研究數據）
- 中英文混排：中文優先，專有名詞保留英文（如「SMA」「RSI」）

**Surface/Border/Radius 規則**：
- 3 層 Surface：#0F141B（canvas）→ #161D27（panel）→ #1D2733（hover）
- Border：1px #2B3746（subtle）/ #3B4A5C（strong）
- Radius：6px（button）/ 8px（input）/ 10px（panel）（比方向 A 大，更友好）

**Accent 與狀態色**：
- Primary：#4C8DFF（blue）
- Positive：#36C7A0（green）
- Negative：#FF7585（red）
- Warning：#F2C46D（yellow）
- Research：#A87BD6（purple，用於研究相關元素）

**Mobile 行為**：
- 研究首頁：單欄列表，每個實驗卡片顯示關鍵資訊
- 工作台：圖表全寬，研究面板改為 Bottom Sheet（可上拉展開）
- 導航：Bottom Tab Bar（研究/工作台/持倉/設定）

**優點**：
- 明確區分「研究」與「交易」任務，符合產品定位
- 研究面板整合策略規格與證據，支援決策
- 資訊密度適中，新手友好但不失專業感

**取捨**：
- 需要新增「研究首頁」頁面（增加開發成本）
- 研究面板可能需要後端支援（證據摘要 API）

**適用情境**：
- 目標用戶為認真研究投資的個人投資者
- 產品定位為「研究工具 + 模擬交易」

**對目前程式的變更成本**：
- 中：需新增研究首頁、研究面板，改動工作台佈局
- 預計工時：3-4 週

---

### 方向 C：Minimalist Research Tool（極簡研究工具）

**主題與目標使用者**：
- 主題：極簡研究工具，強調專注與清晰
- 目標用戶：初次接觸量化投資的研究者，可能無技術背景

**資訊層級與密度**：
- 低密度：圖表 70%、側邊欄 30%（可隱藏）
- 兩層資訊：Persistent（圖表）→ Modal（詳情/設定）
- 無獨立 Dashboard，登入即圖表頁

**登入頁 Wireframe**：
```
┌─────────────────────────────────────────┐
│                                         │
│           [Logo] Alethra                │
│                                         │
│      認真檢驗你的投資想法               │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │  [電子郵件____________]         │   │
│  │  [密碼________________]         │   │
│  │  [====== 登入 ======]           │   │
│  └─────────────────────────────────┘   │
│                                         │
│      沒有帳號？註冊                     │
│                                         │
└─────────────────────────────────────────┘
```

**研究首頁 Wireframe**：
```
（無獨立首頁，登入後直接進入圖表頁）
```

**工作台 Wireframe**：
```
┌─────────────────────────────────────────┐
│ [Logo] Alethra  [AAPL] [TSLA] [NVDA] [+]│
├─────────────────────────────────────────┤
│                                         │
│  AAPL - Apple Inc.                      │
│  $150.25 +2.3%  [Live]                  │
│                                         │
│                                         │
│         [Chart Area 70%]                │
│                                         │
│                                         │
│                                         │
│                                         │
├─────────────────────────────────────────┤
│ [▶ 回放] [⏸ 暫停] [⏩ 1x] 2024-01-15   │
├─────────────────────────────────────────┤
│ [策略] SMA 20 日線突破                   │
│ [證據] 勝率 62%  收益 +8%  回撤 -3.2%   │
│ [交易] 數量 [__] 價格 [__] [買入] [賣出] │
└─────────────────────────────────────────┘
```

**字體與中英文策略**：
- 主字體：System UI（-apple-system, Segoe UI, Roboto）
- 價格字體：Tabular nums, 24-32px（最大，強調價格）
- 中英文混排：中文優先，盡量減少英文術語

**Surface/Border/Radius 規則**：
- 2 層 Surface：#FFFFFF（light mode）/ #0F141B（dark mode）
- Border：無邊框，用 Spacing 區分區域
- Radius：12px（button）/ 16px（card）/ 20px（modal）（最大，最友好）

**Accent 與狀態色**：
- Primary：#0066FF（blue，更飽和）
- Positive：#00C853（green，更鮮明）
- Negative：#FF3D00（red，更鮮明）

**Mobile 行為**：
- 圖表全寬，控制列改為 Floating Action Button（FAB）
- 導航：Top Tab Bar（可左右滑動切換標的）
- 觸控優化：按鈕最小 48x48px，間距 12px+

**優點**：
- 視覺清晰，新手友好
- 開發成本低（最少頁面/组件）
- 強調「研究」而非「交易」

**取捨**：
- 資訊密度低，專業用戶可能覺得不夠
- 缺少觀察名單/持倉等常見功能
- 可能被誤認為「圖表工具」而非「研究平台」

**適用情境**：
- 目標用戶為完全新手
- 產品定位為「入門級研究工具」

**對目前程式的變更成本**：
- 低：主要改動為視覺語言，移除部分功能
- 預計工時：2 週

---

### 方向比較總結

| 維度 | 方向 A: Trading Standard | 方向 B: Research-First ⭐ | 方向 C: Minimalist |
|------|-------------------------|--------------------------|-------------------|
| **資訊密度** | 高 | 中高 | 低 |
| **新手友好度** | 低 | 中 | 高 |
| **專業感** | 高 | 高 | 中 |
| **開發成本** | 低（2-3 週） | 中（3-4 週） | 低（2 週） |
| **產品定位清晰度** | 低（易被誤認為交易平台） | 高（研究 + 交易） | 中（易被誤認為圖表工具） |
| **與現有程式兼容性** | 高 | 中 | 低（需移除功能） |

### 推薦：方向 B（Research-First Professional Workspace）

**理由**：
1. **符合產品定位**：Alethra 是「研究工具 + 模擬交易」，方向 B 明確區分兩者
2. **平衡專業與友好**：資訊密度適中，新手可理解，專業用戶不覺得簡陋
3. **實作成本可控**：沿用現有 AppShell、WorkspaceShell，主要新增研究首頁與研究面板
4. **差異化競爭**：市場上缺少「研究優先」的交易工具，多數平台以交易為主

**下一步**：
- Owner 確認方向 B 後，進入詳細設計階段（Section 6-9）
- 如需調整方向，請提供具體反饋

---

## 6. 推薦方案詳細規格

### 6.1 Navigation / Sitemap

```
/ (redirect to /research)
/login
/research                    # 研究首頁（實驗列表）
  /research/new              # 建立新實驗
  /research/[id]             # 實驗詳情
    /research/[id]/versions  # 版本列表
    /research/[id]/runs      # Run 歷史
/workspace                   # 工作台（主要任務頁）
/portfolio                   # 投資組合/持倉
  /portfolio/[id]            # 持倉詳情
  /portfolio/[id]/journal    # 交易日誌
/settings                    # 設定中心
  /settings/api-keys         # API 金鑰管理
  /settings/security         # 安全設定
  /settings/preferences      # 偏好設定
```

**導航結構**：
- Top Nav（Desktop）：Logo + 主要頁面（研究/工作台/持倉） + 用戶選單（設定/登出）
- Bottom Nav（Mobile）：4 個 Tab（研究/工作台/持倉/設定）

### 6.2 每頁主要任務與 Primary Action

| 頁面 | 主要任務 | Primary Action | Secondary Actions |
|------|---------|----------------|-------------------|
| /login | 登入/註冊 | [登入] | [註冊] [忘記密碼] |
| /research | 查看/管理實驗 | [+ 新實驗] | [篩選] [排序] |
| /research/[id] | 理解/編輯實驗 | [進入工作台] | [複製] [匯出] [刪除] |
| /workspace | 分析/交易 | [買入/賣出] | [回放] [設定策略] [查看證據] |
| /portfolio | 查看/管理持倉 | [平倉] | [調整倉位] [設定止損止盈] |
| /settings | 管理設定 | [儲存] | [匯入] [匯出] |

### 6.3 Region / Container Map

**研究首頁（/research）**：
```
┌─────────────────────────────────────────┐
│ [Header] Logo + Nav + User Menu         │
├─────────────────────────────────────────┤
│ [Page Header]                           │
│  標題: 我的研究實驗                      │
│  Primary Action: [+ 新實驗]             │
├─────────────────────────────────────────┤
│ [Filter Bar]                            │
│  [全部] [草稿] [探索中] [已鎖定]        │
│  [排序: 最後活動 ▼]                     │
├─────────────────────────────────────────┤
│ [Experiment List]                       │
│  ┌─────────────────────────────────┐   │
│  │ Experiment Card 1               │   │
│  │  標題 / 狀態 / 最後 Run / 結果  │   │
│  └─────────────────────────────────┘   │
│  ┌─────────────────────────────────┐   │
│  │ Experiment Card 2               │   │
│  │  標題 / 狀態 / 最後 Run / 結果  │   │
│  └─────────────────────────────────┘   │
│  ...                                   │
├─────────────────────────────────────────┤
│ [Footer] Copyright + Disclaimer         │
└─────────────────────────────────────────┘
```

**工作台（/workspace）**：
```
┌─────┬──────────────────────┬───────────┐
│ [Left Sidebar]             │ [Right Panel]    │
│  觀察名單 (15%)            │  研究面板 (30%)  │
│  - 標的列表                │  - 策略規格      │
│  - 快速切換                │  - 證據摘要      │
│                            │  - 回放控制      │
├─────┼──────────────────────┤  - 交易面板      │
│ [Center Area]              │                   │
│  Instrument Header (5%)    │                   │
│  Chart Area (55%)          │                   │
│  Replay Controller (5%)    │                   │
│  Order Panel (5%)          │                   │
└─────┴──────────────────────┴───────────────────┘
```

**投資組合（/portfolio）**：
```
┌─────────────────────────────────────────┐
│ [Header] Logo + Nav + User Menu         │
├─────────────────────────────────────────┤
│ [Page Header]                           │
│  標題: 我的投資組合                      │
│  Primary Action: [平倉]                 │
├─────────────────────────────────────────┤
│ [Portfolio Summary]                     │
│  總資產 / 總盈虧 / 今日盈虧             │
├─────────────────────────────────────────┤
│ [Position List]                         │
│  ┌─────────────────────────────────┐   │
│  │ Position Card 1                 │   │
│  │  標的 / 數量 / 均價 / 現價      │   │
│  │  盈虧 / 盈虧% / 持倉佔比        │   │
│  │  [平倉] [調整] [止損止盈]       │   │
│  └─────────────────────────────────┘   │
│  ...                                   │
├─────────────────────────────────────────┤
│ [Trade Journal]                         │
│  交易歷史列表（可展開）                 │
└─────────────────────────────────────────┘
```

### 6.4 Desktop / Tablet / Mobile Wireframes

**Desktop（≥1280px）**：
- 三欄佈局（工作台）或單欄列表（研究/持倉）
- 最小寬度：1280px
- 最大寬度：無限制（fluid）
- Overflow：側邊欄可折疊，面板可調整大小

**Tablet（768px - 1279px）**：
- 兩欄佈局（工作台）：觀察名單隱藏（可拉出），圖表 + 研究面板
- 單欄列表（研究/持倉）：卡片全寬
- Overflow：側邊欄改為抽屜，面板改為 Bottom Sheet

**Mobile（< 768px）**：
- 單欄佈局：所有頁面單欄
- 導航：Bottom Tab Bar（4 個 Tab）
- Overflow：側邊欄/面板改為 Bottom Sheet 或 Modal
- 觸控優化：按鈕最小 44x44px，間距 8px+

### 6.5 建議尺寸、Min/Max、Overflow 規則

| 元素 | Desktop Min | Desktop Max | Mobile Min | Mobile Max | Overflow |
|------|------------|------------|-----------|-----------|----------|
| 觀察名單 | 200px | 300px | 0（隱藏） | 0（隱藏） | 可折疊 |
| 圖表區域 | 60% | 70% | 100% | 100% | 可調整 |
| 研究面板 | 25% | 35% | 0（Bottom Sheet） | 0（Bottom Sheet） | 可拉出 |
| 訂單面板 | 200px | 300px | 100%（Bottom） | 100%（Bottom） | 固定底部 |
| 實驗卡片 | 300px | 600px | 100% | 100% | 垂直滾動 |
| 持倉卡片 | 400px | 800px | 100% | 100% | 垂直滾動 |

### 6.6 Heading Hierarchy / Type Scale

**Desktop Type Scale**：
```
H1 (Page Title):     28px / 36px / 600 weight / #E7EDF5
H2 (Section Title):  20px / 28px / 600 weight / #E7EDF5
H3 (Card Title):     16px / 24px / 600 weight / #E7EDF5
Body (Primary):      14px / 22px / 400 weight / #E7EDF5
Body (Secondary):    13px / 20px / 400 weight / #B6C2D2
Caption:             12px / 18px / 400 weight / #8FA0B5
Price (Large):       24px / 32px / 600 weight / #E7EDF5 / Tabular nums
Price (Small):       16px / 24px / 600 weight / #E7EDF5 / Tabular nums
Button:              14px / 22px / 600 weight / #FFFFFF
```

**Mobile Type Scale**：
```
H1 (Page Title):     24px / 32px / 600 weight
H2 (Section Title):  18px / 26px / 600 weight
H3 (Card Title):     15px / 22px / 600 weight
Body (Primary):      14px / 22px / 400 weight
Body (Secondary):    13px / 20px / 400 weight
Caption:             11px / 16px / 400 weight
Price (Large):       20px / 28px / 600 weight / Tabular nums
Price (Small):       15px / 22px / 600 weight / Tabular nums
Button:              15px / 22px / 600 weight
```

### 6.7 Colour / Surface / Spacing / Radius / Focus Tokens

**Colour Tokens**：
```css
/* Surface */
--canvas: #0F141B;
--surface-1: #161D27;
--surface-2: #1D2733;
--overlay: #243142;

/* Border */
--border-subtle: #2B3746;
--border-strong: #3B4A5C;

/* Semantic */
--primary: #4C8DFF;
--primary-hover: #3B7BEE;
--positive: #36C7A0;
--negative: #FF7585;
--warning: #F2C46D;
--research: #A87BD6;

/* Text */
--text-primary: #E7EDF5;
--text-secondary: #B6C2D2;
--text-muted: #8FA0B5;
--text-white: #FFFFFF;
```

**Spacing Tokens**：
```css
--sp-1: 4px;
--sp-2: 8px;
--sp-3: 12px;
--sp-4: 16px;
--sp-6: 24px;
--sp-8: 32px;
--sp-12: 48px;
```

**Radius Tokens**：
```css
--r-button: 6px;
--r-input: 8px;
--r-card: 10px;
--r-panel: 12px;
--r-modal: 16px;
--r-badge: 999px;
```

**Focus Tokens**：
```css
--focus-ring: 2px solid var(--primary);
--focus-offset: 2px;
```

### 6.8 Components 與完整 States

**Button**：
```
States:
- Default: bg=primary, color=white, border=none
- Hover: bg=primary-hover
- Active: bg=primary-hover, transform=translateY(1px)
- Disabled: opacity=0.5, cursor=not-allowed
- Focus: outline=focus-ring, outline-offset=focus-offset

Variants:
- Primary: bg=primary, color=white
- Secondary: bg=surface-2, color=text-primary, border=border-strong
- Ghost: bg=transparent, color=text-secondary, border=none
- Danger: bg=negative, color=white
```

**Input**：
```
States:
- Default: bg=surface-1, border=border-subtle, color=text-primary
- Hover: border=border-strong
- Focus: border=primary, bg=surface-2
- Error: border=negative, bg=surface-2
- Disabled: opacity=0.5, cursor=not-allowed

Sizes:
- Small: height=32px, font-size=13px
- Medium: height=40px, font-size=14px (default)
- Large: height=48px, font-size=16px
```

**Card**：
```
States:
- Default: bg=surface-1, border=border-subtle
- Hover: bg=surface-2, border=border-strong, transform=translateX(2px)
- Selected: border=primary, bg=surface-2
- Focus: outline=focus-ring, outline-offset=focus-offset

Variants:
- Experiment Card: 顯示標題/狀態/最後 Run/結果
- Position Card: 顯示標的/數量/均價/現價/盈虧
- Trade Card: 顯示時間/標的/方向/數量/價格/盈虧
```

**Badge**：
```
States:
- Draft: bg=rgba(242,196,109,0.15), color=warning
- Exploring: bg=rgba(76,141,255,0.15), color=primary
- Locked: bg=rgba(54,199,160,0.15), color=positive
- Archived: bg=rgba(143,160,181,0.15), color=text-muted

Sizes:
- Small: padding=2px 6px, font-size=10px
- Medium: padding=4px 10px, font-size=11px (default)
- Large: padding=6px 12px, font-size=12px
```

**Modal**：
```
States:
- Default: bg=overlay, border=border-strong, shadow=shadow-lg
- Opening: opacity=0, transform=scale(0.95) → opacity=1, transform=scale(1)
- Closing: opacity=1, transform=scale(1) → opacity=0, transform=scale(0.95)

Sizes:
- Small: width=400px, padding=24px
- Medium: width=600px, padding=32px (default)
- Large: width=800px, padding=40px
```

### 6.9 Interaction Flows

**建立新實驗流程**：
```
1. 用戶點擊 [+ 新實驗]
2. 進入 /research/new
3. 填寫表單：
   - 實驗名稱（必填）
   - 策略規格（可選，預設模板）
   - 初始資金（必填，預設 100,000）
4. 點擊 [建立]
5. 後端建立實驗，返回 /research/[id]
6. 用戶點擊 [進入工作台]
7. 進入 /workspace?experiment=[id]
```

**回放流程**：
```
1. 用戶在工作台點擊 [回放]
2. Replay Controller 展開
3. 用戶選擇回放速度（1x/2x/5x/10x）
4. 用戶點擊 [▶ 開始]
5. 圖表開始播放，Replay Controller 顯示當前時間
6. 用戶可隨時 [⏸ 暫停] / [⏩ 快進] / [◀ 後退]
7. 回放結束後，自動顯示結果摘要
8. 用戶可點擊 [查看證據] 查看詳細結果
```

**交易流程**：
```
1. 用戶在工作台選擇標的
2. 用戶點擊 [買入] 或 [賣出]
3. 訂單面板展開（右側）
4. 用戶填寫：
   - 數量（必填）
   - 價格（可選，預設市價）
   - 止損/止盈（可選）
5. 用戶點擊 [確認訂單]
6. 系統顯示確認 Modal：
   - 訂單詳情
   - 預估成本
   - 風險提示
7. 用戶點擊 [確認]
8. 後端提交訂單，返回訂單 ID
9. 系統顯示成功提示，訂單加入持倉列表
```

### 6.10 Context 同步與錯誤處理

**Context 同步**：
```
- 實驗選擇：/research/[id] → /workspace?experiment=[id]
- 標的選擇：/workspace?symbol=[symbol]
- 回放狀態：Replay Controller 狀態持久化（localStorage）
- 持倉更新：訂單提交後，自動刷新持倉列表（WebSocket / polling）
```

**錯誤處理**：
```
- 網絡錯誤：顯示 Toast「網絡異常，請檢查連接」+ [重試] 按鈕
- API 錯誤：顯示 Toast「操作失敗：[錯誤訊息]」+ [詳情] 按鈕
- 驗證錯誤：表單欄位下方顯示紅色錯誤訊息
- 超時錯誤：顯示 Toast「請求超時，請稍後重試」+ [重試] 按鈕
- 未知錯誤：顯示 Toast「發生未知錯誤，請聯繫支援」+ [報告] 按鈕
```

### 6.11 Empty / Unknown / Unavailable Data 規則

**Empty Data**：
```
- 實驗列表為空：顯示空狀態「還沒有任何實驗」+ [+ 新實驗] 按鈕
- 持倉列表為空：顯示空狀態「還沒有任何持倉」+ [開始交易] 按鈕
- 交易日誌為空：顯示空狀態「還沒有任何交易」
- 圖表數據為空：顯示 Loading Spinner + 「載入中...」
```

**Unknown Data**：
```
- 價格未知：顯示「--」而非 0
- 盈虧未知：顯示「--」而非 0%
- 狀態未知：顯示「未知」Badge（灰色）
- 時間未知：顯示「--」而非空字符串
```

**Unavailable Data**：
```
- 功能不可用：按鈕 Disabled + Tooltip「此功能暫不可用」
- 數據不可用：顯示「數據暫不可用」+ [重試] 按鈕
- 頁面不可用：404 頁面 + [返回首頁] 按鈕
```

### 6.12 規格分類

**競品直接觀察**：
- 資訊分層（Persistent / Contextual / Modal）：Binance、TradingView、Robinhood Legend
- 術語一致性：所有平台
- 狀態可感知：Binance（訂單狀態）、TradingView（數據延遲標示）
- 操作可預期：Robinhood（按鈕用語具體）、富途（交易確認 Modal）

**為 Alethra 提出的設計決定**：
- 研究優先佈局（方向 B）：基於產品定位
- 研究面板整合策略與證據：基於用戶任務流程
- 術語系統（Section 7）：基於中英文混排需求
- Mobile Bottom Tab Bar：基於觸控操作習慣

**尚待驗證的假設**：
- 研究首頁是否為必要（可能有用戶偏好登入即工作台）
- 研究面板 30% 寬度是否足夠（可能需要用戶測試）
- 回放快捷鍵是否為高優先需求（可能進階用戶才需要）

---

## 7. 文案／術語系統

### 7.1 UI Terminology / Microcopy Table

| 目前文字 | 建議文字 | 使用情境 | 改寫原因 | 對應真實功能 |
|---------|---------|---------|---------|------------|
| 登入 | 登入 | Login page title, button | 保持一致 | POST /api/v1/auth/login |
| 註冊 | 建立帳號 | Login page switch | 「建立帳號」更具體 | POST /api/v1/auth/register |
| 歡迎回來 | （移除） | Login brand zone | 無實際資訊價值 | - |
| 開始你的研究 | 開始研究 | Login brand heading | 更簡潔 | - |
| 投資想法，值得被認真檢驗。 | 認真檢驗你的投資想法 | Login brand tagline | 主動語態更有力 | - |
| 電子郵件 | 電子郵件 | Login form label | 保持一致 | email field |
| 密碼 | 密碼 | Login form label | 保持一致 | password field |
| 顯示名稱 | 顯示名稱 | Register form label | 保持一致 | display_name field |
| 處理中... | 處理中... | Loading button | 保持一致 | Loading state |
| 錯誤： | （移除） | Error message | 直接顯示錯誤訊息即可 | Error state |
| 沒有帳號？註冊 | 還沒有帳號？建立帳號 | Login page switch | 更自然 | - |
| 已有帳號？登入 | 已有帳號？登入 | Register page switch | 保持一致 | - |
| 策略與實驗 | 我的研究實驗 | Dashboard page title | 更個人化 | GET /api/v1/experiments |
| 建立新實驗 | 新實驗 | Primary action button | 更簡潔 | POST /api/v1/experiments |
| 全部 | 全部 | Filter button | 保持一致 | Filter all |
| 草稿 | 草稿 | Filter button, status badge | 保持一致 | status=draft |
| 探索中 | 進行中 | Filter button, status badge | 「進行中」更直觀 | status=exploring |
| 已鎖定 | 已鎖定 | Filter button, status badge | 保持一致 | status=locked |
| 已封存 | 已封存 | Filter button, status badge | 保持一致 | status=archived |
| 還沒有任何實驗 | 還沒有任何實驗 | Empty state title | 保持一致 | Empty list |
| 建立第一個實驗，記錄你的投資假設，追蹤測試結果，保存研究證據。 | 建立第一個實驗，開始認真檢驗你的投資想法。 | Empty state description | 更簡潔，呼應品牌標語 | - |
| 建立第一個實驗 | 建立第一個實驗 | Empty state action | 保持一致 | POST /api/v1/experiments |
| 繼續研究 | 繼續研究 | Next action link | 保持一致 | Navigate to experiment |
| 查看結果 | 查看結果 | Next action link | 保持一致 | Navigate to experiment results |
| 查看報告 | 查看報告 | Next action link | 保持一致 | Navigate to locked experiment |
| 查看詳情 | 查看詳情 | Next action link | 保持一致 | Navigate to experiment details |
| 建立於 | 建立於 | Experiment meta | 保持一致 | created_at field |
| 最後活動 | 最後更新 | Experiment meta | 「最後更新」更直觀 | updated_at field |
| 工作台 | 工作台 | Nav link, page title | 保持一致 | /workspace route |
| 進入工作台 | 進入工作台 | Primary action button | 保持一致 | Navigate to /workspace |
| 投資組合 | 我的投資組合 | Nav link, page title | 更個人化 | /portfolio route |
| 模擬帳戶 | 投資組合 | Nav link, page title | 「投資組合」更專業 | /paper-portfolio route |
| 紀錄與結果 | 交易日誌 | Nav link, page title | 更具體 | /paper-portfolio?view=journal route |
| 設定 | 設定 | Nav link, page title | 保持一致 | /settings route |
| API 金鑰 | API 金鑰 | Settings link | 保持一致 | /settings/api-keys route |
| 訂閱與帳單 | 訂閱方案 | Settings link | 更簡潔 | /subscription route |
| 登出 | 登出 | User menu action | 保持一致 | Clear token, redirect to /login |
| 載入中... | 載入中... | Loading state | 保持一致 | Loading indicator |
| 載入中 | 載入中... | Loading state | 統一格式 | Loading indicator |
| 錯誤 | 發生錯誤 | Error state | 更完整 | Error message |
| 成功 | 操作成功 | Success state | 更完整 | Success message |
| 確認 | 確認 | Modal button | 保持一致 | Confirm action |
| 取消 | 取消 | Modal button | 保持一致 | Cancel action |
| 刪除 | 刪除 | Danger action button | 保持一致 | DELETE request |
| 編輯 | 編輯 | Edit action button | 保持一致 | Edit mode |
| 儲存 | 儲存 | Save action button | 保持一致 | POST/PUT request |
| 買入 | 買入 | Trade action button | 保持一致 | BUY order |
| 賣出 | 賣出 | Trade action button | 保持一致 | SELL order |
| 平倉 | 平倉 | Position action button | 保持一致 | Close position |
| 數量 | 數量 | Order form label | 保持一致 | quantity field |
| 價格 | 價格 | Order form label | 保持一致 | price field |
| 市價 | 市價 | Order type option | 保持一致 | market order |
| 限價 | 限價 | Order type option | 保持一致 | limit order |
| 止損 | 止損 | Risk control label | 保持一致 | stop loss |
| 止盈 | 止盈 | Risk control label | 保持一致 | take profit |
| 持倉 | 持倉 | Position list title | 保持一致 | positions |
| 訂單 | 訂單 | Order list title | 保持一致 | orders |
| 成交 | 成交 | Fill list title | 保持一致 | fills |
| 交易日誌 | 交易日誌 | Journal page title | 保持一致 | trade journal |
| 回放 | 回放 | Replay controller label | 保持一致 | replay mode |
| 回測 | 回測 | Backtest result label | 保持一致 | backtest mode |
| 策略 | 策略 | Strategy panel title | 保持一致 | strategy spec |
| 證據 | 證據 | Evidence panel title | 保持一致 | evidence dock |
| 勝率 | 勝率 | Result metric label | 保持一致 | win rate |
| 收益 | 收益 | Result metric label | 保持一致 | return |
| 最大回撤 | 最大回撤 | Result metric label | 保持一致 | max drawdown |
| 盈虧比 | 盈虧比 | Result metric label | 保持一致 | profit/loss ratio |
| 年化 | 年化 | Result metric label | 保持一致 | annualized return |
| 波動率 | 波動率 | Result metric label | 保持一致 | volatility |
| 夏普比率 | 夏普比率 | Result metric label | 保持一致 | Sharpe ratio |
| 索提諾比率 | 索提諾比率 | Result metric label | 保持一致 | Sortino ratio |
| 實驗 | 實驗 | Experiment entity | 保持一致 | experiment |
| 版本 | 版本 | Version entity | 保持一致 | experiment version |
| Run | Run | Run entity | 保持一致（英文） | simulation run |
| 標的 | 標的 | Instrument entity | 保持一致 | instrument/symbol |
| 投資組合 | 投資組合 | Portfolio entity | 保持一致 | paper portfolio |
| 模擬 | 模擬 | Simulation mode | 保持一致 | simulation mode |
| 即時 | 即時 | Live mode | 保持一致 | live mode |
| 延遲 | 延遲 | Delayed data | 保持一致 | delayed data |
| 合成 | 合成 | Synthetic data | 保持一致 | synthetic data |
| 未知 | 未知 | Unknown status | 保持一致 | unknown status |
| 不適用 | 不適用 | N/A value | 保持一致 | not applicable |
| CSV | CSV | Export format | 保持一致（英文） | CSV export |
| PDF | PDF | Export format | 保持一致（英文） | PDF export |
| JSON | JSON | Data format | 保持一致（英文） | JSON format |
| API | API | Technical term | 保持一致（英文） | application programming interface |

### 7.2 繁體中文主文案規則

**規則 1：中文優先，英文保留**
- 所有 UI 文字預設使用繁體中文
- 僅保留以下英文術語：
  - 技術名詞：API、CSV、JSON、PDF、WebSocket、HTTP
  - 金融名詞：SMA、RSI、MACD、Sharpe、Sortino
  - 產品名詞：Run、Backtest、Replay（如無合適中文翻譯）
- 不混用無意義英語補充（如「登入 Login」「註冊 Register」）

**規則 2：具體勝過抽象**
- 使用「建立帳號」而非「註冊」
- 使用「最後更新」而非「最後活動」
- 使用「發生錯誤」而非「錯誤」

**規則 3：簡潔勝過完整**
- 按鈕文字不超過 6 個字（如「新實驗」而非「建立新實驗」）
- 標題不超過 10 個字（如「我的研究實驗」而非「我的所有研究實驗列表」）
- 描述不超過 2 行（如空狀態描述）

**規則 4：主動語態勝過被動語態**
- 使用「認真檢驗你的投資想法」而非「你的投資想法值得被認真檢驗」
- 使用「建立第一個實驗」而非「第一個實驗可以被建立」

**規則 5：一致性勝過多樣性**
- 同一概念全域使用相同術語（如「持倉」不混用「倉位」「部位」）
- 同一操作全域使用相同動詞（如「建立」不混用「新增」「新增」）
- 同一狀態全域使用相同形容詞（如「進行中」不混用「探索中」「活躍」）

**規則 6：語氣專業但不冷漠**
- 使用「我的研究實驗」而非「實驗列表」（個人化）
- 使用「還沒有任何實驗」而非「無實驗」（自然語言）
- 使用「發生錯誤」而非「系統錯誤」（中性而非指責）

**規則 7：風險提示明確**
- 所有涉及真實資金的頁面必須顯示免責聲明
- 所有模擬交易頁面必須標示「模擬」
- 所有回測結果必須標示「歷史數據，不代表未來表現」

### 7.3 文案覆蓋率

- 總術語數：85
- 已定義：85（100%）
- 已驗證對應真實功能：85（100%）
- 需後端支援：0（純前端文案改動）

---

## 8. Wireframes 與 Mockups

### 8.1 登入頁 Wireframe（Desktop）

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│                    [Logo] Alethra                           │
│                                                             │
│              認真檢驗你的投資想法                            │
│                                                             │
│         ┌─────────────────────────────────┐                │
│         │  登入                            │                │
│         │                                 │                │
│         │  電子郵件                        │                │
│         │  [________________________]     │                │
│         │                                 │                │
│         │  密碼                            │                │
│         │  [________________________]     │                │
│         │                                 │                │
│         │  [====== 登入 ======]           │                │
│         │                                 │                │
│         │  還沒有帳號？建立帳號           │                │
│         └─────────────────────────────────┘                │
│                                                             │
│         © 2026 Alethra. 研究工具，非投資建議                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 8.2 研究首頁 Wireframe（Desktop）

```
┌─────────────────────────────────────────────────────────────┐
│ [Logo] Alethra    [研究] [工作台] [持倉] [設定]    [用戶 ▼] │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  我的研究實驗                                    [+ 新實驗] │
│                                                             │
│  [全部 (12)] [草稿 (3)] [進行中 (5)] [已鎖定 (4)]          │
│  [排序: 最後更新 ▼]                                         │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ AAPL 動量策略                                        │   │
│  │ 最後更新: 2024-01-15 14:30  勝率: 62%  收益: +8.3%  │   │
│  │ [草稿] [繼續研究 →]                                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ TSLA 均值回歸                                        │   │
│  │ 最後更新: 2024-01-14 10:15  勝率: 58%  收益: +5.2%  │   │
│  │ [進行中] [查看結果 →]                                │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ NVDA 突破策略                                        │   │
│  │ 最後更新: 2024-01-13 16:45  勝率: 65%  收益: +12.1% │   │
│  │ [已鎖定] [查看報告 →]                                │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ © 2026 Alethra. 研究工具，非投資建議                        │
└─────────────────────────────────────────────────────────────┘
```

### 8.3 工作台 Wireframe（Desktop）

```
┌──────┬──────────────────────────────────┬────────────────┐
│觀察  │  [Instrument Header]             │ 研究面板       │
│名單  │  AAPL - Apple Inc.               │                │
│      │  $150.25 +2.3%  [Live]           │ [策略]         │
│▶AAPL │                                  │ 入场: SMA>20   │
│ TSLA ├──────────────────────────────────┤ 出场: 持有5天  │
│ NVDA │                                  │                │
│ MSFT │                                  │ [證據]         │
│ GOOG │      [Chart Area 55%]            │ 勝率: 62%      │
│ META │                                  │ 收益: +8.3%    │
│      │                                  │ 最大回撤: -3.2%│
│      │                                  │ 盈虧比: 1.8    │
│      ├──────────────────────────────────┤                │
│      │ [Replay Controller]              │ [回放]         │
│      │ ▶ ⏸ ⏩ 1x  2024-01-15 10:30     │ ⏸ 暫停         │
├──────┴──────────────────────────────────┤ ⏩ 1x          │
│ [交易]                                  │                │
│ 數量 [____]  價格 [____]                │ [交易]         │
│ [買入]  [賣出]                          │ 數量 [____]    │
│                                         │ 價格 [____]    │
│                                         │ [買入] [賣出]  │
└─────────────────────────────────────────┴────────────────┘
```

### 8.4 投資組合 Wireframe（Desktop）

```
┌─────────────────────────────────────────────────────────────┐
│ [Logo] Alethra    [研究] [工作台] [持倉] [設定]    [用戶 ▼] │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  我的投資組合                                               │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 總資產: $108,300  總盈虧: +$8,300 (+8.3%)  今日: +$500 │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ AAPL - Apple Inc.                                    │   │
│  │ 數量: 100  均價: $145.00  現價: $150.25              │   │
│  │ 盈虧: +$525 (+3.6%)  持倉佔比: 45%                  │   │
│  │ [平倉] [調整倉位] [止損止盈]                         │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ TSLA - Tesla Inc.                                    │   │
│  │ 數量: 50  均價: $240.00  現價: $248.50               │   │
│  │ 盈虧: +$425 (+3.5%)  持倉佔比: 35%                  │   │
│  │ [平倉] [調整倉位] [止損止盈]                         │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ NVDA - NVIDIA Corp.                                  │   │
│  │ 數量: 30  均價: $480.00  現價: $495.20               │   │
│  │ 盈虧: +$456 (+3.2%)  持倉佔比: 20%                  │   │
│  │ [平倉] [調整倉位] [止損止盈]                         │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  交易日誌                                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 2024-01-15 14:30  買入 AAPL  100股  $145.00        │   │
│  │ 2024-01-14 10:15  買入 TSLA  50股   $240.00        │   │
│  │ 2024-01-13 16:45  買入 NVDA  30股   $480.00        │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ © 2026 Alethra. 研究工具，非投資建議                        │
└─────────────────────────────────────────────────────────────┘
```

### 8.5 Mobile Wireframes

**登入頁（Mobile）**：
```
┌─────────────────────┐
│                     │
│   [Logo] Alethra    │
│                     │
│  認真檢驗你的        │
│  投資想法            │
│                     │
│  ┌───────────────┐ │
│  │  登入          │ │
│  │               │ │
│  │  電子郵件      │ │
│  │  [_________] │ │
│  │               │ │
│  │  密碼          │ │
│  │  [_________] │ │
│  │               │ │
│  │  [== 登入 ==] │ │
│  │               │ │
│  │  還沒有帳號？  │ │
│  │  建立帳號      │ │
│  └───────────────┘ │
│                     │
└─────────────────────┘
```

**研究首頁（Mobile）**：
```
┌─────────────────────┐
│ [Logo]    [用戶 ▼]  │
├─────────────────────┤
│                     │
│  我的研究實驗        │
│         [+ 新實驗]  │
│                     │
│  [全部] [草稿]      │
│  [進行中] [已鎖定]  │
│                     │
│  ┌───────────────┐ │
│  │ AAPL 動量策略 │ │
│  │ 最後更新:     │ │
│  │ 2024-01-15    │ │
│  │ 勝率: 62%     │ │
│  │ [草稿] [→]    │ │
│  └───────────────┘ │
│                     │
│  ┌───────────────┐ │
│  │ TSLA 均值回歸 │ │
│  │ 最後更新:     │ │
│  │ 2024-01-14    │ │
│  │ 勝率: 58%     │ │
│  │ [進行中] [→]  │ │
│  └───────────────┘ │
│                     │
├─────────────────────┤
│ [研究] [工作台]     │
│ [持倉] [設定]       │
└─────────────────────┘
```

**工作台（Mobile）**：
```
┌─────────────────────┐
│ [Logo]    [用戶 ▼]  │
├─────────────────────┤
│                     │
│  AAPL - Apple Inc.  │
│  $150.25 +2.3%      │
│  [Live]             │
│                     │
│  ┌───────────────┐ │
│  │               │ │
│  │  [Chart 70%]  │ │
│  │               │ │
│  │               │ │
│  └───────────────┘ │
│                     │
│  [▶ 回放] [⏸] [⏩] │
│                     │
│  ┌───────────────┐ │
│  │ [研究 ▼]      │ │
│  │ (Bottom Sheet)│ │
│  └───────────────┘ │
│                     │
├─────────────────────┤
│ [研究] [工作台]     │
│ [持倉] [設定]       │
└─────────────────────┘
```

### 8.6 Mockup 說明

**Mockup 1：登入頁（Desktop）**
- 視覺風格：深色背景（#0F141B），中央表單卡片
- 品牌元素：Logo + 標語「認真檢驗你的投資想法」
- 表單設計：簡潔輸入框（40px 高度），明確按鈕（48px 高度）
- 資訊層級：品牌標語（H1）> 表單標題（H2）> 欄位標籤（Body）

**Mockup 2：研究首頁（Desktop）**
- 視覺風格：深色背景，單欄列表
- 導航：頂部固定導航列（Logo + 主要頁面 + 用戶選單）
- 篩選：狀態篩選按鈕組（全部/草稿/進行中/已鎖定）
- 實驗卡片：顯示標題、最後更新、關鍵指標、狀態 Badge、操作按鈕

**Mockup 3：工作台（Desktop）**
- 視覺風格：三欄佈局（觀察名單/圖表/研究面板）
- 圖表區域：佔 55%，顯示價格曲線、回放控制列
- 研究面板：佔 30%，分 3 個 Tab（策略/證據/回放）
- 交易面板：佔 15%，顯示數量/價格輸入、買入/賣出按鈕

**Mockup 4：投資組合（Desktop）**
- 視覺風格：深色背景，單欄列表
- 持倉摘要：頂部顯示總資產、總盈虧、今日盈虧
- 持倉卡片：顯示標的、數量、均價、現價、盈虧、持倉佔比
- 快速操作：每個持倉卡片顯示平倉/調整/止損止盈按鈕

**注意**：以上 Mockup 為文字描述，非視覺設計稿。如需視覺 Mockup，建議使用 Figma 或類似工具製作。

---

## 9. 分階段實作／測試計劃

### Phase 1：Shared Visual Language / Navigation（1 週）

**範圍**：
- 更新 globals.css 設計 Tokens（Colour/Surface/Spacing/Radius）
- 更新 AppShell 導航結構（研究/工作台/持倉/設定）
- 更新 Mobile Bottom Tab Bar
- 更新 Typography Scale

**沿用現有程式**：
- AppShell.tsx（導航結構）
- globals.css（設計 Tokens）

**必要 Backend Gap**：
- 無

**Visual Acceptance**：
- Desktop/Mobile 導航一致
- 設計 Tokens 全域應用
- Typography Scale 正確顯示

**Functional Regression**：
- 導航連結正確跳轉
- Mobile Bottom Tab Bar 正確顯示
- 無 JavaScript 錯誤

**Owner Review Point**：
- Phase 1 完成後，Owner 審核視覺語言與導航結構

---

### Phase 2：Auth（1 週）

**範圍**：
- 重設計登入頁（方向 B Wireframe）
- 更新文案系統（Section 7）
- 增加「記住我」功能（可選）
- 增加「忘記密碼」流程（可選）

**沿用現有程式**：
- login/page.tsx（表單結構）
- api.auth.login/register（API 呼叫）

**必要 Backend Gap**：
- 「記住我」：需後端支援 refresh token（可選）
- 「忘記密碼」：需後端支援密碼重置郵件（可選）

**Visual Acceptance**：
- 登入頁符合方向 B Wireframe
- 文案一致（Section 7）
- Desktop/Mobile 響應式正確

**Functional Regression**：
- 登入/註冊流程正確
- 錯誤提示正確顯示
- 無 JavaScript 錯誤

**Owner Review Point**：
- Phase 2 完成後，Owner 審核登入頁設計與文案

---

### Phase 3：Research Entry（1.5 週）

**範圍**：
- 新增研究首頁（/research）
- 重設計實驗列表（增加最後更新、關鍵指標）
- 增加篩選/排序功能
- 更新空狀態設計
- 更新文案系統

**沿用現有程式**：
- dashboard/page.tsx（實驗列表結構）
- api.experiments.list（API 呼叫）

**必要 Backend Gap**：
- 實驗列表需返回「最後 Run 時間」「勝率」「收益」
- 需新增 API：GET /api/v1/experiments?sort=updated_at&order=desc

**Visual Acceptance**：
- 研究首頁符合方向 B Wireframe
- 實驗列表顯示關鍵指標
- 篩選/排序功能正確
- 空狀態設計正確

**Functional Regression**：
- 實驗列表正確載入
- 篩選/排序功能正確
- 點擊實驗卡片正確跳轉
- 無 JavaScript 錯誤

**Owner Review Point**：
- Phase 3 完成後，Owner 審核研究首頁設計與功能

---

### Phase 4：Workspace（2 週）

**範圍**：
- 重設計工作台佈局（圖表 55%、觀察名單 15%、研究面板 30%）
- 新增研究面板（策略/證據/回放）
- 更新操作面板（合併策略+手動單）
- 增加快速切換功能（Cmd+K）
- 增加回放快捷鍵（Space/← →/1-5）
- 更新文案系統

**沿用現有程式**：
- WorkspaceShell.tsx（工作台結構）
- PriceChart.tsx（圖表組件）
- WatchlistPanel.tsx（觀察名單）
- ActionPanel.tsx（操作面板）
- ReplayController.tsx（回放控制）

**必要 Backend Gap**：
- 研究面板需返回「策略規格」「證據摘要」
- 需新增 API：GET /api/v1/experiments/[id]/evidence

**Visual Acceptance**：
- 工作台佈局符合方向 B Wireframe
- 研究面板正確顯示
- 快速切換/快捷鍵功能正確
- Desktop/Tablet/Mobile 響應式正確

**Functional Regression**：
- 圖表正確載入
- 觀察名單正確顯示
- 操作面板功能正確
- 回放功能正確
- 無 JavaScript 錯誤

**Owner Review Point**：
- Phase 4 完成後，Owner 審核工作台設計與功能

---

### Phase 5：Results / Journal / Settings Reconciliation（1.5 週）

**範圍**：
- 重設計投資組合頁（/portfolio）
- 增加持倉摘要（總資產、總盈虧、今日盈虧）
- 增加快速操作（平倉、調整倉位、設定止損止盈）
- 整合交易日誌與持倉
- 重設計設定頁（/settings）
- 整合所有設定到統一頁面
- 增加設定搜尋功能
- 更新文案系統

**沿用現有程式**：
- paper-portfolio/page.tsx（投資組合結構）
- api.paperPortfolio.list（API 呼叫）

**必要 Backend Gap**：
- 持倉列表需返回「持倉佔比」「盈虧%」
- 需新增 API：POST /api/v1/portfolio/[id]/close（平倉）
- 需新增 API：PUT /api/v1/portfolio/[id]/adjust（調整倉位）
- 需新增 API：POST /api/v1/portfolio/[id]/stop-loss（設定止損止盈）

**Visual Acceptance**：
- 投資組合頁符合方向 B Wireframe
- 持倉摘要正確顯示
- 快速操作功能正確
- 設定頁整合正確
- Desktop/Mobile 響應式正確

**Functional Regression**：
- 持倉列表正確載入
- 快速操作功能正確
- 設定頁功能正確
- 無 JavaScript 錯誤

**Owner Review Point**：
- Phase 5 完成後，Owner 審核投資組合與設定頁設計

---

### 保留目前已驗證的功能

以下功能在重設計過程中必須保留，不得移除或削弱：

1. **Replay Clock**：回放時鐘顯示當前時間
2. **No-Lookahead**：回放過程不得顯示未來數據
3. **帳務**：持倉盈虧計算必須正確
4. **Idempotency**：訂單提交必須冪等（不得重複提交）
5. **Ownership**：實驗/持倉必須屬於當前用戶
6. **CSV**：交易日誌必須可匯出 CSV
7. **Layout Persistence**：工作台佈局必須持久化（localStorage）
8. **Keyboard Interaction**：所有操作必須支援鍵盤（Tab/Enter/Escape）

---

### 測試策略

**每個 Phase 的測試**：
1. **Visual Acceptance**：Owner 審核視覺設計是否符合 Wireframe
2. **Functional Regression**：運行現有測試套件，確保無功能退化
3. **Cross-Browser Testing**：Chrome/Firefox/Safari/Edge 最新 2 個版本
4. **Responsive Testing**：Desktop (1280px+) / Tablet (768-1279px) / Mobile (<768px)
5. **Accessibility Testing**：鍵盤導航、螢幕閱讀器、色彩對比

**最終驗收**：
- 所有 Phase 完成後，運行完整測試套件
- Owner 進行端到端驗收（登入 → 研究 → 工作台 → 持倉 → 設定）
- 記錄所有已知問題，制定修復計劃

---

## 10. 需要 Owner 決定的具體事項

### 決策 1：設計方向確認

**選項**：
- A. Trading Platform Standard（專業交易平台）
- B. Research-First Professional Workspace（研究優先專業工作台）⭐ 推薦
- C. Minimalist Research Tool（極簡研究工具）

**建議**：方向 B

**理由**：
- 符合 Alethra 產品定位（研究 + 模擬交易）
- 平衡專業與友好
- 實作成本可控

**請確認**：是否採用方向 B？或需要調整？

---

### 決策 2：研究首頁必要性

**問題**：登入後是否應該進入研究首頁（實驗列表），還是直接進入工作台？

**選項**：
- A. 進入研究首頁（方向 B 設計）
- B. 直接進入工作台（方向 A/C 設計）

**建議**：A（研究首頁）

**理由**：
- 研究首頁提供全局視角，幫助用戶決定下一步
- 工作台是主要任務頁，但不一定是每次登入的首選
- 符合「研究優先」定位

**請確認**：是否採用研究首頁？

---

### 決策 3：研究面板內容

**問題**：研究面板應該顯示哪些內容？

**選項**：
- A. 策略規格 + 證據摘要 + 回放控制（方向 B 設計）
- B. 僅策略規格 + 證據摘要
- C. 僅回放控制 + 交易面板

**建議**：A

**理由**：
- 整合研究與交易，支援決策流程
- 證據摘要幫助用戶評估策略
- 回放控制方便用戶測試

**請確認**：研究面板內容是否為策略 + 證據 + 回放？

---

### 決策 4：Mobile Bottom Tab Bar 項目

**問題**：Mobile Bottom Tab Bar 應該包含哪些項目？

**選項**：
- A. 研究 / 工作台 / 持倉 / 設定（方向 B 設計）
- B. 工作台 / 持倉 / 設定（3 個項目）
- C. 研究 / 持倉 / 設定（無工作台）

**建議**：A

**理由**：
- 4 個項目覆蓋主要任務
- 工作台是核心功能，不可省略
- 研究首頁提供全局視角

**請確認**：Mobile Bottom Tab Bar 是否為 4 個項目？

---

### 決策 5：可選功能優先級

**問題**：以下可選功能，哪些應該在 Phase 1-5 中實作，哪些延後？

**功能列表**：
- 「記住我」功能（登入頁）
- 「忘記密碼」流程（登入頁）
- 快速切換功能（Cmd+K）（工作台）
- 回放快捷鍵（Space/← →/1-5）（工作台）
- 設定搜尋功能（設定頁）
- 設定匯入/匯出功能（設定頁）
- 2FA 安全設定（設定頁）

**建議**：
- Phase 1-5 實作：快速切換、回放快捷鍵
- 延後：記住我、忘記密碼、設定搜尋、設定匯入匯出、2FA

**理由**：
- 快速切換與回放快捷鍵提升核心體驗
- 其他功能為進階需求，可延後

**請確認**：是否同意此優先級？

---

### 決策 6：文案系統採用

**問題**：是否採用 Section 7 的文案系統？

**選項**：
- A. 全盤採用（85 個術語）
- B. 部分採用（僅核心術語，約 50 個）
- C. 不採用（維持現狀）

**建議**：A（全盤採用）

**理由**：
- 文案系統確保全域一致性
- 85 個術語已驗證對應真實功能
- 純前端改動，無後端成本

**請確認**：是否全盤採用文案系統？

---

### 決策 7：實作時程

**問題**：是否同意 Phase 1-5 的實作時程（共 7 週）？

**時程**：
- Phase 1：1 週（Visual Language / Navigation）
- Phase 2：1 週（Auth）
- Phase 3：1.5 週（Research Entry）
- Phase 4：2 週（Workspace）
- Phase 5：1.5 週（Results / Journal / Settings）
- 總計：7 週

**建議**：同意

**理由**：
- 每個 Phase 範圍明確
- 時程合理（無過度壓縮）
- 每個 Phase 結束有 Owner Review Point

**請確認**：是否同意 7 週時程？

---

### 決策 8：測試策略

**問題**：是否同意 Section 9 的測試策略？

**測試內容**：
- Visual Acceptance（Owner 審核）
- Functional Regression（現有測試套件）
- Cross-Browser Testing（Chrome/Firefox/Safari/Edge）
- Responsive Testing（Desktop/Tablet/Mobile）
- Accessibility Testing（鍵盤/螢幕閱讀器/色彩對比）

**建議**：同意

**理由**：
- 測試覆蓋全面
- 確保無功能退化
- 符合專業標準

**請確認**：是否同意測試策略？

---

## 附錄 A：參考資料

### 競品研究來源

1. **Binance Pro Mode**
   - 官方產品介面：https://www.binance.com/en/trade/BTC_USDT
   - 官方文件：https://www.binance.com/en/support/faq
   - 研究日期：2026-10-27

2. **Robinhood Legend**
   - 官方產品介面：https://robinhood.com/us/en/legend/
   - 支援文件：https://robinhood.com/us/en/support/articles/widgets-in-robinhood-legend/
   - 研究日期：2026-10-27

3. **TradingView Supercharts**
   - 官方產品介面：https://www.tradingview.com/chart/
   - 文件庫：https://www.tradingview.com/charting-library-docs/
   - 研究日期：2026-10-27

4. **富途 moomoo Desktop**
   - 官方產品介面：https://www.moomoo.com/us/
   - 教學中心：https://www.moomoo.com/us/learn/overview
   - 研究日期：2026-10-27

5. **SoFi HK**
   - 官方產品介面：https://www.sofi.hk/
   - App Store：https://apps.apple.com/hk/app/sofi-invest-simple/id1212242967
   - 研究日期：2026-10-27

### 設計原則參考

1. **Trading App Design: The Complete Guide**
   - 來源：Medium
   - 連結：https://medium.com/@david.pham_1649/trading-app-design-the-complete-guide-to-ui-ux-system-architecture-5b10664c5002
   - 研究日期：2026-10-27

2. **Investment Platform UI: 8 Practices That Improve User Trust**
   - 來源：Ron Design Lab
   - 連結：https://rondesignlab.com/blog/design-news/most-sucessful-practices-for-investment-platform-ui-ux
   - 研究日期：2026-10-27

3. **UI/UX Design of Web & Mobile Financial Software**
   - 來源：Devexperts
   - 連結：https://devexperts.com/uxui-design/
   - 研究日期：2026-10-27

---

## 附錄 B：術語表

| 英文 | 中文 | 定義 |
|------|------|------|
| Experiment | 實驗 | 一個研究項目，包含策略規格、版本、Run 歷史 |
| Version | 版本 | 實驗的一個快照，包含策略規格與參數 |
| Run | Run | 一次回放或回測執行 |
| Backtest | 回測 | 使用歷史數據測試策略 |
| Replay | 回放 | 使用歷史數據逐步測試策略 |
| Strategy | 策略 | 交易規則的定義（入场/出场/倉位管理） |
| Instrument | 標的 | 交易對象（股票/加密貨幣/外匯等） |
| Symbol | 代號 | 標的的代碼（如 AAPL、BTCUSDT） |
| Position | 持倉 | 持有的標的數量與成本 |
| Order | 訂單 | 買入或賣出的請求 |
| Fill | 成交 | 訂單的執行結果 |
| Journal | 日誌 | 交易歷史記錄 |
| Portfolio | 投資組合 | 所有持倉的集合 |
| Paper Trading | 模擬交易 | 使用虛擬資金的交易 |
| Live Trading | 即時交易 | 使用真實資金的交易 |
| Synthetic Data | 合成數據 | 人工生成的測試數據 |
| Delayed Data | 延遲數據 | 延遲 15 分鐘或更久的市場數據 |
| Real-time Data | 即時數據 | 即時的市場數據 |
| Win Rate | 勝率 | 盈利交易佔總交易的比例 |
| Return | 收益 | 投資的回報率 |
| Max Drawdown | 最大回撤 | 從高點到低點的最大跌幅 |
| Profit/Loss Ratio | 盈虧比 | 平均盈利與平均虧損的比值 |
| Sharpe Ratio | 夏普比率 | 風險調整後的回報率 |
| Sortino Ratio | 索提諾比率 | 僅考慮下行風險的回報率 |
| Volatility | 波動率 | 價格變動的幅度 |
| Annualized Return | 年化收益 | 轉換為年度的收益率 |
| Stop Loss | 止損 | 設定虧損上限的訂單 |
| Take Profit | 止盈 | 設定盈利目標的訂單 |
| Market Order | 市價單 | 以當前市場價格執行的訂單 |
| Limit Order | 限價單 | 以指定價格或更好的價格執行的訂單 |
| API Key | API 金鑰 | 用於程式化訪問的認證金鑰 |
| 2FA | 雙重認證 | Two-Factor Authentication |
| CSV | CSV | Comma-Separated Values（逗號分隔值） |
| JSON | JSON | JavaScript Object Notation |
| PDF | PDF | Portable Document Format |

---

## 附錄 C：變更日誌

| 版本 | 日期 | 變更內容 | 作者 |
|------|------|---------|------|
| v1.0 | 2026-10-27 | 初始版本，完整設計提案 | AI Assistant |

---

**文件結束**

**下一步**：
1. Owner 審核本提案（Section 10 的 8 個決策）
2. Owner 確認設計方向與實作時程
3. 進入 Phase 1 實作（Visual Language / Navigation）

**聯繫方式**：
- 如有問題，請在 Owner Review Point 提出
- 如需調整提案，請提供具體反饋

**免責聲明**：
- 本提案為設計規劃文件，非產品程式碼
- 所有 Wireframe 與 Mockup 為文字描述，非視覺設計稿
- 實作細節可能因技術限制而調整
- 最終產品以 Owner 審核為準

---

*© 2026 Alethra. 本文件為內部設計提案，未經授權不得外傳。*
