ALETHRA — STANDING GITHUB SYNC POLICY

Owner 的持續工作流程要求：

每個完整修改批次完成後，自動執行：
檢查 → commit → push → 遠端驗證。
不要再詢問「是否需要 commit／push」。
不要以「修改只在本機」作為正常交付結尾。

這項要求取代本專案先前一般指令中的
「只 commit、不 push」限制。
不取代工具、組織、OS 或系統的強制批准要求。

一、範圍

只適用於：
GitHub repository：
TommyLam0915-LCW/alethra

允許自動同步的開發分支：
- feat/strategy-revision-and-research
- live1-impl

每個 worktree 只推送自己當前的同名分支。
禁止自行切換、合併或同步另一個 worktree。

不得直接推送 main 或其他未列出的分支。
不得 force push、刪除遠端分支、
改寫已發布歷史或更換 remote。

二、每個修改批次

1. 確認工作目錄、當前分支、HEAD、
   Git remote 的 fetch／push 目標。

2. 確認目標確實是上述 Alethra repository。
   remote 名稱叫 origin 並不足夠，
   必須核對實際 repository。

3. 檢查完整待發布 commit 範圍，
   不只查看最後一個 commit。
   若有不屬於本次工作、來源不明的未推送 commit，
   不得連帶發布。

4. 只 stage 本批修改。
   不使用 git add . 或 git add -A
   把無關、未追蹤內容全部加入。

5. 檢查 diff、格式、必要測試與秘密洩漏。
   不提交：
   .env、token、密碼、私鑰、
   DB dump、真實使用者資料、
   未批准資料集、模型 artifact、
   執行日誌或暫存檔。

6. 測試通過後建立有意義的 commit，
   自動正常 push 到同名開發分支。

7. 推送後查詢遠端分支 SHA，
   確認與本機 HEAD 相同。
   未核對前不得宣稱已同步。

8. 沒有修改或沒有新 commit 時，
   不建立空 commit、不重複推送。

三、失敗處理

- 測試失敗：先修復，不宣稱完成。
- 認證／網路失敗：保留本機 commit，
  回報精確阻塞，不反覆無限重試。
- 遠端出現其他人的提交：
  不 force push，不自行覆寫或合併，
  回報分支分歧。
- 權限不足：不能改用其他身分、
  API 或路徑繞過安全限制。
- 不因推送失敗撤銷已完成的本機工作。

四、邊界

自動 push 不代表批准：
merge、PR、release、production deployment、
DB migration、排程啟動、交易或付費。

設定時先核對目標分支的 push 觸發器。
若 push 會連帶部署或執行其他實際操作，
先一次性釐清並取得所需批准。

若本次任務明確另有「不要發布」要求，
遵守該次較具體要求。

五、交付格式

每批結束簡短回報：
- 分支。
- 本機完整 commit SHA。
- 修改摘要。
- 實際測試結果。
- push 成功／失敗。
- 遠端 SHA 是否與本機一致。

不能只說「已上傳」而沒有遠端驗證。
