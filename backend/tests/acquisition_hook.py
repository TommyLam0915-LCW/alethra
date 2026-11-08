"""Shared acquisition hook for concurrency tests.

Implements transparent session proxy with:
- Session-local pending state (per-session, not shared)
- Result proxy observing fetchone for acquisition verification
- RETURNING id validation: row[0] == target_run_id
- Pending set only when caller fetchone reads valid row
- Pending consumed after commit (success or failure), not left for next commit
- Old result cannot re-set pending after rollback/commit
- Shared totals (not session-local overwrites) for signal/injection counts
- Injected exception pre-built once by caller
- abort_event wired through factory, wait, and B
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
        self.acquisition_pending = False
        self.acquisition_run_id: Optional[int] = None
        self._result_expired = False  # Prevent old result from re-setting pending


class ResultProxy:
    """Transparent result proxy that observes fetchone for acquisition verification."""
    
    def __init__(self, result: Result, session_state: SessionLocalState, target_run_id: int, shared_results: Optional[Dict] = None):
        self._result = result
        self._session_state = session_state
        self._target_run_id = target_run_id
        self._shared_results = shared_results
    
    def fetchone(self):
        """Intercept fetchone to observe acquisition RETURNING id result."""
        # Fix 2: Old result cannot re-set pending after rollback/commit
        if self._session_state._result_expired:
            return self._result.fetchone()
        
        row = self._result.fetchone()
        
        # Acquisition query returns (id,) from RETURNING id
        # Only set pending when we read a valid row with matching run ID
        if row is not None and len(row) >= 1:
            returned_id = row[0]
            if returned_id == self._target_run_id:
                # Valid acquisition confirmed - set pending
                self._session_state.acquisition_pending = True
                self._session_state.acquisition_run_id = returned_id
                if self._shared_results is not None:
                    self._shared_results["acquisition_hook_hit"] = True
            else:
                raise AcquisitionHookError(
                    f"RETURNING id mismatch: got {returned_id}, expected {self._target_run_id}"
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
        'cancel_requested = FALSE' in query_str and
        'RETURNING id' in query_str
    )


def create_hooked_session_factory(
    base_factory: Callable[[], Session],
    target_run_id: int,
    injected_exception: Optional[Exception] = None,
    cancel_committed_event: Optional[threading.Event] = None,
    abort_event: Optional[threading.Event] = None,
    on_acquisition_committed: Optional[Callable[[], None]] = None,
    inject_exception_after_cancel: bool = True,
    shared_results: Optional[Dict] = None,
) -> Callable[[], Session]:
    """Create a session factory with acquisition detection hooks.
    
    Args:
        base_factory: The original session factory to wrap
        target_run_id: The run ID to verify after acquisition
        injected_exception: Pre-built exception to raise after cancel commits (Fix 4: once)
        cancel_committed_event: Event signaled when cancel thread commits
        abort_event: Event to signal abort (wired through factory, wait, and B)
        on_acquisition_committed: Callback after acquisition is verified durable
        inject_exception_after_cancel: Whether to raise after cancel commits
        shared_results: Shared dict for external tracking of signal/injection counts
    
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
            """Intercept execute to detect acquisition query."""
            query_str = str(query) if hasattr(query, '__str__') else ''
            
            result = original_execute(query, params, **kwargs)
            
            # Only wrap if this is the acquisition query
            if _is_acquisition_query(query_str, params):
                return ResultProxy(result, session_state, target_run_id, shared_results)
            
            return result
        
        def hooked_commit():
            """Intercept commit: verify acquisition only after same-session commit success."""
            try:
                original_commit()
            except Exception as commit_err:
                # Commit failure: clear pending and mark result expired
                session_state.acquisition_pending = False
                session_state.acquisition_run_id = None
                session_state._result_expired = True
                raise
            
            # After successful commit, verify acquisition on independent connection
            # Only if pending is set
            if session_state.acquisition_pending:
                verify_session = None
                verification_succeeded = False
                try:
                    verify_session = base_factory()
                    verify_result = verify_session.execute(
                        text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                        {"id": target_run_id}
                    ).fetchone()
                    
                    if verify_result and verify_result[0] == 'running':
                        verification_succeeded = True
                        
                        # Fix 3: Increment shared totals (not overwrite with session-local)
                        if shared_results is not None:
                            shared_results["signal_count"] = shared_results.get("signal_count", 0) + 1
                        
                        if on_acquisition_committed:
                            on_acquisition_committed()
                        
                        # Wait for cancel with abort support
                        if cancel_committed_event:
                            abort_aware_wait_or_raise(
                                cancel_committed_event,
                                abort_event=abort_event,
                                timeout=30.0,
                                error_context="waiting for cancel commit"
                            )
                        
                        # Inject exception to test post-acquisition error handler
                        # Fix 4: injected_exception is pre-built once by caller
                        if inject_exception_after_cancel and injected_exception is not None:
                            if shared_results is not None:
                                shared_results["injection_count"] = shared_results.get("injection_count", 0) + 1
                            raise injected_exception
                finally:
                    # Fix 1: Always consume pending after commit (success or failure)
                    session_state.acquisition_pending = False
                    session_state.acquisition_run_id = None
                    # Fix 2: Mark result expired to prevent re-setting pending
                    session_state._result_expired = True
                    
                    if verify_session:
                        # Verify-session close error must not be swallowed
                        try:
                            verify_session.close()
                        except Exception as close_err:
                            raise VerifyCloseError(
                                f"Failed to close verify session: {close_err}"
                            ) from close_err
            else:
                # No pending: mark result expired anyway
                session_state._result_expired = True
        
        def hooked_rollback():
            """Intercept rollback: clear pending and mark result expired."""
            session_state.acquisition_pending = False
            session_state.acquisition_run_id = None
            session_state._result_expired = True
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
    abort_event: Optional[threading.Event] = None,
    timeout: Optional[float] = None,
    poll_interval: float = 0.5,
    error_context: str = "",
) -> None:
    """Wait for an event with abort/timeout support. Raises on failure.
    
    Args:
        event: The event to wait for
        abort_event: Optional abort event
        timeout: Maximum total wait time (None = no timeout)
        poll_interval: How often to check for completion
        error_context: Context message for errors
    
    Raises:
        AbortError: If abort is signaled
        TimeoutError: If timeout expires before event is set
    """
    start_time = time.time()
    
    while not event.is_set():
        # Check abort
        if abort_event and abort_event.is_set():
            raise AbortError(f"Abort signaled {error_context}")
        
        if timeout is not None:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                raise TimeoutError(
                    f"Timeout after {elapsed:.2f}s {error_context}"
                )
        
        if event.wait(timeout=poll_interval):
            return
    
    return


def abort_aware_wait(
    event: threading.Event,
    abort: threading.Event,
    timeout: Optional[float] = None,
    poll_interval: float = 0.5,
) -> bool:
    """Wait for an event with abort support. Returns False on abort/timeout.
    
    DEPRECATED: Use abort_aware_wait_or_raise for new code.
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
