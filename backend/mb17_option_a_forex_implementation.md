# Option A: Forex Market Implementation

**日期**：2026-11-05  
**狀態**：✓ 完成（架構驗證通過）  
**前置**：Option C（股票市場）完成

---

## 一、實作內容

### 1.1 外匯標的定義

**檔案**：`backend/app/data/forex_instruments.py`（新建）

**外匯貨幣對**：
```python
FOREX_INSTRUMENTS = [
    Instrument(
        market=Market.FOREX,
        symbol="EUR/USD",
        name="Euro / US Dollar",
        asset_type=AssetType.FOREX_PAIR,
        exchange=Exchange.FOREX,
        currency=Currency.USD,
        timezone="UTC",
        lot_size=100000,  # Standard lot
        base_asset="EUR",
        price_precision=5,  # 5 decimal places
    ),
    Instrument(
        market=Market.FOREX,
        symbol="GBP/USD",
        name="British Pound / US Dollar",
        ...
    ),
    Instrument(
        market=Market.FOREX,
        symbol="USD/JPY",
        name="US Dollar / Japanese Yen",
        currency=Currency.JPY,
        price_precision=3,  # 3 decimal places for JPY pairs
        ...
    ),
]
```

### 1.2 枚舉擴展

**檔案**：`backend/app/providers/instruments.py`（更新）

新增：
```python
class Market(str, enum.Enum):
    FOREX = "FOREX"  # 新增

class Exchange(str, enum.Enum):
    FOREX = "FOREX"  # 新增（OTC market, no central exchange）

class Currency(str, enum.Enum):
    JPY = "JPY"  # 新增
    EUR = "EUR"  # 新增
    GBP = "GBP"  # 新增

class AssetType(str, enum.Enum):
    FOREX_PAIR = "FOREX_PAIR"  # 新增
```

### 1.3 合成外匯資料提供者

**檔案**：`backend/app/providers/synthetic_forex_provider.py`（新建）

**價格模型**：
```python
def _get_base_price(symbol: str) -> float:
    prices = {
        "EUR/USD": 1.0850,
        "GBP/USD": 1.2650,
        "USD/JPY": 149.50,
    }
    return prices.get(symbol, 1.0)

def _get_volatility(symbol: str) -> float:
    # Forex is typically less volatile than stocks
    volatilities = {
        "EUR/USD": 0.005,  # 0.5% daily
        "GBP/USD": 0.007,  # 0.7% daily
        "USD/JPY": 0.006,  # 0.6% daily
    }
    return volatilities.get(symbol, 0.005)
```

**特點**：
- 外匯波動率低於股票（0.5-0.7% vs 1.0-3.5%）
- 價格精度：主要貨幣對 5 位小數，JPY 對 3 位小數
- 交易量以 lots（100,000 單位）計量
- 24/5 交易（週一至週五），使用 UTC 時區

### 1.4 Provider Registry 擴展

**檔案**：`backend/app/providers/provider_registry.py`（更新）

新增 `synthetic_forex` 提供者選項：
```python
_SYNTHETIC_FOREX_INITIALIZED = False

def _try_init_synthetic_forex() -> Optional[DataProvider]:
    from app.providers.synthetic_forex_provider import SyntheticForexProvider
    provider = SyntheticForexProvider()
    logger.info("Synthetic forex provider auto-registered (testing only)")
    return provider

def get_data_provider() -> DataProvider:
    global _SYNTHETIC_FOREX_INITIALIZED
    if provider_id == "synthetic_forex" and not _SYNTHETIC_FOREX_INITIALIZED:
        _SYNTHETIC_FOREX_INITIALIZED = True
        sf = _try_init_synthetic_forex()
        if sf:
            _PROVIDERS["synthetic_forex"] = sf
```

### 1.5 多市場路由更新

**檔案**：`backend/app/api/strategy_lab.py`（更新）

```python
def _require_instrument(instrument_id: str):
    # 嘗試 crypto
    instrument = get_crypto_instrument(instrument_id)
    if instrument is not None:
        return instrument
    
    # 嘗試 stock
    instrument = get_stock_instrument(instrument_id)
    if instrument is not None:
        return instrument
    
    # 嘗試 forex
    instrument = get_forex_instrument(instrument_id)
    if instrument is not None:
        return instrument
    
    # 找不到
    raise HTTPException(status_code=404, ...)

def _get_provider_for_instrument(instrument):
    if instrument.market.value == "CRYPTO":
        return get_crypto_provider()
    elif instrument.market.value in ("US", "HK", "FOREX"):
        return get_data_provider()
    else:
        raise HTTPException(status_code=400, ...)
```

---

## 二、測試結果

### 2.1 單元測試

```bash
$ pytest tests/test_strategy_research.py -xvs
======================= 22 passed, 96 warnings in 11.42s =======================
```

所有既有測試通過。

### 2.2 API 整合測試

