# Alethra 工作台重設計規格（方案 A 為主、B/C 為輔）

狀態：提案，待擁有者核准後逐個 increment 實作。
基準 commit：`6bedafa0047e6eb62b8c91d2c5771602456c4531`（branch `work/unified-research-flow`）。
適用範圍：前端版面與互動（UX only）。不改回測/模擬引擎、不接經紀商、不新增相依、不部署、不涉及實盤。

## 0. 設計宣言與邊界

- Alethra 是**研究驅動的模擬交易工作台**：圖表是視覺中心，策略是操作依據，交易日誌與結果是證據。不是券商落單介面的複製品。
- 差異化在於**策略 → 回放 → 模擬執行 → 證據**之間看得見的連結，而不是多一個附了買入按鈕的走勢圖。
- 三個參考站（Binance 個股頁、SoFi HK 入口/漸進揭露、Robinhood Legend widget 工作區）**只提供版面組織概念**；不複製品牌、字型、素材、色彩或確切版面，也不宣稱已獨立驗證競品行為。SoFi 取的是進入方式與行動版，不是行銷首頁。
- 本文件所有尺寸、字號、色碼、容器數量均為替 Alethra 提出的設計建議，**不是**三個參考網站的實測 CSS。
- 第一版方向已定：8 個主要功能區、三欄主體、底部證據區、低對比結構框線、14px 正文字級。
- 現況是兩套視覺密度並存（workspace 多用 10–13px 的密集表格；dashboard 卡片留白較大）。本规格的取向是**統一這兩套密度**，不是另起第三套設計。token 層已以 legacy alias 重映射既有變數名，讓兩邊共用同一套值。

## 1. 資訊架構（IA）

### 1.1 三種頁面模式

| 模式 | 目的 | 主要容器 | URL |
|---|---|---|---|
| 工作台 Workspace | 在同一脈絡內完成「看標的 → 看數據 → 做模擬動作 → 留證據」 | 容器 ①–⑧（§2） | `/workspace` |
| 策略與實驗 Strategies & Experiments | 建立/鎖定策略版本、跑回測、開回放會話 | 表單式工作區（文字密度高，寬度可受限） | `/dashboard`（hub）、`/experiments/new`、`/experiments/[id]`、`/experiments/[id]/simulation`、`/experiments/[id]/runs/[runId]`、`/experiments/[id]/runs/compare` |
| 組合與結果 Portfolios / Journal & Results | 審閱紙上組合、訂單、成交、決策日誌與成果 | 記錄式表格 + 詳情抽屜 | `/paper-portfolio`、`/paper-portfolio/[id]`、`/paper-portfolio?view=journal` |

### 1.2 主要導覽（四項，不動）

`工作台 /workspace` → `策略與實驗 /dashboard` → `投資組合 /paper-portfolio` → `紀錄與結果 /paper-portfolio?view=journal`

### 1.3 設定與進階（不佔主要導覽）

- Settings 選單：`API 金鑰 /api-keys`、`訂閱與帳單 /subscription`。
- 進階／系統選單（保留全部現有頁面，不刪功能）：`模型 /models`、`訓練 /training`、`推論 /inference`、`執行 /execution`、`市場數據 /market-data`、`研究草稿 /research-drafts`、`個人記憶 /personal-memory`、`試點 /pilot`、`管理 /admin`。
- 原則：API 金鑰與帳號設定屬次要入口，不與四個工作區導覽並列。

## 2. 方案 A：八個功能容器（桌面 1440）

