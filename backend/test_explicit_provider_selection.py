"""Test explicit provider selection for stored-data sessions."""
import sys
from contextlib import contextmanager
from datetime import date, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
# Import the model package so every table (including api_keys, touched by the
# request path) is registered on Base.metadata before create_all runs. Bind it
# under another name so it does not shadow the FastAPI `app` imported above.
from app import models as _models  # noqa: F401
from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar
from app.models.simulation import SimulationSession
from app.auth.jwt import create_access_token

# The seeded fixture rows above are addressed by these ids; auth now
# resolves the token subject as a real user.
TEST_USER_ID = UUID('00000000-0000-0000-0000-000000000001')
TEST_VERSION_ID = UUID('00000000-0000-0000-0000-000000000003')


def _override_for(test_session):
    def override_get_db():
        db = test_session()
        try:
            yield db
        finally:
            db.close()
    return override_get_db


@contextmanager
def _provider_selection_stack():
    """Seeded in-memory database plus the get_db override, both torn down here.

    The engine, `app.dependency_overrides[get_db]` and the TestClient used to be
    built at module import and never removed. Because this file sorts first in
    the lane's collection order, every later `TestClient(app)` in the same
    process resolved `get_db` against this module's in-memory database instead
    of the lane database - which is how 18 tests in tests/test_ml_pipeline.py
    came to fail only in full-suite order.

    A plain in-memory SQLite URL gives every connection its own empty database,
    so requests served on TestClient's thread would not see the tables;
    StaticPool keeps one shared connection for the whole stack.
    """
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine)
    setup_test_data(test_session)
    saved_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = _override_for(test_session)
    try:
        yield TestClient(app), test_session
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved_overrides)
        engine.dispose()


@pytest.fixture(scope='module')
def client():
    with _provider_selection_stack() as (_client, _session):
        yield _client

def setup_test_data(test_session):
    """Set up test data with multiple providers."""
    db = test_session()
    
    # Create user
    user = User(
        id=UUID('00000000-0000-0000-0000-000000000001'),
        email='test@example.com',
        password_hash='hashed',
        display_name='Test User',
    )
    db.add(user)
    
    # Create experiment
    experiment = Experiment(
        id=UUID('00000000-0000-0000-0000-000000000002'),
        owner_id=UUID('00000000-0000-0000-0000-000000000001'),
        title='Test Experiment',
        status='locked',
    )
    db.add(experiment)
    
    # Create experiment version
    version = ExperimentVersion(
        id=UUID('00000000-0000-0000-0000-000000000003'),
        experiment_id=UUID('00000000-0000-0000-0000-000000000002'),
        version_number=1,
        created_by=UUID('00000000-0000-0000-0000-000000000001'),
        strategy_spec={
            'schema_version': '1.0',
            'template_type': 'buy_and_hold',
            'asset_ids': ['US:AAPL'],
            'frequency': '1d',
            'start_date': '2024-01-02',
            'end_date': '2024-01-30',
            'initial_cash': 10000.0,
            'signal_parameters': {},
            'entry_rule': {},
            'exit_rule': {},
            'sizing_rule': {},
            'execution_timing': {'execute_at': 'next_open'},
            'commission_rate': 0.0,
            'slippage_rate': 0.0,
            'benchmark_id': '',
            'currency': 'USD',
        },
        locked_at=date(2024, 1, 1),
        locked_by=UUID('00000000-0000-0000-0000-000000000001'),
    )
    db.add(version)
    
    # Add Twelve Data bars
    for i in range(20):
        bar_date = date(2024, 1, 2 + i)
        bar = MarketDataBar(
            instrument_id='US:AAPL',
            bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
            bar_date=bar_date,
            open=180.0 + i * 0.5,
            high=181.0 + i * 0.5,
            low=179.0 + i * 0.5,
            close=180.5 + i * 0.5,
            volume=50000000,
            provider_id='twelvedata',
        )
        db.add(bar)
    
    # Add Demo bars (fewer, different dates)
    demo_dates = [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 2)]
    for i, bar_date in enumerate(demo_dates):
        bar = MarketDataBar(
            instrument_id='US:AAPL',
            bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
            bar_date=bar_date,
            open=190.0 + i,
            high=191.0 + i,
            low=189.0 + i,
            close=190.5 + i,
            volume=1000000,
            provider_id='demo',
        )
        db.add(bar)
    
    db.commit()
    db.close()

