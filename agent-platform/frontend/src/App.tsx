/**
 * 应用根组件：挂载后从持久化 token 恢复登录态，随后渲染路由。
 */
import { useEffect } from 'react'
import { useAuthStore } from './store/auth'
import AppRouter from './router'

export default function App() {
  const fetchMe = useAuthStore((s) => s.fetchMe)
  const ready = useAuthStore((s) => s.ready)

  useEffect(() => {
    // 应用启动：恢复登录态（有 token 才拉用户）
    void fetchMe()
  }, [fetchMe])

  // ready 前先渲染空壳骨架，避免守卫基于未就绪状态误跳
  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center text-gray-400">
        加载中…
      </div>
    )
  }

  return <AppRouter />
}
