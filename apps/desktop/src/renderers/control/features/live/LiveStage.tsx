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
    overlayVisible,
    barrageTotal,
    microphoneLevel,
    message,
    pipPreviewStyle,
    videoRef,
    cameraVideoRef,
    compositeCanvasRef,
    onOpenSourcePicker,
    onChangeVisualMode,
    onToggleGoLive,
    onTogglePause,
    onClearBarrage,
    onToggleOverlay,
    onMessageChange,
    onSendUserMessage
  } = props

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

        <label className="visual-select">
          <span>采样</span>
          <select
            aria-label="视觉采样频率"
            value={visualSettings.sampleIntervalMs}
            onChange={(event) =>
              setVisualSettings((current) => ({
                ...current,
                sampleIntervalMs: Number(event.target.value) as VisualSettings['sampleIntervalMs']
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
                {(Object.entries(pipPositionLabels) as [PipPosition, string][]).map(
                  ([position, label]) => (
                    <option value={position} key={position}>
                      {label}
                    </option>
                  )
                )}
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
                {(Object.entries(pipSizeLabels) as [PipSize, string][]).map(([size, label]) => (
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
        <canvas ref={compositeCanvasRef} className="composite-canvas" aria-hidden="true" />
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
          onChange={(event) => onMessageChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onSendUserMessage()
          }}
          placeholder={
            session.status === 'running' ? '说点什么，AI 观众会回应你' : '开始直播后可发送'
          }
          disabled={session.status !== 'running'}
        />
        <button
          className="icon-button accent"
          type="button"
          title="发送"
          disabled={session.status !== 'running' || message.trim() === ''}
          onClick={onSendUserMessage}
        >
          <Send size={16} />
        </button>
      </div>
    </section>
  )
}
