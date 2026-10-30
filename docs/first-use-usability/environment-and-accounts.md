# 候選環境與帳號

**狀態：未執行（NOT RUN）。** 本檔描述的是可供真人測試使用的候選環境；目前沒有真實受試者。

## 環境身份

| 項目 | 值 |
| --- | --- |
| 前端 | `http://127.0.0.1:3100`（Next dev，`NEXT_DEV_DIST_DIR=.next/candidate-3100`） |
| 後端 | `http://127.0.0.1:8100`（`GET /health` 回 `{"status":"healthy"}`） |
| 資料庫 | PostgreSQL `alethra_personal_e2e_candidate_20261029`，`127.0.0.1:54321`（disposable） |
| Schema head | `r2s3t4u5v6w7` |
| 背景 worker | `python -m app.workers.training_worker`，pid 檔 `.local/candidate/worker.pid` |
| Worker 心跳 | `.local/candidate/worker-heartbeat.json`（`state`、`beat_at`、`poll_interval_seconds`） |
| 啟動腳本 | `./start_candidate_stack.sh`（resume：只用已保存的候選 profile；不遷移、不 seed、不改憑證；見下節） |
| 候選 profile | `.local/candidate/candidate-profile.env`（mode 600；持久候選身份：candidate id、DB identity、origin、artifact 路徑） |
| 每次執行身份 | `.local/candidate/runtime-identity.env`（mode 600；只含 PID、啟動時間、heartbeat，不含 DB identity） |
| 憑證檔 | `.local/candidate-credentials.env`（mode 600；只由主持人當面使用，不貼進任何文件或對話） |

憑證檔內的鍵名（只列名稱，不含值）：

- `CANDIDATE_EMAIL`／`CANDIDATE_PASSWORD`：已訓練帳號
- `CANDIDATE_THIN_EMAIL`／`CANDIDATE_THIN_PASSWORD`：資料不足帳號
- `CANDIDATE_EMPTY_EMAIL`／`CANDIDATE_EMPTY_PASSWORD`：空白帳號

## 帳號狀態（2026-10-29 只讀普查）

| 帳號 | 儀表板 | 策略版本 | 決策 | 已確認 | 工作階段 | 決策時點 | 同意 | 模型版本 | 訓練 | 下一步 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 已訓練 | 1 個實驗（已鎖定） | 1 | 205 | 205 | 1 | 41 | 有效（1 個版本） | 2（同一快照） | 1 個已完成工作 | 執行推論 |
| 資料不足 | 1 個實驗（已鎖定） | 1 | 5 | 3 | 0 | 0 | 無 | 0 | 無 | 記錄同意 |
| 空白 | 0 | 0 | 0 | 0 | 0 | 0 | 無 | 0 | 無 | 建立第一個實驗 |

補充事實（主持人知道、但不提示受試者）：

- 已訓練帳號的資格徽章顯示「尚未達建議資料充分性門檻」，原因是工作階段 1（建議 30）、決策時點 41（建議 60）、類別覆蓋不足；**這屬建議資料充分性未達，不是訓練資格被拒絕，也不是模型表現被判失敗**——該帳號已有 2 個模型版本。
- 兩個模型版本（v1、v2）都來自同一份快照 `f53c5ae7-ec4d-4b19-9e0b-a60365f50937`，checksum `8f21bbbed981f6ee2b7647095f952067e1d42c1998060a28f6a20a958e81a16b`，205 筆合成資料。
- 資料不足帳號的 5 筆決策中 3 筆已確認；未勾選同意前，「記錄同意」按鈕不可用；勾選範圍與確認聲明是兩個獨立核取方塊。
- 空白帳號在實驗列表與訓練頁都有「建立第一個⋯⋯」入口；資格徽章顯示「未達可訓練條件（見下方原因）」。
- 所有資料為合成示範資料；沒有任何真實市場價格、真實金錢或外部券商連線。
- 時間有兩個不同來源，不要混淆。已核實：候選資料庫的時鐘比主機 UTC 慢約 4 小時 35 分（2026-10-30 00:02 HKT 時，PG 顯示 11:27Z、主機為 16:02Z）；在新連線、短查詢、autocommit 下，`now()`、`transaction_timestamp()`、`statement_timestamp()`、`clock_timestamp()` 彼此相差不到 30 毫秒，但全部都比主機 UTC 慢 16,506 秒，session 時區為 UTC，因此這是資料庫環境時鐘偏移，而不是長交易累積或時區轉換。此鏈路沒有以資料庫時鐘做先後判斷（訓練工作的建立時間用主機時鐘，worker 領取／回收比較也是主機對主機），所以不影響功能結論。介面顯示方面：後端現在以帶 `+00:00` 的 UTC 時間輸出心跳與工作時間，瀏覽器不會再把 UTC 當成本地時間讀。不要為了對齊而調整主機時間或重啟 Docker。

