const AUDIO_SETTINGS_STORAGE_KEY = 'advx.audio-settings'
const AUDIO_SETTINGS_VERSION = 1

export type AudioSettings = {
  version: 1
  systemAudioEnabled: boolean
}

export const DEFAULT_AUDIO_SETTINGS: AudioSettings = {
  version: AUDIO_SETTINGS_VERSION,
  systemAudioEnabled: true
}

export function loadAudioSettings(storage: Pick<Storage, 'getItem'>): AudioSettings {
  const raw = storage.getItem(AUDIO_SETTINGS_STORAGE_KEY)
  if (!raw) return DEFAULT_AUDIO_SETTINGS

  try {
    const parsed = JSON.parse(raw) as Partial<AudioSettings>
    if (
      parsed.version !== AUDIO_SETTINGS_VERSION ||
      typeof parsed.systemAudioEnabled !== 'boolean'
    ) {
      return DEFAULT_AUDIO_SETTINGS
    }
    return {
      version: AUDIO_SETTINGS_VERSION,
      systemAudioEnabled: parsed.systemAudioEnabled
    }
  } catch {
    return DEFAULT_AUDIO_SETTINGS
  }
}

export function saveAudioSettings(
  storage: Pick<Storage, 'setItem'>,
  settings: AudioSettings
): void {
  try {
    storage.setItem(AUDIO_SETTINGS_STORAGE_KEY, JSON.stringify(settings))
  } catch {
    // Settings persistence must never interrupt the live control surface.
  }
}
