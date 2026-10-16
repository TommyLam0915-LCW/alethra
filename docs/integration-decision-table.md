# Alethra — Provider Integration Decision Table

**Date:** 2026-10-15  
**Status:** Awaiting owner decisions  
**Branch:** `feat/real-integrations`  
**Baseline:** `6a254e3` (main, PR #7 merged)

---

## Current State Audit

**What exists (offline/demo only):**
- Market data: `DemoDataProvider` with hardcoded symbols (AAPL, MSFT, GOOGL, TSLA, SPY)
- Payment: Abstract `PaymentAdapter` interface, no real provider SDK
- Broker: Paper portfolio simulation only, no real broker adapter
- Training: Grid search parameter optimization only, no real ML training
- Deployment: No deployment configuration, no Dockerfile, no hosting setup
- Legacy migration: Scenario B blocked (documented)

**What does not exist:**
- Real market data provider integration
- Real payment provider (Stripe, etc.)
- Real broker adapter (Alpaca, Interactive Brokers, etc.)
- Real ML training pipeline (scikit-learn, PyTorch, etc.)
- Deployment configuration (Docker, Kubernetes, cloud hosting)
- Legacy database adoption procedure

---

## Provider Decision Table

### 1. Market and Exchanges

**Decision required:** Which markets and exchanges to support first?

**Recommendation:**
- **Initial market:** US equities (NYSE, NASDAQ)
- **Exchanges:** NYSE, NASDAQ (via consolidated feed)
- **Rationale:** Most liquid, widest data availability, simplest regulatory requirements
- **Alternatives considered:**
  - Crypto (24/7, fragmented exchanges) — higher complexity, different regulatory regime
  - Forex (24/5, OTC) — different semantics, no central exchange
  - HK equities (HKEX) — relevant to founder but smaller data provider ecosystem

**Assumptions:**
- Founder has US market familiarity
- US data providers have better developer documentation
- Regulatory burden lower for US equities research tool

**Owner decision needed:** Confirm US equities or specify alternative market

---

### 2. Market Data Provider

**Decision required:** Which market data provider to integrate?

**Recommendation:**
- **Provider:** Polygon.io
- **Tier:** Developer plan or Business plan (verify current pricing at polygon.io)
- **Rationale:**
  - Real-time and historical US equities data
  - WebSocket streaming for live updates
  - REST API for historical bars
  - Reasonable pricing for indie developer
  - Good developer documentation
  - No commercial redistribution restrictions on Developer/Business plans
- **Alternatives considered:**
  - Alpha Vantage — free tier too restrictive (5 calls/min), paid tiers expensive
  - IEX Cloud — shutdown in 2024
  - Yahoo Finance — unofficial API, no SLA, terms prohibit commercial use
  - Tiingo — good but Polygon has better real-time support
  - Alpaca Markets — free data but requires broker account

**Assumptions:**
- Founder will create Polygon.io account
- Developer plan sufficient for initial pilot
- Business plan needed if >5 concurrent users or commercial redistribution

**Owner decision needed:**
- Confirm Polygon.io or specify alternative
- Confirm account creation and API key provisioning
- Confirm cost approval

---

### 3. Broker Integration

**Decision required:** Which broker to integrate for execution?

**Recommendation:**
- **Broker:** Alpaca Markets
- **Account type:** Paper trading first, then live
- **Rationale:**
  - API-first broker designed for algorithmic trading
  - Paper trading environment (no real money)
  - Commission-free US equities
  - Good developer documentation
  - Supports fractional shares
  - No minimum account size
  - User-side execution agent model fits Alethra architecture
- **Alternatives considered:**
  - Interactive Brokers — more powerful but complex API, higher barriers
  - TD Ameritrade — API being deprecated, migrating to Schwab
  - Tradier — good API but less active development
  - Coinbase — crypto only, different regulatory regime

**Assumptions:**
- Founder will create Alpaca account (paper + live)
- Paper trading sufficient for initial validation
- Live trading requires separate owner approval and risk acknowledgment
- User credentials stored in user environment, not Alethra database

**Owner decision needed:**
- Confirm Alpaca Markets or specify alternative
- Confirm account creation (paper trading first)
- Confirm whether live trading is in scope for this workstream

---

### 4. Payment Provider

**Decision required:** Which payment provider to integrate for subscriptions?

**Recommendation:**
- **Provider:** Stripe
- **Rationale:**
  - Industry standard for SaaS subscriptions
  - Excellent developer documentation
  - Webhook-based event model
  - Supports subscriptions, one-time payments, usage-based billing
  - Test mode for development
  - Widely supported, easy to find help
- **Alternatives considered:**
  - Paddle — merchant of record model, higher fees
  - Chargebee — more enterprise-focused
  - Braintree — PayPal-owned, less developer-friendly

**Assumptions:**
- Founder has or will create Stripe account
- Business entity required for Stripe (sole proprietorship acceptable)
- Test mode sufficient for initial development
- Live mode requires separate owner approval

**Owner decision needed:**
- Confirm Stripe or specify alternative
- Confirm Stripe account creation
- Confirm business entity and banking details availability

---

### 5. Model Training Target

**Decision required:** What is the first model training target?

**Recommendation:**
- **Target:** Simple linear regression for price prediction
- **Framework:** scikit-learn
- **Rationale:**
  - Well-understood, interpretable model
  - Fast training, low resource requirements
  - Good baseline for comparison
  - scikit-learn is industry standard for traditional ML
  - Easy to validate and debug
- **Alternatives considered:**
  - LSTM/neural networks — more complex, harder to validate, overkill for initial
  - XGBoost — good but more complex than linear regression
  - LLM fine-tuning — explicitly out of scope per founder instructions

**Assumptions:**
- Training data: User-provided CSV or generated from Alethra backtests
- Features: Technical indicators (SMA, RSI, MACD, volatility)
- Target: Next-day return or price change
- Validation: Chronological train/validation/test split

**Owner decision needed:**
- Confirm linear regression or specify alternative model
- Confirm scikit-learn as framework
- Confirm training data source (user-provided vs. generated)

---

### 6. Deployment Target

**Decision required:** Where to deploy the application?

**Recommendation:**
- **Frontend:** Vercel (Next.js-optimized)
- **Backend:** Railway or Render (PostgreSQL + background workers)
- **Database:** Managed PostgreSQL (Railway/Render built-in)
- **Rationale:**
  - Vercel: Excellent Next.js support, automatic deployments, generous free tier
  - Railway/Render: Support long-running workers (needed for training, market data WebSocket)
  - Both have PostgreSQL built-in
  - Reasonable pricing for indie developer
  - Easy to set up, good developer experience
- **Alternatives considered:**
  - AWS/GCP/Azure — more powerful but complex, higher operational burden
  - Heroku — deprecated free tier, expensive
  - DigitalOcean — good but requires more manual setup
  - Self-hosted — maximum control but maximum operational burden

**Assumptions:**
- Founder will create accounts on chosen platforms
- Free tiers sufficient for initial development
- Paid tiers needed for production (verify current pricing)
- Long-running workers required for market data WebSocket and training

**Owner decision needed:**
- Confirm Vercel + Railway/Render or specify alternative
- Confirm account creation
- Confirm cost approval (verify current pricing)

---

## Implementation Order (Dependency Graph)

**Phase 1: Market Data (independent)**
1. Polygon.io integration (historical bars)
2. WebSocket streaming (live updates)
3. Chart integration (frontend)
4. Contract tests

**Phase 2: Payment (independent)**
1. Stripe SDK integration
2. Subscription management
3. Webhook handling
4. Entitlement enforcement
5. Test mode validation

**Phase 3: Training (depends on market data)**
1. scikit-learn integration
2. Dataset selection and permissions
3. Training pipeline (train/validation/test split)
4. Model artifact persistence
5. Inference using trained model
6. Evaluation and reporting

**Phase 4: Broker (depends on market data)**
1. Alpaca SDK integration
2. Paper trading adapter
3. User-side execution agent
4. Intent signing and authentication
5. Fill reconciliation
6. Kill-switch integration

**Phase 5: Deployment (depends on all above)**
1. Dockerfile for backend
2. Dockerfile for worker
3. Vercel configuration for frontend
4. Railway/Render configuration
5. Secret injection
6. Migration procedure
7. Runbook

**Phase 6: Legacy Adoption (independent)**
1. Schema reconstruction from git history
2. Migration procedure design
3. Disposable database validation
4. Documentation

---

## External Prerequisites

**Before implementation can begin, owner must provide:**

1. **Polygon.io:**
   - Account created
   - API key provisioned (Developer or Business plan)
   - Cost approval (verify current pricing at polygon.io)

2. **Alpaca Markets:**
   - Account created (paper trading)
   - API key provisioned
   - Decision on live trading scope

3. **Stripe:**
   - Account created
   - Business entity confirmed
   - API keys provisioned (test mode)

4. **Deployment platforms:**
   - Vercel account created
   - Railway or Render account created
   - Cost approval (verify current pricing)

---

## Completion Labels

**For each workstream, report:**

- NOT IMPLEMENTED — work not started
- PARTIAL — some components implemented, not complete
- IMPLEMENTED — OFFLINE VERIFIED — code complete, tested with mocks/fixtures
- EXTERNAL SANDBOX VERIFIED — tested with provider sandbox/test mode
- LIVE VERIFIED — tested with real provider in production-like environment
- BLOCKED — SPECIFIC EXTERNAL PREREQUISITE — cannot proceed without external dependency

**Current state:**
- Market data: NOT IMPLEMENTED (demo only)
- Payment: NOT IMPLEMENTED (mock only)
- Broker: NOT IMPLEMENTED (paper only)
- Training: NOT IMPLEMENTED (grid search only)
- Deployment: NOT IMPLEMENTED
- Legacy adoption: BLOCKED — requires git history analysis

---

## Next Steps

**Owner decisions required before implementation:**

1. Confirm or revise each recommendation in the decision table
2. Create external accounts (Polygon, Alpaca, Stripe, deployment platforms)
3. Provision API keys and credentials
4. Approve estimated monthly costs
5. Confirm implementation order

**Implementation can begin once:**
- All provider decisions confirmed
- At least one provider account created (to unblock that workstream)
- Owner approves feature branch and implementation plan

**If decisions are delayed:**
- Continue with provider-independent work (deployment config, legacy adoption analysis)
- Do not mark workstream complete without provider verification

---

**End of decision table.**