## 啟動、resume 與新候選

| 操作 | 指令 | 行為 |
| --- | --- | --- |
| 沿用候選 | `./start_candidate_stack.sh` | 只讀核對 profile 指定的 DB 存在、schema head 相符、三個 fixture 帳號存在；不遷移、不 seed、不產生憑證、不建立替代庫。process 逐一核對 cmdline 與 `DATABASE_URL` 後才重用；backend／worker 指向不同 DB 時不會宣稱 ready。 |
| 唯讀預檢 | `./start_candidate_stack.sh --plan` | 印出將沿用的 DB 與 `SEED=no`，不啟動 process、不寫入 repo。 |
| 明確重設 | `./start_candidate_stack.sh --reset` | 先停本候選的已核實 process，只 drop profile 指定的 DB，重建並重新 seed。checkout schema head 與 profile 不符時拒絕。 |
| 明確新候選 | `./start_candidate_stack.sh --new-candidate --candidate-id <id>` | 新 identity；拒絕覆寫既有 profile、憑證、log 目錄，亦拒絕共用非空 artifact 目錄（需另一個 checkout）。不因 restart 觸發。 |
| 停止 | `./stop_candidate_stack.sh` | 先核對每個 pid 的身份才送訊號；不合的 process 只報告、不殺。DB 保留。 |

候選身份只存在 profile；`runtime-identity.env` 的舊 PID 不會被當成目前程序身份。要切換 DB 必須用 `--new-candidate` 或 `--reset`，不會因跨日自動切換。

非候選資源（只記錄，本輪未刪除、未搬移）：`alethra_personal_e2e_candidate_20261030` 是啟動腳本舊預設在 2026-10-30 產生的空 seed 測試庫（3 個使用者、1 個同意、210 筆決策、0 模型／工作），不是候選，也不應用於真人測試。

## 任務與帳號對應

| 任務 | 帳號 | 備註 |
| --- | --- | --- |
| A 找回研究 | 已訓練 | 使用已有的實驗與回測結果 |
| B 修改策略並比較 | 資料不足 | 修訂後保留原版；種子只補缺失，不會清除修訂 |
| C 判斷能否訓練 | 空白 → 資料不足 → 已訓練 | 三個狀態各看一次，順序固定 |
| D 比較兩個模型 | 已訓練 | 同一快照的兩個版本；差異小、證據重疊 |
| E 離開再登入 | 資料不足 | 接續 B 的修訂與未完成同意 |

## 復原政策（每場之後）

1. 若在資料不足帳號按了「記錄同意」，到訓練頁按「撤銷同意」，確認同意狀態文字消失。
2. B 產生的修訂版本**保留**；不要刪除、不要重設資料庫。
3. 不要執行任何「重設示範資料」的操作；`start_candidate_stack.sh` 的 resume 不會 seed、不會清空進度，亦不會因跨日改用另一個 DB。
4. 不重置 owner 資料、不改共享 8099 schema／權益、不把候選 DB 當成共享 runtime。
5. 確認 worker 心跳在更新；若離線，重新啟動 worker 並確認心跳恢復、介面回到可用狀態。

## 允許與禁止

- 允許：在本機候選環境進行真人測試、以文字筆記或（另經同意）錄音記錄、在 owner 指定位置保存結果。
- 禁止：自行寄出邀請、把研究資料外傳、付費招募、把 localhost 環境公開到互聯網、寫入虛構訪談／成功率／滿意度。
- 需要任何對外行動時，先取得 owner 批准。
