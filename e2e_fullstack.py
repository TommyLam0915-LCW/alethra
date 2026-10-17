#!/usr/bin/env python3
"""Full-stack E2E for the integration branch (directive Item 7).

Runs against a live backend on a clean disposable PostgreSQL 16 database:
    register/login → research experiment → market data → backtest run →
    ML train (real training-worker process) → model reload → inference →
    usage → authorised intent → execution agent (FakeBrokerTransport) →
    fills/reconciliation → platform cancel → checkout fixture →
    signed webhook → entitlement.

Exit codes: 0 all checks PASS, 1 at least one FAIL, 2 environment blocked.
"""

import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid

import requests

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8100")
API = f"{BASE}/api/v1"
AUTH = f"{BASE}/api/auth"
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
PY = os.environ.get("E2E_PY", sys.executable)
DB_URL = os.environ.get(
    "E2E_DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5434/alethra_e2e",
)
WEBHOOK_SECRET = "test-webhook-secret"  # TestPaymentAdapter default

RUN_ID = uuid.uuid4().hex[:8]
EMAIL = f"e2e-fullstack-{RUN_ID}@example.com"
PASSWORD = "e2e-fullstack-pass-123"

results = []


def check(stage, name, ok, detail=""):
    results.append((stage, name, bool(ok), str(detail)[:300]))
    print(f"  {'PASS' if ok else 'FAIL'}  {stage} {name}  {detail}")
    return ok


def section(title):
    print(f"\n=== {title} ===")


