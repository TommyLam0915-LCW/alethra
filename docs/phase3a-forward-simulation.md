# Phase 3A: Locked Research and Offline Historical Replay Simulation

**版本**: 1.0.0
**引擎版本**: Backtest 1.1.0 / Simulation 1.0.0
**日期**: 2026-10-11

---

> **方法論說明 (Methodology Clarification)**
>
> Phase 3A 的模擬引擎採用**歷史重播 (Historical Replay)** 方式：所有價格數據為預先生成的示範數據（demo data），在模擬開始前已完整存在。引擎以逐日釋放快照（snapshot-release）的方式模擬「逐步觀察」的使用者體驗，但底層數據並非即時或新到達的市場數據。
>
> Phase 3A simulation uses **Historical Replay**: all price data is pre-generated demo data that fully exists before the simulation starts. The engine releases snapshots incrementally to simulate a step-by-step observation experience, but the underlying data is not live or newly arriving market data.

---

## 1. 概述

Phase 3A 實現了 Alethra 的核心方法論原則：**策略必須在後續數據可見之前鎖定。** 鎖定後的策略規格不可修改，模擬引擎以逐日釋放快照（snapshot-release）方式，使用鎖定時已存在的示範數據進行歷史重播模擬。

### 核心原則

1. **鎖定不可逆** — 策略版本鎖定後，strategy_spec 不可修改，不可建立新版本
2. **數據截止強制** — 模擬僅能使用 `dataset_cutoff_at` 之前的數據
3. **快照逐日釋放** — 每個交易日按時間順序逐一釋放，不可跳過或重複
4. **決策可審計** — 每步決策完整記錄，包含引擎版本、截止時間、警告
5. **隱私隔離** — 模擬僅所有者可存取

---

## 2. 架構

### 2.1 鎖定流程

```
Experiment (draft)
    │
    ├── 建立 Version 1 (strategy_spec)
    ├── 可繼續建立 Version 2, 3...
    │
    └── 鎖定 → scope.status = "locked"
              ├── experiment.status = "locked"
              ├── version.locked_at = now
              ├── version.locked_by = user_id
              ├── version.strategy_config_hash = SHA-256(spec)
              ├── version.dataset_cutoff_at = end_date
              └── version.engine_version = "1.1.0"
```

鎖定後：
- PUT 更新 strategy_spec → 400 "Experiment is locked"
- POST 建立新版本 → 400 "Cannot create new version after lock"

### 2.2 模擬 Session 生命週期

```
pending → active → paused ↔ active
                  → completed (所有快照釋放完畢)
                  → retired (用戶主動終止)
```

| 狀態 | 說明 |
|------|------|
| pending | 已建立，尚未推進 |
| active | 正在推進中 |
| paused | 用戶暫停，可恢復 |
| completed | 所有快照已釋放，持倉已強制平倉 |
| retired | 用戶主動終止，決策歷史保留 |

### 2.3 快照釋放機制

```
鎖定截止日: 2024-02-13

Step 1 → 釋放 2024-01-02 (首日)
Step 2 → 釋放 2024-01-03
Step 3 → 釋放 2024-01-04
  ...
Step 30 → 釋放 2024-02-13 (末日)
Step 31 → 無更多快照 → 強制平倉 → completed
```

- 快照按 `get_trade_dates()` 返回的確定順序釋放
- 僅釋放 `start_date ≤ date ≤ end_date` 範圍內的日期
- 每次推進嚴格移動到下一個日期，不可跳過或重複

---

## 3. 數據模型

### 3.1 SimulationSession

| 欄位 | 類型 | 說明 |
|------|------|------|
| id | UUID | 主鍵 |
| experiment_version_id | UUID | 關聯的鎖定版本 |
| owner_id | UUID | 所有者 |
| initial_virtual_cash | Float | 初始虛擬資金 |
| data_cutoff_at | String(10) | 數據截止日期 |
| current_snapshot_date | String(10) | 當前已釋放的最新日期 |
| status | Enum | pending/active/paused/completed/retired |
| simulation_policy_version | String(20) | 模擬引擎版本 |
| paper_equity | Float | 當前紙本淨值 |
| current_position_json | JSONB | 當前持倉 {symbol, shares, entry_price, entry_date} |
| warnings | JSONB | 累計警告 |
| total_decisions | Integer | 總決策次數 |
| observed_days | Integer | 已觀察交易日數 |

### 3.2 SimulationDecision

| 欄位 | 類型 | 說明 |
|------|------|------|
| id | UUID | 主鍵 |
| session_id | UUID | 所屬 session |
| step_number | Integer | 步驟序號（從 1 開始） |
| snapshot_date | String(10) | 快照日期 |
| data_cutoff_at | String(10) | 數據截止日 |
| engine_version | String(50) | 引擎版本 |
| decision_type | Enum | 決策類型 |
| decision_detail | JSONB | 決策詳情 |
| paper_cash | Float | 決策後現金 |
| paper_position | JSONB | 決策後持倉 |
| paper_equity | Float | 決策後淨值 |
| warnings | JSONB | 該步警告 |

### 3.3 Decision Types

| 類型 | 說明 |
|------|------|
| no_action | 無操作（無信號觸發） |
| simulated_entry | 模擬買入執行 |
| simulated_exit | 模擬賣出執行 |
| forced_exit | 強制平倉（數據結束或持有期滿） |
| blocked_action | 被阻止的操作（如現金不足） |

