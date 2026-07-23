"""Separate automated fixtures from Stage 5 human quality review.

Revision ID: 20260719_04
Revises: 20260718_03
Create Date: 2026-07-19 14:30:00+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_04"
down_revision: str | None = "20260718_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("media_assets")}
    if "evaluation_scope" not in columns:
        op.add_column(
            "media_assets",
            sa.Column(
                "evaluation_scope",
                sa.String(length=32),
                nullable=False,
                server_default="unclassified",
            ),
        )
    if "fixture_type" not in columns:
        op.add_column(
            "media_assets",
            sa.Column(
                "fixture_type",
                sa.String(length=32),
                nullable=False,
                server_default="unknown",
            ),
        )
    if "human_review_eligible" not in columns:
        op.add_column(
            "media_assets",
            sa.Column(
                "human_review_eligible",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    op.execute(
        sa.text(
            """
            UPDATE media_assets
            SET evaluation_scope = 'automated_pipeline',
                fixture_type = 'synthetic_color',
                human_review_eligible = false
            WHERE display_filename LIKE 'supported-__.mp4'
               OR id IN (
                    SELECT asset_id FROM media_tasks
                    WHERE run_id LIKE 'stage5-e2e-%'
               )
            """
        )
    )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("media_assets")}
    for name in ("human_review_eligible", "fixture_type", "evaluation_scope"):
        if name in columns:
            op.drop_column("media_assets", name)
