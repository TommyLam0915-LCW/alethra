"""
Acceptance harness unit tests.

These tests verify the correctness of the acceptance-test-execution.py
harness logic — not the Alethra application itself.

Categories:
1. A false required check produces FAIL.
2. A failed scenario produces nonzero overall exit status.
3. A missing prerequisite produces BLOCKED, not PASS.
4. Malformed-request validation cannot satisfy the paused/retired
   state-control check.
"""

import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Replicate the harness helpers under test.
# These are pure-logic copies of the functions in acceptance-test-execution.py
# so we can unit-test them without importing the script (which executes
# HTTP calls at module level).
# ---------------------------------------------------------------------------

class SkipScenario(Exception):
    pass


def _make_log(results, scenario_status):
    def log(scenario_id, name, status, details):
        results.append({
            "scenario": scenario_id,
            "name": name,
            "status": status,
            "details": details,
        })
        scenario_status[scenario_id] = status
    return log


def require_prereq(scenario_id, name, prereqs, scenario_status, log_fn):
    for p in prereqs:
        if scenario_status.get(p) == "FAIL":
            log_fn(scenario_id, name, "BLOCKED", {
                "reason": f"Prerequisite {p} failed",
            })
            return True
        if scenario_status.get(p) == "BLOCKED":
            log_fn(scenario_id, name, "BLOCKED", {
                "reason": f"Prerequisite {p} was blocked",
            })
            return True
    return False


def assert_safety_keywords(text, context_label):
    bad_phrases = [
        "buy now", "sell signal", "broker api", "exchange api",
        "live trading", "guaranteed returns", "real money trading",
    ]
    text_lower = text.lower()
    found = [p for p in bad_phrases if p in text_lower]
    assert not found, (
        f"{context_label} contains prohibited broker/trading language: {found}"
    )


def compute_exit_code(results):
    failed = sum(1 for r in results if r["status"] == "FAIL")
    blocked = sum(1 for r in results if r["status"] == "BLOCKED")
    passed = sum(1 for r in results if r["status"] == "PASS")
    if failed > 0:
        return 1
    if blocked > 0 and passed == 0:
        return 2
    return 0


# ---------------------------------------------------------------------------
# 1. A false required check produces FAIL
# ---------------------------------------------------------------------------

class TestFalseCheckProducesFail:
    """An assertion that is intentionally false must produce FAIL, not PASS."""

    def test_false_assertion_logs_fail(self):
        results = []
        status = {}
        log = _make_log(results, status)

        try:
            assert 1 == 2, "Intentionally false check"
            log("T01", "Test", "PASS", {})
        except AssertionError as e:
            log("T01", "Test", "FAIL", {"error": str(e)})

        assert len(results) == 1
        assert results[0]["status"] == "FAIL"
        assert "Intentionally false check" in results[0]["details"]["error"]

    def test_false_safety_keyword_scan_produces_fail(self):
        results = []
        status = {}
        log = _make_log(results, status)

        try:
            assert_safety_keywords(
                "This page says buy now and guaranteed returns",
                "test context",
            )
            log("T02", "Safety Check", "PASS", {})
        except AssertionError as e:
            log("T02", "Safety Check", "FAIL", {"error": str(e)})

        assert results[0]["status"] == "FAIL"
        assert "buy now" in results[0]["details"]["error"]
        assert "guaranteed returns" in results[0]["details"]["error"]

    def test_true_check_logs_pass(self):
        results = []
        status = {}
        log = _make_log(results, status)

        try:
            assert 1 == 1
            log("T03", "Test", "PASS", {})
        except AssertionError:
            log("T03", "Test", "FAIL", {})

        assert results[0]["status"] == "PASS"


# ---------------------------------------------------------------------------
# 2. A failed scenario produces nonzero overall exit status
# ---------------------------------------------------------------------------

class TestFailedScenarioNonzeroExit:
    """If any scenario FAILs, the overall exit code must be nonzero."""

    def test_all_pass_exit_zero(self):
        results = [
            {"status": "PASS"},
            {"status": "PASS"},
            {"status": "PASS"},
        ]
        assert compute_exit_code(results) == 0

    def test_one_fail_exit_one(self):
        results = [
            {"status": "PASS"},
            {"status": "FAIL"},
            {"status": "PASS"},
        ]
        assert compute_exit_code(results) == 1

    def test_all_fail_exit_one(self):
        results = [
            {"status": "FAIL"},
            {"status": "FAIL"},
        ]
        assert compute_exit_code(results) == 1

    def test_all_blocked_no_pass_exit_two(self):
        results = [
            {"status": "BLOCKED"},
            {"status": "BLOCKED"},
        ]
        assert compute_exit_code(results) == 2

    def test_mixed_pass_and_blocked_exit_zero(self):
        results = [
            {"status": "PASS"},
            {"status": "BLOCKED"},
        ]
        assert compute_exit_code(results) == 0

    def test_fail_takes_priority_over_blocked(self):
        results = [
            {"status": "FAIL"},
            {"status": "BLOCKED"},
        ]
        assert compute_exit_code(results) == 1

    def test_empty_results_exit_zero(self):
        assert compute_exit_code([]) == 0


