import type { components } from "@advx/contracts";
import type { AudienceWorkspaceState } from "./audience";
import type {
  AiCallQuery,
  AiCallImagePreview,
  AiCallQueryResponse,
  DebugTraceQueryResult,
  AutoIngestResponse,
  CandidateCommitResponse,
  MemeCandidate,
  MemoryResetResponse,
  ModeMeme,
  ModeMemeEdit,
  ProviderProbeResult,
  RoomLongTermMemory,
  RoomMemoryEdit,
  RoomMemoryHead,
  RuntimeApplySnapshot,
  RuntimeQuerySnapshot,
  TextSubmitTarget
} from "./backend-client";

export type DesktopSource = {
  id: string
  name: string
  thumbnailUrl: string
  appIconUrl: string | null
  kind: 'screen' | 'window'
}

export type BarrageMode = 'scroll' | 'top' | 'bottom'

export type BarrageDisplayMode = 'overlay' | 'floating'

export type ColorTheme = 'light' | 'dark'

export type BarrageEvent = {
  barrageId: string
  audienceId: string
  audienceName?: string
  text: string
  color?: string
  createdAt: number
  mode?: BarrageMode
  roomId?: string
  sessionId?: string
  audienceEpoch?: number
  observationId?: string
  generationRequestId?: string
  viewerInstanceId?: string
  personaId?: string
  viewerSequence?: number
  reactionType?: string
  evidenceRefs?: readonly BarrageEvidenceRef[]
  expiresAt?: number
}

export type BarrageEvidenceRef = {
  source: 'event' | 'frame'
  eventId: string | null
  frameIndex: number | null
}

export const DEFAULT_ASR_BASE_URL = 'https://api.stepfun.com/v1'
export const DEFAULT_ASR_MODEL = 'stepaudio-2.5-asr'

export type ModelConfig = {
  baseUrl: string
  providerProfileId: string
  model: string
  viewerModel: string
  memoryModel: string
  visualSummaryModel: string
  apiKey: string
  asrBaseUrl: string
  asrModel: string
  asrApiKey: string
}

export type ModelConfigStatus = {
  baseUrl: string | null
  providerProfileId: string | null
  model: string | null
  viewerModel: string | null
  memoryModel: string | null
  visualSummaryModel: string | null
  asrBaseUrl: string | null
  asrModel: string | null
  modelApiKeyStored: boolean
  asrApiKeyStored: boolean
}

export type RuntimeModelProviderCandidate =
  components["schemas"]["RuntimeModelProviderCandidate"]

export type SaveModelConfigResult = {
  ok: boolean
  providerProfileId: string
  securelyStored: boolean
  runtimeApplyRequired: boolean
  nextSessionRequired: boolean
}

export type BackendConnectionState =
  | 'starting'
  | 'connecting'
  | 'connected'
  | 'disconnected'
  | 'failed'

export type BackendSessionSnapshot = {
  sessionId: string | null
  state: 'idle' | 'starting' | 'running' | 'paused' | 'stopping' | 'error'
  startedAtMs: number | null
  updatedAtMs: number
  revision: number
}

export type SessionLifecycleLogEvent = {
  reason:
    | 'backend-start-failed'
    | 'backend-stop-failed'
    | 'backend-stop-requested'
    | 'emergency-stop'
    | 'media-failure'
  mediaKind?: 'camera' | 'display' | 'microphone'
  error?: string
}

export type BackendRuntimeStatus = {
  connection: BackendConnectionState
  providersConfigured: boolean
  startupError: string | null
  recoverableRuntimeSessionId: string | null
  session: BackendSessionSnapshot
}

export type RuntimeRoomIdentity = {
  roomId: string
  displayName: string
  revision: number
}

export type BackendBarrageEvent = {
  barrageId: string
  audienceId: string
  audienceName: string
  text: string
  createdAt: number
  roomId: string
  sessionId: string
  audienceEpoch: number
  observationId: string
  generationRequestId: string
  viewerInstanceId: string
  personaId: string
  viewerSequence: number
  reactionType: string
  evidenceRefs: readonly BarrageEvidenceRef[]
  expiresAt: number
}

export type ViewerPresenceState =
  | 'not_joined'
  | 'active'
  | 'left'
  | 'kicked'
  | 'ended'
  | 'removed'

export type BackendViewerSnapshot = {
  viewer_instance_id: string
  username: string
  display_name: string
  avatar_seed: string
  color_seed: string
  persona_id: string
  persona_display_name: string
  presence_state: ViewerPresenceState
  joined_at_ms: number | null
  last_left_at_ms: number | null
  join_count: number
  muted_until_ms: number | null
  viewer_sequence: number
  presence_revision: number
  moderation_revision: number
}

