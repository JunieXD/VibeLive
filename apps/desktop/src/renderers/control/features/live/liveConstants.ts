import type {
  PipPosition,
  PipSize,
  VisualMode,
  VisualPipelineStatus
} from '../../visual'

export const visualModeLabels: Record<VisualMode, string> = {
  screen: '屏幕',
  camera: '摄像头',
  pip: '画中画'
}

export const pipPositionLabels: Record<PipPosition, string> = {
  'top-left': '左上',
  'top-right': '右上',
  'bottom-left': '左下',
  'bottom-right': '右下'
}

export const pipSizeLabels: Record<PipSize, string> = {
  small: '小',
  medium: '中',
  large: '大'
}

export const visualPipelineLabels: Record<VisualPipelineStatus, string> = {
  'waiting-backend': '等待后端接入',
  ready: '已就绪',
  'compression-failed': '压缩失败'
}

export function formatBatchTime(timestamp: number | null): string {
  return timestamp === null
    ? '--:--:--'
    : new Date(timestamp).toLocaleTimeString('zh-CN', { hour12: false })
}
