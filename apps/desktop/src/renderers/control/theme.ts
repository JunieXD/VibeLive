import type { ColorTheme } from '../../shared/contracts'

export const COLOR_THEME_STORAGE_KEY = 'advx.color-theme'
export const DEFAULT_COLOR_THEME: ColorTheme = 'dark'

type ThemeStorage = Pick<Storage, 'getItem' | 'setItem'>
type ThemeRoot = Pick<HTMLElement, 'dataset'>

export function loadColorTheme(storage: ThemeStorage): ColorTheme {
  try {
    const storedTheme = storage.getItem(COLOR_THEME_STORAGE_KEY)
    return storedTheme === 'light' || storedTheme === 'dark'
      ? storedTheme
      : DEFAULT_COLOR_THEME
  } catch {
    return DEFAULT_COLOR_THEME
  }
}

export function saveColorTheme(storage: ThemeStorage, theme: ColorTheme): boolean {
  try {
    storage.setItem(COLOR_THEME_STORAGE_KEY, theme)
    return true
  } catch {
    return false
  }
}

export function applyColorTheme(root: ThemeRoot, theme: ColorTheme): void {
  root.dataset.theme = theme
}

export function getNextColorTheme(theme: ColorTheme): ColorTheme {
  return theme === 'dark' ? 'light' : 'dark'
}
