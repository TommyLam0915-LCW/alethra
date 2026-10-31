"""Hard, shared budget for every upstream HTTP call the crypto lane makes.

Why this module exists
----------------------
Round 2026-10-31 spent **18** Kraken REST calls while the run was capped at 6.
The calls were real and the data was valid, but the budget limit was advisory:
``KrakenProvider`` only kept a per-process ``_request_count`` for reporting and
never refused a call. Once the cap was exceeded, nothing in the code could stop
the next one.

This gate makes the cap load-bearing. Every OHLC / Ticker / Time / AssetPairs
read — plus retries, backfills and UI-triggered reads, because they all funnel
through ``KrakenProvider._request`` — must reserve budget *before* the socket
opens. Two independent budgets are enforced:

* ``provider``  — the venue's long-term allowance (hourly and daily). This is
  what protects the account over time.
* ``verification_run`` — a named, hard per-run cap (the 6-call budget). It is
  keyed by an operator-set run id, so restarting the process, opening another
  tab or deploying another worker does **not** mint a fresh allowance. Clearing
  or renaming the run id is a deliberate operator action, never automatic.

The counter lives in the shared ``rate_limit_counters`` table, so every process
writing to the same database sees one budget. Reservation is a single atomic
conditional upsert per counter; the three counters are reserved in one
transaction, so a denial at any layer rolls back the others and a refused call
consumes nothing.

Nothing here fetches data. A denied reservation simply means the caller must
not open a socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import text

from app.config import get_settings

# Separate identities per budget so an operator can read each one without
# inferring it from a shared category string.
PROVIDER_IDENTITY = "upstream:kraken_rest"
PROVIDER_HOUR_CATEGORY = "kraken_upstream_hour"
PROVIDER_DAY_CATEGORY = "kraken_upstream_day"
VERIFY_IDENTITY_PREFIX = "upstream:kraken_verify:"
VERIFY_CATEGORY = "kraken_verify_run"
# A fixed sentinel window, so the run counter never rolls over with the clock.
# It only moves when an operator changes the run id.
_RUN_SENTINEL = datetime(1970, 1, 1)

# Atomic reserve: increment only while under the limit, and tell the caller
# whether the increment happened. ``ON CONFLICT ... DO UPDATE ... WHERE`` is
# supported by both PostgreSQL and SQLite (>= 3.35), so the same statement is
# the concurrency-safe gate in production and in tests.
_RESERVE_SQL = text(
    """
    INSERT INTO rate_limit_counters
        (id, identity_key, route_category, window_start, request_count, created_at, updated_at)
    VALUES
        (:id, :identity_key, :route_category, :window_start, 1, :now, :now)
    ON CONFLICT (identity_key, route_category, window_start)
    DO UPDATE SET
        request_count = rate_limit_counters.request_count + 1,
        updated_at = :now
    WHERE rate_limit_counters.request_count < :limit
    RETURNING request_count
    """
)


def _naive_utc(value: datetime) -> datetime:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class BudgetLimits:
    max_per_hour: int
    max_per_day: int
    verify_run_id: str = ""
    verify_max_requests: int = 0

    @property
    def run_cap_enabled(self) -> bool:
        return bool(self.verify_run_id) and self.verify_max_requests > 0


@dataclass(frozen=True)
class Reservation:
    """Outcome of one attempted reservation.

    ``allowed`` false means the caller must not make the upstream call.
    ``denied_layer`` names which budget refused it so the API and logs can say
    *why* without turning the refusal into a generic outage.
    """

    allowed: bool
    denied_layer: Optional[str]
    reason: str
    provider_hour_used: int
    provider_day_used: int
    verify_run_used: int
    verify_run_limit: int

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "denied_layer": self.denied_layer,
            "reason": self.reason,
            "provider_hour_used": self.provider_hour_used,
            "provider_day_used": self.provider_day_used,
            "verify_run_used": self.verify_run_used,
            "verify_run_limit": self.verify_run_limit,
        }


@dataclass(frozen=True)
class BudgetSnapshot:
    provider_hour_used: int
    provider_day_used: int
    verify_run_used: int
    max_per_hour: int
    max_per_day: int
    verify_run_id: str
    verify_run_limit: int

    def as_dict(self) -> dict:
        return {
            "provider_hour_used": self.provider_hour_used,
            "provider_day_used": self.provider_day_used,
            "provider_hour_limit": self.max_per_hour,
            "provider_day_limit": self.max_per_day,
            "verify_run_id": self.verify_run_id or None,
            "verify_run_used": self.verify_run_used,
            "verify_run_limit": self.verify_run_limit,
            "scope": "database",
        }


class UpstreamBudgetGate:
    """Reserve-before-send budget enforcement over the shared counter table."""

    def __init__(self, session_factory: Callable[[], object], limits: BudgetLimits):
        if limits.max_per_hour < 1 or limits.max_per_day < 1:
            raise ValueError("upstream budget limits must be positive")
        self._session_factory = session_factory
        self._limits = limits

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    def _try_consume(
        self,
        session,
        *,
        identity: str,
        category: str,
        window_start: datetime,
        limit: int,
        now: datetime,
    ) -> Optional[int]:
        import uuid

        result = session.execute(
            _RESERVE_SQL,
            {
                "id": str(uuid.uuid4()),
                "identity_key": identity,
                "route_category": category,
                "window_start": window_start,
                "limit": limit,
                "now": now,
            },
        )
        row = result.fetchone()
        return int(row[0]) if row is not None else None

    def reserve(self, *, now: Optional[datetime] = None) -> Reservation:
        """Atomically reserve one upstream call, or refuse it.

        All three counters share one transaction: if any layer is exhausted the
        whole reservation is rolled back, so a refused call spends nothing.
        """
        current = _naive_utc(now or datetime.now(timezone.utc))
        hour_start = current.replace(minute=0, second=0, microsecond=0)
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)

        session = self._session_factory()
        try:
            hour_used = self._try_consume(
                session,
                identity=PROVIDER_IDENTITY,
                category=PROVIDER_HOUR_CATEGORY,
                window_start=hour_start,
                limit=self._limits.max_per_hour,
                now=current,
            )
            if hour_used is None:
                session.rollback()
                # Release the connection *before* reading usage, so concurrent
                # reservations can never each hold one connection while
                # waiting for a second one.
                session.close()
                return self._denied("provider_hour", now=current)

            day_used = self._try_consume(
                session,
                identity=PROVIDER_IDENTITY,
                category=PROVIDER_DAY_CATEGORY,
                window_start=day_start,
                limit=self._limits.max_per_day,
                now=current,
            )
            if day_used is None:
                session.rollback()
                session.close()
                return self._denied("provider_day", now=current)

            run_used = 0
            if self._limits.run_cap_enabled:
                consumed = self._try_consume(
                    session,
                    identity=VERIFY_IDENTITY_PREFIX + self._limits.verify_run_id,
                    category=VERIFY_CATEGORY,
                    window_start=_RUN_SENTINEL,
                    limit=self._limits.verify_max_requests,
                    now=current,
                )
                if consumed is None:
                    session.rollback()
                    session.close()
                    return self._denied("verification_run", now=current)
                run_used = consumed

            session.commit()
            return Reservation(
                allowed=True,
                denied_layer=None,
                reason="reserved",
                provider_hour_used=hour_used,
                provider_day_used=day_used,
                verify_run_used=run_used,
                verify_run_limit=self._limits.verify_max_requests
                if self._limits.run_cap_enabled
                else 0,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _denied(self, layer: str, *, now: Optional[datetime] = None) -> Reservation:
        # The reservation was rolled back, so report the *actual* usage rather
        # than zeros: the caller needs to know how close every layer is, not
        # that nothing has ever been spent. Read with the same clock the
        # reservation used, so a test or replay clock reports its own windows.
        try:
            current = self.snapshot(now=now)
            hour_used = current.provider_hour_used
            day_used = current.provider_day_used
            run_used = current.verify_run_used
        except Exception:
            hour_used = day_used = run_used = 0
        return Reservation(
            allowed=False,
            denied_layer=layer,
            reason=f"{layer}_exhausted",
            provider_hour_used=hour_used,
            provider_day_used=day_used,
            verify_run_used=run_used,
            verify_run_limit=self._limits.verify_max_requests
            if self._limits.run_cap_enabled
            else 0,
        )

    def snapshot(self, *, now: Optional[datetime] = None) -> BudgetSnapshot:
        """Read current usage without consuming anything."""
        current = _naive_utc(now or datetime.now(timezone.utc))
        hour_start = current.replace(minute=0, second=0, microsecond=0)
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)

        session = self._session_factory()
        try:
            hour_used = self._read(session, PROVIDER_IDENTITY, PROVIDER_HOUR_CATEGORY, hour_start)
            day_used = self._read(session, PROVIDER_IDENTITY, PROVIDER_DAY_CATEGORY, day_start)
            run_used = 0
            if self._limits.run_cap_enabled:
                run_used = self._read(
                    session,
                    VERIFY_IDENTITY_PREFIX + self._limits.verify_run_id,
                    VERIFY_CATEGORY,
                    _RUN_SENTINEL,
                )
        finally:
            session.close()
        return BudgetSnapshot(
            provider_hour_used=hour_used,
            provider_day_used=day_used,
            verify_run_used=run_used,
            max_per_hour=self._limits.max_per_hour,
            max_per_day=self._limits.max_per_day,
            verify_run_id=self._limits.verify_run_id,
            verify_run_limit=self._limits.verify_max_requests,
        )

    @staticmethod
    def _read(session, identity: str, category: str, window_start: datetime) -> int:
        row = session.execute(
            text(
                """
                SELECT request_count FROM rate_limit_counters
                WHERE identity_key = :identity_key
                  AND route_category = :route_category
                  AND window_start = :window_start
                """
            ),
            {
                "identity_key": identity,
                "route_category": category,
                "window_start": window_start,
            },
        ).fetchone()
        return int(row[0]) if row is not None else 0


class AllowAllBudgetGate:
    """Explicit no-op gate for offline adapter tests that do not model budget.

    It is never the app default: ``crypto_registry.get_crypto_provider`` injects
    the real database gate. Tests that exercise parsing or candle integrity use
    this so they do not open a database.
    """

    class _Reservation(Reservation):
        pass

    def reserve(self, *, now: Optional[datetime] = None) -> Reservation:
        return Reservation(
            allowed=True,
            denied_layer=None,
            reason="allow_all",
            provider_hour_used=0,
            provider_day_used=0,
            verify_run_used=0,
            verify_run_limit=0,
        )

    def snapshot(self, *, now: Optional[datetime] = None) -> BudgetSnapshot:
        return BudgetSnapshot(0, 0, 0, 0, 0, "", 0)


_gate: Optional[UpstreamBudgetGate] = None


def get_upstream_budget_gate() -> UpstreamBudgetGate:
    """Process-wide gate bound to the shared database and current settings."""
    global _gate
    if _gate is None:
        from app.database import SessionLocal

        settings = get_settings()
        _gate = UpstreamBudgetGate(
            session_factory=SessionLocal,
            limits=BudgetLimits(
                max_per_hour=settings.crypto_max_requests_per_hour,
                max_per_day=settings.crypto_max_requests_per_day,
                verify_run_id=settings.crypto_verify_run_id.strip(),
                verify_max_requests=settings.crypto_verify_max_requests,
            ),
        )
    return _gate


def reset_upstream_budget_gate() -> None:
    """Clear the cached gate (used by tests)."""
    global _gate
    _gate = None
