import { describe, expect, it } from 'vitest'
import { canStartLive } from './useMediaController'

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
