import type { FinancialStatementColLabels, FinancialsOverviewSection, OverviewMetricRow } from '../../../lib/api'
import { fmtKpi, fmtPct } from '../../../lib/fmt'
import { plSectionHeadingStyle } from '../pl-two-view/plReportSectionHeadings'
import { TwoLineHeader } from '../pl-two-view/plTableCore'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_VALUE_FONT } from '../statement-two-view/finReportLayout'

const KPI_BG = '#F8FAFC'
const ROW_BG = '#FFFFFF'
/** CM column tint (matches report / Excel #F0F4F8). */
const CM_COL_BG = '#F0F4F8'

const SECTION_LABEL_EN: Record<string, string> = {
  Ertragslage: 'Earnings',
  Vermögenslage: 'Balance sheet',
  Finanzlage: 'Cash flow',
}

function sectionLabel(label: string): string {
  return SECTION_LABEL_EN[label] ?? label
}

type Props = {
  sections: FinancialsOverviewSection[]
  colLabels: FinancialStatementColLabels
}

function formatValue(
  row: OverviewMetricRow,
  key: 'pm' | 'cm' | 'ytd' | 'plan_cm' | 'mtd',
): string {
  const v = row.amounts[key]
  if (v == null || row.row_kind === 'section_header' || row.row_kind === 'kpi_header') return ''
  if (row.unit === 'pct') return fmtPct(v)
  if (row.unit === 'ratio') return v.toLocaleString('de-DE', { maximumFractionDigits: 2 })
  return fmtKpi(v)
}

function formatDelta(row: OverviewMetricRow): string {
  if (row.row_kind === 'section_header' || row.row_kind === 'kpi_header') return ''
  const v = row.deltas.mom
  if (v == null) return '—'
  if (row.unit === 'pct' || row.unit === 'ratio') {
    const sign = v > 0 ? '+' : ''
    return `${sign}${v.toLocaleString('de-DE', { maximumFractionDigits: 1 })}${row.unit === 'pct' ? ' pp' : ''}`
  }
  return fmtKpi(v)
}

function deltaColor(row: OverviewMetricRow): string {
  const v = row.deltas.mom ?? 0
  if (v === 0) return '#94A3B8'
  const good = v > 0
  const looksGood = row.invert_delta ? !good : good
  return looksGood ? '#059669' : '#DC2626'
}

function cellBackground(
  rowKind: OverviewMetricRow['row_kind'],
  colKey: 'label' | 'pm' | 'cm' | 'delta' | 'plan_cm' | 'ytd' | 'mtd',
): string {
  const isKpi = rowKind === 'kpi' || rowKind === 'kpi_header'
  const rowBg = isKpi ? KPI_BG : ROW_BG
  if (colKey === 'cm') return CM_COL_BG
  return rowBg
}

