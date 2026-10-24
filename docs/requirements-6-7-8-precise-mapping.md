# 需求 6/7/8 精確映射:測試斷言與程式碼路徑

**日期**: 2026-10-20<br>
**狀態**: 已驗證<br>
**分支**: auto/candidate-integration<br>
**Commit**: c1a09a3

---

## 需求 6: 確認遺失恢復 (Acknowledgement Loss Recovery)

**定義**: 平台已套用成交,但確認遺失,代理重送後平台仍只記錄一次

### 端到端測試覆蓋

#### 測試 6.1: 平台層級冪等性
**檔案**: `tests/test_execution_recovery.py:237-259`<br>
**測試**: `TestFillReportIdempotency::test_same_fill_reported_twice_returns_same_record`

**精確斷言**:
```python
# 行 246-255: 同一成交回報兩次
first = agent_report_fill(db, intent_id, user_id, quantity=10, price=150.0, fill_time=fill_time, broker_ref="IBKR-100")
second = agent_report_fill(db, intent_id, user_id, quantity=10, price=150.0, fill_time=fill_time, broker_ref="IBKR-100")

# 行 256: 返回相同記錄 ID
assert first.id == second.id

# 行 257-259: 資料庫只有一筆成交記錄
assert db.query(FillRecord).filter(
    FillRecord.order_intent_id == first.order_intent_id
).count() == 1
```

**覆蓋路徑**: 直接呼叫 `agent_report_fill` 服務函數 → 資料庫層級冪等性

---

#### 測試 6.2: 端到端重啟恢復
**檔案**: `tests/test_execution_recovery.py:288-329`<br>
**測試**: `TestFillReportIdempotency::test_restarted_agent_rereport_does_not_double_count`

**精確斷言**:
```python
# 行 307-312: 第一次回報成交 (5/10 部分成交)
resp = client.post(f"/api/v1/execution-agent/order-intents/{intent_id}/fills", json=fill_payload)
assert resp.status_code == 201

# 行 315-320: 模擬重啟:相同 payload 再次回報
resp = client.post(f"/api/v1/execution-agent/order-intents/{intent_id}/fills", json=fill_payload)
assert resp.status_code == 201

# 行 326: 狀態仍為 submitted (5/10),不是 filled (10/10)
assert db_intent.status == IntentStatus.submitted

# 行 327-329: 只有一筆成交記錄
assert db.query(FillRecord).filter(
    FillRecord.order_intent_id == db_intent.id
).count() == 1
```

**覆蓋路徑**: HTTP API → 服務層 → 資料庫層級冪等性

---

### Journal 層級測試覆蓋

#### 測試 6.3: fill_key 穩定性
**檔案**: `tests/test_fill_journal_requirements.py:195-213`<br>
**測試**: `TestAcknowledgementLossRecovery::test_fill_key_stable_across_journal_reload`

**精確斷言**:
```python
# 行 201-202: 新增成交到 journal
fill = _make_pending_fill(fill_key="test-fill-key-123")
journal.add_pending_fill(fill)

# 行 205-206: 重新載入 journal
journal2 = FillDeliveryJournal(journal_path=journal.journal_path)
loaded_fill = journal2.get_pending_fill("test-fill-key-123")

# 行 209: fill_key 保持不變
assert loaded_fill.fill_key == "test-fill-key-123"
```

**覆蓋路徑**: Journal 層級 fill_key 穩定性 (前提條件,非完整證明)

**注意**: 此測試驗證 journal 層級的 fill_key 穩定性,是需求 6 的前提條件。完整證明由測試 6.1 和 6.2 提供。

---

## 需求 7: 取消回報失敗恢復 (Cancellation Report Failure Recovery)

**定義**: 券商成功取消,但平台回報失敗,代理重試直到成功

### 端到端測試覆蓋

#### 測試 7.1: 平台取消回報失敗重試
**檔案**: `tests/test_platform_cancel_flow.py:406-473`<br>
**測試**: `TestCancellationReportFailureRecovery::test_platform_cancel_report_failure_retries_until_success`

