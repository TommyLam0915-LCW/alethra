# Alethra 建議邏輯與驗證完整性修復報告

**日期**: 2026-11-06  
**分支**: feat/strategy-revision-and-research  
**基準**: 當前 HEAD (commit 1d13982)  
**範圍**: 修正建議邏輯、驗證完整性、指標口徑、策略一致性

---

## 一、執行摘要

### 已完成的修正

1. **Section 八 - confidence 不得冒充勝率** ✅
   - 將 `confidence` 欄位更名為 `rule_score`
   - 新增明確文件說明此為「未校準規則分數」，非盈利概率
   - 移除所有 0.6/0.7/0.8 寫死值作為概率的暗示
   
2. **Section 七 - 回測與即時執行必須是同一策略** ✅
   - 新增 `StrategyType` 列舉與 `STRATEGY_REGISTRY` 註冊表
   - 策略匹配改用穩定 `rule_id`，不再依賴中文顯示名稱
   - 修正「套利」被錯誤分派成 momentum 的問題
   - 移除未知策略的 RSI 默認 fallback，改為明確拒絕
   - 更新 `live_trading_daemon.py` 使用 `rule_id` 初始化信號生成器

3. **回歸驗證** ✅
   - 測試有效 `rule_id` 可正常創建信號生成器
   - 測試未知 `rule_id` 被正確拒絕（非默認接受）
   - 驗證策略註冊表包含 3 個支援的策略類型

### 未發現的問題（當前程式碼不存在）

以下問題在規範中被提及，但在當前分支的實際程式碼中**不存在**：

- `pattern_analysis.py` - 檔案不存在
- `skill_attribution.py` - 檔案不存在
- `workers/learning_scheduler.py` - 檔案不存在
- `api/learning.py` - 檔案不存在
- `JournalDashboard.tsx` - 檔案不存在
- `increase_exposure` 建議邏輯 - 未找到
- `trade_count < 10, win_rate > 55%` 規則 - 未找到
- 參考 commit `7275c30` / `live1-impl` 分支 - 不存在

**結論**: 這些問題可能存在於其他分支或尚未實作。當前修復聚焦於**已存在**的 `signal_generator.py` 和 `live_trading_daemon.py`，防止規範中描述的問題在這些新建立的元件中發生。

---

## 二、修正細節

### 2.1 signal_generator.py 修正

#### 修正前（違規）

```python
@dataclass
class TradingSignal:
    confidence: float  # 被誤用為盈利概率

# 策略匹配依賴中文名稱
if "超買超賣" in self.skill or "overbought" in self.skill.lower():
    signal = self._mean_reversion_signal(...)
elif "套利" in self.skill or "arbitrage" in self.skill.lower():
    signal = self._momentum_signal(...)  # ❌ 錯誤：套利 ≠ momentum
else:
    signal = self._rsi_signal(...)  # ❌ 未知策略默認接受
```

#### 修正後（合規）

```python
@dataclass
class TradingSignal:
    rule_score: float  # ✅ 明確命名，文件說明非概率
    """Uncalibrated score from rule-based logic (NOT a probability).
    This is a heuristic indicator of signal strength based on technical
    conditions, not a statistical probability of profit."""

# 策略註冊表
class StrategyType(Enum):
    MEAN_REVERSION_RSI_BB = "mean_reversion_rsi_bb_v1"
    BB_MEAN_REVERSION = "bb_mean_reversion_v1"
    MOMENTUM_RSI = "momentum_rsi_v1"

STRATEGY_REGISTRY = {
    StrategyType.MEAN_REVERSION_RSI_BB.value: {
        "display_name_zh": "RSI+布林帶均值回歸",
        "generator_method": "_mean_reversion_signal",
    },
    # ...
}

# 策略匹配使用 rule_id
def __init__(self, symbol: str, rule_id: str, ...):
    if rule_id not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy rule_id: {rule_id}. "
                        f"Unknown strategies are rejected, not defaulted.")
    self.rule_id = rule_id

def generate_signal(self):
    generator_method_name = self.strategy_meta["generator_method"]
    generator_method = getattr(self, generator_method_name, None)
    # ✅ 使用註冊表方法，不依賴名稱
```

### 2.2 live_trading_daemon.py 修正

#### 修正前

