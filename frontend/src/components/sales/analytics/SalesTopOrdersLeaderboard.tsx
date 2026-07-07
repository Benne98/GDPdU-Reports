import type { SalesTopOrder } from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'
import { BRAND, seriesColor } from './salesChartTheme'

type Props = {
  rows: SalesTopOrder[]
  maxRows?: number
}

export default function SalesTopOrdersLeaderboard({ rows, maxRows = 10 }: Props) {
  const slice = rows.slice(0, maxRows)
  if (!slice.length) {
    return (
      <p className="text-xs py-8 text-center" style={{ color: BRAND.textMuted }}>
        No orders for this period
      </p>
    )
  }

  const totalListed = rows.reduce((s, r) => s + r.amount_keur, 0)
  const maxAmount = slice[0]?.amount_keur ?? 1

  return (
    <ul className="space-y-2.5" role="list">
      {slice.map((row, i) => {
        const rank = i + 1
        const pct = totalListed > 0 ? (row.amount_keur / totalListed) * 100 : 0
        const barPct = maxAmount > 0 ? (row.amount_keur / maxAmount) * 100 : 0
        const isTop3 = rank <= 3
        const barColor = isTop3 ? seriesColor(rank - 1) : BRAND.steelLight

        return (
          <li
            key={`${row.invoice_number}-${rank}`}
            className="rounded-lg px-2.5 py-2"
            style={{ background: isTop3 ? BRAND.surfaceRaised : 'transparent' }}
          >
            <div className="flex items-start gap-2.5">
              <span
                className="shrink-0 w-7 h-7 rounded-md flex items-center justify-center text-[12px] font-bold tabular-nums"
                style={{
                  background: isTop3 ? 'rgba(30, 58, 95, 0.1)' : BRAND.surface,
                  color: isTop3 ? BRAND.navy : BRAND.slate,
                }}
              >
                {rank}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p
                      className="text-xs font-semibold truncate leading-snug"
                      style={{ color: BRAND.text }}
                      title={row.customer_name}
                    >
                      {row.customer_name || '—'}
                    </p>
                    <p className="text-[12px] truncate mt-0.5" style={{ color: BRAND.textMuted }}>
                      {row.invoice_number}
                      {row.invoice_date ? ` · ${row.invoice_date}` : ''}
                    </p>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="text-xs font-semibold tabular-nums" style={{ color: BRAND.navy }}>
                      {fmtChartKpi(row.amount_keur)}
                    </p>
                    <span
                      className="inline-block mt-0.5 px-1.5 py-0 rounded text-[10px] font-medium tabular-nums"
                      style={{ background: BRAND.surface, color: BRAND.textSecondary }}
                    >
                      {pct.toFixed(0)}%
                    </span>
                  </div>
                </div>
                <div
                  className="mt-2 h-1.5 rounded-full overflow-hidden"
                  style={{ background: BRAND.borderLight }}
                >
                  <div
                    className="h-full rounded-full transition-all"
                    style={{ width: `${barPct}%`, background: barColor }}
                  />
                </div>
              </div>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
