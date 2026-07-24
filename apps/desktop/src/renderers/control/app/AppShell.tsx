import {
  Activity,
  Clock,
  LayoutDashboard,
  MessageSquareText,
  Moon,
  RefreshCw,
  Settings,
  SlidersHorizontal,
  Sun,
  Users
} from 'lucide-react'
import type { JSX, ReactNode } from 'react'
import type { ColorTheme } from '../../../shared/contracts'
import type { SessionStatus } from '../../../shared/session'

export type AppShellView = 'live' | 'viewers' | 'audience' | 'settings'

export type AppShellStatusTone = 'offline' | 'online' | 'warning' | 'failed'

export type AppShellStatusItem = {
  id: string
  label: string
  tone?: AppShellStatusTone
  muted?: boolean
}

export type AppShellNotice = {
  title: string
  detail: string
  tone?: 'info' | 'failed'
  action?: {
    label: string
    busyLabel: string
    busy: boolean
    onClick: () => void
  }
}

export type AppShellProps = {
  activeView: AppShellView
  onViewChange: (view: AppShellView) => void
  colorTheme: ColorTheme
  onColorThemeToggle: () => void
  sessionStatus: SessionStatus
  audienceCount: number
  modeName: string
  elapsedSeconds: number
  barrageTotal: number
  statusItems: readonly AppShellStatusItem[]
  notice?: AppShellNotice | null
  children: ReactNode
  roomId?: string
  emergencyStopShortcut?: string
}

const viewLabels: Record<AppShellView, string> = {
  live: '直播控制台',
  viewers: '直播观众',
  audience: '观众配置',
  settings: '设置'
}

const statusLabels: Record<SessionStatus, string> = {
  idle: '未开播',
  starting: '连接中',
  running: '直播中',
  paused: '已暂停',
  stopping: '停止中',
  error: '需要处理'
}

const liveBadgeStyles: Record<SessionStatus, string> = {
  idle: 'border-[var(--border-strong)] bg-[var(--panel-raise)] text-[var(--text-dim)]',
  starting: 'border-[var(--border-strong)] bg-[var(--panel-raise)] text-[var(--text-dim)]',
  running: 'border-[var(--live-border)] bg-[var(--live-soft)] text-[var(--live)]',
  paused: 'border-[var(--amber-border)] bg-[var(--amber-soft)] text-[var(--amber)]',
  stopping: 'border-[var(--border-strong)] bg-[var(--panel-raise)] text-[var(--text-dim)]',
  error: 'border-[var(--danger-border)] bg-[var(--danger-soft)] text-[var(--danger)]'
}

const liveDotStyles: Record<SessionStatus, string> = {
  idle: 'bg-[var(--text-faint)]',
  starting: 'bg-[var(--amber)]',
  running: 'bg-[var(--live)] shadow-[0_0_0_3px_var(--live-soft)]',
  paused: 'bg-[var(--amber)]',
  stopping: 'bg-[var(--amber)]',
  error: 'bg-[var(--danger)]'
}

const statusDotStyles: Record<AppShellStatusTone, string> = {
  offline: 'bg-[var(--text-faint)]',
  online: 'bg-[var(--ok)] shadow-[0_0_0_3px_var(--ok-soft)]',
  warning: 'bg-[var(--amber)]',
  failed: 'bg-[var(--danger)] shadow-[0_0_0_3px_var(--danger-soft)]'
}

function formatElapsed(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(':')
}

