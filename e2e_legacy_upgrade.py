#!/usr/bin/env python3
"""E8: legacy schema upgrade → smoke → restore on a disposable PostgreSQL.

Builds a representative legacy-v1 database (TEXT ids, TEXT json columns,
no alembic_version) in a disposable PG database, stamps the pre-upgrade
revision, upgrades through the full chain, smoke-tests the app against the
upgraded database, then downgrades to verify restoration.

Exit codes: 0 all checks PASS, 1 failure.
"""

import bcrypt
import json
import os
import subprocess
import sys
import uuid

import psycopg2

PG = os.environ.get("E2E_LEGACY_DB_URL",
                    "postgresql://postgres:postgres@127.0.0.1:5434/alethra_e2e_legacy")
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
PY = os.environ.get("E2E_PY", sys.executable)
PRE_REVISION = "a1b2c3d4e5f6"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)[:300]))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")
    return ok


def alembic(args):
    env = dict(os.environ)
    env["DATABASE_URL"] = PG
    return subprocess.run(
        [PY, "-m", "alembic"] + args,
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=300,
    )


def conn():
    return psycopg2.connect(PG)


# ── 0. Recreate the disposable legacy database ───────────────────
print("\n=== E8.0 — create disposable legacy database ===")
admin = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5434/postgres")
admin.autocommit = True
cur = admin.cursor()
cur.execute("DROP DATABASE IF EXISTS alethra_e2e_legacy")
cur.execute("CREATE DATABASE alethra_e2e_legacy")
cur.close()
admin.close()
check("database created", True, PG)

# ── 1. Build the legacy v1 fixture ────────────────────────────────
print("\n=== E8.1 — build representative legacy-v1 fixture ===")
c = conn()
c.autocommit = True
cur = c.cursor()
cur.execute("""
    CREATE TABLE users (
        id VARCHAR(36) PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL, display_name VARCHAR(255),
        user_type VARCHAR(50), created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
        is_active BOOLEAN DEFAULT TRUE, is_admin BOOLEAN DEFAULT FALSE
    )""")
cur.execute("""
    CREATE TABLE datasets (
        id VARCHAR(36) PRIMARY KEY, name VARCHAR(255) NOT NULL, provider VARCHAR(100) NOT NULL,
        source_type VARCHAR(50), license_status VARCHAR(50), description TEXT,
        created_at TIMESTAMP NOT NULL
    )""")
cur.execute("""
    CREATE TABLE experiments (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL, workspace_id VARCHAR(36),
        title VARCHAR(255) NOT NULL, original_idea TEXT, research_type VARCHAR(50),
        hypothesis TEXT, falsification_conditions TEXT, benchmark VARCHAR(100),
        scope TEXT, status VARCHAR(50), visibility VARCHAR(50), current_version_id VARCHAR(36),
        created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
    )""")
cur.execute("""
    CREATE TABLE workspaces (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL, name VARCHAR(255) NOT NULL,
        created_at TIMESTAMP NOT NULL
    )""")
cur.execute("""
    CREATE TABLE training_jobs (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL, experiment_id VARCHAR(36),
        title VARCHAR(255) NOT NULL, status VARCHAR(50) NOT NULL DEFAULT 'pending',
        config_json TEXT, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
    )""")

fixture_ids = {"users": [], "experiments": []}
LEGACY_PASSWORD = "legacy-pass-123"
legacy_hash = bcrypt.hashpw(LEGACY_PASSWORD.encode(), bcrypt.gensalt()).decode()
for i in range(3):
    uid, wid, did, eid, jid = (str(uuid.uuid4()) for _ in range(5))
    fixture_ids["users"].append(uid)
    fixture_ids["experiments"].append(eid)
    cur.execute(
        "INSERT INTO users (id, email, password_hash, created_at, updated_at)"
        " VALUES (%s, %s, %s, NOW(), NOW())",
        (uid, f"legacy-user-{i}@example.com", legacy_hash))
    cur.execute(
        "INSERT INTO workspaces (id, owner_id, name, created_at) VALUES (%s, %s, %s, NOW())",
        (wid, uid, f"Legacy workspace {i}"))
    cur.execute(
        "INSERT INTO datasets (id, name, provider, created_at) VALUES (%s, %s, %s, NOW())",
        (did, f"Legacy dataset {i}", "demo"))
    cur.execute(
        "INSERT INTO experiments (id, owner_id, title, scope, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, NOW(), NOW())",
        (eid, uid, f"Legacy experiment {i}",
         json.dumps({"asset_universe": ["AAPL", f"TICK{i}"]})))
    cur.execute(
        "INSERT INTO training_jobs (id, owner_id, title, status, config_json, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, NOW(), NOW())",
        (jid, uid, f"Legacy job {i}", "completed", json.dumps({"epochs": 10 + i})))

