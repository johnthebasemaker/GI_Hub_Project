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
