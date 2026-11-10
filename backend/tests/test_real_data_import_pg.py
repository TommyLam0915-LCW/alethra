"""The offline importer refuses, or writes once (PostgreSQL only).

``scripts/import_real_data.py`` is the only way this deployment can put a
multi-year candle series into the warehouse without a venue request, so the
question this module answers is not "does it import" but "what does it refuse,
and did the database survive the refusal unchanged". Every refusal test
fingerprints the whole ``market_data_bars`` table before and after the call: a
digest that matches is proof of no new row, no updated row and no deleted row,
which an assertion on a row count cannot give.

The bars here are synthetic test fixtures. They reproduce the *shape* of a
Kraken daily export (decimal strings, UTC midnight opens, a fingerprint over
the completed series) so the gate has something to chew on, and their declared
``data_nature`` is ``synthetic`` for exactly that reason — the file never
claims a venue read it did not perform. Everything is written under a test-only
instrument id, kept clear of the live ``CRYPTO:KRAKEN:*`` series, and deleted
again after each test.

Run against a disposable PostgreSQL database whose URL uses the plain
``postgresql://`` scheme (the safety gate parses the URL itself and does not
recognise a SQLAlchemy-only scheme)::

    ./backend/scripts/run_tests.sh pg tests/test_real_data_import_pg.py
"""

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import make_url, select

if not os.environ.get("ALETHRA_TEST_DB", "").startswith("postgresql"):
    pytest.skip(
        "PostgreSQL not configured (run via scripts/run_tests.sh pg)",
        allow_module_level=True,
    )

from app.data.crypto_instruments import KRAKEN_BTC_USD
from app.models.market_data import MarketDataBar, MarketDataMetadata
from app.services.crypto_bar_harvest import (
    PROVIDER_ID,
    WarehouseImportRejected,
    store_series_bars,
)
from app.services.crypto_series import build_series
from scripts import import_real_data as importer
from scripts.import_real_data import ImportRefusal, UsageError, sha256_file
from tests.conftest import TestSession

SCRATCH_INSTRUMENT = "CRYPTO:KRAKEN:TST-IMPORT-1"
SCRATCH_DIR = Path(importer.__file__).resolve().parents[1] / "tests" / ".import_scratch"
DUMMY_PASSWORD = "dummy-password-never-used"

START = datetime(2026, 6, 1, tzinfo=timezone.utc)
DAYS = 20
#: After every bar closes, so nothing in the fixture is still forming.
FETCHED_AT = START + timedelta(days=DAYS + 1)


def daily_bars(count=DAYS, *, start=START, opens=None):
    """``count`` daily bars in the exporter's own shape: decimal strings."""
    bars = []
    for i in range(count):
        open_time = (opens[i] if opens else start + timedelta(days=i)).astimezone(timezone.utc)
        offset = Decimal(i)
        bars.append({
            "open_time": open_time.isoformat(),
            "open": str(Decimal("100.50") + offset),
            "high": str(Decimal("101.50") + offset),
            "low": str(Decimal("99.25") + offset),
            "close": str(Decimal("100.50") + offset),
            "volume": str(Decimal("1.75") + offset),
            "interval_minutes": 1440,
        })
    return bars


def payload(bars=None, *, interval_minutes=1440, fetched_at=FETCHED_AT,
            tampered=False, **claims):
    """A run detail export whose claims are computed from its own bytes.

    The declared fingerprint, ``bar_count`` and window are what
    :func:`build_series` says the bars *are*, so a test that wants a mismatch
    has to break one claim on purpose rather than get one for free.

    ``tampered`` keeps the claims of the *clean* file while writing the broken
    bars: a value the data gate itself rejects must not first make the
    reference series un-buildable, or the fixture is what fails, not the gate.
    """
    bars = daily_bars() if bars is None else bars
    detail = {
        "id": "test-run-1",
        "instrument_id": SCRATCH_INSTRUMENT,
        "interval_minutes": interval_minutes,
        "provider_id": PROVIDER_ID,
        "data_nature": "synthetic",
        "fetched_at": fetched_at.isoformat(),
        "dataset_integrity_ok": True,
        "bars": bars,
    }
    detail.update(claims)
    if "dataset_fingerprint" not in claims:
        reference = daily_bars(len(bars)) if tampered else bars
        series = series_of(detail, bars=reference,
                           interval_minutes=interval_minutes, fetched_at=fetched_at)
        detail.setdefault("dataset_fingerprint", series.fingerprint)
        detail.setdefault("bar_count", len(series.completed))
        if series.completed:
            detail.setdefault("first_open_time",
                              series.completed[0].open_time.isoformat())
            detail.setdefault("last_open_time",
                              series.completed[-1].open_time.isoformat())
    return {"detail": detail}


