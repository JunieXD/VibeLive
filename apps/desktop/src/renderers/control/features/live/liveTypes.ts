import type { CSSProperties, Dispatch, RefObject, SetStateAction } from 'react'
import type {
  BackendAudienceSnapshot,
  DesktopSource,
  MediaAccessStatus
} from '../../../../shared/contracts'
import type { ProviderProbeResult } from '../../../../shared/backend-client'
import type { SessionState } from '../../../../shared/session'
import type {
  VisualMode,
  VisualPipelineStatus,
  VisualSettings
} from '../../visual'
import type { MessageTarget } from './message-target'

export type LiveActivityItem = {
  id: string
  source: 'user' | 'audience' | 'system'
  author: string
  text: string
  color?: string
}

export type ProviderProbeDisplayState = {
  backendConnected: boolean
  profileLoading: boolean
  profileSaved: boolean
  profileId: string | null
  runtimeProviderReady: boolean
  probing: boolean
  probe: ProviderProbeResult | null
  error: string | null
}

export type LiveStageProps = {
  session: SessionState
  effectiveVisualMode: VisualMode
  visualSettings: VisualSettings
  setVisualSettings: Dispatch<SetStateAction<VisualSettings>>
  selectedSource: DesktopSource | null
  captureStream: MediaStream | null
  cameraStream: MediaStream | null
  cameras: readonly MediaDeviceInfo[]
  cameraEnabled: boolean
  mediaTransitioning: boolean
  isSessionActive: boolean
  canStart: boolean
  goLiveBusy: boolean
  audienceSessionActive: boolean
  overlayVisible: boolean
  barrageTotal: number
  microphoneLevel: number
  message: string
  messageSending: boolean
  providerProbeState: ProviderProbeDisplayState
  targetSuggestions: readonly MessageTarget[]
  pipPreviewStyle: CSSProperties
  videoRef: RefObject<HTMLVideoElement | null>
  cameraVideoRef: RefObject<HTMLVideoElement | null>
  onOpenSourcePicker: () => void
  onChangeVisualMode: (mode: VisualMode) => void | Promise<void>
  onToggleGoLive: () => void
  onTogglePause: () => void | Promise<void>
  onClearBarrage: () => void | Promise<void>
  onToggleOverlay: () => void | Promise<void>
  onMessageChange: (message: string) => void
  onSelectMessageTarget: (target: MessageTarget) => void
  onSendUserMessage: () => void
}

export type LiveChatProps = {
  activity: readonly LiveActivityItem[]
  chatListRef: RefObject<HTMLDivElement | null>
}

export type LiveAudienceProps = {
  audience: BackendAudienceSnapshot | null
  audienceLoading: boolean
  audienceError: string | null
  operationError: string | null
  pendingViewerId: string | null
  onViewAll?: () => void
  onRetryAudience: () => Promise<void>
  onDismissOperationError: () => void
  onMute: (viewerId: string, durationMs: number) => Promise<void>
  onUnmute: (viewerId: string) => Promise<void>
  onKick: (viewerId: string) => Promise<void>
}

export type LiveMixerProps = {
  captureStream: MediaStream | null
  cameraStream: MediaStream | null
  captureStatus: string
  cameraStatus: string
  microphoneLevel: number
  asrReady: boolean
  asrStatus: string
  visualSettings: VisualSettings
  lastFrameBytes: number | null
  lastFrameOverTarget: boolean
  lastVisualBatchAt: number | null
  visualPipelineStatus: VisualPipelineStatus
}

export type LiveDeviceStripProps = {
  session: SessionState
  microphones: readonly MediaDeviceInfo[]
  selectedMicrophoneId: string
  microphoneReady: boolean
  microphonePermission: MediaAccessStatus
  cameras: readonly MediaDeviceInfo[]
  cameraStream: MediaStream | null
  cameraEnabled: boolean
  cameraPermission: MediaAccessStatus
  cameraDeviceId: string
  isSessionActive: boolean
  mediaTransitioning: boolean
  onChangeMicrophone: (deviceId: string) => void | Promise<void>
  onRequestMicrophoneAccess: () => void | Promise<void>
  onChangeCamera: (deviceId: string) => void | Promise<void>
  onToggleCamera: () => void | Promise<void>
}

export type LiveViewProps = {
  session: SessionState
  stage: LiveStageProps
  chat: LiveChatProps
  audience: LiveAudienceProps
  mixer: LiveMixerProps
  devices: LiveDeviceStripProps
}
