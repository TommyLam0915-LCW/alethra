# Current System Status

**Date**: 2026-10-12
**Branch**: feat/phase2a-research-drafts
**System**: Alethra Investment Research Workspace

---

## Current Status

```
Research Prototype — Rehearsed Locally, Pending Manual Acceptance
```

Passing tests do not establish future investment performance,
legal compliance, product-market fit, commercial sustainability,
or suitability for real-money trading.

---

## Implemented

- Deterministic historical backtesting with three restricted templates (Buy & Hold, SMA Crossover, Price Change Fixed Hold).
- Strategy versioning and research event ledger.
- Historical Replay Simulation using pre-existing demo data.
- Paper portfolio with virtual cash and risk controls.
- Model research records, model versions, model cards, and personal research memory structures.
- Model research export package with integrity checks.
- Pilot-entitlement, cost, support, and hypothesis measurement structures.
- Production/staging JWT default-secret validation.
- Deterministic mock research-draft workflow (Phase 2A) — structured draft lifecycle with question generation, answer submission, approval, revision tracking, and audit events. Mock output is a workflow demonstration, not a real AI-quality evaluation.

## Not Implemented

- Real AI research assistant (Phase 2A uses a deterministic mock provider only; no external AI calls).
- Personal AI training or model fine-tuning.
- AI provider abstraction beyond the current mock provider.
- Genuine forward observation using newly arriving market data.
- Live market data.
- Broker integration.
- Exchange integration.
- Broker/exchange API key handling.
- Trading signal API.
- Real-money execution.
- Payment processing.
- Public research sharing.
- Product-market-fit validation.
- Legal compliance approval.
- Security audit or penetration test.

---

## Identity

Alethra is an **Investment Research Workspace** — not a broker, not an advisor, not a live trading platform.

**Tagline**: "Ideas, tested. Decisions, yours."

---

## Implemented Phases

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | Core backtesting engine, auth, experiments | Complete |
| Phase 2A | Deterministic mock research-draft workflow | Complete |
| Phase 2B | Real AI research assistant | Not started |
| Phase 3A | Locked research + historical replay simulation | Complete |
| Phase 3B | Paper portfolio (virtual trading) | Complete |
| Phase 4 | Model research record + model cards | Complete |
| Phase 5 | Model research export packages | Complete |
| Phase 6 | Pilot validation + operational infrastructure | Complete |

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI, Python 3.14.7, SQLAlchemy 2.0.36, Pydantic 2.9.2 |
| Frontend | Next.js 14, TypeScript, React |
| Database | PostgreSQL (SQLite for tests) |
| Auth | JWT (HS256) |
| Testing | pytest, 232 tests, 86% coverage (2026-10-12) |

---

## Engine Versions

| Engine | Version |
|--------|---------|
| Backtest | 1.1.0 |
| Simulation | 1.0.0 |
| Paper Portfolio | 1.0.0 |

---

## Data

- **Provider**: Demo only (no live market data)
- **Assets**: DEMO-ALPHA, DEMO-BETA, DEMO-BENCHMARK
- **Date range**: 2024-01-02 to 2024-02-13 (30 trading days)
- **Nature**: Deterministically pre-generated at module load
- **Simulation type**: Historical Replay (not live forward observation)

---

## Safety Controls

| Control | Status |
|---------|--------|
| Strategy locking (immutable after lock) | Enforced |
| Benchmark execution consistency | Same commission, slippage, sizing |
| Annualization guards (< 252 days) | Shows reason, not zero |
| Export credential exclusion | No broker keys, no PII |
| Production config validation | Rejects insecure JWT defaults |
| Bilingual disclaimers | On dashboard, paper portfolio, simulation pages |
| Admin authorization | Required for admin endpoints |
| No live trading capability | By design |
| No broker/exchange integration | By design |
| No payment processing | By design |

---

## Test Summary

**Current (2026-10-12 Phase 2A update)**:

- **Total tests**: 232
- **Passed**: 232
- **Failed**: 0
- **Coverage**: 86% (re-verified 2026-10-12)
- **Per-file counts** (from `pytest --collect-only -q`):
  - test_research_drafts.py: 28 *(new Phase 2A)*
  - test_acceptance_harness.py: 20
  - test_pilot.py: 51
  - test_engine.py: 29
  - test_api.py: 29
  - test_model_research.py: 24 *(includes 1 UUID JSON boundary regression test)*
  - test_model_research_export.py: 19 *(includes 3 UUID JSON boundary regression tests)*
  - test_paper_portfolio.py: 12
  - test_simulation.py: 10
  - test_config.py: 10

**Historical (2026-10-12 harness repair update)**: 204 passed, 0 failed.

**Historical (2026-10-12 earlier commit `2850198`)**: 184 passed, 0 failed — 180 from Phase 1–6 baseline plus 4 UUID JSON boundary regression tests.

**Historical (2026-10-11 Phase 1–6 baseline)**: 180 passed, 0 failed.

---

## Known Limitations

1. **Demo data only** — No live market data integration
2. **Historical replay, not live forward observation** — Simulation uses pre-existing demo data
3. **Single-asset only** — No multi-asset portfolio support
4. **No real AI assistant** — Phase 2A uses a deterministic mock provider only; real AI research assistant (Phase 2B) not implemented
5. **SQLite for tests** — Production requires PostgreSQL
6. **No walk-forward optimization** — Not implemented
7. **Manual acceptance pending** — E2E browser testing not yet performed

---

## Manual Acceptance Status

**API-scripted acceptance (2026-10-12)**: 12/12 scenarios (A01–A12) pass via `acceptance-test-execution.py` against a locally running service (disposable SQLite database). See `docs/system-rehearsal-manual-acceptance.md` and `docs/acceptance-harness-results.md`.

**Browser-based E2E acceptance**: Pending — a human tester has not yet walked through the 12 scenarios end-to-end in a browser against the frontend UI.

**PostgreSQL acceptance**: Pending — all API-scripted acceptance to date has used SQLite. Running the script against a fresh PostgreSQL instance remains a pre-merge verification step.

---

## Not Production-Ready

This system is **not production-ready**. It is a research prototype that has been rehearsed locally. Before any production deployment:

1. Complete manual acceptance testing
2. Replace demo data with live market data provider
3. Implement real AI research assistant (Phase 2B) with proper safety controls
4. Conduct security audit by qualified reviewer
5. Implement rate limiting and DDoS protection
6. Set up monitoring, alerting, and logging infrastructure
7. Deploy to managed infrastructure with proper secrets management
8. Obtain legal review of disclaimers and compliance requirements
9. Conduct penetration testing
