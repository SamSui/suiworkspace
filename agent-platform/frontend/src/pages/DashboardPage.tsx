/**
 * 工作台总览（ADR §5.2）：知识库卡片 + Agent 数 + 运行概览 + 快捷入口。
 * 数据源：GET /v1/kb、GET /v1/agent、GET /v1/users/me（由 auth store）、/metrics（占位）。
 */
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiListKb } from '../api/kb'
import { useAuthStore } from '../store/auth'
import { SkeletonRows } from '../components/ui/Skeleton'
import { EmptyState, ErrorState } from '../components/ui/Feedback'

export default function DashboardPage() {
  const user = useAuthStore((s) => s.user)
  const { data: kbs, isLoading, isError, error } = useQuery({
    queryKey: ['kb'],
    queryFn: apiListKb,
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">
          {user ? `你好，${user.name}` : '你好'}
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          智能体中台统一管理入口 · 概览与快捷操作
        </p>
      </div>

      {/* 快捷入口 */}
      <div className="flex flex-wrap gap-3">
        <Link
          to="/chat"
          className="rounded-card bg-primary px-4 py-2 text-sm text-white hover:bg-primary-hover"
        >
          发起对话
        </Link>
        <Link
          to="/kb"
          className="rounded-card border border-gray-300 bg-white px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
        >
          管理知识库
        </Link>
      </div>

      {/* 统计卡片 */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="rounded-card bg-white p-5 shadow-card">
          <p className="text-sm text-gray-500">知识库总数</p>
          <p className="mt-1 text-2xl font-semibold">
            {isLoading ? <SkeletonRows rows={1} colClass="h-6 w-16" /> : (kbs?.length ?? 0)}
          </p>
        </div>
        <div className="rounded-card bg-white p-5 shadow-card">
          <p className="text-sm text-gray-500">文档处理</p>
          <p className="mt-1 text-2xl font-semibold">{isLoading ? '…' : '—'}</p>
          <p className="mt-1 text-xs text-gray-400">（需进入知识库查看）</p>
        </div>
        <div className="rounded-card bg-white p-5 shadow-card">
          <p className="text-sm text-gray-500">运行概览</p>
          <p className="mt-1 text-sm text-gray-500">QPS / P95 / 错误率</p>
          <p className="mt-1 text-xs text-gray-400">见「监控」</p>
        </div>
      </div>

      {/* 知识库列表（最近） */}
      <section className="rounded-card bg-white p-5 shadow-card">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-medium">知识库</h2>
          <Link to="/kb" className="text-sm text-primary hover:underline">
            全部 ›
          </Link>
        </div>
        {isLoading ? (
          <SkeletonRows rows={3} />
        ) : isError ? (
          <ErrorState message={error instanceof Error ? error.message : '加载失败'} />
        ) : kbs && kbs.length > 0 ? (
          <ul className="divide-y divide-gray-100">
            {kbs.slice(0, 5).map((kb) => (
              <li key={kb.id} className="flex items-center justify-between py-2">
                <Link to={`/kb/${kb.id}`} className="text-sm text-gray-700 hover:text-primary">
                  {kb.name}
                </Link>
                <span className="text-xs text-gray-400">#{kb.id}</span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            title="还没有知识库"
            hint="创建第一个知识库后即可上传文档"
            action={
              <Link
                to="/kb"
                className="rounded ctrl bg-primary px-4 py-2 text-sm text-white hover:bg-primary-dark"
              >
                新建知识库
              </Link>
            }
          />
        )}
        </section>
    </div>
  )
}
