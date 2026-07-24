from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class AudienceProfileRow(Base):
    __tablename__ = "audience_profiles"
    __table_args__ = (
        CheckConstraint("enabled IN (0, 1)", name="enabled_boolean"),
        CheckConstraint("origin IN ('preset', 'custom')", name="origin_allowed"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("created_at_ms >= 0", name="created_at_nonnegative"),
        CheckConstraint("updated_at_ms >= created_at_ms", name="updated_after_created"),
        Index("ix_audience_profiles_enabled_updated_at_ms", "enabled", "updated_at_ms"),
    )

    audience_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_ref: Mapped[str | None] = mapped_column(Text)
    personality_json: Mapped[str] = mapped_column(Text, nullable=False)
    preferences_json: Mapped[str] = mapped_column(Text, nullable=False)
    speaking_style_json: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    preset_id: Mapped[str | None] = mapped_column(Text)
    preset_version: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class AudienceMemoryRow(Base):
    __tablename__ = "audience_memories"
    __table_args__ = (
        CheckConstraint("importance >= 0.0 AND importance <= 1.0", name="importance_range"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_range"),
        CheckConstraint("origin IN ('extracted', 'user')", name="origin_allowed"),
        CheckConstraint("state IN ('active', 'superseded')", name="state_allowed"),
        CheckConstraint(
            "superseded_by IS NULL OR superseded_by != memory_id", name="not_self_superseded"
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("created_at_ms >= 0", name="created_at_nonnegative"),
        CheckConstraint("updated_at_ms >= created_at_ms", name="updated_after_created"),
        Index(
            "ix_audience_memories_audience_state_updated_at_ms",
            "audience_id",
            "state",
            "updated_at_ms",
        ),
        Index(
            "ix_audience_memories_retrieval",
            "audience_id",
            "state",
            "importance",
            "last_recalled_at_ms",
        ),
    )

    memory_id: Mapped[str] = mapped_column(Text, primary_key=True)
    audience_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("audience_profiles.audience_id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    superseded_by: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("audience_memories.memory_id", ondelete="SET NULL"),
    )
    last_recalled_at_ms: Mapped[int | None] = mapped_column(Integer)
    expires_at_ms: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class AudienceHostRelationshipRow(Base):
    __tablename__ = "audience_host_relationships"
    __table_args__ = (
        CheckConstraint("updated_by IN ('memory', 'user')", name="updated_by_allowed"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("updated_at_ms >= 0", name="updated_at_nonnegative"),
    )

    audience_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("audience_profiles.audience_id", ondelete="CASCADE"),
        primary_key=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_memory_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("audience_memories.memory_id", ondelete="SET NULL"),
    )
    updated_by: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class AudiencePeerRelationshipRow(Base):
    __tablename__ = "audience_peer_relationships"
    __table_args__ = (
        CheckConstraint("audience_id != peer_audience_id", name="distinct_audiences"),
        CheckConstraint("updated_by IN ('memory', 'user')", name="updated_by_allowed"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("updated_at_ms >= 0", name="updated_at_nonnegative"),
    )

    audience_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("audience_profiles.audience_id", ondelete="CASCADE"),
        primary_key=True,
    )
    peer_audience_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("audience_profiles.audience_id", ondelete="CASCADE"),
        primary_key=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_memory_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("audience_memories.memory_id", ondelete="SET NULL"),
    )
    updated_by: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class SessionRecordRow(Base):
    __tablename__ = "session_records"
    __table_args__ = (
        CheckConstraint("started_at_ms >= 0", name="started_at_nonnegative"),
        CheckConstraint(
            "ended_at_ms IS NULL OR ended_at_ms >= started_at_ms",
            name="ended_after_started",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('completed', 'error', 'interrupted')",
            name="outcome_allowed",
        ),
        CheckConstraint(
            "(ended_at_ms IS NULL AND outcome IS NULL) OR "
            "(ended_at_ms IS NOT NULL AND outcome IS NOT NULL)",
            name="completion_consistent",
        ),
        CheckConstraint(
            "audience_epoch IS NULL OR audience_epoch >= 0",
            name="audience_epoch_nonnegative",
        ),
        CheckConstraint(
            "state IS NULL OR state IN "
            "('starting', 'running', 'paused', 'stopping', 'stopped', 'failed')",
            name="state_allowed",
        ),
        Index("ix_session_records_ended_at_ms", "ended_at_ms"),
        Index("ix_session_records_room_state_ended_at_ms", "room_id", "state", "ended_at_ms"),
        UniqueConstraint("client_request_id", name="uq_session_records_client_request_id"),
    )

    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    room_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("rooms.room_id", ondelete="CASCADE"),
    )
    state: Mapped[str | None] = mapped_column(Text)
    audience_epoch: Mapped[int | None] = mapped_column(Integer)
    active_config_hash: Mapped[str | None] = mapped_column(Text)
    recovery_json: Mapped[str | None] = mapped_column(Text)
    session_seed: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_creation_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    target_concurrent_viewers: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    population_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    controller_state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    client_request_id: Mapped[str | None] = mapped_column(Text)
    client_request_hash: Mapped[str | None] = mapped_column(Text)
    started_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    ended_at_ms: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str | None] = mapped_column(Text)
    app_version: Mapped[str] = mapped_column(Text, nullable=False)


