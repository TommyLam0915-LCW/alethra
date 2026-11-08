"""No-DB tests for acquisition hook behavior matrix.

Tests the shared acquisition hook without requiring a real database:
- Session-local state isolation
- Result proxy observation
- Execute params validation
- Commit/rollback state clearing
- Timeout/abort error handling
- Verify-session close error propagation
- Signal/injection counting
"""
import pytest
import threading
from unittest.mock import MagicMock, Mock, patch
from sqlalchemy import text

from acquisition_hook import (
    create_hooked_session_factory,
    SessionLocalState,
    ResultProxy,
    AcquisitionHookError,
    TimeoutError,
    VerifyCloseError,
    abort_aware_wait_or_raise,
    abort_aware_wait,
    _is_acquisition_query,
    _extract_run_id_from_params,
)


# === Tests for helper functions ===

def test_is_acquisition_query_detects_pattern():
    """_is_acquisition_query detects the atomic UPDATE pattern."""
    query = """UPDATE paper_trading_runs
               SET status = 'running', updated_at = CURRENT_TIMESTAMP
               WHERE id = :id AND status IN ('queued', 'pending', 'failed')
                 AND cancel_requested = FALSE
               RETURNING id"""
    assert _is_acquisition_query(query) is True


def test_is_acquisition_query_rejects_non_acquisition():
    """_is_acquisition_query rejects non-acquisition queries."""
    assert _is_acquisition_query("SELECT * FROM users") is False
    assert _is_acquisition_query("UPDATE users SET name = 'test'") is False
    assert _is_acquisition_query("UPDATE paper_trading_runs SET status = 'cancelled'") is False


def test_extract_run_id_from_params_extracts_id():
    """_extract_run_id_from_params extracts the id field."""
    assert _extract_run_id_from_params({"id": 123, "other": "value"}) == 123
    assert _extract_run_id_from_params({"id": 456}) == 456


def test_extract_run_id_from_params_returns_none_for_missing():
    """_extract_run_id_from_params returns None when id is missing."""
    assert _extract_run_id_from_params({"other": "value"}) is None
    assert _extract_run_id_from_params(None) is None
    assert _extract_run_id_from_params({}) is None


# === Tests for SessionLocalState ===

def test_session_local_state_initial_values():
    """SessionLocalState initializes with correct defaults."""
    state = SessionLocalState()
    assert state.acquisition_query_detected is False
    assert state.acquisition_run_id is None
    assert state.signal_count == 0
    assert state.injection_count == 0


def test_session_local_state_independent_instances():
    """Each SessionLocalState instance is independent."""
    state1 = SessionLocalState()
    state2 = SessionLocalState()
    
    state1.acquisition_query_detected = True
    state1.signal_count = 5
    
    assert state2.acquisition_query_detected is False
    assert state2.signal_count == 0


# === Tests for ResultProxy ===

def test_result_proxy_delegates_fetchone():
    """ResultProxy delegates fetchone to wrapped result."""
    mock_result = MagicMock()
    mock_result.fetchone.return_value = ("running",)
    
    state = SessionLocalState()
    state.acquisition_query_detected = True
    state.acquisition_run_id = 123
    
    proxy = ResultProxy(mock_result, state, target_run_id=123)
    row = proxy.fetchone()
    
    assert row == ("running",)
    mock_result.fetchone.assert_called_once()


def test_result_proxy_validates_run_id_match():
    """ResultProxy validates run ID matches target."""
    mock_result = MagicMock()
    mock_result.fetchone.return_value = ("running",)
    
    state = SessionLocalState()
    state.acquisition_query_detected = True
    state.acquisition_run_id = 123
    
    proxy = ResultProxy(mock_result, state, target_run_id=123)
    row = proxy.fetchone()  # Should not raise
    
    assert row == ("running",)


def test_result_proxy_raises_on_run_id_mismatch():
    """ResultProxy raises AcquisitionHookError on run ID mismatch."""
    mock_result = MagicMock()
    mock_result.fetchone.return_value = ("running",)
    
    state = SessionLocalState()
    state.acquisition_query_detected = True
    state.acquisition_run_id = 123  # Session has 123
    
    proxy = ResultProxy(mock_result, state, target_run_id=456)  # But target is 456
    
    with pytest.raises(AcquisitionHookError, match="Run ID mismatch"):
        proxy.fetchone()


def test_result_proxy_delegates_other_attributes():
    """ResultProxy delegates non-intercepted attributes to wrapped result."""
    mock_result = MagicMock()
    mock_result.some_attribute = "test_value"
    
    state = SessionLocalState()
    proxy = ResultProxy(mock_result, state, target_run_id=123)
    
    assert proxy.some_attribute == "test_value"


# === Tests for abort_aware_wait_or_raise ===

def test_abort_aware_wait_or_raise_returns_when_event_set():
    """abort_aware_wait_or_raise returns immediately when event is already set."""
    event = threading.Event()
    event.set()
    
    # Should return without raising
    abort_aware_wait_or_raise(event, timeout=1.0)


