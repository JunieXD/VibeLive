import { useMemo, useState } from 'react'
import { ArrowRight, RefreshCw, UserMinus, Volume2, VolumeX, X } from 'lucide-react'
import type { LiveAudienceProps } from './liveTypes'

type Filter = 'active' | 'muted' | 'all'

export function LiveAudience({
  audience,
  audienceLoading,
  audienceError,
  operationError,
  pendingViewerId,
  onViewAll,
  onRetryAudience,
  onDismissOperationError,
  onMute,
  onUnmute,
  onKick
}: LiveAudienceProps): React.JSX.Element {
  const [filter, setFilter] = useState<Filter>('active')
  const [muteDurationMs, setMuteDurationMs] = useState(60_000)
  const now = Date.now()
  const viewers = useMemo(() => {
    const items = audience?.viewers ?? []
    if (filter === 'muted') {
      return items.filter((viewer) => (viewer.muted_until_ms ?? 0) > now)
    }
    if (filter === 'active') {
      return items.filter((viewer) => viewer.presence_state === 'active')
    }
    return items.filter(
      (viewer) =>
        viewer.joined_at_ms !== null &&
        viewer.presence_state !== 'removed' &&
        viewer.presence_state !== 'ended'
    )
  }, [audience, filter, now])
  const notice = audienceError ?? operationError
  const emptyMessage = audience
    ? '暂无观众'
    : audienceLoading
      ? '正在同步直播观众...'
      : audienceError
        ? '观众数据暂不可用'
        : '等待观众数据'

  return (
    <section className="viewer-panel">
      <div className="panel-heading compact">
        <span className="panel-title">当前观众</span>
        <span className="viewer-panel-heading-actions">
          <span className="chat-count">{audience?.active_count ?? '--'}</span>
          {onViewAll && (
            <button
              type="button"
              onClick={onViewAll}
              title="查看全部直播观众"
              aria-label="查看全部直播观众"
            >
              <ArrowRight size={14} />
            </button>
          )}
        </span>
      </div>
      <div className="viewer-toolbar">
        <div className="viewer-filter" role="tablist" aria-label="观众筛选">
          {(['active', 'muted', 'all'] as const).map((value) => (
            <button
              className={filter === value ? 'active' : ''}
              key={value}
              onClick={() => setFilter(value)}
              role="tab"
              aria-selected={filter === value}
              type="button"
            >
              {{ active: '在线', muted: '禁言', all: '全部' }[value]}
            </button>
          ))}
        </div>
        <select
          aria-label="禁言时长"
          value={muteDurationMs}
          onChange={(event) => setMuteDurationMs(Number(event.target.value))}
        >
          <option value={60_000}>1 分钟</option>
          <option value={300_000}>5 分钟</option>
          <option value={600_000}>10 分钟</option>
        </select>
      </div>
      <div className="viewer-list">
        {notice && (
          <div className="viewer-inline-notice" role="alert">
            <span>
              {audienceError ? `同步失败：${audienceError}` : operationError}
            </span>
            {audienceError ? (
              <button
                type="button"
                disabled={audienceLoading}
                onClick={() => void onRetryAudience()}
                title="重新同步直播观众"
                aria-label="重新同步直播观众"
              >
                <RefreshCw size={13} aria-hidden="true" />
              </button>
            ) : (
              <button
                type="button"
                onClick={onDismissOperationError}
                title="关闭错误提示"
                aria-label="关闭错误提示"
              >
                <X size={13} aria-hidden="true" />
              </button>
            )}
          </div>
        )}
        {viewers.map((viewer) => {
          const muted = (viewer.muted_until_ms ?? 0) > now
          const active = viewer.presence_state === 'active'
          const pending = pendingViewerId !== null
          return (
            <div className="viewer-row" key={viewer.viewer_instance_id}>
              <span className="viewer-avatar">{viewer.display_name.charAt(0)}</span>
              <span className="viewer-identity">
                <strong>{viewer.display_name}</strong>
                <small>{viewer.persona_display_name}</small>
              </span>
              <span className={`viewer-state ${muted ? 'muted' : viewer.presence_state}`}>
                {muted ? '禁言中' : active ? '在线' : viewer.presence_state === 'left' ? '已离开' : '已踢出'}
              </span>
              <span className="viewer-actions">
                {active && (
                  <button
                    disabled={pending}
                    onClick={() => void (muted
                      ? onUnmute(viewer.viewer_instance_id)
                      : onMute(viewer.viewer_instance_id, muteDurationMs))}
                    aria-label={`${muted ? '解除禁言' : '限时禁言'}：${viewer.display_name}`}
                    title={muted ? '解除禁言' : '限时禁言'}
                    type="button"
                  >
                    {muted ? <Volume2 size={14} /> : <VolumeX size={14} />}
                  </button>
                )}
                {active && (
                  <button
                    className="danger"
                    disabled={pending}
                    onClick={() => {
                      if (window.confirm(
                        `确定将“${viewer.display_name}”踢出本场直播吗？该操作本场不可撤销。`
                      )) {
                        void onKick(viewer.viewer_instance_id)
                      }
                    }}
                    aria-label={`踢出本场直播：${viewer.display_name}`}
                    title="踢出本场直播"
                    type="button"
                  >
                    <UserMinus size={14} />
                  </button>
                )}
              </span>
            </div>
          )
        })}
        {viewers.length === 0 && <p className="viewer-empty">{emptyMessage}</p>}
      </div>
    </section>
  )
}
