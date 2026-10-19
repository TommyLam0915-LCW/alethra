#!/bin/bash
# Journey A: Complete flow through actual API calls

set -e

BASE_URL="http://localhost:8001"
TIMESTAMP=$(date +%s)
EMAIL="journey-a-full-${TIMESTAMP}@example.com"
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

# Step 5: Send message and capture AI response
echo "5. Sending message to AI..."
AI_RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/ai/conversations/$CONV_ID/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role":"user","content":"Help me design a momentum strategy for tech stocks"}')

AI_MESSAGE_ID=$(echo "$AI_RESPONSE" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['id'])")
AI_CONTENT=$(echo "$AI_RESPONSE" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['content'])")
echo "AI response role: $(echo "$AI_RESPONSE" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['role'])")"
echo "AI message ID: $AI_MESSAGE_ID"
echo "AI content length: ${#AI_CONTENT}"

if [ "${#AI_CONTENT}" -eq 0 ]; then
  echo "FAIL: AI assistant returned empty content"
  exit 1
fi

# Step 6: Create draft from AI response
echo "6. Creating draft experiment from AI response..."
DRAFT_BODY=$(python3 -c "
import json
body = {
    'title': 'Tech Momentum Strategy',
    'hypothesis': 'Tech stocks with strong momentum outperform the benchmark',
    'falsification_criteria': 'Max drawdown exceeds 25% or Sharpe ratio below 0.5',
    'strategy_template': 'sma_crossover',
    'asset_symbol': 'DEMO-ALPHA',
    'benchmark_symbol': 'DEMO-BENCHMARK',
    'initial_cash': '100000',
    'commission_rate': '0.001',
    'slippage_rate': '0.0005',
    'research_rules': {'entry': 'SMA 20/50 crossover', 'exit': 'stop_loss 5%'},
    'source_message_id': '$AI_MESSAGE_ID'
}
print(json.dumps(body))
")
DRAFT_ID=$(curl -s -X POST "$BASE_URL/api/v1/ai/conversations/$CONV_ID/drafts" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$DRAFT_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Draft ID: $DRAFT_ID"

# Step 6b: Review and approve draft
echo "6b. Reviewing and approving draft..."
APPROVE_RESP=$(curl -s -X POST "$BASE_URL/api/v1/research-drafts/$DRAFT_ID/approve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"accept","notes":"Approved: AI-generated momentum strategy looks sound"}')
DRAFT_STATUS=$(echo "$APPROVE_RESP" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status','unknown'))")
echo "Draft status after approval: $DRAFT_STATUS"

# Step 6c: Read back accepted draft and verify fields
echo "6c. Reading back accepted draft..."
DRAFT_READBACK=$(curl -s "$BASE_URL/api/v1/research-drafts/$DRAFT_ID" \
  -H "Authorization: Bearer $TOKEN")
DRAFT_TITLE=$(echo "$DRAFT_READBACK" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('title',''))")
DRAFT_HYPOTHESIS=$(echo "$DRAFT_READBACK" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('hypothesis',''))")
DRAFT_STATUS_READBACK=$(echo "$DRAFT_READBACK" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status',''))")
DRAFT_SOURCE_CONV=$(echo "$DRAFT_READBACK" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('source_conversation_id',''))")
echo "Draft title: $DRAFT_TITLE"
echo "Draft hypothesis: ${DRAFT_HYPOTHESIS:0:60}..."
echo "Draft status: $DRAFT_STATUS_READBACK"
echo "Draft source_conversation_id: $DRAFT_SOURCE_CONV"

if [ "$DRAFT_STATUS_READBACK" != "accepted" ]; then
  echo "FAIL: Draft status is not accepted ($DRAFT_STATUS_READBACK)"
  exit 1
fi

if [ "$DRAFT_SOURCE_CONV" != "$CONV_ID" ]; then
  echo "FAIL: source_conversation_id not persisted correctly ($DRAFT_SOURCE_CONV != $CONV_ID)"
  exit 1
fi
echo "PASS: source_conversation_id matches"

AUDIT_RESP=$(curl -s "$BASE_URL/api/v1/research-drafts/$DRAFT_ID/audit-events" \
  -H "Authorization: Bearer $TOKEN")
SOURCE_MSG_IN_AUDIT=$(echo "$AUDIT_RESP" | python3 -c "
import sys, json
events = json.load(sys.stdin)
for e in events:
    if e.get('event_type') == 'research_draft_created_from_ai':
        print(e.get('event_data',{}).get('source_message_id',''))
        break
")
if [ "$SOURCE_MSG_IN_AUDIT" != "$AI_MESSAGE_ID" ]; then
  echo "FAIL: source_message_id not in audit event ($SOURCE_MSG_IN_AUDIT != $AI_MESSAGE_ID)"
  exit 1
fi
echo "PASS: source_message_id verified in audit trail"

# Step 7: Create experiment from accepted draft content (use draft's actual fields)
echo "7. Creating experiment from accepted draft fields..."
EXP_BODY=$(python3 -c "
import json
# Use draft's actual fields, not hard-coded values
draft_title = '$DRAFT_TITLE'
draft_hypothesis = '$DRAFT_HYPOTHESIS'
body = {
    'title': draft_title,
    'original_idea': f'Derived from accepted draft: {draft_title}',
    'research_type': 'quantitative',
    'hypothesis': draft_hypothesis,
    'falsification_conditions': 'Max drawdown exceeds 25% or Sharpe ratio below 0.5',
    'benchmark': 'DEMO-BENCHMARK',
    'strategy_spec': {
        'schema_version': '1.0',
        'template_type': 'sma_crossover',
        'asset_ids': ['DEMO-ALPHA'],
        'frequency': '1d',
        'start_date': '2024-01-02',
        'end_date': '2024-06-28',
        'initial_cash': 100000.0,
        'signal_parameters': {'short_window': 20, 'long_window': 50},
        'entry_rule': {'type': 'sma_crossover', 'params': {'short_window': 20, 'long_window': 50}},
        'exit_rule': {'type': 'stop_loss', 'params': {'pct': 0.05}},
        'sizing_rule': {'type': 'equal_weight'},
        'execution_timing': {'execute_at': 'next_open'},
        'commission_rate': 0.001,
        'slippage_rate': 0.0005,
        'benchmark_id': 'DEMO-BENCHMARK',
        'currency': 'USD'
    }
}
print(json.dumps(body))
")
EXP_RESP=$(curl -s -X POST "$BASE_URL/api/v1/experiments/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$EXP_BODY")
EXP_ID=$(echo "$EXP_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
VERSION_ID=$(echo "$EXP_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['current_version_id'])")
echo "Experiment ID: $EXP_ID, Version ID: $VERSION_ID"

# Step 7a: Verify experiment content matches accepted draft
echo "7a. Verifying experiment content matches accepted draft..."
EXP_READBACK=$(curl -s "$BASE_URL/api/v1/experiments/$EXP_ID" \
  -H "Authorization: Bearer $TOKEN")
EXP_TITLE=$(echo "$EXP_READBACK" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('title',''))")
EXP_HYPOTHESIS=$(echo "$EXP_READBACK" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('hypothesis',''))")
echo "Experiment title: $EXP_TITLE"
echo "Experiment hypothesis: ${EXP_HYPOTHESIS:0:60}..."

PASS_CONTENT=true
if [ "$EXP_TITLE" == "$DRAFT_TITLE" ]; then
  echo "PASS: Experiment title matches draft title"
else
  echo "FAIL: Experiment title does not match draft ($EXP_TITLE != $DRAFT_TITLE)"
  PASS_CONTENT=false
fi

if [ "$EXP_HYPOTHESIS" == "$DRAFT_HYPOTHESIS" ]; then
  echo "PASS: Experiment hypothesis matches draft hypothesis"
else
  echo "FAIL: Experiment hypothesis does not match draft"
  PASS_CONTENT=false
fi

if [ "$PASS_CONTENT" != "true" ]; then
  echo "FAIL: Content transfer from draft to experiment failed"
  exit 1
fi

# Step 7b: Link experiment back to draft
echo "7b. Linking experiment to draft..."
LINK_RESP=$(curl -s -w "\n%{http_code}" -X PATCH "$BASE_URL/api/v1/research-drafts/$DRAFT_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"experiment_id\":\"$EXP_ID\"}")
LINK_HTTP=$(echo "$LINK_RESP" | tail -1)
LINK_BODY=$(echo "$LINK_RESP" | sed '$d')
LINKED_EXP=$(echo "$LINK_BODY" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('experiment_id',''))")
echo "Draft experiment_id linked: $LINKED_EXP"

if [ "$LINK_HTTP" != "200" ]; then
  echo "FAIL: Draft linking returned HTTP $LINK_HTTP"
  exit 1
fi
if [ "$LINKED_EXP" != "$EXP_ID" ]; then
  echo "FAIL: experiment_id not persisted ($LINKED_EXP != $EXP_ID)"
  exit 1
fi
echo "PASS: Experiment linked to draft"

# Step 8: Run backtest
echo "8. Running backtest..."
BACKTEST_RESP=$(curl -s -X POST "$BASE_URL/api/v1/experiments/$EXP_ID/versions/$VERSION_ID/backtest" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}')
echo "Backtest result:"
echo "$BACKTEST_RESP" | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'Status: {d[\"status\"]}'); print(f'Metrics: {d[\"metrics\"]}')"

echo ""
echo "=== Journey A Complete ==="
