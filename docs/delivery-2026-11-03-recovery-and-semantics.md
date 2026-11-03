# 2026-11-03 — candidate recovery, environment quadruple, and remaining semantics

Scope: local candidate stack only. No push, no deploy outside `127.0.0.1`, no reset,
no seed, no credential change, no change to the shared e2e stack (`:8099`).

## 1. Outage and recovery (facts, not inference)

Observed before recovery:

- Kernel boot time `2026-11-03 14:14:09 HKT` (`kern.boottime`); the frontend log ends
  with ordinary `GET 200` lines and no shutdown record, i.e. the process was stopped by
  the host going down rather than by the application.
- Candidate backend log ends with `Shutting down … Application shutdown complete … Finished
  server process [86230]`; worker log ends with `Training worker stopped` at `14:13:58`.
- Docker daemon unreachable (`Cannot connect to the Docker daemon`), so the container
  `alethra-pg-test` (postgres:15-alpine, `127.0.0.1:54321`) was `Exited (255)`.
- No listener on `54321`, `8100` or `3100`.

The cause of the host going down is **not established** from these logs; only the
sequence (host down → Docker down → candidate stack down) is.

Recovery performed:

1. One Docker Desktop start; daemon ready after 10 s.
2. `docker start alethra-pg-test`; `pg_isready` at 4 s. The container's data volume was
   left untouched.
3. Pre-migration dump: `.local/candidate/backups/alethra_personal_e2e_candidate_20261029-pre-c4e5f6a7b8c9-20261103T145810+0800.dump`
   (1.4 MB, mode 0600, sha256 `187cb6ee3742fe29395daf4e1f9893a42db3ca297e9fdfc6a757fd17e50894dc`).
4. Additive migrations applied to that database only:
   `c3d4e5f6a7b8 → c4e5f6a7b8c9` (strategy study records) →
   `d5f6a7b8c9d0` (robustness study authorization link).
5. The runtime profile's `EXPECTED_SCHEMA_HEAD` was advanced from `c3d4e5f6a7b8` to
   `d5f6a7b8c9d0` (backup of the previous profile kept at
   `.local/candidate/candidate-profile.env.bak-pre-d5f6`). This is a local runtime
   record, not customer data.
6. `./start_candidate_stack.sh --plan` passed read-only preflight (`SEED=no`), then
   `./start_candidate_stack.sh` started backend, worker and frontend.

Data preserved, read from the database after migration: users 10, experiments 23,
paper portfolios 2, market-data bars 15148. No row was rewritten, no account recreated.

## 2. Environment quadruple

| Item | Value |
| --- | --- |
| Entry | `http://127.0.0.1:3100/login` (backend `http://127.0.0.1:8100`) |
| Alembic revision | `d5f6a7b8c9d0` (head); chain `c3d4e5f6a7b8 → c4e5f6a7b8c9 → d5f6a7b8c9d0` |
| `/health/ready` | `database=connected`, `schema=compatible`, `alembic_revision=d5f6a7b8c9d0` |
| `/openapi.json` sha256 (served bytes) | `4a3409438d50738db4bca82faa9bb164bc8fe28ff6cac36f602bc7152da3a256` (327398 bytes) |
| `/openapi.json` sha256 (canonical, keys sorted) | `d08ba3e7e58fca422e48782cdfd785ed3ec974c2dbc566f2969334330bf5f952` |
| Paths / operations | 239 paths / 281 operations |

The raw hash depends on key order; the canonical hash does not. Both are given so a
different fetch method cannot produce a false mismatch.

Operation delta against the previously deployed commit `04125ff` (generated from that
commit in a detached worktree, same interpreter): **0 added, 0 removed, 0 changed spec
nodes**. This round changed response semantics, not the route surface. The `03f34f53…`
fingerprint therefore belongs to an earlier build than `04125ff`; compare content
(paths/ops and canonical hash), not the raw hash, when re-reading it.

## 3. FB-21 v2 — permanent unreachability

- `POST /api/v1/experiments/{id}/robustness/plan` now returns a top-level `reachability`
  object `{required, limit, unlimited, entitlement_active, permanently_unreachable, reason}`,
  and `quota` repeats `permanently_unreachable` and `limit_block_reason`.
- Reachability is computed from the plan's stable `limit`, never from the transient
  `remaining`: `limit < required` or an inactive entitlement is permanent; a spent
  allowance is transient.
- Under the one-authorization-per-study rule, `required` is 1 for whole studies, so a
  limit-5 plan makes 8–12-backtest studies reachable. The limit itself was the
  adjustment; the 429 and plan now also carry the permanent label for cases where the
  limit or entitlement really does forbid the study.
- Live read-only probe on `:8100` (candidate owner, `parameter_sensitivity`,
  version of *BTC daily SMA 5/20*):
  `required_bars=21`, `reachability={required:1, limit:5, unlimited:false,
  entitlement_active:true, permanently_unreachable:false, reason:null}`,
  quota `limit=5, used=4, remaining=1`. The plan endpoint spent nothing: `used` stayed 4.

## 4. FB-24 — refusal message

