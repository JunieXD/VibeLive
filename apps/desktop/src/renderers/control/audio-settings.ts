const AUDIO_SETTINGS_STORAGE_KEY = 'advx.audio-settings'
const AUDIO_SETTINGS_VERSION = 2

export type AudioSettings = {
  version: 2
  microphoneEnabled: boolean
  selectedMicrophoneId: string
  systemAudioEnabled: boolean
}

export const DEFAULT_AUDIO_SETTINGS: AudioSettings = {
  version: AUDIO_SETTINGS_VERSION,
  microphoneEnabled: true,
  selectedMicrophoneId: '',
  systemAudioEnabled: true
}

export function loadAudioSettings(storage: Pick<Storage, 'getItem'>): AudioSettings {
  const raw = storage.getItem(AUDIO_SETTINGS_STORAGE_KEY)
  if (!raw) return DEFAULT_AUDIO_SETTINGS

  try {
    const parsed = JSON.parse(raw) as {
      version?: unknown
      microphoneEnabled?: unknown
      selectedMicrophoneId?: unknown
      systemAudioEnabled?: unknown
    }
    if (parsed.version === 1 && typeof parsed.systemAudioEnabled === 'boolean') {
      return {
        ...DEFAULT_AUDIO_SETTINGS,
        systemAudioEnabled: parsed.systemAudioEnabled
      }
    }
    if (
      parsed.version !== AUDIO_SETTINGS_VERSION ||
      typeof parsed.microphoneEnabled !== 'boolean' ||
      typeof parsed.selectedMicrophoneId !== 'string' ||
      typeof parsed.systemAudioEnabled !== 'boolean'
    ) {
      return DEFAULT_AUDIO_SETTINGS
    }
    return {
      version: AUDIO_SETTINGS_VERSION,
      microphoneEnabled: parsed.microphoneEnabled,
      selectedMicrophoneId: parsed.selectedMicrophoneId,
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