cur.execute("SELECT COUNT(*) FROM users"); n_users = cur.fetchone()[0]
cur.execute("SELECT data_type FROM information_schema.columns"
            " WHERE table_name='users' AND column_name='id'")
users_id_type = cur.fetchone()[0]
cur.execute("SELECT data_type FROM information_schema.columns"
            " WHERE table_name='experiments' AND column_name='scope'")
scope_type = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='alembic_version'")
has_alembic = cur.fetchone()[0]
cur.close()
c.close()
check("fixture: 3 users in 5 legacy tables", n_users == 3, f"users={n_users}")
check("fixture: legacy VARCHAR ids", users_id_type in ("character", "character varying"), users_id_type)
check("fixture: legacy TEXT json", scope_type == "text", scope_type)
check("fixture: no alembic_version yet", has_alembic == 0, f"tables={has_alembic}")

# ── 2. Stamp pre-upgrade revision, then upgrade through the chain ──
print("\n=== E8.2 — alembic stamp + upgrade head ===")
r = alembic(["stamp", PRE_REVISION])
check("stamp a1b2c3d4e5f6", r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-1:] or "")

r = alembic(["upgrade", "head"])
ok = r.returncode == 0
check("upgrade head", ok, (r.stderr or r.stdout).strip().splitlines()[-3:] if not ok else "")

c = conn()
cur = c.cursor()
cur.execute("SELECT version_num FROM alembic_version")
revs = sorted(row[0] for row in cur.fetchall())
cur.execute("SELECT data_type FROM information_schema.columns"
            " WHERE table_name='users' AND column_name='id'")
users_id_type = cur.fetchone()[0]
cur.execute("SELECT data_type FROM information_schema.columns"
            " WHERE table_name='experiments' AND column_name='scope'")
scope_type = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM information_schema.tables"
            " WHERE table_name='_legacy_schema_backup'")
backup_tables = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM users"); n_users_post = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM experiments"); n_exp_post = cur.fetchone()[0]
cur.execute("SELECT scope FROM experiments ORDER BY created_at LIMIT 1")
scope_val = cur.fetchone()[0]
cur.close()
c.close()

check("chain converged to single head", revs == ["e5f6a7b8c9d0"], f"heads={revs}")
check("users.id converted to UUID", users_id_type == "uuid", users_id_type)
check("experiments.scope converted to JSONB", scope_type == "jsonb", scope_type)
check("legacy conversion backup recorded", backup_tables == 1, f"backup_tables={backup_tables}")
check("user rows preserved", n_users_post == 3, f"users={n_users_post}")
check("experiment rows preserved", n_exp_post == 3, f"experiments={n_exp_post}")
check("json scope data intact", isinstance(scope_val, dict)
      and scope_val.get("asset_universe", [None])[0] == "AAPL"
      and len(scope_val.get("asset_universe", [])) == 2,
      str(scope_val)[:80])

# ── 3. Smoke: the app boots and serves against the upgraded DB ────
print("\n=== E8.3 — smoke on upgraded legacy database ===")
import requests  # noqa: E402

env = dict(os.environ)
env.update({
    "DATABASE_URL": PG,
    "JWT_SECRET_KEY": "e2e-legacy-smoke-secret-minimum-32-chars",
    "AI_PROVIDER": "mock",
    "DATA_PROVIDER": "demo",
    "APP_ENV": "development",
    "ENABLE_LIVE_PAYMENTS": "false",
})
proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8113"],
    cwd=BACKEND_DIR, env=env,
    stdout=open(f"/tmp/e2e-legacy-smoke.log", "wb"), stderr=subprocess.STDOUT)
