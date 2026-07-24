import type {
  Dispatch,
  MutableRefObject,
  SetStateAction
} from 'react'
import type {
  BackendSessionSnapshot,
  DesktopSource,
  MediaAccessStatus
} from '../../../shared/contracts'
import type { SessionAction, SessionStatus } from '../../../shared/session'
import type { VisualMode, VisualSettings } from '../visual'
import type { AudienceWorkspaceState } from '../../../shared/audience'

export type UseMediaControllerOptions = {
  sessionStatus: SessionStatus
  dispatchSession: Dispatch<SessionAction>
  onSystemActivity: (text: string) => void
  onSessionStarted: () => void
  backendConnected?: boolean
  providersConfigured?: boolean
  backendSessionId?: string | null
  onBackendSessionSnapshot?: (snapshot: BackendSessionSnapshot) => void
  audienceWorkspace: AudienceWorkspaceState
}

export type FatalMediaKind = 'display' | 'camera' | 'microphone'

export type MediaOperation = {
  begin: (replaceCurrent?: boolean) => number | null
  finish: (operationId: number) => void
  assertCurrent: (operationId: number) => void
  isCurrent: (operationId: number) => boolean
  invalidate: () => void
  transitioning: boolean
}

export type MediaDevicesController = {
  selectedSource: DesktopSource | null
  setSelectedSource: Dispatch<SetStateAction<DesktopSource | null>>
  captureStream: MediaStream | null
  cameraStream: MediaStream | null
  cameras: MediaDeviceInfo[]
  cameraEnabled: boolean
  cameraPermission: MediaAccessStatus
  visualSettings: VisualSettings
  setVisualSettings: Dispatch<SetStateAction<VisualSettings>>
  microphones: MediaDeviceInfo[]
  selectedMicrophoneId: string
  microphoneLevel: number
  microphoneReady: boolean
  microphonePermission: MediaAccessStatus
  screenPermission: MediaAccessStatus
  videoRef: MutableRefObject<HTMLVideoElement | null>
  cameraVideoRef: MutableRefObject<HTMLVideoElement | null>
  capturePipelineVideoRef: MutableRefObject<HTMLVideoElement | null>
  cameraPipelineVideoRef: MutableRefObject<HTMLVideoElement | null>
  captureStreamRef: MutableRefObject<MediaStream | null>
  cameraStreamRef: MutableRefObject<MediaStream | null>
  microphoneStreamRef: MutableRefObject<MediaStream | null>
  visualSettingsRef: MutableRefObject<VisualSettings>
  operation: MediaOperation
  chooseSource: (source: DesktopSource) => Promise<void>
  requestMicrophoneAccess: () => Promise<void>
  toggleCamera: () => Promise<void>
  changeCamera: (deviceId: string) => Promise<void>
  changeVisualMode: (mode: VisualMode) => Promise<void>
  changeMicrophone: (deviceId: string) => Promise<void>
  startCapture: (operationId: number, sourceId: string) => Promise<MediaStream>
  startCamera: (operationId: number, deviceId?: string) => Promise<MediaStream>
  startMicrophone: (operationId: number, deviceId?: string) => Promise<MediaStream>
  stopCapture: () => void
  stopCamera: () => void
  stopMicrophone: () => Promise<void>
}

export type MediaController = {
  sourcePickerOpen: boolean
  setSourcePickerOpen: Dispatch<SetStateAction<boolean>>
  selectedSource: DesktopSource | null
  captureStream: MediaStream | null
  cameraStream: MediaStream | null
  cameras: MediaDeviceInfo[]
  cameraEnabled: boolean
  cameraPermission: MediaAccessStatus
  visualSettings: VisualSettings
  setVisualSettings: Dispatch<SetStateAction<VisualSettings>>
  microphones: MediaDeviceInfo[]
  selectedMicrophoneId: string
  microphoneLevel: number
  microphoneReady: boolean
  microphonePermission: MediaAccessStatus
  screenPermission: MediaAccessStatus
  mediaTransitioning: boolean
  overlayVisible: boolean
  isSessionActive: boolean
  canStart: boolean
  goLiveBusy: boolean
  effectiveVisualMode: VisualMode
  captureStatus: string
  cameraStatus: string
  microphoneStatus: string
  pipPreviewStyle: { left: string; top: string; width: string; height: string }
  videoRef: MutableRefObject<HTMLVideoElement | null>
  cameraVideoRef: MutableRefObject<HTMLVideoElement | null>
  capturePipelineVideoRef: MutableRefObject<HTMLVideoElement | null>
  cameraPipelineVideoRef: MutableRefObject<HTMLVideoElement | null>
  captureStreamRef: MutableRefObject<MediaStream | null>
  cameraStreamRef: MutableRefObject<MediaStream | null>
  microphoneStreamRef: MutableRefObject<MediaStream | null>
  visualSettingsRef: MutableRefObject<VisualSettings>
  chooseSource: (source: DesktopSource) => Promise<void>
  requestMicrophoneAccess: () => Promise<void>
  toggleCamera: () => Promise<void>
  changeCamera: (deviceId: string) => Promise<void>
  changeVisualMode: (mode: VisualMode) => Promise<void>
  changeMicrophone: (deviceId: string) => Promise<void>
  startSession: () => Promise<void>
  stopSession: () => Promise<void>
  toggleGoLive: () => void
  togglePause: () => Promise<void>
  showOverlay: () => Promise<void>
  hideOverlay: () => Promise<void>
  toggleOverlay: () => Promise<void>
  releaseOverlay: () => Promise<string | null>
}
