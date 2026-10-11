# Metrics Definitions

**日期**: 2026-10-11
**狀態**: Phase 6

---

## Pilot Funnel Metrics

| 指標 | 定義 | 資料來源 |
|------|------|----------|
| total_users | 系統中已註冊用戶總數 | `COUNT(users.id)` |
| applications_submitted | 已提交的試點申請總數 | `COUNT(pilot_applications.id)` |
| applications_approved | 狀態為 approved 的申請數 | `COUNT(pilot_applications.id WHERE status='approved')` |
| first_experiment_created | 觸發 first_experiment_created 事件的次數 | `COUNT(usage_events WHERE event_type='first_experiment_created')` |
| first_run_completed | 觸發 first_run_completed 事件的次數 | `COUNT(usage_events WHERE event_type='first_run_completed')` |
| strategy_locked | 觸發 strategy_locked 事件的次數 | `COUNT(usage_events WHERE event_type='strategy_locked')` |
| forward_simulation_started | 觸發 forward_simulation_started 事件的次數 | `COUNT(usage_events WHERE event_type='forward_simulation_started')` |
| export_generated | 觸發 export_generated 事件的次數 | `COUNT(usage_events WHERE event_type='export_generated')` |
| support_request_created | 觸發 support_request_created 事件的次數 | `COUNT(usage_events WHERE event_type='support_request_created')` |

---

## Activation Metrics

| 指標 | 定義 | 計算方式 |
|------|------|----------|
| total_signed_up | 已註冊用戶總數 | 同 `total_users` |
| activated_at_least_one_experiment | 至少建立一個實驗的不重複用戶數 | `COUNT(DISTINCT user_id WHERE event_type='first_experiment_created')` |
| activation_rate | 啟動率 | `activated_at_least_one_experiment / total_signed_up`（若無用戶則為 0.0） |
| completed_first_run | 完成首次回測的不重複用戶數 | `COUNT(DISTINCT user_id WHERE event_type='first_run_completed')` |
| completion_rate | 完成率 | `completed_first_run / total_signed_up`（若無用戶則為 0.0） |

---

## Cost Metrics

| 指標 | 定義 | 計算方式 |
|------|------|----------|
| total_cost | 所有成本記錄的總金額 | `SUM(cost_records.amount)` |
| total_actual | source=actual 的成本總和 | `SUM(cost_records.amount WHERE source='actual')` |
| total_estimate | source=estimate 的成本總和 | `SUM(cost_records.amount WHERE source='estimate')` |
| cost_per_user | 每註冊用戶的平均成本 | `total_cost / total_users`（若無用戶則為 0.0） |
| cost_per_active_user | 每活躍用戶的平均成本 | `total_cost / activated_at_least_one_experiment`（若無活躍用戶則為 0.0） |
| breakdown_by_type | 按 cost_type 分組的成本總和 | `GROUP BY cost_type → SUM(amount)` |

---

## Cost Record Fields

| 欄位 | 說明 | 驗證規則 |
|------|------|----------|
| cost_type | 成本類型 | 必須為 ai_inference, compute, data, storage, support_time, other 之一 |
| amount | 金額 | 必須 > 0 |
| currency | 貨幣 | 預設 USD |
| source | 來源 | 必須為 actual 或 estimate |
| method_of_estimation | 估算方法 | **source=estimate 時必填** |
| linked_entity | 關聯實體 | 選填，字串 |

---

## Product Hypothesis Statuses

| 狀態 | 定義 | 何時使用 |
|------|------|----------|
| untested | 尚未收集證據 | 新建假設的預設狀態 |
| evidence_emerging | 有一些證據但不足以得出結論 | 部分證據支持但未達預定義標準 |
| insufficient_evidence | 證據不足以支持或反駁 | 經過合理嘗試後仍缺乏數據 |
| contradicted | 證據反駁了假設 | 收集的證據與假設預測相反 |

**注意**：沒有「proven」或「validated」狀態。假設只能在滿足預定義證據標準後才能從 untested 轉為 evidence_emerging，但永遠不會被標記為「已證明」。

---

## Usage Event Types

| 事件 | 觸發時機 | 階段性意義 |
|------|----------|------------|
| signup_completed | 用戶完成註冊 | 漏斗頂部 |
| pilot_application_submitted | 提交試點申請 | 表達興趣 |
| first_experiment_created | 建立首個實驗 | 啟動信號 |
| first_run_completed | 完成首次回測 | 核心價值體驗 |
| strategy_locked | 鎖定策略 | 深度參與 |
| forward_simulation_started | 啟動前瞻模擬 | 進階功能使用 |
| paper_simulation_revisited | 重新訪問紙上模擬 | 留存信號 |
| second_experiment_created | 建立第二個實驗 | 重複使用 |
| ai_draft_approved | 批准 AI 草稿 | AI 功能價值 |
| export_generated | 產生匯出 | 完整工作流完成 |
| support_request_created | 建立支援請求 | 需要幫助（可能是摩擦信號） |

---

## 免責聲明

所有指標為內部試點測量，不代表商業承諾或市場驗證。

> Internal pilot pricing hypothesis — not public pricing and not market validated.

---

**文件版本**: 1.0
**最後更新**: 2026-10-11
