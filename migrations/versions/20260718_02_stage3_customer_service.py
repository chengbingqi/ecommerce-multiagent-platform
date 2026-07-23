"""Add durable customer service Agent runs and approval bindings.

Revision ID: 20260718_02
Revises: 20260718_01
Create Date: 2026-07-18 00:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_02"
down_revision: str | None = "20260718_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(length=80), primary_key=True),
        sa.Column("thread_id", sa.String(length=80), nullable=False),
        sa.Column("principal_id", sa.String(length=64), nullable=False),
        sa.Column("agent_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("intent", sa.String(length=40), nullable=True),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("need_human", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("approval_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"]),
    )
    op.create_index("ix_agent_runs_thread_updated", "agent_runs", ["thread_id", "updated_at"])
    op.create_index("ix_agent_runs_principal_created", "agent_runs", ["principal_id", "created_at"])
    op.create_index("ix_agent_runs_trace", "agent_runs", ["trace_id"])

    with op.batch_alter_table("approvals") as batch:
        batch.add_column(sa.Column("run_id", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("thread_id", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("decision_by", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("used_at", sa.DateTime(timezone=True), nullable=True))
        batch.alter_column("approved_at", existing_type=sa.DateTime(timezone=True), nullable=True)
        batch.create_index("ix_approvals_run_id", ["run_id"])
        batch.create_index("ix_approvals_thread_id", ["thread_id"])

    op.create_table(
        "customer_conversations",
        sa.Column("thread_id", sa.String(length=80), primary_key=True),
        sa.Column("principal_id", sa.String(length=64), nullable=False),
        sa.Column("last_run_id", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"]),
    )
    op.create_index(
        "ix_customer_conversations_principal", "customer_conversations", ["principal_id"]
    )
    op.create_table(
        "agent_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_summary", sa.JSON(), nullable=False),
        sa.Column("trace_id", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
    )
    op.create_index("ix_agent_events_run_created", "agent_events", ["run_id", "created_at"])
    op.create_index("ix_agent_events_trace", "agent_events", ["trace_id"])
    op.create_table(
        "agent_checkpoint_links",
        sa.Column("thread_id", sa.String(length=80), primary_key=True),
        sa.Column("run_id", sa.String(length=80), nullable=False),
        sa.Column("checkpoint_namespace", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
    )
    op.create_index("ix_checkpoint_links_run", "agent_checkpoint_links", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_checkpoint_links_run", table_name="agent_checkpoint_links")
    op.drop_table("agent_checkpoint_links")
    op.drop_index("ix_agent_events_trace", table_name="agent_events")
    op.drop_index("ix_agent_events_run_created", table_name="agent_events")
    op.drop_table("agent_events")
    op.drop_index("ix_customer_conversations_principal", table_name="customer_conversations")
    op.drop_table("customer_conversations")
    with op.batch_alter_table("approvals") as batch:
        batch.drop_index("ix_approvals_thread_id")
        batch.drop_index("ix_approvals_run_id")
        batch.alter_column("approved_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.drop_column("used_at")
        batch.drop_column("decision_by")
        batch.drop_column("decided_at")
        batch.drop_column("thread_id")
        batch.drop_column("run_id")
    op.drop_index("ix_agent_runs_trace", table_name="agent_runs")
    op.drop_index("ix_agent_runs_principal_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_thread_updated", table_name="agent_runs")
    op.drop_table("agent_runs")