export type BackendAudienceSnapshot = {
  session_id: string
  room_id: string
  audience_epoch: number
  population_revision: number
  target_concurrent_viewers: number
  active_count: number
  viewers: BackendViewerSnapshot[]
}

export type BackendViewerEvent = {
  type:
    | 'viewer.joined'
    | 'viewer.left'
    | 'viewer.rejoined'
    | 'viewer.muted'
    | 'viewer.unmuted'
    | 'viewer.kicked'
  protocol_version: 3 | 4
  session_id: string
  audience_epoch: number
  population_revision: number
  occurred_at_ms: number
  viewer: BackendViewerSnapshot
}

export type AudioSource = components["schemas"]["AudioSource"]

export type BackendTranscriptEvent = {
  source: AudioSource
  text: string
  final: boolean
  startedAtMs: number
  endedAtMs: number
  utteranceId: string | null
  revision: number
}

export type RealtimeMediaInput = {
  inputId: string
  capturedAtMs: number
  body: Uint8Array
}

export type RealtimeAudioInput = RealtimeMediaInput & {
  source: AudioSource
  turnId?: string
  systemAudioRequired?: boolean
}

export type RealtimeFrameInput = RealtimeMediaInput & {
  mimeType: string
  changeScore: number
}

export type SaveAudienceWorkspaceResult = {
  ok: boolean
  savedAt: string
  personaDocumentsSynced: boolean
  personaDocumentsError: string | null
}

export type MediaAccessStatus =
  | 'not-determined'
  | 'granted'
  | 'denied'
  | 'restricted'
  | 'unknown'

export type MediaAccessSnapshot = {
  microphone: MediaAccessStatus
  camera: MediaAccessStatus
  screen: MediaAccessStatus
  systemAudioSupported: boolean
}

export type OverlayTarget = {
  id: number
  name: string
  bounds: {
    x: number
    y: number
    width: number
    height: number
  }
  scaleFactor: number
  isPrimary: boolean
}

export type OverlayRegion = {
  topPercent: number
  bottomPercent: number
}

export type OverlayFontFamily = 'bilibili' | 'yahei' | 'system'

export type OverlaySettings = {
  displayMode: BarrageDisplayMode
  targetDisplayId: number
  fontSizePx: number
  fontFamily: OverlayFontFamily
  bold: boolean
  outlineWidthPx: number
  speed: number
  opacity: number
  density: number
  region: OverlayRegion
}

