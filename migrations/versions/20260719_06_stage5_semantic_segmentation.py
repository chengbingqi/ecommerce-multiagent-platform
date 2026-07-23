"""Add auditable ASR and semantic segmentation fields for Stage 5.

Revision ID: 20260719_06
Revises: 20260719_05
Create Date: 2026-07-19 23:45:00+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_06"
down_revision: str | None = "20260719_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    transcript_columns = _columns("transcripts")
    for column in (
        sa.Column("asr_config_version", sa.String(length=64), nullable=True),
        sa.Column("correction_version", sa.String(length=64), nullable=True),
        sa.Column("correction_metadata", sa.JSON(), nullable=True),
    ):
        if column.name not in transcript_columns:
            op.add_column("transcripts", column)

    segment_columns = _columns("transcript_segments")
    for column in (
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("corrected_text", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(7, 6), nullable=True),
        sa.Column("word_timestamps", sa.JSON(), nullable=True),
    ):
        if column.name not in segment_columns:
            op.add_column("transcript_segments", column)

    clip_columns = _columns("clip_candidates")
    for column in (
        sa.Column("product_label", sa.String(length=160), nullable=True),
        sa.Column("predicted_start_ms", sa.Integer(), nullable=True),
        sa.Column("predicted_end_ms", sa.Integer(), nullable=True),
        sa.Column("snapped_start_ms", sa.Integer(), nullable=True),
        sa.Column("snapped_end_ms", sa.Integer(), nullable=True),
        sa.Column("boundary_evidence", sa.JSON(), nullable=True),
        sa.Column("segmentation_version", sa.String(length=64), nullable=True),
        sa.Column("asr_config_version", sa.String(length=64), nullable=True),
    ):
        if column.name not in clip_columns:
            op.add_column("clip_candidates", column)


def downgrade() -> None:
    # Forward-only in Stage 5 acceptance. This remains for Alembic completeness.
    for name in (
        "asr_config_version",
        "segmentation_version",
        "boundary_evidence",
        "snapped_end_ms",
        "snapped_start_ms",
        "predicted_end_ms",
        "predicted_start_ms",
        "product_label",
    ):
        if name in _columns("clip_candidates"):
            op.drop_column("clip_candidates", name)
    for name in ("word_timestamps", "confidence", "corrected_text", "raw_text"):
        if name in _columns("transcript_segments"):
            op.drop_column("transcript_segments", name)
    for name in ("correction_metadata", "correction_version", "asr_config_version"):
        if name in _columns("transcripts"):
            op.drop_column("transcripts", name)
