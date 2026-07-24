import type { AiCallTrace } from '../../../../shared/backend-client'

export const aiCallRoleLabels = {
  legacy_director: '旧版导演记录',
  viewer: '观众',
  visual_summary: '视觉摘要',
  memory: '记忆',
  asr: '语音识别'
} as const

export const aiCallStatusLabels = {
  preparing: '准备中',
  sent: '已发送',
  streaming: '流式接收',
  received: '已接收',
  succeeded: '成功',
  failed: '失败',
  blocked: '已阻止',
  cancelled: '已取消',
  interrupted: '已中断'
} as const

export function formatJson(value: unknown): string {
  if (value === undefined || value === null) return '暂无数据'
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

export function formatTimestamp(timestampMs: number | null | undefined): string {
  if (timestampMs === null || timestampMs === undefined) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    fractionalSecondDigits: 3,
    hour12: false
  }).format(new Date(timestampMs))
}

export function formatDuration(durationMs: number | null | undefined): string {
  if (durationMs === null || durationMs === undefined) return '—'
  if (durationMs < 1_000) return `${durationMs} ms`
  return `${(durationMs / 1_000).toFixed(durationMs < 10_000 ? 2 : 1)} s`
}

export function retainSelectedCallId(
  items: readonly AiCallTrace[],
  selectedCallId: string | null
): string | null {
  if (selectedCallId && items.some((item) => item.call_id === selectedCallId)) {
    return selectedCallId
  }
  return items[0]?.call_id ?? null
}

export function collectCorrelationIds(trace: AiCallTrace): Array<{
  label: string
  value: string
}> {
  return [
    ['调用 ID', trace.call_id],
    ['关联 ID', trace.correlation_id],
    ['会话 ID', trace.session_id],
    ['房间 ID', trace.room_id],
    ['观众 Epoch', trace.audience_epoch === undefined || trace.audience_epoch === null
      ? null
      : String(trace.audience_epoch)],
    ['观察 ID', trace.observation_id],
    ['生成请求 ID', trace.generation_request_id],
    ['Viewer ID', trace.viewer_instance_id],
    ['话语 ID', trace.utterance_id],
    ['Provider 请求 ID', trace.response?.provider_request_id],
    ['请求 SHA-256', trace.request?.wire_sha256],
    ['响应 SHA-256', trace.response?.body_sha256]
  ].flatMap(([label, value]) => value ? [{ label: label as string, value }] : [])
}
