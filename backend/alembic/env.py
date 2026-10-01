import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

from config import SQLITE_PATH
from models import load_all_models
from models.base import BaseModel

config = context.config

sys.path.append(f"{Path(__file__).parent.parent.resolve()}")

load_all_models()
target_metadata = BaseModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=f"sqlite:///{SQLITE_PATH}",
        target_metadata=target_metadata,
        render_as_batch=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(f"sqlite:///{SQLITE_PATH}")
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