def test_abort_aware_wait_or_raise_waits_for_event():
    """abort_aware_wait_or_raise waits until event is set."""
    event = threading.Event()
    
    def set_event_after_delay():
        import time
        time.sleep(0.1)
        event.set()
    
    thread = threading.Thread(target=set_event_after_delay)
    thread.start()
    
    # Should return after event is set
    abort_aware_wait_or_raise(event, timeout=1.0, poll_interval=0.05)
    
    thread.join()


def test_abort_aware_wait_or_raise_raises_on_timeout():
    """abort_aware_wait_or_raise raises TimeoutError on timeout."""
    event = threading.Event()  # Never set
    
    with pytest.raises(TimeoutError, match="Timeout after"):
        abort_aware_wait_or_raise(event, timeout=0.1, poll_interval=0.05)


# === Tests for abort_aware_wait (deprecated but still used) ===

def test_abort_aware_wait_returns_true_when_event_set():
    """abort_aware_wait returns True when event is set."""
    event = threading.Event()
    abort = threading.Event()
    event.set()
    
    assert abort_aware_wait(event, abort, timeout=1.0) is True


def test_abort_aware_wait_returns_false_on_abort():
    """abort_aware_wait returns False when abort is signaled."""
    event = threading.Event()
    abort = threading.Event()
    abort.set()
    
    assert abort_aware_wait(event, abort, timeout=1.0) is False


def test_abort_aware_wait_returns_false_on_timeout():
    """abort_aware_wait returns False on timeout."""
    event = threading.Event()
    abort = threading.Event()
    
    assert abort_aware_wait(event, abort, timeout=0.1, poll_interval=0.05) is False


# === Tests for create_hooked_session_factory ===

def test_hooked_factory_creates_session_with_local_state():
    """create_hooked_session_factory creates sessions with independent local state."""
    def factory_side_effect():
        return MagicMock()
    
    mock_factory = MagicMock(side_effect=factory_side_effect)
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session1 = hooked_factory()
    session2 = hooked_factory()
    
    # Each session should have its own _acquisition_state
    assert hasattr(session1, '_acquisition_state')
    assert hasattr(session2, '_acquisition_state')
    assert session1._acquisition_state is not session2._acquisition_state


def test_hooked_factory_validates_acquisition_query_run_id():
    """Hooked factory validates run ID in acquisition query params."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    # Mock execute to return a result
    mock_result = MagicMock()
    mock_session.execute.return_value = mock_result
    
    # Correct run ID should not raise
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
    """)
    session.execute(acquisition_query, {"id": 123})
    
    # Wrong run ID should raise
    with pytest.raises(AcquisitionHookError, match="run ID mismatch"):
        session.execute(acquisition_query, {"id": 456})


def test_hooked_factory_commit_clears_state_on_failure():
    """Commit failure clears only that session's pending state."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Make commit raise
    mock_session.commit.side_effect = RuntimeError("commit failed")
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    # Simulate acquisition query detection
    session._acquisition_state.acquisition_query_detected = True
    session._acquisition_state.acquisition_run_id = 123
    
    # Commit should raise and clear state
    with pytest.raises(RuntimeError, match="commit failed"):
        session.commit()
    
    # State should be cleared
    assert session._acquisition_state.acquisition_query_detected is False
    assert session._acquisition_state.acquisition_run_id is None


def test_hooked_factory_rollback_clears_session_state():
    """Rollback clears only that session's pending state."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    # Simulate acquisition query detection
    session._acquisition_state.acquisition_query_detected = True
    session._acquisition_state.acquisition_run_id = 123
    
    # Rollback should clear state
    session.rollback()
    
    assert session._acquisition_state.acquisition_query_detected is False
    assert session._acquisition_state.acquisition_run_id is None


def test_hooked_factory_verify_close_error_propagates():
    """Verify-session close error is not swallowed."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Make original commit succeed
    mock_session.commit.side_effect = None
    
    # Make verify session close fail
    verify_session = MagicMock()
    verify_session.close.side_effect = RuntimeError("close failed")
    verify_session.execute.return_value.fetchone.return_value = ("running",)
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session  # First call: main session
        else:
            return verify_session  # Subsequent calls: verify session
    
    mock_factory.side_effect = factory_side_effect
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    # Simulate acquisition query detection
    session._acquisition_state.acquisition_query_detected = True
    session._acquisition_state.acquisition_run_id = 123
    
    # Commit should raise VerifyCloseError
    with pytest.raises(VerifyCloseError, match="Failed to close verify session"):
        session.commit()


def test_hooked_factory_counts_signals_and_injections():
    """Session state tracks signal and injection counts."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Make original commit succeed
    mock_session.commit.side_effect = None
    
    # Make verify session return 'running'
    verify_session = MagicMock()
    verify_session.execute.return_value.fetchone.return_value = ("running",)
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    injected_exception = RuntimeError("injected")
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        injected_exception=injected_exception,
        inject_exception_after_cancel=True,
    )
    
    session = hooked_factory()
    
    # Simulate acquisition query detection
    session._acquisition_state.acquisition_query_detected = True
    session._acquisition_state.acquisition_run_id = 123
    
    # Commit should signal and inject
    with pytest.raises(RuntimeError, match="injected"):
        session.commit()
    
    # Counts should be incremented
    assert session._acquisition_state.signal_count == 1
    assert session._acquisition_state.injection_count == 1


# Test count: 20 tests covering complete no-DB behavior matrix
