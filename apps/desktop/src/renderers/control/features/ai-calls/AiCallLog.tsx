import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  Braces,
  Check,
  ChevronDown,
  Clock3,
  Copy,
  Image as ImageIcon,
  RefreshCw,
  Search,
  Unplug,
  Zap
} from 'lucide-react'
import { memo, useEffect, useMemo, useState } from 'react'
import type {
  AiCallListItem,
  AiCallQuery,
  AiCallRole,
  AiCallStatus,
  AiCallTrace
} from '../../../../shared/backend-client'
import { SelectDropdown } from '../../components/SelectDropdown'
import { useAiCallLog } from '../../hooks/useAiCallLog'
import {
  aiCallRoleLabels,
  aiCallStatusLabels,
  collectCorrelationIds,
  formatDuration,
  formatJson,
  formatScreenChangeScore,
  formatTimestamp,
  formatViewerOutputProgress,
  formatViewerSelectionReasons,
  formatViewerTriggerLabels,
  formatViewerTriggerReasons,
  formatViewerTriggerTarget
} from './aiCallFormatters'
import { collectAiCallImageReferences } from './aiCallImages'

export type AiCallLogProps = {
  active: boolean
  currentSessionId: string | null
}

type SessionScope = 'current' | 'all'

const roleOptions = Object.entries(aiCallRoleLabels) as Array<[AiCallRole, string]>
const statusOptions = Object.entries(aiCallStatusLabels) as Array<[AiCallStatus, string]>

const statusClasses: Record<AiCallStatus, string> = {
  preparing: 'bg-[var(--panel-raise)] text-[var(--text-dim)]',
  sent: 'bg-[var(--accent-soft)] text-[var(--accent)]',
  streaming: 'bg-[var(--accent-soft)] text-[var(--accent)]',
  received: 'bg-[var(--ok-soft)] text-[var(--ok)]',
  succeeded: 'bg-[var(--ok-soft)] text-[var(--ok)]',
  failed: 'bg-[var(--danger-soft)] text-[var(--danger-text)]',
  blocked: 'bg-[var(--amber-soft)] text-[var(--amber)]',
  cancelled: 'bg-[var(--panel-raise)] text-[var(--text-dim)]',
  interrupted: 'bg-[var(--amber-soft)] text-[var(--amber)]'
}

function Metric({
  label,
  value
}: {
  label: string
  value: string | number
}): React.JSX.Element {
  return (
    <div className="min-w-0 border-r border-[var(--border)] px-3 py-2 last:border-r-0">
      <span className="block text-[10px] font-bold uppercase text-[var(--text-faint)]">
        {label}
      </span>
      <strong className="mt-1 block truncate text-xs font-semibold text-[var(--text)]" title={String(value)}>
        {value}
      </strong>
    </div>
  )
}

function JsonBlock({ value }: { value: unknown }): React.JSX.Element {
  return (
    <pre className="m-0 max-h-72 min-h-24 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-[var(--bg-deep)] px-3 py-2.5 font-mono text-[11px] leading-5 text-[var(--text-dim)]">
      {formatJson(value)}
    </pre>
  )
}

function ImagePreview({
  previewId,
  label
}: {
  previewId: string | null
  label: string
}): React.JSX.Element {
  const [dataUrl, setDataUrl] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)

  useEffect(() => {
    let disposed = false
    setDataUrl(null)
    setUnavailable(false)
    if (!previewId) {
      setUnavailable(true)
      return () => {
        disposed = true
      }
    }
    void window.advx.queryAiCallImage(previewId).then(
      (preview) => {
        if (!disposed) setDataUrl(preview.data_url)
      },
      () => {
        if (!disposed) setUnavailable(true)
      }
    )
    return () => {
      disposed = true
    }
  }, [previewId])

  if (dataUrl) {
    return (
      <img
        className="size-full object-contain"
        src={dataUrl}
        alt={label}
        referrerPolicy="no-referrer"
      />
    )
  }

  return (
    <div className="grid size-full place-items-center px-2 text-center text-[10px] text-[var(--text-faint)]">
      {unavailable ? (previewId ? '预览已过期' : '旧记录未保留图片') : '加载图片'}
    </div>
  )
}

