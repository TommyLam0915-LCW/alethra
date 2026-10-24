# C2 本機完成狀態報告

**日期**: 2026-10-23  
**狀態**: Phase 1 完成，Phase 2-4 待執行  
**約束**: 禁止任何 commit/push/ref-update/deployment

---

## 已完成項目

### Phase 1: 後端 Schema 修正 ✅

#### 1.1 SimulationSessionResponse 更新
**檔案**: `backend/app/api/schemas.py:194-212`  
**變更**: 加入 `state_version: int = 0` 欄位

```python
class SimulationSessionResponse(BaseModel):
    # ... existing fields ...
    total_decisions: int
    observed_days: int
    state_version: int = 0  # NEW: for optimistic locking
    created_at: datetime
    updated_at: datetime
```

**驗證**: Schema 定義正確，預設值為 0

#### 1.2 ManualDecisionRequest/Response 加入
**檔案**: `backend/app/api/schemas.py:239-259`  
**變更**: 新增三個 Schema

```python
class ManualDecisionRequest(BaseModel):
    action: str = Field(..., pattern="^(buy|sell|hold)$")
    quantity: Optional[int] = Field(None, ge=1)
    idempotency_key: str = Field(..., min_length=1, max_length=100)
    rationale: Optional[str] = Field(None, max_length=1000)
    reflection: Optional[str] = Field(None, max_length=2000)
    expected_version: Optional[int] = Field(None, ge=0, description="Expected state version for optimistic locking")

class ManualDecisionResponse(BaseModel):
    session: SimulationSessionResponse
    decision: SimulationDecisionResponse
    fill: Optional[dict] = None
    rejection: Optional[dict] = None

class ReflectionUpdateRequest(BaseModel):
    reflection: str = Field(..., min_length=1, max_length=2000)

class ReflectionUpdateResponse(BaseModel):
    decision: SimulationDecisionResponse
    model_config = ConfigDict(from_attributes=True)
```

**驗證**: Schema 定義正確，包含 expected_version 欄位

#### 1.3 SimulationSession 模型更新
**檔案**: `backend/app/models/simulation.py:36`  
**變更**: 加入 `state_version` 欄位

```python
class SimulationSession(Base):
    __tablename__ = "simulation_sessions"
    # ... existing columns ...
    total_decisions = Column(Integer, default=0)
    observed_days = Column(Integer, default=0)
    state_version = Column(Integer, default=0, nullable=False)  # NEW
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
```

**驗證**: 欄位定義正確，不可為 null，預設值為 0

---

## 未完成項目

### Phase 2: 前端合約完成 ❌

#### 2.1 更新 SimulationSession TypeScript 介面
**檔案**: `frontend/src/app/experiments/[id]/simulation/page.tsx:8-21`  
**需要加入**:
```typescript
interface SimulationSession {
  // ... existing fields ...
  state_version: number;  // NEW
}
```

#### 2.2 實作手動決策表單
**需要新增**:
- 動作選擇（buy/sell/hold）
- 數量輸入
- 理由文字框
- 反思文字框
- 提交按鈕

#### 2.3 實作樂觀鎖定邏輯
**需要實作**:
```typescript
const handleSubmitDecision = async () => {
  // 1. 取得目前的 state_version
  const expectedVersion = session.state_version;
  
  // 2. 產生唯一的 idempotency_key
  const idempotencyKey = generateUUID();
  
  // 3. 傳送請求
  try {
    const response = await api.simulations.manualDecision(sessionId, {
      action: selectedAction,
      quantity: quantity,
      idempotency_key: idempotencyKey,
      rationale: rationale,
      expected_version: expectedVersion,  // NEW
    });
    
    // 4. 更新 session 狀態
    setSession(response.session);
  } catch (err: any) {
    // 5. 處理 409 衝突
    if (err.status === 409) {
      setError('狀態已過時，請重新載入後再試');
      await reloadSession();  // 重新載入目前狀態
      // 保留未送出的 rationale
    } else {
      setError(err.message || '決策失敗');
    }
  }
};
```

#### 2.4 實作衝突處理 UI
**需要新增**:
- 409 錯誤訊息顯示
- 重新載入按鈕
- 保留未送出資料的機制

#### 2.5 實作冒等性重試
**需要實作**:
- 相同 idempotency_key 的重試應回傳原始結果
- 不同 payload 的重試應回傳 409 衝突

**狀態**: 未開始

---

### Phase 3: 隔離回歸測試 ❌

#### 3.1 測試環境設定
**需要**:
- 建立獨立 PostgreSQL 資料庫（非共享開發資料庫）
- 設定測試環境變數
- 確保資料庫乾淨

