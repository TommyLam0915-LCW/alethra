# PR #3 — E/G/M Test Repair Verification (2026-10-13)

Sanitised summary of the post-repair browser verification for PR #3 (`feat/phase2a-draft-review-ui`). This document records what was changed, what was run, what passed, what failed, and why.

## Scope of changes (local, uncommitted)

| File | Change |
|------|--------|
| `frontend/e2e/research-drafts.spec.ts` | Repaired vacuous E/G/M tests; hardened `getAuthToken`/`createDraft` fixture helpers to throw on failure |
| `docker-compose.acceptance.yml` | Added top-level `name: alethra-acceptance` for explicit Compose project identity |
| `docs/pr3-browser-acceptance.md` | Added post-run repair note; corrected E coverage note; added F coverage note |
| `docs/pr3-playwright-acceptance-report.md` | Normalised secret-wording constraint; appended dated addendum |
| `docs/pr3-eqm-repair-verification.md` | This document |

No product code was modified. `acceptance-results.json` (untracked) was preserved untouched.

## Code-state identifiers

- Branch: `feat/phase2a-draft-review-ui`
- Git HEAD: `3028d1f4266ef7c86a1996d96c76635d4838c7bf` (all changes are local worktree edits on top of this commit)
- SHA-256:
  - `frontend/e2e/research-drafts.spec.ts` = `7af6efb84c941695490b955c67b6939478f3de306024bb058d3e1f185cb00549`
  - `docker-compose.acceptance.yml` = `0d113c0aa4b28830bef392e373f5a44cc56ab4652403edb43007ab73cfeafb4e`
  - `docs/pr3-browser-acceptance.md` = `9672bba0b0f9402eb58070406542a467031b9f60f84e9d999600e29ea88df0a3`
  - `docs/pr3-playwright-acceptance-report.md` = `4546c274fa97a9ee7303b7f0816de96d40ec370ba35160266c2a55d0ce6a4f6f`

## Exact E/G/M assertions after repair

**E. Zero Values** — `should preserve zero commission and slippage`
- Creates a test-owned fixture via API with `commission_rate: '0'`, `slippage_rate: '0'`; fixture creation failures fail the test.
- Verifies via `getDraft` that both values persist as `'0'`.
- Logs in via UI, navigates to the draft detail page.
- Unconditionally asserts the read-only grid shows `手續費率` and `滑價率` labels (exact text) and their value cells (`following-sibling::div`) contain exactly `0`.

**G. Acceptance and Finalization** — `should accept a draft through the UI and finalize it as read-only`
- Creates a test-owned draft-status fixture via API; verifies `status === 'draft'` before the UI flow.
- Pre-acceptance assertions: badge `草稿`, heading `審閱決定`, button `編輯` all visible.
- Performs acceptance through the UI: clicks `接受草稿`, asserts the disclosure text `這不會建立可執行的策略。` is visible, fills the notes textarea, clicks `確認接受`.
- Post-acceptance assertions: success message matching `/草稿已接受/` visible; badge `已接受` visible; `編輯` button count 0; `儲存` button count 0; `研究助手問題` heading count 0; `審閱決定` heading count 0; `套用草稿欄位` heading visible. No disjunctions.

**M. Deletion** — `should cancel then confirm deletion of a disposable draft`
- Creates a test-owned disposable fixture (unique per-run title) via API; verifies `status === 'draft'`.
- Scopes to the deletion card by its `刪除草稿` heading; asserts the persistent warning `刪除草稿會一併移除所有修訂記錄與稽核事件。此操作無法復原。` is visible.
- Opens confirmation; asserts warning `確定要刪除此草稿嗎？所有修訂與稽核事件將一併刪除。` is visible.
- Cancels: asserts the confirm warning disappears, `刪除此草稿` button returns, the draft title heading is still visible, and the URL still contains the draft id.
- Re-opens confirmation, clicks `確認刪除`; waits for redirect to the drafts list; asserts the `研究草稿` heading, the deleted title's list entry count is 0, and direct navigation to the draft URL shows `草稿不存在`.

## Acceptance-stack isolation

- `docker-compose.acceptance.yml` now declares `name: alethra-acceptance` (Compose v5.2.0), giving the acceptance stack an explicit project identity distinct from the directory-default project name previously shared with the dev stack file.
- Before the change, the previous acceptance containers had been running under the ambiguous project identity (label `com.docker.compose.project.config_files=…/docker-compose.acceptance.yml` verified on all three). After verifying no dev stack was running, exactly those three label-verified containers were removed individually by name (`docker rm -f d705b961-db-1 d705b961-backend-1 d705b961-frontend-1`) — not a broad `compose down`. No volumes were removed: `d705b961_postgres_acceptance_data` and `d705b961_postgres_data` are retained.
- The new stack runs as project `alethra-acceptance` with its own network (`alethra-acceptance_default`) and fresh volume (`alethra-acceptance_postgres_acceptance_data`). Compose project identity is confirmed by container labels on all three containers (`alethra-acceptance-db-1`, `alethra-acceptance-backend-1`, `alethra-acceptance-frontend-1`). Starting/stopping the acceptance stack cannot touch dev-stack resources because the projects and their resources are disjoint.
- Stack state at verification time: db healthy, backend and frontend up, ports 5433/8001/3001.

