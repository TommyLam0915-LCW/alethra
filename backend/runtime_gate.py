#!/usr/bin/env python3
"""Runtime gate: genuine separate agent processes with real HTTP.

This script executes the recovery scenario using:
- Isolated SQLite database (runtime-gate.db)
- Real FastAPI HTTP server on port 18765
- Two separate agent processes (PID-scoped)
- FakeBrokerTransport for offline broker simulation

Scenario: fill reported → ack lost → journal retained → old process stopped
→ new process starts → replay succeeds → exactly one fill → broker submit count = 1
"""
import os
import sys
import time
import signal
import subprocess
import json
from pathlib import Path

# Configuration
GATE_PORT = 18765
GATE_DB = "sqlite:///./runtime-gate.db"
GATE_JOURNAL = "/tmp/alethra-fill-journal-runtime-gate.json"
GATE_SUBMISSION_LOG = "/tmp/alethra-broker-submissions-runtime-gate.log"
AGENT_SCRIPT = Path(__file__).parent / "runtime_gate_agent.py"

def cleanup():
    """Remove isolated database, journal, and submission log."""
    for f in ["runtime-gate.db", GATE_JOURNAL, GATE_SUBMISSION_LOG]:
        if os.path.exists(f):
            os.remove(f)

def start_platform_server():
    """Start FastAPI server on isolated port."""
    env = os.environ.copy()
    env["DATABASE_URL"] = GATE_DB
    env["PORT"] = str(GATE_PORT)

    # Start uvicorn
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

    # Wait for server to be ready
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

def start_agent_process(agent_id, auth_token, should_fail_upload=False):
    """Start a separate agent process."""
    env = os.environ.copy()
    env["DATABASE_URL"] = GATE_DB
    env["ALETHRA_API_URL"] = f"http://127.0.0.1:{GATE_PORT}"
    env["ALETHRA_API_KEY"] = auth_token  # Use JWT token for authentication
    env["IB_CLIENT_ID"] = str(agent_id)
    env["FILL_DELIVERY_JOURNAL_PATH"] = GATE_JOURNAL
    env["BROKER_SUBMISSION_LOG"] = GATE_SUBMISSION_LOG
    env["POLL_INTERVAL"] = "1"
    if should_fail_upload:
        env["SIMULATE_UPLOAD_FAILURE"] = "1"

    cmd = [sys.executable, str(AGENT_SCRIPT)]

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # Merge stderr into stdout
        text=True,
        bufsize=1
    )

    # Give agent time to initialize
    time.sleep(3)

    # Check if process is still running
    if proc.poll() is not None:
        # Process exited early, read output
        output = proc.stdout.read()
        logger.error(f"Agent process exited early with code {proc.returncode}")
        logger.error(f"Agent output:\n{output}")
        raise RuntimeError(f"Agent process failed to start: {output}")

    return proc

def setup_test_data():
    """Create user, portfolio, and authorized intent via HTTP."""
    import requests

    base = f"http://127.0.0.1:{GATE_PORT}"

    # Register user
    resp = requests.post(f"{base}/api/auth/register", json={
        "email": "runtime-gate@test.com",
        "password": "testpass123",
        "display_name": "Runtime Gate",
        "user_type": "individual"
    })
    if resp.status_code != 201:
        raise RuntimeError(f"Failed to register user: {resp.text}")

    # Login
    resp = requests.post(f"{base}/api/auth/login", json={
        "email": "runtime-gate@test.com",
        "password": "testpass123"
    })
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to login: {resp.text}")

    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create portfolio
    resp = requests.post(f"{base}/api/v1/paper-portfolio", headers=headers, json={
        "initial_virtual_cash": 100000.0,
        "currency": "USD"
    })
    if resp.status_code != 201:
        raise RuntimeError(f"Failed to create portfolio: {resp.text}")

    portfolio_id = resp.json()["id"]

    # Create intent
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

    # Authorize intent
    resp = requests.post(f"{base}/api/v1/execution/intents/{intent_id}/authorize", headers=headers, json={
        "authorization_token": intent["authorization_token"]
    })
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to authorize intent: {resp.text}")

    return intent_id, headers, token

