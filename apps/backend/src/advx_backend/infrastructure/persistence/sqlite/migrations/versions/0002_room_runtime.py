"""Add Room shared-brain and runtime persistence.

Revision ID: 0002_room_runtime
Revises: 0001_initial
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_room_runtime"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rooms",
        sa.Column("room_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint("state IN ('active', 'cleared')", name="ck_rooms_state_allowed"),
        sa.CheckConstraint("revision >= 0", name="ck_rooms_revision_nonnegative"),
        sa.CheckConstraint("created_at_ms >= 0", name="ck_rooms_created_at_nonnegative"),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms", name="ck_rooms_updated_after_created"
        ),
        sa.PrimaryKeyConstraint("room_id", name="pk_rooms"),
    )
    with op.batch_alter_table("session_records") as batch:
        batch.add_column(sa.Column("room_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("state", sa.Text(), nullable=True))
        batch.add_column(sa.Column("audience_epoch", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("active_config_hash", sa.Text(), nullable=True))
        batch.add_column(sa.Column("recovery_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("client_request_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("client_request_hash", sa.Text(), nullable=True))
        batch.create_check_constraint(
            "ck_session_records_audience_epoch_nonnegative",
            "audience_epoch IS NULL OR audience_epoch >= 0",
        )
        batch.create_check_constraint(
            "ck_session_records_state_allowed",
            "state IS NULL OR state IN "
            "('starting', 'running', 'paused', 'stopping', 'stopped', 'failed')",
        )
        batch.create_foreign_key(
            "fk_session_records_room_id_rooms",
            "rooms",
            ["room_id"],
            ["room_id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_session_records_client_request_id", ["client_request_id"]
        )
        batch.create_index(
            "ix_session_records_room_state_ended_at_ms",
            ["room_id", "state", "ended_at_ms"],
        )

    op.create_table(
        "session_runtime_revisions",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("apply_id", sa.Text(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("canonical_spec_json", sa.Text(), nullable=False),
        sa.Column("diff_summary_json", sa.Text(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_session_runtime_revisions_revision_positive"),
        sa.CheckConstraint(
            "base_revision >= 0",
            name="ck_session_runtime_revisions_base_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'committed', 'rejected', 'rolled_back')",
            name="ck_session_runtime_revisions_status_allowed",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_session_runtime_revisions_created_at_nonnegative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_session_runtime_revisions_updated_after_created",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["session_records.session_id"],
            name="fk_session_runtime_revisions_session_id_session_records",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "session_id", "revision", name="pk_session_runtime_revisions"
        ),
        sa.UniqueConstraint(
            "session_id", "apply_id", name="uq_runtime_revision_session_apply"
        ),
    )
    op.create_index(
        "ix_runtime_revision_session_config_hash",
        "session_runtime_revisions",
        ["session_id", "config_hash"],
    )
    op.create_table(
        "session_viewer_instances",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("viewer_instance_id", sa.Text(), nullable=False),
        sa.Column("persona_id", sa.Text(), nullable=False),
        sa.Column("persona_revision", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("micro_variant_json", sa.Text(), nullable=False),
        sa.Column("created_epoch", sa.Integer(), nullable=False),
        sa.Column("removed_epoch", sa.Integer(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "persona_revision >= 1",
            name="ck_session_viewer_instances_persona_revision_positive",
        ),
        sa.CheckConstraint(
            "ordinal >= 0", name="ck_session_viewer_instances_ordinal_nonnegative"
        ),
        sa.CheckConstraint(
            "created_epoch >= 0",
            name="ck_session_viewer_instances_created_epoch_nonnegative",
        ),
        sa.CheckConstraint(
            "removed_epoch IS NULL OR removed_epoch >= created_epoch",
            name="ck_session_viewer_instances_removed_after_created",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'removed')",
            name="ck_session_viewer_instances_state_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["session_records.session_id"],
            name="fk_session_viewer_instances_session_id_session_records",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "session_id",
            "viewer_instance_id",
            name="pk_session_viewer_instances",
        ),
    )
    op.create_index(
        "ix_session_viewer_instances_session_state_viewer",
        "session_viewer_instances",
        ["session_id", "state", "viewer_instance_id"],
    )
    op.create_index(
        "ix_session_viewer_instances_session_persona_ordinal",
        "session_viewer_instances",
        ["session_id", "persona_id", "ordinal"],
    )
    op.create_table(
        "room_events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("room_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("audience_epoch", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("occurred_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_room_events_sequence_nonnegative"),
        sa.CheckConstraint(
            "audience_epoch >= 0", name="ck_room_events_audience_epoch_nonnegative"
        ),
        sa.CheckConstraint(
            "occurred_at_ms >= 0", name="ck_room_events_occurred_at_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.room_id"], name="fk_room_events_room_id_rooms", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["session_records.session_id"],
            name="fk_room_events_session_id_session_records",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_room_events"),
        sa.UniqueConstraint(
            "room_id",
            "session_id",
            "sequence",
            name="uq_room_events_room_session_sequence",
        ),
    )
    op.create_index(
        "ix_room_events_room_occurred_at_ms", "room_events", ["room_id", "occurred_at_ms"]
    )
    op.create_table(
        "room_long_term_memories",
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("room_id", sa.Text(), nullable=False),
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
            name="ck_room_long_term_memories_importance_range",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_room_long_term_memories_confidence_range",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'superseded', 'revoked')",
            name="ck_room_long_term_memories_state_allowed",
        ),
        sa.CheckConstraint(
            "revision >= 1", name="ck_room_long_term_memories_revision_positive"
        ),
        sa.CheckConstraint(
            "superseded_by IS NULL OR superseded_by != memory_id",
            name="ck_room_long_term_memories_not_self_superseded",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_room_long_term_memories_created_at_nonnegative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_room_long_term_memories_updated_after_created",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["rooms.room_id"],
            name="fk_room_long_term_memories_room_id_rooms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["room_long_term_memories.memory_id"],
            name="fk_room_long_term_memories_superseded_by_room_long_term_memories",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("memory_id", name="pk_room_long_term_memories"),
    )
    op.create_index(
        "ix_room_long_term_memories_room_state_updated",
        "room_long_term_memories",
        ["room_id", "state", "updated_at_ms"],
    )
    op.create_index(
        "ix_room_long_term_memories_retrieval",
        "room_long_term_memories",
        ["room_id", "state", "importance", "last_recalled_at_ms"],
    )
    op.create_table(
        "room_memory_heads",
        sa.Column("room_id", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_room_memory_heads_revision_nonnegative"),
        sa.CheckConstraint(
            "updated_at_ms >= 0", name="ck_room_memory_heads_updated_at_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["rooms.room_id"],
            name="fk_room_memory_heads_room_id_rooms",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("room_id", name="pk_room_memory_heads"),
    )
    op.create_table(
        "room_memory_evidence",
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("occurred_at_ms", sa.Integer(), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "occurred_at_ms >= 0",
            name="ck_room_memory_evidence_occurred_at_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["room_events.event_id"],
            name="fk_room_memory_evidence_event_id_room_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["room_long_term_memories.memory_id"],
            name="fk_room_memory_evidence_memory_id_room_long_term_memories",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "memory_id", "event_id", name="pk_room_memory_evidence"
        ),
    )
    op.create_index(
        "ix_room_memory_evidence_event_memory",
        "room_memory_evidence",
        ["event_id", "memory_id"],
    )
    op.create_table(
        "room_memory_candidates",
        sa.Column("candidate_id", sa.Text(), nullable=False),
        sa.Column("room_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("candidate_type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column("evidence_event_ids_json", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("result_memory_id", sa.Text(), nullable=True),
        sa.Column("decision_json", sa.Text(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "base_revision >= 0",
            name="ck_room_memory_candidates_base_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "outcome IN ('pending', 'created', 'merged', 'replaced', 'rejected', 'stale')",
            name="ck_room_memory_candidates_outcome_allowed",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_room_memory_candidates_created_at_nonnegative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_room_memory_candidates_updated_after_created",
        ),
        sa.ForeignKeyConstraint(
            ["result_memory_id"],
            ["room_long_term_memories.memory_id"],
            name="fk_room_memory_candidates_result_memory_id_room_long_term_memories",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["rooms.room_id"],
            name="fk_room_memory_candidates_room_id_rooms",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("candidate_id", name="pk_room_memory_candidates"),
        sa.UniqueConstraint(
            "room_id",
            "idempotency_key",
            name="uq_room_memory_candidates_room_idempotency",
        ),
    )
    op.create_table(
        "mode_memes",
        sa.Column("meme_id", sa.Text(), nullable=False),
        sa.Column("mode_namespace", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("intensity", sa.Float(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("source_json", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "intensity >= 0.0 AND intensity <= 1.0",
            name="ck_mode_memes_intensity_range",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'disabled', 'archived', 'revoked')",
            name="ck_mode_memes_state_allowed",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_mode_memes_revision_positive"),
        sa.CheckConstraint("created_at_ms >= 0", name="ck_mode_memes_created_at_nonnegative"),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms", name="ck_mode_memes_updated_after_created"
        ),
        sa.PrimaryKeyConstraint("meme_id", name="pk_mode_memes"),
    )
    op.create_index(
        "ix_mode_memes_namespace_state_updated",
        "mode_memes",
        ["mode_namespace", "state", "updated_at_ms"],
    )
    op.create_table(
        "mode_meme_events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("meme_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("previous_revision", sa.Integer(), nullable=False),
        sa.Column("new_revision", sa.Integer(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "action IN ('created', 'edited', 'revoked', 'restored', 'disabled', 'archived')",
            name="ck_mode_meme_events_action_allowed",
        ),
        sa.CheckConstraint(
            "previous_revision >= 0",
            name="ck_mode_meme_events_previous_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "new_revision >= 1", name="ck_mode_meme_events_new_revision_positive"
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0", name="ck_mode_meme_events_created_at_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["meme_id"],
            ["mode_memes.meme_id"],
            name="fk_mode_meme_events_meme_id_mode_memes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_mode_meme_events"),
    )


def downgrade() -> None:
    op.drop_table("mode_meme_events")
    op.drop_index("ix_mode_memes_namespace_state_updated", table_name="mode_memes")
    op.drop_table("mode_memes")
    op.drop_table("room_memory_candidates")
    op.drop_index(
        "ix_room_memory_evidence_event_memory", table_name="room_memory_evidence"
    )
    op.drop_table("room_memory_evidence")
    op.drop_table("room_memory_heads")
    op.drop_index(
        "ix_room_long_term_memories_retrieval", table_name="room_long_term_memories"
    )
    op.drop_index(
        "ix_room_long_term_memories_room_state_updated",
        table_name="room_long_term_memories",
    )
    op.drop_table("room_long_term_memories")
    op.drop_index("ix_room_events_room_occurred_at_ms", table_name="room_events")
    op.drop_table("room_events")
    op.drop_index(
        "ix_session_viewer_instances_session_persona_ordinal",
        table_name="session_viewer_instances",
    )
    op.drop_index(
        "ix_session_viewer_instances_session_state_viewer",
        table_name="session_viewer_instances",
    )
    op.drop_table("session_viewer_instances")
    op.drop_index(
        "ix_runtime_revision_session_config_hash",
        table_name="session_runtime_revisions",
    )
    op.drop_table("session_runtime_revisions")
    with op.batch_alter_table("session_records") as batch:
        batch.drop_index("ix_session_records_room_state_ended_at_ms")
        batch.drop_constraint("uq_session_records_client_request_id", type_="unique")
        batch.drop_constraint("fk_session_records_room_id_rooms", type_="foreignkey")
        batch.drop_constraint("ck_session_records_state_allowed", type_="check")
        batch.drop_constraint(
            "ck_session_records_audience_epoch_nonnegative", type_="check"
        )
        batch.drop_column("client_request_hash")
        batch.drop_column("client_request_id")
        batch.drop_column("recovery_json")
        batch.drop_column("active_config_hash")
        batch.drop_column("audience_epoch")
        batch.drop_column("state")
        batch.drop_column("room_id")
    op.drop_table("rooms")
