import type { CSSProperties } from 'react'

export function Spinner({
  size = 'md',
  className = '',
}: {
  size?: 'sm' | 'md' | 'lg'
  className?: string
}) {
  const map = { sm: 'h-4 w-4', md: 'h-5 w-5', lg: 'h-8 w-8' }
  const style = { borderTopColor: 'transparent' } as CSSProperties
  return (
    <span
      className={`inline-block animate-spin rounded-full border-2 border-primary ${map[size]} ${className}`}
      style={style}
      aria-hidden
    />
  )
}
