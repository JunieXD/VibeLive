import { describe, expect, it } from 'vitest'
import type {
  BarrageEvent,
  BarrageMode,
  OverlaySettings
} from '../../shared/contracts'
import {
  applySettingsToQueue,
  DEFAULT_OVERLAY_SETTINGS,
  displayDurationMs,
  enqueueBarrage,
  FIXED_BARRAGE_DURATION_MS,
  fitSettingsToViewport,
  laneFor,
  laneBottomPercent,
  laneTopPercent,
  remainingDisplayMs,
  remainingTravelMs,
  travelDurationMs,
  type VisibleBarrage
} from './overlay-state'

function barrage(barrageId: string, mode: BarrageMode = 'scroll'): BarrageEvent {
  return {
    barrageId,
    audienceId: 'audience',
    text: barrageId,
    createdAt: 0,
    mode
  }
}

function settings(overrides: Partial<OverlaySettings> = {}): OverlaySettings {
  return {
    ...DEFAULT_OVERLAY_SETTINGS,
    ...overrides,
    region: overrides.region ?? DEFAULT_OVERLAY_SETTINGS.region
  }
}

describe('overlay state helpers', () => {
  it('maps the speed range to approximately sixteen through five seconds', () => {
    expect(travelDurationMs(20)).toBe(16_000)
    expect(travelDurationMs(60)).toBe(10_500)
    expect(travelDurationMs(100)).toBe(5_000)
    expect(travelDurationMs(0)).toBe(16_000)
    expect(travelDurationMs(120)).toBe(5_000)
    expect(remainingTravelMs(1_000, 100, 3_000)).toBe(3_000)
    expect(remainingTravelMs(1_000, 100, 7_000)).toBe(0)
    expect(displayDurationMs('scroll', 100)).toBe(5_000)
    expect(displayDurationMs('top', 100)).toBe(FIXED_BARRAGE_DURATION_MS)
    expect(displayDurationMs('bottom', 20)).toBe(FIXED_BARRAGE_DURATION_MS)
    expect(remainingDisplayMs(1_000, 'top', 100, 3_500)).toBe(1_500)
    expect(remainingDisplayMs(1_000, 'bottom', 100, 5_500)).toBe(0)
  })

  it('keeps deterministic lanes and their positions inside the configured region', () => {
    expect(laneFor('same-id', 4)).toBe(laneFor('same-id', 4))
    expect(laneFor('same-id', 4)).toBeGreaterThanOrEqual(0)
    expect(laneFor('same-id', 4)).toBeLessThan(4)
    expect(laneTopPercent(0, 4, { topPercent: 12, bottomPercent: 72 })).toBe(12)
    expect(laneTopPercent(3, 4, { topPercent: 12, bottomPercent: 72 })).toBe(57)
    expect(laneTopPercent(0, 1, { topPercent: 12, bottomPercent: 72 })).toBe(12)
    expect(laneBottomPercent(0, 4, { topPercent: 12, bottomPercent: 72 })).toBe(28)
    expect(laneBottomPercent(3, 4, { topPercent: 12, bottomPercent: 72 })).toBe(73)
  })

  it('allocates lanes independently for scrolling, top, and bottom barrages', () => {
    const configured = settings({ density: 3 })
    const items = [
      barrage('same-id', 'scroll'),
      barrage('same-id', 'top'),
      barrage('same-id', 'bottom')
    ].reduce<VisibleBarrage[]>(
      (current, event, index) =>
        enqueueBarrage(current, event, index + 1, configured).items,
      []
    )

    expect(items).toHaveLength(3)
    expect(new Set(items.map((item) => item.mode))).toEqual(
      new Set(['scroll', 'top', 'bottom'])
    )
    expect(items.find((item) => item.mode === 'top')?.lane).toBe(0)
    expect(items.find((item) => item.mode === 'bottom')?.lane).toBe(0)
  })

  it('keeps one visible item per lane and evicts the oldest item when full', () => {
    let items: VisibleBarrage[] = []
    let removed: VisibleBarrage[] = []

    for (let index = 1; index <= 7; index += 1) {
      const result = enqueueBarrage(
        items,
        barrage(`barrage-${index}`),
        index,
        settings({ density: 3 })
      )
      items = result.items
      removed = result.removed
    }

    expect(items.map((item) => item.instanceId)).toEqual([5, 6, 7])
    expect(removed.map((item) => item.instanceId)).toEqual([4])
    expect(items.every((item) => item.lane >= 0 && item.lane < 3)).toBe(true)
    expect(new Set(items.map((item) => item.lane)).size).toBe(items.length)
  })

  it('reflows and trims visible items when a settings snapshot lowers density', () => {
    let items: VisibleBarrage[] = []
    for (let index = 1; index <= 6; index += 1) {
      items = enqueueBarrage(
        items,
        barrage(`barrage-${index}`),
        index,
        settings({ density: 4 })
      ).items
    }

    const result = applySettingsToQueue(
      items,
      settings({
        density: 2,
        region: { topPercent: 20, bottomPercent: 40 }
      })
    )

    expect(result.items.map((item) => item.instanceId)).toEqual([5, 6])
    expect(result.removed.map((item) => item.instanceId)).toEqual([3, 4])
    expect(result.items.every((item) => item.lane < 2)).toBe(true)
    expect(
      result.items.every(
        (item) => item.laneTopPercent >= 20 && item.laneTopPercent <= 40
      )
    ).toBe(true)
  })

  it('reduces effective density when the selected region cannot fit the requested rows', () => {
    const fitted = fitSettingsToViewport(
      settings({
        fontSizePx: 36,
        density: 10,
        region: { topPercent: 40, bottomPercent: 60 }
      }),
      1_080
    )

    expect(fitted.density).toBe(3)
  })
})
