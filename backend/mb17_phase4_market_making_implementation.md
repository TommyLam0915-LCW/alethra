# MB-17 Phase 4: Market Making Strategy Implementation

**日期**：2026-11-05  
**狀態**：✓ 完成（架構驗證通過）  
**前置**：MB-17 Phase 3（波動率策略）完成

---

## 一、實作內容

### 1.1 做市策略概念

做市（Market Making）是一種提供流動性的策略，通過同時掛出買單（bid）和賣單（ask）來捕捉價差（spread）。

**核心原理**：
- 在價格區間兩側掛單
- 當雙邊都被成交時，捕捉價差利潤
- 管理庫存風險（inventory risk）
- 根據波動率調整報價寬度

**挑戰**：
- 需要訂單簿資料（Level 2/Level 3）
- 現有平台僅支援 OHLCV K 線
- 需要模擬限價單成交

### 1.2 簡化做市策略

由於平台限制，實作了 4 條簡化的做市規則，使用 OHLCV 資料模擬做市行為：

**規則 1：spread_capture（價差捕捉）**
```python
# 低波動率環境（ATR% < 2%）
# 價格觸及區間邊緣時提供流動性
# - 接近 5 根低點 → 模擬 bid 成交（做多）
# - 接近 5 根高點 → 模擬 ask 成交（做空）
# 目標：0.5 × ATR（捕捉半個價差）
# 停損：0.5 × ATR
```

**規則 2：inventory_neutral（庫存中性）**
```python
# 根據 Z-score 偏離調整報價方向
# - |Z| > 1.5 → 反向操作（維持庫存中性）
# - Z > 1.5 → 做空（價格偏高，偏好賣出）
# - Z < -1.5 → 做多（價格偏低，偏好買入）
# 波動率過濾：0.5% < ATR% < 3%
```

**規則 3：mean_reversion_mm（均值回歸做市）**
```python
# 結合均值回歸與做市邏輯
# - |Z| > 2（價格極端偏離）
# - RSI 確認超買超賣（RSI < 30 或 > 70）
# - ATR% < 2%（低波動率，價差有利可圖）
# 在極端價格提供流動性，反向進場
```

**規則 4：volatility_mm（波動率做市）**
```python
# 根據波動率變化調整報價寬度
# - ATR 收縮（current < 0.8 × prior）→ 積極報價
# - ATR 擴張（current > 1.2 × prior）→ 保守報價
# - HV < 50th percentile（有利做市環境）
# 模擬做市商根據波動率調整價差
```

### 1.3 技術實作

**檔案**：`backend/app/services/strategy_research.py`（+180 行）

**新增函數**：
```python
def _sig_spread_capture(ind: _Indicators, index: int) -> int:
    """Market Making Rule 1: spread_capture."""
    # 使用 5 根高低點區間
    # 位置 < 20% → 做多（bid fill）
    # 位置 > 80% → 做空（ask fill）
    # ATR% < 2% 過濾

def _sig_inventory_neutral(ind: _Indicators, index: int) -> int:
    """Market Making Rule 2: inventory_neutral."""
    # Z-score > 1.5 → 做空
    # Z-score < -1.5 → 做多
    # 0.5% < ATR% < 3% 過濾

def _sig_mean_reversion_mm(ind: _Indicators, index: int) -> int:
    """Market Making Rule 3: mean_reversion_mm."""
    # |Z| > 2 + RSI 確認 → 反向進場
    # ATR% < 2% 過濾

def _sig_volatility_mm(ind: _Indicators, index: int) -> int:
    """Market Making Rule 4: volatility_mm."""
    # ATR 變化率 + HV < 50th → 調整報價積極度
    # 收縮時積極，擴張時保守
```

**規則註冊**：
```python
RULES: Tuple[ResearchRule, ...] = (
    # ... 既有 12 條規則 ...
    # MB-17 Phase 4: market making rules
    ResearchRule(
        rule_id="spread_capture",
        label_zh="價差捕捉",
        family="market_making",
        thesis_zh="低波動率環境下，價格觸及區間邊緣時提供流動性捕捉價差。",
        signal=_sig_spread_capture,
    ),
    # ... 其他 3 條規則 ...
)
```

