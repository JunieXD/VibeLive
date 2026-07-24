import { describe, expect, it, vi } from 'vitest'
import {
  bindMediaStreamToVideo,
  calculateMicrophoneLevel,
  describeMediaError,
  getDefaultDesktopSource,
  stopMediaStream
} from './media'

describe('desktop media helpers', () => {
  it('prefers a desktop screen over application windows', () => {
    const windowSource = {
      id: 'window:1:0',
      name: 'Editor',
      thumbnailUrl: 'window',
      appIconUrl: null,
      kind: 'window'
    } as const
    const screenSource = {
      id: 'screen:0:0',
      name: 'Screen 1',
      thumbnailUrl: 'screen',
      appIconUrl: null,
      kind: 'screen'
    } as const

    expect(getDefaultDesktopSource([windowSource, screenSource])).toBe(screenSource)
    expect(getDefaultDesktopSource([windowSource])).toBe(windowSource)
    expect(getDefaultDesktopSource([])).toBeNull()
  })

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

  it('binds an existing stream when a preview video is remounted', () => {
    const stream = {} as MediaStream
    const firstPlay = vi.fn(() => Promise.resolve())
    const replacementPlay = vi.fn(() => Promise.resolve())
    const replacementPause = vi.fn()
    const firstVideo = {
      srcObject: null,
      play: firstPlay,
      pause: vi.fn()
    } as unknown as HTMLVideoElement
    const replacementVideo = {
      srcObject: null,
      play: replacementPlay,
      pause: replacementPause
    } as unknown as HTMLVideoElement

    bindMediaStreamToVideo(firstVideo, stream)
    bindMediaStreamToVideo(replacementVideo, stream)
    bindMediaStreamToVideo(replacementVideo, stream)

    expect(firstVideo.srcObject).toBe(stream)
    expect(replacementVideo.srcObject).toBe(stream)
    expect(firstPlay).toHaveBeenCalledOnce()
    expect(replacementPlay).toHaveBeenCalledOnce()

    bindMediaStreamToVideo(replacementVideo, null)
    expect(replacementVideo.srcObject).toBeNull()
    expect(replacementPause).toHaveBeenCalledOnce()
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
