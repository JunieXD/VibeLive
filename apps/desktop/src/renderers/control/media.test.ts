import { describe, expect, it } from 'vitest'
import { calculateMicrophoneLevel, smoothMicrophoneLevel } from './media'

describe('microphone meter', () => {
  it('maps conversational audio from dBFS to a readable display range', () => {
    const quietSpeech = new Float32Array(512).fill(0.01)
    const conversationalSpeech = new Float32Array(512).fill(0.0316227766)

    expect(calculateMicrophoneLevel(quietSpeech)).toBeGreaterThanOrEqual(40)
    expect(calculateMicrophoneLevel(quietSpeech)).toBeLessThanOrEqual(45)
    expect(calculateMicrophoneLevel(conversationalSpeech)).toBeGreaterThanOrEqual(60)
    expect(calculateMicrophoneLevel(conversationalSpeech)).toBeLessThanOrEqual(65)
  })

  it('keeps silence at zero and caps loud input', () => {
    expect(calculateMicrophoneLevel(new Float32Array(512))).toBe(0)
    expect(calculateMicrophoneLevel(new Float32Array(512).fill(0.5))).toBe(100)
  })

  it('rises quickly and decays gradually', () => {
    expect(smoothMicrophoneLevel(0, 100, 50)).toBeCloseTo(63.2, 1)
    expect(smoothMicrophoneLevel(100, 0, 250)).toBeCloseTo(36.8, 1)
  })

  it('uses elapsed time instead of a fixed step per rendered frame', () => {
    expect(smoothMicrophoneLevel(0, 100, 0)).toBe(0)
    expect(smoothMicrophoneLevel(0, 100, 100)).toBeGreaterThan(80)
  })
})
