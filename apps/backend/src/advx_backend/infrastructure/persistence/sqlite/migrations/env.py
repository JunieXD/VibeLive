from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, event, pool

from advx_backend.infrastructure.persistence.sqlite.models import Base
from advx_backend.infrastructure.persistence.sqlite.pragmas import (
    configure_sqlite_connection,
)

config = context.config
target_metadata = Base.metadata

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    event.listen(
        connectable,
        "connect",
        lambda connection, record: configure_sqlite_connection(
            connection,
            record,
            busy_timeout_ms=config.attributes["busy_timeout_ms"],
        ),
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
