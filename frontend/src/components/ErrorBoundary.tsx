import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button, Result } from 'antd'

interface Props {
  children: ReactNode
}
interface State {
  error: Error | null
}

// Top-level React error boundary. Without one, any render-time exception in a
// page blanks the whole screen; here the user gets a recoverable message.
// (Error boundaries must be class components.)
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Log for debugging; a deployment could POST this to an error-tracking
    // endpoint (see the observability backlog item in NEW_STACK_HANDOFF §5).
    console.error('Unhandled UI error:', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <Result
          status="error"
          title="Something went wrong"
          subTitle="This page hit an unexpected error. Reloading usually fixes it."
          extra={[
            <Button type="primary" key="reload" onClick={() => window.location.reload()}>
              Reload
            </Button>,
            <Button key="home" onClick={() => { window.location.href = '/' }}>
              Go to dashboard
            </Button>,
          ]}
        />
      )
    }
    return this.props.children
  }
}
