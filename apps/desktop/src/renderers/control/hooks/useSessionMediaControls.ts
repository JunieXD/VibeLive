import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject
} from 'react'
import type { BackendSessionSnapshot } from '../../../shared/contracts'
import type { AudienceWorkspaceState } from '../../../shared/audience'
import type { SessionAction, SessionStatus } from '../../../shared/session'
import { describeMediaError } from '../media'
import { requiredVisualSources } from '../visual'
import type {
  FatalMediaKind,
  MediaDevicesController
} from './mediaControllerTypes'

type UseSessionMediaControlsOptions = {
  sessionStatus: SessionStatus
  sessionStatusRef: MutableRefObject<SessionStatus>
  dispatchSession: Dispatch<SessionAction>
  devices: MediaDevicesController
  fatalMediaRef: MutableRefObject<(kind: FatalMediaKind, error: string) => void>
  onSystemActivity: (text: string) => void
  onSessionStarted: () => void
  backendSessionId?: string | null
  onBackendSessionSnapshot?: (snapshot: BackendSessionSnapshot) => void
  audienceWorkspace: AudienceWorkspaceState
}

function describeBackendError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

export function useSessionMediaControls({
  sessionStatus,
  sessionStatusRef,
  dispatchSession,
  devices,
  fatalMediaRef,
  onSystemActivity,
  onSessionStarted,
  backendSessionId,
  onBackendSessionSnapshot,
  audienceWorkspace
}: UseSessionMediaControlsOptions) {
  const [overlayVisible, setOverlayVisible] = useState(true)
  const devicesRef = useRef(devices)
  const onSystemActivityRef = useRef(onSystemActivity)
  const onSessionStartedRef = useRef(onSessionStarted)
  const backendSessionIdRef = useRef(backendSessionId)
  const onBackendSessionSnapshotRef = useRef(onBackendSessionSnapshot)
  const audienceWorkspaceRef = useRef(audienceWorkspace)
  const startClientRequestIdRef = useRef<string | null>(null)
  devicesRef.current = devices
  onSystemActivityRef.current = onSystemActivity
  onSessionStartedRef.current = onSessionStarted
  backendSessionIdRef.current = backendSessionId
  onBackendSessionSnapshotRef.current = onBackendSessionSnapshot
  audienceWorkspaceRef.current = audienceWorkspace

  const syncBackendSession = useCallback((snapshot: BackendSessionSnapshot): void => {
    sessionStatusRef.current = snapshot.state
    dispatchSession({ type: 'sync', status: snapshot.state })
    onBackendSessionSnapshotRef.current?.(snapshot)
  }, [dispatchSession, sessionStatusRef])

  const releaseOverlay = useCallback(async (): Promise<string | null> => {
    const [clearResult, hideResult] = await Promise.allSettled([
      window.advx.clearOverlay(),
      window.advx.hideOverlay()
    ])
    if (hideResult.status === 'fulfilled') setOverlayVisible(false)
    if (clearResult.status === 'fulfilled' && hideResult.status === 'fulfilled') return null
    return '悬浮层未能完全关闭，请使用紧急停止快捷键后重试。'
  }, [])

  fatalMediaRef.current = (_kind, error) => {
    sessionStatusRef.current = 'error'
    void releaseOverlay()
    dispatchSession({ type: 'fail', error })
  }

  const startSession = useCallback(async (): Promise<void> => {
    const devices = devicesRef.current
    const operationId = devices.operation.begin()
    if (operationId === null) return
    const requirements = requiredVisualSources(devices.visualSettingsRef.current.mode)
    let displayStream: MediaStream | null = devices.captureStreamRef.current
    let cameraStream: MediaStream | null = devices.cameraStreamRef.current
    let microphoneStream: MediaStream | null = devices.microphoneStreamRef.current
    let backendSessionStarted = false
    sessionStatusRef.current = 'starting'
    dispatchSession({ type: 'start' })
    try {
      if (requirements.screen && !displayStream) {
        try {
          displayStream = await devices.startCapture(
            operationId,
            devices.selectedSource?.id ?? ''
          )
        } catch (error) {
          throw new Error(describeMediaError(error, 'display'))
        }
      }
      if (!devices.operation.isCurrent(operationId)) return

      if (requirements.camera && !cameraStream) {
        try {
          cameraStream = await devices.startCamera(
            operationId,
            devices.visualSettingsRef.current.cameraDeviceId || undefined
          )
        } catch (error) {
          throw new Error(describeMediaError(error, 'camera'))
        }
      }
      if (!devices.operation.isCurrent(operationId)) return

      if (!microphoneStream) {
        try {
          microphoneStream = await devices.startMicrophone(
            operationId,
            devices.selectedMicrophoneId || undefined
          )
        } catch (error) {
          throw new Error(describeMediaError(error, 'microphone'))
        }
      }
      if (!devices.operation.isCurrent(operationId)) return

      startClientRequestIdRef.current ??= `desktop-${crypto.randomUUID()}`
      const backendSession = await window.advx.startBackendSession(
        audienceWorkspaceRef.current,
        startClientRequestIdRef.current
      )
      backendSessionStarted = backendSession.sessionId !== null
      if (!devices.operation.isCurrent(operationId)) {
        if (backendSessionStarted) {
          await window.advx.stopBackendSession().catch(() => undefined)
          startClientRequestIdRef.current = null
        }
        return
      }
      await window.advx.showOverlay()
      if (!devices.operation.isCurrent(operationId)) {
        await window.advx.hideOverlay()
        if (backendSessionStarted) {
          await window.advx.stopBackendSession().catch(() => undefined)
          startClientRequestIdRef.current = null
        }
        return
      }
      setOverlayVisible(true)
      syncBackendSession(backendSession)
      if (backendSession.state === 'running') onSessionStartedRef.current()
    } catch (error) {
      if (!devices.operation.isCurrent(operationId)) return
      if (devices.captureStreamRef.current === displayStream) devices.stopCapture()
      if (devices.cameraStreamRef.current === cameraStream) devices.stopCamera()
      if (devices.microphoneStreamRef.current === microphoneStream) {
        await devices.stopMicrophone()
      }
      if (backendSessionStarted) {
        await window.advx.stopBackendSession().catch(() => undefined)
        startClientRequestIdRef.current = null
      }
      const overlayError = await releaseOverlay()
      if (!devices.operation.isCurrent(operationId)) return
      sessionStatusRef.current = 'error'
      dispatchSession({
        type: 'fail',
        error: `${error instanceof Error ? error.message : '启动失败，请检查视觉来源和麦克风权限。'}${
          overlayError ? ` ${overlayError}` : ''
        }`
      })
    } finally {
      if (!devices.operation.isCurrent(operationId)) {
        if (devices.captureStreamRef.current === displayStream) devices.stopCapture()
        if (devices.cameraStreamRef.current === cameraStream) devices.stopCamera()
        if (devices.microphoneStreamRef.current === microphoneStream) {
          await devices.stopMicrophone()
        }
      }
      devices.operation.finish(operationId)
    }
  }, [dispatchSession, releaseOverlay, sessionStatusRef, syncBackendSession])

  const stopSession = useCallback(async (): Promise<void> => {
    const devices = devicesRef.current
    const operationId = devices.operation.begin(true)
    if (operationId === null) return
    sessionStatusRef.current = 'stopping'
    dispatchSession({ type: 'stop' })
    devices.stopCapture()
    devices.stopCamera()
    await devices.stopMicrophone()
    let stopError: string | null = null
    try {
      if (backendSessionIdRef.current !== null) {
        const backendSession = await window.advx.stopBackendSession()
        syncBackendSession(backendSession)
      }
    } catch (error) {
      stopError = `后端 Session 未能确认停止：${describeBackendError(error, '连接异常。')}`
    }
    try {
      const overlayError = await releaseOverlay()
      if (!devices.operation.isCurrent(operationId)) return
      const notice = [stopError, overlayError].filter(Boolean).join(' ')
      if (notice) onSystemActivityRef.current(notice)
    } finally {
      if (devices.operation.isCurrent(operationId)) {
        sessionStatusRef.current = stopError ? 'error' : 'idle'
        if (stopError) dispatchSession({ type: 'fail', error: stopError })
        else {
          startClientRequestIdRef.current = null
          dispatchSession({ type: 'stopped' })
        }
      }
      devices.operation.finish(operationId)
    }
  }, [dispatchSession, releaseOverlay, sessionStatusRef, syncBackendSession])

  useEffect(() => window.advx.onEmergencyStop(() => void stopSession()), [stopSession])

  const toggleGoLive = useCallback((): void => {
    const active = ['starting', 'running', 'paused', 'stopping'].includes(sessionStatus)
    if (active || sessionStatus === 'error') void stopSession()
    else void startSession()
  }, [sessionStatus, startSession, stopSession])

  const togglePause = useCallback(async (): Promise<void> => {
    const devices = devicesRef.current
    const operationId = devices.operation.begin()
    if (operationId === null) return
    let displayStream: MediaStream | null = null
    let cameraStream: MediaStream | null = null
    let microphoneStream: MediaStream | null = null
    let failureKind: FatalMediaKind = 'display'
    if (sessionStatus === 'running') {
      sessionStatusRef.current = 'paused'
      dispatchSession({ type: 'pause' })
      devices.stopCapture()
      devices.stopCamera()
      try {
        await devices.stopMicrophone()
        const backendSession = await window.advx.pauseBackendSession()
        syncBackendSession(backendSession)
      } catch (error) {
        sessionStatusRef.current = 'error'
        dispatchSession({
          type: 'fail',
          error: `暂停后端 Session 失败：${describeBackendError(error, '连接异常。')}`
        })
      } finally {
        devices.operation.finish(operationId)
      }
      return
    }

    if (sessionStatus === 'paused') {
      try {
        const requirements = requiredVisualSources(devices.visualSettingsRef.current.mode)
        if (requirements.screen) {
          failureKind = 'display'
          displayStream = await devices.startCapture(
            operationId,
            devices.selectedSource?.id ?? ''
          )
          if (!devices.operation.isCurrent(operationId)) return
        }
        if (requirements.camera) {
          failureKind = 'camera'
          cameraStream = await devices.startCamera(
            operationId,
            devices.visualSettingsRef.current.cameraDeviceId || undefined
          )
          if (!devices.operation.isCurrent(operationId)) return
        }
        failureKind = 'microphone'
        microphoneStream = await devices.startMicrophone(
          operationId,
          devices.selectedMicrophoneId || undefined
        )
        if (!devices.operation.isCurrent(operationId)) return
        const backendSession = await window.advx.resumeBackendSession()
        if (!devices.operation.isCurrent(operationId)) return
        syncBackendSession(backendSession)
      } catch (error) {
        if (!devices.operation.isCurrent(operationId)) return
        if (devices.captureStreamRef.current === displayStream) devices.stopCapture()
        if (devices.cameraStreamRef.current === cameraStream) devices.stopCamera()
        if (devices.microphoneStreamRef.current === microphoneStream) {
          await devices.stopMicrophone()
        }
        const overlayError = await releaseOverlay()
        if (!devices.operation.isCurrent(operationId)) return
        sessionStatusRef.current = 'error'
        dispatchSession({
          type: 'fail',
          error: `恢复采集或后端 Session 失败：${
            error instanceof DOMException
              ? describeMediaError(error, failureKind)
              : describeBackendError(error, '连接异常。')
          }${
            overlayError ? ` ${overlayError}` : ''
          }`
        })
      } finally {
        if (!devices.operation.isCurrent(operationId)) {
          if (devices.captureStreamRef.current === displayStream) devices.stopCapture()
          if (devices.cameraStreamRef.current === cameraStream) devices.stopCamera()
          if (devices.microphoneStreamRef.current === microphoneStream) {
            await devices.stopMicrophone()
          }
        }
        devices.operation.finish(operationId)
      }
      return
    }
    devices.operation.finish(operationId)
  }, [dispatchSession, releaseOverlay, sessionStatus, sessionStatusRef, syncBackendSession])

  const showOverlay = useCallback(async (): Promise<void> => {
    await window.advx.showOverlay()
    setOverlayVisible(true)
  }, [])
  const hideOverlay = useCallback(async (): Promise<void> => {
    await window.advx.hideOverlay()
    setOverlayVisible(false)
  }, [])
  const toggleOverlay = useCallback(async (): Promise<void> => {
    if (overlayVisible) await hideOverlay()
    else await showOverlay()
  }, [hideOverlay, overlayVisible, showOverlay])

  return {
    overlayVisible,
    startSession,
    stopSession,
    toggleGoLive,
    togglePause,
    showOverlay,
    hideOverlay,
    toggleOverlay,
    releaseOverlay
  }
}
