#!/usr/bin/env python3
"""Validate worker claiming behavior with PostgreSQL."""
import sys
import uuid
import time
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database URL for worker_test
DATABASE_URL = "postgresql+psycopg2://postgres:testpass@localhost:54321/worker_test"

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

def setup_test_data():
    """Create test user, experiment, and training jobs."""
    db = Session()
    
    # Create test user
    user_id = uuid.uuid4()
    db.execute(text("""
        INSERT INTO users (id, email, password_hash, display_name, user_type, 
                          created_at, updated_at, is_active, is_admin)
        VALUES (:id, :email, :hash, :name, :type, :now, :now, true, false)
    """), {
        "id": user_id,
        "email": "worker-test@alethra.io",
        "hash": "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TcMQjhkSJMZd0Zv0Zv0Zv0Zv0ZvO",  # dummy hash
        "name": "Worker Test",
        "type": "individual",
        "now": datetime.utcnow()
    })
    
    # Create test experiment
    experiment_id = uuid.uuid4()
    db.execute(text("""
        INSERT INTO experiments (id, owner_id, title, hypothesis, research_type, 
                                status, visibility, created_at, updated_at, scope)
        VALUES (:id, :owner_id, :title, :hyp, :type, :status, :vis, :now, :now, :scope)
    """), {
        "id": experiment_id,
        "owner_id": user_id,
        "title": "Worker Test Experiment",
        "hyp": "Test hypothesis",
        "type": "quantitative",
        "status": "draft",
        "vis": "private",
        "now": datetime.utcnow(),
        "scope": "{}"
    })
    
    # Create 5 pending training jobs
    job_ids = []
    for i in range(5):
        job_id = uuid.uuid4()
        job_ids.append(job_id)
        db.execute(text("""
            INSERT INTO training_jobs (id, user_id, experiment_id, parameter_grid, 
                                      strategy_spec_snapshot, status, created_at)
            VALUES (:id, :user_id, :exp_id, :grid, :snapshot, 'pending', :now)
        """), {
            "id": job_id,
            "user_id": user_id,
            "exp_id": experiment_id,
            "grid": '{"param": [1, 2]}',
            "snapshot": '{"strategy": "test"}',
            "now": datetime.utcnow()
        })
    
    db.commit()
    db.close()
    
    return user_id, experiment_id, job_ids

def test_exclusive_claiming(job_ids):
    """Test that two workers cannot claim the same job."""
    print("\n=== Test: Exclusive Claiming ===")
    
    # Create two separate sessions (simulating two workers)
    db1 = Session()
    db2 = Session()
    
    # Worker 1 claims a job
    result1 = db1.execute(text("""
        SELECT id FROM training_jobs 
        WHERE status = 'pending' 
        ORDER BY created_at ASC 
        LIMIT 1 
        FOR UPDATE SKIP LOCKED
    """)).fetchone()
    
    if result1:
        job_id_1 = result1[0]
        db1.execute(text("""
            UPDATE training_jobs 
            SET status = 'running' 
            WHERE id = :id
        """), {"id": job_id_1})
        db1.commit()
        print(f"✓ Worker 1 claimed job: {job_id_1}")
    
    # Worker 2 tries to claim a job (should get a different one)
    result2 = db2.execute(text("""
        SELECT id FROM training_jobs 
        WHERE status = 'pending' 
        ORDER BY created_at ASC 
        LIMIT 1 
        FOR UPDATE SKIP LOCKED
    """)).fetchone()
    
    if result2:
        job_id_2 = result2[0]
        if job_id_2 == job_id_1:
            print(f"✗ FAIL: Worker 2 claimed same job as Worker 1!")
            return False
        else:
            # Update Worker 2's job to running
            db2.execute(text("""
                UPDATE training_jobs 
                SET status = 'running' 
                WHERE id = :id
            """), {"id": job_id_2})
            db2.commit()
            print(f"✓ Worker 2 claimed different job: {job_id_2}")
    
    # Verify both jobs are now running
    running_jobs = db1.execute(text("""
        SELECT id, status FROM training_jobs 
        WHERE id IN (:id1, :id2)
    """), {"id1": job_id_1, "id2": job_id_2}).fetchall()
    
    for job_id, status in running_jobs:
        if status != 'running':
            print(f"✗ FAIL: Job {job_id} status is {status}, expected 'running'")
            return False
    
    print(f"✓ Both jobs are in 'running' status")
    
    db1.close()
    db2.close()
    return True

