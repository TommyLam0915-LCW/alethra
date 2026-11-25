"""Keep credentials out of the suite's own failure output.

pytest prints, for a failing test, the repr of every fixture value the test
asked for plus the assertion message, and ``scripts/run_tests.sh`` tees that
into ``<run dir>/pytest.log`` — so any token a fixture holds outlives the run
that issued it. Two guards, over different paths:

* ``tests.conftest.SecretHeaders`` withholds the value on the object itself, for
  the shared credential fixtures, whatever traceback style is in force;
* the hook below rewrites the *report text*, which also covers the module-local
  fixtures and the ``assert …, resp.text`` messages the object-level guard
  cannot reach.

Only credential-shaped values are removed; the node id, the assert line and the
exception type stay. A skipped report's failure repr is left alone because
``scripts/skip_budget.py`` censuses the skip sentence from the same log — it
reads only lines starting with ``SKIPPED [`` (skip_budget.py:42, :72), never a
captured stream, so the sections of every report are redacted regardless of
outcome.

The sentinels live in ``tests/test_failure_output_secret_hygiene.py``, which
runs this hook over a generated child pytest session and fails if a sentinel
survives — so an upstream change to pytest's report internals is caught here
rather than discovered in a shared log.
"""
import re

import pytest

# ``Bearer <opaque>`` as the suite builds it for every authenticated request.
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}={0,}")
# An access token on its own, e.g. inside a response body printed by an assert.
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
# A raw API key, the one-time secret from ``app/auth/api_keys.py``.
RAW_API_KEY = re.compile(r"\balk_[A-Za-z0-9_-]{20,}")

PATTERNS = (
    (BEARER, "Bearer <redacted-credential>"),
    (JWT, "<redacted-jwt>"),
    (RAW_API_KEY, "alk_<redacted-key>"),
)

MARKER = "<redacted-credential>"


def redact_text(text):
    """Return ``text`` with every credential-shaped value replaced by a marker."""
    if not isinstance(text, str):
        return text
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_entry(entry):
    lines = getattr(entry, "lines", None)
    if isinstance(lines, list):
        entry.lines = [redact_text(line) for line in lines]
    funcargs = getattr(entry, "reprfuncargs", None)
    args = getattr(funcargs, "args", None)
    if isinstance(args, list):
        funcargs.args = [
            (arg[0], redact_text(arg[1])) if isinstance(arg, tuple) and len(arg) == 2
            else arg
            for arg in args
        ]
    locals_repr = getattr(entry, "reprlocals", None)
    local_lines = getattr(locals_repr, "lines", None)
    if isinstance(local_lines, list):
        locals_repr.lines = [redact_text(line) for line in local_lines]
    return entry


def redact_longrepr(longrepr):
    """Redact every printable part of a failure repr, in place."""
    if longrepr is None:
        return longrepr
    reprtracebacks = []
    chain = getattr(longrepr, "chain", None)
    if chain:
        reprtracebacks.extend(element[0] for element in chain)
    elif hasattr(longrepr, "reprtraceback"):
        reprtracebacks.append(longrepr.reprtraceback)
    for reprtraceback in reprtracebacks:
        for entry in getattr(reprtraceback, "reprentries", None) or []:
            _redact_entry(entry)
    crash = getattr(longrepr, "reprcrash", None)
    if crash is not None:
        crash.message = redact_text(crash.message)
    return longrepr


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    # ``TestReport.sections`` is ``(heading, content)`` — the heading already
    # carries the phase (``Captured stdout call``, reports.py:381-382), and every
    # report re-copies them from ``item._report_sections`` (reports.py:317), so
    # redacting one report leaves the next one raw.
    #
    # That matters because the captured streams reach ``junit.xml`` through the
    # *teardown* report: junitxml writes the call report's only when
    # ``junit_log_passing_tests`` is false (junitxml.py:589-590, default true at
    # :470) and writes the teardown report's unconditionally (junitxml.py:597-599)
    # — and a clean teardown report is not failed. Redact the streams of every
    # report, outcome aside.
    report.sections = [
        (name, redact_text(content)) for name, content in report.sections
    ]
    if not report.failed:
        return
    redact_longrepr(report.longrepr)
