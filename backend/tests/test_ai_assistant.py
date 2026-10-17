"""Personal AI assistant tests: provider adapter, conversations, quota,
draft proposals, and secret safety.

All tests run offline: the real OpenAI-compatible provider is exercised
through an injected httpx MockTransport, never the network.
"""
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.ai_provider import (
    AIProviderError,
    CompletionRequest,
    MockAIProvider,
    OpenAICompatibleProvider,
    estimate_cost_cents,
    get_ai_provider,
)


@pytest.fixture()
def client_db():
    """A session bound to the same test engine the API client uses."""
    from tests.conftest import TestSession

    db = TestSession()
    try:
        yield db
    finally:
        db.close()


SECRET_KEY = "sk-test-supersecret-key-123456"


def _completion_body(content="分析完成", prompt_tokens=11, completion_tokens=7):
    return {
        "id": "chatcmpl-1",
        "model": "gpt-test",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def _mock_transport(handler):
    return httpx.MockTransport(handler)


# ── Provider adapter (unit) ────────────────────────────────────────────────


class TestMockProvider:
    def test_deterministic_offline_reply(self):
        provider = MockAIProvider()
        req = CompletionRequest(
            messages=[{"role": "user", "content": "AAPL 值得研究嗎？"}]
        )
        first = provider.complete(req)
        second = provider.complete(req)
        assert first.content == second.content
        assert first.provider == "mock"
        assert first.prompt_tokens > 0 and first.completion_tokens > 0

    def test_test_connection_ok(self):
        result = MockAIProvider().test_connection()
        assert result.ok is True
        assert result.detail


class TestOpenAICompatibleProvider:
    def _provider(self, transport):
        return OpenAICompatibleProvider(
            api_key=SECRET_KEY,
            model="gpt-test",
            base_url="https://ai.example.test/v1",
            timeout_seconds=5.0,
            transport=transport,
        )

    def test_success_parses_usage(self):
        provider = self._provider(
            _mock_transport(
                lambda request: httpx.Response(200, json=_completion_body())
            )
        )
        result = provider.complete(
            CompletionRequest(messages=[{"role": "user", "content": "hi"}])
        )
        assert result.content == "分析完成"
        assert result.model == "gpt-test"
        assert result.prompt_tokens == 11
        assert result.completion_tokens == 7
        assert result.provider == "openai_compatible"

    def test_auth_error(self):
        provider = self._provider(
            _mock_transport(lambda request: httpx.Response(401, json={"error": {}}))
        )
        with pytest.raises(AIProviderError) as exc_info:
            provider.complete(
                CompletionRequest(messages=[{"role": "user", "content": "hi"}])
            )
        assert exc_info.value.kind == "auth"

    def test_rate_limit_error(self):
        provider = self._provider(
            _mock_transport(lambda request: httpx.Response(429, json={}))
        )
        with pytest.raises(AIProviderError) as exc_info:
            provider.complete(
                CompletionRequest(messages=[{"role": "user", "content": "hi"}])
            )
        assert exc_info.value.kind == "rate_limit"

    def test_server_error(self):
        provider = self._provider(
            _mock_transport(lambda request: httpx.Response(503, text="unavailable"))
        )
        with pytest.raises(AIProviderError) as exc_info:
            provider.complete(
                CompletionRequest(messages=[{"role": "user", "content": "hi"}])
            )
        assert exc_info.value.kind == "unavailable"

    def test_timeout_error(self):
        def handler(request):
            raise httpx.ReadTimeout("read timed out")

        provider = self._provider(_mock_transport(handler))
        with pytest.raises(AIProviderError) as exc_info:
            provider.complete(
                CompletionRequest(messages=[{"role": "user", "content": "hi"}])
            )
        assert exc_info.value.kind == "timeout"

    def test_invalid_json_response(self):
        provider = self._provider(
            _mock_transport(lambda request: httpx.Response(200, text="not json"))
        )
        with pytest.raises(AIProviderError) as exc_info:
            provider.complete(
                CompletionRequest(messages=[{"role": "user", "content": "hi"}])
            )
        assert exc_info.value.kind == "invalid_response"

    def test_empty_completion_rejected(self):
        provider = self._provider(
            _mock_transport(
                lambda request: httpx.Response(200, json=_completion_body(content="  "))
            )
        )
        with pytest.raises(AIProviderError) as exc_info:
            provider.complete(
                CompletionRequest(messages=[{"role": "user", "content": "hi"}])
            )
        assert exc_info.value.kind == "invalid_response"

    def test_api_key_never_leaks_in_errors(self):
        def handler(request):
            # Provider echoes the key back in an error body.
            return httpx.Response(400, text=f"bad key {SECRET_KEY}")

        provider = self._provider(_mock_transport(handler))
        with pytest.raises(AIProviderError) as exc_info:
            provider.complete(
                CompletionRequest(messages=[{"role": "user", "content": "hi"}])
            )
        assert SECRET_KEY not in str(exc_info.value)
        assert SECRET_KEY not in exc_info.value.message
        assert "[redacted]" in exc_info.value.message

    def test_connection_test_mappings(self):
        ok = self._provider(
            _mock_transport(lambda request: httpx.Response(200, json={"data": []}))
        ).test_connection()
        assert ok.ok is True

        unauthorized = self._provider(
            _mock_transport(lambda request: httpx.Response(401, json={}))
        ).test_connection()
        assert unauthorized.ok is False and unauthorized.kind == "auth"

        limited = self._provider(
            _mock_transport(lambda request: httpx.Response(429, json={}))
        ).test_connection()
        assert limited.ok is False and limited.kind == "rate_limit"

        def unreachable(request):
            raise httpx.ConnectError("connection refused")

        down = self._provider(_mock_transport(unreachable)).test_connection()
        assert down.ok is False and down.kind == "unavailable"

    def test_missing_key_rejected(self):
        with pytest.raises(AIProviderError) as exc_info:
            OpenAICompatibleProvider(api_key="", model="gpt-test")
        assert exc_info.value.kind == "not_configured"


class TestProviderFactory:
    def test_default_is_mock(self):
        assert get_ai_provider().name == "mock"

    def test_unknown_provider_rejected(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "ai_provider", "psychic", raising=False)
        with pytest.raises(AIProviderError) as exc_info:
            get_ai_provider()
        assert exc_info.value.kind == "not_configured"

    def test_cost_estimate(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "ai_cost_per_1k_tokens", 0.01, raising=False)
        # 1000 tokens * $0.01 / 1k = $0.01 = 1 cent
        assert estimate_cost_cents(500, 500) == 1
        monkeypatch.setattr(settings, "ai_cost_per_1k_tokens", 0.0, raising=False)
        assert estimate_cost_cents(500, 500) == 0


# ── API: status / test-connection ───────────────────────────────────────────


class TestAIStatus:
    def test_requires_auth(self, client):
        assert client.get("/api/v1/ai/status").status_code == 401
        assert client.post("/api/v1/ai/test-connection").status_code == 401

    def test_status_no_secret_leak(self, client, auth_headers):
        resp = client.get("/api/v1/ai/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "mock"
        assert data["mode"] == "mock"
        assert data["api_key_configured"] is False
        assert "ai_api_key" not in data
        assert "api_key" not in data
        assert data["advisory_notice"]

    def test_test_connection_mock_ok(self, client, auth_headers):
        resp = client.post("/api/v1/ai/test-connection", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["provider"] == "mock"


# ── API: conversations ─────────────────────────────────────────────────────


def create_research_draft(client, headers, title="測試草稿"):
    resp = client.post(
        "/api/v1/research-drafts/",
        headers=headers,
        json={"title": title, "hypothesis": "h1", "asset_symbol": "DEMO-ALPHA"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestConversations:
    def test_crud_roundtrip(self, client, auth_headers):
        created = client.post(
            "/api/v1/ai/conversations",
            headers=auth_headers,
            json={"title": "AAPL 研究"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        conv_id = body["id"]
        assert body["title"] == "AAPL 研究"
        assert body["messages"] == []

        listed = client.get("/api/v1/ai/conversations", headers=auth_headers)
        assert listed.status_code == 200
        assert any(c["id"] == conv_id for c in listed.json())

        patched = client.patch(
            f"/api/v1/ai/conversations/{conv_id}",
            headers=auth_headers,
            json={"title": "改名"},
        )
        assert patched.status_code == 200
        assert patched.json()["title"] == "改名"

        deleted = client.delete(
            f"/api/v1/ai/conversations/{conv_id}", headers=auth_headers
        )
        assert deleted.status_code == 204
        assert (
            client.get(
                f"/api/v1/ai/conversations/{conv_id}", headers=auth_headers
            ).status_code
            == 404
        )

    def test_cross_user_404(self, client, auth_headers):
        from tests.test_watchlists import second_user_headers

        created = client.post(
            "/api/v1/ai/conversations", headers=auth_headers, json={}
        )
        conv_id = created.json()["id"]
        other = second_user_headers(client)
        assert (
            client.get(
                f"/api/v1/ai/conversations/{conv_id}", headers=other
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/api/v1/ai/conversations/{conv_id}", headers=other
            ).status_code
            == 404
        )

    def test_context_with_own_draft_and_symbol(self, client, auth_headers):
        draft = create_research_draft(client, auth_headers)
        resp = client.post(
            "/api/v1/ai/conversations",
            headers=auth_headers,
            json={
                "context": {
                    "research_draft_ids": [draft["id"]],
                    "instrument_symbols": ["US:AAPL", "HK:0700"],
                }
            },
        )
        assert resp.status_code == 201, resp.text
        ctx = resp.json()["context"]
        assert ctx["research_draft_ids"] == [draft["id"]]

    def test_context_foreign_draft_404(self, client, auth_headers):
        from tests.test_watchlists import second_user_headers

        other = second_user_headers(client)
        foreign_draft = create_research_draft(client, other)
        resp = client.post(
            "/api/v1/ai/conversations",
            headers=auth_headers,
            json={"context": {"research_draft_ids": [foreign_draft["id"]]}},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "research_drafts_not_found"

    def test_context_unknown_symbol_400(self, client, auth_headers):
        resp = client.post(
            "/api/v1/ai/conversations",
            headers=auth_headers,
            json={"context": {"instrument_symbols": ["XX:NOSUCH"]}},
        )
        assert resp.status_code == 400

    def test_context_private_memory_rejected(self, client, auth_headers, client_db):
        from app.models.model_research import MemoryType, PersonalResearchMemory, PrivacyScope
        from app.models.user import User

        email = "mem-user@alethra.io"
        client.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": "testpass123",
                "display_name": "Mem",
                "user_type": "individual",
            },
        )
        user = client_db.query(User).filter(User.email == email).first()
        private_mem = PersonalResearchMemory(
            owner_id=user.id,
            memory_type=MemoryType.accepted_ai_draft,
            content="private insight",
            privacy_scope=PrivacyScope.private_only,
        )
        approved_mem = PersonalResearchMemory(
            owner_id=user.id,
            memory_type=MemoryType.accepted_ai_draft,
            content="shareable insight",
            privacy_scope=PrivacyScope.approved_for_personal_ai,
        )
        client_db.add_all([private_mem, approved_mem])
        client_db.commit()

        login = client.post(
            "/api/auth/login", json={"email": email, "password": "testpass123"}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        rejected = client.post(
            "/api/v1/ai/conversations",
            headers=headers,
            json={"context": {"memory_ids": [str(private_mem.id)]}},
        )
        assert rejected.status_code == 400
        assert rejected.json()["detail"]["error"] == "memory_privacy_scope"

        accepted = client.post(
            "/api/v1/ai/conversations",
            headers=headers,
            json={"context": {"memory_ids": [str(approved_mem.id)]}},
        )
        assert accepted.status_code == 201


# ── API: send message (quota + persistence) ────────────────────────────────


class TestSendMessage:
    def test_happy_path_persists_and_consumes_quota(self, client, auth_headers):
        conv = client.post(
            "/api/v1/ai/conversations", headers=auth_headers, json={}
        ).json()
        resp = client.post(
            f"/api/v1/ai/conversations/{conv['id']}/messages",
            headers=auth_headers,
            json={"content": "AAPL 的長期研究框架？"},
        )
        assert resp.status_code == 200, resp.text
        reply = resp.json()
        assert reply["role"] == "assistant"
        assert reply["status"] == "completed"
        assert reply["provider"] == "mock"
        assert reply["model"]
        assert reply["completion_tokens"] > 0
        assert reply["quota_used"] == 1

        detail = client.get(
            f"/api/v1/ai/conversations/{conv['id']}", headers=auth_headers
        ).json()
        roles = [m["role"] for m in detail["messages"]]
        assert roles == ["user", "assistant"]

    def test_usage_record_written(self, client, auth_headers, client_db):
        from app.models.billing import UsageRecord

        conv = client.post(
            "/api/v1/ai/conversations", headers=auth_headers, json={}
        ).json()
        client.post(
            f"/api/v1/ai/conversations/{conv['id']}/messages",
            headers=auth_headers,
            json={"content": "hello"},
        )
        record = (
            client_db.query(UsageRecord)
            .filter(UsageRecord.endpoint.like(f"/ai/conversations/%"))
            .first()
        )
        assert record is not None
        assert record.tokens_used > 0
        assert record.metadata_json["conversation_id"] == conv["id"]

    def test_context_fed_to_reply(self, client, auth_headers):
        resp = client.post(
            "/api/v1/ai/conversations",
            headers=auth_headers,
            json={"context": {"instrument_symbols": ["US:AAPL"]}},
        )
        conv = resp.json()
        reply = client.post(
            f"/api/v1/ai/conversations/{conv['id']}/messages",
            headers=auth_headers,
            json={"content": "幫我分析"},
        ).json()
        assert "[symbol:US:AAPL]" in reply["content"]

    def test_quota_exhaustion_429(self, client, auth_headers):
        conv = client.post(
            "/api/v1/ai/conversations", headers=auth_headers, json={}
        ).json()
        for i in range(10):  # free plan ai_usage_limit = 10
            ok = client.post(
                f"/api/v1/ai/conversations/{conv['id']}/messages",
                headers=auth_headers,
                json={"content": f"question {i}"},
            )
            assert ok.status_code == 200, ok.text
        blocked = client.post(
            f"/api/v1/ai/conversations/{conv['id']}/messages",
            headers=auth_headers,
            json={"content": "one more"},
        )
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["quota_type"] == "ai_usage"

    def test_provider_failure_releases_quota(
        self, client, auth_headers, monkeypatch
    ):
        import app.api.ai_assistant as ai_api

        class FailingProvider:
            name = "mock"

            def complete(self, request):
                raise AIProviderError("timeout", "AI provider timed out after 30s")

        monkeypatch.setattr(ai_api, "get_ai_provider", lambda: FailingProvider())

        conv = client.post(
            "/api/v1/ai/conversations", headers=auth_headers, json={}
        ).json()
        resp = client.post(
            f"/api/v1/ai/conversations/{conv['id']}/messages",
            headers=auth_headers,
            json={"content": "會失敗的問題"},
        )
        assert resp.status_code == 200
        reply = resp.json()
        assert reply["status"] == "error"
        assert reply["error_detail"] == "AI provider timed out after 30s"

        # Quota must NOT be consumed by the failed call: the next real
        # message succeeds and reports exactly 1 used unit.
        monkeypatch.setattr(ai_api, "get_ai_provider", get_ai_provider)
        ok = client.post(
            f"/api/v1/ai/conversations/{conv['id']}/messages",
            headers=auth_headers,
            json={"content": "恢復後的問題"},
        )
        assert ok.status_code == 200
        assert ok.json()["quota_used"] == 1

    def test_blank_message_rejected(self, client, auth_headers):
        conv = client.post(
            "/api/v1/ai/conversations", headers=auth_headers, json={}
        ).json()
        resp = client.post(
            f"/api/v1/ai/conversations/{conv['id']}/messages",
            headers=auth_headers,
            json={"content": "   "},
        )
        assert resp.status_code == 422

    def test_cross_user_404(self, client, auth_headers):
        from tests.test_watchlists import second_user_headers

        conv = client.post(
            "/api/v1/ai/conversations", headers=auth_headers, json={}
        ).json()
        other = second_user_headers(client)
        assert (
            client.post(
                f"/api/v1/ai/conversations/{conv['id']}/messages",
                headers=other,
                json={"content": "hi"},
            ).status_code
            == 404
        )


# ── API: AI-proposed drafts (reviewable) ───────────────────────────────────


class TestAIDraftProposal:
    def _conversation(self, client, headers):
        return client.post(
            "/api/v1/ai/conversations", headers=headers, json={}
        ).json()

    def test_creates_pending_review_draft(self, client, auth_headers):
        conv = self._conversation(client, auth_headers)
        resp = client.post(
            f"/api/v1/ai/conversations/{conv['id']}/drafts",
            headers=auth_headers,
            json={
                "title": "AAPL 均值回歸假設",
                "hypothesis": "AAPL 短期超跌時 20 日內回歸均值",
                "falsification_criteria": "回歸次數 < 50%",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
            },
        )
        assert resp.status_code == 201, resp.text
        draft = resp.json()
        assert draft["status"] == "pending_review"
        assert draft["source_conversation_id"] == conv["id"]
        assert draft["review_notice"]

        fetched = client.get(
            f"/api/v1/research-drafts/{draft['id']}", headers=auth_headers
        ).json()
        assert fetched["source_conversation_id"] == conv["id"]

        events = client.get(
            f"/api/v1/research-drafts/{draft['id']}/audit-events",
            headers=auth_headers,
        ).json()
        assert any(
            e["event_type"] == "research_draft_created_from_ai" for e in events
        )

    def test_accept_after_review_records_usage_event(
        self, client, auth_headers, client_db
    ):
        from app.models.pilot import UsageEvent, UsageEventType

        conv = self._conversation(client, auth_headers)
        draft = client.post(
            f"/api/v1/ai/conversations/{conv['id']}/drafts",
            headers=auth_headers,
            json={
                "title": "完整草稿",
                "hypothesis": "h",
                "falsification_criteria": "f",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
            },
        ).json()

        accepted = client.post(
            f"/api/v1/research-drafts/{draft['id']}/approve",
            headers=auth_headers,
            json={"action": "accept", "notes": "人工審核通過"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "accepted"

        event = (
            client_db.query(UsageEvent)
            .filter(UsageEvent.event_type == UsageEventType.ai_draft_approved)
            .first()
        )
        assert event is not None
        assert event.metadata_json["draft_id"] == draft["id"]

    def test_incomplete_ai_draft_cannot_be_accepted(self, client, auth_headers):
        conv = self._conversation(client, auth_headers)
        draft = client.post(
            f"/api/v1/ai/conversations/{conv['id']}/drafts",
            headers=auth_headers,
            json={"title": "不完整"},
        ).json()
        rejected = client.post(
            f"/api/v1/research-drafts/{draft['id']}/approve",
            headers=auth_headers,
            json={"action": "accept", "notes": ""},
        )
        assert rejected.status_code == 400

    def test_reject_ai_draft(self, client, auth_headers):
        conv = self._conversation(client, auth_headers)
        draft = client.post(
            f"/api/v1/ai/conversations/{conv['id']}/drafts",
            headers=auth_headers,
            json={"title": "會被拒絕的提議"},
        ).json()
        resp = client.post(
            f"/api/v1/research-drafts/{draft['id']}/approve",
            headers=auth_headers,
            json={"action": "reject", "notes": "證據不足"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_foreign_source_message_404(self, client, auth_headers):
        conv = self._conversation(client, auth_headers)
        resp = client.post(
            f"/api/v1/ai/conversations/{conv['id']}/drafts",
            headers=auth_headers,
            json={"title": "x", "source_message_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404