class MemoryEvidenceRow(Base):
    __tablename__ = "memory_evidence"
    __table_args__ = (
        CheckConstraint("occurred_at_ms >= 0", name="occurred_at_nonnegative"),
        Index("ix_memory_evidence_session_event", "session_id", "source_event_id"),
    )

    memory_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("audience_memories.memory_id", ondelete="CASCADE"),
        primary_key=True,
    )
    session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("session_records.session_id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False)


class SessionAudienceRow(Base):
    __tablename__ = "session_audiences"
    __table_args__ = (
        CheckConstraint("profile_revision >= 1", name="profile_revision_positive"),
        CheckConstraint("joined_at_ms >= 0", name="joined_at_nonnegative"),
        CheckConstraint(
            "left_at_ms IS NULL OR left_at_ms >= joined_at_ms",
            name="left_after_joined",
        ),
        Index("ix_session_audiences_audience_session", "audience_id", "session_id"),
    )

    session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("session_records.session_id", ondelete="CASCADE"),
        primary_key=True,
    )
    audience_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("audience_profiles.audience_id", ondelete="CASCADE"),
        primary_key=True,
    )
    profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    joined_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    left_at_ms: Mapped[int | None] = mapped_column(Integer)


class RoomRow(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        CheckConstraint("state IN ('active', 'cleared')", name="state_allowed"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint("created_at_ms >= 0", name="created_at_nonnegative"),
        CheckConstraint("updated_at_ms >= created_at_ms", name="updated_after_created"),
    )

    room_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class SessionRuntimeRevisionRow(Base):
    __tablename__ = "session_runtime_revisions"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("base_revision >= 0", name="base_revision_nonnegative"),
        CheckConstraint(
            "status IN ('pending', 'committed', 'rejected', 'rolled_back')",
            name="status_allowed",
        ),
        CheckConstraint("created_at_ms >= 0", name="created_at_nonnegative"),
        CheckConstraint("updated_at_ms >= created_at_ms", name="updated_after_created"),
        UniqueConstraint("session_id", "apply_id", name="uq_runtime_revision_session_apply"),
        Index("ix_runtime_revision_session_config_hash", "session_id", "config_hash"),
    )

    session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("session_records.session_id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    apply_id: Mapped[str] = mapped_column(Text, nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    diff_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class SessionViewerInstanceRow(Base):
    __tablename__ = "session_viewer_instances"
    __table_args__ = (
        CheckConstraint("persona_revision >= 1", name="persona_revision_positive"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        CheckConstraint("created_epoch >= 0", name="created_epoch_nonnegative"),
        CheckConstraint(
            "removed_epoch IS NULL OR removed_epoch >= created_epoch",
            name="removed_after_created",
        ),
        CheckConstraint("state IN ('active', 'removed')", name="state_allowed"),
        Index(
            "ix_session_viewer_instances_session_state_viewer",
            "session_id",
            "state",
            "viewer_instance_id",
        ),
        Index(
            "ix_session_viewer_instances_session_persona_ordinal",
            "session_id",
            "persona_id",
            "ordinal",
        ),
    )

    session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("session_records.session_id", ondelete="CASCADE"),
        primary_key=True,
    )
    viewer_instance_id: Mapped[str] = mapped_column(Text, primary_key=True)
    persona_id: Mapped[str] = mapped_column(Text, nullable=False)
    persona_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    micro_variant_json: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False, default="")
    avatar_seed: Mapped[str] = mapped_column(Text, nullable=False, default="")
    color_seed: Mapped[str] = mapped_column(Text, nullable=False, default="")
    locale: Mapped[str] = mapped_column(Text, nullable=False, default="zh-CN")
    persona_content_hash: Mapped[str] = mapped_column(Text, nullable=False, default="0" * 64)
    presence_state: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    presence_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    moderation_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    behavior_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    joined_at_ms: Mapped[int | None] = mapped_column(Integer)
    last_left_at_ms: Mapped[int | None] = mapped_column(Integer)
    join_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    muted_until_ms: Mapped[int | None] = mapped_column(Integer)
    mute_reason: Mapped[str | None] = mapped_column(Text)
    kicked_at_ms: Mapped[int | None] = mapped_column(Integer)
    kick_reason: Mapped[str | None] = mapped_column(Text)
    viewer_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    behavior_state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    removed_epoch: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(Text, nullable=False)


