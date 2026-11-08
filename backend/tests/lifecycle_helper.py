"""Worker lifecycle helper for no-DB testing.

Provides minimal lifecycle boundary for managing worker threads:
- Track started workers
- Execute abort/release callbacks
- Bounded join all workers
- Verify all stopped before cleanup
- Preserve all errors (main, stop, cleanup)
- Don't repeat cleanup after success

This helper is used by B integration tests and finalizers to ensure
safe worker lifecycle management without requiring database.
"""

import threading
from typing import Callable, List, Optional, Dict, Any


class WorkerLifecycleError(Exception):
    """Base exception for worker lifecycle errors."""
    pass


class WorkerNotStoppedError(WorkerLifecycleError):
    """Raised when worker(s) not confirmed stopped before cleanup."""
    pass


class CleanupRefusedError(WorkerLifecycleError):
    """Raised when cleanup refused due to worker state."""
    pass


class WorkerLifecycle:
    """Manages worker thread lifecycle with safety guarantees.
    
    Responsibilities:
    - Track started workers (thread handles + abort events)
    - Execute abort/release callbacks for all workers
    - Bounded join all workers
    - Verify all stopped (is_alive check) before cleanup
    - Refuse cleanup if any worker not stopped
    - Preserve main error, stop error, cleanup error
    - Don't repeat cleanup after success
    
    Usage:
        lifecycle = WorkerLifecycle()
        
        # Start workers
        thread1, abort1 = lifecycle.start_worker(target1, name="worker1")
        thread2, abort2 = lifecycle.start_worker(target2, name="worker2")
        
        try:
            # Main flow
            do_work()
        finally:
            # Stop all workers
            lifecycle.abort_all()
            lifecycle.join_all(timeout=2.0)
            
            # Verify all stopped before cleanup
            lifecycle.verify_all_stopped()
            
            # Execute cleanup (only if all stopped)
            lifecycle.run_cleanup(cleanup_callback)
    """
    
    def __init__(self):
        """Initialize lifecycle manager."""
        self.workers: List[Dict[str, Any]] = []  # [{thread, abort_event, name}]
        self.cleanup_callback: Optional[Callable] = None
        self.cleanup_called_count = 0
        self.cleanup_call_order: List[str] = []  # Track order of operations
        self.errors: List[Exception] = []
        self._cleanup_executed = False
    
    def start_worker(
        self,
        target: Callable,
        name: str,
        args: tuple = (),
        kwargs: Optional[Dict] = None
    ) -> tuple:
        """Start a worker thread and track it.
        
        Args:
            target: Worker function to execute
            name: Worker name for identification
            args: Positional arguments for target
            kwargs: Keyword arguments for target (must include abort_event if needed)
        
        Returns:
            (thread, abort_event) tuple
        
        Note:
            If worker needs abort_event, it must be in kwargs.
            This method creates the abort_event and adds it to kwargs.
            
        State tracking:
            - Worker is marked as 'created' before thread start
            - Worker is marked as 'started' after successful thread.start()
            - Worker is marked as 'failed' if thread.start() raises
            - Only 'started' workers are joined/verified in abort_all/join_all/verify_all_stopped
        """
        if kwargs is None:
            kwargs = {}
        
        # Create abort event for this worker
        abort_event = threading.Event()
        
        # Add abort_event to kwargs if target expects it
        # (target should accept abort_event parameter)
        kwargs['abort_event'] = abort_event
        
        # Create thread
        thread = threading.Thread(target=target, name=name, args=args, kwargs=kwargs)
        
        # Track worker as created
        worker_entry = {
            'thread': thread,
            'abort_event': abort_event,
            'name': name,
            'state': 'created'
        }
        self.workers.append(worker_entry)
        
        # Start thread with error handling
        try:
            thread.start()
            worker_entry['state'] = 'started'
        except Exception as e:
            worker_entry['state'] = 'failed'
            worker_entry['start_error'] = e
            self.errors.append(e)
            raise
        
        return thread, abort_event
    
    def abort_all(self):
        """Signal abort/release to all STARTED workers."""
        # Record operation automatically
        self.cleanup_call_order.append('abort_all')
        
        for worker in self.workers:
            if worker.get('state') != 'started':
                continue
            try:
                worker['abort_event'].set()
            except Exception as e:
                self.errors.append(e)
    
    def release_all(self):
        """Alias for abort_all - signal release to all STARTED workers."""
        self.abort_all()
    
    def join_all(self, timeout: float = 2.0) -> List[Exception]:
        """Bounded join all STARTED workers.
        
        Args:
            timeout: Maximum seconds to wait per worker
        
        Returns:
            List of exceptions from join failures
        """
        # Record operation automatically
        self.cleanup_call_order.append('join_all')
        
        join_errors = []
        
        for worker in self.workers:
            if worker.get('state') != 'started':
                continue
            try:
                worker['thread'].join(timeout=timeout)
            except Exception as e:
                join_errors.append(e)
                self.errors.append(e)
        
        return join_errors
    
    def verify_all_stopped(self) -> List[str]:
        """Verify all STARTED workers are stopped (not alive).
        
        Checks ALL started workers even if first one fails.
        Collects all errors before raising.
        
        Returns:
            List of worker names that are still alive
        
        Raises:
            WorkerNotStoppedError: If any worker still alive (after checking all)
        """
        # Record operation automatically
        self.cleanup_call_order.append('verify_all_stopped')
        
        alive_workers = []
        check_errors = []
        
        for worker in self.workers:
            if worker.get('state') != 'started':
                continue
            try:
                if worker['thread'].is_alive():
                    alive_workers.append(worker['name'])
            except Exception as e:
                check_errors.append((worker['name'], e))
                self.errors.append(e)
        
        # Report all alive workers at once
        if alive_workers:
            raise WorkerNotStoppedError(
                f"Workers still alive: {alive_workers}. "
                f"Cannot proceed to cleanup."
            )
        
        # If check errors occurred but no alive workers, still report
        if check_errors:
            error_info = "; ".join(f"{name}: {str(e)}" for name, e in check_errors)
            raise WorkerNotStoppedError(
                f"Worker stop verification errors: {error_info}. "
                f"Cannot proceed to cleanup."
            )
        
        return alive_workers
    
    def set_cleanup_callback(self, callback: Callable):
        """Set cleanup callback to execute after all workers stopped.
        
        Args:
            callback: Function to call for cleanup
        """
        self.cleanup_callback = callback
    
    def run_cleanup(self, resource_ids: Optional[Dict[str, str]] = None) -> bool:
        """Execute cleanup if all workers stopped.
        
        Args:
            resource_ids: Optional dict of resource identifiers for error messages
        
        Returns:
            True if cleanup executed successfully
        
        Raises:
            CleanupRefusedError: If workers not stopped
            Exception: If cleanup callback raises
        """
        if self._cleanup_executed:
            # Don't repeat cleanup
            return False
        
        # Verify all workers stopped
        try:
            self.verify_all_stopped()
        except WorkerNotStoppedError as e:
            # Convert to CleanupRefusedError with resource IDs
            if resource_ids:
                resource_info = ", ".join(f"{k}={v}" for k, v in resource_ids.items())
                raise CleanupRefusedError(
                    f"Cleanup refused — {str(e)} Resources at risk: {resource_info}"
                ) from e
            else:
                raise CleanupRefusedError(
                    f"Cleanup refused — {str(e)}"
                ) from e
        
        # Record cleanup in call order
        self.cleanup_call_order.append('cleanup')
        
        # Execute cleanup callback
        try:
            if self.cleanup_callback:
                self.cleanup_callback()
            self.cleanup_called_count += 1
            self._cleanup_executed = True
            return True
        except Exception as e:
            self.errors.append(e)
            raise
    
    def finalize(self, timeout: float = 5.0, resource_ids: Optional[Dict[str, str]] = None) -> bool:
        """Execute complete finalization sequence: abort → join → verify → cleanup.
        
        This is the shared finalization callable that both integration tests (B)
        and no-DB tests use. The sequence is:
        1. abort_all() - signal all workers to stop
        2. join_all(timeout) - wait for all workers to exit
        3. verify_all_stopped() - confirm all workers are not alive
        4. run_cleanup(resource_ids) - execute cleanup callback if all stopped
        
        Args:
            timeout: Timeout for join_all() in seconds
            resource_ids: Optional dict of resource identifiers for error messages
        
        Returns:
            True if cleanup executed successfully
        
        Raises:
            WorkerNotStoppedError: If any worker still alive after join
            CleanupRefusedError: If workers not stopped (from run_cleanup)
            Exception: If cleanup callback raises
        """
        # Step 1: Abort all workers
        self.abort_all()
        
        # Step 2: Join all workers with timeout
        self.join_all(timeout=timeout)
        
        # Step 3: Verify all workers stopped
        self.verify_all_stopped()
        
        # Step 4: Run cleanup (only if verify succeeded)
        return self.run_cleanup(resource_ids=resource_ids)
    
    def record_operation(self, operation: str):
        """Record an operation in the call order log.
        
        Args:
            operation: Operation name (e.g., 'abort', 'join', 'verify')
        """
        self.cleanup_call_order.append(operation)
    
    def get_call_order(self) -> List[str]:
        """Get the order of operations recorded.
        
        Returns:
            List of operation names in order
        """
        return self.cleanup_call_order.copy()
    
    def get_all_errors(self) -> List[Exception]:
        """Get all errors collected during lifecycle.
        
        Returns:
            List of exceptions
        """
        return self.errors.copy()
    
    def reset(self):
        """Reset lifecycle state for reuse.
        
        Clears workers, errors, cleanup state.
        """
        self.workers.clear()
        self.errors.clear()
        self.cleanup_callback = None
        self.cleanup_called_count = 0
        self.cleanup_call_order.clear()
        self._cleanup_executed = False
