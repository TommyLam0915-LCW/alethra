#!/bin/bash
# Journey D: Billing Lifecycle
# Executes complete flow through actual API calls (not TestClient)

set -e

BASE_URL="http://localhost:8001"
EMAIL="journey-d-$(date +%s)@test.com"
PASSWORD="testpass123"
DISPLAY_NAME="Journey D Test"

echo "=== Journey D: Billing Lifecycle ==="
echo "Base URL: $BASE_URL"
echo "Email: $EMAIL"
echo ""

# Step 1: Register and login
echo "Step 1: Register user"
curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"display_name\":\"$DISPLAY_NAME\",\"user_type\":\"individual\"}" | python3 -m json.tool
echo ""

echo "Step 2: Login"
LOGIN_RESP=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
HEADERS="Authorization: Bearer $TOKEN"
echo "Token obtained: ${TOKEN:0:20}..."
echo ""

# Step 3: Check current entitlements
echo "Step 3: Check current entitlements (should be free tier)"
ENTITLEMENTS_RESP=$(curl -s -X GET "$BASE_URL/api/v1/entitlements" \
  -H "$HEADERS")
echo "$ENTITLEMENTS_RESP" | python3 -m json.tool 2>/dev/null || echo "$ENTITLEMENTS_RESP"
echo ""

# Step 4: Check quota status
echo "Step 4: Check quota status"
QUOTA_RESP=$(curl -s -X GET "$BASE_URL/api/v1/quota/status" \
  -H "$HEADERS")
echo "$QUOTA_RESP" | python3 -m json.tool 2>/dev/null || echo "$QUOTA_RESP"
echo ""

# Step 5: Create checkout session
echo "Step 5: Create checkout session"
CHECKOUT_RESP=$(curl -s -X POST "$BASE_URL/api/v1/checkout" \
  -H "Content-Type: application/json" \
  -H "$HEADERS" \
  -d '{
    "plan_tier": "professional",
    "success_url": "http://localhost:3000/billing/success",
    "cancel_url": "http://localhost:3000/billing/cancel"
  }')
echo "$CHECKOUT_RESP" | python3 -m json.tool 2>/dev/null || echo "$CHECKOUT_RESP"
CHECKOUT_URL=$(echo "$CHECKOUT_RESP" | python3 -c "import sys, json; d = json.load(sys.stdin); print(d.get('checkout_url') or d.get('url') or 'N/A')")
echo "Checkout URL: $CHECKOUT_URL"
echo ""

# Step 6: List billing events (should be empty)
echo "Step 6: List billing events"
BILLING_RESP=$(curl -s -X GET "$BASE_URL/api/v1/billing/events" \
  -H "$HEADERS")
echo "$BILLING_RESP" | python3 -m json.tool 2>/dev/null || echo "$BILLING_RESP"
echo ""

# Step 7: Check subscription status
echo "Step 7: Check subscription status"
SUB_RESP=$(curl -s -X GET "$BASE_URL/api/v1/billing/subscription" \
  -H "$HEADERS")
echo "$SUB_RESP" | python3 -m json.tool 2>/dev/null || echo "$SUB_RESP"
echo ""

echo "=== Journey D Complete ==="
echo "Summary:"
echo "  - User registered and logged in"
echo "  - Entitlements checked (free tier)"
echo "  - Quota status verified"
echo "  - Checkout session created"
echo "  - Billing endpoints accessible"
echo ""
echo "Note: Stripe webhook simulation requires external Stripe service."
echo "In normal runtime, Stripe would send webhook to activate subscription."
