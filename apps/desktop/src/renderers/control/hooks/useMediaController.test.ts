import { describe, expect, it } from 'vitest'
import { canStartLive, resolveAudioChannelStatus } from './useMediaController'

describe('live start eligibility', () => {
  it('allows a screen-only session without optional services or microphones', () => {
    expect(
      canStartLive({
        sessionStatus: 'idle',
        visualMode: 'screen',
        hasScreen: true,
        hasCamera: false
      })
    ).toBe(true)
  })

  it('requires the visual source selected by the current mode', () => {
    expect(
      canStartLive({
        sessionStatus: 'idle',
        visualMode: 'screen',
        hasScreen: false,
        hasCamera: true
      })
    ).toBe(false)
    expect(
      canStartLive({
        sessionStatus: 'idle',
        visualMode: 'camera',
        hasScreen: false,
        hasCamera: true
      })
    ).toBe(true)
    expect(
      canStartLive({
        sessionStatus: 'idle',
        visualMode: 'pip',
        hasScreen: true,
        hasCamera: false
      })
    ).toBe(false)
  })

  it('does not allow another live session while one is active', () => {
    expect(
      canStartLive({
        sessionStatus: 'running',
        visualMode: 'screen',
        hasScreen: true,
        hasCamera: false
      })
    ).toBe(false)
  })
})

describe('audio channel UI status', () => {
  it('shows a source-specific transport rejection instead of capture ready', () => {
    expect(
      resolveAudioChannelStatus({
        paused: false,
        ready: true,
        transportError: 'system upload rejected',
        idleStatus: '等待采集'
      })
    ).toBe('传输异常')
    expect(
      resolveAudioChannelStatus({
        paused: false,
        ready: true,
        transportError: null,
        idleStatus: '待检测'
      })
    ).toBe('正常')
  })
})