---

## 二、測試結果

### 2.1 單元測試

```bash
$ pytest tests/test_strategy_research.py -xvs
======================= 22 passed, 96 warnings in 12.03s =======================
```

**更新項目**：
- `test_research_returns_walk_forward_evidence_and_dataset_provenance`
  - 從 48 grid items（12 規則 × 4 出場）更新為 64（16 規則 × 4 出場）

### 2.2 規則統計

**總規則數**：16 條
- 趨勢（trend）：3 條
- 動量（momentum）：1 條
- 均值回歸（mean_reversion）：4 條
- 波動率（volatility）：4 條
- **做市（market_making）：4 條**（新增）

**出場配置**：4 種（不變）
- balanced_1_1（對稱 1:1）
- trend_1p5_1（順勢 1.5:1）
- trend_2_1p5（長線 2:1.5）
- high_win_1_2（高勝率 1:2）

**總組合數**：16 × 4 = **64 個 grid items**

---

## 三、做市策略特性

### 3.1 與方向性策略的差異

| 特性 | 方向性策略 | 做市策略 |
|---|---|---|
| 進場邏輯 | 預測價格方向 | 提供流動性捕捉價差 |
| 持倉時間 | 中長（6-12 根） | 短（1-3 根） |
| 目標距離 | 1-2 × ATR | 0.5 × ATR |
| 勝率要求 | 40-50% | 60-70% |
| 盈虧比 | 1.5:1 - 2:1 | 0.5:1 - 1:1 |
| 市場環境 | 趨勢明確 | 區間震盪 |
| 風險來源 | 趨勢反轉 | 庫存積壓 |

### 3.2 做市策略優勢

**理論優勢**：
1. **正期望值**：在有效市場中，做市商透過捕捉價差獲得穩定收益
2. **低方向性風險**：雙邊掛單，不依賴單一方向預測
3. **高勝率**：小目標 + 频繁交易 = 高勝率（但盈虧比低）
4. **市場中性**：庫存管理得當，可實現市場中性

**實際挑戰**：
1. **逆向選擇**：資訊交易者會在不利時機成交
2. **庫存風險**：單邊成交導致庫存積壓
3. **波動率風險**：高波動時價差擴大，但風險也增加
4. **交易成本**：频繁交易的手續費和滑價

### 3.3 適用環境

**有利環境**：
- ✓ 低波動率（ATR% < 2%）
- ✓ 區間震盪（無明顯趨勢）
- ✓ 高流動性（買賣價差小）
- ✓ 低交易成本（佣金 + 滑價 < 價差）

**不利環境**：
- ✗ 高波動率（ATR% > 3%）
- ✗ 單邊趨勢（價格持續上漲或下跌）
- ✗ 低流動性（買賣價差大）
- ✗ 高交易成本（佣金 + 滑價 > 價差）

---

## 四、G0 驗證閘門

### 4.1 血緣檢查

做市策略使用相同的資料來源：
- **加密貨幣**：data_nature=live, provider_id=kraken ✓
- **股票**：data_nature=synthetic, provider_id=synthetic_stock ✗
- **外匯**：data_nature=synthetic, provider_id=synthetic_forex ✗

### 4.2 預期結果

**加密貨幣市場**：
- 做市策略在低波動率環境下可能表現較好
- 但加密貨幣波動率高，可能不利於做市
- 預期：與 MB-14/16/17 Phase 3 相似，無顯著邊

**股票/外匯市場**：
- 波動率較低，理論上更適合做市
- 但使用合成資料，G0 驗證失敗
- 需要真實資料才能得出結論

---

## 五、下一步建議

### 5.1 批次測試

執行 Phase 4 批次測試（16 規則 × 4 出場 × 3 標的 × 4 時間框架 × 3 佣金 = 576 次 POST）：

