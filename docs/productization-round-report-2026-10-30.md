# Alethra 產品化、UX/UI、效能與發布就緒 — 本輪最終報告（2026-10-30）

狀態：**ENGINEERING_BLOCKED**（production build / smoke / production CWV 未執行，資源門檻未達）。
產品面判定：**INTERNAL_CANDIDATE_READY（有界）** — 可在單機作內部試用；不構成 production-ready、
不構成真實行情或真實交易的驗證。

| 項目 | 值 |
| --- | --- |
| Repo | `/Users/tommylam0915/Documents/Qoder/2026-10-24/alethra-unified-dev` |
| 分支 | `feat/strategy-revision-and-research` |
| 起始 HEAD | `f172afb57435132234cbfc7aecc9ef0b7caaed87` |
| 本輪 commits | `56b5a4e` `82967c2` `e5e4bb3` `bec111f` + 本文件所在 docs commit |
| Push / merge / deploy | **無**（依指令） |

---

## A. 範圍

在既有產品範圍內完成：UX/UI 一致性與可發現性、無障礙、可靠度、帳號安全、量測型效能、
發布範圍文件化。沒有新增任何相依套件，沒有改動共享 runtime schema 或權益，沒有使用真實
行情、真實券商、付款或外傳資料。

發布範圍與能力矩陣見 `docs/release-scope-2026-10-30.md`。

---

## B. 基線與資源

| 資源 | 實測 | 判定 |
| --- | --- | --- |
| 資料卷剩餘 | 1.6 GiB | 低於 10 GiB build 停止線 → **BLOCKED** |
| Swap | 已用 8976.5 / 10240 MB | 無 build 餘裕 |
| Free pages | ≈ 57 MiB | 無 build 餘裕 |

因此 `next build`、production-mode smoke、production LCP/INP/CLS、備份還原演練**未執行**。
這些項目記錄為 BLOCKED 並附數字，不以「跳過」冒充通過。

---

## C. 已修復的缺陷（含根因）

1. **例外文字外洩（安全）**
   根因：provider 失敗的 503 回應與 job/run 的 `error_details` 直接 `str(exc)`，可帶出絕對路徑、
   DSN、bearer token。修法：新增 `backend/app/lib/error_safety.py`，在 4 個序列化邊界與 8 個
   持久化點套用；完整例外仍寫入伺服器日誌。回歸：9 條測試。
2. **登入／註冊無限次（安全）**
   根因：兩個憑證端點是唯一無認證的寫入面，卻未接上 repo 內已有的 per-IP 限流器，等於
   密碼 oracle。修法：登入 20 次/60 秒、註冊 5 次/300 秒。回歸：5 條測試。
3. **限流 bucket 衝突（接線時發現的潛在缺陷）**
   根因：`rate_limit_dependency` 只以 `rl:{ip}` 為鍵，兩個端點會共用同一個計數器而各自對照
   自己的門檻，互相偷用預算。修法：加入 `bucket` 參數，`auth_login` / `auth_register` 分開。
   回歸：`test_register_and_login_keep_separate_budgets`。
4. **背景輪詢未理會隱藏分頁（效能）**
   實測 `/workspace` 隱藏時 70 秒仍有 2 次請求、畫面不可能有可見變化。修法：
   `document.hidden` 時跳過 tick，回到前景時刷新一次。範圍：營運狀態、狀態晶片、
   訓練 job 輪詢、個人策略輪詢。
5. **同一頁雙重 AppShell／導航入口缺失（UX）**
   根因：各頁自行包 `AppShell`，新頁面容易漏接而突然失去導航。修法：殼層移入 route
   layout；補上「研究草稿」「執行意圖」「個人研究記憶」入口。
6. **狀態色彩不一致（UI／無障礙）**
   根因：多頁保留淺色主題 inline 顏色（例：`#fff3cd` / `#856404`）貼在深色畫布上。
   修法：統一 `.status-pill` 語彙與 `statusTone()`；顏色改由 token 提供。
7. **日期格式不一致（UI）**
   根因：`toLocaleDateString('zh-Hant')` 產生未補零日期；`new Date('YYYY-MM-DD')` 在 UTC
   以西會退一天；時間戳帶「上午/下午」與秒數。修法：`formatDate` / `formatDateTime`。
8. **對比度不足（無障礙）**
   基線 230 項 → 換 token 後 15 項 → 修正後 **0 項 / 19 條路由**。主要修正：
   `--text-muted` 由 2.8:1 提升到 ≥4.5:1；accent 文字與白字按鈕拆成
   `--primary-text` / `--primary-solid`；亮色填底改用 `--text-on-bright`。
9. **焦點環與停用狀態缺失（無障礙）**
   修法：全域 `:focus-visible` outline；`disabled` 統一樣式；AI 輸入框補
   `aria-label`。
10. **文件失實（文件）**
    `docs/phase1-known-limitations.md` 聲稱「無 rate limiting」，在修改前後都不成立；
    已改為準確描述（含多 worker ×N、重啟清空、反向代理後 IP 問題）。

---

## D. UX / UI

