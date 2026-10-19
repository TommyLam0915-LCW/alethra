#!/bin/bash
# Journey B: Trainable Model + API Access
# Executes complete flow through actual API calls (not TestClient)

set -e

BASE_URL="http://localhost:8001"
EMAIL="journey-b-$(date +%s)@test.com"
PASSWORD="testpass123"
DISPLAY_NAME="Journey B Test"

echo "=== Journey B: Trainable Model + API Access ==="
echo "Base URL: $BASE_URL"
echo "Email: $EMAIL"
echo ""

# Step 1: Register
echo "Step 1: Register user"
REGISTER_RESP=$(curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"display_name\":\"$DISPLAY_NAME\",\"user_type\":\"individual\"}")
echo "$REGISTER_RESP" | python3 -m json.tool 2>/dev/null || echo "$REGISTER_RESP"
echo ""

# Step 2: Login
echo "Step 2: Login"
LOGIN_RESP=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
HEADERS="Authorization: Bearer $TOKEN"
echo "Token obtained: ${TOKEN:0:20}..."
echo ""

# Step 3: Create experiment
echo "Step 3: Create experiment"
STRATEGY_SPEC='{
  "schema_version": "1.0",
  "template_type": "buy_and_hold",
  "asset_ids": ["DEMO-ALPHA"],
  "frequency": "1d",
  "start_date": "2024-01-02",
  "end_date": "2024-06-28",
  "initial_cash": 100000.0,
  "signal_parameters": {},
  "entry_rule": {},
  "exit_rule": {},
  "sizing_rule": {},
  "execution_timing": {"execute_at": "next_open"},
  "commission_rate": 0.0,
  "slippage_rate": 0.0,
  "benchmark_id": "DEMO-BENCHMARK",
  "currency": "USD"
}'

EXP_RESP=$(curl -s -X POST "$BASE_URL/api/v1/experiments/" \
  -H "Content-Type: application/json" \
  -H "$HEADERS" \
  -d "{
    \"title\": \"Journey B Experiment\",
    \"original_idea\": \"Test hypothesis\",
    \"research_type\": \"quantitative\",
    \"hypothesis\": \"Buy and hold outperforms\",
    \"falsification_conditions\": \"Max drawdown > 30%\",
    \"benchmark\": \"DEMO-BENCHMARK\",
    \"strategy_spec\": $STRATEGY_SPEC
  }")
echo "$EXP_RESP" | python3 -m json.tool 2>/dev/null || echo "$EXP_RESP"
EXPERIMENT_ID=$(echo "$EXP_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Experiment ID: $EXPERIMENT_ID"
echo ""

# Step 4: Create training job
echo "Step 4: Create training job"
TRAINING_RESP=$(curl -s -X POST "$BASE_URL/api/v1/training/jobs" \
  -H "Content-Type: application/json" \
  -H "$HEADERS" \
  -d "{
    \"experiment_id\": \"$EXPERIMENT_ID\",
    \"parameter_grid\": {
      \"short_window\": [5, 10],
      \"long_window\": [20, 50]
    }
  }")
echo "$TRAINING_RESP" | python3 -m json.tool 2>/dev/null || echo "$TRAINING_RESP"
JOB_ID=$(echo "$TRAINING_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Training Job ID: $JOB_ID"
echo ""

# Step 5: Check training job status
echo "Step 5: Check training job status"
JOB_STATUS_RESP=$(curl -s -X GET "$BASE_URL/api/v1/training/jobs/$JOB_ID" \
  -H "$HEADERS")
echo "$JOB_STATUS_RESP" | python3 -m json.tool 2>/dev/null || echo "$JOB_STATUS_RESP"
echo ""

# Step 6: List models (should be empty initially)
echo "Step 6: List models"
MODELS_RESP=$(curl -s -X GET "$BASE_URL/api/v1/ml/models" \
  -H "$HEADERS")
echo "$MODELS_RESP" | python3 -m json.tool 2>/dev/null || echo "$MODELS_RESP"
echo ""

# Step 7: Create API key
echo "Step 7: Create API key"
API_KEY_RESP=$(curl -s -X POST "$BASE_URL/api/v1/api-keys" \
  -H "Content-Type: application/json" \
  -H "$HEADERS" \
  -d "{
    \"name\": \"Journey B Test Key\",
    \"scopes\": [\"read\", \"write\"]
  }")
echo "$API_KEY_RESP" | python3 -m json.tool 2>/dev/null || echo "$API_KEY_RESP"
API_KEY=$(echo "$API_KEY_RESP" | python3 -c "import sys, json; d = json.load(sys.stdin); print(d.get('key') or d.get('api_key') or '')")
API_KEY_ID=$(echo "$API_KEY_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "API Key ID: $API_KEY_ID"
echo "API Key: ${API_KEY:0:20}..."
echo ""

# Step 8: Test API key authentication
echo "Step 8: Test API key authentication"
API_KEY_HEADERS="X-API-Key: $API_KEY"
ME_RESP=$(curl -s -X GET "$BASE_URL/api/auth/me" \
  -H "$API_KEY_HEADERS")
echo "$ME_RESP" | python3 -m json.tool 2>/dev/null || echo "$ME_RESP"
echo ""

# Step 9: Revoke API key
echo "Step 9: Revoke API key"
REVOKE_RESP=$(curl -s -X DELETE "$BASE_URL/api/v1/api-keys/$API_KEY_ID" \
  -H "$HEADERS")
echo "$REVOKE_RESP" | python3 -m json.tool 2>/dev/null || echo "$REVOKE_RESP"
echo ""

# Step 10: Verify API key no longer works
echo "Step 10: Verify revoked API key fails"
FAILED_RESP=$(curl -s -X GET "$BASE_URL/api/auth/me" \
  -H "$API_KEY_HEADERS")
echo "$FAILED_RESP" | python3 -m json.tool 2>/dev/null || echo "$FAILED_RESP"
echo ""

echo "=== Journey B Complete ==="
echo "Summary:"
echo "  - User registered and logged in"
echo "  - Experiment created: $EXPERIMENT_ID"
echo "  - Training job created: $JOB_ID"
echo "  - API key created and tested"
echo "  - API key revoked"
echo ""
echo "Note: Training job execution requires worker process."
echo "In normal runtime, worker would pick up job and execute it."
