# Phase 0: 檢查與規劃

**日期**: 2026-10-11
**狀態**: 規劃完成，準備進入 Phase 1

---

## A. 產品定位與主要用戶

### 產品定位
**Alethra** — Ideas, tested. Decisions, yours.

是一個研究工作台，幫助用戶將模糊的投資想法轉化為清楚、可測試、可重現、可追蹤的研究紀錄。

**核心價值**: 不是告訴用戶「買甚麼」，而是幫他回答：
1. 我的想法是否足夠清楚？
2. 它依賴哪些證據和假設？
3. 用甚麼方法可以測試？
4. 結果是否受成本、期間、參數或資料問題影響？
5. 甚麼情況下，我應該修正或放棄這個想法？
6. 原始判斷和後續結果是否有完整紀錄？

**商業方向**: 「賣鏟子」— 提供投資研究工具，而不是出售投資結果。

### 主要用戶（第一輪試點）
**建議聚焦**: 已使用試算表或程式做研究的個人投資者

**理由**:
- 已有研究習慣，不需要從零教育
- 能理解量化研究的基本概念
- 有付費意願和能力
- 可以提供有意義的反馈

**待驗證假設**:
- [ ] 這類用戶確實需要系統化的研究紀錄工具
- [ ] 他們願意為此付費
- [ ] 他們會再次使用

**其他候選客群**（第一輪不聚焦）:
- 投資教育者及研究內容創作者
- 小型研究團隊、投資社群或學生投資組織

---

## B. 商業假設與最大風險

### 核心商業假設（全部待驗證）

1. **問題假設**: 個人投資者在研究過程中遇到版本混亂、結果不可重現、假設不清楚等問題
   - 驗證方法：用戶訪談 + 試點使用行為

2. **解決方案假設**: 系統化的實驗紀錄工具能解決這些問題
   - 驗證方法：試點用戶完成率 + 再次使用率

3. **付費意願假設**: 用戶願意為這個工具付費
   - 驗證方法：試點申請轉付費轉化率

4. **留存假設**: 用戶會持續使用，而不是用完一次就離開
   - 驗證方法：30/60/90 天留存率

5. **成本假設**: 每次實驗的直接成本（AI、資料、計算）可控，收入能覆蓋
   - 驗證方法：實際成本追蹤

6. **客群匹配假設**: 吸引的是研究導向用戶，而不是只想看高回報的人
   - 驗證方法：用戶行為分析 + 宣傳內容 A/B 測試

### 最大風險

1. **教育價值 ≠ 付費價值**
   - 用戶可能覺得「很有教育意義」但不願意付費
   - 緩解：盡早測試付費意願

2. **一次性工具風險**
   - 用戶可能只需要建立一次實驗就離開
   - 緩解：追蹤再次使用率，設計回訪機制（前瞻模擬、更新筆記）

3. **錯誤客群風險**
   - 宣傳可能吸引只想看「必勝策略」的人，導致高流失和高退款
   - 緩解：清楚定位為研究工具，不使用炫耀性語言

4. **可靠性成本風險**
   - 確保計算正確、資料一致、版本可追溯的成本可能高於預期
   - 緩解：第一版限制範圍（單一市場、日線、只做多）

5. **法律與合規風險**
   - 即使定位為研究工具，仍可能被視為投資建議
   - 緩解：清楚免責聲明 + 法律審查（待辦）

---

## C. 第一版功能及明確不做的功能

### Phase 1 功能範圍（最小可行產品）

**核心功能**:
1. ✅ 用戶註冊與登入（JWT 認證）
2. ✅ 建立實驗（手動輸入規則，不使用 AI）
3. ✅ 實驗版本管理（每次修改產生新版本）
4. ✅ 示範資料（標記為 demo，非真實市場資料）
5. ✅ 一個確定性模板：買入並持有
6. ✅ 回測計算引擎（可重現、可驗證）
7. ✅ 結果展示（累積報酬、最大回撤、波動率、交易明細）
8. ✅ 實驗卡片（狀態、版本、最近更新）
9. ✅ 核心單元測試

