# POC Environment Setup

**Target**: Live-data POC (demo mode for smoke test only)  
**Compose topology**: `docker-compose.prod.yml` (includes worker service)  
**Last verified**: 2026-10-21, commit `e3371e7`

---

## Startup Sequence

### 1. Prepare environment

```bash
cd /Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto

# Copy template (do NOT overwrite existing .env)
if [ ! -f .env ]; then
  cp .env.example .env
fi

# Edit .env with generated secrets:
#   POSTGRES_PASSWORD=$(openssl rand -hex 32)
#   REDIS_PASSWORD=$(openssl rand -hex 32)
#   JWT_SECRET_KEY=$(openssl rand -hex 32)
#
# For live-data POC, also set:
#   DATA_PROVIDER=lseg
#   LSEG_API_KEY=<your-key>  OR
#   LSEG_CLIENT_ID=<id> + LSEG_CLIENT_SECRET=<secret>
#
# Keep ENABLE_LIVE_PAYMENTS=false for POC
```

### 2. Start infrastructure

```bash
# Start db and redis only
docker compose -f docker-compose.prod.yml up -d db redis

# Wait for healthy
docker compose -f docker-compose.prod.yml ps
# Both should show "healthy" or "running"
```

### 3. Start backend (auto-creates tables)

```bash
# Backend startup automatically creates tables via:
#   Base.metadata.create_all(bind=engine, checkfirst=True)
# See backend/app/main.py:11
#
# This means migrations are for schema UPGRADES, not initial creation.
# Backend can start on empty DB.
docker compose -f docker-compose.prod.yml up -d backend

# Wait for healthy
docker compose -f docker-compose.prod.yml ps
```

### 4. Run migrations (for schema upgrades)

```bash
# Migration script uses 'docker compose exec -T backend'
# which requires backend container to be running.
#
# On fresh DB, migrations are no-ops (tables already created).
# On existing DB with schema changes, migrations apply upgrades.
./deploy/migrate.sh

# Verify current revision
docker compose -f docker-compose.prod.yml exec -T backend alembic current
```

### 5. Bootstrap required data

**Finding**: No bootstrap required for basic POC.

**Code evidence**:
- Users register via `/api/auth/register` (creates User row only)
- No Subscription or PilotEntitlement created at registration
- `require_entitlement()` dependency falls back to `FREE_PLAN_ENTITLEMENT` when no entitlement row exists
- Source: `backend/app/auth/entitlements.py:424-439`
- Free-tier limits: 5 research runs, 2 exports, 3 simulations, 10 AI usage

**No seed script created**: Existing product logic handles free-tier users automatically. Creating `poc-plan` or seed script is not justified without demonstrating a blocked operation.

**Optional bootstrap** (only if free-tier limits are insufficient): Create `PilotEntitlement` rows via direct DB insert for specific users. Do NOT create fixed-password demo accounts or invented plan tiers.

### 6. Start remaining services

```bash
# Start worker, frontend, nginx
docker compose -f docker-compose.prod.yml up -d worker frontend nginx

# Verify all services
docker compose -f docker-compose.prod.yml ps
```

### 6. Health verification

```bash
# Backend health
curl -s http://localhost:8000/health/ready | jq .

# Frontend
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/

# Provider status (shows active provider and connection state)
curl -s http://localhost:8000/api/v1/provider/info | jq .
```

### 7. Register test user

```bash
# Use real registration flow
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"your-email@example.com","password":"your-secure-password"}'

# Login and save token
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"your-email@example.com","password":"your-secure-password"}'
```

---

## Shutdown

```bash
# Stop all services (non-destructive)
docker compose -f docker-compose.prod.yml down

# Or stop specific services
docker compose -f docker-compose.prod.yml stop worker frontend nginx backend
```

**Do NOT use** `docker compose down -v` unless intentionally destroying all data.

---

## Live-Data Adapter Trace

### LSEG Provider

**Source**: `backend/app/providers/lseg_provider.py`  
**Class**: `LSEGDataProvider`  
**SDK/Package**: Direct HTTP via `requests` library (no official LSEG SDK)

**Status**: IMPLEMENTED but UNVERIFIED against actual LSEG API.

