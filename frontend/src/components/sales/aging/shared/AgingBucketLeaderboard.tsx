import type { ReceivablesAgingBand } from '../../../../lib/api'
import { fmtAmount } from '../../../../lib/fmt'
import { BRAND, seriesColor } from '../../analytics/salesChartTheme'
import { bucketPieColor } from './agingPortfolioColors'

export default function AgingBucketLeaderboard({
  series,
  total,
}: {
  series: ReceivablesAgingBand[]
  total: number
}) {
  const rows = [...series].filter(s => s.amount > 0).sort((a, b) => b.amount - a.amount)
  if (!rows.length) {
    return (
      <p className="text-xs py-8 text-center" style={{ color: BRAND.textMuted }}>
        No open balance in aging buckets
      </p>
    )
  }

  const maxAmount = rows[0]?.amount ?? 1

  return (
    <ul className="space-y-2.5" role="list">
      {rows.map((row, i) => {
        const rank = i + 1
        const pct = total > 0 ? (row.amount / total) * 100 : 0
        const barPct = maxAmount > 0 ? (row.amount / maxAmount) * 100 : 0
        const isTop3 = rank <= 3
        const barColor = isTop3 ? seriesColor(rank - 1) : bucketPieColor(row.band, i)

        return (
          <li
            key={row.band}
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
                  <p
                    className="text-xs font-semibold leading-snug"
                    style={{ color: BRAND.text }}
                  >
                    {row.label}
                  </p>
                  <div className="shrink-0 text-right">
                    <p className="text-xs font-semibold tabular-nums" style={{ color: BRAND.navy }}>
                      {fmtAmount(row.amount)}
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
