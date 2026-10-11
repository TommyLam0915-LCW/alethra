# Phase 3B: Virtual Portfolio, Paper Orders, Expectations, and Outcome Tracking

**版本**: 1.0.0
**引擎版本**: Backtest 1.1.0 / Simulation 1.0.0 / Paper Portfolio 1.0.0
**日期**: 2026-10-11

---

## 1. 概述

Phase 3B 實現了虛擬投資組合（Paper Portfolio）系統，讓用戶在鎖定策略並完成前瞻模擬後，可以記錄模擬交易決策、追蹤預期結果與實際結果的差異。此系統明確定位為**研究與行為追蹤工具**，而非交易遊戲或模擬器。

### 核心原則

1. **固定虛擬資金** — 初始資金設定後不可追加、不可重置
2. **風險控制** — 可配置的風控限制（持倉比例、每日單量、允許資產）
3. **無操作記錄** — 系統必須記錄「無操作」決策，避免暗示用戶必須頻繁操作
4. **先有預期再有結果** —  qualifying action 前必須記錄預期結果、時間框架、否決條件
5. **覆寫分離** — 覆寫請求不改變鎖定策略，獨立記錄、獨立追蹤
6. **無遊戲化** — 無排行榜、無徽章、無連續操作獎勵、無社交功能

### 重要聲明

> Paper trading records simulated execution under stated assumptions. It does not reproduce real-money psychology, full market liquidity, partial fills, exchange outages, market impact, or actual brokerage execution.

---

## 2. 架構

### 2.1 資料模型

Phase 3B 新增 6 個資料實體：

| 實體 | 說明 |
|------|------|
| `PaperPortfolio` | 虛擬投資組合，包含初始資金、當前現金、狀態 |
| `PaperPosition` | 持倉記錄，包含資產、數量、均價、已實現/未實現損益 |
| `PaperOrder` | 訂單記錄，包含資產、方向、數量、狀態、風控結果 |
| `PaperFill` | 成交記錄，包含觀測價格、成交價格、滑價、佣金 |
| `DecisionRecord` | 決策記錄，包含決策類型、理由、預期結果、信心程度、否決條件 |
| `OutcomeEvaluation` | 結果評估，包含實際結果、假設驗證狀態、用戶反思 |

### 2.2 枚舉類型

```
PortfolioStatus:    active | paused | retired | completed
PositionStatus:     open | closed
OrderSide:          BUY | SELL
OrderStatus:        pending | filled | cancelled | rejected
OrderType:          market | limit
RiskCheckStatus:    passed | failed | override_requested
PaperDecisionActorType: human | ai_draft | human_approved_ai_draft | system
PaperDecisionType:  no_action | entry | exit | pause | retire | override_request
ConfidenceLevel:    low | medium | high | not_provided
OutcomeStatus:      pending | evaluated | expired
HypothesisStatus:   supported | partially_supported | evidence_insufficient | contradicted
```

### 2.3 API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/paper-portfolio` | 建立投資組合 |
| GET | `/api/v1/paper-portfolio` | 列出所有投資組合 |
| GET | `/api/v1/paper-portfolio/{id}` | 取得投資組合詳情 |
| GET | `/api/v1/paper-portfolio/{id}/positions` | 列出持倉 |
| POST | `/api/v1/paper-portfolio/{id}/orders` | 建立訂單（含風控 + 決策記錄 + 成交模擬） |
| GET | `/api/v1/paper-portfolio/{id}/orders` | 列出訂單 |
| GET | `/api/v1/paper-portfolio/{id}/orders/{oid}/fills` | 列出成交 |
| POST | `/api/v1/paper-portfolio/{id}/decisions` | 記錄決策（含 no_action） |
| GET | `/api/v1/paper-portfolio/{id}/decisions` | 列出決策 |
| POST | `/api/v1/paper-portfolio/{id}/decisions/{did}/outcome` | 評估結果 |
| POST | `/api/v1/paper-portfolio/{id}/override-requests` | 建立覆寫請求 |
| POST | `/api/v1/paper-portfolio/{id}/pause` | 暫停投資組合 |
| POST | `/api/v1/paper-portfolio/{id}/resume` | 恢復投資組合 |
| POST | `/api/v1/paper-portfolio/{id}/retire` | 退役投資組合 |

---

## 3. 行為規則

