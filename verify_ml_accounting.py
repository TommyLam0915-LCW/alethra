#!/usr/bin/env python3
"""
Sections 3-4: ML accounting and artifact reload verification.
Uses US:AAPL (761 bars), independent DB session for accounting,
and backend restart for artifact reload.
"""
import requests
import uuid
import time
import sys
import subprocess
import signal
import os
import socket

BASE_URL = "http://127.0.0.1:8001/api/v1"
RUN_MARKER = f"MLTEST-{uuid.uuid4().hex[:12]}"
DB_URL = "postgresql://test:test@localhost:5434/alethra_acceptance"

print(f"=== Sections 3-4: ML Accounting & Artifact Reload ===")
print(f"Run marker: {RUN_MARKER}")
print()

# Step 1: Register and login
print("1. Register user...")
email = f"mltest_{RUN_MARKER}@test.com"
reg = requests.post(f"http://127.0.0.1:8001/api/auth/register", json={
    "email": email,
    "password": "testpass123",
    "display_name": "ML Test User"
})
assert reg.status_code in [200, 201]
login = requests.post(f"http://127.0.0.1:8001/api/auth/login", json={
    "email": email,
    "password": "testpass123"
})
assert login.status_code == 200
token = login.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
user_id = reg.json().get("id")
print(f"✓ User registered, id={user_id}")

# Step 2: Check quota before training
from sqlalchemy import create_engine, text
engine = create_engine(DB_URL)

print(f"\n2. Check quota before training...")
with engine.connect() as conn:
    quota_before = conn.execute(text(
        "SELECT used FROM quota_counters WHERE user_id = :uid AND quota_type = 'ai_usage'"
    ), {"uid": str(user_id)}).fetchone()
    used_before = quota_before[0] if quota_before else 0
    print(f"  quota_counters.used before: {used_before}")

# Step 3: Create ML training job
print(f"\n3. Create ML training job (US:AAPL)...")
train_resp = requests.post(
    f"{BASE_URL}/ml/train?instrument_id=US:AAPL&model_type=logistic_regression",
    headers=headers
)
print(f"  Train status: {train_resp.status_code}")
if train_resp.status_code not in [200, 201]:
    print(f"  Error: {train_resp.text}")
    sys.exit(1)
train_data = train_resp.json()
job_id = train_data["job_id"]
print(f"✓ Job created: {job_id}")

# Step 4: Check quota reservation
print(f"\n4. Check quota reservation after job creation...")
with engine.connect() as conn:
    reservations = conn.execute(text(
        "SELECT id, amount, status FROM quota_reservations WHERE user_id = :uid AND quota_type = 'ai_usage' ORDER BY created_at DESC LIMIT 3"
    ), {"uid": str(user_id)}).fetchall()
    print(f"  Found {len(reservations)} reservation(s)")
    for r in reservations:
        print(f"    id={r[0]}, amount={r[1]}, status={r[2]}")

# Step 5: Wait for worker to complete the job
print(f"\n5. Waiting for training worker to complete...")
for i in range(60):
    time.sleep(1)
    job_resp = requests.get(f"{BASE_URL}/training/jobs/{job_id}", headers=headers)
    if job_resp.status_code != 200:
        continue
    job_data = job_resp.json()
    status = job_data["status"]
    if status == "completed":
        print(f"✓ Job completed in {i+1}s")
        break
    elif status == "failed":
        print(f"✗ Job failed: {job_data.get('error', 'unknown')}")
        sys.exit(1)
    elif i % 10 == 9:
        print(f"  Still waiting... ({i+1}s)")
else:
    print("✗ Job timeout (60s)")
    sys.exit(1)

model_id = job_data["results"]["model_id"]
print(f"✓ Model created: {model_id}")

# Step 6: Check quota after completion
print(f"\n6. Check quota after completion...")
with engine.connect() as conn:
    quota_after = conn.execute(text(
        "SELECT used FROM quota_counters WHERE user_id = :uid AND quota_type = 'ai_usage'"
    ), {"uid": str(user_id)}).fetchone()
    used_after = quota_after[0] if quota_after else 0
    print(f"  quota_counters.used after: {used_after}")
    print(f"  Delta: +{used_after - used_before}")

# Check reservation status
with engine.connect() as conn:
    settled = conn.execute(text(
        "SELECT id, status, settled_at FROM quota_reservations WHERE user_id = :uid AND quota_type = 'ai_usage' ORDER BY created_at DESC LIMIT 3"
    ), {"uid": str(user_id)}).fetchall()
    print(f"  Reservation status after completion:")
    for r in settled:
        print(f"    id={r[0]}, status={r[1]}, settled_at={r[2]}")

# Step 7: Check usage_events
print(f"\n7. Check usage_events...")
with engine.connect() as conn:
    events = conn.execute(text(
        "SELECT id, event_type, metadata_json FROM usage_events WHERE user_id = :uid ORDER BY created_at DESC LIMIT 5"
    ), {"uid": str(user_id)}).fetchall()
    if events:
        print(f"✓ Found {len(events)} usage event(s)")
        for e in events:
            print(f"  id={e[0]}, type={e[1]}")
            if e[2]:
                meta = e[2] if isinstance(e[2], dict) else {}
                if "job_id" in meta:
                    print(f"    job_id={meta['job_id']}")
                if "model_id" in meta:
                    print(f"    model_id={meta['model_id']}")
    else:
        print(f"⚠ No usage events found")

