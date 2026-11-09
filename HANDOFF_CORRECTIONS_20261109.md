# Alethra 交接更正記錄（2026-11-09）

本文件記錄先前交接文件的更正與補充。

## 一、`live1-impl` 分支狀態更正

**先前錯誤聲明：** 「`live1-impl` 分支在本機與遠端都找不到」

**更正：** `live1-impl` **確實存在於遠端**。

- Remote URL: `https://github.com/TommyLam0915-LCW/alethra.git`
- Remote branch: `refs/heads/live1-impl`
- Remote HEAD: `3eaee13f0b768a33b969eb0a4b0f38f5728e5dc5`
- 包含 `AGENTS.md`（agent delivery rules）
- 最新 commit: "docs: add new laptop restore record and agent delivery rules"

**誤判原因：** 先前盤點時只檢查本機分支列表（`git branch -a`），沒有在 fetch 後重新核對遠端分支。本機確實沒有 `live1-impl` 分支，但遠端存在且可從 `origin/live1-impl` 存取。

**教訓：** 判斷分支是否存在時，必須同時檢查本機與遠端，且以 fetch 後的遠端狀態為準。

## 二、Stock Data 與 Cancel 工作保存位置

**Stock data changes（已推送）：**
- `a844e0f` — "Add comprehensive batch test results: 16 rules × 3 markets × 9 instruments"
  → `origin/feat/strategy-revision-and-research`
- `2c4048d` — "Add multi-market support: stock and forex providers with synthetic data"
  → `origin/feat/strategy-revision-and-research`
- `69c6ec5` — "feat: real US/HK market data pipeline — instrument selection to backtest"
  → `origin/auto/real-market-pipeline`
- `3ebfdd9` — "feat: implement provider-independent US/HK market support"
  → `origin/feat/real-integrations`

**Cancel implementation（已推送）：**
- `838ab334` — "Add real IBKR transport and platform-initiated cancel flow"
  → `auto/candidate-integration`, `auto/execution-recovery-reconcile`, `auto/integration-e2e`, `auto/model-lifecycle`
- `2ecff6aa` — "Fix webhook atomicity and cancelled-subscription guard"
  → `auto/billing-webhook-application` (local + remote)

**結論：** 這些工作已在遠端分支，無需重複推送。

## 三、`verify-token.txt` 檢查結果

**檢查範圍：**
1. `git ls-files | grep verify-token` — 追蹤檔案列表：**未找到**
2. `git log --all --full-history -- verify-token.txt` — 完整歷史：**未找到**
3. `/tmp` worktree 目錄（`/private/tmp/alethra-ks-fix`, `/private/tmp/alethra-pr12-fix`）：**目錄已消失**

**結果：** `verify-token.txt` **從未進入 git 歷史**（未追蹤、未提交）。

**限制聲明：** 
- /tmp worktree 目錄已消失，無法核對其原始內容
- 「未發現進 git」不等於「證明所有渠道均無洩漏」
- 可能存在其他未檢查的備份管道（手動複製、其他暫存目錄等）
- 本檢查僅證明 git 歷史中無此檔案

**建議：** 若該 token 仍有效，建議輪換；若已失效，記錄為歷史事件。

## 四、`verify_inference_usage.py` 保存決策

**檔案位置：** `/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/verify_inference_usage.py`

**問題：** 第 83 行包含硬編碼的 JWT secret：
```python
JWT_SECRET = "acceptance-test-secret-minimum-32-chars-long"
```

**決策：** **不提交到 git**。

**理由：**
- 雖為測試用憑證，但仍屬 secret literal
- Commit `aaf3b01` 明確記錄：「verify_inference_usage.py is intentionally excluded (contains a hardcoded JWT_SECRET literal)」
- 該檔案是執行腳本（會連接 `localhost:8002` 和 `postgresql://test:test@localhost:5434`），不是測試規格
- 保存為 WIP 但含 secret 不符合安全原則

**現狀：** 保留為 untracked file，不進入 `backup/auto-candidate-integration-2026-10-24` 分支。

**建議：** 
- 若需跨裝置保存，先 redact secret（替換為環境變數讀取）
- 或保存為本地備份（不進 git）
- 不建議直接提交含 secret literal 的檔案

## 五、本次推送的分支與 SHA

**已推送（2026-11-09）：**

1. `auto/stored-data-backtest-integration`
   - Remote HEAD: `0cf8c310184e055c58cac54f0032b1b12a6feef4`
   - 3 commits: PROGRESS.md 文件更新
   - Fast-forward push 成功

2. `auto/candidate-integration`
   - Remote HEAD: `e3371e7f23b09c0ef9d9b662f6cd9a126ad46085`
   - 3 commits: code + tests + docs
   - Fast-forward push 成功

3. `backup/auto-candidate-integration-2026-10-24`
   - Remote HEAD: `aaf3b01262c2a3237af4cd453c0686b56809c479`
   - 5 preservation commits（verification scripts, docs, frontend, backend tests, migrations）
   - **排除** `verify_inference_usage.py`（含 secret）
   - New branch push 成功

## 六、未完成項目

1. **Demand-intelligence-handoff worktree**：262 個未提交檔案待處理
2. **Cross-border stash `590a729`**：供應商聯絡草稿待保存到隔離分支
3. **`evidence/audit_backup_20261108_incident/`**：待檢查與決定保存方式
4. **其他 Alethra worktree**：各有 0–2 個未提交檔案，待逐一檢查
5. **非 Git 資料**：環境變數、憑證、本地 DB 等待記錄恢復需求

## 七、測試狀態聲明

**本次推送未執行完整回歸測試。**

- `auto/stored-data-backtest-integration`：3 個 commit 純 PROGRESS.md 文件更新，無程式碼變更
- `auto/candidate-integration`：3 個 commit 含程式碼與測試，但未重新執行測試
- `backup/auto-candidate-integration-2026-10-24`：5 個 preservation commits 標記為「Not validated」

**聲明：** 推送基於「保存未遺失」原則，不宣稱通過完整測試驗收。

---

**文件建立時間：** 2026-11-09  
**建立者：** 客戶代理（測試人員角色）  
**狀態：** 本地草稿，待決定是否提交到 handoff 分支
