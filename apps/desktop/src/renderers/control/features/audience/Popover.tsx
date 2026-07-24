import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { cx } from './styles'

type PopoverProps = {
  title: string
  trigger: ReactNode
  iconTrigger?: boolean
  disabled?: boolean
  panelWidth?: number
  children: ReactNode
}

export function Popover({
  title,
  trigger,
  iconTrigger,
  disabled,
  panelWidth,
  children
}: PopoverProps): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const rootRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!open) return
    const handlePointerDown = (event: PointerEvent): void => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  return (
    <div className={cx('aw-popover')} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={cx('aw-popover-trigger', iconTrigger && 'icon', open && 'open')}
        title={title}
        aria-label={title}
        aria-controls={panelId}
        aria-expanded={open}
        aria-haspopup="dialog"
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        {trigger}
      </button>
      <div
        id={panelId}
        className={cx('aw-popover-panel')}
        hidden={!open}
        role="dialog"
        aria-label={title}
        style={panelWidth ? { width: panelWidth } : undefined}
        onClick={(event) => {
          if ((event.target as HTMLElement).closest('[data-popover-close]')) setOpen(false)
        }}
      >
        {children}
      </div>
    </div>
  )
}