主題模型：Context → Analysis → Action → Evidence。

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ① 全站 Header 56px   Alethra │ 工作台 策略與實驗 投資組合 紀錄與結果 │ 設定 登出 │
├──────────────────────────────────────────────────────────────────────────────┤
│ ② 脈絡列 min 48px（可換行）                                                  │
│   標的 US:AAPL 蘋果公司 │ 投資組合 生效中 虛擬現金 100,000.00 USD │ 模式 ▣ 合成歷史回放 · 非真實行情 │ 來源 Demo · 截至 2026-10-24 │ 市場 已收市 │ 回放時計 2026-10-12 │
│                                                    [☰ 觀察列表] [搜尋標的…] [狀態色條] │
├───────────────┬──────────────────────────────────────────┬───────────────────┤
│ ③ 觀察列表    │ ④ 標的資訊列 72–96px                     │ ⑦ 操作面板        │
│ 224–256px     │  蘋果公司 · NASDAQ · USD · 每股          │ 304–336px         │
│ 每列 44–48px  │  182.40 (+2.10 +1.16%)  [模式標籤]       │ 分頁：            │
│ ───────────   │  資料性質：合成 fixture · 未復權         │  〔策略〕〔手動模擬單〕│
│ AAPL  182.40  ├──────────────────────────────────────────┤ ───────────────── │
│ 0700  312.00  │ ⑤ 圖表（中央，最小高約 320px）           │ 策略 v3 已鎖定    │
│ TSLA   …      │  工具列獨立於畫布之上                    │ sma_crossover     │
│ ＋新增列表    │  [1M 3M 6M 1Y 全部] [主圖/成交] [存圖]    │ 進場：SMA(20)>SMA(50)│
│               ├──────────────────────────────────────────┤ 出場：…  停損 8%   │
│               │ ⑥ 回放控制器 min 56px                    │ 成本：佣金 0.1%    │
│               │  目前回放日 2026-10-12 ／ 截止 2026-10-24│ 滑價 0.05%         │
│               │  [下一交易日 +1] [下一根 5 分鐘 K 棒]    │ 資料 checksum…     │
│               │  狀態：每日 12 步／共觀察 34 日          │ 〔展開完整規則〕   │
├───────────────┴──────────────────────────────────────────┴───────────────────┤
│ ⑧ 證據區 240–300px（可收合，分頁而非四張表直排）                             │
│  〔持倉 3〕〔訂單 12〕〔成交 9〕〔日誌 41〕〔結果〕  範圍：● 全部標的 ○ 目前標的 │
│  ─────────────────────────────────────────────────────────────────────────── │
│  表格：橫向分隔線，數值右對齊 tabular-nums，買入/賣出保留文字                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

1440px 網格：頁面左右 gutter 16px，欄間距 12px，左欄 240px，右欄 320px，中欄 `minmax(0,1fr)`。工作區**流動**，不套 `max-width:1400px`。表單、教學、文字密集的成果頁可維持受限寬度。

```css
.workspace-grid {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 320px;
  gap: 12px;
}
```

容器 ↔ 現有實作：現有 `/workspace` 用 `react-resizable-panels` 的 PanelGroup（可拖拽、尺寸入 layout 快取）。`.workspace-grid` 是**目標柵欄語意**；實作採「保留 PanelGroup 的拖曳與持久化，但把 min/max 收斂到 240/320 的 px 區間，並讓每個容器自帶結構邊界」，不推倒重來。

## 3. 中斷點版面（非縮小桌面版）

| 寬度 | 版面 | 重點 |
|---|---|---|
| ≥1280 | 完整三欄 + 底部證據區 | ③⑤⑦ 並列，⑧ 在下方 |
| 1024–1279 | 預設收合 ③ 觀察列表；中欄 + 右欄兩欄 | ⑥ 回放列仍緊貼圖表下方；⑧ 證據區保留但高度縮至 220px |
| 768–1023 | 圖表為主，⑦ 操作面板變側邊抽屜 | ⑧ 證據區移到內容下方（串流，不重疊） |
| <768 | 單欄：標的/模式 → 圖表 → 回放 → 活動紀錄 | 手動模擬單以 bottom sheet 開啟 |

手機 390px：