try:
    base = "http://127.0.0.1:8113"
    healthy = None
    import time
    for _ in range(30):
        try:
            rr = requests.get(f"{base}/health/ready", timeout=2)
            if rr.status_code == 200:
                healthy = rr
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    check("readiness on upgraded DB", healthy is not None,
          f"status={getattr(healthy, 'status_code', None)}")

    if healthy is not None:
        rr = requests.post(f"{base}/api/v1/experiments/", json={
            "title": "post-upgrade probe", "original_idea": "x", "research_type": "quantitative",
            "hypothesis": "h", "falsification_conditions": "f", "benchmark": "DEMO-BENCHMARK",
            "strategy_spec": {
                "template_type": "buy_and_hold", "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02", "end_date": "2024-02-13", "initial_cash": 100000,
                "commission_rate": 0.001, "slippage_rate": 0.0005, "benchmark_id": "DEMO-BENCHMARK",
                "signal_parameters": {}, "entry_rule": {"type": "buy_on_start"},
                "exit_rule": {"type": "hold_to_end"}, "sizing_rule": {"type": "full_invest"},
                "execution_timing": {"type": "close"},
            },
        }, timeout=15)  # expect 401 without a token — proves routing + DB work
        check("API serves against upgraded DB", rr.status_code in (401, 403), f"{rr.status_code}")

        rr = requests.post(f"{base}/api/auth/login", json={
            "email": "legacy-user-0@example.com", "password": LEGACY_PASSWORD}, timeout=15)
        check("legacy user can still log in after upgrade",
              rr.status_code == 200 and bool(rr.json().get("access_token")),
              f"{rr.status_code}")
except Exception as e:
    check("smoke flow", False, repr(e))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

# ── 4. Restore: downgrade back to the pre-upgrade revision ────────
print("\n=== E8.4 — downgrade (restore) ===")
r = alembic(["downgrade", PRE_REVISION])
check("downgrade to a1b2c3d4e5f6", r.returncode == 0,
      (r.stderr or r.stdout).strip().splitlines()[-3:] if r.returncode != 0 else "")

c = conn()
cur = c.cursor()
cur.execute("SELECT version_num FROM alembic_version")
revs = sorted(row[0] for row in cur.fetchall())
cur.execute("SELECT data_type FROM information_schema.columns"
            " WHERE table_name='users' AND column_name='id'")
users_id_type = cur.fetchone()[0]
cur.execute("SELECT data_type FROM information_schema.columns"
            " WHERE table_name='experiments' AND column_name='scope'")
scope_type = cur.fetchone()[0]
cur.execute("SELECT id::text FROM users ORDER BY email")
restored_user_ids = sorted(row[0] for row in cur.fetchall())
cur.execute("SELECT COUNT(*) FROM information_schema.tables"
            " WHERE table_name='_legacy_schema_backup'")
backup_tables = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM users"); n_users_restored = cur.fetchone()[0]
cur.execute("SELECT scope FROM experiments ORDER BY created_at LIMIT 1")
scope_row = cur.fetchone()[0]
cur.close()
c.close()

check("revision restored to pre-upgrade", revs == [PRE_REVISION], f"revs={revs}")
check("users.id restored to VARCHAR(36)", "CHAR" in users_id_type.upper(), users_id_type)
check("experiments.scope restored to TEXT", scope_type == "text", scope_type)
check("legacy user ids preserved through roundtrip", restored_user_ids == sorted(fixture_ids["users"]),
      f"{len(restored_user_ids)} ids")
check("backup table consumed", backup_tables == 0, f"backup_tables={backup_tables}")
check("user rows preserved after restore", n_users_restored == 3, f"users={n_users_restored}")
parsed_scope = scope_row if isinstance(scope_row, dict) else json.loads(scope_row)
check("scope JSON readable after restore",
      parsed_scope.get("asset_universe", [None])[0] == "AAPL"
      and len(parsed_scope.get("asset_universe", [])) == 2,
      str(scope_row)[:80])

failed = [r for r in results if not r[1]]
print(f"\n{'=' * 60}")
print(f"E8 LEGACY UPGRADE ROUNDTRIP: {len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    for name, _, detail in failed:
        print(f"  - {name}: {detail}")
sys.exit(1 if failed else 0)
