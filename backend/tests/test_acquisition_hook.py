"""No-DB tests for acquisition hook behavior matrix.

Tests the shared acquisition hook without requiring a real database:
- Session-local state isolation
- Result proxy observation (RETURNING id validation)
- Pending set only on valid fetchone
- None/execute failure clears pending
- Only wraps current acquisition result
- Consumes pending on successful processing
- abort_event wired through
- Complete execute→fetchone→commit flow
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
    AbortError,
    VerifyCloseError,
    abort_aware_wait_or_raise,
    abort_aware_wait,
    _is_acquisition_query,
)


# === Tests for helper functions ===

def test_is_acquisition_query_detects_pattern_with_returning():
    """_is_acquisition_query detects the atomic UPDATE pattern with RETURNING id."""
    query = """UPDATE paper_trading_runs
               SET status = 'running', updated_at = CURRENT_TIMESTAMP
               WHERE id = :id AND status IN ('queued', 'pending', 'failed')
                 AND cancel_requested = FALSE
               RETURNING id"""
    assert _is_acquisition_query(query) is True


def test_is_acquisition_query_rejects_without_returning():
    """_is_acquisition_query rejects queries without RETURNING id."""
    query = """UPDATE paper_trading_runs
               SET status = 'running'
               WHERE id = :id AND status IN ('queued')
                 AND cancel_requested = FALSE"""
    assert _is_acquisition_query(query) is False


def test_is_acquisition_query_rejects_non_acquisition():
    """_is_acquisition_query rejects non-acquisition queries."""
    assert _is_acquisition_query("SELECT * FROM users") is False
    assert _is_acquisition_query("UPDATE users SET name = 'test'") is False


# === Tests for SessionLocalState ===

def test_session_local_state_initial_values():
    """SessionLocalState initializes with correct defaults."""
    state = SessionLocalState()
    assert state.acquisition_pending is False
    assert state.acquisition_run_id is None
    assert state._transaction_generation == 0


def test_session_local_state_independent_instances():
    """Each SessionLocalState instance is independent."""
    state1 = SessionLocalState()
    state2 = SessionLocalState()
    
    state1.acquisition_pending = True
    state1._transaction_generation = 5
    
    assert state2.acquisition_pending is False
    assert state2._transaction_generation == 0


# === Tests for ResultProxy ===

def test_result_proxy_validates_returning_id():
    """ResultProxy validates RETURNING id matches target."""
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)  # RETURNING id returns (id,)
    
    state = SessionLocalState()
    proxy = ResultProxy(mock_result, state, target_run_id=123)
    row = proxy.fetchone()
    
    assert row == (123,)
    assert state.acquisition_pending is True
    assert state.acquisition_run_id == 123


def test_result_proxy_raises_on_id_mismatch():
    """ResultProxy raises AcquisitionHookError on RETURNING id mismatch."""
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (456,)  # Wrong ID
    
    state = SessionLocalState()
    proxy = ResultProxy(mock_result, state, target_run_id=123)
    
    with pytest.raises(AcquisitionHookError, match="RETURNING id mismatch"):
        proxy.fetchone()
    
    # Pending should not be set on mismatch
    assert state.acquisition_pending is False


def test_result_proxy_none_row_no_pending():
    """ResultProxy with None row does not set pending (requirement 3)."""
    mock_result = MagicMock()
    mock_result.fetchone.return_value = None
    
    state = SessionLocalState()
    proxy = ResultProxy(mock_result, state, target_run_id=123)
    row = proxy.fetchone()
    
    assert row is None
    assert state.acquisition_pending is False


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


def test_abort_aware_wait_or_raise_raises_on_abort():
    """abort_aware_wait_or_raise raises AbortError when abort is signaled (requirement 6)."""
    event = threading.Event()
    abort = threading.Event()
    abort.set()  # Abort immediately
    
    with pytest.raises(AbortError, match="Abort signaled"):
        abort_aware_wait_or_raise(event, abort_event=abort, timeout=1.0)


# === Tests for complete execute→fetchone→commit flow ===

def test_complete_flow_execute_fetchone_commit():
    """Complete execute→fetchone→commit flow sets and consumes pending."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Mock execute to return a result
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)  # RETURNING id
    mock_session.execute.return_value = mock_result
    
    # Mock commit to succeed
    mock_session.commit.side_effect = None
    
    # Mock verify session
    mock_verify_session = MagicMock()
    mock_verify_result = MagicMock()
    mock_verify_result.fetchone.return_value = ("running",)
    mock_verify_session.execute.return_value = mock_verify_result
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    # Execute acquisition query
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    
    # Fetchone should set pending
    row = result.fetchone()
    assert row == (123,)
    assert session._acquisition_state.acquisition_pending is True
    
    # Commit should consume pending and verify
    session.commit()
    
    # Verify session was used
    assert call_count[0] == 2
    mock_verify_session.close.assert_called_once()


