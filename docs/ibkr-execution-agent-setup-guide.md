# IBKR Execution Agent — User Setup Guide

This guide walks you through setting up the Alethra Execution Agent on your local machine to connect your Interactive Brokers (IBKR) account to the Alethra platform.

> ## Current status: broker execution is contained
>
> **This build cannot place orders.** The `OFFLINE-ONLY-2026-10-27` containment
> policy disables real broker execution end to end:
>
> - `ALETHRA_BROKER_TRANSPORT` **no longer defaults** to `ibkr`. It has **no
>   default at all** — leaving it unset exits with an error, an unknown value
>   exits with an error, and `ibkr` is refused explicitly. Only `fake`
>   (in-memory offline simulation) starts.
> - Even if the IBKR transport is constructed directly in code, its
>   `connect()`, `submit_order()` and `cancel_order()` raise a containment
>   refusal before touching a gateway. Read-only adapters still work so
>   restart reconciliation of an already-mapped order stays possible.
> - The agent will not submit an order intent unless the platform binds it to a
>   trusted execution scope. The platform has no execution-scope column yet, so
>   today every real polled intent is refused: **authorizing an intent on the
>   platform is not a submission instruction.**
> - No environment variable (`TRADING_MODE`, `ALLOW_LIVE_TRADING`, port, or any
>   other name) lifts the containment. Turning it off is a code change under
>   **separate approval**.
>
> Everything below describes the **intended end state** for a broker-connected
> agent. Until the containment is lifted by a separate approval **and** a
> server-side scope/account binding exists, following those steps will produce
> the refusals above.
>
> Also note: the gateway **port is not proof of the account environment**.
> `7496`/`7497` are TWS defaults, IB Gateway commonly uses `4001` (live) and
> `4002` (paper), and both are configurable — so a port number only tells the
> agent where to connect. Never treat it as evidence that an account is paper.

## Architecture Overview

```
Your Machine
┌─────────────────────────────────────────┐
│  IB Gateway / TWS                       │
│  (API port you configure in Step 2)     │
│       ↓                                 │
│  Alethra Execution Agent (Python)       │
│       ↓                                 │
│  HTTPS → Alethra Platform API           │
└─────────────────────────────────────────┘
```

**Important**: The execution agent runs on YOUR machine and connects to YOUR IBKR account. Alethra platform never has access to your broker credentials.

---

## Prerequisites

1. **IBKR Account**
   - Paper trading account (for testing) — recommended first
   - Live trading account (for production)
   - Sign up at: https://www.interactivebrokers.com

2. **Python 3.12+**
   - Check version: `python3 --version`
   - Install if needed: https://www.python.org/downloads/

