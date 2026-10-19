#!/bin/bash
# Milestone 2: Verify data persistence across backend restart

set -e

BASE_URL="http://localhost:8001"
BACKEND_DIR="/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend"
TIMESTAMP=$(date +%s)
EMAIL="persistence-${TIMESTAMP}@example.com"
PASSWORD="testpass123"

echo "=== Milestone 2: Data Persistence Verification ==="
echo ""

# Step 1: Register user and create data
echo "1. Creating test data..."
curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"display_name\":\"Persistence Test\",\"user_type\":\"individual\"}" > /dev/null

TOKEN=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

# Create AI conversation
CONV_ID=$(curl -s -X POST "$BASE_URL/api/v1/ai/conversations" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Persistence Test Conversation"}' | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")

echo "Created conversation: $CONV_ID"

# Send AI message to create data
curl -s -X POST "$BASE_URL/api/v1/ai/conversations/$CONV_ID/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role":"user","content":"Test message for persistence"}' > /dev/null

# Create experiment
EXP_RESP=$(curl -s -X POST "$BASE_URL/api/v1/experiments/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Persistence Test Experiment","original_idea":"Test","research_type":"quantitative","hypothesis":"Test","falsification_conditions":"Test","benchmark":"DEMO-BENCHMARK","strategy_spec":{"schema_version":"1.0","template_type":"buy_and_hold","asset_ids":["DEMO-ALPHA"],"frequency":"1d","start_date":"2024-01-02","end_date":"2024-06-28","initial_cash":100000.0,"signal_parameters":{},"entry_rule":{},"exit_rule":{},"sizing_rule":{},"execution_timing":{"execute_at":"next_open"},"commission_rate":0.0,"slippage_rate":0.0,"benchmark_id":"DEMO-BENCHMARK","currency":"USD"}}')
EXP_ID=$(echo "$EXP_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")

echo "Created experiment: $EXP_ID"

