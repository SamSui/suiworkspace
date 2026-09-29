/**
 * 骨架屏（ADR §决策4 加载态）：降低首屏心智，用于列表/卡片加载。
 */
export function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div
      className={`animate-shimmer rounded bg-gray-100 ${className}`}
      aria-hidden
    />
  )
}

/** 一列骨架行（多条） */
export function SkeletonRows({ rows = 3, colClass = 'h-4' }: { rows?: number; colClass?: string }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className={`w-full ${colClass}`} />
      ))}
    </div>
  )
}