**啟動伺服器**：
```bash
DATA_PROVIDER=synthetic_forex .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**測試結果**：

| 貨幣對 | Bars | Trades | Win Rate |
|---|---|---|---|
| FOREX:EUR/USD | 720 | 9 | 33.3% |
| FOREX:GBP/USD | 720 | 11 | 45.5% |
| FOREX:USD/JPY | 720 | 36 | 55.6% |

**結論**：所有外匯貨幣對都能成功執行研究，架構驗證通過。

---

## 三、技術細節

### 3.1 外匯市場特性

**交易時間**：
- 24/5（週日 17:00 ET 至週五 17:00 ET）
- 主要交易時段：倫敦（03:00-12:00 ET）、紐約（08:00-17:00 ET）、東京（19:00-04:00 ET）
- 使用 UTC 時區作為標準

**報價方式**：
- EUR/USD = 1.0850 表示 1 歐元 = 1.0850 美元
- USD/JPY = 149.50 表示 1 美元 = 149.50 日元
- 主要貨幣對：5 位小數（pipette）
- JPY 貨幣對：3 位小數

**交易單位**：
- Standard lot = 100,000 單位
- Mini lot = 10,000 單位
- Micro lot = 1,000 單位

**波動率特徵**：
- 外匯波動率低於股票（0.5-0.7% vs 1.0-3.5% 每日）
- 主要貨幣對波動率較低
- 新興市場貨幣對波動率較高

### 3.2 成本結構

**佣金**：
- 典型外匯佣金：0.01%（1 pip）
- 測試使用：0.0001（0.01%）

**滑價**：
- 主要貨幣對：0.5-1 pip
- 測試使用：0.0001（0.01%）

**點差**：
- EUR/USD：1-2 pips
- GBP/USD：2-3 pips
- USD/JPY：1-2 pips

---

## 四、生產環境注意事項

### 4.1 真實資料提供者

合成資料僅供架構驗證，生產環境需要替換為真實資料來源：

**選項 1：OANDA**（推薦）
- REST API + Streaming API
- 免費模擬帳戶
- 支援歷史資料和即時資料

**選項 2：FXCM**
- REST API
- 免費模擬帳戶
- 支援 50+ 貨幣對

**選項 3：Twelve Data**
- 已有提供者實作（`twelvedata_provider.py`）
- 需要 API 金鑰
- 支援外匯、股票、加密貨幣

### 4.2 設定方式

```bash
# 使用 OANDA（需實作）
OANDA_API_KEY=your_key
OANDA_ACCOUNT_ID=your_account
DATA_PROVIDER=oanda

# 使用 Twelve Data（已有實作）
TWELVEDATA_API_KEY=your_key
DATA_PROVIDER=twelvedata

# 使用合成資料（測試）
DATA_PROVIDER=synthetic_forex
```

---

## 五、G0 驗證閘門

**血緣檢查**：
```json
{
  "data_nature": "synthetic",  // ⚠️ 非 live
  "provider_id": "synthetic_forex",
  "bars": 720,
  "integrity_ok": true
}
```

**結論**：G0 驗證失敗（data_nature ≠ live），合成資料不能用於最終研究結論。

**下一步**：
1. 取得真實外匯資料提供者 API 金鑰
2. 實作 OANDA 或 Twelve Data 提供者
3. 重新執行研究，確保 G0 通過

---

## 六、架構驗證成果

✓ 多市場 Instrument 解析（crypto + stock + forex）  
✓ Provider 路由（market-aware）  
✓ 研究端點支援外匯標的  
✓ 合成資料生成（時間對齊、OHLCV 有效）  
✓ 所有單元測試通過  
✓ API 整合測試通過（3 貨幣對 × 720 bars）  

**架構就緒**：Option A 完成，可繼續 Option D（做市）或多市場批次測試。

---

## 七、檔案清單

**新增**：
- `backend/app/providers/synthetic_forex_provider.py`（285 行）
- `backend/app/data/forex_instruments.py`（85 行）

**修改**：
- `backend/app/providers/instruments.py`（+10 行：新增 FOREX 枚舉）
- `backend/app/providers/provider_registry.py`（+20 行）
- `backend/app/api/strategy_lab.py`（+10 行）

**總計**：+410 行程式碼

---

## 八、與加密貨幣/股票的比較

| 特性 | 加密貨幣 | 股票 | 外匯 |
|---|---|---|---|
| 交易時間 | 24/7 | 6.5h/日 | 24/5 |
| 波動率 | 高（3-5%） | 中（1-3.5%） | 低（0.5-0.7%） |
| 交易單位 | 1（整數） | 1（整數） | 100,000（lot） |
| 價格精度 | 1-8 小數 | 2 -8 小數 | 3-5 小數 |
| 主要成本 | 佣金 + 滑價 | 佣金 + 滑價 | 點差 + 佣金 |
| 市場結構 | 去中心化 | 集中交易所 | OTC（去中心化） |
| 槓桿 | 通常無 | 通常無 | 高（50x-500x） |

**結論**：外匯市場具有獨特的特性（低波動率、高槓桿、OTC 結構），需要專門的風險管理和成本控制策略。
