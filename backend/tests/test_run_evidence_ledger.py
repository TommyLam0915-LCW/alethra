"""The evidence ledger has to refuse, and refusing is what these nodes check.

``backend/scripts/run_evidence.py`` reconciles one run's own artifacts:
``collect.txt`` (the node ids selected), ``junit.xml`` (the outcome records),
``pytest.log`` (the sentences the skips printed) and ``exit-code.txt``. It
exists because the 2026-11-16 full-suite report said 3479 collected while
3298 passed + 183 skipped + 1 failed summed to 3482, and nothing in the tree
could say which number came from which artifact. The mechanism is a shape in
pytest's own JUnit output: a module skipped at collection time writes one
``<testcase classname="" name="tests.test_x">`` outcome record while
contributing zero collected items, so reported units exceed items by exactly the
number of collection-level records -- and only JUnit distinguishes the two.

Every node below feeds the script a synthetic run directory, so the assertions
are about its arithmetic and its refusals rather than about this checkout's
3,480 nodes. The ``classname``/``name`` pairs are written out literally instead
of being derived from the script's own key function, because a fixture built
with the code under test cannot show that the code matches what pytest emits.
"""

import json
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "run_evidence.py"

PG = "PG-ONLY [tests/test_locks.py:12]: needs a real PostgreSQL row lock"
LIVE = "live-stack probe: ALETHRA_LIVE_PROBES is not set to 1"
LOOSE = "TODO: rewrite this fixture"
# Every real PG-ONLY sentence in this repository says "PostgreSQL's", and that
# apostrophe is what decides which quote character pytest's repr uses.
PG_APOS = "PG-ONLY [tests/test_locks.py:12]: PostgreSQL's row lock is the thing held"


def item(case, classname, name, outcome=None, reason=None):
    """One record in the shape pytest writes for a collected node."""
    body = ""
    if outcome == "skipped":
        body = f'<skipped message="{escape(reason)}" type="Skipped"/>'
    elif outcome == "failed":
        body = '<failure message="AssertionError: planned"/>'
    elif outcome == "error":
        body = '<error message="collect_error: planned"/>'
    case.append(f'<testcase classname="{classname}" name="{name}" time="0.100">{body}'
                f'</testcase>')


def collection(case, dotted_module, reason, path="/abs/backend/tests/test_mods.py", line=30):
    """One record for a module skipped while it was being collected.

    This is the record type that made 3482 units come out of 3479 collected
    items: ``classname`` is empty, ``name`` is the module, and the sentence that
    excuses it is in the element text rather than in ``message``.
    """
    text = f"('{path}', {line}, \"Skipped: {escape(reason)}\")"
    case.append(f'<testcase classname="" name="{dotted_module}" time="0.000">'
                f'<skipped message="collection skipped">{text}</skipped></testcase>')


def junit(cases, tests, skipped=0, failures=0, errors=0):
    return ('<testsuites><testsuite name="pytest" errors="%d" failures="%d" '
            'skipped="%d" tests="%d" time="1.000">%s</testsuite></testsuites>'
            % (errors, failures, skipped, tests, "".join(cases)))


def console(reasons):
    """The lines ``-rfEs`` prints, one per skip unit."""
    return "".join(f"SKIPPED [1] tests/x.py:{30 + i}: {r}\n"
                   for i, r in enumerate(reasons))


def write_run(tmp_path, collected, junit_text, log, exit_code="0", name="run-1"):
    run_dir = tmp_path / name
    run_dir.mkdir()
    header = f"{len(collected)} tests collected in 1.00s\n" if collected else \
        "no tests collected in 1.00s\n"
    (run_dir / "collect.txt").write_text("\n".join(collected) + "\n" + header)
    (run_dir / "junit.xml").write_text(junit_text)
    (run_dir / "pytest.log").write_text(log)
    (run_dir / "exit-code.txt").write_text(exit_code + "\n")
    (run_dir / "identity.txt").write_text(
        "command: ./backend/scripts/run_tests.sh sqlite -q\n"
        "git: " + "0" * 40 + "\n"
        f"selection on this tree: {len(collected)} tests collected in 1.00s\n")
    return run_dir


def budget(**over):
    numbers = {"collected": 2, "total": 2, "pg_only": 2, "live_stack": 1, "other": 1}
    numbers.update(over)
    return numbers


def run_script(run_dir, *args):
    return subprocess.run([sys.executable, str(SCRIPT), str(run_dir), *args],
                          capture_output=True, text=True, timeout=120)