**精確斷言**:
```python
# 行 453-454: 券商取消成功
agent.ibkr_client.cancel_order("FAKE-000001")
assert transport.get_order_status("FAKE-000001") == BrokerOrderStatus.CANCELLED

# 行 435-444: 模擬平台回報失敗
call_count = {"n": 0}
def flaky_report(intent_id, reason):
    call_count["n"] += 1
    if call_count["n"] == 1:
        raise ConnectionError("Platform unreachable")
    return original_report(intent_id, reason)
agent.alethra_client.report_order_cancelled = flaky_report

# 行 459: 執行監控器
agent._monitor_fills_for_known_order(intent_id, "FAKE-000001")

# 行 462: 驗證回報被呼叫兩次 (第一次失敗,第二次成功)
assert call_count["n"] == 2

# 行 467-469: 驗證 intent 狀態為 cancelled
row = db.query(OrderIntent).filter(OrderIntent.intent_id == intent_id).first()
assert row.status == IntentStatus.cancelled
assert row.broker_ref == "FAKE-000001"

# 行 473: 驗證 intent 被標記為完成
assert intent_id in agent._completed_intents
```

**覆蓋路徑**: 券商取消 → 監控器檢測 CANCELLED → 平台回報失敗 → 重試 → 成功

---

### 程式碼路徑覆蓋

#### 路徑 7.2: 取消失敗恢復機制
**檔案**: `app/execution_agent/agent.py:637-648`

**精確程式碼**:
```python
# 行 637-641: 嘗試回報取消
try:
    self.alethra_client.report_order_cancelled(
        intent_id=intent_id,
        reason=f"Order {status_enum.value.lower()} by broker",
    )
# 行 642-646: 失敗時重試
except Exception as e:
    logger.error(f"Failed to report cancellation: {e}")
    # Don't mark complete if cancellation report failed
    time.sleep(2)
    continue
# 行 647-648: 成功時標記完成
self._completed_intents.add(intent_id)
return
```

**行為**:
- 捕獲 `report_order_cancelled` 異常
- 不將 intent 加入 `_completed_intents`
- 等待 2 秒後繼續迴圈重試
- 直到成功回報或進程終止

---

### 補充覆蓋

#### 測試 7.3: 券商取消失敗重試
**檔案**: `tests/test_platform_cancel_flow.py:338-388`<br>
**測試**: `TestCancelRequestedContract::test_cancel_retry_after_failed_cancel_attempt`

**精確斷言**:
```python
# 行 366-373: 第一次取消嘗試失敗
calls = {"n": 0}
def flaky_cancel(broker_order_id):
    calls["n"] += 1
    if calls["n"] == 1:
        raise ConnectionError("socket closed")
    return original_cancel(broker_order_id)

# 行 381-383: 第一次嘗試後,不回報取消,不標記完成
agent._cancel_submitted_order(intent)
alethra.report_order_cancelled.assert_not_called()
assert intent.intent_id not in agent._completed_intents

# 行 386-388: 第二次嘗試成功,回報取消,標記完成
agent._cancel_submitted_order(intent)
alethra.report_order_cancelled.assert_called_once()
assert intent.intent_id in agent._completed_intents
```

**覆蓋路徑**: 券商端取消失敗 → 重試 → 成功 (與平台回報失敗不同)

---

#### 測試 7.4: 重啟恢復 (由對帳機制處理,尚未驗證)
**檔案**: `tests/test_execution_recovery.py:413-451`<br>
**測試**: `TestRestartRecovery` 類別

**相關斷言**:
```python
# test_reconciled_intent_monitors_without_resubmitting (行 425-427)
assert transport._order_counter == 1  # no resubmission
assert alethra.report_fill.call_count >= 1
assert alethra.report_order_submitted.call_count == 0

# test_lost_ack_recovered_without_resubmission (行 446-451)
assert transport._order_counter == 1  # duplicate order NOT created
assert alethra2.report_order_submitted.call_count == 1
assert alethra2.report_fill.call_count >= 1
```

