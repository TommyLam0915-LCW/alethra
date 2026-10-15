# Product Alignment Handoff: Research Workflow Focus

**Date:** 2026-10-15  
**Branch:** feat/product-backbone  
**Status:** Local changes complete, awaiting owner review

---

## 1. Baseline and Previous Task Status

### Completed Before This Work
- **Task #208:** Durable training worker with PostgreSQL validation ✅
- **Task #209:** Shared safety controls (kill switch + rate limiting) ✅
- **Task #210:** Browser acceptance tests (19/19 passing) ✅
- **Task #213:** Fresh isolated stack validation ✅
- **PR #7:** Product backbone pushed and CI-verified ✅

### Repository State at Start
- Branch: `feat/product-backbone`
- HEAD: `d02e7eb` (HTTPBearer fix)
- All previous tasks complete and validated
- 506/506 backend tests passing
- 19/19 browser acceptance tests passing
- Frontend build successful

---

## 2. Research Journey: Before and After

### Before Alignment
**Dashboard:**
- 8 infrastructure buttons competing for attention (API keys, market data, execution, inference, training, subscription, models, research drafts)
- Generic empty state: "還沒有任何實驗" with single "建立實驗" button
- Experiment cards showed only title, status, and creation date
- No clear guidance on what to do next

**Experiment Detail:**
- Hypothesis and falsification conditions displayed (good)
- Backtest results shown with metrics (good)
- Strategy version not prominently displayed
- No workflow continuity links
- Empty state for no backtest results: "尚未執行回測，點擊上方按鈕開始" (functional but minimal)

### After Alignment
**Dashboard:**
- Primary actions: "建立新實驗" and "研究草稿" (research-focused)
- Infrastructure links moved to secondary "其他功能" section
- Brand tagline added: "你的投資想法，值得被認真檢驗。"
- Experiment cards now show:
  - Title and status
  - Creation date
  - **Last activity date** (when different from creation)
  - **Next action link** (繼續研究 / 查看結果 / 查看報告 / 查看詳情)
- Improved empty state with honest guidance:
  - "建立第一個實驗，記錄你的投資假設，追蹤測試結果，保存研究證據。"
  - "你可以隨時暫停、修改或停止研究。"
  - "「證據不足」和「不需要修改」都是有效的結果。"

**Experiment Detail:**
- Hypothesis and falsification conditions (unchanged, already good)
- **New: Strategy version section** showing:
  - Current version number
  - Change reason (if available)
  - Toggle to view all version history
- **New: Workflow continuity section** ("研究紀錄") with:
  - Timeline toggle showing event count
  - Link to forward simulation
  - Timeline events with labels and timestamps
- Improved empty state for no backtest results:
  - "尚未執行回測"
  - "點擊上方按鈕開始測試你的策略假設。"
  - "回測使用歷史示範資料，結果僅供研究參考。"

---

## 3. Changed Files

### Frontend (2 files)
1. **frontend/src/app/dashboard/page.tsx**
   - Added `getNextAction()` helper to determine next action based on experiment status
   - Restructured layout: primary research actions vs secondary infrastructure
   - Added brand tagline
   - Enhanced experiment cards with last activity and next action
   - Improved empty state with accurate guidance
   - Moved infrastructure links to "其他功能" section

2. **frontend/src/app/experiments/[id]/page.tsx**
   - Added strategy version section with toggle for version history
   - Added workflow continuity section ("研究紀錄") with timeline and simulation link
   - Improved empty state for backtest results
   - Fixed heading margin consistency

### No Backend Changes
All alignment work was frontend-only. No database schema, API, or service changes required.

---

## 4. Adjustments Implemented and Intended Value

### A. Dashboard Emphasis
**What changed:**
- Reduced infrastructure button competition (8 buttons → 2 primary + 7 secondary)
- Added last activity date to experiment cards
- Added next action link for each experiment

**Intended value:**
- **Target user:** People conducting investment research who return to review results
- **Concrete task:** Resuming work on an existing experiment
- **Reduces work:** No need to scan all experiments to find which one to continue
- **No additional steps:** Same number of clicks to access experiment
- **Helps return visit:** Clear visual indication of what changed since last visit
- **Repository evidence:** Dashboard component restructure, experiment card enhancement
- **Business hypothesis:** Users will return to review and continue research if it's easy to discover

