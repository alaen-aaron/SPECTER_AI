"""
Alembic environment script, wired for SQLAlchemy 2.0's async engine.

No models are imported into `target_metadata` yet — that begins in
Milestone 2 once `infrastructure/db/models/` exists per SRS §5. This
file's only job in Milestone 1 is to prove `alembic` can connect using
the same `Settings`/`Base` the application itself uses, so Milestone 2
can add `alembic revision --autogenerate` without touching this wiring.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.infrastructure.db.session import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No ORM models exist yet (Milestone 1) — this becomes populated as
# soon as `infrastructure/db/models/` is introduced in Milestone 2.
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
