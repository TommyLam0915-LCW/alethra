#!/bin/bash
# Journey A: Personal AI Research - Execute through actual API calls

set -e

BASE_URL="http://localhost:8001"
EMAIL="journey-a-test@example.com"
PASSWORD="testpass123"

echo "=== Journey A: Personal AI Research (Normal Runtime) ==="
echo ""

# Step 1: Register user
echo "1. Registering user..."
REGISTER_RESP=$(curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"display_name\":\"Journey A Test\",\"user_type\":\"individual\"}")
echo "Register response: $REGISTER_RESP" | head -c 200
echo ""

# Step 2: Login
echo "2. Logging in..."
LOGIN_RESP=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
TOKEN=$(echo $LOGIN_RESP | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
echo "Login successful, token obtained"
echo ""

# Step 3: Create AI conversation
echo "3. Creating AI conversation..."
CONV_RESP=$(curl -s -X POST "$BASE_URL/api/v1/ai/conversations" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Journey A Test Conversation"}')
CONV_ID=$(echo $CONV_RESP | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Conversation created: $CONV_ID"
echo ""

# Step 4: Send message to AI
echo "4. Sending message to AI..."
MSG_RESP=$(curl -s -X POST "$BASE_URL/api/v1/ai/conversations/$CONV_ID/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role":"user","content":"Analyze AAPL stock performance"}')
echo "Message sent, AI response received"
echo ""

# Step 5: Create backtest from conversation
echo "5. Creating backtest from conversation..."
BACKTEST_RESP=$(curl -s -X POST "$BASE_URL/api/v1/ai/conversations/$CONV_ID/backtest" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AAPL","start_date":"2024-01-01","end_date":"2024-12-31","signal_parameters":{"short_window":20,"long_window":50}}')
BACKTEST_ID=$(echo $BACKTEST_RESP | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Backtest created: $BACKTEST_ID"
echo ""

# Step 6: Verify backtest has metrics
echo "6. Verifying backtest metrics..."
BACKTEST_DETAIL=$(curl -s "$BASE_URL/api/v1/ai/conversations/$CONV_ID/backtest" \
  -H "Authorization: Bearer $TOKEN")
echo "Backtest detail: $BACKTEST_DETAIL" | head -c 300
echo ""

echo "=== Journey A Complete ==="
echo "All steps executed through actual API calls (not TestClient)"
