"""The lane entry point has contracts of its own, and none of them were executed.

``backend/scripts/run_tests.sh`` is the thing that decides what a count means:
which layer ran, which interpreter, how much of the tree was selected, and
whether a refusal was reported instead of a green zero. Until now a caller could
silently take away the two properties the evidence ledger depends on — the skip
reasons (pytest honours only the last ``-r``) and the collected count (``-q``
suppresses it) — and nothing in the suite noticed, because no test ran the
script.

These tests run it as a subprocess against a stub selection, so they measure the
script, not pytest. The stub carries a synthetic skip on purpose: the contract
being pinned is "the lane itemises skip reasons whatever the caller asked for",
which is generic. Whether the PostgreSQL-only modules are the ones being skipped
is a separate fact, recorded in the run logs (``SKIPPED [1] tests/...: <reason>``
over the real marked files), not here.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "run_tests.sh"

STUB = '''"""one passing and one skipped item, for the lane's own reporting contract."""

import pytest


@pytest.mark.skipif(True, reason="lane contract stub")
def test_skipped_by_contract():
    raise AssertionError("a skipped item must never reach the body")


def test_passes():
    assert True
'''


def lane_env(tmp_path, **overrides):
    env = dict(os.environ)
    # A parent pytest leaks these into the child and the child then reports on
    # the parent's session instead of its own.
    for name in [k for k in env if k.startswith(("PYTEST_", "PYTHONHOME"))] + ["PYTEST_ADDOPTS"]:
        env.pop(name, None)
    for name in ("ALETHRA_PG_TEST_URL", "ALETHRA_SQLITE_DB", "ALETHRA_TEST_DB", "DATABASE_URL"):
        env.pop(name, None)
    env["TMPDIR"] = str(tmp_path)
    env["DATA_PROVIDER"] = "demo"
    env.update({k: str(v) for k, v in overrides.items() if v is not None})
    return env


def run_lane(args, tmp_path, **overrides):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=600,
        env=lane_env(tmp_path, **overrides),
    )


@pytest.fixture()
def stub(tmp_path):
    directory = tmp_path / "lane_stub"
    directory.mkdir()
    (directory / "test_lane_stub.py").write_text(STUB)
    return directory / "test_lane_stub.py"


def test_the_lane_itemises_skips_even_when_the_caller_drops_them(tmp_path, stub):
    """``-rfE`` used to override the script's own ``-rs`` and hide every reason."""
    result = run_lane(
        ["sqlite", "-q", "-p", "no:warnings", "--tb=line", "-rfE", str(stub)], tmp_path
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout and "1 skipped" in result.stdout
    assert "SKIPPED [1]" in result.stdout, (
        "a log without skip reasons cannot support a per-node ledger: " + result.stdout[-500:]
    )
    assert "lane contract stub" in result.stdout


def test_the_lane_prints_the_selection_it_is_about_to_run(tmp_path, stub):
    result = run_lane(["sqlite", "-q", "-p", "no:warnings", str(stub)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    line = [ln for ln in result.stdout.splitlines() if ln.startswith("selection on this tree:")]
    assert len(line) == 1, result.stdout[-800:]
    assert "2 tests collected" in line[0], line[0]


def test_the_lane_records_the_command_cwd_and_revision_it_ran(tmp_path, stub):
    """Without these three lines a log cannot be re-run, only believed.

    The authoritative full-suite log of 2026-11-16 carries none of them: its
    command had to be reconstructed afterwards from the shape of the output
    (progress characters, the absence of a WARNINGS block, ERROR lines in the
    summary), which is exactly the kind of claim an evidence ledger should not
    have to make.
    """
    result = run_lane(["sqlite", "-q", str(stub)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    command = [ln for ln in lines if ln.startswith("command: ")]
    assert len(command) == 1, lines
    assert str(stub) in command[0] and "sqlite" in command[0], command[0]
    assert any(ln.startswith("cwd: ") and ln.endswith("backend") for ln in lines), lines
    revision = [ln for ln in lines if ln.startswith("git: ")]
    assert len(revision) == 1, lines
    assert len(revision[0].split("git: ")[1].split(" ")[0]) == 40, revision[0]


def test_the_lane_log_carries_the_identity_without_needing_the_console(tmp_path, stub):
    """The saved log has to be re-runnable on its own, not beside a transcript.

    The 2026-11-16 authoritative run was invoked with `-q`, and `-q` suppresses
    pytest's own collected header, so the tee'd log carried no selection count at
    all. Its skip-budget ceiling then had to be read as "cannot be shown to be
    the budgeted selection" -- and a second lane run sharing the log path
    overwrote the head of the first, which a console transcript alone would have
    hidden. Four identity lines in the log close both.
    """
    log = tmp_path / "lane.log"
    result = run_lane(["sqlite", "-q", str(stub)], tmp_path, ALETHRA_LANE_LOG=log)
    assert result.returncode == 0, result.stdout + result.stderr
    logged = log.read_text().splitlines()
    command = [ln for ln in logged if ln.startswith("command: ")]
    assert len(command) == 1, logged[:12]
    assert str(stub) in command[0] and "sqlite" in command[0], command[0]
    assert any(ln.startswith("cwd: ") for ln in logged), logged[:12]
    revision = [ln for ln in logged if ln.startswith("git: ")]
    assert len(revision) == 1 and len(revision[0][5:].split(" ")[0]) == 40, revision
    selection = [ln for ln in logged if ln.startswith("selection on this tree:")]
    assert len(selection) == 1 and "2 tests collected" in selection[0], selection


def test_an_empty_selection_is_refused_instead_of_running_zero_tests(tmp_path, stub):
    result = run_lane(["sqlite", "-q", "-k", "no_such_item", str(stub)], tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "refusing" in result.stderr
    assert "no tests collected" in result.stdout


def test_an_unknown_layer_is_refused(tmp_path, stub):
    result = run_lane(["psql", str(stub)], tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "unknown layer: psql" in result.stderr


def test_a_relative_sqlite_path_is_refused(tmp_path, stub):
    result = run_lane(["sqlite", "-q", str(stub)], tmp_path, ALETHRA_SQLITE_DB="relative.db")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "must be an absolute path" in result.stderr


def test_a_missing_tmpdir_is_refused_where_it_would_fail_every_fixture(tmp_path, stub):
    """Without this the run says 'unable to open database file' 3,400 times."""
    result = run_lane(["sqlite", "-q", str(stub)], tmp_path, TMPDIR=tmp_path / "does-not-exist")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "does not exist" in result.stderr


def test_a_non_executable_interpreter_override_is_refused(tmp_path, stub):
    result = run_lane(["sqlite", "-q", str(stub)], tmp_path,
                      ALETHRA_PYTHON=tmp_path / "not-an-interpreter")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "is not an executable file" in result.stderr


def test_the_pg_layer_refuses_without_its_disposable_url(tmp_path, stub):
    result = run_lane(["pg", "-q", str(stub)], tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "ALETHRA_PG_TEST_URL" in result.stderr


# === The skip ceiling ===
#
# Every node below drives backend/scripts/skip_budget.py with a synthetic log,
# so the numbers are the script's arithmetic rather than this checkout's. The
# real census it enforces -- 3462 collected, 183 units, 164 pg_only / 18
# live_stack / 1 other -- is recorded in the ledger and in the generated
# budget's provenance block, not here.

BUDGET_SCRIPT = ROOT / "backend" / "scripts" / "skip_budget.py"


def budget_text(collected=10, total=2, pg_only=1, live_stack=1, other=0):
    return json.dumps(
        {
            "collected": collected,
            "total": total,
            "pg_only": pg_only,
            "live_stack": live_stack,
            "other": other,
            "provenance": {"source_log_sha256": "0" * 64},
        }
    )


def lane_log(skip_lines, collected=10):
    body = [f"selection on this tree: {collected} tests collected in 1.00s"]
    body += [f"SKIPPED [1] tests/x.py:{30 + i}: {reason}"
             for i, reason in enumerate(skip_lines)]
    return "\n".join(body) + "\n"


def run_budget(tmp_path, log_text, budget_json, *extra):
    log = tmp_path / "lane.log"
    log.write_text(log_text)
    budget = tmp_path / "budget.json"
    budget.write_text(budget_json)
    result = subprocess.run(
        [sys.executable, str(BUDGET_SCRIPT), str(log), "check",
         "--budget", str(budget), *extra],
        capture_output=True, text=True, timeout=120,
    )
    return result


class TestSkipCeiling:
    def test_an_over_budget_census_exits_nonzero_and_names_the_category(self, tmp_path):
        """One more skip than the ceiling is a refusal, not a footnote."""
        log = lane_log([
            "PG-ONLY [tests/x.py:9]: needs a real row lock",
            "PG-ONLY [tests/x.py:12]: needs a real row lock",
            "live-stack probe: ALETHRA_LIVE_PROBES is not set to 1",
        ])
        result = run_budget(tmp_path, log, budget_text(total=2, pg_only=1, live_stack=1, other=0))
        assert result.returncode == 1, result.stdout + result.stderr
        assert "OVER BUDGET" in result.stderr, result.stdout + result.stderr
        assert "pg_only: measured 2 > ceiling 1" in result.stderr, result.stderr

    def test_the_ceiling_is_per_category_so_a_new_excuse_cannot_hide(self, tmp_path):
        """164 PostgreSQL entries must not become a shelter for one new reason."""
        log = lane_log([
            "PG-ONLY [tests/x.py:9]: needs a real row lock",
            "BLOCKED: cannot intercept the cleanup commit without complex mocking",
        ])
        budget = budget_text(total=2, pg_only=1, live_stack=0, other=1)
        assert run_budget(tmp_path, log, budget).returncode == 0

        # The same second line, but 'other' carries no room: refused even though
        # the total is exactly at ceiling.
        tight = budget_text(total=2, pg_only=1, live_stack=1, other=0)
        refused = run_budget(tmp_path, log, tight)
        assert refused.returncode == 1
        assert "other: measured 1 > ceiling 0" in refused.stderr, refused.stderr

    def test_an_unfamiliar_excuse_is_classified_other_not_pg_only(self, tmp_path):
        from importlib.machinery import SourceFileLoader

        module = SourceFileLoader("skip_budget", str(BUDGET_SCRIPT)).load_module()
        assert module.classify("PG-ONLY [tests/x.py:1]: row lock") == "pg_only"
        assert module.classify("PG-SETUP [tests/x.py:2]: no query string") == "pg_only"
        assert module.classify("live-stack probe: ALETHRA_LIVE_PROBES=1 unset") == "live_stack"
        assert module.classify("PostgreSQL cannot be reached on this host") == "pg_only"
        assert module.classify("TODO: rewrite this fixture") == "other"

    def test_grouped_skip_lines_are_counted_as_their_group_size(self, tmp_path):
        """pytest folds identical locations into SKIPPED [N]; N units, not one."""
        from importlib.machinery import SourceFileLoader

        module = SourceFileLoader("skip_budget", str(BUDGET_SCRIPT)).load_module()
        text = "SKIPPED [7] tests/x.py:12: PG-ONLY [tests/x.py:3]: row lock\n"
        units, counts, _rows = module.census(text)
        assert units == 7 and counts["pg_only"] == 7, (units, dict(counts))

    def test_a_missing_ceiling_fails_closed_rather_than_being_unlimited(self, tmp_path):
        log = lane_log(["PG-ONLY [tests/x.py:9]: needs a real row lock"])
        (tmp_path / "lane.log").write_text(log)
        result = subprocess.run(
            [sys.executable, str(BUDGET_SCRIPT), str(tmp_path / "lane.log"), "check",
             "--budget", str(tmp_path / "no-such-budget.json")],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert "refused" in result.stderr.lower(), result.stderr
        assert "not an unlimited ceiling" in result.stderr, result.stderr

    def test_a_corrupt_ceiling_fails_closed(self, tmp_path):
        log = lane_log(["PG-ONLY [tests/x.py:9]: needs a real row lock"])
        result = run_budget(tmp_path, log, "{ not json")
        assert result.returncode == 2, result.stdout + result.stderr
        assert "unreadable" in result.stderr, result.stderr

    def test_a_ceiling_that_does_not_add_up_is_rejected(self, tmp_path):
        """Parts that do not sum to the total mean nobody can audit either."""
        log = lane_log(["PG-ONLY [tests/x.py:9]: needs a real row lock"])
        result = run_budget(tmp_path, log, budget_text(total=9, pg_only=1, live_stack=1, other=0))
        assert result.returncode == 2, result.stdout + result.stderr
        assert "does not add up" in result.stderr, result.stderr

    def test_a_changed_selection_is_refused_instead_of_silently_unenforced(self, tmp_path):
        """Deleting tests would otherwise drop the run under every ceiling."""
        log = lane_log(["PG-ONLY [tests/x.py:9]: needs a real row lock"], collected=4)
        result = run_budget(tmp_path, log, budget_text(collected=10, total=2, pg_only=1))
        assert result.returncode == 2, result.stdout + result.stderr
        assert "collected 4 tests but the budget pins 10" in result.stderr, result.stderr

    def test_a_quiet_transcript_is_read_from_the_identity_artifact(self, tmp_path):
        """`-q` hides pytest's collected header; the run still recorded the number.

        The authoritative 2026-11-17 fixed-state run was launched with `-q`, so
        `pytest.log` carried no "N tests collected" line and the ceiling check
        refused the whole run with "the lane printed no collected count" -- a
        refusal that read as a selection problem while the measurement was
        complete. The lane writes that count to `identity.txt` whatever the
        caller asked for, and it is the artifact the check has to fall back to.
        """
        transcript = "\n".join(
            [f"SKIPPED [1] tests/x.py:{30 + i}: PG-ONLY [tests/x.py:{n}]: row lock"
             for i, n in enumerate(range(9, 10))]) + "\n"
        identity = "collected-node-ids: 10\nselection on this tree: 10 tests collected in 1.00s\n"
        budget = budget_text(collected=10, total=5, pg_only=4)

        without = run_budget(tmp_path, transcript, budget)
        assert without.returncode == 2, without.stdout + without.stderr
        assert "printed a collected count" in without.stderr, without.stderr

        path = tmp_path / "identity.txt"
        path.write_text(identity)
        with_identity = run_budget(tmp_path, transcript, budget, "--identity", str(path))
        assert with_identity.returncode == 0, with_identity.stdout + with_identity.stderr
        assert "within budget" in with_identity.stdout, with_identity.stdout

        # The fallback is a second source, not a second chance: an identity
        # naming a different selection still refuses, and does not quietly
        # inherit the log's silence.
        wrong = tmp_path / "identity-11.txt"
        wrong.write_text("selection on this tree: 11 tests collected in 1.00s\n")
        mismatch = run_budget(tmp_path, transcript, budget, "--identity", str(wrong))
        assert mismatch.returncode == 2, mismatch.stdout + mismatch.stderr
        assert "collected 11 tests but the budget pins 10" in mismatch.stderr, mismatch.stderr

    def test_a_subset_run_reports_the_census_but_is_not_judged_by_it(self, tmp_path):
        """One file cannot hold the whole suite's 164 PostgreSQL-only nodes."""
        log = lane_log(
            [f"PG-ONLY [tests/x.py:{n}]: needs a real row lock" for n in range(9, 20)],
            collected=11,
        )
        result = run_budget(tmp_path, log, budget_text(collected=11, total=1, pg_only=1,
                                                        live_stack=0, other=0),
                            "--explicit-selection")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "selected specific tests" in result.stdout, result.stdout
        assert "pg_only=11" in result.stdout, result.stdout

    def test_under_the_ceiling_reports_headroom_rather_than_a_pass_portion(self, tmp_path):
        log = lane_log(["PG-ONLY [tests/x.py:9]: needs a real row lock"], collected=10)
        result = run_budget(tmp_path, log, budget_text(collected=10, total=5, pg_only=4))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "within budget" in result.stdout, result.stdout
        assert "pg_only=3" in result.stdout, result.stdout

    def test_the_ceiling_is_generated_and_regenerates_identically(self, tmp_path):
        """The committed numbers came from a log, and come out of it again.

        No skip when the log is gone: a conditional skip here would add a unit to
        the very census this file's gate polices, and the excuse would be filed
        under the category it is supposed to keep separate.

        What has to reproduce is the ceiling -- the five numbers -- and the digest
        of the text they were counted from. The provenance block itself cannot
        reproduce, because the two sanctioned writers record different things about
        the same run: `write-budget` knows a log, `pin` knows a run directory.
        """
        budget_file = ROOT / "backend" / "tests" / "skip_budget.json"
        committed = json.loads(budget_file.read_text())
        assert sum(committed[c] for c in ("pg_only", "live_stack", "other")) == committed["total"]
        assert committed["total"] <= committed["collected"]
        provenance = committed["provenance"]
        assert provenance["generated_by"] in (
            "backend/scripts/skip_budget.py <lane log> write-budget",
            "backend/scripts/run_evidence.py <run dir> pin",
        ), provenance["generated_by"]
        assert len(provenance["source_log_sha256"]) == 64, provenance
        source = provenance["source_log"]
        if not os.path.exists(source):
            # The /tmp log is a local artifact; the numbers still have to be
            # self-consistent, and a real re-measure regenerates the file.
            return
        assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == \
            provenance["source_log_sha256"], (
            "the ceiling names a log it was not counted from"
        )
        out = tmp_path / "regenerated.json"
        argv = [sys.executable, str(BUDGET_SCRIPT), source, "write-budget", "--out", str(out)]
        run_dir = provenance.get("run_dir")
        identity = os.path.join(run_dir, "identity.txt") if run_dir else None
        if identity and os.path.exists(identity):
            # A quiet caller's transcript carries no collected header, so the
            # re-measure reads the count from the same artifact the lane wrote.
            argv += ["--identity", identity]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        numbers = ("collected", "total", "pg_only", "live_stack", "other")
        regenerated = json.loads(out.read_text())
        assert {k: regenerated[k] for k in numbers} == {k: committed[k] for k in numbers}, (
            "the committed ceiling is not what its own source log produces: "
            + str({k: (committed[k], regenerated[k]) for k in numbers})
        )

    def test_the_lane_runs_the_census_check_after_pytest_returns(self, tmp_path, stub):
        """Wiring the script but not the lane would leave the ceiling unused."""
        script = SCRIPT.read_text()
        assert "skip_budget.py" in script and "check --budget" in script, script
        assert 'RC=${PIPESTATUS[0]}' in script, (
            "the lane must keep pytest's own exit code ahead of the census verdict"
        )
        assert 'exec "$PYTHON" -m pytest' not in script, (
            "exec leaves nothing to run afterwards, so the census could not be checked"
        )

    def test_the_lane_prints_the_census_it_measured(self, tmp_path, stub):
        result = run_lane(["sqlite", "-q", "-p", "no:warnings", str(stub)], tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "skip census of" in result.stdout, result.stdout[-400:]
        # The lines a reader needs to go back to the evidence itself. A single
        # shared "lane log:" path used to be the only pointer, and two runs
        # given that path overwrote each other.
        for pointer in ("run dir:", "console log:", "outcome records:", "exit code:"):
            assert pointer in result.stdout, (pointer, result.stdout[-600:])


# === The run directory, its artifacts, and the recorded exit status ===
#
# One shared log path was the design before 2026-11-16, and it cost two runs: a
# focused run pointed at the full-suite log's path truncated it from byte 0, so
# the head of the measurement was replaced by the tail of something else, and a
# `tee -a` that interleaved with a concurrent run left a hole in the middle. The
# nodes below drive the real script twice over, so what they pin is "one run
# cannot reach another run's evidence" rather than "the script mentions a
# directory".

ARTIFACTS = ("identity.txt", "pytest.log", "junit.xml", "collect.txt", "exit-code.txt")

FAIL_STUB = '''"""one failing item, for the lane's exit-status contract."""


