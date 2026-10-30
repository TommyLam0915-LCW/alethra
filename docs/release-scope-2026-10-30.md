# Alethra 發布範圍與能力矩陣（2026-10-30）

本文件界定「這一版實際可以做甚麼、不可以做甚麼」，作為 release readiness 的唯一對照表。
它是內部工程文件，不是市場宣傳材料；任何對外描述都不得超出本表所列的狀態用語。

---

## 0. 狀態用語（本文件專用）

| 標籤 | 含義 |
| --- | --- |
| `IMPLEMENTED_LOCALLY_VERIFIED` | 有原始碼；有本機自動化測試或本機實跑證據；未經外部服務或第三方驗證 |
| `IMPLEMENTED_UNVERIFIED_EXTERNAL` | 有原始碼與離線測試；從未以真實外部憑證／真實網路運行過 |
| `SCAFFOLD_REFUSES` | 介面與控制流存在；傳輸層主動拒絕所有副作用，故不能造成外部影響 |
| `NOT_IMPLEMENTED` | 不存在相應功能；不以錯誤訊息、stub 或文件代替 |
| `BLOCKED_EXTERNAL` | 依賴本機無法取得的外部資源（帳戶、憑證、雲端、域名） |
| `BLOCKED_RESOURCE` | 依賴本機目前不具備的運算／磁碟餘裕 |

「本機」指單一開發機（macOS，arm64）、單一 uvicorn 進程、單一 `next dev` 進程、
一個 disposable PostgreSQL 資料庫（`127.0.0.1:54321`）。所有本機結論不自動延伸至
多 worker、反向代理、雲端或多用戶環境。

---

## 1. 候選身份

| 項目 | 值 |
| --- | --- |
| Repo | `/Users/tommylam0915/Documents/Qoder/2026-10-24/alethra-unified-dev` |
| Branch | `feat/strategy-revision-and-research` |
| HEAD（完整 SHA） | `f172afb57435132234cbfc7aecc9ef0b7caaed87` |
| 起始日期 | 2026-10-30 |
| 前端 | Next.js 14.2.18，`next dev`，`127.0.0.1:3210`，distDir `.next/e2e-3210` |
| 後端 | FastAPI + uvicorn，`127.0.0.1:8210` |
| 資料庫 | PostgreSQL 15.19（Docker），`127.0.0.1:54321/alethra_r531_e2e_20261030` |
| 測試解譯器 | `/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend/venv/bin/python`（3.12.3） |
| 依賴變更 | 本輪無新增、移除或升級任何套件 |

---

## 2. 產品定位

Alethra 是 **Investment Research Workspace**：讓使用者建立、鎖定、重播、記錄並匯出
自己的研究。它**不是** broker、不是投資顧問、不是即時交易平台。

Tagline：「Ideas, tested. Decisions, yours.」

---

## 3. 能力矩陣

### 3.1 研究核心

| 能力 | 狀態 | 限制 |
| --- | --- | --- |
| 歷史回測（3 模板：Buy & Hold、SMA Crossover、Price Change Fixed Hold） | `IMPLEMENTED_LOCALLY_VERIFIED` | 單資產、只做多、無槓桿／保證金、全倉進出 |
| 固定信號—執行分離（day t 收盤信號 → day t+1 開盤執行） | `IMPLEMENTED_LOCALLY_VERIFIED` | 不支援日內或更高頻率 |
| 佣金／滑價模型 | `IMPLEMENTED_LOCALLY_VERIFIED` | 皆為固定比例；無市場衝擊模型、無階梯佣金 |
| 策略版本鎖定（鎖定後不可變） | `IMPLEMENTED_LOCALLY_VERIFIED` | — |
| 研究事件帳本（append-only audit events） | `IMPLEMENTED_LOCALLY_VERIFIED` | — |
| 歷史重播模擬（replay，非即時前向觀察） | `IMPLEMENTED_LOCALLY_VERIFIED` | 僅用已存在資料重播；不新增未來資料 |
| 紙上投資組合（虛擬現金、風控、order lifecycle） | `IMPLEMENTED_LOCALLY_VERIFIED` | 虛擬現金；不連 broker；不涉真實現金 |
| 研究記錄 / model card / 匯出包（含完整性檢查） | `IMPLEMENTED_LOCALLY_VERIFIED` | 匯出包排除 broker 憑證與 PII |
| 研究草稿流程（question → answer → approve → revision） | `IMPLEMENTED_LOCALLY_VERIFIED` | AI 為決定性 mock，不是真實模型輸出，不構成 AI 品質評估 |