function viewerDecisionReason(value: unknown): string | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const response = value as Record<string, unknown>
  if (response.action !== 'barrage' && response.action !== 'silence') return null
  const reason = response.decision_reason
  return typeof reason === 'string' && reason.trim() ? reason.trim() : '模型未提供'
}

function ViewerTriggerContext({ trace }: { trace: AiCallTrace }): React.JSX.Element | null {
  if (trace.role !== 'viewer') return null
  const context = trace.trigger_context
  if (!context) {
    return (
      <section className="border-b border-[var(--border)]">
        <h2 className="flex items-center gap-2 px-4 py-3 text-xs font-bold">
          <Zap size={15} className="text-[var(--amber)]" aria-hidden="true" />
          本次触发
        </h2>
        <p className="m-0 border-t border-[var(--border)] px-4 py-3 text-xs text-[var(--text-faint)]">
          旧记录未保留触发上下文
        </p>
      </section>
    )
  }

  const triggerReasons = formatViewerTriggerReasons(context)
  const selectionReasons = formatViewerSelectionReasons(context)
  const triggerEventIds = context.trigger_event_ids ?? []
  const triggerFrameIds = context.trigger_frame_ids ?? []
  const hasScreenChangeScore = context.screen_change_score !== null && context.screen_change_score !== undefined
  const references = [
    { label: '触发事件', ids: triggerEventIds },
    { label: '触发画面', ids: triggerFrameIds }
  ].filter(({ ids }) => ids.length > 0)

  return (
    <section className="border-b border-[var(--border)]">
      <h2 className="flex items-center gap-2 px-4 py-3 text-xs font-bold">
        <Zap size={15} className="text-[var(--amber)]" aria-hidden="true" />
        本次触发
      </h2>
      <div className="grid grid-cols-3 border-y border-[var(--border)] max-[980px]:grid-cols-1">
        <Metric label="触发类型" value={formatViewerTriggerLabels(context.triggers)} />
        <Metric label="调度目标" value={formatViewerTriggerTarget(context)} />
        <Metric
          label={hasScreenChangeScore ? '画面变化' : '触发事件'}
          value={hasScreenChangeScore
            ? formatScreenChangeScore(context.screen_change_score)
            : triggerEventIds.length > 0 ? `${triggerEventIds.length} 条` : '无'}
        />
      </div>
      <div className="grid grid-cols-2 border-b border-[var(--border)] max-[980px]:grid-cols-1">
        <div className="border-r border-[var(--border)] px-4 py-3 max-[980px]:border-r-0 max-[980px]:border-b">
          <h3 className="m-0 text-[10px] font-bold uppercase text-[var(--text-faint)]">触发原因</h3>
          <ul className="mb-0 mt-2 list-none space-y-1 p-0 text-xs leading-5 text-[var(--text)]">
            {triggerReasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        </div>
        <div className="px-4 py-3">
          <h3 className="m-0 text-[10px] font-bold uppercase text-[var(--text-faint)]">观众调度</h3>
          <ul className="mb-0 mt-2 list-none space-y-1 p-0 text-xs leading-5 text-[var(--text)]">
            {selectionReasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        </div>
      </div>
      {references.length > 0 && (
        <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2 px-4 py-3 text-[11px]">
          {references.map(({ label, ids }) => (
            <div className="contents" key={label}>
              <dt className="text-[var(--text-faint)]">{label}</dt>
              <dd className="m-0 select-text break-all font-mono text-[var(--text-dim)]">
                {ids.join('、')}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  )
}

function ViewerOutputDelivery({ trace }: { trace: AiCallTrace }): React.JSX.Element | null {
  if (trace.role !== 'viewer' || !trace.viewer_output_delivery) return null
  const delivery = trace.viewer_output_delivery

  return (
    <section className="border-b border-[var(--border)]">
      <h2 className="flex items-center gap-2 px-4 py-3 text-xs font-bold">
        <ArrowUpFromLine size={15} className="text-[var(--ok)]" aria-hidden="true" />
        出站发布
      </h2>
      <div className="grid grid-cols-4 border-y border-[var(--border)] max-[980px]:grid-cols-2">
        <Metric label="模型就绪" value={formatTimestamp(delivery.ready_at_ms)} />
        <Metric label="进入队列" value={formatTimestamp(delivery.scheduled_at_ms)} />
        <Metric label="首条发布" value={formatTimestamp(delivery.published_at_ms)} />
        <Metric label="排队时长" value={formatDuration(delivery.queue_delay_ms)} />
      </div>
      <p className="m-0 px-4 py-2.5 text-xs text-[var(--text-dim)]">
        已发布 {formatViewerOutputProgress(delivery)}
        {delivery.interruption_reason ? `；中断原因：${delivery.interruption_reason}` : ''}
      </p>
    </section>
  )
}

function EmptyDetail(): React.JSX.Element {
  return (
    <div className="grid h-full min-h-64 place-items-center px-8 text-center">
      <div>
        <Braces className="mx-auto mb-3 text-[var(--text-faint)]" size={28} aria-hidden="true" />
        <strong className="block text-sm text-[var(--text)]">未选择 AI 调用</strong>
      </div>
    </div>
  )
}

function LoadingDetail(): React.JSX.Element {
  return (
    <div className="grid h-full min-h-64 place-items-center px-8 text-center text-xs text-[var(--text-dim)]">
      <div>
        <RefreshCw className="mx-auto mb-2 animate-spin" size={18} aria-hidden="true" />
        正在读取 AI 调用详情
      </div>
    </div>
  )
}

function DetailLoadError({ error }: { error: string }): React.JSX.Element {
  return (
    <div className="grid h-full min-h-64 place-items-center px-8 text-center" role="alert">
      <div>
        <Unplug className="mx-auto mb-2 text-[var(--danger)]" size={22} aria-hidden="true" />
        <strong className="block text-xs text-[var(--text)]">调用详情读取失败</strong>
        <p className="mb-0 mt-1 break-words text-[11px] leading-5 text-[var(--text-dim)]">{error}</p>
      </div>
    </div>
  )
}

const AiCallListRow = memo(function AiCallListRow({
  trace,
  selected,
  onSelect
}: {
  trace: AiCallListItem
  selected: boolean
  onSelect: (callId: string) => void
}): React.JSX.Element {
  const triggerLabel = trace.role === 'viewer' && trace.trigger_context
    ? formatViewerTriggerLabels(trace.trigger_context.triggers)
    : null

  return (
    <li className="border-b border-[var(--border)]">
      <button
        className={`grid w-full min-w-0 gap-2 px-3 py-3 text-left hover:bg-[var(--panel-raise)] ${selected ? 'bg-[var(--accent-soft)]' : 'bg-transparent'}`}
        type="button"
        aria-current={selected ? 'true' : undefined}
        onClick={() => onSelect(trace.call_id)}
      >
        <span className="flex min-w-0 items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5">
            <strong className="truncate text-xs">{aiCallRoleLabels[trace.role]}</strong>
            {triggerLabel && (
              <span
                className="max-w-32 truncate rounded-md bg-[var(--panel-raise)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--text-dim)]"
                title={triggerLabel}
              >
                {triggerLabel}
              </span>
            )}
          </span>
          <time className="shrink-0 text-[10px] tabular-nums text-[var(--text-faint)]">
            {formatTimestamp(trace.started_at_ms)}
          </time>
        </span>
        <span className="flex min-w-0 items-center gap-2">
          <span className={`shrink-0 rounded-lg px-1.5 py-0.5 text-[10px] font-bold ${statusClasses[trace.status]}`}>
            {aiCallStatusLabels[trace.status]}
          </span>
          <span className="truncate text-[11px] text-[var(--text-dim)]" title={trace.model_id}>
            {trace.model_id}
          </span>
          <span className="ml-auto shrink-0 text-[10px] tabular-nums text-[var(--text-faint)]">
            {formatDuration(trace.duration_ms)}
          </span>
        </span>
        <code className="truncate font-mono text-[10px] text-[var(--text-faint)]" title={trace.correlation_id}>
          {trace.correlation_id}
        </code>
      </button>
    </li>
  )
})

function CallDetail({ trace }: { trace: AiCallTrace }): React.JSX.Element {
  const correlations = collectCorrelationIds(trace)
  const timeline = trace.timeline ?? []
  const imageReferences = collectAiCallImageReferences(trace.request?.input_preview)
  const hasAvailableImagePreview = imageReferences.some((image) => image.previewId !== null)
  const decisionReason = viewerDecisionReason(trace.response?.parsed_output)
  const [copied, setCopied] = useState(false)

  async function copyTrace(): Promise<void> {
    try {
      await navigator.clipboard.writeText(JSON.stringify(trace, null, 2))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1_500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <article className="min-w-0 overflow-auto">
      <header className="border-b border-[var(--border)] px-4 py-3.5">
        <div className="flex min-w-0 items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <strong className="text-sm">{aiCallRoleLabels[trace.role]}</strong>
              <span className={`rounded-lg px-2 py-0.5 text-[10px] font-bold ${statusClasses[trace.status]}`}>
                {aiCallStatusLabels[trace.status]}
              </span>
            </div>
            <p className="mb-0 mt-1 break-all text-xs text-[var(--text-dim)]">
              {trace.provider} / {trace.model_id}
            </p>
            <p className="mb-0 mt-1 break-all font-mono text-[10px] text-[var(--text-faint)]">
              {trace.endpoint}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <time className="text-[11px] tabular-nums text-[var(--text-faint)]">
              {formatTimestamp(trace.started_at_ms)}
            </time>
            <button
              className="grid size-7 place-items-center rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-dim)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
              type="button"
              title={copied ? '已复制' : '复制调用详情'}
              aria-label={copied ? '已复制调用详情' : '复制调用详情'}
              onClick={() => void copyTrace()}
            >
              {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
            </button>
          </div>
        </div>
      </header>

      <div className="grid grid-cols-4 border-b border-[var(--border)] max-[980px]:grid-cols-2">
        <Metric label="HTTP" value={trace.response?.http_status ?? trace.error?.http_status ?? '—'} />
        <Metric label="耗时" value={formatDuration(trace.duration_ms)} />
        <Metric label="输入 Token" value={trace.response?.input_tokens ?? '—'} />
        <Metric label="输出 Token" value={trace.response?.output_tokens ?? '—'} />
      </div>

      <ViewerTriggerContext trace={trace} />
      <ViewerOutputDelivery trace={trace} />

      {trace.error && (
        <section className="border-b border-[var(--danger-border)] bg-[var(--danger-soft)] px-4 py-3" role="alert">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="mt-0.5 shrink-0 text-[var(--danger)]" size={16} aria-hidden="true" />
            <div className="min-w-0">
              <strong className="block break-all text-xs text-[var(--danger-text)]">
                {trace.error.code}
              </strong>
              <p className="mb-0 mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-[var(--text-dim)]">
                {trace.error.message}
              </p>
              <span className="mt-1 block text-[10px] text-[var(--text-faint)]">
                {trace.error.retryable ? '可重试' : '不可重试'}
              </span>
            </div>
          </div>
        </section>
      )}

      <section className="border-b border-[var(--border)]">
        <h2 className="flex items-center gap-2 px-4 py-3 text-xs font-bold">
          <ArrowUpFromLine size={15} className="text-[var(--accent)]" aria-hidden="true" />
          发送摘要
        </h2>
        <div className="grid grid-cols-3 border-y border-[var(--border)] max-[980px]:grid-cols-1">
          <Metric label="Schema" value={trace.request?.schema_name ?? '—'} />
          <Metric label="请求字节" value={trace.request?.wire_bytes ?? '—'} />
          <Metric label="最大输出 Token" value={trace.request?.max_output_tokens ?? '—'} />
        </div>
        {imageReferences.length > 0 && (
          <div className="border-b border-[var(--border)]">
            <h3 className="m-0 flex items-center gap-2 px-4 py-2 text-[11px] font-bold text-[var(--text-dim)]">
              <ImageIcon size={14} className="text-[var(--accent)]" aria-hidden="true" />
              发送图片
            </h3>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(144px,1fr))] gap-px border-t border-[var(--border)] bg-[var(--border)]">
              {imageReferences.map((image, index) => {
                const label = `发送给模型的图片 ${index + 1}`
                return (
                  <figure
                    className="m-0 min-w-0 bg-[var(--panel)] p-2"
                    key={`${image.previewId ?? image.sha256 ?? 'image'}-${index}`}
                  >
                    <div className="grid aspect-video overflow-hidden rounded-md bg-[var(--bg-deep)]">
                      <ImagePreview previewId={image.previewId} label={label} />
                    </div>
                    <figcaption className="mt-1.5 truncate text-[10px] text-[var(--text-faint)]" title={image.sha256 ?? undefined}>
                      {label}{image.mimeType ? ` · ${image.mimeType}` : ''}
                    </figcaption>
                  </figure>
                )
              })}
            </div>
          </div>
        )}
        <JsonBlock value={trace.request?.input_preview} />
        {trace.request?.redacted_fields && trace.request.redacted_fields.length > 0 && (
          <p className="m-0 border-t border-[var(--border)] px-4 py-2 text-[10px] text-[var(--text-faint)]">
            已保护字段：{trace.request.redacted_fields.join('、')}。{hasAvailableImagePreview
              ? '图片内容已在上方显示，原始地址不会写入调用记录。'
              : '旧记录未保留图片内容；新调用会在上方显示图片预览。'}
          </p>
        )}
      </section>

      <section className="border-b border-[var(--border)]">
        <h2 className="flex items-center gap-2 px-4 py-3 text-xs font-bold">
          <ArrowDownToLine size={15} className="text-[var(--ok)]" aria-hidden="true" />
          接收与解析结果
        </h2>
        <div className="grid grid-cols-3 border-y border-[var(--border)] max-[980px]:grid-cols-1">
          <Metric label="结束原因" value={trace.response?.finish_reason ?? '—'} />
          <Metric label="响应字节" value={trace.response?.body_bytes ?? '—'} />
          <Metric label="总 Token" value={trace.response?.total_tokens ?? '—'} />
        </div>
        {decisionReason && (
          <div className="border-b border-[var(--border)] px-4 py-3">
            <span className="block text-[10px] font-bold uppercase text-[var(--text-faint)]">
              决策原因
            </span>
            <p className="mb-0 mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-[var(--text)]">
              {decisionReason}
            </p>
          </div>
        )}
        <h3 className="m-0 border-b border-[var(--border)] px-4 py-2 text-[11px] font-bold text-[var(--text-dim)]">
          模型原始内容
        </h3>
        <JsonBlock value={trace.response?.model_output} />
        <h3 className="m-0 border-y border-[var(--border)] px-4 py-2 text-[11px] font-bold text-[var(--text-dim)]">
          解析结果
        </h3>
        <JsonBlock value={trace.response?.parsed_output} />
      </section>

      <section className="border-b border-[var(--border)] px-4 py-3">
        <h2 className="mb-3 text-xs font-bold">Correlation IDs 与完整性</h2>
        <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2 text-[11px]">
          {correlations.map(({ label, value }) => (
            <div className="contents" key={label}>
              <dt className="text-[var(--text-faint)]">{label}</dt>
              <dd className="m-0 select-text break-all font-mono text-[var(--text-dim)]">{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section>
        <h2 className="flex items-center gap-2 px-4 py-3 text-xs font-bold">
          <Clock3 size={15} className="text-[var(--text-faint)]" aria-hidden="true" />
          完整 Timeline
        </h2>
        {timeline.length === 0 ? (
          <p className="m-0 border-t border-[var(--border)] px-4 py-5 text-xs text-[var(--text-faint)]">
            暂无时间线事件
          </p>
        ) : (
          <ol className="m-0 list-none border-t border-[var(--border)] p-0">
            {timeline.map((event, index) => (
              <li
                className="grid grid-cols-[92px_120px_minmax(0,1fr)] gap-3 border-b border-[var(--border)] px-4 py-2.5 text-[11px] last:border-b-0 max-[980px]:grid-cols-[88px_minmax(0,1fr)]"
                key={`${event.at_ms}-${event.stage}-${index}`}
              >
                <time className="tabular-nums text-[var(--text-faint)]">
                  +{formatDuration(event.at_ms - trace.started_at_ms)}
                </time>
                <strong className="break-all text-[var(--text)]">{event.stage}</strong>
                <code className="min-w-0 whitespace-pre-wrap break-all font-mono text-[var(--text-dim)] max-[980px]:col-span-2">
                  {formatJson(event.detail)}
                </code>
              </li>
            ))}
          </ol>
        )}
      </section>
    </article>
  )
}

export function AiCallLog({ active, currentSessionId }: AiCallLogProps): React.JSX.Element {
  const [scope, setScope] = useState<SessionScope>(currentSessionId ? 'current' : 'all')
  const [role, setRole] = useState<AiCallRole | ''>('')
  const [status, setStatus] = useState<AiCallStatus | ''>('')
  const [search, setSearch] = useState('')

  useEffect(() => {
    if (!currentSessionId && scope === 'current') setScope('all')
  }, [currentSessionId, scope])

  const query = useMemo<AiCallQuery>(() => ({
    sessionId: scope === 'current' ? currentSessionId ?? undefined : undefined,
    role: role || undefined,
    status: status || undefined,
    correlationId: search.trim() || undefined,
    limit: 50
  }), [currentSessionId, role, scope, search, status])
  const log = useAiCallLog({ enabled: active, query })

  return (
    <section className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)] overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)]">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-3 py-2.5">
        <div className="flex h-8 items-center rounded-lg border border-[var(--border)] bg-[var(--bg)] p-0.5">
          <button
            className={`h-6.5 rounded-lg px-2.5 text-[11px] font-bold ${scope === 'current' ? 'bg-[var(--accent-soft)] text-[var(--accent)]' : 'text-[var(--text-dim)]'}`}
            type="button"
            disabled={!currentSessionId}
            onClick={() => setScope('current')}
          >
            当前会话
          </button>
          <button
            className={`h-6.5 rounded-lg px-2.5 text-[11px] font-bold ${scope === 'all' ? 'bg-[var(--accent-soft)] text-[var(--accent)]' : 'text-[var(--text-dim)]'}`}
            type="button"
            onClick={() => setScope('all')}
          >
            全部
          </button>
        </div>
        <SelectDropdown<AiCallRole | ''>
          className="min-w-28"
          triggerClassName="h-8 rounded-lg border-[var(--border)] bg-[var(--bg)] text-xs"
          ariaLabel="筛选 AI 角色"
          value={role}
          options={[
            { value: '', label: '全部角色' },
            ...roleOptions.map(([optionValue, label]) => ({ value: optionValue, label }))
          ]}
          onChange={setRole}
        />
        <SelectDropdown<AiCallStatus | ''>
          className="min-w-28"
          triggerClassName="h-8 rounded-lg border-[var(--border)] bg-[var(--bg)] text-xs"
          ariaLabel="筛选调用状态"
          value={status}
          options={[
            { value: '', label: '全部状态' },
            ...statusOptions.map(([optionValue, label]) => ({ value: optionValue, label }))
          ]}
          onChange={setStatus}
        />
        <label className="flex h-8 min-w-44 flex-1 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-2 focus-within:border-[var(--accent)]">
          <Search size={14} className="shrink-0 text-[var(--text-faint)]" aria-hidden="true" />
          <input
            className="min-w-0 flex-1 border-0 bg-transparent text-xs text-[var(--text)] outline-none placeholder:text-[var(--text-faint)]"
            value={search}
            placeholder="搜索 correlation ID"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <span className="text-[10px] tabular-nums text-[var(--text-faint)]">
          {log.lastUpdatedAt ? `更新 ${formatTimestamp(log.lastUpdatedAt)}` : '尚未更新'}
        </span>
        <button
          className="grid size-8 place-items-center rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-dim)] hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:opacity-50"
          type="button"
          title="刷新 AI 调用"
          aria-label="刷新 AI 调用"
          disabled={log.refreshing}
          onClick={() => void log.refresh()}
        >
          <RefreshCw size={15} className={log.refreshing ? 'animate-spin' : ''} aria-hidden="true" />
        </button>
      </div>

      <div className="grid min-h-0 grid-cols-[minmax(260px,34%)_minmax(0,1fr)] max-[840px]:grid-cols-1 max-[840px]:grid-rows-[minmax(210px,38%)_minmax(0,1fr)]">
        <aside className="min-h-0 overflow-auto border-r border-[var(--border)] max-[840px]:border-b max-[840px]:border-r-0">
          {log.loading ? (
            <div className="grid h-40 place-items-center text-xs text-[var(--text-dim)]">
              <RefreshCw className="mb-2 animate-spin" size={18} aria-hidden="true" />
              正在读取 AI 调用
            </div>
          ) : log.error && log.items.length === 0 ? (
            <div className="grid h-48 place-items-center px-6 text-center" role="alert">
              <div>
                <Unplug className="mx-auto mb-2 text-[var(--danger)]" size={22} aria-hidden="true" />
                <strong className="block text-xs text-[var(--text)]">调用日志读取失败</strong>
                <p className="mb-0 mt-1 break-words text-[11px] leading-5 text-[var(--text-dim)]">{log.error}</p>
              </div>
            </div>
          ) : log.items.length === 0 ? (
            <div className="grid h-48 place-items-center px-6 text-center">
              <div>
                <Braces className="mx-auto mb-2 text-[var(--text-faint)]" size={22} aria-hidden="true" />
                <strong className="block text-xs text-[var(--text)]">没有匹配的 AI 调用</strong>
              </div>
            </div>
          ) : (
            <>
              <ol className="m-0 list-none p-0">
                {log.items.map((trace) => (
                  <AiCallListRow
                    key={trace.call_id}
                    trace={trace}
                    selected={trace.call_id === log.selectedCallId}
                    onSelect={log.selectCall}
                  />
                ))}
              </ol>
              {log.hasMore && (
                <div className="border-t border-[var(--border)] p-2">
                  <button
                    className="flex h-8 w-full items-center justify-center gap-2 rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 text-[11px] font-bold text-[var(--text-dim)] hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:cursor-wait disabled:opacity-50"
                    type="button"
                    disabled={log.loadingMore || log.refreshing}
                    onClick={() => void log.loadMore()}
                  >
                    {log.loadingMore ? (
                      <RefreshCw className="animate-spin" size={14} aria-hidden="true" />
                    ) : (
                      <ChevronDown size={14} aria-hidden="true" />
                    )}
                    加载更多
                  </button>
                </div>
              )}
            </>
          )}
          {log.error && log.items.length > 0 && (
            <div className="sticky bottom-0 border-t border-[var(--danger-border)] bg-[var(--danger-soft)] px-3 py-2 text-[10px] text-[var(--danger-text)]" role="alert">
              自动刷新失败：{log.error}
            </div>
          )}
        </aside>
        {log.selectedCall ? (
          <CallDetail trace={log.selectedCall} />
        ) : log.detailError ? (
          <DetailLoadError error={log.detailError} />
        ) : log.selectedCallId ? (
          <LoadingDetail />
        ) : (
          <EmptyDetail />
        )}
      </div>
    </section>
  )
}
