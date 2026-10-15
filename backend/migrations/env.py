from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import context

from app.config import get_settings
from app.database import Base
from app.models import *  # noqa: F401,F403 — register all models with Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


if settings.database_url.startswith("sqlite"):
    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(element, compiler, **kw):
        return "JSON"

    @compiles(UUID, "sqlite")
    def _compile_uuid_sqlite(element, compiler, **kw):
        return "VARCHAR(36)"


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    if settings.database_url.startswith("sqlite"):
        @event.listens_for(connectable, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