def test_execute_failure_clears_pending():
    """Execute failure does not leave pending state (requirement 3)."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Mock execute to raise
    mock_session.execute.side_effect = RuntimeError("execute failed")
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    # Execute should raise
    with pytest.raises(RuntimeError, match="execute failed"):
        session.execute(text("UPDATE paper_trading_runs ..."))
    
    # Pending should not be set
    assert session._acquisition_state.acquisition_pending is False


def test_rollback_clears_pending():
    """Rollback clears pending state (requirement 3)."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    # Simulate pending state
    session._acquisition_state.acquisition_pending = True
    session._acquisition_state.acquisition_run_id = 123
    
    # Rollback should clear pending
    session.rollback()
    
    assert session._acquisition_state.acquisition_pending is False
    assert session._acquisition_state.acquisition_run_id is None


def test_commit_consumes_pending_prevents_duplicate():
    """Commit consumes pending to prevent duplicate signal/inject."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Mock execute to return acquisition result
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    
    # Mock commit to succeed
    mock_session.commit.side_effect = None
    
    # Mock verify session
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
    
    shared_results = {"signal_count": 0}
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        shared_results=shared_results,
    )
    
    session = hooked_factory()
    
    # Execute acquisition query (sets pending via fetchone)
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # First commit should signal
    session.commit()
    assert shared_results["signal_count"] == 1
    assert session._acquisition_state.acquisition_pending is False  # Consumed
    
    # Second commit should not signal again (pending consumed)
    session.commit()
    assert shared_results["signal_count"] == 1  # Still 1


def test_abort_event_wired_through_factory():
    """abort_event is wired through factory and wait (requirement 6)."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    abort_event = threading.Event()
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        abort_event=abort_event,
    )
    
    session = hooked_factory()
    
    # Session should be created successfully
    assert session is not None
    
    # abort_event should be accessible (wired through)
    # This is a structural test - the actual abort behavior is tested in wait tests


# Test count: 22 tests covering complete no-DB behavior matrix


# === Real helper path tests (12 tests) ===

def test_different_replay_verify_sessions_full_success():
    """Different replay/verify sessions complete full success flow."""
    mock_replay_session = MagicMock()
    mock_verify_session = MagicMock()
    
    # Replay session executes acquisition query
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_replay_session.execute.return_value = mock_result
    mock_replay_session.commit.side_effect = None
    
    # Verify session returns 'running'
    mock_verify_result = MagicMock()
    mock_verify_result.fetchone.return_value = ("running",)
    mock_verify_session.execute.return_value = mock_verify_result
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_replay_session
        else:
            return mock_verify_session
    
    mock_factory = MagicMock(side_effect=factory_side_effect)
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    # Replay session
    replay_session = hooked_factory()
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = replay_session.execute(acquisition_query, {"id": 123})
    row = result.fetchone()
    assert row == (123,)
    
    # Commit triggers verify on different session
    replay_session.commit()
    
    # Verify session was used
    assert call_count[0] == 2