@pytest.fixture()
def pinned(tmp_path):
    """A manifest and a ceiling that two nodes -- one file, one root suite -- fit."""
    manifest = tmp_path / "collected_nodes.txt"
    manifest.write_text("tests/test_a.py::test_x\n"
                        "test_provider_isolation.py::test_y\n")
    ceiling = tmp_path / "budget.json"
    ceiling.write_text(json.dumps(budget()))
    return manifest, ceiling


class TestLedgerCloses:
    def test_a_collection_level_skip_explains_the_unit_it_adds(self, tmp_path):
        cases = []
        item(cases, "tests.test_a", "test_x", "skipped", PG)
        collection(cases, "tests.test_mods", PG)
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x"],
                            junit(cases, tests=2, skipped=2),
                            "collected 1 item\n" + console([PG, PG]) + "2 skipped in 1.00s\n")
        result = run_script(run_dir, "ledger")
        assert result.returncode == 0, result.stderr
        assert "reported units     2 = collected 1 + collection-level 1 -> OK" in result.stdout
        assert "collection-level   1 module(s): tests.test_mods" in result.stdout
        assert "pg_only=2" in result.stdout

    def test_the_units_and_items_reconcile_when_every_node_is_recorded(self, tmp_path, pinned):
        manifest, ceiling = pinned
        cases = []
        item(cases, "tests.test_a", "test_x")
        item(cases, "test_provider_isolation", "test_y", "skipped", PG)
        run_dir = write_run(
            tmp_path,
            ["tests/test_a.py::test_x", "test_provider_isolation.py::test_y"],
            junit(cases, tests=2, skipped=1),
            "collected 2 items\n" + console([PG]) + "1 passed, 1 skipped in 1.00s\n")
        result = run_script(run_dir, "check", "--manifest", str(manifest),
                            "--budget", str(ceiling))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "evidence gate: ledger closes" in result.stdout

    def test_a_root_level_suite_id_is_not_prefixed_to_make_it_match(self, tmp_path):
        """Three suites live at backend/, so their ids legitimately have no tests/."""
        cases = []
        item(cases, "test_provider_isolation", "test_y")
        run_dir = write_run(tmp_path, ["test_provider_isolation.py::test_y"],
                            junit(cases, tests=1),
                            "collected 1 item\n1 passed in 1.00s\n")
        ledger = run_script(run_dir, "ledger")
        assert "unresolved records  0" in ledger.stdout, ledger.stdout


