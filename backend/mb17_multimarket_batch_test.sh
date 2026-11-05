#!/bin/bash
# Multi-market batch testing script
# Tests crypto, stock, and forex instruments across multiple timeframes

set -e

BASE_URL="http://127.0.0.1:8000"
AUTH_HEADER="X-Test-Auth: localhost"
OUTPUT_DIR="/Users/tommylam0915/Documents/Qoder/2026-11-03/ac51e592/alethra/backend"
RESULTS_FILE="$OUTPUT_DIR/mb17_multimarket_batch_results.json"

echo "================================================================================"
echo "MB-17 Multi-Market Batch Testing"
echo "================================================================================"
echo "Started at: $(date -u +"%Y-%m-%dT%H:%M:%S.000Z")"
echo ""

# Initialize results file
cat > "$RESULTS_FILE" << 'EOF'
{
  "batch_id": "MB-17-MultiMarket",
  "started_at": "TIMESTAMP_PLACEHOLDER",
  "calls": []
}
EOF

# Replace timestamp
sed -i.bak "s/TIMESTAMP_PLACEHOLDER/$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")/" "$RESULTS_FILE"
rm -f "$RESULTS_FILE.bak"

# Test configurations
declare -a INSTRUMENTS=(
  "CRYPTO:KRAKEN:BTC-USD"
  "CRYPTO:KRAKEN:ETH-USD"
  "CRYPTO:KRAKEN:SOL-USD"
  "US:AAPL"
  "US:TSLA"
  "US:SPY"
  "FOREX:EUR/USD"
  "FOREX:GBP/USD"
  "FOREX:USD/JPY"
)

declare -a INTERVALS=(1440 240 60)

# Commission rates by market
get_commission() {
  local instrument=$1
  if [[ $instrument == CRYPTO:* ]]; then
    echo "0.002"  # 0.2% for crypto
  elif [[ $instrument == US:* ]]; then
    echo "0.001"  # 0.1% for stocks
  else
    echo "0.0001"  # 0.01% for forex
  fi
}

# Slippage rates by market
get_slippage() {
  local instrument=$1
  if [[ $instrument == CRYPTO:* ]]; then
    echo "0.001"  # 0.1% for crypto
  elif [[ $instrument == US:* ]]; then
    echo "0.0005"  # 0.05% for stocks
  else
    echo "0.0001"  # 0.01% for forex
  fi
}

# Provider by market
get_provider() {
  local instrument=$1
  if [[ $instrument == CRYPTO:* ]]; then
    echo "kraken"
  elif [[ $instrument == US:* ]]; then
    echo "synthetic_stock"
  else
    echo "synthetic_forex"
  fi
}

# Counter
TOTAL=${#INSTRUMENTS[@]}
CURRENT=0

# Test each instrument
for instrument in "${INSTRUMENTS[@]}"; do
  for interval in "${INTERVALS[@]}"; do
    CURRENT=$((CURRENT + 1))
    commission=$(get_commission "$instrument")
    slippage=$(get_slippage "$instrument")
    provider=$(get_provider "$instrument")
    
    echo "[$CURRENT/${#INSTRUMENTS[@]}x${#INTERVALS[@]}] Testing $instrument @ ${interval}min..."
    
    # Make API call
    response=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/strategy-lab/research" \
      -H "Content-Type: application/json" \
      -H "$AUTH_HEADER" \
      -d "{
        \"instrument_id\": \"$instrument\",
        \"interval_minutes\": $interval,
        \"folds\": 3,
        \"starting_cash\": 10000,
        \"notional_per_trade\": 1000,
        \"commission_rate\": $commission,
        \"slippage_rate\": $slippage,
        \"include_trades\": false
      }")
    
    # Extract HTTP status code
    http_code=$(echo "$response" | tail -n1)
    response_body=$(echo "$response" | sed '$d')
    
    if [ "$http_code" != "200" ]; then
      echo "  ERROR: HTTP $http_code"
      echo "  Response: $response_body"
      continue
    fi
    
    # Extract metrics
    bars=$(echo "$response_body" | .venv/bin/python -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('bars', {}).get('count', 0))
" 2>/dev/null || echo "0")
    
    trades=$(echo "$response_body" | .venv/bin/python -c "
import json, sys
data = json.load(sys.stdin)
wf = data.get('walk_forward', {}).get('out_of_sample', {})
print(wf.get('trades', 0))
" 2>/dev/null || echo "0")
    
    net_pnl=$(echo "$response_body" | .venv/bin/python -c "
import json, sys
data = json.load(sys.stdin)
wf = data.get('walk_forward', {}).get('out_of_sample', {})
print(f'{float(wf.get(\"net_pnl\", 0)):.2f}')
" 2>/dev/null || echo "0.00")
    
    win_rate=$(echo "$response_body" | .venv/bin/python -c "
import json, sys
data = json.load(sys.stdin)
wf = data.get('walk_forward', {}).get('out_of_sample', {})
print(f'{float(wf.get(\"win_rate\", 0))*100:.1f}')
" 2>/dev/null || echo "0.0")
    
    snr=$(echo "$response_body" | .venv/bin/python -c "
import json, sys
data = json.load(sys.stdin)
wf = data.get('walk_forward', {}).get('out_of_sample', {})
print(wf.get('mean_over_stdev_per_trade', 'N/A'))
" 2>/dev/null || echo "N/A")
    
    echo "  Status: OK | Bars: $bars | Trades: $trades | Net PnL: $net_pnl | Win Rate: $win_rate% | SNR: $snr"
    
    # Append to results file
    .venv/bin/python << PYEOF >> "$RESULTS_FILE"
import json

result = {
    "instrument_id": "$instrument",
    "interval_minutes": $interval,
    "commission_rate": $commission,
    "slippage_rate": $slippage,
    "provider": "$provider",
    "status": "OK",
    "bars": $bars,
    "trades": $trades,
    "net_pnl": $net_pnl,
    "win_rate": $win_rate,
    "snr": "$snr",
    "response": json.loads('''$response_body''')
}

with open("$RESULTS_FILE", "r") as f:
    data = json.load(f)

data["calls"].append(result)

with open("$RESULTS_FILE", "w") as f:
    json.dump(data, f, indent=2)
PYEOF
    
  done
done

echo ""
echo "================================================================================"
echo "Batch execution complete. Results saved to:"
echo "  $RESULTS_FILE"
echo "================================================================================"
echo "Completed at: $(date -u +"%Y-%m-%dT%H:%M:%S.000Z")"
