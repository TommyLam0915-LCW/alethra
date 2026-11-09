# Stripe Billing Integration - Configuration Guide

## Overview
Alethra's billing system supports subscription management via Stripe with offline test mode as default.

## Required Environment Variables

### Stripe Configuration (Optional - Test Mode Default)
```bash
# Stripe API credentials (leave unset for test mode)
STRIPE_SECRET_KEY=sk_test_...  # or sk_live_... for production
STRIPE_WEBHOOK_SECRET=whsec_...

# Stripe Price IDs (required for checkout sessions)
STRIPE_PRICE_ID_FREE=price_...
STRIPE_PRICE_ID_STARTER=price_...
STRIPE_PRICE_ID_PRO=price_...
STRIPE_PRICE_ID_ENTERPRISE=price_...
```

### Test Mode Behavior
- If `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are NOT set, the system automatically uses `TestPaymentAdapter`
- All billing operations return mock data with `test_mode: true`
- No real Stripe API calls are made

## Database Tables

### Billing Tables
- `plans` - Subscription plans with pricing and features
- `subscriptions` - User subscriptions linked to plans
- `payment_records` - Payment transaction history
- `webhook_event_records` - Webhook event log (idempotency)
- `usage_records` - API usage tracking

### Quota Tables
- `quota_counters` - Usage counters per user/quota type/period
- `pilot_entitlements` - User entitlement limits (linked to plans)

## API Endpoints

### Public Endpoints
- `GET /api/v1/plans` - List available subscription plans
- `GET /api/v1/operational-status` - Billing system status

### Authenticated Endpoints
- `POST /api/v1/checkout` - Create Stripe checkout session
- `POST /api/v1/portal` - Create Stripe customer portal session
- `GET /api/v1/subscription` - Get current subscription details
- `GET /api/v1/subscriptions/me` - Get subscription (alias)
- `POST /api/v1/subscriptions` - Create subscription (test mode)
- `POST /api/v1/subscriptions/{id}/cancel` - Cancel subscription
- `POST /api/v1/payments/create-intent` - Create payment intent (test mode)

### Usage Tracking
- `GET /api/v1/usage/summary` - Get usage summary (last N days)
- `GET /api/v1/usage/quota` - Check quota status
- `POST /api/v1/usage/record` - Record API call

### Webhooks
- `POST /api/v1/webhooks/payment` - Payment webhook endpoint (requires signature)

## Webhook Events Supported

The system handles these Stripe webhook events:
- `checkout.session.completed` - Subscription checkout completed
- `customer.subscription.updated` - Subscription status changed
- `customer.subscription.deleted` - Subscription cancelled
- `invoice.payment_succeeded` - Payment successful
- `invoice.payment_failed` - Payment failed

### Webhook Security
- All webhooks require `X-Webhook-Signature` header
- Signature is verified using `STRIPE_WEBHOOK_SECRET`
- Duplicate events are detected via `event_id` in `webhook_event_records`

## Quota System

### Quota Types
- `research_run_limit` - Number of research runs
- `ai_usage_limit` - AI API calls
- `export_limit` - Data exports
- `forward_simulation_limit` - Forward simulations

### Quota Periods
- `monthly` - Resets monthly
- `lifetime` - Never resets

### Default Limits (Free Plan)
```python
{
    "research_run_limit": 5,
    "ai_usage_limit": 10,
    "export_limit": 2,
    "forward_simulation_limit": 3,
}
```

### Plan Tiers
Plans are automatically mapped to tiers based on monthly price:
- `free` - $0/month
- `starter` - $1-$49/month
- `pro` - $50+/month

Custom limits can be set via `plan.features` JSONB field.

## Usage Tracking

### Recording API Calls
```python
from app.services.billing import record_api_call

record_api_call(
    db=db,
    user_id=user.id,
    endpoint="/api/inference",
    model="gpt-4",
    tokens_used=150,
    cost=5,  # cents
)
```

### Checking Quota
```python
from app.services.billing import check_quota, enforce_quota

# Check quota without incrementing
quota = check_quota(db, user.id, "ai_usage_limit", "monthly")
if not quota["allowed"]:
    # Handle quota exceeded

