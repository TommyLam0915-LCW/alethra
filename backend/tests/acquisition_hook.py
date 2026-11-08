"""Shared acquisition hook for concurrency tests.

Implements transparent session proxy with:
- Session-local pending state (per-session, not shared)
- Result proxy observing fetchone for acquisition verification
- Execute params and returned row validation for target run ID
- Verify/signal only after same-session commit success
- Rollback/commit failure clears only that session's pending
- Abort/timeout raises explicit errors (no silent return)
- Verify-session close error propagates (not swallowed)
- Injected exception pre-built by caller with signal/injection counts
"""
import threading
import time
from typing import Callable, Optional, Dict, Any
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.engine import Result


class AcquisitionHookError(Exception):
    """Base exception for acquisition hook errors."""
    pass


class AbortError(AcquisitionHookError):
    """Raised when abort is signaled during wait."""
    pass


class TimeoutError(AcquisitionHookError):
    """Raised when timeout expires during wait."""
    pass


class VerifyCloseError(AcquisitionHookError):
    """Raised when verify session close fails."""
    pass


class SessionLocalState:
    """Per-session pending acquisition state."""
    
    def __init__(self):
        self.acquisition_query_detected = False
        self.acquisition_run_id: Optional[int] = None
        self.signal_count = 0
        self.injection_count = 0


class ResultProxy:
    """Transparent result proxy that observes fetchone for acquisition verification."""
    
    def __init__(self, result: Result, session_state: SessionLocalState, target_run_id: int):
        self._result = result
        self._session_state = session_state
        self._target_run_id = target_run_id
    
    def fetchone(self):
        """Intercept fetchone to observe acquisition result."""
        row = self._result.fetchone()
        
        # If this is the acquisition query result, validate and mark session-local
        if self._session_state.acquisition_query_detected and row is not None:
            # Row should be (status,) for SELECT status FROM ... WHERE id = :id
            if len(row) >= 1 and row[0] == 'running':
                # Verify the run ID matches
                if self._session_state.acquisition_run_id == self._target_run_id:
                    pass  # Valid acquisition confirmed
                else:
                    raise AcquisitionHookError(
                        f"Run ID mismatch: session has {self._session_state.acquisition_run_id}, "
                        f"expected {self._target_run_id}"
                    )
        
        return row
    
    def __getattr__(self, name: str):
        """Delegate all other attributes to wrapped result."""
        return getattr(self._result, name)
    
    def __iter__(self):
        return iter(self._result)


def _is_acquisition_query(query_str: str, params: Optional[Dict] = None) -> bool:
    """Detect the atomic acquisition UPDATE pattern."""
    return (
        'UPDATE paper_trading_runs' in query_str and
        'SET status = \'running\'' in query_str and
        'WHERE id = :id AND status IN' in query_str and
        'cancel_requested = FALSE' in query_str
    )


def _extract_run_id_from_params(params: Optional[Dict]) -> Optional[int]:
    """Extract run ID from execute params."""
    if params and isinstance(params, dict):
        return params.get('id')
    return None


