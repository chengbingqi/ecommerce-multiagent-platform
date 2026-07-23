"""Add Stage 5 controlled media pipeline tables.

Revision ID: 20260718_03
Revises: 20260718_02
Create Date: 2026-07-18 12:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

from app.db import models  # noqa: F401
from app.db.base import Base

revision: str = "20260718_03"
down_revision: str | None = "20260718_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = (
    "live_sessions",
    "media_assets",
    "media_tasks",
    "transcripts",
    "transcript_segments",
    "clip_candidates",
    "clip_reviews",
    "media_task_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    for table_name in reversed(TABLES):
        op.drop_table(table_name)
