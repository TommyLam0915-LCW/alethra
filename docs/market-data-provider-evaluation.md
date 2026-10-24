# Market Data Provider Evaluation

**Date**: 2026-10-21  
**Status**: Evaluation in progress, no credentials obtained  
**Objective**: Select minimum viable provider for live-data POC (US historical/EOD first, then real-time, then HK)

---

## Provider Comparison

### Twelve Data

**Sources**:
- Pricing: https://twelvedata.com/pricing
- Credits: https://support.twelvedata.com/en/articles/5615854-credits
- Trial: https://support.twelvedata.com/en/articles/5335783-trial
- Commercial/personal use: https://support.twelvedata.com/en/articles/5332349-commercial-and-personal-usage
- Terms: https://twelvedata.com/terms
- Exchanges: https://twelvedata.com/exchanges

**Rate limits** (from official credits documentation):
- **Basic (Free)**: 8 API credits/minute, 800 credits/day
- **Credit weights vary by endpoint**: Not all API calls consume 1 credit. Different endpoints have different weights (must verify per-endpoint).
- **Cannot assume "8 API calls/day"** — credits ≠ calls.

**Pricing tiers** (from official pricing page):
- **Basic (Free)**: Available, but usage restrictions apply (see below)
- **Paid tiers**: Pricing not verified — must confirm directly with Twelve Data for current rates
- **Do not assume annual pricing from unofficial sources**

**Usage restrictions** (from official terms and support articles):
- **Basic tier**: "internal non-display usage only"
- **Display rights**: Web UI display (Alethra frontend) may NOT be permitted on Basic tier
- **Commercial use**: Separate restrictions apply — must verify
- **Storage/caching**: Rights not confirmed for free tier
- **Team/multi-user access**: May require different licensing

**Exchange coverage vs plan entitlements**:
- **XHKG (HK) listed**: ✅ Exchange appears in official exchange list
- **Basic tier HK access**: ❓ NOT confirmed — exchange coverage ≠ plan entitlement
- **HK real-time vs delayed vs EOD**: ❓ Depends on plan and exchange agreements
- **Cannot assume Basic tier includes HK data**

**API capabilities** (from documentation):
- Historical EOD bars: ✅ (endpoint exists, credit weight TBD)
- Real-time quotes: ✅ (tier-dependent, credit weight TBD)
- WebSocket streaming: ✅ (premium tiers)
- Fundamentals: ✅ (credit weight TBD)

**UNVERIFIED — must confirm before any data use**:
1. **Display rights**: Can Basic tier data be shown on Alethra web UI?
2. **Storage rights**: Can we cache/store OHLCV data in database?
3. **Commercial use**: Is POC considered commercial or personal?
4. **Team access**: Does "owner-only" vs "developer team" change licensing?
5. **HK entitlement**: Does Basic tier include XHKG data? Which frequency?
6. **US feed type**: Real-time or delayed on Basic tier?
7. **Attribution**: Required attribution format and placement?
8. **Endpoint credit weights**: How many credits per API call for each endpoint?

**Authentication**: API key in query param or header

**Status**: Priority evaluation candidate, but **cannot proceed without rights confirmation**.

---

### Alpha Vantage

**Source**: https://www.alphavantage.co/documentation/

**Pricing tiers** (from documentation):
- **Free**: JSON/CSV output, compact (latest 100 data points)
- **Premium**: Required for real-time, full history, intraday, bulk quotes

**Coverage**:
- US stocks: ✅ Historical + real-time (premium)
- HK stocks: ⚠️ Not explicitly listed in fetched documentation
- Forex, crypto: ✅ (out of scope for now)

**API capabilities** (from documentation):
- TIME_SERIES_DAILY: ✅ Historical EOD
- TIME_SERIES_INTRADAY: ✅ Premium required
- GLOBAL_QUOTE: ✅ Latest quote
- HISTORICAL data: ✅ Full history premium

**UNVERIFIED / UNCLEAR**:
- HK stock coverage (need to check symbol list)
- Free tier rate limits (calls per minute, calls per day)
- Display rights for free tier
- Data storage/caching rights
- Real-time vs delayed on which tier
- Timestamp timezone and adjustment semantics

**Authentication**: API key in query param

**Estimated effort to integrate**: 2-3 days (adapter + tests + verification)

---

### LSEG (Retained for comparison, not primary candidate)

**Status**: Adapter implemented but unverified  
**Blocker**: Platform Session license required, Desktop Session incompatible with Docker  
**Estimated cost**: $200-500/month (unsubstantiated, needs official quote)  
**Decision**: Defer until Twelve Data / Alpha Vantage evaluation complete

---

## Intended Usage Classification