def series_of(detail, *, bars=None, interval_minutes=1440, fetched_at=FETCHED_AT):
    """The series the importer would build from these bars, without a file."""
    if "detail" in detail:
        detail = detail["detail"]
    candles = importer.build_raw_candles(
        {"bars": detail["bars"] if bars is None else bars},
        instrument_id=detail["instrument_id"], interval_minutes=interval_minutes)
    return build_series(
        instrument_id=detail["instrument_id"],
        interval_minutes=interval_minutes,
        rows=candles,
        now=fetched_at,
    )


def target_url():
    """This run's DATABASE_URL, in the scheme the safety gate accepts.

    Checked rather than repaired: a ``+psycopg2``/``+psycopg`` scheme is a
    driver hint the gate cannot parse, so every gate test in this module would
    refuse for a reason that has nothing to do with the code under test.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql://"):
        raise AssertionError(
            "DATABASE_URL must use the plain postgresql:// scheme for this "
            f"module (got {url.split('://')[0] if '://' in url else 'unset'!r}); "
            "the safety gate parses the URL itself and rejects a "
            "SQLAlchemy-only scheme")
    return url


COLUMNS = (
    MarketDataBar.id, MarketDataBar.instrument_id, MarketDataBar.provider_id,
    MarketDataBar.interval_minutes, MarketDataBar.bar_timestamp,
    MarketDataBar.bar_date, MarketDataBar.open, MarketDataBar.high,
    MarketDataBar.low, MarketDataBar.close, MarketDataBar.volume,
    MarketDataBar.volume_decimal, MarketDataBar.adjustment_factor,
    MarketDataBar.is_adjusted, MarketDataBar.fetched_at,
)


def bars_state():
    """(digest, row count) over every row of the warehouse.

    ``id`` is in the digest, so a row deleted and re-inserted with identical
    values still reads as a change — and it is one.
    """
    with TestSession() as db:
        rows = db.execute(
            select(*COLUMNS).order_by(
                MarketDataBar.instrument_id, MarketDataBar.provider_id,
                MarketDataBar.interval_minutes, MarketDataBar.bar_timestamp,
                MarketDataBar.id)
        ).all()
    digest = hashlib.sha256()
    for row in rows:
        digest.update(b"|".join(
            b"" if value is None else
            (repr(value) if isinstance(value, float) else str(value)).encode()
            for value in row
        ) + b"\n")
    return digest.hexdigest(), len(rows)


def scratch_bars():
    with TestSession() as db:
        return db.execute(
            select(MarketDataBar.bar_timestamp, MarketDataBar.interval_minutes,
                   MarketDataBar.close, MarketDataBar.volume_decimal)
            .where(MarketDataBar.instrument_id == SCRATCH_INSTRUMENT)
            .order_by(MarketDataBar.bar_timestamp, MarketDataBar.interval_minutes)
        ).all()


def purge_scratch():
    with TestSession() as db:
        for model in (MarketDataBar, MarketDataMetadata):
            db.query(model).filter(
                model.instrument_id == SCRATCH_INSTRUMENT).delete()
        db.commit()


@pytest.fixture
def scratch():
    purge_scratch()
    yield
    purge_scratch()


@pytest.fixture
def write_file():
    """Write exports inside this git worktree, where a source chain can exist."""
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    def _write(data=None, name="import-case", text=None):
        path = SCRATCH_DIR / f"{name}.json"
        path.write_text(json.dumps(data) if text is None else text)
        return path, sha256_file(path)

    yield _write
    shutil.rmtree(SCRATCH_DIR, ignore_errors=True)


@pytest.fixture
def no_connection(monkeypatch):
    """Fail the test if anything opens a database engine.

    The importer promises every file check finishes before it connects. A
    refusal that quietly connected first would still look correct in its
    output, so the promise is enforced by making the connection fatal.
    """
    def boom(*args, **kwargs):
        raise AssertionError("the importer opened a database connection")

    monkeypatch.setattr(importer, "create_engine", boom)


@pytest.fixture
def catalogue(monkeypatch):
    """Let the test-only instrument id pass the catalogue lookup.

    Only the lookup is substituted, with a real catalogue instrument behind it
    for the trading specs; the importer's instrument gate still runs and the
    rows still land under ``SCRATCH_INSTRUMENT``, not a live series.
    """
    monkeypatch.setattr(importer, "get_crypto_instrument",
                        lambda instrument_id: KRAKEN_BTC_USD)


def argv_for(path, sha, *extra, dry=False):
    args = ["--input", str(path), "--expected-sha256", sha]
    if dry:
        args.append("--dry-run")
    return args + list(extra)


def refuse(path, sha, *extra, dry=False):
    with pytest.raises(ImportRefusal) as info:
        importer.run(argv_for(path, sha, *extra, dry=dry))
    return info.value


# ------------------------------------------------------------------ hash, shape

def test_wrong_hash_is_refused_before_the_file_is_trusted(write_file, no_connection):
    path, sha = write_file(payload())
    before = bars_state()

    problem = refuse(path, "0" * 64)

    assert problem.stage == "hash"
    assert sha in problem.problems[0]
    assert bars_state() == before


def test_a_git_blob_sha_is_not_accepted_as_the_file_hash(write_file, no_connection):
    """A blob SHA is computed over a header plus the content: it can only ever
    differ from the SHA256 of the file, so it cannot stand in for it."""
    path, sha = write_file(payload())
    blob = subprocess.run(["git", "hash-object", str(path)],
                          capture_output=True, text=True).stdout.strip()
    assert len(blob) == 40 and blob != sha

    assert refuse(path, blob).stage == "hash"


def test_a_file_that_is_not_an_export_is_refused(write_file, no_connection):
    path, sha = write_file(text="{ this is not a detail export", name="broken")
    before = bars_state()

    assert refuse(path, sha).stage in ("read", "schema")
    assert bars_state() == before


def test_an_export_without_a_detail_block_is_refused(write_file, no_connection):
    path, sha = write_file({"bars": daily_bars(2)}, name="bare-array")

    assert refuse(path, sha).stage == "schema"


# ------------------------------------------------------------------------ data

@pytest.mark.parametrize("field,value,stage,fragment", [
    pytest.param("volume", "-1.5", "data", "bar[3].volume", id="negative-volume"),
    pytest.param("volume", "1.1234567890123", "data", "bar[3].volume",
                 id="volume-over-12-decimals"),
    pytest.param("volume", "1234567890123456789012345678901", "data", "bar[3].volume",
                 id="volume-over-30-digits"),
    pytest.param("volume", "NaN", "data", "bar[3].volume", id="volume-not-finite"),
    pytest.param("volume", None, "data", "bar[3].volume", id="volume-missing"),
    pytest.param("volume", "1e999", "data", "bar[3].volume",
                 id="volume-outside-double-range"),
    pytest.param("close", "0", "data", "bar[3].close", id="price-not-positive"),
    pytest.param("open", "not-a-number", "data", "bar[3].open", id="price-unparseable"),
    pytest.param("high", "98.00", "integrity", "invalid=1", id="high-below-low"),
    pytest.param("low", "200.00", "integrity", "invalid=1", id="low-above-high"),
], ids=str)
def test_an_illegal_bar_is_refused_before_any_connection(
        write_file, no_connection, catalogue, field, value, stage, fragment):
    """OHLC inversions are caught by the same integrity gate a venue read
    passes, so a file cannot arrive in the warehouse by a softer standard."""
    bars = daily_bars()
    bars[3][field] = value
    path, sha = write_file(payload(bars, tampered=True))
    before = bars_state()

    problem = refuse(path, sha)

    assert problem.stage == stage
    assert any(fragment in p for p in problem.problems), problem.problems
    assert bars_state() == before


def test_a_float_instead_of_a_decimal_string_is_refused(
        write_file, no_connection, catalogue):
    """A JSON float has already lost the venue's digits; taking it launders a
    rounded price into a decimal that looks exact."""
    bars = daily_bars()
    bars[1]["close"] = 101.5
    path, sha = write_file(payload(bars, tampered=True))

    problem = refuse(path, sha)

    assert problem.stage == "data"
    assert any("expected a decimal string" in p for p in problem.problems)


def test_a_missing_bar_key_is_refused(write_file, no_connection, catalogue):
    bars = daily_bars()
    del bars[2]["low"]
    path, sha = write_file(payload(bars, tampered=True))

    problem = refuse(path, sha)

    assert problem.stage == "data"
    assert any("bar[2]: missing key(s) low" in p for p in problem.problems)


def test_a_naive_timestamp_is_refused(write_file, no_connection, catalogue):
    """The grain is only defined against a stated zone."""
    bars = daily_bars()
    bars[0]["open_time"] = bars[0]["open_time"].replace("+00:00", "")
    path, sha = write_file(payload(bars, tampered=True))

    problem = refuse(path, sha)

    assert problem.stage == "data"
    assert any("naive timestamp" in p for p in problem.problems)


def test_a_bar_of_another_grain_inside_a_daily_file_is_refused(
        write_file, no_connection, catalogue):
    bars = daily_bars()
    bars[4]["interval_minutes"] = 240
    path, sha = write_file(payload(bars, tampered=True))

    problem = refuse(path, sha)

    assert problem.stage == "data"
    assert any("interval_minutes is 240" in p for p in problem.problems)


def test_an_empty_file_is_refused(write_file, no_connection, catalogue):
    path, sha = write_file(payload(
        [], dataset_fingerprint="0" * 64, bar_count=0,
        first_open_time=None, last_open_time=None))

    problem = refuse(path, sha)

    assert problem.stage == "data"
    assert "file contains no bars" in problem.problems[0]


# ---------------------------------------------------------------------- grains

@pytest.mark.parametrize("interval", ["360", "7", "0", "-60"])
def test_an_unsupported_grain_is_rejected_not_downsampled(
        interval, write_file, no_connection):
    """1440m bars are not a 360m series, and an unsupported grain must not be
    quietly rounded to the nearest grain this warehouse knows."""
    path, sha = write_file(payload())
    args = argv_for(path, sha, "--interval-minutes", interval)
    before = bars_state()

    with pytest.raises(UsageError):
        importer.run(args)
    assert importer.main(args) == 2
    assert bars_state() == before


def test_intraday_bars_under_a_daily_grain_are_refused_not_date_truncated(
        write_file, no_connection, catalogue):
    """The specific failure this rule exists for: 06:30 and 12:00 opens
    collapsed onto their date and written into the daily series as candles for
    days that were never observed."""
    bars = daily_bars(4, opens=[
        START, START + timedelta(hours=6, minutes=30),
        START + timedelta(hours=12), START + timedelta(hours=18, minutes=30)])
    bars += daily_bars(1, start=START + timedelta(days=1))
    path, sha = write_file(payload(bars))
    before = bars_state()

    problem = refuse(path, sha)

    assert problem.stage == "integrity"
    assert any("off_grid=3" in p for p in problem.problems), problem.problems
    assert bars_state() == before
    assert scratch_bars() == []


def test_a_file_for_one_grain_imported_as_another_is_refused(write_file, no_connection):
    four_hour = [{**bar, "interval_minutes": 240,
                  "open_time": (START + timedelta(hours=4 * i)).isoformat()}
                 for i, bar in enumerate(daily_bars(6))]
    data = payload(four_hour, interval_minutes=240,
                   fetched_at=START + timedelta(days=2))
    path, sha = write_file(data)

    problem = refuse(path, sha, "--interval-minutes", "1440")

    assert problem.stage == "instrument"
    assert "a different grain is a different series" in problem.problems[0]


def test_an_instrument_the_importer_was_asked_for_is_not_swapped(
        write_file, no_connection):
    path, sha = write_file(payload())

    problem = refuse(path, sha, "--instrument", "CRYPTO:KRAKEN:BTC-USD")

    assert problem.stage == "instrument"
    assert SCRATCH_INSTRUMENT in problem.problems[0]


def test_an_uncatalogued_instrument_is_refused(write_file, no_connection):
    """No catalogue entry, no write — even with bars that pass every other
    check. The substitution in :func:`catalogue` is what makes this reachable."""
    path, sha = write_file(payload())
    before = bars_state()

    problem = refuse(path, sha)

    assert problem.stage == "instrument"
    assert "not a catalogued crypto instrument" in problem.problems[0]
    assert bars_state() == before


# ------------------------------------------------------------------- integrity

def test_bytes_that_do_not_match_the_declared_fingerprint_are_refused(
        write_file, no_connection, catalogue):
    """The file describes a series; the bars are a different series. Either
    way the declared fingerprint is the claim this gate checks."""
    bars = daily_bars()
    data = payload(bars)
    tampered = dict(bars[5])
    tampered["close"] = "999.99"
    data["detail"]["bars"][5] = tampered
    path, sha = write_file(data)

    problem = refuse(path, sha)

    assert problem.stage == "integrity"
    assert any("bytes are not the series" in p for p in problem.problems)


@pytest.mark.parametrize("claim,value", [
    pytest.param("bar_count", 99, id="bar-count"),
    pytest.param("first_open_time", "2020-01-01T00:00:00+00:00", id="first-open"),
    pytest.param("last_open_time", "2020-01-02T00:00:00+00:00", id="last-open"),
    pytest.param("dataset_integrity_ok", False, id="integrity-flag"),
    pytest.param("provider_id", "binance", id="other-provider"),
    pytest.param("data_nature", "real", id="nature-outside-vocabulary"),
], ids=str)
def test_a_claim_the_bytes_do_not_support_is_refused(
        write_file, no_connection, catalogue, claim, value):
    path, sha = write_file(payload(**{claim: value}))
    before = bars_state()

    problem = refuse(path, sha)

    assert problem.stage == "integrity"
    assert any(claim in p or "integrity" in p or "provider" in p or "vocabulary" in p
               for p in problem.problems), problem.problems
    assert bars_state() == before


def test_a_forming_candle_is_refused_however_the_file_labels_it(
        write_file, no_connection, catalogue):
    """``live`` says where the numbers came from, not that the newest bucket
    closed. A daily file read minutes after midnight cannot contain that day."""
    fetched_at = START + timedelta(days=DAYS - 1) + timedelta(minutes=5)
    path, sha = write_file(payload(data_nature="live", fetched_at=fetched_at))
    before = bars_state()

    problem = refuse(path, sha)

    assert problem.stage == "integrity"
    assert any("still forming" in p for p in problem.problems)
    assert bars_state() == before


def test_a_gapped_series_is_refused_rather_than_interpolated(
        write_file, no_connection, catalogue):
    bars = daily_bars(6)
    path, sha = write_file(payload(bars[:3] + bars[4:]))

    problem = refuse(path, sha)

    assert problem.stage == "integrity"
    assert any("gaps=1" in p for p in problem.problems)


def test_a_duplicated_timestamp_is_refused(write_file, no_connection, catalogue):
    bars = daily_bars(6)
    path, sha = write_file(payload(bars[:4] + [dict(bars[3])]))

    problem = refuse(path, sha)

    assert problem.stage == "integrity"
    assert any("duplicates=1" in p for p in problem.problems)


def test_out_of_order_bars_are_refused(write_file, no_connection, catalogue):
    bars = daily_bars(6)
    bars[0], bars[3] = bars[3], bars[0]
    path, sha = write_file(payload(bars))

    problem = refuse(path, sha)

    assert problem.stage == "integrity"
    assert any("out of time order" in p for p in problem.problems)


# ---------------------------------------------------------------------- target

def test_a_blacklisted_database_is_refused_without_connecting(monkeypatch, no_connection):
    """An allow-list violation must never reach the server, let alone write."""
    url = str(make_url(target_url()).set(database="postgres", password=DUMMY_PASSWORD))
    monkeypatch.setenv("DATABASE_URL", url)
    before = bars_state()

    with pytest.raises(ImportRefusal) as info:
        importer.guard_target(url)

    text = " ".join(info.value.problems)
    assert info.value.stage == "target"
    assert "production blacklist" in text
    assert DUMMY_PASSWORD not in text and "@" not in text
    assert bars_state() == before


def test_a_database_outside_the_whitelist_is_refused_without_connecting(no_connection):
    url = str(make_url(target_url()).set(database="alethra_unknown_db",
                                        password=DUMMY_PASSWORD))

    with pytest.raises(ImportRefusal) as info:
        importer.guard_target(url)

    text = " ".join(info.value.problems)
    assert info.value.stage == "target"
    assert "not in the test whitelist" in text
    assert DUMMY_PASSWORD not in text


def test_without_test_mode_the_gate_refuses(monkeypatch):
    monkeypatch.delenv("TEST_MODE", raising=False)

    with pytest.raises(ImportRefusal) as info:
        importer.guard_target(target_url())

    assert info.value.stage == "target"
    assert "TEST_MODE" in info.value.problems[0]


def test_a_database_that_answers_under_another_name_is_refused(monkeypatch):
    """Connected, then refused: the approval names a database, and a database
    that merely looks right in the URL gets nothing written to it."""
    monkeypatch.setitem(importer.APPROVED_TARGET, "database", "alethra_test")
    before = bars_state()

    with pytest.raises(ImportRefusal) as info:
        importer.guard_target(target_url())

    assert info.value.stage == "target"
    assert "approved target is" in info.value.problems[0]
    assert bars_state() == before


def test_another_role_in_the_same_database_is_refused(monkeypatch):
    monkeypatch.setitem(importer.APPROVED_TARGET, "user", "alethra_other_role")
    before = bars_state()

    with pytest.raises(ImportRefusal) as info:
        importer.guard_target(target_url())

    assert info.value.stage == "target"
    assert "approved target role is" in info.value.problems[0]
    assert bars_state() == before


def test_an_unreachable_whitelisted_database_is_refused_value_free():
    """Driver connection errors echo the conninfo, and the conninfo carries the
    password; only the exception type is allowed out."""
    url = make_url(target_url()).set(database="alethra_test")

    with pytest.raises(ImportRefusal) as info:
        importer.guard_target(str(url))

    text = " ".join(info.value.problems)
    assert info.value.stage == "target"
    assert "cannot verify the target database" in text
    assert url.password not in text and "//" not in text and "@" not in text


def test_the_importer_never_falls_back_to_application_config(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(UsageError):
        importer.guard_target("")


# ------------------------------------------------------------------- provenance

def test_a_file_outside_a_git_worktree_has_no_source_chain(
        tmp_path, catalogue, no_connection):
    path = tmp_path / "outside.json"
    path.write_text(json.dumps(payload()))
    before = bars_state()

    problem = refuse(path, sha256_file(path))

    assert problem.stage == "provenance"
    assert "cannot be recorded as repo + commit + path" in problem.problems[0]
    assert bars_state() == before


def test_an_overlong_source_chain_is_refused_rather_than_truncated():
    """``provider_note`` holds 500 characters. Shortening the chain silently
    would store evidence that no longer says where the numbers came from."""
    data = payload(dataset_fingerprint="f" * 600)
    path = SCRATCH_DIR / "overlong.json"
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    try:
        with pytest.raises(ImportRefusal) as info:
            importer.provenance_for(path, data["detail"], sha256_file(path))
        assert "over provider_note" in str(info.value)
    finally:
        path.unlink(missing_ok=True)


def test_provenance_names_the_file_it_read(write_file):
    path, sha = write_file(payload(), name="provenance-case")

    note = importer.provenance_for(path, importer.load_detail(path), sha)

    assert note["kind"] == "offline_file_import"
    assert note["file_sha256"] == sha
    assert note["path"].endswith(".import_scratch/provenance-case.json")
    assert note["limit"] == importer.CHAIN_LIMIT
    assert note["commit"] == "untracked", note["commit"]
    assert str(SCRATCH_DIR) not in json.dumps(note), "a device-specific path was stored"
    assert len(json.dumps(note, separators=(",", ":"), sort_keys=True)) <= 500


# ----------------------------------------------------------------------- writes

def test_a_clean_file_writes_once_and_labels_itself_synthetically(
        scratch, write_file, catalogue):
    """The one case that has to reach the warehouse, so nothing else in this
    module is testing a gate no file has ever passed."""
    before = bars_state()
    path, sha = write_file(payload())

    report = importer.run(argv_for(path, sha))

    assert report["written"]["bars_new"] == DAYS
    assert report["written"]["bars_conflicting"] == 0
    assert report["written"]["fingerprint"] == report["input"]["fingerprint"]
    assert report["target"]["database"] == importer.APPROVED_TARGET["database"]
    assert report["target"]["verified"] is True
    stored = scratch_bars()
    assert len(stored) == DAYS
    assert {row[1] for row in stored} == {1440}, "the grain is part of the row"
    assert all(row[3] is not None for row in stored), "volume belongs in volume_decimal"
    assert stored[0][2] == 100.50 and stored[0][3] == Decimal("1.75")
    assert stored[-1][2] == 100.50 + DAYS - 1
    assert stored[-1][3] == Decimal("1.75") + DAYS - 1
    with TestSession() as db:
        rows = db.execute(select(MarketDataMetadata).where(
            MarketDataMetadata.instrument_id == SCRATCH_INSTRUMENT)).scalars().all()
    assert len(rows) == 1
    assert rows[0].data_nature == "synthetic"
    assert rows[0].adjustment_status == "not_applicable"
    assert importer.CHAIN_LIMIT in rows[0].provider_note
    assert sha in rows[0].provider_note
    assert bars_state()[1] == before[1] + DAYS


def test_identical_reimport_stores_nothing_twice(scratch, write_file, catalogue):
    path, sha = write_file(payload())
    first = importer.run(argv_for(path, sha))
    after_first = bars_state()
    stored_first = scratch_bars()

    second = importer.run(argv_for(path, sha))

    assert second["written"]["bars_new"] == 0
    assert second["written"]["bars_updated"] == 0
    assert second["written"]["bars_already_stored"] == DAYS
    assert second["plan"]["would_insert"] == 0
    assert second["plan"]["already_stored_identical"] == DAYS
    assert second["written"]["fingerprint"] == first["written"]["fingerprint"]
    assert bars_state() == after_first, "a re-import rewrote stored rows"
    assert scratch_bars() == stored_first

    with TestSession() as db:
        direct = store_series_bars(db, series_of(payload()))
    assert direct["bars_new"] == 0 and direct["bars_updated"] == 0
    assert bars_state() == after_first


def test_a_dry_run_reports_the_plan_and_changes_nothing(scratch, write_file, catalogue):
    before = bars_state()
    path, sha = write_file(payload())

    report = importer.run(argv_for(path, sha, dry=True))

    assert report["dry_run"] is True
    assert report["written"] is None
    assert report["plan"]["would_insert"] == DAYS
    assert report["plan"]["conflicting"] == 0
    assert report["plan"]["grain_ambiguous"] == 0
    assert bars_state() == before
    assert scratch_bars() == []


def test_a_conflicting_stored_bar_is_refused_by_default_and_never_overwritten(
        scratch, write_file, catalogue):
    """The warehouse already holds this key and the file disagrees: the default
    is to refuse the whole import, not to prefer whichever arrived second."""
    path, sha = write_file(payload())
    importer.run(argv_for(path, sha))
    before = bars_state()
    stored_before = scratch_bars()

    changed = daily_bars()
    changed[7]["close"] = "107.77"
    conflict_path, conflict_sha = write_file(payload(changed), name="conflict")

    problem = refuse(conflict_path, conflict_sha)

    assert problem.stage == "write"
    assert "1 stored bar(s) at the same key hold different values" in problem.problems[0]
    assert problem.detail["conflict_count"] == 1
    assert bars_state() == before, "a refused conflict still modified the table"
    assert scratch_bars() == stored_before

    plan = importer.run(argv_for(conflict_path, conflict_sha, dry=True))
    assert plan["plan"]["conflicting"] == 1
    assert plan["plan"]["already_stored_identical"] == DAYS - 1
    example = plan["plan"]["conflicting_examples"][0]
    assert example["incoming"][3] == "107.77"
    assert example["stored"][3] != "107.77"
    assert bars_state() == before

    with TestSession() as db:
        with pytest.raises(WarehouseImportRejected):
            store_series_bars(db, series_of(payload(changed)))
    assert bars_state() == before

    overwritten = importer.run(
        argv_for(conflict_path, conflict_sha, "--on-conflict", "overwrite"))
    assert overwritten["written"]["bars_updated"] == 1
    assert overwritten["written"]["bars_new"] == 0
    updated = [row for row in scratch_bars()
               if row[0] == START.replace(tzinfo=None) + timedelta(days=7)]
    assert len(updated) == 1 and updated[0][2] == 107.77
    # The refusal tests above lean on this digest staying put, so it has to be
    # shown to move for an in-place update with no row added and none removed.
    assert bars_state() != before, "the digest missed an in-place update"


def test_stored_rows_of_another_or_missing_grain_refuse_until_retired(
        scratch, write_file, catalogue):
    """Rows sharing the clock time under another or no grain do not collide on
    the unique key, so an import would store one bar twice and double the
    series for every grain-blind reader. Refused until retired by name."""
    path, sha = write_file(payload())
    importer.run(argv_for(path, sha))

    with TestSession() as db:
        target = db.execute(select(MarketDataBar).where(
            MarketDataBar.instrument_id == SCRATCH_INSTRUMENT,
            MarketDataBar.bar_timestamp == START.replace(tzinfo=None) + timedelta(days=3))
        ).scalar_one()
        db.add(MarketDataBar(
            instrument_id=SCRATCH_INSTRUMENT, provider_id=PROVIDER_ID,
            interval_minutes=None, bar_date=target.bar_date,
            bar_timestamp=target.bar_timestamp,
            open=1.0, high=2.0, low=0.5, close=1.5,
            volume=6252, volume_decimal=None, is_adjusted="raw",
            fetched_at=target.fetched_at))
        db.commit()
    ambiguous_state = bars_state()

    problem = refuse(path, sha)

    assert problem.stage == "write"
    assert "grain" in problem.problems[0]
    assert bars_state() == ambiguous_state

    plan = importer.run(argv_for(path, sha, dry=True))
    assert plan["plan"]["grain_ambiguous"] == 1
    assert plan["plan"]["would_insert"] == 0

    retired = importer.run(argv_for(path, sha, "--retire-grain-ambiguous"))
    assert retired["written"]["bars_grain_ambiguous_retired"] == 1
    assert retired["written"]["bars_new"] == 0
    stored = scratch_bars()
    assert len(stored) == len({row[0] for row in stored}) == DAYS
    assert {row[1] for row in stored} == {1440}
    assert bars_state() != ambiguous_state, "the digest missed a deleted row"
