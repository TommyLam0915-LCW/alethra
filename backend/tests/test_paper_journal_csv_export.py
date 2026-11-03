"""Tests for the owner-authorized paper-portfolio trade-journal CSV/JSON export.

Covers: authorization (no anonymous download, cross-user rejection), exact
column contract, per-column value verification via a real CSV parser, non-ASCII
/ quotes / commas / newlines in free text, spreadsheet formula-injection
guarding, closed vs open record classification, and that the JSON package
coexists with (is not replaced by) the CSV.
"""

import csv
import io
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.paper_portfolio import (
    JOURNAL_COLUMNS,
    _guard_formula,
    _journal_rows,
)
from app.models.paper_portfolio import OrderSide, OrderStatus, OrderType
from app.services.order_lifecycle import SETTLEMENT_POLICY_LABEL


BASE = "/api/v1/paper-portfolio"


def _register_and_login(client, email, password="testpass123"):
    client.post("/api/auth/register", json={
        "risk_acknowledged": True,
        "email": email, "password": password,
        "display_name": "CSV User", "user_type": "individual",
    })
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_portfolio(client, headers, cash=10000.0):
    resp = client.post(BASE, headers=headers, json={"initial_virtual_cash": cash, "currency": "USD"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _place_filled_buy(client, headers, portfolio_id, rationale="opening position"):
    resp = client.post(f"{BASE}/{portfolio_id}/orders", headers=headers, json={
        "asset_id": "DEMO-ALPHA",
        "side": "BUY",
        "quantity": 1,
        "order_type": "market",
        "rationale": rationale,
        "expected_outcome": "fill",
        "self_reported_confidence": "medium",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _parse_csv(content_bytes):
    text = content_bytes.decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    header = rows[0]
    return header, [dict(zip(header, r)) for r in rows[1:]], text


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

def test_csv_export_requires_authentication(client):
    pid = uuid4()
    resp = client.get(f"{BASE}/{pid}/journal?format=csv")
    assert resp.status_code in (401, 403)


def test_csv_export_cross_user_rejected(client, auth_headers):
    owner_pid = _create_portfolio(client, auth_headers)
    _place_filled_buy(client, auth_headers, owner_pid)

    other_headers = _register_and_login(client, "other-csv@alethra.io")
    resp = client.get(f"{BASE}/{owner_pid}/journal?format=csv", headers=other_headers)
    assert resp.status_code == 403


def test_json_export_cross_user_rejected(client, auth_headers):
    owner_pid = _create_portfolio(client, auth_headers)
    other_headers = _register_and_login(client, "other-json@alethra.io")
    resp = client.get(f"{BASE}/{owner_pid}/journal?format=json", headers=other_headers)
    assert resp.status_code == 403


def test_invalid_format_rejected(client, auth_headers):
    pid = _create_portfolio(client, auth_headers)
    resp = client.get(f"{BASE}/{pid}/journal?format=pdf", headers=auth_headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Column contract + per-column value verification
# ---------------------------------------------------------------------------

def test_csv_headers_and_content_type(client, auth_headers):
    pid = _create_portfolio(client, auth_headers)
    _place_filled_buy(client, auth_headers, pid)
    resp = client.get(f"{BASE}/{pid}/journal?format=csv", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    header, _, _ = _parse_csv(resp.content)
    assert header == JOURNAL_COLUMNS


def test_csv_per_column_values_for_a_fill(client, auth_headers):
    pid = _create_portfolio(client, auth_headers)
    _place_filled_buy(client, auth_headers, pid, rationale="verify each column")
    resp = client.get(f"{BASE}/{pid}/journal?format=csv", headers=auth_headers)
    _, rows, _ = _parse_csv(resp.content)

    fills = [r for r in rows if r["record_type"] == "fill"]
    assert len(fills) == 1
    row = fills[0]

    assert row["record_classification"] == "closed"
    # Two different facts on the same row: the order is finished, while the
    # position it opened is still open.
    assert row["order_state"] == "terminal"
    assert row["position_status"] == "open"
    assert row["portfolio_id"] == pid
    assert row["currency"] == "USD"
    assert row["asset_id"] == "DEMO-ALPHA"
    assert row["side"] == "BUY"
    assert row["order_type"] == "market"
    assert row["order_status"] == "filled"
    assert row["actor_type"] == "human"
    assert row["rationale"] == "verify each column"

    # Numeric columns parse as floats and are internally consistent:
    # gross = quantity * execution_price_before_costs (to stored rounding),
    # net_cash_change is negative on a BUY and equals -(gross + commission).
    quantity = float(row["quantity"])
    exec_price = float(row["execution_price_before_costs"])
    commission = float(row["commission_amount"])
    gross = float(row["gross_amount"])
    net = float(row["net_cash_change"])
    assert quantity == 1.0
    assert abs(gross - round(quantity * exec_price, 4)) < 0.01
    assert net < 0
    assert abs(net - round(-(gross + commission), 4)) < 0.01

    # Date column is a bare calendar date; timestamp column is ISO UTC-naive.
    assert len(row["execution_date"]) == 10 and row["execution_date"][4] == "-"
    assert "T" in row["order_created_at_utc"]


# ---------------------------------------------------------------------------
# Non-ASCII, quotes, commas, newlines round-trip through the CSV parser
# ---------------------------------------------------------------------------

def test_csv_preserves_chinese_quotes_commas_newlines(client, auth_headers):
    pid = _create_portfolio(client, auth_headers)
    tricky = '進場理由：價格「突破」，量能放大\n第二行 with "quotes" and, commas'
    _place_filled_buy(client, auth_headers, pid, rationale=tricky)
    resp = client.get(f"{BASE}/{pid}/journal?format=csv", headers=auth_headers)
    _, rows, _ = _parse_csv(resp.content)
    fill = next(r for r in rows if r["record_type"] == "fill")
    # A real CSV parser reconstructs the exact multi-line, quoted, non-ASCII value.
    assert fill["rationale"] == tricky


# ---------------------------------------------------------------------------
# Spreadsheet formula-injection guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "=1+1",
    "+1+1",
    "-1+1",
    "@SUM(A1)",
    "\t=cmd",
])
def test_guard_formula_neutralises_dangerous_leading_chars(payload):
    assert _guard_formula(payload).startswith("'")


def test_guard_formula_leaves_safe_text_alone():
    assert _guard_formula("進場 BUY") == "進場 BUY"
    assert _guard_formula("") == ""


def test_csv_formula_injection_is_prefixed_in_output(client, auth_headers):
    pid = _create_portfolio(client, auth_headers)
    _place_filled_buy(client, auth_headers, pid, rationale="=cmd|'/C calc'!A0")
    resp = client.get(f"{BASE}/{pid}/journal?format=csv", headers=auth_headers)
    _, rows, _ = _parse_csv(resp.content)
    fill = next(r for r in rows if r["record_type"] == "fill")
    # The parsed cell no longer begins with '=' — a spreadsheet reads it as text.
    assert fill["rationale"].startswith("'=")
    assert not fill["rationale"].lstrip("'").startswith("=") or fill["rationale"][0] == "'"


# ---------------------------------------------------------------------------
# Closed / open record classification (unit-level for the pending branch)
# ---------------------------------------------------------------------------

def test_pending_order_classifies_as_open_and_filled_as_closed():
    # An unbound portfolio has no dataset to read settlement from.
    portfolio = SimpleNamespace(
        id=uuid4(), currency="USD", simulation_session_id=None
    )

    pending = SimpleNamespace(
        id=uuid4(), decision_id=None, asset_id="DEMO-ALPHA",
        side=OrderSide.BUY, order_type=OrderType.market, status=OrderStatus.pending,
        actor_type="human", data_cutoff_at=None,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None), rationale=None, fills=[],
    )
    filled = SimpleNamespace(
        id=uuid4(), decision_id=None, asset_id="DEMO-ALPHA",
        side=OrderSide.BUY, order_type=OrderType.market, status=OrderStatus.filled,
        actor_type="human", data_cutoff_at=None,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None), rationale=None, fills=[],
    )
    cancelled = SimpleNamespace(
        id=uuid4(), decision_id=None, asset_id="DEMO-ALPHA",
        side=OrderSide.SELL, order_type=OrderType.limit, status=OrderStatus.cancelled,
        actor_type="system", data_cutoff_at=None,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None), rationale=None, fills=[],
    )

    from tests.conftest import TestSession

    db = TestSession()
    try:
        rows = _journal_rows(db, portfolio, [pending, filled, cancelled], {})
    finally:
        db.close()
    by_status = {r["order_status"].value: r for r in rows}
    assert by_status["pending"]["record_classification"] == "open"
    assert by_status["filled"]["record_classification"] == "closed"
    assert by_status["cancelled"]["record_classification"] == "closed"
    assert by_status["pending"]["order_state"] == "pending"
    assert by_status["filled"]["order_state"] == "terminal"
    assert by_status["cancelled"]["order_state"] == "terminal"
    # The SimpleNamespace portfolio has no position rows, so position status is
    # unknown (empty), not guessed as closed.
    assert all(r["position_status"] is None for r in rows)
    # An order with no fills still appears, typed as 'order', so nothing vanishes.
    assert all(r["record_type"] == "order" for r in rows)
    # No fills and no bound dataset ⇒ settlement is unknown, not an invented date.
    assert all(r["settlement_date"] is None for r in rows)
    assert all(r["settlement_policy"] == SETTLEMENT_POLICY_LABEL for r in rows)


# ---------------------------------------------------------------------------
# JSON package coexists with CSV (not replaced)
# ---------------------------------------------------------------------------

def test_json_format_returns_records_and_semantics(client, auth_headers):
    pid = _create_portfolio(client, auth_headers)
    _place_filled_buy(client, auth_headers, pid)
    resp = client.get(f"{BASE}/{pid}/journal?format=json", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["portfolio_id"] == pid
    assert body["columns"] == JOURNAL_COLUMNS
    assert body["row_count"] == len(body["records"]) >= 1
    assert "column_semantics" in body and "_formula_guard" in body["column_semantics"]
    assert "order_state" in body["column_semantics"]
    assert "position_status" in body["column_semantics"]
    assert any(r["record_type"] == "fill" for r in body["records"])


def test_csv_and_json_describe_the_same_rows(client, auth_headers):
    pid = _create_portfolio(client, auth_headers)
    _place_filled_buy(client, auth_headers, pid)
    csv_resp = client.get(f"{BASE}/{pid}/journal?format=csv", headers=auth_headers)
    json_resp = client.get(f"{BASE}/{pid}/journal?format=json", headers=auth_headers)
    _, csv_rows, _ = _parse_csv(csv_resp.content)
    json_rows = json_resp.json()["records"]
    assert len(csv_rows) == len(json_rows) == json_resp.json()["row_count"]
    assert {r["order_id"] for r in csv_rows} == {r["order_id"] for r in json_rows}
