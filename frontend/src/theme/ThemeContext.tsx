import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type ThemeMode = 'dark' | 'light'

const STORAGE_KEY = 'gi-hub-theme'

interface ThemeModeValue {
  mode: ThemeMode
  toggle: () => void
}

const ThemeModeContext = createContext<ThemeModeValue>({ mode: 'dark', toggle: () => {} })

// Dark is the flagship default regardless of OS preference; the user's
// choice persists per browser. `data-theme` on <html> drives the CSS-side
// tokens (gradients, scrollbars) in index.css.
export function ThemeModeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(() =>
    localStorage.getItem(STORAGE_KEY) === 'light' ? 'light' : 'dark',
  )

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, mode)
    document.documentElement.dataset.theme = mode
  }, [mode])

  const value = useMemo(
    () => ({ mode, toggle: () => setMode((m) => (m === 'dark' ? 'light' : 'dark')) }),
    [mode],
  )
  return <ThemeModeContext.Provider value={value}>{children}</ThemeModeContext.Provider>
}

export function useThemeMode() {
  return useContext(ThemeModeContext)
}
