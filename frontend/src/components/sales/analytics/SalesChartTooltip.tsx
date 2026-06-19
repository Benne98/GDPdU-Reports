import type { ReactNode } from 'react'
import { BRAND } from './salesChartTheme'

export type TooltipRow = {
  label: string
  value: string
  color?: string
}

type Props = {
  title: string
  rows: TooltipRow[]
  footer?: ReactNode
}

export function SalesChartTooltipCard({ title, rows, footer }: Props) {
  return (
    <div
      className="rounded-lg px-3.5 py-2.5 text-xs"
      style={{
        minWidth: 172,
        background: '#FFFFFF',
        border: `1px solid ${BRAND.border}`,
        boxShadow: '0 4px 20px rgba(30, 58, 95, 0.1)',
        color: BRAND.textSecondary,
      }}
    >
      <div
        className="font-semibold mb-2 tracking-tight"
        style={{ color: BRAND.navy, fontSize: '0.8rem' }}
      >
        {title}
      </div>
      <div className="space-y-1.5">
        {rows.map(row => (
          <div key={row.label} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 min-w-0" style={{ color: BRAND.textSecondary }}>
              {row.color && (
                <span
                  className="inline-block w-2 h-2 rounded-sm shrink-0"
                  style={{ background: row.color }}
                />
              )}
              <span className="truncate">{row.label}</span>
            </span>
            <span
              className="font-semibold tabular-nums shrink-0"
              style={{ color: BRAND.text }}
            >
              {row.value}
            </span>
          </div>
        ))}
      </div>
      {footer && (
        <div
          className="mt-2 pt-2 text-[11px]"
          style={{ borderTop: `1px solid ${BRAND.borderLight}`, color: BRAND.textMuted }}
        >
          {footer}
        </div>
      )}
    </div>
  )
}

export const salesChartTooltipProps = {
  wrapperStyle: { zIndex: 60, outline: 'none', pointerEvents: 'none' as const },
  contentStyle: {
    background: 'transparent',
    border: 'none',
    boxShadow: 'none',
    padding: 0,
    margin: 0,
  },
}
