"""Create deterministic business data and MCP governance tables.

Revision ID: 20260718_01
Revises:
Create Date: 2026-07-18 00:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db import models  # noqa: F401
from app.db.base import Base

revision: str = "20260718_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        "stores",
        "principals",
        "products",
        "skus",
        "inventories",
        "orders",
        "order_items",
        "logistics",
        "refunds",
        "sales_daily",
        "campaigns",
        "knowledge_documents",
        "knowledge_chunks",
        "sops",
        "tool_call_audits",
        "idempotency_records",
        "seed_manifests",
    ):
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("principal_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("action_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("id", "action_digest", name="uq_approval_action_digest"),
    )
    op.create_index("ix_approvals_principal_tool", "approvals", ["principal_id", "tool_name"])


def downgrade() -> None:
    for table_name in (
        "seed_manifests",
        "idempotency_records",
        "approvals",
        "tool_call_audits",
        "sops",
        "knowledge_chunks",
        "knowledge_documents",
        "campaigns",
        "sales_daily",
        "refunds",
        "logistics",
        "order_items",
        "orders",
        "inventories",
        "skus",
        "products",
        "principals",
        "stores",
    ):
        op.drop_table(table_name)
