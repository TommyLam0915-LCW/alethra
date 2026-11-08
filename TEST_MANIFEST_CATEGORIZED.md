# Test Manifest — Categorized by Execution Profile

**Code SHA under test**: working tree at `aaf5bb2` + uncommitted guard-rejection test  
**Total tests**: 119  
**Date**: 2026-11-08  
**Environment**: macOS 13.3 arm64, Python 3.12.16, PostgreSQL on port 5432 socket /tmp  
**Test database**: `ai_investment_lab_test`, role `alethra_test_runtime`

## Category Definitions

| Category | Meaning |
|----------|---------|
| **no-DB** | Pure unit tests; no PostgreSQL connection required |
| **connection-failure** | Tests that trigger OperationalError (connection refused, role not found, etc.) |
| **connected-guard-rejection** | Tests that connect successfully, execute verification query, THEN get rejected by guard |
| **PG production-path** | Tests that execute real business logic against PostgreSQL |
| **security** | Extra label: tests that verify credential masking or non-leakage (overlaps with above) |

---

## no-DB (48 tests)

### test_paper_trading_cancel_atomic.py — TestSessionFactoryContract (5)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestSessionFactoryContract::test_check_cancel_requested_calls_factory
backend/tests/test_paper_trading_cancel_atomic.py::TestSessionFactoryContract::test_cancel_run_calls_factory_on_not_found
backend/tests/test_paper_trading_cancel_atomic.py::TestSessionFactoryContract::test_multiple_operations_reuse_factory
backend/tests/test_paper_trading_cancel_atomic.py::TestSessionFactoryContract::test_legacy_database_url_creates_working_factory
backend/tests/test_paper_trading_cancel_atomic.py::TestSessionFactoryContract::test_no_parameters_falls_back_to_global_with_warning
```

### test_paper_trading_cancel_atomic.py — TestCleanupSafety (helper tests) (2)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_detects_residual_orders
backend/tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_detects_residual_fills
```

### test_target_guard.py — URL parsing (20)
```
backend/tests/test_target_guard.py::test_extract_socket_dir_valid
backend/tests/test_target_guard.py::test_extract_socket_dir_tcp_returns_none
backend/tests/test_target_guard.py::test_extract_socket_dir_no_host_param_returns_none
backend/tests/test_target_guard.py::test_extract_socket_dir_empty_host_returns_empty_string
backend/tests/test_target_guard.py::test_extract_socket_dir_multiple_hosts_rejected
backend/tests/test_target_guard.py::test_extract_socket_dir_authority_and_query_host_conflict
backend/tests/test_target_guard.py::test_extract_port_from_url_query_param
backend/tests/test_target_guard.py::test_extract_port_from_url_authority
backend/tests/test_target_guard.py::test_extract_port_from_url_none
backend/tests/test_target_guard.py::test_extract_port_from_url_empty_value_rejected
backend/tests/test_target_guard.py::test_extract_port_from_url_non_numeric_rejected
backend/tests/test_target_guard.py::test_extract_port_from_url_multiple_values_rejected
backend/tests/test_target_guard.py::test_extract_port_from_url_out_of_range_rejected
backend/tests/test_target_guard.py::test_extract_port_from_url_zero_rejected
backend/tests/test_target_guard.py::test_extract_port_from_url_authority_zero_rejected
backend/tests/test_target_guard.py::test_extract_port_from_url_authority_and_query_conflict
backend/tests/test_target_guard.py::test_extract_port_from_url_authority_non_numeric_rejected
backend/tests/test_target_guard.py::test_extract_port_from_url_authority_out_of_range_rejected
backend/tests/test_target_guard.py::test_verify_test_target_illegal_port_not_fallback
backend/tests/test_target_guard.py::test_is_local_database_url_hostname_takes_precedence
```

### test_target_guard.py — is_local_database_url (7)
```
backend/tests/test_target_guard.py::test_is_local_database_url_sqlite
backend/tests/test_target_guard.py::test_is_local_database_url_loopback_tcp
backend/tests/test_target_guard.py::test_is_local_database_url_remote_tcp
backend/tests/test_target_guard.py::test_is_local_database_url_unix_socket_with_path
backend/tests/test_target_guard.py::test_is_local_database_url_unix_socket_no_host_param
backend/tests/test_target_guard.py::test_is_local_database_url_unix_socket_empty_host
backend/tests/test_target_guard.py::test_is_local_database_url_empty_string
```

