# System Rehearsal — Manual Acceptance Checklist

**Date**: 2026-10-12
**Branch**: chore/system-rehearsal-20261012
**Purpose**: Manual verification steps that cannot be automated

---

## Pre-Requisites

- [ ] Backend running: `cd backend && venv/bin/python -m uvicorn app.main:app --reload`
- [ ] Frontend running: `cd frontend && npm run dev`
- [ ] Database: PostgreSQL running or SQLite test mode
- [ ] Browser: http://localhost:3000

---

## 1. Brand Identity

- [ ] Visit `/` — redirects to `/login` or `/dashboard`
- [ ] Visit `/dashboard` — shows "Alethra" branding
- [ ] Visit `/api/v1/brand` — returns `{ "name": "Alethra", "tagline": "Ideas, tested. Decisions, yours." }`
- [ ] Disclaimer visible at top of dashboard (bilingual)

---

## 2. Authentication

- [ ] Register a new user at `/register`
- [ ] Login at `/login` — receives JWT token
- [ ] Access protected endpoint without token — returns 401
- [ ] Access admin endpoint as non-admin — returns 403

---

## 3. Backtest Engine

- [ ] Create experiment via UI or API
- [ ] Select demo asset (DEMO-ALPHA)
- [ ] Select strategy template (Buy & Hold, SMA Crossover, or Price Change)
- [ ] Run backtest — returns results with all metrics
- [ ] Verify metrics include: total_return_pct, max_drawdown_pct, benchmark_total_return_pct, excess_return_pct
- [ ] Verify annualized_return_pct shows reason dict (not zero) for < 252 day periods
- [ ] Verify disclaimer on results page

---

## 4. Strategy Locking

- [ ] Lock an experiment's strategy
- [ ] Attempt to modify locked strategy_spec — returns 400
- [ ] Attempt to create new version of locked experiment — returns 400
- [ ] Verify locked_at timestamp recorded
- [ ] Verify config_hash recorded

---

## 5. Historical Replay Simulation

- [ ] Start simulation on locked experiment
- [ ] Verify simulation progresses through snapshots incrementally
- [ ] Verify methodology status shows "historical_replay" (not "forward_observation")
- [ ] Verify disclaimer: "此模擬使用預先存在的示範數據進行歷史重播"
- [ ] Complete simulation — status becomes "completed"
- [ ] Verify all decisions recorded with step_number, snapshot_date, paper_equity

---

## 6. Paper Portfolio

- [ ] Create paper portfolio with initial virtual cash
- [ ] Verify bilingual disclaimer visible (English + 中文)
- [ ] Place a virtual order
- [ ] Verify order recorded with rationale
- [ ] Record a decision with expected_outcome and falsification_condition
- [ ] Pause portfolio — verify new orders blocked
- [ ] Resume portfolio — verify orders allowed again
- [ ] Retire portfolio — verify immutable (no new orders, no modifications)

---

## 7. Model Research Record

- [ ] Create model research record
- [ ] Add version with strategy_spec
- [ ] Transition status: draft → researching → historical_test_complete → locked_for_simulation → forward_observation
- [ ] Verify status label shows "重播觀察中" (not "前瞻觀察中")
- [ ] Lock a version — verify immutable
- [ ] Attempt to modify locked version — returns 400
- [ ] Add evaluation record

---

## 8. Model Research Export

- [ ] Generate export package for a model
- [ ] Verify package contains: manifest, versions, evaluations, timeline
- [ ] Verify package excludes: broker credentials, PII, live trading instructions
- [ ] Verify SHA-256 checksum in manifest
- [ ] Verify export disclaimer present

---

## 9. Admin Dashboard

- [ ] Visit `/admin` as admin user
- [ ] Verify funnel cards display correctly
- [ ] Verify "啟動歷史重播模擬" label (not "啟動前瞻模擬")
- [ ] Verify metrics cards display

---

## 10. Pilot Validation

- [ ] Visit `/pilot` page
- [ ] Verify "歷史重播模擬上限" label (not "前瞻模擬上限")
- [ ] Verify entitlement display shows limits
- [ ] Verify application form (if not yet approved)

---

## 11. Production Safety

- [ ] Attempt to start backend with `APP_ENV=production` and default JWT secret
- [ ] Verify startup fails with error: "JWT_SECRET_KEY must not be a well-known default"
- [ ] Set `APP_ENV=production` and `JWT_SECRET_KEY` to 32+ char random string
- [ ] Verify startup succeeds

---

## 12. Export Security

- [ ] Generate full system export via `/api/v1/export`
- [ ] Verify export contains: brand, experiments, runs, simulations, paper_portfolios, model_research, timeline
- [ ] Verify export excludes: user passwords, JWT secrets, broker API keys
- [ ] Verify export includes disclaimer

---

## Sign-Off

| Tester | Date | Result |
|--------|------|--------|
| | | ☐ Pass / ☐ Fail |

**Notes**:

