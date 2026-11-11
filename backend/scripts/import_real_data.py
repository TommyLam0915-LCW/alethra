#!/usr/bin/env python3
"""Import an offline bar file into the market-data warehouse, or refuse to.

Kraken only lends its candles (the public OHLC answer is the newest shelf), so a
multi-year series exists here only if what was once read is stored again. This is
the entry point for storing it *without* a request: the input is a file, the write
goes through ``crypto_bar_harvest.store_series_bars`` — the same row contract the
live harvest uses — and every check that could disqualify the file, the target, or
the operator's statement about either runs before the first write.

``scripts/import_real_data.py`` at the repository root is a compatibility entry
point onto this file; it holds no checks of its own, so choosing that path cannot
skip a guard.

Two input shapes are accepted, because both exist in the round records:

* a **run detail export** — ``{"detail": {"instrument_id", "interval_minutes",
  "provider_id", "data_nature", "bar_count", "first_open_time", "last_open_time",
  "fetched_at", "dataset_fingerprint", "dataset_integrity_ok", "bars": [{"open_time",
  "open", "high", "low", "close", "volume", "interval_minutes"}, ...]}}``. Each bar
  declares its own grain and carries the venue's decimal strings, so any grain in
  ``KRAKEN_INTERVALS`` can be imported and the prices keep the digits the venue
  published.
* a **flat daily payload** — ``{"instrument_id", "interval_minutes", "bars":
  [[unix_seconds, open, high, low, close, volume], ...], "harvest_fingerprint",
  "currency"?}``. Storage keys these by calendar date, so this shape is
  daily-only: a finer grain here would silently collapse several bars of one day
  into a single row, and that refusal is kept rather than widened.

Both shapes then run the same checklist: expected SHA256 of the whole file; content
validation; the series' own integrity gate; the safety gate against the real
connection; the target the operator named; and only then the write, which is
idempotent and refuses to overwrite a stored bar whose values differ.

Usage (from the repository root)::

    set -a && . backend/.env.local && set +a
    export TEST_MODE=true
    PYTHONPATH=backend python3 backend/scripts/import_real_data.py \\
        --input path/to/mb10_run_detail.json \\
        --expected-sha256 5e7b398f876afd1b3c8b61dc942ecf7b21e1c322c74a6d088e6dae0ce532044e

``DATABASE_URL`` must be the plain ``postgresql://`` scheme: the safety gate parses
it itself, and a SQLAlchemy-only scheme such as ``postgresql+psycopg://`` is not a
scheme it recognises — the importer normalises it before handing it over. Nothing
here prints it.

Nothing is written unless ``--confirm-write`` is given, and a write additionally
requires the instrument, the target database and the target role to be stated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.data.crypto_instruments import get_crypto_instrument
from app.engine.backtest import KNOWN_DATA_NATURES
from app.providers.kraken_provider import KRAKEN_INTERVALS
from app.services.crypto_bar_harvest import (
    PROVIDER_ID,
    WarehouseImportRejected,
    classify_series_rows,
    store_series_bars,
)
from app.services.crypto_series import CandleSeries, RawCandle, build_series
from app.services.market_data_storage import MarketDataStorage, provenance_note

DAILY_INTERVAL_MINUTES = 1440
FLAT_REQUIRED_KEYS = ("instrument_id", "interval_minutes", "bars", "harvest_fingerprint")
REQUIRED_DETAIL_KEYS = (
    "instrument_id", "interval_minutes", "provider_id", "data_nature",
    "bar_count", "first_open_time", "last_open_time", "fetched_at",
    "dataset_fingerprint", "dataset_integrity_ok", "bars",
)
REQUIRED_BAR_KEYS = (
    "open_time", "open", "high", "low", "close", "volume", "interval_minutes",
)
#: ``volume_decimal`` is Numeric(30, 12): 30 significant digits, 12 of them
#: after the point. Anything wider would be silently rounded on the way in.
VOLUME_MAX_DIGITS = 30
VOLUME_MAX_DECIMALS = 12

#: Says what the source chain does *not* cover, so no reader can mistake a file
#: import for a witnessed venue read.
CHAIN_LIMIT = "venue read performed by the producer of this file, not by this importer"
#: The flat shape states no read time, so finality is inferred from its own last
#: bucket and the report has to admit that instead of leaving it implied.
FLAT_FINALITY = "finality assumed from the file's last bucket (no read time recorded)"


class ImportRefusal(Exception):
    """The file, the plan, or the target disqualifies this import.

    Called with a stage plus the problems under it, or with one message when the
    refusal is a single sentence. Both forms reach stderr as a refusal, and the
    database is unchanged either way.
    """

    def __init__(self, stage_or_message: str, problems: Optional[List[str]] = None,
                 detail: Any = None):
        if problems is None:
            super().__init__(stage_or_message)
            self.stage = "refusal"
            self.problems = [stage_or_message]
        else:
            super().__init__(f"[{stage_or_message}] " + "; ".join(problems))
            self.stage = stage_or_message
            self.problems = problems
        self.detail = detail


class UsageError(Exception):
    """The invocation or environment is wrong; nothing was attempted."""


# -- file-level checks -------------------------------------------------------

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_expected_sha256(path: Path, expected: str) -> str:
    """Hash the whole file and compare, before any of it is trusted.

    A Git blob SHA is not this value and is never accepted in its place — it is
    computed over a compressed header plus the content, so it can only ever
    differ.
    """
    actual = sha256_file(path)
    if actual != expected.lower():
        raise ImportRefusal("hash", [
            f"file sha256 is {actual}, expected {expected.lower()}",
        ], {"sha256": actual})
    return actual


# -- flat daily payload ------------------------------------------------------

def currency_of(instrument_id: str) -> str:
    """Quote currency taken from the instrument id, e.g. CRYPTO:KRAKEN:BTC-USD."""
    try:
        _, _, symbol = instrument_id.split(":", 2)
    except ValueError:
        raise ImportRefusal(
            f"Instrument id {instrument_id!r} is not PROVIDER:VENUE:BASE-QUOTE, "
            "so its currency cannot be verified."
        ) from None
    if "-" not in symbol:
        raise ImportRefusal(
            f"Instrument id {instrument_id!r} carries no quote currency."
        )
    return symbol.rsplit("-", 1)[1].upper()


def read_verified(path: Path, expected_sha256: str) -> Tuple[bytes, str]:
    """Read the file once and return those bytes with the hash they produced.

    Hashing and parsing the same bytes is the point: re-reading after the check
    would let a file that changed in between be imported under the earlier hash.
    """
    if not path.is_file():
        raise ImportRefusal("read", [f"Payload {path} not found."])
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256.lower():
        raise ImportRefusal("hash", [
            f"Payload hash is {digest}, expected {expected_sha256.lower()}: "
            "the file is not the one that was validated."
        ])
    return raw, digest


def payload_from_object(payload: Any, name: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ImportRefusal("schema", [f"Payload {name} must be a JSON object."])
    missing = [key for key in FLAT_REQUIRED_KEYS if key not in payload]
    if missing:
        raise ImportRefusal("schema",
                            [f"Payload is missing required keys: {missing}."])
    if not isinstance(payload["bars"], list) or not payload["bars"]:
        raise ImportRefusal("schema", ["Payload contains no bars."])
    return payload


def load_payload(path: Path, expected_sha256: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
    if not path.exists():
        raise ImportRefusal("read", [f"Payload {path} not found."])
    digest = sha256_file(path)
    if expected_sha256 and digest != expected_sha256.lower():
        raise ImportRefusal("hash", [
            f"Payload hash is {digest}, expected {expected_sha256.lower()}: "
            "the file is not the one that was validated."
        ])
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ImportRefusal("read", [f"Payload {path} is not valid JSON: {exc}"]) from exc
    return payload_from_object(payload, path.name), digest


def validate_bars(payload: Dict[str, Any], expected_instrument: Optional[str]) -> List[Dict[str, Any]]:
    instrument_id = payload["instrument_id"]
    if expected_instrument and instrument_id != expected_instrument:
        raise ImportRefusal(
            f"Payload is for {instrument_id}, expected {expected_instrument}."
        )

    interval = payload["interval_minutes"]
    if interval != DAILY_INTERVAL_MINUTES:
        raise ImportRefusal(
            f"Payload interval is {interval} minutes. This importer only accepts "
            f"{DAILY_INTERVAL_MINUTES}m daily bars: storage keys these rows by "
            "calendar date, so a finer grain would silently collapse several "
            "bars of one day into a single row."
        )

    declared = payload.get("currency")
    actual = currency_of(instrument_id)
    if declared and declared.upper() != actual:
        raise ImportRefusal(
            f"Payload declares {declared} but {instrument_id} is quoted in {actual}."
        )

    rows: List[Dict[str, Any]] = []
    previous: Optional[datetime] = None
    for index, row in enumerate(payload["bars"]):
        if not isinstance(row, (list, tuple)) or len(row) != 6:
            raise ImportRefusal(
                f"Bar {index} is {row!r}; expected [timestamp, open, high, low, close, volume]."
            )
        stamp, open_, high, low, close, volume = row
        try:
            moment = datetime.fromtimestamp(int(stamp), tz=timezone.utc)
        except (TypeError, ValueError, OSError) as exc:
            raise ImportRefusal(f"Bar {index} has an unusable timestamp {stamp!r}.") from exc
        if (moment.hour, moment.minute, moment.second) != (0, 0, 0):
            raise ImportRefusal(
                f"Bar {index} opens at {moment.isoformat()}, not on the UTC midnight "
                "grid this storage shape reserves for daily bars."
            )
        if previous is not None and moment <= previous:
            raise ImportRefusal(
                f"Bar {index} at {moment.isoformat()} does not advance past "
                f"{previous.isoformat()}: the series is unordered or duplicated."
            )
        previous = moment

        prices: List[float] = []
        for label, value in (("open", open_), ("high", high), ("low", low), ("close", close)):
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ImportRefusal(f"Bar {index} {label} is not a number: {value!r}.") from exc
            if not math.isfinite(number) or number <= 0:
                raise ImportRefusal(
                    f"Bar {index} {label} is {number}; must be a finite positive price."
                )
            prices.append(number)
        open_, high, low, close = prices
        if high < max(open_, close) or low > min(open_, close):
            raise ImportRefusal(
                f"Bar {index} is internally inconsistent: open={open_} high={high} "
                f"low={low} close={close}."
            )
        if volume is not None:
            try:
                volume_number: Optional[float] = float(volume)
            except (TypeError, ValueError) as exc:
                raise ImportRefusal(f"Bar {index} volume is not a number: {volume!r}.") from exc
            if not math.isfinite(volume_number) or volume_number < 0:
                raise ImportRefusal(
                    f"Bar {index} volume is {volume_number}; must be finite and non-negative."
                )
        else:
            volume_number = None

        rows.append({
            "date": moment.date().isoformat(),
            # ``open_time`` is kept alongside the date so the write can be a
            # timestamp-exact warehouse row instead of a date-keyed one.
            "open_time": moment,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume_number,
        })
    return rows


def candles_from_flat_rows(rows: List[Dict[str, Any]]) -> List[RawCandle]:
    """Turn validated daily rows into venue-shaped candles.

    The flat format carries binary floats, so the decimals here are the shortest
    round-trip of those floats rather than the venue's published digits. That is a
    property of the file, not of this importer, and the report says so.
    """
    def exact(value: Any) -> Decimal:
        if value is None:
            return Decimal(0)
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ImportRefusal(f"Cannot read {value!r} as an exact decimal.") from exc
        if not number.is_finite():
            raise ImportRefusal(f"{value!r} is not finite.")
        return number

    return [
        RawCandle(
            open_time=row["open_time"],
            open=exact(row["open"]), high=exact(row["high"]),
            low=exact(row["low"]), close=exact(row["close"]),
            volume=exact(row["volume"]),
            trade_count=None,
        )
        for row in rows
    ]


# -- run detail export -------------------------------------------------------

def detail_from_object(payload: Any, name: str) -> Dict[str, Any]:
    if not isinstance(payload, dict) or "detail" not in payload:
        raise ImportRefusal("schema", [
            "expected a run detail export of the form {\"detail\": {...}}"
        ])
    detail = payload["detail"]
    if not isinstance(detail, dict):
        raise ImportRefusal("schema", ["\"detail\" is not an object"])
    missing = [k for k in REQUIRED_DETAIL_KEYS if k not in detail]
    if missing:
        raise ImportRefusal("schema", ["missing required key(s): " + ", ".join(missing)])
    if not isinstance(detail["bars"], list):
        raise ImportRefusal("schema", ["\"bars\" is not a list"])
    return detail


def load_detail(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportRefusal("read", [f"cannot read {path.name}: {type(exc).__name__}: {exc}"])
    return detail_from_object(payload, path.name)


def _decimal(field: str, value: Any, problems: List[str]) -> Optional[Decimal]:
    """Parse one decimal field, keeping the exact digits the venue published."""
    if isinstance(value, bool) or value is None:
        problems.append(f"{field}: missing or not a number ({value!r})")
        return None
    if isinstance(value, float):
        # A float in the file has already lost the venue's digits; the exporter
        # writes strings, so accepting a float here would launder a rounded
        # price into a decimal that looks exact.
        problems.append(f"{field}: expected a decimal string, got float {value!r}")
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        problems.append(f"{field}: not a decimal ({value!r})")
        return None
    if not number.is_finite():
        problems.append(f"{field}: not finite ({value!r})")
        return None
    if not math.isfinite(float(number)):
        problems.append(f"{field}: outside double precision range")
        return None
    return number


def _check_quantity(number: Decimal, problems: List[str], field: str) -> None:
    sign, digits, exponent = number.as_tuple()
    significant = len(digits)
    places = max(0, -exponent)
    if places > VOLUME_MAX_DECIMALS:
        problems.append(
            f"{field}: {places} decimal places, column holds {VOLUME_MAX_DECIMALS}")
    if significant > VOLUME_MAX_DIGITS:
        problems.append(
            f"{field}: {significant} significant digits, column holds {VOLUME_MAX_DIGITS}")


def _parse_open_time(value: Any, problems: List[str]) -> Optional[datetime]:
    if not isinstance(value, str):
        problems.append(f"open_time: expected an ISO 8601 string, got {value!r}")
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        problems.append(f"open_time: unparseable ({value!r})")
        return None
    if moment.tzinfo is None:
        problems.append(
            f"open_time: naive timestamp {value!r}; the grain is only defined "
            "against a stated zone")
        return None
    return moment.astimezone(timezone.utc)


def build_raw_candles(
    detail: Dict[str, Any],
    *,
    instrument_id: str,
    interval_minutes: int,
) -> List[RawCandle]:
    """Validate every bar and return the rows in the file's own exact decimals.

    Shape, presence, finiteness and magnitude are decided here. Ordering,
    duplicates, grid alignment (which for a daily grain is 00:00 UTC), OHLC
    consistency and gaps are decided by ``build_series``, which is the same
    integrity gate the live harvest passes through — so a file cannot arrive at
    the warehouse by a softer standard than a venue read would.
    """
    problems: List[str] = []
    candles: List[RawCandle] = []

    for index, bar in enumerate(detail["bars"]):
        where = f"bar[{index}]"
        if not isinstance(bar, dict):
            problems.append(f"{where}: expected an object, got {type(bar).__name__}")
            continue
        missing = [k for k in REQUIRED_BAR_KEYS if k not in bar]
        if missing:
            problems.append(f"{where}: missing key(s) " + ", ".join(missing))
            continue
        if bar["interval_minutes"] != interval_minutes:
            problems.append(
                f"{where}: interval_minutes is {bar['interval_minutes']!r}, "
                f"import asked for {interval_minutes}")
            continue
        open_time = _parse_open_time(bar["open_time"], problems)
        fields = {}
        for name in ("open", "high", "low", "close", "volume"):
            number = _decimal(f"{where}.{name}", bar[name], problems)
            if number is None:
                continue
            if name == "volume":
                _check_quantity(number, problems, f"{where}.{name}")
                if number < 0:
                    problems.append(f"{where}.volume: negative")
            elif number <= 0:
                problems.append(f"{where}.{name}: price must be positive")
            fields[name] = number
        if len(fields) != 5 or open_time is None:
            continue
        candles.append(RawCandle(
            open_time=open_time,
            open=fields["open"], high=fields["high"], low=fields["low"],
            close=fields["close"], volume=fields["volume"],
            trade_count=None,
        ))
        if len(problems) > 20:
            problems.append(f"…and more (first {len(problems)} problems shown)")
            break

    if problems:
        raise ImportRefusal("data", problems, {"problem_count": len(problems)})
    if not candles:
        raise ImportRefusal("data", ["file contains no bars"])
    return candles


def series_for_import(
    detail: Dict[str, Any],
    *,
    instrument_id: str,
    interval_minutes: int,
    fetched_at: Optional[datetime] = None,
) -> Tuple[List[RawCandle], CandleSeries]:
    """Build the series on the file's own clock.

    ``now`` is the recorded read time rather than the wall clock: finality is a
    property of when the venue answered, and re-deriving it today would let the
    newest bar's status depend on the date the import happens.
    """
    candles = build_raw_candles(
        detail, instrument_id=instrument_id, interval_minutes=interval_minutes)
    if fetched_at is None:
        fetched_at = _parse_open_time(detail["fetched_at"], []) or datetime.now(timezone.utc)
    series = build_series(
        instrument_id=instrument_id,
        interval_minutes=interval_minutes,
        rows=candles,
        now=fetched_at,
    )
    return candles, series


def crosscheck(detail: Dict[str, Any], series: CandleSeries, *, sha256: str) -> None:
    """Compare the file's own claims against what its bytes actually contain."""
    problems: List[str] = []

    if detail["data_nature"] not in KNOWN_DATA_NATURES:
        problems.append(
            f"data_nature {detail['data_nature']!r} is outside the vocabulary "
            f"{sorted(KNOWN_DATA_NATURES)}; every reader would show it as "
            "\"unknown\", so it is not a claim of authenticity")
    if detail["provider_id"] != PROVIDER_ID:
        problems.append(
            f"provider_id {detail['provider_id']!r} is not the {PROVIDER_ID!r} "
            "warehouse this path writes")
    if detail["dataset_integrity_ok"] is not True:
        problems.append("dataset_integrity_ok is not true")
    if not series.integrity_ok:
        problems.append(
            "series fails its own integrity gate: "
            f"duplicates={len(series.duplicate_timestamps)} "
            f"off_grid={len(series.off_grid_timestamps)} "
            f"invalid={len(series.invalid_timestamps)} gaps={len(series.gaps)} "
            f"missing_intervals={series.missing_intervals}")
    if series.out_of_order_rows:
        problems.append(f"{series.out_of_order_rows} rows are out of time order")
    if series.forming is not None:
        problems.append(
            "the newest bucket is still forming at the recorded read time "
            f"({series.forming.open_time.isoformat()}), so this file claims a "
            "candle that had not closed when it was written")
    if len(series.completed) != detail["bar_count"]:
        problems.append(
            f"bar_count says {detail['bar_count']}, {len(series.completed)} "
            "completed bars are present")
    if series.fingerprint != detail["dataset_fingerprint"]:
        problems.append(
            f"recomputed fingerprint {series.fingerprint} does not match the "
            f"declared {detail['dataset_fingerprint']}; the bytes are not the "
            "series this file describes")
    first = series.completed[0].open_time.isoformat() if series.completed else None
    last = series.completed[-1].open_time.isoformat() if series.completed else None
    for label, declared, actual in (
        ("first_open_time", detail["first_open_time"], first),
        ("last_open_time", detail["last_open_time"], last),
    ):
        if declared != actual:
            problems.append(f"{label} says {declared!r}, bars say {actual!r}")

    if problems:
        raise ImportRefusal("integrity", problems, {"sha256": sha256})


