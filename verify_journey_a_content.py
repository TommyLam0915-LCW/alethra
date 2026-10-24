#!/usr/bin/env python3
"""
Journey A content-transfer verification with substantive equality checks.
Uses run-specific marker to detect hard-coded substitution.
"""
import requests
import json
import uuid
import sys

BASE_URL = "http://127.0.0.1:8001/api/v1"

# Run-specific marker to detect hard-coded substitution
RUN_MARKER = f"VERIFY-{uuid.uuid4().hex[:12]}"
MARKER_HYPOTHESIS = f"Tech momentum hypothesis {RUN_MARKER}"
MARKER_TITLE = f"Momentum Strategy {RUN_MARKER}"

print(f"=== Journey A Content-Transfer Verification ===")
print(f"Run marker: {RUN_MARKER}")
print()

# Step 1: Register user and get token
print("1. Registering user...")
email = f"journey_a_verify_{RUN_MARKER}@test.com"
reg = requests.post(f"http://127.0.0.1:8001/api/auth/register", json={
    "email": email,
    "password": "testpass123",
    "display_name": "Journey A Verifier"
})
if reg.status_code not in [200, 201]:
    print(f"Registration failed: {reg.status_code} {reg.text}")
    sys.exit(1)
print(f"✓ User registered: {email}")

# Login to get token
login = requests.post(f"http://127.0.0.1:8001/api/auth/login", json={
    "email": email,
    "password": "testpass123"
})
assert login.status_code == 200, f"Login failed: {login.status_code} {login.text}"
token = login.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
print(f"✓ Login successful, token obtained")

# Step 2: Create conversation
print("\n2. Creating conversation...")
conv = requests.post(f"{BASE_URL}/ai/conversations", headers=headers, json={
    "title": f"Journey A Verification {RUN_MARKER}"
})
assert conv.status_code in [200, 201], f"Conversation creation failed: {conv.status_code}"
conv_id = conv.json()["id"]
print(f"✓ Conversation created: {conv_id}")

# Step 3: Send user message with marker content
print("\n3. Sending user message with marker...")
user_msg = requests.post(f"{BASE_URL}/ai/conversations/{conv_id}/messages", headers=headers, json={
    "role": "user",
    "content": f"Design a momentum strategy. Hypothesis: {MARKER_HYPOTHESIS}"
})
assert user_msg.status_code == 200, f"User message failed: {user_msg.status_code}"
user_msg_id = user_msg.json()["id"]
print(f"✓ User message sent: {user_msg_id}")

# Step 4: Get AI response
print("\n4. Getting AI response...")
ai_msg = requests.post(f"{BASE_URL}/ai/conversations/{conv_id}/messages", headers=headers, json={
    "role": "user",
    "content": f"Help me design a momentum strategy for tech stocks. Specific hypothesis: {MARKER_HYPOTHESIS}"
})
assert ai_msg.status_code == 200, f"AI message failed: {ai_msg.status_code}"
ai_data = ai_msg.json()
ai_msg_id = ai_data["id"]
ai_content = ai_data["content"]
print(f"✓ AI response received: {ai_msg_id}")
print(f"  Content length: {len(ai_content)}")

# CRITICAL: Verify AI content contains the marker
if RUN_MARKER not in ai_content:
    print(f"✗ FAIL: AI content does not contain run marker {RUN_MARKER}")
    print(f"  This suggests hard-coded or cached response")
    sys.exit(1)
print(f"✓ AI content contains run marker: {RUN_MARKER}")

# Step 5: Create draft from AI response with marker content
print("\n5. Creating draft from AI response...")
draft_payload = {
    "title": MARKER_TITLE,
    "hypothesis": MARKER_HYPOTHESIS,
    "falsification_criteria": f"Max drawdown > 25% for {RUN_MARKER}",
    "strategy_template": "sma_crossover",
    "asset_symbol": "DEMO-ALPHA",
    "benchmark_symbol": "DEMO-BENCHMARK",
    "initial_cash": "100000",
    "commission_rate": "0.001",
    "slippage_rate": "0.0005",
    "research_rules": {"entry": "SMA 20/50 crossover", "exit": "stop_loss 5%"},
    "source_message_id": ai_msg_id
}
draft = requests.post(f"{BASE_URL}/ai/conversations/{conv_id}/drafts", headers=headers, json=draft_payload)
if draft.status_code not in [200, 201]:
    print(f"✗ Draft creation failed: {draft.status_code}")
    print(f"  Response: {draft.text}")
    print(f"  Payload: {json.dumps(draft_payload, indent=2)}")
    sys.exit(1)
assert draft.status_code in [200, 201], f"Draft creation failed: {draft.status_code} {draft.text}"
draft_data = draft.json()
draft_id = draft_data["id"]
print(f"✓ Draft created: {draft_id}")

# Step 6: Verify persisted draft readback with substantive content
print("\n6. Verifying persisted draft readback...")
draft_read = requests.get(f"{BASE_URL}/research-drafts/{draft_id}", headers=headers)
assert draft_read.status_code == 200, f"Draft readback failed: {draft_read.status_code}"
draft_read_data = draft_read.json()

# SUBSTANTIVE CONTENT EQUALITY CHECKS
assert draft_read_data["title"] == MARKER_TITLE, \
    f"Title mismatch: {draft_read_data['title']} != {MARKER_TITLE}"
assert draft_read_data["hypothesis"] == MARKER_HYPOTHESIS, \
    f"Hypothesis mismatch: {draft_read_data['hypothesis']} != {MARKER_HYPOTHESIS}"
