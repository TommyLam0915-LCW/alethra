import uuid
from typing import Optional

from fastapi import Request


def generate_correlation_id() -> str:
    return f"corr-{uuid.uuid4().hex[:16]}"


def get_or_create_correlation_id(request: Request) -> str:
    corr_id = request.headers.get("X-Correlation-ID")
    if corr_id:
        return corr_id
    return generate_correlation_id()


class CorrelationIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            corr_key = b"x-correlation-id"
            if corr_key not in headers:
                corr_id = generate_correlation_id()
                scope["headers"] = list(scope.get("headers", [])) + [
                    (corr_key, corr_id.encode())
                ]
        await self.app(scope, receive, send)
