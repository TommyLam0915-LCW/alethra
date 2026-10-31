"""Upstream budget gate — the cap is enforced, not just reported.

Round 2026-10-31 spent 18 Kraken REST calls against a 6-call run budget because
the adapter only counted calls and never refused one. These tests pin the new
contract at every layer:

* the 7th call is refused and touches no socket,
* a refusal consumes nothing (provider counters roll back with the run counter),
* the run counter survives a restart and a second process,
* cache hits and repeated series reads do not multiply upstream calls,
* stored bars stay readable while the budget is exhausted,
* and, on PostgreSQL only, concurrent reservations cannot exceed the cap.

The cross-process and concurrency tests are deliberately separate: a SQLite
file proves a counter is shared across processes, but only PostgreSQL proves
the reservation is atomic under contention.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from tests.conftest import TEST_ENGINE, TestSession

from app.models.rate_limit import RateLimitCounter
from app.providers.crypto_registry import reset_crypto_provider, set_crypto_provider
from app.providers.kraken_provider import (
    KrakenBudgetExceededError,
    KrakenProvider,
    KrakenUnavailableError,
)
from app.services.rate_limit import cleanup_old_counters
from app.services.upstream_budget import (
    BudgetLimits,
    PROVIDER_DAY_CATEGORY,
    PROVIDER_HOUR_CATEGORY,
    UpstreamBudgetGate,
    VERIFY_CATEGORY,
)

BASE = "/api/v1/market/crypto"
INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"
NOW = datetime(2026, 10, 30, 12, 0, 30, tzinfo=timezone.utc)
LAST_CLOSED = datetime(2026, 10, 30, 11, 59, 0, tzinfo=timezone.utc)

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _gate(max_hour=100, max_day=1000, run_id="", run_max=0, session_factory=TestSession):
    return UpstreamBudgetGate(
        session_factory=session_factory,
        limits=BudgetLimits(
            max_per_hour=max_hour,
            max_per_day=max_day,
            verify_run_id=run_id,
            verify_max_requests=run_max,
        ),
    )


# -- core reservation semantics ---------------------------------------------


def test_seventh_call_is_refused_and_touches_no_counter():
    gate = _gate(max_hour=100, max_day=1000, run_id="run-seven", run_max=6)

    allowed = [gate.reserve(now=NOW) for _ in range(6)]
    assert all(r.allowed for r in allowed)
    assert [r.verify_run_used for r in allowed] == [1, 2, 3, 4, 5, 6]

    seventh = gate.reserve(now=NOW)
    assert seventh.allowed is False
    assert seventh.denied_layer == "verification_run"
    assert seventh.reason == "verification_run_exhausted"
    # Refusal is reported with the real usage, not zeros.
    assert seventh.verify_run_used == 6
    assert seventh.provider_hour_used == 6
    assert seventh.provider_day_used == 6

    snapshot = gate.snapshot(now=NOW)
    assert snapshot.verify_run_used == 6
    assert snapshot.provider_hour_used == 6


def test_provider_budget_is_separate_from_the_run_budget():
    """A generous venue allowance does not grant more calls than the run cap."""
    gate = _gate(max_hour=1000, max_day=1000, run_id="run-separate", run_max=2)
    assert gate.reserve(now=NOW).allowed
    assert gate.reserve(now=NOW).allowed
    denied = gate.reserve(now=NOW)
    assert denied.allowed is False
    assert denied.denied_layer == "verification_run"
    # Provider counters show two calls spent, well under their own limit.
    assert denied.provider_hour_used == 2
    assert denied.verify_run_used == 2


def test_provider_hour_limit_refuses_without_a_run_cap():
    gate = _gate(max_hour=2, max_day=10)
    assert gate.reserve(now=NOW).allowed
    assert gate.reserve(now=NOW).allowed
    denied = gate.reserve(now=NOW)
    assert denied.allowed is False
    assert denied.denied_layer == "provider_hour"
    assert denied.provider_hour_used == 2


def test_denial_rolls_back_the_other_counters():
    """If the day budget refuses, the hour increment must not be kept."""
    gate = _gate(max_hour=5, max_day=2)
    assert gate.reserve(now=NOW).allowed
    assert gate.reserve(now=NOW).allowed

    denied = gate.reserve(now=NOW)
    assert denied.allowed is False
    assert denied.denied_layer == "provider_day"

    snapshot = gate.snapshot(now=NOW)
    assert snapshot.provider_hour_used == 2  # not 3
    assert snapshot.provider_day_used == 2


def test_new_hour_window_does_not_reset_the_run_counter():
    gate = _gate(max_hour=100, max_day=1000, run_id="run-clock", run_max=3)
    for _ in range(3):
        assert gate.reserve(now=NOW).allowed

    later = NOW + timedelta(hours=2, minutes=5)
    denied = gate.reserve(now=later)
    assert denied.allowed is False
    assert denied.denied_layer == "verification_run"
    # The hourly window rolled over; the named run did not.
    assert denied.provider_hour_used == 0
    assert denied.verify_run_used == 3


def test_counter_cleanup_never_deletes_the_verification_run():
    gate = _gate(run_id="run-cleanup", run_max=6)
    assert gate.reserve(now=NOW).allowed

    db = TestSession()
    try:
        deleted = cleanup_old_counters(db, max_age_hours=1)
    finally:
        db.close()

    # Other stale counters may be pruned; the sentinel-window run row stays.
    assert deleted >= 0
    assert gate.snapshot(now=NOW).verify_run_used == 1
    db = TestSession()
    try:
        row = (
            db.query(RateLimitCounter)
            .filter(RateLimitCounter.route_category == VERIFY_CATEGORY)
            .one()
        )
        assert row.identity_key == "upstream:kraken_verify:run-cleanup"
    finally:
        db.close()


def test_no_run_cap_means_no_run_counter_row():
    gate = _gate(max_hour=10, max_day=10)
    assert gate.reserve(now=NOW).allowed
    db = TestSession()
    try:
        assert db.query(RateLimitCounter).filter(
            RateLimitCounter.route_category == VERIFY_CATEGORY
        ).count() == 0
        hour = db.query(RateLimitCounter).filter(
            RateLimitCounter.route_category == PROVIDER_HOUR_CATEGORY
        ).one()
        day = db.query(RateLimitCounter).filter(
            RateLimitCounter.route_category == PROVIDER_DAY_CATEGORY
        ).one()
        assert hour.request_count == 1
        assert day.request_count == 1
    finally:
        db.close()


# -- provider wiring --------------------------------------------------------


class FakeTransport:
    def __init__(self, routes):
        self._routes = routes
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        entry = self._routes[path]
        if callable(entry):
            return entry(params)
        if isinstance(entry, Exception):
            raise entry
        return entry


def _row(ts, close="100.0", volume="0.5"):
    c = Decimal(close)
    timestamp = int(ts.timestamp()) if isinstance(ts, datetime) else int(ts)
    return [timestamp, str(c), str(c + Decimal("1")), str(c - Decimal("1")), str(c), str(c), volume, 4]


def _ohlc_rows(count=4):
    start = LAST_CLOSED - timedelta(minutes=count - 1)
    return [_row(start + timedelta(minutes=i)) for i in range(count)]


def _ohlc_payload(rows):
    return {"error": [], "result": {"XXBTZUSD": rows, "last": "unused"}}


def test_refused_call_opens_no_socket():
    gate = _gate(max_hour=100, max_day=1000, run_id="run-socket", run_max=1)
    transport = FakeTransport({"/0/public/OHLC": _ohlc_payload(_ohlc_rows())})
    provider = KrakenProvider(
        transport=transport,
        clock=lambda: NOW,
        min_request_interval=0,
        budget_gate=gate,
    )

    provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert len(transport.calls) == 1
    assert provider.get_status()["request_count"] == 1

    with pytest.raises(KrakenBudgetExceededError) as excinfo:
        provider.get_candle_series(INSTRUMENT, interval_minutes=5)

    assert excinfo.value.denied_layer == "verification_run"
    assert len(transport.calls) == 1  # the refused call never reached transport
    status = provider.get_status()
    assert status["request_count"] == 1
    assert status["budget_denials"] == 1
    assert status["budget"]["verify_run_used"] == 1


def test_retry_after_failure_consumes_another_reservation():
    calls = {"n": 0}

    def _fail_once(params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("network down")
        return _ohlc_payload(_ohlc_rows())

    gate = _gate(max_hour=100, max_day=1000, run_id="run-retry", run_max=2)
    transport = FakeTransport({"/0/public/OHLC": _fail_once})
    provider = KrakenProvider(
        transport=transport, clock=lambda: NOW, min_request_interval=0, budget_gate=gate
    )

    with pytest.raises(KrakenUnavailableError):
        provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert gate.snapshot(now=NOW).verify_run_used == 1

    provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert gate.snapshot(now=NOW).verify_run_used == 2
    assert provider.get_status()["request_count"] == 2

    # The third attempt is a budget refusal, not another socket attempt.
    provider.clear_cache()
    with pytest.raises(KrakenBudgetExceededError):
        provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert len(transport.calls) == 2


def test_repeated_reads_with_different_limits_share_one_upstream_call():
    """Paging the same series client-side must not multiply venue requests."""
    gate = _gate(max_hour=100, max_day=1000, run_id="run-paging", run_max=6)
    transport = FakeTransport({"/0/public/OHLC": _ohlc_payload(_ohlc_rows(count=6))})
    provider = KrakenProvider(
        transport=transport, clock=lambda: NOW, min_request_interval=0, budget_gate=gate
    )

    provider.get_candle_series(INSTRUMENT, interval_minutes=1, limit=2)
    provider.get_candle_series(INSTRUMENT, interval_minutes=1, limit=4)
    provider.get_candle_series(INSTRUMENT, interval_minutes=1, limit=6)

    assert len(transport.calls) == 1
    assert gate.snapshot(now=NOW).verify_run_used == 1


def test_pair_metadata_is_cached_between_reads():
    pair_payload = {
        "error": [],
        "result": {
            "XXBTZUSD": {
                "altname": "XBTUSD",
                "wsname": "XBT/USD",
                "pair_decimals": 1,
                "lot_decimals": 8,
                "tick_size": "0.1",
                "ordermin": "0.0001",
                "costmin": "0.5",
                "status": "online",
            }
        },
    }
    gate = _gate(max_hour=100, max_day=1000, run_id="run-meta", run_max=6)
    transport = FakeTransport({"/0/public/AssetPairs": pair_payload})
    provider = KrakenProvider(
        transport=transport, clock=lambda: NOW, min_request_interval=0, budget_gate=gate
    )

    first = provider.get_trading_specs(INSTRUMENT)
    second = provider.get_trading_specs(INSTRUMENT)

    assert first["min_quantity"] == Decimal("0.0001")
    assert second["price_precision"] == first["price_precision"]
    assert len(transport.calls) == 1
    assert gate.snapshot(now=NOW).verify_run_used == 1


# -- HTTP layer -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_provider():
    reset_crypto_provider()
    yield
    reset_crypto_provider()


def test_budget_exhausted_is_503_and_stored_bars_stay_readable(client, admin_headers):
    gate = _gate(max_hour=100, max_day=1000, run_id="run-http", run_max=1)
    transport = FakeTransport({"/0/public/OHLC": _ohlc_payload(_ohlc_rows())})
    provider = KrakenProvider(
        transport=transport, clock=lambda: NOW, min_request_interval=0, budget_gate=gate
    )
    set_crypto_provider(provider)

    snapshot = client.post(
        f"{BASE}/snapshot",
        params={"instrument_id": INSTRUMENT, "interval": "1m"},
        headers=admin_headers,
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "stored"

    # The next live read is refused before any socket is opened.
    provider.clear_cache()
    denied = client.get(f"{BASE}/series", params={"instrument_id": INSTRUMENT, "interval": "1m"})
    assert denied.status_code == 503
    detail = denied.json()["detail"]
    assert detail["reason"] == "budget_exhausted"
    assert detail["denied_layer"] == "verification_run"
    assert detail["retry_safe"] is False
    assert detail["stored_available"] == "/market/crypto/stored"
    assert len(transport.calls) == 1

    # Data already stored remains served — the refusal is about new calls.
    stored = client.get(f"{BASE}/stored", params={"instrument_id": INSTRUMENT})
    assert stored.status_code == 200
    assert stored.json()["count"] == 4


# -- cross-process (SQLite file proves sharing, not contention) -------------


_WORKER = r"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.rate_limit import RateLimitCounter
from app.services.upstream_budget import BudgetLimits, UpstreamBudgetGate

engine = create_engine(os.environ["DATABASE_URL"])
RateLimitCounter.__table__.create(engine, checkfirst=True)
gate = UpstreamBudgetGate(
    session_factory=sessionmaker(bind=engine),
    limits=BudgetLimits(1000, 1000, "run-cross-process", 6),
)
allowed = sum(1 for _ in range(3) if gate.reserve().allowed)
print(f"{allowed} {gate.snapshot().verify_run_used}")
"""


