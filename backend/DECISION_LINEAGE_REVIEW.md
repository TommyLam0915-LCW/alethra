# Decision Lineage Review Package

> **已被取代（2026-11-17）**：狀態來源是 `alethra/EXECUTION_TABLE_20261117.md` 的「平行摘要取代與更正」節。
> 特别注意本檔 §二 的結論「來源可追溯性由 absence of deletion 保護／UsageRecord 是 append-only」**不成立**：
> 未找到刪除路徑 ≠ 強制不可刪，且 `usage_records.user_id` 的 FK 方向不反向保護 usage row。
> §五.3 的內联驗證片段（允許 NULL `skill_unit_id` 通過）已非現行程式；現行由 `app/services/decision_lineage.py:67`–`:80` 明確拒絕缺失／NULL。
> §八「Expected: 8 passed／6 passed」已過期（實測 26 passed、9 passed）。保留未刪除（未追蹤檔，刪除不可逆）。

## 一、成果標記

### 已完成
- ✅ Decision→inference 穩定 UUID 關聯實作
- ✅ 8 個 lineage 測試通過（SQLite）
- ✅ Migration 檔案撰寫完成
- ✅ Validation logic 實作（cross-owner/cross-skill/type 拒絕）
- ✅ Inference metadata 包含 skill_unit_id
- ✅ Predict endpoint 回傳 inference_id

### 未證明
- ❌ Migration 未執行（PostgreSQL）
- ❌ 正式客戶閉環未驗證
- ❌ Production readiness 未證明
- ❌ 成功推論案例（MarketDataBar fixture + loadable artifact）未完成
- ❌ Paper create/engine 一致性未驗證
- ❌ Selection/activation UI 未完成

## 二、Usage Record 生命週期核對

### 發現
搜尋整個 backend 程式碼，**沒有任何刪除 UsageRecord 的路徑**：
- 無 `session.delete(usage_record)`
- 無 `DELETE FROM usage_records` SQL
- 無 retention/purge/cleanup 邏輯
- 無 API endpoint 刪除 usage records

### 結論
Decision lineage 的來源可追溯性由 **absence of deletion** 保護。
UsageRecord 是 append-only 的帳本記錄，一旦寫入即永久存在。

### 風險
若未來新增刪除邏輯（例如 GDPR 請求、資料清理），必須：
1. 檢查是否有 decision_records.linked_inference_id 引用該 record
2. 拒絕刪除或改為 NULLIFY linked_inference_id（破壞 lineage）
3. 或保留 record 但清除 metadata 中的 PII

**目前無此風險，因為無刪除路徑存在。**

## 三、Lineage 契約驗證

### 測試覆蓋（8 項）

| # | 測試項目 | 驗證內容 | 狀態 |
|---|---------|---------|------|
| T1 | Decision can link to specific inference | 同 owner/skill 的 inference 可被引用 | ✅ PASS |
| T2 | Two inferences → decision points to one | 決策指向明確的單一 inference | ✅ PASS |
| T3 | Later model switch doesn't change source | 模型切換不影響歷史決策來源 | ✅ PASS |
| T4 | Cross-owner inference refused | 跨帳號引用返回 404 | ✅ PASS |
| T5 | Cross-skill inference refused | 跨 skill unit 引用返回 400 | ✅ PASS |
| T6 | Non-personal_decision inference refused | 非推論類型的 usage 返回 400 | ✅ PASS |
| T7 | Non-existent inference refused | 不存在的 inference 返回 404 | ✅ PASS |
| T8 | Decision without link still works | 向後相容（無連結亦可） | ✅ PASS |

### 驗證層級
- ✅ Create schema 接收 linked_inference_id
- ✅ Validation 檢查 existence/ownership/type/skill
- ✅ ORM 真正保存（T1/T2/T3 驗證）
- ✅ Response 讀回（T1 驗證）
- ✅ 查詢讀回（T2/T3 驗證）
- ❌ 匯出功能（未實作，不在本次範圍）

### 明確標示
**Non-personal_decision inference 拒絕** 是目前正式支援範圍。
- `prediction_type="personal_decision"` 是必要條件
- 研究版本（research_backtest）、決策模型（personal_decision）是不同物件
- 測試 T6 明確驗證此區分

## 四、Migration 全文附件

### 附件 A：b2c3d4e5f6g7_add_skill_unit_associations.py