3. **Alethra API Key**
   - Log in to Alethra platform
   - Navigate to Settings → API Keys
   - Generate a new API key with "Execution Agent" scope
   - Save the key securely (you won't see it again)

---

## Step 1: Install IB Gateway

IB Gateway is a lightweight, headless version of TWS (Trader Workstation) designed for API access.

### Download

1. Go to: https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
2. Download the version for your operating system:
   - **macOS**: `.dmg` file
   - **Windows**: `.exe` file
   - **Linux**: `.sh` file

### Install

**macOS**:
```bash
# Open the .dmg file and drag IB Gateway to Applications
# Or via command line:
hdiutil attach ibgateway-stable-10.19.1h-x64.dmg
cp -R /Volumes/ibgateway*/IB\ Gateway.app /Applications/
```

**Windows**:
```
# Run the .exe installer and follow the prompts
```

**Linux**:
```bash
chmod +x ibgateway-stable-10.19.1h-x64.sh
./ibgateway-stable-10.19.1h-x64.sh
```

### Alternative: Use TWS (Trader Workstation)

If you prefer a GUI for debugging, you can use TWS instead:
- Download: https://www.interactivebrokers.com/en/trading/tws-latest.php
- Same API interface as IB Gateway

---

## Step 2: Configure IB Gateway API Access

1. **Launch IB Gateway**
   ```bash
   # macOS
   open /Applications/IB\ Gateway.app
   
   # Windows
   # Start menu → IB Gateway
   
   # Linux
   ./ibgateway
   ```

2. **Log in**
   - Use your IBKR username and password
   - Select "Paper" for testing or "Live" for production

3. **Enable API connections**
   - Go to: **File → Global Configuration → API → Settings**
   - Check: **"Enable ActiveX and Socket Clients"**
   - Set **Socket port** to the port your gateway listens on — see the note below
   - Check: **"Allow connections from localhost only"** (security)
   - Click **OK**

4. **Note the port number**
   - `7496` (live) / `7497` (paper) are the **TWS** defaults
   - IB Gateway commonly uses `4001` (live) / `4002` (paper)
   - Either way the port is configurable, so it only tells the agent **where to
     connect** — it is never evidence that an account is paper or live. Confirm
     the account environment from the gateway login and the returned account
     properties, not from the port.
   - You'll need this for the agent configuration

---

## Step 3: Install Alethra Execution Agent

1. **Clone or download the Alethra repository**
   ```bash
   git clone https://github.com/TommyLam0915-LCW/alethra.git
   cd alethra/backend
   ```

2. **Create a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # macOS/Linux
   # venv\Scripts\activate   # Windows
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install ib_insync
   ```

---

## Step 4: Configure the Agent

Create a configuration file or set environment variables.

### Option A: Environment Variables (Recommended)

```bash
export ALETHRA_API_URL="https://api.alethra.com"
export ALETHRA_API_KEY="your-api-key-here"

# Required, and there is NO default: 'fake' is the only value this build starts
# with. Setting 'ibkr' exits with the containment error until broker execution
# is approved separately.
export ALETHRA_BROKER_TRANSPORT="fake"

# Gateway settings — read by the IBKR transport, which is contained today.
export IB_GATEWAY_HOST="localhost"
export IB_GATEWAY_PORT="4002"   # IB Gateway paper default; configurable
export IB_CLIENT_ID="1"
export POLL_INTERVAL="5"
export LOG_LEVEL="INFO"
```

### Option B: Configuration File

> **Unsupported.** The agent reads configuration from environment variables
> only — there is no `agent_config.yaml` loader in `app/execution_agent/config.py`.
> The example below is kept for reference only; nothing reads it. The
> `OFFLINE-ONLY` containment policy in particular is a module constant and
> cannot be set from a config file or an environment variable.

Create `agent_config.yaml`:

```yaml
alethra_api_url: "https://api.alethra.com"
alethra_api_key: "your-api-key-here"
ib_gateway_host: "localhost"
ib_gateway_port: 7497  # Paper trading
ib_client_id: 1
poll_interval_seconds: 5
log_level: "INFO"
```

---

## Step 5: Run the Agent

```bash
# Make sure your virtual environment is activated

python -m app.execution_agent.agent
```

**In the current contained build this exits immediately** — the transport has no
default, so an unconfigured start fails closed instead of opening a gateway
session:

```
2026-10-27 10:00:00 [ERROR] app.execution_agent.agent: ALETHRA_BROKER_TRANSPORT is unset and has no default. Set it to 'fake' for offline simulation. IBKR broker execution is disabled by the OFFLINE-ONLY-2026-10-27 containment policy: ...
```

To run the agent's control loop against the in-memory fake transport (offline
simulation, no broker reachable):

```bash
export ALETHRA_BROKER_TRANSPORT="fake"
python -m app.execution_agent.agent
```

With `fake`, the agent polls intents, but note that a real platform intent
carries no execution scope yet, so the agent logs a scope refusal and makes no
broker call. Submitting an intent through the platform's own offline HTTP
simulation endpoint is unaffected and still simulates fills.

The gateway-connected output below is the **intended end state**, not reachable
in this build:

```
2026-10-15 10:00:00 [INFO] app.execution_agent.agent: Starting execution agent
2026-10-15 10:00:01 [INFO] app.execution_agent.agent: Connecting to IB Gateway at localhost:7497
2026-10-15 10:00:01 [INFO] app.execution_agent.agent: Connected to IB Gateway
2026-10-15 10:00:01 [INFO] app.execution_agent.agent: Connecting to Alethra platform at https://api.alethra.com
2026-10-15 10:00:02 [INFO] app.execution_agent.agent: Connected to Alethra platform
```

Once running, the agent is designed to:
- Poll Alethra on the configured interval for authorized and in-flight intents
- Submit to the broker only intents bound to a trusted execution scope
  (authorize alone is never a submission instruction)
- Report submissions, fills, rejections and cancellations back to Alethra

---

## Step 6: Test with a Paper Trade

> Not executable in this build: while the containment policy is active no order
> reaches a gateway. The steps below describe the intended end state.

1. **Create a paper portfolio in Alethra**
   - Log in to Alethra platform
   - Navigate to Paper Trading → Create Portfolio
   - Fund with $100,000 (virtual)

2. **Submit an order intent**
   - Use the Alethra API or UI to create an order intent
   - Example: Buy 10 shares of AAPL

3. **Monitor the agent logs**
   ```
   2026-10-15 10:05:00 [INFO] Processing intent abc123: BUY 10 US:AAPL
   2026-10-15 10:05:01 [INFO] Order submitted for intent abc123, IBKR order ID: 12345
   2026-10-15 10:05:02 [INFO] Order 12345 filled
   ```

4. **Verify in IB Gateway**
   - Open IB Gateway
   - Go to **Trade → Order Status & History**
   - You should see the order

5. **Verify in Alethra**
   - Check your paper portfolio
   - The position and cash should be updated

---

## Troubleshooting

### Agent exits at startup with "IBKR broker execution is disabled by the ... containment policy"

**Problem**: the agent refuses to start, or exits immediately.

**This is the intended fail-closed behaviour of the contained build, not a
gateway or credential problem.**

**Solutions**:
1. `ALETHRA_BROKER_TRANSPORT` is unset → set it to `fake` for offline simulation
2. `ALETHRA_BROKER_TRANSPORT=ibkr` → real broker execution is not approved for
   this build; there is no environment variable that enables it
3. Intents stay `authorized` and the log says "no trusted execution scope" →
   expected: the platform does not bind an execution scope yet, so the agent
   never submits. Use the platform's offline simulation endpoint instead.
4. If you need live broker execution, request it as a separate approval; it
   requires a code change plus a server-side account/scope binding.

### "Connection refused" to IB Gateway

**Problem**: Agent cannot connect to IB Gateway.

**Solutions**:
1. Make sure IB Gateway is running and logged in
2. Check the port number (IB Gateway: `4002` paper, `4001` live; TWS defaults to `7497`/`7496`)
3. Verify API access is enabled: **Configure → Settings → API**
4. Check firewall settings (allow localhost connections)

### "Authentication failed" to Alethra

**Problem**: Agent cannot authenticate to Alethra platform.

**Solutions**:
1. Verify your API key is correct
2. Check API key has "Execution Agent" scope
3. Ensure API key is not expired

### "Order rejected" by IBKR

**Problem**: IBKR rejects the order.

**Common reasons**:
- Insufficient buying power
- Invalid symbol or contract
- Market closed
- Position limit exceeded

**Solutions**:
- Check IB Gateway logs for rejection reason
- Verify you have sufficient funds/margin
- Check symbol is valid for your account type

### Agent disconnects frequently

**Problem**: Agent loses connection to IB Gateway.

**Solutions**:
- Check network stability
- Increase `reconnect_interval_seconds` in config
- Check IB Gateway logs for connection issues
- Consider using TWS instead of IB Gateway (more stable)

---

## Security Best Practices

1. **Never share your API key**
   - Treat it like a password
   - Don't commit it to git
   - Use environment variables or a secrets manager

2. **Use paper trading first**
   - Test thoroughly with paper trading before switching to live
   - Verify all order types work as expected

3. **Restrict API access**
   - In IB Gateway: **"Allow connections from localhost only"**
   - In Alethra: Use IP whitelisting if available

4. **Monitor the agent**
   - Check logs regularly
   - Set up alerts for connection failures
   - Review fills in both IB Gateway and Alethra

5. **Keep software updated**
   - Update IB Gateway regularly
   - Update `ib_insync` library: `pip install --upgrade ib_insync`
   - Update Alethra agent code

---

## Running as a Background Service

> **Containment note:** the units below set no `ALETHRA_BROKER_TRANSPORT`, so on
> this build the agent exits non-zero at startup — that is the intended
> fail-closed behaviour, not a misconfigured gateway. Because of that, do **not**
> install them with `KeepAlive` (launchd) or `Restart=always` (systemd) yet: a
> supervisor would relaunch the refused process in a tight crash loop. Until
> broker execution is approved separately, either keep the service disabled or
> set `ALETHRA_BROKER_TRANSPORT=fake` and use `Restart=on-failure` with a
> backoff.

### macOS (launchd)

Create `~/Library/LaunchAgents/com.alethra.execution-agent.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.alethra.execution-agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/alethra/backend/venv/bin/python</string>
        <string>-m</string>
        <string>app.execution_agent.agent</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/alethra/backend</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ALETHRA_API_KEY</key>
        <string>your-api-key-here</string>
        <key>IB_GATEWAY_PORT</key>
        <string>7497</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/alethra-agent.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/alethra-agent.err</string>
</dict>
</plist>
```

Load the service:
```bash
launchctl load ~/Library/LaunchAgents/com.alethra.execution-agent.plist
```

### Linux (systemd)

Create `/etc/systemd/system/alethra-agent.service`:

```ini
[Unit]
Description=Alethra Execution Agent
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/path/to/alethra/backend
Environment="ALETHRA_API_KEY=your-api-key-here"
Environment="IB_GATEWAY_PORT=7497"
ExecStart=/path/to/alethra/backend/venv/bin/python -m app.execution_agent.agent
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable alethra-agent
sudo systemctl start alethra-agent
```

### Windows (Task Scheduler)

1. Open Task Scheduler
2. Create a new task
3. Set trigger: "At log on"
4. Set action: "Start a program"
   - Program: `C:\path\to\alethra\backend\venv\Scripts\python.exe`
   - Arguments: `-m app.execution_agent.agent`
   - Start in: `C:\path\to\alethra\backend`
5. Set environment variables in "Conditions" tab

---

## Support

- **IBKR API Documentation**: https://www.interactivebrokers.com/en/software/api/api.htm
- **ib_insync Documentation**: https://ib-insync.readthedocs.io/
- **Alethra GitHub**: https://github.com/TommyLam0915-LCW/alethra
- **Alethra Support**: support@alethra.com

---

## Status

**Implemented, offline-validated, broker execution contained.** The agent's
control loop (poll → submit → report fills/rejections/cancellations → restart
reconciliation → kill switch) is implemented and validated offline against an
in-memory fake transport. Real IBKR execution is **disabled** by the
`OFFLINE-ONLY-2026-10-27` containment policy and has **never** been verified
against a live or paper IB Gateway; nothing in this guide should be read as
gateway-verified.

See `docs/external-prerequisites-checklist.md` for the full list of blocked items and next steps.
