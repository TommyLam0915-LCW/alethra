#!/usr/bin/env python3
"""Import validated real daily bars into an approved test database.

Every check runs before a single row is written:

* the target must clear ``test_safety_gate.verify_test_database``, which
  connects and compares ``current_database()``/``current_user`` and rejects
  superuser roles — the gate's allowlist is not widened here;
* the payload's declared grain must be the daily interval this format can hold,
  because storage keys bars by calendar date;
* each bar must sit on the UTC midnight grid with finite, internally
  consistent OHLC values;
* the currency must match the instrument id, not a constant in this file.

The 2026-11-09 run did none of this: it took ``DATABASE_URL`` at face value,
wrote into a database the safety gate does not approve, and collapsed each bar
onto a date. If you are adapting this for a different grain, the storage
identity is ``(instrument_id, provider_id, interval_minutes, bar_timestamp)``
and ``MarketDataStorage.store_bars`` leaves ``interval_minutes`` NULL — a
sub-daily payload must go through a timestamp-exact writer instead.

Usage:
    TEST_MODE=true \
    DATABASE_URL="postgresql://USER:PASSWORD@localhost:5432/DB" \
    python3 scripts/import_real_data.py --json-path PATH --expected-sha256 HEX \
        --expected-instrument CRYPTO:KRAKEN:BTC-USD
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

DAILY_INTERVAL_MINUTES = 1440
REQUIRED_KEYS = ("instrument_id", "interval_minutes", "bars", "harvest_fingerprint")


class ImportRefusal(RuntimeError):
    """Raised when a precondition for writing is not met."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def load_payload(path: Path, expected_sha256: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
    if not path.exists():
        raise ImportRefusal(f"Payload {path} not found.")
    digest = sha256_file(path)
    if expected_sha256 and digest != expected_sha256.lower():
        raise ImportRefusal(
            f"Payload hash is {digest}, expected {expected_sha256.lower()}: "
            "the file is not the one that was validated."
        )
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ImportRefusal(f"Payload {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ImportRefusal("Payload must be a JSON object.")
    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise ImportRefusal(f"Payload is missing required keys: {missing}.")
    if not isinstance(payload["bars"], list) or not payload["bars"]:
        raise ImportRefusal("Payload contains no bars.")
    return payload, digest


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
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume_number,
        })
    return rows


def guard_target(database_url: str) -> Dict[str, Any]:
    """Run the repository's own test-database gate against the intended target."""
    if not database_url:
        raise ImportRefusal(
            "DATABASE_URL (or --database-url) is not set. This importer keeps no "
            "fallback target: an unnamed database is not an approved one."
        )
    plain = re.sub(r"^postgresql\+\w+://", "postgresql://", database_url)
    try:
        from test_safety_gate import TestDatabaseGateError, verify_test_database
    except ImportError as exc:
        raise ImportRefusal(
            f"The safety gate is unavailable ({exc.__class__.__name__}: {exc}); "
            "refusing to write without it."
        ) from exc
    try:
        return verify_test_database(plain)
    except TestDatabaseGateError as exc:
        raise ImportRefusal(f"Safety gate rejected the target: {exc}") from exc


def summarize_target(conn_info: Dict[str, Any]) -> str:
    return (
        f"database={conn_info.get('database')} user={conn_info.get('user')} "
        f"server={conn_info.get('server_addr')}:{conn_info.get('server_port')}"
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json-path", default="/tmp/live2b_warehouse_bars_1440m.json")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--expected-sha256", default=None)
    parser.add_argument("--expected-instrument", default=None)
    parser.add_argument("--provider-id", default="kraken")
    parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="Actually write. Without it the checks run and nothing is stored.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    payload, digest = load_payload(Path(args.json_path), args.expected_sha256)
    rows = validate_bars(payload, args.expected_instrument)
    instrument_id = payload["instrument_id"]
    currency = currency_of(instrument_id)

    conn_info = guard_target(args.database_url)

    from app.database import Base
    from app.providers.data_provider import OHLCVBar
    from app.services.market_data_storage import MarketDataStorage
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    print(f"Payload    : {args.json_path}")
    print(f"sha256     : {digest}")
    print(f"Instrument : {instrument_id} ({currency}, {DAILY_INTERVAL_MINUTES}m)")
    print(f"Bars       : {len(rows)}, range {rows[0]['date']} .. {rows[-1]['date']}")
    print(f"Fingerprint: {payload['harvest_fingerprint']}")
    print(f"Target     : {summarize_target(conn_info)}")

    if not args.confirm_write:
        print("\nDRY RUN: every precondition passed; nothing was written. "
              "Re-run with --confirm-write to import.")
        return 0

    engine = create_engine(args.database_url)
    Base.metadata.bind = engine
    session = sessionmaker(bind=engine)()
    try:
        stored = MarketDataStorage(session).store_bars(
            instrument_id=instrument_id,
            provider_id=args.provider_id,
            bars=[OHLCVBar(**row) for row in rows],
            data_nature="real",
            adjustment_status=None,
            timezone="UTC",
            currency=currency,
            provider_note=(
                f"Imported from {Path(args.json_path).name} "
                f"(sha256 {digest[:16]}, harvest {str(payload['harvest_fingerprint'])[:16]})"
            ),
            provider_note_zh="经由安全閘門核對目標後匯入的真實日線資料",
        )
        print(f"\nStored {stored} bars. Re-running upserts the same "
              f"(instrument, provider, midnight) rows rather than duplicating them; "
              "note that store_bars leaves interval_minutes NULL, so this lane is "
              "readable only as a daily series.")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ImportRefusal as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(2)
