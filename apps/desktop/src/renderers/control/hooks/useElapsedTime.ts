import { useEffect, useRef, useState } from 'react'
import type { SessionStatus } from '../../../shared/session'

export function useElapsedTime(
  sessionStatus: SessionStatus,
  backendStartedAtMs: number | null = null
): number {
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const startedAtRef = useRef<number | null>(null)

  useEffect(() => {
    if (sessionStatus === 'running' || sessionStatus === 'paused') {
      startedAtRef.current = backendStartedAtMs ?? startedAtRef.current ?? Date.now()
      const updateElapsed = (): void => {
        setElapsedSeconds(
          Math.max(0, Math.floor((Date.now() - (startedAtRef.current ?? Date.now())) / 1000))
        )
      }
      updateElapsed()
      const timer = window.setInterval(() => {
        updateElapsed()
      }, 1000)
      return () => window.clearInterval(timer)
    }
    if (sessionStatus === 'idle') {
      startedAtRef.current = null
      setElapsedSeconds(0)
    }
  }, [backendStartedAtMs, sessionStatus])

  return elapsedSeconds
}
