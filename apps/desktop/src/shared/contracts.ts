import type { components } from "@advx/contracts";
import type { AudienceWorkspaceState } from "./audience";
import type {
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

export type ModelConfig = {
  baseUrl: string
  providerProfileId: string
  model: string
  directorModel: string
  viewerModel: string
  memoryModel: string
  visualSummaryModel: string
  apiKey: string
  asrApiKey: string
}

export type ModelConfigStatus = {
  baseUrl: string | null
  providerProfileId: string | null
  model: string | null
  directorModel: string | null
  viewerModel: string | null
  memoryModel: string | null
  visualSummaryModel: string | null
  modelApiKeyStored: boolean
  asrApiKeyStored: boolean
}

export type RuntimeModelProviderCandidate =
  components["schemas"]["RuntimeModelProviderCandidate"]

export type SaveModelConfigResult = {
  ok: boolean
  providerProfileId: string
  securelyStored: boolean
  backendConfigured: boolean
  restartRequired: boolean
  runtimeApplyRequired: boolean
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

export type RealtimeMediaInput = {
  inputId: string
  capturedAtMs: number
  body: Uint8Array
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
  targetDisplayId: number
  fontSizePx: number
  fontFamily: OverlayFontFamily
  bold: boolean
  outlineWidthPx: number
  speed: number
  opacity: number
  density: number
  region: OverlayRegion
  clickThrough: boolean
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
  showOverlay: () => Promise<void>
  hideOverlay: () => Promise<void>
  clearOverlay: () => Promise<void>
  pushBarrage: (event: BarrageEvent) => Promise<void>
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
  queryAudienceRuntime: (sessionId: string) => Promise<RuntimeQuerySnapshot>
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
  submitUserText: (text: string, target?: TextSubmitTarget) => Promise<void>
  submitAudioSegment: (input: RealtimeMediaInput) => Promise<void>
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
  onBackendStatus: (listener: (status: BackendRuntimeStatus) => void) => () => void
  onBackendBarrage: (listener: (event: BackendBarrageEvent) => void) => () => void
}

export type OverlayApi = {
  onBarrage: (listener: (event: BarrageEvent) => void) => () => void
  onClear: (listener: () => void) => () => void
  onSettingsChanged: (listener: (settings: OverlaySettings) => void) => () => void
}