class TestLedgerRefuses:
    def test_an_unexplained_unit_delta_cannot_be_closed_by_subtraction(self, tmp_path):
        """3479 collected against 3482 reported units, with one collection record."""
        cases = []
        item(cases, "tests.test_a", "test_x")
        item(cases, "tests.test_a", "test_z")
        collection(cases, "tests.test_mods", PG)
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x", "tests/test_a.py::test_z"],
                            junit(cases, tests=4, skipped=1),
                            "collected 2 items\n" + console([PG]) + "3 passed, 1 skipped in 1.00s\n")
        result = run_script(run_dir, "check")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "not accounted for by collection-level records" in result.stderr, result.stderr

    def test_a_collected_node_that_recorded_nothing_is_reported(self, tmp_path):
        """A file skipped whole after collection leaves an item with no record."""
        cases = []
        item(cases, "tests.test_a", "test_x")
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x", "tests/test_a.py::test_gone"],
                            junit(cases, tests=1),
                            "collected 2 items\n1 passed in 1.00s\n")
        result = run_script(run_dir, "check")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "produced no outcome record" in result.stderr, result.stderr

    def test_a_record_with_no_matching_node_is_unresolved_not_guessed(self, tmp_path):
        """Line-position inference is what the ledger replaces, not repeats."""
        cases = []
        item(cases, "tests.test_a", "test_x")
        item(cases, "tests.test_renamed", "test_old_name", "skipped", PG)
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x"],
                            junit(cases, tests=2, skipped=1),
                            "collected 1 item\n" + console([PG]) + "1 passed, 1 skipped in 1.00s\n")
        result = run_script(run_dir, "check")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "could not be mapped to a collected node id" in result.stderr, result.stderr
        assert "tests.test_renamed::test_old_name" in result.stdout, result.stdout

    def test_one_node_recorded_twice_is_not_a_count_of_nodes(self, tmp_path):
        cases = []
        item(cases, "tests.test_a", "test_x")
        item(cases, "tests.test_a", "test_x")
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x"],
                            junit(cases, tests=2),
                            "collected 1 item\n2 passed in 1.00s\n")
        result = run_script(run_dir, "check")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "duplicate node id(s) recorded" in result.stderr, result.stderr

    def test_the_two_censuses_are_crosschecked_instead_of_one_being_chosen(
        self, tmp_path
    ):
        """junit says PostgreSQL, the console says a TODO: one of them is wrong."""
        cases = []
        item(cases, "tests.test_a", "test_x", "skipped", PG)
        item(cases, "tests.test_a", "test_y", "skipped", LOOSE)
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x", "tests/test_a.py::test_y"],
                            junit(cases, tests=2, skipped=2),
                            "collected 2 items\n" + console([PG, PG]) + "2 skipped in 1.00s\n")
        result = run_script(run_dir, "check")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "the two censuses of the same run disagree" in result.stderr, result.stderr
        assert "other: console says 0, the recorded outcomes say 1" in result.stderr

    def test_a_collection_level_skip_keeps_its_category_in_both_censuses(self, tmp_path):
        """The real PG-ONLY sentences contain an apostrophe, and that changed quotes.

        pytest writes a module skip's element text as the repr of a tuple, and
        Python quotes the inner string with `"` as soon as it carries a `'` --
        which every one of the three whole-file PostgreSQL skips in the
        2026-11-17 fixed-state run does, because they say "PostgreSQL's". A
        reader written for the single-quoted shape found nothing, handed the
        classifier the placeholder `collection skipped`, and reported
        pg_only=161/other=4 from the outcome records against 164/1 from the
        console: the same run, classified two ways, silently.
        """
        cases = []
        item(cases, "tests.test_a", "test_x", "skipped", PG_APOS)
        collection(cases, "tests.test_mods", PG_APOS)
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x"],
                            junit(cases, tests=2, skipped=2),
                            "collected 1 item\n" + console([PG_APOS, PG_APOS])
                            + "2 skipped in 1.00s\n")
        result = run_script(run_dir, "check")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "skip categories    pg_only=2" in result.stdout, result.stdout
        assert "-> agrees with the recorded outcomes" in result.stdout, result.stdout

    def test_a_parameter_that_contains_the_separator_is_still_one_node(self, tmp_path):
        """`test_loopback_hosts[::1]` is an IPv6 literal, not a class boundary.

        Splitting the node id on every `::` turned it into the name `1]` under
        a class ending in `[`, which matches no JUnit record -- so one test that
        passed cleanly was booked twice over as a collected node with no outcome
        *and* an outcome with no node. The totals still added up, which is why
        only an exact-node-id comparison can see this.
        """
        node = "tests/test_a.py::TestParsingHelpers::test_loopback_hosts[::1]"
        cases = []
        item(cases, "tests.test_a.TestParsingHelpers", "test_loopback_hosts[::1]")
        run_dir = write_run(tmp_path, [node], junit(cases, tests=1),
                            "collected 1 item\n1 passed in 1.00s\n")
        result = run_script(run_dir, "check")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "UNRESOLVED" not in result.stdout, result.stdout
        assert "collected items    1" in result.stdout, result.stdout

    def test_a_new_excuse_cannot_hide_in_the_pg_only_ceiling(self, tmp_path):
        cases = []
        item(cases, "tests.test_a", "test_x", "skipped", LOOSE)
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x"],
                            junit(cases, tests=1, skipped=1),
                            "collected 1 item\n" + console([LOOSE]) + "1 skipped in 1.00s\n")
        ceiling = tmp_path / "budget.json"
        ceiling.write_text(json.dumps(budget(collected=1, total=1, pg_only=1,
                                             live_stack=0, other=0)))
        result = run_script(run_dir, "check", "--budget", str(ceiling))
        assert result.returncode == 1, result.stdout + result.stderr
        assert "other: measured 1 > ceiling 0" in result.stderr, result.stderr

    def test_a_missing_artifact_is_a_refusal_not_an_empty_ledger(self, tmp_path):
        cases = []
        item(cases, "tests.test_a", "test_x")
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x"], junit(cases, tests=1),
                            "collected 1 item\n1 passed in 1.00s\n")
        (run_dir / "junit.xml").unlink()
        result = run_script(run_dir, "ledger")
        assert result.returncode == 2, result.stdout + result.stderr
        assert "is missing" in result.stderr, result.stderr

    def test_a_truncated_junit_is_refused_rather_than_read_as_zero_records(self, tmp_path):
        cases = []
        item(cases, "tests.test_a", "test_x")
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x"], junit(cases, tests=1),
                            "collected 1 item\n1 passed in 1.00s\n")
        (run_dir / "junit.xml").write_text(junit(cases, tests=1)[:120])
        result = run_script(run_dir, "ledger")
        assert result.returncode == 2, result.stdout + result.stderr
        assert "not parseable" in result.stderr, result.stderr

    def test_a_whole_suite_run_without_a_pinned_selection_is_refused(self, tmp_path):
        cases = []
        item(cases, "tests.test_a", "test_x")
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x"], junit(cases, tests=1),
                            "collected 1 item\n1 passed in 1.00s\n")
        result = run_script(run_dir, "check", "--manifest", str(tmp_path / "absent.txt"))
        assert result.returncode == 1, result.stdout + result.stderr
        assert "no pinned selection manifest" in result.stderr, result.stderr

    def test_a_pinned_node_that_stopped_being_collected_is_named(self, tmp_path, pinned):
        """A deleted test is invisible to a total, and this is the point of the manifest."""
        manifest, ceiling = pinned
        cases = []
        item(cases, "tests.test_a", "test_x")
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x"], junit(cases, tests=1),
                            "collected 1 item\n1 passed in 1.00s\n")
        result = run_script(run_dir, "check", "--manifest", str(manifest),
                            "--budget", str(ceiling))
        assert result.returncode == 1, result.stdout + result.stderr
        assert "no longer collected" in result.stderr, result.stderr
        assert "test_provider_isolation.py::test_y" in result.stderr, result.stderr

    def test_a_focused_run_is_still_checked_for_its_own_arithmetic(self, tmp_path):
        """--explicit-selection switches off the ceiling, not the reconciliation."""
        cases = []
        item(cases, "tests.test_a", "test_x")
        item(cases, "tests.test_a", "test_z")
        collection(cases, "tests.test_mods", PG)
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x", "tests/test_a.py::test_z"],
                            junit(cases, tests=5, skipped=1),
                            "collected 2 items\n" + console([PG]) + "4 passed, 1 skipped in 1.00s\n")
        result = run_script(run_dir, "check", "--explicit-selection")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "ceiling does not apply" in result.stdout, result.stdout
        assert "not accounted for by collection-level records" in result.stderr, result.stderr