class RoomEventRow(Base):
    __tablename__ = "room_events"
    __table_args__ = (
        CheckConstraint("sequence >= 0", name="sequence_nonnegative"),
        CheckConstraint("audience_epoch >= 0", name="audience_epoch_nonnegative"),
        CheckConstraint("occurred_at_ms >= 0", name="occurred_at_nonnegative"),
        UniqueConstraint(
            "room_id",
            "session_id",
            "sequence",
            name="uq_room_events_room_session_sequence",
        ),
        Index("ix_room_events_room_occurred_at_ms", "room_id", "occurred_at_ms"),
    )

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    room_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("rooms.room_id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("session_records.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    audience_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class RoomLongTermMemoryRow(Base):
    __tablename__ = "room_long_term_memories"
    __table_args__ = (
        CheckConstraint("importance >= 0.0 AND importance <= 1.0", name="importance_range"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_range"),
        CheckConstraint(
            "state IN ('active', 'superseded', 'revoked')",
            name="state_allowed",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "superseded_by IS NULL OR superseded_by != memory_id",
            name="not_self_superseded",
        ),
        CheckConstraint("created_at_ms >= 0", name="created_at_nonnegative"),
        CheckConstraint("updated_at_ms >= created_at_ms", name="updated_after_created"),
        Index(
            "ix_room_long_term_memories_room_state_updated",
            "room_id",
            "state",
            "updated_at_ms",
        ),
        Index(
            "ix_room_long_term_memories_retrieval",
            "room_id",
            "state",
            "importance",
            "last_recalled_at_ms",
        ),
    )

    memory_id: Mapped[str] = mapped_column(Text, primary_key=True)
    room_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("rooms.room_id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    superseded_by: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("room_long_term_memories.memory_id", ondelete="SET NULL"),
    )
    last_recalled_at_ms: Mapped[int | None] = mapped_column(Integer)
    expires_at_ms: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class RoomMemoryEvidenceRow(Base):
    __tablename__ = "room_memory_evidence"
    __table_args__ = (
        CheckConstraint("occurred_at_ms >= 0", name="occurred_at_nonnegative"),
        Index("ix_room_memory_evidence_event_memory", "event_id", "memory_id"),
    )

    memory_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("room_long_term_memories.memory_id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False)


class RoomMemoryCandidateRow(Base):
    __tablename__ = "room_memory_candidates"
    __table_args__ = (
        CheckConstraint("base_revision >= 0", name="base_revision_nonnegative"),
        CheckConstraint(
            "outcome IN ('pending', 'created', 'merged', 'replaced', 'rejected', 'stale')",
            name="outcome_allowed",
        ),
        CheckConstraint("created_at_ms >= 0", name="created_at_nonnegative"),
        CheckConstraint("updated_at_ms >= created_at_ms", name="updated_after_created"),
        UniqueConstraint(
            "room_id",
            "idempotency_key",
            name="uq_room_memory_candidates_room_idempotency",
        ),
    )

    candidate_id: Mapped[str] = mapped_column(Text, primary_key=True)
    room_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("rooms.room_id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_event_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    result_memory_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("room_long_term_memories.memory_id", ondelete="SET NULL"),
    )
    decision_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class RoomMemoryHeadRow(Base):
    __tablename__ = "room_memory_heads"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint("updated_at_ms >= 0", name="updated_at_nonnegative"),
    )

    room_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("rooms.room_id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class ModeMemeRow(Base):
    __tablename__ = "mode_memes"
    __table_args__ = (
        CheckConstraint("intensity >= 0.0 AND intensity <= 1.0", name="intensity_range"),
        CheckConstraint(
            "state IN ('active', 'disabled', 'archived', 'revoked')",
            name="state_allowed",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("created_at_ms >= 0", name="created_at_nonnegative"),
        CheckConstraint("updated_at_ms >= created_at_ms", name="updated_after_created"),
        Index(
            "ix_mode_memes_namespace_state_updated",
            "mode_namespace",
            "state",
            "updated_at_ms",
        ),
    )

    meme_id: Mapped[str] = mapped_column(Text, primary_key=True)
    mode_namespace: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intensity: Mapped[float] = mapped_column(Float, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    source_json: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class ModeMemeEventRow(Base):
    __tablename__ = "mode_meme_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('created', 'edited', 'revoked', 'restored', 'disabled', 'archived')",
            name="action_allowed",
        ),
        CheckConstraint("previous_revision >= 0", name="previous_revision_nonnegative"),
        CheckConstraint("new_revision >= 1", name="new_revision_positive"),
        CheckConstraint("created_at_ms >= 0", name="created_at_nonnegative"),
    )

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    meme_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("mode_memes.meme_id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    previous_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    new_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class ModeMemeCandidateRow(Base):
    __tablename__ = "mode_meme_candidates"
    __table_args__ = (
        CheckConstraint("audience_epoch >= 1", name="audience_epoch_positive"),
        CheckConstraint(
            "outcome IN ('pending', 'accepted', 'rejected')",
            name="outcome_allowed",
        ),
        CheckConstraint("created_at_ms >= 0", name="created_at_nonnegative"),
        CheckConstraint("updated_at_ms >= created_at_ms", name="updated_after_created"),
        Index(
            "ix_mode_meme_candidates_namespace_outcome_created",
            "mode_namespace",
            "outcome",
            "created_at_ms",
        ),
        UniqueConstraint(
            "mode_namespace",
            "idempotency_key",
            name="uq_mode_meme_candidates_namespace_idempotency",
        ),
    )

    candidate_id: Mapped[str] = mapped_column(Text, primary_key=True)
    room_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("rooms.room_id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("session_records.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    audience_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_id: Mapped[str] = mapped_column(Text, nullable=False)
    mode_namespace: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_event_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_frame_indexes_json: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    result_meme_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("mode_memes.meme_id", ondelete="SET NULL"),
    )
    created_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class ModeMemeSettingRow(Base):
    __tablename__ = "mode_meme_settings"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("created_at_ms >= 0", name="created_at_nonnegative"),
        CheckConstraint("updated_at_ms >= created_at_ms", name="updated_after_created"),
    )

    mode_namespace: Mapped[str] = mapped_column(Text, primary_key=True)
    auto_ingest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