**明確不做**（Phase 1）:
- ❌ AI 澄清助手（Phase 2）
- ❌ 前瞻模擬（Phase 3）
- ❌ 鎖定機制（Phase 3）
- ❌ 付費功能（Phase 4）
- ❌ 真實市場資料（Phase 5）
- ❌ 團隊協作（Phase 5）
- ❌ 公開分享（Phase 5）
- ❌ 更多模板（均線、價格條件）（Phase 2+）
- ❌ 匯出功能（Phase 4）
- ❌ Admin 後台（Phase 4）

### Phase 1 限制
- 單一市場：US 股市
- 日線資料
- 單一基準貨幣：USD
- 只做多，不做空
- 不使用槓桿
- 不包含衍生工具
- 不使用任意第三方程式碼

---

## D. 建議架構與資料模型

### 技術架構

```
前端 (Next.js + TypeScript)
  ↓
API (Python FastAPI)
  ↓
資料庫 (PostgreSQL)
計算引擎 (Python)
示範資料 (CSV/JSON)
```

**理由**:
- Next.js: SSR/SSG 支援、TypeScript 原生、生態豐富
- FastAPI: 非同步、自動文檔、Pydantic 驗證
- PostgreSQL: 可靠、JSON 支援、適合複雜查詢
- Python 計算: 科學計算生態成熟（numpy, pandas）

### 核心資料模型

#### 1. User（用戶）
```
id: UUID (PK)
email: String (unique)
password_hash: String
display_name: String
user_type: Enum (individual, educator, team, student)
created_at: DateTime
updated_at: DateTime
is_active: Boolean
is_admin: Boolean
```

#### 2. Workspace（工作區）
```
id: UUID (PK)
owner_id: UUID (FK → User)
name: String
created_at: DateTime
```

#### 3. Experiment（實驗）
```
id: UUID (PK)
owner_id: UUID (FK → User)
workspace_id: UUID (FK → Workspace, nullable)
title: String
original_idea: Text
research_type: Enum (quantitative, fundamental)
hypothesis: Text
falsification_conditions: Text
benchmark: String (e.g., "SPY")
scope: JSONB (market, frequency, currency, etc.)
status: Enum (draft, exploring, locked, archived)
visibility: Enum (private, shared, public)
current_version_id: UUID (FK → ExperimentVersion)
created_at: DateTime
updated_at: DateTime
```

#### 4. ExperimentVersion（實驗版本）
```
id: UUID (PK)
experiment_id: UUID (FK → Experiment)
version_number: Integer
strategy_spec: JSONB (完整策略規格)
assumptions: JSONB
change_reason: Text
created_by: UUID (FK → User)
created_at: DateTime
locked_at: DateTime (nullable)
```

#### 5. ResearchRun（研究執行）
```
id: UUID (PK)
experiment_version_id: UUID (FK → ExperimentVersion)
dataset_snapshot_id: UUID (FK → DatasetSnapshot)
engine_version: String
config_hash: String
status: Enum (pending, running, completed, failed)
metrics: JSONB (累積報酬、最大回撤、波動率等)
warnings: JSONB
error_details: Text
started_at: DateTime
completed_at: DateTime
```

#### 6. Dataset（資料集）
```
id: UUID (PK)
name: String
provider: String
source_type: Enum (demo, licensed, user_uploaded)
license_status: String
description: Text
created_at: DateTime
```

#### 7. DatasetSnapshot（資料快照）
```
id: UUID (PK)
dataset_id: UUID (FK → Dataset)
snapshot_date: Date
data_file_path: String
checksum: String
corporate_actions_adjusted: Boolean
missing_data_notes: Text
created_at: DateTime
```

### StrategySpec 結構（JSONB）

```json
{
  "schema_version": "1.0",
  "template_type": "buy_and_hold",
  "asset_ids": ["AAPL", "MSFT"],
  "frequency": "daily",
  "start_date": "2023-01-01",
  "end_date": "2023-12-31",
  "initial_cash": 10000.0,
  "signal_parameters": {},
  "entry_rule": {
    "type": "fixed_weight",
    "weights": {"AAPL": 0.5, "MSFT": 0.5}
  },
  "exit_rule": {
    "type": "hold_to_end"
  },
  "sizing_rule": {
    "type": "fixed_fraction",
    "fraction": 1.0
  },
  "execution_timing": {
    "signal": "close_to_close",
    "execution": "next_open"
  },
  "commission_model": {
    "type": "fixed",
    "per_trade": 0.0
  },
  "slippage_model": {
    "type": "none"
  },
  "benchmark_id": "SPY",
  "currency": "USD",
  "user_approved_at": "2026-10-11T10:00:00Z"
}
```