export function AppShell({
  activeView,
  onViewChange,
  colorTheme,
  onColorThemeToggle,
  sessionStatus,
  audienceCount,
  modeName,
  elapsedSeconds,
  barrageTotal,
  statusItems,
  notice,
  children,
  roomId = '未启动',
  emergencyStopShortcut = 'Ctrl/⌘ + Shift + X'
}: AppShellProps): JSX.Element {
  const navigationItems = [
    { view: 'live' as const, label: viewLabels.live, icon: LayoutDashboard },
    { view: 'viewers' as const, label: viewLabels.viewers, icon: Users },
    { view: 'audience' as const, label: viewLabels.audience, icon: SlidersHorizontal },
    { view: 'settings' as const, label: viewLabels.settings, icon: Settings }
  ]
  const themeSwitchLabel =
    colorTheme === 'dark' ? '切换到浅色模式' : '切换到深色模式'
  const ThemeSwitchIcon = colorTheme === 'dark' ? Sun : Moon

  return (
    <div className="grid h-screen w-screen grid-cols-[224px_minmax(0,1fr)] overflow-hidden bg-[var(--bg)] pt-8 max-[1240px]:grid-cols-[200px_minmax(0,1fr)]">
      <div
        className="fixed inset-x-0 top-0 z-50 h-8 bg-[var(--bg)] [-webkit-app-region:drag]"
        aria-hidden="true"
      />

      <aside className="flex min-w-0 flex-col border-r border-[var(--border)] bg-[var(--chrome)] px-3 pb-3.5 pt-4.5">
        <div className="flex items-center gap-3 px-2 pb-5.5 pt-0.5">
          <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-[var(--accent)] text-[13px] font-black text-[var(--accent-ink)]">
            AX
          </div>
          <div className="min-w-0">
            <strong className="block truncate text-[15px]">ADVX Live</strong>
            <span className="mt-0.5 block truncate text-[11px] text-[var(--text-faint)]">
              AI 虚拟直播间
            </span>
          </div>
        </div>

        <nav className="grid gap-1" aria-label="主导航">
          {navigationItems.map(({ view, label, icon: Icon }) => {
            const active = activeView === view
            return (
              <button
                className={[
                  'relative grid min-h-10 grid-cols-[20px_1fr_auto] items-center gap-2.5 rounded-lg border-0 px-3 text-left text-[var(--text-dim)] transition-colors hover:bg-[var(--panel-raise)] hover:text-[var(--text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]',
                  active
                    ? 'bg-[var(--accent-soft)] font-bold text-[var(--accent)] before:absolute before:bottom-2.5 before:left-0 before:top-2.5 before:w-[3px] before:rounded-sm before:bg-[var(--accent)]'
                    : 'bg-transparent'
                ].join(' ')}
                type="button"
                key={view}
                aria-current={active ? 'page' : undefined}
                onClick={() => onViewChange(view)}
              >
                <Icon size={18} aria-hidden="true" />
                <span>{label}</span>
                {view === 'viewers' && (
                  <span
                    className={[
                      'min-w-5.5 rounded-full px-1.5 py-0.5 text-center text-[11px]',
                      active
                        ? 'bg-[var(--accent)] text-[var(--accent-ink)]'
                        : 'bg-[var(--panel-raise)] text-[var(--text-dim)]'
                    ].join(' ')}
                  >
                    {audienceCount}
                  </span>
                )}
              </button>
            )
          })}
        </nav>

        <section className="mt-auto border-t border-[var(--border)] px-2.5 pb-1 pt-3.5">
          <div className="mb-3 flex items-center justify-between text-[11px] font-bold uppercase text-[var(--text-faint)]">
            <span>房间信息</span>
            <Activity size={15} aria-hidden="true" />
          </div>
          <div className="grid min-h-6.5 grid-cols-[1fr_auto] items-center gap-2 text-xs text-[var(--text-dim)]">
            <span>房间号</span>
            <strong className="text-[11px] text-[var(--text)]">{roomId}</strong>
          </div>
          <div className="grid min-h-6.5 grid-cols-[1fr_auto] items-center gap-2 text-xs text-[var(--text-dim)]">
            <span>当前模式</span>
            <strong
              className="max-w-28 truncate text-[11px] text-[var(--text)]"
              title={modeName}
            >
              {modeName}
            </strong>
          </div>
          <div className="grid min-h-6.5 grid-cols-[1fr_auto] items-center gap-2 text-xs text-[var(--text-dim)]">
            <span>在线观众</span>
            <strong className="text-[11px] text-[var(--text)]">{audienceCount} 人</strong>
          </div>
        </section>
      </aside>

      <div className="grid min-h-0 min-w-0 grid-rows-[auto_minmax(0,1fr)_auto]">
        <header className="flex min-h-14 items-center justify-between gap-4 border-b border-[var(--border)] bg-[var(--chrome)] px-4.5">
          <div className="flex min-w-0 items-center gap-3.5">
            <span
              className={`inline-flex h-7 items-center gap-2 whitespace-nowrap rounded-full border px-3 text-xs font-bold ${liveBadgeStyles[sessionStatus]}`}
              role="status"
            >
              <span
                className={`size-2 rounded-full ${liveDotStyles[sessionStatus]}`}
                aria-hidden="true"
              />
              {statusLabels[sessionStatus]}
            </span>
            <h1 className="m-0 truncate text-[15px] font-bold">{viewLabels[activeView]}</h1>
          </div>

          <div className="flex items-center gap-2">
            <div className="flex items-center gap-2" aria-label="直播统计">
              <span
                className="inline-flex h-7.5 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 text-xs font-bold tabular-nums text-[var(--text-dim)]"
                title="直播时长"
              >
                <Clock size={14} className="text-[var(--text-faint)]" aria-hidden="true" />
                {formatElapsed(elapsedSeconds)}
              </span>
              <span
                className="inline-flex h-7.5 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 text-xs font-bold tabular-nums text-[var(--text-dim)]"
                title="累计弹幕"
              >
                <MessageSquareText
                  size={14}
                  className="text-[var(--text-faint)]"
                  aria-hidden="true"
                />
                {barrageTotal}
              </span>
              <span
                className="inline-flex h-7.5 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 text-xs font-bold tabular-nums text-[var(--text-dim)]"
                title="在线观众"
              >
                <Users size={14} className="text-[var(--text-faint)]" aria-hidden="true" />
                {audienceCount}
              </span>
            </div>
            <span className="mx-0.5 h-5 w-px bg-[var(--border)]" aria-hidden="true" />
            <button
              className="grid size-7.5 shrink-0 place-items-center rounded-lg border border-[var(--border)] bg-[var(--panel)] text-[var(--text-dim)] transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--panel-raise)] hover:text-[var(--text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
              type="button"
              aria-label={themeSwitchLabel}
              title={themeSwitchLabel}
              onClick={onColorThemeToggle}
            >
              <ThemeSwitchIcon size={15} aria-hidden="true" />
            </button>
          </div>
        </header>

        <main
          className={[
            'min-h-0 min-w-0 px-4.5 py-4',
            activeView === 'live' ? 'flex flex-col overflow-hidden' : 'overflow-auto'
          ].join(' ')}
          data-control-workspace
        >
          {notice && (
            <section
              className={[
                'mb-4 flex min-h-14 items-center justify-between gap-4 border px-4 py-3',
                notice.tone === 'failed'
                  ? 'border-[var(--danger-border)] bg-[var(--danger-soft)]'
                  : 'border-[var(--border-strong)] bg-[var(--panel)]'
              ].join(' ')}
              role={notice.tone === 'failed' ? 'alert' : 'status'}
            >
              <div className="grid min-w-0 gap-1">
                <strong className="text-sm text-[var(--text)]">{notice.title}</strong>
                <span className="text-xs leading-5 text-[var(--text-dim)]">{notice.detail}</span>
              </div>
              {notice.action && (
                <button
                  className="inline-flex min-h-8 shrink-0 items-center gap-2 border border-[var(--border-strong)] bg-[var(--panel-raise)] px-3 text-xs font-bold text-[var(--text)] hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-50"
                  type="button"
                  disabled={notice.action.busy}
                  onClick={notice.action.onClick}
                >
                  <RefreshCw size={15} aria-hidden="true" />
                  {notice.action.busy ? notice.action.busyLabel : notice.action.label}
                </button>
              )}
            </section>
          )}
          <div className={activeView === 'live' ? 'min-h-0 flex-1' : undefined}>{children}</div>
        </main>

        <footer className="flex h-7.5 items-center gap-4.5 border-t border-[var(--border)] bg-[var(--chrome)] px-4 text-[11px]">
          {statusItems.map((item) => (
            <span
              className={`inline-flex items-center gap-2 whitespace-nowrap ${
                item.muted ? 'text-[var(--text-faint)]' : 'text-[var(--text-dim)]'
              }`}
              key={item.id}
            >
              <i
                className={`size-1.75 rounded-full ${statusDotStyles[item.tone ?? 'offline']}`}
                aria-hidden="true"
              />
              {item.label}
            </span>
          ))}
          <span className="flex-1" />
          <span className="whitespace-nowrap text-[var(--text-faint)]">
            紧急停止 {emergencyStopShortcut}
          </span>
        </footer>
      </div>
    </div>
  )
}
