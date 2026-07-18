"""
Alembic environment script, wired for SQLAlchemy 2.0's async engine.

Imports `infrastructure/db/models` for its side effect of registering
every ORM model onto `Base.metadata`, so `alembic revision
--autogenerate` can see the full Milestone 2 schema (users,
organizations, projects, targets, authorization_records, sessions,
audit_logs, and their membership/invitation tables).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Side-effect import: registers every ORM model onto Base.metadata.
# Required before `--autogenerate` can see the full schema.
import app.infrastructure.db.models  # noqa: F401,E402
from app.core.config import get_settings
from app.infrastructure.db.session import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return str(get_settings().DATABASE_URL)


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL only)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live async DB connection."""
    connectable = create_async_engine(get_url(), future=True)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
