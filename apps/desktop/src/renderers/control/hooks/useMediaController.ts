import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SessionStatus } from '../../../shared/session'
import {
  getPipRectangle,
  requiredVisualSources,
  resolveVisualMode,
  type VisualMode
} from '../visual'
import type {
  FatalMediaKind,
  MediaController,
  UseMediaControllerOptions
} from './mediaControllerTypes'
import { useMediaDevices } from './useMediaDevices'
import { useSessionMediaControls } from './useSessionMediaControls'

export type { MediaController, UseMediaControllerOptions } from './mediaControllerTypes'

export type LiveStartEligibility = {
  sessionStatus: SessionStatus
  visualMode: VisualMode
  hasScreen: boolean
  hasCamera: boolean
}

export function resolveAudioChannelStatus({
  paused,
  ready,
  transportError,
  idleStatus
}: {
  paused: boolean
  ready: boolean
  transportError: string | null
  idleStatus: string
}): string {
  if (paused) return '已暂停'
  if (transportError) return '传输异常'
  if (ready) return '正常'
  return idleStatus
}

export function canStartLive({
  sessionStatus,
  visualMode,
  hasScreen,
  hasCamera
}: LiveStartEligibility): boolean {
  const requirements = requiredVisualSources(visualMode)
  return (
    sessionStatus === 'idle' &&
    (!requirements.screen || hasScreen) &&
    (!requirements.camera || hasCamera)
  )
}

