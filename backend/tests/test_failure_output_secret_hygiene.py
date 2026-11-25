"""The lane's own failure output must not carry a credential.

A failing test prints the repr of every fixture value it asked for, and the
isolated runner tees that into ``<run dir>/pytest.log`` beside ``junit.xml`` —
files that travel as evidence. So whenever any test using ``auth_headers``
failed for any reason, the token that request was carrying went into the log.

These nodes use invalid sentinels only: no token issued by the suite is read,
printed or copied here. They pin three things:

* the redaction hook is registered by the very conftest the lane loads;
* the shared credential fixtures still authorize while withholding their value;
* a failing child session keeps its diagnostics but loses the credentials, in
  the console log and in ``junit.xml`` — and the control child, which loads no
  hook, still prints them, so the pass above is about the hook;
* the three *captured* surfaces are read apart, because they are written by
  three different plugins — the console's 「Captured stdout call」 block, the
  JUnit ``<system-out>``/``<system-err>`` elements, and the failure record
  itself. Each gate carries its own control, so a 0 on one of them names the
  channel it measured instead of standing in for a channel that never opened;
* the JUnit streams of a *failed* node are written from its teardown report
  (junitxml.py:597-599), which is not itself failed — so the hook redacts the
  captured sections of every report, and the passing plus skipped node streams
  below are the control that says so.
"""
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from tests import failure_output_hygiene
from tests.conftest import SecretHeaders

TESTS_DIR = Path(__file__).resolve().parent

# Shaped like the three secrets this suite can print, and worthless: nothing
# signed them, no key material, no row behind them.
SENTINEL = "SENTINEL-NOT-A-REAL-CREDENTIAL"
JWT_SENTINEL = "eyJ%sx.%s.signature" % (SENTINEL, SENTINEL)
BEARER_SENTINEL = "Bearer " + JWT_SENTINEL
KEY_SENTINEL = "alk_" + SENTINEL
REDACTED_BEARER = failure_output_hygiene.PATTERNS[0][1]

CHILD_TESTS = '''
import pytest

SENTINEL_JWT = "%s"
SENTINEL_KEY = "%s"


@pytest.fixture()
def auth_headers():
    return {"Authorization": "Bearer " + SENTINEL_JWT}


@pytest.fixture()
def auth_token():
    return SENTINEL_JWT


@pytest.mark.skipif(True, reason="skip census keeps reading this reason")
def test_a_skip_reason_stays_readable():
    pass


def test_a_dict_valued_fixture_auth_headers(auth_headers):
    assert 1 == 2


def test_a_string_valued_fixture_auth_token(auth_token):
    assert 1 == 2


def test_an_assert_message_carrying_a_response_body():
    body = \'{"access_token": "%%s", "raw_key": "%%s"}\' %% (SENTINEL_JWT, SENTINEL_KEY)
    assert 1 == 2, body
''' % (JWT_SENTINEL, KEY_SENTINEL)

CHILD_CONFTEST_WITH_HOOK = '''
import sys

sys.path.insert(0, %r)
from failure_output_hygiene import pytest_runtest_makereport  # noqa: E402,F401
''' % (str(TESTS_DIR),)

CHILD_CONFTEST_WITHOUT_HOOK = """
# The control: no redaction hook is registered in this session.
"""

FAILED_NODES = (
    "test_a_dict_valued_fixture_auth_headers",
    "test_a_string_valued_fixture_auth_token",
    "test_an_assert_message_carrying_a_response_body",
)

# Round-24 item 4: the credential can also leave a test through a stream it
# wrote, which is a different plugin from the one that prints the traceback.
# One node fails with the sentinel on stdout, stderr and the log, and one passes
# and one skips with it on stdout, so each surface below is asked about on its
# own channel — and, for the two non-failing outcomes, on its own report.
FAILING_NODE = "test_a_failing_node_prints_to_every_stream"
PASSING_NODE = "test_a_passing_node_prints_to_stdout"
SKIPPED_NODE = "test_a_skipped_node_prints_to_stdout"
SKIP_SENTENCE = "skip census keeps reading this sentence"

