from sqlalchemy import create_engine, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import get_settings

settings = get_settings()

if settings.database_url.startswith("sqlite"):
    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(element, compiler, **kw):
        return "JSON"

    @compiles(UUID, "sqlite")
    def _compile_uuid_sqlite(element, compiler, **kw):
        return "VARCHAR(36)"

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