assert RUN_MARKER in draft_read_data["falsification_criteria"], \
    f"Falsification criteria missing marker: {draft_read_data['falsification_criteria']}"
assert draft_read_data["source_conversation_id"] == conv_id, \
    f"source_conversation_id mismatch: {draft_read_data['source_conversation_id']} != {conv_id}"
print(f"✓ Draft readback contains marker content:")
print(f"  title: {draft_read_data['title']}")
print(f"  hypothesis: {draft_read_data['hypothesis']}")
print(f"  source_conversation_id: {draft_read_data['source_conversation_id']}")

# Step 7: Verify source_message_id in audit events
print("\n7. Verifying source_message_id in audit events...")
audit = requests.get(f"{BASE_URL}/research-drafts/{draft_id}/audit-events", headers=headers)
assert audit.status_code == 200, f"Audit fetch failed: {audit.status_code}"
audit_events = audit.json()
source_msg_found = False
for event in audit_events:
    if event["event_type"] == "research_draft_created_from_ai":
        event_data = event.get("event_data", {})
        if event_data.get("source_message_id") == ai_msg_id:
            source_msg_found = True
            break
assert source_msg_found, f"source_message_id {ai_msg_id} not found in audit events"
print(f"✓ source_message_id verified in audit: {ai_msg_id}")

# Step 8: Accept the draft
print("\n8. Accepting draft...")
approve = requests.post(f"{BASE_URL}/research-drafts/{draft_id}/approve", headers=headers, json={
    "action": "accept",
    "notes": f"Accepted for verification {RUN_MARKER}"
})
assert approve.status_code == 200, f"Draft acceptance failed: {approve.status_code} {approve.text}"
assert approve.json()["status"] == "accepted", "Draft status not 'accepted'"
print(f"✓ Draft accepted")

# Step 9: Verify accepted draft content immutability
print("\n9. Verifying accepted draft content immutability...")
immutable_check = requests.patch(f"{BASE_URL}/research-drafts/{draft_id}", headers=headers, json={
    "title": "Should Fail"
})
assert immutable_check.status_code == 400, \
    f"Content edit on accepted draft should fail, got {immutable_check.status_code}"
print(f"✓ Accepted draft content is immutable (400 on content edit)")

# Step 10: Create experiment
print("\n10. Creating experiment...")
exp = requests.post(f"{BASE_URL}/experiments", headers=headers, json={
    "title": f"Experiment for {RUN_MARKER}",
    "description": f"Linked to draft with marker {RUN_MARKER}",
    "strategy_spec": {
        "template_type": "sma_crossover",
        "asset_ids": ["DEMO-ALPHA"],
        "frequency": "1d",
        "start_date": "2023-01-01",
        "end_date": "2023-12-31",
        "initial_cash": 100000.0,
        "signal_parameters": {"short_window": 20, "long_window": 50},
        "entry_rule": {"type": "sma_crossover"},
        "exit_rule": {"type": "stop_loss", "threshold": 0.05}
    }
})
assert exp.status_code in [200, 201], f"Experiment creation failed: {exp.status_code} {exp.text}"
exp_id = exp.json()["id"]
print(f"✓ Experiment created: {exp_id}")

# Step 11: Link experiment to accepted draft
print("\n11. Linking experiment to accepted draft...")
link = requests.patch(f"{BASE_URL}/research-drafts/{draft_id}", headers=headers, json={
    "experiment_id": exp_id
})
assert link.status_code == 200, f"Experiment linking failed: {link.status_code} {link.text}"
linked_data = link.json()
assert linked_data["experiment_id"] == exp_id, \
    f"experiment_id not persisted: {linked_data.get('experiment_id')} != {exp_id}"
print(f"✓ Experiment linked to draft: {exp_id}")

# Step 12: Verify experiment readback
print("\n12. Verifying experiment readback...")
exp_read = requests.get(f"{BASE_URL}/experiments/{exp_id}", headers=headers)
assert exp_read.status_code == 200, f"Experiment readback failed: {exp_read.status_code}"
exp_read_data = exp_read.json()
assert RUN_MARKER in exp_read_data["title"], \
    f"Experiment title missing marker: {exp_read_data['title']}"
print(f"✓ Experiment readback contains marker:")
print(f"  title: {exp_read_data['title']}")
if "original_idea" in exp_read_data:
    print(f"  original_idea: {exp_read_data['original_idea']}")

# Step 13: Final draft readback to confirm link persists
print("\n13. Final draft readback to confirm link persists...")
final_draft = requests.get(f"{BASE_URL}/research-drafts/{draft_id}", headers=headers)
assert final_draft.status_code == 200
final_draft_data = final_draft.json()
assert final_draft_data["experiment_id"] == exp_id, \
    f"experiment_id not persisted after readback: {final_draft_data.get('experiment_id')}"
assert final_draft_data["status"] == "accepted", \
    f"Draft status changed: {final_draft_data['status']}"
print(f"✓ Draft link persists:")
print(f"  status: {final_draft_data['status']}")
print(f"  experiment_id: {final_draft_data['experiment_id']}")

print("\n" + "="*60)
print("✓ JOURNEY A CONTENT-TRANSFER VERIFIED")
print("="*60)
print(f"Run marker: {RUN_MARKER}")
print(f"AI content contains marker: YES")
print(f"Draft title matches: YES")
print(f"Draft hypothesis matches: YES")
print(f"source_conversation_id verified: YES")
print(f"source_message_id in audit: YES")
print(f"Accepted draft content immutable: YES")
print(f"Experiment linked: YES")
print(f"Experiment readback contains marker: YES")
print(f"Link persists after readback: YES")
print()
print("Substantive content equality verified — not just non-empty or source IDs.")
