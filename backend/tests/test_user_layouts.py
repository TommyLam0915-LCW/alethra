"""User layout API tests: workspace layout persistence roundtrip, size and
shape validation, auth, and per-user isolation."""
import json
from datetime import datetime

MAX_LAYOUT_BYTES = 65536


def second_user_headers(client, email="layout-second@alethra.io"):
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


def put_layout(client, headers, layout, expect=200):
    resp = client.put(
        "/api/v1/layouts/workspace", headers=headers, json={"layout": layout}
    )
    assert resp.status_code == expect, resp.text
    return resp


class TestAuthRequired:
    def test_layout_endpoints_require_auth(self, client):
        assert client.get("/api/v1/layouts/workspace").status_code == 401
        assert client.put(
            "/api/v1/layouts/workspace", json={"layout": {}}
        ).status_code == 401


class TestLayoutRoundtrip:
    def test_get_before_save_is_null(self, client, auth_headers):
        resp = client.get("/api/v1/layouts/workspace", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"layout": None, "updated_at": None}

    def test_put_then_get_roundtrip(self, client, auth_headers):
        layout = {
            "left": {
                "panel": "watchlist",
                "width": 280,
                "collapsed": False,
            },
            "right": {
                "tabs": ["chart", "research"],
                "activeTab": "chart",
            },
            "chartSymbols": ["US:AAPL", "HK:0700"],
            "split": 0.6,
            "nested": {"a": [1, 2, {"b": None}], "c": True},
        }
        resp = put_layout(client, auth_headers, layout)
        assert resp.status_code == 200
        body = resp.json()
        assert body["layout"] == layout
        assert body["updated_at"]
        datetime.fromisoformat(body["updated_at"])

        resp = client.get("/api/v1/layouts/workspace", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"layout": layout, "updated_at": body["updated_at"]}

    def test_put_upserts(self, client, auth_headers):
        put_layout(client, auth_headers, {"theme": "dark"})
        resp = put_layout(client, auth_headers, {"theme": "light", "extra": [1]})
        body = resp.json()
        assert body["layout"] == {"theme": "light", "extra": [1]}

        resp = client.get("/api/v1/layouts/workspace", headers=auth_headers)
        assert resp.json()["layout"] == {"theme": "light", "extra": [1]}

    def test_empty_object_layout(self, client, auth_headers):
        resp = put_layout(client, auth_headers, {})
        assert resp.status_code == 200
        assert resp.json()["layout"] == {}

        resp = client.get("/api/v1/layouts/workspace", headers=auth_headers)
        assert resp.json()["layout"] == {}

    def test_unicode_layout_roundtrip(self, client, auth_headers):
        layout = {"title": "我的工作區 — 觀察清單", "notes": "café ☕"}
        put_layout(client, auth_headers, layout)
        resp = client.get("/api/v1/layouts/workspace", headers=auth_headers)
        assert resp.json()["layout"] == layout


class TestLayoutValidation:
    def test_non_object_layout_list_rejected(self, client, auth_headers):
        resp = client.put(
            "/api/v1/layouts/workspace",
            headers=auth_headers,
            json={"layout": [1, 2, 3]},
        )
        assert 400 <= resp.status_code < 500
        assert resp.status_code != 413

    def test_non_object_layout_string_rejected(self, client, auth_headers):
        resp = client.put(
            "/api/v1/layouts/workspace",
            headers=auth_headers,
            json={"layout": "not-an-object"},
        )
        assert 400 <= resp.status_code < 500
        assert resp.status_code != 413

    def test_non_object_layout_number_rejected(self, client, auth_headers):
        resp = client.put(
            "/api/v1/layouts/workspace",
            headers=auth_headers,
            json={"layout": 42},
        )
        assert 400 <= resp.status_code < 500
        assert resp.status_code != 413

    def test_missing_layout_key_rejected(self, client, auth_headers):
        resp = client.put("/api/v1/layouts/workspace", headers=auth_headers, json={})
        assert 400 <= resp.status_code < 500

    def test_oversize_layout_413(self, client, auth_headers):
        layout = {"blob": "x" * (MAX_LAYOUT_BYTES + 100)}
        assert len(json.dumps(layout).encode("utf-8")) > MAX_LAYOUT_BYTES
        resp = put_layout(client, auth_headers, layout, expect=413)
        assert resp.json() == {"detail": "Layout too large"}

        # Nothing was persisted.
        resp = client.get("/api/v1/layouts/workspace", headers=auth_headers)
        assert resp.json() == {"layout": None, "updated_at": None}

    def test_layout_at_size_limit_accepted(self, client, auth_headers):
        # Tune the blob so the serialized JSON is exactly MAX_LAYOUT_BYTES.
        base = json.dumps({"blob": ""}).encode("utf-8")
        filler = "x" * (MAX_LAYOUT_BYTES - len(base))
        layout = {"blob": filler}
        assert len(json.dumps(layout).encode("utf-8")) == MAX_LAYOUT_BYTES
        resp = put_layout(client, auth_headers, layout)
        assert resp.status_code == 200
        assert resp.json()["layout"] == layout

    def test_oversize_unicode_layout_413(self, client, auth_headers):
        # 3 bytes per char: still over the byte cap even under the char count.
        layout = {"blob": "字" * (MAX_LAYOUT_BYTES // 2)}
        assert len(json.dumps(layout).encode("utf-8")) > MAX_LAYOUT_BYTES
        resp = put_layout(client, auth_headers, layout, expect=413)
        assert resp.json() == {"detail": "Layout too large"}


class TestLayoutOwnership:
    def test_layout_is_per_user(self, client, auth_headers):
        put_layout(client, auth_headers, {"mine": True})

        other = second_user_headers(client)
        resp = client.get("/api/v1/layouts/workspace", headers=other)
        assert resp.status_code == 200
        assert resp.json() == {"layout": None, "updated_at": None}

        put_layout(client, other, {"mine": False, "user": "second"})

        resp = client.get("/api/v1/layouts/workspace", headers=auth_headers)
        assert resp.json()["layout"] == {"mine": True}

        resp = client.get("/api/v1/layouts/workspace", headers=other)
        assert resp.json()["layout"] == {"mine": False, "user": "second"}
