import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  AiCallListItem,
  AiCallQuery,
  AiCallTrace
} from '../../../shared/backend-client'
import { retainSelectedCallId } from '../features/ai-calls/aiCallFormatters'
import {
  appendAiCallPage,
  mergeAiCallFirstPage
} from '../features/ai-calls/aiCallQuery'

const DEFAULT_PAGE_SIZE = 50

export type UseAiCallLogOptions = {
  enabled: boolean
  query: AiCallQuery
  pollIntervalMs?: number
}

export type UseAiCallLogResult = {
  items: AiCallListItem[]
  selectedCall: AiCallTrace | null
  selectedCallId: string | null
  loading: boolean
  refreshing: boolean
  loadingMore: boolean
  detailLoading: boolean
  error: string | null
  detailError: string | null
  hasMore: boolean
  lastUpdatedAt: number | null
  selectCall: (callId: string) => void
  refresh: () => Promise<void>
  loadMore: () => Promise<void>
}

export function useAiCallLog({
  enabled,
  query,
  pollIntervalMs = 1_000
}: UseAiCallLogOptions): UseAiCallLogResult {
  const [items, setItems] = useState<AiCallListItem[]>([])
  const [selectedCallId, setSelectedCallId] = useState<string | null>(null)
  const [selectedCall, setSelectedCall] = useState<AiCallTrace | null>(null)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null)
  const itemsRef = useRef<AiCallListItem[]>([])
  const selectedCallIdRef = useRef<string | null>(null)
  const nextCursorRef = useRef<string | null>(null)
  const hasLoaded = useRef(false)
  const hasLoadedMore = useRef(false)
  const requestSequence = useRef(0)
  const detailRequestSequence = useRef(0)
  const refreshInFlightKey = useRef<string | null>(null)
  const loadMoreInFlightKey = useRef<string | null>(null)
  const queryKey = JSON.stringify(query)

  const updateItems = useCallback((nextItems: AiCallListItem[]) => {
    itemsRef.current = nextItems
    setItems(nextItems)
  }, [])

  const updateSelectedCallId = useCallback((callId: string | null) => {
    selectedCallIdRef.current = callId
    setSelectedCallId(callId)
  }, [])

  const updateNextCursor = useCallback((cursor: string | null) => {
    nextCursorRef.current = cursor
    setNextCursor(cursor)
  }, [])

  const refresh = useCallback(async (): Promise<void> => {
    if (!enabled || refreshInFlightKey.current === queryKey) return
    if (loadMoreInFlightKey.current === queryKey) return

    refreshInFlightKey.current = queryKey
    const sequence = ++requestSequence.current
    const initialLoad = !hasLoaded.current
    setRefreshing(true)
    setLoading(initialLoad)
    try {
      const page = await window.advx.queryAiCalls({
        ...query,
        cursor: undefined,
        limit: query.limit ?? DEFAULT_PAGE_SIZE
      })
      if (sequence !== requestSequence.current) return

      const nextItems = initialLoad
        ? page.items
        : mergeAiCallFirstPage(itemsRef.current, page.items)
      updateItems(nextItems)
      if (!hasLoadedMore.current) updateNextCursor(page.next_cursor ?? null)
      const nextSelectedCallId = retainSelectedCallId(nextItems, selectedCallIdRef.current)
      if (nextSelectedCallId !== selectedCallIdRef.current) {
        setSelectedCall(null)
        updateSelectedCallId(nextSelectedCallId)
      }
      setError(null)
      setLastUpdatedAt(Date.now())
      hasLoaded.current = true
    } catch (caught) {
      if (sequence !== requestSequence.current) return
      setError(caught instanceof Error ? caught.message : 'AI 调用日志读取失败。')
    } finally {
      if (refreshInFlightKey.current === queryKey) {
        refreshInFlightKey.current = null
      }
      if (sequence === requestSequence.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [enabled, query, queryKey, updateItems, updateNextCursor, updateSelectedCallId])

  const loadMore = useCallback(async (): Promise<void> => {
    const cursor = nextCursorRef.current
    if (!enabled || cursor === null || loadMoreInFlightKey.current === queryKey) return
    if (refreshInFlightKey.current === queryKey) return

    loadMoreInFlightKey.current = queryKey
    const sequence = ++requestSequence.current
    setLoadingMore(true)
    try {
      const page = await window.advx.queryAiCalls({
        ...query,
        cursor,
        limit: query.limit ?? DEFAULT_PAGE_SIZE
      })
      if (sequence !== requestSequence.current) return

      updateItems(appendAiCallPage(itemsRef.current, page.items))
      updateNextCursor(page.next_cursor ?? null)
      hasLoadedMore.current = true
      setError(null)
      setLastUpdatedAt(Date.now())
    } catch (caught) {
      if (sequence !== requestSequence.current) return
      setError(caught instanceof Error ? caught.message : '更多 AI 调用读取失败。')
    } finally {
      if (loadMoreInFlightKey.current === queryKey) {
        loadMoreInFlightKey.current = null
      }
      if (sequence === requestSequence.current) setLoadingMore(false)
    }
  }, [enabled, query, queryKey, updateItems, updateNextCursor])

  useEffect(() => {
    if (!enabled) return
    requestSequence.current += 1
    detailRequestSequence.current += 1
    hasLoaded.current = false
    hasLoadedMore.current = false
    updateItems([])
    updateSelectedCallId(null)
    updateNextCursor(null)
    setSelectedCall(null)
    setLoading(false)
    setRefreshing(false)
    setLoadingMore(false)
    setDetailLoading(false)
    setError(null)
    setDetailError(null)
    setLastUpdatedAt(null)
    void refresh()
    const interval = window.setInterval(() => void refresh(), pollIntervalMs)
    return () => {
      window.clearInterval(interval)
      requestSequence.current += 1
      detailRequestSequence.current += 1
    }
  }, [
    enabled,
    pollIntervalMs,
    queryKey,
    refresh,
    updateItems,
    updateNextCursor,
    updateSelectedCallId
  ])

  const selectedCallUpdatedAt = useMemo(
    () => items.find((item) => item.call_id === selectedCallId)?.updated_at_ms ?? null,
    [items, selectedCallId]
  )

  useEffect(() => {
    if (!enabled || !selectedCallId || selectedCallUpdatedAt === null) {
      setDetailLoading(false)
      return
    }

    const sequence = ++detailRequestSequence.current
    setDetailLoading(true)
    setDetailError(null)
    void window.advx.queryAiCall(selectedCallId).then(
      (trace) => {
        if (sequence !== detailRequestSequence.current) return
        setSelectedCall(trace)
        setDetailLoading(false)
      },
      (caught) => {
        if (sequence !== detailRequestSequence.current) return
        setSelectedCall(null)
        setDetailError(caught instanceof Error ? caught.message : 'AI 调用详情读取失败。')
        setDetailLoading(false)
      }
    )

    return () => {
      detailRequestSequence.current += 1
    }
  }, [enabled, selectedCallId, selectedCallUpdatedAt])

  const selectCall = useCallback((callId: string) => {
    if (callId === selectedCallIdRef.current) return
    setSelectedCall(null)
    setDetailError(null)
    updateSelectedCallId(callId)
  }, [updateSelectedCallId])

  return {
    items,
    selectedCall: selectedCall?.call_id === selectedCallId ? selectedCall : null,
    selectedCallId,
    loading,
    refreshing,
    loadingMore,
    detailLoading,
    error,
    detailError,
    hasMore: nextCursor !== null,
    lastUpdatedAt,
    selectCall,
    refresh,
    loadMore
  }
}
