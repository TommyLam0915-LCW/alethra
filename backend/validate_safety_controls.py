#!/usr/bin/env python3
"""Validate safety controls with PostgreSQL — concurrent sessions and processes."""
import sys
import uuid
import time
import threading
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "postgresql+psycopg2://postgres:testpass@localhost:54321/safety_test"

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def setup_test_data():
    """Create test user and API key."""
    db = Session()
    
    # Clean up any existing test data from previous runs
    db.execute(text("DELETE FROM rate_limit_counters WHERE identity_key IN ('safety-test-user', 'safety-test-user-2')"))
    db.execute(text("DELETE FROM kill_switches"))
    db.execute(text("DELETE FROM users WHERE email IN ('safety-test@alethra.io', 'safety-test-2@alethra.io')"))
    db.commit()
    db.close()
    
    db = Session()
    user_id = uuid.uuid4()
    db.execute(text("""
        INSERT INTO users (id, email, password_hash, display_name, user_type, 
                          created_at, updated_at, is_active, is_admin)
        VALUES (:id, :email, :hash, :name, :type, :now, :now, true, false)
    """), {
        "id": user_id,
        "email": "safety-test@alethra.io",
        "hash": "$2b$12$dummy",
        "name": "Safety Test",
        "type": "individual",
        "now": datetime.utcnow()
    })
    
    # Create a second user for isolation testing
    user_id_2 = uuid.uuid4()
    db.execute(text("""
        INSERT INTO users (id, email, password_hash, display_name, user_type, 
                          created_at, updated_at, is_active, is_admin)
        VALUES (:id, :email, :hash, :name, :type, :now, :now, true, false)
    """), {
        "id": user_id_2,
        "email": "safety-test-2@alethra.io",
        "hash": "$2b$12$dummy",
        "name": "Safety Test 2",
        "type": "individual",
        "now": datetime.utcnow()
    })
    
    db.commit()
    db.close()
    
    return user_id, user_id_2


