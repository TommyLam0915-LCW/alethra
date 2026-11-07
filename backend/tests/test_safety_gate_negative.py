"""
Negative tests for test_safety_gate.py

These tests prove the safety gate REJECTS invalid configurations,
not just warns about them.
"""

import os
import pytest
import sys
from unittest.mock import patch, MagicMock

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_safety_gate import (
    verify_test_database,
    TestDatabaseGateError,
    ALLOWED_TEST_DATABASES,
    FORBIDDEN_DATABASES,
)


class TestSafetyGateNegative:
    """Tests that prove the gate REJECTS invalid configurations."""
    
    def test_rejects_missing_test_mode(self):
        """Gate must reject when TEST_MODE is not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ['DATABASE_URL'] = 'postgresql://user:pass@localhost:5432/ai_investment_lab_test'
            # TEST_MODE not set
            
            with pytest.raises(TestDatabaseGateError, match="TEST_MODE must be 'true'"):
                verify_test_database()
    
    def test_rejects_wrong_test_mode(self):
        """Gate must reject when TEST_MODE is not exactly 'true'."""
        with patch.dict(os.environ, {'TEST_MODE': 'false', 'DATABASE_URL': 'postgresql://user:pass@localhost:5432/ai_investment_lab_test'}):
            with pytest.raises(TestDatabaseGateError, match="TEST_MODE must be 'true'"):
                verify_test_database()
        
        with patch.dict(os.environ, {'TEST_MODE': 'True', 'DATABASE_URL': 'postgresql://user:pass@localhost:5432/ai_investment_lab_test'}):
            with pytest.raises(TestDatabaseGateError, match="TEST_MODE must be 'true'"):
                verify_test_database()
        
        with patch.dict(os.environ, {'TEST_MODE': '1', 'DATABASE_URL': 'postgresql://user:pass@localhost:5432/ai_investment_lab_test'}):
            with pytest.raises(TestDatabaseGateError, match="TEST_MODE must be 'true'"):
                verify_test_database()
    
    def test_rejects_missing_database_url(self):
        """Gate must reject when DATABASE_URL is not set."""
        with patch.dict(os.environ, {'TEST_MODE': 'true'}, clear=True):
            # DATABASE_URL not set
            
            with pytest.raises(TestDatabaseGateError, match="DATABASE_URL not set"):
                verify_test_database()
    
    def test_rejects_production_database(self):
        """Gate must reject production database names."""
        for prod_db in FORBIDDEN_DATABASES:
            with patch.dict(os.environ, {
                'TEST_MODE': 'true',
                'DATABASE_URL': f'postgresql://user:pass@localhost:5432/{prod_db}'
            }):
                with pytest.raises(TestDatabaseGateError, match=f"FORBIDDEN.*{prod_db}"):
                    verify_test_database()
    
    def test_rejects_unknown_database(self):
        """Gate must reject databases not in whitelist."""
        with patch.dict(os.environ, {
            'TEST_MODE': 'true',
            'DATABASE_URL': 'postgresql://user:pass@localhost:5432/unknown_database'
        }):
            with pytest.raises(TestDatabaseGateError, match="REJECTED.*not in the test whitelist"):
                verify_test_database()
    
    def test_rejects_wrong_host(self):
        """Gate must reject connections to non-localhost hosts."""
        with patch.dict(os.environ, {
            'TEST_MODE': 'true',
            'DATABASE_URL': 'postgresql://user:pass@production.example.com:5432/ai_investment_lab_test'
        }):
            with pytest.raises(TestDatabaseGateError, match="REJECTED.*Host/port.*not in allowed instances"):
                verify_test_database()
    
    def test_rejects_wrong_port(self):
        """Gate must reject connections to non-standard ports."""
        with patch.dict(os.environ, {
            'TEST_MODE': 'true',
            'DATABASE_URL': 'postgresql://user:pass@localhost:5433/ai_investment_lab_test'
        }):
            with pytest.raises(TestDatabaseGateError, match="REJECTED.*Host/port.*not in allowed instances"):
                verify_test_database()
    
    def test_rejects_superuser(self):
        """Gate must reject connections as superuser."""
        with patch.dict(os.environ, {
            'TEST_MODE': 'true',
            'DATABASE_URL': 'postgresql://postgres:pass@localhost:5432/ai_investment_lab_test'
        }):
            # Mock psycopg2.connect to return a connection that reports superuser
            with patch('test_safety_gate.psycopg2.connect') as mock_connect:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
                
                # First query: connection info
                mock_cursor.fetchone.side_effect = [
                    ('ai_investment_lab_test', 'postgres', '127.0.0.1', 5432),
                    (True, False, False, False),  # rolsuper=True
                ]
                
                mock_connect.return_value = mock_conn
                
                with pytest.raises(TestDatabaseGateError, match="Connected as superuser"):
                    verify_test_database()
    
    def test_rejects_createdb_permission(self):
        """Gate must reject roles with CREATEDB permission."""
        with patch.dict(os.environ, {
            'TEST_MODE': 'true',
            'DATABASE_URL': 'postgresql://testuser:pass@localhost:5432/ai_investment_lab_test'
        }):
            with patch('test_safety_gate.psycopg2.connect') as mock_connect:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
                
                # First query: connection info
                # Second query: role permissions (rolcreatedb=True)
                mock_cursor.fetchone.side_effect = [
                    ('ai_investment_lab_test', 'testuser', '127.0.0.1', 5432),
                    (False, True, False, False),  # rolcreatedb=True
                ]
                
                mock_connect.return_value = mock_conn
                
                with pytest.raises(TestDatabaseGateError, match="has elevated permissions"):
                    verify_test_database()
    
    def test_rejects_createrole_permission(self):
        """Gate must reject roles with CREATEROLE permission."""
        with patch.dict(os.environ, {
            'TEST_MODE': 'true',
            'DATABASE_URL': 'postgresql://testuser:pass@localhost:5432/ai_investment_lab_test'
        }):
            with patch('test_safety_gate.psycopg2.connect') as mock_connect:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
                
                mock_cursor.fetchone.side_effect = [
                    ('ai_investment_lab_test', 'testuser', '127.0.0.1', 5432),
                    (False, False, True, False),  # rolcreaterole=True
                ]
                
                mock_connect.return_value = mock_conn
                
                with pytest.raises(TestDatabaseGateError, match="has elevated permissions"):
                    verify_test_database()
    
    def test_rejects_bypassrls_permission(self):
        """Gate must reject roles with BYPASSRLS permission."""
        with patch.dict(os.environ, {
            'TEST_MODE': 'true',
            'DATABASE_URL': 'postgresql://testuser:pass@localhost:5432/ai_investment_lab_test'
        }):
            with patch('test_safety_gate.psycopg2.connect') as mock_connect:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
                
                mock_cursor.fetchone.side_effect = [
                    ('ai_investment_lab_test', 'testuser', '127.0.0.1', 5432),
                    (False, False, False, True),  # rolbypassrls=True
                ]
                
                mock_connect.return_value = mock_conn
                
                with pytest.raises(TestDatabaseGateError, match="BYPASSRLS"):
                    verify_test_database()
    
    def test_rejects_database_mismatch(self):
        """Gate must reject when actual database doesn't match expected."""
        with patch.dict(os.environ, {
            'TEST_MODE': 'true',
            'DATABASE_URL': 'postgresql://testuser:pass@localhost:5432/ai_investment_lab_test'
        }):
            with patch('test_safety_gate.psycopg2.connect') as mock_connect:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
                
                # Actual database is different from expected
                mock_cursor.fetchone.side_effect = [
                    ('ai_investment_lab', 'testuser', '127.0.0.1', 5432),  # Wrong DB!
                    (False, False, False, False),
                ]
                
                mock_connect.return_value = mock_conn
                
                with pytest.raises(TestDatabaseGateError, match="Database mismatch"):
                    verify_test_database()
    
    def test_does_not_return_password(self):
        """Gate must never return password in connection info."""
        with patch.dict(os.environ, {
            'TEST_MODE': 'true',
            'DATABASE_URL': 'postgresql://testuser:secretpass@localhost:5432/ai_investment_lab_test'
        }):
            with patch('test_safety_gate.psycopg2.connect') as mock_connect:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
                
                mock_cursor.fetchone.side_effect = [
                    ('ai_investment_lab_test', 'testuser', '127.0.0.1', 5432),
                    (False, False, False, False),
                ]
                
                mock_connect.return_value = mock_conn
                
                result = verify_test_database()
                
                # Check that password is not in result
                assert 'password' not in result
                assert 'secretpass' not in str(result)
                assert 'secretpass' not in str(result.get('url_parsed', {}))


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