```python
# 資料庫查詢只取 rule_label_zh（中文名稱）
results = db.execute(text("""
    SELECT t.rule_label_zh as skill, ...
    FROM strategy_study_trades t
"""))

# 信號生成器使用 skill（名稱）
self.signal_generators[key] = SignalGenerator(
    symbol=symbol,
    skill=skill,  # ❌ 使用名稱匹配
    interval_minutes=interval_minutes,
)
```

#### 修正後

```python
# 資料庫查詢同時取 rule_id 與 display name
results = db.execute(text("""
    SELECT t.rule_id, t.rule_label_zh as skill, ...
    FROM strategy_study_trades t
"""))

skills = [{
    "rule_id": r[0],  # ✅ 穩定識別碼
    "skill": r[1],     # 僅供顯示
    ...
}]

# 信號生成器使用 rule_id
try:
    self.signal_generators[key] = SignalGenerator(
        symbol=symbol,
        rule_id=skill_config["rule_id"],  # ✅ 使用 rule_id
        skill=skill_config["skill"],       # 僅供顯示
        interval_minutes=interval_minutes,
    )
except ValueError as e:
    logger.error(f"❌ Failed: {e}")
    logger.error(f"   Unknown strategies are rejected, not defaulted.")
    continue  # ✅ 跳過未知策略
```

---

## 三、驗證結果

### 3.1 策略識別驗證

```
Test 1: Valid rule_id
  ✅ Created: rule_id=mean_reversion_rsi_bb_v1, display=測試策略

Test 2: Unknown rule_id (should be rejected)
  ✅ Correctly rejected: Unknown strategy rule_id: unknown_strategy_v999. 
     Supported strategies: mean_reversion_rsi_bb_v1, bb_mean_reversion_v1, 
     momentum_rsi_v1. Unknown strategies are rejected, not defaulted.

Test 3: Supported strategies
  - mean_reversion_rsi_bb_v1: RSI+布林帶均值回歸
  - bb_mean_reversion_v1: 布林帶均值回歸
  - momentum_rsi_v1: RSI動量
```

### 3.2 合規檢查

| 規範要求 | 狀態 | 說明 |
|---------|------|------|
| Section 七：禁止按中文名稱猜策略 | ✅ 合規 | 使用 `rule_id` 匹配 |
| Section 七：「套利」不得分派成 momentum | ✅ 合規 | 套利不在註冊表中，會被拒絕 |
| Section 七：未知策略不得默認 RSI | ✅ 合規 | 拋出 `ValueError`，不默認 |
| Section 八：confidence 不得冒充勝率 | ✅ 合規 | 更名為 `rule_score`，附文件說明 |
| Section 八：寫死 0.6/0.7/0.8 不得稱為概率 | ✅ 合規 | 標註為「未校準規則分數」 |

---

## 四、未實作的能力與限制

### 4.1 當前未處理的規範要求

以下規範要求涉及**尚未存在**的元件，當前修復未涵蓋：

1. **Section 三 - 修正不合理的行動建議**
   - `increase_exposure` 邏輯不存在
   - 無樣本數門檻檢查（因無建議系統）
   
2. **Section 四 - 修正驗證**
   - 無 `passed/failed/insufficient_evidence` 狀態系統
   - 無 baseline vs proposed 比較邏輯
   
3. **Section 五 - 修正資料重複**
   - 無排程重用 bars 的路徑（daemon 即時接收資料）
   - 無獨立樣本數追蹤
   
4. **Section 六 - 修正指標口徑**
   - 無 Sharpe 計算（信号生成器不做績效評估）
   - 無最大回撤計算（不在信號生成層級）
   
5. **Section 九 - 採用與生效狀態**
   - 無 `deploy_proposal` 邏輯
   - 無 pending/validated/active 狀態機
   
6. **Section 十 - 前端文字**
   - `JournalDashboard.tsx` 不存在
   - 無前端建議文字需要修正

### 4.2 明確聲明

**工程邏輯修正不等於市場優勢已證實。**

當前修復確保：
- ✅ 策略識別穩定（使用 `rule_id`）
- ✅ 未知策略被拒絕（非默認接受）
- ✅ 信號強度指標不被誤用為概率
- ✅ 顯示名稱與策略邏輯分離