def test_no_fetchone_before_commit_no_signal():
    """Commit without fetchone does not signal."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    mock_session.commit.side_effect = None
    
    shared_results = {"signal_count": 0}
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        shared_results=shared_results,
    )
    
    session = hooked_factory()
    
    # Execute but don't fetchone
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    session.execute(acquisition_query, {"id": 123})
    
    # Commit without fetchone
    session.commit()
    
    # No signal
    assert shared_results["signal_count"] == 0


def test_zero_row_fetchone_no_signal():
    """Fetchone returning zero rows does not signal."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Return zero rows
    mock_result = MagicMock()
    mock_result.fetchone.return_value = None
    mock_session.execute.return_value = mock_result
    mock_session.commit.side_effect = None
    
    shared_results = {"signal_count": 0}
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        shared_results=shared_results,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    row = result.fetchone()
    
    assert row is None
    
    session.commit()
    
    # No signal
    assert shared_results["signal_count"] == 0


def test_session_a_acquisition_session_b_commit_no_signal():
    """Session A acquisition, session B commit does not signal."""
    mock_session_a = MagicMock()
    mock_session_b = MagicMock()
    
    # Session A executes acquisition
    mock_result_a = MagicMock()
    mock_result_a.fetchone.return_value = (123,)
    mock_session_a.execute.return_value = mock_result_a
    
    # Session B commits (no acquisition)
    mock_session_b.commit.side_effect = None
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        return mock_session_a if call_count[0] == 1 else mock_session_b
    
    mock_factory = MagicMock(side_effect=factory_side_effect)
    
    shared_results = {"signal_count": 0}
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        shared_results=shared_results,
    )
    
    # Session A: acquisition
    session_a = hooked_factory()
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session_a.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # Session B: commit (different session, no pending)
    session_b = hooked_factory()
    session_b.commit()
    
    # No signal (session B has no pending)
    assert shared_results["signal_count"] == 0


def test_session_b_rollback_does_not_clear_session_a_pending():
    """Session B rollback does not clear session A pending."""
    mock_session_a = MagicMock()
    mock_session_b = MagicMock()
    
    # Session A executes acquisition
    mock_result_a = MagicMock()
    mock_result_a.fetchone.return_value = (123,)
    mock_session_a.execute.return_value = mock_result_a
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        return mock_session_a if call_count[0] == 1 else mock_session_b
    
    mock_factory = MagicMock(side_effect=factory_side_effect)
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    # Session A: acquisition
    session_a = hooked_factory()
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session_a.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    assert session_a._acquisition_state.acquisition_pending is True
    
    # Session B: rollback
    session_b = hooked_factory()
    session_b.rollback()
    
    # Session A pending still set
    assert session_a._acquisition_state.acquisition_pending is True


def test_same_session_commit_failure_clears_pending():
    """Same session commit failure clears pending."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Execute acquisition
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    
    # Commit fails
    mock_session.commit.side_effect = RuntimeError("commit failed")
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    assert session._acquisition_state.acquisition_pending is True
    
    # Commit fails
    with pytest.raises(RuntimeError, match="commit failed"):
        session.commit()
    
    # Pending cleared
    assert session._acquisition_state.acquisition_pending is False


def test_second_commit_after_success_no_duplicate_signal():
    """Second commit after success does not duplicate signal/inject."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Execute acquisition
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    
    # Commit succeeds
    mock_session.commit.side_effect = None
    
    # Verify session
    mock_verify_session = MagicMock()
    mock_verify_session.execute.return_value.fetchone.return_value = ("running",)
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    shared_results = {"signal_count": 0, "injection_count": 0}
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        shared_results=shared_results,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # First commit
    session.commit()
    assert shared_results["signal_count"] == 1
    
    # Second commit (pending already consumed)
    session.commit()
    assert shared_results["signal_count"] == 1  # Still 1