def wait_for(predicate, timeout, interval=1.0, desc="condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return None


# ── E1: register / login / me ────────────────────────────────────
section("E1 — Authentication")
headers = None
user_id = None
try:
    r = requests.post(f"{AUTH}/register", json={
        "email": EMAIL,
        "password": PASSWORD,
        "display_name": "E2E Fullstack",
        "user_type": "individual",
    }, timeout=15)
    check("E1", "register", r.status_code == 201, f"{r.status_code} {r.text[:120]}")

    r = requests.post(f"{AUTH}/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    token = r.json().get("access_token")
    check("E1", "login", r.status_code == 200 and bool(token), f"{r.status_code}")

    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{AUTH}/me", headers=headers, timeout=15)
    user_id = r.json().get("id") if r.status_code == 200 else None
    check("E1", "me returns user", r.status_code == 200 and bool(user_id),
          f"{r.status_code} id={user_id}")
except Exception as e:
    check("E1", "auth flow", False, repr(e))
    print(json.dumps(results, indent=2))
    sys.exit(2)


# ── E2: research experiment ──────────────────────────────────────
section("E2 — Research experiment")
version_id = None
try:
    payload = {
        "title": f"E2E Fullstack — Buy and Hold {RUN_ID}",
        "original_idea": "Simple buy-and-hold on demo asset",
        "research_type": "quantitative",
        "hypothesis": "Buy-and-hold will track demo asset performance over the test period",
        "falsification_conditions": "Strategy underperforms benchmark by more than 5%",
        "benchmark": "DEMO-BENCHMARK",
        "strategy_spec": {
            "template_type": "buy_and_hold",
            "asset_ids": ["DEMO-ALPHA"],
            "start_date": "2024-01-02",
            "end_date": "2024-02-13",
            "initial_cash": 100000,
            "commission_rate": 0.001,
            "slippage_rate": 0.0005,
            "benchmark_id": "DEMO-BENCHMARK",
            "signal_parameters": {},
            "entry_rule": {"type": "buy_on_start"},
            "exit_rule": {"type": "hold_to_end"},
            "sizing_rule": {"type": "full_invest"},
            "execution_timing": {"type": "close"},
        },
    }
    r = requests.post(f"{API}/experiments/", json=payload, headers=headers, timeout=30)
    ok = r.status_code == 201
    exp = r.json() if ok else {}
    version_id = exp.get("current_version_id")
    check("E2", "create experiment", ok and bool(version_id),
          f"{r.status_code} version_id={version_id}")
    check("E2", "hypothesis + research_type",
          exp.get("hypothesis") is not None and exp.get("research_type") == "quantitative",
          f"research_type={exp.get('research_type')}")
except Exception as e:
    check("E2", "experiment flow", False, repr(e))


# ── E3: market data with provenance ──────────────────────────────
section("E3 — Market data provenance")
try:
    r = requests.get(
        f"{API}/instruments/US:AAPL/bars",
        params={"start_date": "2025-01-02", "end_date": "2025-06-30"},
        timeout=30,
    )
    data = r.json() if r.status_code == 200 else {}
    dq = data.get("data_quality") or {}
    check("E3", "bars fetched", r.status_code == 200 and data.get("bar_count", 0) > 0,
          f"{r.status_code} bar_count={data.get('bar_count')}")
    check("E3", "synthetic labelled as synthetic",
          dq.get("data_nature") == "synthetic",
          f"data_nature={dq.get('data_nature')} provider={data.get('provider')}")
    check("E3", "not-real-data disclaimer", "synthetic" in data.get("disclaimer", "").lower(),
          data.get("disclaimer", "")[:80])

    r = requests.get(
        f"{API}/instruments/US:AAPL/bars/stored",
        params={"start_date": "2025-01-02", "end_date": "2025-06-30"},
        timeout=30,
    )
    stored = r.json() if r.status_code == 200 else {}
    check("E3", "bars persisted to storage with provenance",
          r.status_code == 200 and stored.get("bar_count", 0) > 0
          and (stored.get("data_quality") or {}).get("data_nature") == "synthetic",
          f"{r.status_code} stored={stored.get('bar_count')} "
          f"nature={(stored.get('data_quality') or {}).get('data_nature')}")
except Exception as e:
    check("E3", "market data flow", False, repr(e))


# ── E4: backtest run ─────────────────────────────────────────────
section("E4 — Backtest run")
try:
    r = requests.post(f"{API}/versions/{version_id}/runs", headers=headers, timeout=120)
    run = r.json() if r.status_code == 201 else {}
    warnings = run.get("warnings") or []
    synth_warning = any("Synthetic data used" in w and "DEMO-ALPHA" in w for w in warnings)
    check("E4", "run completed", run.get("status") == "completed",
          f"{r.status_code} status={run.get('status')} err={run.get('error_details')}")
    check("E4", "metrics + integrity fields",
          bool(run.get("metrics")) and bool(run.get("config_hash"))
          and bool(run.get("dataset_checksum")) and bool(run.get("engine_version")),
          f"engine={run.get('engine_version')} return={run.get('metrics', {}).get('total_return_pct')}")
    check("E4", "synthetic provenance warning surfaced", synth_warning,
          f"warnings={warnings}")
except Exception as e:
    check("E4", "backtest flow", False, repr(e))


# ── E5: ML train via real worker process → reload → inference → usage ──
section("E5 — ML training / worker / inference / usage")
worker = None
worker_log = open(f"/tmp/e2e-worker-{RUN_ID}.log", "wb")
model_id = None
try:
    r = requests.get(f"{API}/usage/quota",
                     params={"quota_type": "ai_usage_limit"}, headers=headers, timeout=15)
    q0 = r.json().get("used") if r.status_code == 200 else None

    r = requests.post(
        f"{API}/ml/train",
        params={"instrument_id": "US:AAPL", "model_type": "baseline",
                "start_date": "2025-01-02", "end_date": "2025-06-30"},
        headers=headers, timeout=30,
    )
    job_id = r.json().get("job_id") if r.status_code == 200 else None
    check("E5", "training job enqueued", r.status_code == 200 and bool(job_id),
          f"{r.status_code} job_id={job_id}")

    env = dict(os.environ)
    env.update({
        "DATABASE_URL": DB_URL,
        "DATA_PROVIDER": "demo",
        "AI_PROVIDER": "mock",
        "APP_ENV": "development",
        "ENABLE_LIVE_PAYMENTS": "false",
    })
    worker = subprocess.Popen(
        [PY, "-m", "app.workers.training_worker", "1"],
        cwd=BACKEND_DIR, env=env, stdout=worker_log, stderr=subprocess.STDOUT,
    )

    def _job():
        rr = requests.get(f"{API}/training/jobs/{job_id}", headers=headers, timeout=15)
        return rr.json() if rr.status_code == 200 else None

    def _completed_job():
        j = _job()
        return j if (j or {}).get("status") == "completed" else None

    job = wait_for(_completed_job, 120)
    check("E5", "worker processed job to completed", bool(job),
          f"status={(_job() or {}).get('status')}")
    job = job or _job() or {}
    best = job.get("best_params") or {}
    model_id = best.get("model_id")
    check("E5", "model artifact id recorded", bool(model_id), f"model_id={model_id}")
    check("E5", "job records synthetic data nature", best.get("data_nature") == "synthetic",
          f"data_nature={best.get('data_nature')}")

    r = requests.post(f"{API}/ml/models/{model_id}/predict-latest", headers=headers, timeout=60)
    pred = r.json() if r.status_code == 200 else {}
    check("E5", "predict-latest from reloaded artifact",
          r.status_code == 200 and "latest_prediction" in pred,
          f"{r.status_code} keys={sorted(pred)[:8]}")
    check("E5", "prediction carries synthetic provenance",
          pred.get("data_nature") == "synthetic", f"data_nature={pred.get('data_nature')}")

    r = requests.get(f"{API}/usage/quota",
                     params={"quota_type": "ai_usage_limit"}, headers=headers, timeout=15)
    q1 = r.json().get("used") if r.status_code == 200 else None
    check("E5", "usage counted against quota",
          q0 is not None and q1 is not None and q1 > q0, f"used {q0} → {q1}")

    r = requests.get(f"{API}/usage/summary", headers=headers, timeout=15)
    check("E5", "usage summary available", r.status_code == 200, f"{r.status_code}")
except Exception as e:
    check("E5", "ml flow", False, repr(e))
finally:
    if worker and worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
    worker_log.close()


# ── E6: execution intent → agent → fills / reconciliation / cancel ──
section("E6 — Execution: intent → agent → fills → reconcile → cancel")
agent = None
agent_log = open(f"/tmp/e2e-agent-{RUN_ID}.log", "wb")
try:
    r = requests.post(f"{API}/paper-portfolio", json={"initial_virtual_cash": 100000},
                      headers=headers, timeout=15)
    portfolio_id = r.json().get("id") if r.status_code == 201 else None
    check("E6", "paper portfolio created", bool(portfolio_id), f"{r.status_code}")

    def _intent(symbol="DEMO-ALPHA", side="BUY", quantity=10, order_type="MARKET"):
        rr = requests.post(f"{API}/execution/intents", headers=headers, json={
            "portfolio_id": portfolio_id, "symbol": symbol, "side": side,
            "quantity": quantity, "order_type": order_type,
        }, timeout=15)
        return rr

    r = _intent()
    intent1 = r.json() if r.status_code == 201 else {}
    intent_id = intent1.get("intent_id")
    check("E6", "intent created pending",
          r.status_code == 201 and intent1.get("status") == "pending" and bool(intent_id),
          f"{r.status_code} status={intent1.get('status')}")
    check("E6", "authorization token issued",
          bool(intent1.get("authorization_token")) and intent_id == intent1.get("intent_id"),
          "")

    r = requests.post(f"{API}/execution/intents/{intent_id}/authorize", headers=headers,
                      json={"authorization_token": intent1.get("authorization_token")}, timeout=15)
    check("E6", "intent authorized", r.status_code == 200
          and r.json().get("status") == "authorized", f"{r.status_code} {r.json().get('status')}")

    r = requests.post(f"{API}/api-keys", headers=headers,
                      json={"name": f"e2e-agent-{RUN_ID}", "scopes": ["read", "write"]}, timeout=15)
    raw_key = r.json().get("raw_key") if r.status_code == 201 else None
    check("E6", "agent API key issued (raw shown once)", bool(raw_key), f"{r.status_code}")

    env = dict(os.environ)
    env.update({
        "ALETHRA_API_URL": BASE,
        "ALETHRA_API_KEY": raw_key,
        "ALETHRA_BROKER_TRANSPORT": "fake",
        "POLL_INTERVAL": "1",
        "TRADING_MODE": "paper",
        "LOG_LEVEL": "INFO",
    })
    agent = subprocess.Popen(
        [PY, "-m", "app.execution_agent.agent"],
        cwd=BACKEND_DIR, env=env, stdout=agent_log, stderr=subprocess.STDOUT,
    )

    def _intent_state(iid):
        rr = requests.get(f"{API}/execution/intents/{iid}", headers=headers, timeout=15)
        return rr.json() if rr.status_code == 200 else {}

    def _filled_state():
        s = _intent_state(intent_id)
        return s if s.get("status") == "filled" else None

    state = wait_for(_filled_state, 45)
    state = state or _intent_state(intent_id)
    check("E6", "agent submitted order and fills reported",
          state.get("status") == "filled", f"status={state.get('status')}")

    r = requests.get(f"{API}/execution/intents/{intent_id}/fills", headers=headers, timeout=15)
    fills = r.json() if r.status_code == 200 else []
    fill_qty = sum(f.get("quantity", 0) for f in fills) if isinstance(fills, list) else 0
    check("E6", "fills recorded and complete",
          isinstance(fills, list) and len(fills) >= 1 and fill_qty == 10,
          f"fills={len(fills) if isinstance(fills, list) else fills} qty={fill_qty}")

    r = requests.post(f"{API}/execution/intents/{intent_id}/reconcile", headers=headers, timeout=15)
    rec = r.json() if r.status_code == 201 else {}
    check("E6", "reconciliation no discrepancy",
          r.status_code == 201 and rec.get("expected_quantity") == 10
          and rec.get("filled_quantity") == 10 and rec.get("discrepancy") is False,
          f"{r.status_code} {rec}")

    if agent and agent.poll() is None:
        agent.terminate()
        try:
            agent.wait(timeout=10)
        except subprocess.TimeoutExpired:
            agent.kill()
    time.sleep(1)

    # Platform cancel on an authorized (not yet submitted) intent — no agent race.
    r2 = _intent()
    intent2 = r2.json() if r2.status_code == 201 else {}
    iid2 = intent2.get("intent_id")
    requests.post(f"{API}/execution/intents/{iid2}/authorize", headers=headers,
                  json={"authorization_token": intent2.get("authorization_token")}, timeout=15)
    r = requests.post(f"{API}/execution/intents/{iid2}/cancel", headers=headers, timeout=15)
    check("E6", "platform cancel before submission", r.status_code == 200
          and _intent_state(iid2).get("status") == "cancelled",
          f"{r.status_code} status={_intent_state(iid2).get('status')}")
except Exception as e:
    check("E6", "execution flow", False, repr(e))
finally:
    if agent and agent.poll() is None:
        agent.terminate()
        try:
            agent.wait(timeout=10)
        except subprocess.TimeoutExpired:
            agent.kill()
    agent_log.close()


# ── E7: checkout fixture → signed webhook → entitlement ──────────
section("E7 — Billing: checkout → signed webhook → entitlement")
try:
    import psycopg2
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()
    plan_id = str(uuid.uuid4())
    plan_price = 999
    cur.execute(
        "INSERT INTO plans (id, name, description, monthly_price, currency, features, is_active, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, TRUE, NOW())",
        (plan_id, f"E2E Plan {RUN_ID}", "E2E fixture plan", plan_price, "USD",
         json.dumps({"stripe_price_id": f"price_e2e_{RUN_ID}"})),
    )
    cur.close()
    conn.close()
    check("E7", "plan fixture seeded into disposable PG", True, f"plan_id={plan_id}")

    r = requests.get(f"{API}/plans", timeout=15)
    plans = r.json().get("plans", []) if r.status_code == 200 else []
    check("E7", "plan visible via API", any(p.get("id") == plan_id for p in plans),
          f"{len(plans)} plan(s)")

    r = requests.post(f"{API}/checkout", params={
        "plan_id": plan_id,
        "success_url": "http://localhost:3000/success",
        "cancel_url": "http://localhost:3000/cancel",
    }, headers=headers, timeout=15)
    session = r.json() if r.status_code == 200 else {}
    check("E7", "checkout session created (test mode)",
          r.status_code == 200 and session.get("test_mode") is True and bool(session.get("session_id")),
          f"{r.status_code} session={session.get('session_id')}")

    r = requests.get(f"{API}/subscriptions/me", headers=headers, timeout=15)
    sub = (r.json() or {}).get("subscription") or {}
    sub_id = sub.get("id")
    check("E7", "local subscription created trialing",
          r.status_code == 200 and sub.get("status") == "trialing" and bool(sub_id),
          f"{r.status_code} status={sub.get('status')}")

    body = json.dumps({
        "id": f"evt-e2e-{RUN_ID}",
        "type": "checkout.session.completed",
        "data": {"subscription_id": sub_id, "amount": plan_price, "currency": "USD"},
    })
    sig = hmac.new(WEBHOOK_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()

    r = requests.post(f"{API}/webhooks/payment", data=body.encode(),
                      headers={"X-Webhook-Signature": sig, "Content-Type": "application/json"},
                      timeout=15)
    check("E7", "signed webhook applied", r.status_code == 200
          and r.json().get("duplicate") is False, f"{r.status_code} {r.text[:120]}")

    r = requests.get(f"{API}/subscriptions/me", headers=headers, timeout=15)
    check("E7", "subscription activated", (r.json().get("subscription") or {}).get("status") == "active",
          f"status={(r.json().get('subscription') or {}).get('status')}")

    r = requests.get(f"{API}/pilot/entitlements/me", headers=headers, timeout=15)
    check("E7", "pilot entitlement granted", r.status_code == 200
          and r.json().get("pilot_access") is True, f"{r.status_code} {r.json().get('pilot_access')}")

    r = requests.post(f"{API}/webhooks/payment", data=body.encode(),
                      headers={"X-Webhook-Signature": sig, "Content-Type": "application/json"},
                      timeout=15)
    check("E7", "duplicate delivery idempotent", r.status_code == 200
          and r.json().get("duplicate") is True, f"{r.status_code} {r.text[:120]}")

    r = requests.post(f"{API}/webhooks/payment", data=body.encode(),
                      headers={"X-Webhook-Signature": "0" * 64, "Content-Type": "application/json"},
                      timeout=15)
    check("E7", "tampered signature rejected", r.status_code == 400, f"{r.status_code}")

    r = requests.post(f"{API}/webhooks/payment", data=body.encode(),
                      headers={"Content-Type": "application/json"}, timeout=15)
    check("E7", "missing signature rejected", r.status_code == 400, f"{r.status_code}")
except Exception as e:
    check("E7", "billing flow", False, repr(e))


# ── Summary ──────────────────────────────────────────────────────
failed = [r for r in results if not r[2]]
print(f"\n{'=' * 60}")
print(f"E2E FULL-STACK RESULT: {len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:")
    for stage, name, _, detail in failed:
        print(f"  - {stage} {name}: {detail}")
print(f"Integration branch SHA: ", end="")
print(subprocess.run(["git", "rev-parse", "HEAD"], cwd=os.path.dirname(os.path.abspath(__file__)),
                     capture_output=True, text=True).stdout.strip())
sys.exit(1 if failed else 0)
