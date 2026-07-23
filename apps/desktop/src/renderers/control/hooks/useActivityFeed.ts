import { useCallback, useEffect, useRef, useState } from 'react'

export type ActivityItem = {
  id: string
  source: 'user' | 'audience' | 'system'
  author: string
  text: string
  color?: string
}

type AudienceActivity = Omit<ActivityItem, 'source'>

export function useActivityFeed() {
  const [activity, setActivity] = useState<ActivityItem[]>([
    {
      id: 'system-ready',
      source: 'system',
      author: '系统',
      text: '控制台已就绪，正在连接本地后端。'
    }
  ])
  const [message, setMessage] = useState('')
  const chatListRef = useRef<HTMLDivElement>(null)

  const appendSystemActivity = useCallback((text: string): void => {
    setActivity((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        source: 'system',
        author: '系统',
        text
      }
    ])
  }, [])

  const appendAudienceActivity = useCallback((item: AudienceActivity): void => {
    setActivity((current) => [...current.slice(-40), { ...item, source: 'audience' }])
  }, [])

  const appendUserActivity = useCallback((text: string): void => {
    setActivity((current) => [
      ...current.slice(-40),
      {
        id: crypto.randomUUID(),
        source: 'user',
        author: '你',
        text
      }
    ])
  }, [])

  const clearAudienceActivity = useCallback((): void => {
    setActivity((current) => current.filter((item) => item.source !== 'audience'))
  }, [])

  useEffect(() => {
    const list = chatListRef.current
    if (list) list.scrollTop = list.scrollHeight
  }, [activity])

  return {
    activity,
    message,
    setMessage,
    chatListRef,
    appendSystemActivity,
    appendAudienceActivity,
    appendUserActivity,
    clearAudienceActivity
  }
}
