# Alethra External Services Connection Inventory

**Generated**: 2026-10-18
**Branch**: auto/candidate-integration
**Commit**: 73f57c6

## Summary

| Service | Status | Authorization | Blocker |
|---------|--------|---------------|---------|
| LSEG Market Data | NOT CONFIGURED | No credentials | External blocker |
| AI Provider (OpenAI/Anthropic) | NOT CONFIGURED | No credentials | External blocker |
| Stripe Payments | MOCK MODE | Test mode only | No live credentials |
| IBKR Broker | NOT CONFIGURED | No credentials | External blocker |
| PostgreSQL | OPERATIONAL | Local instance | None |
| Redis | NOT CONFIGURED | Optional | Not required for core |

## Detailed Inventory

### 1. Market Data Provider (LSEG)

**Provider/Product**: LSEG (London Stock Exchange Group) Data API
**Intended Environment**: Production
**Identity/Account**: Not configured
**Read Operations**: 
- Real-time quotes
- Historical OHLCV data
- Instrument metadata
**Write Operations**: None (read-only)
**Required Configuration**:
- `LSEG_API_KEY`
- `LSEG_API_SECRET`
- `DATA_PROVIDER=lseg`
**Credentials Configured**: NO
**Potential Cost**: $200-500/month for real-time data feed
**Current Authorization**: NONE
**Missing Prerequisites**: 
- LSEG developer account
- API key generation
- Subscription to data feed
**Status**: EXTERNAL BLOCKED

**Verification Steps** (if credentials available):
1. Set `DATA_PROVIDER=lseg` in environment
2. Configure `LSEG_API_KEY` and `LSEG_API_SECRET`
3. Restart backend
4. Verify `/api/v1/market/quote?symbol=AAPL` returns real data
5. Check logs for successful LSEG API calls

---

### 2. Personal AI Provider (OpenAI/Anthropic)

**Provider/Product**: OpenAI GPT-4 or Anthropic Claude
**Intended Environment**: Production
**Identity/Account**: Not configured
**Read Operations**: 
- Model inference
- Token usage tracking
**Write Operations**: 
- Conversation history storage (internal)
**Required Configuration**:
- `AI_PROVIDER=openai` or `AI_PROVIDER=anthropic`
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`
**Credentials Configured**: NO
**Potential Cost**: $0.01-0.10 per 1K tokens (usage-based)
**Current Authorization**: NONE
**Missing Prerequisites**:
- OpenAI/Anthropic account
- API key generation
- Billing setup
**Status**: EXTERNAL BLOCKED

**Verification Steps** (if credentials available):
1. Set `AI_PROVIDER=openai` in environment
2. Configure `OPENAI_API_KEY`
3. Restart backend
4. Create AI conversation via `/api/v1/ai/conversations`
5. Send message and verify real AI response (not mock)
6. Check token usage recorded in `/api/v1/usage/record`

---

### 3. Stripe Payments

**Provider/Product**: Stripe Payment Processing
**Intended Environment**: Test mode (sandbox)
**Identity/Account**: Test mode configured, no live credentials
**Read Operations**:
- Subscription status
- Payment history
- Webhook events
**Write Operations**:
- Create checkout sessions
- Process webhooks
- Manage subscriptions
**Required Configuration**:
- `STRIPE_SECRET_KEY` (test mode: `sk_test_...`)
- `STRIPE_WEBHOOK_SECRET` (test mode: `whsec_...`)
- `ENABLE_LIVE_PAYMENTS=false` (for test mode)
**Credentials Configured**: PARTIAL (test mode only)
**Potential Cost**: 2.9% + $0.30 per transaction (test mode: free)
**Current Authorization**: TEST MODE ONLY
**Missing Prerequisites**:
- Stripe account (test mode available)
- Webhook endpoint configuration
- Live mode requires business verification
**Status**: MOCK MODE (test mode available but not configured)

**Verification Steps** (if test credentials available):
1. Set `STRIPE_SECRET_KEY=sk_test_...` in environment
2. Set `STRIPE_WEBHOOK_SECRET=whsec_...`
3. Restart backend
4. Create checkout session via `/api/v1/checkout`
5. Simulate webhook event
6. Verify subscription created in database
7. Check `/api/v1/subscriptions/me` returns active subscription

**Live Mode Requirements** (not authorized):
- Business verification
- Bank account setup
- Legal compliance review
- Owner explicit approval

---

### 4. IBKR Broker (Interactive Brokers)

**Provider/Product**: Interactive Brokers TWS/Gateway API
**Intended Environment**: Paper trading (Gate 1-2)
**Identity/Account**: Not configured
**Read Operations**:
- Account balances
- Position queries
- Order status
- Market data
**Write Operations**:
- Submit orders
- Cancel orders
- Modify orders
**Required Configuration**:
- `IBKR_HOST=127.0.0.1`
- `IBKR_PORT=7497` (paper) or `7496` (live)
- `IBKR_CLIENT_ID=1`
- `ENABLE_LIVE_BROKER=false` (for paper)
**Credentials Configured**: NO
**Potential Cost**: 
- Paper trading: Free
- Live trading: Commission per trade (~$0.005/share)
**Current Authorization**: NONE
**Missing Prerequisites**:
- IBKR account
- TWS or IB Gateway installation
- API connection setup
- Paper trading account configuration
**Status**: EXTERNAL BLOCKED

**Verification Steps** (if credentials available):
1. Install IB Gateway or TWS
2. Configure paper trading account
3. Set `ENABLE_LIVE_BROKER=false` (paper mode)
4. Start IB Gateway on port 7497
5. Restart backend with `IBKR_HOST=127.0.0.1`, `IBKR_PORT=7497`
6. Verify connection via `/api/v1/execution-agent/heartbeat`
7. Submit test order via `/api/v1/execution/intents`
8. Verify order appears in TWS
9. Check fill reconciliation

**Live Trading Requirements** (not authorized):
- Live IBKR account
- Sufficient capital
- Risk management approval
- Owner explicit approval for each trade

---

### 5. PostgreSQL Database

**Provider/Product**: PostgreSQL 16
**Intended Environment**: Production
**Identity/Account**: Local instance
**Read Operations**: All application reads
**Write Operations**: All application writes
**Required Configuration**:
- `DATABASE_URL=postgresql://user:pass@host:5432/dbname`
**Credentials Configured**: YES (local instance)
**Potential Cost**: Free (self-hosted) or $15-50/month (managed)
**Current Authorization**: FULL
**Missing Prerequisites**: None
**Status**: OPERATIONAL

