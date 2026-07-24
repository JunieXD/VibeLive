import {
  Camera,
  CircleStop,
  Eye,
  EyeOff,
  FlipHorizontal2,
  MessageSquareText,
  Mic,
  MonitorUp,
  Pause,
  PictureInPicture2,
  Play,
  Send,
  Trash2,
} from 'lucide-react'
import { useEffect } from 'react'
import { SelectDropdown } from '../../components/SelectDropdown'
import { bindMediaStreamToVideo } from '../../media'
import {
  COMPRESSION_PROFILES,
  cameraPreviewTransform,
  type CompressionPreset,
  type PipPosition,
  type PipSize,
  type VisualSettings
} from '../../visual'
import { pipPositionLabels, pipSizeLabels, visualModeLabels } from './liveConstants'
import type { LiveStageProps } from './liveTypes'

export function getLiveMessagePlaceholder({
  audienceSessionActive,
  sessionStatus,
  providerConfigured
}: {
  audienceSessionActive: boolean
  sessionStatus: LiveStageProps['session']['status']
  providerConfigured: boolean
}): string {
  if (!audienceSessionActive) {
    return providerConfigured
      ? '开始直播后可与 AI 观众互动'
      : '配置供应商后可与 AI 观众互动'
  }
  return sessionStatus === 'running'
    ? '说点什么，AI 观众会回应你'
    : '开始直播后可发送'
}

