"""Alembic environment.

The database URL comes from the environment, resolved the same way the app does
(`tracker.config.database_url`) — never from a committed file. A plain
`postgresql://` URL is upgraded to the psycopg (v3) driver so SQLAlchemy uses the
same driver the app does.

Migrations are hand-written raw SQL (no ORM models), so target_metadata is None
and autogenerate is intentionally unused.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from tracker.config import database_url

config = context.config

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass


def _database_url() -> str:
    url = database_url()
    if not url:
        raise RuntimeError(
            "No database configured: set DATABASE_URL "
            "(e.g. postgresql://tracker:tracker@localhost:5432/tracker)."
        )
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    return url


# The config is a ConfigParser: a literal % (from a URL-encoded password) must be doubled.
config.set_main_option("sqlalchemy.url", _database_url().replace("%", "%%"))

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
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
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
