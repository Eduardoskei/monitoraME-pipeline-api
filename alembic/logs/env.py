from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from app.core import log_models
from app.core.orm import LogBase, log_connect_args, log_database_url


config = context.config
target_metadata = LogBase.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=log_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table="alembic_version_logs",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        log_database_url(),
        poolclass=pool.NullPool,
        connect_args=log_connect_args(),
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table="alembic_version_logs",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