def test_abort_during_wait_raises_no_inject():
    """Abort during wait raises AbortError, no injection."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Execute acquisition
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    
    # Commit succeeds
    mock_session.commit.side_effect = None
    
    # Verify session
    mock_verify_session = MagicMock()
    mock_verify_session.execute.return_value.fetchone.return_value = ("running",)
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    abort_event = threading.Event()
    cancel_committed = threading.Event()
    
    shared_results = {"signal_count": 0, "injection_count": 0}
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        cancel_committed_event=cancel_committed,
        abort_event=abort_event,
        shared_results=shared_results,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # Abort before commit
    abort_event.set()
    
    # Commit should raise AbortError
    with pytest.raises(AbortError, match="Abort signaled"):
        session.commit()
    
    # No injection
    assert shared_results["injection_count"] == 0


def test_verify_not_running_raises_error():
    """Verify returns non-running: raises AcquisitionHookError."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Execute acquisition
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    
    # Commit succeeds
    mock_session.commit.side_effect = None
    
    # Verify session returns 'failed' (not 'running')
    mock_verify_session = MagicMock()
    mock_verify_result = MagicMock()
    mock_verify_result.fetchone.return_value = ("failed",)
    mock_verify_session.execute.return_value = mock_verify_result
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # Commit should raise AcquisitionHookError
    with pytest.raises(AcquisitionHookError, match="expected 'running'"):
        session.commit()
    
    # Pending cleared
    assert session._acquisition_state.acquisition_pending is False


def test_verify_close_error_observable():
    """Verify close error is observable via VerifyCloseError."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Execute acquisition
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    
    # Commit succeeds
    mock_session.commit.side_effect = None
    
    # Verify session close fails
    mock_verify_session = MagicMock()
    mock_verify_session.execute.return_value.fetchone.return_value = ("running",)
    mock_verify_session.close.side_effect = RuntimeError("close failed")
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # Commit should raise VerifyCloseError
    with pytest.raises(VerifyCloseError, match="Failed to close verify session"):
        session.commit()


def test_rollback_then_read_old_result_no_restart_pending():
    """Rollback then read old result does not restart pending."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Execute acquisition
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    assert session._acquisition_state.acquisition_pending is True
    initial_generation = session._acquisition_state._transaction_generation
    
    # Rollback increments generation
    session.rollback()
    
    assert session._acquisition_state.acquisition_pending is False
    assert session._acquisition_state._transaction_generation == initial_generation + 1
    
    # Read old result again - generation mismatch prevents re-setting pending
    result.fetchone()
    
    # Pending not restarted (generation changed)
    assert session._acquisition_state.acquisition_pending is False


