import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  BackendAudienceSnapshot,
  BackendViewerEvent,
  BackendViewerSnapshot
} from '../../../shared/contracts'

export type LiveAudienceRequestResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: string }

export async function resolveLiveAudienceRequest<T>(
  request: () => Promise<T>,
  fallbackMessage: string
): Promise<LiveAudienceRequestResult<T>> {
  try {
    return { ok: true, value: await request() }
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error && error.message ? error.message : fallbackMessage
    }
  }
}

export function useLiveAudience(
  sessionId: string | null,
  active: boolean,
  backendConnected: boolean
) {
  const [audience, setAudience] = useState<BackendAudienceSnapshot | null>(null)
  const [audienceLoading, setAudienceLoading] = useState(false)
  const [audienceError, setAudienceError] = useState<string | null>(null)
  const [operationError, setOperationError] = useState<string | null>(null)
  const [pendingViewerId, setPendingViewerId] = useState<string | null>(null)
  const scopeRef = useRef<string | null>(null)
  const audienceRef = useRef<BackendAudienceSnapshot | null>(null)
  const revisionRef = useRef(0)
  const requiredRevisionRef = useRef(0)
  const refreshPromiseRef = useRef<Promise<void> | null>(null)
  const refreshTokenRef = useRef<symbol | null>(null)
  const pendingOperationRef = useRef<symbol | null>(null)

  const refresh = useCallback((): Promise<void> => {
    if (!sessionId || !active) return Promise.resolve()
    const currentRequest = refreshPromiseRef.current
    if (currentRequest) return currentRequest
    const refreshToken = Symbol('audience-refresh')
    refreshTokenRef.current = refreshToken
    const request = (async () => {
      let refreshAgainForRevision = false
      setAudienceLoading(true)
      setAudienceError(null)
      try {
        const result = await resolveLiveAudienceRequest(
          () => window.advx.queryLiveAudience(sessionId),
          '无法读取直播观众数据。'
        )
        if (
          scopeRef.current !== sessionId ||
          refreshTokenRef.current !== refreshToken
        ) {
          return
        }
        if (!result.ok) {
          setAudienceError(result.error)
          return
        }
        const next = result.value
        if (next.session_id !== sessionId) {
          setAudienceError('服务端返回了不匹配的观众快照。')
          return
        }
        if (next.population_revision >= revisionRef.current) {
          revisionRef.current = next.population_revision
          audienceRef.current = next
          setAudience(next)
        }
        refreshAgainForRevision = revisionRef.current < requiredRevisionRef.current
      } finally {
        if (refreshTokenRef.current !== refreshToken) return
        refreshTokenRef.current = null
        refreshPromiseRef.current = null
        if (scopeRef.current !== sessionId) return
        if (refreshAgainForRevision) {
          void refresh()
        } else {
          setAudienceLoading(false)
        }
      }
    })()
    refreshPromiseRef.current = request
    return request
  }, [active, sessionId])

  useEffect(() => {
    if (!sessionId || !active) {
      refreshPromiseRef.current = null
      refreshTokenRef.current = null
      pendingOperationRef.current = null
      scopeRef.current = null
      audienceRef.current = null
      revisionRef.current = 0
      requiredRevisionRef.current = 0
      setAudience(null)
      setAudienceLoading(false)
      setAudienceError(null)
      setOperationError(null)
      setPendingViewerId(null)
      return
    }
    scopeRef.current = sessionId
    audienceRef.current = null
    revisionRef.current = 0
    requiredRevisionRef.current = 0
    refreshPromiseRef.current = null
    refreshTokenRef.current = null
    pendingOperationRef.current = null
    setAudience(null)
    setAudienceLoading(true)
    setAudienceError(null)
    setOperationError(null)
    setPendingViewerId(null)
    const unsubscribe = window.advx.onBackendViewerEvent((event: BackendViewerEvent) => {
      if (scopeRef.current !== sessionId || event.session_id !== sessionId) return
      if (event.population_revision <= revisionRef.current) return
      requiredRevisionRef.current = Math.max(
        requiredRevisionRef.current,
        event.population_revision
      )
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
      setAudienceLoading(false)
      setAudienceError(null)
    })
    return () => {
      unsubscribe()
      if (scopeRef.current === sessionId) {
        scopeRef.current = null
        audienceRef.current = null
        revisionRef.current = 0
        requiredRevisionRef.current = 0
        refreshPromiseRef.current = null
        refreshTokenRef.current = null
        pendingOperationRef.current = null
      }
    }
  }, [active, refresh, sessionId])

  useEffect(() => {
    if (!sessionId || !active || !backendConnected) return
    void refresh()
  }, [active, backendConnected, refresh, sessionId])

  const run = useCallback(
    async (
      expectedSessionId: string,
      viewerId: string,
      operationLabel: string,
      operation: () => Promise<BackendViewerSnapshot>
    ): Promise<void> => {
      if (pendingOperationRef.current) return
      const operationToken = Symbol('viewer-operation')
      pendingOperationRef.current = operationToken
      setPendingViewerId(viewerId)
      setOperationError(null)
      try {
        const result = await resolveLiveAudienceRequest(
          operation,
          `${operationLabel}请求未完成。`
        )
        if (
          scopeRef.current !== expectedSessionId ||
          pendingOperationRef.current !== operationToken
        ) {
          return
        }
        if (!result.ok) {
          setOperationError(`${operationLabel}失败：${result.error}`)
          return
        }
        const viewer = result.value
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
        if (pendingOperationRef.current !== operationToken) return
        pendingOperationRef.current = null
        setPendingViewerId(null)
      }
    },
    []
  )

  return {
    audience,
    audienceLoading,
    audienceError,
    operationError,
    pendingViewerId,
    refresh,
    clearOperationError: () => setOperationError(null),
    mute: (viewerId: string, durationMs: number) =>
      sessionId
        ? run(sessionId, viewerId, '禁言', () =>
            window.advx.muteViewer(sessionId, viewerId, durationMs)
          )
        : Promise.resolve(),
    unmute: (viewerId: string) =>
      sessionId
        ? run(sessionId, viewerId, '解除禁言', () =>
            window.advx.unmuteViewer(sessionId, viewerId)
          )
        : Promise.resolve(),
    kick: (viewerId: string) =>
      sessionId
        ? run(
            sessionId,
            viewerId,
            '踢出观众',
            () => window.advx.kickViewer(sessionId, viewerId)
          )
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
