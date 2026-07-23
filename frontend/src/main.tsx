import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider, App as AntApp } from 'antd'
import 'antd/dist/reset.css'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './auth/AuthContext'
import ErrorBoundary from './components/ErrorBoundary'
import { ThemeModeProvider, useThemeMode } from './theme/ThemeContext'
import { darkTheme, lightTheme } from './theme/themes'
import { registerSW } from 'virtual:pwa-register'
import { initOfflineQueue } from './offline/queue'

// Phase B — PWA: SW registration (no-op in dev; auto-updates in prod builds)
// + the offline mutation queue's boot flush / online listener / badge events.
// STRICT OTA (native-apps program): don't wait for a navigation to notice a
// new deployment — poll the service worker every 15 min and on every
// tab-refocus; registerType 'autoUpdate' then swaps + reloads automatically.
registerSW({
  immediate: true,
  onRegisteredSW(_url, reg) {
    if (!reg) return
    const check = () => void reg.update().catch(() => undefined)
    window.setInterval(check, 15 * 60 * 1000)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') check()
    })
  },
})
initOfflineQueue()

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 15_000, refetchOnWindowFocus: false } },
})

// Separate component so it can read the theme mode from context.
function ThemedApp() {
  const { mode } = useThemeMode()
  return (
    <ConfigProvider theme={mode === 'dark' ? darkTheme : lightTheme}>
      <AntApp>
        <BrowserRouter>
          <AuthProvider>
            <ErrorBoundary>
              <App />
            </ErrorBoundary>
          </AuthProvider>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeModeProvider>
        <ThemedApp />
      </ThemeModeProvider>
    </QueryClientProvider>
  </StrictMode>,
)