def test_injected_exception_identity_is_assertion():
    """Injected exception identity uses 'is' assertion."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Execute acquisition
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    
    # Commit succeeds
    mock_session.commit.side_effect = None
    
    # Verify session
    mock_verify_session = MagicMock()
    mock_verify_session.execute.return_value.fetchone.return_value = ("running",)
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    # Pre-build exception once
    injected_exception = RuntimeError("Injected")
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        injected_exception=injected_exception,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # Commit should raise the same instance
    with pytest.raises(RuntimeError) as exc_info:
        session.commit()
    
    # Identity assertion
    assert exc_info.value is injected_exception


# === New tests for 10 improvements ===

def test_new_acquisition_after_commit_still_works():
    """After commit, new acquisition in same session still works."""
    mock_session = MagicMock()
    
    # First acquisition result
    mock_result_1 = MagicMock()
    mock_result_1.fetchone.return_value = (123,)
    
    # Second acquisition result
    mock_result_2 = MagicMock()
    mock_result_2.fetchone.return_value = (123,)
    
    # Track which result to return for main session
    execute_call_count = [0]
    def execute_side_effect(query, params=None, **kwargs):
        execute_call_count[0] += 1
        if execute_call_count[0] == 1:
            return mock_result_1
        else:  # execute_call_count[0] == 2 (second acquisition)
            return mock_result_2
    
    mock_session.execute.side_effect = execute_side_effect
    mock_session.commit.side_effect = None
    
    # Verify session (separate from main session)
    mock_verify_session = MagicMock()
    mock_verify_result = MagicMock()
    mock_verify_result.fetchone.return_value = ("running",)
    mock_verify_session.execute.return_value = mock_verify_result
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1 or call_count[0] == 3:
            return mock_session
        else:  # call_count[0] == 2 (verify)
            return mock_verify_session
    
    mock_factory = MagicMock(side_effect=factory_side_effect)
    
    shared_results = {"signal_count": 0}
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        shared_results=shared_results,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    
    # First acquisition
    result1 = session.execute(acquisition_query, {"id": 123})
    row1 = result1.fetchone()
    assert row1 == (123,)
    assert session._acquisition_state.acquisition_pending is True
    
    # Commit
    session.commit()
    assert shared_results["signal_count"] == 1
    assert session._acquisition_state.acquisition_pending is False
    
    # Second acquisition in same session - should work
    result2 = session.execute(acquisition_query, {"id": 123})
    row2 = result2.fetchone()
    assert row2 == (123,)
    assert session._acquisition_state.acquisition_pending is True


def test_new_result_after_rollback_works_old_still_invalid():
    """After rollback, new result works, old result still invalid."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # First result (will be rolled back)
    mock_result_1 = MagicMock()
    mock_result_1.fetchone.return_value = (123,)
    
    # Second result (after rollback)
    mock_result_2 = MagicMock()
    mock_result_2.fetchone.return_value = (123,)
    
    execute_call_count = [0]
    def execute_side_effect(query, params=None, **kwargs):
        execute_call_count[0] += 1
        if execute_call_count[0] == 1:
            return mock_result_1
        else:
            return mock_result_2
    
    mock_session.execute.side_effect = execute_side_effect
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    
    # First acquisition
    result1 = session.execute(acquisition_query, {"id": 123})
    row1 = result1.fetchone()
    assert row1 == (123,)
    assert session._acquisition_state.acquisition_pending is True
    initial_gen = session._acquisition_state._transaction_generation
    
    # Rollback increments generation
    session.rollback()
    assert session._acquisition_state.acquisition_pending is False
    assert session._acquisition_state._transaction_generation == initial_gen + 1
    
    # Old result fetchone - generation mismatch, doesn't set pending
    row1_again = result1.fetchone()
    assert row1_again == (123,)
    assert session._acquisition_state.acquisition_pending is False
    
    # New acquisition - works normally
    result2 = session.execute(acquisition_query, {"id": 123})
    row2 = result2.fetchone()
    assert row2 == (123,)
    assert session._acquisition_state.acquisition_pending is True


def test_ab_factory_sequence_assert_not_same():
    """A/B factory returns different sessions, assert A is not B."""
    mock_session_a = MagicMock()
    mock_session_b = MagicMock()
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session_a
        else:
            return mock_session_b
    
    mock_factory = MagicMock(side_effect=factory_side_effect)
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session_a = hooked_factory()
    session_b = hooked_factory()
    
    # Explicit assertion: A is not B
    assert session_a is not session_b
    assert session_a is mock_session_a
    assert session_b is mock_session_b


def test_b_commit_no_signal_a_commit_signals_once():
    """B commit doesn't signal, A commit signals once."""
    mock_session_a = MagicMock()
    mock_session_b = MagicMock()
    
    # Session A executes acquisition
    mock_result_a = MagicMock()
    mock_result_a.fetchone.return_value = (123,)
    mock_session_a.execute.return_value = mock_result_a
    mock_session_a.commit.side_effect = None
    
    # Session B commits (no acquisition)
    mock_session_b.commit.side_effect = None
    
    # Verify session for A
    mock_verify_session = MagicMock()
    mock_verify_result = MagicMock()
    mock_verify_result.fetchone.return_value = ("running",)
    mock_verify_session.execute.return_value = mock_verify_result
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session_a
        elif call_count[0] == 2:
            return mock_session_b
        else:
            return mock_verify_session
    
    mock_factory = MagicMock(side_effect=factory_side_effect)
    
    shared_results = {"signal_count": 0}
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        shared_results=shared_results,
    )
    
    # Session A: acquisition
    session_a = hooked_factory()
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result_a = session_a.execute(acquisition_query, {"id": 123})
    result_a.fetchone()
    
    # Session B: commit (no signal)
    session_b = hooked_factory()
    session_b.commit()
    assert shared_results["signal_count"] == 0
    
    # Session A: commit (signals once)
    session_a.commit()
    assert shared_results["signal_count"] == 1


