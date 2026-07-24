import { AudioLines, Camera, Clock, Gauge, Image as ImageIcon, Mic, Radio, Sparkles } from 'lucide-react'
import { COMPRESSION_PROFILES, formatFrameKilobytes } from '../../visual'
import { formatBatchTime, visualPipelineLabels } from './liveConstants'
import type { LiveMixerProps } from './liveTypes'

export function LiveMixer(props: LiveMixerProps): React.JSX.Element {
  const {
    captureStream,
    cameraStream,
    captureStatus,
    cameraStatus,
    microphoneLevel,
    asrReady,
    asrStatus,
    visualSettings,
    lastFrameBytes,
    lastFrameOverTarget,
    lastVisualBatchAt,
    visualPipelineStatus
  } = props

  return (
    <section className="mixer-panel">
      <div className="panel-heading compact">
        <span className="panel-title">混音与链路</span>
        <Gauge size={16} />
      </div>
      <div className="mixer-scroll" tabIndex={0} aria-label="混音与链路状态">
        <div className="mixer-row">
          <span>
            <Radio size={14} />
            屏幕采集
          </span>
          <strong className={captureStream ? 'ok' : ''}>{captureStatus}</strong>
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
            云端 ASR
          </span>
          <strong className={asrReady ? 'ok' : ''}>{asrStatus}</strong>
        </div>
        <div className="mixer-row">
          <span>
            <ImageIcon size={14} />
            合成压缩
          </span>
          <strong className={lastFrameOverTarget ? 'warning' : ''}>
            {COMPRESSION_PROFILES[visualSettings.compressionPreset].label}
            {lastFrameBytes !== null ? ` · ${formatFrameKilobytes(lastFrameBytes)}` : ''}
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
              visualPipelineStatus === 'ready' || visualPipelineStatus === 'local-preview'
                ? 'ok'
                : visualPipelineStatus === 'compression-failed'
                  ? 'warning'
                  : ''
            }
          >
            {visualPipelineLabels[visualPipelineStatus]}
          </strong>
        </div>
      </div>
    </section>
  )
}