```
┌──────────────────────────────┐
│ ① 56px  品牌 │ ☰ │ 設定       │
├──────────────────────────────┤
│ ② 脈絡列（換行成兩列）        │
│    標的 · 組合 · 模式標籤      │
├──────────────────────────────┤
│ ④ 標的資訊列                  │
│ ⑤ 圖表（高度 ≥ 260px）        │
│ ⑥ 回放：[下一交易日] [下一步] │
├──────────────────────────────┤
│ ⑧ 活動紀錄（分頁，橫向可滑）  │
├──────────────────────────────┤
│ 底部導覽 4 項                 │
│  工作台 策略 組合 日誌         │
└──────────────────────────────┘
  〔手動模擬單〕→ bottom sheet：
  仍必須顯示 標的 / 投資組合 / 模擬模式 三項脈絡，
  再顯示 方向（買入/賣出）數量 → 檢核步驟 →「提交紙上訂單」
```

## 4. 容器 → 元件對照

| 容器 | 檔案 | 現況 | 動作 |
|---|---|---|---|
| ① 全站 Header | `components/shell/AppShell.tsx` | 已建（48px） | 高度改 56px；導覽四項；Settings/進階選單 |
| ② 脈絡列 | `components/shell/WorkspaceContextBar.tsx` | 已建（標的/組合/模式/來源/回放時計） | 補 dataset 性質與策略版本欄位；購買力維持「未提供」 |
| ③ 觀察列表 | `components/workspace/WatchlistPanel.tsx` | 已建 | 列高 44–48px、選中態＝底色＋側邊指示條 |
| ④ 標的資訊列 | 新增 `InstrumentHeader.tsx` | 名稱散在 DetailsPanel | 新建，取 `detail`/`bars` 既有欄位 |
| ⑤ 圖表 | `PriceChart.tsx` | 已建，range 按鈕在畫布區 | 工具列移出畫布容器；不改繪圖邏輯 |
| ⑥ 回放控制器 | 新增 `ReplayController.tsx` | 回放只在 `/experiments/[id]/simulation` | 新建，接既有 `GET /simulations/{id}` + `POST /advance` |
| ⑦ 操作面板 | 改 `OrderPreviewPanel.tsx` + 新增 `StrategyPanel.tsx` + `ActionPanel.tsx` | 詳情/下單直排；走 execution-intent 流程（提交執行器、支援 LIMIT） | 分頁化；手動單改為檢核後「提交紙上訂單」；策略頁籤唯讀顯示真實 spec |
| ⑧ 證據區 | 改 `BottomTabs.tsx` | 六個分頁、無標的範圍篩選、無計數 | 加分頁計數、「全部標的／目前標的」範圍切換與清除、買入/賣出文字、tabular-nums |
| — 研究助手 | `AIPanel.tsx` + 新增抽屜殼 | 永久佔右欄子面板（第四欄） | 改為按需右側抽屜，開關狀態入 layout；抽屜頭顯示「目前可讀取的脈絡」 |

## 5. 設計 token（source of truth：`frontend/src/app/globals.css` 的 `:root`）

### 5.1 表面與結構邊界（四層，低對比，不做卡中卡）

| 角色 | 值 | 變數 |
|---|---|---|
| Canvas 畫布 | `#0F141B` | `--canvas` |
| Surface 1 面板 | `#161D27` | `--surface-1` |
| Surface 2 raised | `#1D2733` | `--surface-2` |
| Overlay 浮層 | `#243142` | `--overlay` |
| 面板框線 1px | `#2B3746` | `--border-subtle` |
| 強調框線/focus 相鄰 | `#3B4A5C` | `--border-strong` |
| 半徑 | 面板 8 / 控制項 6 / 彈窗抽屜 12 / 徽章 4 | `--r-panel --r-control --r-modal --r-badge` |

規則：表格只畫橫向分隔線，不畫完整格線；面板標頭與內容之間只留一條分隔線；面板預設無陰影，陰影保留給浮層（選單、抽屜、彈窗）；選中態 = 底色 + 側邊指示條/下劃線，不單靠顏色；focus = 明顯 2px 外框（`--focus-ring`），不與框線混色。Figma 框不等於都要看得見的框線。