# -- what both shapes have to say before anything is written -----------------

def claims_from_flat(payload: Dict[str, Any], series: CandleSeries) -> Dict[str, Any]:
    """Normalise the flat payload's claims into the detail export's vocabulary.

    The flat shape declares no data_nature, no provider and no read time, so
    those stay absent here and the write path refuses on them rather than
    filling in a plausible value.
    """
    return {
        "format": "flat_daily",
        "instrument_id": payload["instrument_id"],
        "interval_minutes": payload["interval_minutes"],
        "provider_id": payload.get("provider_id"),
        "data_nature": payload.get("data_nature"),
        "bar_count": len(series.completed),
        "first_open_time": series.completed[0].open_time.isoformat(),
        "last_open_time": series.completed[-1].open_time.isoformat(),
        "fetched_at": payload.get("fetched_at"),
        "fingerprint": str(payload["harvest_fingerprint"]),
        "run_id": payload.get("id"),
        "limit": FLAT_FINALITY,
        "precision": "binary floats in the file; decimals are their round trip",
    }


def claims_from_detail(detail: Dict[str, Any], series: CandleSeries) -> Dict[str, Any]:
    source_run_id = detail.get("id")
    return {
        "format": "run_detail",
        "instrument_id": detail["instrument_id"],
        "interval_minutes": detail["interval_minutes"],
        "provider_id": detail["provider_id"],
        "data_nature": detail["data_nature"],
        "bar_count": detail["bar_count"],
        "first_open_time": detail["first_open_time"],
        "last_open_time": detail["last_open_time"],
        "fetched_at": detail["fetched_at"],
        "fingerprint": detail["dataset_fingerprint"],
        "run_id": source_run_id if isinstance(source_run_id, str) else "unknown",
        "limit": CHAIN_LIMIT,
        "precision": "decimal strings as published",
    }


