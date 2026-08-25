import { Alert, Button } from 'antd'
import { Component, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { failed: boolean }

export class PageErrorBoundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children

    return <Alert
      showIcon
      type="error"
      title="当前页面暂时无法显示"
      description="导航与安全状态仍然可用。请重新加载当前页面；若问题持续，请检查浏览器控制台和服务状态。"
      action={<Button onClick={() => window.location.reload()}>重新加载当前页面</Button>}
    />
  }
}
