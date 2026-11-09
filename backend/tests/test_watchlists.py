"""Watchlist API tests: CRUD, auth, ownership, ordering, reorder, and
the instruments `q` search used by the workspace symbol picker."""
import uuid

from fastapi.testclient import TestClient


def second_user_headers(client: TestClient, email="second@alethra.io"):
    resp = client.post("/api/auth/register", json={
        "email": email,
        "password": "testpass123",
        "display_name": "Second User",
        "user_type": "individual",
    })
    assert resp.status_code in (200, 201), resp.text
    login = client.post("/api/auth/login", json={
        "email": email,
        "password": "testpass123",
    })
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def create_watchlist(client, headers, name=None):
    body = {} if name is None else {"name": name}
    resp = client.post("/api/v1/watchlists", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["watchlist"]


def add_item(client, headers, watchlist_id, symbol, expect=201):
    resp = client.post(
        f"/api/v1/watchlists/{watchlist_id}/items",
        headers=headers,
        json={"symbol": symbol},
    )
    assert resp.status_code == expect, resp.text
    return resp


class TestAuthRequired:
    def test_watchlist_endpoints_require_auth(self, client):
        wl_id = str(uuid.uuid4())
        assert client.get("/api/v1/watchlists").status_code == 401
        assert client.post("/api/v1/watchlists", json={}).status_code == 401
        assert client.post(
            f"/api/v1/watchlists/{wl_id}/items", json={"symbol": "US:AAPL"}
        ).status_code == 401
        assert client.delete(f"/api/v1/watchlists/{wl_id}").status_code == 401
        assert client.delete(f"/api/v1/watchlists/{wl_id}/items/US:AAPL").status_code == 401
        assert client.post(
            f"/api/v1/watchlists/{wl_id}/reorder", json={"symbols": []}
        ).status_code == 401


class TestCreateAndList:
    def test_empty_list_initially(self, client, auth_headers):
        resp = client.get("/api/v1/watchlists", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"watchlists": []}

    def test_create_defaults_to_default_name(self, client, auth_headers):
        resp = client.post("/api/v1/watchlists", headers=auth_headers, json={})
        assert resp.status_code == 201
        wl = resp.json()["watchlist"]
        assert wl["name"] == "Default"
        assert wl["items"] == []
        assert wl["id"]
        assert wl["created_at"]
        assert wl["updated_at"]

    def test_create_without_body(self, client, auth_headers):
        resp = client.post("/api/v1/watchlists", headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["watchlist"]["name"] == "Default"

    def test_list_ordered_by_created_at(self, client, auth_headers):
        first = create_watchlist(client, auth_headers, "First")
        second = create_watchlist(client, auth_headers, "Second")
        resp = client.get("/api/v1/watchlists", headers=auth_headers)
        assert resp.status_code == 200
        names = [w["name"] for w in resp.json()["watchlists"]]
        assert names == ["First", "Second"]
        ids = [w["id"] for w in resp.json()["watchlists"]]
        assert ids == [first["id"], second["id"]]

    def test_created_watchlist_shape(self, client, auth_headers):
        create_watchlist(client, auth_headers, "Tech")
        resp = client.get("/api/v1/watchlists", headers=auth_headers)
        wl = resp.json()["watchlists"][0]
        assert set(wl.keys()) == {"id", "name", "created_at", "updated_at", "items"}
        uuid.UUID(wl["id"])  # parses as a UUID


class TestNameValidation:
    def test_name_over_100_chars_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/v1/watchlists", headers=auth_headers, json={"name": "x" * 101}
        )
        assert resp.status_code in (400, 422)

    def test_name_exactly_100_chars_accepted(self, client, auth_headers):
        resp = client.post(
            "/api/v1/watchlists", headers=auth_headers, json={"name": "x" * 100}
        )
        assert resp.status_code == 201
        assert resp.json()["watchlist"]["name"] == "x" * 100

    def test_empty_name_rejected(self, client, auth_headers):
        resp = client.post("/api/v1/watchlists", headers=auth_headers, json={"name": ""})
        assert resp.status_code in (400, 422)

    def test_whitespace_name_rejected(self, client, auth_headers):
        resp = client.post("/api/v1/watchlists", headers=auth_headers, json={"name": "   "})
        assert resp.status_code in (400, 422)

    def test_name_trimmed(self, client, auth_headers):
        resp = client.post(
            "/api/v1/watchlists", headers=auth_headers, json={"name": "  My List  "}
        )
        assert resp.status_code == 201
        assert resp.json()["watchlist"]["name"] == "My List"

    def test_duplicate_name_conflicts(self, client, auth_headers):
        create_watchlist(client, auth_headers, "Tech")
        resp = client.post("/api/v1/watchlists", headers=auth_headers, json={"name": "Tech"})
        assert resp.status_code == 409
        assert resp.json() == {"detail": "Watchlist name already exists"}

    def test_duplicate_default_name_conflicts(self, client, auth_headers):
        create_watchlist(client, auth_headers)
        resp = client.post("/api/v1/watchlists", headers=auth_headers, json={})
        assert resp.status_code == 409

    def test_same_name_ok_for_different_users(self, client, auth_headers):
        create_watchlist(client, auth_headers, "Shared")
        other = second_user_headers(client)
        resp = client.post("/api/v1/watchlists", headers=other, json={"name": "Shared"})
        assert resp.status_code == 201


class TestItems:
    def test_add_and_list_items_in_position_order(self, client, auth_headers):
        wl = create_watchlist(client, auth_headers, "Tech")
        add_item(client, auth_headers, wl["id"], "US:AAPL")
        add_item(client, auth_headers, wl["id"], "HK:0700")
        resp = add_item(client, auth_headers, wl["id"], "US:MSFT")
        body = resp.json()["watchlist"]
        assert [i["symbol"] for i in body["items"]] == ["US:AAPL", "HK:0700", "US:MSFT"]
        assert [i["position"] for i in body["items"]] == [0, 1, 2]
        assert all(i["added_at"] for i in body["items"])
        assert body["id"] == wl["id"]
        assert body["name"] == "Tech"

    def test_add_unknown_instrument_404(self, client, auth_headers):
        wl = create_watchlist(client, auth_headers)
        resp = client.post(
            f"/api/v1/watchlists/{wl['id']}/items",
            headers=auth_headers,
            json={"symbol": "XX:NOTREAL"},
        )
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Unknown instrument: XX:NOTREAL"}

    def test_add_symbol_without_market_prefix_404(self, client, auth_headers):
        wl = create_watchlist(client, auth_headers)
        resp = client.post(
            f"/api/v1/watchlists/{wl['id']}/items",
            headers=auth_headers,
            json={"symbol": "AAPL"},
        )
        assert resp.status_code == 404

    def test_add_is_idempotent(self, client, auth_headers):
        wl = create_watchlist(client, auth_headers)
        add_item(client, auth_headers, wl["id"], "US:AAPL")
        first = client.get("/api/v1/watchlists", headers=auth_headers).json()
        added_at_before = first["watchlists"][0]["items"][0]["added_at"]

        resp = client.post(
            f"/api/v1/watchlists/{wl['id']}/items",
            headers=auth_headers,
            json={"symbol": "US:AAPL"},
        )
        assert resp.status_code == 200
        items = resp.json()["watchlist"]["items"]
        assert len(items) == 1
        assert items[0]["position"] == 0
        assert items[0]["added_at"] == added_at_before

        listing = client.get("/api/v1/watchlists", headers=auth_headers).json()
        assert len(listing["watchlists"][0]["items"]) == 1

    def test_remove_compacts_positions(self, client, auth_headers):
        wl = create_watchlist(client, auth_headers)
        for symbol in ("US:AAPL", "HK:0700", "US:MSFT"):
            add_item(client, auth_headers, wl["id"], symbol)

        resp = client.delete(
            f"/api/v1/watchlists/{wl['id']}/items/US:AAPL", headers=auth_headers
        )
        assert resp.status_code == 200
        items = resp.json()["watchlist"]["items"]
        assert [i["symbol"] for i in items] == ["HK:0700", "US:MSFT"]
        assert [i["position"] for i in items] == [0, 1]

    def test_remove_middle_compacts_positions(self, client, auth_headers):
        wl = create_watchlist(client, auth_headers)
        for symbol in ("US:AAPL", "HK:0700", "US:MSFT"):
            add_item(client, auth_headers, wl["id"], symbol)

        resp = client.delete(
            f"/api/v1/watchlists/{wl['id']}/items/HK:0700", headers=auth_headers
        )
        assert resp.status_code == 200
        items = resp.json()["watchlist"]["items"]
        assert [i["symbol"] for i in items] == ["US:AAPL", "US:MSFT"]
        assert [i["position"] for i in items] == [0, 1]

    def test_remove_symbol_not_in_watchlist_404(self, client, auth_headers):
        wl = create_watchlist(client, auth_headers)
        add_item(client, auth_headers, wl["id"], "US:AAPL")
        resp = client.delete(
            f"/api/v1/watchlists/{wl['id']}/items/HK:0700", headers=auth_headers
        )
        assert resp.status_code == 404

    def test_add_to_unknown_watchlist_404(self, client, auth_headers):
        resp = client.post(
            f"/api/v1/watchlists/{uuid.uuid4()}/items",
            headers=auth_headers,
            json={"symbol": "US:AAPL"},
        )
        assert resp.status_code == 404


class TestReorder:
    def _setup(self, client, headers):
        wl = create_watchlist(client, headers)
        for symbol in ("US:AAPL", "HK:0700", "US:MSFT"):
            add_item(client, headers, wl["id"], symbol)
        return wl

    def test_valid_reorder(self, client, auth_headers):
        wl = self._setup(client, auth_headers)
        resp = client.post(
            f"/api/v1/watchlists/{wl['id']}/reorder",
            headers=auth_headers,
            json={"symbols": ["US:MSFT", "US:AAPL", "HK:0700"]},
        )
        assert resp.status_code == 200
        items = resp.json()["watchlist"]["items"]
        assert [i["symbol"] for i in items] == ["US:MSFT", "US:AAPL", "HK:0700"]
        assert [i["position"] for i in items] == [0, 1, 2]

        listing = client.get("/api/v1/watchlists", headers=auth_headers).json()
        assert [i["symbol"] for i in listing["watchlists"][0]["items"]] == [
            "US:MSFT", "US:AAPL", "HK:0700",
        ]

    def test_reorder_missing_symbol_400(self, client, auth_headers):
        wl = self._setup(client, auth_headers)
        resp = client.post(
            f"/api/v1/watchlists/{wl['id']}/reorder",
            headers=auth_headers,
            json={"symbols": ["US:AAPL", "HK:0700"]},
        )
        assert resp.status_code == 400

    def test_reorder_extra_symbol_400(self, client, auth_headers):
        wl = self._setup(client, auth_headers)
        resp = client.post(
            f"/api/v1/watchlists/{wl['id']}/reorder",
            headers=auth_headers,
            json={"symbols": ["US:AAPL", "HK:0700", "US:MSFT", "US:NVDA"]},
        )
        assert resp.status_code == 400

    def test_reorder_duplicate_symbol_400(self, client, auth_headers):
        wl = self._setup(client, auth_headers)
        resp = client.post(
            f"/api/v1/watchlists/{wl['id']}/reorder",
            headers=auth_headers,
            json={"symbols": ["US:AAPL", "US:AAPL", "HK:0700"]},
        )
        assert resp.status_code == 400

    def test_reorder_unknown_symbol_400(self, client, auth_headers):
        wl = self._setup(client, auth_headers)
        resp = client.post(
            f"/api/v1/watchlists/{wl['id']}/reorder",
            headers=auth_headers,
            json={"symbols": ["US:AAPL", "HK:0700", "XX:NOTREAL"]},
        )
        assert resp.status_code == 400

    def test_reorder_empty_watchlist_noop(self, client, auth_headers):
        wl = create_watchlist(client, auth_headers)
        resp = client.post(
            f"/api/v1/watchlists/{wl['id']}/reorder",
            headers=auth_headers,
            json={"symbols": []},
        )
        assert resp.status_code == 200
        assert resp.json()["watchlist"]["items"] == []

    def test_reorder_unknown_watchlist_404(self, client, auth_headers):
        resp = client.post(
            f"/api/v1/watchlists/{uuid.uuid4()}/reorder",
            headers=auth_headers,
            json={"symbols": []},
        )
        assert resp.status_code == 404


class TestDelete:
    def test_delete_returns_204_and_cascades_items(self, client, auth_headers):
        wl = create_watchlist(client, auth_headers)
        add_item(client, auth_headers, wl["id"], "US:AAPL")

        resp = client.delete(f"/api/v1/watchlists/{wl['id']}", headers=auth_headers)
        assert resp.status_code == 204
        assert resp.content == b""

        listing = client.get("/api/v1/watchlists", headers=auth_headers).json()
        assert listing == {"watchlists": []}

        # Items cascaded: the same name can be reused with no residue.
        wl2 = create_watchlist(client, auth_headers, wl["name"])
        resp = client.post(
            f"/api/v1/watchlists/{wl2['id']}/items",
            headers=auth_headers,
            json={"symbol": "US:AAPL"},
        )
        assert resp.status_code == 201
        assert len(resp.json()["watchlist"]["items"]) == 1

    def test_delete_unknown_watchlist_404(self, client, auth_headers):
        resp = client.delete(f"/api/v1/watchlists/{uuid.uuid4()}", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_twice_404(self, client, auth_headers):
        wl = create_watchlist(client, auth_headers)
        assert client.delete(
            f"/api/v1/watchlists/{wl['id']}", headers=auth_headers
        ).status_code == 204
        assert client.delete(
            f"/api/v1/watchlists/{wl['id']}", headers=auth_headers
        ).status_code == 404

    def test_items_rows_deleted_with_watchlist(self, client, auth_headers):
        from app.models.watchlist import WatchlistItem
        from tests.conftest import TestSession

        wl = create_watchlist(client, auth_headers)
        add_item(client, auth_headers, wl["id"], "US:AAPL")
        wl_uuid = uuid.UUID(wl["id"])

        def _item_rows():
            session = TestSession()
            try:
                return (
                    session.query(WatchlistItem)
                    .filter(WatchlistItem.watchlist_id == wl_uuid)
                    .count()
                )
            finally:
                session.close()

        assert _item_rows() == 1

        client.delete(f"/api/v1/watchlists/{wl['id']}", headers=auth_headers)

        assert _item_rows() == 0


class TestOwnershipIsolation:
    def _make_owned(self, client, headers):
        wl = create_watchlist(client, headers, "Mine")
        add_item(client, headers, wl["id"], "US:AAPL")
        return wl

    def test_other_user_cannot_see_watchlists(self, client, auth_headers):
        self._make_owned(client, auth_headers)
        other = second_user_headers(client)
        resp = client.get("/api/v1/watchlists", headers=other)
        assert resp.status_code == 200
        assert resp.json() == {"watchlists": []}

    def test_other_user_write_delete_404(self, client, auth_headers):
        wl = self._make_owned(client, auth_headers)
        other = second_user_headers(client)

        resp = client.post(
            f"/api/v1/watchlists/{wl['id']}/items",
            headers=other,
            json={"symbol": "US:MSFT"},
        )
        assert resp.status_code == 404

        resp = client.delete(
            f"/api/v1/watchlists/{wl['id']}/items/US:AAPL", headers=other
        )
        assert resp.status_code == 404

        resp = client.post(
            f"/api/v1/watchlists/{wl['id']}/reorder",
            headers=other,
            json={"symbols": ["US:AAPL"]},
        )
        assert resp.status_code == 404

        resp = client.delete(f"/api/v1/watchlists/{wl['id']}", headers=other)
        assert resp.status_code == 404

        # Owner's watchlist is untouched by all of the above.
        listing = client.get("/api/v1/watchlists", headers=auth_headers).json()
        assert len(listing["watchlists"]) == 1
        assert [i["symbol"] for i in listing["watchlists"][0]["items"]] == ["US:AAPL"]


class TestInstrumentSearch:
    """GET /instruments `q` + `limit` — used by the workspace symbol picker."""

    def _list(self, client, **params):
        resp = client.get("/api/v1/instruments", params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_no_auth_required(self, client):
        resp = client.get("/api/v1/instruments", params={"q": "app"})
        assert resp.status_code == 200

    def test_q_matches_by_name_case_insensitive(self, client):
        for q in ("app", "APP", "App"):
            body = self._list(client, q=q)
            symbols = [i["instrument_id"] for i in body["instruments"]]
            assert "US:AAPL" in symbols, f"q={q}: {symbols}"
            needle = q.lower()
            for inst in body["instruments"]:
                assert needle in inst["symbol"].lower() or needle in inst["name"].lower()

    def test_q_matches_by_symbol(self, client):
        body = self._list(client, q="aapl")
        symbols = [i["instrument_id"] for i in body["instruments"]]
        assert symbols == ["US:AAPL"]

    def test_q_case_insensitive(self, client):
        lower = self._list(client, q="aapl")
        upper = self._list(client, q="AAPL")
        assert [i["instrument_id"] for i in lower["instruments"]] == ["US:AAPL"]
        assert lower["instruments"] == upper["instruments"]

        by_name_lower = self._list(client, q="tencent")
        by_name_upper = self._list(client, q="TENCENT")
        assert by_name_lower["instruments"] == by_name_upper["instruments"]
        assert [i["instrument_id"] for i in by_name_lower["instruments"]] == ["HK:0700"]

    def test_q_no_match_returns_empty(self, client):
        body = self._list(client, q="zzzznotathing")
        assert body["instruments"] == []
        assert body["total"] == 0

    def test_q_combined_with_market_filter(self, client):
        body = self._list(client, q="tencent", market="HK")
        assert [i["instrument_id"] for i in body["instruments"]] == ["HK:0700"]

        body = self._list(client, q="tencent", market="US")
        assert body["instruments"] == []

    def test_q_combined_with_asset_type_filter(self, client):
        body = self._list(client, q="trust", asset_type="ETF")
        symbols = {i["instrument_id"] for i in body["instruments"]}
        assert "US:SPY" in symbols and "US:QQQ" in symbols
        for inst in body["instruments"]:
            assert inst["asset_type"] == "ETF"

        # Same query restricted to STOCKs: the only "trust" matches are ETFs.
        body = self._list(client, q="trust", asset_type="STOCK")
        assert body["instruments"] == []

    def test_q_market_and_asset_type_together(self, client):
        body = self._list(client, q="s&p", market="US", asset_type="ETF")
        assert [i["instrument_id"] for i in body["instruments"]] == ["US:SPY"]

        body = self._list(client, q="s&p", market="HK")
        assert body["instruments"] == []

    def test_limit_caps_results(self, client):
        body = self._list(client, q="a", limit=2)
        assert len(body["instruments"]) == 2
        assert body["total"] == 2
        for inst in body["instruments"]:
            assert "a" in inst["symbol"].lower() or "a" in inst["name"].lower()

    def test_limit_without_q(self, client):
        body = self._list(client, limit=5)
        assert len(body["instruments"]) == 5
        assert body["total"] == 5

    def test_limit_zero_422(self, client):
        resp = client.get("/api/v1/instruments", params={"limit": 0})
        assert resp.status_code == 422

    def test_limit_negative_422(self, client):
        resp = client.get("/api/v1/instruments", params={"limit": -3})
        assert resp.status_code == 422

    def test_response_shape_unchanged(self, client):
        body = self._list(client, q="apple")
        assert body["total"] == len(body["instruments"])
        inst = body["instruments"][0]
        for key in ("instrument_id", "symbol", "name", "market", "exchange",
                    "asset_type", "currency", "timezone", "lot_size"):
            assert key in inst