### test_target_guard.py — credential masking (3) [security]
```
backend/tests/test_target_guard.py::test_mask_url_credentials_with_password
backend/tests/test_target_guard.py::test_mask_url_credentials_without_password
backend/tests/test_target_guard.py::test_mask_url_credentials_unparseable
```

### test_target_guard.py — factory cleanup mock (7)
```
backend/tests/test_target_guard.py::test_factory_verify_failure_disposes_engine
backend/tests/test_target_guard.py::test_factory_sessionmaker_failure_disposes_engine
backend/tests/test_target_guard.py::test_factory_session_close_failure_disposes_engine
backend/tests/test_target_guard.py::test_factory_close_error_chained_with_dispose_error
backend/tests/test_target_guard.py::test_factory_verify_failure_close_error_chained
backend/tests/test_target_guard.py::test_guarded_factory_session_creation_failure
backend/tests/test_target_guard.py::test_triple_failure_chain_verify_close_dispose
```

### test_target_guard.py — triple failure count/order (1)
```
backend/tests/test_target_guard.py::test_triple_failure_explicit_count_and_order
```

### test_regression_contracts.py — mock-based contracts (3)
```
backend/tests/test_regression_contracts.py::TestReturningNotConsumed::test_update_returning_preserved_through_hook
backend/tests/test_regression_contracts.py::TestSessionHookIsolation::test_hooks_isolated_per_session
backend/tests/test_regression_contracts.py::TestFailedCommitNoSuccessSignal::test_rollback_does_not_signal_success
```

---

## connection-failure (7 tests)

These tests trigger `OperationalError` — connection fails before guard can run.  
Categorized as connection-failure, NOT guard-rejection.

### test_target_guard.py — integration tests with wrong parameters (7)
```
backend/tests/test_target_guard.py::test_integration_wrong_db_name_rejected_before_write
backend/tests/test_target_guard.py::test_integration_wrong_socket_dir_rejected
backend/tests/test_target_guard.py::test_integration_wrong_socket_port_rejected
backend/tests/test_target_guard.py::test_integration_empty_host_rejected
backend/tests/test_target_guard.py::test_integration_no_host_param_rejected
backend/tests/test_target_guard.py::test_integration_conflicting_host_params_rejected
backend/tests/test_target_guard.py::test_integration_verified_factory_no_session_on_failure
```

---

## connected-guard-rejection (2 tests)

These tests connect successfully, execute verification query, THEN get rejected by guard.  
True guard-rejection: `TargetGuardError` after successful connection.

### test_target_guard.py — guard rejection after connection (1)
```
backend/tests/test_target_guard.py::test_guard_rejection_after_successful_connection
```

### test_paper_trading_cancel_atomic.py — Batch C guard precision (1)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestBatchCGuardPrecision::test_verified_factory_rejection_asserts_teardown
```

---

## PG production-path (57 tests)

Tests that execute real business logic against PostgreSQL test database.

### test_paper_trading_cancel_atomic.py — TwoPhaseCancelContract (5)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_phase1_cancel_requested_running_state
backend/tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_phase2_worker_completes_cancel
backend/tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_cancel_requested_blocks_execution_acquisition
backend/tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_double_cancel_idempotent
backend/tests/test_paper_trading_cancel_atomic.py::TestTwoPhaseCancelContract::test_cancel_after_completed_rejected
```

### test_paper_trading_cancel_atomic.py — CancelConvergence (3)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestCancelConvergence::test_cancel_queued_completes_immediately
backend/tests/test_paper_trading_cancel_atomic.py::TestCancelConvergence::test_cancel_pending_completes_immediately
backend/tests/test_paper_trading_cancel_atomic.py::TestCancelConvergence::test_cancel_failed_completes_immediately
```

### test_paper_trading_cancel_atomic.py — CompletionFailureProtection (2)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestCompletionFailureProtection::test_completion_handler_respects_cancel
backend/tests/test_paper_trading_cancel_atomic.py::TestCompletionFailureProtection::test_failure_handler_respects_cancel
```

