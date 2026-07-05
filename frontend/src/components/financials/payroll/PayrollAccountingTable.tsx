import { useCallback, useMemo, useState } from 'react'
import type { PersonnelAccountingResponse, PersonnelTableRow } from '../../../lib/api'
import { fmtChartKpi, fmtPct } from '../../../lib/fmt'
import { DeltaCell, ExpandChevron, TwoLineHeader } from '../../financials/pl-two-view/plTableCore'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_VALUE_FONT } from '../statement-two-view/finReportLayout'
import { formatPayrollValueCell, isTopSectionHeader } from './payrollNarrative'
import type { PayrollColumnDef } from './payrollColumnRegistry'

const HEADER_BG = '#F8FAFC'
const SUBTOTAL_BG = '#F8FAFC'
const HIGHLIGHT_BG = 'rgba(30,58,95,0.04)'
const KPI_ITALIC = '#64748B'

export type DisplayCol =
  | { kind: 'snapshot'; key: string }
  | { kind: 'delta'; key: string; from: string; to: string; label: string }

type Props = {
  data: PersonnelAccountingResponse
  displayCols: DisplayCol[]
  columns?: PayrollColumnDef[]
}

function cellDelta(row: PersonnelTableRow, from: string, to: string): number | null {
  const a = row.amounts?.[from]
  const b = row.amounts?.[to]
  if (a == null || b == null || Number.isNaN(a) || Number.isNaN(b)) return null
  return b - a
}

export function buildDisplayCols(
  data: PersonnelAccountingResponse,
  columns: PayrollColumnDef[],
  visibleDates: Set<string>,
): DisplayCol[] {
  const snapKeys = (data.col_keys ?? data.col_dates ?? []).filter(k => {
    const datePart = k.split('|')[0]
    return visibleDates.has(datePart)
  })
  const out: DisplayCol[] = []
  for (const col of columns.filter(c => c.visible)) {
    if (col.kind === 'snapshot' && col.snapshotDate && snapKeys.includes(col.snapshotDate)) {
      out.push({ kind: 'snapshot', key: col.snapshotDate })
    } else if (col.kind === 'delta' && col.deltaFrom && col.deltaTo) {
      if (visibleDates.has(col.deltaFrom) && visibleDates.has(col.deltaTo)) {
        out.push({
          kind: 'delta',
          key: col.id,
          from: col.deltaFrom,
          to: col.deltaTo,
          label: col.label,
        })
      }
    }
  }
  if (out.length) return out
  return snapKeys.map(k => ({ kind: 'snapshot' as const, key: k }))
}

function formatCell(value: number | null | undefined, unit: string): string {
  if (value == null || Number.isNaN(value)) return '—'
  if (unit === 'pct') return fmtPct(value)
  if (unit === 'count') return Math.round(value).toLocaleString('de-DE')
  return fmtChartKpi(value)
}