# Step 2: Record data BEFORE restart
echo "2. Recording data before restart..."
CONV_BEFORE=$(curl -s "$BASE_URL/api/v1/ai/conversations" -H "Authorization: Bearer $TOKEN")
CONV_COUNT_BEFORE=$(echo "$CONV_BEFORE" | python3 -c "import sys, json; print(len(json.load(sys.stdin)))")
EXP_COUNT_BEFORE=$(curl -s "$BASE_URL/api/v1/experiments/" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys, json; print(len(json.load(sys.stdin)))")

echo "Before restart: $CONV_COUNT_BEFORE conversations, $EXP_COUNT_BEFORE experiments"

# Verify specific data exists
CONV_EXISTS_BEFORE=$(echo "$CONV_BEFORE" | python3 -c "import sys, json; data=json.load(sys.stdin); print(any(c['id']=='$CONV_ID' for c in data))")
EXP_EXISTS_BEFORE=$(curl -s "$BASE_URL/api/v1/experiments/" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys, json; data=json.load(sys.stdin); print(any(e['id']=='$EXP_ID' for e in data))")
echo "Conversation $CONV_ID exists: $CONV_EXISTS_BEFORE"
echo "Experiment $EXP_ID exists: $EXP_EXISTS_BEFORE"

# Step 3: Record database state
echo "3. Recording database state..."
docker exec alethra-e2e-db psql -U test -d alethra_acceptance -c "SELECT COUNT(*) as conversation_count FROM ai_conversations;" -t
docker exec alethra-e2e-db psql -U test -d alethra_acceptance -c "SELECT COUNT(*) as experiment_count FROM experiments;" -t

# Step 4: Kill backend
echo "4. Killing backend process..."
BACKEND_PID=$(pgrep -f "uvicorn app.main:app.*--port 8001" | head -1)
if [ -z "$BACKEND_PID" ]; then
  echo "ERROR: Could not find backend PID on port 8001"
  exit 1
fi
echo "Backend PID: $BACKEND_PID"
kill "$BACKEND_PID"
echo "Waiting for backend to stop..."
sleep 2

# Verify backend is down
if curl -s --connect-timeout 2 "$BASE_URL/health" > /dev/null 2>&1; then
  echo "WARNING: Backend still responding after kill, forcing..."
  kill -9 "$BACKEND_PID" 2>/dev/null || true
  sleep 1
fi
echo "Backend stopped."

# Step 5: Restart backend with same env vars
echo "5. Restarting backend..."
cd "$BACKEND_DIR"
source venv/bin/activate
export DATABASE_URL="postgresql://test:test@localhost:5434/alethra_acceptance"
export JWT_SECRET_KEY="acceptance-test-secret-minimum-32-chars-long"
export AI_PROVIDER="mock"
export DATA_PROVIDER="demo"
export APP_ENV="development"
nohup uvicorn app.main:app --host 0.0.0.0 --port 8001 > /tmp/alethra-backend-restart.log 2>&1 &
NEW_PID=$!
echo "New backend PID: $NEW_PID"

# Step 6: Wait for backend to be ready
echo "6. Waiting for backend to start..."
for i in $(seq 1 30); do
  if curl -s --connect-timeout 1 "$BASE_URL/health" > /dev/null 2>&1; then
    echo "Backend ready after ${i}s"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Backend did not start within 30s"
    exit 1
  fi
  sleep 1
done

# Step 7: Verify data AFTER restart
echo "7. Verifying data after restart..."
CONV_AFTER=$(curl -s "$BASE_URL/api/v1/ai/conversations" -H "Authorization: Bearer $TOKEN")
CONV_COUNT_AFTER=$(echo "$CONV_AFTER" | python3 -c "import sys, json; print(len(json.load(sys.stdin)))")
EXP_COUNT_AFTER=$(curl -s "$BASE_URL/api/v1/experiments/" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys, json; print(len(json.load(sys.stdin)))")

echo "After restart: $CONV_COUNT_AFTER conversations, $EXP_COUNT_AFTER experiments"

# Verify specific data still exists
CONV_EXISTS_AFTER=$(echo "$CONV_AFTER" | python3 -c "import sys, json; data=json.load(sys.stdin); print(any(c['id']=='$CONV_ID' for c in data))")
EXP_EXISTS_AFTER=$(curl -s "$BASE_URL/api/v1/experiments/" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys, json; data=json.load(sys.stdin); print(any(e['id']=='$EXP_ID' for e in data))")
echo "Conversation $CONV_ID exists: $CONV_EXISTS_AFTER"
echo "Experiment $EXP_ID exists: $EXP_EXISTS_AFTER"

# Step 8: Compare before and after
echo "8. Comparing before and after..."
echo "Conversations: $CONV_COUNT_BEFORE -> $CONV_COUNT_AFTER"
echo "Experiments: $EXP_COUNT_BEFORE -> $EXP_COUNT_AFTER"

# Step 9: Final verdict
echo "9. Final verdict..."
if [ "$CONV_COUNT_BEFORE" == "$CONV_COUNT_AFTER" ] && \
   [ "$EXP_COUNT_BEFORE" == "$EXP_COUNT_AFTER" ] && \
   [ "$CONV_EXISTS_BEFORE" == "True" ] && [ "$CONV_EXISTS_AFTER" == "True" ] && \
   [ "$EXP_EXISTS_BEFORE" == "True" ] && [ "$EXP_EXISTS_AFTER" == "True" ]; then
  echo "SUCCESS: All data persisted across backend restart"
  echo "  - Conversation count: $CONV_COUNT_BEFORE == $CONV_COUNT_AFTER"
  echo "  - Experiment count: $EXP_COUNT_BEFORE == $EXP_COUNT_AFTER"
  echo "  - Specific conversation $CONV_ID: survived"
  echo "  - Specific experiment $EXP_ID: survived"
  exit 0
else
  echo "FAILURE: Data did not persist across backend restart"
  echo "  - Conversation count: $CONV_COUNT_BEFORE -> $CONV_COUNT_AFTER"
  echo "  - Experiment count: $EXP_COUNT_BEFORE -> $EXP_COUNT_AFTER"
  echo "  - Conversation exists: $CONV_EXISTS_BEFORE -> $CONV_EXISTS_AFTER"
  echo "  - Experiment exists: $EXP_EXISTS_BEFORE -> $EXP_EXISTS_AFTER"
  exit 1
fi
