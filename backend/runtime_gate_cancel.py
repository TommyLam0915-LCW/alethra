#!/usr/bin/env python3
"""Runtime gate: cancellation-report recovery across restart.

Scenario:
- Agent 1: submit order → owner requests cancel → broker cancels → platform report fails → crash
- Agent 2: starts → polls intents → sees cancel_requested=True → re-adopts broker_ref → broker shows CANCELLED → reports to platform → success

This verifies that cancellation recovery works across agent restarts without requiring a durable cancellation journal.
"""
import os
import sys
import time
import subprocess
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Configuration
GATE_PORT = 18767
GATE_DB = "sqlite:///./runtime-gate-cancel.db"
GATE_JOURNAL = "/tmp/alethra-fill-journal-cancel.json"
GATE_SUBMISSION_LOG = "/tmp/alethra-broker-submissions-cancel.log"
GATE_BROKER_STATE = "/tmp/alethra-broker-state-cancel.json"
AGENT_SCRIPT = Path(__file__).parent / "runtime_gate_agent_cancel.py"

def cleanup():
    """Remove isolated database, journal, submission log, and broker state."""
    for f in ["runtime-gate-cancel.db", GATE_JOURNAL, GATE_SUBMISSION_LOG, GATE_BROKER_STATE]:
        if os.path.exists(f):
            os.remove(f)

def start_platform_server():
    """Start FastAPI server on isolated port."""
    env = os.environ.copy()
    env["DATABASE_URL"] = GATE_DB
    env["PORT"] = str(GATE_PORT)

    cmd = [
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--host", "127.0.0.1",
        "--port", str(GATE_PORT),
        "--log-level", "error"
    ]

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    import requests
    for _ in range(30):
        try:
            resp = requests.get(f"http://127.0.0.1:{GATE_PORT}/health", timeout=1)
            if resp.status_code == 200:
                return proc
        except:
            time.sleep(0.5)

    proc.terminate()
    raise RuntimeError("Platform server failed to start")

def start_agent_process(agent_id, auth_token, fail_cancel_report=False):
    """Start a separate agent process."""
    env = os.environ.copy()
    env["DATABASE_URL"] = GATE_DB
    env["ALETHRA_API_URL"] = f"http://127.0.0.1:{GATE_PORT}"
    env["ALETHRA_API_KEY"] = auth_token
    env["IB_CLIENT_ID"] = str(agent_id)
    env["FILL_DELIVERY_JOURNAL_PATH"] = GATE_JOURNAL
    env["BROKER_SUBMISSION_LOG"] = GATE_SUBMISSION_LOG
    env["BROKER_STATE_PATH"] = GATE_BROKER_STATE
    env["POLL_INTERVAL"] = "1"
    if fail_cancel_report:
        env["FAIL_CANCEL_REPORT"] = "1"

    cmd = [sys.executable, str(AGENT_SCRIPT)]

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    time.sleep(3)

    if proc.poll() is not None:
        output = proc.stdout.read()
        raise RuntimeError(f"Agent process failed to start: {output}")

    return proc

def setup_test_data():
    """Create user, portfolio, and authorized intent via HTTP."""
    import requests

    base = f"http://127.0.0.1:{GATE_PORT}"

    resp = requests.post(f"{base}/api/auth/register", json={
        "email": "cancel@test.com",
        "password": "testpass123",
        "display_name": "Cancel Test",
        "user_type": "individual"
    })
    if resp.status_code != 201:
        raise RuntimeError(f"Failed to register user: {resp.text}")

    resp = requests.post(f"{base}/api/auth/login", json={
        "email": "cancel@test.com",
        "password": "testpass123"
    })
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to login: {resp.text}")

    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = requests.post(f"{base}/api/v1/paper-portfolio", headers=headers, json={
        "initial_virtual_cash": 100000.0,
        "currency": "USD"
    })
    if resp.status_code != 201:
        raise RuntimeError(f"Failed to create portfolio: {resp.text}")

    portfolio_id = resp.json()["id"]

    resp = requests.post(f"{base}/api/v1/execution/intents", headers=headers, json={
        "portfolio_id": portfolio_id,
        "symbol": "US:AAPL",
        "side": "BUY",
        "quantity": 10,
        "order_type": "MARKET"
    })
    if resp.status_code != 201:
        raise RuntimeError(f"Failed to create intent: {resp.text}")

    intent = resp.json()
    intent_id = intent["intent_id"]

    resp = requests.post(f"{base}/api/v1/execution/intents/{intent_id}/authorize", headers=headers, json={
        "authorization_token": intent["authorization_token"]
    })
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to authorize intent: {resp.text}")

    return intent_id, headers, token

