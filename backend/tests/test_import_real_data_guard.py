"""Wrong-target and payload refusals for scripts/import_real_data.py.

The 2026-11-09 import wrote "real" bars into a database the repository's own
safety gate does not approve, after flattening each bar onto a calendar date.
These tests run the shipped importer and assert it refuses first — the point is
the refusal, not a successful import. No database is written by anything here.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "import_real_data", REPO_ROOT / "scripts" / "import_real_data.py"
)
assert _spec and _spec.loader
importer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(importer)

INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"
DAY_ONE = datetime(2026, 1, 1, tzinfo=timezone.utc)

# The database the 2026-11-09 run actually wrote into. It is not in the safety
# gate's allowlist, and these tests exist to keep that a refusal rather than a
# warning — widening the allowlist to make them pass would delete the guard.
ISOLATED_20261109_URL = (
    "postgresql+psycopg://alethra_test:not-a-real-secret@localhost:5432/"
    "alethra_test_isolated_20261109"
)
PRODUCTION_URL = "postgresql://alethra_test:not-a-real-secret@localhost:5432/ai_investment_lab"
UNREACHABLE_ALLOWED_URL = "postgresql://nobody:noop@localhost:5432/ai_investment_lab_test"


def daily_rows(days: int, seconds_off: int = 0) -> List[List[Any]]:
    return [
        [(DAY_ONE.toordinal() - 719529 + i) * 86400 + seconds_off,
         "100.5", "101.5", "99.5", "100.75", "12.5"]
        for i in range(days)
    ]


def payload(**overrides) -> Dict[str, Any]:
    base = {
        "instrument_id": INSTRUMENT,
        "interval_minutes": 1440,
        "harvest_fingerprint": "sha256:fixture",
        "currency": "USD",
        "bars": daily_rows(3),
    }
    base.update(overrides)
    return base


def write_payload(tmp_path: Path, data: Dict[str, Any]) -> Path:
    path = tmp_path / "bars.json"
    path.write_text(json.dumps(data))
    return path


# -- target refusal ----------------------------------------------------------

def test_refuses_the_database_the_gate_does_not_approve(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_MODE", "true")
    with pytest.raises(importer.ImportRefusal, match="not in the test whitelist"):
        importer.guard_target(ISOLATED_20261109_URL)


def test_refuses_the_production_named_database(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_MODE", "true")
    with pytest.raises(importer.ImportRefusal, match="production blacklist"):
        importer.guard_target(PRODUCTION_URL)


def test_refuses_when_no_target_is_named():
    with pytest.raises(importer.ImportRefusal, match="no fallback target"):
        importer.guard_target("")


def test_refuses_a_whitelisted_name_that_cannot_be_identity_verified(monkeypatch):
    """The gate connects and compares current_database()/current_user(); a target
    it cannot reach or verify is a refusal, not a warning."""
    monkeypatch.setenv("TEST_MODE", "true")
    with pytest.raises(importer.ImportRefusal, match="Safety gate rejected the target"):
        importer.guard_target(UNREACHABLE_ALLOWED_URL)


def test_refuses_when_test_mode_is_not_declared(monkeypatch):
    monkeypatch.delenv("TEST_MODE", raising=False)
    with pytest.raises(importer.ImportRefusal, match="TEST_MODE"):
        importer.guard_target(ISOLATED_20261109_URL)


def test_gate_receives_a_driver_free_url(monkeypatch):
    """psycopg2 cannot parse the SQLAlchemy ``+psycopg`` scheme, so the importer
    normalises it before handing the URL to the gate."""
    seen: List[str] = []

    def fake_verify(url):
        seen.append(url)
        raise importer.ImportRefusal("stop after capture")

    monkeypatch.setenv("TEST_MODE", "true")
    import test_safety_gate
    monkeypatch.setattr(test_safety_gate, "verify_test_database", fake_verify)
    with pytest.raises(importer.ImportRefusal, match="stop after capture"):
        importer.guard_target(ISOLATED_20261109_URL)
    assert seen == ["postgresql://alethra_test:not-a-real-secret@localhost:5432/alethra_test_isolated_20261109"]


# -- payload refusal: no date squashing -------------------------------------

def test_refuses_to_squash_intraday_bars_onto_a_date(tmp_path):
    path = write_payload(tmp_path, payload(interval_minutes=60, bars=daily_rows(6)))
    loaded, _ = importer.load_payload(path)
    with pytest.raises(importer.ImportRefusal, match="only accepts 1440m daily bars"):
        importer.validate_bars(loaded, None)


def test_refuses_a_daily_bar_that_is_not_on_the_midnight_grid(tmp_path):
    path = write_payload(tmp_path, payload(bars=daily_rows(3, seconds_off=8 * 3600)))
    loaded, _ = importer.load_payload(path)
    with pytest.raises(importer.ImportRefusal, match="not on the UTC midnight grid"):
        importer.validate_bars(loaded, None)


def test_refuses_a_repeated_or_backwards_timestamp(tmp_path):
    rows = daily_rows(3)
    rows[2] = rows[1]
    path = write_payload(tmp_path, payload(bars=rows))
    loaded, _ = importer.load_payload(path)
    with pytest.raises(importer.ImportRefusal, match="does not advance"):
        importer.validate_bars(loaded, None)


def test_refuses_a_payload_for_another_instrument(tmp_path):
    path = write_payload(tmp_path, payload(instrument_id="CRYPTO:KRAKEN:ETH-USD"))
    loaded, _ = importer.load_payload(path)
    with pytest.raises(importer.ImportRefusal, match="expected CRYPTO:KRAKEN:BTC-USD"):
        importer.validate_bars(loaded, INSTRUMENT)


def test_refuses_a_currency_that_mismatches_the_instrument(tmp_path):
    path = write_payload(tmp_path, payload(currency="EUR"))
    loaded, _ = importer.load_payload(path)
    with pytest.raises(importer.ImportRefusal, match="quoted in USD"):
        importer.validate_bars(loaded, None)


def test_currency_is_taken_from_the_instrument_not_a_constant():
    assert importer.currency_of("CRYPTO:KRAKEN:BTC-USD") == "USD"
    assert importer.currency_of("CRYPTO:KRAKEN:ETH-BTC") == "BTC"
    with pytest.raises(importer.ImportRefusal, match="no quote currency"):
        importer.currency_of("CRYPTO:KRAKEN:BTCUSD")


@pytest.mark.parametrize(
    "index,label,value",
    [(0, "high", "NaN"), (1, "low", "Infinity"), (2, "open", "0"), (3, "close", None)],
)
def test_refuses_non_finite_or_non_positive_prices(tmp_path, index, label, value):
    rows = daily_rows(4)
    position = {"open": 1, "high": 2, "low": 3, "close": 4}[label]
    rows[index][position] = value
    path = write_payload(tmp_path, payload(bars=rows))
    loaded, _ = importer.load_payload(path)
    with pytest.raises(importer.ImportRefusal, match=f"Bar {index} {label}"):
        importer.validate_bars(loaded, None)


def test_refuses_a_bar_whose_range_excludes_its_close(tmp_path):
    rows = daily_rows(2)
    rows[1][2] = "100.0"  # high below close 100.75
    path = write_payload(tmp_path, payload(bars=rows))
    loaded, _ = importer.load_payload(path)
    with pytest.raises(importer.ImportRefusal, match="internally inconsistent"):
        importer.validate_bars(loaded, None)


def test_refuses_a_negative_volume(tmp_path):
    rows = daily_rows(2)
    rows[0][5] = "-1"
    path = write_payload(tmp_path, payload(bars=rows))
    loaded, _ = importer.load_payload(path)
    with pytest.raises(importer.ImportRefusal, match="volume is -1.0"):
        importer.validate_bars(loaded, None)


def test_accepts_a_null_volume(tmp_path):
    rows = daily_rows(2)
    rows[1][5] = None
    path = write_payload(tmp_path, payload(bars=rows))
    loaded, _ = importer.load_payload(path)
    assert importer.validate_bars(loaded, None)[1]["volume"] is None


# -- provenance checks -------------------------------------------------------

def test_refuses_a_payload_whose_hash_was_not_the_one_validated(tmp_path):
    path = write_payload(tmp_path, payload())
    with pytest.raises(importer.ImportRefusal, match="is not the one that was validated"):
        importer.load_payload(path, expected_sha256="0" * 64)


def test_reports_the_hash_it_computed(tmp_path):
    path = write_payload(tmp_path, payload())
    _, digest = importer.load_payload(path)
    assert len(digest) == 64
    importer.load_payload(path, expected_sha256=digest)


def test_refuses_a_payload_missing_provenance(tmp_path):
    data = payload()
    del data["harvest_fingerprint"]
    path = write_payload(tmp_path, data)
    with pytest.raises(importer.ImportRefusal, match="missing required keys"):
        importer.load_payload(path)


def test_refuses_an_empty_series(tmp_path):
    path = write_payload(tmp_path, payload(bars=[]))
    with pytest.raises(importer.ImportRefusal, match="contains no bars"):
        importer.load_payload(path)


# -- end to end: refusal happens before any engine is built ------------------

def test_main_refuses_before_constructing_a_session(tmp_path, monkeypatch):
    """Payload first, target second: an unapproved target must be reached only
    with a validated payload, and a refused run must never open a session."""
    monkeypatch.setenv("TEST_MODE", "true")
    path = write_payload(tmp_path, payload())
    called: List[str] = []
    monkeypatch.setattr(
        importer, "guard_target",
        lambda url: called.append(url) or (_ for _ in ()).throw(
            importer.ImportRefusal("Safety gate rejected the target: not in the test whitelist")
        ),
    )
    with pytest.raises(importer.ImportRefusal, match="not in the test whitelist"):
        importer.main([
            "--json-path", str(path),
            "--database-url", ISOLATED_20261109_URL,
            "--expected-instrument", INSTRUMENT,
            "--confirm-write",
        ])
    assert called == [ISOLATED_20261109_URL]


def test_dry_run_still_refuses_an_unapproved_target(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TEST_MODE", "true")
    path = write_payload(tmp_path, payload())
    with pytest.raises(importer.ImportRefusal, match="not in the test whitelist"):
        importer.main([
            "--json-path", str(path),
            "--database-url", ISOLATED_20261109_URL,
            "--expected-instrument", INSTRUMENT,
        ])
    assert "Stored" not in capsys.readouterr().out


# -- grain: the importer names 1440 on the rows it writes -------------------
#
# Storage keys a bar by (instrument, provider, interval_minutes, bar_timestamp),
# and one midnight can already hold a four-hour bar under that key. An importer
# that writes 1440 and one that writes nothing look identical in a code review;
# only the argument actually handed to ``store_bars`` distinguishes them. These
# cases run ``main`` and read that argument.

GRAIN_ARGS = [
    "--database-url", UNREACHABLE_ALLOWED_URL,
    "--expected-instrument", INSTRUMENT,
    "--confirm-write",
]


@pytest.fixture
def storage_spy(monkeypatch):
    """Let ``main`` reach ``store_bars``, and record what it was told.

    Nothing connects: the target gate, the engine and the storage write are all
    replaced. ``UNREACHABLE_ALLOWED_URL`` is only the string handed to the stub
    gate, which the fixture still records so the gate stays on this path. The
    app's shared ``Base.metadata`` is pre-set through monkeypatch so the bind
    ``main`` assigns is deleted again on teardown.
    """
    import sqlalchemy
    import sqlalchemy.orm
    from app.database import Base
    from app.services.market_data_storage import MarketDataStorage

    seen: Dict[str, Any] = {"gate_urls": [], "calls": [], "raises": None,
                            "closed": False}

    def fake_guard(url):
        seen["gate_urls"].append(url)
        return {"database": "ai_investment_lab_test", "user": "alethra_test",
                "server_addr": "localhost", "server_port": 5432}

    def record(self, **kwargs):
        seen["calls"].append(kwargs)
        if seen["raises"] is not None:
            raise seen["raises"]
        return len(kwargs["bars"])

    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setattr(importer, "guard_target", fake_guard)
    monkeypatch.setattr(Base.metadata, "bind", None, raising=False)
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda url: object())
    monkeypatch.setattr(
        sqlalchemy.orm, "sessionmaker",
        lambda **kwargs: (lambda: type(
            "_Session", (), {"close": lambda self: seen.__setitem__("closed", True)}
        )()),
    )
    monkeypatch.setattr(MarketDataStorage, "store_bars", record)
    return seen


def test_importer_names_the_daily_grain_on_the_write(storage_spy, tmp_path):
    path = write_payload(tmp_path, payload())
    assert importer.main(["--json-path", str(path), *GRAIN_ARGS]) == 0

    assert len(storage_spy["calls"]) == 1, storage_spy["calls"]
    assert storage_spy["calls"][0]["interval_minutes"] == 1440
    assert storage_spy["gate_urls"] == [UNREACHABLE_ALLOWED_URL], \
        "the target gate is not on the storage path any more"


def test_importer_grain_constant_matches_the_storage_service():
    """The importer keeps its own 1440 rather than importing the service's.

    Two literals for one key can drift apart, and a drift would re-key every
    imported bar rather than upserting it, so the agreement is asserted here.
    """
    from app.services.market_data_storage import DAILY_INTERVAL_MINUTES as service_grain

    assert importer.DAILY_INTERVAL_MINUTES == service_grain


def test_grain_conflict_refuses_the_import_instead_of_rewriting_the_row(
    storage_spy, tmp_path, capsys
):
    from app.services.market_data_storage import GrainConflictError

    storage_spy["raises"] = GrainConflictError(
        "2026-01-01T00:00:00+00:00 already stores interval_minutes=240 for "
        "CRYPTO:KRAKEN:BTC-USD/kraken"
    )
    path = write_payload(tmp_path, payload())

    with pytest.raises(importer.ImportRefusal, match="another grain") as exc:
        importer.main(["--json-path", str(path), *GRAIN_ARGS])

    assert "Nothing was written" in str(exc.value)
    assert "does not re-stamp" in str(exc.value)
    assert isinstance(exc.value.__cause__, GrainConflictError)
    assert storage_spy["closed"], "the refused run left its session open"
    assert "Stored" not in capsys.readouterr().out