**Verification**:
- PostgreSQL running in Docker on port 5433
- Migrations applied successfully
- All tables created
- Connection pool configured

---

### 6. Redis (Optional)

**Provider/Product**: Redis
**Intended Environment**: Production (optional)
**Identity/Account**: Not configured
**Read Operations**: Caching, session storage
**Write Operations**: Caching, session storage
**Required Configuration**:
- `REDIS_URL=redis://localhost:6379`
**Credentials Configured**: NO
**Potential Cost**: Free (self-hosted) or $10-30/month (managed)
**Current Authorization**: NOT REQUIRED
**Missing Prerequisites**: Redis installation
**Status**: NOT CONFIGURED (optional, not required for core functionality)

**Note**: Application functions without Redis. Used for performance optimization only.

---

## Integration Status Summary

### Fully Operational
- ✅ PostgreSQL database
- ✅ Mock AI provider (for testing)
- ✅ Demo market data (for testing)
- ✅ Paper trading simulation (internal)

### Available but Not Configured
- ⚠️ Stripe test mode (credentials available but not set)
- ⚠️ Redis (optional, not required)

### External Blockers
- ❌ LSEG market data (no credentials)
- ❌ Real AI provider (no credentials)
- ❌ IBKR broker (no credentials, no installation)
- ❌ Stripe live mode (no authorization)

## Cost Summary

**Current Monthly Cost**: $0 (all mock/demo mode)

**If All Services Configured**:
- LSEG data feed: $200-500/month
- AI provider: $50-200/month (usage-based)
- Stripe: 2.9% + $0.30 per transaction
- IBKR: ~$1-5/month (commissions on trades)
- PostgreSQL (managed): $15-50/month (optional)
- Redis (managed): $10-30/month (optional)

**Total Estimated**: $300-800/month + transaction costs

## Authorization Required

To proceed with real-service integration, owner must provide:

1. **LSEG API credentials** (or approve mock/demo mode continuation)
2. **AI provider API key** (OpenAI or Anthropic)
3. **Stripe test mode credentials** (or approve mock mode continuation)
4. **IBKR paper trading setup** (or approve simulation mode continuation)
5. **Explicit approval** for any live trading or real payment processing

**No live trading or real payment processing without explicit owner approval.**

---

## Next Steps

1. Owner decides which services to configure
2. Provide credentials for authorized services
3. Configure environment variables
4. Verify integration with real data
5. Update this document with verification results

**Current Status**: All external services EXTERNAL BLOCKED except PostgreSQL.
System fully functional in mock/demo mode for testing and development.
