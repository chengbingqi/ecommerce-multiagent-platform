"""Add Stage 7 authentication sessions and security audit metadata.

Revision ID: 20260721_08
Revises: 20260721_07
Create Date: 2026-07-21 16:00:00+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_08"
down_revision: str | None = "20260721_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("principal_id", sa.String(length=64), nullable=False),
        sa.Column("username_normalized", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"]),
        sa.UniqueConstraint("username_normalized", name="uq_auth_users_username_normalized"),
        sa.UniqueConstraint("principal_id", name="uq_auth_users_principal"),
    )
    op.create_index("ix_auth_users_locked", "auth_users", ["locked_until"])

    op.create_table(
        "auth_sessions",
        sa.Column("token_digest", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("csrf_digest", sa.String(length=64), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=48), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["auth_users.id"]),
    )
    op.create_index("ix_auth_sessions_user_active", "auth_sessions", ["user_id", "revoked_at"])
    op.create_index("ix_auth_sessions_expiry", "auth_sessions", ["absolute_expires_at"])

    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("actor_principal_id", sa.String(length=64), nullable=True),
        sa.Column("target_ref", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("client_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_security_audit_actor_created",
        "security_audit_events",
        ["actor_principal_id", "created_at"],
    )
    op.create_index(
        "ix_security_audit_rule_created",
        "security_audit_events",
        ["rule_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_security_audit_rule_created", table_name="security_audit_events")
    op.drop_index("ix_security_audit_actor_created", table_name="security_audit_events")
    op.drop_table("security_audit_events")
    op.drop_index("ix_auth_sessions_expiry", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_active", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_auth_users_locked", table_name="auth_users")
    op.drop_table("auth_users")
