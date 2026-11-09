#!/bin/bash
# Journey B: ML Training + API Access - Execute through actual API calls

set -e

BASE_URL="http://localhost:8001"
EMAIL="journey-b-test@example.com"
PASSWORD="testpass123"

echo "=== Journey B: ML Training + API Access (Normal Runtime) ==="
echo ""

# Step 1: Register user
echo "1. Registering user..."
curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"display_name\":\"Journey B Test\",\"user_type\":\"individual\"}" | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'User ID: {d[\"id\"]}')"

# Step 2: Login
echo "2. Logging in..."
TOKEN=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
echo "Token obtained"

# Step 3: Create API key with inference:run scope
echo "3. Creating API key..."
API_KEY_RESP=$(curl -s -X POST "$BASE_URL/api/v1/api-keys" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Journey B Test Key","scopes":["inference:run"]}')
API_KEY=$(echo $API_KEY_RESP | python3 -c "import sys, json; print(json.load(sys.stdin)['raw_key'])")
echo "API key created: ${API_KEY:0:20}..."

# Step 4: Train ML model
echo "4. Training ML model..."
TRAIN_RESP=$(curl -s -X POST "$BASE_URL/api/v1/ml/train" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model_type":"classification","training_data":{"features":[[1,2],[3,4],[5,6],[7,8]],"labels":[0,1,0,1]},"metadata":{"purpose":"Journey B test"}}')
MODEL_ID=$(echo $TRAIN_RESP | python3 -c "import sys, json; print(json.load(sys.stdin)['model_id'])")
echo "Model trained: $MODEL_ID"

# Step 5: List models
echo "5. Listing models..."
MODELS_RESP=$(curl -s "$BASE_URL/api/v1/ml/models" \
  -H "Authorization: Bearer $TOKEN")
echo "Models: $(echo $MODELS_RESP | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'Total: {d[\"total\"]}')")"

# Step 6: Get model detail
echo "6. Getting model detail..."
MODEL_DETAIL=$(curl -s "$BASE_URL/api/v1/ml/models/$MODEL_ID" \
  -H "Authorization: Bearer $TOKEN")
echo "Model detail: $(echo $MODEL_DETAIL | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'Type: {d[\"model_type\"]}, trained at: {d[\"trained_at\"]}')")"

# Step 7: Run inference using API key
echo "7. Running inference with API key..."
INFERENCE_RESP=$(curl -s -X POST "$BASE_URL/api/v1/ml/predict" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"model_id\":\"$MODEL_ID\",\"input_data\":[[2,3]]}")
echo "Inference result: $(echo $INFERENCE_RESP | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'Prediction: {d[\"prediction\"]}, confidence: {d.get(\"confidence\", \"N/A\")}')")"

# Step 8: Check quota usage
echo "8. Checking quota usage..."
QUOTA_RESP=$(curl -s "$BASE_URL/api/v1/usage/quota?quota_type=ai_usage" \
  -H "Authorization: Bearer $TOKEN")
echo "Quota: $(echo $QUOTA_RESP | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'Used: {d.get(\"used\", 0)}, Limit: {d.get(\"limit\", \"N/A\")}')")"

# Step 9: Revoke API key
echo "9. Revoking API key..."
API_KEY_ID=$(echo $API_KEY_RESP | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
REVOKE_RESP=$(curl -s -X DELETE "$BASE_URL/api/v1/api-keys/$API_KEY_ID" \
  -H "Authorization: Bearer $TOKEN")
echo "API key revoked"

# Step 10: Verify API key no longer works
echo "10. Verifying revoked key cannot inference..."
INFERENCE_RESP2=$(curl -s -X POST "$BASE_URL/api/v1/ml/predict" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"model_id\":\"$MODEL_ID\",\"input_data\":[[2,3]]}")
echo "Result after revoke: $(echo $INFERENCE_RESP2 | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'Status: {d.get(\"detail\", \"success\")}')")"

echo ""
echo "=== Journey B Complete ==="