def request_cancellation(intent_id, headers):
    """Request cancellation of an intent."""
    import requests

    base = f"http://127.0.0.1:{GATE_PORT}"
    resp = requests.post(f"{base}/api/v1/execution/intents/{intent_id}/cancel", headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to request cancellation: {resp.text}")
    return resp.json()

def check_intent_status(intent_id, headers):
    """Check intent status."""
    import requests

    base = f"http://127.0.0.1:{GATE_PORT}"
    resp = requests.get(f"{base}/api/v1/execution/intents/{intent_id}", headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to get intent: {resp.text}")

    intent = resp.json()
    return {
        "status": intent.get("status"),
        "cancel_requested": intent.get("cancel_requested", False),
        "broker_ref": intent.get("broker_ref"),
    }

def check_broker_submissions():
    """Check broker submission count from durable log."""
    if not os.path.exists(GATE_SUBMISSION_LOG):
        return 0, []

    with open(GATE_SUBMISSION_LOG, "r") as f:
        lines = f.readlines()

    submissions = []
    for line in lines:
        if line.strip():
            parts = line.strip().split("|")
            if len(parts) == 5:
                submissions.append({
                    "timestamp": parts[0],
                    "order_id": parts[1],
                    "instrument_id": parts[2],
                    "side": parts[3],
                    "quantity": int(parts[4]),
                })

    return len(submissions), submissions

def main():
    print("=" * 70)
    print("RUNTIME GATE: Cancellation-Recovery Across Restart")
    print("=" * 70)
    print(f"\nStarted at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Platform port: {GATE_PORT}")

    cleanup()

    try:
        print("\n[1/7] Starting platform server")
        server_proc = start_platform_server()
        print(f"  ✓ Server started (PID {server_proc.pid})")

        print("\n[2/7] Setting up test data")
        intent_id, headers, auth_token = setup_test_data()
        print(f"  ✓ Intent created: {intent_id}")

        print("\n[3/7] Starting agent 1 (will submit order)")
        agent1_proc = start_agent_process(agent_id=1, auth_token=auth_token, fail_cancel_report=False)
        print(f"  ✓ Agent 1 started (PID {agent1_proc.pid})")

        print("  Waiting for agent to submit order...")
        time.sleep(5)

        intent_state = check_intent_status(intent_id, headers)
        print(f"  Intent status: {intent_state['status']}, broker_ref: {intent_state['broker_ref']}")

        if intent_state['status'] != 'submitted':
            print(f"  ✗ FAIL: Expected status=submitted, got {intent_state['status']}")
            return 1

        print("\n[4/7] Requesting cancellation")
        cancel_resp = request_cancellation(intent_id, headers)
        print(f"  ✓ Cancellation requested")

        intent_state = check_intent_status(intent_id, headers)
        print(f"  Intent status: {intent_state['status']}, cancel_requested: {intent_state['cancel_requested']}")

        if not intent_state['cancel_requested']:
            print(f"  ✗ FAIL: Expected cancel_requested=True")
            return 1

        print("\n[5/7] Stopping agent 1 and starting agent 2 (will fail cancel report)")
        agent1_proc.terminate()
        agent1_proc.wait()
        print(f"  ✓ Agent 1 stopped")

        agent2_proc = start_agent_process(agent_id=1, auth_token=auth_token, fail_cancel_report=True)
        print(f"  ✓ Agent 2 started (PID {agent2_proc.pid})")

        print("  Waiting for agent to process cancellation and fail report...")
        time.sleep(6)

        # Stop agent 2 to simulate crash
        agent2_proc.terminate()
        agent2_proc.wait()
        print(f"  ✓ Agent 2 stopped (simulated crash)")

        intent_state = check_intent_status(intent_id, headers)
        print(f"  Intent status after crash: {intent_state['status']}")

        if intent_state['status'] == 'cancelled':
            print(f"  ✗ FAIL: Intent should not be cancelled yet (report failed)")
            return 1

        print("\n[6/7] Starting agent 3 (will succeed cancel report)")
        agent3_proc = start_agent_process(agent_id=1, auth_token=auth_token, fail_cancel_report=False)
        print(f"  ✓ Agent 3 started (PID {agent3_proc.pid})")

        print("  Waiting for agent to re-process cancellation and succeed...")
        time.sleep(8)

        intent_state = check_intent_status(intent_id, headers)
        print(f"  Final intent status: {intent_state['status']}")

        broker_submit_count, _ = check_broker_submissions()
        print(f"  Broker submissions: {broker_submit_count}")

        print("\n[7/7] Stopping agent 3")
        agent3_proc.terminate()
        try:
            agent3_proc.communicate(timeout=5)
            print(f"  ✓ Agent 3 stopped")
        except subprocess.TimeoutExpired:
            agent3_proc.kill()
            agent3_proc.wait()
            print(f"  ✓ Agent 3 killed")

        print("\n" + "=" * 70)
        print("VERIFICATION")
        print("=" * 70)

        success = True

        if intent_state['status'] != 'cancelled':
            print(f"✗ FAIL: Expected status=cancelled, got {intent_state['status']}")
            success = False
        else:
            print(f"✓ PASS: status=cancelled")

        if broker_submit_count != 1:
            print(f"✗ FAIL: Expected broker_submit_count=1, got {broker_submit_count}")
            success = False
        else:
            print(f"✓ PASS: broker_submit_count=1 (no duplicate submission)")

        print("\n" + "=" * 70)
        if success:
            print("RUNTIME GATE: PASS")
            print("Cancellation-recovery verified across restart")
        else:
            print("RUNTIME GATE: FAIL")
            print("Cancellation-recovery failed")
        print("=" * 70)

        return 0 if success else 1

    finally:
        print("\nCleaning up...")
        cleanup()
        try:
            server_proc.terminate()
            server_proc.wait(timeout=3)
        except:
            try:
                server_proc.kill()
            except:
                pass

if __name__ == "__main__":
    sys.exit(main())
