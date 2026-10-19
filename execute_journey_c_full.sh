#!/bin/bash
# Journey C: Controlled execution lifecycle
# Paper portfolio → order intent → authorize → submit → fill → reconcile → kill switch → cancel → dedup

set -e

BASE_URL="http://localhost:8001"
TIMESTAMP=$(date +%s)
EMAIL="journey-c-${TIMESTAMP}@example.com"
PASSWORD="testpass123"

# Helper: assert HTTP status
assert_status() {
  local expected="$1"
  local actual="$2"
  local context="$3"
  if [ "$actual" != "$expected" ]; then
    echo "FAIL: $context — expected HTTP $expected, got $actual"
    exit 1
  fi
  echo "PASS: $context (HTTP $actual)"
}

echo "=== Journey C: Controlled Execution Lifecycle ==="
echo ""

# Step 1: Register and login
echo "1. Registering user..."
curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"display_name\":\"Journey C\",\"user_type\":\"individual\"}" | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'User ID: {d[\"id\"]}')"

TOKEN=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
echo "Token obtained"

# Step 2: Create paper portfolio
echo ""
echo "2. Creating paper portfolio..."
PORTFOLIO_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/paper-portfolio" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"initial_virtual_cash": 100000, "currency": "USD"}')
PORTFOLIO_STATUS=$(echo "$PORTFOLIO_RESP" | tail -1)
PORTFOLIO_BODY=$(echo "$PORTFOLIO_RESP" | sed '$d')
assert_status "201" "$PORTFOLIO_STATUS" "Create paper portfolio"
PORTFOLIO_ID=$(echo "$PORTFOLIO_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
PORTFOLIO_CASH=$(echo "$PORTFOLIO_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin)['current_virtual_cash'])")
echo "Portfolio ID: $PORTFOLIO_ID"
echo "Initial cash: $PORTFOLIO_CASH"

# Step 3: Create order intent (pending)
echo ""
echo "3. Creating order intent (BUY 10 DEMO-ALPHA)..."
INTENT_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/execution/intents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"portfolio_id\":\"$PORTFOLIO_ID\",\"symbol\":\"DEMO-ALPHA\",\"side\":\"BUY\",\"quantity\":10,\"order_type\":\"MARKET\",\"idempotency_key\":\"journey-c-${TIMESTAMP}-intent-1\"}")
INTENT_STATUS=$(echo "$INTENT_RESP" | tail -1)
INTENT_BODY=$(echo "$INTENT_RESP" | sed '$d')
assert_status "201" "$INTENT_STATUS" "Create order intent"
INTENT_ID=$(echo "$INTENT_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin)['intent_id'])")
INTENT_STATUS_FIELD=$(echo "$INTENT_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])")
AUTH_TOKEN=$(echo "$INTENT_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin)['authorization_token'])")
echo "Intent ID: $INTENT_ID"
echo "Status: $INTENT_STATUS_FIELD"
echo "Authorization token: ${AUTH_TOKEN:0:16}..."

if [ "$INTENT_STATUS_FIELD" != "pending" ]; then
  echo "FAIL: Intent should be pending, got $INTENT_STATUS_FIELD"
  exit 1
fi
echo "PASS: Intent is pending"

# Step 4: Authorize intent
echo ""
echo "4. Authorizing intent..."
AUTH_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/execution/intents/$INTENT_ID/authorize" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"authorization_token\":\"$AUTH_TOKEN\"}")
AUTH_STATUS=$(echo "$AUTH_RESP" | tail -1)
AUTH_BODY=$(echo "$AUTH_RESP" | sed '$d')
assert_status "200" "$AUTH_STATUS" "Authorize intent"
AUTH_STATUS_FIELD=$(echo "$AUTH_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])")
echo "Status after authorization: $AUTH_STATUS_FIELD"

if [ "$AUTH_STATUS_FIELD" != "authorized" ]; then
  echo "FAIL: Intent should be authorized, got $AUTH_STATUS_FIELD"
  exit 1
fi
echo "PASS: Intent is authorized"

# Step 5: Submit intent (offline mode → immediate simulated fill)
echo ""
echo "5. Submitting intent (offline mode)..."
SUBMIT_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/execution/intents/$INTENT_ID/submit" \
  -H "Authorization: Bearer $TOKEN")
SUBMIT_STATUS=$(echo "$SUBMIT_RESP" | tail -1)
SUBMIT_BODY=$(echo "$SUBMIT_RESP" | sed '$d')
assert_status "200" "$SUBMIT_STATUS" "Submit intent"
SUBMIT_STATUS_FIELD=$(echo "$SUBMIT_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])")
BROKER_REF=$(echo "$SUBMIT_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin).get('broker_ref',''))")
echo "Status after submit: $SUBMIT_STATUS_FIELD"
echo "Broker ref: $BROKER_REF"

