import { describe, expect, it, vi } from 'vitest'
import {
  calculateMicrophoneLevel,
  describeMediaError,
  stopMediaStream
} from './media'

describe('desktop media helpers', () => {
  it('stops every track in a media stream', () => {
    const firstStop = vi.fn()
    const secondStop = vi.fn()

    stopMediaStream({
      getTracks: () =>
        [{ stop: firstStop }, { stop: secondStop }] as unknown as MediaStreamTrack[]
    })

    expect(firstStop).toHaveBeenCalledOnce()
    expect(secondStop).toHaveBeenCalledOnce()
  })

  it('maps time-domain samples to a bounded microphone level', () => {
    expect(calculateMicrophoneLevel(new Uint8Array([128, 128, 128, 128]))).toBe(0)
    expect(calculateMicrophoneLevel(new Uint8Array([128, 160, 128, 96]))).toBeGreaterThan(0)
    expect(calculateMicrophoneLevel(new Uint8Array([0, 255, 0, 255]))).toBe(100)
  })

  it('returns actionable permission errors for each capture type', () => {
    expect(describeMediaError({ name: 'NotAllowedError' }, 'display')).toContain('录屏权限')
    expect(describeMediaError({ name: 'NotAllowedError' }, 'camera')).toContain('摄像头权限')
    expect(describeMediaError({ name: 'NotAllowedError' }, 'microphone')).toContain('麦克风权限')
  })
})
