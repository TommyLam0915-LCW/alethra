"""Kraken public market data — read-only.

Only the public, unauthenticated endpoints are used: ``Time``, ``OHLC``,
``Ticker`` and ``AssetPairs``. This adapter holds no key, submits no order and
has no code path that could place one. It answers "what did this venue print"
and nothing else.

Two rules shape the implementation:

1. A price never exists without its venue and pair. Symbol lookup goes through
   the declared crypto catalog; an unknown pair raises instead of being passed
   through to the exchange on a guess.
2. There is no fallback to demo or synthetic bars. If Kraken cannot be reached
   the caller gets :class:`KrakenUnavailableError`, because a plausible number
   from another source is worse than an explicit outage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from app.data.crypto_instruments import (
    CRYPTO_INSTRUMENTS,
    get_crypto_instrument,
    get_kraken_pair,
)
from app.providers.data_provider import AssetInfo, DataProvider, OHLCVBar
from app.providers.instruments import Instrument, Market, parse_crypto_instrument_id
from app.services.crypto_series import CandleSeries, RawCandle, build_series
from app.services.upstream_budget import (
    AllowAllBudgetGate,
    BudgetSnapshot,
    Reservation,
)

logger = logging.getLogger(__name__)

KRAKEN_API_BASE = "https://api.kraken.com"
# Kraken accepts 1/5/15/30/60/240/1440/10080/21600 minutes. Weekly and longer
# buckets are not used by the research lane, so they are refused rather than
# silently mapped to a different series.
KRAKEN_INTERVALS: Tuple[int, ...] = (1, 5, 15, 30, 60, 240, 1440)
# Verified against api.kraken.com on 2026-11-10 with BTC/USD: one OHLC request
# returns at most 720 CLOSED of the most recent buckets for the interval (daily:
# ~720 days back; hourly: ~720 hours back) and rides the still-forming bucket
# along, so a full shelf answers with 721 rows. ``last`` names the newest
# CLOSED bucket, not the newest row returned. An older ``since`` returns the
# identical newest shelf instead of deeper history, and feeding ``last`` back as
# ``since`` moves toward the newest bars. ``since``/``last`` are therefore a
# polling pair, not a backward history cursor, and no page loop can widen this
# shelf.
MAX_OHLC_ROWS = 720
CACHE_TTL_SECONDS = 2.0
# Pair metadata changes only when Kraken re-lists a pair, so refetching it on
# every page load would spend budget on a value that has not moved. Prices keep
# the short TTL; identity metadata does not.
METADATA_CACHE_TTL_SECONDS = 3600.0
MIN_REQUEST_INTERVAL_SECONDS = 1.05


class KrakenError(Exception):
    """Base class for Kraken adapter failures."""


class KrakenUnavailableError(KrakenError):
    """The venue could not be reached; no data was produced."""


class KrakenApiError(KrakenError):
    """Kraken answered, but with an error or a payload we cannot read."""


class KrakenRateLimitError(KrakenError):
    """Kraken refused the request because we exceeded its public call budget."""


class KrakenBudgetExceededError(KrakenError):
    """Our own budget gate refused the call before a socket was opened.

    Distinct from :class:`KrakenRateLimitError`: the venue never saw the call.
    The caller should stop issuing requests in this run and read stored data
    instead; retrying cannot help until the window or run id changes.
    """

    def __init__(self, reservation: Reservation):
        super().__init__(reservation.reason)
        self.reservation = reservation

    @property
    def denied_layer(self) -> Optional[str]:
        return self.reservation.denied_layer


_METADATA_PATHS = frozenset({"/0/public/AssetPairs", "/0/public/Time"})


class KrakenTransport(Protocol):
    """Minimal transport so tests can run without a socket."""

    def get(self, path: str, params: Dict[str, str]) -> Any:  # pragma: no cover
        ...


class HttpxKrakenTransport:
    def __init__(self, base_url: str = KRAKEN_API_BASE, timeout: float = 10.0):
        import httpx

        self._httpx = httpx
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": "Alethra-Research/1.0 (read-only market data)"},
        )

    def get(self, path: str, params: Dict[str, str]) -> Any:
        try:
            response = self._client.get(path, params=params)
        except self._httpx.HTTPError as exc:
            raise KrakenUnavailableError(f"Kraken request failed: {type(exc).__name__}") from exc

        if response.status_code >= 500:
            raise KrakenUnavailableError(f"Kraken returned HTTP {response.status_code}")
        if response.status_code >= 400:
            raise KrakenApiError(f"Kraken returned HTTP {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            raise KrakenApiError("Kraken returned a non-JSON body") from exc


def _to_decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise KrakenApiError(f"Kraken field {field!r} is not numeric: {value!r}") from exc
    if not parsed.is_finite():
        raise KrakenApiError(f"Kraken field {field!r} is not finite: {value!r}")
    return parsed


class KrakenProvider(DataProvider):
    """Read-only adapter for Kraken's public spot market data."""

    def __init__(
        self,
        transport: Optional[KrakenTransport] = None,
        clock: Optional[Any] = None,
        min_request_interval: float = MIN_REQUEST_INTERVAL_SECONDS,
        budget_gate: Optional[Any] = None,
        metadata_cache_ttl: float = METADATA_CACHE_TTL_SECONDS,
    ):
        self._transport: KrakenTransport = transport or HttpxKrakenTransport()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._min_request_interval = min_request_interval
        # Default to allow-all so a bare adapter (offline tests) never needs a
        # database. The application path injects the shared database gate in
        # ``crypto_registry.get_crypto_provider``; that injection is what makes
        # the cap real, so this default must not be relied on there.
        self._budget_gate = budget_gate if budget_gate is not None else AllowAllBudgetGate()
        self._metadata_cache_ttl = metadata_cache_ttl
        self._monotonic = time.monotonic
        self._sleep = time.sleep
        self._last_request_at: float = 0.0
        self._cache: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], Tuple[float, Any]] = {}
        self._request_count = 0
        self._budget_denials = 0
        self._last_budget: Optional[dict] = None
        self._last_reservation: Optional[Reservation] = None
        self._consecutive_failures = 0
        self._last_error: Optional[str] = None
        self._last_error_at: Optional[str] = None
        self._last_success_at: Optional[str] = None

    # -- identity -----------------------------------------------------------

    @property
    def provider_id(self) -> str:
        return "kraken"

    @property
    def provider_name(self) -> str:
        return "Kraken"

    @property
    def feed_mode(self) -> str:
        return "live" if self.is_available() else "disconnected"

    def is_available(self) -> bool:
        """True only after this process has actually reached Kraken.

        Being configured is not being reachable: an adapter that has never
        received a response must not claim availability.
        """
        return self._last_success_at is not None and self._consecutive_failures < 3

    # -- plumbing -----------------------------------------------------------

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def _throttle(self) -> None:
        if self._min_request_interval <= 0:
            return
        elapsed = self._monotonic() - self._last_request_at
        remaining = self._min_request_interval - elapsed
        if self._last_request_at and remaining > 0:
            self._sleep(remaining)

    def _cache_ttl(self, path: str) -> float:
        return self._metadata_cache_ttl if path in _METADATA_PATHS else CACHE_TTL_SECONDS

    def _request(self, path: str, params: Dict[str, str]) -> Any:
        cache_key = (path, tuple(sorted(params.items())))
        now = self._monotonic()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self._cache_ttl(path):
            return cached[1]

        # Budget is reserved *before* the throttle and the socket. A refusal
        # here means the venue is never contacted and the call consumes nothing;
        # retries and UI-triggered reads reach this same gate.
        reservation = self._budget_gate.reserve()
        self._last_reservation = reservation
        self._last_budget = reservation.as_dict()
        if not reservation.allowed:
            self._budget_denials += 1
            logger.warning(
                "Upstream budget refused %s: %s (%s)",
                path,
                reservation.reason,
                reservation.denied_layer,
            )
            raise KrakenBudgetExceededError(reservation)

        self._throttle()
        self._last_request_at = self._monotonic()
        self._request_count += 1

        try:
            payload = self._transport.get(path, params)
        except KrakenError as exc:
            self._record_failure(f"{path}: {exc}")
            raise
        except Exception as exc:  # transport implementations vary
            self._record_failure(f"{path}: {type(exc).__name__}")
            raise KrakenUnavailableError(f"Kraken request failed: {type(exc).__name__}") from exc

        if not isinstance(payload, dict):
            self._record_failure(f"{path}: unexpected payload type {type(payload).__name__}")
            raise KrakenApiError("Kraken returned an unexpected payload")

        errors = payload.get("error") or []
        if errors:
            message = "; ".join(str(e) for e in errors)[:300]
            if "rate limit" in message.lower() or "too many" in message.lower():
                self._record_failure(f"{path}: rate limited")
                raise KrakenRateLimitError(message)
            self._record_failure(f"{path}: {message}")
            raise KrakenApiError(message)

        self._record_success()
        self._cache[cache_key] = (self._monotonic(), payload)
        return payload

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._last_success_at = self._now().isoformat()

    def _record_failure(self, message: str) -> None:
        self._consecutive_failures += 1
        self._last_error = message
        self._last_error_at = self._now().isoformat()
        logger.warning("Kraken request failed: %s", message)

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- catalog ------------------------------------------------------------

    def list_assets(self) -> List[AssetInfo]:
        return [
            AssetInfo.from_instrument(
                instrument,
                description=(
                    f"{instrument.name} — {instrument.exchange.value} spot, "
                    f"quoted in {instrument.quote_asset}"
                ),
            )
            for instrument in CRYPTO_INSTRUMENTS
        ]

    def get_instrument(self, instrument_id: str) -> Optional[AssetInfo]:
        instrument = get_crypto_instrument(instrument_id)
        if instrument is None:
            return None
        return AssetInfo.from_instrument(
            instrument,
            description=f"{instrument.name} — {instrument.exchange.value} spot",
        )

    def supports_instrument_id(self) -> bool:
        return True

    def resolve_instrument(self, instrument_or_symbol: Any) -> str:
        instrument_id = (
            instrument_or_symbol.instrument_id
            if isinstance(instrument_or_symbol, Instrument)
            else str(instrument_or_symbol)
        )
        mapping = get_kraken_pair(instrument_id)
        if mapping is None:
            declared = sorted(
                m.provider_symbol
                for m in (get_kraken_pair(i.instrument_id) for i in CRYPTO_INSTRUMENTS)
                if m is not None
            )
            raise KrakenError(
                f"Kraken does not serve {instrument_id!r}; declared pairs: {declared}"
            )
        return mapping.provider_symbol

    def _require_instrument(self, instrument_id: str) -> Instrument:
        instrument = get_crypto_instrument(instrument_id)
        if instrument is None:
            raise KrakenError(
                f"{instrument_id!r} is not a declared crypto instrument; add it to "
                "app/data/crypto_instruments.py with its venue and quote asset first"
            )
        exchange, base, quote = parse_crypto_instrument_id(instrument.instrument_id)
        if exchange != instrument.exchange or base != instrument.base_asset or quote != instrument.quote_asset:
            raise KrakenError(f"Instrument identity for {instrument_id!r} is internally inconsistent")
        if instrument.market != Market.CRYPTO:
            raise KrakenError(f"{instrument_id!r} is not a crypto market instrument")
        return instrument

    # -- candles ------------------------------------------------------------

    @staticmethod
    def _result_key(payload: Dict[str, Any], expected: Sequence[str]) -> Tuple[str, Optional[str]]:
        """Extract the pair key and the ``last`` field from a Kraken OHLC response.

        Returns (result_key, last_cursor). ``last`` is a newest-side polling
        identifier: feeding it back as ``since`` was observed to return bars
        *newer* than the previous page, not older ones. Kraken documents no
        special values for it, so callers must not infer meaning from any
        particular value — only from whether it moves forward.
        """
        result = payload.get("result") or {}
        keys = [k for k in result.keys() if k != "last"]
        if len(keys) != 1:
            raise KrakenApiError(f"Expected exactly one pair in the Kraken response, got {keys}")
        if keys[0] not in expected:
            raise KrakenApiError(
                f"Kraken answered for {keys[0]!r}, expected one of {list(expected)}"
            )
        last_cursor = result.get("last")
        return keys[0], last_cursor

    def get_candle_series(
        self,
        instrument_id: str,
        interval_minutes: int = 1,
        limit: Optional[int] = None,
    ) -> CandleSeries:
        """Completed + forming candles for one declared pair.

        The forming candle is returned separately and never in
        ``series.completed``; strategy input must read ``bars_for_strategy``.
        """
        instrument = self._require_instrument(instrument_id)
        if interval_minutes not in KRAKEN_INTERVALS:
            raise KrakenError(
                f"Unsupported interval {interval_minutes}m for Kraken; "
                f"choose one of {list(KRAKEN_INTERVALS)}"
            )
        if limit is not None and not (1 <= limit <= MAX_OHLC_ROWS):
            raise KrakenError(f"limit must be between 1 and {MAX_OHLC_ROWS}, got {limit}")

        mapping = get_kraken_pair(instrument.instrument_id)
        assert mapping is not None

        payload = self._request(
            "/0/public/OHLC",
            {"pair": mapping.provider_symbol, "interval": str(interval_minutes)},
        )
        key, _last_cursor = self._result_key(
            payload,
            (mapping.metadata.get("result_key", ""), mapping.metadata.get("result_key_alt", "")),
        )

        rows = payload["result"][key]
        raw_rows = [self._parse_row(row, instrument_id) for row in rows]

        if limit is not None:
            # One extra row keeps the forming candle visible after trimming.
            raw_rows = raw_rows[-(limit + 1):]

        return build_series(
            instrument_id=instrument_id,
            interval_minutes=interval_minutes,
            rows=raw_rows,
            now=self._now(),
        )

    @staticmethod
    def _merge_page(
        seen: Sequence[RawCandle],
        incoming: Sequence[RawCandle],
        instrument_id: str,
        *,
        not_before: Optional[datetime] = None,
        forming_from: Optional[datetime] = None,
    ) -> List[RawCandle]:
        """Fold one page into the rows already collected, refusing regressions.

        Kraken's ``last`` names the newest *closed* bucket, yet the same response
        also carries the bucket still being formed. The next page therefore
        legitimately re-serves a row older than the newest row in hand — bounding
        on ``seen[-1]`` refused every live two-page harvest (observed against
        api.kraken.com on 2026-11-10). What a real regression looks like is a row
        older than the ``since`` the page answered to, or a page that decreases
        within itself.

        A repeated *closed* bucket with different content is refused: picking
        either side would silently decide which price the ledger trusts. A bucket
        at or after ``forming_from`` is exempt because it is still in progress and
        is expected to move between pages; the first read of it is kept, and
        :func:`build_series` keeps it out of ``completed`` so the ledger never
        consumes it either way.
        """
        index = {row.open_time: row for row in seen}
        fresh: List[RawCandle] = []
        within_page: Optional[datetime] = None
        for row in incoming:
            if not_before is not None and row.open_time < not_before:
                raise KrakenApiError(
                    f"Kraken page for {instrument_id} went backwards past its cursor: "
                    f"{row.open_time.isoformat()} precedes the "
                    f"{not_before.isoformat()} it was asked from"
                )
            if within_page is not None and row.open_time < within_page:
                raise KrakenApiError(
                    f"Kraken page for {instrument_id} runs backwards inside the page: "
                    f"{row.open_time.isoformat()} after {within_page.isoformat()}"
                )
            within_page = row.open_time
            known = index.get(row.open_time)
            if known is None:
                index[row.open_time] = row
                fresh.append(row)
                continue
            if forming_from is not None and row.open_time > forming_from:
                continue
            if (known.open, known.high, known.low, known.close, known.volume) != (
                row.open, row.high, row.low, row.close, row.volume
            ):
                raise KrakenApiError(
                    f"Kraken returned conflicting content for closed bar "
                    f"{row.open_time.isoformat()} of {instrument_id}: refused to pick a winner"
                )
        return fresh

    @staticmethod
    def _cursor_instant(since: str) -> Optional[datetime]:
        """Resolve a ``since`` cursor to the instant the venue was asked from."""
        try:
            value = int(since)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return datetime.fromtimestamp(value, tz=timezone.utc)

    @staticmethod
    def _cursor_moves_forward(since: str, cursor: Any) -> bool:
        """True only if the answered cursor is a strictly larger instant."""
        try:
            return int(cursor) > int(since)
        except (TypeError, ValueError) as exc:
            raise KrakenApiError(f"Kraken returned a non-numeric pagination cursor {cursor!r}") from exc

    def get_candle_series_paginated(
        self,
        instrument_id: str,
        interval_minutes: int = 1,
        target_bars: int = 2000,
        max_pages: int = 10,
    ) -> CandleSeries:
        """Collect recent bars up to the venue's shelf, refusing an undeliverable ask.

        Kraken serves at most ``MAX_OHLC_ROWS`` of the most recent buckets for an
        interval, and an older ``since`` does not deepen that shelf (verified
        against api.kraken.com on 2026-11-10). Paging can only confirm the shelf,
        never extend it, so ``target_bars`` beyond it is refused rather than
        answered with a shorter series presented as the requested history.

        Args:
            instrument_id: The instrument to fetch (e.g., "CRYPTO:KRAKEN:BTC-USD")
            interval_minutes: Bar interval (1, 5, 15, 30, 60, 240, or 1440)
            target_bars: Required number of historical bars
            max_pages: Safety limit on number of API calls (default 10)

        Returns:
            CandleSeries carrying at least ``target_bars`` buckets; the newest of
            them may still be forming, so ``series.completed`` can be one shorter

        Raises:
            KrakenError: the venue could not lend ``target_bars`` bars
            KrakenApiError: a page repeated a bucket with different content, or
                the cursor stopped moving forward on a full page
        """
        instrument = self._require_instrument(instrument_id)
        if interval_minutes not in KRAKEN_INTERVALS:
            raise KrakenError(
                f"Unsupported interval {interval_minutes}m for Kraken; "
                f"choose one of {list(KRAKEN_INTERVALS)}"
            )
        if target_bars < 1:
            raise KrakenError(f"target_bars must be >= 1, got {target_bars}")
        if max_pages < 1:
            raise KrakenError(f"max_pages must be >= 1, got {max_pages}")

        mapping = get_kraken_pair(instrument.instrument_id)
        assert mapping is not None
        expected_keys = (
            mapping.metadata.get("result_key", ""),
            mapping.metadata.get("result_key_alt", ""),
        )

        now = self._now()
        start_dt = now - timedelta(seconds=target_bars * interval_minutes * 60)
        since_cursor: str = str(int(start_dt.timestamp()))

        all_rows: List[RawCandle] = []
        pages_fetched = 0

        while pages_fetched < max_pages and len(all_rows) < target_bars:
            params: Dict[str, str] = {
                "pair": mapping.provider_symbol,
                "interval": str(interval_minutes),
                "since": since_cursor,
            }

            payload = self._request("/0/public/OHLC", params)
            key, last_cursor = self._result_key(payload, expected_keys)

            rows = payload["result"][key]
            if not rows:
                break

            page_rows = self._merge_page(
                all_rows,
                [self._parse_row(row, instrument_id) for row in rows],
                instrument_id,
                not_before=self._cursor_instant(since_cursor),
                forming_from=now - timedelta(minutes=interval_minutes),
            )
            all_rows.extend(page_rows)
            pages_fetched += 1
            if not page_rows:
                # The venue re-served the same shelf: there is nothing further to walk into.
                break
            if last_cursor is None or not self._cursor_moves_forward(since_cursor, last_cursor):
                break
            since_cursor = str(last_cursor)

        if len(all_rows) < target_bars:
            raise KrakenError(
                f"Kraken lends at most {MAX_OHLC_ROWS} of the most recent "
                f"{interval_minutes}m buckets; asked for {target_bars} and collected "
                f"{len(all_rows)} across {pages_fetched} page(s). A shorter series must "
                "not be presented as the requested history."
            )

        if len(all_rows) > target_bars:
            all_rows = all_rows[-target_bars:]

        return build_series(
            instrument_id=instrument_id,
            interval_minutes=interval_minutes,
            rows=all_rows,
            now=now,
        )

    def get_history_candle_series(
        self,
        instrument_id: str,
        interval_minutes: int = 1,
        start: Optional[Any] = None,
        end: Optional[Any] = None,
        max_pages: int = 10,
    ) -> CandleSeries:
        """Read the venue's history shelf for one interval, refusing overclaims.

        Verified against api.kraken.com on 2026-11-10: one OHLC request answers
        with at most ``MAX_OHLC_ROWS`` of the most recent buckets for the
        interval, and an older ``since`` returns the same shelf instead of
        deeper history. This method therefore treats a response as a shelf with
        a head, never as a window it can widen, and enforces that framing:

        - ``start`` earlier than the shelf head: refuse, naming the head. Only a
          named start claims the series covers it.
        - empty window (``start >= end``): refuse before any request.
        - a cursor that does not move forward past the ``since`` it answered to,
          or a page that runs backwards: refuse instead of looping or re-reading.
        - a page that adds nothing new: the shelf is exhausted, so stop.
        - page budget spent while the last page still contributed rows: refuse,
          because window completeness can no longer be proven.
        - a bucket repeated with different content: refuse, instead of letting
          de-duplication pick which price the ledger trusts.

        The forming candle is separated by :func:`build_series` and never
        appears in ``series.completed``.

        Args:
            instrument_id: The instrument to fetch (e.g., "CRYPTO:KRAKEN:BTC-USD")
            interval_minutes: Bar interval (1, 5, 15, 30, 60, 240, or 1440)
            start: Optional start datetime/date (inclusive). If None, takes the
                   whole shelf the venue lends without claiming coverage.
            end: Optional end datetime/date (exclusive). Stops fetching after this.
            max_pages: Safety limit on number of API calls (default 10)

        Returns:
            CandleSeries with completed candles and integrity checks

        Raises:
            KrakenError: empty window, missing head, or unprovable completeness
            KrakenApiError: cursor that did not advance, or conflicting content
        """
        instrument = self._require_instrument(instrument_id)
        if interval_minutes not in KRAKEN_INTERVALS:
            raise KrakenError(
                f"Unsupported interval {interval_minutes}m for Kraken; "
                f"choose one of {list(KRAKEN_INTERVALS)}"
            )
        if max_pages < 1:
            raise KrakenError(f"max_pages must be >= 1, got {max_pages}")

        # Normalize start/end to datetime with timezone
        start_dt = self._normalize_datetime(start) if start is not None else None
        end_dt = self._normalize_datetime(end) if end is not None else None

        # Validate window before any request
        if start_dt is not None and end_dt is not None and start_dt >= end_dt:
            raise KrakenError("history window is empty")

        mapping = get_kraken_pair(instrument.instrument_id)
        assert mapping is not None
        expected_keys = (
            mapping.metadata.get("result_key", ""),
            mapping.metadata.get("result_key_alt", ""),
        )

        # Calculate initial since cursor
        if start_dt is not None:
            since_cursor = str(int(start_dt.timestamp()))
        else:
            # No start: take whatever the venue lends (harvest mode)
            since_cursor = "0"

        all_rows: List[RawCandle] = []
        pages_fetched = 0
        last_page_added_rows = False
        reached_end = False
        now = self._now()

        while pages_fetched < max_pages:
            params: Dict[str, str] = {
                "pair": mapping.provider_symbol,
                "interval": str(interval_minutes),
                "since": since_cursor,
            }

            payload = self._request("/0/public/OHLC", params)
            key, last_cursor = self._result_key(payload, expected_keys)

            rows = payload["result"][key]
            if not rows:
                # Empty page: we've reached the end of available data
                break

            page_rows = self._merge_page(
                all_rows,
                [self._parse_row(row, instrument_id) for row in rows],
                instrument_id,
                not_before=self._cursor_instant(since_cursor),
                forming_from=now - timedelta(minutes=interval_minutes),
            )
            all_rows.extend(page_rows)
            pages_fetched += 1

            # Only a named start claims the series covers it. Later pages carry
            # newer buckets, so this head can never improve — refuse now instead
            # of spending the rest of the budget paging to the same conclusion.
            if start_dt is not None and all_rows[0].open_time > start_dt:
                first_bar_time = all_rows[0].open_time
                raise KrakenError(
                    f"Kraken's history shelf starts at {first_bar_time.isoformat()}, "
                    f"after the requested start {start_dt.isoformat()}: a shorter "
                    "series must not be presented as covering the window. An older "
                    f"``since`` does not unlock bars — the venue answers with its "
                    f"{MAX_OHLC_ROWS} most recent {interval_minutes}m buckets."
                )

            last_page_added_rows = bool(page_rows)
            if not page_rows:
                break

            # Stop if we've reached the end boundary
            if end_dt is not None and all_rows[-1].open_time >= end_dt:
                # Trim rows that are at or after end
                all_rows = [r for r in all_rows if r.open_time < end_dt]
                last_page_added_rows = False
                reached_end = True
                break

            # A short page is the end of what the venue lends
            if len(rows) < MAX_OHLC_ROWS:
                break

            if last_cursor is None:
                break
            if not self._cursor_moves_forward(since_cursor, last_cursor):
                raise KrakenApiError(
                    f"Pagination cursor did not advance: {since_cursor} -> {last_cursor}"
                )
            since_cursor = str(last_cursor)

        # Validate short series: the budget ran out while the venue was still
        # contributing new bars, so the window cannot be proven complete.
        if not reached_end and pages_fetched >= max_pages and last_page_added_rows:
            raise KrakenError(
                f"Fetched {len(all_rows)} bars across {max_pages} pages but the "
                "series must not be presented as the full history"
            )

        return build_series(
            instrument_id=instrument_id,
            interval_minutes=interval_minutes,
            rows=all_rows,
            now=self._now(),
        )

    @staticmethod
    def _normalize_datetime(value: Any) -> datetime:
        """Convert date or datetime to timezone-aware datetime."""
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        raise KrakenError(f"Cannot normalize {type(value).__name__} to datetime")

    @staticmethod
    def _parse_row(row: Any, instrument_id: str) -> RawCandle:
        if not isinstance(row, (list, tuple)) or len(row) != 8:
            raise KrakenApiError(f"Unexpected OHLC row shape for {instrument_id}: {row!r}")
        timestamp, open_, high, low, close, _vwap, volume, count = row
        try:
            open_time = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        except (TypeError, ValueError, OSError) as exc:
            raise KrakenApiError(f"Bad OHLC timestamp {timestamp!r}") from exc

        return RawCandle(
            open_time=open_time,
            open=_to_decimal(open_, "open"),
            high=_to_decimal(high, "high"),
            low=_to_decimal(low, "low"),
            close=_to_decimal(close, "close"),
            volume=_to_decimal(volume, "volume"),
            trade_count=int(count) if str(count).lstrip("-").isdigit() else None,
        )

    # -- DataProvider surface ----------------------------------------------

    def get_bars(self, symbol: str, start_date: str, end_date: str) -> List[OHLCVBar]:
        """Completed daily UTC bars inside [start_date, end_date]."""
        instrument_id = self._instrument_id_for(symbol)
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise KrakenError("start_date and end_date must be ISO dates (YYYY-MM-DD)") from exc
        if start > end:
            raise KrakenError("start_date must be on or before end_date")

        series = self.get_candle_series(instrument_id, interval_minutes=1440)
        if series.completed and len(series.completed) >= MAX_OHLC_ROWS - 1:
            earliest = series.completed[0].open_time.date()
            if earliest > start:
                raise KrakenError(
                    f"Kraken returns at most {MAX_OHLC_ROWS} daily candles per request; "
                    f"the window starts at {earliest.isoformat()}, after the requested "
                    f"{start.isoformat()}. Narrow the range or page backwards explicitly — "
                    "a shorter series must not be presented as the full history."
                )

        bars: List[OHLCVBar] = []
        for candle in series.completed:
            bar_day = candle.open_time.date()
            if start <= bar_day <= end:
                bars.append(
                    OHLCVBar(
                        date=bar_day.isoformat(),
                        open=float(candle.open),
                        high=float(candle.high),
                        low=float(candle.low),
                        close=float(candle.close),
                        volume=float(candle.volume),
                    )
                )
        return bars

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        instrument_id = self._instrument_id_for(symbol)
        series = self.get_candle_series(instrument_id, interval_minutes=1440, limit=2)
        if not series.completed:
            return None
        candle = series.completed[-1]
        return OHLCVBar(
            date=candle.open_time.date().isoformat(),
            open=float(candle.open),
            high=float(candle.high),
            low=float(candle.low),
            close=float(candle.close),
            volume=float(candle.volume),
        )

    def _instrument_id_for(self, symbol: str) -> str:
        if ":" in symbol:
            return symbol
        for instrument in CRYPTO_INSTRUMENTS:
            if instrument.symbol == symbol:
                return instrument.instrument_id
        raise KrakenError(f"Unknown symbol {symbol!r}; pass a crypto instrument id")

    def get_date_range(self) -> Dict[str, Any]:
        return {
            "min": None,
            "max": self._now().date().isoformat(),
            "total_trading_days": None,
            "note": (
                "Kraken serves up to 720 candles per request; history bounds are read "
                "from each response, not declared here."
            ),
        }

    def compute_checksum(self, symbols: List[str], start_date: str, end_date: str) -> str:
        serialised: Dict[str, Any] = {}
        for symbol in sorted(symbols):
            bars = self.get_bars(symbol, start_date, end_date)
            serialised[symbol] = [
                {"date": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close}
                for b in bars
            ]
        raw = json.dumps(serialised, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def get_data_quality(self, symbol: str) -> Dict[str, Any]:
        return {
            "data_nature": "live" if self.is_available() else "unknown",
            "adjustment": "not_applicable",
            "timezone": "UTC",
            "note": (
                "Kraken public spot market data, read-only, no account key. External "
                "redistribution has not been cleared: display rights must be reviewed "
                "before any public or paid surface shows these prices."
            ),
            "note_zh": (
                "Kraken 公開現貨行情，唯讀，無帳戶金鑰。對外重新分發尚未取得授權："
                "任何公開或收費介面展示前，須先核對交易所條款。"
            ),
        }

    def get_trading_specs(self, instrument_id: str) -> Dict[str, Any]:
        """Venue-declared tick, lot and minimum order for one pair."""
        instrument = self._require_instrument(instrument_id)
        mapping = get_kraken_pair(instrument.instrument_id)
        assert mapping is not None

        payload = self._request(
            "/0/public/AssetPairs",
            {"pair": mapping.provider_symbol},
        )
        key, _last_cursor = self._result_key(
            payload,
            (mapping.metadata.get("result_key", ""), mapping.metadata.get("result_key_alt", "")),
        )
        pair = payload["result"][key]

        def _optional_decimal(field: str) -> Optional[Decimal]:
            if pair.get(field) in (None, ""):
                return None
            return _to_decimal(pair[field], field)

        return {
            "instrument_id": instrument.instrument_id,
            "exchange": instrument.exchange.value,
            "provider_symbol": mapping.provider_symbol,
            "altname": pair.get("altname"),
            "wsname": pair.get("wsname"),
            "base_asset": instrument.base_asset,
            "quote_asset": instrument.quote_asset,
            "price_precision": pair.get("pair_decimals", instrument.price_precision),
            "quantity_precision": pair.get("lot_decimals", instrument.quantity_precision),
            "tick_size": _optional_decimal("tick_size"),
            "min_quantity": _optional_decimal("ordermin"),
            "min_notional": _optional_decimal("costmin"),
            "venue_status": pair.get("status"),
            "spec_source": "kraken_assetpairs_live",
            "fetched_at": self._now().isoformat(),
        }

    def budget_snapshot(self) -> BudgetSnapshot:
        """Current shared budget usage, read-only (never consumes)."""
        return self._budget_gate.snapshot()

    def get_status(self) -> Dict[str, Any]:
        status: Dict[str, Any] = {
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "feed_mode": self.feed_mode,
            "available": self.is_available(),
            "connected": self.is_available(),
            "read_only": True,
            "orders_supported": False,
            # Process-local count for this adapter instance; the shared budget
            # is in ``budget`` and is the number that gates calls.
            "request_count": self._request_count,
            "budget_denials": self._budget_denials,
            "consecutive_failures": self._consecutive_failures,
            "last_success_at": self._last_success_at,
            "last_error_at": self._last_error_at,
            "declared_pairs": [i.instrument_id for i in CRYPTO_INSTRUMENTS],
            "redistribution": "NOT_CLEARED",
        }
        if self._last_budget is not None:
            status["last_reservation"] = self._last_budget
        try:
            status["budget"] = self.budget_snapshot().as_dict()
        except Exception as exc:  # a status probe must not 500 the lane
            status["budget"] = {"error": type(exc).__name__, "scope": "unavailable"}
        if self._last_error:
            status["last_error"] = self._last_error
        return status