### 5.2 語意色（紅綠只留給盈虧與方向）

| 角色 | 值 | 用法 |
|---|---|---|
| primary `#4C8DFF` | 冷藍 | 產品動作色：主按鈕、選中、連結 |
| positive `#36C7A0` | 正 | 盈虧/方向為正，**必附 + 或文字** |
| negative `#FF7585` | 負 | 盈虧/方向為負，**必附 − 或文字** |
| warning `#F2C46D` | 警示 | 合成資料、stale、需注意 |
| text primary `#E7EDF5` / secondary `#B6C2D2` / muted `#8FA0B5` | 三層文字 | — |

不得把每個主按鈕都上不同顏色，否則失去動作層級。「買入」「賣出」必須保留文字，不能只用綠紅區分。以上為候選值，驗收以 WCAG AA 為目標，且需逐色對配對測試。

### 5.3 字級階層與數字

`--font-ui: Inter, "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif`；等寬字型（`--font-code`）只用給 hash、ID、程式碼。金額與表格用 tabular numerals：`.numeric { font-variant-numeric: tabular-nums lining-nums; text-align: right; }`。

| 用途 | size/line-height | weight | 變數 |
|---|---|---|---|
| H1 | 24/32 | 600 | `--fs-h1 --lh-h1` |
| H2 | 18/26 | 600 | `--fs-h2 --lh-h2` |
| H3 面板標題 | 15/22 | 600 | `--fs-h3 --lh-h3` |
| H4 | 14/20 | 600 | `--fs-h4 --lh-h4` |
| 正文 | 14/22 | 400 | `--fs-body --lh-body` |
| 輸入框（桌面/行動） | 14/20、16 | — | `--fs-input` |
| 表格 | 13/20（舒適 14/20） | 400 | `--fs-table --lh-table` |
| 說明 | 12/18 | 400 | `--fs-caption --lh-caption` |
| 關鍵價值/指標 | 28/36 | 600 | `--fs-metric --lh-metric` |
| 徽章 | 12/16 | 500 | `--fs-badge --lh-badge` |

標題規則：HTML 層級依文件結構選，不是想要多大字就選哪級；面板標題的 baseline、weight、間距保持一致；不要全部大寫加字距（尤其中文）；價格是資料值，不是 H1；表頭用 `<th>` 不用標題標籤；被截斷的 hash/ID 一定要有看完整值與複製的入口。

### 5.4 間距（4px 基）

`--sp-1..--sp-12` = 4/8/12/16/24/32/48。圖示與文字 8、標籤與輸入框 6–8、相關欄位之間 12、面板內邊距 16、任務區塊之間 24、文字頁段落之間 32。

元件尺寸：面板標頭最小 44、桌面輸入框/按鈕 40、觸控動作 44 以上、觀察列表列 44–48、表格列 舒適 44 / 密集 36、圖示 16–20（點擊範圍要更大）。

### 5.5 legacy 別名

`--bg-primary/--bg-secondary/--bg-tertiary/--bg-elevated/--border/--border-light/--accent-blue/--accent-green/--accent-red/--accent-yellow/--accent-purple` 已全部重映射到上表的新語意 token。新代碼用新 token 名；舊規則不改也能跟著收斂，避免出現第三套設計。

## 6. 元件狀態矩陣

