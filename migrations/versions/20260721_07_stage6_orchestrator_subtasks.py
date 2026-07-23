"""Add durable Stage 6 orchestrator subtasks.

Revision ID: 20260721_07
Revises: 20260719_06
Create Date: 2026-07-21 14:30:00+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_07"
down_revision: str | None = "20260719_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orchestrator_subtasks",
        sa.Column("subtask_id", sa.String(length=80), primary_key=True),
        sa.Column("parent_run_id", sa.String(length=80), nullable=False),
        sa.Column("child_run_id", sa.String(length=80), nullable=True),
        sa.Column("external_task_id", sa.String(length=80), nullable=True),
        sa.Column("agent_type", sa.String(length=32), nullable=False),
        sa.Column("dependency_ids", sa.JSON(), nullable=False),
        sa.Column("required_inputs", sa.JSON(), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_ref", sa.String(length=240), nullable=True),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("expected_output_schema", sa.String(length=80), nullable=False),
        sa.Column("tool_scope", sa.JSON(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("retry_policy", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("approval_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_run_id"], ["agent_runs.run_id"]),
        sa.UniqueConstraint(
            "parent_run_id",
            "idempotency_key",
            name="uq_orchestrator_subtask_idempotency",
        ),
    )
    op.create_index(
        "ix_orchestrator_subtasks_parent_status",
        "orchestrator_subtasks",
        ["parent_run_id", "status"],
    )
    op.create_index(
        "ix_orchestrator_subtasks_child_run",
        "orchestrator_subtasks",
        ["child_run_id"],
    )
    op.create_index(
        "ix_orchestrator_subtasks_external_task",
        "orchestrator_subtasks",
        ["external_task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_orchestrator_subtasks_external_task", table_name="orchestrator_subtasks")
    op.drop_index("ix_orchestrator_subtasks_child_run", table_name="orchestrator_subtasks")
    op.drop_index("ix_orchestrator_subtasks_parent_status", table_name="orchestrator_subtasks")
    op.drop_table("orchestrator_subtasks")