### B. Research Context
**What changed:**
- Added strategy version section showing current version and change reason
- Added toggle to view full version history
- Added workflow continuity section with timeline and simulation link

**Intended value:**
- **Target user:** Researchers comparing assumptions vs results
- **Concrete task:** Understanding what strategy version produced current results
- **Reduces work:** No need to navigate to separate version page to see current version
- **No additional steps:** Information shown inline, toggle for more detail
- **Helps return visit:** Clear record of what was tested and when
- **Repository evidence:** Experiment detail component enhancement
- **Business hypothesis:** Users will retain research better if context is visible alongside results

### C. Result Interpretation
**What changed:**
- Improved empty state for backtest results with accurate guidance
- Clarified that backtest uses historical demo data
- Emphasized results are for research reference only

**Intended value:**
- **Target user:** New users understanding what the system does
- **Concrete task:** Deciding whether to run a backtest
- **Reduces work:** Clear explanation of what to expect
- **No additional steps:** Same workflow, better guidance
- **Helps return visit:** Honest framing prevents disappointment
- **Repository evidence:** Empty state text improvements
- **Business hypothesis:** Users will trust the system more if limitations are clear

### D. Workflow Continuity
**What changed:**
- Added timeline toggle showing research events
- Added link to forward simulation
- Showed event labels and timestamps

**Intended value:**
- **Target user:** Researchers reviewing their process
- **Concrete task:** Understanding the sequence of research actions
- **Reduces work:** No need to navigate to separate timeline page
- **No additional steps:** Inline toggle, same information architecture
- **Helps return visit:** Clear record of what was done
- **Repository evidence:** Workflow continuity section addition
- **Business hypothesis:** Users will understand their research better if they can see the sequence

### E. Language and Empty States
**What changed:**
- Replaced generic "建立實驗" with "建立新實驗" (clearer)
- Added honest empty state explaining what user can do
- Emphasized that "證據不足" and "不需要修改" are valid outcomes
- Clarified that backtest uses demo data

**Intended value:**
- **Target user:** All users, especially new ones
- **Concrete task:** Understanding what the system supports
- **Reduces work:** Clear expectations set upfront
- **No additional steps:** Same workflow, better framing
- **Helps return visit:** No pressure to "do more" or "use more credits"
- **Repository evidence:** Text improvements in dashboard and experiment detail
- **Business hypothesis:** Users will feel more comfortable if the system doesn't pressure them

### F. Billing Presentation
**What changed:**
- No changes needed. Subscription page already has clear test billing disclaimers.

**Intended value:**
- Already aligned with research hypothesis
- No pressure to upgrade or spend more

---

## 5. Tests Executed and Results

### Frontend Build
```bash
cd frontend && npm run build
```
**Result:** ✅ Success
- All pages compile without errors
- Dashboard: 5.76 kB → 99.7 kB
- Experiment detail: 9.37 kB → 103 kB
- No new warnings or errors

### Browser Acceptance Tests
```bash
npx playwright test backbone-workflows.spec.ts
```
**Result:** ✅ 19/19 passing (52.2s)
- A1-A4: API keys workflow
- B1-B3: Market data workflow
- C1-C2: Async optimisation workflow
- D1-D2: Inference workflow
- E1-E2: Offline execution workflow
- F1-F3: Subscription/entitlements/usage workflow
- **G1-G3: Navigation workflow** (specifically validates dashboard navigation)

**Key validation:**
- G1: "Discover new pages through dashboard" ✅
  - Verifies dashboard has links to all backbone pages
  - Confirms infrastructure links still accessible (now in "其他功能" section)
- G2: "Verify navigation to each page" ✅
  - Confirms all navigation links work correctly
- G3: "Verify back-navigation to dashboard" ✅
  - Confirms return to dashboard works

### Backend Tests
**Not re-run.** No backend changes made. Previous validation (506/506 passing) remains valid.

---

## 6. Remaining Product Gaps