**說明**: 取消回報失敗後的進程重啟恢復預期由對帳機制處理。對帳測試驗證代理重啟後能正確採用現有券商訂單狀態,但「取消回報失敗後進程重啟」的具體恢復情境尚未驗證。程式碼路徑存在不等於恢復情境已通過。

---

## 需求 8: 恢復時無重複提交 (No Duplicate Submission During Recovery)

**定義**: 恢復期間,券商提交次數維持一次

### 端到端測試覆蓋

#### 測試 8.1: 對帳不重複提交
**檔案**: `tests/test_execution_recovery.py:413-427`<br>
**測試**: `TestRestartRecovery::test_reconciled_intent_monitors_without_resubmitting`

**精確斷言**:
```python
# 行 416-419: 提交一次訂單
result = transport.submit_order("US:AAPL", "BUY", 10)
submitted = _make_intent(status="submitted", broker_ref=result.broker_order_id)
alethra.poll_order_intents.return_value = [submitted]

# 行 422-423: 對帳
agent.reconcile_on_startup()
agent.process_order_intents()

# 行 425: 只提交一次 (order counter = 1)
assert transport._order_counter == 1

# 行 427: 沒有重複報告提交
assert alethra.report_order_submitted.call_count == 0
```

**覆蓋路徑**: 對帳 → 採用現有訂單 → 不重複提交

---

#### 測試 8.2: 遺失確認恢復不重複提交
**檔案**: `tests/test_execution_recovery.py:429-451`<br>
**測試**: `TestRestartRecovery::test_lost_ack_recovered_without_resubmission`

**精確斷言**:
```python
# 行 436-440: 模擬崩潰:已提交訂單,但未回報平台
agent1, _, alethra1 = _make_agent(transport=transport)
alethra1.report_order_submitted.side_effect = Exception("network lost")
intent = _make_intent()
agent1._process_single_intent(intent)
assert transport._order_counter == 1

# 行 443-444: 新代理啟動,對帳
agent2, _, alethra2 = _make_agent(transport=transport)
agent2._process_single_intent(intent)

# 行 446: 採用現有訂單,不重複提交
assert transport._order_counter == 1

# 行 447-450: 只回報一次提交
assert alethra2.report_order_submitted.call_count == 1
assert alethra2.report_order_submitted.call_args.kwargs["broker_ref"] == (
    transport.get_recent_orders(utcnow() - timedelta(minutes=1))[0].order_id
)
```

**覆蓋路徑**: 崩潰 → 重啟 → 對帳 → 採用現有訂單 → 不重複提交

---

#### 測試 8.3: 相同 Intent 不採用其他 Intent 的訂單
**檔案**: `tests/test_execution_recovery.py:536-549`<br>
**測試**: `TestRestartRecovery::test_identical_second_intent_does_not_adopt_first_order`

**精確斷言**:
```python
# 行 543-546: 兩個相同的 intent
intent_a = _make_intent(intent_id="rec-intent-A")
intent_b = _make_intent(intent_id="rec-intent-B")
agent._process_single_intent(intent_a)
agent._process_single_intent(intent_b)

# 行 548: 第二個應該提交新訂單,不採用第一個的
assert transport._order_counter == 2
```

**覆蓋路徑**: idempotency key 隔離 → 相同 intent 不共享訂單

---

### Journal 層級測試覆蓋

#### 測試 8.4: Journal 去重
**檔案**: `tests/test_fill_journal_requirements.py:276-293`<br>
**測試**: `TestNoDuplicateOrder::test_journal_dedup_prevents_duplicate_broker_submission`

**精確斷言**:
```python
# 行 284-286: 第一次新增成功
journal1 = FillDeliveryJournal(journal_path=journal_path)
assert journal1.add_pending_fill(fill)
assert len(journal1.get_pending_fills()) == 1

# 行 289-293: 重複新增是 no-op (冪等)
assert journal1.add_pending_fill(fill)
assert len(journal1.get_pending_fills()) == 1, (
    "Duplicate add should not create second entry — "
    "this would cause duplicate broker submission"
)
```