def test_fails_on_purpose():
    raise AssertionError("a planned failure")
'''


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_dir_of(stdout):
    lines = [ln for ln in stdout.splitlines() if ln.startswith("run dir:")]
    assert len(lines) == 1, stdout[-800:]
    return Path(lines[0].split("run dir: ", 1)[1].strip())


def identity_line(stdout, prefix):
    lines = [ln for ln in stdout.splitlines() if ln.startswith(prefix)]
    assert len(lines) == 1, (prefix, stdout[-800:])
    return lines[0]


def test_every_run_leaves_a_complete_artifact_set_behind(tmp_path, stub):
    result = run_lane(["sqlite", "-q", str(stub)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    directory = run_dir_of(result.stdout)
    missing = [name for name in ARTIFACTS if not (directory / name).is_file()]
    assert not missing, (str(directory), missing)
    assert (directory / "exit-code.txt").read_text().strip() == "0"
    # collect.txt holds node ids, which are the ledger's keys; the per-file
    # count form it replaced carries no node to reconcile an outcome against.
    collected = (directory / "collect.txt").read_text()
    assert "test_skipped_by_contract" in collected and "test_passes" in collected, collected[-400:]
    assert (directory / "junit.xml").read_text().count("<testcase") == 2


def test_two_runs_cannot_reach_each_others_output(tmp_path, stub):
    first = run_lane(["sqlite", "-q", str(stub)], tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    one = run_dir_of(first.stdout)
    before = {name: sha256(one / name) for name in ARTIFACTS}

    second = run_lane(["sqlite", "-q", str(stub)], tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    two = run_dir_of(second.stdout)
    assert one != two, (str(one), str(two))
    after = {name: sha256(one / name) for name in ARTIFACTS}
    assert before == after, "a later run touched an earlier run's evidence"


def test_a_shared_lane_log_is_replaced_whole_rather_than_holed(tmp_path, stub):
    """The truncation regression: ALETHRA_LANE_LOG is now a copy, never a target."""
    log = tmp_path / "shared.log"
    wide = run_lane(["sqlite", "-q", str(stub)], tmp_path, ALETHRA_LANE_LOG=log)
    assert wide.returncode == 0, wide.stdout + wide.stderr
    assert wide.stdout.count("2 tests collected") >= 1

    focused = run_lane(["sqlite", "-q", f"{stub}::test_passes"], tmp_path,
                       ALETHRA_LANE_LOG=log)
    assert focused.returncode == 0, focused.stdout + focused.stderr
    text = log.read_text()
    # One identity header, and it describes the run that ended last: a file
    # that carried both runs' headers, or the shorter run's tail bolted onto the
    # longer run's head, would read as one measurement of something else.
    assert text.count("command: ") == 1, text[:600]
    assert text.count("selection on this tree:") == 1, text[:600]
    assert "1 test collected" in text, text[:600]
    assert "lane contract stub" not in text, "the previous run's skip reason survived"


def test_an_existing_run_directory_is_refused_and_left_untouched(tmp_path, stub):
    directory = tmp_path / "pinned-run"
    directory.mkdir()
    (directory / "pytest.log").write_text("evidence that must survive\n")
    result = run_lane(["sqlite", "-q", str(stub)], tmp_path, ALETHRA_RUN_DIR=directory)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "already exists" in result.stderr, result.stderr
    assert sorted(p.name for p in directory.iterdir()) == ["pytest.log"], list(directory.iterdir())
    assert (directory / "pytest.log").read_text() == "evidence that must survive\n"


def test_an_explicit_run_directory_without_a_parent_is_refused_not_invented(tmp_path, stub):
    """A typo in ALETHRA_RUN_DIR must not create the evidence path it names."""
    directory = tmp_path / "never-created" / "run-1"
    result = run_lane(["sqlite", "-q", str(stub)], tmp_path, ALETHRA_RUN_DIR=directory)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no parent directory" in result.stderr, result.stderr
    assert not directory.parent.exists(), "the lane invented an evidence directory"


def test_the_recorded_exit_status_is_pytest_own_not_the_scribes(tmp_path):
    """A logger's success used to be readable as the run's success."""
    failing = tmp_path / "lane_stub_fail"
    failing.mkdir()
    node = failing / "test_lane_stub_fail.py"
    node.write_text(FAIL_STUB)
    result = run_lane(["sqlite", "-q", str(node)], tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    directory = run_dir_of(result.stdout)
    assert (directory / "exit-code.txt").read_text().strip() == "1"
    assert "exit-code: 1" in (directory / "identity.txt").read_text()
    assert "1 failed" in (directory / "pytest.log").read_text()


@pytest.mark.parametrize("verbosity", [
    [], ["-q"], ["-qq"], ["-q", "-q"],
    ["-v"], ["-vv"], ["-vs"], ["--verbose"], ["--quiet"],
])
def test_the_collector_is_read_at_one_verbosity_level_whatever_the_caller_asked(
    tmp_path, stub, verbosity
):
    """-qq prints per-file counts and -v prints the tree; either one leaves the
    ledger with no node ids to reconcile, so neither reaches the collector."""
    result = run_lane(["sqlite", "-p", "no:warnings", *verbosity, str(stub)], tmp_path)
    assert result.returncode == 0, result.stdout[-1200:] + result.stderr[-800:]
    assert identity_line(result.stdout, "collected-node-ids:").endswith("2"), result.stdout[-800:]
    assert "2 tests collected" in identity_line(result.stdout, "selection on this tree:")
    directory = run_dir_of(result.stdout)
    assert "test_passes" in (directory / "collect.txt").read_text()
    assert "<Function test_passes>" not in (directory / "collect.txt").read_text()
    if verbosity:
        line = identity_line(result.stdout, "collector-verbosity-normalised:")
        assert all(flag in line for flag in verbosity), line
    else:
        assert "collector-verbosity-normalised:" not in (directory / "identity.txt").read_text()


def test_selection_kind_comes_from_cli_semantics_not_from_a_missing_dash(tmp_path, stub):
    """An option's value is not a named test, but a -k expression is a filter."""
    whole = run_lane(["sqlite", "--collect-only", "-q", "-p", "no:warnings"], tmp_path)
    assert whole.returncode == 0, whole.stdout[-800:] + whole.stderr[-800:]
    assert (identity_line(whole.stdout, "selection-kind:") ==
            "selection-kind: whole-suite (collect-only)"), whole.stdout[-800:]

    filtered = run_lane(["sqlite", "--collect-only", "-q", "-k", "test_passes", str(stub)],
                        tmp_path)
    assert filtered.returncode == 0, filtered.stdout[-800:] + filtered.stderr[-800:]
    line = identity_line(filtered.stdout, "selection-kind:")
    assert line.startswith("selection-kind: explicit-selection with filters:"), line
    assert "-k" in line, line

    named = run_lane(["sqlite", "--collect-only", "-q", str(stub)], tmp_path)
    assert named.returncode == 0, named.stdout[-800:] + named.stderr[-800:]
    assert (identity_line(named.stdout, "selection-kind:") ==
            "selection-kind: explicit-selection (collect-only)"), named.stdout[-800:]


def test_a_collect_only_run_says_it_executed_nothing(tmp_path, stub):
    """A collection is not a result, and must not close a ledger over it."""
    result = run_lane(["sqlite", "--collect-only", "-q", str(stub)], tmp_path)
    assert result.returncode == 0, result.stdout[-800:] + result.stderr[-800:]
    assert "nothing executed" in result.stdout, result.stdout[-800:]
    assert "skip census of" not in result.stdout
    assert (run_dir_of(result.stdout) / "exit-code.txt").read_text().strip() == "0"


SPAWNER_STUB = '''"""one node that starts the lane the way the suite's own contract tests do.

A run of `run_tests.sh` starts pytest with the shell's environment. Some of
those tests start `run_tests.sh` again, so anything the outer lane left in that
environment is handed down -- and its `ALETHRA_RUN_DIR` is the coordinate that
must never be shared. This stub is that second caller, kept out of the
repository so the outer run's tree digest is not affected by it.
"""
'''
SPAWNER_BODY = '''