#### 3.2 版本衝突測試案例
**需要實作**:

```python
# test_c2_optimistic_locking.py

def test_two_different_keys_same_version():
    """兩個不同 key 對同一版本：一個生效，一個衝突"""
    # 1. 建立 session，state_version = 0
    # 2. 使用 key1 傳送決策，expected_version = 0
    # 3. 驗證決策生效，state_version = 1
    # 4. 使用 key2 傳送決策，expected_version = 0（過時）
    # 5. 驗證回傳 409 衝突

def test_stale_intent_after_advancement():
    """過時意圖在推進後：被拒絕"""
    # 1. 建立 session，state_version = 0
    # 2. 傳送決策 A，expected_version = 0
    # 3. 驗證決策 A 生效，state_version = 1
    # 4. 再次傳送決策 A（相同 key），expected_version = 0（過時）
    # 5. 驗證回傳 409 衝突

def test_fresh_intent_with_current_version():
    """新鮮意圖與目前版本：成功"""
    # 1. 建立 session，state_version = 0
    # 2. 傳送決策，expected_version = 0
    # 3. 驗證決策生效，state_version = 1
    # 4. 傳送新決策，expected_version = 1（目前）
    # 5. 驗證決策生效，state_version = 2

def test_same_key_retry_after_advancement():
    """相同 key 在推進後重試：回傳原始結果"""
    # 1. 建立 session，state_version = 0
    # 2. 傳送決策 A（key1），expected_version = 0
    # 3. 驗證決策 A 生效，state_version = 1
    # 4. 再次傳送決策 A（相同 key1），expected_version = 1
    # 5. 驗證回傳原始決策 A 的結果（不重複執行）

def test_same_key_changed_payload():
    """相同 key 但 payload 變更：衝突"""
    # 1. 建立 session，state_version = 0
    # 2. 傳送決策 A（key1, action=buy），expected_version = 0
    # 3. 驗證決策 A 生效
    # 4. 再次傳送決策 B（相同 key1, action=sell），expected_version = 1
    # 5. 驗證回傳 409 衝突（相同 key，不同 payload）

def test_concurrent_same_key_requests():
    """併發相同 key 請求：無重複效果，無未處理錯誤"""
    # 1. 建立 session，state_version = 0
    # 2. 併發傳送 5 個相同 key 的請求
    # 3. 驗證只有一個生效，其他回傳相同結果或 409
    # 4. 驗證 state_version = 1（只增加一次）
    # 5. 驗證無 500 錯誤

def test_transaction_fault_injection():
    """事務故障注入：session version、execution、accounting 一致回滾"""
    # 1. 建立 session，state_version = 0
    # 2. 模擬事務故障（在決策執行後、commit 前拋出異常）
    # 3. 驗證 state_version 未增加（仍為 0）
    # 4. 驗證決策未寫入
    # 5. 驗證帳戶未變更
    # 6. C4 不得以輸入驗證或業務拒絕替代
```

#### 3.3 執行測試
**命令**:
```bash
cd backend
pytest tests/test_c2_optimistic_locking.py -v --tb=short
```

**狀態**: 未開始

---

### Phase 4: 遷移隔離測試 ❌

#### 4.1 建立遷移檔案
**檔案**: `backend/migrations/versions/add_state_version_to_sessions.py`  
**需要內容**:

```python
"""add state_version to simulation_sessions

Revision ID: add_state_version
Revises: [previous_revision]
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_state_version'
down_revision = '[previous_revision]'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column(
        'simulation_sessions',
        sa.Column('state_version', sa.Integer(), nullable=False, server_default='0')
    )

def downgrade() -> None:
    op.drop_column('simulation_sessions', 'state_version')
```

**注意**: 需要確認 previous revision ID

#### 4.2 隔離資料庫測試
**步驟**:
```bash
# 1. 建立獨立 PostgreSQL 資料庫
createdb alethra_c2_test

# 2. 設定環境變數
export DATABASE_URL=postgresql://user:pass@localhost:5432/alethra_c2_test

# 3. 執行遷移
cd backend
alembic upgrade head

# 4. 驗證欄位存在
psql alethra_c2_test -c "\d simulation_sessions" | grep state_version

# 5. 驗證預設值
psql alethra_c2_test -c "INSERT INTO simulation_sessions (id, experiment_version_id, owner_id, initial_virtual_cash, data_cutoff_at, status, simulation_policy_version) VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 10000, '2024-01-01', 'pending', '1.0.0') RETURNING state_version;"
# 應該回傳 0
```

