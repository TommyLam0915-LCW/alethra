"""Both CLI importer entries must run one safety flow, not two similar ones.

``scripts/import_real_data.py`` is a compatibility entry onto
``backend/scripts/import_real_data.py``. That only holds if the root file really
delegates: an older copy of it carried its own validation and wrote through
``MarketDataStorage`` directly, so choosing it skipped the grain check, the
identity check and the write contract. These tests pin the delegation three ways
— structurally (the root module holds no check of its own), behaviourally (each
entry run as a script produces byte-identical output for the same refusal), and
by ordering (hash, then content, then the safety gate, then the session).

Nothing here writes: every scenario refuses at or before the gate, so no
database is needed and PostgreSQL may be down.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_ENTRY = REPO_ROOT / "scripts" / "import_real_data.py"
CORE_ENTRY = REPO_ROOT / "backend" / "scripts" / "import_real_data.py"
CORE_MODULE_NAME = "alethra_import_real_data_core"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


root = load(ROOT_ENTRY, "import_entry_root")
# The shim exec'd the core under this name; that instance is the one it uses.
core = sys.modules[CORE_MODULE_NAME]

INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"
DAY_ONE = datetime(2026, 1, 1, tzinfo=timezone.utc)
#: A password that must never appear in anything either entry prints.
SENTINEL = "SENTINEL-PASSWORD-DO-NOT-ECHO"

UNAPPROVED_URL = (
    f"postgresql://importtest:{SENTINEL}@localhost:5432/alethra_test_isolated_20261109"
)
PRODUCTION_URL = f"postgresql://importtest:{SENTINEL}@localhost:5432/ai_investment_lab"
NAMELESS_URL = ""


def daily_rows(days: int) -> List[List[Any]]:
    return [
        [(DAY_ONE.toordinal() - 719529 + i) * 86400,
         "100.5", "101.5", "99.5", "100.75", "12.5"]
        for i in range(days)
    ]


def write_payload(tmp_path: Path, **overrides) -> Path:
    data: Dict[str, Any] = {
        "instrument_id": INSTRUMENT,
        "interval_minutes": 1440,
        "harvest_fingerprint": "sha256:fixture",
        "currency": "USD",
        "bars": daily_rows(3),
    }
    data.update(overrides)
    path = tmp_path / "bars.json"
    path.write_text(json.dumps(data))
    return path


def digest_of(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# -- the root entry is a forwarder, not a second importer --------------------

#: Every name that carries a check or a write. A root entry that resolves any of
#: these to something other than the core's version is a second implementation.
CHECK_BEARING = (
    "run", "prepare", "check_series", "guard_target",
    "require_for_write", "provenance_for", "read_verified", "verify_expected_sha256",
    "validate_bars", "payload_from_object", "detail_from_object",
    "crosscheck", "store_series_bars", "classify_series_rows", "ImportRefusal",
    "UsageError",
)


@pytest.mark.parametrize("name", sorted(set(CHECK_BEARING)))
def test_root_entry_resolves_check_bearing_names_to_the_core(name):
    assert getattr(root, name) is getattr(core, name), (
        f"scripts/import_real_data.py defines its own {name!r}: the two entries "
        "would no longer run one safety flow"
    )


def test_root_entry_wrappers_only_hand_the_argv_over():
    """``main`` and ``cli`` are the two names the entry is allowed to define, and
    they may do nothing but pass argv to the core's versions."""
    seen: List[tuple] = []

    def record(argv):
        seen.append(("main", tuple(argv)))
        return 0

    def record_cli(argv):
        seen.append(("cli", tuple(argv)))
        return 1

    old = (core.main, core.cli)
    core.main, core.cli = record, record_cli
    try:
        argv = ["--input", "x.json", "--expected-sha256", "0" * 64]
        assert root.main(argv) == 0
        assert root.cli(argv) == 1
    finally:
        core.main, core.cli = old
    assert seen == [("main", tuple(argv)), ("cli", tuple(argv))]


def test_root_entry_holds_no_check_of_its_own():
    """The whole file may only be plumbing. Any logic in it is a fork waiting to
    drift, so the names it defines itself are enumerated here."""
    own = {
        name for name, value in vars(root).items()
        if not name.startswith("__") and getattr(value, "__module__", None) == root.__name__
        and not name.startswith("_")
    }
    assert own == {"main", "cli"}, (
        f"root entry defines logic of its own: {sorted(own)}"
    )


def test_root_entry_source_does_not_reach_the_warehouse():
    source = ROOT_ENTRY.read_text()
    for forbidden in ("create_engine", "verify_test_database", "store_series_bars",
                      "MarketDataStorage", "OHLCVBar", "sessionmaker", "INSERT"):
        assert forbidden not in source, (
            f"{forbidden!r} appears in the compatibility entry: that file must not "
            "hold a path to the database"
        )