import os
import subprocess


def test_a_nested_lane_call_makes_its_own_run_directory():
    child = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    result = subprocess.run(
        ["bash", os.environ["ALETHRA_CONTRACT_SCRIPT"], "sqlite", "-q",
         os.environ["ALETHRA_CONTRACT_TARGET"]],
        capture_output=True, text=True, timeout=600, env=child,
    )
    assert result.returncode == 0, (
        "the nested lane answered: " + (result.stdout[-900:] + result.stderr[-900:])
    )
    assert "run directory already exists" not in result.stderr, result.stderr[-900:]
    assert "run directory already exists" not in result.stdout, result.stdout[-900:]
'''


def test_the_lane_keeps_its_output_coordinates_out_of_the_suite(tmp_path, stub):
    """The 2026-11-17 fixed-state run lost 21 nodes to exactly this leak.

    That run was started with `ALETHRA_RUN_DIR` naming its evidence directory,
    which every nested lane call then inherited, so each answered "the run
    directory already exists" and exited 2. Fifteen of the run's twenty-one
    failures were this single cause, and none of them said so: the ledger just
    showed the file each test lived in.
    """
    spawner = tmp_path / "lane_stub_spawn"
    spawner.mkdir()
    node = spawner / "test_lane_stub_spawn.py"
    node.write_text(SPAWNER_STUB + SPAWNER_BODY)
    pinned = tmp_path / "outer-run"
    result = run_lane(["sqlite", "-q", str(node)], tmp_path,
                      ALETHRA_RUN_DIR=pinned,
                      ALETHRA_CONTRACT_SCRIPT=str(SCRIPT),
                      ALETHRA_CONTRACT_TARGET=str(stub))
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-1500:]
    assert run_dir_of(result.stdout) == pinned, (
        "the outer lane did not use the directory it was named, so this run "
        "proved nothing about inheritance"
    )
    assert (pinned / "exit-code.txt").read_text().strip() == "0"
