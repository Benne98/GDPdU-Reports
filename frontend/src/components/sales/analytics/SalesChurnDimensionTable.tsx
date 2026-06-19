import { useMemo } from 'react'
import type { SalesChurnBridge, SalesChurnBridgeResponse } from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'
import {
  buildChurnTableColumns,
  churnCellValue,
  churnGridTemplate,
  type ChurnTableColumn,
} from './churnBridgeLayout'

const TOTAL_COL_BG = 'rgba(30, 58, 95, 0.04)'
const CELL_WHITE = '#FFFFFF'

function cellBg(col: ChurnTableColumn): string {
  if (col.kind === 'total') return TOTAL_COL_BG
  return CELL_WHITE
}

function formatEffect(v: number): string {
  if (Math.abs(v) < 0.05) return '—'
  return (v >= 0 ? '+' : '') + fmtChartKpi(v)
}

function effectColor(v: number): string {
  if (Math.abs(v) < 0.05) return '#94A3B8'
  return v >= 0 ? '#10B981' : '#DC2626'
}

type Props = {
  data: SalesChurnBridgeResponse
  dimLabel: string
  loading?: boolean
}

function ChurnGridRow({
  columns,
  gridTemplate,
  periodCount: _periodCount,
  dimValue,
  periodTotals,
  bridges,
  bold,
}: {
  columns: ChurnTableColumn[]
  gridTemplate: string
  periodCount: number
  dimValue: string
  periodTotals: number[]
  bridges: Array<Pick<SalesChurnBridge, 'new' | 'upsell' | 'cross_sell' | 'downsell' | 'lost'>>
  bold?: boolean
}) {
  return (
    <div
      className="grid border-b last:border-b-0"
      style={{ gridTemplateColumns: gridTemplate, borderColor: '#F1F5F9' }}
    >
      {columns.map(col => {
        const raw = churnCellValue(col, dimValue, periodTotals, bridges)
        const bg = cellBg(col)
        if (col.kind === 'dim') {
          return (
            <div
              key={col.key}
              className="px-3 py-2 text-left truncate text-xs"
              style={{
                color: bold ? '#1E3A5F' : '#334155',
                fontWeight: bold ? 600 : 500,
                background: bg,
              }}
              title={String(raw)}
            >
              {raw}
            </div>
          )
        }
        if (col.kind === 'total') {
          const n = typeof raw === 'number' ? raw : 0
          return (
            <div
              key={col.key}
              className="px-2 py-2 text-right tabular-nums text-xs whitespace-nowrap"
              style={{
                color: bold ? '#1E3A5F' : '#111827',
                fontWeight: bold ? 600 : 400,
                background: bg,
              }}
            >
              {Math.abs(n) < 0.05 ? '—' : fmtChartKpi(n)}
            </div>
          )
        }
        const n = typeof raw === 'number' ? raw : 0
        return (
          <div
            key={col.key}
            className="px-2 py-2 text-right tabular-nums text-xs whitespace-nowrap"
            style={{ color: effectColor(n), fontWeight: bold ? 600 : 400, background: bg }}
          >
            {formatEffect(n)}
          </div>
        )
      })}
    </div>
  )
}

export default function SalesChurnDimensionTable({ data, dimLabel, loading }: Props) {
  const columns = useMemo(() => buildChurnTableColumns(data), [data])
  const gridTemplate = useMemo(() => churnGridTemplate(columns), [columns])
  const periodCount = data.periods?.length ?? 0
  const rows = data.table_rows ?? []

  if (loading) {
    return (
      <div className="px-5 py-8 text-center text-xs" style={{ color: '#94A3B8' }}>
        Loading dimension breakdown…
      </div>
    )
  }

  if (!rows.length) {
    return (
      <div className="px-5 py-6 text-center text-xs" style={{ color: '#94A3B8' }}>
        No dimensional breakdown for this period.
      </div>
    )
  }

  return (
    <div className="px-3 pb-4">
      <p className="text-[10px] font-semibold uppercase tracking-wider px-2 mb-2" style={{ color: '#94A3B8' }}>
        By {dimLabel} · kEUR
      </p>
      <div className="rounded-lg overflow-hidden border" style={{ borderColor: '#E2E8F0' }}>
        <div
          className="grid border-b"
          style={{ gridTemplateColumns: gridTemplate, borderColor: '#E2E8F0', background: CELL_WHITE }}
        >
          {columns.map(col => (
            <div
              key={col.key}
              className="px-2 py-2 text-xs font-semibold whitespace-nowrap"
              style={{
                textAlign: col.kind === 'dim' ? 'left' : 'right',
                color: col.kind === 'dim' ? '#475569' : '#64748B',
                background: cellBg(col),
                paddingLeft: col.kind === 'dim' ? 12 : 8,
              }}
            >
              {col.kind === 'dim' ? dimLabel : col.label}
            </div>
          ))}
        </div>
        {rows.map(row => (
          <ChurnGridRow
            key={row.dim_value}
            columns={columns}
            gridTemplate={gridTemplate}
            periodCount={periodCount}
            dimValue={row.dim_value}
            periodTotals={row.period_totals}
            bridges={row.bridges}
          />
        ))}
        <ChurnGridRow
          columns={columns}
          gridTemplate={gridTemplate}
          periodCount={periodCount}
          dimValue="Total"
          periodTotals={data.period_totals}
          bridges={data.bridges}
          bold
        />
      </div>
    </div>
  )
}
