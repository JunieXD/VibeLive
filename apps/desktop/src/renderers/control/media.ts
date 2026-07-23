export type MediaKind = 'display' | 'microphone' | 'camera'

type StoppableMediaStream = Pick<MediaStream, 'getTracks'>

export function stopMediaStream(stream: StoppableMediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop())
}

export function calculateMicrophoneLevel(samples: Uint8Array): number {
  if (samples.length === 0) return 0

  let sumOfSquares = 0
  for (const sample of samples) {
    const centered = (sample - 128) / 128
    sumOfSquares += centered * centered
  }

  const rootMeanSquare = Math.sqrt(sumOfSquares / samples.length)
  return Math.min(100, Math.round(rootMeanSquare * 320))
}

export function describeMediaError(error: unknown, kind: MediaKind): string {
  const name =
    typeof error === 'object' && error !== null && 'name' in error
      ? String((error as { name: unknown }).name)
      : ''

  if (kind === 'display') {
    if (name === 'NotAllowedError') return '录屏权限被拒绝，请在系统设置中允许屏幕录制。'
    if (name === 'NotFoundError') return '没有找到可采集的屏幕或窗口。'
    if (name === 'NotReadableError') return '画面来源暂时无法读取，请关闭占用它的应用后重试。'
    return '未能启动画面采集，请重新选择来源并检查录屏权限。'
  }

  if (kind === 'camera') {
    if (name === 'NotAllowedError') return '摄像头权限被拒绝，请在系统设置中允许访问。'
    if (name === 'NotFoundError') return '没有检测到可用摄像头。'
    if (name === 'NotReadableError') return '摄像头暂时无法打开，可能正被其他应用独占。'
    return '未能启动摄像头，请检查设备和系统权限。'
  }

  if (name === 'NotAllowedError') return '麦克风权限被拒绝，请在系统设置中允许访问。'
  if (name === 'NotFoundError') return '没有检测到可用麦克风。'
  if (name === 'NotReadableError') return '麦克风暂时无法打开，可能正被其他应用独占。'
  return '未能启动麦克风，请检查设备和系统权限。'
}
