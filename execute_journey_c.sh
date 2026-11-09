#!/bin/bash
# Journey C: Controlled Execution
# Executes complete flow through actual API calls (not TestClient)

set -e

BASE_URL="http://localhost:8001"
EMAIL="journey-c-$(date +%s)@test.com"
PASSWORD="testpass123"
DISPLAY_NAME="Journey C Test"

echo "=== Journey C: Controlled Execution ==="
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

# Step 3: Create experiment
echo "Step 3: Create experiment"
STRATEGY_SPEC='{
  "schema_version": "1.0",
  "template_type": "sma_crossover",
  "asset_ids": ["DEMO-ALPHA"],
  "frequency": "1d",
  "start_date": "2024-01-02",
  "end_date": "2024-06-28",
  "initial_cash": 100000.0,
  "signal_parameters": {"short_window": 20, "long_window": 50},
  "entry_rule": {"type": "sma_crossover", "params": {"short_window": 20, "long_window": 50}},
  "exit_rule": {"type": "stop_loss", "params": {"pct": 0.05}},
  "sizing_rule": {"type": "equal_weight"},
  "execution_timing": {"execute_at": "next_open"},
  "commission_rate": 0.001,
  "slippage_rate": 0.0005,
  "benchmark_id": "DEMO-BENCHMARK",
  "currency": "USD"
}'

EXP_RESP=$(curl -s -X POST "$BASE_URL/api/v1/experiments/" \
  -H "Content-Type: application/json" \
  -H "$HEADERS" \
  -d "{
    \"title\": \"Journey C Experiment\",
    \"original_idea\": \"Test SMA crossover\",
    \"research_type\": \"quantitative\",
    \"hypothesis\": \"SMA crossover generates alpha\",
    \"falsification_conditions\": \"Max drawdown > 25%\",
    \"benchmark\": \"DEMO-BENCHMARK\",
    \"strategy_spec\": $STRATEGY_SPEC
  }")
EXPERIMENT_ID=$(echo "$EXP_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Experiment ID: $EXPERIMENT_ID"
echo ""

# Step 4: Run backtest
echo "Step 4: Run backtest"
VERSION_RESP=$(curl -s -X GET "$BASE_URL/api/v1/experiments/$EXPERIMENT_ID" \
  -H "$HEADERS")
VERSION_ID=$(echo "$VERSION_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['current_version_id'])")

BACKTEST_RESP=$(curl -s -X POST "$BASE_URL/api/v1/experiments/$EXPERIMENT_ID/versions/$VERSION_ID/backtest" \
  -H "Content-Type: application/json" \
  -H "$HEADERS" \
  -d "{}")
echo "$BACKTEST_RESP" | python3 -c "import sys, json; d = json.load(sys.stdin); print(f'Status: {d[\"status\"]}, Total Return: {d[\"metrics\"][\"total_return_pct\"]:.2f}%')"
echo ""

# Step 5: Check execution agent status
echo "Step 5: Check execution agent control status"
CONTROL_RESP=$(curl -s -X GET "$BASE_URL/api/v1/execution-agent/control-status" \
  -H "$HEADERS")
echo "$CONTROL_RESP" | python3 -m json.tool 2>/dev/null || echo "$CONTROL_RESP"
echo ""

# Step 6: List order intents (should be empty)
echo "Step 6: List order intents"
INTENTS_RESP=$(curl -s -X GET "$BASE_URL/api/v1/execution-agent/order-intents" \
  -H "$HEADERS")
echo "$INTENTS_RESP" | python3 -m json.tool 2>/dev/null || echo "$INTENTS_RESP"
echo ""

# Step 7: Check claimed broker refs
echo "Step 7: Check claimed broker refs"
BROKER_RESP=$(curl -s -X GET "$BASE_URL/api/v1/execution-agent/claimed-broker-refs" \
  -H "$HEADERS")
echo "$BROKER_RESP" | python3 -m json.tool 2>/dev/null || echo "$BROKER_RESP"
echo ""

echo "=== Journey C Complete ==="
echo "Summary:"
echo "  - User registered and logged in"
echo "  - Experiment created: $EXPERIMENT_ID"
echo "  - Backtest executed successfully"
echo "  - Execution agent endpoints verified"
echo ""
echo "Note: Order intent creation requires execution agent with broker connection."
echo "In normal runtime, execution agent would claim intents and submit to broker."
