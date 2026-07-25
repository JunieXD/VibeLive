export type VisualMode = 'screen' | 'camera' | 'pip'
export type PipPosition = 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right'
export type PipSize = 'small' | 'medium' | 'large'
export type CompressionPreset = 'economy' | 'balanced' | 'clear'
export type VisualPipelineStatus =
  | 'waiting-backend'
  | 'local-preview'
  | 'ready'
  | 'compression-failed'

export type VisualSettings = {
  mode: VisualMode
  cameraDeviceId: string
  mirrorCamera: boolean
  pipPosition: PipPosition
  pipSize: PipSize
  sampleIntervalMs: 500 | 1000 | 2000 | 5000
  compressionPreset: CompressionPreset
}

export type CompressionProfile = {
  label: string
  maxLongEdge: number
  targetBytes: number
}

export type Rectangle = {
  x: number
  y: number
  width: number
  height: number
}

export type EncodedJpeg = {
  blob: Blob
  width: number
  height: number
  quality: number
  overTarget: boolean
}

export type VisualFrame = {
  frameId: string
  capturedAt: number
  width: number
  height: number
  mode: VisualMode
  bytes: number
  overTarget: boolean
  changeScore: number
  visualSignature: string
  blob: Blob | null
}

export type VisualBatch = {
  batchId: string
  createdAt: number
  frames: VisualFrame[]
}

export type VisualBatchSinkResult = 'accepted' | 'waiting-backend'

export interface VisualBatchSink {
  consume(batch: VisualBatch, signal: AbortSignal): Promise<VisualBatchSinkResult>
}

export type VisualFrameSubmitter = (input: {
  inputId: string
  capturedAtMs: number
  mimeType: string
  changeScore: number
  visualSignature: string
  body: Uint8Array
}) => Promise<void>

type StorageReader = Pick<Storage, 'getItem'>
type StorageWriter = Pick<Storage, 'setItem'>
type VideoFrameSource = Pick<HTMLVideoElement, 'videoWidth' | 'videoHeight'> & CanvasImageSource

const SETTINGS_VERSION = 1
const MIN_READABLE_LONG_EDGE = 720
const JPEG_MAX_QUALITY = 0.9
const JPEG_MIN_QUALITY = 0.42
const JPEG_QUALITY_SEARCH_STEPS = 2
const CHANGE_SIGNATURE_WIDTH = 32
const CHANGE_SIGNATURE_HEIGHT = 18
const VISUAL_SIGNATURE_WIDTH = 16
const VISUAL_SIGNATURE_HEIGHT = 18
const VISUAL_SIGNATURE_QUANTIZATION = 15
const BASE64_URL_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
const PIP_WIDTH_RATIOS: Record<PipSize, number> = {
  small: 0.2,
  medium: 0.28,
  large: 0.36
}

export const VISUAL_SETTINGS_STORAGE_KEY = 'advx.visual-settings'

export const DEFAULT_VISUAL_SETTINGS: VisualSettings = {
  mode: 'screen',
  cameraDeviceId: '',
  mirrorCamera: false,
  pipPosition: 'bottom-right',
  pipSize: 'medium',
  sampleIntervalMs: 1000,
  compressionPreset: 'balanced'
}

