import { Check, ChevronDown } from 'lucide-react'
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode
} from 'react'
import { createPortal } from 'react-dom'
import './select-dropdown.css'

type SelectValue = string | number

export type SelectDropdownOption<T extends SelectValue> = {
  value: T
  label: ReactNode
  textValue?: string
  disabled?: boolean
}

export type SelectDropdownProps<T extends SelectValue> = {
  ariaLabel: string
  value: T
  options: readonly SelectDropdownOption<T>[]
  onChange: (value: T) => void
  className?: string
  triggerClassName?: string
  disabled?: boolean
  placeholder?: string
  compact?: boolean
  align?: 'start' | 'end'
  id?: string
}

type MenuPosition = {
  left: number
  top: number
  width: number
  maxHeight: number
  placement: 'top' | 'bottom'
}

const VIEWPORT_MARGIN = 8
const MENU_GAP = 5
const MENU_MAX_HEIGHT = 280

function optionText<T extends SelectValue>(option: SelectDropdownOption<T>): string {
  if (option.textValue) return option.textValue
  return typeof option.label === 'string' || typeof option.label === 'number'
    ? String(option.label)
    : String(option.value)
}

export function findNextEnabledOptionIndex<T extends SelectValue>(
  options: readonly SelectDropdownOption<T>[],
  currentIndex: number,
  direction: 1 | -1
): number {
  if (options.length === 0) return -1
  for (let step = 1; step <= options.length; step += 1) {
    const index = (currentIndex + direction * step + options.length) % options.length
    if (!options[index]?.disabled) return index
  }
  return -1
}

function firstEnabledOptionIndex<T extends SelectValue>(
  options: readonly SelectDropdownOption<T>[],
  fromEnd = false
): number {
  if (fromEnd) {
    for (let index = options.length - 1; index >= 0; index -= 1) {
      if (!options[index]?.disabled) return index
    }
    return -1
  }
  return options.findIndex((option) => !option.disabled)
}

