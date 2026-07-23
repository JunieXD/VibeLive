import {
  Activity,
  AudioLines,
  CircleStop,
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
import type { BarrageEvent, DesktopSource } from '../../shared/contracts'
import { demoLines, initialAudience, type AudienceMember } from '../../shared/demo'
import { initialSessionState, sessionReducer, type SessionStatus } from '../../shared/session'

type ActiveView = 'live' | 'audience' | 'settings'

type ActivityItem = {
  id: string
  source: 'user' | 'audience' | 'system'
  author: string
  text: string
  color?: string
}

const statusLabels: Record<SessionStatus, string> = {
  idle: '待机',
  starting: '启动中',
  running: '直播中',
  paused: '已暂停',
  stopping: '停止中',
  error: '需要处理'
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

  const videoRef = useRef<HTMLVideoElement>(null)
  const captureStreamRef = useRef<MediaStream | null>(null)
  const microphoneStreamRef = useRef<MediaStream | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const meterFrameRef = useRef<number | null>(null)
  const barrageSequenceRef = useRef(0)
  const sessionStatusRef = useRef(session.status)

  const activeAudience = useMemo(() => audience.filter((member) => member.active), [audience])
  const isSessionActive = ['starting', 'running', 'paused', 'stopping'].includes(session.status)
  const canStart =
    session.status === 'idle' && selectedSource !== null && selectedMicrophoneId !== ''

  useEffect(() => {
    sessionStatusRef.current = session.status
  }, [session.status])

  useEffect(() => {
    captureStreamRef.current = captureStream
    if (videoRef.current) {
      videoRef.current.srcObject = captureStream
    }
  }, [captureStream])

  const stopCapture = useCallback(() => {
    setCaptureStream((current) => {
      current?.getTracks().forEach((track) => track.stop())
      captureStreamRef.current = null
      return null
    })
  }, [])

  const stopMicrophone = useCallback(() => {
    if (meterFrameRef.current !== null) {
      cancelAnimationFrame(meterFrameRef.current)
      meterFrameRef.current = null
    }
    microphoneStreamRef.current?.getTracks().forEach((track) => track.stop())
    microphoneStreamRef.current = null
    void audioContextRef.current?.close()
    audioContextRef.current = null
    setMicrophoneLevel(0)
    setMicrophoneReady(false)
  }, [])

  useEffect(() => {
    return () => {
      captureStreamRef.current?.getTracks().forEach((track) => track.stop())
      stopMicrophone()
    }
  }, [stopMicrophone])

  const startCapture = useCallback(async (): Promise<MediaStream> => {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: {
        frameRate: { ideal: 12, max: 20 }
      },
      audio: false
    })
    setCaptureStream((current) => {
      current?.getTracks().forEach((track) => track.stop())
      captureStreamRef.current = stream
      return stream
    })
    stream.getVideoTracks()[0]?.addEventListener(
      'ended',
      () => {
        setCaptureStream(null)
        if (sessionStatusRef.current === 'running') {
          dispatch({ type: 'fail', error: '画面来源已结束，请重新选择。' })
        }
      },
      { once: true }
    )
    return stream
  }, [])

  const chooseSource = async (source: DesktopSource): Promise<void> => {
    const accepted = await window.advx.selectDesktopSource(source.id)
    if (!accepted) {
      setActivity((current) => [
        ...current,
        { id: crypto.randomUUID(), source: 'system', author: '系统', text: '该画面来源已失效。' }
      ])
      return
    }

    setSelectedSource(source)
    setSourcePickerOpen(false)
    try {
      await startCapture()
    } catch {
      setActivity((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          source: 'system',
          author: '系统',
          text: '未能启动画面预览，请重新授权录屏权限。'
        }
      ])
    }
  }

  const requestMicrophoneAccess = useCallback(async () => {
    try {
      const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true })
      permissionStream.getTracks().forEach((track) => track.stop())
      const devices = await navigator.mediaDevices.enumerateDevices()
      const inputs = devices.filter((device) => device.kind === 'audioinput')
      setMicrophones(inputs)
      setSelectedMicrophoneId((current) => current || inputs[0]?.deviceId || '')
    } catch {
      setActivity((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          source: 'system',
          author: '系统',
          text: '麦克风权限被拒绝，直播会话暂时无法开始。'
        }
      ])
    }
  }, [])

  useEffect(() => {
    void navigator.mediaDevices.enumerateDevices().then((devices) => {
      const inputs = devices.filter((device) => device.kind === 'audioinput')
      setMicrophones(inputs)
      setSelectedMicrophoneId(inputs[0]?.deviceId ?? '')
    })
  }, [])

  const startMicrophone = useCallback(async (): Promise<void> => {
    stopMicrophone()
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: selectedMicrophoneId ? { exact: selectedMicrophoneId } : undefined,
        echoCancellation: true,
        noiseSuppression: true
      }
    })
    microphoneStreamRef.current = stream
    const context = new AudioContext()
    const analyser = context.createAnalyser()
    analyser.fftSize = 256
    context.createMediaStreamSource(stream).connect(analyser)
    audioContextRef.current = context

    const levels = new Uint8Array(analyser.frequencyBinCount)
    const measure = (): void => {
      analyser.getByteFrequencyData(levels)
      const average = levels.reduce((total, value) => total + value, 0) / levels.length
      setMicrophoneLevel(Math.min(100, Math.round(average * 1.8)))
      meterFrameRef.current = requestAnimationFrame(measure)
    }
    measure()
    setMicrophoneReady(true)
  }, [selectedMicrophoneId, stopMicrophone])

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

      setActivity((current) => [
        ...current.slice(-30),
        {
          id: event.barrageId,
          source: 'audience',
          author: `${member.name} · AI`,
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
    dispatch({ type: 'start' })
    try {
      if (!captureStream) {
        await startCapture()
      }
      if (!microphoneReady) {
        await startMicrophone()
      }
      await window.advx.showOverlay()
      setOverlayVisible(true)
      dispatch({ type: 'started' })
      emitBarrage('画面和声音都收到啦，今天从这里开始。')
    } catch {
      dispatch({ type: 'fail', error: '启动失败，请检查屏幕和麦克风权限。' })
    }
  }

  const stopSession = useCallback(async () => {
    dispatch({ type: 'stop' })
    stopCapture()
    stopMicrophone()
    await window.advx.clearOverlay()
    await window.advx.hideOverlay()
    setOverlayVisible(false)
    dispatch({ type: 'stopped' })
  }, [stopCapture, stopMicrophone])

  useEffect(() => window.advx.onEmergencyStop(() => void stopSession()), [stopSession])

  const togglePause = (): void => {
    if (session.status === 'running') {
      dispatch({ type: 'pause' })
    } else if (session.status === 'paused') {
      dispatch({ type: 'resume' })
    }
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
      ...current.slice(-30),
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
            <span>系统状态</span>
            <Activity size={15} />
          </div>
          <div className="compact-status">
            <span className={`status-dot ${captureStream ? 'online' : ''}`} />
            画面采集
            <strong>{captureStream ? '正常' : '待配置'}</strong>
          </div>
          <div className="compact-status">
            <span className={`status-dot ${microphoneReady ? 'online' : ''}`} />
            麦克风
            <strong>{microphoneReady ? '正常' : '待配置'}</strong>
          </div>
          <div className="compact-status">
            <span className="status-dot demo" />
            AI 核心
            <strong>演示</strong>
          </div>
          <span className="shortcut-hint">紧急停止 Ctrl/⌘ + Shift + X</span>
        </div>
      </aside>

      <main className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">本地会话</p>
            <h1>
              {activeView === 'live' && '直播控制台'}
              {activeView === 'audience' && 'AI 观众'}
              {activeView === 'settings' && '设置'}
            </h1>
          </div>
          <div className={`session-badge ${session.status}`}>
            <span />
            {statusLabels[session.status]}
          </div>
        </header>

        {activeView === 'live' && (
          <>
            <section className="command-bar" aria-label="会话控制">
              <button
                className="primary-button"
                type="button"
                disabled={!canStart}
                onClick={() => void startSession()}
              >
                <Play size={17} fill="currentColor" />
                开始
              </button>
              <button
                className="command-button"
                type="button"
                disabled={session.status !== 'running' && session.status !== 'paused'}
                onClick={togglePause}
              >
                {session.status === 'paused' ? <Play size={17} /> : <Pause size={17} />}
                {session.status === 'paused' ? '恢复' : '暂停'}
              </button>
              <button
                className="command-button"
                type="button"
                disabled={!isSessionActive}
                onClick={() => void clearBarrage()}
              >
                <Trash2 size={17} />
                清屏
              </button>
              <button
                className="command-button"
                type="button"
                disabled={!isSessionActive}
                onClick={() => void toggleOverlay()}
              >
                {overlayVisible ? <EyeOff size={17} /> : <Eye size={17} />}
                {overlayVisible ? '隐藏' : '显示'}
              </button>
              <span className="command-spacer" />
              <button
                className="danger-button"
                type="button"
                disabled={!isSessionActive && session.status !== 'error'}
                onClick={() => void stopSession()}
              >
                <CircleStop size={17} />
                停止
              </button>
            </section>

            {session.error && <div className="error-banner">{session.error}</div>}

            <div className="live-layout">
              <section className="stage-panel">
                <div className="panel-heading">
                  <div>
                    <span className="panel-title">画面预览</span>
                    <span className="panel-subtitle">{selectedSource?.name ?? '尚未选择来源'}</span>
                  </div>
                  <button className="secondary-button" type="button" onClick={() => setSourcePickerOpen(true)}>
                    <MonitorUp size={16} />
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
                      <MonitorUp size={28} />
                      <strong>等待画面来源</strong>
                    </div>
                  )}
                  <div className="preview-label">PREVIEW</div>
                  {session.status === 'paused' && <div className="paused-overlay">观察已暂停</div>}
                </div>

                <div className="composer">
                  <MessageSquareText size={18} />
                  <input
                    value={message}
                    onChange={(event) => setMessage(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') sendUserMessage()
                    }}
                    placeholder={session.status === 'running' ? '发送一条文字弹幕' : '开始会话后可发送'}
                    disabled={session.status !== 'running'}
                  />
                  <button
                    className="icon-button accent"
                    type="button"
                    title="发送"
                    disabled={session.status !== 'running' || message.trim() === ''}
                    onClick={sendUserMessage}
                  >
                    <Send size={17} />
                  </button>
                </div>
              </section>

              <aside className="right-rail">
                <section className="runtime-panel">
                  <div className="panel-heading compact">
                    <span className="panel-title">运行状态</span>
                    <Gauge size={17} />
                  </div>
                  <div className="runtime-row">
                    <span><Radio size={15} />画面</span>
                    <strong className={captureStream ? 'positive' : ''}>
                      {captureStream ? '采集中' : '未连接'}
                    </strong>
                  </div>
                  <div className="runtime-row">
                    <span><Mic size={15} />麦克风</span>
                    <div className="level-meter" aria-label={`麦克风音量 ${microphoneLevel}%`}>
                      <span style={{ width: `${microphoneLevel}%` }} />
                    </div>
                  </div>
                  <div className="runtime-row">
                    <span><AudioLines size={15} />本地 ASR</span>
                    <strong>等待后端</strong>
                  </div>
                  <div className="runtime-row">
                    <span><Sparkles size={15} />模型</span>
                    <strong>演示模式</strong>
                  </div>
                </section>

                <section className="activity-panel">
                  <div className="panel-heading compact">
                    <span className="panel-title">房间动态</span>
                    <span className="activity-count">{activity.length}</span>
                  </div>
                  <div className="activity-list">
                    {activity.slice(-12).map((item) => (
                      <article className={`activity-item ${item.source}`} key={item.id}>
                        <span className="activity-author" style={{ color: item.color }}>
                          {item.author}
                        </span>
                        <p>{item.text}</p>
                      </article>
                    ))}
                  </div>
                </section>
              </aside>
            </div>

            <section className="device-strip">
              <div className="device-control">
                <Mic size={17} />
                <div>
                  <label htmlFor="microphone">麦克风</label>
                  <select
                    id="microphone"
                    value={selectedMicrophoneId}
                    onChange={(event) => setSelectedMicrophoneId(event.target.value)}
                    disabled={isSessionActive}
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
                  className="secondary-button"
                  type="button"
                  disabled={isSessionActive}
                  onClick={() => void requestMicrophoneAccess()}
                >
                  <Volume2 size={16} />
                  检测设备
                </button>
              </div>
              <div className="privacy-note">
                <KeyRound size={16} />
                原始麦克风音频仅供本地处理
              </div>
            </section>
          </>
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
                  <span>不透明度<strong>{barrageOpacity}%</strong></span>
                  <input
                    type="range"
                    min="30"
                    max="100"
                    value={barrageOpacity}
                    onChange={(event) => setBarrageOpacity(Number(event.target.value))}
                  />
                </label>
                <label>
                  <span>移动速度<strong>{barrageSpeed}</strong></span>
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

      {sourcePickerOpen && (
        <SourcePicker onClose={() => setSourcePickerOpen(false)} onSelect={chooseSource} />
      )}
    </div>
  )
}