### 3.2 個人化與模型

| 能力 | 狀態 | 限制 |
| --- | --- | --- |
| 個人化資料快照 + 預覽 | `IMPLEMENTED_LOCALLY_VERIFIED` | 快照源自本機資料；不外出 |
| 個人決策訓練（job + worker + 版本 + artifact） | `IMPLEMENTED_LOCALLY_VERIFIED` | 訓練以合成資料完成；**未證明任何預測效力** |
| 個人模型推論 API（scoped API key、不可變版本、rate limit） | `IMPLEMENTED_LOCALLY_VERIFIED` | 只有本機 scoped key 驗證；未經第三方安全審查 |
| 模型比較與安全選版（eligibility、CAS、idempotency） | `IMPLEMENTED_LOCALLY_VERIFIED` | 比較有效性只在本機資料上驗證 |
| 規則分數 vs 機率 vs 校正後 margin | 介面與文件已分開命名 | 規則分數**不是**機率，也不是校正後 margin；不得以「信心」字樣呈現 |

### 3.3 帳號與安全

| 能力 | 狀態 | 限制 |
| --- | --- | --- |
| JWT（HS256）認證，`/auth/register`、`/auth/login`、`/auth/me` | `IMPLEMENTED_LOCALLY_VERIFIED` | 無 refresh token 輪替、無 MFA、無密碼重設流程 |
| 登入／註冊暴力破解防護（行程內 per-IP 限流，獨立 bucket） | `IMPLEMENTED_LOCALLY_VERIFIED` | 登入 20 次／60 秒、註冊 5 次／300 秒；多 worker 時上限為 N 倍，重啟即清空；反向代理後需另行處理真實客戶端 IP |
| API key 限流＋計量（per-key 滑動視窗） | `IMPLEMENTED_LOCALLY_VERIFIED` | 行程內計數器；多 worker 同上 |
| DB 為底的每小時配額（訓練／執行／推論／市場資料刷新） | `IMPLEMENTED_LOCALLY_VERIFIED` | 單一行程層級語意 |
| 錯誤回應不洩漏內部（路徑、憑證、例外訊息） | `IMPLEMENTED_LOCALLY_VERIFIED` | 9 條針對 serialisation 邊界的回歸測試；provider 自身 `_last_error` 來源仍可能含內部字串，輸出時已過濾 |
| 候選本機免密登入（`/auth/candidate-session`） | `IMPLEMENTED_LOCALLY_VERIFIED` | **僅 loopback peer**；伺服器端 allowlist；永不簽發 admin；`APP_ENV=development` 以外 fail closed；只影響簽發，不撤銷已簽發 token |
| 生產設定驗證（預設／過短 JWT key 拒絕啟動） | `IMPLEMENTED_LOCALLY_VERIFIED` | — |
| Admin 端點授權 | `IMPLEMENTED_LOCALLY_VERIFIED` | 角色檢查在 API 層；無獨立審計角色 |
| 公開分享 / 外送通知 | `NOT_IMPLEMENTED` | 設定項存在但預設關閉且無消費路徑 |
| MFA、密碼重設、email 驗證、session 撤銷 | `NOT_IMPLEMENTED` | 產品化缺口，見 §6 |

### 3.4 資料與市場

