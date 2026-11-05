# Option C: Stock Market Implementation

**日期**：2026-11-05  
**狀態**：✓ 完成（架構驗證通過）  
**前置**：Option B（分頁擴展）完成

---

## 一、實作內容

### 1.1 合成股票資料提供者

由於 Yahoo Finance API 嚴格限流（429 Too Many Requests），實作了 `SyntheticStockProvider` 作為測試用途。

**檔案**：
- `backend/app/providers/synthetic_stock_provider.py`（新建）
- `backend/app/data/stock_instruments.py`（新建）
- `backend/app/providers/provider_registry.py`（更新）
- `backend/app/api/strategy_lab.py`（更新）

**股票標的**：
```python
YAHOO_STOCK_INSTRUMENTS = [
    Instrument.create_us(symbol="AAPL", name="Apple Inc.", ...),
    Instrument.create_us(symbol="TSLA", name="Tesla, Inc.", ...),
    Instrument.create_us(symbol="SPY", name="SPDR S&P 500 ETF Trust", ...),
]
```

### 1.2 多市場架構

**Instrument 解析**：
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
    
    # 找不到
    raise HTTPException(status_code=404, ...)
```

**Provider 路由**：
```python
def _get_provider_for_instrument(instrument):
    if instrument.market.value == "CRYPTO":
        return get_crypto_provider()
    elif instrument.market.value in ("US", "HK"):
        return get_data_provider()  # 使用設定的資料提供者
    else:
        raise HTTPException(status_code=400, ...)
```

### 1.3 Provider Registry 擴展

新增 `synthetic_stock` 提供者選項：
```python
_SYNTHETIC_STOCK_INITIALIZED = False

def _try_init_synthetic_stock() -> Optional[DataProvider]:
    from app.providers.synthetic_stock_provider import SyntheticStockProvider
    provider = SyntheticStockProvider()
    logger.info("Synthetic stock provider auto-registered (testing only)")
    return provider

def get_data_provider() -> DataProvider:
    global _SYNTHETIC_STOCK_INITIALIZED
    if provider_id == "synthetic_stock" and not _SYNTHETIC_STOCK_INITIALIZED:
        _SYNTHETIC_STOCK_INITIALIZED = True
        ss = _try_init_synthetic_stock()
        if ss:
            _PROVIDERS["synthetic_stock"] = ss
```

---

## 二、測試結果

### 2.1 單元測試

```bash
$ pytest tests/test_strategy_research.py -xvs
======================= 22 passed, 96 warnings in 11.37s =======================
```

所有既有測試通過，錯誤訊息從 `unknown_crypto_instrument` 更新為 `unknown_instrument`。

### 2.2 API 整合測試

**啟動伺服器**：
```bash
DATA_PROVIDER=synthetic_stock .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**測試 US:AAPL**：
```bash
$ curl -X POST /api/v1/strategy-lab/research \
  -d '{"instrument_id": "US:AAPL", "interval_minutes": 1440, ...}'

# 結果
{
  "bars": {"count": 720, "first_open_time": "2024-11-15", "last_open_time": "2026-11-04"},
  "walk_forward": {
    "tested_folds": 2,
    "out_of_sample": {
      "trades": 71,
      "win_rate": "0.394",
      "net_pnl": "-309.93",
      "mean_over_stdev_per_trade": "-0.166"
    }
  },
  "dataset_provenance": {
    "data_nature": "synthetic",
    "provider_id": "synthetic_stock"
  }
}
```

### 2.3 多標的測試

| 標的 | Bars | Trades | Net PnL | Win Rate |
|---|---|---|---|---|
| US:AAPL | 720 | 71 | -309.93 | 39.4% |
| US:TSLA | 720 | 28 | (negative) | (varies) |
| US:SPY | 720 | 9 | (negative) | (varies) |

**結論**：所有股票標的都能成功執行研究，架構驗證通過。

---

## 三、技術細節

### 3.1 合成資料生成

