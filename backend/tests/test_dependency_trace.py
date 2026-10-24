"""Trace HTTP dependency chain to identify database mismatch."""
import pytest
from sqlalchemy import text
from datetime import date, datetime
from uuid import uuid4


class TestDependencyTrace:
    """Trace the actual dependency chain used by HTTP requests."""
    
    def test_trace_http_dependency(self, client, auth_headers, setup_db):
        """Trace which get_db is actually used by HTTP requests."""
        from tests.conftest import TestSession, TEST_ENGINE, override_get_db
        from app.database import get_db as app_get_db
        from app.main import app
        
        print("\n=== HTTP Dependency Trace ===")
        
        # Step 1: Check dependency override registration
        print("\n--- Step 1: Dependency Override Registration ---")
        print(f"app.dependency_overrides keys: {list(app.dependency_overrides.keys())}")
        
        for key, value in app.dependency_overrides.items():
            print(f"\nOverride key: {key}")
            print(f"  Module: {key.__module__}")
            print(f"  Name: {key.__name__}")
            print(f"  File: {key.__code__.co_filename if hasattr(key, '__code__') else 'N/A'}")
            print(f"\nOverride value: {value}")
            print(f"  Module: {value.__module__}")
            print(f"  Name: {value.__name__}")
            print(f"  File: {value.__code__.co_filename if hasattr(value, '__code__') else 'N/A'}")
            print(f"  Same object? {key is value}")
        
        # Step 2: Check app_get_db identity
        print("\n--- Step 2: app.database.get_db Identity ---")
        print(f"app_get_db: {app_get_db}")
        print(f"  Module: {app_get_db.__module__}")
        print(f"  Name: {app_get_db.__name__}")
        
        # Step 3: Check TEST_ENGINE identity
        print("\n--- Step 3: TEST_ENGINE Identity ---")
        print(f"TEST_ENGINE: {TEST_ENGINE}")
        print(f"  URL: {TEST_ENGINE.url}")
        print(f"  Pool: {TEST_ENGINE.pool}")
        print(f"  Pool class: {TEST_ENGINE.pool.__class__.__name__}")
        
        # Step 4: Check TestSession bind
        print("\n--- Step 4: TestSession Bind ---")
        print(f"TestSession: {TestSession}")
        print(f"  Bind: {TestSession.kw.get('bind')}")
        print(f"  Bind is TEST_ENGINE? {TestSession.kw.get('bind') is TEST_ENGINE}")
        
        # Step 5: Create test data via HTTP and trace the session
        print("\n--- Step 5: HTTP Request with Session Trace ---")
        
        # Instrument override_get_db to trace what it yields
        original_override = override_get_db
        
        traced_sessions = []
        
        def traced_override_get_db():
            print("  [TRACE] override_get_db() called")
            db = TestSession()
            print(f"  [TRACE] Created TestSession: {db}")
            print(f"  [TRACE] Session bind: {db.bind}")
            print(f"  [TRACE] Session bind is TEST_ENGINE? {db.bind is TEST_ENGINE}")
            traced_sessions.append(db)
            try:
                yield db
            finally:
                print(f"  [TRACE] Closing TestSession: {db}")
                db.close()
        
        # Temporarily replace the override
        app.dependency_overrides[app_get_db] = traced_override_get_db
        
        try:
            # Make HTTP request
            resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
                "title": "Dependency Trace Test",
                "research_type": "quantitative",
                "strategy_spec": {
                    "template_type": "buy_and_hold",
                    "asset_ids": ["US:AAPL"],
                    "start_date": "2024-01-02",
                    "end_date": "2024-01-31",
                    "initial_cash": 100000,
                }
            })
            
            if resp.status_code != 201:
                print(f"✗ HTTP request failed: {resp.status_code} - {resp.text}")
                return
            
            exp_id = resp.json()["id"]
            print(f"\n✓ HTTP created experiment: {exp_id}")
            print(f"  Traced sessions: {len(traced_sessions)}")
            
            if traced_sessions:
                http_session = traced_sessions[0]
                print(f"  HTTP session bind: {http_session.bind}")
                print(f"  HTTP session bind URL: {http_session.bind.url if http_session.bind else 'None'}")
        
        finally:
            # Restore original override
            app.dependency_overrides[app_get_db] = original_override
        
        # Step 6: Try to read via fresh TestSession
        print("\n--- Step 6: Fresh TestSession Read ---")
        fresh_session = TestSession()
        try:
            print(f"Fresh session: {fresh_session}")
            print(f"Fresh session bind: {fresh_session.bind}")
            print(f"Fresh session bind is TEST_ENGINE? {fresh_session.bind is TEST_ENGINE}")
            
            # Try ORM query first
            from app.models import Experiment
            from uuid import UUID as UUID_Type
            exp_id_uuid = UUID_Type(exp_id) if isinstance(exp_id, str) else exp_id
            result = fresh_session.query(Experiment).filter(Experiment.id == exp_id_uuid).first()
            if result:
                print(f"✓ ORM query found experiment: {result.id}, {result.title}")
            else:
                print(f"✗ ORM query did NOT find experiment {exp_id}")
            
            # Try raw SQL
            result = fresh_session.execute(text("""
                SELECT id, title FROM experiments WHERE id = :exp_id
            """), {"exp_id": exp_id})
            row = result.fetchone()
            if row:
                print(f"✓ Raw SQL found experiment: {row[0]}, {row[1]}")
            else:
                print(f"✗ Raw SQL did NOT find experiment {exp_id}")
        
        finally:
            fresh_session.close()
        
        # Step 7: Check database_list from both perspectives
        print("\n--- Step 7: Database List Comparison ---")
        
        # From HTTP session perspective (if we still have it)
        if traced_sessions and not traced_sessions[0].is_closed:
            print("From HTTP session:")
            try:
                result = traced_sessions[0].execute(text("PRAGMA database_list"))
                for row in result.fetchall():
                    print(f"  {row[0]}: {row[1]} = {row[2]}")
            except Exception as e:
                print(f"  Error: {e}")
        
        # From fresh session
        print("From fresh session:")
        fresh_session = TestSession()
        try:
            result = fresh_session.execute(text("PRAGMA database_list"))
            for row in result.fetchall():
                print(f"  {row[0]}: {row[1]} = {row[2]}")
        finally:
            fresh_session.close()
        
        print("\n=== Trace Complete ===")