class TestRepinningNeedsAHuman:
    def test_a_clean_run_pins_after_its_diff_is_acknowledged(self, tmp_path):
        cases = []
        item(cases, "tests.test_a", "test_x")
        item(cases, "tests.test_a", "test_new")
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x", "tests/test_a.py::test_new"],
                            junit(cases, tests=2),
                            "collected 2 items\n2 passed in 1.00s\n")
        out_manifest = tmp_path / "new_manifest.txt"
        out_budget = tmp_path / "new_budget.json"
        common = ["pin", "--manifest-out", str(out_manifest), "--budget-out", str(out_budget)]

        refused = run_script(run_dir, *common)
        assert refused.returncode == 2, refused.stdout + refused.stderr
        assert "refused: re-pinning requires --ack-diff" in refused.stderr, refused.stderr

        digest = refused.stdout.split("acknowledgement digest: ")[1].splitlines()[0].strip()
        accepted = run_script(run_dir, *common, "--ack-diff", digest)
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr
        assert out_manifest.read_text() == ("tests/test_a.py::test_new\n"
                                            "tests/test_a.py::test_x\n")
        written = json.loads(out_budget.read_text())
        assert written["collected"] == 2 and written["total"] == 0, written
        assert written["provenance"]["run_dir"] == str(run_dir)

    def test_pin_refuses_a_run_whose_ledger_does_not_close(self, tmp_path):
        """A ceiling is generated from a measurement, never from a broken one."""
        cases = []
        item(cases, "tests.test_a", "test_x")
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x"], junit(cases, tests=1),
                            "collected 1 item\n1 passed in 1.00s\n")
        (run_dir / "collect.txt").write_text(
            "tests/test_a.py::test_x\ntests/test_a.py::test_ghost\nno tests collected\n")
        result = run_script(run_dir, "pin", "--manifest-out", str(tmp_path / "m.txt"),
                            "--budget-out", str(tmp_path / "b.json"))
        assert result.returncode == 2, result.stdout + result.stderr
        assert "whose own ledger does not close" in result.stderr, result.stderr
        assert not (tmp_path / "m.txt").exists(), "a refused pin still wrote the manifest"

    def test_pin_refuses_a_run_that_did_not_exit_as_a_measurement(self, tmp_path):
        cases = []
        item(cases, "tests.test_a", "test_x")
        run_dir = write_run(tmp_path, ["tests/test_a.py::test_x"], junit(cases, tests=1),
                            "collected 1 item\n1 passed in 1.00s\n", exit_code="2")
        result = run_script(run_dir, "pin", "--manifest-out", str(tmp_path / "m.txt"),
                            "--budget-out", str(tmp_path / "b.json"), "--ack-diff", "0" * 64)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "not a measurement of a suite that ran" in result.stderr, result.stderr
