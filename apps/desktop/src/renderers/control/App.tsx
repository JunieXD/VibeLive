import {
  Activity,
  AudioLines,
  Camera,
  CameraOff,
  CircleStop,
  Clock,
  Eye,
  EyeOff,
  FlipHorizontal2,
  Gauge,
  Image as ImageIcon,
  KeyRound,
  LayoutDashboard,
  MessageSquareText,
  Mic,
  MonitorUp,
  Pause,
  PictureInPicture2,
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
import {
  archiveStaleMemes,
  autoIngestMeme,
  compileAudienceWorkspaceSnapshot,
  createInitialAudienceWorkspace,
  type AudienceWorkspaceState,
  type MemeCandidate,
  type RuntimePersona
} from '../../shared/audience'
import type {
  BackendBarrageEvent,
  BackendConnectionState,
  BackendRuntimeStatus,
  BarrageEvent,
  DesktopSource,
  MediaAccessStatus,
  OverlaySettings,
  OverlayTarget,
  SaveAudienceWorkspaceResult
} from '../../shared/contracts'
import { initialSessionState, sessionReducer, type SessionStatus } from '../../shared/session'
import { AudienceWorkspace } from './AudienceWorkspace'
import { AUDIO_SEGMENT_SECONDS, encodePcm16Mono } from './audio'
import { calculateMicrophoneLevel, describeMediaError, stopMediaStream } from './media'
import {
  COMPRESSION_PROFILES,
  cameraPreviewTransform,
  deliverAndReleaseVisualBatch,
  drawCompositeFrame,
  formatFrameKilobytes,
  getPipRectangle,
  loadVisualSettings,
  releaseVisualFrames,
  requiredVisualSources,
  resolveVisualMode,
  saveVisualSettings,
  selectVisualBatchFrames,
  compressCompositeCanvas,
  type CompressionPreset,
  type PipPosition,
  type PipSize,
  type VisualBatchSink,
  type VisualFrame,
  type VisualMode,
  type VisualPipelineStatus,
  type VisualSettings
} from './visual'

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

const visualModeLabels: Record<VisualMode, string> = {
  screen: '屏幕',
  camera: '摄像头',
  pip: '画中画'
}

const pipPositionLabels: Record<PipPosition, string> = {
  'top-left': '左上',
  'top-right': '右上',
  'bottom-left': '左下',
  'bottom-right': '右下'
}

const pipSizeLabels: Record<PipSize, string> = {
  small: '小',
  medium: '中',
  large: '大'
}

const visualPipelineLabels: Record<VisualPipelineStatus, string> = {
  'waiting-backend': '等待后端',
  ready: '已就绪',
  'compression-failed': '压缩失败'
}

const backendConnectionLabels: Record<BackendConnectionState, string> = {
  starting: '正在启动',
  connecting: '连接中',
  connected: '已连接',
  disconnected: '正在恢复',
  failed: '启动失败'
}

const backendAudiencePresentation: Record<string, { name: string; color: string }> = {
  'builtin-luna': { name: 'Luna', color: '#e879a9' },
  'builtin-max': { name: 'Max', color: '#3da9d5' },
  'builtin-nova': { name: 'Nova', color: '#8f7bd8' }
}

function formatElapsed(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(':')
}

function formatBatchTime(timestamp: number | null): string {
  return timestamp === null
    ? '--:--:--'
    : new Date(timestamp).toLocaleTimeString('zh-CN', { hour12: false })
}

function describeBackendError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

function backendAudienceFor(
  audienceId: string,
  personas: readonly RuntimePersona[]
): { name: string; color: string } {
  const localPersona = personas.find((persona) => persona.id === audienceId)
  if (localPersona) return { name: localPersona.name, color: localPersona.color }
  return (
    backendAudiencePresentation[audienceId] ?? {
      name: `AI 观众 ${audienceId.slice(-6)}`,
      color: '#5f8f7a'
    }
  )
}

function proposeDemoMemeCandidate(input: {
  modeId: string
  text: string
  sourceKinds: MemeCandidate['sourceKinds']
  evidenceSummary: string
  personaTags?: readonly string[]
}): MemeCandidate | null {
  const text = input.text.trim()
  const looksLikeRoomMeme =
    /(这下|有说法|绷|笑死|离谱|稳了|好家伙|来了|寄了?|赢了?|输了?|典|急了?|孝|乐|麻了?|草|牛|神|逆天|抽象|懂不懂|[?？!！]{2,}|(.)\1{2,})/u.test(text)
  if (
    text.length < 2 ||
    text.length > 60 ||
    !looksLikeRoomMeme ||
    /(?:1[3-9]\d{9}|[\w.+-]+@[\w.-]+\.[a-z]{2,})/i.test(text)
  ) {
    return null
  }
  return {
    id: `meme-${crypto.randomUUID()}`,
    modeId: input.modeId,
    text,
    personaTags: input.personaTags,
    sourceKinds: input.sourceKinds,
    evidenceSummary: input.evidenceSummary.slice(0, 160),
    createdAt: new Date().toISOString()
  }
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
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null)
  const [cameras, setCameras] = useState<MediaDeviceInfo[]>([])
  const [cameraEnabled, setCameraEnabled] = useState(false)
  const [cameraPermission, setCameraPermission] = useState<MediaAccessStatus>('unknown')
  const [visualSettings, setVisualSettings] = useState<VisualSettings>(() =>
    loadVisualSettings(window.localStorage)
  )
  const [visualPipelineStatus, setVisualPipelineStatus] =
    useState<VisualPipelineStatus>('waiting-backend')
  const [lastFrameBytes, setLastFrameBytes] = useState<number | null>(null)
  const [lastFrameOverTarget, setLastFrameOverTarget] = useState(false)
  const [lastVisualBatchAt, setLastVisualBatchAt] = useState<number | null>(null)
  const [microphones, setMicrophones] = useState<MediaDeviceInfo[]>([])
  const [selectedMicrophoneId, setSelectedMicrophoneId] = useState('')
  const [microphoneLevel, setMicrophoneLevel] = useState(0)
  const [microphoneReady, setMicrophoneReady] = useState(false)
  const [microphonePermission, setMicrophonePermission] =
    useState<MediaAccessStatus>('unknown')
  const [screenPermission, setScreenPermission] = useState<MediaAccessStatus>('unknown')
  const [mediaTransitioning, setMediaTransitioning] = useState(false)
  const [overlayVisible, setOverlayVisible] = useState(true)
  const [audienceWorkspace, setAudienceWorkspace] = useState<AudienceWorkspaceState>(
    createInitialAudienceWorkspace
  )
  const [audienceWorkspaceReady, setAudienceWorkspaceReady] = useState(false)
  const [audienceWorkspaceLoadError, setAudienceWorkspaceLoadError] = useState<string | null>(null)
  const [audienceDocumentsNeedSync, setAudienceDocumentsNeedSync] = useState(false)
  const [activity, setActivity] = useState<ActivityItem[]>([
    {
      id: 'system-ready',
      source: 'system',
      author: '系统',
      text: '控制台已就绪，正在连接本地后端。'
    }
  ])
  const [message, setMessage] = useState('')
  const [messageSending, setMessageSending] = useState(false)
  const [modelBaseUrl, setModelBaseUrl] = useState('https://api.openai.com/v1')
  const [modelName, setModelName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [asrApiKey, setAsrApiKey] = useState('')
  const [configNotice, setConfigNotice] = useState<string | null>(null)
  const [backendStatus, setBackendStatus] = useState<BackendRuntimeStatus | null>(null)
  const [backendRetrying, setBackendRetrying] = useState(false)
  const [overlayTargets, setOverlayTargets] = useState<OverlayTarget[]>([])
  const [overlaySettings, setOverlaySettings] = useState<OverlaySettings | null>(null)
  const [overlaySettingsNotice, setOverlaySettingsNotice] = useState<string | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [barrageTotal, setBarrageTotal] = useState(0)

  const videoRef = useRef<HTMLVideoElement>(null)
  const cameraVideoRef = useRef<HTMLVideoElement>(null)
  const compositeCanvasRef = useRef<HTMLCanvasElement>(null)
  const captureStreamRef = useRef<MediaStream | null>(null)
  const cameraStreamRef = useRef<MediaStream | null>(null)
  const microphoneStreamRef = useRef<MediaStream | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const audioProcessorRef = useRef<ScriptProcessorNode | null>(null)
  const audioChunksRef = useRef<Float32Array[]>([])
  const audioSampleCountRef = useRef(0)
  const audioSampleRateRef = useRef(0)
  const audioSegmentStartedAtRef = useRef<number | null>(null)
  const audioSendQueueRef = useRef<Promise<void>>(Promise.resolve())
  const audioSegmentSequenceRef = useRef(0)
  const audioIngestErrorReportedRef = useRef(false)
  const meterFrameRef = useRef<number | null>(null)
  const mediaOperationRef = useRef(0)
  const mediaTransitionRef = useRef(false)
  const overlaySettingsTimerRef = useRef<number | null>(null)
  const overlaySettingsRevisionRef = useRef(0)
  const overlaySettingsPendingRef = useRef(false)
  const sessionStatusRef = useRef(session.status)
  const startedAtRef = useRef<number | null>(null)
  const chatListRef = useRef<HTMLDivElement>(null)
  const audiencePersistenceErrorRef = useRef(false)
  const audienceDocumentSyncErrorRef = useRef(false)
  const audienceWorkspaceRef = useRef(audienceWorkspace)
  const audienceWorkspaceReadyRef = useRef(audienceWorkspaceReady)
  const audienceLoadRequestRef = useRef(0)

  audienceWorkspaceRef.current = audienceWorkspace
  audienceWorkspaceReadyRef.current = audienceWorkspaceReady
  const visualSettingsRef = useRef(visualSettings)
  const pendingVisualFramesRef = useRef<VisualFrame[]>([])
  const visualRunRef = useRef(0)
  const visualSampleBusyRef = useRef<number | null>(null)
  const visualBatchBusyRef = useRef<number | null>(null)
  const visualFrameSequenceRef = useRef(0)
  const visualBatchSinkRef = useRef<VisualBatchSink>({
    consume: async (batch, signal) => {
      for (const frame of batch.frames) {
        if (signal.aborted) throw new DOMException('Visual delivery aborted.', 'AbortError')
        if (!frame.blob) continue
        const body = new Uint8Array(await frame.blob.arrayBuffer())
        if (signal.aborted) throw new DOMException('Visual delivery aborted.', 'AbortError')
        await window.advx.submitVisualFrame({
          inputId: frame.frameId,
          capturedAtMs: frame.capturedAt,
          mimeType: frame.blob.type || 'image/jpeg',
          body
        })
      }
      return 'accepted'
    }
  })

  const audienceRuntime = useMemo(
    () => compileAudienceWorkspaceSnapshot(audienceWorkspace),
    [audienceWorkspace]
  )
  const activeAudience = audienceRuntime.personas
  const isSessionActive = ['starting', 'running', 'paused', 'stopping'].includes(session.status)
  const requiredSources = requiredVisualSources(visualSettings.mode)
  const canStart =
    session.status === 'idle' &&
    backendStatus?.connection === 'connected' &&
    backendStatus.providersConfigured &&
    selectedMicrophoneId !== '' &&
    (!requiredSources.screen || selectedSource !== null) &&
    (!requiredSources.camera || cameraEnabled)
  const goLiveBusy =
    session.status === 'starting' || session.status === 'stopping' || mediaTransitioning
  const effectiveVisualMode =
    resolveVisualMode(
      visualSettings.mode,
      captureStream !== null || selectedSource !== null,
      cameraStream !== null
    ) ?? visualSettings.mode
  const captureStatus =
    session.status === 'paused'
      ? '已暂停'
      : captureStream
        ? '采集中'
        : selectedSource
          ? '待启动'
          : '未连接'
  const cameraStatus =
    session.status === 'paused'
      ? '已暂停'
      : cameraStream
        ? '采集中'
        : cameraPermission === 'denied' || cameraPermission === 'restricted'
          ? '权限受限'
          : cameraEnabled
            ? '待启动'
            : '已关闭'
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

  const pipPreviewStyle = useMemo(() => {
    const rectangle = getPipRectangle(
      1600,
      900,
      visualSettings.pipPosition,
      visualSettings.pipSize
    )
    return {
      left: `${(rectangle.x / 1600) * 100}%`,
      top: `${(rectangle.y / 900) * 100}%`,
      width: `${(rectangle.width / 1600) * 100}%`,
      height: `${(rectangle.height / 900) * 100}%`
    }
  }, [visualSettings.pipPosition, visualSettings.pipSize])

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

  const applyBackendStatus = useCallback((status: BackendRuntimeStatus): void => {
    setBackendStatus(status)
    if (status.connection !== 'connected') return
    sessionStatusRef.current = status.session.state
    startedAtRef.current = status.session.startedAtMs
    dispatch({
      type: 'sync',
      status: status.session.state,
      error: status.session.state === 'error' ? '后端 Session 进入错误状态。' : null
    })
  }, [])

  useEffect(() => {
    let active = true
    const unsubscribe = window.advx.onBackendStatus((status) => {
      if (active) applyBackendStatus(status)
    })
    void window.advx
      .getBackendStatus()
      .then((status) => {
        if (active) applyBackendStatus(status)
      })
      .catch(() => {
        if (!active) return
        setBackendStatus({
          connection: 'disconnected',
          providersConfigured: false,
          startupError: null,
          session: {
            sessionId: null,
            state: 'idle',
            startedAtMs: null,
            updatedAtMs: Date.now(),
            revision: 0
          }
        })
      })
    return () => {
      active = false
      unsubscribe()
    }
  }, [applyBackendStatus])

  useEffect(() => {
    visualSettingsRef.current = visualSettings
    saveVisualSettings(window.localStorage, visualSettings)
  }, [visualSettings])

  useEffect(() => {
    captureStreamRef.current = captureStream
    if (videoRef.current) {
      videoRef.current.srcObject = captureStream
      if (captureStream) {
        void videoRef.current.play().catch(() => undefined)
      }
    }
  }, [captureStream, effectiveVisualMode])

  useEffect(() => {
    cameraStreamRef.current = cameraStream
    if (cameraVideoRef.current) {
      cameraVideoRef.current.srcObject = cameraStream
      if (cameraStream) {
        void cameraVideoRef.current.play().catch(() => undefined)
      }
    }
  }, [cameraStream, effectiveVisualMode])

  useEffect(() => {
    void window.advx
      .getMediaAccessStatus()
      .then((status) => {
        setMicrophonePermission(status.microphone)
        setCameraPermission(status.camera)
        setScreenPermission(status.screen)
      })
      .catch(() => undefined)
  }, [])

  const handleAudienceSaveResult = useCallback((result: SaveAudienceWorkspaceResult): void => {
    audiencePersistenceErrorRef.current = false
    if (result.personaDocumentsSynced) {
      audienceDocumentSyncErrorRef.current = false
      setAudienceDocumentsNeedSync(false)
      return
    }

    setAudienceDocumentsNeedSync(true)
    if (audienceDocumentSyncErrorRef.current) return
    audienceDocumentSyncErrorRef.current = true
    setActivity((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        source: 'system',
        author: '系统',
        text: result.personaDocumentsError
          ? `观众配置已保存，但 personality.md 暂未同步：${result.personaDocumentsError}`
          : '观众配置已保存，但 personality.md 暂未同步。'
      }
    ])
  }, [])

  const reportAudienceSaveFailure = useCallback((): void => {
    if (audiencePersistenceErrorRef.current) return
    audiencePersistenceErrorRef.current = true
    setActivity((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        source: 'system',
        author: '系统',
        text: '模式与人格配置暂未保存，请检查本地数据目录。'
      }
    ])
  }, [])

  const loadAudienceWorkspace = useCallback(async (): Promise<void> => {
    const requestId = audienceLoadRequestRef.current + 1
    audienceLoadRequestRef.current = requestId
    audienceWorkspaceReadyRef.current = false
    setAudienceWorkspaceReady(false)
    try {
      const storedWorkspace = await window.advx.loadAudienceWorkspace()
      if (audienceLoadRequestRef.current !== requestId) return
      const workspace = storedWorkspace ?? createInitialAudienceWorkspace()
      const hydratedWorkspace = {
        ...workspace,
        memes: archiveStaleMemes(workspace.memes, new Date().toISOString())
      }
      audienceWorkspaceRef.current = hydratedWorkspace
      setAudienceWorkspace(hydratedWorkspace)
      setAudienceWorkspaceLoadError(null)
      audienceWorkspaceReadyRef.current = true
      setAudienceWorkspaceReady(true)
    } catch (error) {
      if (audienceLoadRequestRef.current !== requestId) return
      const message =
        error instanceof Error
          ? error.message
          : '观众配置加载失败，原文件未被覆盖。'
      audienceWorkspaceReadyRef.current = false
      setAudienceWorkspaceLoadError(message)
      setActivity((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          source: 'system',
          author: '系统',
          text: message
        }
      ])
    }
  }, [])

  const resetRejectedAudienceWorkspace = useCallback((): void => {
    audienceLoadRequestRef.current += 1
    const workspace = createInitialAudienceWorkspace()
    audienceWorkspaceRef.current = workspace
    audienceWorkspaceReadyRef.current = true
    setAudienceWorkspace(workspace)
    setAudienceWorkspaceLoadError(null)
    setAudienceWorkspaceReady(true)
    setActivity((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        source: 'system',
        author: '系统',
        text: '已显式重置观众配置；受保护的拒绝文件仍保留在本地数据目录。'
      }
    ])
  }, [])

  useEffect(() => {
    void loadAudienceWorkspace()
    return () => {
      audienceLoadRequestRef.current += 1
    }
  }, [loadAudienceWorkspace])

  useEffect(() => {
    if (!audienceWorkspaceReady) return
    const timer = window.setTimeout(() => {
      void window.advx
        .saveAudienceWorkspace(audienceWorkspace)
        .then(handleAudienceSaveResult)
        .catch(reportAudienceSaveFailure)
    }, 350)
    return () => window.clearTimeout(timer)
  }, [
    audienceWorkspace,
    audienceWorkspaceReady,
    handleAudienceSaveResult,
    reportAudienceSaveFailure
  ])

  useEffect(() => {
    if (!audienceWorkspaceReady || !audienceDocumentsNeedSync) return
    const timer = window.setInterval(() => {
      void window.advx
        .saveAudienceWorkspace(audienceWorkspaceRef.current)
        .then(handleAudienceSaveResult)
        .catch(reportAudienceSaveFailure)
    }, 10_000)
    return () => window.clearInterval(timer)
  }, [
    audienceDocumentsNeedSync,
    audienceWorkspaceReady,
    handleAudienceSaveResult,
    reportAudienceSaveFailure
  ])

  useEffect(
    () =>
      window.advx.onCloseRequested(() => {
        if (!audienceWorkspaceReadyRef.current) {
          void window.advx.confirmCloseAfterAudienceSave()
          return
        }
        void window.advx
          .saveAudienceWorkspace(audienceWorkspaceRef.current)
          .then(handleAudienceSaveResult)
          .catch(reportAudienceSaveFailure)
          .finally(() => window.advx.confirmCloseAfterAudienceSave())
      }),
    [handleAudienceSaveResult, reportAudienceSaveFailure]
  )

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

  useEffect(() => {
    let active = true

    const unsubscribe = window.advx.onOverlaySettingsChanged((settings) => {
      if (!active) return
      void window.advx
        .listOverlayTargets()
        .then((targets) => {
          if (active) setOverlayTargets(targets)
        })
        .catch(() => undefined)
      if (overlaySettingsPendingRef.current) return
      overlaySettingsRevisionRef.current += 1
      setOverlaySettings(settings)
      setOverlaySettingsNotice('已同步')
    })

    void Promise.all([window.advx.listOverlayTargets(), window.advx.getOverlaySettings()])
      .then(([targets, settings]) => {
        if (!active) return
        setOverlayTargets(targets)
        setOverlaySettings(settings)
      })
      .catch(() => {
        if (active) setOverlaySettingsNotice('加载失败')
      })

    return () => {
      active = false
      unsubscribe()
      if (overlaySettingsTimerRef.current !== null) {
        window.clearTimeout(overlaySettingsTimerRef.current)
      }
    }
  }, [])

  const updateOverlaySettings = (settings: OverlaySettings): void => {
    const revision = overlaySettingsRevisionRef.current + 1
    overlaySettingsRevisionRef.current = revision
    overlaySettingsPendingRef.current = true
    setOverlaySettings(settings)
    setOverlaySettingsNotice('正在同步')

    if (overlaySettingsTimerRef.current !== null) {
      window.clearTimeout(overlaySettingsTimerRef.current)
    }
    overlaySettingsTimerRef.current = window.setTimeout(() => {
      overlaySettingsTimerRef.current = null
      void window.advx
        .setOverlaySettings(settings)
        .then((normalizedSettings) => {
          if (overlaySettingsRevisionRef.current !== revision) return
          overlaySettingsPendingRef.current = false
          setOverlaySettings(normalizedSettings)
          setOverlaySettingsNotice('已同步')
        })
        .catch(() => {
          if (overlaySettingsRevisionRef.current === revision) {
            overlaySettingsPendingRef.current = false
            setOverlaySettingsNotice('同步失败')
          }
        })
    }, 150)
  }

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

  const stopCamera = useCallback(() => {
    void window.advx.cancelCameraCaptureAuthorization().catch(() => undefined)
    const stream = cameraStreamRef.current
    cameraStreamRef.current = null
    stopMediaStream(stream)
    if (cameraVideoRef.current?.srcObject === stream) {
      cameraVideoRef.current.pause()
      cameraVideoRef.current.srcObject = null
    }
    setCameraStream(null)
  }, [])

  const flushAudioSegment = useCallback((includePartial = false): Promise<void> => {
    const sampleRate = audioSampleRateRef.current
    const sampleCount = audioSampleCountRef.current
    const minimumSamples = includePartial
      ? Math.round(sampleRate * 0.25)
      : Math.round(sampleRate * AUDIO_SEGMENT_SECONDS)
    if (sampleRate <= 0 || sampleCount < minimumSamples) {
      if (includePartial) {
        audioChunksRef.current = []
        audioSampleCountRef.current = 0
        audioSegmentStartedAtRef.current = null
      }
      return audioSendQueueRef.current
    }

    const chunks = audioChunksRef.current
    const capturedAtMs = audioSegmentStartedAtRef.current ?? Date.now()
    audioChunksRef.current = []
    audioSampleCountRef.current = 0
    audioSegmentStartedAtRef.current = null
    const body = encodePcm16Mono(chunks, sampleRate)
    const sequence = audioSegmentSequenceRef.current + 1
    audioSegmentSequenceRef.current = sequence
    const send = audioSendQueueRef.current.then(() =>
      window.advx.submitAudioSegment({
        inputId: `audio-${capturedAtMs}-${sequence}`,
        capturedAtMs,
        body
      })
    )
    const observed = send.then(
      () => {
        audioIngestErrorReportedRef.current = false
      },
      (error: unknown) => {
        if (!audioIngestErrorReportedRef.current) {
          audioIngestErrorReportedRef.current = true
          setActivity((current) => [
            ...current.slice(-40),
            {
              id: crypto.randomUUID(),
              source: 'system',
              author: '系统',
              text: `音频暂未送达后端：${describeBackendError(error, '实时连接异常。')}`
            }
          ])
        }
      }
    )
    audioSendQueueRef.current = observed
    return observed
  }, [])

  const stopMicrophone = useCallback(async (): Promise<void> => {
    if (meterFrameRef.current !== null) {
      cancelAnimationFrame(meterFrameRef.current)
      meterFrameRef.current = null
    }
    const processor = audioProcessorRef.current
    audioProcessorRef.current = null
    if (processor) {
      processor.onaudioprocess = null
      processor.disconnect()
    }
    const stream = microphoneStreamRef.current
    microphoneStreamRef.current = null
    stopMediaStream(stream)
    await flushAudioSegment(true)
    const context = audioContextRef.current
    audioContextRef.current = null
    if (context && context.state !== 'closed') {
      await context.close().catch(() => undefined)
    }
    setMicrophoneLevel(0)
    setMicrophoneReady(false)
  }, [flushAudioSegment])

  useEffect(() => {
    return () => {
      mediaOperationRef.current += 1
      mediaTransitionRef.current = false
      stopMediaStream(captureStreamRef.current)
      stopMediaStream(cameraStreamRef.current)
      void window.advx.cancelCameraCaptureAuthorization().catch(() => undefined)
      releaseVisualFrames(pendingVisualFramesRef.current)
      pendingVisualFramesRef.current = []
      visualRunRef.current += 1
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
              visualSettingsRef.current.mode === 'pip' &&
              cameraStreamRef.current !== null
            ) {
              setVisualSettings((current) => ({ ...current, mode: 'camera' }))
              setActivity((current) => [
                ...current,
                {
                  id: crypto.randomUUID(),
                  source: 'system',
                  author: '系统',
                  text: '屏幕来源已断开，已自动切换为摄像头画面。'
                }
              ])
              return
            }
            if (
              sessionStatusRef.current === 'running' ||
              sessionStatusRef.current === 'starting'
            ) {
              sessionStatusRef.current = 'error'
              stopCamera()
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
    [assertMediaOperationCurrent, releaseOverlay, stopCamera, stopMicrophone]
  )

  const chooseSource = async (source: DesktopSource): Promise<void> => {
    const operationId = beginMediaOperation()
    if (operationId === null) return
    try {
      setSourcePickerOpen(false)
      await startCapture(operationId, source.id)
      setSelectedSource(source)
      if (cameraEnabled && cameraStreamRef.current) {
        setVisualSettings((current) => ({ ...current, mode: 'pip' }))
      }
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

  const refreshCameras = useCallback(
    async (preferredDeviceId?: string, operationId?: number): Promise<void> => {
      const devices = await navigator.mediaDevices.enumerateDevices()
      if (operationId !== undefined) {
        assertMediaOperationCurrent(operationId)
      }
      const inputs = devices.filter((device) => device.kind === 'videoinput')
      setCameras(inputs)
      setVisualSettings((current) => {
        if (inputs.some((device) => device.deviceId === current.cameraDeviceId)) return current
        const cameraDeviceId =
          preferredDeviceId &&
          inputs.some((device) => device.deviceId === preferredDeviceId)
            ? preferredDeviceId
            : inputs[0]?.deviceId ?? ''
        return cameraDeviceId === current.cameraDeviceId
          ? current
          : { ...current, cameraDeviceId }
      })
    },
    [assertMediaOperationCurrent]
  )

  useEffect(() => {
    const handleDeviceChange = (): void => {
      void refreshMicrophones()
      void refreshCameras()
    }

    void refreshMicrophones()
    void refreshCameras()
    navigator.mediaDevices.addEventListener('devicechange', handleDeviceChange)
    return () => navigator.mediaDevices.removeEventListener('devicechange', handleDeviceChange)
  }, [refreshCameras, refreshMicrophones])

  const startCamera = useCallback(
    async (operationId: number, deviceId?: string): Promise<MediaStream> => {
      const authorized = await window.advx.authorizeCameraCapture()
      assertMediaOperationCurrent(operationId)
      if (!authorized) {
        throw new DOMException('Camera access is denied by the operating system.', 'NotAllowedError')
      }

      let stream: MediaStream
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: {
            deviceId: deviceId ? { exact: deviceId } : undefined,
            width: { ideal: 1280 },
            height: { ideal: 720 },
            frameRate: { ideal: 15, max: 24 }
          },
          audio: false
        })
      } finally {
        await window.advx.cancelCameraCaptureAuthorization().catch(() => undefined)
      }
      try {
        assertMediaOperationCurrent(operationId)
        const videoTrack = stream.getVideoTracks()[0]
        if (!videoTrack) {
          throw new DOMException('No camera video track was created.', 'NotReadableError')
        }

        const actualDeviceId = videoTrack.getSettings().deviceId
        await refreshCameras(actualDeviceId, operationId)
        assertMediaOperationCurrent(operationId)

        const previousStream = cameraStreamRef.current
        cameraStreamRef.current = stream
        stopMediaStream(previousStream)
        setCameraStream(stream)
        setCameraEnabled(true)
        setCameraPermission('granted')

        videoTrack.addEventListener(
          'ended',
          () => {
            if (cameraStreamRef.current !== stream) return
            mediaOperationRef.current += 1
            mediaTransitionRef.current = false
            setMediaTransitioning(false)
            cameraStreamRef.current = null
            setCameraStream(null)
            setCameraEnabled(false)
            void refreshCameras()
            if (
              visualSettingsRef.current.mode === 'pip' &&
              captureStreamRef.current !== null
            ) {
              setVisualSettings((current) => ({ ...current, mode: 'screen' }))
              setActivity((current) => [
                ...current,
                {
                  id: crypto.randomUUID(),
                  source: 'system',
                  author: '系统',
                  text: '摄像头已断开，已自动切换为屏幕画面。'
                }
              ])
              return
            }
            if (
              sessionStatusRef.current === 'running' ||
              sessionStatusRef.current === 'starting'
            ) {
              sessionStatusRef.current = 'error'
              stopCapture()
              void stopMicrophone()
              void releaseOverlay()
              dispatch({ type: 'fail', error: '摄像头连接已中断，请检查设备。' })
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
    [
      assertMediaOperationCurrent,
      refreshCameras,
      releaseOverlay,
      stopCapture,
      stopMicrophone
    ]
  )

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
        const source = context.createMediaStreamSource(stream)
        source.connect(analyser)
        const processor = context.createScriptProcessor(4096, 1, 1)
        const silentOutput = context.createGain()
        silentOutput.gain.value = 0
        source.connect(processor)
        processor.connect(silentOutput)
        silentOutput.connect(context.destination)
        processor.onaudioprocess = (event): void => {
          if (sessionStatusRef.current !== 'running') return
          const samples = event.inputBuffer.getChannelData(0)
          if (samples.length === 0) return
          if (audioSegmentStartedAtRef.current === null) {
            audioSegmentStartedAtRef.current = Date.now()
          }
          const copy = new Float32Array(samples)
          audioChunksRef.current.push(copy)
          audioSampleCountRef.current += copy.length
          audioSampleRateRef.current = context?.sampleRate ?? event.inputBuffer.sampleRate
          if (
            audioSampleCountRef.current >=
            audioSampleRateRef.current * AUDIO_SEGMENT_SECONDS
          ) {
            void flushAudioSegment()
          }
        }
        if (context.state === 'suspended') {
          await context.resume()
          assertMediaOperationCurrent(operationId)
        }

        const actualDeviceId = audioTrack.getSettings().deviceId
        await refreshMicrophones(actualDeviceId, operationId)
        assertMediaOperationCurrent(operationId)

        microphoneStreamRef.current = stream
        audioContextRef.current = context
        audioProcessorRef.current = processor
        audioChunksRef.current = []
        audioSampleCountRef.current = 0
        audioSampleRateRef.current = context.sampleRate
        audioSegmentStartedAtRef.current = null
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
            const activeProcessor = audioProcessorRef.current
            audioProcessorRef.current = null
            if (activeProcessor) {
              activeProcessor.onaudioprocess = null
              activeProcessor.disconnect()
            }
            if (meterFrameRef.current !== null) {
              cancelAnimationFrame(meterFrameRef.current)
              meterFrameRef.current = null
            }
            void flushAudioSegment(true)
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
      flushAudioSegment,
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

  const toggleCamera = useCallback(async (): Promise<void> => {
    const operationId = beginMediaOperation(true)
    if (operationId === null) return

    if (cameraEnabled && cameraStreamRef.current) {
      stopCamera()
      setCameraEnabled(false)
      if (captureStreamRef.current) {
        setVisualSettings((current) => ({ ...current, mode: 'screen' }))
        setActivity((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            source: 'system',
            author: '系统',
            text: '摄像头已关闭，继续使用屏幕画面。'
          }
        ])
      } else if (
        sessionStatusRef.current === 'running' ||
        sessionStatusRef.current === 'starting'
      ) {
        sessionStatusRef.current = 'error'
        void stopMicrophone()
        void releaseOverlay()
        dispatch({ type: 'fail', error: '唯一的摄像头画面已关闭，视觉采样已停止。' })
      } else {
        setVisualSettings((current) => ({ ...current, mode: 'screen' }))
      }
      finishMediaOperation(operationId)
      return
    }

    let startedCamera: MediaStream | null = null
    let startedDisplay: MediaStream | null = null
    try {
      const nativeStatus = await window.advx.requestCameraPermission()
      assertMediaOperationCurrent(operationId)
      setCameraPermission(nativeStatus)
      if (nativeStatus === 'denied' || nativeStatus === 'restricted') {
        throw new DOMException('Camera access is denied by the operating system.', 'NotAllowedError')
      }
      startedCamera = await startCamera(
        operationId,
        visualSettingsRef.current.cameraDeviceId || undefined
      )
      let hasScreen = captureStreamRef.current !== null
      if (!hasScreen && selectedSource) {
        try {
          startedDisplay = await startCapture(operationId, selectedSource.id)
          hasScreen = true
        } catch (error) {
          if (mediaOperationRef.current !== operationId) return
          setActivity((current) => [
            ...current,
            {
              id: crypto.randomUUID(),
              source: 'system',
              author: '系统',
              text: `${describeMediaError(error, 'display')} 已改用摄像头全屏。`
            }
          ])
        }
      }
      assertMediaOperationCurrent(operationId)
      setCameraEnabled(true)
      setVisualSettings((current) => ({ ...current, mode: hasScreen ? 'pip' : 'camera' }))
    } catch (error) {
      if (mediaOperationRef.current !== operationId) return
      if (cameraStreamRef.current === startedCamera) stopCamera()
      if (captureStreamRef.current === startedDisplay) stopCapture()
      setCameraEnabled(false)
      setActivity((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          source: 'system',
          author: '系统',
          text: describeMediaError(error, 'camera')
        }
      ])
    } finally {
      finishMediaOperation(operationId)
    }
  }, [
    assertMediaOperationCurrent,
    beginMediaOperation,
    cameraEnabled,
    finishMediaOperation,
    releaseOverlay,
    selectedSource,
    startCamera,
    startCapture,
    stopCamera,
    stopCapture,
    stopMicrophone
  ])

  const changeCamera = useCallback(
    async (deviceId: string): Promise<void> => {
      const previousDeviceId = visualSettingsRef.current.cameraDeviceId
      setVisualSettings((current) => ({ ...current, cameraDeviceId: deviceId }))
      if (!cameraStreamRef.current) return

      const operationId = beginMediaOperation()
      if (operationId === null) return
      try {
        await startCamera(operationId, deviceId || undefined)
      } catch (error) {
        if (mediaOperationRef.current !== operationId) return
        setVisualSettings((current) => ({ ...current, cameraDeviceId: previousDeviceId }))
        setActivity((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            source: 'system',
            author: '系统',
            text: describeMediaError(error, 'camera')
          }
        ])
      } finally {
        finishMediaOperation(operationId)
      }
    },
    [beginMediaOperation, finishMediaOperation, startCamera]
  )

  const changeVisualMode = useCallback(
    async (nextMode: VisualMode): Promise<void> => {
      if (nextMode === visualSettingsRef.current.mode) return
      const requirements = requiredVisualSources(nextMode)
      if (requirements.screen && !selectedSource) {
        setSourcePickerOpen(true)
        return
      }
      if (requirements.camera && !cameraEnabled) {
        setActivity((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            source: 'system',
            author: '系统',
            text: '请先显式开启摄像头。'
          }
        ])
        return
      }

      const operationId = beginMediaOperation()
      if (operationId === null) return
      const previousDisplay = captureStreamRef.current
      const previousCamera = cameraStreamRef.current
      try {
        if (requirements.screen && !captureStreamRef.current) {
          await startCapture(operationId, selectedSource?.id ?? '')
        }
        if (requirements.camera && !cameraStreamRef.current) {
          await startCamera(
            operationId,
            visualSettingsRef.current.cameraDeviceId || undefined
          )
        }
        assertMediaOperationCurrent(operationId)
        setVisualSettings((current) => ({ ...current, mode: nextMode }))
        if (!requirements.screen) stopCapture()
        if (!requirements.camera) stopCamera()
      } catch (error) {
        if (mediaOperationRef.current !== operationId) return
        if (!previousDisplay && captureStreamRef.current) stopCapture()
        if (!previousCamera && cameraStreamRef.current) stopCamera()
        const kind = requirements.camera && !previousCamera ? 'camera' : 'display'
        setActivity((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            source: 'system',
            author: '系统',
            text: describeMediaError(error, kind)
          }
        ])
      } finally {
        finishMediaOperation(operationId)
      }
    },
    [
      assertMediaOperationCurrent,
      beginMediaOperation,
      cameraEnabled,
      finishMediaOperation,
      selectedSource,
      startCamera,
      startCapture,
      stopCamera,
      stopCapture
    ]
  )

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

  const acceptDirectorMemeCandidate = useCallback(
    (candidate: MemeCandidate): void => {
      setAudienceWorkspace((current) => {
        if (!current.modeState.modes.some((mode) => mode.id === candidate.modeId)) return current
        const result = autoIngestMeme(current.memes, candidate)
        return result.accepted ? { ...current, memes: result.entries } : current
      })
    },
    []
  )

  useEffect(() => {
    return window.advx.onBackendBarrage((backendEvent: BackendBarrageEvent) => {
      const member = backendAudienceFor(backendEvent.audienceId, activeAudience)
      const event: BarrageEvent = {
        ...backendEvent,
        audienceName: member.name,
        color: member.color
      }
      setBarrageTotal((current) => current + 1)
      setActivity((current) => [
        ...current.slice(-40),
        {
          id: event.barrageId,
          source: 'audience',
          author: event.audienceName,
          text: event.text,
          color: event.color
        }
      ])
      if (overlayVisible) void window.advx.pushBarrage(event)

      const candidate = proposeDemoMemeCandidate({
        modeId: audienceRuntime.mode.id,
        text: event.text,
        sourceKinds: ['audience_barrage'],
        evidenceSummary: `${event.audienceName} 在真实直播互动中形成的房间短句`,
        personaTags: [event.audienceId]
      })
      if (candidate) acceptDirectorMemeCandidate(candidate)
    })
  }, [activeAudience, acceptDirectorMemeCandidate, audienceRuntime.mode.id, overlayVisible])

  useEffect(() => {
    const runId = visualRunRef.current + 1
    visualRunRef.current = runId
    releaseVisualFrames(pendingVisualFramesRef.current)
    pendingVisualFramesRef.current = []

    if (session.status !== 'running') {
      setVisualPipelineStatus('waiting-backend')
      return
    }

    setVisualPipelineStatus('waiting-backend')
    const profile = COMPRESSION_PROFILES[visualSettings.compressionPreset]
    const batchAbortController = new AbortController()

    const sampleFrame = async (): Promise<void> => {
      if (visualSampleBusyRef.current !== null) return
      const requirements = requiredVisualSources(visualSettings.mode)
      const screenVideo = videoRef.current
      const cameraVideo = cameraVideoRef.current
      if (
        (requirements.screen &&
          (!captureStreamRef.current || !screenVideo || screenVideo.videoWidth === 0)) ||
        (requirements.camera &&
          (!cameraStreamRef.current || !cameraVideo || cameraVideo.videoWidth === 0))
      ) {
        return
      }

      const canvas = compositeCanvasRef.current
      if (!canvas) return
      visualSampleBusyRef.current = runId
      try {
        const primaryVideo =
          visualSettings.mode === 'camera' ? cameraVideo : screenVideo
        const outputLongEdge = Math.min(
          profile.maxLongEdge,
          Math.max(primaryVideo?.videoWidth ?? 0, primaryVideo?.videoHeight ?? 0)
        )
        if (outputLongEdge <= 0) return
        const drawn = drawCompositeFrame(canvas, {
          mode: visualSettings.mode,
          screen: requirements.screen ? screenVideo : null,
          camera: requirements.camera ? cameraVideo : null,
          mirrorCamera: visualSettings.mirrorCamera,
          pipPosition: visualSettings.pipPosition,
          pipSize: visualSettings.pipSize,
          longEdge: outputLongEdge
        })
        if (!drawn) return

        const encoded = await compressCompositeCanvas(canvas, profile)
        if (visualRunRef.current !== runId || sessionStatusRef.current !== 'running') return
        const sequence = visualFrameSequenceRef.current + 1
        visualFrameSequenceRef.current = sequence
        const frame: VisualFrame = {
          frameId: `visual-${Date.now()}-${sequence}`,
          capturedAt: Date.now(),
          width: encoded.width,
          height: encoded.height,
          mode: visualSettings.mode,
          bytes: encoded.blob.size,
          overTarget: encoded.overTarget,
          blob: encoded.blob
        }
        pendingVisualFramesRef.current.push(frame)
        setLastFrameBytes(frame.bytes)
        setLastFrameOverTarget(frame.overTarget)
      } catch {
        if (visualRunRef.current === runId) {
          setVisualPipelineStatus('compression-failed')
        }
      } finally {
        if (visualSampleBusyRef.current === runId) {
          visualSampleBusyRef.current = null
        }
      }
    }

    const flushBatch = async (): Promise<void> => {
      if (
        visualBatchBusyRef.current !== null ||
        pendingVisualFramesRef.current.length === 0
      ) {
        return
      }
      visualBatchBusyRef.current = runId
      const pending = pendingVisualFramesRef.current
      pendingVisualFramesRef.current = []
      const selected = selectVisualBatchFrames(pending)
      const selectedIds = new Set(selected.map((frame) => frame.frameId))
      releaseVisualFrames(pending.filter((frame) => !selectedIds.has(frame.frameId)))
      if (visualRunRef.current !== runId) {
        releaseVisualFrames(selected)
        visualBatchBusyRef.current = null
        return
      }

      const createdAt = Date.now()
      try {
        const result = await deliverAndReleaseVisualBatch(
          visualBatchSinkRef.current,
          {
            batchId: `visual-batch-${createdAt}`,
            createdAt,
            frames: selected
          },
          batchAbortController.signal
        )
        if (visualRunRef.current === runId) {
          setLastVisualBatchAt(createdAt)
          setVisualPipelineStatus(result === 'accepted' ? 'ready' : 'waiting-backend')
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return
        if (visualRunRef.current === runId) {
          setVisualPipelineStatus('waiting-backend')
        }
      } finally {
        if (visualBatchBusyRef.current === runId) {
          visualBatchBusyRef.current = null
        }
      }
    }

    void sampleFrame()
    const sampleTimer = window.setInterval(
      () => void sampleFrame(),
      visualSettings.sampleIntervalMs
    )
    const batchTimer = window.setInterval(() => void flushBatch(), 3000)
    return () => {
      window.clearInterval(sampleTimer)
      window.clearInterval(batchTimer)
      batchAbortController.abort()
      if (visualRunRef.current === runId) visualRunRef.current += 1
      if (visualSampleBusyRef.current === runId) visualSampleBusyRef.current = null
      if (visualBatchBusyRef.current === runId) visualBatchBusyRef.current = null
      releaseVisualFrames(pendingVisualFramesRef.current)
      pendingVisualFramesRef.current = []
    }
  }, [
    cameraStream,
    captureStream,
    session.status,
    visualSettings.compressionPreset,
    visualSettings.mirrorCamera,
    visualSettings.mode,
    visualSettings.pipPosition,
    visualSettings.pipSize,
    visualSettings.sampleIntervalMs
  ])

  const startSession = async (): Promise<void> => {
    const operationId = beginMediaOperation()
    if (operationId === null) return
    const requirements = requiredVisualSources(visualSettingsRef.current.mode)
    let displayStream: MediaStream | null = captureStreamRef.current
    let activeCameraStream: MediaStream | null = cameraStreamRef.current
    let microphoneStream: MediaStream | null = microphoneStreamRef.current
    let backendSessionStarted = false
    sessionStatusRef.current = 'starting'
    dispatch({ type: 'start' })
    try {
      if (requirements.screen && !displayStream) {
        try {
          displayStream = await startCapture(operationId, selectedSource?.id ?? '')
        } catch (error) {
          throw new Error(describeMediaError(error, 'display'))
        }
      }
      if (mediaOperationRef.current !== operationId) return

      if (requirements.camera && !activeCameraStream) {
        try {
          activeCameraStream = await startCamera(
            operationId,
            visualSettingsRef.current.cameraDeviceId || undefined
          )
        } catch (error) {
          throw new Error(describeMediaError(error, 'camera'))
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

      const backendSession = await window.advx.startBackendSession()
      backendSessionStarted = backendSession.state === 'running'
      if (mediaOperationRef.current !== operationId) {
        if (backendSessionStarted) await window.advx.stopBackendSession().catch(() => undefined)
        return
      }
      await window.advx.showOverlay()
      if (mediaOperationRef.current !== operationId) {
        await window.advx.hideOverlay()
        if (backendSessionStarted) await window.advx.stopBackendSession().catch(() => undefined)
        return
      }
      setOverlayVisible(true)
      sessionStatusRef.current = backendSession.state
      startedAtRef.current = backendSession.startedAtMs
      dispatch({ type: 'sync', status: backendSession.state })
    } catch (error) {
      if (mediaOperationRef.current !== operationId) return
      if (captureStreamRef.current === displayStream) stopCapture()
      if (cameraStreamRef.current === activeCameraStream) stopCamera()
      if (microphoneStreamRef.current === microphoneStream) await stopMicrophone()
      if (backendSessionStarted) {
        await window.advx.stopBackendSession().catch(() => undefined)
      }
      const overlayError = await releaseOverlay()
      if (mediaOperationRef.current !== operationId) return
      sessionStatusRef.current = 'error'
      dispatch({
        type: 'fail',
        error: `${error instanceof Error ? error.message : '启动失败，请检查视觉来源和麦克风权限。'}${
          overlayError ? ` ${overlayError}` : ''
        }`
      })
    } finally {
      if (mediaOperationRef.current !== operationId) {
        if (captureStreamRef.current === displayStream) stopCapture()
        if (cameraStreamRef.current === activeCameraStream) stopCamera()
        if (microphoneStreamRef.current === microphoneStream) await stopMicrophone()
      }
      finishMediaOperation(operationId)
    }
  }

  const stopSession = useCallback(async () => {
    const operationId = beginMediaOperation(true)
    if (operationId === null) return
    let stopError: string | null = null
    sessionStatusRef.current = 'stopping'
    dispatch({ type: 'stop' })
    stopCapture()
    stopCamera()
    await stopMicrophone()
    try {
      if (backendStatus?.session.sessionId) {
        await window.advx.stopBackendSession()
      }
    } catch (error) {
      stopError = `后端 Session 未能确认停止：${describeBackendError(error, '连接异常。')}`
    }
    try {
      const overlayError = await releaseOverlay()
      if (mediaOperationRef.current !== operationId) return
      if (overlayError || stopError) {
        setActivity((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            source: 'system',
            author: '系统',
            text: [stopError, overlayError].filter(Boolean).join(' ')
          }
        ])
      }
    } finally {
      if (mediaOperationRef.current === operationId) {
        sessionStatusRef.current = stopError ? 'error' : 'idle'
        if (stopError) dispatch({ type: 'fail', error: stopError })
        else dispatch({ type: 'stopped' })
      }
      finishMediaOperation(operationId)
    }
  }, [
    beginMediaOperation,
    backendStatus?.session.sessionId,
    finishMediaOperation,
    releaseOverlay,
    stopCamera,
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
    let activeCameraStream: MediaStream | null = null
    let microphoneStream: MediaStream | null = null
    let resumeFailureKind: 'display' | 'camera' | 'microphone' = 'display'
    if (session.status === 'running') {
      sessionStatusRef.current = 'paused'
      dispatch({ type: 'pause' })
      stopCapture()
      stopCamera()
      try {
        await stopMicrophone()
        const backendSession = await window.advx.pauseBackendSession()
        sessionStatusRef.current = backendSession.state
        dispatch({ type: 'sync', status: backendSession.state })
      } catch (error) {
        sessionStatusRef.current = 'error'
        dispatch({
          type: 'fail',
          error: `暂停后端 Session 失败：${describeBackendError(error, '连接异常。')}`
        })
      } finally {
        finishMediaOperation(operationId)
      }
      return
    }

    if (session.status === 'paused') {
      try {
        const requirements = requiredVisualSources(visualSettingsRef.current.mode)
        if (requirements.screen) {
          resumeFailureKind = 'display'
          displayStream = await startCapture(operationId, selectedSource?.id ?? '')
          if (mediaOperationRef.current !== operationId) return
        }
        if (requirements.camera) {
          resumeFailureKind = 'camera'
          activeCameraStream = await startCamera(
            operationId,
            visualSettingsRef.current.cameraDeviceId || undefined
          )
          if (mediaOperationRef.current !== operationId) return
        }
        resumeFailureKind = 'microphone'
        microphoneStream = await startMicrophone(
          operationId,
          selectedMicrophoneId || undefined
        )
        if (mediaOperationRef.current !== operationId) return
        const backendSession = await window.advx.resumeBackendSession()
        sessionStatusRef.current = backendSession.state
        dispatch({ type: 'sync', status: backendSession.state })
      } catch (error) {
        if (mediaOperationRef.current !== operationId) return
        if (captureStreamRef.current === displayStream) stopCapture()
        if (cameraStreamRef.current === activeCameraStream) stopCamera()
        if (microphoneStreamRef.current === microphoneStream) await stopMicrophone()
        const overlayError = await releaseOverlay()
        if (mediaOperationRef.current !== operationId) return
        sessionStatusRef.current = 'error'
        dispatch({
          type: 'fail',
          error: `恢复采集或后端 Session 失败：${
            error instanceof DOMException
              ? describeMediaError(error, resumeFailureKind)
              : describeBackendError(error, '连接异常。')
          }${
            overlayError ? ` ${overlayError}` : ''
          }`
        })
      } finally {
        if (mediaOperationRef.current !== operationId) {
          if (captureStreamRef.current === displayStream) stopCapture()
          if (cameraStreamRef.current === activeCameraStream) stopCamera()
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

  const sendUserMessage = async (): Promise<void> => {
    const trimmed = message.trim()
    if (!trimmed || messageSending || session.status !== 'running') return
    setMessageSending(true)
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
    const candidate = proposeDemoMemeCandidate({
      modeId: audienceRuntime.mode.id,
      text: trimmed,
      sourceKinds: ['user_text'],
      evidenceSummary: `用户在房间文字中主动说出：“${trimmed.slice(0, 72)}”`
    })
    if (candidate) acceptDirectorMemeCandidate(candidate)
    try {
      await window.advx.submitUserText(trimmed)
    } catch (error) {
      setMessage((current) => current || trimmed)
      setActivity((current) => [
        ...current.slice(-40),
        {
          id: crypto.randomUUID(),
          source: 'system',
          author: '系统',
          text: `文字未送达后端：${describeBackendError(error, '实时连接异常。')}`
        }
      ])
    } finally {
      setMessageSending(false)
    }
  }

  const saveModelConfig = async (): Promise<void> => {
    setConfigNotice(null)
    try {
      const result = await window.advx.saveModelConfig({
        baseUrl: modelBaseUrl,
        model: modelName,
        apiKey,
        asrApiKey
      })
      setApiKey('')
      setAsrApiKey('')
      const status = await window.advx.getBackendStatus()
      applyBackendStatus(status)
      setConfigNotice(
        result.restartRequired
          ? '配置已保存；后端已使用另一组配置，请重启桌面应用后生效'
          : result.securelyStored
            ? '模型与语音识别配置已安全保存并接入后端'
            : '配置已接入本次运行；当前系统无法加密密钥，因此密钥不会落盘'
      )
    } catch (error) {
      setConfigNotice(`保存失败：${describeBackendError(error, '请检查后端连接和配置内容。')}`)
    }
  }

  const retryBackend = async (): Promise<void> => {
    if (backendRetrying) return
    setBackendRetrying(true)
    try {
      applyBackendStatus(await window.advx.restartBackend())
    } catch {
      // The Main process publishes the actionable startup error through onBackendStatus.
    } finally {
      setBackendRetrying(false)
    }
  }

  const backendConnection = backendStatus?.connection ?? 'starting'
  const backendNotice =
    backendConnection === 'connected'
      ? null
      : backendConnection === 'failed'
        ? {
            title: '本地服务启动失败',
            detail: backendStatus?.startupError ?? '请重试，或检查本地后端文件和日志。'
          }
        : backendConnection === 'disconnected'
          ? {
              title: '本地服务连接中断',
              detail: '正在自动恢复连接，恢复前不会发送新的音频或画面。'
            }
          : backendConnection === 'connecting'
            ? {
                title: '正在连接实时管线',
                detail: '本地服务已经启动，正在建立安全连接。'
              }
            : {
                title: '正在启动本地服务',
                detail: '通常只需要几秒，完成后即可配置模型或开始直播。'
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
            <span>当前模式</span>
            <strong title={audienceRuntime.mode.name}>{audienceRuntime.mode.name}</strong>
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
          {backendNotice && (
            <section
              className={`backend-notice ${backendConnection === 'failed' ? 'failed' : ''}`}
              role={backendConnection === 'failed' ? 'alert' : 'status'}
            >
              <div>
                <strong>{backendNotice.title}</strong>
                <span>{backendNotice.detail}</span>
              </div>
              {backendConnection === 'failed' && (
                <button
                  className="secondary-button"
                  type="button"
                  disabled={backendRetrying}
                  onClick={() => void retryBackend()}
                >
                  <RefreshCw size={15} />
                  {backendRetrying ? '正在重试' : '重试'}
                </button>
              )}
            </section>
          )}
          {activeView === 'live' && (
            <div className="live-view">
              {session.error && <div className="error-banner">{session.error}</div>}

              <div className="live-layout">
                <section className="stage-panel">
                  <div className="stage-toolbar">
                    <div className="stage-source">
                      {effectiveVisualMode === 'pip' ? (
                        <PictureInPicture2 size={17} />
                      ) : effectiveVisualMode === 'camera' ? (
                        <Camera size={17} />
                      ) : (
                        <MonitorUp size={17} />
                      )}
                      <div>
                        <span className="panel-title">
                          {visualModeLabels[effectiveVisualMode]}预览
                        </span>
                        <span className="panel-subtitle">
                          {effectiveVisualMode === 'camera'
                            ? cameras.find(
                                (camera) =>
                                  camera.deviceId === visualSettings.cameraDeviceId
                              )?.label || '默认摄像头'
                            : selectedSource?.name ?? '尚未选择屏幕来源'}
                        </span>
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

                  <div className="visual-toolbar" aria-label="视觉设置">
                    <div className="segmented-control" aria-label="视觉模式">
                      {(['screen', 'camera', 'pip'] as const).map((mode) => (
                        <button
                          className={visualSettings.mode === mode ? 'active' : ''}
                          type="button"
                          key={mode}
                          disabled={
                            mediaTransitioning ||
                            session.status === 'paused' ||
                            session.status === 'starting' ||
                            session.status === 'stopping' ||
                            (mode === 'camera' && !cameraEnabled) ||
                            (mode === 'pip' && (!cameraEnabled || !selectedSource))
                          }
                          title={
                            mode !== 'screen' && !cameraEnabled
                              ? '请先开启摄像头'
                              : `切换到${visualModeLabels[mode]}`
                          }
                          onClick={() => void changeVisualMode(mode)}
                        >
                          {mode === 'screen' ? (
                            <MonitorUp size={14} />
                          ) : mode === 'camera' ? (
                            <Camera size={14} />
                          ) : (
                            <PictureInPicture2 size={14} />
                          )}
                          {visualModeLabels[mode]}
                        </button>
                      ))}
                    </div>

                    <label className="visual-select">
                      <span>采样</span>
                      <select
                        aria-label="视觉采样频率"
                        value={visualSettings.sampleIntervalMs}
                        onChange={(event) =>
                          setVisualSettings((current) => ({
                            ...current,
                            sampleIntervalMs: Number(
                              event.target.value
                            ) as VisualSettings['sampleIntervalMs']
                          }))
                        }
                      >
                        <option value={5000}>5 秒</option>
                        <option value={2000}>2 秒</option>
                        <option value={1000}>1 秒</option>
                        <option value={500}>0.5 秒</option>
                      </select>
                    </label>

                    <label className="visual-select">
                      <span>压缩</span>
                      <select
                        aria-label="图像压缩档位"
                        value={visualSettings.compressionPreset}
                        onChange={(event) =>
                          setVisualSettings((current) => ({
                            ...current,
                            compressionPreset: event.target.value as CompressionPreset
                          }))
                        }
                      >
                        {(
                          Object.entries(COMPRESSION_PROFILES) as [
                            CompressionPreset,
                            (typeof COMPRESSION_PROFILES)[CompressionPreset]
                          ][]
                        ).map(([preset, profile]) => (
                          <option value={preset} key={preset}>
                            {profile.label}
                          </option>
                        ))}
                      </select>
                    </label>

                    {visualSettings.mode === 'pip' && (
                      <>
                        <label className="visual-select">
                          <span>位置</span>
                          <select
                            aria-label="画中画位置"
                            value={visualSettings.pipPosition}
                            onChange={(event) =>
                              setVisualSettings((current) => ({
                                ...current,
                                pipPosition: event.target.value as PipPosition
                              }))
                            }
                          >
                            {(
                              Object.entries(pipPositionLabels) as [PipPosition, string][]
                            ).map(([position, label]) => (
                              <option value={position} key={position}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="visual-select">
                          <span>尺寸</span>
                          <select
                            aria-label="画中画尺寸"
                            value={visualSettings.pipSize}
                            onChange={(event) =>
                              setVisualSettings((current) => ({
                                ...current,
                                pipSize: event.target.value as PipSize
                              }))
                            }
                          >
                            {(
                              Object.entries(pipSizeLabels) as [PipSize, string][]
                            ).map(([size, label]) => (
                              <option value={size} key={size}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </label>
                      </>
                    )}

                    <label className="visual-toggle">
                      <input
                        type="checkbox"
                        checked={visualSettings.mirrorCamera}
                        disabled={!cameraEnabled}
                        onChange={(event) =>
                          setVisualSettings((current) => ({
                            ...current,
                            mirrorCamera: event.target.checked
                          }))
                        }
                      />
                      <FlipHorizontal2 size={14} />
                      镜像
                    </label>
                  </div>

                  <div className="video-stage">
                    {effectiveVisualMode === 'screen' &&
                      (captureStream ? (
                        <video
                          className="screen-video"
                          ref={videoRef}
                          autoPlay
                          muted
                          playsInline
                        />
                      ) : selectedSource ? (
                        <img
                          className="screen-preview-image"
                          src={selectedSource.thumbnailUrl}
                          alt={`${selectedSource.name} 预览`}
                        />
                      ) : null)}
                    {effectiveVisualMode === 'camera' && cameraStream && (
                      <video
                        className="camera-video camera-primary"
                        ref={cameraVideoRef}
                        autoPlay
                        muted
                        playsInline
                        style={{
                          transform: cameraPreviewTransform(visualSettings.mirrorCamera)
                        }}
                      />
                    )}
                    {effectiveVisualMode === 'pip' && (
                      <>
                        {captureStream ? (
                          <video
                            className="screen-video"
                            ref={videoRef}
                            autoPlay
                            muted
                            playsInline
                          />
                        ) : selectedSource ? (
                          <img
                            className="screen-preview-image"
                            src={selectedSource.thumbnailUrl}
                            alt={`${selectedSource.name} 预览`}
                          />
                        ) : null}
                        {cameraStream && (
                          <div className="camera-pip" style={pipPreviewStyle}>
                            <video
                              className="camera-video"
                              ref={cameraVideoRef}
                              autoPlay
                              muted
                              playsInline
                              style={{
                                transform: cameraPreviewTransform(
                                  visualSettings.mirrorCamera
                                )
                              }}
                            />
                          </div>
                        )}
                      </>
                    )}
                    {!captureStream &&
                      !cameraStream &&
                      !(effectiveVisualMode === 'screen' && selectedSource) && (
                      <div className="stage-empty">
                        {cameraEnabled ? <Camera size={30} /> : <MonitorUp size={30} />}
                        <strong>等待视觉来源</strong>
                        <span>选择屏幕或显式开启摄像头</span>
                      </div>
                    )}
                    <canvas ref={compositeCanvasRef} className="composite-canvas" aria-hidden="true" />
                    <div
                      className={`stage-badge ${session.status === 'running' ? 'rec' : ''} ${
                        visualSettings.mode === 'pip' &&
                        visualSettings.pipPosition === 'top-left'
                          ? 'avoid-pip'
                          : ''
                      }`}
                    >
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
                      disabled={!overlayVisible && barrageTotal === 0}
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
                        if (event.key === 'Enter') void sendUserMessage()
                      }}
                      placeholder={session.status === 'running' ? '说点什么，AI 观众会回应你' : '开始直播后可发送'}
                      disabled={session.status !== 'running'}
                    />
                    <button
                      className="icon-button accent"
                      type="button"
                      title="发送"
                      disabled={
                        session.status !== 'running' || message.trim() === '' || messageSending
                      }
                      onClick={() => void sendUserMessage()}
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
                        屏幕采集
                      </span>
                      <strong className={captureStream ? 'ok' : ''}>
                        {captureStatus}
                      </strong>
                    </div>
                    <div className="mixer-row">
                      <span>
                        <Camera size={14} />
                        摄像头
                      </span>
                      <strong className={cameraStream ? 'ok' : ''}>{cameraStatus}</strong>
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
                        实时 ASR
                      </span>
                      <strong
                        className={
                          backendStatus?.connection === 'connected' &&
                          backendStatus.providersConfigured
                            ? 'ok'
                            : ''
                        }
                      >
                        {backendStatus?.connection !== 'connected'
                          ? '等待后端'
                          : backendStatus.providersConfigured
                            ? '已接入'
                            : '等待配置'}
                      </strong>
                    </div>
                    <div className="mixer-row">
                      <span>
                        <ImageIcon size={14} />
                        合成压缩
                      </span>
                      <strong className={lastFrameOverTarget ? 'warning' : ''}>
                        {COMPRESSION_PROFILES[visualSettings.compressionPreset].label}
                        {lastFrameBytes !== null
                          ? ` · ${formatFrameKilobytes(lastFrameBytes)}`
                          : ''}
                        {lastFrameOverTarget ? ' · 超出目标' : ''}
                      </strong>
                    </div>
                    <div className="mixer-row">
                      <span>
                        <Clock size={14} />
                        最近批次
                      </span>
                      <strong>{formatBatchTime(lastVisualBatchAt)}</strong>
                    </div>
                    <div className="mixer-row">
                      <span>
                        <Sparkles size={14} />
                        图像适配器
                      </span>
                      <strong
                        className={
                          visualPipelineStatus === 'ready'
                            ? 'ok'
                            : visualPipelineStatus === 'compression-failed'
                              ? 'warning'
                              : ''
                        }
                      >
                        {visualPipelineLabels[visualPipelineStatus]}
                      </strong>
                    </div>
                  </section>
                </aside>
              </div>

              <section className="device-strip">
                <div className="device-grid">
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

                  <div className="device-control">
                    <Camera size={16} />
                    <div>
                      <label htmlFor="camera">摄像头</label>
                      <select
                        id="camera"
                        value={visualSettings.cameraDeviceId}
                        onChange={(event) => void changeCamera(event.target.value)}
                        disabled={
                          mediaTransitioning ||
                          session.status === 'paused' ||
                          session.status === 'starting' ||
                          session.status === 'stopping'
                        }
                      >
                        {cameras.length === 0 && <option value="">未检测到设备</option>}
                        {cameras.map((device, index) => (
                          <option key={device.deviceId || `camera-${index}`} value={device.deviceId}>
                            {device.label || `摄像头 ${index + 1}`}
                          </option>
                        ))}
                      </select>
                    </div>
                    <button
                      className={`ghost-button ${cameraStream ? 'camera-active' : ''}`}
                      type="button"
                      disabled={
                        mediaTransitioning ||
                        session.status === 'paused' ||
                        session.status === 'starting' ||
                        session.status === 'stopping'
                      }
                      onClick={() => void toggleCamera()}
                    >
                      {cameraStream ? <CameraOff size={15} /> : <Camera size={15} />}
                      {cameraStream
                        ? '关闭摄像头'
                        : cameraEnabled
                          ? '重新开启'
                          : '开启摄像头'}
                    </button>
                  </div>
                </div>
                <div className="privacy-stack">
                  <div className="privacy-note">
                    <KeyRound size={14} />
                    {microphonePermission === 'denied' ||
                    microphonePermission === 'restricted'
                      ? '系统麦克风权限受限'
                      : microphoneReady
                        ? '正在进行本地音量检测'
                        : '授权后可实时检测麦克风音量'}
                  </div>
                  <div className="privacy-note">
                    <Camera size={14} />
                    {cameraPermission === 'denied' || cameraPermission === 'restricted'
                      ? '系统摄像头权限受限'
                      : cameraStream
                        ? '摄像头视频仅保存在内存'
                        : cameraEnabled
                          ? '当前视觉模式未使用摄像头'
                          : '摄像头默认关闭'}
                  </div>
                </div>
              </section>
            </div>
          )}

          {activeView === 'audience' && (
            <AudienceWorkspace
              workspace={audienceWorkspace}
              sessionStatus={session.status}
              persistenceReady={audienceWorkspaceReady}
              persistenceIssue={audienceWorkspaceLoadError}
              onChange={setAudienceWorkspace}
              onRetryLoad={() => void loadAudienceWorkspace()}
              onResetRejected={resetRejectedAudienceWorkspace}
            />
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
                    模型 API Key
                    <input
                      type="password"
                      value={apiKey}
                      onChange={(event) => setApiKey(event.target.value)}
                      placeholder="仅由 Electron Main 安全保存"
                    />
                  </label>
                  <label>
                    StepFun ASR API Key
                    <input
                      type="password"
                      value={asrApiKey}
                      onChange={(event) => setAsrApiKey(event.target.value)}
                      placeholder="用于实时语音识别"
                    />
                  </label>
                  <div className="form-action">
                    {configNotice && <span>{configNotice}</span>}
                    <button
                      className="primary-button"
                      type="button"
                      disabled={
                        backendStatus?.connection !== 'connected' ||
                        !modelBaseUrl.trim() ||
                        !modelName.trim() ||
                        !apiKey.trim() ||
                        !asrApiKey.trim()
                      }
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
                    <h2>弹幕覆盖层</h2>
                  </div>
                  <SlidersHorizontal size={24} />
                </div>
                {!overlaySettings ? (
                  <div className="overlay-settings-loading">
                    {overlaySettingsNotice ?? '正在读取覆盖层设置...'}
                  </div>
                ) : (
                  <div className="overlay-settings-form">
                    <label className="overlay-target-field">
                      <span>弹幕目标</span>
                      <select
                        aria-label="弹幕目标"
                        value={overlaySettings.targetDisplayId}
                        onChange={(event) =>
                          updateOverlaySettings({
                            ...overlaySettings,
                            targetDisplayId: Number(event.target.value)
                          })
                        }
                      >
                        {overlayTargets.map((target) => (
                          <option key={target.id} value={target.id}>
                            {target.isPrimary ? '主屏 · ' : ''}
                            {target.name} · {target.bounds.width} × {target.bounds.height}
                          </option>
                        ))}
                      </select>
                    </label>

                    <div className="slider-stack">
                      <label>
                        <span>
                          字号<strong>{overlaySettings.fontSizePx}px</strong>
                        </span>
                        <input
                          aria-label="字号"
                          type="range"
                          min="14"
                          max="36"
                          value={overlaySettings.fontSizePx}
                          onChange={(event) =>
                            updateOverlaySettings({
                              ...overlaySettings,
                              fontSizePx: Number(event.target.value)
                            })
                          }
                        />
                      </label>
                      <label>
                        <span>
                          移动速度<strong>{overlaySettings.speed}</strong>
                        </span>
                        <input
                          aria-label="移动速度"
                          type="range"
                          min="20"
                          max="100"
                          value={overlaySettings.speed}
                          onChange={(event) =>
                            updateOverlaySettings({
                              ...overlaySettings,
                              speed: Number(event.target.value)
                            })
                          }
                        />
                      </label>
                      <label>
                        <span>
                          透明度<strong>{overlaySettings.opacity}%</strong>
                        </span>
                        <input
                          aria-label="透明度"
                          type="range"
                          min="30"
                          max="100"
                          value={overlaySettings.opacity}
                          onChange={(event) =>
                            updateOverlaySettings({
                              ...overlaySettings,
                              opacity: Number(event.target.value)
                            })
                          }
                        />
                      </label>
                      <label>
                        <span>
                          密度<strong>{overlaySettings.density}</strong>
                        </span>
                        <input
                          aria-label="密度"
                          type="range"
                          min="1"
                          max="10"
                          value={overlaySettings.density}
                          onChange={(event) =>
                            updateOverlaySettings({
                              ...overlaySettings,
                              density: Number(event.target.value)
                            })
                          }
                        />
                      </label>
                      <label>
                        <span>
                          显示区域顶部<strong>{overlaySettings.region.topPercent}%</strong>
                        </span>
                        <input
                          aria-label="显示区域顶部"
                          type="range"
                          min="0"
                          max={overlaySettings.region.bottomPercent - 20}
                          value={overlaySettings.region.topPercent}
                          onChange={(event) =>
                            updateOverlaySettings({
                              ...overlaySettings,
                              region: {
                                ...overlaySettings.region,
                                topPercent: Number(event.target.value)
                              }
                            })
                          }
                        />
                      </label>
                      <label>
                        <span>
                          显示区域底部<strong>{overlaySettings.region.bottomPercent}%</strong>
                        </span>
                        <input
                          aria-label="显示区域底部"
                          type="range"
                          min={overlaySettings.region.topPercent + 20}
                          max="100"
                          value={overlaySettings.region.bottomPercent}
                          onChange={(event) =>
                            updateOverlaySettings({
                              ...overlaySettings,
                              region: {
                                ...overlaySettings.region,
                                bottomPercent: Number(event.target.value)
                              }
                            })
                          }
                        />
                      </label>
                    </div>

                    <div className="overlay-toggle-row">
                      <span>
                        点击穿透
                        {overlaySettingsNotice && (
                          <small role="status" aria-live="polite">
                            {overlaySettingsNotice}
                          </small>
                        )}
                      </span>
                      <label className="switch">
                        <input
                          aria-label="点击穿透"
                          type="checkbox"
                          checked={overlaySettings.clickThrough}
                          onChange={(event) =>
                            updateOverlaySettings({
                              ...overlaySettings,
                              clickThrough: event.target.checked
                            })
                          }
                        />
                        <span aria-hidden="true" />
                        <em>{overlaySettings.clickThrough ? '开启' : '关闭'}</em>
                      </label>
                    </div>
                  </div>
                )}
              </section>
            </div>
          )}
        </main>

        <footer className="status-bar">
          <span className="status-item">
            <i
              className={`status-dot ${
                backendConnection === 'connected'
                  ? 'online'
                  : backendConnection === 'failed'
                    ? 'failed'
                    : 'demo'
              }`}
            />
            后端 ·{' '}
            {backendConnectionLabels[backendConnection]}
          </span>
          <span className="status-item">
            <i className={`status-dot ${captureStream ? 'online' : ''}`} />
            屏幕 {captureStatus}
            {screenPermission === 'denied' || screenPermission === 'restricted' ? ' · 权限受限' : ''}
          </span>
          <span className="status-item">
            <i className={`status-dot ${cameraStream ? 'online' : ''}`} />
            摄像头 {cameraStatus}
          </span>
          <span className="status-item">
            <i className={`status-dot ${microphoneReady ? 'online' : ''}`} />
            麦克风 {microphoneStatus}
          </span>
          <span className="status-item">
            <i className={`status-dot ${visualPipelineStatus === 'ready' ? 'online' : 'demo'}`} />
            图像 · {visualPipelineLabels[visualPipelineStatus]}
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
