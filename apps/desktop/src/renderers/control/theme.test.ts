import { describe, expect, it } from 'vitest'
import {
  COLOR_THEME_STORAGE_KEY,
  DEFAULT_COLOR_THEME,
  applyColorTheme,
  getNextColorTheme,
  loadColorTheme,
  saveColorTheme
} from './theme'

function createStorage(initialValue: string | null = null): {
  storage: Pick<Storage, 'getItem' | 'setItem'>
  values: Map<string, string>
} {
  const values = new Map<string, string>()
  if (initialValue !== null) values.set(COLOR_THEME_STORAGE_KEY, initialValue)
  return {
    values,
    storage: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value)
    }
  }
}

describe('color theme preference', () => {
  it('restores valid preferences and defaults invalid values to dark', () => {
    expect(loadColorTheme(createStorage('light').storage)).toBe('light')
    expect(loadColorTheme(createStorage('dark').storage)).toBe('dark')
    expect(loadColorTheme(createStorage('system').storage)).toBe(DEFAULT_COLOR_THEME)
    expect(loadColorTheme(createStorage().storage)).toBe(DEFAULT_COLOR_THEME)
  })

  it('falls back when storage is unavailable', () => {
    expect(
      loadColorTheme({
        getItem: () => {
          throw new Error('Storage is unavailable')
        },
        setItem: () => undefined
      })
    ).toBe(DEFAULT_COLOR_THEME)
  })

  it('saves, applies, and toggles the selected theme', () => {
    const { storage, values } = createStorage()
    const root = { dataset: {} } as Pick<HTMLElement, 'dataset'>

    expect(saveColorTheme(storage, 'light')).toBe(true)
    expect(values.get(COLOR_THEME_STORAGE_KEY)).toBe('light')

    applyColorTheme(root, 'light')
    expect(root.dataset.theme).toBe('light')
    expect(getNextColorTheme('light')).toBe('dark')
    expect(getNextColorTheme('dark')).toBe('light')
  })
})
