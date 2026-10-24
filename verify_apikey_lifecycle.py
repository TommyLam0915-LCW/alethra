#!/usr/bin/env python3
"""
Section 2 (corrected): API-key lifecycle verification.
Uses correct contract: Authorization: Bearer <raw_key>, DELETE for deactivation.
"""
import requests
import uuid
import sys

BASE_URL = "http://127.0.0.1:8001/api/v1"
RUN_MARKER = f"KEYTEST-{uuid.uuid4().hex[:12]}"

print(f"=== Section 2: API-Key Lifecycle (Corrected Contract) ===")
print(f"Run marker: {RUN_MARKER}")
print()

# Step 1: Register and login
print("A. Register user and obtain JWT...")
email = f"keytest_{RUN_MARKER}@test.com"
reg = requests.post(f"http://127.0.0.1:8001/api/auth/register", json={
    "email": email,
    "password": "testpass123",
    "display_name": "Key Test User"
})
assert reg.status_code in [200, 201], f"Registration failed: {reg.status_code}"
login = requests.post(f"http://127.0.0.1:8001/api/auth/login", json={
    "email": email,
    "password": "testpass123"
})
assert login.status_code == 200, f"Login failed: {login.status_code}"
jwt_token = login.json()["access_token"]
jwt_headers = {"Authorization": f"Bearer {jwt_token}"}
print(f"✓ JWT obtained")

# Step 2: Create API key using owner JWT
print(f"\nB. Create API key using owner JWT...")
create_resp = requests.post(f"{BASE_URL}/api-keys", headers={**jwt_headers, "Content-Type": "application/json"}, json={
    "name": f"Test Key {RUN_MARKER}",
    "scopes": ["inference"]
})
print(f"  Create status: {create_resp.status_code}")
assert create_resp.status_code == 201, f"Expected 201, got {create_resp.status_code}: {create_resp.text}"
key_data = create_resp.json()
api_key_id = key_data["id"]
raw_key = key_data["raw_key"]
assert raw_key and len(raw_key) > 20, "raw_key is empty or too short"
assert api_key_id, "id is empty"
print(f"✓ Key created: id={api_key_id}")
print(f"  raw_key prefix: {raw_key[:12]}... (redacted)")

# Step 3: Use raw_key on protected read endpoint (before deletion)
print(f"\nC. Use raw_key on protected endpoint (before deletion)...")
key_auth_headers = {"Authorization": f"Bearer {raw_key}"}
list_resp = requests.get(f"{BASE_URL}/api-keys", headers=key_auth_headers)
print(f"  List keys with API key: {list_resp.status_code}")
assert list_resp.status_code == 200, f"Expected 200, got {list_resp.status_code}: {list_resp.text}"
keys_list = list_resp.json()
print(f"✓ API key authentication works before deletion ({len(keys_list)} key(s) listed)")

# Step 4: Also test on a different protected endpoint (experiments)
print(f"\nD. Use raw_key on experiments endpoint (before deletion)...")
exp_list_resp = requests.get(f"{BASE_URL}/experiments", headers=key_auth_headers)
print(f"  List experiments with API key: {exp_list_resp.status_code}")
assert exp_list_resp.status_code == 200, f"Expected 200, got {exp_list_resp.status_code}: {exp_list_resp.text}"
print(f"✓ API key works on experiments endpoint")

# Step 5: DELETE the key using owner JWT
print(f"\nE. DELETE the key using owner JWT...")
delete_resp = requests.delete(f"{BASE_URL}/api-keys/{api_key_id}", headers=jwt_headers)
print(f"  Delete status: {delete_resp.status_code}")
assert delete_resp.status_code == 204, f"Expected 204, got {delete_resp.status_code}: {delete_resp.text}"
# 204 has no body — do NOT parse JSON
print(f"✓ Key deleted (204 No Content)")

# Step 6: Reuse the same raw_key — should receive authentication rejection
print(f"\nG. Reuse same raw_key after deletion...")
revoked_resp = requests.get(f"{BASE_URL}/api-keys", headers=key_auth_headers)
print(f"  Access with deleted key: {revoked_resp.status_code}")
assert revoked_resp.status_code == 401, f"Expected 401, got {revoked_resp.status_code}: {revoked_resp.text}"
print(f"✓ Deleted key correctly rejected with 401")

# Step 7: Verify this is authentication rejection, not missing-resource
print(f"\nH. Confirm rejection is authentication, not missing-resource...")
# Use the deleted key on experiments endpoint — should also get 401, not 404
revoked_exp_resp = requests.get(f"{BASE_URL}/experiments", headers=key_auth_headers)
print(f"  Experiments with deleted key: {revoked_exp_resp.status_code}")
assert revoked_exp_resp.status_code == 401, f"Expected 401, got {revoked_exp_resp.status_code}"
print(f"✓ 401 on experiments endpoint confirms authentication rejection (not 404)")

# Step 8: Owner JWT still accesses the resource
print(f"\nI. Owner JWT still accesses the resource...")
owner_resp = requests.get(f"{BASE_URL}/api-keys", headers=jwt_headers)
print(f"  Owner list keys: {owner_resp.status_code}")
assert owner_resp.status_code == 200, f"Expected 200, got {owner_resp.status_code}"
owner_keys = owner_resp.json()
print(f"✓ Owner JWT still works ({len(owner_keys)} key(s))")
# Verify the deleted key shows as inactive
deleted_key_info = [k for k in owner_keys if k["id"] == api_key_id]
if deleted_key_info:
    print(f"  Deleted key is_active: {deleted_key_info[0].get('is_active', 'N/A')}")
    assert deleted_key_info[0].get('is_active') == False, "Key should be inactive"
    print(f"✓ Deleted key correctly shows is_active=False")

print("\n" + "="*60)
print("✓ SECTION 2 VERIFICATION COMPLETE")
print("="*60)
print(f"Run marker: {RUN_MARKER}")
print(f"B. Create key: 201 ✓")
print(f"C. API key auth before deletion: 200 ✓")
print(f"D. API key auth on experiments: 200 ✓")
print(f"E. DELETE key (owner JWT): 204 ✓")
print(f"G. Deleted key reuse: 401 ✓")
print(f"H. Authentication rejection confirmed: 401 (not 404) ✓")
print(f"I. Owner JWT preserved: 200 ✓")
print()
print("Correct contract: Authorization: Bearer <raw_key>")
print("Deactivation: DELETE /api-keys/{key_id} -> 204")
print("Post-deletion: 401 authentication rejection")
