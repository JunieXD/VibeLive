import { describe, expect, it } from 'vitest'
import type { AiCallTrace } from '../../../../shared/backend-client'
import {
  collectCorrelationIds,
  formatDuration,
  formatJson,
  retainSelectedCallId
} from './aiCallFormatters'

const trace = {
  call_id: 'call-1',
  correlation_id: 'corr-1',
  role: 'viewer',
  status: 'succeeded',
  provider: 'openai-compatible',
  model_id: 'viewer-model',
  endpoint: '/chat/completions',
  session_id: 'session-1',
  started_at_ms: 10,
  updated_at_ms: 20,
  timeline: [],
  redacted: true
} satisfies AiCallTrace

describe('AI call formatters', () => {
  it('retains the current selection across polling updates', () => {
    const next = [{ ...trace, call_id: 'call-2' }, trace]
    expect(retainSelectedCallId(next, 'call-1')).toBe('call-1')
    expect(retainSelectedCallId(next, 'missing')).toBe('call-2')
    expect(retainSelectedCallId([], 'call-1')).toBeNull()
  })

  it('formats payloads and durations for operator-readable output', () => {
    expect(formatJson({ prompt: 'hello' })).toBe('{\n  "prompt": "hello"\n}')
    expect(formatJson(null)).toBe('暂无数据')
    expect(formatDuration(842)).toBe('842 ms')
    expect(formatDuration(1_250)).toBe('1.25 s')
  })

  it('collects the complete available correlation chain', () => {
    expect(collectCorrelationIds({
      ...trace,
      response: { provider_request_id: 'provider-1' }
    })).toEqual([
      { label: '调用 ID', value: 'call-1' },
      { label: '关联 ID', value: 'corr-1' },
      { label: '会话 ID', value: 'session-1' },
      { label: '供应商请求 ID', value: 'provider-1' }
    ])
  })
})
