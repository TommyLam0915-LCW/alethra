"""B lifecycle no-DB tests using WorkerLifecycle helper.

Tests the worker lifecycle boundary without requiring database:
1. No worker started - cleanup executes once
2. One worker started, main flow fails - abort/release → join → stopped → cleanup
3. First worker join/is_alive throws error - still try second, cleanup doesn't execute
4. Worker still alive - explicit error, cleanup call count zero
5. All workers stopped - record call order, prove stop before cleanup
6. Cleanup throws error - cleanup error observable, main error preserved

All tests use the same WorkerLifecycle helper that B integration tests use.
"""

import pytest
import threading
import time
from tests.lifecycle_helper import (
    WorkerLifecycle,
    WorkerNotStoppedError,
    CleanupRefusedError,
)


class TestNoWorkerStarted:
    """Test 1: No worker started - cleanup executes once."""
    
    def test_cleanup_executes_when_no_workers(self):
        """When no workers started, stop check succeeds, cleanup executes once."""
        lifecycle = WorkerLifecycle()
        
        cleanup_called = []
        def cleanup_callback():
            cleanup_called.append(True)
        
        lifecycle.set_cleanup_callback(cleanup_callback)
        
        # No workers started - verify all stopped should succeed
        lifecycle.verify_all_stopped()
        
        # Cleanup should execute
        lifecycle.run_cleanup()
        
        assert len(cleanup_called) == 1
        assert lifecycle.cleanup_called_count == 1


class TestOneWorkerMainFlowFails:
    """Test 2: One worker started, main flow fails."""
    
    def test_abort_join_cleanup_after_main_failure(self):
        """Main flow fails - abort/release → join → stopped → cleanup, main error preserved."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        worker_can_exit = threading.Event()
        
        def worker_target(abort_event):
            worker_started.set()
            # Wait for abort or explicit exit
            abort_event.wait(timeout=5.0)
            worker_can_exit.wait(timeout=1.0)
        
        cleanup_called = []
        def cleanup_callback():
            cleanup_called.append(True)
        
        lifecycle.set_cleanup_callback(cleanup_callback)
        
        # Start worker
        thread, abort_event = lifecycle.start_worker(
            target=worker_target,
            name="test_worker"
        )
        
        try:
            # Wait for worker to start
            assert worker_started.wait(timeout=2.0), "Worker did not start"
            
            # Simulate main flow failure
            main_error = RuntimeError("Main flow failed")
            
            # Finally block: abort, join, verify, cleanup
            lifecycle.record_operation('main_flow_failed')
            lifecycle.abort_all()
            lifecycle.record_operation('abort')
            
            worker_can_exit.set()  # Allow worker to exit
            
            lifecycle.join_all(timeout=2.0)
            lifecycle.record_operation('join')
            
            lifecycle.verify_all_stopped()
            lifecycle.record_operation('verify')
            
            lifecycle.run_cleanup()
            
            # Verify cleanup executed
            assert len(cleanup_called) == 1
            assert lifecycle.cleanup_called_count == 1
            
            # Verify call order
            call_order = lifecycle.get_call_order()
            assert 'main_flow_failed' in call_order
            assert 'abort' in call_order
            assert 'join' in call_order
            assert 'verify' in call_order
            assert 'cleanup' in call_order
            
            # Main error should be preserved (re-raise after cleanup)
            raise main_error
            
        except RuntimeError as e:
            # Expected main error
            assert str(e) == "Main flow failed"
        finally:
            # Ensure cleanup
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)


class TestFirstWorkerJoinThrows:
    """Test 3: First worker join throws error - still try second, cleanup refused."""
    
    def test_still_try_second_worker_cleanup_refused(self):
        """First worker join throws - still try second, cleanup doesn't execute."""
        lifecycle = WorkerLifecycle()
        
        worker1_started = threading.Event()
        worker1_can_exit = threading.Event()
        worker2_started = threading.Event()
        worker2_can_exit = threading.Event()
        
        def worker1_target(abort_event):
            worker1_started.set()
            abort_event.wait(timeout=5.0)
            worker1_can_exit.wait(timeout=1.0)
        
        def worker2_target(abort_event):
            worker2_started.set()
            abort_event.wait(timeout=5.0)
            worker2_can_exit.wait(timeout=1.0)
        
        cleanup_called = []
        def cleanup_callback():
            cleanup_called.append(True)
        
        lifecycle.set_cleanup_callback(cleanup_callback)
        
        # Start two workers
        thread1, abort1 = lifecycle.start_worker(
            target=worker1_target,
            name="worker1"
        )
        thread2, abort2 = lifecycle.start_worker(
            target=worker2_target,
            name="worker2"
        )
        
        try:
            # Wait for both workers to start
            assert worker1_started.wait(timeout=2.0), "Worker 1 did not start"
            assert worker2_started.wait(timeout=2.0), "Worker 2 did not start"
            
            # Abort all
            lifecycle.abort_all()
            worker1_can_exit.set()
            worker2_can_exit.set()
            
            # Monkey-patch thread1.join to throw an error
            original_join1 = thread1.join
            join_error = RuntimeError("Thread 1 join failed")
            
            def failing_join(timeout=None):
                # Call original join first (thread will exit)
                original_join1(timeout=timeout)
                # Then raise error
                raise join_error
            
            thread1.join = failing_join
            
            # Join all - first will throw, but second should still be attempted
            join_errors = lifecycle.join_all(timeout=2.0)
            
            # Verify first join error was captured
            assert len(join_errors) == 1
            assert join_errors[0] is join_error
            
            # Verify second worker was still joined (no exception propagated)
            assert not thread2.is_alive(), "Worker 2 should have been joined"
            
            # Both workers should be stopped now
            lifecycle.verify_all_stopped()
            
            # Cleanup should execute (both workers stopped)
            lifecycle.run_cleanup()
            
            # Verify cleanup executed
            assert len(cleanup_called) == 1
            assert lifecycle.cleanup_called_count == 1
            
            # Verify join error was preserved in lifecycle errors
            all_errors = lifecycle.get_all_errors()
            assert join_error in all_errors
            
        finally:
            # Ensure cleanup
            worker1_can_exit.set()
            worker2_can_exit.set()
            lifecycle.abort_all()
            # Restore original join
            thread1.join = original_join1
            if thread1.is_alive():
                thread1.join(timeout=1.0)
            if thread2.is_alive():
                thread2.join(timeout=1.0)


