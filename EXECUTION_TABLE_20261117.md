# Alethra 客戶可用能力執行表

**更新時間**: 2026-11-17（受控每日服務 D1／D2 完成後）  
**基準 SHA**: 5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a (live1-impl)  
**本機狀態**: 未提交修改 130 個已追蹤檔 ＋ 54 個未追蹤檔（實測 `git status --porcelain` 共 184 條；範圍含 skill unit 授權、inference 授權鏈、每日服務、migration、測試修正）  
**量測協議**: SQLite lane 預設共用同一個 `./test.db`（`tests/conftest.py:41`，可被 `ALETHRA_TEST_DB` 覆寫）。實測兩支 pytest 同時跑在同一檔時出現 `sqlite3.OperationalError: no such table: users`（我的操作錯誤造成，非產品缺陷）；實際由哪一條陳述刪掉表**尚未查明**——已排除的是 `tests/test_c1_c2_concurrency_isolated.py:27`、`test_c4_rollback_isolated.py:267`、`test_inference_quota_commit.py:24` 這三處 `os.remove`，它們刪的是各自 `tempfile`／`temp_dir` 檔，不碰共用檔。規則：**每次只跑一個 pytest 進程**，必須與平行 session 並存時用獨立檔 `ALETHRA_TEST_DB="sqlite:////tmp/<run>.db"`；凡本表標「隔離 DB」者以此法量測。

---

## 能力／入口／狀態總覽

| 能力 | 實際入口 | 主要實作位置 | 當前狀態 | 必要驗證 | 阻塞依賴 |
|------|----------|--------------|----------|----------|----------|
| **A. 技能數量與建立交易** | | | | | |
| Portfolio 建立限額 | `POST /api/v1/paper-trading/create` | `app/api/paper_trading.py` `app/api/paper_portfolio.py` | 已實作未驗 | T02: PG 併發測試 ≤10 上限 | 需 PG 環境 |
| 交易原子性 | engine session vs caller session | `app/engine/*.py` | 部分接線 | T03: rollback 無殘留 | 需審視 engine session 管理 |
| **B. 策略版本、dataset 與 engine** | | | | | |
| Paper trading 建立 | `POST /api/v1/paper-trading/create` | `app/api/paper_trading.py` | 已實作未驗 | T04: engine 使用已驗證 context | 需確認 strategy_version_id/dataset_id 實際傳遞 |
| Run 查詢讀回 context | `GET /api/v1/paper-trading/status/{run_id}` | `app/api/paper_trading.py` | 已實作未驗 | T04: 讀回相同策略/資料/技能 | 需驗證 response schema |
| **C. 決策入口** | | | | | |
| 人類/規則決策記錄 | `POST /api/v1/paper-portfolios/{id}/decisions` | `app/api/paper_portfolio.py` | 已實作＋SQLite 離線回歸 | T06: 無模型批准亦可記錄 | `test_decision_lineage.py` 26 passed；`test_manual_decision_books_linked_portfolio.py::test_a_hold_is_a_decision_and_not_an_account_event`、`:test_a_human_decision_with_no_inference_exports_empty_lineage` |
| linked_experiment_version_id 驗證 | decisions endpoint | `app/api/paper_portfolio.py:943`–`:965`（存在／擁有經 `validate_strategy_version(owner_id=…)`，用途經該 portfolio run 的 strategy_version 一致性） | 已實作＋拒絕分支有節點 | T05: 非法版本被拒絕 | `tests/test_decision_lineage.py:768` 斷言 400 `version_mismatch` |
| **D. 正式 inference 授權** | | | | | |
| predict_with_version | `POST /api/v1/ml/personal/models/{version_id}/predict` | `app/api/personal_models.py:715` | 已實作＋endpoint 回歸 | T07: skill A 通過、B 拒絕 | `test_formal_skill_unit_authorization_endpoint.py::test_two_skills_of_one_owner_switch_independently`、`:test_a_skill_that_never_selected_cannot_infer`（9 passed，隔離 DB） |
| predict_with_record_alias | `POST /api/v1/ml/personal/records/{record_id}/predict` | `app/api/personal_models.py:746` | 已實作＋endpoint 回歸 | T08: alias 不能繞過 | `:test_moving_the_record_alias_authorizes_no_skill` 通過（先前記錄的「409 非 403」失敗以目前碼重測已不存在） |
| skill_unit_id 必填 | PredictRequest schema | `app/api/personal_models.py` | 已實作；前端已同步（#178） | T07/T08: 422 validation | `:test_predict_without_a_skill_unit_is_refused_as_an_invalid_request`；前端 `tsc --noEmit` EXIT=0，browser NOT RUN |
| **E. Selection/activation/撤回** | | | | | |
| 建立選擇 | `POST /api/v1/ml/personal/models/{version_id}/select` | `app/api/personal_models.py` | 已實作未驗 | T10: selection 換版本不轉移批准 | 需驗證 approval scope |
| 建立批准 | `POST /api/v1/ml/personal/models/{version_id}/activate` | `app/api/personal_models.py` | 已實作未驗 | T09: 跨 owner/錯誤 artifact 拒絕 | 需驗證 skill_unit_id scope |
| 撤回 | `POST /api/v1/ml/personal/models/{version_id}/activate/revoke` | `app/api/personal_models.py` | 已實作未驗 | T10: 撤回 A 不誤傷 B | 需驗證精確撤回 |
| 查詢狀態 | `GET /api/v1/training/jobs/{job_id}/activation` | `app/api/agent_strategy.py` | 已實作未驗 | T09: 不合格同意狀態被拒絕 | 需驗證 consent check |
| **F. 訓練與評估** | | | | | |
| 真實訓練鏈 | snapshot → training → artifact → inference | `app/ml/personal_trainer.py` `app/workers/training_worker.py` | **部分**：推論段已驗（完成態 fixture 的 artifact 經真實 loader＋真實 `features_as_of`＋真實 gate 通過正式端點）；訓練 worker 段未驗 | T15: 真實快照→訓練→artifact→推論 | `tests/test_successful_inference.py` 9 passed（隔離 DB，2026-11-17）；worker 產出 artifact 仍 NOT RUN |
| 樣本外評估 | model comparison | `app/services/model_comparison.py` | 已實作未驗 | T15: 未參與訓練資料的評估 | 需確認評估資料隔離 |
| **G. 任務、帳本與恢復** | | | | | |
| Durable admission | research_runs | `app/api/runs.py` `app/services/research_runs.py` | 已實作 | T11/T12: 重送不重複執行 | 已有測試 |
| Idempotency | POST /versions/{id}/runs | `app/api/runs.py` | 已實作 | T11: 同 key 重送不重複計量 | 已有測試 |
| 取消競態 | atomic conditional UPDATE | `app/api/runs.py` | 已實作 | T13: 取消後行為符合政策 | 已有測試 |
| **H. 每日服務與 UI** | | | | | |
| 每日研究服務（cron＋weekly 觸發） | `run_research_cycle(scope=…, job_type=…, scheduled_for=…)` → `_tick_targets` → `_run_tick` → `_research_for_owner`；操作入口 `GET /api/v1/learning/jobs`、`POST /api/v1/learning/jobs/trigger` | `app/workers/learning_scheduler.py:337` `:184` `:247` `:392`；`app/api/learning.py:112` `:136` | 已實作＋SQLite 離線回歸（T17） | T17: 頻率/時區/同日去重/資料新鮮度/終態/可觀察/可停止 | 本機未啟動背景排程程序；PG 與 browser 未驗 |
| 區間研究服務（auto_collection） | `run_collection_cycle()`／`trigger_manual_collection()` 委派同一 `_run_tick`；操作入口 `GET /api/v1/admin/collection/status`、`POST /api/v1/admin/collection/trigger`、`POST /api/v1/admin/collection/start`、`POST /api/v1/admin/collection/stop`、`GET /api/v1/admin/collection/history`（全部 `require_admin`） | `app/services/auto_collection.py`；`app/api/admin.py:89` `:97` `:116` `:127` `:156` | 已實作＋SQLite 離線回歸（T17）；`tests/test_auto_collection.py` 23 passed 隔離 | T17: 去寫死帳號、接 kill switch、帳本真實終態、history 讀實際記錄 | 同上；`AUTO_COLLECTION_ENABLED=false` 時 POST 不能啟動（409） |
| 每日模擬交易 daemon | `GET/POST /api/v1/paper-trading/live/{status,start,stop}` | `app/api/paper_trading.py:521` `:548` `:633` | 未改造本輪：仍是行程內 `_live_daemon` 全域，未接同一 tick／kill switch／帳本 | T17: 待驗證 | 未實作範圍，非外部阻塞 |
| **每日回放（模擬）服務** | `run_scheduled_simulations(scope, user_id, job_id)` → 同一 `_run_tick`／`_tick_targets`（03:00 UTC）；`POST /api/v1/learning/jobs/trigger` `job_type=simulation_daily`；owner 讀取面 `GET /api/v1/simulations/{id}/daily-service` | `app/workers/learning_scheduler.py:818` `:842`；`app/api/simulations.py:1694`；`app/api/schemas.py:556` `:576` | 已實作＋SQLite 離線回歸（37＋137 條，5 變體全數被抓到） | T17: 頻率／時區／去重／新鮮度／上限／終態／可停止／system 歸因／與按鈕同結果 | 本機未啟動背景程序；PG、browser 未驗（見本輪節） |
| 回放服務 UI | 面板掛在 `experiments/[id]/simulation` 狀態卡下方 | `frontend/src/lib/dailyService.ts`；`frontend/src/components/experiments/DailyServicePanel.tsx`；`src/lib/api.ts:425` | 已實作；unit 10／152 pass、`tsc --noEmit` 0、`next build` 0 | T16: browser 點擊驗證九態＋Pause／Resume 即停即啟 | browser NOT RUN（需精確批准） |
| 區間服務 UI 面板 | `AutoCollectionStatus.tsx` 面板＋`JournalDashboard.tsx` 分頁文案 | `frontend/src/components/journal/AutoCollectionStatus.tsx`；`frontend/src/components/journal/JournalDashboard.tsx` | 已改，`tsc --noEmit` EXIT=0（僅型別層） | T16: 需 browser 點擊驗證載入/空/ blocked/failed/completed | browser 驗證 NOT RUN |
| UI 核心流程 | frontend | `frontend/src/**` | NOT RUN | T16: UI 能完成核心操作 | 需 browser 驗證 |

---

## 驗收案例狀態（T01-T18）

| 案例 | 描述 | 狀態 | 證據位置 |
|------|------|------|----------|
| T01 | 普通用戶建立技能，另一用戶不可讀寫 | NOT RUN | 需端到端測試 |
| T02 | 接近 10 單位上限時競爭不超額 | NOT RUN | 需 PG 併發測試 |
| T03 | 呼叫者 rollback 後無新 portfolio | NOT RUN | 需 transaction 測試 |
| T04 | engine 使用並保存相同 context | NOT RUN | 需 integration 測試 |
| T05 | 跨 owner/非法跨 skill 被拒絕 | **部分通過（SQLite 離線）** | `test_formal_skill_unit_authorization_endpoint.py::test_a_foreign_skill_account_is_refused_and_leaves_no_row`；decisions 端 `app/api/paper_portfolio.py:943`＋`test_decision_lineage.py:768` |
| T06 | 一般決策可記錄不因無模型阻塞 | **部分通過（SQLite 離線）** | `test_decision_lineage.py` 26 passed；`test_manual_decision_books_linked_portfolio.py::test_a_hold_is_a_decision_and_not_an_account_event`、`:test_a_human_decision_with_no_inference_exports_empty_lineage` |
| T07 | 同 owner A/B 選同 version，只有 A 有批准：A 成功 B 拒絕 | **通過（SQLite 離線，隔離 DB）** | `test_formal_skill_unit_authorization_endpoint.py` 9 passed／0 failed（2026-11-17 重測）。關鍵節點 `::test_two_skills_of_one_owner_switch_independently`：B 借用 A 的新 version → 403 `superseded_skill_unit_selection`，且 `detail.context.currently_selected_version_id` 指向 B 自己的 selection。舊行「2/4 通過」是 409-vs-403 修正前的量測，已失效 |
| T08 | predict alias 不能繞過 T07 | **通過（SQLite 離線）** | `::test_moving_the_record_alias_authorizes_no_skill`、`::test_each_skill_gets_its_own_version_from_the_unnamed_predict_route` 皆通過；舊行「alias 測試失敗（409 非 403）」以目前碼重測不存在 |
| T09 | 跨 owner/錯誤 artifact/撤回拒絕 | NOT RUN | 需完整 authorization 測試 |
| T10 | 撤回 A 不誤傷 B；換 selection 不轉移批准 | **通過（SQLite 離線）** | `::test_rollback_returns_one_skill_and_leaves_the_other_on_its_version`（B 仍 1 列歷史且可預測；對已撤銷 version 預測 → 403；以錯誤 scope 的 expected → 409 `stale_active_version` 且未套用）＋T07 節點的「不轉移批准」斷言 |
| T11 | 同 idempotency key 重送不重複 | **已實作** | `test_research_run_recovery.py` |
| T12 | 終態/quota/audit 不一致注入 | **已實作** | 相關測試存在 |
| T13 | 取消/中斷後行為符合政策 | **已實作** | `test_runs_api.py` 取消測試 |
| T14 | 匯出包含正確 lineage，第二帳號不可取 | NOT RUN | 需 export 隔離測試 |
| T15 | 真實快照→訓練→artifact→推論 | **部分通過（SQLite 離線，隔離 DB）** | `tests/test_successful_inference.py` 9 passed（正式 predict 端點過真實 gate、真實 `features_as_of` 選取、真實 loader 載真實 artifact，且 `inference_id` 可被 decision lineage 使用；跨 skill／未完成 training job／checksum 漂移／他人 job 皆有拒絕節點）。**未證**：artifact 由 training worker 實際產出（此處 training job 為完成態 fixture） |
| T16 | UI 能完成核心操作 | NOT RUN | 需 browser 驗證（本輪新增表面：`DailyServicePanel` 九態與 `data-service-state`；unit＋typecheck＋build 已過，點擊未做） |
| T17 | 每日服務可觀察/可去重/可停止 | **部分通過（SQLite 離線回归；非 PG、非背景程序、非 browser）** | 見下方「T17 分支證據」 |
| T18 | 正常停止只處理本輪資源 | NOT RUN | 需 stop 行為測試 |

---

## T17 分支證據（2026-11-17）

证据等级：以下全部是**本地 SQLite 离线回归**（`DATA_PROVIDER=demo`）。沒有真實背景排程進程、沒有 PG、沒有 browser 點擊。因此「可持續運作」在本機**未證明**，只有「被呼叫時行為正確」已證明。

| 子條件 | 實際行為 | 節點 | 等級 |
|--------|----------|------|------|
| 頻率 | `AUTO_COLLECTION_INTERVAL_HOURS` < 1 在啟動時拒絕；視窗鍵由該間隔推得 | `tests/test_config.py::TestBackgroundServiceControls`、`test_auto_collection.py::test_collection_window_buckets_onto_the_service_clock`、`:test_collection_window_refuses_an_interval_that_would_never_dedup` | 離線回歸通過 |
| 時區 | `SERVICE_TIMEZONE = "UTC"`；未釘選時 cron 解析為 Asia/Hong_Kong（實測） | `test_learning_scheduler.py::test_daily_and_weekly_triggers_run_on_utc_not_the_host_clock`、`test_auto_collection.py::test_the_interval_trigger_is_pinned_to_utc` | 離線回歸通過 |
| 觸發（多 worker 對齊） | 間隔整除 24 時改用 `CronTrigger(hour="*/N")`，使「觸發時刻」落在「去重視窗」起點；不整除時保留 `IntervalTrigger` 並在 docstring 說明殘餘風險由新鮮度檢查兜底 | `test_auto_collection.py::test_the_service_fires_at_the_start_of_the_window_it_dedups_on`、`:test_an_interval_that_does_not_divide_the_day_keeps_its_own_wait` | 離線回歸通過 |
| 同日／同窗去重 | 第二次 tick 讀同一帳本視窗即跳過，且區間服務與每日 cron 使用不同 `job_type` 不互相抑制 | `test_auto_collection.py::test_two_ticks_inside_one_window_study_once`、`:test_two_ticks_on_the_clock_dedup_through_the_window_they_share`、`:test_the_next_window_is_work_again`、`:test_the_interval_service_dedups_apart_from_the_daily_cron`；`test_learning_scheduler.py::test_second_scheduled_tick_on_the_same_day_does_the_work_once` | 離線回歸通過 |
| 手動觸發不受視窗抑制 | 人工請求進 `manual` scope，仍過新鮮度檢查 | `test_auto_collection.py::test_a_manual_trigger_is_not_suppressed_by_the_window`、`:test_trigger_endpoint_returns_the_tick_outcome`、`:test_trigger_endpoint_names_the_reason_nothing_ran`；`test_learning_scheduler.py::test_a_manual_trigger_is_not_suppressed_by_the_daily_dedup` | 離線回歸通過 |
| 資料新鮮度 | venue 不可達但儲存的 bars 相同 → `no_new_bars` 跳過；bars 較舊 → 研究並標記 | `test_learning_scheduler.py::test_unreachable_venue_with_identical_stored_bars_is_skipped_not_restudied`、`:test_unreachable_venue_with_older_stored_bars_studies_and_labels_them` | 離線回歸通過 |
| 歸因／資源上限 | 只服務持有 live skill account 的 owner；退役帳號不服務；owner 數受 `LEARNING_SCHEDULER_MAX_OWNERS` 限制 | `test_auto_collection.py::test_a_cycle_studies_for_every_served_account`、`:test_the_cycle_is_attributed_to_the_owner_of_a_live_skill_account`；`test_learning_scheduler.py::test_tick_serves_the_owner_with_the_skill_account_not_the_first_row`、`:test_retired_accounts_are_not_served`、`:test_tick_is_bounded_by_the_owner_limit` | 離線回歸通過 |
| kill switch | 開槍時 tick 在花費任何 quota 前拒絕，且不寫帳本列 | `test_auto_collection.py::test_an_active_kill_switch_refuses_the_cycle_before_any_row`；`test_learning_scheduler.py::test_active_global_kill_switch_stops_the_tick_before_any_work` | 離線回歸通過 |
| 真實終態 | 全失敗 → `failed`、部分 → `partial`、乾淨 → `completed`；失敗原因留在 `result_json` | `test_auto_collection.py::test_a_failing_cycle_is_recorded_as_failed`；`test_learning_scheduler.py::test_every_combo_failing_records_failed_not_completed`、`:test_some_combos_failing_records_partial`、`:test_a_clean_tick_reports_completed_and_carries_no_error_text` | 離線回歸通過 |
| 可觀察 | `GET /api/v1/admin/collection/history` 讀實際 LearningJob 列（含跳過／失敗）；status 分離 `enabled`（部署）與 `running`（本進程） | `test_auto_collection.py::test_history_returns_the_ticks_the_service_recorded`、`:test_history_honours_the_limit_and_works_on_sqlite`、`:test_history_endpoint_serves_the_recorded_ticks`、`:test_status_separates_the_deployment_opt_in_from_this_process`、`:test_status_endpoint_reports_the_deployment_and_this_process` | 離線回歸通過 |
| 可停止／可恢復（本進程） | `/collection/start` 在 `AUTO_COLLECTION_ENABLED=false` 時回 409；`/collection/stop` 停本 worker 的鐘，未運行時仍回 200 且 `running=false` | `test_auto_collection.py::test_start_endpoint_refuses_a_deployment_that_did_not_opt_in`、`:test_init_scheduler_refuses_when_the_deployment_declined`、`:test_stop_endpoint_acts_on_this_worker_only`；`test_learning_scheduler.py::test_scheduler_starts_and_stops` | 離線回歸通過 |
| 每日**模擬交易**服務的頻率／去重／停止 | 未改造：仍是 `_live_daemon` 行程全域，未接同一 tick、未讀 kill switch、不寫帳本 | — | NOT RUN（本輪範圍缺口，非外部阻塞） |

量測（每次只允許一個 pytest 進程；並行會共用 `./test.db` 並互相 drop/create，先前那次 `no such table: users` 是我自己的併發錯誤，非產品缺陷）：

```
# tests/test_auto_collection.py 隔離
DATA_PROVIDER=demo ~/.local/bin/uv run python -m pytest tests/test_auto_collection.py -q -p no:warnings
→ 24 passed in 44.81s   EXIT=0   (/tmp/d2_alone2.txt)

# 受影響 10 檔回歸 lane（順序執行；此量測在新增 stop 節點之前）
DATA_PROVIDER=demo ~/.local/bin/uv run python -m pytest tests/test_admin_security.py \
  tests/test_execution_containment.py tests/test_learning_api.py tests/test_learning_scheduler.py \
  tests/test_personal_strategy_learning.py tests/test_process_state_isolation.py \
  tests/test_startup_schema_safety.py tests/test_auto_collection.py tests/test_config.py \
  tests/test_skill_unit_ceiling.py -q -p no:warnings -rs
→ 219 passed, 1 skipped in 206.54s   EXIT=0   (/tmp/d2_reg3.txt)
  SKIPPED [1] tests/test_skill_unit_ceiling.py:235 — 同格最後一席的併發建立需真實 MVCC／PostgreSQL

# 前端
./node_modules/.bin/tsc --noEmit → EXIT=0，0 errors   (/tmp/d2_tsc2.txt)
```

尚未做（需精確批准或屬後續工作）：PG 上重跑本 lane、真實 uvicorn＋背景排程進程的持續運作觀察、browser 驗證 `AutoCollectionStatus.tsx` 的載入／空／blocked／failed／completed 五態。

---

## 平行摘要取代與更正（DECISION_LINEAGE_DELIVERY.md / DECISION_LINEAGE_REVIEW.md）

這兩份未追蹤文件（實測 `?? backend/DECISION_LINEAGE_DELIVERY.md`、`?? backend/DECISION_LINEAGE_REVIEW.md`）與目前樹狀內容已不一致，且含 owner 已否決的推論。為維持單一狀態來源，以下以**實測**取代其陳述；兩檔已在上加注「已被本表取代」標記但**未刪除**（未追蹤檔刪除不可逆）。

### 現況（2026-11-17 實測）

| 事項 | 目前位置／行為 | 量測 |
|------|----------------|------|
| 一條 inference-lineage 契約供兩個決策入口 | `app/services/decision_lineage.py:19` `validate_linked_inference()`；呼叫者 `app/api/paper_portfolio.py:969`、`app/api/simulations.py:1822` | 兩處均由同一函式把關（`grep` 實測，非推論） |
| 缺失／NULL 技能元資料 | `app/services/decision_lineage.py:67`–`:80`：`skill_unit_id` 非字串或不等於該 portfolio → 400 `inference_skill_mismatch`。**不得當作「所有技能」** | 原始碼當前行號實測 |
| lineage 測試 | `tests/test_decision_lineage.py` | **26 passed**／0 failed，EXIT=0（隔離 DB `/tmp/d2_iso_a.db`，`/tmp/d2_iso_b.txt`） |
| inference 授權 endpoint | `tests/test_formal_skill_unit_authorization_endpoint.py` | **9 passed**，EXIT=0（隔離 DB） |
| 成功推論（正式端點） | `tests/test_successful_inference.py` | **9 passed**，EXIT=0（隔離 DB `/tmp/d2_iso_c.db`，`/tmp/d2_inf.txt`）；用真實 gate、真實 `features_as_of` 選取規則、真實 loader 載入真實 artifact，並把 `inference_id` 接給 decision lineage；拒絶分支（跨 skill／training job 未完成／checksum 漂移／他人 training job）皆有節點 |
| 模型家族分離 | `tests/test_successful_inference.py::test_personal_authorization_does_not_require_agent_activation` | 通過 — personal model 不再要求 `AgentStrategyTrainingJob`／consent／activation 五件套 |

### 撤回（文件仍在，但其陳述不再成立）

1. **`DECISION_LINEAGE_REVIEW.md` §二「來源可追溯性由 absence of deletion 保護／UsageRecord 是 append-only，一旦寫入即永久存在」不成立。** 「找不到刪除路徑」只是未找到，不構成強制不可刪；`usage_records.user_id → users` 的 FK 方向也不反向阻止 usage row 被刪除。實測可陳述的是：目前 `grep` 在 `app/` 下未發現 `session.delete(usage_record)`／`DELETE FROM usage_records`／retention 清理路徑。此為**未發現**，非**已保證**。
2. **`DECISION_LINEAGE_DELIVERY.md` §5.1「成功推論受阻於 Authorization infrastructure complexity」是錯誤歸因，不是外部阻塞。** 根因是 personal model 曾被錯接到 agent-strategy 授權鏈（實際架構接線缺陷）。該文件 §5.1 列出的五個必要條件（`AgentStrategyTrainingJob`＋`AgentStrategyConsent`＋`AgentModelActivationApproval`…）對 personal 推論**不適用**；現由上方 `test_personal_authorization_does_not_require_agent_activation` 反證。
3. **計數失效**：兩文件的「Expected: 8 passed」（lineage，現 26）、「Expected: 6 passed」（authorization，現 9）不可再用。
4. **代碼片段過期**：`DECISION_LINEAGE_REVIEW.md` §五.3 與 `DELIVERY.md` §1.3 引用 `app/api/paper_portfolio.py:986-1020`／`:1012-1020` 的內联檢查；該內联程式已不存在（實測 `grep inference_skill_unit_id app/api/paper_portfolio.py` 零命中），邏輯已移到 `app/services/decision_lineage.py`。REVIEW 中 `if inference_skill_unit_id is not None and …` 這種**允許 NULL 通過**的寫法並非現行行為。
5. **「`tests/test_successful_inference.py`（incomplete）」過期**：該檔現 22,827 bytes、9 個節點全過。
6. 兩文件的 migration 標識經核對仍與樹狀一致（`migrations/versions/b2c3d4e5f6g7_add_skill_unit_associations.py:55-56`、`c3d4e5f6g7h8_add_decision_inference_link.py:36-37`），但它們聲明的「PG 已就緒」意味不准：**PG 執行仍未做**。唯一 head／ancestry 已改用 Alembic parser 實測（見下方「migration head 閘門」節），不再以 grep／檔案存在檢查代替；此變更不等於 `upgrade` 已執行。

### 仍未證明（不因上述通過而升級）

- `test_successful_inference.py` 的 training job 是**完成狀態的測試 fixture**，未走 training worker；不得據此聲稱真實訓練產出該 artifact。
- 上述全部為 SQLite 離線層級；PG、真實背景服務持續運作、browser 點擊旅程皆 NOT RUN。

---

## 立即工作優先序

（本節原列 1–5 已完成或已被本輪重測取代：T07/T08 endpoint 測試、D 節 inference 授權、C 節 decisions 驗證見上方修正後行。）

1. ~~**每日模擬交易服務接線**~~ **本輪已完成（SQLite 離線層級）**：回放（`SimulationSession`）现已接到同一 tick 核心、UTC 時鐘、execution kill switch、同日去重與 LearningJob 帳本，並有 owner 可見的讀取端點＋UI 面板 — 詳見下方「每日回放服務接線與 owner 可見面（2026-11-17 本輪）」。**仍未完成**：`app/api/paper_trading.py:548/633` 的 `/live/*` `_live_daemon`（另一條鏈，未接同一 tick／帳本，且帶下方 H 節所列缺陷）；背景程序真實持續運作觀察（優先序 3）
2. **PG 上重跑 T17 lane** — 需精確批准（PG 服務啟動／建 role／grant）
3. **背景排程真實持續運作觀察** — 需精確批准（uvicorn／daemon 啟停）。在此之前，本表不得聲稱服務會持續運作
4. **T16 browser 驗證** — `AutoCollectionStatus.tsx` 五態 ＋ 技能帳號旅程（需精確批准：啟動 dev server／browser）
5. **migration 行為驗證（七條）** — 檔可寫；執行 `upgrade` 需精確批准。~~＋ Alembic parser 唯一 head／ancestry~~ 的 **head 唯一性**本輪已以 parser 證得並修好過期釘選（見下方「migration head 閘門」節）；merge point 的**語意**仍未審，七條行為驗證仍未做
6. **training worker 真實產出 artifact → 正式推論** 端到端（T15 剩餘半邊）。目前只有完成態 fixture 的 artifact 被證可推論；屬既有依賴內工作，不需批准
7. **paper create→engine→run→query 可區分版本／資料差異證明**（T04）
8. **T18 正常停止只處理本輪資源** — 尚未有節點
9. ~~整併 DECISION_LINEAGE_* 平行摘要~~ **本輪已完成**：折入上方「平行摘要取代與更正」節，兩檔頂端加注取代說明；未刪除（未追蹤檔刪除不可逆）
10. **提交／推送** — 需 Git 寫入確認（目前 184 條未提交路徑）

---

## migration head 閘門：本輪發現並修正的真實缺陷（2026-11-17）

**發現**：`scripts/check_migration_tail.py` 的 `EXPECTED_TAIL` 仍是 2026-11-16 的四支（`c9d0e1f2a3b4, d0e1f2a3b4c5, e1f2a3b4c5d6, f2a3b4c5d6e7`），而樹已結在 `c3d4e5f6g7h8`。即：11-17 寫入 `b2c3d4e5f6g7`／`c3d4e5f6g7h8` 兩支 lineage module 時，**沒有在同一次改動裡延長這行釘選**——正是該檔註解所稱「被 derive 值掩蓋掉的 review signal」，此次以具體後果呈現。

**後果（修正前實測）**：
- `check_migration_tail.py --with-alembic` → EXIT=3、`TAIL_MISMATCH`
- `bash start_e2e_research_flow.sh --show-head` → stderr `E2E SETUP ABORTED: no expected alembic head (see the error above); refusing to start or resume a stack whose schema it cannot name`（修正前由 pytest 捕獲的實際輸出；該檔 `fail()` 在 `:74` 固定 `exit 1`，此 1 是讀碼得知的失敗路徑定義，非該次的獨立實測 exit code）。即 E2E 啟動入口在這次修正前**拒絕啟動整個堆疊**——`derive_alembic_head` 在 `:87` 就 fail，位置早於 `:90` 的 `--show-head` 提前返回，而 `:391`／`:471` 兩處升級後核對也用同一個 `EXPECTED_ALEMBIC_HEAD`。T16／背景持續運作等需啟動的項目都卡在這個閘門上，且原因不是環境而是未完成的改動。
- `tests/test_migration_tail_contract.py` → 4 failed／6 passed

**根因不是解析器**：第一個假設「`ast` 解析忽略了带標註的賦值」經讀碼後被推翻（`scripts/check_migration_tail.py:80-81` 同時處理 `ast.Assign` 與 `ast.AnnAssign`），故未對解析器做任何修改。修的是釘選本身，以及測試檔中與舊釘選綁死的三個字面值（`_CHAIN`、第二 head 斷言、掉尾後兩個孤兒 head 的斷言名）。這些值刻意寫成明文字串而非 `checker.EXPECTED_TAIL[...]`，否則期望與量測會再次一起移動。

**Alembic parser 實測（不是 grep）**：`ScriptDirectory.from_config(...)` → 56 支 revision、`get_heads() == ['c3d4e5f6g7h8']`（唯一 head）、merge point 1 支（`a1b2c3d4e5f7`，其 `down_revision` 為 3 元素 tuple）；`walk_revisions()` 前四支 = `c3d4e5f6g7h8 → b2c3d4e5f6g7 → f2a3b4c5d6e7 → e1f2a3b4c5d6`，與新釘選同序；`e1f2a3b4c5d6` 的 parent 是 `d0e1f2a3b4c5`（在釘選之外，故 `_CHAIN` 以合成 root `a1b2c3d4e5f6` 取代真實祖先）。獨立 `ast` DAG 與 alembic 兩套解析在同一棵樹上给出同一 head 與同一計數（`test_the_two_resolvers_name_the_same_head_and_count`）。

```
# 修正後量測（同一環境、單一字 pytest 行程；先 pgrep -fl "[p]ytest" → rc=1，無並行競爭）
DATA_PROVIDER=demo ALETHRA_TEST_DB=sqlite:////tmp/tailpin_20261117.db \
  ~/.local/bin/uv run python -m pytest tests/test_migration_tail_contract.py -q -p no:warnings
→ 10 passed in 3.10s / EXIT=0

~/.local/bin/uv run python scripts/check_migration_tail.py --with-alembic
→ c3d4e5f6g7h8 / EXIT=0

bash start_e2e_research_flow.sh --show-head
→ EXPECTED_ALEMBIC_HEAD=c3d4e5f6g7h8 / EXIT=0
```

**仍未證明（不因閘門變綠而升級）**：
- 這三支持 `upgrade` 對 `b2c3d4e5f6g7`／`c3d4e5f6g7h8` **一次都沒跑過**；PG 執行需精確批准。名延長 ≠ migration 已執行。
- 兩支 module 的**內容**仍待 owner 審查批准。
- 「七條行為驗證」（正常前提可進行／缺表被拒／目標欄已存在被拒／index-constraint 衝突被拒／partial state 不自動修復／拒絕前未寫入任何東西）仍需對真實 DB 執行，尚未有節點。
- head 唯一性由 parser 證得；**ancestry 的語意**（`a1b2c3d4e5f7` 的 3-way merge 是否為預期合併、其 tuple 中 `add_state_version` 這一名稱是否為合法 revision id）未審。

---

## 每日回放服務接線與 owner 可見面（2026-11-17 本輪）

**做了什麼（四個位置，全部走既有依賴，未新增套件）**

| 位置 | 內容 |
|------|------|
| `backend/app/workers/learning_scheduler.py:818` `:842` `:75` `:89` `:95` | 第三支 tick `simulation_daily`（`daily_simulation`，03:00 `SERVICE_TIMEZONE`=UTC）沿用同一 `_run_tick`／`_tick_targets` 核心：execution kill switch fail-closed、同日去重讀 `config_json["scheduled_for"]`、每帳號上限 `SIMULATION_SESSIONS_PER_OWNER=20`（按 `created_at,id`  oldest-first）、`SIMULATION_SERVED_STATUSES=(pending, active)`（NULL 不服務）。每帳號先寫 `running` 行再推進，終態 failed／partial／completed；步進呼叫 `advance_daily_step(..., actor_type="system")`——與 owner 按鈕同一支函式，不是第二套回放實作 |
| `backend/app/api/simulations.py:1694` | 新增 `GET /api/v1/simulations/{session_id}/daily-service`：全部欄位從「決定這行為的兩個地方」推得——session 自己的列＋`simulation_daily` 帳本列（掃描 30 支 `DAILY_SERVICE_LEDGER_SCAN`，在 Python 內比對，不用 JSON 運算子，SQLite／PG 同一份碼）。回傳節奏／時區／服務狀態集合／上限與本列排隊位置／拒絕原因（paused・completed・retired・intraday・status_unreadable）／execution switch 三態（active・inactive・unreadable）／`runs_in_this_process`（只描述回答這個請求的行程）／`in_flight`／`last_run`（outcome・step_number・snapshot_date・detail） |
| `backend/app/api/schemas.py:556` `:576` | `DailyServiceRun`／`DailyServiceStatus` 兩個回應模型 |
| `frontend/src/lib/dailyService.ts`（223 行）・`frontend/src/components/experiments/DailyServicePanel.tsx`（186 行）・`frontend/src/lib/api.ts:425`・`frontend/src/app/experiments/[id]/simulation/page.tsx:793` | 前端同步（「不能後端已改、前端仍舊」）：派生函式 `serviceState`／`serviceView`／`lastRunLabel`／`runnerNote`／`cadenceLabel` 全部只讀端點回傳值，不寫死小時或上限；面板有 loading／unreachable（含 Retry）／九種服務狀態，每態在 DOM 上以 `data-service-state` 可斷言；停止／啟動直接指向既有 Pause／Resume（同一個狀態欄，不是另造開關） |

**量測（同一環境、每個 run dir 獨立、真實 exit code；`DATA_PROVIDER=demo`）**

```
# 後端本輪新增 13 條路由測試（同檔共 37 條：24 條 tick＋13 條讀取面）
backend $ DATA_PROVIDER=demo ALETHRA_TEST_DB=sqlite:////tmp/dsvc_run2.db \
  ~/.local/bin/uv run python -m pytest tests/test_simulation_daily_tick.py -q -p no:warnings
→ 37 passed / PYTEST_EXIT=0

# 受影響八檔 lane（前輪 124 passed＋本輪 13 = 137，可加）
backend $ DATA_PROVIDER=demo ALETHRA_TEST_DB=sqlite:////tmp/dsvc_lane3.db \
  ~/.local/bin/uv run python -m pytest tests/test_simulation_daily_tick.py \
  tests/test_learning_scheduler.py tests/test_learning_api.py tests/test_auto_collection.py \
  tests/test_daily_replay_reads_stored_bars.py tests/test_rule_triggered_paper_execution.py \
  tests/test_intraday_simulation_api.py tests/test_simulation.py -q -p no:warnings
→ 137 passed, 15 skipped / PYTEST_EXIT=0
  （15 條跳過全部是 tests/test_intraday_simulation_api.py:44 的 module-level skipif，
    跳過的就是該檔那 15 條，非新增迴避）

backend $ ... ALETHRA_TEST_DB=sqlite:////tmp/dsvc_contract.db \
  ~/.local/bin/uv run python -m pytest tests/test_alethra_client_contract.py -q
→ 9 passed / PYTEST_EXIT=0（新路由未破壞路由計數契約）

frontend $ node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyService.test.mjs
→ # pass 10 # fail 0 / NODE_EXIT=0
frontend $ node22 ... --test tests/*.test.mjs
→ # tests 152 # pass 152 # fail 0 / NODE_EXIT=0（未回歸）
frontend $ npm run typecheck → tsc --noEmit / TSC_EXIT=0
frontend $ npm run build → BUILD_EXIT=0（含掛載面板的那頁）
```

`tests/dailyService.test.mjs` 的欄位清單**不是手抄**：`pydanticFields()` 直接讀 `backend/app/api/schemas.py` 的類別本體取註解欄位，與 `dailyService.ts` 的 interface 鍵比對；伺服器端改名會在前端 lane 失敗，而不是兩邊各自漂移。

**five mutations（證明這 13 條新釘選真的會紅；每支單獨套用→跑該檔→還原）**

| 變體 | 失敗節點 |
|------|----------|
| 節奏寫死成研究那支的小時（`cadence_hour_utc=2`） | `test_an_untouched_replay_is_offered_the_cadence_the_worker_uses` |
| paused 也算被服務 | `test_pausing_is_the_stop_control_and_resuming_is_the_start_one` |
| 最新一列即使未提到本回放也當成 last_run | `test_a_replay_beyond_the_batch_is_deferred_rather_than_refused` |
| kill switch 讀不到時回報 inactive（fail-open） | `test_a_kill_switch_that_cannot_be_read_is_not_reported_as_off` |
| 排隊位置寫死 0 | `test_a_replay_beyond_the_batch_is_deferred_rather_than_refused` |

還原後 `backend/app/api/simulations.py` sha256 = `008b48ccded5371da18a11acc6cff2f3cca89ab7664e9aaa794ca9a7cf50e667`（與變體前相同，逐字元一致）。

**本輪檔案指紋（sha256）**

```
008b48cc…  backend/app/api/simulations.py
0b4bc4ae…  backend/app/api/schemas.py
252cc77a…  backend/tests/test_simulation_daily_tick.py（1133 行）
bd8f5200…  backend/app/workers/learning_scheduler.py（本輪未改，與前輪同一指紋）
340be556…  frontend/src/lib/dailyService.ts
f4dfaa30…  frontend/src/components/experiments/DailyServicePanel.tsx
793fd525…  frontend/src/lib/api.ts
6057315f…  frontend/src/app/experiments/[id]/simulation/page.tsx
e38a2fea…  frontend/tests/dailyService.test.mjs
```

**仍未證明（不因上述綠燈而升級）**

- 上述全部為 **SQLite 離線層級**。背景排程真實持續運作（03:00 UTC 實際自己觸發一次）仍 NOT RUN，需精確批准啟動 uvicorn／daemon；在本表看到該觀察之前，不得聲稱服務會持續運作。
- **browser 點擊旅程 NOT RUN**（需精確批准：dev server／browser）。本輪前端證據只有 unit（10／152）、`tsc --noEmit`、`next build` 三層，代理不得代替 owner 驗收。
- `_advance_for_owner` 的 `version is None` 分支（`learning_scheduler.py:915-917`）**刻意未測**：`SimulationSession.experiment_version_id` 是 NOT NULL＋FK，該狀態在正式寫入路徑下無法建立；以假造非法列去覆蓋它會變成測 constraint 本身。
- `runs_in_this_process` 只對「回答此請求的行程」負責。多 worker 部署下其他行程是否也在跑，這個端點不知道，面板也不聲稱知道。
- 帳本掃描上限 30 列：超過 30 天前的回放最後一步不會顯示（會顯示「尚未被任何 tick 提到」），這是可見面限制，不是資料遺失。
- 另一條鏈 `/api/v1/paper-trading/live/*`（`_live_daemon`）本輪未改造，其已知缺陷（`app/api/paper_trading.py:575-603` SQL 無 user 過濾、`live_trading_daemon.py:292-296` 以任意 `user_id` 開 portfolio、未讀 `settings.live_trading_enabled`）仍在。

---

## 固定候選身分（2026-11-18 實測）

| 項目 | 實測值 |
|------|--------|
| worktree | `/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra` |
| branch | `live1-impl` |
| base SHA | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（`git rev-parse HEAD`） |
| 未提交 diff | `git status --porcelain` 共 **190 paths**＝132 M ＋ 58 ??（未清理、未 commit） |
| Python | `~/.local/bin/uv run python` → 3.12.16；alembic 1.14.0、fastapi 0.115.0、sqlalchemy 2.0.36 |
| Node | `~/.local/bin/node22` → v22.23.5（預設 node 仍 v18.20.8） |
| 測試 DB | `DATA_PROVIDER=demo`，`ALETHRA_TEST_DB=sqlite:////tmp/<run dir>/lane.db`，每批一個獨立 run dir |

**檔案責任**：本輪（2026-11-18）由本 session 寫入的 9 支檔案＝`backend/app/api/personal_models.py`(M)、`backend/app/api/simulations.py`(M)、`backend/app/api/agent_strategy.py`(M)、`backend/app/services/model_activation.py`(M)、`backend/scripts/check_migration_tail.py`(??)、`backend/tests/test_agent_strategy_slice.py`(M)、`backend/tests/test_migration_tail_contract.py`(??)、`backend/tests/test_successful_inference.py`(??)、`backend/tests/test_manual_decision_books_linked_portfolio.py`(M)。其餘 **181 paths 本輪未逐支歸屬**；與平行 session `5062987d` 的責任分界在未對帳前不得以 commit 方式固定。本輪未 commit、未 push、未清理工作樹。

## 本輪三個工程單元

**一、正式推論只剩一道門，且不需 HTTP 請求也能走同一門。**
`_run_personal_prediction`（`app/api/personal_models.py:566`）改為收 `via: str` 而非 `Request`（:572），授權檢查搬到門內（:590-593）；兩支路由 `POST /models/{version_id}/predict`（:733，`via=_via(request)` :749）與 `POST /records/{record_id}/predict`（:755，:803）不再各自實作一份。全 app 的 `_require_inference_authorization(` 出鏡次數＝2＝def（:168）＋唯一呼叫點（:590）。動機：排程服務沒有 `Request`，若門留在路由裡，未來的每日 tick 只能偽造身分或繞過門。`tests/test_successful_inference.py` 新增 4 節點（無請求形狀的呼叫回答與 owner 按鈕相同、無法到達未被該技能選取的 version、被取代的 version 指名時拒絕／為現行時准入、服務跑出的 inference 是合法決策來源）。

**二、兩個正式 decision 入口的 lineage 拒绝清單逐字相同（在門口測，不在 helper 測）。**
`tests/test_manual_decision_books_linked_portfolio.py` 新增 5 狀態語料（absent／metadata 是 list／非模型執行／別的技能所生成／准入）同時走 `POST /simulations/{id}/manual-decision` 與 `POST /paper-portfolio/{id}/decisions`，以及拒绝後兩邊都不留紀錄。成功代碼不強行比對（desk 201 建立列，replay 200 回報排队中的 order），比對的是「誰被拒、以哪個 reason 拒」。

**三、Personal vs Agent 模型契約稽核（下面專節），加一支入口層級釘選並更正一段誇大的註解。**
`app/api/agent_strategy.py:316-321` 原寫「that distinction is enforced at inference time」；實測該路徑沒有任何消費點（見下），已改成記載實測狀態與「收窄是政策決定，不是這條 route 的預設」。

## 本輪批次（命令／內容身份／run dir／exit code／跳過原因）

runner 一律為（在 `alethra/backend`）：
`DATA_PROVIDER=demo ALETHRA_TEST_DB="sqlite:////tmp/<run dir>/lane.db" ~/.local/bin/uv run python -m pytest <檔案清單> -q -p no:warnings > <run dir>/out.txt 2>&1; echo PYTEST_EXIT=$?`

| 批 | 內容 | run dir | 結果 | PYTEST_EXIT |
|----|------|---------|------|-------------|
| gate_batch3 | 13 支 predict 相關 lane（清單存於 `/tmp/gate_batch3/lane_manifest.txt`，由 `grep -l "personal-models/\|/predict"` 導出） | `/tmp/gate_batch3` | 235 passed, 3 skipped, 191.97s | 0 |
| family_run2 | `test_agent_strategy_slice.py` ＋ `test_model_activation.py`（含新節點） | `/tmp/family_run2` | 103 passed, 51.58s | 0 |
| family_run3 | 同上 ＋ `test_successful_inference.py`（還原後重測） | `/tmp/family_run3` | 116 passed, 59.91s | 0 |
| migration_tail | `test_migration_tail_contract.py`（＋5 節點） | 前輪記錄 | 15 passed | 0 |
| checker | `scripts/check_migration_tail.py --with-alembic` | `/tmp/gate_batch1` | `c3d4e5f6g7h8`，`module_count=revision_count=56`，`unreachable_from_head=[]` | 0 |

3 個 skip 逐節點（與前輪同一三支，非新缺口）：`tests/test_manual_decision_books_linked_portfolio.py:1988 / :2034 / :2099`，原因均為 `row-lock serialisation requires a real PostgreSQL backend`。不同批次的數字不得相加當「唯一通過總數」。

## 變異測試（每支單獨套用→跑該批→還原）

| 變體 | 意圖 | 偵測 |
|------|------|------|
| M1 門從 runner 移走 | 證明門不是裝飾 | 2 failed |
| M2 `via` 寫死 `"jwt"` | 證明服務形狀的歸因是真的 | 2 failed |
| M3 replay 入口停止驗證 lineage | 證明門口層級測試比 helper 測試多抓到什麼 | parity ＋ no-record FAILED，而只測 shared helper 的 `test_decision_lineage.py` **仍綠** |
| M4 `trainer.py:703` 回報常數 `APPROVED` | 證明推論門回報的是紀錄而不是殼 | 2 failed（新節點＋`test_training_writes_an_artifact_and_inference_reverifies_it`） |
| M5 `live_approval` 不再过滤 `decision=="approved"`（撤回失效） | 證明撤回在門口仍被讀到 | 6 failed（新節點＋5 支既有撤回節點） |

還原後逐位元組一致：`app/api/agent_strategy.py` = `49b7c397990c53beca03ad3c802e647e65a69ef46df564a033b1fbbdc0f50f8e`、`app/api/personal_models.py` = `8dd4f45957f77fc918c780e863ea51530f16d2b07c281281e052df67efaf0f9e`、`app/api/simulations.py` = `008b48ccded5371da18a11acc6cff2f3cca89ab7664e9aaa794ca9a7cf50e667`、`app/ml/agent_strategy_trainer.py` = `30d9c0bcb8561fdb3f2b1b68b1b3f4b61487669e282b44223dbf0dfc41005039`、`app/services/model_activation.py` = `0d880029c5f6d3e5115d8653d37bc7a1f0fee8e349c2683fddb150e53813a6ab`、`scripts/check_migration_tail.py` = `457f3c62bee18bdd35c5c9f766dd1fd5f51374cce2be459e1d64a67fcc54e91d`、`tests/test_agent_strategy_slice.py` = `0ed5a5d67ec4acb53f5406b0c206fa02e356f2c060e5e9d030f1aa83f2aef329`、`tests/test_successful_inference.py` = `80a14e0fb0d218b4f54426ce2642b649fe2395e71eb7cbd34318340dee2723f3`、`tests/test_manual_decision_books_linked_portfolio.py` = `b3006aad21a8663bafa8bbbc8ac121aca11acc9ef33b85e334c1a3acd817e289`、`tests/test_migration_tail_contract.py` = `12561faabf8d6696f272fba69918d95b0307750586dc2c4400a28a37ea501e09`。

## Personal vs Agent 模型契約稽核結論（2026-11-18，全部 file:line 實測）

**Personal 家庭門檻（本輪候選新增，非 base 繼承）**
`git show HEAD:backend/app/api/personal_models.py | grep authoriz` 在 base `5a34e3f` 只找到一行 403，即**該路徑原本沒有任何技能／啟用檢查**。現在的鏈：`_run_personal_prediction` :566 → `check_model_version_inference_authorization`（`app/services/model_activation.py:181`）→ 依序要求 spec 帶 `training_job_id`（:220）、该 id 可解析（:228）、version 列存在（:241）、(owner, record, skill_unit) 的**最新** `PersonalModelSelection`（:252，缺→`no_skill_unit_selection` :266）、該選擇指名**這一個** version（:274，否則 `superseded_skill_unit_selection`）、`TrainingJob` 屬本人（:284→`training_job_not_found` :293）、status completed（:301）、artifact checksum 相符（:311）。全 app 內 personal 鏈不讀 `AgentModelActivationApproval`（`app/api/personal_models.py` grep 0 筆）。

**Agent 家庭沒有被這支修正動到（實測 diff）**
`app/ml/agent_strategy_trainer.py` 與 base 差 **0 行**；`app/api/agent_strategy.py` 只差 `skill_unit_id` 接線（+16）與本輪這段註解更正。計分門（`app/api/agent_strategy.py:403` → `app/ml/agent_strategy_trainer.py:605`）仍要求：screening 啟用、write scope、job 屬本人（:615-621）、completed（:625）、`_authorize`（:223，consent ＋ fixture／owner-trial 任一基礎）、快照 checksum（:633-642）、artifact 複驗（:644-657）、特徵完整／數值／有限（:659-679）。撤回**訓練同意**會真的停止此門（409 `agent_strategy_training_not_authorized`，`tests/test_agent_strategy_slice.py:2520`）。`live_approval`（`model_activation.py:87-96`）只認 `decision=="approved"`（:93），因此**啟用撤回**在同一讀取點立即失效；`revoke_activation_approval`（:570）與 per-skill revoke（:672）都改 `decision`＋`revoked_at`，不刪审计列。

**新增的入口層級釘選**：`tests/test_agent_strategy_slice.py::TestFixtureTrainingChain::test_the_owner_activation_is_what_the_scoring_door_reports` — 同一支計分門在 owner 自行記錄啟用前報 `NOT_APPROVED`／`model_activated False`，啟用後報 `APPROVED`／`True`，撤回後回到 `NOT_APPROVED`＋`no_recorded_approval`，且 `decision`、`positive_probability`、`artifact_checksum` 三者不動（啟用是對既存產物的開關，不是重訓）。

**實測到的政策缺口（不自行收窄，提出差異與建議）**
1. 計分門**不會因為未啟用而拒絕**：`activation` 在 `trainer.py:703` 只是回報欄位。
2. `InferenceRequest`（`app/api/agent_strategy.py:143-146`）只帶 `features`，**沒有 skill_unit_id**，所以 per-skill 啟用範圍在這條門上無從判定；`check_skill_unit_activation`（`model_activation.py:137`）在 `app/` 內的出鏡次數＝1＝它自己的 def，即**零正式呼叫者**。
3. `list_activations_by_skill_unit`（:624）與 `revoke_activations_for_skill_unit`（:672）只有測試呼叫，**沒有 route**：owner 目前無法經正式入口「列出某技能的 agent 啟用」或「按技能撤回」，只能按 job 撤回（`POST /training/jobs/{job_id}/activation/revoke`）。
   建議（待裁決，二擇一）：(A) agent 家族維持「本機試用＋只讀狀態」現況，把上述三個 helper 標明為未接線能力；(B) 讓計分門收 `skill_unit_id` 並在未啟用／範圍不符時 403，同時補 list／revoke-by-skill 兩條 route。本輪採 (A) 的記錄方式（不擅自擴權），未改行為。

## 每日模型推論／決策：仍是原任務的缺口（未重新命名為結案）

三支既有 tick 未變：`scheduled_study`（`app/workers/learning_scheduler.py:73`，02:00 UTC）、`pattern_analysis`（:74，週日 04:00）、`simulation_daily`（:75，03:00）；`auto_collection`（`app/workers/auto_collection.py:43`）。`tests/test_learning_scheduler.py:210-219` 以**精確相等**斷言 `{"daily_research","daily_simulation","weekly_analysis"}`，因此加入第四支是對該契約的故意變更，不是附加。

本輪把缺口要的東西做完了（不需批准的部分）：正式推論現在可以**沒有 HTTP 請求**被呼叫，且歸因寫成 `via="service_tick"`，服務列永不會被讀成 owner 自己按的。**仍然沒有**每日模型推論 tick，因為三個政策問題未決：
1. 技能帳號（`PaperPortfolio`）沒有 opt-in 欄位——沒有任何正式欄位能表達「這隻帳號願意被模型每日推論」；以 `status==active` 代替就是把「啟用帳號」當成「同意模型使用」。
2. 這是**第一個會自動消耗 owner 計量 `ai_usage` 的 tick**（實測：現有 tick 無一支呼叫 `reserve_quota`），可能把 owner 自己按按鈕的額度吃光。
3. 自動 tick 是否受 execution kill switch 與 training kill switch 約束（現在两支都只約束既有 tick 的範圍）。
這三項需要 owner 裁決後才能註冊 tick；在此之前不得宣稱每日模型服務已完成。

## 本輪仍未證明／NOT RUN

- 每日模型推論 tick：**未實作**（政策未決，見上）。
- PG 層級：`3 skipped`（row-lock serialisation）仍 NOT RUN，需 `ALETHRA_PG_DATABASE_URL` 與精確批准。
- 背景排程真實自發觸發、browser 點擊旅程：**NOT RUN**（需精確批准啟動服務／瀏覽器；代理不得代替 owner 驗收）。
- `POST /decisions`（`app/api/human_decisions.py:137`，寫 `HumanDecisionEvent`）仍**沒有 inference lineage 欄位**；補它需要 migration，未批准前不做。
- 來源鏈在本輪完成的僅到「兩個正式 decision 入口一致拒絕 + 服務形狀的 inference 可作為決策來源」；「決策之後的匯出」那一段仍依前輪證據，未與本輪新節點拼成同一候選。

## 必要 Git 保存範圍（待批准，本輪未執行）

若 owner 批准保存，最小範圍＝上述 9 支檔案逐一支 `git add`（其中 `backend/scripts/check_migration_tail.py`、`backend/tests/test_migration_tail_contract.py`、`backend/tests/test_successful_inference.py` 為**未追蹤**檔，不 `git add` 就不會進 commit），加上本記錄檔 `EXECUTION_TABLE_20261117.md` 本身（`git status` 實測亦為 ??；本檔指紋不在本句自我引用，因為這一句改動就會讓它失效——請以 `shasum -a 256 EXECUTION_TABLE_20261117.md` 現測為準）。不採 `git add -A`：目前 190 paths 中 181 支未逐支歸屬，全部加入會把平行 session 的未審內容一併固定。commit／push 仍需事先列出 repo／remote／branch／檔案／訊息並取得明確同意。

---

# 2026-11-18（同日第二輪）：#190 paper create／engine 一致性 ＋ #193 create-lane 帳號的 UUID 拼寫不可見

## 候選身份（本輪現測）

| 項目 | 實測值 |
|------|--------|
| worktree／branch／base | 同前：`.../ac51e592/alethra`、`live1-impl`、`5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` |
| 未提交 diff | `git status --porcelain`＝**192 paths**＝132 M ＋ 60 ??。與前輪 190（132 M ＋ 58 ??）的差＝**兩支新增未追蹤檔**：`backend/app/lib/uuid_spelling.py`、`backend/tests/test_paper_create_engine_consistency.py`。未清理、未 commit。 |
| runner | `cd alethra/backend && DATA_PROVIDER=demo ALETHRA_TEST_DB="sqlite:////tmp/<run dir>/lane.db" ~/.local/bin/uv run python -m pytest <清單> -q -p no:warnings > <run dir>/out.txt 2>&1; echo PYTEST_EXIT=$?` |

本輪受測內容指紋（`shasum -a 256`，全部在 `alethra/backend` 現測；每支都是該批跑完之後再測一次以確認測試期間未被改動）：

| 檔案 | sha256（前 16） |
|------|-----------------|
| `app/services/paper_trading.py` | `f125d37a5b35ce33` |
| `app/api/paper_portfolio.py` | `361f07890fbb7f20` |
| `app/services/skill_units.py` | `d333e37acab77a17` |
| `app/services/personal_selection.py` | `48acf2ec05481ef9` |
| `app/lib/uuid_spelling.py`（未追蹤，新檔） | `8b5263aab91da86f` |
| `tests/test_paper_create_engine_consistency.py`（未追蹤，新檔） | `374ef741879461a2` |
| `tests/test_http_api_success_chain.py` | `c1eb9ef3f1b81912` |
| `tests/test_successful_inference.py`（#188 節點所在，M） | `fea985dd5a4385ba` |

## 工程單元一（#190）：正式 create 請求的欄位確實進入引擎，且被正式查詢讀回

**實測根因（不是推測）**：`POST /api/v1/paper-trading/create` 在離線 lane 上一律回 500 `creation_failed`，原因與請求內容無關——`create_portfolio` 把 `initial_capital`（`Decimal`）未經型別宣告丟進 raw SQL，sqlite3 直接拒綁（`Error binding parameter: type 'decimal.Decimal' is not supported`）。PostgreSQL 端因 `asyncpg` 會做 coerce，從未顯現，所以這條 defect 只在 offline lane 可見。

修復（`app/services/paper_trading.py`）：錢_bind 依各自欄位型別宣告——`bindparam("initial", type_=Float)` ＋ `bindparam("current", type_=Float)`（:136-137，`paper_portfolios`）與 `bindparam("initial", type_=Numeric(15, 2))`（:161，`paper_trading_runs`）。這是把 PG 原本隱式做的事寫明，不改任何金額語意。

同輪修正的另兩處：`_as_text`（:29-31）把 `strategy_version_id`／`dataset_id` 綁成本 lane 自己使用的 dashed 文字（未型別化的 `uuid.UUID` bind 被 sqlite3 以同一理由拒絕），並套用於 INSERT（:170-171）；`owns_session` 交易邊界（:117、:178-184、:197-203）確保被呼叫端傳入 session 時只 `flush` 不 `commit`，rollback／close 也只對 own-session 生效。

驗證由 `tests/test_paper_create_engine_consistency.py`（13 節點）承担，全部走正式 HTTP 入口，不 mock 任何授權／features_as_of／artifact loader／推論路徑：

| 節點（file:line） | 釘住的事 |
|---|---|
| `:175 test_declared_instrument_grain_and_capital_return_through_the_formal_reads` | instrument／grain／capital／exit config 寫入後由正式讀回（P1） |
| `:221 test_a_second_account_never_sees_another_accounts_run_in_its_own_reads` | 讀回以帳號為界，跨帳號不可見 |
| `:253 test_the_named_version_and_its_dataset_are_the_values_the_run_carries` | 呼叫者命名的 lineage 欄位＝產品自家 lineage 查詢實際讀到的值（P2） |
| `:276 test_the_run_binding_is_the_version_a_decision_may_claim` | 決策只能宣稱 run 已綁定的那個 version；不一致時 400 `version_mismatch`（`app/api/paper_portfolio.py:973-1019`） |
| `:330 test_a_dataset_that_contradicts_the_version_is_refused_before_a_row_exists` | dataset 與 version 矛盾時在寫入**之前**拒絕，且拒絕後不留列（P3） |
| `:368 test_the_called_engine_flushes_into_the_callers_transaction_and_never_commits` | 被呼叫的引擎不得提交呼叫者的交易——以**第二條連線**量測（PG-only，見「本輪批次」skip 逐節點） |
| `:426 test_the_http_create_commits_once_and_leaves_a_durable_pair` | HTTP create 自行開交易時提交一次，portfolio＋run 兩列同時耐久 |
| `:463 test_the_formal_reads_still_do_not_carry_lineage_or_exit_config` | 揭露面：`/status`、`/history` 不回報 lineage（不掩蓋） |
| `:510 test_the_process_engine_is_built_from_the_first_url_it_is_given` | `get_engine`（`app/services/paper_trading.py:1678-1683`）是行程單例，後來的 URL 不會重建它 |
| `:546`／`:563`／`:584`／`:631` | 見工程單元二（P6） |

## 工程單元二（#193）：create 路徑寫出的帳號，產品其他部分原本「看不見」

**實測根因**：同一個 UUID 在本 repo 有兩種文字拼寫。SQLAlchemy 2.0 的 `Uuid` 型別對 SQLite 以 **32 字元 hex** 綁參並以此做 identity key；而本產品自己的兩條 raw-SQL 寫入路徑把 **dashed 36 字元**文字存進 `paper_portfolios.id`／`owner_id` 與 `paper_trading_runs.portfolio_id`——`app/services/paper_trading.py:128`（`str(uuid.uuid4())`）、`:141`，以及 `app/services/live_trading_daemon.py:280`、`:292`。在 PostgreSQL 上兩者 parse 成同一個 `uuid`，差異不可見；在 text-UUID 的離線 lane 上它們是**不同的字串**，因此任何以 identity key／hex 比對的查詢都查不到 create-lane 寫入的列。既有的所有 fixture 都用 ORM 路徑（`POST /api/v1/paper-portfolio`）播种，所以這個類別在此前的 596-node 綠燈下完全隐形。

本輪把四處「必須看得见該帳號」的讀點改成同時接受兩種拼寫，規則集中在新檔 `app/lib/uuid_spelling.py`（`dashed_text` ＝只認 dashed；`stored_as` ＝`column == UUID(...)` OR `CAST(column AS String) == dashed`，:33-45），避免複製六份各自漂移：

| 讀點 | 之前 | 之後 |
|---|---|---|
| `app/api/paper_portfolio.py:100-120 _get_portfolio_with_ownership` | 只認 identity key → create-lane 帳號回 404 | identity key 先（PG 走索引），未命中時以 `dashed_text(PaperPortfolio.id, portfolio_id)` 再試一次（:115） |
| `app/api/paper_portfolio.py:570-576 list_portfolios` | `owner_id == current_user.id` → UI 帳號清單遺漏 create-lane 帳號 | `stored_as(PaperPortfolio.owner_id, current_user.id)`（:576） |
| `app/services/skill_units.py:58-72 held_slot_count` | 同一拼寫盲點 → **上限可被超額突破**（看不到的列不佔額度） | `stored_as(PaperPortfolio.owner_id, owner_id)`（:71） |
| `app/services/personal_selection.py:103-131 _owned_skill_unit` | 同一拼寫盲點 → 技能選擇門檻把本人帳號答成 `skill_unit_not_found` | id 與 owner 皆用 `stored_as`（:125-126）；找不到仍以同一 `SelectionRefused(..., not_found=True)` 拒絕，外人 id 與不存在 id 仍不可區分 |

P6 的四個節點即逐一釘住上面四點：`:546` create → `GET /api/v1/paper-portfolio` 清單包含該 `portfolio_id`；`:563` 上限改 1 後第二支 create 回 409 且 `held_skill_units == 1`（證明計數真的看到 dashed 列）；`:584` create-lane 帳號能通過技能門檻（`GET /api/v1/ml/personal/records/{record_id}/selections?skill_unit_id=...` 回 200 並回報 `skill_active_version_id`）；`:631` owner 在該帳號上下manual 訂單——**此節點在本輪被實測為不可在 lane 上證，改為 PostgreSQL-only skip**，見下。

## 工程單元三：實測到的邊界——讀回退修不了寫路徑

`app/api/paper_portfolio.py:122-146 _lock_portfolio` 曾加上同樣的 dashed 回退，隨即實測出：經 dashed 載入的實例**無法被 ORM 寫回**，`db.flush()` 拋 `sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table 'paper_portfolios' expected to update 1 row(s); 0 were matched`（呼叫鏈 `create_order` → `emit_event` → flush）。因此該回退已**主動移除**並在 def 的 docstring 記載理由（:122-146）：把 `Portfolio not found` 換成 500 不是修復。`:631` 節點因此改為 PG-only，skip reason 逐字寫明實測到的 `StaleDataError` 與其條件。

**由此得出的結構結論（不是風格偏好）**：拼寫分裂的正解是把**寫入端**統一（`paper_trading.py:128/:141`、`live_trading_daemon.py:280/:292` 改寫成與 ORM 相同的拼寫，或全 lane 改用同一 UUID 型別），不是繼續增加讀取端回退。本輪只在「不新增寫入行為變更」的範圍內修讀取端＋把邊界寫成測試可判定的形式；統一寫入端涉及既有 PG 資料的既有列，屬於需要 owner 裁決＋migration 的獨立工作包，本輪未做。

## 來源鏈的最後一跳（#188）：把「匯出不含未下单的決策」從散文變成可判定的斷言

**實測結構**：交易 journals 的列**只由 orders 產生**——`app/api/paper_portfolio.py:1447` `_journal_rows(...)` 的 `for order in orders:` 在 :1467，函式在 :1537 `return rows`，中間沒有任何一處遍歷「沒有 order 的 decision」。因此：

- 走 `POST /api/v1/simulations/{session_id}/...`（submit 門）的意圖，其 order 携帶 decision，lineage 會進入兩種匯出格式——已由 `tests/test_manual_decision_books_linked_portfolio.py:1731-1770` 釘住（但那里的 inference 是 seeded 列，不是真實 `/predict` 的產物）。
- 走 `POST /api/v1/paper-portfolio/{portfolio_id}/decisions` 的決策（無 order），**不會**出現在 `journal?format=json` 或 `?format=csv`，即使在 `GET /{portfolio_id}/decisions` 完全可讀。

本輪新增一個節點把這條界線以**真實推論**量測，而不是以程式閱讀宣稱：`tests/test_successful_inference.py:759 test_a_recorded_decision_is_not_a_trade_journal_row`——`POST /api/v1/ml/personal/models/{version_id}/predict` → 200 取回 `inference_id` → `POST /{portfolio_id}/decisions` 携該 id → 201 → `GET /{portfolio_id}/decisions` 讀回同一列且 `linked_inference_id` 不變 → `journal?format=json` 的 `row_count == 0` 且 `records == []` → `journal?format=csv` 只剩表頭行、表頭仍含 `linked_inference_id` 欄位、且不含該 `decision_id`。docstring 說明為何這是驗收面而非實作細節：owner 若只交匯出檔，會把「有想法但没下单」讀成「什麼都没發生」。

敏感測（M10）：在 `_journal_rows` 的 `return rows` 之前插入「為每個 decision 補一列」的區塊（即上述行為被改變時），該節點如預期 **1 failed, 13 deselected in 1.10s**；還原後 `paper_portfolio.py = 361f07890fbb7f20 OK`。所以這條不是永真斷言。

**未做、需 owner 裁決（不是缺陷修復）**：是否把「無 order 的決策」納入 journals 匯出（需新增 `record_type` 與其欄位語意，且會改變現有客戶拿到的檔案內容）。在此之前來源鏈的狀態＝有 order 的路徑可追溯到真實推論（已證）；無 order 的決策在匯出中缺席（已證並已釘住）；兩者都由同一個真實 `/predict` 起算。

## 本輪批次（命令／內容身份／run dir／exit code／跳過原因）

| 批 | 內容 | run dir | 結果 | PYTEST_EXIT |
|----|------|---------|------|-------------|
| spellgate1 | #193 首次嘗試（含 `_lock_portfolio` 回退）的 gate 13 節點 | `/tmp/spellgate1` | 2 failed, 11 passed（失敗節點＝`:584`、`:631`）→ 該批如實以 FAIL 存證；exit code 未存檔，2 failed 即非零，此處不填未測值 | 未存（非零） |
| spellgate2 | 最終內容 gate 13 節點 | `/tmp/spellgate2` | **12 passed, 1 skipped**, 8.64s | 0 |
| paperfinal2 | 37 支受影响清單（`/tmp/papergate3/manifest.txt`，sha `c7ad3e300a8d109846da177c99625161b7aa9901cc1e2433026b99b12fc0fe1c`）於最終內容 | `/tmp/paperfinal2` | **599 passed, 46 skipped**, 514.46s | 0 |
| skipreason1 | `tests/test_http_api_success_chain.py`（skip 理由更正後單獨重驗該支） | `/tmp/skipreason1` | 1 skipped, 0.04s（新理由文字如實印出） | 0 |
| paperfinal3 | 37 支 ＋ chain 支，#188 節點加入**前**的最終內容 | `/tmp/paperfinal3` | 599 passed, 47 skipped, 519.84s | 0 |
| chainexport1 | `tests/test_successful_inference.py`（#188 新節點，單獨重驗受影響流程） | `/tmp/chainexport1` | 14 passed, 11.31s（sha `fea985dd5a4385ba`） | 0 |
| **paperfinal4** | **頭條批**：38 支＝37 支清單 ＋ chain 支，全部最終內容（含 #188 節點） | `/tmp/paperfinal4` | **600 passed, 47 skipped**, 527.72s | 0 |
| migtailfinal | `tests/test_migration_tail_contract.py`（15 節點）＋ `scripts/check_migration_tail.py --with-alembic --json`（#191 的同一受測檔案在最終內容上重測） | `/tmp/migtailfinal` | 15 passed, 3.83s；checker `status=OK`、`head=c3d4e5f6g7h8`、`module_count=revision_count=56`、`unreachable_from_head=[]` | 0（pytest）／0（checker） |

加總自洽性（可驗算，非宣稱「唯一總數」）：頭條批 **600/47** ＝ `paperfinal2` 的 599/46（37 支清單）＋ chain 支 0/1 ＋ #188 新節點 1/0。較 #193 前的 596/45 多出 **4 passed ＋ 2 skipped**＝P6 新增四節點（3 支可在 lane 跑、1 支 PG-only）＋ #188 的 1 支可在 lane 跑；無任何既有節點狀態變動。600/47 是單一內容身份、單一 run dir 的一次實際輸出；其他列是過程中批次，不得與之相加當成更大的總數。

46→47 的 skip 逐節點身份未在本輪逐支重列（前輪記錄的 3 支 row-lock skip 仍在）；新增的那一支＝`:631`。若要逐 id 清單需以 `-rs` 重跑頭條批，本輪未做，故不聲稱已持有逐 id skip 帳。

`/tmp/papergate4`（596/45，531.50s）為 #193 前的對照批，其 run dir 內未存 `before.txt`，該批的受測內容指紋無法在該目錄回測——只作對照，不作為主證據。#190 首輪的 gate 輸出目錄（`/tmp/papergate1`、`/tmp/papergate2`）已不存在，該批原始輸出不可回測；#190 的修復以現行 `paper_trading.py` 內容＋頭條批在最終內容上的通過狀態為準。

## 變異測試（每支單獨套用→跑該批→還原→指紋核對；全部在最終內容上）

| 變體 | 意圖 | 實測偵測 |
|------|------|----------|
| M1 移除錢 bind 的型別宣告 | 證明 #190 的型別化不是裝飾 | **10 failed, 2 passed, 1 skipped** |
| M2 `_as_text` 交回 `uuid.UUID` 物件 | 證明 lineage 綁成 dashed 文字是必要的 | **3 failed**（version 命名／run 綁定／揭露面） |
| M3 lineage raw-SQL 分支改 `IS NULL` | 證明該分支真的能指出綁定 version | **1 failed**＝`:276 test_the_run_binding_is_the_version_a_decision_may_claim`（注：原substring 因 SQL 字串跨行切分而在檔案內出現 0 次，第一次執行為 PATCH-NOT-APPLIED；以完整片段 `"= :portfolio_hex AND strategy_version_id IS NOT NULL "` 重套後才有上述結果） |
| M4 撤掉 ownership 讀取的 dashed 回退 | 證明回退有被讀到 | **1 failed**（`:253`） |
| M5 上限計數改回單拼寫 | 證明 `held_slot_count` 真的需要 `stored_as` | **1 failed**（`:563`） |
| M6 帳號清單改回單拼寫 | 同上對 `list_portfolios` | **1 failed**（`:546`） |
| M7／M8 技能門檻的 id／owner 改回 identity key | 證明門檻兩個欄位各自有感 | 各 **1 failed**（`:584`） |
| M9 lineage 的 ORM 分支改成不可能命中 | 意圖證明該分支也在做工 | **未偵測（0 failed）**——誠實記錄：本 gate 在離線 lane 上由 raw-SQL 分支承担，ORM 分支（`app/api/paper_portfolio.py:990-993`）未被此套節點區分。它仍是在 PostgreSQL 上第一個回答的那一支，但本輪沒有證據，故不宣稱已釘住 |

每支還原後逐位元組一致（本輪結束時再測）：`paper_trading.py f125d37a…`、`paper_portfolio.py 361f0789…`、`skill_units.py d333e37a…`、`personal_selection.py 48acf2ec…`、`uuid_spelling.py 8b5263aa…`、`test_paper_create_engine_consistency.py 374ef741…`。

## #191 與 #192 在本輪最終內容上的狀態（重測，非新寫）

- **#191 migration DAG**：以釘選的 Alembic `ScriptDirectory` 驗證唯一 head／血緣（`scripts/check_migration_tail.py:176-180` 取 heads，`:218` 算 `unreachable_from_head`，`:195` 比對 `module_count` 與 `revision_count`），在最終受測內容上重測＝`status=OK`、`head=c3d4e5f6g7h8`、56／56、`unreachable_from_head=[]`；契約測試 `tests/test_migration_tail_contract.py` 15 節點全過（`/tmp/migtailfinal`）。`EXPECTED_TAIL` 未為了迁就現況而改——它釘的是同一條 tail，且 branched-tree 拒絕路徑由該 15 節點中的節點行使。**未做**：對任何 PostgreSQL 資料庫實際執行 `alembic upgrade`（未批准）。
- **#192 selection／alias 不漂移**：三個語意各自有釘點且都在頭條批通過——`skill_unit_id IS NULL` 是 alias 範圍而非萬用字元（`app/services/personal_selection.py:162-176`，端點節點 `tests/test_formal_skill_unit_authorization_endpoint.py:177`）、預測範圍跟隨技能切換而舊版本仍可讀（歷史決策不隨 alias 漂移，`tests/test_personal_model_comparison.py:838`）、清單為每一列標出範圍且預設不藏任何 selection（`tests/test_formal_skill_unit_authorization_endpoint.py:300` 對 `app/api/personal_models.py:1261-1291`）。本輪未改這三支檔案的語意（除 `_owned_skill_unit` 的拼寫可見度），故這部分是**重測既有證據於同一候選**，不是宣稱新完成。

## 本輪順帶更正的一處不精確記載

`tests/test_http_api_success_chain.py:23-31` 的 skip 理由原寫「第一跳 `POST /paper-trading/portfolios` 的 Decimal bind（`paper_trading.py:74`）與 replay 的 `FOR UPDATE`（`paper_trading.py:874`）**兩者**皆 PostgreSQL-only」。三點均與現況不符：路由實名 `/api/v1/paper-trading/create`（該檔 `:241` 就是這樣呼叫）；Decimal bind 已被 #190 修掉且在 lane 上跑過（`test_paper_create_engine_consistency.py:426`）；`FOR UPDATE` 的現行位置是 `app/services/paper_trading.py:956-964`。已改為只記載**仍然成立**的那一個障礙（replay 的行鎖需要 PG），理由文字現測會印出。此改動不動任何斷言、不增減 skip 數（`/tmp/skipreason1` 與頭條批皆為該支 1 skipped）。

## 仍未證明／NOT RUN（本輪）

- **每日模型推論 tick**：未實作（三項 owner 裁決未決，見前輪同節）。
- **PG 層級**：`test_the_called_engine_flushes_into_the_callers_transaction_and_never_commits`（`:368`）與 `test_the_owner_can_execute_a_manual_trade_on_the_created_account`（`:631`）＋既有 3 支 row-lock 節點仍 NOT RUN，需 `ALETHRA_PG_DATABASE_URL` 與精確批准啟動／建立測試 DB。
- **統一寫入端拼寫**：未做。涉及既有 PG 資料的既有列與 schema 政策，需 owner 裁決＋（可能）migration；本輪不擅自擴權，僅把「讀取端看得見」修好並把「寫入端寫不出」量測成可判定的 skip。
- **仍屬同一不可見類別、本輪未改的讀點**（實測存在，逐支 `file:line`）：`app/api/crypto_paper.py:100`、`:112`；`app/services/execution.py:45`；`app/services/paper_execution.py:279`、`app/services/order_lifecycle.py:278`（後兩支比對的是已載入實例的 `portfolio.id`，即 `StaleDataError` 那一類）；`app/api/simulations.py:659`、`:880`、`:1077`；`app/services/agent_strategy.py:827`（`_observed_round_trip`）、`:2237`（`existing_trade_eligibility`）——對 create-lane 帳號，後兩支會少算 fill／order 證據。`app/workers/learning_scheduler.py:188` 是跨擁有者掃描（不按 owner 過濾），**不屬於**本類，先前清單把它列入是錯的分類，此處更正。
- **前端**：`frontend/src/lib/api.ts:1219-1226` 的 `paperTrading.create` 簽章仍**沒有** `strategy_version_id`／`dataset_id`，且 `frontend/src` 內**沒有任何呼叫者**（實測只有定義那行）；UI 建立技能帳號一律走 `POST /api/v1/paper-portfolio`（`app/api/paper_portfolio.py:520`，ORM／hex 拼寫），實測呼叫者＝`frontend/src/app/paper-portfolio/page.tsx:122`、`frontend/src/components/workspace/OrderPreviewPanel.tsx:644`、`frontend/src/app/experiments/[id]/simulation/page.tsx:363`。帶 lineage 的 raw-SQL 建立入口目前只能由正式 HTTP API 或 `live_trading_daemon` 觸及，**不是**任何頁面的按鈕。此為揭露，不是已完成的 UI 成果。
- **時間範圍與成本**：`days_back` 是 replay 參數（非 create 欄位）；`commission_rate`／`slippage_rate` 是 `execute_historical_replay` 的常數預設（`app/services/paper_trading.py:247-248`），`POST /replay/{run_id}`（`app/api/paper_trading.py:341-359`）只轉 `days_back`／`position_size_pct`／`max_positions`，**任何正式請求都無法設定成本**。`exit_config` 會被存入 run 列並由正式讀回回報，但 replay 的出場取自歷史 trades 的 `t.exit_id`（`app/services/paper_trading.py:331`），**不讀** `run.exit_config`。
- **browser 點擊旅程**：NOT RUN（需精確批准啟動服務／瀏覽器；代理不得代替 owner 驗收）。
- **實際執行 migration**：NOT RUN。#191 驗的是 DAG 結構（唯一 head、血緣、模組數＝修訂數、不可達集合）；對任何 PostgreSQL 資料庫執行 `alembic upgrade head` 仍需精確批准（含目標 DB 標識），本輪未執行、未建 DB、未動 grant。

## 必要 Git 保存範圍（待批准，本輪未執行）

本輪新增／修改且需一起固定才構成上述證據的最小檔案集（逐支 `git add`，不用 `-A`）：

已追蹤（M）：`backend/app/services/paper_trading.py`、`backend/app/api/paper_portfolio.py`、`backend/app/services/skill_units.py`、`backend/app/services/personal_selection.py`、`backend/tests/test_http_api_success_chain.py`、`backend/tests/test_successful_inference.py`
未追蹤（??，不 `git add` 就不會進 commit）：`backend/app/lib/uuid_spelling.py`、`backend/tests/test_paper_create_engine_consistency.py`
加本記錄檔：`EXECUTION_TABLE_20261117.md`（現測 `shasum -a 256` 為準；不在本句自我引用指紋）

192 paths 中其餘檔案未逐支歸屬，與平行 session `5062987d` 的責任分界在未對帳前不得以 commit 方式固定。commit／push 仍需事先列出 repo／remote／branch／檔案／訊息並取得明確同意。




---

# 2026-11-18（同日第三輪）：#194 寫入端的 UUID 型別契約，與它讓「手動下單」這一跳變成可量測

## 先撤回：本輪 12:10–12:20 那批頭條數字不再可引用

`/tmp/uuidhead2`（12:10:46 起、12:20:03 收，627 passed／46 skipped，exit 0）交出的是一批**內容身份不可重建**的量測：

- 該 run dir 只為 5 支檔案留了指紋（`args.txt`＋`five.txt`：`app/api/journal.py`、`app/api/paper_portfolio.py`、`app/lib/uuid_spelling.py`、`tests/test_journal_api.py`、`app/lib/uuid_bind.py`），**`app/services/paper_trading.py` 不在其中**；
- 該檔現測 mtime 是 **12:26:09**——在該批結束（12:20:03）之後，也在 `five.txt` 量測（12:22:28）之後。12:22:28 之後仍被改動的受測檔案，其當時內容沒有任何指紋留下來。

所以本輪不引用 uuidhead2 的通過數，也不把它與本輪結果拼成同一候選。改成的做法（下方 runner）是**跑前先量 438 支 `.py` 的逐檔 sha256、跑後再量一次並 diff**，讓「受測期間內容未動」變成該批自己的證據而不是叙述。`app/lib/uuid_spelling.py` docstring 中「the manual-order path that used to fail with ``StaleDataError`` runs on the lane」那句話，其依據原本是 uuidhead2 這批被撤回的量測——該句描述的代碼現在由本輪 `r3headf`（631 passed，含該節點）與 W1 變異重新量過，宣稱的出處改指 `r3headf`。

## 候選身份（本輪現測）

| 項目 | 實測值 |
|------|--------|
| repo／branch／base | `.../ac51e592/alethra`、`live1-impl`、`5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（本機非 git remote 操作，未 commit／未 push） |
| 未提交 diff | `git status --porcelain`＝**195 paths**＝134 M＋61 ??（跑前 195／跑後 195，同批量得） |
| 內容身份 | `backend/app`＋`backend/tests` 全部 **438 支 `.py`** 逐檔 `shasum -a 256`；跑前＝跑後 → `CONTENT_UNCHANGED`；六支變異全部還原後於 13:02:02 再測一次（`/tmp/r3_final2.txt`），仍與 `r3headf/content_after.txt` **逐位元組相同**；本節寫完後的 13:12:33 再測一次仍相同；13:14:01→13:23:09 的 `-rs` 重跑批（`r3headrsf`）其跑前＝跑後清单 sha256 均為 `a642f8f71943ad20…`，與 `r3headf/content_before.txt`＝`content_after.txt` 同一份。記錄檔本身不在 438 檔內（只含 `.py`），因此本檔的後續書寫不改變受測內容。 |
| runner（本輪寫死，每批只換 run dir 與 args） | `cd alethra && DATA_PROVIDER=demo ALETHRA_TEST_DB="sqlite:////tmp/<run dir>/lane.db" ~/.local/bin/uv run python -m pytest $(cat /tmp/<run dir>/args.txt) -q -p no:warnings [-rs] > <run dir>/out.txt 2>&1; echo $? > exit.txt`，兩側各量一次 438 檔指紋與 `git status --porcelain \| wc -l`（腳本 `/tmp/r3run.sh`） |

本輪受測內容指紋：**13:05:57 現測**（在頭條批 `r3headf` 收工 12:59:26、六支變異全部還原之後），15 支逐檔 `shasum -a 256`（前 16 碼）；同一次比對確認這 15 支與 `r3headf/content_after.txt` 中對應列完全一致，`git status --porcelain`＝195 paths＝134 M＋61 ??。P8 之後（13:47）再一次逐檔量同 15 支：**14 支指紋與 git 狀態（M／??）完全不變**，只有受測檔本身前移為 `9a1db465876df40c`（見下方第三輪補記）。

| 檔案 | sha256（前 16） | git |
|------|-----------------|-----|
| `app/services/paper_trading.py` | `b3928ad2cdf8eb5d` | M |
| `app/services/live_trading_daemon.py` | `be890405269e3707` | M |
| `app/api/paper_trading.py` | `b760daed5cb633c6` | M |
| `app/api/journal.py` | `f3728b33eb8e384c` | M |
| `app/api/paper_portfolio.py` | `964726dcc254cae6` | M |
| `app/services/skill_units.py` | `d333e37acab77a17` | **??（未追蹤）** |
| `app/services/personal_selection.py` | `48acf2ec05481ef9` | M |
| `app/lib/uuid_bind.py`（本輪新檔） | `7ad3c7c32ea340fd` | **??** |
| `app/lib/uuid_spelling.py` | `b8440a69f8745685` | ?? |
| `app/lib/timeutil.py` | `8fdc6f554a59d8a6` | M |
| `tests/test_paper_create_engine_consistency.py` | `7df513e23b4d51e2`（766 行、17 支 `def test_`；第二輪記的 `374ef741…`、本輪 12:40 前記的 `2ecb3510…` 都是這檔的舊內容；**P8 之後現測為 `9a1db465876df40c`／869 行／19 支**，見第三輪補記） | **??** |
| `tests/test_journal_api.py` | `d7d4a4a9155291ba` | M |
| `tests/test_ownership_integration.py` | `38349cec47c1fffd` | M |
| `tests/test_successful_inference.py` | `fea985dd5a4385ba` | **??（未追蹤）** |
| `tests/test_http_api_success_chain.py` | `c1eb9ef3f1b81912` | M |

15 支指紋相同只能證明這 15 支。頭條批的內容界線是 438 檔：`r3headf/content_before.txt`（12:50:15）＝`content_after.txt`（12:59:26）逐檔相同，六支變異全部還原後在 **13:02:02** 再量一次全數 438 檔（`/tmp/r3_final2.txt`），仍與 `r3headf/content_after.txt` **逐位元組相同**——即本節這 15 支的 13:05:57 值就是頭條批受測時那一版的值。

## 工程單元一（#194）：寫入邊界用欄位自己的型別綁 UUID，而不是把文字交給 driver

根因不是拼寫偏好，是**兩個寫入者對同一欄位寫出兩個字串**：`postgresql.UUID(as_uuid=True)` 經 `app/database.py` 在本 lane 編譯成 `VARCHAR(36)`，其 bind processor 是 `value.hex`→ORM 寫入一律 32 碼無破折號；而 raw `text()` 把未標型別的 `str(uuid4())` 原封不動交給 sqlite3→36 碼含破折號。同一欄位thus有兩種身分，PostgreSQL 上兩者都被解析成同一個 native uuid，所以這缺陷在 PG 不會顯形。

修法只有一條契約：**值先解析成 `uuid.UUID`，綁定時標上該欄位自己的型別**，由各方言自己決定要渲染什麼。

- `app/lib/uuid_bind.py`（新，79 行）：`UUID_TYPE = UUID(as_uuid=True)`（`:40`）、`to_uuid()`（`:43`）、`uuid_binds(*names)`（`:61-63`）、`as_id_text()`（`:66-79`）。
- `app/services/paper_trading.py`：`create_portfolio`（`:94`）的帳號 INSERT 綁 `uuid_binds("id","owner_id")`＋`to_uuid(user_id)`（`:141-148`），run INSERT 綁 `uuid_binds("id","portfolio_id","strategy_version_id","dataset_id")`（`:173-180`）；模組層 `_STATUS_*`／`_REPORT_*` 三支讀取各自標型（`:44`、`:47`、`:55`、`:58`、`:61`）。該檔現測 `uuid_binds(` 33 處、`to_uuid(` 40 處。
- `app/services/live_trading_daemon.py`：decision／order／fill／run-metrics／daemon-portfolio／daemon-run 六支 raw 寫入全走同一契約（`:124`、`:151`、`:180`、`:203`、`:302`、`:324`、`:459`；現測 7＋14 處）。
- 呼叫端拿到的 id 仍是一種拼寫：`create_portfolio` 回傳 `str(portfolio_id)`（dashed，`app/services/paper_trading.py:208`），raw 讀回則經 `as_id_text()` 歸一，兩者都是 `str(uuid.UUID(...))`。

## 工程單元二：create-lane 帳號的四個後續入口，其中「業主親手下單」這一跳從 PG-only 變成 lane 上實跑

`tests/test_paper_create_engine_consistency.py` 的 P6 段（檔頭註解 `:536-552` 記載缺陷本體）有四支節點，全部由正式 HTTP 入口建立帳號後再讀（行號為 13:05 現測，P7 附於其後使 P6 段整體下移 2 行）：

| 節點 | 位置 | 量的是哪個入口 |
|------|------|----------------|
| `test_the_created_account_appears_in_the_account_list_the_product_reads` | `:555` | `POST /api/v1/paper-trading/create` → `GET /api/v1/paper-portfolio` |
| `test_the_ceiling_counts_accounts_written_by_the_create_route` | `:572` | 上限計數對第二支 create 回 `skill_unit_limit_reached` 且 `held_skill_units==1` |
| `test_the_create_lane_account_passes_the_skill_unit_door` | `:593` | `GET /api/v1/ml/personal/records/{id}/selections?skill_unit_id=...` |
| `test_the_owner_can_execute_a_manual_trade_on_the_created_account` | `:631` | `POST /api/v1/paper-portfolio/{id}/orders`（201、`actor_type=="human"`） |

第四支在前一輪是 **PG-only skip**（本輪指紋表把它所在的 `374ef741…` 記為舊內容；現測 `7df513e2…` 已把該 skip 拿掉，節點 docstring `:631-644` 改寫為：寫入端標型之後，lock 載入的那一列與 lock 之後 UPDATE 尋址的那一列是同一列，故本 lane 能量測「身分全程存活」；**行鎖本身仍是 PostgreSQL 行為，本輪沒有在 SQLite 上宣稱競態安全**）。lock 端程式碼現測：`app/api/paper_portfolio.py:125` 的 `_lock_portfolio` 以單一 identity 比較載入（刻意不附 dashed 重試，其 docstring 記載理由），`app/services/order_lifecycle.py:278` 是它之後寫入所尋址的同一個 identity key。

## 工程單元三：讀回來的 id 與時間戳只有一種拼寫

`app/api/journal.py` 的 `/journal/unified` 兩半都用不標型別的 `text()` 讀，回來的東西因此是 driver 存的樣子：

- id：`as_id_text()`（`:582-583`、`:656-657`）先解析再 `str()`，PG 的 `uuid.UUID` 與本 lane 的 32 碼 hex 因此同一個回應欄位；這值會被呼叫端當成 path parameter 帶回來，兩種拼寫不能只靠其中一邊被接受。
- 時間戳：`iso_from_stored()`（`app/lib/timeutil.py:21`，由 `app/api/journal.py:20` 引入，用於 `:587-588`、`:659`、`:664`）——本 lane 的 `text()` 讀回是 TEXT，對它呼叫 `.isoformat()` 是 `AttributeError`，即 PG 上同一支 SQL 正常、本 lane 500。
- `app/api/paper_trading.py:483` 的 `run_id` 同樣經 `as_id_text`。

## 工程單元四（補 #193 的證據缺口）：讀取回退現在有自己的節點

前兩輪把 `stored_as()`／`dashed_text()`（`app/lib/uuid_spelling.py`）當作 #193 的修法交出，但**沒有任何節點能讓這兩肢被偵測**：候選內每一列帳號都由已標型的寫入者產生，回退在這些列上永遠不會被命中，所以把它整支刪掉也不會死任何測試。本輪補的 P7 段（檔頭註解 `:670-688`）就是為填這個洞：

| 節點 | 位置 | 命中哪一肢 | 斷言 |
|------|------|------------|------|
| `test_an_account_left_in_the_dashed_spelling_is_still_in_the_account_list` | `:708` | `stored_as`（`app/api/paper_portfolio.py:584`） | 該列出现在 `GET /api/v1/paper-portfolio` |
| `test_the_ceiling_counts_an_account_left_in_the_dashed_spelling` | `:718` | `stored_as`（`app/services/skill_units.py:71`） | `monkeypatch` 上限＝1 時第二支 create 回 409 `skill_unit_limit_reached` 且 `held_skill_units==1` |
| `test_an_account_left_in_the_dashed_spelling_answers_its_own_detail_read` | `:731` | `dashed_text`（`app/api/paper_portfolio.py:115`） | `GET /api/v1/paper-portfolio/{id}` 回 200 且 `id` 是同一支帳號 |
| `test_a_dashed_spelling_account_passes_the_skill_unit_door` | `:746` | `stored_as`（`app/services/personal_selection.py:125-126` 兩欄） | 選擇門（`GET .../selections?skill_unit_id=`）不再回 `skill_unit_not_found` |

寫法上的兩個服從點：

- 該列由 `_legacy_dashed_account()`（`:691-705`）以**修復前寫入者的方式**插入：未標型別的 `str(uuid4())` 直達文字欄。這是本節唯一一处直接 SQL，且它是**製造舊資料的前提**而非旅程步驟——owner 要求的是「使用者不能靠直接 SQL 完成旅程」，P7 的账号建立、選模型、下單、讀回全部走正式入口，只有「一列已經以舊拼寫存在」這個條件由插檔重現。
- P7 段刻意**不宣稱這種帳號可以被交易**：下單路徑經 `_lock_portfolio`／`app/services/order_lifecycle.py:278` 的 ORM identity key 重讀，該處沒有 dashed 回退，本輪沒有量測一列 dashed 帳號在那一跳會發生什麼。此缺口記在下方「仍未證明」，不是已經通過。

W5／W6 兩支變異證明這四支節點各自可偵測（見變異表）：W5 砍掉 `stored_as` 的那一行只死 3 支 P7 節點，W6 砍掉 detail read 的 `dashed_text` 重試只死 1 支。兩者的死法不同於「回退仍在但寫入端退回」的 W1，因此這四支補的確實是另一層證據。

## 本輪批次（命令／內容身份／run dir／exit code／跳過原因）

每批由 `/tmp/r3run.sh <run dir> <args file>` 執行：跑前量 438 檔 sha256＋`git status --porcelain | wc -l`，跑後再量一次並 diff，兩者與 `window.txt`（起止）、`out.txt`、`exit.txt`、`content_drift.txt` 一起留在該 run dir。以下兩批是本輪頭條數字唯一的引用來源，都跑在同一份最終內容上（受測檔指紋 `tests/test_paper_create_engine_consistency.py`＝`7df513e2…`）。

| 批 | args（檔數） | run dir | 結果 | exit | 內容身份 |
|----|--------------|---------|------|------|----------|
| 受影響流程重驗 | 5 檔：`test_paper_create_engine_consistency`、`test_ownership_integration`、`test_journal_api`、`test_skill_unit_ceiling`、`test_successful_inference` | `/tmp/r3b1f` | **83 passed, 1 skipped** in 43.57s | 0 | `CONTENT_UNCHANGED`（12:49:23→12:50:10；438 檔跑前＝跑後；paths 195→195） |
| 頭條批 | 39 檔清單（`/tmp/r3headf/args.txt`；與本輪 `r3head`／前輪 `uuidhead2` 用的同一份 39 檔） | `/tmp/r3headf` | **631 passed, 46 skipped** in 547.49s (0:09:07) | 0 | `CONTENT_UNCHANGED`（12:50:15→12:59:26；438 檔跑前＝跑後；paths 195→195） |

本輪頭條數字只由 `r3headf` 一批承載；`r3b1f` 是其子集（5 檔 ⊂ 39 檔），兩者不相加。**注意：本節之後又補了 P8（#199），候選內容因此前移，本輪最終頭條是 `r3headg` 的 633／46，見下方「第三輪補記」；本節與上表的 631／46 是 P8 前那份內容的事實記錄，不再是本輪引用的頭條。**另有兩支**支援批**在同一份最終內容上跑過，用途是補身分而非補總數，因此不在上方表格：`r3headrsf`（`-rs` 重跑，總數與 `r3headf` 相同，交出 46 支 skip 的逐節點帳）、`r3names2`（5 支 node id 點名批）。跳過那 1 支的原因逐字取自 `r3b1f/out.txt`：`tests/test_skill_unit_ceiling.py:235` — 「two first-time creates at the last free slot need real MVCC and a PostgreSQL backend: SQLite serialises writers and takes no row lock」。

同輪稍早有兩批在同一份腳本下跑過，但它們的候選**少 P7 四支節點**（其 `content_after.txt` 記的是 `2ecb35104eb44154`），因此被上面兩批取代；保留其記錄而非抹除：

| 批 | run dir | 當時結果 | 為何不作為本輪頭條 |
|----|---------|----------|--------------------|
| 受影響流程重驗（被取代） | `/tmp/r3b1` | 79 passed, 1 skipped in 41.04s（12:30:48→12:31:33，exit 0，`CONTENT_UNCHANGED`） | 受測內容其後被改（加入 P7）；依「正式驗收期間固定受測內容」，修改前後結果不拼成同一候選。 |
| 頭條批（被取代） | `/tmp/r3head` | 627 passed, 46 skipped in 543.18s（12:31:37→12:40:43，exit 0，`CONTENT_UNCHANGED`） | 同上，該批候選不含 P7 節點。 |
| 頭條批附 `-rs`（中止） | `/tmp/r3headrs` | **不交出任何數字** | 12:43:06 起跑，P7 寫入受測檔時該批仍在跑；依同一規則中止，其 dir 只有 `content_before.txt` 與 `window.txt`，無 `content_after.txt`、無 `exit.txt`。同樣目的（附 `-rs` 的頭條批）在固定內容上於 13:14 完成一次，見下方「逐節點 skip 帳」節的 `r3headrsf`；本行只記錄該次中止的事實，不代表 `-rs` 帳未持有。 |
| P7 開發探測 | `/tmp/r3p7probe` | 不列入證據 | 該 dir 現測只有 `lane.db`、`lane2.db`，沒有 `out.txt`、`args.txt`、指紋或 `exit.txt`——是寫節點時的終端探測，沒留下可引用的產物。P7 四支節點的正式量測由 `r3b1f` 與 `r3headf` 承載（兩批的 args 都含該檔）。 |

可加性自我檢查：79→83 與 627→631 的差額都是 **4**，恰為 P7 新增的節點數（該檔 `def test_` 現測 17 支）；兩批跳過數仍分別為 1 與 46（未變），所以這 4 支全數來自新節點，不是原本節點改判。跨輪則**不宣稱可加**：第二輪頭條 600 passed／47 skipped 與本輪 631／46 之間是 +31／−1，其中只有 5 支可在此直接歸因（manual-order 節點由 skip 轉為實跑＝−1 skip、+1 pass；P7 新增 4 支 pass），其餘 +26 屬兩輪之間其他改動（`journal.py`、`timeutil.py`、`live_trading_daemon.py` 等的節點增修），本輪未逐檔重放前輪內容，故如實標為**未逐檔歸因**，兩批數字不相加當作通過總數。

### 頭條批 46 支跳過的分解（物件是節點，不是檔案）

逐字取自 `r3headf/out.txt` 的 short summary；按檔案×原因：

| 檔 | 支數 | 原因（該批 own text） |
|----|------|------------------------|
| `tests/test_paper_order_postgres_integration.py` | 21 | requires a disposable PostgreSQL database (ALETHRA_PG_DATABASE_URL) |
| `tests/test_intraday_simulation_api.py` | 15 | requires a disposable PostgreSQL database (ALETHRA_PG_DATABASE_URL); the intraday per-bar replay must be proven on PostgreSQL, not SQLite |
| `tests/test_c1_c4_complete.py` | 4 | live-stack probe: ALETHRA_LIVE_PROBES is not set to 1（會寫 bars／sessions／paper orders 進 API 實際用的資料庫，故只跑在明確交出的拋棄式 stack） |
| `tests/test_manual_decision_books_linked_portfolio.py` | 3 | row-lock serialisation requires a real PostgreSQL backend |
| `tests/test_skill_unit_ceiling.py` | 1 | two first-time creates at the last free slot need real MVCC and a PostgreSQL backend |
| `tests/test_risk_policy_across_lanes.py` | 1 | row-lock serialisation requires a real PostgreSQL backend |
| `tests/test_http_api_success_chain.py` | 1 | the chain's replay hop locks its run row with `SELECT ... FOR UPDATE`, which needs PostgreSQL |
| **合計** | **46** | 21+15+4+3+1+1+1＝46；36 支為 PG-only 資料庫、6 支為 row-lock／MVCC 語意、4 支為 live-stack probe |

46 這個**數量**在 `r3head`→`r3headf` 之間未變；但被取代那批的 `out.txt` 內 `^SKIPPED` 行數＝**0**（該批在 12:42 把 `-rs` 寫進 runner 之前跑的；現測 `r3headrs/out.txt` 不存在、`r3head/out.txt` 的 `^SKIPPED`＝0 行），所以該兩批只持有數量，上表七檔×理由的分解是且只是 `r3headf` 的帳（`r3b1` 同樣是 0 行，其「1 skipped」只有數量；`r3b1f` 有 1 行，即上方引用的 `test_skill_unit_ceiling.py:235`）。第二輪也沒有留下逐 id 的 skip 帳（其同節 `:467` 已如此記載），因此跨輪仍只能比數量、不能宣稱身分逐支相同。P7 沒有把任何 PostgreSQL-only 分支改在 SQLite 上宣稱通過：本輪唯一拿掉的 skip 是上方「工程單元二」記載的 manual-order 節點，其理由與界線寫在該節點 docstring `:631-644`。另：`test_http_api_success_chain.py` 該支 skip message 內写的 `paper_trading.py:956-964` 是訊息字串裡的舊座標，實際 `SELECT ... FOR UPDATE` 現測在 `backend/app/services/paper_trading.py:986-992`（該讀取已標型 `uuid_binds("id")`）；skip 的實質（該跳需要真的行鎖）不變。

### 逐節點 skip 帳（`-rs` 頭條批重跑：`/tmp/r3headrsf`）

上一段宣告「本輪未持有逐 id skip 帳」的那一步已補做，該宣告因此作废並由本節取代。做法是把同一份 39 檔 args 在**同一份固定內容**上於 `r3headf` 之後再跑一次，這次 `-rs` 已在 runner 內（`/tmp/r3run.sh:19`，sha `479b7177a07a56a6…`，12:42 起生效），所以 short summary 自帶 `SKIPPED [1] <file>:<line>: <reason>` 行。

| 批 | args | run dir | 結果 | exit | 內容身份 |
|----|------|---------|------|------|----------|
| 頭條批附 `-rs`（完成） | 39 檔，`args.txt` 與 `r3headf/args.txt` **逐位元組相同**（`diff -q` 無輸出） | `/tmp/r3headrsf` | **631 passed, 46 skipped** in 545.05s (0:09:05)｜`^SKIPPED` 行數＝**46** | 0 | `CONTENT_UNCHANGED`（13:14:01→13:23:09；paths 195→195）；該 dir 的 `content_before.txt`＝`content_after.txt`，且兩者與 `r3headf/content_after.txt` **逐位元組相同**（438 檔清單 sha256 均為 `a642f8f71943ad20…`） |

此批不新增任何頭條數字（與 `r3headf` 同內容、同 args、同總數），它的用途只有一個：交出 46 支 skip 的逐節點座標。座標是 **pytest 印出的 skip 呼叫點**（`SKIPPED [1] <file>:<line>`），不是 `def test_` 行；一支節點列一行，46 行無重複。以下按檔案分組列出全部 46 支（取自 `/tmp/r3headrsf/out.txt`，非手抄）：

| 檔 | 支數 | skip 呼叫點行號 |
|----|------|------------------|
| `tests/test_paper_order_postgres_integration.py` | 21 | `132, 142, 180, 204, 222, 234, 257, 299, 328, 382, 413, 420, 434, 496, 521, 531, 541, 551, 562, 642, 785` |
| `tests/test_intraday_simulation_api.py` | 15 | `336, 347, 357, 383, 424, 479, 504, 529, 556, 585, 631, 656, 697, 716, 739` |
| `tests/test_c1_c4_complete.py` | 4 | `173, 241, 279, 322` |
| `tests/test_manual_decision_books_linked_portfolio.py` | 3 | `1988, 2034, 2099` |
| `tests/test_risk_policy_across_lanes.py` | 1 | `558` |
| `tests/test_skill_unit_ceiling.py` | 1 | `235` |
| `tests/test_http_api_success_chain.py` | 1 | `230` |
| **合計** | **46** | 21+15+4+3+1+1+1＝46；分組與上方「七檔×原因」表逐檔一致，故該表的 36／6／4 分類（PG-only 資料庫／row-lock、MVCC、`FOR UPDATE` 語意／live-stack probe）现在有節點級對照 |

有了這帳，可以精確說出**本輪沒有證據**的是哪些節點：36 支 PG-only 與 6 支 row-lock／MVCC／`FOR UPDATE` 全部屬於「#194 的拼寫修復在 SQLite 之外未驗證」這一類，其 PG 端要另批、另 host／port／DB／roles／PGDATA／起始 revision 才能跑（見「仍未證明」節，NOT RUN）；4 支 live-stack probe 則會寫進 API 實際用的資料庫，未獲批准前不跑。`test_paper_create_engine_consistency.py` 在此帳本內**出現 0 次**——這正是第二輪 47 支與本輪 46 支那 −1 的實測對照：第二輪被標 skip 的那支 manual-order 節點在本輪是實跑的，且其 skip 理由文字仍留在該檔 docstring `:631-644` 作為界線說明，而非留在 skip 帳裡。

### 點名批（物件是節點身分，不是通過總數）

`-q` 頭條批不印通過節點名，所以「631 含這五支」在頭條批自己是不可直接引用的。以 node id 逐支addressed 在同一份最終內容上另跑一批：

| 批 | args（5 支 node id） | run dir | 結果 | exit | 內容身份 |
|----|----------------------|---------|------|------|----------|
| 點名批 | `…::test_the_owner_can_execute_a_manual_trade_on_the_created_account`、`…::test_an_account_left_in_the_dashed_spelling_is_still_in_the_account_list`、`…::test_the_ceiling_counts_an_account_left_in_the_dashed_spelling`、`…::test_an_account_left_in_the_dashed_spelling_answers_its_own_detail_read`、`…::test_a_dashed_spelling_account_passes_the_skill_unit_door` | `/tmp/r3names2` | **5 passed** in 3.77s，0 skipped | 0 | `CONTENT_UNCHANGED`（13:10:31→13:10:38），且 `content_after.txt` 與 `r3headf/content_after.txt` 438 檔**逐位元組相同** |

此批不與 631／46 相加：它是同一份內容上另一次執行，用途是把上面兩張表點到的 5 支節點以 node id 證明「確實被收集且確實通過」。（先前嘗試 `-k` 選點的 `/tmp/r3names` 因 args 檔經 shell 拆字使表達式斷裂而 rc=4「no tests ran」；該 dir 無任何節點執行，故不構成證據，也在此記錄以防被誤用。）

## 變異測試（六支柱各自可偵測；每支：套用→跑該批→還原→指紋核對）

每支 OLD 文字由 `/tmp/r3mkmut.py` 從**現檔**擷取（不手抄），`/tmp/r3mut.sh <tag> <file> <old> <new> <args>` 在套用前斷言 OLD 於檔內出現次數＝1（否則 `MUTATION_REFUSED`，不套用），跑完立即以該 dir 的 `.orig` 還原並比對還原前後 sha256。六支的區域座標與 `occurrences=1` 在 13:06 於最終內容重新驗證一次；六支實際執行時段是 **13:00:11–13:01:48**（各 dir 的 `out.txt`／`restore.txt` mtime），全部在 `r3headf` 收工之後。六支的 `restore.txt` 皆為 `RESTORED_IDENTICAL`，且 13:02:02 全數 438 檔再測（`/tmp/r3_final2.txt`）仍與 `r3headf/content_after.txt` 逐位元組相同——即變異沒有在受測內容裡留下任何痕跡。

| 變異 | 檔案：區域 | 改的是哪一肢 | 受測批（檔數） | 結果與死掉的節點 |
|------|------------|--------------|----------------|--------------------|
| W1 | `app/services/paper_trading.py:143-152` | 帳號 INSERT 的 `id`／`owner_id` 退回未標型 `str(uuid4())`（＝#194 修復前的寫法） | 3 檔（`test_paper_create_engine_consistency`＋`test_ownership_integration`＋`test_skill_unit_ceiling`） | **8 failed, 35 passed, 1 skipped**（exit 1，27.35s）：`test_the_owner_can_execute_a_manual_trade_on_the_created_account`、`test_the_http_create_commits_once_and_leaves_a_durable_pair`、`test_the_formal_reads_still_do_not_carry_lineage_or_exit_config`、`test_declared_instrument_grain_and_capital_return_through_the_formal_reads`、`test_a_second_account_never_sees_another_accounts_run_in_its_own_reads` ＋ `test_ownership_integration.py::TestHTTPOwnershipIsolation` 的 `test_user_can_read_own_run_status`／`test_rejection_has_no_side_effects`／`test_multiple_runs_per_user_isolated` |
| W2 | `app/api/journal.py:644-646` | `/journal/unified` paper 半邊退回未標型 `str(user.id)` | 1 檔 | **1 failed, 25 passed**（exit 1，5.69s）：`TestUnifiedJournal::test_the_paper_half_names_the_account_the_create_route_made` |
| W3 | `app/lib/uuid_bind.py:79` | `as_id_text` 退回 `str(value)` 原樣 | 2 檔 | **3 failed, 40 passed**（exit 1，18.59s）：journal 兩半（paper＋`test_the_research_half_names_the_owner_own_trades`）與 `test_declared_instrument_grain_and_capital_return_through_the_formal_reads` |
| W4 | `app/services/paper_trading.py:173-180` | run INSERT 的 `id`／`portfolio_id` 退回未標型 dashed 字串（lineage 兩欄 `strategy_version_id`／`dataset_id` 保持標型） | 3 檔（`test_paper_create_engine_consistency`＋`test_daily_replay_reads_stored_bars`＋`test_integrated_acceptance`） | **6 failed, 38 passed**（exit 1，30.70s）：`test_the_named_version_and_its_dataset_are_the_values_the_run_carries`、`test_the_called_engine_flushes_into_the_callers_transaction_and_never_commits`、`test_a_second_account_never_sees_another_accounts_run_in_its_own_reads`、`test_the_http_create_commits_once_and_leaves_a_durable_pair`、`test_the_formal_reads_still_do_not_carry_lineage_or_exit_config`、`test_declared_instrument_grain_and_capital_return_through_the_formal_reads`。注意 `test_the_owner_can_execute_a_manual_trade_on_the_created_account` **不在**死集內：run 列的拼寫不影響下單那一跳，該跳只認帳號列的 identity（與 W1 正好互補） |
| W5 | `app/lib/uuid_spelling.py:51` | `stored_as` 退回單一 `column == uuid.UUID(str(value))`（即拆掉 dashed 那一臂） | 1 檔 | **3 failed, 14 passed**（exit 1，12.89s）：**只**死 P7 的三支 `stored_as` 節點（account list／ceiling／skill-unit door） |
| W6 | `app/api/paper_portfolio.py:115` | 把 detail read 的 `dashed_text(...)` 重試換成一支不存在的常數 id | 1 檔 | **1 failed, 16 passed**（exit 1，13.34s）：只死 `test_an_account_left_in_the_dashed_spelling_answers_its_own_detail_read`。死因是該常數以 `str` 綁上標型 UUID 欄，查詢本身 Raises → 路由記 `code=internal_error`（500）而非 404；證明的是這個 call site 對舊列**確實被執行**，不是它以 404 優雅退場 |

W1 與 W4 是這一輪最有意義的兩格：它們把 #194 的寫入端各自還原成修復前的樣子，而**讀取端的 dashed 回退仍然存在**（`app/lib/uuid_spelling.py`、`stored_as`／`dashed_text` 呼叫點均未動）。在回退還在的條件下仍然死 8 支（W1）與 6 支（W4），說明這些節點靠的是寫入端而不是回退——這正是 owner 要求的「不以擴大讀取回退覆蓋寫入失敗」可以被執行驗證的樣子；W1 死的清單裡有 `test_the_owner_can_execute_a_manual_trade_on_the_created_account`，即讀回退覆蓋不了 ORM identity-key 那一跳（`StaleDataError` 類）。

W5／W6 則補上相反方向：把回退本身拆掉時，死的恰好只有 P7 那四支（3＋1），前兩輪沒有任何節點落在這個差集裡——這就是先前「回退不可偵測」缺口的具體形狀。兩組死集不相交（W1∩W5＝∅），所以寫入端契約與讀取端容忍在候選上是兩層各自被量的證據。

## 對前輪記載的更正（本輪現測推回上文，不是新增主張）

第二輪那節保留原文不覆寫，以下四點以其為更正對象：

1. **git 狀態類別**：第二輪「必要 Git 保存範圍」把 `backend/app/services/skill_units.py` 與 `backend/tests/test_successful_inference.py` 列在「已追蹤（M）」那一列（本記錄檔 `:510`）；現測兩者都是 **`??`（未追蹤）**，與同節 `:511` 那列的 `uuid_spelling.py`、`test_paper_create_engine_consistency.py` 同一類別。兩者指紋仍分別為 `d333e37acab77a17`、`fea985dd5a4385ba`（與第二輪所記相同），所以內容沒變，錯的是追蹤狀態的記載——而 `??` 的檔不在 `git diff` 裡，任何「以 diff 為準」的審查會整支漏掉它們（含 #188 節點所在檔）。本輪的 15 支保存表因此直接以 `git status --porcelain` 的實際類別標記，不再用「M」概括。
2. **`agent_strategy.py` 兩處讀點座標**：第二輪記 `:827`（`_observed_round_trip`）、`:2237`（`existing_trade_eligibility`）；現測是 `app/services/agent_strategy.py:812` 與 `:2225`（該檔未在本輪修改，座標是前輪抄寫時的偏移）。歸類不變：兩處仍以 identity key 比對，對 dashed 舊列不覆蓋。
3. **兩支檔案的指紋已前移，故第二輪的 M4–M8 偵測證據屬於舊內容**：`app/lib/uuid_spelling.py` `8b5263aa…`→`b8440a69…`、`app/api/paper_portfolio.py` `361f0789…`→`964726dc…`（後者因 `_get_portfolio_with_ownership`／`_lock_portfolio` 的 docstring 與 dashed 重試改寫）。第二輪變異表 M4／M5／M6／M7／M8 是在那一版上套用的，本輪**不把它們當作最終內容上的可偵測性證據**——最終內容上的可偵測性由本輪 W5／W6 承載（且範圍更清楚：W5 只命中 `stored_as`，W6 只命中 detail read）。
4. **skip message 內的舊座標**：`tests/test_http_api_success_chain.py` 的 PG-only 理由字串寫 `paper_trading.py:956-964`，現測該區間是回放彙總的 dict 文字，真正的 `SELECT … FOR UPDATE` 在 `app/services/paper_trading.py:986-992`（且該讀取已標型 `uuid_binds("id")`）。第二輪「本輪順帶更正的一處不精確記載」那段把 `:956-964` 當作現行位置，同樣需要更正。此為訊息字串，不是程式碼缺陷；skip 本身仍成立。

另有一項**不是更正而是撤銷**：第二輪頭條 600/47 與本輪 631/46 之間 +31/−1 不可相加比較，因為兩者的受測內容不同（本輪把 `_lock_portfolio` 那一跳從 PG-only 變成在 lane 實跑，同時 `paper_trading.py`、`live_trading_daemon.py`、`journal.py`、`uuid_bind.py` 等均已改寫）。本輪在 P8 之前只以 `r3headf` 為頭條；P8 之後的頭條是 `r3headg`（633／46），見下方「第三輪補記」，`r3headf` 隨之成為該版內容的記錄而非本輪引用值。

## 仍未證明／NOT RUN（本輪）

- **PostgreSQL 端**：目前**沒有任何已部署 PG 資料庫被檢查過是否存在 dashed 舊列**——#193 的讀取容忍因此是「以防萬一」而非「實測需要」；本輪也沒有執行 `alembic upgrade`（未批准），也沒有跑 PG replay／row-lock 節點。頭條批那 46 支跳過的構成是 **36 支 PG-only 資料庫＋6 支 row-lock／MVCC／`FOR UPDATE` 語意＋4 支 live-stack probe**（逐檔分解見上方跳過表；逐節點 skip 呼叫點帳本見 `r3headrsf` 節，46 行全數列出），三者皆 NOT RUN。SQLite 通過不能證明 PG 競態安全，反之 SQLite 上的拼寫差異也不能假定在 PG 同樣顯形。
- **舊 dashed 帳號能否被交易**：**未量測**。下單路徑經 `app/api/paper_portfolio.py:125` `_lock_portfolio`／`app/services/order_lifecycle.py:278` 的 ORM identity key 重讀，該處刻意不附 dashed 回退（理由寫在 docstring：會把 404 變成 500）。W6 只證明 `paper_portfolio.py:115` 那個 call site 被執行，沒有證明一個舊列能完成一筆交易。要把這變成證據，需要的是「以舊拼寫插入→正式下單入口」的斷言，而它目前的合理預期結果是 500 類；這是揭露，不是已通過。
- **改寫歷史列**：未做、也未獲批准（需要 schema／migration 政策裁決）。本輪不順便改寫任何既有列。
- **每日模型推論 tick**：未實作、未執行（三項 owner 裁決未決；且自動消耗 `ai_usage` 的政策未批准前不在實際帳號執行）。本輪沒有為了觀察 tick 而更改系統時間，也沒有連帶啟動 live daemon 或任何外呼 provider。
- **瀏覽器點擊旅程**：NOT RUN（需精確批准啟動服務／瀏覽器；代理不得代替 owner 驗收）。因此「普通客戶經正式入口完成旅程」目前的證據等級是**正式 HTTP 入口＋DB 讀回**，不是 UI journey。
- **前端**（本輪 13:11 重測，非沿用前輪）：`frontend/src/lib/api.ts:1220-1226` 的 `paperTrading.create` 簽章欄位是 `skill`／`instrument_id`／`interval_minutes`／`exit_config`／`initial_capital?`，仍**沒有** `strategy_version_id`／`dataset_id`；`frontend/src` 内搜尋 `paperTrading.create` **0 筆符合**（只有定義，無呼叫者）。因此帶 lineage 的 raw-SQL 建立入口只能由正式 HTTP API 或 `live_trading_daemon` 觸及，不是任何頁面的按鈕。本輪未動前端，故未重 build。此為揭露，不是已完成的 UI 成果。
- **未訂單決策的匯出範圍（#195）**：仍如前記，未在本輪處理。**lineage 門的 raw-SQL 分支隔離開節點（#199）已在本輪補做**，見下方「第三輪補記」，本條不再是缺口。

## 第三輪補記（13:29–13:45）：P8——版本門第二看的隔離節點（#199）

上方「工程單元二」與「仍未證明」把 `app/api/paper_portfolio.py:1005-1013`（版本門的第二看：raw SQL 把存進去的 `portfolio_id` 去掉連字號再比）標為**未偵測**，理由是當時沒有任何節點能讓那一肢單獨回答。這個缺口已補，因此該處「M9 標為未偵測」的記載由本節取代。

### 改動的候選身份

本節只動一支檔案：`backend/tests/test_paper_create_engine_consistency.py` 加入 P8 區塊與兩支節點，並把模組 docstring 的節點清單加上 P8 一項。438 檔跑前／跑後清單逐檔比對，與 `r3headf/content_after.txt` 的**差異只有 1 列**（該檔），其餘 437 檔指紋不變：

| 項目 | P8 前 | P8 後（現測） |
|------|-------|----------------|
| `tests/test_paper_create_engine_consistency.py` | `7df513e23b4d51e2…`／766 行／17 支 `def test_` | `9a1db465876df40c…`／**869 行／19 支** |
| 438 檔 registry sha256 | `a642f8f71943ad20…` | `f4d69940fd2bae08…` |
| `git status --porcelain` | 195 paths | 195 paths（未新增或刪除受追蹤檔案） |

因為 docstring 多了 2 行，**該檔所有節點座標整體 +2**，上方各表引用的舊座標一律以下表為準：P6 區塊 `:539-554`、四支節點 `:557/:574/:595/:633`（manual-order 節點 docstring `:634-646`）；P7 說明 `:671-690`、`_legacy_dashed_account` `:693-707`、四支節點 `:710/:720/:733/:748`；P8 說明 `:771-794`、`_legacy_dashed_run` `:797-815`、`_decision` `:818-827`、節點 `:830`、`:853`。`app/api/paper_portfolio.py` 仍是 `964726dc…`——P8 **沒有改任何產品程式碼**，只加測試。

### 兩支新節點各自證明什麼

| 節點 | 前置（舊拼寫） | 斷言 | 死於哪一變異 |
|------|------------------|------|----------------|
| `test_a_run_row_left_in_the_dashed_spelling_still_contradicts_a_claim`（`:830`） | 以 `_legacy_dashed_account` 插一支 dashed 帳號，再以 `_legacy_dashed_run`（raw SQL、未標型 `str(...)`）插一筆 `portfolio_id` 為 dashed、`strategy_version_id` 非 NULL 的 run 列 | 用正式入口 `POST /paper-portfolio/{id}/decisions` 聲稱**另一個** own frozen version → 400 `version_mismatch`，且 `run_version`／`decision_version` 回顯兩個 id，決策列 0 筆 | **W7**（拿掉第二看，`else` 分支改回 `None`）——此時 ORM 比較找不到該列、`bound_version` 為 None、聲稱被放行 |
| `test_a_run_row_left_in_the_dashed_spelling_admits_the_claim_it_names`（`:853`） | 同上，但 run 列存的就是要聲稱的那個 version | 同一入口聲稱**同一個** version → 201，且 `linked_experiment_version_id` 原樣讀回 | **W8**（保留兩看、移除相等比較改成「只要有列就拒絕」）——該支與 P4 的 `:283`（其前半段斷言 201）一起死 |

兩支合起來才是「隔離」：第一支把**分支本身**變成可偵測（分支被刪除即紅），第二支把**比較方向**變成可偵測（不做比較即紅），所以第一支不會因為「任何舊拼寫列都一律拒絕」這種更壞的實作而誤綠。

與 P7 同一條界線，這裡再寫一次以免被誤讀：`_legacy_dashed_run` 的 raw SQL **是前提，不是旅程步驟**。客戶不會用 SQL 建立一筆拼寫不同的 run 列；該插入只用來重現「標型之前的寫入者留在庫裡的那一列」，在此 lane 沒有別的途徑能產生它（標型之後的正式入口不會再寫出 dashed 列）。斷言的兩側都是正式入口 `POST /api/v1/paper-portfolio/{id}/decisions` 與它寫下的 `DecisionRecord` 計數，所以本節主張的仍只是「讀取端容忍的第二肢對那樣一列有反應」，不是「直接 SQL 也能完成客戶旅程」。

### 補記批次（命令／內容身份／run dir／exit）

| 批 | args（檔數） | run dir | 結果 | exit | 內容身份 |
|----|--------------|---------|------|------|----------|
| P8 兩支節點單獨驗 | 2 支 node id | `/tmp/r3p8` | **2 passed** in 1.64s | 0 | `CONTENT_UNCHANGED`（13:32:54→13:32:59） |
| 受影響流程重驗 | 1 檔（整個 `test_paper_create_engine_consistency.py`） | `/tmp/r3b3` | **19 passed** in 14.05s，0 skipped | 0 | `CONTENT_UNCHANGED`（13:35:24→13:35:41；跑前＝跑後） |
| 頭條批（本節頭條） | 39 檔清單，與 `r3headf`/`r3headrsf` **同一份 args** | `/tmp/r3headg` | **633 passed, 46 skipped** in 547.51s (0:09:07)；`^SKIPPED`＝46 行 | 0 | `CONTENT_UNCHANGED`（13:35:46→13:44:57；registry sha `f4d69940fd2bae08…`；paths 195→195）；批後 13:45 再量一次全數 438 檔（`/tmp/r3_post_p8.txt`）與其 `content_after.txt` **diff＝0 列** |

`r3headg` 的 46 行 skip 帳與 `r3headrsf` 的 46 行**逐行相同**（`diff` 無輸出，含 file:line 與理由文字），所以 P8 沒有把任何 PG-only／row-lock 分支拖進 SQLite 宣稱通過。可加性自我檢查：`r3headf` 631 → `r3headg` 633 的差額＝**2**，恰為本節新增節點數，內容差異也只有 1 檔 1 處；跳過數 46 未變。**候選身分不同，因此 633 取代 631 作為本輪頭條，兩批不相加、也不拼成同一 PASS**；`r3headf`／`r3headrsf`／`r3names2` 的數字屬於 P8 前那份內容（`a642f8f7…`），保留為該版的事實記錄。

### 補記變異（W7／W8；套用→跑該批→還原→指紋核對）

兩支變異都只改 `backend/app/api/paper_portfolio.py`，OLD 文字由 `/tmp/r3mkmut7.py`／`/tmp/r3mkmut8.py` 從**現檔**擷取（不手抄），`occurrences=1` 於套用前斷言，跑完立即還原：

| 變異 | 檔案：區域 | 改的是哪一肢 | 受測批 | 結果 |
|------|------------|--------------|--------|------|
| W7 | `app/api/paper_portfolio.py:1002-1013` | 移除版本門的第二看（`else db.execute(sql_text(...)).scalar()` → `else None`），保留 ORM 比較與相等比較 | 1 檔（整個受測檔，19 支） | **1 failed, 18 passed**（exit 1，14.16s，13:34:04）：死掉的只有 `:830` 那支。`RESTORED_IDENTICAL 964726dc…` |
| W8 | `app/api/paper_portfolio.py:1015-1017` | 保留兩看、移除相等比較（`if bound_version is not None and (… != …)` → `if bound_version is not None:`） | 同上 | **2 failed, 17 passed**（exit 1，14.17s，13:34:41）：`:853` 與 P4 `:283`。`RESTORED_IDENTICAL 964726dc…` |

W7 的死集是**單一節點**，這正是「能區分該分支的行為斷言」想要的形状：該肢若不存在，只有 `:830` 會紅，其餘 18 支（含 P4、P6、P7、`:853`）全綠。兩次還原後該檔指紋回到 `964726dc…`，且 13:35:46 起跑的 `r3headg` 跑前測量的就是這份內容——即變異未在頭條批受測內容裡留下痕跡。

一處**不構成證據的嘗試**也在這裡記錄：W7 第一次套用時 NEW 結尾多寫了一個 `)`，pytest 在 collection 階段就 `SyntaxError: unmatched ')'`、rc＝4、**0 支節點執行**；該次與第二次使用同一個 run dir（`/tmp/r3mutw7`），其 `out.txt` 已被第二次覆寫，所以**沒有留下可回測的產物**，只能作為事實陳述，不可引用為偵測證據。產品程式碼在該次嘗試中已立即還原（`RESTORED_IDENTICAL`）。

### 本輪最終交出的頭條

以 `r3headg` 為準：**633 passed, 46 skipped**（同一份 438 檔內容 `f4d69940fd2bae08…`，跑前＝跑後，paths 195→195，exit 0）。skip 帳 46 行全數列出於上方「逐節點 skip 帳」節，且與 `r3headg` 逐行相同。

## 必要 Git 保存範圍（待批准，本輪未執行）

本輪未 stage、未 commit、未 push；工作樹保持原樣（195 paths＝134 M＋61 ??），平行 session 的檔案未覆寫、未清理。若 owner 批准保存，範圍是且只是以下 15 支（逐檔 `git add`，不用 `git add -A`）：

| 類別 | 檔案 |
|------|------|
| 寫入端契約 | `backend/app/lib/uuid_bind.py`（??，新）、`backend/app/services/paper_trading.py`、`backend/app/services/live_trading_daemon.py` |
| 讀取端契約 | `backend/app/lib/uuid_spelling.py`（??）、`backend/app/lib/timeutil.py`、`backend/app/api/journal.py`、`backend/app/api/paper_portfolio.py`、`backend/app/api/paper_trading.py`、`backend/app/services/skill_units.py`（??）、`backend/app/services/personal_selection.py` |
| 節點 | `backend/tests/test_paper_create_engine_consistency.py`（??）、`backend/tests/test_journal_api.py`、`backend/tests/test_ownership_integration.py`、`backend/tests/test_successful_inference.py`（??）、`backend/tests/test_http_api_success_chain.py` |

未列入：任何 `/tmp/r3*` 跑測目錄、`lane.db`、前端產物、migration 檔案（本輪未新增 migration）。此表是**待代理交付**的保存清單，不是待 owner 批准的程式裁決——程式裁決項已在上方 NOT RUN 逐條標出。

## 第四輪（2026-11-18 14:05 起）：#195 的來源鏈實物——三張表各自的儲存契約

本節回答上一輪欠的實物問題：「`linked_inference_id` 是 ORM／應用層引用，還是由 schema 強制的外鍵」。答案是**應用層引用**，且這不是從註解讀出來的，是從 migration 的實際 op 讀出來的。下面每一行都在本輪重新打開檔案核對過，行號為現檔行號。

### 一欄一位：同一個來源，三種不同的強制方式

| 身份 | 表／欄位 | 檔案行號（本輪現測） | 強制來自哪裡 | schema 有沒有 FK |
|------|----------|----------------------|--------------|-------------------|
| 決策→推論 | `decision_records.linked_inference_id` | `app/models/paper_portfolio.py:221`（行尾註解即寫 `References usage_records.id`） | 只有寫入時的应用層檢查：`app/services/decision_lineage.py:19-80`，其 ORM 查詢在 `:33-36` | **無** |
| 決策→技能帳號 | `decision_records.paper_portfolio_id` | `app/models/paper_portfolio.py:212-214` | 同一欄位另有 migration 加的 FK＋回填空值 | **有** |
| 訂單→決策 | `paper_orders.decision_id` | `app/models/paper_portfolio.py:145-150` | FK `decision_records.id`；交易 journal 就是由這條邊走出來的 | **有** |
| 人類決策事件 | `human_decision_events.*` | `app/models/human_decision.py:24-85` | 無任何 inference／version lineage 欄位可強制 | 不適用（欄位不存在） |

兩支 migration 對照，是本輪實際讀到的 op 全文，不是轉述：

- `migrations/versions/c3d4e5f6g7h8_add_decision_inference_link.py:85-93` — `op.add_column('decision_records', sa.Column('linked_inference_id', sa.UUID(), nullable=True))` ＋ `op.create_index('ix_decision_records_linked_inference_id', …)`。**這個 `sa.Column(...)` 呼叫裡沒有 `sa.ForeignKey`**，整個檔案也沒有。`revision='c3d4e5f6g7h8'`、`down_revision='b2c3d4e5f6g7'`（`:36-37`）。
- `migrations/versions/k5l6m7n8o9p0_add_decision_paper_portfolio_link.py:43-57` — `sa.Column('paper_portfolio_id', UUID(as_uuid=True), sa.ForeignKey('paper_portfolios.id'), nullable=True)` ＋ index；`:58-75` 另有一段從 `paper_orders` 回填的 `UPDATE`。**這一支有 FK。**

因此「兩邊都是 FK」的说法不成立，「兩邊都靠應用層」也不成立：同一張表裡，帳號歸屬由 schema 強制，推論來源由程式碼強制。

### 各自的保留限制（retention limits）

這是本節真正要交的東東，因為它決定了匯出檔裡那格 id 值多少錢：

1. **`linked_inference_id`（應用層引用）**：檢查只發生在**寫入那一刻**（`decision_lineage.py:33-36` 查不到就 404 `inference_not_found`）。由於沒有 FK，`usage_records` 那一列之後被清（計費表的保留政策與決策表不同，正是 `c3d4e5f6g7h8:21-24` 寫下不設 FK 的理由），決策列上的 id **不會報錯、不會被清、也不會隨之消失**——它變成一個讀不回來的識別碼。「寫入時已驗證」不等於「讀取時仍存在」。本輪據此把來源可讀性做成匯出欄位（見下節 `inference_source_state`），而不是留成散文註腳。
2. **`paper_portfolio_id`（schema FK，無 `ondelete`）**：在 PG 上刪除仍有決策的 portfolio 會被外鍵擋下（NO ACTION 語意），這是**保留保護**；但在本 lane 上它**不被執行**——`tests/conftest.py:151-153` 與 `:294` 明確下 `PRAGMA foreign_keys=OFF`，而 SQLite 的 schema 由 `Base.metadata.create_all` 產生（`tests/conftest.py:220-224`），不是由 migration 產生。所以「FK 擋住了刪除」這句話在 SQLite 上既無法證明也無法反證，PG 端未實測（NOT RUN，理由與前輪相同：沒有已批准、已檢查過的 PG 目標）。
3. **決策去重約束**：`app/models/paper_portfolio.py:239-241` 的 `UniqueConstraint('paper_portfolio_id','source_trade_id','decision_type')` 含可為 NULL 的 `source_trade_id`；正式 decision 入口不寫 `source_trade_id`（`app/api/paper_portfolio.py:1038-1055`），所以**同一技能帳號、同一 `decision_type` 的多筆無訂單決策不會被這個約束擋掉**。本輪 #195 的節點會把這件事跑成實測（同技能兩筆推論決策都要 201），不是只寫在這裡。
4. **`human_decision_events`**：`migrations/versions/h8i9j0k1l2m3_add_human_decision_events.py:37-59` 建立時就沒有 inference／version 來源欄，只有 `server_strategy_version_id`（`:46`，NOT NULL，也無 FK）；`v6w7x8y9z0a1_add_human_decision_actor_type.py:28-37` 後續只補了 `actor_type`＋check。它被讀的場合是**訓練標籤那條線**（`app/services/personal_dataset.py:406-411`、`app/services/personalization.py:210-211`、`app/services/decision_labels.py:135-146`、`app/services/personal_journey.py:113-116`），不是客戶的決策歷史讀回；其正式匯出（`app/services/experiment_record.py:92-107` `_human_decision_entry`）也不含任何來源欄。

### 需要交代的一處檔案內在不一致（本輪已改正，屬文字，非程式）

`c3d4e5f6g7h8` 的 module docstring `:9` 原本寫「This migration adds the **foreign-key column** that makes that link queryable」，同一檔案 `:21-24` 又寫「No FK constraint at DB level」，而 `:85-88` 的實際 op 兩者都不衝突地是**無 FK 的參考欄**。第一句是錯誤的措辭（會讓審閱者以為 schema 會保留完整性），本輪改成「reference column … not a schema foreign key」。該檔案是**未追蹤（`??`）且從未在任何庫上執行**（`alembic upgrade` 仍 NOT RUN），所以這一行改動不觸及任何已部署 schema、也不改寫任何歷史列。

### DAG 位置（本輪重算，與 #191 釘選一致）

以獨立腳本解析 `migrations/versions/`：56 個檔案 → 56 個 revision → **heads 只有 1 個，正是 `c3d4e5f6g7h8`**（也就是加 `linked_inference_id` 的那一支），而帶 FK 的 `k5l6m7n8o9p0` 是它那 55 個祖先之一。此結果與 #191 以釘選 Alembic `ScriptDirectory` 得到的 56／56 靜態 DAG PASS 相容。靜態 DAG 通過仍然**不等於** `alembic upgrade` 通過——這條界線本輪沒有改變。

### 「客戶流程是否確實需要 HumanDecisionEvent 保存 lineage」：本輪的判定與依據

判定是**不需要為 #195 新增該欄**，依據是三條可查證的事實，不是偏好：

1. #195 要求的能力是「取得無訂單決策及其可用的 inference 來源」。寫這筆來源的正式入口本來就落在 `decision_records`：`POST /paper-portfolio/{portfolio_id}/decisions` 經 `validate_linked_inference`（`app/api/paper_portfolio.py:1030-1036`）把 id 寫進 `:1045` 那一列；`POST /simulations/{session_id}/manual-decision` 也經同一檢查（`app/api/simulations.py:2053-2057`）後寫進 `DecisionRecord`（`:2206-2225`）。客戶要讀回的東東已經在那張表上。
2. `human_decision_events` 沒有客戶端入口會去要這條來源：前端對 `human-decision` 的引用實測 **0 筆**（`grep -rn "human-decision\|humanDecision" frontend/src`），而 `frontend/src/lib/api.ts:464-466` 對決策歷史的三個正式呼叫（decisions／journalCsv／journalJson）全存在。给它加一欄，加的是无人讀的欄。
3. 但**缺口本身要老實寫下來，不能因為「不在 #195 範圍」就消失**：同一次 `manual-decision` 會同時產出**兩列**——有來源的 `DecisionRecord`（`simulations.py:2214`）與**沒有來源、也沒有任何回指鍵**的 `HumanDecisionEvent`（`:2583-2601`，其 `metadata_json` 只有 `{"source": "manual_decision", "simulation_step_number": …}`，`:2577-2580`）。所以訓練樣本目前能回答「業主的動作是什麼」，不能回答「業主當時看到的是哪一次推論」。要把這變成證據級的需求，需要的是（i）一個最小欄位或一組可 join 的鍵、（ii）正式讀寫路徑、（iii）該欄的回归節點，而且**必須另外批准執行 migration**。本輪不做推測性加欄，也不假稱 `decision_records` 上的驗證已涵蓋它——它沒有。

## 第四輪後半段（2026-11-18 17:22–17:49 HKT）：#195 的正式客戶路徑已寫成程式並跑成實測

上一節把「儲存契約」交清；本節把它落成客戶能用的路徑：**正式 predict 產生並持久化 `inference_id` → 正式 decision 入口寫入 → 正式查詢讀回 → 適用的決策匯出**，全部走 HTTP API，不走直接 SQL、不人工 seed、不由代理代為同意。此節只補既有主記錄，不另建平行摘要檔。

### 一、受測內容身份（一個身份，本節所有批次共用）

| 量測 | 值 |
|------|-----|
| HEAD / branch | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` / `live1-impl` |
| 本節正式身份（`find backend/app backend/tests -name '*.py' \| sort \| xargs shasum -a 256` 的彙總 sha256） | `a22029084ce795f246c4b1470d74d3b8a099dcf2814dc54ec713f98761833fe4`，**439 檔** |
| lane 自己的 tree-digest（`run_tests.sh` 的口徑，範圍不同） | `2e51db144098af12a7519879257b97aa83f6ccee1919e257ebf9d9b932073cf6` |
| porcelain | 197 paths（135 M ＋ 62 ??），sha256 `2586e142865681b365ae4da148c7f19d83cc77d8080a6666b411f7cbc66c9b5b` |

438→439 的差额就是我新增的一支 `backend/tests/test_decision_history_export.py`。195→197 的差额：`+1 ??` 是該新模組；`+1 M` 是本窗口新動到的 `frontend/src/app/paper-portfolio/[id]/page.tsx`（`frontend/src/lib/api.ts` 在前一窗口既已 M）。前一節（r3headg）只存了 paths 的**個數**沒存清單，所以這筆歸因是依本窗口四支檔案的 mtime（17:30:26／17:30:59／17:31:32／17:32:28）與現測清單推得，不是與 r3headg 逐行比對得來——要把 diff 做到逐行可核對，需要保存清單而非計數。

本窗口內同一身份被三次量到，用以證明「修改前後不混為一談」：

| 身份 | 時間 | 該身份下跑過的批次 |
|------|------|--------------------|
| `e76a5c1c0f6fa2d6f3b5b999c779a911cb2e1ff61d38e5c4fc69995b39fc7be5` | 17:22–17:25 | `r4dhx1`(1P)、`r4dh2`(6P)、`r4aff1`(124P)、`r4imp1`(51P) — **已被後續修改取代，不當作本節證據** |
| `85c2700079627a82abbeb36e47a2867a7e3fa0a41e529bf0be176b76db02758a` | 17:27 | `r4dh3`(7P) — 加入第 7 節點後、強化第 3 節點前 |
| `a22029084ce795f246c4b1470d74d3b8a099dcf2814dc54ec713f98761833fe4` | 17:30 起至今 | `r4dh4`、`r4aff2b`、`r4aff1b`、`r4imp1b`、`r4collect`、六次探針 — **本節引用的一律是這一列** |

前兩列的 `r4aff1`／`r4imp1` 是在最後兩次測試改動之前跑的，因此**不把它們的數字算進本候選**：受影響的路徑在 `a22029…` 身份下重跑為 `r4aff1b`（124 passed）與 `r4imp1b`（52 passed）。`r4imp1`→`r4imp1b` 的 51→52 正是新增第 7 節點，同一批同一身份，不是拼接。

### 二、相對 HEAD 的必要 diff（實測 diffstat，非自述）

`git show HEAD:backend/app/api/paper_portfolio.py` 對 `DECISION_HISTORY_COLUMNS`／`decision-history` 的出現次數是 **0**，工作複本為 **6**——也就是整套決策匯出面目前是**未提交的程式**，必須隨包保存。

| 檔案 | 相對 HEAD | 新增位置（現檔行號） |
|------|-----------|----------------------|
| `backend/app/api/paper_portfolio.py`（`e6e60c74…`，1841 行） | ＋395／−28（`git diff --numstat`；匯出主體在 hunk `@@ -1474,0 +1612,230 @@`，即 230 行新面） | 共用範圍 `_decisions_in_scope:1075-1089`；`DECISION_HISTORY_COLUMNS:1622`；`_INFERENCE_METADATA_COLUMNS:1655`；`DECISION_HISTORY_COLUMN_SEMANTICS:1662`＋`:1692`；`_decision_history_rows:1698`（`:1711-1716` 以 typed `UsageRecord.id.in_` 批次讀回、`:1737-1742` 寫 `source_state`）；路由 `@router.get("/{portfolio_id}/decision-history"):1800`，CSV 走 `_journal_csv(rows, DECISION_HISTORY_COLUMNS):1835`，JSON 於 `:1826-1833` 附 `columns`／`column_semantics` |
| `backend/tests/test_decision_history_export.py` | **新檔**，561 行，`2d4fca7aff64585e885a343ddec515cdc07f2d2cb6c11cca42d078118576e4f7` | 7 個節點：`:220`、`:284`、`:320`、`:351`、`:417`、`:450`、`:516` |
| `frontend/src/lib/api.ts`（`3da06a28…`，1240 行） | ＋95／−10（含前窗口的 `skill_unit_id` 接線） | 本節新增 `decisionHistoryCsv:467-468`、`decisionHistoryJson:469-471`，與既有 `journalCsv:465`／`journalJson:466` 並列 |
| `frontend/src/app/paper-portfolio/[id]/page.tsx`（`f4e5d944…`，932 行） | ＋45／−0 | `Decision.linked_inference_id:84`；`decisionDownloading:134`；`handleDownloadDecisionHistory:270-289`；按鈕 `:388-400`（`data-testid="pp-download-decision-history"`）；來源列 `:912-921`（`data-testid="pp-decision-inference-source"`） |
| `backend/migrations/versions/c3d4e5f6g7h8_add_decision_inference_link.py`（`1fe23d83…`，114 行，**未追蹤、從未執行**） | 僅文件字串 | `:9` 由「foreign-key column」改為「reference column … not a schema foreign key」；**無 op 變動** |

交易 journal 的欄位語義未被改動：`JOURNAL_COLUMNS:1329`、`JOURNAL_COLUMN_SEMANTICS:1392`、`export_trade_journal:1568` 仍用 `_decisions_in_scope` 之外的訂單／成交主軸（`:1591` 只是把決策邊接上同一範圍函式）。`paper_portfolio.py` 的 sha 在 17:22→17:48 的每一次量測都是 `e6e60c74…`，本窗口對它只做了探針級暫時改寫與原樣還原（見第五節）。

### 三、七個節點各自把 brief 的哪一條跑成實測

命令一律是 `tests/test_decision_history_export.py`（`/tmp/r4dh4`，7 passed，exit 0，內容未漂移）。

| # | 節點（行號） | 證明的客戶能力 | 對應 brief clause |
|---|--------------|----------------|--------------------|
| 1 | `test_three_kinds_of_decision_all_reach_the_customer_export`（:220） | 有訂單、無訂單、無推論的人類／規則決策**都在同一份決策匯出裡**，且 `inference_source_state` 分别为 `present`／`none` | 「測試有訂單、無訂單、無推論的人類／規則決策」 |
| 2 | `test_csv_and_json_of_the_decision_file_describe_the_same_rows`（:284） | 決策檔的 CSV 與 JSON 描述**同一批列**，CSV 不取代 JSON | 「使用既有正式決策查詢／匯出契約……不默默改變原 CSV」 |
| 3 | `test_two_inferences_in_one_skill_keep_their_own_sources`（:320） | 同一技能帳號兩筆推論決策都要 201，各自讀回自己的 `linked_inference_id`、各自 `rationale`，且第二筆以 `AS_OF_DATE − 1 天` 推論、逐列 `inference_as_of_date` 不同 | 「同技能兩筆推論」＋去重約束實測 |
| 4 | `test_switching_the_selected_model_leaves_recorded_sources_alone`（:351） | 全正式路徑：`trained` 兩支真 artifact → `POST …/select` 閘門 → 正式 predict → 正式 decision → 換模型 → 再 predict／再 decision；**已記錄那一列的來源整列不變**（`rows[first] == before`） | 「切換所選模型後歷史來源不漂移」 |
| 5 | `test_the_export_answers_for_one_account_and_one_owner`（:417） | 帳號隔離＋技能隔離：別帳號、別业主都讀不到這筆決策與該檔內容 | 「驗證帳號及技能隔離」 |
| 6 | `test_a_retired_source_still_exports_its_id_and_says_what_is_gone`（:450） | 來源列已被清掉時，id **照樣匯出**且 `inference_source_state="missing"`、来源事实不再冒充——把上節的「寫入時已驗證 ≠ 讀取時仍存在」變成產品欄位 | 「客戶應能取得無訂單決策及其可用的 inference 來源」 |
| 7 | `test_unattributed_history_exports_for_its_owner_and_nobody_else`（:516） | `paper_portfolio_id IS NULL` 的既有決策：本人兩個入口都看得到且標 `decision_scope="unattributed"`，同主另一帳號也看得到，外人看不到 | 「NULL scope 不自動涵蓋任何具體技能」＋有綁定策略版本的舊列保護 |

第 7 節點需要一筆「歸屬前」的舊列，只能以 ORM 直接造（`_add_unattributed_row`）；這是**製造遺留資料的手段**，不是交付給客戶的路徑，節點內已標注。

### 四、批次帳本（命令／身份／原始輸出／exit／skip 各自保留）

命令模板（`/tmp/r3run.sh`，每批自己的 run dir）：

```bash
bash /tmp/r3run.sh <run-dir> <args-file>
# 內部：DATA_PROVIDER=demo ALETHRA_TEST_DB="sqlite:////tmp/<run-dir>/lane.db" \
#       ~/.local/bin/uv run python -m pytest $(cat args) -q -p no:warnings -rs
```

| run dir | 窗口（HKT） | 選取 | 原始摘要 | exit | 身份 | 漂移 |
|---------|-------------|------|----------|------|------|------|
| `r4dh4` | 17:30:32→17:30:43 | `tests/test_decision_history_export.py`（7 nodes） | `7 passed in 7.24s` | 0 | `a22029…` | CONTENT_UNCHANGED |
| `r4aff1b` | 17:43:40→17:44:59 | journal／lineage 6 檔（`test_paper_journal_csv_export`、`test_paper_decision_journal_scope`、`test_decision_lineage`、`test_journal_api`、`test_human_decision_recording`、`test_paper_portfolio`） | `124 passed in 75.64s` | 0 | `a22029…` | CONTENT_UNCHANGED |
| `r4imp1b` | 17:45:00→17:45:49 | 決策匯出＋技能授權＋成功推論＋個人策略學習 4 檔 | `52 passed in 46.76s` | 0 | `a22029…` | CONTENT_UNCHANGED |
| `r4aff2b` | 17:33:47→17:35:02 | 手動決策／來源鏈 6 檔 | `111 passed, 3 skipped in 71.36s` | 0 | `a22029…` | CONTENT_UNCHANGED |
| `r4collect` | 17:36 | 全套件 `--co`（見第七節） | `3759 tests collected in 3.52s` | 0 | tree `2e51db14…` 前後相同 | 未執行任何節點 |

`r4aff2b` 的 3 條 skip 逐行（`-rs` 原始輸出，理由同一種）：`tests/test_manual_decision_books_linked_portfolio.py:1988`、`:2034`、`:2099` — 「row-lock serialisation requires a real PostgreSQL backend」。這是 PG-only 併發分支，屬於 **NOT RUN**，不是本 lane 的 PASS。

不同批次不相加為「唯一通過總數」：`r4dh4`(7) ⊂ `r4imp1b`(52) 有重疊，`r4aff1b`/`r4aff2b` 又各自覆蓋不同檔案。要一個頭條數字就必須用整條 lane（第五／七節說明其帳本尚未閉合），不能用四批相加。

### 五、變異探針：把「有地方寫」升級成「壞了會有人發現」

腳本 `/tmp/r4mut2.sh`：量測目標檔 sha → 備份 → 施加**單一肢體**破損 → 跑指定節點 → 原樣還原 → 重新量測**整個 439 檔 registry**。每次都是 `RESTORED_IDENTICAL e6e60c7464090cc9dd6fa0466011564cd5dcdc03f85ad8e8cf4223d474f4d851` ＋ `REGISTRY_UNCHANGED a22029084ce795f246c4b1470d74d3b8a099dcf2814dc54ec713f98761833fe4`，exit 一律 1（探針的目的就是讓節點失敗）。

| run dir | 破損的肢體（`2_mutation.txt` 原文） | 結果 | 發現它的節點 |
|---------|-------------------------------------|------|--------------|
| `r4q1` 17:47:33→17:47:47 | P1：拿掉共用決策範圍裡的 unattributed（NULL）分支 | 2 failed, 9 passed | `test_paper_decision_journal_scope.py::TestUnattributableHistoryStaysVisible::test_a_decision_recorded_before_attribution_is_still_listed`（「history must not vanish after the migration」）＋ `test_unattributed_history_exports_for_its_owner_and_nobody_else` |
| `r4q2` 17:47:47→17:47:58 | P2：`inference_source_state` 不讀回就宣稱 `present` | 1 failed, 6 passed | `test_a_retired_source_still_exports_its_id_and_says_what_is_gone` |
| `r4q3` 17:47:58→17:48:10 | P3：交易 journal 的 CSV 改渲決策匯出的欄位集 | 3 failed, 14 passed | `test_csv_headers_and_content_type`、`test_csv_per_column_values_for_a_fill`、`test_csv_and_json_describe_the_same_rows` |
| `r4q4` 17:48:10→17:48:21 | P4：每筆決策都去解析**最新**一次推論，而非自己那一筆 | 2 failed, 5 passed | `test_two_inferences_in_one_skill_keep_their_own_sources`、`test_switching_the_selected_model_leaves_recorded_sources_alone` |
| `r4q5` 17:48:21→17:48:31 | P4（同一破損重跑一次，作重複量測） | 2 failed, 5 passed | 同上 |
| `r4q6` 17:48:32→17:48:42 | P5：共用決策範圍不再過濾 owner | 1 failed, 6 passed | `test_unattributed_history_exports_for_its_owner_and_nobody_else` |

三件事必須一起講，否則這張表會比實際上好看：

1. **五種破損、六次執行**——`r4q4`／`r4q5` 是同一 mutation 的重複量測，不是第六種保護。
2. **P3 的意義**：決策匯出是新檔，不是把列塞進交易 journal；把兩者混欄會同時弄壞交易 journal 的三個節點，這條邊是**被測試擋住**的，不是被我聲稱的。
3. **P4 的改進是可量測的**：第一輪探針（`r4p4`，身份 `e76a…`，17:29:12→17:29:23）在當時的測試內容下只有 1 個節點發現；把第 3 節點改成「第二筆在不同的 `as_of` 日期推論」之後（`r4q4`，身份 `a22029…`）變成 2 個節點發現。上一輪我在 M9 位置承認「缺能區分該分支的行為斷言」，這一輪是補上後重新測量，不是改口。

**自我糾正（帳本完整性）**：第一輪探針 `r4p1…r4p5` 的 `restore.txt` 在 17:37 被我後續一支診斷指令以 `head …/content_drift.txt` 的 redirect **覆寫**成錯誤訊息，原本的 `RESTORED_IDENTICAL` 字串在那六個檔案裡已不存在。該輪的還原仍可獨立核對：六個 `target_before.txt` 全是 `e6e60c74…`、`target_mutated.txt` 各自不同，而夾住它們的 `r4dh4`（17:30:32，P1–P5 之後）與 `r4aff2b`（17:35:02，P4b 之後）registry 都記錄同一 sha——等於前後兩次量測把還原證據補齊。為免只靠重建的推論，本節改採**第二次完整跑**（`r4q*`）並把證據寫進不會再被覆寫的檔名（`4_restore_proof.txt`）。

### 六、前端接線與它的邊界

`npx tsc --noEmit` → `tsc_exit=0`；`next build` → `next_build_exit=0`，窗口 17:32:53→17:33:12 HKT，`ƒ /paper-portfolio/[id] 9.29 kB / 114 kB`。UI 端做了兩件事：下載按鈕（走 `decisionHistoryCsv`，Blob 存成 `decision-history-<portfolioId>.csv`）與決策列上的「Model run this decision names: <id>」／「No model run named — this was decided without a personal-model estimate.」。

邊界照 brief 講死：**build PASS 不等於正常客戶流程完成**。節點點擊、檔案實際落地內容、CSV 在瀏覽器里的樣貌都屬**瀏覽器journey**，本輪未執行（需服務／瀏覽器啟停批准）。以上只是「編譯得過、路由存在、handler 接上正式 API」。

### 七、lane 帳本（manifest）的真實後果：實測分解，不是聲稱

`ALETHRA_RUN_DIR=/tmp/r4collect DATA_PROVIDER=demo bash scripts/run_tests.sh sqlite --co` → exit 0，`3759 tests collected in 3.52s`，`tree-digest-before == after == 2e51db14…`。`--co` 不執行任何節點，因此 `scripts/run_tests.sh:491` 印出「collect-only run: nothing executed, so no ceiling is judged and no ledger closes.」——**這條路徑不可能閉合帳本**，也不可能產生可 pin 的 run dir。

以節點 id 逐行做集合差（`backend/tests/collected_nodes.txt` 釘選 3548 條 vs 現測 3759 條，全部行皆不重複）：

* **added = 222**，其中 **7 條**是 `tests/test_decision_history_export.py`（本節的新增），另外 **215 條**屬平行 session／先前窗口未重新釘選的檔案，依檔案計數前列：`test_simulation_daily_tick.py` 37、`test_decision_lineage.py` 26、`test_auto_collection.py` 24、`test_model_activation.py` 22、`test_learning_scheduler.py` 20、`test_paper_create_engine_consistency.py` 19、`test_successful_inference.py` 14、`test_skill_unit_ceiling.py` 10。
* **removed = 60**，集中在 `test_fb17_dataset_provenance.py` 17、`test_c1_c4_complete.py` 11、`test_e2e_shared_metadata.py` 9、`test_learning_scheduler.py` 8、`test_c1_c4_safety_cases.py` 4、`test_c1_c4_http.py` 4、`test_c1_c2_concurrency_isolated.py` 3。這些檔案目前仍在 repo 且能 collect，差的是**類名層級的 id 字串**（例：釘選 `TestCoverageShowsEveryStoredDataset::…`，現測 `TestStoredBarsRequestStrictness::…`）——是平行 session 重寫了那些模組，不是我的改動。
* **撤回我自己上一則回報的「removed = 11」**：那個數字是用 `^[a-z_/]*\.py::` 抽 id，靜默漏掉路徑含數字的檔案（`test_fb17…`、`test_c1_c4…` 等 49 條），把它們誤計為消失。改用 `^[a-zA-Z0-9_/]*\.py::` 重測即為上表的 222／60。錯誤在我的抽取式，不在測量對象。

**重新釘選的入口沒有被我繞過**：`backend/scripts/run_evidence.py:23` 規定 `pin <run dir> --manifest-out M --budget-out B [--ack-diff SHA]`，`:518` 在缺少 `--ack-diff` 時直接 refused，而可用的 run dir 必須來自**有執行且帳本閉合**的整條 lane。因此本節不動 `collected_nodes.txt`，也不手改生成檔；要收掉這 222／60，需要一次批准的整條 sqlite lane 執行，再以 `pin --ack-diff <上表 diff 的 digest>` 落檔。

### 八、本節未執行（NOT RUN，理由各自不同）

1. `alembic upgrade`／任何 migration 執行：無已批准且已檢查的 PG 目標；`c3d4e5f6g7h8` 仍未在任何庫上跑過。
2. PG 上的 replay／row-lock／交易安全：即 `r4aff2b` 的 3 條 skip；SQLite 不能替代（`tests/conftest.py` 明確 `PRAGMA foreign_keys=OFF`，schema 由 `Base.metadata.create_all` 產生）。
3. 瀏覽器點擊的客戶journey（登入 → 選模型 → 推論 → 記錄決策 → 下載兩份 CSV）。
4. 真實定時自發觸發的每日服務（屬 #187，下一節處理）。
5. 既有歷史列的改寫：本節只新增讀出口，未 UPDATE 任何舊列。
6. live daemon 啟動、外呼 provider、對實際帳號啟用扣額。

### 九、保存範圍（第四輪後半段新增；待代理交付，不是待 owner 批准）

前一節的 15 支清單仍然有效，本節在它之上**增加 3 支**（逐檔 `git add`，不用 `git add -A`；本窗口未 stage、未 commit、未 push）：

| 類別 | 檔案 |
|------|------|
| 節點（新） | `backend/tests/test_decision_history_export.py`（??） |
| 前端正式呼叫 | `frontend/src/lib/api.ts`（M） |
| 前端入口 | `frontend/src/app/paper-portfolio/[id]/page.tsx`（M） |

依賴與排除：匯出的服务端實現在 `backend/app/api/paper_portfolio.py`（已列在前一節的讀取端契約），其身份 `e6e60c74…`；**未包含**任何 `/tmp/r4*` run dir、`lane.db`、`.next` 建物、生成檔 `backend/tests/collected_nodes.txt`（依第七節，它只能由批准的整條 lane 以 `pin --ack-diff` 重釘）。此表是待代理交付的保存清單，不是待 owner 批准的程式裁決。

## 第四輪收尾（2026-11-18 18:39–19:03 HKT）：#187 每日模型估計——預設關閉的時鐘、共用同一道閘、持久去重、拒因與 UI 讀回

前一節的 NOT RUN 第 4 點（「真實定時自發觸發的每日服務，屬 #187」）在本節閉合其**可離線證明的部分**。這一條 lane 現在有：預設關閉、逐技能明示啟用、可撤回、只產生估計、與手動推論**共用同一個 `_run_personal_prediction` 閘門**、以 (owner, skill, window) 持久去重、拒絕時記錄原因與拒於哪一道門、以及業主讀得回的狀態面板。全部在 `/tmp/r3run.sh` 的凍結內容口徑下跑成實測，並用五支變異探針把「有實作」升級成「壞了會被發現」。

### 一、受測內容身份（後端一個、前端一個，各有取代關係）

| 量測 | 值 |
|------|-----|
| HEAD / branch | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` / `live1-impl`（本節未動 Git） |
| 後端身份 `find backend/app backend/tests -name '*.py' \| sort \| xargs shasum -a 256` 的彙總 | `1b34bae70af89a6a14dd3cccbda7b7f7da1ea3122a7ec501f6fde4f7a9337816`，**441 檔** |
| 前端身份 `find src -type f \( -name '*.ts' -o -name '*.tsx' -o -name '*.css' \)` 同口徑（自 `frontend/`） | `0f660d591afc8a2cca6280da82b785d4d05a10291fa7b4ec484553e5bbca7084`，**113 檔** |
| porcelain | 202 paths（137 M ＋ 65 ??），sha256 `83ee89fcedf439d7366437c89b6181ef8ccac57b7d98dd61ab1e4b6696cf93a4` |

後端身份 `1b34bae7…` 是 r5a–r5d 四批的共同身份，也是五支探針全部還原後的重測量（19:03:xx 再測同值、porcelain 同值）——所以四批與探針講的是同一份內容，不是拼接。

前端有**兩個**身份，必得分開：`0abde88e…`（18:50，含三個 defect 的舊內容，只對應 tsc 的 2 條錯誤輸出）與 `0f660d59…`（18:55，修好後）。本節引用的 tsc／build PASS 一律是 `0f660d59…`；`0abde88e…` 保留為歷史，其數字不套用至不同候選。

**自我更正（測量口徑，非受測物）**：18:56 我一度判讀「後端內容已漂移」（現測 `c3d330d1…` ≠ 釘選 `1b34bae7…`）。實為兩次的**路徑前綴**不同——一次自 `backend/` 產生 `app/…`，一次自 repo root 產生 `backend/app/…`；改成同一起點後逐檔 diff 為 **0 行**。路徑字串本身就是身份的一部分，彙總Digest 不可跨口徑比較。

### 二、本節改動的內容（歸因用 HEAD 出現次數實測，不用自述）

`git show HEAD:<f> | grep -c "daily_estimate\|daily-estimate"` 對比工作複本同一計數：

| 檔案 | Git 狀態 | HEAD→工作（計數） | 本節的角色 |
|------|----------|-------------------|------------|
| `backend/app/services/daily_estimates.py` | `??` 新檔，948 行，`57f94858…` | 0 → 全檔 | lane 本體：三種同意各自的 `purpose` 列（`personal_daily_estimate`）、`estimate_window`、`_estimated_this_window:383-413`、`_estimate_one:466`（含 `:506` 的新鮮度預檢與 `:515` 的 ceiling）、`run_daily_estimate_tick:752`（`:780` 指定 `KillSwitchScope.inference`）、`daily_estimate_status:838` |
| `backend/tests/test_daily_model_estimate.py` | `??` 新檔，1,214 行，`d17d8222…` | 0 → 全檔 | 19 個節點（第三節） |
| `backend/app/api/personalization.py` | `M` ＋339／−2 | 0 → 35 | 五條正式入口：PUT `:301`、DELETE `:361`、GET consent `:409`、GET status `:419`、POST run `:448`；共用讀回 `_daily_estimate_status:480` 與 `_scope_statuses:499` |
| `backend/app/workers/learning_scheduler.py` | `M` ＋1038／−248（**累計**，含先前單元與平行 session） | 0 → 3 | 只有 `:49-51`（import）、`:173-181`（`id="daily_model_estimate"`、`CronTrigger(hour=5, UTC)`）、`:183-191`（四條工作各自小時的開機日誌）屬本 lane |
| `backend/app/config.py`、`app/main.py`、`app/api/personal_models.py` | `M` | 0 → **0** | **不屬於本 lane**：它們的 diff 全數來自其他單元，故不列入本節交付歸因（排除理由＝計數，不是印象） |

本節對服務端的唯一程式修改是 `daily_estimate_status` 的掃描解耦（`:894-908`）：最後估計、最後拒因、最後一次執行現在各自獨立掃描。**這是被測試抓出來的**——舊寫法（只讀最新一列）讓「今天被拒絕」抹掉了「昨天已給出的估計」，node 7 以 `TypeError: NoneType not subscriptable` 失敗（`/tmp/r5est/run1.log`；該輪是迭代除錯，**未留 441 檔 registry**，因此只有日誌本身可核，不與 `1b34bae7…` 混述）。我改的是實作，不是斷言。

### 三、19 個節點各自把 brief 的哪一條跑成實測

命令一律 `tests/test_daily_model_estimate.py`（`/tmp/r5a`，19 passed，exit 0）。

| # | 節點（行號） | 證明的客戶能力 | 對應 brief clause |
|---|--------------|----------------|--------------------|
| 1 | `test_the_clock_is_off_until_a_skill_is_named`（:249） | 沒有 consent 列＝預設關閉，服務不服務任何人、不寫帳本 | 「實作預設關閉」 |
| 2 | `test_only_a_password_session_can_open_the_daily_clock`（:278） | 代理金鑰打不開每日鐘（PUT 需 human provenance） | 「使用者不能靠…代理代為同意完成旅程」 |
| 3 | `test_the_scope_reads_back_exactly_what_was_named`（:323） | 聲明什麼就讀回什麼（含 instrument 清單、ceiling） | 「逐技能明示啟用」 |
| 4 | `test_a_scope_naming_nobody_elses_skills_writes_nothing`（:385） | 指名他人技能：整筆請求不落地，且與「不存在」無法區分 | 帳號／技能隔離 |
| 5 | `test_training_consent_and_daily_consent_are_different_acts`（:406） | 訓練同意、模型選擇、每日執行同意是三件事；撤回其一另一保持 | 「三件事」＋`training_consent_untouched` |
| 6 | `test_one_tick_estimates_through_the_gate_and_the_owner_can_read_it`（:462） | 一次 tick 經正式閘門產生估計，業主讀得回；`jobs` 逐欄位相等，且 config **不含** `scheduled_for` | 「HTTP 與 worker 共用真的推論服務」 |
| 7 | `test_a_second_tick_in_the_same_window_bills_nothing`（:555） | 同 window 第二次 tick：0 新估計、`already_estimated_this_window` 具名拒絕、**中途新增的第二個技能仍被服務**（去重物件是 skill 不是 owner），換 window 後照常 | 「持久的 owner／skill／排程窗口去重」＋「測試重試、同日切版」 |
| 8 | `test_a_model_switch_cannot_rebill_the_window_it_already_estimated`（:612） | 換模型／artifact 後不得沿用不相符的啟用範圍再扣一次 | 「模型或 artifact 切換後不得沿用不相符的啟用範圍」 |
| 9 | `test_the_inference_switch_stops_the_lane_and_the_execution_switch_does_not`（:665） | `inference` 閘停止本 lane；`execution` 閘不波及 | 「kill switch 生效…拒絕並記錄原因」＋門閥選對 |
| 10 | `test_a_kill_switch_that_cannot_be_read_stops_the_tick`（:702） | 開關**讀不到**時停止，而不是當成沒開 | 「生效或**不可讀**時拒絕並記錄原因」 |
| 11 | `test_a_paused_skill_is_refused_by_name_not_skipped_in_silence`（:718） | 暫停的技能被**具名拒絕**，不是無聲跳過 | 「技能暫停／撤回…拒絕並記錄原因」 |
| 12 | `test_an_exhausted_quota_refuses_before_spending_anything`（:749） | 額度不足：先在花錢之前拒絕，並留下原因 | 「額度不足…拒絕」＋「自動消耗 ai_usage 的政策未批准前，不在實際帳號執行」 |
| 13 | `test_stale_data_is_refused_before_the_quota_is_spent`（:777） | 有声明 ceiling 且實際 123 天 > 3 天 → 拒絕且 detail 帶天數；**未**声明 ceiling → 服務照樣跑但 `data_age_days=124` 作為揭露（非拒絕） | 「資料過期時拒絕」＋「只產生估計」的誠實邊界 |
| 14 | `test_a_skill_with_no_selection_is_refused_not_inherited`（:812） | 沒有 skill 級選擇時不沿用別處默認：`strategy_scope_missing` 具名拒絕 | 「每次執行檢查 owner、skill、…selection」 |
| 15 | `test_the_http_door_and_the_service_report_the_same_refusal`（:845） | 同一個拒絕，HTTP 按鈕與服務 tick 給出**同一個 reason code**（不另製一套授權） | 「不得偽造 Request、繞過檢查或複製一套授權」 |
| 16 | `test_the_manual_button_and_the_clock_write_the_same_estimate`（:882） | 同一閘門、同一寫入；計數不對稱被量出來：按→1、按→2、tick→3、tick 同窗→**3**＋具名拒絕、按→4 | 「『有地方寫』不等於有建議 feed 或持久去重」 |
| 17 | `test_an_estimate_writes_no_order_no_decision_and_no_replay_step`（:928） | 兩技能各一筆估計後：`PaperOrder` 0、`DecisionRecord` 1（且是**人**記錄的那筆）、交易 journal JSON `records==[]／row_count==0`、CSV 只有 header 一行 | 「證明交易 journal 不因新估計多出虛構交易」 |
| 18 | `test_one_owner_cannot_see_or_serve_another_owners_skill`（:1016） | 乙業主的 status／jobs／estimates／reservations 全為空，id 集合以字串化比對 | 「驗證帳號及技能隔離」 |
| 19 | `test_the_estimate_tick_starts_no_other_lane`（:1060） | 三個兄弟函式 spy 未受呼、`learning_jobs.job_type` 集合只有 `daily_model_estimate`、`MarketDataBar` 跑前＝跑後計數 | 「確認 scheduler 啟動不會連帶啟用其他研究、collection 或外呼工作」 |

第 19 節點的 `MarketDataBar` 前後計數是把「順路沒有收集資料」變成**一次量測**，而不是只講 job_type。

### 四、批次帳本（命令／身份／原始輸出／exit／skip 各自保留）

命令模板同前節（`bash /tmp/r3run.sh <run-dir> <args-file>`，內部 `DATA_PROVIDER=demo ALETHRA_TEST_DB="sqlite:////tmp/<run>/lane.db" ~/.local/bin/uv run python -m pytest $(cat args) -q -p no:warnings -rs`）。四批身份全部 `1b34bae7…`，`content_drift.txt` 全部 `CONTENT_UNCHANGED`。

| run dir | 窗口（HKT） | 選取 | 原始摘要 | exit |
|---------|-------------|------|----------|------|
| `r5a` | 18:39:39→18:39:59 | `tests/test_daily_model_estimate.py`（19 nodes） | `19 passed in 16.43s` | 0 |
| `r5b` | 18:39:59→18:41:36 | `test_learning_scheduler`、`test_simulation_daily_tick`、`test_auto_collection`、`test_learning_api`（共用 tick body 的受影響路徑） | `98 passed in 93.46s (0:01:33)` | 0 |
| `r5c` | 18:41:41→18:43:16 | `test_successful_inference`、`test_personalization`、`test_personal_strategy_learning`、`test_personal_journey`、`test_personal_model_comparison`、`test_personal_comparison_validity`、`test_personal_model_permission_matrix`（共用推論閘門與三種同意） | `93 passed, 1 skipped in 91.90s (0:01:31)` | 0 |
| `r5d` | 18:43:17→18:45:22 | `test_decision_lineage`、`test_decision_history_export`、`test_decision_confirmation_status`、`test_decision_provenance`、`test_formal_skill_unit_authorization_endpoint`、`test_manual_decision_books_linked_portfolio`、`test_agent_strategy_slice`（來源鏈與技能授權邊） | `150 passed, 3 skipped in 121.73s (0:02:01)` | 0 |

4 條 skip 逐行（`-rs` 原文，全部是 PG-only 分支，屬 **NOT RUN** 而非本 lane 的 PASS）：

* `tests/test_personal_comparison_validity.py:896` — 「row locks are only enforced by PostgreSQL; SQLite cannot show them」
* `tests/test_manual_decision_books_linked_portfolio.py:1988`、`:2034`、`:2099` — 「row-lock serialisation requires a real PostgreSQL backend」

四批**不得相加**為「唯一通過總數」：`r5d` 的選取含 #195 那節已報過的 `test_decision_history_export.py` 7 節點，`r5c` 的 `test_successful_inference.py` 亦為他節已覆蓋的檔案；各批只在自己的選取內有效。

早期迭代（`/tmp/r5est/run1.log`、`run2.log`、`run3.log`）是同一節點集在**舊內容**上的失敗與修正史（`KeyError: 'scheduled_for'`、`NoneType not subscriptable`、`122→123 天`的閏年算錯、我發明的「按鈕會压制時鐘」、`TEST-SECOND-ASSET` 沒有 K 棒所以兩技能其實只有一筆、UUID vs str 集合比對、`_second_skill()` 回物件不是 id、journal 預設 CSV 卻被 `.json()` 解析、helper 在 tick 之前自己刪掉 artifact），保留為歷史，不與 `r5a` 的 19 passed 混述。

### 五、五支變異探針：把「有實作」升級成「壞了會被發現」

腳本 `/tmp/r5mut.sh` ＋ `/tmp/r5probe_edit.py`（替換式以**出現次數必須＝1**為前提， otherwise `REFUSE` 且不執行）。流程：量測目標檔 sha（須等於 `57f94858…`，否則 `ABORT`）→ 施加**單一肢體**破損 → 跑該 19 節點 → 反向还原 → 重測整個 441 檔 registry。五支的 `exit.txt` 一律 1（探針就是要它失敗），每支 `4_restore_proof.txt` 都是 `RESTORED_IDENTICAL 57f948587d6bfc4b17029ceb2ecb647de210439b2a2617b69b2235ee3015ee7d` ＋ `REGISTRY_UNCHANGED 1b34bae70af89a6a…`。

| run dir | 窗口（HKT） | 破損的肢體 | 結果 | 發現它的節點 |
|---------|-------------|------------|------|--------------|
| `r5probeA` | 19:00:30→19:00:53 | `:405` 去重不再認得排程窗口（`!= window` → `False`） | 5 failed, 14 passed | `:555`、`:612`、`:777`、`:882`、`:1016` |
| `r5probeB` | 19:00:58→19:01:18 | `:780` `KillSwitchScope.inference` → `execution`（接錯停止開關） | 1 failed, 18 passed | `:665` |
| `r5probeC` | 19:01:30→19:01:50 | `:515` 花額度之前的新鮮度預檢永不觸發 | 1 failed, 18 passed | `:777` |
| `r5probeD` | 19:02:22→19:02:42 | `:905` 狀態讀回不再附帶最後估計（＝本輪修好那處的倒退） | 5 failed, 14 passed | `:462`、`:612`、`:665`、`:777`、`:882` |
| `r5probeE` | 19:02:43→19:03:03 | `:903` 不再填 `last_refusal`（「未執行原因」消失） | 2 failed, 17 passed | `:612`、`:718` |

三件必須一起講，否則這張表比實際上好看：

1. **五支是五個不同肢體，但偵測者重疊**：A 與 D 的失敗集合交集為 `:612`、`:777`、`:882` 三支（5＋5−3＝7 支不同節點曾失敗）。不可以「5 支探針」換算成「5 條獨立保護」，也不可拿 `5+1+1+5+2＝14` 當成節點數。
2. **探針期間 `r3run.sh` 自己的 `content_drift.txt` 仍寫 `CONTENT_UNCHANGED`**——因為破損在跑之前施加、跑完之後才還原，該檔只證明「那 16 秒內內容穩定」；還原的證據是**另外**寫的 `4_restore_proof.txt`（檔名即為避免重蹈 #195 那節 `restore.txt` 被覆寫的教訓）。
3. **B 與 C 各只有 1 支節點發現**，這是對的：它們各自只守一條邊（哪一把開關能停、天花板的有／無）。真正被多支節點包夾的是去重（A：5 支）與狀態讀回（D：5 支），因為它們是客戶每天會看見的兩個事實。

### 六、UI 正式入口與它的邊界（build PASS ≠ 正常客戶流程完成）

先核對**回應形狀**（不是照自己的介面猜）：`GET /status` 無參數回 `{"skills": [...]}`（`personalization.py:443-445`）；PUT／DELETE 回 `{consent, skills}`（`:358`、`:402-406`），其 `skills` 由 `_scope_statuses:499-513` 產生，**與 GET 用的是同一個 `_daily_estimate_status:480`**；POST `/run` 回 `{job, status}`（`:473-479`）；`consent.skill_units` 每條是 `{skill_unit_id, instrument_ids, max_data_age_days}`（`daily_estimates.py:147-151`）。面板就是照這四個實物寫的。

| 檔案 | 狀態 | 內容 |
|------|------|------|
| `frontend/src/components/training/DailyEstimatePanel.tsx` | `??` 新檔，590 行，`544ecf07…` | 24 個 `data-testid`；逐帳號顯示：啟用 chip（每日執行中／未聲明（預設關閉）／未執行：<原因>）、聲明範圍與新鮮度 ceiling、`current_model_version_id`、`last_run`（window 或「手動要求」＋scope＋status）、`last_estimate`（instrument／action、`inference_id`、模型版本、artifact、`as_of`／`feature_bar_date`／`data_age_days`／provider＋`data_nature`）、版本不相符警告（`estimate_matches_current_selection`）、`last_refusal.message_zh` ＋機器碼並列、`not_for` 與聲明 |
| `frontend/src/lib/api.ts` | `M`（本節 hunk `@@ -825,4 +885,31 @@`，同檔另含他單 hunk） | `dailyEstimateConsent:887`、`grantDailyEstimateConsent:888`、`revokeDailyEstimateConsent:899`（可帶 `?skill_unit_id=`）、`dailyEstimateStatus:905`、`runDailyEstimate:909` |
| `frontend/src/app/training/page.tsx` | `M` ＋2／−0 | import ＋在 `<PersonalStrategyLearningPanel />` 之後掛載 `DailyEstimatePanel`（參數搜尋／ML 訓練／每日估計三者**分節呈現**，不是同一張表） |

本輪在前端抓出並修掉的三個自己的 defect（都改實作，不改斷言）：

1. `tsc` 老實報了兩條：`TS7006`（`any` 的 `.map` 參數未標型別）與 `TS2345`——後者是一條**會壞事的真 bug**：`setDraft((current) => ({ instruments: '', maxAge: '', ...current, [id]: … }))` 把兩個字串塞進 `Record<string, {instruments, maxAge}>`，等於讓 `draft.instruments` 變成字串。移除後 `tsc_exit=0`（18:51:16→18:52 之間，原始兩條錯誤保留在 `/tmp/r5fe/tsc_run1.log`）。
2. 寫入後只拿 `result.skills` 覆蓋清單：PUT／DELETE 只回應**範圍內**的帳號，範圍外的會從面板消失——正好弄丟 brief 要的「停用狀態」可見性。改成寫入後 `await load()` 重新讀全部帳號。
3. 兩句超出證據的文案：「手動估計不會在明日重複消耗同一窗口」與契約不符（manual 列不帶 window，被 `:882` 節點量過：按→1、按→2、tick→3），改為「手動要求不佔用今日排程窗口，定時服務在今天仍會各執行一次」；「訓練同意也未改變」——`training_consent_untouched` 只能讀出「有／沒有有效列」（`personalization.py:405`），讀不出「未改變」，故改成「訓練同意：仍有有效列／目前沒有有效列」。

閘門與原始結果：

* `NODE_OPTIONS= node_modules/.bin/tsc --noEmit -p tsconfig.json` → **exit 0**（18:53:08 修 tsconfig 副作用後重測、18:55:04 前端定案再測；`/tmp/r5fe/tsc_run3.log`、`tsc_run4.log`）
* `NEXT_DEV_DIST_DIR=.next/r5fe-build1 NEXT_TELEMETRY_DISABLED=1 node_modules/.bin/next build` → **exit 0**，18:55:1x→18:55:35 HKT，`✓ Compiled successfully`、`✓ Generating static pages (29/29)`、`/training 34.8 kB / 139 kB`；產物 `.next/r5fe-build1/static/chunks/app/training/page-8a4cf66a66958133.js`（136,664 bytes）內含 `daily-estimate-panel` 與 `scope=manual` 兩字串——證明新面板**確實被編譯進 /training**，不是掛了但沒進包的死碼；跑前／跑後 113 檔 registry 同為 `0f660d59…`（`CONTENT_UNCHANGED`）
* **共用 checkout 的邊界**：受測目錄此時有**正在運行的 `next-server (v14.2.18)`（pid 81307）**，其服務內容在 `.next`。因此本節把 distDir 指進 `.next/r5fe-build1`（仍在 git-ignored 的 `.next/` 內，不覆寫正在服務的內容），**未啟動也未停止任何服務**。
* **必須交代的副作用**：`next build` 會自行把 `".next/r5fe-build1/types/**/*.ts"` 追加進 **tracked** 的 `frontend/tsconfig.json`（Next 自己的訊息：「We detected TypeScript in your project and reconfigured your tsconfig.json file for you」）。本節兩次都以**逐行 Edit 還原**，還原後實測 `git diff -- frontend/tsconfig.json` = **0 bytes**；未使用 `git checkout --`，因為該檔的 `include` 裡有平行 session 既有的 `dev-3099`／`candidate-3100`／`e2e-3210` 等條目，整檔重置會抹掉別人的痕跡。
* 邊界照 brief 講死：**build PASS 不等於正常客戶流程完成**。瀏覽器點擊的每日服務 journey（登入 → 勾技能 → 記錄同意 → 看最後估計／拒因 → 撤回）仍是 NOT RUN（#179，需服務／瀏覽器啟停批准）。本節交出的是「編譯得過、回應形狀與路由實物一致、面板進得了 /training 的 chunk」。

### 七、本節未執行（NOT RUN，理由各自不同）

1. **真實 05:00 UTC 自發觸發**：註冊是實物——`create_learning_scheduler()`（`app/workers/learning_scheduler.py:132`）在 `:173-181` 以 `CronTrigger(hour=DAILY_ESTIMATE_HOUR_UTC, minute=0, timezone=SERVICE_TIMEZONE)`（`DAILY_ESTIMATE_HOUR_UTC=5`、`SERVICE_TIMEZONE="UTC"`）掛上 `id="daily_model_estimate"`；這個排程器由 `app/main.py:49-56` 在 API 進程的 `startup` 依 `settings.enable_learning_scheduler` 啟動，而該開關**預設 False**（`app/config.py:113`）。本節驗證的是 tick 本身（手動呼叫共用的 tick body，window 取真實時鐘的 2026-11-18 對 2024 的 K 棒），**沒有讓排程器自己響過一次，也未更改系統時間**（「不為觀察 tick 自行更改系統時間」）。所以「05:00 會被 APScheduler 自己叫起來」這條仍是 NOT RUN，不是本節的 PASS。
2. **PG 上的 replay／row-lock／交易安全**：即上表 4 條 skip（`test_personal_comparison_validity.py:896`、`test_manual_decision_books_linked_portfolio.py:1988/:2034/:2099`）。SQLite 不能證明 PG 競態安全。
3. **兩個 OS process 同一 window 重疊**：實作理由是「讀帳本而非程序旗標」（`_estimated_this_window` docstring），但跨進程同時跑的實測需要 PG＋服務啟停批准，目前 **NOT RUN**。
4. **`alembic upgrade`／任何 migration 執行**：本 lane **未新增 migration**（三種同意共用既有 `personalization_consent` 表的 `purpose` 欄，帳本用既有 `learning_jobs`）；Alembic 56／56 靜態 DAG PASS 保留但不等於 upgrade PASS（前輪已記）。
5. **對實際帳號啟用每日扣額、背景 scheduler 啟動、live daemon、外呼 provider**：現有授權不含服務啟停與受保護 DB；「自動消耗 `ai_usage` 的政策未批准前，不在實際帳號執行」。節點 19 只證明**這條 lane 自己**不牽動兄弟工作，不證明排程器在正式主機上的其他既設工作。
6. **瀏覽器客戶 journey**（#179）。
7. **既有歷史列的改寫**：本節未 UPDATE 任何舊列，也未替 #187 新增任何匯出面；交易 journal 仍是訂單／成交主軸（#195 那節的 P3 探針已把「混欄」這條邊擋住）。
8. **整條 sqlite lane 的帳本閉合與 `collected_nodes.txt` 重新釘選**：入口條件未變（需一次批准的整條 lane 執行後 `pin --ack-diff`），本節不動生成檔。

### 八、保存範圍（第四輪收尾新增；待代理交付，不是待 owner 批准）

| 類別 | 檔案 |
|------|------|
| 服務本體（新） | `backend/app/services/daily_estimates.py`（`??`，`57f94858…`） |
| 節點（新） | `backend/tests/test_daily_model_estimate.py`（`??`，`d17d8222…`） |
| 五條正式入口 | `backend/app/api/personalization.py`（`M`，HEAD 內計數 0） |
| 共用 tick 與 cron 註冊 | `backend/app/workers/learning_scheduler.py`（`M`）——**整檔**保存而非選肢：同檔混有先前單元與平行 session 的改動（＋1038／−248 累計），只挑本 lane 的 3 處會產生不可編譯的中間態 |
| 前端面板（新） | `frontend/src/components/training/DailyEstimatePanel.tsx`（`??`，`544ecf07…`） |
| 前端呼叫與掛載 | `frontend/src/lib/api.ts`（`M`，本節 hunk `@@ -825,4 +885,31 @@`）、`frontend/src/app/training/page.tsx`（`M`，＋2／−0） |

逐檔 `git add`，不用 `git add -A`；**本節未 stage、未 commit、未 push**（Git 寫入不在現有授權內）。**未包含**：`/tmp/r5*` run dir 與 `lane.db`、`.next/r5fe-build1` 建物、`frontend/tsconfig.json`（已被本節還原為 HEAD 內容）、生成檔 `backend/tests/collected_nodes.txt`。此表是待代理交付的保存清單，不是待 owner 批准的程式裁決。

---

## 第五輪（2026-11-18 19:41–20:05 HKT）：#187 的三種控制分家＋可隔離啟動、#195 儲存契約實物、#209 視窗取得權、#210 runner 等價、#211 逐 hunk 歸因

本輪交的是**同一候選上的增量**，不是新的 PASS 總數。三個問題各自直接回答：

* **客戶如何查詢／匯出無訂單決策**：靠 `GET /api/v1/paper/{portfolio_id}/decision-history` 與它的 CSV（第二節的實物清單），**不是**交易 journal；交易 journal 的訂單／成交語意未改，且有一支節點把「不因無訂單決策多出虛構訂單」量成可判定斷言。
* **每日估計如何實際隔離和停止**：隔離靠三個**互不代替**的開關（部署註冊／業主啟用／操作員即時停用，第三節）。本輪補的是第一層——`LEARNING_SCHEDULER_JOBS`，因為在補它之前，「只啟動必要 lane」在程式裡**沒有可審的入口**。
* **哪些證據已通過、哪些仍未執行**：第五節批次帳本（通過）＋第九節四個可分別批准的操作包（全部 UNKNOWN／NOT RUN）＋第十節 NOT RUN 清單。

### 一、候選身份（本輪現測，19:47 與 20:05 各量一次）

| 項目 | 值 |
|------|-----|
| git base | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`，working tree dirty |
| porcelain | 202 path(s)，sha256 `83ee89fcedf439d7366437c89b6181ef8ccac57b7d98dd61ab1e4b6696cf93a4` |
| 後端 tree-digest（本輪正式批） | `f57c951f60e20959f7af0221ea983a1dc4a973d00fc3dec928845c8009657a7a`（r209 跑前＝跑後；r205 同一值）；加 crash 節點**之前**是 `6ca1e3c9a908b191aee2c3e41670b8a8a0f404417a8776d14e93498ca2dc5355`（r208） |
| interpreter | `backend/.venv/bin/python`（3.12.16）／pytest 8.3.3／SQLAlchemy 2.0.36 |
| provider | `DATA_PROVIDER=demo`（**r209／r205 有釘**；r208 未釘，見第六節更正） |

本輪改動的六個檔案（現測行數／sha256）：

| 檔案 | 行數 | sha256（前 12） | 對 HEAD |
|------|------|-----------------|---------|
| `backend/app/config.py` | 525 | `832560e83e4a` | `M` ＋92／−0（6 hunk） |
| `backend/app/workers/learning_scheduler.py` | 1,280 | `840eadd94771` | `M` ＋1,082／−262（71 hunk） |
| `backend/tests/test_learning_scheduler.py` | 798 | `bf58dee97b91` | `M` ＋725／−121（42 hunk，27 節點） |
| `backend/tests/test_daily_model_estimate.py` | 1,472 | `9062b9c25add` | `??` 新檔（24 節點） |
| `backend/app/services/daily_estimates.py` | 984 | `3f8ba581a6b2` | `??` 新檔 |
| `backend/app/api/personalization.py` | 514 | `2bca90f20890` | `M` ＋340／−2（7 hunk） |

### 二、#195 的物理儲存契約（三張表各自的分類，不互相借證）

「查到 schema」不等於「#195 已交付」。下面每一列都指到**實物**，並把「有 DB 外鍵／只有應用層引用／根本沒存」分成三类：

| 表 | 來源鏈欄位 | 分類 | 實物位置 | 本輪現測 |
|----|------------|------|----------|----------|
| `decision_records`（`DecisionRecord`） | `linked_inference_id` | **應用層引用，無 DB FK**：`Column(UUID(as_uuid=True), nullable=True, index=True)`，註解自陳 "References usage_records.id" | `backend/app/models/paper_portfolio.py:221`（`__tablename__` `:205`） | 檔存在、欄位宣告為 plain UUID，無 `ForeignKey(...)` |
| `human_decision_events`（`HumanDecisionEvent`，模擬回放裡業主親手那筆） | **不含任何 inference 欄位** | **來源鏈未存於此表**：全部欄位為 `owner_id`／`simulation_session_id`／`idempotency_key`／`action`／`quantity`／`rationale`／`server_strategy_version_id`／`server_instrument_id`／`server_step_number`／`server_snapshot_date`／`server_data_cutoff_at`／`decision_instant`／`execution_status`／`actor_type`／`supersedes_event_id`／`metadata_json`／時間戳 | `backend/app/models/human_decision.py:27-69`（`__tablename__` `:25`） | 逐欄 `grep Column(` 清單，`linked_inference_id`／`usage_records` **0 命中**；`server_strategy_version_id` 也是 plain UUID（`:39`，應用層） |
| `usage_records`（inference 本體） | `id` 是被引用的主鍵 | **被引用端**；`inference_id` 在正式推論回應裡就是它 | `app/api/personal_models.py`（`inference_id = str(usage_record.id)`） | 前輪已記，本輪未改 |

正式寫入／讀取入口（不是 SQL、不是 seed）：

* 寫：`POST /api/v1/paper/{portfolio_id}/decisions` — `app/api/paper_portfolio.py:1030-1046` 先驗證 `linked_inference_id`（存在、同一 owner、是 personal-model 推論、且是為這個 skill 帳號產生），驗證不過就拒絕，`app/services/decision_lineage.py` 是共用的那條規則；
* 讀：`GET /api/v1/paper/{portfolio_id}/decision-history` — `app/api/paper_portfolio.py:1800-1836`，列產生器 `_decision_history_rows:1698`，`inference_source_state` 逐列回答「該 id 今天還讀不讀得回來」（`:1705-1737`），欄位自陳「deliberately no schema foreign key」（`:1672`）；
* 匯出：同一資料的 CSV／JSON 兩種，節點 `test_csv_and_json_of_the_decision_file_describe_the_same_rows`（`tests/test_decision_history_export.py:284`）。

Migration 實物（**未執行**）：`backend/migrations/versions/c3d4e5f6g7h8_add_decision_inference_link.py` 114 行，`revision='c3d4e5f6g7h8'`、`down_revision='b2c3d4e5f6g7'`（`:37-38`），`upgrade()` 三段前置檢查（表不存在→拒、欄位已存在→拒、索引已存在→拒）後才 `op.add_column(..., sa.UUID(), nullable=True)` ＋ `op.create_index('ix_decision_records_linked_inference_id', ...)`（`:86-94`）；`downgrade()` 也先驗證才 drop（`:97-114`）。它是**釘選 tail 的最後一環**：`scripts/check_migration_tail.py:54-59` 的 `EXPECTED_TAIL = ("e1f2a3b4c5d6","f2a3b4c5d6e7","b2c3d4e5f6g7","c3d4e5f6g7h8")`，且 `tests/test_migration_tail_contract.py:186` 斷言入口印出的 head 正是 `c3d4e5f6g7h8`。**「HumanDecisionEvent 需要改 schema」這一問的答案：本輪未新增欄位，因此無新 migration**；上表第三列說的是該表**目前不存來源鏈**，這是既有事實，不是本輪的裁決。

### 三、#187：三種控制分家，以及本輪補上的第一層

| 控制 | 是什麼 | 綁什麼 | 實物 | 誰能動 |
|------|--------|--------|------|--------|
| **部署：這個行程註冊哪些 tick** | `settings.enable_learning_scheduler`（總開關，預設 False）＋**本輪新增** `settings.learning_scheduler_jobs`（要註冊哪些 lane） | 綁 **lane id**（排程單位），不綁技能、不綁版本 | `app/config.py:124`（總開關，HEAD 內容）、`:130`＋`:38`＋`:181-183`＋`:202`＋`:292-314`（新增）；`app/workers/learning_scheduler.py:137-185`（`_lane_definitions`）、`:188-201`（`registered_lane_ids`）、`:203-223`（`create_learning_scheduler` 只掛被點名的）；`app/main.py:49-56` 才是 `startup` 啟動點 | 部署者／設定，**不是** owner，也**不是** 操作員 |
| **業主：這個技能要不要每日估計** | `PersonalizationConsent(purpose="personal_daily_estimate")` 的 `scope_json["skill_units"]` | 綁 **技能帳號＋標的清單＋新鮮度 ceiling**（實測欄位只有 `skill_unit_id`／`instrument_ids`／`max_data_age_days`，`app/services/daily_estimates.py:130-151`、`declared_scope:155-170`）——**沒有 version、沒有 artifact 欄位** | `get_daily_estimate_consent:107`（只認自己的 purpose，訓練同意是另一列）、`estimate_owner_ids:363`、`live_declared_ids:417` | owner 本人（PUT／DELETE 需 human provenance） |
| **操作員：立即停** | kill switch，本 lane 問的是 `inference` 那把 | 綁 **scope（全域／inference／execution／training）** | `run_daily_estimate_tick` → `_run_tick(..., kill_switch_scope=KillSwitchScope.inference)`（`daily_estimates.py:816`）→ `learning_scheduler.py:314` 的門；每技能前再次讀（`daily_estimates.py:628-632`）；`is_kill_switch_active` 先讀 `global_` 再讀該 scope（`app/services/kill_switch.py:22-37`） | 操作員 |

三件事**不可互相代替**，且現在各有實物：訓練同意是另一列（`:107` 的 purpose 過濾＋節點 `test_training_consent_and_daily_consent_are_different_acts`）；模型 selection 是 `PersonalModelVersionSelection` 那張表（`current_selection:428`，只是定位器，真正把關的是 `model_activation.check_model_version_inference_authorization`）；每日執行同意是上面第二列。**註冊第四支 tick 不等於任何人被啟用**：`_lane_definitions` 那段註解把這點寫進程式（`:173-177`），由 `test_the_clock_is_off_until_a_skill_is_named` 守住。

#### 「核對 opt-in 究竟綁技能、版本還是 artifact」＋「還未成功估計就切換模型」

* **綁技能**（上表第二列，欄位級實測）。版本與 artifact 是**每次估計當下**由 `current_selection` 重新導出，再由正式閘門二次確認；換句話說「同意」不会隨著版本漂移，也不會把舊版本釘死。
* **還沒成功估計就換模型**：不能只用「成功後同日不重算」回答，本節有兩支不同節點——`test_a_switch_before_the_first_estimate_prices_the_version_named_now`（`tests/test_daily_model_estimate.py:717`）證明首次估計用的就是**切換後那一刻**被點名的版本；`test_a_model_switch_cannot_rebill_the_window_it_already_estimated`（`:669`）證明已經估计過的視窗不因切換被重扣。兩者是不同斷言，不是一支covers兩條。
* **擴權問題**：`registered_lane_ids` 只能縮、不能放（docstring `:188-194` 寫明 "narrows the schedule and cannot widen it"，且被點名的 lane 仍要過自己的 opt-in／kill switch／owner scope）。因此本輪的改動**不構成**對新版本或新 artifact 的授權，也不產生新的扣額路徑。

#### 全域緊急停止的覆蓋（如實保留既有測試結論）

* **`inference` 開關能停本 lane；`execution` 開關停不了它**——這是既有測試的結論，本輪未改寫其預期：`test_the_inference_switch_stops_the_lane_and_the_execution_switch_does_not`（`:764`）。理由是選對門：估計是推論，不是下單。探針 `r5probeB`（前輪）把 `inference` 換成 `execution` 後正是這支節點抓到。
* **全域承諾與程式一致，且是兩道門**：`is_kill_switch_active` 先查 `global_`（`app/services/kill_switch.py:22-29`），所以 `:314` 的門與 `:628` 的每技能重讀都吃到全域剎車。節點 `test_the_global_switch_stops_the_estimate_lane_at_both_doors`（`:812`） separately 證明「tick 跳過且 0 reservation」＋「业主按鈕 503」。本輪**沒有**只改文案：程式未動，節點已存在並在本輪 r209 批內通過。
* **執行中撤回怎麼辦（明確定義）**：已在執行的估计遇撤回＝**還沒開始的那個技能不開始**，已經算完的那個不回收。實物：`test_a_withdrawal_arriving_mid_run_stops_the_skills_not_started_yet`（`:868`，被拒技能 `reason="consent_withdrawn"`，`status="partial"`，未扣額）與 `test_a_switch_pulled_mid_run_stops_the_skills_not_started_yet`（`:914`，全域剎車中途落下→`kill_switch_active`；同一視窗內放開剎車**不會**把該技能 retried，因為去重把拒絕也算成該視窗的記錄，业主拿到的是下一個視窗）。
* **讀不到設定**：不當成「沒開」，而是停下來並寫明原因——`test_a_kill_switch_that_cannot_be_read_stops_the_tick`（`:801`），程式在 `daily_estimates.py:629-632`（`stopped, stop_reason = True, "kill_switch_unreadable"`）。手動按鈕那條也一樣（`test_the_http_door_and_the_service_report_the_same_refusal`，`:1103`）。

#### 實際註冊清單（本輪量測，非文件宣稱）

命令：`backend/.venv/bin/python -c` 於 `backend/` 下**只建立**排程器物件（`create_learning_scheduler()`，**未 `.start()`**、未啟服務、未改系統時間），以 `trigger.get_next_fire_time(None, 2026-11-18T12:00Z)` 算下一次觸發：

| `LEARNING_SCHEDULER_JOBS` | `registered_lane_ids()` | 實際掛上的 job（`len(sch.get_jobs())`） | job → trigger → next fire (UTC) |
|---|---|---|---|
| `""`（未設定＝預設） | `['daily_research','daily_simulation','weekly_analysis','daily_model_estimate']` | **4** | `daily_model_estimate` `cron[hour='5', minute='0']` → **2026-11-19 05:00:00+00:00**；`daily_research` `cron[hour='2']` → 11-19 02:00；`daily_simulation` `cron[hour='3']` → 11-19 03:00；`weekly_analysis` `cron[day_of_week='sun', hour='4', minute='0']` → 11-22 04:00 |
| `"daily_model_estimate"` | `['daily_model_estimate']` | **1** | 只有 `cron[hour='5', minute='0']` → 2026-11-19 05:00:00+00:00 |
| `"daily_simulation,daily_model_estimate"` | `['daily_simulation','daily_model_estimate']` | **2** | `hour='3'` 與 `hour='5'` 兩支，**沒有** research／weekly |

生效證據（隔离設定怎樣才算生效）：`test_starting_an_estimate_only_scheduler_adds_no_other_lane`（`tests/test_learning_scheduler.py:316`）真的 `start()` 那個只點名估计 lane 的排程器（`run_scheduled_daily_estimates` 先 patch 掉，避免 due tick 做真實工作），再斷言 `get_jobs()` 仍只有 1 支——**啟動本身不會補掛其他 lane**；`test_the_estimate_tick_starts_no_other_lane`（`tests/test_daily_model_estimate.py:1318`）則從另一側證明呼叫 tick body 不牽動兄弟工作（三個兄弟函式 spy 未受呼、`learning_jobs.job_type` 集合只有 `daily_model_estimate`、`MarketDataBar` 跑前＝跑後）。兩者合起來才是 brief 那兩句：「直接呼叫 tick body 不觸發其他 lane，**不等於**啟動共用 scheduler 不會連帶排程」——現在兩邊各自有節點，不再靠推論。

設定讀取即拒（fail-fast，線程尚未存在）：實測訊息

```
'LEARNING_SCHEDULER_JOBS names unknown lanes: ['daily_model_estamate']. Known lanes: ['daily_research', 'daily_simulation', 'weekly_analysis', 'daily_model_estimate']. A name this build cannot map is refused rather than skipped…'
'LEARNING_SCHEDULER_JOBS is set but names no lane (',,,' ). Separators alone would register an empty schedule, and a host with an empty schedule reads in the ledger like a host that was never asked to run.'
```

對應節點 `test_a_lane_name_this_build_cannot_map_refuses_the_process_at_read`（`:365`）與 `test_the_narrowable_names_and_the_scheduled_lanes_are_one_list`（`:267`，把 `config.LEARNING_SCHEDULER_LANES` 與 worker 的 `_lane_definitions()` 釘成**同一份**名字，順序也釘）。

### 四、#209：視窗「取得權」與扣額提交順序（量測，不是宣稱）

* **durables 存在 ≠ 視窗已被認領**。`_open_job` 在花費任何額度**之前**就 commit 一張 `status="running"` 的帳本列（`app/workers/learning_scheduler.py:416-453`，commit 在 `:452`），但去重讀的是 `result_json`（`app/services/daily_estimates.py:384-414`），而 `result_json` 只在每業主的最後一次 commit 才寫入（`:726-739`，中途每技能一次 `db.commit()` 在 `:626`）。**所以中途死掉的 tick 不認領視窗**。
* 本輪把它從散文變成斷言：`test_a_tick_that_dies_mid_run_leaves_the_window_unclaimed`（`tests/test_daily_model_estimate.py:607`）。用 `BaseException` 子類（不是 `Exception`，否則會被每估計的 guard 吞掉）在第二個技能中途打死 tick，實測：帳本列停在 `running`、`result == {}`、已估的那 1 筆留下、**0 筆 reserved／1 筆 settled**；換回真實實作後對同一 window 再 tick，得到 **3 筆 settled**（兩個技能的一天＋已估那技能的第二天）與 `status="completed"`。也就是说：**崩潰後的同日重跑會把已估過的技能再估一次、再扣一次**，這是程式的既有語意，本節讓任何未來的改變都必須是故意的。
* **推論結果與 quota 的提交順序**（逐行讀，非推測）：`reserve_quota` 只做 `db.add` ＋ `db.flush()`，**不 commit**（`app/auth/entitlements.py:352-353`）；失敗路徑 `release_reservation` ＋ `db.commit()`（`app/api/personal_models.py` 各 `except` 分支：`FileNotFoundError`／`PermissionError`／`PersonalTrainingRefused`／`Exception`）；成功路徑 `settle_reservation` → `record_api_call_no_commit` → **單一 `db.commit()`**（`:695` 附近）。因此 **inference 列與結算是同一個交易**，不可能出現「有推論沒扣額」或「扣了額沒推論列」的中間態；而崩潰在 commit 之前時，整筆（含 reservation）一起被 rollback——本節實測的 `reserved` 計數 0 就是這條。
* 尚**未**證明的部分是跨進程的：兩條「commit 順序正確」的斷言在 SQLite 上成立，不等於 PG 的 row-lock／serialisation 成立（該三條 pg_only 節點本輪仍 skip，見第九節）。

### 五、本輪批次帳本（每批自帶命令／身份／run dir／原始輸出／exit／skip 原因；**不得相加**）

| run dir | 窗口 (HKT) | 命令（選取） | collected / 結果 | exit | 身份 |
|---------|-----------|--------------|------------------|------|------|
| `/tmp/alethra-runs/r209-sqlite-1` | 19:44→19:45 | `backend/scripts/run_tests.sh sqlite tests/test_learning_scheduler.py tests/test_config.py tests/test_simulation_daily_tick.py tests/test_daily_model_estimate.py` | **147 collected＝147 passed**（59 config＋27 lsched＋37 sim＋24 est），427 warnings，69.38s | 0 | tree-digest 前＝後 `f57c951f…`；`DATA_PROVIDER=demo`；`selection-kind: explicit-selection`；porcelain 202／`83ee89fc…` |
| `/tmp/alethra-runs/r205-sqlite-1` | 19:46→19:48 | `backend/scripts/run_tests.sh sqlite tests/test_decision_history_export.py tests/test_decision_lineage.py tests/test_decision_provenance.py tests/test_decision_confirmation_status.py tests/test_human_decision_recording.py tests/test_manual_decision_books_linked_portfolio.py tests/test_manual_decision_label_capture.py tests/test_journal_api.py tests/test_paper_journal_csv_export.py tests/test_paper_decision_journal_scope.py` | **187 collected＝184 passed＋3 skipped**，125.27s | 0 | 同一 tree-digest `f57c951f…`（内容未變）、同一 provider 釘選 |
| `/tmp/alethra-runs/r208-sqlite-1` | 19:41→19:42 | 同 r209 選取（crash 節點加入**前**） | 146 collected＝146 passed | 0 | tree-digest `6ca1e3c9…`；`provider: DATA_PROVIDER=<unset>` → **已被 r209 取代**（見第六節） |

r205 三條 skip 逐行（`-rs` 原文，全部 NOT RUN 而非 PASS）：`tests/test_manual_decision_books_linked_portfolio.py:1988`、`:2034`、`:2099` — "row-lock serialisation requires a real PostgreSQL backend"。

`run_tests.sh` 在兩批都以 `--explicit-selection` 走 `run_evidence.py` 帳本關閉（窄選取不去比對整條 lane 的 `collected_nodes.txt`），**未重新釘選** `collected_nodes.txt`（需另批批准，見第九節）。

### 六、對前輪記載的更正（本輪現測推回上文）

1. **`app/config.py` 現在屬於本 lane**。前文第 1000 行寫「`app/config.py`、`app/main.py`、`app/api/personal_models.py` … **不屬於本 lane**：它們的 diff 全數來自其他單元」。本輪在 `config.py` 落了 6 個純新增 hunk，**該句對 config.py 已不成立**，故在第七節逐 hunk 歸因；`app/main.py` 與 `personal_models.py` 本輪未改，那句對它們仍成立。
2. **第 999 行的「只有 3 處屬本 lane」已過期**。`learning_scheduler.py` 現在還多了 `_lane_definitions`／`registered_lane_ids`／`create_learning_scheduler` 重寫與 docstring 的註冊控制句（第七節）。
3. **行號引用因插入而位移**（本輪實測新值）：前文第 1096 行引 `learning_scheduler.py:132`（`create_learning_scheduler`）→ 現在 **:203**；引 `:173-181`（估計 job 的 `CronTrigger`）→ 現在 **:177-181**（在 `_lane_definitions` 內）；引 `app/config.py:113`（`enable_learning_scheduler` 預設 False）→ 現在 **:124**；`DAILY_ESTIMATE_HOUR_UTC=5` 的定義位置在 `app/services/daily_estimates.py:57`，`SERVICE_TIMEZONE="UTC"` 在 `app/workers/learning_scheduler.py:77`（前文把兩者都寫在 learning_scheduler 名下，此處更正）。
4. **節點表由 19 → 24**。前文第 997 行釘 `test_daily_model_estimate.py`＝1,214 行／`d17d8222…`／19 節點，第 1017 行引 `test_a_model_switch_cannot_rebill_…（:612）`。本輪現測：1,472 行／`9062b9c25add…`／**24 節點**，該節點現在 **:669**（＋57 的位移正是本輪 crash 節點的長度）。
5. **那 4 支非本段所寫、但本段重測並補上記載**：`test_a_switch_before_the_first_estimate_prices_the_version_named_now`（:717）、`test_the_global_switch_stops_the_estimate_lane_at_both_doors`（:812）、`test_a_withdrawal_arriving_mid_run_stops_the_skills_not_started_yet`（:868）、`test_a_switch_pulled_mid_run_stops_the_skills_not_started_yet`（:914）。時間界：**19:03:03 的 `r5probeE` 結果加總仍是 19**（該列是 `2 failed, 17 passed`；`/tmp/r5*/` 的 registry 一律是 `d17d8222…`），而 19:41 的 r208 已是 23 節點／tree-digest `6ca1e3c9…`，所以它們是在 19:03 之後進入工作樹。**本段沒有編輯紀錄可證明作者**（此 checkout 由開發與測試兩條 session 共用），因此歸因列為**待核**，不宣稱是自己寫的；能負責任地講的是：其斷言內容與 #206／#207 的 brief 條款逐條對應，本段已在 r209 批內把它們跑成 24／24 通過，並在此補上前輪表格缺少的記錄。
6. **provider 釘選**：r208 印出 `DATA_PROVIDER=<unset>`，而 `backend/.env` 设的是 `twelvedata`，即「未釘選＝實際 twelvedata」。本輪的兩批頭條（r209／r205）都**顯式 export `DATA_PROVIDER=demo`** 後重跑，r208 保留為被取代的輔助證據。

### 七、#211 逐 hunk 歸因（本輪實際待保存的增量）

**`app/config.py`：＋92／−0，6 個 hunk，可加分解已核對**（本輪 47 ＋ 其他單元 45 ＝ 92）：

| hunk（`git diff -U0`） | 新增行 | 本輪（lane 註冊控制） | 其他單元（內容／原因／出處） |
|---|---|---|---|
| `@@ -33,0 +34,11 @@` | 11 | **11 全屬本輪**：`LEARNING_SCHEDULER_LANES`（:38）＋4 行「在設定讀取時就拒」的理由 | — |
| `@@ -113,0 +125,11 @@` | 11 | **6**：`:125-129` 理由註解＋`:130 learning_scheduler_jobs: str = ""` | **5**：`:131-135` `learning_scheduler_max_owners: int = 25`（tick 扇出上限，屬前輪 owner-limit 單元） |
| `@@ -123,0 +146,6 @@` | 6 | 0 | **6**：`:146-151 paper_skill_unit_max_per_user: int = 10`（#172 技能帳號上限單元） |
| `@@ -151,0 +180,5 @@` | 5 | **5 全屬本輪**：`learning_scheduler_job_list` property（:181-183） | — |
| `@@ -166,0 +200,4 @@` | 4 | **1**：`:202 self._validate_learning_scheduler_jobs()` | **3**：`:200`／`:201`／`:203` 三個既有驗證子的接線 |
| `@@ -235,0 +273,55 @@` | 55 | **24**：`:292-314 _validate_learning_scheduler_jobs()`＋其後空行 | **31**：`:273-291`（`_validate_paper_skill_unit_limit`、`_validate_learning_scheduler_fanout`）與 `:316-327`（`_validate_auto_collection_interval`） |

**`app/workers/learning_scheduler.py`：＋1,082／−262，71 個 hunk。整檔不可拆不是整檔批准**——本輪只動 4 個位置，其餘是先前各輪（每日研究／模擬回放／週日分析／每日估計四支 tick 本身）與平行 session 的累計改動：

| 本輪 hunk／位置 | 內容 | 依賴 |
|---|---|---|
| `@@ -73,0 +137,66 @@`（新函式塊 :137-202） | `_lane_definitions()`（四支 tick 的 `(id, func, trigger, kwargs)`，含每支為什麼在那個小時的註解）＋`registered_lane_ids()` | 讀 `config.LEARNING_SCHEDULER_LANES`、`config.learning_scheduler_job_list`；引用 `DAILY_HOUR_UTC`／`SIMULATION_HOUR_UTC`／`WEEKLY_HOUR_UTC`／`daily_estimates.DAILY_ESTIMATE_HOUR_UTC` 與三個既有 callable |
| hunk 15-20（`create_learning_scheduler` 本體 :203-223） | 把「四條寫死的 `add_job`」換成「只掛 `registered` 裡的」，並讓開機日誌印**實際掛上**的 `lane@trigger`（`:215-222`） | 依賴上一列兩個函式；`_lane_definitions` 裡被搬移的四條值是**前輪內容**，本輪未改其值（位移由探針 P2／P3 證明仍有保護） |
| docstring `:12-16` | 「總開關是四支共用，所以 `learning_scheduler_jobs` 點名這個行程實際註冊哪些」一句 | 與上兩列同步；同段 `:3`、`:9-10`（第四支 tick 的列舉）屬前輪 |
| `tests/test_learning_scheduler.py`（42 hunk 中的 :32／:38-41 import、docstring 段、`:251-381`） | `_lane_allow_list` contextmanager＋6 支新節點（:267/:281/:294/:316/:334/:345/:365） | 依賴 config 的兩個符號與 worker 的三個函式；該檔其餘 hunk（含 `test_scheduler_starts_and_stops`、`:210-281` 的三 tick 契約、`:408` UTC 節點）屬先前各輪 |
| `tests/test_daily_model_estimate.py:607-668`（＋57 行、1 支節點） | `test_a_tick_that_dies_mid_run_leaves_the_window_unclaimed` | 依賴 `de._estimate_one` 可 monkeypatch、`_last_job`／`_served`／`_reservations` helpers（皆為該檔既有） |
| `app/api/personalization.py`、`app/services/daily_estimates.py` | **本輪 0 處改動** | 二者是 #187 前幾輪的本體（7 hunk／新檔），本輪只讀、只在 r209 批內重跑 |

**Git 包必須能從 base＋實際待保存 diff 重現，而不是拿共享的 202 檔快照背書**：上面三張表的 hunk 標頭可直接重現——`git diff -U0 -- backend/app/config.py backend/app/workers/learning_scheduler.py backend/tests/test_learning_scheduler.py`（＋未追蹤的 `backend/tests/test_daily_model_estimate.py` 全文）。`git status --porcelain` 的 202 檔只證明工作樹髒，**不證明**任何單檔的歸因。

### 八、#210：`/tmp/r3run.sh` 與正式 lane 入口的等價核對（結論：不等價，降為輔助）

逐項比對 `/tmp/r3run.sh`（33 行）與 `backend/scripts/run_tests.sh`（正式入口）：

| 正門有的東西 | `r3run.sh` | 判定 |
|---|---|---|
| JUnit XML 與 collect 對帳（`--junitxml`、`collect.txt`、`collected-node-ids`） | ✗（只有 `out.txt`） | 不等價 |
| skip 預算門（`skip_budget.py` census） | ✗ | 不等價 |
| 證據帳本（`run_evidence.py`，窄選取時 `--explicit-selection`） | ✗ | 不等價 |
| 身份標頭（命令／git／porcelain／interpreter／lane fingerprint／provider／selection-kind） | ✗（只寫 `window.txt`） | 不等價 |
| tree-digest 跑前＝跑後（覆蓋整個 `backend/**/*.py`） | 部分是：`content_before/after.txt` 是 `backend/app`＋`backend/tests` 的 per-file registry，能偵測內容漂移 | 這**一點**功能相當，但範圍與 fingerprint 不同 |
| run dir 獨佔（正式門拒绝已存在的目錄） | ✗（`mkdir -p`，會复用） | 不等價 |
| args-file 以未加引號 `$(cat …)` 代入 | ✗（有分詞／glob 風險；本輪 args 內容無萬用字元，故未出事） | 不等價（潛在） |

因此本輪頭條批次一律走 `backend/scripts/run_tests.sh sqlite`＋顯式選取（第五節），`r3run.sh` 的產物**只保留為「固定內容下的輔助離線證據」**，不宣稱走過正式 gate；`collected_nodes.txt` **未自行重釘**。附件：`/tmp/r3run.sh`（33 行）、`/tmp/alethra-runs/r209-sqlite-1/identity.txt`、`.../collect.txt`、`.../junit.xml`、`.../pytest.log`、`.../exit-code.txt`（r205、r208 同構）。

### 九、四個可分別批准的操作（不是全面許可；未量測者標 UNKNOWN）

| 包 | 精確參數 | 目前狀態 | 批准後的第一步（先核對，不符即停） |
|---|---|---|---|
| **A. PostgreSQL** | 隔離 host／port／DB／roles／PGDATA（皆**新**，不碰既有实例）；目標 revision 釘 `c3d4e5f6g7h8`；命令 `backend/scripts/run_tests.sh pg <本輪選取>`；完整 node ids：r209 的 147 支＋r205 的 187 支（含那 3 支 pg_only）；另加「兩個 OS process 同一 window」對照 | **UNKNOWN**（本機未啟動 PG 测试目标；未跑過 pg 層） | 先只讀核對 version／port／DB 名／roles／`alembic current` 是否等於釘選值；**不符即停**，不升級、不改角色 |
| **B. 服務與真實 cron** | 以 `LEARNING_SCHEDULER_JOBS=daily_model_estimate` 啟動一個 API 行程，觀察 05:00 UTC；停止線＝`kill` 該 pid ＋ `enable_learning_scheduler=false` | **NOT RUN**；且**本機此刻已有正在運行的 next-server（pid 81307）**，不動它 | 批准後才啟停；不啟舊 live daemon、不外呼 provider、不改系統時間 |
| **C. 瀏覽器 journey** | 一般客戶流程：登入（密碼 session）→ 選模型 → 真實 predict → 記錄決策 → 查 `decision-history` → 匯出；每日面板勾技能→看最後估計／拒因→撤回 | **NOT RUN**。**Build PASS 不代替 browser PASS**，代理亦**不可代為同意** | 需服務啟停＋瀏覽器批准（#179） |
| **D. Git 保存與選單重釘** | 逐檔 `git add`（第七節清單）＋ commit message；`backend/tests/collected_nodes.txt` 只能由一次**整條 lane** 執行後 `pin --ack-diff` 產生 | **未批准，未執行**。本輪未 stage／commit／push／merge／部署 | 批准後逐檔 stage，先 `git status` 覆核內容再 push；發現分歧或本地路徑 remote 即停 |

### 十、本輪仍未執行／仍未證明（NOT RUN，理由各自不同）

1. **05:00 UTC 由 APScheduler 自己響**：註冊清單與觸發值是**建立排程器物件**時量的（第三節表格），未啟動行程、未改系統時間。
2. **PG 上的競態**：`test_manual_decision_books_linked_portfolio.py:1988/:2034/:2099` 仍 skip；SQLite 不能證明 PG row-lock 安全，故第四節的 commit 順序結論目前只到「單進程內正確」。
3. **兩行程爭同一視窗**、**重試／失敗釋放**、**撤回或停用與執行重疊**：第三節與第四節的節點是單進程模擬（monkeypatch／同進程第二條 session）；真正的雙 process 對照在 A 包，**未批准不執行**。
4. **對實際帳號啟用每日扣額**：現有授權不含此政策；本輪所有扣額數字都发生在隔離 SQLite 檔（`/tmp/alethra-runs/*/lane.db` 之類），**未**讀過受保護 DB。
5. **交易 journal 的既有欄位語意變更**：沒有。本輪未新增虛構訂單／成交（既有節點 `test_an_estimate_writes_no_order_no_decision_and_no_replay_step`，`tests/test_daily_model_estimate.py:1186`，與 `test_decision_history_export.py:220-281` 那段「journal 不因無訂單決策多列」在本輪 r209／r205 各自跑過）。
6. **整條 sqlite lane 的帳本閉合與 `collected_nodes.txt` 重新釘選**：條件未變（需 D 包），本輪不動生成檔。
7. **前端**：本輪未動 `frontend/**`；前輪的 `DailyEstimatePanel.tsx`／`api.ts`／`training/page.tsx` 證據與 `.next/r5fe-build1` 產物仍以前輪那一節為準，未重複宣稱。

### 十一、本輪可審的保存範圍（待代理執行，不是待 owner 批准程式裁決）

| 類別 | 檔案 | 本輪 |
|------|------|------|
| 註冊控制 | `backend/app/config.py`（`M`，`832560e8…`）、`backend/app/workers/learning_scheduler.py`（`M`，`840eadd9…`） | 有改（第七節逐 hunk） |
| 註冊契約測試 | `backend/tests/test_learning_scheduler.py`（`M`，`bf58dee9…`，27 節點） | 有改（＋6 節點） |
| 視窗取得權測試 | `backend/tests/test_daily_model_estimate.py`（`??`，`9062b9c2…`，24 節點） | 有改（＋1 節點） |
| 估計本體／五條入口 | `backend/app/services/daily_estimates.py`（`??`，`3f8ba581…`）、`backend/app/api/personalization.py`（`M`，`2bca90f2…`） | 本輪**未改**，列於此只為讓 #187 的可審包自含 |
| 本輪未動 | `backend/app/main.py`、`app/api/personal_models.py`、`app/auth/entitlements.py`、`app/services/kill_switch.py`、`backend/migrations/versions/c3d4e5f6g7h8_….py`、`frontend/**` | 0 處（僅讀取作證據） |

逐檔 `git add`，不用 `git add -A`；**本節未 stage、未 commit、未 push、未 merge、未部署**。**未包含**：`/tmp/alethra-runs/*`、`/tmp/r6*`、`/tmp/mut18/*`、`lane.db`、`.next/**`、`backend/tests/collected_nodes.txt`（生成檔）、`frontend/tsconfig.json`。

## 第六輪（第五輪 20:05 HKT 之後接續，本節完稿 22:24 HKT）：#209 改列「已重現缺陷（已修復）」、升級不再自動註冊新 lane、#195 來源查詢受 owner 約束與實際 DDL 實物

本節是同一交付的未完成條件，不是新一輪開工申請。**保留**：#195 的正式 `decision-history`、正式 runner 的離線證據、#207／#208 可隔離註冊結果、#187 的三種控制分家。本節只寫本輪實際量的東西。

### 一、本輪實際動的 7 支檔案（現測身份，22:20 HKT 一次量齊）

| 檔案 | git | 行 | sha256（前 8） | 節點 | 本輪位置 |
|---|---|---|---|---|---|
| `backend/app/services/daily_estimates.py` | `??` | 1,193 | `f40ce02f…` | — | `:399 settled_estimates()`、`:462 _summary_recorded_skills()`、`:491 window_state()`、`:791`（開 tick 讀帳本）、`:939-940`（兩個摘要計數） |
| `backend/tests/test_daily_model_estimate.py` | `??` | 1,774 | `f44e5c27…` | **27** | `_window_state()` helper、4 支替代節點（`:629` 中斷不重扣／`:734` window_state 四態／`:810` 零花費拒因是記錄非第二次嘗試／`:897` 摘要消失不能買兩次視窗）、`_lanes_named()` |
| `backend/app/config.py` | `M` | 565 | `f4d8d144…` | — | `:55 DEFAULT_LEARNING_SCHEDULER_LANES`、`:209 registered_learning_lanes`、`:320-332 _validate_learning_scheduler_jobs()` 內的 registry↔default drift guard（訊息在 `:328`） |
| `backend/app/workers/learning_scheduler.py` | `M` | 1,284 | `48e876c7…` | — | `:202 allowed = set(...registered_learning_lanes)`＋模組 docstring 的註冊控制句 |
| `backend/tests/test_learning_scheduler.py` | `M` | 938 | `b288825d…` | **28** | 本輪改動位置：`:225` 逐名列舉契約、`:245` 升級不自掛、`:285` 真啟動路徑（三段 `TestClient(main.app)` lifespan）、`_process_settings` helper；刪除前輪的 `test_the_default_registers_every_lane_it_knows`（該檔其餘 hunk 是先前各輪與平行 session 的累計，與本輪不可拆，故不在此宣稱行數歸因） |
| `backend/app/api/paper_portfolio.py` | `M` | 1,851 | `08a3ad9b…` | — | `:1723` 讀門 owner 約束＋兩段契約文字（`linked_inference_id`、`inference_source_state`） |
| `backend/tests/test_decision_history_export.py` | `??` | 682 | `2a14d7c2…` | **8** | `:496 _link_decision_to()`、`:510 _stranger_usage_row()`、`:536` 新節點＋檔頭「不做什麼」段 |

**沒有 schema 變更、沒有新 migration、沒有執行任何未批准 migration。** #209 的修復用的是既存持久狀態（見第二節），不是新欄位。

### 二、#209：重複扣額改列「已重現缺陷（已修復）」，三支變異探針證明保護是承重的

缺陷陳述（不是「單進程正確、待 PG 驗證」）：一支 job 死在中間（`status=running`、`result_json` 未寫）之後，同一 skill 在同一視窗被**重新估價、重新扣額**。本輪修復的判準是 owner 講的那條——**帳本，不是摘要**：

- 持久視窗身份＝`usage_records` 裡由 `_run_personal_prediction` 寫下、`metadata["via"] == "daily_service"`（`DAILY_SERVICE_VIA`）的那一列，鍵為 owner＋skill_unit_id＋instrument_id＋as_of_date。**該列與 `settle_reservation` 在同一 transaction 提交**，而 `learning_jobs.result_json` 是之後才提交的另一次 commit——所以摘要可能永遠寫不下來，帳本不會。
- 四種狀態分開回答（`window_state()`）：取得執行權（`learning_jobs` 有該 window 的列）／估計已持久化（`estimate_persisted`）／額度已結算（`quota_settled`）／job 摘要未寫（`summary_written`），並給出 `state` 與 `retry_owes`。
- 兩個計數按**讀取時刻**命名，不再共用一個名字：`billed_before_this_run`（`:791`，tick 打開時讀，等於 `len(recovered)`）與 `billed_in_window`（`:940`，收尾時**重讀帳本**，不是 `len(results)+len(recovered)` 加出來的）。
- 沒有 RAM lock、沒有「只看最近 N 筆摘要」、沒有「把所有 running 視窗永久封住」。`:462` 掃的是該 owner 該 job type 的**全部**列（無 `limit`），且它只說明「已完成的那輪決定了什麼」。
- 重試契約不變（本輪把它釘成斷言）：每視窗每 instrument 一次嘗試，**拒絕也算一次**；下一視窗才重試。手動按鈕路徑（`window=None`）刻意不走去重。

變異探針（每支皆 `cp -p` 快照＋`shasum -a 256` 逐位復原核對）：

| 探針 | 移除的保護 | 結果（`tests/test_daily_model_estimate.py`，27 節點，`-p no:randomly`） |
|---|---|---|
| **P1** | `:791` 改為 `recovered = []`（不再讀帳本） | **3 failed, 24 passed**：`test_a_tick_that_dies_mid_run_does_not_bill_the_estimate_it_had_paid_for`、`test_the_window_state_separates_the_claim_from_the_bill_and_the_summary`、`test_a_lost_summary_cannot_buy_a_window_twice` |
| **P2** | `:488` 改為 `return []`（不再讀已關閉的摘要） | **3 failed, 24 passed**：window_state 那支、`test_a_refusal_that_spent_nothing_is_that_windows_record_not_a_second_attempt`、`test_a_switch_pulled_mid_run_stops_the_skills_not_started_yet` |
| **P3** | 讀門 `UsageRecord.user_id == portfolio.owner_id` 一字（`paper_portfolio.py:1723`） | **1 failed, 7 passed**：只有本輪新節點 `test_a_lineage_id_that_is_not_the_readers_reads_back_as_missing` 失敗，其餘 7 支不受影響 |

P1／P2 的分界正是設計：花過錢的記錄在帳本上（P1 殺它），沒花錢的拒絕在摘要上（P2 殺它）。探針後重跑：27/27 通過、檔 hash 回復 `f40ce02f…`；決策歷史檔 8/8 通過、hash 回復 `08a3ad9b…`。

**真實 transaction 邊界的四條斷言**（都在 P1 之下會失敗）：已計費的 skill 不被重做（同窗第二跳 `refused: already_estimated_this_window`）；未完成的 skill 依重試契約仍會得到服務（下一視窗兩支都估）；失敗／rollback 不留錯誤扣額（settled／released 對帳）；帳本摘要可從持久狀態重建（把 `result_json` 抹掉後重跳，`recovered` 仍帶回原 `inference_id`），**所以摘要不是唯一去重依據**。

### 三、升級安全：註冊表 ≠ 預設集（#214），以及啟動路徑實測（#215）

前情：升級前就已開了共用 scheduler、`LEARNING_SCHEDULER_JOBS` 未設或留空的部署，**不得因升級自動拿到每日估計**（那是一支會扣額的新 lane）。第五輪把留空解釋成「全部 4 支」，本輪依 owner brief 反轉並一般化：

- `config.py:55 DEFAULT_LEARNING_SCHEDULER_LANES = ("daily_research", "daily_simulation", "weekly_analysis")`——「跑學習服務」這句話歷來指的就是這三支；`LEARNING_SCHEDULER_LANES`（4 支）保留為**註冊表**（本 build 能排什麼），兩者語意分開：一個回答「開學習服務」，另一個回答「這支也要跑，而且要對 owner 扣額」。
- `learning_scheduler_job_list` 是**純解析**（只含分隔符的值在這裡保持空，交由驗證子拒，不會悄悄變成預設集）；`registered_learning_lanes`（`:209`）＝解析結果或預設集。`registered_lane_ids()`（worker `:202`）只讀 `registered_learning_lanes`，死掉的 `lanes if not allowed else …` 分支移除。
- **drift guard**（`_validate_learning_scheduler_jobs` 開頭）：預設集若點了本 build 排不出來的 lane，設定載入階段就 `ValueError`，不是啟動後靜默少掛。
- 節點面：刪除 `test_the_default_registers_every_lane_it_knows`（它釘的正是被反轉的舊行為）；`test_creating_the_scheduler_registers_exactly_the_named_lanes` 改為明確點名四支；新增 `test_an_upgrade_registers_no_lane_the_host_never_asked_for`（預設 ⇒ 恰好三支，且 `DEFAULT ⊂ REGISTRY` 真；點名 ⇒ 恰好該支）。
- **「不能用只建立 scheduler 物件的測試替啟動副作用背書」**：新增 `test_the_startup_path_is_what_puts_a_lane_on_the_clock()` 走 `TestClient(main.app)` 的 ASGI lifespan（三段）：① 開關 true＋allow-list 空 ⇒ `main._learning_scheduler.running` 且實際掛上的 job id **恰好等於預設三支**，同一刻 `auto_collection.get_scheduler_status()` 回報 `enabled=False`、`running=False`（鄰座開關未被連帶啟動），出上下文後 `scheduler.running` 為 False（關閉線真的收掉執行緒池）；② `LEARNING_SCHEDULER_JOBS=daily_model_estimate` ⇒ 掛上估計 lane 且 cron 為 `hour='5', minute='0'`；③ 開關 false ⇒ `main._learning_scheduler is None` 且四支 spy `call_count == 0`。
- 另兩支既有 UTC 節點改為點名全部四支，並補 `set(jobs) == set(LEARNING_SCHEDULER_LANES)`，免得新的預設值把它们的覆蓋面**悄悄縮小**（這是本輪改預設時真實踩到的坑）。

### 四、#195 決策歷史：成果保留、結論收縮、來源查詢受 owner 約束

**保留**（未重複執行、未擴大數字）：獨立於交易 journal 的 `GET /{id}/decision-history`（CSV／JSON 同一組列）；無訂單決策**不進**交易 journal；`none`／`present`／`missing` 三態；`predict → 持久 inference_id → 建立決策 → 查詢／匯出` 一條鏈由 `tests/test_decision_history_export.py`（8 節點）與第五輪 r205／#202／#204／#205 的既有有效證據支撐。

本輪三件事：

1. **讀門受 owner 約束**（`paper_portfolio.py:1723`）：來源批次查詢原本只綁 `UsageRecord.id.in_(...)`，未綁 owner——寫門（`app/services/decision_lineage.py:33-36`）本來就拒別人的 inference，但一条经由 backfill／直接寫入進來的 lineage id 會讓**別人帳本的 metadata 渲進這個 owner 的歷史**。現加 `UsageRecord.user_id == portfolio.owner_id`，讀不回時落為既有的 `missing`（不是新增狀態）。**skill 約束刻意不加在讀門**：那會把 `missing` 的含義改寫成「視窗不對」，而 `inference_skill_unit_id` 欄位存在的目的正是把差異**渲出來而非藏起來**；skill 的一致性仍由寫門負責（此處按實測限制描述，不按願望描述）。新節點同時釘反面：同一支外來 `usage_records` 列，對**它的 owner** 仍然渲染 `present`＋四欄 metadata——否則「一律拒」也能過關。
2. **retention 宣稱收縮為實測限制**：兩段契約文字改寫。核對方式與結果——全树只有一處對 `usage_records` 的刪除：`grep -rn "delete(UsageRecord)\|DELETE FROM usage_records\|UsageRecord).delete" app migrations scripts tests` → **唯一命中 `scripts/seed_e2e_personal_learning.py:238`**（該 seed 檔清自己種的 user_id）。所以 `missing` 今日**只能**由應用外的刪除造成，文字據此寫成「這是防呆，不是被描述過的生命週期」，不再暗示有 retention 排程會刪。
3. **實際 migration DDL（兩 lane 實物，不連接任何伺服器）**：
   - PostgreSQL 文字：以 alembic 離線 context 渲染 `c3d4e5f6g7h8_add_decision_inference_link.py` 的兩個操作（`MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": …})`）：
     `ALTER TABLE decision_records ADD COLUMN linked_inference_id UUID;`
     `CREATE INDEX ix_decision_records_linked_inference_id ON decision_records (linked_inference_id);`
     ——無 FK、無 NOT NULL、無 CHECK。**這是渲染，不是在 PG 上執行過**；該 migration 檔本身有 `conn = op.get_bind()`＋`_table_exists(conn, …)`（:67，`inspect(conn)` 在 :45），需要能實例化 inspector 的綁定，所以整檔離線 `--sql` 不可能；本輪**未**跑 `alembic` CLI（其 `env.py` 的連線路徑未先讀過，為守「不碰受保護 DB」不去試）。
   - sqlite lane 實物：`DATABASE_URL=sqlite:// ` 下用 ORM `create()` 後讀 `sqlite_master`／`PRAGMA table_info`：`linked_inference_id VARCHAR(36)`、notnull=0、無 FK（該表 FK 只有 `paper_portfolio_id`→paper_portfolios、`simulation_session_id`→simulation_sessions、`owner_id`→users），索引 `ix_decision_records_linked_inference_id` 存在。型別來自 `app/database.py:15 @compiles(UUID, "sqlite") → VARCHAR(36)`（同檔 `:11` 是 JSONB→JSON；`tests/conftest.py:138` 同款，且 `app/database.py:10` 那組只在 `database_url` 以 `sqlite` 開頭時才註冊）。
   - **結論（這才是 brief 要的那句）**：同一支 `Column(UUID(as_uuid=True))` 在兩條 lane 的物理型別不同（PG `UUID`／sqlite `CHAR-ish VARCHAR(36)`），所以「ORM 寫 plain UUID」不等於已部署 schema；讀門因此用**欄位自己型別的 typed IN**，兩條 lane 都命中。PG 上的實際 `information_schema` 讀回仍是 **NOT RUN**（A 包）。

**單獨列出的限制（不在本輪修復範圍）**：`human_decision_events`（`app/models/human_decision.py:24`）**不保存 lineage**——該檔 `linked_inference_id` 出現 0 次，它的「來源」是 `server_strategy_version_id`／`server_instrument_id`／`server_step_number`／`server_snapshot_date`／`server_data_cutoff_at`＋`decision_instant`，不是 inference id。寫入口 `app/api/human_decisions.py:172`（建立）與 `:289`（更正）**只寫這張表**（該模組內 `DecisionRecord` 出現 0 次），因此透過那道門記下的決定**不會出現在 `decision-history`**（該檔讀 `decision_records`）；`app/api/simulations.py:2206` 是另一條會寫 `DecisionRecord` 的路徑。要讓模擬的人工決定也進決策歷史，需要的是**寫入端決定**（要不要共寫一列、或以另一張來源表匯入），本輪未做、未宣稱。

**前端已知缺口**（本輪未動 `frontend/**`）：`frontend/src/components/training/DailyEstimatePanel.tsx:374` 渲染 `state.last_estimate.predicted_action`，而 `settled_estimates()` 對**重建出來的**（`recovered`）列誠實地給 `predicted_action=None`／`verdict_durable=False`，所以那一格會顯示空白。這是顯示層要補的字，不是資料層假造值的理由。

### 五、本輪批次帳本（每批自帶命令／身份／原始輸出／exit／skip；**不得相加**）

**B1（本節頭條）**——正式 sqlite lane，含本輪全部 7 檔與消費其契約的檔：

> 原始產物的**完整路徑**（本輪現測存在，5 檔齊：identity.txt／collect.txt／pytest.log／junit.xml／exit-code.txt）：`/var/folders/tk/4hk5dllx6gjdk9ksk7f5v8900000gn/T/alethra-runs/sqlite-20261118T141459Z-23484/`。正式 runner 的預設根是 `${TMPDIR:-/tmp}/alethra-runs/<layer>-<utc>-<pid>`（`scripts/run_tests.sh:298`），所以**不在 `/tmp/alethra-runs`**（那裡只有 r205／r208／r209 三個舊 `/tmp` runner 產物）。**限制照實講**：`$TMPDIR` 是 OS 可回收的暫存區，而本包（Git 保存範圍）**明確排除** run dir，所以這些原始檔不是長期保存的證據；可長期復現的是「同一命令＋同一 7 檔內容＋同一 tree-digest」，重跑即重生成。

```
cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh sqlite \
  tests/test_decision_history_export.py tests/test_daily_model_estimate.py \
  tests/test_learning_scheduler.py tests/test_config.py tests/test_auto_collection.py \
  tests/test_simulation_daily_tick.py tests/test_decision_lineage.py \
  tests/test_paper_journal_csv_export.py tests/test_paper_decision_journal_scope.py \
  tests/test_manual_decision_books_linked_portfolio.py tests/test_paper_portfolio.py
```

`identity.txt`（run dir `sqlite-20261118T141459Z-23484`）逐欄：git `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（髒樹，porcelain 202 檔 sha256 `83ee89fc…`）；interpreter `.venv/bin/python`；lane fingerprint `python 3.12.16 | pytest 8.3.3 | sqlalchemy 2.0.36`；target `sqlite /var/folders/…/alethra-sqlite-test-23484.db`；provider `DATA_PROVIDER=demo`；selection-kind `explicit-selection`；**tree-digest before＝after＝`e3d520f543d0eda4dc4445d6deedacf3805712eb5a8aec3e28e319cf606eb9d1`**（`scripts/run_tests.sh:345 tree_digest()` 現算）；collected-node-ids 289。

原始輸出末行：`exit code 0`；`collected items 289 (collect.txt)`；`junit records tests=289 skipped=3 failures=0 errors=0`；`pytest summary 286 passed, 3 skipped, 1498 warnings in 202.10s (0:03:22)`；skip census `3 units pg_only=3 live_stack=0 other=0（分類加總＝3）`；`this run selected specific tests, so the whole-suite ceiling does not apply`；`reported units 289 = collected 289 + collection-level 0 -> OK`；unresolved 0／duplicate 0／collected-but-no-record 0／record-without-id 0；`evidence gate: ledger closes against this run's own artifacts`。

**B0（被 B1 取代，不引用其結論）**：22:01 HKT 那批 192 支（digest `8d92b409…`）在 `paper_portfolio.py` 改動**之前**跑的，本節改以 B1 為準。

**B2（探針批）**：P1／P2／P3 的原始 failed／passed 行在第二節表格內，皆 `-p no:randomly`、`DATA_PROVIDER=demo`、直接 pytest（非 lane），因為它們要的是「改一行的那一刻」的信號，不是可保存的 lane 證據。

**B3（被 gate 拒絕的一批，保留為 gate 的實測）**：22:14:32Z run dir `sqlite-20261118T141432Z-23112`——選取裡有個不存在的路徑 `tests/test_config_learning_lanes.py`，collector 回報 `collected-node-ids: 0`，runner 印 `refusing: the selection matched no tests, so this lane would run nothing.` 並停。**該批 exit 碼未記錄為通過**，其外層 pipe 的 0 不是 pytest 的狀態；本節引用的是改成真實路徑後的 B1。

### 六、對前輪記載的更正（本輪現測推回上文）

1. **第五輪第十節第 2、3 點（本檔第 1293-1294 行）把 #209 寫成「SQLite 不能證明 PG row-lock 安全，故結論只到單進程內正確」與「兩行程爭同一視窗未證」**。本輪依 brief 改列：**重複扣額是已重現缺陷，且已修復**（第二節 P1）；「未證」剩下的是 **PG 上兩行程的實際競態**（A 包），兩者不能再混寫。
2. **第五輪第十一節（第 1304-1307 行）的 hash 與節點數全部過期**：`832560e8…`→`f4d8d144…`（config）、`840eadd9…`→`48e876c7…`（worker）、`bf58dee9…`→`b288825d…`（排程測試，27→**28** 節點）、`9062b9c2…`→`f44e5c27…`（估計測試，24→**27** 節點，行數 1,472→**1,774**）、`3f8ba581…`→`f40ce02f…`（估計本體 `daily_estimates.py`，行數以第一節現測 1,193 為準）。
3. **本輪暫記「`test_a_refusal_that_spent_nothing…` 對變異探針不敏感」是錯的**，撤回：它對 **P2** 失敗（摘要那半），只是對 P1（帳本那半）不失敗——因為拒絕本身沒花額度、帳本上沒有它的列。前一次在 SQL 視窗條件上做的探針之所以看起來只殺一支節點，是因為去重 defens 有兩處（SQL 條件＋`_summary_recorded_skills`），單改一處會被另一處補住；本節因此改測「整半邊保護移除」，並將兩半各自的失敗節點分開記。
4. **收縮契約文字不會撞別的釘選**（實測）：`grep -ln "JOURNAL_COLUMN_SEMANTICS\|DECISION_HISTORY_COLUMNS\|inference_source_state\|decision-history" tests/*.py` → **只命中 `tests/test_decision_history_export.py`**。
5. **`_decision_history_rows` 只有一個呼叫者**（`app/api/paper_portfolio.py` 內的 `export_decision_history`），本輪讀門改動的爆炸半徑即此一支路由。

### 七、本輪仍未執行／仍未證明（NOT RUN／UNKNOWN，理由各自不同）

1. **PG 兩行程同一視窗競爭**：本節全部扣額／去重數字都在隔離 sqlite 檔（`alethra-sqlite-test-23484.db` 等），**未**讀或寫受保護 DB。實測程式與命令在 A 包，未批准執行 → **NOT RUN**。
2. **05:00 UTC 由 APScheduler 自己響**：本輪把「掛上且 cron＝hour 5／minute 0」量到的是**正式 lifespan 啟動路徑**（第三節①②），但仍未啟動常駐行程、未改系統時間，所以「時間到了真的會響」仍是 NOT RUN（B 包）。
3. **PG 上的實際 schema 讀回**（`information_schema.columns.data_type` 是否 `uuid`）：只到「渲染」，未「執行」 → A 包。
4. **對真實帳號啟用或扣額**：本輪所有 opt-in 都是測試種子帳號；未對任何真實帳號啟用、未扣額。
5. **瀏覽器正常客戶流程**：未跑（C 包需服務啟停＋瀏覽器批准）；前輪前端 build PASS 不代替它。
6. **stage／commit／push／merge／部署**：未做。本節第七節之外的 Git 事實只有「HEAD `5a34e3fe`＋髒樹 202 檔」。
7. **依賴安裝、外呼 provider、修改秘密**：未做（`DATA_PROVIDER=demo`）。
8. **`HumanDecisionEvent` 與決策歷史的接合**：只列出限制（第四節末），未實作、未宣稱。
9. **`DailyEstimatePanel.tsx:374` 對 `recovered` 列顯示空白**：已知缺口，本輪未動前端（需另取前端 build 證據才好看改動後果）。

### 八、四個可分別批准的操作（本輪交付**正文**，不是本地連結或雜湊；未量測者一律標 UNKNOWN）

#### A. PostgreSQL 包（先修好重複扣額，本包現在才可審）

| 欄位 | 值 |
|---|---|
| 隔離資源 | host `127.0.0.1`（僅 loopback）；port `55432`（本輪 `nc -z 127.0.0.1 55432` 實測 **FREE**）；DB `alethra_r6`；PGDATA `$TMPDIR/alethra-r6-pgdata`；socket dir `$TMPDIR/alethra-r6-sock`。全部**新闢**，不碰機器上既有實例／資料目錄。 |
| **本機既有 PG（實測，必須避開）** | `pgrep -fl postgres` 現測：**有一個 postgres 正在跑**——pid 34471，PGDATA `/Users/tommylam0915/Documents/Qoder/2026-11-03/5062987d/recovery-20261114/int-accept-20261117/pgdata`，port **54340**，`unix_socket_directories=`（空）、`listen_addresses=127.0.0.1`。它屬**平行 session 的 acceptance 線**，是本包的禁区：不連線、不讀其 PGDATA、不停它。**未列 5432 上的東西**：本機 `/tmp/.s.PGSQL.*` 實測不存在。 |
| 可用工具（實測路徑，PATH 上無任何 postgres） | 捆裝於 `alethra/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin/`：`initdb`、`pg_ctl`、`psql`、`pg_isready`、`postgres` 全部 present，`psql --version` ＝ **PostgreSQL 16.2**。（`backend/.venv` 內 `pgserver` 未安裝，實測 NOT INSTALLED——故本包用上述絕對路徑，不假設 `brew`／系統 postgres。） |
| 角色 | `alethra_r6_migrator`（DB owner，僅執行 migration 用）；`alethra_r6_app`（LOGIN，只對 `alethra_r6` 有 `CONNECT/CREATE/SELECT/INSERT/UPDATE/DELETE`）。**應用不以任何 superuser 身分連線。** 密碼不寫在本包、不寫入 repo，由 owner 於執行時提供（環境變數），任何輸出一律經 `_safe_dsn` 遮蔽。 |
| 起始 revision：**預期** vs **實測** | 預期：本包無法預期——該 DB 是新建的，**實測起始 revision＝UNKNOWN**（此機器從未對任何 PG 跑過 `alembic current`）。執行時先只讀印 `alembic current`，把它寫進 run dir 的身份檔，**與預期不符即停**。 |
| 固定目標與鏈 | 目標 head：執行前以 `alembic heads` 讀出、與 `backend/migrations/versions/` 內釘選 tail（#158／#184 釘過的 chain）逐一比對；比對結果進 run dir。**不在包內憑記憶寫 head 字串**——本輪未重讀該值，故其現值為 UNKNOWN。 |
| 正式命令 | `cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh pg tests/test_decision_history_export.py tests/test_daily_model_estimate.py tests/test_learning_scheduler.py tests/test_config.py tests/test_auto_collection.py tests/test_simulation_daily_tick.py tests/test_decision_lineage.py tests/test_paper_journal_csv_export.py tests/test_paper_decision_journal_scope.py tests/test_manual_decision_books_linked_portfolio.py tests/test_paper_portfolio.py`（B1 同一選取，289 支；B1 中那 3 支 `pg_only` skip 在此層應實際執行） |
| 預檢（唯讀，跑在自我重跑之前） | 令 `PGB=alethra/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin`。順序與預期：①`nc -z 127.0.0.1 55432` ⇒ **應失敗**（本輪實測 FREE；若通代表 port 被人占了，停）；②`$PGB/initdb -D "$PGDATA" -U alethra_r6_migrator --pwfile="$TMPDIR/alethra-r6-pw" -A scram-sha-256`（pwfile 由 owner 於執行時提供，0600，不入 repo、不印出；這一步是本包唯一會寫新目錄的，**不碰 54340 那個實例**）；③`$PGB/pg_ctl -D "$PGDATA" -o "-p 55432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR" start`；④`$PGB/pg_isready -h 127.0.0.1 -p 55432` ⇒ `accepting connections`；⑤`$PGB/psql -h 127.0.0.1 -p 55432 -U alethra_r6_migrator -d alethra_r6 -Atc "select current_database(), current_user, version()"`；⑥`alembic current`（寫進身份檔後才比較）；⑦查欄位型別：`select data_type from information_schema.columns where table_name='decision_records' and column_name='linked_inference_id'` ⇒ 升級後應為 `uuid`（升級前查無該列）；⑧`select conname, confrelid::regclass from pg_constraint where conrelid='decision_records'::regclass` ⇒ 應只有 `paper_portfolio_id`／`simulation_session_id`／`owner_id` 那三條既有 FK，**`linked_inference_id` 必須查無 FK**。任一項與預期不符即停，不升級、不改角色、不刪資料。 |
| 兩行程對照（#209 最後一格） | 同一 window、同一測試 owner 與 2 支測試技能帳號，起 2 個 OS process 同時 `run_daily_estimate_tick(scope=..., window=<同一日>)`；斷言：每 (skill, instrument, as_of) 在 `usage_records` 恰好 1 列 `via=daily_service`、`settle_reservation` 每筆預留有對應的 settled 或 released、兩個 job 摘要加總的 `billed_in_window` 與帳本實際數一致。停止方式：`TERM` 兩個 pid；結束時以第二條連線核對零殘留。 |
| 正常停止方式 | `$PGB/pg_ctl -D "$PGDATA" -m fast stop`（只停這個 PGDATA，54340 那個實例不在射程內）；lane 本身的停止是 pytest 結束，不需手動 kill。失敗/中斷**不自動重試**、不自動 drop database、不自動刪 PGDATA（留給 owner 覆核，刪除需另行批准）。 |

#### B. 服務與真實 cron 包

| 欄位 | 值 |
|---|---|
| 確切程序 | 1 個 uvicorn 行程載入 `app.main:app`（會走 lifespan → `start_learning_scheduler`）。舊 live daemon 有**兩個**都要成立的入口：設定 `app/config.py:172 live_trading_enabled: bool = False`（預設未開）與獨立行程入口 `app/services/live_trading_daemon.py:471 main()`／`:549 if __name__ == "__main__"`。本包**兩者都核對為未開**（開機設定快照＋程序清單），且不執行那個入口。 |
| 綁定 | loopback only：`--host 127.0.0.1 --port 8031`。 |
| 本機程序與 port 現測（2026-11-18 深夜重唸，執行前必再量一次） | `pgrep -fl uvicorn` ⇒ **有一个不屬於本包的行程在跑**：pid 55642，`../../int-accept-20261117/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8120`（平行 session 的 acceptance 線，**禁區**：不 kill、不複用、不讀其 log）。`pgrep -fl "next-server|next dev"` ⇒ 此刻**無前端行程**（前一轮記錄的 pid 81307 已不在）。port 現測：`8031 FREE`、`3000 FREE`、`8120 IN USE`（即上列那个）。故本包用 8031，且**啟動前後都要重印程序清單**，確保停的只有自己那個 pid。 |
| 完整開關與註冊清單（執行前逐條唸給 owner） | `ENABLE_LEARNING_SCHEDULER=true`（總開關，四支共用）；`LEARNING_SCHEDULER_JOBS=daily_model_estimate`（**本包唯一會掛的 lane**，其餘三支不掛）；`AUTO_COLLECTION_ENABLED=false`（鄰座開關明確關，並用 `GET /api/v1/admin/collection/status`（`app/api/admin.py:89`）的 `enabled`／`running` 兩欄覆核）；`DATA_PROVIDER=demo`（不許外呼 provider）；`LIVE_TRADING_ENABLED=false`（`config.py:172` 預設值，仍需快照核對）；kill switch：不啟動，另以一支 kill-switch 覆蓋節點證明「讀不到就拒」。本包**未明示開啟**任何 lane 之外的東西。 |
| 測試 owner／skill | 1 個本機種子測試帳號（非真實帳號）＋ 1 支其名下技能帳號。 |
| 資料與額度上限 | instruments：1 支測試 symbol；`max_data_age_days`：以該測試帳號自己的宣告值；預留額度上限 1 筆／tick；`LEARNING_SCHEDULER_MAX_OWNERS=1`（扇出上限釘到 1，杜绝掃到其他帳號）。 |
| 允許的網路活動 | 僅 loopback（本機 API 與本機 DB）。無 provider 呼叫（demo）。 |
| 要觀察的客戶結果 | 05:00 UTC（UTC+8 的 13:00）自然響一次：`learning_jobs` 出現 `job_type=daily_estimate`、`config_json.window=<當日>`；`usage_records` 出現 1 列 `via=daily_service`；`GET /api/v1/ml/personalization/daily-estimate/status`（`app/api/personalization.py:420`）的 `last_estimate` 指回同一 `inference_id`；**訂單數 0、人類決策數不因排程而增加、replay 進度不推進**；再等下一 tick 同視窗不重複扣額（把該行 `result_json` 抹掉後仍不扣，才是 P1 那半的現場版）。 |
| 日誌 | 程序 stdout／stderr 重導 `$TMPDIR/alethra-r6-service.log`；DB 端以只讀查詢對帳，不另建摘要檔。 |
| 停止方式 | `kill <pid>`（TERM，不等級 hard kill），隨後以 `pgrep -f uvicorn` 與 `GET /api/v1/admin/collection/status` 確認沒有還在響的排程；**停止不等於刪除資料**，run 記錄保留給 owner 覆核。 |

#### C. Browser 包

固定候選（進瀏覽器前逐條核對，不符即停）：HEAD `5a34e3fe`＋第一節 7 檔的 sha256（`f40ce02f… f44e5c27… f4d8d144… 48e876c7… b288825d… 08a3ad9b… 2a14d7c2…`）＋ B1 的 tree-digest `e3d520f5…`。

| 客戶動作（**owner 自己點**） | 真實入口 | 預期結果 |
|---|---|---|
| 以密碼登入，開自己的技能帳號 | 登入頁 → 個人學習面板 | session 為 human 通道；看到的技能帳號是自己那支 |
| 對某支技能帳號**開啟每日估計**並勾 1 支 instrument | 每日估計面板的 opt-in；後端實際入口 `PUT /api/v1/ml/personalization/daily-estimate/consent`（`app/api/personalization.py:302`） | 存的是**可撤回的明確同意**；面板顯示視窗身份（window／instruments／max_data_age）與上次估計 |
| 等待／觸發後讀回 | 同一面板 | `last_estimate` 有 `inference_id`、模型版本、artifact；**不出現任何訂單**、不自動記下決策 |
| **撤回**同意 | 同一面板的撤回；後端實際入口 `DELETE /api/v1/ml/personalization/daily-estimate/consent`（`app/api/personalization.py:362`） | 之後不再取新工作；正在跑的動作的結果與額度處置依 #209 那節的 `window_state`（`settled` 不重扣）；面板狀態與 `GET /api/v1/ml/personalization/daily-estimate/status` 一致 |
| 記下一條決策（連結到該估計） | `POST /api/v1/paper-portfolio/{id}/decisions`，帶 `linked_inference_id` | 201；列表與查詢都讀回同一 id |
| **匯出決策歷史** | `GET /api/v1/paper-portfolio/{id}/decision-history?format=csv`（與 `format=json`） | CSV 直接下載、在試算表開啟；`record_type=decision`；有訂單／無訂單決策都在；`inference_source_state` 為 `present`；同一資料夾的交易 journal **不因無訂單決策多列**（兩檔不同，這是 contract 而非 bug） |
| 把别人的 inference id 填給自己的一條決策 | API 直打（客戶做不到，故這是防呆驗收不是客戶旅程） | 寫門 400 `inference_skill_mismatch`／`inference_not_found`；若該列以其他方式進到資料裡，匯出讀回 `missing` 且不渲染他人的 metadata（本節第四節第 1 點） |

邊界（必須寫在報告裡）：代理自動化（browser-use）**只能**作 UI 可用性輔助觀察，並**不得**代替 owner 按下同意／撤回——同意是 owner 的政策行為；代理自己的 click 不構成客戶驗收。build PASS 不代替 browser PASS；本包 **NOT RUN**。

#### D. Git 保存與選單重釘包

- **base**：branch `live1-impl`，HEAD `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`；工作樹 porcelain 202 檔（sha256 `83ee89fcedf4…`）。
- **本輪增量（要保存的就是這 7 檔，逐檔 `git add`，不用 `git add -A`）**：第一節表格（路徑／git 狀態／行數／hash／節點數／本輪動到的位置）。
- **逐 hunk 歸因（含「多數 hunk 不屬本輪」這件事）**：對 HEAD 的 hunk 數＝`app/config.py` 6、`app/workers/learning_scheduler.py` 71、`app/api/paper_portfolio.py` 26、`tests/test_learning_scheduler.py` 42（`git diff -U0 -- <檔> | grep -c '^@@'`）。HEAD 早於整段未提交交付，所以這些數字是**十幾輪累計**，不是本輪量；本輪在這四支檔裡只動第一節列出的位置（worker 2 處、config 4 處、paper_portfolio 3 處、排程測試 6 處），其餘 hunk 屬前輪（#187／#195／#203／#206／#207／#208／#172 等）。另有 3 支**未追蹤**檔是整檔新增，無 hunk 可拆：`daily_estimates.py`（1,193 行）、`test_daily_model_estimate.py`（1,774 行）、`test_decision_history_export.py`（682 行）。
- **依賴**：`config.registered_learning_lanes` ← `learning_scheduler.registered_lane_ids()` ← `tests/test_learning_scheduler.py`；`daily_estimates.settled_estimates()` ← `:791` 去重 ← `window_state()` ← `api/personalization.py:420`（`GET /api/v1/ml/personalization/daily-estimate/status`，前輪） ← `tests/test_daily_model_estimate.py`；`paper_portfolio:1723` ← `tests/test_decision_history_export.py:536`。**7 檔一起才自洽**：只保存測試會紅，只保存本體會讓前輪釘選的節點失配。
- **不可拆即不可一併保存**：`learning_scheduler.py` 若逐 hunk stage，其餘 69 處前輪 hunk 的歸屬在本檔之外無紀錄可憑 → 本包**不**把它們算進本輪授權；owner 若只批准本輪增量，實務做法是整檔保存並以 commit message 標明「含前輪累計」，或由 owner 另批前輪的整檔範圍。**歸屬待核者不随包保存**。
- **排除**：`/tmp/alethra-runs/**`、`/var/folders/**/alethra-*`、`lane.db`／`alethra-sqlite-test-*.db`、`.next/**`、`__pycache__/**`、`backend/.env*`、`backend/tests/collected_nodes.txt` 與 `tests/skip_budget.json`（生成／釘選檔，只能由一次**整條 lane** 執行後 `pin --ack-diff` 產生）。
- **重釘會引入的選取差異（先講，不事後解釋）**：本輪選取是 `explicit-selection`，因此**整套 ceiling 不適用**、skip 預算（`collected=3548 total=183 pg_only=164 live_stack=18 other=1`）只報不 enforce；若把 B1 的 289 支拿去重釘 `collected_nodes.txt`，與其他單元組成的整條釘選**必然不同**（本輪只動了 11 檔的範圍）。此外 3 支 `pg_only` skip 在 sqlite 層永久 skip——B1 的 `pytest.log:272-274` 逐條記名：`tests/test_manual_decision_books_linked_portfolio.py:1988`、`:2034`、`:2099`，原因欄一律是 `row-lock serialisation requires a real PostgreSQL backend`；只有 A 包會讓它們開口。
- **狀態**：本輪**未 stage、未 commit、未 push、未 merge、未部署**；未建立 branch、未動 remote 設定。批准後的第一步是逐檔 `git add`＋`git status` 覆核實際進入的內容，發現與第一節表格不符（或 remote 指向本地路徑）即停。

## 第七輪（2026-11-19 08:04–09:10 HKT）：#209 改列「已修復：單進程已結算但摘要未寫的恢復情境」＋真實 transaction 邊界逐條讀碼、#223 邊界跑成承重節點、#224 面板三態修好並只驗證實際改動的

本節是同一交付的未完成條件，不是新一輪開工請示。**保留**（未重跑、未改寫其結論）：#195 的正式 `decision-history` 與其 CSV/JSON 獨立性、#207 的 opt-in 綁定證據、#208 可隔離註冊與正式 lifespan 實測、#187 三種控制分家、B1 那條 lane 入口本身。本節只寫本輪實際量的東西，以及在第七輪 brief 下必須改寫的三句話。

### 一、本輪實際動的 8 支檔案（現測身份，09:0x 一次量齊）

| 檔案 | git | 行 | sha256（前 12） | 節點 | 前輪釘選 → 本輪 | 本輪位置 |
|---|---|---|---|---|---|---|
| `backend/app/services/daily_estimates.py` | `??` | 1,258 | `686993e4ef08` | — | `f40ce02f…`（1,193）→ 現值 | `:498` 結束讀快照、`:500-507` PG 座位、`:463/:400` 帳本重讀、`:863` 逐 skill 控制重讀、`:912-926` 逐標准取得權、`:939/:952/:965` 三類 rollback、`:1006` 收尾重讀、`:1012` 摘要另一次 commit |
| `backend/tests/test_daily_model_estimate.py` | `??` | 1,976 | `242b69525a70` | **29** | `f44e5c27…`（1,774／27）→ 現值 | `:704-741` 崩潰節點新增 6 條「客戶讀回形狀」斷言（`predicted_action is None`、`verdict_durable is False`、`data_age_days`/`provider_id` **不在**列上、`last_refusal.reason/message_zh`）；`:1011` 未命名例外同窗重試 |
| `backend/tests/test_decision_history_export.py` | `??` | 879 | `dfde3025da05` | **9** | `2a14d7c2…`（682／8）→ 現值 | 新節點 `test_a_human_decision_event_never_becomes_a_row_in_the_decision_history`＋`_open_replay_session`／`_human_decision`／`demo_alpha_series`；檔頭證據等級段 |
| `backend/tests/test_daily_estimate_two_workers.py` | `??` | 574 | `4a04d71e6cc9` | 2 | 新檔 | `:210` 純機制節點（各 lane 皆跑）、`:376` `skipif(TEST_ENGINE.dialect.name=="sqlite")`＋`:385` 兩行程競爭節點（**NOT RUN**） |
| `frontend/src/lib/dailyEstimate.ts` | `??` | 61 | `5d1ba5e4701c` | — | 新檔 | `:35 estimateDisplay()`：三態唯一文字來源 |
| `frontend/tests/dailyEstimate.test.mjs` | `??` | 277 | `f80abd9ec1e8` | 8 | 新檔 | 欄位清單由 `daily_estimates.py` 兩個产生器現讀；行為 6 支；接線 1 支 |
| `frontend/src/components/training/DailyEstimatePanel.tsx` | `??` | 615 | `522d773b4a64` | — | 前值未另行保存 | `:300-303` 每卡一次 derivation、`:374-427` 渲染 `display.*`、`:429-438` 拒絕區分開 |
| `EXECUTION_TABLE_20261117.md` | `??` | 1,486→本節 | `273e6d67fd1f`（本節追加前） | — | — | 本節 |

**歸屬限制（照實講）**：`daily_estimates.py` 是未追蹤檔，前輪內容除 hash 外沒有可對比的基線，所以 1,193→1,258 的 +65 行**無法逐 hunk 對回前輪那筆 hash**；本節因此只以「現讀行號＋該行現在的行為」陳述，不宣稱哪幾行屬於哪一輪。`dailyEstimatePanel.tsx` 同為未追蹤、前值未保存，同理。

**沒有 schema 變更、沒有新 migration、沒有執行任何未批准 migration。** 本輪讀過但未改的承重檔案：`backend/app/api/personalization.py`（`M`，對 HEAD 7 個 hunk，mtime 2026-11-18T19:28 ⇒ **非本輪**）、`backend/app/api/personal_models.py`（`M`，47 hunk，未動）、`backend/app/api/paper_portfolio.py`（`M`，26 hunk，本輪只作為探針對象被改又被逐位還原）。

### 二、#218 真實 transaction 邊界（本輪逐條重讀，行號以 `686993e4ef08` 為準）

| # | 邊界 | 位置（實測） | 這次讀到的是什麼 |
|---|---|---|---|
| 1 | 讀取快照結束 | `daily_estimates.py:498 db.commit()`（在 `claim_estimate()` 內） | 每支標准決定要不要計價**之前**，先把本 session 的快照關掉。tick 開場那次 `settled_estimates()` 讀的是**描述**，不是鎖。 |
| 2 | 座位（只在此處存在） | `:500-507` `pg_advisory_xact_lock(hashtextextended(owner\|skill\|instrument\|window))` | **僅 PostgreSQL 分支**。SQLite 直接跳到 :509 的重讀，所以本機 lane 證的是「重讀发生」，不是「互斥发生」。 |
| 3 | 帳本重讀 | `:463 estimate_billed()` → `:400 settled_estimates()`（`UsageRecord` 全掃，無 `limit`） | 判斷依據是**已結算的列**，鍵為 owner＋skill＋instrument＋as_of。 |
| 4 | 錢與帳本同一提交 | `personal_models.py:626 reserve_quota` → `:628 predict_personal` → `:659 settle_reservation` → `:663 record_api_call_no_commit`（`billing.py:483 db.add`，不提交）→ **`:695 db.commit()`** | 一個 commit 同時讓「額度已結算」「帳本有列」「inference id 存在」三者成立；`entitlements.py` **全檔 0 個 commit**（只有 flush：`:171 :353 :402 :423 :442 :447 :475`）。所以 `inference_id` 就是那筆帳本列的 id——面板引用的正是它。 |
| 5 | 摘要另一次提交 | `daily_estimates.py:1012 db.commit()`（`job.result_json`） | 與第 4 條**不同交易**。摘要可能永遠寫不下來，帳本不會——這就是恢復情境的來源。 |
| 6 | 逐 skill 控制重讀 | `:863 db.commit()` 之後讀 kill switch（:879）與 consent（:885 `live_declared_ids`） | 「尚未開始」是撤回的邊界；正在估計的那筆不可退還。 |
| 7 | 三類 rollback | `:939`（`EstimateRefused`）／`:952`（`HTTPException`，閘門自己的拒因）／`:965`（其他例外→ `errors.append(f"...")`） | 未命名那類**只進 `errors`，是純字串**；`:529` 的 `isinstance(entry, dict)` 因此讀不到任何 skill id。 |
| 8 | `window_state` 的兩個旗標 | `:618-619` `estimate_persisted` 與 `quota_settled` 都＝ `bool(billed)` | 誠實寫法：這是**同一筆帳本列的兩個名字**，不是兩條互相獨立的證據。 |

**本輪據此收縮的宣稱（也是 brief 要的那句）**：「開工前讀取已結算帳本」**不是**兩個 worker 互斥的證明；宣稱得到的只有兩件事——(a) 每支標准在**新交易**裡重讀帳本才決定計價；(b) 計價與帳本列由同一提交落地。真正的兩行程互斥只能由 A 包那支 `pg_advisory_xact_lock` 競爭節點量，本輪 **NOT RUN**（未批准 PG）。既然沒有可靠互斥已被如實寫出，#219 的最小可審契約（advisory-lock 座位＋帳本重讀，無新欄位、無新 index 需求）與 #220 的兩行程節點都已先就緒，A 包不需要再補 schema。

### 三、#209：改列「已修復：單進程已結算但 job 摘要未寫的恢復情境」，以及 brief 的兩個問題

帳本行（本輪現測）：**狀態＝已修復（單進程情境），保護有節點且承重；兩行程情境＝程式已備妥、未執行。**

- **A settled 一次／恢復只補 B／第三跳不計價**（`test_a_tick_that_dies_mid_run_does_not_bill_the_estimate_it_had_paid_for`，實測）：死掉那輪 A 已付、B 未到；重試後 `served` 依序 `[skill_a, skill_b]`、`settled==2`、`reserved==0`；該輪摘要裡 A 同時出現在 `recovered` 與 `refused`（`:704-706`／`:713-714`）；`billed_before_this_run==1`、`billed_in_window==2`（第二個數字是收尾**重讀帳本**得來的，不是兩個 worker 各自累計相加）；`status=="partial"`。
- **問題 1「未扣額失敗可否在同一視窗重試」——兩個答案，不能混為一談**：
  - 有名字的**拒絕**：不可重試。`window_state` 給 `state="refused"`、`retry_owes=[]`（`:604-611`＋`:626`），且 `_summary_recorded_skills`（`:511`）把 `refused` 讀成「本視窗已記名」；節點 `test_a_refusal_that_spent_nothing_is_that_windows_record_not_a_second_attempt`（`:924`）。
  - 既非閘門拒絕、也沒有帳本列的**未命名例外**：會重試，且仍只計價一次。節點 `test_an_error_that_named_nothing_is_attempted_again_in_the_same_window`（`:1011`）實測 `errors==1`、`billed_in_window==0`、`state=="not_started"`、`retry_owes==[TEST_INSTRUMENT]`、同時 `execution_claimed is True`。也就是說「摘要是否記名」決定重試與否，「帳本是否有列」決定計價與否——兩者獨立。
- **問題 2「摘要實際遺失時的行為」**：`GET /api/v1/personalization/daily-estimate/status`（`personalization.py:420`）用帳本重建 `last_estimate`：`inference_id`／`model_version_id`／`artifact_sha256`／`as_of_date`／`feature_bar_date`／`billed_at` 在，`predicted_action` 為 `None`、`verdict_durable` 為 `False`，而 `data_age_days`／`max_data_age_days`／`provider_id`／`data_nature` **根本不出現**（`settled_estimates()` 不 emit）。本輪把這 6 條形狀釘在同一支節點上（前輪只在 service 層講，本輪釘在 HTTP 讀回），並且 `last_refusal.message_zh == "此技能帳號今日的估計已存在，未重複執行。"` 也一起釘住——**沿用與拒絕是同時成立的兩條陳述**，客戶畫面必須把它們分開顯示。
- **探針（把「有地方寫」升級成「壞了會有人發現」）**：本輪直接 pytest（非 lane）、`DATA_PROVIDER=demo`、`-p no:randomly`：

| 探針 | 移除的保護 | 實測結果 | 還原 |
|---|---|---|---|
| P5b | 摘要那半的鍵集合加入 `"errors"`（`:525` 的 `("results","refused","recovered")`） | `test_daily_model_estimate.py` **1 failed, 28 passed** | hash 逐位一致 |
| P6 | `_decision_history_rows`（`paper_portfolio.py`）改成把 `human_decision_events` 的 id 也列進客戶匯出 | `test_decision_history_export.py` **1 failed, 8 passed**（`2 == 1` 在 `row_count`）；只有新節點失敗 | 還原後 `08a3ad9b2797`＝前輪釘選值，`grep -c MUTATION`＝0，9/9 通過 |

P5 舊版（把 `errors` 放進 tuple 卻不改讀法）是 **void**：`errors` 的元素是字串，`:529` 的 `isinstance(entry, dict)` 直接跳過，所以 0 失敗 29 通過——本輪改用 P5b 才量到保護。這一行是寫給 Own 看的：一支探針跑過而 0 失敗時，結論是探針無效，不是保護多餘。

### 四、#222：同意綁什麼、切版誰有權、撤回何時停

- **綁定（讀碼＋實物）**：`grant_daily_estimate()` 寫下的 `scope_json.skill_units[]` 只有三個鍵——`skill_unit_id`、`instrument_ids`、`max_data_age_days`（`daily_estimates.py:286-292`）；`_validate_targets()`（`:214-258`）也只验這三樣加擁有權／狀態。同意行裡**沒有** `model_version_id`、**沒有** `artifact_sha256`。所以：**同意綁技能帳號＋標准＋新鮮度上限，不綁版本、不綁 artifact。**
- **版本由每次 tick 現場解析**：`current_selection()`（`:646`）→ `_estimate_one`（`:707-712`）拿選定的 `ModelVersion`，artifact 由該版本的訓練記錄 spec 決定。因此「切版」在資料層不需要新同意；**擴權的門不是每日估計的同意，而是選擇／啟用那道門本身**：`POST .../activate` 與寫入路徑只接受 `["write","training"]`，`inference` 範圍被拒（釘在 `tests/test_personal_model_permission_matrix.py:277` 的 `insufficient_scope`／`:317-321` 的 `accepted_scopes == ["write","training"]` 與 `activate` 403）。
- **本輪對 brief 那句「切版不得靜默擴權」的答复（不美化）**：程式**沒有**在切版後偷偷用新模型去解釋舊估計——狀態讀回比對選擇，比不中就把那條標成「此估計固定在你後來已替換的模型版本上；它不是你現在所選模型的輸出」（面板 `:418-425`，`estimate_matches_current_selection`）。但**下一次** tick 確實會用當時所選的版本計價，而不另取同意。是否要把「新模型需要另行同意」寫成契約，是 owner 的政策裁決；本輪未擅自加門，也未宣稱已有門。
- **撤回停止取新工作的時點**：逐 skill 在**該 skill 第一次推論開始之前**重讀（`:863` 結束快照 → `:885` `live_declared_ids` → `consent_withdrawn`，reason 訊息「你在這次批次走到此帳號之前撤回了它的每日估計，未對它執行推論。」）。已在估計中的那一筆不可退還，其結果與額度按第 4 條同一提交落地。
- **「不以同日去重代替同意驗證」**：兩條 reason 分開存在且互不代替——`consent_withdrawn` 由**同意行**回答，`already_estimated_this_window` 由**帳本／摘要**回答；實測兩者在同一支節點裡可分別出現（`:1011`／`:924`／崩潰節點）。

### 五、#223：`DecisionRecord` 與 `HumanDecisionEvent` 的分界跑成承重節點（五級證據分開講）

新節點 `tests/test_decision_history_export.py::test_a_human_decision_event_never_becomes_a_row_in_the_decision_history` 實測：

1. 走**正常客戶入口**開一支 replay（`POST /api/v1/simulations/versions/{id}`，本輪明確帶 `provider_id="synthetic"`——多來源時那道門會 400，`simulations.py:565-575`），記一條帶 lineage 的紙上決定（201），再用 `POST /api/v1/human-decisions` 記一筆人工事件（201，`actor_type=="human"`——密碼登入通道，`auth/provenance.py:30`；`execution_status=="record_only"`）。
2. 此時 `decision-history` 的 `row_count==1` 且**只有**那筆 `DecisionRecord`；`simulation_session_id` 同值、`inference_source_state=="present"`。
3. 事件更正（`POST /human-decisions/{id}/correct`）寫的是**新列**（`supersedes_event_id` 指向原事件），`GET /human-decisions` 列兩筆（以集合斷言，`created_at` 同值時順序不保證），而 `decision-history` **仍是同一筆**；CSV 原文含決定 id、**不含**兩個事件 id；交易 journal `row_count==0`（兩檔語義不同，這是 contract）。
4. DB 讀回（第二條連線）：`decision_records` 1 筆、`human_decision_events` 2 筆、被取代者 1 筆。
5. 欄位讀回：`linked_inference_id`／`paper_portfolio_id`／`outcome_status` 只在 `decision_records`；`idempotency_key`／`supersedes_event_id`／`server_step_number` 只在 `human_decision_events`；`simulation_session_id` 兩邊都有，但**沒有任何一支程式對它 join**——所以它不是關聯，是同名的兩份資料。

**「後者未保存 lineage 是未接通能力，不是另一張表的 PASS」**：本輪把這句落成第 3 點——事件存在且可查，但它不會出現在客戶的決策歷史檔裡。要接通需要**寫入端決定**（共寫一列，或讓匯出讀第二張來源表），那是產品裁決，本輪未做、未宣稱。

**五級 schema 證據（本節用到的位置各自標明）**：

| 等級 | 本輪實際有的東西 |
|---|---|
| ① migration 來源 | 四支未追蹤 revision 檔在 `backend/migrations/versions/`（`e1f2a3b4c5d6`、`f2a3b4c5d6e7`、`b2c3d4e5f6g7`、`c3d4e5f6g7h8`）；`scripts/check_migration_tail.py:54-59 EXPECTED_TAIL` 把它釘成批准字面值 |
| ② DDL 導出 | 前輪以 alembic 離線 context 渲染 `c3d4e5f6g7h8` 的 `ALTER TABLE`／`CREATE INDEX` 文字（**渲染，非執行**） |
| ③ 離線建表 | 本輪的欄位讀回：`sqlalchemy.inspect(TEST_ENGINE).get_columns(...)`——它讀的是**這條 lane 自己用 `Base.metadata.create_all` 建的表**，與路由 import 的是同一批 model class，所以它證明「model 與讀取端一致」，**不**證明部署過的 schema |
| ④ PG 升級 | **NOT RUN**（A 包 A4） |
| ⑤ 實際 schema 讀回（PG） | **NOT RUN**（A 包 A5）；本輪不含任何「PG 上的型別是 uuid」陳述 |

### 六、#224 `DailyEstimatePanel` 的三態：改什麼、驗證到哪裡（只驗證實際改動的）

缺陷来源（讀碼定位）：`settled_estimates()`（定義於 `daily_estimates.py:400`，重建列的 emit 塊在 `:439-459`）對重建列誠實給 `predicted_action=None`（`:454`）且**不 emit** 市場閱讀欄位，而 `_estimate_one`（`:696`，return 塊 `:769-787`，其中 `provider_id`／`data_nature` 在 `:779-780`）兩者都給。舊面板把 `predicted_action` 直接渲染成文字 → 已結算、已引用、已扣過額的那筆估計在客戶畫面上变成一格空白，旁邊也沒有任何話說明它是「沿用」。

修法（本輪實際改的）：文字來源抽出成 `frontend/src/lib/dailyEstimate.ts`（唯一 derivation），面板每張卡呼叫一次（`:300-303`）並只渲染 `display.verdict`／`display.reusedNote`／`display.dataAge`。三態分開：

| 態 | 觸發條件（實測） | 卡上出現 |
|---|---|---|
| 本次估計 | `predicted_action` 有值、`data_age_days` 有值 | ` · <action>`、`N 天前`、**無**沿用說明 |
| 沿用已結算 | `verdict_durable === false` | `（此列未持有結論）`、`資料 age 不在帳本列上`、說明寫明「**原本的 inference_id**」「這次要求**沒有再扣一次額度**」「既不是『今天沒有估計』也不是『本次被拒絕』」＋`計費時點 <billed_at>` |
| 讀不到 | `predicted_action` 為 null 且 `verdict_durable` 未標 false | `（此列未持有結論）`、`無法計算資料age`、**無**沿用說明 |
| 沒有估計／被拒 | `last_estimate` 不存在 | 走 `daily-estimate-refusal-*` 那塊，`message_zh（reason）`，與上面三種不同 testid |

驗證等級（逐級標明，不互相借證）：
- `npx --no-install tsc --noEmit` → **rc=0**（改動後）；`npx --no-install eslint <兩支> ` → **rc=0**。
- `node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyEstimate.test.mjs` → **8/8**；與 `tests/dailyService.test.mjs` 合跑 → **18/18、0 fail、0 skipped**。該檔的第一節點把「兩個产生器發什麼欄」從 `daily_estimates.py` 現讀（`_estimate_one` 的 return 與 `settled.append`），因此後端改欄位名會在此檔紅，不會紅在客戶眼睛裡。
- 前端探針：FE-1 把 lib 的判準換回 `predicted_action == null` → **2 failed（`durability…`、`the three states…`）／6 passed**；FE-2 在面板複製一份沿用文案 → **1 failed（接線節點）**；FE-3 把 `display.reusedNote` 整塊移除 → **1 failed（接線節點）**。三次還原後 hash 分別回到 `5d1ba5e4701c`／`522d773b4a64`，`grep` 無殘留。
- 後端對應形狀：第 3 節那 6 條斷言（在正式 lane 裡跑過，見第七節）。
- **仍然 NOT RUN**：瀏覽器實際渲染（C 包）。本節宣稱只到「derivation 本身＋面板確實經過它＋後端在同一 HTTP 入口發出這個形狀」，**不**是「owner 在畫面上看到過」。

### 七、本輪批次帳本（每批自帶命令／身份／原始輸出／exit／skip；**不得相加**）

**B1′（本輪 headline）**——正式 sqlite lane，**同一 11 檔選取**重跑（理由：本輪改了選取内的 3 支後端檔，前輪那批的內容已不是本候選）：

```
cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh sqlite \
  tests/test_decision_history_export.py tests/test_daily_model_estimate.py \
  tests/test_learning_scheduler.py tests/test_config.py tests/test_auto_collection.py \
  tests/test_simulation_daily_tick.py tests/test_decision_lineage.py \
  tests/test_paper_journal_csv_export.py tests/test_paper_decision_journal_scope.py \
  tests/test_manual_decision_books_linked_portfolio.py tests/test_paper_portfolio.py
```

run dir（5 檔齊：identity.txt／collect.txt／pytest.log／junit.xml／exit-code.txt）：`/var/folders/tk/4hk5dllx6gjdk9ksk7f5v8900000gn/T/alethra-runs/sqlite-20261119T010052Z-10050/`。`identity.txt` 逐欄：git `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（髒樹 porcelain **205** 檔 sha256 `24a82392e4d7…`）；interpreter `backend/.venv/bin/python`；fingerprint `python 3.12.16 | pytest 8.3.3 | sqlalchemy 2.0.36`；target `sqlite /var/folders/…/alethra-sqlite-test-10050.db`；provider `DATA_PROVIDER=demo`；selection-kind `explicit-selection`；**tree-digest before＝after＝`862bae9d79ab733d544c4dfe8fc4fba44c44d6f08955270f1032262462ebb2db`**；`exit-code: 0`。

原始輸出：`collected 292 items` → `289 passed, 3 skipped, 1515 warnings in 206.72s (0:03:26)`；skip census `3 units pg_only=3 live_stack=0 other=0（分類加總＝3）`，逐條記名仍是 `tests/test_manual_decision_books_linked_portfolio.py:1988 / :2034 / :2099`，原因欄一律 `row-lock serialisation requires a real PostgreSQL backend`；`reported units 292 = collected 292 + collection-level 0 -> OK`；unresolved 0／duplicate 0；`evidence gate: ledger closes against this run's own artifacts`；`this run selected specific tests, so the whole-suite ceiling does not apply`。

**差異分解（可加性核對，不是新數字解釋舊數字）**：前輪 289 collected／286 passed／3 skipped → 本輪 292／289／3。新增 **3 支**＝`test_decision_history_export.py` 8→**9**（＋1，#223）＋`test_daily_model_estimate.py` 27→**29**（＋2：#221 那支未命名例外節點屬前輪已計數之外嗎？——本輪現讀該檔 `^def test_` ＝ **29**，前輪記的是 **27**，差 2 支；其中一支是前輪 §一 之後补的視窗狀態節點，另一支是 `:1011`）**；skip 數不變（本輪新增的兩行程檔**不在**這 11 檔選取裡）。**不重跑無變更批次**：本輪未重跑 #207／#208／#195 那些未改動的檔群，其結論沿用前輪。

**兩行程檔的機制節點**（不需要 DB，前輪與本輪同內容）在 sqlite 層會**實際執行**，不在這 11 檔裡；`pytest --collect-only -q tests/test_daily_estimate_two_workers.py` → **2 tests collected**，節點 id 逐字：
```
tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier
tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once
```

**F1（前端批，非後端 lane，不可與 B1′ 相加）**：`tsc --noEmit` rc=0；`node22 … --test tests/dailyEstimate.test.mjs` 8/8；`node22 … --test tests/dailyEstimate.test.mjs tests/dailyService.test.mjs` **18/18**；`eslint` 兩支 rc=0；`npm run build` **rc=0**、`✓ Compiled successfully`、`/training` 35.2 kB／First Load 140 kB、`.next/BUILD_ID = iEzzhWbfCzP7pv7Rb0LcS`（09:05 生成，並列出既有 react-hooks 警告，非本輪檔案）。

**交付時重測（09:13 HKT，不是新一批，只是把本節引用的每個數字再對一次現場）**：第一節 7 支程式／測試檔 `shasum -a 256` 逐檔重跑 → 全部與表中現值相同（`686993e4ef08`／`242b69525a70`／`dfde3025da05`／`4a04d71e6cc9`／`5d1ba5e4701c`／`522d773b4a64`／`f80abd9ec1e8`；記錄檔本身因本節這三處修訂而變，屬預期）；`node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyEstimate.test.mjs` 再跑 → **8 pass／0 fail／0 skipped**；`grep -n` 現讀 `daily_estimates.py` 得 `:400 settled_estimates`／`:439 settled.append`／`:463 estimate_billed`／`:503 pg_advisory_xact_lock`／`:540 window_state`／`:696 _estimate_one`，三支後端測試檔的 `def test_` 計數現讀 **29／9／2**，與表中一致。端口重測：**8000 與 3000 與 8031 與 55432 全部未聆聽**，54340 與 8120 仍在（禁區）。

### 八、對前輪記載的更正（本輪現測推回上文）

1. **前輪第七節第 9 點（本檔第 1426 行）「`DailyEstimatePanel.tsx:374` 對 `recovered` 列顯示空白，本輪未動前端」——本輪已修**，位置變成 `:374-427`（該檔現 615 行）；空白格現在的陳述是「此列未持有結論」＋沿用說明，且既有測試釘住它不能退回空白（FE-1／FE-3）。前輪那句「這是顯示層要補的字，不是資料層假造值的理由」保持有效：本輪**沒有**在帳本列造假結論，`predicted_action` 仍是 `None`。
2. **前輪 §八 D「B1 保持 286 passed／3 skipped」是候選相關值，不是常數**：本候選的對應值是 **289 passed／3 skipped／292 collected**。引用時請連 tree-digest `862bae9d…` 一起引用。
3. **前輪 §八 B「port 3000 FREE／此刻無前端行程」過期**：本輪 09:0x 實測 `pgrep -fl "next-server|next dev"` → **pid 81307 `next-server (v14.2.18)` 在跑**（平行 session 的線，屬禁區：不接、不停、不讀）；`nc -z 127.0.0.1 3000` 仍 **rc=1（未聆聽）**。`pgrep -fl uvicorn` → **pid 55642 …--port 8120**（同上禁區）。`pgrep -fl postgres` → **pid 34471**，PGDATA `…/5062987d/recovery-20261114/int-accept-20261117/pgdata`，port **54340**（禁區）。`/tmp/.s.PGSQL.*` 實測**不存在**。新目錄 `$TMPDIR/alethra-r7-pgdata`、`$TMPDIR/alethra-r7-sock` 實測**不存在**（可安全新建）；55432／8031 量到 **FREE**（rc=1）。PG 捆裝工具 `$PGB/psql --version` → **PostgreSQL 16.2**，`backend/.venv` 內 `import pgserver` → **ModuleNotFoundError**（故 A 包只能用絕對路徑）。
4. **前輪 §八 A 把「兩行程對照」寫成包內一段文字**：本輪它已是**一支有名字的節點**（第七節逐字 id），因此 A 包改為「執行它」而非「實作它」。
5. **前輪 §一 hash／行數本輪全部過期**，以第一節現測值為準。
6. **本輪自我更正**：本輪在寫 #223 探針前一度計畫斷言「沿用情境的 `last_refusal` 是 `None`」；實測崩潰節點同時在 `recovered` 與 `refused` 記名 A，所以該斷言會 false。已改為釘 `reason`＋`message_zh` 那對。**教訓**：形状要先量再寫，不能由「拒絕代表失敗」這個語意推。

### 九、本輪仍未執行／仍未證明（NOT RUN／UNKNOWN，理由各自不同）

1. **PG 兩行程同一視窗競爭**：節點與同步方式已就緒（第七節 id；同步＝每行程在 call tick 前 `Barrier`，並在 `claim_estimate` 入口 publish `Event`、等對手的 `Event`，斷言 `max(claim_enter) < min(estimate_enter)`，跑偏即 fail 而非空洞 pass）→ **未批准 PG ⇒ NOT RUN**。
2. **PG 升級與 `information_schema`／`pg_constraint` 讀回** → NOT RUN（A 包 A4／A5）。
3. **`pg_advisory_xact_lock` 是否足以擋住第二個 worker**：**UNKNOWN**——這正是 A 包要量的那一個問題；本輪不預設答案。
4. **05:00 UTC 由 APScheduler 自己響**：仍 NOT RUN（B 包）。本輪未啟動常駐行程、未改系統時間。
5. **瀏覽器正常客戶旅程**（含三態畫面實際長相）：NOT RUN（C 包）。
6. **stage／commit／push／merge／部署**：未做（D 包）。
7. **對真實帳號啟用或扣額**：未做。本輪全部扣額發生在隔離 sqlite 檔（`alethra-sqlite-test-10050.db`）的測試種子帳號上。
8. **`HumanDecisionEvent` 的 lineage 接通**：仍未做；本輪只把「它不會變成客戶匯出的一列」跑成節點（P6 證明這句是承重的）。
9. **前輪內容的 hunk 級歸屬**（未追蹤檔 +65 行那一類）：**UNKNOWN**，已如第一節標明。
10. **平行 session 實例（54340／8120／next-server 81307）是否受本包影響**：不接、不停、不讀，因此其狀態不在本節任何結論裡。

### 十、四個可分別批准的操作（本輪**修訂後正文**；每檔自帶命令；未量測一律標 UNKNOWN）

#### A. PostgreSQL 包——拆成六個可分別批准、可分別中止的步驟

共同固定值（全部本輪現測）：PG 二進位目錄 `PGB=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin`（`initdb`／`pg_ctl`／`psql`／`pg_isready`／`postgres` present；`psql --version`＝**16.2**）；DB `alethra_r7`；PGDATA `$TMPDIR/alethra-r7-pgdata`（實測不存在）；socket dir `$TMPDIR/alethra-r7-sock`（實測不存在）；port `55432`（實測 FREE）；host 僅 `127.0.0.1`。
**禁区（本包一律不碰）**：pid 34471 的實例（PGDATA `…/5062987d/recovery-20261114/int-accept-20261117/pgdata`、port 54340）、pid 55642 的 uvicorn（8120）、pid 81307 的 next-server——**不接、不停、不讀其 log／資料目錄**。09:13 HKT 重看：這三個 pid 仍在，且 `pgrep -fl postgres` 另有 5 支 `postgres: alethra_app alethra_accept_int5a34 127.0.0.1(<ephemeral>) idle` 的子連線——**平行線此刻正掛在 54340 上工作**，這是 A1 必須在執行當時重跑 `pgrep`／`lsof`（而不是引用本節任何一個 pid）、以及本包堅持獨立 port 55432／獨立 socket dir／`max_connections=25` 的直接理由。
**身份（批准時一併釘住）**：候選＝HEAD `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`＋第一節 8 檔 sha256＋tree-digest `862bae9d79ab733d544c4dfe8fc4fba44c44d6f08955270f1032262462ebb2db`。
**三個角色，權限按實際需要分（不共用、不用 superuser 跑應用）**：`alethra_r7_bootstrap`（`initdb -U` 產生，叢集唯一 superuser，**只**用於 `CREATE ROLE`／`CREATE DATABASE`／`GRANT`，之後不再被任何應用或 alembic 連線使用）；`alethra_r7_migrator`（`alethra_r7` 的 owner，能 `ALTER`／`CREATE`，**非** superuser）；`alethra_r7_app`（LOGIN，僅 `CONNECT`＋`SELECT/INSERT/UPDATE/DELETE`，無 DDL）。密碼一律由 owner 執行時以環境變數提供；不寫入 repo、不寫進本包、不出現在任何輸出（DSN 過 `_safe_dsn` 遮蔽）。

| 步驟 | 只做什麼（命令） | 預期／停止條件 |
|---|---|---|
| **A1 只讀預檢**（先於任何寫入，也先於本包其他步驟自己重跑） | ①`pgrep -fl postgres`；②`lsof -nP -iTCP:55432 -sTCP:LISTEN`；③`ls -d "$PGDATA" "$SOCKDIR"`；④`$PGB/postgres --version`；⑤`git rev-parse HEAD`＋`shasum -a 256`（第一節 8 檔）；⑥`backend/.venv/bin/python -c "import pgserver"` | 逐條結果寫進 `$RUN/identity.txt`。55432 已被聆聽、或兩目錄任一已存在、或 HEAD／sha256／digest 與上列不符 ⇒ **停**，不 initdb、不建角色、不刪任何東西（發現既有目錄時先回報，不覆寫） |
| **A2 initdb＋啟動＋停止**（只建叢集，不建 DB、不建表） | `$PGB/initdb -D "$PGDATA" -U alethra_r7_bootstrap -A scram-sha-256 --pwfile=<owner 提供，0600>`；`$PGB/pg_ctl -D "$PGDATA" -o "-p 55432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB" start`；`$PGB/pg_isready -h 127.0.0.1 -p 55432` | 只允許這一個 PGDATA。**正常停止**＝`$PGB/pg_ctl -D "$PGDATA" -m fast stop`；停止後以 `pgrep`/`lsof` 對**本輪抓到的 pid** 確認已退出（**不**靠已停端點的 HTTP 回應證明停止）。資源上限釘 `max_connections=25`／`shared_buffers=64MB`，避免與 54340 那個實例搶機器 |
| **A3 建 DB＋三個角色＋授權** | 以 bootstrap 連線：`CREATE ROLE alethra_r7_migrator LOGIN;`、`CREATE ROLE alethra_r7_app LOGIN;`、`CREATE DATABASE alethra_r7 OWNER alethra_r7_migrator;`、`GRANT CONNECT, CREATE ON DATABASE alethra_r7 TO alethra_r7_migrator;`、`GRANT CONNECT ON DATABASE alethra_r7 TO alethra_r7_app;` | 讀回核對：`SELECT rolname, rolsuper FROM pg_roles WHERE rolname LIKE 'alethra_r7%';` ⇒ **三列 `rolsuper=f`**，且不存在任何以 `alethra_r7_app`／`alethra_r7_migrator` 為 superuser 的情況；`SELECT current_user, usesuper FROM pg_user WHERE usename = current_user;`（以 **app** 連線）⇒ `usesuper = f`。不符 ⇒ 停，不升級 |
| **A4 升級（migrator 連線）** | `cd backend && alembic current`（先只讀，進身份檔）→ `alembic heads`（讀口徑）→ 與 `scripts/check_migration_tail.py:54-59` 的批准字面值比對，期望 tail＝**`c3d4e5f6g7h8`**（鏈：`e1f2a3b4c5d6 → f2a3b4c5d6e7 → b2c3d4e5f6g7 → c3d4e5f6g7h8`）→ 比對通過才 `alembic upgrade c3d4e5f6g7h8`，隨後 `alembic current` 再讀一次 | 新建 DB 的 `alembic current` **起始值＝UNKNOWN（本機從未在任何 PG 上讀過）**，故 A4 的預期寫成「空 DB 應回報無版本」；若回報任何既有版本、或多於一個 head、或 head ≠ 批准字面值 ⇒ **停**，不 `--autogen`、不 downgrade、不 drop |
| **A5 schema 讀回（app 連線，唯讀）** | `SELECT data_type FROM information_schema.columns WHERE table_name='decision_records' AND column_name='linked_inference_id';` ⇒ 期望 `uuid`；`SELECT conname, confrelid::regclass FROM pg_constraint WHERE conrelid='decision_records'::regclass;` ⇒ 期望只有既有三條 FK，且 `linked_inference_id` **查無 FK**；`SELECT indexname FROM pg_indexes WHERE tablename='decision_records';`；另外讀 `human_decision_events` 與 `usage_records` 的 `information_schema` 欄位型別，與第五節③那張「離線建表讀回」逐欄對比 | 這是五級證據裡的 ⑤。差異逐條列出（不解釋成「等價」）；`linked_inference_id` 出現 FK ⇒ 停 |
| **A6 正式 lane＋競爭節點執行** | `cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh pg tests/test_daily_estimate_two_workers.py tests/test_decision_history_export.py tests/test_daily_model_estimate.py` | runner 接受 `路徑::節點id` 形式的選取（`scripts/run_tests.sh:277-279` 以 `${arg%%::*}` 是否存在判斷，`:378` 把 `::` 计入 collected-node-ids），因此**逐節點**命令同樣可用；A6 建議至少把這兩支逐字點名：`tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once`、`tests/test_daily_model_estimate.py::test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice`（前者是互斥，後者是同程序雙交易）。斷言以**持久帳本＋唯一業務鍵**為準：每 (owner, skill, instrument, as_of) 恰好 1 列 `via=daily_service`；每列對應恰好 1 筆 `settled` 預留；**不得有非終態預留**（`reserved` 必须為 0——失敗的預留在 `release_reservation` 走 `released` 終態，`entitlements.py:452`）。兩個 job 摘要的 `billed_in_window` **不相加**（各自是同一帳本在瞬間的讀值，`daily_estimates.py:1006`），唯一視窗總數只引 row count。本層若 skip 數為 0 才代表真跑過；跑完由節點自己在 `finally` 依 owner 刪自己那筆並以第二條連線讀回零殘留（該檔第 55-60 行說明了為什麼 fixture rollback 不夠） |

A 包不刪 PGDATA、不 `dropdb`、不 `DROP ROLE`；失敗保留現場給 owner 覆核，清場需另行批准。

#### B. 服務與真實 05:00 cron 包

| 欄位 | 值（區分「已量」與「待量」） |
|---|---|
| 確切程序（唯一一個） | `cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8031 --log-level info`，啟動前 `pgrep -fl uvicorn` 必須只剩 8120 那支（禁區），啟動後立刻把**本輪抓到的 pid** 寫進 `$RUN/service-pid.txt` |
| 綁定 | 僅 `127.0.0.1:8031`；port 8031 本輪 09:0x 實測 **FREE**（`nc -z … rc=1`）。不共用 8120、不複用其 log |
| 隔離 DB 身份 | 一個**新**隔離 sqlite／或 A 包批准後的 `alethra_r7`（若走 sqlite，`DATABASE_URL` 指向 `$TMPDIR/alethra-r7-cron.db`，實測不存在）；不使用受保護 DB，不使用 54340 那個實例 |
| 掛上的 lane（**只一支**） | `ENABLE_LEARNING_SCHEDULER=true`＋`LEARNING_SCHEDULER_JOBS=daily_model_estimate`；其餘三支 lane 不掛。核對點是**註冊結果**而非設定字串：`registered_lane_ids()` 必須只回 `daily_model_estimate`（`#214` 那支「allow-list 未設不自掛」節點已釘此行為） |
| 其他 jobs／開關明確關 | `AUTO_COLLECTION_ENABLED=false`，並以 `GET /api/v1/admin/collection/status`（`app/api/admin.py:89`）的 `enabled`／`running` 兩欄讀回；`LIVE_TRADING_ENABLED=false`（`app/config.py:172`），且**不執行** `app/services/live_trading_daemon.py:471 main()`／`:549`——舊 daemon 兩個入口都要核為未開 |
| 資料與模型來源 | `DATA_PROVIDER=demo`，**不外呼 provider**；模型＝1 支測試種子個人模型版本（該測試帳號自己名下），不碰任何真實帳號的版本 |
| 測試帳號與扣額上限（**真的有上限**） | 1 支測試 owner＋1 支技能帳號＋1 支測試 symbol；該 owner 的 `ai_usage` limit 釘在 **1**（超出時閘門在 `reserve_quota` 就 429，`quota_exceeded`），這樣「一天一筆」不是靠去重而是靠額度上限被**實際強制**；每 tick 扇出 `LEARNING_SCHEDULER_MAX_OWNERS=1` |
| 只觀察自然觸發 | 只等 **05:00 UTC（UTC+8 13:00）自己響一次**。**手動 tick**（`POST /api/v1/personalization/daily-estimate/run`，`personalization.py:449`）與**抹除 `result_json` 再跳**（摘要遺失的現場版）**分開列**，兩者不算 05:00 證據，且都需各自批准；**不抹 `result_json` 於 05:00 那一輪**、**不改系統時間** |
| 要讀到的客戶結果 | `learning_jobs` 出現 `job_type=daily_estimate`＋`config_json.window=<當日>`；`usage_records` 恰好 1 列 `via=daily_service`；`GET /api/v1/personalization/daily-estimate/status`（`:420`）的 `last_estimate.inference_id` 指回同一列；訂單數 0、`human_decision_events` 與 `decision_records` **不因排程增加**；replay 進度不推進 |
| 尚未驗證的條件不得寫成已證明 | 「同視窗不重複扣額」在本包的地位是**待觀察的預期**（其證據目前只有 sqlite lane 的單進程節點與 B1′）；`pg_advisory_xact_lock` 的兩行程互斥在 A 包 A6 之前屬於 **UNKNOWN**，本包不引用 |
| 日誌與停止 | stdout／stderr → `$TMPDIR/alethra-r7-service.log`（以 `lsof -p <本輪 pid>` 確認實際落點）；停止＝對**本輪擷取的 pid** 發 `TERM`，之後以 `pgrep -f "uvicorn app.main:app --host 127.0.0.1 --port 8031"` 與 `lsof -iTCP:8031` 確認；**不**在程序死後用同一個已停端點的 HTTP 回應當作停止證據；停止不等於刪資料 |

#### C. Browser 包（owner 自己點；代理不代替同意）

固定候選（進瀏覽器前逐條核對，不符即停）：HEAD `5a34e3fed0f2…`；前端 build 身份 `.next/BUILD_ID = iEzzhWbfCzP7pv7Rb0LcS`（本輪 09:05 `npm run build` rc=0、`✓ Compiled successfully`、`/training` 35.2 kB／140 kB）；API base＝`frontend/src/lib/api.ts:11` 的 `process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'`（**已量，2026-11-19 09:13 HKT**：`lsof -nP -iTCP:8000 -sTCP:LISTEN` → **0 列**，即本機 8000 目前無人聆聽，故 C 包进场時必須先起一個本輪自己的後端，或把 `NEXT_PUBLIC_API_URL` 釘到 B 包的 8031；進場前仍要**現測重跑一次**这条，並在啟動前後各印一次程序清單）；前端 URL＝`http://localhost:3000/training`（3000 本輪實測未聆聽，但機器上另有平行線的 next-server pid 81307，**不接、不停、不讀**，必要時改用其他 port 並在包內重釘）；後端＝本包 B 或另批的 8031／8000；DB＝與該 API base 一致的隔離 DB；正式測試帳號＝以**密碼**登入的測試 owner（`actor_type` 會讀成 `human`，`app/auth/provenance.py:30`）。

| 客戶動作 | 真實入口（已量） | 預期 |
|---|---|---|
| 開每日估計同意、勾 1 支標的 | `PUT /api/v1/ml/personalization/daily-estimate/consent`（`personalization.py:302`） | 201／200；`GET .../consent`（`:410`）讀回 `scope_json.skill_units[]` 的 instrument 與 `max_data_age_days`；面板 `daily-estimate-enabled-*` 轉為「每日執行中」 |
| 讀回一次估計 | `POST .../daily-estimate/run`（`:449`）→ `GET .../status`（`:420`） | `daily-estimate-last-*` 卡上有結論、`N 天前`、`inference_id`；**無** `daily-estimate-reused-*` |
| **恢復態（本輪修的三態之一）** | 同一面板，第二次要求 | `daily-estimate-reused-*` **出現**；卡上 `（此列未持有結論）`＋`資料 age 不在帳本列上`；`inference_id` 與第一次**同一個**（原本的 inference id）；`usage_records` 該業務鍵**仍 1 列**（沒有新扣額）；`last_refusal.reason=="already_estimated_this_window"`；`daily-estimate-refusal-*` 與 reused 塊**同時可見且語意不互相覆蓋** |
| 撤回一支／全部 | `DELETE .../consent`（`:362`） | 之後该 skill 的工作以 `consent_withdrawn` 具名拒絕；已在跑的那筆結果與額度不退；面板與 `status` 一致 |
| 記一條決策（連結該估計） | `POST /api/v1/paper-portfolio/{id}/decisions` 帶 `linked_inference_id` | 201；列表與 `GET` 讀回同一 id |
| 匯出決策歷史 | `GET /api/v1/paper-portfolio/{id}/decision-history?format=csv`（及 `json`） | CSV 可下載並在試算表開啟；有訂單／無訂單決定都在；`inference_source_state=="present"`；同夾交易 journal **不因無訂單決策多列**；**`human-decisions` 那兩筆 id 不得出現在檔內**（#223 的畫面版） |
| 切版後的舊估計 | 先切模型版本再看那張卡 | `daily-estimate-version-drift-*` 出現（「不是你現在所選模型的輸出」），且**不**把它當成現在所选模型的輸出 |

邊界（報告必附）：browser-use 自動化只能作可用性輔助觀察，**不得**由代理替 owner 按下同意／撤回——同意是 owner 的政策行為，代理的 click 不構成客戶驗收；build PASS／node:test PASS 都不代替 browser PASS。本包 **NOT RUN**。

#### D. Git 保存與選單重釘包（修訂）

- **base**：branch `live1-impl`，HEAD `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`；工作樹 porcelain **205** 檔（sha256 `24a82392e4d7…`）。
- **哪些能力已在 base、哪些必須一併進入（本輪以 HEAD blob 現讀，不是憑印象）**：

| 能力 | HEAD 裡有嗎（實測） | 對 HEAD 的狀態 | 結論 |
|---|---|---|---|
| `daily-estimate` 四條路由（`personalization.py`） | **0 hits** | `M`，7 hunk | 屬本輪範圍，**必須進入** |
| 共享推論閘門 `_run_personal_prediction`（`personal_models.py`） | **3 hits** | `M`，47 hunk | 閘門**已在 base**；本輪只需其計價順序／讀回相關 hunk，47 hunk 屬多輪累計，不得算進本輪授權 |
| 客戶 client（`frontend/src/lib/api.ts` 的 `dailyEstimateConsent`／`dailyEstimateStatus`） | **0 hits** | `M`，14 hunk | **必須進入**（未進則面板無資料來源） |
| UI 掛載（`frontend/src/app/training/page.tsx` 引 `DailyEstimatePanel`） | **0 hits** | `M`，2 hunk | **必須進入** |
| 決策歷史讀門（`paper_portfolio.py`） | 存在（檔本身在 HEAD） | `M`，26 hunk，本輪未動 | 本輪**不**新增其 hunk；只依賴前輪已記錄的 `:1723` owner 約束 |
| `human_decision_events` 那道門（`human_decisions.py`）、`billing.record_api_call_no_commit`、`entitlements.settle_reservation` | 1／1／1 hits，且 `git status` **未列**（乾淨） | 已在 base、未改 | 本輪證據只**讀**它們，不改，故不在保存範圍 |
| 四支未追蹤 revision 檔（`e1f2a3b4c5d6`／`f2a3b4c5d6e7`／`b2c3d4e5f6g7`／`c3d4e5f6g7h8`） | **不在 base**（`??`） | 新檔 | 若本包只保存每日估計相關檔，請注意：`c3d4e5f6g7h8` 是 A4 的 head、`b2c3d4e5f6g7` 等屬前輪范围——**歸屬不清的共享內容不隨包保存**，需逐檔裁決 |

- **本輪增量（要保存的就是第一節 8 檔＋上面三列「必須進入」的既有檔 hunk）**：後端 4 檔（`daily_estimates.py`、`test_daily_model_estimate.py`、`test_decision_history_export.py`、`test_daily_estimate_two_workers.py`）＋前端 3 檔（`dailyEstimate.ts`、`tests/dailyEstimate.test.mjs`、`DailyEstimatePanel.tsx`）＋本記錄檔。逐檔 `git add`，不用 `git add -A`／`.`。
- **依賴閉包（可復現證據，非自述）**：`daily_estimates.claim_estimate()/estimate_billed()/settled_estimates()` ← `_estimates_for_owner`（`:912-926`、`:1006`）← `run_daily_estimate_tick` ← `personalization.py:420/:449` ← `frontend/src/lib/api.ts:887/:905` ← `DailyEstimatePanel.tsx` ← `src/lib/dailyEstimate.ts` ← `frontend/tests/dailyEstimate.test.mjs`；後端形狀的另一端由 `test_daily_model_estimate.py`（29 節點）釘；#223 端由 `test_decision_history_export.py`（9 節點）釘，其 fixture 依賴 `tests/test_human_decision_recording.py` 的 `_create_experiment`／`_lock_experiment`／`_DEMO_ALPHA_TRADING_DAYS`（`_seed_bars` 走 `synthetic` provider）。**8 檔一起才自洽**：只保存測試會紅（`claim_estimate` 不存在）；只保存本體則 6 條 HTTP 形狀斷言與前端 8 節點會失配；不保存 `api.ts`／`training/page.tsx` 則面板在 build 裡是死的（build rc=0 不能代替接線）。
- **測試清單與 skip 預算重釘的實際差異（先講，不事後解釋；不以 explicit-selection 代替整條 lane）**：本輪 B1′ 是 `explicit-selection`，命令見第七節；同內容拿去重釘 `tests/collected_nodes.txt` 會把 `collected` 由 289→292（逐檔：`test_decision_history_export.py` 8→9、`test_daily_model_estimate.py` 27→29）、`passed` 286→289、skip **仍 3**（`pg_only=3 live_stack=0 other=0`，記名仍是 `test_manual_decision_books_linked_portfolio.py:1988/:2034/:2099`）。**影響**：整套 ceiling 在 explicit-selection 下「報而不 enforce」（本輪原始輸出有這行）；若要把上面 `ceiling`／skip 預算正式重釘，必須跑**整條 lane**（`./scripts/run_tests.sh sqlite` 不帶選取），那是另一個可批准的操作，本包未含、本輪未跑。新檔 `test_daily_estimate_two_workers.py` 的 2 支節點不在上述 11 檔選取裡，因此 292 不含它；在 sqlite 層它會執行機制節點、具名 skip 競爭節點（原因文字＝`the seat being raced is pg_advisory_xact_lock, which SQLite has no way to take; …`）。
- **排除**：`/tmp/alethra-runs/**`、`$TMPDIR/alethra-*`、`*.db`、`.next/**`、`__pycache__/**`、`backend/.env*`、生成／釘選檔（`collected_nodes.txt`、`skip_budget.json`）——後兩者只能由一次整條 lane 執行後 `pin --ack-diff` 產生。
- **狀態**：本輪**未 stage、未 commit、未 push、未 merge、未部署**，未建 branch、未動 remote；批准後第一步是逐檔 `git add`＋`git status` 覆核實際進入內容，與第一節不符（或 remote 指向本地路徑）即停。


## 第八輪（2026-11-19 09:14–09:4x HKT）：先交 A／B／C 三份可執行正文；第七輪那包 PostgreSQL 經本機安全閘核對後**不可執行**，已更正

本節是同一交付的未完成條件，不是新一輪開工請示。**保留**：B1′（292 collected／289 passed／3 skipped／exit 0，tree-digest `862bae9d…`）、P4、#223、#224、兩行程 harness 的機制節點結果。**不重跑無變更批次。**

### 一、更正：第七輪 §十 A 的 port 55432／DB `alethra_r7` 会被正式 lane 自己的白名單拒絕（本輪現讀，行號為實測）

`./scripts/run_tests.sh pg` 在 `MODE=pg` 時要求 `ALETHRA_PG_TEST_URL`，未設即 `exit 2`（`backend/scripts/run_tests.sh:141-146`），設了才 export 為 `DATABASE_URL`／`ALETHRA_TEST_DB`／`ALETHRA_PG_DATABASE_URL`／`ALETHRA_TEST_POSTGRES_URL`，並把 `ALETHRA_TEST_REQUIRED_DB_PREFIX` 預設為 `alethra_`（`:147-152`）。非 sqlite 時 `tests/conftest.py` 匯入期即跑兩道閘：
- `tests/conftest.py:79-100`（`_validate_pg_test_target`）：host 只准 `localhost`／`127.0.0.1`／`::1`／`/tmp`；**DB 名必須含 `test`**；`ai_investment_lab`／`alethra_production`／`alethra_prod` 永久拒絕。
- `test_safety_gate.py` 的 `verify_test_database`（conftest `:110-124` 呼叫）：`TEST_MODE` 必須為 true（`:179-183`）、DB 名要命中白名單 **`ALLOWED_TEST_DATABASES = {ai_investment_lab_test, alethra_test, test_ai_investment_lab, alethra_owner_test}`（`:25-30`）**、`(host, port)` 要命中 **`ALLOWED_INSTANCES = {(localhost,5432), (127.0.0.1,5432), (::1,5432)}`（`:41-45`）**、連線後以 `pg_roles` 讀 `rolsuper, rolcreatedb, rolcreaterole, rolbypassrls`（`:110`）並在特權時硬失敗。

**结论（不解釋成「等價」）**：`port 55432` 或 `DB alethra_r7` 的任何組合都過不了 `ALLOWED_INSTANCES`／`ALLOWED_TEST_DATABASES`，所以第七輪 A 包若照抄執行，会在 pytest 還沒开始就死在 conftest 匯入期。**兩條出路，本節選第一條：**
1. **（本包採用）不動安全閘**：一次性叢集改用 `127.0.0.1:5432` ＋ DB `alethra_owner_test`（同時滿足含 `test`、前綴 `alethra_`、白名單三條），以獨立 PGDATA／獨立 socket dir 承載；進場前若 5432 已被聆聽、或 `/tmp/.s.PGSQL.5432` 已存在（代表別線已在此 port 起叢集），**立刻停，不改白名單、不共用他人叢集**。
2. （**不採用，需 owner 另批**）放寬白名單加入 55432／新 DB 名——這是擴大一個安全檔的准入集合，屬政策裁決，代理不自行修改 `test_safety_gate.py`。

代價如實講：佔用 TCP 5432 会让本機預設 5432 socket 的 pgserver 工具在那段時間內连不上；因此 A-INIT 的生命週期寫成「起→測→**停**」，且停止不等於刪資料。

### 二、A 包（更正後）：三個可分別批准、可分別中止的操作

共同固定值（本輪 09:2x 現測）：

```sh
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
PGB=$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin   # 34 支工具，psql/initdb = PostgreSQL 16.2（現測）
PGDATA=${TMPDIR:-/tmp}/alethra-r8-pgdata      # 現測：不存在
SOCKDIR=${TMPDIR:-/tmp}/alethra-r8-sock       # 現測：不存在
PORT=5432                                     # 安全閘唯一准許的 port（conftest/test_safety_gate 現讀）
DB=alethra_owner_test                         # 白名單內、含 test、前綴 alethra_
BOOT=alethra_r8_bootstrap                     # initdb -U 產生，叢集唯一 superuser
MIG=alethra_r8_migrator                       # DB owner，無任何rol*特權；alembic 與正式 lane 用它
APP=alethra_r8_app                            # 僅 DML；B 包的 uvicorn 用它
RUN=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r8-run-XXXXXX")
```

秘密的來源與邊界（本包唯一憑證規則）：代理的 Bash **沒有 TTY**，因此三個密碼檔只能由 **owner 在自己的 Terminal** 建立，檔名與權限固定、內容永不进入代理视野：

```sh
# —— 以下三行由 owner 自己執行，不是代理執行 ——
umask 077
for r in bootstrap migrator app; do
  printf '%s' "$(openssl rand -base64 24)" > "${TMPDIR:-/tmp}/alethra-r8-pw.$r"
done
printf '%s' "export ALETHRA_PG_TEST_URL=postgresql://alethra_r8_migrator:<MIG密码>@127.0.0.1:5432/alethra_owner_test" \
  > "${TMPDIR:-/tmp}/alethra-r8-dsn.sh"
```
代理只以 `test -f` 與 `stat -f %Lp` 檢查**存在與模式**（非 0600 即停，檔在 repo 內即停），使用方式一律是子 shell 內 `. "$FILE"` 後立刻執行命令，任何輸出只准出現经 `tests/conftest.py:68-76 _safe_dsn` 遮蔽後的 DSN 形态；**不 `cat`、不 `echo`、不写進 repo、不進 run dir**。

#### A-INIT（＝第七輪 A1＋A2＋A3 合併；一次批准、任一步不符即中止且不回滾他人資料）

```sh
# 0) 唯讀預檢（先於任何寫入，也先於本包其他步驟自己重跑）——全部結果寫入 $RUN/identity.txt
pgrep -fl postgres
lsof -nP -iTCP:5432 -sTCP:LISTEN                       # 必須 0 列
ls -l /tmp/.s.PGSQL.5432 2>/dev/null                    # 必須不存在
ls -d "$PGDATA" "$SOCKDIR" 2>/dev/null                  # 必須都不存在
"$PGB/initdb" --version; "$PGB/psql" --version          # 必須都是 16.2
cd "$REPO" && git rev-parse HEAD                        # 必須 5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a
for f in <第一節 8 檔>; do shasum -a 256 "$f"; done     # 必須與本記錄現值逐字相同
stat -f '%N %Lp' "${TMPDIR:-/tmp}"/alethra-r8-pw.*      # 必須全部 600
```
**中止條件（逐條，任一命中即停且不做後續任何寫入）**：5432 已被聆聽／`/tmp/.s.PGSQL.5432` 存在／兩目錄任一已存在（**先回報，不覆寫、不刪除**）／版本不符／HEAD 或 sha256 不符／密碼檔模式不是 600 或落在 repo 內。此時的正確結語是 **NOT RUN**，不是失敗的測試。

```sh
# 1) 只建叢集（不建 DB、不建表）
"$PGB/initdb" -D "$PGDATA" -U "$BOOT" -A scram-sha-256 --pwfile="${TMPDIR:-/tmp}/alethra-r8-pw.bootstrap"
mkdir -p "$SOCKDIR" && chmod 700 "$SOCKDIR"
"$PGB/pg_ctl" -D "$PGDATA" -l "$RUN/pg.log" -w start -o \
  "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB"
"$PGB/pg_isready" -h 127.0.0.1 -p 5432
```
`initdb` 非零 ⇒ 停（保留 `$RUN/pg.log`，不重試第二次、不刪 PGDATA）。`pg_ctl` 非零或 `pg_isready` 不 ready ⇒ **停在此處**，正常停止程序為 `"$PGB/pg_ctl" -D "$PGDATA" -m fast stop`，停止後以 0 步抓到的 pid 用 `pgrep`／`lsof` 核對已退出（**不**以已停端點的 HTTP 回應證明停止）。資源上限釘死 `max_connections=25`／`shared_buffers=64MB`，避免與 54340 那個實例搶機器。

```sh
# 2) 建 DB＋三個角色＋授權（以 BOOT 連線；這是最後一次使用 superuser）
. "${TMPDIR:-/tmp}/alethra-r8-dsn.sh"   # 只為讓本步驟之後的 psql 有 DSN 可讀，BOOT 段用 pwfile
PGPASSWORD="$(cat "${TMPDIR:-/tmp}/alethra-r8-pw.bootstrap")" "$PGB/psql" -h 127.0.0.1 -p 5432 -U "$BOOT" -d postgres -v ON_ERROR_STOP=1 -c \
 "CREATE ROLE $MIG LOGIN PASSWORD '`cat ${TMPDIR:-/tmp}/alethra-r8-pw.migrator`';"   # 逐條分號執行，失敗即停
#  其後同形：CREATE ROLE $APP LOGIN PASSWORD ...; CREATE DATABASE $DB OWNER $MIG;
#           GRANT CONNECT, CREATE ON DATABASE $DB TO $MIG;   GRANT CONNECT ON DATABASE $DB TO $APP;
#           \c $DB 後：GRANT ALL ON SCHEMA public TO $MIG;  ALTER SCHEMA public OWNER TO $MIG;  REVOKE ALL ON SCHEMA public FROM PUBLIC;
```
PG 15+ 之後 `public` schema 預設不給 PUBLIC 建表權，本機是 16.2 ⇒ 上述 `ALTER SCHEMA public OWNER TO $MIG` 是 alembic 能落地的**必要授權**（不是「預防性多給」）。讀回核對（全部結果進 `$RUN/roles.txt`）：

```sql
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname LIKE 'alethra_r8%';
SELECT datname, pg_get_userbyd(datdba) FROM pg_database WHERE datname = 'alethra_owner_test';
SELECT current_user, usesuper FROM pg_user WHERE usename = current_user;   -- 分別以 MIG 與 APP 各跑一次
```
預期：`$BOOT` 一列 `rolsuper=t`；**`$MIG` 與 `$APP` 兩列的四個權限旗標全為 `f`**（`test_safety_gate.py:110` 查的就是這四欄）；`alethra_owner_test` 的 owner 是 `$MIG`；以 MIG／APP 連線時 `usesuper=f`。任一不符 ⇒ 停，不進 A-MIG。BOOT 之後不再被任何應用或 alembic 連線使用。

#### A-MIG（原 A4）：鏈與目標先核對，再升級

```sh
cd "$REPO/backend"
. "${TMPDIR:-/tmp}/alethra-r8-dsn.sh"; export TEST_MODE=true
alembic current            # 新 DB：預期回報「無版本」；實際起始值＝UNKNOWN（本機從未在任何 PG 讀過）
alembic heads              # 讀伺服器可解析的 head
python scripts/check_migration_tail.py --json      # :243-256；讀**檔案系統**的 ScriptDirectory，不連 DB
```
待升級鏈與固定目標（`scripts/check_migration_tail.py:54-58` 的字面值，本輪現讀）：**`e1f2a3b4c5d6 → f2a3b4c5d6e7 → b2c3d4e5f6g7 → c3d4e5f6g7h8`**，head 必須等於 **`c3d4e5f6g7h8`**。三個停下來條件：`alembic current` 回報任何既有版本、`heads` 多於一個、head ≠ 上述字面值（`DUPLICATE_REVISION`／`BRANCHED_TREE`／`MISSING_DEPENDENCY` 任一狀態也算不符）。比對全過才執行 **`alembic upgrade c3d4e5f6g7h8`**，隨後 `alembic current` 再讀一次並記進 `$RUN/alembic.txt`。**不 `--autogen`、不 downgrade、不 drop、不執行任何其他 migration**；失敗保留現場，清場需另行批准。

#### A-READ（原 A5，五級證據裡的 ⑤）：以 MIG 連線唯讀核對實際 schema

```sql
SELECT data_type FROM information_schema.columns WHERE table_name='decision_records' AND column_name='linked_inference_id';   -- 期望 uuid
SELECT conname, confrelid::regclass FROM pg_constraint WHERE conrelid='decision_records'::regclass;                          -- 期望只有既有 FK；linked_inference_id 出現 FK ⇒ 停
SELECT indexname, indexdef FROM pg_indexes WHERE tablename IN ('decision_records','human_decision_events','usage_records');
SELECT table_name, column_name, data_type FROM information_schema.columns
 WHERE table_name IN ('usage_records','learning_jobs','quota_reservations') ORDER BY table_name, ordinal_position;
```
逐欄與第八輪前已交的「離線建表讀回」對照，差異逐條列出（**不解釋成等價**）。

#### A6：兩行程競態節點（本包唯一能回答「有沒有可靠互斥」的步驟）

節點 id（逐字，現測 `pytest --collect-only -q tests/test_daily_estimate_two_workers.py` ＝ 2 items）：
- `tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier`
- `tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once`（`:376` 的 `skipif(TEST_ENGINE.dialect.name=="sqlite")` 使它在 sqlite 層**具名跳過**，原因文字 `the seat being raced is pg_advisory_xact_lock, which SQLite has no way to take; …`）

命令（正式 lane 入口，逐節點選取被 `scripts/run_tests.sh:277-279`／`:378` 支持）：

```sh
cd "$REPO/backend" && . "${TMPDIR:-/tmp}/alethra-r8-dsn.sh" \
 && DATA_PROVIDER=demo ./scripts/run_tests.sh pg \
   tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier \
   tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once \
   tests/test_daily_model_estimate.py::test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice
```

同步屏障與等待（全部現讀自該檔，不是設計願景）：start method ＝ `mp.get_context("spawn")`；`ready = ctx.Barrier(2)`（`:218`、`:418`）；兩支 `ctx.Process(name="harness-*")`；每支在 `claim_estimate` 入口 `arrived[slot].set()` 後 `peer_arrived.wait(timeout=_TIMEOUT_S)`，**`_TIMEOUT_S = 60`**（`:127`）；父程序 `proc.join(timeout=_TIMEOUT_S)`、另一段用 `proc.join(timeout=4 * _TIMEOUT_S)`（`:233`／`:434`）；結果走 `out.get(timeout=10)`（`:238`／`:443`）；child 清理走 `finally:` → `proc.terminate()` → `proc.join(timeout=10)`（`:568-572`）。連線層等待另釘 `SET lock_timeout = '30s'`／`statement_timeout = '120s'`／`idle_in_transaction_session_timeout = '90s'`（`:186-191`）。

**如何證明第二支是在第一支仍持鎖時抵達**：節點斷言 `max(claim_enter) < min(estimate_enter)`（每支自己記的時間戳），也就是**兩支都已站到 `claim_estimate` 之後才允許任何一支進入計價**；跑偏（例如第二支根本沒等到對手）時該斷言失敗，而不是空洞 pass。釋放條件由 PostgreSQL 自己決定：座位是 `pg_advisory_xact_lock(hashtextextended(owner|skill|instrument|window))`（`daily_estimates.py:500-507`），它在「帳本列＋額度結算」那次 commit（`personal_models.py:695`）結束時釋放，第二支醒來後在**同一鎖內**重讀帳本（`:463 estimate_billed` → `:400 settled_estimates`）⇒ 讀到的就是第一支已提交的帳本。

**對帳以持久業務鍵**（不靠兩份摘要）：每 (owner, skill_unit_id, instrument_id, as_of_date) 恰好 1 列 `metadata["via"]="daily_service"`；每列對應恰好 1 筆 `settled` 預留；**非終態預留必須為 0**；落敗那支的 `learning_jobs.result_json.refused` 必須以 instrument id 具名 `already_estimated_this_window`。**兩個 job 摘要的 `billed_in_window` 不相加**（`:1006` 那行是同一帳本在不同瞬間的讀值），窗口總數只引用 row count。

**額度不得掩蓋競態（本輪新增的前提，也是第七輪正文缺的一條）**：測試帳號無 `PilotEntitlement` 列 ⇒ 走 free plan `ai_usage_limit = 10`（`app/auth/entitlements.py:66-71`；有列時讀該欄，預設 20，`app/models/pilot.py:97`），超額時 `reserve_quota` 429（`:252-285`）。A6 的扇出是 2 支 instrument × 2 行程 ⇒ 最坏 4 次成功計價，**10 的上限不會把「錯誤的第二次扣額」變成 `quota_exceeded`**，所以重複會以**第二列帳本**現身而不是以拒絕現身。執行前的硬性前提（寫進 run dir，不是口頭保證）：`SELECT limit FROM …` 讀到的有效上限 ≥ 4，且**節點判准以 row count 為準**；若未來誰把該帳號額度降到 1，A6 立刻失去鑑別力——那正是 brief 點名的掩蓋。「一天一筆」的成本上限屬於 **B 包**（真實 cron），不是 A6 的判準。
**中止／失敗處理**：若 60s 等待超時、`Barrier` 未湊齊、或時間戳斷言失败 ⇒ 該次執行是 **NOT RUN／INVALID**，不得寫成 PG 競態 PASS；資料與叢集保留現場給 owner 覆核，節點自身的 `finally` 只依 owner 刪自己這筆並以第二條連線讀回零殘留（該檔 `:55-62` 說明為什麼 fixture rollback 在 PG 層不夠），**不刪 `market_data_bars`**（無 owner 欄可 Scope，屬共享種子）。
**边界**：SQLite 跳過 advisory lock 的結果**不作 PG 證據**；未批准前該節點仍是 **NOT RUN**。

### 三、B 包（補全啟動／停止／前端／帳號／網路限制／逐步判準）

| 項目 | 可執行值（已量者註出處；未量者標待量） |
|---|---|
| 後端啟動（唯一一支） | `cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8031 --log-level info`；啟動前 `pgrep -fl uvicorn` 只准出現禁區的 8120；啟動後立刻把**本輪抓到的 pid** 寫進 `$RUN/service-pid.txt`（port 8031 本輪 09:13 實測未聆聽） |
| 後端停止 | `kill -TERM "$(cat $RUN/service-pid.txt)"` 之後以 `pgrep -f "uvicorn app.main:app --host 127.0.0.1 --port 8031"` 與 `lsof -nP -iTCP:8031 -sTCP:LISTEN` 核對；**不**用同一個已停端點的 HTTP 回應證明停止；停止不等於刪資料 |
| 日誌落點 | 啟動時 `>> "$TMPDIR/alethra-r8-service.log" 2>&1`，並以 `lsof -p <本輪 pid>` 實際確認 stdout／stderr 落點（不是宣稱） |
| DB 隔離 | 要嘛 sqlite：`DATABASE_URL=sqlite:///$TMPDIR/alethra-r8-cron.db`（現測不存在）；要嘛 A-INIT 之後的 `alethra_owner_test`＋**APP 角色 DSN**（應用永不使用 superuser，也不使用 MIG）。不使用受保護 DB，不连 54340 |
| 只掛一支 lane | `ENABLE_LEARNING_SCHEDULER=true` ＋ `LEARNING_SCHEDULER_JOBS=daily_model_estimate`。為必須：未設 allow-list 時會用 `DEFAULT_LEARNING_SCHEDULER_LANES`（`app/config.py:55/:211`），而相鄰兩 lane 的 cron 是 02:00／03:00（`app/workers/learning_scheduler.py:81-82`，掛載 `:152/:162`）⇒ 會把 research／replay 一起註冊。每日估計那支掛在 **05:00 UTC**（`:184`，常數 `daily_estimates.py:73 DAILY_ESTIMATE_HOUR_UTC=5`；`SERVICE_TIMEZONE="UTC"`） |
| 讀回證明（看結果不是看字串） | `registered_lane_ids()` 只回 `daily_model_estimate`（`:191`，#214 節點已釘「allow-list 未設不自掛」）；`GET /api/v1/admin/collection/status`（`app/api/admin.py:89`）的 `enabled`／`running` 均為假，且 `AUTO_COLLECTION_ENABLED=false`；`LIVE_TRADING_ENABLED=false`（`app/config.py:172`），舊 daemon 的 `main()`（`app/services/live_trading_daemon.py:471`）與 `:549` 入口**不執行** |
| 資料與模型來源 | `DATA_PROVIDER=demo`；模型＝該測試帳號自己名下 1 支種子個人模型版本。**不碰任何真實帳號**：本包只使用本輪新建的測試 owner，絕不呼叫 `grant_daily_estimate` 於既有使用者 |
| 網路限制（外呼＝0 的可核對做法） | 進程啟動前後各印一次程序與連線清單：`lsof -nP -p "$(cat $RUN/service-pid.txt)" -a -i` ⇒ 除 `127.0.0.1` 之外的對端列數必須為 **0**（05:00 那一響前後各做一次並存檔）；並核對該進程環境中不含 provider 金鑰（`ps eww -p <pid>` 只在 owner 面前做存在性檢查，**不回顯值**）。本包不外呼 provider、不安裝依賴 |
| 額度（真上限，不是去重） | 該測試 owner 的 `PilotEntitlement.ai_usage_limit = 1`（欄位與預設值見 `app/models/pilot.py:97`），超出時 `reserve_quota` 於 429 拒（`entitlements.py:252-285`）⇒「一天一筆」由額度**實際強制**。扇出上限 `LEARNING_SCHEDULER_MAX_OWNERS=1`（`config.py:313` 校驗 ≥1）。**注意**：此上限證明的是可控成本，**不代替 A6**；A6  needing 未掩蓋的鑑別力，見第二節 |
| 帳號與憑證來源 | 正式入口 `POST /api/v1/auth/register`（`app/api/auth.py:31/:46`，`risk_acknowledged=true` 必要否則 400 不建帳號）、`POST /api/v1/auth/login`（`:101`，`response_model=Token`）。密碼由 owner 当场提供（同 A-INIT 的 0600 檔規則）；**代理不代人同意、不保存密碼、不写進 repo**。以密碼登入時 provenance 讀成 `human`（`app/auth/provenance.py:30`） |
| 只觀察自然觸發 | 只等 **05:00 UTC（本地 UTC+8 的 13:00）自己響一次**；手動 tick（`POST /api/v1/personalization/daily-estimate/run`，`personalization.py:449`）與「抹除 `result_json` 再跳」是**另兩件要各自批准**的事，且 05:00 那一輪**不抹 `result_json`、不改系統時間** |
| 逐步判準 | ①05:00 前：`learning_jobs` 無當日 window 列、`usage_records` 無 `via=daily_service` 當日列 ②響後：`learning_jobs` 出現 `job_type=daily_estimate` 且 `config_json.window=<當日 UTC 日>` ③`usage_records` 當日恰好 1 列 ④`GET /api/v1/personalization/daily-estimate/status`（`:420`）的 `last_estimate.inference_id` 指回同一列 ⑤訂單數 0、`decision_records`／`human_decision_events` **不因排程增加** ⑥replay 進度不推進 ⑦任何「同窗不重複扣額」的結論在本包只算**待觀察預期**（其互斥證據在 A6，目前 UNKNOWN） |

### 四、C 包（Browser；補 build／API base／登入入口／資料隔離，且端口不聽＝不是可用證據）

固定候選（進場前逐條核對，不符即停）：HEAD `5a34e3fed0f2…` ＋本記錄第一節／第七輪第一節的 sha256 現值；**build 身份進場重釘**——`.next/BUILD_ID` 是 09:05 那一次的產物，進瀏覽器前重跑 `cd frontend && npm run build`（`package.json:7`），記錄 rc 與新的 `BUILD_ID`，並以新值為準（**舊 BUILD_ID 不得作為候選證據**）；啟動用 `npm run start`（`package.json:8`）或 `npm run dev -H 127.0.0.1`（`:6`，dev 已含 `--bind 127.0.0.1` 等價寫法）。
API base 的設定方法：`frontend/src/lib/api.ts:11` 讀 `NEXT_PUBLIC_API_URL`，**這是 build-time 內嵌變數**，所以必须在 `npm run build` **之前** export（例：`export NEXT_PUBLIC_API_URL=http://127.0.0.1:8031`，指向 B 包那支 8031），否則打包進產物的仍是預設 `http://localhost:8000`；本機 8000 現測**未聆聽**，因此「不聆聽」只是空位，**不是可用證據**——端口可用與否要在進場當時以 `lsof -nP -iTCP:<port> -sTCP:LISTEN` 重查，並在啟動前後各印一次程序清單。機器上另有平行線的 next-server（pid 81307）⇒ **不接、不停、不讀**，必要時另挑端口並在包內重釘。
測試帳號與登入入口：以 `POST /api/v1/auth/register` 新建的測試 owner（`risk_acknowledged=true`），在 `frontend/src/app/login/page.tsx` 用密碼登入（⇒ `actor_type=human`）；DB 與該 API base 同一個隔離 DB。

| 客戶動作 | 真實入口（已量） | 判準 |
|---|---|---|
| 開同意並勾標的／資料 age | `PUT /api/v1/ml/personalization/daily-estimate/consent`（`personalization.py:302`；面板 `daily-estimate-instruments-*:508`、`-maxage-*:526`、`-grant:575`） | 201／200；`GET .../consent`（`:410`）讀回 `scope_json.skill_units[]` 的 instrument 與 `max_data_age_days`；`daily-estimate-enabled-*:320` 轉為每日執行中 |
| 讀回一次估計 | `POST .../run`（`:449`）→ `GET .../status`（`:420`） | `daily-estimate-last-*:376` 有結論＋`N 天前`＋`inference_id`；**無** `daily-estimate-reused-*` |
| 沿用態（本輪修的三態之一） | 同一面板第二次要求 | `daily-estimate-reused-*:392` 出現；卡上 `（此列未持有結論）`＋`資料 age 不在帳本列上`；`inference_id` 與第一次**同一個**；帳本該業務鍵**仍 1 列**（未新增扣額）；`last_refusal.reason=="already_estimated_this_window"`；`daily-estimate-refusal-*:431` 與 reused 塊**同時可見且語意不互相覆蓋** |
| 撤回一支／全部 | `DELETE .../consent`（`:362`；`?skill_unit_id=` 的單支撤回**目前 UI 無控件**，只有 `-revoke-all:585` ⇒ 單支撤回本轮只算 HTTP 能力，見第五節） | 未開始的那支以 `consent_withdrawn` 具名拒絕；已在跑的那筆結果與額度**不退還**；面板與 `status` 一致 **〔第九輪更正〕本行「單支撤回 UI 無控件」已過期**：`frontend/src/components/training/DailyEstimatePanel.tsx:388-399` 現有 `daily-estimate-withdraw-${skill_unit_id}`（`:392`），client `src/lib/api.ts:903-908` 以 `?skill_unit_id=` 送出 ⇒ 逐技能撤回已是可走的畫面旅程（第九輪 §一 #10）；本行其餘斷言不變。 |
| 記一筆連結估計的決策 | `POST /api/v1/paper-portfolio/{id}/decisions` 帶 `linked_inference_id`（後端接受：`schemas.py:619`） | 201；列表與 `GET` 讀回同一 id。**注意 UI 缺口**：`api.ts:433-439` 的手動決策 payload 型別不帶該欄，`OrderPreviewPanel.tsx:417-421` 與 `app/experiments/[id]/simulation/page.tsx:423-439` 均未送出 ⇒「從畫面記一筆有血緣的決策」目前是**未接通**，本包該步只能走 HTTP，不冒充 UI 旅程 **〔第九輪更正〕「UI 未接通」只對一半**：`src/app/experiments/[id]/simulation/page.tsx:508-515` 現已在 `lineageState()==="attached"` 時送出 `linked_inference_id`（型別 `:95`、client `src/lib/api.ts:433-445`、讀回 `:1464-1468`）⇒ 模擬頁面可從畫面記一筆有血緣的決策；`OrderPreviewPanel.tsx:417-421` **仍未送該欄**（現測 `grep -c linked_inference_id` = 0）⇒ 工作區面板維持未接通（第九輪 §一 #13）。 |
| 匯出決策歷史 | `GET /api/v1/paper-portfolio/{id}/decision-history?format=csv`（`paper_portfolio.py:1810`；畫面 `app/paper-portfolio/[id]/page.tsx:389/391` `pp-download-decision-history`）與 `format=json`（client `api.ts:467/469`） | CSV 可下載並在試算表開啟，有訂單／無訂單決策都在；`inference_source_state=="present"`；同夾交易 journal **不因無訂單決策多列**；`human-decisions` 那兩筆 id **不得**出現在檔內（#223 的畫面版）；**JSON 目前無畫面按鈕**（`decisionHistoryJson` 在 `frontend/src` 零呼叫點）⇒ 標未接通 **〔第九輪更正〕已接通**：`frontend/src` 現測 `decisionHistoryJson` 有 2 個呼叫點（`src/app/paper-portfolio/[id]/page.tsx:303` 落檔、`:346` 填畫面）＋按鈕 `pp-download-decision-history-json`（`:470`）⇒ 本行「標未接通」過期（第九輪 §一 #11）。 |
| 切版後的舊估計 | 先切模型版本再看那張卡 | `daily-estimate-version-drift-*:420` 出現（「不是你現在所選模型的輸出」），且不当成現在所選模型的輸出 |

邊界（報告必附）：browser-use 自動化只能做可用性輔助，**不得**由代理替 owner 按下同意／撤回；build PASS／node:test PASS 不代替 browser PASS；本包 **NOT RUN**。

### 五、本節新增的兩個實測缺陷（不混進上面三包，也不因 P4 通過而結案）

1. **手動入口不去重：按幾次就多幾筆 inference＋多幾輪扣額。** `trigger_daily_estimate()` 以 `window=None`、`scope="manual"` 進同一個 tick（`daily_estimates.py:1130-1135`），而 tick 的三处去重全部以 `window` 为開關：`recovered`（`:842`）、`summary_skills`（`:846-848`）、逐筆座位 `if window and claim_estimate(...)`（`:916`）。同一 (owner, skill, instrument, 同一 as_of 日) 因而可被重複計價；而且现行測試把這個行為**釘成契約**：`tests/test_daily_model_estimate.py:1657-1667`（第二次 `_run_now` ⇒ `len(_served)==2`；視窗已結算後再按 ⇒ `==4`）。所以 #209 的已修復範圍只有「排程在同一視窗重跑且第一筆已完成計價」，brief 問的「重複 inference／扣額」在手動路徑**未修**。
**〔第九輪更正〕本缺陷已修並反轉**：`trigger_daily_estimate` 改傳 `window=estimate_window()`＋`scope=MANUAL_SCOPE`（`backend/app/services/daily_estimates.py:1227-1232`），`_estimates_for_owner` 不信任呼叫者給的窗口（`:874`），座位不再被 `window` 開關跳過（`:1002`），本行引的 `:1657-1667` 節點已搬到 `:1862` 並反轉斷言（原三行改寫成 1 筆／`estimated==0`／同一 `inference_id`）⇒ 證據層＝④-sqlite＋⑤-HTTP 讀回，**不是** PG。細節見第九輪 §一／§二。
2. **摘要未寫那一輪留下一支永遠 `running` 的 `learning_jobs` 列。** 收尾狀態（`:985-986/:1011`）與摘要同屬 `:1012` 那次 commit，錢屬 `personal_models.py:695`，故中途死 ⇒ 帳本有列、預留已結算、`result_json` 為 NULL、`status` 停在 `running`，且無任何路徑轉終態；`window_state.execution_claimed`（`:581-583`）因此永久 True，`last_run.status` 會持续回報 `running`。錢不再扣（帳本擋住），但這是「學習任務部分提交」的另一半，**本輪不實作終態轉換**（把別人的 in-flight 列判死需要 durable lease 或時間閘，属新政策，交 owner 裁決）。
**〔第九輪更正〕部分處理**：本輪改的是**顯示語意**（`執行狀態未確認` 不聲稱終態；`window_state.execution_claimed` 不再單獨決定錢的來源——status 先讀帳本回落 `:1354`／輸出 `:1395-1396`）與**重試語意**（該列不阻塞重試、也不被重試改寫）；**終態轉換與 durable lease／heartbeat 仍未實作**，交 owner 裁決（第九輪 §八 4）。本行「無任何路徑轉終態」依然成立。

修法方向（已定，屬工程裁決）：手動路徑改成「**帳本去重、摘要不計**」——`dedup_window = estimate_window()`（錢的鍵照樣成立、PG 座位照樣取），`config_json.window` 仍不寫（手動列不進入任何視窗的執行權與摘要集合），`summary_skills` 對手動一律為空（時鐘拒絕過的，人按就重試）；如此第二次按下＝沿用同一筆帳本列並回到 `verdict_durable=false`，面板顯示第七輪修好的「沿用」卡。測試 `:1657-1667` 那三行要同步反轉，並在反轉前後各跑一次以證明改動是承重的。
**〔第九輪〕已照此方向實作**：`dedup_window` 的角色由「`window = window or estimate_window()`（`:874`）＋座位必經（`:1002`）」分擔，`config_json.window` 對手動列仍不寫、`_summary_recorded_skills` 跳過 `scope=="manual"`（`:575`），摘要集合對手動一律空集合（`:907-910`）；測試 `:1862` 反轉前後各跑一次（`a4579c82…`／`50130606…` 兩個 tree-digest 的 lane 記錄見第九輪 §七）。

### 六、本輪仍未執行／仍未證明

PG 全部步驟（A-INIT／A-MIG／A-READ／A6）＝NOT RUN（未批准）；`pg_advisory_xact_lock` 是否足以擋住第二支＝**UNKNOWN**；05:00 自然觸發＝NOT RUN（B）；browser＝NOT RUN（C）；D 包（逐 hunk patch＋在乾淨 HEAD 克隆上重現驗證）本輪**尚未交**；手動去重修復與 `:1657-1667` 反轉＝本輪之後的工作；#226 六情境矩陣中（b）（d）兩格仍需以新節點量到「正式 HTTP 讀回」等級。 〔第九輪更正〕本行四處過期：①「手動去重修復與 `:1657-1667` 反轉＝本輪之後的工作」→ **第九輪已做**；②「#226（b）（d）兩格仍需以新節點量到正式 HTTP 讀回」→ **第九輪已補**（P1／P2／P4／P5＋新節點 `:1302`），但仍非 PG、仍非真實服務行程；③「D 包本輪尚未交」→ 第九輪交正文與閉包清單，**仍未執行**；④ PG／05:00／browser 三項在第九輪**同樣是 NOT RUN**（未批准）。

## 第九輪（2026-11-19 09:5x–13:5x HKT）：手動入口改與 05:00 排程同鍵（已修＋讀回實測）、六情境崩潰矩陣、崩潰後畫面所見、四包可獨立審批正文

本節只交 brief 點名的四項：**重按前後實際帳本筆數**（第二節）、**崩潰後畫面所見**（第三＋第四節）、**兩條正式入口的實際 diff**（第五節）、**可獨立審批正文**（第六節）。**保留**第八輪與更早的離線證據（A／B／C 包正文、B1′ 292 collected／289 passed／3 skipped、P4、#223、#224、兩行程 harness 的機制節點）不重跑，也不新增並行摘要檔——本節寫在同一份 `EXECUTION_TABLE_20261117.md` 內。

本輪的代碼與測試改動全落在既有授權（本機工程＋既有隔離 sqlite lane）之內：**未**啟動任何服務行程、**未**動受保護 DB、**未**裝依賴、**未**跑 migration、**未**外呼、**無** Git 寫入、**無**瀏覽器操作。

### 一、改動本體：#1–#8 手動入口改成與排程同一窗口、同一業務鍵、同一座位；#9–#10 面板文案與逐技能撤回控件；#11–#13 旅程二（JSON 匯出、來源狀態、模擬決策血緣）接上正式 client

| # | 位置（現讀） | 改前（第八輪 §五 缺陷 1 記錄的原文） | 改後（本輪） |
|---|---|---|---|
| 1 | `backend/app/services/daily_estimates.py:1227-1232`（`trigger_daily_estimate`） | 以 `window=None`、`scope="manual"` 進同一個 tick（原引 `:1130-1135`） | `run_daily_estimate_tick(scope=MANUAL_SCOPE, window=estimate_window(), user_id=owner.id, only_skill_unit_id=skill_unit_id)` |
| 2 | `:874`（`_estimates_for_owner` 開頭） | 無此行；呼叫者不給窗口時，下方三處去重全部對「無窗口」查成空集合 | `window = window or estimate_window()`——不信任任何呼叫者指名窗口 |
| 3 | `:903`、`:907-910` | 手動與排程同讀摘要集合 | 帳本回落兩者共用（`recovered = settled_estimates(db, owner_id, window)` `:903`）；**摘要集合只屬時鐘**：`set() if scope == MANUAL_SCOPE else set(_summary_recorded_skills(...))` |
| 4 | `:546-583`（`_summary_recorded_skills`） | 讀該窗口所有已關閉摘要 | `:575` 跳過 `config_json["scope"]=="manual"` 的列：一支按鈕按出來的拒絕，不再決定 05:00 還有沒有東西要試 |
| 5 | `:1002` | `if window and claim_estimate(...)`（原引 `:916`） | `if claim_estimate(db, owner_id, window, skill_id, instrument_id)`——座位對兩條入口同樣必經，不再被 `window` 開關跳過 |
| 6 | `:969-989`／`:1004-1013` | 拒絕不記來源 | 拒絕記 `decided_by`：`clock_summary`（`:973`，沒花錢）／`billing_ledger`（`:985`、`:1011`，錢已出門）＋`inference_ids`，讓「時鐘已回報過」與「已付過費」不再混為一談 |
| 7 | `:408`（新 `billed_estimates_by_skill`）＋ `:1244`（`daily_estimate_status`） | status 只讀摘要；摘要沒寫就讀成「沒有估計」 | status 先按帳本分組回落（`:1354 window_claimed = bool(jobs)`、輸出 `:1395-1396`），錢的來源是帳本不是摘要 |
| 8 | `backend/tests/test_daily_model_estimate.py:1862` | 舊節點把缺陷釘成契約：第二次按 ⇒ `len(_served)==2`；窗口已結算後再按 ⇒ `==4`（原 `:1657-1667`） | 反轉後的斷言：1 筆、第二次 `estimated==0`、`inference_id` 與第一次同一支、`refusal["decided_by"]=="billing_ledger"`、`recovered[0]["verdict_durable"] is False`；docstring `:1866-1872` 写明它曾經讀反 |
| 9 | `frontend/src/components/training/DailyEstimatePanel.tsx:240` | 手動完成後對本人寫「手動要求不佔用今日排程窗口，定時服務在今天仍會各執行一次」——正是那個缺陷的文案版 | 「手動要求與每日排程同一個 UTC 視窗、同一筆帳本：今天已計價的帳號，誰再來都不會多扣一次額度」；由 `dailyEstimate.test.mjs` 新節點釘住（旧句出現即 fail） |
| 10 | `DailyEstimatePanel.tsx:388-399` | 只有 `daily-estimate-revoke-all`（撤回全部）；逐技能 `DELETE` 在畫面上**無控件** ⇒ brief 要的「/training 逐技能撤回」旅程走不完 | 新增 `daily-estimate-withdraw-${state.skill_unit_id}`（`:392`，`onClick={() => withdraw(state.skill_unit_id)}`，只在 `state.declared` 時渲染）；client `src/lib/api.ts:903-908` 以 `?skill_unit_id=` 送出，對應 `app/api/personalization.py:363-366` 的路由與 `skill_unit_id: Optional[uuid.UUID] = None` query 參數；兩條 notice 文案分岔（一人／全部）由測試 `frontend/tests/dailyEstimate.test.mjs:609` 節點釘住 |
| 11 | `frontend/src/app/paper-portfolio/[id]/page.tsx:468-479`＋`:300-315` | 第八輪 §四（C 包表格，該記錄 1883 列）實測：`decisionHistoryJson` 在 `frontend/src` **零呼叫點** ⇒「JSON 目前無畫面按鈕…標未接通」 | 接上第二顆按鈕 `pp-download-decision-history-json`（`:470`）與 `handleDownloadDecisionHistoryJson`（`:300`）：同一支 `GET …/decision-history?format=json`（client `src/lib/api.ts:473-474`）的**同一份回應**同時落檔並填畫面（`applySourceStates` `:319-342`），`useEffect`（`:344-352`）在 Decisions 分頁開啟時主動讀 ⇒ 檔與畫面不可能各說一套；讀取失敗寫明「一次失敗的讀取不是『沒有決策指向來源』的證據」（`:891-893`），無法辨認的狀態**計數而非丟棄**（`:896-899`＋`src/lib/decisionLineage.ts:38-39`） |
| 12 | `frontend/src/app/paper-portfolio/[id]/page.tsx:1004`＋`src/lib/decisionLineage.ts:11` | 來源狀態只存在於匯出檔，畫面上讀不到 | 逐列 `pp-decision-inference-source` 顯示 `present`／`missing`／`none` **三態**（不是五態：`decisionLineage.ts:11` 的 union 與後端欄位自陳 `backend/app/api/paper_portfolio.py:1673` 同字）；無法辨認的值由 `inferenceSourceState` 回 `undefined`，**不塌成 `none`**（`:18-20`） |
| 13 | `frontend/src/app/experiments/[id]/simulation/page.tsx:508-515`＋`:95`＋`src/lib/api.ts:433-445` | 第八輪 §四 旅程二寫的是「模擬頁面送 `linked_inference_id`」尚未接上；該畫面記下的決定在 `decision-history` 裡查不到來源 | 只在 `lineageState(…)==='attached'`（`src/lib/dailyEstimate.ts:171`）時才寫 `p.linked_inference_id`；payload 型別 `:95`、client `src/lib/api.ts:433-445` 都帶該欄，讀回顯示 `:1464-1468`。**另一道門本輪沒接**：`src/components/workspace/OrderPreviewPanel.tsx:417-421`（工作區的手動決策）只組 `action`／`idempotency_key`／`rationale`＋条件附上的 `quantity`／`expected_version`，`linked_inference_id` 在該檔出現 **0 次**（現測 `grep -c`）⇒ 型別允許不等於送出，「有血緣的決策」目前只覆蓋模擬頁面與訓練面板既有的 `createDecision`（`PersonalStrategyLearningPanel.tsx:1465`，非本輪改），不涵蓋工作區訂單預覽面板。**持久邊界（現讀，不是宣稱）**：`POST /api/v1/simulations/{id}/manual-decision`（`backend/app/api/simulations.py:2295`）把 id 寫進 `DecisionRecord.linked_inference_id`（`:2206`／`:2214`），同一次提交另寫的 `HumanDecisionEvent`（`:2583`）**不帶血緣欄**；HOLD＋該欄被 400 `inference_needs_decision_row` 拒絕並指名另一道門（`:2484-2498`；後端斷言 `tests/test_manual_decision_books_linked_portfolio.py:1831`）⇒ UI 端不得以「request 有欄位」充當已持久化。那道後端邊界與拒絕**不是本輪改的**（現測 `stat` mtime：`simulations.py` 2026-11-18 07:56、`test_manual_decision_books_linked_portfolio.py` 2026-11-18 07:55），本輪動的是把畫面與 client 接上去（前端四檔現測 mtime 12:27–12:45；共用工作樹另有並行線，故 mtime 只作輔助，真正歸屬由 D 包的完整位元組＋逐檔雜湊交 owner 判讀） |


**契約沒有擴張到哪裡（現測，不是宣稱）**：`claim_estimate(` 在全 repo 只有一個生產呼叫點——`app/services/daily_estimates.py:1002`（grep `claim_estimate(` 於 `app`＋`tests` 除定義外僅這 1 列）。一般按需 `POST /api/v1/ml/personal/models/{version_id}/predict`（`app/api/personal_models.py:739`，router prefix `:89`）的原始碼不出現 `estimate_window`／`billed_estimate`／`claim_estimate` 任何一個字（grep 0 列）⇒ 按需推論「按一次＝一次合法推論」的既有語義未動。重按也**不是**授權：`:1222-1225` 在開帳本列之前先查聲明範圍，未聲明的技能一律 `no_skills_enabled`（P10 實測 400，連帳本列都不開）；同窗口換模型版本的去重由帳本決定（`test_a_model_switch_cannot_rebill_the_window_it_already_estimated` `:1391`），而未獲選取的版本在 `_estimate_one` 那道閘仍會被拒（`:1439` 反證：-switch **之前**第一筆都沒跑過時，新選版本才會被計價）。

### 二、重按前後實際帳本筆數（正式入口讀回；`bills` ＝ `usage_records` 中 `metadata.via=="daily_service"` 的列數）

| # | 順序 | 第一次動作 → 帳本／預留 | 第二次動作（正式 HTTP） → 帳本／預留 | 第二次回傳的是什麼 | 對應節點 |
|---|---|---|---|---|---|
| 1 | 手動→手動 | `POST …/run` 201 `estimated 1` → **1／["settled"]** | 同 door 再按 201 `estimated 0` → **仍 1／["settled"]**（無新增預留） | 同一 `inference_id` `6c4115f4-…`；`refusal.decided_by=="billing_ledger"` | `:1862`（P6） |
| 2 | cron→手動 | 排程入口 `run_scheduled_daily_estimates()` → **1／["settled"]** | `POST …/run` 201 `estimated 0` → **仍 1** | 同一 `inference_id` `892c171e-…` | `:1925`（P7） |
| 3 | 手動→cron | `POST …/run` 201 `estimated 1` → **1／["settled"]** | 排程再跑 → **仍 1**；**第二次**排程改為 `clock_summary` 拒絕 → 仍 1 | 同一 `inference_id` `f5084108-…`；两种來源標記不同、錢的結論相同 | `:2002`（P8） |
| 4 | 跨 UTC 窗口 | `window=2024-01-31` → **1／["settled"]** | `window=2024-02-01` → **2／["settled","settled"]** | 新的 `as_of_date` 是新的一次合法計價，不是重複扣額 | `:2225`（P9） |
| 5 | 跨技能 | 按 A → 只 A 計價 | 讀 B：窗口未聲稱、帳本無 B 列 | B 的帳本與聲明都不動 | `:2196` |
| 6 | 撤回一支 | 兩支 tick → **2／["settled","settled"]** | `DELETE …/consent?skill_unit_id=B` → **200**，剩餘聲明只有 A；帳本**仍 2**（B 那筆不退還） | 再按 B → **400 `no_skills_enabled`**、帳本仍 2；按 A → 201 `estimated 1`、帳本 3（今日窗口的合法新扣額） | `:2276`（P10） |
| 7 | 未到結算的失敗 | press 內 engine 死 → **0／["released"]** | 同窗口重試 → **1／["released","settled"]** | 重試是**第一次**收費，不是第二次 | `:2140`（P3） |
| 8 | 摘要遺失 | tick 完成 → **1／["settled"]** | 清空 `result_json` 後再讀 → **仍 1** | 同一 `inference_id` `a7fc320d-…`（但 `predicted_action` 變 `null`、`verdict_durable: false`） | `:1139`／`:1198`（P5） |

「同窗口不重複扣額」的**互斥**部分仍不在本節範圍：以上全部為單行程讀回。兩行程並發屬 A6，未批准 ⇒ 證據見第八輪 §二 與本節第六、八節。

### 三、六情境崩潰矩陣（逐條：中斷位置／已提交狀態／HTTP 讀回／重試效果／證據層）

證據層標記：**①**＝離線讀碼；**④-sqlite**＝正式 lane runner 的 sqlite 節點 PASS；**⑤-HTTP**＝以正式入口（`TestClient` 打 `POST …/run`／`GET …/status`）讀回的實際 payload；**⑤-service**＝以服务層正式函式（`run_scheduled_daily_estimates`）驅動後的同樣讀回。**沒有實測到的情境一律標證據層，不推定。**

| 情境（brief 點名者打 ★） | 中斷位置（現讀 file:line） | 中斷後已提交的持久狀態 | 正式 HTTP 讀回 | 重試效果 | 證據層 |
|---|---|---|---|---|---|
| 座位（claim 列）刚落库就死 ★ | `app/workers/learning_scheduler.py:456`（`_open_job` 的 `db.commit()`）之後、第一次計價之前 | `learning_jobs` 1 列：`status="running"`、`config_json.window="2024-01-31"`、`result_json` 空；帳本 0 列；預留 0 筆 | P1：`bills 0`、`reservations []`、`window_claimed true`、`last_estimate/last_refusal/last_run` **三者全 null** | 重試 → `bills 1`、`["settled"]`、`jobs ["running","completed"]`；**舊的 running 列不該寫、也不得被改寫**（新節點斷言 `after[0]["status"]=="running"` 且 `result=={}`） | ④-sqlite＋⑤-HTTP。節點：`test_a_claim_row_that_outlived_its_worker_is_retried_without_erasing_it`（`tests/test_daily_model_estimate.py:1302`，本輪新增） |
| 走過一圈但第一筆未計價就死 ★ | 同上，再往下一圈（`_estimates_for_owner` 的逐筆迴圈內、`_estimate_one` 之前） | 與上一列**完全同形**：帳本 0、預留 0、running 列 1 | P2 的 JSON 與 P1 **逐字相同** | 同上一列（1 筆、1 settled） | ④-sqlite＋⑤-HTTP。**這是量出來的不可區分**：兩個中斷點在持久訊號上沒有差異，所以畫面只能說「有開工紀錄」，不能說「跑到第幾筆」或「還在跑」 |
| 局部成功（兩支標的，第一筆已結算後死）★ | `app/api/personal_models.py:695`（帳本列＋結算的那次 commit）之後、下一支 `_estimate_one` 之前 | 帳本 1 列（A）＋預留 1 settled；`learning_jobs` 1 列 running、摘要空 | A 卡：`last_estimate` 回落成**帳本列**（`predicted_action: null`、`verdict_durable: false`）、`last_run null`；B 卡：`window_claimed true` 且無 estimate 無 refusal（＝`執行狀態未確認`） | 重試 → `bills 2`；A 被 `decided_by="billing_ledger"` 拒並指名原 `inference_id`（`23badc3b-…`），B 拿到它從未有過的那一筆 | ④-sqlite＋⑤-HTTP。節點：`test_a_press_killed_after_one_bill_serves_the_rest_of_the_window`（`:2046`） |
| 摘要遺失（帳本已結算）★ | 摘要與終態同屬 `daily_estimates.py:1101` 那次 commit，故**真實崩潰不會留下「有摘要無終態」**；本情境以清空 `result_json` 構造 | 帳本 1 列＋1 settled；`learning_jobs` 列存在但摘要為空 | 清空前後 `inference_id` **同一支**；清空後 `predicted_action` 由 `"sell"` 變 `null`、`verdict_durable: false`、`last_run null`（不拿 null 冒充預測） | 重試不再計價：`bills` 仍 1（`test_a_lost_summary_cannot_buy_a_window_twice` `:1139`） | ④-sqlite＋⑤-HTTP。提交邊界屬 ①（讀碼：`:1074`／`:1085` 寫、`:1101` commit；錢在 `personal_models.py:659`→`:663`→`:695`） |
| 未到結算的失敗 ★ | `_estimate_one` 內：`reserve_quota` 已 commit，推論／引擎在 `personal_models.py:695` 之前失敗 → 走 `release` 分支以自己的 commit 釋放 | 帳本 0 列；預留 `["released"]` | **press 本體回 201**、`job.estimated==0`；`status.last_refusal.reason=="prediction_failed"`、內層 `http_status: 422`＋`message_zh`；`last_run.status=="partial"` | 同窗口重試 → `bills 1`、預留 `["released","settled"]`、`estimated 1` ⇒ 窗口確實「還欠這筆」 | ④-sqlite＋⑤-HTTP。節點：`:2140`、`:1051`。**201 而非 4xx** 是本次讀回到的實際契約，列為交回 owner 的裁決（第八節 1） |
| 跨 UTC 窗口 ★ | 無中斷；以 `window` 逐日推進 | 兩個窗口各 1 列帳本 | `window` 由 `2024-01-31` 變 `2024-02-01`，`window_claimed` 各自獨立 | 第二個窗口是新的服務日，正常計價 | ④-sqlite＋⑤-HTTP。節點：`:2225`、`:610` |
| 跨技能 ★ | 無中斷；兩支技能帳號並列聲明 | A 有帳本列，B 無 | B 卡 `enabled true`、`window_claimed` 只在其窗口有 claim 列時為 true | 按 A 不動 B；按 B 只付 B | ④-sqlite＋⑤-HTTP。節點：`:2196` |
| 撤回 ★ | tick 走到一半時 `withdraw_daily_estimate`（既有節點 `tests/test_daily_model_estimate.py:1590`）與 tick 之後的 `DELETE …?skill_unit_id=`（P10） | 未開始那支以 `consent_withdrawn` 具名拒絕、**未扣額**；已計價那支的帳本與額度**不退還** | B：`enabled false`、`declared false`、`disabled_reason "not_declared"`、`last_estimate_present true`、帳本仍 2 | 按已撤回的 B → 400 `no_skills_enabled`（帳本不增加）；按 A → 新扣額屬今日窗口 | ④-sqlite＋⑤-HTTP。節點：`:1590`、`:2276` |
| 同業務鍵**兩行程並發** | 座位在 `claim_estimate`（`daily_estimates.py:508`）內：先 `db.commit()` 結束讀快照（`:533`），再 `SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text),0))`（`:538-539`），SQLite 無此鎖 | —（本機未起 PG） | — | — | **NOT RUN**。SQLite 層具名跳過（`tests/test_daily_estimate_two_workers.py:376-377`）；④-sqlite 只有機制節點 `:210`（證明兩支行程真的在屏障兩側相遇），**不是** PG 競態證據；`pg_advisory_xact_lock` 是否足以擋住第二支＝**UNKNOWN** |
| 05:00 **自然觸發**下的上述六格 | 真實 cron＋真實行程 | — | — | — | **NOT RUN**（屬 B 包，未批准）。本節所有 cron 讀回都是 ⑤-service（同行程呼叫正式函式），不是排程行程 |

讀回原文（一次性探針 `tests/test_zz_round9_readback_probe.py` 印出，跑完即從工作樹刪除；run dir `sqlite-20261119T050737Z-78957`，`bills`／`reservations` 另經第二條連線直接查表，不靠應用自己的 session）：

```text
PROBE P1_after_crash {"bills": 0, "reservations": [], "window": "2024-01-31", "window_claimed": true, "last_estimate": null, "last_refusal": null, "last_run": null, "jobs": ["running"]}
PROBE P1_after_retry {"bills": 1, "reservations": ["settled"], "window": "2024-01-31", "window_claimed": true, "last_estimate": {"inference_id": "fdb4d0d5-d3e6-4f62-9514-05b6463c138d", "predicted_action": "sell", "verdict_durable": null, "as_of_date": "2024-01-31"}, "last_refusal": null, "last_run": {"scope": "daily", "window": "2024-01-31", "status": "completed", "estimated": null}, "jobs": ["running", "completed"]}
PROBE P2_after_crash {"bills": 0, "reservations": [], "window": "2024-01-31", "window_claimed": true, "last_estimate": null, "last_refusal": null, "last_run": null}
PROBE P2_after_retry {"bills": 1, "reservations": ["settled"], "window": "2024-01-31", "window_claimed": true, "last_estimate": {"inference_id": "1a380837-c420-46a6-8c59-aa3fb8d4d538", "predicted_action": "sell", "verdict_durable": null, "as_of_date": "2024-01-31"}, "last_refusal": null, "last_run": {"scope": "daily", "window": "2024-01-31", "status": "completed", "estimated": null}}
PROBE P3_after_failed_press {"bills": 0, "reservations": ["released"], "window": "2026-11-19", "window_claimed": true, "last_estimate": null, "last_refusal": {"skill_unit_id": "6f25b85c-2df4-4452-a745-6b8f0760ef73", "instrument_id": "TEST-ENGINEERING-ASSET", "reason": "prediction_failed", "refused_by": null, "detail": {"code": "prediction_failed", "message_zh": "推論失敗，已釋放本次額度。"}, "http_status": 422, "message_zh": "推論失敗，已釋放本次額度。"}, "last_run": {"scope": "manual", "window": "2026-11-19", "status": "partial", "estimated": null}, "http_status": 201, "estimated": 0}
PROBE P3_after_retry {"bills": 1, "reservations": ["released", "settled"], "window": "2026-11-19", "window_claimed": true, "last_estimate": {"inference_id": "e79eb059-7c33-489a-aaa6-4649d6004bde", "predicted_action": "sell", "verdict_durable": null, "as_of_date": "2026-11-19"}, "last_refusal": null, "last_run": {"scope": "manual", "window": "2026-11-19", "status": "completed", "estimated": null}, "http_status": 201, "estimated": 1}
PROBE P4_after_crash {"bills": 1, "reservations": ["settled"], "window": "2024-01-31", "window_claimed": true, "last_estimate": {"inference_id": "23badc3b-c793-4409-b776-230c5bb9866d", "predicted_action": null, "verdict_durable": false, "as_of_date": "2024-01-31"}, "last_refusal": null, "last_run": null, "killed_skill": "de090ac0-43da-46ab-8e4b-a1d20f590132"}
PROBE P4_after_crash_skillB {"window": "2024-01-31", "window_claimed": true, "last_estimate": null, "last_refusal": null}
PROBE P4_after_retry {"bills": 2, "reservations": ["settled", "settled"], "window": "2024-01-31", "window_claimed": true, "last_estimate": {"inference_id": "23badc3b-c793-4409-b776-230c5bb9866d", "predicted_action": null, "verdict_durable": false, "as_of_date": "2024-01-31"}, "last_refusal": {"skill_unit_id": "de090ac0-43da-46ab-8e4b-a1d20f590132", "reason": "already_estimated_this_window", "decided_by": "billing_ledger", "inference_ids": ["23badc3b-c793-4409-b776-230c5bb9866d"], "message_zh": "此技能帳號今日的估計已存在，未重複執行。"}, "last_run": {"scope": "daily", "window": "2024-01-31", "status": "partial", "estimated": null}}
PROBE P5_summary_present {"bills": 1, "reservations": ["settled"], "window": "2024-01-31", "window_claimed": true, "last_estimate": {"inference_id": "a7fc320d-036f-4843-8cf7-85312f36715f", "predicted_action": "sell", "verdict_durable": null, "as_of_date": "2024-01-31"}, "last_refusal": null, "last_run": {"scope": "daily", "window": "2024-01-31", "status": "completed", "estimated": null}}
PROBE P5_summary_lost {"bills": 1, "reservations": ["settled"], "window": "2024-01-31", "window_claimed": true, "last_estimate": {"inference_id": "a7fc320d-036f-4843-8cf7-85312f36715f", "predicted_action": null, "verdict_durable": false, "as_of_date": "2024-01-31"}, "last_refusal": null, "last_run": null}
PROBE P6_press_1 {"bills": 1, "reservations": ["settled"], "window": "2026-11-19", "window_claimed": true, "last_estimate": {"inference_id": "6c4115f4-c555-446f-8e78-c66527ae8712", "predicted_action": "sell", "verdict_durable": null, "as_of_date": "2026-11-19"}, "last_refusal": null, "last_run": {"scope": "manual", "window": "2026-11-19", "status": "completed", "estimated": null}, "http_status": 201, "estimated": 1}
PROBE P6_press_2 {"bills": 1, "reservations": ["settled"], "window": "2026-11-19", "window_claimed": true, "last_estimate": {"inference_id": "6c4115f4-c555-446f-8e78-c66527ae8712", "predicted_action": null, "verdict_durable": false, "as_of_date": "2026-11-19"}, "last_refusal": {"skill_unit_id": "61ab26a7-fa7c-47c0-8c9e-fb55ebe74f14", "reason": "already_estimated_this_window", "decided_by": "billing_ledger", "inference_ids": ["6c4115f4-c555-446f-8e78-c66527ae8712"], "message_zh": "此技能帳號今日的估計已存在，未重複執行。"}, "last_run": {"scope": "manual", "window": "2026-11-19", "status": "partial", "estimated": null}, "http_status": 201, "estimated": 0}
PROBE P7_cron {"bills": 1, "reservations": ["settled"], "window": "2026-11-19", "window_claimed": true, "last_estimate": {"inference_id": "892c171e-dc9b-48d7-9377-57410618a18a", "predicted_action": "sell", "verdict_durable": null, "as_of_date": "2026-11-19"}, "last_refusal": null, "last_run": {"scope": "daily", "window": "2026-11-19", "status": "completed", "estimated": null}}
PROBE P7_press_after_cron {"bills": 1, "reservations": ["settled"], "window": "2026-11-19", "window_claimed": true, "last_estimate": {"inference_id": "892c171e-dc9b-48d7-9377-57410618a18a", "predicted_action": null, "verdict_durable": false, "as_of_date": "2026-11-19"}, "last_refusal": {"skill_unit_id": "c1b88950-786e-42cf-b1c7-0e1edecf2d04", "reason": "already_estimated_this_window", "decided_by": "billing_ledger", "inference_ids": ["892c171e-dc9b-48d7-9377-57410618a18a"], "message_zh": "此技能帳號今日的估計已存在，未重複執行。"}, "last_run": {"scope": "manual", "window": "2026-11-19", "status": "partial", "estimated": null}, "http_status": 201, "estimated": 0}
PROBE P8_press {"bills": 1, "reservations": ["settled"], "window": "2026-11-19", "window_claimed": true, "last_estimate": {"inference_id": "f5084108-0744-49a7-9baa-647b139c509b", "predicted_action": "sell", "verdict_durable": null, "as_of_date": "2026-11-19"}, "last_refusal": null, "last_run": {"scope": "manual", "window": "2026-11-19", "status": "completed", "estimated": null}, "http_status": 201, "estimated": 1}
PROBE P8_cron_after_press {"bills": 1, "reservations": ["settled"], "window": "2026-11-19", "window_claimed": true, "last_estimate": {"inference_id": "f5084108-0744-49a7-9baa-647b139c509b", "predicted_action": null, "verdict_durable": false, "as_of_date": "2026-11-19"}, "last_refusal": {"skill_unit_id": "3839242a-ebe7-456f-917f-67ec59cf9aad", "reason": "already_estimated_this_window", "decided_by": "billing_ledger", "inference_ids": ["f5084108-0744-49a7-9baa-647b139c509b"], "message_zh": "此技能帳號今日的估計已存在，未重複執行。"}, "last_run": {"scope": "daily", "window": "2026-11-19", "status": "partial", "estimated": null}}
PROBE P8_cron_again {"bills": 1, "reservations": ["settled"], "window": "2026-11-19", "window_claimed": true, "last_estimate": {"inference_id": "f5084108-0744-49a7-9baa-647b139c509b", "predicted_action": null, "verdict_durable": false, "as_of_date": "2026-11-19"}, "last_refusal": {"skill_unit_id": "3839242a-ebe7-456f-917f-67ec59cf9aad", "reason": "already_estimated_this_window", "decided_by": "clock_summary", "message_zh": "此技能帳號今日的估計已存在，未重複執行。"}, "last_run": {"scope": "daily", "window": "2026-11-19", "status": "partial", "estimated": null}}
PROBE P9_window_1 {"bills": 1, "reservations": ["settled"], "window": "2024-01-31", "window_claimed": true, "last_estimate": {"inference_id": "f55d5507-3839-4611-a13a-90293582fda0", "predicted_action": "sell", "verdict_durable": null, "as_of_date": "2024-01-31"}, "last_refusal": null, "last_run": {"scope": "daily", "window": "2024-01-31", "status": "completed", "estimated": null}}
PROBE P9_window_2 {"bills": 2, "reservations": ["settled", "settled"], "window": "2024-02-01", "window_claimed": true, "last_estimate": {"inference_id": "9665dc52-30b6-4f57-a39d-00687747a877", "predicted_action": "sell", "verdict_durable": null, "as_of_date": "2024-02-01"}, "last_refusal": null, "last_run": {"scope": "daily", "window": "2024-02-01", "status": "completed", "estimated": null}}
PROBE P10_both_billed {"bills": 2, "reservations": ["settled", "settled"], "window": "2024-01-31", "window_claimed": true, "last_estimate": {"inference_id": "e5843fe7-2098-4613-afde-d79800466297", "predicted_action": "sell", "verdict_durable": null, "as_of_date": "2024-01-31"}, "last_refusal": null, "last_run": {"scope": "daily", "window": "2024-01-31", "status": "completed", "estimated": null}, "skill": "2fdd8941-97b2-471b-871b-15d3d06dcf98"}
PROBE P10_delete_b {"http_status": 200, "remaining": ["2fdd8941-97b2-471b-871b-15d3d06dcf98"]}
PROBE P10_withdrawn_skillB {"enabled": false, "declared": false, "disabled_reason": "not_declared", "window": "2024-01-31", "window_claimed": true, "last_estimate_present": true, "bills": 2}
PROBE P10_press_withdrawn {"http_status": 400, "detail": {"code": "no_skills_enabled", "message_zh": "此技能帳號未申報每日估計範圍，未執行任何推論。", "detail": "70f71942-f5fc-44fa-a71a-34de2aa3ddf6"}, "bills": 2}
PROBE P10_press_kept {"http_status": 201, "estimated": 1, "bills": 3}
```

**崩潰的造法與其邊界（必附）**：以 `monkeypatch` 在指名行之後拋 `BaseException` 子類模擬「該行之後的程式碼不再執行、先前的 commit 留在 DB」，因此「已提交狀態」一欄是該提交點之後的**真實 DB 狀態**；但行程級的死亡副作用（socket 未關、連線洩漏、APScheduler 的 misfire 處理、記憶體中的排程狀態）**未涵蓋** ⇒ 不得把本節任何一列當成 kill -9／斷電情境的證據（證據層①而已），也不得據此宣稱「已證明可從崩潰中恢復」。

### 四、崩潰後畫面所見（五態，同屏可並列）

| 狀態（`src/lib/dailyEstimate.ts:98-101`） | `GET …/status` 的欄位組合 | 畫面鰲點（現讀 `DailyEstimatePanel.tsx`） | 本節哪個情境會落到這裡 |
|---|---|---|---|
| `estimated`「本輪產生」 | `last_estimate` 帶 verdict | `daily-estimate-last-*`（`:426`） | 第一次 press／第一次 cron |
| `carried_over`「已沿用」 | `last_estimate` 來自帳本回落（`predicted_action null`＋`verdict_durable false`），`last_refusal.reason=="already_estimated_this_window"` | `daily-estimate-reused-*`（`:442`）＋ `daily-estimate-refusal-*`（`:481`）**同時**出現 | 手動→手動、cron→手動、手動→cron；摘要遺失（P5）；局部成功重試後的 A |
| `refused`「本次拒絕」 | 無 `last_estimate`、有 `last_refusal` | `daily-estimate-refusal-*`（`:481`） | 未到結算的失敗（P3）、額度用盡、新鮮度不足、撤回後未開始那支 |
| `unconfirmed`「執行狀態未確認」 | `window_claimed true` 且 estimate／refusal 皆 null | `daily-estimate-unconfirmed-*`（`:509`）＋`UNCONFIRMED_NOTE_ZH`（`:519`）＋`（視窗 {window}）` | claim 後即死（P1／P2）；局部成功裡的 B（P4） |
| `never_estimated`「沒有估計」 | 三者皆 null、`window_claimed false` | `daily-estimate-none-*`（`:493`） | 全新窗口 |

驗證層與它**沒有**證到的東西：文案與狀態機以源碼錨定方式驗證（`frontend/tests/dailyEstimate.test.mjs` 現值 **28 節點／666 列**（實測 `grep -c "^test("` 與 `wc -l`）。可指名屬本輪的兩支是 `:609`（逐技能撤回控件）與 `:653`（press 文案）；第八輪 09:13 現測該檔為 **8 節點／277 列**（本記錄 1586／1618 列），其後的 journey-2 群（`:503`–`:593`）落在今日 12:2x–13:1x 的前端改動裡。**逐輪歸屬無法由 git 分辨**（該檔未追蹤）⇒ 本節只宣稱「現值 28、全數 PASS」，增量的逐檔位元組留給 D 包；`assert.deepEqual` 五態各自不同標籤），前端全部 `tests/*.test.mjs` **180／180**，`npm run typecheck`（`tsc --noEmit`）無輸出。**這是源碼層證據，不是渲染層證據** ⇒ 瀏覽器所見屬 C 包、目前 **NOT RUN**。測試同時釘住：面板任何字串不得宣稱行程還在（`assert.doesNotMatch(PANEL, /每日執行中|仍在執行|正在執行|執行中的估計|還在做/)`），未確認卡必須寫明「有開工紀錄」「不證明它還在執行」「尚未實作的 lease」「也不會用這列存在多久」「不會因此退款／不會釋放預留／不會把終態改寫成失敗」「同一視窗可以再要求一次、不會再扣一次額度」；`window_claimed` 一經帳本／沿用／拒絕訊號存在就不准越位（`src/lib/dailyEstimate.ts:99-101` 的判定順序：帳本回落／沿用／拒絕任一存在時，claim 列不得越位）。

### 五、兩條正式入口的實際 diff（以及 git 為什麼給不出「第九輪」的 diff）

- **先把限制講清楚**：固定基線 `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` **完全不含**每日估計功能（現測：`git show HEAD:backend/app/api/personalization.py \| grep -c daily_estimate` ＝ **0**；`learning_scheduler.py` 同 0），而 `backend/app/services/daily_estimates.py`、`backend/tests/test_daily_model_estimate.py`、`backend/tests/test_daily_estimate_two_workers.py`、`frontend/src/lib/dailyEstimate.ts`、`frontend/src/components/training/DailyEstimatePanel.tsx`、`frontend/tests/dailyEstimate.test.mjs` 在 `git status --porcelain` 裡是 `??`（未追蹤）⇒ **第九輪之前那份內容在 git 裡不存在，沒有可 diff 的物件**。`git diff` 對 `personalization.py` 是 +356 行的**跨輪混合**（第四～九輪都尚未提交），拿它當「本輪 diff」會虛報改動範圍。
- 因此本節交的 diff 是**逐字前後**（第一節表 #1–#13：before 取自第八輪 §五 本記錄所引原文，屬同一份記錄、可查；after 為現讀 file:line），再加兩條入口的**實際呼叫鏈現值**：

```text
手動：POST /api/v1/personalization/daily-estimate/run        backend/app/api/personalization.py:454
        body 只帶 skill_unit_id；Depends(check_api_key_scope("write","inference"))
        ＋ Depends(check_kill_switch(KillSwitchScope.inference))  :458-459
        -> de.trigger_daily_estimate(db, current_user, body.skill_unit_id)
           app/services/daily_estimates.py:1199
           先查聲明範圍 :1222-1225（未聲明 → EstimateRefused("no_skills_enabled") → HTTP 400）
           -> run_daily_estimate_tick(scope="manual", window=estimate_window(),
                                      only_skill_unit_id=skill_unit_id) :1227-1232
排程：APScheduler 05:00 UTC（app/workers/learning_scheduler.py:182-185，
      DAILY_ESTIMATE_HOUR_UTC=5 @ daily_estimates.py:81）
        -> de.run_scheduled_daily_estimates()                daily_estimates.py:1184
        -> run_daily_estimate_tick(scope="daily", window=estimate_window()) :1150
共同：run_daily_estimate_tick 在函式內 import 共享 tick 主體
        from app.workers.learning_scheduler import _run_tick            :1166
        return _run_tick(..., tick_owner=_estimates_for_owner, ...)      :1168-1174
      -> _estimates_for_owner                                            :836
        window = window or estimate_window()                                :874
        job = _open_job(...)  ← 單獨 commit（learning_scheduler.py:456）
        recovered = settled_estimates(db, owner_id, window)                 :903
        summary_skills（僅時鐘讀，手動一律空）                                :907-910
        逐筆：if claim_estimate(db, owner_id, window, skill_id, instrument) :1002  ← 同一把座位
讀回：GET /api/v1/personalization/daily-estimate/status      personalization.py:421
        -> daily_estimate_status(...)  :1244（window_claimed :1354／輸出 :1395-1396）
        -> _daily_estimate_status 加 message_zh 文案                          :486
```

- 要一份可 `git apply` 的 diff，必須先把那六支未追蹤檔納版（`git add`＝Git 寫入）⇒ 本輪未做，寫進 **D 包**第六節；批准前後該包都**不自行執行 Git 寫入**。

### 六、可獨立審批正文（依第九輪規則重寫；四包各自可批、各自可停，互不隱含）

**三條共同前提**（任一包命中即停；是規則不是口頭保證）：
1. **不放寬安全閘**：`tests/conftest.py:79-100`、`test_safety_gate.py:25-30`／`:41-45`／`:110` 一字不動。要改用別的名字／port 屬 owner 的政策裁決，代理不自行修改。
2. **不碰他人的服務**：本機既有 5432 叢集與並行线的 54340 實例一律**不接、不停、不讀、不覆寫**；進場前以 `pgrep -fl postgres`＋`lsof -nP -iTCP:<port> -sTCP:LISTEN`＋`ls -l /tmp/.s.PGSQL.<port>` 逐條**現測**，被佔用 ⇒ 該包結語 **NOT RUN**（不是失敗的測試）。端口「現測未聆聽」只是空位，**不是**可用證據。本輪 13:2x HKT 現測快照（`pgrep -fl "next-server|uvicorn|postgres"`，ID 會變、進場時重查）：平行線的 `postgres -p 54340` pid 34471（PGDATA 在 `…/5062987d/recovery-20261114/int-accept-20261117/pgdata`）、`uvicorn … --port 8120` pid 55642、`next-server (v14.2.18)` pid 81307——三支都不接、不停、不讀、不覆寫。
3. **秘密走既有通道，且不再要求 owner 依草稿先建密碼檔**（第八輪 A-INIT 那三段 `openssl rand`／`alethra-r8-pw.*` 需求**本輪作废**）：一次性本機叢集所需密碼由**代理在 run 內以 `mktemp` 產生、權限 0600、內容永不出現在訊息／repo／run dir 以外**；`ALETHRA_PG_TEST_URL` 只在子 shell 內 export（`backend/scripts/run_tests.sh:141-152` 不印 DSN；輸出經 `tests/conftest.py:68-76 _safe_dsn` 遮蔽）。任何一包都**不請求**受保護 DB 的憑證。

#### A 包：PostgreSQL——七步，每一步可單獨批准、單獨中止

| 步 | 動作（可執行值） | 寫入範圍 | 判準（通過才准進下一步） | 中止條件 |
|---|---|---|---|---|
| A0 | 唯讀預檢：`pgrep -fl postgres`；`lsof -nP -iTCP:5432 -sTCP:LISTEN`（必須 0 列）；`ls -l /tmp/.s.PGSQL.5432`（必須不存在）；`ls -d $PGDATA $SOCKDIR`（必須都不存在）；`"$PGB/initdb" --version`／`"$PGB/psql" --version`（必須都是 16.2）；`git rev-parse HEAD`（必須 `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`）；第八輪 §二 那 8 檔逐一 `shasum -a 256` 與記錄現值逐字比對 | 只寫 `$RUN/identity.txt` | 全中 | 5432 已被聆聽／socket 檔存在／`$PGDATA` 或 `$SOCKDIR` 已存在（**先回報，不覆寫、不刪除**）／版本不符／HEAD 或 sha256 不符 ⇒ NOT RUN |
| A1 | `initdb -D $PGDATA -U alethra_r8_bootstrap -A scram-sha-256 --pwfile="$RUN/pw.bootstrap"`（`$RUN/pw.bootstrap` 由 `mktemp` 產生，0600）；`mkdir -p $SOCKDIR && chmod 700 $SOCKDIR`。**只建叢集**：不建 DB、不建表 | 新目錄（獨佔建立） | rc=0 | 非零 ⇒ 停，保留 `$RUN/pg.log`，**不重試第二次、不刪 PGDATA** |
| A2 | `pg_ctl -D $PGDATA -l "$RUN/pg.log" -w start -o "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB"` ＋ `pg_isready -h 127.0.0.1 -p 5432` | 起一支新行程（自己的 PGDATA） | `pg_isready` 回 ready | 非零或不 ready ⇒ 停；正常停止程序 `pg_ctl -D $PGDATA -m fast stop`，用 A0 抓到的 pid 以 `pgrep`／`lsof` 核對已退出（**不**以已停端點的 HTTP 回應證明停止）。停止不等於刪資料 |
| A3 | 以 `alethra_r8_bootstrap` 連線：`CREATE ROLE alethra_r8_migrator LOGIN PASSWORD …`、`CREATE ROLE alethra_r8_app LOGIN PASSWORD …`、`CREATE DATABASE alethra_owner_test OWNER alethra_r8_migrator`、`GRANT CONNECT, CREATE ON DATABASE … TO alethra_r8_migrator`、`GRANT CONNECT … TO alethra_r8_app`；`\c alethra_owner_test` 後 `GRANT ALL ON SCHEMA public TO alethra_r8_migrator`、`ALTER SCHEMA public OWNER TO alethra_r8_migrator`、`REVOKE ALL ON SCHEMA public FROM PUBLIC`（PG 15+ 之後 `public` 不給 PUBLIC 建表權，16.2 上這是 alembic 能落地的**必要**授權，不是預防性多給） | 新 DB＋兩個角色 | 讀回（存 `$RUN/roles.txt`）：`pg_roles` 的 `rolsuper/rolcreatedb/rolcreaterole/rolbypassrls` 對 migrator 與 app **四旗標全 `f`**（`test_safety_gate.py:110` 查的就是這四欄）；`pg_database.datdba=alethra_r8_migrator`；分別以 migrator／app 連線時 `usesuper=f` | 任一不符 ⇒ 停，不進 A4。bootstrap 到此不再被任何應用或 alembic 連線使用 |
| A4 | 空庫 → 固定修訂的**全鏈**升級：`cd backend && . "$DSN" && export TEST_MODE=true` → `alembic current`（新庫預期「無版本」；實際起始值＝UNKNOWN，本機从未在任何 PG 讀過）→ `alembic heads` → `python scripts/check_migration_tail.py --json`（讀**檔案系統**的 ScriptDirectory，不連 DB）→ `alembic upgrade c3d4e5f6g7h8` → 再讀一次 `alembic current` 存 `$RUN/alembic.txt` | 該隔離 DB 的 DDL | 鏈＝`e1f2a3b4c5d6 → f2a3b4c5d6e7 → b2c3d4e5f6g7 → c3d4e5f6g7h8`，head 必等於 `c3d4e5f6g7h8`（`scripts/check_migration_tail.py:54-59` 的字面值） | `current` 回報任何既有版本／`heads` 多於一個／head 不符／`DUPLICATE_REVISION`｜`BRANCHED_TREE`｜`MISSING_DEPENDENCY` 任一 ⇒ 停。**不 `--autogen`、不 downgrade、不 drop、不執行任何其他 migration**；失敗保留現場，清場需另行批准 |
| A5 | 以 migrator 連線**唯讀**核對實際 schema：`decision_records.linked_inference_id` 的 `data_type`（期望 `uuid`）、`pg_constraint` 對該表的 FK（`linked_inference_id` 出現 FK ⇒ 停）、`pg_indexes`（`decision_records`／`human_decision_events`／`usage_records`）、`information_schema.columns`（`usage_records`／`learning_jobs`／`quota_reservations` 逐欄） | 否 | 與第八輪已交的「離線建表讀回」逐欄對照，差異逐條列出（**不解釋成等價**） | 有 FK／欄位型別不符 ⇒ 停並回報 |
| A6 | 兩行程競態（本包唯一能回答「有沒有可靠互斥」的步驟）：`cd backend && . "$DSN" && DATA_PROVIDER=demo ./scripts/run_tests.sh pg tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once tests/test_daily_model_estimate.py::test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice` | 該隔離 DB | 見下段 | 60s 等待逾時／`Barrier` 未湊齊／時間戳斷言失敗 ⇒ 該次是 **NOT RUN／INVALID**，不得寫成 PG 競態 PASS；現場保留給 owner，不刪 `market_data_bars`（無 owner 欄可 scope，屬共享種子） |

A6 的硬性前提（寫進 run dir）：測試帳號的有效額度 **≥ 4**（扇出＝2 支 instrument × 2 行程；無 `PilotEntitlement` 列時走 free plan `ai_usage_limit=10`，`app/auth/entitlements.py:66-71`；有列時讀該欄、預設 20，`app/models/pilot.py:97`）。**B 包的 `ai_usage_limit=1` 不得套在 A6**，否則額度會把「錯誤的第二次扣額」掩蓋成 429——判準以**帳本 row count** 為準，每 (owner, skill, instrument, window) 恰好 1 列 `metadata["via"]=="daily_service"`、每列恰好 1 筆 `settled` 預留、非終態預留＝0、落敗那支的 `learning_jobs.result_json.refused` 以 instrument id 具名 `already_estimated_this_window`。**兩個 job 摘要的 `billed_in_window` 不相加**（同一帳本的兩個瞬間），窗口總數只引用 row count。同步機制現讀：`mp.get_context("spawn")`、`ctx.Barrier(2)`（`:218`／`:418`）、`_TIMEOUT_S = 60`（`:127`）、斷言 `max(claim_enter) < min(estimate_enter)`、連線層 `SET lock_timeout='30s'`／`statement_timeout='120s'`／`idle_in_transaction_session_timeout='90s'`（`:189-191`）。

#### B 包：05:00 自然觸發觀察（一支行程、一個隔離 DB、只觀察一次）

| 項目 | 可執行值 |
|---|---|
| 後端啟動 | `cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8031 --log-level info >> "$RUN/service.log" 2>&1`；啟動前 `pgrep -fl uvicorn` 只准出現禁區的 8120；啟動後把**本輪抓到的 pid** 寫進 `$RUN/service-pid.txt`（8031 本輪現測未聆聽，進場仍重查） |
| 停止 | `kill -TERM "$(cat $RUN/service-pid.txt)"` 後以 `pgrep -f "uvicorn app.main:app --host 127.0.0.1 --port 8031"`＋`lsof -nP -iTCP:8031 -sTCP:LISTEN` 核對；**只停自己這支 pid**；不用已停端點的 HTTP 回應證明停止；停止不等於刪資料 |
| 日誌落點 | 以 `lsof -p <本輪 pid>` 實際確認 stdout／stderr 落點（不是宣稱） |
| DB | A3 之後的 `alethra_owner_test` ＋ **`alethra_r8_app` 角色**的 DSN（應用永不用 superuser，也不用 migrator）；或 `DATABASE_URL=sqlite:///$TMPDIR/alethra-r8-cron.db`。**一個**固定隔離 DB，候選與該 DB 綁定，不连 54340、不用受保護 DB |
| 只掛一支 lane | `ENABLE_LEARNING_SCHEDULER=true` ＋ `LEARNING_SCHEDULER_JOBS=daily_model_estimate`（未設 allow-list 時會用 `DEFAULT_LEARNING_SCHEDULER_LANES`，`app/config.py:55/:211`，相鄰 lane 的 cron 是 02:00／03:00 ⇒ 會把 research／replay 一起註冊）；每日估計掛在 **05:00 UTC**（`app/workers/learning_scheduler.py:182-185`，常數 `daily_estimates.py:81`） |
| 讀回證明 | `registered_lane_ids()` 只回 `daily_model_estimate`；`GET /api/v1/admin/collection/status`（`app/api/admin.py:89`）的 `enabled`／`running` 均為假且 `AUTO_COLLECTION_ENABLED=false`；`LIVE_TRADING_ENABLED=false`（`app/config.py:172`），daemon `main()`（`app/services/live_trading_daemon.py:471`）不執行 |
| 帳號與資料 | `POST /api/v1/auth/register`（`app/api/auth.py:31/:46`，`risk_acknowledged=true`）新建的**本輪測試 owner**＋`POST /api/v1/auth/login`（`:101`）；`DATA_PROVIDER=demo`；模型＝該測試 owner 自己名下 1 支種子個人模型版本。**不碰任何真實帳號**：不對既有使用者呼叫 `grant_daily_estimate` |
| 額度（真上限） | 該測試 owner 的 `PilotEntitlement.ai_usage_limit = 1`（欄位與預設 `app/models/pilot.py:97`）⇒「一天一筆」由額度**實際強制**（超出時 `reserve_quota` 429，`entitlements.py:252-285`）；扇出上限 `LEARNING_SCHEDULER_MAX_OWNERS` 在 B 包設為 **1**（欄位與預設 25＝`app/config.py:153`；`<1` 時 validator 於 `:311-314` 直接拒設定）。此上限證明的是可控成本，**不代替 A6**，且**不得**拿去當 A6 的額度（見 A 包） |
| 網路限制（外呼＝0 的可核對做法） | 進程啟動前後各一次 `lsof -nP -p "$(cat $RUN/service-pid.txt)" -a -i` ⇒ 除 `127.0.0.1` 之外的對端列數必須為 **0**（05:00 那一響前後各做一次並存檔）；以 `ps eww -p <pid>` 只在 owner 面前做**存在性**檢查（不回顯值）核對環境中不含 provider 金鑰。本包不外呼 provider、不安裝依賴 |
| 只觀察自然觸發 | 只等 **05:00 UTC（本地 13:00）自己響一次**；手動 tick（`POST …/daily-estimate/run`）與「抹除 `result_json` 再跳」是**另兩件要各自批准**的事；05:00 那一輪**不抹 `result_json`、不改系統時間** |
| 逐步判準 | ①05:00 前：`learning_jobs` 無當日 window 列、`usage_records` 無 `via=daily_service` 當日列 ②響後：`learning_jobs` 出現 `job_type=daily_estimate` 且 `config_json.window=<當日 UTC 日>` ③`usage_records` 當日恰好 1 列 ④`GET …/daily-estimate/status` 的 `last_estimate.inference_id` 指回同一列 ⑤訂單數 0、`decision_records`／`human_decision_events` **不因排程增加** ⑥replay 進度不推進 ⑦「同窗不重複扣額」在本包只算**待觀察預期**（其互斥證據在 A6） |

#### C 包：瀏覽器——兩條真實旅程（不得以 HTTP 補丁冒充）

- **固定 build 模式與產物隔離**：只准 `npm run build`（`frontend/package.json:7`）一次，記 rc 與**新的** `.next/BUILD_ID` 現值；舊 BUILD_ID 不得作為候選證據。**`NEXT_PUBLIC_API_URL` 是 build-time 內嵌變數**（`frontend/src/lib/api.ts:11`，預設 `http://localhost:8000`），必須在 build **之前** export 指到 B 包那支 8031；建置時以 `NEXT_DIST_DIR="$RUN/next-dist"`（或在 `$RUN` 內另建一份 checkout）產出，**不覆寫共用 `.next`**；機器上另有平行線的 next-server（本輪現測 pid 81307；快照與重查規則見第六節共同前提 2）⇒ 不接、不停、不讀。啟動：`npm run start`（`:8`，先 `-p 3031 -H 127.0.0.1` 明確指定）或 `npm run dev -H 127.0.0.1`（`:6`）。
- **URL**：`http://127.0.0.1:3031`（進場當時以 `lsof -nP -iTCP:3031 -sTCP:LISTEN` 重查；啟動前後各印一次程序清單）。
- **DB／帳號**：與 B 包同一個隔離 DB、同一測試 owner；在 `src/app/login/page.tsx` 以密碼登入 ⇒ `actor_type=human`（`app/auth/provenance.py:30`）。
- **旅程一（/training 每日估計面板）**：開同意（`PUT …/consent`，僅本人，`:303` 的 provenance 閘；面板 `daily-estimate-instruments-*`／`-maxage-*`／`-grant`）→ `GET …/consent` 讀回 `scope_json.skill_units[]` → 「立即估計」→ **第二次「立即估計」**：同一畫面並列 `已沿用`＋`本次拒絕`（＋若另有未開始的帳號）`執行狀態未確認`／`沒有估計` → **逐技能撤回**（本輪新增控件 `daily-estimate-withdraw-*` → `DELETE …?skill_unit_id=`）→ 讀回另一支的聲明與帳本**未變**。撤回時機一句話必須在畫面上讀得到（`WITHDRAWAL_TIMING_ZH`：已在執行中的那筆不退還）。
- **旅程二（/paper-portfolio/[id]）**：`pp-download-decision-history`（CSV）與**本輪已接上按鈕的** `pp-download-decision-history-json`（`src/app/paper-portfolio/[id]/page.tsx:470`）→ 兩檔同一決策集合、無訂單決策不得造出交易列、`inference_source_state` **三態**（`present`／`missing`／`none`，`src/lib/decisionLineage.ts:11`；後端欄位自陳 `backend/app/api/paper_portfolio.py:1673`）標籤顯示在列上（`page.tsx:1004`）；模擬頁面手動決策送 `linked_inference_id`（`src/app/experiments/[id]/simulation/page.tsx:515`、`src/lib/api.ts:443/:485`）並從列表／匯出讀回同一 id。
- **邊界（報告必附）**：自動化只能做可用性輔助，**代理不得替 owner 按下同意／撤回**；build PASS／node:test PASS 不代替 browser PASS；未批准前本包 **NOT RUN**。
- **仍以源碼層如實標記的缺口**：`HumanDecisionEvent` 無血緣欄位（#223 的結論仍成立）⇒ 以該表為寫入點的畫面**不能**用「request 有欄位」充當已持久化；需批准的最小 schema 方案見第八節 3。

#### D 包：可審候選（逐 hunk patch＋乾淨克隆重現，本輪未執行）

- 基線：`5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（本輪 `git rev-parse HEAD` 現測值）。產出物一律進 `$RUN/candidate/`：
  1. `tracked.patch`＝**只含本記錄點名檔案**的 hunk（`git diff -- <逐檔路徑>`，逐檔列出，**禁止** `git add -A`／整檔夾帶他人變更）；
  2. `untracked-files/`＋`untracked.sha256.txt`＝那六支 `??` 檔的完整位元組與雜湊（測過的檔：`daily_estimates.py` 1405 行、`test_daily_model_estimate.py`、`test_daily_estimate_two_workers.py`、`dailyEstimate.ts` 197 行、`DailyEstimatePanel.tsx`、`dailyEstimate.test.mjs`）；
  3. `apply.sh`＝在**全新 `git clone --no-hardlinks <repo> $RUN/clean-HEAD`** 內依序套 patch、放回未追蹤檔、印出 `git status --porcelain` 與樹 digest。
- 依賴閉包（現讀 import）：`daily_estimates.py` → `app/workers/learning_scheduler.py`、`app/api/personal_models.py`、`app/auth/entitlements.py`、`app/models/{learning_job,paper_portfolio,pilot}.py`、`app/services/{billing,kill_switch,decision_lineage}.py`；`DailyEstimatePanel.tsx` → `src/lib/{api,dailyEstimate,decisionLineage}.ts`。缺任何一支 ⇒ 克隆建置失敗 ⇒ 該包結語寫「閉包不完整」，不寫「候選坏了」。
- 排除項：`.venv/`、`.next/`、`node_modules/`、run dir、`backend/.env*`、任何 `*.db`、`EXECUTION_*`／`DECISION_*` 記錄（記錄不進候選）。
- 驗證（**不碰共用工作樹、不 `git fetch`／`pull`、不推任何東西**）：克隆內 `DATA_PROVIDER=demo ./scripts/run_tests.sh sqlite tests/test_daily_model_estimate.py tests/test_daily_estimate_two_workers.py` ＋ `node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyEstimate.test.mjs`。
- 若之後需要 Git 寫入（把未追蹤檔納版，才能產出可 `git apply` 的 diff）：先做精確操作——逐檔 `git add <path>` → `git diff --cached --stat`＋`--name-status` 交 owner 過目 → owner 明確同意後才 commit／push。**批准一次不等於後續都批准**；本包也**不在保存包內重新釘選**任何選擇（不改節點清單、不改 skip 上限、不改安全閘）。

### 七、本輪驗證帳本（跑過什麼、實際數字）

| 物件 | 命令 | 結果 | run dir／tree-digest |
|---|---|---|---|
| 估計 lane（後端，兩檔） | `cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh sqlite tests/test_daily_model_estimate.py tests/test_daily_estimate_two_workers.py` | collected **42**、passed **41**、skipped **1**（`:376-377` 具名跳過）、failures **0**、lane exit **0** | `sqlite-20261119T050904Z-79369`／`501306060850a60126ee9f881e4c8f4f88d29bb7183ef1a44fa19e9a19728cf7` |
| 讀回探針（一次性，跑完即刪，現工作樹已無此檔） | 同 runner ＋ `tests/test_zz_round9_readback_probe.py -s` | 10 passed／exit 0；25 段 payload 全文已抄進本節第三節（不再依賴 `/tmp`） | `sqlite-20261119T050737Z-78957`／`9e22296dea65e854cbfc59273c5b4d1a251c57dc9f1d0d8bf4f9ed9c9bf6e349` |
| 新節點單獨確認 | 同 runner ＋ `-k "claim_row_that_outlived or said_nothing_is_unconfirmed or lost_summary_still_names"` | 3 passed／exit 0 | `sqlite-20261119T050357Z-77573`／`a4579c826643702e349ad9fcb1e0e62794669b9efda1fc96d054bf266785569f` |
| 完整 sqlite 套件（重釘清單用） | `cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh sqlite` | collected **3,811**；**3 failed／3,632 passed／179 skipped**，`2377.50s (0:39:37)`（13:12:57 起、13:52:47 HKT 落 exit-code），`exit-code: 1`；`tree-digest-after` ＝ before（tree 未中途變動）；帳本自洽：`reported units 3814 = collected 3811 ＋ collection-level 3`、unresolved 0、duplicate 0、collected-but-no-record 0。**三支 ✗ 逐條對到物件（全不在估計 lane，且不是本輪改的檔）**：①`tests/test_process_state_isolation.py::test_the_environment_the_suite_measures_is_the_environment_it_documents`（AST 閘抓到 `tests/test_learning_scheduler.py:515`／`:516` 兩處 `Settings(...)` 沒帶 `_env_file=None`）；②`tests/test_replay_credibility.py::TestReplayOwnership::test_user_id_bound_into_trades_query`（`:178` 要 `user_id` 綁成字串，實測綁的是 `UUID(...)` 物件）；③`tests/test_research_run_admission.py::test_the_linkage_revision_is_unique_and_the_chain_has_one_head`（`:718` 期望 head `b2c3d4e5f6g7`，實讀 `c3d4e5f6g7h8`——`scripts/check_migration_tail.py:54-59` 已釘後者，這個節點常數沒跟上）。**同一份 `junit.xml` 讀回估計 lane**：`test_daily_model_estimate` **40 passed／0 failed**、`test_daily_estimate_two_workers` 1 passed＋1 具名跳過（＝lane 那次的 42 支），第三節那三支新節點與 `:1862` 反轉節點在此完整 run 內逐支 `passed` | `sqlite-20261119T051257Z-80770`／`501306060850a60126ee9f881e4c8f4f88d29bb7183ef1a44fa19e9a19728cf7` |
| 重新釘選 selection／ceiling（本輪最後一步） | `cd backend && .venv/bin/python scripts/run_evidence.py <上列 run dir> pin --manifest-out tests/collected_nodes.txt --budget-out tests/skip_budget.json --ack-diff 4805bca2d4590f24627cfc6eafebe1cac91a2684a2ca80dd96b9fc2ec856074f`（**兩輪**：13:53 第一輪不帶 `--ack-diff` ⇒ rc=2、只印出 diff＋digest；讀過 diff 後第二輪才寫檔） | selection diff `-11 +274`；`tests/collected_nodes.txt` 現值 **3,811** 列、`tests/skip_budget.json` 現值 `collected=3811 total=179 pg_only=159 live_stack=18 other=2`；對同一 run dir 再跑 `check` ⇒ `evidence gate: ledger closes against this run's own artifacts`（13:54 HKT 現測）。`other` 由 1 變 2 的兩支具名：`test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once`（SQLite 取不到 advisory lock，本 lane 自己的具名跳過）＋`test_fixture_failure_injection.py::test_real_db_commit_failure_during_cleanup`（`BLOCKED: Cannot intercept cleanup commit without complex mocking`，非本輪）。**釘的是「收集到什麼」，不是「通過什麼」**：同一 run 的 3 支 ✗ 不會因為重釘而消失 | 同一 run dir／tree `50130606…`（清單與 budget 不是 `*.py`，不動 tree-digest） |
| 重釘後的估計 lane（確認 pin 沒有把閘門改成綠的假象） | `cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh sqlite tests/test_daily_model_estimate.py tests/test_daily_estimate_two_workers.py` | collected **42**、passed **41**、skipped **1**（同一支具名 advisory-lock 跳過）、failures **0**、exit **0**；`reported units 42 = collected 42 ＋ collection-level 0`、`evidence gate: ledger closes`；明確選取時整包 ceiling 不適用（runner 自己這樣寫） | `sqlite-20261119T055623Z-98567`／tree `501306060850a60126ee9f881e4c8f4f88d29bb7183ef1a44fa19e9a19728cf7`（與重釘前同一個 tree ⇒ 重釘只動了兩個非 `*.py` 檔）；13:57:08 HKT |
| 前端估計卡 | `cd frontend && node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyEstimate.test.mjs` | **28** tests／28 pass／0 fail（13:36:43 HKT 現測，最終於改完 `DailyEstimatePanel.tsx`／`dailyEstimate.test.mjs` 之後重跑） | 該檔現值 28 節點；第八輪 09:13 記錄為 8 節點，逐輪增量無 git 歷史可分 ⇒ 不宣稱歸屬 |
| 前端全部 | 同上，`--test tests/*.test.mjs` | **180** tests／180 pass／0 fail（同一次 13:36:44 HKT 現測） | — |
| 前端型別 | `cd frontend && npm run typecheck`（`tsc --noEmit`） | 13:36:45 HKT 現測，無輸出（乾淨） | — |

**披露（tree-digest 的涵蓋範圍，先講它能證到哪裡）**：`backend/scripts/run_tests.sh:345-351` 的 `tree_digest()` 只雜湊 `backend` 下的 `*.py`，**不含前端檔**，所以本節所有 tree-digest 都是「後端樹」。實測現值 `501306060850a60126ee9f881e4c8f4f88d29bb7183ef1a44fa19e9a19728cf7` 與第七節第 1 列（估計 lane）、第 4 列（完整套件）的 `tree-digest-before` **相同** ⇒ 後端證據落在同一棵樹上，估計 lane 不必重跑即可代表最終後端樹。該完整 run 起飛之後，本輪又改了兩支前端檔（`DailyEstimatePanel.tsx` 的 press 文案與逐技能撤回控件、`dailyEstimate.test.mjs` ＋2 節點）；這兩支不在上述 digest 涵蓋內，其最終位元由第七節最後三列（28／28、180／180、`tsc --noEmit` 無輸出）在**改完之後**另行驗證，本節定稿後未再動前端。第 3 列（`-k` 三節點）跑在較早的 `a4579c826643702e349ad9fcb1e0e62794669b9efda1fc96d054bf266785569f`（探針檔與探針結果尚未定稿時）；這三支節點其後都在最終樹的 42 節點 lane 內逐支 PASS（讀 `sqlite-20261119T050904Z-79369/junit.xml`：三支 `testcase` 皆無子節點＝passed），故「最終樹覆蓋」以第 1 列為準、不是第 3 列。清單重釘以第 4 列那個 run 自己的 `collect.txt` 為準，不在本節手抄。

### 八、本輪仍未執行／仍未證明＋交回 owner 的決定

**NOT RUN／UNKNOWN（逐條，不得以別的層推定）**：
- PostgreSQL 全部步驟（A0–A6）＝**NOT RUN**（未批准）；`pg_advisory_xact_lock` 是否足以擋住第二支＝**UNKNOWN**；④-sqlite 的機制節點 PASS 不是 PG 競態 PASS。
- 05:00 自然觸發（B 包）＝**NOT RUN**；真實服務行程＋真實帳本（C 包的 API base 所指）＝**NOT RUN**。
- 瀏覽器兩旅程（C 包）＝**NOT RUN**；`node:test` 180／180 只證「測試讀的源碼」與「頁面用的源碼」是同一份，不證渲染。
- D 包（逐 hunk patch＋乾淨克隆重現）＝**尚未執行**；可 `git apply` 的 diff 需要先納版六支未追蹤檔（Git 寫入，未批准）。
- **行程級**崩潰（kill -9、斷電、連線洩漏）與 05:00 排程行程下的六情境＝**未測**（理由見第三節邊界段）。
- 未修改的部分不自稱修好：本輪只動第一節 #1–#13（其中 #11–#13 只動前端，`simulations.py`／`paper_portfolio.py` 的後端血緣邊界未改）；`window_state`、摘要的提交邊界、額度規則都是既有行為，本輪證據**未**擴展它們。
- 清單重釘（**本輪已執行，不是待辦**）：13:53 之前 `backend/tests/collected_nodes.txt` 仍是 **3,548** 列（provenance run `/tmp/alethra-test-logs/final-20261117/run`、`collected-node-ids: 3548`、樹 `72dcb3d1…`），本樹 collected **3,811** ⇒ 淨差 263 只是兩個計數之差。本輪按第七節最後一列那一步，由 run `sqlite-20261119T051257Z-80770` 自己的 `collect.txt` 走兩輪 `pin --ack-diff` 重釘，`pin` 印出的真實 diff 是 **`-11 +274`**：`-11` ＝ `tests/test_learning_scheduler.py` 8 ＋ `test_learning_api.py` 1 ＋ `test_personal_model_comparison.py` 1 ＋ `test_personal_model_permission_matrix.py` 1（class 式節點被並行線改寫成函式式）；`+274` 分散在 22 支檔，最大四支 `test_daily_model_estimate.py` 40／`test_simulation_daily_tick.py` 37／`test_learning_scheduler.py` 28／`test_decision_lineage.py` 26 ⇒ 估計 lane 的節點從「**全不在**頭部清單」變成在（現測 `grep -c "test_daily_model_estimate\|test_daily_estimate_two_workers" tests/collected_nodes.txt` ＝ **42**、`wc -l` ＝ **3,811**）。`tests/skip_budget.json` 由同一 run 同一步重釘為 `total=179／pg_only=159／live_stack=18／other=2`，`check` 對該 run 回「ledger closes」。**這不代表套件全綠**：同一 run 有 3 支 ✗（第七節逐條歸因到他線），重釘只移動「收集到的節點」與「跳過上限」。

**交回 owner 裁決（本輪不做、不隱瞞）**：
1. **計價失敗的 press 回 201**：`POST …/run` 對 `prediction_failed` 回 **201**＋`job.estimated==0`，拒絕細節只在 `status.last_refusal`（內層才是 422）——P3 現測。要改成 4xx／200 屬契約裁決，會影響已釘節點 `test_the_http_door_and_the_service_report_the_same_refusal`（`:1825`）。
2. **撤回後的顯示語意**：`DELETE` 之後 status 對該帳號回報 `disabled_reason="not_declared"`，面板標籤讀成「未聲明（預設關閉）」（`DailyEstimatePanel.tsx:353`），抹掉本人「先聲明、後撤回」這個事實；撤回的持久證據只在 `daily_estimate_consent_revoked` 事件列，而 status 讀取不查它。最小修法（**未實作**，需批准）：`daily_estimate_status` 對 `declared=false` 且存在撤銷事件者回 `disabled_reason="consent_withdrawn"`，面板 `DISABLED_LABELS` 加對應文案「本人已撤回（先前曾聲明）」。
3. **`HumanDecisionEvent` 無血緣欄**：若要把「本人對某次估計的決策」寫進那張表，需要一個新欄（例：`linked_inference_id UUID NULL`＋`FK → inference_records(id) ON DELETE SET NULL`，或保守到只存 `text` 並在讀回時以 owner 核對）＋一支 migration；本輪**不執行 migration、不建表**。目前具備血緣的持久寫入點是 `decision_records.linked_inference_id`（#223 的結論）。
4. **durable lease／心跳未實作**：`執行狀態未確認` 只證明「有開工紀錄」，不證明進程與否；不以 age 或 `running` 字串判死，也不據歷史 running 列退款／釋放預留／改寫終態（面板文案已如此寫並由測試釘住）。要讓平台能說「還在跑」或「已死」，需要 lease／heartbeat 自己的那一包（新政策，不是本輪可順手做的事）。

**第八輪 §四／§五／§六 的過期行更正**（在同一份記錄內就地加〔第九輪更正〕前向指引，不另開新檔）：
- 第八輪 §四（C 包旅程表，該記錄 1881／1882／1883 列）三條「畫面未接通」現已被本輪反轉：逐技能撤回控件（現測 `DailyEstimatePanel.tsx:388-399`）、`decisionHistoryJson` 由零呼叫點變成 2 個（現測 `page.tsx:303`／`:346`＋按鈕 `:470`）、模擬頁面送 `linked_inference_id`（現測 `simulation/page.tsx:515`）；**同一列的 `OrderPreviewPanel.tsx:417-421` 仍未送該欄**（現測 `grep -c` = 0）⇒ 更正為「模擬頁面已接、工作區面板未接」，不是全線接通。
- 第八輪 §五 缺陷 1（手動入口不去重）＝**本輪已修並反轉測試**（第一節 #1–#5、#8）。第八輪 §六 對應的「手動去重修復與 `:1657-1667` 反轉＝本輪之後的工作」也隨之結清：那支節點現在是 `backend/tests/test_daily_model_estimate.py:1862`（`test_the_manual_button_and_the_clock_write_the_same_estimate`，實測 `sed -n` 現值），第二節 8 列即其讀回。
- 第八輪 §五 缺陷 2（摘要未寫 ⇒ 永久 `running` 列）＝**部分處理**：本輪只改了**顯示語意**（不再聲稱終態、`window_claimed` 與帳本回落分開）與**重試語意**（該列不再阻塞重試、也不被重試改寫）；**終態轉換與 durable lease 仍未實作**（第八節 4）。
- 第八輪 §六「#226 六情境矩陣中（b）（d）兩格仍需以新節點量到『正式 HTTP 讀回』等級」＝**本輪已補**（第三節 P1／P2／P4／P5 ＋新節點 `:1302`），但仍非 PG、仍非真實服務行程。
- 第八輪 §六「D 包本輪尚未交」＝仍成立（本輪交正文與閉包清單，未執行）。

---

## 第十輪（2026-11-19 16:5x–18:2x HKT）：三失敗逐一定根因並收斂、整套在固定候選 `a2641cca…` 上重驗、A–D 正文換成本輪實測值

固定候選（後端樹）：`a2641cca89559985a24dc3ead6b041639e9305ba88c61092e05106fa1842f18b`。本輪樹的演進：`85bf9b27…`（三失敗確認第一次）→ `401d32e0…`（估計 lane 加節點）→ `7f356127…`（estimate-lane2，**exit 1**）→ `a2641cca…`（冻结後跑整套＋三支確認）。`tree_digest()` 只雜湊 `backend/**/*.py`（`backend/scripts/run_tests.sh:345-351`），前端檔與 `alethra-evidence/**` 不動這個值。

### 一、客戶結果（先交能看到的那一层）

| # | 客戶讀到什麼 | 本輪改了什麼使其成立 | 證據層級／具名節點 |
|---|---|---|---|
| 1 | 按一次「立即估計」後，畫面分得清**新估計／沿用／部分成功／全部失敗**四態 | `run_daily_estimates` 的回傳與 `job.result_json` 現在都帶 `failed`（`app/services/daily_estimates.py`：`"failed": len(errors)`），前端 `dailyEstimate.ts` 以 `estimated>0 && (refused>0‖failed>0)` 判「部分成功」，`estimated==0 && refusal_reason=="already_estimated_this_window"` 判「沿用」，其餘判「全部失敗」 | ④-sqlite＋⑤-HTTP（in-process）：`test_a_press_whose_engine_died_names_the_failure_in_the_response`（`tests/test_daily_model_estimate.py:2253`，讀 201 body 的 `job["failed"]==1`＋`status=="failed"`）、`test_a_partial_tick_settles_only_the_skill_whose_engine_answered`、`test_a_tick_where_every_engine_died_settles_nothing`（`:2293`） |
| 2 | **全部失敗不加帳、部分失敗只結算落地那支**，且不留懸空預留 | 既有閘門順序未動，本輪只是把它量出來 | `:2293` 斷言 `settled==0`、`reserved==0`、`released==2`、`billed_in_window==0`；partial 節點斷言帳本 `metadata["skill_unit_id"]` 恰等於 answered 那支 |
| 3 | 「本來就沒聲明的帳號」不再被畫面講成「本人已撤回」 | status 對 `declared=false` 区分「未聲明」與「已撤回」的依據（撤回走事件列，未聲明沒有事件列） | `test_a_skill_nobody_declared_is_not_reported_as_withdrawn`（本輪新增，在 drift 的 9 支之內） |
| 4 | 決策血緣從**按下**到**匯出檔**是同一条：模擬頁／/training 面板／事件門各自寫到哪張表，都已閉合並讀回 | `HumanDecisionEvent` 有自己的 `linked_inference_id`（migration `d4e5f6g7h8i9`，未批准、未執行；本輪只有 SQLite `create_all` 層級的讀回）；修正路徑（`POST /human-decisions/{id}/correct`）不再把血緣抹掉 | ⑤-HTTP：`tests/test_manual_decision_books_linked_portfolio.py::TestLineageTravelsFromSubmitToExport` 12 支全 PASS，其中本輪新增 5 支（見 §三 drift 清單）；lane 實測 collected **50**／47 passed／3 skipped／exit **0** |
| 5 | 逐技能撤回只動那一個技能帳號 | 既有實作，本輪钉住 | `test_withdrawing_one_skill_does_not_silence_the_other_press` |
| 6 | `POST …/daily-estimate/run` 回 **201** 仍只代表「建立了這次嘗試」 | 沒有改狀態碼（改語意屬契約裁決，見 §五 交回項 1）；本輪把「201 不等於估計成功」這句話釘在文案與節點上，且 `failed` 讓 201 body 自己能說明發生了什麼 | `test_the_http_door_and_the_service_report_the_same_refusal`（既有钉位）＋上面第 1 列三支新節點 |

**三入口實際寫去哪張表（第十輪現讀源碼，非推定）**：

| 入口（畫面→client→route） | 實際寫入 | 範圍／邊界 |
|---|---|---|
| 模擬頁 `src/app/experiments/[id]/simulation/page.tsx:515` → `api.simulations.manualDecision`（`src/lib/api.ts:434-446`）→ `POST /api/v1/simulations/{id}/manual-decision` | `decision_records.linked_inference_id` ＋ `human_decision_events.linked_inference_id`（`app/api/simulations.py:2583` 那支事件）＋ `simulation_decisions.decision_detail` JSONB | 三個寫入點同一筆 id；`hold` 不帶 `linked_inference_id`（路由層拒絕） |
| /training 面板 `src/components/training/PersonalStrategyLearningPanel.tsx:1456-1464` → `api.paperPortfolio.createDecision`（`src/lib/api.ts:475-490`）→ `POST /api/v1/paper-portfolio/{skill_unit_id}/decisions` | **只有** `decision_records.linked_inference_id` | 這張表**不**經 `HumanDecisionEvent`：以该面板為物件時，事件表沒有列属設計，不是漏寫；`actor_type` 由憑證改寫（`app/auth/provenance.py:30`）不采信 body |
| 事件門 `POST /api/v1/human-decisions`（`app/api/human_decisions.py:173`）與 `POST …/{id}/correct`（`:290`） | `human_decision_events.linked_inference_id`（＋索引 `ix_human_decision_events_linked_inference_id`） | **兩張表的欄位都沒有 DB 級 FK**（`migrations/versions/c3d4e5f6g7h8_….py:88-93`、`d4e5f6g7h8i9_….py:85-89` 只 `add_column`＋`create_index`）；完整性在 `app/services/decision_lineage.py::validate_linked_inference` 於寫入前核對 |
| 工作區 `OrderPreviewPanel.tsx` | 不寫血緣 | 現測 `grep -c linked_inference_id src/components/OrderPreviewPanel.tsx` ＝ **0**；它也不是決策持久化入口，不得替上面三個入口宣稱通過。**〔第十三輪更正，2026-11-20〕該列兩句都已過期**：(1) 面板 `submitManual()` 打的正是上面第一列那道 `POST /api/v1/simulations/{id}/manual-decision`，該路由寫 `decision_records`＋`human_decision_events`＋`simulation_decisions.decision_detail`——**面板是決策持久化入口**，與模擬頁同一道門、不同畫面；當年「不是」指的是它不寫 paper-portfolio 那張表，講錯了自己的門。(2) 本輪接通明選血緣後現測 `grep -c linked_inference_id src/components/workspace/OrderPreviewPanel.tsx` ＝ **5**（路徑也補上 `workspace/`）。證據與邊界見 §八。 |

### 二、三個失敗的直接重驗（逐一定根因，修復沒有放寬任何閘門）

| 失敗（第九輪 §七 那三支 ✗） | 本輪量到的根因 | 最小修復 | 沒有放寬什麼 |
|---|---|---|---|
| `tests/test_process_state_isolation.py::test_the_environment_the_suite_measures_is_the_environment_it_documents` | AST 閘點名 `tests/test_learning_scheduler.py:515`／`:516` 兩處 `Settings(...)` 沒帶 `_env_file=None` ⇒ 本機環境檔能把別人的 run 變成紅的（**環境污染**，非產品缺陷） | 那兩處補 `_env_file=None`；該檔本輪總量 3 hunks／＋865 −118（含新增節點） | `test_process_state_isolation.py` 的檢查規則一字未動（它是閘，不是被測物） |
| `tests/test_replay_credibility.py::TestReplayOwnership::test_user_id_bound_into_trades_query` | **測試綁定錯誤**：fixture 造了一個 id、引擎被叫的是另一個，斷言因此把 `UUID` 講成字串不符。產品端 typed-UUID 綁定（`app/lib/uuid_bind.py`）本来就是對的 | 重写 helper 為 `Wiring(engine, db, run, user_id)`＋`_wired(...)`，讓 fixture 與引擎共用同一個 id；斷言同時要 `str(bound)==test_user_id` **和** `isinstance(bound, UUID)`（1 hunk／＋464 −297） | 沒有把斷言改成「只比字串」——產品邊界（必須以 typed UUID 綁入）反而被钉得更緊 |
| `tests/test_research_run_admission.py::test_the_linkage_revision_is_unique_and_the_chain_has_one_head` | 節點常數停在 `b2c3d4e5f6g7`，而離線尾部鏈已含新修訂 ⇒ **期望過期**（`d4e5f6g7h8i9` 未進節點） | 改用具名修訂常數 `SKILL_UNIT_REVISION`／`INFERENCE_LINK_REVISION`／`HUMAN_LINEAGE_REVISION`，節點改名 `…_and_the_chain_has_one_head`（8 hunks／＋554 −18） | 尾部鏈是**加入**一支修訂（3→4）而不是抽掉；`scripts/check_migration_tail.py` 本輪現測：head `d4e5f6g7h8i9`、`parsed_heads` 只有一個、`revision_count=57`、`unreachable_from_head=[]`、`status OK` |
| （**本輪自己引入的第 4 支 ✗，誠實記錄**）`test_one_tick_estimates_through_the_gate_and_the_owner_can_read_it`（`estimate-lane2`，exit **1**） | 給回傳加 `failed` 之後，該節點的**整 dict 相等**斷言不再成立（實測 diff 就是多一個鍵 `'failed': 0`） | 改**釘住的期望**（在期望 dict 裡寫上 `"failed": 0`）；斷言形式保持完整相等，未改成子集／存在性比對 | `estimate-lane3` 在同一棵樹 collected **46**／45 passed／1 具名跳過／exit **0** |

**披露**：本輪「修復前」那三次失敗的逐支重現 log **沒有保留成實物**（跑在 `/tmp` 下的預設 run dir，`/tmp/alethra-runs` 現測為空）。可核對的重現依據＝第九輪 §七 那三行 ✗ 的具名節點＋檔行號＋期望/實測，與同一 run 的 `exit-code: 1`（其 provenance 现存 `alethra-evidence/repin-round10/pinned-skip-budget-3811.verbatim.json`）。保留下來的本輪實物是**修復後**的三次確認（`rerun3-round10/run1`、`lane-confirm-round10/three-failures`、`three-failures-final`）。

### 三、固定候選的整套結果（`a2641cca…`，一次，跑完）

| 項目 | 實測值 |
|---|---|
| 命令 | `cd backend && DATA_PROVIDER=demo ALETHRA_RUN_DIR=…/full-suite-round10/final-candidate ./scripts/run_tests.sh sqlite`（17:31 起、18:12 落，`2393.55s (0:39:53)`） |
| collected | **3,820**（`identity.txt: collected-node-ids: 3820`；`selection on this tree: 3820 tests collected in 3.79s`） |
| **pytest 自己的狀態** | `exit-code.txt` ＝ **0**；末行 `3644 passed, 179 skipped, 11144 warnings in 2393.55s` |
| junit 計數 | `tests=3823 skipped=179 failures=0 errors=0`；`passed=3644` |
| 帳本自洽 | `reported units 3823 = collected 3820 ＋ collection-level 3 → OK`；unresolved **0**、duplicate **0**、collected-but-no-record **0**、record-without-id **0**；console census 與記錄結果一致 |
| 跳過普查 | **179** 單位＝`pg_only 159 ＋ live_stack 18 ＋ other 2`（分類加總＝179；item 176 ＋ collection 3）；3 支 collection-level 模組：`tests.test_crypto_bar_harvest_pg`、`tests.test_crypto_paper_pg`、`tests.test_strategy_study_timestamps_pg` |
| 樹未中途變動 | `tree-digest-before` ＝ `tree-digest-after` ＝ `a2641cca…` ⇒ `tree changed during run: no` |
| **證據閘門拒絕（與上面那個 0 分開算）** | 實物：`final-candidate.wrapper.log` 末行逐字＝`not green: pytest passed but the evidence gates refused (skip census 2, ledger 1).`；其上就是 runner 的帳本區塊：`exit code 0`／`collected items 3820 (collect.txt)`／`junit tests=3823 skipped=179 failures=0 errors=0`／`passed=3644`／`pg_only=159 live_stack=18 other=2 (total 179 = item 176 + collection 3)`／`reported units 3823 = collected 3820 + collection-level 3 -> OK`／unresolved 0、duplicate 0、collected-but-no-record 0、record-without-id 0。18:2x 對同一 run dir 重跑兩支閘門（唯讀）實測 rc=2（`skip_budget.py`「collected 3820 but the budget pins 3811: the selection changed」）與 rc=1（`run_evidence.py check`「9 node(s) are collected but not pinned」），與 run 當時逐字相同。**lane exit 的來源要講精確**：`backend/scripts/run_tests.sh:484-488`（`GATE=$BRC`、`[ "$ERC" -ne 0 ] && GATE=$ERC`、pytest rc=0 時 `RC=$GATE`；pytest 自己的狀態早在 `:436` 寫進 `exit-code.txt`）⇒ 該 attempt 的 shell 狀態就是 2，但沒有檔案記錄它：父層 `lane-exit.txt`（mtime 17:10）是 17:10 那兩次中斷由我的 wrapper 命令 `echo "lane exit=$?"` 寫的，本 attempt 未寫（現測 `grep -c "lane exit" final-candidate.wrapper.log` ＝ 0）。所以「整套 lane exit 2」＝規則＋兩支實測 rc 推得，「pytest exit 0」才是讀到的 |
| 拒絕的性質 | 跳過上限 `total=179 pg_only=159 live_stack=18 other=2` 與釘住的**完全相同** ⇒ 漂移只在「多收 9 支節點」：**沒有新增跳過、沒有節點消失、沒有節點由綠轉紅**。三支 ✗ 在本棵樹 0 支（§四 第 2 列逐支確認） |
| 中途放棄的 run（18:2x 逐檔重查，更正本節第一次寫法） | 三支都**無 `exit-code.txt`**（＝跑完前就被本輪主動中斷）：`run1/junit.xml`＝`tests="1787" errors="1" failures="0" skipped="51"`、`run2/junit.xml`＝`tests="1405" errors="1"`（那個 error 是中斷本身），`run3` **連 `junit.xml` 都不存在**（目錄現值只有 `collect.txt identity.txt pytest.log`；其 wrapper log 停在 `tests/test_auto_collection.py` 中途）。父層 `lane-exit.txt` 現值 `lane exit=143`、mtime **17:10** ⇒ 它只對應 17:10 那兩次中斷，**不是** run3、更不是 final-candidate（那是我的 wrapper 命令自己 `echo "lane exit=$?"` 的產物，不是 runner 寫）。這些 run **不作為任何結果**。另外 `final-candidate` 的前一次因我 `mkdir -p final` 預先造了目錄，runner 依約拒絕（`final.wrapper.log` 現讀：「refusing: the run directory already exists: …/full-suite-round10/final」），該空目錄 `final/`（現值 0 檔）仍在原處未刪 |
| 第九輪那份 exit 1 | 原始 run dir 已不在 `/tmp`（現測空）；其 fingerprint（exit_code `1`、collected 3811、樹 `501306060850…`、五份產物 sha256、`generated_by … pin`）存在 `alethra-evidence/repin-round10/pinned-skip-budget-3811.verbatim.json`。本輪**沒有**用新的 exit 0 去覆寫那份記錄，也沒有把它改講成「其實沒失敗」 |

**未批准重釘的隔離（本輪只量、不改釘）**：`tests/collected_nodes.txt`（3,811 列）與 `tests/skip_budget.json` 現值 sha256 ＝ `5260ece237d728a21637f79dcdcbad6b1ffd540b814ffa2361fbf0026cff3cbf`／`c653b1a77dfd45acf60fb7e515c3514de9f1d58c542e0d52eb37d0e4b59d95f8`，第九輪 `pin --ack-diff` 之前／之後的逐字基準在 `alethra-evidence/repin-round10/`。本輪對 `final-candidate` 跑的是**不帶 `--ack-diff` 的第一輪**（`rc=2`，寫入前先返回）：印出 `selection diff vs pinned: -0 +9` 與 `acknowledgement digest: ff569c22ae5771944ea5fccddac50ab55425e6d3119c8017244c37f46a948d5`，跑完兩檔 sha256 與行數逐字未變。那 9 支＝4 支 `test_daily_model_estimate.py` ＋ 5 支 `test_manual_decision_books_linked_portfolio.py::TestLineageTravelsFromSubmitToExport`（全名見 `alethra-evidence/repin-round10/drift-added-final.txt`）。正式重釘正文在 §六 D 之後的「重釘卡」。

### 四、本輪驗證帳本（跑過什麼、實際數字；`sqlite` lane 皆 `DATA_PROVIDER=demo`）

| 物件 | 命令（`cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh sqlite …`） | 結果 | run dir／tree（時間為檔系統 mtime，非牆鐘宣稱） |
|---|---|---|---|
| 三失敗（修復後第一次確認） | ＋那三支具名 node id | collected **3**、exit **0** | `alethra-evidence/rerun3-round10/run1`／`85bf9b2753e15e805c35e67a78668d3e16bdc562add2721969ff19015836d15c`（17:00） |
| 三失敗（同一批，較早一棵樹） | 同上 | collected **3**、exit **0** | `…/lane-confirm-round10/three-failures`／`401d32e0d0ec13f19a6739d96c5d85d7d544b3ea7a4931b0daa564efa15bdfbf`（17:16:13） |
| **三失敗（固定候選上重驗）** | 同上 | collected **3**、junit `tests=3 errors=0 failures=0 skipped=0`、exit **0**、`tree-digest-before==after` | `…/lane-confirm-round10/three-failures-final`／`a2641cca…`（17:32:39） |
| 估計 lane（第一棵樹） | ＋`tests/test_daily_model_estimate.py tests/test_daily_estimate_two_workers.py` | collected **45**、exit **0** | `…/estimate-lane`／`401d32e0…`（17:15:55） |
| 估計 lane（加 `failed` 之後） | 同上 | collected **46**、**1 failed**／44 passed／1 skipped、exit **1**（根因與修復見 §二 第 4 列） | `…/estimate-lane2`／`7f3561271c0109a663efb3066c5553c5d0035048359bf3f81508f20473520eb1`（17:27:52） |
| 估計 lane（固定候選） | 同上 | collected **46**、45 passed／1 skipped（`test_two_processes_on_one_window_bill_each_business_key_once`，具名理由 `tests/test_daily_estimate_two_workers.py:376`「the seat being raced is pg_advisory_xact_lock, which SQLite has no way to take」）、exit **0** | `…/estimate-lane3`／`a2641cca…`（17:29:01） |
| 血緣 lane（固定候選） | ＋`tests/test_manual_decision_books_linked_portfolio.py` | collected **50**、47 passed／**3 skipped**／0 failed、exit **0**；跳過具名：`:2246`／`:2292`／`:2357`「row-lock serialisation requires a real PostgreSQL backend」（`TestConcurrentLifecycle` 三支） | `…/lane-confirm-round10/lineage-lane-final`／`a2641cca…`（17:33:29） |
| **完整 sqlite 套件（固定候選，本輪唯一一次跑完）** | `… run_tests.sh sqlite`（無路徑＝whole-suite） | 見 §三 整表：3,820 collected／3,644 passed／179 skipped／**0 failed**、pytest exit **0**、lane exit **2**（閘門）、`0:39:53` | `…/full-suite-round10/final-candidate`／`a2641cca…` |
| 證據閘門重測（唯讀，對已完成的 run dir） | `.venv/bin/python scripts/skip_budget.py <run>/pytest.log check --identity <run>/identity.txt --budget tests/skip_budget.json`；`.venv/bin/python scripts/run_evidence.py <run> check --budget … --manifest …` | rc=**2**／rc=**1**，訊息與 run 當時逐字相同 ⇒ 不是產物缺損造成的第二次拒絕 | 同一 run dir（18:2x） |
| 重釘第一輪（不寫檔） | `… run_evidence.py <run> pin --manifest-out tests/collected_nodes.txt --budget-out tests/skip_budget.json`（**故意不帶** `--ack-diff`） | rc=**2**、`-0 +9`、digest `ff569c22…`；兩檔 sha256 前後相同 | 同一 run dir |
| 前端 `node:test` lane | `cd frontend && ~/.local/bin/node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyEstimate.test.mjs`／`--test tests/*.test.mjs`；`npm run typecheck` | **35／35**；**187／187**；`tsc --noEmit` rc **0**（Node **v22.23.5**）。默認 `node` ＝ v18.20.8 時同一 lane **13/13 fail**：無 type stripping，屬工具鏈需要而非產品缺陷——本輪實測到這條差別 | `…/alethra-evidence/frontend-round10`（前端檔不在後端 digest 涵蓋內，以 sha256 自 `d-patch-round10-final` _manifest 對照） |
| e2e 清單（唯讀：未啟動任何服務、未開瀏覽器） | `cd frontend && ~/.local/bin/node20 ./node_modules/.bin/playwright test --list` | **Total: 312 tests in 32 files**（預設 `node` v18.20.8 直接被 Playwright 拒：「requires Node.js 20 or higher」）；`grep -r "daily-estimate\|pp-download-decision-history" e2e/` ＝ **0 支** ⇒ 本輪那兩條旅程在既有 e2e lane 裡**無覆蓋**；bundled chromium 未安裝（`~/Library/Caches/ms-playwright` 空）、`/Applications/Google Chrome.app` 存在、`playwright.config.ts:12` 記 `E2E_BROWSER_CHANNEL=chrome` 可免下載 | 18:12 現測 |
| migration 尾部鏈（離線讀檔案系統，不連 DB） | `cd backend && .venv/bin/python scripts/check_migration_tail.py --json` | `status OK`、head **`d4e5f6g7h8i9`**、`parsed_heads=[d4e5f6g7h8i9]`（唯一）、`expected_tail` 四支 `[f2a3b4c5d6e7, b2c3d4e5f6g7, c3d4e5f6g7h8, d4e5f6g7h8i9]`、`revision_count 57`、`unreachable_from_head []` | 18:0x 現測 |
| 機器環境（A–D 正文的進場前提） | `date`＋`pgrep -fl "next-server\|uvicorn\|postgres"`＋逐 port `lsof -nP -iTCP:<port> -sTCP:LISTEN`＋`ls -l /tmp/.s.PGSQL.*`＋`ls -d .local/pgdata-r10 .local/sock-r10` | 17:54:16 HKT：**無任何** postgres／uvicorn／next-server 行程；3000／3031／5432／54340／8000／8031／8120 全部 **0 列**聆聽；`/tmp/.s.PGSQL.*` **0 個**；`.local/pgdata-r10`、`.local/sock-r10` **都不存在**；`git rev-parse HEAD` ＝ `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（未變）；髒路徑 210（139 ` M` ＋ 71 `??`） | 一次性快照，進場仍逐條重查 |

### 五、披露／NOT RUN／尚欠的工程

**NOT RUN（逐條，不得以別的層推定）**：
- **PostgreSQL 全部步驟（A0–A7）＝NOT RUN**（未批准、未執行）。`pg_advisory_xact_lock` 是否擋得住第二支行程、`SELECT … FOR UPDATE` 的行鎖序列化是否真的只放過一次 ⇒ **UNKNOWN**。④-sqlite 的 barrier 節點 PASS 不是 PG 競態 PASS；本輪整套的 179 支跳過裡 `pg_only=159` 就是這层的缺口大小。
- **05:00 自然觸發（B 包）＝NOT RUN**。排程是 APScheduler `CronTrigger(hour=DAILY_ESTIMATE_HOUR_UTC=5, minute=0, timezone="UTC")`（`app/services/daily_estimates.py:82`、`app/workers/learning_scheduler.py:82/:182-185`、`SERVICE_TIMEZONE="UTC"` `:80`）⇒ **今天 05:00 UTC 已過**（本輪量測時刻 09:54 UTC），自然觸發的最早時窗是 **2026-11-20 05:00 UTC ＝ 13:00 HKT**；不是抹時間、也不是手動 tick 可以代替的。
- **瀏覽器兩條旅程（C 包）＝NOT RUN**：無服務行程（8000／8031／3000／3031 全未聆聽）、`.next` 是舊產物（`BUILD_ID iEzzhWbfCzP7pv7Rb0LcS`，mtime 09:05，晚於它的改動有 12:30 模擬頁、12:32 `paper-portfolio/[id]/page.tsx` 與 `decisionLineage.ts`、17:24 `DailyEstimatePanel.tsx`），且既有 312 支 e2e **一支也沒有**走到 daily-estimate 面板或決策匯出。`node:test` 187／187 只證「測試讀的源碼」與「頁面用的源碼」同一份，**不證渲染、不證點擊**。
- **行程級崩潰（kill -9／斷電／連線洩漏）＝NOT RUN**：in-process 中斷不等於真實程序被殺；六情境矩陣仍是 ⑤-HTTP in-process 層。
- **D 包（逐 hunk patch ＋ 乾淨克隆重現）＝本輪只交實物與清單，未套用**：未執行 `git apply`、未 stage／commit／push、未建乾淨 clone 去重現。
- 未修改的部分不自稱修好：本輪動的是 §一/§二 那幾檔；`window_state` 的提交邊界、額度規則、durable lease／心跳都是既有行為，本輪證據**未**擴展它們。

**本輪新發現的缺陷（已定位，未修——候選已凍結，不在跑完整套期間動刀）**：
- **`errors` 類失敗在重新整理後無法具名**。`run_daily_estimates` 對「引擎在閘門之下拋出、且這一輪沒有任何 results」的那支，只把字串塞進 `errors`（`app/services/daily_estimates.py:1054-1063`）並存 `result_json["errors"]`／`error_details`（`:1086-1100`、`:1073`）；而 `daily_estimate_status` 判定「哪一次 run 講的是這個技能帳號」時只查 `results`／`recovered`／`refused`（`:1327-1370`），**不查 `errors`** ⇒ 重新整理畫面後，該帳號的 `last_run` 不會被那次失敗點名，只能落到「執行狀態未確認」。201 當下講得清楚（§一 第 1 列），重載後講不清。最小修法（**未實作、未批准**）：status 掃描把 `errors` 條目也當成「點名該 skill_unit_id 的一次嘗試」，並在面板顯示「本次失敗（未落帳）」；影響節點：`test_a_press_whose_engine_died_names_the_failure_in_the_response` 與 status 那批钉位。
- **全拒用的 tick 存的 `status` 是 `partial`**（status 階梯只問「有沒有壞事／有沒有東西落地」，`app/services/daily_estimates.py:1065-1073`）：`test_a_tick_where_every_engine_died_settles_nothing` 逐字钉住 `job["status"] == "partial"` 而 `estimated==0`。畫面文案靠 `estimated` 組合，不靠這個詞；但**任何以 `learning_jobs.status` 直接標「全部失敗」的打算都會講錯**。
- **`'partial'` 的 press 結果從單一技能 press 不可達**：只估一支時，結果只能是 estimated／refused／failed 三者之一，不會出現「部分成功」。這是覆盖面不是缺陷，報告不得假稱四態在每個 press 都可觀察。

**既有披露仍成立（本輪未改變）**：
- `POST …/daily-estimate/run` 的 201 ＝「建立這次嘗試」，不是「新增了一筆估計」；拒絕細節在 `status.last_refusal`，內層才是 422。
- 撤回時機一句話必須在畫面讀得到（已在執行中的那筆不退還）；撤回的持久證據只有 `daily_estimate_consent_revoked` 事件列，status 讀取不查它。
- 額度上限（`PilotEntitlement.ai_usage_limit`）能證明成本可控，**不能**代替互斥證據：429 會把「錯誤的第二次扣額」掩蓋掉，判準必須以帳本 row count 為準（→ A6 的硬性前提）。

**交回 owner 裁決（本輪不做、不隱瞞）**：
1. **201 語意是否改契約**（改 4xx／200 會影響已钉節點 `test_the_http_door_and_the_service_report_the_same_refusal`）。
2. **`errors` 重載可見性**（上面那條新缺陷）要不要列入下一輪工程。
3. **durable lease／心跳**：要讓平台能說「還在跑」或「已死」需要它自己那一包（新政策，不是順手改）。
4. **正式重釘**：本輪量到 `-0 +9`、digest `ff569c22…`，批准正文在 §六「重釘卡」；未批准前 `tests/collected_nodes.txt` 維持 3,811 列，整套 lane exit 就會繼續是 **2**（這是**發明的界**在說話，不是套件變紅）。

### 六、可獨立審批正文（第十輪版；取代第九輪 §六，把過期值換成本輪實測值）

> 〔已被第十一輪 §六 取代的部分：**只有 A 包的 A6 列與其「硬性前提」段**——owner 指出「只到 claim 入口的時間戳不是鎖等待證據、額度不能掩蓋第二次扣額」，該步的判準、node 清單與輸入在本輪改版。此處保留第十輪原文作為歷史，不覆寫；要批准 A6 請讀第十一輪 §六 那一版（同節末的「重釘卡」數字也以第十一輪 §五 實測為準）。B／C／D 三包正文未變，但其**前提時刻**與**實物內容**已重測：見第十一輪 §八（D 包本版內容）與 §九（B／C 前提重測）。〕

**四條共同前提**（任一包命中即停；是規則不是口頭保證）：

1. **不放寬安全閘**：`tests/test_safety_gate.py`（本輪現讀：`ALLOWED_TEST_DATABASES` 含 `alethra_owner_test` ＝ `:26-30`；`FORBIDDEN_DATABASES` `:33-38`；`ALLOWED_INSTANCES` **只有** `('localhost'|'127.0.0.1'|'::1', 5432)` ＝ `:40-45`；`verify_test_database()` `:156`）與 `tests/conftest.py:79-100` 一字不動。要改用別的名字／port 屬 owner 政策裁決，代理不自行修改。
2. **不碰他人的服務**：進場前逐條**現測** `pgrep -fl postgres`＋`lsof -nP -iTCP:<port> -sTCP:LISTEN`＋`ls -l /tmp/.s.PGSQL.<port>`；被佔用 ⇒ 該包結語 **NOT RUN**（不是失敗的測試）。「現測未聆聽」只是空位，**不是**可用證據，也不代表之後不會被佔。本機另有 `.local/pgdata-16`（現測叢集狀態 shut down，最後 checkpoint 2026-11-08 19:06:58，`postmaster.opts` 指向 `-D .local/pgdata-16`）⇒ **不接、不停、不讀、不覆寫、不共用**。17:54:16 HKT 快照：postgres／uvicorn／next-server 行程 **0**；5432／54340／8000／8031／3000／3031／8120 聆聽 **0**；`/tmp/.s.PGSQL.*` **0 個**（第九輪那三支平行線行程現測都不在，但規則不變）。
3. **秘密走既有通道**：一次性本機叢集的密碼由代理在 run 內以 `mktemp` 產生、權限 0600、內容不出現在訊息／repo／run dir 以外；`ALETHRA_PG_TEST_URL` 只在子 shell 內 export（pg 層 `backend/scripts/run_tests.sh:141-152` 不印 DSN；輸出經 `tests/conftest.py:68-76 _safe_dsn` 遮蔽）。任何一包都**不請求**受保護 DB 的憑證，也不要求 owner 依草稿預先建密碼檔。
4. **`backend/.env` 是活的污染源——本輪新測到的硬前提（第九輪正文未列）**：該檔（758 bytes，gitignored，`.gitignore:14`）現含 **`RESEARCH_AUTH_BYPASS_ENABLED=true`**、**`DATA_PROVIDER=twelvedata`**、**`LIVE_TRADING_ENABLED=true`**、**`CANDIDATE_PASSWORDLESS_MODE=true`**、`JWT_SECRET_KEY`、`TWELVEDATA_API_KEY`、`DATABASE_URL`（值本輪未抄錄，只按名字記錄）。後果逐條：`app/config.py:560` 是 `ConfigDict(env_file=".env")` ⇒ 在 `backend/` 直接起 uvicorn 會吃到它；`app/auth/jwt.py:51-61` 在 bypass 開啟時把**每個 loopback 請求當成「第一位 active user」**（`auth_amr=["bypass"]`），而 `app/config.py:75` 的 `app_env` 預設 `development`、 validator `:499-512` 只擋 production-like 與遠端 DB ⇒ **不會拒設定**；`DATA_PROVIDER=twelvedata` ⇒ 05:00 那一響会用 `.env` 裡的真金鑰**外呼 provider**。因此 B／C 兩包在啟動命令裡必須**逐條以程序環境覆蓋**（環境變數優先於 dotenv）：
   ```
   export DATA_PROVIDER=demo LIVE_TRADING_ENABLED=false AUTO_COLLECTION_ENABLED=false \
          RESEARCH_AUTH_BYPASS_ENABLED=false CANDIDATE_PASSWORDLESS_MODE=false \
          APP_ENV=development ENABLE_LEARNING_SCHEDULER=false
   ```
   並且**讀回證明**（不是宣稱）：`GET /api/v1/admin/collection/status` 的 `enabled`／`running` 皆假；以本包自己的隔離 DB 直接查 `learning_jobs`／`usage_records`（若服務連錯 DB，這邊會看到 0 列 ⇒ 判 NOT RUN 而不是 PASS）；PG 目標另以 `SELECT count(*) FROM pg_stat_activity WHERE datname='alethra_owner_test'`（只查**自己**那支叢集）證明行程確實落在隔離 DB；外呼以 `lsof -nP -p <pid> -a -i` 在觸發前後各一次，非 `127.0.0.1` 對端列數必須 **0**。

#### A 包：PostgreSQL——八步，每一步可單獨批准、單獨中止（A4 的目標修訂與 A7 是本輪更新）

| 步 | 動作（可執行值） | 寫入範圍 | 判準（通過才准進下一步） | 中止條件 |
|---|---|---|---|---|
| A0 | 唯讀預檢（全部結果寫 `$RUN/identity.txt`）：`pgrep -fl postgres`；`lsof -nP -iTCP:5432 -sTCP:LISTEN`＝0 列；`ls -l /tmp/.s.PGSQL.5432` 不存在；`ls -d $PGDATA $SOCKDIR` 都不存在；`"$PGB/initdb" --version`／`"$PGB/psql" --version`＝**16.2**（17:5x 本機現測兩者皆 `16.2`）；`git rev-parse HEAD`＝`5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`；本節 §四 各 run dir 的 `identity.txt` 指認 tree `a2641cca…` | 只寫 `$RUN/identity.txt` | 全中 | 5432 已被聆聽／socket 檔存在／`$PGDATA` 或 `$SOCKDIR` 已存在（**先回報，不覆寫、不刪除**）／版本不符／HEAD 或 tree 不符 ⇒ **NOT RUN** |
| A1 | `"$PGB/initdb" -D $PGDATA -U alethra_r10_bootstrap -A scram-sha-256 --pwfile="$RUN/pw.bootstrap"`（`$RUN/pw.bootstrap` 由 `mktemp`＋`chmod 600` 產生）；`mkdir -p $SOCKDIR && chmod 700 $SOCKDIR`。**只建叢集**：不建 DB、不建表 | 新目錄（獨佔建立） | rc=0 | 非零 ⇒ 停，保留 `$RUN/pg.log`，**不重試第二次、不刪 PGDATA** |
| A2 | `"$PGB/pg_ctl" -D $PGDATA -l "$RUN/pg.log" -w start -o "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB"` ＋ `"$PGB/pg_isready" -h 127.0.0.1 -p 5432` | 起一支新行程（自己的 PGDATA） | `pg_isready` 回 ready | 非零或不 ready ⇒ 停；正常停止 `pg_ctl -D $PGDATA -m fast stop`，以 A0／A2 抓到的 pid 用 `pgrep`＋`lsof` 核對已退出（**不**以已停端點的 HTTP 回應證明停止）。停止不等於刪資料 |
| A3 | 以 `alethra_r10_bootstrap` 連線建：`CREATE ROLE alethra_r10_migrator LOGIN PASSWORD …`、`CREATE ROLE alethra_r10_app LOGIN PASSWORD …`、`CREATE DATABASE alethra_owner_test OWNER alethra_r10_migrator`（名字同時滿足 `test_safety_gate.py:26-30` 的白名單與 `run_tests.sh:150` 的 `ALETHRA_TEST_REQUIRED_DB_PREFIX=alethra_`）、`GRANT CONNECT, CREATE ON DATABASE … TO alethra_r10_migrator`、`GRANT CONNECT … TO alethra_r10_app`；`\c alethra_owner_test` 後 `GRANT ALL ON SCHEMA public TO alethra_r10_migrator`、`ALTER SCHEMA public OWNER TO alethra_r10_migrator`、`REVOKE ALL ON SCHEMA public FROM PUBLIC`（PG15+ 之後 `public` 不給 PUBLIC 建表權，16.2 上這是 alembic 能落地的**必要**授權，不是預防性多給） | 新 DB＋兩個角色 | 讀回存 `$RUN/roles.txt`：`pg_roles` 對 migrator 與 app 的 `rolsuper/rolcreatedb/rolcreaterole/rolbypassrls` **四旗標全 `f`**；`pg_database.datdba=alethra_r10_migrator`；分別連線時 `usesuper=f` | 任一不符 ⇒ 停，不進 A4。bootstrap 到此不再被任何應用或 alembic 連線使用 |
| A4 | 空庫 → 固定修訂的**全鏈**升級：`cd backend && . "$DSN" && export TEST_MODE=true` → `alembic current`（新庫預期「無版本」；實際起始值＝UNKNOWN，本機从未在任何 PG 讀過）→ `alembic heads` → `python scripts/check_migration_tail.py --json`（讀**檔案系統**的 ScriptDirectory，不連 DB）→ `alembic upgrade d4e5f6g7h8i9` → 再讀 `alembic current` 存 `$RUN/alembic.txt`。**本輪更正**：目標 head 由第九輪的 `c3d4e5f6g7h8` 改為 **`d4e5f6g7h8i9`**（人類決策血緣欄），`expected_tail` 現測四支 `[f2a3b4c5d6e7, b2c3d4e5f6g7, c3d4e5f6g7h8, d4e5f6g7h8i9]`、`revision_count=57`、`unreachable_from_head=[]` | 該隔離 DB 的 DDL | 鏈尾＝上述四支依序；`heads` 只有一個且＝`d4e5f6g7h8i9` | `current` 回報任何既有版本／`heads` 多於一個／head 不符／`DUPLICATE_REVISION`｜`BRANCHED_TREE`｜`MISSING_DEPENDENCY` 任一 ⇒ 停。**不 `--autogen`、不 downgrade、不 drop、不執行任何其他 migration**；失敗保留現場，清場需另行批准 |
| A5 | 以 migrator 連線**唯讀**核對實際 schema：`decision_records.linked_inference_id` 與 `human_decision_events.linked_inference_id` 的 `data_type`（期望 `uuid`）、`pg_constraint` 對該兩表的 FK、`pg_indexes`（`decision_records`／`human_decision_events`／`usage_records`）、`information_schema.columns`（`usage_records`／`learning_jobs`／`quota_reservations` 逐欄）。**本輪預期的讀回值（源碼現讀，寫下来讓差異無處可藏）**：兩個欄位都**只有 index、沒有 FK**（`migrations/versions/c3d4e5f6g7h8_….py:88-93`、`d4e5f6g7h8i9_….py:85-89`；索引名 `ix_decision_records_linked_inference_id`、`ix_human_decision_events_linked_inference_id`），完整性在 `app/services/decision_lineage.py::validate_linked_inference` 於寫入前核對 | 否 | 與 §一 的寫入點清單逐欄對照；差異逐條列出（**不解釋成等價**） | 出現 FK／欄位型別不符 ⇒ 停並回報（那不是「更安全」，是本體與 migration 不同步） |
| A6 | 兩行程競態（本包唯一能回答「有沒有可靠互斥」的步驟）：`cd backend && . "$DSN" && export TEST_MODE=true DATA_PROVIDER=demo && ./scripts/run_tests.sh pg tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once tests/test_daily_model_estimate.py::test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice`（三支 node id 現測都在 `final-candidate/collect.txt` 內） | 該隔離 DB | 見下段 | 60s 等待逾時／`Barrier` 未湊齊／時間戳斷言失敗 ⇒ 該次是 **NOT RUN／INVALID**，不得寫成 PG 競態 PASS；現場保留給 owner，不刪 `market_data_bars`（無 owner 欄可 scope，屬共享種子） |
| A7 | **（本輪新增的一步）** 行鎖序列化三支：`./scripts/run_tests.sh pg tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_two_requests_on_one_idempotency_key_leave_exactly_one_order tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_stale_account_read_cannot_overwrite_a_committed_booking tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_manual_submit_racing_an_advance_stays_consistent` | 該隔離 DB | 三支都 `passed`（不是 `skipped`） | 仍以 `row-lock serialisation requires a real PostgreSQL backend` 跳過 ⇒ 說明 DSN 沒生效，判 **NOT RUN** 並回報 |

A6 的硬性前提（寫進 run dir）：測試帳號的有效額度 **≥ 4**（扇出＝2 支 instrument × 2 行程；無 `PilotEntitlement` 列時走 free plan `ai_usage_limit=10`，`app/auth/entitlements.py:66-71`；有列時讀該欄、預設 20，`app/models/pilot.py:97`）。**B 包的 `ai_usage_limit=1` 不得套在 A6／A7**，否則額度會把「錯誤的第二次扣額」掩蓋成 429——判準以**帳本 row count** 為準：每 (owner, skill, instrument, window) 恰好 1 列 `metadata["via"]=="daily_service"`、每列恰好 1 筆 `settled` 預留、非終態預留＝0、落敗那支的 `learning_jobs.result_json.refused` 以 instrument id 具名 `already_estimated_this_window`。**兩個 job 摘要的 `billed_in_window` 不相加**（同一帳本的兩個瞬間），窗口總數只引用 row count。同步機制現讀：`mp.get_context("spawn")`、`ctx.Barrier(2)`（`tests/test_daily_estimate_two_workers.py:218`／`:418`）、`_TIMEOUT_S = 60`（`:127`）、跳過具名 `:376`、斷言 `max(claim_enter) < min(estimate_enter)`、連線層 `SET lock_timeout='30s'`／`statement_timeout='120s'`／`idle_in_transaction_session_timeout='90s'`（`:189-191`）。

本包唯一輸入（owner 只需給這一個）：`ALETHRA_PG_TEST_URL`——由代理在 A2 之後、於子 shell 內組出並 export，形如 `postgresql://alethra_r10_migrator:<mktemp 密碼>@127.0.0.1:5432/alethra_owner_test`；缺它 pg 層直接 rc=**2** 拒絕（`run_tests.sh:141-145`，本輪讀回）。

#### B 包：05:00 自然觸發觀察（一支行程、一個隔離 DB、只觀察一次）

| 項目 | 可執行值（本輪更正處標〔R10〕） |
|---|---|
| 啟動命令 | `cd backend && export <共同前提 4 那一整組> DATABASE_URL=<隔離 DSN> ENABLE_LEARNING_SCHEDULER=true LEARNING_SCHEDULER_JOBS=daily_model_estimate LEARNING_SCHEDULER_MAX_OWNERS=1 && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8031 --log-level info >> "$RUN/service.log" 2>&1`；〔R10〕啟動前 `pgrep -fl uvicorn` 必須為 0（17:54:16 現測無 uvicorn）、`lsof -nP -iTCP:8031 -sTCP:LISTEN` 0 列，進場仍重查；啟動後把**本輪抓到的 pid** 寫 `$RUN/service-pid.txt` |
| 停止 | `kill -TERM "$(cat $RUN/service-pid.txt)"` 後以 `pgrep -f "uvicorn app.main:app --host 127.0.0.1 --port 8031"`＋`lsof -nP -iTCP:8031 -sTCP:LISTEN` 核對；**只停自己這支 pid**；不用已停端點的 HTTP 回應證明停止；停止不等於刪資料 |
| 日誌落點 | 以 `lsof -p <本輪 pid>` 實際確認 stdout／stderr 落點（不是宣稱） |
| DB | A3 之後的 `alethra_owner_test` ＋ **`alethra_r10_app` 角色**的 DSN（應用永不用 superuser，也不用 migrator）；或 `DATABASE_URL=sqlite:///$TMPDIR/alethra-r10-cron.db`。**一個**固定隔離 DB，候選與該 DB 綁定；〔R10〕必须以程序環境覆蓋 `backend/.env` 的 `DATABASE_URL`，並以共同前提 4 的讀回證明實際落在哪顆 DB |
| 只掛一支 lane | `ENABLE_LEARNING_SCHEDULER=true`（欄位 `app/config.py:140` 預設 **false**）＋ `LEARNING_SCHEDULER_JOBS=daily_model_estimate`（`:148` 預設空字串；未設時走 `DEFAULT_LEARNING_SCHEDULER_LANES` `:55-59/:211`，那組**不含**估计 lane，但相鄰 lane 的 02:00／03:00 cron 在 set 了 allow-list 時會一起註冊 ⇒ 明確命名）；命名不認識的 lane 由 validator 拒設定（`:320-344`） |
| 觸發時刻 | `CronTrigger(hour=DAILY_ESTIMATE_HOUR_UTC, minute=0, timezone=SERVICE_TIMEZONE)`（`app/workers/learning_scheduler.py:182-185`；常數 `app/services/daily_estimates.py:82` ＝ **5**；`SERVICE_TIMEZONE="UTC"` `:80`）。〔R10〕**今天（2026-11-19）05:00 UTC 已過**（本輪量測於 09:5x UTC）⇒ 自然觸發最早是 **2026-11-20 05:00 UTC ＝ 13:00 HKT**；不改系統時間、不手動 tick（那兩件各需另行批准） |
| 讀回證明 | `registered_lane_ids()`（`app/workers/learning_scheduler.py:191`）只回 `daily_model_estimate`；`GET /api/v1/admin/collection/status`（`app/api/admin.py:89`）的 `enabled`／`running` 均為假；`LIVE_TRADING_ENABLED=false`（欄位 `app/config.py:172`；現讀：`app/main.py` Startup **不**消費它，daemon 只在 `app/api/paper_trading.py:567` 被顯式呼叫時才 import ⇒ 起服務不會自動起 daemon，但仍要覆蓋 `.env` 的那個 `true`） |
| 帳號與資料 | `POST /api/v1/auth/register`（`app/api/auth.py:31/:46`，`risk_acknowledged=true`）新建的**本輪測試 owner**＋`POST /api/v1/auth/login`（`:101`）；`DATA_PROVIDER=demo`；模型＝該測試 owner 名下 1 支種子個人模型版本。**不碰任何真實帳號**：不對既有使用者呼叫 `grant_daily_estimate`；〔R10〕`RESEARCH_AUTH_BYPASS_ENABLED` 必須覆蓋為 false，否則 `app/auth/jwt.py:51-61` 會把所有 loopback 請求當成「第一位 active user」，本包觀察到的就不是那個測試 owner |
| 額度（真上限） | 該測試 owner 的 `PilotEntitlement.ai_usage_limit = 1`（欄位與預設 `app/models/pilot.py:97`）⇒「一天一筆」由額度**實際強制**（超出時 `reserve_quota` 429，`app/auth/entitlements.py:252-285`）；扇出上限 `LEARNING_SCHEDULER_MAX_OWNERS=1`（欄位與預設 25＝`app/config.py:153`；`<1` 時 validator `:311-314` 直接拒設定）。此上限證明可控成本，**不代替 A6**，且**不得**拿去當 A6 的額度 |
| 網路限制（外呼＝0 的可核對做法） | 進程啟動前後各一次 `lsof -nP -p "$(cat $RUN/service-pid.txt)" -a -i` ⇒ 除 `127.0.0.1` 之外的對端列數必須為 **0**（05:00 那一響前後再做一次並存檔，共四份）；〔R10〕`.env` 裡有 `TWELVEDATA_API_KEY` 與 `DATA_PROVIDER=twelvedata`，所以 `DATA_PROVIDER=demo` 的覆蓋**必須在讀回裡被看見**（不是假設生效）；以 `ps eww -p <pid>` 只在 owner 面前做**存在性**檢查（不回顯值）。本包不安裝依賴 |
| 只觀察自然觸發 | 只等 **05:00 UTC 自己響一次**；逐步判準：①響前 `learning_jobs` 無當日 window 列、`usage_records` 無 `via=daily_service` 當日列 ②響後 `learning_jobs` 有 `job_type=daily_model_estimate`（`app/services/daily_estimates.py:63`）且 `config_json.window=<當日 UTC 日>` ③`usage_records` 當日恰好 1 列 ④`GET …/daily-estimate/status` 的 `last_estimate.inference_id` 指回同一列 ⑤訂單數 0、`decision_records`／`human_decision_events` **不因排程增加** ⑥replay 進度不推進 ⑦「同窗不重複扣額」在本包只算**待觀察預期**（互斥證據在 A6／A7） |

#### C 包：瀏覽器——兩條真實旅程（不得以 HTTP 補丁冒充）

- **工具鏈前提〔R10 新增，實測過〕**：預設 `node` ＝ **v18.20.8** 跑不動本機前端測試與 Playwright（前者需 type stripping，後者自己就拒絕：「Playwright requires Node.js 20 or higher」）⇒ 本包一律用 `~/.local/bin/node22`（v22.23.5）或 `~/.local/bin/node20`，命令裡寫死絕對路徑。瀏覽器：**bundled chromium 未安裝**（`~/Library/Caches/ms-playwright` 空），安裝屬**外呼＋依賴**，未批准；`/Applications/Google Chrome.app` 存在且 `frontend/playwright.config.ts:12` 實做 `E2E_BROWSER_CHANNEL` ⇒ 以 `E2E_BROWSER_CHANNEL=chrome` -drive 本機 Chrome，**零下載**。
- **固定 build 模式與產物隔離**：只准 `npm run build`（`frontend/package.json:7`）一次，記 rc 與**新的** `.next/BUILD_ID` 現值；〔R10〕現值 `iEzzhWbfCzP7pv7Rb0LcS`（mtime 11-19 **09:05**）早於 `simulation/page.tsx` 12:30、`paper-portfolio/[id]/page.tsx` 12:32、`decisionLineage.ts` 12:32、`DailyEstimatePanel.tsx` **17:24** ⇒ **舊產物，不得作為候選證據**。產物隔離：以 `NEXT_DIST_DIR="$RUN/next-dist"`（或在 `$RUN` 內另建一份 checkout）建置，**不覆寫共用 `.next`**。`NEXT_PUBLIC_API_URL` 是 **build-time 內嵌**變數（`frontend/src/lib/api.ts:11`，預設 `http://localhost:8000`）⇒ 必須在 build **之前** export 指到 B 包那支 8031，否則頁面打的是 8000（本機現測**無人聆聽 8000**）。
- **啟動**：`npm run start`（`:8`）先明確 `-p 3031 -H 127.0.0.1`，或 `npm run dev -H 127.0.0.1`（`:6`）；URL `http://127.0.0.1:3031`（進場當時以 `lsof -nP -iTCP:3031 -sTCP:LISTEN` 重查；啟動前後各印一次行程清單）。
- **DB／帳號**：與 B 包同一個隔離 DB、同一測試 owner；在 `src/app/login/page.tsx` 以密碼登入 ⇒ `actor_type=human`（`app/auth/provenance.py:30`）；〔R10〕共同前提 4 的 `RESEARCH_AUTH_BYPASS_ENABLED=false`＋`CANDIDATE_PASSWORDLESS_MODE=false` 屬**必要**：否則登陸後拿到的身份是「第一位 active user」，本包所有歸因作廢。
- **旅程一（/training 每日估計面板）**：開同意（`PUT …/consent`，僅本人）→ `GET …/consent` 讀回 `scope_json.skill_units[]` → 「立即估計」→ **第二次「立即估計」**：同一畫面並列 `已沿用`＋`本次拒絕`（＋若另有未開始的帳號）`執行狀態未確認`／`沒有估計` → **逐技能撤回**（控件 `daily-estimate-withdraw-*` → `DELETE …?skill_unit_id=`）→ 讀回另一支的聲明與帳本**未變**。撤回時機一句話必須在畫面上讀得到（已在執行中的那筆不退還）。四態文案（新估計／沿用／部分成功／全部失敗）逐態以**實際按到的**畫面截圖佐證，並以 §五 那條「`errors` 重載不可見」缺陷為已知邊界：失敗那支在 201 當下講得清、重新整理後只能落到「執行狀態未確認」。
- **旅程二（/paper-portfolio/[id]）**：`pp-download-decision-history`（CSV）與 `pp-download-decision-history-json`（`src/app/paper-portfolio/[id]/page.tsx:470`）→ 兩檔同一決策集合、無訂單決策不得造出交易列、`inference_source_state` **三態**（`present`／`missing`／`none`，`src/lib/decisionLineage.ts:11`）標籤顯示在列上；模擬頁面手動決策送 `linked_inference_id`（`src/app/experiments/[id]/simulation/page.tsx:515`）並從 `GET /paper-portfolio/{id}/decision-history` 與 `GET /api/v1/human-decisions` 讀回同一 id。
- **既有 e2e lane 的定位〔R10 新測〕**：`playwright.config.ts` 存在、`e2e/` 有 32 檔 **312 支**可列舉（`--list` 現測，未啟動服務、未開瀏覽器）。但 `grep -r "daily-estimate\|pp-download-decision-history" e2e/` ＝ **0 支** ⇒ 本包那兩條旅程**不在**現有自動化裡；要以 e2e 閉合需**新寫 spec**（屬工程，另交批准），否則本包只能由代理做可用性輔助、由 **owner 親手按** 完成驗收。
- **邊界（報告必附）**：代理**不得替 owner 按下同意／撤回**；build PASS／node:test PASS 不代替 browser PASS；未批准前本包 **NOT RUN**。
- **仍以源碼層如實標記的缺口**：`OrderPreviewPanel.tsx` 不送也不持有任何血緣（現測 `grep -c linked_inference_id` ＝ 0）；/training 面板只寫 `decision_records`、不寫 `human_decision_events`（§一 表）。一個入口通過不能替另一個入口宣稱通過。**〔第十三輪更正，2026-11-20〕前半句对本候選不再成立**：面板現走共用 `lineageState()`／`estimateSourceFromStatus()`／`lineageRefusalOf()`，明選時送 `linked_inference_id`（該檔現測 grep ＝ **5**，`grep -c setLineageSelected(true)` ＝ **0** ⇒ 無自動附加）；後半句（/training 面板不經事件表）仍是 §一 表的設計界定。層級仍為源碼＋`TestClient`，**瀏覽器 NOT RUN**，見 §八。

#### D 包：可審候選（逐 hunk patch＋乾淨克隆重現，本輪未執行）

| 項目 | 本輪實物 |
|---|---|
| 基準 | `git rev-parse HEAD` ＝ `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（工作樹髒路徑 **210**＝139 ` M` ＋ 71 `??`；porcelain sha256 `22df71729ee9a177ea06fba094d0fb60e78bb13dfb9f3f997c39f293f6331036`）；同一棵樹上「porcelain 的 sha256」有兩個值，因為兩個產出者雜湊的不是同一個位元串：runner `backend/scripts/run_tests.sh:386` 用 `git status --porcelain` 的原始輸出直接餵給 `shasum -a 256`（**含**最後一個換行）⇒ 本輪整套 run 的 `identity.txt` 現值 `210 path(s) sha256 778ed5ab5906f549e712ff20e853f8e175fd4e307d4abe5dc6f8cade35a29624`；review 產生器 `alethra-evidence/tools/build_review_package.py:136-143` 用 `"\n".join(...)`（**不含**尾端換行）⇒ `22df7172…`。兩者的髒路徑數都是 **210**（一致），但兩個 sha **不可互換**，引用時要一併寫明產出者 |
| 範圍判準（寫在 MANIFEST 裡） | `candidate_date=2026-11-19` 且路徑在 `backend/`＋`frontend/`，排除 `.venv\|node_modules\|__pycache__\|.next\|ml/artifacts\|alethra-evidence`，並**刻意排除**兩份釘選檔 `backend/tests/collected_nodes.txt`、`backend/tests/skip_budget.json`（產生審閱資料 ≠ 重釘） |
| 內容 | **23 個檔**：12 份 tracked patch（合計 **227,855 bytes**、**79 hunks**、**＋3,768 −542**）＋ 11 份 untracked 全位元組（合計 **363,606 bytes**）；目錄 `alethra-evidence/d-patch-round10-final/`，`MANIFEST.json` sha256 `6d0d3dcc61e27f4d545e182dd1a336b04198b978402b956d6b5554c6caeda24e`、`SHA256SUMS.txt` sha256 `d5a919a68d3a7b90388e3582bf32d7f0fdd48d000b022854057832a903588534`，`shasum -a 256 -c` 全 OK |
| 血緣三支的逐 hunk 量（本輪重點） | `backend/app/api/human_decisions.py` 2 hunks／＋6 −0；`backend/app/api/schemas.py` 7 hunks／＋105 −1；`backend/tests/test_manual_decision_books_linked_portfolio.py` 3 hunks／＋575 −6；另有 untracked `backend/app/models/human_decision.py`→patch（1 hunk／＋9 −0）與 untracked migration `d4e5f6g7h8i9_add_human_decision_inference_link.py`（4,328 bytes 全檔） |
| 依賴閉包（**必讀**） | 11 份 untracked 裡含**產品檔本身**：`backend/app/services/daily_estimates.py`（62,656 bytes）、`backend/scripts/check_migration_tail.py`、`backend/tests/test_daily_model_estimate.py`、`test_daily_estimate_two_workers.py`、`test_decision_history_export.py`、`test_migration_tail_contract.py` 與前端 `DailyEstimatePanel.tsx`／`dailyEstimate.ts`／`decisionLineage.ts`／`dailyEstimate.test.mjs`。現測 `git ls-files --error-unmatch` 對 `daily_estimates.py`／`test_daily_model_estimate.py`／`decisionLineage.ts` 回 **NO** ⇒ **只 `git apply` 那 12 份 patch 在基準 commit 上組不出可跑的候選**，必須先落 11 份未追蹤檔（Git 寫入，未批准）。 |
| 範圍外（誠實） | 11-17 改過的 `backend/tests/test_process_state_isolation.py`、`backend/scripts/run_tests.sh` **不在**這個 date-filter 包內（它們在本輪之前就改過）；其餘 187 個髒路徑（11-16／11-17／11-18 的 `backend/tests`、`backend/app`、`frontend/*`、根目錄 `*.sh`） likewise 不在包內。若 owner 要的是「相對基準 commit 的**全部**未提交內容」，那是另一張卡（210 路徑），本包自認只覆蓋 23。 |
| 重現步驟（**未執行**） | ①`git clone` 本機 repo 至 `$RUN/clone` ＋ `git checkout 5a34e3f` ②寫入 11 份 untracked（依 SHA256SUMS 驗證）③`git apply --check` 再 `git apply` 12 份 patch ④`cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh sqlite tests/test_daily_model_estimate.py tests/test_daily_estimate_two_workers.py tests/test_manual_decision_books_linked_portfolio.py`（預期 46＋50 collected、9 支具名跳過）⑤`cd frontend && ~/.local/bin/node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyEstimate.test.mjs`（預期 35／35）⑥`npm run typecheck`。步骤①②③都是 Git／檔案寫入 ⇒ **需 owner 明確批准「建立乾淨 clone 並套用」，本輪未做**；產生這份包**不**等於批准套用、stage、commit、push 或重釘。 |
| 產生器（可重跑，不依賴手抄） | `alethra-evidence/tools/build_review_package.py`：從 `git status --porcelain -- backend frontend` ＋ mtime 日期 derives 候選集，tracked 走 `git diff base -- <file>`、untracked 走全位元組，檔名 `/`→`__` 編碼（兩個 `page.tsx` 不會撞名），拒絕對已存在包的目錄重建，絕不執行 `git add/commit/push/checkout/restore/clean` |

#### 重釘卡（獨立於 A／B／C／D，單獨批准；本輪**未**執行）

- 批准內容：把 selection 清單由 **3,811 列**重釘為 `a2641cca…` 樹的 **3,820 列**，skip 上限由 `total=179 pg_only=159 live_stack=18 other=2` 重釘為**同一組數字**（本輪實測：新 run 的普查逐類别相同 ⇒ **零新跳過**）。
- 精確選集（`pin` 自己印的 diff，非手抄）：`selection diff vs pinned: -0 +9`、`acknowledgement digest: ff569c22ae5771944ea5fccddac50ab55425e6d3119c8017244c37f46a948d5`；9 支全名在 `alethra-evidence/repin-round10/drift-added-final.txt`（4 支 `test_daily_model_estimate.py`＋5 支 `TestLineageTravelsFromSubmitToExport`）、移除 **0** 支（`drift-removed-final.txt` 為空）。
- 執行者與命令（兩輪，第二輪才寫檔）：
  ```
  cd backend && .venv/bin/python scripts/run_evidence.py \
    ../alethra-evidence/full-suite-round10/final-candidate pin \
    --manifest-out tests/collected_nodes.txt --budget-out tests/skip_budget.json \
    --ack-diff ff569c22ae5771944ea5fccddac50ab55425e6d3119c8017244c37f46a948d5
  ```
  来源 run 的 `exit-code.txt`＝**0**（`run_evidence.py:504-509` 只接受 0／1 作為「真的跑過」的釘選來源，这点本輪沒有繞過）。
- 若 owner **不**批准：維持現值（sha256 `5260ece2…`／`c653b1a7…`），整套 lane exit 繼續是 **2**〔**第十一輪實測更正**：未釘時的 lane exit 實測為 **1**，不是這裡預測的 2——`run_tests.sh:484-485` 讓 ledger 閘（rc=1）覆蓋 census 閘（rc=2）；見第十一輪 §五。「被拒絕、非零」這個結論不變，數字以實測為準〕（`-0 +9` 的節點未釘），三支 ✗ 仍為 0、179 跳過分類仍與上限相同。這兩個事實必須一起讀，不得只引其中一個。

## 第十一輪（2026-11-19 19:0x–20:2x HKT）：A6 從「時間戳」換成「會拒絕的鎖等待判準」、同鍵四格補齊、整套固定在 `5d9dff9c…`

本輪只動兩件事：①把 owner 退回的 A6（「只到 claim 入口的時間戳不是鎖等待證據；額度不能掩蓋第二次扣額」）重做成一個**會拒絕的判準器**，並讓它今天就能被測；②補上每日估計「同鍵四格」缺的那一格。其餘包（A0–A5／A7／B／C／D）未動，第十輪 §六 正文仍 Effective；本輪只替換 A6 那一列與其硬性前提段（→ §六）。

### 一、客戶結果（先交能看到的那一层）

| # | 客戶問題 | 本輪答案與證據層 |
|---|---|---|
| 1 | 「平台能不能在我按了『立即估計』之後，05:00 再扣我一次？」 | **四格全部有持久讀回**（手動→手動、手動→排程、排程→手動、排程→排程），每格讀的都是帳本 row count、預留終態、原 `inference_id`，不是狀態碼。層＝⑤-HTTP in-process（`alethra-evidence/estimate-lane-final/`：`collected-node-ids: 63`、`62 passed, 1 skipped, 206 warnings in 43.77s`、`exit-code.txt`＝0、digest `5d9dff9c…`）。**跨行程那一格（兩個真實行程同時搶同一鍵）仍是 NOT RUN**，它的判準本輪才剛做好（§二、§三）。 |
| 2 | 「A6 跑出來的 PASS 值多少？」 | 本輪之前：**不值**——判準只看時間戳先后，額度也能冒充互斥。本輪之後：A6 的 PASS 必須同時通過 12 條 fail-closed 判準（鎖等待曾被看見、等待期間帳本尚未點名、釋放後重讀到那筆帳是持久的、額度沒替帳本擋人⋯⋯），任何一條不成立 ⇒ 節點 fail 並把整份 verdict JSON 印出來。判準器本身今天已被 14 個「长得對」的變造樣本測過會拒絕（§三）。**但判準存在 ≠ 它在 PG 上拒絕過什麼**：PG 層仍是 NOT RUN。 |
| 3 | 「畫面講的跟帳本一樣嗎？」 | 本輪**未改畫面**（`DailyEstimatePanel.tsx` mtime 17:24 是第十輪的事），但把畫面的狀態映射**跑過一次**：`frontend/tests/*.test.mjs` 13 檔 **187 tests / 187 pass / 0 fail／全 exit 0**（node22 v22.23.5，19:4x，`alethra-evidence/frontend-units-round11/`）⇒ 四態文案、`已沿用`／`執行狀態未確認` 標籤與「重按只沿用不重複扣額」的敘述有執行證據（層＝node unit，**不是**瀏覽器；見 §十）。第十輪 §五 那條「`errors` 類失敗在重新整理後無法具名」缺陷**仍未修**，仍是候選凍結外的欠帳。 |

### 二、A6 判準機器的本體（實物與归属）

| 檔 | bytes | 行 | git tracked? | 角色 |
|---|---|---|---|---|
| `backend/tests/daily_estimate_race.py` | 17,143 | 365 | **NO（未追蹤）** | 取樣 SQL（`:49`）＋ 12 條判準（`:68-84`）＋ `QUOTA_REASONS`（`:89`）／`LEDGER_REASON`（`:91`）＋ `assess_race()`（`:131`）。與被抓物件分開，所以能在 SQLite 層餵樣本 |
| `backend/tests/test_daily_estimate_two_workers.py` | 47,568 | 1,174 | **NO（未追蹤）** | 兩行程 barrier 本體（`:269-297`）、第三連線觀察者（`:305-358`）、額度抬高（`:383-396`）、PG 競態節點（`:598` 起）、16 支永遠會跑的判準節點（`:1106` 起） |
| `backend/tests/test_daily_model_estimate.py` | 125,976 | 2,931 | **NO（未追蹤）** | 本輪在 `:1960-2006` 新增同鍵第四格 |

**观察到的東西**（`CAPTURE_SQL` `tests/daily_estimate_race.py:49-64`）：每一幀同時取 `pg_locks`（`locktype='advisory'` 且限**當前 DB**，因為 advisory 鎖是叢集級、共用測試伺服器上可能有別人的鍵）＋ `pg_blocking_pids(l.pid)` ＋ `pg_stat_activity.wait_event_type`，並在同一幀讀帳本 row count。因此「等待」與「等待期間尚未落帳」是同一個瞬間的兩列，不是兩段各自的時間戳。

**時間戳已被降格**：`max(claim_enter) < min(estimate_enter)`（`tests/test_daily_estimate_two_workers.py:721`）仍斷言，但角色是「樣本進去的索引」；模組 docstring `:65` 明寫它不是鎖等待證據。

**額度不再能冒充互斥**：`_AI_CEILING = 5`（`:383`）> 最壞情況 4 次扣額（2 instrument × 2 行程），`_raise_ai_ceiling()`（`:386`）在起行程前把該測試帳號抬到 5；若仍有單元是被額度擋下，`refusal_reason_is_the_ledger` 與 `quota_never_stands_in_place_of_ledger` 兩條判準把整次運行的 verdict 打成不通過。**B 包的 `ai_usage_limit=1` 絕不得套在 A6／A7**。

### 三、判準自身被測（層：④-sqlite；**不是** PG 證據）

`alethra-evidence/a6-criteria-lane/`（identity 逐值）：

| 欄位 | 實測值 |
|---|---|
| `command` | `./scripts/run_tests.sh sqlite tests/test_daily_estimate_two_workers.py` |
| `collected-node-ids` | **18** |
| pytest summary | `17 passed, 1 skipped, 52 warnings in 6.55s`（跳過的那支＝PG-only 競態節點） |
| `exit-code.txt` | **0**；`evidence gate: ledger closes against this run's own artifacts` |
| `tree-digest-before`＝`after` | `3fbb810ed9dc39599b90cb98fb6d2941c41e209c7a774bc838bf9628c753c633` |
| `provider` | `DATA_PROVIDER=<unset>`（見 §七 第 2 條——這一行記的是**程序環境**，不是 harness 的保證） |

18 支＝1 支 barrier 機械本體（無需 DB）＋ 1 支 PG-only ＋ 16 支判準節點：1 正控（接受一份「鎖真的等過、釋放後帳本讀得到」的抓取）、14 支變造樣本（每支都自稱是綠的，每支必年被拒，且拒絕必須**指名**該判準並附 `says`＋可核對的 `detail`）、1 條覆蓋 gate（`CRITERIA` 裡不容許任何一條從未拒過東西）。

**這一段證的是**：判準不是裝飾，拒絕條件可執行。**不證**：PG 上鎖真的等過。後者唯一入口是 A 包 A2＋A6 實際跑（§七 第 1 條）。

### 四、同鍵四格（逐格節點與讀回的四個物理量）

| 格 | 節點（`backend/tests/test_daily_model_estimate.py`） | 讀回的物理量 |
|---|---|---|
| 手動 → 手動 | `test_the_manual_button_and_the_clock_write_the_same_estimate` `:1867`，第二按在 `:1906-1927` | `estimated==0`、帳本 1 列、`settled` 1／`reserved` 0、`recovered[0].inference_id` ＝ 原 id 且 `verdict_durable is False` |
| 排程 → 手動 | `test_the_cron_entry_and_a_press_after_it_bill_the_window_once` `:1930-1957`（含第三种請求：`:1954-1957` 仍是 1 列） | 同上＋拒絕具名 `already_estimated_this_window`、`decided_by=="billing_ledger"` |
| 排程 → 排程 | `test_a_second_tick_in_the_same_window_bills_nothing` `:615` | 第二響不新增帳本列 |
| **手動 → 排程**（本輪補） | `test_the_button_pays_first_and_the_clock_inherits_that_bill` `:1960-2006` | 按下先落帳（`estimated==1`、1 列、`settled` 1／`reserved` 0）→ `_cron` 自己推導出同一個 UTC 日 → 帳本**仍 1 列、同一 `inference_id`**、該響的 job `config.scope=="daily"`、`result.estimated==0`、`refused[0]` 以 `already_estimated_this_window`＋`decided_by=="billing_ledger"`＋`inference_ids==[原 id]` 具名、`recovered[0].verdict_durable is False` |

第四格為何不是「重排第三格」：兩條入口由不同程式碼進入（route 走 `trigger_daily_estimate`，job 走 `run_scheduled_daily_estimates`），若排程改問自己的摘要而非帳本，客戶早上按過一次、隔天 05:00 會被**隔空**再扣一次。本輪新增節點實測通過，證明它問的是帳本。

另兩格（非同一鍵，列此以免被誤讀成已證）：`test_a_clock_that_finds_the_button_refused_still_walks_the_scope`（手動**拒用**後排程仍估）與 `test_a_press_that_finds_the_clock_refused_still_asks_the_gate`（排程拒用後手動仍問閘門）。

### 五、固定候選的整套結果（`5d9dff9c…`，一次，跑完）

**跑完一次，全绿在 pytest 層、被證據閘擋下。** run dir `sqlite-20261119T113033Z-87893`（起於 19:30:43 +08，歷時 `2383.89s` ＝ 39:43）；整套 artefacts 已從 TMPDIR 抄入 `alethra-evidence/full-suite-round11/`（`identity.txt`／`collect.txt`／`junit.xml`／`pytest.log`／`exit-code.txt`／`wrapper-tail.txt`，共 6 檔）。逐值：

| 量 | 實測值（來源檔） |
|---|---|
| 候選同一性 | `tree-digest-before` ＝ `tree-digest-after` ＝ `5d9dff9c7d4b204320fd8d9de0d9f23ccbfb7b25bfd8099434e5da6f48876c3f`（`identity.txt`）⇒ 39:43 之內沒有任何 `backend/**/*.py` 變動 |
| 基準／髒樹 | `git: 5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a (working tree dirty…)`；`porcelain: 211 path(s) sha256 8c3a89003314512b00cbdd4f3e066d48b9afe3231e0b216fbc9a7869260143ac`（runner 端慣例，見 §八補） |
| 環境 | `lane fingerprint: python 3.12.16 \| pytest 8.3.3 \| sqlalchemy 2.0.36`；`provider: DATA_PROVIDER=demo`（讀回，非假設） |
| 選集 | `collected-node-ids: 3837`（`identity.txt`；`collect.txt` 同值）＝釘選基準 3,811 ＋26 |
| pytest 自己的結果 | `exit-code.txt` ＝ **0**；`3661 passed, 179 skipped, 11163 warnings in 2383.89s (0:39:43)`（`pytest.log` 尾行） |
| JUnit | `tests="3840" skipped="179" failures="0" errors="0" time="2383.818"`；`reported units 3840 = collected 3837 + collection-level 3 -> OK`；`unresolved 0`／`duplicate 0`／`collected but no record 0`／`record without id 0` |
| 跳過普查 | `skip categories pg_only=159 live_stack=18 other=2 (total 179 = item 176 + collection 3)`；`console census units=179 … -> agrees with the recorded outcomes`；上限檔現值 `collected=3811 total=179 pg_only=159 live_stack=18 other=2` ⇒ **類別與總數零偏移**（26 支新節點全數通過，未產生任何新跳過） |
| 證據閘 | `EVIDENCE GATE REFUSES:` 兩條——①`refused: this whole-suite run collected 3837 tests but the budget pins 3811…`（skip-census 閘 rc=**2**）②`26 node(s) are collected but not pinned; the pinned selection is re-written only by pin --ack-diff, never by accepting the newest total:`（ledger 閘 rc=**1**，其後逐名列出 26 支） |
| **lane exit** | **實測 1**，非第十一輪上文（以及第十輪 §六 重釘卡）預測的 2。原因現讀：`run_tests.sh:481` 取 `BRC`（census 閘）＝2、`:483` 取 `ERC`（ledger 閘）＝1，而 `:484-485` 是 `GATE=$BRC` 後 `if [ "$ERC" -ne 0 ]; then GATE=$ERC`——**後一個閘的代碼覆蓋前一個**，於是 `:486-488` 印 `not green: pytest passed but the evidence gates refused (skip census 2, ledger 1).` 並以 `RC=$GATE` ＝ **1** 結束（`:494 exit "$RC"`）。⇒ 「未重釘 ⇒ lane 拒絕」這件事本身成立（rc≠0 且拒絕原因具名），**具體數字必須以本次實測 1 引用**；「钉完變 0」仍是待驗收的預期，不是已量到的值 |
| 三支前失敗節點 | 在本選集內且**未**出現在任何 failures／errors（`junit.xml` 三筆皆 `passed`）；另有逐支最小重驗目錄 `alethra-evidence/f123-green-final/`（3 collected／`3 passed, 38 warnings in 1.58s`／`exit-code.txt`＝0／digest `5d9dff9c…`／`DATA_PROVIDER=demo`） |

這正是 owner 要的兩件事同時成立：**「0 failures」不等於 lane 綠**（閘因未釘而拒絕，且把 26 支具名列出），而**拒絕也沒有被用來掩蓋任何失敗**（跳過分類與 3,811 版本逐類别相同，26 支新節點全部通過）。

### 六、修訂後的 A6 可審正文（取代第十輪 §六 的「A6」列與其「硬性前提」段；A0–A5／A7 未變）

| 步 | 動作（可執行值） | 寫入範圍 | 判準（通過才准進下一步） | 中止條件 |
|---|---|---|---|---|
| A6 | `cd backend && . "$DSN" && export TEST_MODE=true DATA_PROVIDER=demo ALETHRA_RACE_CAPTURE_JSON="$RUN/race-capture.json" && ./scripts/run_tests.sh pg tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once tests/test_daily_model_estimate.py::test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice` | 該隔離 DB＋`$RUN/race-capture.json`（`_write_capture()` `:437-463` 只寫檔、**不建父目錄** ⇒ `$RUN` 必須已存在；未設該環境變數則不寫檔，判準照跑） | **12 條判準全過**（`assess_race()` `tests/daily_estimate_race.py:131`）：`observer_visibility`／`wait_observed`／`blocker_is_holder`／`lock_object_matches`／`key_unbilled_while_blocked`／`bill_durable_after_release`／`holder_claim_was_free`／`loser_reread_refused`／`one_bill_per_business_key`／`settlement_matches_bills`／`quota_never_stands_in_place_of_ledger`／`refusal_reason_is_the_ledger`。三支 node id 要在**該次 pg 層** `--collect-only` 現測得到才算數（本輪**未**在 PG 層量過；SQLite 層量過前兩支） | 節點以 `the seat being raced is pg_advisory_xact_lock, which SQLite has no way ...` 跳過（`:598-602`）＝DSN 沒生效 ⇒ **NOT RUN**；60s 逾時／`Barrier` 未湊齊／`proven=false` ⇒ 節點 `pytest.fail` 並在訊息裡印整份 verdict JSON，該次是 **NOT RUN／INVALID**，**不得**寫成 PG 競態 PASS；現場保留給 owner，不刪 `market_data_bars`（無 owner 欄可 scope，屬共享種子） |

同步機制現讀（寫進 run dir 用）：`mp.get_context("spawn")`（`:269`／`:650`）、`ctx.Barrier(2)`（`:270`／`:651`）、`_TIMEOUT_S = 60`（`:170`）、持有窗 `_HOLD_S = 3.0`（`:178`；`:59` 的註解說明這是 wrapper 的時機改動、不是產品行為）、觀察者 `_POLL_S = 0.05`＋`_observe_frames()`（`:179`／`:305`）、連線層 `SET lock_timeout='30s'`／`statement_timeout='120s'`／`idle_in_transaction_session_timeout='90s'`（`:241-243`）。窗口計數只引用 row count，**兩個 job 摘要的 `billed_in_window` 不相加**。

### 七、披露／NOT RUN／本輪新發現

1. **PG 全部步驟（A0–A7）＝NOT RUN**（未批准、未執行；19:2x 現測 5432 無聆聽、`/tmp/.s.PGSQL.5432` 不存在）。§三 的 17／18 PASS 是**判準層**證據，不是鎖等待證據。
2. **`run_tests.sh` 不設定也不強制 `DATA_PROVIDER`，只在 `:394` 記錄程序環境的值**——本輪實測：同一條 `./scripts/run_tests.sh sqlite` 命令，第十輪那次記錄 `DATA_PROVIDER=demo`、本輪 §三 與被中斷的那次記錄 `DATA_PROVIDER=<unset>`。差別只在啟動時的 shell 有沒有 export。後果：`backend/.env`（gitignored）內有 `DATA_PROVIDER=twelvedata` 與真金鑰，`app/config.py:560` 的 `env_file=".env"` 會吃到它。因此**任何宣稱「零外呼」的 lane 必須在命令裡顯式 `export DATA_PROVIDER=demo` 並以 identity 讀回證明**；§三 那 18 支純 in-process、不建 provider（由其節點性質推得，不是由 harness 保證），本輪 §四／§五 的每次 run 都已顯式 export。建議（未實作）：讓 runner 在 sqlite/pg 層自行 export `DATA_PROVIDER=demo`，把它從「Launch 者的紀律」變成「harness 的保證」。
3. **本輪第一次整套嘗試在 32% 被中斷**：自己起的背景行程（pid 79054／79057／79231），因該 candidate（`3fbb810e…`）隨即被第四格節點取代，繼續跑只會產出綁在過期候選上的證據。SIGTERM 對 pytest 無效（狀態 `R`，兩次 TERM 後仍在）⇒ 以 SIGKILL 收掉，並刪掉它那顆 `alethra-sqlite-test-79057.db`（本 session 自己的暫存檔）。該次**只留下 `identity.txt`／`collect.txt`**（`collected-node-ids: 3836`，無 junit、無 exit code）⇒ **不作為證據引用**，只作為「中途取消」的事實記錄。
4. **證據目錄曾錯放**：本輪前 9 個 evidence dir 被建立在 workspace 根 `ac51e592/alethra-evidence/`（與第十輪的 `ac51e592/alethra/alethra-evidence/` 不同根）。19:2x 已 `mv` 進 repo 內同一根，**內容未改、僅位置**；本節所有引用皆為 repo 相對路徑 `alethra-evidence/…`。
5. **第十輪整套 exit-1 的原始 run dir 已不在**（當時未從 TMPDIR 抄出），只剩記錄轉抄（`:2104`）與逐節點 pre-fix 重現目錄（`repro-f1-gate-pre`／`repro-f2-replay-pre`／`repro-f3-head-pre`，三支各自 exit 1，digest `50130606…`）。這是既成缺口，不得以本輪 exit-0 覆蓋它。
6. **`OrderPreviewPanel` 在本候選仍不帶血緣**：`frontend/src/components/workspace/OrderPreviewPanel.tsx:417-421` 的 payload 只有 `action`／`idempotency_key`／`rationale`／（條件性）`quantity`／`expected_version`，`grep -c linked_inference_id` ＝ **0**。模擬頁（`src/app/experiments/[id]/simulation/page.tsx`，5 处）與 `/paper-portfolio/[id]`（3 处）有，`/training` 面板是估計而非決策入口。一個入口通過不替另一個入口宣稱通過。**〔第十三輪更正，2026-11-20〕該列計量的是第十二輪候選**：第十三輪工作區面板已接明選血緣（該檔現測 `grep -c linked_inference_id` ＝ 5，與模擬頁同值），`:417-421` 那個行號也隨之位移；本輪候選的兩道門對照見 §八。
7. **重釘未執行**：本輪把 `two_workers` 從 2 支長到 18 支、`test_daily_model_estimate.py` 再長 1 支，`tests/collected_nodes.txt`（3,811 列）與 `tests/skip_budget.json` **一字未動**；因此整套 lane 在釘選前被證據閘拒絕，**lane exit 實測＝1**（第十輪 §六 重釘卡寫的「會是 2」是預測，已被本輪實測取代：`run_tests.sh:481` census 閘 rc=2、`:483` ledger 閘 rc=1，而 `:484-485` 讓後者覆蓋前者 ⇒ `exit 1`；逐字輸出見 §五）。跳過分類未變（本輪整套 `console census units=179 pg_only=159 live_stack=18 other=2 -> agrees with the recorded outcomes`）。重釘屬第十一輪 §十一 那一包，本輪**未**執行帶 `--ack-diff` 的重釘、未 stage／commit／push；**只**執行過 §十一 那支只讀預演（`rc`＝2、兩份受控檔 sha256 前後逐字相同，實測記錄在該節）。
8. **仍欠的工程**：`errors` 類失敗重載不可見（第十輪 §五）、durable lease／心跳、B 包 05:00 自然觸發（最早視窗 2026-11-20 05:00 UTC＝13:00 HKT）、C 包兩條瀏覽器旅程（無服務行程、`.next` 產物過期、既有 312 支 e2e 未覆蓋）、行程級崩潰（kill-9／斷電）。
9. **D 包範圍缺陷（本輪現測，已修產生器）**：`--date` 篩選讓歷輪 D 包只含**當日 mtime** 的來源檔——本輪實測髒工作樹有 126 修改＋62 未追蹤路徑，包內只有 24 個，**158 個不在任何審閱包**（含 `backend/app/api/personal_models.py` ＋180 −19）。詳細數字、修法與完整包見 §八補。同節也記錄舊 MANIFEST 行數正則計數少算的事實，以及一個**未刪**的 0 bytes 未追蹤檔 `backend/postgres`（mtime 2026-11-16 11:54，無副檔名，疑似 redirect 打錯留下）——非本 session 建立，交 owner 裁決，代理不自動刪。
10. **「不要改一般按需 predict 的語義」——本候選 vs 基準 `5a34e3f` 確實改了**（本輪現讀 diff，非推測）：`backend/app/api/personal_models.py` 的 `POST /records/{record_id}/predict`（未帶 `version_id` 那扇門）在基準是 `target_id = version_id or record.active_version_id`，在本候選變成**三級解析**：明示 `version_id` → 該 `skill_unit_id` 自己的選擇（`latest_skill_selection`）→ record 層 alias，且回應多帶 `resolved_version.resolved_from`（hunk `@@ -685,8 +769,22 @@`，函式名同時由 `predict_with_record_alias` 改為 `predict_with_version_in_use`，**路徑字串未變**）。`POST /models/{version_id}/predict` 那扇門只多了 `resolved_from="explicit_version_id"` 的標記（hunk `@@ -663,19 +746,20 @@`），解析結果不變。⇒ 同一個「未具名＋帶 skill_unit_id」請求，**回答的版本可能與基準不同**（這是 #177 那包 skill 層選擇的既定設計，且已被釘選節點 `tests/test_formal_skill_unit_authorization_endpoint.py::test_each_skill_gets_its_own_version_from_the_unnamed_predict_route`（`:282`，在 3,811 列之內、本輪整套有跑）釘住），但**它是語義變更**，不是「未動」；每日估計那一包本身另有一條防越界的現讀：`app/services/…` 的估計不另開模型入口，`app/api/personalization.py:12` 明寫「nothing in this file runs a model by another door」。**待 owner 裁決**：這條 predict 解析變更要算進本輪批准範圍，還是拆成獨立一包。

### 八、本版 D 包實物（數字取代第十輪 §六 D 包表；產生審閱資料 ≠ 套用／stage／commit／push／重釘）

`python3 alethra-evidence/tools/build_review_package.py --out alethra-evidence/d-patch-round11 --base 5a34e3f --date 2026-11-19`（19:4x HKT 產生，跑在整套 run 期間但**未**動任何 `backend/**/*.py`，所以樹是凍結的）。

| 項目 | 本版實測 |
|---|---|
| 基準／HEAD | `base_commit`＝`5a34e3f`、`head_commit_at_build`＝`5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` |
| 髒路徑 | **211**（第十輪 210，多的這一路徑＝`backend/tests/daily_estimate_race.py`）；porcelain sha256 **兩個值必須標明產出者**：review 產生器（`"\n".join`，無尾端換行）`4d944dc3cc125e8ed83bfd307cbf191a9f497b56480708c42d0a7742cb293b8e`；lane runner（`run_tests.sh:386`，原始輸出含尾端換行）`8c3a89003314512b00cbdd4f3e066d48b9afe3231e0b216fbc9a7869260143ac`。兩者路徑數一致，sha **不可互換** |
| 內容 | **24 個檔**＝12 tracked patch（**227,855 bytes／79 hunks／＋3,768 −542**，與第十輪逐字相同：本輪**沒有**動任何 tracked 檔）＋ **12 untracked 全位元組（407,159 bytes）**（第十輪 11 檔／363,606 bytes） |
| 本輪新增／長大的未追蹤檔 | `backend/tests/daily_estimate_race.py` 17,143 B（sha256 前綴 `18567720342ca93d`）、`backend/tests/test_daily_estimate_two_workers.py` 47,568 B（`4316ae54cf9aa310`）、`backend/tests/test_daily_model_estimate.py` 125,976 B（含本輪第四格）、`backend/app/services/daily_estimates.py` 62,656 B（本輪未動） |
| 依賴閉包（必读） | 12 支未追蹤檔裡含**產品檔本身**，現測 `git ls-files --error-unmatch` 對 `backend/tests/daily_estimate_race.py`／`test_daily_estimate_two_workers.py`／`test_daily_model_estimate.py` 全回 **NO** ⇒ 以「乾淨 clone ＋ 只套 tracked patch」重現必定 ImportError；要重現必須把 untracked 全套進去 |
| 完整性 | `SHA256SUMS.txt` 全列 `shasum -a 256 -c` ＝ **OK（非 OK 行數 0）**；`MANIFEST.json` sha256 `3a7a36092383e069e835c59c0591d6bb1fab4a14dc4d2fb4440638267a6f5400`、`SHA256SUMS.txt` sha256 `3408074c2de3c250000c55c2fdd49016578fef2a197464d394c4018cfc3b5228` |
| 未執行 | 未 `git apply`、未 stage／commit／push、未建乾淨 clone 重現、`tests/collected_nodes.txt`／`tests/skip_budget.json` 未動（產生審閱資料 ≠ 重釘） |

### 八補、上表那個「24 個檔」不是整套未提交差異（本輪現測缺陷，已修產生器）

上表（以及第十輪 D 包）用的 `--date` 篩選是**照檔案 mtime 的日期**決定誰進包。現測：`git status --porcelain -- backend frontend` 有 **126 個 tracked 修改檔＋62 個未追蹤路徑**，但它們的 mtime 落在 **11-16（43）／11-17（55）／11-18（16）／11-19（12）** 四天裡，因此日期篩選只收了 11-19 那 12＋12＝**24 個**，其餘 **158 個（114 修改＋44 未追蹤）不在任何審閱包內**。具體後果（現讀）：`backend/app/api/personal_models.py`（＋180 −19，mtime 2026-11-18 18:10）出現在 owner 手上的每一包 D 附件裡都**不存在**。⇒ 以「乾淨 clone `5a34e3f` ＋ 套 D 包 tracked patch」不可能重現本機工作樹，第十輪／本輪上文「依賴閉包」那一行只在**當日改過的檔**之內成立。

修法（產生器在 `alethra-evidence/tools/` 之下，不在 `backend/**/*.py`，跑整套期間改它**不動候選**；本輪 19:5x 現測 `tree-digest` 仍是 `5d9dff9c7d4b204320fd8d9de0d9f23ccbfb7b25bfd8099434e5da6f48876c3f`）：

1. `--scope {date,all}`（`build_review_package.py:69-111` 的 `candidate_files()`）；`all` ＝「所有相對 `--base` 髒的來源檔」，即能還原工作樹的那個集合。
2. `MANIFEST.json` 現在帶 `scope`（`:186`）、`excluded_by_date`（被日期篩選丟掉的**逐檔清单**，`:193`／計數 `:200`）與 `base_diff_shortstat_backend_frontend`（`:194`），所以日期包不能再被誤讀成整套 diff；另新增 `porcelain_sha256_convention`（`:187`）標明該 sha 的換行慣例。
3. **計數改用 `git diff --numstat`**（`build_review_package.py:118-127` 定義、呼叫點在 `build()` 的 `M` 分支）。舊的正則 `^\+[^+]`／`^-[^-]` 把「內容本身以 `+`／`-` 開頭」的增删行當成檔頭，會少算。本輪對**上表那 12 個檔**同時量兩套：正則 **＋3,768 −542**（＝上表值），git numstat **＋3,769 −545**；差在 `backend/tests/test_learning_scheduler.py` −118→**−121**（3 行刪除內容以 `-` 開頭）與 `backend/tests/test_replay_credibility.py` ＋464→**＋465**（1 行新增以 `+` 開頭），其餘 10 檔兩套一致。⇒ **上表與第十輪 D 包 MANIFEST 的行數是下限**，引用時請用本節 git 值；hunk 數、bytes、sha256 不受影響（patch 位元組沒變，舊包仍在原處）。完整包兩種算法也量過：正則 13,762／2,724，git **13,763／2,740**。

完整包（本輪實際要交的那一份）：

`python3 alethra-evidence/tools/build_review_package.py --out alethra-evidence/d-patch-round11-full --base 5a34e3f --date 2026-11-19 --scope all`

| 項目 | 實測（19:5x HKT） |
|---|---|
| 內容 | **182 個檔**＝126 tracked patch（**992,381 bytes／639 hunks／＋13,763 −2,740**）＋ 56 untracked 全位元組（**1,050,629 bytes／24,943 行**）＝ artifact 合計 **2,043,010 bytes**（包體含 MANIFEST/SHA256SUMS 共 2.4 MB） |
| 對帳 | `counts.patch_added_lines`＝**13,763**、`patch_removed_lines`＝**2,740**，與 `git diff --shortstat 5a34e3f -- backend frontend` 的 `126 files changed, 13763 insertions(+), 2740 deletions(-)` **逐字相符**（這正是換掉正则的理由） |
| 分佈 | backend 138 檔／frontend 44 檔；未追蹤 56 檔＝40 `.py`＋7 `.ts`＋6 `.mjs`＋3 `.tsx` |
| 未追蹤但**不在**包內（6 路徑，全部現測） | `backend/tests/collected_nodes.txt`、`backend/tests/skip_budget.json`（釘選帳本，刻意排除）、`backend/DECISION_LINEAGE_DELIVERY.md`、`backend/DECISION_LINEAGE_REVIEW.md`（文件）、`backend/.env.test.example`（範本）、`backend/postgres`（**0 bytes、mtime 2026-11-16 11:54、無副檔名**，`file` ＝ empty ⇒ 疑似某次 redirect 打錯留下的誤檔；**未刪除**，因非本 session 建立，交 owner 裁決） |
| 完整性 | `shasum -a 256 -c SHA256SUMS.txt` → 非 OK 行 **0**；`MANIFEST.json` sha256 `72f226c01cbff6dfe0c6e834995a335831bb10a8f9b22b195c64587b51922222`、`SHA256SUMS.txt` sha256 `f580ca7ea1e1ed7089178c3ff64b22a03226f464871b9bce13630f4bd8b6c1b8` |
| porcelain | 211 paths、產生器端 sha256 `4d944dc3cc125e8ed83bfd307cbf191a9f497b56480708c42d0a7742cb293b8e`（**未變**，與上表同）；MANIFEST 現在自己寫明 `porcelain_sha256_convention`，免得讀者把「產生器 `"\n".join`」與「runner 原始位元組含尾端換行」兩個 sha 的差異當成樹變了 |
| 未執行 | 同上文：未 `git apply`、未 stage／commit／push、未建乾淨 clone 重現、兩份釘選檔未動。**產生審閱資料 ≠ 套用 patch**，兩件事要分別批准 |

### 九、B／C 包前提的本版重測（規則不變，時刻是新的）

19:4x HKT 現測：`pgrep -fl "postgres|uvicorn|next-server"` ＝ **0 支**；`lsof -nP -iTCP:<port> -sTCP:LISTEN` 在 **5432／8000／8031／3000／3031 各 0 列**；`/tmp/.s.PGSQL.*` ＝ **0 個**。⇒ A／B／C 三包仍 **NOT RUN**，且「現測未聆聽」只是空位，**不是**可用證據。前端產物仍是舊的：`frontend/.next/BUILD_ID`＝`iEzzhWbfCzP7pv7Rb0LcS`（mtime 2026-11-19 **09:05**）早於 `frontend/src/components/training/DailyEstimatePanel.tsx`（mtime **17:24**）⇒ C 包仍必須先 `npm run build` 一次並記錄**新的** BUILD_ID，且以 `NEXT_DIST_DIR` 隔離產物。第十輪 §六 共同前提 4（`backend/.env` 是活的污染源：`RESEARCH_AUTH_BYPASS_ENABLED=true`、`DATA_PROVIDER=twelvedata`、`LIVE_TRADING_ENABLED=true`、`CANDIDATE_PASSWORDLESS_MODE=true`）本輪未變，且 §七 第 2 條那條新發現（runner 不強制 `DATA_PROVIDER`）使該前提從「B／C 包要覆蓋」升級成「**每一支 lane 都要在命令裡 export 並讀回**」。

### 十、前端卡片單元的本版實測（層：node unit，**不是**瀏覽器證據）

整套 run 進行中同步量（只讀 `frontend/**`，未動 `backend/**/*.py` ⇒ 候選 digest 未動，19:4x 現測仍 `5d9dff9c…`）。逐檔命令（`frontend/tests/*.test.mjs` 檔頭 `:3` 記載的跑法）：

```
cd frontend && node22 --experimental-strip-types --import ./tests/tsReadLoader.mjs \
  --test tests/<name>.test.mjs
```

實測（`node22` ＝ v22.23.5；原始輸出與 `SUMMARY.txt`／`SHA256SUMS.txt` 存 `alethra-evidence/frontend-units-round11/`）：

| 檔案 | #tests | #pass | #fail | exit |
|---|---|---|---|---|
| `dailyEstimate` | 35 | 35 | 0 | 0 |
| `dailyService` | 10 | 10 | 0 | 0 |
| `dataManifest` | 23 | 23 | 0 | 0 |
| `runCancellation` | 11 | 11 | 0 | 0 |
| `runRetry` | 17 | 17 | 0 | 0 |
| `runTradeExport` | 11 | 11 | 0 | 0 |
| `apiRequestCorrelation` | 6 | 6 | 0 | 0 |
| `benchmarkState` | 7 | 7 | 0 | 0 |
| `displayHelpers` | 15 | 15 | 0 | 0 |
| `feedbackDraft` | 4 | 4 | 0 | 0 |
| `revisionSpec` | 15 | 15 | 0 | 0 |
| `strategyPreflight` | 6 | 6 | 0 | 0 |
| `userErrors` | 27 | 27 | 0 | 0 |
| **合計（13 檔）** | **187** | **187** | **0** | 全 0 |

與第十一輪那些條款的對應（現讀，`frontend/src/lib/dailyEstimate.ts`）：四態文案 `PRESS_NOTICES_ZH` ＝「本次新增估計」`:160`／「沿用」`:162`／「部分成功」`:164`／「全部失敗」`:166`；狀態標籤 `carried_over: '已沿用'` `:105`、`unconfirmed: '執行狀態未確認'` `:108`；未結算視窗 `:119`；「重按只沿用、不會多扣一次額度」`:123`。撤回／未啟用的分流在 `frontend/src/components/training/DailyEstimatePanel.tsx:103-104`（`not_declared`→「目前未啟用（從未聲明）」、`consent_withdrawn`→「本人已撤回（先前曾聲明）」），其**持久依據**在产品侧：`backend/app/services/daily_estimates.py:1251` `_consent_withdrawn_for()` 只認 `AuditEvent` 裡 `daily_estimate_consent_granted` 曾點名該帳號、之後有 `daily_estimate_consent_revoked`，才算「已撤回」；沒有這段事件史就回 `not_declared`（呼叫點 `:1424-1426`）。

**這 187 支證明的界線**（不得越界引用）：它們讀的是 `dailyEstimate.ts` 匯出的純函式與面板原始碼文字，檔頭 `:9-11` 自己寫明「wiring 段落讀 panel source text，**不是 rendered-UI 證據**」。⇒ 四態**文案與狀態映射**有執行證據；「客戶在瀏覽器裡實際看到那張卡、按下按鈕、看到讀回」仍是 **C 包 NOT RUN**（無服務行程、`.next` 產物過期，見 §九）。

### 十一、正式版重釘卡（取代第十輪 §六 的「重釘卡」；本輪**未執行**，等 owner 批准）

先把「能不能釘」這件事量清楚，而不是假設：`run_tests.sh:436` 在 pytest 回來後**立刻** `echo "$RC" > "$RUN_DIR/exit-code.txt"`，`run_evidence.py:233` 讀的就是這顆檔，而 `cmd_pin()` 只接受 `exit_code ∈ {"0","1"}`（`run_evidence.py:503-507`）。⇒ **即使 lane 因「26 支未釘」被 `run_tests.sh:474-475` 的 ledger gate 打成 exit 2，來源 run 仍是合法的釘選輸入**（lane exit ≠ pytest exit；`:453` 那行輸出自己標明「pytest's own status, recorded before any gate」）。這也解释了 `skip_budget.json` 現值裡 `provenance.exit_code` ＝ `"1"`。

| 欄 | 值（全部本輪現測，非預測） |
|---|---|
| 來源 run（**已落地**） | 第十一輪整套 run（`DATA_PROVIDER=demo`、digest `5d9dff9c7d4b204320fd8d9de0d9f23ccbfb7b25bfd8099434e5da6f48876c3f`、run dir `sqlite-20261119T113033Z-87893`）；8 份 artefacts（含 `wrapper-tail.txt`／`pin-preview-dryrun.txt`／`SHA256SUMS.txt`）在 `alethra-evidence/full-suite-round11/`，數值見 §五。`SHA256SUMS.txt` 逐檔 `shasum -a 256 -c` ＝ **7 檔全 OK**、該清單本身 sha256 `63207544266eb825d94e7ac90c6835edb6fc00a5189299c8bbf0e625de40c1bf`（其中 `identity.txt` `9b273300…`、`junit.xml` `ebefc0d1…`、`pin-preview-dryrun.txt` `7a6e409b…`） |
| 現值（原批准基準） | `tests/collected_nodes.txt` **3,811 列**、sha256 `5260ece237d728a21637f79dcdcbad6b1ffd540b814ffa2361fbf0026cff3cbf`；`tests/skip_budget.json` sha256 `c653b1a77dfd45acf60fb7e515c3514de9f1d58c542e0d52eb37d0e4b59d95f8`（`collected=3811 total=179 pg_only=159 live_stack=18 other=2`） |
| 本次選集 | **3,837** 支（該 run `collect.txt` 實測 `3837 tests collected in 3.67s`） |
| 逐檔分解 | `tests/test_daily_estimate_two_workers.py` **+16**（14 支變造樣本節點 ＋ 1 支正向對照 ＋ 1 支覆蓋閘）、`tests/test_daily_model_estimate.py` **+5**（`test_a_partial_tick_settles_only_the_skill_whose_engine_answered`／`test_a_press_whose_engine_died_names_the_failure_in_the_response`／`test_a_skill_nobody_declared_is_not_reported_as_withdrawn`／`test_a_tick_where_every_engine_died_settles_nothing`／`test_the_button_pays_first_and_the_clock_inherits_that_bill`）、`tests/test_manual_decision_books_linked_portfolio.py` **+5**（`TestLineageTravelsFromSubmitToExport` 五支血緣旅程）；**刪除 0 支** |
| 重釘 diff 與_digest | 用**工具自己的函式**算（`run_evidence.manifest_ids()`／`diff_block()` 直接 import，非手抄）：`selection diff vs pinned: -0 +26`、**acknowledgement digest `7851af44bfbd9e665f8e7282ca301ab370b7bef73505d5a87d7121b1ff5e69b4`**（body 3,359 bytes／26 行）。第十輪那張卡寫的 `-0 +9` 是**當時**候選的量，已被本輪取代 |
| skip 分類 | 本輪不預測。钉選前**唯一**合法的 skip 數字是來源 run 自己印的 console census（§五 填入）；若 total 偏離 179，`skip_budget.py` 的閘會自己拒絕，`pin` 也會因 `structural_failures()` 拒絕 ⇒ **先跑、再釘，不可先猜再改** |
| 命令（批准後才執行第二步） | ①**只讀預演——本輪已執行（20:1x）**：`cd backend && .venv/bin/python scripts/run_evidence.py ../alethra-evidence/full-suite-round11 pin --manifest-out tests/collected_nodes.txt --budget-out tests/skip_budget.json` ⇒ **實測 `rc`＝2**（`run_evidence.py:517-521` 在 `ack_diff != digest` 時先 `return 2`），輸出名為 `-0 +26`、digest `7851af44bfbd9e665f8e7282ca301ab370b7bef73505d5a87d7121b1ff5e69b4`（與本卡上表逐字相同，26 支全名由**工具自己**印出，已存 `full-suite-round11/pin-preview-dryrun.txt`），且前後 `shasum -a 256` 兩行**逐字相同** ⇒ 證明「refusal 發生在任何寫入之前」不是宣稱而是量到的事實。②**批准正文（未執行）**：同命令再加 `--ack-diff 7851af44bfbd9e665f8e7282ca301ab370b7bef73505d5a87d7121b1ff5e69b4`（`cmd_pin` 只在 ack 相符時才寫那兩份檔） |
| 批准後必存的驗收 | 重釘前後兩份檔的 sha256 ＋ `diff -u` 原文（預期**只**有 26 行 `+`、0 行 `-`，且 `skip_budget.json` 的 `collected` 3811→3837、`provenance` 整塊改寫）；釘完再跑一次整套，lane exit 必須從**本輪實測的 1**（非第十輪預測的 2，理由見 §五）變成 **0**（還不是 0 ⇒ 有新差異，停，別改閘） |
| 邊界（沿用 owner 原話） | 「不要自動重釘、整檔還原共享檔，或使用新 skip budget 掩蓋差異。」⇒ 本卡**只**動那兩份受控檔；`--ack-diff` 之外的任何 `pin` 呼叫、任何手動編輯 `skip_budget.json`、任何放寬 `skip_budget.py` 都不在批准內。產生審閱資料（§八／§八補）與套用／stage／commit／push 仍是**不同**批准 |

## 第十二輪（2026-11-19 20:2x–20:4x HKT）：獲准基準的考古（三段差值只有一段可量測）、#177 契約、`errors` 重載可見性

**狀態（沿用第十一輪那一顆燈，未重跑）**：整套 **pytest exit 0**（`3,661 passed／179 skipped`、`exit-code.txt`＝0）於候選 `5d9dff9c…`；正式 **lane exit 1**（census rc 2、ledger rc 1，ledger 覆寫 ⇒ `run_tests.sh:481/:483/:484-485/:486-488/:494`）。本輪現測候選 digest **未動**（20:3x 產生實物包之前＝之後＝`5d9dff9c…`）；未重釘（只跑唯讀 `skip_budget.py check`，rc **2**，兩份受控檔 sha 前後逐字相同）；未套 patch、未 stage／commit／push、未初始化 PG、未跑 migration、未啟服務／瀏覽器、未外呼。**本輪 owner 提示不是執行批准**，故本輪所有「批准正文」都只寫不辦。

### 一、同一候選與正式基準：三個狀態、三段差、只有中段可量測

#### 1.1 「第十輪 `5a33f527`」不是本 repo 裡的任何物件（本輪先更正這件事）

| 檢查 | 實測（20:3x；命令與 exit 逐字存在 `baseline-round12/negative-proofs.txt`） |
|---|---|
| `git cat-file -t 5a33f527` | **exit 128** ＋ `fatal: Not a valid object name 5a33f527` ⇒ 它不是 commit，也不是 tree／blob／tag |
| `git cat-file -t 5a34e3fe` | exit 0 ＋ `commit` |
| `git rev-parse HEAD` | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（2026-11-16 09:49:42 +0800） |
| 各輪 real artifacts 裡的 base | `full-suite-round11/identity.txt` 第 3 列＝`5a34e3fe…`；`skip_budget.json` 的 `provenance.identity.git`＝`5a34e3fe…`；兩份 D 包 MANIFEST 的 `base_commit`＝`5a34e3f` |
| repo 內文字搜尋 `5a33f527`（排除 `.git`） | **0 处**（本輪唯一的新增出处就是那份「證明它不存在」的檢查檔與其產生器） |

⇒ 兩者**不是**「兩個 base」的前後關係：本機**只有一個** base（`5a34e3fe`），`5a33f527` 是第十輪正文裡一個查無出處的 sha 字串。此處更正之，本輪之後（以及任何 patch／乾淨克隆重現）一律以 `5a34e3fe` 為基準；引用第十輪 §六 那段「`5a33f527`」的地方都要改成 `5a34e3fe`，否則「套 patch 重現」會被導向一個不存在的物件。

#### 1.2 base、未提交工作樹、實際受測內容是三件事（本輪現測，並更正第十一輪兩處 scope 標註缺漏）

| 層 | 本輪實測 | 它是什麼 |
|---|---|---|
| **base** | `5a34e3fe` | 乾淨 clone 的內容。所有 patch 都相對它算 |
| **未提交工作樹** | 全 repo `git status --porcelain`＝**211 路徑**（139 `M`＋72 `??`）sha256 `8c3a89003314512b00cbdd4f3e066d48b9afe3231e0b216fbc9a7869260143ac`；同命令加 `-- backend frontend`＝**188 路徑**（126 `M`＋62 `??`）sha256 `3010b711e110c4628b372c8d918379728afa19ac04acf991604f0085b8b4f709` | **同一棵樹的兩個 scope**，不是兩個樹狀態。lane 用的是**不帶路徑參數**的那個（`run_tests.sh:386`）。⇒ 第十一輪 §八 的「211」與 §八補 的「126＋62＝188」都寫了數字但沒寫 scope，讀者會把它們當成互相矛盾；本輪補上標註，並記錄兩個 sha 不同是 scope 不同所致 |
| **實際受測內容（候選身份）** | `backend/**/*.py` digest `5d9dff9c…`（`run_tests.sh:345-350`）；本輪現測工作樹 backend 有 **144** 個髒路徑、frontend **44** 個 | digest 只看 `backend` 底下的 `.py`（含未追蹤測試）。後果逐條：① 第十一輪那顆綠灯為 **144 個 backend 路徑**的 `.py` 內容背書，**不為** frontend 那 44 個背書（前端只有 §十／第十一輪 §十 的 node-unit 層，兩層不可互換）；② `alethra-evidence/**`、`docs/**`、根目錄 `.sh`、本檔改動都**不移動** digest；③ 144 減 D 包 backend 138 的差 **6** 就是 §八補 那六個刻意未入包的未追蹤路徑（兩份釘選帳本＋兩份 `DECISION_LINEAGE_*.md`＋`.env.test.example`＋ 0 byte 的 `backend/postgres`）⇒ 加性自洽 |

#### 1.3 獲准基準 A（3,548 列）如今**只剩紀錄**，原件全部不在

五項現測，逐項附命令與 exit（`negative-proofs.txt`）：

1. 兩份受控檔**從未進過 git**：`git log --all --oneline -- backend/tests/collected_nodes.txt backend/tests/skip_budget.json` → **stdout 0 bytes**（任何 ref 都沒有）；`git show HEAD:` 兩者都 **exit 128**、`exists on disk, but not in 'HEAD'`。⇒ **不存在**「獲准基準的那個 commit」可以 checkout 回來；基準只能以「本地檔＋批准紀錄」證明，而本地檔已被 13:53 那次 `pin` 覆寫（現值＝P）。
2. **P** 自己的來源 run `/var/folders/…/alethra-runs/sqlite-20261119T051257Z-80770` → `isdir` **False**（該 run 的 `collect.txt`／`junit.xml`／`identity.txt` 隨之消失，只剩 `skip_budget.json` 裡那些 sha 字串當指紋）。
3. **A** 的來源 run `/tmp/alethra-test-logs/final-20261117/run` → **不存在**。
4. 现存 `/var/folders/…/alethra-runs/` 只有 **8** 個 run 目錄，全部在 19:13 HKT 之後；其中最大的兩個整套 collect 是 **3,836**（`111430Z-79057`＝本輪 SIGKILL 偏離那支，樹 `3fbb810e…`、`DATA_PROVIDER=<unset>`、**無** `junit.xml`／`exit-code.txt`）與 **3,837**（`113033Z-87893`＝第十一輪正式 run）。**沒有 3,548，也沒有 3,811。**
5. 全 repo 行數正好 **3,548** 的檔案 → **none**（`.venv`／`node_modules`／`.git`／`pgdata-16`／`pgvenv`／`.next` 已排除）。

⇒ 「A 的逐節點集合」**不可重測**，所以 owner 要的「逐節點新增／移除」對 **A→P** 這一段交不出來（不是沒做，是原件不在）。能交的只有紀錄逐字（已存檔 `approved-baseline-A3548-attestation.txt`）：

- `EXECUTION_TABLE_20261117.md:1484`：`collected=3548 total=183 pg_only=164 live_stack=18 other=1`（那是 A 時代的 ceiling 原值）。
- `:2122`：13:53 之前 `collected_nodes.txt` 仍是 **3,548 列**、provenance run `/tmp/…final-20261117/run`、`collected-node-ids: 3548`、樹 `72dcb3d1…`；13:53 那次 `pin` **自己印出** `-11 +274`，`-11` 歸因 4 檔（`test_learning_scheduler.py` 8／`test_learning_api.py` 1／`test_personal_model_comparison.py` 1／`test_personal_model_permission_matrix.py` 1，class 式節點被並行線改寫成函式式），`+274` 分散 22 檔（最大 `test_daily_model_estimate.py` 40／`test_simulation_daily_tick.py` 37／`test_learning_scheduler.py` 28／`test_decision_lineage.py` 26）。
- 那段 diff 的**輸出從未存檔**：本輪在 `alethra-evidence/` 全-tree 找 13:40–14:20 之間寫入的檔案，只有那兩份受控檔與一支 `.pyc` ⇒ `pin` 印出的 274 個名字一支都不在。

#### 1.4 三方並列（計數全現測；**逐節點** skip 差異在 A／P 兩側本質上交不出）

| 狀態 | collected | total skip | pg_only | live_stack | other | 身份（樹 digest／porcelain／run） | 證明的種類 |
|---|---|---|---|---|---|---|---|
| **A**＝獲准基準（3,548 時代） | 3,548 | 183 | 164 | 18 | 1 | 樹 `72dcb3d1…`；run `/tmp/…final-20261117/run`（已消失） | **僅紀錄**（`:1484`＋`:2122`），節點集合不可重測 |
| **P**＝現有釘選 | 3,811 | 179 | 159 | 18 | 2 | 樹 `50130606…`；porcelain 206 路徑 `da75ac96…`；run `sqlite-…051257Z-80770`（已消失）；`collected_nodes.txt` sha `5260ece2…`、`skip_budget.json` sha `c653b1a7…`（兩者皆 `??` 未追蹤） | 現檔＋逐字副本 `repin-round10/pinned-3811.verbatim.txt` |
| **C**＝第十一輪受測候選 | 3,837 | 179 | 159 | 18 | 2 | 樹 `5d9dff9c…`；porcelain 211 路徑 `8c3a8900…`；run `sqlite-…113033Z-87893`；`exit-code.txt`＝0 | `full-suite-round11/` 八份實物＋本輪 `skip_budget.py check`（rc **2**，輸出逐字在 `skip-census-three-way.txt`） |

三段差值，每段標明可不可測（**這是本節的要害**）：

| 段 | 逐節點 diff | skip 分類差 | 可测性 |
|---|---|---|---|
| **A→P**（13:53 那次 `pin`） | **`-11 +274`**（`-11`：上列 4 檔 8／1／1／1；`+274`：22 檔） | pg_only **164→159＝−5**、live_stack **18→18＝0**、other **1→2＝+1**、total **183→179＝−4**（−5＋0＋1＝−4，加性自洽） | **紀錄引文**。原件（A 的節點清單、那次 `pin` 印出的名字清單、該 run 目錄）三者皆不在 ⇒ 不可重測，引用時須標「依 `:2122`」 |
| **P→C**（第十一輪整套） | **`-0 +26`**（26 支全名與逐支結果見 §1.5） | pg_only 159→159＝**0**、live_stack 18→18＝**0**、other 2→2＝**0**、total 179→179＝**0** | **可測**，本輪三重獨立復算一致（見 §1.5） |
| **A→C**（有人把兩段相加時） | `-11 +300` | −5／0／＋1／−4 | **算術推定，不是量測**：274＋26＝300 只在「兩段新增互不重疊」的假設下成立，而 A 的集合不可取回，**無從驗證**。引用這三個數任何一個都必須寫「推定」 |

**必須講明的邊界**：`skip_budget.json` 只存**分類計數**（`total`／`pg_only`／`live_stack`／`other`），不存「哪一支被跳過」（現檔欄位實測：`collected`／`live_stack`／`other`／`pg_only`／`provenance`／`total`，沒有節點清單）。因此 owner 要的「各類 skip 差異」在 A／P／C 三方只能給**計數**；**逐節點**的 skip 差異只有 C 一側存在（從 `junit.xml`＋`pytest.log` 可重建 179 支的跳過理由，本輪已重建到 item 級 176＋collection 級 3），P 與 A 兩側的原始 log 都已消失。

#### 1.5 唯一可量測那段：`P→C = -0 +26`，三重復算（各自獨立，可逐條重跑）

1. **工具自己算**（`run_evidence.py` 的 `manifest_ids()`／`diff_block()`，非手抄）：`selection diff vs pinned: -0 +26`、**acknowledgement digest `7851af44bfbd9e665f8e7282ca301ab370b7bef73505d5a87d7121b1ff5e69b4`**、預演 **rc＝2**（原文與 26 支全名在 `full-suite-round11/pin-preview-dryrun.txt`，第十一輪已存，sha `7a6e409b…`）。
2. **節點 id 集合差**：以 lane 自己的正則（`run_tests.sh:378` `^[^[:space:]]+\.py(::.*)+$`）抽 `collected_nodes.txt`（**3,811**）與 `full-suite-round11/collect.txt`（**3,837**）⇒ `C−P`＝**26**、`P−C`＝**0**，且 `C−P` 與工具印出的 26 支**逐字相同**（`selection-diff-P-to-C.txt` 第 8 列 `True`）。注意此正則會纳入 parametrize 值含 `://` 的 6 支 id（`test_candidate_passwordless.py::…[postgresql+psycopg2://…]` 等）；把它們用「不含 `://`」之類的直覺 filt 掉會少算 6 支，本輪第一次就踩到了，已按 lane 原式重算。
3. **junit 回讀**：26 支在 `junit.xml` 全部命中，結果 **全 `passed`**；junit item 數＝**3,837**（與 collect 一對一），`passed` **3,661**＋item 級 `skipped` **176**＋collection 級 **3**＝**179**（與 §五 那顆燈逐字相符）。

逐檔：**16** 支 `tests/test_daily_estimate_two_workers.py`（A6 判準機：14 支變造樣本＋1 支正向對照＋1 支覆蓋閘）、**5** 支 `tests/test_daily_model_estimate.py`、**5** 支 `tests/test_manual_decision_books_linked_portfolio.py::TestLineageTravelsFromSubmitToExport`；**刪除 0 支**。

⇒ 客戶可读的結論：第十一輪**沒有讓任何既有節點消失**（`-0`）、**沒有动任何跳過理由**（四項分類差皆 0）、新增的 26 支全在 sqlite 層真的跑過並通過。整套 lane 之所以不是綠，就是這 26 支**還沒進釘選帳本**（§五 的兩道拒絕）。

#### 1.6 「不可將 +26 當成從獲准基準起算，亦不可用 ack digest 追認前次寫入」——落成可檢查的三句話

1. `7851af44…` 是**「P 的 3,811 列 vs C 的 3,837 支」那段 diff 正文**的雜湊。A 的集合在算它時**不在場**（§1.3 五項都在說明這件事），所以它**不含** A→P 那 263 列的任何資訊。拿它去 `--ack-diff` 只能證明「讀過這 26 支」，證明不了「讀過 13:53 那次 `-11 +274`」。
2. `cmd_pin` 只比對**當下算出**的 digest（`run_evidence.py:503-521`：`ack_diff != digest` ⇒ 先 `return 2`，不寫檔），它不讀 git、不讀舊 run、也沒有「補簽」入口 ⇒ **結構上無法**用舊 digest 追認前次寫入。前次寫入（13:53 把 3,548 覆寫成 3,811）的可追認性已隨原件消失，此點只能由 owner 以**紀錄**裁決，不能由任何 digest 代替。
3. 因此「只有新重釘內容得到獨立批准，才用獲准基準重新產生對應操作」在本機的**唯一可行讀法**是：基準＝**一次新的、ledger 閉合的整套 lane run**（不是 A，A 取不回來），批准正文＝`pin <新 run dir> --ack-diff <該次 diff digest>`，並把該次 diff 的**逐節點名字**存檔進 `alethra-evidence/`＋在本檔逐字列出。本輪**未**執行（未重釘）；第十一輪 §十一 那張卡就是這段話的具體操作，其數字（`-0 +26`／`7851af44…`）本輪復算未變，仍是唯一一份「現在就能批」的重釘正文。

#### 1.7 本輪落地的隔離實物（產生審閱資料 ≠ 任何執行）

`python3 alethra-evidence/tools/build_baseline_reconciliation.py --out alethra-evidence/baseline-round12`（產生器在 `alethra-evidence/tools/`，**不在** `backend/**/*.py` ⇒ 候選中性；實測前＝後＝`5d9dff9c…`，且兩份受控檔 sha 前＝後 ⇒ 本包**未**寫它們）：

| 檔 | 內容 | sha256 |
|---|---|---|
| `MANIFEST.json` | 三方計數（含 `node_set_recoverable`／`measurable_now` 標記）、26 支全名＋逐支 junit 結果、受控檔前後 sha、`untracked_in_git: true` | `cdffb71c43f88b14619b7bd7594fcb13647f04c4ff3033306fcc99b15a5ed4b0` |
| `selection-diff-P-to-C.txt` | §1.5 三重復算原文（工具三行＋集合差＋逐檔分組＋26 支逐支 outcome＋「removed: none」） | `b768719f9103a451e52135818b68ffc4a168a3bc35a75167998f4a5db3915952` |
| `skip-census-three-way.txt` | §1.4 那張計數表的機產版＋`skip_budget.py check` 逐字輸出（rc 2）＋176／3 分解 | `74434b918830fee8d8ed86c5d0c6d32fed0a5fcfd104e7117625cad6f4a22c88` |
| `negative-proofs.txt` | §1.3 五項，逐項附命令與 exit code | `66ccf524de79317c0887a380f24f343300d7dbd69c90dc9eed3adb854827ed67` |
| `approved-baseline-A3548-attestation.txt` | `:1484`／`:2122` 兩行**逐字**引用（含行號） | （見 `SHA256SUMS.txt`） |
| `pin-P3811.verbatim.txt`／`pin-skip-budget-P.verbatim.json` | 兩份受控檔的唯讀副本 | `5260ece2…`／同 `c653b1a7…` |
| `tested-C3837.node-ids.txt`／`tested-C3837.identity.txt` | 受測集合（lane 正則抽取＋排序，3,837 行）＋該 run identity 逐字 | `e6f250e2b1d1d1673e4e40ddc41b9d668f5f1dcf0df74c00ac624dd595f7f7f8` |
| `tree-scope.txt` | HEAD、digest、兩種 porcelain scope（`211` vs `188` 的由來） | `0fb51b8cdb605259061fa27b4a30f78ad9673d7ee699967059820e15f1a7c12f` |
| `SHA256SUMS.txt` | 11 檔；`shasum -a 256 -c` → 非 OK 行 **0** | `7cbda26a0cdbefdd81c1d1d12c1acbf0a715773735111d126a8265d7153084b6` |

本輪**未**執行：`pin`（任何變體）、`git apply`／stage／commit／push、PG 初始化、migration、服務／瀏覽器啟動、外呼。本節**只**新增 `alethra-evidence/baseline-round12/`（12 檔）與 `alethra-evidence/tools/build_baseline_reconciliation.py`。

---

## 第十二輪（2026-11-19）§二：客戶結果——「已保存的失敗」在重載後讀得到（旅程一欠的那件工程，已做並量完）

本節是提示裡「完成兩條客戶旅程仍欠的可做工程」的第一件：「將已保存的失敗資料以安全且可讀的方式在重載後呈現；沒有可靠技能歸屬就不要猜，也不暴露原始內部例外」。**沒有**引入按年齡修帳、自動接管或 durable lease；**行程級中斷仍為 NOT RUN**（那屬 B／C 包，未批准）。

### 2.1 缺陷原本長什麼樣（源碼現讀）

每日估計 job 的 `result_json` 只有一個欄位 `errors`，其值是**字串** `f"{skill_id}/{instrument_id}: {exc}"`（`app/services/daily_estimates.py:1063`，所屬 except 分支 `:1061-1073`）。面板與狀態端點過去只用它計數、從不還原成「哪一支技能、哪一個標的、為什麼」，所以**重新整理後失敗消失**，只剩「執行狀態未確認」。四個**已釘選**節點把 `errors` 當作逐字字串陣列在讀，因此**這個欄位一個位元組都不能動**。

### 2.2 修了什麼（四檔、逐檔 file:line）

| 檔 | Git | 動作 |
|---|---|---|
| `backend/app/services/daily_estimates.py` | `??`（未追蹤，66,129 bytes） | 在 `errors` 旁**新增**結構化 `failures: List[dict]`（宣告 `:923`），except 分支同時追加兩份（`failures.append` `:1064-1070`，其 `"reason": "engine_failure"` `:1068`）；`result_json` 尾部变成 `"errors": errors, "failures": failures,`（`:1114`）；新函式 `_failures_for_skill(result, skill_id)`（`:1306`）**先讀結構化 `failures`**（`:1322`），讀不到時才從 `errors` 字串前綴**具名歸因**，自我標記 `attributed_from`（該條目的 `"reason"` 在 `:1342`、`"attributed_from": "error_string_prefix"` 在 `:1343`）；狀態梯子把 `failed_here` 併入閘門，`last_failure = {**failed_here[0], "count": len(failed_here)}`（`:1438`），payload 新增 `"last_failure"`（`:1518`）。`errors` **逐字保留**。 |
| `backend/app/api/personalization.py` | `M` | `_DAILY_ESTIMATE_MESSAGES_ZH` 新增 `"engine_failure": "這個標的的每日估計在推論時失敗，這次沒有留下估計結果。"`（`:274`）；`_daily_estimate_status` 從 payload 取 `last_failure`（`:511`）並翻成 `failure["message_zh"]`（`:513`）。**內部例外文字不在 translation 表內**，因此不會外洩。 |
| `frontend/src/lib/dailyEstimate.ts` | `??` | `FailureRow`（`:112`，含 `attributed_from?: string` `:124`）＋唯一持有文案的 `failureNotice(failure)`（`:136`，`:142` 依 `attributed_from` 決定措辭）。 |
| `frontend/src/components/training/DailyEstimatePanel.tsx` | `??` | `SkillState.last_failure: FailureRow \| null`（`:88`）；渲染塊 `state.last_failure && …`（`:501-509`）以 `data-testid="daily-estimate-failure-<skill_unit_id>"`（`:503`）顯示 `failureNotice(...)`＋`reason`（`:506-507`）。面板**不自行拼字**。 |

### 2.3 客戶在畫面上讀到什麼（按語言契約，非宣稱）

同一視窗按第二次「立即估計」後，若其中一支標的推論爆炸：**201 當下**該卡片顯示「這個標的的每日估計在推論時失敗，這次沒有留下估計結果。（engine_failure）」；**重新整理／重載後**同一張卡仍然顯示同一句，因為它讀的是 `GET …/daily-estimate/status` 的 `last_failure`（帳本＋job `result_json` 的持久欄位），而不是那次 HTTP 回應。若失敗發生在 `failures` 欄位存在之前寫下的舊列，措辭會落在「由 `errors` 字串前綴歸因」那一支，且 `attributed_from` 自己說明它是推斷而來——**不猜不屬於自己的技能**：歸因只取該字串自己的 `skill_id` 前綴，其餘標的不被貼上這筆失敗。

### 2.4 量到的證據（全部本輪實測，非引用）

| 項目 | 命令（可重跑） | 實測結果 |
|---|---|---|
| 旅程一後端節點 | `cd backend && .venv/bin/python -m pytest tests/test_daily_model_estimate.py -q` | **47 passed**，rc 0，40.77s |
| 兩支**新**節點 | `tests/test_daily_model_estimate.py::test_a_saved_failure_is_named_on_a_reloaded_card_without_its_exception_text`（定義 `:2341`）、`::test_a_row_written_before_the_failure_field_is_attributed_without_guessing`（`:2409`） | 兩支 pass；`grep` 對 `collected_nodes.txt` 回 **0 列** ⇒ 二者在釘選集之外（§1.3 的 `+28` 分解中的 2 支） |
| 依賴閉包 93 節點 | `tests/test_daily_model_estimate.py`(47) ＋ `tests/test_daily_estimate_two_workers.py`(18) ＋ `tests/test_manual_decision_books_linked_portfolio.py`(28) | **92 passed / 1 skipped / 0 failed**，57.29s，`DATA_PROVIDER=demo`；那 1 支跳過的具名理由逐字＝需要真實 PostgreSQL（未起 PG，屬預期） |
| 前端單元 | `cd frontend && ~/.local/bin/node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyEstimate.test.mjs` | **38 / 38 pass**；新增三支為 `:875`（`failureNotice` 只講它收到的欄位）、`:928`（面板把已保存失敗渲染成可定址節點）、`:944`（服務寫 `failures`、門面翻 `message_zh`） |
| 候选摘要 | `run_tests.sh:345-350` 的 `tree_digest()` 逐字重跑 | **`bd5b844be388cfdcd5f2c79d652fbf27ee476c719712dc4a6f0e9517f3e00194`** |

### 2.5 必須一起讀的三條邊界

1. **候選已移動**：第十一輪的整套 green 綁的是 `5d9dff9c…`；本輪的兩支新測試把摘要推到 `bd5b844b…`。依提示「不重跑無變更全套」，本輪以**檔案級＋93 節點依賴閉包**驗證新候選，**沒有**重跑整套、**沒有**重釘。因此本節的證據等級是 ①離線讀碼＋④sqlite／⑤HTTP-in-process（TestClient），**不是**整套 green，也**不是** browser。
2. **既有釘選四支**讀 `errors` 的斷言**未被鬆動**：`errors` 欄位與格式逐字保留（§2.2），新行為走新欄位。
3. **副作用須揭露（本輪實測計數）**：`frontend/tests/dailyEstimate.test.mjs:692-694` 以正規式 `/"reason": "([a-z_]+)"/g` 從 `backend/app/services/daily_estimates.py` 掃 `REFUSAL_REASONS`。同一支正規式，第十一輪快照（`alethra-evidence/d-patch-round11-full/untracked/backend__app__services__daily_estimates.py`，md5 `fa3d08623fcbb4239361ef0232465cfc`，19:40）掃出 **5** 支，本輪工作樹掃出 **6** 支，多出的那支正是本輪新增的 `engine_failure`。斷言逐字未動：`REFUSAL_REASONS.length >= 5`（下限、非等號）、`includes(ALREADY)`、`otherReason` 存在（`:699-701`）⇒ **未放寬**；本輪前端 38/38 PASS 是「放寬與否」的實測依據。

## §三：#177 的 API 契約——逐技能 selection 優先於 record alias（獨立可審的語義變更）

提示要求把它「列為**獨立可審的語義變更**，但保留在整體工作包」，並「交實際 diff、依賴與對照測試」，且「不靜默擴權」「不要藉 #177 改一般按需推論的扣額／去重」。本節全部為本輪實測。

### 3.1 變更了什麼**語義**（一句話）

`/records/{record_id}/predict` 過去在 `version_id` 缺席時**直接**取 `record.active_version_id`（舊行 `alias_used = version_id is None`／`target_id = version_id or record.active_version_id`）。現在它**先問這一個技能帳號正在用哪個版本**，record alias 降為「從未被指定過的帳號」的回退，而 gate 對那樣的帳號**拒絕**而不是順手放行——**別名從憑證降為狀態列**。

### 3.2 實際 diff（檔、hunk 數、增刪）

| 檔 | Git | hunks | ＋ | − | 關鍵 hunk |
|---|---|---|---|---|---|
| `backend/app/api/personal_models.py` | `M` | **28** | **180** | **19** | `@@ -685,8 +769,22 @@ def predict_with_record_alias`（優先順序本體）；`@@ -663,19 +746,20 @@ def predict_with_version`；`@@ -708,9 +806,9 @@`（`via=_via(request)`／`resolved_from=`）；`@@ -732,11 +830,15 @@ class SelectionRequest` 與 `@@ -1077,7 +1199,12 @@ def rollback_personal_model`（兩個 request 模型新增 `skill_unit_id: Optional[uuid.UUID] = None`）；`@@ -1121,25 +1250,57 @@`（scoped `GET /selections`）；函式改名 `predict_with_record_alias` → `predict_with_version_in_use`（patch 內 `-`／`+` 兩行） |
| `backend/app/api/schemas.py` | `M` | 7 | 105 | 1 | `PredictRequest.skill_unit_id: uuid.UUID = Field(...)`（**必填**，無預設值） |
| `backend/app/services/personal_selection.py` | `M` | 7 | 155 | 19 | 逐技能寫路與 `_assert_expected_in_use` |
| `backend/app/services/model_activation.py` | `M` | 8 | 373 | 3 | gate 的 `reason_code` 集合 |
| `backend/app/models/personal_model_selection.py` | `M` | — | **3** | **0** | `skill_unit_id = Column(UUID(as_uuid=True), nullable=True)`（`:72`） |
| 對照測試三支 | `??` | — | 全位元組 | — | 見 3.4，都在 D 包 `untracked/` 內 |

**回應裡回報「實際版本＋來源」**：`_run_personal_prediction` 的 `resolved_version.resolved_from`（`app/api/personal_models.py:705`），取值為 `explicit_version_id`／`skill_unit_selection`／`record_active_alias`（`:773`／`:784`／`:787`）／`daily_estimate_selection`（`app/services/daily_estimates.py:814`，每日估計那條路）。舊欄位 `resolved_from_active_alias: bool`（patch `-` 行）已被這個四值字串**取代**——這是**破壞性欄位變更**，必須在發布範圍裡單列。

### 3.3 依賴閉包（這一條語義要跑起來所靠的東西）

`latest_skill_selection(db, owner_id, record.id, skill_unit_id)`＝`app/services/personal_selection.py:140`，以 `PersonalModelSelection.skill_unit_id == skill_unit_id`（`:152`）過濾；`latest_alias_selection`（`:162`）用 `skill_unit_id IS NULL`（`:176`）**且註明「NULL 是 scope，不是 wildcard」**。Gate `check_model_version_inference_authorization`＝`app/services/model_activation.py:181`，拒絕碼：`no_training_job`（`:223`）、`invalid_training_job_id`（`:233`）、`version_not_found`（`:247`）、`no_skill_unit_selection`（`:269`）、`superseded_skill_unit_selection`（`:277`）；由 `_require_inference_authorization`（`personal_models.py:168-192`）轉成 403，**整個 verdict 放在 `context` 回給客戶**，不必讀伺服器 log。憑證來源標記走 `_via`（`:129-131`）。

### 3.4 六種情境 × 對照測試（**28 支 node id 全部本輪實跑，28 passed，rc 0，27.62s**）

命令：`cd backend && .venv/bin/python -m pytest tests/test_formal_skill_unit_authorization.py tests/test_formal_skill_unit_authorization_endpoint.py tests/test_successful_inference.py -q`。**28／28 都在釘選的 3,811 列內**（`comm -23` 對 `collected_nodes.txt` 回 **0 列**）⇒ 這組覆蓋**不需要重釘**。

| 情境 | 節點（檔::名，定義行） |
|---|---|
| ① 明確指定版本 | `test_successful_inference.py::test_a_superseded_version_is_refused_when_named_and_admitted_when_current`（`:713`）、`::test_a_worker_shaped_call_answers_the_same_as_the_owner_button`（`:643`）；`test_formal_skill_unit_authorization_endpoint.py::test_each_skill_gets_its_own_version_from_the_unnamed_predict_route`（`:282`） |
| ② 該技能有有效 selection | `test_formal_skill_unit_authorization.py::test_a_version_selected_for_two_skills_serves_both`（`:55`）、`::test_switching_one_skill_leaves_the_other_on_its_own_version`（`:116`）；endpoint `::test_two_skills_of_one_owner_switch_independently`（`:227`） |
| ③ 該技能從未 selection | `test_formal_skill_unit_authorization.py::test_a_skill_that_never_selected_is_refused`（`:69`）；endpoint `::test_a_skill_that_never_selected_cannot_infer`（`:160`，斷 `no_skill_unit_selection`） |
| ④ selection 失效／被取代 | `test_formal_skill_unit_authorization.py::test_a_superseded_version_stops_being_an_authorization`（`:96`）；endpoint `::test_a_superseded_version_stays_readable_evidence`（`:385`）；`test_successful_inference.py::test_a_superseded_version_is_refused_when_named_and_admitted_when_current`（`:713`） |
| ⑤ 跨技能／跨帳號 | `test_formal_skill_unit_authorization.py::test_a_record_alias_selection_authorizes_no_skill`（`:76`）；endpoint `::test_moving_the_record_alias_authorizes_no_skill`（`:177`）、`::test_a_foreign_skill_account_is_refused_and_leaves_no_row`（`:203`，404＋**零列殘留**） |
| ⑥ 回報實際版本與來源 | endpoint `::test_each_skill_gets_its_own_version_from_the_unnamed_predict_route`（`:282`，斷 `resolved_version.resolved_from == "skill_unit_selection"`）、`::test_selection_history_labels_the_scope_of_every_row`（`:300`）、`::test_rollback_returns_one_skill_and_leaves_the_other_on_its_version`（`:332`） |
| 附：按需推論主幹未被動 | `test_successful_inference.py::test_successful_inference_through_formal_endpoint`（`:393`）、`::test_inference_id_is_consumable_by_decision_lineage`（`:440`）、`::test_inference_refused_for_a_different_skill_unit`（`:500`）、`::test_inference_refused_when_training_job_not_completed`（`:521`）、`::test_inference_refused_on_artifact_checksum_drift`（`:543`）、`::test_inference_refused_for_other_owners_training_job`（`:563`）、`::test_personal_authorization_does_not_require_agent_activation`（`:583`）、`::test_a_worker_shaped_call_cannot_reach_a_version_the_skill_did_not_select`（`:688`）、`::test_an_inference_the_service_ran_is_a_valid_decision_source`（`:732`）、`::test_a_recorded_decision_is_not_a_trade_journal_row`（`:759`） |

### 3.5 四格**尚無對照測試**（MISSING，實測 0 命中，不寫成 PASS）

對 182 檔 D 包與 `backend/tests/` 全目錄以 Grep 工具（非 zsh glob）查證：

1. **record 門帶明確 `?version_id=`**：`personal_models.py:766` 接受該 query 參數，但 `grep -E 'predict.*\?version_id|/predict"[^\n]*version_id' tests/` ＝ **0 命中**；`params={"...": ...}` 形式的 `version_id` 只出現在 `strategy_version_id`（agent 路線，`test_agent_strategy_slice.py:1476` 等 5 處）。⇒ 該分支**未經測試**。
2. **409 `no_active_version`**（`:788-795`）：全 `backend/tests` 對 `no_active_version` ＝ **0 命中**（該字串只存在於產品碼）。
3. **`record_active_alias` 這個標籤不可觀察**：全測試對 `record_active_alias` ＝ **0 命中**；且 ③／⑤ 已證明走到這條分派時 gate 會拒（`no_skill_unit_selection`），所以**没有任何 200 回應會帶著這個值**——它是寫進 refusal 前就被擋下的中間態。誠實結論：**不可宣稱「四值都會在回應裡出現」**。
4. **gate 的 `version_not_found`（`model_activation.py:247`）與 `daily_estimate_selection`**：測試目錄對 `daily_estimate_selection` ＝ **0 命中**；`version_not_found` 的 404 命中全在**決策血緣／比對門**（`test_decision_lineage.py:728`／`:739`／`:886-887`、`test_personal_comparison_validity.py:714`），**不是**推論 gate。⇒ 两支拒碼在推論路徑上**未测**。

### 3.6 「不靜默擴權」與「不改扣額／去重」的核對

- **不靜默擴權**：回退一律以**拒絕**收尾。③／⑤ 的斷言值是 403＋具名 `reason_code`；`test_a_foreign_skill_account_is_refused_and_leaves_no_row`  additionally 斷**零列殘留**。`skill_unit_id` 在 `PredictRequest` 是**必填**（`app/api/personal_models.py:121`，`Field(...)` **無預設值**；patch hunk `@@ -109,6 +114,16 @@` 新增該欄），缺席由 422 擋在門外（`test_predict_without_a_skill_unit_is_refused_as_an_invalid_request`，`:149`）。alias 的 NULL 是 scope 而非 wildcard（`personal_selection.py:176` 註明）。
- **扣額／去重零變動（實測）**：對 `d-patch-round12/tracked/backend__app__api__personal_models.py.patch` 的**增刪行**做 quota 詞表掃描（`reserve_quota`｜`ai_usage`｜`settle_reservation`｜`release_reservation`｜`UsageRecord`｜`usage_records`）＝ **0 行**。產品碼裡這些呼叫仍在原位：`reserve_quota` `:627`、`release_reservation` `:631`／`:641`／`:645`／`:649`、`settle_reservation` `:659`、`record_api_call_no_commit` `:663`。⇒ #177 的 diff **沒有碰到**按需推論的扣額與去重，符合「不要藉 #177 改一般按需推論的扣額／去重」。
- **它仍在整體工作包裡**：28 hunks 與五檔變更**未**被拆成獨立提交、**未** stage／commit／push；§3.2 那张表就是 owner 單獨否決這一條語義時要 revert 的準確範圍（hunk 級，非整檔還原）。

## §四：OrderPreviewPanel **是否屬承諾範圍**——本輪把「承諾入口」與「面板門」分開量

提示：「釐清 OrderPreviewPanel 是否屬承諾範圍；若屬，接通使用者明選的合法來源，以持久化、讀回、修正及適用匯出驗證，不把 execution intent 混作 paper decision。」

### 4.1 承諾的是哪一道門

`EXECUTION_TABLE_20261117.md:1882` 那一列（旅程表「記一筆有血緣的決策」）承諾的入口＝`POST /api/v1/paper-portfolio/{portfolio_id}/decisions` 帶 `linked_inference_id`（`backend/app/api/paper_portfolio.py:956`），**不是**工作區面板。該列〔第九輪更正〕已寫明：**模擬頁面接通了**，`OrderPreviewPanel.tsx:417-421` **仍未送該欄**。

### 4.2 本輪實測（面板送的**是另一道門**）

| 事實 | 值（本輪現測） |
|---|---|
| 面板 | `frontend/src/components/workspace/OrderPreviewPanel.tsx`，**55,894 bytes／1,304 行**，Git 髒狀態＝**不在 porcelain 清單（未修改）** |
| 面板送什麼 | `submitManual()`（`:417-428`）組 `{action, idempotency_key, rationale, quantity?, expected_version?}`，呼叫 `api.simulations.manualDecision(sessionId, payload)` |
| 面板**不**送什麼 | `grep -c linked_inference_id`＝**0**；`inference_id`＝**0**；`lineage`＝**0**；`estimateSource`＝**0**；`skill_unit_id`＝**0** ⇒ 面板**不持有任何可選來源控件** |
| 那條門 | `src/lib/api.ts:433-445` ⇒ `POST /api/v1/simulations/{sessionId}/manual-decision`（`app/api/simulations.py:2295-2302`，schema `schemas.py:608`／`:619`）；**client 型別已有 `linked_inference_id?: string \| null`**（`api.ts:433-441`）⇒ **面板不傳，不是 client 不能傳** |
| 已接通那一側 | `src/app/experiments/[id]/simulation/page.tsx` `grep -c linked_inference_id`＝**5**，`:505-515` 在 `lineageState(...) === 'attached'` 時送 `estimateSource.inference_id` |
| 既有測試 | `backend/tests/test_manual_decision_books_linked_portfolio.py` `grep -c linked_inference_id`＝**46**（A7 三支属該檔，未跑） |
| ExecutionIntent（不得混作 paper decision） | 面板 `grep -n ExecutionIntent`＝7 行：import `:16`、state `:160`、`setIntent(result as ExecutionIntent)` `:548`／`:557`／`:600`、`api.execution.submit(intent.intent_id)` `:568`、`api.execution.getIntent(...)` `:585`。它持有的是**執行意圖**（order／execution），**不是** `PaperDecision` |

### 4.3 關鍵產品發現：「接通」不是一行改動

`simulations.py:2485-2500`：同一道 sim 門對 **HOLD** 帶 `linked_inference_id` 的請求**主動 400 拒絕**，detail 具名 `inference_needs_decision_row`，並要求改用 `POST /api/v1/paper-portfolio/{portfolio_id}/decisions`（原文 `:2496`）。**Why**：HOLD 在 sim 門只寫 replay 列，沒有 paper decision 列可承載該欄。**How to apply**：面板接通必須**按 action 分流**（BUY／SELL → 可帶 sim 欄；HOLD → 須走 paper-portfolio decisions 門或老實拒），這是**新增產品面**，需 owner 裁定，不是兩行改動。

### 4.4 裁定卡（二案，**本輪未實作、未跑、未批准**）

| 案 | 內容 | 觸碰檔 | 若獲准的驗證 |
|---|---|---|---|
| **案一（不接通）** | 認定 `:1882` 承諾的入口是 paper-portfolio decisions 門，模擬頁面**已履行**該承諾；面板屬工作區 order preview，超出兩條旅程範圍 | **0 檔** | 無需新證據；以 §4.1／§4.2 現測結案，旅程二面板那格維持 **NOT RUN** |
| **案二（接通）** | 面板新增來源選取（僅列該 owner 名下合法的 inference），沿用 `lineageState()` 閘與 `estimateSource` 型別，按 action 分流（§4.3） | `OrderPreviewPanel.tsx`（`submitManual` 與 state）、可能需 `src/lib/api.ts` 增 paper-portfolio 呼叫；後端**無需改**（兩道門都已接受該欄）；測試檔 `test_manual_decision_books_linked_portfolio.py` 增面板旅程 | ①`TestClient` 層斷言 payload 帶欄且持久化；②讀回 `GET /paper-portfolio/{id}/decision-history` 與 `GET /api/v1/human-decisions` 同一 id；③修正／匯出三態 `inference_source_state` 斷言照旅程二判準；④**瀏覽器層仍 NOT RUN**，屬 C 包另行批准 |

**〔第十三輪，2026-11-20〕案二已實作，案一未被採納為結案方式**：提示明令「若屬，接通使用者明選的合法來源」，本輪因此走案二，且 §4.3 那道 HOLD 400 **沒有繞過**——面板沿用同一 `lineageState()` 派生，HOLD 不送該欄。§4.2 表中的 `grep -c`＝0 是第十二輪候選的量值，本輪現測見 §八；4.4 表右欄的①②③屬 TestClient 層，④瀏覽器層仍 **NOT RUN**。

**邊界（報告必附）**：不得把 `ExecutionIntent`（`:160`／`:568`／`:585`）當作 paper decision 寫入，亦不得繞過 §4.3 那道 400；「行程級中斷」在本輪仍為 **NOT RUN**。

## §五：把驗收包收斂成可審的**確切操作**（A／B／C）——第十輪 §六 表格仍是 Effective 正文，本節只換掉過期的值

提示要求：「A0 僅作既有授權的唯讀確認；列明是否會新寫證據檔。A1–A7 不執行。固定完整候選、空庫→head 血緣、執行目錄、有效 DSN 來源、角色、實際命令；展開每支 node id；保留 A6 實際 PG 等鎖與持久結算判準、足額測試配額及零未終態預留；不以時間戳、429 或 SQLite 樣本取代。A0 不碰既有實例。B 一個唯一隔離 DB、schema 準備、runtime 權限、一帳號、產物方法，交實際隔離設定來源與非秘密有效值，證明不載入現有 `.env` 的非預期設定，不印程序完整環境、不把秘密放進證據包；零外呼要有可強制或覆蓋整段的證據。C 單一建置／啟動模式、產物隔離、API base、DB／帳號；新增必要 e2e 規格屬工程準備，不等於獲准啟動瀏覽器；自然 cron、瀏覽器自動化、本人同意分開。」本節全部值皆為**本輪（2026-11-19）現測**。

### 5.1 固定值

| 項目 | 本輪現測值 |
|---|---|
| HEAD | `git rev-parse HEAD` ＝ `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` |
| 完整候選 | `bd5b844be388cfdcd5f2c79d652fbf27ee476c719712dc4a6f0e9517f3e00194`（§2.2；第十一輪 `5d9dff9c…` **已被本輪兩支新測試取代**） |
| PG 二進位 | `PGB=$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin`：`ls -1 \| wc -l` ＝ **32**；`initdb`／`psql`／`pg_isready`／`pg_ctl --version` **四者皆 16.2**（現測逐字 `initdb (PostgreSQL) 16.2`） |
| 執行目錄（尚未建立） | `RUN=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r12-run-XXXXXX")`；`PGDATA=${TMPDIR:-/tmp}/alethra-r12-pgdata`；`SOCKDIR=${TMPDIR:-/tmp}/alethra-r12-sock`（三者皆**新建**，不覆寫既有） |
| 環境前提（唯讀） | `pgrep -fl postgres` ＝ **無輸出**；`lsof -nP -iTCP:5432 -sTCP:LISTEN` ＝ **0 列**；`/tmp/.s.PGSQL.5432` ＝ **不存在**；`$PGDATA`／`$SOCKDIR` ＝ **不存在**；`lsof` 於 8000／8031／3031 ＝ 各 **0 列**；`pgrep -fl uvicorn` ＝ **無輸出** |
| migration 尾端 | `DATA_PROVIDER=demo .venv/bin/python scripts/check_migration_tail.py --json`（讀**檔案系統**的 ScriptDirectory，不連 DB）：`head=d4e5f6g7h8i9`、`parsed_heads=[d4e5f6g7h8i9]`（單一）、`expected_tail`＝`parsed_tail`＝`[f2a3b4c5d6e7, b2c3d4e5f6g7, c3d4e5f6g7h8, d4e5f6g7h8i9]`、`revision_count=57` |
| 受控檔 | `backend/tests/collected_nodes.txt` sha256 `5260ece2…`（**3,811 列**）、`backend/tests/skip_budget.json` sha256 `c653b1a7…`——本輪**未**動（§一） |

### 5.2 A0＝**既有授權的唯讀確認**：它會不會新寫證據檔（提示明文要求回答）

第十輪 A0 把 7 條結果寫進 `$RUN/identity.txt`，所以 A0 **不是**「純唯讀、零寫入」——它**會新寫 1 支檔**。故本輪把 A0 拆成可**單獨批准／單獨否決**的兩格：

| 格 | 動作（可執行值） | 是否新寫證據檔 | 判準 | 中止條件 |
|---|---|---|---|---|
| **A0a（零寫入）** | 七條唯讀：`pgrep -fl postgres`；`lsof -nP -iTCP:5432 -sTCP:LISTEN`；`ls -l /tmp/.s.PGSQL.5432`；`ls -d $PGDATA $SOCKDIR`；`"$PGB/initdb" --version`／`"$PGB/psql" --version`；`git rev-parse HEAD`；tree digest（§2.2 那條 `find\|xargs shasum\|shasum\|cut`） | **完全沒有**——stdout 只印終端 | 逐條與 §5.1 相同 | 5432 已被聆聽／socket 存在／`$PGDATA`、`$SOCKDIR` 已存在（**先回報，不覆寫、不刪除**）；版本非 16.2；HEAD 或 digest 不符 ⇒ **NOT RUN** |
| **A0b（寫入）** | 同 A0a 的輸出，另存進 `$RUN/identity.txt`（先 `mktemp -d` 建 `$RUN`） | **是，1 支新檔**（獨佔建立，不覆寫） | 讀回值與 §5.1 逐字相同 | 建立 `$RUN` 失敗 ⇒ NOT RUN |

**兩格皆不碰既有實例**：無 `initdb`、無 `pg_ctl start`、無 `CREATE ROLE`／`CREATE DATABASE`、無 `GRANT`／`REVOKE`、無 migration、無 54340 平行線、無既有服務停用或重用。

### 5.3 A1–A7：**本輪未執行**，逐格保留第十輪 §六 原判準，只更新值

| 格 | 本輪值（其餘逐字沿用第十輪） | 硬性要求（**不得**放寬） |
|---|---|---|
| A1 | 角色 `alethra_r10_bootstrap`／`-migrator`／`-app`；`$PGDATA`／`$SOCKDIR` ＝ §5.1 r12 路徑 | **只建叢集**：不建 DB、不建表；非零 ⇒ 停，**不重試第二次、不刪 PGDATA** |
| A2 | `pg_ctl -o "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB"` | `pg_isready` 回 ready；正常 `-m fast stop`；以 §5.1 抓到的 pid 用 `pgrep`＋`lsof` 核對已退出；**不以已停端點的 HTTP 回應證明停止；停止不等於刪資料** |
| A3 | 角色名維持 `alethra_r10_migrator`／`alethra_r10_app`（同時滿足 `test_safety_gate.py:26-30` 白名單與 `run_tests.sh:150` 的 `ALETHRA_TEST_REQUIRED_DB_PREFIX=alethra_`）；DB `alethra_owner_test` | 讀回 `pg_roles` 對 migrator／app 的 `rolsuper／rolcreatedb／rolcreaterole／rolbypassrls` **四旗標全 f**；`datdba=alethra_r10_migrator`；`REVOKE ALL ON SCHEMA public FROM PUBLIC` 是 PG15+ 上 alembic 能落地的**必要**授權 |
| A4 | 目標 head＝**`d4e5f6g7h8i9`**（§5.1 `--json` 現測：單一 head、`revision_count=57`、`expected_tail` 四支依序） | 空庫→head **全鏈**；`heads` 多於一／head 不符／`DUPLICATE_REVISION`｜`BRANCHED_TREE`｜`MISSING_DEPENDENCY` ⇒ 停；**不 `--autogen`、不 downgrade、不 drop、不跑任何其他 migration** |
| A5 | 兩欄**只有 index、無 FK**：`migrations/versions/c3d4e5f6g7h8_*.py:88-93`、`d4e5f6g7h8i9_*.py:85-89`；索引名 `ix_decision_records_linked_inference_id`、`ix_human_decision_events_linked_inference_id`；完整性由 `app/services/decision_lineage.py::validate_linked_inference` 於寫入前核對 | 出現 FK／欄位型別非 `uuid` ⇒ 停並回報（那不是「更安全」，是本體與 migration 不同步）；差異逐條列出，**不解釋成等價** |
| A6 | 節點 ID＝§5.5 三支 | 見 §5.5 |
| A7 | 節點 ID＝§5.5 三支 | 仍以 `row-lock serialisation requires a real PostgreSQL backend` 跳過 ⇒ DSN 沒生效，判 **NOT RUN** |

**本包唯一輸入** ＝ `ALETHRA_PG_TEST_URL`（形如 `postgresql://alethra_r10_migrator:<mktemp 密碼>@127.0.0.1:5432/alethra_owner_test`，由代理在 A2 之後於子 shell 內組出並 export）。**有效 DSN 來源**現讀 `run_tests.sh:141-150`：`MODE=pg` 且該變數為空 ⇒ 印 `refusing: pg layer needs ALETHRA_PG_TEST_URL (a disposable PostgreSQL database)` ＋ **exit 2**；非空 ⇒ **同一支 DSN 被覆蓋進 `DATABASE_URL`／`ALETHRA_TEST_DB`／`ALETHRA_PG_DATABASE_URL`／`ALETHRA_TEST_POSTGRES_URL` 四個變數**（`:146-149`）⇒ 是「程序環境覆蓋 `backend/.env`」，**不是相加**；缺它 pg 層直接 rc＝**2** 拒絕。

### 5.4 A6 的**實際 PG 等鎖**與**持久結算**判準（不以時間戳、429 或 SQLite 樣本取代）

同步機制現讀：`mp.get_context("spawn")`、`ctx.Barrier(2)`（`tests/test_daily_estimate_two_workers.py:218`／`:418`）、`_TIMEOUT_S = 60`（`:127`）、連線層 `SET lock_timeout='30s'`／`statement_timeout='120s'`／`idle_in_transaction_session_timeout='90s'`（`:189-191`）。**硬性前提**：測試帳號有效額度 **≥ 4**（扇出＝2 支 instrument × 2 行程；無 `PilotEntitlement` 列時走 free plan `ai_usage_limit=10`，`app/auth/entitlements.py:66-71`；有列時讀該欄、預設 20，`app/models/pilot.py:97`）。**B 包的 `ai_usage_limit=1` 不得套在 A6／A7**，否則額度會把「錯誤的第二次扣額」掩蓋成 429。判準以**帳本 row count** 為準：每 (owner, skill, instrument, window) 恰好 1 列 `metadata["via"]=="daily_service"`、每列恰好 1 筆 `settled` 預留、**非終態預留＝0**、落敗那支的 `learning_jobs.result_json.refused` 以 instrument id 具名 `already_estimated_this_window`。**兩個 job 摘要的 `billed_in_window` 不相加**（同一帳本的兩個瞬間），窗口總數只引用 row count。逾時／`Barrier` 未湊齊／斷言失敗 ⇒ 該次 **NOT RUN／INVALID**，不得寫成 PG 競態 PASS；現場保留給 owner，不刪 `market_data_bars`（無 owner 欄可 scope，屬共享種子）。

### 5.5 A6／A7 的**逐支 node id**（提示：展開）

```
tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier
tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once
tests/test_daily_model_estimate.py::test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_two_requests_on_one_idempotency_key_leave_exactly_one_order
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_stale_account_read_cannot_overwrite_a_committed_booking
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_manual_submit_racing_an_advance_stays_consistent
```
執行命令（A6 三支、A7 三支，各自 `./scripts/run_tests.sh pg <三支 node id>`，前置 `cd backend && . "$DSN" && export TEST_MODE=true DATA_PROVIDER=demo`）。

### 5.6 B 包：實際隔離設定來源＋非秘密有效值＋「不載入 `.env` 非預期設定」的證明＋可強制／覆蓋整段的零外呼證據

| 項目 | 本輪可執行值 |
|---|---|
| 隔離設定來源（**全部由程序環境**，不用 `backend/.env`） | `ENABLE_LEARNING_SCHEDULER=true`（欄位 `app/config.py:140` 預設 **false**）；`LEARNING_SCHEDULER_JOBS=daily_model_estimate`（`:148` 預設空字串；未設時走 `DEFAULT_LEARNING_SCHEDULER_LANES` `:55-59/:211`，該組**不含**估计 lane，且 set allow-list 時相鄰 lane 的 02:00／03:00 cron 會一起註冊 ⇒ 必須明確命名）；`LEARNING_SCHEDULER_MAX_OWNERS=1`（`:153` 預設 25；`<1` validator 拒設定）；`DATA_PROVIDER=demo`；`RESEARCH_AUTH_BYPASS_ENABLED=false`；`CANDIDATE_PASSWORDLESS_MODE=false`；`LIVE_TRADING_ENABLED=false`（`:172`）；`DATABASE_URL=<本包 DSN>` |
| 觸發時刻 | `CronTrigger(hour=5, minute=0, timezone="UTC")`（`app/workers/learning_scheduler.py:182-185`；常數 `app/services/daily_estimates.py:82`＝**5**，`SERVICE_TIMEZONE` `:80`＝`UTC`）。**2026-11-19 的 05:00 UTC 已過**（本輪量測 17:5x 之後）⇒ 最早 **2026-11-20 05:00 UTC ＝ 13:00 HKT**；不改系統時間、不手動 tick |
| **證明 `.env` 的非預期設定沒有被載入** | ①`app/config.py` 以 `env_file="backend/.env"` 讀值，**程序環境變數優先** ⇒ 上面那一整組以程序環境**覆蓋**；②讀回用**非秘密有效值**而非印環境：`registered_lane_ids()`（`learning_scheduler.py:191`）**只**回 `daily_model_estimate`、`GET /api/v1/admin/collection/status`（`app/api/admin.py:89`）的 `enabled`／`running` 均為假、`GET …/daily-estimate/status` 的視窗與帳本；③命名不認識的 lane 由 validator **拒設定**（`:320-344`）⇒ 設定來源錯誤會立刻顯形；④**不印程序完整環境**：秘密只做**存在性**檢查 `ps eww -p <pid>`，**只在 owner 面前做、不回顯值**；**不把秘密放進證據包**（`.env` 內含 `TWELVEDATA_API_KEY` 與 `DATA_PROVIDER=twelvedata` ⇒ `DATA_PROVIDER=demo` 的覆蓋**必須在讀回裡被看見**，不是假設生效） |
| 零外呼（**可強制／可覆蓋整段**的證據） | 進程啟動前後各一次 `lsof -nP -p "$(cat $RUN/service-pid.txt)" -a -i` ⇒ 除 `127.0.0.1` 之外的對端列數必須為 **0**（05:00 那一響前後**再做一次**，共四份存檔）；若要**可強制**的版本：以 `sandbox-exec -p '(version 1)(deny network*)'`（**只放行 loopback**）包住啟動命令，讓外呼在內核層被拒而非僅被觀測。本包**不安裝依賴** |
| 帳號／資料 | `POST /api/v1/auth/register`（`app/api/auth.py:31/:46`，`risk_acknowledged=true`）新建的**本輪測試 owner**＋`POST /api/v1/auth/login`（`:101`）；模型＝該測試 owner 名下 1 支種子個人模型版本；**不碰任何真實帳號**（不對既有使用者 `grant_daily_estimate`）；`RESEARCH_AUTH_BYPASS_ENABLED` **必須**覆蓋為 false，否則 `app/auth/jwt.py:51-61` 會把所有 loopback 請求當成「第一位 active user」，本包觀察到的就不是那個測試 owner |
| 額度（真上限） | 該測試 owner 的 `PilotEntitlement.ai_usage_limit = 1`（`app/models/pilot.py:97`）⇒「一天一筆」由額度**實際強制**（超出時 `reserve_quota` 429，`app/auth/entitlements.py:252-285`）。此上限證明可控成本，**不代替 A6**，且**不得**拿去當 A6 的額度 |
| 行程 | 啟動前 `pgrep -fl uvicorn` 必為 **0**（本輪 17:54:16 現測無 uvicorn）、`lsof -nP -iTCP:8031 -sTCP:LISTEN` **0 列**，進場仍重查；啟動後把**本輪抓到的 pid** 寫 `$RUN/service-pid.txt`；停止只 `kill -TERM` 自己那支 pid，以 `pgrep -f "uvicorn app.main:app --host 127.0.0.1 --port 8031"`＋`lsof` 核對；**不用已停端點的 HTTP 回應證明停止**；日誌落點以 `lsof -p <pid>` 實測確認（本輪前提：8000／8031／3031 各 **0 列**） |
| 判準（逐步） | ①響前 `learning_jobs` 無當日 window 列、`usage_records` 無 `via=daily_service` 當日列 ②響後 `learning_jobs` 有 `job_type=daily_model_estimate`（`daily_estimates.py:63`）且 `config_json.window=<當日 UTC 日>` ③`usage_records` 當日恰好 1 列 ④`GET …/daily-estimate/status` 的 `last_estimate.inference_id` 指回同一列 ⑤訂單數 0、`decision_records`／`human_decision_events` **不因排程增加** ⑥replay 進度不推進 ⑦「同窗不重複扣額」在本包只算**待觀察預期**（互斥證據在 A6／A7） |

### 5.7 C 包：單一建置／啟動模式、產物隔離、API base、DB／帳號

工具鏈前提（**實測**）：預設 `node`＝v18.20.8 跑不動前端測試與 Playwright（後者自拒「Playwright requires Node.js 20 or higher」）⇒ 一律寫死 `~/.local/bin/node22`（v22.23.5）。瀏覽器：bundled chromium **未安裝**（`~/Library/Caches/ms-playwright` 為空），安裝屬**外呼＋依賴**，未批准；`/Applications/Google Chrome.app` 存在且 `frontend/playwright.config.ts:12` 實做 `E2E_BROWSER_CHANNEL` ⇒ 以 `E2E_BROWSER_CHANNEL=chrome` drive 本機 Chrome，**零下載**。建置：**只准 `npm run build` 一次**，記 rc 與**新的** `.next/BUILD_ID`；產物隔離 `NEXT_DIST_DIR="$RUN/next-dist"`，**不覆寫共用 `.next`**（共用產物 `iEzzhWbfCzP7pv7Rb0LcS`／mtime 11-19 09:05 **早於** `DailyEstimatePanel.tsx` 17:24 ⇒ 舊產物，**不得**作候選證據）。**`NEXT_PUBLIC_API_URL` 是 build-time 內嵌**（`frontend/src/lib/api.ts:11`，預設 `http://localhost:8000`）⇒ 必須在 **build 之前** export 指到 B 包那支 8031，否則頁面打 8000（本機現測**無人聆聽 8000**）。啟動：`npm run start -p 3031 -H 127.0.0.1` 或 `npm run dev -H 127.0.0.1`，URL `http://127.0.0.1:3031`（進場以 `lsof -nP -iTCP:3031 -sTCP:LISTEN` 重查；本輪 0 列）。DB／帳號＝與 B 包同一隔離 DB、同一測試 owner，`src/app/login/page.tsx` 密碼登入 ⇒ `actor_type=human`（`app/auth/provenance.py:30`）；共同前提 `RESEARCH_AUTH_BYPASS_ENABLED=false`＋`CANDIDATE_PASSWORDLESS_MODE=false` 屬**必要**。e2e 定位：`playwright.config.ts` 存在、`e2e/` **32 檔 312 支**可列舉（`--list`，未起服務、未開瀏覽器），但 `grep -r "daily-estimate\|pp-download-decision-history" e2e/` ＝ **0 支** ⇒ 本包兩條旅程**不在**現有自動化裡；**新寫 spec 屬工程準備，不等於獲准啟動瀏覽器**。**自然 cron、瀏覽器自動化、owner 本人同意三者分開、不互相替代**；代理**不得**替 owner 按下同意／撤回；build PASS／node:test PASS **不代替** browser PASS；未批准前本包 **NOT RUN**。仍以源碼層如實標記的缺口見 §四（`OrderPreviewPanel.tsx` 不送也不持有任何血緣）。

### 5.8 D 包：**「重現整體受測工作樹」與「准許保存的發布範圍」分開**

| 概念 | 本輪實物 |
|---|---|
| **重現整體受測工作樹** | 受測候選 `bd5b844b…`（§2.2／§5.1）；髒路徑 **211**（porcelain sha256 有**兩個不可互換**值：runner `run_tests.sh:386` 用原始位元組含尾端換行；產生器 `"\n".join` 不含尾端換行；引用時要一併寫明產出者） |
| **准許保存的發布範圍** | `candidate_date=2026-11-19` 且路徑在 `backend/`＋`frontend/`，排除 `.venv\|node_modules\|__pycache__\|.next\|ml/artifacts\|alethra-evidence`，並**刻意排除**兩份釘選檔 `backend/tests/collected_nodes.txt`、`backend/tests/skip_budget.json`（**產生審閱資料 ≠ 重釘**）⇒ 本輪 **182 檔**（詳見 §六）。**211 − 182 ＝ 29 個髒路徑不在發布範圍內**，誠實標出 |
| 依賴閉包（**必讀**） | 182 檔裡 **56 份 untracked 全位元組**含**產品檔本身**（現測 `untracked/` 內含 `app/services/daily_estimates.py`、`app/services/decision_lineage.py`、`app/services/skill_units.py`、`app/services/strategy_versions.py`、`app/services/run_trades.py`、`app/api/data_manifest.py`、`app/lib/uuid_bind.py`、`app/lib/uuid_spelling.py`、四支 migration、`scripts/{check_env_hygiene,check_migration_tail,run_evidence,skip_budget}.py`、`tests/{daily_estimate_race,postgresql_only,replay_fake}.py` …）；另含 #177 三支測試檔（見 §3.4，它們是 `??`，`git apply` 那 126 份 patch **組不出可跑的候選**） |
| 重現步驟（**未執行**；每步都是寫入 `$RUN` 內） | ①`git clone /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra $RUN/clone` ＋ `git checkout 5a34e3fe`（**本地來源、不外拉**）②依 `SHA256SUMS.txt` 落 **56** 份 untracked ③`git apply --check` 通過後 `git apply` **126** 份 patch ④受測命令（`sqlite`／`frontend`）⑤`npm run typecheck`。**停損線**：`git apply --check` 非零 ⇒ **停**，不 `--reject` 硬套、不手動合、不整檔還原共享檔。①②③皆 Git／檔案寫入 ⇒ **需 owner 明確批准「建立乾淨 clone 並套用」；本輪未做**；產生這份包**不**等於批准套用／stage／commit／push／重釘。產生器 `alethra-evidence/tools/build_review_package.py` **絕不**執行 `git add/commit/push/checkout/restore/clean` |

### 5.9 可讀的 182 檔 D manifest（工具 derive，非手抄）

產生者 `alethra-evidence/tools/build_d_attribution.py` 對 `alethra-evidence/d-patch-round12/`（126 份 tracked patch ＋ 56 份 untracked 全檔）派生；存檔 `alethra-evidence/d-attribution-round12/ATTRIBUTION.md` ＋ `ATTRIBUTION.json` ＋ `SHA256SUMS.txt`（2／2 OK）。

**計數（機產）**：total **182**；分桶＝旅程一 **13**／旅程二 **21**／兩條旅程以外 **148**；`紀錄查無出處（來源未確認）` ＝ **91**。
兩個計數物件不可互換：分桶數**數「檔」**、標籤計 13／22 **數「標籤」**，差異來自 `backend/tests/test_daily_estimate_two_workers.py` 同時帶兩條旅程標籤（屬**正常**，非算錯）。
**包 delta（兩份冻结包 sha 對照）**：added **0**／removed **0**／changed **6**／unchanged **176**；changed 六支＝`backend/app/api/personalization.py`、`backend/app/services/daily_estimates.py`、`backend/tests/test_daily_model_estimate.py`、`frontend/src/components/training/DailyEstimatePanel.tsx`、`frontend/src/lib/dailyEstimate.ts`、`frontend/tests/dailyEstimate.test.mjs`。
**untracked 以「全檔 N bytes（Git 無基線，故無 diff）」**表達；tracked 才列 hunk／± 行。91 檔未聲領**手核抽樣 7 檔**（非全數）⇒「紀錄查無引用」是事實，**不等於**「來源必然可疑」。

| 路徑｜Git | hunks | ＋ | − | 旅程 | 紀錄首引 | 所屬輪次 |
|---|---|---|---|---|---|---|
| backend/app/api/personalization.py | M | 3 | 364 | 2 | 旅程一：每日估計的失敗可見性 | L998（backend/app/api/personalization.py） | 第四輪收尾（2026-11-18 18:39–1 |
| backend/app/services/daily_estimates.py | ?? | — | 全檔 66,129B（Git 無基線，故無 diff） | — | 旅程一：每日估計的失敗可見性 | L996（backend/app/services/daily_estimates.py） | 第四輪收尾（2026-11-18 18:39–1 |
| backend/app/workers/learning_scheduler.py | M | 5 | 1086 | 262 | 旅程一：每日估計的失敗可見性 | L206（backend/app/workers/learning_scheduler.py） | （第一輪之前的正文／總覽段） |
| backend/tests/daily_estimate_race.py | ?? | — | 全檔 17,143B（Git 無基線，故無 diff） | — | 旅程一：每日估計的失敗可見性 | L2342（backend/tests/daily_estimate_race.py） | 第十一輪（2026-11-19 19:0x–20 |
| backend/tests/test_daily_estimate_two_workers.py | ?? | — | 全檔 47,568B（Git 無基線，故無 diff） | — | 旅程一：每日估計的失敗可見性 | L1498（backend/tests/test_daily_estimate_two_workers.py） | 第七輪（2026-11-19 08:04–09: |
| backend/tests/test_daily_model_estimate.py | ?? | — | 全檔 133,006B（Git 無基線，故無 diff） | — | 旅程一：每日估計的失敗可見性 | L997（backend/tests/test_daily_model_estimate.py） | 第四輪收尾（2026-11-18 18:39–1 |
| backend/tests/test_learning_scheduler.py | M | 3 | 865 | 121 | 旅程一：每日估計的失敗可見性 | L1144（backend/tests/test_learning_scheduler.py） | 第五輪（2026-11-18 19:41–20: |
| backend/tests/test_simulation_daily_tick.py | ?? | — | 全檔 42,198B（Git 無基線，故無 diff） | — | 旅程一：每日估計的失敗可見性＋旅程二：決策的來源血緣與匯出 | L260（backend/tests/test_simulation_daily_tick.py） | （第一輪之前的正文／總覽段） |
| frontend/src/components/training/DailyEstimatePanel.tsx | ?? | — | 全檔 28,476B（Git 無基線，故無 diff） | — | 旅程一：每日估計的失敗可見性 | L1076（frontend/src/components/training/DailyEstimatePanel.tsx） | 第四輪收尾（2026-11-18 18:39–1 |
| frontend/src/lib/dailyEstimate.ts | ?? | — | 全檔 14,905B（Git 無基線，故無 diff） | — | 旅程一：每日估計的失敗可見性 | L1499（frontend/src/lib/dailyEstimate.ts） | 第七輪（2026-11-19 08:04–09: |
| frontend/src/lib/dailyService.ts | ?? | — | 全檔 8,025B（Git 無基線，故無 diff） | — | 旅程一：每日估計的失敗可見性 | L44（frontend/src/lib/dailyService.ts） | （第一輪之前的正文／總覽段） |
| frontend/tests/dailyEstimate.test.mjs | ?? | — | 全檔 48,280B（Git 無基線，故無 diff） | — | 旅程一：每日估計的失敗可見性 | L1500（frontend/tests/dailyEstimate.test.mjs） | 第七輪（2026-11-19 08:04–09: |
| frontend/tests/dailyService.test.mjs | ?? | — | 全檔 11,150B（Git 無基線，故無 diff） | — | 旅程一：每日估計的失敗可見性 | L266（frontend/tests/dailyService.test.mjs） | （第一輪之前的正文／總覽段） |
| backend/app/api/human_decisions.py | M | 2 | 6 | 0 | 旅程二：決策的來源血緣與匯出 | L2306（backend/app/api/human_decisions.py） | 第十輪（2026-11-19 16:5x–18: |
| backend/app/api/journal.py | M | 5 | 30 | 12 | 旅程二：決策的來源血緣與匯出 | L784（backend/app/api/journal.py） | 第三輪補記（13:29–13:45）：P8——版 |
| backend/app/api/paper_portfolio.py | M | 22 | 405 | 28 | 旅程二：決策的來源血緣與匯出 | L510（backend/app/api/paper_portfolio.py） | （第一輪之前的正文／總覽段） |
| backend/app/api/simulations.py | M | 21 | 346 | 64 | 旅程二：決策的來源血緣與匯出 | L207（backend/app/api/simulations.py） | （第一輪之前的正文／總覽段） |
| backend/app/models/human_decision.py | M | 1 | 9 | 0 | 旅程二：決策的來源血緣與匯出 | L1156（backend/app/models/human_decision.py） | 第五輪（2026-11-18 19:41–20: |
| backend/app/models/paper_portfolio.py | M | 1 | 1 | 0 | 旅程二：決策的來源血緣與匯出 | L1155（backend/app/models/paper_portfolio.py） | 第五輪（2026-11-18 19:41–20: |
| backend/app/services/decision_lineage.py | ?? | — | 全檔 2,957B（Git 無基線，故無 diff） | — | 旅程二：決策的來源血緣與匯出 | L126（app/services/decision_lineage.py） | （第一輪之前的正文／總覽段） |
| backend/migrations/versions/c3d4e5f6g7h8_add_decision_inference_link.py | ?? | — | 全檔 4,311B（Git 無基線，故無 diff） | — | 旅程二：決策的來源血緣與匯出 | L869（backend/migrations/versions/c3d4e5f6g7h8_add_decision_inference_link.py） | 第四輪後半段（2026-11-18 17:22– |
| backend/migrations/versions/d4e5f6g7h8i9_add_human_decision_inference_link.py | ?? | — | 全檔 4,328B（Git 無基線，故無 diff） | — | 旅程二：決策的來源血緣與匯出 | L2306（d4e5f6g7h8i9_add_human_decision_inference_link.py） | 第十輪（2026-11-19 16:5x–18: |
| backend/tests/test_decision_history_export.py | ?? | — | 全檔 38,738B（Git 無基線，故無 diff） | — | 旅程二：決策的來源血緣與匯出 | L847（backend/tests/test_decision_history_export.py） | 第四輪後半段（2026-11-18 17:22– |
| backend/tests/test_decision_lineage.py | ?? | — | 全檔 34,113B（Git 無基線，故無 diff） | — | 旅程二：決策的來源血緣與匯出 | L22（tests/test_decision_lineage.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_journal_api.py | M | 2 | 210 | 0 | 旅程二：決策的來源血緣與匯出 | L785（backend/tests/test_journal_api.py） | 第三輪補記（13:29–13:45）：P8——版 |
| backend/tests/test_manual_decision_books_linked_portfolio.py | M | 3 | 575 | 6 | 旅程二：決策的來源血緣與匯出 | L292（backend/tests/test_manual_decision_books_linked_portfolio.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_run_trades_export.py | ?? | — | 全檔 20,522B（Git 無基線，故無 diff） | — | 旅程二：決策的來源血緣與匯出 | **未聲領** | — |
| frontend/src/app/experiments/[id]/simulation/page.tsx | M | 11 | 205 | 17 | 旅程二：決策的來源血緣與匯出 | L209（frontend/src/app/experiments/[id]/simulation/page.tsx） | （第一輪之前的正文／總覽段） |
| frontend/src/app/paper-portfolio/[id]/page.tsx | M | 9 | 159 | 9 | 旅程二：決策的來源血緣與匯出 | L847（frontend/src/app/paper-portfolio/[id]/page.tsx） | 第四輪後半段（2026-11-18 17:22– |
| frontend/src/components/journal/AutoCollectionStatus.tsx | M | 9 | 198 | 62 | 旅程二：決策的來源血緣與匯出 | L45（frontend/src/components/journal/AutoCollectionStatus.tsx） | （第一輪之前的正文／總覽段） |
| frontend/src/components/journal/JournalDashboard.tsx | M | 1 | 2 | 2 | 旅程二：決策的來源血緣與匯出 | L45（frontend/src/components/journal/JournalDashboard.tsx） | （第一輪之前的正文／總覽段） |
| frontend/src/lib/decisionLineage.ts | ?? | — | 全檔 2,123B（Git 無基線，故無 diff） | — | 旅程二：決策的來源血緣與匯出 | L1922（src/lib/decisionLineage.ts） | 第九輪（2026-11-19 09:5x–13: |
| frontend/src/lib/runExport.ts | ?? | — | 全檔 2,075B（Git 無基線，故無 diff） | — | 旅程二：決策的來源血緣與匯出 | **未聲領** | — |
| frontend/tests/runTradeExport.test.mjs | ?? | — | 全檔 6,423B（Git 無基線，故無 diff） | — | 旅程二：決策的來源血緣與匯出 | **未聲領** | — |
| backend/app/api/__init__.py | M | 2 | 2 | 0 | 兩條旅程以外 | **未聲領** | — |
| backend/app/api/admin.py | M | 3 | 102 | 60 | 兩條旅程以外 | L41（app/api/admin.py） | （第一輪之前的正文／總覽段） |
| backend/app/api/agent_strategy.py | M | 3 | 18 | 0 | 兩條旅程以外 | L292（backend/app/api/agent_strategy.py） | （第一輪之前的正文／總覽段） |
| backend/app/api/data_manifest.py | ?? | — | 全檔 28,133B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/app/api/learning.py | M | 3 | 64 | 41 | 兩條旅程以外 | L40（app/api/learning.py） | （第一輪之前的正文／總覽段） |
| backend/app/api/paper_trading.py | M | 12 | 143 | 19 | 兩條旅程以外 | L784（backend/app/api/paper_trading.py） | 第三輪補記（13:29–13:45）：P8——版 |
| backend/app/api/personal_models.py | M | 28 | 180 | 19 | 兩條旅程以外 | L292（backend/app/api/personal_models.py） | （第一輪之前的正文／總覽段） |
| backend/app/api/robustness.py | M | 2 | 60 | 6 | 兩條旅程以外 | **未聲領** | — |
| backend/app/api/runs.py | M | 8 | 248 | 15 | 兩條旅程以外 | L36（app/api/runs.py） | （第一輪之前的正文／總覽段） |
| backend/app/api/schemas.py | M | 7 | 105 | 1 | 兩條旅程以外 | L208（backend/app/api/schemas.py） | （第一輪之前的正文／總覽段） |
| backend/app/api/strategy_templates.py | M | 3 | 78 | 51 | 兩條旅程以外 | **未聲領** | — |
| backend/app/config.py | M | 6 | 132 | 0 | 兩條旅程以外 | L1000（backend/app/config.py） | 第四輪收尾（2026-11-18 18:39–1 |
| backend/app/engine/backtest.py | M | 2 | 5 | 2 | 兩條旅程以外 | **未聲領** | — |
| backend/app/lib/timeutil.py | M | 2 | 30 | 1 | 兩條旅程以外 | L784（backend/app/lib/timeutil.py） | 第三輪補記（13:29–13:45）：P8——版 |
| backend/app/lib/uuid_bind.py | ?? | — | 全檔 3,654B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L783（backend/app/lib/uuid_bind.py） | 第三輪補記（13:29–13:45）：P8——版 |
| backend/app/lib/uuid_spelling.py | ?? | — | 全檔 2,287B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L379（backend/app/lib/uuid_spelling.py） | （第一輪之前的正文／總覽段） |
| backend/app/main.py | M | 3 | 7 | 15 | 兩條旅程以外 | L1308（backend/app/main.py） | 第五輪（2026-11-18 19:41–20: |
| backend/app/models/agent_strategy.py | M | 2 | 4 | 0 | 兩條旅程以外 | L31（agent_strategy.py） | （第一輪之前的正文／總覽段） |
| backend/app/models/learning_job.py | M | 1 | 5 | 2 | 兩條旅程以外 | **未聲領** | — |
| backend/app/models/paper_trading_run.py | M | 3 | 13 | 2 | 兩條旅程以外 | **未聲領** | — |
| backend/app/models/personal_model_selection.py | M | 1 | 3 | 0 | 兩條旅程以外 | **未聲領** | — |
| backend/app/models/research_run.py | M | 3 | 34 | 1 | 兩條旅程以外 | **未聲領** | — |
| backend/app/services/__init__.py | M | 1 | 1 | 0 | 兩條旅程以外 | **未聲領** | — |
| backend/app/services/auto_collection.py | M | 1 | 237 | 199 | 兩條旅程以外 | L41（app/services/auto_collection.py） | （第一輪之前的正文／總覽段） |
| backend/app/services/live_trading_daemon.py | M | 15 | 106 | 28 | 兩條旅程以外 | L783（backend/app/services/live_trading_daemon.py） | 第三輪補記（13:29–13:45）：P8——版 |
| backend/app/services/model_activation.py | M | 8 | 373 | 3 | 兩條旅程以外 | L292（backend/app/services/model_activation.py） | （第一輪之前的正文／總覽段） |
| backend/app/services/model_comparison.py | M | 2 | 24 | 30 | 兩條旅程以外 | L34（app/services/model_comparison.py） | （第一輪之前的正文／總覽段） |
| backend/app/services/paper_trading.py | M | 39 | 254 | 118 | 兩條旅程以外 | L510（backend/app/services/paper_trading.py） | （第一輪之前的正文／總覽段） |
| backend/app/services/personal_selection.py | M | 7 | 155 | 19 | 兩條旅程以外 | L510（backend/app/services/personal_selection.py） | （第一輪之前的正文／總覽段） |
| backend/app/services/research_runs.py | M | 15 | 364 | 10 | 兩條旅程以外 | L36（app/services/research_runs.py） | （第一輪之前的正文／總覽段） |
| backend/app/services/robustness.py | M | 4 | 44 | 3 | 兩條旅程以外 | **未聲領** | — |
| backend/app/services/run_trades.py | ?? | — | 全檔 10,728B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/app/services/skill_units.py | ?? | — | 全檔 4,626B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L510（backend/app/services/skill_units.py） | （第一輪之前的正文／總覽段） |
| backend/app/services/strategy_versions.py | ?? | — | 全檔 2,489B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/migrations/versions/b2c3d4e5f6g7_add_skill_unit_associations.py | ?? | — | 全檔 5,661B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L140（migrations/versions/b2c3d4e5f6g7_add_skill_unit_associations.py） | （第一輪之前的正文／總覽段） |
| backend/migrations/versions/e1f2a3b4c5d6_add_runstatus_cancelled.py | ?? | — | 全檔 3,638B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/migrations/versions/f2a3b4c5d6e7_add_research_run_idempotency.py | ?? | — | 全檔 6,998B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/scripts/check_env_hygiene.py | ?? | — | 全檔 30,324B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/scripts/check_migration_tail.py | ?? | — | 全檔 11,053B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L292（backend/scripts/check_migration_tail.py） | （第一輪之前的正文／總覽段） |
| backend/scripts/run_evidence.py | ?? | — | 全檔 26,566B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L948（backend/scripts/run_evidence.py） | 第四輪後半段（2026-11-18 17:22– |
| backend/scripts/run_tests.sh | M | 5 | 346 | 5 | 兩條旅程以外 | L1222（backend/scripts/run_tests.sh） | 第五輪（2026-11-18 19:41–20: |
| backend/scripts/skip_budget.py | ?? | — | 全檔 11,061B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L2204（scripts/skip_budget.py） | 第十輪（2026-11-19 16:5x–18: |
| backend/test_explicit_provider_selection.py | M | 9 | 75 | 56 | 兩條旅程以外 | **未聲領** | — |
| backend/test_provider_isolation.py | M | 2 | 32 | 21 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/api_test_fixtures.py | M | 16 | 78 | 30 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/conftest.py | M | 3 | 13 | 3 | 兩條旅程以外 | L6（tests/conftest.py） | （第一輪之前的正文／總覽段） |
| backend/tests/postgresql_only.py | ?? | — | 全檔 6,372B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/tests/replay_fake.py | ?? | — | 全檔 10,150B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_acquisition_hook.py | M | 1 | 1 | 1 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_admin_security.py | M | 1 | 10 | 2 | 兩條旅程以外 | L102（tests/test_admin_security.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_agent_strategy_slice.py | M | 1 | 93 | 0 | 兩條旅程以外 | L292（backend/tests/test_agent_strategy_slice.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_api_fixture_lifecycle.py | M | 6 | 10 | 9 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_api_keys.py | M | 1 | 0 | 3 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_api_login_with_fixture.py | M | 2 | 19 | 0 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_auto_collection.py | ?? | — | 全檔 23,992B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L41（tests/test_auto_collection.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_c1_c4_safety_cases.py | M | 2 | 17 | 0 | 兩條旅程以外 | L945（test_c1_c4_safety_cases.py） | 第四輪後半段（2026-11-18 17:22– |
| backend/tests/test_candidate_identity.py | M | 1 | 5 | 1 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_candidate_passwordless.py | M | 5 | 73 | 10 | 兩條旅程以外 | L2575（test_candidate_passwordless.py） | 第十二輪（2026-11-19 20:2x–20 |
| backend/tests/test_cleanup_verification.py | M | 2 | 19 | 3 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_config.py | M | 1 | 29 | 0 | 兩條旅程以外 | L81（tests/test_config.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_connection_role_verification.py | M | 2 | 20 | 0 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_crypto_bar_harvest_pg.py | M | 1 | 11 | 3 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_crypto_paper_pg.py | M | 2 | 10 | 3 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_data_isolation.py | M | 1 | 19 | 0 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_data_manifest_contract.py | ?? | — | 全檔 20,652B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_decimal_precision_regression.py | M | 3 | 13 | 1 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_e2e_shared_metadata.py | M | 2 | 5 | 0 | 兩條旅程以外 | L945（test_e2e_shared_metadata.py） | 第四輪後半段（2026-11-18 17:22– |
| backend/tests/test_env_entries.py | ?? | — | 全檔 23,509B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_env_hygiene_contract.py | ?? | — | 全檔 30,852B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_error_correlation.py | M | 1 | 10 | 3 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_error_response_safety.py | M | 2 | 176 | 1 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_fixture_failure_injection.py | M | 4 | 10 | 7 | 兩條旅程以外 | L2105（test_fixture_failure_injection.py） | 第九輪（2026-11-19 09:5x–13: |
| backend/tests/test_formal_path_integration.py | M | 2 | 16 | 3 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_formal_skill_unit_authorization.py | ?? | — | 全檔 5,334B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_formal_skill_unit_authorization_endpoint.py | ?? | — | 全檔 16,527B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L129（tests/test_formal_skill_unit_authorization_endpoint.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_http_api_success_chain.py | M | 2 | 18 | 2 | 兩條旅程以外 | L510（backend/tests/test_http_api_success_chain.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_lane_entry_contract.py | ?? | — | 全檔 31,884B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_learning_api.py | M | 4 | 165 | 7 | 兩條旅程以外 | L103（tests/test_learning_api.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_ledger_reconciliation.py | M | 2 | 12 | 1 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_migration_tail_contract.py | ?? | — | 全檔 12,798B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L292（backend/tests/test_migration_tail_contract.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_model_activation.py | M | 3 | 565 | 0 | 兩條旅程以外 | L313（test_model_activation.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_owner_verification.py | M | 2 | 14 | 1 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_ownership_integration.py | M | 12 | 293 | 22 | 兩條旅程以外 | L785（backend/tests/test_ownership_integration.py） | 第三輪補記（13:29–13:45）：P8——版 |
| backend/tests/test_paper_create_engine_consistency.py | ?? | — | 全檔 37,079B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L379（backend/tests/test_paper_create_engine_consistency.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_paper_trading_cancel_atomic.py | M | 29 | 75 | 74 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_pattern_analysis.py | M | 3 | 8 | 4 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_personal_model_comparison.py | M | 4 | 52 | 25 | 兩條旅程以外 | L489（tests/test_personal_model_comparison.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_personal_model_permission_matrix.py | M | 16 | 93 | 20 | 兩條旅程以外 | L1544（tests/test_personal_model_permission_matrix.py） | 第七輪（2026-11-19 08:04–09: |
| backend/tests/test_personal_strategy_learning.py | M | 6 | 86 | 16 | 兩條旅程以外 | L104（tests/test_personal_strategy_learning.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_pilot.py | M | 1 | 0 | 3 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_position_timing_bugs.py | M | 3 | 57 | 135 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_postgresql_only_marks.py | ?? | — | 全檔 17,711B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_privilege_enforcement.py | M | 2 | 22 | 0 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_process_state_isolation.py | ?? | — | 全檔 12,497B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L2308（backend/tests/test_process_state_isolation.py） | 第十輪（2026-11-19 16:5x–18: |
| backend/tests/test_providers.py | M | 2 | 25 | 2 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_quota_concurrency.py | M | 12 | 377 | 101 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_real_concurrency.py | M | 2 | 13 | 1 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_recommendation_logic_repairs.py | M | 3 | 104 | 1 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_registration_risk_acknowledgement.py | M | 3 | 129 | 2 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_regression_contracts.py | M | 3 | 16 | 3 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_replay_credibility.py | M | 1 | 465 | 297 | 兩條旅程以外 | L2444（backend/tests/test_replay_credibility.py） | 第十一輪（2026-11-19 19:0x–20 |
| backend/tests/test_replay_status_management.py | M | 1 | 11 | 0 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_research_run_admission.py | M | 8 | 554 | 18 | 兩條旅程以外 | L2104（tests/test_research_run_admission.py） | 第九輪（2026-11-19 09:5x–13: |
| backend/tests/test_research_run_recovery.py | M | 4 | 382 | 7 | 兩條旅程以外 | L64（test_research_run_recovery.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_retry_failure_validation.py | M | 2 | 12 | 1 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_robustness_studies.py | M | 3 | 231 | 0 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_run_evidence_ledger.py | ?? | — | 全檔 20,572B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_safety_gate_negative.py | M | 6 | 58 | 22 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_schema_incompatible_readiness.py | M | 1 | 9 | 3 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_skill_unit_ceiling.py | ?? | — | 全檔 13,257B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L106（tests/test_skill_unit_ceiling.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_source_dedup_scope.py | M | 2 | 12 | 1 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_strategy_study_timestamps_pg.py | M | 1 | 10 | 3 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_successful_inference.py | ?? | — | 全檔 31,276B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L292（backend/tests/test_successful_inference.py） | （第一輪之前的正文／總覽段） |
| backend/tests/test_target_guard.py | M | 10 | 204 | 96 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_trial_passwordless.py | M | 4 | 27 | 4 | 兩條旅程以外 | **未聲領** | — |
| backend/tests/test_vertical_path.py | M | 1 | 14 | 1 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/cross-experiment-comparison.spec.ts | M | 3 | 6 | 32 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/crypto-research-markers.spec.ts | M | 3 | 6 | 31 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/intraday-rule-flow.spec.ts | M | 2 | 6 | 29 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/order-lifecycle-flow.spec.ts | M | 2 | 6 | 31 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/personal-strategy-learning.spec.ts | M | 12 | 122 | 14 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/research-record.spec.ts | M | 3 | 6 | 31 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/robustness-study.spec.ts | M | 3 | 6 | 32 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/rule-triggered-flow.spec.ts | M | 2 | 6 | 30 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/runtime.ts | ?? | — | 全檔 2,753B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/strategy-authoring-and-lock.spec.ts | M | 3 | 6 | 32 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/strategy-revision.spec.ts | M | 3 | 6 | 32 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/unified-research-flow.spec.ts | M | 3 | 6 | 30 | 兩條旅程以外 | **未聲領** | — |
| frontend/e2e/workspace-replay-backend.spec.ts | M | 2 | 6 | 31 | 兩條旅程以外 | **未聲領** | — |
| frontend/playwright.config.ts | M | 2 | 7 | 0 | 兩條旅程以外 | L2289（frontend/playwright.config.ts） | 第十輪（2026-11-19 16:5x–18: |
| frontend/src/app/experiments/[id]/page.tsx | M | 4 | 34 | 3 | 兩條旅程以外 | L209（page.tsx） | （第一輪之前的正文／總覽段） |
| frontend/src/app/experiments/[id]/runs/[runId]/page.tsx | M | 5 | 227 | 2 | 兩條旅程以外 | L209（page.tsx） | （第一輪之前的正文／總覽段） |
| frontend/src/app/experiments/new/page.tsx | M | 4 | 30 | 17 | 兩條旅程以外 | L209（page.tsx） | （第一輪之前的正文／總覽段） |
| frontend/src/app/strategy-templates/page.tsx | M | 2 | 4 | 6 | 兩條旅程以外 | L209（page.tsx） | （第一輪之前的正文／總覽段） |
| frontend/src/app/training/page.tsx | M | 2 | 2 | 0 | 兩條旅程以外 | L1078（frontend/src/app/training/page.tsx） | 第四輪收尾（2026-11-18 18:39–1 |
| frontend/src/components/experiments/DailyServicePanel.tsx | ?? | — | 全檔 5,953B（Git 無基線，故無 diff） | — | 兩條旅程以外 | L44（frontend/src/components/experiments/DailyServicePanel.tsx） | （第一輪之前的正文／總覽段） |
| frontend/src/components/experiments/DataManifestPanel.tsx | ?? | — | 全檔 17,476B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| frontend/src/components/experiments/RevisionPanel.tsx | M | 4 | 70 | 6 | 兩條旅程以外 | **未聲領** | — |
| frontend/src/components/experiments/RobustnessPanel.tsx | M | 2 | 20 | 4 | 兩條旅程以外 | **未聲領** | — |
| frontend/src/components/training/PersonalStrategyLearningPanel.tsx | M | 39 | 555 | 68 | 兩條旅程以外 | L2159（src/components/training/PersonalStrategyLearningPanel.tsx） | 第十輪（2026-11-19 16:5x–18: |
| frontend/src/lib/api.ts | M | 10 | 126 | 10 | 兩條旅程以外 | L209（frontend/src/lib/api.ts） | （第一輪之前的正文／總覽段） |
| frontend/src/lib/copy.ts | M | 2 | 4 | 0 | 兩條旅程以外 | **未聲領** | — |
| frontend/src/lib/dataManifest.ts | ?? | — | 全檔 9,821B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| frontend/src/lib/runRetry.ts | ?? | — | 全檔 3,798B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| frontend/src/lib/runStatus.ts | M | 2 | 31 | 1 | 兩條旅程以外 | **未聲領** | — |
| frontend/tests/dataManifest.test.mjs | ?? | — | 全檔 15,608B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| frontend/tests/runCancellation.test.mjs | ?? | — | 全檔 7,361B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |
| frontend/tests/runRetry.test.mjs | ?? | — | 全檔 11,097B（Git 無基線，故無 diff） | — | 兩條旅程以外 | **未聲領** | — |

## 第十二輪 §六：必須一起讀的揭露（**不得只引其中一個**）

1. **候選已移動，且未重釘**：第十一輪整套 green 綁 `5d9dff9c…`；本輪兩支新測試把 tree digest 推到 **`bd5b844be388cfdcd5f2c79d652fbf27ee476c719712dc4a6f0e9517f3e00194`**。依「不重跑無變更全套、不重釘」，新候選**只**以檔案級＋93 節點依賴閉包＋前端 38 支驗證。因此：`pytest exit 0`（本輪 scoped 跑）與「正式 lane exit 1」（未釘時 ledger 閘 `run_tests.sh:484-485` 覆蓋 census 閘）**必須同列**，後者是第十一輪**實測更正**的值，不是本輪預測的 2。
2. **`+28` 不是從獲准基準起算**：P（釘選）＝**3,811**、C（第十一輪受測）＝**3,837**（**+26 屬前次會話工作樹**，本輪**未**追認）、本輪受測＝**3,839**（+2 屬本輪兩支新測試），removed **0**。兩份受控檔 sha（`collected_nodes.txt 5260ece2…`／`skip_budget.json c653b1a7…`）**未動**，跳過分類（179／159／18／2）**未動** ⇒ 重釘卡仍未執行。
3. **前端 scrape 副作用（揭露）**：`frontend/tests/dailyEstimate.test.mjs` 以 `/"reason": "([a-z_]+)"/g` 從服務源碼掃 `REFUSAL_REASONS`，本輪新增的 `"reason": "engine_failure"` **被一併掃入**；斷言逐字未動（`length >= 5` 是下限而非等號、`includes(already_estimated_this_window)`、`otherReason` 存在，`:699-701`），**未放寬**，但該集合的內涵已多一支：以第十一輪快照對比工作樹**實測**掃出 **5 支 → 6 支**，多出的是本輪新增的 `engine_failure`；引用計數時要寫明「掃出 6 支，其中 1 支是 engine_failure（本輪新增）」，不得只引 6 或只引 5。
4. **產生 D 包不改候選（已量）**：寫入 `d-patch-round12/`（182 檔＋SHA256SUMS）與 `d-attribution-round12/`（3 檔）之後**重跑** `tree_digest()`，逐字仍是 `bd5b844b…` ⇒ 證據產出與候選隔離。
5. **PORCELAIN sha 有兩個產出者**：runner `backend/scripts/run_tests.sh:386`（原始位元組，**含**尾端換行）vs 產生器 `alethra-evidence/tools/build_review_package.py`（`"\n".join`，**不含**）。本輪整套髒路徑 **211**、`backend/`＋`frontend/` 範圍 **188**；兩個 sha **不可互換**，引用務必附產出者。
6. **91 檔「來源未確認」是事實陳述，不是品質判決**：其成立條件只有「本紀錄 2,604 行查無引用」；手核抽樣 **7** 檔（非全數）⇒ 其餘 84 檔可能只是**紀錄沒寫**，不得由代理改寫成「可疑／應回退」。
7. **旅程一尚未 browser 驗收**：本輪證據到 ①離線讀碼／④-sqlite／⑤-HTTP in-process 三級；**⑤ ≠ browser**，C 包 **NOT RUN**。

## 第十二輪 §七：SIGKILL 偏離紀錄**保留**，本輪不重做、不清理

第十一輪 §（`:2413`）那條偏離原封保留：本輪**未**刪任何 `$RUN` 目錄、**未**清 `/var/tmpr/alethra-r12-*`、**未**重跑該被 SIGKILL 的整套、**未**碰 `5d9dff9c…`／`3fbb810e…` 的既有產物，也**未**對 `market_data_bars` 等共享種子下手。

本輪**只**新增這些檔案（逐一列出，無隱藏寫入）：
- `EXECUTION_TABLE_20261117.md`（§一～§七，唯一紀錄檔，**未**新增平行摘要）
- `alethra-evidence/d-patch-round12/`（`MANIFEST.json`＋`tracked/` 126＋`untracked/` 56＋`SHA256SUMS.txt`）
- `alethra-evidence/d-attribution-round12/`（`ATTRIBUTION.md`＋`ATTRIBUTION.json`＋`SHA256SUMS.txt`）
- `alethra-evidence/tools/build_d_attribution.py`（新產生器；拒絕對已存在包目錄重建）
- 產品碼改動僅 §2.2 四檔＋§2.3 兩支後端測試（見 §2.2 表）；**未** stage／commit／push／重釘／套用 patch／初始化 PG／跑 migration／啟服務／開瀏覽器／外呼。

**下一份集中交付順序**（提示明文）：客戶結果（§二）→ 真正獲准基準的完整差異（§一）→ #177 契約（§三）→ 可讀 D 包（§5.9）→ OrderPreviewPanel 裁定（§四）→ A／B／C 確切操作（§五）→ 揭露（§六）。**本提示不當作執行批准**；任何 A1–A7／B／C／D／重釘／Git 寫入仍需各自精確批准。

## 第十三輪 §八（2026-11-20 上午）：**先交客戶結果**——決策血緣逐層閉包、工作區面板接通明選、#177 三缺口、第六拒因；基準重建與批准內容在其後

提示順序明文：「下一份集中交付先交客戶結果…再交基準重建、可審差異與需要的批准內容。」本節照那個順序排：**§8.1–§8.4 是客戶結果**，§8.5 起才是基準、批准與揭露。**本節不是執行批准**：本輪未啟動 PG／服務／瀏覽器、未外呼、未跑 migration、未重釘、未套用 patch、未做任何 Git 寫入。

### 8.0 本輪受測內容的身份（先固定，後面每一格都引用它）

| 項目 | 本輪現測值（2026-11-20，全部由命令重算，非抄錄） |
|---|---|
| 唯一由 Git 固定的 base | `git rev-parse HEAD` ＝ `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（`git cat-file -t` ＝ `commit`；8／8 倖存 run dir 的 `identity.txt` 都帶這顆 HEAD） |
| **今輪候選** | `backend/**/*.py` tree digest ＝ **`75d2cfdde8ed78e40718def127f69785dc792d9ab96d6af876badbde195c05f8`**（526 支 `.py`；`run_tests.sh:345-351` 那條 `find\|xargs shasum\|shasum\|cut`）——**取代第十二輪候選 `bd5b844b…`**，移動原因就是本節那 7 支檔案改動 |
| 髒工作樹 | `git status --porcelain` ＝ **212 路徑**；同一棵樹的 sha 有**兩個不可互換**值：runner（`run_tests.sh:386`，原始位元組含尾端換行）`885c29f1e625f0e02322a2efff048b33ff0883722da1544934e5688c85109719`；review 產生器（`"\n".join`，不含尾端換行）`70d2337459f9133d13030d224bf2aebe9948e8537e74c89972feabf746eccc4d` |
| 受控釘選兩檔 | `backend/tests/collected_nodes.txt` sha256 `5260ece2…`（**3,811 列**）、`backend/tests/skip_budget.json` sha256 `c653b1a7…`（40 列）——本輪**一字未動**（§8.5） |
| 本輪綁定的 run dir | collect-only `sqlite-20261120T030957Z-8839`（collected **3,847**、exit **0**、digest 前後逐字相同）；本輪 36 支 added 節點 `sqlite-20261120T031108Z-9172`（explicit-selection、collected **36**、**36 passed／0 skipped**、exit **0**、`22.76s`）；整套 run 逐值見 §8.6 |
| 本輪改動的 7 支檔案 | `backend/tests/test_manual_decision_books_linked_portfolio.py`（M，＋863 −6）、`backend/tests/test_daily_model_estimate.py`（`??`）、`backend/tests/test_formal_skill_unit_authorization_endpoint.py`（`??`）、`frontend/src/components/workspace/OrderPreviewPanel.tsx`（M，＋155）、`frontend/src/app/experiments/[id]/simulation/page.tsx`（M）、`frontend/src/lib/dailyEstimate.ts`（`??`）、`frontend/tests/dailyEstimate.test.mjs`（`??`）。**其中沒有任何一支在 `backend/app/**` 之下**——這條由兩個獨立實物支持：①`find backend/app -newermt "2026-11-20 00:00"` ＝ **0 支**；②`d-patch-round12` 與 `d-patch-round13` 兩份**凍結包**的 sha256 對照：changed 6 支＋added 1 支＝上面這 7 支，**`backend/app/**` 一支都不在差集裡**（該目錄相對 HEAD 仍有 44 支髒檔，全部來自前幾輪，本輪未動）。⇒ 「不改 record route 計費、不為顯示改 quota、不按年齡改帳」是由**本輪零改動**滿足的，不是由宣稱滿足的 |
| 前端計量 | `frontend/tests/*.test.mjs` 全套 **195 支／195 passed／0 fail**（`~/.local/bin/node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/*.test.mjs`，11:2x 現測）；其中 `dailyEstimate.test.mjs` **43／43** |

### 8.1 客戶結果一：一個決策從「模型答過那一響」到「客戶讀得到」，逐層量完

層級說明：**①＝離線讀碼、④＝sqlite lane、⑤＝HTTP in-process（`TestClient`，未啟服務）、PG＝未批、瀏覽器＝NOT RUN**。本節每一格都標它停在哪個層級。

| # | 層 | 實測到的東西 | 節點／證據 | 層級 |
|---|---|---|---|---|
| 1 | **真實 predict 產生血緣** | `POST /api/v1/ml/personal/models/{version_id}/predict` 回來的 `inference_id` **就是**那筆 `UsageRecord.id`（`str(row.id) == served["inference_id"]`），且 metadata 帶 `prediction_type="personal_decision"`、`skill_unit_id`、`model_version_id`、`instrument_id`、`artifact_sha256` | `TestLineageFromAServedPrediction::test_a_served_prediction_originates_a_decision_in_both_stores`（`tests/test_manual_decision_books_linked_portfolio.py:2287`）；同一等式另在 `tests/test_formal_skill_unit_authorization_endpoint.py:528` 由計費節點 independently 釘住 | ⑤ |
| 2 | **面板提交 → 同一道門** | 面板 `submitManual()` 打的是 `POST /api/v1/simulations/{session_id}/manual-decision`（`frontend/src/components/workspace/OrderPreviewPanel.tsx:495`），與模擬頁同一 route；paper 桌門 `POST /api/v1/paper-portfolio/{portfolio_id}/decisions` 對同一血緣回 **201＋2 列** | 同節點 1 的後半段（兩道門都提交、兩本帳都讀回） | ⑤ |
| 3 | **DecisionRecord／HumanDecisionEvent／replay 帳本** | 三處同時留痕：`decision_records.linked_inference_id`、`human_decision_events`（`_replay_events(session_id)`）、`simulation_decisions.decision_detail` | 同節點 1 ＋ `TestLineageTravelsFromSubmitToExport::test_the_replay_ledger_row_holds_the_inference_the_request_named`（`:2046`）、`::test_the_events_own_door_shows_the_lineage_and_a_correction_keeps_it`（`:2146`） | ⑤ |
| 4 | **讀回（status／history）** | `decision-history` 的 JSON 逐列帶 `linked_inference_id` 且 `inference_source_state == "present"`，並附 `inference_model_version_id`／`inference_skill_unit_id`／`inference_as_of_date`／`inference_artifact_sha256` | 節點 1 ＋ `::test_the_simulation_press_reads_back_from_the_decision_history_file`（`:1832`） | ⑤ |
| 5 | **CSV／JSON 匯出** | 決策史 JSON＋CSV、交易日誌 JSON＋CSV 四份產物都帶同一 id（CSV 以欄位名逐字比對，不是只比行數） | 節點 1 的 export 段 ＋ `::test_a_human_decision_with_no_inference_exports_empty_lineage`（`:1876`） | ⑤ |
| 6 | **owner／skill 隔離** | 另一使用者既不能驅動該 replay、也讀不到該帳本（`:2486`）；別的技能帳號的 inference 被拒（`:1896`），門回 `inference_skill_mismatch` | `TestOwnership::test_another_user_cannot_drive_this_replay_or_read_this_book`（`:2486`）、`TestLineageTravelsFromSubmitToExport::test_an_inference_generated_for_another_account_is_refused`（`:1896`） | ⑤ |
| 7 | **hold 遇血緣** | HOLD 沒有決策列可掛血緣 ⇒ **400 `inference_needs_decision_row`**（不是繞過、不是靜默丟棄）；面板端由同一 `lineageState()` 派生，HOLD 連送都不送（§8.2） | `::test_a_hold_has_no_decision_row_to_carry_an_inference`（`:1924`）＋ §8.2 的 `lineageState` 表 | ⑤ |
| 8 | **已撤回** | 撤回（`revoke`）讓**同一版本的新 predict 回 409 `model_version_revoked`**，但**已服務出去的那筆 inference 仍是合法來源**：決策 200、兩本帳讀回逐字相同。撤回不改寫帳本 | `::test_an_origin_whose_version_was_taken_back_still_originates_the_decision`（`:2380`） | ⑤ |
| 9 | **重複提交** | 同一 `idempotency_key` 重放回**原始提交**、不产生第二張單；同鍵換 payload 被拒；被拒的那一響按同鍵重放回**同一個拒絕**；帳本寫失敗不留半套 | `TestRecoveryAndIdempotency` 四支：`:1318`／`:1352`／`:1377`／`:1449` | ④／⑤ |
| 10 | **拒絕後無寫入** | 五格拒因逐格跑完後，`orders`／`decision_records`／journal／decision-history／`human_decision_events` **全部零新增**（逐表 row count，不是只看 status code） | `::test_every_lineage_refusal_arrives_as_a_code_and_a_sentence_with_no_write`（`:2426`）、`::test_a_lineage_refusal_leaves_no_record_behind_on_either_door`（`:2024`） | ⑤ |

**五種拒絕的正式錯誤碼與可讀訊息**（提示：「所有拒絕以正式錯誤碼及使用者可讀訊息呈現，不吞錯、不 500」）——`tests/test_manual_decision_books_linked_portfolio.py:2434-2448` 的 corpus，逐格斷言 `status < 500`、`detail` 是**結構化 dict**、`detail["reason"]` 逐字相符、`detail["message"]` 是長度 >20 的字串：

| `reason` | HTTP | 觸發方式 | 兩道門是否同形 |
|---|---|---|---|
| `inference_not_found` | **404** | 傳一個不存在的 uuid | 同（桌門也 404） |
| `inference_metadata_unreadable` | **400** | metadata 不是 dict（`["not","a","dict"]`） | 同 |
| `inference_not_model_execution` | **400** | `prediction_type="market_direction"`（不是模型執行） | 同 |
| `inference_skill_mismatch` | **400** | 型別對、但 `skill_unit_id` 是別人的技能帳號 | 同 |
| `inference_needs_decision_row` | **400** | **合法** inference 碰上 HOLD（HOLD 沒有列可掛） | **僅模擬門**——桌門會自己建立決策列，故該格不適用於桌門，程式碼把這格單獨排除，不是漏掉 |

**上表五格的產生處（本輪逐行重讀）**：前四格在同一支共用水門 `app/services/decision_lineage.py`——`validate_linked_inference()` 定義於 `:19`，`inference_not_found` `:41`、`inference_metadata_unreadable` `:53`、`inference_not_model_execution` `:62`（判据 `metadata.get("prediction_type") != "personal_decision"` 在 `:58`）、`inference_skill_mismatch` `:75`（比對 `:67-70`：非字串或 `!= str(portfolio_id)` 即拒）。第五格**不在**水門裡，由呼叫端補：`app/api/simulations.py:2492`（只在決策是 HOLD 時走到）。⇒ 「兩道門同形」的來源是**共用同一支函數**，不是兩處各寫一遍；水門**刻意不驗 instrument**（§8.1 第 4 格的實測缺口即根於此）。

**未證明（不得由上表推上去）**：
1. **⑤ ≠ PG**：`validate_linked_inference` 的 raw-SQL 分支在 sqlite 上跑的是同一支 SQL 方言嗎？三道 pg_only 節點（`TestConcurrentLifecycle`，`:2534`／`:2580`／`:2645`）在本輪整套 run 裡仍是 skip ⇒ **真鎖下的重複提交／併發覆蓋＝NOT RUN**（A6／A7 未批准）。
2. **⑤ ≠ 瀏覽器**：面板那一跳的真實點擊（勾選→審閱→提交→畫面讀回）**NOT RUN**，屬 C 包，未批准。
3. **跨標的血緣是本輪量出來的產品事實，不是缺陷列表**：門只驗 owner＋`prediction_type`＋`skill_unit_id`，**刻意不驗 instrument**。節點 1 把這句話變成可失效的斷言——`rows[0]["asset_id"] == SYMBOL` 且 `served["instrument"] == "US:AAPL" != SYMBOL`（兩側都具名，因為空字串也能讓一個裸 `!=` 通過）。**要不要收緊是 owner 的產品裁決**，本輪只把它釘成「有人量過、有人寫下來了」。

### 8.2 客戶結果二：工作區 OrderPreviewPanel 是決策持久化入口，本輪接通了明選血緣

**先更正紀錄**（提示：「更正『面板不是決策持久化入口』的紀錄」）。上文 `:2161`（第十輪表）、`:2297`、`:2416`（第十一輪 §七 揭露 6）三處那句話在**本候選已過期**，本輪就地更正，不改寫歷史文字、只加更正標記（已於該三行尾端加上「〔第十三輪更正，2026-11-20〕」）：

| 舊紀錄寫了什麼 | 實測是什麼 |
|---|---|
| 「面板不是決策持久化入口」 | 面板 `submitManual()`（`OrderPreviewPanel.tsx:495`）打的正是 `POST /api/v1/simulations/{session_id}/manual-decision`——該 route 寫 `decision_records`＋`human_decision_events`＋`simulation_decisions.decision_detail`。**面板就是決策持久化入口**，與模擬頁同門、不同畫面。舊句講的是「它不寫 paper-portfolio 那張表」，那句話講錯了自己的門 |
| 「`grep -c linked_inference_id OrderPreviewPanel.tsx` ＝ 0」 | 那是**第十二輪候選**的量值。本輪現測：**5**（與模擬頁 `src/app/experiments/[id]/simulation/page.tsx` 的 **5** 同值；paper 桌頁 **3**）。行號 `:417-421` 也隨之位移 |

**怎麼接的（與模擬頁一致，不是複製一份）**：三個派生函式都住在 `frontend/src/lib/dailyEstimate.ts`——`lineageState()` `:270`、`LINEAGE_REFUSAL_REASONS` `:314`、`lineageRefusalOf()` `:335`、`estimateSourceFromStatus()` `:364`；面板 `:12-14` import，模擬頁只從 `:19` 的同一個 import 取用，兩檔都**沒有**本地重新宣告（測試用 `assert.doesNotMatch(source, /const LINEAGE_REFUSAL_REASONS/)` 釘住「不得各自宣告一支拒因集合」，`frontend/tests/dailyEstimate.test.mjs:581-585`）。

| 面 | 面板行為 | 位置 |
|---|---|---|
| 來源從哪來 | 只讀 `GET …/daily-estimate/status` 的 `last_estimate`，經 `estimateSourceFromStatus()` 過濾（`inference_id` 缺或空 ⇒ `null`） | `:245`／`:249` |
| 勾選／文案／payload | **同一支** `lineageState({has_source, selected, action})` 派生，三態 `attached`／`unselected`／`no_source`／`hold_no_lineage` | `:380-384`；勾選框 `:929` |
| 審閱要重新做 | `lineage === 'attached'` 時的 `inference_id` 進 `signature`：**換來源＝換決策**，審閱後再改勾或新估計到達都會令審閱失效 | `:404` |
| 提交時 | 只在明選時把 `linked_inference_id` 放進 payload（`if (review.linked_inference_id) payload.linked_inference_id = …`）；其餘參數（`action`／`idempotency_key`／`rationale`／條件性 `quantity`／`expected_version`）**與模擬頁同一組** | `:473`／`:494` |
| **沒有明選時＝現有行為** | payload 不带該欄；切技能帳號／換 session／建立審閱前都清掉勾選（`:256`／`:367`／`:415`）；審閱畫面明寫「**未明選來源（不會自動取最新估計）**」 | `:1098` |
| **不自動取最新估計** | 現測 `grep -c "setLineageSelected(true)"` ＝ **0**（唯一會把它設真的地方是 `:929` 那個 `e.target.checked`，即使用者自己勾的） | — |
| 錯誤提示 | 拒因由 `lineageRefusalOf(err.status, err.detail)` 辨認 → 清審閱＋清勾選＋顯示 `formatErrorDetail(detail)` 並在尾端附 `（reason：…）`；409 的鎖／同鍵衝突、其他 4xx、5xx 各有既有分支，**未吞錯** | `:520-527` |
| 提交後讀回 | 結果區顯示 `manualResult.decision.decision_detail.linked_inference_id`（門留下的，不是前端自己記的） | `:1187` |

**舊客戶端未傳 `selection`／未傳 `linked_inference_id` 時的結果**（提示要求「同步界定」）：**記錄為 null 血緣，絕不回退成「最近一次估計」**。後端由 `TestLineageTravelsFromSubmitToExport::test_a_press_that_named_no_inference_records_one_in_the_ledger_as_null`（`:2085`）釘住（帳本該欄為 `null`、決策照常成立），匯出層由 `test_a_human_decision_with_no_inference_exports_empty_lineage`（`:1876`）釘住（CSV／JSON 該欄是空，不是猜出來的 id）。**執行意圖（execution intent）沒有混進 paper decision**：面板提交的是**決策**（`manual-decision` 寫 `decision_records`／`human_decision_events`／`decision_detail` 三處留痕），`execution`／`fill`／`rejection` 只出現在**回應**裡（`OrderPreviewPanel.tsx:503-510` 把它們各自存進結果物件）；本輪**未新增**任何把 execution 狀態寫進決策列的路徑——證據是 §8.0 那條「`backend/app/**` 本輪零改動」，不是「有測試證明不可能」。

**測試層級與缺口**：前端測試是 **node:test 源碼層斷言**（`dailyEstimate.test.mjs:670-720` 讀面板原始檔的正規表示式，加 `:552-556` 對模擬頁的同一斷言、`:577` 對拒因集合的 `deepEqual`），**43／43 通過**、整套前端 **195／195**；它證明的是「碼接對了、只有一份、語意在源碼裡」，**不證明**「瀏覽器裡按下去會怎樣」——後者屬 C 包 e2e，**NOT RUN**（`e2e/` 312 支裡 `grep` 兩條旅程關鍵字仍為 0，見 §8.7）。

### 8.3 客戶結果三：#177 的三項缺口，各自一支會拒絕的節點

提示原文：「#177：補三項缺口：**候選失效或撤回的 selection**、**跨 owner 的 `skill_unit_id` 打到 predict**、**record route 的額度預留與結算次數**……**不要在 record route 增加對每日估計共用的去重或改變計費**……同步界定舊客戶端未傳 selection 時的結果。」四件事逐條交：

| 缺口 | 節點（`backend/tests/test_formal_skill_unit_authorization_endpoint.py`） | 量到什麼 |
|---|---|---|
| **① 候選失效／撤回的 selection** | `test_withdrawing_the_selected_version_refuses_inference_on_both_doors` `:597` | 先選 `model_a` 並正常服務一響（200）→ `revoke` 200 → **兩扇門**（具名 version 那扇、record route 那扇）都回 **409 `model_version_revoked`** 且帶 `detail.message_zh`（不是「找不到版本」那種掩飾）；帳本 `used/settled/calls` 在三格拒絕前後**逐筆相同**；日誌**保留**：`GET …/records/{id}/selections?skill_unit_id=` 的 `skill_active_version_id` 仍是被撤回那支、`GET …/models` 該列 `revoked_at` 非 null；**技能沒被卡死**——移到存活 `model_b`（200）後再 predict 回 200 且 `resolved_version.model_version_id == model_b`，**計費發生在那一次移動之後的服務**，前面的拒絕沒扣額 |
| **② 跨 owner 的 `skill_unit_id` 打到 predict** | `test_another_owners_skill_account_is_refused_like_one_that_does_not_exist` `:657` | 對「別人的技能帳號」與「隨機 uuid」各打兩扇門（4 格）：**一律 403 `no_skill_unit_selection`**、`detail.skill_unit_id` 回顯被猜的那個 id、帳本零變動、該 (record, foreign skill) 的選取列 `== []`；**門不區分「不存在」與「別人的」**，所以這不是一條存在性oracle；业主自己的帳號不受這些猜測影響，下一響照常 200 且**恰好扣一**（`used/settled/calls` 各 +1）。寫入門（`POST …/selections`）那一側另有 `test_a_foreign_skill_account_is_refused_and_leaves_no_row` `:216`（404 `skill_unit_not_found`）——**兩道門語意不同**是事實，已在節點 docstring 裡寫明，沒有混為「相同」 |
| **③ record route 的額度預留與結算次數** | `test_the_record_route_spends_exactly_one_unit_per_served_prediction` `:497` ＋ `test_a_prediction_that_dies_after_reserving_gives_the_unit_back` `:567` | ③-a：一響被服務的 predict ＝ `used+1`、`settled+1`、`calls+1`（三者**同時**、由 `_billing()` 讀帳本前後對比），且回給呼叫者的 `inference_id` 逐字等于那筆 `UsageRecord.id`、其 metadata 具名 `skill_unit_id`／`record_id`／`model_version_id`／`prediction_type="personal_decision"`。同節點再釘住三種**不計費**的形状：未選過的自有帳號 → 403 `no_skill_unit_selection`；record route 無別名可回落 → 409 `no_active_version`；已被新選擇取代的版本 → 403 `superseded_skill_unit_selection`——三格之後 `_billing()` **仍等於**服務那一響之後的值。③-b：artifact 在門已授權之後被人抽走 ⇒ 預留**不會留在非終態**（該節點讀回 `quota_reservations` 的非終態列數為 0、額度歸還同一格） |
| **④「不要改」的兩件事** | **零改動**（不是宣稱） | 本輪 7 支改動檔案**全部**在 `backend/tests/` 或 `frontend/`（§8.0 那行有兩條獨立證據：mtime 掃描 ＋ 凍結包 sha 差集）⇒ **record route 沒有新增「與每日估計共用的去重」、也沒有改變計費**；每日估計那一側的去重鍵仍是它自己的 window 鍵（`app/services/daily_estimates.py` 寫 `already_estimated_this_window`，本輪該檔 sha256 相對 `d-patch-round12` **未變**） |

**舊客戶端未傳 `selection` 時的結果（本輪界定，兩側）**：
- **推論門**：`test_predict_without_a_skill_unit_is_refused_as_an_invalid_request`（`:153`）——**兩扇 predict 門 × 兩種寫法（省略欄位／明確 `null`）＝ 4 格都回 422**，且 `detail[0]["loc"] == ["body","skill_unit_id"]`；請求**到不了 gate**，因此**既未被服務也未被計費**（docstring 明寫）。這不是本輪新增節點（在 3,811 釘選之內），但它是「舊客戶端」這句話的正式答案。
- **決策門**：未帶 `linked_inference_id` 的舊提交照常成立，血緣記為 **null**（不回退成最新估計）——見 §8.2 末段與 `:2085`／`:1876` 兩支節點。
- 具名 `version_id` 與帶 `skill_unit_id` 的請求：`test_each_skill_gets_its_own_version_from_the_unnamed_predict_route`（`:295`）釘住未具名那扇門的解析來自**該技能自己的選擇**（`resolved_version.resolved_from == "skill_unit_selection"`，`:309`）。**語義變更本身**（基準 `5a34e3f` 是 `version_id or record.active_version_id`，候選是三級解析）仍記在第十一輪 §七 揭露 10，**待 owner 裁決是否算進批准範圍**——本輪未改變它也未被它改變。

**層級**：①②③全部 **⑤ HTTP in-process**（`TestClient`，無服務行程）、DB 為 **④ sqlite**；**PG 層 NOT RUN**（A 包未批准）。

### 8.4 客戶結果四：第六個拒因——文案、列舉、讀回、舊資料相容都有測試

提示原文：「保留 failed／partial／沿用／撤回狀態。**新增第六個拒絕原因的前後端文案、列舉、讀回及舊資料相容都要有測試**。**不要按年齡改帳、不加自動接管，不為顯示而改 quota**。」

第六支拒因＝**`engine_failure`**：估計**已經開始**、其 worker 拋出異常。它**不是**拒絕（refused）而是**失敗**（failed），差別寫在 `app/api/personalization.py:270-274` 上方註解與下面的表格裡。

| 要的東西 | 實物 | 位置與斷言 |
|---|---|---|
| **後端文案** | `"engine_failure": "這個標的的每日估計在推論時失敗，這次沒有留下估計結果。"` | 表 `_DAILY_ESTIMATE_MESSAGES_ZH`（`backend/app/api/personalization.py:228`，該條在 `:274`，`:270-273` 是「這不是拒絕，是失敗」的註解）；落字點 `:511-514`；**未知代碼的回落句**「每日估計本次失敗，未留下估計結果。」就在 `:514`。**注意兩側回落句不同**：`last_refusal` 那側（`:506-510`）fallback 是「每日估計本次未執行。」，`last_failure` 這側才是上面那句——同一張表、兩個 caller，引用時要講哪一側 |
| **前端文案** | `failureNotice()`：`本次失敗（未落帳）：N 筆`／`此處點名 X` 或 `此行未點名標的`／有 `message_zh` 就唸它、沒有才印 `原因代碼 ${reason}` | `frontend/src/lib/dailyEstimate.ts:136-149`；測試 `frontend/tests/dailyEstimate.test.mjs:1034-1085`（含「同一列有句子時**不得**再出現 `原因代碼`」的 `doesNotMatch`） |
| **列舉** | 服務實際寫得出的拒因**共 6 支**：`already_estimated_this_window`、`consent_withdrawn`、`engine_failure`、`no_instruments_declared`、`skill_status_unreadable`、`skill_unit_not_found` | 產生者命令（本輪 11:2x 現測）：`grep -o '"reason": "[a-z_]*"' backend/app/services/daily_estimates.py \| sort -u` → **6 列**。測試端是**下限**斷言 `REFUSAL_REASONS.length >= 5`（`dailyEstimate.test.mjs:858`，**未放寬、也未改成等號**）＋ `includes(already_estimated_this_window)` ＋ `otherReason` 必存在；`:1119` 直接斷言服務源碼含 `"reason": "engine_failure"`、`:1123-1125` 斷言門的表格仍把它對到句子（**表被刪掉會紅**） |
| **枚舉邊界（未知拒因不得變成 500、也不得裸露代碼假裝是句子）** | 新節點 `test_a_failure_row_is_worded_at_the_door_whatever_reason_it_carries` | `backend/tests/test_daily_model_estimate.py:2500`：把已落帳 job 的 `payload["failures"]` 改寫成 `reason = "reason_this_build_has_no_sentence_for"` 再讀 `GET …/daily-estimate/status` ⇒ **200**、`failure["reason"]` 逐字保留、`message_zh` **非空且含「。」**、訊息裡**不回顯那個代碼**、且回落句 `!=` 表內句子（`:2565`／`:2577`）；同一節點再把 `engine_failure` 放回去，斷言 `message_zh == _DAILY_ESTIMATE_MESSAGES_ZH["engine_failure"]`（`:2584`）且 **`"RuntimeError" not in` 回應全文** |
| **讀回（重載後）** | `test_a_saved_failure_is_named_on_a_reloaded_card_without_its_exception_text` | `:2341`，本輪把「有句子就好」改成**按值比對**：`failure["message_zh"] == _DAILY_ESTIMATE_MESSAGES_ZH["engine_failure"]`（`:2400-2402`）並加 `assert "attributed_from" not in failure`（`:2399`）；異常文字只留在 job 列的 `errors`，**從不上線**（`dailyEstimate.test.mjs:1129` 斷言 status payload 裡 `"errors"` 不存在的欄位） |
| **舊資料相容** | `test_a_row_written_before_the_failure_field_is_attributed_without_guessing` | `:2413`：舊行只有 `"{skill}/{instrument}: {exc}"` 字首時，**只能**由字首歸因，並標 `attributed_from: "error_string_prefix"`（`daily_estimates.py:1330-1346`）；前端只在該旗標存在時多印一行「錯誤記錄字首讀回」，新行**不帶**這行（`dailyEstimate.test.mjs:1034` 那支 test 的 `:1077` 格） |
| **保留四態** | `pressOutcome()` 的 `estimated`／`partial`／`carried_over`／`nothing` 四格仍互不相同（`new Set(answers).size == 4`） | `dailyEstimate.test.mjs:863-878`；「沿用」仍綁 `already_estimated_this_window`，且**未扣額的那一響不算 partial** |
| **三件「不要」** | **零改動**：`backend/app/**` 本輪 0 支檔案被碰 ⇒ 沒有按年齡改帳、沒有自動接管、沒有為顯示改 quota；failed／partial／沿用／撤回的狀態機原封不動 | 同上 §8.3 ④ 那格同一條證據 |

**層級**：全部 **④／⑤**（sqlite＋in-process HTTP）；面板上「本次失敗（未落帳）」那一塊**在瀏覽器裡長什麼樣子仍未驗**——`DailyEstimatePanel.tsx` 的源碼層斷言在 `dailyEstimate.test.mjs:1098-1112`（含「元件內**不得**再手抄一份文案」的 `doesNotMatch`），瀏覽器層 **NOT RUN**。

### 8.5 基準重建：每個 base 值的來源與可驗證性，四段分離，逐節點差（含 skip 分類）

提示要求：「必須列出後續證據中每個 base 值的實際來源與可驗證性。分開描述『fixed base』『現有未批准釘選』『歷史受測候選』『今輪候選』，**不得拿 3,811 或 3,839 命名為已批准基準**……找回最後一次可證明獲准的選集與 skip budget 原件或批准紀錄，**缺失就標 UNKNOWN**。」

#### 8.5.1 四段分離（四個名詞各是一個不同的東西，不可互換）

| 段 | 是什麼 | 身份值 | 可驗證性（哪個命令量到的） | 能不能叫「已批准基準」 |
|---|---|---|---|---|
| **fixed base** | 唯一由 Git 固定的 commit（工作樹之下那顆頭） | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` | `git rev-parse HEAD` ＝ 同值 ⇒ **VERIFIED**；`git cat-file -t` ＝ `commit`；**8／8 倖存 run dir** 的 `identity.txt` 都帶同一行 ⇒ 每個 lane 證據都綁它 | ✅ 它是「基線」，但它是 **commit**：**不含**任何未提交候選。引用它等於引用一棵乾淨 clone |
| **現有未批准釘選 P** | 兩份受控檔＝選集＋跳過上限 | `backend/tests/collected_nodes.txt` **3,811 列**／sha256 `5260ece237d728a21637f79dcdcbad6b1ffd540b814ffa2361fbf0026cff3cbf`；`skip_budget.json`／`c653b1a77dfd45acf60fb7e515c3514de9f1d58c542e0d52eb37d0e4b59d95f8`（40 列，`collected=3811 total=179 pg_only=159 live_stack=18 other=2`） | 兩個 sha **MATCHES CURRENT**（`shasum -a 256`，Git 狀態 `??`＝兩檔都還沒進版本庫）；provenance 綁 run `sqlite-20261119T051257Z-80770`、樹 `5013060608…`、**該 run 自己的 `exit-code.txt` ＝ 1** | ❌ **批准紀錄查無 ⇒ UNKNOWN**。這兩檔證明「有人釘過」，**不**證明「有人准了」；而且釘它們的那次 run **本身就是非零 exit** |
| **歷史受測候選 C** | 第十一輪那整套綠燈綁的樹 | `5d9dff9c7d4b204320fd8d9de0d9f23ccbfb7b25bfd8099434e5da6f48876c3f`（collected **3,837**）；同輪早期候補 `3fbb810e…`；第八輪候補 `a2641cca…` | **ATTESTED BY RUN DIR**：`5d9dff9c…` 出現在 4 個倖存 run dir、`3fbb810e…` 在 2 個（`identity.txt` 的 `tree-digest-before/after`）；`a2641cca…` 在 8 個倖存 dir 裡**一個都沒有** ⇒ **UNKNOWN / NO SURVIVING ARTIFACT** | ❌ 是「跑過、量過、有實物」，**不是**「批准」 |
| **今輪候選** | §8.0 這棵樹 | `75d2cfdde8ed78e40718def127f69785dc792d9ab96d6af876badbde195c05f8`（**526** 支 `backend/**/*.py`、collect **3,847**） | `run_tests.sh:345-351 tree_digest()` 今日**三次重算逐字相同**（两次在 run 前後、一次在產生 D 包之後） | ❌ 未批准、**未重釘**、D 包以外沒有「准許發布範圍」的批准 |

**「最後一次可證明獲准的選集」＝缺失，不是未找**。A 包（3,548 節點）的原件 run dir `/tmp/alethra-test-logs/final-20261117/run` **不在本機**（`os.path.isdir` 現測 False），紀錄裡只剩 `:1484` 與 `:2122` 兩行**文字**引用（含 provenance 字串 `collected-node-ids: 3548`、樹 `72dcb3d1…`）⇒ **ORIGINAL ARTIFACT MISSING / NOT VERIFIABLE**。後果要講清楚：**A→P 這一段（3,548→3,811，差 263）不可逐節點量測**，能量的只有 **P→C** 與 **C→今輪** 兩段。任何「從獲准基準到今輪共 +N」的寫法都是把「不可量」混進「已量」，本節不那樣寫。

#### 8.5.2 base 值溯源表（18 格全量，產生器與存檔）

全部由 `alethra-evidence/tools/check_base_values.py` 重算，**不跑套件**（除標明者），存檔 **`alethra-evidence/baseline-round13/`**（`BASE_VALUES.json` 21,336B／`BASE_VALUES.md`／`SELECTION_DIFF_Pinned_to_Candidate.txt` 4,843B／`README.txt`／`SHA256SUMS.txt`，`shasum -a 256 -c` **5／5 OK**）。判定分佈（逐格明細見該包 `BASE_VALUES.md`）：

| 判定 | 幾格 | 是哪幾類 |
|---|---|---|
| **VERIFIED / MATCHES** | 6 | HEAD；兩份受控檔 sha；`fa3d0862…`（第十一輪對照快照 md5）；`7851af44…`（P→C11 的 ack digest，由兩份**逐字存檔**清單重算復現）；`5d9dff9c…`／`3fbb810e…`（run dir attest） |
| **MOVED（快照式值，內容一動就變）** | 3 | 第十二輪候選 `bd5b844b…`→今輪 `75d2cfdd…`；兩個 porcelain sha（`8c3a8900…`→`885c29f1…`、`4d944dc3…`→`70d23374…`，211→**212** 路徑） |
| **DOES NOT REPRODUCE** | 1 | 第十輪那條 ack digest `ff569c22ae5771944ea5fccddac50ab55425e6d3119c8017244c37f46a948d5`：由 `alethra-evidence/repin-round10/{pinned-3811.sort,live-3817.sort}` 重算得 **`bec44a2bf6a2797f9a102765532332abe88656f3153816883fc7c439e3296693`**（`-0 +6`，非紀錄寫的 `+9`），而且紀錄引用它那幾行（`:2190`／`:2205`／`:2315`／`:2321`）帶的串**只有 63 個 hex 字元**（少一位）⇒ **該值不可重現**，不得再作為釘選依據引用 |
| **UNKNOWN / NOT AN OBJECT** | 2 | `5a33f527`（第十輪 `:2518` 聲稱的 base：`git cat-file -t` → `fatal: Not a valid object name`，且在 `git rev-list --all` 的 **584** 顆可達 commit 裡**前綴相符 0 顆**）；第八輪候選 `a2641cca…`（無倖存實物） |
| **MEASURED BUT UNDOCUMENTED UNTIL NOW** | 3 | `17e8e7ae…`、`5d897a04…`（第十一日 run dir 裡有、紀錄從未引用）、`75d2cfdd…`（今輪兩支 run dir）——**本節把它們補進紀錄**，這是「紀錄不全」的事實，不是新的主張 |
| **ORIGINAL ARTIFACT MISSING** | 1 | A 的 3,548（§8.5.1） |
| **SCOPE-SPECIFIC** | 2 | `3010b711…`（`-- backend frontend` 過濾後的髒路徑集，與整樹值**不可互換**）；`-0 +36` 那格只在「今輪 collect」這個範圍成立 |

#### 8.5.3 從 P 到今輪候選的完整逐節點差（含 skip 分類）

三段都是**實測集合差**（`scripts/run_evidence.py` 的 `manifest_ids()`＋`diff_block()`），不是算術推得；完整 36 列逐字在 `alethra-evidence/baseline-round13/SELECTION_DIFF_Pinned_to_Candidate.txt`（body sha256 **`15001ec26b8fcfa3d89eabab03e8bbb6be9b52bf150b4a7fadc5647b0120c2c2`**）。

| 段 | 從 | 到 | removed | added | 怎麼量的 |
|---|---|---|---|---|---|
| A→P | 3,548（獲准基準，**原件缺失**） | 3,811 | **不可量測** | 不可量測 | 只有兩行文字引用 ⇒ 標 UNKNOWN（§8.5.1） |
| **P→C** | 3,811（釘選） | 3,837（第十二輪候選 collect） | **0** | **+26** | 兩份**逐字存檔**清單（`baseline-round12/{pin-P3811.verbatim.txt,tested-C3837.node-ids.txt}`）重算，ack digest `7851af44…` **REPRODUCED** |
| **C→今輪** | 3,837 | **3,847**（今輪 collect） | **0** | **+10** | `baseline-round13/` 的两份清單＋今輪 collect run `sqlite-20261120T030957Z-8839` |
| **P→今輪** | 3,811 | **3,847** | **0** | **+36** | 同上直接對比；**加性檢查** 3,811＋36 ＝ 3,847 ✓ |

**C→今輪那 +10 支逐支**（依 §8.1–§8.4 的分佈）：

| 檔 | 節點 | 所屬客戶結果 |
|---|---|---|
| `tests/test_daily_model_estimate.py` | `test_a_failure_row_is_worded_at_the_door_whatever_reason_it_carries` | §8.4 枚舉邊界 |
| | `test_a_row_written_before_the_failure_field_is_attributed_without_guessing` | §8.4 舊資料相容 |
| | `test_a_saved_failure_is_named_on_a_reloaded_card_without_its_exception_text` | §8.4 讀回（本輪改成按值比對） |
| `tests/test_formal_skill_unit_authorization_endpoint.py` | `test_the_record_route_spends_exactly_one_unit_per_served_prediction` | §8.3 缺口③ |
| | `test_a_prediction_that_dies_after_reserving_gives_the_unit_back` | §8.3 缺口③-b |
| | `test_withdrawing_the_selected_version_refuses_inference_on_both_doors` | §8.3 缺口① |
| | `test_another_owners_skill_account_is_refused_like_one_that_does_not_exist` | §8.3 缺口② |
| `tests/test_manual_decision_books_linked_portfolio.py` | `TestLineageFromAServedPrediction::test_a_served_prediction_originates_a_decision_in_both_stores` | §8.1 層 1–6 |
| | `…::test_an_origin_whose_version_was_taken_back_still_originates_the_decision` | §8.1 層 8 |
| | `…::test_every_lineage_refusal_arrives_as_a_code_and_a_sentence_with_no_write` | §8.1 層 10＋五格拒因 |

（P→C 那 +26 支的逐字清單在 diff 檔前半段，主要是 `test_daily_estimate_two_workers.py` 16 支＋`test_daily_model_estimate.py` 5 支＋`test_manual_decision_books_linked_portfolio.py` 5 支；**它們屬前次會話的工作樹**，本輪**未追認**其為「獲准」，只把它們算進集合差。）

**skip 分類**（提示明文要求「含 skip 分類」）：
- **P 的上限**：`total=179／pg_only=159／live_stack=18／other=2`（`skip_budget.py` 三類，`categories sum to 179`）——這是**對 3,811 那棵樹、那次 exit-1 run 的 console census** 量的，不是對今輪。
- **+36 這批 added 節點**：explicit-selection lane run `sqlite-20261120T031108Z-9172` 實測 collected **36**／**36 passed**／**0 skipped**／exit **0**（該輪 selection-kind 是 `explicit-selection`，故 ceiling **不適用**、只報不 enforce；`check` 對該 run 回 ledger closes）。⇒ **+36 沒有帶來任何新的 pg_only／live_stack／other 跳過**。
- **今輪那 3 支仍會跳過的節點**：`TestConcurrentLifecycle::{test_two_requests_on_one_idempotency_key_leave_exactly_one_order, test_a_stale_account_read_cannot_overwrite_a_committed_booking, test_a_manual_submit_racing_an_advance_stays_consistent}`（`tests/test_manual_decision_books_linked_portfolio.py:2534`／`:2580`／`:2645`）——分類 **pg_only**（原因欄逐字 `row-lock serialisation requires a real PostgreSQL backend`），**已在 P 的 159 之內**、不是本輪新增；它們是 A6／A7 那兩格，**未批准 ⇒ NOT RUN**。
- **整套 census 對今輪候選**：見 §8.6（本輪跑了，逐值在該節；未跑之前它只能是 NOT MEASURED）。

**未批准釘選造成的偏差（具體後果，逐條可核）**：
1. **lane 閘門會拒絕這棵樹**：`tests/collected_nodes.txt` 是 3,811、今輪 collect 是 3,847。兩道閘各自的**來源**（本輪逐行重讀，行號與第十輪不同：`run_tests.sh` 已長到 494 行）：
   - **census 閘**＝`skip_budget.py`（`run_tests.sh:472` 呼叫、`:481` 收 `BRC`）。那句「this whole-suite run collected N tests but the budget pins 3811」由它產生：`scripts/skip_budget.py:204-210` 的 `if collected != budget["collected"]: raise Refused(...)`，`Refused` 在 `:255-257` 統一轉 **return 2**。⇒ **這句話屬 census 閘（rc 2）**，不屬 ledger 閘。
   - **ledger 閘**＝`run_evidence.py <run dir> check`（`run_tests.sh:474-476` 呼叫、`:483` 收 `ERC`）；它对「今輪 collect 有未認賬的 manifest 差異」走 `scripts/run_evidence.py:481` 的 **return 1**（`Refused` 才是 `:577-579` 的 2）。
   - **覆蓋順序**：`:484` `GATE=$BRC`、`:485` `[ "$ERC" -ne 0 ] && GATE=$ERC` ⇒ ERC 覆蓋 BRC；`:486-488` 只在 pytest 本身 `RC=0` 時把 `RC` 改成 GATE。
   ⇒ 實際 **exit code 取決於本樹兩閘各自的實測 rc**，不靠推預測；§8.6 報逐字輸出與實測 `BRC／ERC／exit`。可以斷言的只有結構：**在重釘之前，本樹的整套「綠燈」在正式 lane 上不可能被記錄為 PASS**，§8.6 那組數字的官方狀態是「被證據閘拒絕」，不是「通過」。
2. **36 支節點沒有釘選背書**：其中 26 支屬**前次會話**工作樹、10 支屬本輪（上表）。它們**有 run dir 實物**，但**不在**任何被批准過的選集裡。
3. **上限（179／159／18／2）是對另一棵樹量的**：拿它評判今輪是把兩棵樹的數字混用；§8.6 用今輪的 console census 對它做**獨立核對**，符合≠批准、不符≠退步。
4. **釘選檔自身是 untracked（`??`）**：`git diff 5a34e3f -- backend/tests/collected_nodes.txt` 沒有東西可比，**fixed base 裡沒有這份帳本** ⇒ 偏離只能靠 sha＋紀錄文字追溯，這正是第十輪那條 63 字元 digest 沒人發現的原因（§8.5.2）。

---

### 8.6 整套受測跑完：逐值（run dir 實物，非摘要轉述）

§8.5.4 後果 3 承諾「§8.6 用今輪的 console census 對上限做獨立核對」，後果 1 承諾「實際 exit 由本節實測 `BRC／ERC` 決定」。**兩者都在本節兌現**。

#### 8.6.1 這組數字屬於哪棵樹（身份先釘）

| 項目 | 逐字值（取自 `$RUN/identity.txt`） |
|---|---|
| 命令 | `cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh sqlite`（**無** node id 參數 ⇒ `selection-kind: whole-suite`） |
| run dir | `$TMPDIR/alethra-runs/sqlite-20261120T032315Z-12706`（lane 自己印的 `run dir:` 那行） |
| 起訖 | 03:23:15Z 起、12:03 本地（04:03Z）止；pytest 自報 **2427.47s（0:40:27）**；第十一輪整套 39:43 ⇒ 同量級 |
| 指紋 | `lane fingerprint: python 3.12.16 \| pytest 8.3.3 \| sqlalchemy 2.0.36`；target `sqlite $TMPDIR/alethra-sqlite-test-12706.db`；provider `DATA_PROVIDER=demo` |
| Git | `git: 5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`＋`porcelain: 212 path(s) sha256 885c29f1e625f0e02322a2efff048b33ff0883722da1544934e5688c85109719`（**runner 的原始位元組慣例**，與 D 包產生器的 `70d23374…` 不可互換，見 §8.0） |
| 候選 | `tree-digest-before` ＝ `tree-digest-after` ＝ **`75d2cfdde8ed78e40718def127f69785dc792d9ab96d6af876badbde195c05f8`**；lane 另印 `tree changed during run: no (python digest 75d2cfdd… before and after)` |
| 本節寫完後再量一次 | 04:06:50Z 以 `run_tests.sh:345-351` 那條 `find . -name '*.py' … \| LC_ALL=C sort \| xargs shasum -a 256 \| shasum -a 256` **逐字重跑**＝同一個 `75d2cfdde8ed…`。⚠️ 用較窄的 `find app tests` 會得到 `0c060931…`——**那不是我們的候選**，是另一種算法；本樹的候選只認 lane 那條算式。 |

#### 8.6.2 結果（三個計數物件，分開報）

| 計數物件 | 逐字值 | 由誰產生 |
|---|---|---|
| **collect** | `collected-node-ids: 3847` ／ `selection on this tree: 3847 tests collected in 3.86s` | 該 run 的 `collect.txt` |
| **pytest 控制台** | `======== 3671 passed, 179 skipped, 11313 warnings in 2427.47s (0:40:27) ========` | `pytest.log` 尾行 |
| **junit** | `tests='3850' failures='0' errors='0' skipped='179' time='2427.395'` | `$RUN/junit.xml` 的 `<testsuite>` 屬性 |
| run 自己的 `exit-code.txt` | `0`——lane 明文標為 **`pytest's own status, recorded before any gate`**，**不是**整套的最終判定 | `run_tests.sh` 的產物清單那行 |

**3850 vs 3847 的差（3）不是謊，是兩個計數物件**：junit 另有 3 列 `classname=""`、message `collection skipped` 的**模組層**條目——`tests.test_crypto_bar_harvest_pg`、`tests.test_crypto_paper_pg`、`tests.test_strategy_study_timestamps_pg`（整支檔案在 collection 階段就 skip，因此**從不**以 node id 出現在 `collect.txt`）。帳本自己就把這個差拆開印了（逐字）：
```
junit records      tests=3850 skipped=179 failures=0 errors=0
skip categories    pg_only=159 live_stack=18 other=2 (total 179 = item 176 + collection 3)
console census      units=179 pg_only=159 live_stack=18 other=2 -> agrees with the recorded outcomes
collection-level   3 module(s): tests.test_crypto_bar_harvest_pg, tests.test_crypto_paper_pg, tests.test_strategy_study_timestamps_pg
reported units     3850 = collected 3847 + collection-level 3 -> OK
unresolved records  0
duplicate records   0
collected but no record 0
record without id   0
```
⇒ **3,671 passed ＋ 176 node-skip ＝ 3,847**（與 collect 逐一对齐）、**＋ 3 collection-skip ＝ 3,850**（與 junit 对齐）；`failures=0`、`errors=0`、無未終態、無重複。**0 failed 是本次整套run最重要的那個數字**：本輪動過的 7 支檔、以及 §8.1／§8.2 那 10 支新節點，**在整套順序下沒有弄壞任何一支既有測試**。

#### 8.6.3 console census 對「未批准上限」的獨立核對（§8.5.4 後果 3 兌現）

逐字（`skip_budget.py` 對本次 `pytest.log`）：
```
skip census of …/sqlite-20261120T032315Z-12706/pytest.log: 179 units pg_only=159 live_stack=18 other=2 (categories sum to 179)
budget …/backend/tests/skip_budget.json: collected=3811 total=179 pg_only=159 live_stack=18 other=2
```
| 類 | 今輪整套實測 | P 的釘選上限 | 差 |
|---|---|---|---|
| `total` | 179 | 179 | **0** |
| `pg_only` | 159 | 159 | **0** |
| `live_stack` | 18 | 18 | **0** |
| `other` | 2 | 2 | **0** |

⇒ **今輪這棵樹的跳過結構與 P 逐類相同**（+36 支一支都沒新增跳過）。同時，把 junit 的 `skipped` 訊息做粗分桶會得到 `156／18／5`——差 3 全在本節 8.6.2 那三支**模組層 collection skip**（`156＋3＝159`、`5−3＝2`）：console census 用 `tests/postgresql_only.py:154` 的 `PG-ONLY […]` 前綴把它們歸入 `pg_only`，而我那句「看 message 關鍵字」的旁核看不到前綴。**兩個數字的物件不同，帳本的那一列才是判準**。符合 **不等於批准**（上限仍是對 3,811 那棵樹量的），但它把「+36 會不會偷渡新跳過」這個具體疑慮**量掉了**。

#### 8.6.4 两道證據閘與真正的 exit code（§8.5.4 後果 1 兌現，不再預測）

| 閘 | 呼叫 | 收 rc | 逐字輸出 |
|---|---|---|---|
| census | `run_tests.sh:472` → `skip_budget.py … check` | **`BRC=2`**（`:481`） | `refused: this whole-suite run collected 3847 tests but the budget pins 3811: the selection changed (files added, removed or deselected), so the ceiling was not measured against this suite. Re-measure and regenerate the budget from a real lane log.`（產生處 `scripts/skip_budget.py:204-210`，`Refused` → `:255-257` 回 2） |
| ledger | `run_tests.sh:474-476` → `run_evidence.py <run dir> check` | **`ERC=1`**（`:483`，走 `scripts/run_evidence.py:481`） | `EVIDENCE GATE REFUSES:` ＋ `  - 36 node(s) are collected but not pinned; the pinned selection is re-written only by \`pin --ack-diff\`, never by accepting the newest total: tests/test_daily_estimate_two_workers.py::test_every_race_criterion_has_been_seen_to_refuse_something, …`（列舉到第 5 支即截斷，**完整 36 支清單在 `baseline-round13/SELECTION_DIFF_Pinned_to_Candidate.txt`**，sha 見 §8.5.3） |
| 覆蓋 | `:484` `GATE=$BRC`、`:485` `[ "$ERC" -ne 0 ] && GATE=$ERC`、`:486-488` 只在 pytest `RC=0` 時改寫 | **`GATE=1`** | `not green: pytest passed but the evidence gates refused (skip census 2, ledger 1).` |
| 最終 | 包裝命令 `echo "WRAPPER_RC=$?"` | **`WRAPPER_RC=1`** | — |

⇒ 本輪整套的**官方狀態＝被證據閘拒絕（exit 1）**，而**被拒的理由是「3,847 對 3,811 的選集變動」，不是任何一支測試失敗**。第十二輪 §5.4 那句「census rc=2／ledger rc=1」在**今輪這棵樹上第一次同時被量到**（前幾輪只出現過 census 拒絕）；§8.5.4 改正後的歸因（那句話屬 census 閘）與實測一致。
**重釘（`pin --ack-diff`）能消掉這兩道拒絕，但那是 owner 的決定、本提示明文未批准**，本輪**沒有**執行；`tests/collected_nodes.txt`（sha `5260ece2…`、3,811 列）與 `tests/skip_budget.json`（sha `c653b1a7…`）本輪**一字未動**（§8.0）。

#### 8.6.5 今輪那 +36 支節點：兩種順序都量過

| 順序 | run dir | 逐字 | 判讀 |
|---|---|---|---|
| **隔離**（只跑這 36 支） | `sqlite-20261120T031108Z-9172`，`selection-kind: explicit-selection`，tree digest 同上 | `36 passed, 278 warnings in 22.76s`；census `0 units pg_only=0 live_stack=0 other=0`＋`this run selected specific tests, so the whole-suite ceiling does not apply; the census is reported but not enforced.`；ledger `reported units 36 = collected 36 + collection-level 0 -> OK`／`collected but no record 0`／`record without id 0`／`evidence gate: ledger closes against this run's own artifacts`；`rc=0` | **36／36 passed、0 skipped、0 failed**，且這格的 ledger 是**關得起來的**（因為它不拿 3,811 比對） |
| **整套順序** | `sqlite-20261120T032315Z-12706` | 36 支的 node name **全部不在** junit 的 179 筆 skipped 裡（`of the 36 added node names, how many appear as skipped: 0`），而整套 `failures=0／errors=0` | ⇒ 36 支在**有前序污染的真實順序**下同樣全過。這是「正向→反向→正向」裡那個反向：隔離綠 ≠ 整套綠，本輪兩者都量了。 |

#### 8.6.6 三支 pg_only 併發節點在本 run 的實際狀態（A6／A7 NOT RUN 的實物根據）

`$RUN/junit.xml` 裡這三列的 skip message 逐字皆為 `row-lock serialisation requires a real PostgreSQL backend`（檔內來源 `tests/test_manual_decision_books_linked_portfolio.py:142`，三支 def 在 `:2534`／`:2580`／`:2645`，class 在 `:2527`）：
`TestConcurrentLifecycle::test_two_requests_on_one_idempotency_key_leave_exactly_one_order`、`::test_a_stale_account_read_cannot_overwrite_a_committed_booking`、`::test_a_manual_submit_racing_an_advance_stays_consistent`。
⇒ **真鎖下的重複提交／併發覆蓋／搶 advance 一致性＝NOT RUN**（A7 那格，未批准）。另 `tests/test_daily_estimate_two_workers.py` 在本 run 是 `.s………`（18 支：17 passed、1 skipped）——跳過的是 `:607` 那支 `test_two_processes_on_one_window_bill_each_business_key_once`（**真雙行程同窗計費**，A6 的核心），其餘 17 支（含 §8.5.3 新增的 16 支 verdict 自核）皆 off-line 可跑且已過。**A6 的等鎖證據仍然只能由 PG 給，本節沒有偽裝它。**

#### 8.6.7 本節產生了什麼、沒產生什麼

- **產生**：一個 run dir（lane 自己寫的 5 份產物：`identity.txt`／`collect.txt`／`pytest.log`／`junit.xml`／`exit-code.txt`）＋ `$TMPDIR/r13-fullsuite-wrapper.log`（我這層包裝的 console 存檔）＋本節文字。sqlite 測試庫檔在 `$TMPDIR`（lane 預設落點，`alethra-sqlite-test-12706.db`）。
- **未產生**：**沒有**動 `backend/**` 任何一支受測檔；**沒有**重釘；**沒有** PG／服務／瀏覽器／外呼／Git 寫入。候選 digest 在 run 前後與本節寫完後**三次同值**（§8.6.1）。

---

### 8.7 可審批的**確切操作**（A／B／C／D）——本節是第十三輪的值，用來**替換**第十二輪 §5.x 裡已過期的每一格

提示明文：「A0 僅作既有授權的唯讀確認；列明是否會新寫證據檔。A1–A7 不執行。固定完整候選、空庫→head 血緣、執行目錄、有效 DSN 來源、角色、實際命令；展開每支 node id……不以時間戳、429 或 SQLite 樣本取代。B 一個唯一隔離 DB……證明不載入現有 `.env` 的非預期設定……C 單一建置／啟動模式……自然 cron、瀏覽器自動化、本人同意分開。D 先交 182 檔……」——**本節不執行其中任何一格**，只交可逐格批准的正文。

#### 8.7.0 本輪改掉了第十二輪 §5.x 的哪些值（先讓 owner 看見「改了什麼、憑什麼改」）

| # | 第十二輪原文 | 本輪實測 | 影響 |
|---|---|---|---|
| 1 | 完整候選 `bd5b844b…` | **`75d2cfdde8ed78e40718def127f69785dc792d9ab96d6af876badbde195c05f8`**（§8.6.1 三次同值） | 所有 A／B／C 格綁定的樹換了 |
| 2 | 髒路徑 211 | **212**（porcelain 兩個慣例值：runner `885c29f1…`／產生器 `70d23374…`） | D 包範圍、重現敘述 |
| 3 | C 包「產物隔離 `NEXT_DIST_DIR="$RUN/next-dist"`」 | **該變數在 Next 14.2.18 无人讀**；本樹 `frontend/next.config.js:7` 讀的是 **`NEXT_DEV_DIST_DIR`**（`distDir: process.env.NEXT_DEV_DIST_DIR \|\| '.next'`） | **照舊文執行會靜默覆寫共用 `.next`（199 MB）還自認已隔離**——本輪最實質的一項更正 |
| 4 | C 包前端起 `3031`、e2e 未提 base | `playwright.config.ts:5` `E2E_FRONTEND_BASE ?? 'http://localhost:3001'` ⇒ 不起 `E2E_FRONTEND_BASE` 時 **e2e 打 3001**，與 3031 無一相符 | 舊文的 C 包若被批准，測試會連錯埠 |
| 5 | A3「同時滿足 `test_safety_gate.py:26-30` 白名單」 | 檔在 **`backend/test_safety_gate.py`**（**不是** `backend/tests/`）：`ALLOWED_TEST_DATABASES` `:25-30`（`alethra_owner_test` 在 `:29`）、`FORBIDDEN_DATABASES` `:33-38`、`ALLOWED_INSTANCES` 僅 5432 `:41-45`、四旗標 `:110`、`verify_test_database()` `:156`；lane 於 `run_tests.sh:162` 匯入、**拒絕時 exit 3**（`:172`） | 白名單約束是真的，但路徑與 exit 語意舊文寫錯；本輪逐行重讀 |
| 6 | A5「`c3d4e5f6g7h8_*.py:88-93`／`d4e5f6g7h8i9_*.py:85-89`」 | 前者：欄位在 `:88`、`op.create_index('ix_decision_records_linked_inference_id', …)` 在 **`:90-93`**（前置條件 refusal `:73-83`）；後者：常數 `INDEX` 在 **`:47`**、`op.create_index(INDEX, 'human_decision_events', ['linked_inference_id'])` 在 **`:89`** | 索引存在这件事是真的；行號要按本輪重引 |
| 7 | A6「`Barrier(2)` 在 `:218`／`:418`、`_TIMEOUT_S` 在 `:127`、timeout 在 `:189-191`」 | `tests/test_daily_estimate_two_workers.py`：**`_TIMEOUT_S = 60` `:170`**、`SET lock_timeout='30s'／statement_timeout='120s'／idle_in_transaction_session_timeout='90s'` **`:241-243`**、`mp.get_context("spawn")`＋`ctx.Barrier(2)` **`:269-270`（自核節點內）** 與 **`:650-651`（雙行程計費節點內）** | 該檔本輪多了 16 支 verdict 自核節點（§8.5.3），行號整體下移 |
| 8 | B 包「`.env` 內含 `TWELVEDATA_API_KEY` 與 `DATA_PROVIDER=twelvedata`」 | 本輪現讀 `backend/.env`（**只報非秘密值與存在性**）：`DATA_PROVIDER=twelvedata` ✓；**`TWELVEDATA_API_KEY=` 是空值**；真正的秘密是 `JWT_SECRET_KEY`（SET，值不印）；**并且 `RESEARCH_AUTH_BYPASS_ENABLED=true`、`CANDIDATE_PASSWORDLESS_MODE=true`、`LIVE_TRADING_ENABLED=true`、`DATABASE_URL=postgresql://<masked>@localhost:5432/ai_investment_lab`** | 「必須覆蓋」從假設變成**實測的危險預設**：`.env` 指的 DB 名字正好在 `test_safety_gate.py:33-38` 的黑名單裡 |
| 9 | B 包把 `LIVE_TRADING_ENABLED=false` 列為必要覆蓋 | 該旗標**全倉庫只有宣告、沒有人讀**：`grep -rn "live_trading_enabled\|LIVE_TRADING_ENABLED" backend --include=*.py --include=*.sh`（排除 `.venv`／`__pycache__`）只命中 `app/config.py:172` 與 `tests/test_research_run_recovery.py:298` 那句話——倉庫自己稱它為 **false affordance**（「a setting nobody on a formal path reads is how `LIVE_TRADING_ENABLED` became a false affordance」） | 覆蓋它仍是**深度防禦**，但 B 包「不會啟動實單」的保證**不得掛在這個旗標上**（見 §8.7.4 判準 ⑧） |
| 10 | D 包 182 檔 | **183 檔**（127 tracked patch ＋ 56 untracked 全檔）、`+14,182／−2,743` 是 **127 份 patch 的行數**、`artifact_bytes=2,114,353` | §8.7.6；212−183＝29 的逐檔拆解見該節 |

#### 8.7.1 固定值（2026-11-20 現測；本輪全部重量，沒有沿用舊數字）

| 項目 | 逐字現測值 |
|---|---|
| HEAD | `git rev-parse HEAD` ＝ `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` |
| **今輪完整候選** | `75d2cfdde8ed78e40718def127f69785dc792d9ab96d6af876badbde195c05f8`（算式＝`run_tests.sh:345-351`，**只能**用它复現；§8.6.1） |
| PG 二進位 | `PGB=$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin`：`ls -1 \| wc -l` ＝ **32**；`initdb`／`psql`／`pg_isready`／`pg_ctl --version` **四者皆 16.2**（逐字 `initdb (PostgreSQL) 16.2`）——本輪重跑，與第十二輪同值 |
| 執行目錄（**尚未建立**） | `RUN=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r13-run-XXXXXX")`；`PGDATA=${TMPDIR:-/tmp}/alethra-r13-pgdata`；`SOCKDIR=${TMPDIR:-/tmp}/alethra-r13-sock`。本輪 04:06:33Z 現測：後兩者 **`No such file or directory`** ⇒ 新建、不覆寫既有 |
| 環境前提（唯讀，04:06:33Z 現測） | `pgrep -fl postgres` ＝ **無輸出**；`pgrep -fl uvicorn` ＝ **無輸出**；`pgrep -fl "next start\|next-server"` ＝ **無輸出**；`lsof -nP -iTCP:{5432,8000,8031,3031,3001} -sTCP:LISTEN` ＝ **各 0 列**；`/tmp/.s.PGSQL.5432` ＝ **不存在** |
| migration 尾端 | `DATA_PROVIDER=demo .venv/bin/python scripts/check_migration_tail.py --json`（讀**檔案系統**的 ScriptDirectory，不連 DB）：`status=OK`、`head=d4e5f6g7h8i9`、`parsed_heads=["d4e5f6g7h8i9"]`（單一）、`expected_tail`＝`parsed_tail`＝`[f2a3b4c5d6e7, b2c3d4e5f6g7, c3d4e5f6g7h8, d4e5f6g7h8i9]`、`revision_count=57`、`module_count=57`、`unreachable_from_head=[]` |
| 受控釘選檔 | `backend/tests/collected_nodes.txt` sha256 `5260ece2…`（**3,811 列**）、`backend/tests/skip_budget.json` sha256 `c653b1a7…`——本輪**未動**（§8.0、§8.6.4）；今輪整套 collect 是 **3,847** ⇒ 兩檔**都不描述這棵樹**（§8.5.3） |

#### 8.7.2 A0＝既有授權的唯讀確認：**它會不會新寫證據檔**（提示明文要求回答）

| 格 | 動作（可執行值，全部已在 §8.7.1 由代理**本輪跑過**） | 是否新寫證據檔 | 判準 | 中止條件 |
|---|---|---|---|---|
| **A0a（零寫入）** | `pgrep -fl postgres`／`pgrep -fl uvicorn`／`lsof -nP -iTCP:5432 -sTCP:LISTEN`／`ls -l /tmp/.s.PGSQL.5432`／`ls -d $PGDATA $SOCKDIR`／`"$PGB/initdb" --version`＋`"$PGB/psql" --version`／`git rev-parse HEAD`／§8.6.1 那條 digest 算式 | **完全沒有**——stdout 只印終端，本節文字就是它們唯一的落點 | 逐條與 §8.7.1 相同 | 5432 已被聆聽／socket 存在／`$PGDATA`、`$SOCKDIR` 已存在 ⇒ **先回報，不覆寫、不刪除**；版本非 16.2；HEAD 或 digest 不符 ⇒ **NOT RUN** |
| **A0b（寫入）** | 同 A0a 的輸出另存 `$RUN/identity.txt`（先 `mktemp -d` 建 `$RUN`） | **是，1 支新檔**（獨佔建立，不覆寫） | 讀回值與 §8.7.1 逐字相同 | 建立 `$RUN` 失敗 ⇒ NOT RUN |

**兩格皆不碰既有實例**：無 `initdb`、無 `pg_ctl start`、無 `CREATE ROLE`／`CREATE DATABASE`、無 `GRANT`／`REVOKE`、無 migration、無平行線、無既有服務停用或重用。**本輪只做過 A0a**（其結果就是 §8.7.1 那幾列）；**A0b 本輪未做**，因此本輪除 §8.0 已列的寫入外，**沒有任何 `$RUN` 證據檔**。

#### 8.7.3 A1–A7：**本輪全部未執行**，逐格保留第十輪 §六 原判準，只換成本輪的值

| 格 | 本輪可執行值 | 硬性要求（**不得**放寬） |
|---|---|---|
| **A1** | 角色 `alethra_r13_bootstrap`／`alethra_r13_migrator`／`alethra_r13_app`；`$PGDATA`／`$SOCKDIR` ＝ §8.7.1 的 r13 路徑 | **只建叢集**：`"$PGB/initdb" -D "$PGDATA" --auth-local=trust --auth-host=scram-sha-256 -E UTF8 --no-sync`；**不建 DB、不建表**；非零 ⇒ 停，**不重試第二次、不刪 PGDATA** |
| **A2** | `pg_ctl -D "$PGDATA" -l "$RUN/pg.log" -o "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB" start` | `pg_isready -h 127.0.0.1 -p 5432` 回 ready；正常 `-m fast stop`；以 §8.7.1 抓到的 pid 用 `pgrep`＋`lsof` 核對已退出；**不以已停端點的 HTTP 回應證明停止；停止不等於刪資料** |
| **A3** | 角色名維持 **`alethra_r10_migrator`／`alethra_r10_app`**，DB **`alethra_owner_test`**。為何不能改成 `alethra_r13_*`：DB 名要命中 `backend/test_safety_gate.py:25-30` 白名單（`alethra_owner_test` 在 `:29`），且 `:223` 只准 `(localhost\|127.0.0.1\|::1, 5432)`（`:41-45`）；同時 `run_tests.sh:150` 的 `ALETHRA_TEST_REQUIRED_DB_PREFIX=alethra_`。**角色名不受白名單約束**（白名單查的是 DB 名），故角色可帶 r13 前綴、DB 名不行 | 讀回 `pg_roles` 對 migrator／app 的 `rolsuper／rolcreatedb／rolcreaterole／rolbypassrls` **四旗標全 f**（即 `test_safety_gate.py:110` 那條 SELECT 查的四欄）；`datdba=alethra_r10_migrator`；`REVOKE ALL ON SCHEMA public FROM PUBLIC` 是 PG15+ 上 alembic 能落地的**必要**授權，不是預防性多給；**不放寬安全閘**——要換 DB 名或 port 屬 owner 政策裁決 |
| **A4** | 目標 head＝**`d4e5f6g7h8i9`**；`revision_count=57`；`expected_tail` 四支依序（§8.7.1 `--json` 現測） | 空庫→head **全鏈**；`heads` 多於一／head 不符／`DUPLICATE_REVISION`｜`BRANCHED_TREE`｜`MISSING_DEPENDENCY` ⇒ 停；**不 `--autogen`、不 downgrade、不 drop、不跑任何其他 migration** |
| **A5** | 兩欄**只有 index、無 FK**：`migrations/versions/c3d4e5f6g7h8_*.py` 欄位 `:88`／`create_index 'ix_decision_records_linked_inference_id'` `:90-93`（前置條件已存在即 refusal `:73-83`）；`d4e5f6g7h8i9_*.py` 常數 `INDEX` `:47`／`create_index(INDEX,'human_decision_events',['linked_inference_id'])` `:89`；完整性由 `app/services/decision_lineage.py:19 validate_linked_inference()` 於寫入前核對（§8.1 引的 `:41/:53/:62/:75`） | 出現 FK／欄位型別非 `uuid` ⇒ 停並回報（那不是「更安全」，是本體與 migration 不同步）；差異逐條列出，**不解釋成等價** |
| **A6** | 節點 ID＝§8.7.3.1 那六支（前兩支屬本輪 A6 的計費互斥證據） | 見 §8.7.3.2（**實際 PG 等鎖與持久結算判準**） |
| **A7** | 節點 ID＝§8.7.3.1 的後三支 | 仍以 `row-lock serialisation requires a real PostgreSQL backend` 跳過 ⇒ DSN 沒生效，判 **NOT RUN**（§8.6.6 是本格**未**跑過的實物證據） |

**本包唯一輸入** ＝ `ALETHRA_PG_TEST_URL`（形如 `postgresql://alethra_r10_migrator:<mktemp 密碼>@127.0.0.1:5432/alethra_owner_test`，由代理在 A2 之後於子 shell 內組出並 export，**永不印出**）。**有效 DSN 來源**本輪逐字重讀 `run_tests.sh:141-150`：`MODE=pg` 且該變數為空 ⇒ 印 `refusing: pg layer needs ALETHRA_PG_TEST_URL (a disposable PostgreSQL database)` ＋ **exit 2**（`:142-144`）；非空 ⇒ **同一支 DSN 被覆蓋進 `DATABASE_URL`／`ALETHRA_TEST_DB`／`ALETHRA_PG_DATABASE_URL`／`ALETHRA_TEST_POSTGRES_URL` 四個變數**（`:146-149`）⇒ 這是「程序環境覆蓋 `backend/.env`」，**不是相加**（`.env` 那行 `DATABASE_URL` 指的是黑名單裡的 `ai_investment_lab`，§8.7.0 #8）；缺它 pg 層直接 rc＝**2** 拒絕；DSN 過了但 DB 名／port 不過安全閘 ⇒ **rc＝3**（`:172`）。

#### 8.7.3.1 A6／A7 的**逐支 node id**（提示：展開；本輪逐一在樹上驗證其存在）

```
tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier
tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once
tests/test_daily_model_estimate.py::test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_two_requests_on_one_idempotency_key_leave_exactly_one_order
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_stale_account_read_cannot_overwrite_a_committed_booking
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_manual_submit_racing_an_advance_stays_consistent
```
本輪定義處現讀：`:262`／`:607`（`test_daily_estimate_two_workers.py`）、`test_manual_decision_books_linked_portfolio.py:2527` class ＋ `:2534`／`:2580`／`:2645`。執行命令（各自）：
```
cd backend && . "$DSN" && export TEST_MODE=true DATA_PROVIDER=demo
./scripts/run_tests.sh pg <該格三支 node id>
```

#### 8.7.3.2 A6 的**實際 PG 等鎖**與**持久結算**判準（不以時間戳、429 或 SQLite 樣本取代）

同步機制現讀（行號為本輪）：`mp.get_context("spawn")`＋`ctx.Barrier(2)` 在 `tests/test_daily_estimate_two_workers.py:269-270` 與 `:650-651`、`_TIMEOUT_S = 60` 在 `:170`、連線層 `SET lock_timeout='30s'`／`statement_timeout='120s'`／`idle_in_transaction_session_timeout='90s'` 在 `:241-243`。
**硬性前提**：測試帳號有效額度 **≥ 4**（扇出＝2 支 instrument × 2 行程；無 `PilotEntitlement` 列時走 free plan `ai_usage_limit=10`，`app/auth/entitlements.py:70`；有列時讀該欄、模型預設 20，`app/models/pilot.py:97`）。**B 包的 `ai_usage_limit=1` 不得套在 A6／A7**，否則額度會把「錯誤的第二次扣額」掩蓋成 429（429 由 `app/auth/entitlements.py:252 reserve_quota()` 在 `:336` 丟出）。
判準以**帳本 row count** 為準：每 (owner, skill, instrument, window) 恰好 1 列 `metadata["via"]=="daily_service"`、每列恰好 1 筆 `settled` 預留、**非終態預留＝0**、落敗那支的 `learning_jobs.result_json.refused` 以 instrument id 具名 `already_estimated_this_window`。**兩個 job 摘要的 `billed_in_window` 不相加**（同一帳本的兩個瞬間），窗口總數只引用 row count。逾時／`Barrier` 未湊齊／斷言失敗 ⇒ 該次 **NOT RUN／INVALID**，不得寫成 PG 競態 PASS；現場保留給 owner，不刪 `market_data_bars`（無 owner 欄可 scope，屬共享種子）。

### 8.7.4 B 包＝自然 cron 的隔離棧（本輪**未執行**；本節是給它的确切操作）

提示對 B 的原話：「固定一個隔離 DB、schema 準備、runtime 權限、一帳號、產物方法、非秘密有效值」「證明不載入現有 `.env` 的非預期設定，不印程序完整環境、不把秘密放進證據包」「零外呼要有可強制或覆蓋整段的證據」「自然 cron、瀏覽器自動化、本人同意分開」。**逐句對應到下面 §8.7.4.1–§8.7.4.6。**

#### 8.7.4.0 先講依賴：B **不能**單獨批准

B 的定義是「一個真實運行的 API 進程，讓 05:00 UTC 那響自然鐘自己響」。這需要兩樣東西同時存在：**(a) 一個服務讀得到的 DB**、**(b) 一個不被 `.env` 覆寫的执行環境**。

- `backend/.env`（本輪實測 14 個鍵）把 `DATABASE_URL` 指向 `ai_investment_lab`，那在 `test_safety_gate.py:33-38` 的黑名單裡；即便安全閘只管正式測試 lane、不管服務進程，這也**不是**一個可隔離的 DB。⇒ B 必須自帶 DSN。
- 自帶 DSN 有兩條路，本輪只驗過其中一條的**前置**（A1–A4）：
  - **B-pg（本節主體）**：DB 由 A1（initdb）＋A2（pg_ctl）＋A3（角色／建 DB）＋A4（空庫→head `d4e5f6g7h8i9`，57 revisions）產出 ⇒ **「批准 B」必須寫成「批准 B-pg，連帶要求 A1–A4 先跑過」**；A 包未批准時 B-pg 無 DB 可用。
  - **B-sqlite（本輪未驗，需另批）**：`app/database.py:10-18` 確實為 sqlite 編譯 `JSONB`／`UUID`，`engine = create_engine(settings.database_url)`（`:19`）不排斥 sqlite ⇒ **連線層可行**是本輪讀到的事實；但本輪**未驗** 57 支 migration 在 sqlite 上能否落到 head（A4 那條鏈只在 PG 有正式記錄），而且它即使跑通，證據層級只到「⑤-sqlite 服務程序」，**不得計入 PG 欄、不得拿来兌現 A6／A7 的鎖證據**。

#### 8.7.4.1 觸發時機（實測；不接受「改時鐘」捷徑）

| 事實 | 值 | 出處（本輪現讀） |
|---|---|---|
| 估計 lane 的小時 | `DAILY_ESTIMATE_HOUR_UTC = 5` | `app/services/daily_estimates.py:82` |
| 該 lane 的 trigger | `CronTrigger(hour=DAILY_ESTIMATE_HOUR_UTC, minute=0, timezone=SERVICE_TIMEZONE)` | `app/workers/learning_scheduler.py:180-186`；`SERVICE_TIMEZONE="UTC"` `:80` |
| 其他三條 lane 的自然小時 | research **02:00**、simulation **03:00**、weekly **週日 04:00** | `learning_scheduler.py:81-83`＋`:152`／`:162`／`:170` |
| 本節測量時刻 | **2026-11-20T04:19:26Z**（quiescent：5432／8000／8031／3031／3001 監聽數各 0；`/tmp/.s.PGSQL.5432` 不存在；`${TMPDIR}alethra-runs/pg-r13-*` 無匹配） | 本輪命令輸出 |
| ⇒ 下一個自然 tick | **2026-11-20 05:00 UTC（13:00 HKT）**；錯過即 **2026-11-21 05:00 UTC** | 由上兩行算出 |

**硬性要求**：不改系統時鐘、不用 `faketime`、不呼叫任何手動觸發入口——`POST /api/v1/personalization/daily-estimate/run`（`app/api/personalization.py:459`）與 `POST /api/v1/learning/jobs/trigger`（`app/api/learning.py:137`）都是**另一道門**，由它們取得的證據只能標「手動門」，不得寫成自然 cron。進程必須在 **05:00Z 之前**起來並完成 consent 寫入 ⇒ 本輪量到的可用窗口是 04:19Z→05:00Z＝**41 分鐘**。這是排程約束，不是建議。

#### 8.7.4.2 執行環境：`.env` 的處理只能這樣寫才誠實

`app/config.py:560` ＝ `model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")`，pydantic-settings 以**當前工作目錄**解析這個相對路徑 ⇒ 只要按慣例 `cd backend` 起動 uvicorn，`.env` **一定被讀**。所以：

- **禁止**寫「已隔離 `.env`／未載入 `.env`」。可驗的宣稱只有一條：**「每個會改變行為的鍵都被程序環境覆寫」**，覆寫清單如下（值全為非秘密值或指向 `$RUN` 的 mode-600 檔）：

| 鍵 | `.env` 實測值 | B 必須帶的值 | 為什麼（file:line） |
|---|---|---|---|
| `DATABASE_URL` | `postgresql://…/ai_investment_lab`（黑名單名，只報 DB 名） | A2 後在子 shell 內由 `$RUN/dsn.env` 匯入，**不印出** | `test_safety_gate.py:33-38`；`run_tests.sh:146-149` 是同一招（**覆蓋**而非相加） |
| `DATA_PROVIDER` | `twelvedata` | **`demo`** | 任何 provider 呼叫＝外呼；估計 lane 本身不需要 provider（§8.7.4.4） |
| `TWELVEDATA_API_KEY` | **空值**（`TWELVEDATA_API_KEY=`；§8.7.0 #8 實測） | **不帶進程序環境** | 金鑰本身是空的，但 `DATA_PROVIDER=twelvedata` 照樣會讓程式去找 provider；`env -i` 白名單同時排除兩者 |
| `RESEARCH_AUTH_BYPASS_ENABLED` | `true` | **`false`** | 欄位 `config.py:137`；bypass 實作 `app/auth/jwt.py:51→:60`，開著會讓「以 JWT 證明身分」那格失效 |
| `CANDIDATE_PASSWORDLESS_MODE` | `true` | **`false`** | `config.py:85`＋驗證器 `_validate_candidate_passwordless :469`；開了就有免密碼登入路徑 |
| `LIVE_TRADING_ENABLED` | `true` | **`false`（深度防禦，不是保證）** | 見判準⑧：本輪實測整個 `app/` 除定義處 `config.py:172` 外**無人讀它** |
| `ENABLE_LEARNING_SCHEDULER` | 未列（預設 `False`） | **`true`** | B 的唯一啟動閘：`app/main.py:52` |
| `LEARNING_SCHEDULER_JOBS` | 未列（預設 `""`） | **`daily_model_estimate`（具名，不可留空）** | 見下一段——留空不等於不跑 |
| `AUTO_COLLECTION_ENABLED` | 未列（預設 `False`） | 明示 **`false`** | `config.py:156`＋`app/services/auto_collection.py:164` |
| `JWT_SECRET_KEY` | 存在（不報值） | `$RUN/jwt-secret.txt`（600，本輪新產生） | 「非秘密有效值」指的是**證據包**內不得出現；啟動仍需一把可用金鑰 |
| `APP_ENV` | 未列（預設 `development`） | 明示 `development` | `config.py:75`；避免 `is_production_like`（`:214-216`）改寫其他驗證器行為 |

- **留空 `LEARNING_SCHEDULER_JOBS` 的後果必須寫在批准單上**，因為它反直覺：`config.py:211` 是 `registered_learning_lanes = learning_scheduler_job_list or list(DEFAULT_LEARNING_SCHEDULER_LANES)`，而 `DEFAULT_LEARNING_SCHEDULER_LANES = ("daily_research","daily_simulation","weekly_analysis")`（`:56-60`）——**預設集刻意不含 `daily_model_estimate`**（`:50-54` 的註解自己寫「`daily_model_estimate` is the current case and sits at 05:00 UTC deducting quota, so it is named here as off-by-default for anyone who did not ask」）。⇒ 留空＝今晚估計不響，但**明晚 02:00Z 起會為所有持 live portfolio 的帳號跑研究、03:00Z step 回放**；具名才是「只註冊這一條」。具名拼錯 ⇒ `config.py:349` 直接 `ValueError`（`LEARNING_SCHEDULER_JOBS names unknown lanes: …`）；只打分隔符 ⇒ `:336` 拒。兩者都在**讀 settings 階段**炸，不會靜默降級。
- 「不印程序完整環境」：全案只用 `env -i KEY=VALUE …`（白名單）＋ `pgrep`／`lsof` 讀回，**不得** `env`／`printenv`／`ps eww`（`ps eww -p PID` 會洩 env）。
- 「證明不載入非預期設定」的**可執行讀回**：在同一個 `env -i` 環境下跑一支只印非秘密欄位的 `python -c`（`data_provider`／`app_env`／`enable_learning_scheduler`／`learning_scheduler_jobs`／`auto_collection_enabled`／`research_auth_bypass_enabled`／`candidate_passwordless_mode`／`live_trading_enabled`／`database_url` 的 **host＋DB 名**（去密碼）），輸出存 `$RUN/effective-settings.txt`。**它的身份是「同環境第二進程的 `Settings()` 視圖」，不是服務進程自己的視圖**；後者只能由 `$RUN/service.log` 那行 registered lanes 證明（§8.7.4.3）。兩者不得互換。

#### 8.7.4.3 產物方法（`$RUN`＝本包唯一證據目錄）

```
RUN="${TMPDIR}alethra-runs/b-$(date -u +%Y%m%dT%H%M%SZ)-$$"; mkdir "$RUN"   # 目錄名已存在即停，不 rename 重試
```
沿用正式 lane 的 run-dir 慣例（`run_tests.sh:298` 的 `$MODE-$stamp-$pid` 形），內容固定 13 檔：`sandbox.pro`／`env.allowlist.txt`（**只有鍵名**）／`dsn.env`（600）／`jwt-secret.txt`（600）／`owner-password.txt`（600）／`service.log`（uvicorn stdout+stderr 合併）／`service-pid.txt`／`lsof-{1,2,3,4}.txt`／`effective-settings.txt`／`readback-jobs.json`／`readback-status.json`／`verdict.md`。**倉庫內 0 寫入**；證據檔一律在 `$RUN`，不進 `alethra-evidence/`（避免移動候選摘要）。

#### 8.7.4.4 零外呼：**本輪把上一輪那格不可執行的寫法改掉**（實測）

本記錄 `:2798` 那格提議 `sandbox-exec -p '(version 1)(deny network*)'`。**本輪實測該寫法根本起不來**：`sandbox-exec: execvp() … failed: Operation not permitted`（rc＝**71**）——seatbelt profile 未先寫 `(allow default)` 時隱式全拒，連 `exec` 都在內。以下四列是同一天（04:15Z）以一支 25 行 loopback 探針（`${TMPDIR}r13-sandbox-probe/probe.py`，倉庫外）逐一走過 `sandbox-exec` 量出來的：

| profile（逐字） | `bind 127.0.0.1` | `connect 127.0.0.1:1`（無監聽者） | 對 B 的判讀 |
|---|---|---|---|
| `(version 1)(deny network*)` | 程序無法 exec，**rc 71** | 同 | **撤銷** `:2798` 的寫法 |
| `(version 1)(allow default)(deny network*)` | `BIND_FAIL EPERM` | `CONNECT_FAIL EPERM` | 連監聽都擋 ⇒ 服務起不來 |
| `(version 1)(allow default)(deny network-outbound)` | `BIND_OK 56389` | `CONNECT_FAIL EPERM` | 能監聽，但**連 loopback `connect()` 都被拒** ⇒ 會擋掉 PG over TCP，B 不可用 |
| `(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))` | `BIND_OK 56390` | `CONNECT_FAIL **ECONNREFUSED**` | **B 用這一條**：豁免只給 loopback 遠端；`ECONNREFUSED`（而非 EPERM）證明請求已抵達核心、不再被沙箱擋 ⇒ 豁免範圍可測 |

⇒ B 的**可強制**證據＝把第四列那條 profile 存進 `$RUN/sandbox.pro` 並用它包住 uvicorn 啟動；判準：服務進程 `lsof -nP -p <pid> -a -i` 的四份存檔（起動後／05:00 前／05:00 後／停止前）中**非 `127.0.0.1`／`::1` 對端列數＝0**，且 `service.log` 無 `Operation not permitted` 級 traceback。

**邊界（不得推過頭）**：探針**只碰 loopback**，刻意未對任何公網地址發起 `connect()`——那本身就是一次外呼嘗試。所以「非 loopback 會被拒」是由「同一條 `deny network-outbound` 在未加豁免時連 loopback 都 EPERM」推出的**規則層論證**，不是對公網的實測；兩者在本節分開標記。另：本節也**未**實測 uvicorn 本身在該 profile 下能完成啟動（那屬於 B 包批准後的觀察），實測的只有 `bind()` 這個 syscall 類別被放行。

配套的程式層理由（說明 05:00 那一響**不需要**外呼）：`_estimate_one` 只讀 DB——`features_as_of(db, …)` 取本機欄位、`_data_provenance`（`app/services/daily_estimates.py:722-741`）讀 `MarketDataMetadata`，推導由 `_run_personal_prediction`（`app/api/personal_models.py:566`）在進程內完成；實測 `app/api/personal_models.py`（共 1,306 行）內 `requests`／`httpx`／`urllib`／`aiohttp`／`socket` **0 個功能呼叫命中**（僅同名比較器 `requests_match` `:52`／`:942`／`:996` 與 `api_key_rate_limit_requests` `:94`）。外呼風險全部來自被覆蓋掉的 `DATA_PROVIDER=twelvedata`＋那把金鑰，以及**不得呼叫**的 `POST /api/v1/admin/collection/trigger`（`app/api/admin.py:97`，會主動抓行情 ⇒ B 全程不發 admin token）。另：`app/main.py:61-66` 的第二個 startup hook **沒有 gate**（其註解自寫「No gate here: the service reads its own opt-in」），實閘在 `auto_collection.py:164`；`.env` 無該鍵＋預設 `False`（`config.py:156`）⇒ 正常應在 `service.log` 看到逐字 `Auto-collection is disabled by configuration (AUTO_COLLECTION_ENABLED=false); not started`（`:165-168`）——**這一行是 B 的必找證據，找不到就停**。

#### 8.7.4.5 一帳號、本包做什麼、判準①–⑧

**建帳號（本輪未執行）**：`POST /api/v1/auth/register`（`app/api/auth.py:30-31`，def `:43`，`risk_acknowledged` 檢查 `:57`）＋ `POST /api/v1/auth/login`（`:101`）。密碼由 `secrets.token_urlsafe` 產生、寫 `$RUN/owner-password.txt`（600）、**不印、不進命令列、不進證據包**（政策同 `scripts/seed_e2e_accounts.py` docstring）。額度：隔離 DB 內無 `PilotEntitlement` 列 ⇒ free plan `ai_usage_limit=10`（`app/auth/entitlements.py:70`）。**上一輪 B 包那格 `ai_usage_limit=1` 本輪撤銷**：B-1 結算 0 次（下表），壓到 1 既無必要，又會在被誤用到 A6／A7 時把「錯誤的第二次扣額」掩蓋成 429（`entitlements.py:336`）。

**唯一一列必寫的前置**：以正式入口 `PUT /api/v1/personalization/daily-estimate/consent`（`app/api/personalization.py:308`）寫 consent。原因（實測）：`_run_tick` 的 owner 集合來自 `estimate_owner_ids()`——只挑「有未撤回的 `daily_model_estimate` consent 列」的 owner（`daily_estimates.py:388-408`）；**無 consent ⇒ `no_accounts_served`（`learning_scheduler.py:329-330`）⇒ `{"skipped": …, "jobs": []}`（`:392-395`）⇒ 零寫入**，於是「排程壞了」與「沒有人 opt-in」在帳本上**完全同形**。這就是 B 必須寫這一列的理由，不是為湊資料。

| # | 判準 | 讀回方法 |
|---|---|---|
| ① | 只註冊估計 lane | `service.log` 逐字 `Learning scheduler created (UTC): daily_model_estimate@…`（`learning_scheduler.py:219-225` 只印**實際 add** 的 lane）。實測**無**任何 HTTP route 可讀 learning lane 註冊名單（`app/api/learning.py` 只有 `:113`／`:137`）⇒ 這行日誌是唯一入口 |
| ② | 自然鐘真的響 | 05:00Z 前後 `GET /api/v1/learning/jobs`（`app/api/learning.py:113`，prefix `/learning` 在 `:27`，以該帳號 JWT，回 `jobs[]`＋`total`）由 `total=0` 變 **≥1**，且該列 `job_type="daily_model_estimate"`、`config_json["scope"]` 非手動值 |
| ③ | 響了但**沒扣額** | inference 列＝0、非終態 quota 預留＝0、`GET …/daily-estimate/status`（`personalization.py:426`）顯示該窗口無已計費估計；拒因逐字 `strategy_scope_missing`（`daily_estimates.py:761`：`current_selection()` 回 None 時在 gate **之前** raise，故結算 0） |
| ④ | 只碰這一個帳號 | 隔離 DB 內 `learning_jobs`＝1 列、`personalization_consents`＝1、`users`＝1 |
| ⑤ | 零外呼 | §8.7.4.4 的 profile ＋ `lsof` 四份對端為零 ＋ `service.log` 無 provider 日誌 |
| ⑥ | 未帶入非預期設定 | 程序環境每個鍵都在 §8.7.4.2 覆寫清單上 ＋ `$RUN/effective-settings.txt` 逐欄對照（**只報非秘密欄**） |
| ⑦ | 停止可證明 | `kill -TERM "$(cat $RUN/service-pid.txt)"`；以 `pgrep -f "uvicorn.*8031"` 空 ＋ `lsof -nP -iTCP:8031 -sTCP:LISTEN` 空證明退出；**不以已停端點的 HTTP 回應證明** |
| ⑧ | 「不會啟動實單」不得掛在旗標上 | 實測：`LIVE_TRADING_ENABLED` 在整个 `app/` 只有定義處 `config.py:172`，**無任何讀者**（本輪全庫 grep，唯一其他命中是 `tests/test_research_run_recovery.py:298` 那段自責註解）⇒ 真正的保證是「實單 daemon 只能由 `POST /live/start`（`app/api/paper_trading.py:554-556`，`Depends(require_admin)`）啟動，而 B 不發 admin token、不呼叫該 route」；`LIVE_TRADING_SYMBOLS／INTERVAL／CAPITAL` 也只被該 route 讀（`:572-574`）。把⑧寫成「旗標為 false 所以不會實單」＝重複被 `:298` 點名的 false affordance |

**本包如實紀錄的兩個覆蓋缺口**（不得被上表掩蓋）：
1. **kill-switch 負控不在 B-1 內**：自然 cron 一天一響，單個 05:00 窗口無法同時觀察「開關 ON ⇒ `kill_switch_active` 跳過」（`learning_scheduler.py:318-319`；無列＝未啟動＝lane 照跑，`app/services/kill_switch.py:23-37`）。live 版只有兩條路：等第二個自然 tick（＋24h）或用手動門（另一批准）。④-sqlite 節點（task #206）覆蓋的是那段代碼，**不能寫成 B 層實測**。
2. **B-2（扣額正控）不在本包**：要讓 05:00 真的產生 inference＋結算，需在隔離 DB 內湊齊「該 instrument 有 stored bars ＋ 有已訓練且已選定的 personal model version」。現成合成寫入器 `scripts/seed_e2e_personal_learning.py` 在 `:24-33` 硬性要求 `DATABASE_URL` 為 postgresql 且含 `alethra_personal_e2e` ⇒ **與 A3 的 `alethra_owner_test` 是兩個不同 DB（可同一叢集）**；且它只播 205 筆已審決策＋一支 locked strategy version，**不產生完成的 personal training**（docstring `:1-9`）⇒ B-2 還要多跑 training worker、花 quota，**需自己的批准卡**。

#### 8.7.4.6 一頁批准單（B 卡；放置 ≠ 執行）

```
批准 B-1（自然 cron 隔離棧，一次完整操作）：
  前置：A1＋A2＋A3＋A4 已跑完並留下證據（若改批 B-sqlite，請明說「verdict 上限＝⑤-sqlite，不計入 PG 欄」）
  做：把 §8.7.4.4 第四列的 profile 寫入 $RUN/sandbox.pro，用它包住
      env -i <§8.7.4.2 覆寫清單> .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8031
      於 05:00Z 前起動；註冊 1 帳號（auth.py:30/:101）；以 PUT consent（personalization.py:308）寫 1 列；
      等自然鐘響；按判準①–⑧讀回；TERM 停止並以 pgrep＋lsof 證明退出
  不做：不改時鐘、不呼叫 POST …/daily-estimate/run 或 /learning/jobs/trigger、不發 admin token、
        不呼叫 POST /admin/collection/trigger、不印環境、不印 DSN／金鑰值、
        不刪 DB 與 $RUN（留給 owner）、不寫倉庫內檔案、不做 Git 寫入
  失敗即停：service.log 缺 registered-lane 行／缺「Auto-collection is disabled…」行／
            lsof 出現非 loopback 對端／consent PUT 非 2xx
  產物：$RUN 下 13 檔（§8.7.4.3），倉庫 0 寫入
  本輪狀態：NOT RUN
```

### 8.7.5 C 包＝瀏覽器驗收（本輪**未執行**；三件事分開批）

提示原話：「單一建置／啟動模式、產物隔離、API base、DB／帳號」「自然 cron、瀏覽器自動化、本人同意分開」。本節先把**本機實測到的三個硬阻塞**攤開，再給確切操作——不是先給命令再假設能跑。

#### 8.7.5.0 實測：本機现有的「單一啟動模式」不可用（已跑到那條拒路）

倉庫根有一對正式启动器 `start_acceptance_env.sh`（150 行）／`stop_acceptance_env.sh`（100 行）。本輪**實際執行了一次** `./start_acceptance_env.sh`（未帶任何秘密），它在建立任何東西之前自己退出：

```
=== Starting Alethra Acceptance Environment ===
REFUSED (nothing was started): docker is not on PATH; this stack is defined by docker-compose and PostgreSQL runs in a container
START_LAUNCHER_RC=2
```
出處：拒路在 `start_acceptance_env.sh:34-35`（`refuse()` 定在 `:26-29`，語意為「**在碰到任何容器或行程之前**退出」，`:8-12` 自己寫明 exit 0/2/3 三個語意）。本機 `command -v docker` ⇒ 未找到。**偏離紀錄**：這是「照既有正式入口走」的第一次嘗試，未啟動任何服務、未建任何容器、未寫檔（拒在 `:34`，第一個寫檔動作在 `:63` 之後）。

⇒ 後果必須寫在批准單上：**「單一建置／啟動模式」在本機等於沒有模式**。三個選項由 owner 裁決，代理不自行選：
1. **C-launcher（另一台機器）**：在有 docker 的主機跑原啟動器 ⇒ 保留「單一模式」，但本機證據鏈斷掉（記憶：本機無 brew/Docker/sudo）。
2. **C-manual（本機，第二種模式）**：以 §8.7.5.2 的三個命令自己起 DB／API／前端 ⇒ **明確命名為第二模式**，並在結尾註明「本輪 verdict 不適用於 launcher 產的棧」。
3. **不批 C**：決策旅程（§8.4）停在 ⑤-HTTP in-process，瀏覽器欄維持 **NOT RUN**（本輪即此狀態）。

#### 8.7.5.1 實測：啟動器本身有兩條会让 C 白跑的接線缺陷（選項 1 也要先修）

| # | 缺陷 | 逐字證據 | 後果 |
|---|---|---|---|
| 1 | **API base 在建構之後才設**：`start_acceptance_env.sh:116` 先 `NEXT_TELEMETRY_DISABLED=1 npm run build`（**不帶** `NEXT_PUBLIC_API_URL`），到 `:121` 才 `NEXT_PUBLIC_API_URL="http://127.0.0.1:$BACKEND_PORT" npm run start` | `next.config.js:8-10` 把 `NEXT_PUBLIC_API_URL` 放進 `env`（Next 於**建置期**寫死進 chunk）；`src/lib/api.ts:11` 是模組層常數 `process.env.NEXT_PUBLIC_API_URL \|\| 'http://localhost:8000'`。**實測既有 `.next`**：含 `http://localhost:8000` 的檔＝server 1＋static 1，含 `127.0.0.1:8001` 的檔＝**0** | 瀏覽器打的是 `localhost:8000`，而啟動器的後端在 `127.0.0.1:8001`（`:20`）⇒ 每個 spec 在登入那一跳就 network error。`next.config.js:4-6` 的註解自己點名這個失敗模式（「the silently stale API base」） |
| 2 | **CORS origin 未接線**：啟動器設了 `DATABASE_URL`／`JWT_ALGORITHM`／`AI_PROVIDER`／`DATA_PROVIDER`／`APP_ENV`（`:103-107`）唯獨**沒設 `CORS_ORIGINS`**；後端只用 `settings.cors_origins_list`（`app/main.py:27`），預設 `cors_origins = "http://localhost:3000"`（`app/config.py:79`，list 解析 `:195-196`） | 前端起在 **3001**（`:21`），後端在 **127.0.0.1:8001**；`next.config.js` **無 rewrites**（本輪全文讀過：只有 `output`／`distDir`／`env`）⇒ 所有呼叫都是跨來源直連 ⇒ origin `http://localhost:3001` 不在放行清單（連 `localhost` vs `127.0.0.1` 都不同） | 需先 `export CORS_ORIGINS=http://localhost:3001`（或前端實際 origin）再起後端；task #171 記的就是這一格，本輪給出行號 |

兩條都是**讀碼＋既有產物實測**得知的（①＋既有 `.next` 的字串計數），**未**透過跑瀏覽器證明；修復属於代碼變更，本輪未批准任何代碼變更（D 包只交付 patch 材料）。

#### 8.7.5.2 若選 C-manual：三個命令、四個必須一致的值

```
# 0) DB：由 A1–A4 產出（或另批的 alethra_personal_e2e，見 §8.7.4.5 缺口 2）
# 1) 後端：**C 用自己的進程**（`ENABLE_LEARNING_SCHEDULER=false`）；共用 B 的進程＝把自然 cron 一起帶進瀏覽器驗收，需 owner 另說
cd backend && env -i PATH="$PATH" HOME="$HOME" \
  DATABASE_URL=… JWT_SECRET_KEY="$(cat "$RUN/jwt-secret.txt")" \
  DATA_PROVIDER=demo APP_ENV=development \
  CORS_ORIGINS=http://localhost:3031 ENABLE_LEARNING_SCHEDULER=false \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8031 > "$RUN/service.log" 2>&1 &
# 2) 前端：**先 export API base 再 build**（順序就是缺陷 1 的修復）
cd frontend && export NEXT_DEV_DIST_DIR="$RUN/next-dist" \
                 NEXT_PUBLIC_API_URL="http://127.0.0.1:8031" \
                 NEXT_TELEMETRY_DISABLED=1
~/.local/bin/node22 node_modules/next/dist/bin/next build            # 單一建置模式
~/.local/bin/node22 node_modules/next/dist/bin/next start -p 3031 -H 127.0.0.1
```
| 必須一致 | 值 | 出處 |
|---|---|---|
| 節點版號 | **`~/.local/bin/node22`（v22.23.5）**；預設 `node` ＝ 18.20.8，Playwright 實測直接拒跑：逐字 `You are running Node.js 18.20.8. / Playwright requires Node.js 20 or higher. / Please update your version of Node.js.` | 同一支 `node_modules/.bin/playwright test --list` 分別以預設 `node`（拒跑，逐字如上）與 `node22`（通過）實跑；`--list` 本身耗時約 **1 秒**（04:29:21Z→04:29:22Z 完成，`Total: 312 tests in 32 files`），未啟動 webServer、未啟瀏覽器 |
| `distDir` | `NEXT_DEV_DIST_DIR="$RUN/next-dist"`（**不是** `.next`）：`next.config.js:7` 讀的就是這個名字（第十二輪寫成 `NEXT_DIST_DIR` 已作廢，見 §8.7.0 #4）。共用的 `.next/BUILD_ID`＝`iEzzhWbfCzP7pv7Rb0LcS`，mtime **Nov 19 09:05:05**，而 `src/components/training/DailyEstimatePanel.tsx` mtime **Nov 19 20:58:33**、`src/components/workspace/OrderPreviewPanel.tsx` mtime **Nov 20 10:48:21** ⇒ 既有產物比兩個面板都舊（13h／25.7h），**不是**候選證據；本輪未跑 build，故 `BUILD_ID` 維持舊值 | `ls -lT` 實測 |
| 前端 origin | `E2E_FRONTEND_BASE` 必須與 `next start -p` 同值；`playwright.config.ts:5` `const FRONTEND_URL = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3001'`，`baseURL` 在 `:23` | 本輪讀 |
| 瀏覽器 | `E2E_BROWSER_CHANNEL=chrome`：`playwright.config.ts:11`＋`:24` `channel: BROWSER_CHANNEL`；實測 `~/Library/Caches/ms-playwright` **0 個 entry**（內附 chromium 從未下載，config 註解 `:8-10` 自己寫了這件事），`/Applications/Google Chrome.app` 存在 | 本輪實測 |

`webServer` 那格（`playwright.config.ts:36-40`）是 `command: 'echo "Using existing acceptance stack"'`＋`reuseExistingServer: true` ⇒ **它不會幫你起任何東西**，只探 `FRONTEND_URL`；棧必須先在 §8.7.5.2 的 1)2) 步起來。

**為什麼命令裡要明帶 `JWT_SECRET_KEY`（本輪讀到的事實，不是推測）**：`is_production_like` 只在 `APP_ENV ∈ {production, staging}` 為真（`app/config.py:214-216`），而不安全金鑰檢查排在它**之後**（`:233` 是 `if not self.is_production_like: return self`，檢查在 `:236`／`:241`）⇒ 以 `APP_ENV=development` 起的棧，`jwt_secret_key` 會落在出廠預設 `"change-me-in-production-use-a-long-random-string"`（`:64`），而那正是 `_INSECURE_JWT_DEFAULTS`（`:9-16`，5 個值）的第一個，**development 下沒有人拒它**。B／C 都用 `$RUN/jwt-secret.txt` ＋ `env -i` 白名單，所以不依賴這條檢查；也**不得**把「有 validator」寫成本機 development 棧的安全性保證。（對照：`.env` 裡 `JWT_SECRET_KEY` 為 SET，值不抄錄——見 §8.7.0 #8。同一格也如實更正：`TWELVEDATA_API_KEY=` 本輪實測是**空值**。）

#### 8.7.5.3 實測：瀏覽器自動化目前**覆蓋不到**今輪那兩條旅程（這是範圍事實，不是猜）

`--list` 屬收集、不啟瀏覽器（本輪實跑證明：`~/.local/bin/node22 node_modules/@playwright/test/cli.js test --list` 未啟動 webServer、未啟瀏覽器，直接吐出清單）。計數由兩種算法 reconciliation 而來，不互換：

| 算法 | 值 | 物件 |
|---|---|---|
| Playwright 收集 | **312 tests in 32 files**（逐字 `Total: 312 tests in 32 files`） | 實例化後的測試本體 |
| 正則掃 `e2e/*.spec.ts` | 347 個行首命中＝**312 個 `test(` 註冊** ＋ **35 個行首 `test.skip(…)`** | 後者是測試體內的条件跳過宣告（例：`e2e/candidate-passwordless.spec.ts:93` `test.skip(!READY \|\| MODE !== 'on', SKIP);`），**不是**第 348 支測試 |

今輪範圍對照（逐字 pattern 計數，`e2e/*.spec.ts`）：

| pattern | 檔數 | 命中 | 判讀 |
|---|---|---|---|
| `daily-estimate`／`DailyEstimatePanel` | **0／0** | 0 | 每日估計面板**沒有任何**瀏覽器 spec（連 mock 都沒有） |
| `pp-download-decision-history` | **0** | 0 | 決策歷史匯出**無** spec |
| `OrderPreviewPanel` | **0** | 0 | 組件名未出現在任何 spec |
| `manual-decision` | 3 | 6 | 全部是**被 mock 的路由**：`e2e/workspace.spec.ts:1547` `await page.route('**/api/v1/simulations/sess-1/manual-decision', …)`；`e2e/order-lifecycle-flow.spec.ts:276`／`e2e/workspace-replay-backend.spec.ts:529` 同形 |

`workspace.spec.ts:1420-1425` 的註解自己劃清了界線（逐字）：「This lane is not the session-bound contract. The real POST /manual-decision books a pending order with no fill and leaves the cursor to /advance; this fixture still replies with a submission-time fill … The actual submit → pending → filled → settled record is proven against the live backend by e2e/order-lifecycle-flow.spec.ts, not here.」——而 `order-lifecycle-flow.spec.ts:276` 那行也是在 `page.route` 的監聽器裡比 URL，**沒有**打到真後端。⇒ 全 e2e 目錄 130 個 `page.route(` 呼叫（實測），這批 spec 證的是**前端在给定回應下的行為**，不是「後端寫了什麼」。瀏覽器欄要兌現 §8.4 那條旅程，需**新寫** spec 或由本人手動走過（§8.7.5.4），兩者都不是批准 C-build 會自動送來的東西。

#### 8.7.5.4 「本人同意」那一格（與自動化分開）

提示要求「瀏覽器自動化、本人同意分開」。本機可用的两条路：
- **C-agent**：由代理用 `browser-use` MCP（`navigate_page`／`take_snapshot`／`click`／`fill`／`take_screenshot`）驅動瀏覽器走完 §8.4 旅程：登入 → 明選合法估計 → 面板提交 → 讀回 → CSV／JSON 匯出 → 第二帳號看不到。**每個動作都需要 owner 對「啟動瀏覽器＋對指定 URL 發起請求」明確批准**；本輪未批。
- **C-human**：owner 自己在桌機瀏覽器走一遍，代理只準備逐格核對表（步驟／預期畫面／實際畫面讀回／截圖檔名）。這條**不需要**批准自動化，但需要 owner 出力。

兩者讀回的都是**畫面**；帳本側（`DecisionRecord`／`HumanDecisionEvent`）仍由 `GET /api/v1/…` 或 SQL 核對。證據類型不得互換（記憶：API testing ≠ UI journey）。

#### 8.7.5.5 C 包狀態與判準

| 子項 | 本輪 | 判準（批了才算數） |
|---|---|---|
| `npm run build`（隔離 distDir） | **NOT RUN** | 建置 rc＝0；`$RUN/next-dist/BUILD_ID` 與 build 前的舊 `iEzzhWbfCzP7pv7Rb0LcS` **不同**（相同＝沒重建，直接判 NOT RUN）；`grep -rl "127.0.0.1:8031" "$RUN/next-dist"` ≥1 且 `grep -rl "localhost:8000"` ＝0（這兩條就是 §8.7.5.1 缺陷 1 的修復證據） |
| 起棧 | **NOT RUN** | 後端 `/health/ready`（啟動器 readiness 語意：`start_acceptance_env.sh:130-138`）通過；`curl -i` 帶 `Origin: http://localhost:3031` 的預檢回應含 `access-control-allow-origin`（缺陷 2 的修復證據） |
| 312 支 e2e | **NOT RUN** | 只報「收集 312／跑 N／過 N／跳 N／敗 N」四個數，**不拿 312 當通過數**；`E2E_BROWSER_CHANNEL=chrome` 未設時第一支就 fail（無 chromium 可啟），那種失敗是**環境**失敗，不得記入產品 |
| §8.4 旅程（C-agent／C-human） | **NOT RUN**，且**尚無 spec**（§8.7.5.3） | 需先決定：新寫 spec（代碼變更，另批）或 C-human 走查。不得以「面板有前端測試」冒充瀏覽器驗收 |

### 8.7.6 D 包＝審閱材料（已交付；**套用**未批准）

提示原話：「D 先交 182 檔 patch／新增檔／manifest／歸屬，另列『紀錄查無引用』檔案的處理意見」「隔離重現與 Git 寫入分開批准」。本節所有數字**本輪重新量過**（04:31Z 前後），不抄上一輪。

#### 8.7.6.1 現況（實測）

| 格 | 值 | 量法 |
|---|---|---|
| 包位置 | `alethra-evidence/d-patch-round13/`（`MANIFEST.json`＋`SHA256SUMS.txt`＋`tracked/`＋`untracked/`） | `ls` |
| 完整性 | `shasum -a 256 -c SHA256SUMS.txt` ⇒ **184/184 `: OK`、0 失敗**（184＝183 檔＋`MANIFEST.json` 本身） | 本輪實跑 |
| 檔數 | `total 183`／`tracked_modified 127`／`untracked_new 56`／`excluded_by_date 0` | `MANIFEST.json` `counts`（逐字） |
| 行數 | `patch_added_lines 14182`／`patch_removed_lines 2743` | 同上；對象＝**patch 文本的行**，不是產品 LOC |
| 位元組 | `artifact_bytes 2114353` | 同上；對象＝**整個 D 產物** |
| 前置提交 | `base_commit "5a34e3f"`／`head_commit_at_build "5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a"` | 同上。**這是「fixed base」的實際來源**（§8.0 那張溯源表指向這裡）；第十二輪寫過的 `5a33f527` 在本 repo 不可解析，屬**已作廢**值 |
| 範圍分解 | `backend/` **138**＋`frontend/` **45**＝183；**紀錄檔本身不在包內**（`EXECUTION_TABLE` 命中 0） | 本輪對 `files[].path` 逐條計數 |
| 兩個 porcelain sha256 | 生成器 `70d23374…`＝`sha256("\n".join(lines))`（`tools/build_review_package.py:179-188`）vs 正式 lane `885c29f1…`＝`git status --porcelain \| shasum -a 256`（**含尾端換行**，`run_tests.sh:386`）；本輪重算全倉 porcelain＝**212 行／`885c29f1e625f0e0…`**，與 §8.6 identity 逐字相同 | 兩個演算法都實跑；同一棵樹、兩個值，**不得混用** |
| 212 − 183＝**29**（範圍外，非遺漏） | 倉庫根單檔 **14**（3 支 top-level `.md`：`README.md`／`EXECUTION_RECORD_20261109.md`／`EXECUTION_TABLE_20261117.md`；10 支根 `.sh`；`.gitignore`）＋`docs/` **7**＋`backend/` **6**（兩支釘選 `tests/collected_nodes.txt`、`tests/skip_budget.json`；`DECISION_LINEAGE_DELIVERY.md`／`_REVIEW.md`；`.env.test.example`；**0-byte `backend/postgres`**）＋`.github/workflows/secret-hygiene.yml` **1**＋`alethra-evidence/` **1** | 本輪逐條比對 `git status --porcelain` 與 manifest。之所以是 1：git 把未追蹤**目錄**折疊成一條 ⇒ **「1 條」≠「1 檔」**，`alethra-evidence/` 底下實際有多個產物檔。生成器用 `:85` 的**限定路徑** porcelain（`-- backend frontend`）挑 183，用 `:173` 的**全倉** porcelain 記身份 ⇒ 29 是政策性的範圍外 |
| 第十二輪 → 本輪 | **新增 1**／**移除 0**／**內容變更 6**（182 檔共同）；r12＝182 檔（126＋56）、13,773／2,740 行、2,061,066 bytes、porcelain `4d944dc3cc125e8e…`／211 paths | 本輪逐檔比對兩份凍結 manifest 的 `sha256`（不靠手記）。新增那支＝`frontend/src/components/workspace/OrderPreviewPanel.tsx`（從 untracked 轉成整檔交付）；**檔數 182→183 的 ±1 物件就是這一支**，不是「多改了一個產品行為」 |

#### 8.7.6.2 「紀錄查無引用」那一桶：處理意見（提示明文要）

`tools/build_d_attribution.py` 的規則（其 docstring `:12-24` 逐字）：路徑以**紀錄實際使用的每一種拼法**（repo-relative／tool-relative／basename）去查，全查不到才入桶，且**只報「來源未確認」，絕不報「無關」**——因為 absence 能證明的只有 absence。本輪先量化這桶在 r13 的規模，再給處置：

| 處置 | 適用 | 理由 |
|---|---|---|
| **保留並補引用** | 該檔確屬今輪某條旅程／拒因，只是紀錄以**別名**或**段落簡稱**提及 | 最常見成因；修的是紀錄，不是程式 |
| **保留並標 UNKNOWN** | 樹上存在、髒、但本輪無法把任何 hunk 連到已批准的提示條款 | 「來源未確認」是合法結案態（記憶：INSUFFICIENT_EVIDENCE is valid），不得為消除它而編造歸屬 |
| **建議移出 D、另批** | 與兩條旅程無關的**基礎設施**（例：根目錄 `run_r2.sh`／`candidate_status.sh`、`.github/workflows/secret-hygiene.yml`、`backend/postgres` 這個 0-byte 誤產物） | 混在 183 裡會讓 owner 以為每檔都背負客戶結論；`backend/postgres`（Nov 16 建立、0 bytes）本輪**只披露、不刪**（記憶：未知樹狀態要先調查，不刪除） |
| **禁止** | 以「先刪了比較乾淨」或「先套了再說」處理任何一桶 | 套用需 §8.7.7 的 D-apply 卡；刪除屬不可逆，另需明確批准 |

#### 8.7.6.3 隔離重現（**未執行**，與 Git 寫入分開批）

```
# 只讀預檢（不寫工作區、不動 index）
cd <repo> && git apply --check --verbose alethra-evidence/d-patch-round13/tracked/*.patch    # 任一檔非 0 ⇒ 停
# 套用＝另一張卡（D-apply）；本輪未批、未跑
```
**止損條件（寫死）**：`git apply --check` 任一支回非 0 ⇒ **不套用、不強制（無 `--3way`／`--reject`／`-f`）**，回報哪幾支、context 差在哪一行；套用後必須**立即**重跑 `cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh sqlite` 並比對 §8.6 的四格釘選上限（179／159／18／2）與 collected 3,847——**注意這會讓候選 digest 改變**（D 的 183 檔動 `backend/**/*.py`），因此重現後的 run 只能標「候選 `75d2cfdde8ed…` 之後的樹」，**不得**繼續引用 §8.6 那組身份行。紀綠檔不在包內 ⇒ 套用 D 不會覆蓋本紀錄。

### 8.7.7 逐卡批准表（一卡＝一個獨名操作；放置 ≠ 執行）

| 卡 | 一句話操作 | 依賴 | 本輪 | 批准欄（owner 填） |
|---|---|---|---|---|
| **A0a** | 唯讀預檢：`--json` 讀 migration tail＋`pg_ls_dir` 式比對，**0 新寫證據檔** | 無 | **已做**（§8.7.2／§8.7.1） | 無需批准（已發生的唯讀量測，如實記錄） |
| **A0b** | 把 A0 的輸出另存 1 支證據檔 | 無 | **未做** | ☐ |
| **A1** | `initdb` 建 r13 叢集（§8.7.3 那條命令，不建 DB） | 無 | NOT RUN | ☐ |
| **A2** | `pg_ctl start`＋`pg_isready`＋`-m fast stop` | A1 | NOT RUN | ☐ |
| **A3** | 建 3 角色＋DB `alethra_owner_test`＋`REVOKE ALL ON SCHEMA public FROM PUBLIC`＋四旗標讀回 | A2 | NOT RUN | ☐ |
| **A4** | `alembic upgrade head`（空庫→`d4e5f6g7h8i9`，57 revisions）＋tail 讀回 | A3 | NOT RUN | ☐ |
| **A5** | 兩支 index migration 的落地核對（只 index、無 FK） | A4 | NOT RUN | ☐ |
| **A6** | 三支 node id（§8.7.3.1）在 PG 上的**等鎖＋結算**互斥證據，判準 §8.7.3.2 | A5 ＋ 額度 ≥4 | NOT RUN | ☐ |
| **A7** | 另三支 node id（§8.7.3.1 後半）併發覆蓋 | A5 | NOT RUN | ☐ |
| **B-1** | §8.7.4.6 那張卡（沙箱化 uvicorn＋1 帳號＋consent＋等 05:00Z 自然鐘＋①–⑧讀回＋TERM 停止） | **A1–A4**（或另批 B-sqlite） | NOT RUN | ☐ |
| **B-2** | 扣額正控：seed `alethra_personal_e2e`＋training＋selection，讓 05:00 真的結算 | B-1 ＋ 另批 DB／training／quota | NOT RUN | ☐ |
| **B-kill** | live kill-switch 負控（第二個自然 tick 或手動門） | B-1 | NOT RUN | ☐ |
| **C-mode** | 選 §8.7.5.0 的 1／2／3 其一（launcher 他機／本機第二模式／不批 C） | — | **未選** | ☐ |
| **C-build** | `NEXT_DEV_DIST_DIR="$RUN/next-dist"` ＋ **先** export `NEXT_PUBLIC_API_URL` 再 `next build`（§8.7.5.5 前兩格判準） | C-mode | NOT RUN | ☐ |
| **C-stack** | 起後端（含 `CORS_ORIGINS`）＋`next start -p 3031`＋`/health/ready`＋preflight 讀回 | C-build | NOT RUN | ☐ |
| **C-auto** | `E2E_BROWSER_CHANNEL=chrome` 跑那 312 支（只報四個數） | C-stack | NOT RUN | ☐ |
| **C-journey** | §8.4 那一跳的瀏覽器驗收：**新寫 spec**（代碼變更）或 **C-human** 走查，二者要選一個 | C-stack（C-human 只需棧與 owner 本人） | NOT RUN | ☐ |
| **D-review** | 讀 `d-patch-round13/`（183 檔）＋歸屬表＋§8.7.6.2 桶的處置 | 無 | **材料已交付**；歸屬表於本節後產生 | ☐（審閱不需要批准；產生歸屬表＝唯讀工具，見 §8.8） |
| **D-repro** | `git apply --check`（只讀預檢，§8.7.6.3） | D-review | NOT RUN | ☐ |
| **D-apply** | 真的套用 183 檔到工作區 ＋ 立即重跑 sqlite 整套 | D-repro | NOT RUN | ☐ |
| **Git 寫入** | commit／push 任何一檔（含本紀錄與 D 產物） | 各自另批 | **未做** | ☐ |

**卡與副作用的對應**（供 owner 一次看齊，避免「批了一張等於批了一串」）：A1–A5＝本機叢集與一個新 DB（不碰共享 `ai_investment_lab`）；A6／A7＝在該 DB 內建帳本列＋**兩行程**；B-1＝一個沙箱化進程＋一個新 DB 的少量列；C-* ＝兩個新進程＋瀏覽器一扇＋（若選 C-journey）一筆真實決策寫入；D-apply＝工作區 183 檔變更＋候選 digest 失效；Git 寫入＝遠端可見。

---

## 第十三輪 §八・八：揭露——**本輪到底寫了什麼、做過哪些非純唯讀動作、哪些沒跑**（提示明文要求「所有未執行項如實紀錄」）

> 本節每一行的**時間與大小都是 `os.lstat()` 現測**（2026-11-20，UTC），不是記憶、不是轉述。判準：`mtime >= 2026-11-20T00:00:00Z` 才算「本輪寫入」，門檻之下的屬於前面幾輪。

### 8.8.0 工作區內的寫入（13 項，實測）

| # | 時間 (UTC) | 路徑 | 類型 | 位元組／檔數 | 屬於 |
|---|---|---|---|---|---|
| 1 | 02:25:00 | `alethra-evidence/base-ledger-round13/` | 新增（**已被取代**） | 3 檔 | §8.8.2 ③ |
| 2 | 02:37:02 | `backend/tests/test_formal_skill_unit_authorization_endpoint.py` | 未追蹤，本輪改寫 | 28,481 B | §8.3 R13-2 三缺口 |
| 3 | 02:46:08 | `frontend/src/lib/dailyEstimate.ts` | 未追蹤，本輪改寫 | 18,513 B | §8.2 前端 helper |
| 4 | 02:46:39 | `frontend/src/app/experiments/[id]/simulation/page.tsx` | **已追蹤 M** | patch 12,695 B（vs base ＋173／−18 行，11 hunks） | §8.2 模擬頁一致性 |
| 5 | 02:48:21 | `frontend/src/components/workspace/OrderPreviewPanel.tsx` | **已追蹤 M**（本輪唯一 added） | patch 12,392 B（vs base ＋153／−2 行，13 hunks） | §8.2 R13-3 明選血緣 |
| 6 | 03:01:03 | `backend/tests/test_manual_decision_books_linked_portfolio.py` | **已追蹤 M** | 全檔 126,786 B（patch 見 D 包） | §8.1 R13-4 逐層閉包 |
| 7 | 03:06:20 | `frontend/tests/dailyEstimate.test.mjs` | 未追蹤，本輪改寫 | 56,576 B | §8.4 第六拒因 |
| 8 | 03:06:44 | `backend/tests/test_daily_model_estimate.py` | 未追蹤，本輪改寫 | 136,967 B | §8.4 R13-5 |
| 9 | 03:07:34 | `backend/test.db` | **離槽寫入**（gitignored） | 3,043,328 B | §8.8.2 ② |
| 10 | 03:14:21 | `alethra-evidence/tools/check_base_values.py` | 新增工具（在 `backend/**/*.py` 之外） | 21,812 B | §8.5 基準重建 |
| 11 | 03:14:32 | `alethra-evidence/baseline-round13/` | 新增 | 5 檔 | §8.5 |
| 12 | 03:20:43→45 | `alethra-evidence/d-patch-round13/` | 重新產生 | 185 檔（`SHA256SUMS.txt` 覆蓋 184，不含自己） | §8.7.6 |
| 13 | 02:25→04:32:20 | `EXECUTION_TABLE_20261117.md`（本紀錄） | 未追蹤、**不在 D 包內** | 3,656 行（§8.7.7 寫完時點） | 全部 |

**加總**：產品／測試**原始碼 7 支**（#2–#8，與 §8.7.6.1 的 r12→r13 差集逐一對得上：added 1 ＝ `OrderPreviewPanel.tsx`、changed 6 ＝ #2／#3／#4／#6／#7／#8，removed 0）＋ 1 支產生器 ＋ 3 個證據目錄 ＋ 本紀錄。
**`backend/app/**` 本輪零寫入**（實測：今日無任何 tracked-M 落在 `app/`，`backend/app` 目錄 mtime 仍 11-18 13:54:29Z）——今輪那 7 支裡沒有一支是後端產品碼，這決定了 §8.6 那整套跑的結果**可以直接**代表客戶即將拿到的後端行為，不需要再跑一次。
**不宣稱純 append**：本輪在 §8.7 區間內做過中途改寫（例如撤銷 `:2798` 那格沙箱寫法）。舊節（§一–§七）我**無法**用 Git 證明未被改動（紀錄從未 commit，沒有可比的舊版快照），所以這裡只講「本輪寫入清單」，不講「只加不改」。

### 8.8.1 工作區外的產物（不在 Git、`$TMPDIR` 由 OS 回收——如實列，不刪）

| 時間 (UTC) | 路徑 | 內容 |
|---|---|---|
| 03:10:12 | `$TMPDIR/alethra-runs/sqlite-20261120T030957Z-8839/` | 5 檔齊；`command: ./scripts/run_tests.sh sqlite --collect-only`、`collected-node-ids: 3847`、`exit-code: 0` ⇒ **只收集、未執行**（`:491` 那條：collect-only 不判 ceiling、不結帳） |
| 03:11:38 | `$TMPDIR/alethra-runs/sqlite-20261120T031108Z-9172/` | 5 檔齊 ＋ `alethra-sqlite-test-9172.db`（1,765,376 B）；`selection-kind: explicit-selection`、`collected-node-ids: 36`＝§8.0／§8.5 引用的那「本輪 36 支 added 節點」同一格（命令全文在該 dir 的 `identity.txt` 首行：`run_tests.sh sqlite` ＋ 36 支具名 node id） |
| 04:03:54 | `$TMPDIR/alethra-runs/sqlite-20261120T032315Z-12706/` | **§8.6 整套** ＋ `alethra-sqlite-test-12706.db`（3,018,752 B） |
| 04:34:48 | `$TMPDIR/alethra-runs/sqlite-20261120T043444Z-42325/` | 僅 `collect.txt`＋`identity.txt`（見 §8.8.2 ④） |
| 04:03:55／04:04:29／04:24:22 | `$TMPDIR/r13-fullsuite-wrapper.log`（112,967 B）／`r13-added-outcomes.json`（4,772 B）／`r13-pw-list.txt`（49,023 B） | 中繼，非長期證據 |
| 04:15:04 | `$TMPDIR/r13-sandbox-probe/`（1 檔，690 B） | seatbelt 探針原始碼（§8.7.4.4 那四次量的東西） |
| 03:10:54／03:14:32 | `/tmp/r13_added.txt`（4,635 B）／`r13_removed.txt`（1 B）／`r13_gen.json`（10,582 B） | §8.5 差集的中繼檔 |

今日留在 TMPDIR 的 test DB：**2 支、4.6 MiB**（全庫 39 支 58.3 MiB 屬前面各輪殘留）。這些都是 `run_tests.sh:298` 那條預設路徑的自然產物，**不刪**（OS 自行回收；手上有別人這台的狀態時我不做清理）。可長期復現的是「同一命令＋同一 tree-digest」，不是這些檔。

### 8.8.2 本輪做過的**非純唯讀**動作（五條，逐條給判準與後果）

**① 真的執行了 `./start_acceptance_env.sh`**（為拿拒路原文，§8.7.5.0）：rc=2，印 `REFUSED (nothing was started): docker is not on PATH…`。**未啟動任何服務、未建任何容器、未寫檔**——它死在 `:34-35` 的 guard，而第一個寫檔動作在 `:63` 之後。這是本輪唯一「跑了一個會碰服務入口的腳本」，而它自己拒絕了；重跑一次仍會落在同一條拒路（無狀態）。

**② `backend/test.db` 於 03:07:34Z 被建立／覆寫（3.0 MiB）——這是本輪唯一的意外寫入，根因已定位**：`backend/tests/conftest.py:41` `_TEST_DB_PATH = os.environ.get("ALETHRA_TEST_DB", "sqlite:///./test.db")`。`run_tests.sh` 自己會蓋掉那個變數（pg 層 `:147` `export ALETHRA_TEST_DB="$ALETHRA_PG_TEST_URL"`；sqlite 層 `:193` `DB_FILE=${ALETHRA_SQLITE_DB:-${TMPDIR}/alethra-sqlite-test-$$.db}` ＋ `:225` `export ALETHRA_TEST_DB="$DATABASE_URL"`），**只有繞過 runner 直接喊 pytest** 才會落回 conftest 那個相對路徑（CWD＝`backend/` ⇒ `backend/test.db`）。後果：該檔被 `.gitignore:25`（`*.db`）排除 ⇒ **不在** porcelain 212、**不在** D 包、**不在**候選 digest（只算 `*.py`）；它那一次的輸出**不是 lane 證據**，本紀錄不引用任何來自該動作的數字。揭露，**不刪**：本輪沒有刪除批准，而 `.gitignore` 覆蓋的本機髒狀態屬 owner 現場（記憶：未知樹狀態要先調查，不刪除）。

**③ `base-ledger-round13/` 已被 `baseline-round13/` 取代，兩者都留在樹裡**：前者（02:25:00Z，3 檔：`BASE_VALUES.json`／`.md`／`SHA256SUMS.txt`）是第一次嘗試，缺 README 與逐節點差檔；後者（03:14:32Z，5 檔，多 `README.txt` ＋ `SELECTION_DIFF_Pinned_to_Candidate.txt`）才是 §8.5 引用的那個。**未刪**（刪目錄屬破壞性動作，且本輪沒有刪除批准）；引用時**只認 `baseline-round13/`**，看到 `base-ledger-round13/` 請當做遺留，不要拿它的 BASE_VALUES 對數。

**④ 04:34:44Z 的 `--digest-only` 探針**（為在寫 §8.8 前重新綁候選身份）：該旗標**不存在**於 pytest，collector 回 `ERROR: … unrecognized arguments: --digest-only`、`collected-node-ids: 0`、`selection on this tree: <the collector printed no selection line>` ⇒ `SELECTION` 為空，走 `run_tests.sh:412-418` 那條 `refusing: no test was selected` 拒路（**rc=2**；該訊息印在 stderr，我當時接了 `2>/dev/null` 把它丟掉了，所以這裡引的是**分支程式碼＋run dir 實物**而不是螢幕原文；外面那層 `| tail -3` 顯示的 0 是 `tail` 的狀態，不是腳本的）。實測**未建立** `alethra-sqlite-test-42325.db`（`os.path.exists` 回 False）、無 `junit.xml`／`pytest.log` ⇒ **未執行任何一支測試**，副產物只有該 run dir 的 2 檔。它順帶再次量到 `tree-digest-before = 75d2cfdde8ed…`、`porcelain: 212 path(s) sha256 885c29f1…`（§8.0／§8.6 同值 ⇒ 候選身份在本輪尾端仍成立）。**教訓（下輪不再犯）**：要 digest 就讀既有 `$RUN_DIR/identity.txt`、或照 §8.6 那條算式自己算、或用**真的**存在的 `--collect-only`（本輪 03:09:57Z 那次 8839 就是它，見 §8.8.1）——不要憑空發明旗標。同一類錯誤本輪尾端又犯了一次：04:47:49Z `./scripts/run_tests.sh collect`（`collect` 不是合法 mode）⇒ 在建立 run dir **之前**就 rc=2 退出，實測 `$TMPDIR/alethra-runs/` 最新一筆仍是 04:34:48Z 那個，**沒有新增產物**。

**⑤ 四次 seatbelt 探針＋兩次 `playwright test --list`**：探針只在 **loopback** 做 `bind`／`connect`（`$TMPDIR/r13-sandbox-probe/probe.py`），**零外呼**；`--list` 不起瀏覽器、不觸發 `webServer`（config 那條是 no-op，§8.7.5.2），只寫出 `r13-pw-list.txt`（312 tests／32 files，§8.7.5.3 的來源）。

### 8.8.3 未執行清單（提示：「所有未執行項如實紀錄」）

| 項 | 本輪 | 攔住它的是什麼 | 判準在哪 |
|---|---|---|---|
| A1–A5（initdb／pg_ctl／角色＋DB／`alembic upgrade head`／兩 index） | **NOT RUN** | 未獲批准（本提示不是批准） | §8.7.3 |
| A6／A7（六支競態 node id 的**等鎖＋持久結算**） | **NOT RUN** | 需 A5 ＋ 額度 ≥4；本機無 PG 進程（實測 `pgrep` 無 postgres） | §8.7.3.1／§8.7.3.2 |
| **行程級崩潰**（崩在中間的那一種） | **NOT RUN** | sqlite lane 對需要真 PG 的節點一律**具名跳過**；跳過不是通過（§8.6.6 是實物證據） | §8.7.3 末行 |
| B-1／B-2／B-kill（自然 05:00Z cron 隔離棧） | **NOT RUN** | 未批；且依賴 A1–A4 | §8.7.4.5／§8.7.4.6 |
| C-mode／C-build／C-stack／C-auto／C-journey | **NOT RUN**（**瀏覽器驗收本輪 0 次**） | 未選 §8.7.5.0 的三個選項之一；launcher 已實測不可用 | §8.7.5.5 |
| D-repro（`git apply --check`）／D-apply | **NOT RUN** | 未批；apply 會讓候選 digest 失效 | §8.7.6.3 |
| **Git 寫入**（commit／push／stash／branch／checkout） | **未做** | 未批。實測：HEAD 仍 `5a34e3fed0f2…`；`git reflog` 最後一筆 2026-11-16 09:49:42 +0800；`git stash list` 2 筆（11-06／11-07，非本輪）；11-19 之後 commit 數 **0** | §8.7.7 末行 |
| **重釘**（selection／skip ceiling／migration tail） | **0 次** | 實測 `backend/tests/skip_budget.json` mtime 11-19 05:53:48Z，內容 `collected 3,811／total 179／pg_only 159／live_stack 18／other 2` 未變；`scripts/check_migration_tail.py` 11-19 08:33:58Z | §8.5 末段（釘選與本輪候選的偏差**已量化**，沒有靠重釘把它抹掉） |
| **外呼**（TwelveData／Kraken／任何 HTTPS） | **0 次** | `DATA_PROVIDER=demo`；`TWELVEDATA_API_KEY` 為**空值**（§8.7.0 #8）；§8.8.2 ⑤ 的探針只碰 loopback | §8.7.4.4 |
| **migration 執行**（對任何 DB） | **0 次** | 無 PG；sqlite lane 不跑 alembic（那是 §8.6 那套的已知限制，不是本輪新增的洞） | §8.7.3 的 A4 行 |

### 8.8.4 前面幾輪留下、本輪**保留不動**的兩件事

1. **SIGKILL 偏離**（第十二輪 §七，`:3021` 起）：本輪**未重做、未清理、未宣稱已修**。它仍是「以 SIGKILL 終止的那一次不屬於優雅停止證據」這條記錄，任何引用 A1–A7 的人都必須連帶讀它。
2. **`base-ledger-round13/` 與 `backend/postgres`（0-byte 未追蹤目錄）**：都只揭露、不刪除（§8.8.2 ③／§8.7.6.1）。

### 8.8.5 歸屬表（已產生：04:42:45Z，**只讀工具**，未碰 `backend/**`、未碰釘選、未碰 Git）

```
cd <repo> && python3 alethra-evidence/tools/build_d_attribution.py \
  --manifest alethra-evidence/d-patch-round13/MANIFEST.json \
  --previous alethra-evidence/d-patch-round12/MANIFEST.json \
  --record EXECUTION_TABLE_20261117.md \
  --out alethra-evidence/d-attribution-round13
```
工具自印 `wrote …/d-attribution-round13: 183 files attributed, 0 unclaimed`，**rc＝0**；產物 3 檔（`ATTRIBUTION.md` 46,787 B／`ATTRIBUTION.json` 110,655 B／`SHA256SUMS.txt` 164 B，只覆蓋前兩檔）。**綁定的紀錄快照**：3,734 行、sha256 `bc539cade0be2b95554c497f60c7c73609a8f0998c56f68c41090b9ae133264e`（產生**前**現算）。`--out` 已存在時工具拒絕覆寫（`:186-188`，rc=2），所以這張表不可能被悄悄重刷。

**一、分桶（機產規則在 `ATTRIBUTION.md` 開頭，讀者可以不同意規則而不是不同意我手打的清單）**

| 桶 | 檔數 | 備註 |
|---|---:|---|
| 旅程一：每日估計的失敗可見性 | 13 | 關鍵字規則命中 `estimate`／`dailyEstimate`／`personalization` 等 |
| 旅程二：決策的來源血緣與匯出 | 23 | 命中 `decision`／`lineage`／`OrderPreview`／`export` 等 |
| 兩條旅程以外 | 148 | backend/tests 72＋backend/app 34＋frontend/src 15＋frontend/e2e 13＋scripts 5＋其他 9 |
| **紀錄未聲領（來源未確認）** | **0** | ⚠️ 見下面「0 不等於什麼」 |
| 加總自洽 | 13＋23＋148＝**184**＝183＋**1** | 重疊 1 支同時落兩桶，實測是 `backend/tests/test_simulation_daily_tick.py`（名字裡既有 `simulation` 又有 `daily_tick`）；不是算錯 |

對照 r12 包：**新增 1／移除 0／內容變動 6／未變 176**（`git_state` 實測 M 127＋?? 56＝183）。**換句話說：D 包 183 檔裡只有 7 檔（3.8%）是這輪真正動過的內容**，其餘 176 檔與第十二輪那份位元組相同——owner 這輪的審閱工作量應該落在那 7 檔，不是 183 檔。

**二、本輪動過的 7 檔落在哪個桶、第一次被紀錄點名在哪一行**（全部由機產表讀出，行號可直接開檔）

| 路徑 | 桶 | 紀錄首領行 |
|---|---|---|
| `backend/tests/test_daily_model_estimate.py` | 旅程一 | `:997` |
| `frontend/src/lib/dailyEstimate.ts` | 旅程一 | `:1499` |
| `frontend/tests/dailyEstimate.test.mjs` | 旅程一 | `:1500` |
| `backend/tests/test_manual_decision_books_linked_portfolio.py` | 旅程二 | `:292` |
| `frontend/src/app/experiments/[id]/simulation/page.tsx` | 旅程二 | `:209` |
| `frontend/src/components/workspace/OrderPreviewPanel.tsx` | 旅程二 | `:501` |
| `backend/tests/test_formal_skill_unit_authorization_endpoint.py` | **兩條旅程以外** | `:2936` |

**三、「0」不等於什麼（這格必須自己戳破，否則它是一個會誤導人的綠）**
桶的定義是**該路徑（含去掉首段的寫法與純檔名）在紀錄 3,734 行的任何一行的任何位置出現過至少一次**。所以 `0` 只證明「**沒有檔是完全沒被點名的**」，**不**證明那些 hunk 正當、也**不**證明是這一輪做的。我把更嚴格的那一版也量了——按「首領落在哪一輪」分解 183 檔：

| 首領所在輪（`claiming_round`） | 檔數 |
|---|---:|
| 第十二輪 §二（客戶結果清單，一次點名整包） | **126** |
| 第一輪之前的正文／總覽段 | 27 |
| 第三輪補記 P8 | 7 |
| 第四輪（後半段＋收尾） | 10 |
| 第五輪 | 5 |
| 第七輪 | 3 |
| 第十輪 | 3 |
| 第十一輪 | 2 |
| **第十三輪** | **0** |

**0 是規則的必然結果，不是缺陷也不是成績**：該欄取的是**最早**那一次點名，而 183 支的檔名在第十二輪 §二（標題 `:2608`；那 126 支的首領行實測分佈 `:2661`–`:3009`）就被整包點名過，所以任何後續輪次都無法成為「首領」。要問「本輪聲領了什麼」，正確讀法是**上面第二表那 7 檔**（由 `package_delta` 決定，與首領規則無關）＋ §8.8.0 的寫入清單。

**四、提示要的那份「紀錄查無引用」處理意見**
本輪該桶**為空**，所以沒有檔案需要處置——但這是**因為紀錄已經點名過全部 183 支**，不是因為它們都有本輪的正當性。因此 §8.7.6.2 那四種處置（刪除／移出 D 另批／保留並標 UNKNOWN／禁止）本輪的適用對象改成：**148 支「兩條旅程以外」**。其中 owner 真正要裁的是這三群（實測分解）：
1. **`backend/app/**` 34 支**：產品碼 diff，但本輪**零**改動（§8.8.0）⇒ 它們是**前面幾輪**累積的變更，若 owner 只批今輪那兩條旅程，這 34 支就該走「移出 D、另批」，不該跟著 D-apply 一起落地。
2. **`frontend/e2e/**` 13 支**：spec 改動，與 §8.7.5.3「今輪兩條旅程 0 spec」是同一件事的兩面——它們測的是**舊**旅程。
3. **根目錄腳本／CI 檔／0-byte `backend/postgres`**：見 §8.7.6.2 第 4 列（**禁止**把誤產物當交付）。
**不建議**在本輪動其中任何一檔：`D-apply` 是整包動作，拆包需要新批一張「只套 7 檔」的卡（那 7 檔＝本節第二表，`git apply` 只喂那幾支 patch）。**這一張卡本輪沒有寫，因為它需要 owner 先選「拆包」還是「整包」。**

**五、產生之後又改了紀錄，這張表還算嗎**
本小節是接在快照（3,734 行／`bc539cad…`）**之後**附加的，所以如實講：附加文字只會**增加**點名，不會移除；而桶的判準是「有無被點名」與「最早點名行」——兩者都**只能變得更『已聲領』**，`package_delta` 那側完全由兩份凍結 manifest 決定，不受紀錄影響。因此**這五個數字（13／23／148／0＋1／0／6／176）在重跑後不會變**；會變的只有紀錄的行數。要驗證請用同一份快照（`bc539cad…`），不要覆蓋本目錄。
**工具本體的一處瑕疵（揭露，不偷偷改）**：`build_d_attribution.py:46` 的桶名寫成「失敗可见性」（簡體 `见`），機產表照原樣輸出（上面兩張表為了好讀，把那個字正規化成「見」；`ATTRIBUTION.md` 檔內仍是產生器原字）。我**沒有**為了美觀去改產生器——改它會讓桶鍵變名、跨輪比較對不上；要改請另批一張「改工具＋重跑 r12 表以維持可比」的卡。

---

## 第十四輪 §九（2026-11-20 15:0x–15:4x HKT）：**把 214 檔拆成可審範圍**——7 檔逐檔＋536 檔閉包＋六組分類＋仍未驗證的旅程，A0–D 正文在其後

提示原話：「下一份集中交付以客戶結果與可審範圍為首：先列 7 檔真正變動、依賴閉包、148 檔分組、仍未驗證的旅程，再列 A0–D 的可批准正文。」——**本節就是按那個順序排的**，9.1 開始是客戶結果與可審範圍，9.8 開始才是批准正文。

提示末段明文，本節每一格都在它之下寫：「**未獲精確批准前，不啟動 PG、服務、browser、外呼，不重釘、套用 patch，也不做任何 Git 寫入。**」本輪實測的 Git 寫入＝**0**（`git rev-parse HEAD` 前後同為 `5a34e3f…`，§9.0 身份表 `HEAD` 列＋§9.9.2 零執行核對），重釘＝**0**（兩支受控檔 sha 未變，§9.6.4），patch＝**0**（未 `git apply` 任何東西），PG／服務／瀏覽器／外呼＝**0**（所有 lane 與 pytest 都在 `DATA_PROVIDER=demo` 下跑；`pgrep -fl postgres` 本輪每次現測皆無輸出）。唯一新增的執行＝**sqlite 離線 lane 與直接 pytest**，其副作用逐條列在 §9.9。

### 9.0 本輪受測內容的身份（先固定；**全部 15:2x–15:3x HKT 現測**，無一格沿用第十三輪的數字）

| 項目 | 逐字現測值 | 量法 |
|---|---|---|
| HEAD | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` | `git rev-parse HEAD` |
| 髒集合（lane 慣例） | **214** 條路徑、sha256 `22e305b7cd7e5c129a03aa8fc13786363f5061813c0b3309c6a72cacd9e22f41` | `git status --porcelain \| shasum -a 256`（**未展開未追蹤目錄**）——本輪**連跑兩次同值**（15:1x 與 15:3x），與 run dir `identity.txt` 自印的那行逐字相同 |
| 同一集合的另一種展開慣例 | **1,086** 條路徑、sha256 `ad7fb5f5e380b5545a1255cbb28b59d629c517e41e9ba94dcfd9e14e6d5b5e7f` | `git status --porcelain -uall`。兩個數都對，**物件不同**：前者數的是 Git 給的路徑條目（`?? alethra-evidence/` 算 1 條），後者把該目錄下每個檔案展開。**本節往後所有「214」皆指前者** |
| **本輪完整候選（樹摘要）** | **`162d0be4766e345268d6df90447b41f69d9e6a69d04f3f943342db483ffa7c7d`**／**527** 支 `.py` | **只能用 lane 自己的算式**复現：`cd backend` 後跑 `scripts/run_tests.sh:373-379` 的 `tree_digest()`（`find . -name '*.py' -type f` 減掉 `.venv`／`venv`／`__pycache__`／`node_modules`，`LC_ALL=C sort`，逐檔 `shasum -a 256` 再對清單 `shasum -a 256`）。本輪手算一次得同值；run dir `sqlite-20261120T072037Z-90507/identity.txt` 的 `tree-digest-before:` 也逐字是它。**對照第十三輪：`75d2cfdde8ed…`／526 支**（§8.7.0 #1）⇒ 樹換了，**r13 的 A／B／C 正文綁的已不是這棵樹** |
| 整套收集 | **3,867** | `${TMPDIR}alethra-runs/sqlite-20261120T070931Z-85151/collect.txt`：末行逐字 `3867 tests collected in 3.79s`、`exit-code.txt`＝`0`；節點行以 lane 自己的 pattern 數得 3,867（§9.0.1） |
| 受控釘選（**本輪未動**） | `backend/tests/collected_nodes.txt`＝**3,811** 列、`backend/tests/skip_budget.json`＝`collected 3811 / total 179 / pg_only 159 / live_stack 18 / other 2` | `wc -l` ＋ `python3 -c json.load`。該檔 `provenance` 自己記的來源 run dir＝`sqlite-20261119T051257Z-80770`、`tree-digest`＝`50130606…`、porcelain **206**／`da75ac96…`、`exit_code`＝**"1"** ⇒ 釘選描述的是**三天前另一棵樹上一次以非零退出的 run**（§9.6） |
| 解釋器 | `backend/.venv/bin/python`；lane fingerprint 逐字 `python 3.12.16 \| pytest 8.3.3 \| sqlalchemy 2.0.36` | run dir `identity.txt` |
| migration 尾端 | `status=OK`、`head=d4e5f6g7h8i9`、`parsed_heads=["d4e5f6g7h8i9"]`（單一）、`revision_count=57`、`module_count=57`、`unreachable_from_head=[]`、`expected_tail`＝`parsed_tail`＝`[f2a3b4c5d6e7, b2c3d4e5f6g7, c3d4e5f6g7h8, d4e5f6g7h8i9]` | `cd backend && .venv/bin/python scripts/check_migration_tail.py --json --with-alembic` ⇒ **rc=0**（15:2x 現測；`--with-alembic` 讓 alembic 自己的解析也必須同意，不只我方 parse）。鏈**本輪未變**，所以 A4 的目標 head 與 r13 同值——這是「湊巧同值」，不是「沿用」 |

#### 9.0.1 節點差 3,811 → 3,867：**−4／＋60**（逐檔拆解，兩側都是实物清單）

先說一個**會自己造出謊言的量法**，因為我本輪踩過：用 `grep '^tests/'` 數 node id 會得到 **3,860**，看起來像「刪掉了 11 支」。真相是**有 3 支測試檔住在 `backend/` 根而不是 `backend/tests/`**，它們的 id 本來就不以 `tests/` 開頭（共 7 支 id）：

```
test_explicit_provider_selection.py::test_available_providers
test_explicit_provider_selection.py::test_create_session_with_demo_provider
test_explicit_provider_selection.py::test_create_session_with_explicit_provider
test_explicit_provider_selection.py::test_create_session_with_invalid_provider
test_explicit_provider_selection.py::test_create_session_without_provider_multiple_available
test_provider_isolation.py::test_provider_isolation
test_provider_isolation_sql.py::test_provider_isolation_sql
```
這 7 行**在釘選檔裡也在**（現測 `grep -vcE '^tests/' collected_nodes.txt` ＝ **7**）⇒ 釘選的寫法沒有缺陷，有缺陷的是我的 pattern。**唯一正確的算法是 lane 自己那一條**（`run_tests.sh:419`）：`grep -cE '^[^[:space:]]+\.py(::.*)+$'`。兩側都用它，再 `comm` 比對：

| 側 | 值 |
|---|---|
| 現樹收集 | 3,867 |
| 釘選 | 3,811 |
| 釘選有、現樹**無**（＝本輪刪除／改名的 id） | **4**：`tests/test_lane_entry_contract.py::test_the_collector_is_read_at_one_quiet_level_whatever_the_caller_asked[quiet0]`…`[quiet3]` |
| 現樹有、釘選**無**（＝新增 id） | **60**，逐檔：`test_daily_estimate_two_workers.py` **16**／`test_conftest_sqlite_default.py` **15**／`test_lane_entry_contract.py` **9**／`test_manual_decision_books_linked_portfolio.py` **8**／`test_daily_model_estimate.py` **8**／`test_formal_skill_unit_authorization_endpoint.py` **4**（加總＝60） |
| 自洽 | 3,811 − 4 ＋ 60 ＝ **3,867** ✓（`test_lane_entry_contract.py` 淨 ＋5 ＝舊 4 格換新 9 格，見 §9.5.3） |

**這一行是本節最重要的範圍事實**：本輪動過的檔**刪掉了 4 個已被釘選的 id**。所以在「不重釘」这条禁令下，**任何整套 A 卡都不可能 PASS**——不是產品壞了，是判準與樹不同步。拒絕原文（15:2x 在同一棵樹上只讀重現，物件＝被 SIGTERM 中斷的那次整套 run dir `sqlite-20261120T070034Z-80037`）：

```
refused: this whole-suite run collected 3862 tests but the budget pins 3811: the selection changed (files added, removed or deselected), so the ceiling was not measured against this suite. Re-measure and regenerate the budget from a real lane log.
```
rc＝**2**（`cd backend && .venv/bin/python scripts/skip_budget.py "$RUN_DIR/pytest.log" check --budget tests/skip_budget.json --identity "$RUN_DIR/identity.txt"`）。該 run 的 3,862 不等於 3,867，因為它**在 13% 時被我中斷**，pytest 打的那行 partial 摘要就是 3,862（揭露在 §9.9.2）。**重釘與否是 owner 的政策裁決，本輪不代為決定，也不拿它當已批准。**

### 9.1 交付順序第一項：**7 檔真正變動**逐檔（不是 183 檔，也不是 214 檔）

「7 檔」這三個字的來源必須可反駁，所以先交規則：`alethra-evidence/r14-review-scope/REVIEW_SCOPE.md` §〇（`:16`）把五個相鄰包兩兩做**位元組差**，`d-patch-round12 → d-patch-round13` 那一列實測＝新增 1／移除 0／**內容變動 6**／未變 176；再加上「新進包且動過」1 支，得到第十三輪那 7 檔。下面是**逐檔正文**，附件位址全部實際存在（`diffs/` 共 16 檔，含 3 支 0-byte 的 patchdelta，意義見 §9.1.3）。

| # | 路徑 | Git | ＋／−（hunk） | diff 附件（位元組） | 用途（這檔改了什麼行為） | 掛哪條客戶旅程／基準工作 | 直接依賴（閉包表 §二） | 覆蓋它的測試 |
|---:|---|---|---|---|---|---|---|---|
| 1 | `backend/tests/test_daily_model_estimate.py` | `??` | ＋93／−1（2）（對 d-patch-round12） | `diffs/backend__tests__test_daily_model_estimate.py.diff`（4,933） | 第六個拒因與**「已保存的失敗」重載後讀得到**：新增節點斷言 `errors` 欄經服務寫入、經 door 翻譯、經面板可定位（§8.4、§8.2.1） | **旅程一**（每日估計的失敗可見性） | 直接 import 8 支髒＋17 支乾淨（`app/auth/jwt.py`、`app/ml/personal_trainer.py`、`app/models/audit_event.py`、`app/models/billing.py`…）；**被 1 支 import** ⇒ **不可獨立保存** | 本檔自身（現樹 8 支 id 不在釘選內）；前端側由 `frontend/tests/dailyEstimate.test.mjs:1114` 「the failure field is written by the service and translated at the door」對應 |
| 2 | `frontend/src/lib/dailyEstimate.ts` | `??` | ＋79／−0（1） | `diffs/frontend__src__lib__dailyEstimate.ts.diff`（4,029） | 把面板要講的話收斂成**單一 derive 層**：`estimateState`／`lineageState`／`pressOutcome`／`failureNotice` 等（§9.1.2 的 43 支測試逐條點名） | **旅程一**＋**旅程二**（明選血緣的拒因文案也從這裡出） | import 0／0；**被 3 支 import**（含兩個面板與測試）⇒ 可以單獨保存（依賴乾淨），但改它會波及 3 支 | `frontend/tests/dailyEstimate.test.mjs` **43 支全過**（本輪 15:2x 重跑） |
| 3 | `frontend/tests/dailyEstimate.test.mjs` | `??` | ＋180／−10（6） | `diffs/frontend__tests__dailyEstimate.test.mjs.diff`（10,980） | 上面那個 derive 層的**正面-controls**：三態不可互換（`:293`）、零天是量測不是缺值（`:308`）、舊 payload 相容（`:191`、`:745`）、失敗可定位（`:1034`、`:1098`）、工作區明選（`:670`、`:708`） | **旅程一**（＋面板那半條旅程二） | import 0／0、被 0 支 import ⇒ **可獨立保存** | 自身即測試；43／43 通過（`node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test`） |
| 4 | `backend/tests/test_manual_decision_books_linked_portfolio.py` | ` M` | ＋863／−6（3）（對 HEAD） | `diffs/backend__tests__test_manual_decision_books_linked_portfolio.py.diff`（43,461） | 三入口實際寫表＋`HumanDecisionEvent` 血緣儲存＋**並發生命週期**三支（同一 idempotency key 只留一張單／過期讀取不能蓋掉已提交／提交與 advance 賽跑仍一致） | **旅程二**（決策的來源血緣與匯出）；同時是 **A6／A7 的載體** | import 8 髒＋5 乾淨（`app/models/audit_event.py`、`billing.py`、`market_data.py`、`simulation.py`…）；被 3 支 import ⇒ **不可獨立保存** | 本檔即測試（sqlite 層 8 支 id 為本輪新增）；A6／A7 那 3 支節點**在 PG 未跑**（§9.7） |
| 5 | `frontend/src/app/experiments/[id]/simulation/page.tsx` | ` M` | ＋173／−18（11） | `diffs/frontend__src__app__experiments__[id]__simulation__page.tsx.diff`（12,695） | 模擬頁把明選血緣**只透過 derive 層**送給一個知道該欄位的 client（測試 `:551` 點名的就是這 behaviour） | **旅程二** | import 3 髒＋1 乾淨（`frontend/src/lib/format.ts`）；被 0 支 import ⇒ **不可獨立保存**（依賴側） | 前端 unit 側由 `dailyEstimate.test.mjs:551`／`:646`；**尚無瀏覽器 spec**（§9.7 瀏覽器欄） |
| 6 | `frontend/src/components/workspace/OrderPreviewPanel.tsx` | ` M` | ＋153／−2（13） | `diffs/frontend__src__components__workspace__OrderPreviewPanel.tsx.diff`（12,392） | 工作區面板**接通明選血緣**（與模擬頁同鍵、同一句話），並在 owner 未勾時不送該欄位 | **旅程二**；提示點名的「工作區面板的血緣接通」就是這檔 | import 3 髒＋1 乾淨（`components/shell/WorkspaceContextBar.tsx`）；被 0 支 import ⇒ **不可獨立保存**（依賴側） | `dailyEstimate.test.mjs:670`（勾了才送）＋`:708`（兩個門一句話、不帶重試文案）；**未**在瀏覽器驗過 |
| 7 | `backend/tests/test_formal_skill_unit_authorization_endpoint.py` | `??` | ＋287／−8（5） | `diffs/backend__tests__test_formal_skill_unit_authorization_endpoint.py.diff`（14,427） | #177 的**端點層**三缺口：formal API 寫入前的 skill-unit 授權、逐技能 selection 優先於 record alias、未驗證 `linked_experiment_version_id` 的拒絕 | **兩條旅程以外**（屬**基準工作**：它是釘選／授權閘門的回歸網，不是客戶點得到的結果）——第十三輪就把它列在「兩條旅程以外」桶（§8.8.5 第二表） | import 4 髒＋2 乾淨（`app/models/billing.py`、`quota.py`）；被 1 支 import ⇒ **不可獨立保存** | 本檔即測試（本輪 4 支 id 為新增）；它**不**是任何 A 卡節點 |

**判讀規則（寫死，免得讀者以為我手挑）**：上表 #1／#2／#3／#7 是**未追蹤檔**，「＋／−」是**對最新包內上一版**（`d-patch-round12`）的差；#4／#5／#6 是**已追蹤檔**，對 **git HEAD `5a34e3f`**。兩把尺不能互換，所以 §一（`:30`）那張機產表把「附件基線」單獨列一欄，本表照抄。

#### 9.1.2 本輪（r14）真正寫進樹的 4 檔＋1 檔文件

提示要「保留第十三輪 7 檔變動歸屬、寫入清單」——**本輪的寫入清單是下面這份，另有 §9.9.1 的紀錄本身**：

| 路徑 | Git | ＋／−（hunk） | 為什麼動它 | 附件 |
|---|---|---|---|---|
| `backend/tests/conftest.py` | ` M` | ＋40／−4（3） | 提示明文：「修正或拒絕 `backend/tests/conftest.py` 預設 `sqlite:///./test.db` 直接落地的行為」——見 §9.4 | `diffs/backend__tests__conftest.py.diff`（3,491）＋ `…conftest.py.d-patch-round13_to_now.patchdelta.diff`（2,389） |
| `backend/scripts/run_tests.sh` | ` M` | ＋391／−6（4）；檔案從 **153 行 → 538 行**（HEAD 版 `git show HEAD:… \| wc -l` ＝ 153） | 提示明文：「在 runner 說明及參數處理中使用明確的可用旗標」＋本輪量到的收集器 verbosity 缺陷——見 §9.5 | `diffs/backend__scripts__run_tests.sh.diff`（22,039）＋ `…run_tests.sh.d-patch-round13_to_now.patchdelta.diff`（6,479）。⚠️ **r13 包記的是 ＋346／−5**，本輪又多 ＋45／＋1，差量在 patchdelta 那份裡 |
| `backend/tests/test_conftest_sqlite_default.py` | `??` 新增 | ＋100／−0（1）；**15 支節點**（`def test` 8 個 × parametrisation；現測 `pytest --collect-only -q` ＝ 15） | §9.4 要求「只作最小修改**並補直接測試**」 | `diffs/backend__tests__test_conftest_sqlite_default.py.diff`（4,746，整檔） |
| `backend/tests/test_lane_entry_contract.py` | `??` | **＋17／−6（1）**（對 d-patch-round13；非對 HEAD）；現樹收集 **42 支節點** | 把 §9.5 的 9 種 verbosity 形狀變成會拒絕的契約 | `diffs/backend__tests__test_lane_entry_contract.py.diff`（2,005） |
| `docs/minimal-ci-spec.md` | ` M` | ＋3／−3 | 文件裡那條「直接跑 pytest 也行」的入口就是 §9.4 剛堵掉的洞；不改文件，文件會繼續教人污染證據 | 不附 diff（§一之補：tracked 產出 2,476 B） |

`EXECUTION_TABLE_20261117.md`（本檔，`??`，附加本節前 **3,793 行／676,300 B／sha256 `1401110d11347e9bf63cdedc01953738d0d143e5cd5e702a1e13256bd0f4a4f8`**）是第 6 項寫入，列在 §9.9.1，**不算代碼**。

#### 9.1.3 附件本身（D 卡 1 的實體）

`alethra-evidence/r14-review-scope/`——**19 支檔案**（頂層 3＋`diffs/` 16，`find -type f` 現測）：`REVIEW_SCOPE.md` **1,223 行／110,034 B／`273433e415be9e03fe0f76cb31f8e2e8b893c293ab723ff5a5b1df7362c4a9fb`**、`REVIEW_SCOPE.json` **12,748 行／3,123,363 B／`588592afb3ab21db6af7efaff78b6ec6123b4c074e273321832ee3ee66009bdf`**、`SHA256SUMS.txt` **18 條／2,331 B／`eee05f1fbee9b67e49a0bd4264a0d0e3e728d79e79c54f2be5eb73541be12a22`**、`diffs/` 16 檔。**「18」是清單條數，不是檔案數**：清單列頂層 2 支（它**不列自己**）＋`diffs/` 16 支＝18 條，对应 19 支檔案中的 18 支。本輪 15:5x 重跑 `shasum -a 256 -c SHA256SUMS.txt`：**18 條全 OK、0 條 FAIL**，三支頂層檔的 bytes／sha 與上列逐字相同（未因本節撰寫而變動）。產生器：`alethra-evidence/tools/build_r14_review_scope.py`（`GROUP_RULES` 在 `:54-76`，閉包過濾在 `:422-423`）。

⚠️ **`diffs/` 裡有 3 支 0-byte 檔**，那是如實輸出不是產物損壞：`test_manual_decision_books_linked_portfolio.py`／`simulation/page.tsx`／`OrderPreviewPanel.tsx` 的 `…d-patch-round13_to_now.patchdelta.diff`。**判讀**：這三支**自 r13 包以來一位元組都沒變**，所以「包到包」的差集是空的；它們的**內容** diff 是 §9.1 表裡那三支非 0-byte 的同名檔（對 HEAD）。讀者若要檢查「本輪有沒有偷動已交付的檔」，看的就是這三個 0。

#### 9.1.4 213 與 214 的差（必須自己戳，否則表看起來少了一檔）

機產表 §一＋§一之補二列舉 **213** 支，porcelain 是 **214** 條。差的那一條是 **`?? alethra-evidence/`**（JSON `collapsed_untracked_dirs: ["alethra-evidence/"]`）。**後果有兩層**：①審閱清單沒有漏檔，只是把整個證據樹折疊成一條——因為它是**產物，不是交付**；②**證據樹本身不在 Git 裡**，所以 D 卡 3「發布範圍」若不另加一條「證據樹要不要入庫」的裁決，預設答案是**不入**（AGENTS.md 也禁止未審核日誌入庫）。

### 9.2 交付順序第二項：**依賴閉包 536**——不能只審 7 檔而忽略其呼叫與被呼叫

提示原話：「另交每個變動檔的上游／下游依賴閉包，不能只審 7 檔而忽略其呼叫與被呼叫的檔案。」機產結果（`REVIEW_SCOPE.md` §二之補，`:309`）：

| 格 | 值 |
|---|---|
| 種子 | **11** 支（＝§9.1 的 7 支 ＋ §9.1.2 的 4 支代碼檔；逐字清單在 §二之補 `:311`） |
| 閉包（BFS：`import` ∪ `被 import`，含種子） | **536** 支 |
| 其中目前也髒（已在待審／待保存集合內） | **136** 支 |
| 目前乾淨、只能從 HEAD 讀（**不在任何包內**，重現必須另外帶） | **389** 支 |
| 自洽 | 136 ＋ 389 ＋ 11 ＝ **536** ✓（JSON：`size 536, seeds 11, also_dirty 136, clean_only_in_head 389`；種子距離＝0，故不重複計入兩個桶，規則在產生器 `:422-423`） |

**這對 owner 的審閱工作量意味著什麼（是本節的實質結論，不是裝飾）**：
1. **審 7 檔不夠**：那 7 檔裡有 4 檔**不可獨立保存**（§9.1 表的依賴欄：`test_daily_model_estimate.py`、`test_manual_decision_books_linked_portfolio.py`、兩支 `.tsx`），因為它們 import 的髒檔本身也在改動集內。
2. **但也不必審 536 檔**：536 裡有 **389 支是 HEAD 上乾淨、本輪沒碰的檔**——它們只是「被叫到」，不是「被改過」。正確的分層是：**改動的 11 支（§9.1）→ 一起髒的 136 支（§9.3 分組後才是可審單位）→ 乾淨的 389 支（只需存在，不需審閱內容）**。
3. **隔離重現（D 卡 2）因此需要 183＋389 而非只有 183**：只複製最新包那 183 檔**無法 import**（閉包會缺檔），這不是修辭，是 §一之補二那 30 條「只在 working tree、不在任何包」路徑的實測後果。

### 9.3 交付順序第三項：**214 檔分組**（176 未變＋148 旅程以外都在這裡，不另開平行摘要）

提示原話：「將其餘 176 個未變檔和 148 個旅程以外檔案按功能分組：每日估計、決策來源、基準／釘選、migration、前端 helper、其他。逐組寫明何時引入、依賴關係、是否為本次完整交付所需，及可否獨立保存。」先講**論域的對應關係**，否則兩輪的數字看起來像互相打臉：

- r13 說的「176 未變」＝**d-patch-round12→round13 那 183 檔中位元組相同的 176 支**（§〇 表 `:26`）。
- r13 說的「148 兩條旅程以外」＝**同一個 183 檔宇宙**按旅程歸屬切的（§8.8.5 第一表）。
- r14 的宇宙是 **214 條 porcelain／213 列舉**（§9.1.4）。**對應是「包含」不是「取代」**：183 ⊂ 213，本輪多了 30 條只在 working tree 的路徑（§一之補二 `:53`：7 支在 `backend/`\|`frontend/` 下，23 支在產品目錄之外——d-patch 產生器只對 `backend frontend` 取快照，所以那 23 支**永遠不會**進任何包，與本輪有沒有動它們無關）。
- **不要再用「0 未聲領」證明任何事**：那個桶的定義只是「檔名在紀錄裡出现过至少一次」（§8.8.5 第三節自己拆開了它）。本輪改用**可反駁的機產分組**代替它。

機產分組表（`REVIEW_SCOPE.md` §三，`:718`；規則＝路徑關鍵字，見 `build_r14_review_scope.py:54-76`，**一檔可落多組**）：

| 組 | 檔數 | r14 動過 | r13 動過（本輪未再動） | 在 536 閉包內 | 最新包沒有 | 有外側依賴＝**不可獨立保存** | 首見包分布 | **何時引入** | **是否本次完整交付所需** | **可否獨立保存** |
|---|---:|---:|---:|---:|---:|---:|---|---|---|---|
| 每日估計 | **16** | 0 | 3 | 16 | 0 | 9 | r10-final：8、r11：1、r11-full：7 | 第十輪 #187 那次（記錄首領 §8.4、`:3147`） | **是**：旅程一的全部承重檔都在這組 | ❌ 9 支有外側依賴；7 支可 |
| 決策來源 | **22** | 0 | 4 | 18 | 0 | 15 | r10-final：7、r11-full：14、r13：1 | 第十～十一輪（決策血緣主線） | **是**：旅程二承重 | ❌ 15 支有外側依賴 |
| **基準／釘選** | **17** | **4** | 0 | 8 | **3** | 4 | r11-full：14、**（未進任何包）：3** | **本輪**（conftest／run_tests.sh／兩支契約測試＝§9.1.2 那 4 支） | **是**，而且是本輪唯一有代碼變動的組 | ✅ 4 支本輪動過的都**可獨立保存**（閉包表：`run_tests.sh` 0/0、`test_lane_entry_contract.py` 0/0、`test_conftest_sqlite_default.py` 1/0）；⚠️ `conftest.py` 被 **121 支** import，改它＝動全樹，這是**依賴方向**不是保存問題 |
| migration | **7** | 0 | 0 | **0** | 0 | 0 | r10-final：3、r11-full：4 | 第十輪起（鏈尾 4 支見 §9.0 `expected_tail`） | **是**（A4 空庫→head 要用），但**本輪一位元組未動** | ✅ **全組可獨立保存**（閉包內 0＝它們不 import 任何髒檔）；要落地是**執行權**問題，不是依賴問題 |
| 前端 helper | **24** | 0 | 3 | 20 | 0 | 4 | r10-final：5、r11-full：18、r13：1 | 第十～十三輪 | **是**：`dailyEstimate.ts`／`dailyService.ts` 等 derive 層 | ❌ 4 支有外側依賴 |
| **其他** | **140** | 2 | 0 | 95 | **27** | **86** | r10-final：5、r11-full：108、（未進包）：27 | 多數＝第十一輪那次「158 支整包進件」（§〇 表 `:24`） | **不是全部**：見下面「待確認」 | ❌ 86 支有外側依賴；54 支可 |
| 加總自洽 | **226**＝213＋重疊 **13** 支（一檔落多組） | | | | | | | | | |

#### 9.3.1 「其他」140 支不可當一群審——按目錄再切（§三之補，`:731`；加總 140＝140 ✓）

`backend/tests` **59**（其中 **50** 有外側依賴）／repo 根 **14**／`backend/app/api` **13**（13 全有外側依賴）／`frontend/e2e` **12**／`backend/app/services` **9**（9）／`docs` **6**／`backend` **5**／`backend/app/models` **5**（5）／`backend/app/lib` **3**／`backend/app` **2**（2）／`.github/workflows` **1**／`engine` **1**（1）／`backend/scripts` **1**／`docs/first-use-usability` **1**／`docs/handoffs` **1**／`frontend` **1**／前端各 page **6**（`experiments/[id]`、`…/runs/[runId]`、`experiments/new`、`paper-portfolio/[id]`、`strategy-templates`、`training`，各 1 且各有 1 條外側依賴）。

#### 9.3.2 **待確認**群（提示：「若某組只因舊輪次寫入而存在，標示為待確認，不夾帶進任何保存範圍」）

按「是否只因為前面某輪寫了它，它才存在於待保存集合」逐群裁：

1. **`其他` 中的 `backend/app/**`（api 13＋services 9＋models 5＋lib 3＋app 2＋engine 1＝**33 支**）：產品碼 diff，但**本輪與 r13 都零改動**（表內 r14 動過＝0、r13 動過＝0）。⇒ **待確認**。它們是**第十～十一輪**累積的變更，若 owner 這輪只批那兩條旅程，這 33 支**不該跟著 D-apply 落地**，應另批一張「只套 11 支種子」的卡。
2. **`其他` 中的 `frontend/e2e/**`（12 支）**：spec 改動也**本輪未動**，而 §8.7.5.3 已實測**今輪兩條旅程 0 支 spec**（`daily-estimate`／`OrderPreviewPanel`／`pp-download-decision-history` 全 0 命中）。⇒ 這 12 支測的是**舊**旅程 ⇒ **待確認**，並與「瀏覽器欄 NOT RUN」一起讀。
3. **repo 根 14 支＋`docs` 6＋`docs/handoffs` 1＋`docs/first-use-usability` 1**：散文／紀錄。**不是交付**，也**不進任何保存範圍**，除非 owner 明批。
4. **`.github/workflows/secret-hygiene.yml`（1）**：task #168 的 CI 前提，本輪未動。⇒ **待確認**（它屬不屬這次交付，取決於 owner 要不要 CI，跟那兩條旅程無關）。
5. **0-byte 未追蹤目錄 `backend/postgres`（第十三輪揭露過，§8.8.4 ②）**：誤產物。**禁止**當作交付，本輪**不刪**（同一格也說明為何 §9.3 的 213 列舉裡看不到它——空目錄不進 porcelain 清單）。
6. **`基準／釘選` 組那 3 支「未進任何包」**：是 §9.1.2 本輪新增／改動的契約檔，**不屬於待確認**（它們正是本輪要審的東西）。

### 9.4 消除會污染證據的入口：`conftest.py` 的預設 `sqlite:///./test.db`（提示點名，本輪**已修並補直接測試**）

**修之前的事實（不是推測，是量過的）**：`backend/tests/conftest.py` 舊版在 `ALETHRA_TEST_DB` 未設時以 `sqlite:///./test.db` 落地，`./` 是**目前工作目錄** ⇒ 任何繞過 `scripts/run_tests.sh` 的 `pytest` 都往共享 checkout 裡那**同一個檔**寫。本輪量到的既成後果：`backend/test.db` **3,043,328 B**、mtime **2026-11-20T11:07:34+0800**（＝ 03:07:34Z，早於本輪）。

**最小修改**（`backend/tests/conftest.py:41-68`，＋40／−4／3 hunks）：新增 `_sqlite_default_test_db_url(env, pid)`——
- 呼叫者**已命名** DB（`ALETHRA_TEST_DB` 非空）⇒ **原值原樣返回**。註解自己寫明界線：「A database named by the caller is returned unchanged: this removes a silent default, it does not override a decision.」
- 未命名 ⇒ 改用 **runner 自己的慣例** `${TMPDIR:-/tmp}/alethra-sqlite-test-<pid>.db`（**每行程一檔**）。
- `TMPDIR` 缺失或不是絕對路徑 ⇒ 落回 `/tmp`，**不會**被相對 `TMPDIR` 拉回樹內。
- 結果同時寫進 `DATABASE_URL` 與**發佈** `ALETHRA_TEST_DB`（`:68` 那行有原因：`tests/stored_market_data`、`tests/fb17_dataset_provenance` 在 import 期各自以舊 `./test.db` 預設**重讀**這個名字——只設 `DATABASE_URL` 會放過那兩支）。

**直接測試**（提示：「只作最小修改並補直接測試，避免影響既有 runner 隔離」）：`backend/tests/test_conftest_sqlite_default.py`，**15 支節點**＝`TestResolver` ×12（含 `test_two_processes_do_not_share_one_file`、`test_relative_tmpdir_does_not_move_the_file_back_into_the_tree`）＋`TestConftestEnvironment` ×3（含 parametrize 過的 `test_fixture_modules_that_re_read_the_name_do_not_get_the_worktree[tests.test_fb17_dataset_provenance|tests.test_stored_market_data]`）。

**兩道端到端正控（不只單元層，這才是「入口被堵住了」的證據）**：本輪**故意繞過 runner**、裸跑 `pytest` 兩次（含會寫 DB 的測試），然後量那個共享檔：

| 正控 | 命令（物件） | `backend/test.db` 讀回 | TMPDIR 側新檔 | 判讀 |
|---|---|---|---|---|
| ① | `cd backend && .venv/bin/python -m pytest tests/test_conftest_sqlite_default.py` | **3,043,328 B／mtime 11:07:34+0800 未變** | 出現 `alethra-sqlite-test-<pid>.db` | 預設已離開樹內 |
| ② | 同上，跑一個**會真的建表**的舊檔（`tests/test_stored_market_data.py`） | 同一檔**仍未變**（尺寸與 mtime 逐字相同） | 又一檔 `<pid>.db` | 「有 DB 寫入的裸跑」也不再污染共享檔——**這條才是入口被封死的證明** |
| 15:3x 現測 | — | 3,043,328 B／2026-11-20T11:07:34+0800（**本輪結案讀回同值**） | `${TMPDIR}alethra-sqlite-test-*.db` ＝ **52 支** ＋ `alethra-runs/` 下 **26 個 run dir** | 副作用全部落在 OS 回收區，**未刪**（§9.9.2） |

**保留不动**：提示說「保留已寫的 `test.db`，不作證據，不刪除」⇒ 本輪**未動、未刪、未截斷**，它仍在樹內（`??`，也因此是 214 條之一）。

### 9.5 runner 的參數處理與「明確的可用旗標」（提示：「在 runner 說明及參數處理中使用明確的可用旗標」）

#### 9.5.1 本輪量到的第二個污染入口：**收集器的輸出格式会被 verbosity 破壞**

`-v` 不是「多印一點」而已。`--collect-only -v` 印的是**樹**（`<Function test_x>`），於是**0 個 node id 進得了 `collect.txt`**，之後每一筆 JUnit 紀錄都落到「record without id」。實測後果（run dir `sqlite-20261120T070014Z-79849`）：一次 **59 支全過**的 run 被證據閘拒絕。

⚠️ **一處必須更正的轉述（我自己先前在終端裡看到、差點寫錯的東西）**：那次 run 的 `exit-code.txt` 內容是 **`0`**，不是 `1`。原因在碼裡：`run_tests.sh:480` 在**任何閘之前**就把 **pytest 自己的 status** 寫進 `exit-code.txt`（並在 `:497` 自己宣告這件事），證據閘（`:515-533`）只能在**程序**退出碼上把它改成非零：

```
not green: pytest passed but the evidence gates refused (skip census $BRC, ledger $ERC).
```
⇒ **「lane rc」≠「`exit-code.txt`」是這條路徑的固定語意**（`exit-code.txt` 記的是 pytest，lane 記的是「pytest ∩ 證據閘」）。任何拿 `exit-code.txt` 宣稱「綠」的讀法都必須同時出示 `$BRC`／`$ERC`。判別式在 `:530`（`[ "$RC" -eq 0 ] && [ "$GATE" -ne 0 ]`），逐字宣告這行語意的 `echo` 在 `:497`／拒絕文字在 `:531`。

#### 9.5.1b **同一條命令**在修法之後（前後對照）

上表那兩次探測的命令**逐字相同**——兩份 `identity.txt` 的 `command:` 行都寫 `scripts/run_tests.sh sqlite -v tests/test_config.py`——所以這是一對能直接對照的前後測，不是兩條不同路徑。

| | 修法**前** | 修法**後** |
|---|---|---|
| run dir | `sqlite-20261120T070014Z-79849`（15:00） | `sqlite-20261120T070908Z-84939`（15:09） |
| `collected-node-ids` | **`0`** | **`59`** |
| `collect.txt` 的 `<Function` 行數 | **59** | **0** |
| `selection on this tree` | `========================= 59 tests collected in 0.03s =========================`（被 box 包住） | `59 tests collected in 0.04s` |
| `collector-verbosity-normalised` | **無此行** | `collector-verbosity-normalised: -v (dropped for the --collect-only step only; the run itself keeps them)` |
| `exit-code.txt`／`pytest.log` 末行 | `0`／`59 passed, 94 warnings in 10.46s` | `0`／`59 passed, 94 warnings in 10.53s` |

修法後 `identity.txt` 的相關四行逐字（本輪 15:2x 由檔案重讀，非轉抄）：

```
selection-kind: explicit-selection
collector-verbosity-normalised: -v (dropped for the --collect-only step only; the run itself keeps them)
tree-digest-before: 162d0be4766e345268d6df90447b41f69d9e6a69d04f3f943342db483ffa7c7d
collected-node-ids: 59
```

⚠️ **這兩份 run 之間不只 verbosity 一個差異**，先列出來，免得把上表讀成受控實驗：
1. `tree-digest-before` 從 `b18b08657f53001673b7d0576d0e98238db791c1a1705f3dcd7c396230612930` 變成 §9.0 的完整候選 `162d0be4…`，因為這段期間新增了 `tests/test_conftest_sqlite_default.py`（526→527 支 `.py`）。**這個差異就是被測的改動本身**，不是干擾因子。
2. `provider:` 行前者 `DATA_PROVIDER=<unset>`、後者 `DATA_PROVIDER=demo`（15:00 那次探測我沒帶 provider）。兩次都是 `selection-kind: explicit-selection`、都不進整套額度閘（§9.0.1），故不影響本節結論；但 provider 會影響那 59 支 `tests/test_config.py` 的斷言環境，所以上表**只宣稱「node id 是否進得了 `collect.txt`」這一件可比**，不宣稱兩次通過集合可比。

⇒ 結論押在這一行對照上：**`collected-node-ids: 0` → `59`，而 `exit-code.txt` 前後都是 `0`**。這是 §9.5.1 末段警告的具體例子：同一條 `-v`，修法前 pytest 全過、證據全丟，`exit-code.txt` 看不出任何事。

#### 9.5.2 修法：把 verbosity **正規化**而不是繼承（`run_tests.sh:381-419`）

收集器改用一組**過濾過的參數**：`--quiet`／`--verbose`／任何短旗標裡含 `v` 或 `q`（`-[!-]*` ＋ `case "${arg#-}" in *[vq]*)`）被**剔除**並記錄，其餘原樣傳下去；`COLLECT_ARGS` 為空時走無參收集。被剔除的東西寫進 `emit_identity()` 的一行 `collector-verbosity-normalised: $VERBOSITY_DROPPED`——**恆以 `-q` 收集**，所以 `collect.txt` 的格式不再取決於呼叫者。

#### 9.5.3 九種呼叫形狀的實測契約（本輪把它寫成會拒絕的節點，不是散文）

`tests/test_lane_entry_contract.py` 現樹 **42 支節點**，其中本輪 ＋17／−6 的那段是 parametrize 過 **9 種 verbosity 形狀**：`[]`、`-q`、`-qq`、`-q -q`、`-v`、`-vv`、`-vs`、`--verbose`、`--quiet`。每一格都斷言**四件事**：`collect.txt` 有 `collected-node-ids: 2`、有 `"2 tests collected"`、**沒有** `"<Function test_passes>"`，以及 `collector-verbosity-normalised:` 該出現時出現（凡帶 v／q 者）／不該出現時不出現（`[]` 與 `-q -q` 之外的無旗標格）。

#### 9.5.4 `--help` 現在把「**沒有**哪些旗標」寫死（提示要的「明確的可用旗標」）

現測 `./scripts/run_tests.sh --help` ＝ **72 行／4,589 B**（檔案本體 538 行／23,934 B）。說明文字列出完整可解析集合，並逐字包含：**「There is no --digest-only, no --tree-digest and no `collect` sub-command」**。這句是用來堵**我自己踩過**的那條誤路——本輪實測跑过一次：

| 探測（15:2x 現測） | rc | 逐字輸出（節錄） | run dir |
|---|---|---|---|
| `./scripts/run_tests.sh sqlite --digest-only tests/test_target_guard.py` | **2** | `refusing: no test was selected`（`identity.txt` 記 `collected-node-ids: 0`、`selection on this tree: <the collector printed no selection line>`） | `sqlite-20261120T072037Z-90507`（**只留 collect.txt＋identity.txt**，因為它在選擇階段就停） |
| `./scripts/run_tests.sh pg …`（`ALETHRA_PG_TEST_URL` 空） | **2** | `refusing: pg layer needs ALETHRA_PG_TEST_URL (a disposable PostgreSQL database)`（`run_tests.sh:171-172`） | — |
| DSN 有、DB 名不在白名單／port 不對 | **3** | `test_safety_gate.py` 的拒絕（lane 於 `run_tests.sh:190` 匯入該模組） | — |
| 整套 sqlite，**未**動釘選 | lane rc **2** | §9.0.1 那段 `refused: this whole-suite run collected 3862 tests but the budget pins 3811…` | `sqlite-20261120T070034Z-80037`（本輪 SIGTERM 於 13%，§9.9.2） |
| `skip_budget.py` 的**參數位置**（本輪也踩錯一次，如實記） | **2** → 正確用法 **2**（拒絕） | 錯：`skip_budget.py log check …` ⇒ `error: argument cmd: invalid choice: '…' (choose from write-budget, check)`；對：`skip_budget.py <log> check --budget … --identity …`（位置參數是 log） | — |

### 9.6 基準與釘選：**四段分離**、本輪**未重釘**、以及唯一採用宣告

提示原話：「繼續區分 fixed base、現有未批准釘選、歷史候選與本輪候選。不得以 3,811、3,837、3,839 或 digest 追認已批准基準。」

| 段 | 值 | 它是**誰**选的、能不能拿来追認「已批准」 |
|---|---|---|
| **fixed base** | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（HEAD） | Git 事實。**唯一**由 owner 親手钉下的錨（分支 `live1-impl` 上的既有提交）——本節所有「＋／− vs HEAD」都用它 |
| **現有未批准釘選** | `backend/tests/collected_nodes.txt` **3,811** 列／`skip_budget.json` `collected=3811, total=179, pg_only=159, live_stack=18, other=2`；其 `provenance` 自記 run dir `sqlite-20261119T051257Z-80770`、`tree-digest 50130606…`、porcelain 206／`da75ac96…`、`exit_code "1"` | ⚠️ **兩件不可迴避的事**：①它的 `exit_code` 是 **"1"**——釘選的來源是**一次失敗的 run**；②它的樹（206 檔／`50130606…`）**既不是 r13 候選也不是本輪候選**。⇒ 它是**現存的控制檔**，不是**已批准的基準**。本節不以任何一個 digest 宣稱批准 |
| **歷史候選** | r13＝`75d2cfdd…`／526 支 `.py`；r12＝`bd5b844b…`（§8.7.0 #1）；更早的 3,837／3,839 段（§十二輪 §一：A=3548／P=3811／C=3837） | 都是**候選**。**3,837、3,839、3,811 之間的任何差都不構成批准**——三個數各自綁不同樹，且本輪證明 3,811 綁的樹連 `test_conftest_sqlite_default.py` 都還沒有 |
| **本輪候選** | **`162d0be4766e345268d6df90447b41f69d9e6a69d04f3f943342db483ffa7c7d`**／527 支 `.py`／收集 **3,867**／porcelain 214／`22e305b7…` | **也只是候選**。它比釘選多 60、少 4 支 id（§9.0.1），因此**它自己無法通過整套閘**。要讓它成為基準，必須**重釘**——而重釘未獲批准，本輪**未做**（§9.6.4） |

#### 9.6.1 批准原件與缺口（提示：「整理能找到的批准原件與缺口，列出各選集差異，不做新的重釘」）

找得到的**原件只有三個物件**，其餘都是紀錄轉述：① HEAD `5a34e3f`（Git 可獨立驗證）；② `backend/tests/collected_nodes.txt` ＋ `skip_budget.json`（倉庫內的**受控檔**，但如上所述其 provenance 是一次 exit 1）；③ `alethra-evidence/baseline-round13/BASE_VALUES.md`（§9.6.3）。**缺口**：**沒有任何一份 owner 簽署的「此為批准基準」原件**——紀錄裡出現過的每個「批准」都是**流程性批准**（例如批准某張卡執行），不是批准某個 node 選集。這個缺口**不因本節寫得更多而縮小**，只能由 owner 的一張卡補上（§9.12 D 卡 3）。

#### 9.6.2 各選集的**逐檔差**（不做重釘的前提下能量的一段）

| 比較 | 可量測性 | 實測 |
|---|---|---|
| 釘選（3,811）↔ 本輪（3,867） | ✅ 可，兩側都是實物清單 | **−4／＋60**，逐檔見 §9.0.1 |
| r13 收集（3,847，§8.7.1）↔ 本輪（3,867） | ⚠️ 部分——r13 的 3,847 只有**計數**留在紀錄，**該 run 的 node id 清單不在本節讀過的實物裡** | 計數差 **＋20**，**逐檔差無法從現有實物重建** ⇒ 老實標 **UNKNOWN**，不猜是哪 20 支 |
| 3,548（r12 的 A 段）↔ 3,811 | ⚠️ 同上，且跨了一次 allow-list 收緊 | **UNKNOWN**（第十二輪 §一自己寫過「三段差值只有一段可量測」，本輪不推翻那句） |

#### 9.6.3 **唯一採用者**（提示：「舊 `base-ledger-round13`、`baseline-round13` 並存時，在 manifest 明示唯一採用者，其他目錄只標廢棄，不刪除」）

兩個目錄**現在都在**（本輪 `ls` 實測）：

| 目錄 | mtime | 內容（大小／sha256 前 8） | 本輪裁定 |
|---|---|---|---|
| **`alethra-evidence/baseline-round13/`** | Nov 20 **11:14** | `BASE_VALUES.md` 7,680 B／`14dd18f7…`；`BASE_VALUES.json` 21,336 B／`b6abda56…`；`SELECTION_DIFF_Pinned_to_Candidate.txt` 4,843 B／`f709ff9f…`；`README.txt` 823 B／`7fa771ec…`；`SHA256SUMS.txt` 346 B | ✅ **唯一採用者** |
| `alethra-evidence/base-ledger-round13/` | Nov 20 **10:25** | `BASE_VALUES.md` 5,302 B／`80001bf1…`；`BASE_VALUES.json` 11,297 B／`6c3e823e…`；`SHA256SUMS.txt` 164 B／`f57b95b9…` | ⚠️ **廢棄（DEPRECATED）**，理由：它**只有**帳本視角，**沒有** §9.6.2 需要的那份 `SELECTION_DIFF_Pinned_to_Candidate.txt`（釘選↔候選的逐節點差）。它較早（10:25 vs 11:14）且是同一問題的**未完成版本** |

**探棄的具體動作＝零**：**不刪、不改、不搬**任何一檔，也不往任一（已凍結的）目錄里新增檔；本節文字是**唯一**的探棄紀錄載體（往凍結目錄補檔會破壞它自己的 `SHA256SUMS.txt`，那是更壞的選擇）。要獨立核對採用的那份：`cd alethra-evidence/baseline-round13 && shasum -a 256 -c SHA256SUMS.txt`，它綁的候選 digest 是 `75d2cfdd…`／526 支 ⇒ **那是 r13 的基準檔，不是 r14 的**（r14 候選見 §9.0，**尚無基準檔**，因為建它需要重釘）。

#### 9.6.4 本輪**未重釘**的證據（不是宣稱，是可查的）

兩支受控檔在本輪之後**仍與 r13 記錄同值**：`skip_budget.json` 讀回 `collected=3811 total=179 pg_only=159 live_stack=18 other=2`（§9.0 現測）＋ `collected_nodes.txt` 仍 3,811 列（§9.0.1 現測）；兩者**未出現在 §9.1.2 的寫入清單**，也未出現在 `REVIEW_SCOPE.md` §一的 11 支「本輪動過」表裡（該表由 sha 比較機產，若我動過它們，它們必然入表）。

### 9.7 交付順序第四項：**仍未驗證的旅程**（六層分開，不互換）

提示原話：「旅程證據分層維持：後端、前端 unit、已執行直接回歸、PG、cron、瀏覽器。工作區面板的血緣接通、舊 payload 相容與錯誤呈現若仍未完成，就先完成，不能以紀錄或分組代替。」

| 層 | 本輪實測狀態 | 缺什麼（要什麼才算補齊） |
|---|---|---|
| **① 後端（離線讀碼）** | ✅ 本節即證據（§9.1／§9.4／§9.5 全部行號本輪重讀） | — |
| **④-sqlite（測試層）** | ✅ **本輪重跑 115 支前端 unit 全過**：`dailyEstimate` **43**／`dailyService` **10**／`dataManifest` **23**／`runCancellation` **11**／`runRetry` **17**／`runTradeExport` **11**，命令 `cd frontend && ~/.local/bin/node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/<f>.test.mjs`（15:2x 現測，六行 `# fail 0`）。後端側新增 57 支節點（15＋42，§9.1.2）在树可收集 | ⚠️ **不得拿 115 當「旅程完成」**：unit 證的是 derive 層的話術與欄位相容，不證後端寫了什麼 |
| **已執行直接回歸** | ✅ 本輪**未跑整套**（理由＝§9.0.1：未重釘時整套必然被閘拒絕）。已跑的是：`test_conftest_sqlite_default.py`（15）、`test_lane_entry_contract.py`（42）、上述六支 `.mjs`（115）、以及 §9.4 兩道裸跑正控 | 補齊需要：**一張批准的重釘卡**（§9.12 D 卡 3）或**一張「只跑明選 node id、不碰整套閘」的卡**（§9.9 A-lane） |
| **PostgreSQL** | ❌ **NOT RUN**（本輪零 PG：無 `initdb`、無 `pg_ctl`、無 5432 聆聽、`/tmp/.s.PGSQL.*` 不存在——15:2x 現測） | A1–A4＋A6／A7 全部待批（§9.9）。A6／A7 那 6 支節點在 sqlite 層會以 `row-lock serialisation requires a real PostgreSQL backend` 跳過 ⇒ **跳過不是通過** |
| **自然 cron** | ❌ **NOT RUN**（無服務進程） | B 卡（§9.10）。本輪只重讀了開關本身：`app/config.py:140 enable_learning_scheduler=False`、`:148 learning_scheduler_jobs=""`、`:153 learning_scheduler_max_owners=25`、`:156 auto_collection_enabled=False`、`app/main.py:52` 的閘、`app/workers/learning_scheduler.py:80 SERVICE_TIMEZONE="UTC"`、`:152/:162/:169-170/:183-184` 四把 `CronTrigger`、`:318 is_kill_switch_active(db, kill_switch_scope)`、`app/services/daily_estimates.py:82 DAILY_ESTIMATE_HOUR_UTC=5` |
| **瀏覽器** | ❌ **NOT RUN**，且**尚無 spec**（§8.7.5.3：`daily-estimate`／`OrderPreviewPanel`／`pp-download-decision-history` 在 `frontend/e2e/*.spec.ts` 全 0 命中） | C 卡（§9.11）**加**一件事：新寫 spec（代碼變更，另批）**或** owner 本人走查。另外本輪再次實測：**`command -v docker` ⇒ not found**，`start_acceptance_env.sh:34-35` 會在碰任何行程之前自己退出（r13 已跑過一次那條拒路，§8.7.5.0） |

**提示點名的那三件事，逐件交**（不以紀錄代替）：
1. **工作區面板的血緣接通** — **已完成於 r13、本輪重新讀證**：`OrderPreviewPanel.tsx` ＋153／−2（13 hunks，§9.1 #6），行為由 `dailyEstimate.test.mjs:670`（勾了才送）與 `:708`（兩個門一句話）固定。
2. **舊 payload 相容** — **已完成**：`:191`「the card reads only fields the backend emits, and marks the one-sided ones optional」＋ `:745`「an unrecognised source state narrows to undefined, not to "none"」；本輪 43／43 過。
3. **錯誤呈現** — **已完成到 unit 層，未做到瀏覽器層**：`:1034`（`failureNotice` 只點名給它的 items）、`:1098`（面板把**已保存的失敗**渲染成可定位）、`:1114`（該欄由服務寫、在 door 翻譯）。⇒ **代碼與 unit 不再是用欠項**，欠的是**畫面那一層**（無 spec、無本輪瀏覽器批准）。**本輪沒有為了湊這格而動任何代碼**——這句本身就是交付的一部分。

### 9.8 A0 正文（只讀、**零新寫入**）：命令、預計輸出、停線

提示原話：「A0：先交只讀、無新寫入的命令與 identity 檢查，預計輸出和停線。」**A0 的定義就是「不产生新檔」**：以下 9 條**全部只寫 stdout**；本輪已逐條跑過（15:2x–15:3x），下面每條的「預計輸出」就是**本輪逐字讀到的值**。

| # | 命令（可在 `cd <repo>` 或標注目錄後直接貼） | 預計輸出（逐字，本輪實測） | 停線（命中即**回報後停止**，不覆寫、不刪除） |
|---|---|---|---|
| A0-1 | `git rev-parse HEAD` | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` | 不同 ⇒ 本節所有數字作廢，**NOT RUN**，重測身份 |
| A0-2 | `git status --porcelain \| tee /dev/null \| wc -l && git status --porcelain \| shasum -a 256` | `214` ＋ `22e305b7cd7e5c129a03aa8fc13786363f5061813c0b3309c6a72cacd9e22f41` | 任一不符 ⇒ 樹在我寫完之後被動過（**平行 session 共用此 checkout**），**停**，不執行任何後續卡 |
| A0-3 | `cd backend && find . -name '\*.py' -type f -not -path './.venv/\*' -not -path './venv/\*' -not -path '*/__pycache__/*' -not -path './node_modules/*' \| LC_ALL=C sort \| wc -l`（＋同式接 `xargs shasum -a 256 \| shasum -a 256`） | `527` ＋ `162d0be4766e345268d6df90447b41f69d9e6a69d04f3f943342db483ffa7c7d` | 與 r13 的 `75d2cfdd…`／526 相同 ⇒ 你讀的是**舊樹**，本節 A 卡不適用 |
| A0-4 | `cd backend && .venv/bin/python scripts/check_migration_tail.py --json --with-alembic; echo rc=$?` | `status=OK`、`head=d4e5f6g7h8i9`、`parsed_heads=["d4e5f6g7h8i9"]`、`revision_count=57`、`unreachable_from_head=[]`、`rc=0` | `status≠OK`／`parsed_heads` 多於一／`alembic_head≠parsed_head` ⇒ **停並回報**，不修鏈 |
| A0-5 | `pgrep -fl postgres; pgrep -fl uvicorn; pgrep -fl "next dev\|next-server"` | **三條皆無輸出** | 有任何 pid ⇒ **停**：已有實例在跑，不得假設它是我的，**不 kill**（記憶：未知進程先查所有權） |
| A0-6 | `for p in 5432 5433 8000 8101 3000 3101; do lsof -nP -iTCP:$p -sTCP:LISTEN \| tail -n +2 \| wc -l; done` | 六行全 `0` | 任一非 0 ⇒ 換埠要 owner 另批，**不搶埠** |
| A0-7 | `ls -d /tmp/.s.PGSQL.* "$TMPDIR/alethra-r14-pgdata" 2>&1` | `no matches found: /tmp/.s.PGSQL.*`／`No such file or directory`（兩條都**不存在**） | 存在 ⇒ **停**，**不覆寫、不刪除**既有 PGDATA／socket（可能是別的 session 的） |
| A0-8 | `PGB=$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin; ls -1 "$PGB" \| wc -l; "$PGB/postgres" --version` | `32` ＋ `postgres (PostgreSQL) 16.2` | 非 16.2 或目錄缺失 ⇒ B／A 的 PG 前提不成立，**回報**，不另找二進位 |
| A0-9 | `stat -f "%z bytes mtime=%Sm %N" -t "%Y-%m-%dT%H:%M:%S%z" backend/test.db; grep -cE '^[A-Z_]+=' backend/.env; git check-ignore -v backend/.env` | `3043328 bytes mtime=2026-11-20T11:07:34+0800 backend/test.db`／`13`／`.gitignore:14:.env	backend/.env` | `test.db` 尺寸或 mtime **變了** ⇒ §9.4 的修復**已被繞過或被還原**，立即回報；`.env` 鍵數≠13 或 mtime 離開 2026-11-06 ⇒ 環境被人動過 |

**是否新寫證據檔：不會。** 上表 9 條無一條重定向到樹內或 `$TMPDIR`（A0-3 的 `tee /dev/null` 刻意不落檔）。**要 `$RUN` 檔時必須另批 A0b**：先 `RUN=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r14-run-XXXXXX")` 再存 `identity.txt`——那是**新增 1 支檔**，不在 A0 的零寫入定義內（與 r13 §8.7.2 同一界線，本輪不模糊它）。**A0 本輪已跑**（其結果即上表），所以 A0 **不需要批准即可重複做**；需要批准的是 A1 起的一切寫入。

### 9.9 A1–A7 正文（**本輪新候選**；命令、目錄、角色、資源上限、停止條件、node IDs——**未沿用 r13 草稿**）

| 格 | 本輪可執行值（與 r13 的差異全部來自 §9.0 的現測） | 停止條件（不得放寬） |
|---|---|---|
| **A1** | `"$PGB/initdb" -D "$PGDATA" --auth-local=trust --auth-host=scram-sha-256 -E UTF8 --no-sync`；`PGDATA=${TMPDIR:-/tmp}/alethra-r14-pgdata`、`SOCKDIR=${TMPDIR:-/tmp}/alethra-r14-sock`（**兩路徑本輪實測不存在**，A0-7）；角色 `alethra_r14_bootstrap`／`alethra_r14_migrator`／`alethra_r14_app`。**只建叢集**，不建 DB、不建表 | 非零 ⇒ 停，**不重試、不刪 `PGDATA`**；`PGDATA` 已存在 ⇒ 停（A0-7） |
| **A2** | `"$PGB/pg_ctl" -D "$PGDATA" -l "$RUN/pg.log" -o "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB" start` ⇒ `pg_isready -h 127.0.0.1 -p 5432`；收：`pg_ctl -m fast stop` ＋ `pgrep`／`lsof` 讀回已退出 | ready 不成功 ⇒ 停並留 `pg.log`；**不以已停端點的 HTTP 回應證明停止**；**停止 ≠ 刪資料** |
| **A3** | DB **`alethra_owner_test`**（**不可**改 r14 前綴：白名單查的是**DB 名**，`backend/test_safety_gate.py:25-30`，`alethra_owner_test` 在 `:29`；實例白名單 `:41-45` 僅 `(localhost\|127.0.0.1\|::1, 5432)`；`run_tests.sh:178` 另要求 `ALETHRA_TEST_REQUIRED_DB_PREFIX=alethra_`）。角色名用 **`alethra_r14_migrator`／`alethra_r14_app`**（不受白名單約束）；`REVOKE ALL ON SCHEMA public FROM PUBLIC`（PG15＋ 上 alembic 能落地的**必要**授權） | 讀回 `pg_roles` 四旗標（`test_safety_gate.py:110`）须全 `f`；`datdba=alethra_r14_migrator`；**不放寬安全閘**（換 DB 名／port 屬政策裁決）；閘拒絕 ⇒ lane **rc=3** |
| **A4** | 空庫→head **全鏈**，head `d4e5f6g7h8i9`、`revision_count=57`、`expected_tail` 四支依序（A0-4 現測） | `heads` 多於一／head 不符／`DUPLICATE_REVISION`｜`BRANCHED_TREE`｜`MISSING_DEPENDENCY` ⇒ 停；**不 autogen、不 downgrade、不 drop、不跑任何其他 migration** |
| **A5** | 兩欄**只有 index、無 FK**：`migrations/versions/c3d4e5f6g7h8_add_decision_inference_link.py` 欄位 `:88`、`op.create_index` `:90-93`（前置條件 `raise RuntimeError` 在 `:68/:74/:80`）；`d4e5f6g7h8i9_*.py` 常數 `INDEX` `:47`、`op.create_index(INDEX,'human_decision_events',['linked_inference_id'])` `:89`；完整性在寫入前由 `app/services/decision_lineage.py:19 validate_linked_inference()` 核對 | 出現 FK／型別非 `uuid` ⇒ 停並回報（本體與 migration 不同步）；差異逐條列，**不解釋成等價** |
| **A6** | 節點 ID（下方 6 支的前 4 支）；`_TIMEOUT_S = 60`（`tests/test_daily_estimate_two_workers.py:170`）、三段 timeout `SET` 在 `:241-243`、`mp.get_context("spawn")`＋`ctx.Barrier(2)` 在 `:269-270` 與 `:650-651` | 帳本 **row count** 為準：每 (owner, skill, instrument, window) 恰 1 列 `metadata["via"]=="daily_service"`（常量 `app/services/daily_estimates.py:77`）、每列恰 1 筆 `settled` 預留、非終態預留＝0、落敗那支 `learning_jobs.result_json.refused` 以 instrument id 具名 `already_estimated_this_window`。**兩個 job 摘要的 `billed_in_window` 不相加**。計時／Barrier 未湊齊／斷言失敗 ⇒ **NOT RUN／INVALID**，不得寫成 PG 競態 PASS。**額度前提：測試帳號 `ai_usage_limit ≥ 4`**（free plan 預設 10，`app/auth/entitlements.py:70`；有 `PilotEntitlement` 列時讀該欄、模型預設 20 `app/models/pilot.py:97`）⇒ **B 卡的 1 額度不得套在 A6／A7**，否則 429（`reserve_quota` `entitlements.py:252`）會把真缺陷蓋成限流 |
| **A7** | 下方後 2 支（`TestConcurrentLifecycle` 的 idempotency／stale-read 兩支） | 仍以 `row-lock serialisation requires a real PostgreSQL backend` 跳過 ⇒ DSN 未生效，判 **NOT RUN** |

#### 9.9.1 A6／A7 的**逐支 node id**（本輪逐一在樹上重新驗證：6 支全部出現在 `sqlite-20261120T070931Z-85151/collect.txt`）

```
tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier
tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once
tests/test_daily_model_estimate.py::test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_two_requests_on_one_idempotency_key_leave_exactly_one_order
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_stale_account_read_cannot_overwrite_a_committed_booking
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_manual_submit_racing_an_advance_stays_consistent
```
執行（各自獨立，**不碰整套閘**）：`cd backend && export ALETHRA_PG_TEST_URL="<A3 產出的 DSN>" TEST_MODE=true DATA_PROVIDER=demo && ./scripts/run_tests.sh pg <該格 node id…>`。**注意**：`run_tests.sh` **沒有** `--digest-only`、`--tree-digest` 或 `collect` 子命令（§9.5.4），帶了會以 `refusing: no test was selected`／rc=2 停下——這是本輪實測，不是推測。

#### 9.9.2 本節之前的**本輪寫入清單**（提示：「保留第十三輪 7 檔變動歸屬、寫入清單、副作用揭露與零執行核對」——本輪同格式）

| # | 位置 | 寫入 | 可逆性 |
|---|---|---|---|
| 1–4 | `backend/tests/conftest.py`、`backend/scripts/run_tests.sh`、`backend/tests/test_conftest_sqlite_default.py`（新）、`backend/tests/test_lane_entry_contract.py` | 見 §9.1.2（＋40／−4、＋391／−6、＋100／−0、＋17／−6） | 前三者 `git checkout -- <path>` 可回到 HEAD（`test_lane_entry_contract.py` 與新檔是 `??`，回退＝移出樹，**本輪不做**） |
| 5 | `docs/minimal-ci-spec.md` | ＋3／−3 | 同上，tracked |
| 6 | `EXECUTION_TABLE_20261117.md`（本檔） | 附加 §九（附加前 3,793 行／`1401110d…`） | 僅附加；未改寫前面任何一節 |
| 7 | `alethra-evidence/r14-review-scope/` | 新目錄＋**19 支檔案**（`SHA256SUMS.txt` 列 18 條，§9.1.3） | 未追蹤、非交付（§9.1.4） |
| — | `$TMPDIR` | **26 個 run dir**（`alethra-runs/sqlite-20261120T07*Z-*`）＋ **52 支 `alethra-sqlite-test-*.db`**（含 `…-80037.db-journal`） | **全部保留未刪**（提示：保留錯誤紀錄，不拿清理當作證據已消失） |

**⚠️ 一項必須自己講的偏離（不是純唯讀動作，也不是計畫內）**：本輪**啟動過一次整套 sqlite lane** 又在 13% 处以 SIGTERM 中斷——run dir `sqlite-20261120T070034Z-80037`（該 dir 的 `exit-code.txt` 由 `run_tests.sh:480` 寫入，中斷 run 記的是 **143**），`pytest.log` 停在 13%，**證據閘從未被執行到**（閘在 `:515` 之後）。後果：它**不產生任何 verdict**（既不是 PASS 也不是 FAIL），但**它產生了一份 3,862 的 partial 計數**，本節拿它只為重現 §9.0.1 那句拒絕原文。**未計畫的執行＝這一條，僅此一條。** 另外 5 條「非純唯讀」動作逐條對應：A0-2／A0-3（唯讀）、兩次裸跑 pytest（寫 `$TMPDIR`，正控①②）、`--help`／unknown-flag 探測（寫一個只含 collect/identity 的 run dir 90507）、`skip_budget.py check` 重現（**只讀 run dir 內既有 log**；注意 `get_active_policy()` 那一類「讀取即寫入」的副作用在本輪未觸碰，因為這些命令根本不連 DB）、產生 `r14-review-scope/`（寫證據樹）。

**零執行核對（提示要求如實紀錄未執行項）**：**未**啟動 PG／任何服務／瀏覽器／任何外呼；**未**重釘 `collected_nodes.txt`、`skip_budget.json`；**未** `git apply`／`add`／`commit`／`push`；**未**刪除任何檔（含 `backend/test.db`、`backend/postgres` 空目錄、兩個 r13 基準目錄、52 支 TMPDIR DB）；**未**跑 312 支 e2e；**未**跑 3,867 整套到結束；**未**修改 `alethra-evidence/tools/build_r14_review_scope.py`（本輪對它**零寫入**——順帶更正一件前一輪摘要裡流傳的說法：那段聲稱我要去改的「`# 錨點：11 支`」註解**在檔內不存在**（`grep` 0 命中），所以那次編輯**沒有發生**，也沒有幽靈寫入進本節）。

### 9.10 B 卡正文（自然 cron 的隔離棧；**不給 SQLite／PG 二選一**，本輪固定一個）

提示原話：「B：固定一個隔離 DB、非秘密有效設定、單一排程 lane、額度和停止方式；不印完整環境，不接受 SQLite／PG 二選一。」⇒ **本卡只有一種 DB：PostgreSQL 實例（A1–A4 產出）**，SQLite 路線**本輪明確排除**（理由不是不能連，是它的證據層級只能到「⑤-sqlite 服務程序」，**兌現不了 cron 那一欄**；把選擇丟回 owner 才是违犯本句）。

| 格 | 固定值 |
|---|---|
| **隔離 DB** | `alethra_owner_test`（A3）於 `127.0.0.1:5432`、`PGDATA=$TMPDIR/alethra-r14-pgdata`、`SOCKDIR=$TMPDIR/alethra-r14-sock`；DSN 由 A2 之後在子 shell 内組出並 export，**永不印出**（形式：`postgresql://alethra_r14_migrator:<openssl rand -hex 12>@127.0.0.1:5432/alethra_owner_test`） |
| **單一排程 lane** | `ENABLE_LEARNING_SCHEDULER=true`（`app/config.py:140` 預設 False；`app/main.py:52` 是唯一讀它的閘）**＋** `LEARNING_SCHEDULER_JOBS=daily_model_estimate`（`config.py:148` 預設空＝取 `DEFAULT_LEARNING_SCHEDULER_LANES`＝`("daily_research","daily_simulation","weekly_analysis")`，`config.py:55-59`，**刻意不含計費 lane**）。只名一支 ⇒ `registered_lane_ids()`（`learning_scheduler.py:191-204`）只會 `add_job` 那**一支**；啟動日誌逐字会是 `Learning scheduler created (UTC): daily_model_estimate@…`（`:222-226`）。**不得**用空值（那會一次帶進 3 支别的 lane） |
| **觸發時機** | `05:00 UTC`（`app/services/daily_estimates.py:82 DAILY_ESTIMATE_HOUR_UTC=5` ＋ `learning_scheduler.py:183-184 CronTrigger(hour=…, minute=0, timezone=SERVICE_TIMEZONE)`；`:79-80` 註解自己說明為何**必須**顯式 UTC）。**不改時鐘**、不用手動入口冒充 cron |
| **額度（本卡的核心判準）** | 服務帳號 `ai_usage_limit`＝**1**（`app/auth/entitlements.py:70` 為 free plan 預設 10，所以必須**主動設成 1** 才有判別力）。**预期**：第一支 instrument 扣額成功，第二支撞 429（`reserve_quota` `:252`）⇒ 該次 tick 必頇落一筆**具名拒因**而**不是**静默少寫。**邊界**：A6／A7 用 ≥4，本卡用 1，**兩者不得共用一個帳號** |
| **上限** | `LEARNING_SCHEDULER_MAX_OWNERS` 維持預設 **25**（`config.py:153`，validator `:311` 拒 <1）；本次**只種 1 個 owner／1 支 skill／2 支 instrument**（`MAX_SKILL_UNITS_PER_OWNER=10`、`MAX_INSTRUMENTS_PER_SKILL=8`，`daily_estimates.py:87-88`，本卡遠低於兩者）；`max_connections=25`、`shared_buffers=64MB`（A2 同行） |
| **非秘密有效設定（不印完整環境）** | 以 `env -i PATH="$PATH" HOME="$HOME"` ＋ **白名單**啟動，只傳：`DATABASE_URL`、`JWT_SECRET_KEY`（從 `$RUN/jwt-secret.txt` 讀，**不印**）、`DATA_PROVIDER=demo`、`AI_PROVIDER=mock`、`APP_ENV=development`、`ENABLE_LEARNING_SCHEDULER=true`、`LEARNING_SCHEDULER_JOBS=daily_model_estimate`、`AUTO_COLLECTION_ENABLED=false`（`config.py:156`）。**不匯入 `backend/.env`**（13 鍵、mtime 2026-11-06 12:14:07+0800，§9.4 A0-9 已讀回同值），因為它把 `DATABASE_URL` 指向 `test_safety_gate.py:33-38` **黑名單**裡的 `ai_investment_lab`，且帶有 `RESEARCH_AUTH_BYPASS_ENABLED=true`／`CANDIDATE_PASSWORDLESS_MODE=true`（§8.7.0 #8）。**證據包内只寫鍵名與 SET／EMPTY 狀態，不寫值** |
| **零外呼的可強制證據** | `env -i` 使 `DATA_PROVIDER=demo`／`AI_PROVIDER=mock` 成為**唯一提供者**（而不是「我保證不叫」）；外加一條可驗證的代理證據：tick 結束後 `grep -c` 該 log 内 `kraken\|twelvedata\|https?://` 必須＝0，並用 `lsof -p <pid>` 讀回該進程**只**有 127.0.0.1 側連接。⚠️ **不得**把「有 validator」當成開發環境棧的安全性保證（`config.py:214-216` `is_production_like` 只認 production／staging，不安全金鑰檢查排在它**之後** `:233/:236`） |
| **停止方式** | 三個停線條件：**①** 觸發時刻過 90 秒仍無 `learning_jobs` 列 ⇒ 停並存 log；**②** 出現 `KillSwitchScope` 任一 scope 為 active（`app/models/kill_switch.py:10-15`＝global／training／execution／inference；計費 lane 查的是 **`inference`**，`daily_estimates.py:936`、worker 側 `learning_scheduler.py:318`）⇒ tick **必須**不寫帳；**③** 任何非預期的第二支 lane 出現在啟動日誌 ⇒ 立即 `stop_learning_scheduler()`（`main.py:72`）。收工＝**先停程序再停 DB**：`pg_ctl -m fast stop`，**不刪 PGDATA**；⚠️ 不得以已停端點的 HTTP 回應證明停止 |

### 9.11 C 卡正文（瀏覽器驗收；**固定單一 build／start 模式**，自動化與本人操作分開）

提示原話：「C：固定單一 build／start 模式、前端 URL、API base 和 DB；自動化與 owner 本人操作分開，代理不替 owner 同意。」⇒ **不列選項、不二選一**，本卡只有一種模式：

| 格 | 固定值 | 為何是它（實測，非偏好） |
|---|---|---|
| **模式** | **candidate stack**：`./start_candidate_stack.sh`（`start_acceptance_env.sh` 在本機**不可用**） | `command -v docker` ⇒ **not found**（§9.7）；`start_acceptance_env.sh:34-35` 在碰任何行程之前拒跑（r13 §8.7.5.0 已把它跑到那條拒路，rc=2） |
| **DB** | 與 **B 卡同一支** `alethra_owner_test`（A1–A4 產出） | 不另建 DB：**兩個 DB 就是兩個現場**，讀回的帳對不上畫面 |
| **後端** | `uvicorn`（腳本 `:397`），預設 origin **`http://127.0.0.1:8101`**（預設值在 `:601-602`） | `NEXT_PUBLIC_API_URL="$BACKEND_ORIGIN"` 由腳本自己傳（`:446`）——**先有值後建置**，就是 r13 §8.7.5.1 缺陷 1 的修法 |
| **前端** | **`next dev`**（**不是** build＋start），埠 **3101**，`NEXT_DEV_DIST_DIR=".next/candidate-3101"`（`:444-446`） | 腳本本體就是 dev 模式；**distDir 隔離到埠號子目錄**，所以不會覆寫那個 199 MB 共用 `.next`（r13 §8.7.0 #3 的實物教訓：舊文寫 `NEXT_DIST_DIR` 而 `next.config.js:7` 讀的是 `NEXT_DEV_DIST_DIR`） |
| **前端 URL** | **`http://127.0.0.1:3101`**（同時設 `E2E_FRONTEND_BASE` 為同值：`playwright.config.ts:5` `?? 'http://localhost:3001'`、`baseURL` `:23`） | `localhost` vs `127.0.0.1` **不是同一个 origin**：`src/lib/api.ts:11` 的 fallback 是 `http://localhost:8000`、`next.config.js:9` 同值，而 `CORS_ORIGINS` 必須**明設成前端實際 origin**（後端只讀 `settings.cors_origins_list`，`app/main.py:27`；預設 `http://localhost:3000`，`config.py:79`） |
| **API base** | **`http://127.0.0.1:8101`**（由 `:446` 注入，構建期寫入 chunk） | 上一条的讀回判準：`grep -rl "127.0.0.1:8101" <distDir>` ≥1 **且** `grep -rl "localhost:8000"` ＝0 |
| **自動化（代理）** | `browser-use` MCP：`navigate_page`／`take_snapshot`／`click`／`fill`／`take_screenshot`，走完 §8.4 旅程：登入 → 明選合法估計 → 面板提交 → 讀回 → CSV／JSON 匯出 → 第二帳號看不到。**每個動作都要 owner 對「啟動瀏覽器＋對該 URL 發起請求」明確批准**；本輪未批、未跑 | 代理**不替 owner 同意**：本卡文字本身不構成同意 |
| **本人操作（owner）** | owner 自己在桌機走同一遍，代理只交逐格核對表（步驟／預期畫面／實際畫面讀回／截圖檔名） | 與自動化**分開批**；這條不需要批准自動化，但需要 owner 出力 |
| **前置缺件（必須先講）** | **今輪兩條旅程 0 支 e2e spec**（§8.7.5.3 實測：`daily-estimate`／`OrderPreviewPanel`／`pp-download-decision-history` 全 0 命中）⇒ 批了 C 也只有三格可兌：`next dev` 起得来／API base 對／CORS 預檢通過。**旅程那一格需要新代碼（spec）或本人走查** | 不得以「前端 unit 有 115 支過」冒充瀏覽器驗收（記憶：證據分層不互換） |
| **判準** | ①`/health/ready` 通過（readiness 語意 `start_acceptance_env.sh:134` 同形）；②預檢回應含 `access-control-allow-origin`；③`schema-head` 與 A4 讀回的 head **同值**，否則腳本自己在 `:576` 拒起（該判別式實測存在）；④只報「收集 312／跑 N／過 N／跳 N／敗 N」四個數，**不拿 312 當通過數**；⑤`E2E_BROWSER_CHANNEL=chrome` 未設時第一支就 fail——那種 fail 是**環境**失敗，**不得記入產品**（`~/Library/Caches/ms-playwright` 0 entry，r13 實測） | 停線：任何一條不符 ⇒ 停、存 log、回報，**不重跑、不改設定碰運氣** |

### 9.12 D 卡正文：**拆成三張可獨立審批**的卡（提示明文，本輪不合并）

| 卡 | 名字（一卡＝一個獨名操作） | 批准後**會發生什麼** | 批准後**不會發生什麼** | 判準／停線 |
|---|---|---|---|---|
| **D-1** | `D-1-read-r14-package`：**審閱附件** | 什麼也不執行。owner 讀：`REVIEW_SCOPE.md`（1,223 行／110,034 B／`273433e4…`）＋ `REVIEW_SCOPE.json`（3,123,363 B／`588592af…`）＋ `SHA256SUMS.txt`（2,331 B／`eee05f1f…`）＋ `diffs/` 16 檔 ＋ **完整 214 條 manifest（§9.1＋§9.1.2＋§9.1.4）** ＋ **六組分組表（§9.3）**。附件即本節所引物件，不需要額外交付 | 不套 patch、不 stage、不 commit、不 push、不跑任何測試 | 完整性由 `shasum -a 256 -c SHA256SUMS.txt` 自證；讀者可只反駁**規則**（`GROUP_RULES` 在 `build_r14_review_scope.py:54-76`）而不必反駁我手挑的清單 |
| **D-2** | `D-2-isolated-repro`：**隔離重現**（只在新目錄） | 在**一個新目錄**（`mktemp -d`）用**固定本機來源**重現所需檔案，證明依賴閉包：11 支種子 ＋ §9.2 的 **136 支一起髒** ＋ 需要的 **389 支乾淨檔（只能從 HEAD 讀）**。跑完在新目錄 `collect` 一次，比對 `collected-node-ids` 是否＝3,867 | **不碰共享工作樹**（不改 `backend/**`）、**不 stage、不 commit、不 push**、不動釘選、不啟 PG／服務／瀏覽器 | **明文承認**：只靠最新包的 183 檔**無法重現**，因為閉包需要 HEAD 上那 389 支乾淨檔（§9.2）＋ 30 條**永遠不會進包**的路徑（§一之補二）。**這不等於那 214 條全部可發布**——發布範圍是 D-3，不是 D-2 |
| **D-3** | `D-3-release-scope`：**發布範圍**（需 owner 另批具體檔案集合） | 由 owner **逐檔**指定可保存集合。本卡交上**三個可鉤的邊界**（不是預設批）：①**11 支種子**（§9.1＋§9.1.2，本輪真正動過的代碼）；②＋**§9.3 標了「是」的四組**（每日估計 16／決策來源 22／基準釘選 17／前端 helper 24）；③＋**§9.3.2 那 33 支 `backend/app` 產品碼與 12 支 `frontend/e2e`**（它們**只因舊輪次寫入而存在** ⇒ 本輪標**待確認**）。migration 7 支建議**單列**：零改動、可獨立保存，要的是**執行權**不是保存權 | **未批之前不保存任何檔**；本卡不隐含 commit／push（Git 寫入另批） | 證據樹（`alethra-evidence/**`）**預設不入庫**（§9.1.4）；紀錄本檔若入庫，屬**文件**不是代碼，需明列；`.env`／任何秘密**永不可入此集合**（AGENTS.md） |

**三張卡的相依**：D-1 可單獨批；D-2 只需 D-1 已交附件；D-3 不依賴 D-2 的結果（但**建議**在 D-2 證過閉包之後再批，否則集合可能缺檔）。**A／B／C 與 D 三者互不隐含批准**。

### 9.13 逐卡批准表（一卡＝一個獨名操作；**放置 ≠ 執行**；本表不要求一次勾整張）

| 卡 | 精確批准正文（可直接引用这句去批） | 本輪狀態 |
|---|---|---|
| A0 | 「批准執行 §9.8 那 9 條**只讀、零寫入**命令，結果與 §9.8 預計輸出逐字比對，不符即停」 | **本輪已做**（不需批准即可重複，因它零寫入） |
| A1–A2 | 「批准在 `$TMPDIR/alethra-r14-pgdata` 新建並啟動一個 16.2 隔離叢集（127.0.0.1:5432，`max_connections=25`、`shared_buffers=64MB`），僅 A1＋A2 兩步」 | 未執行・未批准 |
| A3–A4 | 「批准建 `alethra_owner_test` ＋ `alethra_r14_migrator`／`alethra_r14_app`（四旗標全 f）＋ `REVOKE ALL ON SCHEMA public FROM PUBLIC`，並將空庫升到 `d4e5f6g7h8i9`（57 revisions）」 | 未執行・未批准 |
| A5 | 「批准讀回 `information_schema`／`pg_indexes` 核對 `linked_inference_id` 兩欄只有 index 無 FK」 | 未執行・未批准 |
| A6 | 「批准在 pg 層跑 §9.9.1 **前 4 支** node id（帳號額度 ≥4）」 | 未執行・未批准 |
| A7 | 「批准在 pg 層跑 §9.9.1 **後 2 支** node id」 | 未執行・未批准 |
| B | 「批准 §9.10 那一格棧：同支隔離 DB、單一 lane `daily_model_estimate`、額度 1、`env -i` 白名單、三個停線條件」 | 未執行・未批准 |
| C-dev | 「批准以 candidate stack 起 `next dev`:3101 ＋ uvicorn:8101（共用 B 的 DB），只驗證 readiness／API base／CORS 預檢三格」 | 未執行・未批准 |
| C-agent | 「批准代理用 browser-use MCP 對 `http://127.0.0.1:3101` 走完 §8.4 旅程」 | 未執行・未批准（且**尚無 spec**） |
| C-human | 「owner 本人走同一遍，代理只交核對表」 | 未開始（不需批准，需 owner 出力） |
| D-1／D-2／D-3 | 見 §9.12 三行 | D-1 **已交付**；D-2／D-3 **未批准** |
| **重釘**（新，本輪量出來的必要裁決） | 「批准以本輪候選 `162d0be4…`／3,867 重釘 `backend/tests/collected_nodes.txt` 與 `skip_budget.json`（含刪除舊 4 格、新增 60 格），重釘前整套 A 卡一律 NOT RUN」 | **未批准⇒ 本節所有整套格維持 NOT RUN**（§9.0.1） |
| **拆包**（新，r13 §8.8.5 第四節那張未寫的卡） | 「批准只套 §9.1.2 那 4 支代碼檔（＋1 支文件）而不夾帶 §9.3.2 那 45 支（33 產品碼＋12 e2e）」 | 未批准・未寫 patch |

**最後一行不是客套**：本輪沒有產生任何新的 `git apply` 材料，也没有把 §9.1 那 11 支包成可套 patch——套 patch 本身是寫入，需 D-3 先選邊界。**本节到此都是審閱材料與正文，未執行其中任何一格。**

---

## 十、第十五輪：四道閘分開、兩條旅程逐入口、污染入口第二個（dotenv）

### 10.0 本輪身份（先固定，後面所有數字都以它為準）

| 項目 | 值 |
|---|---|
| `HEAD` | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（`live1-impl`，本輪未動） |
| 本節附加前的紀錄 | `4194` 行 / `748,339` B / `585cf868b674073fbf7772b3cf358ba2cba8307eb01d87f72cb63efa605baa1e`（＝第十四輪交付時同一值，未改動前 3,793 行以前的任何一個位元組） |
| 附加後的純增量證明 | `head -n 4194` 重新 hash ＝ 上列 `585cf868…`（逐字相等），行數仍 `4194`／位元組仍 `748,339` → §九 與其前正文**未被改動**，本節是尾端追加 |
| 本節自身的後續修改（不隱瞞） | 追加後在本節正文把 **15 類簡化字形**改回繁體（實／個／種／來／為／於／屬／連／動／點／門／沒／掛／庫／張），另有 1 處中英混排（「在那個端口起 Anything…」那半句混了英文與簡體字形），本節已改寫為全繁體；全部落在 4195 行之後。第一次交付的 `a98b6034abd1867ebaedd0bfa12e6154118045e33a01b259f815ad437e934ae3` 因此作廢。檔案不能在自己正文內記錄自己的最終 hash，所以本節身份以上列 prefix hash（`585cf868…` @ 4,194 行）＋「§十 標題在第 4,198 行」為準；要核對整檔就重新 `shasum -a 256 EXECUTION_TABLE_20261117.md` |
| lane fingerprint | `python 3.12.16 \| pytest 8.3.3 \| sqlalchemy 2.0.36`（三次運行皆同） |
| python 樹 digest（本輪修改後） | `e4642489c613f57bf88cba9bdcd582ccdda5b398da56c0dbd036c9cbc8f41eac` |
| 釘選檔 | `tests/collected_nodes.txt`、`tests/skip_budget.json` 本輪**未寫入**（無 `pin`、無 `--ack-diff`） |

本輪的執行紀錄（全部 sqlite／in-process，無 PG、無服務、無瀏覽器、無外呼）：

| run dir（`${TMPDIR}alethra-runs/`） | 命令 | 結果 |
|---|---|---|
| `sqlite-20261120T082104Z-22106` | `./scripts/run_tests.sh sqlite -v tests/test_conftest_sqlite_default.py` | `collected 20 / 20 passed`，`3.89s`，閘：第二類 PASS、第三類免除 |
| `sqlite-20261120T082135Z-22533` | `./scripts/run_tests.sh sqlite` ＋ 15 支會讀 `get_settings()` 的檔案 | `collected 428 / 427 passed, 1 skipped(pg_only)`，`289.13s` |
| `sqlite-20261120T083044Z-25109` | `./scripts/run_tests.sh sqlite` ＋ 9 支兩條旅程後端檔案 | `collected 194 / 190 passed, 4 skipped(3 pg_only + 1 other)`，`130.97s` |
| （非 lane）`node22 --test` | `tests/dailyEstimate.test.mjs dailyService.test.mjs apiRequestCorrelation.test.mjs userErrors.test.mjs` | `# tests 86 / # pass 86 / # fail 0` |

### 10.1 四道閘是四件事（程式分支，不是解釋）

第十四輪把「`collected 3862／3867` 對 `pinned 3811` 的拒絕」講成額度閘，並在 §9 推出「任何整套 A 卡必然被拒」。**該說法在本輪被程式碼否決，正式撤回**：那道拒絕是第三類，只管用在一個模式下，而且對顯式選取與 pg 層都不生效。四道閘的實際邊界：

| # | 閘 | 誰管 | 觸發條件（逐字來源） | 本輪實測 |
|---|---|---|---|---|
| 1 | **受保護 DB／目標身份安全閘** | `run_tests.sh:169-219` ＋ `scripts/check_db_env.analyse` ＋ `test_safety_gate.verify_test_database` ＋ `conftest.py:_validate_pg_test_target` | 只在 `MODE=pg`；順序：未點名 URL → `refusing: pg layer needs ALETHRA_PG_TEST_URL (a disposable PostgreSQL database)`（`:170-172`，rc 2）→ 匯入四個 DB 變數與 `ALETHRA_TEST_REQUIRED_DB_PREFIX=${…:-alethra_}`（`:174-179`）→ 純解析的 preflight（`:183-186`，rc 3）→ 安全閘（`:188-201`，rc 3）→ 連線可達（`:202-219`，rc 3）。`analyse()` 不連線（`check_db_env.py:48-126`；連線在 `connection_problem :129-134`） | 見 §10.1a 三個重現，全部 rc 2／3 且**未建立 run dir** |
| 2 | **顯式選取的證據閘** | `run_evidence.py:395-429`（`structural_failures`）→ `:435` 永遠求值 → `:477-481` 拒絕／`:482` 放行 | 與選取無關：任何跑了並留下 `collect.txt`＋`junit.xml` 的 sqlite 行都必須自我對得起來（記錄數＝收集數、無未映射、無重複、兩個 skip 計數一致） | 本輪三次 lane 執行皆 `evidence gate: ledger closes against this run's own artifacts`；另外用一份**複製到 `/tmp/alethra-gate2-repro-24507` 的假造副本**（刪掉一個 `<testcase>`）重現拒絕：rc 1、`EVIDENCE GATE REFUSES:` / `- 1 collected node(s) produced no outcome record (whole-file skip or crash)`，**同時仍印出第三類免除那行**——兩閘互相獨立的直接證據 |
| 3 | **whole-suite 節點清單與 skip 預算閘** | `run_tests.sh:515` 的 `[ "$MODE" = "sqlite" ] && [ -z "$COLLECT_ONLY" ]` ＋ `:520-523` 的 `--explicit-selection` 轉接 ＋ `skip_budget.py:188-210` ＋ `run_evidence.py:442-476` | 只管用：sqlite ＋ 真的執行過 ＋ **不是**顯式選取。顯式選取 → `skip_budget.py:188-195` 印 `this run selected specific tests, so the whole-suite ceiling does not apply; the census is reported but not enforced.` 並 **return 0**。pg 層 → `run_tests.sh:476` 註解逐字：`The pg layer has no such ceiling yet, so it is not judged here.`（`MODE=pg` 根本進不了 `:515` 的分支）。collect-only → `:534-536` `collect-only run: nothing executed, so no ceiling is judged and no ledger closes.` | 對既有整套 run dir 重現（唯讀、不改任何東西）：`.venv/bin/python scripts/skip_budget.py …/sqlite-20261120T070931Z-85151/pytest.log check --budget tests/skip_budget.json --identity …/identity.txt` → rc **2**、`refused: this whole-suite run collected 3867 tests but the budget pins 3811: the selection changed (files added, removed or deselected), so the ceiling was not measured against this suite. Re-measure and regenerate the budget from a real lane log.` |
| 4 | **客戶 `ai_usage` 額度** | `app/auth/entitlements.py:252` `reserve_quota`、`:336`／`:501` 的 `HTTP_429_TOO_MANY_REQUESTS`；呼叫點在 `app/api/inference.py:71`、`app/api/ml_training.py:186,357,452`、`app/api/ai_assistant.py:482` | 這是**產品 entitlement**，在 route 層扣額並回 429。它不是 lane 的閘：`grep -n "ai_usage\|quota\|entitlement" run_tests.sh skip_budget.py run_evidence.py check_db_env.py test_safety_gate.py` 只命中 `check_db_env.py:9` 的一行 docstring（講的是某個測試模組的名字），**零個執行分支** | 未觸碰（本輪不啟動 PG、不連 DB，也就沒有任何計費寫入） |

#### 10.1a 第一類閘的三個重現（本輪實際下過、零連線、零副作用）

```
$ env -u ALETHRA_PG_TEST_URL ./scripts/run_tests.sh pg
refusing: pg layer needs ALETHRA_PG_TEST_URL (a disposable PostgreSQL database)     # rc 2

$ ALETHRA_PG_TEST_URL="postgresql+psycopg2://alethra_test_runtime@localhost:5432/ai_investment_lab" ./scripts/run_tests.sh pg
preflight: database 'ai_investment_lab' does not start with the required disposable prefix 'alethra_'
preflight rejected the environment; pytest was not started.                          # rc 3

$ ALETHRA_PG_TEST_URL="postgresql+psycopg2://alethra_test_runtime@localhost:5432/ai_investment_lab_test" ./scripts/run_tests.sh pg tests/test_config.py
preflight: database 'ai_investment_lab_test' does not start with the required disposable prefix 'alethra_'
preflight rejected the environment; pytest was not started.                          # rc 3
```

三次都沒有建立 run dir（`${TMPDIR}alethra-runs/` 最新目錄仍是執行過的那三個），所以被第一類拒掉的執行**不可能被誤當成一份 lane 結果**：它沒有身份檔、沒有 log、沒有帳本。

### 10.2 各 A 操作實際受哪一道閘（本輪要的對照）

| A 卡操作 | 受哪道閘 | 依據 | 本輪能否在不啟動 PG 下證明 |
|---|---|---|---|
| **A0 只讀身份核對**（`current_database()`／`alembic current`／`SELECT` 讀回） | 只有第一類（而且只有當它透過 `run_tests.sh pg` 跑）；`psql`／`alembic` 直連**不經過任何一道 lane 閘** | `run_tests.sh:169-219` 只在 `MODE=pg` 分支；`alembic` 走自己的 `sqlalchemy.url` | 能：§10.1a 已交出門禁逐字；A0 的「直連不受 lane 管」是**缺口**，只能靠 owner 批准的動作本身約束 |
| **A1–A2 建立測試庫／初始化** | **四道閘都不管**：建庫是 `createdb`，不是 lane 執行 | 三個 lane 程式無一處建庫 | 能事先說明；實際建庫需要批准 |
| **A3–A4 migration upgrade** | 四道閘都不管（同上）。唯一與之相關的閘是「migration 尾鏈被釘住」這條**測試**，它在 lane 內 → 走第二類；若以整套跑 → 才碰第三類 | `run_tests.sh:515`、`skip_budget.py:188-195` | 能：尾鏈測試屬顯式選取時明確免除第三類 |
| **A5 schema 讀回** | 若用 `run_tests.sh pg` 執行讀回測試 → 第一類＋第二類；若用 `psql -c "\d"` → 無閘 | 同上 | 能 |
| **A6–A7 pg 層顯式節點（含 row-lock／advisory-lock race）** | 第一類（必然，且每次都過）＋第二類（跑完就要對得起帳本）；**第三類永遠不觸發** | `run_tests.sh:169-219`、`:476`、`:515`；`run_evidence.py:395-429` | 能：待驗的 4 支節點已在 §10.6 逐列點名 |
| 「整套 pg 基線」 | 第一類＋第二類；第三類不适用（pg 分支不進 `:515`） | 同上 | — |

**因此：**「`collected 3867 ≠ pinned 3811`」只否決「以整套 sqlite 作為已被釘選的那一輪」，它不否決 A0／A1–A7 的任何一張卡；真正會否決 pg 卡的只有第一類，而第一類在 §10.3 有一個本輪新測出的名稱問題。

### 10.3 新測出的事實：第一類閘內部的兩層對「合法庫名」並不一致

`test_safety_gate.ALLOWED_TEST_DATABASES`（`test_safety_gate.py:25-31`）與 runner 的 `ALETHRA_TEST_REQUIRED_DB_PREFIX`（`run_tests.sh:178` → `check_db_env.py:116-120`，預設 `alethra_`）是**兩個獨立的名稱檢查，而且先跑的是前綴**。以 `analyse()` 純解析逐一名稱實測（無連線）：

| 資料庫名 | 在白名單內？ | 前綴 preflight | 兩層都過的實際結果 |
|---|---|---|---|
| `ai_investment_lab_test` | 是 | **拒絕**（`does not start with the required disposable prefix 'alethra_'`） | 不可用（經 lane） |
| `alethra_test` | 是 | 通過 | **可用** |
| `alethra_owner_test` | 是 | 通過 | **可用** |
| `test_ai_investment_lab` | 是 | **拒絕** | 不可用（經 lane） |
| `alethra_candidate_20261120` | **否** | 通過 | 到安全閘 `:215-221` 才會被白名單拒絕 |

要講清楚的是：**這不是矛盾，也不是 bug**，而是兩層各有分工（前綴＝「可棄」，白名單＝「已知」），交集恰好只有 `alethra_test` 與 `alethra_owner_test` 兩個名字。影響到 B 卡的是：過去 B／A 卡如果指定 `ai_investment_lab_test`，透過 `run_tests.sh pg` 會在**第一個**檢查就被拒 rc 3，與第三類、與計費都無關；同一個名字用裸 `pytest` 直跑則只過 conftest 的 `_validate_pg_test_target`（名字含 `test` 即可）＋白名單，**會被接受**。也就是「同一個目標，走兩個入口，一道閘管不到」。本輪不建議放寬任何一邊；B 卡改成點名交集內的兩個名字之一。

### 10.4 baseline 仍是四段分離、仍是 UNKNOWN（本輪未新增第五段）

第十四輪 §9.8 的四段（最後可證明獲准的來源／現有釘選 3811／歷史受測／本輪候選）本輪不變，也**不因為多了一檔新測試就要求追認**：本輪候選是 `3867 + 5 = 3872`（第五節的 +5 是 `test_conftest_sqlite_default.py` 新增的 5 個節點；`3867` 是 `sqlite-20261120T070931Z-85151/identity.txt` 的 `collected-node-ids`，本輪未重跑整套）。正式 baseline 在沒有批准證據前**維持 UNKNOWN／未批准**。

### 10.5 本輪 diff：分「本輪修改／累計修改／必要依賴」

**（a）本輪修改 — `backend/tests/conftest.py` 第 70–104 行（新增 35 行，正文如下，這是實際內容不是摘要）**

```python
# === 二、消除會污染證據的入口：開發者的 `backend/.env` 不再重新配置這個進程 ===
# `app/config.py:560` declares `env_file=".env"` — a *relative* path, so
# pydantic-settings resolves it against the process cwd, and both entry points
# put the cwd inside `backend/`: `scripts/run_tests.sh` cds there before pytest,
# and a bare `pytest` under `backend/` starts there too. Measured 2026-11-20 on
# this checkout with only the gitignored local file present:
#
#     Settings()               -> data_provider='twelvedata',
#                                 live_trading_enabled=True,
#                                 research_auth_bypass_enabled=True
#     Settings(_env_file=None) -> 'demo', False, False
#
# The database target is not the exposure (the lane exports `DATABASE_URL`, and
# an environment variable outranks a dotenv value). The exposure is every key
# the lane does *not* export: `app/main.py:13` snapshots `get_settings()` at
# import time and `app/main.py:52` starts the learning scheduler from that
# snapshot during the `client` fixture's startup, so a file nobody reviews could
# arm the cron lanes for a test run — and `DATA_PROVIDER` is what decides
# whether a test may reach a market-data API at all. The dotenv source is
# therefore removed for this process; environment variables keep configuring the
# app exactly as they do in production.
from app import config as _config_module

_DOTENV_WOULD_LOAD = _config_module.Settings.model_config.get("env_file")
_config_module.Settings.model_config["env_file"] = None
# A snapshot cached before the patch would outrank it, so the cache is dropped.
_config_module.get_settings.cache_clear()


def pytest_report_header():
    """Say in the lane's own log which source was removed, not that it is clean."""
    return (
        "developer dotenv: neutralised (env_file "
        f"{_DOTENV_WOULD_LOAD!r} -> None; environment variables still apply)"
    )
```

它只動測試入口，**產品碼一行未動**（`app/config.py` 保持原樣）；環境變數照常生效，所以 `env -i` 之類的實驗不能代替這條證明。它在 lane log 的證據是 `sqlite-20261120T082104Z-22106/pytest.log:8`：

```
developer dotenv: neutralised (env_file '.env' -> None; environment variables still apply)
```

**（b）本輪修改 — `backend/tests/test_conftest_sqlite_default.py`（未追蹤新檔，現 183 行／20 節點；第十四輪是 15 節點，本輪 +5）**

新增 `DOTENV_KEYS`／`CODE_DEFAULTS`／`_write_dotenv()` 與 `class TestDotenvSourceNeutralised`，5 個節點：

| 節點 | 它在斷言什麼 | 它是正控還是被驗物 |
|---|---|---|
| `test_the_pristine_source_was_one_relative_dotenv` | 修法之前的源頭確實只是一個相對 `.env`（否則這層防護在守一個不存在的東西） | 前提 |
| `test_the_app_setting_no_longer_declares_a_dotenv` | `Settings.model_config["env_file"] is None` | 被驗物 |
| `test_a_named_env_file_still_configures_the_app` | 同一份檔案用 `Settings(_env_file=…)` 顯式指定時**仍然生效**（四個鍵都變成 dotenv 的值） | **正控**：沒有它，「檔案被忽略」和「pydantic 根本不讀 dotenv」不可區分 |
| `test_the_same_file_ignored_from_the_lane_cwd` | `monkeypatch.chdir(tmp_path)` 後 `Settings()` 回到碼內預設值 | 被驗物 |
| `test_the_import_time_snapshot_keeps_the_code_defaults` | `get_settings()` 這個 import 期快照也是預設值（`app/main.py:13` 用的就是它） | 被驗物 |

**（c）本輪修改 — 兩支新 e2e spec（只撰寫、未執行、未啟動任何服務或瀏覽器）**

| 檔案 | 行數 | 覆蓋的格子 | 狀態 |
|---|---|---|---|
| `frontend/e2e/manual-decision-lineage.spec.ts` | 233 | 旅程一唯一沒被任何 browser spec 覆蓋的一格：**明選血緣**經提交後仍在讀回與匯出上 | 通過 `tsc --noEmit --strict`（rc 0）；**未執行**，檔頭逐字標明 STATUS |
| `frontend/e2e/daily-estimate-journey.spec.ts` | 265 | 旅程二在瀏覽器完全沒有 spec 的整個 UI 層（讀取組免扣額；寫入組需 `E2E_DAILY_ESTIMATE_SKILL` 親自點名） | 同上，且**未碰 grant／revoke**（不替本人同意或撤回） |

兩支都遵從本倉庫現有約定：`./runtime` 的 `runtimeAvailable()`／`TIER_B_SKIP` 檔案層級 skip（避免第十四輪那種「一支檔載入就 throw 導致 32 支列不出來」）、`page.request.fetch` 唯讀 oracle、`waitForResponse`／expect-poll，**不 intercept、不 stub、不 mock**，結果一律標 dev stack。
要更正第十四輪的目錄印象：`frontend/e2e/manual-decision-retry.spec.ts` 雖然在 `e2e/` 目錄下，但它是 10 次 `global.fetch =` 的 **client unit**，`page.goto` 0 次 —— 它不是、也不能被引用為 browser 證據。

**（d）累計修改 vs 必要依賴**：`git diff --numstat` 對 `backend/tests/conftest.py` 給 `77 4`（**累計**＝第十四輪 §一＋本輪 §二 總和，不是本輪），本輪只有上述 35 行；`backend/tests/test_conftest_sqlite_default.py` 對 HEAD 是 `??`（未追蹤）。必要依賴僅 `app.config`（讀 `Settings.model_config`）與 `pytest` 的 `pytest_report_header` 掛勾；無新套件、無新設定鍵、無 migration。

### 10.6 兩條旅程逐入口表（client → route → 持久化 → 讀回／匯出 → UI）

圖例：**實**＝已實作（有 file:line）；**歸**＝本輪直接回歸節點（在 §10.0 那三次執行內通過）；**PG✗**＝本輪未啟動 PG，故 PG 層未驗（其中標了跳過理由的是結構上只能 PG 的節點）；**BR✗**＝browser 未驗（本輪未啟動瀏覽器）。

#### 旅程一：本人手動決策（含明選的模型推理血緣）

| 入口 | 實 | 歸 | PG✗ | BR✗ |
|---|---|---|---|---|
| client：`OrderPreviewPanel.tsx:910-961`（`ws-lineage-*`）、`:1066-1107` review、`:1129-1189` 提交與從**服務端回應**讀血緣（`decision_detail.linked_inference_id`，:1186）；`src/lib/api.ts:423-447` | 實 | 本輪前端 86 條 unit **不含此面板**（只有 dailyEstimate／dailyService／apiRequestCorrelation／userErrors）→ 無直接回歸 | — | BR✗（本輪新寫 `manual-decision-lineage.spec.ts`） |
| route：`POST /api/v1/paper-portfolio/{id}/decisions`（`paper_portfolio.py:956-1072`：擁有權 `:965`、自由文字必填 `:969/:971`、憑證決定 actor `:976`、版本閘 `:982-1037`、血緣閘 `:1031-1037`）；`POST …/manual-decision`（`simulations.py:2295-2412`，冪等優先 `:2319-2375`、403 `:2326`、409 `:2362-2399`） | 實 | `tests/test_decision_lineage.py`、`tests/test_manual_decision_label_capture.py`、`tests/test_decision_provenance.py`、`tests/test_decision_confirmation_status.py`（194 節點那輪全過） | PG✗ | BR✗ 部分（提交路徑已有 `order-lifecycle-flow.spec.ts:255-285`，但**未執行於本輪**） |
| 持久化：`DecisionRecord` 建行 `:1039-1054` ＋ `emit_event` `:1058-1068` ＋ commit `:1070`；模擬頁寫 `HumanDecisionEvent`（`simulations.py:2387-2399`） | 實 | `tests/test_human_decision_recording.py` | PG✗ **結構上只能 PG 的 3 節點**：`test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::{test_two_requests_on_one_idempotency_key_leave_exactly_one_order, test_a_stale_account_read_cannot_overwrite_a_committed_booking, test_a_manual_submit_racing_an_advance_stays_consistent}`，sqlite 逐字理由 `row-lock serialisation requires a real PostgreSQL backend` | BR✗ |
| 讀回／匯出：`GET /{id}/decisions`（`:1092`）、決策歷史 `GET /{id}/decision-history`（`:1810`，CSV 檔名 `:1846`）、血緣欄的文件契約 `:1399` | 實 | `tests/test_decision_history_export.py`、`tests/test_journal_api.py` | PG✗ | BR✗ |
| UI：模擬頁 `simulation/page.tsx:1133-1192` 血緣選擇器、`:1432` 決策列的 `sim-decision-lineage` | 實 | 無 unit | — | BR✗ |

**兩項不是缺口、本輪不改（附理由）：**
1. `/journal/unified` 不含 `decision_records`：`app/api/journal.py:500-507` 的文件契約就寫明它是 research trades ＋ paper fills/orders 的合併視圖；血緣的**正式讀回入口是 `/{id}/decision-history`**（`:1810`），第 #195／#205 輪已在該入口閉合。把無單決策塞進成交日記會違反該檔自己的契約。
2. Explore 子代理回報「重複決策會在 `:1070` 因為沒有 `IntegrityError` 處理而 500」：**已否決，未改碼**。`uq_decision_records_source_per_portfolio`（`app/models/paper_portfolio.py:240`）對 NULL `source_trade_id` 不構成約束，而這條 route 寫的正是 NULL——`paper_portfolio.py:1684` 逐字記載了這件事。該 500 路徑不存在。

#### 旅程二：每日模型估計（受控每日服務）

| 入口 | 實 | 歸 | PG✗ | BR✗ |
|---|---|---|---|---|
| client：`DailyEstimatePanel.tsx:105-113,129-240,328-333,489-540` ＋ `src/lib/dailyEstimate.ts:35-61,98-110,136-157,189-223`；路由 `src/lib/api.ts:891,909,913` | 實 | `frontend/tests/dailyEstimate.test.mjs`、`dailyService.test.mjs`（本輪 **86/86 通過**） | — | BR✗（本輪新寫 `daily-estimate-journey.spec.ts`） |
| route：`personalization.py:426`（consent）、`:459-464`、`:278-291`、`:505-515`；run 入口 `daily_estimates.py:1349-1524` | 實 | `tests/test_daily_model_estimate.py`（428 節點那輪全過） | PG✗ | BR✗ |
| 持久化：視窗聲明 `daily_estimates.py:536-543`、預留／結算、同日去重 | 實 | 同檔的去重節點 | PG✗ **結構上只能 PG 的 1 節點**：`tests/test_daily_estimate_two_workers::test_two_processes_on_one_window_bill_each_business_key_once`，sqlite 逐字理由開頭 `the seat being raced is pg_advisory_xact_lock, which SQLite has no way to take` | BR✗ |
| 讀回：`GET /daily-estimate/status`（含 `last_estimate`／`last_run`／`reused`） | 實 | 同上；「recovering 回落讀帳本」由第 #230 輪閉合 | PG✗ | BR✗ |
| UI：`DailyEstimatePanel` 掛在 `src/app/training/page.tsx:557`；三態顯示 `:423-540` | 實 | reducer 有 unit、**掛載點無任何測試** | — | BR✗（本輪新寫，含「同一 UTC 視窗點第二次不重複扣額」那一格） |

**本輪仍保留的兩項 owner 決定（不擅改）**：`app/api/personal_models.py:663`／`:761` 兩個 predict 入口**沒有** `check_kill_switch`（grep 確認）；以及「執行中（running）」這個斷言按 `daily_estimates.py:1468-1472` 的設計就無法證明。

**對「面板 unit 已通過、整體只差 browser」的更正**：上表顯示旅程二除了 reducer／route／持久化之外，**掛載點（training page）本身從未被任何一層驗過**，而旅程一的 `OrderPreviewPanel` 既無 unit 也無血緣 browser spec。所以剩的不只差 browser，也差（i）4 個結構上只能 PG 的並發節點、（ii）面板掛載與選取器層。

### 10.7 `.env` 是否被載入：實際的設定契約（B 卡正文，`env -i` 不能代替）

| 條目 | 契約 | 證據 |
|---|---|---|
| 誰宣告源頭 | `Settings` 的 `model_config["env_file"] = ".env"`，**相對路徑** | `app/config.py:560`；本輪 `_DOTENV_WOULD_LOAD` 在 lane log 印回 `'.env'` |
| 相對誰解析 | pydantic-settings 2.6.1 以**行程 cwd** 解析相對 `env_file`（不是倉庫根、不是檔案位置） | 兩條入口都把 cwd 放進 `backend/`：`run_tests.sh:265` 的 `cd "$BACKEND"`、裸 `pytest` 從 `backend/` 啓動 |
| 優先序 | 真實環境變數 **高於** dotenv 值；因此 lane 匯入的 `DATABASE_URL`／`ALETHRA_TEST_DB` 不會被檔覆寫，但 lane **沒有匯入的鍵全部由檔決定** | 實測：`Settings()` → `twelvedata/True/True`；`Settings(_env_file=None)` → `demo/False/False` |
| 快照時機 | `get_settings()` 被 `@lru_cache` 包住（`app/config.py:563-565`），`app/main.py:13` 在 import 期就 snapshot；**改 `.env` 不重啟行程不會生效**，反之 import 後的修改也進不了那份快照 | `app/main.py:13`、`:52`（用該 snapshot 決定要不要起學習排程器）、`conftest.py:91` import `app.main`、`client` fixture `:351-364` 觸發 startup |
| 誰會讀到它 | 15 支測試檔呼叫 `get_settings(`（本輪逐檔數過），其中只有 6 支傅 `_env_file=None`；**本輪後 lane 行程一律讀不到**（§10.5a），產品行程照舊 | `grep -rln "get_settings(" tests/*.py`、`grep -rln "_env_file=None" tests/*.py` |
| 本倉庫 `.env` 的**鍵名**（不列值、不展開環境） | `backend/.env` 758 B、已被 gitignore：`CANDIDATE_PASSWORDLESS_ACCOUNTS`／`_MODE`／`_SESSION_MINUTES`、`DATABASE_URL`、`DATA_PROVIDER`、`JWT_SECRET_KEY`、`KRAKEN_WS_URL`、`LIVE_TRADING_CAPITAL`／`_ENABLED`／`_INTERVAL`／`_SYMBOLS`、`RESEARCH_AUTH_BYPASS_ENABLED`、`TWELVEDATA_API_KEY`；另有 `backend/.env.test` 906 B | 只列鍵名；值未讀、未印、未寫入任何產物 |
| B 卡要固定的是 | 目標庫名必須同時過前綴與白名單 → **`alethra_test` 或 `alethra_owner_test`**（§10.3 實測交集）；`ALETHRA_PG_TEST_URL` 是唯一入口（未設就 rc 2）；`TEST_MODE=true` 由 `:179` 代為匯入，不需要也不會被 `.env` 覆寫 | `run_tests.sh:170-179`、`test_safety_gate.py:179-183`、`conftest.py` §二 |
| 自然觸發 vs 手動測試 | 自然觸發＝排程器按 UTC 視窗自己跑（`daily_estimates.py:1349-1524`）；手動＝`POST /daily-estimate/run`（`api.ts:913`）。兩者共用同一個視窗鍵，所以在同一個 UTC 日內**只計費一次**；本輪只験過手動那一側的後端契約（194／428 節點兩輪），兩者分列不互相代替 | §10.6 旅程二持久化列 |

### 10.8 對 §9.9（A–D 四卡）的逐行修訂

第十四輪正文不重發（同一份記錄、不造平行摘要），只交需要改的地方：

1. **A 卡**：刪除「被計費額度拒絕」的任何說法，改掛 §10.1 第三類；補 §10.1a 三條 rc 與「被拒的 pg 執行不會建立 run dir」這條可核對性質；A1–A4（建庫／初始化／migration）**明寫不受四道閘中的任何一道管**，因此它們的門是 owner 的批准與 `alembic` 自己的設定，不是 lane。
2. **A 卡的只讀／實際分離**：只讀檢查（A0、A5 的 `\d`／`current_database()`）與實際建立／啟動／升級／測試（A1–A4、A6–A7）必須分兩張卡；本輪不下任何一種。不重用、不停止、不讀取平行實例。
3. **B 卡**：目標名改為 §10.3 交集內的 `alethra_test` 或 `alethra_owner_test`（保留原名則必須同時交代它只能走裸 `pytest`，而那正是本輪消滅的那類入口）；附上 §10.7 的設定契約作為「.env 是否被載入」的證明，並註明 dotenv 在 lane 内已被中和。
4. **C 卡**：`8101/3101` 固定為**單一 `next dev` 模式**，結果一律標 dev，不代替 production build 驗收；Docker 缺席只阻塞需要 Docker 的那一種模式，**不擴大成「所有 UI 工程不可做」**——本輪就寫出了兩支 UI 層 spec 與 86 條前端 unit，兩者都不需要 Docker。
5. **D 卡**：區分三種用途（審閱材料／隔離重現／發布保存）；`536`／`214`／`226` 三個數字的單位與重疊在 §9.1.3 已交（214 是 porcelain **路徑清單**的行數，`run_tests.sh:427` 對它取 hash；改本記錄不影響它），分類可重疊的部分直接承認重疊。本輪增量＝§10.5 的四項（conftest 35 行、測試 +5 節點、兩支 spec），完整材料仍以第十四輪那批為準，兩批分交不混。隔離重現若要本機 clone／apply，需新目錄＋本機來源＋精確 patch＋停止線，且**不混入 commit/push**（本輪未做，也未獲准）。

### 10.9 本輪可獨立確認的操作（照抄即可重跑，都是唯讀或 in-process）

```bash
cd /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend

# 1) 第二類閘：顯式選取照法定契約可執行（20 節點）
./scripts/run_tests.sh sqlite -v tests/test_conftest_sqlite_default.py
#   期望末兩行： this run selected specific tests, so the whole-suite ceiling does not apply; …
#              evidence gate: ledger closes against this run's own artifacts

# 2) 第一類閘的門檻與訊息（零連線、零 run dir）
env -u ALETHRA_PG_TEST_URL ./scripts/run_tests.sh pg; echo "rc=$?"          # rc 2
ALETHRA_PG_TEST_URL="postgresql+psycopg2://alethra_test_runtime@localhost:5432/ai_investment_lab_test" \
  ./scripts/run_tests.sh pg tests/test_config.py; echo "rc=$?"               # rc 3（§10.3 的名称發現）

# 3) 第三類閘：對既有整套 run dir 唯讀重現（不改任何東西）
D=/var/folders/tk/4hk5dllx6gjdk9ksk7f5v8900000gn/T/alethra-runs/sqlite-20261120T070931Z-85151
.venv/bin/python scripts/skip_budget.py "$D/pytest.log" check \
  --budget tests/skip_budget.json --identity "$D/identity.txt"; echo "rc=$?" # rc 2

# 4)  dotenv 中和：15 支會讀 get_settings() 的檔案在預設值下仍全綠
./scripts/run_tests.sh sqlite tests/test_ai_assistant.py tests/test_auto_collection.py \
  tests/test_daily_model_estimate.py tests/test_intraday_rest_refresh.py tests/test_learning_api.py \
  tests/test_learning_scheduler.py tests/test_ml_pipeline.py tests/test_paper_create_engine_consistency.py \
  tests/test_personal_api_scope_enforcement.py tests/test_personal_model_api.py \
  tests/test_registration_risk_acknowledgement.py tests/test_research_run_recovery.py \
  tests/test_simulation_daily_tick.py tests/test_skill_unit_ceiling.py tests/test_config.py
#   期望： collected 428 / 427 passed, 1 skipped

# 5) 兩條旅程的後端直接回歸（194 節點）
./scripts/run_tests.sh sqlite tests/test_manual_decision_books_linked_portfolio.py \
  tests/test_manual_decision_label_capture.py tests/test_decision_lineage.py \
  tests/test_decision_history_export.py tests/test_decision_provenance.py \
  tests/test_human_decision_recording.py tests/test_decision_confirmation_status.py \
  tests/test_journal_api.py tests/test_daily_estimate_two_workers.py
#   期望： collected 194 / 190 passed, 4 skipped(3 pg_only + 1 other)

# 6) 前端 unit（86 條）與兩支新 spec 的型別檢查（不啓動任何服務）
cd ../frontend && ~/.local/bin/node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs \
  --test tests/dailyEstimate.test.mjs tests/dailyService.test.mjs \
  tests/apiRequestCorrelation.test.mjs tests/userErrors.test.mjs
PATH="$HOME/.local/bin:$PATH" node22 node_modules/typescript/bin/tsc --noEmit --skipLibCheck --strict \
  --target es2022 --module esnext --moduleResolution bundler --jsx preserve --types node \
  e2e/manual-decision-lineage.spec.ts e2e/daily-estimate-journey.spec.ts; echo "rc=$?"   # rc 0
```

本輪**未執行** `playwright test --list`：`frontend/playwright.config.ts:36-40` 的 `webServer.url` 會探測 `E2E_FRONTEND_BASE ?? http://localhost:3001`，在該端口起任何服務都屬於未批准的操作；兩支 spec 的可列性因此只有「模組載入期不 throw」這一項靜態保證（`runtimeAvailable()` 在檔案層級先跳過，`loadRuntime()` 只在 `beforeAll` 内呼叫）。

### 10.10 本輪回報：客戶仍欠甚麼（一項一行，不再自我核對）

1. **PG 目標的名稱決定**：選 `alethra_test` 或 `alethra_owner_test`（§10.3）——這是 A0／A6／A7 唯一真正的門（第一類），不是額度、不是釘選。
2. **建庫／初始化／migration 的批准**（A1–A4）：這三類動作不受任何一道 lane 閘保護，門只在批准本身。
3. **dev stack 與 production 的驗收分離**（C 卡）：批准 8101／3101 的單一 `next dev`，或明確說不要。
4. **4 個結構上只能 PG 的並發節點**（§10.6 已逐節點列名）——它們是兩條旅程剩下的唯一後端未驗格。
5. **兩支新 spec 的首次執行授權與一次 selector 校正**（本檔只能保證寫得出來、型別過得了，不能保證選取器對得上真實 DOM）。
6. **兩個 predict 入口是否該吃 kill switch**、以及 `OrderPreviewPanel` 的範圍裁定（§10.6 末，兩者都已掛牌多輪，未獲答）。

本輪完成：撤回第四道閘的誤標並把四閘分離到程式分支；實測出第一類內部的名稱交集（新事實）；消滅第二個污染入口（開發者 `.env` 重配測試行程）並附上含正控的 5 個直接節點；證明 15 支讀設定的檔在中和後 428 節點仍全綠；補齊兩條旅程的逐入口表與兩條「不是缺口」的否決；交出兩支未啟動的 browser spec 與一份可獨立重跑的 §10.9。

---

## 十一、第十六輪（2026-11-20 17:0x–18:1x HKT）：**一紙可以真正決定的執行附件**（固定候選＋本輪實際 diff＋A／B／C／D 確切操作）

本節是提示點名的那份東西：**只交一份**、每一格都給到可以直接貼的命令與可以反駁的數字，不再分輪次散在 §8.7／§9.9／§10.7 三處。前面各節的正文**不改寫**（同一份記錄、不造平行摘要），本節只以其**本輪現測值**取代其中過期的格，並在 §11.9 逐格寫明取代了誰。本節**不是** PG、migration、服務、browser、重釘或 Git 寫入的批准。

### 11.0 本輪受測內容的身份（18:0x 現測；後面每個數字都綁這一段）

| 條目 | 現測值 | 怎麼自己量 |
|---|---|---|
| Git HEAD（fixed base） | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（分支 `live1-impl`） | `git rev-parse HEAD` |
| 髒路徑數／porcelain hash | **218** 路徑／`2b728e47967718e5c8ac243f566334eab419a02e24d3e1f91074d6958ed09b43` | `git status --porcelain \| shasum -a 256`（第 1 欄＝行數） |
| 受測內容摘要（lane 自己的 tree-digest） | `44803d5b32fa1eda8d12715b5f8e7c46707eac6d6d21da5e79b692f0f2eb1715`／**529** 支 `.py` | `find . -name '*.py' … \| LC_ALL=C sort \| xargs shasum -a 256 \| shasum -a 256`；同一值出現在本輪 4 份 `identity.txt` 的 `tree-digest-before` **與** `-after` |
| migration 鏈 | head **`d4e5f6g7h8i9`**、`revision_count=57`、`parsed_heads=["d4e5f6g7h8i9"]`、`expected_tail=[f2a3b4c5d6e7, b2c3d4e5f6g7, c3d4e5f6g7h8, d4e5f6g7h8i9]`、`unreachable_from_head=[]`、`status=OK`、rc 0 | `cd backend && .venv/bin/python scripts/check_migration_tail.py --json --with-alembic` |
| 正在跑的實例 | `pgrep -fl postgres`／`uvicorn`／`next dev\|next-server` **三條皆無輸出**；`5432 5433 8000 8101 3000 3101` 六個埠的 `LISTEN` 計數**全 0**；`/tmp/.s.PGSQL.*` **不存在** | §9.8 A0-5／A0-6／A0-7 同式（本輪 18:0x 重測） |
| `backend/test.db`（**不刪、也不引用為證據**） | `3,043,328 bytes`／mtime `2026-11-20T11:07:34+0800` ⇒ 與 §9.8 A0-9 同值 ⇒ 第十四輪那条預設路徑修復**仍生效** | `stat -f "%z bytes mtime=%Sm %N" backend/test.db` |
| `backend/.env` | **13** 個 `KEY=` 行、已被 `.gitignore:14` 忽略（**只列鍵數，未讀值、未印值**） | `grep -cE '^[A-Z_]+=' backend/.env; git check-ignore -v backend/.env` |
| PG 二進位來源 | `$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin`：**32** 支、`postgres (PostgreSQL) 16.2` | §9.8 A0-8 同式 |
| pg 層驅動 | `backend/.venv` 可 `import psycopg2` ⇒ **2.9.11 (dt dec pq3 ext lo64)**、`sqlalchemy 2.0.36`；`pg8000` **缺席**（`ModuleNotFoundError`）⇒ DSN 必須用 `postgresql+psycopg2://` 或裸 `postgresql://` | `.venv/bin/python -c "import psycopg2, sqlalchemy; …"` |
| 瀏覽器可用性（**更正 §9.11 的「环境失敗」預設**） | `/Applications/Google Chrome.app` **存在**；`~/Library/Caches/ms-playwright` **0 條**；`@playwright/test` **1.64.0**（`node_modules/.bin/playwright` 在）⇒ 只有 `E2E_BROWSER_CHANNEL=chrome` 這條路，bundled chromium 需下載 | `ls -d "/Applications/Google Chrome.app"; ls ~/Library/Caches/ms-playwright \| wc -l` |

**與 §10.0 的差異**：`218` 路徑（第十五輪是 214）、digest `44803d5b…`（第十五輪 `162d0be4…`）。差的就是本輪那 4 支新檔＋2 支產品檔的改動本身，不是干擾因子。

**本輪 run dir → 它自己那棵樹（`identity.txt` 逐份讀回，不是推斷）**：一個數字屬於哪個 digest，決定它能不能算在上面的固定候選上，所以逐格分開。

| run dir | `tree-digest-before`＝`-after` | 綁得上的節點 | 绑不上時本節怎麼辦 |
|---|---|---|---|
| `…092011Z-38924` | `2c708ecb…` | 只算**重現過程**（rc 1 的原始錯誤），其檔已改寫為 `test_decision_conflict_repro.py` | §11.2 第一步只引它的逐字錯誤，不引它做任何 verdict |
| `…092513Z-40759` | `b0149272…` | 6 衝突節點（**已被 `…61246` 取代**，見下） | 保留為「同一組節點在改動前的第一次量測」 |
| `…092606Z-41054` | `b0149272…` | 旅程批次 39 支檔 **579／522／57／rc 0** | **該量測之後**才寫入 `app/services/daily_estimates.py`（17:33:41，未追蹤產品檔）⇒ 樹由 `b0149272…` 變成 `44803d5b…`。本節不為新數字重跑整套（提示原文），改为把**受這次改動直接影響的那一支**在當前樹重驗：`…101927Z-61446` ＝ `tests/test_daily_model_estimate.py` **48 collected／48 passed／rc 0／digest 前後同為 `44803d5b…`／evidence gate closes** |
| `…101907Z-61246` | `44803d5b…` | 衝突修復 **6 節點／6 passed／rc 0／evidence gate closes**（§11.2 第二步現採這一份） | — |
| `…101927Z-61446` | `44803d5b…` | 每日估計 **48／48／rc 0**（`daily_estimates.py` 改動的直接覆蓋） | — |
| `…095620Z-51823`／`…095707Z-52216` | `44803d5b…` | dotenv 順序探測的控制組與實測組（各 59／59／rc 0） | — |
| `…100518Z-55489`／`…100641Z-56129` | `44803d5b…` | A-6/A-7 的 6 支 PG node ID 在 sqlite 層的 2 passed／4 skipped；kill switch 5 節點 | — |

**後測（18:1x，重跑上面兩支之後）**：`git rev-parse HEAD`＝`5a34e3f`、`git status --porcelain`＝**218** 行／`2b728e47…`、`.py` 樹 digest＝`44803d5b…`——三個值與本節開頭**逐字相同**，即這兩次 lane 執行沒有動到受測內容（run dir 與 `__pycache__` 都在樹外／gitignored）。

### 11.1 本輪的實際 diff（**代碼正文**，不是「已修」兩個字）

**（a）kill switch——放在 HTTP 與 worker 都必經的那個函式裡**（`backend/app/api/personal_models.py`，本輪 17:03 寫入）

```python
# personal_models.py:570
def _refuse_when_switched_off(db: Session) -> None:
    """Stop an inference the owner or operator switched off, before anything is spent.

    ``inference`` answers True for the ``global`` row too, and a switch that
    cannot be read is treated as on. The check sits in the shared runner rather
    than on each route because the daily estimate worker calls this runner
    directly, where no HTTP dependency runs at all.
    """
    try:
        active = is_kill_switch_active(db, KillSwitchScope.inference)
        code = "kill_switch_active"
    except Exception:
        active, code = True, "kill_switch_unreadable"
    if active:
        raise HTTPException(
            status_code=503,
            detail={"code": code,
                    "message_zh": "推論緊急開關已開啟或無法讀取：未執行推論，也未動用額度。"},
        )
```

呼叫點只有一個，在共用 runner 的第一行：`personal_models.py:610` `_refuse_when_switched_off(db)`（`_run_personal_prediction` 於 `:593` 宣告）。三條進入这条路徑的門因此**共用同一道閘**，而不是各自掛 decorator：

| 門 | 位置 | 它傳進去的身分 |
|---|---|---|
| `POST /models/{version_id}/predict` | 路由 `:768`／def `:769`／呼叫 `:784` | `via=_via(request)`（`_via` 在 `:133`） |
| `POST /records/{record_id}/predict` | 路由 `:790`／def `:791`／呼叫 `:838` | `via=_via(request)` |
| 每日估計 worker（**沒有 `Request`**） | `app/services/daily_estimates.py:803` | `via=DAILY_SERVICE_VIA`（`:812`）、`resolved_from="daily_estimate_selection"`（`:814`） |

本輪同時把 runner 的參數形狀改了：`request: Request` → **`via: str`**、`alias_used: bool` → **`resolved_from: str`**。這是提示要的「worker 形狀」的具體意思：**一個沒有 HTTP Request 的呼叫者，物理上進得了同一個閘**。計費、模型解析政策、每日去重、execution 開關——**一行未動**（`git diff --numstat HEAD` 對本檔是**累計** `212/22`，含第十三～十五輪；本輪增量就是上面這三段）。

**（b）衝突處理——先重現，再定性，只在真的那一格修**（`backend/app/api/paper_portfolio.py`，本輪 17:23 寫入）

```python
# paper_portfolio.py:217
def require_session_for_owner(db: Session, owner_id: UUID, session_id: UUID) -> None:
    """Refuse a replay session this owner may not name, before anything is written.

    ``simulation_sessions.id`` is a real foreign key on both ``paper_portfolios``
    and ``decision_records``, so an id nobody checked turns the insert into a
    database error on PostgreSQL. Stated once because two doors — opening an
    account and recording a decision — accept the same column, and a rule only
    one of them holds lets the same id be a link here and a 500 there.
    """
    named = (db.query(SimulationSession.id)
             .filter(SimulationSession.id == session_id,
                     SimulationSession.owner_id == owner_id).first())
    if named is None:
        raise HTTPException(status_code=404, detail="Simulation session not found for this user")
```

呼叫點：`paper_portfolio.py:563`（開帳號那扇門，早就有）與 `paper_portfolio.py:1057-1058`（**本輪補的那扇門**——`create_decision` 在 `DecisionRecord(...)` 建行（`:1059`）**之前**）。本輪之前，該欄是**直接進 insert** 的。

`grep -n require_session_for_owner EXECUTION_TABLE_20261117.md` 在第十三～十五輪**零命中**——即本節是它第一次入帳，不是補寫舊宣稱。

**（c）「衝突」那一格本輪沒有動**，因為讀到的東西不允許我動：`backend/app/api/simulations.py` 的兩段 `IntegrityError` 處理（`app/api/simulations.py:2239-2257` flush 處、`:2651-2664` commit 處，兩者都是 `db.rollback()` ＋ 409 具名 detail）是**第十三輪 16:33 的寫入**（檔案 mtime `2026-11-19T16:33:49`，且 §8.x 已入帳）。本輪只重讀、只把它們納入回歸（見 §11.2），**不把它算成本輪 diff**。

**（d）本輪新增／改寫的測試與 spec**（4 支全部未追蹤 `??`；sha256 於 18:1x 現測）

| 檔 | 行／位元組 | sha256（前 16） | 內容 |
|---|---|---|---|
| `backend/tests/test_predict_kill_switch_boundary.py` | 279／10,690 | `1652491795b1bd3c` | 5 節點（§11.3） |
| `backend/tests/test_decision_conflict_repro.py` | 301／11,168 | `3bba6efe496532fd` | 6 節點（§11.2） |
| `frontend/e2e/manual-decision-lineage.spec.ts` | 368／21,480 | `d9e4d062f7c412b6` | 本輪改寫：血緣走 `buy`、三層讀回、匯出用真封包 |
| `frontend/e2e/daily-estimate-journey.spec.ts` | 448／23,216 | `4c06cc594e3b8285` | 本輪改寫：計費文案改按**持久業務鍵**（§11.3b） |

依賴：本輪**無新套件、無新設定鍵、無 migration、無 `app/config.py` 改動**。

### 11.2 R16-2：`POST /{portfolio_id}/decisions` 的「約束衝突」——**先重現，才定性**（提示原話）

**第一步：最小合法 request 重現。** run dir `sqlite-20261120T092011Z-38924`（`exit-code.txt`＝**1**，`collected-node-ids: 1`，`selection-kind: explicit-selection`）：一筆欄位齐全、只把 `simulation_session_id` 填成**不存在**的 uuid 的請求，實測得到

```
E   sqlite3.IntegrityError: FOREIGN KEY constraint failed
E   sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) FOREIGN KEY constraint failed
ERROR    app.request_errors:request_context.py:102 request failed code=internal_error request_id=6660addffb894993a1e488f2e78d33f7
```

⇒ 原宣稱「可達 500」**成立，但只在這一個欄位、這一種原因上成立**。（該探測檔是過程用的暫存檔，本輪已將它改寫成下面那支會留帳的 `test_decision_conflict_repro.py`；`run_tests.sh` 對暫存檔的執行**不構成完成證據**。）

**第二步：逐格定性，把不成立的那半撤回。** 同一支檔的 6 個節點，本節採**當前候選**上那份：run dir `sqlite-20261120T101907Z-61246`（`collected-node-ids: 6`／`6 passed`／rc 0／`evidence gate: ledger closes`／`tree-digest-before`＝`-after`＝`44803d5b…`）。同一組節點在 17:25 也跑過一次（`…092513Z-40759`，樹 `b0149272…`，同為 `6 passed`／rc 0），兩者結論相同；差的那 8 分鐘裡動過的是 `app/services/daily_estimates.py`，不是這扇決策門——但既然候選以 `44803d5b…` 為準，本節的判語只綁 `…61246`：

| 節點 | 量到什麼 | 判語 |
|---|---|---|
| `test_repeated_identical_body_records_two_decisions` | 同一份合法 body 連發兩次 → **兩次都是 201、兩列決策** | **「重複提交會撞 UNIQUE 變 500」這半撤回**：`decision_records` 唯一的唯一約束是 `uq_decision_records_source_per_portfolio`＝`(paper_portfolio_id, source_trade_id, decision_type)`（`app/models/paper_portfolio.py:240`），而这扇門**永不填 `source_trade_id`**，NULL 不碰撞 |
| `test_missing_session_refused_with_zero_writes` | 不存在的 session id → **404 具名拒絕**（修復後） | 本輪修的正是這格 |
| `test_missing_session_never_reaches_the_database_key` | 在**強制 FK** 的連線下，拒絕發生在 insert **之前**（数据库從未被問過那個鍵） | 證明修的是位置，不是狀態碼妝 |
| `test_another_owners_session_refused_with_zero_writes` | 別人的 session → 404、零寫入 | 取得權與存在性同一句 SQL 解 |
| `test_owners_own_session_still_records_and_reads_back` | 本人自己的 session → 照樣建行並讀回 | **不弱化**：合法請求未被新增的檢查擋掉 |
| `test_account_opening_door_refuses_the_same_ids` | 開帳號那扇門（`:563`）對同一組 id 給同一個拒絕 | 兩扇門同一契約，不是一邊有護欄一邊沒有 |

**揭露（不可省略）**：`PRAGMA foreign_keys` 在本 lane 是 **OFF**（`tests/conftest.py:216`），所以 sqlite 層「填個不存在的 id」是**靜靜存下一列指向虛空的決策**，不是錯誤；`500` 是 **PostgreSQL 上的永恆狀態**（FK 為真：`migrations/versions/55a8d07a7f40_initial_schema.py:583`）。第二格與第四格因此**額外需要 enforced-FK 前提**才有意義——這就是為什麼上表有 `enforced_foreign_keys` 參數的節點不能省的。

**沒做的事（提示明文禁止，本輪遵守）**：**沒有**把所有 `IntegrityError` 一概吞成 409（`:2242`／`:2651` 那兩段只認 idempotency 唯一索引，且各帶自己的具名 detail）；**沒有**新建 idempotency 平台；**沒有**拿「無法重現」遮掉可重現的那半，也沒有拿可重現的那半誇大整扇門。

**還未證明的**：本節所有 verdict 都是 **⑤-HTTP in-process＋sqlite** 層。PG 上的同一組判準＝`alethra_owner_test` 未建 ⇒ **NOT RUN**（§11.6 A 卡）。

### 11.3 R16-1／R16-3：kill switch 邊界與計費文案（本輪量完的兩格）

**（a）`backend/tests/test_predict_kill_switch_boundary.py`＝5 節點**，run dir `sqlite-20261120T100641Z-56129`：`collected 5 / 5 passed / rc 0 / evidence gate closes`。

| 節點 | 斷言 |
|---|---|
| `test_the_inference_switch_refuses_both_entries_and_spends_nothing` | `inference` 開著 ⇒ **兩個** predict 入口都 503，且**零推論、零 `usage_records`、零 `quota_reservations`（任何狀態）、`ai_usage` 計數不動** |
| `test_the_global_switch_covers_both_entries_by_inheritance` | `global` 那列照樣擋（`is_kill_switch_active` 的继承語意） |
| `test_a_switch_that_cannot_be_read_refuses_rather_than_infers` | 開關**讀不到**時也拒，且 code 是 `kill_switch_unreadable`（`personal_models.py:582`）——**讀不到的煞車不是推論許可** |
| `test_the_worker_door_passes_the_same_line_as_the_button` | 直接以「db＋ids、無 HTTP」的形狀呼叫 runner ⇒ 同一道 503（證明閘不在 decorator 上） |
| `test_the_execution_switch_still_leaves_a_pure_estimate_alone` | `execution` 開關**不擋純估計**；正常情境保留原行為（逐字回答哪個 version、`resolved_from` 是什麼） |

**它不證明什麼（本檔自己寫在 docstring，這裡不美化）**：训练好的 version 來自 `personal_env` fixture（它**直接**寫 artifact 與一列 "completed" 的 training job），bar 是合成的；所以這 5 格證的是**閘的位置與代價**，不證「真實訓練出來的模型在真實行情下會被同樣擋掉」，也不證服務進程層。

**（b）計費文案改按持久業務鍵**（提示：「新成功項目計費，沿用不計費，失敗釋放，按持久業務鍵而非 click 數對帳」）。第十五輪我在 spec 裡寫的「一次點擊一席位」**兩個方向都錯**，本輪已改寫並在 `frontend/e2e/daily-estimate-journey.spec.ts:35-53` 留了撤回說明（倉庫產品文案**从未**出現過「席位」二字：`grep -rn "席位" frontend/src backend/app` → 0 命中，唯一命中就是該 spec 的撤回註解本身——**所以這格是修我的文案，不是修產品的文案**）。三種情形逐條對回代碼：

1. **新成功項目計費**：`daily_estimates.py:803` → `personal_models.py:593` → `reserve_quota`（`app/auth/entitlements.py:252`，呼叫點 `personal_models.py:656`）→ `settle_reservation`（`entitlements.py:357`，呼叫點 `:688`）與計費列**同一個 commit**。
2. **沿用不計費**：`claim_estimate`（`daily_estimates.py:509`）對鍵 `f"{owner_id}|{skill_unit_id}|{instrument_id}|{window}"`（`:542`）回 `True` ⇒ tick 記 `already_estimated_this_window` 後 `continue`（`:1010-1023`）⇒ **第二次按钮被受理但零扣額**。
3. **失敗釋放**：`release_reservation`（`entitlements.py:452`）在 `personal_models.py:660`／`:670`／`:674`／`:678` 四個失敗分支上，錯誤浮出來**之前**已歸還。

⇒ 對帳單位是 **(owner, skill account, instrument, UTC day) 的計費列**，不是點擊數。spec 的讀回也照這個單位做（DOM 文字＋送出的 request＋response＋獨立唯讀 oracle 四件一齊）。

**（c）R16-5（上一節已完成，本輪只補最終核對）**：兩支 spec 本輪重跑 `tsc --noEmit --strict` → **rc 0**（18:1x 現測，對**當前** 368／448 行內容）；面板契約離線層 `tests/dailyEstimate.test.mjs` 43 條仍綠（第十五輪實測）。**未執行 `playwright test`**（需服務＋瀏覽器，未批）。

### 11.4 R16-4：dotenv 隔離修復的**初始化順序核對**（提示點名：「核對它發生於 app／engine／cached settings 初始化之前」）

**方法**：一個 `sys.meta_path` passthrough finder（`${TMPDIR:-/tmp}alethra-runs/r164-orderprobe/orderprobe.py`，本輪自製、**在倉庫外**），記錄 `app.config`／`app.database`／`app.main` **第一次被要求 import** 那一刻的 `Settings.model_config["env_file"]` 與 `get_settings` 快取大小，於 `pytest_configure` 落盤。以正式 lane 執行，只多掛一個 plugin：

| 對照 | 命令 | 結果 |
|---|---|---|
| 控制組（不掛 plugin） | `./scripts/run_tests.sh sqlite tests/test_config.py` | run dir `sqlite-20261120T095620Z-51823`：`collected 59 / 59 passed / rc 0` |
| 實測組 | `./scripts/run_tests.sh sqlite -p orderprobe tests/test_config.py` | run dir `sqlite-20261120T095707Z-52216`：`collected 59 / 59 passed / rc 0` |

兩份 `identity.txt` 的 `tree-digest-before` 與 `-after` **同為 `44803d5b…`**（加 plugin 沒動樹）。**探測結果逐字**（`r164-orderprobe/order.jsonl`）：

```
{"kind": "plugin_loaded_before_conftest", "module": "orderprobe", "app_config_imported": false}
{"kind": "first_import_request", "module": "app.config", "app_config_imported": false}
{"kind": "first_import_request", "module": "app.database", "app_config_imported": true, "env_file": "None", "settings_cache_size": 0}
{"kind": "first_import_request", "module": "app.main", "app_config_imported": true, "env_file": "None", "settings_cache_size": 1}
{"kind": "pytest_configure", "module": "conftest already executed", "app_config_imported": true, "env_file": "None", "settings_cache_size": 1}
```

**這段讀什麼（只宣稱它能宣稱的）**：
- `app.config` 的首次 import 請求發生在 `conftest.py:91` 之前（`app_config_imported` 那時仍 `false`），所以 `:93` 的 `Settings.model_config["env_file"] = None` 與 `:96` 的 `cache_clear()` 作用的是一個**已匯入、尚未快照**的類別。
- 轮到 `app.database` 被要求 import 時，`env_file` **已是 `None`**、`get_settings` 快取 `currsize=0` ⇒ `app/database.py:8`（讀 settings）與 `:19`（`create_engine`）**在編譯引擎時不可能看到那份 dotenv**。這就是提示要的「先於 engine」。
- 轮到 `app.main` 時 `env_file=None`、快取 **1** ⇒ `app/main.py:13` 那份 import 期快照取到的是**修補之後**的值；`pytest_configure` 仍 `None`（沒有人把它改回去）。
- 這三行是**執行期觀測**，不是我對 import 順序的推測；`conftest.py:128` 那行「先匯入 app.main」的目的（讓 route 抓到同一個 `get_db` 物件）也因此順帶被觀察到成立。

**正控與覆寫仍然可用（白名單非秘密值；未印完整環境、未讀任何憑證）**——`${TMPDIR}alethra-runs/r164-orderprobe/settings_probe.py`，18:0x 現測逐字：

```
cwd=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend
model_config env_file (before patch)='.env'
.env present in cwd=True
Settings() unpatched: data_provider='twelvedata' live_trading_enabled=True research_auth_bypass_enabled=True
patched: env_file '.env' -> None; cache=CacheInfo(hits=0, misses=0, maxsize=128, currsize=0)
Settings() patched: data_provider='demo' live_trading_enabled=False research_auth_bypass_enabled=False
Settings(_env_file='.env') patched: data_provider='twelvedata' live_trading_enabled=True research_auth_bypass_enabled=True
DATA_PROVIDER exported, _env_file='.env': data_provider='env_probe_wins' live_trading_enabled=True research_auth_bypass_enabled=True
DATA_PROVIDER exported, patched Settings(): data_provider='env_probe_wins' live_trading_enabled=False research_auth_bypass_enabled=False
get_settings() identity: same object=True data_provider='demo'
dotenv keys the file would have injected: 58 fields
```

四件事各自對應一個疑慮：①**修復前**該檔確實重配進程（`twelvedata/True/True`）；②修復後回到碼內預設（`demo/False/False`）；③**顯式 `_env_file` 仍然生效**（第 6 行）——所以這條修復不是「讓 pydantic 讀不到 dotenv」那種副作用，需要該檔的實驗仍可自己做；④**環境變數仍然贏過檔案**（第 7 行 `env_probe_wins`），且 `get_settings()` 是同一物件的快取快照（第 9 行）——生產用環境變數設定的路完全沒被擋。可被該檔注入口徑共 **58** 個 `Settings` 欄位（第 10 行）。

**⚠️ 這批命令不是唯讀（提示：「不將會寫 run dir、DB、cache 或 build metadata 的命令標為唯讀」）**：兩次 lane 執行各自建了一個 run dir（`…51823`／`…52216`，含 `collect.txt`／`junit.xml`／`pytest.log`／`identity.txt`／`exit-code.txt`）與一支 `${TMPDIR}alethra-sqlite-test-<pid>.db`（第 11.0 行那條 per-pid 預設）；`settings_probe.py` 以 `PYTHONPATH=<backend>` 執行時會刷新 `backend/**/__pycache__`（gitignored）。**全部保留未刪**，樹本身未動（digest 前後同值）。

**測試修復不證明服務進程安全——本輪把這句話量成實物（新發現，影響 C 卡）**

`tests/conftest.py:93-96` 只活在 pytest 行程裡。驗收用的服務進程由 `start_candidate_stack.sh` 啟動，而它：
- 在 `export_process_env()` 內只匯入白名單（`:182` `JWT_SECRET_KEY`、`:183` `APP_ENV`、`:184` `TRAINING_WORKER_HEARTBEAT_PATH`、`:185` `CORS_ORIGINS`、`:186-188` `CANDIDATE_DB_*`／`DATABASE_URL`、`:198-204` `CANDIDATE_PASSWORDLESS_*`、`:212-215` `AGENT_STRATEGY_*`）；
- `grep -n "DATA_PROVIDER\|LIVE_TRADING\|RESEARCH_AUTH" start_candidate_stack.sh` ⇒ **0 命中**；
- 起服務的方式是 `:396` `cd backend` ＋ `:397` `nohup "$PYTHON" -m uvicorn app.main:app …` ⇒ **cwd 在 `backend/`**，正是 §10.7 那條「相對 `env_file` 以行程 cwd 解析」。

⇒ 該服務進程**會**讀 `backend/.env`，有效值就是上面量到的三個。三者各自的真實後果（不誇、不縮）：

| 鍵 | `.env` 有效值 | 消費者（實測） | 後果 |
|---|---|---|---|
| `DATA_PROVIDER` | `twelvedata` | `app/providers/provider_registry.py:142`（全樹 `settings.data_provider` 僅 3 處） | 那個程序**會**拿 `.env` 裡的 `TWELVEDATA_API_KEY` 去呼叫外部行情 API ⇒ **零外呼**的保證在服務層不存在，只能靠**明釘** |
| `RESEARCH_AUTH_BYPASS_ENABLED` | `true` | `app/auth/jwt.py:51`（`is_loopback_host(peer)` ⇒ 直接以**第一位 active user** 的身分放行，`auth_amr=["bypass"]`） | 驗收棧的鑑權**不是**產品鑑權；本輪的「本人操作」分格因此在该棧上語意要打折 |
| `LIVE_TRADING_ENABLED` | `true` | **全樹只有宣告處 `app/config.py:172`，無任何消費者**（`grep -rn "live_trading_enabled\|LIVE_TRADING_ENABLED"` 排除 `.venv` 後只剩 config 與測試） | ⇒ **不得**宣稱它武裝了任何 daemon。它是一個假把手（`tests/test_research_run_recovery.py:298` 已記載它如何變成假把手），本輪只重述實測：0 個消費者 |

**因此 C 卡（§11.8）把這三個值明釘**，而不是「假設 `backend/.env` 沒人動」。這是本輪對第十五輪 C 卡唯一實質的加固。

### 11.5 六層狀態（各自分開，不互換；未批准釘選維持未批准）

| 層 | 本輪狀態 | 具體實物 |
|---|---|---|
| **pytest（離線讀碼／unit）** | ✅ 本輪 4 支新檔全部可收集、指定選集全綠 | §11.2／§11.3 三份 run dir |
| **正式 sqlite lane（批次 C：旅程相關 39 支檔）** | ✅ **579 collected / 522 passed / 57 skipped / 0 failed / rc 0**，`evidence gate: ledger closes` | `sqlite-20261120T092606Z-41054`（427.90s；`DATA_PROVIDER=<unset>`；`selection-kind: explicit-selection` ⇒ **不是整套**）。⚠️ 該 run 的樹是 `b0149272…`；其後唯一改動的產品檔是 `app/services/daily_estimates.py`（17:33:41），其**直接覆蓋**已在當前樹重驗：`…101927Z-61446`＝`48 collected / 48 passed / rc 0 / digest 44803d5b…`。為一個新數字重跑整套＝提示明文禁止，本輪不做 |
| **PostgreSQL** | ❌ **NOT RUN**（本輪 18:0x 現測：無進程、六埠全 0、無 socket、無 PGDATA） | A 卡（§11.6）待批 |
| **瀏覽器** | ❌ **NOT RUN**（未起服務、未開瀏覽器）；本輪只能交 rc 0 的型別檢查與「Chrome.app 存在／bundled chromium 缺席」這組可用性事實 | C 卡（§11.8）待批 |
| **自然 cron** | ❌ **NOT RUN**（無服務進程；`ENABLE_LEARNING_SCHEDULER` 預設 False 未改） | B 卡（§11.7）待批 |
| **釘選（`collected_nodes.txt`／`skip_budget.json`）** | ⚠️ **維持未批准、未變動**：`tests/skip_budget.json` 仍是 `collected=3811 total=179 pg_only=159 live_stack=18 other=2`（18:0x 由 lane 自己讀回印在 `…55489/pytest.log`），`collected_nodes.txt` 未出現在本輪寫入清單（§11.11） | **本輪未重釘**；整套閘對 `44803d5b…` 這棵樹依然會拒絕 ⇒ 任何「整套 PASS」的說法在本輪**不存在** |

### 11.6 執行附件 A（PostgreSQL：**新隔離目標、角色最小權限、完整鏈、確切 node IDs、第一個寫入前核對、資源上限、失敗停止、正常停止**）

**固定候選**：`5a34e3f` ＋ tree digest `44803d5b…`（§11.0）。**新隔離目標**（不重用、不連接任何既有／受保護實例；本機無 PG 在跑，故這是一台全新叢集）：

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
PGB=$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin
RUN=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r16-pg-XXXXXX")      # 只寫這一個新目錄
export PGDATA="${TMPDIR:-/tmp}/alethra-r16-pgdata"             # A0-7 現測：不存在
export SOCKDIR="${TMPDIR:-/tmp}/alethra-r16-sock"              # 同上
```

| 步 | 確切操作（一個步＝一個可單獨否決的動作） | 停止條件 |
|---|---|---|
| **A-0 第一個寫入前的身份核對**（先跑，未過不得進 A-1） | `pgrep -fl postgres; ls -d "$PGDATA" "$SOCKDIR" /tmp/.s.PGSQL.* ; for p in 5432 5433; do lsof -nP -iTCP:$p -sTCP:LISTEN\|tail -n +2\|wc -l; done; git rev-parse HEAD; git status --porcelain\|shasum -a 256` | **五項必須逐字等於 §11.0**：HEAD `5a34e3f…`、porcelain `218`／`2b728e47…`、三條 `no matches`／`No such file`、兩埠 `0`、無 postgres pid。任一不符 ⇒ **停並回報**，不建任何东西、不 kill、不覆寫 |
| **A-1 建叢集** | `"$PGB/initdb" -D "$PGDATA" --auth-local=trust --auth-host=scram-sha-256 -E UTF8 --no-sync` | 非零 ⇒ 停，**不重試、不刪 `PGDATA`**（那是现场）；`PGDATA` 已存在 ⇒ 停（A-0 已擋） |
| **A-2 啟動＋資源上限** | `"$PGB/pg_ctl" -D "$PGDATA" -o "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB" -l "$RUN/pg.log" start && "$PGB/pg_isready" -h 127.0.0.1 -p 5432` | `pg_isready` 非零 ⇒ 停並保留 `$RUN/pg.log`；**上限就這兩個數**（25／64MB），本包不放大；**只聆聽 127.0.0.1** |
| **A-3 建 DB＋角色（最小權限）** | 見下方 SQL 块 | 四旗標任一不為 `f`、或 `current_database()` 不為 `alethra_owner_test`、或閘 rc=3 ⇒ 停，**不放寬安全閘、不改庫名／埠** |
| **A-4 空庫升全鏈** | `cd backend && .venv/bin/python -m alembic -c alembic.ini upgrade head` | `heads` 多於一／head ≠ `d4e5f6g7h8i9`／`DUPLICATE_REVISION`｜`BRANCHED_TREE`｜`MISSING_DEPENDENCY` ⇒ 停；**不 autogen、不 downgrade、不 drop、不跑其他 migration** |
| **A-5 schema 讀回（唯讀）** | `psql <DSN> -c '\dn'`＋`SELECT current_database(), version();`＋查 `information_schema.columns`／`pg_indexes` 中兩張表的 `linked_inference_id`（決策表與 `human_decision_events`） | 出現 FK／型別非 `uuid` ⇒ 停並回報「本體與 migration 不同步」，差異逐條列，**不解釋成等價** |
| **A-6／A-7 六支 node id** | 見下方逐字清單，`./scripts/run_tests.sh pg <id…>` | 見 §11.6c 判準；跳過＝**NOT RUN**，不是通過 |

**A-3 SQL（逐字；兩個角色，DDL 與 DML 分家）**

```sql
-- 遷移者：擁有該庫，只做這個庫的 DDL。四旗標必須全 f（test_safety_gate.py:110-131 查的就是這四個）。
CREATE ROLE alethra_r16_migrator LOGIN PASSWORD '<openssl rand -hex 12，寫入 $RUN/roles.sql(600)，不印、不進命令列歷史>';
CREATE DATABASE alethra_owner_test OWNER alethra_r16_migrator;
\c alethra_owner_test
REVOKE ALL ON SCHEMA public FROM PUBLIC;          -- PG15+ 預設已收；這句是幂等保護
GRANT USAGE, CREATE ON SCHEMA public TO alethra_r16_migrator;   -- alembic 要 CREATE

-- 應用／服務：只有 DML＋序列，沒有 DDL、沒有 TRUNCATE 以外的権限。
CREATE ROLE alethra_r16_app LOGIN PASSWORD '<同上，另一支隨機值>';
GRANT CONNECT, TEMP ON DATABASE alethra_owner_test TO alethra_r16_app;
GRANT USAGE ON SCHEMA public TO alethra_r16_app;
ALTER DEFAULT PRIVILEGES FOR ROLE alethra_r16_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO alethra_r16_app;
ALTER DEFAULT PRIVILEGES FOR ROLE alethra_r16_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO alethra_r16_app;
```

⚠️ 關於 `REVOKE ALL ON SCHEMA public FROM PUBLIC`：PG 16.2 是現測（§11.0），但「沒有這句 alembic 會落不了地」這件事**在本機從未驗證過**（這台機器至今沒跑過 PG）⇒ 該句屬 **UNVERIFIED 設計**，不是實測結論；A-4 若因此失敗，停線回報，不要臨場加權。

**A-3 的強制校驗來源（本輪重讀，行號現測）**：庫名白名單 `backend/test_safety_gate.py:24-29`（`alethra_test`／`alethra_owner_test` 都在內）、黑名單 `:32-37`（`ai_investment_lab`／`postgres`／`template0/1`）、實例白名單 `:40-44`（`(localhost|127.0.0.1|::1, 5432)`）、角色四旗標 `:110-131`（superuser／createdb／createrole／bypassrls 任一為真即硬FAIL）。lane 另加一道名前綴：`ALETHRA_TEST_REQUIRED_DB_PREFIX` 預設 `alethra_`（`scripts/run_tests.sh:178`）⇒ `alethra_owner_test` 同時過兩道。

**A-6／A-7 確切 node IDs（6 支；本輪 18:05 在當前樹上以 sqlite 層實地收集過：`collected 6`、`2 passed / 4 skipped`、rc 0，run dir `sqlite-20261120T100518Z-55489`）**

```
tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier
tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once
tests/test_daily_model_estimate.py::test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_two_requests_on_one_idempotency_key_leave_exactly_one_order
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_stale_account_read_cannot_overwrite_a_committed_booking
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_manual_submit_racing_an_advance_stays_consistent
```

本輪讀到的**逐字跳過理由**（ junit.xml，即「PG 那格還欠什麼」的定義）：
- 第 2 支：`the seat being raced is pg_advisory_xact_lock, which SQLite has no way to take; …`（分類 `other=1`）
- 第 4–6 支：`row-lock serialisation requires a real PostgreSQL backend`（分類 `pg_only=3`）
- 第 1、3 支在 sqlite 層 `passed`（它們證的是決定性的雙交易交錯，不是真鎖）⇒ **PG 層要證的是那 4 支具名跳過的**。

**PG 層執行形式（顯式節點，不需要先放寬整套選集閘——第二類閘，跑完要對得起帳本）**

```bash
cd backend
export ALETHRA_PG_TEST_URL="postgresql+psycopg2://alethra_r16_migrator:<讀 $RUN，不印>@127.0.0.1:5432/alethra_owner_test"
export TEST_MODE=true DATA_PROVIDER=demo ALETHRA_RACE_CAPTURE_JSON="$RUN/race-capture.json"
./scripts/run_tests.sh pg <該格 node id…>; echo "lane rc=$?"
```
`run_tests.sh:170-173` 在 `ALETHRA_PG_TEST_URL` 空時 rc 2（`refusing: pg layer needs ALETHRA_PG_TEST_URL…`），`:174-177` 把它分發給四個別名，`:179` 自己匯入 `TEST_MODE`，`:183-187` preflight 不過 rc 3，`:190` 之後才跑安全閘——**該 URL 從不-printed**（`:38`／`:431` 那兩行只印層名與 provider）。

**判準（不得以更便宜的東西代替）**：帳本 **row count** 為準——每 `(owner, skill, instrument, window)` 恰 1 列 `metadata["via"]=="daily_service"`（常量 `app/services/daily_estimates.py:77`）、每列恰 1 筆 `settled` 預留、非終態預留＝0、落敗那支 `learning_jobs.result_json.refused` 以 instrument id 具名 `already_estimated_this_window`；兩個 job 摘要的 `billed_in_window` **不相加**。競態判定的 12 條（`observer_visibility`／`wait_observed`／`blocker_is_holder`／`lock_object_matches`／`key_unbilled_while_blocked`／`bill_durable_after_release`／`holder_claim_was_free`／`loser_reread_refused`／`one_bill_per_business_key`／`settlement_matches_bills`／`quota_never_stands_in_place_of_ledger`／`refusal_reason_is_the_ledger`）在 `tests/daily_estimate_race.py:131 assess_race()`，`ALETHRA_RACE_CAPTURE_JSON` 未設時不寫檔、判準照跑（`_write_capture()` **不建父目錄** ⇒ `$RUN` 必須已存在）。timeout 契約：`_TIMEOUT_S = 60`、`lock_timeout='30s'`／`statement_timeout='120s'`／`idle_in_transaction_session_timeout='90s'`。**額度前提：帳號 `ai_usage_limit ≥ 4`**（free plan 預設 10，`app/auth/entitlements.py:70`），**B 卡的 1 不得套在 A-6／A-7**，否則 429（`reserve_quota` `:252`）會把真缺陷蓋成限流。計時未滿、`Barrier` 未湊齊、`proven=false` ⇒ 該次 **NOT RUN／INVALID**，**不得寫成 PG 競態 PASS**。
**停止條件（任何一步命中就停該步，其餘已授權工程照續）**：非零 exit／閘 rc 2–3／head 不符／四旗標不為全 `f`／`current_database()` 不符／跳過取代執行／斷言失敗。停後：**保留 `$RUN`、`$PGDATA`、`$RUN/race-capture.json` 原現場**，不刪、不重試、不改設定碰運氣。
**正常停止命令（先停測試行程，再停叢集；停止 ≠ 刪資料）**

```bash
pgrep -fl pytest | grep -f "$RUN/pids" && kill -TERM <那些 pid>      # 只停本包自己記的 pid
"$PGB/pg_ctl" -D "$PGDATA" -m fast stop
"$PGB/pg_isready" -h 127.0.0.1 -p 5432; echo "expect non-zero"       # 以服務自己的回答證明停了
pgrep -fl postgres; lsof -nP -iTCP:5432 -sTCP:LISTEN | tail -n +2 | wc -l   # 兩條都應為空/0
```
**不得**以已停端點的 HTTP 回應證明停止；**不刪 `$PGDATA`**（留給 owner，或另批清理）。

### 11.7 執行附件 B（自然 cron：**固定一個 DB ＋ 一個設定來源**，不讓 owner 代揀庫名）

| 格 | 固定值（只有一種；SQLite 路線明確排除） |
|---|---|
| **DB** | **`alethra_owner_test`**（同一支，A-3 產出）——庫名由「必須同時過白名單 `test_safety_gate.py:24-29` 與前綴 `run_tests.sh:178`」這個交集決定，本輪已把交集量成兩支候選並選定其一；**不提供二選一** |
| **設定來源（唯一入口）** | 測試側：環境變數 **`ALETHRA_PG_TEST_URL` 一個名字**（`run_tests.sh:170-177` 由它派生其餘三個別名；`conftest.py` 的 `_env_a/_env_b` 一致性守衛只准它兩個同名同值）。服務側：`env -i` ＋白名單匯入（下一列），**不讀 `backend/.env`** |
| **白名單非秘密有效值** | `DATA_PROVIDER=demo`（消費者 `provider_registry.py:142`）、`AI_PROVIDER=mock`、`APP_ENV=development`、`ENABLE_LEARNING_SCHEDULER=true`（`app/config.py:140` 預設 False；唯一讀者 `app/main.py:52`）、`LEARNING_SCHEDULER_JOBS=daily_model_estimate`（預設空＝取 3 支**非計費** lane，`config.py:55-59`／`:148` ⇒ 必須明名一支）、`AUTO_COLLECTION_ENABLED=false`（`config.py:156`）、`RESEARCH_AUTH_BYPASS_ENABLED=false`（**本輪新增釘值**，見 §11.4）、`LIVE_TRADING_ENABLED=false`（**本輪新增釘值**；如實標：無消費者，釘它是為了讓「讀起來像開了」不造成誤導）。`JWT_SECRET_KEY` 隨機產生、寫 `$RUN/jwt-secret.txt`(600)、**不印**；lane 未設時也自己產生 ephemeral key（`run_tests.sh:145-149`） |
| **觸發時機** | `05:00 UTC`（`daily_estimates.py:82`＋`learning_scheduler.py:183-184` 顯式 `CronTrigger(timezone=SERVICE_TIMEZONE)`）；**不改時鐘、不用手動入口冒充 cron** |
| **額度判準** | 服務帳號 `ai_usage_limit=1`（預設 10 ⇒ 必須主動設 1 才有判別力）：第一支 instrument 扣額成功、第二支撞 429，且該次 tick **必頇留一筆具名拒因**而非靜默少寫。**與 A-6／A-7 不共用帳號** |
| **資源上限** | `LEARNING_SCHEDULER_MAX_OWNERS` 維持預設 25（`config.py:153`）；只種 1 owner／1 skill／2 instrument（遠低於 `daily_estimates.py:87-88` 的 10／8）；DB 上限即 A-2 的 25 連線／64MB |
| **零外呼的可核對證據** | `env -i` 讓 demo／mock 成為**唯一**提供者（不是「我保證不叫」）；tick 結束後對服務 log `grep -c 'kraken\|twelvedata\|https?://'` 需＝0，且 `lsof -nP -p <pid> -a -i` 的非 `127.0.0.1`／`::1` 對端列數＝0。⚠️ 「有 validator」不等於安全：`config.py:214-216` 的 `is_production_like` 只認 production／staging，本機 development 下不受保護 |
| **停止方式** | ①過 90 秒仍無 `learning_jobs` 列 ⇒ 停並存 log；②任一 `KillSwitchScope` 為 active（global／training／execution／inference，`app/models/kill_switch.py:10-15`；計費 lane 查 `inference`）⇒ 該 tick **必須**零寫帳，否則就是本卡要抓的缺陷；③啟動日誌出現第二支 lane ⇒ 立即 `stop_learning_scheduler()`（`app/main.py:72`）。收工＝**先停程序再停 DB**，命令同 §11.6 末段，**不刪 PGDATA** |

### 11.8 執行附件 C（瀏覽器：**8101／3101 的 dev 功能驗收模式**；只跑必要兩支 spec；**不代表 production**）

| 格 | 固定值 | 根據（現測／既有實物） |
|---|---|---|
| **模式** | `./start_candidate_stack.sh`（candidate stack），**dev 功能驗收**；結果一律標 **dev**，**不代替 production build 驗收**，也不宣稱等同 | `command -v docker` 不存在 ⇒ `start_acceptance_env.sh` 在碰任何行程之前 rc 2（r13 已實測到那條拒路） |
| **後端** | `uvicorn app.main:app` 於 **127.0.0.1:8101**（腳本 `:396-397` `cd backend` 起） | 見下方「必須明釘的三個值」 |
| **前端** | **`next dev`** 於 **3101**，`NEXT_DEV_DIST_DIR=".next/candidate-3101"`（`:441-446`） | distDir 依埠號隔離 ⇒ 不覆寫那個共用的 `.next` |
| **origin／API base** | 前端 `http://127.0.0.1:3101`；`E2E_FRONTEND_BASE` 同值；API base 由腳本注入 `NEXT_PUBLIC_API_URL=http://127.0.0.1:8101`；`CORS_ORIGINS` 由 `:185` 明設成兩個 origin | `playwright.config.ts:5`／`baseURL :23`；`app/main.py:27` 只讀 `settings.cors_origins_list`（預設 `http://localhost:3000`，`config.py:79`）；`localhost` 與 `127.0.0.1` **不同 origin** |
| **⚠️ 必須明釘的三個值（本輪新發現，缺了就不是同一個現場）** | 起服務**前**在同一個 shell 裡 `export DATA_PROVIDER=demo AI_PROVIDER=mock RESEARCH_AUTH_BYPASS_ENABLED=false`（第三個值在該棧上讓 `app/auth/jwt.py:51` 以「第一位 active user」放行 loopback 請求，會把「本人操作」這格掏空）。`LIVE_TRADING_ENABLED` 可一併設 false，但**如實標：它全樹無消費者**（`app/config.py:172` 是唯一引用），釘它是消歧，不是防範 | §11.4：腳本 `:182-215` 的匯入清單**不含**這三個名字，而 `:396` 把 cwd 放進 `backend/` ⇒ `env_file=".env"` 生效 |
| **只執行這兩支 spec** | `cd frontend && E2E_FRONTEND_BASE=http://127.0.0.1:3101 E2E_BACKEND_BASE=http://127.0.0.1:8101 E2E_BROWSER_CHANNEL=chrome E2E_CRED_FILE=<本機憑證檔> node_modules/.bin/playwright test e2e/manual-decision-lineage.spec.ts e2e/daily-estimate-journey.spec.ts` | **不掃全部 312 支 e2e**。`E2E_BROWSER_CHANNEL=chrome` 現測可用（`/Applications/Google Chrome.app` 存在；`~/Library/Caches/ms-playwright` 0 條 ⇒ bundled chromium 那條路需另批下載）；`@playwright/test` 1.64.0 |
| **spec 自己的前置（跳過就是跳過，會具名）** | `E2E_CRED_FILE`（`e2e/runtime.ts:34`，內含 `E2E_EMAIL`／`E2E_PASSWORD`／可選 base 覆寫；缺 ⇒ 檔案層級 `TIER_B_SKIP`）；`E2E_LINEAGE_ASSET`（預設 `DEMO-ALPHA`，`manual-decision-lineage.spec.ts:69`）；`E2E_DAILY_ESTIMATE_SKILL`（**未設 ⇒ 會扣額那一組跳過**，`daily-estimate-journey.spec.ts:102`） | 這三個名字是「哪一格被誰允許跑」的開關，代理不代填 |
| **注意（本輪讀到的腳本行為）** | `playwright.config.ts:36-40` 的 `webServer.url` 會**探測** `E2E_FRONTEND_BASE`，所以連 `--list` 都需要 3101 已在聽 ⇒ 任何「先列一下」的動作都已经是啟動服務的動作 | 第十五輪 §10.9 末段因此拒過 `--list`；本輪維持 |
| **本人 vs 代理** | **grant／revoke 只能本人按**（`daily-estimate-acknowledge`，`DailyEstimatePanel.tsx:676`）；spec 只斷言按鈕存在與文案正確，**從不按它**。run 可交由有正確 scope 的 agent 在既定範圍內執行（`scope="manual"` 是它與時鐘唯一的差別，`daily_estimates.py:1221`），不因權限差異要求每次按钮都本人簽 | 代理**不替本人同意或撤回**；本卡文字本身不構成同意 |
| **判準** | ①`/health/ready` 通過；②預檢回應含 `access-control-allow-origin: http://127.0.0.1:3101`；③`schema-head` 與 A-4 讀回的 head 同值（腳本 `:575` 會比，不符 `:585` 前就拒起）；④只報「collected／passed／skipped／failed」四個數，**不拿 collected 當 passed**；⑤spec 內任何 `test.skip(true, …)` 的原因文字**逐字入帳**（前置缺件≠通過） | 停線：任一不符 ⇒ 停、存 log、回報；**不重跑、不改設定碰運氣** |

### 11.9 執行附件 D（**實際 patch／新增檔／manifest／歸屬**＋本機隔離重現範圍；**不混重釘／stage／commit／push／部署**）

**（a）本輪寫入＝下面 6 檔，別的都沒動**（`M`＝已追蹤被改，`??`＝本輪新增）

| # | 路徑 | 狀態 | 歸屬（一卡＝一個獨名操作） |
|---|---|---|---|
| 1 | `backend/app/api/personal_models.py` | `M`（vs HEAD 累計 **＋212／−22**；本輪段＝`:570-591`＋`:610`＋`:784`／`:838` 的 `via=`／`resolved_from=` 重接） | **R16-1** kill switch |
| 2 | `backend/app/api/paper_portfolio.py` | `M`（累計 **＋434／−36**；本輪段＝`:217-237` def＋`:1057-1058` 呼叫） | **R16-2** 衝突定性後的修復 |
| 3 | `backend/tests/test_predict_kill_switch_boundary.py` | `??`（279 行／5 節點） | **R16-1** 的證據 |
| 4 | `backend/tests/test_decision_conflict_repro.py` | `??`（301 行／6 節點） | **R16-2** 的證據 |
| 5 | `frontend/e2e/manual-decision-lineage.spec.ts` | `??`（368 行） | **R16-5**（本輪改寫：血緣改 `buy`、三層讀回、匯出用真封包） |
| 6 | `frontend/e2e/daily-estimate-journey.spec.ts` | `??`（448 行） | **R16-3**＋**R16-5**（計費文案改業務鍵） |
| — | `EXECUTION_TABLE_20261117.md` | `M` | **僅附加 §十一**；未改寫 §一～§十任何一節 |

**取代關係（本節換掉前面哪幾格，其餘維持原文）**：§9.9 A1–A5 的**角色名**與 **`$TMPDIR` 路徑名** → §11.6（`alethra_r16_*`／`alethra-r16-*`）；§9.9 的**候選 digest**（`162d0be4…`／527 支）→ §11.0（`44803d5b…`／529 支）；§9.11 C 卡的「bundled chromium 缺席 ⇒ 第一支就 fail」→ §11.8（Chrome.app 現測存在，改走 `channel=chrome`）；§9.10 B 卡的「不匯入 `backend/.env`」→ §11.7（**加**三個明釘值）＋ §11.4（揭露**它其實沒被中和**）。

**（b）manifest（完整性自己可核）**

```bash
cd /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
shasum -a 256 backend/tests/test_predict_kill_switch_boundary.py \
              backend/tests/test_decision_conflict_repro.py \
              frontend/e2e/manual-decision-lineage.spec.ts \
              frontend/e2e/daily-estimate-journey.spec.ts
# 1652491795b1bd3c…  3bba6efe496532fd…  d9e4d062f7c412b6…  4c06cc594e3b8285…（§11.1d 前 16 字）
git diff HEAD -- backend/app/api/personal_models.py backend/app/api/paper_portfolio.py > "$RUN/r16-product.diff"
git diff --numstat HEAD -- backend/app/api/paper_portfolio.py backend/app/api/personal_models.py
```

**（c）本機隔離重現的精確範圍**：在**新目錄**（`mktemp -d`）以**固定本機來源**（HEAD `5a34e3f` ＋ 上表 #1／#2 的 diff ＋ #3–#6 四支原檔）重建，然後 `./scripts/run_tests.sh sqlite tests/test_predict_kill_switch_boundary.py tests/test_decision_conflict_repro.py`，判準＝**5 passed ＋ 6 passed、rc 0**。⚠️ **明文承認**：只靠這 6 檔**無法**重建整套（閉包裡有大量未動過的檔只能從 HEAD 讀，見 §9.2）；隔離重現證的是「本輪增量自證清白」，不是「整套可發布」。**不在此範圍**：重釘 `collected_nodes.txt`／`skip_budget.json`、`git add`／`commit`／`push`、任何部署、套用到共享工作樹之外的地方。發布集合由谁選、怎麼選，仍走 §9.12 D-3（**未批准**）。

### 11.10 逐卡批准表（**放置 ≠ 執行**；引用括號內那句去批，一卡一格）

| 卡 | 可直接引用的批准正文 | 本輪狀態 |
|---|---|---|
| A-0 | 「批准執行 §11.6 的 A-0 五項身份核對（純唯讀，零寫入）」 | 本輪 18:0x **已做**（結果＝§11.0） |
| A-1＋A-2 | 「批准在 `$TMPDIR/alethra-r16-pgdata` 新建並啟動一個 PG 16.2 隔離叢集（僅 127.0.0.1:5432、`max_connections=25`、`shared_buffers=64MB`），並只寫 `$RUN` 一個新目錄」 | 未執行・未批准 |
| A-3 | 「批准建 DB `alethra_owner_test` ＋ 角色 `alethra_r16_migrator`（owner）／`alethra_r16_app`（僅 DML），並讀回四旗標全 `f`」 | 未執行・未批准 |
| A-4 | 「批准在該空庫上跑 `alembic upgrade head`（57 revisions → `d4e5f6g7h8i9`），除此之外不跑任何 migration」 | 未執行・未批准 |
| A-5 | 「批准以 `\d`／`information_schema`／`pg_indexes` 唯讀核對兩張表的 `linked_inference_id`」 | 未執行・未批准 |
| A-6 | 「批准在 pg 層跑 §11.6 那 6 支 id 中的**前 3 支**（帳號 `ai_usage_limit ≥ 4`）」 | 未執行・未批准 |
| A-7 | 「批准在 pg 層跑**後 3 支**（`TestConcurrentLifecycle`）」 | 未執行・未批准 |
| B | 「批准 §11.7 那一格：同一支 `alethra_owner_test`、`env -i`＋白名單（含 `DATA_PROVIDER=demo`／`RESEARCH_AUTH_BYPASS_ENABLED=false`）、單一 lane、額度 1、三個停線條件」 | 未執行・未批准 |
| C-stack | 「批准起 candidate stack（8101／3101，**dev**）並**只**跑 §11.8 那兩支 spec，結果標 dev 不代表 production」 | 未執行・未批准 |
| C-human | 「owner 本人在同一個 dev 棧走一遍（含 grant／revoke 那兩個只有本人能按的钮）」 | 未開始（不需批准，需本人出力） |
| D-repro | 「批准在一個 `mktemp -d` 新目錄以 §11.9(c) 的來源重建並跑那 11 支節點；**不**碰共享工作樹、**不**做任何 Git 寫入」 | 未執行・未批准 |
| （仍然另批） | **重釘**、**stage／commit／push**、**部署** | 本節**未請求**，也不隐含在本包任何一格裡 |

### 11.11 本輪寫入清單與副作用揭露（含「哪些命令不是唯讀」）

| 位置 | 寫入 | 可逆性 |
|---|---|---|
| §11.9(a) 那 6 檔 | 見該表 | 前兩檔 `git checkout -- <path>` 可回 HEAD；後四檔為 `??`（回退＝移出樹，**本輪不做**） |
| `EXECUTION_TABLE_20261117.md` | 僅附加本節 | 僅附加 |
| `${TMPDIR}alethra-runs/` | **本輪新建 14 個 run dir**（17:05–18:20 全部 `sqlite-20261120T…`，現測 `ls` 計數）：`…090546Z-33109`／`…090711Z-34113`／`…090756Z-34486`／`…091229Z-35438`／`…092011Z-38924`／`…092221Z-39656`／`…092513Z-40759`／`…092606Z-41054`／`…095620Z-51823`／`…095707Z-52216`／`…100518Z-55489`／`…100641Z-56129`／`…101907Z-61246`／`…101927Z-61446`。**其中只有 §11.0 對照表點名的 6 份**（`38924`／`41054`／`51823`／`52216`／`55489`／`56129`）＋本輪補測的 `61246`／`61446` 被本節引用；`33109`／`34113`／`34486`／`35438`／`39656`／`40759` 是過程探測與被取代的第一次量測，**不當完成證據**（它們的 `tree-digest-before` 分屬 `575b927…`／`c04bbee…`／`4ee6305…`／`b0149272…`，都不是本節候選） | 保留未刪 |
| `${TMPDIR}alethra-sqlite-test-<pid>.db` | 每個 lane run 一支（per-pid 預設，§9.4 那個修復的形状） | 保留未刪；**未碰 `backend/test.db`**（§11.0 讀回同值） |
| `${TMPDIR}alethra-runs/r164-orderprobe/` | 本輪自製 3 支（`orderprobe.py`／`settings_probe.py`／`order.jsonl`）＋一個 `__pycache__` | 在倉庫外、屬我本輪所造；列此供清理，本節未刪 |
| `backend/**/__pycache__` | `settings_probe.py` 以 `PYTHONPATH=backend` 執行時刷新（gitignored） | 不影響 digest（tree-digest 只取 `*.py`） |

**被標為唯讀／實際的界線（提示：不得把會寫 run dir／DB／cache／build metadata 的命令叫唯讀）**：本節只有 **§11.0 那一列列的命令**（`git rev-parse`／`git status`／`find…shasum`／`pgrep`／`lsof`／`ls`／`stat`／`grep`／`check_migration_tail.py --json`／`import psycopg2`／`playwright --version`）與 §11.9(b) 的 `shasum`／`git diff --numstat` 是唯讀（`check_migration_tail.py` 與 `--version` 只讀盤與印 stdout）；**其餘全部是實際執行**（4 次 lane run、1 次 tsc、1 次 node 測試腳本）。本節**未宣稱**任何 PG／服務／瀏覽器命令做過。

### 11.12 本輪未執行（如實，不美化）

未啟動 PG／uvicorn／`next dev`／瀏覽器／任何外呼；未跑 `playwright test`（連 `--list` 都沒跑，理由＝§11.8「注意」列）；未跑整套 sqlite lane（未重釘 ⇒ 必然被第二類閘拒絕，§9.0.1）；未重釘 `collected_nodes.txt`／`skip_budget.json`；未 `git apply`／`add`／`commit`／`push`、未產生可套的 patch 檔；未刪除任何檔（含 `backend/test.db`、52＋支舊 TMPDIR DB、兩個 r13 基準目錄）；未改 `app/config.py`、未加新設定鍵、未加 migration；**本輪唯一「非計畫內」的動作是新增了两個 TMPDIR 探測檔與 6 個 run dir**，無其他越界。

### 11.13 這一份附件要 owner 回答的，只剩格位（不是問題清單）

| 缺的那格 | 只有谁能給 | 本節已把門收斂到 |
|---|---|---|
| PG 現場的建立與升級 | owner 一張卡 | §11.6 的 A-1…A-5（庫名、角色、SQL、上限、停線全固定） |
| 4 支具名跳過的競態節點 | owner 批准 pg 層執行 | §11.6 的 node id 逐字清單 ＋ 帳本判準 ＋「跳過＝NOT RUN」 |
| 自然 cron 那一欄 | owner 批准 B 卡（需要等到 05:00 UTC） | §11.7 全格固定（不給二選一） |
| 兩條旅程的**瀏覽器**層（含只有本人能按的 grant／revoke） | owner 批准 C-stack ＋ 本人出力 | §11.8：兩個埠、三個明釘值、只那兩支 spec、跳過原因逐字入帳 |
| 本輪增量能不能保存、保存哪些 | owner 選 D-3 邊界 | §11.9：6 檔逐檔、manifest、隔離重現範圍與不混的清單 |

**本輪完成**：把 predict 兩條門與 worker 收到同一道 kill switch（5 節點實測）；用最小合法 request 把「衝突」那格拆成**一半撤回、一半是真 500 並已修**（6 節點實測）；計費文案改成按持久業務鍵並逐條對回代碼；以執行期證據完成 dotenv 修復的**順序核對＋四項正控**；量出**測試修復不涵蓋服務進程**這條新缺口並把它釘進 C／B 兩卡；旅程批次 579 節點 522 過 57 跳 0 敗；兩支 spec 對當前內容 rc 0 型別通過；把每個數字逐份對回它自己 `identity.txt` 的 `tree-digest`（發現 17:33:41 對 `daily_estimates.py` 的寫入讓樹從 `b0149272…` 換成 `44803d5b…`，因此把受影響的兩組節點在當前樹重驗：衝突 6／6、每日估計 48／48，皆 rc 0＋`evidence gate closes`），並把 §11.11 的 run dir 數由誤寫的 6 改成現測的 14；最後把散在三節的 A／B／C／D 收斂成上面這一紙。

---

## 十二、第十七輪（2026-11-20 18:4x–19:3x HKT）：**§1–§4 四格工程，不是紀錄更新**（一次收斂驗證＋九檔增量）

### 12.0 座標核對（簡報指定的「核對」，非 checkout／reset 指令）

| 核對項 | 現測值 | 來源 |
|---|---|---|
| repo 預期 | `TommyLam0915-LCW/alethra`；本輪**未**再查 remote（無 git 寫入授權，也無新事實要推翻 §11.0） | — |
| branch | `live1-impl` | `git rev-parse --abbrev-ref HEAD` |
| HEAD | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` ＝ 簡報的歷史 base（同一 commit，未動） | `git rev-parse HEAD` |
| 工作樹髒度 | **221 path(s)**，`git status --porcelain` 的 sha256 `88cdeeb57ebd7a94d4b921053ee45c3936faf88eff93cc9e0b3f3c031b4f483e` | 由 lane 自己寫進 `identity.txt` 第 4 行 |
| 受測內容身份 | `tree-digest-before` ＝ `tree-digest-after` ＝ **`2ec1ef9cbde42fb82acfaf0383b4cbfbb17a645116db654e2154a47121f1a5e8`**（本節所有後端數字綁這一支樹；由 `identity.txt` 讀回，非手抄） | run dir `sqlite-20261120T113217Z-92572` |
| 其他 session 的成品 | 221 髒路徑全部原樣保留；本輪只碰 §12.6 那 9 檔 ＋ 本節附加 | `git status --porcelain` |

### 12.1 §1 一般 personal predict：九條順序**已在共用函式內**，本輪只補辨識測試

簡報要求「已由程式與測試滿足就標明證據並跳過修改」。逐條核對（行號綁 `2ec1ef9c…`，`backend/app/api/personal_models.py`）：

| # | 不變量 | 现在的位置 | 之前怎麼壞（若適用） | 直接測試（節點） |
|---|---|---|---|---|
| 1 | request→login／API scope | `:773-774`（version 入口）／`:796-797`（record 入口）＝`get_current_user` ＋ `check_api_key_scope("write","inference")` | — | `test_formal_skill_unit_authorization_endpoint.py`（13 節點，重用） |
| 2 | owner／skill 存在且屬該 owner | `_owned_version :777`、`_owned_record :800`，再進 skill 檢查 | 早期只在 decorator 擋，worker 直呼叫共用函式可繞過 | 同上 ＋ `test_predict_kill_switch_boundary.py::test_the_worker_door_passes_the_same_line_as_the_button` |
| 3 | #177 固定版本＋qualification／selection／source／consent | **`_require_inference_authorization(check_model_version_inference_authorization(...))` 在 `:619-623`，位置在 `_run_personal_prediction` 內**，故 HTTP 與 worker 必經同一道 | 同上（decorator-only） | `test_formal_skill_unit_authorization_endpoint.py` ＋ §12.1 新檔 #1 |
| 4 | global／inference kill switch，**讀不到就拒絕** | `_refuse_when_switched_off :610`（實作 `:570-591`：`kill_switch_active`／except 分支 `kill_switch_unreadable`） | switch 讀取異常被當成「沒開」 | `test_predict_kill_switch_boundary.py` 全 5 節點（`::test_the_inference_switch_refuses_both_entries_and_spends_nothing`／`::test_the_global_switch_covers_both_entries_by_inheritance`／`::test_a_switch_that_cannot_be_read_refuses_rather_than_infers`／`::test_the_execution_switch_still_leaves_a_pure_estimate_alone`） |
| 5 | features＋schema（真實 loader） | `features_as_of :635`；`artifact_missing` 先擋於 `:626-633` | — | `test_successful_inference.py`（14 節點，重用） |
| 6 | reserve→engine→settle→UsageRecord | `reserve_quota :656`／`settle_reservation :688`／`record_api_call_no_commit :692`（三個函式 AST 核過**自身不 commit**） | 多層重複 reserve／settle | `test_successful_inference.py` ＋ `test_personal_strategy_learning.py`（22 節點，重用） |
| 7 | 由正確交易擁有者 commit | 成功**只**有 `:730` 一次（本輪由 `:724` 右移，見 §14.4）；四個引擎失敗分支 `:660-661`／`:670-671`／`:674-675`／`:678-679` commit 的是 **release**，不是帳單 | 內層偷偷 commit 半筆 | 拒付判準：`test_predict_kill_switch_boundary.py::test_the_inference_switch_refuses_both_entries_and_spends_nothing`（0 engine call／0 usage 列／0 settlement） |
| 8 | 回真實持久化 `inference_id` ＋ 實際版本 | 回應 body `:736-765`（含 `skill_unit_id`、`resolved_from`） | — | `test_successful_inference.py` |
| 9 | #177  precedence：明示 version ＞ skill selection ＞ record alias；**解析到版本≠授權** | record 入口 `:800-841`（明示 `:801-802`、`latest_skill_selection :808-814`、alias `:815-817`），三道都再進 `:619` 的 gate | alias 顶替 skill selection | **新檔 `test_predict_record_route_explicit_version.py`（270 行／4 節點）**：`::test_the_record_route_serves_a_version_the_caller_names`、`::test_naming_a_superseded_version_is_refused_and_not_swapped`、`::test_the_record_alias_cannot_stand_in_for_a_refused_version`、`::test_a_version_of_another_record_is_not_found_on_this_route` |
| 10 | 「按需 predict 不加入每日窗口去重」 | 該路徑無 `claim_estimate`／window 參數（grep 無命中） | — | 上述節點均以「兩次按需 predict＝兩次計費」為前提 |

**§1 本輪淨增量＝一支新測試檔（＋ kill switch 檔的本輪段）**，產品碼未動：九條里有八條已由 `2ec1ef9c…` 上的程式與既有測試滿足。**新增**：`tests/test_predict_record_route_explicit_version.py`（4 節點，補「record 入口明示版本」與「alias 不得顶替」兩格）。

### 12.2 §2 每日估計：`window=None` 已不存在，八情境由既有 48 節點覆蓋，鎖生命週期改寫成文

| 簡報要求 | 現況（綁 `2ec1ef9c…`） |
|---|---|
| 「不得再用 `window=None` 繞過每日去重」 | `grep -rn "window=None" app/` ＝ **0 命中**（現測） |
| 逐項順序：重核 owner／skill／consent／selection／switch → 業務鍵交易執行權 → **取得席位後重讀持久帳本** → 已結算則回原 `inference_id`（不 reserve／不推論／不結算）→ 否則共用推論 → 落帳 → 釋放 → 摘要 | `daily_estimates.py::claim_estimate :509`（`db.commit()` 後取 `pg_advisory_xact_lock(hashtextextended(f"{owner}|{skill}|{instrument}|{window}"))`）→ `_estimate_one :758`（同一 session 傳下去）→ `_run_personal_prediction`（§12.1 那條鏈）；帳本回落＝`app.services.billing.record_api_call_no_commit` |
| 「`job.result_json` 不是已付費事實的唯一依據」 | `_window_state :251`（測試端）對 `_estimates`／`_reservations` 讀實表；節點 `test_the_window_state_separates_the_claim_from_the_bill_and_the_summary`（`:893`） |
| 「不要未經分析改業務鍵」 | 業務鍵仍＝`owner|skill|instrument|window`（未改），本輪**未**動鍵的形狀 |
| **必須寫明**：內層 commit 是否釋放 advisory lock、第二個 worker 何時讀得到 | 已寫進 `claim_estimate` docstring `:534-551`（18 行）：席位是 **transaction-scoped**，屬於下方 `db.commit()` 開啟的那個交易，**與 Python 框是否仍在執行無關**；成功路徑上 `personal_models.py:730` 那一次 commit **同時**讓帳單持久並釋放席位 ⇒ 「釋放」與「可被第二個 worker 讀到」是同一瞬間；其餘出口（引擎失敗 commit release、item loop except 分支 rollback）都是「沒花到錢就把席位還回去」，因為 commit 與 rollback 同樣結束交易 |
| 摘要寫入失敗仍不得雙重計費 | `test_a_lost_summary_cannot_buy_a_window_twice`（`:1144`）＋ `test_a_billed_window_with_a_lost_summary_still_names_its_estimate`（`:1203`） |
| **只補 8 個直接回歸**，已有最新有效證據不重跑 | 八格對應到的節點（全部在 `tests/test_daily_model_estimate.py`，本輪**未新增也未改寫任何節點**，因八格都已有判準節點；該檔最後一次改動在本輪之前的 11:06，其證據由 §11.0 的 `44803d5b…` 48／48 與本輪 12.5 的 `2ec1ef9c…` 重測各自綁樹）：①manual→manual `:1867`／`:2193` ②manual→cron `:1960`／`:2008` ③cron→manual `:1930`／`:2055` ④cron→cron `:615` ⑤成功後摘要缺失 `:1144`／`:1203` ⑥未結算失敗再試 `:969`／`:1056`／`:2193` ⑦部分成功 `:2099`／`:2249` ⑧撤回及切版 `:1396`／`:1444`／`:1595`／`:2745`。**§2 本輪唯一改動＝`daily_estimates.py` 的鎖生命週期那段文字**（上列第 5 列），去重路徑與業務鍵未動 |
| PG 跨 process 驗證保留，未批准＝**NOT RUN**，不用 SQLite 冒充 | `tests/daily_estimate_race.py`、`tests/test_daily_estimate_two_workers.py` 兩支存在、未執行；後者在 sqlite 層自行跳過，跳過理由逐字進帳（§12.5） |

### 12.3 §3 決策約束衝突：只把可辨識的預期約束映射成 409

**新增產品檔 `backend/app/api/conflicts.py`（98 行）**：`expected_unique_violation()` 只認兩條預期唯一索引——`uq_paper_order_portfolio_idempotency`（`paper_orders(portfolio_id,idempotency_key)`）與 `uq_human_decision_owner_session_key`（`human_decision_events(owner_id,simulation_session_id,idempotency_key)`）；PostgreSQL 走 `diag.constraint_name` ＋ sqlstate `23505`（`23502` NOT NULL／`23503` FK 因此被擋在外），SQLite 走**逐欄位比對整個有序欄位集合**（所以 `paper_orders.id` 主鍵碰撞不是 idempotency 重複）。認不出＝回 `None`＝**原樣抛出**，由請求層給通用錯誤碼；「一個沒人認得的缺陷」不會再被改名成「你重複提交了」。

| 門口 | 接線位置 | 交易 rollback 歸屬 |
|---|---|---|
| 回放門（訂單寫入） | `app/api/simulations.py:21` import ＋ `:2253` | `if expected_unique_violation(exc) != PAPER_ORDER_IDEMPOTENCY: raise` － 由 route（交易擁有者）決定回滾，helper 不碰外層交易 |
| 回放門（決策寫入） | 同上 ＋ `:2665` | 同上 |
| 人為決策事件門 ×2 | `app/api/human_decisions.py:18` ＋ `:214`、`:343` | 同上 |
| 帳戶決策門（既有） | `app/api/paper_portfolio.py`（§11.9 的 R16-2 段，本輪未再動） | 同上 |

**未做（簡報禁止項，照單確認）**：沒為記錄決策加 `ai_usage`、沒新建 idempotency 系統、預查（如有）只改善訊息不取代 DB 競態保護、回傳內容不含 SQL／參數／內部路徑／他人資料、不偷偷重試建第二筆。

**直接測試**：`tests/test_conflict_recognition.py`（**新檔 520 行／8 節點**）——`::test_the_idempotency_keys_name_the_indexes_the_doors_expect`（釘住兩條索引與 DDL 同義）、`::test_a_primary_key_collision_is_not_an_idempotency_duplicate`、`::test_another_doors_unique_index_is_not_this_one`、`::test_a_missing_required_value_is_not_a_duplicate`、`::test_a_broken_foreign_key_is_not_a_duplicate`、`::test_the_postgresql_spellings_are_read_the_same_way`（PG 兩種措辭）、`::test_the_create_door_answers_a_real_collision_with_409`、`::test_the_create_door_does_not_rename_a_null_as_a_duplicate`；加上 §11.9 那 6 節點（`test_decision_conflict_repro.py`）繼續守「可達衝突」那一格。**未重寫探測腳本**（簡報：先使用已量測的可達衝突及現有六個回歸）。

### 12.4 §4 兩條畫面旅程：一處真缺陷已修，斷言補齊；spec 仍未執行

**本輪唯一的前端產品改動**（`frontend/src/components/workspace/OrderPreviewPanel.tsx`，累計 `＋168／−3` vs HEAD，本輪段＝下列 4 處）：
`buildReview` 過去只凍 session／action／quantity／rationale／來源／key，**游標版本沒凍**——`submitManual` 在按「送出」的當下才回讀 `session.state_version`。後端的樂觀鎖（`app/api/simulations.py:2419-2425`）比較的就是這個數字，而**每一筆被記錄的決策（含 hold）都把版本推進一步**（同檔 `:2563`，游標日期與 bar 不動：`:2551-2555`）。後果：檢視之後若別人動過 session，這次送出會帶著「別人動完之後」的新版本去打鎖 ⇒ **門打不開，畫面還以為成功**。修法：
1. `ManualReview` 介面新增 `expected_version: number | null`（`:69`）；
2. `buildReview` 凍結它（`:473-474`，紧跟 `cursorLabel`）；
3. `submitManual` 只從凍結值送出（`:496`，取代原本的回讀）；
4. 409「State version conflict」分支重讀 session 並 `onSessionChange`，否則那個死數字會跟著之後每一次檢視，門永遠開不了（`:~534`）。
冪等重試不受影響：route 的 idempotent replay 在版本檢查**之前**就回（`:2373`），同 key 同 payload 不會因為版本已前進而被拒。

**兩個面板狀態格**：`simulation/page.tsx` 的舊路徑（`pendingDecision` 凍 payload）本輪未動——它已凍 key／版本；`dailyEstimate.ts` 的 `lineageState :270-274` 未動。

**spec（`?? → 本輪改寫`，未執行，TIER B）**：
- `frontend/e2e/manual-decision-lineage.spec.ts`（368→**504 行**）：提交節點新增「URL 打在 review 那個 session 上」、「body 必帶凍結的 `expected_version`」、「該數字恰等於寫入前那一版（＝回應 `state_version − 1`）」三支斷言；**第四節點**造「檢視之後被人動過」——讀 live 版本→選 hold（斷言 `ws-lineage-attach` disabled、`ws-lineage-hold` 可見）→按 Review→用正式入口塞一筆合法 hold（200，版本恰 +1）→按 submit→斷言送出仍帶 `atReview`、**無** `linked_inference_id`、**409**、訊息為 state version conflict、決策列恰多 1、`order-review` 計數 0（檢視作廢）→重載後重選重檢視，斷言這一次帶 `atReview+1` 且 200。標頭補「這一節點會寫什麼」的揭露（3 筆 hold 決策列、不建訂單、不動現金、不扣 `ai_usage`）。
- `frontend/e2e/daily-estimate-journey.spec.ts`（448→**539 行**）：新增「每一列徽章由帳本推導」節點——逐列以 `estimateState()` 從 status 實算期望態，斷言徽章文字＝`ESTIMATE_STATE_LABELS_ZH[期望]`、徽章只能是該函式回得出的那幾個字（**發明的終態在此就失敗**，「不得按 job 年齡改終態」讀成對字串的閘），再逐区块斷言 `none`／`unconfirmed`／`reused`／`refusal`／`failure`／`drift` 六块的出現次數與資料同有同無，並把這台機器實際覆蓋到哪些態印進 console（没覆蓋的態對本次執行就是未驗，不由文案宣稱）。
- **未做**：沒開瀏覽器、沒跑 `playwright test --list`（「寫 spec 不代表獲准開瀏覽器」）；沒新開第三套 spec（「兩支已有就補缺失斷言」）；**同意／撤回按鈕只斷言存在，絕不代按**。

### 12.5 收斂驗證（一次，綁 `2ec1ef9c…`；不與改動並行）

| 層 | 命令（原樣） | 真實結果 |
|---|---|---|
| 後端 sqlite 層 | `cd backend && DATA_PROVIDER=demo ./scripts/run_tests.sh sqlite -q` ＋ 11 支檔（§1 五支、§2 兩支、§3 兩支、§4 兩支） | **`203 collected / 199 passed / 4 skipped / 0 failed / 0 error`，pytest 自身 exit `0`**；`evidence gate: ledger closes against this run's own artifacts`；run dir `sqlite-20261120T113217Z-92572`（`identity.txt`：`provider: DATA_PROVIDER=demo`、`layer: sqlite`、`selection-kind: explicit-selection`、`tree-digest-before == after == 2ec1ef9c…`） |
| ↑ 的 4 個跳過（逐字） | 同上 | 1× `test_daily_estimate_two_workers.py:598`「the seat being raced is pg_advisory_xact_lock, which SQLite has no way to take…」（被 census 歸為 `other`，實質是 PG-only）；3× `test_manual_decision_books_linked_portfolio.py:2534／:2580／:2645`「row-lock serialisation requires a real PostgreSQL backend」⇒ **PG 跨 process 那格維持 NOT RUN**，未用 SQLite 冒充 |
| 前端單位 | `node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/*.test.mjs` | **`195 tests / 195 pass / 0 fail`**，RC `0`（第一次以 `--test tests/` 執行得 `ERR_UNSUPPORTED_DIR_IMPORT`、`0 pass`、RC 1——那是我叫錯命令，不是成品失敗；改檔清單後重跑才成立） |
| 前端型別 | `npm run typecheck`（`tsc --noEmit`，含 `e2e/**`） | 第一輪抓到我自己造成的 **TS2802**（`Set` 展開需 `downlevelIteration`）⇒ 改 `Array.from(seen)`；重跑 **RC `0`、無輸出** |
| 舊證據 | §11.0 的 `44803d5b…` 6／6 與 48／48 | 未重跑（樹已換成 `2ec1ef9c…`，本輪同檔同層已重測：衝突 8＋6、每日估計 48，全部在上面的 199 之內） |

### 12.6 本輪寫入清單（9 檔 ＋ 本節附加）與副作用

| # | 路徑 | 狀態 | 歸屬 |
|---|---|---|---|
| 1 | `backend/app/api/conflicts.py` | `??` 98 行（新檔） | §3 |
| 2 | `backend/app/api/simulations.py` | `M`（累計 ＋364／−72；**本輪段**＝`:21` import、`:2253`、`:2665`） | §3 |
| 3 | `backend/app/api/human_decisions.py` | `M`（累計 ＋17／−2；**本輪段**＝`:18`、`:214`、`:343`） | §3 |
| 4 | `backend/tests/test_conflict_recognition.py` | `??` 520 行／8 節點 | §3 |
| 5 | `backend/tests/test_predict_record_route_explicit_version.py` | `??` 270 行／4 節點 | §1 |
| 6 | `backend/app/services/daily_estimates.py` | 未追蹤檔；**本輪段**＝docstring `:534-551`（18 行，無程式改動） | §2 |
| 7 | `frontend/src/components/workspace/OrderPreviewPanel.tsx` | `M`（累計 ＋168／−3；**本輪段**＝`:69`、`:473-474`、`:496`、409 分支重讀） | §4 |
| 8 | `frontend/e2e/manual-decision-lineage.spec.ts` | `??`→本輪改寫 368→504 行 | §4 |
| 9 | `frontend/e2e/daily-estimate-journey.spec.ts` | `??`→本輪改寫 448→539 行 | §4 |
| — | `EXECUTION_TABLE_20261117.md` | 僅附加本節 | — |

副作用（全部揭露，非唯讀）：新建 **1 個** run dir（`sqlite-20261120T113217Z-92572`）＋其 per-pid `${TMPDIR}alethra-sqlite-test-92572.db`（保留未刪）；`${TMPDIR}` 之外另寫 `/tmp/r17-sqlite-convergence.txt`、`/tmp/fe-units-r17.txt`、`/tmp/tc-r17.txt` 三份本輪 console 快照；`backend/**/__pycache__` 被 import 刷新（gitignored，不進 `*.py` digest）；**未碰 `backend/test.db`**、未刪任何檔、未動其他 session 的 221−9 條髒路徑。

### 12.7 本輪未執行（如實）

未啟動 PG／uvicorn／`next dev`／瀏覽器／任何外呼；未跑 `playwright test`（含 `--list`）；未跑整套 sqlite lane（`collected_nodes.txt`／`skip_budget.json` 未重釘 ⇒ 整套必然被第二類閘拒絕，本輪也不重釘）；未 `git add`／`commit`／`push`／`apply`、未產生 patch 檔；未加 migration、未改 `app/config.py`、未新增設定鍵；未讀任何秘密值、未展開程序環境；未跑 §2 的 PG 跨 process 兩支程式（**NOT RUN**）。

### 12.8 已知缺口（不由本節美化）

1. **面板凍結版本沒有可執行的測試層**：`OrderPreviewPanel.tsx` 是 React 元件，本倉庫無元件渲染 harness；它的證據階＝①離線讀碼 ＋ ④sqlite 層的 route 側（`:2419-2425`／`:2563`）＋ 一支**未執行**的 TIER B spec。瀏覽器層未批之前這格不能升等。
2. **「不要在 confirm 才重讀最新估計」只證到一半**：版本與 id 已證（第四節點），但「檢視存活的同時『最新估計』被換掉」這格在同一個 UTC 視窗內不製造第二次計費事件就無法造出來——該子格保持**未驗**，不是已驗。
3. **「每日面板七個狀態」與代碼的對照**：代碼裡由資料決定的是 `estimateState()` 的 **5 個窗口態**（`dailyEstimate.ts:80-85`／`:104-111`）＋ 兩個獨立區塊（`last_failure` 失敗列、`estimate_matches_current_selection` 漂移警示）＝**7 件資料決定的事**；若 owner 所指的「七態」是另一套切法，需要點名，本輪不發明代號（spec 標題已改寫為「只能是 `estimateState()` 回得出的那幾個」）。
4. **跳過 census 的歸類**：`test_daily_estimate_two_workers.py` 的 PG-only 跳過被 `skip_budget.py` 歸入 `other=1` 而非 `pg_only`（理由字串未含關鍵字）；這是分類器的事實，不是新的阻塞，改字串屬文案整理，本輪依簡報禁止項未做。
5. **route 側的衝突映射在 SQLite 上只驗過措辭與判準**：PostgreSQL 的 `diag.constraint_name`＋`23505` 那條分支由 `::test_the_postgresql_spellings_are_read_the_same_way`（偽造 orig）覆蓋，**真庫上的同條**仍属 §11.6 的 A 卡範圍（未批 ⇒ NOT RUN）。

---

## 十三、第十八輪（2026-11-20 20:5x–22:0x HKT）：**四門改在入口證、409 不 reload 就能恢復、鎖敘述對齊實測呼叫順序、A／B／C／D 換成可批准正文**

提示原文：「以第 17 輪回報的候選 `2ec1ef9c…` 為核對起點，不 checkout、reset、stash 或清理其他 session。若目前樹有漂移，只核對下面承重檔及依賴，用實際差異決定哪些證據仍適用。」＋「本指令不批准 PG／DDL／migration 執行、服務／browser、自然 cron、外呼、依賴安裝、秘密操作、重釘、clone／apply、Git 寫入或部署。」**本輪一律照辦：四張卡只交付、未執行。**

### 13.0 座標核對（本輪 candidate；每個數字都讀回自本輪實物）

| 項 | 值 | 來源 |
|---|---|---|
| 固定基線 | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`（branch `live1-impl`） | `git rev-parse HEAD` 本輪現測 |
| 工作樹髒度 | **224 paths**（141 `M` ＋ 83 `??`）porcelain sha256 `c67ee8e9f34b152f1bdb29deed566c83893a2f9ea73f8930c8ba026c74a8f272` | `run_tests.sh` 寫入 `identity.txt`，非手抄 |
| 後端樹 digest | `699cd4b82d25488797a158dce68955043159774c92fb20e338eedd18cd565883`（**before == after**） | run dir `sqlite-20261120T135744Z-39627/identity.txt` |
| 第 17 輪候選 `2ec1ef9c…` 的现状 | **已被本輪寫入超越**：本輪動過 `app/services/daily_estimates.py`（21:36:23）與 `app/api/simulations.py`（21:03:42），兩支都在 `*.py` digest 涵蓋內 ⇒ 舊數字不再代表當前樹。舊證據不因新樹作廢的部分，逐檔標在 §13.7 | `tree_digest()` `backend/scripts/run_tests.sh:373-379`（只雜湊 `*.py`，**不含前端**） |
| 平行 session | `human_decisions.py`（18:44:29）／`conflicts.py`（18:43:31）＝第十七輪那批，本輪**未動**；本輪只讀它們的門 | mtime 現測 ＋ §12 對照 |
| 本機 PG | **無執行中實例**（無 postgres pid、5432／5433 未聆聽）；可用二進位＝`.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin`（32 支，`postgres (PostgreSQL) 16.2`，含 `initdb`／`pg_ctl`／`psql`／`pg_isready`）；驅動 `psycopg2_binary-2.9.11` 已在 `backend/.venv`（**不需要裝依賴**） | 本輪現測 |

### 13.1 ①四門：每門**實際證到什麼**、**未證什麼**（入口證明，不是 helper 測試）

新檔 `backend/tests/test_conflict_doors_at_entry.py`（723 行，`??`，sha256 `a2bc0a903ec661ac…`），8 支節點全部**走 HTTP 入口**、讀回用請求沒碰過的 `Session`。四門與它們**自己**预期的索引（門的代碼順序＝本輪現讀）：

| 門 | 入口與代碼位置 | 本門索引 | 已證（sqlite 層，本輪 183 節點 lane 內 8／8 PASS） | **未證**（不得由本輪任何一格推得） |
|---|---|---|---|---|
| `A` | `app/api/simulations.py` `_submit_manual_order`（pending order 落地的瞬間） | `uq_paper_order_portfolio_idempotency`（`migrations/versions/j4k5l6m7n8o9_add_paper_order_idempotency.py:49-51`；`app/models/paper_portfolio.py:168`） | 只認自己那支索引；**NOT NULL 反控**不改名成重複（`test_door_A_does_not_rename_its_own_not_null_failure`） | PG driver 實際回的 `SQLSTATE`／constraint 字串（sqlite 由 qualified column tuple 判別）；**兩筆真交易重疊**（本檔用「同 session 內暫存另一門的列」代換，`autoflush=False` 對可讀檢查隱形，檔頭 §docstring 已具名承認） |
| `B` | `app/api/simulations.py` `manual_decision` 收尾（decision＋ledger event＋audit 一起 commit） | 同上（A／B 同入口、同索引） | 守衛起點已**移到 audit 寫入之前**——本輪由這支節點挖出的真缺陷：原守衛從 `emit_event` 之後的 `db.flush()` 開始，而 `emit_event` 自己會 flush（`app/services/__init__.py:64`），衝突早已落地成 500，文件上的 409 分支幾乎到不了 | 同上（PG 側）；**跨連線**的兩個 request 同時到達 B（本機 sqlite 是 `StaticPool`，全部 session 共用一條 DBAPI 連線，`tests/conftest.py:207`） |
| `C` | `app/api/human_decisions.py` `POST /human-decisions` | `uq_human_decision_owner_session_key`（`…h8i9j0k1l2m3_add_human_decision_events.py:56`；`app/models/human_decision.py:83`） | **不冒充**：另一門的 paper 索引撞了，C 不答成自己的 409（`test_door_C_does_not_answer_another_doors_order_index_as_its_own`） | C 自己索引在 PG 上的 23505＋constraint 名；FK 違反在 PG 的實際分級 |
| `D` | `app/api/human_decisions.py` `POST /human-decisions/{id}/correct` | 同 C | **唯一沒做任何暫存的一格**：衝突列在請求前就已 commit（慢點第二下的真實情境），只有 `utcnow()` 被釘住；另含**不把 order 索引改名**的反控（`test_door_D_does_not_rename_the_order_index`） | 同一把鑰字被**兩條真交易**同時送達（sqlite 單連線做不到） |
| 通用 | 兩支 HTTP 入口 | — | 空 key 先被拒，索引不會被順序摸到（`test_the_entries_refuse_an_empty_key_so_the_index_cannot_be_reached_sequentially`）；`B` 讓另一門的索引**原樣抛出成 500**（`test_door_B_lets_another_doors_index_through_as_a_500`）＝**反控**，不是本門的重複 | 任何 PG-only 行為 |

**清理與可恢復性（提示點名的那一格）**：每支節點在失敗後用**獨立 Session** 讀回，斷言該門沒有新增局部 decision／order／audit，**並且**斷言 fixture 先前**已 commit** 的列在門的 rollback 之後仍在——這句證的是清理範圍只在請求自己的交易內，不是靠一次更大範圍 reset；接著再送一筆合法請求確認入口恢復可用。**沒有用「下一個請求成功」反推前一個交易已正確清理**（前者是獨立讀回的 0 列，後者是另一條斷言）。

**保留、未重寫**：`POST /paper-portfolio/{id}/decisions` 那六支回歸原封不動（`tests/test_manual_decision_books_linked_portfolio.py`），本輪**沒有**拿它們當這四門的整合證據；本輪 lane 內它們 14 支節點各自通過，歸屬照舊寫在 §12.3。

### 13.2 ②工作區 409 之後**不 reload** 的恢復路徑

| 條目 | 內容 |
|---|---|
| 改了哪裡 | 產品：`frontend/src/components/workspace/OrderPreviewPanel.tsx`（M，`git diff --numstat HEAD` = **＋197／−15**，1,486 行，sha256 `36ed4ea8773d52f7…`）——409 分支改成**原地重讀**：`setReview(null)` → 重讀版本 → 以讀回值重建 review → 再裝上錯誤，**不 reload、不重新導航**。新增共用判準：`frontend/src/lib/reviewCurrency.ts`（`??`，70 行，sha256 `3a8aaa77b083e6ed…`）＝`reviewStamp`／`frozenVersion`／`isStillViewed` |
| spec | 改**現有**那支，沒有開第三支：`frontend/e2e/manual-decision-lineage.spec.ts`（`??`，810 行，sha256 `e319daab7438f034…`；第 16 輪記錄的是 368 行／`d9e4d062…`） |
| 分開的斷言 | ①送出時帶著版本 N（请求本體讀回）→ ②伺服器以 **409** 確認版本衝突 → ③舊 review 從畫面消失（`setReview(null)` 的 DOM 效果）→ ④**獨立讀回**看不見 A 那筆額外寫入。四格是四條斷言，不是一條流程 |
| 不硬寫 N+1 | 競爭動作之後**讀回真實版本**當預期值；code 裡沒有任何 `version + 1` 常量 |
| session／版本已先變 | 若在重讀前就偵測到 session／版本改變 ⇒ **作廢 review、零 POST**（`isStillViewed` 為 false 時不送出）；spec 以請求計數＝0 驗這條 |
| 切換 session 的競速 | 不用「await 前閉包裡的 session」當作唯一現況證明：送出口再讀一次當前的 session identity，舊請求完成後若已切走則**不得覆蓋**新 session 的狀態 |
| 靠 reload 的舊路徑 | **降為另一個恢復情境**（reload 後恢復），不再是主證明；兩者斷言不同、標題不同 |
| legacy 無版本路徑 | 獨立標示為 **legacy（無樂觀鎖）**，**不算**樂觀鎖已驗證 |
| 驗證等級 | 離線層：`node:test` lane **202／202 rc 0**（含本輪新增的 oracle 節點）＋ `npx tsc --noEmit` **rc 0**。**瀏覽器層 NOT RUN**（未獲准啟動 8101／3101 棧；`playwright --list` 的探測副作用見 §13.4-C）⇒ 本格目前是「本地已寫＋型別通過」，**不是**「已測試」，更不是「已驗收」 |
| 同窗口切換最新估計 | **維持未驗證**：既有隔離 fixture 無法在不扣額的前提下驅動它，本輪**沒有**改帳本、**沒有**另造付費事件 |

### 13.3 ③每日估計：敘述與斷言只修「可證明」的兩處

**(a) `claim_estimate` 的生命週期改成實測呼叫順序**（`backend/app/services/daily_estimates.py`，`??`，1,592 行，sha256 `6672d853a11059e2…`；本輪只動文字，業務鍵與推論碼未動）：

| 提示要問的 | 現行正文（每個行號本輪重測） |
|---|---|
| 哪個 SQL 取得鎖 | `db.commit()`（`:593`）**結束** tick 之前開著的交易，接著 `:594-595` 的 dialect 守衛，再 `:596-602` 對 `pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), CAST(0 AS bigint)))` 發一條 `SELECT`，鍵值 `f"{owner_id}\|{skill_unit_id}\|{instrument_id}\|{window}"` 在 `:601`。**沒有寫成「commit 開啟了持鎖交易」**——commit 關的是上一段，鎖是 autobegin 後的下一條語句拿的（`app/database.py:20` `autocommit=False, autoflush=False`） |
| 哪個交易結束釋放它 | 任何結束該交易的动作：成功那側是 `_estimate_one` 內部那**一筆** `db.commit()`（`app/api/personal_models.py:730`，其前依次為 `reserve_quota` `:656` → `settle_reservation` `:688` → `record_api_call_no_commit` `:692`）；失敗那側見下兩列 |
| 失敗分支提交的是哪筆釋放 | `personal_models.py` 自己的四支失敗分支各提交一筆 **release**：`release_reservation` 在 `:660`／`:670`／`:674`／`:678`，緊接著 `db.commit()` 在 `:661`／`:671`／`:675`／`:679`（helper 本身只 flush）⇒ 這筆 commit 已經把持鎖交易關掉，**比迴圈更早**。其餘失敗由 `_estimates_for_owner` 項目迴圈裡的 **三支** `db.rollback()`（本輪重測：`:1095`／`:1108`／`:1121`，對應 `EstimateRefused`／`HTTPException`／`except Exception`）結束交易，零寫入 |
| 第二個 worker 在哪個交易／隔離級別、用哪次查詢讀到帳本 | 它是**自己那個新交易**裡的第一批語句：`estimate_billed` → `billed_estimate` → `settled_estimates`（`:465`／`:484`／`:502`），即對 `usage_records` 的那一次 `SELECT`（查詢由 `billed_estimates_by_skill` `:409` 建），取回後在 Python 端按業務鍵比對。引擎**沒有**設 isolation level（`app/database.py:19`）⇒ PG 用預設 **READ COMMITTED**，每條語句一份新快照。**沒有**寫成「任何既有 snapshot 都看得见」：正文明白寫出在 REPEATABLE READ 下、或在拿到鎖**之前**讀過帳本的 worker，仍然只看得见自己那份快照 |
| 反控（本輪自己抓到並修掉的引用缺陷） | 正文曾引用不存在的 `run_daily_estimates` 與 `:1055`／`:1068`；已改為可解析的函式／例外名＋現測行號，並用 AST 核對過四個跨模組符號（`_assert_personal_version` `personal_models.py:199`、`_require_inference_authorization` `:172`、`_refuse_when_switched_off` `:570`、`reserve_quota` `entitlements.py:252`） |

**(b) 徽章 oracle 去迴圈**（同一份提示：「現有 spec 不得只用產品相同 helper 算答案、再拿答案驗證產品」）：

| 條目 | 內容 |
|---|---|
| 五個視窗徽章 | **全數保留**：`已沿用`／`本次估計`／`本次拒絕`／`執行狀態未確認`／`沒有估計`（`src/lib/dailyEstimate.ts` 的 `ESTIMATE_STATE_LABELS_ZH`）。失敗資訊與版本漂移是**兩條獨立提示**，spec 註解現寫成「Two notices, not two more terminal states」，沒有把它們升格成互斥終態 |
| 獨立判準 | spec 內新增自己的讀法 `badgeOfLedger(row)`——只讀帳本**原始欄位** `last_estimate`／`last_estimate.verdict_durable`／`last_refusal`／`window_claimed`，**不调用** `estimateState()`；另加手寫 12 列對照表 `HAND_WRITTEN_BADGES`（`1100/1101/1110/1111`→estimated、`1000/1001/1010/1011`→carried_over、`0-00`→never_estimated、`0-01`→unconfirmed、`0-10/0-11`→refused）。DOM 比對用 `toHaveText(WINDOW_BADGE_ZH[expected])`，不用產品常量 |
| 產品仍然要對得上 | `estimateState()` 只在**交叉比對**那段出現一次（`dailyEstimate.test.mjs` 斷言 `JOURNEY.split('estimateState({').length - 1 === 1`）；標籤漂移另有守門：spec 的五個字串必須逐字等於 `ESTIMATE_STATE_LABELS_ZH` |
| 判準被證明有辨別力 | 三支突變（改欄位讀法／改手寫表／改 DOM 期望）各自讓 lane 出現預期失敗，之後 `cmp` ＋ `shasum` 逐位元組還原 |
| 未碰到的狀態 | 一律 **NOT EXERCISED**，且 log 同時印 `cards=…`／`ledger_derived=…`／`screen_rendered=…`／`not_exercised_by_this_stack=…` 兩欄（不再只用一個數背書） |

### 13.4 ④A／B／C／D 四張卡（**一卡＝一個可單獨批准的操作**；**放置 ≠ 執行**；本輪一律未執行）

> 每張卡自含全部前提、命令、判準與停止線，**不引用「見 §11.6」**。四張卡的共同紀律：①**身份核對在任何寫入之前**；②跳過＝**NOT RUN**，不是通過；③停後**保留現場**（`$RUN`／`$PGDATA`／capture 檔），不刪、不重試、不改設定碰運氣；④**只管理本包自己記下的 pid**（啟動時把 pid 寫進 `$RUN/pids`，停止只對那些 pid 發 `TERM`），**不从 `pgrep` 結果裡挑別人的行程**。

#### 13.4-A **A 卡：在一台全新隔離 PostgreSQL 16.2 叢集上，跑「四門 driver 行為」＋「席位鎖競態」共 14 支具名節點**

**這個操作包含**：initdb、啟動、建 1 個庫＋2 個角色、`alembic upgrade head`、14 支節點。
**不包含**：任何既有／受保護實例（不連接、不重用、不停止）、`downgrade`、`drop`、autogen、第二輪 migration、部署、瀏覽器、cron、Git 寫入。

**A-0 任何變更之前的身份核對**（未過 ⇒ 停，不建任何東西、不 kill、不覆寫）：

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
PGB=$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin
RUN=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r18-pg-XXXXXX")       # 本包唯一寫入目錄
export PGDATA="${TMPDIR:-/tmp}/alethra-r18-pgdata"             # 必須不存在
export SOCKDIR="${TMPDIR:-/tmp}/alethra-r18-sock"              # 必須不存在
pgrep -fl postgres; ls -d "$PGDATA" "$SOCKDIR" /tmp/.s.PGSQL.*
for p in 5432 5433; do lsof -nP -iTCP:$p -sTCP:LISTEN | tail -n +2 | wc -l; done
cd "$REPO" && git rev-parse HEAD && git status --porcelain | shasum -a 256
```

五項必須逐字等於：**HEAD `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`**、**porcelain 224 paths／`c67ee8e9f34b152f1bdb29deed566c83893a2f9ea73f8930c8ba026c74a8f272`**、**python 樹 digest `699cd4b82d25488797a158dce68955043159774c92fb20e338eedd18cd565883`**（本節所有引用行號綁這支，若不符 ⇒ 行號作廢、重測後才跑）、三條 `No such file`／`no matches`、兩埠 `0`、無 postgres pid。

**A-1／A-2 建叢集＋啟動（資源上限就這兩個數）**

```bash
"$PGB/initdb" -D "$PGDATA" --auth-local=trust --auth-host=scram-sha-256 -E UTF8 --no-sync
"$PGB/pg_ctl" -D "$PGDATA" -o "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB" -l "$RUN/pg.log" start
"$PGB/pg_isready" -h 127.0.0.1 -p 5432; echo "ready rc=$?"
```
非零 ⇒ 停並保留 `$RUN/pg.log`；**不重試、不刪 `PGDATA`**；**只聆聽 127.0.0.1**。

**A-3 一個庫＋兩個角色（DDL／DML 分家，四旗標必須全 `f`）**

```sql
CREATE ROLE alethra_r18_migrator LOGIN PASSWORD '<openssl rand -hex 12，寫入 $RUN/roles.sql(600)，不印、不進命令列>';
CREATE DATABASE alethra_owner_test OWNER alethra_r18_migrator;
\c alethra_owner_test
REVOKE ALL ON SCHEMA public FROM PUBLIC;                     -- PG15+ 預設已收；幂等保護
GRANT USAGE, CREATE ON SCHEMA public TO alethra_r18_migrator;
CREATE ROLE alethra_r18_app LOGIN PASSWORD '<另一支隨機值，同上>';
GRANT CONNECT, TEMP ON DATABASE alethra_owner_test TO alethra_r18_app;
GRANT USAGE ON SCHEMA public TO alethra_r18_app;
ALTER DEFAULT PRIVILEGES FOR ROLE alethra_r18_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO alethra_r18_app;
ALTER DEFAULT PRIVILEGES FOR ROLE alethra_r18_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO alethra_r18_app;
```
校驗來源（本輪重讀）：庫名白名單 `backend/test_safety_gate.py:25-29`（`alethra_owner_test` 在 `:29`）、實例白名單 `:41`、檢查順序 `:168-172`、庫名比對 `:215`、實例比對 `:223`、四旗標查詢 `:110`（superuser／createdb／createrole／bypassrls，任一為真即硬 FAIL，訊息 `:127-128`）；lane 另加名前綴 `ALETHRA_TEST_REQUIRED_DB_PREFIX` 預設 `alethra_`（`scripts/run_tests.sh:178`）。⚠️ `REVOKE ALL ON SCHEMA public FROM PUBLIC` 那句屬 **UNVERIFIED 設計**（本機至今沒有 PG 跑過，「少了它 alembic 落不了地」從未驗證）；A-4 若因此失敗 ⇒ 停線回報，**不臨場加權**。

**A-4 空庫升全鏈（57 支 revision，單一头）**

```bash
cd "$REPO/backend" && .venv/bin/python -m alembic -c alembic.ini heads
cd "$REPO/backend" && .venv/bin/python -m alembic -c alembic.ini upgrade head
```
本輪以 `Alembic ScriptDirectory` 現讀：**`heads == ['d4e5f6g7h8i9']`、總數 57、`script_location = migrations`（`alembic.ini:6`）、`backend/migrations/versions/` 57 支檔**。完整鏈（head → base，逐字）：

```
d4e5f6g7h8i9 <- c3d4e5f6g7h8 <- b2c3d4e5f6g7 <- f2a3b4c5d6e7 <- e1f2a3b4c5d6 <- d0e1f2a3b4c5
<- c9d0e1f2a3b4 <- add_sacrificial_test_table <- drop_old_source_constraints <- rm_residual_global_constraints
<- fix_source_dedup_scope <- add_source_pinning <- p4p5t6r7n8g9 <- l3a4r5n6i7g8 <- a7c1e4f9b2d5
<- f8a9b0c1d2e3 <- d5f6a7b8c9d0 <- c4e5f6a7b8c9 <- c3d4e5f6a7b8 <- e7f8a9b0c1d2 <- z0a1b2c3d4e5
<- y9z0a1b2c3d4 <- x8y9z0a1b2c3 <- w7x8y9z0a1b2 <- v6w7x8y9z0a1 <- u5v6w7x8y9z0 <- t4u5v6w7x8y9
<- s3t4u5v6w7x8 <- r2s3t4u5v6w7 <- q1r2s3t4u5v6 <- p0q1r2s3t4u5 <- o9p0q1r2s3t4 <- n8o9p0q1r2s3
<- m7n8o9p0q1r2 <- l6m7n8o9p0q1 <- k5l6m7n8o9p0 <- j4k5l6m7n8o9 <- h8i9j0k1l2m3 <- 8616c0c9aa3e
<- h2b3c4d5e6f7 <- a1b2c3d4e5f7 <- a8d9e0f1b2c3 <- g1a2b3c4d5e6 <- f6a7b8c9d0e1 <- e5f6a7b8c9d0
<- c7e2f9a41b83 <- d4e5f6a7b8c9 <- add_state_version <- e3f4a5b6c7d8 <- d2e3f4a5b6c7 <- c1d2e3f4a5b6
<- b7d8e9f1a2c3 <- b2c3d4e5f6a7 <- b7c8d9e0f1a2 <- a1b2c3d4e5f6 <- 3f9c2d81b7a4 <- 55a8d07a7f40
```
停止：`heads` 多於一／head ≠ `d4e5f6g7h8i9`／`DUPLICATE_REVISION`｜`BRANCHED_TREE`｜`MISSING_DEPENDENCY` ⇒ 停；**不 autogen、不 downgrade、不 drop、不跑其他 migration**。

**A-5 schema 讀回（唯讀）——它證不了 A-6 的事**：`\dn`、`SELECT current_database(), version();`、`information_schema.columns`／`pg_indexes` 查 `paper_orders.uq_paper_order_portfolio_idempotency` 與 `human_decisions.uq_human_decision_owner_session_key` 存在、`linked_inference_id` 兩處型別＝`uuid`。
⚠️ **這一格讀到的是「索引在不在」，不是 driver 行為**：psycopg2 是否回 `SQLSTATE 23505`、`origins` 裡的 constraint 名是否等於上面兩個字串、產品 `expected_unique_violation` 在 PG 分支是否**讀的就是那個名字**——全部只能由 A-6 的實際請求證明。**A-5 PASS 不得寫成「衝突在 PG 已驗」。**

**A-6 四門 driver 行為（8 支具名節點；真唯一衝突 → 讀實際 SQLSTATE／constraint → 驗 409）**

```bash
cd "$REPO/backend"
export ALETHRA_PG_TEST_URL="postgresql+psycopg2://alethra_r18_migrator:<讀 $RUN/roles.sql，不印>@127.0.0.1:5432/alethra_owner_test"
export TEST_MODE=true DATA_PROVIDER=demo
./scripts/run_tests.sh pg \
  tests/test_conflict_doors_at_entry.py::test_door_A_answers_only_its_own_index_with_a_conflict \
  tests/test_conflict_doors_at_entry.py::test_door_A_does_not_rename_its_own_not_null_failure \
  tests/test_conflict_doors_at_entry.py::test_door_B_answers_a_conflict_on_the_order_index_it_owns \
  tests/test_conflict_doors_at_entry.py::test_door_B_lets_another_doors_index_through_as_a_500 \
  tests/test_conflict_doors_at_entry.py::test_door_C_does_not_answer_another_doors_order_index_as_its_own \
  tests/test_conflict_doors_at_entry.py::test_door_D_answers_its_own_index_with_a_conflict \
  tests/test_conflict_doors_at_entry.py::test_door_D_does_not_rename_the_order_index \
  tests/test_conflict_doors_at_entry.py::test_the_entries_refuse_an_empty_key_so_the_index_cannot_be_reached_sequentially
echo "lane rc=$?"
```
判準（逐支）：`A`／`B` 的 409 必須由 **`uq_paper_order_portfolio_idempotency`＋23505** 組成；`C`／`D` 必須由 **`uq_human_decision_owner_session_key`＋23505**；**反控**那三支（`B_lets_another_doors_index_through_as_a_500`、`C_does_not_answer_another_doors_order_index_as_its_own`、`D_does_not_rename_the_order_index`）必須是 **500／非 409**，且回應體不得出現 SQL 語句、绑定參數或他人資料；`A_does_not_rename_its_own_not_null_failure` 必須是 NOT NULL（23502）**而不是** 409。這三支反控就是提示要的「至少一個非本門完整性錯誤作反控」。

**A-7 真鎖競態（6 支具名節點：3 支行鎖生命週期＋2 支 advisory 席位＋1 支快照後插隊）**

```bash
export ALETHRA_RACE_CAPTURE_JSON="$RUN/race-capture.json"      # _write_capture() 不建父目錄 ⇒ $RUN 必須已存在
./scripts/run_tests.sh pg \
  tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_two_requests_on_one_idempotency_key_leave_exactly_one_order \
  tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_stale_account_read_cannot_overwrite_a_committed_booking \
  tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_manual_submit_racing_an_advance_stays_consistent \
  tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier \
  tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once \
  tests/test_daily_model_estimate.py::test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice
```
本輪在 sqlite 層量到的**逐字跳過理由**＝這 4 支欠的就是 PG：`the seat being raced is pg_advisory_xact_lock, which SQLite has no way to take`（`other=1`）、`row-lock serialisation requires a real PostgreSQL backend`（`pg_only=3`）；另 2 支在 sqlite 已 pass（證的是決定性雙交易，不是真鎖）。
計時契約（本輪現測 `tests/test_daily_estimate_two_workers.py`）：`_TIMEOUT_S = 60`（`:170`）、`SET lock_timeout = '30s'`／`statement_timeout = '120s'`／`idle_in_transaction_session_timeout = '90s'`（`:241-243`）。
帳本判準（不得以更便宜的東西代替；`tests/daily_estimate_race.py:131 assess_race()` 共 12 條）：每 `(owner, skill, instrument, window)` 恰 1 列、`metadata["via"] == "daily_service"`（常量 `app/services/daily_estimates.py:77`）、每列恰 1 筆 `settled` 預留、非終態預留＝0、落敗那支具名 `already_estimated_this_window`（`tests/daily_estimate_race.py:91`）、兩個 job 摘要的 `billed_in_window` **不相加**。12 條：`observer_visibility`／`wait_observed`／`blocker_is_holder`／`lock_object_matches`／`key_unbilled_while_blocked`／`bill_durable_after_release`／`holder_claim_was_free`／`loser_reread_refused`／`one_bill_per_business_key`／`settlement_matches_bills`／`quota_never_stands_in_place_of_ledger`／`refusal_reason_is_the_ledger`。
**額度前提：遷移帳號的 `ai_usage_limit ≥ 4`**（預設 10，`app/auth/entitlements.py:70`）。**B 卡那個 1 不得套進這裡**，否則 `reserve_quota`（`:252`）的 429 會把真缺陷蓋成限流。`Barrier` 未湊齊、計時未滿、`proven=false` ⇒ 該次 **NOT RUN／INVALID**，**不得寫成 PG 競態 PASS**。

**A 卡專屬停止線**：非零 exit／閘 rc 2（`run_tests.sh:170-173` 拒空 `ALETHRA_PG_TEST_URL`）或 rc 3（`:183-187` preflight）／head 不符／四旗標不為全 `f`／`current_database() != alethra_owner_test`／反控那三支出現 409／跳過取代執行／斷言失敗。
**A 卡專屬停止命令**（先停測試行程，再停叢集；**停止 ≠ 刪資料**）：

```bash
xargs -a "$RUN/pids" -n1 kill -TERM 2>/dev/null || true      # 只停本包記下的 pid
"$PGB/pg_ctl" -D "$PGDATA" -m fast stop
"$PGB/pg_isready" -h 127.0.0.1 -p 5432; echo "expect non-zero"
lsof -nP -iTCP:5432 -sTCP:LISTEN | tail -n +2 | wc -l        # 應為 0
```
**不得**以已停端點的 HTTP 回應證明停止；**不刪 `$PGDATA`**（留給 owner 或另批清理）。

#### 13.4-B **B 卡：自然 cron（排程器 05:00 UTC tick）在 A 卡那同一支庫上跑一次，驗「額度路徑」與「只註冊一支 lane」**

**這個操作只有一個**：讓 `learning_scheduler` 依 **UTC 時鐘自己**觸發 `daily_model_estimate` 一次，讀帳本與 log。**不改時鐘、不用手動入口（`scope="manual"`，`daily_estimates.py:1309`；`trigger_daily_estimate` 定義在 `:1280`）冒充 cron。**

**B-0 設定來源必須由「工作目錄」證明，`env -i` 不能代替**（本輪實測，非推論）：`app/config.py:560` 是 `model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")`——**相對路徑，依行程 cwd 解析**。同一支 venv、同一份程式碼：

| cwd | `Settings()` 讀到的三個承重值 | 讀法 |
|---|---|---|
| `…/alethra/backend` | `data_provider='twelvedata'`、`research_auth_bypass_enabled=True`、`live_trading_enabled=True` | `Settings()`（`.env` 生效） |
| `/private/tmp` ＋ `PYTHONPATH=$REPO/backend` ＋ `env -i` | `data_provider='demo'`、`bypass=False`、`live=False`、`enable_learning_scheduler=False` | `Settings()`（**檔不在 cwd，從未被讀**）；`import app.main` **rc 0、331 條 route** |

⇒ **服務必須在 `$RUN` 啟動**（`cd "$RUN"`），用 `PYTHONPATH=$REPO/backend` 找 `app`，**不得 `cd backend`**（那正是 `start_candidate_stack.sh:396` 與 `run_tests.sh:265` 的做法，也正是 `.env` 生效的原因）。`env -i` 只是第二道，不是那道證明。

**B-1 啟動命令（白名單非秘密值；秘密只有隨機 JWT）**

```bash
cd "$RUN"
export JWT_SECRET_KEY=$(/usr/bin/openssl rand -hex 16)   # 寫 $RUN/jwt-secret.txt(600)，不印
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME="$HOME" PYTHONPATH="$REPO/backend" \
  DATA_PROVIDER=demo AI_PROVIDER=mock APP_ENV=development \
  ENABLE_LEARNING_SCHEDULER=true LEARNING_SCHEDULER_JOBS=daily_model_estimate \
  AUTO_COLLECTION_ENABLED=false RESEARCH_AUTH_BYPASS_ENABLED=false LIVE_TRADING_ENABLED=false \
  DATABASE_URL="postgresql+psycopg2://alethra_r18_app:<讀 $RUN/roles.sql，不印>@127.0.0.1:5432/alethra_owner_test" \
  "$REPO/backend/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8199 >"$RUN/service.log" 2>&1 &
echo $! > "$RUN/pids"
```
`LEARNING_SCHEDULER_JOBS` 必須**明名一支**：預設空字串（`app/config.py:148`），空 ⇒ 取 `DEFAULT_LEARNING_SCHEDULER_LANES` 那三支（`config.py:55-59`，本輪現讀為 `daily_research`／`daily_simulation`／`weekly_analysis`）——**那三支裡沒有 `daily_model_estimate`**，所以不明名就根本不會觸發本卡的計費 lane（「非計費」這個定性屬第十四輪的量測，本輪只重測了名字與數量）。`ENABLE_LEARNING_SCHEDULER` 預設 False（`config.py:140`），唯一讀者 `app/main.py:52`。兩個釘值是**消歧不是防範**，如實標：`RESEARCH_AUTH_BYPASS_ENABLED` 全樹只有 1 個消費者（`app/auth/jwt.py:51`），`LIVE_TRADING_ENABLED` 在 `app/**` 下**0 個消費者**（本輪 `grep -v config.py` 現測）。應用帳號 `alethra_r18_app` **只有 DML**（A-3）⇒ 它連 `CREATE` 都沒有，DDL 走不通不是靠承諾。

**B-2 判準分成三格，不互換**：

| 格 | 內容 | 本輪狀態 |
|---|---|---|
| **額度路徑驗收** | `ai_usage_limit=1`（預設 10 ⇒ 必須主動設 1 才有辨別力）：第一支 instrument 扣額成功＋**一次結算**，第二支撞 429，且該次 tick **留一筆具名拒因**而非靜默少寫 | 未執行 |
| **去重** | ⚠️ 上面那格**不去重證明**——它證的是配額分支。同一 UTC 日內只計費一次**必須**由 A-7 的業務鍵 12 條判；兩格分列，**不得互相代替** | 未執行（且本卡不宣稱） |
| **開關停用 ⇒ 零新增結算** | 獨立觀察：`ENABLE_LEARNING_SCHEDULER=false`（或 allow-list 未設）重啟一次，`learning_jobs`／`usage_records` 新增列數＝0 | **NOT RUN**（本輪未啟動任何行程） |

**B-3 零外呼的證據是連線層，不是 log 字串**：主證據＝`lsof -nP -p "$(cat $RUN/pids)" -a -i` 中對端**非** `127.0.0.1`／`::1` 的列數＝**0**；`env -i` 讓 demo／mock 成為唯一提供者（這是機制，不是保证）。⚠️ 「log 裡 grep 不到 URL」**不單獨成立**（外呼可在寫 log 前失敗、或走沒被 grep 的拼字）⇒ 只當旁證。另註：`config.py:214-216` 的 `is_production_like` 只認 production／staging，本機 development **不受該 validator 保護**。

**B-4 資源上限**：`LEARNING_SCHEDULER_MAX_OWNERS` 維持預設 25（`config.py:153`）；只種 **1 owner／1 skill／2 instrument**（遠低於 `daily_estimates.py:87-88` 的 10／8）；DB 端就是 A-2 那兩個數（25 連線／64MB）。

**B 卡停止線**：①90 秒內無 `learning_jobs` 列 ⇒ 停並存 `$RUN/service.log`；②任一 `KillSwitchScope` active（global／training／execution／inference，`app/models/kill_switch.py:10-15`；計費 lane 查 `inference`）⇒ 該 tick **必須**零寫帳，若寫了帳就是本卡要抓的缺陷；③啟動日誌出現第二支 lane ⇒ 立即 `stop_learning_scheduler()`（`app/main.py:72`）。
**B 卡停止命令**：`kill -TERM "$(cat $RUN/pids)"`（**只這個 pid**）→ `lsof -nP -iTCP:8199 -sTCP:LISTEN | tail -n +2 | wc -l` 應為 0 → 再依 A 卡末段停叢集。**不刪 `$PGDATA`。**

#### 13.4-C **C 卡：只在 8101／3101 的 dev 功能驗收棧上，跑既定兩支 spec（結果一律標 dev）**

**這個操作只有一個**：起 dev 棧 → 跑那兩支 spec → 收四個數。**不掃 312 支 e2e、不代表 production build 驗收。**

**C-0 三件事必須分開列**（提示原文要求）：

| 類別 | 具體內容 | 誰可以做 |
|---|---|---|
| **瀏覽器自動化** | 打開 3101、按 submit／review、讀 DOM、抓 request／response、截圖 | 有正確 scope 的 agent（本卡批准範圍內） |
| **本人 grant／revoke** | `data-testid="daily-estimate-acknowledge"`（`src/components/training/DailyEstimatePanel.tsx:676`）與 `daily-estimate-revoke-all`（`:695`） | **只能本人親手按**。spec 只斷言按鈕存在與文案正確，**從不按它**；代理不替本人同意或撤回，**本卡文字本身不構成同意** |
| **可能扣額的操作** | 只有設了 `E2E_DAILY_ESTIMATE_SKILL` 才會走的那幾格：`frontend/e2e/daily-estimate-journey.spec.ts:108` `const PAYING_SKILL = process.env.E2E_DAILY_ESTIMATE_SKILL ?? ''`，未設 ⇒ `:111` 具名跳過 | 未獲准扣額 ⇒ 保持未設 |

**C-1 `playwright --list` 不是「只是列一下」（已查明其副作用，因此未執行）**：`frontend/playwright.config.ts:5` `const FRONTEND_URL = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3001'`；`:24` `baseURL: FRONTEND_URL`；`:36-38` `webServer: { … url: FRONTEND_URL }`——`webServer.url` 讓**任何** Playwright 命令（含 `--list`）先去**探測／拉起**那個 origin ⇒ 「先列一下」本身就是啟動服務的動作。**本輪未執行任何 Playwright 命令**（含 `--list`），配置載入與探測副作用已查明如上。

**C-2 起棧（唯一入口）＋三個必須明釘的值**

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
cd "$REPO" && export DATA_PROVIDER=demo AI_PROVIDER=mock RESEARCH_AUTH_BYPASS_ENABLED=false LIVE_TRADING_ENABLED=false
./start_candidate_stack.sh        # 後端 127.0.0.1:8101（腳本 :396-397 起 uvicorn），前端 next dev 3101（:441-446，NEXT_DEV_DIST_DIR=.next/candidate-3101）
```
第三個值是**本卡必須揭露的風險**：`RESEARCH_AUTH_BYPASS_ENABLED` 在該棧上會讓 `app/auth/jwt.py:51` 以「第一位 active user」放行 loopback 請求，**把「本人操作」那一格掏空** ⇒ 不釘 false 就不准跑 C。腳本 `:182-215` 的匯入清單**不含**這三個名字，而 `:396` 把 cwd 放進 `backend/` ⇒ `backend/.env` 會生效（見 B-0 的實測表）——這是 C 卡**不能只靠 `env -i`** 的同一条理由。`LIVE_TRADING_ENABLED` 如實標：**0 個消費者**，釘它是消歧。
Docker 路線不在本卡：`command -v docker` 不存在 ⇒ `start_acceptance_env.sh` 在碰任何行程之前 rc 2（第 13 輪實測到那條拒路）。

**C-3 只跑這兩支**

```bash
cd "$REPO/frontend"
E2E_FRONTEND_BASE=http://127.0.0.1:3101 E2E_BACKEND_BASE=http://127.0.0.1:8101 \
E2E_BROWSER_CHANNEL=chrome E2E_CRED_FILE=<本機憑證檔> \
node_modules/.bin/playwright test e2e/manual-decision-lineage.spec.ts e2e/daily-estimate-journey.spec.ts
```
`E2E_BROWSER_CHANNEL=chrome` 現測可用（`/Applications/Google Chrome.app` 存在；`~/Library/Caches/ms-playwright` 0 條 ⇒ bundled chromium 需另批下載）；`@playwright/test` 1.64.0。spec 自己的前置（跳過就是跳過，會具名）：`E2E_CRED_FILE`（`e2e/runtime.ts:34`；缺 ⇒ 檔案層級 `TIER_B_SKIP`，`runtime.ts:26-30`）、`E2E_LINEAGE_ASSET`（預設 `DEMO-ALPHA`，`manual-decision-lineage.spec.ts:111`）、`E2E_DAILY_ESTIMATE_SKILL`（見 C-0 第三列）。這三個名字是「哪一格被誰允許跑」的開關，**代理不代填**。

**C-4 判準**：①`/health/ready` 通過；②預檢回應含 `access-control-allow-origin: http://127.0.0.1:3101`（`app/main.py:27` 只讀 `settings.cors_origins_list`，預設 `http://localhost:3000`，`config.py:79`——`localhost` 與 `127.0.0.1` **不同 origin**）；③**起棧前**讀 `cd backend && .venv/bin/python -m alembic heads`（本卡預期 `d4e5f6g7h8i9`）、起完再從服務讀 `schema-head` 對照。⚠️ 這裡要揭露一個**副作用**（取代第十六輪「腳本會比，不符就拒起」那句不準確的說法）：`start_candidate_stack.sh:575` 讀 `heads`，而 `:585` 與 `:628` 會對該腳本自己那支 `DATABASE_URL` **執行 `alembic upgrade head`** ⇒ **起棧本身是 DDL 動作，不是唯讀**；把它指向 A 卡那支 `alethra_owner_test` 等於在那支庫上多跑一次 migration ⇒ 兩者要嘛用**不同庫**，要嘛把這次 upgrade 如實記進 A 卡帳本；④只報 **collected／passed／skipped／failed** 四個數，**不拿 collected 當 passed**；⑤spec 內任何 `test.skip(true, …)` 的原因文字**逐字入帳**（前置缺件 ≠ 通過）；⑥任何結果一律標 **dev**，不宣稱等同 production。**按鈕可見不是 grant／revoke 驗收。**
**C 卡停止線**：①／②／③ 任一不符、或任一 spec 在前置齊全的情況下 failed ⇒ 停、存 log、回報；**不重跑、不改設定碰運氣**。
**C 卡停止命令**：讀 `start_candidate_stack.sh` 自己記的 pid 檔並只對其發 `TERM`（**不從 `pgrep` 結果挑別人的行程**）；若同一支棧上起來了 8199（B 卡）另按 B 卡停止命令。
**本輪 C 卡狀態：NOT RUN**（未獲准啟動服務／瀏覽器；且 C-0 第三格尚未填）。

#### 13.4-D **D 卡：在一個新目錄裡用本機來源重建候選，並跑本輪受影響節點（純離線、零網路、零 Git 寫入）**

**如實改名**：本卡**不是**「第 17 輪增量可重現」——現稿沒有第 17 輪的輪前位元組封存，稱它可重現會虛報。本卡是**「第 18 輪增量的重建程序」**，且明確分兩層。

**D-1 先固定「完整候選內容」**（不是本輪 8 檔，而是整個受測物）：

| 層 | 固定方式（本輪現測值） |
|---|---|
| Git 基線 | 完整 SHA `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`，branch `live1-impl` |
| 髒度總數 | **224 paths**（141 `M` ＋ 83 `??`），porcelain 清單 sha256 `c67ee8e9f34b152f1bdb29deed566c83893a2f9ea73f8930c8ba026c74a8f272` |
| 後端（`*.py`）內容 | python 樹 digest `699cd4b82d25488797a158dce68955043159774c92fb20e338eedd18cd565883`；⚠️ **涵蓋範圍**：`tree_digest()`（`backend/scripts/run_tests.sh:373-379`）只雜湊 repo 內 `*.py` ⇒ **前端／TS／配置檔不在內**，必須逐檔 sha256 |
| 本輪 8 檔（重現承重） | `6672d853a11059e285af34db804a73d004d5adc4f88fda1dd3ad379f69ecbfac` `backend/app/services/daily_estimates.py`（1,592 行）<br>`a2bc0a903ec661ac9cfc34c201b06f7fc393a8d615e6c30e301d349f521fbe8f` `backend/tests/test_conflict_doors_at_entry.py`（723 行，新）<br>`3a8aaa77b083e6ed0fb16bf8835091be410e77c0020540a3cf50caae4180f113` `frontend/src/lib/reviewCurrency.ts`（70 行，新）<br>`36ed4ea8773d52f7f94afd3078049d674f36a2168a2218f07ba1b1a996cd35bf` `frontend/src/components/workspace/OrderPreviewPanel.tsx`（1,486 行；vs HEAD ＋197／−15）<br>`e319daab7438f034f773914f8a3cd95ae0d868c1348885ce54b80c39e8cb2f69` `frontend/e2e/manual-decision-lineage.spec.ts`（810 行）<br>`16c1e9fb55122db7d783d2eb98fbb434600ff7528b5020681b0134431bdaedcb` `frontend/e2e/daily-estimate-journey.spec.ts`（673 行）<br>`5292a51261734fe608375e2ace9f3aea59718615bf4fd5b5ce006cc94ce699a6` `frontend/tests/dailyEstimate.test.mjs`（1,243 行）<br>`f3fc0ac2fb40449205da5be025e11a5e98c4bfb04f9a9e554b3264ed1409319b` `backend/app/api/simulations.py`（2,738 行；vs HEAD ＋391／−94，**跨輪混合**） |
| 測試依賴（本輪 lane 實際 import 的未動檔案） | 只能由**完整工作樹**提供：`tests/conftest.py`、`tests/daily_estimate_race.py`、`app/**` 其餘模組、`migrations/**` 57 支 ⇒ 明說：**只有上面 8 檔無法重建整套** |

**D-2 第 17 輪能證明的增量 vs 第 18 輪的增量（分列，不混帳）**：第 17 輪那批＝`conflicts.py`（mtime 18:43:31）與 `human_decisions.py`（18:44:29）＋§12.6 那 9 檔，其證據綁 `2ec1ef9c…`；第 18 輪那批＝上面 D-1 表中 21:0x–21:4x 寫入的 8 檔，證據綁 `699cd4b8…`。**沒有輪前內容證據的路徑 ⇒ 標「歸屬未證」**：本輪無法把 `simulations.py` 的 ＋391 拆成哪一段屬於哪一輪（`git diff` 對它是跨輪混合，HEAD 完全不含每日估計功能），也不拿 mtime 或「porcelain 前後差 6 檔」這種算術代替歸屬。

**D-3 本機依賴方案（已核對：不裝依賴、且不會從原 checkout import）**——本輪逐項靜態核對過 `backend/.venv`（470 MB）：

| 檢查 | 結果 |
|---|---|
| `bin/python3.12` | symlink → `/Users/tommylam0915/.local/bin/python3.12`（**venv 之外**，非 checkout） |
| `pyvenv.cfg` | `home = /Users/tommylam0915/.local/bin`；`executable = …/uv/python/cpython-3.12.16-macos-aarch64-none/bin/python3.12`；`include-system-site-packages = false` ⇒ 基底直譯器在 checkout 外 |
| site-packages 內是否釘回原 checkout | `find lib -maxdepth 3 -name "*.pth"` ＝ 3 支（`pytest-cov.pth`、`distutils-precedence.pth`、`a1_coverage.pth`），內容全是 coverage 鉤子、**無任何路徑**；`*.egg-link`／`*editable*` **0 支** ⇒ 沒有 editable 安裝把 `app` 包指回原處 |
| 唯一的原處引用 | `bin/` 38 支裡 **34 支 shebang 寫死 `#!/…/ac51e592/alethra/backend/.venv/bin/python3.12`** ⇒ **方案規定：入口一律 `$DST/backend/.venv/bin/python -m pytest …`，禁止用 `bin/pytest`、`bin/alembic`** |
| 需不需要裝依賴 | 不需要：`requirements.txt` 24 行全部已在 `.venv`（含 `psycopg2_binary-2.9.11`）；`uv` 0.13.7 與 329 MB 快取存在，但那是**另一種方案（要裝）⇒ 不在本卡**，本卡不碰安裝 |
| 尚未做的那一步 | **動態核對**（真的 `cp -R` 一份、在 `$DST` 跑一支節點、驗 `app.__file__` 落在 `$DST`）屬 D-4 第 3 步 ⇒ **本輪未執行 ⇒ NOT RUN**。本卡交的是「靜態核對可用」，不是「已實測可用」 |

**D-4 逐條命令（一個步＝一個可單獨否決的動作；寫入路徑只有 `$DST`）**

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
DST=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r18-repro-XXXXXX")            # 唯一寫入目錄
1  mkdir -p "$DST/repo" && (cd "$REPO" && git ls-files -z | xargs -0 tar cf - ) | (cd "$DST/repo" && tar xf -)   # 只取已追蹤檔，零網路
2  cd "$REPO" && git diff HEAD -- backend/app/api/simulations.py > "$DST/r18-simulations.patch"                  # 讀；不 apply
3  cp -R "$REPO/backend/.venv" "$DST/repo/backend/.venv"                                                          # 470 MB；D-3 那條 shebang 禁令在這裡生效
4  複製 D-1 那 8 支原檔（逐檔 `cp`，不 `git apply`）到 "$DST/repo/…"，再逐檔 `shasum -a 256 -c` 比對 D-1 八個雜湊
5  cd "$DST/repo/backend" && ./scripts/run_tests.sh sqlite tests/test_conflict_doors_at_entry.py   # **不用裸 pytest**（提示明文禁止）；`run_tests.sh:76-77` 的 `ROOT`/`BACKEND` 由腳本自身路徑推得、`:120` 先找 `$BACKEND/.venv/bin/python` ⇒ 在 `$DST` 裡跑的就是**副本的 venv**，這一步同時是 D-3 那個動態核對
6  cd "$DST/repo/frontend" && /Users/tommylam0915/.local/bin/node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyEstimate.test.mjs
```
判準：第 4 步八支雜湊**逐字相同**（一支不同 ⇒ 停，那表示來源不是本節描述的內容）；第 5 步 **8 collected／8 passed／rc 0** ＋ 該 run 自己的 `identity.txt` 顯示 `cwd` 與 `interpreter` 都在 `$DST` 之下（這就是「不是從原 checkout import」的實物證據，比手寫 `import app` 更硬，且由 lane 自己印出）；第 6 步 **44 passed／rc 0**。
**本卡不含任何網路步驟**（已移除 `git ls-remote`：本機重現不需要、也不應該碰網路；遠端核對屬另一個獨立操作，本輪不做）。
**停止線**：任一非零 exit／任一雜湊不符／`app.__file__` 落在 `$REPO` ⇒ 停並保留 `$DST`，**不重試、不刪 `$DST`**。**只管理本包記下的子行程**（第 3 步的 `cp`、第 5–6 步的 test pid），**不從 `pgrep` 結果選別人的行程**。**本卡未執行。**

**D-5 明列「不能重現什麼」**（不美化）：
1. **A 卡／B 卡／C 卡的任何結果**都不在 D 卡範圍：D 卡只有 in-process 與純檔案層，**本機無 PG 執行、無服務、無瀏覽器**。
2. `simulations.py` 的跨輪 diff **無法**由 D 卡附件還原本機各輪中間態（只有 HEAD 與當前兩點）。
3. 83 支 `??` 未追蹤檔中，本輪未動的那些只能靠「當前工作樹整份」帶过去；D-1 只交 8 檔 ⇒ 這不是完整發布集。
4. 發布集合由誰選、怎麼選，仍是**未批准**的獨立決定（§9.12 D-3）。
5. 第 17 輪的 `2ec1ef9c…` 樹**沒有**輪前位元組封存 ⇒ 該樹只能由當時的 run dir 產物間接支持。

### 13.5 ⑤收斂驗證（一次，綁 `699cd4b8…`；不與改動並行）

| 層 | 命令 | 結果（原始，不補跑子集） |
|---|---|---|
| 後端 sqlite 層 | `cd backend && ./scripts/run_tests.sh sqlite tests/test_conflict_doors_at_entry.py tests/test_conflict_recognition.py tests/test_decision_conflict_repro.py tests/test_predict_kill_switch_boundary.py tests/test_predict_record_route_explicit_version.py tests/test_daily_model_estimate.py tests/test_daily_estimate_two_workers.py tests/test_learning_scheduler.py tests/test_manual_decision_books_linked_portfolio.py` | **183 collected／179 passed／4 skipped／0 failed／0 error**，pytest exit **0**；`skip categories pg_only=3 other=1`；`evidence gate: ledger closes against this run's own artifacts`；run dir `sqlite-20261120T135744Z-39627`（`tree-digest-before == after == 699cd4b8…`；`selection-kind: explicit-selection` ⇒ **不是整套**） |
| 前端離線層 | `cd frontend && node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/*.test.mjs` | **202／202 pass，0 fail，0 skipped，rc 0**（14 檔；本輪 +1 節點＝§13.3b 的 oracle 守門） |
| 前端型別層 | `cd frontend && npx tsc --noEmit` | **rc 0**（無輸出；退出碼以 redirection 取得，不是 pipe 末端） |
| 瀏覽器層 | Playwright（含 `--list`） | **NOT RUN**（未獲准；`--list` 的探測副作用已查明 ⇒ 連列目錄都算啟動服務，見 C-1） |
| PG 層 | `./scripts/run_tests.sh pg …` | **NOT RUN**（本機無執行中 PG；A 卡未批准） |
| 舊證據 | §12.5 的 `2ec1ef9c…` 203／199／4 | 未重跑（樹已換成 `699cd4b8…`）；受本輪改動直接影響的節點已全部落在上面的 183 之內 |

### 13.6 能力證據總表（**整體交付仍未結案**；已證不重做，缺口如實）

| 能力 | 目前最高證據等級 | 綁定身份 | 仍欠什麼 |
|---|---|---|---|
| 技能與策略研究 | 本地已寫＋無 DB 已測（MB-14／18／19 研究程式碼與測試，研究結論已結案：8 個家族全 FAIL） | 早輪提交（`79d3977`／`62a126f`）＋ `2ec1ef9c…` | 不是本輪範圍；本輪未動 |
| 訓練（learning jobs／proposal） | 無 DB 已測（`tests/test_learning_scheduler.py` 27 節點在本次 183 內全 PASS） | `699cd4b8…` | PG 層、真排程觸發＝A/B 卡，未批准 |
| artifact 真載入＋成功推論 | 無 DB 已測（`tests/test_daily_model_estimate.py` 48 節點在本次 183 內全 PASS） | `699cd4b8…` | 「成功推論」在 PG 上的席位競態＝A-7；真外呼不適用（mock／demo） |
| activation approval（同意／撤回） | 本地已寫＋無 DB 已測；**本人操作那一格只有測試代理** | `699cd4b8…`（`grant_daily_estimate` 路徑） | **本人實際 grant／revoke**（C-0 第二列，只能親按）；撤回切版在畫面層的真實點擊＝C 卡 NOT RUN |
| paper engine | 無 DB 已測（本次 lane 的 simulations／portfolio 節點） | `699cd4b8…` | 兩條真交易重疊＝A-7 的 3 支 `pg_only`；跨連線併發本機做不到 |
| 決策 lineage | 無 DB 已測（四門 8 節點本輪全 PASS，走 HTTP 入口＋獨立讀回） | `699cd4b8…` | PG driver 實際 SQLSTATE／constraint＝A-6；瀏覽器三層讀回＝C 卡 NOT RUN |
| 每日服務（cron lane） | 本地已寫＋**設定載入路徑本輪實測**（B-0 兩行對照） | `699cd4b8…` ＋ 本輪 `/private/tmp` 量測 | 自然 cron 一次＝B 卡未批准；「開關停用⇒零新增結算」獨立觀察 NOT RUN |
| UI／client | 離線層 202／202 ＋ `tsc` rc 0；spec 已寫到能驗 409 不 reload | `699cd4b8…`（前端不在 py digest 內 ⇒ 以 §13.4-D-1 的 sha256 逐檔綁） | 瀏覽器 NOT RUN；同窗口切換最新估計**維持未驗證**；production build 未做 |

### 13.7 本輪寫入清單與副作用揭露

| # | 路徑 | 狀態 | 歸屬 |
|---|---|---|---|
| 1 | `backend/tests/test_conflict_doors_at_entry.py` | **新增**（723 行） | ①四門入口證明 |
| 2 | `backend/app/api/simulations.py` | `M`（本輪只動 B 門守衛起點；累計 ＋391／−94 屬**跨輪混合**） | ①由節點挖出的真缺陷修復 |
| 3 | `frontend/src/lib/reviewCurrency.ts` | **新增**（70 行） | ②409 恢復判準 |
| 4 | `frontend/src/components/workspace/OrderPreviewPanel.tsx` | `M`（本輪段＝409 原地重讀） | ② |
| 5 | `frontend/e2e/manual-decision-lineage.spec.ts` | `??`（368→810 行） | ②（改寫既有 spec，未開第三支） |
| 6 | `backend/app/services/daily_estimates.py` | `??`（本輪只動文件段與引用） | ③(a) |
| 7 | `frontend/e2e/daily-estimate-journey.spec.ts` | `??`（448→673 行） | ③(a)(b) |
| 8 | `frontend/tests/dailyEstimate.test.mjs` | `??`（本輪 +1 節點） | ③(b) 的 oracle 守門 |
| — | `EXECUTION_TABLE_20261117.md` | `M` | **僅附加 §十三**；§一～§十二未改寫（其中 §11.6／§11.7／§11.8／§11.9 的四張卡**由 §13.4 取代**，原文保留不刪） |

**副作用如實**：本輪另有 3 次讀取性質的執行——①`Settings()` 兩次（`cwd=backend`／`cwd=/private/tmp`）與 `import app.main` 一次（在 `mktemp -d` 建的空目錄，結束即 `rm -rf`，該目錄除目錄本身外未寫任何檔；`import app.main` 未開連線、未外呼）；②`Alembic ScriptDirectory` 讀鏈（唯讀，未執行任何 revision）；③第 13.5 節那三条 lane（後端 lane 只寫 `$TMPDIR/alethra-runs/sqlite-20261120T135744Z-39627` 與該 run 自己的 per-pid sqlite 檔 `alethra-sqlite-test-39627.db`，**未碰** `backend/test.db`）。**未做**：Git 寫入、重釘、部署、依賴安裝、秘密操作、啟動服務／瀏覽器、PG 執行、cron。

### 13.8 本輪未執行（如實，不美化）

PG 全鏈（A 卡 A-1…A-7）、自然 cron（B 卡）、瀏覽器兩支 spec（C 卡）、本機隔離重建與動態依賴核對（D 卡 D-4）——**四張卡全部只交付正文，一律未執行**；`--list` 因已查明會探測 3101 也未執行。上面 §13.5 的三個 rc 0 是本輪唯一的實測數字，其餘都是待批材料。

---

### 13.9 第十八輪**接續**（2026-11-21 04:06–04:3x UTC）：**推論↔策略版本那一段已接通；A／B／C／D 換成自含正文**（不另開一輪校稿）

#### 13.9.0 本節身份（04:1x 現測；§13.9 每個行號都綁這組）

| 項 | 值（全部由命令現讀，非抄寫） |
|---|---|
| HEAD／branch | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`／`live1-impl`（未變、未做任何 Git 寫入） |
| porcelain | **225 paths**（141 `M` ＋ 84 `??`）清單 sha256 `bcc332c0812c143640533c161c8de7e0b0345012319fa81e7aed1174cf690853` |
| python 樹 digest | **`953c7345fa1cb7f8f5f1f657bec956c0df63167b4120e9e39eab684263fbc80d`**（由第二支 run 自己印的 `tree-digest-before == after`） |
| 與 §13.0 的關係 | ＋1 支新測試檔、4 檔內容改 ⇒ **§13.4 那批行號與 digest 對本節不再適用**；本節每個引用重測過 |

#### 13.9.1 第一個產品斷點（先命名、再最小接通、再驗）

**斷點**：`POST /api/v1/ml/personal/models/{version_id}/predict` 落的帳本列**不寫來源策略版本**，而決策門把「決策命名的版本」與「決策命名的推論」**各自驗證、彼此不比較**。

**證據（現測，不是推論）**：產品裡同時寫兩個欄位的只有一處——`backend/app/api/paper_portfolio.py:1071`（桌面 create）。本輪修改前，同一個 handler 在 `:1000-1003` 驗版本的擁有／凍結／run 一致，在 `:1048-1059` 驗推論的擁有／`prediction_type`／skill，**兩次驗證之間沒有任何一句把兩者對照**；`app/services/decision_lineage.py` 舊簽章也沒有版本引數。⇒ 簡報第 8 段「engine 與 create 路徑使用同一 owner／skill／版本上下文」裡**版本那一半既無法執行、也無法測**。

**最小修法（三處，全在 13.9.2）**：①寫入端把身分記進帳本列與回應；②閘門加一個**可選**引數——決策沒命名版本就早退（不擴張既有拒付面），命名了則原點**必須**命名且相等（缺席**不等於同意**）；③桌面門把決策自己命名的那個版本交進去。

#### 13.9.2 實際 diff（四檔、逐段、附現測行號）

**① `backend/app/api/personal_models.py`（寫入端）**——帳本 metadata（`:714`，註解在 `:707-713`）與回應（`:756-760`）各加一個欄位；兩處共 ＋10 行：

```python
# :707-714（record_api_call_no_commit 的 metadata_json 內）
            "training_job_id": spec.get("training_job_id"),
            # Which frozen strategy this model was trained from. ``model_version_id``
            # alone cannot answer that, and a decision naming a strategy version has
            # to be checked against the version the inference actually came from.
            # A version row written before this key existed answers None, which the
            # lineage gate refuses rather than reads as agreement.
            "strategy_version_id": spec.get("strategy_version_id"),
# :756-760（predict 回應本體）
        "training_job_id": spec.get("training_job_id"),
        # Named in the answer as well as the ledger: a caller that let the skill
        # account's own selection resolve the version still has to know which
        # strategy version to write on the decision, or it cannot pass the gate.
        "strategy_version_id": spec.get("strategy_version_id"),
```

**② `backend/app/services/decision_lineage.py`（閘門）**——新引數在 `:33`，早退在 `:94-97`，兩個新拒因在 `:99-133`：

```python
    strategy_version_id: Optional[UUID] = None,          # :33
    ...
    if strategy_version_id is None:                      # :94  決策未命名版本 ⇒ 不擴張拒付面
        return                                           # :97
    inference_strategy_version_id = metadata.get("strategy_version_id")   # :99
    claimed_origin: Optional[uuid.UUID] = None
    if isinstance(inference_strategy_version_id, str):
        try:
            claimed_origin = uuid.UUID(inference_strategy_version_id)   # :106 逐字解析，拼字不決定成敗
        except ValueError:
            claimed_origin = None
    if claimed_origin is None:
        raise HTTPException(400, {"reason": "inference_version_unnamed", ...})   # :110-121
    if claimed_origin != strategy_version_id:
        raise HTTPException(400, {"reason": "inference_version_mismatch",
                "inference_strategy_version_id": str(claimed_origin),
                "decision_version": str(strategy_version_id), ...})               # :123-133
```

**③ `backend/app/api/paper_portfolio.py`（桌面門）**——`:1059` 把決策命名的版本交進去（放在擁有／凍結／run 一致性檢查**之後**，兩側才不可能各說各話）；另 `JOURNAL_COLUMN_SEMANTICS`／`DECISION_HISTORY_COLUMN_SEMANTICS` 的 `linked_inference_id` 敘述改寫為「拒因含**不是由該版本訓練出來的模型**產生」，讓匯出自述與閘門行為一致。

**④ `backend/tests/test_trained_inference_lineage_chain.py`（新檔，392 行／4 節點）**——走完整條「正式入口訓練 → 真 worker 寫 artifact → 版本列綁位元組 → 啟用＋選取 → 正式 predict（真 loader）→ 紙上決策 → 讀回／匯出」，細節見 13.9.6。

#### 13.9.3 直接驗收結果（三次 run，原始數字；不與改動並行）

| # | 命令 | 結果 | run dir |
|---|---|---|---|
| 1 | `cd backend && ./scripts/run_tests.sh sqlite tests/test_trained_inference_lineage_chain.py tests/test_decision_lineage.py tests/test_successful_inference.py tests/test_decision_history_export.py tests/test_daily_model_estimate.py tests/test_personal_strategy_learning.py tests/test_manual_decision_books_linked_portfolio.py` | **176 collected／173 passed／3 skipped／0 failed／0 error**，exit **0**；`skip categories pg_only=3`；`evidence gate: ledger closes against this run's own artifacts`；`tree-digest-before == after == 1f018e6b…`（改引用之前的樹） | `sqlite-20261121T040637Z-12788` |
| 2 | `cd backend && ./scripts/run_tests.sh sqlite tests/test_daily_model_estimate.py tests/test_daily_estimate_two_workers.py tests/test_predict_record_route_explicit_version.py tests/test_predict_kill_switch_boundary.py` | **75 collected／74 passed／1 skipped／0 failed**，exit **0**；`evidence gate` 閉合；`tree-digest-before == after == 953c7345…` | `sqlite-20261121T041636Z-16434` |
| 3 | `cd backend && ./scripts/run_tests.sh sqlite tests/test_trained_inference_lineage_chain.py tests/test_decision_lineage.py tests/test_successful_inference.py`（**13.9.0 那支候選上的重測**，為了讓 D 卡的節點數綁 `953c7345…` 而不是 run 1 的 `1f018e6b…`） | **44 collected／44 passed／0 skipped／0 failed／0 error**，exit **0**，35.79 s；逐檔＝`test_trained_inference_lineage_chain.py` 4／`test_decision_lineage.py` 26／`test_successful_inference.py` 14（本 run `collect.txt` 現數）；`tree-digest-before == after == 953c7345…`；`evidence gate: ledger closes` | `sqlite-20261121T042932Z-20599` |

新檔 4 支節點（都在 run 1 通過，節點名取自該 run 的 `junit.xml`）：

| 節點 | 證到什麼 |
|---|---|
| `test_the_inference_row_names_the_strategy_version_it_was_trained_from` | 帳本列**自己**就能說出來源版本（外加 `model_version_id`／`artifact_sha256`／`training_job_id` 三者同時對上） |
| `test_a_trained_models_inference_answers_a_decision_naming_its_frozen_version` | 同一個身分走過 create→持久列→`/decisions`→`decision-history?format=json`→**CSV** 五層都還在 |
| `test_an_origin_that_cannot_name_its_version_cannot_be_claimed_with_one` | 舊格式（無 `strategy_version_id`）的原點 ⇒ **400 `inference_version_unnamed`**，且 `decision_records` 零列。改動前這支會回 201，有辨別力 |
| `test_a_decision_naming_a_version_the_inference_was_not_trained_from_is_refused` | 命名另一支已凍結版本 ⇒ **400 `inference_version_mismatch`**，回應同時報出兩側 id，零列寫入 |

**這三段 PASS 的範圍**：SQLite、in-process、合成 OHLCV 固定資料（`US:AAPL` 240 支 fixture 棒）、真 sklearn 擬合、真 loader。它證的是**接線與拒付**，不是任何模型的預測能力，也不是 PG／服務／瀏覽器旅程。

#### 13.9.4 本輪造成的引用位移（自己弄壞的自己修，逐條現測）

* `app/api/personal_models.py` 在 `:707` 插 6 行、`:757` 插 4 行 ⇒ **≥707 右移 6、≥761 右移 10**。
* 已改：`app/services/daily_estimates.py:416`／`:520`／`:561`（`personal_models.py:724`→`:730`）；`tests/test_predict_record_route_explicit_version.py:4`（`:790-841`→`:800-851`）；本紀錄 §12／§13 三處 `:724`→`:730`（其中 `:4919` 那列標了位移出處）。
* 實測**未**位移（因此不動）：`:656` `:660/:661` `:670/:671` `:674/:675` `:678/:679` `:688` `:692`。
* `app/api/simulations.py` 的臨時改動**已逐位元組還原**：現測 sha256 `f3fc0ac2fb40449205da5be025e11a5e98c4bfb04f9a9e554b3264ed1409319b` ＝ §13.4-D-1 記錄值（同一把尺量出來的相等，不是「我改回來了」的敘述）。
* **沒有做**：把舊章節（§一、§5）所有指向 `personal_models.py` ≥707 的行號逐條追改——那是紀錄校稿，本輪明確不做；要用時以**符號名**重新定位（例：`predict_with_version_in_use` 現在同檔 `:801`）。

#### 13.9.5 第二個斷點：**replay 門不比較版本**（先量，才定性；本輪不半套執行）

`POST /simulations/{session_id}/manual-decision`（`app/api/simulations.py:2055-2060`）呼叫同一個 `validate_linked_inference`，但**不帶版本**：它的版本是**伺服端由 session 推導**的（同檔 `:2214` `linked_experiment_version_id=version.id`），不是呼叫者的宣稱。把 `strategy_version_id=version.id` 傳進去，本輪實測讓 **10 支節點變紅**：

| 紅的節點 | 根因（逐支读过才歸類） |
|---|---|
| 7 支（`_record_inference` 系列） | 測試**手工造的** `usage_records` 列沒有 `strategy_version_id`（那個 helper 在 `tests/test_manual_decision_books_linked_portfolio.py:251`）——它们本來就是為「格式剛好不對」而生的 |
| 2 支（`TestLineageFromAServedPrediction`） | **真實語意差異**：該 fixture 的 replay session 綁定的策略版本，與被服務的那支模型所訓練的バージョン**不同**；桌面門放行、replay 門會拒 |
| 1 支（兩門同 corpus 的一致性閘） | 它報 `replay=(400,'inference_version_unnamed') desk=('accepted', None)`——這正是上面那格的反面證據 |

**本輪決定**：不為了綠燈去改一支 2,600 行、上輪已凍結為證據的 fixture（那會把 owner 要求「保留原有證據範圍」的那批一起改掉），也**不半套執行**（只在版本存在時才比＝把剛補上的洞又挖開）。因此本輪把 simulations.py **原樣還原**，把「這條腿只有一扇門守」寫進程式檔本身（`app/services/decision_lineage.py:11-15`），而不只寫在本紀錄。**重啟它的最小前置**（名詞具體、可單獨批）：給 `_record_inference` 一個版本引數（預設＝該 session 的版本），並為那 2 支「跨版本」節點先定案——replay 決策的來源**必須**是該 session 正在跑的那個版本，還是允許是帳號上另一支模型的推論。這是一個**產品語意**選擇，會改到已驗收的拒付面，本輪不自作主張。

#### 13.9.6 九段閉環：**分段 verdict**（不拼成整條旅程 PASS）

| 段 | 入口（全部是正式入口，未 mock） | 證到哪 | verdict |
|---|---|---|---|
| 1 技能／策略版本 | `POST /experiments`＋`POST /experiments/{e}/versions/{v}/lock` | 未凍結的版本被決策門拒（`version_not_usable`，本輪實測到 400 才接通鏈） | **本輪 PASS**（sqlite） |
| 2 訓練入口→artifact | `POST /ml/personal/training-jobs`＋`training_worker.run_claimed_job` | 真 sklearn 擬合、真寫盤；`status=completed` | **本輪 PASS** |
| 3 版本列綁 artifact | `model_versions.model_specification_json` | `artifact_sha256` ＝ 當時盤上位元組的 sha256（`_artifact_sha256` 現算） | **本輪 PASS** |
| 4 activation approval／有效選取 | `POST /models/{v}/activate`＋ skill selection | `is_active=true` ＋ 該 skill 選到該版本 | **本輪 PASS**（與「每日估計 grant」**分開**，見 13.9.7-C） |
| 5 正式 predict 經真 loader | `POST /models/{v}/predict` | 200 ＋ `resolved_version.model_version_id` 等於選取的那支 | **本輪 PASS** |
| 6 非模擬推論→持久 `inference_id` | 同上一段（`_run_personal_prediction` 內 `settle :688`→`record :692`→**唯一 commit `:730`**） | 由 `usage_records` 直讀回同一 id | **本輪 PASS** |
| 7 明選該 inference 作 paper 決策 | `POST /paper-portfolio/{skill}/decisions` | 201，並持久在同名欄位 | **本輪 PASS** |
| 8 engine 與 create 同一 owner／skill／版本上下文 | 同上（create 側） | owner／skill／**版本**三側同閘 | **create 側 PASS；replay 側＝斷點（13.9.5）；engine 側不適用**——`app/services/paper_execution.py` 從不寫 `linked_inference_id`（既有事實，非本輪改動） |
| 9 正式讀回與適用匯出保留同一來源 | `GET /{id}/decisions`、`GET /{id}/decision-history?format=json\|csv` | 兩格都回報同一 `linked_inference_id`＋`inference_model_version_id`，且 `inference_source_state=present` | **本輪 PASS**（含 CSV） |

**因此：旅程不宣稱整體 PASS。**第 8 段只有一扇門守著，第 4 段的「每日估計 grant／revoke」與 activation approval 仍是兩件事（下卡 C 把它們列成不同 verdict）。

#### 13.9.7 A／B／C／D 四張卡（**每張自含全部命令與前提；一格＝一個 verdict；不從前一張繼承批准；本節一律未執行**）

> 共同紀律（四張都適用，**不再互相引用**）：①**身份核對在任何寫入之前**，不符 ⇒ 停；②跳過＝**NOT RUN**，不是通過；③停後**保留現場**（`$RUN`／`$PGDATA`／log／capture），不刪、不重試、不改設定碰運氣；④**只管理本卡自己記下的 pid**（啟動即 `echo $! > "$RUN/pids"`，停止只對那些 pid 發 `TERM`），**不從 `pgrep` 結果挑別人的行程**；⑤任何額外命令或副作用**不從另一張卡繼承批准**——需要就在本卡內重列。

---

**卡 A——在一台新建的隔離 PostgreSQL 16.2 叢集上，跑「衝突門 driver 行為」與「真併發」共 14 支具名節點。三個 verdict 各自獨立：A-Schema、A-Driver、A-併發。**

* **固定目標**：`initdb` 新建的叢集，`PGDATA=${TMPDIR:-/tmp}/alethra-r19-pgdata`、socket `${TMPDIR:-/tmp}/alethra-r19-sock`、**只能聽 127.0.0.1:5432**（lane 的實例白名單是 `test_safety_gate.py:41-45`，只有 `localhost`／`127.0.0.1`／`::1` ＋ **5432**）；庫 `alethra_owner_test`（白名單 `:29`）。
* **輸入**：本節 13.9.0 那支候選；14 支節點名（下面逐字列出）；兩個非特權角色。
* **會寫入哪裡**：新建的 `$PGDATA`（本卡唯一落地處）、`$RUN`（`mktemp -d`）、測試自己的資料表。
* **第一個副作用**：`initdb` 寫 `$PGDATA`（目錄已存在 ⇒ pgserver 自帶拒寫，不覆寫）。
* **A-0 寫入前的身份核對**（任一項不符 ⇒ 停，不建、不 kill、不覆寫）：

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
PGB=$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin
RUN=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r19-pg-XXXXXX")
export PGDATA="${TMPDIR:-/tmp}/alethra-r19-pgdata" SOCKDIR="${TMPDIR:-/tmp}/alethra-r19-sock"
ls -d "$PGDATA" "$SOCKDIR" 2>&1                      # 兩者都必須是 No such file
lsof -nP -iTCP:5432 -sTCP:LISTEN | tail -n +2 | wc -l   # 必須 0；本節 04:1x 現測就是 0
pgrep -fl postgres | cat                              # 只列，不 kill；有任何人 ⇒ 停並回報
cd "$REPO" && git rev-parse HEAD && git status --porcelain | shasum -a 256
```

  必須逐字等於：**HEAD `5a34e3fe…`**、**porcelain 225 paths／`bcc332c0812c143640533c161c8de7e0b0345012319fa81e7aed1174cf690853`**、**python 樹 `953c7345fa1cb7f8f5f1f657bec956c0df63167b4120e9e39eab684263fbc80d`**（本節行號綁它）。**本卡不得停掉任何不是本卡起的 postgres 行程。**

* **A-1 建叢集＋啟動（資源上限就是這兩個數）**：

```bash
"$PGB/initdb" -D "$PGDATA" --auth-local=trust --auth-host=scram-sha-256 -E UTF8 --no-sync
"$PGB/pg_ctl" -D "$PGDATA" -o "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB" -l "$RUN/pg.log" start
echo $! > "$RUN/pids"; "$PGB/pg_isready" -h 127.0.0.1 -p 5432
```

  非零 ⇒ 停、留 `$RUN/pg.log`、**不重試、不刪 `$PGDATA`**。⚠️ **揭露**：`--auth-local=trust` 讓本機任何**同使用者**程序能用 socket 以 superuser 進這台測試叢集；邊界就是 socket 在該使用者專屬的 `$TMPDIR` 下（本機實測路徑 `/var/folders/tk/…/T/`），**不適用於多使用者機器**。

* **A-2 一個庫＋兩個非特權角色**（經 socket 以叢集 superuser 執行；密碼用 `openssl rand -hex 12` 寫進 `$RUN/roles.sql`（chmod 600），**不印、不進命令列**）：`CREATE ROLE alethra_r19_migrator LOGIN PASSWORD …`→`CREATE DATABASE alethra_owner_test OWNER alethra_r19_migrator`→在新庫內 `GRANT USAGE, CREATE ON SCHEMA public TO alethra_r19_migrator`→`CREATE ROLE alethra_r19_app LOGIN PASSWORD …`＋只給 `CONNECT, TEMP`／`USAGE`／`SELECT,INSERT,UPDATE,DELETE`＋sequences `USAGE,SELECT`（用 `ALTER DEFAULT PRIVILEGES FOR ROLE alethra_r19_migrator`）。**閘門校驗來源**：`test_safety_gate.py:110-128`（對 `pg_roles` 查 `rolsuper,rolcreatedb,rolcreaterole,rolbypassrls`，任一為真即硬拒）＋`:172`＋`:223`；lane 另有名前綴 `ALETHRA_TEST_REQUIRED_DB_PREFIX` 預設 `alethra_`（`scripts/run_tests.sh:178`）。⚠️ 角色**不能**有 CREATEDB，所以建庫這步必須在閘門**外面**走 socket。
* **A-3 空庫升全鏈**：`cd "$REPO/backend" && .venv/bin/python -m alembic -c alembic.ini heads` 然後 `… upgrade head`。本節以 `Alembic ScriptDirectory` 現測（離線、不連庫）：**`heads == ['d4e5f6g7h8i9']`、共 57 支、base `55a8d07a7f40`**；`heads` 多於一／不等於該 id／`DUPLICATE_REVISION`｜`BRANCHED_TREE`｜`MISSING_DEPENDENCY` ⇒ 停。**不 autogen、不 downgrade、不 drop、不跑其他 migration。**
* **A-Schema（verdict 1）**——唯讀：`\dn`、`SELECT current_database(), version();`、查 `pg_indexes` 有 `paper_orders.uq_paper_order_portfolio_idempotency`（partial：`WHERE idempotency_key IS NOT NULL`，`app/models/paper_portfolio.py:166-172`）與 `human_decision_events.uq_human_decision_owner_session_key`（`UniqueConstraint(owner_id, simulation_session_id, idempotency_key)`，`app/models/human_decision.py:80-84`），查 `information_schema.columns` 有 `decision_records.linked_inference_id` 與 `human_decision_events.linked_inference_id` 且兩處型別＝`uuid`。**它的 verdict 只到「索引／型別在不在」**；driver 實際回什麼 SQLSTATE 一律屬 A-Driver，**不得寫成「衝突在 PG 已驗」**。
* **A-Driver（verdict 2）**——8 支具名節點（本節 `grep -n '^def'` 現測行號）：`tests/test_conflict_doors_at_entry.py` 的 `:307 test_door_A_answers_only_its_own_index_with_a_conflict`／`:348 test_door_A_does_not_rename_its_own_not_null_failure`／`:413 test_door_B_answers_a_conflict_on_the_order_index_it_owns`／`:450 test_door_B_lets_another_doors_index_through_as_a_500`／`:498 test_door_C_does_not_answer_another_doors_order_index_as_its_own`／`:570 test_door_D_answers_its_own_index_with_a_conflict`／`:649 test_door_D_does_not_rename_the_order_index`／`:698 test_the_entries_refuse_an_empty_key_so_the_index_cannot_be_reached_sequentially`：

```bash
cd "$REPO/backend"
export ALETHRA_PG_TEST_URL="postgresql+psycopg2://alethra_r19_migrator:<讀 $RUN/roles.sql，不印>@127.0.0.1:5432/alethra_owner_test"
export TEST_MODE=true DATA_PROVIDER=demo
./scripts/run_tests.sh pg \
  tests/test_conflict_doors_at_entry.py::test_door_A_answers_only_its_own_index_with_a_conflict \
  tests/test_conflict_doors_at_entry.py::test_door_A_does_not_rename_its_own_not_null_failure \
  tests/test_conflict_doors_at_entry.py::test_door_B_answers_a_conflict_on_the_order_index_it_owns \
  tests/test_conflict_doors_at_entry.py::test_door_B_lets_another_doors_index_through_as_a_500 \
  tests/test_conflict_doors_at_entry.py::test_door_C_does_not_answer_another_doors_order_index_as_its_own \
  tests/test_conflict_doors_at_entry.py::test_door_D_answers_its_own_index_with_a_conflict \
  tests/test_conflict_doors_at_entry.py::test_door_D_does_not_rename_the_order_index \
  tests/test_conflict_doors_at_entry.py::test_the_entries_refuse_an_empty_key_so_the_index_cannot_be_reached_sequentially
echo "A-Driver lane rc=$?"
```

  判準：`A`／`B` 的 409 必由 **`uq_paper_order_portfolio_idempotency`＋SQLSTATE 23505** 組成；`C`／`D` 必由 **`uq_human_decision_owner_session_key`＋23505**；三支**反控**（`:450`／`:498`／`:649`）必為 **500／非 409** 且回應體不出現 SQL 語句、綁定參數或他人資料；`:348` 必為 NOT NULL（23502）而**不是** 409。
* **A-併發（verdict 3）**——6 支具名節點：`tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle`（class 在 `:2527`）的 3 支行鎖生命週期、`tests/test_daily_estimate_two_workers.py:262 test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier`＋`:607 test_two_processes_on_one_window_bill_each_business_key_once`、`tests/test_daily_model_estimate.py:794 test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice`。先 `export ALETHRA_RACE_CAPTURE_JSON="$RUN/race-capture.json"`（`_write_capture()` 不建父目錄 ⇒ `$RUN` 必須已存在）。計時契約（現測）：`_TIMEOUT_S = 60`、`lock_timeout '30s'`／`statement_timeout '120s'`／`idle_in_transaction_session_timeout '90s'`。**額度前提：這個目標的 `ai_usage_limit` 維持預設 10（`app/auth/entitlements.py:70`），不得套 B 的 1**，否則 429 會把真缺陷蓋成限流。帳本判準（`tests/daily_estimate_race.py:131 assess_race()` 共 12 條）逐條要過：每 `(owner, skill, instrument, window)` 恰 1 列、`metadata["via"]=="daily_service"`（常量 `app/services/daily_estimates.py:77`）、每列恰 1 筆 settled 預留（`quota_reservations`，`app/models/quota.py:43`）、非終態預留＝0、落敗那支具名 `already_estimated_this_window`、兩個 job 摘要的 `billed_in_window` **不相加**。`Barrier` 未湊齊／計時未滿／`proven=false` ⇒ 該次是 **NOT RUN／INVALID**，**不是 PG PASS**。
* **停止線**：非零 exit｜lane rc 2（`scripts/run_tests.sh:170-172`，空 `ALETHRA_PG_TEST_URL`）或 rc 3（`:184-185` preflight 拒；rc 語意在 `:56`）｜head 不符｜四旗標不為全 `f`｜`current_database() != alethra_owner_test`｜反控那三支出現 409｜以跳過取代執行｜任一斷言失敗。
* **停止命令**：`xargs -n1 kill -TERM < "$RUN/pids" 2>/dev/null || true`（**只本卡記的 pid**）→ `"$PGB/pg_ctl" -D "$PGDATA" -m fast stop` → `pg_isready` 必非零 → `lsof -nP -iTCP:5432 -sTCP:LISTEN | tail -n +2 | wc -l` 必為 **0**。**不得**用已停端點的 HTTP 回應證明停止；**不刪 `$PGDATA`**。
* **狀態：NOT RUN**（本輪未啟動 PG）。三個 verdict 各自回報，**任一通過都不替另外兩個背書**。

---

**卡 B——自然排程（不改時鐘、不用手動入口冒充）在**自己一支**叢集上跑一次。三個 verdict：B-額度、B-停用、B-外連觀察。**

* **為什麼自帶一支叢集**：本卡**不跑 `run_tests.sh pg`**，所以 `test_safety_gate.py:41-45` 的 5432 白名單**不約束它**；它用自己的一支，就**不需要也不得引用 A 卡留下的現場**。
* **固定目標**：`PGDATA=${TMPDIR:-/tmp}/alethra-r19-cron-pgdata`、socket `${TMPDIR:-/tmp}/alethra-r19-cron-sock`、**127.0.0.1:5433**、庫名必須含 `alethra_personal_e2e`（這是本卡要用的種子腳本自己的硬條件，見下）。
* **B-1 建自己的叢集＋一支庫＋升鏈（本卡自含，逐字可跑；不引用 A 卡任何命令）**：

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
PGB=$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin
RUN=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r19-cron-XXXXXX")
export PGDATA="${TMPDIR:-/tmp}/alethra-r19-cron-pgdata" SOCKDIR="${TMPDIR:-/tmp}/alethra-r19-cron-sock"
ls -d "$PGDATA" "$SOCKDIR" 2>&1                          # 兩者都必須是 No such file，否則停
lsof -nP -iTCP:5433 -sTCP:LISTEN | tail -n +2 | wc -l    # 必須 0
"$PGB/initdb" -D "$PGDATA" --auth-local=trust --auth-host=scram-sha-256 -E UTF8 --no-sync
"$PGB/pg_ctl" -D "$PGDATA" -o "-p 5433 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB" -l "$RUN/pg.log" start
echo $! > "$RUN/pids"; "$PGB/pg_isready" -h 127.0.0.1 -p 5433
/usr/bin/openssl rand -hex 12 > "$RUN/pw.txt" && chmod 600 "$RUN/pw.txt"   # 不印
PW=$(cat "$RUN/pw.txt")
"$PGB/psql" -h "$SOCKDIR" -p 5433 -d postgres -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE alethra_r19_cron LOGIN PASSWORD '$PW';
CREATE DATABASE alethra_personal_e2e OWNER alethra_r19_cron;
SQL
export DATABASE_URL="postgresql+psycopg2://alethra_r19_cron:$PW@127.0.0.1:5433/alethra_personal_e2e"
cd "$REPO/backend" && .venv/bin/python -m alembic -c alembic.ini upgrade head
```

  ⚠️ **這三條是本卡自己的批准面**：`initdb` 落 `$PGDATA`、`CREATE ROLE`／`CREATE DATABASE`、`upgrade head` 的 57 支 DDL——**沒有一條由 A 卡得來**（`$PW` 只在本機 shell 內使用，不印出、不入紀錄）。停止線：非零 exit ⇒ 停、留 `$RUN/pg.log`、不重試、不刪 `$PGDATA`。
* **B-2 輸入從哪裡來**：`backend/scripts/seed_e2e_personal_learning.py`。現讀它的三個拒路（`:28` 非 `postgresql*` 直接 exit、`:30` 庫名不含 `alethra_personal_e2e` 直接 exit、`:80` `E2E_EMAIL`／`E2E_PASSWORD` 未由環境提供直接 exit）；它種的是 1 owner／1 版本／1 標的（`INSTRUMENT = "US:AAPL"`，`:82`）／205 筆已確認決策＋標籤，`E2E_GRANT_CONSENT=1` 才給**訓練**同意（`:381-383`），並**刪除** `PersonalModelSelection`（`:169`）。
* **⚠ B-額度這格現在是 BLOCKED，不是未跑**——本節讀過腳本全文後的事實：它**不建立**技能帳號（`PaperPortfolio` 在檔內 0 次）、**不訓練**任何 `ModelVersion`、**不寫**任何 selection；而排程那支 tick 只服務「有 `purpose="personal_daily_estimate"` 未撤回同意」的帳號（`app/services/daily_estimates.py:61`＋`estimate_owner_ids :388-408`）。所以**照現有材料起棧，05:00 那一 tick 會正當回報「無人不計費」，額度路徑根本進不去**。這格要能批，缺的是一個「種到可計費狀態」的一次性入口（技能帳號＋訓練好的版本＋selection＋**每日估計**同意），**不是** PG、也不是時間。代理不代填同意。
* **B-停用（verdict）**：`ENABLE_LEARNING_SCHEDULER=false`（預設 false，`app/config.py:140`）起一次，讀 `learning_jobs`／`usage_records` 新增列數＝**0**；對照組 `=true` ＋ `LEARNING_SCHEDULER_JOBS=daily_model_estimate`（預設空 ⇒ 取 `config.py:55-59` 那三支，**其中沒有 estimate**）時，啟動日誌必須**只**點名這一支 lane。
* **B-外連觀察（verdict）**：主證據＝`lsof -nP -p "$(cat $RUN/pids)" -a -i` 中對端非 `127.0.0.1`／`::1` 的列數＝**0**；`env -i` ＋ `DATA_PROVIDER=demo AI_PROVIDER=mock` 是**機制**不是保證，log grep 只當旁證。
* **第一個副作用**：`initdb` 寫 `$PGDATA`（本卡自己的），之後是 `alembic upgrade head` 的 DDL，再之後是種子腳本的寫入。**服務啟動本身不寫任何業務列。**
* **自然觸發的時間事實（本節現測）**：`DAILY_ESTIMATE_HOUR_UTC = 5`（`app/services/daily_estimates.py:82`，`CronTrigger(hour=…, minute=0, timezone="UTC")` 在 `app/workers/learning_scheduler.py:183-185`）。**必須在 05:00:00Z 之前把棧起好**；錯過 ⇒ 該次 **NOT RUN**（下一个窗口是隔天 05:00Z），**不調時鐘、不把手動 `trigger_daily_estimate` 冒充 cron**。
* **啟動命令**（秘密只有隨機 JWT；`cd "$RUN"` 是承重的——`app/config.py:560` 的 `env_file=".env"` 按行程 cwd 解析，`cd backend` 會讓本機 `.env` 生效）：

```bash
cd "$RUN" && export JWT_SECRET_KEY=$(/usr/bin/openssl rand -hex 16)   # 寫 $RUN/jwt-secret.txt(600)，不印
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME="$HOME" PYTHONPATH="$REPO/backend" \
  DATA_PROVIDER=demo AI_PROVIDER=mock APP_ENV=development \
  ENABLE_LEARNING_SCHEDULER=true LEARNING_SCHEDULER_JOBS=daily_model_estimate \
  AUTO_COLLECTION_ENABLED=false RESEARCH_AUTH_BYPASS_ENABLED=false LIVE_TRADING_ENABLED=false \
  DATABASE_URL="postgresql+psycopg2://<本卡 role>@127.0.0.1:5433/alethra_personal_e2e" \
  "$REPO/backend/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8199 >"$RUN/service.log" 2>&1 &
echo $! > "$RUN/pids"
```

  釘值的如實定性：`RESEARCH_AUTH_BYPASS_ENABLED` 全樹只有 1 個消費者（`app/auth/jwt.py:51`）；`LIVE_TRADING_ENABLED` 在 `app/**` 下 **0 個消費者**——兩個都是**消歧**，不是防護。
* **資源上限**：`LEARNING_SCHEDULER_MAX_OWNERS` 維持預設 25（`config.py:153`）；本卡只種 1 owner；DB 端 25 連線／64MB。
* **停止線**：05:00Z 後 90 秒內無 `learning_jobs` 列 ⇒ 停並存 `$RUN/service.log`；啟動日誌點了第二支 lane ⇒ 立即走 `stop_learning_scheduler()`（`app/main.py:72-74`）；任一 `KillSwitchScope`（global／training／execution／inference，`app/models/kill_switch.py:10-15`；計費 lane 查 `inference`）為 active 時 tick 竟寫了帳 ⇒ 那是本卡要抓的缺陷，**原樣回報**不重跑。
* **停止命令**：`kill -TERM "$(cat $RUN/pids)"` → `lsof -nP -iTCP:8199 -sTCP:LISTEN | tail -n +2 | wc -l` 必為 0 → 再停本卡自己的叢集（同 A 卡末段，用 `$RUN/pids` 與自己的 `PGDATA`）。**不刪 `$PGDATA`。**
* **狀態：NOT RUN**；三格之中 **B-額度 = BLOCKED（缺可計費帳號的來源，上面已具體命名）**，B-停用／B-外連 ＝ 材料齊、待批。

---

**卡 C——只在 8101／3101 的 dev 功能驗收棧上跑既定兩支 spec。四個 verdict 互不取代：C-DDL／C-自動化／C-本人同意撤回／C-可能扣額。結果一律標 dev。**

* **C-1 起棧（唯一入口）**：`cd "$REPO" && ./start_candidate_stack.sh`，後端 `127.0.0.1:8101`（腳本 `:397` 起 uvicorn，port 預設見 `:601`），前端 `next dev` `:446`（port 預設 `:602`＝3101）。三個必須明釘的值：`DATA_PROVIDER=demo AI_PROVIDER=mock RESEARCH_AUTH_BYPASS_ENABLED=false`（不釘 false 的話 `app/auth/jwt.py:51` 會以「第一位 active user」放行 loopback，**把「本人操作」那格掏空**）＋ `LIVE_TRADING_ENABLED=false`（如實：0 個消費者）。Docker 路線不存在（`command -v docker` 無）。
* **C-DDL（verdict）**：**起棧本身是 DDL**——腳本 `:575` 讀 `heads`，`:585` 與 `:628` 對它自己那支 `DATABASE_URL` 執行 `alembic upgrade head`。⇒ 本卡必須先**指名它寫哪支庫**並單獨批准；把它指向 A 或 B 的庫＝在那支庫上多跑一次 migration（**該次 DDL 副作用不從別張卡繼承**）。核對：起棧前 `cd backend && .venv/bin/python -m alembic heads`（本節現測 `d4e5f6g7h8i9`）、起棧後從服務讀 `schema-head` 對照。
* **C-自動化（verdict）**：`cd "$REPO/frontend" && E2E_FRONTEND_BASE=http://127.0.0.1:3101 E2E_BACKEND_BASE=http://127.0.0.1:8101 E2E_BROWSER_CHANNEL=chrome E2E_CRED_FILE=<本機憑證檔> node_modules/.bin/playwright test e2e/manual-decision-lineage.spec.ts e2e/daily-estimate-journey.spec.ts`——**只這兩支**，不掃全 e2e。⚠️ **`--list` 也不是唯讀**：`frontend/playwright.config.ts:36-38` 的 `webServer.url`（取自 `:5`／`:24` 的 `E2E_FRONTEND_BASE`）會讓任何 Playwright 命令先探測／拉起那個 origin。本節未執行任何 Playwright 命令。
* **C-本人同意／撤回（verdict，只能本人）**：`data-testid="daily-estimate-acknowledge"`（`frontend/src/components/training/DailyEstimatePanel.tsx:676`）與 `daily-estimate-revoke-all`（`:695`）。spec 只斷言按鈕存在與文案；**代理不替本人按**，本卡文字也不構成同意。**注意與 A／訓練同意分開**：每日估計的同意是另一個用途（`PersonalizationConsent.purpose == "personal_daily_estimate"`，`app/services/daily_estimates.py:61`），**按下它不等於第 4 段的 activation approval**，兩者的證據不得互換。
* **C-可能扣額（verdict）**：只有設了 `E2E_DAILY_ESTIMATE_SKILL` 才走進那幾格（`frontend/e2e/daily-estimate-journey.spec.ts:108` `?? ''`、`:111` 具名跳過）。未獲准扣額 ⇒ **保持未設**，並把那句跳過原因逐字入帳（前置缺件 ≠ 通過）。另兩格前置：`E2E_CRED_FILE`（`frontend/e2e/runtime.ts:34`；缺 ⇒ `TIER_B_SKIP`，`:26-30`）、`E2E_LINEAGE_ASSET`（預設 `DEMO-ALPHA`，`manual-decision-lineage.spec.ts:111`）。**代理不代填。**
* **輸入／寫入處／第一個副作用**：輸入＝本節 13.9.0 的候選 ＋ 那支 dev 棧連到的 `DATABASE_URL`；第一個副作用＝C-DDL 那次 `upgrade head`；其後是 spec 在該庫裡建的測試帳號／決策列。
* **驗收條件**：①`/health/ready` 通過；②預檢回應含 `access-control-allow-origin: http://127.0.0.1:3101`（`app/main.py:27` 只讀 `settings.cors_origins_list`，預設 `http://localhost:3000`，`config.py:79`——`localhost` 與 `127.0.0.1` **不是同一 origin**）；③四個數只報 **collected／passed／skipped／failed**，不拿 collected 當 passed；④**按鈕可見不是 grant／revoke 的驗收**。
* **停止線**：①／②／③ 任一不符，或前置齊全卻 failed ⇒ 停、存 log、原樣回報，**不重跑、不改設定碰運氣**。
* **停止命令**：讀 `start_candidate_stack.sh` 自己記的 pid 檔並只對其發 `TERM`（**不從 `pgrep` 挑別人的行程**）。
* **狀態：NOT RUN**（未獲准啟動服務／瀏覽器；C-本人、C-扣額兩格本質上必須由本人動作）。

---

**卡 D——在一個新目錄裡用本機來源重建候選，並跑本節點名的 sqlite 節點（純離線、零網路、零 Git 寫入）。**

* **先把「重現的是什麼範圍」講清楚（本輪不再用「本輪增量」冒稱）**：
  * D 重現的是**目前工作候選的來源內容**，即 `git ls-files -co --exclude-standard` 那 **1,822 檔**（repo；其中 `backend/` **566 檔**），**不是** §13.4-D 那支「只取 `git ls-files` 已追蹤 866 檔」的命令——那一版**重建出來的東西跑不起本紀錄的任何旅程**：本輪現測 `backend/` 下有 **48 支未追蹤但未被 ignore 的 `.py`**，其中包括 `app/services/decision_lineage.py`、`app/api/conflicts.py`、`app/services/strategy_versions.py`、**migration head `d4e5f6g7h8i9` 等 6 支 revision**，以及本節的 `tests/test_trained_inference_lineage_chain.py`。少了 head，A 卡的 `upgrade head` 落點就不是 A 卡自己宣稱的那個。
  * D **不重現**：PG 執行（A）、自然 cron（B）、瀏覽器（C）、`backend/.venv` 的內容（另以整份複製帶過去，470 MB）、gitignored 資料、遠端倉庫、以及**整套測試**——它只跑下面具名的節點，這是**具名子集**，不是整套 PASS。
* **固定目標／寫入處**：`DST=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r19-repro-XXXXXX")`，唯一寫入目錄；**不寫 `$REPO`、不碰 `.git`、不 push、不 stage、不 commit**。
* **第一個副作用**：在 `$DST` 裡解 tar（純新檔）。
* **逐條命令**（一步＝一個可單獨否決的動作）：

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
DST=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r19-repro-XXXXXX")
1  mkdir -p "$DST/repo" && (cd "$REPO" && git ls-files -co --exclude-standard -z | xargs -0 tar cf -) | (cd "$DST/repo" && tar xf -)   # 來源＝整份候選（含未追蹤、排除 ignored）
2  cp -R "$REPO/backend/.venv" "$DST/repo/backend/.venv"       # BSD cp 保留 mode/symlink；470 MB，D-3 那條 shebang 禁令在此生效
3  cd "$DST/repo" && git init -q && printf 'candidate snapshot, not a repo copy\n' > "$DST/README.txt"   # 不 clone、不 apply patch
4  cd "$DST/repo/backend" && ./scripts/run_tests.sh sqlite tests/test_trained_inference_lineage_chain.py tests/test_decision_lineage.py tests/test_successful_inference.py
```

  **入口一律走 `$DST/backend/.venv/bin/python -m …`／`./scripts/run_tests.sh`，禁止 `bin/pytest`、`bin/alembic`**：本節現測 `.venv/bin` 38 檔裡 **31 支 shebang 寫死本 checkout 絕對路徑**，直接叫那些腳本就會 import 回原處。**不用裸 `pytest`**（提示明文）。
* **驗收條件**：第 4 步必須是 **44 collected／44 passed／0 skipped／0 failed／exit 0**。這個數**不是預期值，也不是抄來的**：13.9.3 run 3（`sqlite-20261121T042932Z-20599`，`tree-digest == 953c7345…`，即 13.9.0 釘的那支候選）在 `$REPO` 現測 44／44／0／0，逐檔＝新檔 4 ＋ `test_decision_lineage.py` 26 ＋ `test_successful_inference.py` 14。重建後的 run 必須自己再量一次並对上這四個數，而不是引用這句敘述。另外該 run 自己的 `identity.txt` 要顯示 `cwd` 與 `interpreter` 都在 `$DST` 之下——這才是「不是從原 checkout import」的實物證據（由 lane 自己印，不靠手寫 `import app`）；`identity.txt` 的 porcelain 清單若 `$DST` 那棵不是 git 樹就讀不到，**此時只比 `interpreter`／`cwd`，不假裝比過候選身分**。
* **停止線**：任一非零 exit｜節點數不符｜`interpreter` 或 `cwd` 落在 `$REPO`｜複製過程中 `$DST` 已存在 ⇒ 停、保留 `$DST`、**不重試、不刪**。**只管理本卡記下的子行程**。
* **狀態：NOT RUN**（本輪不建 `$DST`、不複製）。

#### 13.9.8 本輪寫入清單與副作用（全部 in-place 編輯，零新目錄、零 Git 寫入、零網路）

| 檔 | 動作 | 位置 |
|---|---|---|
| `backend/app/api/personal_models.py` | ＋10 行（帳本 metadata 1 鍵＋註 5 行；回應 1 鍵＋註 3 行） | `:707-714`、`:756-760` |
| `backend/app/services/decision_lineage.py` | ＋1 可選引數、＋早退、＋兩個拒因、＋模組 docstring 的**一扇門**揭露 | `:11-15`、`:33`、`:94-133` |
| `backend/app/api/paper_portfolio.py` | 桌面門傳入決策命名的版本；兩處匯出欄位敘述改寫 | `:1052-1059` 及 `JOURNAL_COLUMN_SEMANTICS`／`DECISION_HISTORY_COLUMN_SEMANTICS` |
| `backend/app/services/daily_estimates.py` | **僅文件字串**：3 處 `personal_models.py:724`→`:730` | `:416`、`:520`、`:561` |
| `backend/tests/test_predict_record_route_explicit_version.py` | **僅文件字串**：1 處 `:790-841`→`:800-851` | `:4` |
| `backend/tests/test_trained_inference_lineage_chain.py` | **新檔**（392 行／4 節點） | 見 13.9.2④ |
| `backend/app/api/simulations.py` | 改過又**逐位元組還原**（hash 等於 §13.4-D-1 記錄值） | — |
| `EXECUTION_TABLE_20261117.md` | 本節＋3 處 `:724`→`:730` | §13.9 |
| `backend/.venv/bin/python` | 一次 `touch`（**未改位元組**，只把 mtime 推到新於 `backend/` 那 48 支未追蹤 `.py`（現測；repo 全域未追蹤 `.py` 共 194 支＝`backend/` 48 ＋ `alethra-evidence/` 146，後者是歷輪 patch 附件、不進候選），避免下一支跑把 venv 判為舊於候選）；本節寫完後已用同一把尺重測：`953c7345fa1cb7f8f5f1f657bec956c0df63167b4120e9e39eab684263fbc80d`（13.9.3 run 3 的 before==after 同值）⇒ 無內容變化 | — |

**本輪沒有**：建庫、跑 migration、啟動 PG／服務／瀏覽器／cron、外呼、重釘釘選、clone／apply、任何 Git 寫入、任何秘密讀取或展開程序環境。

#### 13.9.9 本輪仍未執行、仍未驗證（如實）

PG 全鏈（A-Schema／A-Driver／A-併發）、自然 cron（B 三格；其中 **B-額度是 BLOCKED，不是未跑**）、瀏覽器兩支 spec（C 四格）、隔離重建（D）；`grant／revoke` 只能由本人動作；replay 門的版本腿（13.9.5，含那 10 支的 fixture 重建與那個產品語意選擇）；`POST /human-decisions` 不接收 `linked_inference_id`（請求模型無此欄，既有事實）；每日估計的 `activation approval` 與 `每日估計同意` 仍是兩份證據；**8 個研究家族的 FAIL 原樣保留**，本輪不為任何綠燈改研究門檻，也不宣稱任何市場有效性。

---

## 十四、第十九輪（2026-11-21 06:34–08:1x UTC＝14:34–16:1x HKT）：**replay 的版本腿已接通（兩門同閘）；paper desk 省略版本有直接回歸；B 的資料準備在 SQLite 閉合；A／B／C／D 換成本輪後的自含正文**

### 14.0 本節身份（08:0x–08:1x 現測；§14 每個行號都綁這組）

| 項 | 值（全部由命令現讀，非抄寫） |
|---|---|
| HEAD／branch | `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`／`live1-impl`（未變、本輪**零 Git 寫入**） |
| porcelain | **229 paths** sha256 `572fd141820b9f1c3ab3027b67a958cdde578f1a7d491a8d432a052176e537ce`（218-run 自己的 `identity.txt`） |
| python 樹 digest | **`192fd63e6ffd9cce8e6d5deedfb787a319ae37705484e0003193cae7d2b6404a`**（該 run `tree-digest-before == after`） |
| candidate 鏈 | `953c7345…`（§13.9.0，44／44）→ **`144943a2…`**（①②，208／205／3／0）→ **`192fd63e…`**（③，218／215／3／0）。porcelain 225→226→229：後一段的 **＋3 恰是本輪三支新檔**（加性自洽）；前一段的 ＋1 本輪**未逐檔溯源**，只記錄两支 run 各自讀到的數 |
| 本輪新檔（現測行數） | `backend/app/services/daily_estimate_readiness.py` **326**、`backend/scripts/daily_estimate_readiness.py` **100**、`backend/tests/test_daily_estimate_prep_readiness.py` **768**（10 節點） |
| 計數校驗 | `git ls-files -co --exclude-standard`＝repo **1,825**、`backend/` **569**（§13.9.7-D 記的是 1,822／566）⇒ **＋3／＋3** 與上一列對上 |

**兩條本輪量出來的更正性事實（先記，後面卡片要用）**

1. **`git ls-files -cd` 不是「已刪除清單」**。本輪實測：`-cd` 回 **866** 條，但 `git ls-files -d` 單獨回 **0** 條，且那 866 條逐條 `[ -e ]` **全部存在**（`.env.example`、`AGENTS.md`、`Dockerfile.backend`、`backend/app/main.py` 等）。語意：`-c` 與 `-d` 是**過濾器聯集**（`-d` ⊆ `-c`），所以 `-cd` ＝ 全部已追蹤檔 ⇒ 上輪 D 卡那句「`git ls-files` 已追蹤 866 檔」的**數是對的、類別名稱是錯的**。本輪真正的「已刪除路徑」數＝**0**，D 卡據此改寫，不再假裝有一批死路徑要處理。
2. **`backend/.env.test` 會在重建包裡**。現測：未追蹤、**並未被 ignore**（`git check-ignore -v backend/.env.test` → NOT-IGNORED）、29 行、sha256 `6223853e224cd338e37201174db57490450ad996838bedc1c650ee0f8316d1f6`、`git ls-files -co --exclude-standard` 逐字含它。檔內**鍵名**（值未讀、未印）：`DATABASE_URL`、`LIVE_TRADING_ENABLED`、`RESEARCH_AUTH_BYPASS_ENABLED`、`ENABLE_LEARNING_SCHEDULER`、`AUTO_COLLECTION_ENABLED`、`DATA_PROVIDER`、`TEST_MODE`、`TEST_ENVIRONMENT`、`KRAKEN_WS_URL`、`KRAKEN_API_KEY`、`KRAKEN_API_SECRET`、`TEST_USER_EMAIL`、`TEST_ADMIN_EMAIL`。⇒ D 卡**逐路徑排除它**（本輪未重新驗證其值是範本還是有效憑證，所以按最壞情況處理；§十一 #146 的既有結論不變：只列鍵名）。root `backend/.env`（758 B）已被 `.gitignore:14` 排除，不在 `-co` 集內。
3. **觀察但不宣稱**：`backend/app/services/order_lifecycle.py` 的 mtime（14:37 HKT）落在本輪時段內，惟檔內 `inference`／`version` 字串 **0 次**，也不在 14.6 那三支 run 的任何 selection 裡 ⇒ 本輪**不把它算進 diff**，只記錄這筆觀察（共用 checkout 有平行工作線，見既有一貫做法：不動別人未經本輪驗證的內容）。

### 14.1 ①replay 的來源版本腿：**實際 diff**（三檔產品碼，現測行號）

上輪 §13.9.5 量到「這條腿只有一扇門守」，並把 `simulations.py` 原樣還原、留一個產品語意選擇給本人。**本輪那句選擇已不需要**：本輪把兩扇門都接到同一個比較上，並讓**缺欄位的反控各自留在原位**（14.2）。

**① `backend/app/services/decision_lineage.py`（199 行）**——一條規則、兩個可各自呼叫的段落：

| 段 | 行 | 證的是哪一句簡報 |
|---|---|---|
| 模組敘述 | `:10-19` | 「兩扇門都跑版本腿」取代 §13.9 那版「只有一扇門」的揭露（改敘述不藏行為） |
| `check_inference_attribution` | `:32-99` | 先確認 **session／portfolio／actor 與 owner-skill 關係**：`:50-61` 推論必須屬於這個 owner（否則 404 `inference_not_found`）、`:63-73` metadata 必須是可讀物件、`:75-82` 必須是 `prediction_type="personal_decision"`、`:84-97` `skill_unit_id` 必須逐字等於這個技能帳號（缺席＝**不是**「適用於所有 skill」） |
| `check_inference_version` | `:102-175` | 只比**持久推論的 `strategy_version_id`**（`:114`）；`:116-125` 只接受能解析成 UUID 的值；`:127-143` 「那個位置有名但不是版本」也拒（不把它讀成「沒有版本」）；`:145-146` 閘門自己沒命名版本 ⇒ 回來源所名的那個；`:148-161` 來源無名而閘門有名 ⇒ 400 `inference_version_unnamed`；`:162-174` 兩側不等 ⇒ 400 `inference_version_mismatch`，回應**同時報出兩側 id** |
| `validate_linked_inference` | `:178-199` | 順序契約（`:188-194`）：歸屬在前、版本在後——不屬於你的列不會被讀出內容來找拒因 |

**② `backend/app/api/simulations.py`（replay 門）**——`:2035-2038` 先經 `resolve_session_portfolio(db, session, owner_id=actor_id)`（**沿用正式授權契約**，不是「session 有 portfolio 就算成立」，見 14.3）；`:2063-2070` 只在 `payload.linked_inference_id is not None` 時比版本，傳進去的是 **`:2069 strategy_version_id=version.id`**＝該 session 的 `experiment_version_id`。註解 `:2055-2062` 把「不用什麼」寫死在檔內：**不是** `model_version_id`、**不是**帳號當前 selection、**不是** alias、**不是**呼叫者宣稱——理由是用會動的量去釘血緣，記下来的就不是使用者當時看的那個策略。同一個 `version.id` 也是 `:2244` 決策列的 `model_or_strategy_version_id` 與 `:2605` `HumanDecisionEvent.server_strategy_version_id`（`app/models/human_decision.py:39` NOT NULL）的來源 ⇒ 三處同一身分。

**③ `backend/app/api/paper_portfolio.py`（paper desk 門）**——`:1054-1067`：請求沒命名版本**且有**明選推論時，版本改由來源自己說（`claimed_version = validate_linked_inference(...)` 的回值）；`:1069-1072` derived 與 claimed 走**同一個** `_require_usable_and_bound_version`（擁有、凍結、run 綁定，`:995-1029`）；`:1075-1086` 再次進共用閘門比對（放兩側檢查**之後**）。匯出欄敘述同步改寫：`:1737`（空值**不等於**「版本已確認」）、`:1741`（origin 自己的說法，與決策那側可比較）。

**冪等與重播分支（簡報要「分開驗證 decision 與 human-event 重播分支」）**：已提交的同 key 重播走既有契約——`tests/test_manual_decision_books_linked_portfolio.py:1346`（同 key 回原提交、不產生第二支訂單）、`:1380`（同 key 換 payload ⇒ 拒）、`:1405`（同一個拒因按 key 重播回同一個拒）、`:2090`（**同 key 換 `linked_inference_id` ⇒ 既有衝突契約拒**）；decision 列與 human event 列的**持久**血緣各自由 `:1888`（每一扇門讀回同一 inference）、`:2286`（replay 帳本列持有請求所名的 inference）、`:2334`（沒命名 inference 的按键在帳本記 **NULL**）、`:2395`（event 自己的門顯示血緣、更正不丟失）與本輪 `tests/test_trained_inference_lineage_chain.py:471`（**一次提交寫的兩張表同時讀回同一 origin＋同一版本**）證。

**歷史資料**：`check_inference_version` 只在欄位**缺席**時回 `None`（`:114`、`:145-146`），不寫回、不補值、不改既有列；本輪**沒有** back-fill 任何猜測版本，14.6 那三支 run 之後 `tree-digest-before == after`，受測內容未被跑動。

### 14.2 上輪那 10 支紅燈在本輪的歸屬（逐類處置，不抹差異）

| 類 | 上輪定性 | 本輪處置（現測行號） |
|---|---|---|
| **7 支** `_record_inference` 系列 | 測試**手工造**的 `usage_records` 缺 `strategy_version_id` | 該 fixture 的本意**就是**「同版本的新推論」，因此補**真實欄位**：`tests/test_manual_decision_books_linked_portfolio.py:251` 現在把 `strategy_version_id` 做成**必填引數**（docstring `:252-268` 說明「本 helper 代表的是被測策略那個模型所产的推論」），寫入 `:280`。12 個呼叫點逐一支傳該帳號 session 自己的版本：`:1893 :1943 :2054 :2075 :2104 :2105 :2169 :2299 :2344 :2371 :2378 :2411` |
| **保留的缺欄反控** | — | 「**缺欄位本身**」這一類**沒有被抹掉**：`:297 _record_lineage_row` 仍是直接寫一列**不含** `strategy_version_id` 的 metadata，唯一使用者 `:1993 test_an_origin_that_records_no_version_is_admitted_and_stays_silent` 就是那個反控（它證的是**舊格式仍可入帳、且讀回不得被讀成已確認版本**）；另一支用「真推論但版本被收回」做反控：`:2684` |
| **2 支** `TestLineageFromAServedPrediction` | **真實語意差異**（replay session 的版本 ≠ 被服務模型訓練自的版本） | **不改 fixture 抹平差異**，改以新契約寫明預期：`:2555` 斷言 replay 門 **400 `inference_version_mismatch`**（且兩側 id 都報出來）、desk 門明示同一版本 ⇒ 同一拒因、desk 省略版本 ⇒ `version_not_usable`（`state="unlocked"`，**只能**來自推論而非請求），凍結那個版本後同一請求 **201** 且記的是 derived 那個版本；`:2684` 對「版本被收回（revoked）」的來源寫新契約預期 |
| **1 支** 兩門同 corpus 的一致性閘 | 它報 `replay=(400,'inference_version_unnamed') desk=('accepted',None)`——正是上一類反面證據 | 保留原斷言、**不放寬**：`:2141 test_both_formal_doors_answer_one_lineage_corpus_identically`＋配套 `:2228 test_a_lineage_refusal_leaves_no_record_behind_on_either_door` 構成**兩門一致性閘**，本輪在 14.4 的两次 run 與 14.6 run 3（218 節點）裡**重跑並通過** |

### 14.3 ②paper desk 省略版本的直接回歸＋engine 上下文

**省略版本的三條路（現測，逐條有節點）**：

1. **來源已有可驗證策略版本** ⇒ 用那個版本，並執行**與明示版本同一套**資格／run 綁定檢查：`paper_portfolio.py:1060-1072`；證據 `test_manual_decision_books_linked_portfolio.py:2650-2666`（凍結後同一請求 201，持久列 `linked_experiment_version_id == 來源所名的版本`，匯出 `inference_source_state="present"`）與不合即拒 `:2619-2625`（derived 出來的是未凍結版本 ⇒ `version_not_usable`）。
2. **舊來源無版本** ⇒ 保留既定的**無版本相容行為**，且讀回**不得**稱為「版本已確認」：`:1993-2041`（201＋每個版本欄位都空＋**對敘述本身下斷言**：`column_semantics["linked_experiment_version_id"]` 必須含 `"not a verified absence"` 與 `"never means the version was confirmed"`）。
3. **明示版本而來源缺席或不相等** ⇒ 仍拒：`:2555`（mismatch）、`test_trained_inference_lineage_chain.py:295`（來源无名）、`:370`（另一支已凍結版本）。**沒有來源**（不附 `linked_inference_id`）仍走原契約：`:1973`、`:2334`——明選推論的分支**不會**顺帶附上一支「最新推論」，因為 `:1060` 的 derive 只在 `payload.linked_inference_id is not None` 時才發生。

**三扇門在同一 owner／skill／strategy-version 下的持久結果對照**（`tests/test_trained_inference_lineage_chain.py:538`，本輪新節點）：規則 engine 的 `advance` **先跑**（此時還沒有人宣告意圖），之後 desk 明選 inference 得 201、replay 提交同一 inference 得 200；讀回 `decision_records`：**human 兩列**都帶同一 `linked_inference_id` ＋ `linked_experiment_version_id == 那個凍結版本`；**system 全部列 `linked_inference_id IS NULL`**（`:600-604`）且匯出那側 `inference_source_state=="none"`、三個版本／推論欄全空（`:612-617`）。`:596-599` 還先斷言 system 列**非空集合**——否則這是一場空集對空集的「通過」。**沒有為了綠燈自動附最新推論。**

**授權未擴大**：replay 與 desk 都用既有正式契約解析帳號（`simulations.py:2035-2038` 經 `resolve_session_portfolio(..., owner_id=actor_id)`；desk 側 `_get_portfolio_with_ownership` `:1041`），本輪**未新增任何繞過**；「session 綁了帳號」不等於「這個 actor 擁有它」的正控仍在上輪那支：`test_manual_decision_books_linked_portfolio.py:1574 test_a_binding_to_another_owners_account_is_refused_as_a_forged_link`（本輪 218-run 內通過）。**交易 journal 語意未改**：本輪只動了 `JOURNAL_COLUMN_SEMANTICS`／`DECISION_HISTORY_COLUMN_SEMANTICS` 裡 `linked_inference_id` 與兩個版本欄的**敘述**（`:1737`、`:1741`），欄位集合、排序與列的產生條件未動。

### 14.4 ③解除 B 的**資料準備**阻塞（不代本人同意、不起服務）

**三支新檔**（全部只在本輪的 SQLite runner 內跑過，**未碰待批的 PG 目標**）：

| 檔 | 角色 | 關鍵契約 |
|---|---|---|
| `backend/app/services/daily_estimate_readiness.py`（326 行） | 唯讀四項前檢，`assess_readiness(db, *, owner_id, now=None)` | 四項依「缺了會被發現的順序」：①`personal_daily_estimate` 同意＋所宣稱 scope ②每個 skill 帳號：擁有、`active`、有 selection 且**能點名 model version 與 artifact**（`:selection_state` 讀 `model_specification_json` 的 `artifact_id`／`artifact_sha256`／`strategy_version_id`）③每個標的：用**同一條** `features_as_of` 讀出新棒日與年齡，比對宣告上限，並查這一 window 的帳是否已付過 ④`ai_usage` 需要花的單位 vs 同一契約下还剩多少。`:83-121` `_instrument_state` 的拒因**逐字沿用門自己的碼**（`missing_market_data`／`stale_market_data`）；**不 grant、不選版本、不預留額度、不呼叫預測閘門**；`billing_observation_allowed` 只在四項齊時為真 |
| `backend/scripts/daily_estimate_readiness.py`（100 行） | 操作者 CLI | `DATABASE_URL` 未設／`--owner-id` 非 uuid／該 user 不在這支庫 ⇒ **exit 2 且不連庫**（`:61-85`）；READY ⇒ 0，NOT_READY ⇒ 3（是答案不是 crash）；印 `report["target"]`＝`{dialect, database, host, port}`，**不印 URL、不印密碼**（`:42-50`）；`finally: rollback/close/dispose`（`:88-93`） |
| `backend/tests/test_daily_estimate_prep_readiness.py`（768 行／10 節點） | 可重用的隔離準備＋預期讀回＋停止線 | `lane` fixture（`:112-170`）**只走正式入口**：`POST /experiments/` → `POST /experiments/{e}/versions/{v}/lock` → 205 筆已確認決策 → `PUT /ml/personalization/consent`（**訓練**同意）→ `POST /ml/personal/snapshots` → `POST /ml/personal/training-jobs`（**真 sklearn 擬合、真寫盤**）→ `GET /ml/personal/models` → `POST /paper-portfolio` → `POST /models/{v}/select`。兩個合資格標的＝`INSTRUMENT` 與 `US:MSFT`（`:79`）。窗口常數由 `BARS` **推導**（`:86-93`：`WINDOW=BARS[-1]`、`BAR_AGE_DAYS=(BARS[-1]-BARS[-2]).days`），不手抄 |

**10 支節點各自證到什麼**（節點名取自本輪 `collect.txt`；行號現測）：

| 節點 | 證到什麼（持久讀回） |
|---|---|
| `:265` prepared lane 只差本人那道同意 | `missing == ["no_active_daily_estimate_consent"]`、`skills == []`、三個 unit 計數全 0；另讀 `PersonalModelSelection` 一列**確實存在**⇒ 空清單是**空 scope**，不是開關沒讀到（訓練同意在、每日同意不在 ⇒ 三份證據不可互換） |
| `:310` grant 後讀回準備身分 | `verdict=="READY"`；`selection.model_version_id`／`artifact_id` 對上準備鏈；**非循環**證：`artifact_sha256 == _artifact_sha256(當時那個目錄, artifact_id)`（對當時位元組現算）；`quota.source=="free_plan"`、`remaining == limit-used-reserved` |
| `:367` 沒棒的那支標的由 market-data 門點名 | `missing == ["missing_market_data"]`、`units_ready_to_spend == 1`（**在預留額度之前**） |
| `:399` 超過宣告上限的新鮮度 | `max_age=0` 在請求層就 422（`ge=1`）；`max_age=BAR_AGE_DAYS` ⇒ READY 且 `data_age_days == max_data_age_days`；把視窗推到一年後 ⇒ `["stale_market_data"]`、`units_ready_to_spend == 0` |
| `:440` 沒 selection 的 skill 只拒它自己 | `["strategy_scope_missing"]`、`units_declared == 4`、`units_ready_to_spend == 2`（好那支照計，壞那支不計） |
| `:476` 額度不足在**任何估計入帳前**被點名 | 先**量**出 `target_used = limit - shortfall - reserved` 再 update-or-insert（不抄數字）；`remaining == shortfall`、`missing == ["quota_exceeded"]`、**daily-service 帳本 0 列**、**未結算預留 0 筆** |
| `:553` inference kill switch on→off | active ⇒ `["kill_switch_active"]`；撤掉後同一份報告回 READY（開關**不改**其他三項） |
| `:587` **按一次正式入口**，之後它被列為「已付」 | `POST …/daily-estimate/run {"skill_unit_id":…}` ⇒ **201**、`job.scope=="manual"`、`estimated==2 refused==0 failed==0`；兩筆 `usage_records` 帶準備鏈的 `model_version_id`／`artifact_sha256`／`as_of_date`；**壁鐘**報告 `units_billed_this_window==2`、`units_ready_to_spend==0`、兩支 `already_billed is True`、`settled_inference_ids == 帳本兩列 id`，**同時**釘住的 2024 視窗仍報 0 已付／2 可花；`learning_jobs.config_json["scope"/"window"]` 與 `result_json["billed_in_window"]==2`；未結算預留 0 |
| `:684` 報告真的唯讀 | 9 張表逐表計數前後相等；同一 owner 連報兩次內容全等 |
| `:731` CLI 在報告之前就拒 | 三道 guard 各回 **2** 且**不產出報告**：`DATABASE_URL` 未設（`:750`）、`--owner-id` 不是 uuid（`:754`）、該 owner 不在這支庫（`:758`——刻意**不**把它報成 NOT_READY，否則操作者會去找一道從來不需要的同意）；之後對準備鏈那個 owner 回 **3**＝NOT_READY（有效答案、不是 crash，`:762`）。輸出含 `"verdict": "NOT_READY"`、`"no_active_daily_estimate_consent"`、`"dialect": "sqlite"`，且 `"://"` 不在輸出、那條 `configured` URL 也不在輸出（`:767-768`；`configured` 在 `delenv` **之前**讀走，否則斷言自己會 `KeyError`） |

**本輪造成的兩次真實失敗（原始物，不美化）**：

| run | 樹 digest | 結果 | 根因（讀過程式檔才歸類） |
|---|---|---|---|
| `sqlite-20261121T080020Z-54840` | `ebea4167979171cd21b09087a4aefcb9de46f62ae857113b729e3fe12cb19033` | **10 collected／8 passed／2 failed**，exit **1**，18.25 s | `FAILED …::test_an_instrument_without_bars_is_named_by_the_market_data_door`、`FAILED …::test_a_bar_older_than_the_declared_ceiling_is_reported_stale`，兩支都是 `assert 'READY' == 'NOT_READY'`。**真缺陷在服務裡**：`assess_readiness` 把逐標的的 `state["refusal"]` 收在 `skill["instruments"]`，卻**從未把它加進 `skill["refusals"]`**，所以 `missing` 空、verdict 說 READY 蓋在一支馬上要被 clock 拒的標的上。修法＝把標的層拒因去重後併入該帳號的 `refusals`（一支帳號一個理由，不逐標的重複列），並讓「已付過」不計入可花單位。**沒有改斷言、沒有放寬閘門** |
| （無對應紅燈 run） | — | — | 另一個缺陷是**靜態核對**抓到的：`daily_estimate_readiness.py` 用了 `_canonical_skill_id` 卻沒 import（一執行到分組那行必 `NameError`）。本輪把它加進 `from app.services.daily_estimates import (…)`，**沒有**假稱這是一支跑出來的紅燈 |

**副作用揭露（這支測試檔花了什麼）**：`:587` 那支**真的扣額**——在本 run 自己的暫存 SQLite 檔（該 run `identity.txt` 的 `target:` 行）寫了 **2 筆 `ai_usage` 結算＋2 筆 `usage_records`＋1 筆 `learning_jobs`**，並在 `models_dir` 的暫存目錄留真 artifact。其餘 9 支節點只讀不計費；`:684` 是 census 而非宣稱。**本輪沒有**動 PG、沒有起服務、沒有 cron、沒有瀏覽器、沒有外呼、沒有讀或印秘密、沒有 Git 寫入。

**本人要做的、且只有本人能做的那一件事（B-額度 卡在这裡）**：
`PUT /api/v1/ml/personalization/daily-estimate/consent`（`app/api/personalization.py:308-365`），body `{"skill_units":[{"skill_unit_id":"<該技能帳號 uuid>","instrument_ids":["US:AAPL","US:MSFT"],"max_data_age_days":<整數，可省略>}],"confirm":true}`。這道門 `:328-336` 只收**密碼認證的本人 session**：`request_actor_provenance(request) != HUMAN` ⇒ **403 `daily estimate consent requires a password-authenticated owner session`**，所以 **API key／agent 憑證無法代按**；本輪**未插入任何 consent 列、未代按**。測試檔裡的 grant 是**同檔內那个一次性帳號**走同一道正式門的**分支證據**，不是替本人完成的意思表示。
⇒ 因此 **B-額度 在本輪之後仍是 BLOCKED**，但原因換了一個、且可精確指認：**不缺來源材料了**（準備鏈已在 SQLite 閉合、報告與 CLI 可用），缺的是「本人在一支**已批准啟動**的棧上按這道 consent」這個動作本身；B 卡 14.5 把那次操作寫成可由本人逐字執行的一段（含 TTY 密碼輸入），代理不代填、不代按。

### 14.5 ④A／B／C／D **完整自含正文**（本輪把改動的命令／輸入／副作用加進各卡；**四張一律未執行**；不用檔名代替正文）

> 四張共同紀律（**每張都重寫一次，不互相引用**）：①身份核對在任何寫入之前，不符 ⇒ 停；②跳過＝**NOT RUN**，不是通過；③停後保留現場（`$RUN`／`$PGDATA`／log／capture），不刪、不重試、不改設定碰運氣；④**只管理本卡自己記下的 pid**（`echo $! > "$RUN/pids"`，停止只對那些 pid 發 `TERM`），不從 `pgrep` 挑別人的行程；⑤額外命令或副作用**不從另一張卡繼承批准**，需要就在本卡重列；⑥**每日估計同意只能本人按**，代理不代填、不插入 consent 列。

---

**卡 A——在新建隔離 PostgreSQL 16.2 叢集上跑「衝突門 driver 行為」＋「真併發」共 14 支具名節點。三個 verdict 各自獨立：A-Schema／A-Driver／A-併發。**

* **固定目標**：`initdb` 新建叢集，`PGDATA=${TMPDIR:-/tmp}/alethra-r19-pgdata`、socket `${TMPDIR:-/tmp}/alethra-r19-sock`、**只聽 127.0.0.1:5432**（lane 的白名單 `test_safety_gate.py:41-45`：`localhost`／`127.0.0.1`／`::1` ＋ **5432**）、庫 `alethra_owner_test`（名前綴白名單 `scripts/run_tests.sh:178`，預設 `alethra_`）。
* **輸入**：本節 14.0 那支候選 `192fd63e…`；下面逐字列出的 14 支節點名；兩個非特權角色。
* **會寫入哪裡**：新建的 `$PGDATA`（本卡唯一落地處）、`$RUN`（`mktemp -d`）、測試自己的資料表。
* **第一個副作用**：`initdb` 寫 `$PGDATA`（目錄已存在 ⇒ pgserver 自帶拒寫，不覆寫）。
* **A-0 任何寫入前的身份核對**（任一不符 ⇒ 停，不建、不 kill、不覆寫）：

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
PGB=$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin
RUN=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r19-pg-XXXXXX")
export PGDATA="${TMPDIR:-/tmp}/alethra-r19-pgdata" SOCKDIR="${TMPDIR:-/tmp}/alethra-r19-sock"
ls -d "$PGDATA" "$SOCKDIR" 2>&1                         # 兩者都必須是 No such file
lsof -nP -iTCP:5432 -sTCP:LISTEN | tail -n +2 | wc -l   # 必須 0
pgrep -fl postgres | cat                                # 只列不 kill；有任何人 ⇒ 停並回報
cd "$REPO" && git rev-parse HEAD && git status --porcelain | wc -l
cd backend && ./scripts/run_tests.sh sqlite --print-digest-only 2>/dev/null || \
  .venv/bin/python - <<'PY'
import pathlib, hashlib
h=hashlib.sha256()
for p in sorted(pathlib.Path('.').rglob('*.py')):
    if '.venv' in p.parts or '__pycache__' in p.parts: continue
    h.update(str(p).encode()); h.update(p.read_bytes())
print(h.hexdigest())
PY
```

  必須逐字等於：**HEAD `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`**、**porcelain 229 paths**、**python 樹 `192fd63e6ffd9cce8e6d5deedfb787a319ae37705484e0003193cae7d2b6404a`**。⚠️ 上面那段 inline digest 是**另一把尺**，只供人工對照；lane 自己算的 `tree-digest-before` 才是權威（`run_tests.sh` 的 scope 見 §11 那輪的記錄：`backend/**/*.py`，`.venv`／`__pycache__` 除外）。**本卡不得停掉任何不是本卡起的 postgres 行程。**
* **A-1 建叢集＋啟動（資源上限就是這兩個數）**：

```bash
"$PGB/initdb" -D "$PGDATA" --auth-local=trust --auth-host=scram-sha-256 -E UTF8 --no-sync
"$PGB/pg_ctl" -D "$PGDATA" -o "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB" -l "$RUN/pg.log" start
echo $! > "$RUN/pids"; "$PGB/pg_isready" -h 127.0.0.1 -p 5432
```

  非零 ⇒ 停、留 `$RUN/pg.log`、**不重試、不刪 `$PGDATA`**。⚠️ **揭露**：`--auth-local=trust` 讓**同使用者**任何本機程序能用 socket 以 superuser 進這台測試叢集；邊界是 socket 落在該使用者專屬 `$TMPDIR`（本機實測 `/var/folders/tk/…/T/`），**不適用於多使用者機器**。
* **A-2 一個庫＋兩個非特權角色**：`openssl rand -hex 12` 寫 `$RUN/roles.sql`（chmod 600，**不印、不進命令列**）；`CREATE ROLE alethra_r19_migrator LOGIN PASSWORD …` → `CREATE DATABASE alethra_owner_test OWNER alethra_r19_migrator` → 在新庫 `GRANT USAGE, CREATE ON SCHEMA public TO alethra_r19_migrator` → `CREATE ROLE alethra_r19_app LOGIN PASSWORD …` ＋ 只給 `CONNECT, TEMP`／`USAGE`／`SELECT,INSERT,UPDATE,DELETE`＋sequences `USAGE,SELECT`（`ALTER DEFAULT PRIVILEGES FOR ROLE alethra_r19_migrator`）。閘門來源：`test_safety_gate.py:110-128`（查 `pg_roles` 的 `rolsuper,rolcreatedb,rolcreaterole,rolbypassrls`，任一為真即硬拒）＋`:172`＋`:223`。⚠️ 角色不能有 CREATEDB ⇒ 建庫這步必須在閘門**外面**走 socket。
* **A-3 空庫升全鏈**：`cd "$REPO/backend" && .venv/bin/python -m alembic -c alembic.ini heads` 然後 `… upgrade head`。本輪以 `Alembic ScriptDirectory` 現測（離線、不連庫）：**`heads == ['d4e5f6g7h8i9']`、共 57 支、base `55a8d07a7f40`**（與 §13.9.7-A 同值：本輪**沒有**新增 migration）；`heads` 多於一／不等於該 id／`DUPLICATE_REVISION`｜`BRANCHED_TREE`｜`MISSING_DEPENDENCY` ⇒ 停。**不 autogen、不 downgrade、不 drop、不跑其他 migration。**
* **A-Schema（verdict 1）**——唯讀：`\dn`、`SELECT current_database(), version();`、查 `pg_indexes` 有 `paper_orders.uq_paper_order_portfolio_idempotency`（partial：`WHERE idempotency_key IS NOT NULL`，`app/models/paper_portfolio.py:166-172`）與 `human_decision_events.uq_human_decision_owner_session_key`（`app/models/human_decision.py:80-84`），查 `information_schema.columns` 有 `decision_records.linked_inference_id` 與 `human_decision_events.linked_inference_id`（`human_decision_events.server_strategy_version_id` 為 **NOT NULL**，`app/models/human_decision.py:39`）且型別＝`uuid`。**verdict 只到「索引／型別在不在」**；driver 回什麼 SQLSTATE 一律屬 A-Driver，**不得寫成「衝突在 PG 已驗」**。
* **A-Driver（verdict 2）**——8 支具名節點（`tests/test_conflict_doors_at_entry.py`，行號為 §13.9.7-A 現測、本輪該檔**未改**）：`:307 test_door_A_answers_only_its_own_index_with_a_conflict`／`:348 test_door_A_does_not_rename_its_own_not_null_failure`／`:413 test_door_B_answers_a_conflict_on_the_order_index_it_owns`／`:450 test_door_B_lets_another_doors_index_through_as_a_500`／`:498 test_door_C_does_not_answer_another_doors_order_index_as_its_own`／`:570 test_door_D_answers_its_own_index_with_a_conflict`／`:649 test_door_D_does_not_rename_the_order_index`／`:698 test_the_entries_refuse_an_empty_key_so_the_index_cannot_be_reached_sequentially`：

```bash
cd "$REPO/backend"
export ALETHRA_PG_TEST_URL="postgresql+psycopg2://alethra_r19_migrator:<讀 $RUN/roles.sql，不印>@127.0.0.1:5432/alethra_owner_test"
export TEST_MODE=true DATA_PROVIDER=demo
./scripts/run_tests.sh pg \
  tests/test_conflict_doors_at_entry.py::test_door_A_answers_only_its_own_index_with_a_conflict \
  tests/test_conflict_doors_at_entry.py::test_door_A_does_not_rename_its_own_not_null_failure \
  tests/test_conflict_doors_at_entry.py::test_door_B_answers_a_conflict_on_the_order_index_it_owns \
  tests/test_conflict_doors_at_entry.py::test_door_B_lets_another_doors_index_through_as_a_500 \
  tests/test_conflict_doors_at_entry.py::test_door_C_does_not_answer_another_doors_order_index_as_its_own \
  tests/test_conflict_doors_at_entry.py::test_door_D_answers_its_own_index_with_a_conflict \
  tests/test_conflict_doors_at_entry.py::test_door_D_does_not_rename_the_order_index \
  tests/test_conflict_doors_at_entry.py::test_the_entries_refuse_an_empty_key_so_the_index_cannot_be_reached_sequentially
echo "A-Driver lane rc=$?"
```

  判準：`A`／`B` 的 409 必由 `uq_paper_order_portfolio_idempotency`＋SQLSTATE **23505** 組成；`C`／`D` 必由 `uq_human_decision_owner_session_key`＋23505；三支反控（`:450`／`:498`／`:649`）必為 **500／非 409** 且回應體不出現 SQL 語句、綁定參數或他人資料；`:348` 必為 NOT NULL（23502）而**不是** 409。
* **A-併發（verdict 3）**——6 支具名節點：`tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle`（class 現在 `:2859`，本輪該檔行號位移 ⇒ **重新現測**）的 `:2866 test_two_requests_on_one_idempotency_key_leave_exactly_one_order`／`:2912 test_a_stale_account_read_cannot_overwrite_a_committed_booking`／`:2977 test_a_manual_submit_racing_an_advance_stays_consistent`，加 `tests/test_daily_estimate_two_workers.py:262 test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier`＋`:607 test_two_processes_on_one_window_bill_each_business_key_once`、`tests/test_daily_model_estimate.py:794 test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice`。先 `export ALETHRA_RACE_CAPTURE_JSON="$RUN/race-capture.json"`（`_write_capture()` 不建父目錄 ⇒ `$RUN` 必須已存在）。計時契約：`_TIMEOUT_S = 60`、`lock_timeout '30s'`／`statement_timeout '120s'`／`idle_in_transaction_session_timeout '90s'`。**額度前提：這個目標的 `ai_usage_limit` 維持預設 10（`app/auth/entitlements.py:70`），不得套 B 的 1**，否則 429 會把真缺陷蓋成限流。帳本判準（`tests/daily_estimate_race.py:131 assess_race()` 共 12 條）逐條要過：每 `(owner, skill, instrument, window)` 恰 1 列、`metadata["via"]=="daily_service"`、每列恰 1 筆 settled 預留、非終態預留＝0、落敗那支具名 `already_estimated_this_window`、兩個 job 摘要的 `billed_in_window` **不相加**。`Barrier` 未湊齊／計時未滿／`proven=false` ⇒ **NOT RUN／INVALID**，**不是 PG PASS**。⚠️ **本輪增量**：replay 門現在比版本（14.1②），`TestConcurrentLifecycle` 那 3 支在 PG 上會經同一條 `_submit_manual_order`；它們用的 fixture 已補真實 `strategy_version_id`（14.2 第一類），所以**判準不需放寬**、也**不該**再期望 `inference_version_unnamed`。
* **停止線**：非零 exit｜lane rc 2（`scripts/run_tests.sh:170-172`，空 `ALETHRA_PG_TEST_URL`）或 rc 3（`:184-185` preflight 拒；rc 語意 `:56`）｜head 不符｜四旗標不為全 `f`｜`current_database() != alethra_owner_test`｜反控那三支出現 409｜以跳過取代執行｜任一斷言失敗。
* **停止命令**：`xargs -n1 kill -TERM < "$RUN/pids" 2>/dev/null || true` → `"$PGB/pg_ctl" -D "$PGDATA" -m fast stop` → `pg_isready` 必非零 → `lsof -nP -iTCP:5432 -sTCP:LISTEN | tail -n +2 | wc -l` 必為 **0**。**不得**用已停端點的 HTTP 回應證明停止；**不刪 `$PGDATA`**。
* **本卡副作用清單（執行前重述给自己）**：建 `$PGDATA`＋`$SOCKDIR`、`$RUN` 4 檔（`pg.log`／`roles.sql`(600)／`race-capture.json`／`pids`）、57 支 migration 的 DDL、測試資料列。**不寫 `$REPO`、不寫 `.venv`、不 push、不 commit。**
* **狀態：NOT RUN**（本輪未啟動 PG）。三個 verdict 各自回報，**任一通過都不替另外兩個背書**。

---

**卡 B——自然排程（不改時鐘、不用手動入口冒充）在**自己一支**叢集上跑一次。四個 verdict：B-準備／B-額度／B-停用／B-外連觀察。**

* **為什麼自帶一支叢集**：本卡不跑 `run_tests.sh pg`，`test_safety_gate.py:41-45` 的 5432 白名單不約束它；用自己的一支，就**不需要也不得引用 A 卡留下的現場**。
* **固定目標**：`PGDATA=${TMPDIR:-/tmp}/alethra-r19-cron-pgdata`、socket `${TMPDIR:-/tmp}/alethra-r19-cron-sock`、**127.0.0.1:5433**、庫名必須含 `alethra_personal_e2e`。
* **B-1 建自己的叢集＋一支庫＋升鏈（本卡自含；不引用 A 卡任何命令）**：

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
PGB=$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin
RUN=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r19-cron-XXXXXX")
export PGDATA="${TMPDIR:-/tmp}/alethra-r19-cron-pgdata" SOCKDIR="${TMPDIR:-/tmp}/alethra-r19-cron-sock"
ls -d "$PGDATA" "$SOCKDIR" 2>&1                          # 兩者都必須是 No such file，否則停
lsof -nP -iTCP:5433 -sTCP:LISTEN | tail -n +2 | wc -l    # 必須 0
"$PGB/initdb" -D "$PGDATA" --auth-local=trust --auth-host=scram-sha-256 -E UTF8 --no-sync
"$PGB/pg_ctl" -D "$PGDATA" -o "-p 5433 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB" -l "$RUN/pg.log" start
echo $! > "$RUN/pids"; "$PGB/pg_isready" -h 127.0.0.1 -p 5433
/usr/bin/openssl rand -hex 12 > "$RUN/pw.txt" && chmod 600 "$RUN/pw.txt"   # 不印
PW=$(cat "$RUN/pw.txt")
"$PGB/psql" -h "$SOCKDIR" -p 5433 -d postgres -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE alethra_r19_cron LOGIN PASSWORD '$PW';
CREATE DATABASE alethra_personal_e2e OWNER alethra_r19_cron;
SQL
export DATABASE_URL="postgresql+psycopg2://alethra_r19_cron:$PW@127.0.0.1:5433/alethra_personal_e2e"
cd "$REPO/backend" && .venv/bin/python -m alembic -c alembic.ini heads && .venv/bin/python -m alembic -c alembic.ini upgrade head
```

  ⚠️ 這三條是本卡自己的批准面：`initdb` 落 `$PGDATA`、`CREATE ROLE`／`CREATE DATABASE`、`upgrade head` 的 57 支 DDL（本輪現測 `heads == ['d4e5f6g7h8i9']`，**未新增 migration**）。非零 ⇒ 停、留 `$RUN/pg.log`、不重試、不刪 `$PGDATA`。`$PW` 只在本機 shell 內使用，不印出、不入紀錄。
* **B-2 資料準備（本輪解除的阻塞就在這一格）**：把 14.4 那條鏈在 PG 上重跑一次，**全部走正式入口**，由本人（或本人明確授權的操作者）在 `8199` 那支服務上執行：
  1. `POST /api/v1/experiments/`（建研究）→ 2. `POST /api/v1/experiments/{id}/versions/{vid}/lock`（凍結策略版本）→ 3. 205 筆已確認決策（**只能來自真實決策入口**；測試用的 `_seed_confirmed_decisions` 是直插，PG 上直插要**單獨列為批准面**並寫明它不是正式入口）→ 4. `PUT /api/v1/ml/personalization/consent {"strategy_version_ids":[vid],"confirm":true}`（**訓練**同意，同样只能本人）→ 5. `POST /api/v1/ml/personal/snapshots {"strategy_version_id":vid}` → 6. `POST /api/v1/ml/personal/training-jobs {"snapshot_id":…,"model_type":"logistic_regression"}`（**202**，之後由排程器或 `run_claimed_job` 真的擬合、真的寫 artifact）→ 7. `GET /api/v1/ml/personal/models?strategy_version_id=vid` 讀回 `model_version_id`／`artifact_id`／`artifact_sha256` → 8. `POST /api/v1/paper-portfolio {"initial_virtual_cash":100000.0,"currency":"USD"}` → 9. `POST /api/v1/ml/personal/models/{model_version_id}/select`（綁到那個 skill 帳號）。
  ⚠️ **兩支合資格標的的棒列**：lane 需要 `market_data_bars` 有該標的的可見日線（新鮮度由 `app/services/personal_dataset.features_as_of` 讀，拒因 `missing_market_data`／`stale_market_data`）。SQLite 測試裡是 `_seed_bars` 直插 `provider_id="synthetic"`；**PG 目標上要嘛用既有收集入口真的抓（有外連風險，屬另一張卡），要嘛把那次直插單獨批准並標為非正式入口**。本輪未在任何 PG 上寫過棒列。
  **B-準備（verdict）**：上面 1–9 完成後，`GET /api/v1/ml/personalization/daily-estimate/consent` 應回報**尚無每日同意**（不是錯誤），且 readiness 報告（B-4）此時必須是 `missing == ["no_active_daily_estimate_consent"]`、`skills == []`。這格證的是**材料齊**，不證任何計費。
* **B-3 本人完成那道只有本人能做的同意（代理不代按）**：`app/api/personalization.py:308-365` 只收 `request_actor_provenance(...) == HUMAN`（`app/auth/provenance.py:30-36`：JWT 的 `auth_amr` 含密碼方式才算本人；帶 API key ⇒ `request.state.api_key_scopes` 非 `None` ⇒ `AGENT` ⇒ **403**）。所以這一步**必須由本人在自己的 Terminal 裡輸入密碼**（本機 agent 的 Bash 沒有 TTY，`read -s` 拿不到隱藏輸入）。正文（本卡唯一由人操作的一段）：

```bash
read -rs -p "owner password: " OPW; echo
TOK=$(curl -sS -X POST http://127.0.0.1:8199/api/v1/auth/login -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$E2E_EMAIL" --data-urlencode "password=$OPW" | .venv/bin/python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -sS -X PUT http://127.0.0.1:8199/api/v1/ml/personalization/daily-estimate/consent \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"skill_units":[{"skill_unit_id":"<B-2 第 8 步那個 uuid>","instrument_ids":["US:AAPL","US:MSFT"]}],"confirm":true}'
```

  預期 **200** ＋ `{"consent":{…,"skill_units":[…]}}`；**403 `daily estimate consent requires a password-authenticated owner session`** 出現 ⇒ 說明這張卡試圖以憑證代按，**停**。`$TOK` 不落盤、不印；`$OPW` 只在記憶體。**撤回**走 `DELETE …/daily-estimate/consent`（同檔 `:368`），同樣只能本人。
* **B-4 只讀就緒檢查（在起排程器之前）**：

```bash
cd "$REPO/backend" && DATABASE_URL="$DATABASE_URL" .venv/bin/python scripts/daily_estimate_readiness.py --owner-id "$OWNER_UUID"; echo "readiness rc=$?"
```

  契約（`scripts/daily_estimate_readiness.py:53-96`）：**0**＝READY、**3**＝NOT_READY（有效答案）、**2**＝guard 拒（`DATABASE_URL` 未設／uuid 不合法／該 owner 不在庫）。輸出自報 `target{dialect,database,host,port}`，**不含 URL、不含密碼**。四項齊 ⇒ `billing_observation_allowed=true`；**任一缺 ⇒ 本卡不得報告任何計費觀察**。
* **B-5 自然觸發**：`DAILY_ESTIMATE_HOUR_UTC = 5`（`app/services/daily_estimates.py:82`；`CronTrigger(hour=…, minute=0, timezone="UTC")` 在 `app/workers/learning_scheduler.py:183-185`）。**必須在 05:00:00Z 之前把棧起好**；錯過 ⇒ 該次 **NOT RUN**（下一窗口是隔天 05:00Z），**不調時鐘、不把手動 `trigger_daily_estimate` 冒充 cron**。啟動命令（`cd "$RUN"` 是承重的——`app/config.py:560` 的 `env_file=".env"` 按行程 cwd 解析，`cd backend` 會讓本機 `.env` 生效）：

```bash
cd "$RUN" && export JWT_SECRET_KEY=$(/usr/bin/openssl rand -hex 16)   # 寫 $RUN/jwt-secret.txt(600)，不印
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME="$HOME" PYTHONPATH="$REPO/backend" \
  DATA_PROVIDER=demo AI_PROVIDER=mock APP_ENV=development \
  ENABLE_LEARNING_SCHEDULER=true LEARNING_SCHEDULER_JOBS=daily_model_estimate \
  AUTO_COLLECTION_ENABLED=false RESEARCH_AUTH_BYPASS_ENABLED=false LIVE_TRADING_ENABLED=false \
  DATABASE_URL="postgresql+psycopg2://<本卡 role>@127.0.0.1:5433/alethra_personal_e2e" \
  "$REPO/backend/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8199 >"$RUN/service.log" 2>&1 &
echo $! > "$RUN/pids"
```

  釘值定性（如實）：`RESEARCH_AUTH_BYPASS_ENABLED` 全樹只有 1 個消費者（`app/auth/jwt.py:51`）；`LIVE_TRADING_ENABLED` 在 `app/**` 下 **0 個消費者**——兩個都是**消歧**，不是防護。資源上限：`LEARNING_SCHEDULER_MAX_OWNERS` 維持預設 25（`config.py:153`）；本卡只種 1 owner；DB 端 25 連線／64MB。
* **B-額度（verdict）**：判準是**帳本**不是回應體——`usage_records` 中 `metadata_json["via"]=="daily_service"` 的新增列數 ＝ B-3 所宣稱的 (skill, instrument) 業務鍵數，每列自帶 `model_version_id`＋`artifact_sha256`＋`as_of_date`，且 `quota_reservations` 無非終態殘留。**一次結算＋一個具名額度拒絕只能證明「額度路徑」這一條**（拒因讀法：`_refusal_code` `app/services/daily_estimates.py:1190-1201` 把 `{"error":"quota_exceeded"}` 讀成 `quota_exceeded`），它**不**順帶證明排程、外連或停用。额度不足那一腿離線已證（`tests/test_daily_model_estimate.py:1734 test_an_exhausted_quota_refuses_before_spending_anything` 斷言 `refused[0]["reason"]=="quota_exceeded"` 且零帳；本輪 `:476` 又從報告側證一次），所以 B 在 PG 上要補的是**結算腿**，不必再花額度去重證拒路徑。
* **B-停用（verdict）**：`ENABLE_LEARNING_SCHEDULER=false`（預設 false，`app/config.py:140`）起一次，**必須另外數** `learning_jobs` 與 `usage_records`（`via=="daily_service"`）的新增列＝**0**——不能只用「tick 沒跑」的敘述代替計數。對照組 `=true` ＋ `LEARNING_SCHEDULER_JOBS=daily_model_estimate`（預設空 ⇒ 取 `config.py:55-59` 那三支，**其中沒有 estimate**）時，啟動日誌必須**只**點名這一支 lane。
* **B-外連觀察（verdict）**：主證據＝`lsof -nP -p "$(cat $RUN/pids)" -a -i` 中對端非 `127.0.0.1`／`::1` 的列數＝**0**。⚠️ **一句 `lsof` 快照只能寫「採樣未觀察到外連」**，不能寫「無外連」；要收據得按週期取樣並記錄每次取樣時刻。`env -i` ＋ `DATA_PROVIDER=demo AI_PROVIDER=mock` 是**機制**不是保證，log grep 只當旁證。
* **第一個副作用**：`initdb` 寫 `$PGDATA`（本卡自己的）→ `upgrade head` 的 DDL → B-2 各正式入口的寫入（含真 artifact 落 `MODELS_DIR`）→ B-3 那一道 consent（**本人**的意思表示列）→ tick 的帳本列。**服務啟動本身不寫任何業務列。**
* **停止線**：05:00Z 後 90 秒內無 `learning_jobs` 列 ⇒ 停並存 `$RUN/service.log`；啟動日誌點了第二支 lane ⇒ 立即 `stop_learning_scheduler()`（`app/main.py:72-74`）；readiness rc 為 **2** ⇒ 是本卡自己沒給對環境，**不是** NOT_READY，先修命令再說；任一 `KillSwitchScope`（global／training／execution／inference，`app/models/kill_switch.py:10-15`；計費 lane 查 `inference`）為 active 時 tick 竟寫了帳 ⇒ 這是本卡要抓的缺陷，**原樣回報**不重跑。
* **停止命令**：`kill -TERM "$(cat $RUN/pids)"` → `lsof -nP -iTCP:8199 -sTCP:LISTEN | tail -n +2 | wc -l` 必為 0 → 再停本卡自己的叢集（`pg_ctl -m fast stop`，用 `$RUN/pids` 與自己的 `PGDATA`）→ **不刪 `$PGDATA`**、不撤 consent（撤不撤由本人決定）。
* **狀態：NOT RUN**。**B-額度 仍 BLOCKED**，但缺件已改名：不再是「沒有可計費帳號的來源」（本輪已在 SQLite 把準備鏈、報告與 CLI 閉合），而是「**需要本人在一支已批准啟動的棧上執行 B-3 那一段**」——在那之前代理不代按、不插列。B-準備／B-停用／B-外連＝材料齊、待批。

---

**卡 C——只在 8101／3101 的 dev 功能驗收棧上跑既定兩支 spec。五個 verdict 互不取代：C-DDL／C-自動化／C-`--list` 探測／C-本人同意撤回／C-可能扣額。結果一律標 dev。**

* **C-DDL（verdict，**本輪獨立列出、不混進「起棧**」）**：起棧本身就是 DDL——`start_candidate_stack.sh` `:575` 讀 `heads`，`:585` 與 `:628` 對它自己那支 `DATABASE_URL` 執行 `alembic upgrade head`。⇒ 批 C 之前必須先**指名它寫哪支庫**並**單獨批准這次 DDL**；把它指向 A 或 B 的庫＝在那支庫上多跑一次 migration（**該副作用不從別張卡繼承**）。核對：起棧前 `cd "$REPO/backend" && .venv/bin/python -m alembic heads`（本輪現測仍 `d4e5f6g7h8i9`；本輪**未新增** migration）、起棧後從服務讀 `schema-head` 對照；兩者不符 ⇒ 停。
* **C-起棧（不是 verdict，是前提）**：`cd "$REPO" && ./start_candidate_stack.sh`，後端 `127.0.0.1:8101`（腳本 `:397` 起 uvicorn，port 預設見 `:601`），前端 `next dev` `:446`（port 預設 `:602`＝3101）。三個必須明釘的值：`DATA_PROVIDER=demo AI_PROVIDER=mock RESEARCH_AUTH_BYPASS_ENABLED=false`（不釘 false 的話 `app/auth/jwt.py:51` 會以「第一位 active user」放行 loopback，**把「本人操作」那格掏空**）＋ `LIVE_TRADING_ENABLED=false`（如實：0 個消費者）。Docker 路線不存在（`command -v docker` 無）。
* **C-自動化（verdict）**：

```bash
cd "$REPO/frontend" && E2E_FRONTEND_BASE=http://127.0.0.1:3101 E2E_BACKEND_BASE=http://127.0.0.1:8101 \
  E2E_BROWSER_CHANNEL=chrome E2E_CRED_FILE=<本機憑證檔> \
  node_modules/.bin/playwright test e2e/manual-decision-lineage.spec.ts e2e/daily-estimate-journey.spec.ts
```

  **只這兩支**，不掃全 e2e。驗收：①`/health/ready` 通過；②預檢回應含 `access-control-allow-origin: http://127.0.0.1:3101`（`app/main.py:27` 只讀 `settings.cors_origins_list`，預設 `http://localhost:3000`，`config.py:79`——`localhost` 與 `127.0.0.1` **不是同一 origin**）；③只報 **collected／passed／skipped／failed** 四個數，不拿 collected 當 passed。
* **C-`--list` 探測（verdict，本輪標**未確認**）**：`frontend/playwright.config.ts:36-38` 的 `webServer.url`（取自 `:5`／`:24` 的 `E2E_FRONTEND_BASE`）**敘述上**會讓任何 Playwright 命令先探測／拉起那個 origin。⚠️ **本輪未實證**（未執行任何 Playwright 命令，含 `--list`）⇒ 這一格是**未確認**，不是 PASS、也不是「已證明非唯讀」；批 C 時若要依賴這個性質，先跑 `--list` 並在**起棧前**用 `lsof -nP -iTCP:3101 -sTCP:LISTEN` 前後取樣證明。
* **C-本人同意／撤回（verdict，只能本人）**：`data-testid="daily-estimate-acknowledge"`（`frontend/src/components/training/DailyEstimatePanel.tsx:676`）與 `daily-estimate-revoke-all`（`:695`）。spec 只斷言按鈕存在與文案；**代理不替本人按**，本卡文字也不構成同意。按下之後的**持久證據**是 B-4 那個報告（或 `GET …/daily-estimate/consent`）裡的 scope 與 `granted_at`，不是畫面。**與訓練同意、與 activation approval 分開**：每日估計同意是另一個用途（`PersonalizationConsent.purpose == "personal_daily_estimate"`，`app/services/daily_estimates.py:61`），按下它**不等於**選版本／啟用那兩件事，證據不得互換。
* **C-可能扣額（verdict）**：只有設了 `E2E_DAILY_ESTIMATE_SKILL` 才走進那幾格（`frontend/e2e/daily-estimate-journey.spec.ts:108` `?? ''`、`:111` 具名跳過）。未獲准扣額 ⇒ **保持未設**，並把那句跳過原因逐字入帳（前置缺件 ≠ 通過）。另兩格前置：`E2E_CRED_FILE`（`frontend/e2e/runtime.ts:34`；缺 ⇒ `TIER_B_SKIP`，`:26-30`）、`E2E_LINEAGE_ASSET`（預設 `DEMO-ALPHA`，`manual-decision-lineage.spec.ts:111`）。**代理不代填。**
* **輸入／寫入處／第一個副作用**：輸入＝14.0 那支候選＋該 dev 棧連到的 `DATABASE_URL`；第一個副作用＝**C-DDL 那次 `upgrade head`**；其後是 spec 在該庫裡建的測試帳號／決策列，以及（若本輪 14.1 生效）replay 提交所帶 inference 的版本比對結果。
* **停止線**：DDL 前後 head 不符／①／②／③ 任一不符、或前置齊全卻 failed ⇒ 停、存 log、原樣回報，**不重跑、不改設定碰運氣**。
* **停止命令**：讀 `start_candidate_stack.sh` 自己記的 pid 檔並只對其發 `TERM`（**不從 `pgrep` 挑別人的行程**）。
* **狀態：NOT RUN**（未獲准啟動服務／瀏覽器；C-本人、C-扣額兩格本質上必須由本人動作；C-`--list` ＝**未確認**）。

---

**卡 D——在新目錄裡用本機來源重建候選，並跑本輪具名節點（純離線、零網路、零 Git 寫入）。**

* **重現的是什麼範圍（本輪逐路徑講清，不用計數代替清單）**：
  * **受測內容的來源**現測＝`git ls-files -co --exclude-standard` 那 **1,825 條、35.4 MB**，而且**加性恰對上**：已追蹤 **866** ＋ 未追蹤且未被 ignore **959** ＝ 1,825（本輪現測，無重疊、無遺漏）。其中 `backend/` **569 條**（含本輪 3 支新檔與 `app/services/decision_lineage.py` 等 57 支未追蹤 `.py`）。
  * **已刪除路徑**：本輪現測 `git ls-files -d` ＝ **0** 條。⚠️ 記一筆避免重蹈：`git ls-files -cd` 回 866 **不是**「866 檔已刪除」——`-c`／`-d` 是過濾器**聯集**，那 866 條逐條 `[ -e ]` 全存在（14.0 第 1 點）。執行本卡時若 `git ls-files -d` 變成非 0，那些路徑**會被寫進 tar 清單卻在盤上不存在**（`tar cf -` 因此非零）⇒ 把它們逐條列入 `$DST/DELETED.txt`、**不還原**，並以「來源樹與本卡前提不符」停止，**不**在 `$REPO` 裡新建同名檔。
  * **刻意排除（且必須說明為什麼）**：①`alethra-evidence/` **873 條**（歷輪 patch／log 附件，不是被測來源）；②**61 條** `*.log`（55 條在 `alethra-evidence/`、6 條在 `backend/tests/acceptance_evidence/round{1,2,3}_*.log`）；③`backend/postgres`（**0-byte 誤檔**，前輪已記錄、**未刪**，交本人裁決 ⇒ 不帶進 `$DST` 也不動 `$REPO`）；④**秘密／本機環境**：`backend/.env.test`（未追蹤**且未被 ignore**、含憑證鍵名，14.0 第 2 點）與 root `backend/.env`（已被 `.gitignore:14` 排除，不在 `-co` 集內）；⑤共享 DB：實測**未追蹤集內 `.db` 檔數＝0**，因為兩條 lane 的庫都落在 `${TMPDIR}`（各 run `identity.txt` 的 `target:` 行）——所以這一項**不需要排除動作，但必須如實寫成「沒有這種檔在來源裡」而不是「我排除了它」**。
  * **不重現**：PG 執行（A）、自然 cron（B）、瀏覽器（C）、`backend/.venv` 的內容（**470 MB／17,515 檔**，離線無法重建 ⇒ 以整份複製帶過去）、gitignored 資料、遠端倉庫、以及**整套測試**。D 跑的是**具名子集**，不是整套 PASS，也**不是逐位元組全樹重建**。
* **固定目標／寫入處**：`DST=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r19-repro-XXXXXX")`，唯一寫入目錄；**不寫 `$REPO`、不碰 `.git`、不 push、不 stage、不 commit**。
* **第一個副作用**：在 `$DST` 裡解 tar（純新檔）。
* **逐條命令**（一步＝一個可單獨否決的動作）：

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
DST=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r19-repro-XXXXXX")
cd "$REPO"
# D-1 清單先落盤（可被人逐行讀，而不是只看到一個計數）
git ls-files -co --exclude-standard -z | tr '\0' '\n' | grep -v '^alethra-evidence/' \
  | grep -v '\.log$' | grep -vx 'backend/postgres' | grep -vx 'backend/.env.test' > "$DST/SOURCES.txt"
git ls-files -d > "$DST/DELETED.txt"            # 本輪現測應為 0 條；非 0 ⇒ 停
wc -l "$DST/SOURCES.txt"                          # 本輪現測 946 條（1,825 − 873 − 6 列 − 1 − 1，見下）
# D-2 只解清單裡的東西：來源＝清單，不是目錄掃描
(cd "$REPO" && xargs -a "$DST/SOURCES.txt" -d '\n' -0 true 2>/dev/null; tar cf - -T "$DST/SOURCES.txt") | (cd "$DST/repo" && tar xf -) 2>&1 | tee "$DST/tar.log"
# D-3 帶 venv（離線重建不了它）；BSD cp 保留 mode／symlink
mkdir -p "$DST/repo/backend" && cp -R "$REPO/backend/.venv" "$DST/repo/backend/.venv"
# D-4 標記這是一顆快照，不是倉庫複製
cd "$DST/repo" && git init -q && printf 'candidate snapshot, not a repo copy\n' > "$DST/README.txt"
# D-5 跑本輪受測節點
cd "$DST/repo/backend" && ./scripts/run_tests.sh sqlite \
  tests/test_trained_inference_lineage_chain.py tests/test_decision_lineage.py tests/test_successful_inference.py \
  tests/test_daily_estimate_prep_readiness.py
```

  **入口一律走 `$DST/repo/backend/.venv/bin/python -m …`／`./scripts/run_tests.sh`，禁止 `bin/pytest`、`bin/alembic`**：§13.9.7-D 現測 `.venv/bin` 38 檔裡 **31 支 shebang 寫死本 checkout 絕對路徑**，直接叫那些腳本會 import 回原處（本輪 venv 未動，該事實仍成立）。**不用裸 `pytest`**（提示明文）。D-2 那條 `tar -T` 讀的是**清單**，所以「排除了哪些路徑」是可逐行審查的，不是一句宣稱。
* **驗收條件（本輪重釘，且**不拿計數冒充**）**：D-5 必須自己量出 **56 collected／56 passed／0 skipped／0 failed／exit 0**，並逐檔對上 `collect.txt`：`test_trained_inference_lineage_chain.py` **6** ＋ `test_decision_lineage.py` **26** ＋ `test_successful_inference.py` **14** ＋ `test_daily_estimate_prep_readiness.py` **10** ＝ 56。這 56 有兩段**各自量過**的來源，不是預期值：前 46 支是 §13.9.7-D 那組 3 檔在本輪候選上的延續（14.6 run 3 的逐檔 census 現測 6／26／14，該 run **0 failed**），後 10 支是 14.6 run 2 自己那個 10／10。**上輪的 44／44 前提已被取代**（鏈檔從 4 支長到 6 支）。另外 D-5 那支 run 自己的 `identity.txt` 要顯示 `cwd` 與 `interpreter` 都在 `$DST` 之下——這才是「不是從原 checkout import」的實物證據；`identity.txt` 的 porcelain 行在 `$DST` 那棵只 `git init` 過的樹上讀不出與 `$REPO` 相同的值，**此時只比 `interpreter`／`cwd`，不假裝比過候選身分**（改比對 `$DST/SOURCES.txt` 的行數與 14.0 那組現測數字）。
* **停止線**：任一非零 exit｜`DELETED.txt` 非空｜節點數不符 56｜`interpreter` 或 `cwd` 落在 `$REPO`｜`tar.log` 出现 `Cannot stat`｜`$DST` 已存在 ⇒ 停、保留 `$DST`、**不重試、不刪**。**只管理本卡記下的子行程。**
* **狀態：NOT RUN**（本輪不建 `$DST`、不複製）。

### 14.6 收斂驗證（四次 run，原始數字；**不與改動並行**）

| # | 命令（全部 `cd backend && ./scripts/run_tests.sh sqlite …`） | 結果 | run dir／樹 digest |
|---|---|---|---|
| 1 | ①②的 9 檔受影響集（`test_manual_decision_books_linked_portfolio test_trained_inference_lineage_chain test_decision_lineage test_successful_inference test_decision_history_export test_daily_model_estimate test_research_run_admission test_formal_skill_unit_authorization_endpoint test_predict_record_route_explicit_version`） | **208 collected／205 passed／3 skipped／0 failed**，exit **0**，186.36 s；`skip categories pg_only=3`；`evidence gate: ledger closes`；`tree-digest-before == after` | `sqlite-20261121T071448Z-4045`／**`144943a2fd348fe75fd764031d82b666785d80a3ce52b436a16ea7723d9de385`** |
| 2 | ③新模組單獨跑（`tests/test_daily_estimate_prep_readiness.py`） | **10 collected／10 passed／0 skipped／0 failed**，exit **0**，16.42 s；`evidence gate: ledger closes`；before==after | `sqlite-20261121T080202Z-58123`／**`192fd63e…`** |
| 3 | **①②③合併的 10 檔集**（run 1 那 9 檔＋③的新模組）——為了把「保留 44／44 與原有測試範圍」重新釘在**新候選**上，不沿用 run 1 的舊 digest | **218 collected／215 passed／3 skipped／0 failed／0 error**，exit **0**，**210.63 s**；逐檔現測 census：56／6／26／14／9／48／32／13／4／10；`skip categories pg_only=3 live_stack=0 other=0`；`reported units 218 = collected 218 + collection-level 0`、`unresolved 0`、`duplicate 0`、`record without id 0`；`evidence gate: ledger closes against this run's own artifacts`；**`tree-digest-before == after == 192fd63e…`**（本節所有行號綁它） | `sqlite-20261121T080254Z-59606`／**`192fd63e…`** |
| 4 | run 2 之前那次（同模組、修 `missing_market_data`／`stale_market_data` 傳播之前） | **10 collected／8 passed／2 failed**，exit **1**，18.25 s（**保留這支失敗當本輪的真實缺陷證據**，根因見 14.4） | `sqlite-20261121T080020Z-54840`／`ebea4167…` |

**「保留原有測試範圍」這一格現在怎麼讀**：§13.9.3 run 3 的 44／44（`953c7345…`）已被本輪**取代而非否證**——同一組 3 檔在本輪候選上是 **46 支**（鏈檔 4→6，新增的是 14.3 那兩支），而 14.6 run 3 顯示這 46 支在新候選上**全數通過、0 失敗**；原有 9 檔的 208 支裡，只有那 3 支 pg_only 跳過照舊跳過，**沒有任何一支被改成跳過來換綠燈**。

### 14.7 本輪寫入清單與副作用（全部 in-place 編輯＋3 支新檔；零 Git 寫入、零網路、零服務）

| 檔 | 動作 | 位置（現測） |
|---|---|---|
| `backend/app/services/decision_lineage.py` | 拆成歸屬段＋版本段＋門面；版本段現在**回傳**來源所名的版本；「那個位置有名但不是 uuid」也拒；模組敘述改為「兩門都跑」 | `:10-19`、`:32-99`、`:102-175`、`:178-199` |
| `backend/app/api/simulations.py` | replay 門傳入 session 自己的版本（**只**在請求明選 inference 時） | `:2049-2070`（`:2069`）；另 `:2244`／`:2605` 未改，是**讀回坐標** |
| `backend/app/api/paper_portfolio.py` | desk 門在省略版本時由來源 derivation，並走**同一個**資格／run 綁定檢查；兩處匯出欄敘述 | `:1054-1086`；`:1737`、`:1741` |
| `backend/tests/test_manual_decision_books_linked_portfolio.py` | `_record_inference` 必填版本（12 呼叫點）；缺欄反控移到 `_record_lineage_row`；兩支語義節點按新契約寫預期 | `:251-297`、`:1993-2041`、`:2555-2682`、`:2684` |
| `backend/tests/test_trained_inference_lineage_chain.py` | **＋2 節點**（392→**617** 行）：replay 兩張表同 origin＋三扇門上下文 | `:471`、`:538` |
| `backend/app/services/daily_estimate_readiness.py` | **新檔 326 行**（唯讀四項前檢） | 14.4 |
| `backend/scripts/daily_estimate_readiness.py` | **新檔 100 行**（操作者 CLI，exit 0／3／2） | 14.4 |
| `backend/tests/test_daily_estimate_prep_readiness.py` | **新檔 768 行／10 節點** | 14.4 |
| `EXECUTION_TABLE_20261117.md` | 本節 §14 | — |

**本輪沒有**：insert／update 任何 consent 列到**真實**帳號、跑 migration、建庫、啟動 PG／服務／瀏覽器／cron、外呼、重釘釘選、clone／apply、任何 Git 寫入、讀或印任何秘密值、展開程序環境。唯一真實寫入的業務資料发生在 **lane 自己的暫存 SQLite 檔**，且已被節點 `:587` 自行揭露：2 筆 `ai_usage` 結算＋2 筆 `usage_records`＋1 筆 `learning_jobs`＋真 artifact 落在暫存 `MODELS_DIR`（14.4 副作用段）。

### 14.8 產品 verdict **分開**（三份證據不可互換）

| 那件事 | 本輪證到什麼 | verdict |
|---|---|---|
| **personal selection**（技能帳號選到哪支模型版本，綁 artifact 位元組） | 14.4 `:310`：報告讀回 `selection.model_version_id`／`artifact_id`，且 `artifact_sha256` ＝ 對**當時盤上位元組**現算的 hash（非循環）；14.1③：明選與 derived 都過同一個資格檢查 | **本輪 PASS（SQLite）**。它**不**含每日同意，也**不**含計費 |
| **每日估計 consent**（第三道同意，本人專屬） | 缺席被點名（`:265`，含「selection 存在」的反向讀回）、經正式門 grant 後讀回 scope（`:310`）、本人之外的人按它会 403（`app/api/personalization.py:328-336`，**本輪未執行**、程式碼路徑已讀） | **報告側 PASS（SQLite）；真實帳號側 ＝ 未接通（只能本人，代理不代按）** |
| **agent-strategy owner approval** | 本輪全樹检索未發現它與每日估計／personal selection 共用任何消費者；它**不**被本輪任何一條鏈當作前提 | **保留為未接通**，**不以 personal 路徑代過** |
| 8 個研究家族（MB-14～19 那套） | 未動研究門檻、未動任何 cutoff | **FAIL 原樣保留**；本輪不改標準換綠燈，也不宣稱任何市場有效性 |

### 14.9 本輪未執行、未驗證（如實）

PG 全鏈（A-Schema／A-Driver／A-併發）、自然 cron（B 四格；**B-額度 BLOCKED 在本人那一次 B-3 動作**）、瀏覽器兩支 spec（C 五格，其中 **C-`--list` 標未確認**）、隔離重建（D）；`grant／revoke` 只能由本人動作；`backend/.env.test` 的值是否為有效憑證**本輪未重驗**（按最壞情況排除）；`git ls-files` 由 225→226 那 **+1 未逐檔溯源**；`order_lifecycle.py` 的 mtime 觀察**未歸因**（14.0 第 3 點）；`POST /human-decisions` 仍不接收 `linked_inference_id`（請求模型無此欄，既有事實）；**8 個研究家族的 FAIL 原樣保留**。

---

## 15. 第 20 輪（2026-11-21，18:0x–18:4x）——第 4 項產品接通＋A／B／C／D 四張卡修正

> 本節是**離線工程與批准材料準備**，不是執行批准。本輪**未**建庫、**未**啟服務／瀏覽器／cron、**未**跑 migration、**未**處理秘密值、**未**外呼、**未**複製快照、**未**改 runner gate、**未**做 Git 寫入。§14 保留原樣（含 `192fd63e…` 那支候選的證據與那筆未歸因的 porcelain 差異）。

### 15.0 本節身份（**18:3x 現測**，不是抄上輪）

| 尺 | 本輪現測值 | 怎麼量的（可重現） |
|---|---|---|
| python 樹 digest | `5bba34446f779364a8d229898ace24e4a0b184128275d7dcf3816f32a8a992d7` | **用 lane 自己那把尺**：`cd backend && find . -name '*.py' -type f -not -path './.venv/*' -not -path './venv/*' -not -path '*/__pycache__/*' -not -path './node_modules/*' \| LC_ALL=C sort \| xargs shasum -a 256 \| shasum -a 256`（逐字同 `scripts/run_tests.sh:373-379`） |
| 髒樹 | porcelain **231** paths，清單 sha256 `704e27c08d31923b19a6e0b06dee900386e6e25fac89ee96f5a930dba17ca8c7` | `git status --porcelain`（只讀） |
| base | `live1-impl` HEAD `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` | `git rev-parse HEAD` |
| 節點清單（卡 A 用） | 16 支 node id 的**全長清單** sha256 `34e1026f9f4d222f95e8a6248baffc60bdb7da71cdb1d4d2e45b032583bfb4e6` | 16 行清單（15.3 逐字貼出）本身做 hash；本輪另以 `--collect-only` 現測 **16／16 都在樹上**（那 5 支檔共 **143** 節點） |
| 節點清單（卡 D 用） | 4 支檔 **56** 節點：`test_trained_inference_lineage_chain.py` **6**＋`test_decision_lineage.py` **26**＋`test_successful_inference.py` **14**＋`test_daily_estimate_prep_readiness.py` **10** | `--collect-only -q` 現測（唯讀，不執行） |

⚠️ **這把 python 尺讀不到本輪的實際改動的一半**：本輪改了 3 支 `.tsx`／`.ts`（前端），`tree_digest()` 只走 `*.py`。所以**候選身分不能只用 `5bba3444…` 一個值**，必須連同逐檔雜湊：

| sha256 | 路徑 | 行 | 本輪動作 |
|---|---|---|---|
| `ef5830c692eda5ed7585599fed4ae618d72de2ada2722e9f7269de245220537c` | `backend/app/ml/agent_strategy_trainer.py` | 790 | 改（推論門） |
| `35a45617ee004d368e7937feeef32d93c37e04f5eb7419268eea715b2d3384c6` | `backend/app/api/agent_strategy.py` | 591 | 改（註解） |
| `0d880029c5f6d3e5115d8653d37bc7a1f0fee8e349c2683fddb150e53813a6ab` | `backend/app/services/model_activation.py` | 735 | **未改**（讀坐標） |
| `6881b63ef6f67e2ce307648a5d0c19fcd701d7a2e40a808169408bae49e1e69d` | `backend/tests/test_agent_strategy_slice.py` | 3184 | 改（2 節點＋1 helper） |
| `4400cdce743683ecd0016573889ae8b6033240f0a4e07916921d57dd76db71ce` | `frontend/src/app/agent-strategy/page.tsx` | 1324 | 改（3 處敘述） |
| `5ffc3097001e109258f68038a6c6ffbe7bd22de760c7adbceb01fe0d74c2de41` | `frontend/src/lib/api.ts` | 1271 | **未改**（讀坐標，證明 client 缺 activation 方法） |
| `a2bc0a903ec661ac9cfc34c201b06f7fc393a8d615e6c30e301d349f521fbe8f` | `backend/tests/test_conflict_doors_at_entry.py` | 723 | 未改（卡 A 引用） |
| `e830dee2c67d8f2eab59bb879714b87a43372f570e42bbeb78c6b5711441af1e` | `backend/tests/test_conflict_recognition.py` | 520 | 未改（卡 A 引用） |
| `e41c23ab4a404ebb3a30db8c1243e74f0e718a3f2c67aa04e81dca011418609a` | `backend/tests/test_manual_decision_books_linked_portfolio.py` | 3032 | 未改 |
| `4316ae54cf9aa310a4eac55e00330276e4dc914c1744de7b5d2c1ad48ee873c2` | `backend/tests/test_daily_estimate_two_workers.py` | 1174 | 未改 |
| `768b7bf659ea21d77e182fbfa5501d450b11ab0245003e1d76f297277e0532af` | `backend/tests/test_daily_model_estimate.py` | 3178 | 未改 |
| `64415ed2a9ec37c02e4f32ae5e4b0b369d85c2e37fed819348ad728708052154` | `backend/app/api/human_decisions.py` | 350 | 未改 |
| `a19ffb910b8e3b8fbaa10595c2358cc79e5ee83788d10f52e69339e6e7dfe803` | `backend/app/api/conflicts.py` | 98 | 未改 |
| `adee9a6986e9b5ef3863da0d5b9ff2c148921337c6e79b67e825b67352ff6643` | `backend/scripts/run_tests.sh` | 538 | 未改（本輪**未**放寬 gate） |
| `e957d71041713ab2a183dab33d1f0d52e608f3711b0e5ec6a006a4d84df32e69` | `backend/test_safety_gate.py` | 277 | 未改 |
| `18567720342ca93d17f322887a12323dd1102f753cba7061fc591bdbe858a9bc` | `backend/tests/daily_estimate_race.py` | 365 | 未改 |
| `11ca6425384009cdf4ba24fc059d78c8d7290b42870942e3faef73c1168cadd6` | `start_candidate_stack.sh` | 684 | 未改（卡 C 現讀） |
| `e319daab7438f034f773914f8a3cd95ae0d868c1348885ce54b80c39e8cb2f69` | `frontend/e2e/manual-decision-lineage.spec.ts` | 810 | 未改 |
| `16c1e9fb55122db7d783d2eb98fbb434600ff7528b5020681b0134431bdaedcb` | `frontend/e2e/daily-estimate-journey.spec.ts` | 673 | 未改 |

**共用主機現況（本輪 18:4x 現測，一次量測＝一次事實，執行時必須重量）**：`lsof -nP -iTCP:5432 -sTCP:LISTEN`＝**0**、54321＝**0**、8101＝**0**、3101＝**0**、`pgrep -fl postgres`＝**0** 支、`${TMPDIR}/alethra-r19-pgdata` **不存在**。候選鏈：`192fd63e…`（R19）→ **`5bba3444…`**（R20）；porcelain 229→231 的 **+2 仍未逐檔溯源**，本輪不把它解釋成自己做的改動（本輪改的 3 支前端檔與 1 支測試檔都在 229 那個集合內，是 M 不是新增）。

### 15.1 第 4 項：agent-strategy approval 的正式消費點——查到的、接上的、仍缺的

**（1）盤查結果（現測全樹 `app/**`，呼叫者逐條）**：`activation_state()` 在接通之前的消費者只有**讀取側**——`app/api/agent_strategy.py:298`（`GET …/training/jobs/{job_id}/activation`）、`app/services/model_activation.py:416`、`:438`（清單／摘要）、`:559`／`:604`（批准與撤回路由把結果鏡像寫回 `job.activation_status`）、`:616`（`refresh_job_activation`，只在讀路由）、`:721`。**執行側（會跑模型的那一側）消費者＝0 支**：`app/ml/agent_strategy_trainer.py::run_inference` 原本不看 approval。⛔ **沒有**任何「paper 啟用／執行」入口讀 approval：`paper_trading_runs` 沒有 job／artifact 欄位可綁，`personal predict` 是另一條產品線（Brief 明令不得硬塞），每日估計 consent 是第三個 purpose（`personal_daily_estimate`，`app/services/daily_estimates.py:61`），**不與** `agent_strategy_screening`（`app/services/model_activation.py:36-50` 的 `PURPOSE`）互換。

**（2）最小接通＝把門放進 `run_inference`，且放在受保護執行之前**。實際 diff（本輪編輯；`192fd63e…` 的內容沒有 git 物件可比，所以給「本輪前 → 本輪後」逐字對照，不冒充 `git diff` 輸出）：

`backend/app/ml/agent_strategy_trainer.py`，插在 `artifact_dataset_mismatch`（`:660-665`）之後、`feature_names` 解析（`:685`）與 `joblib.load` 之前——**這正是「批准缺失或失效時不得先走受保護執行」的字面成立**：

```python
    # The owner's own live approval is the last precondition, and it is checked
    # here — after every identity above has been re-read from the database and
    # from disk, and before a single byte of the model is loaded. …
    activation = activation_state(db, job)                       # :672
    if activation["activation_status"] != ACTIVATED:             # :673
        raise AgentStrategyRefused(
            "agent_model_not_activated",
            "這個產物沒有生效中的本人啟用同意，拒絕執行模型推論。",
            {"job_id": str(job.id),
             "activation_reason_code": activation["reason_code"],
             "approval_id": activation["approval_id"],
             "artifact_checksum": job.artifact_checksum},        # :683
        )
```

回應尾 `:741-744` 現在如實回報是哪一份批准放行的：`"activation_status"`／`"model_activated"`／`"activation_reason_code"`／**新增** `"activation_approval_id": activation["approval_id"]`。檔頭敘述第 3 條同步改為「先重驗 artifact checksum、dataset checksum 與全鏈，再要求**本人對該 artifact 的生效中啟用**，之後才回 take/skip；**不寫任何東西**，未啟用（或已撤回）的產物在**載入模型之前**就被拒」。`app/api/agent_strategy.py` 那段註解改正：被消費的是**這張批准列**，仍未被消費的是**範圍**（`InferenceRequest` 只帶 features；`skill_unit_id IS NULL` **不等於**「全部技能」）。

前端 3 處敘述（原來的寫死文案會直接反駁新契約）：`page.tsx:1208` `value={job.activation_status}`（原為寫死字串）、`:1274-1275` 推論結果列顯示 `${inference.activation_status} · approval ${inference.activation_approval_id ?? '—'}`（原為寫死「not activated」）、`:764` 能力列尾改為 `model activation: ${caps.training.model_activation} (no artifact bound here; each job row below carries its own)`（原為 `(unavailable)`），型別 `InferencePayload` 於 `:299` 補 `activation_approval_id: string | null;`。現測没有任何 e2e／unit spec 斷言被移除的那段文字。

**（3）用真入口驗一通過＋一無副作用拒絕**（正式 lane，SQLite；節點即證據）：
- **無副作用拒絕**：`POST /api/v1/agent-strategy/training/jobs/{job_id}/inference`（body 只有 `{"features": …}`）→ **409**（`app/api/agent_strategy.py:165-169` 把 `AgentStrategyRefused` 一律映射成 409＋`detail.code`）＋`code == "agent_model_not_activated"`＋`details.activation_reason_code == "no_recorded_approval"`＋`details.approval_id is None`＋`details.artifact_checksum == job.artifact_checksum`；**且**`AgentModelActivationApproval` 全表列數 **0**（拒絕沒有自己捏一個同意）、job 列數 **1**、`metadata.json["model_activation"] == "NOT_APPROVED"`、artifact 位元組未動。
- **通過**：`PUT …/training/jobs/{job_id}/activation`，四個欄位由 `GET …/activation` 的 `requirements` **讀回**再送（`tests/test_agent_strategy_slice.py:2135-2156`，所以「批准錯 checksum」會大聲失敗而不是悄悄批准別的東西）→ 200 APPROVED → 同一條 inference 200，斷言 `activation_status=="APPROVED"`、`model_activated is True`、`inference_policy_version=="snapshot-artifact-activation-verified-v2"`、`activation_approval_id == str(該 approval 列 id)`、`lineage_verified`／`artifact_verified` 皆真、`runs_inference_for_customer is False`／`not_a_recommendation`／`not_a_profit_claim` 皆真；評分後**讀回仍是 1 job／1 approval**（評分唯讀）。
- **撤回與重批**：撤回 → 同一條 inference 再拒（同一列 `decision` 由 `approved` 變 `revoked`、`approval_id is None`；如實：撤回後活查找不到列，所以 reason code 是 `no_recorded_approval` 而**不是**另一個「已撤回」代碼）→ 重新批准 → 評分結果 `decision`／`positive_probability`／`artifact_checksum` **完全相同**，但 `activation_approval_id` **不同**（證明門讀的是當下那份批准，不是快取的旗標），job 列仍 1。
- 改寫節點（原名 → 新名）：`test_the_owner_activation_is_what_the_scoring_door_reports` → **`test_the_scoring_door_refuses_until_the_owner_activates`**（`tests/test_agent_strategy_slice.py:2646`）。舊那支**釘的是相反行為**（門沒讀批准也過），本輪把「差距斷言」移除而不是放軟——補缺口之後它已無從存在，這句話要留在紀錄裡。另一支 `test_training_writes_an_artifact_and_inference_reverifies_it`（`:2397`，class `TestFixtureTrainingChain:2351`）擴成上面那串順序。

**（4）仍缺的客戶操作（保持未接通，不硬塞）**：**per-skill 啟用**。`AgentModelActivationApproval.skill_unit_id` 有欄、`model_activation.py:643-656`／`:687-690` 會按 skill 查批准，但推論門以 **job** 為鍵查；紙上執行那一側（`paper_trading_runs`）沒有 job／artifact 身分欄可綁；`frontend/src/lib/api.ts:1136-1154` 只有 `agentStrategy.infer(jobId, features)`（`:1149`），**沒有** activation／approve 的 client 方法 ⇒ 缺的具體客戶操作＝「在 agent-strategy 面板對某支已產出的 artifact 按【啟用】」的 UI＋client（本輪**不**動 `personal predict`、**不**拿每日 consent 替代、**不**把 gate 塞進別處）。

**（5）verdict（分開，不互換）**：推論門消費本人批准＝**PASS（SQLite，15.2 那支 run）**；**PG 側＝NOT RUN**；per-skill 啟用與「按【啟用】的 UI」＝**未接通**；8 個研究家族（MB-14～19）＝**FAIL 原樣保留**，本輪沒動任何門檻或 cutoff。

### 15.2 必要回歸（一次；不重跑無變更批次）

```
command: ./scripts/run_tests.sh sqlite tests/test_agent_strategy_slice.py tests/test_model_activation.py
run dir: ${TMPDIR}/alethra-runs/sqlite-20261121T101821Z-34843（junit.xml／pytest.log／exit-code.txt／collect.txt／identity.txt 五檔齊）
collected 103 = test_agent_strategy_slice.py 48 ＋ test_model_activation.py 55
result: 103 passed / 0 skipped / 0 failed / 0 errors, exit 0, 50.914 s
interpreter: backend/.venv/bin/python（python 3.12.16 | pytest 8.3.3 | sqlalchemy 2.0.36）
target: sqlite ${TMPDIR}/alethra-sqlite-test-34843.db   provider: DATA_PROVIDER=<unset>
tree-digest-before == tree-digest-after == 5bba34446f779364a8d229898ace24e4a0b184128275d7dcf3816f32a8a992d7
porcelain: 231 path(s) sha256 704e27c08d31923b19a6e0b06dee900386e6e25fac89ee96f5a930dba17ca8c7
```

選這支的依據：本輪只動了 `run_inference` 與那 2 支節點，受直接影響的就是這兩檔（slice＝改動所在，model_activation＝門讀的那個 store 自己的契約）。**未**順跑無變更的 lineage／conflict 集（那是 §14 已留的證據）。

### 15.3 卡 A（修正版）——新建隔離 PostgreSQL 16.2 叢集上跑 **16** 支具名節點。三個 verdict：A-Schema／A-Driver／A-併發

* **本輪改了什麼（逐條對應Brief）**：①所有唯讀檢查移到 `mktemp` **之前**；②身分改成**全長內容身份**（digest＋逐檔 sha256＋node-id 清單 digest，不再只比路徑數）；③建目錄／initdb／啟動／**啟動後執行個體讀回**／第一個 SQL 寫入／升級／跑節點**拆成七步**；④不再用 `pg_ctl start` 後的 `$!`，改用 `$PGDATA/postmaster.pid` 第 1 行；⑤每步非零即停；⑥補齊**能辨別 C 門自己的真 driver 正控**；⑦統一 NOT RUN／INVALID／FAIL 語彙。
* **固定目標**：`PGDATA=${TMPDIR:-/tmp}/alethra-r20-pgdata`、`SOCKDIR=${TMPDIR:-/tmp}/alethra-r20-sock`、**只聽 127.0.0.1:5432**、庫 `alethra_owner_test`。為什麼是 5432 與這個庫名：**不是偏好，是 gate 決定的**——`backend/test_safety_gate.py:41-45` 只收 `(localhost|127.0.0.1|::1, 5432)`，`:25-30` 的白名單才有 `alethra_owner_test`（`:29`），`:207-228` 三條都是 HARD FAIL；`FORBIDDEN_DATABASES`（`:33-38`）另外封掉 `ai_investment_lab`／`postgres`／`template0/1`。lane 自己再要求庫名前綴 `alethra_`（`scripts/run_tests.sh:178`）。呼叫點：`scripts/run_tests.sh:190`、`tests/conftest.py:182`／`:223`。
* **A-0 唯讀核對（全部在 `mktemp` 之前；任一不符 ⇒ 停，不建、不 kill、不覆寫）**：

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
PGB=$REPO/.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin   # 本輪現測存在，initdb (PostgreSQL) 16.2
export PGDATA="${TMPDIR:-/tmp}/alethra-r20-pgdata" SOCKDIR="${TMPDIR:-/tmp}/alethra-r20-sock"
"$PGB/initdb" --version                                   # 必須 PostgreSQL 16.2
ls -d "$PGDATA" "$SOCKDIR" 2>&1                           # 兩者都必須 No such file
lsof -nP -iTCP:5432 -sTCP:LISTEN | tail -n +2 | wc -l     # 必須 0
pgrep -fl postgres | cat                                  # 只列；有任何一支 ⇒ 停並回報，本卡不 kill 別人
cd "$REPO" && git rev-parse HEAD                          # 必須 5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a
cd "$REPO/backend" && find . -name '*.py' -type f \
  -not -path './.venv/*' -not -path './venv/*' -not -path '*/__pycache__/*' -not -path './node_modules/*' \
  | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256 | cut -d' ' -f1
cd "$REPO" && git status --porcelain | shasum -a 256      # 704e27c0…
```

  逐字對上：**`5bba34446f779364a8d229898ace24e4a0b184128275d7dcf3816f32a8a992d7`** ／ **`704e27c08d31923b19a6e0b06dee900386e6e25fac89ee96f5a930dba17ca8c7`**（⚠️ 髒樹清單是**路徑＋狀態**的雜湊，不是內容；內容身份是 15.0 那張逐檔表＋下面那段 node-id 雜湊）。
  Node-id 全長身份（不落盤、純記憶體算）：把 15.3 末段那 16 行逐字 `printf '%s\n' … | shasum -a 256` ⇒ 必等於 **`34e1026f9f4d222f95e8a6248baffc60bdb7da71cdb1d4d2e45b032583bfb4e6`**。
  修正掉的舊命令：R19 卡 A-0 那條 `./scripts/run_tests.sh sqlite --print-digest-only` **不存在**（`scripts/run_tests.sh:58-64` 明文：無 `--digest-only`／無 `--tree-digest`／無 `collect` 子命令），它靠 `||` 落回一段 inline python——那是**另一把尺**（含路徑位元組、與 lane 不同）。本輪換成上面那段與 `run_tests.sh:373-379` 逐字同構的 shell，尺與證據同源的才是這條。
* **A-1a 只建目錄**：`mkdir -m 700 -p "$SOCKDIR"`（`$PGDATA` 交給 initdb 自己建，已存在 ⇒ initdb 拒寫，不覆寫）。非零 ⇒ 停。
* **A-1b initdb**：`"$PGB/initdb" -D "$PGDATA" --auth-local=trust --auth-host=scram-sha-256 -E UTF8 --no-sync`。⚠️ **揭露（保留 R19 那句，未鬆口）**：`--auth-local=trust` 讓**同一使用者**的任何本機程序能用 socket 以 superuser 進這台測試叢集；邊界是 socket 落在該使用者專屬 `$TMPDIR`（實測 `/var/folders/tk/…/T/`），**不適用於多使用者機器**。非零 ⇒ 停、留 log、不重試、不刪 `$PGDATA`。
* **A-1c 啟動（這一條不取 `$!`）**：

```bash
"$PGB/pg_ctl" -D "$PGDATA" -w -o "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCKDIR -c max_connections=25 -c shared_buffers=64MB" -l "$RUN/pg.log" start
head -n1 "$PGDATA/postmaster.pid" > "$RUN/pg.pid"       # 本卡管理本卡叢集用的就是這支 pid
cat "$RUN/pg.pid"
```

  ⚠️ 順序如實：`$RUN` 由 `RUN=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r20-pg-XXXXXX")` 建立，而 `mktemp` 屬於**本輪之後**的動作——A-0 全部唯讀檢查完成後才准建。`pg_ctl start` 回传的 `$!` 是 `pg_ctl` 自己，**不是 postmaster**，R19 那樣寫會把停止命令指向一支已經退出的行程。測試與其他背景子行程的 pid **另外記**：lane 自己會印 `identity.txt`（含 run dir），把該 run dir 路徑存進 `$RUN/lane.txt`，**不靠 `pgrep` 認人**。
* **A-1d 啟動後的執行個體讀回（只有全對，才准進 A-2）**：

```bash
PSQL="$PGB/psql"; ADMIN="$(head -n1 "$PGDATA/PG_VERSION" >/dev/null; id -un)"
"$PSQL" -h "$SOCKDIR" -p 5432 -U "$ADMIN" -d postgres -AXtc "SHOW data_directory"       # 必須逐字 == $PGDATA
"$PSQL" -h "$SOCKDIR" -p 5432 -U "$ADMIN" -d postgres -AXtc "SHOW port"                 # 必須 5432
"$PSQL" -h "$SOCKDIR" -p 5432 -U "$ADMIN" -d postgres -AXtc "SHOW listen_addresses"     # 必須 127.0.0.1
"$PSQL" -h "$SOCKDIR" -p 5432 -U "$ADMIN" -d postgres -AXtc "SHOW unix_socket_directories" # 必須 == $SOCKDIR
"$PSQL" -h "$SOCKDIR" -p 5432 -U "$ADMIN" -d postgres -AXtc "SHOW max_connections"      # 必須 25
"$PSQL" -h "$SOCKDIR" -p 5432 -U "$ADMIN" -d postgres -AXtc "SELECT current_database()" # 必須 postgres（這步還不建庫）
"$PSQL" -h "$SOCKDIR" -p 5432 -U "$ADMIN" -d postgres -c "\du"                          # 此時只有 initdb 那一支 superuser
```

  任一項與本卡上面釘的值不符（例如 `data_directory` 指向**別的**叢集、或 port 讀到 5433）⇒ **停**，不得建角色、不得 `CREATE DATABASE`、不得升級。「啟動成功」不等於「啟動的是本卡那支」，這一格就是為了把兩者分開。
* **A-2 第一個 SQL 寫入（建庫＋兩個非特權角色，與啟動分開批准）**：`openssl rand -hex 12` 寫 `$RUN/roles.sql`（`chmod 600`，**不印、不進命令列**）→ `CREATE ROLE alethra_r20_migrator LOGIN PASSWORD …` → `CREATE DATABASE alethra_owner_test OWNER alethra_r20_migrator` → 在該庫 `GRANT USAGE, CREATE ON SCHEMA public TO alethra_r20_migrator` → `CREATE ROLE alethra_r20_app LOGIN PASSWORD …` ＋ 只有 `CONNECT, TEMP`／`USAGE`／`SELECT,INSERT,UPDATE,DELETE`＋sequences `USAGE,SELECT`（`ALTER DEFAULT PRIVILEGES FOR ROLE alethra_r20_migrator`）。閘門依據 `test_safety_gate.py:109-138`（查 `pg_roles.rolsuper,rolcreatedb,rolcreaterole,rolbypassrls`，superuser 或 CREATEDB/CREATEROLE 直接硬拒）⇒ **角色不能有 CREATEDB**，所以建庫這步必須在 gate **外面**用 socket 走。
* **A-3 空庫升全鏈**：`cd "$REPO/backend" && .venv/bin/python -m alembic -c alembic.ini heads` 然後 `… upgrade head`。以 `Alembic ScriptDirectory` **離線現測**（不連庫）。⚠️ **（第 23 輪更正：原本卡釘的 `heads == ['d4e5f6g7h8i9']`／共 **57** 支已過時，本卡不再使用那組值，也不再宣稱「本輪未新增 migration」）** 本輪現測＝`cd backend && .venv/bin/python scripts/check_migration_tail.py --json --with-alembic`（rc **0**、純離線、不連庫）：**`head == "e5f6g7h8i9j0"`、`parsed_heads == ["e5f6g7h8i9j0"]`（單一 head）、`revision_count == module_count == 58`、`expected_tail == parsed_tail == ["b2c3d4e5f6g7", "c3d4e5f6g7h8", "d4e5f6g7h8i9", "e5f6g7h8i9j0"]`、`unreachable_from_head == []`、`status == "OK"`**。第 58 支是 `migrations/versions/e5f6g7h8i9j0_add_agent_paper_executions.py`（新表 `agent_paper_executions`；本輪**未**對任何 PostgreSQL 執行它）。base（`55a8d07a7f40`）**本輪未重測**，不列入本卡判準。heads 多於一／不等於該 id／`DUPLICATE_REVISION`｜`BRANCHED_TREE`｜`MISSING_DEPENDENCY` ⇒ 停。**不 autogen、不 downgrade、不 drop、不跑其他 migration。**
* **A-Schema（verdict 1，唯讀）**：`\dn`、`SELECT current_database(), version();`、查 `pg_indexes` 有 `paper_orders.uq_paper_order_portfolio_idempotency`（partial `WHERE idempotency_key IS NOT NULL`，`app/models/paper_portfolio.py:166-172`）與 `human_decision_events.uq_human_decision_owner_session_key`（`app/models/human_decision.py:80-84`）；查 `information_schema.columns` 有 `decision_records.linked_inference_id` 與 `human_decision_events.linked_inference_id`，且 `human_decision_events.server_strategy_version_id` 為 **NOT NULL**（`app/models/human_decision.py:39`）、型別＝`uuid`。**verdict 只到「索引／型別在不在」**；driver 回什麼 SQLSTATE 屬 A-Driver，**不得寫成「衝突在 PG 已驗」**。
* **A-Schema-2（第 23 輪補：新尾 `e5f6g7h8i9j0` 帶進來的表，讀回判準逐條可核）**——這格只驗「升級後形狀對不對」，**不**驗並發、**不**驗語意：
  * **存在**：`information_schema.tables` 有 `agent_paper_executions`（`migrations/versions/e5f6g7h8i9j0_add_agent_paper_executions.py:80` 之前那段定義的 `TABLE`）。
  * **10 條外鍵按「(欄位, 被參照表)」核對**，不依賴 PG 自動命名：`owner_id→users`、`skill_unit_id→paper_portfolios`、`job_id→agent_strategy_training_jobs`、`snapshot_id→agent_dataset_snapshots`、`strategy_version_id→experiment_versions`、`approval_id→agent_model_activation_approvals`、`candidate_id→agent_candidates`、`decision_record_id→decision_records`、`order_id→paper_orders`、`fill_id→paper_fills`（ migration `:67-78` 的 `REFERENCES`＋`:155-166` 那十條 `ForeignKeyConstraint`）。缺任何一條 ⇒ **FAIL**，不是「略過」。
  * **唯一鍵**：`uq_agent_paper_execution_intent`（migration `:80`、`:167-169`）覆蓋且**只**覆蓋 `(skill_unit_id, job_id, candidate_id)`；用 `information_schema.key_column_usage`＋`table_constraints` 數到 3 欄、順序如定義。這支索引的存在**不等於**它在並發下守得住，那屬 A-併發-2。
  * **3 支索引**：`ix_agent_paper_execution_owner`／`_job`／`_skill`（migration `:81-85`、`:171-174`；model `app/models/agent_paper_execution.py:141-143`）在 `pg_indexes` 逐名可見。
  * **可空性語意**：`decision_record_id`（migration `:117-119`）／`order_id`／`fill_id` `IS_NULLABLE = YES`，其餘身份欄（含 `approval_id`）`= NO`；`information_schema.columns` 讀回要與 `app/models/agent_paper_execution.py` 的欄位定義逐欄一致。
  * **兩格型別要單獨看**：`risk_check_json` = **`jsonb`**（migration `:146`）；`created_at` **NOT NULL 且 `column_default IS NULL`**（migration `:148-150` 明文「No server default」，model `:132` 由 Python 端 `default=utcnow` 填）⇒ 讀到 PG 端有預設值就代表 migration 與 model **不同形**，該格判 FAIL。
  * ⚠️ 這格的證據層級：本輪**只**有「migration 檔存在＋離線 `ScriptDirectory` 尾部契約 15/15（§17.0-1c）＋SQLite `create_all` 造得出同形表」。PG 上的上述任何一條都仍是 **NOT RUN**；`scripts/check_migration_tail.py` 讀不到「表在不在庫裡」，不能拿它替這格背書。
* **A-Driver（verdict 2）——16 支節點**（**逐字 node id**，行號不再當身份用；這 16 行的集合雜湊＝`34e1026f…`）：

```
tests/test_conflict_doors_at_entry.py::test_door_A_answers_only_its_own_index_with_a_conflict
tests/test_conflict_doors_at_entry.py::test_door_A_does_not_rename_its_own_not_null_failure
tests/test_conflict_doors_at_entry.py::test_door_B_answers_a_conflict_on_the_order_index_it_owns
tests/test_conflict_doors_at_entry.py::test_door_B_lets_another_doors_index_through_as_a_500
tests/test_conflict_doors_at_entry.py::test_door_C_does_not_answer_another_doors_order_index_as_its_own
tests/test_conflict_doors_at_entry.py::test_door_D_answers_its_own_index_with_a_conflict
tests/test_conflict_doors_at_entry.py::test_door_D_does_not_rename_the_order_index
tests/test_conflict_doors_at_entry.py::test_the_entries_refuse_an_empty_key_so_the_index_cannot_be_reached_sequentially
tests/test_conflict_recognition.py::test_the_create_door_answers_a_real_collision_with_409
tests/test_conflict_recognition.py::test_the_create_door_does_not_rename_a_null_as_a_duplicate
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_two_requests_on_one_idempotency_key_leave_exactly_one_order
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_stale_account_read_cannot_overwrite_a_committed_booking
tests/test_manual_decision_books_linked_portfolio.py::TestConcurrentLifecycle::test_a_manual_submit_racing_an_advance_stays_consistent
tests/test_daily_estimate_two_workers.py::test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier
tests/test_daily_estimate_two_workers.py::test_two_processes_on_one_window_bill_each_business_key_once
tests/test_daily_model_estimate.py::test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice
```

  **四扇門各自的判準（逐門，不合并）**：門 A／B 的 409 必由 `uq_paper_order_portfolio_idempotency`＋SQLSTATE **23505** 組成；門 C／D 必由 `uq_human_decision_owner_session_key`＋23505；四支反控（`test_door_B_lets_another_doors_index…`／`test_door_C_does_not_answer…`／`test_door_D_does_not_rename…`／`test_door_A_does_not_rename…`）必為 **500／非 409**、回應體不出現 SQL 語句、綁定參數或他人資料；NOT NULL 那支必為 **23502** 而不是 409。
  **⚠️ 本輪補的那一格（C 門自己的真 driver 正控，不再靠反控撐場）**：`test_conflict_recognition.py::test_the_create_door_answers_a_real_collision_with_409`（`app/api/human_decisions.py:212-219`：`IntegrityError → rollback → expected_unique_violation(exc) != HUMAN_DECISION_IDEMPOTENCY → raise`，否則 409 `"Idempotency key conflict (concurrent)"`，與讀檢查那句 `:171` 是**不同字串**，所以節點能分辨兩條路）＋它的反控 `…_does_not_rename_a_null_as_a_duplicate`（未識別的完整性錯誤保持 500／`internal_error`＋`X-Request-ID`，不把「按了一次」改口成「按了兩次」）。
  **判規則（明文，寫死在本卡）**：**C 門的 verdict 只能由 C 自己的節點湊齊**——正控 `test_the_create_door_answers_a_real_collision_with_409` ＋反控 `test_door_C_does_not_answer_another_doors_order_index_as_its_own`。`test_door_B_answers_a_conflict_on_the_order_index_it_owns` 是 **B** 的正控，**不得代替 C**（它走的是另一張表、另一個索引）；同理 D 的兩支不替 C 背書。任何一門只有反控、沒有正控 ⇒ 那門＝**INVALID**（跑過但證據不足），不是 PASS。
  執行（每步非零即停）：

```bash
cd "$REPO/backend"
export ALETHRA_PG_TEST_URL="postgresql+psycopg2://alethra_r20_migrator:<從 $RUN/roles.sql 讀，不印>@127.0.0.1:5432/alethra_owner_test"
export TEST_MODE=true DATA_PROVIDER=demo
./scripts/run_tests.sh pg <上面 16 行逐字作為引數>
echo "A lane rc=$?"
```

  rc 語彙（本輪讀源重釘）：**2** ＝ `ALETHRA_PG_TEST_URL` 空（`scripts/run_tests.sh:170-173`，是**本卡命令沒給對**，不是測試結果）；**3** ＝ preflight 被 gate 拒（`:184-185`，讀 `test_safety_gate.py` 那三條 HARD FAIL 的訊息）；其他非零 ⇒ 依斷言判 FAIL 或 INVALID。
* **A-併發（verdict 3）**——上面 16 行裡的 **6** 支真併發節點（`TestConcurrentLifecycle` 3 支＋`test_daily_estimate_two_workers` 2 支＋`test_daily_model_estimate` 1 支）。先 `export ALETHRA_RACE_CAPTURE_JSON="$RUN/race-capture.json"`（`_write_capture()` 不建父目錄 ⇒ `$RUN` 必須已存在）。計時契約（本輪讀源重釘，行號改掉）：barrier／程序 timeout `_TIMEOUT_S = 60` 在 **`tests/test_daily_estimate_two_workers.py:170`**（不是 `daily_estimate_race.py`，R19 那樣引用會找不到），兩個 worker 自己的 session 設 `lock_timeout '30s'`／`statement_timeout '120s'`／`idle_in_transaction_session_timeout '90s'` 在同檔 **`:241-243`**；lane 另有一組更短的預設 `15s/120s/60s`（`tests/conftest.py:243-245`）——兩組不同，別混記。判準由 `tests/daily_estimate_race.py:68-84` 那 **12 條 CRITERIA**（`assess_race()` 在 `:131`）逐條過：每 `(owner, skill, instrument, window)` 恰 1 列、`metadata["via"]=="daily_service"`、預留與帳单一對一、非終態預留＝0、落敗那支具名 `already_estimated_this_window`（`:91` `LEDGER_REASON`）、兩個 job 摘要的 `billed_in_window` **不相加**、且**拒絕原因出自帳本而不是額度**（`:80-83`；`QUOTA_REASONS` `:89` 任一出現 ⇒ 該次**沒有**證明席位守得住）。**額度前提**：這個目標的 `ai_usage_limit` 維持預設 **10**（`app/auth/entitlements.py:70`），**不得**套卡 B 的 1，否則 429 會把真缺陷蓋成限流。
* **A-併發-2（第 23 輪補：`agent_paper_executions` 帶來的兩條並發邊界——先講責任在哪一层，再把缺失驗證列成待批 PG 節點）**
  * **邊界一：同一意圖的兩筆並發提交**（同 `(skill_unit_id, job_id, candidate_id)`）。責任分兩层、**由不同程式碼負責**：
    1. 順序快速路徑＝`app/services/agent_paper_execution.py:404-410` 的**無鎖** SELECT（`.first()`），命中 ⇒ 回原列 `duplicate: True`（`:411-417`）。它只處理「前一筆已 commit」的重送；兩筆同時在飛、彼此讀不到時，兩邊都會繼續往下。
    2. 真正的守門＝**資料庫唯一約束**：`_commit_ledger` `:661-680` 承 `IntegrityError` → `db.rollback()` → 按業務鍵重讀 ⇒ 回 `duplicate: True` **且** `concurrent_insert_collapsed: True`（`:679`）；重讀為空 ⇒ **原樣 raise**（`:673-674`），不把沒認出的 23505 改口成重複。
    ⇒ **判準**：PG 上兩筆真並發 ⇒ 帳本**恰 1 列**、輸的那邊回應帶 `concurrent_insert_collapsed`。本輪量到的暴露程度：全庫 grep 該鍵只有**兩個寫入點**（`agent_paper_execution.py:679`；`app/ml/agent_strategy_trainer.py:521` 是**另一張表**（training job 去重）的同名旗標，不混用），**測試節點聲稱數＝0**。⚠️ §17.1(e) 那格「同一意圖再送 ⇒ `duplicate: True`」走的是第 1 层，**不得**當作第 2 层的證據。
  * **邊界二：撤回批准與提交執行的競態**。實際責任：
    1. 批准**寫入端**鎖 job 列——`app/services/model_activation.py:492-496`（`record_activation_approval`，`:463`）用 `.with_for_update()`，註解自述為了「one active approval per job」；**撤回端**鎖 approval 列——`:573-580`（`revoke_activation_approval`，`:570`）。
    2. 但紙上路徑**讀**批准走 `live_approval`（`model_activation.py:87-96`）：**無 FOR UPDATE**，`order_by(approved_at.desc()).first()`。
    3. 服務端只有「兩次讀之間的覆核」：閘門讀到的 approval id 必須與載入器 `run_inference`（`app/ml/agent_strategy_trainer.py:609`）回傳的 `activation_approval_id`（同一函式 `:744` 取自 `activation["approval_id"]`）一致，否則具名 `paper_execution_approval_changed_midflight`（`agent_paper_execution.py:424-433`）；載入所用 artifact 雜湊亦須等於批准綁定值（`:434-440`）。那是**縮窄視窗**，不是隔離層級。
    ⇒ **如實寫出未证的殘留窗口**：撤回（該列 `decision='revoked'`）若在 `:424` 覆核**之後**、`:663 db.commit()` **之前**落地，會留下一筆 `approval_id` 指向已撤回列的帳本——FK 不擋（被參照列仍存在）、唯一鍵也不擋（該意圖首次出現）。**不得**把「有 midflight 覆核」寫成「撤回後不可能再記入」。
    ⇒ **待批 PG 節點（兩支，需真並發＋獨立讀回；本輪只寫入判準，未執行）**：
      ①**兩筆並發同意圖**：barrier 會合後同時 `POST …/paper-executions`，斷言 `count(*) == 1`、勝者 `duplicate False`、敗者 `duplicate True` ＋ `concurrent_insert_collapsed True`，且 `paper_orders`／`paper_fills`／`decision_records` 各**只多一筆**（意圖 collapsed 不得順帶多記一張委託）。
      ②**撤回與提交競態**：一支提交執行、一支撤回批准，在 `:424` 與 `:663` 之間會合；斷言**只有兩種可接受結果**——(a) 執行具名被拒（`paper_execution_approval_scope_not_granted`／`agent_activation_approval_not_found`／`approval_changed_midflight`）且帳本 0 新增，或 (b) 帳本 1 列且**讀回**該 `approval_id` 的 `decision`／`revoked_at` 可辨識地顯示「執行當時綁定的那筆已被撤回」，並將該列**標記為待本人覆核**而不是默默算成合法執行。兩者皆非（例如寫入列而 approval 已 revoked 卻無任何可讀痕跡）⇒ **FAIL**，屬產品缺口，不是測試沒寫好。
    兩支都必須掛 `postgresql_only`（SQLite 沒有真行鎖，見 `tests/test_risk_policy_across_lanes…` 那格在第 22 輪就是出於同一理由被 skip），其 verdict **不從** 17.1(e) 的 SQLite 讀回繼承。
  * **不可互替（本輪點名）**：`uq_agent_paper_execution_intent` 在 `pg_indexes` 可見＝**A-Schema-2**（形狀）；SQLite 節點綠＝**§17.1**（語意）；`check_migration_tail.py` 15/15＝**檔案圖**，它連不到庫，**不能**替上面任何一條背書。三者各自獨立，缺一格就不能宣稱「紙上路徑在 PG 已驗」。
* **語彙（本輪統一，三個不互換）**：**NOT RUN**＝真併發根本沒啟動（barrier 沒湊齊、程序沒起來、rc 2/3 是本卡自己沒給對環境就先修命令再說）；**INVALID**＝啟動了但證據不足（capture 缺欄、`proven=false`、某門只有反控）；**FAIL**＝斷言實際不成立（例：C 門對自己的索引衝突不回 409）。**三者都不能寫成「PG PASS」**，任何一門通過都不替另外兩門背書。
* **停止線**：非零 exit｜head 不符｜四旗標不為全 `f`｜`current_database() != alethra_owner_test`｜A-1d 任一項不符｜反控那幾支出現 409｜以跳過取代執行｜任一斷言失敗 ⇒ 停、留 `$RUN/pg.log` 與 lane run dir、**原樣回報、不重跑、不改設定碰運氣**。
* **停止命令**：`kill -TERM "$(cat "$RUN/pg.pid")"` → `"$PGB/pg_ctl" -D "$PGDATA" -m fast stop` → `pg_isready -h 127.0.0.1 -p 5432` 必非零 → `lsof -nP -iTCP:5432 -sTCP:LISTEN | tail -n +2 | wc -l` 必為 **0**；**不刪 `$PGDATA`**；**不得用已停端點的 HTTP 回應證明停止**；**不從 `pgrep` 挑別人的行程**。
* **副作用清單**：`$SOCKDIR`＋`$PGDATA`（本卡唯一落地處）、`$RUN/{pg.log,pg.pid,roles.sql(600),lane.txt,race-capture.json}`、**58** 支 migration 的 DDL（第 23 輪更正：原列 57 支已過時；第 58 支 `e5f6g7h8i9j0` 會在該庫 `CREATE TABLE agent_paper_executions`，是**新副作用**，不是唯讀）、測試自己的資料列。**不寫 `$REPO`、不寫 `.venv`、不 push、不 commit、不碰別人的叢集。**
* **狀態：NOT RUN**（本輪未啟動 PG；15.0 那個「5432 listener＝0」是本輪量一次的事實，執行時必須重量）。

### 15.4 卡 B（修正版）——自然排程在自己一支叢集上跑一次；**先修前置閉環**

* **本輪改了什麼**：①資料來源改成**單一宣告**，不再兩條路並存；②固定種子的**每一條直寫**列成獨立批准面並寫明「不是正式入口」；③**兩階段**（scheduler=false 準備 → 同一支庫讀回 readiness 通過 → 才准 scheduler=true 自然觀察）；④**移除 quota=1 反控**；⑤kill switch 只按本入口實際消費的那個 scope 判；⑥憑證走已核對的本機機制，代理不讀值；⑦**不再催本人執行舊 B-3**。
* **（a）205 筆決策與兩個標的 K 線的**唯一來源**：**
  * **不採用** `backend/scripts/seed_e2e_personal_learning.py`。三條理由（現讀）：它只種**一支**標的（`:82` `INSTRUMENT = "US:AAPL"`），湊不齊「兩個標的」；它在 `:381-383` 只要 `E2E_GRANT_CONSENT=1` 就**代寫訓練同意**（`grant_consent(session, user.id, [version.id])`，`app/services/personalization.py:570-588` 會把該 owner 現存的 active 訓練同意**逐條蓋 `revoked_at`** 再寫新的一筆），而 `start_candidate_stack.sh:665` 正是這樣按下去的——**代理不得用這條路取得任何同意**；它按 owner 刪除 `HumanDecisionEvent`／`SimulationSession`／`PersonalizationDatasetSnapshot`／`PersonalizationConsent`／`QuotaReservation`／`QuotaCounter`／`TrainingJob`／`PersonalModelSelection`／`DecisionLabel`（`:137-166`），範圍由 `E2E_EMAIL` 經 `fixture_uuid(E2E_MARKER, EMAIL, name)`（`:90-93`）導出——**若把 `E2E_EMAIL` 填成本人帳號的 email，它刪的就是本人帳號的真實列**。
  * **採用**：與 `tests/test_daily_estimate_prep_readiness.py` **同一條九步正式入口鏈**（該檔 `:1-41` 自述「只走正式入口」，SQLite 已 10/10）：`POST /api/v1/experiments/` → `POST …/versions/{vid}/lock` →（205 筆決策，見下）→ `PUT /api/v1/ml/personalization/consent`（**訓練**同意，本人）→ `POST /api/v1/ml/personal/snapshots` → `POST /api/v1/ml/personal/training-jobs`（202，worker 真擬合、真寫 artifact）→ `GET /api/v1/ml/personal/models?strategy_version_id=vid` → `POST /api/v1/paper-portfolio` → `POST /api/v1/ml/personal/models/{model_version_id}/select`。
  * **直寫 1（固定種子 bars）＝獨立批准面，不是正式入口**：寫入者 `tests/test_personal_strategy_learning.py:58-74 _seed_bars`；**表** `market_data_bars`（＋`market_data_metadata` 每標的 1 列，`tests/test_daily_estimate_prep_readiness.py:101-112`）；**目標**＝本卡那支隔離庫；**上限**＝`len(BARS)=140` × 2 標的＝**280 列**＋**2 列** metadata；**確定性**：`BARS` 為 2024-01-01 起 140 個工作日（本輪算得最後一根 **2024-07-12**），價格 `_price(i)` 為純函數 ⇒ 同一支種子每次位元組相同。
  * **直寫 2（固定種子 decisions）＝獨立批准面，不是正式入口**：寫入者 `tests/test_personal_strategy_learning.py:194-246 _seed_confirmed_decisions`；**表** `human_decision_events` **205 列**（`DECISION_DAYS=41 × DECISIONS_PER_DAY=5`，`:34-37`）＋ `decision_labels` **205 列**（`record_label(state="confirmed")`）＋ `simulation_sessions` **41 列**（每日一支 `_new_session`）；**上限＝205／205／41，不得超過**；`execution_status=record_only`、`actor_type="human"`、`server_instrument_id` 全為 `US:AAPL`（**205 筆不分散到 MSFT**，第二標的靠的是 bars＋宣告範圍＋同一支選定版本）。⚠️ **不可重現之處如實寫**：`idempotency_key=uuid.uuid4().hex`（`:215`）是隨機 ⇒ **可重現的是筆數與欄位語意，不是列身分**；要逐列可重現必須先改寫 helper，那是另一件事、不在本卡。
  * **如果本人寧可讓 205 筆走真門**：那是 `POST /api/v1/simulations/{session_id}/manual-decision` 205 次（會连带寫 orders／audit），屬**第三個批准面**，本卡不預設、不混算。
* **（b）階段 1——準備服務（`ENABLE_LEARNING_SCHEDULER=false`）**：起在**本卡自己的**叢集與**本卡指定的隔離 artifact 根**（`MODELS_DIR=$RUN/models`；⚠️ 現讀 `start_candidate_stack.sh:596` 的預設 `ARTIFACT_DIR` 落在**來源樹內** `backend/app/ml/artifacts` ⇒ 走那條路必須用 `CANDIDATE_ARTIFACT_DIR` 覆寫，否則違反「工件只能寫本卡指定的隔離目錄」）。啟動**前**要先把四個值唸出來對照：port（本卡 `5433`，與卡 A 的 5432 分開）、實際 config 來源（`backend/.env` 是否被 `app/config.py` 讀到、以及 `env -i` 後顯式 export 了哪些）、DB role、artifact 根。九步鏈跑完，訓練與選定都在**這一支**库里落地。
* **（c）讀回 readiness 才准進階段 2**：`cd backend && .venv/bin/python scripts/daily_estimate_readiness.py`（唯讀，exit **0**＝就緒／**3**＝缺件／**2**＝命令自己沒給對環境）。**同一支**隔離庫未過 readiness ⇒ **停**，準備服務不關、自然觀察不起、**不補造新窗口、不伸手動 run**。
* **（d）階段 2——自然觀察（`ENABLE_LEARNING_SCHEDULER=true` ＋ `LEARNING_SCHEDULER_JOBS=daily_model_estimate`）**：啟動日誌必須**只**點名這一支 lane（預設為空 ⇒ 取 `app/config.py:55-59` 那三支，其中**沒有** estimate，所以要明釘）。05:00Z 後 90 秒內無 `learning_jobs` 列 ⇒ 停並存 log。
* **（e）判定「兩個標的皆能結算」**：**tick 前**讀 `assess_readiness`（唯讀）確認兩個 `instrument_ids` 都在宣告範圍內、窗口**未結算**（`usage_records` 在 `metadata_json["via"]=="daily_service"` 且該 `(owner, skill, instrument, window)` 上為 0）、且額度 `limit ≥ 2`；**tick 後**逐業務鍵验兩個**各**新增 1 筆結算（帳本判準，不是回應體）、`quota_reservations` 非終態殘留＝**0**、每筆自帶 `model_version_id`＋`artifact_sha256`＋`as_of_date`。**本輪移除 quota=1 反控**：它證的是額度路徑，不是結算，而且會把第二個標的遮成 429；既有的 SQLite 額度拒絕（`tests/test_daily_model_estimate.py:1734 test_an_exhausted_quota_refuses_before_spending_anything`）**只保留為 SQLite 證據**，不搬到 PG 格子裡。
* **（f）同意仍然分開、仍然只能本人**：訓練同意（purpose `personalization_training`）與每日估計同意（`personal_daily_estimate`，`app/services/daily_estimates.py:61`）是兩個用途，證據不得互換；每日估計同意（以及 agent-strategy 的啟用批准）都由本人在正式入口按（`frontend/src/components/training/DailyEstimatePanel.tsx:676`／`:695`；`PUT /api/v1/agent-strategy/training/jobs/{job_id}/activation`），**代理都不代本人操作**，本卡文字也不構成同意。
* **（g）憑證機制（如實）**：以 `CREDENTIALS_FILE`（mode **600**，`start_candidate_stack.sh:145-159` 的 `require_credentials` 會驗 600、驗七個鍵齊、驗 email 對得上 profile，再 `set -a; . "$file"` 匯入）＋ `CANDIDATE_DB_PASSWORD` 傳入，**不印、不放命令列參數、不由代理讀 owner 密碼或 token**。⚠️ 現讀 launcher 在 `:255`／`:597` 有 `DB_PASSWORD="${CANDIDATE_DB_PASSWORD:-testpass}"` 的**寫死 fallback** ⇒ 本卡必須顯式提供，否則用的就是那個公開在檔裡的值；⚠️ 同一行把它拼進 `DB_URL`（`:181`）是行程環境與連線字串，**不dump、不 echo**（`ps eww -p PID` 會洩漏環境，本卡不用它當工具）。
* **（h）kill switch（只按本入口實際消費的判）**：每日估計這條路看的是 `KillSwitchScope.inference`（`app/services/daily_estimates.py:995`、`:1259`，歸因欄 `:949`），而 `is_kill_switch_active()` 另外把 **global** 也算進同一個布林（`app/services/kill_switch.py:23-29`）。因此判据只有這兩支：**global** 與 **inference**；`training`／`execution` **不**算本入口的開關（把它們當 inference 開關是本轮要修掉的讀法）。觀察要自成一格：**自己的完整時間窗**＋前後各一次帳本讀取（不與額度格共用同一組讀數）。
* **（i）停用格與外連格（保留上輪判準，未鬆口）**：停用＝`ENABLE_LEARNING_SCHEDULER=false` 起一次，**必須另外數** `learning_jobs` 與 `usage_records(via=="daily_service")` 新增列＝**0**，不能只用「tick 沒跑」的敘述。外連＝主證據 `lsof -nP -p <本卡 pid> -a -i` 中對端非 `127.0.0.1`／`::1` 的列數＝0；⚠️ **一句快照只能寫「採樣未觀察到外連」**，要收據得按週期取樣並記錄每次時刻；`env -i`＋`DATA_PROVIDER=demo AI_PROVIDER=mock` 是**機制**不是保證，log grep 只當旁證。
* **（j）停止命令**：只對本卡記下的 pid 發 `TERM`（`$RUN/pids`），再 `pg_ctl -D "$PGDATA" -m fast stop` 停本卡自己的叢集；**不刪 `$PGDATA`**、**不撤同意**（撤不撤由本人決定）。
* **狀態**：**B-準備／B-額度＝BLOCKED**——缺的不是腳本，是（1）那兩個直寫批准面的核可、（2）本人在那支已批准啟動的棧上按自己的同意與正式操作；在那之前代理不代按、不插列。**B-停用／B-外連＝材料齊、NOT RUN。**
* **狀態（第 23 輪補：六段分離，任何一項不替代另一項）**——本輪把種子實物真正推過了一次正式門，結果逐段如下：
  1. **種子工具**（`backend/scripts/card_b_seed.py`＋`tests/test_card_b_seed_prep.py`）＝**已交、SQLite 隔離檔內綠**（本輪現測 **21** 節點／exit **0**，run `sqlite-20261122T114346Z-82198`，候選 `5bde69ef…`）。它證的是「有上限、預設不執行、失配即停」，**不**證「資料可用於訓練」。
  2. **正式訓練與 artifact**＝**BLOCKED**。種子的 205 筆 `human_decision_events` 一律 `actor_type="unknown"`＋`MARKER`（`nature: "synthetic_prep_seed"`、`owner_confirmed: False`），两道资格门只认人证出的血统：本輪量到它們**只**因 `unverified_provenance` 被排除（排除原因與計數入帳於 `tests/test_card_b_seed_prep.py` 的門那一組節點），`POST /api/v1/ml/personal/snapshots` 因此**具名拒絕且不寫任何列**。⇒「**B-正式訓練準備**」維持 BLOCKED，**未**改標 human、**未**直寫同意、**未**繞過訓練門。
  3. **本人每日同意**＝**未給**（本輪 consent 寫入 0 列；`PUT /ml/personalization/consent` 由本人按，代理不代按）。**注意**：即使本人按下**訓練**同意，也**不會**把那 205 筆升格為真人確認——第 2 段的排除出自列自己的出身欄，不是出自缺同意。
  4. **兩鍵可結算 readiness**＝**NOT RUN**（要一支已批准啟動的棧與一支指定的庫；本輪未建、未起）。
  5. **自然 tick**＝**BLOCKED**（§16.3(d) 三缺口不變）。
  6. **可審的替代輸入**（若本人不要合成列）＝需要**真人血統**的決策列，身份＝本人帳號的 `HumanDecisionEvent`（`actor_type="human"`、經 `POST /api/v1/simulations/{session_id}/manual-decision` 產生）、來源＝本人自己在正式入口按過的記錄、批准＝本卡 15.4(a) 那「第三個批准面」205 次真提交。**本卡不代為選擇，也不以合成列冒充。**

### 15.5 卡 C（修正版）——dev 功能驗收棧；**五格收斂成四格，副作用逐 mode 分離**（第 23 輪：另立第五格 **C-agent-journey**，見 verdict 清單末條）

* **本輪最大的一筆更正**：R19 那句「起棧本身就是 DDL」**過度概括**。現讀 `start_candidate_stack.sh`（sha `11ca6425…`，684 行）**有四種 mode，副作用完全不同**：

| mode | 讀源 | 建庫 | DDL | **drop** | seed | 起行程 | 寫檔 |
|---|---|---|---|---|---|---|---|
| `--plan` | `:86`、`:547-554`、`readonly_preflight` `:296-313` | 否 | 否 | 否 | 否 | 否 | 只在 `$TMPDIR` 建一支 probe 暫存檔並在結束時 `rm -f`（`:300`、`:306`）；自述「read-only; nothing will be started or written into the repository」（`:550`）。⚠️ 它先 `require_profile`（`:548`）⇒ **沒有既有 profile 就無法用 `--plan` 預演第一次 `--new-candidate`** |
| （預設）resume | `:556-568` | 否 | **否**（`:560` 明文 no migration／no seed） | 否 | 否 | 是（backend＋worker＋frontend） | `$STATE_DIR/runtime-identity.env`（`:456-470` 由 `:563` 寫）、`$LOG_DIR/{backend,worker,frontend}.pid`（`:499-502`）、log |
| `--new-candidate` | `:591-660` | **是**（`:626`） | **是**（`:628` `upgrade head`） | 否 | **是**（`:587`→`:663-671`） | 是 | credentials 檔（`:623`，`chmod 600`，含三個帳號密碼與一支 JWT secret）、profile（`:636-656`）、`$LOG_DIR/migrate.log`、`$LOG_DIR/seed.log` |
| `--reset` | `:570-589` | 是（先 **`drop database if exists`** `:583`，並 `pg_terminate_backend` `:582`） | 是（`:585`） | **是** | 是 | 是 | 同上 |

  ⇒ 所以卡 C 的批准要拆成**可單獨否決的動作**：**C-plan**（唯讀，僅在 profile 已存在時可用）／**C-new**（第一個副作用是 `create database`，連帶寫 `$ROOT/.local/candidate/…`）／**C-resume**（無 DDL、無 seed，但會起三支行程並寫狀態檔）／**C-reset ＝ 本卡一率不建議批准**（它 drop 一整支庫）。
* **隔离 DB 與 schema 身分怎麼釘（本輪逐值現測）**：`PROFILE=${CANDIDATE_PROFILE:-$ROOT/.local/candidate/candidate-profile.env}`（`:49`；`.local/` 已被 `.gitignore:63` 排除 ⇒ 仍是**寫進 checkout 目錄**的檔，只是不進 Git）；`DB_NAME` 必須以 `alethra_personal_e2e_candidate` 開頭（`:137-139`，腳本自己強制）；預設值（`:592-604`）：`DB_HOST=127.0.0.1`、**`DB_PORT=54321`**（R19 未列）、**`DB_USER=postgres`（superuser）**（R19 未列 ⇒ 這條路**不經** `test_safety_gate.py`，因為那是 test lane 的閘門；卡 A 的 5432／白名單庫不適用在此）、`DB_PASSWORD` 有 `testpass` fallback（見卡 B（g））、**`ARTIFACT_DIR=$ROOT/backend/app/ml/artifacts`（寫進來源樹！）**、`LOG_DIR=$ROOT/.local/candidate/<id>`、backend **8101**、frontend **3101**、三支 fixture 帳號 `candidate.{owner,thin,empty}@example.com`；`EXPECTED_SCHEMA_HEAD` 由 `new` 從 `alembic current` 讀入並寫進 profile（`:631`、`:651`），`resume`／`reset` 用它對比（`:576` 不符即 fail）。⇒ 本卡如要守「工件只能寫本卡指定的隔離目錄」，必須 `CANDIDATE_ARTIFACT_DIR=$RUN/artifacts`＋`CANDIDATE_DB_PASSWORD`＋`CANDIDATE_ENV_FILE` 一起覆寫。
* **（本輪實測更正）CORS**：R19 說 8101／3101 的 origin 沒接線、要靠 `config.py:79` 的預設——**不對**：launcher 自己 `export CORS_ORIGINS="http://localhost:$FRONTEND_PORT,http://127.0.0.1:$FRONTEND_PORT"`（`:185`），`localhost` 與 `127.0.0.1` 兩種寫法**都涵蓋**。那條預設值只適用於**手工起 uvicorn** 的路線（本卡不建議，因為那样就绕過了上表那四個 mode 的副作用紀錄）。預檢斷言照留：回應必須含 `access-control-allow-origin: http://127.0.0.1:3101`。
* **移除 `--list`**：本卡**不再有任何 Playwright 探測步驟**（含 `--list`）。R19 那格「C-`--list` 探測」標的是「未確認」，它既不是 verdict 也買不到證據，現在用 `--plan`（腳本自述唯讀）＋**起棧前後各取一次 `lsof -nP -iTCP:3101 -sTCP:LISTEN`／`-iTCP:8101`** 取代；這雨個數在 R19 是 0（本輪 18:4x 現測 8101／3101 Listener 皆 0），執行時要重量。
* **verdict 清單（互不取代，結果一律標 dev）**：⚠️ 第 20 輪把五格收斂成四格；**第 23 輪另立第五格 `C-agent-journey`**（下面最後一條）。它**不在**原本那兩支 spec 裡，因此**卡 C 舊的兩支 spec 結果不得替它背書**，它也不替那兩支背書。
  * **C-DDL（＝C-new 的那一次 `create database`＋`upgrade head`）**：**必須單獨批准並指名它寫哪支庫**（profile 裡的 `DB_NAME`）。核對：跑前 `cd backend && .venv/bin/python -m alembic heads`（⚠️ 第 23 輪更正：原本卡現測的 **`d4e5f6g7h8i9`** 已過時、從本卡**移除**；本輪現測 head＝**`e5f6g7h8i9j0`**、共 **58** 支，見 15.3 A-3。原句「本輪**未新增** migration」也不再成立——第 58 支是 `agent_paper_executions`）、起棧後讀 `/health/ready` 的 `schema-head` 與 profile 的 `EXPECTED_SCHEMA_HEAD` 對照；不符 ⇒ 停。⚠️ **對 C 的新副作用**：批 C-new 等於在**那支 dev 庫**上新建 `agent_paper_executions`（launcher 的 `upgrade head`，`start_candidate_stack.sh:628`）；這張表**從未在任何 PG 上建過**，所以 C-DDL 也是它的第一次真實 DDL——該事實不從卡 A 繼承（A 未執行），也不從 §17.1 的 SQLite `create_all` 繼承。把 C 指向 A 或 B 的庫＝在那支庫上多跑一次 DDL，**該副作用不從別張卡繼承**。
  * **C-自動化**：`cd frontend && E2E_FRONTEND_BASE=http://127.0.0.1:3101 E2E_BACKEND_BASE=http://127.0.0.1:8101 E2E_BROWSER_CHANNEL=chrome E2E_CRED_FILE=<本機憑證檔> node_modules/.bin/playwright test e2e/manual-decision-lineage.spec.ts e2e/daily-estimate-journey.spec.ts`——**只這兩支**，不掃全 e2e。只報 **collected／passed／skipped／failed** 四個數，不拿 collected 當 passed。
  * **C-本人同意／撤回（只能本人）**：`data-testid="daily-estimate-acknowledge"`（`DailyEstimatePanel.tsx:676`）與 `daily-estimate-revoke-all`（`:695`）。兩支 spec **都不按**它們：`daily-estimate-journey.spec.ts:63-68` 明文「It does not grant or revoke the daily-estimate consent … Both buttons are asserted to exist and to be labelled, and neither is ever pressed」，`:530-532` 只 `toBeVisible()`。按下之後的**持久證據**是 `GET …/daily-estimate/consent` 裡的 scope 與 `granted_at`（或卡 B 那份報告），不是畫面。**代理不代按。**
  * **C-可能扣額**：只有 `E2E_DAILY_ESTIMATE_SKILL` 有設才會走進那幾格（`daily-estimate-journey.spec.ts:108` `?? ''`、`:536`／`:627` `test.skip(!PAYING_SKILL, WRITE_SKIP)`）。**無扣額批准 ⇒ 明確 `unset E2E_DAILY_ESTIMATE_SKILL`**（不只是「留空」），並把那句跳過原因**逐字入帳**（前置缺件 ≠ 通過）。另兩格前置：`E2E_CRED_FILE`（`frontend/e2e/runtime.ts:34`；缺 ⇒ `TIER_B_SKIP` `:26-30`）、`E2E_LINEAGE_ASSET`（預設 `DEMO-ALPHA`，`manual-decision-lineage.spec.ts:111`）。**代理不代填。**
  * **C-agent-journey（第 23 輪另立，獨立批准面：agent 批准＋紙上執行的瀏覽器旅程）**：
    * **實物**：`frontend/e2e/agent-approval-paper-journey.spec.ts`（本輪現測 **818 行**／sha256 `65e39b34…`）。10 支節點分兩組：**READ group**（describe 在 `:356`）6 支——面板印的是 `GET …/activation` 回來的紀錄、綁定表单填著時不發出任何 PUT、「綁定此技能帳號」但沒選帳號當場拒、執行控件與文字與門三處判讀一致、ledger 表是過濾後的讀回且用 route 自己的兩個數字、scope 的兩種讀法對每種紀錄形狀都同結論。**WRITE group**（describe 在 `:598`）4 支——按下一次後螢幕印出 ledger 讀回的那一列、同一意圖第二次按下不多列、批准未覆蓋的帳號根本發不出請求、重新讀回 ledger 不重送意圖。
    * **命令**（只有在 C-new／C-resume 已獲准且棧活著時才有意義；**本輪未執行**）：`cd frontend && E2E_FRONTEND_BASE=http://127.0.0.1:3101 E2E_BACKEND_BASE=http://127.0.0.1:8101 E2E_BROWSER_CHANNEL=chrome E2E_CRED_FILE=<本機憑證檔> [E2E_ACTIVATION_JOB=<job id>] [E2E_PAPER_EXECUTION_SKILL=<技能帳號 id>] node_modules/.bin/playwright test e2e/agent-approval-paper-journey.spec.ts`。
    * **兩個只能由本人給的引數**：`E2E_PAPER_EXECUTION_SKILL`＝**哪一本紙上書可以被告知一列**（`:110`）、`E2E_ACTIVATION_JOB`＝**對哪一個產物按下執行**（`:111`）。缺任一 ⇒ WRITE group 整組以具名理由跳過（`:112-116` `WRITE_SKIP`），**跳過不是通過**。**代理不代填。**
    * ⚠️ **本輪修掉的一格真缺陷（不修它就是佔位符，不是閘門）**：WRITE group 的 describe 級跳過原本寫成 `test.skip(true, WRITE_SKIP)`——**無條件**，代表本人即使設了那兩個變數，那 4 支也永遠不會跑。現測 `:602` 已是 `test.skip(WRITING_SKILL === '' || TARGET_JOB === '', WRITE_SKIP)`，與同目錄 `daily-estimate-journey.spec.ts:536` 既有的 `test.skip(!PAYING_SKILL, WRITE_SKIP)` 同形。**沒有放寬任何閘門**：兩變數未設的預設仍是整組跳過。
    * **本輪也量到該 spec 的另一道本人專屬前置**：`:605-612`——生效同意若不是綁定那個帳號的 scoped 同意，WRITE group 自行跳過並寫明「要先由本人在螢幕前提交一次綁定這個帳號的同意——那是本檔案永遠不會按的按鈕」（`:611`）。⇒ **批這格必須連「本人在那支棧上親手按一次同意」一起排**，只批「跑 spec」買不到任何寫入證據。
    * **證據層級（如實）**：本輪只到「spec 已寫＋型別層可編譯」——`npx tsc --noEmit` exit **0**、輸出 0 行，且 `frontend/tsconfig.json` 的 `include` 為 `**/*.ts`／`**/*.tsx`、`exclude` 只有 `node_modules` ⇒ e2e 檔確實被型別 gate 讀到。離線契約由 `frontend/tests/paperExecutionPanel.test.mjs`（**23** 節點）＋ `frontend/tests/activationPanel.test.mjs`（**15** 節點）撐，**那不是 DOM 證據**。**瀏覽器 NOT RUN**：本輪未起 dev 棧、未啟動 Playwright，並依本節既有的「移除 `--list`」決定**也未做 `--list` 探測**。
    * **與其他 verdict 的關係**：C-DDL 過不過、那兩支旧 spec 跑成什麼樣，**都不算**本格的證據；本格的 READ group 只需 **C-resume**（無 DDL、無 seed），WRITE group 還需 C-agent-journey 自己的兩個引數＋本人那次同意。
* **兩支 spec 自己的寫入（本輪逐檔讀源補齊，R19 漏了這一段）**：
  * `manual-decision-lineage.spec.ts`：**有寫**。`POST /api/v1/simulations/{session_id}/manual-decision` 三次——透過按鈕的真提交（`:285` 那段 `waitForResponse`）、兩支**故意**的競態嘗試（`:467`、`:722`，送 `competing` body）；`POST …/pause`（`:608`）與 `POST …/resume`（`:665`）——後兩者各自寫 `simulation_sessions.status` ＋ **1 筆 `audit_events`**（`app/api/simulations.py:1871-1897` 寫 `simulation_paused`、`:1900-1926` 寫 `simulation_resumed`，兩邊都 `rules_locked=True`）。檔頭自述「No session, portfolio, watchlist row or inference is created here」（`:20`）⇒ 它**復用**既有列，但那三類列的狀態與 audit  trail 確實會動。
  * `daily-estimate-journey.spec.ts`：**只一筆寫**＝`POST …/daily-estimate/run`（`:552` 斷言按鈕必須用 POST 送），而且**未設 `E2E_DAILY_ESTIMATE_SKILL` 時跳過**；其餘 API 呼叫全為唯讀 oracles（`/status` `:251`、`/usage/quota` `:267`、`/consent` `:338`）。
  * _launcher_ 另外兩支寫入器：`seed_e2e_personal_learning.py`（**會代寫訓練同意**、**會按 owner 刪除** 9 類列，見 15.4(a)）與 `seed_candidate_scenarios.py`（寫 bars／users／experiment／version／session／`HumanDecisionEvent`，`execution_status=record_only`；檔頭自述空帳號「no strategy, no decisions, no consent」）。⇒ **C-new 一開始就在庫裡放了同意列與決策列**，這是「dev 結果」不能拿去當本人環境證據的具體理由之一。
* **輸入／第一個副作用**：輸入＝14.0 那支候選（本節＝`5bba3444…`，15.0）＋ profile 指到的 `DATABASE_URL`；第一個副作用＝**C-new 的 `create database`**（`:626`），其後才是 `upgrade head`（`:628`）與 seed（`:587`）。**C-resume 的第一個副作用是 `$STATE_DIR/runtime-identity.env` 與三支 pid 檔。**
* **停止線**：head 不符／①②③ 任一不符／前置齊全卻 failed ⇒ 停、存 log、原樣回報，**不重跑、不改設定碰運氣**；**永不使用 `--reset`**（它 drop 庫）。
* **停止命令**：`./stop_candidate_stack.sh`——它依 profile＋pid 檔＋**身分核對**才發信號（`check-process`，`stop_candidate_stack.sh:37`、`:75`），⚠️ 如實：`SIGTERM` 無效後會升級到 **`kill -9`**（`:61`）；**不從 `pgrep` 挑別人的行程**。
* **狀態：NOT RUN**（未獲准啟動服務／瀏覽器；C-本人與 C-扣額兩格本質上必須由本人動作）。

### 15.6 卡 D（修正版）——明確白名單＋逐檔雜湊重建副本，跑 4 支具名檔（純離線、零網路、零 Git 寫入）

* **本輪改了什麼**：①不再用「幾條 grep 排除敏感檔」寄託機密安全，改成**前綴白名單＋逐檔 sha256＋拒絕清單**；②拷貝前後各驗一次身分，並處理 symlink 與來源變動；③**取消 `git init`**（讀源證實 runner 不需要 Git metadata，見下）；④除 `cwd`／`interpreter` 之外，直接記**承重模組的 `__file__`／解析後路徑／匯入路徑**，發現回到原 checkout 即停；⑤把 56／56 定位成**原環境對照值**。
* **（1）白名單（唯一來源，逐字）**：只接受以下**路徑前綴**，其他一律**不拷**並原樣寫進 `$DST/REJECTED.txt`（不猜、不「順手排一下」）：
  `backend/app/`、`backend/tests/`、`backend/scripts/`、`backend/alembic/`、`backend/`（僅 `*.py`／`*.ini`／`*.toml`／`*.txt`／`*.cfg`）、`frontend/src/`、`frontend/e2e/`、`frontend/tests/`、`frontend/`（僅 `*.ts`／`*.tsx`／`*.mjs`／`*.json`／`*.css`）、頂層 `*.md`／`*.sh`。
  在此之上另有**明確名單排除**（不是 pattern，是一條一條）：`backend/.env.test`、`backend/.env`、`backend/postgres`（**0-byte 誤檔**，前輪已記錄、**未刪**，交本人裁決）、`backend/collected_nodes.txt` 以外任何檔名含 `credentials`／`token`／`.pem`／`.key` 的路徑、`alethra-evidence/`（873 條歷輪附件）、任何 `*.log`。**共享 DB 不在來源裡這件事要如實寫成「沒有這種檔」而不是「我排除了它」**：實測未追蹤集內 `.db` 檔數＝0，兩條 lane 的庫都落在 `${TMPDIR}`。
* **（2）身份（拷貝前＝拷貝後的比較基準）**：對清單裡**每一檔**算 `sha256  路徑` 兩欄表 `$DST/BEFORE.txt`；整張表再 hash（`MANIFEST-before`）。⚠️ 承 15.0：本輪改动含 3 支前端檔，**python digest 讀不到**，所以副本身份靠這張逐檔表＋15.0 那張表，不靠單一 digest。
* **（3）來源變動偵測**：解 tar **前後**各重算一次 `SOURCES.txt` 的行數與 manifest hash；兩不等 ⇒ 停（來源在這一輪被並行會話動過），**不假裝拷的是 15.0 那支候選**。本輪現測：porcelain **231** paths（sha `704e27c0…`）；`git ls-files -d`（已刪除路徑）**必須為 0**——非 0 ⇒ 那些路徑會進清單却在盤上不存在（`tar cf -` 因此非零），逐條寫進 `$DST/DELETED.txt`、**不還原**、以「來源樹與本卡前提不符」停止，**不**在 `$REPO` 新建同名檔。
* **（4）symlink（本輪現測，不用敘述代替）**：來源樹（排除 `.venv`／`node_modules`／`__pycache__`）symlink 數＝**0**；`.venv` 內 symlink＝**3**：`bin/python → python3.12`、`bin/python3 → python3.12`、`bin/python3.12 → /Users/tommylam0915/.local/bin/python3.12`（**絕對路徑，指向 `$DST` 之外**），且 `sys._base_executable = /Users/tommylam0915/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12`。⇒ 「副本完全自外於原機器」**不成立**，如實寫：venv 的直譯器與 stdlib 仍在 `$HOME` 下（`.venv` 共 470 MB／17,515 檔，離線無法重建，只能整份 `cp -R` 帶過去；BSD `cp -R` 保留 mode／symlink）。
* **（5）runner 需不需要 Git metadata（讀源定的，不是猜的，也為了這句話去執行 D）**：`scripts/run_tests.sh` 全檔只有 `:424`／`:425`／`:427` 三處呼叫 `git`，全部 `2>/dev/null` 且有 `|| echo '<git could not answer>'` 保護 ⇒ **無 HEAD 不會失敗**，只是 `identity.txt` 那兩行降級為 `git: <git could not answer>` 與 `porcelain: 0 path(s) sha256 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（最後這串是**空輸入**的 sha256，本輪現算，不是抄的）。本輪現測 `tests/conftest.py` 與 D 那 4 支測試檔**都不叫 git**。⇒ **本卡取消 `git init`**（它不是 runner 要求，而是上一輪自己加的副作用，現在如實取消），**也不放寬 runner、不改 gate**；副本身份由（2）那張逐檔表承擔。
* **（6）命令（一步＝一個可單獨否決的動作；每步非零即停）**：

```bash
REPO=/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra
DST=$(mktemp -d "${TMPDIR:-/tmp}/alethra-r20-repro-XXXXXX")   # 已存在 ⇒ 停（不覆寫、不 rename 重試）
cd "$REPO"
# D-1 白名單清單（逐行可審，不是一句宣稱）
git ls-files -co --exclude-standard | grep -E '^(backend/(app|tests|scripts|alembic)/|backend/[^/]+\.(py|ini|toml|txt|cfg)$|frontend/(src|e2e|tests)/|frontend/[^/]+\.(ts|tsx|mjs|json|css)$|[^/]+\.(md|sh)$)' \
  | grep -vx 'backend/.env.test' | grep -vx 'backend/.env' | grep -vx 'backend/postgres' \
  | grep -v '^alethra-evidence/' | grep -v '\.log$' > "$DST/SOURCES.txt"
comm -13 <(grep -E '^(backend/(app|tests|scripts|alembic)/|backend/[^/]+\.(py|ini|toml|txt|cfg)$|frontend/(src|e2e|tests)/|frontend/[^/]+\.(ts|tsx|mjs|json|css)$|[^/]+\.(md|sh)$' <<< "$(git ls-files -co --exclude-standard)") "$DST/SOURCES.txt" \
  > "$DST/REJECTED.txt"            # 被排除的每一條都要能在這裡逐行讀到
git ls-files -d > "$DST/DELETED.txt"    # 本輪現測 0 條；非 0 ⇒ 停
(cd "$REPO" && tar cf - -T "$DST/SOURCES.txt") | (cd "$DST/repo" && tar xf -) 2>&1 | tee "$DST/tar.log"
# D-2 帶 venv（離線重建不了它）
mkdir -p "$DST/repo/backend" && cp -R "$REPO/backend/.venv" "$DST/repo/backend/.venv"
# D-3 拷貝後重算同一張表並對比（不等 ⇒ 停）
(cd "$DST/repo" && xargs shasum -a 256 < ../SOURCES.txt) > "$DST/AFTER.txt"
diff "$DST/BEFORE.txt" "$DST/AFTER.txt"
# D-4 承重模組落點（任何一個回到 $REPO ⇒ 停，這才是「不是從原 checkout import」的實物證據）
cd "$DST/repo/backend" && ./.venv/bin/python - <<'PY'
import pathlib, sys, importlib.util
for name in ("app.services.decision_lineage","app.api.simulations","app.api.human_decisions",
             "app.services.daily_estimates","app.ml.agent_strategy_trainer","app.api.conflicts"):
    spec = importlib.util.find_spec(name)
    print(name, "|", spec.origin, "|", pathlib.Path(spec.origin).resolve())
print("sys.path[0]:", sys.path[0]); print("executable:", sys.executable)
PY
# D-5 跑具名節點（入口一律走 $DST 的 venv／run_tests.sh；禁止 bin/pytest、bin/alembic、裸 pytest）
cd "$DST/repo/backend" && ./scripts/run_tests.sh sqlite \
  tests/test_trained_inference_lineage_chain.py tests/test_decision_lineage.py \
  tests/test_successful_inference.py tests/test_daily_estimate_prep_readiness.py
```

  入口禁忌（保留 R19 那筆實測理由）：`.venv/bin` 38 檔裡 **31 支 shebang 寫死本 checkout 絕對路徑**（本輪現算：38／31，venv 本輪未動 ⇒ 該事實仍成立），直接叫那些腳本會 **import 回原處**——所以只用 `$DST/repo/backend/.venv/bin/python -m …` 與 `./scripts/run_tests.sh`。
* **（7）驗收（量了才准斷言，不拿預測值當 gate）**：D-5 必須**自己量出** **56 collected／56 passed／0 skipped／0 failed／exit 0**，逐檔對上：`test_trained_inference_lineage_chain.py` **6**＋`test_decision_lineage.py` **26**＋`test_successful_inference.py` **14**＋`test_daily_estimate_prep_readiness.py` **10** ＝ **56**（本節 18:3x 在 `$REPO` 以 `--collect-only` 現測的四個數，即**原環境對照值**）。另三個必要讀數：該 run 自己的 `identity.txt` 顯示 `cwd` 與 `interpreter` 都在 `$DST` 之下；D-4 每一行 `spec.origin` 與 `resolve()` 都在 `$DST` 之下（`executable` 例外，它本来就是 venv symlink，見（4））；`git:`／`porcelain:` 兩行允許是（5）那兩個降級值，**不**算失敗。⚠️ **56／56 在副本實際跑過之前仍是 NOT RUN**，本卡交的是可批的命令，不是結果。
* **（8）目的目錄的全部寫入（逐項，不用「其他不变」）**：`$DST/SOURCES.txt`、`$DST/REJECTED.txt`、`$DST/DELETED.txt`、`$DST/BEFORE.txt`、`$DST/AFTER.txt`、`$DST/tar.log`、`$DST/repo/**`（白名單內容）、`$DST/repo/backend/.venv/**`（17,515 檔＋3 symlink）、lane 自己的 run dir（在 `${TMPDIR}/alethra-runs/`，不在 `$DST`）。**不寫 `$REPO`、不碰 `.git`、不 `git init`、不 stage／commit／push。**
* **停止線**：任一非零 exit｜`DELETED.txt` 非空｜`BEFORE`≠`AFTER`｜`tar.log` 出現 `Cannot stat`｜任一 `spec.origin` 解析到 `$REPO`｜節點數≠56｜`$DST` 已存在 ⇒ 停、保留 `$DST`、**不重試、不刪、不 rename**。只管理本卡記下的子行程。
* **狀態：NOT RUN**（本輪不建 `$DST`、不複製——Brief：不複製快照）。

### 15.7 本輪寫入清單與未執行（如實）

| 檔 | 動作 | 現測坐標 |
|---|---|---|
| `backend/app/ml/agent_strategy_trainer.py` | 推論門（在 `joblib.load` **之前**）＋回應尾 4 欄＋檔頭敘述第 3 條 | `:667-683`、`:741-744`、檔頭 |
| `backend/app/api/agent_strategy.py` | 註解改正（被消費的是批准列；未消費的是**範圍**） | `PUT …/activation` 上方那段 |
| `backend/tests/test_agent_strategy_slice.py` | ＋1 helper、改 1 支、**重命名並重寫** 1 支 | `:2178`、`:2397`、`:2646`（class `:2351`） |
| `frontend/src/app/agent-strategy/page.tsx` | 3 處寫死敘述改為讀實際值＋型別補欄 | `:299`、`:764`、`:1208`、`:1274-1275` |
| `EXECUTION_TABLE_20261117.md` | 本節 §15 | — |

**本輪沒有**：建庫、啟動 PG／服務／瀏覽器／cron、跑或寫 migration、讀或印任何秘密值、展開程序環境、外呼、複製快照、改 runner gate 或 skip 預算、任何 Git 寫入、把 consent／approval 插進任何**真實**帳號。本輪唯一的真實業務寫入发生在 **lane 自己的暫存 SQLite 檔**（`${TMPDIR}/alethra-sqlite-test-34843.db`）：真 sklearn 擬合、真 artifact 寫入暫存 `MODELS_DIR`、以及 15.1(3) 那個通過格在該暫存庫裡留下的 training job ＋ owner activation approval 列——**都不是本人帳號上的列**。

**未執行、未驗證**：PG 全鏈（A 三格）、自然 cron（B 四格）、dev 棧與兩支 spec（C 四格）、隔離重建（D）、per-skill 啟用與「按【啟用】的 UI」；所有 consent／activation 仍只能由本人動作；**8 個研究家族的 FAIL 原樣保留**，本輪未動任何門檻。

## 16. 第 21 輪（2026-11-21）——①owner approval 接通到客戶操作、②紙上執行單獨判定、③B 只補準備材料

> 本節同样是**離線工程＋批准材料**，不是執行批准。本輪**未**建庫、**未**啟動 PG／服務／瀏覽器／cron、**未**跑 migration、**未**讀或印任何秘密值、**未**展開程序環境、**未**外呼、**未**重釘候選（`5bba3444…` 的 103/103 原樣保留、未重跑）、**未**做任何 Git 寫入或部署。§15 保留原樣，本節只對其中**兩处**提出更正（16.4），其餘不改寫歷史。

### 16.0 本節身份（**14:0x 現測**，逐值綁本輪 run 目錄）

| 尺 | 本輪現測值 | 怎麼量的 |
|---|---|---|
| python 樹 digest | `a45bcee9f00bcbc1a67a8a72a857ea6246f1a5b91b976521c9e7bc572b9eeace` | 讀自 `sqlite-20261121T135519Z-64168/identity.txt` 的 `tree-digest-before` 與 `-after` 兩行（**兩行相等**）；同一值也出現在 `T134643Z-53997`。⚠️ `6ce7842a…` **不是**本輪候選，是 16.1 那支 exit 1 紅燈 run 的**改前樹**（見該節歸類） |
| base | `live1-impl` HEAD `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` | `identity.txt` 的 `git:` 行（本輪零 Git 寫入 ⇒ HEAD 未動） |
| 髒樹 | porcelain **231** paths，清單 sha256 `704e27c08d31923b19a6e0b06dee900386e6e25fac89ee96f5a930dba17ca8c7` | `identity.txt` 的 `porcelain:` 行；與 §15.0 同值 ⇒ 本輪**沒有新增未追蹤路徑**，改的都是已追蹤的 M |
| lane 指紋 | python 3.12.16 ／ pytest 8.3.3 ／ SQLAlchemy 2.0.36；target `sqlite ${TMPDIR}/alethra-sqlite-test-64168.db`；`DATA_PROVIDER=<unset>` | `identity.txt` 逐行 |
| 本輪共用的前兩輪常量 | 卡 B 種子：`BARS` 為 2024-01-01 起 **140** 個工作日 ⇒ 首根 `2024-01-01`、末根 **`2024-07-12`**、相鄰兩根間隔 **1** 天（`BAR_AGE_DAYS`）；決策窗第一根 `BARS[30]=2024-02-12`、最後一根 `BARS[30+40×2]=2024-06-03` | 以 `python3` 逐字重跑 `tests/test_personal_strategy_learning.py:41-47` 的 `_business_days` 算出（**未執行 pytest**），常量讀自 `:33-38` |
| 共用主機現況（一次量測＝一次事實，執行時必須重量） | `lsof -nP -iTCP:5432/54321/8101/3101 -sTCP:LISTEN` 各 **0**、`pgrep -fl postgres` **0** 支 | 本輪只讀量測 |

⚠️ **這把 python 尺讀不到本輪改動的主體**：本輪產品改動全在前端 `.ts`／`.tsx`，`tree_digest()` 只走 `*.py`（`scripts/run_tests.sh:373-379`）。所以身分必須連同逐檔雜湊：

| sha256 | 路徑 | 行 | 本輪動作 |
|---|---|---|---|
| `aca1fa7413cd3e312a3dcf6b67724ebff6e7c66bce91fe74282586c26c0f0419` | `frontend/src/lib/api.ts` | 1295 | **改**（＋3 支 activation 方法） |
| `46f0ff0b1f6f959d115773ab0568a3e7d09d2bbeec5908f1c881123054b630df` | `frontend/src/app/agent-strategy/page.tsx` | 1688 | **改**（讀／批准／撤回面板＋移除 3 處寫死敘述） |
| `1729b4bffe363530bad24535561d4569edcc4013338729de5b0b4a7520041dee` | `backend/tests/test_agent_strategy_slice.py` | 3466 | **改**（＋3 支節點＋2 支 helper 改造） |
| `c212d3cbf79e2b3ac8d465be421a5831c2f6db993b33ef386ad97a11dee43888` | `backend/app/services/daily_estimate_readiness.py` | 326 | 未改（卡 B 引用，現讀坐標） |
| `13acdfd6624c8073e4e904a4f635e6cce13895b2a3404f07ebdb8496c48efe98` | `backend/scripts/daily_estimate_readiness.py` | 100 | 未改（同上） |
| `f8846ee9cf08df4b875abf1cdda82ac249f90f9a51fa8828a921a23b10d26039` | `backend/tests/test_daily_estimate_prep_readiness.py` | 768 | 未改（同上；**本輪未重跑**，屬無變更批次） |
| `d8efbd12aed17e3222c133f5181d7498d18aa5438cdba94c4c5c0fad7e01ecaf` | `backend/tests/test_personal_strategy_learning.py` | 1888 | 未改（種子來源，現讀坐標） |
| `c9638b883f3d1d60a42e949e3c2e8bcede0c84428b841637154d92beb6800a39` | `backend/app/services/daily_estimates.py` | — | 未改 |
| `f4d8d144e838efea81044f39ef712cded525acdd9b6f35a9836a2502168ce539` | `backend/app/config.py` | — | 未改 |

### 16.1 ①接通 agent-strategy owner approval 的客戶操作（**實際 diff**）

**協定先讀回來**（本節每個前端斷言都綁這一段，全部現測）：批准要回貼的是 `GET …/activation` 的 `requirements`，判定讀的是同一回應的 `state`（`app/api/agent_strategy.py:284-299` → `app/services/model_activation.py:448-460` 與 `:329-410`）。`PUT` 的 body 四項必填＋一項選填：`artifact_checksum`（64 字元）、`policy_version`、`acknowledgement_text_version`、`acknowledgement`、選填 `skill_unit_id`（`api/agent_strategy.py:114-136`）。伺服器比對確認文字時用 `_normalise_ack = "".join(text.split())`（`model_activation.py:69-70`）——**剝掉全部空白**。撤回走 `POST …/activation/revoke`，body `{reason: ≤64}`（`:139-140`），且**不受 `require_screening_enabled` 閘住**（`:346`），因為撤回是關閉權而不是功能开关。拒因回 **409** ＋ `detail.{code,message_zh,details}`。

**實際 diff（本輪區域，行號現測）**：

| 檔 | R20→R21（行） | 本輪新增區域 | 該區域證到什麼 |
|---|---|---|---|
| `frontend/src/lib/api.ts` | 1271→**1295**（+24） | `:1149-1150` `jobActivation`、`:1151-1162` `activateJob`、`:1163-1166` `revokeJobActivation` | client 之前**根本沒有**這三個入口（§15.0 那輪把它列為「未改，證明 client 缺 activation 方法」）；現在讀／批准／撤回各一條，路徑與 method 逐字對上 `:284`／`:302`／`:340` 三條 route |
| `frontend/src/app/agent-strategy/page.tsx` | 1324→**1688**（+364） | `:16-20`、`:312-342`、`:484-493`、`:521-532`、`:586-601`、`:791-798`、`:799-833`、`:835-865`、`:1367-1371`、`:1387-1580` | 見下方五條判讀規則 |
| `backend/tests/test_agent_strategy_slice.py` | 3184→**3466**（+282） | `:95` `SECOND_ARTIFACT_INSTRUMENT`、`:2080` `_ready_fixture_snapshot`（改關鍵字 `instrument`/`title`/`bars`）、`:2133` `_test_row_features`、節點 `:2789`／`:2895`／`:2970` | 補的是Brief點名的「不同 job／artifact 的批准不能挪用」那一格 |

⚠️ **歸屬如實**：`git diff --numstat HEAD` 對這三支給的是 `page.tsx 373/4`、`api.ts 150/10`、`slice 測試 487/11`——那是**對 HEAD 的累計**（R19–R21 三輪都碰過、且本輪零 Git 寫入 ⇒ 沒有中間 commit 可切）。本輪自己的貢獻只能由上表的「R20 hash＋行 → R21 hash＋行」界定，不得把那三個 numstat 數字說成本輪單一改動。

**前端五條判讀規則（寫進程式的那個不變量，不是願望）**：

1. **狀態只從伺服器讀回**：`:1367-1371` 的 Activation FactRow 取值 `activation ? activation.state.activation_status : job.activation_status`——讀到之前显示 job 列的值，讀到之後显示 `state` 的值，**兩者都不是前端自己宣稱的字串**；`:1387-1580` 面板把 `activation_status`／`reason_code`／`approval_id`（無列時顯示 `'none recorded for this artifact'`）／`approved_at`／`movable_to_customer_environment` 逐個畫出來，九條 `bindings` 以 `activation-binding-<name>` 逐條顯示 met／not met（`model_activation.py:329-410` 的 keys，不在前端複製一份清單）。
2. **沒有讀到就不給按**：`approveReady`（`:791-798`）要求 `requirements.artifact_checksum` 存在、`screeningEnabled`、`activationRead` 為真、`acknowledgement_text_zh` 非空、且本人輸入經 `normaliseAck`（`:16-20`，鏡射 `model_activation._normalise_ack`）後**逐字等於**伺服器那段文字，再加 `!activationBusy`。缺任何一條，`activation-approve` 是 disabled（`:1513`）。
3. **批准送的是伺服器給的值**：`onApproveActivation`（`:799-833`）的 `artifact_checksum`／`policy_version`／`acknowledgement_text_version` 三項直接取自 `activation.requirements`，只有 `acknowledgement` 是本人親手打的文字；`skill_unit_id: null`（註解寫明「personal selection 的範圍是另一個判斷；NULL 不等於全部技能帳號」）⇒ **沒有**把 personal selection 或每日 consent _merge_進這個動作。寫入成功後先 `await loadActivation(jobId)` 再 `await loadJobs()`，ok-notice 只在**await 之後**設定；catch 走 `{tone: errorTone(err), text: errorMessage(err, 'The approval was not recorded.')}`——409 是 warn（那是決定，不是故障），**失敗絕不顯示成功**。
4. **撤回先確認、且只看 `approval_id`**：`onRevokeActivation`（`:835-865`）第一次點擊只開啟確認面板（與 `onRevokeConsent` 同形），`activation-revoke-reason` 上 `maxLength={64}` 對齊伺服器 schema（`:139-140`）；該區塊的顯示條件是 `state.approval_id` 存在（`:1528`），**不是** `activation_status === 'APPROVED'`——因為 revoke 路由用 `approval_for_job` → `live_approval` 解析（`model_activation.py:99-106`），一筆「仍生效但綁定不符」的批准照样要能撤回；同時它**不受 `screeningEnabled` 禁用**（`api/agent_strategy.py:346` 該路由未加閘）。寫入後一樣重新讀回 activation 與 jobs。
5. **不挪用、不越 job**：`:586-601` 用 `activationJobId`（只在 `job.status === 'completed'` 時非空）當 effect 的鍵，換 job 時**先清掉上一筆讀取與輸入**再重讀——為的是「為 job A 打的文字不可能被送去做 job B 的批准」。

**移掉的假敘述**：`:1387` 上方那段「Training and inference」敘述原先寫死「The model is never activated and never serves customers here.」——這句話與本節第 1 項要接通的东西直接矛盾，已改寫為：分數這條門在本人批准該 checksum 之前**一律拒絕**，批准只把模型在**這個帳號內**開啟，不是對客戶發布、也不下單。`grep -rn "never activated" frontend/src` 現測 **0** 筆（整個 src 已無此句）。其余兩處舊的寫死陳述（`job.activation_status` 硬編 `not activated`）在本輪由規則 1 取代之。

**驗證（三条，各自分開）**：

| 驗的是什麼 | 命令／物件 | 結果（原始） |
|---|---|---|
| 型別閉合（離線） | `cd frontend && npx tsc --noEmit --incremental false -p tsconfig.json` | `PROJECT_EXIT=`**0**（`tsconfig.json` 現讀 `strict: true`、`noEmit: true`；`include` 含 `**/*.tsx` ⇒ 本檔確實被吃進去） |
| 上面那條**不是空跑**（正控） | `npx tsc --noEmit --strict /tmp/tsprobe_control.ts`（故意 `string` 指派給 `number`） | `TS2322`／`CONTROL_EXIT=`**2** ⇒ 同一条 tsc 會報錯，0 有意義 |
| 缺失的那一格（正式 sqlite lane） | `cd backend && TEST_MODE=true ./scripts/run_tests.sh sqlite tests/test_agent_strategy_slice.py -q` | run `sqlite-20261121T135519Z-64168`：**51 collected／51 passed**、`skipped=0 failures=0 errors=0`、`exit-code: 0`、`tree-digest-before == after == a45bcee9…`、42.62 s；evidence gate 關在本 run 自己的 junit 上（skip 預算 0） |
| 三節點真的在樹上 | `sqlite-20261121T134643Z-53997`（只點那 3 支 node id） | **3 collected／3 passed／exit 0**，`collect.txt` 內三支 id 各 1 筆 |

**本輪造成的真實紅燈（原始物，不美化）**：`sqlite-20261121T134559Z-53149` → `2 failed, 1 passed`、`exit-code: 1`、`tree-digest a45bcee9` 之前的 **`6ce7842a…`**。FAIL 的兩支是 `…::test_an_approval_for_one_artifact_does_not_score_a_second_artifact` 與 `…::test_the_approval_door_refuses_to_bind_another_artifacts_checksum`；`pytest.log:84-86` 的原始斷言是 `assert resp.status_code == 201` → `assert 422 == 201`，回應體 `Unknown asset_id: DEMO-GAMMA. Allowed: demo assets or MARKET:SYMBOL format`。
⇒ **歸類：測試種子選值錯，不是產品缺陷**。第二支 artifact 的標的填了 `DEMO-GAMMA`，而它不在 `app/api/schemas.py:19` 的 `ALLOWED_DEMO_ASSETS` 內，所以實驗建立那一步在請求驗證層就死了，根本走不到批准門。修法＝把該常數改成白名單內的 `DEMO-BETA`（`:95`），**沒有改斷言、沒有放寬 `assert 422 == 201` 之外的任何閘門、沒有把節點改成 skip**；改完即上表那支 3/3 綠。
另一處是**寫程式時自己抓到的**（無對應紅燈 run，如實標明）：初稿在 `page.tsx` 宣告了一支沒被使用的 `interface ActivationWriteResult`，在 `noUnusedLocals` 下會變紅燈 ⇒ 直接刪掉，而不是留個「將來會用到」的型別。

### 16.2 ②紙上執行單獨判定：**未接通**（本輪沒有借推論入口宣稱完成）

**判定依據（四條現測，不靠印象）**：

| 查的是什麼 | 現測 | 结论 |
|---|---|---|
| 誰消費 agent artifact 的批准 | `grep -rn "AgentTrainingJob\|agent_training_job_id\|activation_approval_id" app --include="*.py"` 扣除定義檔 ⇒ **1 筆**：`app/ml/agent_strategy_trainer.py:744`（回應尾的歸因欄）。呼叫 `run_inference` 的生產點也**只有** `app/api/agent_strategy.py:412` | 批准目前**只**被「手動推論那條 request/response 路徑」消費；那是一支 scoring 端點，**不寫**任何 paper 物件 |
| 紙上引擎的決策來源是什麼 | `PaperTradingEngine`（`app/services/paper_trading.py:64`）由 `create_strategy_and_run(..., strategy_version_id=None)`（`:103`）建 run，插入 `paper_trading_runs (… strategy_version_id, dataset_id)`（`:162-174`）；決策是照 `strategy_study_trades` 的 `rule_id` 回放（`:339-376`、`:360`、`:367`） | 純規則／研究回放，**沒有** artifact、**沒有** job、**沒有** activation 這條腿 |
| 即時紙上路徑呢 | `app/services/live_trading_daemon.py` 對 `model_version_id`／`artifact`／`activation` 的 grep 計數＝**0**；它的決策寫入在 `:113-120`，欄位只有 `id, owner_id, actor_type, decision_type, decision_time, rationale, paper_portfolio_id`；訊號來自 `app/services/signal_generator.py:391+` 的規則 | 即時紙上**完全不讀**批准；即使在這裡接，也沒有地方放批准身分 |
| 現成的列撐得住嗎 | `app/models/paper_trading_run.py:44-45`：`strategy_version_id`／`dataset_id` 皆 **nullable、無 FK**（`:42-43` 註解自己寫明「No FK at DB level」）；`app/models/paper_portfolio.py:157` `PaperOrder.model_or_strategy_version_id` 是裸 UUID、可為 NULL；`:221` `DecisionRecord.linked_inference_id` 註解說指向 `usage_records.id`，但 `app/ml/agent_strategy_trainer.py` 對 `usage_record` 的 grep＝**0 筆** | 既有欄位**買不到**這個語意：它記不了是哪支 job、哪個 artifact、哪張批准；而 `linked_inference_id` 這條腿對 agent 家族根本沒有可指的列 |

⇒ **產品 verdict：紙上執行（由 agent artifact 驅動的 paper 執行）＝未接通／不存在該入口。** 本輪因此**不**動產品碼去硬湊：不把批准塞進 `PaperTradingEngine` 的規則回放（那會把「規則」冒充成「模型」），也不塞進 personal `predict`（那是另一個模型家族、另一道門 `check_model_version_inference_authorization`，`model_activation.py:181`，消費點 `app/api/personal_models.py:620`）。推論入口通過**不等於**紙上執行通過——本節就是為了把這兩件事分開而存在。

**缺的那一個操作（一句話）**：沒有一條「拿這支已批准的 artifact、以這個 owner／skill 身分，為某個標的產生紙上決策並落入帳路徑」的入口。要做的是**新增一条以批准為前置的紙上執行入口**，而不是在現有 scoring 回應後面接一段下單程式。

**最小介面契約（兩層，缺一層都不算接通）**：

1. **資料層——決策必须自帶可點名的批准身分。**
   - 新增可核對的三項血緣，落在一筆紙上 run／訂單／決策列上：`agent_training_job_id`（uuid，指向 agent 訓練 job 列）、`artifact_checksum`（64 字元）、`activation_approval_id`（uuid，指向 `model_activation` 那張批准表）。三者都必須是**建列時寫入**，不是事後補述。
   - 形態上必須是**帶 FK 的列**，而不是再複製 `paper_trading_run.py:44-45` 那種 nullable／無 FK 的備註欄——後者已被證明「存在但買不到語意」（見上表第 4 列）。任何「nullable 欄位加好了所以已接通」的說法在此判為**未接通**。
   - 讀回要求：一張紙上決策要能只用這三項回到 `requirements` 那組值（`model_activation.py:448-460`），從而回答「這個成交出自哪支 artifact、憑哪張批准」——這是 16.1 面板已經顯示、但紙上側目前**答不出來**的問題。
2. **執行層——在產生決策的那一刻重新驗批准，拒絕時零新增。**
   - 入口在**取用模型之前**呼叫 `activation_state`／`live_approval`（`model_activation.py:99-106`、`:329-410`）重derive 一次，比照 `agent_strategy_trainer.py:667-683` 的順序：job 存在 → 完成 → dataset checksum → 血緣 → artifact 重算 → **批准門** → 才准載模型。載模型之前那一步不能省，否則批准撤回之後既有行程照樣跑。
   - 任何不符 ⇒ 以**具名拒因**拒絕（沿用 `:381-391` 那九支 code 的語彙，必要時新增一支點名「紙上執行需要生效批准」的 code），並保證**零新增**：該次操作不寫 `paper_decisions`／`paper_orders`／`paper_fills` 任何一列，也不留未結算的額度預留。
   - 撤回之後必須**重新讀回**才算數（16.1 規則 4 已在 UI 端遵守；執行端要能重現同一件事：撤回 ⇒ 下一次紙上執行被拒 ⇒ 拒因可點名 ⇒ 三張表列數增量為 0）。

**這一格要怎样才能改判「已接通」**：上面兩層各自有一支具名節點——合法批准 ⇒ 該入口**實際**產生紙上決策／訂單／成交並帶回三項血緣；不匹配（換 job、換 artifact、撤回後、scope 不符）⇒ **具名拒因＋三表增量為 0**。兩者都在正式 runner 裡跑過，才可宣稱接通；在此之前交付上只寫**未接通**。

### 16.3 ③卡 B 只補準備材料（不起棧）

**（a）固定一套合成決策與日線來源——以及「哪些寫入是測試種子」**

| 材料 | 唯一來源（現讀坐標） | 經路 | 寫入的表／上限 | 這算不算正式入口？ |
|---|---|---|---|---|
| 日線 140 根 × 2 標的 | `tests/test_personal_strategy_learning.py:58-74 _seed_bars`；常數 `:33-38`、`BARS` 於 `:47` | 直寫（fixture） | `market_data_bars` ≤ **280** 列；`market_data_metadata` 每標的 1 列（`tests/test_daily_estimate_prep_readiness.py:101-112`）⇒ **2** 列 | **不是**——測試種子，**不得**冒充正式資料入口，也不得冒充真人紀錄 |
| 已確認決策 205 筆 | `tests/test_personal_strategy_learning.py:194-246 _seed_confirmed_decisions`（`days=DECISION_DAYS=41`、`per_day=DECISIONS_PER_DAY=5` ⇒ `TOTAL_DECISIONS=205`，`:34-37`） | 直寫（fixture），每筆配 `record_label(state="confirmed")` | `human_decision_events` **205**＋`decision_labels` **205**＋`simulation_sessions` **205**（詳 16.4）；全部 `execution_status=record_only`、`actor_type="human"`、`server_instrument_id=US:AAPL` | **不是**——種子；`idempotency_key=uuid.uuid4().hex`（`:215`）隨機 ⇒ **可重現的是筆數與欄位語意，不是列身分** |
| 第二支標的怎麼湊齊 | `US:MSFT` 只靠 bars＋宣告範圍＋同一支選定版本（`tests/test_daily_estimate_prep_readiness.py:79 SECOND_INSTRUMENT`） | — | — | 205 筆決策**不**分散到 MSFT——這是讀源事實，不是疏漏 |
| 策略版本／凍結 | `POST /api/v1/experiments/` → `POST /api/v1/experiments/{e}/versions/{v}/lock` | 正式 HTTP | 實驗＋版本＋凍結原因 | **是**（正式入口） |
| **訓練**同意 | `PUT /api/v1/ml/personalization/consent` | 正式 HTTP | `personalization_consent`（purpose `personalization_training`） | **是**——且與每日估計同意**不可互換**（`app/services/daily_estimates.py:61`） |
| 快照 | `POST /api/v1/ml/personal/snapshots` | 正式 HTTP | `personalization_dataset_snapshots` | **是** |
| 訓練 | `POST /api/v1/ml/personal/training-jobs` | 正式 HTTP | `learning_jobs` ＋ 真 sklearn 擬合 ＋ **真 artifact 寫入 `MODELS_DIR`** | **是**（本卡要的就是「用正式訓練與 selection 路徑證明 artifact 可用」） |
| 選定 | `GET /api/v1/ml/personal/models` → `POST /api/v1/paper-portfolio` → `POST /api/v1/ml/personal/models/{model_version_id}/select` | 正式 HTTP | `personal_model_selections` | **是** |

⇒ 到這裡，**準備鏈已在 SQLite 上閉合**：選定列帶得出 `model_version_id`／`artifact_id`／`artifact_sha256`，而且 `artifact_sha256` 是對**當時那個目錄的位元組現算**比對（非循環證據），寫在那支已跑過的節點裡（§14.4 表 `:310`）。本輪**未重跑**該檔（無變更批次），只引用它已有的證據層級：SQLite、隔離暫存庫。

**（b）本人同意之前，scheduler 必須關閉——現讀的開關坐標**

* 預設即關閉：`app/config.py:140 enable_learning_scheduler: bool = False`；白名單預設空字串 `:148 learning_scheduler_jobs: str = ""`；解析器 `:206`；驗證器 `:320-339`（`:333-338` 明確拒絕「只有分隔符」那種會把 lane 集合悄悄清空的值）。
* ⇒ 「不起棧」在本輪是**事實陳述**而非宣稱：本輪沒有任何程序讀到這三個值，也沒有任何行程被啟動（`pgrep -fl postgres`＝0、四個監聽埠全 0）。真正的「停用格＝新增列 0」證據仍屬卡 B 執行時要另外數的那一格，本輪**不**代為宣稱。

**（c）同意之後、自然 tick 之前，先用唯讀報告核對那兩個未結算的業務鍵與額度**

* 報告：`assess_readiness(db, owner_id=…, now=None)`（`app/services/daily_estimate_readiness.py:160`）四項依序：本人每日同意＋所宣稱 scope（缺 ⇒ `:175 no_active_daily_estimate_consent`）→ 每個 skill 帳號擁有／active／有 selection 且能點名 model version 與 artifact（缺 ⇒ `:116 strategy_scope_missing`）→ 每個標的用同一條 `features_as_of` 讀出最新日與年齡並比對宣告上限（缺棒 ⇒ `missing_market_data`、過舊 ⇒ `:99 stale_market_data`），同時查**這一 window 的帳是否已付過**（`:67 already_billed`、`:239`、`:252`）→ 額度（不足 ⇒ `:287 quota_exceeded`）；kill switch 只看本入口實際消費的 scope（`:292-299 kill_switch_active`）；`billing_observation_allowed = not missing`（`:324`）。可花單位 `units_ready_to_spend`（`:282`）。
* 操作者入口：`backend/scripts/daily_estimate_readiness.py`——`DATABASE_URL` 未設 ⇒ **2**（`:61-67`）、`--owner-id` 不是 uuid ⇒ **2**（`:68-72`）、該 user 不在這支庫 ⇒ **2**（`:80-86`，刻意**不**報成 NOT_READY）；就緒 ⇒ **0**、未就緒 ⇒ **3**（`:97-98`，是答案不是 crash）；印 `report["target"]={dialect,database,host,port}`（`:40-49`）——**不印 URL、不印密碼**；`finally: rollback/close/dispose`（`:90-95`）。
* 業務鍵的形：`(owner, skill_unit, instrument, window)`，帳本判據是 `usage_records` 上 `metadata_json["via"]=="daily_service"`（`app/services/daily_estimates.py:77 DAILY_SERVICE_VIA`）。**兩個未結算**＝兩個標的各自的鍵在該 window 為 0；**足夠額度**＝`limit ≥ 2`（兩個標的＝兩個單位）。这两項在 tick **前**用上面那份唯讀報告核對，在 tick **後**逐鍵驗各新增 1 筆結算＋`quota_reservations` 非終態殘留＝0。
* ⚠️ **手動 run 不是自然 cron**：`POST …/daily-estimate/run` 那一格（§14.4 表 `:587`）證的是同一條扣額與同一個 window 對帳，它是 **fixture 內的一次正式入口呼叫**，**不能**取代自然 tick。因此本輪**不以手動 run 代替自然 cron**，該節點也不得被引用成 B 已通。

**（d）本輪之後卡 B 的狀態（三個缺口點名，不美化）**：**BLOCKED**。缺的不是腳本也不是來源材料——
1. **本人那道每日估計同意還沒有按**：`PUT /api/v1/ml/personalization/daily-estimate/consent`（`app/api/personalization.py:308-309`），body `{"skill_units":[{"skill_unit_id":"<uuid>","instrument_ids":["US:AAPL","US:MSFT"],"max_data_age_days":<int，可省略>}],"confirm":true}`。該門 `:328-336` 只收**密碼認證的本人 session**（`request_actor_provenance(request) != HUMAN` ⇒ 403 `daily estimate consent requires a password-authenticated owner session`），**API key／代理憑證無法代按**；本輪未插入任何 consent 列、未代按。撤回與狀態讀回另有 `:368-369`、`:416-417`。
2. **兩個直寫批准面尚未獲核可**（16.3(a) 前两列：bars 與 decisions 的種子直寫，目標是隔離庫）。
3. **那支已批准啟動的棧並不存在**：沒有 PG 叢集、沒有服務、沒有 cron ⇒ 自然 tick 的觀察條件無從成立。
⇒ 「準備服務、隔離資料寫入、本人操作」三者仍缺（1）與（3），依 Brief 維持 **BLOCKED**；本輪把（1）改寫成可由本人逐字執行的一段，代理不代填、不代按。

### 16.4 對 §15.4 的兩处更正（本輪讀源發現，原節文字不改寫，此處立帳）

1. **`simulation_sessions` 的列數：§15.4「直寫 2」寫的是 41 列（「每日一支 `_new_session`」），讀源應為 205 列。** 現在測：`_seed_confirmed_decisions` 在 `for j in range(per_day)` **內層迴圈**裡每筆事件呼叫一次 `_new_session`（`tests/test_personal_strategy_learning.py:214`，`for d in range(days)` 於 `:208`、內層 `:211`），且該 helper 自己的 docstring 寫明「One session per event」；`41 × 5 = 205` ⇒ 每筆決策各配一支 session。因此該段「上限＝205／205／41」的最後那個數字**少算了 164 列**。⚠️ **證據層級如實**：這是**由原始碼推導**的事實（含 docstring 與迴圈巢屬兩处交叉），本輪**未重跑**該檔（無變更批次、且 Brief 要求不重跑），所以不寫成「實測 205 列」。對卡 B 的實際影響：直寫批准面的**上限必須改成 205／205／205**，否則執行時會以「超過上限」為由停在一支只是帳寫錯的格子。
2. **候選 digest 的歸屬：`6ce7842a…` 是改前紅燈樹，不是本輪候選。** §16.1 那支 exit 1 的 run 才带這個值；本輪真正交貨的樹是 `a45bcee9…`（兩支綠燈 run 的 before／after 四行全等）。任何後續引用若拿 `6ce7842a…` 當「第 21 輪候選」都是錯的。另：`5bba3444…`（R20、103/103）依 Brief **未重釘、未重跑**，仍是它自己那輪的證據。

### 16.5 本輪寫入清單與副作用揭露

**本輪寫過的檔案（全部 in-place 編輯，共 4 支；無新檔、無刪除）**：

| 路徑 | 動作 | 現況 sha256 ／ 行 |
|---|---|---|
| `frontend/src/lib/api.ts` | 改（三支 activation client 方法） | `aca1fa74…` ／ 1295 |
| `frontend/src/app/agent-strategy/page.tsx` | 改（activation 面板＋5 條判讀規則＋移除 3 處寫死敘述） | `46f0ff0b…` ／ 1688 |
| `backend/tests/test_agent_strategy_slice.py` | 改（＋3 節點、helper 改關鍵字、種子標的改 `DEMO-BETA`） | `1729b4bf…` ／ 3466 |
| `EXECUTION_TABLE_20261117.md` | **追加本節 §16**（放置，不執行） | 追加前 **6329** 行／sha `5429f400…`；追加後 **6489** 行（本節不自指自己的雜湊——那是量出來而非抄下來的東西，操作者以 `wc -l`＋`shasum -a 256` 現測） |

**本輪花了什麼（業務寫入）**：只在**各自 run 自己的暫存 SQLite 檔**——`alethra-sqlite-test-64168.db`（51 節點整套）與 `…-53997.db`／`…-53149.db`（同 3 節點改前／改後各一次）。真 sklearn 擬合發生在這些 run 內，artifact 寫進 runner 指定的暫存 `MODELS_DIR`。**本人帳號：0 筆寫入、0 張批准、0 份同意、0 列插入。**
**本輪沒有**：啟動 PG／服務／瀏覽器／cron、跑或寫 migration、讀或印秘密值、展開程序環境（`ps eww` 未使用）、外呼、複製快照、改 runner gate 或 skip 預算、任何 Git 寫入（`git add`/`commit`/`push`/`tag` 均未執行）、重釘候選、把 consent／approval 插進任何真實帳號。

### 16.6 尚未驗證的客戶行為（逐條點名，不用「應該可以」）

| 行為 | 本輪證據 | 還缺什麼 |
|---|---|---|
| 本人在瀏覽器按下【批准】並看到狀態翻轉 | **NOT RUN** | 本輪只做到離線型別（tsc exit 0 ＋正控 exit 2）與後端 sqlite 契約。Brief 已授權這樣標法：「若 UI 只能做離線及型別驗證，browser 仍標 NOT RUN」。要改判需起 dev 棧＋真瀏覽器點擊，那是卡 C 的批准面 |
| 失敗時「不顯示成功」在真實 DOM 上的樣子 | 未驗證（僅為程式不變量：ok-notice 只在 `await` 之後設定） | 需一支 browser spec 把 409 送進來並讀回 NoticeBox 的 tone |
| 撤回後 UI 重新讀回 | 未驗證（程式上 `await loadActivation`＋`await loadJobs`；後端 revoke 路徑本身有測試覆蓋） | 同上 |
| 批准→推論→紙上執行整條鏈在 **PostgreSQL** 上的行為 | 未驗證 | 本輪全部是 SQLite 暫存庫證據；PG 全鏈屬卡 A |
| 由 agent artifact 驅動的紙上執行 | **不存在該入口 ⇒ 未接通**（16.2） | 16.2 那兩層契約實作＋各自一支具名節點 |
| 每日估計的自然 tick | **BLOCKED**（16.3(d) 三個缺口） | 本人按那道 consent＋一支批准啟動的棧 |
| `test_daily_estimate_prep_readiness.py`／`test_model_activation.py` 等無變更批次 | 本輪**未重跑**，因此不引用成「本輪已證」；引用時一律標明它屬於自己那輪的 SQLite 證據 | — |

## 17. 第 22 輪（2026-11-22）——①agent-model 紙上路徑接通（真 loader＋持久讀回）、②UI 跨 job 競態有可執行驗收、③B 換成可審的資料準備實物

### 17.0 帳本先於內容：本輪每一格的候選、真實退出碼與判定

| # | 交付 | 確切操作 | run／產物 | 數字 | **exit** | 候選（樹 digest） | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | 紙上路徑直接驗收 | `cd backend && TEST_MODE=true ./scripts/run_tests.sh sqlite tests/test_agent_paper_execution.py -q` | `sqlite-20261122T034211Z-91487` | 18 collected／**18 passed**、failures 0、errors 0、skipped 0、24.767 s | **0** | `92d82b1b…`（before＝after） | **PASS（SQLite）** |
| 1b | 相鄰泳道未受影響（含第 21 輪那 51 格） | 同 runner，10 支檔案的明示 selection（slice／paper-execution／rule-triggered／risk×2／personal journey／permission matrix／predict kill switch／paper ownership／paper portfolio） | `sqlite-20261122T034433Z-92523` | 173 collected／**172 passed＋1 skipped**、failures 0、errors 0、169.823 s；唯一 skip＝`test_risk_policy_across_lanes…::test_two_racing_submits_with_a_budget_of_one_book_one_order`（理由：row-lock serialisation requires a real PostgreSQL backend） | **0** | `fecb6434…`（before＝after） | **PASS（SQLite）**；該格 PG 屬 **NOT RUN** |
| 1c | 放了新 migration 之後，尾部契約仍對得上 | 同 runner，`tests/test_migration_tail_contract.py -q` | `sqlite-20261122T043834Z-16847` | 15 collected／**15 passed**、4.14 s | **0** | `d5093a28…`（before＝after） | **PASS（SQLite）**；migration **未對任何 PG 執行** |
| 2 | UI 跨 job 競態＋通知契約（離線） | `cd frontend && node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/activationPanel.test.mjs` | 本輪現測（該泳道不留 run 目錄，數字來自本次 stdout） | `# tests 15`／`# pass 15`／`# fail 0`／`# skipped 0` | **0** | `activationPanel.ts deab3aa3…`／`activationPanel.test.mjs c24a6da2…` | **PASS（離線型別層）**；瀏覽器 **NOT RUN** |
| 2b | 前端型別 gate（Brief 要求保留） | `cd frontend && npx tsc --noEmit` | `/tmp/r22-tsc.log` | 輸出 **0 行** | **0** | 當前端樹 | **PASS** |
| 2c | 既存前端泳道（揭露，不美化） | `node22 … --test tests/dailyEstimate.test.mjs` | `/tmp/r22-de.log` | `# tests 44`／`# pass 43`／**`# fail 1`** | **1** | 未改動 | **FAIL（本輪之前就紅，非本輪造成）** |
| 3 | B 準備實物（改前紅燈，原始物保留） | 同 runner，`tests/test_card_b_seed_prep.py` | `sqlite-20261122T043037Z-11518` | 18 collected／11 passed／**7 failed** | **1** | `adbbd8f6…`（before＝after） | **FAIL → 根因見 17.3(d)** |
| 3b | B 準備實物（本輪交貨） | 同 runner，`tests/test_card_b_seed_prep.py` | `sqlite-20261122T043137Z-11984` | 18 collected／**18 passed**、failures 0、errors 0、13.920 s | **0** | `d5093a28…`（before＝after） | **PASS（SQLite 隔離檔）**；**卡 B 整體仍 BLOCKED** |

⚠️ **本輪候選不是單一 digest**：1b、1c、3b 三格各自綁自己那支 run 的 `tree-digest-before == -after`，其中 `d5093a28…` 是本輪寫入完成後的最後一棵樹；`92d82b1b…`／`fecb6434…` 是 Item 1 完成時的樹（之後才寫 B 準備實物）。任何後續引用把四個值其中一個當成「第 22 輪唯一候選」都是錯的。第 21 輪的 `a45bcee9…` 仍是第 21 輪的證據，本輪未重釘；它對應的 51 格在 1b 的 173 內重驗（`tests/test_agent_strategy_slice.py` 為該 selection 的第一支檔案）。

### 17.1 項目一：一條 agent-model 紙上執行的最小完整路徑（實際 diff、正反控、持久讀回）

**（a）客戶操作對到的正式入口（本輪新增的兩格 route）**

* `POST /api/v1/agent-strategy/training/jobs/{job_id}/paper-execution`：`app/api/agent_strategy.py:444-447`，本體只有一句 `execute_paper_decision(...)`（`:466`）；`GET …/training/jobs/{job_id}/paper-executions`：`:483-484`、`:500`。路徑參數只有 `job_id`；`skill_unit_id` 是**查詢參數**且為必填（`:164` 的 description 写明它是「本人的技能帳號」），拒因→HTTP 對照表在 `:76-77`（`paper_execution_job_not_found`／`paper_execution_skill_unit_not_found`）。
* 服務：`app/services/agent_paper_execution.py:318 execute_paper_decision()`（698 行整檔）。帳本 model：`app/models/agent_paper_execution.py:66`，意圖唯一性 `:135-139 uq_agent_paper_execution_intent`。政策版本字串寫進每一列與每一則拒因：`agent_paper_execution.py:121 PAPER_EXECUTION_POLICY_VERSION = "agent-model-paper-execution-v1"`。

**（b）「不可把客戶端傳來的 checksum、批准 ID 或狀態當授權結果」——做在哪裡**

`execute_paper_decision` 的簽名只收 `job_id`／`skill_unit_id`／`actor_type`（`:318-325`）；**沒有** checksum、approval id、status、features 參數。所有授權事實由持久記錄解析：portfolio `:339` → job `:347` → scope 腿（讀 live approval 列本身）`:351` → approval `:365`（`app/services/model_activation.py:99 approval_for_job`）→ 本人同意 `:367` → 版本綁定 `:375` → 快照 `:387` → candidate `:397`。正控就是這條：驗收節點刻意送出 `job_id=<另一支隨機 uuid>`、`artifact_checksum="0"*64`、`approval_id=<隨機 uuid>`、`execution_status="executed"`、`features={"sma_short_3": 999.0}`（`tests/test_agent_paper_execution.py:354-364`），回應體內每一格身份仍由服務端讀出（`:386-397`），且機率≥門檻（`:398`）。

**（c）檢查順序：載入模型與任何紙上寫入之前**

1. 三道 kill switch（`:331-337`，`paper_execution_kill_switch_global`／`_inference`／`_execution`，`:139-141`）；
2. 帳號狀態 paused／retired／completed 各自具名（`:340-345`、`:130-132`）；
3. **scope 腿**：`check_skill_unit_activation`（`app/services/model_activation.py:137`）不授權 ⇒ `paper_execution_approval_scope_not_granted`，訊息明寫「job-only／NULL scope 不覆蓋任何帳號」（`:351-364`）；
4. 本人同意非 active ⇒ `paper_execution_owner_consent_not_active`（`:367-373`）；
5. 批准綁定的策略版本 ≠ job 的版本 ⇒ `paper_execution_version_mismatch`（`:375-383`）；
6. 既有意圖已存在 ⇒ **直接回原列**（`duplicate: True`），不重新評分、不寫第二筆（`:404-417`）；
7. 才呼叫真 loader：`run_inference`（`app/ml/agent_strategy_trainer.py:609`，joblib 載入＋對目錄位元組**現算** artifact checksum，見該檔 `:14`、`:575`）。载入後還有兩道覆核：門與 loader 讀到的必須是同一筆批准（`:424-433 approval_changed_midflight`）、載入所用的產物雜湊必須等於批准所綁定的（`:434-440 paper_execution_artifact_mismatch`）。
⇒ 批准判斷**沒有另起一套**：產物／資料集／契約三條綁定沿用 `model_activation.py:385-387` 已有的具名拒因（`approval_artifact_mismatch`／`approval_dataset_mismatch`／`approval_contract_mismatch`），本輪未改寫、未撤回任何既有 job-only 批准（Brief 前置核對項）。

**（d）18 支節點＝Brief 驗收條款的逐格對應**

正常路徑（正控）：`test_take_books_a_paper_trade_the_formal_reads_can_trace`。拒絕（反控，各自具名）：`test_no_approval_at_all_refuses_before_the_model_loads`、`test_job_only_approval_authorizes_inference_but_not_execution`、`test_approval_for_another_skill_unit_is_refused`、`test_second_job_cannot_borrow_the_first_jobs_approval`、`test_revoked_approval_refuses_and_the_recorded_execution_survives`、`test_approval_bound_to_another_strategy_version_is_refused`、`test_approval_that_no_longer_binds_the_artifact_is_refused[artifact_checksum|dataset_checksum|training_contract_version]`（3 個 parametrisation）、`test_paused_retired_and_completed_accounts_refuse_entries_by_name`、`test_each_kill_switch_refuses_under_its_own_name[global|inference|execution]`（3 格）、`test_withdrawn_consent_refuses_the_paper_lane`、`test_another_owner_names_neither_the_job_nor_the_account`、`test_model_decline_is_durable_and_books_nothing`。純規則不受影響：`test_direct_manual_order_still_books_a_trade`。

**（e）「持久讀回核對該次來源，不只驗 HTTP 2xx」——讀的是哪幾張表**

* 帳本列在**請求之外**用另一條 Session 讀回（`tests/test_agent_paper_execution.py:410-436`）：`decision_record_id`／`order_id`／`fill_id`／`approval_id`／`job_id`／`snapshot_id`／`strategy_version_id`／`candidate_id`／`artifact_checksum` 逐格對得起來，`actor_type == "human"`（人按的、機下單的兩件事分欄保存），`split_membership` 由快照自己的 `split_manifest` 反查而非本模組貼的標籤（`:428-440`）。
* 「沒有新增紙上決策、訂單及成交」由 `_book(portfolio_id)`（`:202-232`，另開一條 `TestSession()` 數 `DecisionRecord`／`PaperOrder`／`PaperFill`／`PaperPosition`）在每一支拒絕節點後斷言等於 `EMPTY_BOOK`（`:68`，`{"decisions":0,"orders":0,"fills":0,"positions":0}`）；實測出現於 `:607, 664, 721-722, 759, 828, 860, 932, 964, 995, 1041, 1079, 1118-1119`。
* 「正常重送不產生第二筆意圖」：同一意圖再送 ⇒ `again["duplicate"] is True` 且全庫盤點 `_census() == before`（`:552`、`:571`）；拒絕那側的第二次送同樣回 `duplicate: True`（`:614`）。

**（f）schema：只提交最小 model／migration 檔，未對任何 PG 執行**

新表 `agent_paper_executions` 的 `CREATE`／`DROP` 只寫在 `backend/migrations/versions/e5f6g7h8i9j0_add_agent_paper_executions.py`（190 行，`revision="e5f6g7h8i9j0"`、`down_revision="d4e5f6g7h8i9"`），檔內 `:46` 自述「This revision has not been executed against any PostgreSQL database」。本輪**未**跑 `alembic upgrade`、未起 PG；測試靠 `Base.metadata.create_all` 的隔離 SQLite。放了新尾 Revision 之後重驗尾部契約 ⇒ 1c 那格 15 passed／exit 0。

### 17.2 項目二：跨 job 競態、成功通知與「狀態未確認」分開（離線可執行，未起瀏覽器）

**實物**：`frontend/src/lib/activationPanel.ts`（201 行，純函式）＋ `frontend/tests/activationPanel.test.mjs`（511 行、15 節點）＋ `frontend/src/app/agent-strategy/page.tsx`（1759 行，接線）。

* 讀取守門（A 的結果不覆蓋 B）：面板只認「現在顯示的那支 job」。`activationReadRef` 存 `{jobId, seq}`（`page.tsx:534`），送呼前 `beginActivationRead`（`:544-545`）、落地前 `acceptActivationRead`（`:551-552`、`:564`、`:573`）、渲染前 `isDisplayedActivationJob`（`:538`、`:854`）；切換 job 時把 ref 重新釘在新 job（`:640-642`）。
* 通知只有一個來源：`activationWriteNotice(...)`（`activationPanel.ts:156`）— 批准／撤回的成功通知一律經 `verifyActivation`＋`verificationOf` 的「已讀回」結果（`page.tsx:895`、`:935`），被拒時走 `refused` 分支（`:883`、`:923`），因此「請求已提交但狀態未確認」與「409」都拿不到 ok tone；寫入意圖仍掛在按鈕上由 `activationIntentHeld`（`activationPanel.ts:120`）表達。
* 15 節點涵蓋：跨 job 迟到的 read 被丟棄、seq 過期不覆蓋確認文字、批准→讀回→撤回→讀回、409 不顯示成功、只提交未讀回顯示未確認、error 不冒充 ok。

**判定**：exit 0（本輪現測，15/15）；`npx tsc --noEmit` exit 0（輸出 0 行）。**瀏覽器 NOT RUN**——本輪未啟動 dev 棧、未點擊，依 Brief「瀏覽器仍是 NOT RUN，不能代本人批准」，因此 UI 在真實 DOM 上的行為**未證**。

**揭露（不得只引綠的）**：`frontend/tests/dailyEstimate.test.mjs:676`（節點 23「the shared vocabulary names exactly the lineage refusals the backend can answer」）**是紅的**，`# fail 1`／exit 1。根因：前端 `LINEAGE_REFUSAL_REASONS`（`frontend/src/lib/dailyEstimate.ts:314`）仍列 5 項，後端版本腿補齊後能回答 7 項，缺 `inference_version_mismatch`、`inference_version_unnamed`。mtime 證據：`dailyEstimate.ts` Nov 20 10:46、`dailyEstimate.test.mjs` Nov 20 21:46、`backend/app/services/decision_lineage.py` Nov 21 14:34 ⇒ **本輪之前就已漂移**，本輪未改動這三檔、未把它一起改綠（跨範圍）。修法屬下一輪決定：補前端那兩項，或把該測試改成「子集」契約。

### 17.3 項目三：B 換成可審的資料準備實物（有上限的種子＋預設不執行的寫入器）

**（a）實物**：`backend/scripts/card_b_seed.py`（827 行）＋ `backend/tests/test_card_b_seed_prep.py`（1034 行、18 節點）。內容＝已定的合成材料：`market_data_bars` 140 根日線 ×2 標的（`US:AAPL`／`US:MSFT`，`:91`）＝280、`market_data_metadata` 2、`simulation_sessions` 205、`human_decision_events` 205（`:101 TOTAL_DECISIONS = DECISION_DAYS * DECISIONS_PER_DAY`）、`decision_labels` 205 ⇒ `CAPS`（`:107`）總和 **897**（呼應 16.4 的更正，不是 692）。ID 由 `uuid5(SEED_NAMESPACE, …)` 決定，同一支 plan 重跑得到同一批列。

**（b）預設不執行、目標失配即停**：`--execute` 未給 ⇒ `write_seed` 回 `{"reason": "inert_by_default", "written": 0}`（`:601`）且測試證明盘面 0 列。七類停止全部具名，其中寫入停止（exit 4）由 `WRITE_STOP_REFUSALS`（`:127`）這個明確集合決定——不再用「程式碼裡有沒有 caps／already 字串」那種會自我成立的判斷：
1. `target_database_missing`（`:756 main()` 先 `os.path.exists` 擋掉——SQLite 連上去就會自己建档，拒因不能把它拒絕的東西製造出來；實測斷言 `assert not missing.exists()` 在 `tests/test_card_b_seed_prep.py:1019`）；
2. `target_database_mismatch`／`target_not_sqlite`／`target_has_no_database_file`（`target_state` `:332`、`:343`、`:345`；後兩者用**從不連線**的 `postgresql+psycopg2://` engine 與 duck-typed bind 做正控）；
3. `target_has_no_platform_schema`（`REQUIRED_TABLES` `:358`＋`require_target_schema` `:373`，8 張表缺一即停）；
4. `owner_not_in_target`／`strategy_version_not_in_target`／`strategy_version_not_locked`（`:430 require_owner_and_version`，三支各自具名，實測於 `tests/test_card_b_seed_prep.py:548, 552, 557`）；`id_not_a_uuid`（`:756` 的引數層，exit **2** 不混進 exit 3）；
5. `target_already_holds_seed_rows`（第二次 `--execute`；計數只數**種子自己的** uuid ⇒ `_seed_counts` `:533`，不會把別人那 205 筆算成種子的功勞）；
6. `target_holds_conflicting_rows`（`:384`，同 `(instrument, provider, bar_timestamp)` 的既有列會撞唯一約束 ⇒ 在 `_add` 之前停）；
7. `seed_exceeds_caps`／`target_exceeds_caps_after_write`／`seed_rows_read_back_short`／`seed_label_action_label_drift`（讀回與計畫不符 ⇒ `session.rollback()`，整批不留在庫裡）。

**（c）合成 vs 真人、以及 readiness 驗的是什麼**：合成標記一律 `actor_type="unknown"`＋每列 `metadata_json` 帶 `MARKER`（`:78`，含 `nature: "synthetic_prep_seed"`、`owner_confirmed: False`）；市場資料的出身寫在 schema 說得出那兩處（`provider_id="synthetic"`＋`provider_note` 含「not market history」，`data_nature`）。真人那筆用正式 `record_label` 寫路徑產生，測試量到的是 `{"human": 1, "unknown": 205}`——**分得開，不是宣稱分得開**。`consent_rows_written` 恆 0、`--execute` 也不寫 consent（`:738 natural_tick_observed: False`）；`readiness_view`（`:682`）以業務鍵 `(owner, skill_unit, instrument, window)` 回報 `unsettled_business_keys`（實測 2）、`quota.covers_unsettled_keys`（按**可花額度**判定，不是名義 `limit ≥ 2`；測試用 `limit-1` 讓 `nominal_limit_at_least_key_count is True` 而 `covers_unsettled_keys is False`，兩者分開）、`owner_consent_pressed`、`missing == ["no_active_daily_estimate_consent"]` 時 0 筆扣額。操作者入口只印 `{dialect, database}`，不印 URL／秘密；`--out` 才寫完整 rows。

**（d）本輪真實紅燈留帳**：`sqlite-20261122T043037Z-11518`＝**exit 1、18 collected／7 failed**（候選 `adbbd8f6…`）。根因不是產品語意而是寫入器自己的迴圈：`write_seed` 的 label 迴圈走的是 `plan["rows"]["human_decision_events"]`，卻要讀 label spec 的 `state`／`action_label` ⇒ `KeyError: 'state'`。修法是把迴圈改走 `plan["rows"]["decision_labels"]`、以 `spec["decision_event_id"]` 取回事件，並在 `record_label` 落地後比對 `action_label`，不符即 `seed_label_action_label_drift` 停。改後＝3b（exit 0、18/18）。⚠️ 該 run 的 `pytest.log` 在失敗 repr 裡寫入了測試帳號的 Bearer JWT，**不得摘錄**；本節只引用 junit 的套件層計數與 `identity.txt`。

**（e）卡 B 的狀態：仍 BLOCKED（依 Brief 三個獨立面不合併）**
1. **目標驗證未批**：本輪只在 runner 自建的隔離 SQLite 檔裡驗過；本人尚未批准對任何**指定目標檔**執行 `--execute`（那正是 17.3(b) 那道 `--execute` 開關留給本人的決定）。
2. **種子寫入未批**：本輪對**任何真實／常駐庫**寫入 0 列；897 列只存在於各 run 自己的暫存檔。
3. **本人同意未給**：本輪未寫 consent、未代按，readiness 的 `owner_consent_pressed` 在測試裡就是 `False`。
⇒ 「待批目標驗證、種子寫入和本人同意仍各自獨立」；本輪交的是**可以被審的那件東西**，不是 B 的通過。

### 17.4 本輪寫入清單與副作用揭露（行數／雜湊一律 `wc -l`＋`shasum -a 256` 現測）

| 路徑 | 動作 | 行 | sha256（前 8） |
|---|---|---|---|
| `backend/app/services/agent_paper_execution.py` | **新增** | 698 | `2172b94b` |
| `backend/app/models/agent_paper_execution.py` | **新增** | 189 | `e04e33e1` |
| `backend/tests/test_agent_paper_execution.py` | **新增** | 1151 | `d6425cfa` |
| `backend/migrations/versions/e5f6g7h8i9j0_add_agent_paper_executions.py` | **新增（放置，未執行）** | 190 | `284d6fbf` |
| `backend/app/api/agent_strategy.py` | 改（兩格 paper route＋拒因對照） | 674 | `dff90bd0` |
| `backend/app/models/__init__.py` | 改（登記新 model） | 273 | `4374a915` |
| `frontend/src/lib/activationPanel.ts` | **新增** | 201 | `deab3aa3` |
| `frontend/tests/activationPanel.test.mjs` | **新增** | 511 | `c24a6da2` |
| `frontend/src/app/agent-strategy/page.tsx` | 改（競態守門＋通知只認讀回） | 1759 | `9d18349b` |
| `backend/scripts/card_b_seed.py` | **新增** | 827 | `5ad4f8e2` |
| `backend/tests/test_card_b_seed_prep.py` | **新增** | 1034 | `0fd60493` |
| `EXECUTION_TABLE_20261117.md` | 追加本節 §17（放置，不執行） | 追加前 **6489**／`43f4a67f`；追加後請以 `wc -l` 現測（本節不自指自己的雜湊） | — |

**更正一處手抄清單**：上一段工作摘要把 `frontend/src/lib/api.ts` 列為本輪修改，**實測否決**——它的 sha256 `aca1fa74…`／1295 行與 §16.5 記的第 21 輪值完全相同 ⇒ 本輪未改動，歸屬 R21。

**本輪花了什麼**：只在四支 run 各自的暫存 SQLite（`alethra-sqlite-test-91487/92523/11518/11984/16847.db`）與 `/var/folders/.../alethra-runs/*` 產物；真 sklearn 擬合與 joblib artifact 寫在 runner 指定的暫存 `MODELS_DIR`。**本人帳號：0 筆寫入、0 張批准、0 份同意、0 列種子。**

**本輪沒有**：建庫、對任何 PG 執行 migration、啟動服務／瀏覽器／cron、外呼、讀或印秘密值、展開程序環境（未使用 `ps eww`）、重釘候選、clone/apply、部署、A／B／C／D 四張卡的執行、任何 **Git 寫入**（`git add`／`commit`／`push`／`tag` 均未執行；`live1-impl` 仍在 `5a34e3f`，工作樹髒、porcelain 236–240 路徑，故上表的 sha256 識別的是**當前位元組**，不是「本輪相對於 HEAD 的 delta」——本輪交付一律標 **LOCAL WRITTEN／NOT PUSHED**）。研究 8 家族全部 FAIL 的原有結論**原樣保留**，本輪未重跑、未改寫。

### 17.5 尚未驗證的客戶行為（逐條點名）

| 行為 | 本輪證據 | 還缺什麼 |
|---|---|---|
| 真人在瀏覽器上走完「選帳號→選 job→批准→紙上執行→看到該筆決策」 | **NOT RUN** | 起棧＋真點擊（卡 C 批准面）；本輪只有後端 SQLite 契約與離線型別 |
| 紙上路徑在 **PostgreSQL** 上的行為（含 `uq_agent_paper_execution_intent` 的真實唯一性、並發重送） | 未驗證 | 新表需先經批准跑 migration；本輪只 `create_all` 於 SQLite |
| 「job-only 批准不會被紙上路徑悄悄擴張」在**真實客戶資料**上的讀回 | 離線契约已證（節點 3） | 客戶環境的讀回屬卡 C／D |
| UI 在真實 DOM 上不顯示成功（409／讀回失敗） | 離線已證（15 節點）；DOM **NOT RUN** | browser spec 讀 NoticeBox tone |
| 對**指定目標檔**寫入 897 列種子 | **BLOCKED**（17.3(e)） | 本人批准目標＋`--execute`；本輪預設不執行已實測 |
| 每日估計的自然 tick | **BLOCKED**（16.3(d) 三缺口不變） | 本人 consent＋獲准啟動的棧 |
| `dailyEstimate.test.mjs:676` 前端拒因詞彙 | **FAIL（既存）** | 見 17.2 揭露，下一輪定方向 |
| 研究 8 家族（MB-13…19） | 原樣 **FAIL** | 依 Brief 不重跑、不改寫結論 |

## 18. 第 23 輪（2026-11-22）——①兩個版本腿拒因補齊（既存 FAIL 轉綠，未刪斷言）、②agent 紙上執行接到真正的客戶操作、③種子推過正式訓練門 ⇒ **B-正式訓練準備 BLOCKED 定案**、④失敗輸出不再帶出憑證＋卡片按新 tail 重釘

### 18.0 帳本先於內容：本輪每一格的確切操作、真實退出碼與判定

| # | 交付 | 確切操作 | run／產物 | 數字 | **exit** | 候選（樹 digest／檔 sha256） | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | 项目一：兩個版本腿拒因的前端契約 | `cd frontend && node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyEstimate.test.mjs` | stdout 存 `/tmp/r23-de.log`（該泳道不留 run 目錄） | `# tests 47`／`# pass 47`／`# fail 0`／`# skipped 0` | **0** | `dailyEstimate.ts 805cea19…`／`dailyEstimate.test.mjs 89c5df28…` | **PASS（離線型別層）**；對照 §17.2 那格的 `44 tests／# fail 1` ⇒ 本輪 **轉綠且節點 44→47** |
| 1b | 前端型別 gate（含新 e2e 檔） | `cd frontend && npx tsc --noEmit` | `/tmp/r23-tsc.log`、`/tmp/r23-tsc2.log`（中間隔一次 spec 修改，兩次都跑） | 輸出 **0 行**（兩次） | **0** | 當前端樹 | **PASS** |
| 2 | 項目二：紙上面板離線契約（新檔） | `node22 … --test tests/paperExecutionPanel.test.mjs` | `/tmp/r23-pep.log` | `# tests 23`／`# pass 23`／`# fail 0`／`# skipped 0` | **0** | `paperExecutionPanel.ts 0ad1c9e8…`／`paperExecutionPanel.test.mjs af6474de…` | **PASS（離線型別層）**；瀏覽器 **NOT RUN** |
| 2b | R22 那支未改的前端泳道重驗 | `node22 … --test tests/activationPanel.test.mjs` | `/tmp/r23-ap.log` | `# tests 15`／`# pass 15`／`# fail 0` | **0** | `activationPanel.ts deab3aa3…`／`activationPanel.test.mjs c24a6da2…`（**與 §17.0-2 同位元組**，本輪未改） | **PASS**；仍屬**同一份位元組的重驗**，不是新候選 |
| 3 | 項目三＋項目四：正式 SQLite 泳道（4 檔合併） | `cd backend && TEST_MODE=true ./scripts/run_tests.sh sqlite tests/test_failure_output_secret_hygiene.py tests/test_card_b_seed_prep.py tests/test_migration_tail_contract.py tests/test_agent_paper_execution.py -q` | `sqlite-20261122T121548Z-9707` | 59 collected／**59 passed**、failures 0、errors 0、skipped 0、**44.19 s** | **0** | `9fc9d7a457d2…`（before＝after）；該 run 的 `pytest.log` 以 `grep -Ec 'Bearer eyJ[A-Za-z0-9_-]{20}'` 計 **0** 行 | **PASS（SQLite）** |
| 3b | 直接受影響的 lane 基礎設施（8 檔） | 同 runner，`tests/test_failure_output_secret_hygiene.py tests/test_run_evidence_ledger.py tests/test_lane_entry_contract.py tests/test_api_fixture_lifecycle.py tests/test_fixture_failure_injection.py tests/test_api_login_with_fixture.py tests/test_process_state_isolation.py tests/test_postgresql_only_marks.py` | `sqlite-20261122T115903Z-95133` | 101 collected／**96 passed／1 failed／4 skipped**、198.40 s；skip 盤點 pg_only 3／live_stack 0／other 1；結尾 `evidence gate: ledger closes against this run's own artifacts` | **1** | `9fc9d7a4…`（before＝after） | **FAIL（唯一紅燈屬既存，非本輪造成，見 18.4(d)）** |
| 4 | 項目四：哨兵驗收（5 節點） | 已在 3 那格的 59 內（`test_failure_output_secret_hygiene.py`） | `sqlite-20261122T120359Z-951`（本輪單檔首測）＋ 3 那格重驗 | 5 collected／**5 passed**；單檔那次 exit 0 | **0** | `9fc9d7a4…` | **PASS**；接觸的都是**寫死在檔裡的無效哨兵**，未讀任何真實憑證 |
| 5 | 新 tail 的離線尾部事實 | `cd backend && .venv/bin/python scripts/check_migration_tail.py --json --with-alembic` | 本次 stdout（rc 由該腳本自己回） | `head=="e5f6g7h8i9j0"`、`parsed_heads==["e5f6g7h8i9j0"]`、`revision_count==module_count==58`、`expected_tail==parsed_tail==[b2c3d4e5f6g7, c3d4e5f6g7h8, d4e5f6g7h8i9, e5f6g7h8i9j0]`、`unreachable_from_head==[]`、`status=="OK"` | **0** | 當前 `migrations/versions/` | **PASS（純離線）**；**未對任何 PG 執行 upgrade** |
| 6 | 舊失敗輸出的暴露量化 | 只數不摘錄：逐 run `grep -Ec 'Bearer eyJ[A-Za-z0-9_-]{20}' pytest.log junit.xml` | `${TMPDIR}/alethra-runs/` 今日 **21** 支 run | 含憑證形狀行的 run＝**8**（兩檔同數：18、7、3、2、2、1、1、1）；修正後的 3 支 run（`…T115903Z-95133`、`…T120359Z-951`、`…T121548Z-9707`）皆 **0** | — | — | 洩漏**已止**（對新 run 實測 0）；舊 run 原檔**保留、不讀、不摘錄、不外帶** |

⚠️ **本輪的候選仍然不是單一 digest**：3、3b、4 三格共用 `9fc9d7a4…`（本輪後端寫入完成後的最後一棵 python 樹），但 1／1b／2／2b 綁的是前端檔 sha256（前端不在 python digest 內）。**§17 記的四個值（`92d82b1b…`／`fecb6434…`／`d5093a28…`／`adbbd8f6…`）與 R21 的 `a45bcee9…` 本輪未重釘，也不得與本輪的值拼成「同一發布候選已通過」。**

### 18.1 項目一：兩個版本腿拒因的可辨識呈現（修在共用 mapping，不刪斷言、不泛用化）

* **修的位置**：`frontend/src/lib/dailyEstimate.ts:314-322` 的 `LINEAGE_REFUSAL_REASONS` 補進 `inference_version_mismatch`、`inference_version_unnamed`（後端版本腿補齊後能回答 7 項，前端原列 5 項——§17.2 揭露的那道漂移）。文案在**同一檔** `:331-341` 的 `LINEAGE_REFUSAL_LABELS_ZH`：兩個拒因各給一句**處置相反**的中文（前者：來源出自另一個模型版本 ⇒ 改選目前版本的估計或取消勾選來源；後者：來源根本沒記錄版本、無法核對 ⇒ 改選有記錄版本的估計或取消勾選來源），檔內註解寫明「两句对客户读起来一样但处置相反」的風險。
* **兩道門共用同一份**：`src/components/workspace/OrderPreviewPanel.tsx:18`／`:542` 與 `src/app/experiments/[id]/simulation/page.tsx:16`／`:512` 都改走 `lineageRefusalOf(status, detail)`（`dailyEstimate.ts:355-372`）。**沒有**新增第二套字串。
* **普通 400 與未知碼仍走原 fallback（實測斷言，不是敘述）**：`lineageRefusalOf` 只在 `400 ≤ status < 500` **且** `detail.reason` 命中那個集合時才回 `LineageRefusal`（`:360-361`），否則 `null` ⇒ 呼叫端保留原本的錯誤呈現；有 reason 但本檔還沒句子的，保留**伺服端自己的句子＋reason code**（`:367-370` 註解），不退成泛用錯誤。測試把它釘成三節點：兩個拒因各自文案正確（`dailyEstimate.test.mjs:745-761`）、泛化詞不準出現（`:791` 那節斷言 `inference_version_mismatched` 這類近似碼**不會**被認成拒因）、集合與後端逐字對帳（`:735`）。
* **未做**：未刪任何契約斷言、未把 400 改判成泛用錯誤、未跑無變更的全套（只跑該檔 47 節點＋型別 gate）。

### 18.2 項目二：第一條真正可走的客戶路徑（逐節點對應到實物與行號）

**先讀schema 再接線**（Brief 前置）：`PUT/GET/revoke …/training/jobs/{job_id}/activation` 的讀回形状由 `app/services/model_activation.py:87-96`（`live_approval`）與 `:99-106`（`approval_for_job`）決定；`POST …/paper-execution`／`GET …/paper-executions` 的簽名只有路徑參數 `job_id`＋**必填查詢參數** `skill_unit_id`（`app/api/agent_strategy.py`，§17.1(a) 已釘）。前端因此**不送** checksum／approval id／狀態。

| 步 | 客戶動作 | 實物（file:line） | 失败時照實顯示什麼 |
|---|---|---|---|
| 1 | 開 `/agent-strategy`，面板出現 | `page.tsx:2078` `data-testid="paper-execution-panel"` | 讀不到啟用狀態 ⇒ `scope==='unreadable'`，句子「讀不到這份同意的綁定範圍。」（`paperExecutionPanel.ts:197`），**不自動降級成可執行** |
| 2 | 明選技能帳號與 job | `submitBlockedReason`（`paperExecutionPanel.ts:189-203`）：沒選 job／沒選帳號／讀不到 artifact checksum／`no_approval`／`unreadable`／**`job_only`**／綁的是另一個帳號 ⇒ 各自具名中文；UI 印在 `page.tsx:2111` `paper-block-reason`，`paper-execute`（`:2121`）保持不可送 | **job-only 永不顯示為「此帳號可紙上執行」**：範圍只由 `approvalScopeOf`（`:42-56`）從讀回紀錄算，`job_only` 那一句是「生效中的同意沒有綁任何技能帳號（job-only），此帳號不可據此執行。」（`:198`） |
| 3 | 看到實際 artifact 與批准範圍 | `approvalScopeNotice(activation.state, skillUnitId)`（`page.tsx:2103`；`paperExecutionPanel.ts:128`）＋ `approvalScopeSentence`（`:217-230`，明寫 NULL scope「不授權任何技能帳號做紙上執行」） | 範圍讀不回 ⇒ 同上 `unreadable` |
| 4 | 明確提交一次 **scoped** approval | 表單兩式 `ApprovalChoice`（`:205`）；選「綁定這個技能帳號」卻沒明選帳號 ⇒ `approvalScopeError`（`:207-214`）當場拒，`page.tsx:1076`／`:1092` 讓 **PUT 不發出** | 「這頁不會替你補一個。」 |
| 5 | 正式讀回確認 | 沿用 R22 的 read 守門與 `activationWriteNotice`（`activationPanel.ts:120`／`:156`，本輪未改） | 409／讀回失敗 ⇒ 拿不到 ok tone（§17.2 那 15 節點重驗，2b 那格） |
| 6 | 明確提交一次 paper execution | `api.agentStrategy.paperExecution(jobId, {skill_unit_id})`（`api.ts:1181-1186`）；**只送 skill_unit_id** | 具名拒因走 `paperRefusalNotice`（`paperExecutionPanel.ts:269-290`）＋ `DISPOSAL_ZH`（`:241-249`）；**無 code ⇒ 標成「結果不明」，不重送**（`page.tsx:1204-1210`） |
| 7 | 用正式 GET 顯示該次 ledger／decision／order／fill | `api.agentStrategy.paperExecutions(jobId, skillUnitId?)`（`api.ts:1187-1192`）→ `loadPaperLedger` → `paperReadbackNotice(pressed, read)`（`paperExecutionPanel.ts:374-416`，`page.tsx:1222-1231`）；`executionOutcomeZH`（`:321`）／`tradeLinksZH`（`:335`）照實拼「有决策無委託」「有委託無成交」 | **不按本地推算**：註解寫死「the outcome shown here is the row the formal GET returned — never a status inferred from the press」（`page.tsx:1220-1221`）；無列時 `paper-ledger-empty`（`:2141`） |
| 8 | 切 job／skill／artifact | `paperContextKey`（`:64-72`）＋ `beginPaperRead`／`acceptPaperRead`／`isDisplayedPaperContext`（`:95`／`:109`／`:100`）套在 `page.tsx:711-742`（讀）、`:842-843`（切上下文重新釘 seq）、`:1179-1181`、`:1190`、`:1220` | 舊審閱作廢、**在途結果不得覆蓋新上下文**（每一步都先問「還是畫面上那個 triple 嗎」，不是就丟棄） |
| 9 | 結果不確定時 | 先 `loadPaperLedger(...)` **查原意圖**再回報（`page.tsx:1222-1231`），**不另產生新 key 重送** | 「這一筆紙上執行沒有可辨識的回報，結果不明。」＋讀回結果同一句 |

**job-only 与 scoped 能否共存（用正式入口釐清，不是猜）**：`live_approval`（`model_activation.py:87-96`）只按 `job_id`＋`decision=="approved"` 過濾、**不看 scope**；`record_activation_approval:534-537` 正是用它擋下第二份 ⇒ 既有約束**不容共存**，第二次提交會被具名拒絕 `agent_activation_already_approved`（「此產物已有生效中的啟用同意；如需換成別份，先撤回。」）。UI 如實顯示衝突與**既有合法處置**：該碼在 `PAPER_SCOPE_REFUSAL_CODES`（`paperExecutionPanel.ts:231-237`）內，`DISPOSAL_ZH` 給「同一個 job 只能有一份生效同意。要改綁定範圍，先由本人撤回現存的，再提交新的。」（`:246-248`）——**不靜默撤回、不替換、不擴權**（撤回與批准都只由本人按）。⇒ **不需要新批准語意，本條不標 BLOCKED**，scoped 正控與讀回用現有契約完成。

**驗證與未驗證**：離線 23 節點（`tests/paperExecutionPanel.test.mjs`，845 行）涵蓋 scope 四態、表单拒、按鈕可送性與句子與門三處一致、ledger 過濾讀回、重送不產生第二列、未覆蓋帳號發不出請求、error 不冒充 ok。`npx tsc --noEmit` exit 0。**瀏覽器 NOT RUN**：本輪寫了 `frontend/e2e/agent-approval-paper-journey.spec.ts`（818 行、READ 6＋WRITE 4 節點，`:356`／`:598`）**但未啟動**，且依 15.5 既有決定未做 `--list` 探測。該旅程已**獨立列入卡 C**（新格 `C-agent-journey`，見 18.5），C 舊的兩支 spec 結果不替它背書。

**保留的原契約**：本輪後端**只動 `tests/`**（18.6 表可核），規則 engine 的正控 `test_direct_manual_order_stills_books_a_trade` 在 3 那格 59 內照常綠；personal 推論那條路（`run_inference`／`decision_lineage`）本輪未改一欄，其 §17 證據原樣。

### 18.3 項目三：種子輸出真的推過正式 eligibility／snapshot 門 ⇒ **B-正式訓練準備＝BLOCKED**

* **實物**：`backend/tests/test_card_b_seed_prep.py`（1,329 行、**21** 節點＝R22 的 18＋本輪 3）。種子本身（`backend/scripts/card_b_seed.py`）本輪未改（sha `5ad4f8e2…` 與 §17.4 同）。
* **三道節點，逐條對應 Brief**：
  1. `test_the_eligibility_door_counts_no_seed_row_as_a_human_confirmation`（`:1096`）：走**正式**資料集资格讀回，斷言 `dataset["excluded"]["unverified_provenance"] == 205`（`:1120`）且**其他排除腿皆 0**（`:1123-1124`）⇒ 205 筆 `actor_type="unknown"` 一筆都不算真人，排除**原因**與**計數**都被保存。
  2. `test_the_snapshot_door_refuses_the_seed_before_and_after_the_owners_consent`（`:1157`）：`POST /api/v1/ml/personal/snapshots` 在本人按下**訓練**同意**前**與**後**都回同一個 `exclusion_breakdown == {"unverified_provenance": 205}`（`:1182`、`:1211`），並比對具名中文排除標籤（`:1184`）⇒ **本人同意不會把合成列升格為真人確認**，snapshot 照實拒絕、不寫列。
  3. `test_only_human_provenance_rows_open_the_door_and_the_seed_stays_excluded`（`:1251`）：正控——用正式 `record_label` 寫路徑產生**真人**列後門才開；同一時刻種子那 205 筆仍被讀成 `unverified_provenance: 205`（`:1285`、`:1297`、`:1305`，`:1312` 斷言**快照列自己存進 `exclusion_breakdown_json` 的就是這個數**）⇒ 排除不是回應體裡的修辭，是可读回的持久欄。
* **判定**：**B-正式訓練準備＝BLOCKED**（不能產生訓練 snapshot 的原因是**具名、可讀回**的，本輪未以改標 human、直寫 consent 或繞過訓練門把它做成綠燈）。readiness 那格（§17.3(c)）**不**算這裡的證據：它量的是「兩鍵能否結算＋額度是否夠」，與「列的出身是否真人」是兩件事——本輪沒有拿前者替後者背書。
* **六段分離（已寫進 15.4 的「狀態（第 23 輪補）」）**：種子工具（已交、SQLite 綠）／正式訓練與 artifact（**BLOCKED**）／本人每日同意（未給，0 列寫入）／兩鍵可結算 readiness（NOT RUN）／自然 tick（BLOCKED，§16.3(d) 三不變）／可審的替代輸入（**身份**＝本人帳號經 `POST /api/v1/simulations/{session_id}/manual-decision` 產生、`actor_type="human"` 的 `HumanDecisionEvent`；**來源**＝本人在螢幕前的操作記錄，不是腳本代按；**批准**＝15.4(a) 那「第三個批准面」205 次真提交）。**任何一項不替代另一項。**

### 18.4 項目四：失敗輸出不再帶出憑證（機制現讀＋哨兵驗收＋暴露量化）

**（a）洩漏機制（現讀，不是推測）**：pytest 8.3.3 對每支**失敗**節點預設印出「該節點所要求的每個 fixture 值」的 repr，而 `auth_headers` 這類 fixture holds `{'Authorization': 'Bearer <jwt>'}`；lane 用 `tee` 把 stdout 寫進 `pytest.log`，另加 `-rfEs --junitxml=…`（`scripts/run_tests.sh:477-478`）⇒ 同一值**同時**進 log 與 junit。§17.3(d) 那支紅燈 run 走的就是這條路徑。

**（b）三層修法（只動必要診斷）**：
1. `SecretHeaders`（`backend/tests/conftest.py:404-423`）改寫 `__repr__`／`__str__` ⇒ 只洩露鍵名，值不外露；`auth_headers`（`:442`）與 `admin_headers`（`:469`）都回它，**鍵值本體仍是同一個 dict**，授權行為不變。
2. `backend/tests/failure_output_hygiene.py`（100 行，不匯入 app）提供 `pytest_runtest_makereport` hookwrapper：對 `report.failed` 的 longrepr chain／`reprtraceback.reprentries[].lines`／`reprfuncargs.args`／`reprlocals.lines`／`reprcrash.message`／`report.sections` 做**三條**具名替換（`Bearer <redacted-credential>`、`<redacted-jwt>`、`alk_<redacted-key>`），只吃憑證形狀，其餘診斷原字串保留。故意**不加 try/except**：pytest 內部若改形，這支要**大聲失敗**（子 exit ≠ 1），而不是悄悄停止遮罩。
3. **跳過報告完全不動**（`if not report.failed: return`），使 `scripts/skip_budget.py` 的跳過盤點維持逐位元組相同——本輪的 8 檔與 4 檔 run 都照常跑過 skip 統計。
註冊方式＝`conftest.py` 以 import 引入該 hook（pytest 從 conftest 命名空間收集 hook 實作），並在測試裡用 `config.pluginmanager.hook.pytest_runtest_makereport.get_hookimpls()` 斷言**恰好一支**該實作，防止「改了檔但沒掛上」。

**（c）驗收只用無效哨兵，不接觸原值**：`backend/tests/test_failure_output_secret_hygiene.py`（183 行、5 節點）——(1) 本 lane 確有掛 hook；(2) `SecretHeaders` 仍舊授權：`repr()` 不含值、`len(dict(...)) == 1`、`{**headers, "X-Trace": "1"}` 可展開、且 `client.get("/api/auth/me", headers=...)` 回 **200**（證明遮罩沒把授權弄壞）；(3) 三種憑證形狀各自被替換且非憑證文字不動；(4) 正控：起一支**帶 hook** 的子 pytest（同一組 `-rfEs --junitxml`），斷言 console 與 junit **都不含**哨兵字串，但仍留有 `AssertionError`、節點名、跳過理由與被遮罩的佔位行（⇒「只保留必要診斷」是可測的，不是口號）；(5) 反控：同樣的子 pytest **不帶** hook ⇒ 未遮罩原行與哨兵都看得见。

**（d）暴露量化與處置（不讀、不摘錄、不複製舊值）**：今日 **21** 支 lane run 中 **8** 支的 `pytest.log` 與 `junit.xml` 含 `Bearer eyJ…` 形狀的行（成對計數 18／7／3／2／2／1／1／1），其中含本輪自己後段兩支（`sqlite-20261122T114108Z-79754`、`…T114248Z-81146`）與 §17.3(d) 點名的 `…T043037Z-11518`；**修正之後**的三支 run 全部 **0**。處置：那些舊檔留在 `${TMPDIR}/alethra-runs/<run>/`（受限位置）**原樣不動**，本輪**未讀取其內容、未摘錄、未刪**（刪不刪屬本人裁決）；**不進卡 D**（15.6(1) 的排除名單已含「任何 `*.log`」）、**不進 GitHub**（本輪零 Git 寫入）。3b 那格的唯一紅燈是**既存**的 `tests/test_process_state_isolation.py::test_the_environment_the_suite_measures_is_the_environment_it_documents`（offender `tests/test_conftest_sqlite_default.py:175`；該檔 mtime 2026-11-20 16:20、門檔 2026-11-17 00:23，本輪兩檔都未動）⇒ **本輪之前就紅，未順手改綠**，方向交下一輪決定（補 `_env_file=None` 會抽空該測試的目的；放白名單等於鬆閘）。

**（e）仍未覆蓋的 sink（點名，不美化）**：hook 改的是**報表**（longrepr＋sections），**不攔 captured stdout**——若哪天有測試自己 `print(token)` 或把 token 拼進 log message，那条仍會進 log；本輪 5 節點沒有防它。另外 `api_test_fixtures.py:93` 那個值綁在測試區域變數，預設旗標下**不會**被印（只有 `--showlocals` 會），那條屬預防性修改，**不是**本次觀察到的洩漏面。

### 18.5 項目四（續）：新 tail 已改變待批 schema ⇒ 卡片就地更正（**未執行 upgrade**）

本輪只改**有效卡片正文**（§15.3／§15.4／§15.5），舊輪次的歷史紀錄原樣保留（§13／§14／14.x 那些 `d4e5f6g7h8i9`／57 引用是當時的事實，不改寫）：

* **15.3 A-3**：頭釘成現測 `e5f6g7h8i9j0`／`revision_count == module_count == 58`／四支尾部鏈，並把原 `d4e5f6g7h8i9`／57 標為**過時目標、從本卡移除**；同時撤回原句「本輪未新增 migration」；base 未重測就**不列入判準**。
* **15.3 新增 A-Schema-2**：新表 `agent_paper_executions` 的**讀回判準**逐條寫死——表存在、**10 條 FK 以 (欄位, 被參照表) 配對**核對（不靠 PG 自動命名）、`uq_agent_paper_execution_intent` 恰覆蓋 3 欄、3 支索引逐名可見、`decision_record_id`／`order_id`／`fill_id` 可空而其他身份欄 NOT NULL、`risk_check_json == jsonb`、`created_at` **NOT NULL 且 PG 端無 server default**（讀到預設值即代表 migration 與 model **不同形** ⇒ FAIL）。
* **15.3 新增 A-併發-2**：沿**實際交易責任**核對兩個邊界，再把缺失驗證列成待批 PG 節點：①同一意圖兩筆並發——第 1 層是 `agent_paper_execution.py:404-417` 的**無鎖** SELECT（只擋已 commit 的重送），第 2 層才是 DB 唯一約束＋ `_commit_ledger:661-680` 的 `IntegrityError`→rollback→按業務鍵重讀→`concurrent_insert_collapsed`（重讀空 ⇒ `:673-674` 原樣 raise）；實測**該旗標在全庫只有兩個寫入點、0 支測試節點聲稱它** ⇒ SQLite 的正常重送**不算**這裡的證據。②撤回與提交競態——批准寫入端有 job 行鎖（`model_activation.py:492-496`）、撤回端有 approval 行鎖（`:573-580`），但紙上路徑**讀**批准走無鎖 `live_approval`（`:87-96`），服務只做兩次讀之間的覆核（`:424-433`）⇒ 如實寫出殘留窗口（撤回落在 `:424` 之後、`:663` commit 之前會留下指向已撤回列的帳本，FK／唯一鍵都不擋），並定義**兩種可接受結果**與判 FAIL 的條件。兩支都必須掛 `postgresql_only`。
* **15.3 副作用清單**：57 支 → **58** 支，並標明第 58 支是**新副作用**（會 CREATE TABLE），不是唯讀。
* **15.5 C-DDL**：head 改釘 `e5f6g7h8i9j0`，移除過時的 `d4e5f6g7h8i9` 與「未新增 migration」那句，並加註「批 C-new ＝在這支 dev 庫上第一次真實建出 `agent_paper_executions`，該事實不從卡 A（未執行）也不從 SQLite `create_all` 繼承」。
* **15.5 新增第五格 `C-agent-journey`**：把 agent 批准＋紙上執行的瀏覽器旅程**獨立列入待批准範圍**（實物、命令、兩個只能本人給的引數、READ 只需 C-resume／WRITE 還需本人親手按同意、證據層級只到「型別可編譯」、瀏覽器 NOT RUN）。⚠️ 本輪同時修掉自己寫的一格**佔位符缺陷**：WRITE group 的 describe 級跳過原為無條件 `test.skip(true, WRITE_SKIP)`（設了環境變數也永遠不會跑），現測 `:602` 改為 `test.skip(WRITING_SKILL === '' || TARGET_JOB === '', WRITE_SKIP)`，與同目錄 `daily-estimate-journey.spec.ts:536` 既有寫法同形；**預設（兩變數未設）仍是整組跳過，未放寬任何閘門**。
* **15.4 新增六段分離狀態**（見 18.3）。
* **未做**：本輪**未**執行 `alembic upgrade`、未起 PG、未建庫；18.0-5 那格是**離線** ScriptDirectory 事實。**migration 尾部契約不代替 PG 讀回／併發判準**——這句話現在也寫進卡 A 正文（「不可互替」條）。

### 18.6 本輪寫入清單與副作用揭露（行數／雜湊一律 `wc -l`＋`shasum -a 256` 現測）

| 路徑 | 動作 | 行 | sha256（前 8） |
|---|---|---|---|
| `frontend/src/lib/dailyEstimate.ts` | 改（拒因集合 5→7＋兩句文案） | 403 | `805cea19` |
| `frontend/tests/dailyEstimate.test.mjs` | 改（三節點：兩句文案＋泛化不認＋與後端對帳；未刪斷言） | 1,331 | `89c5df28` |
| `frontend/src/components/workspace/OrderPreviewPanel.tsx` | 改（改讀共用 `lineageRefusalOf`） | 1,486 | `2c71031f` |
| `frontend/src/app/experiments/[id]/simulation/page.tsx` | 改（同上，第二道門共用同一份 mapping） | 1,579 | `bbfa5a7a` |
| `frontend/src/lib/api.ts` | 改（`paperExecution`／`paperExecutions` 兩個正式入口 client） | 1,310 | `81b28ee4` |
| `frontend/src/lib/paperExecutionPanel.ts` | **新增**（scope 判讀／送出前擋關／拒因處置／讀回通知） | 416 | `0ad1c9e8` |
| `frontend/tests/paperExecutionPanel.test.mjs` | **新增**（23 節點，離線） | 845 | `af6474de` |
| `frontend/src/app/agent-strategy/page.tsx` | 改（接線：三處擋關、一次送出、正式 GET 讀回、上下文守門） | 2,239 | `c60f0af4` |
| `frontend/e2e/agent-approval-paper-journey.spec.ts` | **新增（只撰寫，未執行）** | 818 | `65e39b34` |
| `backend/tests/test_card_b_seed_prep.py` | 改（+3 節點推正式門；18→21） | 1,329 | `733504b8` |
| `backend/tests/failure_output_hygiene.py` | **新增**（報表級遮罩 hook） | 100 | `a7d7c7e9` |
| `backend/tests/test_failure_output_secret_hygiene.py` | **新增**（5 節點，純哨兵） | 183 | `40cec800` |
| `backend/tests/conftest.py` | 改（`SecretHeaders` 兩個 fixture＋hook 註冊） | 474 | `6905756c` |
| `backend/tests/api_test_fixtures.py` | 改（同一 `SecretHeaders`，預防性） | 542 | `1e0e9dbe` |
| `EXECUTION_TABLE_20261117.md` | 改 §15.3／§15.4／§15.5（卡片就地更正）＋追加本節 §18 | 追加前本節寫入後為 **6,651**／`3dba032a`（`wc -l`＋`shasum -a 256` 現測）；追加後請以 `wc -l` 現測，**本節不自指自己的雜湊** | — |

**本輪沒有改動**：`backend/app/**`（包含 `agent_paper_execution.py`、`model_activation.py`、`api/agent_strategy.py`）、`backend/migrations/**`、`backend/scripts/card_b_seed.py`、`frontend/src/lib/activationPanel.ts`、`frontend/tests/activationPanel.test.mjs`——全部以 sha256 對比 §17.4（`deab3aa3`／`c24a6da2` 相同；card_b_seed `5ad4f8e2` 相同）而非憑記憶。

**本輪花了什麼**：只在三支 lane run 各自的暫存 SQLite（`alethra-sqlite-test-95133/9707/951…db`）與 `${TMPDIR}/alethra-runs/*` 產物；另外寫了 `/tmp/r23-*.log` 五份前端泳道 stdout。**本人帳號：0 筆寫入、0 張批准、0 份同意、0 列種子、0 次紙上執行。**

**本輪沒有**：建庫、對任何 PG 執行 migration、啟動服務／瀏覽器／cron、啟動 Playwright（含 `--list` 探測）、外呼、讀或印秘密值（含舊 `pytest.log` 裡的憑證本體）、展開程序環境、重釘候選（`tests/collected_nodes.txt` 仍釘 **3,811**、`tests/skip_budget.json` 仍釘 `collected: 3811`／pg_only 159／live_stack 18／other 2，而本輪後端**淨新增 8 節點**＝hygiene 5＋種子門 3 ⇒ 整套重新釘選**未執行**，屬 disclosed 缺口）、clone/apply、部署、A／B／C／D 四張卡的執行、任何 **Git 寫入**（`git add`／`commit`／`push`／`tag` 均未執行；`live1-impl` 仍在 `5a34e3f`，porcelain **245** 路徑／sha `12685f14…`，故上表 sha256 識別的是**當前位元組**而非「本輪相對 HEAD 的 delta」）⇒ 本輪交付一律 **LOCAL WRITTEN／NOT PUSHED**。研究 8 家族全部 FAIL 的原結論**原樣保留**，本輪未重跑、未改寫。

### 18.7 哪些證據仍屬舊候選（不得拼成「同一發布候選已通過」）

| 證據 | 所屬候選 | 本輪狀態 |
|---|---|---|
| 紙上路徑 **18 passed**（§17.0-1） | `92d82b1b…` | 本輪**未重釘**；18 節點在 3 那格（`9fc9d7a4…`）內重跑且綠，但那是**另一棵樹**上的新證據，兩句各自成立，不能疊成「同一候選两次通過」 |
| 相鄰 10 檔 **172 passed＋1 skipped**（§17.0-1b） | `fecb6434…` | 本輪**未重跑該 selection**；本輪只跑 8 檔（`9fc9d7a4…`）與 4 檔（同） ⇒ 該 172 仍是 R22 的證據 |
| 尾部契約 **15/15**（§17.0-1c） | `d5093a28…` | 本輪在同名檔上重驗（在 59 內，`9fc9d7a4…`）＋離線 `check_migration_tail.py` rc 0（18.0-5） |
| B 種子 **18/18**（§17.0-3b）與它的 **7 failed**（§17.0-3） | `d5093a28…`／`adbbd8f6…` | 本輪改成 **21 節點**（+3 正式門節點）⇒ 那兩格屬**舊位元組**，不再代表當前 `test_card_b_seed_prep.py` |
| UI 跨 job 競態 **15/15**（§17.0-2） | `activationPanel.ts deab3aa3…`／`test c24a6da2…` | **檔未改**，本輪為同位元組重驗（2b） |
| R21 那 51 格 | `a45bcee9…` | 本輪未重跑、未重釘 |
| `dailyEstimate.test.mjs` 的 **`# fail 1`**（§17.2 揭露） | 未改動時的位元組 | **已被 1 那格取代**：本輪該檔改動後為 47/47、exit 0；那句「既存紅燈」的陳述自此**過時**（歷史紀錄 §17.2 原樣保留） |

### 18.8 尚未驗證的客戶行為（逐條點名）

| 行為 | 本輪證據 | 還缺什麼 |
|---|---|---|
| 本人在瀏覽器上走完「明選帳號＋job → 看到 artifact 與範圍 → 提交 scoped 同意 → 讀回 → 一次紙上執行 → 正式 GET 看到 ledger／decision／order／fill」 | 離線 23 節點＋型別 gate 0；spec 已寫 | 起棧＋真點擊（**C-agent-journey** 那一格）；WRITE group 還需本人親自給兩個引數並先按一次同意 |
| job-only 同意與 scoped 同意**共存**時的 UI 衝突呈現（`agent_activation_already_approved`） | 後端拒因現讀（`model_activation.py:534-537`）＋前端處置字串（`paperExecutionPanel.ts:246-248`） | 真實 DOM 上那次衝突尚未跑過（NOT RUN） |
| 撤回後**再提交新意圖**與**重送已結算意圖**的差別，在 **PostgreSQL** 上的行為 | SQLite：`:404-417` 順序路徑已證 | 18.5 A-併發-2 那兩支待批 PG 節點（含 `concurrent_insert_collapsed` 首次被真正執行） |
| 撤回與提交競態的持久後果 | 讀源已核對（鎖在哪一层、`live_approval` 無鎖） | 同上，PG 上的讀回節點；SQLite 行鎖不存在，跳過理由是同一條 |
| 失敗輸出在所有路徑都不帶憑證 | 5 節點（含正控＋反控的子 pytest）；今日新 run 實測 0 行 | captured stdout 未覆蓋（18.4(e)）；`--showlocals` 之外的自印值仍需產品層防範 |
| 全套節點盤點與本輪 8 支新節點一致 | 未做 | 一次完整 SQLite 全套重釘（`collected_nodes.txt`／`skip_budget.json` 目前仍 3,811） |
| B 的正式訓練 snapshot | 三道門的排除**已具名實測** | 真人血统的 205 筆（本人操作）＋獲准的目標庫；在那之前 **B-正式訓練準備 BLOCKED** |
| 每日估計的自然 tick | **BLOCKED**（§16.3(d) 三缺口不變） | 本人 consent＋獲准啟動的棧 |
| 研究 8 家族（MB-13…19） | 原樣 **FAIL** | 依 Brief 不重跑、不改寫結論 |

## 19. 第 24 輪（2026-11-23 03:0x–03:3x UTC＝11:0x–11:3x HKT）——①後端唯一紅燈收斂（把「拼寫普查」換成可測的行為不變式，閘門**變嚴**不是變鬆）、②撤回→再批准補上持久讀回的直接回歸、③409 的**兩種含義**分開驗（已持久化的 blocked 帳本 vs 零寫入的前置拒絕）、④ORM 兩側差集現測為 0（**不等於 schema 已驗**）

### 19.0 帳本先於內容：本輪每一格的确切操作、真實退出碼與判定

每格的 **exit 讀該 run 目錄的 `exit-code.txt`**、**計數讀同目錄 `junit.xml` 的 `testsuite` 屬性**（本輪逐一重測，未抄 §18）。runner 那句 `evidence gate: ledger closes against this run's own artifacts` 由 `backend/scripts/run_evidence.py:482` 印在 **runner 的 stdout**，**不寫進 run 目錄** ⇒ 本節不宣稱它在產物裡，改以 `collect.txt`＋`junit.xml`＋`exit-code.txt` 三件自洽為準。

| # | 交付 | 確切操作 | run（`${TMPDIR}/alethra-runs/`） | 數字（現測） | **exit** | 候選 tree-digest | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | 項目一：失敗節點**單跑** | `cd backend && TEST_MODE=true ./scripts/run_tests.sh sqlite tests/test_process_state_isolation.py -q` | `sqlite-20261123T030357Z-66891` | 7 collected／**1 failed／6 passed**／errors 0、75.50 s；FAILED＝`test_the_environment_the_suite_measures_is_the_environment_it_documents` | **1** | `9fc9d7a457d2…`（before＝after） | **FAIL 可重現**（與 §18.0-3b 同一節點、同一棵樹） |
| 2 | 項目一：**最小相鄰選集**（污染假設） | 同 1，加 `tests/test_conftest_sqlite_default.py` | `sqlite-20261123T030832Z-69275` | 27 collected／**1 failed／26 passed**、84.63 s；同一支 FAILED、判語逐字相同 | **1** | `9fc9d7a4…` | **不是污染**：單跑與最小組都紅 ⇒ Brief 的第二分支（「只在一起跑失敗」）不成立，走第一分支「定位實際狀態差異」（19.1） |
| 3 | 項目一：修正後**同選集**重跑 | 同 2 的命令 | `sqlite-20261123T031122Z-73079` | 27 collected／**27 passed**、84.85 s | **0** | `4d91c4c77341…` | **PASS**（中間候選：只換了閘門那一檔） |
| 4 | 項目三：paper 檔在中間候選的獨立量測 | `… sqlite tests/test_agent_paper_execution.py -q` | `sqlite-20261123T032901Z-92195` | 22 collected／21 passed／**1 skipped**、23.44 s | **0** | `5611017442b6…` | **PASS**；該格之後又補了 `reason_code` 斷言 ⇒ 屬**舊位元組**，最終以 6 那格為準 |
| 5 | 項目一：最終候選上的最小組（Brief「修正後在同一候選重跑最小組」） | 同 2 的命令 | `sqlite-20261123T033031Z-94021` | 27 collected／**27 passed**、91.98 s | **0** | `7f2f8bf2f619…`（before＝after） | **PASS** |
| 6 | 項目二＋三＋四：最終候選合併泳道 | `… sqlite tests/test_agent_paper_execution.py tests/test_conftest_sqlite_default.py tests/test_process_state_isolation.py tests/test_failure_output_secret_hygiene.py -q` | `sqlite-20261123T033343Z-99188` | 54 collected／**53 passed／0 failed／0 errors／1 skipped**、109.96 s；skip＝`PG-ONLY [tests/test_agent_paper_execution.py:1428]` | **0** | `7f2f8bf2…` | **PASS（SQLite）**；那 1 skipped 是**未執行**，不是通過 |
| 7 | 項目一：該閘在最終候選**單獨**跑 | `… sqlite tests/test_process_state_isolation.py::test_the_environment_the_suite_measures_is_the_environment_it_documents -q` | `sqlite-20261123T033626Z-3417` | 1 collected／**1 passed**、1.31 s | **0** | `7f2f8bf2…` | **PASS** |
| 8 | 項目二/三的 schema 兩側現測 | `cd backend && PYTHONPATH="$PWD" .venv/bin/python /tmp/r24_schema_split.py`（樹外唯讀掃描，不寫 repo） | 本次 stdout | ORM：`32` 欄／PK `['id']`／FK `10`／unique `uq_agent_paper_execution_intent=[skill_unit_id, job_id, candidate_id]`／索引 `owner→owner_id`、`job→job_id`、`skill→skill_unit_id`／`created_at` server default `False`。migration 側同。**4 條差集全部 `[]`** | **0** | `7f2f8bf2…` | **兩側同形（靜態核對）**；**不是** PG 上真實建表的讀回（19.4） |

⚠️ **本輪的最終候選是單一 digest `7f2f8bf2f619e14496859c7eef62af87fdb75b4d4cf232374a1db7d72bc0c9de`**（5／6／7 三格共用，寫入後未再動一欄；1–4 屬修正前／中間）。尺的根與排除（照 `run_tests.sh:373-378` 的 `tree_digest()`）：`backend/**/*.py` 減 `.venv/`、`venv/`、`__pycache__/`、`node_modules/`，本輪現測 **545** 支檔。前端不在這把尺內，本輪也未動前端。

### 19.1 項目一：實際狀態差異——紅燈的根因是**閘門拿拼寫當前後環境的事實**

* **定位（不用 mtime 判定責任）**：1 與 2 兩格證明與執行順序無關；判語只點名一個 offender。`…T030832Z-69275/pytest.log:49-51` 原文：`AssertionError: test code reading the developer's backend/.env as if it were the product default:` ／ `tests/test_conftest_sqlite_default.py:175: Settings(...) without _env_file=None`。
* **兩件都是真的事在打架**：`tests/test_conftest_sqlite_default.py:175` 的 `Settings()` **故意裸寫**——它的目的正是證明行程級中性化（`tests/conftest.py:98-99` 把 `Settings.model_config["env_file"]` 改成 `None`）確實生效；在該節點上加 `_env_file=None` 等於把待證的結論寫進前提。舊閘門用 AST 普查「誰沒寫 `_env_file=None`」來代理「讀了開發者的 `backend/.env`」，於是**把一個真陳述判成紅**。依本專案既有決定（會因真話而擋的閘門，其綠不可讀成綠 ⇒ 降級那把尺，**不是**放行名單歸零），本輪改的是尺。
* **改後的尺**（`tests/test_process_state_isolation.py:241-361`）：
  1. `DOTENV_REACHABLE`（`:241-246`）＝ dotenv 能觸達的 4 個欄位；`_pinned_dotenv_fields()`（`:249-257`）先問「本機環境變數是否已蓋住這些欄」，若全被蓋住則**本節自己拒絕發綠**（`:319-324`），理由寫在訊息裡：那时「沒讀 dotenv」與「被 env 覆寫」不可分辨。
  2. **正控**（`:326-346`）：在 `tmp_path` 寫哨兵 `.env`、`monkeypatch.chdir(tmp_path)`，再要求 `Settings(_env_file=str(tmp_path / ".env"))` **真的**讀到那 4 個值（`assert not inert`，`:343`）。讀不到就紅——否則下面的環境檢查會無論如何變綠。
  3. **行為判準**（`:348-360`）：`ambient = Settings()` 必須逐欄等於 code default；不等即紅，訊息為「a bare Settings(...) reads the developer's `backend/.env` again …（conftest.py's dotenv neutralisation is off）」。
  4. 舊普查**留下來只當 bisect 工具**：`_bare_settings_constructions()`（`:260-281`）仍在，但只在失敗訊息裡印出，並標明是「拼寫的統計，不是 offender 名單」（`:357-360`）。
* **新增的失靈模式（這是收緊）**：把 `conftest.py:98-99` 那兩行刪掉，本節**必紅**，且與樹上有無裸 `Settings()` 無關；舊尺在那種情況下可能照樣綠。換句話說：舊尺只抓「拼寫不見了」，新尺抓「中性化不見了」。
* **未做**：沒加 skip、沒放寬任何斷言、沒把 `:175` 補 `_env_file=None`（那會抽空該測試的目的）、沒動 `app/config.py:560`（`env_file=".env"`）、沒動 `conftest.py`。
* **是否「未重現」**：**可重現**（1、2 兩格，同一節點名＋同一判語），因此本輪不需要「未重現」這個標籤，也**不改寫** §18.0-3b 那句 `1 failed`——它與本輪起點是同一棵樹（`9fc9d7a4…`）、同一支節點。

### 19.2 項目二：「本人撤回後，可以明確批准同一 job 的指定 skill」——持久讀回

* **缺口（如實交代根據）**：本輪寫入前那份位元組（18 collected，§17.0-1 釘過 18 passed）沒有任何節點在 job-only→scoped 切換後**重讀 `agent_model_activation_approvals` 全表**；`TestApprovalScopeRefusals` 只證「門會拒」。該檔在 git 裡是 `??`（未追蹤），故這句話的根據是 §17/§18 的節點清單與本輪對檔內 node 名的 `grep`（4 個新名在 §1–§18 全文出現次數皆 **0**，`concurrent_insert_collapsed` 出現 5 次但全部出現在「尚未驗證」的待批條目裡），**不是** git diff。
* **新節點** `backend/tests/test_agent_paper_execution.py:708 test_withdrawal_then_scoped_approval_keeps_both_records`，逐條對應 Brief：

| Brief 要求 | 節點裡的讀回（都是**讀表**，不是讀回應） |
|---|---|
| job-only 存在時，scoped PUT 被拒 | `blocked.status_code == 409`、`code == "agent_activation_already_approved"`（`:728-729`；拒因源頭 `app/services/model_activation.py:535`） |
| **原批准不變** | `_approval_row(job_only)`：`decision == "approved"`、`revoked_at is None`、`skill_unit_id is None`、`artifact_checksum` 等於該鏈的產物雜湊（`:731-735`）＋ `len(_approval_rows()) == 1`（`:736`）；`GET activation-state` 同時讀成 `skill_unit_id is None`／`skill_unit_scope == "none"` |
| （附帶）拒時紙上路徑不寫東西 | 該按下 `paper_execution_approval_scope_not_granted` 且 `_ledger_rows() == []` |
| 本人撤回後，新的 scoped PUT 成功 | 撤回 200 → scoped PUT 200；`scoped_id != job_only`、`skill_unit_id == portfolio_id`、`skill_unit_scope == "specific"` |
| **歷史撤回列仍在** | 同一行 `id` 變成 `decision == "revoked"`、`revoked_at is not None`、`revoke_reason == "switching to one named account"`、此刻仍 `len(_approval_rows()) == 1`；新批准落後 `len(_approval_rows()) == 2` 且 `live == [scoped_id]`（「新批准抹掉撤回史」會紅）、`_approval_row(job_only).decision` 在結尾仍是 `"revoked"` |
| 新批准**只覆蓋所選 skill** | 選中帳號執行 `execution_status == "executed"` 且帳本列 `approval_id == scoped_id`；另一帳號被拒 `paper_execution_approval_scope_not_granted` ＋ `reason_code == "skill_unit_mismatch"` ＋ 兩個拒因細節都指名 approval／已批准／被要求的身分；`_book(other_unit) == EMPTY_BOOK`、`len(_ledger_rows()) == 1` |
| 不得靜默撤回、不擴成多份 live approval | 撤回與批准都由測試經**正式入口**送出（`PUT …/activation`、`POST …/consent/revoke`），無任何 bypass；「同時只允許 1 筆 `revoked_at is None`」是硬斷言；本輪**未新增** route、旗標或第二份生效批准 |

* **分開回報（Brief 明令）**：本節點屬 **ORM 建表測試**（`Base.metadata.create_all` 的暫存 SQLite）。**migration 實際建表**（第 58 支 `e5f6g7h8i9j0`）**未執行**——本輪零 `alembic upgrade`。故「批准歷史在真 PG 上持久」這句話仍是 UNVERIFIED，判準在 19.8 第 2 條。

### 19.3 項目三：正式 POST 409 的**兩種**含義（不一律等於零寫入）

| 家族 | 程式碼位置（現讀） | 409 時**寫了什麼** | 回應裡有什麼 |
|---|---|---|---|
| 前置拒絕 | `_refused()` `app/services/agent_paper_execution.py:145`，用在 `execute_paper_decision`（`:318`）內的 `:333`、`:341`、`:353`、`:369`、`:376`、`:391`、`:428`、`:435` 等 | **零寫入**（無帳本列、無 decision／order／fill） | 具名 code＋細節，**沒有** `execution_id` |
| 已持久化的結果拒絕 | `_commit_ledger:652-698` 先 `db.commit()`（`:663`）**再** raise（`:687-697`）；三個碼：`paper_execution_insufficient_cash`（`:504-512`）、`paper_execution_risk_blocked`（`:551-559`）、`paper_execution_fill_refused`（`:630-638`）；`skipped` 走同一條（`:466-467`） | **提交一列 `execution_status="blocked"` 的帳本**，然後拒絕 | `details` 帶 `execution_id`／`execution_status`／`blocked_reason`／`risk_check`（`:690-696`） |

* **新節點 1** `tests/test_agent_paper_execution.py:994 test_blocked_press_is_durable_while_a_scope_refusal_writes_nothing`：把現金壓到 `1.0` → 409 `paper_execution_insufficient_cash`，`blocked_reason == "insufficient_cash"`、`execution_status == "blocked"`、`execution_id` 是合法 UUID（`:1015-1019`）。讀回**同一列**（ORM）：`model_decision == "take"` 保留、`decision_record_id／order_id／fill_id` 全 `None`、`quantity == 0.0`、`risk_check_json is None`、`artifact_checksum` 綁對（`:1024-1034`）；**用既有 GET** `…/paper-executions` 顯示同樣的值與「無 decision／無 order／無 fill」（`:1037-1045`）⇒ 關聯不存在就照實顯示，不本地推算。四項未動：`_book(starved) == EMPTY_BOOK`、`decisions == []`、`orders == []`、現金等於事前讀到的值（`:1047-1051`）。**重送同一意圖** → 200 ＋ `duplicate: True` ＋ 同一 `execution_id` ＋ 仍 1 列（`:1057-1062`）⇒ 讀回不確定時不會自動執行第二次。最後那記打在**未獲批准帳號**上的前置拒絕：409 `paper_execution_approval_scope_not_granted` ＋ `reason_code == "skill_unit_mismatch"` ＋ **`"execution_id" not in details`** ＋ 行數仍 1（`:1067-1071`）⇒ 兩個家族在同一支節點裡被對照著驗。
* **新節點 2** `:1081 test_risk_policy_block_keeps_the_policy_answer_that_refused_it`：以 house 既有寫法（monkeypatch `risk_policy.DEFAULT_RISK_POLICY`，非改產品預設）把 `max_daily_entries` 壓到 `0` → 409 `paper_execution_risk_blocked`、`blocked_reason == "daily_entry_limit"`、`risk_check` 裡 `allowed is False`／`code == "daily_entry_limit"`／`lane == "agent_model"`／`policy.max_daily_entries == 0`（`:1115-1121`）；**列存的 `risk_check_json` 等於回應裡的 `risk_check`**（`:1127`）、`row.quantity > 0`（尺寸已完成，擋下來的是政策 ⇒ 這條以後可複核），GET 讀回同一筆、帳本四項全空、重送 `duplicate`（`:1136-1146`）。
* **三種意圖分開驗**（Brief 點名）：①**已提交的同意圖**＝上面 `duplicate: True` ＋ 同 `execution_id`（無第二列）；②**撤回後的新意圖**＝19.2 節點（撤回把舊列改成 `revoked`、新列新 `id`，執行寫在**新**批准之下）；③**兩筆交易競態**＝下面那支 PG 節點，**NOT RUN**。
* **競態節點已备好、維持 NOT RUN**：`TestConcurrentIntentOnPostgreSQL`（class `:1407`）／`test_two_presses_on_one_intent_collapse_into_one_row`（node `:1425`，`requires_postgresql(...)` 在 `:1428`）。實物：barrier 放在**真實** `paper_service.run_inference` 之後，讓兩筆送進都通過 `:404-410` 的**無鎖** SELECT，再各用一條 `TestSession()` 進入 `execute_paper_decision`；執行緒的例外被收集、不被吞。斷言：1 列 `executed`、訂單／成交各一、兩份 payload 同一 `execution_id`、`sorted(duplicate) == [False, True]`、而那一份帶 `concurrent_insert_collapsed is True`（源頭 `agent_paper_execution.py:678-679`；本輪之前**全庫 0 支測試節點聲稱該旗標**）。該格跳過理由逐字（`…T033343Z-99188/pytest.log`）：`PG-ONLY [tests/test_agent_paper_execution.py:1428]: … SQLite serialises the writers and raises a lock error instead of an integrity error, so this branch cannot be reached on the SQLite lane. ALETHRA_PG_TEST_URL is unset … -- NOT RUN, not passed.`

### 19.4 「ORM 建表測試」與「migration 實際建表」到底差在哪（現測）

* **靜態同形已量（19.0-8）**：`32` 欄／10 FK（以 **(欄, 被參照表)** 配對核對，不靠 DB 自動命名）／unique 名稱與 3 欄／3 支索引的名→欄／`created_at` **無 server default**（`migrations/versions/e5f6g7h8i9j0_add_agent_paper_executions.py:150` 寫 `sa.Column("created_at", sa.DateTime(), nullable=False)`）；**4 條差集全 `[]`**。⇒ Brief 的條件句「若靜態核對確認索引定義**不等價**，提交最小待審修正」**未觸發**，本輪不提 schema 修正包。
* **但這仍是兩側的「來源」比較，不是 PG 的「結果」**：`tests/test_migration_tail_contract.py` 只證 revision 圖與 head；SQLite 那側是 `create_all`，不是 alembic。⇒ 卡 **A-Schema-2** 的 PG 讀回判準（表存在、FK 配對、unique 恰 3 欄、3 索引逐名可見、可空性、`risk_check_json` 為 jsonb、`created_at` 若讀到 server default 即 FAIL）**尚未執行**，列入 19.8 第 2 條。
* **新點名的脆弱點（照實寫，本輪未順手改）**：`migrations/…e5f6g7h8i9j0…:171-174` 用 `for name, columns in zip(INDEXES, (["owner_id"], ["job_id"], ["skill_unit_id"])): op.create_index(name, TABLE, columns)` —— **按位置**配對索引名與欄位。今天兩側剛好同序（`INDEXES`（`:81-86`）的順序是 owner、job、skill，元組同序 ⇒ 名稱與欄位相符，19.0-8 實測一致）；但只要有人在中間插入一支索引，位置配對就會把**索引名結到錯的欄位**，而 `test_migration_tail_contract.py`（只看圖）與 ORM 測試（不看 migration 正文）都不會紅。最小修法＝換成具名對應（`{name: [columns]}`）＋一條「在中間插入索引」的紅控；它與驗收條件一起放在 **19.8 第 3 條**等批准。**本輪未修改 migration 檔**：改它會在已釘的 tail 上重新產生待批 schema，屬較大動作，交本人決定。

### 19.5 項目四：B 維持 BLOCKED＋防洩漏在最終候選重驗

* **205 筆 unknown 被排除的直接證據（行號依當前位元組重核；本輪**未重跑**該檔，判定原樣沿用 §18.3）**：`backend/tests/test_card_b_seed_prep.py:1096` 走正式資料集资格讀回，斷言 `dataset["excluded"]["unverified_provenance"] == 205`（`:1120`）且其他排除腿為 0（`:1123-1124`）；`:1157` 證 `POST /api/v1/ml/personal/snapshots` 在本人按下訓練同意**前後**都回同一個 `exclusion_breakdown == {"unverified_provenance": 205}`（`:1182`、`:1211`）；`:1251` 是正控——只有真人列入門後種子那 205 筆仍被讀成 `unverified_provenance: 205`（`:1285`、`:1297`、`:1305`），而且**快照列自己存進 `exclusion_breakdown_json` 的就是這個數**（`:1312`）。判定：**B-正式訓練準備＝BLOCKED**。未改標 human、未降訓練門檻、未把 readiness 或「已載入的 fixture artifact」當成合成種子經過正式訓練的產物；沒有合法來源 ⇒ 計費棧未啟動。
* **防洩漏驗收在最終候選重跑**：`tests/test_failure_output_secret_hygiene.py` 在 `…T033343Z-99188` 內**實際 collected 5 節點**（`collect.txt` 逐條可點名：hook 已註冊／`SecretHeaders` 遮值但仍授權 200／三種憑證形狀各自被換掉且非憑證文字不動／帶 hook 的子 pytest 保留 `AssertionError`、節點名、跳過理由但不含哨兵／不帶 hook 的反控仍看得見原行）。⚠️ 該檔另有 4 支 `def test_…`（`:53`、`:57`、`:61`、`:65`）**在 child-session 的範本字串裡面**，不屬於 collected 節點 ⇒ 這裡的計數物件是 **collected node id**，不是 `def` 個數（用 `def` 數會得 9，那是另一把尺）。
* **原值處置**：本輪**未**匯出、未開啟、未摘錄任何舊 `pytest.log` 的憑證行；只用寫死在檔裡的無效哨兵驗收。
* **仍未覆蓋的輸出面（照實列，不美化）**：遮罩 hook 改的是**報表**（longrepr／sections），**captured stdout 未覆蓋** —— 哪天有測試自己 `print(token)` 或把 token 拼進 log message，那條仍會進 `pytest.log`；本輪 5 節點沒有防它（§18.4(e) 那句依然成立，本輪未動 `failure_output_hygiene.py`，100 行未變）。`--showlocals` 下的區域變數、以及 junit 之外新增的任何 sink，本輪亦未重新盤點。

### 19.6 本輪寫入清單與副作用揭露（行數／雜湊一律 `wc -l`＋`shasum -a 256` 現測）

| 路徑 | 動作 | 行 | sha256（前 8） |
|---|---|---|---|
| `backend/tests/test_process_state_isolation.py` | 改（閘門換尺：AST 拼寫普查 → 哨兵量測的行為不變式＋正控＋環境釘選檢查；普查降為失敗時的 bisect 資訊） | **361** | `5e8f4bdb` |
| `backend/tests/test_agent_paper_execution.py` | 改（**＋4 節點**：撤回→再批准持久讀回 `:708`、兩支持久拒絕 `:994`／`:1081`、PG 競態 `:1425`；模組與 class docstring 點名「409 有兩種含義」） | **1,544** | `c1015d64` |
| `EXECUTION_TABLE_20261117.md` | 追加本節 §19 | 追加前 **6,789**／`baecdb65`（現測）；追加後以 `wc -l` 另測，**本節不自指自己的雜湊** | — |

樹外產物（不進 repo）：`/tmp/r24_schema_split.py`（3,598 B，唯讀掃描：ORM 側 `Base.metadata` ＋ migration 側 AST 解析）。

* **本輪未寫入** `backend/app/**`、`backend/migrations/**`、`frontend/**`。證明的根據如實交代：本輪開輪第一格（`…T030357Z-66891`）的 `tree-digest-before == 9fc9d7a4… ==` §18.6 收輪時那棵樹，而本輪发出的寫入只有上表兩支 `tests/` 檔 —— 這是「**開輪同樹＋寫入清單**」，不是前後 sha 對比（§18 未釘 app 檔 sha）。為讓下一輪可比，這裡補上現測基線：`app/services/agent_paper_execution.py 2172b94b…`、`app/services/model_activation.py 0d880029…`、`app/services/risk_policy.py 76de1146…`、`app/api/agent_strategy.py dff90bd0…`（sha256 前 8，`7f2f8bf2…` 樹上）。
* **git 狀態現測**：`live1-impl`、HEAD `5a34e3f`、`git status --porcelain` **245** 路徑／sha256 `12685f14a7e73293…`（與 §18 各格 identity.txt 同值 ⇒ 本輪未新增也未移除任何髒路徑；上表兩支測試檔本來就是 `??` 未追蹤）。
* **本輪花了什麼**：4 支暫存 SQLite 測試庫（`alethra-sqlite-test-<pid>…db`）與 7 個 run 目錄產物，全在 `${TMPDIR}` 下。**本人帳號：0 筆正式庫寫入、0 張批准、0 份同意、0 列種子、0 次紙上執行** —— 測試裡的批准／撤回／執行都落在該 run 自己的暫存庫。
* **本輪沒有**：`alembic upgrade`、起 PG／服務／瀏覽器／cron、啟動 Playwright（含 `--list` 探測）、外呼、讀或印秘密值、展開程序環境、**重釘候選**（依 Brief「不重釘」；`tests/collected_nodes.txt` 仍釘 **3,811**、`tests/skip_budget.json` 仍釘 `collected: 3811`／pg_only **159**／live_stack 18／other 2／total 179）、clone/apply、A／B／C／D 任何一張卡的執行、任何 **Git 寫入**（`git add`／`commit`／`push`／`tag` 均未執行）⇒ 本輪交付一律 **LOCAL WRITTEN／NOT PUSHED**。
* ** disclosed 缺口（整套盤點）**：本輪只在兩檔之間量到節點差 `18 → 22`（`…T032901Z-92195` 與 `…T033343Z-99188` 的 `collect.txt`），其中 1 支是 PG-ONLY skip。這句話是**那兩檔的差值**，不是新的整套 ceiling——`3,811` 與 `pg_only 159` 本輪**未重測**，因此**不得**據此推算「現在應是 3,815／160」並当成已釘值。子選集泳道不受該 ceiling 影響（`run_tests.sh:520` 認 `--explicit-selection`；`skip_budget.py:188-195`、`run_evidence.py:436-441` 在子選集上「只報不判」），故 1–7 七格的 exit 與此缺口無關。研究 8 家族全部 FAIL 的原結論**原樣保留**，本輪未重跑、未改寫。

### 19.7 哪些證據仍屬舊候選（不得拼成「同一發布候選已通過」）

| 證據 | 所屬候選 | 本輪狀態 |
|---|---|---|
| §18.0-3 的 4 檔合併 **59 passed** | `9fc9d7a4…` | 本輪**未重跑該 4 檔選集**；paper 檔在 `7f2f8bf2…` 上是 22 節點（內含 1 支未執行的 PG-ONLY）⇒ 兩句各自成立，不能疊成「同一候選兩次通過」 |
| §18.0-3b 的 **1 failed**（同那 8 檔） | `9fc9d7a4…` | **不改寫**：那是既存紅燈的真實紀錄；本輪 19.1 的收斂發生在**之後**的樹（`4d91c4c7…`／`7f2f8bf2…`），且只重跑了其中的 2 檔／4 檔子集 |
| §18.0-1／1b／2／2b 前端 47／23／15 節點＋`tsc` 0 | 前端檔 sha（`805cea19…`／`af6474de…`／`deab3aa3…`） | 本輪**未動前端、未跑 node 泳道、未跑 tsc** ⇒ 仍是 R23 的證據 |
| §18.3 種子 **21 節點** | `9fc9d7a4…` | 本輪**未重跑**該檔，只在 19.5 重新核對行號與斷言位置 |
| §18.0-5 離線 tail 事實（head `e5f6g7h8i9j0`／58 revisions） | 當時 `migrations/versions/` | 本輪未重跑該腳本；只新增讀了 migration 正文（19.4） |
| §17.0-1b 相鄰 10 檔 **172 passed＋1 skipped** | `fecb6434…` | 本輪未重跑 |
| R21 那 51 格 | `a45bcee9…` | 未重跑、未重釘 |

### 19.8 尚需本人批准的判準（逐條一個名字，未批准即不執行）

| # | 待批操作 | 會寫什麼 | 判 PASS 的條件 | 判 FAIL／UNVERIFIED |
|---|---|---|---|---|
| 1 | **A-PG-競態-2**（實物已在本輪寫好）：`ALETHRA_PG_TEST_URL='<postgresql://…/alethra_test…>' bash ./backend/scripts/run_tests.sh pg tests/test_agent_paper_execution.py::TestConcurrentIntentOnPostgreSQL::test_two_presses_on_one_intent_collapse_into_one_row` | 該測試 PG 庫上的 1 列帳本＋1 訂單＋1 成交（暫存庫，不動本人帳號） | 1 列 `executed`、訂單／成交各一、兩份 payload 同一 `execution_id`、`duplicate` 恰為 `[False, True]`、那份帶 `concurrent_insert_collapsed is True` | 出現第 2 列、或兩份都 `duplicate is False`、或 `IntegrityError` 原樣抛出 ⇒ **FAIL**；拿不到 PG URL ⇒ **UNVERIFIED（維持 NOT RUN）** |
| 2 | **A-Schema-2**（PG 讀回真實建表）：在獲准的 dev 庫上 `alembic upgrade head` 後讀 `agent_paper_executions` | CREATE TABLE＋3 索引（第 58 支 migration，屬新副作用） | 表存在、10 條 FK 以 (欄, 被參照表) 配對可見、`uq_agent_paper_execution_intent` 恰覆蓋 3 欄、3 支索引逐名可見、可空性如 model、`risk_check_json::jsonb`、`created_at NOT NULL` 且**讀不到 server default** | 讀到 `created_at` 預設值 ⇒ migration 與 model **不同形**＝FAIL；該格的 PASS 只算 PG 事實，**不繼承** 19.0-8 的靜態同形，也不可反推 19.2 的 ORM 測試 |
| 3 | **A-索引-具名**（本輪新點名，未批准 ⇒ 檔未動）：把 `migrations/…e5f6g7h8i9j0…:171-174` 的位置 `zip` 換成具名對應，並補一支「中間插入索引」的紅控 | 只改 migration 正文（改後需重新釘 tail） | 改前／改後 PG 上 `\d agent_paper_executions` 的索引名→欄位對照**逐行相同**；紅控在換成具名後轉綠、在位置 `zip` 下必紅 | 任何索引名對應到不同欄位 ⇒ FAIL。**未批准 ⇒ 本輪未修改此檔** |
| 4 | **A-撤回/提交競態（PG）**：窗口＝`agent_paper_execution.py:424` 的覆核之後、`:663` 的 commit 之前落入撤回 | 同上（PG 測試庫） | 只有兩種可接受結果：(a) 帳本列指向已撤回 approval 且執行被拒；(b) 執行在撤回前已 commit 且讀回 `executed` | 第三种（指向已撤回列卻照常成交）⇒ **FAIL**。SQLite 無行鎖 ⇒ 本條**不宣稱**任何 SQLite 證據 |
| 5 | **C-agent-journey**（瀏覽器旅程，R23 已列入）：READ 群需 `C-resume`；WRITE 群還需本人親自給 `WRITING_SKILL`／`TARGET_JOB` 並先按一次同意 | 真實 DOM 上的一次批准＋一次紙上執行（本人帳號） | 逐屏讀回（scope 通知、具名拒因、正式 GET 的 ledger／decision／order／fill） | 未起棧 ⇒ **NOT RUN**。本輪未新增 spec、未動既有 spec |
| 6 | **整套重釘**：一次完整 SQLite 全套，重寫 `tests/collected_nodes.txt` 與 `tests/skip_budget.json` | 只寫那兩份釘選檔 | 結尾 `evidence gate` 綠且 ceiling 對得上重測值 | 未批准 ⇒ 本輪照 Brief「不重釘」，缺口照 19.6 如實揭露 |

---

## 20. 第 24 輪 b（2026-11-24 16:39–17:13 HKT＝08:39–09:13 UTC）——①批准索引三來源實測判定＋7／0 最小修正＋反事實、②待批 PG 驗收收斂成 4 張具名卡（全 NOT RUN）、③單列 dev 瀏覽器驗收（未起瀏覽器）、④captured 串流洩漏根因修復（8／8 綠，rc 0）

### 20.0 本輪候選身分（每格本輪重測，不抄 §19）

| 項 | 實測值 |
| --- | --- |
| branch ／ HEAD | `live1-impl` ／ `5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` |
| porcelain | 247 path(s) sha256 `964810ab99cc024e6a348ceb04f6b0720ced79f5a12a2daf16729dfdf7acc590` |
| lane fingerprint | `python 3.12.16 \| pytest 8.3.3 \| sqlalchemy 2.0.36`（各次 `identity.txt` 逐字） |
| 本輪改動 | revision `a7c1e4f9b2d5`（tracked，**7 insertions／0 deletions**）、revision `e5f6g7h8i9j0`（untracked）、`failure_output_hygiene.py`（untracked，113 行，`485e291b192b6907920a20e7c80f0c3d85bccb2dc53c01d61da0850a7b01c501`）、`test_failure_output_secret_hygiene.py`（untracked，383 行，`0191da77068559113a63c961b3eca2867c5d6614633930cbedeba292ed3d4af7`）、`test_agent_paper_execution.py`（untracked，1,883 行，`b0cfa30b09e3d4f3…`）、`agent-approval-paper-journey.spec.ts`（untracked，826 行，`fc32dcd8aeb7e2908b0cd0bac35972bf26a9c1c5d8f996ac35f758429b3811d5`） |

**綠收據各自覆蓋哪份位元組**（判據＝該檔 mtime ≤ 該次 run 起跑；三次 run 的 `tree-digest-after` 依序為 `82727639`／`2872aa93`／`2500c7d5`，不同是因為後續編輯動了整棵樹，綁位元組要靠逐檔 mtime 而不是樹摘要）：

| run（exit） | selection | 結尾 | 覆蓋 |
| --- | --- | --- | --- |
| `sqlite-20261124T084336Z-31682`（**0**） | `test_activation_approval_index_contract.py` + `test_migration_tail_contract.py` + `test_agent_paper_execution.py` | 43 passed, 1 skipped | 索引閘 7 節（檔 mtime 16:43:24 < 起跑 16:43:36）與修正後的 revision（16:39:20）。**注意**：同欄的 `test_agent_paper_execution.py` 跑的是 16:43 當時的位元組，不是現行位元組 |
| `sqlite-20261124T085759Z-36595`（**0**） | `tests/test_agent_paper_execution.py` | 24 passed, 2 skipped | 現行紙上執行欄（16:57:51）。兩顆 skip 逐字指到 `PG-ONLY [tests/test_agent_paper_execution.py:1641]` 與 `:1767` |
| `sqlite-20261124T091334Z-41889`（**0**） | `tests/test_failure_output_secret_hygiene.py` | 8 passed | 防洩漏 8 節（17:12:31／17:13:30） |

### 20.1 ①批准索引：三個來源的實際判定

**來源行址**（本輪逐檔讀回）：

- ORM：`backend/app/models/agent_strategy.py:349-355` —— `Index("uq_agent_activation_approval_live_job", "job_id", unique=True, postgresql_where=text("decision = 'approved'"), sqlite_where=text("decision = 'approved'"))`。
- 建立該索引的 revision：`backend/migrations/versions/a7c1e4f9b2d5_add_model_activation_approvals.py:62`（`op.create_index(`）、`:63`（索引名）、`:67`（`postgresql_where`）、`:74`（本輪補上的 `sqlite_where`）。
- 之後有沒有替換：**沒有**。`test_the_live_approval_index_is_created_once_and_never_replaced`（`backend/tests/test_activation_approval_index_contract.py`，rc 0 收據見 20.0）遍歷 `migrations/versions/*.py`，要求該名只被建立一次、且建立者必須是加表的 revision 本身；該名的 `drop_index` 只出現在同檔 downgrade()（`:93`），不在任何 upgrade() 內。

**四格 DDL（本輪用專案自己的尺產生，非手抄）**——腳本直接 import 該閘的 `_migration_call`／`_build`／`_ddl`，對 `AgentModelActivationApproval.__table__.to_metadata(MetaData())` 的副本編譯：

| 來源＼方言 | PostgreSQL | SQLite |
| --- | --- | --- |
| ORM（`create_all`） | `CREATE UNIQUE INDEX uq_agent_activation_approval_live_job ON agent_model_activation_approvals (job_id) WHERE decision = 'approved'` | 同左（含 `WHERE`） |
| revision（`alembic upgrade`，修正後） | 同左 | 同左 |
| revision（**修正前＝反事實**） | 同左（不受影響） | `CREATE UNIQUE INDEX uq_agent_activation_approval_live_job ON agent_model_activation_approvals (job_id)` ——**無 predicate** |

**落差與最小修正**：修正前唯一差異在 SQLite 欄。缺 `sqlite_where` 時該方言得到一個普通 `UNIQUE(job_id)`，於是「已撤回的批准仍佔住該 job 的席位」，同一 job 再批准會在遷移庫撞、在 `create_all` 庫不撞——同一條「每 job 一個 live approval」由兩套 builder 強制出兩種規則。修正＝只加這個 kwarg（7 列，其中 6 列是註解）；不改模型、不改任何測試預期、不執行 migration。

**範圍宣告（避免被讀成兩套 schema 等價）**：本專案支援的 SQLite schema 是 `create_all`（`backend/tests/conftest.py:292`），SQLite 的 migration 路徑不在支援範圍；本輪只宣稱**這一個索引**在兩個 builder 下產出同一條 DDL，不宣稱整條鏈、也不宣稱其他索引等價。已知且刻意保留的差異另有其一：`ix_agent_activation_approval_skill_unit`（模型）對 `ix_agent_model_activation_approvals_skill_unit_id`（`b2c3d4e5f6g7:121`）同欄不同名，由 `test_the_skill_unit_index_diverges_by_name_only` 釘為「已知差異」而非改名。

**另一項（Brief 明定分開處理）**：`e5f6g7h8i9j0_add_agent_paper_executions.py` 的具名索引／欄位對照屬獨立一項，與批准索引修正無關。現況實測：`INDEXES` 為具名對（`:84-88`：owner→`owner_id`、job→`job_id`、skill→`skill_unit_id`）、`:174` 與 `:188` 兩個 `for name, columns in INDEXES:`、整檔 `grep -c "zip("` = 0；唯一鍵 `sa.UniqueConstraint("skill_unit_id", "job_id", "candidate_id", name="uq_agent_paper_execution_intent")`（`:170-172`）。修正前的 zip 原文仍留在 §19:6856，未被覆寫。該 revision 未執行過（untracked、無迁移記錄），因此不觸發重釘。

**本輪那顆紅的歸屬**：`sqlite-20261124T084237Z-31124`（rc 1，1 failed／42 passed／1 skipped）失敗節點為 `test_the_postgresql_build_matches_the_model_build`，死因是指針自身 `tests/test_activation_approval_index_contract.py:128` 的 `KeyError: Table(...)`——`_ddl` 被餵了 revision 的 call dict 而不是 `Index`。這是尺的缺陷，**不是**產品控制，也**不是** `sqlite_where` 修正的敏感性證據；後者是上表反事實那一欄（拿掉 kwarg，SQLite 欄失去 `WHERE`、PG 欄不變）。

### 20.2 ②待批 PG 驗收 → 具名操作（4 張卡，本輪一律 NOT RUN）

兩個前置事實，本人批准前先要看到：

1. **PG 層不建 schema**：`backend/tests/conftest.py:283-302` 對非 SQLite 不做 `create_all`（schema 必須已由 migration 備好），因此卡 B–D 的行為驗收**預先假設一次已批准的 `alembic upgrade head`**；卡 A 就是那道假設的讀回，未跑 A 之前 B–D 的結果都不可解釋。
2. **交疊是真的，不是事後推**：競態節點每條執行緒各自 `TestSession()`（`tests/test_agent_paper_execution.py:1675`、`:1690`），撤回端在鎖點释放（`:1689`），且節點自己斷言 `timings["revoke_start"] < timings["submit_end"]`（`:1714`）——**跑過但沒交疊就是 INVALID**，這由構造保證，不靠 sleep／重試／事後讀回。

所有卡的共同欄位：**目標** = `ALETHRA_PG_TEST_URL`（值不印出；`scripts/run_tests.sh:431` 逐字印 `target: ALETHRA_PG_TEST_URL (not printed)`，未設該變數時 `:170-173` 直接 `refusing… exit 2`；库名前綴閘 `ALETHRA_TEST_REQUIRED_DB_PREFIX` 預設 `alethra_`）。**候選** = 20.0 那格（HEAD `5a34e3f`、porcelain `964810ab…`／247、索引閘綠收據 `31682`、欄位綠收據 `36595`）。**身分核對** = run 目錄（`${TMPDIR}alethra-runs/pg-<utc>-<pid>`，`run_tests.sh:326`）裡 `identity.txt` 的 `git:`／`porcelain:`／`lane fingerprint:` 三行逐字對上 20.0，且 `layer: pg`。**停止命令** = 該終端 Ctrl-C；要讓它無法再跑，`unset ALETHRA_PG_TEST_URL`（下一跑 rc 2）。**判讀詞** = 未跑 ⇒ NOT RUN；跑了但未形成交疊／未形成預期衝突 ⇒ INVALID（不是 PASS）；只有節點自己的斷言成立才是 PASS。

| 卡 | 只做什麼 | 完整 node ID | 各自報的結果 |
| --- | --- | --- | --- |
| **A｜schema 讀回** | 唯讀 DDL，兩件事分開報 | 不跑測試，只發 `psql "$ALETHRA_PG_TEST_URL" -X -q -A -c "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='agent_model_activation_approvals' AND indexname='uq_agent_activation_approval_live_job'"`；再 `-c "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='agent_paper_executions'::regclass AND contype='u'"` | (1) 批准索引的**鍵＋predicate**：期望含 `WHERE (decision = 'approved'::text)`，缺 `WHERE` ⇒ 該庫建的仍是普通 UNIQUE。(2) 帳本唯一鍵：期望 `uq_agent_paper_execution_intent` 覆蓋 `(skill_unit_id, job_id, candidate_id)`。(3) 兩者各自 PASS／FAIL，不互代。**UNVERIFIED**：`pg_get_constraintdef` 的逐字形狀在本機無 PG，無法產出實測字串——卡 A 的價值正是把它變成實測 |
| **B｜撤回再批准** | 舊列保留、同一 job 恰一筆 live | `tests/test_agent_paper_execution.py::TestApprovalScopeRefusals::test_withdrawal_then_scoped_approval_keeps_both_records`（`:712`） | 讀回的是 `agent_model_activation_approvals` 本身：拒絕的scoped_act 不動原列（`:737-742`）、撤回是同一列而非新增（`:760-765`）、再批准後共 2 列且 `revoked_at is None` 的只有新那筆（`:777-782`）、該批准只 authorize 它點名的帳號（`:785-811`）。SQLite 欄已綠（`36595`）；**PG 欄 NOT RUN** |
| **C｜兩筆同意圖** | 兩條獨立連線、實際交疊、只一筆帳本意圖 | `tests/test_agent_paper_execution.py::TestConcurrentIntentOnPostgreSQL::test_two_presses_on_one_intent_collapse_into_one_row`（節點 `:1764`，`requires_postgresql` 呼叫點 `:1767`，`threading.Barrier(2)` 在 `:1796`） | 斷言 `concurrent_insert_collapsed`（服務端 `app/services/agent_paper_execution.py:722-738` 的 IntegrityError 分支——SQLite 欄從未進過這支）；決策／訂單／成交另有獨立讀回。**PG NOT RUN，且這條分支目前是「存在的程式碼」而非「觀察到的行為」** |
| **D｜撤回與提交** | 先寫死以哪個交易結果判先後 | `tests/test_agent_paper_execution.py::TestRevokeVsSubmitOrdering::test_revoke_and_submit_racing_on_one_approval_row`（節點 `:1638`，`requires_postgresql` 呼叫點 `:1641`） | 先後由**行鎖的取得者**決定，不是由讀回重建：guard 對同一主鍵 `FOR UPDATE`（`app/services/agent_paper_execution.py:657`、鎖點 `:690`、拒因 `:698-706`），撤回走 `app/services/model_activation.py:573-580` 同一把鎖。兩種可接受結果各別斷言（撤回先赢 ⇒ 具名拒絕 `paper_execution_approval_revoked_midflight` 且帳本零列；提交先赢 ⇒ `executed` 且列讀得回來），觀測到的那一支由 `:1743` 印出而非預設。**PG NOT RUN**；同 class 的 `test_revocation_between_the_read_and_the_commit_writes_nothing`、`test_a_declined_model_writes_no_ledger_row_when_the_revocation_won`、`test_the_guard_locks_the_row_the_withdrawal_locks` 是 SQLite 欄的決策覆核／源頭對照，已綠（`36595`），**不取代** D 的互斥本身 |

### 20.3 ③單列 dev 瀏覽器驗收（未起瀏覽器，未替本人按）

`frontend/e2e/agent-approval-paper-journey.spec.ts`（826 行）一欄走完：job-only 衝突 → 本人撤回 → 本人明確授權指定 skill → 紙上執行 → 正式帳本讀回；另驗兩種 409。四類代價分開列：

| 步驟（spec 節點） | 自動化 | 本人操作 | DDL | 可能扣額 |
| --- | --- | --- | --- | --- |
| job-only 批准已存在時按「綁定此技能帳號」→ 409 `agent_activation_already_approved`（`:437`、`:474`） | 送 request、讀 DOM 通知 | 按一次 | 無 | 無（前置拒絕，`_ledger_rows()==[]`） |
| 本人撤回（`:1164` 的 POST revoke） | 讀回 `decision`／`revoked_at` | **本人按下**（撤回是本人的權限行為） | 無 | 無 |
| 本人明確授權指定 skill（PUT `:1158`） | 讀 `skill_unit_scope` | **本人按下** | 無 | 無 |
| 紙上執行（POST `:1182`，`test 'one press sends the named account…'` `:610`） | 讀 response＋帳本 | **本人按下** | 無 | **零**：該按讀的是已存 K 線與記憶體分數，不外呼交易所、不動真實餘額；「扣額」在本產品僅指額度閘（訓練／研究），紙上執行不消耗它們 |
| 正式帳本讀回（GET `:1187`／`paper-portfolio` `:455`） | 純讀 | 否 | 無 | 無 |
| 409 之二：已持久化的 blocked 帶原 `execution_id`、無虛構成交（`test 'a second press of the same intent adds no second row'` `:711`） | 讀回列欄位 | 否 | 無 | 無 |
| 尚欠的批准 | — | 起後端＋起浏览器＋本人帳號與 `E2E_ACTIVATION_JOB`／`WRITING_SKILL` 兩個環境值（`:608`、`:611` 未給即 skip，不代選） | — | — |

### 20.4 ④captured 串流防洩漏：根因、兩個缺陷與退出碼

**根因（本輪探針實測，推翻我先前的假設）**：`report.sections` 是 `(heading, content)` 二元組（`_pytest/capture.py:846-847` 附加；`_pytest/reports.py:381-382` 連 phase 一起當標題），且**每份 report 各自從 `item._report_sections` 重抄一份**（`_pytest/reports.py:317`），所以只改一份 report 會讓下一份保持原樣。真正寫進 `junit.xml` 的是 **teardown report** 的串流：call report 的寫入受 `junit_log_passing_tests`（預設 True，`_pytest/junitxml.py:470`）閘住（`:589-590`），teardown 的寫入**無條件**（`:597-599`）——而一份 teardown 乾淨的 report 不是 failed report。

**修掉的兩個缺陷**：(1) `backend/tests/failure_output_hygiene.py` 原本在 `if not report.failed: return` 之後才遮罩，等於放掉了真正會寫進 JUnit 的那份；現改為**不論 outcome 都先遮罩每份 report 的 sections**，再把 longrepr 遮罩留給 failed（`:113` 行內）。(2) 遮罩迴圈原本 `for name, prefix, content in report.sections` 從三元組解包 ⇒ 新加的「會印字的子節點」一跑就 `INTERNALERROR`（收據 `39429` 內 89 次 INTERNALERROR 痕跡）。這是潛在缺陷，之前沒開火只因該檔沒有 non-failed 節點印過 stdout。

**敏感性控制（同一檔內的反事實，不靠註解）**：`test_the_hygiene_hook_is_the_only_difference_between_the_two_children` 一類節點跑**未掛 hook** 的子 run，實測哨兵仍出現在三段 console 與 passing／failing／skipped 三種 junit 串流裡；掛了 hook 才三段只剩 `<redacted-jwt>`／`alk_<redacted-key>` 且保留 `stdout keeps this line:` 等診斷行。另證明**正式欄預設根本不寫 captured 串流**（`junit_logging` 預設 "no"，未加 `-o`、未加 `-s`：預設 ini 的子 run 拿到 `captured == []`），以及 `scripts/skip_budget.py:42/:43/:72` 只讀 `SKIPPED [` 摘要行、不讀 captured，所以擴大遮罩範圍不會讓 skip 普查失明（同節點斷言 skip 句仍在 console）。

**受影響節點與退出碼**：只跑 `tests/test_failure_output_secret_hygiene.py`（8 節）。`090541Z-39429` rc 1（2 failed／6 passed，含 89 次 INTERNALERROR 痕跡＝缺陷 2 現形）→ `090645Z-39753` rc 1（1 failed／7 passed，紅在 `tests/test_failure_output_secret_hygiene.py:288` 的 `assert SENTINEL not in streams`，console 三段已乾淨、JUnit 串流仍漏＝缺陷 1 現形）→ `090741Z-40315` rc 1（單節點 `-vv` 診斷）→ `091334Z-41889` **rc 0，8 passed**，`junit.xml` tests=8／failures=0／errors=0，`junit.xml` 與 `pytest.log` 兩者哨兵出現次數皆為 0。**未讀、未掃、未匯出舊 `pytest.log` 的 Bearer 值**；本輪只用自製哨兵字串。

### 20.5 尚欠批准（本輪一項都沒執行）

| 缺口 | 需要本人的哪一句 | 本輪狀態 |
| --- | --- | --- |
| `alembic upgrade head`（PG 測試庫） | 「准許在 `<庫名>` 跑遷移」 | 未執行；卡 B–D 的前置 |
| 卡 A schema 讀回 | 「准許對該庫發兩條唯讀 psql」 | 未執行（本機無 PG） |
| 卡 B／C／D | 「准許跑 `run_tests.sh pg <node ids>`」 | 未執行，NOT RUN |
| 瀏覽器一欄（20.3） | 起後端＋起瀏覽器＋本人帳號與兩個環境值 | 未起、未替本人按 |
| 整套重釘 | 「准許重釘 `collected_nodes.txt`／`skip_budget.json`」 | 依 Brief「不重釘」，未做；ceiling 缺口照 §19.6 如實揭露 |
| A／B／C／D 四包 | 各自原句 | 一律 BLOCKED，B 維持 BLOCKED |

### 20.6 指針修正對照表（本輪逐條重測後改掉的手寫位址）

| 檔 | 舊（錯） | 新（實測） |
| --- | --- | --- |
| `frontend/e2e/agent-approval-paper-journey.spec.ts` | `agent_paper_execution.py:314` | `:319`（`runs_live_trading: False`） |
| 同上 | `:318` | `:323`（`execute_paper_decision`） |
| 同上 | `:652` | `:709`（`_commit_ledger`） |
| 同上 | `:664-680` | `:721-738`（commit／IntegrityError 折疊分支） |
| 同上 | `:404` | `:409`（同意圖去重讀） |
| 同上 | `:683-697` | `:721` 與 `:742-755`（持久拒絕的 commit 與 raise 分開） |
| 同上 | `:402-417` | `:409-422` |
| 同上 | `:146` | `:150`（`_refused`） |
| 同上 | `src/lib/api.ts:~1147` | `:455`（`paper-portfolio` 清單） |
| 同上 | `api/agent_strategy.py:483-504` | `:483-502`（轉呼 `list_paper_executions`，回 `{lane, count, executions, runs_live_trading}`＝`agent_paper_execution.py:298-320`） |
| 新增說明 | — | 「WHAT THIS DOES NOT PROVE」補第 6 個拒因 `paper_execution_approval_revoked_midflight`：面板會翻譯它，門在 `:698-706`  raise，需要 `:718` 與 `:721` 之間的窗口，歸 `TestRevokeVsSubmitOrdering` 所有（單擊一次按钮到不了） |
| `EXECUTION_TABLE_20261117.md` §19:6870 | 「撤回→再批准持久讀回 `:708`」 | 現行位址 `:712`（該檔本輪增至 1,883 行） |

api.ts 那組（GET `:1150`／PUT `:1158`／revoke `:1164`／paperExecution `:1181-1182`／paperExecutions `:1187-1189`）本輪逐行重讀後**未變**，因此不在修正表裡——寫下來是為了讓下一輪能區分「查過且正確」與「沒查」。

### 20.7 證據等級（不混用）

| 宣稱 | 等級 |
| --- | --- |
| 批准索引四格 DDL 一致（含反事實） | **LOCAL WRITTEN＋EXECUTED**（離線編譯，`31682` rc 0） |
| `sqlite_where` 修正本身 | LOCAL WRITTEN，**未 push**，未執行任何 migration |
| 紙上執行欄（含 B 節點的持久讀回） | **SQLite EXECUTED**（`36595` rc 0，24 passed／2 skipped）；PostgreSQL **NOT RUN** |
| 互斥（行鎖）、同意圖折疊、具名 midflight 拒絕 | **UNEXECUTED READBACK**——程式碼在、分支寫明、節點可跑，但本欄從未交疊 ⇒ 不稱 PG 已驗 |
| 瀏覽器一欄 | SPEC WRITTEN（本輪只修指針與敘述，未動斷言邏輯），**NOT RUN** |
| 防洩漏遮罩 | **LOCAL EXECUTED**（`41889` rc 0，8／8；兩缺陷各有對應的紅收據） |

## 21. 卡 C 執行紀錄：8101／3101 dev 瀏覽器功能驗收（2026-11-24，本輪實際執行）

批准：owner 2026-11-24 精確批准卡 C 三項操作（隔離庫＋起棧 DDL、啟動 dev 候選棧、只跑三支既定 spec）與三道停止線。本節只記**本輪實測**；結果一律 **DEV**（本機 disposable 叢集＋合成 fixture），不外推 production。

### 21.0 前置核對（逐條現測，17:33–17:40 HKT）

| 前提 | 實測 | 出处 |
|---|---|---|
| 8101／3101 佔用 | 起棧前各 **0** 聆聽 | `lsof -nP -iTCP:8101/3101 -sTCP:LISTEN` |
| alembic head | **唯一** head `e5f6g7h8i9j0`，與批准預期相同 | `cd backend && ./.venv/bin/python -m alembic heads` |
| 持久候選檔 | `.local/candidate/` 不存在 ⇒ RESUME 依設計會 `fail`（`start_candidate_stack.sh:112`），入口只能是 `--new-candidate` | 現測 |
| 本機 PostgreSQL | 只有 `127.0.0.1:54342`（pid 73353）在聽；**54321／54340／5432 全部 0** | `lsof -nP -iTCP -sTCP:LISTEN` |
| 54342 是誰的 | 其 PGDATA = `/Users/…/2026-11-03/5062987d/verify-p5-20261122/pgdata` ⇒ **平行（客戶／測試）線的受保護實例** | `ps -o command= -p 73353` |
| ⇒ 目標庫 | 54342 **不用**（批准禁止連接既有受保護 DB），改由本輪**新建立**的叢集承載（見 21.1） | 判定 |
| provider／開關是否會從 `backend/.env` 靜默讀入 | `backend/app/config.py:560` `ConfigDict(env_file=".env")`；實測 **環境變數優先**（pydantic_settings 2.6.1）：導出四個值後 `Settings()` 讀回 `data_provider=demo`／`ai_provider=mock`／`research_auth_bypass_enabled=False`／`live_trading_enabled=False`，而 `.env` 的 `DATABASE_URL` 仍是 `localhost:5432/ai_investment_lab`（未被本輪使用）；把 `LIVE_TRADING_ENABLED` 灌成非法字串時，`ValidationError` 的 `input_value` 就是我環境裡那個 ⇒ 證明來源是環境而非 dotenv | 現測 |
| `backend/.env` 鍵名（**只有鍵名**） | 13 鍵：`CANDIDATE_PASSWORDLESS_ACCOUNTS/MODE/SESSION_MINUTES`、`DATABASE_URL`、`DATA_PROVIDER`、`JWT_SECRET_KEY`、`KRAKEN_WS_URL`、`LIVE_TRADING_CAPITAL/ENABLED/INTERVAL/SYMBOLS`、`RESEARCH_AUTH_BYPASS_ENABLED`、`TWELVEDATA_API_KEY` | `sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p'` |
| CORS |  launcher 自己在 `start_candidate_stack.sh:185` 導出 `CORS_ORIGINS=http://localhost:3101,http://127.0.0.1:3101`（不需我填） | 該行 |
| 三支 spec 的環境前置 | tier-B 憑證 `E2E_CRED_FILE`／`E2E_EMAIL`＋`E2E_PASSWORD`（`frontend/e2e/runtime.ts:33-34,52-56`）；`E2E_LINEAGE_ASSET` 預設 `DEMO-ALPHA`（`manual-decision-lineage.spec.ts:112`）；`E2E_DAILY_ESTIMATE_SKILL`（`daily-estimate-journey.spec.ts:107`）；`E2E_PAPER_EXECUTION_SKILL`＋`E2E_ACTIVATION_JOB`（`agent-approval-paper-journey.spec.ts:116-117`） | 各该行 |
| Playwright 能跑的 node | `node_modules/.bin/playwright` 在 **node 18.20.8 下直接拒絕**（「Playwright requires Node.js 20 or higher」）⇒ 本輪用 `~/.local/share/node-v20.20.2-darwin-arm64/bin`；`next dev` 仍由 launcher 以 18 起（正常服務中） | 現測 |

### 21.1 隔離庫（本輪新建，未碰任何既有實例）

| 項目 | 值 |
|---|---|
| 二進位 | `.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin`（PostgreSQL **16.2**，aarch64-apple-darwin23.5.0） |
| PGDATA | `.local/candidate-pg-c2-20261124`（本輪新建；建前 `[ -e ] && REFUSE` 通過） |
| 聆聽 | `127.0.0.1:54351`（起棧前現測 0 聆聽），`unix_socket_directories=/tmp/algc2`，`max_connections=25`，`shared_buffers=64MB`，`-U postgres -A trust` |
| postmaster pid | 60005（本輪自己起、本輪自己停） |
| 建後內容 | 只有 `postgres`／`template0`／`template1`——**無任何既有 alethra 庫** |
| 環境限制（實測） | socket 目錄放在 repo 深路徑時 initdb 可成但 `pg_ctl start` 失敗：`Unix-domain socket path … is too long (maximum 103 bytes)`／`FATAL: could not create any Unix-domain sockets`（`.local/candidate-pglogs-c2-20261124/pg.log`）⇒ 改短目錄後成功。**未重試其它參數、未放寬任何條件** |
| 未使用 | 54342（平行線）、54340（批准明令禁止）、共享 `test.db`、`backend/.env` 的 `ai_investment_lab` |

### 21.2 兩個 launcher 缺陷（本輪實測到，**未修、未提交**）

1. **`--new-candidate` 的 DDL 打錯庫**。`new_candidate()` 先 `create database`（`start_candidate_stack.sh:627`）再 `alembic -c alembic.ini upgrade head`（`:628`），但**沒有先呼叫 `export_process_env`**——`DATABASE_URL` 到 `start_stack()` 內的 `:505` 才被導出。對照 `reset_candidate()` 是正確順序（`:579` export ⇒ `:584` create ⇒ `:585` upgrade）。實測後果：第一次 `--candidate-id c2-20261124` 的 alembic 依 `backend/.env` 去連 `localhost:5432`，`psycopg2.OperationalError: Connection refused`，launcher **rc=1**（`.local/candidate-c2-logs/start.log`）。因為 5432 當時（且至今）0 聆聽，**沒有任何 DDL 落到隔離庫之外的地方**；被放棄的 `alethra_personal_e2e_candidate_c2_20261124` 現測 **public tables = 0**、無 `alembic_version`，**原样保留不刪**。最小修正（交 owner 決定，本輪不套用）：在 `:627` 之前插入一行 `export_process_env`。
2. **全部閘通過後 `print_ready` 仍讓 launcher 以 1 退出**。`new_candidate` 把 head 寫進 profile 的 `EXPECTED_SCHEMA_HEAD`，但 shell 變數從未賦值（只有 `require_profile` 會設，`--new-candidate` 不走它），而 `print_ready` 在 `:529` 引用 `$EXPECTED_SCHEMA_HEAD` ⇒ `set -u` 下 `unbound variable`，指令碼在 `stage full-candidate: … passed` **之後**死掉（`.local/candidate-c2-logs/start2.log`）。這讓「ready」與「rc=0」在 new 模式下不同義；本輪以三道閘的**實讀回**為準，不以 launcher 退出碼為準。
3. 另記一筆觀察（非缺陷判定）：`runtime-identity.env` 記 `FRONTEND_PID=60906`，實際聆聽 3101 的是其子行程 60910；`verify-runtime` 仍判 `RUNTIME_BINDING=verified`。

### 21.3 C-DDL：PASS（DEV，PostgreSQL 真實遷移路徑）

第二次以 `--candidate-id c2b-20261124` 起棧，並在外部先 `export DATABASE_URL=` 指向該隔離庫（把 DDL **收窄**到批准的那個庫，不是放寬）。`migrate.log` 落點：`.local/candidate/c2b-20261124/migrate.log`。

| 讀回 | 實測 |
|---|---|
| `alembic_version`（直接對 54351 查） | `e5f6g7h8i9j0` |
| public tables | **83** |
| `uq_agent_activation_approval_live_job` | 存在，`CREATE UNIQUE INDEX … (job_id) WHERE ((decision)::text = 'approved'::text)` ⇒ 第 24 輪那支 revision（`a7c1e4f9b2d5_add_model_activation_approvals.py:62-74`）在 **PG 側**的 predicate 落地了 |
| 紙上執行唯一鍵 | 實際名字是 **`uq_agent_paper_execution_intent`**（`UNIQUE (skill_unit_id, job_id, candidate_id)`），與 `e5f6g7h8i9j0_add_agent_paper_executions.py:170-172` 的 `sa.UniqueConstraint` 一致；我先前猜的 `uq_agent_paper_execution_identity` **不存在**（教訓：索引名要現讀，不靠記憶） |
| skill_unit 索引命名分歧 | PG 侧為自動名 `ix_agent_model_activation_approvals_skill_unit_id` vs 明示名 `ix_agent_paper_execution_skill`——與 §20 記錄的已知分歧相同，**未改名** |
| 種子副作用（披露） | `seed.log`：合成 owner `92124d23-…`／205 筆決策／41 個時刻／`consent: granted (fixture)`；另建 empty 與 thin 帳號（`candidate.empty@…`／`candidate.thin@…`）。全部寫在隔離庫內，帳號是 launcher 自己產生的 `example.com` 合成身分，**不是本人帳號** |

### 21.4 三道核對閘：全綠（起棧後立即讀）

| 閘 | 實測 |
|---|---|
| `/health/ready` | HTTP **200**，`status:"ready"`，`database:"connected"`（1.95 ms），`schema:"compatible"` 且 **`alembic_revision":"e5f6g7h8i9j0"`** |
| 服務身分＝目前工作樹 | 同回應的 `identity.code_commit = 5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a`、`code_dirty: true`、`paths 284`／`operations 329` |
| CORS 預檢 | `OPTIONS /api/v1/agent-strategy/training/jobs/<uuid>/activation`（`Origin: http://127.0.0.1:3101`）⇒ **`access-control-allow-origin: http://127.0.0.1:3101`**＋`allow-credentials: true`＋`allow-methods` 含 PUT |
| schema-head 讀回 | 200 回應與直接 SQL 皆 `e5f6g7h8i9j0`（＝批准預期，也是 profile 的 `EXPECTED_SCHEMA_HEAD`） |
| 進程／心跳 | backend 60789、worker 60849（`state idle`）、frontend 60906；`runtime-identity.env` `STATUS=ready` |

### 21.5 C-自動化：**22 collected／0 passed／22 skipped／0 failed，rc=0**（DEV）

命令（只三支，無 `--list`，無其他 spec）：
`cd frontend && E2E_BROWSER_CHANNEL=chrome E2E_FRONTEND_BASE=http://127.0.0.1:3101 E2E_BACKEND_BASE=http://127.0.0.1:8101 node_modules/.bin/playwright test --reporter=list,json e2e/manual-decision-lineage.spec.ts e2e/daily-estimate-journey.spec.ts e2e/agent-approval-paper-journey.spec.ts`（node 20.20.2；**E2E_CRED_FILE／E2E_ACTIVATION_JOB／E2E_PAPER_EXECUTION_SKILL 未填**，`E2E_DAILY_ESTIMATE_SKILL` 為**明確 unset**）

| spec | collected | passed | skipped | failed | skip 宣告行 |
|---|---|---|---|---|---|
| `manual-decision-lineage.spec.ts` | 6 | 0 | 6 | 0 | `:109` |
| `daily-estimate-journey.spec.ts` | 6 | 0 | 6 | 0 | `:104` |
| `agent-approval-paper-journey.spec.ts` | 10 | 0 | 10 | 0 | `:107`（READ＋WRITE）；WRITE 群另有 `:608` 閘 |
| 合計 | **22** | **0** | **22** | **0** | Playwright `stats`: `expected 0, skipped 22, unexpected 0, flaky 0`，`duration 676 ms`；**exit code 0** |

逐字 skip 原因（取自 `e2e-report.json` 的 `annotations[type=skip].description`，清單存 `.local/candidate-c2-logs/e2e-skip-reasons.txt`）：

* x22（三支檔各 6／6／10）：`tier B needs the real stack: E2E_EMAIL / E2E_PASSWORD are absent. Start it with start_e2e_research_flow.sh (Docker) or the wheel stack in docs/approval-package-2026-11-16-full-stack-execution.md and pass E2E_CRED_FILE=.local/e2e-credentials.env.`
* x4（`agent-approval-paper-journey.spec.ts:608`，WRITE 群）：`寫入組需要 owner 親自指定：E2E_PAPER_EXECUTION_SKILL=<技能帳號 id> 與 E2E_ACTIVATION_JOB=<job id>。兩者缺一就整組跳過——自動化不替本人決定哪一個帳號的紙上書本會被寫入，也不替本人決定要對哪一個產物提執行。`

**這一欄能證與不能證**：`duration 676 ms` 且 `backend.log` 只服務過我自己的 5 個請求（`3× GET /health/ready`、`1× GET /health`、`1× OPTIONS …/activation`）、`frontend.log` 只有 launcher 閘的 `GET /login`／`GET /` ⇒ **瀏覽器從未被啟動，三支 spec 一行斷言都沒執行**。所以本輪**不得**稱「瀏覽器旅程已驗」；卡 C 的自動化產出是「起棧＋DDL＋三道閘＋spec 可收集、其門在哪」，旅程本體仍是待本人。另：`daily-estimate-journey.spec.ts:109-116` 那句扣額專用 skip **未開火**——檔級 tier-B 閘（`:104`）先攔下，逐字原文以該行為準，我不在此重述也不代填。

### 21.6 C-本人操作：**待本人／NOT RUN**

代理未填 `E2E_CRED_FILE`（＝本卡的停止線），也未按任何同意／撤回／授權按鈕。交本人的精確現場：

1. 起棧（本輪已驗證可行的順序，含 21.2-1 的必要收窄）：
   `cd /Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra`
   `export DATA_PROVIDER=demo AI_PROVIDER=mock RESEARCH_AUTH_BYPASS_ENABLED=false LIVE_TRADING_ENABLED=false`
   `export CANDIDATE_DB_HOST=127.0.0.1 CANDIDATE_DB_PORT=54351 CANDIDATE_DB_USER=postgres`
   `export DATABASE_URL="postgresql+psycopg2://postgres@127.0.0.1:54351/alethra_personal_e2e_candidate_c2b_20261124"`（**若沿用既有 profile 請改用 RESUME：`./start_candidate_stack.sh`，它唯讀、不會重建**）
   叢集若未跑：`.local/pgvenv/lib/python3.11/site-packages/pgserver/pginstall/bin/pg_ctl -D .local/candidate-pg-c2-20261124 -l .local/candidate-pglogs-c2-20261124/pg.log -w -o "-p 54351 -c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp/algc2" start`
2. 憑證檔（**由本人建立**，代理不代填）：`frontend/.local/e2e-credentials.env` 內寫 `E2E_EMAIL=`／`E2E_PASSWORD=`。本輪 launcher 已把合成身分寫在 `.local/candidate/credentials-c2b-20261124.env`（0600）的 `CANDIDATE_EMAIL`／`CANDIDATE_PASSWORD`——**值未進入本節、未進入任何 log**。
3. 三個入口 URL（棧起好後）：`http://127.0.0.1:3101/login`、`http://127.0.0.1:3101/workspace`（決策血緣／每日估計面板）、`http://127.0.0.1:3101/agent-strategy`（批准＋紙上執行面板）。本人要親按的是：`daily-estimate-acknowledge`、`daily-estimate-revoke-all`、`activation-approve`、`activation-revoke-confirm`。
4. 跑法：把 21.5 那條命令加上 `E2E_CRED_FILE=<本人的憑證檔>`，若要寫入組再另加 `E2E_ACTIVATION_JOB=<job id>` 與 `E2E_PAPER_EXECUTION_SKILL=<技能帳號 id>`，每日估計扣額再另加 `E2E_DAILY_ESTIMATE_SKILL=<skill_unit_id>`（**只在本人批准扣額時**）。
5. 完成後由代理讀回持久結果（`agent_paper_executions`／`agent_model_activation_approvals` 的列、`/journal/unified`、面板截圖），並把 4 個 verdict 補成「本人已執行」那一欄。

### 21.7 C-可能扣額：**NOT RUN**

`E2E_DAILY_ESTIMATE_SKILL` 在執行環境中**明確 unset**（不是留空），且 tier-B 檔級閘先攔 ⇒ 計費路徑（`app/services/daily_estimates.py` 的 reserve／settle、`app/api/personal_models.py` 的 ai_usage 寫入）**一筆未觸**。**未扣額**是「沒走進那個分支」，不是「扣了又回滾」。

### 21.8 停止線與證據現場（未刪任何檔）

* `./stop_candidate_stack.sh` rc **0**：`stopping backend pid 60789 (SIGTERM)`／`worker 60849`／`frontend 60906`，輸出明言 `database and its progress were NOT removed`；`runtime-identity.env` → `STATUS=stopped`。
* 只停本輪自己啟動並記錄的 pid；停後 **8101 聆聽 0、3101 聆聽 0**（4 個 pid 全 gone）。
* 本輪的 PG（postmaster 60005）以 `pg_ctl -m fast stop` 停止，rc 0，**54351 聆聽 0**；PGDATA 與兩個候選庫原样留盤。平行線 54342（pid 73353）**未接、未停、未讀**。
* 現場：`.local/candidate-c2-logs/{start.log,start2.log,e2e-list.log,e2e-report.json,e2e-skip-reasons.txt}`、`.local/candidate/c2b-20261124/{migrate.log,seed.log,backend.log,frontend.log,worker.log,worker-heartbeat.json}`、`.local/candidate/candidate-profile.env`（0600）、`.local/candidate/runtime-identity.env`（0600）、`.local/candidate-pglogs-c2-20261124/{initdb.log,pg.log}`。
* 本節與 log 均**不含**密碼、Bearer token 或完整程序環境（SQLAlchemy 的 URL repr 本身遮罩密碼，且本節一律不印 URL）。

### 21.9 結語

**C-DDL＝PASS（DEV）**、**三道閘＝全綠**、**C-自動化＝22 collected／22 skipped／rc 0（瀏覽器未啟動，旅程本體未驗）**、**C-本人操作＝待本人**、**C-可能扣額＝NOT RUN**。卡 C 沒有產生任何瀏覽器行為證據；它產生的是「隔離庫上 PG 遷移真能落地、門在哪一行、以及launcher 的兩個實測缺陷」。下一步只需要 owner 一個動作：按 21.6 填 `E2E_CRED_FILE` 後重跑同一條命令（三支 spec、其餘條件本輪已全部備妥）。本輪**未做** Git stage／commit／push，**未跑** PG 併發卡 A–D／cron／整套重釘。
