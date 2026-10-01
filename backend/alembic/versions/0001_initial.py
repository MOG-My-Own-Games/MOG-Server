"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(length=255), nullable=False, unique=True, index=True),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="user"),
        sa.Column("last_login", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )

    op.create_table(
        "libraries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("root_path", sa.String(length=1000), nullable=False, unique=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )

    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("library_id", sa.Integer(), sa.ForeignKey("libraries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fs_name", sa.String(length=450), nullable=False),
        sa.Column("name", sa.String(length=400), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("igdb_id", sa.Integer(), nullable=True),
        sa.Column("igdb_metadata", sa.JSON(), nullable=True),
        sa.Column("sgdb_id", sa.Integer(), nullable=True),
        sa.Column("cover_path", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )

    op.create_table(
        "install_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="detecting"),
        sa.Column("installer_path", sa.String(length=1000), nullable=True),
        sa.Column("source_path", sa.String(length=1000), nullable=True),
        sa.Column("proton_build", sa.String(length=255), nullable=True),
        sa.Column("cache_path", sa.String(length=1000), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("phase", sa.String(length=32), nullable=True),
        sa.Column("phase_detail", sa.String(length=1000), nullable=True),
        sa.Column("auto_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("manual_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_status", sa.String(length=32), nullable=True),
        sa.Column("auto_detail", sa.String(length=1000), nullable=True),
        sa.Column("vnc_url", sa.String(length=500), nullable=True),
        sa.Column("vnc_web_port", sa.Integer(), nullable=True),
        sa.Column("bytes_written", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index("ix_install_sessions_game", "install_sessions", ["game_id"])
    op.create_index("ix_install_sessions_user", "install_sessions", ["user_id"])
    op.create_index("ix_install_sessions_state", "install_sessions", ["state"])


def downgrade() -> None:
    op.drop_table("install_sessions")
    op.drop_table("games")
    op.drop_table("libraries")
    op.drop_table("users")
