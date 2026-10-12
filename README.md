# Alethra

**Ideas, tested. Decisions, yours.**

把投資想法轉化為可測試、可追溯、可持續檢視的研究。

## Phase 1 Status

**Feature Complete — Local Prototype, Demo Data Only**

- 所有結果僅供研究參考，不代表未來績效
- 使用示範資料，非真實市場資料
- 引擎版本：1.1.0

## 重要聲明

**Alethra 是研究工具，非投資建議。**

- 所有結果僅供研究參考，不代表未來績效
- 不進行實盤交易，不提供投資建議
- 不保證任何收益
- Phase 1 使用示範資料，非真實市場資料

## 功能

### Phase 1（當前版本）

- 用戶註冊與登入（JWT）
- 3 種策略模板：
  - **Buy and Hold** — 首日買入，持有至末日
  - **SMA Crossover** — 短長期移動平均線交叉信號
  - **Price Change Fixed Hold** — 價格跌幅觸發買入，固定持有期
- 實驗版本管理與 config hash 追蹤
- 示範資料回測（DEMO-ALPHA, DEMO-BETA, DEMO-BENCHMARK）
- 16 欄交易分類帳（signal date, execution date, slippage, commission 等）
- 績效指標（累積報酬、年化報酬、最大回撤、波動率、超額報酬等）
- Benchmark 對齊的股權曲線
- Dataset checksum 確保資料一致性
- 方法論摘要（methodology summary）
- 實驗匯出功能
- Versioned API（`/api/v1/`）

### 後續階段

- Phase 2: AI 澄清助手、策略視覺化改善
- Phase 3A: ✅ 鎖定與歷史重播模擬
- Phase 3B: ✅ 紙上投資組合
- Phase 4: ✅ 模型研究記錄與模型卡
- Phase 5: ✅ 模型研究匯出套件
- Phase 6: ✅ 試點運營與產品驗證

**未排程（Future Research Engine Expansion — Unscheduled）**: 多資產組合、策略優化、walk-forward optimization。這些需要獨立的研究與驗證工作。

## 技術架構

- **前端**: Next.js 14 + TypeScript
- **後端**: Python FastAPI
- **資料庫**: PostgreSQL
- **計算**: Python (pandas, numpy)
- **容器**: Docker Compose

## 快速開始

### 前置需求

- Docker 和 Docker Compose
- 或 Python 3.12+ 和 Node.js 20+

### 使用 Docker（推薦）

1. 複製環境設定檔：

```bash
cp .env.example .env
```

2. 啟動所有服務：

```bash
docker-compose up --build
```

3. 存取應用：
   - 前端: http://localhost:3000
   - 後端 API: http://localhost:8000
   - API 文件: http://localhost:8000/docs

### 本機開發

#### 後端

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 設定環境變數
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ai_investment_lab
export JWT_SECRET_KEY=your-secret-key

# 啟動資料庫
docker-compose up db -d