```python
"""add skill-unit association columns for unified tracing

Revision ID: b2c3d4e5f6g7
Revises: f2a3b4c5d6e7
Create Date: 2026-11-17

[完整內容見 migrations/versions/b2c3d4e5f6g7_add_skill_unit_associations.py]

Adds:
- paper_trading_runs.strategy_version_id (UUID, nullable, indexed)
- paper_trading_runs.dataset_id (UUID, nullable, indexed)
- personal_model_selections.skill_unit_id (UUID, nullable, indexed)
- agent_model_activation_approvals.skill_unit_id (UUID, nullable, indexed)

All NULLABLE, no FK constraints at DB level.
Application validates at write time.
"""
```

### 附件 B：c3d4e5f6g7h8_add_decision_inference_link.py

```python
"""add linked_inference_id to decision_records for model-execution traceability

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-11-17

[完整內容見 migrations/versions/c3d4e5f6g7h8_add_decision_inference_link.py]

Adds:
- decision_records.linked_inference_id (UUID, nullable, indexed)

NULLABLE because existing decisions cannot be retroactively linked.
No FK constraint at DB level (cross-schema coupling).
Application validates existence/ownership/type/skill at write time.
"""
```

## 五、關鍵 Diff 摘要

### 1. Model Schema
**File**: `app/models/paper_portfolio.py:221`
```python
linked_inference_id = Column(UUID(as_uuid=True), nullable=True, index=True)
```

### 2. API Schemas
**File**: `app/api/schemas.py`
- Line 699: `linked_inference_id: Optional[UUID] = None` (DecisionRecordCreate)
- Line 720: `linked_inference_id: Optional[UUID]` (DecisionRecordResponse)

### 3. Validation Logic
**File**: `app/api/paper_portfolio.py:986-1020`
```python
if payload.linked_inference_id is not None:
    usage_record = db.query(UsageRecord).filter(
        UsageRecord.id == payload.linked_inference_id,
        UsageRecord.user_id == current_user.id,
    ).first()
    if usage_record is None:
        raise HTTPException(status_code=404, detail="inference_not_found ...)
    
    metadata = usage_record.metadata_json or {}
    if metadata.get("prediction_type") != "personal_decision":
        raise HTTPException(status_code=400, detail="inference_not_model_execution", ...)
    
    inference_skill_unit_id = metadata.get("skill_unit_id")
    if inference_skill_unit_id is not None and inference_skill_unit_id != str(portfolio.id):
        raise HTTPException(status_code=400, detail="inference_skill_mismatch", ...)
```

### 4. Inference Metadata
**File**: `app/api/personal_models.py:602-629`
```python
usage_record = record_api_call_no_commit(
    db,
    user_id=current_user.id,
    endpoint="/api/v1/ml/personal/models/{version_id}/predict",
    model=str(version.id),
    tokens_used=0,
    cost=0,
    metadata={
        "prediction_type": "personal_decision",
        "skill_unit_id": str(body.skill_unit_id) if body.skill_unit_id else None,
        "model_version_id": str(version.id),
        "artifact_id": artifact_id,
        "artifact_sha256": spec.get("artifact_sha256"),
        ...
    },
)
...
return {
    ...
    "inference_id": str(usage_record.id),
}
```

## 六、完整待套用鏈

```
f2a3b4c5d6e7 (base)
    ↓
b2c3d4e5f6g7 (add skill-unit associations)
    ↓
c3d4e5f6g7h8 (add decision→inference link)
    ↓
[HEAD]
```

### 起始 Revision
`f2a3b4c5d6e7` (add_research_run_idempotency)

### 目標 Revision
`c3d4e5f6g7h8` (add_decision_inference_link)

## 七、固定環境規格

### Database
- **Type**: PostgreSQL 15+ (production), SQLite 3.35+ (tests)
- **Port**: 5432 (default)
- **Socket**: /tmp/.s.PGSQL.5432 (macOS)
- **PGDATA**: 由 pgserver 管理
- **Roles**: 
  - `alethra` (owner)
  - `alethra_test` (test runner)

### Python
- **Version**: 3.12.16
- **Venv**: `.venv/` (backend)
- **Key packages**:
  - SQLAlchemy 2.0.x
  - Alembic 1.14.0
  - FastAPI 0.115.x
  - pytest 8.3.3

