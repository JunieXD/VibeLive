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
}

export type SaveModelConfigResult = {
  ok: boolean
  securelyStored: boolean
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

export type ControlApi = {
  listDesktopSources: () => Promise<DesktopSource[]>
  selectDesktopSource: (sourceId: string) => Promise<boolean>
  getMediaAccessStatus: () => Promise<MediaAccessSnapshot>
  requestMicrophonePermission: () => Promise<MediaAccessStatus>
  requestCameraPermission: () => Promise<MediaAccessStatus>
  authorizeCameraCapture: () => Promise<boolean>
  cancelCameraCaptureAuthorization: () => Promise<void>
  showOverlay: () => Promise<void>
  hideOverlay: () => Promise<void>
  clearOverlay: () => Promise<void>
  pushBarrage: (event: BarrageEvent) => Promise<void>
  saveModelConfig: (config: ModelConfig) => Promise<SaveModelConfigResult>
  loadAudienceWorkspace: () => Promise<AudienceWorkspaceState | null>
  saveAudienceWorkspace: (
    workspace: AudienceWorkspaceState
  ) => Promise<SaveAudienceWorkspaceResult>
  confirmCloseAfterAudienceSave: () => Promise<void>
  onCloseRequested: (listener: () => void) => () => void
  onEmergencyStop: (listener: () => void) => () => void
}

export type OverlayApi = {
  onBarrage: (listener: (event: BarrageEvent) => void) => () => void
  onClear: (listener: () => void) => () => void
}
