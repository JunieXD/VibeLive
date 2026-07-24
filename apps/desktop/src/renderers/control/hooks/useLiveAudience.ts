import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  BackendAudienceSnapshot,
  BackendViewerEvent,
  BackendViewerSnapshot
} from '../../../shared/contracts'

export function useLiveAudience(sessionId: string | null, active: boolean) {
  const [audience, setAudience] = useState<BackendAudienceSnapshot | null>(null)
  const [pendingViewerId, setPendingViewerId] = useState<string | null>(null)
  const scopeRef = useRef<string | null>(null)
  const audienceRef = useRef<BackendAudienceSnapshot | null>(null)
  const revisionRef = useRef(0)

  const refresh = useCallback(async (): Promise<void> => {
    if (!sessionId || !active) return
    const next = await window.advx.queryLiveAudience(sessionId)
    if (
      scopeRef.current !== sessionId ||
      next.session_id !== sessionId ||
      next.population_revision < revisionRef.current
    ) {
      return
    }
    revisionRef.current = next.population_revision
    audienceRef.current = next
    setAudience(next)
  }, [active, sessionId])

  useEffect(() => {
    if (!sessionId || !active) {
      scopeRef.current = null
      audienceRef.current = null
      revisionRef.current = 0
      setAudience(null)
      return
    }
    scopeRef.current = sessionId
    audienceRef.current = null
    revisionRef.current = 0
    setAudience(null)
    void refresh()
    const unsubscribe = window.advx.onBackendViewerEvent((event: BackendViewerEvent) => {
      if (scopeRef.current !== sessionId || event.session_id !== sessionId) return
      if (event.population_revision <= revisionRef.current) return
      const current = audienceRef.current
      if (!current || event.population_revision > revisionRef.current + 1) {
        void refresh()
        return
      }
      revisionRef.current = event.population_revision
      const next = mergeViewerSnapshot(
        current,
        event.viewer,
        event.population_revision
      )
      audienceRef.current = next
      setAudience(next)
    })
    return () => {
      unsubscribe()
      if (scopeRef.current === sessionId) {
        scopeRef.current = null
        audienceRef.current = null
        revisionRef.current = 0
      }
    }
  }, [active, refresh, sessionId])

  const run = useCallback(
    async (
      expectedSessionId: string,
      viewerId: string,
      operation: () => Promise<BackendViewerSnapshot>
    ): Promise<void> => {
      setPendingViewerId(viewerId)
      try {
        const viewer = await operation()
        const current = audienceRef.current
        if (
          scopeRef.current !== expectedSessionId ||
          current?.session_id !== expectedSessionId
        ) {
          return
        }
        const next = mergeViewerSnapshot(current, viewer)
        audienceRef.current = next
        setAudience(next)
      } finally {
        setPendingViewerId(null)
      }
    },
    []
  )

  return {
    audience,
    pendingViewerId,
    refresh,
    mute: (viewerId: string, durationMs: number) =>
      sessionId
        ? run(sessionId, viewerId, () =>
            window.advx.muteViewer(sessionId, viewerId, durationMs)
          )
        : Promise.resolve(),
    unmute: (viewerId: string) =>
      sessionId
        ? run(sessionId, viewerId, () =>
            window.advx.unmuteViewer(sessionId, viewerId)
          )
        : Promise.resolve(),
    kick: (viewerId: string) =>
      sessionId
        ? run(sessionId, viewerId, () => window.advx.kickViewer(sessionId, viewerId))
        : Promise.resolve()
  }
}

export function mergeViewerSnapshot(
  audience: BackendAudienceSnapshot,
  viewer: BackendViewerSnapshot,
  populationRevision = audience.population_revision
): BackendAudienceSnapshot {
  const exists = audience.viewers.some(
    (item) => item.viewer_instance_id === viewer.viewer_instance_id
  )
  const viewers = exists
    ? audience.viewers.map((item) =>
        item.viewer_instance_id === viewer.viewer_instance_id &&
        viewer.presence_revision >= item.presence_revision &&
        viewer.moderation_revision >= item.moderation_revision
          ? viewer
          : item
      )
    : [...audience.viewers, viewer]
  return {
    ...audience,
    population_revision: populationRevision,
    active_count: viewers.filter((item) => item.presence_state === 'active').length,
    viewers
  }
}
