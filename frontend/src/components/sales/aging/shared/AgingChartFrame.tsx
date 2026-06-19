import type { ReactNode } from 'react'

export default function AgingChartFrame({
  hasData,
  loading,
  height = 260,
  emptyMessage = 'No data',
  children,
}: {
  hasData: boolean
  loading?: boolean
  height?: number
  emptyMessage?: string
  children: ReactNode
}) {
  if (!hasData) {
    return (
      <div
        className="flex items-center justify-center text-sm"
        style={{ height, color: '#94A3B8' }}
      >
        {loading ? 'Loading…' : emptyMessage}
      </div>
    )
  }
  return <>{children}</>
}
