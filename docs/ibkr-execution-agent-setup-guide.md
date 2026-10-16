# IBKR Execution Agent — User Setup Guide

This guide walks you through setting up the Alethra Execution Agent on your local machine to connect your Interactive Brokers (IBKR) account to the Alethra platform.

## Architecture Overview

```
Your Machine
┌─────────────────────────────────────────┐
│  IB Gateway / TWS                       │
│  (port 7496 live, 7497 paper)           │
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
   - Set **Socket port**: `7497` (paper) or `7496` (live)
   - Check: **"Allow connections from localhost only"** (security)
   - Click **OK**

4. **Note the port number**
   - Paper trading: `7497`
   - Live trading: `7496`
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
export IB_GATEWAY_HOST="localhost"
export IB_GATEWAY_PORT="7497"  # Paper trading
export IB_CLIENT_ID="1"
export POLL_INTERVAL="5"
export LOG_LEVEL="INFO"
```

### Option B: Configuration File

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
# Make sure IB Gateway is running and logged in
# Make sure your virtual environment is activated

python -m app.execution_agent.agent
```

You should see:
```
2026-10-15 10:00:00 [INFO] app.execution_agent.agent: Starting execution agent
2026-10-15 10:00:01 [INFO] app.execution_agent.agent: Connecting to IB Gateway at localhost:7497
2026-10-15 10:00:01 [INFO] app.execution_agent.agent: Connected to IB Gateway
2026-10-15 10:00:01 [INFO] app.execution_agent.agent: Connecting to Alethra platform at https://api.alethra.com
2026-10-15 10:00:02 [INFO] app.execution_agent.agent: Connected to Alethra platform
```

The agent will now:
- Poll Alethra every 5 seconds for new order intents
- Submit orders to IBKR when authorized
- Report fills back to Alethra

---

## Step 6: Test with a Paper Trade

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

### "Connection refused" to IB Gateway

**Problem**: Agent cannot connect to IB Gateway.

**Solutions**:
1. Make sure IB Gateway is running and logged in
2. Check the port number (7497 for paper, 7496 for live)
3. Verify API access is enabled: **File → Global Configuration → API → Settings**
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

**NOT IMPLEMENTED** — This guide documents the intended architecture and setup process. The execution agent code is a skeleton and requires implementation.

See `docs/external-prerequisites-checklist.md` for the full list of blocked items and next steps.