### Node.js
- **Version**: 24.18.0 (system), 18.20.8 (user-local)
- **Frontend**: React + TypeScript

### 資源上限
- **Test timeout**: 120s per test
- **Memory**: 4GB (test runner)
- **Disk**: 10GB (PGDATA + artifacts)

## 八、執行與驗證腳本

### 腳本 1：套用 Migration（PostgreSQL）
```bash
#!/bin/bash
set -euo pipefail

cd /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend

echo "=== Current revision ==="
.venv/bin/alembic current

echo "=== Applying migrations ==="
.venv/bin/alembic upgrade head

echo "=== Verify final revision ==="
.venv/bin/alembic current

echo "=== Expected: c3d4e5f6g7h8 ==="
```

### 腳本 2：執行 Lineage 測試
```bash
#!/bin/bash
set -euo pipefail

cd /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend

echo "=== Running decision lineage tests ==="
.venv/bin/python -m pytest tests/test_decision_lineage.py -v

echo "=== Expected: 8 passed ==="
```

### 腳本 3：驗證 Authorization 測試未回歸
```bash
#!/bin/bash
set -euo pipefail

cd /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend

echo "=== Running authorization tests ==="
.venv/bin/python -m pytest tests/test_formal_skill_unit_authorization_endpoint.py -v

echo "=== Expected: 6 passed ==="
```

### 腳本 4：停止線（失敗即停）
```bash
#!/bin/bash
set -euo pipefail

cd /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend

echo "=== Pre-flight checks ==="

# Check 1: Migration chain integrity
echo "Checking migration chain..."
.venv/bin/alembic heads | grep -q "c3d4e5f6g7h8" || {
    echo "FAIL: Migration chain broken"
    exit 1
}

# Check 2: Tests pass
echo "Running lineage tests..."
.venv/bin/python -m pytest tests/test_decision_lineage.py -q || {
    echo "FAIL: Lineage tests failed"
    exit 1
}

# Check 3: No regression
echo "Running authorization tests..."
.venv/bin/python -m pytest tests/test_formal_skill_unit_authorization_endpoint.py -q || {
    echo "FAIL: Authorization tests regressed"
    exit 1
}

echo "=== All checks passed ==="
```

## 九、Git 保存範圍

### 必須包含
1. `migrations/versions/b2c3d4e5f6g7_add_skill_unit_associations.py`
2. `migrations/versions/c3d4e5f6g7h8_add_decision_inference_link.py`
3. `app/models/paper_portfolio.py` (linked_inference_id column)
4. `app/api/schemas.py` (DecisionRecordCreate/Response)
5. `app/api/paper_portfolio.py` (validation logic)
6. `app/api/personal_models.py` (inference metadata)
7. `tests/test_decision_lineage.py` (8 tests)

### 依賴關係
- Migration B 依賴 Migration A（revision chain）
- API validation 依賴 model schema
- Tests 依賴所有上述元件
- **不可只提交 API 片段而遗漏 schema/model**

## 十、剩餘缺口

### 高優先（本次任務範圍）
1. **成功推論案例**
   - 需要 MarketDataBar fixture
   - 需要 loadable artifact（matching loader contract）
   - 需要通過正式 predict endpoint 取得 200 + actual prediction

2. **Paper create/engine 一致性**
   - 驗證 request version/dataset → validation → engine uses → run saves → query reads
   - 需要實際執行 paper trading run 並驗證資料流

3. **Selection/activation UI**
   - Frontend components for skill unit selection
   - Client-side validation
   - Integration with backend API

### 中優先（後續任務）
4. **匯出功能**
   - Decision export with inference lineage
   - CSV/JSON format
   - Filtering by inference_id

5. **Production deployment**
   - PostgreSQL migration execution
   - Service restart
   - Monitoring setup

## 十一、待審事項

### 需要 Owner 批准
1. ✅ Migration 檔案內容（A + B）
2. ✅ Validation logic 設計
3. ✅ Test coverage 範圍
4. ❌ PostgreSQL migration 執行
5. ❌ 成功推論案例實作
6. ❌ Paper create/engine 一致性驗證
7. ❌ Git commit + push

### 已確認不在此次範圍
- 大型審計系統
- FK constraint at DB level
- UsageRecord deletion logic
- 完整 production readiness 證明
