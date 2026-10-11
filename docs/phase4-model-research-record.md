# Phase 4: Model Research Records

**版本**: 1.0.0
**引擎版本**: Backtest 1.1.0 / Simulation 1.0.0 / Paper Portfolio 1.0.0
**日期**: 2026-10-11

---

## 1. 概述

Phase 4 實現了模型研究記錄系統，讓用戶能夠結構化地追蹤研究模型的生命週期——從初始假設、版本迭代、歷史測試、鎖定、前瞻模擬觀察，到最終的證據評估與退役。

### 核心原則

1. **生命週期可追溯** — 每個模型從草稿到退役的每次狀態轉換都有記錄
2. **版本不可變性** — 鎖定後的版本不可修改，撤銷後永久不可恢復
3. **證據驅動** — 模型必須通過評估才能推進狀態，證據不足時應明確標記
4. **所有權隔離** — 用戶只能存取自己的模型記錄
5. **無交易執行** — 系統不執行任何真實交易，不提供買賣信號

### 重要聲明

> Model research records are for tracking research methodology and evidence. They do not constitute investment advice, do not generate trading signals, and do not connect to any brokerage or exchange.

---

## 2. 架構

### 2.1 資料模型

Phase 4 新增 5 個資料實體：

| 實體 | 說明 |
|------|------|
| `ModelResearchRecord` | 模型研究記錄，包含標題、目的、狀態、允許的資產範圍 |
| `ModelVersion` | 模型版本，包含模型類型、規格 JSON、鎖定/撤銷狀態 |
| `ModelEvaluation` | 模型評估，包含評估類型、指標、警告旗標、資料截止日期 |
| `PersonalResearchMemory` | 個人研究記憶，包含記憶類型、隱私範圍、確認狀態 |
| `ModelCard` | 模型卡，包含用途、限制、風險旗標、免責聲明 |

### 2.2 枚舉類型

```
ModelStatus:    draft | researching | historical_test_complete | locked_for_simulation
                | forward_observation | evidence_insufficient | retired | revoked
ModelType:      rule_based | prompt_based_research_assistant | future_external_model_reference
EvaluationType: historical | forward_simulation | methodology_review | behavior_consistency
EvaluatorType:  system | human | future_ai_assistant
MemoryType:     explicit_preference | approved_constraint | recurring_methodology_issue
                | reflection | accepted_ai_draft | rejected_ai_draft
PrivacyScope:   private_only | approved_for_personal_ai
```

### 2.3 狀態轉換規則

```
draft → researching | revoked
researching → historical_test_complete | evidence_insufficient | retired | revoked
historical_test_complete → locked_for_simulation | evidence_insufficient | retired | revoked
locked_for_simulation → forward_observation | evidence_insufficient | retired | revoked
forward_observation → evidence_insufficient | retired | revoked
evidence_insufficient → retired | revoked
retired → (terminal)
revoked → (terminal)
```

---

## 3. API 端點

### 3.1 模型研究記錄

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/model-research` | 建立研究記錄 |
| GET | `/api/v1/model-research` | 列出研究記錄（可篩選 status） |
| GET | `/api/v1/model-research/{id}` | 取得單一記錄 |
| PATCH | `/api/v1/model-research/{id}` | 更新記錄（含狀態轉換驗證） |

### 3.2 模型版本

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/model-research/{id}/versions` | 建立版本（僅 draft/researching） |
| GET | `/api/v1/model-research/{id}/versions` | 列出版本 |
| GET | `/api/v1/versions/{version_id}` | 取得單版本 |
| POST | `/api/v1/versions/{version_id}/lock` | 鎖定版本 |
| POST | `/api/v1/versions/{version_id}/revoke` | 撤銷版本（需原因） |

### 3.3 模型評估

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/model-research/{id}/evaluations` | 建立評估 |
| GET | `/api/v1/model-research/{id}/evaluations` | 列出評估 |

### 3.4 模型卡

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/model-cards` | 建立模型卡 |
| GET | `/api/v1/model-cards/{id}` | 取得模型卡 |
| PATCH | `/api/v1/model-cards/{id}` | 更新模型卡 |

---

## 4. 前端頁面

| 路徑 | 說明 |
|------|------|
| `/models` | 模型研究列表，含建立表單 |
| `/models/[id]` | 模型詳情頁，含版本、評估、模型卡分頁 |

---

## 5. 測試覆蓋

10 個測試類別、23 個測試案例：

| 類別 | 測試數 | 覆蓋範圍 |
|------|--------|----------|
| TestModelResearchPrivacy | 3 | 用戶隔離：無法存取他人的記錄或建立版本 |
| TestLockedVersionImmutable | 2 | 鎖定不可重複、鎖定後仍可讀取 |
| TestRevokedVersionPermanent | 2 | 撤銷不可重複、撤銷後不可再鎖定 |
| TestMemoryConsentPending | 2 | 未確認記憶保持 pending、已確認有時間戳 |
| TestMemoryTrainingOptInRequiresConfirmation | 2 | 未確認不能改 opt-in、已確認可以 |
| TestEvaluationRequiresValidVersion | 2 | 跨記錄版本被拒、有效版本成功 |
| TestStatusTransitionValidation | 3 | 無效轉換被拒、有效轉換接受、終端狀態不可轉換 |
| TestNoLiveExecutionEndpoints | 2 | 無信號/執行/交易端點、模型規格無執行欄位 |
| TestModelCardOwnership | 2 | 無法為他人版本建卡、有效建立成功 |
| TestConsentEnforcement | 3 | training_opt_in=True 被拒、預設 false、隱私預設 private_only |

**全部 103 個測試通過**（80 既有 + 23 Phase 4 新增）

---

## 6. 明確排除項目

Phase 4 **不**包含以下功能，且永遠不應該：

- 自主交易代理（autonomous trading agent）
- 即時信號服務（live signal service）
- 買入/賣出 API（BUY/SELL API）
- 目標權重計算（target weighting）
- 券商連接（broker connection）
- 交易所 API 金鑰處理（exchange API key handling）
- 真實資金執行（real-money execution）
