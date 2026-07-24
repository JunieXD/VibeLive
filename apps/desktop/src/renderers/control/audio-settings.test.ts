import { describe, expect, it } from 'vitest'
import {
  DEFAULT_AUDIO_SETTINGS,
  loadAudioSettings,
  saveAudioSettings
} from './audio-settings'

describe('audio settings', () => {
  it('defaults system audio on without reading visual settings', () => {
    expect(loadAudioSettings({ getItem: () => null })).toEqual(DEFAULT_AUDIO_SETTINGS)
  })

  it('round trips the versioned setting', () => {
    let saved = ''
    saveAudioSettings(
      { setItem: (_key, value) => { saved = value } },
      { version: 1, systemAudioEnabled: false }
    )
    expect(loadAudioSettings({ getItem: () => saved })).toEqual({
      version: 1,
      systemAudioEnabled: false
    })
  })

  it('falls back when stored data has an unsupported version', () => {
    expect(
      loadAudioSettings({
        getItem: () => JSON.stringify({ version: 2, systemAudioEnabled: false })
      })
    ).toEqual(DEFAULT_AUDIO_SETTINGS)
  })

  it('does not surface storage write failures', () => {
    expect(() =>
      saveAudioSettings(
        { setItem: () => { throw new Error('quota exceeded') } },
        DEFAULT_AUDIO_SETTINGS
      )
    ).not.toThrow()
  })
})