#### 4.3 禁止操作
- ❌ 不套用到共享開發資料庫
- ❌ 不蓋 Alembic 戳記
- ❌ 不重啟服務
- ❌ 不變更全域提供者配置

#### 4.4 啟用先決條件報告
**需要記錄**:
- 遷移 ID 與依賴關係
- 套用步驟
- 回滾步驟
- 驗證命令
- 預期結果

**狀態**: 未開始

---

## 本機/遠端等價性

### 已驗證
- ✅ 提交 `1940aa9` 存在於本機 Git 物件庫
- ✅ 可透過 `git show 1940aa9` 存取檔案內容
- ✅ Schema 定義與遠端提交一致（手動比對）

### 未驗證
- ⚠️ 完整檔案樹等價性（需要遠端操作，已禁止）
- ⚠️ 執行階段行為等價性（需要實際測試）
- ⚠️ 測試結果等價性（需要實際執行）

### 標記
**本機/遠端差異**: 本機已套用 Phase 1 的 Schema 修正，但 Phase 2-4 未執行。遠端提交 `1940aa9` 包含完整實作但前端合約未完成（提交說明明確記載）。

---

## Git 狀態

### 目前工作區
```
工作目錄: /Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto
分支: auto/candidate-integration
HEAD: e3371e7
追蹤: origin/auto/candidate-integration
遠端: https://github.com/TommyLam0915-LCW/alethra.git
```

### 已修改檔案（未提交）
```
M backend/app/api/schemas.py          # Phase 1: 加入 state_version, ManualDecisionRequest/Response
M backend/app/models/simulation.py    # Phase 1: 加入 state_version 欄位
```

### 未追蹤檔案
```
?? docs/c2-local-completion-work.md   # 工作計畫
?? docs/slice1-completion-report.md   # Slice 1 完成報告
?? backend/app/api/datasets.py        # Slice 1: Dataset API
?? backend/app/api/human_decisions.py # B1: Human decision API
?? backend/migrations/versions/a1b2c3d4e5f6_add_daily_equity_and_transactions.py
?? backend/migrations/versions/h8i9j0k1l2m3_add_human_decision_events.py
?? frontend/src/app/experiments/[id]/data/page.tsx
?? frontend/src/app/experiments/[id]/runs/[runId]/page.tsx
```

### 遠端提交（未授權）
```
提交: 1940aa9009a4e8d49215c3d714002622354f3a63
分支: work/new-laptop-main-flow
狀態: 未見授權的遠端寫入
```

---

## 誠實標記

### 已驗證
- ✅ Phase 1 Schema 修正已套用
- ✅ 模型欄位定義正確
- ✅ Schema 欄位定義正確

### 未驗證
- ❌ 前端合約未完成
- ❌ 回歸測試未執行
- ❌ 遷移未測試
- ❌ 端到端流程未驗證

### 不正確聲明纠正
- ❌ **不重複**「所有安全案例已通過」
- ❌ **不宣稱** C2 已完成（前端合約未完成）
- ❌ **不宣稱** 測試已執行（實際未執行）
- ✅ **明確標記** Phase 1 完成，Phase 2-4 待執行

---

## 下一步

### 立即執行（本對話內）
1. 完成 Phase 2 前端合約（需要大量程式碼）
2. 撰寫 Phase 3 測試案例（需要大量程式碼）
3. 建立 Phase 4 遷移檔案（需要確認 previous revision）

### 建議交接
由於 C2 完成工作需要大量本機測試與前端整合，建議：
1. 保留目前 Phase 1 的修正
2. 建立交接文件（本文件）
3. 由 Owner 決定是否繼續執行 Phase 2-4
4. 或在新對話中繼續執行

### 禁止操作
- ❌ 不進行任何 commit
- ❌ 不進行任何 push
- ❌ 不進行任何 ref-update
- ❌ 不進行任何 deployment
- ❌ 不自動 revert 或重寫遠端歷史

---

## 附錄：C2 提交說明摘要

**提交**: `1940aa9`  
**標題**: feat: implement C2 race condition fix and comprehensive QA testing

**提交說明明確記載**:
- ✅ Add optimistic locking with state_version
- ✅ Implement atomic conditional UPDATE in manual_decision endpoint
- ✅ Add expected_version field to ManualDecisionRequest
- ✅ Create migration for state_version column
- ⚠️ **Migration created but not applied** (requires restart)
- ⚠️ **Frontend contract update pending** (expected_version + 409 handling)

**結論**: 提交本身即承認 C2 未完成端到端。
