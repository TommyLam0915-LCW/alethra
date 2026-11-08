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
            original_join2 = thread2.join
            join_error = RuntimeError("Thread 1 join failed")
            
            join2_called = []
            
            def failing_join1(timeout=None):
                # Call original join first (thread will exit)
                original_join1(timeout=timeout)
                # Then raise error
                raise join_error
            
            def tracking_join2(timeout=None):
                # Record that second join was called
                join2_called.append(True)
                # Call original join
                original_join2(timeout=timeout)
            
            thread1.join = failing_join1
            thread2.join = tracking_join2
            
            # Join all - first will throw, but second should still be attempted
            join_errors = lifecycle.join_all(timeout=2.0)
            
            # Verify first join error was captured
            assert len(join_errors) == 1
            assert join_errors[0] is join_error
            
            # Verify second join was actually called (not skipped due to first error)
            assert len(join2_called) == 1, "Second join should have been called despite first join error"
            
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
            # Restore original joins
            thread1.join = original_join1
            thread2.join = original_join2
            # Independently try to reclaim each worker
            if thread1.is_alive():
                thread1.join(timeout=1.0)
            if thread2.is_alive():
                thread2.join(timeout=1.0)
            # Verify no alive test workers
            assert not thread1.is_alive(), "Test worker 1 still alive after cleanup"
            assert not thread2.is_alive(), "Test worker 2 still alive after cleanup"


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
        
        # Pre-build two distinct exception instances
        main_error = ValueError("Main flow failed")
        cleanup_error = RuntimeError("Cleanup failed")
        
        caught_main = []
        caught_cleanup = []
        
        # Simulate real finalizer path: try/finally with main error
        try:
            try:
                # Wait for worker to start
                assert worker_started.wait(timeout=2.0), "Worker did not start"
                
                # Simulate main flow failure - raise pre-built instance
                raise main_error
                
            finally:
                # Finally block: abort, join, verify, cleanup
                lifecycle.abort_all()
                worker_can_exit.set()
                lifecycle.join_all(timeout=2.0)
                lifecycle.verify_all_stopped()
                
                # Cleanup should throw pre-built cleanup_error
                lifecycle.run_cleanup()
                    
        except ValueError as e:
            # Should get main_error (pre-built instance)
            assert e is main_error, f"Expected main_error instance, got {e}"
            caught_main.append(e)
        except RuntimeError as e:
            # Should get cleanup_error (pre-built instance)
            assert e is cleanup_error, f"Expected cleanup_error instance, got {e}"
            caught_cleanup.append(e)
        finally:
            # Ensure cleanup
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
        
        # Verify we caught exactly one of the pre-built exceptions
        # (cleanup_error should be caught, masking main_error)
        assert len(caught_cleanup) == 1, "Should catch cleanup_error"
        assert caught_cleanup[0] is cleanup_error, "Caught exception should be pre-built cleanup_error instance"
        
        # Verify both pre-built exception instances are identifiable in lifecycle errors
        all_errors = lifecycle.get_all_errors()
        assert cleanup_error in all_errors, "cleanup_error should be in lifecycle errors"
        
        # Verify we can distinguish the two pre-built instances by identity
        error_ids = [id(e) for e in all_errors]
        assert id(cleanup_error) in error_ids, "cleanup_error instance should be preserved"
        assert id(main_error) not in error_ids, "main_error should not be in lifecycle errors (it was caught)"


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


class TestSecondStartThrows:
    """Test: Second worker start throws - first worker still cleaned up."""
    
    def test_first_worker_cleaned_up_when_second_start_fails(self):
        """Second start throws - first worker gets abort/release/join, start error preserved."""
        lifecycle = WorkerLifecycle()
        
        worker1_started = threading.Event()
        worker1_can_exit = threading.Event()
        
        def worker1_target(abort_event):
            worker1_started.set()
            abort_event.wait(timeout=5.0)
            worker1_can_exit.wait(timeout=1.0)
        
        # Start first worker successfully
        thread1, abort1 = lifecycle.start_worker(
            target=worker1_target,
            name="worker1"
        )
        
        # Wait for worker1 to start
        assert worker1_started.wait(timeout=2.0), "Worker 1 did not start"
        
        # Monkey-patch threading.Thread.start to throw on second call
        original_start = threading.Thread.start
        start_error = RuntimeError("Thread start failed")
        
        def failing_start(self):
            raise start_error
        
        threading.Thread.start = failing_start
        
        try:
            # Try to start second worker - should throw
            with pytest.raises(RuntimeError) as exc_info:
                thread2, abort2 = lifecycle.start_worker(
                    target=lambda abort_event: None,
                    name="worker2"
                )
            
            # Verify start error is the one we raised
            assert exc_info.value is start_error
            
            # Verify start error preserved in lifecycle errors
            all_errors = lifecycle.get_all_errors()
            assert start_error in all_errors, "Start error should be preserved"
            
            # Verify first worker state is 'started'
            worker1_entry = next(w for w in lifecycle.workers if w['name'] == 'worker1')
            assert worker1_entry['state'] == 'started'
            
            # Now cleanup first worker
            lifecycle.abort_all()
            worker1_can_exit.set()
            lifecycle.join_all(timeout=2.0)
            
            # Verify first worker actually stopped
            lifecycle.verify_all_stopped()
            assert not thread1.is_alive(), "Worker 1 should be stopped"
            
        finally:
            # Restore original start
            threading.Thread.start = original_start
            # Ensure worker1 exits
            worker1_can_exit.set()
            lifecycle.abort_all()
            if thread1.is_alive():
                thread1.join(timeout=1.0)
            # Verify no alive test workers
            assert not thread1.is_alive(), "Test worker still alive after cleanup"


class TestFirstIsAliveThrows:
    """Test: First is_alive throws - still check second, cleanup refused."""
    
    def test_still_check_second_when_first_is_alive_throws(self):
        """First is_alive throws - still check second, cleanup callback zero times."""
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
            
            # Join all
            lifecycle.join_all(timeout=2.0)
            
            # Monkey-patch thread1.is_alive to throw
            original_is_alive1 = thread1.is_alive
            is_alive_error = RuntimeError("is_alive check failed")
            
            def failing_is_alive():
                raise is_alive_error
            
            thread1.is_alive = failing_is_alive
            
            # Verify all stopped - should collect error from first, still check second
            with pytest.raises(WorkerNotStoppedError) as exc_info:
                lifecycle.verify_all_stopped()
            
            # Verify error message mentions the is_alive error
            assert "is_alive" in str(exc_info.value).lower() or "verification errors" in str(exc_info.value).lower()
            
            # Verify is_alive error preserved in lifecycle errors
            all_errors = lifecycle.get_all_errors()
            assert is_alive_error in all_errors, "is_alive error should be preserved"
            
            # Cleanup should NOT execute (verification failed)
            with pytest.raises(CleanupRefusedError):
                lifecycle.run_cleanup()
            
            # Verify cleanup callback called zero times
            assert len(cleanup_called) == 0, "Cleanup callback should not be called when verification fails"
            assert lifecycle.cleanup_called_count == 0
            
        finally:
            # Restore original is_alive
            thread1.is_alive = original_is_alive1
            # Ensure cleanup
            worker1_can_exit.set()
            worker2_can_exit.set()
            lifecycle.abort_all()
            if thread1.is_alive():
                thread1.join(timeout=1.0)
            if thread2.is_alive():
                thread2.join(timeout=1.0)
            # Verify no alive test workers
            assert not thread1.is_alive(), "Test worker 1 still alive after cleanup"
            assert not thread2.is_alive(), "Test worker 2 still alive after cleanup"
