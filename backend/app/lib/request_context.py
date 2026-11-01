"""Give a failed request an id the customer and the operator can share.

The trial instance answered a database outage with a bare
``Internal Server Error`` and no identifier, so a report could not be tied to
a log line or to another report of the same incident. Each request now gets a
generated id (never accepted from the caller), the response carries it, and
the error log line carries the same value.

Only recognised upstream-connection failures become 503. An unrecognised
programming error stays a 500: using 503 for everything would tell a customer
to retry an operation that will fail the same way again. No ``Retry-After`` is
sent because this service cannot know a recovery time it has not measured.

A missing column/table (SQLSTATE 42703/42P01) is the one other case that gets
its own 503 code: the request is well-formed and the database is reachable, so
neither a generic 500 nor ``database_unavailable`` tells the truth. Only the
statement failed, and only an operator applying the pending migration fixes it.
"""

import logging
import uuid

from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

logger = logging.getLogger("app.request_errors")

REQUEST_ID_HEADER = "X-Request-ID"

DATABASE_UNAVAILABLE_CODE = "database_unavailable"
SCHEMA_INCOMPATIBLE_CODE = "schema_incompatible"
INTERNAL_ERROR_CODE = "internal_error"

# PostgreSQL SQLSTATEs that mean "the database structure does not match the
# running code": undefined_column and undefined_table.
_SCHEMA_PG_CODES = {"42703", "42P01"}


def request_id_for(request) -> str | None:
    state = request.scope.get("state") or {}
    value = state.get("request_id")
    return value if isinstance(value, str) and value else None


class RequestIDMiddleware:
    """Attach one generated id to the request scope and to every response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Generated here, never read from an incoming header: a caller-supplied
        # value could collide with, or impersonate, another request's log line.
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                # Set, not append: a handler that already wrote the header
                # (the unhandled-exception path) must not produce a duplicate.
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)


def _error_response(
    status_code: int, code: str, detail: str, request_id: str | None
) -> JSONResponse:
    body = {"detail": detail, "code": code}
    if request_id:
        body["request_id"] = request_id
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(status_code=status_code, content=body, headers=headers)


async def database_unavailable_handler(request, exc):
    request_id = request_id_for(request)
    logger.exception(
        "request failed code=%s request_id=%s", DATABASE_UNAVAILABLE_CODE, request_id
    )
    return _error_response(
        503,
        DATABASE_UNAVAILABLE_CODE,
        "The service cannot reach its database right now. This is a temporary "
        "service condition, not a problem with the request itself. This "
        "response is an error report, not a confirmation that the request "
        "changed anything.",
        request_id,
    )


async def unhandled_error_handler(request, exc):
    request_id = request_id_for(request)
    logger.exception(
        "request failed code=%s request_id=%s", INTERNAL_ERROR_CODE, request_id
    )
    return _error_response(
        500,
        INTERNAL_ERROR_CODE,
        "The server hit an unexpected error and could not complete this "
        "request. Quote the reference below when reporting it. This response "
        "is an error report, not a confirmation that the request changed "
        "anything.",
        request_id,
    )


async def schema_incompatible_handler(request, exc):
    """503 with its own code when the DB lacks a column/table the code uses."""
    request_id = request_id_for(request)
    logger.exception(
        "request failed code=%s request_id=%s", SCHEMA_INCOMPATIBLE_CODE, request_id
    )
    return _error_response(
        503,
        SCHEMA_INCOMPATIBLE_CODE,
        "The service's database schema does not match the running application, "
        "so this request could not be executed. Quote the reference below when "
        "reporting it. This is an operator-side condition, not a problem with "
        "the request itself, and this response is not a confirmation that the "
        "request changed anything.",
        request_id,
    )


async def programming_error_handler(request, exc):
    """Route structural SQL errors to their own class; everything else is 500."""
    pgcode = getattr(getattr(exc, "orig", None), "pgcode", None)
    if pgcode in _SCHEMA_PG_CODES:
        return await schema_incompatible_handler(request, exc)
    return await unhandled_error_handler(request, exc)


def install_error_handlers(app) -> None:
    app.add_exception_handler(OperationalError, database_unavailable_handler)
    app.add_exception_handler(InterfaceError, database_unavailable_handler)
    app.add_exception_handler(PoolTimeoutError, database_unavailable_handler)
    app.add_exception_handler(ProgrammingError, programming_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
