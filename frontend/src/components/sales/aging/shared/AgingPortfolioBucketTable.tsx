import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { AgingPortfolioBucket } from '../../../../lib/api'
import { fmtAmount } from '../../../../lib/fmt'
import { bucketPieColor } from './agingPortfolioColors'
import { portfolioDimensionLabel, type AgingPortfolioSide } from './agingPortfolioDimensions'

function formatRelationshipDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
    return d.toLocaleDateString('de-DE', { year: 'numeric', month: 'short', day: 'numeric' })
  } catch {
    return iso.slice(0, 10)
  }
}

export default function AgingPortfolioBucketTable({
  side,
  dimension,
  buckets,
  showRelationship,
}: {
  side: AgingPortfolioSide
  dimension: string
  buckets: AgingPortfolioBucket[]
  showRelationship: boolean
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {}
    for (const b of buckets) init[b.band] = true
    return init
  })

  const dimLabel = portfolioDimensionLabel(side, dimension)
  const relHeader = side === 'receivables' ? 'Customer since' : 'Supplier since'

  const totalOpen = useMemo(
    () => buckets.reduce((s, b) => s + b.amount, 0),
    [buckets],
  )

  function toggle(band: string) {
    setExpanded(prev => ({ ...prev, [band]: !prev[band] }))
  }

  if (!buckets.some(b => b.amount > 0)) {
    return (
      <p className="text-xs py-8 text-center" style={{ color: '#94A3B8' }}>
        No open balance for this view
      </p>
    )
  }

  return (
    <div className="space-y-2">
      {buckets.map((bucket, bi) => {
        if (bucket.amount <= 0 && bucket.rows.length === 0) return null
        const isOpen = expanded[bucket.band] !== false
        const color = bucketPieColor(bucket.band, bi)

        return (
          <div
            key={bucket.band}
            className="rounded-lg overflow-hidden"
            style={{ border: '1px solid #E2E8F0' }}
          >
            <button
              type="button"
              className="w-full flex items-center gap-2 px-3 py-2.5 text-left"
              style={{ background: '#F8FAFC' }}
              onClick={() => toggle(bucket.band)}
            >
              {isOpen ? (
                <ChevronDown size={14} style={{ color: '#64748B' }} />
              ) : (
                <ChevronRight size={14} style={{ color: '#64748B' }} />
              )}
              <span className="w-2 h-2 rounded-sm shrink-0" style={{ background: color }} />
              <span className="text-xs font-semibold flex-1" style={{ color: '#1E3A5F' }}>
                {bucket.label}
              </span>
              <span className="text-[10px] tabular-nums" style={{ color: '#94A3B8' }}>
                {bucket.document_count} inv.
              </span>
              <span className="text-xs font-semibold tabular-nums ml-2" style={{ color: '#1E3A5F' }}>
                {fmtAmount(bucket.amount)}
              </span>
              <span className="text-[10px] tabular-nums ml-1" style={{ color: '#94A3B8' }}>
                {totalOpen > 0 ? `${Math.round((bucket.amount / totalOpen) * 100)}%` : ''}
              </span>
            </button>

            {isOpen && (
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ borderTop: '1px solid #F1F5F9' }}>
                    <th className="px-3 py-1.5 text-left font-semibold" style={{ color: '#64748B' }}>
                      {dimLabel}
                    </th>
                    <th className="px-2 py-1.5 text-right font-semibold" style={{ color: '#64748B' }}>
                      Open amount
                    </th>
                    <th className="px-2 py-1.5 text-right font-semibold w-16" style={{ color: '#64748B' }}>
                      Invoices
                    </th>
                    {showRelationship && (
                      <th className="px-3 py-1.5 text-right font-semibold" style={{ color: '#64748B' }}>
                        {relHeader}
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {bucket.rows.length === 0 ? (
                    <tr>
                      <td
                        colSpan={showRelationship ? 4 : 3}
                        className="px-3 py-4 text-center"
                        style={{ color: '#94A3B8' }}
                      >
                        No {dimLabel.toLowerCase()} rows in this bucket
                      </td>
                    </tr>
                  ) : (
                    bucket.rows.map((row, ri) => (
                      <tr key={`${bucket.band}-${ri}-${row.label}`} style={{ borderTop: '1px solid #F8FAFC' }}>
                        <td className="px-3 py-1.5 max-w-[12rem] truncate font-medium" style={{ color: '#334155' }} title={row.label}>
                          {row.label}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums font-semibold" style={{ color: '#1E3A5F' }}>
                          {fmtAmount(row.amount)}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums" style={{ color: '#64748B' }}>
                          {row.document_count}
                        </td>
                        {showRelationship && (
                          <td className="px-3 py-1.5 text-right tabular-nums" style={{ color: '#64748B' }}>
                            {formatRelationshipDate(row.relationship_since)}
                          </td>
                        )}
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            )}
          </div>
        )
      })}
    </div>
  )
}