export function SelectDropdown<T extends SelectValue>({
  ariaLabel,
  value,
  options,
  onChange,
  className,
  triggerClassName,
  disabled = false,
  placeholder = '请选择',
  compact = false,
  align = 'start',
  id
}: SelectDropdownProps<T>): React.JSX.Element {
  const generatedId = useId()
  const triggerId = id ?? `select-trigger-${generatedId}`
  const menuId = `select-menu-${generatedId}`
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const typeaheadRef = useRef({ text: '', timeoutId: 0 })
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null)
  const selectedIndex = options.findIndex((option) => option.value === value)
  const selectedOption = selectedIndex >= 0 ? options[selectedIndex] : null
  const activeOptionId =
    open && activeIndex >= 0 ? `${menuId}-option-${activeIndex}` : undefined
  const portalHost =
    triggerRef.current?.closest<HTMLElement>('[data-select-dropdown-portal-host]') ??
    document.body

  const updateMenuPosition = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const bounds = trigger.getBoundingClientRect()
    const availableWidth = Math.max(0, window.innerWidth - VIEWPORT_MARGIN * 2)
    const width = Math.min(Math.max(bounds.width, 132), availableWidth)
    const roomBelow = window.innerHeight - bounds.bottom - MENU_GAP - VIEWPORT_MARGIN
    const roomAbove = bounds.top - MENU_GAP - VIEWPORT_MARGIN
    const placement = roomBelow < 140 && roomAbove > roomBelow ? 'top' : 'bottom'
    const availableHeight = placement === 'bottom' ? roomBelow : roomAbove
    const idealLeft = align === 'end' ? bounds.right - width : bounds.left
    const left = Math.min(
      Math.max(VIEWPORT_MARGIN, idealLeft),
      Math.max(VIEWPORT_MARGIN, window.innerWidth - width - VIEWPORT_MARGIN)
    )

    setMenuPosition({
      left,
      top: placement === 'bottom' ? bounds.bottom + MENU_GAP : bounds.top - MENU_GAP,
      width,
      maxHeight: Math.min(MENU_MAX_HEIGHT, Math.max(88, availableHeight)),
      placement
    })
  }, [align])

  const close = useCallback((restoreFocus = false) => {
    setOpen(false)
    setMenuPosition(null)
    if (restoreFocus) {
      window.requestAnimationFrame(() => triggerRef.current?.focus())
    }
  }, [])

  const openMenu = useCallback(
    (preferredIndex = selectedIndex) => {
      if (disabled || options.length === 0) return
      const nextIndex =
        preferredIndex >= 0 && !options[preferredIndex]?.disabled
          ? preferredIndex
          : firstEnabledOptionIndex(options)
      setActiveIndex(nextIndex)
      setOpen(true)
    },
    [disabled, options, selectedIndex]
  )

  const selectIndex = useCallback(
    (index: number) => {
      const option = options[index]
      if (!option || option.disabled) return
      if (option.value !== value) onChange(option.value)
      close(true)
    },
    [close, onChange, options, value]
  )

  useLayoutEffect(() => {
    if (!open) return
    updateMenuPosition()
  }, [open, updateMenuPosition])

  useEffect(() => {
    if (!open) return
    const handlePointerDown = (event: PointerEvent): void => {
      const target = event.target as Node
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) return
      close()
    }
    const handleViewportChange = (): void => updateMenuPosition()
    document.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('resize', handleViewportChange)
    window.addEventListener('scroll', handleViewportChange, true)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('resize', handleViewportChange)
      window.removeEventListener('scroll', handleViewportChange, true)
    }
  }, [close, open, updateMenuPosition])

  useEffect(
    () => () => {
      window.clearTimeout(typeaheadRef.current.timeoutId)
    },
    []
  )

  useEffect(() => {
    if (!disabled || !open) return
    close()
  }, [close, disabled, open])

  const menuStyle = useMemo<CSSProperties | undefined>(() => {
    if (!menuPosition) return undefined
    return {
      left: menuPosition.left,
      top: menuPosition.top,
      width: menuPosition.width,
      maxHeight: menuPosition.maxHeight,
      transform: menuPosition.placement === 'top' ? 'translateY(-100%)' : undefined
    }
  }, [menuPosition])

  const moveActive = (direction: 1 | -1): void => {
    const origin =
      activeIndex >= 0
        ? activeIndex
        : direction === 1
          ? -1
          : 0
    setActiveIndex(findNextEnabledOptionIndex(options, origin, direction))
  }

  const handleTypeahead = (key: string): void => {
    const nextText = `${typeaheadRef.current.text}${key.toLocaleLowerCase()}`
    window.clearTimeout(typeaheadRef.current.timeoutId)
    typeaheadRef.current = {
      text: nextText,
      timeoutId: window.setTimeout(() => {
        typeaheadRef.current.text = ''
      }, 500)
    }
    const matchIndex = options.findIndex(
      (option) =>
        !option.disabled &&
        optionText(option).toLocaleLowerCase().startsWith(nextText)
    )
    if (matchIndex >= 0) setActiveIndex(matchIndex)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>): void => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) {
        openMenu()
      } else {
        moveActive(event.key === 'ArrowDown' ? 1 : -1)
      }
      return
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      if (!open) openMenu()
      setActiveIndex(firstEnabledOptionIndex(options, event.key === 'End'))
      return
    }
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      event.stopPropagation()
      close(true)
      return
    }
    if ((event.key === 'Enter' || event.key === ' ') && open) {
      event.preventDefault()
      selectIndex(activeIndex)
      return
    }
    if (event.key === 'Tab' && open) {
      close()
      return
    }
    if (event.key.length === 1 && /\S/.test(event.key)) {
      if (!open) openMenu()
      handleTypeahead(event.key)
    }
  }

  return (
    <div
      className={[
        'select-dropdown',
        compact ? 'select-dropdown-compact' : '',
        open ? 'is-open' : '',
        className ?? ''
      ].filter(Boolean).join(' ')}
    >
      <button
        ref={triggerRef}
        id={triggerId}
        className={['select-dropdown-trigger', triggerClassName ?? ''].filter(Boolean).join(' ')}
        type="button"
        role="combobox"
        aria-label={ariaLabel}
        aria-controls={menuId}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-activedescendant={activeOptionId}
        data-value={String(value)}
        disabled={disabled}
        onClick={() => {
          if (open) close()
          else openMenu()
        }}
        onKeyDown={handleKeyDown}
      >
        <span className={selectedOption ? 'select-dropdown-value' : 'select-dropdown-placeholder'}>
          {selectedOption?.label ?? placeholder}
        </span>
        <ChevronDown className="select-dropdown-chevron" size={14} aria-hidden="true" />
      </button>

      {open && menuPosition && createPortal(
        <div
          ref={menuRef}
          id={menuId}
          className="select-dropdown-menu"
          role="listbox"
          aria-label={ariaLabel}
          aria-labelledby={triggerId}
          style={menuStyle}
          data-placement={menuPosition.placement}
        >
          {options.map((option, index) => {
            const selected = option.value === value
            const active = index === activeIndex
            return (
              <button
                id={`${menuId}-option-${index}`}
                className={[
                  'select-dropdown-option',
                  selected ? 'is-selected' : '',
                  active ? 'is-active' : ''
                ].filter(Boolean).join(' ')}
                type="button"
                role="option"
                aria-selected={selected}
                data-value={String(option.value)}
                disabled={option.disabled}
                key={String(option.value)}
                tabIndex={-1}
                onPointerMove={() => setActiveIndex(index)}
                onClick={() => selectIndex(index)}
              >
                <span>{option.label}</span>
                <Check size={14} aria-hidden="true" />
              </button>
            )
          })}
        </div>,
        portalHost
      )}
    </div>
  )
}
