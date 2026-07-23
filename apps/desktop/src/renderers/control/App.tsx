import {
  Activity,
  AudioLines,
  CircleStop,
  Clock,
  Eye,
  EyeOff,
  Gauge,
  KeyRound,
  LayoutDashboard,
  MessageSquareText,
  Mic,
  MonitorUp,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Send,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Users,
  Volume2,
  X
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import type { BarrageEvent, DesktopSource, MediaAccessStatus } from '../../shared/contracts'
import { demoLines, initialAudience, type AudienceMember } from '../../shared/demo'
import { initialSessionState, sessionReducer, type SessionStatus } from '../../shared/session'
import { calculateMicrophoneLevel, describeMediaError, stopMediaStream } from './media'

type ActiveView = 'live' | 'audience' | 'settings'

type ActivityItem = {
  id: string
  source: 'user' | 'audience' | 'system'
  author: string
  text: string
  color?: string
}

const statusLabels: Record<SessionStatus, string> = {
  idle: '未开播',
  starting: '连接中',
  running: '直播中',
  paused: '已暂停',
  stopping: '停止中',
  error: '需要处理'
}

function formatElapsed(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(':')
}

function SourcePicker({
  onClose,
  onSelect
}: {
  onClose: () => void
  onSelect: (source: DesktopSource) => Promise<void>
}): React.JSX.Element {
  const [sources, setSources] = useState<DesktopSource[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadSources = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const nextSources = await window.advx.listDesktopSources()
      setSources(nextSources)
      setSelectedId((current) =>
        nextSources.some((source) => source.id === current) ? current : nextSources[0]?.id ?? null
      )
    } catch {
      setError('无法读取屏幕来源，请检查系统录屏权限。')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadSources()
  }, [loadSources])

  const selectedSource = sources.find((source) => source.id === selectedId)

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="source-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="source-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="dialog-header">
          <div>
            <p className="eyebrow">画面采集</p>
            <h2 id="source-dialog-title">选择屏幕或窗口</h2>
          </div>
          <div className="dialog-actions">
            <button className="icon-button" type="button" title="刷新来源" onClick={loadSources}>
              <RefreshCw size={17} />
            </button>
            <button className="icon-button" type="button" title="关闭" onClick={onClose}>
              <X size={18} />
            </button>
          </div>
        </header>

        <div className="source-grid">
          {loading && <div className="empty-state">正在读取可用窗口...</div>}
          {error && <div className="empty-state error-text">{error}</div>}
          {!loading &&
            sources.map((source) => (
              <button
                className={`source-option ${source.id === selectedId ? 'selected' : ''}`}
                key={source.id}
                type="button"
                onClick={() => setSelectedId(source.id)}
              >
                <img src={source.thumbnailUrl} alt="" />
                <span className="source-name">
                  {source.appIconUrl && <img className="source-app-icon" src={source.appIconUrl} alt="" />}
                  <span>{source.name}</span>
                </span>
                <span className="source-kind">{source.kind === 'screen' ? '屏幕' : '窗口'}</span>
              </button>
            ))}
        </div>

        <footer className="dialog-footer">
          <span>{sources.length} 个可用来源</span>
          <button
            className="primary-button"
            type="button"
            disabled={!selectedSource}
            onClick={() => selectedSource && void onSelect(selectedSource)}
          >
            <MonitorUp size={17} />
            使用此来源
          </button>
        </footer>
      </section>
    </div>
  )
}

