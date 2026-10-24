# Alethra 主要功能完成 Implementation Map

**版本**: e3371e7 (auto/candidate-integration)  
**日期**: 2026-10-22  
**狀態**: 等待一次性批准

---

## 1. 目前狀態摘要

### 已完成（可立即使用）

| 功能 | 狀態 | 證據 |
|---|---|---|
| 使用者認證與權限 | ✅ 完成 | 38 tests passing |
| 實驗與策略版本管理 | ✅ 完成 | 鎖定、版本化、審計追蹤 |
| 回測引擎（demo 資料） | ✅ 完成 | SMA crossover, buy-and-hold, 每日帳務 |
| 向前模擬（record-only） | ✅ 完成 | Phase 3A, 68 tests |
| 人類決策記錄（B1） | ✅ 完成 | 36 tests, audit atomicity verified |
| 模型訓練管線（sklearn） | ✅ 完成 | 離線測試通過，未真實執行 |
| 前端基礎頁面 | ✅ 完成 | Next.js, 實驗/模擬/模型頁面 |
| Twelve Data adapter | ⚠️ 已實作，未提交 | staged, 離線測試通過 |

### 未完成（本次任務範圍）

| 功能 | 缺失 | 估計工作量 |
|---|---|---|
| **A. 真實市場資料** | 前端整合 Twelve Data、資料集保存、UI 展示 | 1-2 天 |
| **B. 回測每日紀錄 UI** | 前端展示每日帳務、交易明細、費用 | 1 天 |
| **C. B2 人類決策驅動模擬** | replay session、逐步決策、模擬成交、冪等 | 2-3 天 |
| **D. 決策日誌與反思** | 原始紀錄、修正、反思分離呈現 | 1 天 |
| **E. 真實 AI 分析** | 接入 OpenAI-compatible provider、證據引用 | 1-2 天 |
| **F. 個人模型訓練與推論** | 真實執行 fit()、保存模型、推論 UI | 1-2 天 |

**總計**: 7-11 天集中工作

---

## 2. 三個交付切片

### 切片 1: 真實資料 → 回測 → 每日紀錄 → 瀏覽器查閱

**交付成果**:
- [ ] 前端選擇標的（美股單一日線）
- [ ] 從 Twelve Data 取得真實歷史日線
- [ ] 保存為本次研究資料集（版本化）
- [ ] 執行回測，顯示交易明細、費用、每日現金/持倉/估值
- [ ] 重新查閱同一次結果
- [ ] 瀏覽器截圖 + API/DB 核對

**修改檔案**:
- `frontend/app/experiments/[id]/data.tsx` (新增資料集頁面)
- `frontend/app/experiments/[id]/backtest.tsx` (增強每日帳務展示)
- `backend/app/api/experiments.py` (資料集綁定端點)
- `backend/migrations/versions/xxxx_dataset_versioning.py` (資料集版本表)

**外部依賴**: Twelve Data API key

---

### 切片 2: 人類 replay 操作 → 模擬成交 → 日誌與反思

**交付成果**:
- [ ] 建立 replay session（明確標記為歷史重播）
- [ ] 每一步只提供當時可用的歷史資料
- [ ] 使用者輸入 BUY/SELL/HOLD + 數量 + 理由
- [ ] 風險檢查 → 模擬成交或拒絕
- [ ] 更新現金、持倉、估值
- [ ] 冪等：同一決策重試不重複成交
- [ ] 修正紀錄（不重放原成交）
- [ ] 事後反思筆記
- [ ] 瀏覽器操作截圖 + 帳務核對

**修改檔案**:
- `backend/app/api/simulations.py` (增強 replay 邏輯)
- `backend/app/api/human_decisions.py` (已存在，需整合 replay)
- `frontend/app/simulations/[id]/replay.tsx` (新增 replay 頁面)
- `frontend/app/simulations/[id]/journal.tsx` (決策日誌頁面)

**外部依賴**: 無（使用切片 1 的資料集）

---

### 切片 3: 真實 AI 分析 → 個人訓練 → 推論與評估

**交付成果**:
- [ ] 接入真實 AI provider（OpenAI-compatible）
- [ ] 分析使用者決策日誌，引用具體決策/交易 ID
- [ ] 區分事實、推測、使用者確認
- [ ] 使用者可確認/駁回分析標籤
- [ ] 建立個人訓練資料（從決策日誌）
- [ ] 真實執行 `fit()`，保存模型檔案
- [ ] 時間序列分割，檢查洩漏
- [ ] 與 baseline 比較
- [ ] 推論 UI：輸入決策前資訊 → 輸出偏離預測
- [ ] 模型版本、停用、回退
- [ ] 跨使用者隔離驗證

