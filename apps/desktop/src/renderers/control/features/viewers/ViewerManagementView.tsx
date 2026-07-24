import { useEffect, useMemo, useState } from 'react'
import {
  Clock3,
  LoaderCircle,
  LogIn,
  RefreshCw,
  Search,
  ShieldAlert,
  UserMinus,
  Users,
  Volume2,
  VolumeX,
  X
} from 'lucide-react'
import type { BackendViewerSnapshot } from '../../../../shared/contracts'
import type { SessionStatus } from '../../../../shared/session'
import type { LiveAudienceProps } from '../live/liveTypes'
import {
  isViewerMuted,
  selectViewers,
  summarizeViewers,
  type ViewerFilter
} from './viewerManagement'
import './viewer-management.css'

export type ViewerManagementViewProps = LiveAudienceProps & {
  sessionStatus: SessionStatus
}

const filterLabels: Record<ViewerFilter, string> = {
  all: '全部',
  active: '在线',
  muted: '禁言',
  left: '暂离',
  kicked: '已踢出'
}

function formatTime(timestamp: number | null): string {
  if (timestamp === null) return '--'
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(timestamp)
}

function formatMuteRemaining(until: number | null, now: number): string {
  if (until === null || until <= now) return '--'
  const seconds = Math.ceil((until - now) / 1000)
  if (seconds < 60) return `${seconds} 秒`
  return `${Math.ceil(seconds / 60)} 分钟`
}

function viewerStateLabel(viewer: BackendViewerSnapshot, now: number): string {
  if (isViewerMuted(viewer, now)) return '禁言中'
  if (viewer.presence_state === 'active') return '在线'
  if (viewer.presence_state === 'left') return '暂时离开'
  if (viewer.presence_state === 'kicked') return '已踢出'
  return '已结束'
}