def test_available_providers(client):
    """Test GET /available-providers endpoint."""
    print("\n=== Test 1: Get available providers ===")
    
    token = create_access_token({'sub': str(TEST_USER_ID)})
    headers = {'Authorization': f'Bearer {token}'}
    
    response = client.get(
        f'/api/v1/simulations/versions/{TEST_VERSION_ID}/available-providers',
        headers=headers,
    )
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    
    assert data['symbol'] == 'US:AAPL'
    assert len(data['available_providers']) == 2
    
    providers = {p['provider_id']: p for p in data['available_providers']}
    assert 'twelvedata' in providers
    assert 'demo' in providers
    assert providers['twelvedata']['bar_count'] == 20
    # Only the 2024-01-30 demo bar falls inside the version window
    # (2024-01-02..2024-01-30); the other three are outside it.
    assert providers['demo']['bar_count'] == 1
    
    print(f"✓ Found {len(data['available_providers'])} providers")
    print(f"  - twelvedata: {providers['twelvedata']['bar_count']} bars")
    print(f"  - demo: {providers['demo']['bar_count']} bars")

def test_create_session_with_explicit_provider(client):
    """Test POST /versions/{version_id} with explicit provider_id."""
    print("\n=== Test 2: Create session with explicit provider ===")
    
    token = create_access_token({'sub': str(TEST_USER_ID)})
    headers = {'Authorization': f'Bearer {token}'}
    
    response = client.post(
        f'/api/v1/simulations/versions/{TEST_VERSION_ID}',
        headers=headers,
        json={
            'initial_virtual_cash': 10000.0,
            'provider_id': 'twelvedata',
        },
    )
    
    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"
    data = response.json()
    
    assert data['provider_id'] == 'twelvedata'
    assert data['status'] == 'pending'
    
    print(f"✓ Session created with provider_id={data['provider_id']}")

def test_create_session_without_provider_multiple_available(client):
    """Test that creating session without provider_id fails when multiple providers available."""
    print("\n=== Test 3: Create session without provider (should fail) ===")
    
    token = create_access_token({'sub': str(TEST_USER_ID)})
    headers = {'Authorization': f'Bearer {token}'}
    
    response = client.post(
        f'/api/v1/simulations/versions/{TEST_VERSION_ID}',
        headers=headers,
        json={
            'initial_virtual_cash': 10000.0,
        },
    )
    
    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
    data = response.json()
    
    assert 'Multiple providers' in data['detail']
    
    print(f"✓ Correctly rejected: {data['detail']}")

def test_create_session_with_invalid_provider(client):
    """Test that creating session with invalid provider_id fails."""
    print("\n=== Test 4: Create session with invalid provider (should fail) ===")
    
    token = create_access_token({'sub': str(TEST_USER_ID)})
    headers = {'Authorization': f'Bearer {token}'}
    
    response = client.post(
        f'/api/v1/simulations/versions/{TEST_VERSION_ID}',
        headers=headers,
        json={
            'initial_virtual_cash': 10000.0,
            'provider_id': 'nonexistent',
        },
    )
    
    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
    data = response.json()
    
    assert 'has no stored data' in data['detail']
    
    print(f"✓ Correctly rejected: {data['detail']}")

def test_create_session_with_demo_provider(client):
    """Test creating session with demo provider."""
    print("\n=== Test 5: Create session with demo provider ===")
    
    token = create_access_token({'sub': str(TEST_USER_ID)})
    headers = {'Authorization': f'Bearer {token}'}
    
    response = client.post(
        f'/api/v1/simulations/versions/{TEST_VERSION_ID}',
        headers=headers,
        json={
            'initial_virtual_cash': 10000.0,
            'provider_id': 'demo',
        },
    )
    
    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"
    data = response.json()
    
    assert data['provider_id'] == 'demo'
    
    print(f"✓ Session created with provider_id={data['provider_id']}")

def cleanup(test_session):
    """Clean up test database."""
    db = test_session()
    db.query(SimulationSession).delete()
    db.query(MarketDataBar).delete()
    db.query(ExperimentVersion).delete()
    db.query(Experiment).delete()
    db.query(User).delete()
    db.commit()
    db.close()

if __name__ == '__main__':
    with _provider_selection_stack() as (client, test_session):
        try:
            test_available_providers(client)
            test_create_session_with_explicit_provider(client)
            test_create_session_without_provider_multiple_available(client)
            test_create_session_with_invalid_provider(client)
            test_create_session_with_demo_provider(client)
            print("\n✅ All provider selection tests passed!")
        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        finally:
            cleanup(test_session)