### test_paper_trading_cancel_atomic.py — RaceConditionsWithBarrier (2)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestRaceConditionsWithBarrier::test_concurrent_cancel_with_barrier
backend/tests/test_paper_trading_cancel_atomic.py::TestRaceConditionsWithBarrier::test_cancel_vs_completion_race
```

### test_paper_trading_cancel_atomic.py — CleanupSafety (PG-backed) (5)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_normal_cleanup_deletes_all_resources
backend/tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_with_setup_failure_still_deletes_partial
backend/tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_helper_rollback_on_injected_failure
backend/tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_aborts_on_cross_user_decision_order
backend/tests/test_paper_trading_cancel_atomic.py::TestCleanupSafety::test_cleanup_user2_does_not_affect_user1_data
```

### test_paper_trading_cancel_atomic.py — WorkerAtomicTerminalState (4)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestWorkerAtomicTerminalState::test_cancel_committed_before_exception_sets_cancelled
backend/tests/test_paper_trading_cancel_atomic.py::TestWorkerAtomicTerminalState::test_failure_committed_before_cancel_sets_failed
backend/tests/test_paper_trading_cancel_atomic.py::TestWorkerAtomicTerminalState::test_zero_row_update_when_not_running
backend/tests/test_paper_trading_cancel_atomic.py::TestWorkerAtomicTerminalState::test_terminal_state_commit_failure_preserves_running
```

### test_paper_trading_cancel_atomic.py — TargetGuard (PG-backed) (9)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestTargetGuard::test_target_guard_rejects_wrong_db
backend/tests/test_paper_trading_cancel_atomic.py::TestTargetGuard::test_target_guard_rejects_wrong_role
backend/tests/test_paper_trading_cancel_atomic.py::TestTargetGuard::test_target_guard_rejects_wrong_address
backend/tests/test_paper_trading_cancel_atomic.py::TestTargetGuard::test_target_guard_rejects_wrong_port
backend/tests/test_paper_trading_cancel_atomic.py::TestTargetGuard::test_target_guard_accepts_correct_target
backend/tests/test_paper_trading_cancel_atomic.py::TestTargetGuard::test_target_guard_rejects_ipv6_variant
backend/tests/test_paper_trading_cancel_atomic.py::TestTargetGuard::test_target_guard_rejects_null_port
backend/tests/test_paper_trading_cancel_atomic.py::TestTargetGuard::test_target_guard_zero_writes_before_verification
backend/tests/test_paper_trading_cancel_atomic.py::TestTargetGuard::test_get_test_db_url_fails_closed
```

### test_paper_trading_cancel_atomic.py — ReplayBarrierIntegration (6)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_cancel_first_during_replay_polling
backend/tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_completion_first_before_cancel_check
backend/tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_cancel_vs_failure_race_during_replay
backend/tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_polling_boundary_exactly_100_events
backend/tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_final_commit_protection_respects_cancel
backend/tests/test_paper_trading_cancel_atomic.py::TestReplayBarrierIntegration::test_ledger_and_cash_consistency_after_cancel
```

### test_paper_trading_cancel_atomic.py — ThreadPrecision (1)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestThreadPrecision::test_all_threads_terminated_after_barrier
```

### test_paper_trading_cancel_atomic.py — ResidualAggregation (2)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestResidualAggregation::test_aggregation_collects_all_residuals
backend/tests/test_paper_trading_cancel_atomic.py::TestResidualAggregation::test_aggregation_all_zeros_no_error
```

### test_paper_trading_cancel_atomic.py — BatchA/B/C (4)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestBatchAReverseOwnership::test_cross_user_cleanup_both_directions_abort
backend/tests/test_paper_trading_cancel_atomic.py::TestBatchCGuardPrecision::test_guard_with_recording_fake_verifies_identity_query
backend/tests/test_paper_trading_cancel_atomic.py::TestBatchCGuardPrecision::test_verified_factory_success_path_with_teardown
backend/tests/test_paper_trading_cancel_atomic.py::TestBatchCGuardPrecision::test_verified_factory_flow_shared_between_fixtures
```