- 主要路由全部有同一殼層與導航；`/experiments` 維持導向 `/dashboard`（刻意，不是缺陷）。
- 狀態名稱與顏色跨頁一致；金錢／百分比／日期格式統一。
- NULL 不以 0 或假資料呈現（沿用既有契約，本輪未放寬）。
- 巡檢：36 次路由×視窗造訪，**0 issues**（console error、HTTP 非 200、橫向溢出、
  未命名控制項均為 0）。

## E. 無障礙

| 檢查 | 結果 |
| --- | --- |
| 對比度（4.5:1） | 0 findings / 19 routes |
| 200% zoom 溢出／裁切 | 0 |
| 焦點環不可見 | 0 |
| reduced-motion 下過慢動畫 | 0 |

未做：完整 WCAG 2.2 AA 稽核（例如螢幕閱讀器實測、對話框焦點鎖定的全路由覆蓋）。

## F. 效能

| 項目 | 結果 |
| --- | --- |
| 隱藏分頁輪詢 | 已修（4 個輪詢點） |
| 後端 35 個讀取端點延遲抽樣 | 已量測（見 `r5-performance.md`） |
| `/ml/models` 離群 | 已定位為 in-process `list_models()` 72.4 ms，非 I/O |
| Production LCP / INP / CLS | **未量測**（build 被資源門檻阻塞） |
| DB query counts | **未量測** |

dev server 的 LCP/CLS 只作為除錯訊號，**不當作 production 數字**。

## G. 安全與帳號產品化

- 憑證端點限流（新）；API key 限流與 DB 配額（既有，已核實）。
- 錯誤外洩面關閉；`mark_error` 從不持久化訊息；live DB 6 個錯誤欄位 0 筆非空。
- 候選免密登入：只接受 loopback peer、伺服器端 allowlist、永不簽發 admin、
  `APP_ENV=development` 以外 fail closed。
- 未做：MFA、密碼重設、email 驗證、session 撤銷、refresh token 輪替、第三方安全審計。

## H. 可靠性 / 營運 / 發布工程

- `/health` 200（3.6 ms）；`/health/ready` 200，DB 12.67 ms，Redis `not_configured`。
- 結構化日誌、request ID、metrics、tracing、告警：**NOT_IMPLEMENTED**（已在發布範圍文件列明）。
- 發布工程：版控 kept；未 push、未 merge、未 deploy；未刪任何歷史 artifact。

## I. 測試證據

| 批次 | 結果 |
| --- | --- |
| error-safety + auth 限流（合跑） | 14 passed / 10.84 s |
| affected（A） | 179 passed / 102.69 s |
| regression（B） | 281 passed / 146.27 s |
| affected + regression（C） | 306 passed, 3 skipped / 306.44 s |
| TypeScript | `tsc --noEmit` exit 0 |

3 條 skip 為需要真實 PostgreSQL row-lock 的測試，在 SQLite 上 skip，**不是 xfail**。
證據檔案在 `.local/evidence/2026-10-30-productization/`，依指令**不納入版控**。

未執行：production build、prod smoke、完整單次 suite、DB query counts、production CWV。

## J. 阻塞、殘餘風險、狀態

**阻塞（不可繞過）**：build/smoke/CWV（資源）；LSEG、IBKR、Stripe、雲端部署（外部）。

**殘餘風險**：
1. 限流是行程內計數：多 worker 上限 ×N、重啟清空；反向代理後需真實客戶端 IP。
2. Provider 的 `_last_error` 來源仍可能含內部字串（輸出時已過濾，但源頭未改）。
3. 未實作 FX 換算：US 與 HK 金額不可混算。
4. US/HK 標的板數、tick size、交易時段為宣告值，未逐檔向交易所核對。
5. 個人化模型以合成資料訓練，**未證明任何預測效力**。
6. `/training` 的 eligibility 面板 CLS 0.401（資料驅動，未修）。

**狀態判定**：`ENGINEERING_BLOCKED`（build 部分）＋ `INTERNAL_CANDIDATE_READY`（本機試用）。
`BETA_CANDIDATE_READY` **未達**：欠 production build/smoke/CWV、安全審計、法務審閱、
真實 provider 驗證。

### Owner 短檢查（共 5 項，全部可選）

1. 用本機候選堆疊開啟 `/dashboard`，確認左側導航有「研究草稿」「執行意圖」「個人研究記憶」。
2. 開 `/training`，確認狀態徽章顏色與 `/execution` 同義同色。
3. 開任一含日期的列表，確認日期為 `YYYY/MM/DD`、時間為 24 小時制。
4. 用 Tab 走一遍 `/models`，確認每個可聚焦元素都有可見焦點環。
5. 用一個不存在的電郵連續登入超過 20 次，確認之後回 429（限流以 IP 為鍵，不會鎖帳戶）。

以上皆非必要；未完成亦不改變本輪判定。

---

## 相關文件

- `docs/release-scope-2026-10-30.md` — 發布範圍與能力矩陣
- `docs/phase1-known-limitations.md` — 已更正限流描述
- `.local/evidence/2026-10-30-productization/` — 本輪實測證據（本機）