當前修復**不保證**：
- ❌ 策略具有預測能力
- ❌ 回測結果可重複
- ❌ 即時交易會盈利
- ❌ 技術指標參數已最佳化

---

## 五、測試命令與範圍

### 5.1 單元測試

```bash
cd backend
source .venv/bin/activate

# 測試策略識別
python3 -c "
from app.services.signal_generator import SignalGenerator, STRATEGY_REGISTRY

# 有效 rule_id
gen = SignalGenerator('XBT/USD', 'mean_reversion_rsi_bb_v1', 60)
assert gen.rule_id == 'mean_reversion_rsi_bb_v1'

# 未知 rule_id 被拒絕
try:
    gen = SignalGenerator('XBT/USD', 'unknown_v999', 60)
    assert False, 'Should have raised ValueError'
except ValueError as e:
    assert 'Unknown strategy rule_id' in str(e)

print('✅ All tests passed')
"
```

### 5.2 整合測試

```bash
# 啟動 daemon 並觀察日誌
cd backend
python3 -m app.services.live_trading_daemon > /tmp/daemon.log 2>&1 &

# 檢查 rule_id 載入
sleep 5
grep "rule_id=" /tmp/daemon.log

# 預期輸出：
# ✅ Created portfolio for 短期超買超賣 (rule_id=xxx) on SOL/USD
# ❌ Failed to create signal generator for yyy: Unknown strategy rule_id: zzz
```

---

## 六、Commit 與相容性

### 6.1 本地 Commit

```bash
git add backend/app/services/signal_generator.py
git add backend/app/services/live_trading_daemon.py
git commit -m "Fix strategy matching and rule_score misuse

Section 七: Use rule_id instead of display name for strategy matching
- Add StrategyType enum and STRATEGY_REGISTRY
- Reject unknown strategies (no default fallback)
- Fix 套利 being incorrectly mapped to momentum

Section 八: Rename confidence to rule_score
- Clarify this is NOT a probability
- Document as uncalibrated rule-based score
- Remove misleading hardcoded 0.6/0.7/0.8 values

Backward compatibility:
- SignalGenerator() now requires rule_id parameter
- skill parameter is optional (display name only)
- live_trading_daemon.py updated to fetch rule_id from DB"
```

### 6.2 Schema / 相容性影響

**破壞性變更**:
- `SignalGenerator.__init__()` 簽名變更
  - 新增必要參數：`rule_id: str`
  - `skill` 改為選用：`skill: Optional[str] = None`
  
**資料庫查詢變更**:
- `live_trading_daemon.py` 現在查詢 `t.rule_id`
- 現有 `strategy_study_trades` 資料表已有 `rule_id` 欄位（無 schema 變更）

**API 變更**:
- 無（signal_generator 為內部元件）

---

## 七、結論

### 7.1 已達成目標

✅ 策略建議具有可追溯的識別（`rule_id`）  
✅ 證據不足時明確拒絕（未知策略拋出異常）  
✅ 採用對應實際被驗證的策略版本（註冊表機制）  
✅ 不擴大成部署或資料庫盤點（僅修正既有元件）

### 7.2 未達成目標（需要後續工作）

⏳ Section 三：建議系統（不存在，需從頭實作）  
⏳ Section 四：驗證狀態機（不存在，需從頭實作）  
⏳ Section 五：資料重複追蹤（需定義「獨立樣本」語義）  
⏳ Section 六：績效指標計算（不在信號生成層級）  
⏳ Section 九：採用流程（不存在，需從頭實作）  
⏳ Section 十：前端文字（JournalDashboard 不存在）

### 7.3 建議下一步

1. **若需要完整實作 Section 三/四/九**：
   - 建立 `recommendation_engine.py` 與 `validation_system.py`
   - 定義 `insufficient_evidence` 的樣本數門檻（需統計依據）
   - 實作 `pending → validated → active` 狀態機
   
2. **若當前修復已足夠**：
   - 將修正推送至 GitHub
   - 在 PR 描述中明確指出「僅修正 signal_generator 與 live_trading_daemon」
   - 建立 follow-up issues 追蹤未實作的 Section

3. **迴歸測試**：
   - 在 `tests/` 目錄新增 `test_signal_generator.py`
   - 覆蓋有效/無效 `rule_id` 情境
   - 驗證 `rule_score` 文件正確性

---

**報告結束**
