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
