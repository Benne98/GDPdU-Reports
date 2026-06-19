import type { ReactNode } from 'react'
import { BRAND, SALES_CHART_CARD_CLASS, SALES_CHART_CARD_STYLE } from './salesChartTheme'

type Props = {
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  headerExtra?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  compactHeader?: boolean
}

export default function SalesAnalyticsChartShell({
  title,
  subtitle,
  actions,
  headerExtra,
  children,
  className = '',
  bodyClassName = '',
  compactHeader = false,
}: Props) {
  return (
    <div className={`${SALES_CHART_CARD_CLASS} flex flex-col ${className}`} style={SALES_CHART_CARD_STYLE}>
      <div
        className={`px-5 shrink-0 ${compactHeader ? 'pt-3.5 pb-2.5' : 'pt-4 pb-3'}`}
        style={{ borderBottom: `1px solid ${BRAND.borderLight}` }}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h3
              className="text-sm font-semibold tracking-tight leading-snug"
              style={{ color: BRAND.navy }}
            >
              {title}
            </h3>
            {subtitle && (
              <p className="text-xs mt-0.5 font-medium" style={{ color: BRAND.textMuted }}>
                {subtitle}
              </p>
            )}
          </div>
          {actions && <div className="shrink-0">{actions}</div>}
        </div>
        {headerExtra}
      </div>
      <div className={`px-3 pb-3 flex flex-col min-h-0 ${bodyClassName}`}>{children}</div>
    </div>
  )
}
