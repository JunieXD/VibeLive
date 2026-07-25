import { describe, expect, it } from 'vitest'
import {
  AUDIO_MICROPHONE_SEGMENT_SECONDS,
  AUDIO_SYSTEM_SEGMENT_SECONDS,
  concatenateFloat32,
  encodePcm16Mono,
  float32ToPcm16Le,
  resampleMono,
  shouldHardFlushMicrophoneSegment,
  shouldFlushSystemAudioSegment,
  speechThresholds,
  updateNoiseFloor
} from './audio'

describe('desktop realtime audio encoding', () => {
  it('hard-splits continuous microphone speech every 30 seconds beyond 90 seconds', () => {
    let segmentStartedAtMs: number | null = 0
    let splits = 0
    for (let nowMs = 0; nowMs <= 95_000; nowMs += 1_000) {
      if (!shouldHardFlushMicrophoneSegment(segmentStartedAtMs, nowMs)) continue
      splits += 1
      segmentStartedAtMs = nowMs
    }

    expect(AUDIO_MICROPHONE_SEGMENT_SECONDS).toBe(30)
    expect(splits).toBe(3)
  })

  it('flushes continuous system speech every 8 seconds or after sentence silence', () => {
    expect(AUDIO_SYSTEM_SEGMENT_SECONDS).toBe(8)
    expect(shouldFlushSystemAudioSegment(1_000, 8_900, 9_000)).toBe(true)
    expect(shouldFlushSystemAudioSegment(10_000, 10_100, 10_799)).toBe(false)
    expect(shouldFlushSystemAudioSegment(10_000, 10_100, 10_900)).toBe(true)
    expect(shouldFlushSystemAudioSegment(null, null, 20_000)).toBe(false)
  })

  it('concatenates captured chunks in order', () => {
    expect([...concatenateFloat32([new Float32Array([1, 2]), new Float32Array([3])])]).toEqual([
      1, 2, 3
    ])
  })

  it('resamples mono audio to 16 kHz', () => {
    const input = new Float32Array(48_000).map((_, index) => index / 48_000)
    const output = resampleMono(input, 48_000)
    expect(output).toHaveLength(16_000)
    expect(output[8_000]).toBeCloseTo(0.5, 4)
  })

  it('encodes clipped signed PCM in little-endian order', () => {
    expect([...float32ToPcm16Le(new Float32Array([-2, -1, 0, 1, 2]))]).toEqual([
      0x00, 0x80, 0x00, 0x80, 0x00, 0x00, 0xff, 0x7f, 0xff, 0x7f
    ])
  })

  it('combines resampling and PCM encoding', () => {
    const encoded = encodePcm16Mono([new Float32Array(44_100)], 44_100)
    expect(encoded).toHaveLength(32_000)
  })

  it('raises the speech gate above a sustained background level', () => {
    let noiseFloor = 0.003
    for (let index = 0; index < 20; index += 1) {
      noiseFloor = updateNoiseFloor(noiseFloor, 0.08)
    }

    const thresholds = speechThresholds(noiseFloor)
    expect(noiseFloor).toBeGreaterThan(0.003)
    expect(thresholds.start).toBeGreaterThan(0.015)
    expect(thresholds.continue).toBeLessThan(thresholds.start)
    expect(thresholds.continue).toBeGreaterThanOrEqual(0.01)
  })
})
