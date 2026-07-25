import { describe, expect, it } from 'vitest'
import {
  BufferedVoiceActivityDetector,
  createWebRtcVoiceActivityDetector,
  WEB_RTC_VAD_FRAME_MS
} from './web-rtc-vad'

describe('WebRTC VAD framing', () => {
  it('waits for a complete 20 ms frame before classifying audio', () => {
    const frames: Float32Array[] = []
    const detector = new BufferedVoiceActivityDetector((frame) => {
      frames.push(frame)
      return true
    })

    expect(detector.detect(new Float32Array(319), 16_000)).toBeNull()
    expect(detector.detect(new Float32Array(1), 16_000)).toBe(true)
    expect(WEB_RTC_VAD_FRAME_MS).toBe(20)
    expect(frames).toHaveLength(1)
    expect(frames[0]).toHaveLength(320)
  })

  it('treats a chunk as speech when any contained VAD frame is speech', () => {
    let frameCount = 0
    const detector = new BufferedVoiceActivityDetector(() => {
      frameCount += 1
      return frameCount === 2
    })

    expect(detector.detect(new Float32Array(640), 16_000)).toBe(true)
    expect(frameCount).toBe(2)
  })

  it('resamples input before it reaches the 16 kHz VAD frames', () => {
    const frameLengths: number[] = []
    const detector = new BufferedVoiceActivityDetector((frame) => {
      frameLengths.push(frame.length)
      return false
    })

    expect(detector.detect(new Float32Array(960), 48_000)).toBe(false)
    expect(frameLengths).toEqual([320])
  })

  it('drops an incomplete frame when the microphone speech gate resets', () => {
    let frames = 0
    const detector = new BufferedVoiceActivityDetector(() => {
      frames += 1
      return false
    })

    expect(detector.detect(new Float32Array(160), 16_000)).toBeNull()
    detector.reset()
    expect(detector.detect(new Float32Array(160), 16_000)).toBeNull()
    expect(frames).toBe(0)
  })

  it('loads the bundled WebRTC VAD implementation', async () => {
    const detector = await createWebRtcVoiceActivityDetector()

    expect(detector.detect(new Float32Array(320), 16_000)).toBe(false)
    detector.dispose()
    expect(detector.detect(new Float32Array(320), 16_000)).toBeNull()
  })
})
