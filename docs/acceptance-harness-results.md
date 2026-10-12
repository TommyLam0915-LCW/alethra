# Acceptance Harness Results — 2026-10-12

**Execution date**: 2026-10-12
**Harness**: `acceptance-test-execution.py`
**Target**: Locally running Alethra service (disposable SQLite database)
**Scenarios**: 12 (A01–A12)
**Result**: 12/12 PASS

---

## Scenario Outcomes

| Scenario | Name | Status |
|----------|------|--------|
| A01 | Landing Page | PASS |
| A02 | Registration and Login | PASS |
| A03 | Create Buy-and-Hold Experiment | PASS |
| A04 | Historical Backtest Result | PASS |
| A05 | Versioning and Change Reason | PASS |
| A06 | Event Timeline and Research Notes | PASS |
| A07 | Historical Replay Simulation | PASS |
| A08 | Paper Portfolio | PASS |
| A09 | Model Research Record and Model Card | PASS |
| A10 | Model Research Export Package | PASS |
| A11 | Privacy and Access Isolation | PASS |
| A12 | Production Safety Validation | PASS |

---

## Key Verification Results

### A01 — Landing Page
- Branding: "Alethra"
- Tagline: "Ideas, tested. Decisions, yours."
- Disclaimer present: yes
- No broker language, no live trading claim, no stock recommendation, no API key storage field

### A02 — Registration and Login
- Two users registered and logged in successfully
- Tokens received; `/me` endpoint verified

### A03 — Create Buy-and-Hold Experiment
- Template: `buy_and_hold`
- Asset: DEMO-ALPHA, Benchmark: DEMO-BENCHMARK
- Commission: 0.001, Slippage: 0.0005, Initial cash: 100,000
- Hypothesis and falsification criteria present
- Demo data only confirmed

### A04 — Historical Backtest Result
- Status: completed
- All 13 expected metric keys present
- Total return: 8.6735%, Total trades: 2
- Commissions: 208.682, Slippage cost: 104.3432
- Config hash, dataset checksum, engine version (1.1.0) present
- Methodology and warnings fields present
- Historical simulation only (no forward claim)

### A05 — Versioning and Change Reason
- Versions before: 1, after: 2
- Change reason recorded
- Original version preserved with original commission (0.001) unchanged
- New commission: 0.002

### A06 — Event Timeline and Research Notes
- Timeline events before: 10, after: 11
- Note created successfully
- Chronological order asserted
- Prior events immutable (10 prior events unchanged)
- 9 distinct event types present in timeline

### A07 — Historical Replay Simulation
- Strategy locked, status: active
- 3 snapshots advanced, 3 decisions recorded, 3 observed days
- Data cutoff: 2024-02-13
- Paper equity: 103,740.53
- Decision types: simulated_entry, no_action, no_action
- Labeled as replay; no forward simulation claim

### A08 — Paper Portfolio
- Virtual cash only, no real money, no broker connection
- Order with rationale filled virtually; risk check passed
- Pause blocks orders with rejection reason
- Resume works; retire preserves history
- Final status: retired; order history preserved (2 orders)

### A09 — Model Research Record and Model Card
- Version locked; card created
- No buy/sell output, no target weights
- No broker field, no exchange credential, no live execution
- No profitability certification

### A10 — Model Research Export Package
- Export status: completed; package checksum present
- Downloadable: yes
- No passwords, JWT secrets, API keys, exchange credentials, broker credentials
- No buy/sell commands, no live execution instructions

### A11 — Privacy and Access Isolation
- User B blocked from all User A resources: experiment, timeline, notes, runs, run detail, simulation, portfolio, model research, admin
- Isolation enforced across all 10 access checks

### A12 — Production Safety Validation
- Config tests passed: 10/10
- Rejects insecure JWT default
- Enforces minimum secret length
- Production mode validated

---

## Reproduction

```bash
cd backend
uvicorn app.main:app --port 8000 &
python ../acceptance-test-execution.py
```

The script creates a disposable SQLite database (`acceptance_disposable.db`) and tears it down after execution. All UUIDs and timestamps in raw output are disposable test artifacts.

---

## Notes

- Raw `acceptance-results.json` is not committed; this sanitised summary retains only scenario outcomes and reproducibility metadata.
- No passwords, tokens, Authorization headers, secrets, or personal data are present in this summary.
- Harness repair (2026-10-12) fixed false-positive assertions in A01, A06, A08, A09, and A10; all 12 scenarios now enforce real assertion outcomes.