CHILD_CAPTURE_TESTS = '''
import logging
import sys

import pytest

SENTINEL_JWT = "%s"
SENTINEL_KEY = "%s"


def test_a_failing_node_prints_to_every_stream():
    print("stdout keeps this line: " + SENTINEL_JWT)
    print("stderr keeps this line: " + SENTINEL_KEY, file=sys.stderr)
    logging.getLogger("child.hygiene").warning("log keeps this line: " + SENTINEL_JWT)
    assert 1 == 2


def test_a_passing_node_prints_to_stdout():
    print("a passing node prints: " + SENTINEL_JWT)


def test_a_skipped_node_prints_to_stdout():
    print("a skipped node prints: " + SENTINEL_JWT)
    pytest.skip("%s")
''' % (JWT_SENTINEL, KEY_SENTINEL, SKIP_SENTENCE)

# ``junit_logging`` defaults to "no", so a lane that wants the captured streams in
# its machine-readable artifact has to ask; these nodes ask, and say so.
CAPTURE_ARGS = ("-o", "junit_logging=all")


def _junit_streams(junit_xml: str, node_name: str) -> str:
    """The ``<system-out>``/``<system-err>`` text one node's record carries."""
    for case in ET.fromstring(junit_xml).iter("testcase"):
        if case.get("name") != node_name:
            continue
        return "".join(
            child.text or ""
            for child in case
            if child.tag in ("system-out", "system-err")
        )
    raise AssertionError("junit.xml holds no <testcase> named %s" % node_name)


def _junit_failure(junit_xml: str, node_name: str) -> str:
    """One node's failure record: the ``message`` attribute and the traceback."""
    for case in ET.fromstring(junit_xml).iter("testcase"):
        if case.get("name") != node_name:
            continue
        for child in case:
            if child.tag == "failure":
                return "%s\n%s" % (child.get("message") or "", child.text or "")
        raise AssertionError("the record for %s holds no <failure>" % node_name)
    raise AssertionError("junit.xml holds no <testcase> named %s" % node_name)


def _console_stream(console: str, header: str) -> str:
    """One captured-stream block of the terminal report, up to the next divider."""
    match = re.search(re.escape(header) + r"[^\n]*\n(.*?)(?=\n[-=]{10,}|\Z)", console, re.S)
    assert match, "the console printed no %r block at all" % header
    return match.group(1)


def _run_child(tmp_path: Path, conftest_source: str, tests_source=CHILD_TESTS, extra_args=()):
    """Run a throwaway pytest session that fails while holding sentinels."""
    (tmp_path / "conftest.py").write_text(conftest_source)
    (tmp_path / "test_child_hygiene.py").write_text(tests_source)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST")
    }
    # The reporting flags the lane itself adds, plus the machine-readable twin
    # of pytest.log, since both files are shared as evidence.
    command = [
        sys.executable, "-m", "pytest", "test_child_hygiene.py",
        "-rfEs", "--junitxml=junit.xml", "-p", "no:cacheprovider", *extra_args,
    ]
    proc = subprocess.run(
        command, cwd=str(tmp_path), env=env,
        capture_output=True, text=True, timeout=300,
    )
    junit_path = tmp_path / "junit.xml"
    assert junit_path.exists(), proc.stdout + proc.stderr
    return proc, proc.stdout + proc.stderr, junit_path.read_text()


def _assert_records_the_failures(console: str):
    """The failure record has to survive: this is not a quieter suite."""
    assert "3 failed, 1 skipped" in console, console
    for node in FAILED_NODES:
        assert node in console, console
    assert "AssertionError" in console
    assert "skip census keeps reading this reason" in console


def test_the_lane_conftest_registers_the_redaction_hook(request):
    """The module works on its own; this proves the lane actually loads it."""
    implementations = (
        request.config.pluginmanager.hook.pytest_runtest_makereport.get_hookimpls()
    )
    wired = [
        impl for impl in implementations
        if impl.function is failure_output_hygiene.pytest_runtest_makereport
    ]
    assert len(wired) == 1, [getattr(impl, "plugin", None) for impl in implementations]


