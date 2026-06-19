import { useEffect, useMemo, useState } from 'react'
import { api, type ReceivablesDimensionRowBuckets } from '../../../../lib/api'
import { fmtAmount } from '../../../../lib/fmt'

const RANK_BANDS = [
  { label: 'Top 5', from: 0, to: 5 },
  { label: 'Top 6–10', from: 5, to: 10 },
  { label: 'Top 11–20', from: 10, to: 20 },
  { label: 'Rank 21+', from: 20, to: null as number | null },
] as const

const BUCKET_COLS: { key: keyof ReceivablesDimensionRowBuckets; label: string }[] = [
  { key: 'not_yet_due', label: 'Not due' },
  { key: 'overdue_1_30', label: '1–30 days' },
  { key: 'overdue_31_60', label: '31–60 days' },
  { key: 'overdue_61_90', label: '61–90 days' },
  { key: 'overdue_91_180', label: '91–180 days' },
  { key: 'overdue_over_180', label: '>180 days' },
]

type Props = {
  side: 'receivables' | 'payables'
  year: number
  month: number
  entity?: string
}

function isBucketRow(row: unknown): row is ReceivablesDimensionRowBuckets {
  return !!row && typeof row === 'object' && 'not_yet_due' in row
}

function sumRow(row: ReceivablesDimensionRowBuckets): ReceivablesDimensionRowBuckets {
  return { ...row }
}

function addRows(a: ReceivablesDimensionRowBuckets, b: ReceivablesDimensionRowBuckets): ReceivablesDimensionRowBuckets {
  const total = (a.total || 0) + (b.total || 0)
  const overdue = total - ((a.not_yet_due || 0) + (b.not_yet_due || 0))
  return {
    label: a.label,
    not_yet_due: (a.not_yet_due || 0) + (b.not_yet_due || 0),
    overdue_1_30: (a.overdue_1_30 || 0) + (b.overdue_1_30 || 0),
    overdue_31_60: (a.overdue_31_60 || 0) + (b.overdue_31_60 || 0),
    overdue_61_90: (a.overdue_61_90 || 0) + (b.overdue_61_90 || 0),
    overdue_91_180: (a.overdue_91_180 || 0) + (b.overdue_91_180 || 0),
    overdue_over_180: (a.overdue_over_180 || 0) + (b.overdue_over_180 || 0),
    total,
    overdue_pct: total > 0 ? Math.round(overdue / total * 1000) / 10 : 0,
  }
}