| 元件 | default | hover | focus | disabled | loading | error | empty |
|---|---|---|---|---|---|---|---|
| 主按鈕（提交紙上訂單） | primary 底 + 白字 | primary-hover | 2px `--focus-ring` + offset | 灰底 + `not-allowed` + 原因 tooltip | 保持寬度、顯示「提交中…」、重複點擊被鎖 | 錯誤訊息在按鈕下方，不改變按鈕高度 | 不適用 |
| 中性按鈕／連結鈕 | Surface2 底 + 1px 框 | 框轉 `--border-strong` | 同上 | 文字 muted | — | — | — |
| 輸入框 / select | 底 `--canvas`、框 `--border-subtle` | 框微亮 | 框 `--primary` + 2px 外框 | muted 底 | — | 框 `--negative` + 下方文字 + `aria-invalid` | 佔位符不是 0 |
| 分頁 tab | muted 文字 + 無底 | 文字 `--text-secondary` | 同上 | — | 骨架列 | 分頁內錯誤區 | 分頁內空態 |
| 表格列 | 透明底 | `--surface-2` | 鍵盤 focus 框 | — | 骨架（行數保持） | 列級錯誤旗標 | 一句說明 + 下一步入口 + 計數為 0 的事實 |
| 模式徽章 | 依 tone 底色 + 圖示 + 文字 | 不變（非互動） | 不適用 | — | 不顯示未知為確定值 | 未知即顯示「數據模式未知」 | — |
| 觀察列表列 | 名稱 + 價 | 底色 | focus 框 | — | 骨架 | 「讀取失敗」 | 「列表是空的」+ 搜尋入口 |
| 面板標題 | 15/22 600 + 一條下框 | — | — | — | — | — | — |
| 抽屜（操作/證據/研究助手） | `--overlay` 底 + 陰影 + 12 圓角 | — | 開啟時 focus 移入，Escape 可關 | — | 「載入中…」 | 錯誤訊息 | 空態 + 脈絡保留 |

## 7. 互動原型：選股 → 回放 → 模擬操作 → 日誌 → 結果

| 步 | 動作 | 讀寫 | 脈絡更新規則 |
|---|---|---|---|
| 1 選股 | ③ 點列或 ② 搜尋選 `US:AAPL` | `GET /instruments/{id}`、`GET /instruments/{id}/bars`、`GET /watchlists` | 寫入 layout（symbol/range/面板尺寸），④⑤⑥⑦⑧ 同時更新；**未提交的手動模擬單草稿立即作廢**並明示作廢原因；證據區範圍篩選維持使用者選定（全部／目前標的），不自動跳 |
| 2 回放 | ⑥ 按「下一交易日」或「下一根 5 分鐘 K 棒」 | `GET /simulations/{session_id}`、`POST /simulations/{session_id}/advance` | 只推進綁定會話的 `current_snapshot_date`／`current_bar_timestamp`；圖表以 `end_date = 游標日` 重新請求 → **游標之後的 K 棒根本不下發**；模式徽章轉為「歷史行情回放 · 非即時」；② 回放時計同步 |
| 3 模擬操作 | ⑦ 〔手動模擬單〕填方向/數量 → 進入檢核步驟 →「提交紙上訂單」 | `POST /paper-portfolio/{pid}/orders`（Market only，帶 `idempotency_key`） | 檢核步驟要重顯 標的／投資組合／模擬模式／方向／數量／預估成本；任一欄位變動 → 檢核立即失效需重檢核；提交期間按鈕鎖定，伺服器 409 `idempotency_key_conflict` 視為既有結果而非錯誤；**不叫「提交執行器」，不提供 LIMIT 選項** |
| 4 日誌 | ⑧ 切到〔日誌〕，按某一筆 | `GET /paper-portfolio/{pid}/journal?format=json` | 開細節抽屜：策略版本、進/出場原因、成交時間 + 資料來源、成本與滑價、淨盈虧、使用者註記；**抽屜覆在版面上，導航不離開工作台**（深連結仍保留可分享的 URL） |
| 5 結果 | ⑧ 切到〔結果〕 | `GET /paper-portfolio/{pid}`（現金、已實現、未實現、估價） | 未實現為 `null` 時顯示「無資料」，不得填 0.00；每一項要標明是「市場/回放時間」還是「紀錄建立時間」；CSV 由既有 `?format=csv` 下載 |

## 8. 不可犧牲的 Alethra 特性（設計規則）

