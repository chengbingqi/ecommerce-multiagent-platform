"""Add public demo account metadata and run session isolation.

Revision ID: 20260721_09
Revises: 20260721_08
Create Date: 2026-07-21 18:30:00+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_09"
down_revision: str | None = "20260721_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_users",
        sa.Column("account_type", sa.String(length=32), nullable=False, server_default="normal"),
    )
    op.create_index("ix_auth_users_account_type", "auth_users", ["account_type"])
    op.add_column(
        "agent_runs",
        sa.Column("auth_session_digest", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_agent_runs_principal_session_created",
        "agent_runs",
        ["principal_id", "auth_session_digest", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_principal_session_created", table_name="agent_runs")
    op.drop_column("agent_runs", "auth_session_digest")
    op.drop_index("ix_auth_users_account_type", table_name="auth_users")
    op.drop_column("auth_users", "account_type")
