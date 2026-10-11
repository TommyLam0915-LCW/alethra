# Phase 6: Pilot Operations, Product Validation, and Cost Measurement

**日期**: 2026-10-11
**狀態**: 完成
**測試數量**: 170（51 個新增）

---

## 概述

Phase 6 建立試點運營基礎設施，用於收集產品驗證證據、測量成本，以及追蹤產品假設。此階段**不包含任何付款處理、定價公告或公開商業承諾**。

---

## 組件

### 1. Pilot Application Flow

用戶提交試點申請，提供以下資訊：

| 欄位 | 說明 |
|------|------|
| intended_user_type | 預期角色（individual_researcher, educator, creator, ai_builder, small_research_team） |
| current_research_workflow | 目前研究流程描述 |
| tools_currently_used | 目前使用的工具 |
| primary_pain_point | 主要痛點 |
| expected_usage_frequency | 預期使用頻率 |
| willingness_to_interview | 是否願意參與訪談 |
| consent_to_contact | 是否同意被聯繫 |

**明確排除**：
- 不收集券商帳戶資料
- 不收集敏感財務資料
- 不收集銀行帳戶或支付資訊

**API 端點**：
- `POST /api/v1/pilot/applications` — 提交申請（需認證）
- `GET /api/v1/pilot/applications/me` — 查看自己的申請
- `GET /api/v1/pilot/applications` — 列出所有申請（管理員）
- `PUT /api/v1/pilot/applications/{id}/status` — 更新狀態（管理員）

### 2. Pilot Entitlement System

非付款為基礎的權限系統：

| 欄位 | 說明 |
|------|------|
| pilot_access | 是否啟用試點存取 |
| research_run_limit | 回測執行上限 |
| ai_usage_limit | AI 使用上限 |
| export_limit | 匯出上限 |
| forward_simulation_limit | 前瞻模擬上限 |
| expiry_date | 到期日 |
| support_tier | 支援等級（standard, priority, dedicated） |

**明確排除**：
- 無 Stripe 整合
- 無付款處理
- 無 checkout 流程
- 無付款處理器金鑰

**API 端點**：
- `POST /api/v1/pilot/entitlements` — 授予權限（管理員）
- `GET /api/v1/pilot/entitlements/me` — 查看自己的權限（含到期驗證）
- `PUT /api/v1/pilot/entitlements/{id}` — 更新權限（管理員）
- `GET /api/v1/pilot/entitlements` — 列出所有權限（管理員）

### 3. Usage Events

追蹤 11 種關鍵事件類型：

| 事件類型 | 說明 |
|----------|------|
| signup_completed | 完成註冊 |
| pilot_application_submitted | 提交試點申請 |
| first_experiment_created | 建立首個實驗 |
| first_run_completed | 完成首次回測 |
| strategy_locked | 策略已鎖定 |
| forward_simulation_started | 啟動前瞻模擬 |
| paper_simulation_revisited | 重新訪問紙上模擬 |
| second_experiment_created | 建立第二個實驗 |
| ai_draft_approved | 批准 AI 草稿 |
| export_generated | 產生匯出 |
| support_request_created | 建立支援請求 |

**API 端點**：
- `POST /api/v1/pilot/usage-events` — 記錄事件（需認證）
- `GET /api/v1/pilot/usage-events/me` — 查看自己的事件
- `GET /api/v1/pilot/usage-events` — 列出所有事件（管理員）

### 4. Cost Records

記錄實際與估算成本：

| 欄位 | 說明 |
|------|------|
| cost_type | ai_inference, compute, data, storage, support_time, other |
| amount | 金額 |
| currency | 貨幣（預設 USD） |
| source | actual 或 estimate |
| method_of_estimation | 估算方法（source=estimate 時必填） |
| linked_entity | 關聯實體 |

**API 端點**：
- `POST /api/v1/pilot/costs` — 建立成本記錄（管理員）
- `GET /api/v1/pilot/costs` — 列出成本記錄（管理員）

### 5. Support Logs

追蹤支援互動：

| 欄位 | 說明 |
|------|------|
| category | 類別（onboarding, technical, billing, bug 等） |
| time_spent_minutes | 花費時間（分鐘） |
| resolution_status | open, in_progress, resolved, wont_fix |
| linked_entity | 關聯實體 |
| recurring_issue_tag | 重複問題標籤 |

**API 端點**：
- `POST /api/v1/pilot/support-logs` — 建立支援記錄（管理員）
- `PUT /api/v1/pilot/support-logs/{id}` — 更新記錄（管理員）
- `GET /api/v1/pilot/support-logs` — 列出記錄（管理員）

### 6. Admin Validation Dashboard

管理員專用的驗證儀表板，包含：

- **Pilot 漏斗**：從註冊到各里程碑的轉化
- **啟動指標**：啟動率、完成率
- **成本摘要**：總成本、每用戶成本、按類型分佈

**API 端點**：
- `GET /api/v1/pilot/admin/dashboard` — 完整儀表板（管理員）

### 7. Product Hypothesis Dashboard

追蹤產品假設及其狀態：

| 狀態 | 說明 |
|------|------|
| untested | 尚未測試 |
| evidence_emerging | 證據浮現中 |
| insufficient_evidence | 證據不足 |
| contradicted | 已被反駁 |

**明確排除**：
- 沒有「proven」或「validated」狀態
- 任何假設在沒有預定義標準的情況下不能被標記為「已證明」

**API 端點**：
- `POST /api/v1/pilot/hypotheses` — 建立假設（管理員）
- `PUT /api/v1/pilot/hypotheses/{id}` — 更新假設（管理員）
- `GET /api/v1/pilot/hypotheses` — 列出假設（管理員）
- `GET /api/v1/pilot/admin/hypotheses-dashboard` — 假設儀表板（管理員）

---

## 安全模型

### 存取控制

| 端點類型 | 存取要求 |
|----------|----------|
| 用戶自己的資料 | 需認證（`get_current_user`） |
| 管理員操作 | 需管理員（`require_admin`） |
| 公開端點 | 無 |

### 資料隔離

- 標準用戶只能查看自己的申請、權限、使用事件
- 所有管理員端點返回 403 給非管理員用戶
- 使用事件不暴露私人研究資料（無 strategy_spec、metrics 等）

---

## 前端頁面

| 頁面 | 路徑 | 說明 |
|------|------|------|
| Pilot 申請 | `/pilot` | 用戶提交試點申請、查看狀態與權限 |
| 管理員儀表板 | `/admin` | 管理員查看漏斗、啟動指標、成本、假設 |

---

## 強制免責聲明

所有儀表板回應包含：

> Internal pilot pricing hypothesis — not public pricing and not market validated.

---

## 資料庫模型

| 模型 | 資料表 |
|------|--------|
| PilotApplication | pilot_applications |
| PilotEntitlement | pilot_entitlements |
| UsageEvent | usage_events |
| CostRecord | cost_records |
| SupportLog | support_logs |
| ProductHypothesis | product_hypotheses |

---

## 測試覆蓋

51 個測試，涵蓋：

- 申請提交與重複防護
- 無券商/敏感資料收集
- 權限授予與到期執行
- 所有 11 種使用事件類型
- 成本記錄實際 vs 估算區分
- 估算方法必填驗證
- 支援記錄保護
- 假設狀態限制（無 "proven"）
- 管理員儀表板無資料狀態處理
- 除零防護
- 標準用戶存取控制
- 無付款處理器整合

---

**文件版本**: 1.0
**最後更新**: 2026-10-11