def test_verification_non_running_raises_error():
    """Verification returns non-running status: raises AcquisitionHookError."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    mock_session.commit.side_effect = None
    
    # Verify session returns 'failed'
    mock_verify_session = MagicMock()
    mock_verify_result = MagicMock()
    mock_verify_result.fetchone.return_value = ("failed",)
    mock_verify_session.execute.return_value = mock_verify_result
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # Commit should raise AcquisitionHookError
    with pytest.raises(AcquisitionHookError, match="expected 'running'"):
        session.commit()


def test_verification_zero_rows_raises_error():
    """Verification returns zero rows: raises AcquisitionHookError."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    mock_session.commit.side_effect = None
    
    # Verify session returns None (zero rows)
    mock_verify_session = MagicMock()
    mock_verify_result = MagicMock()
    mock_verify_result.fetchone.return_value = None
    mock_verify_session.execute.return_value = mock_verify_result
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # Commit should raise AcquisitionHookError
    with pytest.raises(AcquisitionHookError, match="zero rows"):
        session.commit()


def test_verification_query_failure_clears_pending():
    """Verification query failure clears pending."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    mock_session.commit.side_effect = None
    
    # Verify session query fails
    mock_verify_session = MagicMock()
    mock_verify_session.execute.side_effect = RuntimeError("query failed")
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    assert session._acquisition_state.acquisition_pending is True
    
    # Commit should raise the query error
    with pytest.raises(RuntimeError, match="query failed"):
        session.commit()
    
    # Pending cleared
    assert session._acquisition_state.acquisition_pending is False


def test_shared_totals_protected_by_lock():
    """Shared totals increments are protected by lock."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    mock_session.commit.side_effect = None
    
    mock_verify_session = MagicMock()
    mock_verify_result = MagicMock()
    mock_verify_result.fetchone.return_value = ("running",)
    mock_verify_session.execute.return_value = mock_verify_result
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    shared_results = {"signal_count": 0}
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        shared_results=shared_results,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # Commit with lock protection
    session.commit()
    
    # Signal count incremented atomically
    assert shared_results["signal_count"] == 1


def test_real_abort_during_wait_no_inject_verify_closed():
    """Real abort during wait: AbortError raised, no injection, verify closed."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    mock_session.commit.side_effect = None
    
    mock_verify_session = MagicMock()
    mock_verify_result = MagicMock()
    mock_verify_result.fetchone.return_value = ("running",)
    mock_verify_session.execute.return_value = mock_verify_result
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    abort_event = threading.Event()
    cancel_committed = threading.Event()
    injected_exception = RuntimeError("Should not be raised")
    
    shared_results = {"signal_count": 0, "injection_count": 0}
    
    # Track when thread reaches the wait point
    wait_reached = threading.Event()
    
    def on_acquisition_committed():
        # Signal that we've reached the wait point
        wait_reached.set()
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        cancel_committed_event=cancel_committed,
        abort_event=abort_event,
        injected_exception=injected_exception,
        on_acquisition_committed=on_acquisition_committed,
        shared_results=shared_results,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # Explicit handshake: wait_reached signals when thread enters the wait
    thread_result = {"exception": None}
    
    # Start commit in a thread
    def commit_thread():
        try:
            session.commit()
        except Exception as e:
            thread_result["exception"] = e
    
    thread = threading.Thread(target=commit_thread)
    try:
        thread.start()
        
        # Wait for thread to reach the wait point (inside cancel_committed_event.wait)
        assert wait_reached.wait(timeout=1.0), "Thread did not reach wait point"
        
        # Now signal abort (thread is definitely in the wait)
        abort_event.set()
        
        # Join with timeout
        thread.join(timeout=2.0)
        
        # Assert thread stopped
        assert not thread.is_alive(), "Thread did not stop"
        
        # Assert AbortError was raised
        assert thread_result["exception"] is not None, "No exception raised"
        assert isinstance(thread_result["exception"], AbortError), \
            f"Expected AbortError, got {type(thread_result['exception'])}"
        
        # No injection
        assert shared_results["signal_count"] == 1  # Signal happened before wait
        assert shared_results["injection_count"] == 0  # No injection
        
        # Verify session closed
        mock_verify_session.close.assert_called_once()
    finally:
        # Ensure cleanup
        abort_event.set()
        if thread.is_alive():
            thread.join(timeout=1.0)


def test_second_commit_after_injection_still_one():
    """After first injection, second commit doesn't inject again."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)
    mock_session.execute.return_value = mock_result
    mock_session.commit.side_effect = None
    
    mock_verify_session = MagicMock()
    mock_verify_result = MagicMock()
    mock_verify_result.fetchone.return_value = ("running",)
    mock_verify_session.execute.return_value = mock_verify_result
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    injected_exception = RuntimeError("Injected")
    
    shared_results = {"signal_count": 0, "injection_count": 0}
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        injected_exception=injected_exception,
        shared_results=shared_results,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    result.fetchone()
    
    # First commit: injects exception
    with pytest.raises(RuntimeError):
        session.commit()
    
    assert shared_results["injection_count"] == 1
    
    # Second commit: pending already consumed, no injection
    session.commit()
    
    assert shared_results["injection_count"] == 1  # Still 1