**覆蓋路徑**: Journal 層級去重 (前提條件,非完整證明)

**注意**: 此測試驗證 journal 層級的去重,是需求 8 的前提條件。完整證明由測試 8.1、8.2、8.3 提供。

---

## 覆蓋矩陣

| 需求 | 整合測試 | Journal 測試 | 程式碼路徑 | 總計 |
|------|-----------|-------------|-----------|------|
| 6 (確認遺失) | 2 個 (6.1, 6.2) | 1 個 (6.3) | N/A | 3 個 |
| 7 (取消失敗) | 1 個 (7.1) | N/A | 1 個 (7.2) + 2 個補充 (7.3, 7.4) | 4 個 |
| 8 (無重複提交) | 3 個 (8.1, 8.2, 8.3) | 1 個 (8.4) | N/A | 4 個 |

**總計**: 6 個整合測試 + 2 個 journal 測試 + 3 個程式碼路徑 = 11 個覆蓋點

**注意**: 上述整合測試使用 TestClient + FakeBrokerTransport + MagicMock,在同一進程內執行,非獨立程序的端到端驗收。獨立程序的恢復情境驗證見 runtime gate 執行證據。

---

## 執行證據

**測試執行**: 2026-10-20<br>
**環境**:<br>
- Python 3.12
- pytest 8.3.3
- SQLite 測試資料庫

**結果**:
- `test_execution_recovery.py`: 37 通過
- `test_fill_journal_requirements.py`: 20 通過
- `test_journey_c_execution.py`: 6 通過
- `test_platform_cancel_flow.py`: 8 通過 (包含 1 個新增的取消恢復測試)
- **總計**: 71 通過,0 失敗

**基線比較**:
- 基線 (c1a09a3): 1201 通過,0 失敗
- 變更後: 1227 通過,0 失敗
- **結論**: 無回歸

---

## 誠實聲明

### Mock 邊界

1. **整合測試** (6.1, 6.2, 7.1, 8.1, 8.2, 8.3):
   - 使用 FastAPI TestClient (真實 HTTP 路由,同一進程)
   - 使用真實資料庫 (TestSession)
   - 使用 FakeBrokerTransport (模擬券商)
   - 使用 MagicMock alethra_client (模擬平台 API)
   - **非獨立程序的端到端驗收**
   - **不啟動真實 HTTP 伺服器或網路連線**

2. **Journal 測試** (6.3, 8.4):
   - 純 journal 層級測試
   - 不涉及平台或券商
   - 驗證資料結構不變量

3. **程式碼路徑** (7.2):
   - 生產程式碼路徑,非測試
   - 依賴異常處理機制

### 覆蓋缺口

1. **需求 7 重啟恢復**: 無專門測試驗證「取消回報失敗後進程重啟,journal 保留狀態」。此場景預期由對帳機制恢復,但尚未驗證。程式碼路徑存在不等於恢復情境已通過。

2. **跨進程測試**: 所有整合測試使用同一進程內的 FillDeliveryJournal 實例,非獨立 OS 進程。跨進程行為依賴 fcntl.flock (POSIX 限定),需 runtime gate 驗證。

3. **真實平台整合**: 無測試使用真實 Alethra 平台 HTTP API。所有整合測試使用 TestClient 或 MagicMock。Runtime gate 將使用獨立進程的真實 HTTP 伺服器驗證。

### Durability 表述

**已實作檔案同步,保證範圍依檔案、目錄同步與儲存環境而定**。

- `fsync` 確保資料寫入磁碟,但不保證電源故障安全
- `fcntl.flock` 提供 POSIX 跨進程鎖定,但不適用於 NFS 或 Windows
- 原子寫入 (temp + rename) 防止寫入中途崩潰導致損壞,但不保證所有檔案系統行為一致
