"""Skip guard for pytest modules that probe a running backend over HTTP.

Three suites (test_c1_c4_http, test_c1_c4_complete, test_manual_replay_workflow)
are live-stack probes: they log in as a seeded ``owner@test.com``, POST to
``http://127.0.0.1:8000`` and insert simulation sessions straight into the dev
SQLite file with raw SQL. Under ``pytest tests`` on a clean checkout they can
only fail — and if a server happens to be listening they corrupt someone's dev
database. They are opt-in, and skipped with the exact missing precondition in
the reason (never xfail, so a genuinely broken probe cannot hide behind a
green "expected failure").
"""
import os
from typing import Optional, Tuple

import requests

HEALTH_TIMEOUT_SECONDS = 3
OPT_IN_ENV_VAR = "ALETHRA_LIVE_DEV_TESTS"


def live_probe_skip_reason(base_url: str, db_paths: Tuple[str, ...] = ()) -> Optional[str]:
    """Return why the probe cannot run, or None when every precondition holds."""
    if os.environ.get(OPT_IN_ENV_VAR) != "1":
        return (
            f"{OPT_IN_ENV_VAR} is not set to 1: this module writes sessions "
            "directly into a dev database and needs a seeded owner@test.com"
        )

    health_url = f"{base_url}/health"
    try:
        resp = requests.get(health_url, timeout=HEALTH_TIMEOUT_SECONDS)
    except Exception as exc:
        return f"no backend answering {health_url} ({type(exc).__name__})"
    if resp.status_code != 200:
        return f"{health_url} returned {resp.status_code}"

    for path in db_paths:
        if not os.path.exists(path):
            return f"this probe seeds rows directly into {path}, which does not exist"
    return None