**價格模型**：
```python
def _generate_synthetic_candles(instrument, interval_minutes, count):
    base_price = self._get_base_price(instrument.symbol)  # AAPL=175, TSLA=250, SPY=450
    volatility = self._get_volatility(instrument.symbol)  # AAPL=1.5%, TSLA=3.5%, SPY=1.0%
    
    for i in range(count):
        # 對齊到時間網格（每日 = 午夜 UTC）
        open_time = aligned_now - interval * (count - i)
        
        # 隨機價格運動（常態分佈）
        change_pct = random.gauss(0, volatility)
        price = price * (1 + change_pct)
        
        # 生成 OHLCV
        open_price = price * (1 + random.gauss(0, volatility * 0.3))
        close_price = price * (1 + random.gauss(0, volatility * 0.3))
        high_price = max(open_price, close_price) * (1 + abs(random.gauss(0, volatility * 0.5)))
        low_price = min(open_price, close_price) * (1 - abs(random.gauss(0, volatility * 0.5)))
        volume = random.randint(1000000, 10000000)
```

**時間戳對齊**：
- 每日（1440 分鐘）：對齊到午夜 UTC
- 每小時（60 分鐘）：對齊到整點
- 確保 `(open_time - epoch) % interval == timedelta(0)`

### 3.2 資料品質標記

```python
def get_data_quality(self, symbol: str) -> Dict[str, Any]:
    return {
        "data_nature": "synthetic",
        "adjustment": "not_applicable",
        "timezone": "America/New_York",
        "note": "Synthetic data for testing. Replace with real provider for production.",
        "note_zh": "合成資料僅供測試。生產環境請使用真實資料來源。",
    }
```

---

## 四、生產環境注意事項

### 4.1 真實資料提供者

合成資料僅供架構驗證，生產環境需要替換為真實資料來源：

**選項 1：Alpha Vantage**（推薦）
- 免費 API 金鑰：https://www.alphavantage.co/support/#api-key
- 每日 5 次呼叫限制（免費）
- 支援 intraday 和 daily 資料

**選項 2：Polygon.io**
- 免費層：每日 5 次 API 呼叫
- 延遲 15 分鐘資料
- 支援美股、期權、外匯、加密貨幣

**選項 3：Twelve Data**
- 已有提供者實作（`twelvedata_provider.py`）
- 需要 API 金鑰
- 支援 800+ 交易所

### 4.2 設定方式

```bash
# 使用 Alpha Vantage（需實作）
ALPHA_VANTAGE_API_KEY=your_key
DATA_PROVIDER=alpha_vantage

# 使用 Twelve Data（已有實作）
TWELVEDATA_API_KEY=your_key
DATA_PROVIDER=twelvedata

# 使用合成資料（測試）
DATA_PROVIDER=synthetic_stock
```

---

## 五、G0 驗證閘門

**血緣檢查**：
```json
{
  "data_nature": "synthetic",  // ⚠️ 非 live
  "provider_id": "synthetic_stock",
  "bars": 720,
  "integrity_ok": true
}
```

**結論**：G0 驗證失敗（data_nature ≠ live），合成資料不能用於最終研究結論。

**下一步**：
1. 取得真實股票資料提供者 API 金鑰
2. 實作 Alpha Vantage 或 Twelve Data 提供者
3. 重新執行研究，確保 G0 通過

---

## 六、架構驗證成果

✓ 多市場 Instrument 解析（crypto + stock）  
✓ Provider 路由（market-aware）  
✓ 研究端點支援股票標的  
✓ 合成資料生成（時間對齊、OHLCV 有效）  
✓ 所有單元測試通過  
✓ API 整合測試通過（3 標的 × 720 bars）  

**架構就緒**：Option C 完成，可繼續 Option A（套利）和 Option D（做市）。

---

## 七、檔案清單

**新增**：
- `backend/app/providers/synthetic_stock_provider.py`（280 行）
- `backend/app/data/stock_instruments.py`（45 行）

**修改**：
- `backend/app/providers/provider_registry.py`（+20 行）
- `backend/app/api/strategy_lab.py`（+35 行）
- `backend/tests/test_strategy_research.py`（1 行更新）

**總計**：+380 行程式碼
