import { describe, expect, it } from 'vitest'
import type { BarrageEvent, OverlaySettings } from '../../shared/contracts'
import {
  BARRAGE_LINE_HEIGHT,
  DEFAULT_OVERLAY_SETTINGS,
  enqueueBarrage,
  fixedBarrageLaneOffsetPx,
  FIXED_BARRAGE_LANE_GAP_PX,
  normalizeOverlaySettings,
  type VisibleBarrage
} from './overlay-state'

function settings(overrides: Partial<OverlaySettings> = {}): OverlaySettings {
  return {
    ...DEFAULT_OVERLAY_SETTINGS,
    ...overrides,
    region: overrides.region ?? DEFAULT_OVERLAY_SETTINGS.region
  }
}

function barrage(index: number): BarrageEvent {
  return {
    barrageId: `barrage-${index}`,
    audienceId: 'audience',
    text: `弹幕 ${index}`,
    createdAt: index,
    mode: 'scroll'
  }
}

describe('overlay density', () => {
  it('keeps the requested density even when rows would overlap', () => {
    const configured = normalizeOverlaySettings(settings({
      density: 60,
      fontSizePx: 36,
      region: { topPercent: 40, bottomPercent: 60 }
    }))
    let items: VisibleBarrage[] = []

    for (let index = 1; index <= configured.density; index += 1) {
      items = enqueueBarrage(items, barrage(index), index, configured).items
    }

    expect(configured.density).toBe(60)
    expect(items).toHaveLength(60)
    expect(new Set(items.map((item) => item.lane)).size).toBe(60)
  })

  it('keeps compact fixed lanes until they must compress to stay visible', () => {
    const fontSizePx = 25
    const compactPitch = fontSizePx * BARRAGE_LINE_HEIGHT +
      FIXED_BARRAGE_LANE_GAP_PX
    const region = { topPercent: 0, bottomPercent: 50 }
    const viewportHeight = 1_080

    expect(
      fixedBarrageLaneOffsetPx(1, fontSizePx, 7, region, viewportHeight)
    ).toBe(compactPitch)

    const lastOffset = fixedBarrageLaneOffsetPx(
      99,
      fontSizePx,
      100,
      region,
      viewportHeight
    )
    const regionHeightPx = viewportHeight * 0.5

    expect(lastOffset + fontSizePx * BARRAGE_LINE_HEIGHT + 8)
      .toBeLessThanOrEqual(regionHeightPx)
  })
})
