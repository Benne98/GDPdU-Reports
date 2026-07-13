import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { FinancialStatementRow } from '../../../lib/api'
import { fmtKpi, fmtPct, fmtDays } from '../../../lib/fmt'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_CELL_DENSE_CLASS, FIN_TABLE_VALUE_FONT } from '../finReportLayout'

export type ValueCol = 'py_cm' | 'pm' | 'cm' | 'ytd' | 'ytd_py'

export function lastDay(y: number, m: number): string {
  return new Date(y, m, 0).toISOString().slice(0, 10)
}

export function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

export function periodRange(year: number, month: number, col: ValueCol): { from: string; to: string } {
  const pmYear = month === 1 ? year - 1 : year
  const pmMonth = month === 1 ? 12 : month - 1
  switch (col) {
    case 'py_cm':
      return { from: `${year - 1}-${pad2(month)}-01`, to: lastDay(year - 1, month) }
    case 'pm':
      return { from: `${pmYear}-${pad2(pmMonth)}-01`, to: lastDay(pmYear, pmMonth) }
    case 'cm':
      return { from: `${year}-${pad2(month)}-01`, to: lastDay(year, month) }
    case 'ytd':
      return { from: `${year}-01-01`, to: lastDay(year, month) }
    case 'ytd_py':
      return { from: `${year - 1}-01-01`, to: lastDay(year - 1, month) }
  }
}

export function DeltaBar({ value, maxAbs }: { value: number; maxAbs: number }) {
  if (maxAbs === 0) return <span className="inline-block" style={{ width: 28 }} />
  const pct = Math.min((Math.abs(value) / maxAbs) * 100, 100)
  const isPos = value >= 0
  return (
    <span
      className="inline-block align-middle"
      style={{ width: 28, height: 6, background: '#F1F5F9', borderRadius: 2, overflow: 'hidden', flexShrink: 0 }}
    >
      <span
        style={{
          display: 'block',
          height: '100%',
          width: `${pct}%`,
          background: isPos ? '#10B981' : '#DC2626',
          borderRadius: 2,
        }}
      />
    </span>
  )
}

function deltaColor(value: number, invert: boolean): string {
  if (value === 0) return '#94A3B8'
  const good = value > 0
  const looksGood = invert ? !good : good
  return looksGood ? '#10B981' : '#DC2626'
}

