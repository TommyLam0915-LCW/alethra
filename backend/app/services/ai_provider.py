"""Personal AI provider adapter.

A pluggable LLM adapter layer for the personal AI assistant. Providers are
selected via settings (``AI_PROVIDER``). Two implementations ship:

* ``MockAIProvider`` — deterministic, offline, no external calls. The default.
* ``OpenAICompatibleProvider`` — real HTTP adapter speaking the OpenAI
  Chat Completions API (``AI_BASE_URL`` points it at any OpenAI-compatible
  endpoint). Requires ``AI_API_KEY`` in production-like environments.

Security invariants
-------------------
* The API key is never included in errors, logs, or API responses.
* Provider failures are mapped to ``AIProviderError`` with a ``kind`` the
  API layer can translate to HTTP semantics — raw exception text from the
  network stack is sanitized before it reaches a client.
* This module is advisory-only: it has no imports of execution, order, or
  intent code. LLM replies are never trading authorization.
"""
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_settings

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"

_SECRET_SUBSTITUTION = "[redacted]"


def _sanitize(text: str) -> str:
    """Strip anything that looks like a bearer credential from error text."""
    settings = get_settings()
    cleaned = text
    key = settings.ai_api_key
    if key:
        cleaned = cleaned.replace(key, _SECRET_SUBSTITUTION)
    cleaned = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", _SECRET_SUBSTITUTION, cleaned)
    cleaned = re.sub(
        r"(Bearer\s+)[^\s\"']+",
        lambda m: m.group(1) + _SECRET_SUBSTITUTION,
        cleaned,
    )
    return cleaned


class AIProviderError(Exception):
    """Provider failure with a machine-readable kind.

    Kinds: ``timeout``, ``auth``, ``rate_limit``, ``unavailable``,
    ``invalid_response``, ``not_configured``.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = _sanitize(message)


@dataclass
class CompletionRequest:
    messages: List[Dict[str, str]]  # [{"role": "...", "content": "..."}]
    max_tokens: Optional[int] = None
    timeout_seconds: Optional[float] = None


@dataclass
class CompletionResult:
    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


@dataclass
class ConnectionTestResult:
    ok: bool
    detail: str
    latency_ms: int = 0
    kind: Optional[str] = None  # error kind when ok=False


class BaseAIProvider(ABC):
    """Interface every personal-AI provider must implement."""

    name: str = "base"

    @abstractmethod
    def complete(self, request: CompletionRequest) -> CompletionResult:
        """Run a chat completion. Raises AIProviderError on failure."""

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Cheap connectivity/credential check. Must not leak secrets."""


