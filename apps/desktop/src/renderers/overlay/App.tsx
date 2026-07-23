import { useEffect, useRef, useState } from 'react'
import type { OverlaySettings } from '../../shared/contracts'
import {
  applySettingsToQueue,
  DEFAULT_OVERLAY_SETTINGS,
  enqueueBarrage,
  fitSettingsToViewport,
  normalizeOverlaySettings,
  remainingTravelMs,
  travelDurationMs,
  type VisibleBarrage
} from './overlay-state'

export function App(): React.JSX.Element {
  const [items, setItems] = useState<VisibleBarrage[]>([])
  const [settings, setSettings] = useState(DEFAULT_OVERLAY_SETTINGS)
  const itemsRef = useRef<VisibleBarrage[]>([])
  const settingsRef = useRef(settings)
  const requestedSettingsRef = useRef(DEFAULT_OVERLAY_SETTINGS)
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
      const remainingMs = remainingTravelMs(
        item.shownAt,
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
      const fitted = fitSettingsToViewport(normalized, window.innerHeight)
      const result = applySettingsToQueue(itemsRef.current, fitted)
      for (const removed of result.removed) {
        clearTimer(removed.instanceId)
      }
      itemsRef.current = result.items
      requestedSettingsRef.current = normalized
      settingsRef.current = fitted
      setItems(result.items)
      setSettings(fitted)
      for (const item of result.items) {
        scheduleRemoval(item)
      }
    })
    const reflowForViewport = (): void => {
      const fitted = fitSettingsToViewport(
        requestedSettingsRef.current,
        window.innerHeight
      )
      const result = applySettingsToQueue(itemsRef.current, fitted)
      for (const removed of result.removed) {
        clearTimer(removed.instanceId)
      }
      itemsRef.current = result.items
      settingsRef.current = fitted
      setItems(result.items)
      setSettings(fitted)
    }
    window.addEventListener('resize', reflowForViewport)

    return () => {
      removeBarrage()
      clear()
      removeSettings()
      window.removeEventListener('resize', reflowForViewport)
      clearItems()
    }
  }, [])

  return (
    <main
      className="overlay-root"
      aria-label="弹幕覆盖层"
      style={{
        '--overlay-font-size': `${settings.fontSizePx}px`,
        '--overlay-speed': settings.speed,
        '--overlay-opacity': settings.opacity / 100,
        '--overlay-density': settings.density,
        '--overlay-region-top': `${settings.region.topPercent}%`,
        '--overlay-region-bottom': `${settings.region.bottomPercent}%`,
        '--travel-duration': `${travelDurationMs(settings.speed)}ms`
      } as React.CSSProperties}
    >
      <div className="ai-watermark">ADVX LIVE · AI AUDIENCE</div>
      {items.map((item) => (
        <div
          className="overlay-barrage"
          key={item.instanceId}
          style={{
            '--lane': item.lane,
            '--lane-top': `${item.laneTopPercent}%`,
            '--barrage-color': item.color
          } as React.CSSProperties}
        >
          <span className="overlay-name">{item.audienceName} · AI</span>
          <span>{item.text}</span>
        </div>
      ))}
    </main>
  )
}