**Implemented capabilities** (code complete, not externally tested):
- ✅ OAuth2 token acquisition (`_acquire_oauth2_token`, lines 108-126)
- ✅ API key authentication (static Bearer token, lines 147-150)
- ✅ Historical EOD bars (`_fetch_history`, lines 298-338)
  - Endpoint: `/data/historical-pricing/v1/views/eod-summaries`
  - Parses `captures` array with `trading_date`, `open`, `high`, `low`, `close`, `volume`
- ✅ Latest bar (`get_latest_bar`, lines 340-376)
  - Endpoint: `/data/quotes/v1/{ric}`
  - Parses quote response with `last`, `open`, `high`, `low`, `volume`
- ✅ Rate limiting with retry-after (lines 193-245)
- ✅ Retry logic for transient failures (lines 205-245)
- ✅ Error handling and status tracking

**NOT implemented**:
- ❌ Real-time streaming (WebSocket or long-polling)
- ❌ Delayed data distinction (latest bar may be real-time or delayed, not distinguished)
- ❌ Intraday bars (only EOD summaries)
- ❌ Desktop session support (only Platform OAuth2/API key)

**Authentication modes**:
1. API key: `LSEG_API_KEY` environment variable → static Bearer token
2. OAuth2: `LSEG_CLIENT_ID` + `LSEG_CLIENT_SECRET` → client credentials grant with auto-refresh

**Token endpoint**: `https://api.refinitiv.com/auth/oauth2/v2/token`  
**API base**: `https://api.refinitiv.com`

**Session type**: Platform Session only (no desktop application required).  
**Desktop vs Platform**: This adapter uses Platform Session authentication. Desktop Session requires Workspace/Eikon desktop app running on same machine, which is incompatible with Docker deployment.

**Instrument mapping**: Internal IDs (`US:AAPL`, `HK:0700`) → LSEG RIC format (`AAPL.O`, `0700.HK`)  
**Mapping source**: `backend/app/data/real_instruments.py:LSEG_MAPPINGS`

**Source metadata**:
- `data_nature`: "live" when connected, "synthetic" for demo provider
- `feed_mode`: "live" or "disconnected"
- Timestamps: Provider returns `trading_date` field; no explicit timezone in response

**Failure behavior**:
- Missing credentials: Raises `LSEGConfigurationError`, provider not registered
- Connection failure: Returns `False` from `connect()`, logs warning, `is_available()` returns `False`
- Subsequent fetch attempts: Raise `LSEGDataError` if `is_available()` is `False`
- Rate limit (HTTP 429): Raises `LSEGRateLimitError`, starts rate-limit window
- Server error (5xx): Retries up to 2 times with backoff, then raises `LSEGDataError`
- **No silent fallback**: If LSEG fails, API returns error; does NOT fall back to demo data

**Provider registry**: `backend/app/providers/provider_registry.py`
- Lazy initialization: LSEG only initialized when `DATA_PROVIDER=lseg`
- Falls back to demo if LSEG init fails AND `DATA_PROVIDER=demo`
- Raises `ValueError` if `DATA_PROVIDER=lseg` but credentials missing

**UNVERIFIED aspects** (require actual LSEG API access to confirm):
- API endpoint paths and response schemas
- OAuth2 token refresh behavior
- Rate limit thresholds and retry-after handling
- RIC symbol mappings for all instruments
- Data entitlement scope (which RICs are accessible)
- Timestamp timezone and freshness guarantees

---

## Live-Data Acceptance Path

### Smallest path: Provider → Backend API → Workspace chart

1. **Provider response**
   - LSEG returns OHLCV bars with `trading_date`, `open`, `high`, `low`, `close`, `volume`
   - Timestamp format: Date string (no timezone in response)
   - Source: `lseg_provider.py:320-338`

2. **Backend API**
   - `/api/v1/market-data/bars?symbol=US:AAPL&start=2024-01-01&end=2024-01-31`
   - Returns bars with `data_nature` field ("live" or "synthetic")
   - Source: `backend/app/api/market_data.py`

3. **Workspace chart**
   - Frontend fetches bars and displays with provider label
   - Must show `data_nature` badge to distinguish live vs synthetic
   - Source: `frontend/pages/workspace.tsx`

### Evidence required

- [ ] Provider and instrument identity: API response shows `provider_id=lseg`, `symbol=US:AAPL`
- [ ] Source timestamp: Bar `date` field matches LSEG `trading_date`
- [ ] Data classification: `data_nature=live` (not "synthetic")
- [ ] Freshness: Latest bar timestamp within expected range for EOD data
- [ ] Stale/disconnected behavior: If LSEG fails, API returns error (not silent demo data)
- [ ] No synthetic fallback: Verify `data_nature` is never "synthetic" when `DATA_PROVIDER=lseg`