export function App(): React.JSX.Element {
  const [activeView, setActiveView] = useState<ActiveView>('live')
  const [session, dispatch] = useReducer(sessionReducer, initialSessionState)
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false)
  const [selectedSource, setSelectedSource] = useState<DesktopSource | null>(null)
  const [captureStream, setCaptureStream] = useState<MediaStream | null>(null)
  const [microphones, setMicrophones] = useState<MediaDeviceInfo[]>([])
  const [selectedMicrophoneId, setSelectedMicrophoneId] = useState('')
  const [microphoneLevel, setMicrophoneLevel] = useState(0)
  const [microphoneReady, setMicrophoneReady] = useState(false)
  const [microphonePermission, setMicrophonePermission] =
    useState<MediaAccessStatus>('unknown')
  const [screenPermission, setScreenPermission] = useState<MediaAccessStatus>('unknown')
  const [mediaTransitioning, setMediaTransitioning] = useState(false)
  const [overlayVisible, setOverlayVisible] = useState(true)
  const [audience, setAudience] = useState<AudienceMember[]>(initialAudience)
  const [activity, setActivity] = useState<ActivityItem[]>([
    {
      id: 'system-ready',
      source: 'system',
      author: '系统',
      text: '控制台已就绪，当前使用前端演示模式。'
    }
  ])
  const [message, setMessage] = useState('')
  const [modelBaseUrl, setModelBaseUrl] = useState('https://api.openai.com/v1')
  const [modelName, setModelName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [configNotice, setConfigNotice] = useState<string | null>(null)
  const [barrageOpacity, setBarrageOpacity] = useState(86)
  const [barrageSpeed, setBarrageSpeed] = useState(58)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [barrageTotal, setBarrageTotal] = useState(0)

  const videoRef = useRef<HTMLVideoElement>(null)
  const captureStreamRef = useRef<MediaStream | null>(null)
  const microphoneStreamRef = useRef<MediaStream | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const meterFrameRef = useRef<number | null>(null)
  const mediaOperationRef = useRef(0)
  const mediaTransitionRef = useRef(false)
  const barrageSequenceRef = useRef(0)
  const sessionStatusRef = useRef(session.status)
  const startedAtRef = useRef<number | null>(null)
  const chatListRef = useRef<HTMLDivElement>(null)

  const activeAudience = useMemo(() => audience.filter((member) => member.active), [audience])
  const isSessionActive = ['starting', 'running', 'paused', 'stopping'].includes(session.status)
  const canStart =
    session.status === 'idle' && selectedSource !== null && selectedMicrophoneId !== ''
  const goLiveBusy =
    session.status === 'starting' || session.status === 'stopping' || mediaTransitioning
  const captureStatus =
    session.status === 'paused'
      ? '已暂停'
      : captureStream
        ? '采集中'
        : selectedSource
          ? '待启动'
          : '未连接'
  const microphoneStatus =
    session.status === 'paused'
      ? '已暂停'
      : microphoneReady
        ? '正常'
        : microphonePermission === 'denied' || microphonePermission === 'restricted'
          ? '权限受限'
          : selectedMicrophoneId
            ? '待检测'
            : '待授权'

  const beginMediaOperation = useCallback((replaceCurrent = false): number | null => {
    if (mediaTransitionRef.current && !replaceCurrent) return null
    const operationId = mediaOperationRef.current + 1
    mediaOperationRef.current = operationId
    mediaTransitionRef.current = true
    setMediaTransitioning(true)
    return operationId
  }, [])

  const finishMediaOperation = useCallback((operationId: number): void => {
    if (mediaOperationRef.current !== operationId) return
    mediaTransitionRef.current = false
    setMediaTransitioning(false)
  }, [])

  const assertMediaOperationCurrent = useCallback((operationId: number): void => {
    if (mediaOperationRef.current !== operationId) {
      throw new DOMException('Media operation was superseded.', 'AbortError')
    }
  }, [])

  const releaseOverlay = useCallback(async (): Promise<string | null> => {
    const [clearResult, hideResult] = await Promise.allSettled([
      window.advx.clearOverlay(),
      window.advx.hideOverlay()
    ])
    if (hideResult.status === 'fulfilled') {
      setOverlayVisible(false)
    }
    if (clearResult.status === 'fulfilled' && hideResult.status === 'fulfilled') {
      return null
    }
    return '悬浮层未能完全关闭，请使用紧急停止快捷键后重试。'
  }, [])

  useEffect(() => {
    sessionStatusRef.current = session.status
  }, [session.status])

  useEffect(() => {
    captureStreamRef.current = captureStream
    if (videoRef.current) {
      videoRef.current.srcObject = captureStream
      if (captureStream) {
        void videoRef.current.play().catch(() => undefined)
      }
    }
  }, [captureStream])

  useEffect(() => {
    void window.advx
      .getMediaAccessStatus()
      .then((status) => {
        setMicrophonePermission(status.microphone)
        setScreenPermission(status.screen)
      })
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (session.status === 'running' || session.status === 'paused') {
      if (startedAtRef.current === null) {
        startedAtRef.current = Date.now() - elapsedSeconds * 1000
      }
      const timer = window.setInterval(() => {
        setElapsedSeconds(Math.floor((Date.now() - (startedAtRef.current ?? Date.now())) / 1000))
      }, 1000)
      return () => window.clearInterval(timer)
    }
    if (session.status === 'idle') {
      startedAtRef.current = null
      setElapsedSeconds(0)
    }
  }, [session.status, elapsedSeconds])

  useEffect(() => {
    const list = chatListRef.current
    if (list) {
      list.scrollTop = list.scrollHeight
    }
  }, [activity])

  const stopCapture = useCallback(() => {
    const stream = captureStreamRef.current
    captureStreamRef.current = null
    stopMediaStream(stream)
    if (videoRef.current?.srcObject === stream) {
      videoRef.current.pause()
      videoRef.current.srcObject = null
    }
    setCaptureStream(null)
  }, [])

  const stopMicrophone = useCallback(async (): Promise<void> => {
    if (meterFrameRef.current !== null) {
      cancelAnimationFrame(meterFrameRef.current)
      meterFrameRef.current = null
    }
    const stream = microphoneStreamRef.current
    microphoneStreamRef.current = null
    stopMediaStream(stream)
    const context = audioContextRef.current
    audioContextRef.current = null
    if (context && context.state !== 'closed') {
      await context.close().catch(() => undefined)
    }
    setMicrophoneLevel(0)
    setMicrophoneReady(false)
  }, [])

  useEffect(() => {
    return () => {
      mediaOperationRef.current += 1
      mediaTransitionRef.current = false
      stopMediaStream(captureStreamRef.current)
      void stopMicrophone()
    }
  }, [stopMicrophone])

  const startCapture = useCallback(
    async (operationId: number, sourceId: string): Promise<MediaStream> => {
      const accepted = await window.advx.selectDesktopSource(sourceId)
      assertMediaOperationCurrent(operationId)
      if (!accepted) {
        throw new DOMException('The selected display source is no longer available.', 'NotFoundError')
      }
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: {
          frameRate: { ideal: 12, max: 20 }
        },
        audio: false
      })
      try {
        assertMediaOperationCurrent(operationId)
        const videoTrack = stream.getVideoTracks()[0]
        if (!videoTrack) {
          throw new DOMException('No display video track was created.', 'NotReadableError')
        }

        const previousStream = captureStreamRef.current
        captureStreamRef.current = stream
        stopMediaStream(previousStream)
        setCaptureStream(stream)
        setScreenPermission('granted')
        videoTrack.addEventListener(
          'ended',
          () => {
            if (captureStreamRef.current !== stream) return
            mediaOperationRef.current += 1
            mediaTransitionRef.current = false
            setMediaTransitioning(false)
            captureStreamRef.current = null
            setCaptureStream(null)
            if (
              sessionStatusRef.current === 'running' ||
              sessionStatusRef.current === 'starting'
            ) {
              sessionStatusRef.current = 'error'
              void stopMicrophone()
              void releaseOverlay()
              dispatch({ type: 'fail', error: '画面来源已结束，请重新选择。' })
            }
          },
          { once: true }
        )
        return stream
      } catch (error) {
        stopMediaStream(stream)
        throw error
      }
    },
    [assertMediaOperationCurrent, releaseOverlay, stopMicrophone]
  )

  const chooseSource = async (source: DesktopSource): Promise<void> => {
    const operationId = beginMediaOperation()
    if (operationId === null) return
    try {
      setSourcePickerOpen(false)
      await startCapture(operationId, source.id)
      setSelectedSource(source)
    } catch (error) {
      if (mediaOperationRef.current !== operationId) return
      const message = describeMediaError(error, 'display')
      void window.advx
        .getMediaAccessStatus()
        .then((status) => setScreenPermission(status.screen))
        .catch(() => undefined)
      setActivity((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          source: 'system',
          author: '系统',
          text: message
        }
      ])
    } finally {
      finishMediaOperation(operationId)
    }
  }

  const refreshMicrophones = useCallback(
    async (preferredDeviceId?: string, operationId?: number): Promise<void> => {
      const devices = await navigator.mediaDevices.enumerateDevices()
      if (operationId !== undefined) {
        assertMediaOperationCurrent(operationId)
      }
      const inputs = devices.filter((device) => device.kind === 'audioinput')
      setMicrophones(inputs)
      setSelectedMicrophoneId((current) => {
        if (inputs.some((device) => device.deviceId === current)) return current
        if (preferredDeviceId && inputs.some((device) => device.deviceId === preferredDeviceId)) {
          return preferredDeviceId
        }
        return inputs[0]?.deviceId ?? ''
      })
    },
    [assertMediaOperationCurrent]
  )

  useEffect(() => {
    const handleDeviceChange = (): void => {
      void refreshMicrophones()
    }

    void refreshMicrophones()
    navigator.mediaDevices.addEventListener('devicechange', handleDeviceChange)
    return () => navigator.mediaDevices.removeEventListener('devicechange', handleDeviceChange)
  }, [refreshMicrophones])

  const startMicrophone = useCallback(
    async (operationId: number, deviceId?: string): Promise<MediaStream> => {
      await stopMicrophone()
      assertMediaOperationCurrent(operationId)
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: deviceId ? { exact: deviceId } : undefined,
          echoCancellation: true,
          noiseSuppression: true
        }
      })
      let context: AudioContext | null = null
      try {
        assertMediaOperationCurrent(operationId)
        const audioTrack = stream.getAudioTracks()[0]
        if (!audioTrack) {
          throw new DOMException('No microphone audio track was created.', 'NotReadableError')
        }

        context = new AudioContext()
        const analyser = context.createAnalyser()
        analyser.fftSize = 512
        context.createMediaStreamSource(stream).connect(analyser)
        if (context.state === 'suspended') {
          await context.resume()
          assertMediaOperationCurrent(operationId)
        }

        const actualDeviceId = audioTrack.getSettings().deviceId
        await refreshMicrophones(actualDeviceId, operationId)
        assertMediaOperationCurrent(operationId)

        microphoneStreamRef.current = stream
        audioContextRef.current = context
        setMicrophoneReady(true)
        setMicrophonePermission('granted')

        audioTrack.addEventListener(
          'ended',
          () => {
            if (microphoneStreamRef.current !== stream) return
            mediaOperationRef.current += 1
            mediaTransitionRef.current = false
            setMediaTransitioning(false)
            microphoneStreamRef.current = null
            if (meterFrameRef.current !== null) {
              cancelAnimationFrame(meterFrameRef.current)
              meterFrameRef.current = null
            }
            void context?.close()
            audioContextRef.current = null
            setMicrophoneLevel(0)
            setMicrophoneReady(false)
            if (
              sessionStatusRef.current === 'running' ||
              sessionStatusRef.current === 'starting'
            ) {
              sessionStatusRef.current = 'error'
              stopCapture()
              void releaseOverlay()
              dispatch({ type: 'fail', error: '麦克风连接已中断，请检查设备。' })
            }
          },
          { once: true }
        )

        const samples = new Uint8Array(analyser.fftSize)
        const measure = (): void => {
          if (microphoneStreamRef.current !== stream) return
          analyser.getByteTimeDomainData(samples)
          setMicrophoneLevel(calculateMicrophoneLevel(samples))
          meterFrameRef.current = requestAnimationFrame(measure)
        }
        measure()
        return stream
      } catch (error) {
        stopMediaStream(stream)
        if (context && context.state !== 'closed') {
          await context.close().catch(() => undefined)
        }
        throw error
      }
    },
    [
      assertMediaOperationCurrent,
      refreshMicrophones,
      releaseOverlay,
      stopCapture,
      stopMicrophone
    ]
  )

  const requestMicrophoneAccess = useCallback(async (): Promise<void> => {
    const operationId = beginMediaOperation()
    if (operationId === null) return
    try {
      const nativeStatus = await window.advx.requestMicrophonePermission()
      if (mediaOperationRef.current !== operationId) return
      setMicrophonePermission(nativeStatus)
      if (nativeStatus === 'denied' || nativeStatus === 'restricted') {
        throw new DOMException('Microphone access is denied by the operating system.', 'NotAllowedError')
      }
      await startMicrophone(operationId, selectedMicrophoneId || undefined)
    } catch (error) {
      if (mediaOperationRef.current !== operationId) return
      await stopMicrophone()
      setActivity((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          source: 'system',
          author: '系统',
          text: describeMediaError(error, 'microphone')
        }
      ])
    } finally {
      finishMediaOperation(operationId)
    }
  }, [
    beginMediaOperation,
    finishMediaOperation,
    selectedMicrophoneId,
    startMicrophone,
    stopMicrophone
  ])

  const changeMicrophone = useCallback(
    async (deviceId: string): Promise<void> => {
      setSelectedMicrophoneId(deviceId)
      if (!microphoneReady) return

      const operationId = beginMediaOperation()
      if (operationId === null) return
      try {
        await startMicrophone(operationId, deviceId)
      } catch (error) {
        if (mediaOperationRef.current !== operationId) return
        await stopMicrophone()
        setActivity((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            source: 'system',
            author: '系统',
            text: describeMediaError(error, 'microphone')
          }
        ])
      } finally {
        finishMediaOperation(operationId)
      }
    },
    [
      beginMediaOperation,
      finishMediaOperation,
      microphoneReady,
      startMicrophone,
      stopMicrophone
    ]
  )

  const emitBarrage = useCallback(
    (text?: string) => {
      const member = activeAudience[barrageSequenceRef.current % Math.max(activeAudience.length, 1)]
      if (!member) return

      const event: BarrageEvent = {
        barrageId: `demo-${Date.now()}-${barrageSequenceRef.current}`,
        audienceId: member.id,
        audienceName: member.name,
        text: text ?? demoLines[barrageSequenceRef.current % demoLines.length],
        color: member.color,
        createdAt: Date.now()
      }
      barrageSequenceRef.current += 1
      setBarrageTotal((current) => current + 1)

      setActivity((current) => [
        ...current.slice(-40),
        {
          id: event.barrageId,
          source: 'audience',
          author: member.name,
          text: event.text,
          color: member.color
        }
      ])

      if (overlayVisible) {
        void window.advx.pushBarrage(event)
      }
    },
    [activeAudience, overlayVisible]
  )

  useEffect(() => {
    if (session.status !== 'running') return
    const timer = window.setInterval(() => emitBarrage(), 5200)
    return () => window.clearInterval(timer)
  }, [emitBarrage, session.status])

  const startSession = async (): Promise<void> => {
    const operationId = beginMediaOperation()
    if (operationId === null) return
    let displayStream: MediaStream | null = captureStreamRef.current
    let microphoneStream: MediaStream | null = microphoneStreamRef.current
    sessionStatusRef.current = 'starting'
    dispatch({ type: 'start' })
    try {
      if (!displayStream) {
        try {
          displayStream = await startCapture(operationId, selectedSource?.id ?? '')
        } catch (error) {
          throw new Error(describeMediaError(error, 'display'))
        }
      }
      if (mediaOperationRef.current !== operationId) return

      if (!microphoneStream) {
        try {
          microphoneStream = await startMicrophone(
            operationId,
            selectedMicrophoneId || undefined
          )
        } catch (error) {
          throw new Error(describeMediaError(error, 'microphone'))
        }
      }
      if (mediaOperationRef.current !== operationId) return

      await window.advx.showOverlay()
      if (mediaOperationRef.current !== operationId) {
        await window.advx.hideOverlay()
        return
      }
      setOverlayVisible(true)
      sessionStatusRef.current = 'running'
      dispatch({ type: 'started' })
      emitBarrage('画面和声音都收到啦，今天从这里开始。')
    } catch (error) {
      if (mediaOperationRef.current !== operationId) return
      if (captureStreamRef.current === displayStream) stopCapture()
      if (microphoneStreamRef.current === microphoneStream) await stopMicrophone()
      const overlayError = await releaseOverlay()
      if (mediaOperationRef.current !== operationId) return
      sessionStatusRef.current = 'error'
      dispatch({
        type: 'fail',
        error: `${error instanceof Error ? error.message : '启动失败，请检查屏幕和麦克风权限。'}${
          overlayError ? ` ${overlayError}` : ''
        }`
      })
    } finally {
      if (mediaOperationRef.current !== operationId) {
        if (captureStreamRef.current === displayStream) stopCapture()
        if (microphoneStreamRef.current === microphoneStream) await stopMicrophone()
      }
      finishMediaOperation(operationId)
    }
  }

  const stopSession = useCallback(async () => {
    const operationId = beginMediaOperation(true)
    if (operationId === null) return
    sessionStatusRef.current = 'stopping'
    dispatch({ type: 'stop' })
    stopCapture()
    await stopMicrophone()
    try {
      const overlayError = await releaseOverlay()
      if (mediaOperationRef.current !== operationId) return
      if (overlayError) {
        setActivity((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            source: 'system',
            author: '系统',
            text: overlayError
          }
        ])
      }
    } finally {
      if (mediaOperationRef.current === operationId) {
        sessionStatusRef.current = 'idle'
        dispatch({ type: 'stopped' })
      }
      finishMediaOperation(operationId)
    }
  }, [
    beginMediaOperation,
    finishMediaOperation,
    releaseOverlay,
    stopCapture,
    stopMicrophone
  ])

  useEffect(() => window.advx.onEmergencyStop(() => void stopSession()), [stopSession])

  const toggleGoLive = (): void => {
    if (isSessionActive || session.status === 'error') {
      void stopSession()
    } else {
      void startSession()
    }
  }

  const togglePause = async (): Promise<void> => {
    const operationId = beginMediaOperation()
    if (operationId === null) return
    let displayStream: MediaStream | null = null
    let microphoneStream: MediaStream | null = null
    if (session.status === 'running') {
      sessionStatusRef.current = 'paused'
      dispatch({ type: 'pause' })
      stopCapture()
      try {
        await stopMicrophone()
      } finally {
        finishMediaOperation(operationId)
      }
      return
    }

    if (session.status === 'paused') {
      try {
        displayStream = await startCapture(operationId, selectedSource?.id ?? '')
        if (mediaOperationRef.current !== operationId) return
        microphoneStream = await startMicrophone(
          operationId,
          selectedMicrophoneId || undefined
        )
        if (mediaOperationRef.current !== operationId) return
        sessionStatusRef.current = 'running'
        dispatch({ type: 'resume' })
      } catch (error) {
        if (mediaOperationRef.current !== operationId) return
        if (captureStreamRef.current === displayStream) stopCapture()
        if (microphoneStreamRef.current === microphoneStream) await stopMicrophone()
        const overlayError = await releaseOverlay()
        if (mediaOperationRef.current !== operationId) return
        sessionStatusRef.current = 'error'
        dispatch({
          type: 'fail',
          error: `恢复采集失败：${
            microphoneStream
              ? describeMediaError(error, 'microphone')
              : describeMediaError(error, displayStream ? 'microphone' : 'display')
          }${overlayError ? ` ${overlayError}` : ''}`
        })
      } finally {
        if (mediaOperationRef.current !== operationId) {
          if (captureStreamRef.current === displayStream) stopCapture()
          if (microphoneStreamRef.current === microphoneStream) await stopMicrophone()
        }
        finishMediaOperation(operationId)
      }
      return
    }
    finishMediaOperation(operationId)
  }

  const toggleOverlay = async (): Promise<void> => {
    if (overlayVisible) {
      await window.advx.hideOverlay()
      setOverlayVisible(false)
    } else {
      await window.advx.showOverlay()
      setOverlayVisible(true)
    }
  }

  const clearBarrage = async (): Promise<void> => {
    setActivity((current) => current.filter((item) => item.source !== 'audience'))
    await window.advx.clearOverlay()
  }

  const sendUserMessage = (): void => {
    const trimmed = message.trim()
    if (!trimmed) return
    setActivity((current) => [
      ...current.slice(-40),
      {
        id: crypto.randomUUID(),
        source: 'user',
        author: '你',
        text: trimmed
      }
    ])
    setMessage('')
    if (session.status === 'running') {
      window.setTimeout(() => emitBarrage(`听到了。关于“${trimmed.slice(0, 20)}”，我想再看一会儿。`), 550)
    }
  }

  const saveModelConfig = async (): Promise<void> => {
    setConfigNotice(null)
    try {
      const result = await window.advx.saveModelConfig({
        baseUrl: modelBaseUrl,
        model: modelName,
        apiKey
      })
      setApiKey('')
      setConfigNotice(result.securelyStored ? '配置已安全保存' : '普通配置已保存，当前系统无法加密密钥')
    } catch {
      setConfigNotice('保存失败')
    }
  }

  const toggleAudience = (id: string): void => {
    setAudience((current) =>
      current.map((member) => (member.id === id ? { ...member, active: !member.active } : member))
    )
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">AX</div>
          <div>
            <strong>ADVX Live</strong>
            <span>AI 虚拟直播间</span>
          </div>
        </div>

        <nav className="primary-nav" aria-label="主导航">
          <button
            className={activeView === 'live' ? 'active' : ''}
            type="button"
            onClick={() => setActiveView('live')}
          >
            <LayoutDashboard size={18} />
            直播控制台
          </button>
          <button
            className={activeView === 'audience' ? 'active' : ''}
            type="button"
            onClick={() => setActiveView('audience')}
          >
            <Users size={18} />
            AI 观众
            <span className="nav-count">{activeAudience.length}</span>
          </button>
          <button
            className={activeView === 'settings' ? 'active' : ''}
            type="button"
            onClick={() => setActiveView('settings')}
          >
            <Settings size={18} />
            设置
          </button>
        </nav>

        <div className="sidebar-status">
          <div className="status-heading">
            <span>房间信息</span>
            <Activity size={15} />
          </div>
          <div className="compact-status">
            <span>房间号</span>
            <strong>AX-1024</strong>
          </div>
          <div className="compact-status">
            <span>分区</span>
            <strong>虚拟主播</strong>
          </div>
          <div className="compact-status">
            <span>在线观众</span>
            <strong>{activeAudience.length} 人</strong>
          </div>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <div className="topbar-leading">
            <span className={`live-badge ${session.status}`}>
              <span className="live-badge-dot" />
              {statusLabels[session.status]}
            </span>
            <h1 className="topbar-title">
              {activeView === 'live' && '直播控制台'}
              {activeView === 'audience' && 'AI 观众'}
              {activeView === 'settings' && '设置'}
            </h1>
          </div>
          <div className="topbar-stats">
            <span className="stat-chip" title="直播时长">
              <Clock size={14} />
              {formatElapsed(elapsedSeconds)}
            </span>
            <span className="stat-chip" title="累计弹幕">
              <MessageSquareText size={14} />
              {barrageTotal}
            </span>
            <span className="stat-chip" title="在线观众">
              <Users size={14} />
              {activeAudience.length}
            </span>
          </div>
        </header>

        <main className="workspace">
          {activeView === 'live' && (
            <div className="live-view">
              {session.error && <div className="error-banner">{session.error}</div>}

              <div className="live-layout">
                <section className="stage-panel">
                  <div className="stage-toolbar">
                    <div className="stage-source">
                      <MonitorUp size={17} />
                      <div>
                        <span className="panel-title">画面预览</span>
                        <span className="panel-subtitle">{selectedSource?.name ?? '尚未选择来源'}</span>
                      </div>
                    </div>
                    <button
                      className="ghost-button"
                      type="button"
                      disabled={isSessionActive || mediaTransitioning}
                      onClick={() => setSourcePickerOpen(true)}
                    >
                      <MonitorUp size={15} />
                      {selectedSource ? '更换来源' : '选择来源'}
                    </button>
                  </div>

                  <div className="video-stage">
                    {captureStream ? (
                      <video ref={videoRef} autoPlay muted playsInline />
                    ) : selectedSource ? (
                      <img src={selectedSource.thumbnailUrl} alt={`${selectedSource.name} 预览`} />
                    ) : (
                      <div className="stage-empty">
                        <MonitorUp size={30} />
                        <strong>等待画面来源</strong>
                        <span>选择屏幕或窗口后开始预览</span>
                      </div>
                    )}
                    <div className={`stage-badge ${session.status === 'running' ? 'rec' : ''}`}>
                      {session.status === 'running' ? 'REC' : 'PREVIEW'}
                    </div>
                    {session.status === 'paused' && <div className="paused-overlay">观察已暂停</div>}
                  </div>

                  <div className="command-bar" aria-label="会话控制">
                    <button
                      className={`go-live-button ${isSessionActive || session.status === 'error' ? 'is-live' : ''}`}
                      type="button"
                      disabled={goLiveBusy || (!canStart && !isSessionActive && session.status !== 'error')}
                      onClick={toggleGoLive}
                    >
                      {isSessionActive || session.status === 'error' ? (
                        <CircleStop size={18} />
                      ) : (
                        <Play size={18} fill="currentColor" />
                      )}
                      {session.status === 'starting' && '启动中...'}
                      {session.status === 'stopping' && '停止中...'}
                      {(session.status === 'running' ||
                        session.status === 'paused' ||
                        session.status === 'error') &&
                        '结束直播'}
                      {session.status === 'idle' && '开始直播'}
                    </button>
                    <button
                      className="command-button"
                      type="button"
                      disabled={
                        mediaTransitioning ||
                        (session.status !== 'running' && session.status !== 'paused')
                      }
                      onClick={() => void togglePause()}
                      title={session.status === 'paused' ? '恢复观察' : '暂停观察'}
                    >
                      {session.status === 'paused' ? <Play size={16} /> : <Pause size={16} />}
                      {session.status === 'paused' ? '恢复' : '暂停'}
                    </button>
                    <button
                      className="command-button"
                      type="button"
                      disabled={!isSessionActive}
                      onClick={() => void clearBarrage()}
                      title="清空弹幕"
                    >
                      <Trash2 size={16} />
                      清屏
                    </button>
                    <button
                      className="command-button"
                      type="button"
                      disabled={!isSessionActive}
                      onClick={() => void toggleOverlay()}
                      title={overlayVisible ? '隐藏弹幕覆盖层' : '显示弹幕覆盖层'}
                    >
                      {overlayVisible ? <EyeOff size={16} /> : <Eye size={16} />}
                      {overlayVisible ? '隐藏' : '显示'}
                    </button>
                    <span className="command-spacer" />
                    <div className="command-meter" aria-label={`麦克风音量 ${microphoneLevel}%`}>
                      <Mic size={14} />
                      <div className="mini-meter">
                        <span style={{ width: `${microphoneLevel}%` }} />
                      </div>
                    </div>
                  </div>

                  <div className="composer">
                    <MessageSquareText size={17} />
                    <input
                      value={message}
                      onChange={(event) => setMessage(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') sendUserMessage()
                      }}
                      placeholder={session.status === 'running' ? '说点什么，AI 观众会回应你' : '开始直播后可发送'}
                      disabled={session.status !== 'running'}
                    />
                    <button
                      className="icon-button accent"
                      type="button"
                      title="发送"
                      disabled={session.status !== 'running' || message.trim() === ''}
                      onClick={sendUserMessage}
                    >
                      <Send size={16} />
                    </button>
                  </div>
                </section>

                <aside className="right-rail">
                  <section className="chat-panel">
                    <div className="panel-heading compact">
                      <span className="panel-title">房间互动</span>
                      <span className="chat-count">{activity.length}</span>
                    </div>
                    <div className="chat-list" ref={chatListRef}>
                      {activity.map((item) => (
                        <article className={`chat-item ${item.source}`} key={item.id}>
                          <span
                            className="chat-avatar"
                            style={item.color ? { backgroundColor: item.color } : undefined}
                          >
                            {item.author.charAt(0)}
                          </span>
                          <div className="chat-content">
                            <span
                              className="chat-author"
                              style={item.color ? { color: item.color } : undefined}
                            >
                              {item.author}
                              {item.source === 'audience' && <em className="chat-tag">AI</em>}
                            </span>
                            <p>{item.text}</p>
                          </div>
                        </article>
                      ))}
                    </div>
                  </section>

                  <section className="mixer-panel">
                    <div className="panel-heading compact">
                      <span className="panel-title">混音与链路</span>
                      <Gauge size={16} />
                    </div>
                    <div className="mixer-row">
                      <span>
                        <Radio size={14} />
                        画面采集
                      </span>
                      <strong className={captureStream ? 'ok' : ''}>
                        {captureStatus}
                      </strong>
                    </div>
                    <div className="mixer-row">
                      <span>
                        <Mic size={14} />
                        麦克风
                      </span>
                      <div className="mixer-meter" aria-label={`麦克风音量 ${microphoneLevel}%`}>
                        <span style={{ width: `${microphoneLevel}%` }} />
                      </div>
                    </div>
                    <div className="mixer-row">
                      <span>
                        <AudioLines size={14} />
                        本地 ASR
                      </span>
                      <strong>等待后端</strong>
                    </div>
                    <div className="mixer-row">
                      <span>
                        <Sparkles size={14} />
                        多模态模型
                      </span>
                      <strong>演示模式</strong>
                    </div>
                  </section>
                </aside>
              </div>

              <section className="device-strip">
                <div className="device-control">
                  <Mic size={16} />
                  <div>
                    <label htmlFor="microphone">麦克风</label>
                    <select
                      id="microphone"
                      value={selectedMicrophoneId}
                      onChange={(event) => void changeMicrophone(event.target.value)}
                      disabled={isSessionActive || mediaTransitioning}
                    >
                      {microphones.length === 0 && <option value="">未授权设备</option>}
                      {microphones.map((device, index) => (
                        <option key={device.deviceId} value={device.deviceId}>
                          {device.label || `麦克风 ${index + 1}`}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    className="ghost-button"
                    type="button"
                    disabled={isSessionActive || mediaTransitioning}
                    onClick={() => void requestMicrophoneAccess()}
                  >
                    <Volume2 size={15} />
                    {mediaTransitioning
                      ? '检测中...'
                      : microphoneReady
                        ? '重新检测'
                        : '授权并检测'}
                  </button>
                </div>
                <div className="privacy-note">
                  <KeyRound size={14} />
                  {microphonePermission === 'denied' || microphonePermission === 'restricted'
                    ? '系统麦克风权限受限'
                    : microphoneReady
                      ? '正在进行本地音量检测'
                      : '授权后可实时检测麦克风音量'}
                </div>
              </section>
            </div>
          )}

          {activeView === 'audience' && (
            <section className="settings-surface">
              <div className="section-intro">
                <div>
                  <p className="eyebrow">本场参与者</p>
                  <h2>{activeAudience.length} 位 AI 观众已启用</h2>
                </div>
                <Users size={24} />
              </div>
              <div className="audience-list">
                {audience.map((member) => (
                  <article className="audience-row" key={member.id}>
                    <div className="audience-avatar" style={{ backgroundColor: member.color }}>
                      {member.initials}
                    </div>
                    <div className="audience-identity">
                      <strong>{member.name}</strong>
                      <span>AI · {member.role}</span>
                    </div>
                    <p>{member.memory}</p>
                    <label className="switch">
                      <input
                        type="checkbox"
                        checked={member.active}
                        onChange={() => toggleAudience(member.id)}
                      />
                      <span aria-hidden="true" />
                      <em>{member.active ? '参与' : '安静'}</em>
                    </label>
                  </article>
                ))}
              </div>
            </section>
          )}

          {activeView === 'settings' && (
            <div className="settings-columns">
              <section className="settings-surface">
                <div className="section-intro">
                  <div>
                    <p className="eyebrow">模型连接</p>
                    <h2>OpenAI-compatible</h2>
                  </div>
                  <Sparkles size={24} />
                </div>
                <div className="form-stack">
                  <label>
                    服务地址
                    <input value={modelBaseUrl} onChange={(event) => setModelBaseUrl(event.target.value)} />
                  </label>
                  <label>
                    模型名称
                    <input
                      value={modelName}
                      onChange={(event) => setModelName(event.target.value)}
                      placeholder="输入多模态模型名称"
                    />
                  </label>
                  <label>
                    API Key
                    <input
                      type="password"
                      value={apiKey}
                      onChange={(event) => setApiKey(event.target.value)}
                      placeholder="仅由 Electron Main 安全保存"
                    />
                  </label>
                  <div className="form-action">
                    {configNotice && <span>{configNotice}</span>}
                    <button
                      className="primary-button"
                      type="button"
                      disabled={!modelBaseUrl.trim() || !modelName.trim()}
                      onClick={() => void saveModelConfig()}
                    >
                      <KeyRound size={16} />
                      保存连接
                    </button>
                  </div>
                </div>
              </section>

              <section className="settings-surface">
                <div className="section-intro">
                  <div>
                    <p className="eyebrow">弹幕显示</p>
                    <h2>覆盖层偏好</h2>
                  </div>
                  <SlidersHorizontal size={24} />
                </div>
                <div className="slider-stack">
                  <label>
                    <span>
                      不透明度<strong>{barrageOpacity}%</strong>
                    </span>
                    <input
                      type="range"
                      min="30"
                      max="100"
                      value={barrageOpacity}
                      onChange={(event) => setBarrageOpacity(Number(event.target.value))}
                    />
                  </label>
                  <label>
                    <span>
                      移动速度<strong>{barrageSpeed}</strong>
                    </span>
                    <input
                      type="range"
                      min="20"
                      max="100"
                      value={barrageSpeed}
                      onChange={(event) => setBarrageSpeed(Number(event.target.value))}
                    />
                  </label>
                </div>
              </section>
            </div>
          )}
        </main>

        <footer className="status-bar">
          <span className="status-item">
            <i className={`status-dot ${captureStream ? 'online' : ''}`} />
            画面 {captureStatus}
            {screenPermission === 'denied' || screenPermission === 'restricted' ? ' · 权限受限' : ''}
          </span>
          <span className="status-item">
            <i className={`status-dot ${microphoneReady ? 'online' : ''}`} />
            麦克风 {microphoneStatus}
          </span>
          <span className="status-item">
            <i className="status-dot demo" />
            AI 核心 · 演示模式
          </span>
          <span className="status-spacer" />
          <span className="status-item muted">紧急停止 Ctrl/⌘ + Shift + X</span>
        </footer>
      </div>

      {sourcePickerOpen && (
        <SourcePicker onClose={() => setSourcePickerOpen(false)} onSelect={chooseSource} />
      )}
    </div>
  )
}