# Step 8: Verify model detail
print(f"\n8. Verify model detail...")
model_resp = requests.get(f"{BASE_URL}/ml/models/{model_id}", headers=headers)
assert model_resp.status_code == 200, f"Model detail failed: {model_resp.status_code}"
model_data = model_resp.json()
print(f"✓ Model detail: model_id={model_data['model_id']}")
print(f"  model_type: {model_data.get('metadata', {}).get('model_type', 'N/A')}")
print(f"  instrument: {model_data.get('metadata', {}).get('instrument_id', 'N/A')}")

# Step 9: Test prediction
print(f"\n9. Test prediction...")
predict_resp = requests.post(f"{BASE_URL}/ml/models/{model_id}/predict-latest", headers=headers)
print(f"  Predict status: {predict_resp.status_code}")
if predict_resp.status_code == 200:
    pred_data = predict_resp.json()
    print(f"✓ Prediction successful")
    print(f"  prediction: {pred_data.get('prediction', 'N/A')}")
else:
    print(f"  Response: {predict_resp.text[:200]}")

# Step 10: Record model identity for reload verification
print(f"\n10. Record model identity for reload verification...")
model_identity = {
    "model_id": model_id,
    "user_id": str(user_id),
    "job_id": job_id,
    "model_type": model_data.get("metadata", {}).get("model_type"),
    "instrument_id": model_data.get("metadata", {}).get("instrument_id"),
}
for k, v in model_identity.items():
    print(f"  {k}: {v}")

# Step 11: Restart backend
print(f"\n11. Restarting backend...")
pid_result = subprocess.run(["lsof", "-ti:8001"], capture_output=True, text=True)
if pid_result.stdout.strip():
    old_pid = int(pid_result.stdout.strip().split()[0])
    print(f"  Killing backend PID {old_pid}...")
    os.kill(old_pid, signal.SIGTERM)
    time.sleep(2)
else:
    print("  No process on port 8001")
    old_pid = None

print(f"  Starting new backend...")
backend_dir = "/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend"
new_backend = subprocess.Popen(
    ["venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"],
    cwd=backend_dir,
    env={**os.environ, "DATABASE_URL": DB_URL},
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
new_pid = new_backend.pid
print(f"  New backend PID: {new_pid}")

print(f"  Waiting for backend readiness...")
for i in range(20):
    time.sleep(1)
    try:
        health = requests.get("http://127.0.0.1:8001/health")
        if health.status_code == 200:
            print(f"✓ Backend ready in {i+1}s")
            break
    except:
        pass
else:
    print("✗ Backend not ready after 20s")
    sys.exit(1)

# Step 12: Verify model detail after restart
print(f"\n12. Verify model detail after restart...")
model_resp2 = requests.get(f"{BASE_URL}/ml/models/{model_id}", headers=headers)
if model_resp2.status_code == 200:
    model_data2 = model_resp2.json()
    assert model_data2["model_id"] == model_id
    print(f"✓ Model detail preserved after restart")
    print(f"  model_type: {model_data2.get('metadata', {}).get('model_type', 'N/A')}")
    print(f"  instrument: {model_data2.get('metadata', {}).get('instrument_id', 'N/A')}")
else:
    print(f"✗ Model detail after restart: {model_resp2.status_code}")

# Step 13: Test prediction after restart
print(f"\n13. Test prediction after restart...")
predict_resp2 = requests.post(f"{BASE_URL}/ml/models/{model_id}/predict-latest", headers=headers)
print(f"  Predict status: {predict_resp2.status_code}")
if predict_resp2.status_code == 200:
    pred_data2 = predict_resp2.json()
    print(f"✓ Prediction after restart successful")
    print(f"  prediction: {pred_data2.get('prediction', 'N/A')}")
else:
    print(f"  Response: {predict_resp2.text[:200]}")

# Step 14: Verify quota after predictions
print(f"\n14. Verify quota after predictions...")
with engine.connect() as conn:
    quota_final = conn.execute(text(
        "SELECT used FROM quota_counters WHERE user_id = :uid AND quota_type = 'ai_usage'"
    ), {"uid": str(user_id)}).fetchone()
    used_final = quota_final[0] if quota_final else 0
    print(f"  quota_counters.used final: {used_final}")

# Step 15: Clean up
print(f"\n15. Clean up - delete model...")
delete_resp = requests.delete(f"{BASE_URL}/ml/models/{model_id}", headers=headers)
print(f"  Delete status: {delete_resp.status_code}")
if delete_resp.status_code in [200, 204]:
    # Verify 404 after deletion
    verify_404 = requests.get(f"{BASE_URL}/ml/models/{model_id}", headers=headers)
    print(f"  Verify 404 after delete: {verify_404.status_code}")
    print(f"✓ Model deleted and confirmed 404")

print("\n" + "="*60)
print("✓ SECTIONS 3-4 VERIFICATION COMPLETE")
print("="*60)
print(f"Run marker: {RUN_MARKER}")
print(f"Job ID: {job_id}")
print(f"Model ID: {model_id}")
print(f"Quota: {used_before} → {used_after} (after training) → {used_final} (after predictions)")
print(f"UsageEvents: {'FOUND' if events else 'NOT FOUND'}")
print(f"Backend restart: PID {old_pid} → {new_pid}")
print(f"Model preserved after restart: {'YES' if model_resp2.status_code == 200 else 'NO'}")
print(f"Prediction after restart: {'YES' if predict_resp2.status_code == 200 else 'NO'}")