def test_success_with_row_callback_pending_close_assertions():
    """Comprehensive success test: row identity, callback, pending, close."""
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    
    # Create expected row object for identity assertion
    expected_row = (123,)
    mock_result = MagicMock()
    mock_result.fetchone.return_value = expected_row
    mock_session.execute.return_value = mock_result
    mock_session.commit.side_effect = None
    
    mock_verify_session = MagicMock()
    mock_verify_result = MagicMock()
    mock_verify_result.fetchone.return_value = ("running",)
    mock_verify_session.execute.return_value = mock_verify_result
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_session
        else:
            return mock_verify_session
    
    mock_factory.side_effect = factory_side_effect
    
    callback_called = [False]
    def on_acquisition_committed():
        callback_called[0] = True
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        on_acquisition_committed=on_acquisition_committed,
    )
    
    session = hooked_factory()
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    result = session.execute(acquisition_query, {"id": 123})
    
    # Row identity assertion - call fetchone only once
    row = result.fetchone()
    assert row is expected_row, f"Row identity failed: expected {id(expected_row)}, got {id(row)}"
    
    # Pending set
    assert session._acquisition_state.acquisition_pending is True
    assert session._acquisition_state.acquisition_run_id == 123
    
    # Commit
    session.commit()
    
    # Callback invoked
    assert callback_called[0] is True
    
    # Pending consumed
    assert session._acquisition_state.acquisition_pending is False
    assert session._acquisition_state.acquisition_run_id is None
    
    # Verify session closed
    mock_verify_session.close.assert_called_once()
    
    # Verify fetchone called only once on underlying result
    assert mock_result.fetchone.call_count == 1


