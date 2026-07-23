import { MonitorUp, RefreshCw, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type JSX } from 'react'
import type { DesktopSource } from '../../../../shared/contracts'

export type SourcePickerDialogProps = {
  onClose: () => void
  onSelect: (source: DesktopSource) => Promise<void>
}

export function SourcePickerDialog({
  onClose,
  onSelect
}: SourcePickerDialogProps): JSX.Element {
  const [sources, setSources] = useState<DesktopSource[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const dialogRef = useRef<HTMLElement>(null)

  const loadSources = useCallback(async (): Promise<void> => {
    setLoading(true)
    setError(null)
    try {
      const nextSources = await window.advx.listDesktopSources()
      setSources(nextSources)
      setSelectedId((current) =>
        nextSources.some((source) => source.id === current)
          ? current
          : (nextSources[0]?.id ?? null)
      )
    } catch {
      setError('无法读取屏幕来源，请检查系统录屏权限。')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadSources()
  }, [loadSources])

  useEffect(() => {
    dialogRef.current?.focus()

    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      onClose()
    }

    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  const selectedSource = sources.find((source) => source.id === selectedId)

  return (
    <div
      className="fixed inset-0 z-20 grid place-items-center bg-[rgb(5_6_8_/_68%)] p-7 backdrop-blur-sm"
      role="presentation"
      onMouseDown={onClose}
    >
      <section
        className="grid h-[min(720px,86vh)] w-[min(980px,92vw)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden rounded-xl border border-[var(--border-strong)] bg-[var(--panel)] shadow-[0_32px_80px_rgb(0_0_0_/_55%)] outline-none"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="source-dialog-title"
        aria-describedby="source-dialog-summary"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-3.5 border-b border-[var(--border)] bg-[var(--panel-raise)] px-4.5 py-3.5">
          <div>
            <p className="m-0 text-[10px] font-bold uppercase text-[var(--accent)]">画面采集</p>
            <h2 id="source-dialog-title" className="m-0 mt-1 text-[17px] font-bold">
              选择屏幕或窗口
            </h2>
          </div>
          <div className="flex gap-2">
            <button
              className="grid size-8 place-items-center rounded-lg border border-[var(--border)] bg-transparent text-[var(--text-dim)] transition-colors hover:bg-[var(--panel)] hover:text-[var(--text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
              type="button"
              title="刷新来源"
              aria-label="刷新来源"
              disabled={loading}
              onClick={() => void loadSources()}
            >
              <RefreshCw size={17} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
            </button>
            <button
              className="grid size-8 place-items-center rounded-lg border border-[var(--border)] bg-transparent text-[var(--text-dim)] transition-colors hover:bg-[var(--panel)] hover:text-[var(--text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
              type="button"
              title="关闭"
              aria-label="关闭来源选择器"
              onClick={onClose}
            >
              <X size={18} aria-hidden="true" />
            </button>
          </div>
        </header>

        <div
          className="grid min-h-0 content-start grid-cols-3 gap-3 overflow-auto p-4"
          aria-busy={loading}
        >
          {loading && (
            <div
              className="col-span-full grid min-h-55 place-items-center text-xs text-[var(--text-faint)]"
              role="status"
              aria-live="polite"
            >
              正在读取可用窗口...
            </div>
          )}
          {error && (
            <div
              className="col-span-full grid min-h-55 place-items-center text-xs text-[var(--danger)]"
              role="alert"
            >
              {error}
            </div>
          )}
          {!loading &&
            !error &&
            sources.map((source) => {
              const selected = source.id === selectedId
              return (
                <button
                  data-source-option
                  className={[
                    'relative min-w-0 overflow-hidden rounded-lg border bg-[var(--panel-raise)] p-1.5 text-left text-[var(--text)] transition-[border-color,box-shadow] hover:border-[#4a4e5c] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]',
                    selected
                      ? 'border-[var(--accent)] shadow-[0_0_0_2px_var(--accent-soft)]'
                      : 'border-[var(--border-strong)]'
                  ].join(' ')}
                  key={source.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => setSelectedId(source.id)}
                >
                  <img
                    className="block aspect-video w-full rounded-sm bg-[var(--bg-deep)] object-cover"
                    src={source.thumbnailUrl}
                    alt=""
                  />
                  <span className="flex min-w-0 items-center gap-2 px-1 pb-1 pt-2 text-[11px] font-bold">
                    {source.appIconUrl && (
                      <img
                        className="size-4 shrink-0 rounded-sm"
                        src={source.appIconUrl}
                        alt=""
                      />
                    )}
                    <span className="truncate">{source.name}</span>
                  </span>
                  <span className="absolute right-3 top-3 rounded bg-[rgb(10_11_14_/_80%)] px-1.5 py-0.5 text-[9px] text-white">
                    {source.kind === 'screen' ? '屏幕' : '窗口'}
                  </span>
                </button>
              )
            })}
          {!loading && !error && sources.length === 0 && (
            <div
              className="col-span-full grid min-h-55 place-items-center text-xs text-[var(--text-faint)]"
              role="status"
            >
              没有可用的屏幕或窗口
            </div>
          )}
        </div>

        <footer className="flex min-h-15 items-center justify-between gap-3.5 border-t border-[var(--border)] bg-[var(--panel-raise)] px-4.5 py-3.5 text-[11px] text-[var(--text-dim)]">
          <span id="source-dialog-summary" aria-live="polite">
            {sources.length} 个可用来源
          </span>
          <button
            className="inline-flex min-h-9 items-center gap-2 rounded-lg border-0 bg-[var(--accent)] px-4 text-xs font-bold text-[var(--accent-ink)] transition-[filter] hover:brightness-105 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40"
            type="button"
            disabled={!selectedSource}
            onClick={() => selectedSource && void onSelect(selectedSource)}
          >
            <MonitorUp size={17} aria-hidden="true" />
            使用此来源
          </button>
        </footer>
      </section>
    </div>
  )
}