def test_compatibility_entry_is_the_documented_one():
    assert core.__file__ == str(CORE_ENTRY)
    assert "Compatibility entry point" in ROOT_ENTRY.read_text(), (
        "the root file must say it is a compatibility entry, or a reader will "
        "treat it as the importer"
    )
    doc = core.__doc__ or ""
    assert "scripts/import_real_data.py" in doc, (
        "the core must name the compatibility entry it owns, or the two files "
        "read as unrelated tools"
    )


# -- the same refusal through either entry ----------------------------------

def run_entry(entry: Path, argv: List[str], env: Dict[str, str]):
    proc = subprocess.run(
        [sys.executable, str(entry), *argv],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=180,
    )
    return proc


def base_env(extra: Dict[str, str] | None = None) -> Dict[str, str]:
    env = dict(os.environ)
    env["TEST_MODE"] = "true"
    env.pop("DATABASE_URL", None)
    env.update(extra or {})
    return env


SCENARIOS = [
    # label, payload overrides, argv, expected fragment, exit code, hash to declare
    dict(label="unapproved target", payload={}, argv=["--database-url", UNAPPROVED_URL],
         fragment="not in the test whitelist", code=1),
    dict(label="production-named target", payload={},
         argv=["--database-url", PRODUCTION_URL],
         fragment="production blacklist", code=1),
    dict(label="no target named", payload={}, argv=[],
         fragment="no fallback target", code=1),
    dict(label="hash not the file being read", payload={},
         argv=["--database-url", UNAPPROVED_URL],
         fragment="is not the one that was validated", code=1, hash="wrong"),
    dict(label="intraday flat payload",
         payload={"interval_minutes": 60, "bars": daily_rows(6)},
         argv=["--database-url", UNAPPROVED_URL],
         fragment="only accepts 1440m daily bars", code=1),
    dict(label="off-grid bar",
         payload={"bars": [[r[0] + 8 * 3600, *r[1:]] for r in daily_rows(3)]},
         argv=["--database-url", UNAPPROVED_URL],
         fragment="not on the UTC midnight grid", code=1),
    dict(label="another instrument", payload={"instrument_id": "CRYPTO:KRAKEN:ETH-USD"},
         argv=["--database-url", UNAPPROVED_URL, "--instrument", INSTRUMENT],
         fragment="expected CRYPTO:KRAKEN:BTC-USD", code=1),
    dict(label="unknown grain requested", payload={},
         argv=["--interval-minutes", "7", "--database-url", UNAPPROVED_URL],
         fragment="not a grain this warehouse supports", code=2),
    dict(label="dry-run against an unapproved target", payload={},
         argv=["--dry-run", "--database-url", UNAPPROVED_URL],
         fragment="not in the test whitelist", code=1),
    dict(label="contradictory flags", payload={},
         argv=["--dry-run", "--confirm-write", "--database-url", UNAPPROVED_URL],
         fragment="contradict each other", code=2),
    dict(label="missing file", payload={}, input="/nonexistent/bars.json",
         argv=[], fragment="is not a file", code=2, hash="wrong"),
    dict(label="write without stated target", payload={},
         argv=["--confirm-write", "--instrument", INSTRUMENT,
               "--database-url", UNAPPROVED_URL],
         fragment="not in the test whitelist", code=1),
]


