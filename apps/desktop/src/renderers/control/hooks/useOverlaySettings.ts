import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import type { OverlaySettings, OverlayTarget } from '../../../shared/contracts'

type OverlaySettingsState = {
  targets: OverlayTarget[]
  settings: OverlaySettings | null
  setSettings: Dispatch<SetStateAction<OverlaySettings | null>>
  updateSettings: (settings: OverlaySettings) => void
  notice: string | null
}

export function useOverlaySettings(): OverlaySettingsState {
  const [targets, setTargets] = useState<OverlayTarget[]>([])
  const [settings, setSettings] = useState<OverlaySettings | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)
  const revisionRef = useRef(0)
  const pendingRef = useRef(false)

  useEffect(() => {
    let active = true

    const unsubscribe = window.advx.onOverlaySettingsChanged((updatedSettings) => {
      if (!active) return
      void window.advx
        .listOverlayTargets()
        .then((updatedTargets) => {
          if (active) setTargets(updatedTargets)
        })
        .catch(() => undefined)
      if (pendingRef.current) return
      revisionRef.current += 1
      setSettings(updatedSettings)
      setNotice('已同步')
    })

    void Promise.all([window.advx.listOverlayTargets(), window.advx.getOverlaySettings()])
      .then(([loadedTargets, loadedSettings]) => {
        if (!active) return
        setTargets(loadedTargets)
        setSettings(loadedSettings)
      })
      .catch(() => {
        if (active) setNotice('加载失败')
      })

    return () => {
      active = false
      unsubscribe()
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
      }
    }
  }, [])

  const updateSettings = (updatedSettings: OverlaySettings): void => {
    const revision = revisionRef.current + 1
    revisionRef.current = revision
    pendingRef.current = true
    setSettings(updatedSettings)
    setNotice('正在同步')

    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
    }
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      void window.advx
        .setOverlaySettings(updatedSettings)
        .then((normalizedSettings) => {
          if (revisionRef.current !== revision) return
          pendingRef.current = false
          setSettings(normalizedSettings)
          setNotice('已同步')
        })
        .catch(() => {
          if (revisionRef.current === revision) {
            pendingRef.current = false
            setNotice('同步失败')
          }
        })
    }, 150)
  }

  return {
    targets,
    settings,
    setSettings,
    updateSettings,
    notice
  }
}
