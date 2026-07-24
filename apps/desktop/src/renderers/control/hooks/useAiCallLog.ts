import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  AiCallQuery,
  AiCallTrace
} from '../../../shared/backend-client'
import { retainSelectedCallId } from '../features/ai-calls/aiCallFormatters'
import { queryAllAiCalls } from '../features/ai-calls/aiCallQuery'

export type UseAiCallLogOptions = {
  enabled: boolean
  query: AiCallQuery
  pollIntervalMs?: number
}

export function useAiCallLog({
  enabled,
  query,
  pollIntervalMs = 1_500
}: UseAiCallLogOptions): {
  items: AiCallTrace[]
  selectedCall: AiCallTrace | null
  selectedCallId: string | null
  loading: boolean
  refreshing: boolean
  error: string | null
  lastUpdatedAt: number | null
  selectCall: (callId: string) => void
  refresh: () => Promise<void>
} {
  const [items, setItems] = useState<AiCallTrace[]>([])
  const [selectedCallId, setSelectedCallId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null)
  const requestSequence = useRef(0)
  const hasLoaded = useRef(false)
  const inFlight = useRef(false)
  const queryKey = JSON.stringify(query)

  const refresh = useCallback(async (): Promise<void> => {
    if (!enabled || inFlight.current) return
    inFlight.current = true
    const sequence = ++requestSequence.current
    setRefreshing(true)
    setLoading(!hasLoaded.current)
    try {
      const items = await queryAllAiCalls(
        query,
        (pageQuery) => window.advx.queryAiCalls(pageQuery)
      )
      if (sequence !== requestSequence.current) return
      setItems(items)
      setSelectedCallId((current) => retainSelectedCallId(items, current))
      setError(null)
      setLastUpdatedAt(Date.now())
      hasLoaded.current = true
    } catch (caught) {
      if (sequence !== requestSequence.current) return
      setError(caught instanceof Error ? caught.message : 'AI 调用日志读取失败。')
    } finally {
      inFlight.current = false
      if (sequence === requestSequence.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [enabled, queryKey])

  useEffect(() => {
    if (!enabled) return
    void refresh()
    const interval = window.setInterval(() => void refresh(), pollIntervalMs)
    return () => {
      window.clearInterval(interval)
      requestSequence.current += 1
    }
  }, [enabled, pollIntervalMs, refresh])

  const selectedCall = useMemo(
    () => items.find((item) => item.call_id === selectedCallId) ?? null,
    [items, selectedCallId]
  )

  return {
    items,
    selectedCall,
    selectedCallId,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    selectCall: setSelectedCallId,
    refresh
  }
}