| 能力 | 狀態 | 限制 |
| --- | --- | --- |
| `DataProvider` 抽象 + demo provider（預設） | `IMPLEMENTED_LOCALLY_VERIFIED` | demo 為決定性預生成資料，非真實行情 |
| LSEG provider（真實 HTTP client） | `IMPLEMENTED_UNVERIFIED_EXTERNAL` | 從未以真實 LSEG 憑證運行 |
| Twelve Data provider（真實 HTTP client） | `IMPLEMENTED_UNVERIFIED_EXTERNAL` | 從未以真實 API key 運行 |
| 真實標的目錄（US 10 檔：7 股票 + 3 ETF；HK 9 檔：7 股票 + 2 ETF） | `IMPLEMENTED_LOCALLY_VERIFIED` | 身份與市場結構為**宣告值**，未經交易所逐檔核對 |
| 市場結構（交易所、時區、交易時段、假期） | `IMPLEMENTED_LOCALLY_VERIFIED` | US `America/New_York`、HK `Asia/Hong_Kong`；假期表為宣告值 |
| 板數（board lot）／最小跳動（tick size） | `IMPLEMENTED_LOCALLY_VERIFIED` | 宣告值；未經交易所核對 |
| 跨幣別換算（FX） | `NOT_IMPLEMENTED` | 無任何 FX 匯率或換算路徑；US 與 HK 金額不可混算 |
| 即時行情串流 | `NOT_IMPLEMENTED` | 只有歷史 bar 與重播；無 websocket / tick feed |

### 3.5 執行、付款與營運

| 能力 | 狀態 | 限制 |
| --- | --- | --- |
| 訂單意圖模型與 API（order intent / authorize / submit / cancel / reconcile） | `IMPLEMENTED_LOCALLY_VERIFIED` | 只在本機 paper 層運作 |
| IBKR 執行代理控制流 | `SCAFFOLD_REFUSES` | `IBKRBrokerTransport` 在 connect / submit / cancel 前一律拒絕；只允許 `FakeBrokerTransport` 離線測試；`ALLOW_LIVE_TRADING` 必須顯式開啟且本輪未開啟 |
| 真實 broker 連線與落單 | `BLOCKED_EXTERNAL` | 需 IBKR 帳戶與 IB Gateway |
| Stripe 付款 adapter | `IMPLEMENTED_UNVERIFIED_EXTERNAL` | 使用官方 SDK 與真實 API；預設 `enable_live_payments=false`，本輪未啟用、未用真實金鑰 |
| 真實付款收取 | `BLOCKED_EXTERNAL` | 需 Stripe 帳戶、`STRIPE_SECRET_KEY`、`STRIPE_WEBHOOK_SECRET` |
| 健康檢查 `/health`、`/health/ready` | `IMPLEMENTED_LOCALLY_VERIFIED` | ready 檢查 DB（critical）與 Redis（optional）；不洩漏例外文字 |
| 結構化日誌 / request ID / 指標 / tracing / 告警 | `NOT_IMPLEMENTED` | 只有模組層 Python logging；無 request-id middleware、無 metrics、無 alerting |
| 備份／還原演練 | `BLOCKED_RESOURCE` | 與 production build 共用資源門檻，見 §5 |
| 公開部署 | `BLOCKED_EXTERNAL` | 需雲端選擇、域名、TLS、secrets 管理 |

---

## 4. US / HK 逐項驗證狀態

| 項目 | US | HK | 證據層級 |
| --- | --- | --- | --- |
| 標的身份（symbol、交易所、資產類別） | 10 檔已入目錄 | 9 檔已入目錄 | 宣告值；未逐檔向交易所核對 |
| 幣別 | USD | HKD | 宣告值 |
| 市場時區與交易時段 | `America/New_York` | `Asia/Hong_Kong` | 宣告值；時段與假期未對照官方日曆 |
| 板數 | 1 | 100–500（逐檔宣告） | 宣告值 |
| 最小跳動 | 宣告 | 宣告 | 宣告值 |
| 歷史 bar 來源 | demo（預設） | demo（預設） | 真實 provider 已接線但從未以憑證運行 |
| 回測／重播 | 本機驗證 | 本機驗證 | 同一引擎；以 demo 資料 |
| 紙上執行 | 本機驗證 | 本機驗證 | 虛擬現金 |
| 真實 broker 執行 | `SCAFFOLD_REFUSES` | `SCAFFOLD_REFUSES` | 傳輸層拒絕 |
| 跨市場資金換算 | `NOT_IMPLEMENTED` | `NOT_IMPLEMENTED` | 無 FX 路徑 |

**結論**：US／HK 的「同一套 provider-independent 身份與市場結構」已實作並在本機驗證；
**真實行情與真實交易對兩地都未驗證**，而且兩地金額目前不能換算。

---

## 5. 被阻塞項（本輪不可繞過）