export function ViewerManagementView({
  sessionStatus,
  audience,
  audienceLoading,
  audienceError,
  operationError,
  pendingViewerId,
  onRetryAudience,
  onDismissOperationError,
  onMute,
  onUnmute,
  onKick
}: ViewerManagementViewProps): React.JSX.Element {
  const [filter, setFilter] = useState<ViewerFilter>('all')
  const [query, setQuery] = useState('')
  const [muteDuration, setMuteDuration] = useState('300000')
  const [customMuteMinutes, setCustomMuteMinutes] = useState(15)
  const [selectedViewerId, setSelectedViewerId] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now)
  const summary = useMemo(() => summarizeViewers(audience, now), [audience, now])
  const viewers = useMemo(
    () => selectViewers(audience, filter, query, now),
    [audience, filter, now, query]
  )
  const selectedViewer =
    viewers.find((viewer) => viewer.viewer_instance_id === selectedViewerId) ?? null
  const muteDurationMs =
    muteDuration === 'custom' ? customMuteMinutes * 60_000 : Number(muteDuration)
  const noticeMessage = audienceError
    ? `观众数据更新失败：${audienceError}`
    : operationError

  useEffect(() => {
    if (!selectedViewerId || !viewers.some(
      (viewer) => viewer.viewer_instance_id === selectedViewerId
    )) {
      setSelectedViewerId(viewers[0]?.viewer_instance_id ?? null)
    }
  }, [selectedViewerId, viewers])

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(interval)
  }, [])

  const confirmKick = async (viewer: BackendViewerSnapshot): Promise<void> => {
    if (!window.confirm(`确定将“${viewer.display_name}”踢出本场直播吗？该操作本场不可撤销。`)) {
      return
    }
    await onKick(viewer.viewer_instance_id)
  }

  if (sessionStatus === 'idle') {
    return (
      <section className="viewer-management viewer-management-empty">
        <span className="viewer-management-empty-icon"><Users size={28} /></span>
        <h2>本场暂无观众</h2>
        <p>开播后将生成本场新的 AI 观众，直播结束后观众列表会自动清空。</p>
      </section>
    )
  }

  if (!audience) {
    const hasError = audienceError !== null
    return (
      <section
        className="viewer-management viewer-management-empty"
        role={hasError ? 'alert' : 'status'}
        aria-busy={audienceLoading}
      >
        <span
          className={[
            'viewer-management-empty-icon',
            audienceLoading ? 'syncing' : ''
          ].join(' ')}
        >
          {audienceLoading
            ? <LoaderCircle size={28} aria-hidden="true" />
            : <Users size={28} aria-hidden="true" />}
        </span>
        <h2>
          {hasError
            ? '无法同步直播观众'
            : audienceLoading
              ? '正在同步直播观众'
              : '观众会话尚未启动'}
        </h2>
        <p>
          {audienceError ??
            (audienceLoading
              ? '正在读取服务端的本场观众快照。'
              : '直播观众准备完成后会自动显示在这里。')}
        </p>
        {hasError && (
          <button
            className="viewer-management-empty-action"
            type="button"
            disabled={audienceLoading}
            onClick={() => void onRetryAudience()}
          >
            <RefreshCw size={15} aria-hidden="true" />
            重新同步
          </button>
        )}
      </section>
    )
  }

  return (
    <section className="viewer-management">
      <header className="viewer-management-summary" aria-label="直播观众概览">
        <div><span>当前在线</span><strong>{summary.active}</strong></div>
        <div><span>禁言中</span><strong>{summary.muted}</strong></div>
        <div><span>暂时离开</span><strong>{summary.left}</strong></div>
        <div><span>本场观众</span><strong>{summary.total}</strong></div>
        <div className="viewer-management-target">
          <span>目标在线</span>
          <strong>{audience.target_concurrent_viewers}</strong>
        </div>
        {noticeMessage && (
          <div className="viewer-management-notice" role="alert">
            <span>{noticeMessage}</span>
            {audienceError ? (
              <button
                className="viewer-management-notice-action"
                type="button"
                disabled={audienceLoading}
                onClick={() => void onRetryAudience()}
              >
                <RefreshCw size={14} aria-hidden="true" />
                {audienceLoading ? '正在同步' : '重试'}
              </button>
            ) : (
              <button
                className="viewer-management-notice-dismiss"
                type="button"
                onClick={onDismissOperationError}
                title="关闭错误提示"
                aria-label="关闭错误提示"
              >
                <X size={14} aria-hidden="true" />
              </button>
            )}
          </div>
        )}
      </header>

      <div className="viewer-management-toolbar">
        <div className="viewer-management-filters" role="tablist" aria-label="观众状态筛选">
          {(Object.keys(filterLabels) as ViewerFilter[]).map((value) => (
            <button
              type="button"
              role="tab"
              aria-selected={filter === value}
              className={filter === value ? 'active' : ''}
              key={value}
              onClick={() => setFilter(value)}
            >
              {filterLabels[value]}
            </button>
          ))}
        </div>
        <label className="viewer-management-search">
          <Search size={15} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索用户名或人格"
            aria-label="搜索直播观众"
          />
        </label>
      </div>

      <div className="viewer-management-content">
        <div className="viewer-management-table-wrap">
          <table className="viewer-management-table">
            <thead>
              <tr>
                <th>观众</th>
                <th>人格</th>
                <th>状态</th>
                <th>加入时间</th>
                <th>重进</th>
                <th><span className="sr-only">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {viewers.map((viewer) => {
                const muted = isViewerMuted(viewer, now)
                const active = viewer.presence_state === 'active'
                const pending = pendingViewerId !== null
                return (
                  <tr
                    key={viewer.viewer_instance_id}
                    className={selectedViewerId === viewer.viewer_instance_id ? 'selected' : ''}
                    tabIndex={0}
                    aria-label={`查看观众详情：${viewer.display_name}`}
                    onClick={() => setSelectedViewerId(viewer.viewer_instance_id)}
                    onKeyDown={(event) => {
                      if (event.target !== event.currentTarget) return
                      if (event.key !== 'Enter' && event.key !== ' ') return
                      event.preventDefault()
                      setSelectedViewerId(viewer.viewer_instance_id)
                    }}
                  >
                    <td>
                      <span className="viewer-management-person">
                        <span className="viewer-management-avatar">{viewer.display_name.charAt(0)}</span>
                        <span><strong>{viewer.display_name}</strong><small>@{viewer.username}</small></span>
                      </span>
                    </td>
                    <td><span className="viewer-management-persona">{viewer.persona_display_name}</span></td>
                    <td>
                      <span className={`viewer-management-state ${muted ? 'muted' : viewer.presence_state}`}>
                        {viewerStateLabel(viewer, now)}
                      </span>
                    </td>
                    <td>{formatTime(viewer.joined_at_ms)}</td>
                    <td>{Math.max(0, viewer.join_count - 1)}</td>
                    <td>
                      <span className="viewer-management-actions" onClick={(event) => event.stopPropagation()}>
                        {active && (
                          <button
                            type="button"
                            disabled={pending}
                            title={muted ? '解除禁言' : '限时禁言'}
                            onClick={() => void (muted
                             ? onUnmute(viewer.viewer_instance_id)
                              : onMute(viewer.viewer_instance_id, muteDurationMs))}
                            aria-label={`${muted ? '解除禁言' : '限时禁言'}：${viewer.display_name}`}
                          >
                            {muted ? <Volume2 size={15} /> : <VolumeX size={15} />}
                          </button>
                        )}
                        {active && (
                          <button
                            type="button"
                            className="danger"
                            disabled={pending}
                            title="踢出本场直播"
                            aria-label={`踢出本场直播：${viewer.display_name}`}
                            onClick={() => void confirmKick(viewer)}
                          >
                            <UserMinus size={15} />
                          </button>
                        )}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {viewers.length === 0 && <p className="viewer-management-no-results">没有符合条件的观众</p>}
        </div>

        <aside className="viewer-management-detail" aria-label="观众详情">
          {selectedViewer ? (
            <>
              <div className="viewer-management-detail-heading">
                <span className="viewer-management-avatar large">{selectedViewer.display_name.charAt(0)}</span>
                <div><strong>{selectedViewer.display_name}</strong><span>@{selectedViewer.username}</span></div>
              </div>
              <dl>
                <div><dt>人格</dt><dd>{selectedViewer.persona_display_name}</dd></div>
                <div><dt>当前状态</dt><dd>{viewerStateLabel(selectedViewer, now)}</dd></div>
                <div><dt>首次加入</dt><dd>{formatTime(selectedViewer.joined_at_ms)}</dd></div>
                <div><dt>最近离开</dt><dd>{formatTime(selectedViewer.last_left_at_ms)}</dd></div>
                <div><dt>加入次数</dt><dd>{selectedViewer.join_count}</dd></div>
                <div><dt>禁言剩余</dt><dd>{formatMuteRemaining(selectedViewer.muted_until_ms, now)}</dd></div>
              </dl>
              {selectedViewer.presence_state === 'active' && (
                <div className="viewer-management-moderation">
                  <label>
                    <span>禁言时长</span>
                    <select value={muteDuration} onChange={(event) => setMuteDuration(event.target.value)}>
                      <option value="60000">1 分钟</option>
                      <option value="300000">5 分钟</option>
                      <option value="600000">10 分钟</option>
                      <option value="1800000">30 分钟</option>
                      <option value="custom">自定义</option>
                    </select>
                  </label>
                  {muteDuration === 'custom' && (
                    <label>
                      <span>分钟</span>
                      <input
                        type="number"
                        min={1}
                        max={60}
                        value={customMuteMinutes}
                        onChange={(event) => setCustomMuteMinutes(
                          Math.min(60, Math.max(1, Number(event.target.value) || 1))
                        )}
                      />
                    </label>
                  )}
                  <div className="viewer-management-detail-actions">
                    <button
                      type="button"
                      disabled={pendingViewerId !== null}
                      onClick={() => void (isViewerMuted(selectedViewer, now)
                        ? onUnmute(selectedViewer.viewer_instance_id)
                        : onMute(selectedViewer.viewer_instance_id, muteDurationMs))}
                    >
                      {isViewerMuted(selectedViewer, now) ? <Volume2 size={15} /> : <VolumeX size={15} />}
                      {isViewerMuted(selectedViewer, now) ? '解除禁言' : '限时禁言'}
                    </button>
                    <button
                      type="button"
                      className="danger"
                      disabled={pendingViewerId !== null}
                      onClick={() => void confirmKick(selectedViewer)}
                    >
                      <UserMinus size={15} />
                      踢出本场
                    </button>
                  </div>
                </div>
              )}
              <div className="viewer-management-facts">
                <span><LogIn size={14} />会话观众</span>
                <span><ShieldAlert size={14} />AI 身份</span>
                <span><Clock3 size={14} />仅本场有效</span>
              </div>
            </>
          ) : (
            <p className="viewer-management-detail-empty">选择一名观众查看详情</p>
          )}
        </aside>
      </div>
    </section>
  )
}
