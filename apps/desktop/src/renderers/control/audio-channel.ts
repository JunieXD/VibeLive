import type { AudioSource } from '../../shared/contracts'
import type { VisualMode } from './visual'

export type AudioChannelState = {
  source: AudioSource
  stream: MediaStream | null
  context: AudioContext | null
  processor: ScriptProcessorNode | null
  chunks: Float32Array[]
  sampleCount: number
  sampleRate: number
  lastSpeechAt: number | null
  segmentStartedAt: number | null
  sendQueue: Promise<void>
  sequence: number
  ingestErrorReported: boolean
  error: string | null
  level: number
  meterFrame: number | null
}

export function createAudioChannelState(source: AudioSource): AudioChannelState {
  return {
    source,
    stream: null,
    context: null,
    processor: null,
    chunks: [],
    sampleCount: 0,
    sampleRate: 0,
    lastSpeechAt: null,
    segmentStartedAt: null,
    sendQueue: Promise.resolve(),
    sequence: 0,
    ingestErrorReported: false,
    error: null,
    level: 0,
    meterFrame: null
  }
}

export function resetAudioSegment(channel: AudioChannelState): void {
  channel.chunks = []
  channel.sampleCount = 0
  channel.segmentStartedAt = null
  channel.lastSpeechAt = null
}

export function shouldReleaseLoopbackVideo(
  visualMode: VisualMode,
  captureIsAudioStream: boolean
): boolean {
  return visualMode === 'camera' && captureIsAudioStream
}

export function releaseFailedLoopbackCapture(
  visualMode: VisualMode,
  candidateStream: MediaStream,
  currentCaptureStream: MediaStream | null,
  stopCapture: () => void
): boolean {
  candidateStream.getAudioTracks().forEach((track) => track.stop())
  if (!shouldReleaseLoopbackVideo(visualMode, currentCaptureStream === candidateStream)) {
    return false
  }
  stopCapture()
  return true
}

export function updateAudioTransportError(
  channel: AudioChannelState,
  error: unknown,
  onChange: (error: string | null) => void
): void {
  channel.error =
    error === null
      ? null
      : error instanceof Error && error.message
        ? error.message
        : '实时连接异常。'
  onChange(channel.error)
}
