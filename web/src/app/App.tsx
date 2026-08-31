import { Link } from 'react-router-dom'
import { AppRouter } from './router'

export function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <Link className="brand-lockup" to="/assessments/new" aria-label="衡鉴首页">
          <span className="brand-mark" aria-hidden="true">衡</span>
          <span>
            <strong>衡鉴 · Evidence Hiring</strong>
            <small>数据审计评估工作台</small>
          </span>
        </Link>
        <div className="header-context" aria-label="系统上下文">
          <span className="status-dot" aria-hidden="true" />
          <span>企业评估工作台</span>
        </div>
      </header>
      <main className="app-main">
        <AppRouter />
      </main>
      <footer className="app-footer">
        <span>辅助决策工具 · 证据可追溯</span>
        <span>AI Agent / 2026-H2</span>
      </footer>
    </div>
  )
}
