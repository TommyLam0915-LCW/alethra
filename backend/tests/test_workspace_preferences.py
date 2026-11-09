"""Tests for workspace preferences endpoints."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.user import User


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client):
    resp = client.post(
        "/api/auth/register",
        json={
            "email": "test@example.com",
            "password": "testpass123",
            "display_name": "Test User",
        },
    )
    assert resp.status_code == 201
    resp = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def other_user_headers(client):
    resp = client.post(
        "/api/auth/register",
        json={
            "email": "other@example.com",
            "password": "testpass123",
            "display_name": "Other User",
        },
    )
    assert resp.status_code == 201
    resp = client.post(
        "/api/auth/login",
        json={"email": "other@example.com", "password": "testpass123"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestWorkspacePreferencesAuth:
    def test_get_unauthenticated(self, client):
        resp = client.get("/api/v1/layouts/workspace_preferences")
        assert resp.status_code == 401

    def test_put_unauthenticated(self, client):
        resp = client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": {"default_symbol": "US:AAPL"}},
        )
        assert resp.status_code == 401


class TestWorkspacePreferencesCRUD:
    def test_get_empty(self, client, auth_headers):
        resp = client.get(
            "/api/v1/layouts/workspace_preferences",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["preferences"] is None
        assert data["updated_at"] is None

    def test_save_and_readback(self, client, auth_headers):
        prefs = {"default_symbol": "US:AAPL", "default_range": "6M"}
        resp = client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": prefs},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["preferences"] == prefs
        assert data["updated_at"] is not None

        resp = client.get(
            "/api/v1/layouts/workspace_preferences",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["preferences"] == prefs

    def test_update_overwrites(self, client, auth_headers):
        prefs1 = {"default_symbol": "US:AAPL"}
        client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": prefs1},
            headers=auth_headers,
        )

        prefs2 = {"default_symbol": "US:MSFT", "default_range": "1Y"}
        resp = client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": prefs2},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["preferences"] == prefs2

        resp = client.get(
            "/api/v1/layouts/workspace_preferences",
            headers=auth_headers,
        )
        assert resp.json()["preferences"] == prefs2

    def test_reset_to_empty(self, client, auth_headers):
        prefs = {"default_symbol": "US:AAPL"}
        client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": prefs},
            headers=auth_headers,
        )

        resp = client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": {}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["preferences"] == {}

        resp = client.get(
            "/api/v1/layouts/workspace_preferences",
            headers=auth_headers,
        )
        assert resp.json()["preferences"] == {}


class TestWorkspacePreferencesIsolation:
    def test_user_isolation(self, client, auth_headers, other_user_headers):
        prefs_a = {"default_symbol": "US:AAPL"}
        client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": prefs_a},
            headers=auth_headers,
        )

        prefs_b = {"default_symbol": "US:MSFT"}
        client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": prefs_b},
            headers=other_user_headers,
        )

        resp_a = client.get(
            "/api/v1/layouts/workspace_preferences",
            headers=auth_headers,
        )
        assert resp_a.json()["preferences"] == prefs_a

        resp_b = client.get(
            "/api/v1/layouts/workspace_preferences",
            headers=other_user_headers,
        )
        assert resp_b.json()["preferences"] == prefs_b


class TestWorkspacePreferencesValidation:
    def test_payload_too_large(self, client, auth_headers):
        large_payload = {"x": "a" * 70000}
        resp = client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": large_payload},
            headers=auth_headers,
        )
        assert resp.status_code == 413

    def test_unknown_fields_accepted(self, client, auth_headers):
        prefs = {"default_symbol": "US:AAPL", "unknown_field": "value"}
        resp = client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": prefs},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["preferences"] == prefs

    def test_invalid_json_rejected(self, client, auth_headers):
        resp = client.put(
            "/api/v1/layouts/workspace_preferences",
            content=b"not json",
            headers={**auth_headers, "Content-Type": "application/json"},
        )
        assert resp.status_code == 422


class TestWorkspacePreferencesCompatibility:
    def test_does_not_affect_workspace_layout(self, client, auth_headers):
        layout = {"symbol": "US:AAPL", "range": "6M", "panels": {}}
        client.put(
            "/api/v1/layouts/workspace",
            json={"layout": layout},
            headers=auth_headers,
        )

        prefs = {"default_symbol": "US:MSFT"}
        client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": prefs},
            headers=auth_headers,
        )

        resp = client.get("/api/v1/layouts/workspace", headers=auth_headers)
        assert resp.json()["layout"] == layout

    def test_workspace_layout_does_not_affect_preferences(self, client, auth_headers):
        prefs = {"default_symbol": "US:MSFT"}
        client.put(
            "/api/v1/layouts/workspace_preferences",
            json={"layout": prefs},
            headers=auth_headers,
        )

        layout = {"symbol": "US:AAPL", "range": "6M", "panels": {}}
        client.put(
            "/api/v1/layouts/workspace",
            json={"layout": layout},
            headers=auth_headers,
        )

        resp = client.get(
            "/api/v1/layouts/workspace_preferences",
            headers=auth_headers,
        )
        assert resp.json()["preferences"] == prefs