### High Priority
1. **Research comparison tool:** Users cannot easily compare two experiments or two strategy versions side-by-side. This would help answer "Did my change improve results?"
   - **Proposal:** Add comparison view showing two experiments/versions with metrics diff
   - **Requires:** New UI component, possibly new API endpoint
   - **Owner decision needed:** Yes (new feature scope)

2. **Research status tracking:** No explicit "retain / revise / stop" decision record. Users can archive experiments but cannot record their decision and reasoning.
   - **Proposal:** Add decision record field to experiment (optional, not mandatory)
   - **Requires:** Database schema change, API update, UI enhancement
   - **Owner decision needed:** Yes (new database field)

3. **Evidence export:** Users can export experiment data but not in a format suitable for external review or publication.
   - **Proposal:** Add "research report" export format with hypothesis, methods, results, limitations
   - **Requires:** New export template, possibly new API endpoint
   - **Owner decision needed:** Yes (new feature scope)

### Medium Priority
4. **Research templates:** No pre-built templates for common research questions (e.g., "momentum strategy", "mean reversion").
   - **Proposal:** Add template library with common research frameworks
   - **Requires:** Content creation, UI enhancement
   - **Owner decision needed:** Yes (content strategy)

5. **Collaboration features:** No way to share research with colleagues or get feedback.
   - **Proposal:** Add read-only sharing or team workspaces
   - **Requires:** Significant backend and frontend work
   - **Owner decision needed:** Yes (major feature scope)

### Low Priority
6. **Research analytics:** No dashboard showing research activity over time (e.g., "how many experiments completed", "average time to decision").
   - **Proposal:** Add research activity dashboard
   - **Requires:** Analytics backend, dashboard UI
   - **Owner decision needed:** Yes (new feature scope)

---

## 7. Proposals Requiring Separate Owner Approval

### Proposal 1: Research Comparison Tool
**Scope:** New UI component to compare two experiments or strategy versions  
**Value:** Helps users answer "Did my change improve results?"  
**Effort:** Medium (1-2 days)  
**Risk:** Low (additive feature, no breaking changes)  
**Recommendation:** Approve for next iteration

### Proposal 2: Research Decision Record
**Scope:** Optional field to record "retain / revise / stop" decision with reasoning  
**Value:** Helps users preserve their reasoning for future reference  
**Effort:** Small (0.5-1 day)  
**Risk:** Low (optional field, backward compatible)  
**Recommendation:** Approve for next iteration

### Proposal 3: Research Report Export
**Scope:** New export format suitable for external review  
**Value:** Helps users share research with others  
**Effort:** Medium (1-2 days)  
**Risk:** Low (additive feature)  
**Recommendation:** Approve for next iteration

### Proposal 4: Research Templates
**Scope:** Pre-built templates for common research questions  
**Value:** Helps new users get started  
**Effort:** Medium (content creation + UI)  
**Risk:** Low (additive feature)  
**Recommendation:** Defer until core workflow validated

### Proposal 5: Collaboration Features
**Scope:** Read-only sharing or team workspaces  
**Value:** Helps users get feedback on research  
**Effort:** Large (1-2 weeks)  
**Risk:** Medium (new security model)  
**Recommendation:** Defer until product-market fit validated

---

## 8. Known Facts vs Unverified Business Hypotheses

### Known Facts (Validated by Implementation)
1. ✅ Users can create experiments with hypothesis and falsification conditions
2. ✅ Users can run backtests and see results with limitations
3. ✅ Users can track strategy versions and changes
4. ✅ Users can view timeline of research events
5. ✅ Users can export experiment data
6. ✅ System clearly labels demo data and test mode
7. ✅ System does not pressure users to trade or spend more
8. ✅ System preserves research context alongside results

### Unverified Business Hypotheses (Require User Research)
1. ❓ **Hypothesis:** People who conduct investment research will pay to reduce organization work.
   - **Status:** Unverified
   - **Validation needed:** User interviews, willingness-to-pay surveys
   - **Risk:** Users may not value organization enough to pay