export function DeltaCell({
  value,
  maxAbs,
  invert,
  isPct,
  isDays,
  italic,
  compact,
  exportLayout,
  onClick,
}: {
  value: number
  maxAbs: number
  invert: boolean
  isPct?: boolean
  isDays?: boolean
  italic?: boolean
  compact?: boolean
  /** PDF export: stack value above bar with fixed column width */
  exportLayout?: boolean
  onClick?: () => void
}) {
  const color = isPct || isDays
    ? value > 0 ? '#10B981' : value < 0 ? '#DC2626' : '#94A3B8'
    : deltaColor(value, invert)
  const text = isDays
    ? `${value >= 0 ? '+' : ''}${Math.abs(value).toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}`
    : isPct
      ? `${value >= 0 ? '+' : ''}${value.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} PP`
      : fmtKpi(value)
  if (exportLayout) {
    return (
      <td
        className="text-right tabular-nums"
        style={{ padding: '4px 6px', minWidth: 78, verticalAlign: 'middle', cursor: 'default' }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
          <span style={{ color, fontWeight: 500, fontSize: 'inherit', fontStyle: italic ? 'italic' : undefined, whiteSpace: 'nowrap' }}>
            {text}
          </span>
          {!isPct && !isDays && <DeltaBar value={value} maxAbs={maxAbs} />}
        </div>
      </td>
    )
  }

  return (
    <td
      className={`${compact ? FIN_TABLE_CELL_CLASS : 'px-2.5 py-2'} text-right whitespace-nowrap tabular-nums`}
      onClick={onClick}
      style={{ cursor: onClick ? 'pointer' : 'default' }}
    >
      <span className="flex items-center justify-end gap-1">
        <span style={{ color, fontWeight: 500, fontSize: FIN_TABLE_VALUE_FONT, fontStyle: italic ? 'italic' : undefined }}>{text}</span>
        {!isPct && !isDays && <DeltaBar value={value} maxAbs={maxAbs} />}
      </span>
    </td>
  )
}

export function ValCell({
  value,
  highlighted = false,
  bold = false,
  isPct = false,
  isDays = false,
  italic = false,
  compact = false,
  denser = false,
  muted = false,
  onClick,
}: {
  value: number
  highlighted?: boolean
  bold?: boolean
  isPct?: boolean
  isDays?: boolean
  italic?: boolean
  compact?: boolean
  denser?: boolean
  muted?: boolean
  onClick?: () => void
}) {
  const [hovered, setHovered] = useState(false)
  const text = isDays ? fmtDays(value) : isPct ? fmtPct(value) : fmtKpi(value)
  const cellPad = denser ? FIN_TABLE_CELL_DENSE_CLASS : compact ? FIN_TABLE_CELL_CLASS : 'px-2.5 py-2'
  return (
    <td
      className={`${cellPad} text-right whitespace-nowrap tabular-nums`}
      onClick={onClick}
      onMouseEnter={() => onClick && setHovered(true)}
      onMouseLeave={() => onClick && setHovered(false)}
      style={{
        background: highlighted ? 'rgba(30,58,95,0.04)' : undefined,
        cursor: onClick ? 'pointer' : 'default',
        fontWeight: bold ? 600 : 400,
        fontStyle: italic ? 'italic' : undefined,
        fontSize: FIN_TABLE_VALUE_FONT,
        color: muted ? '#94A3B8' : hovered ? '#1E3A5F' : '#111827',
        opacity: muted ? 0.65 : 1,
      }}
    >
      {text}
    </td>
  )
}

export function collectNumericRows(rows: FinancialStatementRow[]): FinancialStatementRow[] {
  const out: FinancialStatementRow[] = []
  function walk(r: FinancialStatementRow) {
    if (r.amounts && r.row_kind !== 'title') out.push(r)
    for (const c of r.children ?? []) walk(c)
    for (const a of r.accounts ?? []) walk(a)
  }
  for (const r of rows) walk(r)
  return out
}

const CM_HIGHLIGHT_BG = 'rgba(30,58,95,0.04)'

/** Shared period-column tint (CM / YTD in P&L, anchor year-end in fixed assets). */
export const PERIOD_HIGHLIGHT_BG = CM_HIGHLIGHT_BG

/** Dark tint only for current-month (CM) and YTD actual columns — not custom month columns. */
export function isReportPeriodHighlightColumn(kind: string): boolean {
  return kind === 'cm' || kind === 'ytd'
}

export function BlankValCell({
  compact = false,
  highlighted = false,
}: {
  compact?: boolean
  highlighted?: boolean
}) {
  return (
    <td
      className={`${compact ? 'px-1.5 py-1' : 'px-2.5 py-2'} text-right whitespace-nowrap`}
      style={{ background: highlighted ? CM_HIGHLIGHT_BG : undefined }}
    />
  )
}

export function TwoLineHeader({
  line1,
  line2,
  highlighted,
  spanBothLines,
}: {
  line1: string
  line2?: string
  highlighted?: boolean
  /** Drop the subheader and let line1 use (and wrap into) both header lines, vertically centred. */
  spanBothLines?: boolean
}) {
  return (
    <th
      className={`px-2 py-2 text-right font-semibold ${spanBothLines ? '' : 'whitespace-nowrap'}`}
      style={{
        color: '#475569',
        verticalAlign: spanBothLines ? 'middle' : 'bottom',
        background: highlighted ? CM_HIGHLIGHT_BG : undefined,
      }}
    >
      <span className="block leading-tight text-xs">{line1}</span>
      {!spanBothLines && line2 && <span className="block leading-tight text-[0.65rem] font-normal mt-0.5" style={{ color: '#94A3B8' }}>{line2}</span>}
    </th>
  )
}

export function headerCellBackground(kind: string): string | undefined {
  return isReportPeriodHighlightColumn(kind) ? CM_HIGHLIGHT_BG : undefined
}

export function ExpandChevron({
  open,
  onToggle,
}: {
  open: boolean
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      onClick={e => {
        e.stopPropagation()
        onToggle()
      }}
      className="p-0.5 rounded shrink-0"
      style={{ color: '#1E3A5F' }}
      aria-expanded={open}
    >
      <ChevronRight
        size={14}
        style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}
      />
    </button>
  )
}
