#!/bin/bash
# Milestone 2: Verify quota persistence across sessions

set -e

BASE_URL="http://localhost:8001"
EMAIL="quota-persistence@example.com"
PASSWORD="testpass123"

echo "=== Milestone 2: Quota Persistence Verification ==="
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

# Step 3: Check initial quota
echo "3. Check initial quota (Session 1)..."
QUOTA1=$(curl -s "$BASE_URL/api/v1/usage/quota?quota_type=ai_usage" \
  -H "Authorization: Bearer $TOKEN1")
echo "Initial quota: $QUOTA1" | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'Used: {d.get(\"used\", 0)}, Limit: {d.get(\"limit\", \"N/A\")}')"

# Step 4: Create AI conversation (uses quota)
echo "4. Creating AI conversation (uses quota)..."
CONV_ID=$(curl -s -X POST "$BASE_URL/api/v1/ai/conversations" \
  -H "Authorization: Bearer $TOKEN1" \
  -H "Content-Type: application/json" \
  -d '{"title":"Quota Test"}' | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")

# Step 5: Send message (uses quota)
echo "5. Sending message (uses quota)..."
curl -s -X POST "$BASE_URL/api/v1/ai/conversations/$CONV_ID/messages" \
  -H "Authorization: Bearer $TOKEN1" \
  -H "Content-Type: application/json" \
  -d '{"role":"user","content":"Test message"}' > /dev/null

# Step 6: Check quota after usage (Session 1)
echo "6. Check quota after usage (Session 1)..."
QUOTA2=$(curl -s "$BASE_URL/api/v1/usage/quota?quota_type=ai_usage" \
  -H "Authorization: Bearer $TOKEN1")
echo "Quota after usage: $QUOTA2" | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'Used: {d.get(\"used\", 0)}, Limit: {d.get(\"limit\", \"N/A\")}')"

# Step 7: Login again (Session 2 - simulates new session)
echo "7. Login Session 2 (new session)..."
TOKEN2=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

# Step 8: Check quota in new session
echo "8. Check quota in new session (should see same usage)..."
QUOTA3=$(curl -s "$BASE_URL/api/v1/usage/quota?quota_type=ai_usage" \
  -H "Authorization: Bearer $TOKEN2")
echo "Quota in Session 2: $QUOTA3" | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'Used: {d.get(\"used\", 0)}, Limit: {d.get(\"limit\", \"N/A\")}')"

# Step 9: Verify quota persisted
echo "9. Verifying quota persistence..."
USED1=$(echo $QUOTA2 | python3 -c "import sys, json; print(json.load(sys.stdin).get('used', 0))")
USED2=$(echo $QUOTA3 | python3 -c "import sys, json; print(json.load(sys.stdin).get('used', 0))")

if [ "$USED1" == "$USED2" ]; then
  echo "✅ SUCCESS: Quota persisted across sessions ($USED1 == $USED2)"
else
  echo "❌ FAILURE: Quota did not persist ($USED1 != $USED2)"
  exit 1
fi

echo ""
echo "=== Quota Persistence Verification Complete ==="