1. **脈絡完整性**：任何時刻都能辨識目前是哪一個投資組合／標的／資料集／策略版本／回放時計；換掉其中一項，相關的草稿、篩選、顯示必須有明確處置。禁止「只換圖表、留舊標的的訂單」。
2. **證據可追溯**：從任一成交／訂單／決策都能點開其策略版本、原因、時間與資料來源、成本與淨盈虧。
3. **回放不洩漏未來**：游標之後的 K 棒不得出現在圖表、tooltip、指標、區間統計或坐標軸。實作採「請求端裁窗」而非「繪圖端隱藏」。
4. **篩選要看得見**：⑧ 一定要有「全部標的／目前標的」與明顯的清除；切換標的時其他標的的紀錄不能看起來像消失了（Robinhood Legend 的教訓：接收標的切換的持倉/訂單面板會只顯示該標的）。
5. **生成文字 ≠ 規則 ≠ 證據**：研究助手產出、已存策略規則、真實執行狀態三者要視覺分隔（不同底色層 + 明確標籤），生成建議不得看起來像已存規則或成交證據。研究助手第一版不長期佔用第四欄，改為按需抽屜，且抽屜頭要顯示它目前能讀取哪些脈絡。
6. **風險與模式標籤不收合**；「未知」「無資料」「不適用」三種狀態分別表達，一律不顯示 `0.00`。
7. **漸進揭露**：⑦ 預設顯示策略名稱/版本/風險摘要/狀態，展開才顯示完整規則、成本模型、資料 checksum。

## 9. 後端能力核對（實作前查證，非記憶）

### 9.1 已存在，可直接用

| 能力 | 證據 |
|---|---|
| 回放會話游標 | `backend/app/models/simulation.py:19-52`（`data_cutoff_at`、`current_snapshot_date`、`timeframe`、`current_bar_timestamp`、`observed_days`、`total_decisions`、`state_version`）；`SimulationSessionResponse` `backend/app/api/schemas.py:217-241` |
| 逐日/逐棒推進 | `POST /api/v1/simulations/{session_id}/advance` `backend/app/api/simulations.py:828`（日線一支一步、日內走 `_advance_intraday` :850-851） |
| 組合 → 會話連結 | `PaperPortfolio.simulation_session_id` `backend/app/models/paper_portfolio.py:319`（校驗 :304-315） |
| 策略規則真實欄位 | `StrategySpec` `backend/app/api/schemas.py:19-37`；`ExperimentVersionResponse.strategy_spec` :104-117；`GET /api/v1/experiments/{id}/versions` `experiments.py:231` |
| 紙上訂單（Market only + 冪等） | `POST /api/v1/paper-portfolio/{pid}/orders` `paper_portfolio.py:379`，`idempotency_key_conflict` 409 |
| 持倉/訂單/成交/決策/日誌 | `paper_portfolio.py:358, 523, 533, 598, 950`；日誌 `?format=csv|json`（28 欄 `JOURNAL_COLUMNS` :798） |
| 成果欄位（部分） | `PaperPortfolioResponse`：`current_virtual_cash`、`total_realized_pnl`、`total_unrealized_pnl`（可為 `None`）、`valuation`（含 `mode="synthetic_fixture"`、`data_as_of`）`schemas.py:297-305` |
| 標的/資料集脈絡 | `GET /api/v1/instruments/{id}`、`.../bars?start_date&end_date`（兩個都必填）`api/instruments.py:180-189` |

### 9.2 真實缺口 → 前端處置（不假造控制）