## Seed/bootstrap steps (no secrets)

1. `docker compose -f docker-compose.acceptance.yml up -d --build` — creates the `alethra-acceptance` project, fresh volume, and containers.
2. Wait for backend readiness (e.g. `GET http://localhost:8001/docs`). Database tables are created automatically by the backend at startup (`Base.metadata.create_all`); no migration step is needed.
3. Register the two disposable fixture accounts via `POST /api/auth/register` with emails `user_a_test@example.com` and `user_b_test@example.com`. Passwords are whatever the suite reads from `ACCEPTANCE_PASSWORD_A` / `ACCEPTANCE_PASSWORD_B` (the spec contains disposable fallback values; they are intentionally not reproduced in this document).
4. Verify seeding by logging in to each account via `POST /api/auth/login` (expect HTTP 200). Both accounts verified at seed time (HTTP 201 register, HTTP 200 login).

## Tool versions

- Playwright 1.64.0, Chromium (cache `chromium-1248`), 1 worker
- Node.js v26.5.0
- Docker Compose v5.2.0
- OS: macOS (arm64), host Darwin 25.5.0

## Commands and results

Focused run (first, cold pages):

```
npx playwright test --grep "E\. Zero Values|G\. Acceptance and Finalization|M\. Deletion" --reporter=list
```

- 3 tests: E PASS (6.0s), G PASS (3.1s), M FAIL (31.8s) — 2 passed, 1 failed, 47.5s total.

Full suite:

```
npx playwright test --reporter=list
```

- 14 total — 13 passed, 1 failed (M), 1.6m. Actual count reported; no scenarios silently skipped.

`git diff --check`: clean (no whitespace errors).

**M failure analysis (verified, not speculative):** M passes every cancellation-branch assertion, then times out at the confirm-deletion navigation wait. The page snapshot captured at failure shows the error `Failed to execute 'json' on 'Response': Unexpected end of JSON input`. Root cause is a product defect (below), confirmed by post-run API check: the fixture draft was no longer present in the drafts list (server-side deletion succeeded) while the UI reported failure and never navigated.

## Product defect found (owner decision pending)

- **Symptom:** UI-initiated draft deletion always fails from the user's perspective: an error message is shown, the page does not redirect, yet the draft is actually deleted server-side.
- **Cause:** `DELETE /api/v1/research-drafts/{id}` returns HTTP 204 No Content with an empty body (`backend/app/api/research_drafts.py:221`), but the frontend `apiFetch` calls `response.json()` unconditionally on successful responses (`frontend/src/lib/api.ts:33`), which throws on the empty body. `handleDelete` (`frontend/src/app/research-drafts/[id]/page.tsx`) catches the throw and displays it as an error.
- **Blast radius:** the only frontend caller of a 204 endpoint today is research-draft deletion (`api.researchDrafts.delete`); the other 204 DELETE endpoint (`model_research.py:513`) has no frontend caller.
- **Status:** product code intentionally not modified in this task; awaiting separate owner approval. This is why the old vacuous M test never caught the defect: it only exercised cancellation and cancelled via selectors that matched nothing.

## Retained evidence and hygiene

- Raw Playwright artifacts (screenshots, videos, error contexts) remain uncommitted under gitignored `frontend/test-results/` (`.gitignore:38`); `frontend/playwright-report/` is likewise gitignored (`.gitignore:37`). Nothing in this task commits traces, storage states, videos, or credential-bearing reports.
- No credentials, tokens, or password values appear in this document or the updated PR docs.
- `acceptance-results.json` (untracked) preserved untouched.

## Applicability of existing evidence

The existing evidence — 258 backend tests passing and a successful frontend production build, both recorded against commit `3028d1f` — remains applicable without a rerun: this task changed only the E2E spec (test-only code not exercised by either the backend suite or the production build), the acceptance compose file (not part of either build), and documentation. Rerunning both merely to refresh dates would not change any tested input.

## Remaining limitations

- M cannot pass until the deletion defect is fixed with owner approval; after a fix, re-run focused M and the full suite before merge.
- Scenario D's E2E coverage remains the empty-answer rejection path only (edit-form validation is enforced and tested at the API layer), as documented.
- Scenario F coverage remains label-presence for audit events (payloads, counts, and edit-correlation are not asserted), as documented.
- The full-suite M failure consumed its 30s timeout; total run time includes that delay.