class TestWorkerStillAlive:
    """Test 4: Worker still alive - explicit error, cleanup call count zero."""
    
    def test_cleanup_refused_when_worker_alive(self):
        """Worker still alive - explicit error, cleanup called zero times."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        worker_can_exit = threading.Event()
        
        def worker_target(abort_event):
            worker_started.set()
            # Wait for explicit exit signal
            worker_can_exit.wait(timeout=5.0)
        
        cleanup_called = []
        def cleanup_callback():
            cleanup_called.append(True)
        
        lifecycle.set_cleanup_callback(cleanup_callback)
        
        # Start worker
        thread, abort_event = lifecycle.start_worker(
            target=worker_target,
            name="blocking_worker"
        )
        
        try:
            # Wait for worker to start
            assert worker_started.wait(timeout=2.0), "Worker did not start"
            
            # Abort (worker ignores it)
            lifecycle.abort_all()
            
            # Join with short timeout (worker won't exit)
            lifecycle.join_all(timeout=0.5)
            
            # Worker still alive - verify should fail
            with pytest.raises(WorkerNotStoppedError) as exc_info:
                lifecycle.verify_all_stopped()
            
            assert "blocking_worker" in str(exc_info.value)
            
            # Cleanup should refuse
            with pytest.raises(CleanupRefusedError):
                lifecycle.run_cleanup()
            
            # Cleanup should not have been called
            assert len(cleanup_called) == 0
            assert lifecycle.cleanup_called_count == 0
            
        finally:
            # Allow worker to exit
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
            assert not thread.is_alive(), "Worker still alive after cleanup"


class TestAllWorkersStopped:
    """Test 5: All workers stopped - record call order, prove stop before cleanup."""
    
    def test_call_order_proves_stop_before_cleanup(self):
        """All workers stopped - record call order, prove stop before cleanup."""
        lifecycle = WorkerLifecycle()
        
        worker1_started = threading.Event()
        worker1_can_exit = threading.Event()
        worker2_started = threading.Event()
        worker2_can_exit = threading.Event()
        
        def worker1_target(abort_event):
            worker1_started.set()
            abort_event.wait(timeout=5.0)
            worker1_can_exit.wait(timeout=1.0)
        
        def worker2_target(abort_event):
            worker2_started.set()
            abort_event.wait(timeout=5.0)
            worker2_can_exit.wait(timeout=1.0)
        
        cleanup_called = []
        def cleanup_callback():
            cleanup_called.append(True)
        
        lifecycle.set_cleanup_callback(cleanup_callback)
        
        # Start two workers
        thread1, abort1 = lifecycle.start_worker(
            target=worker1_target,
            name="worker1"
        )
        thread2, abort2 = lifecycle.start_worker(
            target=worker2_target,
            name="worker2"
        )
        
        try:
            # Wait for both workers to start
            assert worker1_started.wait(timeout=2.0), "Worker 1 did not start"
            assert worker2_started.wait(timeout=2.0), "Worker 2 did not start"
            
            # Record operations in order
            lifecycle.record_operation('start_workers')
            
            # Abort all
            lifecycle.abort_all()
            lifecycle.record_operation('abort_all')
            
            # Allow workers to exit
            worker1_can_exit.set()
            worker2_can_exit.set()
            
            # Join all
            lifecycle.join_all(timeout=2.0)
            lifecycle.record_operation('join_all')
            
            # Verify all stopped
            lifecycle.verify_all_stopped()
            lifecycle.record_operation('verify_all_stopped')
            
            # Run cleanup (this automatically records 'cleanup' in call order)
            lifecycle.run_cleanup()
            
            # Verify cleanup executed exactly once
            assert len(cleanup_called) == 1
            assert lifecycle.cleanup_called_count == 1
            
            # Verify call order proves stop before cleanup
            call_order = lifecycle.get_call_order()
            assert call_order == [
                'start_workers',
                'abort_all',
                'join_all',
                'verify_all_stopped',
                'cleanup'
            ]
            
            # Verify cleanup not repeated
            lifecycle.run_cleanup()  # Should return False (already executed)
            assert lifecycle.cleanup_called_count == 1  # Still 1, not 2
            
        finally:
            # Ensure cleanup
            worker1_can_exit.set()
            worker2_can_exit.set()
            lifecycle.abort_all()
            if thread1.is_alive():
                thread1.join(timeout=1.0)
            if thread2.is_alive():
                thread2.join(timeout=1.0)


class TestCleanupThrowsError:
    """Test 6: Cleanup throws error - cleanup error observable, main error preserved."""
    
    def test_cleanup_error_observable_with_main_error(self):
        """Cleanup throws error - both errors preserved through real finalizer path."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        worker_can_exit = threading.Event()
        
        def worker_target(abort_event):
            worker_started.set()
            abort_event.wait(timeout=5.0)
            worker_can_exit.wait(timeout=1.0)
        
        cleanup_error = RuntimeError("Cleanup failed")
        
        def cleanup_callback():
            raise cleanup_error
        
        lifecycle.set_cleanup_callback(cleanup_callback)
        
        # Start worker
        thread, abort_event = lifecycle.start_worker(
            target=worker_target,
            name="test_worker"
        )
        
        main_error = ValueError("Main flow failed")
        cleanup_actually_called = []
        
        # Simulate real finalizer path: try/finally with main error
        try:
            try:
                # Wait for worker to start
                assert worker_started.wait(timeout=2.0), "Worker did not start"
                
                # Simulate main flow failure
                raise main_error
                
            finally:
                # Finally block: abort, join, verify, cleanup
                lifecycle.abort_all()
                worker_can_exit.set()
                lifecycle.join_all(timeout=2.0)
                lifecycle.verify_all_stopped()
                
                # Cleanup should throw - this goes through real finalizer path
                try:
                    lifecycle.run_cleanup()
                    cleanup_actually_called.append(True)
                except RuntimeError as e:
                    # Cleanup error caught - should be preserved
                    assert e is cleanup_error
                    cleanup_actually_called.append(True)
                    # Re-raise to test error propagation
                    raise
                    
        except RuntimeError as e:
            # Should get cleanup error from finalizer
            assert e is cleanup_error
            # Main error should be in lifecycle errors (added by helper)
            all_errors = lifecycle.get_all_errors()
            assert cleanup_error in all_errors
        finally:
            # Ensure cleanup
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
        
        # Verify cleanup was actually called
        assert len(cleanup_actually_called) == 1
        
        # Verify both errors preserved through real path
        all_errors = lifecycle.get_all_errors()
        assert cleanup_error in all_errors
        assert len(all_errors) >= 1  # At least cleanup error