| 缺口 | 證據 | 處置 |
|---|---|---|
| 回放只能單向推進，無回溯/跳轉/速度 | `simulations.py:828` 無 body/query 參數；`_get_next_snapshot_date` `:176-183` 嚴格 `>` 過濾 | ⑥ 只提供「下一步」與「本步已觀察到的日期」；不做拖拽式 scrubber、不做倒帶 |
| 沒有「依游標裁切 K 棒」的 endpoint，`bars` 無 `as_of` 參數 | `api/instruments.py:180-189`（僅 `start_date`/`end_date`） | 用 `end_date = 游標日` 重新請求達成裁窗；此舉是既有 contract 內的用法，不是新增能力宣稱 |
| 持倉/訂單/決策/成交**無標的篩選參數** | `paper_portfolio.py:358`（僅 `status_filter`）、`:523`、`:533`、`:598` 無 query | ⑧ 的「全部標的／目前標的」採客戶端篩選，並在列頭明確標示篩選範圍與筆數（含 0 筆的說明），不宣稱伺服器篩選 |
| 沒有 `/{pid}/results`、無權益曲線 endpoint | 不存在；`round_trips`/`daily_equity_curve` 只屬回測（`runs.py:89-90`、`experiments.py:561-564`） | 〔結果〕分頁只顯示既有欄位 + 明確註明「每日權益曲線僅回測有，紙上組合尚無」；不繪製假曲線 |
| 無「購買力」欄位 | `PaperPortfolioResponse` 無此欄 | ② 顯示「購買力 未提供」並註明虛擬現金不等於購買力 |
| 無 fill/order/decision 的 by-id 明細 endpoint | 僅 `.../orders/{order_id}/fills` `:533` | 證據抽屜用 list 回應中已有的該筆資料；缺的欄位顯示「無資料」 |
| 只有 version_id 拿不到策略 spec（需 experiment_id 遍歷） | 無 `GET /versions/{id}`；`api.ts` 僅 `versions.compare` | 〔策略〕分頁以 `GET /experiments/` + 逐項 `.../versions` 比對 `asset_ids`／會話的 `experiment_version_id`，結果快取；代價是 1+N 次請求，屬既有 contract 內做法 |
| 交易紀錄「市場時間 vs 紀錄建立時間」語意 | journal 28 欄含語意映射 `:838`；fill 有 `filled_at`/`created_at` | 〔日誌〕明確分列兩種時間，不混用 |

## 10. 驗收清單

真實瀏覽器（非 mock 截圖）逐項驗：

- 實際中文內容、**長公司名稱**（例：中國工商銀行股份有限公司 / Berkshire-like 全稱）在 ④⑤⑥⑦⑧ 不破版、不遮尾（截斷要有看全值入口）。
- 200% zoom：② 可換行、⑧ 橫向可滑、不出現元件重疊或文字消失。
- 缺價／`null` 未實現盈虧：顯示「無資料」而非 `0.00`。
- stale／斷線：模式徽章與市場狀態要如實轉態，不得殘留「即時」。
- 表格溢出：⑧ 在窄欄使用橫向捲，不壓縮數值欄。
- 鍵盤：導覽可用 Tab 走到所有動作、分頁用方向鍵、Escape 關抽屜、focus 視覺可見。
- Reload / 深連結：`/workspace` 還原標的、range、面板尺寸、分頁、證據範圍篩選。
- 跨容器情境：換標的後草稿是否作廢、提交後是否出現於 ⑧、回放游標之後是否有 K 棒洩漏（圖表、tooltip、區間統計、坐標軸都要查）。
- 色彩逐配對測到 WCAG AA（文字/底、徽章/底、focus/鄰邊），紅綠不只作單一線索。
- 驗收定位用 role/label 且**限該面板範圍**，不得用 `.first()` 解重複匹配。

## 11. 方案 B / C（輔助，共用 shell、字型、色彩與脈絡模型）

- B「研究優先雙欄」：左圖右證據、策略列在下方 — 適合 1024–1279 的收合變體思路（併入 §3）。
- C「widget 工作區」：Legend 式可配置卡片 — 作為後續方向（面板持久化已有 foundation），本輪不做拖拽編輯，避免擴增範圍。

## 12. 實作順序與提交分組（逐步，不停在規劃）

