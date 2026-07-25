import { describe, expect, it } from 'vitest'
import {
  DEFAULT_AUDIO_SETTINGS,
  loadAudioSettings,
  saveAudioSettings
} from './audio-settings'

describe('audio settings', () => {
  it('round trips the versioned setting', () => {
    let saved = ''
    saveAudioSettings(
      { setItem: (_key, value) => { saved = value } },
      {
        version: 2,
        microphoneEnabled: false,
        selectedMicrophoneId: 'microphone-2',
        systemAudioEnabled: false
      }
    )
    expect(loadAudioSettings({ getItem: () => saved })).toEqual({
      version: 2,
      microphoneEnabled: false,
      selectedMicrophoneId: 'microphone-2',
      systemAudioEnabled: false
    })
  })

  it('migrates the previous system-audio-only setting', () => {
    expect(
      loadAudioSettings({
        getItem: () => JSON.stringify({ version: 1, systemAudioEnabled: false })
      })
    ).toEqual({
      version: 2,
      microphoneEnabled: true,
      selectedMicrophoneId: '',
      systemAudioEnabled: false
    })
  })

  it('falls back when stored data has an unsupported version', () => {
    expect(
      loadAudioSettings({
        getItem: () => JSON.stringify({ version: 3, systemAudioEnabled: false })
      })
    ).toEqual(DEFAULT_AUDIO_SETTINGS)
  })

})