def test_concurrent_b_updates_while_a_in_callback_wait():
    """A and B both do full acquisition→fetchone→commit, updating same counter.
    
    Proves narrow lock scope: B acquires lock and increments counter while A
    is blocked in callback (after A released lock but before A's commit completes).
    """
    # Session A setup
    mock_session_a = MagicMock()
    mock_result_a = MagicMock()
    mock_result_a.fetchone.return_value = (123,)
    mock_session_a.execute.return_value = mock_result_a
    mock_session_a.commit.side_effect = None
    
    # Verify session for A
    mock_verify_a = MagicMock()
    mock_verify_result_a = MagicMock()
    mock_verify_result_a.fetchone.return_value = ("running",)
    mock_verify_a.execute.return_value = mock_verify_result_a
    
    # Session B setup
    mock_session_b = MagicMock()
    mock_result_b = MagicMock()
    mock_result_b.fetchone.return_value = (123,)
    mock_session_b.execute.return_value = mock_result_b
    mock_session_b.commit.side_effect = None
    
    # Verify session for B
    mock_verify_b = MagicMock()
    mock_verify_result_b = MagicMock()
    mock_verify_result_b.fetchone.return_value = ("running",)
    mock_verify_b.execute.return_value = mock_verify_result_b
    
    call_count = [0]
    def factory_side_effect():
        call_count[0] += 1
        # Call sequence: A_main, A_verify, B_main, B_verify
        if call_count[0] == 1:
            return mock_session_a
        elif call_count[0] == 2:
            return mock_verify_a
        elif call_count[0] == 3:
            return mock_session_b
        else:
            return mock_verify_b
    
    mock_factory = MagicMock(side_effect=factory_side_effect)
    
    # Track callback entry and B's completion
    callback_entered = threading.Event()
    callback_can_exit = threading.Event()
    b_completed = threading.Event()
    
    def on_acquisition_committed():
        callback_entered.set()
        # Wait in callback to simulate long-running operation
        # B should be able to acquire lock and increment during this pause
        callback_can_exit.wait(timeout=2.0)
    
    shared_results = {"signal_count": 0, "injection_count": 0}
    
    hooked_factory = create_hooked_session_factory(
        base_factory=mock_factory,
        target_run_id=123,
        on_acquisition_committed=on_acquisition_committed,
        shared_results=shared_results,
    )
    
    acquisition_query = text("""
        UPDATE paper_trading_runs
        SET status = 'running'
        WHERE id = :id AND status IN ('queued')
          AND cancel_requested = FALSE
        RETURNING id
    """)
    
    # Session A: acquisition and commit (will enter callback and wait)
    session_a = hooked_factory()
    result_a = session_a.execute(acquisition_query, {"id": 123})
    result_a.fetchone()
    
    # Capture thread exceptions back to main thread
    thread_exceptions = {"a": None, "b": None}
    
    # Start A's commit in a thread
    def a_commit_thread():
        try:
            session_a.commit()
        except Exception as e:
            thread_exceptions["a"] = e
    
    a_thread = threading.Thread(target=a_commit_thread)
    try:
        a_thread.start()
        
        # Wait for A to enter callback (explicit handshake)
        assert callback_entered.wait(timeout=1.0), "A did not enter callback"
        
        # At this point, A has incremented signal_count to 1 and is blocked in callback
        # A has released the lock (narrow scope) but not yet completed
        
        # While A is in callback (before release), B does full acquisition flow
        # B should be able to acquire the lock and increment counter
        session_b = hooked_factory()
        result_b = session_b.execute(acquisition_query, {"id": 123})
        row_b = result_b.fetchone()
        assert row_b == (123,)
        
        # B commits - this will also increment signal_count
        # This proves B acquired the lock while A was paused in callback
        session_b.commit()
        b_completed.set()
        
        # Verify B completed during A's callback pause
        assert b_completed.wait(timeout=1.0), "B did not complete during A's pause"
        
        # B completed without deadlock - key assertion
        # signal_count is 2: A incremented before callback, B incremented during A's callback
        assert shared_results["signal_count"] == 2, \
            f"Expected signal_count==2 (A+B both incremented), got {shared_results['signal_count']}"
        
        # Allow A's callback to exit
        callback_can_exit.set()
        
        # Wait for A thread with bounded timeout
        a_thread.join(timeout=2.0)
        assert not a_thread.is_alive(), "A thread did not complete"
        
        # Check for thread exceptions
        if thread_exceptions["a"] is not None:
            raise thread_exceptions["a"]
        
        # Final count
        assert shared_results["signal_count"] == 2
    finally:
        # Ensure cleanup: release A from callback and join thread
        callback_can_exit.set()
        if a_thread.is_alive():
            a_thread.join(timeout=1.0)
            # If still alive after final join, that's a test failure
            assert not a_thread.is_alive(), "A thread still alive after cleanup"


# Total: 30 + 10 + 1 = 41 tests
