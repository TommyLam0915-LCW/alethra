# Current System Status

**Date**: 2026-10-12
**Branch**: chore/system-rehearsal-20261012
**System**: Alethra Investment Research Workspace

---

## Identity

Alethra is an **Investment Research Workspace** — not a broker, not an advisor, not a live trading platform.

**Tagline**: "Ideas, tested. Decisions, yours."

---

## Implemented Phases

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | Core backtesting engine, auth, experiments | ✅ Complete |
| Phase 2 | AI Research Assistant | ❌ Not scheduled |
| Phase 3A | Locked research + historical replay simulation | ✅ Complete |
| Phase 3B | Paper portfolio (virtual trading) | ✅ Complete |
| Phase 4 | Model research record + model cards | ✅ Complete |
| Phase 5 | Model research export packages | ✅ Complete |
| Phase 6 | Pilot validation + operational infrastructure | ✅ Complete |

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI, Python 3.14.7, SQLAlchemy 2.0.36, Pydantic 2.9.2 |
| Frontend | Next.js 14, TypeScript, React |
| Database | PostgreSQL (SQLite for tests) |
| Auth | JWT (HS256) |
| Testing | pytest, 180 tests, 86% coverage |

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
| Strategy locking (immutable after lock) | ✅ Enforced |
| Benchmark execution consistency | ✅ Same commission, slippage, sizing |
| Annualization guards (< 252 days) | ✅ Shows reason, not zero |
| Export credential exclusion | ✅ No broker keys, no PII |
| Production config validation | ✅ Rejects insecure JWT defaults |
| Bilingual disclaimers | ✅ On dashboard, paper portfolio, simulation pages |
| Admin authorization | ✅ Required for admin endpoints |
| No live trading capability | ✅ By design |
| No broker/exchange integration | ✅ By design |
| No payment processing | ✅ By design |

---

## Known Limitations

1. **Demo data only** — No live market data integration (planned for future)
2. **Historical replay, not live forward observation** — Simulation uses pre-existing demo data
3. **Single-asset only** — No multi-asset portfolio support
4. **No AI assistant** — Phase 2 not implemented
5. **SQLite for tests** — Production requires PostgreSQL
6. **No walk-forward optimization** — Not implemented
7. **datetime.utcnow() deprecation** — Python 3.12+ warnings (non-blocking)

---

## Test Summary (Post-Repair)

- **Total tests**: 180
- **Passed**: 180
- **Failed**: 0
- **Coverage**: 86%
- **Test files**: 9 (test_api, test_engine, test_config, test_simulation, test_paper_portfolio, test_model_research, test_model_research_export, test_pilot, + conftest)

---

## Not Production-Ready

This system is **not production-ready**. It is a research prototype for pilot validation. Before any production deployment:

1. Replace demo data with live market data provider
2. Implement AI assistant (Phase 2) with proper safety controls
3. Conduct security audit by qualified reviewer
4. Implement rate limiting and DDoS protection
5. Set up monitoring, alerting, and logging infrastructure
6. Deploy to managed infrastructure with proper secrets management
7. Obtain legal review of disclaimers and compliance requirements
8. Conduct penetration testing
