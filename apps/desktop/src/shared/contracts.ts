export type DesktopSource = {
  id: string
  name: string
  thumbnailUrl: string
  appIconUrl: string | null
  kind: 'screen' | 'window'
}

export type BarrageEvent = {
  barrageId: string
  audienceId: string
  audienceName: string
  text: string
  color: string
  createdAt: number
}

export type ModelConfig = {
  baseUrl: string
  model: string
  apiKey: string
}

export type SaveModelConfigResult = {
  ok: boolean
  securelyStored: boolean
}

export type MediaAccessStatus =
  | 'not-determined'
  | 'granted'
  | 'denied'
  | 'restricted'
  | 'unknown'

export type MediaAccessSnapshot = {
  microphone: MediaAccessStatus
  screen: MediaAccessStatus
}

export type OverlayTarget = {
  id: number
  name: string
  bounds: {
    x: number
    y: number
    width: number
    height: number
  }
  scaleFactor: number
  isPrimary: boolean
}

export type OverlayRegion = {
  topPercent: number
  bottomPercent: number
}

export type OverlaySettings = {
  targetDisplayId: number
  fontSizePx: number
  speed: number
  opacity: number
  density: number
  region: OverlayRegion
  clickThrough: boolean
}

export type ControlApi = {
  listDesktopSources: () => Promise<DesktopSource[]>
  selectDesktopSource: (sourceId: string) => Promise<boolean>
  getMediaAccessStatus: () => Promise<MediaAccessSnapshot>
  requestMicrophonePermission: () => Promise<MediaAccessStatus>
  listOverlayTargets: () => Promise<OverlayTarget[]>
  getOverlaySettings: () => Promise<OverlaySettings>
  setOverlaySettings: (settings: OverlaySettings) => Promise<OverlaySettings>
  showOverlay: () => Promise<void>
  hideOverlay: () => Promise<void>
  clearOverlay: () => Promise<void>
  pushBarrage: (event: BarrageEvent) => Promise<void>
  saveModelConfig: (config: ModelConfig) => Promise<SaveModelConfigResult>
  onEmergencyStop: (listener: () => void) => () => void
  onOverlaySettingsChanged: (listener: (settings: OverlaySettings) => void) => () => void
}

export type OverlayApi = {
  onBarrage: (listener: (event: BarrageEvent) => void) => () => void
  onClear: (listener: () => void) => () => void
  onSettingsChanged: (listener: (settings: OverlaySettings) => void) => () => void
}
