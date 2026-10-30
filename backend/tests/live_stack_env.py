"""Tracked harness for the three live-stack probe modules.

These probes talk to a *running* API and write stored bars / sessions / paper
orders into the database that API is using. They are therefore opt-in and
refuse to run unless the caller names a disposable stack explicitly:

    ALETHRA_LIVE_PROBES=1
    ALETHRA_PROBE_API_ORIGIN=http://127.0.0.1:<port>
    ALETHRA_PROBE_DB_URL=postgresql+psycopg2://...@127.0.0.1:<port>/alethra_r<N>_<label>_<YYYYMMDD>
    ALETHRA_PROBE_DB_NAME=alethra_r<N>_<label>_<YYYYMMDD>

There is deliberately no default for any of them. The previous guard defaulted
to 127.0.0.1:8000 + backend/alethra_dev.db + a seeded owner@test.com account;
that made the probes corpus-dependent and let them write into whoever's dev
database happened to be listening. Here the probes create their own account,
experiment, bars and sessions through the public API, and delete exactly those
rows afterwards.

Preflight proves *actual* identity, not a name prefix:

    current_database() == ALETHRA_PROBE_DB_NAME
    alembic_version is present (schema was migrated, not hand-made)
    /health and /health/ready answer, readiness reports the database connected
    a freshly registered probe account is readable by id in that database,
      which is the only evidence that this API and this DB are coupled

Refusals raise ``LiveProbeRefused`` with the failing identity in the message.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import pytest
import requests

OPT_IN_VAR = "ALETHRA_LIVE_PROBES"
ORIGIN_VAR = "ALETHRA_PROBE_API_ORIGIN"
DB_URL_VAR = "ALETHRA_PROBE_DB_URL"
DB_NAME_VAR = "ALETHRA_PROBE_DB_NAME"

DISPOSABLE_DB_RE = re.compile(r"^alethra_r\d+_[a-z0-9]+(?:_[a-z0-9]+)*_\d{8}$")
NON_DISPOSABLE_MARKERS = ("candidate", "dev")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
HEALTH_TIMEOUT_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 20

PROBE_PROVIDER = "twelvedata"
PROBE_START = "2024-01-01"
PROBE_END = "2024-01-31"
# Weekdays in the requested window minus the 2024-01-15 market holiday, so the
# fixture has the same 20-bar shape the original probes asserted.
PROBE_BAR_DATES = tuple(
    d
    for d in (date(2024, 1, 2) + timedelta(days=i) for i in range(29))
    if d.weekday() < 5 and d != date(2024, 1, 15)
)
assert len(PROBE_BAR_DATES) == 20


class ProbeConfigRefusal(ValueError):
    """The environment does not name a disposable probe stack."""


class LiveProbeRefused(RuntimeError):
    """The named stack exists but failed an identity or health check."""


@dataclass(frozen=True)
class ProbeTarget:
    origin: str
    db_url: str
    db_name: str


def db_name_from_url(db_url: str) -> Optional[str]:
    match = re.match(r"^[a-z0-9+]+://[^/]+/([^?/#]+)", db_url or "")
    return match.group(1) if match else None


def parse_target(env: Optional[Dict[str, str]] = None) -> ProbeTarget:
    """Validate the env contract. Pure function: no network, no filesystem."""
    env = os.environ if env is None else env

    if env.get(OPT_IN_VAR) != "1":
        raise ProbeConfigRefusal(
            f"{OPT_IN_VAR} is not set to 1: these probes write bars, sessions and "
            "paper orders into whatever database the API uses, so they only run "
            "on an explicitly provided disposable stack"
        )

    origin = (env.get(ORIGIN_VAR) or "").strip().rstrip("/")
    if not origin:
        raise ProbeConfigRefusal(
            f"{ORIGIN_VAR} is not set (no default by design: the probes must not "
            "silently target 127.0.0.1:8000)"
        )
    scheme, _, host_part = origin.partition("://")
    host = host_part.split("/")[0].split(":")[0]
    if scheme not in ("http", "https") or not host:
        raise ProbeConfigRefusal(f"{ORIGIN_VAR}={origin!r} is not an http(s) origin")
    if host not in LOOPBACK_HOSTS:
        raise ProbeConfigRefusal(
            f"{ORIGIN_VAR}={origin!r} is not loopback: probe writes are only "
            "allowed against a local disposable stack"
        )

    db_url = (env.get(DB_URL_VAR) or "").strip()
    if not db_url:
        raise ProbeConfigRefusal(
            f"{DB_URL_VAR} is not set: the probes insert stored bars and clean "
            "them up, so they need the API's database, not a test file"
        )
    if not db_url.startswith("postgresql+psycopg2://"):
        raise ProbeConfigRefusal(
            f"{DB_URL_VAR} must be a postgresql+psycopg2:// URL (got scheme "
            f"{db_url.split(':', 1)[0]!r}); a SQLite file cannot prove the API/DB "
            "coupling these probes exist to check"
        )

    declared = (env.get(DB_NAME_VAR) or "").strip()
    if not declared:
        raise ProbeConfigRefusal(
            f"{DB_NAME_VAR} is not set: the probes refuse to guess which database "
            "they are about to write into"
        )

    actual = db_name_from_url(db_url)
    if actual != declared:
        raise ProbeConfigRefusal(
            f"{DB_NAME_VAR}={declared!r} does not match the database named in "
            f"{DB_URL_VAR} ({actual!r})"
        )

    lowered = declared.lower()
    if lowered == "alethra_dev" or any(marker in lowered for marker in NON_DISPOSABLE_MARKERS):
        raise ProbeConfigRefusal(
            f"database {declared!r} looks like a shared dev or accepted-candidate "
            "database; the probes refuse to write into it"
        )
    if not DISPOSABLE_DB_RE.match(declared):
        raise ProbeConfigRefusal(
            f"database {declared!r} is not named like a per-round disposable "
            "database (alethra_r<N>_<label>_<YYYYMMDD>)"
        )

    return ProbeTarget(origin=origin, db_url=db_url, db_name=declared)


def skip_reason(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Why the probes cannot run, from the environment alone (used by skipif).

    Network/identity failures are deliberately *not* folded in here: a named but
    broken stack is a failure to investigate, not a reason to skip.
    """
    try:
        parse_target(env)
    except ProbeConfigRefusal as exc:
        return str(exc)
    return None


