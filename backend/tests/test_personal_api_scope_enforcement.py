"""Personal paper API: scope, revocation and account isolation enforcement.

Section 6 of the acceptance brief requires the personal API to reuse the
existing API-key machinery rather than bolt on a parallel auth path. These tests
drive the real HTTP routes with a real API key in the Bearer header, so they
fail if a mutating endpoint is missing its scope guard — the state this file was
written against, where only POST /orders checked "write" and a read-scoped key
could still create, pause, resume and retire portfolios.

JWT sessions must stay unrestricted: the browser UI authenticates with a JWT and
would break if scope enforcement leaked onto it.
"""

import pytest

from app.config import get_settings

BASE = "/api/v1/paper-portfolio"


def _register_and_login(client, email):
    client.post("/api/auth/register", json={
        "email": email,
        "password": "testpass123",
        "display_name": email.split("@")[0],
        "user_type": "individual",
    })
    login = client.post("/api/auth/login", json={"email": email, "password": "testpass123"})
    # The login body carries the access token, so it must never reach an
    # assertion message.
    login_status = login.status_code
    assert login_status == 200, f"login for {email} -> {login_status}"
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _make_key(client, jwt_headers, name, scopes):
    resp = client.post(
        "/api/v1/api-keys", json={"name": name, "scopes": scopes}, headers=jwt_headers
    )
    # The creation body carries the raw key — the one value that must not leak.
    create_status = resp.status_code
    assert create_status == 201, f"create key {name!r} -> {create_status}"
    body = resp.json()
    return body["raw_key"], body["id"]


def _key_headers(raw_key):
    return {"Authorization": f"Bearer {raw_key}"}


