import type {
  EntityBreakdownEntity,
  EntityBreakdownRow,
  OverviewUnit,
} from '../../../lib/api'
import { fmtKpi, fmtPct } from '../../../lib/fmt'
import { plSectionHeadingStyle } from '../pl-two-view/plReportSectionHeadings'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_VALUE_FONT } from '../statement-two-view/finReportLayout'

const KPI_BG = '#F8FAFC'
const ROW_BG = '#FFFFFF'

const SECTION_LABEL_EN: Record<string, string> = {
  Ertragslage: 'Earnings',
  Vermögenslage: 'Balance sheet',
  Finanzlage: 'Cash flow',
}

function sectionLabel(label: string): string {
  return SECTION_LABEL_EN[label] ?? label
}

type Props = {
  entities: EntityBreakdownEntity[]
  rows: EntityBreakdownRow[]
  cmLabel: string
}

function formatCm(val: number | null | undefined, unit: OverviewUnit): string {
  if (val == null) return '—'
  if (unit === 'pct') return fmtPct(val)
  if (unit === 'ratio') return val.toLocaleString('de-DE', { maximumFractionDigits: 2 })
  return fmtKpi(val)
}

function cellBg(rowKind: EntityBreakdownRow['row_kind']): string {
  return rowKind === 'kpi' || rowKind === 'kpi_header' ? KPI_BG : ROW_BG
}

function entityLabel(ent: EntityBreakdownEntity): string {
  return ent.display_name?.trim() || ent.name
}

function renderRow(row: EntityBreakdownRow, entities: EntityBreakdownEntity[]) {
  if (row.row_kind === 'section_header') {
    return (
      <tr key={row.id} style={{ background: ROW_BG }}>
        <td
          colSpan={entities.length + 1}
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
    const bg = KPI_BG
    return (
      <tr key={row.id} style={{ background: bg }}>
        <td
          className={`${FIN_TABLE_CELL_CLASS} text-left`}
          style={{
            fontSize: FIN_TABLE_VALUE_FONT,
            color: '#64748B',
            background: bg,
            fontWeight: 600,
            paddingLeft: 12,
          }}
        >
          {kpiLabel}
        </td>
        {entities.map(ent => (
          <td
            key={`${row.id}-${ent.code}`}
            className={FIN_TABLE_CELL_CLASS}
            style={{ background: bg }}
          />
        ))}
      </tr>
    )
  }

  const isKpi = row.row_kind === 'kpi'
  const bg = cellBg(row.row_kind)

  return (
    <tr
      key={row.id}
      style={{
        background: bg,
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
          background: bg,
        }}
      >
        {row.label}
      </td>
      {entities.map(ent => (
        <td
          key={`${row.id}-${ent.code}`}
          className={`${FIN_TABLE_CELL_CLASS} text-right tabular-nums whitespace-nowrap`}
          style={{
            fontSize: FIN_TABLE_VALUE_FONT,
            color: '#0F172A',
            fontWeight: 400,
            background: bg,
          }}
        >
          {formatCm(row.cm_by_entity[ent.code], row.unit)}
        </td>
      ))}
    </tr>
  )
}

export default function EntityBreakdownTable({ entities, rows, cmLabel }: Props) {
  if (!entities.length) {
    return (
      <p className="text-xs m-0" style={{ color: '#94A3B8' }}>
        No legal entities available for breakdown.
      </p>
    )
  }

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ border: '1px solid #E2E8F0', background: ROW_BG }}
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-xs min-w-[480px]" style={{ background: ROW_BG }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #E2E8F0', background: KPI_BG }}>
              <th
                className="px-3 py-2.5 text-left font-semibold"
                style={{ color: '#475569', background: KPI_BG, minWidth: 180 }}
              >
                EURk
              </th>
              {entities.map(ent => (
                <th
                  key={ent.code}
                  className="px-2 py-2.5 text-right font-semibold whitespace-nowrap"
                  style={{ color: '#475569', background: KPI_BG }}
                  title={ent.name}
                >
                  {entityLabel(ent)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>{rows.map(row => renderRow(row, entities))}</tbody>
        </table>
      </div>
      <div
        className="px-3 py-2 text-[10px] border-t"
        style={{ color: '#94A3B8', borderColor: '#E2E8F0', background: ROW_BG }}
      >
        Current month ({cmLabel}) by legal entity · KPI rows in italics
      </div>
    </div>
  )
}