def check_server_fills(intent_id, headers):
    """Check how many fills the server has recorded and return detailed info."""
    import requests

    base = f"http://127.0.0.1:{GATE_PORT}"
    resp = requests.get(f"{base}/api/v1/execution/intents/{intent_id}", headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to get intent: {resp.text}")

    intent = resp.json()
    status = intent.get("status")
    broker_ref = intent.get("broker_ref")

    # Get fill records and sum quantities
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
    print("RUNTIME GATE: Genuine Separate Agent Processes")
    print("=" * 70)
    print(f"\nRuntime gate started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Platform port: {GATE_PORT}")
    print(f"Journal path: {GATE_JOURNAL}")
    print(f"Database: {GATE_DB}")

    # Cleanup
    cleanup()

    try:
        # Step 1: Start platform server
        print("\n[1/7] Starting platform server on port", GATE_PORT)
        server_proc = start_platform_server()
        print(f"  ✓ Server started (PID {server_proc.pid})")

        # Step 2: Setup test data
        print("\n[2/7] Setting up test data (user, portfolio, intent)")
        intent_id, headers, auth_token = setup_test_data()
        print(f"  ✓ Intent created: {intent_id}")

        # Step 3: Start agent process 1 (will fail upload)
        print("\n[3/7] Starting agent process 1 (PID-scoped, will fail upload)")
        agent1_proc = start_agent_process(agent_id=1, auth_token=auth_token, should_fail_upload=True)
        print(f"  ✓ Agent 1 started (PID {agent1_proc.pid})")

        # Wait for agent to submit order and attempt fill delivery
        print("  Waiting for agent to submit order and attempt fill delivery...")
        time.sleep(5)

        # Check journal state
        journal_before = check_journal()
        print(f"  Journal state: {len(journal_before)} pending fill(s)")

        # Check server state
        server_state = check_server_fills(intent_id, headers)
        print(f"  Server state: filled_quantity={server_state['filled_quantity']}, status={server_state['status']}")

        # Step 4: Stop agent process 1
        print("\n[4/7] Stopping agent process 1")
        agent1_proc.terminate()
        try:
            stdout1, _ = agent1_proc.communicate(timeout=5)
            print(f"  ✓ Agent 1 stopped")
            if stdout1:
                print(f"  Agent 1 output (last 20 lines):")
                for line in stdout1.split('\n')[-20:]:
                    if line.strip():
                        print(f"    {line}")
        except subprocess.TimeoutExpired:
            agent1_proc.kill()
            agent1_proc.wait()
            print(f"  ✓ Agent 1 killed (timeout)")

        # Verify journal persists
        journal_after_stop = check_journal()
        print(f"  Journal after stop: {len(journal_after_stop)} pending fill(s)")

        if len(journal_after_stop) == 0:
            print("  ✗ FAIL: Journal lost pending fills after process stop")
            return 1

        # Step 5: Start agent process 2 (same journal, will succeed upload)
        print("\n[5/7] Starting agent process 2 (same journal, will succeed)")
        agent2_proc = start_agent_process(agent_id=1, auth_token=auth_token, should_fail_upload=False)
        print(f"  ✓ Agent 2 started (PID {agent2_proc.pid})")

        # Wait for replay and successful delivery
        print("  Waiting for replay and successful delivery...")
        time.sleep(8)

        # Capture agent 2 output before stopping
        if agent2_proc.poll() is None:
            # Agent is still running, try to read output without blocking
            import select
            if select.select([agent2_proc.stdout], [], [], 0.1)[0]:
                try:
                    line = agent2_proc.stdout.readline()
                    if line:
                        print(f"  Agent 2 latest: {line.strip()}")
                except:
                    pass

        # Step 6: Verify exactly one fill on server
        print("\n[6/7] Verifying server state")
        final_state = check_server_fills(intent_id, headers)
        print(f"  Final server state:")
        print(f"    status={final_state['status']}")
        print(f"    broker_ref={final_state['broker_ref']}")
        print(f"    fill_count={final_state['fill_count']}")
        print(f"    filled_quantity={final_state['filled_quantity']}")

        journal_final = check_journal()
        print(f"  Final journal state: {len(journal_final)} pending fill(s)")

        broker_submit_count, broker_submissions = check_broker_submissions()
        print(f"  Broker submission log: {broker_submit_count} submission(s)")
        if broker_submissions:
            for sub in broker_submissions:
                print(f"    {sub['timestamp']} | {sub['order_id']} | {sub['instrument_id']} | {sub['side']} | {sub['quantity']}")

        # Step 7: Stop agent process 2
        print("\n[7/7] Stopping agent process 2")
        agent2_proc.terminate()
        try:
            stdout2, _ = agent2_proc.communicate(timeout=5)
            print(f"  ✓ Agent 2 stopped")
            if stdout2:
                print(f"  Agent 2 output (last 20 lines):")
                for line in stdout2.split('\n')[-20:]:
                    if line.strip():
                        print(f"    {line}")
        except subprocess.TimeoutExpired:
            agent2_proc.kill()
            agent2_proc.wait()
            print(f"  ✓ Agent 2 killed (timeout)")

        # Verification
        print("\n" + "=" * 70)
        print("VERIFICATION")
        print("=" * 70)

        success = True

        # Check fill count == 1
        if final_state['fill_count'] != 1:
            print(f"✗ FAIL: Expected fill_count=1, got {final_state['fill_count']}")
            success = False
        else:
            print(f"✓ PASS: fill_count=1 (exactly one fill)")

        # Check filled_quantity == 10
        if final_state['filled_quantity'] != 10:
            print(f"✗ FAIL: Expected filled_quantity=10, got {final_state['filled_quantity']}")
            success = False
        else:
            print(f"✓ PASS: filled_quantity=10")

        # Check fill identity (broker_ref matches)
        if final_state['broker_ref'] is None:
            print(f"✗ FAIL: broker_ref is None")
            success = False
        elif not final_state['broker_ref'].startswith("FAKE-"):
            print(f"✗ FAIL: broker_ref={final_state['broker_ref']} doesn't match expected pattern")
            success = False
        else:
            print(f"✓ PASS: broker_ref={final_state['broker_ref']}")

        # Check individual fill record
        if len(final_state['fills']) == 1:
            fill = final_state['fills'][0]
            if fill.get('quantity') != 10:
                print(f"✗ FAIL: fill quantity={fill.get('quantity')}, expected 10")
                success = False
            elif fill.get('broker_ref') != final_state['broker_ref']:
                print(f"✗ FAIL: fill broker_ref mismatch")
                success = False
            else:
                print(f"✓ PASS: fill record identity verified")
        else:
            print(f"✗ FAIL: Expected 1 fill record, got {len(final_state['fills'])}")
            success = False

        # Check intent status == filled
        if final_state['status'] != "filled":
            print(f"✗ FAIL: Expected status=filled, got {final_state['status']}")
            success = False
        else:
            print(f"✓ PASS: status=filled")

        # Check journal pending count == 0
        if len(journal_final) != 0:
            print(f"✗ FAIL: Expected journal empty, got {len(journal_final)} pending")
            success = False
        else:
            print(f"✓ PASS: journal empty (all fills delivered)")

        # Check broker submission count == 1 (directly observed)
        if broker_submit_count != 1:
            print(f"✗ FAIL: Expected broker_submit_count=1, got {broker_submit_count}")
            success = False
        else:
            print(f"✓ PASS: broker_submit_count=1 (directly observed)")

        print("\n" + "=" * 70)
        if success:
            print("RUNTIME GATE: PASS")
            print("Recovery scenario verified with genuine separate processes")
        else:
            print("RUNTIME GATE: FAIL")
            print("Recovery scenario failed")
        print("=" * 70)

        return 0 if success else 1

    finally:
        # Cleanup
        print("\nCleaning up...")
        if 'server_proc' in locals():
            server_proc.terminate()
            server_proc.wait(timeout=5)
        cleanup()

if __name__ == "__main__":
    sys.exit(main())