def test_concurrent_rate_limit_creation(user_id):
    """Test that concurrent first requests create only one counter."""
    print("\n=== Test: Concurrent Rate Limit Creation ===")
    
    identity_key = f"user:{user_id}"
    route_category = "training"
    
    # Use fixed hourly window (same as the service)
    now = datetime.utcnow()
    window_start = now.replace(minute=0, second=0, microsecond=0)
    
    # Create 5 concurrent sessions
    sessions = [Session() for _ in range(5)]
    results = []
    errors = []
    
    def try_increment(session_idx):
        db = sessions[session_idx]
        try:
            result = db.execute(text("""
                INSERT INTO rate_limit_counters (id, identity_key, route_category, window_start, request_count, created_at, updated_at)
                VALUES (gen_random_uuid(), :identity_key, :route_category, :window_start, 1, :now, :now)
                ON CONFLICT (identity_key, route_category, window_start)
                DO UPDATE SET 
                    request_count = rate_limit_counters.request_count + 1,
                    updated_at = :now
                RETURNING request_count
            """), {
                "identity_key": identity_key,
                "route_category": route_category,
                "window_start": window_start,  # Use fixed window
                "now": now,
            })
            row = result.fetchone()
            results.append(row[0])
            db.commit()
        except Exception as e:
            errors.append(str(e))
            db.rollback()
    
    # Launch concurrent threads
    threads = [threading.Thread(target=try_increment, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # Verify only one counter was created
    db = Session()
    counter_count = db.execute(text("""
        SELECT COUNT(*) FROM rate_limit_counters
        WHERE identity_key = :identity_key AND route_category = :route_category
    """), {"identity_key": identity_key, "route_category": route_category}).scalar()
    
    total_requests = db.execute(text("""
        SELECT SUM(request_count) FROM rate_limit_counters
        WHERE identity_key = :identity_key AND route_category = :route_category
    """), {"identity_key": identity_key, "route_category": route_category}).scalar()
    
    db.close()
    
    print(f"  Counter rows created: {counter_count} (expected: 1)")
    print(f"  Total request count: {total_requests} (expected: 5)")
    print(f"  Errors: {len(errors)}")
    
    if counter_count == 1 and total_requests == 5 and len(errors) == 0:
        print("  ✓ PASS: Concurrent creation is safe")
        return True
    else:
        print("  ✗ FAIL: Concurrent creation has issues")
        return False


def test_rate_limit_threshold(user_id):
    """Test that rate limit enforces threshold correctly."""
    print("\n=== Test: Rate Limit Threshold ===")
    
    identity_key = f"user:{user_id}"
    route_category = "test_threshold"
    limit = 10
    
    # Use fixed hourly window
    now = datetime.utcnow()
    window_start = now.replace(minute=0, second=0, microsecond=0)
    
    db = Session()
    
    # Clear any existing counters for this test
    db.execute(text("""
        DELETE FROM rate_limit_counters
        WHERE identity_key = :identity_key AND route_category = :route_category
    """), {"identity_key": identity_key, "route_category": route_category})
    db.commit()
    
    # Make requests up to and beyond the limit
    allowed_count = 0
    rejected_count = 0
    
    for i in range(15):
        result = db.execute(text("""
            INSERT INTO rate_limit_counters (id, identity_key, route_category, window_start, request_count, created_at, updated_at)
            VALUES (gen_random_uuid(), :identity_key, :route_category, :window_start, 1, :now, :now)
            ON CONFLICT (identity_key, route_category, window_start)
            DO UPDATE SET 
                request_count = rate_limit_counters.request_count + 1,
                updated_at = :now
            RETURNING request_count
        """), {
            "identity_key": identity_key,
            "route_category": route_category,
            "window_start": window_start,
            "now": now,
        })
        
        row = result.fetchone()
        count = row[0]
        
        if count <= limit:
            allowed_count += 1
        else:
            rejected_count += 1
        
        db.commit()
    
    db.close()
    
    print(f"  Allowed requests: {allowed_count} (expected: {limit})")
    print(f"  Rejected requests: {rejected_count} (expected: 5)")
    
    if allowed_count == limit and rejected_count == 5:
        print("  ✓ PASS: Threshold enforced correctly")
        return True
    else:
        print("  ✗ FAIL: Threshold not enforced correctly")
        return False


def test_user_isolation(user_id_1, user_id_2):
    """Test that different users have separate counters."""
    print("\n=== Test: User Isolation ===")
    
    identity_key_1 = f"user:{user_id_1}"
    identity_key_2 = f"user:{user_id_2}"
    route_category = "test_isolation"
    
    # Use fixed hourly window
    now = datetime.utcnow()
    window_start = now.replace(minute=0, second=0, microsecond=0)
    
    db = Session()
    
    # Clear existing counters
    db.execute(text("""
        DELETE FROM rate_limit_counters
        WHERE identity_key IN (:key1, :key2) AND route_category = :route_category
    """), {"key1": identity_key_1, "key2": identity_key_2, "route_category": route_category})
    db.commit()
    
    # Make requests for user 1
    for _ in range(5):
        db.execute(text("""
            INSERT INTO rate_limit_counters (id, identity_key, route_category, window_start, request_count, created_at, updated_at)
            VALUES (gen_random_uuid(), :identity_key, :route_category, :window_start, 1, :now, :now)
            ON CONFLICT (identity_key, route_category, window_start)
            DO UPDATE SET request_count = rate_limit_counters.request_count + 1, updated_at = :now
        """), {
            "identity_key": identity_key_1,
            "route_category": route_category,
            "window_start": window_start,
            "now": now,
        })
        db.commit()
    
    # Make requests for user 2
    for _ in range(3):
        db.execute(text("""
            INSERT INTO rate_limit_counters (id, identity_key, route_category, window_start, request_count, created_at, updated_at)
            VALUES (gen_random_uuid(), :identity_key, :route_category, :window_start, 1, :now, :now)
            ON CONFLICT (identity_key, route_category, window_start)
            DO UPDATE SET request_count = rate_limit_counters.request_count + 1, updated_at = :now
        """), {
            "identity_key": identity_key_2,
            "route_category": route_category,
            "window_start": window_start,
            "now": now,
        })
        db.commit()
    
    # Verify separate counters
    count_1 = db.execute(text("""
        SELECT request_count FROM rate_limit_counters
        WHERE identity_key = :identity_key AND route_category = :route_category
    """), {"identity_key": identity_key_1, "route_category": route_category}).scalar()
    
    count_2 = db.execute(text("""
        SELECT request_count FROM rate_limit_counters
        WHERE identity_key = :identity_key AND route_category = :route_category
    """), {"identity_key": identity_key_2, "route_category": route_category}).scalar()
    
    db.close()
    
    print(f"  User 1 count: {count_1} (expected: 5)")
    print(f"  User 2 count: {count_2} (expected: 3)")
    
    if count_1 == 5 and count_2 == 3:
        print("  ✓ PASS: Users are isolated")
        return True
    else:
        print("  ✗ FAIL: Users are not isolated")
        return False


def test_kill_switch_visibility():
    """Test that kill switch activation is visible across sessions."""
    print("\n=== Test: Kill Switch Cross-Process Visibility ===")
    
    db1 = Session()
    db2 = Session()
    
    # Clear existing switches
    db1.execute(text("DELETE FROM kill_switches WHERE scope = 'training'"))
    db1.commit()
    
    # Activate kill switch in session 1
    db1.execute(text("""
        INSERT INTO kill_switches (id, scope, is_active, activated_at, activated_by, reason, created_at, updated_at)
        VALUES (gen_random_uuid(), 'training', true, :now, 'test-user', 'test', :now, :now)
    """), {"now": datetime.utcnow()})
    db1.commit()
    
    # Check from session 2
    is_active = db2.execute(text("""
        SELECT is_active FROM kill_switches WHERE scope = 'training'
    """)).scalar()
    
    print(f"  Kill switch active in session 2: {is_active} (expected: True)")
    
    # Deactivate in session 2
    db2.execute(text("""
        UPDATE kill_switches SET is_active = false, updated_at = :now WHERE scope = 'training'
    """), {"now": datetime.utcnow()})
    db2.commit()
    
    # Check from session 1
    is_active_after = db1.execute(text("""
        SELECT is_active FROM kill_switches WHERE scope = 'training'
    """)).scalar()
    
    print(f"  Kill switch active in session 1 after deactivation: {is_active_after} (expected: False)")
    
    db1.close()
    db2.close()
    
    if is_active == True and is_active_after == False:
        print("  ✓ PASS: Kill switch visible across sessions")
        return True
    else:
        print("  ✗ FAIL: Kill switch not visible across sessions")
        return False


def test_transaction_rollback():
    """Test that rate limit counter rolls back with transaction."""
    print("\n=== Test: Transaction Rollback Semantics ===")
    
    identity_key = "user:test-rollback"
    route_category = "test_rollback"
    
    db = Session()
    
    # Clear existing counters
    db.execute(text("""
        DELETE FROM rate_limit_counters
        WHERE identity_key = :identity_key AND route_category = :route_category
    """), {"identity_key": identity_key, "route_category": route_category})
    db.commit()
    
    # Increment counter but rollback
    db.execute(text("""
        INSERT INTO rate_limit_counters (id, identity_key, route_category, window_start, request_count, created_at, updated_at)
        VALUES (gen_random_uuid(), :identity_key, :route_category, :window_start, 1, :now, :now)
        ON CONFLICT (identity_key, route_category, window_start)
        DO UPDATE SET request_count = rate_limit_counters.request_count + 1, updated_at = :now
    """), {
        "identity_key": identity_key,
        "route_category": route_category,
        "window_start": datetime.utcnow() - timedelta(minutes=60),
        "now": datetime.utcnow(),
    })
    db.rollback()  # Rollback instead of commit
    
    # Check from a fresh session
    db2 = Session()
    count = db2.execute(text("""
        SELECT request_count FROM rate_limit_counters
        WHERE identity_key = :identity_key AND route_category = :route_category
    """), {"identity_key": identity_key, "route_category": route_category}).scalar()
    
    db.close()
    db2.close()
    
    print(f"  Counter after rollback: {count} (expected: None or 0)")
    
    if count is None or count == 0:
        print("  ✓ PASS: Rollback works correctly")
        return True
    else:
        print("  ✗ FAIL: Rollback did not work")
        return False


def main():
    print("=" * 70)
    print("Safety Controls PostgreSQL Validation")
    print("=" * 70)
    
    print("\nSetting up test data...")
    user_id, user_id_2 = setup_test_data()
    print(f"✓ Created test users")
    
    all_passed = True
    
    all_passed &= test_concurrent_rate_limit_creation(user_id)
    all_passed &= test_rate_limit_threshold(user_id)
    all_passed &= test_user_isolation(user_id, user_id_2)
    all_passed &= test_kill_switch_visibility()
    all_passed &= test_transaction_rollback()
    
    print("\n" + "=" * 70)
    if all_passed:
        print("✓ ALL TESTS PASSED")
        print("=" * 70)
        return 0
    else:
        print("✗ SOME TESTS FAILED")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
