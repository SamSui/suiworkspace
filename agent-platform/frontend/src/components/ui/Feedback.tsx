/**
 * 通用四态反馈：加载骨架 / 空态（带主操作引导）/ 错误态（可读文案）。
 * ADR §决策4 状态规范。
 */
import type { ReactNode } from 'react'
import { Spinner } from './Spinner'

export function LoadingBlock({ label = '加载中…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-gray-400">
      <Spinner size="sm" /> {label}
    </div>
  )
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-gray-300 bg-white px-6 py-12 text-center">
      <p className="text-sm font-medium text-gray-600">{title}</p>
      {hint && <p className="text-sm text-gray-400">{hint}</p>}
      {action}
    </div>
  )
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-card border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">
      {message}
    </div>
  )
}