export const COMPRESSION_PROFILES: Record<CompressionPreset, CompressionProfile> = {
  economy: {
    label: '省流',
    maxLongEdge: 960,
    targetBytes: 120 * 1024
  },
  balanced: {
    label: '平衡',
    maxLongEdge: 1440,
    targetBytes: 250 * 1024
  },
  clear: {
    label: '清晰',
    maxLongEdge: 1920,
    targetBytes: 500 * 1024
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isOneOf<T extends string>(value: unknown, values: readonly T[]): value is T {
  return typeof value === 'string' && values.includes(value as T)
}

export function parseVisualSettings(value: unknown): VisualSettings {
  if (!isRecord(value) || value.version !== SETTINGS_VERSION || !isRecord(value.settings)) {
    return { ...DEFAULT_VISUAL_SETTINGS }
  }

  const settings = value.settings
  return {
    mode: isOneOf(settings.mode, ['screen', 'camera', 'pip'])
      ? settings.mode
      : DEFAULT_VISUAL_SETTINGS.mode,
    cameraDeviceId:
      typeof settings.cameraDeviceId === 'string' ? settings.cameraDeviceId : '',
    mirrorCamera:
      typeof settings.mirrorCamera === 'boolean'
        ? settings.mirrorCamera
        : DEFAULT_VISUAL_SETTINGS.mirrorCamera,
    pipPosition: isOneOf(settings.pipPosition, [
      'top-left',
      'top-right',
      'bottom-left',
      'bottom-right'
    ])
      ? settings.pipPosition
      : DEFAULT_VISUAL_SETTINGS.pipPosition,
    pipSize: isOneOf(settings.pipSize, ['small', 'medium', 'large'])
      ? settings.pipSize
      : DEFAULT_VISUAL_SETTINGS.pipSize,
    sampleIntervalMs:
      typeof settings.sampleIntervalMs === 'number' &&
      [500, 1000, 2000, 5000].includes(settings.sampleIntervalMs)
        ? (settings.sampleIntervalMs as VisualSettings['sampleIntervalMs'])
        : DEFAULT_VISUAL_SETTINGS.sampleIntervalMs,
    compressionPreset: isOneOf(settings.compressionPreset, [
      'economy',
      'balanced',
      'clear'
    ])
      ? settings.compressionPreset
      : DEFAULT_VISUAL_SETTINGS.compressionPreset
  }
}

export function loadVisualSettings(storage: StorageReader): VisualSettings {
  try {
    const raw = storage.getItem(VISUAL_SETTINGS_STORAGE_KEY)
    return raw ? parseVisualSettings(JSON.parse(raw)) : { ...DEFAULT_VISUAL_SETTINGS }
  } catch {
    return { ...DEFAULT_VISUAL_SETTINGS }
  }
}

export function saveVisualSettings(storage: StorageWriter, settings: VisualSettings): boolean {
  try {
    storage.setItem(
      VISUAL_SETTINGS_STORAGE_KEY,
      JSON.stringify({ version: SETTINGS_VERSION, settings })
    )
    return true
  } catch {
    return false
  }
}

export function resolveVisualMode(
  requestedMode: VisualMode,
  hasScreen: boolean,
  hasCamera: boolean
): VisualMode | null {
  if (requestedMode === 'pip') {
    if (hasScreen && hasCamera) return 'pip'
    if (hasScreen) return 'screen'
    if (hasCamera) return 'camera'
    return null
  }
  if (requestedMode === 'screen') return hasScreen ? 'screen' : hasCamera ? 'camera' : null
  return hasCamera ? 'camera' : hasScreen ? 'screen' : null
}

export function requiredVisualSources(mode: VisualMode): {
  screen: boolean
  camera: boolean
} {
  return {
    screen: mode === 'screen' || mode === 'pip',
    camera: mode === 'camera' || mode === 'pip'
  }
}

export function getPipRectangle(
  frameWidth: number,
  frameHeight: number,
  position: PipPosition,
  size: PipSize
): Rectangle {
  const margin = Math.max(8, Math.round(Math.min(frameWidth, frameHeight) * 0.025))
  const width = Math.round(frameWidth * PIP_WIDTH_RATIOS[size])
  const height = Math.round(width * (9 / 16))
  const left = position.endsWith('right') ? frameWidth - margin - width : margin
  const top = position.startsWith('bottom') ? frameHeight - margin - height : margin
  return { x: left, y: top, width, height }
}

export function getContainRectangle(
  sourceWidth: number,
  sourceHeight: number,
  target: Rectangle
): Rectangle {
  if (sourceWidth <= 0 || sourceHeight <= 0 || target.width <= 0 || target.height <= 0) {
    return { ...target, width: 0, height: 0 }
  }
  const scale = Math.min(target.width / sourceWidth, target.height / sourceHeight)
  const width = sourceWidth * scale
  const height = sourceHeight * scale
  return {
    x: target.x + (target.width - width) / 2,
    y: target.y + (target.height - height) / 2,
    width,
    height
  }
}

export function getCoverSourceRectangle(
  sourceWidth: number,
  sourceHeight: number,
  targetWidth: number,
  targetHeight: number
): Rectangle {
  const sourceAspect = sourceWidth / sourceHeight
  const targetAspect = targetWidth / targetHeight
  if (sourceAspect > targetAspect) {
    const width = sourceHeight * targetAspect
    return { x: (sourceWidth - width) / 2, y: 0, width, height: sourceHeight }
  }
  const height = sourceWidth / targetAspect
  return { x: 0, y: (sourceHeight - height) / 2, width: sourceWidth, height }
}

export function cameraPreviewTransform(mirror: boolean): string {
  return mirror ? 'scaleX(-1)' : 'none'
}

export function grayscaleSignature(rgba: Uint8ClampedArray): Uint8Array {
  if (rgba.length % 4 !== 0) throw new Error('RGBA pixels must contain complete pixels.')
  const signature = new Uint8Array(rgba.length / 4)
  for (let source = 0, target = 0; source < rgba.length; source += 4, target += 1) {
    signature[target] =
      (77 * rgba[source] + 150 * rgba[source + 1] + 29 * rgba[source + 2]) >> 8
  }
  return signature
}

export function grayscaleMeanAbsoluteDifference(
  previous: Uint8Array | null,
  current: Uint8Array
): number {
  if (previous === null) return 0
  if (previous.length !== current.length || current.length === 0) {
    throw new Error('Visual signatures must have the same non-zero length.')
  }
  let difference = 0
  for (let index = 0; index < current.length; index += 1) {
    difference += Math.abs(current[index] - previous[index])
  }
  return difference / (current.length * 255)
}

export function sampleCanvasChangeSignature(
  sourceCanvas: HTMLCanvasElement,
  sampleCanvas: HTMLCanvasElement
): Uint8Array {
  return sampleCanvasGrayscaleSignature(
    sourceCanvas,
    sampleCanvas,
    CHANGE_SIGNATURE_WIDTH,
    CHANGE_SIGNATURE_HEIGHT
  )
}

export function sampleCanvasVisualSignature(
  sourceCanvas: HTMLCanvasElement,
  sampleCanvas: HTMLCanvasElement
): Uint8Array {
  return sampleCanvasGrayscaleSignature(
    sourceCanvas,
    sampleCanvas,
    VISUAL_SIGNATURE_WIDTH,
    VISUAL_SIGNATURE_HEIGHT
  )
}

export function encodeVisualSignature(signature: Uint8Array): string {
  const expectedLength = VISUAL_SIGNATURE_WIDTH * VISUAL_SIGNATURE_HEIGHT
  if (signature.length !== expectedLength) {
    throw new Error(`Visual signature must contain exactly ${expectedLength} grayscale samples.`)
  }
  const packed = new Uint8Array(expectedLength / 2)
  for (let index = 0; index < signature.length; index += 2) {
    const high = Math.min(
      VISUAL_SIGNATURE_QUANTIZATION,
      Math.round(signature[index] / 17)
    )
    const low = Math.min(
      VISUAL_SIGNATURE_QUANTIZATION,
      Math.round(signature[index + 1] / 17)
    )
    packed[index / 2] = (high << 4) | low
  }
  return encodeBase64Url(packed)
}

function sampleCanvasGrayscaleSignature(
  sourceCanvas: HTMLCanvasElement,
  sampleCanvas: HTMLCanvasElement,
  width: number,
  height: number
): Uint8Array {
  sampleCanvas.width = width
  sampleCanvas.height = height
  const context = sampleCanvas.getContext('2d', {
    alpha: false,
    willReadFrequently: true
  })
  if (!context) throw new Error('Canvas 2D context is unavailable.')
  context.drawImage(
    sourceCanvas,
    0,
    0,
    width,
    height
  )
  return grayscaleSignature(
    context.getImageData(
      0,
      0,
      width,
      height
    ).data
  )
}

function encodeBase64Url(bytes: Uint8Array): string {
  let encoded = ''
  for (let index = 0; index < bytes.length; index += 3) {
    const value = (bytes[index] << 16) | (bytes[index + 1] << 8) | bytes[index + 2]
    encoded += BASE64_URL_ALPHABET[(value >> 18) & 0x3f]
    encoded += BASE64_URL_ALPHABET[(value >> 12) & 0x3f]
    encoded += BASE64_URL_ALPHABET[(value >> 6) & 0x3f]
    encoded += BASE64_URL_ALPHABET[value & 0x3f]
  }
  return encoded
}

function dimensionsForLongEdge(
  sourceWidth: number,
  sourceHeight: number,
  longEdge: number
): { width: number; height: number } {
  const sourceLongEdge = Math.max(sourceWidth, sourceHeight)
  const scale = longEdge / sourceLongEdge
  return {
    width: Math.max(1, Math.round(sourceWidth * scale)),
    height: Math.max(1, Math.round(sourceHeight * scale))
  }
}

export async function encodeJpegWithinTarget(
  sourceWidth: number,
  sourceHeight: number,
  profile: CompressionProfile,
  encode: (width: number, height: number, quality: number) => Promise<Blob>,
  maximumQuality = JPEG_MAX_QUALITY
): Promise<EncodedJpeg> {
  const highQuality = Math.min(1, Math.max(0.01, maximumQuality))
  const lowQuality = Math.min(JPEG_MIN_QUALITY, highQuality)
  let longEdge = Math.min(profile.maxLongEdge, Math.max(sourceWidth, sourceHeight))
  let lastResult: EncodedJpeg | null = null

  while (true) {
    const dimensions = dimensionsForLongEdge(sourceWidth, sourceHeight, longEdge)
    const highQualityBlob = await encode(
      dimensions.width,
      dimensions.height,
      highQuality
    )
    if (highQualityBlob.size <= profile.targetBytes) {
      return {
        blob: highQualityBlob,
        ...dimensions,
        quality: highQuality,
        overTarget: false
      }
    }

    const lowQualityBlob = await encode(
      dimensions.width,
      dimensions.height,
      lowQuality
    )
    lastResult = {
      blob: lowQualityBlob,
      ...dimensions,
      quality: lowQuality,
      overTarget: lowQualityBlob.size > profile.targetBytes
    }

    if (!lastResult.overTarget) {
      let accepted = lastResult
      let acceptedQuality = lowQuality
      let rejectedQuality = highQuality
      for (let step = 0; step < JPEG_QUALITY_SEARCH_STEPS; step += 1) {
        const quality = (acceptedQuality + rejectedQuality) / 2
        const blob = await encode(dimensions.width, dimensions.height, quality)
        if (blob.size <= profile.targetBytes) {
          accepted = {
            blob,
            ...dimensions,
            quality,
            overTarget: false
          }
          acceptedQuality = quality
        } else {
          rejectedQuality = quality
        }
      }
      return accepted
    }

    if (longEdge <= MIN_READABLE_LONG_EDGE) break
    const estimatedScale = Math.sqrt(profile.targetBytes / lowQualityBlob.size) * 0.96
    const nextLongEdge = Math.max(
      MIN_READABLE_LONG_EDGE,
      Math.floor(longEdge * Math.min(0.85, Math.max(0.65, estimatedScale)))
    )
    if (nextLongEdge === longEdge) break
    longEdge = nextLongEdge
  }

  if (!lastResult) throw new Error('JPEG encoder returned no result.')
  return lastResult
}

function drawVideo(
  context: CanvasRenderingContext2D,
  source: VideoFrameSource,
  target: Rectangle,
  fit: 'contain' | 'cover',
  mirror: boolean
): void {
  if (source.videoWidth <= 0 || source.videoHeight <= 0) return

  let sourceRectangle: Rectangle = {
    x: 0,
    y: 0,
    width: source.videoWidth,
    height: source.videoHeight
  }
  let destination = target
  if (fit === 'contain') {
    destination = getContainRectangle(source.videoWidth, source.videoHeight, target)
  } else {
    sourceRectangle = getCoverSourceRectangle(
      source.videoWidth,
      source.videoHeight,
      target.width,
      target.height
    )
  }

  context.save()
  if (mirror) {
    context.translate(destination.x + destination.width, destination.y)
    context.scale(-1, 1)
    context.drawImage(
      source,
      sourceRectangle.x,
      sourceRectangle.y,
      sourceRectangle.width,
      sourceRectangle.height,
      0,
      0,
      destination.width,
      destination.height
    )
  } else {
    context.drawImage(
      source,
      sourceRectangle.x,
      sourceRectangle.y,
      sourceRectangle.width,
      sourceRectangle.height,
      destination.x,
      destination.y,
      destination.width,
      destination.height
    )
  }
  context.restore()
}

export function drawCompositeFrame(
  canvas: HTMLCanvasElement,
  options: {
    mode: VisualMode
    screen: VideoFrameSource | null
    camera: VideoFrameSource | null
    mirrorCamera: boolean
    pipPosition: PipPosition
    pipSize: PipSize
    longEdge: number
  }
): boolean {
  const width = options.longEdge
  const height = Math.round(width * (9 / 16))
  const context = canvas.getContext('2d', { alpha: false })
  if (!context) return false

  canvas.width = width
  canvas.height = height
  context.fillStyle = '#0a0b0e'
  context.fillRect(0, 0, width, height)
  const fullFrame = { x: 0, y: 0, width, height }

  if (options.mode === 'screen' && options.screen) {
    drawVideo(context, options.screen, fullFrame, 'contain', false)
    return true
  }
  if (options.mode === 'camera' && options.camera) {
    drawVideo(context, options.camera, fullFrame, 'cover', options.mirrorCamera)
    return true
  }
  if (options.mode === 'pip' && options.screen && options.camera) {
    drawVideo(context, options.screen, fullFrame, 'contain', false)
    const pip = getPipRectangle(width, height, options.pipPosition, options.pipSize)
    context.fillStyle = '#0a0b0e'
    context.fillRect(pip.x, pip.y, pip.width, pip.height)
    drawVideo(context, options.camera, pip, 'contain', options.mirrorCamera)
    context.strokeStyle = 'rgba(232, 234, 240, 0.72)'
    context.lineWidth = Math.max(2, Math.round(width / 720))
    context.strokeRect(pip.x, pip.y, pip.width, pip.height)
    return true
  }
  return false
}

export async function compressCompositeCanvas(
  sourceCanvas: HTMLCanvasElement,
  profile: CompressionProfile,
  maximumQuality = JPEG_MAX_QUALITY
): Promise<EncodedJpeg> {
  const workCanvas = document.createElement('canvas')
  return encodeJpegWithinTarget(
    sourceCanvas.width,
    sourceCanvas.height,
    profile,
    async (width, height, quality) => {
      workCanvas.width = width
      workCanvas.height = height
      const context = workCanvas.getContext('2d', { alpha: false })
      if (!context) throw new Error('Canvas 2D context is unavailable.')
      context.drawImage(sourceCanvas, 0, 0, width, height)
      return new Promise<Blob>((resolve, reject) => {
        workCanvas.toBlob(
          (blob) => (blob ? resolve(blob) : reject(new Error('JPEG compression failed.'))),
          'image/jpeg',
          quality
        )
      })
    },
    maximumQuality
  )
}

export function selectVisualBatchFrames(frames: readonly VisualFrame[]): VisualFrame[] {
  if (frames.length === 0) return []
  const sorted = [...frames].sort((first, second) => first.capturedAt - second.capturedAt)
  return sorted.length <= 2 ? sorted : [sorted[0], sorted[sorted.length - 1]]
}

export function releaseVisualFrames(frames: readonly VisualFrame[]): void {
  for (const frame of frames) frame.blob = null
}

export async function deliverAndReleaseVisualBatch(
  sink: VisualBatchSink,
  batch: VisualBatch,
  signal: AbortSignal
): Promise<VisualBatchSinkResult> {
  try {
    return await sink.consume(batch, signal)
  } finally {
    releaseVisualFrames(batch.frames)
  }
}

export async function deliverVisualFrames(
  frames: readonly VisualFrame[],
  signal: AbortSignal,
  submit: VisualFrameSubmitter
): Promise<VisualBatchSinkResult> {
  let accepted = false
  let failed = false
  let firstFailure: unknown
  for (const frame of frames) {
    if (signal.aborted) {
      throw new DOMException('Visual delivery aborted.', 'AbortError')
    }
    if (!frame.blob) continue
    const body = new Uint8Array(await frame.blob.arrayBuffer())
    if (signal.aborted) {
      throw new DOMException('Visual delivery aborted.', 'AbortError')
    }
    try {
      await submit({
        inputId: frame.frameId,
        capturedAtMs: frame.capturedAt,
        mimeType: frame.blob.type || 'image/jpeg',
        changeScore: frame.changeScore,
        visualSignature: frame.visualSignature,
        body
      })
      accepted = true
    } catch (error) {
      if (isRealtimeDisconnectedError(error)) throw error
      if (!failed) firstFailure = error
      failed = true
    }
  }
  if (failed) throw firstFailure
  return accepted ? 'accepted' : 'waiting-backend'
}

export function isRealtimeDisconnectedError(error: unknown): boolean {
  const code =
    typeof error === 'object' && error !== null && 'code' in error
      ? String(error.code)
      : ''
  if (code === 'backend_disconnected' || code === 'connection_closed') return true
  const message = error instanceof Error ? error.message.toLowerCase() : ''
  return (
    message.includes('backend_disconnected') ||
    message.includes('connection_closed') ||
    message.includes('实时连接尚未就绪') ||
    message.includes('连接已断开')
  )
}

export function createWaitingVisualBatchSink(): VisualBatchSink {
  return {
    consume: async (_batch, signal) => {
      if (signal.aborted) {
        throw new DOMException('Visual batch delivery was aborted.', 'AbortError')
      }
      return 'waiting-backend'
    }
  }
}

export function formatFrameKilobytes(bytes: number): string {
  return `${(bytes / 1024).toFixed(bytes >= 100 * 1024 ? 0 : 1)} KB`
}