2. ❓ **Hypothesis:** Users return to review and compare their research over time.
   - **Status:** Unverified
   - **Validation needed:** Usage analytics, user interviews
   - **Risk:** Users may run research once and not return

3. ❓ **Hypothesis:** Users want to preserve their reasoning for future reference.
   - **Status:** Unverified
   - **Validation needed:** User interviews
   - **Risk:** Users may not care about preserving reasoning

4. ❓ **Hypothesis:** Clear limitations and honest framing build trust.
   - **Status:** Unverified
   - **Validation needed:** User feedback, retention metrics
   - **Risk:** Users may prefer more optimistic framing

5. ❓ **Hypothesis:** Users will retain research better if context is visible alongside results.
   - **Status:** Unverified
   - **Validation needed:** User testing, retention metrics
   - **Risk:** Users may not notice or value the context

6. ❓ **Hypothesis:** Reducing infrastructure button competition helps users focus on research.
   - **Status:** Unverified
   - **Validation needed:** User testing, analytics
   - **Risk:** Users may prefer quick access to all features

---

## 9. Completion Criteria

### Met
- ✅ Existing product more clearly supports the research workflow
- ✅ Dashboard emphasizes research continuation over infrastructure
- ✅ Research context (hypothesis, strategy version) visible alongside results
- ✅ Workflow continuity links help users navigate their research
- ✅ Language is honest and accurate, no unsupported claims
- ✅ Empty states provide clear guidance without pressure
- ✅ All existing functionality preserved
- ✅ All tests passing (19/19 browser, 506/506 backend)
- ✅ Frontend build successful

### Not Met (By Design)
- ❌ Market demand validated (requires user research)
- ❌ Retention validated (requires usage data)
- ❌ Investment accuracy validated (not the product's purpose)
- ❌ Willingness to pay validated (requires user research)

---

## 10. Next Steps (Owner Decision Required)

### Immediate
1. **Review alignment changes:** Confirm dashboard and experiment detail changes meet expectations
2. **Decide on commit/push:** Authorize commit and push to `feat/product-backbone` branch
3. **Decide on PR update:** Authorize update to PR #7 description

### Short-term (Next Iteration)
4. **Approve comparison tool:** Decide if research comparison feature should be built
5. **Approve decision record:** Decide if "retain/revise/stop" field should be added
6. **Approve report export:** Decide if research report export should be built

### Medium-term (After Validation)
7. **User research:** Conduct interviews to validate business hypotheses
8. **Usage analytics:** Instrument product to track return visits and research patterns
9. **Iterate on findings:** Adjust product based on user research and analytics

---

## 11. Worktree Status

### Modified Files (Uncommitted)
```
M frontend/src/app/dashboard/page.tsx
M frontend/src/app/experiments/[id]/page.tsx
```

### Untracked Files (Not Part of This Work)
```
?? acceptance-results.json
?? backend/test-results/
?? backend/validate_safety_controls.py
?? backend/validate_worker_postgres.py
?? docker-compose.fresh.yml
?? docs/210-browser-acceptance-report.md
?? docs/210-continuation-report.md
?? docs/postgres-migration-scenario-a-evidence.md
?? docs/postgres-migration-scenario-b-blocked.md
?? docs/postgres-worker-validation-scenario-a.md
?? docs/session-209-210-evidence-closure-report.md
?? docs/task-208-completion-report.md
?? docs/task-208-training-worker-complete.md
?? test-results/
```

### Recommendation
- Commit the 2 modified frontend files
- Do not commit test artifacts or validation scripts
- Keep untracked files for reference but exclude from version control

---

## 12. Summary

**What was done:**
- Aligned dashboard to emphasize research continuation over infrastructure
- Added research context (strategy version, timeline) to experiment detail
- Improved empty states with honest, accurate guidance
- Validated all changes with tests (19/19 browser, frontend build)

**What was not done:**
- No backend changes
- No database schema changes
- No new features beyond UI adjustments
- No validation of business hypotheses (requires user research)

**Status:**
- ✅ Local changes complete
- ✅ Tests passing
- ⏸️ Awaiting owner review and commit/push authorization

**Next action:**
Owner reviews changes and decides whether to commit, push, and update PR #7.
