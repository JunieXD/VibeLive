import { useEffect, useMemo, useRef, useState } from 'react'
import type { SessionStatus } from '../../../shared/session'
import { getPipRectangle, requiredVisualSources, resolveVisualMode } from '../visual'
import type {
  FatalMediaKind,
  MediaController,
  UseMediaControllerOptions
} from './mediaControllerTypes'
import { useMediaDevices } from './useMediaDevices'
import { useSessionMediaControls } from './useSessionMediaControls'

export type { MediaController, UseMediaControllerOptions } from './mediaControllerTypes'

export function useMediaController({
  sessionStatus,
  dispatchSession,
  onSystemActivity,
  onSessionStarted
}: UseMediaControllerOptions): MediaController {
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false)
  const sessionStatusRef = useRef<SessionStatus>(sessionStatus)
  const fatalMediaRef = useRef<(kind: FatalMediaKind, error: string) => void>(() => undefined)

  useEffect(() => {
    sessionStatusRef.current = sessionStatus
  }, [sessionStatus])

  const devices = useMediaDevices({
    sessionStatusRef,
    fatalMediaRef,
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
    onSessionStarted
  })

  const isSessionActive = ['starting', 'running', 'paused', 'stopping'].includes(sessionStatus)
  const requirements = requiredVisualSources(devices.visualSettings.mode)
  const canStart =
    sessionStatus === 'idle' &&
    devices.selectedMicrophoneId !== '' &&
    (!requirements.screen || devices.selectedSource !== null) &&
    (!requirements.camera || devices.cameraEnabled)
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
    sessionStatus === 'paused'
      ? '已暂停'
      : devices.captureStream
        ? '采集中'
        : devices.selectedSource
          ? '待启动'
          : '未连接'
  const cameraStatus =
    sessionStatus === 'paused'
      ? '已暂停'
      : devices.cameraStream
        ? '采集中'
        : devices.cameraPermission === 'denied' || devices.cameraPermission === 'restricted'
          ? '权限受限'
          : devices.cameraEnabled
            ? '待启动'
            : '已关闭'
  const microphoneStatus =
    sessionStatus === 'paused'
      ? '已暂停'
      : devices.microphoneReady
        ? '正常'
        : devices.microphonePermission === 'denied' ||
            devices.microphonePermission === 'restricted'
          ? '权限受限'
          : devices.selectedMicrophoneId
            ? '待检测'
            : '待授权'
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
    microphoneLevel: devices.microphoneLevel,
    microphoneReady: devices.microphoneReady,
    microphonePermission: devices.microphonePermission,
    screenPermission: devices.screenPermission,
    mediaTransitioning: devices.operation.transitioning,
    overlayVisible: session.overlayVisible,
    isSessionActive,
    canStart,
    goLiveBusy,
    effectiveVisualMode,
    captureStatus,
    cameraStatus,
    microphoneStatus,
    pipPreviewStyle,
    videoRef: devices.videoRef,
    cameraVideoRef: devices.cameraVideoRef,
    captureStreamRef: devices.captureStreamRef,
    cameraStreamRef: devices.cameraStreamRef,
    microphoneStreamRef: devices.microphoneStreamRef,
    visualSettingsRef: devices.visualSettingsRef,
    chooseSource,
    requestMicrophoneAccess: devices.requestMicrophoneAccess,
    toggleCamera: devices.toggleCamera,
    changeCamera: devices.changeCamera,
    changeVisualMode: devices.changeVisualMode,
    changeMicrophone: devices.changeMicrophone,
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