**Must classify before evaluating provider plans**:

| Usage type | Description | Twelve Data Basic permitted? |
|---|---|---|
| **Owner-only internal evaluation** | Single user, non-production, no external display | ❓ Unclear — "internal non-display" may permit this |
| **Developer/team access** | Multiple developers, internal tools | ❓ Likely requires different licensing |
| **External demo/customer access** | Web UI visible to external users | ❌ Basic explicitly "non-display" |
| **Storage/cache** | Persisting data in database | ❓ Rights not confirmed |
| **Model training/research** | Internal ML training using historical data | ❓ Rights not confirmed |

**Alethra POC intended usage** (to be confirmed by owner):
- Initial: Owner-only internal evaluation with web UI display
- Later: Developer team access
- Goal: Web-based chart display (may require paid tier)
- Storage: Cache historical OHLCV for training
- Future: Multi-user access, possibly commercial

**Critical question**: Does "web UI display for owner-only POC" count as "display" under Twelve Data terms?
- If yes: Basic tier insufficient, need paid plan
- If no: Basic tier may work for initial validation

**This must be clarified with Twelve Data support before implementation**.

---

## Recommendation

**Priority evaluation candidate**: **Twelve Data**

**Reasons**:
1. Explicit US equity coverage
2. HK exchange (XHKG) listed in exchange catalog
3. Clear API documentation
4. Credit-based pricing (flexible)

**Cannot recommend Basic tier for Alethra POC without confirmation**:
- Basic tier is "internal non-display usage only"
- Alethra POC requires web UI display
- Storage/caching rights unconfirmed
- Commercial use restrictions unclear

**Next step**: Owner to contact Twelve Data support with specific usage description (see draft inquiry below) and confirm required plan.

**Fallback**: Alpha Vantage (if Twelve Data rights insufficient or pricing prohibitive)

**Phased approach** (conditional on rights confirmation):
1. **Phase 0**: Rights confirmation (REQUIRED BEFORE ANY DATA USE)
2. **Phase 1**: US historical EOD → verify data pipeline (if rights permit)
3. **Phase 2**: US real-time/delayed → verify freshness (if rights permit)
4. **Phase 3**: HK coverage → verify symbol mapping, HKD, trading sessions (if entitlement includes HK)
5. **Phase 4**: IBKR paper execution (separate approval, not tied to market data)

---

## Draft Support Inquiry for Twelve Data

**To**: support@twelvedata.com (or support portal)  
**From**: [Owner]  
**Subject**: Licensing clarification for research platform POC

---

**Inquiry** (DRAFT — do not send until owner reviews):

Hello Twelve Data team,

I am evaluating your market data API for a research and simulation trading platform called Alethra. Before proceeding, I need to clarify licensing requirements for our specific use case.

**Project description**:
Alethra is an investment research workbench in development. It allows users to:
- View stock charts with real-time and historical market data
- Conduct research and record investment hypotheses
- Train ML models on historical data for backtesting
- Run paper trading simulations (no real money)

**Intended usage**:
- **Initial phase**: Owner-only internal POC (single user, non-production)
- **Web UI**: Browser-based interface displaying stock charts and data
- **Data storage**: Cache historical OHLCV bars in PostgreSQL database for model training
- **Future phase**: Expand to small developer team (2-3 users) for internal testing
- **Markets**: US equities first, then Hong Kong equities (XHKG)

**Specific questions**:

1. **Display rights on Basic (free) tier**:
   - Your terms state Basic is "internal non-display usage only"
   - Does showing data on a web UI for owner-only internal POC count as "display"?
   - If yes, which paid tier is required for web UI display?

2. **Storage and caching**:
   - Can we store historical OHLCV data in our database on Basic tier?
   - Are there restrictions on data retention period?
   - Does storage require a specific plan?

3. **Model training and research**:
   - Can we use historical data to train ML models for internal research?
   - Does this require a specific plan or is it covered under "internal use"?

4. **US equity data**:
   - What is the actual data feed on Basic tier: real-time or delayed?
   - If delayed, what is the delay (15 minutes, EOD, etc.)?
   - Which US exchanges are included?

5. **Hong Kong equity data (XHKG)**:
   - Is XHKG data available on Basic tier?
   - If yes, what is the frequency (real-time, delayed, EOD)?
   - If no, which tier is required for HK data?
   - Are there additional exchange licensing requirements?

6. **Team access**:
   - If we expand from owner-only to a small developer team (2-3 users), does licensing change?
   - Do all users need separate accounts, or can we share one account?

7. **Attribution requirements**:
   - What attribution is required when displaying Twelve Data on a web UI?
   - Where must attribution appear (footer, chart label, etc.)?

