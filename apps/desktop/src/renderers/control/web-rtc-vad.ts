import createFvad from '@echogarden/fvad-wasm'
import { PCM_SAMPLE_RATE, resampleMono } from './audio'

export const WEB_RTC_VAD_FRAME_MS = 20

const WEB_RTC_VAD_MODE = 1
const WEB_RTC_VAD_FRAME_SAMPLES = (PCM_SAMPLE_RATE * WEB_RTC_VAD_FRAME_MS) / 1_000

export type VoiceActivityDetector = {
  detect(samples: Float32Array, inputSampleRate: number): boolean | null
  reset(): void
  dispose(): void
}

type FrameClassifier = (frame: Float32Array) => boolean

export class BufferedVoiceActivityDetector {
  private pendingSamples: Float32Array = new Float32Array(0)

  constructor(private readonly classifyFrame: FrameClassifier) {}

  detect(samples: Float32Array, inputSampleRate: number): boolean | null {
    if (samples.length === 0) return null

    this.pendingSamples = appendSamples(
      this.pendingSamples,
      resampleMono(samples, inputSampleRate, PCM_SAMPLE_RATE)
    )

    let evaluated = false
    let speechDetected = false
    while (this.pendingSamples.length >= WEB_RTC_VAD_FRAME_SAMPLES) {
      const frame = this.pendingSamples.slice(0, WEB_RTC_VAD_FRAME_SAMPLES)
      this.pendingSamples = this.pendingSamples.slice(WEB_RTC_VAD_FRAME_SAMPLES)
      evaluated = true
      speechDetected = this.classifyFrame(frame) || speechDetected
    }
    return evaluated ? speechDetected : null
  }

  reset(): void {
    this.pendingSamples = new Float32Array(0)
  }
}

class WebRtcVoiceActivityDetector implements VoiceActivityDetector {
  private disposed = false
  private readonly bufferedDetector: BufferedVoiceActivityDetector

  constructor(
    private readonly module: Awaited<ReturnType<typeof createFvad>>,
    private readonly handle: number,
    private readonly framePointer: number
  ) {
    this.bufferedDetector = new BufferedVoiceActivityDetector((frame) => {
      if (this.disposed) return false
      this.module.HEAP16.set(float32ToPcm16(frame), this.framePointer / Int16Array.BYTES_PER_ELEMENT)
      const result = this.module._fvad_process(this.handle, this.framePointer, frame.length)
      if (result < 0) throw new Error('WebRTC VAD rejected an audio frame')
      return result === 1
    })
  }

  detect(samples: Float32Array, inputSampleRate: number): boolean | null {
    if (this.disposed) return null
    return this.bufferedDetector.detect(samples, inputSampleRate)
  }

  reset(): void {
    this.bufferedDetector.reset()
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.bufferedDetector.reset()
    this.module._free(this.framePointer)
    this.module._fvad_free(this.handle)
  }
}

export async function createWebRtcVoiceActivityDetector(): Promise<VoiceActivityDetector> {
  const module = await createFvad()
  const handle = module._fvad_new()
  if (handle === 0) throw new Error('WebRTC VAD could not allocate a detector')

  let framePointer = 0
  try {
    if (module._fvad_set_mode(handle, WEB_RTC_VAD_MODE) !== 0) {
      throw new Error('WebRTC VAD could not set its operating mode')
    }
    if (module._fvad_set_sample_rate(handle, PCM_SAMPLE_RATE) !== 0) {
      throw new Error('WebRTC VAD could not set the microphone sample rate')
    }
    framePointer = module._malloc(WEB_RTC_VAD_FRAME_SAMPLES * Int16Array.BYTES_PER_ELEMENT)
    if (framePointer === 0) throw new Error('WebRTC VAD could not allocate an audio frame')
    return new WebRtcVoiceActivityDetector(module, handle, framePointer)
  } catch (error) {
    if (framePointer !== 0) module._free(framePointer)
    module._fvad_free(handle)
    throw error
  }
}

function appendSamples(left: Float32Array, right: Float32Array): Float32Array {
  if (left.length === 0) return right
  const result = new Float32Array(left.length + right.length)
  result.set(left)
  result.set(right, left.length)
  return result
}

function float32ToPcm16(samples: Float32Array): Int16Array {
  const output = new Int16Array(samples.length)
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]))
    output[index] = sample < 0 ? Math.round(sample * 0x8000) : Math.round(sample * 0x7fff)
  }
  return output
}