function ValueCell({
  row,
  colKey,
  highlighted,
}: {
  row: OverviewMetricRow
  colKey: 'pm' | 'cm' | 'ytd' | 'plan_cm' | 'mtd'
  highlighted?: boolean
}) {
  const bg = cellBackground(row.row_kind, colKey === 'cm' ? 'cm' : colKey)
  const text = formatValue(row, colKey)
  if (!text) return <td className={FIN_TABLE_CELL_CLASS} style={{ background: bg }} />
  const isCm = colKey === 'cm'
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-right tabular-nums whitespace-nowrap`}
      style={{
        fontSize: FIN_TABLE_VALUE_FONT,
        color: isCm || highlighted ? '#0F172A' : '#475569',
        fontWeight: isCm ? 600 : 400,
        background: bg,
      }}
    >
      {text}
    </td>
  )
}

function renderRow(row: OverviewMetricRow, trailingKey: 'mtd' | 'ytd') {
  if (row.row_kind === 'section_header') {
    return (
      <tr key={row.id} style={{ background: ROW_BG }}>
        <td
          colSpan={6}
          className="px-3 pt-4 pb-1"
          style={{ background: ROW_BG, borderTop: '1px solid #E2E8F0' }}
        >
          <span style={plSectionHeadingStyle}>{sectionLabel(row.label)}</span>
        </td>
      </tr>
    )
  }

  if (row.row_kind === 'kpi_header') {
    const kpiLabel = row.label === 'KPIS' ? 'KPIs' : row.label
    return (
      <tr key={row.id} style={{ background: KPI_BG }}>
        <td
          className={`${FIN_TABLE_CELL_CLASS} text-left`}
          style={{
            fontSize: FIN_TABLE_VALUE_FONT,
            color: '#64748B',
            background: KPI_BG,
            fontWeight: 600,
            paddingLeft: 12,
          }}
        >
          {kpiLabel}
        </td>
        <td className={FIN_TABLE_CELL_CLASS} style={{ background: KPI_BG }} />
        <td className={FIN_TABLE_CELL_CLASS} style={{ background: CM_COL_BG }} />
        <td className={FIN_TABLE_CELL_CLASS} style={{ background: KPI_BG }} />
        <td className={FIN_TABLE_CELL_CLASS} style={{ background: KPI_BG }} />
        <td className={FIN_TABLE_CELL_CLASS} style={{ background: KPI_BG }} />
      </tr>
    )
  }

  const isKpi = row.row_kind === 'kpi'

  return (
    <tr
      key={row.id}
      style={{
        background: isKpi ? KPI_BG : ROW_BG,
        borderBottom: isKpi ? undefined : '1px solid #F1F5F9',
      }}
    >
      <td
        className={`${FIN_TABLE_CELL_CLASS} text-left max-w-[220px]`}
        style={{
          fontSize: FIN_TABLE_VALUE_FONT,
          color: '#334155',
          fontWeight: isKpi ? 500 : 400,
          fontStyle: isKpi ? 'italic' : 'normal',
          paddingLeft: isKpi ? 20 : 12,
          background: cellBackground(row.row_kind, 'label'),
        }}
      >
        {row.label}
      </td>
      <ValueCell row={row} colKey="pm" />
      <ValueCell row={row} colKey="cm" highlighted />
      <td
        className={`${FIN_TABLE_CELL_CLASS} text-right tabular-nums whitespace-nowrap`}
        style={{
          fontSize: FIN_TABLE_VALUE_FONT,
          color: deltaColor(row),
          fontWeight: 500,
          background: cellBackground(row.row_kind, 'delta'),
        }}
      >
        {formatDelta(row)}
      </td>
      <ValueCell row={row} colKey="plan_cm" />
      <ValueCell row={row} colKey={trailingKey} />
    </tr>
  )
}

export default function ExecutiveSummaryTable({ sections, colLabels }: Props) {
  const allRows = sections.flatMap(s => s.rows)
  const isWeek = Boolean(colLabels.mtd)
  const priorSub = isWeek ? 'Prior week' : 'Prior month'
  const currentSub = isWeek ? 'Current week' : 'Current month'
  const trailingKey: 'mtd' | 'ytd' = isWeek ? 'mtd' : 'ytd'
  const trailingHdr = isWeek ? (colLabels.mtd ?? 'MTD') : colLabels.ytd
  const trailingSub = isWeek ? 'Month to date' : 'Year to date'
  const planHdr = colLabels.plan_cm ?? `Plan ${colLabels.cm}`
  const deltaHdr = isWeek ? 'Δ WoW' : 'Δ MoM'

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ border: '1px solid #E2E8F0', background: ROW_BG }}
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-xs min-w-[640px]" style={{ background: ROW_BG }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #E2E8F0', background: KPI_BG }}>
              <th
                className="px-3 py-2.5 text-left font-semibold sticky left-0 z-10"
                style={{ color: '#475569', background: KPI_BG, minWidth: 200 }}
              >
                EURk
              </th>
              <TwoLineHeader line1={colLabels.pm} line2={priorSub} />
              <th
                className="px-2 py-2 text-right font-semibold whitespace-nowrap"
                style={{ color: '#475569', verticalAlign: 'bottom', background: CM_COL_BG }}
              >
                <span className="block leading-tight text-xs">{colLabels.cm}</span>
                <span
                  className="block leading-tight text-[0.65rem] font-normal mt-0.5"
                  style={{ color: '#94A3B8' }}
                >
                  {currentSub}
                </span>
              </th>
              <TwoLineHeader line1={deltaHdr} line2={`${colLabels.cm} − ${colLabels.pm}`} />
              <TwoLineHeader line1={planHdr} line2="Month budget" />
              <TwoLineHeader line1={trailingHdr} line2={trailingSub} />
            </tr>
          </thead>
          <tbody style={{ background: ROW_BG }}>
            {allRows.map(row => renderRow(row, trailingKey))}
          </tbody>
        </table>
      </div>
      <div
        className="px-3 py-2 text-[10px] border-t"
        style={{ color: '#94A3B8', borderColor: '#E2E8F0', background: ROW_BG }}
      >
        KPI rows in italics · Percentages and ratios as shown · Balance sheet and working capital at period end
      </div>
    </div>
  )
}