# Check and increment atomically (raises HTTPException if exceeded)
enforce_quota(db, user.id, "ai_usage_limit", "monthly")
```

## Stripe Setup Checklist

### For Production Deployment

1. **Create Stripe Account**
   - Sign up at https://stripe.com
   - Complete verification

2. **Create Products and Prices**
   - Create subscription products for each tier
   - Create recurring prices (monthly billing)
   - Note price IDs (e.g., `price_123...`)

3. **Configure Environment Variables**
   ```bash
   export STRIPE_SECRET_KEY=sk_live_...
   export STRIPE_WEBHOOK_SECRET=whsec_...
   export STRIPE_PRICE_ID_FREE=price_...
   export STRIPE_PRICE_ID_PRO=price_...
   ```

4. **Set Up Webhooks**
   - In Stripe Dashboard → Webhooks → Add endpoint
   - URL: `https://yourdomain.com/api/v1/webhooks/payment`
   - Events to listen:
     - `checkout.session.completed`
     - `customer.subscription.updated`
     - `customer.subscription.deleted`
     - `invoice.payment_succeeded`
     - `invoice.payment_failed`
   - Copy webhook secret to `STRIPE_WEBHOOK_SECRET`

5. **Test Checkout Flow**
   - Use Stripe test mode (`sk_test_...`)
   - Test card: `4242 4242 4242 4242`
   - Verify webhook delivery

6. **Database Migration**
   ```bash
   cd backend
   alembic upgrade head
   ```

7. **Seed Plans** (optional)
   ```python
   from app.models.billing import Plan
   from app.database import SessionLocal
   
   db = SessionLocal()
   plans = [
       Plan(name="Free", monthly_price=0, features={...}),
       Plan(name="Pro", monthly_price=9900, features={...}),
   ]
   db.add_all(plans)
   db.commit()
   ```

## Security Considerations

### DO NOT
- ❌ Trust browser-submitted prices
- ❌ Expose Stripe secret keys to frontend
- ❌ Skip webhook signature verification
- ❌ Process payments without checking subscription status

### ALWAYS
- ✅ Verify webhook signatures
- ✅ Use idempotency keys for webhook processing
- ✅ Enforce quotas server-side
- ✅ Store customer IDs securely
- ✅ Use HTTPS for all endpoints

## Testing

### Run Tests
```bash
cd backend
python3 -m pytest tests/test_billing.py -v
```

### Test Coverage
- 41 tests covering:
  - Plan listing and filtering
  - Subscription lifecycle (create, activate, cancel)
  - Payment intent creation
  - Webhook processing and signature verification
  - Usage tracking and quota enforcement
  - Stripe checkout and portal sessions
  - Adapter initialization and fallback

### Mock Stripe in Tests
Tests use `TestPaymentAdapter` by default (no Stripe credentials needed).

For Stripe-specific tests, mock the adapter:
```python
from unittest.mock import Mock
from app.services.billing import set_payment_adapter

mock_adapter = Mock()
mock_adapter.create_checkout_session.return_value = "https://checkout.stripe.com/test"
set_payment_adapter(mock_adapter)
```

## Troubleshooting

### "No Stripe price ID configured"
- Ensure plan has `features.stripe_price_id` OR
- Set `STRIPE_PRICE_ID_*` environment variables

### Webhook signature verification failed
- Check `STRIPE_WEBHOOK_SECRET` matches Stripe dashboard
- Ensure payload is not modified before verification
- Check timestamp is within tolerance (5 minutes)

### Quota exceeded but user has subscription
- Verify subscription is `active` (not `trialing` or `cancelled`)
- Check entitlement was created on activation
- Verify quota type matches entitlement field name

## Migration Path

### Current State (Test Mode)
- All billing operations work with mock data
- No real payments processed
- Suitable for development and testing

### Production Migration
1. Set Stripe credentials in environment
2. Create real Stripe products/prices
3. Update plan features with real price IDs
4. Test checkout flow in Stripe test mode
5. Switch to live mode (`sk_live_...`)
6. Monitor webhook delivery and processing

## Support

For Stripe integration issues:
1. Check logs for adapter initialization errors
2. Verify environment variables are set
3. Test webhook endpoint accessibility
4. Review Stripe dashboard for failed events

For billing logic issues:
- See `backend/app/services/billing.py`
- See `backend/app/services/stripe_service.py`
- Review tests in `backend/tests/test_billing.py`
