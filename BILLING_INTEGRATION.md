# Stripe Billing Integration

## Summary
Implemented complete Stripe billing integration for Alethra with subscription management, usage tracking, and quota enforcement.

## Files Created/Modified

### New Files
1. **backend/app/services/stripe_service.py** - High-level Stripe operations
   - Customer creation
   - Checkout session management
   - Portal session creation
   - Webhook event processing

2. **backend/migrations/versions/b7d8e9f1a2c3_add_usage_records.py** - Database migration
   - Creates `usage_records` table
   - Adds indexes for efficient querying

3. **docs/stripe-billing-config.md** - Configuration documentation
   - Environment variables
   - Setup checklist
   - Security considerations

### Modified Files
1. **backend/app/models/billing.py**
   - Added `UsageRecord` model for API usage tracking
   - Tracks endpoint, model, tokens, cost per call

2. **backend/app/services/billing.py**
   - Added `record_api_call()` - Record API usage
   - Added `check_quota()` - Check remaining quota
   - Added `increment_quota()` - Increment usage counter
   - Added `enforce_quota()` - Atomic check and increment
   - Added `get_usage_summary()` - Get usage statistics

3. **backend/app/api/billing.py**
   - Added `POST /checkout` - Create Stripe checkout session
   - Added `POST /portal` - Create customer portal session
   - Added `GET /subscription` - Get subscription details
   - Added `GET /usage/summary` - Get usage summary
   - Added `GET /usage/quota` - Check quota status
   - Added `POST /usage/record` - Record API call

4. **backend/tests/test_billing.py**
   - Added 11 new test cases (41 total, 30 existing)
   - Test Stripe checkout and portal
   - Test usage tracking
   - Test quota enforcement
   - Test UsageRecord model

## Test Results
```
41 passed, 75 warnings in 16.25s
```

### Test Coverage
- Plan listing and filtering (3 tests)
- Subscription lifecycle (7 tests)
- Payment intent creation (1 test)
- Webhook processing (5 tests)
- Payment records (1 test)
- Plan limits and entitlements (4 tests)
- Payment adapters (4 tests)
- Stripe adapter guards (3 tests)
- Operational status (1 test)
- **Stripe checkout and portal (4 tests)** - NEW
- **Usage tracking (7 tests)** - NEW
- **UsageRecord model (1 test)** - NEW

## API Endpoints

### New Endpoints
```
POST /api/v1/checkout
  - Create Stripe checkout session
  - Params: plan_id, success_url, cancel_url
  - Returns: session_id, session_url, customer_id

POST /api/v1/portal
  - Create Stripe customer portal session
  - Params: return_url
  - Returns: portal_url

GET /api/v1/subscription
  - Get current subscription details
  - Returns: subscription object with plan info

GET /api/v1/usage/summary
  - Get usage summary for user
  - Params: days (default: 30)
  - Returns: total_api_calls, total_tokens, by_endpoint

GET /api/v1/usage/quota
  - Check quota status
  - Params: quota_type, period
  - Returns: allowed, used, limit, remaining

POST /api/v1/usage/record
  - Record API call
  - Params: endpoint, model, tokens_used, cost
  - Returns: recorded usage details
```

## Key Features

### 1. Usage Tracking
- Track every API call with endpoint, model, tokens, cost
- Automatic subscription linking
- Efficient querying with composite indexes

### 2. Quota Enforcement
- Real-time quota checking
- Atomic check-and-increment operations
- HTTPException (429) when quota exceeded
- Configurable per plan and quota type

### 3. Stripe Integration
- Checkout session creation
- Customer portal access
- Webhook signature verification
- Idempotent event processing
- Test mode fallback (no credentials required)

### 4. Security
- Webhook signature verification
- No browser-submitted prices trusted
- Server-side quota enforcement
- Idempotency for webhook events

## Database Schema

### New Table: usage_records
```sql
CREATE TABLE usage_records (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    subscription_id UUID REFERENCES subscriptions(id),
    endpoint VARCHAR(255) NOT NULL,
    model VARCHAR(100),
    tokens_used INTEGER DEFAULT 0,
    cost INTEGER DEFAULT 0,
    metadata_json JSONB,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX ix_usage_user_date ON usage_records(user_id, created_at);
CREATE INDEX ix_usage_subscription ON usage_records(subscription_id, created_at);
```

## Configuration Required

### Environment Variables
```bash
# Optional - Test mode if not set
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Required for checkout sessions
STRIPE_PRICE_ID_FREE=price_...
STRIPE_PRICE_ID_PRO=price_...
```

### Webhook Setup
- URL: `https://yourdomain.com/api/v1/webhooks/payment`
- Events: checkout.session.completed, customer.subscription.*, invoice.payment_*

## Test Mode Behavior
- All billing operations work without Stripe credentials
- Returns mock data with `test_mode: true`
- Suitable for development and testing
- No real payments processed

## Acceptance Criteria Met

✅ All models have proper SQLAlchemy definitions
✅ Stripe service handles all webhook events
✅ Webhook signature verification implemented
✅ Idempotency for webhook processing
✅ Usage quota enforcement (rejects API calls when exceeded)
✅ 41 tests pass with mocked Stripe (exceeds 20+ requirement)
✅ Clear documentation of required Stripe configuration

## Next Steps for Production

1. Create Stripe products and prices
2. Set Stripe credentials in environment
3. Configure webhook endpoint in Stripe dashboard
4. Update plan features with real price IDs
5. Test checkout flow in Stripe test mode
6. Deploy and monitor webhook delivery

## Blockers/Assumptions

### Assumptions
- Users are created via existing auth system
- Plans are seeded in database (or created via API)
- Frontend will integrate with new billing endpoints

### No Blockers
- System works fully in test mode without Stripe credentials
- All tests pass offline
- Ready for production deployment when Stripe credentials are available
