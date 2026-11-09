#!/usr/bin/env python3
"""Runtime gate: acknowledgement-loss recovery scenario.

This scenario tests a different failure mode than runtime_gate.py:
- Agent 1: submit order → fill reported → upload SUCCEEDS → server commits fill
- Agent 1 crashes BEFORE removing fill from journal (ack lost)
- Journal still has pending fill
- Agent 2: starts → loads journal → replays pending fill
- Server should DEDUPLICATE (same broker_ref + fill_time + quantity)
- Result: still only 1 fill record, not 2

This verifies the idempotency guarantee of the fill reporting API.
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
GATE_PORT = 18766  # Different port to avoid conflict
GATE_DB = "sqlite:///./runtime-gate-ack-loss.db"
GATE_JOURNAL = "/tmp/alethra-fill-journal-ack-loss.json"
GATE_SUBMISSION_LOG = "/tmp/alethra-broker-submissions-ack-loss.log"
AGENT_SCRIPT = Path(__file__).parent / "runtime_gate_agent_ack_loss.py"

def cleanup():
    """Remove isolated database, journal, and submission log."""
    for f in ["runtime-gate-ack-loss.db", GATE_JOURNAL, GATE_SUBMISSION_LOG]:
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

def start_agent_process(agent_id, auth_token, crash_after_upload=False):
    """Start a separate agent process."""
    env = os.environ.copy()
    env["DATABASE_URL"] = GATE_DB
    env["ALETHRA_API_URL"] = f"http://127.0.0.1:{GATE_PORT}"
    env["ALETHRA_API_KEY"] = auth_token
    env["IB_CLIENT_ID"] = str(agent_id)
    env["FILL_DELIVERY_JOURNAL_PATH"] = GATE_JOURNAL
    env["BROKER_SUBMISSION_LOG"] = GATE_SUBMISSION_LOG
    env["POLL_INTERVAL"] = "1"
    if crash_after_upload:
        env["CRASH_AFTER_UPLOAD"] = "1"

    cmd = [sys.executable, str(AGENT_SCRIPT)]

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    # Wait for agent to initialize and potentially crash
    time.sleep(4)

    # Check if process exited
    exit_code = proc.poll()
    if exit_code is not None:
        # Process exited
        output = proc.stdout.read()
        if exit_code == 42:
            # Expected crash after upload
            logger.info(f"Agent crashed with exit code 42 (simulated crash after upload)")
            return proc
        elif crash_after_upload:
            # We expected a crash but got a different exit code
            logger.warning(f"Agent exited with unexpected code {exit_code}")
            return proc
        else:
            # Agent shouldn't have exited
            raise RuntimeError(f"Agent process exited early with code {exit_code}: {output}")

    return proc

def setup_test_data():
    """Create user, portfolio, and authorized intent via HTTP."""
    import requests

    base = f"http://127.0.0.1:{GATE_PORT}"

    resp = requests.post(f"{base}/api/auth/register", json={
        "email": "ack-loss@test.com",
        "password": "testpass123",
        "display_name": "Ack Loss Test",
        "user_type": "individual"
    })
    if resp.status_code != 201:
        raise RuntimeError(f"Failed to register user: {resp.text}")

    resp = requests.post(f"{base}/api/auth/login", json={
        "email": "ack-loss@test.com",
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

def check_server_fills(intent_id, headers):
    """Check how many fills the server has recorded."""
    import requests

    base = f"http://127.0.0.1:{GATE_PORT}"
    resp = requests.get(f"{base}/api/v1/execution/intents/{intent_id}", headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to get intent: {resp.text}")

    intent = resp.json()
    status = intent.get("status")
    broker_ref = intent.get("broker_ref")

    resp = requests.get(f"{base}/api/v1/execution/intents/{intent_id}/fills", headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to get fills: {resp.text}")

    fills = resp.json()
    fill_count = len(fills)
    filled_quantity = sum(fill.get("quantity", 0) for fill in fills)

    return {
        "status": status,
        "broker_ref": broker_ref,
        "fill_count": fill_count,
        "filled_quantity": filled_quantity,
        "fills": fills,
    }

def check_journal():
    """Check journal state."""
    if not os.path.exists(GATE_JOURNAL):
        return []

    with open(GATE_JOURNAL, "r") as f:
        data = json.load(f)

    return data.get("pending_fills", [])

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
    print("RUNTIME GATE: Acknowledgement-Loss Recovery")
    print("=" * 70)
    print(f"\nStarted at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Platform port: {GATE_PORT}")
    print(f"Journal path: {GATE_JOURNAL}")
    print(f"Submission log: {GATE_SUBMISSION_LOG}")

    cleanup()

    try:
        print("\n[1/7] Starting platform server")
        server_proc = start_platform_server()
        print(f"  ✓ Server started (PID {server_proc.pid})")

        print("\n[2/7] Setting up test data")
        intent_id, headers, auth_token = setup_test_data()
        print(f"  ✓ Intent created: {intent_id}")

        print("\n[3/7] Starting agent 1 (will succeed upload, then crash)")
        agent1_proc = start_agent_process(agent_id=1, auth_token=auth_token, crash_after_upload=True)
        print(f"  ✓ Agent 1 started (PID {agent1_proc.pid})")

        print("  Waiting for agent to submit, upload fill, and crash...")
        time.sleep(6)

        # Check if agent 1 crashed (expected)
        if agent1_proc.poll() is None:
            print("  Agent 1 still running, killing...")
            agent1_proc.kill()
            agent1_proc.wait()
        else:
            print(f"  ✓ Agent 1 exited with code {agent1_proc.returncode}")

        journal_after_crash = check_journal()
        print(f"  Journal after crash: {len(journal_after_crash)} pending fill(s)")

        server_state_after_crash = check_server_fills(intent_id, headers)
        print(f"  Server after crash: fill_count={server_state_after_crash['fill_count']}, filled_quantity={server_state_after_crash['filled_quantity']}")

        if server_state_after_crash['fill_count'] != 1:
            print(f"  ✗ FAIL: Expected server to have 1 fill after crash, got {server_state_after_crash['fill_count']}")
            return 1

        if len(journal_after_crash) != 1:
            print(f"  ✗ FAIL: Expected journal to retain 1 pending fill, got {len(journal_after_crash)}")
            return 1

        print("\n[4/7] Starting agent 2 (will replay pending fill)")
        agent2_proc = start_agent_process(agent_id=1, auth_token=auth_token, crash_after_upload=False)
        print(f"  ✓ Agent 2 started (PID {agent2_proc.pid})")

        print("  Waiting for replay...")
        time.sleep(8)

        print("\n[5/7] Verifying server state (should still have exactly 1 fill)")
        final_state = check_server_fills(intent_id, headers)
        print(f"  Final server state:")
        print(f"    fill_count={final_state['fill_count']}")
        print(f"    filled_quantity={final_state['filled_quantity']}")
        print(f"    status={final_state['status']}")

        journal_final = check_journal()
        print(f"  Final journal state: {len(journal_final)} pending fill(s)")

        broker_submit_count, _ = check_broker_submissions()
        print(f"  Broker submissions: {broker_submit_count}")

        print("\n[6/7] Stopping agent 2")
        agent2_proc.terminate()
        try:
            agent2_proc.communicate(timeout=5)
            print(f"  ✓ Agent 2 stopped")
        except subprocess.TimeoutExpired:
            agent2_proc.kill()
            agent2_proc.wait()
            print(f"  ✓ Agent 2 killed")

        print("\n" + "=" * 70)
        print("VERIFICATION")
        print("=" * 70)

        success = True

        # Critical: fill count must still be 1 (no duplicate)
        if final_state['fill_count'] != 1:
            print(f"✗ FAIL: Expected fill_count=1 after replay, got {final_state['fill_count']}")
            print("  This means the server did NOT deduplicate the replayed fill!")
            success = False
        else:
            print(f"✓ PASS: fill_count=1 (server deduplicated replay)")

        if final_state['filled_quantity'] != 10:
            print(f"✗ FAIL: Expected filled_quantity=10, got {final_state['filled_quantity']}")
            success = False
        else:
            print(f"✓ PASS: filled_quantity=10")

        if final_state['status'] != "filled":
            print(f"✗ FAIL: Expected status=filled, got {final_state['status']}")
            success = False
        else:
            print(f"✓ PASS: status=filled")

        if len(journal_final) != 0:
            print(f"✗ FAIL: Expected journal empty after replay, got {len(journal_final)} pending")
            success = False
        else:
            print(f"✓ PASS: journal empty (replay succeeded)")

        if broker_submit_count != 1:
            print(f"✗ FAIL: Expected broker_submit_count=1, got {broker_submit_count}")
            success = False
        else:
            print(f"✓ PASS: broker_submit_count=1 (no duplicate submission)")

        print("\n" + "=" * 70)
        if success:
            print("RUNTIME GATE: PASS")
            print("Acknowledgement-loss recovery verified")
            print("Server correctly deduplicated replayed fill")
        else:
            print("RUNTIME GATE: FAIL")
            print("Acknowledgement-loss recovery failed")
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