| 阻塞 | 性質 | 實測 |
| --- | --- | --- |
| Production build、production-mode smoke、production Core Web Vitals、備份還原演練 | `BLOCKED_RESOURCE` | 資料卷剩 **1.6 GiB**（門檻 10 GiB）；swap 已用 8976.5 / 10240 MB；free pages ≈ 57 MiB |
| LSEG 真實行情 | `BLOCKED_EXTERNAL` | 無 LSEG 帳戶與憑證 |
| IBKR 真實落單 | `BLOCKED_EXTERNAL` | 無 IBKR 帳戶與 IB Gateway |
| Stripe 真實收款 | `BLOCKED_EXTERNAL` | 無 Stripe 金鑰與 webhook secret |
| 公開部署 | `BLOCKED_EXTERNAL` | 無雲端與域名 |

資源門檻依既定 light/build gate，**沒有被降低**。未達門檻時不啟動 build，不以
「跳過」冒充通過。

---

## 6. 刻意不存在（by design，非缺陷）

- 真實資金交易、券商連線、交易所直連
- 真實行情使用（預設關閉，且從未以憑證啟用）
- 付款收取（預設關閉）
- 公開研究分享、外送通知
- 投資建議、投資顧問、保證回報
- 多資產組合回測、做空、槓桿／保證金、日內高頻

---

## 7. 驗證狀態摘要（本輪實測）

| 項目 | 結果 | 備註 |
| --- | --- | --- |
| 後端 error-safety 回歸 | 9 passed | `test_error_response_safety.py` |
| 後端 auth 限流回歸 | 5 passed | `test_auth_rate_limit.py` |
| 上述兩檔合跑 | 14 passed | 10.84 s |
| 後端批次 A（affected） | 179 passed | 102.69 s |
| 後端批次 B（regression） | 281 passed | 146.27 s |
| 後端批次 C（affected + regression） | 306 passed, 3 skipped | 306.44 s；3 條需真實 PostgreSQL row-lock，SQLite 上 skip（非 xfail） |
| 前端 UX 巡檢 | 36 截圖、0 issues | 14:06Z 刷新，對應現行碼 |
| 無障礙探測 | 0 zoom overflow / 0 clipping / 0 invisible focus / 0 slow reduced-motion | 19 條路由 |
| 顏色對比 | 0 findings across 19 routes | 4.5:1 門檻 |
| `/health` | 200 `{"status":"healthy"}` | 3.6 ms |
| `/health/ready` | 200，DB 12.67 ms，Redis `not_configured` | 13.4 ms |
| Production LCP / INP / CLS | **未量測** | 被 build 資源門檻阻塞 |
| DB query counts、全庫單次完整 suite | **未量測** | 資源門檻 |

所有日誌、截圖、JSON 證據位於 `.local/evidence/2026-10-30-productization/`，
**不納入版控**（依指令：不提交 artifact、log、證據截圖）。

---

## 8. Release readiness 判定

| 候選等級 | 判定 | 依據 |
| --- | --- | --- |
| `INTERNAL_CANDIDATE_READY` | **達標（有界）** | 本機研究核心、帳號安全、UX／a11y 已有本機實測；無真實行情、無真實交易、無付款 |
| `BETA_CANDIDATE_READY` | **未達標** | 欠 production build、production smoke、production CWV、安全審計、法務審閱、真實 provider 驗證 |
| `ENGINEERING_BLOCKED`（build 部分） | **成立** | 資源門檻 1.6 GiB vs 10 GiB |

**本輪結論**：可作為**內部試用候選**在單機使用；不可對外宣稱 production-ready、
不可用於真實資金、不可視為 US/HK 真實行情或真實交易已驗證。

---

## 9. 相關文件

- `docs/phase1-known-limitations.md` — 策略、執行模型、基礎設施限制
- `docs/paper-trading-limitations.md` — 紙上交易限制
- `docs/us-hk-provider-integration-summary.md` — provider-independent 身份與市場結構
- `docs/deployment.md` — 部署前置與設定
- `docs/external-prerequisites-checklist.md` — 外部資源前置清單
- `docs/stripe-billing-config.md` — Stripe 設定（預設關閉）
- `docs/ibkr-execution-agent-setup-guide.md` — IBKR 代理設定（本輪拒絕副作用）
- `docs/risk-policy.md` — 風控政策
- `.local/evidence/2026-10-30-productization/` — 本輪實測證據（本機，不入版控）
