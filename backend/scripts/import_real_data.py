#!/usr/bin/env python3
"""Import an offline bar file into the market-data warehouse, or refuse to.

The venue only lends its candles (Kraken answers with the newest 721 rows), so a
multi-year series exists here only if what was once read is stored again. This is
the entry point for storing it *without* a request: the input is a file, the
write goes through ``crypto_bar_harvest.store_series_bars`` — the same row
contract the live harvest uses — and every check that could disqualify the file
runs before the first write.

Usage (from the repository root)::

    set -a && . backend/.env.local && set +a
    export TEST_MODE=true
    export DATABASE_URL="postgresql://$ALETHRA_TEST_DB_USER:$ALETHRA_TEST_DB_PASSWORD@\\\
$ALETHRA_TEST_DB_HOST:$ALETHRA_TEST_DB_PORT/$ALETHRA_TEST_DB_NAME"
    PYTHONPATH=backend python3 backend/scripts/import_real_data.py \\
        --input path/to/mb10_run_detail.json \\
        --expected-sha256 5e7b398f876afd1b3c8b61dc942ecf7b21e1c322c74a6d088e6dae0ce532044e \\
        --dry-run

``DATABASE_URL`` must be the plain ``postgresql://`` scheme: the safety gate
parses it itself, and a SQLAlchemy-only scheme such as ``postgresql+psycopg://``
is not a scheme it recognises. Nothing here prints it.

What the file has to be: a research run detail export, i.e. JSON of the form
``{"detail": {"instrument_id", "interval_minutes", "provider_id", "data_nature",
"bar_count", "first_open_time", "last_open_time", "fetched_at",
"dataset_fingerprint", "dataset_integrity_ok", "bars": [{"open_time", "open",
"high", "low", "close", "volume", "interval_minutes"}, ...]}}`` — the shape the
research lane already writes when it publishes a result, so the bars come with
the fingerprint the venue read produced rather than as a bare array.

Order of refusal, and why: the expected SHA256 is verified before the file is
trusted at all; the content is validated before any connection is opened; and
the target database is verified before the first write. A refusal therefore
leaves the database exactly as it was.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
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
from test_safety_gate import (
    TestDatabaseGateError,
    verify_database_identity,
    verify_url_policy,
)

#: The only database this importer is approved to write. Widening it means
#: editing this constant, which is the reviewable approval step — no connection
#: string can widen it from the outside.
APPROVED_TARGET = {
    "database": "alethra_test_isolated_20261109",
    "user": "alethra_test",
}

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

# Says what the source chain does *not* cover, so no reader can mistake a file
# import for a witnessed venue read.
CHAIN_LIMIT = "venue read performed by the research lane, not by this importer"


class ImportRefusal(Exception):
    """The file, the plan, or the target disqualifies this import."""

    def __init__(self, stage: str, problems: List[str], detail: Any = None):
        super().__init__(f"[{stage}] " + "; ".join(problems))
        self.stage = stage
        self.problems = problems
        self.detail = detail


class UsageError(Exception):
    """The invocation or environment is wrong; nothing was attempted."""


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


def load_detail(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportRefusal("read", [f"cannot read {path.name}: {type(exc).__name__}: {exc}"])
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


def provenance_for(path: Path, detail: Dict[str, Any], sha256: str) -> Dict[str, Any]:
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
    source_run_id = detail.get("id")

    provenance = {
        "kind": "offline_file_import",
        "repo": Path(toplevel).name if toplevel else "not-in-git",
        "commit": commit or ("untracked" if not tracked else "unknown"),
        "path": (
            str(path.relative_to(Path(toplevel))) if toplevel else path.name
        ),
        "file_sha256": sha256,
        "fingerprint": detail["dataset_fingerprint"],
        "run_id": source_run_id if isinstance(source_run_id, str) else "unknown",
        "venue_read_at": detail["fetched_at"],
        "limit": CHAIN_LIMIT,
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


def guard_target(database_url: str) -> Dict[str, Any]:
    """Open the database, prove which one it is, and prove who we are in it.

    The URL is parsed, never printed. The identity check runs against the live
    connection, so a database that answers under a different name than the URL
    claims is refused here rather than discovered mid-write.
    """
    if not database_url:
        raise UsageError("DATABASE_URL is not set; this importer never falls "
                         "back to application config")
    try:
        parsed = verify_url_policy(database_url)
    except TestDatabaseGateError as exc:
        raise ImportRefusal("target", [str(exc)])

    engine = None
    try:
        engine = create_engine(database_url, connect_args={"connect_timeout": 5})
        raw = engine.raw_connection()
        try:
            identity = verify_database_identity(raw, parsed["database"])
        finally:
            raw.close()
    except Exception as exc:
        # Type only: driver connection errors can echo the conninfo.
        raise ImportRefusal("target", [
            f"cannot verify the target database ({type(exc).__name__})"
        ])
    finally:
        if engine is not None:
            engine.dispose()

    if identity["database"] != APPROVED_TARGET["database"]:
        raise ImportRefusal("target", [
            f"connected to database {identity['database']!r}, approved target is "
            f"{APPROVED_TARGET['database']!r}"
        ])
    if identity["user"] != APPROVED_TARGET["user"]:
        raise ImportRefusal("target", [
            f"connected as role {identity['user']!r}, approved target role is "
            f"{APPROVED_TARGET['user']!r}"
        ])
    return {
        "database": identity["database"],
        "user": identity["user"],
        "host": parsed["host"],
        "port": parsed["port"],
        "server_addr": str(identity["server_addr"]),
        "verified": True,
    }


def run(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, type=Path,
                        help="run detail export to import")
    parser.add_argument("--expected-sha256", required=True,
                        help="sha256 of the whole file, verified before anything is read as data")
    parser.add_argument("--instrument",
                        help="instrument id the file must be for (default: the file's own)")
    parser.add_argument("--interval-minutes", type=int, default=1440,
                        help=f"grain being imported; one of {list(KRAKEN_INTERVALS)}")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and report the plan; write nothing")
    parser.add_argument("--on-conflict", choices=("reject", "overwrite"), default="reject",
                        help="what to do when a stored bar at the same key differs")
    parser.add_argument("--retire-grain-ambiguous", action="store_true",
                        help="delete stored rows that share these timestamps under another "
                             "or no grain instead of refusing")
    args = parser.parse_args(argv)

    path: Path = args.input
    if not path.is_file():
        raise UsageError(f"--input {path} is not a file")
    if args.interval_minutes not in KRAKEN_INTERVALS:
        raise UsageError(
            f"interval {args.interval_minutes}m is not a grain this warehouse "
            f"supports: {list(KRAKEN_INTERVALS)}")

    sha256 = verify_expected_sha256(path, args.expected_sha256)
    detail = load_detail(path)

    instrument_id = detail["instrument_id"]
    if args.instrument and args.instrument != instrument_id:
        raise ImportRefusal("instrument", [
            f"file is for {instrument_id!r}, import asked for {args.instrument!r}"
        ])
    if detail["interval_minutes"] != args.interval_minutes:
        raise ImportRefusal("instrument", [
            f"file declares {detail['interval_minutes']}m but the import is for "
            f"{args.interval_minutes}m; a different grain is a different series, "
            "not a slice of this one"
        ])
    instrument = get_crypto_instrument(instrument_id)
    if instrument is None:
        raise ImportRefusal("instrument", [
            f"{instrument_id!r} is not a catalogued crypto instrument"
        ])

    _candles, series = series_for_import(
        detail, instrument_id=instrument_id, interval_minutes=args.interval_minutes)
    crosscheck(detail, series, sha256=sha256)
    provenance = provenance_for(path, detail, sha256)
    target = guard_target(os.environ.get("DATABASE_URL", ""))

    engine = create_engine(os.environ["DATABASE_URL"])
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        plan = classify_series_rows(db, series)
        report = {
            "input": {
                "file": path.name,
                "sha256": sha256,
                "instrument_id": instrument_id,
                "interval_minutes": args.interval_minutes,
                "bar_count": len(plan["rows"]),
                "first_open_time": plan["rows"][0]["bar_timestamp"].isoformat(),
                "last_open_time": plan["rows"][-1]["bar_timestamp"].isoformat(),
                "fingerprint": series.fingerprint,
                "data_nature": detail["data_nature"],
            },
            "provenance": provenance,
            "target": target,
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
            "dry_run": bool(args.dry_run),
            "written": None,
        }
        if args.dry_run:
            return report

        try:
            written = store_series_bars(
                db,
                series,
                on_conflict=args.on_conflict,
                retire_grain_ambiguous=args.retire_grain_ambiguous,
            )
        except WarehouseImportRejected as exc:
            raise ImportRefusal("write", [exc.reason], exc.detail)

        note = MarketDataStorage(db).record_offline_provenance(
            instrument_id=instrument_id,
            provider_id=PROVIDER_ID,
            data_nature=detail["data_nature"],
            timezone=instrument.timezone,
            currency=instrument.currency.value,
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
    try:
        report = run(argv)
    except UsageError as exc:
        print(f"USAGE ERROR: {exc}", file=sys.stderr)
        return 2
    except ImportRefusal as exc:
        print(f"REFUSED [{exc.stage}] — nothing was written", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        if exc.detail:
            print(json.dumps({"detail": exc.detail}, indent=2, default=str),
                  file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
