from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
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
        Index("ix_session_records_ended_at_ms", "ended_at_ms"),
    )

    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
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
