import type { BarrageEvent } from '../../shared/contracts'

export const MAX_FLOATING_CHAT_MESSAGES = 300

export type FloatingChatMessage = {
  id: string
  audienceId: string | null
  author: string
  text: string
  color: string | null
  createdAt: number
  source: 'audience' | 'host'
}

export function appendAudienceMessage(
  messages: readonly FloatingChatMessage[],
  event: BarrageEvent
): FloatingChatMessage[] {
  if (messages.some((message) => message.id === event.barrageId)) {
    return [...messages]
  }

  return [
    ...messages,
    {
      id: event.barrageId,
      audienceId: event.audienceId,
      author: event.audienceName?.trim() || '匿名观众',
      text: event.text,
      color: event.color ?? null,
      createdAt: event.createdAt,
      source: 'audience' as const
    }
  ].slice(-MAX_FLOATING_CHAT_MESSAGES)
}

export function appendHostMessage(
  messages: readonly FloatingChatMessage[],
  text: string,
  createdAt: number,
  id: string
): FloatingChatMessage[] {
  return [
    ...messages,
    {
      id,
      audienceId: null,
      author: '主播',
      text,
      color: '#ff6f9f',
      createdAt,
      source: 'host' as const
    }
  ].slice(-MAX_FLOATING_CHAT_MESSAGES)
}

export function uniqueAudienceCount(
  messages: readonly FloatingChatMessage[]
): number {
  return new Set(
    messages
      .filter((message) => message.source === 'audience')
      .map((message) => message.audienceId)
      .filter((audienceId): audienceId is string => audienceId !== null)
  ).size
}

export function messagesPerMinute(
  messages: readonly FloatingChatMessage[],
  now: number
): number {
  return messages.filter((message) => message.createdAt >= now - 60_000).length
}
