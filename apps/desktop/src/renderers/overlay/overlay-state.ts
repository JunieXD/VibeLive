import type {
  BarrageEvent,
  BarrageMode,
  OverlaySettings
} from '../../shared/contracts'

export const FIXED_BARRAGE_DURATION_MS = 4_000

export const DEFAULT_OVERLAY_SETTINGS: OverlaySettings = {
  targetDisplayId: 0,
  fontSizePx: 25,
  fontFamily: 'bilibili',
  bold: true,
  outlineWidthPx: 1,
  speed: 75,
  opacity: 80,
  density: 6,
  region: {
    topPercent: 0,
    bottomPercent: 50
  },
  clickThrough: true
}

export type VisibleBarrage = BarrageEvent & {
  instanceId: number
  shownAt: number
  lane: number
  laneTopPercent: number
  laneBottomPercent: number
}

export type OverlayQueueResult = {
  items: VisibleBarrage[]
  removed: VisibleBarrage[]
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

export function normalizeOverlaySettings(settings: OverlaySettings): OverlaySettings {
  const topPercent = clamp(settings.region.topPercent, 0, 80)
  const bottomPercent = clamp(settings.region.bottomPercent, topPercent + 20, 100)

  return {
    ...settings,
    fontSizePx: clamp(settings.fontSizePx, 14, 36),
    fontFamily:
      settings.fontFamily === 'yahei' || settings.fontFamily === 'system'
        ? settings.fontFamily
        : 'bilibili',
    bold: typeof settings.bold === 'boolean' ? settings.bold : true,
    outlineWidthPx:
      typeof settings.outlineWidthPx === 'number'
        ? clamp(settings.outlineWidthPx, 0, 3)
        : 1,
    speed: clamp(settings.speed, 20, 100),
    opacity: clamp(settings.opacity, 30, 100),
    density: Math.round(clamp(settings.density, 1, 10)),
    region: {
      topPercent,
      bottomPercent
    }
  }
}

export function travelDurationMs(speed: number): number {
  const normalizedSpeed = clamp(speed, 20, 100)
  const progress = (normalizedSpeed - 20) / 80
  return Math.round(16_000 - progress * 11_000)
}

export function remainingTravelMs(
  shownAt: number,
  speed: number,
  now = Date.now()
): number {
  return Math.max(0, travelDurationMs(speed) - (now - shownAt))
}

export function displayDurationMs(mode: BarrageMode, speed: number): number {
  return mode === 'scroll' ? travelDurationMs(speed) : FIXED_BARRAGE_DURATION_MS
}

export function remainingDisplayMs(
  shownAt: number,
  mode: BarrageMode,
  speed: number,
  now = Date.now()
): number {
  return Math.max(0, displayDurationMs(mode, speed) - (now - shownAt))
}

export function fitSettingsToViewport(
  settings: OverlaySettings,
  viewportHeight: number
): OverlaySettings {
  const normalized = normalizeOverlaySettings(settings)
  const regionHeight =
    Math.max(1, viewportHeight) *
    ((normalized.region.bottomPercent - normalized.region.topPercent) / 100)
  const rowPitch = normalized.fontSizePx * 1.35 + 20
  const availableLanes = Math.max(1, Math.floor(regionHeight / rowPitch))

  return {
    ...normalized,
    density: Math.min(normalized.density, availableLanes)
  }
}

export function laneFor(id: string, laneCount: number): number {
  const normalizedLaneCount = Math.max(1, Math.round(laneCount))
  return (
    [...id].reduce((total, character) => total + character.charCodeAt(0), 0) %
    normalizedLaneCount
  )
}

export function laneTopPercent(
  lane: number,
  laneCount: number,
  region: OverlaySettings['region']
): number {
  const normalizedLaneCount = Math.max(1, Math.round(laneCount))
  const normalizedLane = clamp(Math.round(lane), 0, normalizedLaneCount - 1)
  const topPercent = clamp(region.topPercent, 0, 100)
  const bottomPercent = clamp(region.bottomPercent, topPercent, 100)

  if (normalizedLaneCount === 1) {
    return topPercent
  }

  return topPercent + (normalizedLane / normalizedLaneCount) * (bottomPercent - topPercent)
}

export function laneBottomPercent(
  lane: number,
  laneCount: number,
  region: OverlaySettings['region']
): number {
  const normalizedLaneCount = Math.max(1, Math.round(laneCount))
  const normalizedLane = clamp(Math.round(lane), 0, normalizedLaneCount - 1)
  const topPercent = clamp(region.topPercent, 0, 100)
  const bottomPercent = clamp(region.bottomPercent, topPercent, 100)

  return 100 - bottomPercent +
    (normalizedLane / normalizedLaneCount) * (bottomPercent - topPercent)
}

function barrageMode(mode: BarrageEvent['mode'] | undefined): BarrageMode {
  return mode === 'top' || mode === 'bottom' ? mode : 'scroll'
}

function availableLaneFor(
  id: string,
  laneCount: number,
  occupiedLanes: ReadonlySet<number>,
  mode: BarrageMode = 'scroll'
): number | null {
  const preferredLane = mode === 'scroll' ? laneFor(id, laneCount) : 0
  for (let offset = 0; offset < laneCount; offset += 1) {
    const lane = (preferredLane + offset) % laneCount
    if (!occupiedLanes.has(lane)) return lane
  }
  return null
}

export function enqueueBarrage(
  current: VisibleBarrage[],
  event: BarrageEvent,
  instanceId: number,
  settings: OverlaySettings,
  shownAt = Date.now()
): OverlayQueueResult {
  const normalizedSettings = normalizeOverlaySettings(settings)
  const mode = barrageMode(event.mode)
  const sameModeItems = current.filter((item) => barrageMode(item.mode) === mode)
  const occupiedLanes = new Set(sameModeItems.map((item) => item.lane))
  const freeLane = availableLaneFor(
    event.barrageId,
    normalizedSettings.density,
    occupiedLanes,
    mode
  )
  const evicted =
    freeLane === null
      ? sameModeItems[0]
      : current.length >= normalizedSettings.density
        ? current[0]
        : undefined
  const lane = freeLane ?? evicted?.lane ?? 0
  const item: VisibleBarrage = {
    ...event,
    mode,
    instanceId,
    shownAt,
    lane,
    laneTopPercent: laneTopPercent(
      lane,
      normalizedSettings.density,
      normalizedSettings.region
    ),
    laneBottomPercent: laneBottomPercent(
      lane,
      normalizedSettings.density,
      normalizedSettings.region
    )
  }
  const remaining = evicted
    ? current.filter((candidate) => candidate.instanceId !== evicted.instanceId)
    : current

  return {
    items: [...remaining, item],
    removed: evicted ? [evicted] : []
  }
}

export function applySettingsToQueue(
  current: VisibleBarrage[],
  settings: OverlaySettings
): OverlayQueueResult {
  const normalizedSettings = normalizeOverlaySettings(settings)
  const overflow = Math.max(0, current.length - normalizedSettings.density)
  const kept = current.slice(overflow)
  const occupiedLanesByMode = new Map<BarrageMode, Set<number>>()

  return {
    items: kept.map((item) => {
      const mode = barrageMode(item.mode)
      const occupiedLanes = occupiedLanesByMode.get(mode) ?? new Set<number>()
      const lane =
        availableLaneFor(
          item.barrageId,
          normalizedSettings.density,
          occupiedLanes,
          mode
        ) ?? 0
      occupiedLanes.add(lane)
      occupiedLanesByMode.set(mode, occupiedLanes)
      return {
        ...item,
        mode,
        lane,
        laneTopPercent: laneTopPercent(
          lane,
          normalizedSettings.density,
          normalizedSettings.region
        ),
        laneBottomPercent: laneBottomPercent(
          lane,
          normalizedSettings.density,
          normalizedSettings.region
        )
      }
    }),
    removed: current.slice(0, overflow)
  }
}
