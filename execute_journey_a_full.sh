#!/bin/bash
# Journey A: Complete flow through actual API calls

set -e

BASE_URL="http://localhost:8001"
EMAIL="journey-a-full@example.com"
PASSWORD="testpass123"

echo "=== Journey A: Personal AI Research (Full Flow) ==="
echo ""

# Step 1: Register
echo "1. Registering user..."
curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"display_name\":\"Journey A Full\",\"user_type\":\"individual\"}" | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'User ID: {d[\"id\"]}')"

# Step 2: Login
echo "2. Logging in..."
TOKEN=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
echo "Token obtained"

# Step 3: Check AI status
echo "3. Checking AI status..."
curl -s "$BASE_URL/api/v1/ai/status" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'AI mode: {d[\"mode\"]}, provider: {d[\"provider\"]}')"

# Step 4: Create conversation
echo "4. Creating AI conversation..."
CONV_ID=$(curl -s -X POST "$BASE_URL/api/v1/ai/conversations" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Momentum Strategy Research"}' | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Conversation ID: $CONV_ID"

# Step 5: Send message
echo "5. Sending message to AI..."
curl -s -X POST "$BASE_URL/api/v1/ai/conversations/$CONV_ID/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role":"user","content":"Help me design a momentum strategy for tech stocks"}' | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'AI response role: {d[\"role\"]}, content length: {len(d[\"content\"])}')"

# Step 6: Create draft
echo "6. Creating draft experiment..."
DRAFT_ID=$(curl -s -X POST "$BASE_URL/api/v1/ai/conversations/$CONV_ID/drafts" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Tech Momentum Strategy","original_idea":"Momentum-based strategy","research_type":"quantitative","hypothesis":"Tech stocks with strong momentum outperform","falsification_conditions":"Max drawdown > 25%","benchmark":"DEMO-BENCHMARK"}' | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Draft ID: $DRAFT_ID"

# Step 7: Create experiment
echo "7. Creating experiment from draft..."
EXP_RESP=$(curl -s -X POST "$BASE_URL/api/v1/experiments/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Tech Momentum Strategy","original_idea":"Momentum-based strategy","research_type":"quantitative","hypothesis":"Tech stocks with strong momentum outperform","falsification_conditions":"Max drawdown > 25%","benchmark":"DEMO-BENCHMARK","strategy_spec":{"schema_version":"1.0","template_type":"sma_crossover","asset_ids":["DEMO-ALPHA"],"frequency":"1d","start_date":"2024-01-02","end_date":"2024-06-28","initial_cash":100000.0,"signal_parameters":{"short_window":20,"long_window":50},"entry_rule":{"type":"sma_crossover","params":{"short_window":20,"long_window":50}},"exit_rule":{"type":"stop_loss","params":{"pct":0.05}},"sizing_rule":{"type":"equal_weight"},"execution_timing":{"execute_at":"next_open"},"commission_rate":0.001,"slippage_rate":0.0005,"benchmark_id":"DEMO-BENCHMARK","currency":"USD"}}')
EXP_ID=$(echo $EXP_RESP | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
VERSION_ID=$(echo $EXP_RESP | python3 -c "import sys, json; print(json.load(sys.stdin)['current_version_id'])")
echo "Experiment ID: $EXP_ID, Version ID: $VERSION_ID"

# Step 8: Run backtest
echo "8. Running backtest..."
BACKTEST_RESP=$(curl -s -X POST "$BASE_URL/api/v1/experiments/$EXP_ID/versions/$VERSION_ID/backtest" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}')
echo "Backtest result:"
echo $BACKTEST_RESP | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'Status: {d[\"status\"]}'); print(f'Metrics: {d[\"metrics\"]}')"

echo ""
echo "=== Journey A Complete ==="