@pytest.mark.parametrize("case", SCENARIOS, ids=[s["label"] for s in SCENARIOS])
def test_both_entries_refuse_identically(tmp_path, case):
    """Same argv, same bytes on stderr, same exit code, through either entry."""
    path = case.get("input") or str(write_payload(tmp_path, **case["payload"]))
    declared = "0" * 64 if case.get("hash") == "wrong" else digest_of(path)
    argv = ["--input", path, "--expected-sha256", declared, *case["argv"]]

    outputs = {}
    for entry in (ROOT_ENTRY, CORE_ENTRY):
        proc = run_entry(entry, argv, base_env())
        outputs[entry.name] = proc
        assert case["fragment"] in proc.stderr, (
            f"{entry.name} did not refuse with {case['fragment']!r} for "
            f"{case['label']}: exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        )
        assert proc.returncode == case["code"], (
            f"{entry.name} exited {proc.returncode}, expected {case['code']} "
            f"for {case['label']}"
        )
    assert outputs[ROOT_ENTRY.name].stderr == outputs[CORE_ENTRY.name].stderr, (
        f"{case['label']}: the two entries report the same refusal differently"
    )


def test_both_entries_print_the_same_report_when_everything_passes(capsys):
    """Refusals agreeing is not enough: the report a caller stores as evidence
    has to be the same object graph through either entry.

    The gate answers, the engine is never built, and the plan is supplied, so
    what is compared is everything the two entries agree to print for a
    successful dry run — no database and no write involved.
    """
    name = ".pytest_import_entrypoints_bars.json"
    path = REPO_ROOT / name
    path.write_text(json.dumps({
        "instrument_id": INSTRUMENT,
        "interval_minutes": 1440,
        "harvest_fingerprint": "sha256:fixture",
        "currency": "USD",
        "bars": daily_rows(3),
    }))

    sha = digest_of(path)
    from app.services.crypto_bar_harvest import _warehouse_row

    def fake_classify(db, series, *, provider_id="kraken", lock_for_write=False):
        rows = [
            _warehouse_row(
                candle,
                instrument_id=series.instrument_id,
                interval_minutes=series.interval_minutes,
                fetched_at=series.fetched_at,
                provider_id=provider_id,
            )
            for candle in series.completed
        ]
        return {"rows": rows, "new": rows, "identical": 0, "conflicting": [],
                "grain_ambiguous": []}

    argv = ["--input", str(path), "--expected-sha256", sha,
            "--database-url", UNAPPROVED_URL]
    conn = {"database": "ai_investment_lab_test", "user": "importtest",
            "server_addr": "127.0.0.1", "server_port": "5432"}
    old = {k: getattr(core, k) for k in
           ("guard_target", "create_engine", "sessionmaker", "classify_series_rows")}
    reports = {}
    try:
        core.guard_target = lambda url, **kw: conn
        core.classify_series_rows = fake_classify
        core.create_engine = lambda url: _DummyEngine()
        core.sessionmaker = lambda **kw: (lambda: _DummySession())
        for module, label in ((root, "root"), (core, "core")):
            capsys.readouterr()
            assert module.main(argv) == 0
            reports[label] = capsys.readouterr().out
    finally:
        for k, v in old.items():
            setattr(core, k, v)
        path.unlink()

    assert reports["root"] == reports["core"], (
        "the two entries printed different reports for one successful dry run")
    parsed = json.loads(reports["root"])
    assert parsed["dry_run"] is True and parsed["written"] is None
    assert parsed["input"]["sha256"] == sha
    assert parsed["target"] == (
        "database=ai_investment_lab_test user=importtest "
        "server=127.0.0.1:5432")
    assert parsed["plan"]["would_insert"] == 3
    assert SENTINEL not in reports["root"]


class _DummyEngine:
    disposed = False

    def dispose(self):
        self.disposed = True


class _DummySession:
    closed = False

    def close(self):
        self.closed = True


@pytest.mark.parametrize("input_flag,instrument_flag",
                         [("--input", "--instrument"),
                          ("--json-path", "--expected-instrument")],
                         ids=["canonical", "legacy aliases"])
def test_legacy_alias_cannot_bypass_a_required_input(tmp_path, input_flag,
                                                     instrument_flag):
    """Old spellings still parse, and still demand the same preconditions."""
    path = write_payload(tmp_path)
    aliased = [input_flag, str(path), "--expected-sha256", digest_of(path),
               instrument_flag, INSTRUMENT]
    for entry in (ROOT_ENTRY, CORE_ENTRY):
        proc = run_entry(entry, aliased, base_env())
        assert proc.returncode == 1, (
            f"{entry.name} accepted {input_flag} without a target: {proc.stderr}")
        assert "no fallback target" in proc.stderr, (
            f"{entry.name} ran {input_flag}/{instrument_flag} through a different "
            "checklist than the canonical flags")
        assert SENTINEL not in proc.stdout + proc.stderr

    for entry in (ROOT_ENTRY, CORE_ENTRY):
        proc = run_entry(
            entry,
            [input_flag, str(path), instrument_flag, INSTRUMENT],
            base_env(),
        )
        assert proc.returncode == 2, (
            f"{entry.name} imported without --expected-sha256: {proc.stderr}")
        assert "the following arguments are required" in proc.stderr
        assert "--expected-sha256" in proc.stderr


def test_instrument_alias_disagreement_is_a_usage_error(tmp_path):
    """--instrument and --expected-instrument are the same slot; two different
    answers cannot both be honoured."""
    path = write_payload(tmp_path)
    argv = ["--input", str(path), "--expected-sha256", digest_of(path),
            "--instrument", INSTRUMENT, "--expected-instrument", "CRYPTO:KRAKEN:ETH-USD",
            "--database-url", UNAPPROVED_URL]
    for entry in (ROOT_ENTRY, CORE_ENTRY):
        proc = run_entry(entry, argv, base_env())
        assert proc.returncode == 2, proc.stderr
        assert "disagree" in proc.stderr


# -- the order of the checks, through either entry ---------------------------

class Spy:
    def __init__(self, label, sink, result=None):
        self.label, self.sink, self.result = label, sink, result

    def __call__(self, *args, **kwargs):
        self.sink.append(self.label)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("entry_name", ["root", "core"])
def test_hash_and_content_are_cleared_before_the_gate_is_asked(entry_name, tmp_path):
    module = root if entry_name == "root" else core
    order: List[str] = []
    path = write_payload(tmp_path, interval_minutes=60, bars=daily_rows(6))

    guard = Spy("gate", order, {"database": "ai_investment_lab_test",
                                "user": "importtest", "server_addr": "127.0.0.1",
                                "server_port": "5432"})
    engine = Spy("engine", order, result=AssertionError("a session was opened"))
    old_guard, old_engine = core.guard_target, core.create_engine
    core.guard_target = guard
    core.create_engine = engine
    try:
        with pytest.raises(core.ImportRefusal, match="only accepts 1440m daily bars"):
            module.main(["--input", str(path), "--expected-sha256", digest_of(path),
                         "--database-url", UNAPPROVED_URL])
    finally:
        core.guard_target = old_guard
        core.create_engine = old_engine
    assert order == [], "the gate was consulted for a payload that had already failed"


@pytest.mark.parametrize("entry_name", ["root", "core"])
def test_a_refused_target_never_reaches_a_session(entry_name, tmp_path):
    module = root if entry_name == "root" else core
    order: List[str] = []
    path = write_payload(tmp_path)

    refuse = core.ImportRefusal("Safety gate rejected the target: not in the test whitelist")
    guard = Spy("gate", order, refuse)
    engine = Spy("engine", order, result={"database": "unused"})
    old_guard, old_engine = core.guard_target, core.create_engine
    core.guard_target = guard
    core.create_engine = engine
    try:
        with pytest.raises(core.ImportRefusal, match="not in the test whitelist"):
            module.main(["--input", str(path), "--expected-sha256", digest_of(path),
                         "--database-url", UNAPPROVED_URL])
    finally:
        core.guard_target = old_guard
        core.create_engine = old_engine
    assert order == ["gate"], "the session was built even though the target was refused"


@pytest.mark.parametrize("entry_name", ["root", "core"])
def test_missing_provenance_is_refused_after_the_gate_and_before_the_write(
        entry_name, tmp_path):
    """A file outside a worktree has no source chain, and no row may be written
    to compensate for that."""
    module = root if entry_name == "root" else core
    order: List[str] = []
    path = write_payload(tmp_path)

    guard = Spy("gate", order, {"database": "ai_investment_lab_test", "user": "importtest",
                                "server_addr": "127.0.0.1", "server_port": "5432"})
    engine = Spy("engine", order, result=AssertionError("a session was opened"))
    old = {name: getattr(core, name) for name in ("guard_target", "create_engine")}
    core.guard_target = guard
    core.create_engine = engine
    try:
        with pytest.raises(core.ImportRefusal, match="not inside a git worktree"):
            module.main(["--input", str(path), "--expected-sha256", digest_of(path),
                         "--instrument", INSTRUMENT, "--confirm-write",
                         "--expected-database", "ai_investment_lab_test",
                         "--expected-role", "importtest",
                         "--database-url", UNAPPROVED_URL])
    finally:
        core.guard_target, core.create_engine = old["guard_target"], old["create_engine"]
    assert order == ["gate"]


# -- nothing leaks the credentials in the target ----------------------------

@pytest.mark.parametrize("entry", [ROOT_ENTRY.name, CORE_ENTRY.name], ids=["root", "core"])
def test_stderr_and_stdout_never_echo_the_target_password(entry, tmp_path):
    path = write_payload(tmp_path)
    for url, expectation in (
        (UNAPPROVED_URL, "whitelist"),
        (PRODUCTION_URL, "blacklist"),
    ):
        proc = run_entry(
            REPO_ROOT / ("scripts" if entry == ROOT_ENTRY.name else "backend/scripts")
            / "import_real_data.py",
            ["--input", str(path), "--expected-sha256", digest_of(path),
             "--database-url", url],
            base_env(),
        )
        combined = proc.stdout + proc.stderr
        assert expectation in combined, combined
        assert SENTINEL not in combined, f"{entry} printed the target password"
        assert "importtest" not in combined, f"{entry} printed the target role"


def test_environment_database_url_is_not_echoed(tmp_path):
    path = write_payload(tmp_path)
    env = base_env({"DATABASE_URL": f"postgresql://role:{SENTINEL}@localhost:5432/nope"})
    leaks = []
    for entry in (ROOT_ENTRY, CORE_ENTRY):
        proc = run_entry(entry, ["--input", str(path),
                                 "--expected-sha256", digest_of(path)], env)
        if SENTINEL in proc.stdout + proc.stderr:
            leaks.append(entry.name)
    assert leaks == [], f"{leaks} echoed DATABASE_URL"