---

## E. 示範實驗（可手動核對）

### 實驗設定
- **模板**: 買入並持有
- **股票**: AAPL, MSFT
- **期間**: 2024-01-02 至 2024-01-16（10 個交易日）
- **初始資金**: $10,000
- **權重**: AAPL 50%, MSFT 50%
- **佣金**: $0
- **滑價**: 無

### 示範資料（模擬，非真實價格）

| 日期 | AAPL Open | AAPL Close | MSFT Open | MSFT Close |
|------|-----------|------------|-----------|------------|
| 2024-01-02 | 100.00 | 101.00 | 200.00 | 202.00 |
| 2024-01-03 | 101.00 | 102.00 | 202.00 | 204.00 |
| 2024-01-04 | 102.00 | 100.50 | 204.00 | 201.00 |
| 2024-01-05 | 100.50 | 103.00 | 201.00 | 205.00 |
| 2024-01-08 | 103.00 | 104.50 | 205.00 | 207.00 |
| 2024-01-09 | 104.50 | 103.50 | 207.00 | 206.00 |
| 2024-01-10 | 103.50 | 105.00 | 206.00 | 208.00 |
| 2024-01-11 | 105.00 | 106.00 | 208.00 | 210.00 |
| 2024-01-12 | 106.00 | 105.50 | 210.00 | 209.00 |
| 2024-01-16 | 105.50 | 107.00 | 209.00 | 211.00 |

### 預期計算結果

**執行邏輯**:
1. 2024-01-02 開盤買入
   - AAPL: $5,000 / $100.00 = 50 股
   - MSFT: $5,000 / $200.00 = 25 股
   - 剩餘現金: $0

2. 持有至 2024-01-16 收盤
   - AAPL: 50 股 × $107.00 = $5,350
   - MSFT: 25 股 × $211.00 = $5,275
   - 總價值: $10,625
   - 現金: $0

3. 計算指標
   - **累積報酬**: ($10,625 - $10,000) / $10,000 = 6.25%
   - **年化報酬**: 6.25% × (365 / 14) ≈ 162.9% (僅 14 天，年化不具參考價值)
   - **最大回撤**: 需計算每日淨值曲線
   - **波動率**: 需計算每日報酬標準差
   - **交易次數**: 2 (AAPL + MSFT)

**手動核對清單**:
- [ ] 初始資金分配正確
- [ ] 股數計算正確
- [ ] 最終價值計算正確
- [ ] 累積報酬計算正確
- [ ] 每日淨值曲線正確
- [ ] 最大回撤計算正確

---

## F. 分階段開發計劃

### Phase 0: 檢查與規劃 ✅ (已完成)
**交付物**:
- [x] 定位與用戶分析
- [x] 商業假設清單
- [x] 功能範圍定義
- [x] 架構與資料模型
- [x] 示範實驗設計
- [x] 阻塞問題清單

### Phase 1: 不用 AI 也能研究 (進行中)
**目標**: 建立核心研究功能，用戶可以手動建立規則並執行回測

**交付物**:
- [ ] 專案基礎結構（Docker、套件設定）
- [ ] 資料庫模型與 migration
- [ ] JWT 認證系統
- [ ] 實驗 CRUD API
- [ ] 實驗版本管理
- [ ] 示範資料產生器
- [ ] 買入並持有模板
- [ ] 回測計算引擎
- [ ] 績效指標計算
- [ ] 前端：Landing, Login, Dashboard, Experiment Detail
- [ ] 核心單元測試
- [ ] README 與啟動說明

**驗收標準**:
- 用戶可以註冊並登入
- 用戶可以建立實驗（買入並持有）
- 系統可以執行回測並產生結果
- 結果可以手動核對
- 所有測試通過

