"""Feedback API tests: submit, read back, validation, ownership, and the
promise that nothing is collected beyond what the form sent.

The /feedback page is only allowed to say "submitted" after these endpoints
return the stored row, so the read-back assertions here are what the UI's
success message rests on.
"""
import uuid

from app.api.feedback import (
    FEEDBACK_CATEGORIES,
    MAX_ERROR_REFERENCE,
    MAX_EXPECTED,
    MAX_PAGE,
    MAX_STEPS,
)

BASE = "/api/v1/feedback"


def payload(**overrides):
    body = {
        "category": "research_results",
        "page": "Experiment results",
        "steps": "1. Opened the run. 2. Clicked Compare.",
        "expected": "A diff table.",
        "actual": "Nothing rendered until I reloaded.",
        "error_reference": "ERR-42",
    }
    body.update(overrides)
    return body


def second_user(client, email):
    resp = client.post("/api/auth/register", json={
        "risk_acknowledged": True,
        "email": email,
        "password": "testpass123",
        "display_name": email.split("@")[0],
        "user_type": "individual",
    })
    assert resp.status_code in (200, 201), resp.text
    login = client.post("/api/auth/login", json={"email": email, "password": "testpass123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def current_user_id(client, headers):
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    return me.json()["id"]


class TestAuthRequired:
    def test_all_feedback_endpoints_require_auth(self, client):
        assert client.post(BASE, json=payload()).status_code == 401
        assert client.get(BASE).status_code == 401
        assert client.get(f"{BASE}/{uuid.uuid4()}").status_code == 401


class TestSubmitAndReadBack:
    def test_submit_returns_the_stored_row(self, client, auth_headers):
        resp = client.post(BASE, headers=auth_headers, json=payload())
        assert resp.status_code == 201, resp.text
        row = resp.json()
        assert row["id"]
        assert row["created_at"]
        assert row["category"] == "research_results"
        assert row["page"] == "Experiment results"
        assert row["steps"] == "1. Opened the run. 2. Clicked Compare."
        assert row["expected"] == "A diff table."
        assert row["actual"] == "Nothing rendered until I reloaded."
        assert row["error_reference"] == "ERR-42"
        assert row["submitted_by"] == str(current_user_id(client, auth_headers))

    def test_submitted_row_is_read_back_by_id_and_in_the_list(self, client, auth_headers):
        created = client.post(BASE, headers=auth_headers, json=payload()).json()

        by_id = client.get(f"{BASE}/{created['id']}", headers=auth_headers)
        assert by_id.status_code == 200, by_id.text
        assert by_id.json() == created

        listing = client.get(BASE, headers=auth_headers)
        assert listing.status_code == 200, listing.text
        assert [row["id"] for row in listing.json()] == [created["id"]]

    def test_list_is_newest_first(self, client, auth_headers, monkeypatch):
        # The clock is stepped so "newest first" cannot depend on two writes
        # landing in the same microsecond; the ids are random UUIDs, so an
        # unchanged timestamp order would be decided by the id tiebreak alone.
        import app.models.user_feedback as feedback_model
        from datetime import datetime, timedelta

        start = datetime(2026, 11, 15, 12, 0, 0)
        ticks = iter(start + timedelta(seconds=n) for n in range(100))
        monkeypatch.setattr(feedback_model, "utcnow", lambda: next(ticks))

        first = client.post(BASE, headers=auth_headers, json=payload(steps="first")).json()
        second = client.post(BASE, headers=auth_headers, json=payload(steps="second")).json()

        listing = client.get(BASE, headers=auth_headers).json()
        assert [row["id"] for row in listing] == [second["id"], first["id"]]
        assert first["created_at"] < second["created_at"]

    def test_optional_fields_may_be_omitted(self, client, auth_headers):
        resp = client.post(BASE, headers=auth_headers, json={
            "category": "other",
            "steps": "Typed only the required fields.",
            "actual": "This still submits.",
        })
        assert resp.status_code == 201, resp.text
        row = resp.json()
        assert row["page"] is None
        assert row["expected"] is None
        assert row["error_reference"] is None


class TestValidation:
    def test_unknown_category_is_rejected(self, client, auth_headers):
        for category in ("", "Research Results", "sql_injection", None):
            resp = client.post(BASE, headers=auth_headers, json=payload(category=category))
            assert resp.status_code == 422, (category, resp.text)

    def test_every_declared_category_is_accepted(self, client, auth_headers):
        for category in FEEDBACK_CATEGORIES:
            resp = client.post(BASE, headers=auth_headers, json=payload(category=category))
            assert resp.status_code == 201, (category, resp.text)

    def test_blank_required_text_is_rejected(self, client, auth_headers):
        for field in ("steps", "actual"):
            for blank in ("", "   ", "\n\t "):
                resp = client.post(BASE, headers=auth_headers, json=payload(**{field: blank}))
                assert resp.status_code == 422, (field, repr(blank), resp.text)

    def test_missing_required_field_is_rejected(self, client, auth_headers):
        for field in ("category", "steps", "actual"):
            body = payload()
            del body[field]
            resp = client.post(BASE, headers=auth_headers, json=body)
            assert resp.status_code == 422, (field, resp.text)

    def test_leading_and_trailing_whitespace_is_stripped(self, client, auth_headers):
        resp = client.post(BASE, headers=auth_headers, json=payload(
            steps="  padded steps  ",
            actual="  padded actual  ",
            page="  padded page  ",
        ))
        assert resp.status_code == 201, resp.text
        row = resp.json()
        assert row["steps"] == "padded steps"
        assert row["actual"] == "padded actual"
        assert row["page"] == "padded page"

    def test_whitespace_only_optional_field_is_stored_as_null(self, client, auth_headers):
        resp = client.post(BASE, headers=auth_headers, json=payload(
            page="   ", expected="\t", error_reference="  ",
        ))
        assert resp.status_code == 201, resp.text
        row = resp.json()
        assert row["page"] is None
        assert row["expected"] is None
        assert row["error_reference"] is None

    def test_length_limits_are_enforced_at_the_boundary(self, client, auth_headers):
        at_limit = {
            "steps": "x" * MAX_STEPS,
            "actual": "x" * MAX_STEPS,
            "expected": "x" * MAX_EXPECTED,
            "page": "x" * MAX_PAGE,
            "error_reference": "x" * MAX_ERROR_REFERENCE,
        }
        resp = client.post(BASE, headers=auth_headers, json=payload(**at_limit))
        assert resp.status_code == 201, resp.text

        for field, value in at_limit.items():
            over = dict(at_limit)
            over[field] = value + "x"
            rejected = client.post(BASE, headers=auth_headers, json=payload(**over))
            assert rejected.status_code == 422, (field, rejected.text)


class TestOwnership:
    def test_list_returns_only_my_rows(self, client, auth_headers):
        mine = client.post(BASE, headers=auth_headers, json=payload(steps="mine")).json()
        other_headers = second_user(client, "feedback-other@alethra.io")
        theirs = client.post(BASE, headers=other_headers, json=payload(steps="theirs")).json()

        listed = client.get(BASE, headers=auth_headers).json()
        assert [row["id"] for row in listed] == [mine["id"]]
        assert theirs["id"] not in [row["id"] for row in listed]

        their_listing = client.get(BASE, headers=other_headers).json()
        assert [row["id"] for row in their_listing] == [theirs["id"]]

    def test_reading_another_account_row_is_indistinguishable_from_missing(
        self, client, auth_headers
    ):
        # CONTRACT CHANGE, disclosed: this test asserted 403 before. 403 is an
        # answer only the row's existence can produce, so it let any caller
        # probe ids and learn which ones belong to other accounts. The endpoint
        # now scopes the read to the caller, and a stranger's id must not be
        # distinguishable from an id that was never written.
        other_headers = second_user(client, "feedback-owner@alethra.io")
        theirs = client.post(BASE, headers=other_headers, json=payload()).json()

        cross_user = client.get(f"{BASE}/{theirs['id']}", headers=auth_headers)
        missing = client.get(f"{BASE}/{uuid.uuid4()}", headers=auth_headers)

        assert cross_user.status_code == 404, cross_user.text
        assert (cross_user.status_code, cross_user.json()) == (
            missing.status_code,
            missing.json(),
        )
        assert "steps" not in cross_user.text
        assert theirs["id"] not in cross_user.text

    def test_the_owner_still_reads_the_row_the_other_account_cannot(
        self, client, auth_headers
    ):
        other_headers = second_user(client, "feedback-owner2@alethra.io")
        theirs = client.post(BASE, headers=other_headers, json=payload(steps="theirs")).json()

        assert client.get(f"{BASE}/{theirs['id']}", headers=auth_headers).status_code == 404
        mine = client.get(f"{BASE}/{theirs['id']}", headers=other_headers)
        assert mine.status_code == 200, mine.text
        assert mine.json()["steps"] == "theirs"

    def test_unknown_id_is_not_found(self, client, auth_headers):
        resp = client.get(f"{BASE}/{uuid.uuid4()}", headers=auth_headers)
        assert resp.status_code == 404, resp.text


class TestListPaging:
    def test_limit_bounds_are_inclusive(self, client, auth_headers):
        # The endpoint declares the range once, in the Query, so both edges
        # are accepted and only the outside is rejected.
        assert client.get(f"{BASE}?limit=1", headers=auth_headers).status_code == 200
        assert client.get(f"{BASE}?limit=100", headers=auth_headers).status_code == 200

    def test_limit_out_of_range_is_rejected(self, client, auth_headers):
        for limit in (0, -1, 101, 5000):
            resp = client.get(f"{BASE}?limit={limit}", headers=auth_headers)
            assert resp.status_code == 422, (limit, resp.text)

    def test_non_integer_limit_is_rejected(self, client, auth_headers):
        resp = client.get(f"{BASE}?limit=abc", headers=auth_headers)
        assert resp.status_code == 422, resp.text

    def test_limit_bounds_the_response(self, client, auth_headers):
        ids = []
        for _ in range(3):
            ids.append(client.post(BASE, headers=auth_headers, json=payload()).json()["id"])
        limited = client.get(f"{BASE}?limit=2", headers=auth_headers).json()
        assert len(limited) == 2
        assert {row["id"] for row in limited} <= set(ids)


class TestNothingIsAutoCollected:
    def test_row_holds_only_submitted_fields(self, client, auth_headers):
        resp = client.post(BASE, headers=auth_headers, json=payload())
        assert resp.status_code == 201, resp.text
        assert set(resp.json()) == {
            "id", "category", "page", "steps", "expected", "actual",
            "error_reference", "created_at", "submitted_by",
        }

    def test_credentials_never_reach_the_stored_text(self, client, auth_headers):
        token = auth_headers["Authorization"].split(" ", 1)[1]
        created = client.post(BASE, headers=auth_headers, json=payload())
        assert created.status_code == 201, created.text
        stored = client.get(f"{BASE}/{created.json()['id']}", headers=auth_headers).json()
        rendered = str(stored)
        assert token not in rendered
        assert "Bearer " not in rendered
