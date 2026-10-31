"""A failed request must be reportable: one id, one classification, no internals.

The trial instance answered a database outage with a bare ``Internal Server
Error``. A customer could not tell their own bad request from a dead service,
and an operator could not tie the report to a log line. These tests pin the
replacement contract: a generated id on every response, 503 only for
recognised connection failures, 500 for anything unrecognised, and unchanged
4xx bodies so existing clients keep reading ``detail`` as before.
"""

import logging
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

HEX32 = re.compile(r"^[0-9a-f]{32}$")
SECRET_DSN = "postgresql+psycopg2://postgres:hunter2@db:5432/alethra"


def _operational_error(message: str) -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception(message))


def _fail_db_with(exc: BaseException):
    from app.main import app
    from app.database import get_db as orig_get_db

    def failing_db():
        raise exc
        yield  # pragma: no cover - unreachable, keeps this a generator

    return app, orig_get_db, failing_db


@pytest.fixture()
def raw_client():
    """A client that returns the error response instead of re-raising it."""
    from app.main import app
    from app.database import get_db as orig_get_db
    from tests.conftest import override_get_db

    app.dependency_overrides[orig_get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "exc",
    [
        _operational_error(f"could not connect {SECRET_DSN}"),
        InterfaceError("SELECT 1", {}, Exception(f"connection lost {SECRET_DSN}")),
        PoolTimeoutError("QueuePool limit reached for " + SECRET_DSN),
    ],
    ids=["operational", "interface", "pool-timeout"],
)
def test_recognised_connection_failures_are_503_with_an_id(
    client, auth_headers, monkeypatch, caplog, exc
):
    app, orig_get_db, failing_db = _fail_db_with(exc)
    monkeypatch.setitem(app.dependency_overrides, orig_get_db, failing_db)
    caplog.set_level(logging.ERROR, logger="app.request_errors")

    resp = client.get("/api/v1/experiments/", headers=auth_headers)

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["code"] == "database_unavailable"
    assert isinstance(body["detail"], str)
    assert HEX32.match(body["request_id"])
    assert body["request_id"] == resp.headers["x-request-id"]
    # No recovery time was measured, so no header may promise one.
    assert "retry-after" not in {key.lower() for key in resp.headers}

    text = resp.text
    for secret in ("hunter2", "postgresql", "SELECT 1", "QueuePool"):
        assert secret not in text

    assert any(
        body["request_id"] in record.getMessage()
        for record in caplog.records
        if record.name == "app.request_errors"
    ), "the response id must also appear in the server log line"


def test_unknown_exception_is_500_not_503_and_hides_internals(
    client, auth_headers, raw_client, monkeypatch
):
    app, orig_get_db, failing_db = _fail_db_with(
        ValueError(f"simulated crash /Users/owner/private/x.db {SECRET_DSN}")
    )
    monkeypatch.setitem(app.dependency_overrides, orig_get_db, failing_db)

    resp = raw_client.get("/api/v1/experiments/", headers=auth_headers)

    assert resp.status_code == 500, resp.text
    body = resp.json()
    assert body["code"] == "internal_error"
    assert HEX32.match(body["request_id"])
    assert body["request_id"] == resp.headers["x-request-id"]
    text = resp.text
    for secret in ("simulated crash", "/Users/", "hunter2", "postgresql", "ValueError"):
        assert secret not in text


def test_wrong_credentials_keep_the_existing_401_shape(client):
    resp = client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "wrongpass1"},
    )
    assert resp.status_code == 401
    # Additive correlation must not change what existing clients read.
    assert resp.json() == {"detail": "Incorrect email or password"}
    assert HEX32.match(resp.headers["x-request-id"])


def test_validation_errors_keep_the_existing_422_shape(client):
    resp = client.post("/api/auth/login", json={"email": "not-an-email"})
    assert resp.status_code == 422
    body = resp.json()
    assert set(body) == {"detail"}
    assert isinstance(body["detail"], list)
    assert HEX32.match(resp.headers["x-request-id"])


def test_every_response_carries_a_fresh_id_and_ignores_caller_input(client):
    first = client.get("/health")
    second = client.get("/health", headers={"X-Request-ID": "attacker-supplied"})

    first_id = first.headers["x-request-id"]
    second_id = second.headers["x-request-id"]
    assert HEX32.match(first_id) and HEX32.match(second_id)
    assert first_id != second_id
    # A caller-supplied id is never adopted: it could impersonate a log line.
    assert second_id != "attacker-supplied"