**修改檔案**:
- `backend/app/services/ai_analysis.py` (真實 AI 呼叫)
- `backend/app/api/analysis.py` (分析端點)
- `backend/app/services/training.py` (真實 fit/inference)
- `backend/app/api/models.py` (模型管理端點)
- `frontend/app/experiments/[id]/analysis.tsx` (AI 分析頁面)
- `frontend/app/models/[id]/inference.tsx` (推論頁面)

**外部依賴**: AI provider API key, 預算上限

---

## 3. 必要資源確認（一次性批准）

### 外部服務

| 服務 | 用途 | 預估用量 | 費用上限 | 狀態 |
|---|---|---|---|---|
| **Twelve Data** | 美股日線歷史資料 | ~50 請求/驗收 | 免費額度內 | ⚠️ 需確認 key 狀態 |
| **AI Provider** | 決策分析、行為摘要 | ~20 請求/驗收 | $5 USD | ⚠️ 需確認憑證 |

### 內部資源

| 資源 | 用途 | 狀態 |
|---|---|---|
| **PostgreSQL** | 驗收環境資料庫 | ✅ 可用（localhost:5432） |
| **Redis** | 背景任務佇列 | ⚠️ 需確認是否運行 |
| **本機端口** | Backend 8000, Frontend 3000 | ✅ 可用 |

### 需要 Owner 確認

1. **Twelve Data API key**:
   - 舊 key 是否已撤銷？
   - 新 key 是否已設定在環境變數？
   - 允許用途：歷史日線查詢、研究展示、模型訓練（非即時交易）

2. **AI Provider 憑證**:
   - 使用哪個 provider？（OpenAI / Azure OpenAI / 其他 compatible）
   - API key 是否已設定？
   - 允許傳送：使用者決策日誌、交易紀錄（匿名化，不含其他使用者資料）
   - 費用上限：$5 USD（超過即停止）

3. **資料庫**:
   - 使用 `ai_investment_lab_acceptance` 專用 DB？
   - 允許建立 schema、執行 migration？
   - 驗收完成後是否保留或清除？

4. **市場資料使用範圍**:
   - 允許私人展示（本機瀏覽器）
   - 允許保存為研究資料集（綁定實驗）
   - 允許用於回測與模型訓練
   - 不允許：公開分享、即時交易、轉售

5. **遠端部署**:
   - 本輪是否需要遠端部署？
   - 若需要：主機、存取方式、費用批准
   - 建議：先本機驗收，確認功能完整後再考慮部署

---

## 4. 執行方式

### 批准後立即執行

1. **環境準備** (30 分鐘):
   - 確認 API keys 已設定
   - 啟動 PostgreSQL + Redis
   - 執行 migration
   - 啟動 backend + frontend

2. **切片 1 實作** (1-2 天):
   - 完成真實資料整合
   - 瀏覽器驗收
   - 記錄證據

3. **切片 2 實作** (2-3 天):
   - 完成 replay session
   - 瀏覽器操作驗收
   - 記錄證據

4. **切片 3 實作** (2-3 天):
   - 完成 AI 分析 + 模型訓練
   - 瀏覽器驗收
   - 記錄證據

5. **端到端驗收** (1 天):
   - 兩個測試帳戶
   - 完整 16 步驗收劇本
   - 最終報告

### 停止條件

只有以下情況停下：
- 新增付費服務或超出預算
- 需要公開部署或接觸真實資金
- 需要改動未授權的資料庫或其他 worktree
- 發現秘密曝光、跨使用者存取或資料破壞
- 需要明顯超出核准功能範圍

其餘問題集中記錄，繼續推進。

---

## 5. 最終交付

提供一份 owner 可操作的報告：

- 真實環境網址（本機 localhost:3000）
- 版本識別（git commit SHA）
- 每個驗收步驟的 pass/fail/blocked
- 去秘密的瀏覽器截圖
- 資料集、run、session、decision、model ID
- 外部請求數、AI 用量、實際費用
- Migration 及資料保存結果
- 測試結果摘要
- 已完成能力、限制、阻擋問題
- Owner 可重做的操作指南

最終標記：

```
REAL DATA INTEGRATION: PASS / FAIL / BLOCKED
BACKTEST AND DAILY ACCOUNTING: PASS / FAIL / BLOCKED
HUMAN REPLAY EXECUTION: PASS / FAIL / BLOCKED
DECISION JOURNAL: PASS / FAIL / BLOCKED
REAL AI ANALYSIS: PASS / FAIL / BLOCKED
PERSONAL TRAINING AND INFERENCE: PASS / FAIL / BLOCKED
MULTI-USER ISOLATION: PASS / FAIL / BLOCKED
END-TO-END OWNER ACCEPTANCE: PENDING / ACCEPTED
```

---

## 6. 等待批准

**請 Owner 確認**:

1. Twelve Data API key 狀態與授權
2. AI Provider 憑證與費用上限
3. 驗收資料庫名稱與保留策略
4. 市場資料使用範圍
5. 本輪是否需要遠端部署

批准後立即按切片 1 → 2 → 3 順序執行，不再逐檔請求。
