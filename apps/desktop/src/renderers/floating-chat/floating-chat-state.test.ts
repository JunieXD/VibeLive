import { describe, expect, it } from 'vitest'
import type { BarrageEvent } from '../../shared/contracts'
import {
  appendAudienceMessage,
  appendHostMessage,
  MAX_FLOATING_CHAT_MESSAGES,
  messagesPerMinute,
  uniqueAudienceCount
} from './floating-chat-state'

function barrage(index: number, audienceId = `audience-${index}`): BarrageEvent {
  return {
    barrageId: `barrage-${index}`,
    audienceId,
    audienceName: `观众 ${index}`,
    text: `消息 ${index}`,
    color: '#61c5df',
    createdAt: index
  }
}

describe('floating chat state', () => {
  it('deduplicates barrage events and retains the newest bounded history', () => {
    let messages = appendAudienceMessage([], barrage(0))
    messages = appendAudienceMessage(messages, barrage(0))
    expect(messages).toHaveLength(1)

    for (let index = 1; index <= MAX_FLOATING_CHAT_MESSAGES; index += 1) {
      messages = appendAudienceMessage(messages, barrage(index))
    }

    expect(messages).toHaveLength(MAX_FLOATING_CHAT_MESSAGES)
    expect(messages[0]?.id).toBe('barrage-1')
    expect(messages.at(-1)?.id).toBe(`barrage-${MAX_FLOATING_CHAT_MESSAGES}`)
  })

  it('tracks unique audience members independently from host messages', () => {
    let messages = appendAudienceMessage([], barrage(1, 'same-audience'))
    messages = appendAudienceMessage(messages, barrage(2, 'same-audience'))
    messages = appendAudienceMessage(messages, barrage(3, 'another-audience'))
    messages = appendHostMessage(messages, '大家好', 4, 'host-1')

    expect(uniqueAudienceCount(messages)).toBe(2)
  })

  it('calculates the current one-minute message rate', () => {
    let messages = appendAudienceMessage([], { ...barrage(1), createdAt: 39_999 })
    messages = appendAudienceMessage(messages, { ...barrage(2), createdAt: 40_000 })
    messages = appendHostMessage(messages, '刚刚发送', 99_000, 'host-1')

    expect(messagesPerMinute(messages, 100_000)).toBe(2)
  })
})
