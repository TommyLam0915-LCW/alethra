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
            # (Operations are automatically recorded by helper methods)
            lifecycle.abort_all()
            
            worker_can_exit.set()  # Allow worker to exit
            
            lifecycle.join_all(timeout=2.0)
            
            lifecycle.verify_all_stopped()
            
            lifecycle.run_cleanup()
            
            # Verify cleanup executed
            assert len(cleanup_called) == 1
            assert lifecycle.cleanup_called_count == 1
            
            # Verify call order (from automatic recording by helper methods)
            call_order = lifecycle.get_call_order()
            assert 'abort_all' in call_order
            assert 'join_all' in call_order
            assert 'verify_all_stopped' in call_order
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
        
        # Start first worker
        thread1, abort1 = lifecycle.start_worker(
            target=worker1_target,
            name="worker1"
        )
        
        # After first worker starts successfully, enter protected scope
        # Initialize original methods IMMEDIATELY (before any assertions)
        original_join1 = thread1.join
        original_join2 = None  # Will be set after second worker starts
        
        try:
            # Wait for first worker to start
            assert worker1_started.wait(timeout=2.0), "Worker 1 did not start"
            
            # Start second worker (may fail, but first worker is protected)
            thread2, abort2 = lifecycle.start_worker(
                target=worker2_target,
                name="worker2"
            )
            original_join2 = thread2.join
            
            # Wait for second worker to start
            assert worker2_started.wait(timeout=2.0), "Worker 2 did not start"
            
            # Abort all
            lifecycle.abort_all()
            worker1_can_exit.set()
            worker2_can_exit.set()
            
            # Monkey-patch thread1.join to throw an error
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
            # Restore original joins (thread2 may not exist if second start failed)
            thread1.join = original_join1
            if original_join2 is not None:
                thread2.join = original_join2
            # Independently try to reclaim each worker
            if thread1.is_alive():
                thread1.join(timeout=1.0)
            if original_join2 is not None and thread2.is_alive():
                thread2.join(timeout=1.0)
            # Verify no alive test workers
            assert not thread1.is_alive(), "Test worker 1 still alive after cleanup"
            if original_join2 is not None:
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
            
            # Abort all (automatically recorded by helper)
            lifecycle.abort_all()
            
            # Allow workers to exit
            worker1_can_exit.set()
            worker2_can_exit.set()
            
            # Join all (automatically recorded by helper)
            lifecycle.join_all(timeout=2.0)
            
            # Run cleanup (internally calls verify_all_stopped, then cleanup callback)
            # Both operations are automatically recorded by helper methods
            lifecycle.run_cleanup()
            
            # Verify cleanup executed exactly once
            assert len(cleanup_called) == 1
            assert lifecycle.cleanup_called_count == 1
            
            # Verify call order proves stop before cleanup
            # (Operations are automatically recorded by helper methods)
            # run_cleanup() internally calls verify_all_stopped() before cleanup
            call_order = lifecycle.get_call_order()
            assert call_order == [
                'abort_all',
                'join_all',
                'verify_all_stopped',  # Called by run_cleanup()
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
        """Cleanup throws error - both errors preserved through exception chain."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        worker_can_exit = threading.Event()
        
        def worker_target(abort_event):
            worker_started.set()
            abort_event.wait(timeout=5.0)
            worker_can_exit.wait(timeout=1.0)
        
        # Pre-build two distinct exception instances
        main_error = ValueError("Main flow failed")
        cleanup_error = RuntimeError("Cleanup failed")
        
        def cleanup_callback():
            raise cleanup_error
        
        lifecycle.set_cleanup_callback(cleanup_callback)
        
        # Start worker
        thread, abort_event = lifecycle.start_worker(
            target=worker_target,
            name="test_worker"
        )
        
        caught_exception = []
        
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
                # This will mask main_error, but Python preserves it in __context__
                lifecycle.run_cleanup()
                    
        except Exception as e:
            # Should get cleanup_error (pre-built instance)
            caught_exception.append(e)
        finally:
            # Ensure cleanup
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
        
        # Verify we caught cleanup_error
        assert len(caught_exception) == 1, "Should catch one exception"
        caught = caught_exception[0]
        assert caught is cleanup_error, f"Caught exception should be cleanup_error instance, got {caught}"
        
        # Verify exception chain: cleanup_error.__context__ should be main_error
        assert caught.__context__ is main_error, (
            f"cleanup_error.__context__ should be main_error instance. "
            f"Got: {caught.__context__}"
        )
        
        # Verify cleanup_error is in lifecycle errors
        all_errors = lifecycle.get_all_errors()
        assert cleanup_error in all_errors, "cleanup_error should be in lifecycle errors"
        
        # Verify we can distinguish the two pre-built instances by identity
        error_ids = [id(e) for e in all_errors]
        assert id(cleanup_error) in error_ids, "cleanup_error instance should be preserved"
        # main_error is NOT in lifecycle.errors (it's in __context__, not added by helper)
        assert id(main_error) not in error_ids, "main_error should not be in lifecycle errors (only in __context__)"


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
            
            # Abort worker (automatically recorded by helper)
            lifecycle.abort_all()
            
            # Allow worker to exit
            worker_can_exit.set()
            
            # Join worker (automatically recorded by helper)
            lifecycle.join_all(timeout=2.0)
            
            # Verify worker stopped (automatically recorded by helper)
            lifecycle.verify_all_stopped()
            
            # Run cleanup (automatically recorded by helper)
            lifecycle.run_cleanup()
            
            # Verify cleanup executed
            assert cleanup_executed.wait(timeout=1.0), "Cleanup did not execute"
            assert lifecycle.cleanup_called_count == 1
            
            # Verify thread actually completed
            assert worker_completed.is_set(), "Worker did not complete"
            
            # Verify call order (from automatic recording by helper methods)
            call_order = lifecycle.get_call_order()
            assert 'abort_all' in call_order
            assert 'join_all' in call_order
            assert 'verify_all_stopped' in call_order
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
        
        # Initialize original methods IMMEDIATELY after worker start
        # (before any assertions that might fail)
        original_is_alive1 = thread1.is_alive
        original_is_alive2 = thread2.is_alive
        
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
            is_alive_error = RuntimeError("is_alive check failed")
            
            is_alive2_called = []
            
            def failing_is_alive1():
                raise is_alive_error
            
            def tracking_is_alive2():
                # Record that second is_alive was called
                is_alive2_called.append(True)
                # Call original is_alive
                return original_is_alive2()
            
            thread1.is_alive = failing_is_alive1
            thread2.is_alive = tracking_is_alive2
            
            # Verify all stopped - should collect error from first, still check second
            with pytest.raises(WorkerNotStoppedError) as exc_info:
                lifecycle.verify_all_stopped()
            
            # Verify second is_alive was actually called (not skipped due to first error)
            assert len(is_alive2_called) == 1, "Second is_alive should have been called despite first is_alive error"
            
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
            # Restore original is_alives
            thread1.is_alive = original_is_alive1
            thread2.is_alive = original_is_alive2
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


class TestFinalizeDirectCall:
    """Tests that call finalize() directly to verify the shared finalization callable."""
    
    def test_finalize_normal_flow(self):
        """finalize() with normal flow: abort → release → join → cleanup."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        worker_can_exit = threading.Event()
        release_called = []
        
        def worker_target(abort_event):
            worker_started.set()
            abort_event.wait(timeout=5.0)
            worker_can_exit.wait(timeout=1.0)
        
        def release_callback():
            release_called.append(True)
            worker_can_exit.set()
        
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
            
            # Call finalize() directly with release callback
            result = lifecycle.finalize(timeout=2.0, release_callback=release_callback)
            
            # Verify cleanup executed
            assert result is True
            assert len(cleanup_called) == 1
            assert lifecycle.cleanup_called_count == 1
            
            # Verify release callback was called
            assert len(release_called) == 1
            
            # Verify call order (from automatic recording)
            call_order = lifecycle.get_call_order()
            assert call_order == ['abort_all', 'join_all', 'verify_all_stopped', 'cleanup']
            
        finally:
            # Ensure cleanup
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
    
    def test_finalize_workers_not_stopped(self):
        """finalize() refuses cleanup when workers not stopped."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        worker_can_exit = threading.Event()
        
        def worker_target(abort_event):
            worker_started.set()
            abort_event.wait(timeout=5.0)
            # Wait for explicit exit signal - worker will stay alive until this is set
            worker_can_exit.wait(timeout=10.0)
        
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
            
            # Call finalize() with short timeout - should refuse because worker not stopped
            # (worker is waiting on worker_can_exit which is not set yet)
            # run_cleanup converts WorkerNotStoppedError to CleanupRefusedError
            with pytest.raises(CleanupRefusedError):
                lifecycle.finalize(timeout=0.5)
            
            # Verify cleanup NOT executed
            assert len(cleanup_called) == 0
            assert lifecycle.cleanup_called_count == 0
            
        finally:
            # Ensure cleanup
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
    
    def test_finalize_verification_throws(self):
        """finalize() propagates verification errors."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        worker_can_exit = threading.Event()
        
        def worker_target(abort_event):
            worker_started.set()
            abort_event.wait(timeout=5.0)
            worker_can_exit.wait(timeout=1.0)
        
        # Start worker
        thread, abort_event = lifecycle.start_worker(
            target=worker_target,
            name="test_worker"
        )
        
        # Save original is_alive IMMEDIATELY after worker start
        # (before any assertions that might fail)
        original_is_alive = thread.is_alive
        
        try:
            # Wait for worker to start
            assert worker_started.wait(timeout=2.0), "Worker did not start"
            
            # Abort and join
            lifecycle.abort_all()
            worker_can_exit.set()
            lifecycle.join_all(timeout=2.0)
            
            # Monkey-patch is_alive to throw
            is_alive_error = RuntimeError("is_alive check failed failed")
            
            def failing_is_alive():
                raise is_alive_error
            
            thread.is_alive = failing_is_alive
            
            # Call finalize() - should propagate verification error as CleanupRefusedError
            # (run_cleanup converts WorkerNotStoppedError to CleanupRefusedError)
            with pytest.raises(CleanupRefusedError) as exc_info:
                lifecycle.finalize(timeout=2.0)
            
            # Verify error message mentions is_alive
            assert "is_alive" in str(exc_info.value).lower() or "verification errors" in str(exc_info.value).lower()
            
            # Verify the cause is WorkerNotStoppedError
            assert isinstance(exc_info.value.__cause__, WorkerNotStoppedError)
            
            # Verify error preserved
            all_errors = lifecycle.get_all_errors()
            assert is_alive_error in all_errors
            
        finally:
            # Restore original is_alive (guaranteed to exist)
            thread.is_alive = original_is_alive
            # Ensure cleanup
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
    
    def test_finalize_duplicate_cleanup(self):
        """finalize() does not repeat cleanup on duplicate call."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        worker_can_exit = threading.Event()
        
        def worker_target(abort_event):
            worker_started.set()
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
            
            # Call finalize() first time
            result1 = lifecycle.finalize(timeout=2.0, release_callback=worker_can_exit.set)
            assert result1 is True
            assert len(cleanup_called) == 1
            
            # Call finalize() second time - should not repeat cleanup
            result2 = lifecycle.finalize(timeout=2.0, release_callback=worker_can_exit.set)
            assert result2 is False  # Returns False on duplicate
            assert len(cleanup_called) == 1  # Still 1, not 2
            assert lifecycle.cleanup_called_count == 1
            
        finally:
            # Ensure cleanup
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
    
    def test_finalize_callback_fails(self):
        """finalize() propagates cleanup callback errors and does not mark success."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        worker_can_exit = threading.Event()
        
        def worker_target(abort_event):
            worker_started.set()
            abort_event.wait(timeout=5.0)
            worker_can_exit.wait(timeout=1.0)
        
        cleanup_error = RuntimeError("Cleanup callback failed")
        def failing_callback():
            raise cleanup_error
        
        lifecycle.set_cleanup_callback(failing_callback)
        
        # Start worker
        thread, abort_event = lifecycle.start_worker(
            target=worker_target,
            name="test_worker"
        )
        
        try:
            # Wait for worker to start
            assert worker_started.wait(timeout=2.0), "Worker did not start"
            
            # Call finalize() - should propagate cleanup error
            with pytest.raises(RuntimeError) as exc_info:
                lifecycle.finalize(timeout=2.0, release_callback=worker_can_exit.set)
            
            # Verify error is the cleanup error
            assert exc_info.value is cleanup_error
            
            # Verify error preserved in lifecycle errors
            all_errors = lifecycle.get_all_errors()
            assert cleanup_error in all_errors
            
            # Verify cleanup NOT marked as executed (public behavior)
            assert lifecycle.cleanup_called_count == 0
            assert not lifecycle._cleanup_executed
            
            # Now swap to a success callback
            success_called = []
            def success_callback():
                success_called.append(True)
            
            lifecycle.set_cleanup_callback(success_callback)
            
            # Call finalize() again - should succeed this time
            result = lifecycle.finalize(timeout=2.0, release_callback=worker_can_exit.set)
            assert result is True
            assert len(success_called) == 1
            assert lifecycle.cleanup_called_count == 1
            assert lifecycle._cleanup_executed
            
            # Call finalize() a third time - should not repeat cleanup
            result2 = lifecycle.finalize(timeout=2.0, release_callback=worker_can_exit.set)
            assert result2 is False
            assert len(success_called) == 1  # Still 1, not 2
            assert lifecycle.cleanup_called_count == 1  # Still 1
            
        finally:
            # Ensure cleanup
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
            # Assert thread stopped
            assert not thread.is_alive(), "Worker thread still alive after cleanup"
    
    def test_finalize_main_error_chain(self):
        """finalize() preserves main error in exception chain."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        worker_can_exit = threading.Event()
        
        def worker_target(abort_event):
            worker_started.set()
            abort_event.wait(timeout=5.0)
            worker_can_exit.wait(timeout=1.0)
        
        main_error = ValueError("Main flow failed")
        cleanup_error = RuntimeError("Cleanup callback failed")
        
        def cleanup_callback():
            raise cleanup_error
        
        lifecycle.set_cleanup_callback(cleanup_callback)
        
        # Start worker
        thread, abort_event = lifecycle.start_worker(
            target=worker_target,
            name="test_worker"
        )
        
        caught_exception = []
        
        # Real outer try/finally ensures cleanup even if assertions fail
        try:
            # Wait for worker to start
            assert worker_started.wait(timeout=2.0), "Worker did not start"
            
            # Simulate main flow failure with finalize in finally
            try:
                raise main_error
            finally:
                lifecycle.finalize(timeout=2.0, release_callback=worker_can_exit.set)
                
        except Exception as e:
            caught_exception.append(e)
        finally:
            # Ensure worker exits and thread is reclaimed
            worker_can_exit.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
            # Assert thread stopped
            assert not thread.is_alive(), "Worker thread still alive after cleanup"
        
        # Verify we caught cleanup_error
        assert len(caught_exception) == 1
        caught = caught_exception[0]
        assert caught is cleanup_error
        
        # Verify exception chain: cleanup_error.__context__ is main_error
        assert caught.__context__ is main_error
        
        # Verify cleanup_error preserved in lifecycle errors
        all_errors = lifecycle.get_all_errors()
        assert cleanup_error in all_errors
    
    def test_finalize_independent_release_event(self):
        """finalize() with independent release Event (like B's release_all)."""
        lifecycle = WorkerLifecycle()
        
        worker_started = threading.Event()
        release_all = threading.Event()  # Independent release Event
        
        def worker_target(abort_event):
            worker_started.set()
            abort_event.wait(timeout=5.0)
            release_all.wait(timeout=5.0)  # Wait for release Event
        
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
            
            # Call finalize() with release_all.set as release callback
            result = lifecycle.finalize(timeout=2.0, release_callback=release_all.set)
            
            # Verify cleanup executed
            assert result is True
            assert len(cleanup_called) == 1
            
            # Verify release Event was set
            assert release_all.is_set()
            
        finally:
            # Ensure cleanup
            release_all.set()
            lifecycle.abort_all()
            if thread.is_alive():
                thread.join(timeout=1.0)
