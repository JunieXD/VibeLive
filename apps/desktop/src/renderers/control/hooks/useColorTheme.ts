import { useCallback, useEffect, useState } from 'react'
import type { ColorTheme } from '../../../shared/contracts'
import {
  applyColorTheme,
  getNextColorTheme,
  saveColorTheme
} from '../theme'

export function useColorTheme(initialTheme: ColorTheme): {
  colorTheme: ColorTheme
  toggleColorTheme: () => void
} {
  const [colorTheme, setColorTheme] = useState(initialTheme)

  useEffect(() => {
    applyColorTheme(document.documentElement, colorTheme)
    saveColorTheme(window.localStorage, colorTheme)
    void window.advx.setColorTheme(colorTheme).catch((error: unknown) => {
      console.error('Failed to sync the control window theme', error)
    })
  }, [colorTheme])

  const toggleColorTheme = useCallback(() => {
    setColorTheme((currentTheme) => getNextColorTheme(currentTheme))
  }, [])

  return { colorTheme, toggleColorTheme }
}