def test_a_restart_and_a_second_process_share_one_run_budget(tmp_path):
    db_path = tmp_path / f"budget-{uuid.uuid4().hex}.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_path}",
        "PYTHONPATH": str(BACKEND_DIR),
    }

    results = []
    for _ in range(3):
        proc = subprocess.run(
            [sys.executable, "-c", _WORKER],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        allowed, used = proc.stdout.strip().split()
        results.append((int(allowed), int(used)))

    # Each process is a fresh interpreter with the same run id: the first two
    # spend the 6-call allowance, the third gets nothing.
    assert results == [(3, 3), (3, 6), (0, 6)]


# -- PostgreSQL contention --------------------------------------------------


@pytest.mark.skipif(
    TEST_ENGINE.dialect.name != "postgresql",
    reason="atomicity under contention is a PostgreSQL claim; run on a disposable PG",
)
def test_concurrent_reservations_never_exceed_the_cap_on_postgres():
    run_id = f"pg-race-{uuid.uuid4().hex}"
    local_factory = sessionmaker(bind=TEST_ENGINE)
    gate = _gate(max_hour=1000, max_day=1000, run_id=run_id, run_max=6, session_factory=local_factory)

    def _reserve(_):
        return gate.reserve().allowed

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_reserve, range(16)))

    assert sum(results) == 6
    assert gate.snapshot().verify_run_used == 6
    # Provider counters also stopped at 6: each worker holds its own connection.
    assert gate.snapshot().provider_hour_used == 6
