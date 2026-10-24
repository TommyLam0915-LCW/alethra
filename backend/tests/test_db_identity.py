"""Diagnostic test to verify HTTP and SQL use the same database."""
import pytest
from sqlalchemy import text
from datetime import date, datetime
from uuid import uuid4
from app.models.market_data import MarketDataBar


class TestDatabaseIdentity:
    """Diagnose whether HTTP requests and SQL queries access the same database."""
    
    def test_http_sql_same_database(self, client, auth_headers, setup_db):
        """Prove HTTP-created data is visible to direct SQL queries."""
        from tests.conftest import TestSession, TEST_ENGINE
        
        print("\n=== Database Identity Diagnostic ===")
        
        # Diagnostic 1: Check engine identity
        print(f"\nTEST_ENGINE URL: {TEST_ENGINE.url}")
        print(f"TEST_ENGINE pool class: {TEST_ENGINE.pool.__class__.__name__}")
        
        # Diagnostic 2: Create data via HTTP
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "DB Identity Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["US:AAPL"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 100000,
            }
        })
        assert resp.status_code == 201, f"HTTP creation failed: {resp.text}"
        exp_id = resp.json()["id"]
        print(f"\nHTTP created experiment: {exp_id}")
        
        # Diagnostic 3: Try to read via TestSession
        print("\n--- Attempt 1: Read via TestSession() ---")
        db_session = TestSession()
        try:
            result = db_session.execute(text("""
                SELECT id, title FROM experiments WHERE id = :exp_id
            """), {"exp_id": exp_id})
            row = result.fetchone()
            if row:
                print(f"✓ TestSession can see HTTP data: {row[0]}, {row[1]}")
            else:
                print("✗ TestSession CANNOT see HTTP data")
        finally:
            db_session.close()
        
        # Diagnostic 4: Try to read via TEST_ENGINE.connect()
        print("\n--- Attempt 2: Read via TEST_ENGINE.connect() ---")
        with TEST_ENGINE.connect() as conn:
            result = conn.execute(text("""
                SELECT id, title FROM experiments WHERE id = :exp_id
            """), {"exp_id": exp_id})
            row = result.fetchone()
            if row:
                print(f"✓ TEST_ENGINE.connect() can see HTTP data: {row[0]}, {row[1]}")
            else:
                print("✗ TEST_ENGINE.connect() CANNOT see HTTP data")
        
        # Diagnostic 5: Check SQLite database_list
        print("\n--- Attempt 3: Check SQLite database_list ---")
        with TEST_ENGINE.connect() as conn:
            result = conn.execute(text("PRAGMA database_list"))
            rows = result.fetchall()
            for row in rows:
                print(f"  Database {row[0]}: {row[1]} = {row[2]}")
        
        # Diagnostic 6: Write via TestSession, read via HTTP
        print("\n--- Attempt 4: Write via TestSession, verify via HTTP ---")
        marker_id = f"marker_{uuid4().hex[:8]}"
        db_session = TestSession()
        try:
            # Insert a marker experiment directly
            db_session.execute(text("""
                INSERT INTO experiments (id, title, research_type, user_id, created_at, updated_at)
                VALUES (:id, :title, :type, :user_id, datetime('now'), datetime('now'))
            """), {
                "id": marker_id,
                "title": "SQL Marker",
                "type": "quantitative",
                "user_id": auth_headers["Authorization"].split(" ")[1].split(".")[1]  # Extract user ID from JWT (approximate)
            })
            db_session.commit()
            print(f"TestSession inserted marker: {marker_id}")
        except Exception as e:
            print(f"TestSession insert failed: {e}")
            db_session.rollback()
        finally:
            db_session.close()
        
        # Try to read marker via HTTP
        resp = client.get("/api/v1/experiments/", headers=auth_headers)
        if resp.status_code == 200:
            experiments = resp.json()
            marker_found = any(exp["id"] == marker_id for exp in experiments)
            if marker_found:
                print(f"✓ HTTP can see TestSession data: {marker_id}")
            else:
                print(f"✗ HTTP CANNOT see TestSession data (marker {marker_id} not found)")
                print(f"  HTTP returned {len(experiments)} experiments")
        else:
            print(f"✗ HTTP GET failed: {resp.status_code}")
        
        print("\n=== Diagnostic Complete ===")
