# Alethra UI/UX 重設計提案 — Owner 審核摘要

**日期**：2026-10-27  
**提案版本**：v1.0  
**文件位置**：`docs/design-proposal-v1.md`

---

## 執行摘要

本提案完成以下工作：

1. **競品研究**：深入研究 5 個核心平台（Binance、SoFi HK、Robinhood Legend、TradingView、富途 moomoo）+ 2 個補充平台（IBKR、Webull），涵蓋 18 個頁面樣本
2. **現況診斷**：分析 Alethra 目前 7 個主要頁面的 UI/UX 問題，提供具體症狀、影響、設計原因、競品證據、建議改法
3. **三個設計方向**：提出 3 個真正不同的設計方向（非僅換色），包含完整 Wireframe 與規格
4. **推薦方案**：詳細規格包含 Navigation、Region Map、Type Scale、Design Tokens、Components、Interaction Flows
5. **文案系統**：85 個術語的完整文案表，確保全域一致性
6. **實作計劃**：5 個 Phase，共 7 週時程，每個 Phase 有明確範圍、驗收標準、Owner Review Point

---

## 核心問題

Alethra 目前 UI 呈現「工程示範後台」氣質，而非成熟研究交易平台：

- **資訊層級扁平**：登入頁、研究首頁、工作台使用相同視覺權重
- **術語不一致**：「實驗」「策略」「版本」「Run」混用
- **容器過度均質**：七個等權重功能入口卡片令設定與主要研究任務競爭注意力
- **缺乏金融產品語境**：未區分資料與操作、即時與模擬、研究與交易

---

## 推薦方向：Research-First Professional Workspace

**理由**：
1. 符合 Alethra 定位（研究、測試、模擬交易平台）
2. 強調資訊層級與任務流程，符合「認真檢驗投資想法」的產品主張
3. 實作成本可控（沿用現有 AppShell、WorkspaceShell）
4. 可與 TradingView、Robinhood Legend 的專業用戶期待接軌

**關鍵設計**：
- 登入頁：中央表單 + 品牌標語（無裝飾性品牌區）
- 研究首頁：實驗列表 + 篩選/排序 + 關鍵指標（勝率/收益）
- 工作台：三欄佈局（觀察名單 15% / 圖表 55% / 研究面板 30%）
- 投資組合：持倉列表 + 快速操作 + 交易日誌整合

---

## 需要 Owner 決定的 8 個事項

### 決策 1：設計方向確認
- **選項**：A. Trading Platform Standard / B. Research-First ⭐ / C. Minimalist
- **建議**：方向 B
- **影響**：整體產品定位與視覺風格

### 決策 2：研究首頁必要性
- **問題**：登入後進入研究首頁（實驗列表）還是直接進入工作台？
- **建議**：進入研究首頁
- **影響**：用戶首次體驗與任務流程

### 決策 3：研究面板內容
- **問題**：研究面板應該顯示哪些內容？
- **建議**：策略規格 + 證據摘要 + 回放控制
- **影響**：工作台資訊密度與功能完整性

### 決策 4：Mobile Bottom Tab Bar 項目
- **問題**：Mobile 導航應該包含哪些項目？
- **建議**：研究 / 工作台 / 持倉 / 設定（4 個）
- **影響**：Mobile 體驗與任務可達性

### 決策 5：可選功能優先級
- **問題**：哪些功能在 Phase 1-5 實作，哪些延後？
- **建議**：Phase 1-5 實作快速切換（Cmd+K）與回放快捷鍵；延後記住我、忘記密碼、設定搜尋等
- **影響**：實作範圍與時程

### 決策 6：文案系統採用
- **問題**：是否採用 85 個術語的文案系統？
- **建議**：全盤採用
- **影響**：產品術語一致性與專業感

### 決策 7：實作時程
- **問題**：是否同意 7 週時程（Phase 1-5）？
- **建議**：同意
- **影響**：項目排程與資源分配

### 決策 8：測試策略
- **問題**：是否同意完整測試策略（Visual/Functional/Cross-Browser/Responsive/Accessibility）？
- **建議**：同意
- **影響**：品質保證與驗收標準

---

## 實作計劃概覽

| Phase | 範圍 | 時程 | 關鍵交付 |
|-------|------|------|---------|
| Phase 1 | Visual Language / Navigation | 1 週 | 設計 Tokens、AppShell 更新 |
| Phase 2 | Auth | 1 週 | 登入頁重設計、文案更新 |
| Phase 3 | Research Entry | 1.5 週 | 研究首頁、實驗列表重設計 |
| Phase 4 | Workspace | 2 週 | 工作台佈局、研究面板、快捷鍵 |
| Phase 5 | Results / Journal / Settings | 1.5 週 | 投資組合頁、設定頁整合 |
| **總計** | | **7 週** | **完整 UI/UX 重設計** |

---

## 保留功能（不得移除或削弱）

以下功能在重設計過程中必須保留：

1. Replay Clock（回放時鐘）
2. No-Lookahead（不得顯示未來數據）
3. 帳務計算正確性
4. Idempotency（訂單冪等性）
5. Ownership（實驗/持倉所有權）
6. CSV 匯出功能
7. Layout Persistence（佈局持久化）
8. Keyboard Interaction（鍵盤操作）

---

## 風險與緩解

| 風險 | 影響 | 緩解措施 |
|------|------|---------|
| 研究首頁增加用戶步驟 | 中 | 提供「跳過」選項，記住用戶偏好 |
| 研究面板 30% 寬度不足 | 中 | 用戶測試驗證，可調整為 35% |
| 回放快捷鍵學習成本高 | 低 | 提供快捷鍵提示（首次使用時） |
| 文案系統改動影響現有用戶 | 低 | 漸進式更新，保留舊術語 3 個月 |
| Backend API 不足（證據摘要） | 高 | Phase 3 前完成 API 開發 |

---

## 下一步

1. **Owner 審核本提案**（本文件 + `docs/design-proposal-v1.md`）
2. **Owner 做出 8 個決策**（回覆本文件或召開會議）
3. **進入 Phase 1 實作**（Visual Language / Navigation，1 週）

---

## 文件清單

| 文件 | 路徑 | 說明 |
|------|------|------|
| 完整提案 | `docs/design-proposal-v1.md` | 10 個章節，完整設計規格 |
| Owner 審核摘要 | `docs/design-proposal-owner-review.md` | 本文件，8 個決策事項 |
| 競品研究筆記 | `docs/design-proposal-v1.md` Section 2-3 | 5 平台研究結果 |
| 現況診斷 | `docs/design-proposal-v1.md` Section 4 | 7 個頁面問題分析 |
| 設計方向 | `docs/design-proposal-v1.md` Section 5 | 3 個方向完整說明 |
| 推薦方案規格 | `docs/design-proposal-v1.md` Section 6 | 詳細設計規格 |
| 文案系統 | `docs/design-proposal-v1.md` Section 7 | 85 個術語表 |
| Wireframes | `docs/design-proposal-v1.md` Section 8 | 文字 Wireframe 描述 |
| 實作計劃 | `docs/design-proposal-v1.md` Section 9 | 5 Phase 時程與驗收 |

---

## 聯繫方式

- **提案作者**：AI Assistant
- **審核截止日期**：待定
- **會議安排**：如需討論，請提議時間

---

**免責聲明**：
- 本提案為設計規劃文件，非產品程式碼
- 所有 Wireframe 與 Mockup 為文字描述，非視覺設計稿
- 實作細節可能因技術限制而調整
- 最終產品以 Owner 審核為準

---

*© 2026 Alethra. 本文件為內部設計提案，未經授權不得外傳。*
