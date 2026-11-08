"""Shared acquisition hook for concurrency tests.

Extracts the session-hooking pattern from test_paper_trading_cancel_atomic.py
into a reusable helper that:
- Detects the atomic acquisition UPDATE
- Verifies durability on an independent connection before signaling
- Supports abort-aware waits
- Clears pending state on rollback/commit failure
- Returns a transparent session proxy
"""
import threading
from typing import Callable, Optional, Dict, Any
from sqlalchemy import text
from sqlalchemy.orm import Session


class AcquisitionHook:
    """Tracks acquisition query detection and commit verification."""
    
    def __init__(self):
        self.acquisition_query_detected = False
        self.acquisition_committed = threading.Event()
        self.can_inject_exception = threading.Event()
        self.abort = threading.Event()
        self.results: Dict[str, Any] = {}
    
    def reset(self):
        """Reset state for reuse."""
        self.acquisition_query_detected = False
        self.acquisition_committed.clear()
        self.can_inject_exception.clear()
        self.abort.clear()
        self.results.clear()


def _is_acquisition_query(query_str: str) -> bool:
    """Detect the atomic acquisition UPDATE pattern."""
    return (
        'UPDATE paper_trading_runs' in query_str and
        'SET status = \'running\'' in query_str and
        'WHERE id = :id AND status IN' in query_str and
        'cancel_requested = FALSE' in query_str
    )


def create_hooked_session_factory(
    base_factory: Callable[[], Session],
    hook: AcquisitionHook,
    paper_run_id: int,
    on_acquisition_committed: Optional[Callable[[], None]] = None,
    inject_exception_after_cancel: bool = True,
    cancel_committed_event: Optional[threading.Event] = None,
) -> Callable[[], Session]:
    """Create a session factory that hooks into acquisition detection.
    
    Args:
        base_factory: The original session factory to wrap
        hook: AcquisitionHook instance for tracking state
        paper_run_id: The run ID to verify after acquisition
        on_acquisition_committed: Callback after acquisition is verified durable
        inject_exception_after_cancel: Whether to raise after cancel commits
        cancel_committed_event: Event signaled when cancel thread commits
    
    Returns:
        A factory function that returns hooked sessions
    """
    def hooked_session_factory() -> Session:
        """Returns a session with acquisition detection hooks."""
        session = base_factory()
        original_execute = session.execute
        original_commit = session.commit
        original_rollback = session.rollback
        
        def hooked_execute(query, params=None, **kwargs):
            """Intercept execute to detect acquisition query."""
            query_str = str(query) if hasattr(query, '__str__') else ''
            
            if not hook.acquisition_query_detected and _is_acquisition_query(query_str):
                hook.acquisition_query_detected = True
                hook.results["acquisition_hook_hit"] = True
            
            return original_execute(query, params, **kwargs)
        
        def hooked_commit():
            """Intercept commit to verify acquisition durability."""
            try:
                original_commit()
            except Exception as commit_err:
                # Commit failure: clear any pending acquisition state
                hook.acquisition_query_detected = False
                hook.results["commit_failed"] = str(commit_err)
                raise
            
            # After successful commit, verify acquisition on independent connection
            if hook.acquisition_query_detected and not hook.acquisition_committed.is_set():
                verify_session = None
                try:
                    verify_session = base_factory()
                    verify_result = verify_session.execute(
                        text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                        {"id": paper_run_id}
                    ).fetchone()
                    
                    if verify_result and verify_result[0] == 'running':
                        # Acquisition verified durable
                        hook.acquisition_committed.set()
                        
                        if on_acquisition_committed:
                            on_acquisition_committed()
                        
                        # Wait for cancel with abort support
                        if cancel_committed_event:
                            while not cancel_committed_event.wait(timeout=0.5):
                                if hook.abort.is_set():
                                    hook.results["aborted_during_wait"] = True
                                    return
                        
                        if hook.abort.is_set():
                            hook.results["aborted_before_injection"] = True
                            return
                        
                        # Inject exception to test post-acquisition error handler
                        if inject_exception_after_cancel:
                            hook.can_inject_exception.set()
                            injected_error = RuntimeError("Injected after cancel committed")
                            hook.results["injected_exception"] = injected_error
                            raise injected_error
                finally:
                    if verify_session:
                        try:
                            verify_session.close()
                        except Exception:
                            pass
        
        def hooked_rollback():
            """Intercept rollback to clear pending state."""
            # Rollback: clear any pending acquisition state
            hook.acquisition_query_detected = False
            hook.results["rollback_called"] = True
            return original_rollback()
        
        # Replace methods on session instance
        session.execute = hooked_execute
        session.commit = hooked_commit
        session.rollback = hooked_rollback
        
        return session
    
    return hooked_session_factory


def abort_aware_wait(
    event: threading.Event,
    abort: threading.Event,
    timeout: Optional[float] = None,
    poll_interval: float = 0.5,
) -> bool:
    """Wait for an event with abort support.
    
    Args:
        event: The event to wait for
        abort: The abort event that cancels the wait
        timeout: Maximum total wait time (None = no timeout)
        poll_interval: How often to check abort flag
    
    Returns:
        True if event was set, False if aborted or timed out
    """
    import time
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


class SessionProxy:
    """Transparent proxy that delegates to wrapped session.
    
    Allows intercepting specific methods while passing through all others.
    """
    
    def __init__(self, session: Session, interceptors: Optional[Dict[str, Callable]] = None):
        self._session = session
        self._interceptors = interceptors or {}
    
    def __getattr__(self, name: str):
        if name in self._interceptors:
            return self._interceptors[name]
        return getattr(self._session, name)
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self._session.close()
