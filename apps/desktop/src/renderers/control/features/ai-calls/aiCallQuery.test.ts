import { describe, expect, it } from 'vitest'
import type { AiCallListItem } from '../../../../shared/backend-client'
import { appendAiCallPage, mergeAiCallFirstPage } from './aiCallQuery'

function item(callId: string, updatedAtMs: number): AiCallListItem {
  return {
    call_id: callId,
    correlation_id: `correlation-${callId}`,
    role: 'viewer',
    status: 'succeeded',
    model_id: 'model',
    started_at_ms: updatedAtMs,
    updated_at_ms: updatedAtMs,
    duration_ms: 10
  }
}

describe('AI call pagination', () => {
  it('replaces refreshed first-page items without discarding loaded history', () => {
    const current = [item('call-2', 2), item('call-1', 1)]
    const refreshed = [item('call-3', 3), item('call-2', 20)]

    expect(mergeAiCallFirstPage(current, refreshed)).toEqual([
      item('call-3', 3),
      item('call-2', 20),
      item('call-1', 1)
    ])
  })

  it('appends a later page once even when its boundary overlaps', () => {
    const current = [item('call-3', 3), item('call-2', 2)]

    expect(appendAiCallPage(current, [item('call-2', 20), item('call-1', 1)])).toEqual([
      item('call-3', 3),
      item('call-2', 2),
      item('call-1', 1)
    ])
  })
})
