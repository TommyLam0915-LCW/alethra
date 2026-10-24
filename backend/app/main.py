from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.database import engine, Base
from app.api import api_router
from app.models import *

settings = get_settings()

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Alethra",
    description="Ideas, tested. Decisions, yours. — Investment research workbench",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list + (
        ["http://localhost:3001", "http://localhost:3002", "http://localhost:3003", "http://localhost:3004"]
        if settings.app_env == "development" else []
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.get("/health")
def health():
    return {"status": "healthy"}
