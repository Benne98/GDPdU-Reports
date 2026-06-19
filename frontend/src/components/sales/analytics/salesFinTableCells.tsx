import { DeltaBar } from '../../financials/pl-two-view/plTableCore'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_VALUE_FONT } from '../../financials/statement-two-view/finReportLayout'
import { fmtChartKpi } from '../../../lib/fmt'
import { isSalesHighlightColumn } from './salesColumnKinds'

export const CM_HIGHLIGHT_BG = 'rgba(30,58,95,0.04)'

/** Opaque header backgrounds — sticky thead must not use rgba or body cells show through. */
export const SALES_TABLE_HEADER_BG = '#F8FAFC'
export const SALES_TABLE_HEADER_HIGHLIGHT_BG = '#EFF2F6'

function deltaColor(value: number): string {
  if (value === 0) return '#94A3B8'
  return value > 0 ? '#10B981' : '#DC2626'
}

export function SalesFinHeader({
  label,
  align = 'right',
  highlighted = false,
}: {
  label: string
  align?: 'left' | 'right'
  highlighted?: boolean
}) {
  return (
    <th
      className={`${FIN_TABLE_CELL_CLASS} font-semibold whitespace-nowrap text-xs`}
      style={{
        color: '#475569',
        textAlign: align,
        verticalAlign: 'bottom',
        background: highlighted ? SALES_TABLE_HEADER_HIGHLIGHT_BG : SALES_TABLE_HEADER_BG,
      }}
    >
      {label}
    </th>
  )
}

export function SalesRankCell({ rank, bold = false }: { rank: number; bold?: boolean }) {
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-right tabular-nums whitespace-nowrap`}
      style={{
        color: '#64748B',
        fontSize: FIN_TABLE_VALUE_FONT,
        fontWeight: bold ? 600 : 400,
        width: 32,
      }}
    >
      {rank}
    </td>
  )
}

export function SalesLabelCell({
  label,
  bold = false,
  title,
}: {
  label: string
  bold?: boolean
  title?: string
}) {
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-left max-w-[12rem] truncate whitespace-nowrap`}
      style={{
        color: bold ? '#1E3A5F' : '#334155',
        fontSize: FIN_TABLE_VALUE_FONT,
        fontWeight: bold ? 600 : 500,
      }}
      title={title ?? label}
    >
      {label}
    </td>
  )
}

export function SalesValCell({
  value,
  highlighted = false,
  bold = false,
  isPct = false,
  muted = false,
}: {
  value: number | null | undefined
  highlighted?: boolean
  bold?: boolean
  isPct?: boolean
  muted?: boolean
}) {
  if (value == null && isPct) {
    return (
      <td
        className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums`}
        style={{
          background: highlighted ? CM_HIGHLIGHT_BG : undefined,
          fontSize: FIN_TABLE_VALUE_FONT,
          color: '#94A3B8',
        }}
      >
        —
      </td>
    )
  }
  const n = value ?? 0
  const text = isPct
    ? `${n.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`
    : fmtChartKpi(n)
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums`}
      style={{
        background: highlighted ? CM_HIGHLIGHT_BG : undefined,
        fontWeight: bold ? 600 : 400,
        fontSize: FIN_TABLE_VALUE_FONT,
        color: muted ? '#94A3B8' : '#111827',
      }}
    >
      {text}
    </td>
  )
}

export function SalesDeltaCell({
  value,
  maxAbs,
  bold = false,
}: {
  value: number | null | undefined
  maxAbs: number
  bold?: boolean
}) {
  if (value == null) {
    return (
      <td
        className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums`}
        style={{ fontWeight: bold ? 600 : 400, color: '#94A3B8', fontSize: FIN_TABLE_VALUE_FONT }}
      >
        —
      </td>
    )
  }
  const n = value
  const color = deltaColor(n)
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums`}
      style={{ fontWeight: bold ? 600 : 400 }}
    >
      <span className="flex items-center justify-end gap-1">
        <span style={{ color, fontSize: FIN_TABLE_VALUE_FONT }}>{fmtChartKpi(n)}</span>
        <DeltaBar value={n} maxAbs={maxAbs} />
      </span>
    </td>
  )
}

export function salesHeaderHighlighted(field: string, periodGrain: 'month' | 'week'): boolean {
  return isSalesHighlightColumn(field, periodGrain)
}
