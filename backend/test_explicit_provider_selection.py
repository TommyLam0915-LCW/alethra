"""Test explicit provider selection for stored-data sessions."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import date, datetime
from uuid import UUID

from app.database import Base, get_db
from app.main import app
from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar
from app.models.simulation import SimulationSession
from app.auth.jwt import create_access_token

# Create test database
engine = create_engine('sqlite:///:memory:')
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine)

def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

def setup_test_data():
    """Set up test data with multiple providers."""
    db = TestSession()
    
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

def test_available_providers():
    """Test GET /available-providers endpoint."""
    print("\n=== Test 1: Get available providers ===")
    
    token = create_access_token({'sub': 'test-user-id'})
    headers = {'Authorization': f'Bearer {token}'}
    
    response = client.get(
        '/api/v1/simulations/versions/test-version-id/available-providers',
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
    assert providers['demo']['bar_count'] == 4
    
    print(f"✓ Found {len(data['available_providers'])} providers")
    print(f"  - twelvedata: {providers['twelvedata']['bar_count']} bars")
    print(f"  - demo: {providers['demo']['bar_count']} bars")

def test_create_session_with_explicit_provider():
    """Test POST /versions/{version_id} with explicit provider_id."""
    print("\n=== Test 2: Create session with explicit provider ===")
    
    token = create_access_token({'sub': 'test-user-id'})
    headers = {'Authorization': f'Bearer {token}'}
    
    response = client.post(
        '/api/v1/simulations/versions/test-version-id',
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

def test_create_session_without_provider_multiple_available():
    """Test that creating session without provider_id fails when multiple providers available."""
    print("\n=== Test 3: Create session without provider (should fail) ===")
    
    token = create_access_token({'sub': 'test-user-id'})
    headers = {'Authorization': f'Bearer {token}'}
    
    response = client.post(
        '/api/v1/simulations/versions/test-version-id',
        headers=headers,
        json={
            'initial_virtual_cash': 10000.0,
        },
    )
    
    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
    data = response.json()
    
    assert 'Multiple providers' in data['detail']
    
    print(f"✓ Correctly rejected: {data['detail']}")

def test_create_session_with_invalid_provider():
    """Test that creating session with invalid provider_id fails."""
    print("\n=== Test 4: Create session with invalid provider (should fail) ===")
    
    token = create_access_token({'sub': 'test-user-id'})
    headers = {'Authorization': f'Bearer {token}'}
    
    response = client.post(
        '/api/v1/simulations/versions/test-version-id',
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

def test_create_session_with_demo_provider():
    """Test creating session with demo provider."""
    print("\n=== Test 5: Create session with demo provider ===")
    
    token = create_access_token({'sub': 'test-user-id'})
    headers = {'Authorization': f'Bearer {token}'}
    
    response = client.post(
        '/api/v1/simulations/versions/test-version-id',
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

def cleanup():
    """Clean up test database."""
    db = TestSession()
    db.query(SimulationSession).delete()
    db.query(MarketDataBar).delete()
    db.query(ExperimentVersion).delete()
    db.query(Experiment).delete()
    db.query(User).delete()
    db.commit()
    db.close()

if __name__ == '__main__':
    try:
        setup_test_data()
        test_available_providers()
        test_create_session_with_explicit_provider()
        test_create_session_without_provider_multiple_available()
        test_create_session_with_invalid_provider()
        test_create_session_with_demo_provider()
        print("\n✅ All provider selection tests passed!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cleanup()
