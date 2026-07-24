import type { AudioSource } from '../../shared/contracts'
import { AUDIO_SYSTEM_BUFFER_SECONDS } from './audio'
import type { VisualMode } from './visual'

export type BufferedAudioChunk = {
  samples: Float32Array
  sampleRate: number
  startedAtMs: number
  endedAtMs: number
}

export type SystemAudioSnapshot = {
  chunks: Float32Array[]
  sampleRate: number
  capturedAtMs: number
  endedAtMs: number
}

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
  candidateChunks: Float32Array[]
  candidateStartedAt: number | null
  noiseFloor: number
  bufferedChunks: BufferedAudioChunk[]
  lastSystemAudioSubmittedAtMs: number | null
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
    candidateChunks: [],
    candidateStartedAt: null,
    noiseFloor: 0.003,
    bufferedChunks: [],
    lastSystemAudioSubmittedAtMs: null,
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
  channel.candidateChunks = []
  channel.candidateStartedAt = null
}

export function resetSpeechGate(channel: AudioChannelState): void {
  resetAudioSegment(channel)
  channel.noiseFloor = 0.003
}

export function clearSystemAudioBuffer(channel: AudioChannelState): void {
  channel.bufferedChunks = []
  channel.lastSystemAudioSubmittedAtMs = null
}

export function appendSystemAudioBuffer(
  channel: AudioChannelState,
  samples: Float32Array,
  sampleRate: number,
  endedAtMs: number,
  maximumDurationMs = AUDIO_SYSTEM_BUFFER_SECONDS * 1_000
): void {
  if (samples.length === 0 || sampleRate <= 0) return
  const durationMs = Math.max(1, Math.round((samples.length * 1_000) / sampleRate))
  const startedAtMs = Math.max(0, endedAtMs - durationMs)
  channel.bufferedChunks.push({ samples, sampleRate, startedAtMs, endedAtMs })
  const cutoff = Math.max(0, endedAtMs - maximumDurationMs)
  channel.bufferedChunks = channel.bufferedChunks.flatMap((chunk) => trimChunk(chunk, cutoff))
}

export function pendingSystemAudioSnapshot(
  channel: AudioChannelState,
  endedAtMs: number,
  maximumDurationMs = AUDIO_SYSTEM_BUFFER_SECONDS * 1_000
): SystemAudioSnapshot | null {
  const firstAvailableAtMs = Math.max(0, endedAtMs - maximumDurationMs)
  const fromMs = Math.max(
    firstAvailableAtMs,
    channel.lastSystemAudioSubmittedAtMs ?? firstAvailableAtMs
  )
  const selected = channel.bufferedChunks.flatMap((chunk) => cropChunk(chunk, fromMs, endedAtMs))
  if (selected.length === 0) return null
  const sampleRate = selected[0].sampleRate
  if (selected.some((chunk) => chunk.sampleRate !== sampleRate)) return null
  return {
    chunks: selected.map((chunk) => chunk.samples),
    sampleRate,
    capturedAtMs: selected[0].startedAtMs,
    endedAtMs: selected.at(-1)?.endedAtMs ?? endedAtMs
  }
}

export function markSystemAudioSubmitted(
  channel: AudioChannelState,
  endedAtMs: number
): void {
  channel.lastSystemAudioSubmittedAtMs = Math.max(
    channel.lastSystemAudioSubmittedAtMs ?? 0,
    endedAtMs
  )
}

function trimChunk(chunk: BufferedAudioChunk, cutoffMs: number): BufferedAudioChunk[] {
  return cropChunk(chunk, cutoffMs, chunk.endedAtMs)
}

function cropChunk(
  chunk: BufferedAudioChunk,
  fromMs: number,
  toMs: number
): BufferedAudioChunk[] {
  const startedAtMs = Math.max(chunk.startedAtMs, fromMs)
  const endedAtMs = Math.min(chunk.endedAtMs, toMs)
  if (endedAtMs <= startedAtMs) return []
  const startOffset = Math.max(
    0,
    Math.floor(((startedAtMs - chunk.startedAtMs) * chunk.sampleRate) / 1_000)
  )
  const endOffset = Math.min(
    chunk.samples.length,
    Math.ceil(((endedAtMs - chunk.startedAtMs) * chunk.sampleRate) / 1_000)
  )
  if (endOffset <= startOffset) return []
  return [{
    samples: chunk.samples.slice(startOffset, endOffset),
    sampleRate: chunk.sampleRate,
    startedAtMs,
    endedAtMs
  }]
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