### 3.1 固定虛擬資金 (Rule A)

- 初始資金在建立時設定，之後不可追加
- 無「充值」或「重置」端點
- 資金僅能通過訂單成交增減
- 退役後建立新投資組合是唯一的「重新開始」方式

**測試**: `TestVirtualCapitalCannotGoNegative`, `TestNoMidSessionCapitalReset`

### 3.2 風險控制 (Rule B)

預設風控配置：

```python
RiskCheckConfig(
    max_position_pct=1.0,       # 單一持倉最大佔比
    max_daily_orders=10,        # 每日最大訂單數
    allowed_assets=["DEMO-ALPHA", "DEMO-BETA", "DEMO-BENCHMARK"],
    max_drawdown_warning_pct=0.20,  # 最大回撤警告
)
```

風控檢查順序：
1. 投資組合狀態（paused 阻止所有訂單）
2. 資產是否在允許清單
3. 賣出數量是否超過持有量
4. 每日訂單數是否超過上限
5. 買入持倉是否超過最大佔比

**測試**: `TestOrderBlockedWhenRiskLimitExceeded`

### 3.3 無操作記錄 (Rule C)

系統允許並鼓勵記錄 `no_action` 決策：

```json
POST /api/v1/paper-portfolio/{id}/decisions
{
    "actor_type": "human",
    "decision_type": "no_action",
    "rationale": "Market conditions unclear, choosing to wait",
    "self_reported_confidence": "medium"
}
```

此設計避免暗示用戶必須頻繁操作。

**測試**: `TestNoActionCorrectlyRecorded`

### 3.4 先有預期再有結果 (Rule D)

建立 `entry` 或 `exit` 決策時，必須提供：

- `rationale` — 決策理由
- `expected_outcome` — 預期結果
- `expected_timeframe_days` — 預期時間框架（天）
- `falsification_condition` — 否決條件
- `self_reported_confidence` — 信心程度

結果評估 (`OutcomeEvaluation`) 必須連結到既有決策，不可獨立建立。

**測試**: `TestExpectedOutcomeRecordedBeforeAction`, `TestOutcomeEvaluationLinksToDecision`

### 3.5 覆寫分離 (Rule E)

覆寫請求：
- 建立 `override_request` 類型的決策記錄
- 標記 `risk_flags.is_override = true` 和 `risk_flags.outside_locked_strategy = true`
- 不修改鎖定策略的決策記錄
- 審計事件標記 `rules_locked = true`

覆寫決策與鎖定策略決策分開追蹤，績效獨立計算。

**測試**: `TestLockedStrategyAndOverrideSeparate`

### 3.6 無遊戲化 (Rule F)

系統不包含：
- 排行榜或排名
- 徽章或成就
- 連續操作獎勵或「連續操作天數」
- 公開投資組合
- 社交複製或跟單功能
- 「頂級交易者」標籤

---

## 4. 成交模擬

### 4.1 價格模型

成交使用示範數據的 OHLCV 資料：

- **買入**: 執行價格 = 當日開盤價 × (1 + 滑價率)
- **賣出**: 執行價格 = 當日開盤價 × (1 - 滑價率)

預設參數：
- 滑價率: 0.1% (0.001)
- 佣金率: 0.1% (0.001)

### 4.2 成本計算

```
買入:
  觀測價格 = open
  執行價格 = open × 1.001
  總金額 = 數量 × 執行價格
  佣金 = 總金額 × 0.001
  現金變化 = -(總金額 + 佣金)

賣出:
  觀測價格 = open
  執行價格 = open × 0.999
  總金額 = 數量 × 執行價格
  佣金 = 總金額 × 0.001
  現金變化 = 總金額 - 佣金
```

### 4.3 現金保護

買入訂單在成交前檢查：

```python
if portfolio.current_virtual_cash + net_cash_change < 0:
    raise HTTPException(status_code=400, detail="Insufficient cash for order")
```

此檢查確保虛擬資金不會變為負數。

**測試**: `TestSimulatedFillIncludesCosts`

---

## 5. 前端介面

### 5.1 投資組合列表頁

路徑: `/paper-portfolio`

功能：
- 顯示所有投資組合卡片
- 建立新投資組合表單（初始資金輸入）
- 狀態徽章（active / paused / retired）
- 免責聲明橫幅

