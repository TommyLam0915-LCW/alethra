from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.api import api_router
from app.api.health import router as health_router
from app.lib.request_context import (
    REQUEST_ID_HEADER,
    RequestIDMiddleware,
    install_error_handlers,
)
from app.models import *

settings = get_settings()

# Schema is owned by Alembic. Import-time ``create_all`` can add a new table but
# cannot add a column to an existing one, which left the 2026-11-01 candidate
# database half-migrated (later tables present, ``volume_decimal`` missing)
# while alembic_version stayed behind. Startup must never mutate schema.
app = FastAPI(
    title="Alethra",
    description="Ideas, tested. Decisions, yours. — Investment research workbench",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Without this the browser cannot read the correlation id on a cross-origin
    # API response, so the customer would have a reference they could not copy.
    expose_headers=[REQUEST_ID_HEADER],
)

# Added after CORS so it wraps it: even a CORS preflight carries a request id.
app.add_middleware(RequestIDMiddleware)
install_error_handlers(app)

app.include_router(health_router)
app.include_router(api_router, prefix="/api")


@app.get("/")
def root():
    return {
        "name": "Alethra",
        "tagline": "Ideas, tested. Decisions, yours.",
        "version": "1.0.0",
        "status": "running",
        "disclaimer": "Research tool, not investment advice."
    }