8. **Credit consumption**:
   - How many credits does each endpoint consume (time_series, quote, etc.)?
   - With 800 credits/day, how many API calls can we make for typical POC usage?

9. **Recommended plan**:
   - Based on our usage (owner-only POC with web UI, storage, model training, US + HK), which plan do you recommend?
   - What is the current pricing for that plan?

**Additional context**:
- This is a non-commercial POC for now, but may become commercial if the project succeeds
- We are evaluating multiple providers (Alpha Vantage, LSEG, etc.)
- We need a clear answer on licensing before we can proceed with implementation

Thank you for your assistance. I look forward to your response.

Best regards,
[Owner name]
[Contact information]

---

**Status**: DRAFT — owner to review, customize, and send. Do not send without owner approval.

---

## Minimum Integration Proposal

### Smallest adapter scope (Phase 1: historical EOD only)

**File**: `backend/app/providers/twelve_data_provider.py`

**Implemented methods** (only what's needed for provider → backend → chart validation):
- `provider_id` → "twelve_data"
- `provider_name` → "Twelve Data"
- `feed_mode` → "historical" (Phase 1, not real-time yet)
- `list_assets()` → Return approved US instrument set (5 symbols)
- `get_bars(symbol, start_date, end_date)` → Historical EOD bars via `/time_series` endpoint
- `get_date_range()` → Return available date range (from API or config)
- `get_data_quality()` → Return provenance metadata (data_nature="live" or "delayed", adjustment, timezone)
- `resolve_instrument()` → Map `US:AAPL` → `AAPL` (Twelve Data format)

**Explicitly unsupported methods** (raise NotImplementedError):
- `get_latest_bar(symbol)` → NOT implemented in Phase 1 (real-time/delayed quotes deferred)
- `compute_checksum()` → NOT implemented in Phase 1 (can add later if needed)

**Rationale**: Do not implement all 10 DataProvider methods if POC only needs 7. Unsupported methods must raise clearly, not return synthetic data or empty results.

**Configuration**:
- Environment variable: `TWELVE_DATA_API_KEY`
- Optional: `TWELVE_DATA_BASE_URL` (default: "https://api.twelvedata.com")

**HTTP stack**:
- Reuse existing `requests` library (already used by LSEG adapter)
- No additional dependencies required

**Instrument mapping**:
- Extend `backend/app/data/real_instruments.py` with `TWELVE_DATA_MAPPINGS`
- Phase 1 (US only):
  ```python
  TWELVE_DATA_MAPPINGS = {
      "US:AAPL": "AAPL",
      "US:MSFT": "MSFT",
      "US:GOOGL": "GOOGL",
      "US:AMZN": "AMZN",
      "US:TSLA": "TSLA",
  }
  ```
- Phase 3 (HK, deferred):
  ```python
  # Add after HK entitlement confirmed
  "HK:0700": "0700.HK",  # Verify actual format
  "HK:9988": "9988.HK",
  "HK:0005": "0005.HK",
  ```

**Error handling**:
- Missing API key: Raise `ConfigurationError`, provider not registered
- Rate limit (HTTP 429): Raise `RateLimitError`, respect retry-after header
- API error (4xx, 5xx): Raise `DataError`, log and return gracefully
- Invalid symbol: Raise `ValueError` with clear message
- **No silent fallback**: Never return demo data when Twelve Data fails

**Metadata** (from `get_data_quality()`):
- `data_nature`: "live" or "delayed" (based on Twelve Data response and tier)
- `adjustment`: "unknown" (until verified with Twelve Data)
- `timezone`: "America/New_York" for US (until verified)
- `note`: "Data provided by Twelve Data. Verify rights and freshness before production use."

**Credit tracking** (optional, for monitoring):
- Log credit consumption per API call
- Track daily usage against 800 credits/day limit
- Warn when approaching limit

---

### Offline contract tests

**File**: `backend/tests/test_twelve_data_provider.py`

**Test scope** (Phase 1 only):

1. **Configuration**:
   - Missing `TWELVE_DATA_API_KEY` → provider not registered
   - Invalid API key format → connection fails gracefully
   - Valid API key → provider registered successfully

2. **Instrument resolution**:
   - `US:AAPL` → "AAPL"
   - `US:MSFT` → "MSFT"
   - Unknown instrument `US:UNKNOWN` → raises ValueError
   - HK instrument (Phase 1) → raises NotImplementedError or returns None

3. **Historical bars** (mocked HTTP):
   - `get_bars("US:AAPL", "2024-01-01", "2024-01-31")` → returns List[OHLCVBar]
   - Verify OHLCVBar fields: date, open, high, low, close, volume
   - Verify bars sorted by date ascending
   - Mock response format: Twelve Data `/time_series` JSON schema

4. **Unsupported methods**:
   - `get_latest_bar("US:AAPL")` → raises NotImplementedError
   - `compute_checksum(...)` → raises NotImplementedError

5. **Error handling** (mocked HTTP):
   - HTTP 429 response → raises RateLimitError
   - HTTP 401 response → raises DataError (invalid API key)
   - HTTP 500 response → raises DataError (server error)
   - Timeout → raises DataError

6. **Metadata**:
   - `get_data_quality("US:AAPL")` → returns dict with data_nature, adjustment, timezone, note
   - `feed_mode` → "historical" (Phase 1)

7. **No silent fallback**:
   - Provider failure → error raised, NOT demo data returned
   - Verify `get_bars()` does not fall back to synthetic data

**Test isolation**:
- Use `unittest.mock.patch` for HTTP requests
- Do NOT call actual Twelve Data API in unit tests
- Verify request format (URL, params, headers), not response content
- Mock response should match Twelve Data API schema

**Coverage target**: 100% of Phase 1 adapter code paths

---

### Bounded external smoke-test plan (Phase 1 only)

**Precondition**: Twelve Data support confirms Basic tier permits intended usage

**Step 1: API key configuration** (30 min)
1. Owner obtains Twelve Data API key (do not share in chat)
2. Configure `TWELVE_DATA_API_KEY` in isolated POC environment
3. Verify provider registers successfully

**Step 2: Historical EOD validation** (2 hours)
1. Fetch 30-day EOD bars for `US:AAPL` via `get_bars()`
2. Verify:
   - Response contains 20-22 trading days (weekends/holidays excluded)
   - Each bar has date, open, high, low, close, volume
   - Prices are reasonable (no obvious errors like negative prices)
   - Dates are in ascending order
3. Compare with public source (Yahoo Finance) for 3 random dates
4. Verify prices match within tolerance (accounting for adjustment differences)
5. Document: actual data_nature (live or delayed), timezone, adjustment status

**Step 3: Credit consumption tracking** (1 hour)
1. Monitor credit usage during Step 2
2. Verify actual credits consumed per API call
3. Calculate: with 800 credits/day, how many 30-day fetches possible?
4. Document credit weights for `/time_series` endpoint

**Step 4: Error handling validation** (1 hour)
1. Test rate limit: Make rapid calls until HTTP 429
2. Verify RateLimitError raised with retry-after
3. Test invalid symbol: Request `US:INVALID`
4. Verify ValueError raised with clear message
5. Test network error: Simulate timeout
6. Verify DataError raised gracefully

**Step 5: Metadata verification** (30 min)
1. Call `get_data_quality("US:AAPL")`
2. Verify returned metadata matches actual data characteristics
3. Confirm data_nature label is accurate (live vs delayed)
4. Verify timezone is correct for US market

**Pass criteria** (Phase 1):
- All 5 steps complete without errors
- Historical data matches public sources within tolerance
- Credit consumption documented
- Error handling works as specified
- Metadata accurately labels data source and characteristics
- No silent fallback to demo data

**Do NOT proceed to Phase 2 (real-time) until Phase 1 passes and rights confirmed**.

---

## Local Preparation Status

**Completed**:
- ✅ POC runbook corrected (startup sequence, bootstrap decision)
- ✅ LSEG adapter status clarified (implemented but unverified)
- ✅ Provider interface documented
- ✅ Twelve Data / Alpha Vantage initial evaluation

**Pending**:
- ⏳ Owner decision: Twelve Data vs Alpha Vantage vs alternative
- ⏳ Owner action: Review terms of service, confirm rights
- ⏳ Owner action: Obtain API key (if proceeding)
- ⏳ Implementation: Twelve Data adapter (after approval)
- ⏳ Testing: Offline contract tests (after implementation)
- ⏳ Verification: 4-phase smoke test (after API key obtained)

**Not started** (deferred):
- LSEG adapter verification (deferred until Twelve Data evaluation complete)
- IBKR market data adapter (separate phase, not tied to market data selection)
- Real-time streaming implementation (Phase 2, after EOD validated)

---

## Next Steps

1. **Owner**: Review this evaluation and select provider (Twelve Data recommended)
2. **Owner**: Review provider terms of service, confirm display/storage/commercial rights
3. **Owner**: Obtain API key from selected provider (do not share in chat)
4. **Agent**: Implement adapter for selected provider (after owner approval)
5. **Agent**: Write offline contract tests
6. **Agent**: Execute 4-phase smoke test with real API key
7. **Agent**: Document results, update poc-setup.md with verified configuration

**Stop point**: Awaiting owner provider selection and rights confirmation before implementation.