1. R1 收尾：token/字級套用到既有 `.ws-*` 與 `.dash-*`（統一密度）、header 56px、面板/輸入/列高、focus 環、`.numeric`。
2. 版面：④ 標的資訊列、⑤ 工具列分離、③ 列高與選中態。
3. ⑥ 回放控制器（綁組合的 simulation session）+ 裁窗防洩漏。
4. ⑦ 操作面板分頁（策略／手動模擬單 + 檢核 +「提交紙上訂單」+ 欄位變動作廢 + 防重複提交）。
5. ⑧ 證據區（分頁計數、全部/目前標的範圍、時間語意、證據細節抽屜、CSV 入口）+ 研究助手改抽屜。
6. 中斷點 1280/1024/768/390 + 行動 bottom sheet 保脈絡 + a11y/WCAG AA。
7. 其餘需要 AppShell 的頁面接上（`/dashboard`、`/paper-portfolio` 等），URL 與既有 `data-testid` 維持不動。

提交分組（待核准才 commit/push/PR）：
- G1 design tokens + 密度統一（`frontend/src/app/globals.css`）
- G2 shell 容器（`components/shell/*`、`app/layout.tsx` 若需）
- G3 工作台容器重構（`components/workspace/*`）
- G4 e2e / 視覺基準與文件（`e2e/*`、`scripts/visual-baseline.mjs`、本文件）

### 12.1 實作狀態紀錄（僅追加，不改動上方設計規則）

2026-10-26｜已實作並經瀏覽器驗證（`next dev` :3099、Chromium、 intercepted API）：
- 步驟 2 全部：④ `InstrumentHeader.tsx`（含「無報價」而非 0.00、性質/模式/市場/陳舊徽章、數據截至與擷取時間）、⑤ 工具列與畫布分離（`PriceChart` 圖例移除，畫布實測 800×422 有繪製）、③ 列高 44px＋`<button>` 選取＋選中態底色與左側指示條。
- 步驟 6 之部分：1024–1279 預設收合 ③（`isCompact` 媒體查詢 → 脈絡列 ☰ 開關 → `ws-watchlist-drawer`），且 ≥1280 時 ③ 以視窗寬度換算 `minSize`，實測寬 1440→229px、1280→223px，代號未被 ellipsis 截斷；390 脈絡列改可換行（`.ctx-group`／工具叢集），實測 1440/1280/1024/390 四種視窗皆無 `overflowing` 元素。
- 迴歸：`e2e/workspace.spec.ts` 新增「watchlist width: 224px floor at 1280, collapses to drawer below 1280」，11/11 通過；以 `minSize=8` 變異測試確認該守衛會失敗（實測 203.5px < 220），再還原。

尚未實作（不得視為完成）：步驟 1 的 `.dash-*` 密度統一、步驟 3–5（⑥⑦⑧＋研究助手抽屜）、步驟 6 剩餘（768/390 的 bottom sheet 保脈絡、行動 bottom nav、WCAG AA 逐對照）、步驟 7 其他頁面接 AppShell。已知缺口：390 主要導覽「投資組合」被截斷（需 bottom nav，非縮小桌面版）；⑧ 證據區在 1024 縮至 220px 未做；③ 名稱欄位仍需後端批次查詢（見 §9.2）。

### 12.2 2026-10-28 修訂：目前採用方向（僅追加，不改動上方提案）

- 目前採用的視覺方向是 `docs/design-prototypes/direction-a-content-led.html`（無框、content-led 版面；其橫幅自述「定稿」、「所有數據均為設計示例，非真實帳戶」、「Not implemented - Visual review only - 2026-10-27」）。`direction-a-mobile.html` 為其行動版對應稿。§0 所述「低對比結構框線」屬早期提案，與現行方向不同；本節不重寫歷史提案，只記錄現行採用者。
- 待 owner 視覺／使用驗收：在真實（合成）資料下的可讀性、窄視窗與行動版取用路徑，尚未經 owner 親自驗收。
- production／live 未驗證：現有畫面證據全部來自 host `next dev` 與合成／模擬資料；未經 production build，未接真實行情或經紀商。
- 最新 full backend suite 仍為 EXIT=1（既有 4 個失敗原樣保留）。