# 啟動後端
uvicorn app.main:app --reload --port 8000
```

#### 前端

```bash
cd frontend
npm install
npm run dev
```

存取 http://localhost:3000

## 示範資料

Phase 1 包含 3 個示範資產：

| 資產 | 說明 | 交易日數 | 日期範圍 |
|------|------|----------|----------|
| DEMO-ALPHA | 模擬成長型股票 | 29 天（缺 2024-01-18） | 2024-01-02 ~ 2024-02-14 |
| DEMO-BETA | 模擬波動型股票 | 30 天 | 2024-01-02 ~ 2024-02-14 |
| DEMO-BENCHMARK | 模擬基準指數 | 30 天 | 2024-01-02 ~ 2024-02-14 |

所有示範資料為確定性生成，可透過 dataset checksum 驗證完整性。

## API 端點

### 認證（`/api/auth/`）
- `POST /api/auth/register` — 註冊
- `POST /api/auth/login` — 登入
- `GET /api/auth/me` — 當前用戶

### 品牌與資料（`/api/v1/`）
- `GET /api/v1/brand` — 品牌資訊
- `GET /api/v1/demo-assets` — 示範資產列表

### 實驗（`/api/v1/`）
- `POST /api/v1/experiments/` — 建立實驗
- `GET /api/v1/experiments/` — 列出實驗
- `GET /api/v1/experiments/{id}` — 實驗詳情
- `PUT /api/v1/experiments/{id}` — 更新實驗
- `GET /api/v1/experiments/{id}/export` — 匯出實驗

### 回測（`/api/v1/`）
- `POST /api/v1/versions/{version_id}/runs` — 執行回測
- `GET /api/v1/runs/{run_id}` — 取得執行詳情
- `GET /api/v1/experiments/{experiment_id}/runs` — 列出執行紀錄

### 模型研究（`/api/v1/`）
- `POST /api/v1/model-research` — 建立模型研究記錄
- `GET /api/v1/model-research` — 列出模型研究記錄
- `GET /api/v1/model-research/{id}` — 模型研究詳情
- `POST /api/v1/model-research/{id}/versions` — 建立模型版本
- `POST /api/v1/model-research-records/{id}/exports` — 匯出研究套件
- `GET /api/v1/exports/{id}` — 匯出狀態
- `GET /api/v1/exports/{id}/download` — 下載研究套件

### 試點運營（`/api/v1/pilot/`）
- `POST /api/v1/pilot/applications` — 提交試點申請
- `GET /api/v1/pilot/applications/me` — 我的試點申請
- `GET /api/v1/pilot/applications` — 列出所有申請（管理員）
- `PATCH /api/v1/pilot/applications/{id}/status` — 更新申請狀態（管理員）
- `POST /api/v1/pilot/entitlements` — 授予試點權限（管理員）
- `GET /api/v1/pilot/entitlements/me` — 我的試點權限
- `PUT /api/v1/pilot/entitlements/{id}` — 更新試點權限
- `POST /api/v1/pilot/usage-events` — 記錄使用事件
- `GET /api/v1/pilot/usage-events/me` — 我的使用事件
- `POST /api/v1/pilot/costs` — 建立成本記錄
- `GET /api/v1/pilot/costs` — 列出成本記錄（管理員）
- `POST /api/v1/pilot/support-logs` — 建立支援日誌
- `PATCH /api/v1/pilot/support-logs/{id}` — 更新支援日誌
- `GET /api/v1/pilot/support-logs` — 列出支援日誌（管理員）
- `POST /api/v1/pilot/hypotheses` — 建立產品假說（管理員）
- `PATCH /api/v1/pilot/hypotheses/{id}` — 更新產品假說（管理員）
- `GET /api/v1/pilot/hypotheses` — 列出產品假說
- `GET /api/v1/pilot/admin/dashboard` — 管理員驗證儀表板（管理員）
- `GET /api/v1/pilot/admin/hypotheses-dashboard` — 假說儀表板（管理員）

## 測試

```bash
cd backend
pytest tests/ -v
```

Phase 1–6 包含 170 個測試：
- 29 個引擎測試（3 種策略、交易分類帳、benchmark Model A 對齊、訊號-執行分離、年化指標防護、checksum、指標計算等）
- 9 個 API 測試（品牌、示範資產、認證、實驗 CRUD、回測執行）
- 16 個模型研究匯出測試（套件內容、憑證排除、授權、校驗、隱私）
- 65 個其他測試（歷史重播模擬、紙上投資組合、模型研究記錄、模型卡、個人記憶等）
- 51 個試點運營與產品驗證測試（申請、權限、使用事件、成本記錄、支援日誌、假說、管理員儀表板、安全邊界）

## 專案結構

```
.
├── backend/
│   ├── app/
│   │   ├── api/          # API 路由（versioned）
│   │   ├── auth/         # 認證系統（JWT + bcrypt）
│   │   ├── models/       # 資料庫模型（SQLAlchemy）
│   │   ├── engine/       # 回測引擎
│   │   │   ├── backtest.py    # 主引擎（config hash, methodology）
│   │   │   ├── strategies.py  # 3 種策略模板
│   │   │   └── metrics.py     # 績效指標計算
│   │   ├── data/         # 示範資料
│   │   └── lib/          # 共用工具（brand）
│   └── tests/            # 測試（170 個）
├── frontend/
│   └── src/
│       ├── app/          # Next.js 頁面
│       └── lib/          # API 客戶端
├── docs/                 # 文件
└── docker-compose.yml
```

## 已知限制

詳見 [Phase 1 已知限制](docs/phase1-known-limitations.md)。

## 方法論

詳見 [研究方法論](docs/research-methodology.md)。

## 授權

本專案為私人專案，未開放授權。

---

**最後更新**: 2026-10-11