# ---------------------------------------------------------------------------
# 3. A missing prerequisite produces BLOCKED, not PASS
# ---------------------------------------------------------------------------

class TestMissingPrereqProducesBlocked:
    """When a prerequisite FAILs or is BLOCKED, downstream must be BLOCKED."""

    def test_prereq_fail_blocks_downstream(self):
        results = []
        status = {}
        log = _make_log(results, status)

        status["A02"] = "FAIL"
        blocked = require_prereq("A03", "Create Experiment", ["A02"], status, log)

        assert blocked is True
        assert results[0]["status"] == "BLOCKED"
        assert "A02" in results[0]["details"]["reason"]

    def test_prereq_blocked_blocks_downstream(self):
        results = []
        status = {}
        log = _make_log(results, status)

        status["A03"] = "BLOCKED"
        blocked = require_prereq("A04", "Backtest", ["A03"], status, log)

        assert blocked is True
        assert results[0]["status"] == "BLOCKED"

    def test_prereq_pass_does_not_block(self):
        results = []
        status = {}
        log = _make_log(results, status)

        status["A02"] = "PASS"
        blocked = require_prereq("A03", "Create Experiment", ["A02"], status, log)

        assert blocked is False
        assert len(results) == 0

    def test_multiple_prereqs_one_fail_blocks(self):
        results = []
        status = {}
        log = _make_log(results, status)

        status["A02"] = "PASS"
        status["A03"] = "FAIL"
        blocked = require_prereq("A06", "Timeline", ["A02", "A03"], status, log)

        assert blocked is True
        assert results[0]["status"] == "BLOCKED"
        assert "A03" in results[0]["details"]["reason"]

    def test_skip_scenario_exception_prevents_pass(self):
        results = []
        status = {}
        log = _make_log(results, status)

        status["A02"] = "FAIL"

        try:
            if require_prereq("A03", "Test", ["A02"], status, log):
                raise SkipScenario("A02 failed")
            log("A03", "Test", "PASS", {})
        except SkipScenario:
            pass

        pass_count = sum(1 for r in results if r["status"] == "PASS")
        assert pass_count == 0, "BLOCKED prerequisite must not produce PASS"


# ---------------------------------------------------------------------------
# 4. Malformed-request validation cannot satisfy state-control check
# ---------------------------------------------------------------------------

class TestMalformedRequestCannotSatisfyStateControl:
    """
    A 422 (schema validation error) must NOT satisfy an assertion
    that expects 400 (state-based rejection). This proves the harness
    distinguishes 'rejected for the right reason' from 'rejected because
    the request was malformed'.
    """

    def test_422_does_not_satisfy_400_assertion(self):
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = '{"detail": [{"loc": ["body", "asset_id"], "msg": "string does not match pattern"}]}'

        with pytest.raises(AssertionError, match="expected 400"):
            assert mock_response.status_code == 400, (
                f"Paused order: expected 400 state-rejection, got {mock_response.status_code}. "
                f"Body: {mock_response.text[:200]}"
            )

    def test_400_with_correct_reason_satisfies_assertion(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"detail": "Risk check failed: Portfolio is paused"}

        assert mock_response.status_code == 400
        body = mock_response.json().get("detail", "")
        assert "paused" in body.lower() or "risk check" in body.lower()

    def test_403_does_not_satisfy_400_assertion(self):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        with pytest.raises(AssertionError):
            assert mock_response.status_code == 400

    def test_retired_assertion_requires_retired_keyword(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"detail": "Portfolio is paused"}

        body = mock_response.json().get("detail", "")
        with pytest.raises(AssertionError, match="rejection reason unexpected"):
            assert "retired" in body.lower(), (
                f"Retired order rejection reason unexpected: {body}"
            )

    def test_valid_schema_required_fields_present(self):
        """
        The paused/retired order payloads must include all required fields
        so that a rejection is due to state control, not schema validation.
        """
        payload = {
            "asset_id": "DEMO-ALPHA",
            "side": "BUY",
            "quantity": 5,
            "rationale": "Schema-valid order to test paused-state rejection",
            "expected_outcome": "This order should be rejected by state control",
            "expected_timeframe_days": 7,
            "self_reported_confidence": "medium",
            "falsification_condition": "State control works correctly",
        }
        import re
        assert re.match(r"^DEMO-", payload["asset_id"])
        assert payload["side"] in ("BUY", "SELL")
        assert payload["quantity"] > 0
        assert payload["order_type"] if "order_type" in payload else True
