import { useEffect, useRef, useState } from 'react'
import type { SessionStatus } from '../../../shared/session'

export function useElapsedTime(sessionStatus: SessionStatus): number {
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const startedAtRef = useRef<number | null>(null)

  useEffect(() => {
    if (sessionStatus === 'running' || sessionStatus === 'paused') {
      if (startedAtRef.current === null) {
        startedAtRef.current = Date.now() - elapsedSeconds * 1000
      }
      const timer = window.setInterval(() => {
        setElapsedSeconds(Math.floor((Date.now() - (startedAtRef.current ?? Date.now())) / 1000))
      }, 1000)
      return () => window.clearInterval(timer)
    }
    if (sessionStatus === 'idle') {
      startedAtRef.current = null
      setElapsedSeconds(0)
    }
  }, [elapsedSeconds, sessionStatus])

  return elapsedSeconds
}
