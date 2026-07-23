"""Create the initial local persistence schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audience_profiles",
        sa.Column("audience_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("avatar_ref", sa.Text(), nullable=True),
        sa.Column("personality_json", sa.Text(), nullable=False),
        sa.Column("preferences_json", sa.Text(), nullable=False),
        sa.Column("speaking_style_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("preset_id", sa.Text(), nullable=True),
        sa.Column("preset_version", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint("enabled IN (0, 1)", name="ck_audience_profiles_enabled_boolean"),
        sa.CheckConstraint(
            "origin IN ('preset', 'custom')",
            name="ck_audience_profiles_origin_allowed",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_audience_profiles_revision_positive"),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_audience_profiles_created_at_nonnegative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_audience_profiles_updated_after_created",
        ),
        sa.PrimaryKeyConstraint("audience_id", name="pk_audience_profiles"),
    )
    op.create_index(
        "ix_audience_profiles_enabled_updated_at_ms",
        "audience_profiles",
        ["enabled", "updated_at_ms"],
    )

    op.create_table(
        "session_records",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("started_at_ms", sa.Integer(), nullable=False),
        sa.Column("ended_at_ms", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("app_version", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "started_at_ms >= 0",
            name="ck_session_records_started_at_nonnegative",
        ),
        sa.CheckConstraint(
            "ended_at_ms IS NULL OR ended_at_ms >= started_at_ms",
            name="ck_session_records_ended_after_started",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('completed', 'error', 'interrupted')",
            name="ck_session_records_outcome_allowed",
        ),
        sa.CheckConstraint(
            "(ended_at_ms IS NULL AND outcome IS NULL) OR "
            "(ended_at_ms IS NOT NULL AND outcome IS NOT NULL)",
            name="ck_session_records_completion_consistent",
        ),
        sa.PrimaryKeyConstraint("session_id", name="pk_session_records"),
    )
    op.create_index(
        "ix_session_records_ended_at_ms",
        "session_records",
        ["ended_at_ms"],
    )

    op.create_table(
        "audience_memories",
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("audience_id", sa.Text(), nullable=False),
        sa.Column("memory_type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("superseded_by", sa.Text(), nullable=True),
        sa.Column("last_recalled_at_ms", sa.Integer(), nullable=True),
        sa.Column("expires_at_ms", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "importance >= 0.0 AND importance <= 1.0",
            name="ck_audience_memories_importance_range",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_audience_memories_confidence_range",
        ),
        sa.CheckConstraint(
            "origin IN ('extracted', 'user')",
            name="ck_audience_memories_origin_allowed",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'superseded')",
            name="ck_audience_memories_state_allowed",
        ),
        sa.CheckConstraint(
            "superseded_by IS NULL OR superseded_by != memory_id",
            name="ck_audience_memories_not_self_superseded",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_audience_memories_revision_positive"),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_audience_memories_created_at_nonnegative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_audience_memories_updated_after_created",
        ),
        sa.ForeignKeyConstraint(
            ["audience_id"],
            ["audience_profiles.audience_id"],
            name="fk_audience_memories_audience_id_audience_profiles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["audience_memories.memory_id"],
            name="fk_audience_memories_superseded_by_audience_memories",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("memory_id", name="pk_audience_memories"),
    )
    op.create_index(
        "ix_audience_memories_audience_state_updated_at_ms",
        "audience_memories",
        ["audience_id", "state", "updated_at_ms"],
    )
    op.create_index(
        "ix_audience_memories_retrieval",
        "audience_memories",
        ["audience_id", "state", "importance", "last_recalled_at_ms"],
    )

    op.create_table(
        "audience_host_relationships",
        sa.Column("audience_id", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("source_memory_id", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "updated_by IN ('memory', 'user')",
            name="ck_audience_host_relationships_updated_by_allowed",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_audience_host_relationships_revision_positive",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= 0",
            name="ck_audience_host_relationships_updated_at_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["audience_id"],
            ["audience_profiles.audience_id"],
            name="fk_audience_host_relationships_audience_id_audience_profiles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_memory_id"],
            ["audience_memories.memory_id"],
            name="fk_audience_host_relationships_source_memory_id_audience_memories",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("audience_id", name="pk_audience_host_relationships"),
    )

    op.create_table(
        "audience_peer_relationships",
        sa.Column("audience_id", sa.Text(), nullable=False),
        sa.Column("peer_audience_id", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("source_memory_id", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "audience_id != peer_audience_id",
            name="ck_audience_peer_relationships_distinct_audiences",
        ),
        sa.CheckConstraint(
            "updated_by IN ('memory', 'user')",
            name="ck_audience_peer_relationships_updated_by_allowed",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_audience_peer_relationships_revision_positive",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= 0",
            name="ck_audience_peer_relationships_updated_at_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["audience_id"],
            ["audience_profiles.audience_id"],
            name="fk_audience_peer_relationships_audience_id_audience_profiles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["peer_audience_id"],
            ["audience_profiles.audience_id"],
            name="fk_audience_peer_relationships_peer_audience_id_audience_profiles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_memory_id"],
            ["audience_memories.memory_id"],
            name="fk_audience_peer_relationships_source_memory_id_audience_memories",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "audience_id",
            "peer_audience_id",
            name="pk_audience_peer_relationships",
        ),
    )

    op.create_table(
        "memory_evidence",
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("occurred_at_ms", sa.Integer(), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "occurred_at_ms >= 0",
            name="ck_memory_evidence_occurred_at_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["audience_memories.memory_id"],
            name="fk_memory_evidence_memory_id_audience_memories",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["session_records.session_id"],
            name="fk_memory_evidence_session_id_session_records",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "memory_id",
            "session_id",
            "source_event_id",
            name="pk_memory_evidence",
        ),
    )
    op.create_index(
        "ix_memory_evidence_session_event",
        "memory_evidence",
        ["session_id", "source_event_id"],
    )

    op.create_table(
        "session_audiences",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("audience_id", sa.Text(), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("joined_at_ms", sa.Integer(), nullable=False),
        sa.Column("left_at_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "profile_revision >= 1",
            name="ck_session_audiences_profile_revision_positive",
        ),
        sa.CheckConstraint(
            "joined_at_ms >= 0",
            name="ck_session_audiences_joined_at_nonnegative",
        ),
        sa.CheckConstraint(
            "left_at_ms IS NULL OR left_at_ms >= joined_at_ms",
            name="ck_session_audiences_left_after_joined",
        ),
        sa.ForeignKeyConstraint(
            ["audience_id"],
            ["audience_profiles.audience_id"],
            name="fk_session_audiences_audience_id_audience_profiles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["session_records.session_id"],
            name="fk_session_audiences_session_id_session_records",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "session_id",
            "audience_id",
            name="pk_session_audiences",
        ),
    )
    op.create_index(
        "ix_session_audiences_audience_session",
        "session_audiences",
        ["audience_id", "session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_session_audiences_audience_session",
        table_name="session_audiences",
    )
    op.drop_table("session_audiences")
    op.drop_index("ix_memory_evidence_session_event", table_name="memory_evidence")
    op.drop_table("memory_evidence")
    op.drop_table("audience_peer_relationships")
    op.drop_table("audience_host_relationships")
    op.drop_index("ix_audience_memories_retrieval", table_name="audience_memories")
    op.drop_index(
        "ix_audience_memories_audience_state_updated_at_ms",
        table_name="audience_memories",
    )
    op.drop_table("audience_memories")
    op.drop_index("ix_session_records_ended_at_ms", table_name="session_records")
    op.drop_table("session_records")
    op.drop_index(
        "ix_audience_profiles_enabled_updated_at_ms",
        table_name="audience_profiles",
    )
    op.drop_table("audience_profiles")