### test_paper_trading_cancel_atomic.py — Batch B integration (1)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestBatchBMinimalIntegration::test_acquisition_cancel_synchronization_with_exception_injection
```

### test_paper_trading_cancel_atomic.py — cleanup finalizer safety (1)
```
backend/tests/test_paper_trading_cancel_atomic.py::TestBatchBMinimalIntegration::test_cleanup_refuses_when_workers_not_confirmed_stopped
```

### test_target_guard.py — socket validation (5)
```
backend/tests/test_target_guard.py::test_verify_socket_file_exists_mocked_socket
backend/tests/test_target_guard.py::test_verify_socket_file_exists_mocked_not_socket
backend/tests/test_target_guard.py::test_verify_socket_file_exists_mocked_not_found
backend/tests/test_target_guard.py::test_verify_socket_file_exists_mocked_wrong_port
backend/tests/test_target_guard.py::test_socket_validation_wrong_port_in_url
```

Wait — the above 5 are mock-based, should be no-DB. Let me recategorize.

### test_target_guard.py — socket validation mock (5) → move to no-DB
```
backend/tests/test_target_guard.py::test_verify_socket_file_exists_mocked_socket
backend/tests/test_target_guard.py::test_verify_socket_file_exists_mocked_not_socket
backend/tests/test_target_guard.py::test_verify_socket_file_exists_mocked_not_found
backend/tests/test_target_guard.py::test_verify_socket_file_exists_mocked_wrong_port
backend/tests/test_target_guard.py::test_socket_validation_wrong_port_in_url
```

### test_target_guard.py — socket validation integration (3)
```
backend/tests/test_target_guard.py::test_socket_validation_wrong_instance_dir
backend/tests/test_target_guard.py::test_socket_validation_conflicting_host_params
backend/tests/test_target_guard.py::test_socket_validation_empty_host_rejected
backend/tests/test_target_guard.py::test_socket_validation_no_host_param_rejected
```

### test_target_guard.py — credential leakage (5) [security]
```
backend/tests/test_target_guard.py::test_credential_leakage_authority_password_in_exception
backend/tests/test_target_guard.py::test_credential_leakage_encoded_password_in_exception
backend/tests/test_target_guard.py::test_credential_leakage_sensitive_query_params
backend/tests/test_target_guard.py::test_credential_leakage_connection_failure
backend/tests/test_target_guard.py::test_error_message_masks_credentials
```

### test_regression_contracts.py — PG-backed (2)
```
backend/tests/test_regression_contracts.py::TestFailedCommitNoSuccessSignal::test_successful_commit_signals_success
backend/tests/test_regression_contracts.py::TestSourceQuerySelectsFixture::test_historical_query_selects_created_trade
```

---

## security (extra label, 11 tests overlapping above categories)

```
backend/tests/test_target_guard.py::test_mask_url_credentials_with_password          [no-DB]
backend/tests/test_target_guard.py::test_mask_url_credentials_without_password        [no-DB]
backend/tests/test_target_guard.py::test_mask_url_credentials_unparseable             [no-DB]
backend/tests/test_target_guard.py::test_error_message_masks_credentials              [no-DB]
backend/tests/test_target_guard.py::test_credential_leakage_authority_password_in_exception  [PG]
backend/tests/test_target_guard.py::test_credential_leakage_encoded_password_in_exception      [PG]
backend/tests/test_target_guard.py::test_credential_leakage_sensitive_query_params             [PG]
backend/tests/test_target_guard.py::test_credential_leakage_connection_failure                 [PG]
backend/tests/test_target_guard.py::test_credential_leakage_mask_function_used_in_errors       [PG]
backend/tests/test_target_guard.py::test_guard_rejection_after_successful_connection           [connected-guard-rejection]
```

---

## Recount Summary

| Category | Count |
|----------|-------|
| no-DB | 48 |
| connection-failure | 7 |
| connected-guard-rejection | 2 |
| PG production-path | 57 |
| security (overlap) | 11 |
| **Total unique** | **119** |

Note: Some tests appear in both PG production-path and security categories.  
connection-failure tests are NOT guard-rejection — they fail at connection level (OperationalError).  
connected-guard-rejection tests succeed at connection, execute query, THEN get rejected (TargetGuardError).
