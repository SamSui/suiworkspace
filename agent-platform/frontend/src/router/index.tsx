/**
 * 路由表 + 受保护路由守卫（ADR §决策1 信息架构 / §5.9 顶栏全局 UI 态）。
 * 未登录访问受保护页 → 跳 /login?redirect=<原路径>；登录成功后回跳。
 */
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { isAuthenticated } from '../store/auth'
import AppLayout from '../components/layout/AppLayout'
import LoginPage from '../pages/LoginPage'
import DashboardPage from '../pages/DashboardPage'
import KnowledgeBasePage from '../pages/KnowledgeBasePage'
import KbDetailPage from '../pages/KbDetailPage'
import ChatPage from '../pages/ChatPage'
import MonitorPage from '../pages/MonitorPage'
import P2PlaceholderPage from '../pages/P2PlaceholderPage'

/** 受保护路由包装：未登录统一跳登录并带 redirect 回跳点 */
function Protected({ children }: { children: ReactNode }) {
  const location = useLocation()
  if (!isAuthenticated()) {
    return (
      <Navigate
        to={`/login?redirect=${encodeURIComponent(location.pathname + location.search)}`}
        replace
      />
    )
  }
  return children
}

export default function AppRouter() {
  return (
    <Routes>
      {/* 公开：登录页 */}
      <Route path="/login" element={<LoginPage />} />

      {/* 受保护：外层统一应用布局（顶栏主导航 + 内容区） */}
      <Route
        path="/"
        element={
          <Protected>
            <AppLayout />
          </Protected>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="kb" element={<KnowledgeBasePage />} />
        <Route path="kb/:kbId" element={<KbDetailPage />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="monitor" element={<MonitorPage />} />

        {/* P2 骨架：仅导航占位 + 徽标，不实现 */}
        <Route path="model" element={<P2PlaceholderPage module="模型配置" />} />
        <Route path="tools" element={<P2PlaceholderPage module="工具 - MCP" />} />
        <Route path="users" element={<P2PlaceholderPage module="用户与租户权限" />} />
      </Route>

      {/* 其余 → 登录或工作台 */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