class MockAIProvider(BaseAIProvider):
    """Deterministic offline provider.

    Produces a structured advisory reply that echoes the user's question,
    lists the context references it was given, and never invents market
    data. Token counts are deterministic (content-derived), so usage
    accounting behaves consistently in tests.
    """

    name = "mock"

    def complete(self, request: CompletionRequest) -> CompletionResult:
        started = time.monotonic()
        user_messages = [m for m in request.messages if m["role"] == "user"]
        last_user = user_messages[-1]["content"] if user_messages else ""
        system = next(
            (m["content"] for m in request.messages if m["role"] == "system"), ""
        )
        referenced = re.findall(r"\[[A-Za-z]+:[^\]]+\]", system + last_user)

        lines = [
            "（模擬 AI 助手 — 確定性回覆，不會呼叫外部 AI 服務）",
            "",
            f"已收到你的問題：{last_user[:200]}",
        ]
        if referenced:
            lines.append(f"參考脈絡：{', '.join(referenced[:10])}")
        lines += [
            "",
            "建議下一步：",
            "1. 將假設與可證偽條件寫成研究草稿（AI 僅提議，不構成交易授權）",
            "2. 以歷史數據回測驗證，並檢視數據來源標示",
            "3. 經你明確審核後才建立實驗",
        ]

        content = "\n".join(lines)
        prompt_tokens = sum(len(m["content"]) // 4 + 1 for m in request.messages)
        completion_tokens = len(content) // 4 + 1
        return CompletionResult(
            content=content,
            model=get_settings().ai_model,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(
            ok=True,
            detail="模擬供應商已就緒（離線、確定性，無外部呼叫）",
            latency_ms=1,
        )


class OpenAICompatibleProvider(BaseAIProvider):
    """Real HTTP adapter for OpenAI-compatible chat-completion APIs.

    Uses ``httpx`` with an explicit timeout; failures are mapped to
    ``AIProviderError`` kinds. The API key is attached as a bearer token
    and never appears in error messages (see ``_sanitize``).
    """

    name = "openai_compatible"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "",
        timeout_seconds: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        if not api_key:
            raise AIProviderError(
                "not_configured", "AI_API_KEY is required for openai_compatible"
            )
        self._api_key = api_key
        self._model = model
        self._base_url = (base_url or OPENAI_DEFAULT_BASE_URL).rstrip("/")
        self._timeout_seconds = timeout_seconds
        # ``transport`` allows tests to inject an offline httpx mock.
        self._transport = transport

    @property
    def base_url(self) -> str:
        return self._base_url

    def _client(self) -> httpx.Client:
        kwargs: Dict[str, Any] = {
            "base_url": self._base_url,
            "timeout": httpx.Timeout(self._timeout_seconds),
            "headers": {"Authorization": f"Bearer {self._api_key}"},
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _map_http_error(self, exc: httpx.HTTPError) -> AIProviderError:
        if isinstance(exc, httpx.TimeoutException):
            return AIProviderError(
                "timeout",
                f"AI provider timed out after {self._timeout_seconds:g}s",
            )
        return AIProviderError("unavailable", _sanitize(f"AI provider unreachable: {exc}"))

    def complete(self, request: CompletionRequest) -> CompletionResult:
        settings = get_settings()
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": request.messages,
            "max_tokens": request.max_tokens or settings.ai_max_output_tokens,
        }
        started = time.monotonic()
        try:
            with self._client() as client:
                response = client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise self._map_http_error(exc) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        self._raise_for_status(response)
        return self._parse_completion(response, latency_ms)

    def test_connection(self) -> ConnectionTestResult:
        started = time.monotonic()
        try:
            with self._client() as client:
                response = client.get("/models")
        except httpx.HTTPError as exc:
            error = self._map_http_error(exc)
            return ConnectionTestResult(
                ok=False, detail=error.message, kind=error.kind
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code in (200, 401, 403, 429):
            if response.status_code == 200:
                return ConnectionTestResult(
                    ok=True,
                    detail="已成功連線供應商並通過驗證",
                    latency_ms=latency_ms,
                )
            if response.status_code in (401, 403):
                return ConnectionTestResult(
                    ok=False,
                    detail="供應商拒絕憑證（API key 無效或無權限）",
                    latency_ms=latency_ms,
                    kind="auth",
                )
            return ConnectionTestResult(
                ok=False,
                detail="供應商速率限制中，請稍後再試",
                latency_ms=latency_ms,
                kind="rate_limit",
            )
        return ConnectionTestResult(
            ok=False,
            detail=f"供應商回應非預期狀態 {response.status_code}",
            latency_ms=latency_ms,
            kind="unavailable",
        )

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise AIProviderError("auth", "AI provider rejected the API key")
        if response.status_code == 429:
            raise AIProviderError("rate_limit", "AI provider rate limit reached")
        if response.status_code >= 500:
            raise AIProviderError(
                "unavailable",
                f"AI provider server error (HTTP {response.status_code})",
            )
        if response.status_code >= 400:
            body = response.text[:300]
            raise AIProviderError(
                "invalid_response",
                f"AI provider rejected the request (HTTP {response.status_code}): {_sanitize(body)}",
            )

    def _parse_completion(self, response: httpx.Response, latency_ms: int) -> CompletionResult:
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(
                "invalid_response",
                f"AI provider returned an unparseable completion: {exc}",
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError(
                "invalid_response", "AI provider returned an empty completion"
            )
        return CompletionResult(
            content=content,
            model=data.get("model", self._model),
            provider=self.name,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=latency_ms,
        )


def get_ai_provider(transport: Optional[httpx.BaseTransport] = None) -> BaseAIProvider:
    """Build the configured provider.

    ``transport`` is a test hook for offline httpx mocking of the real
    provider; production code never passes it.
    """
    settings = get_settings()
    if settings.ai_provider == "mock":
        return MockAIProvider()
    if settings.ai_provider == "openai_compatible":
        return OpenAICompatibleProvider(
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            base_url=settings.ai_base_url,
            timeout_seconds=settings.ai_timeout_seconds,
            transport=transport,
        )
    raise AIProviderError(
        "not_configured",
        f"Unknown AI_PROVIDER {settings.ai_provider!r}; known: mock, openai_compatible",
    )


def estimate_cost_cents(prompt_tokens: int, completion_tokens: int) -> int:
    """Optional cost estimate from ``AI_COST_PER_1K_TOKENS`` (USD)."""
    settings = get_settings()
    if settings.ai_cost_per_1k_tokens <= 0:
        return 0
    total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
    return int(round(total_tokens * settings.ai_cost_per_1k_tokens / 10.0))