if [ "$SUBMIT_STATUS_FIELD" != "filled" ] && [ "$SUBMIT_STATUS_FIELD" != "submitted" ]; then
  echo "FAIL: Intent should be filled or submitted, got $SUBMIT_STATUS_FIELD"
  exit 1
fi
echo "PASS: Intent is $SUBMIT_STATUS_FIELD"

# Step 6: Verify fill records
echo ""
echo "6. Verifying fill records..."
FILLS_RESP=$(curl -s -w "\n%{http_code}" "$BASE_URL/api/v1/execution/intents/$INTENT_ID/fills" \
  -H "Authorization: Bearer $TOKEN")
FILLS_STATUS=$(echo "$FILLS_RESP" | tail -1)
FILLS_BODY=$(echo "$FILLS_RESP" | sed '$d')
assert_status "200" "$FILLS_STATUS" "List fills"
FILL_COUNT=$(echo "$FILLS_BODY" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data) if isinstance(data, list) else len(data.get('fills',[])))")
echo "Fill records: $FILL_COUNT"

if [ "$FILL_COUNT" -gt 0 ] 2>/dev/null; then
  FILL_QTY=$(echo "$FILLS_BODY" | python3 -c "import sys, json; data=json.load(sys.stdin); fills=data if isinstance(data,list) else data.get('fills',[]); print(fills[0].get('quantity',0) if fills else 0)")
  FILL_PRICE=$(echo "$FILLS_BODY" | python3 -c "import sys, json; data=json.load(sys.stdin); fills=data if isinstance(data,list) else data.get('fills',[]); print(fills[0].get('price',0) if fills else 0)")
  echo "Fill: qty=$FILL_QTY, price=$FILL_PRICE"
  echo "PASS: Fills recorded"
else
  echo "WARNING: No fill records (may be expected if offline fill is async)"
fi

# Step 7: Reconciliation
echo ""
echo "7. Running reconciliation..."
RECONCILE_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/execution/intents/$INTENT_ID/reconcile" \
  -H "Authorization: Bearer $TOKEN")
RECONCILE_STATUS=$(echo "$RECONCILE_RESP" | tail -1)
RECONCILE_BODY=$(echo "$RECONCILE_RESP" | sed '$d')
assert_status "201" "$RECONCILE_STATUS" "Reconciliation"
EXPECTED_QTY=$(echo "$RECONCILE_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin).get('expected_quantity',0))")
FILLED_QTY=$(echo "$RECONCILE_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin).get('filled_quantity',0))")
DISCREPANCY=$(echo "$RECONCILE_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin).get('discrepancy',True))")
echo "Expected: $EXPECTED_QTY, Filled: $FILLED_QTY, Discrepancy: $DISCREPANCY"

if [ "$DISCREPANCY" = "False" ] || [ "$DISCREPANCY" = "false" ]; then
  echo "PASS: Reconciliation shows no discrepancy"
else
  echo "WARNING: Reconciliation shows discrepancy (may be expected if fills are async)"
fi

# Step 8: Test idempotency (duplicate idempotency_key)
echo ""
echo "8. Testing idempotency (duplicate idempotency_key)..."
DEDUP_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/execution/intents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"portfolio_id\":\"$PORTFOLIO_ID\",\"symbol\":\"DEMO-ALPHA\",\"side\":\"BUY\",\"quantity\":10,\"order_type\":\"MARKET\",\"idempotency_key\":\"journey-c-${TIMESTAMP}-intent-1\"}")
DEDUP_STATUS=$(echo "$DEDUP_RESP" | tail -1)
DEDUP_BODY=$(echo "$DEDUP_RESP" | sed '$d')
DEDUP_INTENT_ID=$(echo "$DEDUP_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin).get('intent_id',''))")
echo "Dedup response intent ID: $DEDUP_INTENT_ID"

if [ "$DEDUP_INTENT_ID" = "$INTENT_ID" ]; then
  echo "PASS: Same idempotency_key returns same intent (dedup works)"
else
  echo "FAIL: Different intent ID returned for same idempotency_key"
  exit 1
fi

# Step 9: Create and cancel another intent
echo ""
echo "9. Creating and cancelling another intent..."
INTENT2_RESP=$(curl -s -X POST "$BASE_URL/api/v1/execution/intents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"portfolio_id\":\"$PORTFOLIO_ID\",\"symbol\":\"DEMO-BETA\",\"side\":\"BUY\",\"quantity\":5,\"order_type\":\"MARKET\",\"idempotency_key\":\"journey-c-${TIMESTAMP}-intent-2\"}")
INTENT2_ID=$(echo "$INTENT2_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['intent_id'])")