def _psycopg2():
    try:
        import psycopg2  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the interpreter
        raise LiveProbeRefused(
            "psycopg2 is not importable: the live probes need the same driver "
            "the API uses"
        ) from exc
    return psycopg2


def _dsn(db_url: str) -> str:
    return "postgresql://" + db_url.split("://", 1)[1]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ProbeVersion:
    id: str
    experiment_id: str
    symbol: str
    initial_cash: float = 100000.0


@dataclass
class ProbeSession:
    probe: "LiveProbe"
    id: str
    version: ProbeVersion
    portfolio_id: Optional[str] = None

    def advance(self) -> Dict[str, Any]:
        return self.probe.post(f"/api/v1/simulations/{self.id}/advance")

    def submit(self, action: str, *, quantity: Optional[int] = None,
               rationale: str = "", key: Optional[str] = None,
               expected_version: Optional[int] = None,
               expect: Sequence[int] = (200,)):
        payload: Dict[str, Any] = {
            "action": action,
            "rationale": rationale or f"probe {action}",
            "idempotency_key": key or f"probe-{secrets.token_hex(8)}",
        }
        if quantity is not None:
            payload["quantity"] = quantity
        if expected_version is not None:
            payload["expected_version"] = expected_version
        return self.probe.request(
            "POST", f"/api/v1/simulations/{self.id}/manual-decision",
            json=payload, expect=expect,
        )

    def submit_raw(self, payload: Dict[str, Any]):
        """Unasserted submission: race probes inspect both status codes."""
        return self.probe.raw(
            "POST", f"/api/v1/simulations/{self.id}/manual-decision", json=payload,
        )

    def state(self) -> Dict[str, Any]:
        return self.probe.get(f"/api/v1/simulations/{self.id}")

    def decisions(self) -> List[Dict[str, Any]]:
        return self.probe.get(f"/api/v1/simulations/{self.id}/decisions")

    def orders(self) -> List[Dict[str, Any]]:
        return self.probe.get(f"/api/v1/simulations/{self.id}/orders")

    def fills(self, order_id: str) -> List[Dict[str, Any]]:
        if not self.portfolio_id:
            raise LiveProbeRefused("session has no linked portfolio")
        return self.probe.get(
            f"/api/v1/paper-portfolio/{self.portfolio_id}/orders/{order_id}/fills"
        )

    def counts(self) -> Dict[str, int]:
        """Row counts for this test-owned session, read straight from the DB.

        List endpoints are part of the product under test; a duplicate that the
        endpoint collapses would hide a double booking, so the probes count the
        rows the transaction actually wrote.
        """
        with self.probe._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM simulation_decisions WHERE session_id = %s",
                    (self.id,),
                )
                decisions = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*) FROM decision_records WHERE simulation_session_id = %s",
                    (self.id,),
                )
                decision_records = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*) FROM paper_orders o JOIN paper_portfolios p "
                    "ON p.id = o.portfolio_id WHERE p.simulation_session_id = %s",
                    (self.id,),
                )
                orders = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*) FROM paper_fills f JOIN paper_orders o "
                    "ON o.id = f.paper_order_id JOIN paper_portfolios p "
                    "ON p.id = o.portfolio_id WHERE p.simulation_session_id = %s",
                    (self.id,),
                )
                fills = cur.fetchone()[0]
        return {
            "decisions": decisions,
            "decision_records": decision_records,
            "orders": orders,
            "fills": fills,
        }


