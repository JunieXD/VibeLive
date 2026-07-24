import { describe, expect, it } from 'vitest'
import type {
  AiCallQuery,
  AiCallQueryResponse,
  AiCallTrace
} from '../../../../shared/backend-client'
import { queryAllAiCalls } from './aiCallQuery'

function trace(callId: string): AiCallTrace {
  return {
    call_id: callId,
    correlation_id: `correlation-${callId}`,
    role: 'viewer',
    status: 'succeeded',
    provider: 'test',
    model_id: 'viewer-model',
    endpoint: '/chat/completions',
    started_at_ms: 10,
    updated_at_ms: 20,
    timeline: [],
    redacted: true
  }
}

describe('AI call pagination', () => {
  it('loads every retained page and removes overlapping call IDs', async () => {
    const queries: AiCallQuery[] = []
    const pages: Record<string, AiCallQueryResponse> = {
      first: {
        items: [trace('call-3'), trace('call-2')],
        next_cursor: 'next',
        metadata: {}
      },
      next: {
        items: [trace('call-2'), trace('call-1')],
        next_cursor: null,
        metadata: {}
      }
    }

    const items = await queryAllAiCalls(
      { sessionId: 'session-1', limit: 250 },
      async (query) => {
        queries.push(query)
        return pages[query.cursor ?? 'first']
      }
    )

    expect(items.map((item) => item.call_id)).toEqual([
      'call-3',
      'call-2',
      'call-1'
    ])
    expect(queries).toEqual([
      { sessionId: 'session-1', limit: 250, cursor: undefined },
      { sessionId: 'session-1', limit: 250, cursor: 'next' }
    ])
  })

  it('fails instead of looping on a repeated cursor', async () => {
    await expect(
      queryAllAiCalls({}, async () => ({
        items: [],
        next_cursor: 'same',
        metadata: {}
      }))
    ).rejects.toThrow('分页游标重复')
  })
})
