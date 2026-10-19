#!/bin/bash
# Milestone 2: Verify quota persistence across sessions (with actual restart)

set -e

BASE_URL="http://localhost:8001"
TIMESTAMP=$(date +%s)
EMAIL="quota-persistence-${TIMESTAMP}@example.com"
PASSWORD="testpass123"
WORKTREE="/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto"

echo "=== Milestone 2: Quota Persistence Verification (Actual Restart) ==="
echo ""

# Step 1: Register user
echo "1. Registering user..."
curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"display_name\":\"Quota Test\",\"user_type\":\"individual\"}" > /dev/null

# Step 2: Login (Session 1)
echo "2. Login Session 1..."
TOKEN1=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

# Step 3: Check initial quota (parse JSON directly, no prefix text)
echo "3. Check initial quota (Session 1)..."
QUOTA1=$(curl -s "$BASE_URL/api/v1/usage/quota?quota_type=ai_usage" \
  -H "Authorization: Bearer $TOKEN1")
USED_INITIAL=$(echo "$QUOTA1" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('used', 0))")
LIMIT_INITIAL=$(echo "$QUOTA1" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('limit', 'N/A'))")
echo "Initial quota: used=$USED_INITIAL, limit=$LIMIT_INITIAL"

# Step 4: Create AI conversation (uses quota)
echo ""
echo "4. Creating AI conversation (uses quota)..."
CONV_ID=$(curl -s -X POST "$BASE_URL/api/v1/ai/conversations" \
  -H "Authorization: Bearer $TOKEN1" \
  -H "Content-Type: application/json" \
  -d '{"title":"Quota Test"}' | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Conversation created: $CONV_ID"

# Step 5: Send message (uses quota)
echo "5. Sending message (uses quota)..."
MSG_RESP=$(curl -s -X POST "$BASE_URL/api/v1/ai/conversations/$CONV_ID/messages" \
  -H "Authorization: Bearer $TOKEN1" \
  -H "Content-Type: application/json" \
  -d '{"role":"user","content":"Test message for quota tracking"}')
MSG_ID=$(echo "$MSG_RESP" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('id','unknown'))")
echo "Message sent: $MSG_ID"

# Step 6: Check quota after usage (Session 1)
echo ""
echo "6. Check quota after usage (Session 1)..."
QUOTA2=$(curl -s "$BASE_URL/api/v1/usage/quota?quota_type=ai_usage" \
  -H "Authorization: Bearer $TOKEN1")
USED_AFTER_USAGE=$(echo "$QUOTA2" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('used', 0))")
echo "Quota after usage: used=$USED_AFTER_USAGE"

# Step 7: Verify quota actually increased
echo ""
echo "7. Verifying quota increased after usage..."
if [ "$USED_AFTER_USAGE" -gt "$USED_INITIAL" ] 2>/dev/null; then
  echo "PASS: Quota increased ($USED_INITIAL -> $USED_AFTER_USAGE)"
else
  echo "FAIL: Quota did not increase after AI message ($USED_INITIAL -> $USED_AFTER_USAGE)"
  exit 1
fi

# Step 8: Record backend PID and restart
PID_BEFORE=$(lsof -i :8001 -P -n 2>/dev/null | awk 'NR>1{print $2; exit}')
echo ""
echo "8. Restarting backend (PID $PID_BEFORE)..."
kill "$PID_BEFORE"
sleep 2

if kill -0 "$PID_BEFORE" 2>/dev/null; then
  kill -9 "$PID_BEFORE"
  sleep 1
fi

BACKEND_DIR="/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend"

cd "$BACKEND_DIR"
source venv/bin/activate
export DATABASE_URL="postgresql://test:test@localhost:5434/alethra_acceptance"
export JWT_SECRET_KEY="acceptance-test-secret-minimum-32-chars-long"
export AI_PROVIDER="mock"
export DATA_PROVIDER="demo"
export APP_ENV="development"
nohup uvicorn app.main:app --host 0.0.0.0 --port 8001 > /tmp/alethra-quota-restart.log 2>&1 &
NEW_PID=$!
echo "New backend PID: $NEW_PID"

# Step 9: Wait for backend
echo ""
echo "9. Waiting for backend to start..."
for i in $(seq 1 30); do
  if curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health" 2>/dev/null | grep -q "200"; then
    echo "Backend ready after ${i}s"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Backend did not start within 30s"
    tail -20 /tmp/alethra-quota-restart.log
    exit 1
  fi
  sleep 1
done

PID_AFTER=$(lsof -i :8001 -P -n 2>/dev/null | awk 'NR>1{print $2; exit}')
echo "PID changed: $PID_BEFORE -> $PID_AFTER"

# Step 10: Login again (Session 2)
echo ""
echo "10. Login Session 2 (after restart)..."
TOKEN2=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

# Step 11: Check quota in new session
echo "11. Check quota in Session 2 (should match post-usage)..."
QUOTA3=$(curl -s "$BASE_URL/api/v1/usage/quota?quota_type=ai_usage" \
  -H "Authorization: Bearer $TOKEN2")
USED_AFTER_RESTART=$(echo "$QUOTA3" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('used', 0))")
echo "Quota after restart: used=$USED_AFTER_RESTART"

# Step 12: Final assertions
echo ""
echo "12. Final assertions..."
PASS=true

echo "Quota progression: $USED_INITIAL -> $USED_AFTER_USAGE -> $USED_AFTER_RESTART"

if [ "$USED_AFTER_RESTART" == "$USED_AFTER_USAGE" ]; then
  echo "PASS: Quota persisted across restart ($USED_AFTER_USAGE == $USED_AFTER_RESTART)"
else
  echo "FAIL: Quota changed after restart ($USED_AFTER_USAGE != $USED_AFTER_RESTART)"
  PASS=false
fi

if [ "$PID_BEFORE" != "$PID_AFTER" ]; then
  echo "PASS: Backend actually restarted (PID changed)"
else
  echo "FAIL: Backend PID did not change"
  PASS=false
fi

echo ""
if [ "$PASS" = true ]; then
  echo "=== Quota Persistence Verification PASSED ==="
else
  echo "=== Quota Persistence Verification FAILED ==="
  exit 1
fi
