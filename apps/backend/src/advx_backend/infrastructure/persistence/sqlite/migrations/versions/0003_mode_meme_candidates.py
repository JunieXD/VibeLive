"""Persist ModeMeme candidates and namespace settings.

Revision ID: 0003_mode_meme_candidates
Revises: 0002_room_runtime
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_mode_meme_candidates"
down_revision: str | None = "0002_room_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mode_meme_candidates",
        sa.Column("candidate_id", sa.Text(), nullable=False),
        sa.Column("room_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("audience_epoch", sa.Integer(), nullable=False),
        sa.Column("observation_id", sa.Text(), nullable=False),
        sa.Column("mode_namespace", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("evidence_event_ids_json", sa.Text(), nullable=False),
        sa.Column("evidence_frame_indexes_json", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("result_meme_id", sa.Text(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "audience_epoch >= 1",
            name="ck_mode_meme_candidates_audience_epoch_positive",
        ),
        sa.CheckConstraint(
            "outcome IN ('pending', 'accepted', 'rejected')",
            name="ck_mode_meme_candidates_outcome_allowed",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_mode_meme_candidates_created_at_nonnegative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_mode_meme_candidates_updated_after_created",
        ),
        sa.ForeignKeyConstraint(
            ["result_meme_id"],
            ["mode_memes.meme_id"],
            name="fk_mode_meme_candidates_result_meme_id_mode_memes",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["rooms.room_id"],
            name="fk_mode_meme_candidates_room_id_rooms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["session_records.session_id"],
            name="fk_mode_meme_candidates_session_id_session_records",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "candidate_id",
            name="pk_mode_meme_candidates",
        ),
    )
    op.create_index(
        "ix_mode_meme_candidates_namespace_outcome_created",
        "mode_meme_candidates",
        ["mode_namespace", "outcome", "created_at_ms"],
    )
    op.create_table(
        "mode_meme_settings",
        sa.Column("mode_namespace", sa.Text(), nullable=False),
        sa.Column("auto_ingest_enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_mode_meme_settings_revision_positive",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_mode_meme_settings_created_at_nonnegative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_mode_meme_settings_updated_after_created",
        ),
        sa.PrimaryKeyConstraint(
            "mode_namespace",
            name="pk_mode_meme_settings",
        ),
    )


def downgrade() -> None:
    op.drop_table("mode_meme_settings")
    op.drop_index(
        "ix_mode_meme_candidates_namespace_outcome_created",
        table_name="mode_meme_candidates",
    )
    op.drop_table("mode_meme_candidates")