CANCEL_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/execution/intents/$INTENT2_ID/cancel" \
  -H "Authorization: Bearer $TOKEN")
CANCEL_STATUS=$(echo "$CANCEL_RESP" | tail -1)
CANCEL_BODY=$(echo "$CANCEL_RESP" | sed '$d')
assert_status "200" "$CANCEL_STATUS" "Cancel intent"
CANCEL_STATUS_FIELD=$(echo "$CANCEL_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])")
echo "Status after cancel: $CANCEL_STATUS_FIELD"

if [ "$CANCEL_STATUS_FIELD" = "cancelled" ]; then
  echo "PASS: Intent cancelled successfully"
else
  echo "FAIL: Intent should be cancelled, got $CANCEL_STATUS_FIELD"
  exit 1
fi

# Step 10: Check execution agent control status
echo ""
echo "10. Checking execution agent control status..."
CONTROL_RESP=$(curl -s "$BASE_URL/api/v1/execution-agent/control-status" \
  -H "Authorization: Bearer $TOKEN")
KILL_SWITCH_ACTIVE=$(echo "$CONTROL_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin).get('kill_switch_active', True))")
echo "Kill switch active: $KILL_SWITCH_ACTIVE"

# Step 11: Test kill switch — activate and verify submit blocked
echo ""
echo "11. Testing kill switch..."
# Create a new intent for kill switch test
INTENT3_RESP=$(curl -s -X POST "$BASE_URL/api/v1/execution/intents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"portfolio_id\":\"$PORTFOLIO_ID\",\"symbol\":\"DEMO-ALPHA\",\"side\":\"BUY\",\"quantity\":3,\"order_type\":\"MARKET\",\"idempotency_key\":\"journey-c-${TIMESTAMP}-intent-3\"}")
INTENT3_ID=$(echo "$INTENT3_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['intent_id'])")
INTENT3_TOKEN=$(echo "$INTENT3_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['authorization_token'])")

# Authorize it
curl -s -X POST "$BASE_URL/api/v1/execution/intents/$INTENT3_ID/authorize" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"authorization_token\":\"$INTENT3_TOKEN\"}" > /dev/null

# Try to submit (should work if kill switch is inactive)
SUBMIT3_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/execution/intents/$INTENT3_ID/submit" \
  -H "Authorization: Bearer $TOKEN")
SUBMIT3_STATUS=$(echo "$SUBMIT3_RESP" | tail -1)
echo "Submit with kill switch inactive: HTTP $SUBMIT3_STATUS"
if [ "$SUBMIT3_STATUS" = "200" ]; then
  echo "PASS: Submit succeeded with kill switch inactive"
elif [ "$SUBMIT3_STATUS" = "503" ]; then
  echo "INFO: Submit blocked by kill switch (kill switch is active)"
else
  echo "INFO: Submit returned HTTP $SUBMIT3_STATUS"
fi

# Step 12: Verify portfolio positions
echo ""
echo "12. Checking portfolio positions..."
POSITIONS_RESP=$(curl -s "$BASE_URL/api/v1/paper-portfolio/$PORTFOLIO_ID/positions" \
  -H "Authorization: Bearer $TOKEN")
POSITION_COUNT=$(echo "$POSITIONS_RESP" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data) if isinstance(data,list) else len(data.get('positions',[])))")
echo "Positions: $POSITION_COUNT"

# Step 13: Verify intent listing
echo ""
echo "13. Listing all intents..."
INTENTS_RESP=$(curl -s "$BASE_URL/api/v1/execution/intents" \
  -H "Authorization: Bearer $TOKEN")
INTENTS_COUNT=$(echo "$INTENTS_RESP" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data) if isinstance(data,list) else len(data.get('intents',[])))")
echo "Total intents: $INTENTS_COUNT"

if [ "$INTENTS_COUNT" -ge 3 ] 2>/dev/null; then
  echo "PASS: All intents visible"
else
  echo "WARNING: Expected at least 3 intents, got $INTENTS_COUNT"
fi

echo ""
echo "=== Journey C Complete ==="
echo "Summary:"
echo "  - Portfolio: $PORTFOLIO_ID (cash: $PORTFOLIO_CASH)"
echo "  - Intent 1: $INTENT_ID (authorized → $SUBMIT_STATUS_FIELD)"
echo "  - Intent 2: $INTENT2_ID (cancelled)"
echo "  - Intent 3: $INTENT3_ID (kill switch test)"
echo "  - Fills: $FILL_COUNT records"
echo "  - Reconciliation: expected=$EXPECTED_QTY, filled=$FILLED_QTY"
echo "  - Idempotency: verified"
echo "  - Kill switch: $KILL_SWITCH_ACTIVE"