```bash
# 加密貨幣（G0 通過）
./mb17_phase4_batch_execute.sh

# 分析結果
# - 做市規則是否比方向性規則更穩定？
# - 低波動率環境下是否表現較好？
# - SNR 是否顯著改善？
```

### 5.2 真實資料驗證

**股票市場**：
1. 取得 Alpha Vantage API 金鑰（免費）
2. 實作 AlphaVantageProvider
3. 重新執行研究，確保 G0 通過

**外匯市場**：
1. 取得 OANDA 模擬帳戶（免費）
2. 實作 OANDAProvider
3. 重新執行研究，確保 G0 通過

### 5.3 進階做市策略

**擴展方向**：
1. **訂單簿模擬**：實作 Level 2 資料支援
2. **庫存管理**：加入庫存限制和風險控制
3. **動態價差**：根據波動率和庫存動態調整報價
4. **多標的做市**：同時在多個相關標的做市，對沖風險

---

## 六、技術細節

### 6.1 信號函數設計

**輸入**：
```python
@dataclass
class _Indicators:
    close: List[Decimal]
    atr: List[Optional[Decimal]]
    zscore: List[Optional[Decimal]]
    rsi: List[Optional[Decimal]]
    macd: List[Optional[Decimal]]
    hv: List[Optional[Decimal]]
    atr_pct: List[Optional[Decimal]]
    # ... 其他指標
```

**輸出**：
```python
def _sig_xxx(ind: _Indicators, index: int) -> int:
    """
    Returns:
        1: Long signal (buy)
        -1: Short signal (sell)
        0: No signal
    """
```

### 6.2 出場配置

做市策略使用相同的 4 種出場配置：
```python
EXIT_CONFIGS = (
    ExitConfig(exit_id="balanced_1_1", target_atr=1.0, stop_atr=1.0, time_stop_bars=6),
    ExitConfig(exit_id="trend_1p5_1", target_atr=1.5, stop_atr=1.0, time_stop_bars=8),
    ExitConfig(exit_id="trend_2_1p5", target_atr=2.0, stop_atr=1.5, time_stop_bars=12),
    ExitConfig(exit_id="high_win_1_2", target_atr=1.0, stop_atr=2.0, time_stop_bars=6),
)
```

**注意**：做市策略的理想出場配置應該是更小的目標（0.5 × ATR），但為了保持可比性，使用相同的配置。

---

## 七、檔案清單

**修改**：
- `backend/app/services/strategy_research.py`（+180 行：4 條做市規則）
- `backend/tests/test_strategy_research.py`（1 行更新：grid items 從 48 → 64）

**總計**：+181 行程式碼

---

## 八、與前三階段的比較

| 階段 | 假設 | 規則數 | 總組合數 | 結果 |
|---|---|---|---|---|
| MB-14 | 趨勢追蹤 | 4 | 16 | FAIL（所有 |t| < 2.96） |
| MB-16 | 均值回歸 | 4 | 16 | FAIL（所有 |t| < 2.96） |
| MB-17 Phase 3 | 波動率交易 | 4 | 16 | FAIL（所有 |t| < 2.96） |
| **MB-17 Phase 4** | **做市** | **4** | **16** | **待測試** |

**累計**：16 條規則，64 個組合

---

## 九、結論

✓ 成功實作 4 條做市策略規則  
✓ 所有單元測試通過（22/22）  
✓ 架構驗證完成（16 規則 × 4 出場 = 64 組合）  

**下一步**：
1. 執行 Phase 4 批次測試（加密貨幣市場）
2. 分析做市策略是否比方向性策略更穩定
3. 若結果仍為 FAIL，考慮放棄短線交易假設，轉向其他研究方向

**研究進展**：
- MB-14（趨勢）：FAIL
- MB-16（均值回歸）：FAIL
- MB-17 Phase 3（波動率）：FAIL
- MB-17 Phase 4（做市）：**待驗證**

若 Phase 4 也 FAIL，則 G6 觸發：在當前資料窗口和交易機制下，無法發現統計顯著的邊。
