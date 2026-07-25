import { useEffect, useRef, useState } from 'react'
import type { OverlayFontFamily, OverlaySettings } from '../../shared/contracts'
import {
  applySettingsToQueue,
  BARRAGE_LINE_HEIGHT,
  DEFAULT_OVERLAY_SETTINGS,
  enqueueBarrage,
  fixedBarrageLaneOffsetPx,
  FIXED_BARRAGE_DURATION_MS,
  normalizeOverlaySettings,
  remainingDisplayMs,
  travelDurationMs,
  type VisibleBarrage
} from './overlay-state'

const FONT_FAMILY_STACKS: Record<OverlayFontFamily, string> = {
  bilibili:
    'SimHei, "Microsoft JhengHei", "Microsoft YaHei", Arial, Helvetica, sans-serif',
  yahei: '"Microsoft YaHei", "PingFang SC", Arial, Helvetica, sans-serif',
  system: '"Segoe UI", "Microsoft YaHei", Arial, Helvetica, sans-serif'
}

function outlineTextShadow(widthPx: number): string {
  if (widthPx <= 0) return 'none'

  const offset = `${widthPx}px`
  const blur = `${Math.max(1, widthPx)}px`
  return [
    `${offset} 0 ${blur} #000`,
    `-${offset} 0 ${blur} #000`,
    `0 ${offset} ${blur} #000`,
    `0 -${offset} ${blur} #000`
  ].join(', ')
}

export function App(): React.JSX.Element {
  const [items, setItems] = useState<VisibleBarrage[]>([])
  const [settings, setSettings] = useState(DEFAULT_OVERLAY_SETTINGS)
  const [viewportHeight, setViewportHeight] = useState(() => window.innerHeight)
  const itemsRef = useRef<VisibleBarrage[]>([])
  const settingsRef = useRef(settings)
  const timersRef = useRef(new Map<number, number>())
  const nextInstanceIdRef = useRef(1)

  useEffect(() => {
    const clearTimer = (instanceId: number): void => {
      const timer = timersRef.current.get(instanceId)
      if (timer !== undefined) {
        window.clearTimeout(timer)
        timersRef.current.delete(instanceId)
      }
    }

    const removeInstance = (instanceId: number): void => {
      clearTimer(instanceId)
      itemsRef.current = itemsRef.current.filter((item) => item.instanceId !== instanceId)
      setItems(itemsRef.current)
    }

    const scheduleRemoval = (item: VisibleBarrage): void => {
      clearTimer(item.instanceId)
      const remainingMs = remainingDisplayMs(
        item.shownAt,
        item.mode,
        settingsRef.current.speed
      )
      if (remainingMs <= 0) {
        removeInstance(item.instanceId)
        return
      }
      const timer = window.setTimeout(() => removeInstance(item.instanceId), remainingMs)
      timersRef.current.set(item.instanceId, timer)
    }

    const clearItems = (): void => {
      for (const timer of timersRef.current.values()) {
        window.clearTimeout(timer)
      }
      timersRef.current.clear()
      itemsRef.current = []
      setItems([])
    }

    const removeBarrage = window.advxOverlay.onBarrage((event) => {
      const instanceId = nextInstanceIdRef.current
      nextInstanceIdRef.current += 1
      const result = enqueueBarrage(
        itemsRef.current,
        event,
        instanceId,
        settingsRef.current
      )

      for (const removed of result.removed) {
        clearTimer(removed.instanceId)
      }
      itemsRef.current = result.items
      setItems(result.items)

      const addedItem = result.items.find((item) => item.instanceId === instanceId)
      if (addedItem) {
        scheduleRemoval(addedItem)
      }
    })
    const clear = window.advxOverlay.onClear(clearItems)
    const removeSettings = window.advxOverlay.onSettingsChanged((snapshot: OverlaySettings) => {
      const normalized = normalizeOverlaySettings(snapshot)
      const result = applySettingsToQueue(itemsRef.current, normalized)
      for (const removed of result.removed) {
        clearTimer(removed.instanceId)
      }
      itemsRef.current = result.items
      settingsRef.current = normalized
      setItems(result.items)
      setSettings(normalized)
      for (const item of result.items) {
        scheduleRemoval(item)
      }
    })
    const updateViewportHeight = (): void => setViewportHeight(window.innerHeight)
    window.addEventListener('resize', updateViewportHeight)

    return () => {
      removeBarrage()
      clear()
      removeSettings()
      window.removeEventListener('resize', updateViewportHeight)
      clearItems()
    }
  }, [])

  return (
    <main
      className="overlay-root"
      aria-label="弹幕覆盖层"
      style={{
        '--overlay-font-size': `${settings.fontSizePx}px`,
        '--overlay-font-family': FONT_FAMILY_STACKS[settings.fontFamily],
        '--overlay-font-weight': settings.bold ? 700 : 400,
        '--overlay-outline-shadow': outlineTextShadow(settings.outlineWidthPx),
        '--overlay-line-height': BARRAGE_LINE_HEIGHT,
        '--overlay-speed': settings.speed,
        '--overlay-opacity': settings.opacity / 100,
        '--overlay-density': settings.density,
        '--overlay-region-top': `${settings.region.topPercent}%`,
        '--overlay-region-bottom': `${settings.region.bottomPercent}%`,
        '--travel-duration': `${travelDurationMs(settings.speed)}ms`,
        '--fixed-duration': `${FIXED_BARRAGE_DURATION_MS}ms`
      } as React.CSSProperties}
    >
      {items.map((item) => (
        <div
          className={`overlay-barrage overlay-barrage--${item.mode}`}
          key={item.instanceId}
          style={{
            '--lane': item.lane,
            '--lane-top': `${item.laneTopPercent}%`,
            '--lane-bottom': `${item.laneBottomPercent}%`,
            '--fixed-lane-offset': `${fixedBarrageLaneOffsetPx(
              item.lane,
              settings.fontSizePx,
              settings.density,
              settings.region,
              viewportHeight
            )}px`
          } as React.CSSProperties}
        >
          {item.text}
        </div>
      ))}
    </main>
  )
}