export default function AgingTopGroupsTable({ side, year, month, entity }: Props) {
  const [rows, setRows] = useState<ReceivablesDimensionRowBuckets[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    const fetcher =
      side === 'receivables'
        ? api.salesReceivablesByDimension('customer', year, month, entity, 100, 'buckets')
        : api.salesPayablesByDimension('supplier', year, month, entity, 100, 'buckets')
    void fetcher
      .then(res => {
        if (cancelled) return
        const bucketRows = res.rows.filter(isBucketRow).map(sumRow)
        setRows(bucketRows)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load aging table')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [side, year, month, entity])

  const grouped = useMemo(() => {
    const grand = rows.reduce(
      (acc, r) => addRows(acc, r),
      {
        label: 'Total',
        not_yet_due: 0,
        overdue_1_30: 0,
        overdue_31_60: 0,
        overdue_61_90: 0,
        overdue_91_180: 0,
        overdue_over_180: 0,
        total: 0,
        overdue_pct: 0,
      },
    )
    const sections: { bandLabel: string; subtotal: ReceivablesDimensionRowBuckets; items: ReceivablesDimensionRowBuckets[] }[] = []
    for (const band of RANK_BANDS) {
      const slice = rows.slice(band.from, band.to ?? undefined)
      if (!slice.length) continue
      const subtotal = slice.reduce(
        (acc, r) => addRows(acc, r),
        {
          label: band.label,
          not_yet_due: 0,
          overdue_1_30: 0,
          overdue_31_60: 0,
          overdue_61_90: 0,
          overdue_91_180: 0,
          overdue_over_180: 0,
          total: 0,
          overdue_pct: 0,
        },
      )
      sections.push({ bandLabel: band.label, subtotal, items: slice })
    }
    return { sections, grand }
  }, [rows])

  const partnerCol = side === 'receivables' ? 'Customer' : 'Supplier'
  const title = side === 'receivables' ? 'Trade receivables aging' : 'Trade payables aging'

  return (
    <section
      className="rounded-xl overflow-hidden"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
    >
      <div className="px-5 py-4 border-b" style={{ borderColor: '#F1F5F9' }}>
        <h3 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>{title}</h3>
        <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
          Clustered by top {partnerCol.toLowerCase()} groups · kEUR
        </p>
      </div>

      {error && (
        <p className="px-5 py-3 text-xs" style={{ color: '#DC2626' }}>{error}</p>
      )}

      <div className="overflow-x-auto max-h-[min(70vh,640px)]">
        <table className="w-full text-xs border-collapse min-w-[960px]">
          <thead className="sticky top-0 z-[1]" style={{ background: '#F8FAFC' }}>
            <tr>
              <th className="text-left px-4 py-2.5 font-semibold" style={{ color: '#64748B' }}>{partnerCol}</th>
              {BUCKET_COLS.map(c => (
                <th key={c.key} className="text-right px-3 py-2.5 font-semibold whitespace-nowrap" style={{ color: '#64748B' }}>
                  {c.label}
                </th>
              ))}
              <th className="text-right px-4 py-2.5 font-semibold whitespace-nowrap" style={{ color: '#64748B' }}>
                {side === 'receivables' ? 'Trade receivables' : 'Trade payables'}
              </th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center" style={{ color: '#94A3B8' }}>Loading…</td>
              </tr>
            ) : grouped.sections.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center" style={{ color: '#94A3B8' }}>No open items</td>
              </tr>
            ) : (
              <>
                {grouped.sections.flatMap(section => [
                  <tr
                    key={`${section.bandLabel}-hdr`}
                    style={{ background: '#EFF6FF', borderTop: '1px solid #E2E8F0' }}
                  >
                    <td className="px-4 py-2 font-bold" style={{ color: '#1E3A5F' }}>{section.bandLabel}</td>
                    {BUCKET_COLS.map(c => (
                      <td key={c.key} className="px-3 py-2 text-right tabular-nums font-semibold" style={{ color: '#1E3A5F' }}>
                        {fmtAmount(section.subtotal[c.key] as number)}
                      </td>
                    ))}
                    <td className="px-4 py-2 text-right tabular-nums font-bold" style={{ color: '#1E3A5F' }}>
                      {fmtAmount(section.subtotal.total)}
                    </td>
                  </tr>,
                  ...section.items.map(item => (
                    <tr
                      key={`${section.bandLabel}-${item.label}`}
                      className="hover:bg-slate-50/60"
                      style={{ borderTop: '1px solid #F1F5F9' }}
                    >
                      <td className="px-4 py-1.5 max-w-[240px] truncate" style={{ color: '#334155' }} title={item.label}>
                        {item.label}
                      </td>
                      {BUCKET_COLS.map(c => (
                        <td key={c.key} className="px-3 py-1.5 text-right tabular-nums" style={{ color: '#475569' }}>
                          {fmtAmount(item[c.key] as number)}
                        </td>
                      ))}
                      <td className="px-4 py-1.5 text-right tabular-nums font-medium" style={{ color: '#334155' }}>
                        {fmtAmount(item.total)}
                      </td>
                    </tr>
                  )),
                ])}
                <tr style={{ background: '#F8FAFC', borderTop: '2px solid #CBD5E1' }}>
                  <td className="px-4 py-2.5 font-bold" style={{ color: '#0F172A' }}>
                    {side === 'receivables' ? 'Trade receivables' : 'Trade payables'}
                  </td>
                  {BUCKET_COLS.map(c => (
                    <td key={c.key} className="px-3 py-2.5 text-right tabular-nums font-bold" style={{ color: '#0F172A' }}>
                      {fmtAmount(grouped.grand[c.key] as number)}
                    </td>
                  ))}
                  <td className="px-4 py-2.5 text-right tabular-nums font-bold" style={{ color: '#0F172A' }}>
                    {fmtAmount(grouped.grand.total)}
                  </td>
                </tr>
              </>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