### BLOCKED items (require Owner decision)

- LSEG credentials: Not configured
- Data entitlement scope: Unknown which instruments/modes are licensed
- Session type: API key vs OAuth2 (depends on LSEG subscription)
- Real-time vs EOD: Only EOD implemented; real-time requires separate verification

---

## Required External Access

### For live-data POC

**BLOCKED on two dimensions**:

1. **Implementation gaps** (code-level):
   - No real-time streaming implemented (only EOD and latest bar)
   - No delayed data distinction
   - No intraday bars
   - Desktop session not supported (Docker incompatibility)
   - API contract unverified against actual LSEG endpoints

2. **Access/entitlement gaps** (external):
   - LSEG Platform Session license required (Desktop Session incompatible with Docker)
   - API credentials not configured
   - Data entitlement scope unknown (which instruments, which modes)
   - Rate limits and quotas not verified

### Minimum viable live-data path

**Option A: LSEG Platform Session (EOD only)**
- Requires: LSEG DataScope Select or Refinitiv Data Library license
- Credentials: `LSEG_CLIENT_ID` + `LSEG_CLIENT_SECRET` (OAuth2) or `LSEG_API_KEY`
- Scope: Historical EOD bars for licensed instruments
- Limitation: No real-time streaming; "latest bar" may be delayed or EOD
- Verification needed: Test API endpoints, confirm RIC mappings, validate response schemas

**Option B: IBKR Market Data (alternative)**
- Requires: IBKR account with market data subscription
- Adapter status: IBKR execution adapter exists; market data adapter NOT implemented
- Scope: US/HK equities with appropriate subscriptions
- Limitation: Requires new adapter implementation
- Verification needed: Implement IBKR market data adapter, test API contract

**Option C: Alternative provider**
- Requires: Provider with Python SDK or REST API
- Adapter status: New implementation required
- Scope: Depends on provider capabilities
- Limitation: Integration effort
- Verification needed: Evaluate provider, implement adapter, test

### Current status

**BLOCKED**: Cannot proceed with live-data POC without:
1. Selecting a market data source (LSEG Platform, IBKR, or alternative)
2. Obtaining appropriate license/credentials
3. Verifying API contract (for LSEG) or implementing adapter (for IBKR/alternative)
4. Confirming data entitlement scope (instruments, modes, rate limits)

Demo smoke test is available but does not satisfy live-data POC requirements.

---

## Owner Decisions Required

**Genuine unknowns** (cannot proceed without):

1. **Market data source selection**:
   - LSEG Workspace/Platform license available?
   - IBKR account with market data subscription?
   - Alternative provider (specify)?
   - No market data access yet?

2. **Session type** (if LSEG):
   - Platform Session (OAuth2/API key, Docker-compatible)?
   - Desktop Session (requires Workspace app, Docker-incompatible)?
   - Note: Current adapter only supports Platform Session

3. **Data scope requirements**:
   - Which markets (US, HK, both)?
   - Which modes (EOD, real-time, both)?
   - If real-time required: LSEG adapter gap (not implemented) blocks this path
   - If EOD sufficient: Can proceed with existing LSEG adapter after verification

4. **Bootstrap scope**:
   - Free-tier entitlement (5 research, 2 export, 3 simulation, 10 AI) sufficient?
   - Or: Specific users need higher limits (requires PilotEntitlement seed)?

5. **Implementation vs access priority**:
   - If LSEG Platform Session available: Verify existing adapter (1-2 days)
   - If IBKR only: Implement market data adapter (3-5 days)
   - If no access: POC remains BLOCKED; demo smoke test only

---

## Git Status

**Branch**: `auto/candidate-integration`  
**HEAD**: `e3371e7f23b09c0ef9d9b662f6cd9a126ad46085`  
**Working tree**: Clean (18 untracked files: docs, verify scripts, journals)

---

## Next Steps

1. **Owner provides market data access decision**
2. **If LSEG credentials available**: Configure `.env`, verify connection, run live-data acceptance
3. **If no credentials**: POC remains BLOCKED for live-data validation; demo smoke test only
4. **Proceed with core journey validation** (research, training, inference, paper portfolio) using demo data
5. **Separately approve** IBKR paper execution and Stripe sandbox (out of scope for this setup)