export default function PayrollAccountingTable({ data, displayCols }: Props) {
  const colGroups = data.col_groups ?? {}
  const snapCols = displayCols.filter((c): c is DisplayCol & { kind: 'snapshot' } => c.kind === 'snapshot')
  const anchorKey = snapCols[snapCols.length - 1]?.key
  const hasGroups = snapCols.some(c => colGroups[c.key])

  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())

  const toggle = useCallback((id: string) => {
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const hiddenIndices = useMemo(() => {
    const hidden = new Set<number>()
    for (let i = 0; i < data.rows.length; i++) {
      const row = data.rows[i]
      if (row.row_kind !== 'section_header' || !collapsed.has(row.id)) continue
      const d = row.depth ?? 0
      for (let j = i + 1; j < data.rows.length; j++) {
        const r = data.rows[j]
        if (r.row_kind === 'subtotal' && (r.depth ?? 0) === d) break
        hidden.add(j)
      }
    }
    return hidden
  }, [data.rows, collapsed])

  const visibleRows = useMemo(
    () => data.rows.map((row, i) => ({ row, i })).filter(({ i }) => !hiddenIndices.has(i)),
    [data.rows, hiddenIndices],
  )

  const maxDelta = useMemo(() => {
    let m = 1
    for (const { row } of visibleRows) {
      if (row.row_kind === 'kpi_header' || row.row_kind === 'kpi') continue
      for (const col of displayCols) {
        if (col.kind !== 'delta') continue
        const d = cellDelta(row, col.from, col.to)
        if (d != null) m = Math.max(m, Math.abs(d))
      }
    }
    return m
  }, [visibleRows, displayCols])

  const groupSpans = useMemo(() => {
    if (!hasGroups) return []
    const spans: Array<{ label: string; span: number }> = []
    let i = 0
    while (i < snapCols.length) {
      const g = colGroups[snapCols[i].key]
      let span = 1
      while (i + span < snapCols.length && colGroups[snapCols[i + span].key] === g) span++
      spans.push({ label: g ?? '', span })
      i += span
    }
    return spans
  }, [snapCols, colGroups, hasGroups])

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs" style={{ minWidth: 720 }}>
        <thead>
          {hasGroups && (
            <tr style={{ borderBottom: '1px solid #E2E8F0', background: HEADER_BG }}>
              <th className="px-2 py-1" />
              {groupSpans.map((g, i) => (
                <th
                  key={`${g.label}-${i}`}
                  colSpan={g.span}
                  className="px-2 py-1 text-right font-semibold text-xs"
                  style={{ color: '#1E3A5F' }}
                >
                  {g.label}
                </th>
              ))}
              {displayCols.some(c => c.kind === 'delta') && <th className="px-2 py-1" />}
            </tr>
          )}
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: HEADER_BG }}>
            <th className="px-2 py-2 text-left font-semibold" style={{ color: '#475569' }}>EURk</th>
            {displayCols.map(col => {
              if (col.kind === 'delta') {
                return (
                  <th
                    key={col.key}
                    className="px-2 py-2 text-right font-semibold whitespace-nowrap text-xs"
                    style={{ color: '#475569', fontSize: FIN_TABLE_VALUE_FONT }}
                  >
                    {col.label}
                  </th>
                )
              }
              const k = col.key
              const group = colGroups[k]
              const line1 = group ?? data.col_labels[k] ?? k
              const line2 = group ? (data.col_labels[k] ?? undefined) : undefined
              return (
                <TwoLineHeader
                  key={k}
                  line1={line1}
                  line2={line2}
                  highlighted={k === anchorKey}
                />
              )
            })}
          </tr>
        </thead>
        <tbody>
          {visibleRows.map(({ row }) => (
            <PayrollRow
              key={row.id}
              row={row}
              displayCols={displayCols}
              anchorKey={anchorKey}
              maxDelta={maxDelta}
              collapsed={collapsed.has(row.id)}
              onToggle={() => toggle(row.id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function PayrollRow({
  row,
  displayCols,
  anchorKey,
  maxDelta,
  collapsed,
  onToggle,
}: {
  row: PersonnelTableRow
  displayCols: DisplayCol[]
  anchorKey: string | undefined
  maxDelta: number
  collapsed: boolean
  onToggle: () => void
}) {
  const isSection = row.row_kind === 'section_header'
  const isSubtotal = row.row_kind === 'subtotal'
  const isTotal = row.row_kind === 'total'
  const isKpiHeader = row.row_kind === 'kpi_header'
  const isKpi = row.row_kind === 'kpi'
  const depth = row.depth ?? 0
  const indent = 8 + depth * 16

  if (isKpiHeader) {
    return (
      <tr style={{ background: HEADER_BG }}>
        <td
          className={`${FIN_TABLE_CELL_CLASS} text-left`}
          style={{
            color: '#1E3A5F',
            fontStyle: 'italic',
            fontWeight: 600,
            fontSize: FIN_TABLE_VALUE_FONT,
            background: HEADER_BG,
          }}
        >
          {row.label}
        </td>
        {displayCols.map(col => {
          if (col.kind === 'delta') {
            return <td key={col.key} className={FIN_TABLE_CELL_CLASS} style={{ background: HEADER_BG }} />
          }
          const highlighted = col.key === anchorKey
          return (
            <td
              key={col.key}
              className={FIN_TABLE_CELL_CLASS}
              style={{ background: highlighted ? HIGHLIGHT_BG : HEADER_BG }}
            />
          )
        })}
      </tr>
    )
  }

  const bg = isSubtotal ? SUBTOTAL_BG : isKpi ? HEADER_BG : '#fff'
  const italic = isKpi
  const labelColor = isKpi ? KPI_ITALIC : isSection || isTotal ? '#1E3A5F' : isSubtotal ? '#0F172A' : '#475569'

  return (
    <tr style={{ background: bg }}>
      <td
        className={`${FIN_TABLE_CELL_CLASS} text-left`}
        style={{
          paddingLeft: indent,
          fontWeight: isTotal || isSubtotal || (isSection && depth != null) ? 600 : 400,
          fontStyle: italic ? 'italic' : undefined,
          color: labelColor,
          fontSize: FIN_TABLE_VALUE_FONT,
        }}
      >
        <span className="inline-flex items-center gap-0.5">
          {isSection && row.depth != null && <ExpandChevron open={!collapsed} onToggle={onToggle} />}
          {isSection && row.depth == null ? (
            <span className="font-semibold" style={{ color: '#1E3A5F' }}>{row.label}</span>
          ) : (
            row.label
          )}
        </span>
      </td>
      {displayCols.map(col => {
        if (col.kind === 'delta') {
          const delta = cellDelta(row, col.from, col.to)
          if (isKpi || delta == null) {
            return (
              <td
                key={col.key}
                className={`${FIN_TABLE_CELL_CLASS} text-right`}
                style={{ fontSize: FIN_TABLE_VALUE_FONT, color: '#CBD5E1' }}
              >
                {isTopSectionHeader(row) ? '' : '—'}
              </td>
            )
          }
          return (
            <DeltaCell
              key={col.key}
              value={delta}
              maxAbs={maxDelta}
              invert={false}
              isPct={row.unit === 'pct'}
              compact
            />
          )
        }
        const k = col.key
        return (
          <td
            key={k}
            className={`${FIN_TABLE_CELL_CLASS} text-right tabular-nums whitespace-nowrap`}
            style={{
              fontSize: FIN_TABLE_VALUE_FONT,
              fontWeight: isTotal || isSubtotal ? 600 : 400,
              fontStyle: italic ? 'italic' : undefined,
              color: isKpi ? KPI_ITALIC : '#111827',
              background: k === anchorKey ? HIGHLIGHT_BG : undefined,
            }}
          >
            {formatPayrollValueCell(row, row.amounts?.[k], row.unit, formatCell)}
          </td>
        )
      })}
    </tr>
  )
}