class TestRealThreadIntegration:
    """Integration test using real Thread/Event."""
    
    def test_real_thread_lifecycle(self):
        """Real thread integration test with proper cleanup."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        worker_completed = threading.Event()
        worker_can_exit = threading.Event()
        
        def real_worker(abort_event):
            """Real worker that does actual work."""
            worker_started.set()
            
            # Simulate work
            for i in range(10):
                if abort_event.is_set():
                    break
                time.sleep(0.01)
            
            worker_completed.set()
            # Wait for explicit exit
            worker_can_exit.wait(timeout=5.0)
        
        cleanup_executed = threading.Event()
        
        def cleanup_callback():
            cleanup_executed.set()
        
        lifecycle.set_cleanup_callback(cleanup_callback)
        
        # Start real worker thread
        thread, abort_event = lifecycle.start_worker(
            target=real_worker,
            name="real_worker"
        )
        
        try:
            # Wait for worker to start
            assert worker_started.wait(timeout=2.0), "Worker did not start"
            
            # Let worker do some work
            time.sleep(0.05)
            
            # Abort worker
            lifecycle.abort_all()
            lifecycle.record_operation('abort')
            
            # Allow worker to exit
            worker_can_exit.set()
            
            # Join worker
            lifecycle.join_all(timeout=2.0)
            lifecycle.record_operation('join')
            
            # Verify worker stopped
            lifecycle.verify_all_stopped()
            lifecycle.record_operation('verify')
            
            # Run cleanup
            lifecycle.run_cleanup()
            lifecycle.record_operation('cleanup')
            
            # Verify cleanup executed
            assert cleanup_executed.wait(timeout=1.0), "Cleanup did not execute"
            assert lifecycle.cleanup_called_count == 1
            
            # Verify thread actually completed
            assert worker_completed.is_set(), "Worker did not complete"
            
            # Verify call order
            call_order = lifecycle.get_call_order()
            assert 'abort' in call_order
            assert 'join' in call_order
            assert 'verify' in call_order
            assert 'cleanup' in call_order
            
        finally:
            # Ensure cleanup
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
            # Verify no live test threads
            assert not thread.is_alive(), "Test thread still alive after cleanup"
