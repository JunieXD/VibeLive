import type { ReactNode } from 'react'
import { cx } from './styles'

type IconButtonProps = {
  title: string
  disabled?: boolean
  danger?: boolean
  onClick(): void
  children: ReactNode
}

export function IconButton({
  title,
  disabled,
  danger,
  onClick,
  children
}: IconButtonProps): React.JSX.Element {
  return (
    <button
      type="button"
      className={cx('aw-icon-button', danger && 'danger')}
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  )
}