## Resolution (2026-10-13, after owner approval)

The owner approved the product fix via the task "Alethra PR #3 — Fix 204 Delete Response and Reverify". The recommendation above (TARGETED VERIFICATION BLOCKED) was superseded by the results in this section after successful verification. The failed-run evidence above is preserved unmodified.

**Root cause (recap):** `apiFetch` parsed `response.json()` unconditionally on success; the backend's `DELETE` endpoints return 204 No Content with an empty body, so UI-initiated deletions always threw and `handleDelete` displayed failure despite successful server-side deletion.

**Verified before editing:** live `curl -i` on `DELETE /api/v1/research-drafts/{id}` returned `HTTP/1.1 204 No Content` with an empty body. Inspection found exactly two 204 endpoints in the backend contract (`research_drafts.py:221`, `model_research.py:513` — the personal-memory DELETE), each with one frontend caller.

**Exact fix (`frontend/src/lib/api.ts`, 3 edits, no other product changes):**
1. `apiFetch`: after the 401 and `!response.ok` error handling, a narrow branch returns `null` for `status === 204` instead of calling `response.json()`. JSON responses are still parsed; non-ok responses still throw the API error detail; malformed JSON from a JSON-returning response still throws (nothing broadly swallowed).
2. `researchDrafts.delete` → explicit `Promise<void>` wrapper (return type now represents no content).
3. `personalMemory.deactivate` → same explicit `Promise<void>` wrapper (same 204 contract; its caller ignores the return value).

**Focused regression coverage (`frontend/e2e/api-fetch.spec.ts`, new):** apiFetch regression tests using the existing Playwright runner (no new test framework installed; the project has no jest/vitest): 204 resolves `null` without JSON parsing; JSON success is parsed and returned; a 404 with `detail` rejects with the API error message; malformed JSON with HTTP 200 rejects (not silently hidden). 4/4 pass (1.1s standalone).

**Repaired M results:**
- First post-fix run: FAIL on the 30s test timeout while the dev server was recompiling after the `api.ts` change (the invalidation triggered a 58s full compile during M's navigation; the snapshot caught the detail page at `載入中...`). Backend logs for that window show the full flow completed anyway — UI `DELETE … 204 No Content`, redirect, list `GET 200`, final direct navigation `GET … 404` — i.e. the product fix worked and the failure was cold-server timing, not behavior.
- Immediate re-run on the warm server: **PASS (2.7s)** — all Section-4 requirements proven in the browser: warning and confirmation displayed, cancel preserves the draft, confirm deletes, UI redirects to the list, deleted title absent, direct navigation shows `草稿不存在`, and no deletion-failure message appears after successful deletion.

**Full browser + regression suite:** 18 total (14 browser acceptance tests + 4 apiFetch regression tests, executed with the Playwright runner) — **18 passed, 0 failed, 39.4s**. Actual count reported; nothing skipped.

**Frontend production build:** success (`npm run build`, exit 0; all routes compiled, including `/research-drafts` and `/research-drafts/[id]`).

**Backend tests:** not rerun — no backend code changed in this fix; the existing 258-test evidence remains applicable.

**Code-state identifiers (post-fix):**
- Git HEAD: `3028d1f4266ef7c86a1996d96c76635d4838c7bf` (fix is an uncommitted worktree edit on top)
- `frontend/src/lib/api.ts` = `b40065dc30f0f3ef7d065dabb4013ab0ad6adceaa4f540c02b9467cd5828001f`
- `frontend/e2e/api-fetch.spec.ts` = `a4b290b2871ec1559bf109d80a094e777fa5086c68bcb9555132a9b31cfa61c5`
- `frontend/e2e/research-drafts.spec.ts` = `7af6efb84c941695490b955c67b6939478f3de306024bb058d3e1f185cb00549` (unchanged from the repair task)
- `docker-compose.acceptance.yml` = `0d113c0aa4b28830bef392e373f5a44cc56ab4652403edb43007ab73cfeafb4e` (unchanged)
- Final documentation states: `docs/pr3-browser-acceptance.md` = `3e6a7a41ccc9adff1644af8e1fc8a0296274f0243b3c98d7e037db5c044d06dd`, `docs/pr3-playwright-acceptance-report.md` = `172afbd5a50ffb4d795689ec4a24b77b1c0b63012ea9772315bf2f2f348954fb`
- Stack: project `alethra-acceptance`, containers up and healthy throughout; `git diff --check` clean.

## Recommendation

**READY FOR OWNER REVIEW OF LOCAL FIX AND EVIDENCE** — all required browser checks pass (18/18), the deletion flow is verified end-to-end through the UI, regression coverage is in place, and the frontend build succeeds. No human code-review approval is claimed; the local fix and evidence await owner review.