def check_series(series: CandleSeries, *, sha256: str) -> None:
    """The integrity gate both shapes share, minus the detail-only claims."""
    problems: List[str] = []
    if not series.integrity_ok:
        problems.append(
            "series fails its own integrity gate: "
            f"duplicates={len(series.duplicate_timestamps)} "
            f"off_grid={len(series.off_grid_timestamps)} "
            f"invalid={len(series.invalid_timestamps)} gaps={len(series.gaps)} "
            f"missing_intervals={series.missing_intervals}")
    if series.out_of_order_rows:
        problems.append(f"{series.out_of_order_rows} rows are out of time order")
    if series.forming is not None:
        problems.append(
            f"the newest bucket ({series.forming.open_time.isoformat()}) is still "
            "forming, so this file claims a candle that had not closed")
    if not series.completed:
        problems.append("no completed bars")
    if problems:
        raise ImportRefusal("integrity", problems, {"sha256": sha256})


def provenance_for(path: Path, claims: Dict[str, Any], sha256: str) -> Dict[str, Any]:
    """Where these bars came from — and the limit of that claim.

    The git identity of the *file* is read from the repository holding it, so no
    device-specific absolute path is stored: ``repo`` is the worktree's own name
    and ``path`` is relative to it. The remote URL is never read, because a
    remote can carry credentials in it.
    """

    def git(*args: str) -> Optional[str]:
        try:
            out = subprocess.run(
                ["git", "-C", str(path.parent), *args],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None

    toplevel = git("rev-parse", "--show-toplevel")
    tracked = git("ls-files", "--error-unmatch", str(path)) is not None
    commit = git("log", "-1", "--format=%H", "--", str(path)) if tracked else None

    provenance = {
        "kind": "offline_file_import",
        "format": claims["format"],
        "repo": Path(toplevel).name if toplevel else "not-in-git",
        "commit": commit or ("untracked" if not tracked else "unknown"),
        "path": (
            str(path.relative_to(Path(toplevel))) if toplevel else path.name
        ),
        "file_sha256": sha256,
        "fingerprint": claims["fingerprint"],
        "run_id": claims.get("run_id") or "not declared",
        "venue_read_at": claims.get("fetched_at") or "not recorded",
        "precision": claims["precision"],
        "limit": claims["limit"],
    }
    if not toplevel:
        raise ImportRefusal("provenance", [
            "the file is not inside a git worktree, so its source chain cannot "
            "be recorded as repo + commit + path"
        ])
    # Fail here rather than after the bars are committed.
    try:
        provenance_note(provenance)
    except ValueError as exc:
        raise ImportRefusal("provenance", [str(exc)])
    return provenance


# -- the target --------------------------------------------------------------

def guard_target(database_url: str, *, expected_database: Optional[str] = None,
                 expected_role: Optional[str] = None) -> Dict[str, Any]:
    """Run the repository's own test-database gate against the intended target.

    The gate connects and compares ``current_database()``/``current_user``, and
    rejects superuser and role-creating roles; its allowlist is not widened here.
    When the operator names the database and role they meant, the answers from
    the live connection have to match, so a URL that reaches a different
    database than it claims is refused instead of written into.

    The URL is parsed and normalised, never printed.
    """
    if not database_url:
        raise ImportRefusal("target", [
            "DATABASE_URL (or --database-url) is not set. This importer keeps no "
            "fallback target: an unnamed database is not an approved one."
        ])
    plain = re.sub(r"^postgresql\+\w+://", "postgresql://", database_url)
    try:
        from test_safety_gate import TestDatabaseGateError, verify_test_database
    except ImportError as exc:
        raise ImportRefusal("target", [
            f"The safety gate is unavailable ({exc.__class__.__name__}: {exc}); "
            "refusing to write without it."
        ]) from exc
    try:
        conn_info = verify_test_database(plain)
    except TestDatabaseGateError as exc:
        raise ImportRefusal("target", [
            f"Safety gate rejected the target: {exc}"
        ]) from exc

    for label, expected, actual in (
        ("database", expected_database, conn_info.get("database")),
        ("role", expected_role, conn_info.get("user")),
    ):
        if expected is not None and actual != expected:
            raise ImportRefusal("target", [
                f"Connected to {label} {actual!r}, but the import was stated for "
                f"{expected!r}: refusing to write into a target nobody named."
            ])
    return conn_info


def summarize_target(conn_info: Dict[str, Any]) -> str:
    return (
        f"database={conn_info.get('database')} user={conn_info.get('user')} "
        f"server={conn_info.get('server_addr')}:{conn_info.get('server_port')}"
    )


# -- the one pipeline --------------------------------------------------------

def prepare(parsed: Any, sha256: str, expected_instrument: Optional[str],
             interval_minutes: Optional[int],
             name: str) -> Tuple[CandleSeries, Dict[str, Any]]:
    """Validate the hashed bytes and build the series, without opening a connection."""
    is_detail = isinstance(parsed, dict) and "detail" in parsed
    if is_detail:
        detail = detail_from_object(parsed, name)
        instrument_id = detail["instrument_id"]
        if expected_instrument and instrument_id != expected_instrument:
            raise ImportRefusal("instrument", [
                f"file is for {instrument_id!r}, import asked for {expected_instrument!r}"
            ])
        grain = detail["interval_minutes"]
        if interval_minutes is not None and interval_minutes != grain:
            raise ImportRefusal("instrument", [
                f"file declares {grain}m but the import is for {interval_minutes}m; "
                "a different grain is a different series, not a slice of this one"
            ])
        if grain not in KRAKEN_INTERVALS:
            raise ImportRefusal("instrument", [
                f"interval {grain}m is not a grain this warehouse supports: "
                f"{list(KRAKEN_INTERVALS)}"
            ])
        if get_crypto_instrument(instrument_id) is None:
            raise ImportRefusal("instrument", [
                f"{instrument_id!r} is not a catalogued crypto instrument"
            ])
        _candles, series = series_for_import(
            detail, instrument_id=instrument_id, interval_minutes=grain)
        crosscheck(detail, series, sha256=sha256)
        return series, claims_from_detail(detail, series)

    payload = payload_from_object(parsed, name)
    rows = validate_bars(payload, expected_instrument)
    if interval_minutes is not None and interval_minutes != DAILY_INTERVAL_MINUTES:
        raise ImportRefusal(
            f"--interval-minutes {interval_minutes} cannot be applied to a flat "
            "daily payload: this shape declares no per-bar grain, and only "
            f"{DAILY_INTERVAL_MINUTES}m bars fit it."
        )
    grain = payload["interval_minutes"]
    instrument_id = payload["instrument_id"]
    if get_crypto_instrument(instrument_id) is None:
        raise ImportRefusal("instrument", [
            f"{instrument_id!r} is not a catalogued crypto instrument"
        ])
    # Finality is judged one bucket past the last timestamp the file carries, so
    # re-running the import tomorrow cannot move the boundary.
    now = rows[-1]["open_time"] + timedelta(minutes=grain)
    series = build_series(
        instrument_id=instrument_id,
        interval_minutes=grain,
        rows=candles_from_flat_rows(rows),
        now=now,
    )
    check_series(series, sha256=sha256)
    return series, claims_from_flat(payload, series)


def require_for_write(claims: Dict[str, Any], instrument: Optional[str],
                      args: argparse.Namespace) -> None:
    """The inputs a write cannot go without, stated by the operator or the file."""
    missing: List[str] = []
    if not instrument:
        missing.append("--instrument (the id the file was validated against)")
    if claims["provider_id"] != PROVIDER_ID:
        missing.append(
            f"provider_id must be {PROVIDER_ID!r}, file says {claims['provider_id']!r}")
    if claims["data_nature"] not in KNOWN_DATA_NATURES:
        missing.append(
            f"data_nature must be one of {sorted(KNOWN_DATA_NATURES)}, "
            f"file says {claims['data_nature']!r}")
    if not args.expected_database:
        missing.append("--expected-database (the database this import is for)")
    if not args.expected_role:
        missing.append("--expected-role (the role it connects as)")
    if missing:
        raise ImportRefusal("confirm", [
            f"{len(missing)} requirement(s) for a write are unmet: "
            + "; ".join(missing)
        ])


def run(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", "--json-path", dest="input", required=True,
                        type=Path, help="bar file to import (no default path)")
    parser.add_argument("--expected-sha256", required=True,
                        help="sha256 of the whole file, verified before anything is read as data")
    parser.add_argument("--instrument", "--expected-instrument",
                        dest="instrument_aliases", action="append", default=[],
                        help="instrument id the file must be for")
    parser.add_argument("--interval-minutes", type=int, default=None,
                        help=f"grain being imported; one of {list(KRAKEN_INTERVALS)}")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--expected-database", default=None,
                        help="database name the live connection must answer to")
    parser.add_argument("--expected-role", default=None,
                        help="role the live connection must answer as")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and report the plan; write nothing (the default)")
    parser.add_argument("--confirm-write", action="store_true",
                        help="Actually write. Without it the checks run and nothing is stored.")
    parser.add_argument("--on-conflict", choices=("reject", "overwrite"), default="reject",
                        help="what to do when a stored bar at the same key differs")
    parser.add_argument("--retire-grain-ambiguous", action="store_true",
                        help="delete stored rows that share these timestamps under another "
                             "or no grain instead of refusing")
    args = parser.parse_args(argv)

    if args.dry_run and args.confirm_write:
        raise UsageError("--dry-run and --confirm-write contradict each other")
    stated = [value for value in args.instrument_aliases if value]
    if len(set(stated)) > 1:
        raise UsageError(
            f"--instrument and --expected-instrument disagree: {sorted(set(stated))}")
    instrument = stated[0] if stated else None

    path: Path = args.input
    if not path.is_file():
        raise UsageError(f"--input {path} is not a file")
    if args.interval_minutes is not None and args.interval_minutes not in KRAKEN_INTERVALS:
        raise UsageError(
            f"interval {args.interval_minutes}m is not a grain this warehouse "
            f"supports: {list(KRAKEN_INTERVALS)}")

    raw, sha256 = read_verified(path, args.expected_sha256)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImportRefusal("read", [f"{path.name} is not valid JSON: {exc}"]) from exc
    series, claims = prepare(parsed, sha256, instrument, args.interval_minutes, path.name)

    database_url = args.database_url or os.environ.get("DATABASE_URL", "")
    conn_info = guard_target(
        database_url,
        expected_database=args.expected_database,
        expected_role=args.expected_role,
    )
    provenance = provenance_for(path, claims, sha256)
    if args.confirm_write:
        require_for_write(claims, instrument, args)

    engine = create_engine(database_url)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        plan = classify_series_rows(db, series)
        if not plan["rows"]:
            raise ImportRefusal("write", ["the validated series produced no rows; "
                                          "nothing to write"])
        report = {
            "input": {
                "file": path.name,
                "sha256": sha256,
                "format": claims["format"],
                "instrument_id": claims["instrument_id"],
                "interval_minutes": claims["interval_minutes"],
                "bar_count": len(plan["rows"]),
                "first_open_time": plan["rows"][0]["bar_timestamp"].isoformat(),
                "last_open_time": plan["rows"][-1]["bar_timestamp"].isoformat(),
                "fingerprint": claims["fingerprint"],
                "recomputed_fingerprint": series.fingerprint,
                "data_nature": claims["data_nature"],
                "precision": claims["precision"],
            },
            "provenance": provenance,
            "target": summarize_target(conn_info),
            "plan": {
                "would_insert": len(plan["new"]),
                "already_stored_identical": plan["identical"],
                "conflicting": len(plan["conflicting"]),
                "grain_ambiguous": len(plan["grain_ambiguous"]),
                "conflicting_examples": plan["conflicting"][:3],
                "grain_ambiguous_examples": [
                    {
                        "bar_timestamp": bar.bar_timestamp.isoformat(),
                        "interval_minutes": bar.interval_minutes,
                        "volume": str(bar.volume),
                        "volume_decimal": str(bar.volume_decimal),
                    }
                    for bar in plan["grain_ambiguous"][:3]
                ],
            },
            "written": None,
        }
        if not args.confirm_write:
            report["dry_run"] = True
            return report

        report["dry_run"] = False
        try:
            written = store_series_bars(
                db,
                series,
                on_conflict=args.on_conflict,
                retire_grain_ambiguous=args.retire_grain_ambiguous,
            )
        except WarehouseImportRejected as exc:
            raise ImportRefusal("write", [exc.reason], exc.detail)

        instrument = get_crypto_instrument(claims["instrument_id"])
        note = MarketDataStorage(db).record_offline_provenance(
            instrument_id=claims["instrument_id"],
            provider_id=PROVIDER_ID,
            data_nature=claims["data_nature"],
            timezone=getattr(instrument, "timezone", None) or "UTC",
            currency=currency_of(claims["instrument_id"]),
            provenance=provenance,
            bar_dates=[row["bar_date"] for row in plan["rows"]],
        )
        report["written"] = written
        report["provenance_note_chars"] = len(note)
        return report
    finally:
        db.close()
        engine.dispose()


def main(argv: Optional[List[str]] = None) -> int:
    """Run one import. A bad invocation returns 2; a refusal propagates.

    :class:`ImportRefusal` is raised rather than turned into an exit code here so
    that a caller can tell "the target was refused" from "the flags were wrong",
    and can prove nothing was written by catching it. The command line turns it
    into exit code 1 in :func:`cli`.
    """
    try:
        report = run(argv)
    except UsageError as exc:
        print(f"USAGE ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, default=str, sort_keys=True))
    return 0


def describe_refusal(exc: ImportRefusal) -> None:
    print(f"REFUSED [{exc.stage}] — nothing was written", file=sys.stderr)
    for problem in exc.problems:
        print(f"  - {problem}", file=sys.stderr)
    if exc.detail:
        print(json.dumps({"detail": exc.detail}, indent=2, default=str),
              file=sys.stderr)


def cli(argv: Optional[List[str]] = None) -> int:
    try:
        return main(argv)
    except ImportRefusal as exc:
        describe_refusal(exc)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
