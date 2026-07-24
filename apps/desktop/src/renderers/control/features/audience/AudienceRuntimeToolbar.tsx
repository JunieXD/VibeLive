import { Bug, Check, MoreHorizontal, RefreshCcw, RefreshCw, RotateCcw, TestTube2 } from 'lucide-react'
import type {
  DebugTraceSummary,
  ProviderProbeResult,
  RuntimeQuerySnapshot
} from '../../../../shared/backend-client'
import { Popover } from './Popover'
import { cx } from './styles'

export type AudienceRuntimeToolbarProps = {
  runtime: RuntimeQuerySnapshot | null
  autoApply: boolean
  pending: boolean
  canApply: boolean
  applying: boolean
  rollingBack: boolean
  recovering: boolean
  recoverableSessionId: string | null
  canRecover: boolean
  probing: boolean
  canProbe: boolean
  loadingTraces: boolean
  probe: ProviderProbeResult | null
  probeError: string | null
  traces: readonly DebugTraceSummary[]
  issue: string | null
  onAutoApplyChange(enabled: boolean): void
  onApply(): void
  onRollback(): void
  onRecover(): void
  onProbe(): void
  onLoadTraces(): void
}

export function AudienceRuntimeToolbar({
  runtime,
  autoApply,
  pending,
  canApply,
  applying,
  rollingBack,
  recovering,
  recoverableSessionId,
  canRecover,
  probing,
  canProbe,
  loadingTraces,
  probe,
  probeError,
  traces,
  issue,
  onAutoApplyChange,
  onApply,
  onRollback,
  onRecover,
  onProbe,
  onLoadTraces
}: AudienceRuntimeToolbarProps): React.JSX.Element {
  const latestTrace = traces[0]
  const probeLabel = probing
    ? 'Probe 检测中'
    : probe
      ? `Probe ${probe.status}`
      : probeError
        ? 'Probe 失败'
        : 'Probe'
  return (
    <div className={cx('aw-runtime-toolbar')} data-audience-runtime>
      <div className={cx('aw-runtime-state')}>
        <strong>{runtime ? `Runtime r${runtime.config_revision}` : 'Runtime 未启动'}</strong>
        {runtime && (
          <span>
            epoch {runtime.audience_epoch} · {runtime.viewers.length} Viewer ·{' '}
            {runtime.config_hash.slice(0, 8)}
          </span>
        )}
        {pending && runtime && <em>本地修改待应用</em>}
        {recoverableSessionId && !runtime && (
          <em>发现可恢复会话 {recoverableSessionId.slice(0, 8)}</em>
        )}
        {issue && <em className={cx('is-error')}>{issue}</em>}
      </div>
      <label className={cx('aw-runtime-toggle')}>
        <input
          type="checkbox"
          checked={autoApply}
          onChange={(event) => onAutoApplyChange(event.target.checked)}
        />
        <span>保存后自动应用</span>
      </label>
      <div className={cx('aw-runtime-actions')}>
        {recoverableSessionId && !runtime && (
          <button type="button" disabled={!canRecover || recovering} onClick={onRecover}>
            <RefreshCcw className={recovering ? cx('is-spinning') : undefined} size={14} />
            {recovering ? '恢复中' : '恢复会话'}
          </button>
        )}
        <button type="button" disabled={!canApply || applying} onClick={onApply}>
          {applying ? <RefreshCw className={cx('is-spinning')} size={14} /> : <Check size={14} />}
          {applying ? '应用中' : '应用到当前会话'}
        </button>
        <Popover title="更多操作" iconTrigger trigger={<MoreHorizontal size={15} />} panelWidth={220}>
          <div className={cx('aw-menu')}>
            <button
              type="button"
              className={cx('aw-menu-item')}
              data-popover-close
              disabled={!runtime || runtime.config_revision <= 1 || rollingBack}
              onClick={onRollback}
            >
              <RotateCcw size={14} />
              回滚
              <span>{runtime && runtime.config_revision > 1 ? `r${runtime.config_revision - 1}` : ''}</span>
            </button>
            <button
              type="button"
              className={cx('aw-menu-item')}
              data-popover-close
              disabled={probing || !canProbe}
              title={canProbe ? '检测当前会话的 Provider 能力' : '开始或恢复直播后可检测 Provider'}
              onClick={onProbe}
            >
              <TestTube2 size={14} />
              Provider 检测
              <span>{probeLabel}</span>
            </button>
            <button
              type="button"
              className={cx('aw-menu-item')}
              data-popover-close
              disabled={!runtime || loadingTraces}
              onClick={onLoadTraces}
            >
              <Bug size={14} />
              调试 Trace
              <span>{latestTrace ? latestTrace.trace_id.slice(0, 8) : ''}</span>
            </button>
          </div>
        </Popover>
      </div>
    </div>
  )
}