### Phase 2: AI 澄清助手
**目標**: 加入 AI 協助澄清模糊想法，產生結構化規則

**交付物**:
- [ ] AI provider adapter（支援 mock + 真實模型）
- [ ] 澄清對話流程
- [ ] Schema 驗證
- [ ] 用戶批准流程
- [ ] 結果解釋功能
- [ ] 更多模板：均線策略、價格條件策略

**依賴**: Phase 1 完成

### Phase 3: 紀錄與前瞻模擬
**目標**: 支援鎖定實驗、開始前瞻模擬、研究筆記

**交付物**:
- [ ] 鎖定機制
- [ ] Job 處理系統
- [ ] 模擬帳本
- [ ] Idempotency 保證
- [ ] 前瞻模擬引擎
- [ ] 研究筆記功能
- [ ] 回訪追蹤

**依賴**: Phase 2 完成

### Phase 4: 付費試點支援
**目標**: 支援試點申請、方案權限、成本追蹤、Admin 後台

**交付物**:
- [ ] 試點申請表單
- [ ] Mock 方案權限系統
- [ ] 成本紀錄功能
- [ ] 支援紀錄功能
- [ ] Admin 後台（漏斗、使用量、成本）
- [ ] 匯出功能（PDF/CSV）

**依賴**: Phase 3 完成

### Phase 5: 通過需求驗證後才考慮
**目標**: 根據試點結果決定是否開發進階功能

**候選功能**:
- 真實市場資料服務整合
- 真實付款系統（Stripe）
- 團隊協作功能
- 公開實驗分享
- 更多策略模板
- 正式生產環境部署

**依賴**: Phase 4 完成 + 試點結果驗證

---

## G. 需要創辦人決定的阻塞問題

### 立即需要決定

1. **法律審查**
   - 問題：即使定位為研究工具，仍可能被視為投資建議
   - 需要決定：是否在 Phase 1 上線前進行法律審查？
   - 建議：是，至少確認免責聲明是否足夠

2. **示範資料來源**
   - 問題：Phase 1 使用模擬資料還是真實歷史資料？
   - 選項 A：完全模擬（簡單，但說服力低）
   - 選項 B：使用免費歷史資料 API（如 Yahoo Finance）
   - 需要決定：Phase 1 使用哪種？
   - 建議：選項 B，取得真實資料但標記為 demo

3. **試點用戶招募**
   - 問題：Phase 1 完成後，如何招募試點用戶？
   - 需要決定：創辦人是否已有潛在用戶管道？
   - 建議：盡早開始招募，Phase 2 完成時開始試點

### 可以稍後決定

4. **定價策略**
   - 問題：個人訂閱 vs 團隊訂閱 vs 按次付費，哪個先推出？
   - 可以等到 Phase 4 再決定

5. **資料供應商**
   - 問題：使用哪個付費資料供應商？
   - 可以等到 Phase 5 再決定

6. **生產環境部署**
   - 問題：使用哪個雲端服務商？
   - 可以等到 Phase 5 再決定

---

## 已知限制與風險

### 技術限制
1. Phase 1 僅支援日線資料，不支援日內交易
2. 僅支援 US 股市，不支援其他市場
3. 僅支援買入並持有模板，不支援複雜策略
4. 僅支援做多，不支援做空或槓桿
5. 不處理股息和公司行動（第一版）

### 資料限制
1. 示範資料可能與真實市場有差異
2. 不保證資料完整性（可能有缺失）
3. 不保證資料準確性（可能有錯誤）

### 合規風險
1. 即使定位為研究工具，仍可能被視為投資建議
2. 需要清楚免責聲明
3. 需要法律審查（待辦）

### 商業風險
1. 用戶可能只需要一次性工具
2. 教育價值可能不等於付費價值
3. 宣傳可能吸引錯誤客群

---

## 下一步

1. ✅ 完成 Phase 0 規劃文件
2. 🔄 開始 Phase 1 開發
3. ⏳ 建立專案基礎結構
4. ⏳ 實作核心功能
5. ⏳ 撰寫測試
6. ⏳ 準備試點用戶招募

---

**文件版本**: 1.0
**最後更新**: 2026-10-11