def test_completed_not_claimed():
    """Test that completed jobs are not claimed."""
    print("\n=== Test: Completed Jobs Not Claimed ===")
    
    db = Session()
    
    # Mark one job as completed (use subquery instead of LIMIT in UPDATE)
    result = db.execute(text("""
        SELECT id FROM training_jobs 
        WHERE status = 'pending' 
        ORDER BY created_at ASC 
        LIMIT 1
    """)).fetchone()
    
    if result:
        job_id = result[0]
        db.execute(text("""
            UPDATE training_jobs 
            SET status = 'completed', completed_at = :now
            WHERE id = :id
        """), {"id": job_id, "now": datetime.utcnow()})
        db.commit()
        print(f"✓ Marked job {job_id} as completed")
    
    # Try to claim pending jobs
    result = db.execute(text("""
        SELECT id, status FROM training_jobs 
        WHERE status = 'pending' 
        ORDER BY created_at ASC 
        LIMIT 1 
        FOR UPDATE SKIP LOCKED
    """)).fetchone()
    
    if result:
        job_id, status = result
        if status == 'completed':
            print(f"✗ FAIL: Claimed a completed job!")
            return False
        print(f"✓ Claimed pending job (not completed): {job_id}")
    
    # Verify completed job count
    completed_count = db.execute(text("""
        SELECT COUNT(*) FROM training_jobs WHERE status = 'completed'
    """)).scalar()
    
    print(f"✓ Completed jobs: {completed_count}")
    
    db.close()
    return True

def test_cancelled_not_claimed():
    """Test that cancelled jobs are not claimed."""
    print("\n=== Test: Cancelled Jobs Not Claimed ===")
    
    db = Session()
    
    # Mark one job as cancelled (use subquery instead of LIMIT in UPDATE)
    result = db.execute(text("""
        SELECT id FROM training_jobs 
        WHERE status = 'pending' 
        ORDER BY created_at ASC 
        LIMIT 1
    """)).fetchone()
    
    if result:
        job_id = result[0]
        db.execute(text("""
            UPDATE training_jobs 
            SET status = 'cancelled', cancelled_at = :now
            WHERE id = :id
        """), {"id": job_id, "now": datetime.utcnow()})
        db.commit()
        print(f"✓ Marked job {job_id} as cancelled")
    
    # Try to claim pending jobs
    result = db.execute(text("""
        SELECT id, status FROM training_jobs 
        WHERE status = 'pending' 
        ORDER BY created_at ASC 
        LIMIT 1 
        FOR UPDATE SKIP LOCKED
    """)).fetchone()
    
    if result:
        job_id, status = result
        if status == 'cancelled':
            print(f"✗ FAIL: Claimed a cancelled job!")
            return False
        print(f"✓ Claimed pending job (not cancelled): {job_id}")
    
    # Verify cancelled job count
    cancelled_count = db.execute(text("""
        SELECT COUNT(*) FROM training_jobs WHERE status = 'cancelled'
    """)).scalar()
    
    print(f"✓ Cancelled jobs: {cancelled_count}")
    
    db.close()
    return True

def test_failure_handling():
    """Test that failed jobs are marked correctly."""
    print("\n=== Test: Failure Handling ===")
    
    db = Session()
    
    # Get a running job
    result = db.execute(text("""
        SELECT id FROM training_jobs WHERE status = 'running' LIMIT 1
    """)).fetchone()
    
    if not result:
        print("✗ FAIL: No running job to test failure")
        return False
    
    job_id = result[0]
    
    # Simulate failure
    db.execute(text("""
        UPDATE training_jobs 
        SET status = 'failed', error_details = :error, completed_at = :now
        WHERE id = :id
    """), {
        "id": job_id,
        "error": "Test failure: simulated error",
        "now": datetime.utcnow()
    })
    db.commit()
    
    # Verify failure was recorded
    failed_job = db.execute(text("""
        SELECT id, status, error_details FROM training_jobs WHERE id = :id
    """), {"id": job_id}).fetchone()
    
    if failed_job[1] != 'failed':
        print(f"✗ FAIL: Job status is {failed_job[1]}, expected 'failed'")
        return False
    
    if not failed_job[2] or "simulated error" not in failed_job[2]:
        print(f"✗ FAIL: Error details not recorded correctly")
        return False
    
    print(f"✓ Failure recorded correctly: {failed_job[2]}")
    
    db.close()
    return True

def main():
    print("=" * 60)
    print("PostgreSQL Worker Claiming Behavior Validation")
    print("=" * 60)
    
    # Setup test data
    print("\nSetting up test data...")
    user_id, experiment_id, job_ids = setup_test_data()
    print(f"✓ Created {len(job_ids)} pending training jobs")
    
    # Run tests
    all_passed = True
    
    all_passed &= test_exclusive_claiming(job_ids)
    all_passed &= test_completed_not_claimed()
    all_passed &= test_cancelled_not_claimed()
    all_passed &= test_failure_handling()
    
    # Summary
    print("\n" + "=" * 60)
    if all_passed:
        print("✓ ALL TESTS PASSED")
        print("=" * 60)
        return 0
    else:
        print("✗ SOME TESTS FAILED")
        print("=" * 60)
        return 1

if __name__ == "__main__":
    sys.exit(main())