def test_the_shared_credential_fixtures_hide_their_value_yet_still_authorize(
    client, auth_headers, admin_headers
):
    for headers in (auth_headers, admin_headers):
        assert isinstance(headers, SecretHeaders)
        printed_value = headers["Authorization"] in repr(headers)
        assert printed_value is False, repr(headers)
        assert headers["Authorization"].startswith("Bearer ")
        assert len(dict(headers)) == 1
        assert {**headers, "X-Trace": "1"}["X-Trace"] == "1"
        assert client.get("/api/auth/me", headers=headers).status_code == 200


def test_redaction_replaces_each_credential_shape_and_leaves_the_rest():
    message = (
        "AssertionError: {'access_token': '%s', 'raw_key': '%s'}"
        " | sent as 'Bearer %s' to /api/v1/orders"
        % (JWT_SENTINEL, KEY_SENTINEL, JWT_SENTINEL)
    )
    redacted = failure_output_hygiene.redact_text(message)

    assert SENTINEL not in redacted, redacted
    assert "AssertionError:" in redacted
    assert "| sent as 'Bearer" in redacted
    assert "' to /api/v1/orders" in redacted

    diagnostics = "assert 200 == 400\\n +  where 200 = <Response [200 OK]>.status_code"
    assert failure_output_hygiene.redact_text(diagnostics) == diagnostics


def test_a_failing_child_session_keeps_diagnostics_but_not_credentials(tmp_path):
    proc, console, junit = _run_child(tmp_path, CHILD_CONFTEST_WITH_HOOK)

    assert proc.returncode == 1, console
    _assert_records_the_failures(console)
    # The header line is still printed, which is what makes the record useful.
    assert "auth_headers = {'Authorization': '" in console, console
    assert REDACTED_BEARER in console, console

    assert SENTINEL not in console, console
    assert SENTINEL not in junit, junit


def test_the_control_child_session_without_the_hook_still_prints_them(tmp_path):
    proc, console, junit = _run_child(tmp_path, CHILD_CONFTEST_WITHOUT_HOOK)

    assert proc.returncode == 1, console
    _assert_records_the_failures(console)

    assert "auth_headers = {'Authorization': '%s'}" % BEARER_SENTINEL in console
    assert SENTINEL in junit, junit


# ── round-24 item 4: the captured streams, surface by surface ───────────────


def test_a_failing_node_loses_the_credential_on_every_captured_surface(tmp_path):
    """stdout, stderr and the log all carry it, and none of the surfaces keeps it.

    The three surfaces are checked apart because three different plugins write
    them: the terminal reporter prints ``report.sections``, ``junitxml`` copies
    ``report.capstdout`` into ``<system-out>``, and the failure record is the
    ``longrepr``. A green run here says each of the three was open and each
    redacted — not that one stood in for the others.
    """
    proc, console, junit = _run_child(
        tmp_path, CHILD_CONFTEST_WITH_HOOK, CHILD_CAPTURE_TESTS, CAPTURE_ARGS
    )

    assert proc.returncode == 1, console
    assert "1 failed, 1 passed, 1 skipped" in console, console
    assert FAILING_NODE in console, console

    stdout_block = _console_stream(console, "Captured stdout call")
    stderr_block = _console_stream(console, "Captured stderr call")
    log_block = _console_stream(console, "Captured log call")
    assert "stdout keeps this line: " in stdout_block, stdout_block
    assert "stderr keeps this line: " in stderr_block, stderr_block
    assert "log keeps this line: " in log_block, log_block
    assert SENTINEL not in stdout_block, stdout_block
    assert SENTINEL not in stderr_block, stderr_block
    assert SENTINEL not in log_block, log_block

    streams = _junit_streams(junit, FAILING_NODE)
    assert "stdout keeps this line: " in streams, streams
    assert "stderr keeps this line: " in streams, streams
    assert SENTINEL not in streams, streams
    # The failure record itself, which is a third element of the same file. The
    # passing node's stream is deliberately not checked here — node 3 below owns
    # that difference instead of this node passing by accident.
    assert SENTINEL not in _junit_failure(junit, FAILING_NODE), junit
    # An exception raised inside the hook would end the child session here, so a
    # green run also says the hook survives a report that carries output.
    assert "INTERNALERROR" not in console, console