### 5.2 投資組合詳情頁

路徑: `/paper-portfolio/[id]`

四個分頁：

**Overview 分頁**:
- 新訂單表單（資產、方向、數量、信心程度、理由*、預期結果*、時間框架、否決條件）
- 當前持倉表格

**Positions 分頁**:
- 所有持倉列表（含已平倉）
- 狀態徽章（open / closed）
- 損益顯示

**Orders 分頁**:
- 訂單歷史
- 方向顏色標記（BUY 綠 / SELL 紅）
- 狀態顯示

**Decisions 分頁**:
- 決策日誌
- 決策類型徽章
- _actor type_ 顯示
- 覆寫旗標
- 信心程度、理由、預期結果、否決條件

會話控制：
- 暫停 / 恢復 / 退役按鈕（根據當前狀態顯示）

---

## 6. 隱私與安全

### 6.1 所有權檢查

所有投資組合操作都驗證所有權：

```python
def _get_portfolio_with_ownership(portfolio_id, db, current_user):
    portfolio = db.query(PaperPortfolio).filter(PaperPortfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if portfolio.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return portfolio
```

**測試**: `TestPrivateUserCannotReadOtherPortfolio`

### 6.2 退役不可變

退役後的投資組合：
- 不可建立新訂單
- 不可暫停或恢復
- 不可再次退役
- 持倉和決策記錄保留供查閱

**測試**: `TestRetiredPortfolioImmutable`

### 6.3 無外部經紀商欄位

Schema 和資料模型不包含任何經紀商或交易所相關欄位：

- 無 `api_key`, `secret`, `credential`
- 無 `broker`, `exchange`, `binance`, `coinbase`
- 無外部 API 整合

**測試**: `TestNoExternalBrokerFields`

---

## 7. 審計追蹤

所有投資組合操作都會發出審計事件：

| 事件 | 說明 |
|------|------|
| `portfolio_created` | 投資組合建立 |
| `order_created` | 訂單建立（含資產、方向、數量） |
| `decision_recorded` | 決策記錄（含類型、actor type） |
| `outcome_evaluated` | 結果評估（含假設狀態） |
| `override_requested` | 覆寫請求（標記 `rules_locked = true`） |
| `portfolio_paused` | 投資組合暫停 |
| `portfolio_resumed` | 投資組合恢復 |
| `portfolio_retired` | 投資組合退役（含最終現金） |

---

## 8. 測試

Phase 3B 包含 12 個測試，覆蓋所有行為規則：

| # | 測試類別 | 測試項目 |
|---|----------|----------|
| 1 | `TestVirtualCapitalCannotGoNegative` | 虛擬資金不可為負 |
| 2 | `TestNoMidSessionCapitalReset` | 不可中途充值 |
| 3 | `TestOrderBlockedWhenRiskLimitExceeded` | 風控限制阻止訂單（暫停、禁止資產） |
| 4 | `TestNoActionCorrectlyRecorded` | 無操作正確記錄 |
| 5 | `TestLockedStrategyAndOverrideSeparate` | 鎖定策略與覆寫分離 |
| 6 | `TestExpectedOutcomeRecordedBeforeAction` | 預期結果在 qualifying action 前記錄 |
| 7 | `TestOutcomeEvaluationLinksToDecision` | 結果評估連結到決策 |
| 8 | `TestSimulatedFillIncludesCosts` | 成交包含佣金和滑價 |
| 9 | `TestPrivateUserCannotReadOtherPortfolio` | 隱私保護 |
| 10 | `TestRetiredPortfolioImmutable` | 退役後不可變 |
| 11 | `TestNoExternalBrokerFields` | 無外部經紀商欄位 |

**測試檔案**: `backend/tests/test_paper_portfolio.py`

---

## 9. 已知限制

Phase 3B 的紙上交易有以下限制，詳見 `docs/paper-trading-limitations.md`：

1. 不重現真實資金心理
2. 不重現完整市場流動性
3. 不重現部分成交
4. 不重現交易所宕機
5. 不重現市場衝擊
6. 不重現實際經紀商執行

> Paper trading records simulated execution under stated assumptions. It does not reproduce real-money psychology, full market liquidity, partial fills, exchange outages, market impact, or actual brokerage execution.

---

**文件版本**: 1.0
**最後更新**: 2026-10-11
