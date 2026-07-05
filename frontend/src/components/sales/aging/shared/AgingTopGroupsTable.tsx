import { useEffect, useMemo, useState } from 'react'
import { api, type ReceivablesDimensionRowBuckets } from '../../../../lib/api'
import { FIN_TABLE_CELL_CLASS } from '../../../financials/statement-two-view/finReportLayout'
import PlExportMenu, { type PlExportKind } from '../../../financials/pl-two-view/PlExportMenu'
import { L1_ROW_STYLE } from '../../analytics/salesBreakdownRender'
import { SalesFinHeader, SalesLabelCell, SalesValCell, SALES_TABLE_HEADER_BG } from '../../analytics/salesFinTableCells'
import { exportAgingTopGroups } from './agingTopGroupsExport'

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

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

const DETAIL_ROW_STYLE = { background: '#FFFFFF', borderBottom: '1px solid #F8FAFC' } as const

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
  const periodLabel = `${MONTHS[month - 1]} ${year}`
  const totalColLabel = side === 'receivables' ? 'Trade receivables' : 'Trade payables'

  async function handleExport(kind: PlExportKind) {
    if (!grouped.sections.length) return
    await exportAgingTopGroups({
      kind,
      side,
      sections: grouped.sections,
      grand: grouped.grand,
      periodLabel,
    })
  }

  return (
    <section
      className="rounded-xl overflow-hidden"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
    >
      <div
        className="px-5 py-3.5 flex items-center justify-between gap-3 border-b"
        style={{ borderColor: '#F1F5F9' }}
      >
        <div>
          <h3 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>{title}</h3>
          <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
            Clustered by top {partnerCol.toLowerCase()} groups · kEUR
          </p>
        </div>
        <PlExportMenu
          formats={['pptx', 'xlsx']}
          onExport={handleExport}
          disabled={loading || !!error || !grouped.sections.length}
        />
      </div>

      {error && (
        <p className="px-5 py-3 text-xs" style={{ color: '#DC2626' }}>{error}</p>
      )}

      <div className="p-4">
        <div className="overflow-auto max-h-[min(70vh,640px)]">
          <table className="w-full border-collapse">
            <thead className="sticky top-0 z-[1]" style={{ background: SALES_TABLE_HEADER_BG }}>
              <tr>
                <th
                  className={`${FIN_TABLE_CELL_CLASS} text-left font-semibold text-xs align-bottom`}
                  style={{ color: '#475569', background: SALES_TABLE_HEADER_BG, verticalAlign: 'bottom', minWidth: 140 }}
                >
                  {partnerCol}
                </th>
                {BUCKET_COLS.map(c => (
                  <SalesFinHeader key={c.key} label={c.label} />
                ))}
                <SalesFinHeader label={totalColLabel} />
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={8} className={`${FIN_TABLE_CELL_CLASS} py-8 text-center text-xs`} style={{ color: '#94A3B8' }}>
                    Loading…
                  </td>
                </tr>
              ) : grouped.sections.length === 0 ? (
                <tr>
                  <td colSpan={8} className={`${FIN_TABLE_CELL_CLASS} py-8 text-center text-xs`} style={{ color: '#94A3B8' }}>
                    No open items
                  </td>
                </tr>
              ) : (
                <>
                  {grouped.sections.flatMap(section => [
                    <tr key={`${section.bandLabel}-hdr`} style={L1_ROW_STYLE}>
                      <SalesLabelCell label={section.bandLabel} bold />
                      {BUCKET_COLS.map(c => (
                        <SalesValCell key={c.key} value={section.subtotal[c.key] as number} bold />
                      ))}
                      <SalesValCell value={section.subtotal.total} bold />
                    </tr>,
                    ...section.items.map(item => (
                      <tr key={`${section.bandLabel}-${item.label}`} style={DETAIL_ROW_STYLE}>
                        <td
                          className={`${FIN_TABLE_CELL_CLASS} text-left max-w-[12rem] truncate whitespace-nowrap pl-4`}
                          style={{ color: '#334155', fontSize: '0.68rem', fontWeight: 500, background: '#FFFFFF' }}
                          title={item.label}
                        >
                          {item.label}
                        </td>
                        {BUCKET_COLS.map(c => (
                          <SalesValCell key={c.key} value={item[c.key] as number} />
                        ))}
                        <SalesValCell value={item.total} />
                      </tr>
                    )),
                  ])}
                  <tr style={L1_ROW_STYLE}>
                    <SalesLabelCell label={totalColLabel} bold />
                    {BUCKET_COLS.map(c => (
                      <SalesValCell key={c.key} value={grouped.grand[c.key] as number} bold />
                    ))}
                    <SalesValCell value={grouped.grand.total} bold />
                  </tr>
                </>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
