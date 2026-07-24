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
  onBackendSessionSnapshot?: (snapshot: BackendSessionSnapshot) => void
  audienceWorkspace: AudienceWorkspaceState
  audienceAvailable: boolean
  onAudienceSessionActiveChange: (active: boolean) => void
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
  onBackendSessionSnapshot,
  audienceWorkspace,
  audienceAvailable,
  onAudienceSessionActiveChange
}: UseSessionMediaControlsOptions) {
  const [overlayVisible, setOverlayVisible] = useState(true)
  const devicesRef = useRef(devices)
  const onSystemActivityRef = useRef(onSystemActivity)
  const onSessionStartedRef = useRef(onSessionStarted)
  const onBackendSessionSnapshotRef = useRef(onBackendSessionSnapshot)
  const audienceWorkspaceRef = useRef(audienceWorkspace)
  const audienceAvailableRef = useRef(audienceAvailable)
  const onAudienceSessionActiveChangeRef = useRef(onAudienceSessionActiveChange)
  const backendSessionActiveRef = useRef(false)
  const restoreMicrophoneOnResumeRef = useRef(false)
  const startClientRequestIdRef = useRef<string | null>(null)
  devicesRef.current = devices
  onSystemActivityRef.current = onSystemActivity
  onSessionStartedRef.current = onSessionStarted
  onBackendSessionSnapshotRef.current = onBackendSessionSnapshot
  audienceWorkspaceRef.current = audienceWorkspace
  audienceAvailableRef.current = audienceAvailable
  onAudienceSessionActiveChangeRef.current = onAudienceSessionActiveChange

  const setAudienceSessionActive = useCallback((active: boolean): void => {
    backendSessionActiveRef.current = active
    onAudienceSessionActiveChangeRef.current(active)
  }, [])

  const syncBackendSession = useCallback((snapshot: BackendSessionSnapshot): void => {
    if (snapshot.state === 'idle' || snapshot.state === 'error') setAudienceSessionActive(false)
    sessionStatusRef.current = snapshot.state
    dispatchSession({ type: 'sync', status: snapshot.state })
    onBackendSessionSnapshotRef.current?.(snapshot)
  }, [dispatchSession, sessionStatusRef, setAudienceSessionActive])

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
    let backendSession: BackendSessionSnapshot | null = null
    setAudienceSessionActive(false)
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

      if (!microphoneStream && audienceAvailableRef.current && devices.selectedMicrophoneId) {
        try {
          microphoneStream = await devices.startMicrophone(
            operationId,
            devices.selectedMicrophoneId
          )
        } catch (error) {
          onSystemActivityRef.current(
            `麦克风未能启用：${describeMediaError(error, 'microphone')} 继续进行仅画面直播。`
          )
        }
      }
      if (!devices.operation.isCurrent(operationId)) return

      if (audienceAvailableRef.current) {
        startClientRequestIdRef.current ??= `desktop-${crypto.randomUUID()}`
        try {
          backendSession = await window.advx.startBackendSession(
            audienceWorkspaceRef.current,
            startClientRequestIdRef.current
          )
          backendSessionStarted =
            backendSession.sessionId !== null && backendSession.state === 'running'
          if (!backendSessionStarted) {
            if (backendSession.sessionId !== null) {
              await window.advx.stopBackendSession().catch(() => undefined)
            }
            startClientRequestIdRef.current = null
            onSystemActivityRef.current('AI 观众未能接入，继续进行仅画面直播。')
          }
        } catch (error) {
          if (!devices.operation.isCurrent(operationId)) return
          startClientRequestIdRef.current = null
          onSystemActivityRef.current(
            `AI 观众未能接入：${describeBackendError(error, '连接异常。')} 继续进行仅画面直播。`
          )
        }
      } else {
        onSystemActivityRef.current('Provider 未配置或后端未连接，继续进行仅画面直播。')
      }
      if (!devices.operation.isCurrent(operationId)) {
        if (backendSessionStarted) {
          await window.advx.stopBackendSession().catch(() => undefined)
          startClientRequestIdRef.current = null
        }
        return
      }

      if (backendSessionStarted) setAudienceSessionActive(true)
      try {
        await window.advx.showOverlay()
        setOverlayVisible(true)
      } catch (error) {
        setOverlayVisible(false)
        onSystemActivityRef.current(
          `悬浮层未能显示：${describeBackendError(error, '连接异常。')} 直播将继续。`
        )
      }
      if (!devices.operation.isCurrent(operationId)) {
        await window.advx.hideOverlay()
        if (backendSessionStarted) {
          await window.advx.stopBackendSession().catch(() => undefined)
          startClientRequestIdRef.current = null
        }
        setAudienceSessionActive(false)
        return
      }
      if (backendSessionStarted && backendSession) {
        syncBackendSession(backendSession)
      } else {
        sessionStatusRef.current = 'running'
        dispatchSession({ type: 'started' })
      }
      onSessionStartedRef.current()
    } catch (error) {
      if (!devices.operation.isCurrent(operationId)) return
      setAudienceSessionActive(false)
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
        error: `${error instanceof Error ? error.message : '启动失败，请检查视觉来源。'}${
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
  }, [
    dispatchSession,
    releaseOverlay,
    sessionStatusRef,
    setAudienceSessionActive,
    syncBackendSession
  ])

  const stopSession = useCallback(async (): Promise<void> => {
    const devices = devicesRef.current
    const operationId = devices.operation.begin(true)
    if (operationId === null) return
    const backendSessionActive = backendSessionActiveRef.current
    sessionStatusRef.current = 'stopping'
    dispatchSession({ type: 'stop' })
    devices.stopCapture()
    devices.stopCamera()
    try {
      await devices.stopMicrophone()
    } catch (error) {
      onSystemActivityRef.current(
        `麦克风未能完全停止：${describeMediaError(error, 'microphone')}`
      )
    }
    let stopError: string | null = null
    try {
      if (backendSessionActive) {
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
      setAudienceSessionActive(false)
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
  }, [
    dispatchSession,
    releaseOverlay,
    sessionStatusRef,
    setAudienceSessionActive,
    syncBackendSession
  ])

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
      restoreMicrophoneOnResumeRef.current = devices.microphoneStreamRef.current !== null
      sessionStatusRef.current = 'paused'
      dispatchSession({ type: 'pause' })
      devices.stopCapture()
      devices.stopCamera()
      try {
        await devices.stopMicrophone()
      } catch (error) {
        onSystemActivityRef.current(
          `麦克风未能完全暂停：${describeMediaError(error, 'microphone')}`
        )
      }
      if (backendSessionActiveRef.current) {
        try {
          const backendSession = await window.advx.pauseBackendSession()
          if (!devices.operation.isCurrent(operationId)) {
            devices.operation.finish(operationId)
            return
          }
          if (backendSession.state !== 'paused') {
            throw new Error('后端没有进入暂停状态。')
          }
          syncBackendSession(backendSession)
        } catch (error) {
          setAudienceSessionActive(false)
          await window.advx.stopBackendSession().catch(() => undefined)
          onSystemActivityRef.current(
            `AI 观众已暂停：${describeBackendError(error, '连接异常。')} 画面直播仍可恢复。`
          )
        }
      }
      devices.operation.finish(operationId)
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
        if (restoreMicrophoneOnResumeRef.current) {
          failureKind = 'microphone'
          try {
            microphoneStream = await devices.startMicrophone(
              operationId,
              devices.selectedMicrophoneId || undefined
            )
          } catch (error) {
            microphoneStream = null
            onSystemActivityRef.current(
              `麦克风未能恢复：${describeMediaError(error, 'microphone')} 继续进行仅画面直播。`
            )
          }
          if (!devices.operation.isCurrent(operationId)) return
        }
        if (backendSessionActiveRef.current) {
          try {
            const backendSession = await window.advx.resumeBackendSession()
            if (!devices.operation.isCurrent(operationId)) return
            if (backendSession.state !== 'running') {
              throw new Error('后端没有恢复运行状态。')
            }
            syncBackendSession(backendSession)
          } catch (error) {
            setAudienceSessionActive(false)
            await window.advx.stopBackendSession().catch(() => undefined)
            onSystemActivityRef.current(
              `AI 观众未能恢复：${describeBackendError(error, '连接异常。')} 继续进行仅画面直播。`
            )
            sessionStatusRef.current = 'running'
            dispatchSession({ type: 'resume' })
          }
        } else {
          sessionStatusRef.current = 'running'
          dispatchSession({ type: 'resume' })
        }
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
  }, [
    dispatchSession,
    releaseOverlay,
    sessionStatus,
    sessionStatusRef,
    setAudienceSessionActive,
    syncBackendSession
  ])

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