def _create_portfolio(client, headers, cash=50000.0):
    resp = client.post(BASE, json={"initial_virtual_cash": cash, "currency": "USD"}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _decision_payload():
    return {
        "actor_type": "human",
        "decision_type": "no_action",
        "rationale": "scope enforcement probe",
    }


def _order_payload():
    return {
        "asset_id": "DEMO-ALPHA",
        "side": "BUY",
        "quantity": 1,
        "order_type": "market",
        "rationale": "scope enforcement probe",
        "expected_outcome": "should be refused by scope, not by risk",
    }


@pytest.fixture()
def owner_jwt(client):
    return _register_and_login(client, "scope-owner@alethra.io")


class TestWriteScopeEnforcement:
    def test_a_read_scoped_key_cannot_mutate_any_paper_portfolio_state(self, client, owner_jwt):
        portfolio_id = _create_portfolio(client, owner_jwt)
        read_key, _ = _make_key(client, owner_jwt, "read-only", ["read"])
        headers = _key_headers(read_key)

        # A decision is needed for the outcome route; recording one is itself a
        # write, so create it as the JWT owner.
        decision = client.post(
            f"{BASE}/{portfolio_id}/decisions", json=_decision_payload(), headers=owner_jwt
        )
        assert decision.status_code == 201, decision.text
        decision_id = decision.json()["id"]

        client.post(f"{BASE}/{portfolio_id}/pause", headers=owner_jwt)
        client.post(f"{BASE}/{portfolio_id}/resume", headers=owner_jwt)

        mutations = [
            ("POST", BASE, {"initial_virtual_cash": 10000.0, "currency": "USD"}),
            ("POST", f"{BASE}/{portfolio_id}/orders", _order_payload()),
            ("POST", f"{BASE}/{portfolio_id}/decisions", _decision_payload()),
            ("POST", f"{BASE}/{portfolio_id}/decisions/{decision_id}/outcome",
             {"actual_outcome_summary": "probe", "hypothesis_status": "evidence_insufficient"}),
            ("POST", f"{BASE}/{portfolio_id}/override-requests",
             {"original_rationale": "original", "override_reason": "probe"}),
            ("POST", f"{BASE}/{portfolio_id}/pause", None),
            ("POST", f"{BASE}/{portfolio_id}/resume", None),
            ("POST", f"{BASE}/{portfolio_id}/retire", None),
        ]

        for method, url, payload in mutations:
            resp = client.request(method, url, json=payload, headers=headers)
            assert resp.status_code == 403, f"{method} {url} -> {resp.status_code} {resp.text}"
            detail = resp.json()["detail"]
            assert detail["error"] == "insufficient_scope", f"{method} {url}: {detail}"
            assert detail["required_scope"] == "write", f"{method} {url}: {detail}"
            assert detail["held_scopes"] == ["read"], f"{method} {url}: {detail}"

        # Reads still work with the read scope, so the refusals above are scope
        # denials and not a blanket auth failure.
        # Status codes are bound before asserting: pytest rewrites chained
        # expressions by repr-ing intermediates, and an httpx Response repr
        # carries its request headers — i.e. the raw key — into the log.
        list_status = client.get(BASE, headers=headers).status_code
        assert list_status == 200
        listed = client.get(f"{BASE}/{portfolio_id}", headers=headers)
        detail_status = listed.status_code
        assert detail_status == 200
        assert listed.json()["status"] == "active"

    def test_a_write_scoped_key_can_mutate_the_same_state(self, client, owner_jwt):
        write_key, _ = _make_key(client, owner_jwt, "writer", ["read", "write"])
        headers = _key_headers(write_key)

        portfolio_id = _create_portfolio(client, headers)

        pause_status = client.post(f"{BASE}/{portfolio_id}/pause", headers=headers).status_code
        assert pause_status == 200
        resume_status = client.post(f"{BASE}/{portfolio_id}/resume", headers=headers).status_code
        assert resume_status == 200

        decision = client.post(
            f"{BASE}/{portfolio_id}/decisions", json=_decision_payload(), headers=headers
        )
        assert decision.status_code == 201, decision.text
        decision_id = decision.json()["id"]

        outcome = client.post(
            f"{BASE}/{portfolio_id}/decisions/{decision_id}/outcome",
            json={"actual_outcome_summary": "probe", "hypothesis_status": "evidence_insufficient"},
            headers=headers,
        )
        assert outcome.status_code == 201, outcome.text

        override = client.post(
            f"{BASE}/{portfolio_id}/override-requests",
            json={"original_rationale": "original", "override_reason": "probe"},
            headers=headers,
        )
        assert override.status_code == 201, override.text

        retire_status = client.post(f"{BASE}/{portfolio_id}/retire", headers=headers).status_code
        assert retire_status == 200

    def test_a_jwt_session_is_not_restricted_by_scopes(self, client, owner_jwt):
        portfolio_id = _create_portfolio(client, owner_jwt)
        statuses = {
            action: client.post(f"{BASE}/{portfolio_id}/{action}", headers=owner_jwt).status_code
            for action in ("pause", "resume", "retire")
        }
        assert statuses == {"pause": 200, "resume": 200, "retire": 200}


class TestRevocationAndIsolation:
    def test_a_revoked_key_is_rejected_on_reads_and_writes(self, client, owner_jwt):
        portfolio_id = _create_portfolio(client, owner_jwt)
        raw_key, key_id = _make_key(client, owner_jwt, "doomed", ["read", "write"])
        headers = _key_headers(raw_key)

        before_revoke = client.get(BASE, headers=headers).status_code
        assert before_revoke == 200

        revoke = client.delete(f"/api/v1/api-keys/{key_id}", headers=owner_jwt)
        revoke_status = revoke.status_code
        assert revoke_status == 204, revoke.text

        read_status = client.get(BASE, headers=headers).status_code
        assert read_status == 401
        write_status = client.post(f"{BASE}/{portfolio_id}/pause", headers=headers).status_code
        assert write_status == 401

    def test_a_second_accounts_key_cannot_reach_the_first_accounts_portfolio(self, client):
        owner_a = _register_and_login(client, "scope-a@alethra.io")
        owner_b = _register_and_login(client, "scope-b@alethra.io")
        portfolio_a = _create_portfolio(client, owner_a)

        key_b, _ = _make_key(client, owner_b, "b-writer", ["read", "write"])
        headers_b = _key_headers(key_b)

        # B holds a valid read+write key, so these refusals are ownership
        # refusals, not scope or auth refusals. The ownership helper answers 403
        # for a portfolio that exists but belongs to someone else, and 404 only
        # when the id does not exist at all.
        probes = [
            ("GET", f"{BASE}/{portfolio_a}", None),
            ("POST", f"{BASE}/{portfolio_a}/pause", None),
            ("POST", f"{BASE}/{portfolio_a}/retire", None),
            ("POST", f"{BASE}/{portfolio_a}/orders", _order_payload()),
            ("POST", f"{BASE}/{portfolio_a}/decisions", _decision_payload()),
        ]
        for method, url, payload in probes:
            resp = client.request(method, url, json=payload, headers=headers_b)
            status = resp.status_code
            assert status == 403, f"{method} {url} -> {status}"
            assert resp.json()["detail"] == "Not authorized", f"{method} {url}"

        missing = "11111111-1111-4111-8111-111111111111"
        absent_status = client.get(f"{BASE}/{missing}", headers=headers_b).status_code
        assert absent_status == 404

        # B's own listing must not leak A's portfolio either.
        listed = client.get(BASE, headers=headers_b)
        listed_status = listed.status_code
        assert listed_status == 200
        assert portfolio_a not in [p["id"] for p in listed.json()]

        # A can still read its own portfolio with its own JWT.
        owner_view = client.get(f"{BASE}/{portfolio_a}", headers=owner_a)
        owner_status = owner_view.status_code
        assert owner_status == 200
        assert owner_view.json()["id"] == portfolio_a


class TestPerApiKeyRateLimit:
    """The personal API must budget each key, not each IP and not each user.

    A per-IP ceiling would let one user's runaway script starve a second user
    behind the same NAT, and would let a user multiply their own allowance by
    minting more keys.  Keying the bucket on the authenticated key's id makes the
    budget a property of the credential the operator can revoke.
    """

    LIMIT = get_settings().api_key_rate_limit_requests
    WINDOW = get_settings().api_key_rate_limit_window_seconds

    def _spend(self, client, headers, count):
        """Issue ``count`` reads, returning their status codes in order."""
        return [client.get(BASE, headers=headers).status_code for _ in range(count)]

    def test_a_key_is_refused_only_once_its_budget_is_spent(self, client, owner_jwt):
        _create_portfolio(client, owner_jwt)
        raw_key, _ = _make_key(client, owner_jwt, "budgeted", ["read"])
        headers = _key_headers(raw_key)

        statuses = self._spend(client, headers, self.LIMIT)
        assert statuses == [200] * self.LIMIT, (
            f"a key must be served up to its budget; first refusal at "
            f"{statuses.index(429) if 429 in statuses else 'none'}"
        )

        exhausted = client.get(BASE, headers=headers)
        exhausted_status = exhausted.status_code
        assert exhausted_status == 429
        assert exhausted.headers["X-RateLimit-Limit"] == str(self.LIMIT)
        assert exhausted.headers["X-RateLimit-Remaining"] == "0"
        retry_after = int(exhausted.headers["Retry-After"])
        assert 1 <= retry_after <= self.WINDOW + 1, (
            "Retry-After must be a usable wait, not zero and not longer than the window"
        )
        detail = exhausted.json()["detail"]
        assert str(self.LIMIT) in detail and "Retry-After" in detail, (
            "the refusal must tell the caller the budget and where to find the wait"
        )

        # Still refused, so the 429 is a state and not a one-off response.
        still_status = client.get(BASE, headers=headers).status_code
        assert still_status == 429

    def test_two_keys_of_one_user_do_not_share_a_budget(self, client, owner_jwt):
        _create_portfolio(client, owner_jwt)
        key_a, _ = _make_key(client, owner_jwt, "first", ["read"])
        key_b, _ = _make_key(client, owner_jwt, "second", ["read"])

        assert self._spend(client, _key_headers(key_a), self.LIMIT) == [200] * self.LIMIT
        spent_status = client.get(BASE, headers=_key_headers(key_a)).status_code
        assert spent_status == 429

        # Both keys came from the same user and the same client IP, so a 200 here
        # can only mean the bucket is keyed on the credential.
        other = client.get(BASE, headers=_key_headers(key_b))
        other_status = other.status_code
        assert other_status == 200
        assert other.headers["X-RateLimit-Remaining"] == str(self.LIMIT - 1)

    def test_a_jwt_session_is_not_charged_against_a_key_budget(self, client, owner_jwt):
        portfolio_id = _create_portfolio(client, owner_jwt)
        raw_key, _ = _make_key(client, owner_jwt, "browser-alike", ["read"])

        assert self._spend(client, _key_headers(raw_key), self.LIMIT) == [200] * self.LIMIT
        assert client.get(BASE, headers=_key_headers(raw_key)).status_code == 429

        # The browser UI authenticates with a JWT. Limiting it here would break
        # the product for a reason that has nothing to do with programmatic abuse.
        jwt_statuses = [client.get(BASE, headers=owner_jwt).status_code for _ in range(5)]
        assert jwt_statuses == [200] * 5
        detail_status = client.get(f"{BASE}/{portfolio_id}", headers=owner_jwt).status_code
        assert detail_status == 200
        assert "x-ratelimit-remaining" not in {k.lower() for k in client.get(
            BASE, headers=owner_jwt
        ).headers}

    def test_a_revoked_key_cannot_order_whatever_budget_remains(self, client, owner_jwt):
        portfolio_id = _create_portfolio(client, owner_jwt)
        raw_key, key_id = _make_key(client, owner_jwt, "revoked-writer", ["read", "write"])
        headers = _key_headers(raw_key)

        before = client.post(
            f"{BASE}/{portfolio_id}/orders", json=_order_payload(), headers=headers
        )
        before_status = before.status_code
        assert before_status in (200, 201), before.text

        revoke_status = client.delete(
            f"/api/v1/api-keys/{key_id}", headers=owner_jwt
        ).status_code
        assert revoke_status == 204

        # Revocation must be enforced by authentication, which runs before the
        # budget is consulted, so a revoked key never reaches the order path.
        after = client.post(
            f"{BASE}/{portfolio_id}/orders", json=_order_payload(), headers=headers
        )
        after_status = after.status_code
        assert after_status == 401

    def test_the_refusal_never_echoes_the_key_it_refused(self, client, owner_jwt):
        _create_portfolio(client, owner_jwt)
        raw_key, _ = _make_key(client, owner_jwt, "leak-probe", ["read"])
        headers = _key_headers(raw_key)

        self._spend(client, headers, self.LIMIT)
        refused = client.get(BASE, headers=headers)
        assert refused.status_code == 429

        # Bound to locals first: pytest rewrites assertions by repr-ing their
        # operands, and an operand holding the raw key would print it into the log.
        in_body = raw_key in refused.text
        in_headers = any(raw_key in str(value) for value in refused.headers.values())
        in_detail = raw_key in str(refused.json()["detail"])
        assert not in_body, "the raw key must not appear in a 429 body"
        assert not in_headers, "the raw key must not appear in a 429 header"
        assert not in_detail, "the raw key must not appear in a 429 detail"

    def test_a_second_caller_is_kept_out_of_the_count_then_write_window(self):
        """Two callers may not both be granted the last unit of one budget.

        Driven at the limiter rather than over HTTP: the property under test is
        the window's own atomicity, and the TestClient's single-threaded ASGI
        transport would serialise the callers and prove nothing.

        Hammering the window statistically does not work either.  An earlier
        version of this test released 40 barrier-synchronised callers per trial
        over 40 independent limiters with the interpreter's switch interval
        shrunk to 1us, and it was measured to pass with the limiter's lock
        deleted: the gap between reading the count and appending the timestamp
        is a handful of bytecodes, so the scheduler declines to interrupt
        inside it.  A guard that no mutation can break is not evidence.

        This version drives the interleaving instead of waiting for it.  The
        limiter's store is swapped for a list subclass whose ``append`` reports
        that the caller is past the length check and has not yet written — the
        exact moment a second caller must be excluded — and holds it there while
        the second caller runs.  With the lock the second caller blocks outside
        ``check_rate_limit``, sees the first caller's timestamp once admitted and
        is refused; without it the second caller reads the still-empty count and
        both are granted a budget of one.

        Reaching into the private store makes this deliberately white-box.  It
        fails loudly rather than silently passing if the limiter's storage
        changes shape, which is the intended failure mode.
        """
        import threading

        from app.auth.rate_limit import RateLimiter

        a_writing = threading.Event()
        b_writing = threading.Event()
        release_a = threading.Event()

        class GatedList(list):
            def append(self, item):
                name = threading.current_thread().name
                if name == "gated-a":
                    a_writing.set()
                    release_a.wait(5.0)
                elif name == "gated-b":
                    b_writing.set()
                super().append(item)

        class GatedStore(dict):
            # _cleanup reassigns a plain filtered list on every call, so the
            # gate has to be re-applied on write, not only on first access.
            def __setitem__(self, key, value):
                if not isinstance(value, GatedList):
                    value = GatedList(value)
                dict.__setitem__(self, key, value)

            def __missing__(self, key):
                value = GatedList()
                self[key] = value
                return value

        limiter = RateLimiter()
        limiter._requests = GatedStore()
        budget = 1
        granted = {}

        def call(label):
            granted[label] = limiter.check_rate_limit("apk:gated", budget, 60.0)

        first = threading.Thread(target=call, args=("a",), name="gated-a")
        second = threading.Thread(target=call, args=("b",), name="gated-b")

        first.start()
        assert a_writing.wait(5.0), "the first caller never reached the write"
        second.start()
        # Bounded, not asserted: whether the second caller reaches the write is
        # the outcome under test, so a timeout here is a result, not a failure.
        second_entered = b_writing.wait(1.0)
        release_a.set()
        first.join(5.0)
        second.join(5.0)

        assert not first.is_alive() and not second.is_alive(), "a gated caller hung"
        assert not second_entered, (
            "the second caller reached the write while the first was still "
            "between the count and the write, so the budget is not guarded"
        )
        assert granted == {"a": True, "b": False}, (
            f"a budget of {budget} may be granted once; got {granted}"
        )
