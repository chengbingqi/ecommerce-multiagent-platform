"""Add immutable development ground-truth annotations for Stage 5.

Revision ID: 20260719_05
Revises: 20260719_04
Create Date: 2026-07-19 22:00:00+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_05"
down_revision: str | None = "20260719_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "media_ground_truth_annotations" not in tables:
        op.create_table(
            "media_ground_truth_annotations",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("asset_id", sa.String(length=36), nullable=False),
            sa.Column("source_transcript_id", sa.String(length=36), nullable=False),
            sa.Column("annotation_version", sa.String(length=64), nullable=False),
            sa.Column("annotator_id", sa.String(length=64), nullable=False),
            sa.Column("source_authorized", sa.Boolean(), nullable=False),
            sa.Column("annotation_digest", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"]),
            sa.ForeignKeyConstraint(["annotator_id"], ["principals.id"]),
            sa.ForeignKeyConstraint(["source_transcript_id"], ["transcripts.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "asset_id",
                "annotation_version",
                name="uq_media_ground_truth_asset_version",
            ),
        )
        op.create_index(
            "ix_media_ground_truth_asset_created",
            "media_ground_truth_annotations",
            ["asset_id", "created_at"],
        )
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "product_ground_truth_segments" not in tables:
        op.create_table(
            "product_ground_truth_segments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("annotation_id", sa.String(length=36), nullable=False),
            sa.Column("product_segment_id", sa.String(length=80), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("product_name", sa.String(length=160), nullable=False),
            sa.Column("start_ms", sa.Integer(), nullable=False),
            sa.Column("end_ms", sa.Integer(), nullable=False),
            sa.Column("corrected_transcript", sa.Text(), nullable=False),
            sa.Column("annotator_id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["annotation_id"], ["media_ground_truth_annotations.id"]),
            sa.ForeignKeyConstraint(["annotator_id"], ["principals.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "annotation_id",
                "ordinal",
                name="uq_product_ground_truth_annotation_ordinal",
            ),
            sa.UniqueConstraint(
                "annotation_id",
                "product_segment_id",
                name="uq_product_ground_truth_annotation_segment",
            ),
        )
        op.create_index(
            "ix_product_ground_truth_annotation",
            "product_ground_truth_segments",
            ["annotation_id", "ordinal"],
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "product_ground_truth_segments" in tables:
        op.drop_table("product_ground_truth_segments")
    if "media_ground_truth_annotations" in tables:
        op.drop_table("media_ground_truth_annotations")
