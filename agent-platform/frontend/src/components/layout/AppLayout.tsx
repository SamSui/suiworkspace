/**
 * 应用布局（ADR §决策1）：顶部全局导航条 + 主内容区。
 * 已登录显示用户名 + 退出；导航按信息架构 8 模块分列。
 */
import { NavLink, Outlet } from 'react-router-dom'
import { useAuthStore } from '../../store/auth'

const NAV_ITEMS = [
  { to: '/', label: '工作台', end: true },
  { to: '/kb', label: '知识库' },
  { to: '/chat', label: '对话', end: true },
  { to: '/monitor', label: '监控', end: true },
  { to: '/model', label: '模型配置', p2: true },
  { to: '/tools', label: '工具-MCP', p2: true },
  { to: '/users', label: '用户', p2: true },
]

export default function AppLayout() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)

  return (
    <div className="flex min-h-screen flex-col">
      {/* 顶部主导航（64px） */}
      <header className="flex h-16 items-center gap-6 border-b border-gray-200 bg-white px-6 shadow-sm">
        <span className="text-lg font-semibold text-primary">智能体中台</span>
        <nav className="flex items-center gap-1">
          {NAV_ITEMS.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              end={it.end}
              className={({ isActive }) =>
                `rounded ctrl px-3 py-1.5 text-sm transition ${
                  isActive
                    ? 'bg-primary-light text-primary font-medium'
                    : 'text-gray-600 hover:bg-gray-50'
                }`
              }
            >
              {it.label}
              {it.p2 && (
                <span className="ml-1.5 rounded bg-accent-purple/10 px-1 py-0.5 text-xs text-accent-purple">
                  P2
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          {user ? (
            <>
              <span className="text-sm text-gray-700">{user.name}</span>
              <button
                onClick={() => void logout()}
                className="rounded ctrl border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
              >
                退出
              </button>
            </>
          ) : (
            <span className="text-sm text-gray-400">未登录</span>
          )}
        </div>
      </header>

      {/* 主内容区 */}
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  )
}
