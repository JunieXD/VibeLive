export const PCM_SAMPLE_RATE = 16_000
export const AUDIO_SENTENCE_SILENCE_SECONDS = 0.8
export const AUDIO_MIN_SPEECH_THRESHOLD = 0.015
export const AUDIO_SPEECH_CONFIRMATION_MS = 200
export const AUDIO_SYSTEM_BUFFER_SECONDS = 60
export const AUDIO_SYSTEM_SNAPSHOT_SECONDS = 30
export const AUDIO_SYSTEM_SEGMENT_SECONDS = 8
export const AUDIO_MICROPHONE_SEGMENT_SECONDS = 30

const AUDIO_NOISE_FLOOR_ALPHA = 0.08
const AUDIO_NOISE_FLOOR_MAX_STEP = 0.01

export type SpeechThresholds = {
  start: number
  continue: number
}

export function shouldHardFlushMicrophoneSegment(
  segmentStartedAtMs: number | null,
  nowMs: number
): boolean {
  return (
    segmentStartedAtMs !== null &&
    nowMs - segmentStartedAtMs >= AUDIO_MICROPHONE_SEGMENT_SECONDS * 1_000
  )
}

export function shouldFlushSystemAudioSegment(
  segmentStartedAtMs: number | null,
  lastSpeechAtMs: number | null,
  nowMs: number
): boolean {
  if (segmentStartedAtMs === null || lastSpeechAtMs === null) return false
  return (
    nowMs - segmentStartedAtMs >= AUDIO_SYSTEM_SEGMENT_SECONDS * 1_000 ||
    nowMs - lastSpeechAtMs >= AUDIO_SENTENCE_SILENCE_SECONDS * 1_000
  )
}

export function updateNoiseFloor(current: number, level: number): number {
  const boundedLevel = Math.max(0, Math.min(1, level))
  const delta = boundedLevel - current
  const limitedDelta = Math.max(-AUDIO_NOISE_FLOOR_MAX_STEP, Math.min(
    AUDIO_NOISE_FLOOR_MAX_STEP,
    delta
  ))
  return Math.max(0, current + limitedDelta * AUDIO_NOISE_FLOOR_ALPHA)
}

export function speechThresholds(noiseFloor: number): SpeechThresholds {
  const start = Math.max(AUDIO_MIN_SPEECH_THRESHOLD, noiseFloor * 2 + 0.006)
  return {
    start,
    continue: Math.max(0.01, start * 0.65)
  }
}

export function concatenateFloat32(chunks: readonly Float32Array[]): Float32Array {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0)
  const output = new Float32Array(length)
  let offset = 0
  for (const chunk of chunks) {
    output.set(chunk, offset)
    offset += chunk.length
  }
  return output
}

export function resampleMono(
  samples: Float32Array,
  inputSampleRate: number,
  outputSampleRate = PCM_SAMPLE_RATE
): Float32Array {
  if (inputSampleRate <= 0 || outputSampleRate <= 0) {
    throw new Error('Audio sample rates must be positive.')
  }
  if (samples.length === 0 || inputSampleRate === outputSampleRate) {
    return samples.slice()
  }

  const outputLength = Math.max(1, Math.round((samples.length * outputSampleRate) / inputSampleRate))
  const output = new Float32Array(outputLength)
  const sourceStep = inputSampleRate / outputSampleRate
  for (let index = 0; index < outputLength; index += 1) {
    const sourcePosition = index * sourceStep
    const leftIndex = Math.min(samples.length - 1, Math.floor(sourcePosition))
    const rightIndex = Math.min(samples.length - 1, leftIndex + 1)
    const fraction = sourcePosition - leftIndex
    output[index] = samples[leftIndex] * (1 - fraction) + samples[rightIndex] * fraction
  }
  return output
}

export function float32ToPcm16Le(samples: Float32Array): Uint8Array {
  const output = new Uint8Array(samples.length * 2)
  const view = new DataView(output.buffer)
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]))
    const encoded = sample < 0 ? Math.round(sample * 0x8000) : Math.round(sample * 0x7fff)
    view.setInt16(index * 2, encoded, true)
  }
  return output
}

export function encodePcm16Mono(
  chunks: readonly Float32Array[],
  inputSampleRate: number
): Uint8Array {
  return float32ToPcm16Le(resampleMono(concatenateFloat32(chunks), inputSampleRate))
}
