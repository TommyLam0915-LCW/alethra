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


# -- Auto-learning scheduler ------------------------------------------------

_learning_scheduler = None
_auto_collection_scheduler = None


@app.on_event("startup")
def start_learning_scheduler():
    global _learning_scheduler
    if settings.enable_learning_scheduler:
        from app.workers.learning_scheduler import create_learning_scheduler

        _learning_scheduler = create_learning_scheduler()
        _learning_scheduler.start()
        import logging
        logging.getLogger(__name__).info("Auto-learning scheduler started")


@app.on_event("startup")
def start_auto_collection_scheduler():
    global _auto_collection_scheduler
    if settings.auto_collection_enabled:
        from app.services.auto_collection import init_scheduler
        import os

        database_url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/ai_investment_lab')
        init_scheduler(
            database_url=database_url,
            enabled=True,
            interval_hours=settings.auto_collection_interval_hours,
        )
        import logging
        logging.getLogger(__name__).info(f"Auto-collection scheduler started (every {settings.auto_collection_interval_hours} hours)")


@app.on_event("shutdown")
def stop_learning_scheduler():
    if _learning_scheduler:
        _learning_scheduler.shutdown(wait=False)
        import logging
        logging.getLogger(__name__).info("Auto-learning scheduler stopped")


@app.on_event("shutdown")
def stop_auto_collection_scheduler():
    from app.services.auto_collection import stop_scheduler
    stop_scheduler()
    import logging
    logging.getLogger(__name__).info("Auto-collection scheduler stopped")


@app.get("/")
def root():
    return {
        "name": "Alethra",
        "tagline": "Ideas, tested. Decisions, yours.",
        "version": "1.0.0",
        "status": "running",
        "disclaimer": "Research tool, not investment advice."
    }
