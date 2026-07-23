import type { AudienceWorkspaceState } from "./audience";

export type DesktopSource = {
  id: string
  name: string
  thumbnailUrl: string
  appIconUrl: string | null
  kind: 'screen' | 'window'
}

export type BarrageEvent = {
  barrageId: string
  audienceId: string
  audienceName: string
  text: string
  color: string
  createdAt: number
}

export type ModelConfig = {
  baseUrl: string
  model: string
  apiKey: string
  asrApiKey: string
}

export type SaveModelConfigResult = {
  ok: boolean
  securelyStored: boolean
  backendConfigured: boolean
  restartRequired: boolean
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
  session: BackendSessionSnapshot
}

export type BackendBarrageEvent = {
  barrageId: string
  audienceId: string
  text: string
  createdAt: number
}

export type RealtimeMediaInput = {
  inputId: string
  capturedAtMs: number
  body: Uint8Array
}

export type RealtimeFrameInput = RealtimeMediaInput & {
  mimeType: string
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

export type OverlaySettings = {
  targetDisplayId: number
  fontSizePx: number
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
  getBackendStatus: () => Promise<BackendRuntimeStatus>
  restartBackend: () => Promise<BackendRuntimeStatus>
  startBackendSession: () => Promise<BackendSessionSnapshot>
  pauseBackendSession: () => Promise<BackendSessionSnapshot>
  resumeBackendSession: () => Promise<BackendSessionSnapshot>
  stopBackendSession: () => Promise<BackendSessionSnapshot>
  submitUserText: (text: string) => Promise<void>
  submitAudioSegment: (input: RealtimeMediaInput) => Promise<void>
  submitVisualFrame: (input: RealtimeFrameInput) => Promise<void>
  loadAudienceWorkspace: () => Promise<AudienceWorkspaceState | null>
  saveAudienceWorkspace: (
    workspace: AudienceWorkspaceState
  ) => Promise<SaveAudienceWorkspaceResult>
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