export function useMediaController({
  sessionStatus,
  dispatchSession,
  onSystemActivity,
  onSessionStarted,
  backendConnected = true,
  providerProfileAvailable = true,
  onBackendSessionSnapshot,
  audienceWorkspace
}: UseMediaControllerOptions): MediaController {
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false)
  const [audienceSessionActive, setAudienceSessionActiveState] = useState(false)
  const sessionStatusRef = useRef<SessionStatus>(sessionStatus)
  const audienceSessionActiveRef = useRef(false)
  const fatalMediaRef = useRef<(kind: FatalMediaKind, error: string) => void>(() => undefined)

  const setAudienceSessionActive = useCallback((active: boolean): void => {
    audienceSessionActiveRef.current = active
    setAudienceSessionActiveState(active)
  }, [])

  useEffect(() => {
    sessionStatusRef.current = sessionStatus
  }, [sessionStatus])

  const devices = useMediaDevices({
    sessionStatusRef,
    fatalMediaRef,
    mediaIngestEnabledRef: audienceSessionActiveRef,
    onSystemActivity,
    onRequestSourcePicker: () => setSourcePickerOpen(true)
  })
  const session = useSessionMediaControls({
    sessionStatus,
    sessionStatusRef,
    dispatchSession,
    devices,
    fatalMediaRef,
    onSystemActivity,
    onSessionStarted,
    onBackendSessionSnapshot,
    audienceWorkspace,
    audienceAvailable: backendConnected && providerProfileAvailable,
    onAudienceSessionActiveChange: setAudienceSessionActive
  })

  const isSessionActive = ['starting', 'running', 'paused', 'stopping'].includes(sessionStatus)
  const canStart = canStartLive({
    sessionStatus,
    visualMode: devices.visualSettings.mode,
    hasScreen: devices.selectedSource !== null,
    hasCamera: devices.cameraEnabled
  })
  const goLiveBusy =
    sessionStatus === 'starting' ||
    sessionStatus === 'stopping' ||
    devices.operation.transitioning
  const effectiveVisualMode =
    resolveVisualMode(
      devices.visualSettings.mode,
      devices.captureStream !== null || devices.selectedSource !== null,
      devices.cameraStream !== null
    ) ?? devices.visualSettings.mode
  const captureStatus =
    devices.captureStream
      ? sessionStatus === 'running' || sessionStatus === 'starting'
        ? '采集中'
        : '预览中'
      : devices.selectedSource
        ? '待启动'
        : '未连接'
  const cameraStatus =
    devices.cameraStream
      ? sessionStatus === 'running' || sessionStatus === 'starting'
        ? '采集中'
        : '预览中'
      : devices.cameraPermission === 'denied' || devices.cameraPermission === 'restricted'
        ? '权限受限'
        : devices.cameraEnabled
          ? '待启动'
          : '已关闭'
  const microphoneStatus =
    !devices.microphoneEnabled
      ? '已关闭'
      : resolveAudioChannelStatus({
          paused: sessionStatus === 'paused',
          ready: devices.microphoneReady,
          transportError: devices.microphoneTransportError,
          idleStatus:
            devices.microphonePermission === 'denied' ||
            devices.microphonePermission === 'restricted'
              ? '权限受限'
              : devices.selectedMicrophoneId
                ? '待检测'
                : '待授权'
        })
  const systemAudioStatus =
    !devices.systemAudioSupported
      ? '当前平台不可用'
      : !devices.systemAudioEnabled
        ? '已关闭'
        : resolveAudioChannelStatus({
            paused: sessionStatus === 'paused',
            ready: devices.systemAudioReady,
            transportError: devices.systemAudioTransportError,
            idleStatus: devices.systemAudioError
              ? '降级运行'
              : isSessionActive
                ? '等待采集'
                : '开播时采集'
          })
  const pipPreviewStyle = useMemo(() => {
    const rectangle = getPipRectangle(
      1600,
      900,
      devices.visualSettings.pipPosition,
      devices.visualSettings.pipSize
    )
    return {
      left: `${(rectangle.x / 1600) * 100}%`,
      top: `${(rectangle.y / 900) * 100}%`,
      width: `${(rectangle.width / 1600) * 100}%`,
      height: `${(rectangle.height / 900) * 100}%`
    }
  }, [devices.visualSettings.pipPosition, devices.visualSettings.pipSize])

  const chooseSource = async (source: Parameters<typeof devices.chooseSource>[0]): Promise<void> => {
    setSourcePickerOpen(false)
    await devices.chooseSource(source)
  }

  return {
    sourcePickerOpen,
    setSourcePickerOpen,
    selectedSource: devices.selectedSource,
    captureStream: devices.captureStream,
    cameraStream: devices.cameraStream,
    cameras: devices.cameras,
    cameraEnabled: devices.cameraEnabled,
    cameraPermission: devices.cameraPermission,
    visualSettings: devices.visualSettings,
    setVisualSettings: devices.setVisualSettings,
    microphones: devices.microphones,
    selectedMicrophoneId: devices.selectedMicrophoneId,
    microphoneEnabled: devices.microphoneEnabled,
    microphoneLevel: devices.microphoneLevel,
    microphoneReady: devices.microphoneReady,
    microphonePermission: devices.microphonePermission,
    microphoneTransportError: devices.microphoneTransportError,
    systemAudioEnabled: devices.systemAudioEnabled,
    systemAudioSupported: devices.systemAudioSupported,
    systemAudioLevel: devices.systemAudioLevel,
    systemAudioReady: devices.systemAudioReady,
    systemAudioError: devices.systemAudioError,
    systemAudioTransportError: devices.systemAudioTransportError,
    screenPermission: devices.screenPermission,
    mediaTransitioning: devices.operation.transitioning,
    overlayVisible: session.overlayVisible,
    audienceSessionActive,
    isSessionActive,
    canStart,
    goLiveBusy,
    effectiveVisualMode,
    captureStatus,
    cameraStatus,
    microphoneStatus,
    systemAudioStatus,
    pipPreviewStyle,
    videoRef: devices.videoRef,
    cameraVideoRef: devices.cameraVideoRef,
    capturePipelineVideoRef: devices.capturePipelineVideoRef,
    cameraPipelineVideoRef: devices.cameraPipelineVideoRef,
    captureStreamRef: devices.captureStreamRef,
    cameraStreamRef: devices.cameraStreamRef,
    microphoneStreamRef: devices.microphoneStreamRef,
    visualSettingsRef: devices.visualSettingsRef,
    chooseSource,
    requestMicrophoneAccess: devices.requestMicrophoneAccess,
    toggleMicrophone: devices.toggleMicrophone,
    toggleCamera: devices.toggleCamera,
    changeCamera: devices.changeCamera,
    changeVisualMode: devices.changeVisualMode,
    changeMicrophone: devices.changeMicrophone,
    toggleSystemAudio: devices.toggleSystemAudio,
    startSession: session.startSession,
    stopSession: session.stopSession,
    toggleGoLive: session.toggleGoLive,
    togglePause: session.togglePause,
    showOverlay: session.showOverlay,
    hideOverlay: session.hideOverlay,
    toggleOverlay: session.toggleOverlay,
    releaseOverlay: session.releaseOverlay
  }
}
