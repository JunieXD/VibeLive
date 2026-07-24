from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_FRAME_BUNDLE_SIZE = 15


class ObservationDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FrameSelectionStrategy(StrEnum):
    LATEST_N = "latest_n"
    EVENLY_SPACED = "evenly_spaced"
    CHANGE_PEAKS = "change_peaks"


class ViewerVisualInputMode(StrEnum):
    DIRECT_FRAMES = "direct_frames"
    SHARED_SUMMARY = "shared_summary"
    TEXT_ONLY = "text_only"


class ObservationTrigger(StrEnum):
    USER_TEXT = "user_text"
    FINAL_VOICE = "final_voice"
    SCREEN_CHANGE = "screen_change"
    AMBIENT_TICK = "ambient_tick"


class FrameBundleSettings(ObservationDomainModel):
    frame_bundle_size: int = Field(
        default=MAX_FRAME_BUNDLE_SIZE,
        ge=1,
        le=MAX_FRAME_BUNDLE_SIZE,
    )
    frame_window_ms: int = Field(default=120_000, ge=1)
    frame_selection_strategy: FrameSelectionStrategy = FrameSelectionStrategy.CHANGE_PEAKS
    frame_max_dimension: int = Field(default=1280, ge=64, le=8192)
    frame_quality: int = Field(default=80, ge=1, le=100)
    frame_similarity_threshold: float = Field(default=0.9, ge=0, le=1)
    frame_anchor_interval_ms: int = Field(default=5_000, ge=1)


class FrameBundleItem(ObservationDomainModel):
    frame_id: str = Field(min_length=1, max_length=128)
    frame_index: int = Field(ge=0)
    captured_at_ms: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    encoding: str = Field(min_length=1, max_length=64)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_ref: str = Field(min_length=1, max_length=2048)
    change_score: float = Field(default=0.0, ge=0, le=1)


class FrameBundle(ObservationDomainModel):
    bundle_id: str = Field(min_length=1, max_length=128)
    settings: FrameBundleSettings
    frames: list[FrameBundleItem] = Field(min_length=1, max_length=MAX_FRAME_BUNDLE_SIZE)

    @model_validator(mode="after")
    def validate_frames(self) -> "FrameBundle":
        indexes = [frame.frame_index for frame in self.frames]
        if indexes != list(range(len(indexes))):
            raise ValueError("frame indexes must be contiguous and ordered from zero")
        if len(self.frames) > self.settings.frame_bundle_size:
            raise ValueError("frame count cannot exceed frame_bundle_size")
        timestamps = [frame.captured_at_ms for frame in self.frames]
        if timestamps != sorted(timestamps):
            raise ValueError("frames must be ordered by captured_at_ms")
        return self


class ObservationWave(ObservationDomainModel):
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    observation_id: str = Field(min_length=1, max_length=128)
    created_at_ms: int = Field(ge=0)
    deadline_at_ms: int = Field(gt=0)
    triggers: list[ObservationTrigger] = Field(min_length=1, max_length=4)
    event_ids: list[str] = Field(default_factory=list, max_length=4_096)
    trigger_event_ids: list[str] = Field(default_factory=list, max_length=4_096)
    trigger_frame_ids: list[str] = Field(default_factory=list, max_length=120)
    trigger_screen_change_score: float = Field(default=0.0, ge=0, le=1)
    frame_bundle: FrameBundle | None = None
    visual_input_mode: ViewerVisualInputMode = ViewerVisualInputMode.DIRECT_FRAMES
    shared_visual_summary: str | None = Field(default=None, max_length=8_000)
    target_viewer_id: str | None = Field(default=None, min_length=1, max_length=128)
    target_persona_id: str | None = Field(default=None, min_length=1, max_length=128)
    target_ambiguous: bool = False
    input_revision: int = Field(default=0, ge=0)
    semantic_input_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_wave(self) -> "ObservationWave":
        if self.deadline_at_ms <= self.created_at_ms:
            raise ValueError("deadline_at_ms must be later than created_at_ms")
        if len(set(self.triggers)) != len(self.triggers):
            raise ValueError("triggers must be unique")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("event_ids must be unique")
        if not set(self.trigger_event_ids).issubset(self.event_ids):
            raise ValueError("trigger_event_ids must reference public event_ids")
        if len(set(self.trigger_event_ids)) != len(self.trigger_event_ids):
            raise ValueError("trigger_event_ids must be unique")
        if len(set(self.trigger_frame_ids)) != len(self.trigger_frame_ids):
            raise ValueError("trigger_frame_ids must be unique")
        if self.target_viewer_id is not None and self.target_persona_id is not None:
            raise ValueError("a wave can target either a Viewer or a Persona")
        if self.target_ambiguous and (
            self.target_viewer_id is not None or self.target_persona_id is not None
        ):
            raise ValueError("an ambiguous target must use ordinary broadcast")
        if (
            self.visual_input_mode is ViewerVisualInputMode.SHARED_SUMMARY
            and not self.shared_visual_summary
        ):
            raise ValueError("shared_summary mode requires shared_visual_summary")
        if (
            self.visual_input_mode is ViewerVisualInputMode.DIRECT_FRAMES
            and self.shared_visual_summary is not None
        ):
            raise ValueError("direct_frames mode cannot include shared_visual_summary")
        if self.visual_input_mode is ViewerVisualInputMode.TEXT_ONLY and (
            self.frame_bundle is not None or self.shared_visual_summary is not None
        ):
            raise ValueError("text_only mode cannot include visual input")
        return self
