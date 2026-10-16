from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.database import engine, Base
from app.api import api_router
from app.api.health import router as health_router
from app.models import *

settings = get_settings()

Base.metadata.create_all(bind=engine, checkfirst=True)

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
)

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