The original sentence is kept verbatim with both numbers, and the executable path is
appended rather than substituted:

> This plan needs {required} research-run authorization but only {remaining} remain, so the
> study did not start. The whole study of {estimated_attempts} backtests is charged as this
> one unit, not per backtest. [permanent-block sentence, only when applicable] Check used
> versus limit at GET /api/v1/usage/quota?quota_type=research_run_limit&period=monthly.

The 429 body carries the same numbers as fields (`required`, `remaining`, `limit`, `used`,
`estimated_attempts`, `permanently_unreachable`, `limit_block_reason`, `check_url`). The
`/plans: []` self-report is no longer used in the message.

## 5. Journal — `order_state` and `position_status`

Each journal row (CSV and JSON) now carries:

- `order_state`: `pending` while the order can still execute, `terminal` once filled,
  cancelled or rejected. This describes the order.
- `position_status`: `open` or `closed` for the portfolio's position in that row's asset
  at export time, empty when no position row exists. This describes the position.

A terminal order can leave its position open; a closed position does not rewrite the
order's history. Live probe of the candidate owner's portfolio returned a 31-column CSV
whose first six columns are `record_type, record_classification, order_state,
position_status, portfolio_id, currency`.

## 6. Warm-up rule (①)

`required_bars` and the human-readable rule now come from one function:

- SMA: `long_window + 1`. Test pins long_window 20 → 21, and the rule text contains
  `long_window (20) + 1`. Live plan above returned 21 for the 5/20 version.
- Price-change fixed hold: `lookback_days + min(holding_days, 5) + 1`. Test pins
  lookback 3 / holding 8 → 9, and the rule text contains `min(holding_days, 5) (5)`.
- Templates with no numeric warm-up: planning floor 3, now described as
  "the engine's minimum slice length … a planning floor, not a signal look-back", so the
  copy no longer calls `buy_and_hold`'s 3 a signal warm-up.

## 7. ④ two books — status of the null rule

The rule is: `total_unrealized_pnl` is `null` (and `valuation.unpriced_assets` names the
assets) if and only if at least one open position has no mark; otherwise it is a real
number, `0.0` included when there are no positions.

Today's observation (`1.0727`, `mode: session_replay`, `unpriced_assets: []`) is the
inverse direction only. The null direction is pinned by existing tests:
`test_missing_mark_price_reports_null_and_names_the_asset` (mark removed after the order,
asserts `position.unrealized_pnl is None`, `total_unrealized_pnl is None`,
`unpriced_assets == ["DEMO-ALPHA"]`, `data_as_of is None`) and
`test_unadvanced_session_reports_null_rather_than_zero` (an unadvanced bound session).
This is test-level evidence, not a fresh production observation.

## 8. Lineage — exact predicate

`lineage_status` becomes `candidate_linked` only when all of these hold:

1. The order carries a strategy version: `decision.linked_experiment_version_id` or, if
   absent, `order.model_or_strategy_version_id`.
2. A candidate exists for the same owner, that strategy version and the order's
   instrument (created through `POST /api/v1/agent-strategy/candidates/collect`).
3. A current outcome references that exact order (`observed_order_id == order.id`) or one
   of its fills (`observed_fill_id`), recorded through
   `POST /api/v1/agent-strategy/candidates/outcomes` (requires the screening flag, which
   is on in this profile, an authenticated owner and write scope).

A shared execution date alone yields `date_only_unconfirmed` — traceability, not identity.
Declared functional-test orders are excluded before any of this.

Consequences, stated plainly:

- An order created by `POST /api/v1/paper-portfolio/{id}/orders` carries no version field,
  so it can **never** become `candidate_linked`; it stays `no_strategy_lineage`. A manual
  simulated order made that way will never enter the C dataset.
- A manual decision recorded inside a replay session bound to a locked version **does**
  carry `linked_experiment_version_id`, so it can become `candidate_linked` through the
  outcome step above. That is the customer-reachable operation.
- Even then, dataset eligibility stays false: after a confirmed link the classification is
  `agent_strategy_consent_not_configured`, because no consent/authorization scope has been
  configured. `candidate_linked` is traceability, not admission.

## 9. Verification and what is not run

- Targeted backend tests: 47 passed (robustness studies, journal CSV export, portfolio
  valuation).
- Regression slice: 61 passed, 4 skipped (manual decision books, risk policy across lanes,
  paper portfolio).
- Live: backend `/health/ready` ready; frontend `/login` 200; read-only plan and journal
  probes as above; OpenAPI diff as above.
- Frontend typecheck/build: **NOT RUN**. Resource gate at 14:56 HKT reported
  RAM available 1893 MiB (threshold 2048) and swap free 973 MiB (threshold 3072), so the
  build gate exits 3. `next dev` serves the candidate UI; no production build was claimed.

## 10. Not done / boundaries

- No frontend production build, no full backend suite in this round.
- No study was started, so the live 429 text is evidenced by tests, not by a live refusal.
- No training, no inference, no consent record, no dataset snapshot.
- Branch stays local; nothing was pushed, merged, deployed publicly or scheduled.