@dataclass
class LiveProbe:
    target: ProbeTarget
    headers: Dict[str, str] = field(default_factory=dict)
    user_id: Optional[str] = None
    identity: Dict[str, Any] = field(default_factory=dict)

    # -- preflight -------------------------------------------------------
    def start(self) -> "LiveProbe":
        self.identity["target"] = {
            "api_origin": self.target.origin,
            "declared_db": self.target.db_name,
            "started_at": _now_iso(),
        }
        db_identity = self._db_identity()
        self._check_api()
        self._register_account()
        self._assert_api_db_coupling()
        self.identity["database"] = db_identity
        self.identity["account_id"] = self.user_id
        return self

    def _connect(self):
        return _psycopg2().connect(_dsn(self.target.db_url), connect_timeout=5)

    def _db_identity(self) -> Dict[str, Any]:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT current_database()")
                    name = cur.fetchone()[0]
                    if name != self.target.db_name:
                        raise LiveProbeRefused(
                            f"{DB_NAME_VAR}={self.target.db_name!r} but the server "
                            f"reports current_database()={name!r}"
                        )
                    try:
                        cur.execute("SELECT version_num FROM alembic_version")
                        revision = cur.fetchone()[0]
                    except Exception as exc:
                        raise LiveProbeRefused(
                            f"database {name!r} has no readable alembic_version "
                            f"({type(exc).__name__}): it is not a migrated stack"
                        ) from exc
                    if not revision:
                        raise LiveProbeRefused(
                            f"database {name!r} has an empty alembic_version"
                        )
                    cur.execute(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                    tables = cur.fetchone()[0]
        except LiveProbeRefused:
            raise
        except Exception as exc:
            raise LiveProbeRefused(
                f"cannot open {self.target.db_name!r} at "
                f"{self.target.db_url.split('@')[-1]}: {type(exc).__name__}"
            ) from exc
        if tables == 0:
            raise LiveProbeRefused(
                f"database {name!r} has no public tables; run migrations first"
            )
        return {"name": name, "alembic_revision": revision, "public_tables": tables}

    def _check_api(self) -> None:
        try:
            health = requests.get(
                f"{self.target.origin}/health", timeout=HEALTH_TIMEOUT_SECONDS
            )
            ready = requests.get(
                f"{self.target.origin}/health/ready", timeout=HEALTH_TIMEOUT_SECONDS
            )
        except Exception as exc:
            raise LiveProbeRefused(
                f"no API answering {self.target.origin}/health "
                f"({type(exc).__name__})"
            ) from exc
        if health.status_code != 200 or health.json().get("status") != "healthy":
            raise LiveProbeRefused(
                f"{self.target.origin}/health returned {health.status_code} "
                f"{health.text[:120]}"
            )
        if ready.status_code != 200:
            raise LiveProbeRefused(
                f"{self.target.origin}/health/ready returned {ready.status_code}: "
                f"{ready.text[:200]}"
            )
        db_check = (ready.json().get("checks") or {}).get("database") or {}
        if db_check.get("status") != "connected":
            raise LiveProbeRefused(
                f"{self.target.origin}/health/ready reports database "
                f"{db_check.get('status')!r}, not 'connected'"
            )
        self.identity["readiness"] = ready.json()

    def _register_account(self) -> None:
        suffix = secrets.token_hex(6)
        self._email = f"liveprobe+{suffix}@probe.alethra.dev"
        self._password = secrets.token_urlsafe(24)
        resp = self._raw("POST", "/api/auth/register", json={
            "email": self._email,
            "password": self._password,
            "display_name": "live probe",
        })
        if resp.status_code != 201:
            raise LiveProbeRefused(
                f"probe account registration returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        self.user_id = resp.json()["id"]

        login = self._raw("POST", "/api/auth/login", json={
            "email": self._email, "password": self._password,
        })
        if login.status_code != 200:
            raise LiveProbeRefused(
                f"probe account login returned {login.status_code}: "
                f"{login.text[:200]}"
            )
        self.headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    def _assert_api_db_coupling(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT email FROM users WHERE id = %s", (self.user_id,))
                row = cur.fetchone()
        if row is None:
            raise LiveProbeRefused(
                f"account {self.user_id} was created by {self.target.origin} but is "
                f"absent from {self.target.db_name}: the API is not backed by the "
                "declared database"
            )
        if row[0] != self._email:
            raise LiveProbeRefused(
                f"account id {self.user_id} in {self.target.db_name} has a "
                "different email than the one just registered: identity mismatch"
            )

    # -- HTTP ------------------------------------------------------------
    def _raw(self, method: str, path: str, **kwargs):
        return requests.request(
            method, f"{self.target.origin}{path}",
            timeout=REQUEST_TIMEOUT_SECONDS, **kwargs,
        )

    def request(self, method: str, path: str, *, expect=(200,), **kwargs):
        kwargs.setdefault("headers", self.headers)
        resp = self._raw(method, path, **kwargs)
        if resp.status_code not in expect:
            raise AssertionError(
                f"{method} {path} returned {resp.status_code}, expected {expect}: "
                f"{resp.text[:400]}"
            )
        return resp

    def get(self, path: str, *, expect=(200,), **kwargs):
        return self.request("GET", path, expect=expect, **kwargs).json()

    def post(self, path: str, *, expect=(200,), **kwargs):
        return self.request("POST", path, expect=expect, **kwargs).json()

    def raw(self, method: str, path: str, **kwargs):
        """Unasserted request: race probes compare status codes themselves."""
        kwargs.setdefault("headers", self.headers)
        return self._raw(method, path, **kwargs)

    # -- fixtures ---------------------------------------------------------
    def create_version(self, label: str = "probe") -> ProbeVersion:
        symbol = f"US:PB{secrets.token_hex(3).upper()}"
        resp = self.request("POST", "/api/v1/experiments/", expect=(201,), json={
            "title": f"live probe {label}",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "sma_crossover",
                "asset_ids": [symbol],
                "start_date": PROBE_START,
                "end_date": PROBE_END,
                "initial_cash": 100000.0,
                # Long window far beyond the fixture: the rule engine stays in
                # warm-up, so every fill asserted below is the manual order's.
                "signal_parameters": {"short_window": 200, "long_window": 300},
            },
        })
        created = resp.json()
        experiment_id = created["id"]
        version_id = created["current_version_id"]
        self.request("PUT", f"/api/v1/experiments/{experiment_id}", json={
            "scope": {"status": "locked"},
        })
        self._insert_bars(symbol)
        return ProbeVersion(id=version_id, experiment_id=experiment_id, symbol=symbol)

    def _insert_bars(self, symbol: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                for i, bar_date in enumerate(PROBE_BAR_DATES):
                    base = 180.0 + i
                    cur.execute(
                        """
                        INSERT INTO market_data_bars
                            (id, instrument_id, provider_id, bar_date, bar_timestamp,
                             open, high, low, close, volume, adjustment_factor,
                             is_adjusted, fetched_at)
                        VALUES
                            (gen_random_uuid(), %s, %s, %s, %s,
                             %s, %s, %s, %s, 1000000, 1.0, 'false', now())
                        """,
                        (symbol, PROBE_PROVIDER, bar_date,
                         datetime.combine(bar_date, datetime.min.time()),
                         base, base + 1.0, base - 1.0, base + 0.5),
                    )

    def create_session(
        self, version: ProbeVersion, *, cash: float = 100000.0, portfolio: bool = True
    ) -> ProbeSession:
        session = self.request(
            "POST", f"/api/v1/simulations/versions/{version.id}", expect=(201,),
            json={"initial_virtual_cash": cash, "provider_id": PROBE_PROVIDER},
        ).json()
        portfolio_id = None
        if portfolio:
            paper = self.request(
                "POST", "/api/v1/paper-portfolio", expect=(201,),
                json={"initial_virtual_cash": cash, "simulation_session_id": session["id"]},
            ).json()
            portfolio_id = paper["id"]
        return ProbeSession(
            probe=self, id=session["id"], version=version, portfolio_id=portfolio_id,
        )

    def set_provider_confirmed(self, session_id: str, value: bool) -> None:
        """Flip the legacy guard on this probe's own session row.

        Only the row this probe created, still scoped by ``owner_id``, is ever
        touched: the guard test needs a session that predates provider
        confirmation, and the product has no endpoint that creates one.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE simulation_sessions SET provider_confirmed = %s "
                    "WHERE id = %s AND owner_id = %s",
                    (value, session_id, self.user_id),
                )
                if cur.rowcount != 1:
                    raise LiveProbeRefused(
                        f"expected to update probe session {session_id}, "
                        f"updated {cur.rowcount} row(s)"
                    )

    # -- teardown ---------------------------------------------------------
    def delete_sessions(self, session_ids: Sequence[str]) -> None:
        ids = [s for s in session_ids if s]
        if not ids:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '60s'")
                cur.execute(
                    "DELETE FROM paper_fills WHERE paper_order_id IN ("
                    "  SELECT o.id FROM paper_orders o"
                    "  JOIN paper_portfolios p ON p.id = o.portfolio_id"
                    "  WHERE p.simulation_session_id = ANY(%s::uuid[]))", (ids,))
                cur.execute(
                    "DELETE FROM paper_positions WHERE portfolio_id IN ("
                    "  SELECT id FROM paper_portfolios WHERE simulation_session_id = ANY(%s::uuid[]))",
                    (ids,))
                cur.execute(
                    "DELETE FROM paper_orders WHERE portfolio_id IN ("
                    "  SELECT id FROM paper_portfolios WHERE simulation_session_id = ANY(%s::uuid[]))",
                    (ids,))
                cur.execute(
                    "DELETE FROM order_intents WHERE portfolio_id IN ("
                    "  SELECT id FROM paper_portfolios WHERE simulation_session_id = ANY(%s::uuid[]))",
                    (ids,))
                cur.execute(
                    "DELETE FROM decision_records WHERE simulation_session_id = ANY(%s::uuid[]) "
                    "OR paper_portfolio_id IN (SELECT id FROM paper_portfolios "
                    "WHERE simulation_session_id = ANY(%s::uuid[]))", (ids, ids))
                cur.execute(
                    "DELETE FROM paper_portfolios WHERE simulation_session_id = ANY(%s::uuid[])",
                    (ids,))
                cur.execute(
                    "DELETE FROM human_decision_events WHERE simulation_session_id = ANY(%s::uuid[])",
                    (ids,))
                cur.execute(
                    "DELETE FROM simulation_decisions WHERE session_id = ANY(%s::uuid[])",
                    (ids,))
                cur.execute(
                    "DELETE FROM simulation_sessions WHERE id = ANY(%s::uuid[])", (ids,))

    def delete_version(self, version: ProbeVersion) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '60s'")
                cur.execute(
                    "DELETE FROM personalization_dataset_snapshots "
                    "WHERE strategy_version_id = %s", (version.id,))
                cur.execute("DELETE FROM research_notes WHERE version_id = %s", (version.id,))
                cur.execute("DELETE FROM research_runs WHERE experiment_version_id = %s",
                            (version.id,))
                cur.execute(
                    "DELETE FROM robustness_studies WHERE version_id = %s "
                    "OR experiment_id = %s", (version.id, version.experiment_id))
                cur.execute("DELETE FROM saved_comparisons WHERE experiment_id = %s",
                            (version.experiment_id,))
                cur.execute("DELETE FROM research_drafts WHERE experiment_id = %s",
                            (version.experiment_id,))
                cur.execute("DELETE FROM training_jobs WHERE experiment_id = %s",
                            (version.experiment_id,))
                cur.execute("UPDATE experiments SET current_version_id = NULL WHERE id = %s",
                            (version.experiment_id,))
                cur.execute("DELETE FROM experiment_versions WHERE experiment_id = %s",
                            (version.experiment_id,))
                cur.execute("DELETE FROM experiments WHERE id = %s",
                            (version.experiment_id,))
                cur.execute(
                    "DELETE FROM market_data_bars WHERE instrument_id = %s AND provider_id = %s",
                    (version.symbol, PROBE_PROVIDER))

    def finish(self) -> None:
        """Remove this run's account and every row the probes are allowed to own."""
        if not self.user_id:
            return
        uid = self.user_id
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '120s'")
                cur.execute(
                    "DELETE FROM outcome_evaluations WHERE decision_id IN ("
                    "  SELECT id FROM decision_records WHERE owner_id = %s)", (uid,))
                cur.execute(
                    "DELETE FROM paper_fills WHERE paper_order_id IN ("
                    "  SELECT o.id FROM paper_orders o"
                    "  JOIN paper_portfolios p ON p.id = o.portfolio_id"
                    "  WHERE p.owner_id = %s)", (uid,))
                cur.execute("DELETE FROM paper_positions WHERE portfolio_id IN ("
                            "  SELECT id FROM paper_portfolios WHERE owner_id = %s)", (uid,))
                cur.execute("DELETE FROM paper_orders WHERE portfolio_id IN ("
                            "  SELECT id FROM paper_portfolios WHERE owner_id = %s)", (uid,))
                cur.execute("DELETE FROM order_intents WHERE portfolio_id IN ("
                            "  SELECT id FROM paper_portfolios WHERE owner_id = %s)", (uid,))
                cur.execute("DELETE FROM decision_records WHERE owner_id = %s", (uid,))
                cur.execute("DELETE FROM paper_portfolios WHERE owner_id = %s", (uid,))
                cur.execute("DELETE FROM human_decision_events WHERE owner_id = %s", (uid,))
                cur.execute("DELETE FROM simulation_decisions WHERE session_id IN ("
                            "  SELECT id FROM simulation_sessions WHERE owner_id = %s)", (uid,))
                cur.execute("DELETE FROM simulation_sessions WHERE owner_id = %s", (uid,))
                cur.execute("DELETE FROM personalization_dataset_snapshots WHERE owner_id = %s",
                            (uid,))
                cur.execute("DELETE FROM research_notes WHERE author_id = %s", (uid,))
                cur.execute("DELETE FROM robustness_studies WHERE user_id = %s", (uid,))
                cur.execute("DELETE FROM saved_comparisons WHERE author_id = %s", (uid,))
                cur.execute("DELETE FROM research_drafts WHERE user_id = %s", (uid,))
                cur.execute("DELETE FROM decision_labels WHERE owner_id = %s OR author_id = %s",
                            (uid, uid))
                cur.execute("UPDATE experiments SET current_version_id = NULL WHERE owner_id = %s",
                            (uid,))
                cur.execute("DELETE FROM experiment_versions WHERE experiment_id IN ("
                            "  SELECT id FROM experiments WHERE owner_id = %s)", (uid,))
                cur.execute(
                    "SELECT DISTINCT instrument_id FROM market_data_bars "
                    "WHERE instrument_id LIKE 'US:PB%%' AND provider_id = %s",
                    (PROBE_PROVIDER,))
                symbols = [row[0] for row in cur.fetchall()]
                for symbol in symbols:
                    cur.execute(
                        "DELETE FROM market_data_bars WHERE instrument_id = %s AND provider_id = %s",
                        (symbol, PROBE_PROVIDER))
                cur.execute("DELETE FROM experiments WHERE owner_id = %s", (uid,))
                cur.execute("DELETE FROM audit_events WHERE actor_id = %s", (uid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))


def submit_concurrently(
    session: ProbeSession, payloads: Sequence[Dict[str, Any]]
) -> List[Any]:
    """Submit several manual intents in parallel and return their responses.

    The probes exist to check what a real race does to persisted state, so the
    requests start together and each caller inspects the status codes itself.
    """
    responses: List[Optional[Any]] = [None] * len(payloads)

    def worker(index: int, payload: Dict[str, Any]) -> None:
        responses[index] = session.submit_raw(payload)

    threads = [
        threading.Thread(target=worker, args=(index, payload))
        for index, payload in enumerate(payloads)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    missing = [i for i, response in enumerate(responses) if response is None]
    if missing:
        raise LiveProbeRefused(f"race request(s) at index {missing} never returned")
    return [response for response in responses if response is not None]


# --------------------------------------------------------------------------
# Pytest fixtures. Import these names into a probe module to register them.
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def live_probe():
    reason = skip_reason()
    if reason is not None:
        pytest.skip(reason)
    probe = LiveProbe(parse_target())
    try:
        probe.start()
    except LiveProbeRefused as exc:
        pytest.fail(str(exc))
    yield probe
    probe.finish()


@pytest.fixture(scope="module")
def probe_version(live_probe):
    version = live_probe.create_version()
    yield version
    live_probe.delete_version(version)


@pytest.fixture
def new_session(live_probe, probe_version):
    created: List[ProbeSession] = []

    def factory(*, portfolio: bool = True, cash: float = 100000.0) -> ProbeSession:
        session = live_probe.create_session(
            probe_version, cash=cash, portfolio=portfolio,
        )
        created.append(session)
        return session

    yield factory
    live_probe.delete_sessions([s.id for s in created])


@pytest.fixture
def probe_session(new_session):
    return new_session()