export type ControlApi = {
  listDesktopSources: () => Promise<DesktopSource[]>
  selectDesktopSource: (sourceId: string) => Promise<boolean>
  getMediaAccessStatus: () => Promise<MediaAccessSnapshot>
  requestMicrophonePermission: () => Promise<MediaAccessStatus>
  requestCameraPermission: () => Promise<MediaAccessStatus>
  authorizeCameraCapture: () => Promise<boolean>
  cancelCameraCaptureAuthorization: () => Promise<void>
  listOverlayTargets: () => Promise<OverlayTarget[]>
  getOverlaySettings: () => Promise<OverlaySettings>
  setOverlaySettings: (settings: OverlaySettings) => Promise<OverlaySettings>
  showOverlay: () => Promise<boolean>
  hideOverlay: () => Promise<void>
  clearOverlay: () => Promise<void>
  pushBarrage: (event: BarrageEvent) => Promise<boolean>
  saveModelConfig: (config: ModelConfig) => Promise<SaveModelConfigResult>
  getModelConfigStatus: () => Promise<ModelConfigStatus>
  getBackendStatus: () => Promise<BackendRuntimeStatus>
  restartBackend: () => Promise<BackendRuntimeStatus>
  startBackendSession: (
    workspace: AudienceWorkspaceState,
    clientRequestId: string
  ) => Promise<BackendSessionSnapshot>
  pauseBackendSession: () => Promise<BackendSessionSnapshot>
  resumeBackendSession: () => Promise<BackendSessionSnapshot>
  stopBackendSession: () => Promise<BackendSessionSnapshot>
  reportSessionLifecycle: (event: SessionLifecycleLogEvent) => void
  queryAudienceRuntime: (sessionId: string) => Promise<RuntimeQuerySnapshot>
  queryLiveAudience: (sessionId: string) => Promise<BackendAudienceSnapshot>
  muteViewer: (
    sessionId: string,
    viewerId: string,
    durationMs: number,
    reason?: string
  ) => Promise<BackendViewerSnapshot>
  unmuteViewer: (sessionId: string, viewerId: string) => Promise<BackendViewerSnapshot>
  kickViewer: (
    sessionId: string,
    viewerId: string,
    reason?: string
  ) => Promise<BackendViewerSnapshot>
  applyAudienceRuntime: (
    sessionId: string,
    workspace: AudienceWorkspaceState,
    baseRevision: number
  ) => Promise<RuntimeApplySnapshot>
  rollbackAudienceRuntime: (
    sessionId: string,
    baseRevision: number,
    targetRevision: number
  ) => Promise<RuntimeApplySnapshot>
  recoverAudienceRuntime: (sessionId: string) => Promise<RuntimeQuerySnapshot>
  getAudienceRuntimeConfigHash: (
    workspace: AudienceWorkspaceState,
    configRevision: number,
    room: RuntimeRoomIdentity
  ) => Promise<string>
  probeAudienceProvider: () => Promise<ProviderProbeResult>
  queryDebugTraces: (
    sessionId: string,
    cursor?: string
  ) => Promise<DebugTraceQueryResult>
  queryAiCalls: (query: AiCallQuery) => Promise<AiCallQueryResponse>
  queryAiCallImage: (previewId: string) => Promise<AiCallImagePreview>
  submitUserText: (text: string, target?: TextSubmitTarget) => Promise<void>
  submitAudioSegment: (input: RealtimeAudioInput) => Promise<void>
  notifyVoiceActivity: (source: AudioSource, occurredAtMs: number) => void
  submitVisualFrame: (input: RealtimeFrameInput) => Promise<void>
  listRoomMemories: (roomId: string) => Promise<RoomLongTermMemory[]>
  getRoomMemoryHead: (roomId: string) => Promise<RoomMemoryHead>
  editRoomMemory: (
    roomId: string,
    memoryId: string,
    edit: RoomMemoryEdit
  ) => Promise<RoomLongTermMemory>
  revokeRoomMemory: (
    roomId: string,
    memoryId: string,
    expectedRevision: number
  ) => Promise<RoomLongTermMemory>
  deleteRoomMemory: (
    roomId: string,
    memoryId: string,
    expectedRevision: number
  ) => Promise<void>
  resetRoomMemories: (roomId: string, expectedRevision: number) => Promise<MemoryResetResponse>
  listModeMemes: (namespaceId: string) => Promise<ModeMeme[]>
  listPendingMemeCandidates: (namespaceId: string) => Promise<MemeCandidate[]>
  getModeMemeAutoIngest: (namespaceId: string) => Promise<AutoIngestResponse>
  setModeMemeAutoIngest: (
    namespaceId: string,
    enabled: boolean,
    expectedRevision: number
  ) => Promise<AutoIngestResponse>
  approveMemeCandidate: (
    namespaceId: string,
    candidateId: string
  ) => Promise<CandidateCommitResponse>
  rejectMemeCandidate: (
    namespaceId: string,
    candidateId: string
  ) => Promise<MemeCandidate>
  mutateModeMeme: (
    namespaceId: string,
    memeId: string,
    action: 'undo' | 'revoke' | 'disable' | 'restore' | 'pin' | 'unpin' | 'archive' | 'restart',
    expectedRevision: number
  ) => Promise<ModeMeme>
  editModeMeme: (
    namespaceId: string,
    memeId: string,
    edit: ModeMemeEdit
  ) => Promise<ModeMeme>
  loadAudienceWorkspace: () => Promise<AudienceWorkspaceState | null>
  saveAudienceWorkspace: (
    workspace: AudienceWorkspaceState
  ) => Promise<SaveAudienceWorkspaceResult>
  setColorTheme: (theme: ColorTheme) => Promise<void>
  confirmCloseAfterAudienceSave: () => Promise<void>
  onCloseRequested: (listener: () => void) => () => void
  onEmergencyStop: (listener: () => void) => () => void
  onOverlaySettingsChanged: (listener: (settings: OverlaySettings) => void) => () => void
  onOverlayVisibilityChanged: (listener: (visible: boolean) => void) => () => void
  onBackendStatus: (listener: (status: BackendRuntimeStatus) => void) => () => void
  onBackendBarrage: (listener: (event: BackendBarrageEvent) => void) => () => void
  onBackendViewerEvent: (listener: (event: BackendViewerEvent) => void) => () => void
  onBackendTranscript: (listener: (event: BackendTranscriptEvent) => void) => () => void
}

export type OverlayApi = {
  onBarrage: (listener: (event: BarrageEvent) => void) => () => void
  onClear: (listener: () => void) => () => void
  onSettingsChanged: (listener: (settings: OverlaySettings) => void) => () => void
}

export type FloatingChatApi = {
  onBarrage: (listener: (event: BarrageEvent) => void) => () => void
  onClear: (listener: () => void) => () => void
  minimize: () => Promise<void>
  hide: () => Promise<void>
  clear: () => Promise<void>
  submitText: (text: string) => Promise<void>
}