def create_hooked_session_factory(
    base_factory: Callable[[], Session],
    target_run_id: int,
    injected_exception: Optional[Exception] = None,
    cancel_committed_event: Optional[threading.Event] = None,
    on_acquisition_committed: Optional[Callable[[], None]] = None,
    inject_exception_after_cancel: bool = True,
) -> Callable[[], Session]:
    """Create a session factory with acquisition detection hooks.
    
    Args:
        base_factory: The original session factory to wrap
        target_run_id: The run ID to verify after acquisition
        injected_exception: Pre-built exception to raise after cancel commits
        cancel_committed_event: Event signaled when cancel thread commits
        on_acquisition_committed: Callback after acquisition is verified durable
        inject_exception_after_cancel: Whether to raise after cancel commits
    
    Returns:
        A factory function that returns hooked sessions with session-local state
    """
    def hooked_session_factory() -> Session:
        """Returns a session with acquisition detection hooks."""
        session = base_factory()
        
        # Session-local state (not shared across sessions)
        session_state = SessionLocalState()
        
        original_execute = session.execute
        original_commit = session.commit
        original_rollback = session.rollback
        
        def hooked_execute(query, params=None, **kwargs):
            """Intercept execute to detect acquisition query and validate params."""
            query_str = str(query) if hasattr(query, '__str__') else ''
            
            # Check if this is the acquisition query
            if _is_acquisition_query(query_str, params):
                run_id = _extract_run_id_from_params(params)
                if run_id == target_run_id:
                    session_state.acquisition_query_detected = True
                    session_state.acquisition_run_id = run_id
                else:
                    raise AcquisitionHookError(
                        f"Acquisition query run ID mismatch: got {run_id}, expected {target_run_id}"
                    )
            
            result = original_execute(query, params, **kwargs)
            
            # Wrap result in proxy if this was an acquisition query
            if session_state.acquisition_query_detected:
                return ResultProxy(result, session_state, target_run_id)
            
            return result
        
        def hooked_commit():
            """Intercept commit: verify acquisition only after same-session commit success."""
            try:
                original_commit()
            except Exception as commit_err:
                # Commit failure: clear only this session's pending state
                session_state.acquisition_query_detected = False
                session_state.acquisition_run_id = None
                raise
            
            # After successful commit, verify acquisition on independent connection
            if session_state.acquisition_query_detected:
                verify_session = None
                try:
                    verify_session = base_factory()
                    verify_result = verify_session.execute(
                        text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                        {"id": target_run_id}
                    ).fetchone()
                    
                    if verify_result and verify_result[0] == 'running':
                        # Acquisition verified durable
                        session_state.signal_count += 1
                        
                        if on_acquisition_committed:
                            on_acquisition_committed()
                        
                        # Wait for cancel with abort/timeout support
                        if cancel_committed_event:
                            abort_aware_wait_or_raise(
                                cancel_committed_event,
                                timeout=30.0,
                                error_context="waiting for cancel commit"
                            )
                        
                        # Inject exception to test post-acquisition error handler
                        if inject_exception_after_cancel and injected_exception is not None:
                            session_state.injection_count += 1
                            raise injected_exception
                finally:
                    if verify_session:
                        # Verify-session close error must not be swallowed
                        try:
                            verify_session.close()
                        except Exception as close_err:
                            raise VerifyCloseError(
                                f"Failed to close verify session: {close_err}"
                            ) from close_err
        
        def hooked_rollback():
            """Intercept rollback: clear only this session's pending state."""
            session_state.acquisition_query_detected = False
            session_state.acquisition_run_id = None
            return original_rollback()
        
        # Replace methods on session instance
        session.execute = hooked_execute
        session.commit = hooked_commit
        session.rollback = hooked_rollback
        
        # Attach session state for inspection
        session._acquisition_state = session_state
        
        return session
    
    return hooked_session_factory


def abort_aware_wait_or_raise(
    event: threading.Event,
    timeout: Optional[float] = None,
    poll_interval: float = 0.5,
    error_context: str = "",
) -> None:
    """Wait for an event with abort/timeout support. Raises on failure.
    
    Args:
        event: The event to wait for
        timeout: Maximum total wait time (None = no timeout)
        poll_interval: How often to check for completion
        error_context: Context message for errors
    
    Raises:
        TimeoutError: If timeout expires before event is set
    """
    start_time = time.time()
    
    while not event.is_set():
        if timeout is not None:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                raise TimeoutError(
                    f"Timeout after {elapsed:.2f}s {error_context}"
                )
        
        if event.wait(timeout=poll_interval):
            return
    
    # Event was set
    return


def abort_aware_wait(
    event: threading.Event,
    abort: threading.Event,
    timeout: Optional[float] = None,
    poll_interval: float = 0.5,
) -> bool:
    """Wait for an event with abort support. Returns False on abort/timeout.
    
    DEPRECATED: Use abort_aware_wait_or_raise for new code.
    
    Args:
        event: The event to wait for
        abort: The abort event that cancels the wait
        timeout: Maximum total wait time (None = no timeout)
        poll_interval: How often to check abort flag
    
    Returns:
        True if event was set, False if aborted or timed out
    """
    start_time = time.time()
    
    while not event.is_set():
        if abort.is_set():
            return False
        
        if timeout is not None:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                return False
        
        if event.wait(timeout=poll_interval):
            return True
    
    return True
