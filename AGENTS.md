# Agent Delivery Rules

每次完成有 repository 修改的任務，必須遵循以下規則：

## 1. 狀態檢查
- 檢查 modified、staged、untracked 及未推送 commits
- 執行 `git status --short --branch` 確認當前狀態
- 執行 `git diff --stat` 及 `git diff --cached --stat` 檢查變更

## 2. 安全審查
- 保存必要程式、測試及文件
- 不得提交：
  - `.env` 真實值、API keys、tokens、密碼、私鑰
  - 帶憑證的連線字串
  - 私人或 production 資料、DB dumps、本機 DB
  - `.venv`、`node_modules`、cache、build artifacts
  - 未審查 logs、無關檔案或不必要大型產物

## 3. 提交原則
- 不使用 `git add -A`，按明確路徑 staged
- 提交前檢查 staged diff、whitespace 及敏感資訊
- 未完成功能可用清楚標示的 WIP commit 保存
- 報告必須保留未完成與未驗證事項
- 執行適合本次修改的測試；測試失敗時可保存 WIP，但不得聲稱通過

## 4. 外部寫入確認
- 遵守工具及平台的外部寫入確認政策
- 需要確認時，先列出：
  - 確切 repository、remote、branch
  - 將提交／推送的檔案與 commits
  - commit message
  - 是否包含 WIP
- 再要求批准；不要繞過確認

## 5. 推送驗證
- 推送後必須直接查詢遠端：`git ls-remote <實際 remote> refs/heads/<branch>`
- 比較本機 branch 完整 SHA 與 GitHub 遠端 branch 完整 SHA
- 只有二者一致，才標記 PUSHED / SYNCED
- 不要只看本機 remote-tracking ref 就宣稱完成
- 無法推送、權限不足或需認證時，明確報告：
  - 已保存的本機 commit SHA
  - 尚未同步的內容
  - 具體阻擋及需要使用者做的動作

## 6. 狀態報告
最終回覆必須包含：

### GitHub 保存狀態
- Repository
- Branch
- Remote
- 本次 commits：完整 SHA + message
- 遠端 HEAD：完整 SHA
- 本機與遠端是否一致
- Working tree 是否乾淨
- 剩餘未提交／未推送內容
- 因秘密、體積或其他原因未上傳的項目

### 驗證結果
- 對應程式 SHA
- 精確測試／檢查命令
- Raw summary
- Exit codes
- 未執行或受阻範圍

### 尚待處理
- 未完成開發
- WIP 狀態
- 待批准的提交／推送
- 其他安全移轉需求

## 7. 禁止操作
- 不使用 force-push
- 不使用破壞性 reset (`git reset --hard`)
- 不自動刪除 stash (`git stash drop`)
- 不覆蓋其他人的提交
- 不自動合併無關 branches 或套用 stash
- 如需修改他人歷史或處理衝突，先取得確認

## 8. 同步狀態標籤
- 如果尚未 push：明確寫「LOCAL COMMITTED / NOT PUSHED」
- 不得寫「已永久上傳」「交付完成」或「完全同步」（除非遠端 SHA 核對一致）
- 如果本輪沒有程式修改：寫「無新修改，已核對同步狀態」，不要建立空 commit

## 9. 交接注意事項
下一個代理必須先讀此文件，並：
- 核對當前 branch 及 HEAD
- 檢查是否有未推送的 commits
- 確認 working tree 狀態
- 遵循上述規則完成後續任務

---

**最後更新**: 2026-11-09  
**適用分支**: live1-impl  
**Repository**: TommyLam0915-LCW/alethra