---

## 4. API 端點

### 建立模擬

```
POST /api/v1/simulations/versions/{version_id}
Body: { "initial_virtual_cash": 100000 }
```

- 版本必須已鎖定
- 所有事件標記 `rules_locked: true`

### 推進模擬

```
POST /api/v1/simulations/{session_id}/advance
```

- 釋放下一個交易日快照
- 返回 session 狀態 + 該步決策詳情
- 無更多快照時：強制平倉 → 標記 completed → 返回 400 "simulation complete"

### 暫停 / 恢復 / 終止

```
POST /api/v1/simulations/{session_id}/pause
POST /api/v1/simulations/{session_id}/resume
POST /api/v1/simulations/{session_id}/retire
```

### 查詢

```
GET /api/v1/simulations/{session_id}
GET /api/v1/simulations/{session_id}/decisions
```

---

## 5. 引擎邏輯

### 5.1 ForwardSimulator

模擬引擎使用與回測引擎相同的策略邏輯，但以逐日步驟方式運行：

1. 接收截至 `snapshot_date` 的 OHLCV 數據
2. 根據策略規則計算信號
3. 若有待執行訂單（pending order），以 `snapshot_date` 開盤價執行
4. 返回 StepResult：決策類型、現金、持倉、淨值、警告

### 5.2 狀態重建

每次推進時，引擎從歷史決策重建狀態：

```python
simulator = ForwardSimulator(spec, cash, commission, slippage, cutoff, version)
for decision in session.decisions:
    ohlcv = get_demo_ohlcv(symbol, start_date, decision.snapshot_date)
    simulator.step(ohlcv, decision.snapshot_date)
# 然後執行新的一步
result = simulator.step(new_ohlcv, next_date)
```

### 5.3 強制平倉

當所有快照釋放完畢（`next_date is None`）：
- 若有未平倉位，以最後可用收盤價強制平倉
- 平倉記錄為 `forced_exit` 決策
- 然後標記 session 為 completed

---

## 6. 事件追蹤

所有模擬操作產生審計事件，統一記錄於 `audit_events` 表：

| 事件 | 觸發時機 |
|------|----------|
| simulation_created | 建立模擬 session |
| simulation_advanced | 推進一步 |
| simulation_paused | 暫停 |
| simulation_resumed | 恢復 |
| simulation_retired | 終止 |
| simulation_completed | 所有快照釋放完畢 |

所有事件標記：
- `rules_locked: true`
- `data_cutoff_at: <截止日>`
- `actor_type: human`（操作類）或 `system`（自動完成類）

---

## 7. 前端儀表板

路徑：`/experiments/[id]/simulation`

### 功能

- Session 建立表單（輸入虛擬資金）
- 狀態卡片：狀態、紙本淨值（含 % 報酬）、觀察天數、決策次數
- 當前持倉顯示（含未實現損益）
- 控制按鈕：推進、暫停、恢復、終止（根據狀態顯示）
- 決策時間軸（可展開詳情）
- 色彩編碼決策類型
- 方法論狀態標籤與免責聲明

---

## 8. 測試

### 8.1 自動化測試（10 項）

| 測試 | 驗證內容 |
|------|----------|
| TestLockImmutability | 鎖定版本的 strategy_spec 不可修改 |
| TestVersionCreationAfterLock | 鎖定後不可建立新版本 |
| TestCutoffEnforcement | 模擬不能看到截止日後的數據 |
| TestDeterministicSnapshotRelease | 快照按時間順序釋放 |
| TestNoActionStorage | no_action 決策被正確儲存 |
| TestIdempotentRelease | 推進到下一日期，不重複 |
| TestMissingPriceWarnings | 暖機期間產生數據不足警告 |
| TestPrivacy | 模擬僅所有者可存取 |
| TestRetiredHistory | 終止的 session 保留決策歷史 |
| TestDecisionMetadata | 決策包含所有必要元數據 |

### 8.2 測試結果

- **總測試數**: 68（38 既有 + 10 Phase 3A + 20 引擎）
- **通過**: 68
- **失敗**: 0
- **回歸**: 無

---

## 9. 方法論狀態標籤

| 標籤 | 說明 |
|------|------|
| historical_test_complete | 歷史回測完成 |
| locked_for_simulation | 已鎖定，可進行前瞻模擬 |
| forward_observation | 前瞻模擬進行中 |
| evidence_insufficient | 證據不足（模擬未完成或結果不顯著） |
| retired | 已終止 |

---

## 10. 限制

### Phase 3A 已知限制

1. **僅支援示範數據** — 尚不支持真實市場數據接入
2. **單一資產** — 每次模擬僅針對單一標的
3. **無自動報告生成** — 模擬完成後需手動檢視結果
4. **無多 session 比較** — 不能並排比較多個模擬結果
5. **無批量推進** — 每次只能推進一步（設計決策，確保可審計性）
6. **狀態重建為線性** — 每次推進從頭重建引擎狀態，O(n) 複雜度

### 不處理的事項

- 無真實時間推送（設計為離線模擬）
- 無隨機路徑或蒙特卡洛模擬
- 無策略參數優化
- 無多資產組合模擬

---

**文件版本**: 1.0.0
**引擎版本**: Backtest 1.1.0 / Simulation 1.0.0
**最後更新**: 2026-10-11