export function LiveStage(props: LiveStageProps): React.JSX.Element {
  const {
    session,
    effectiveVisualMode,
    visualSettings,
    setVisualSettings,
    selectedSource,
    captureStream,
    cameraStream,
    cameras,
    cameraEnabled,
    mediaTransitioning,
    isSessionActive,
    canStart,
    goLiveBusy,
    audienceSessionActive,
    overlayVisible,
    barrageTotal,
    microphoneLevel,
    message,
    messageSending,
    providerProbeState,
    targetSuggestions,
    pipPreviewStyle,
    videoRef,
    cameraVideoRef,
    onOpenSourcePicker,
    onChangeVisualMode,
    onToggleGoLive,
    onTogglePause,
    onClearBarrage,
    onToggleOverlay,
    onMessageChange,
    onSelectMessageTarget,
    onSendUserMessage
  } = props

  useEffect(() => {
    bindMediaStreamToVideo(videoRef.current, captureStream)
    bindMediaStreamToVideo(cameraVideoRef.current, cameraStream)
  }, [cameraStream, cameraVideoRef, captureStream, effectiveVisualMode, videoRef])

  const canMessage = session.status === 'running' && audienceSessionActive
  const providerDisplay = getProviderProbeDisplay(providerProbeState)
  const providerProbe = providerProbeState.probe

  return (
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
            <span className="panel-title">{visualModeLabels[effectiveVisualMode]}预览</span>
            <span className="panel-subtitle">
              {effectiveVisualMode === 'camera'
                ? cameras.find((camera) => camera.deviceId === visualSettings.cameraDeviceId)
                    ?.label || '默认摄像头'
                : selectedSource?.name ?? '尚未选择屏幕来源'}
            </span>
          </div>
        </div>
        <button
          className="ghost-button"
          type="button"
          disabled={isSessionActive || mediaTransitioning}
          onClick={onOpenSourcePicker}
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
              onClick={() => void onChangeVisualMode(mode)}
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

        <div className="visual-select">
          <span>采样</span>
          <SelectDropdown
            ariaLabel="视觉采样频率"
            compact
            value={visualSettings.sampleIntervalMs}
            options={[
              { value: 5000, label: '5 秒' },
              { value: 2000, label: '2 秒' },
              { value: 1000, label: '1 秒' },
              { value: 500, label: '0.5 秒' }
            ]}
            onChange={(sampleIntervalMs) =>
              setVisualSettings((current) => ({
                ...current,
                sampleIntervalMs
              }))
            }
          />
        </div>

        <div className="visual-select">
          <span>压缩</span>
          <SelectDropdown
            ariaLabel="图像压缩档位"
            compact
            value={visualSettings.compressionPreset}
            options={(
              Object.entries(COMPRESSION_PROFILES) as [
                CompressionPreset,
                (typeof COMPRESSION_PROFILES)[CompressionPreset]
              ][]
            ).map(([preset, profile]) => ({
              value: preset,
              label: profile.label
            }))}
            onChange={(compressionPreset) =>
              setVisualSettings((current) => ({
                ...current,
                compressionPreset
              }))
            }
          />
        </div>

        {visualSettings.mode === 'pip' && (
          <>
            <div className="visual-select">
              <span>位置</span>
              <SelectDropdown
                ariaLabel="画中画位置"
                compact
                value={visualSettings.pipPosition}
                options={(Object.entries(pipPositionLabels) as [PipPosition, string][]).map(
                  ([position, label]) => ({ value: position, label })
                )}
                onChange={(pipPosition) =>
                  setVisualSettings((current) => ({
                    ...current,
                    pipPosition
                  }))
                }
              />
            </div>
            <div className="visual-select">
              <span>尺寸</span>
              <SelectDropdown
                ariaLabel="画中画尺寸"
                compact
                value={visualSettings.pipSize}
                options={(Object.entries(pipSizeLabels) as [PipSize, string][]).map(
                  ([size, label]) => ({ value: size, label })
                )}
                onChange={(pipSize) =>
                  setVisualSettings((current) => ({
                    ...current,
                    pipSize
                  }))
                }
              />
            </div>
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
            <video className="screen-video" ref={videoRef} autoPlay muted playsInline />
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
            style={{ transform: cameraPreviewTransform(visualSettings.mirrorCamera) }}
          />
        )}
        {effectiveVisualMode === 'pip' && (
          <>
            {captureStream ? (
              <video className="screen-video" ref={videoRef} autoPlay muted playsInline />
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
                  style={{ transform: cameraPreviewTransform(visualSettings.mirrorCamera) }}
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
        <div
          className={`stage-badge ${session.status === 'running' ? 'rec' : ''} ${
            visualSettings.mode === 'pip' && visualSettings.pipPosition === 'top-left'
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
          disabled={
            goLiveBusy || (!canStart && !isSessionActive && session.status !== 'error')
          }
          onClick={onToggleGoLive}
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
        <details className="provider-capability">
          <summary>供应商 · {providerDisplay.label}</summary>
          <div>
            <strong>{providerDisplay.heading}</strong>
            <span>{providerDisplay.detail}</span>
            {providerProbe && (
              <>
                <span>模型：{providerProbe.discovered_model_ids.join('、') || '未发现'}</span>
                {providerProbe.checks.map((check) => (
                  <span key={check.capability}>
                    {check.capability} · {check.status}
                    {check.model_id ? ` · ${check.model_id}` : ''}
                  </span>
                ))}
              </>
            )}
          </div>
        </details>
        <button
          className="command-button"
          type="button"
          disabled={
            mediaTransitioning ||
            (session.status !== 'running' && session.status !== 'paused')
          }
          onClick={() => void onTogglePause()}
          title={session.status === 'paused' ? '恢复观察' : '暂停观察'}
        >
          {session.status === 'paused' ? <Play size={16} /> : <Pause size={16} />}
          {session.status === 'paused' ? '恢复' : '暂停'}
        </button>
        <button
          className="command-button"
          type="button"
          disabled={!overlayVisible && barrageTotal === 0}
          onClick={() => void onClearBarrage()}
          title="清空弹幕"
        >
          <Trash2 size={16} />
          清屏
        </button>
        <button
          className="command-button"
          type="button"
          disabled={!isSessionActive}
          onClick={() => void onToggleOverlay()}
          title={overlayVisible ? '隐藏弹幕窗口' : '显示弹幕窗口'}
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
          onChange={(event) => onMessageChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onSendUserMessage()
          }}
          placeholder={getLiveMessagePlaceholder({
            audienceSessionActive,
            sessionStatus: session.status,
            providerConfigured:
              providerProbeState.profileSaved || providerProbeState.runtimeProviderReady
          })}
          disabled={!canMessage || messageSending}
        />
        <button
          className="icon-button accent"
          type="button"
          title="发送"
          disabled={!canMessage || messageSending || message.trim() === ''}
          onClick={onSendUserMessage}
        >
          <Send size={16} />
        </button>
        {canMessage && targetSuggestions.length > 0 && (
          <div className="mention-menu" role="listbox" aria-label="选择消息目标">
            {targetSuggestions.map((target) => (
              <button
                type="button"
                key={`${target.kind}:${target.id}`}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => onSelectMessageTarget(target)}
              >
                <strong>@{target.label}</strong>
                <span>{target.kind === 'viewer' ? 'Viewer' : 'Persona'}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {audienceSessionActive && (
        <p className="provider-disclosure">
          屏幕帧、用户文字、最终转写和必要房间上下文会发送给已配置的模型供应商；原始音频仅发送给 StepFun ASR，生成模型仅接收最终转写。原始音频和连续帧默认不持久化，Persona、房间长期记忆和 ModeMeme 保存在本机。
        </p>
      )}
    </section>
  )
}

export function getProviderProbeDisplay(state: LiveStageProps['providerProbeState']): {
  label: string
  heading: string
  detail: string
} {
  if (!state.backendConnected) {
    return {
      label: '后端未连接',
      heading: '等待本地后端连接',
      detail: '后端连接完成后才会检测供应商。'
    }
  }
  if (state.profileLoading) {
    return {
      label: '读取配置中',
      heading: '正在读取已保存的供应商档案',
      detail: '供应商凭据存放在本机安全存储中。'
    }
  }
  if (!state.profileSaved) {
    return {
      label: '未配置',
      heading: '尚未保存供应商档案',
      detail: '请在设置中保存模型和语音识别配置。'
    }
  }
  if (state.probing) {
    return {
      label: '检测中',
      heading: '正在验证供应商能力',
      detail: '检测会请求模型列表、文本、图片和并发能力，最长约两分钟。'
    }
  }
  if (state.error) {
    return {
      label: '检测失败',
      heading: '供应商能力检测失败',
      detail: state.error
    }
  }
  if (state.probe?.status === 'passed') {
    return {
      label: '已通过',
      heading: state.probe.provider_profile_id,
      detail: '供应商能力检测已通过。'
    }
  }
  if (state.probe) {
    return {
      label: '检测失败',
      heading: state.probe.provider_profile_id,
      detail: `供应商返回 ${state.probe.status}。请展开查看各项能力结果。`
    }
  }
  if (state.runtimeProviderReady) {
    return {
      label: '运行中',
      heading: state.profileId ?? '当前供应商档案',
      detail: '此直播的供应商已装载到后端运行时。'
    }
  }
  return {
    label: '已保存',
    heading: state.profileId ?? '当前供应商档案',
    detail: '供应商档案已安全保存；开始或恢复直播时将按会话选择装载。'
  }
}