def test_the_control_child_prints_the_credential_on_every_captured_surface(tmp_path):
    """Same corpus, no hook: each surface above has a branch that can catch fire.

    Without this the pass above could be a channel that never opened — the
    sentinel could be absent from ``<system-out>`` because pytest writes no
    such element, and the node would still read green.
    """
    proc, console, junit = _run_child(
        tmp_path, CHILD_CONFTEST_WITHOUT_HOOK, CHILD_CAPTURE_TESTS, CAPTURE_ARGS
    )

    assert proc.returncode == 1, console
    assert "1 failed, 1 passed, 1 skipped" in console, console

    assert SENTINEL in _console_stream(console, "Captured stdout call")
    assert SENTINEL in _console_stream(console, "Captured stderr call")
    assert SENTINEL in _console_stream(console, "Captured log call")
    assert SENTINEL in _junit_streams(junit, FAILING_NODE), junit
    # The two non-failing outcomes are the channels the hook used to return
    # before, so the control has to show they do carry a stream at all.
    assert "a passing node prints: " in _junit_streams(junit, PASSING_NODE)
    assert SENTINEL in _junit_streams(junit, PASSING_NODE), junit
    assert "a skipped node prints: " in _junit_streams(junit, SKIPPED_NODE)
    assert SENTINEL in _junit_streams(junit, SKIPPED_NODE), junit


def test_the_guard_reaches_non_failing_reports_and_the_lane_writes_no_captured_streams(tmp_path):
    """Which reports the hook covers, and which artifact the lane actually writes.

    Two facts this node keeps apart. First, the formal lane asks for ``--junitxml``
    and no ``junit_logging``, and pytest's default is ``no`` — measured on a child
    run below, not read out of a docstring — so the lane's machine-readable
    artifact holds no captured stream at all. Second, once a caller does open that
    channel, the node that failed is not the only one whose stream is written:
    junitxml emits the passing node's (``junit_log_passing_tests`` defaults to true)
    and the skipped node's, and it emits a failed node's from the *teardown*
    report, which is not itself failed. Redacting failed reports only therefore
    left all three raw — which is what the last two legs below now measure the
    other way round, while the skip sentence the census reads stays verbatim.
    """
    runner = (Path(__file__).resolve().parents[1] / "scripts" / "run_tests.sh").read_text()
    lane_line = [
        line for line in runner.splitlines()
        if "-m pytest" in line and "--junitxml" in line
    ]
    assert len(lane_line) == 1, lane_line
    assert "junit_logging" not in runner, runner
    assert not re.search(r"(?:^|\s)(?:-s|--capture=no|--capture\s+no)(?:\s|$)", lane_line[0])

    _proc, _console, default_junit = _run_child(
        tmp_path, CHILD_CONFTEST_WITH_HOOK, CHILD_CAPTURE_TESTS
    )
    tree = ET.fromstring(default_junit)
    captured = [case.tag for case in tree.iter() if case.tag in ("system-out", "system-err")]
    assert captured == [], "pytest stopped defaulting to junit_logging=no: %s" % captured

    proc, console, opened_junit = _run_child(
        tmp_path, CHILD_CONFTEST_WITH_HOOK, CHILD_CAPTURE_TESTS, CAPTURE_ARGS
    )
    assert proc.returncode == 1, console
    for node, prefix in (
        (PASSING_NODE, "a passing node prints: "),
        (SKIPPED_NODE, "a skipped node prints: "),
    ):
        streams = _junit_streams(opened_junit, node)
        assert prefix in streams, "the channel for %s never opened: %s" % (node, streams)
        assert SENTINEL not in streams, streams
    # The census reads ``SKIPPED [n] <nodeid>: <reason>`` lines, not a stream, so
    # widening the hook must not cost it the sentence.
    assert SKIP_SENTENCE in console, console
    assert "INTERNALERROR" not in console, console

