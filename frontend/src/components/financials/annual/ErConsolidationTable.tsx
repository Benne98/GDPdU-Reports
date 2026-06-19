import { ConsolidationResponse, ConsolidationRow } from '../../../lib/api'
import { fmtKpi } from '../../../lib/fmt'

interface ErConsolidationTableProps {
  data: ConsolidationResponse | null
  loading: boolean
}

function ValCell({ value, bold, highlighted }: { value: number; bold?: boolean; highlighted?: boolean }) {
  return (
    <td
      className="px-2 py-1.5 text-right whitespace-nowrap tabular-nums"
      style={{
        fontSize: '0.7rem',
        fontWeight: bold ? 600 : 400,
        background: highlighted ? 'rgba(30,58,95,0.06)' : undefined,
        color: '#111827',
      }}
    >
      {fmtKpi(value)}
    </td>
  )
}

function renderRow(
  row: ConsolidationRow,
  entityCodes: string[],
  depth: number,
): JSX.Element {
  const isTitle    = row.row_kind === 'title'
  const isSubtotal = row.row_kind === 'subtotal'
  const isBold     = row.is_bold || isSubtotal

  const colSpan = entityCodes.length + 2 // label + entities + group

  if (isTitle) {
    return (
      <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '1px solid #E2E8F0' }}>
        <td
          colSpan={colSpan}
          className="px-3 py-1.5 text-[11px] font-bold uppercase tracking-wide"
          style={{ color: '#1E3A5F' }}
        >
          {row.label}
        </td>
      </tr>
    )
  }

  return (
    <tr
      key={row.id}
      style={{
        borderBottom: '1px solid #E2E8F0',
        borderTop: isSubtotal ? '2px solid #E2E8F0' : undefined,
        background: isSubtotal ? '#F8FAFC' : undefined,
      }}
    >
      <td
        className="py-1.5 text-left whitespace-nowrap text-[11px]"
        style={{
          paddingLeft: 12 + depth * 14,
          paddingRight: 12,
          fontWeight: isBold ? 600 : 400,
          color: '#111827',
          minWidth: 220,
        }}
      >
        {row.label}
      </td>

      {entityCodes.map(code => (
        <ValCell
          key={`${row.id}-${code}`}
          value={row.entity_amounts[code] ?? 0}
          bold={isBold}
        />
      ))}

      <ValCell value={row.consolidation} bold={isBold} highlighted />
    </tr>
  )
}

export default function ErConsolidationTable({ data, loading }: ErConsolidationTableProps) {
  if (loading) {
    return (
      <div
        className="rounded-xl mt-4 p-8 text-center text-sm"
        style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}
      >
        Loading…
      </div>
    )
  }

  if (!data || !data.rows.length) {
    return (
      <div
        className="rounded-xl mt-4 p-8 text-center text-sm"
        style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#94A3B8' }}
      >
        No consolidation data available.
      </div>
    )
  }

  const entityCodes = data.entities.map(e => e.code)
  const entityByCode = Object.fromEntries(data.entities.map(e => [e.code, e.label]))

  return (
    <div
      className="rounded-xl overflow-x-auto mt-4"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
    >
      <div
        className="px-4 pt-4 pb-3 flex items-start justify-between gap-3"
        style={{ borderBottom: '1px solid #F1F5F9' }}
      >
        <div>
          <span className="text-sm font-semibold" style={{ color: '#111827' }}>
            Income statement (consolidated) — entity breakdown
          </span>
          <div className="flex items-center gap-2 mt-1">
            <span
              className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium"
              style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
            >
              {data.col_label}
            </span>
          </div>
          <p className="text-xs mt-1" style={{ color: '#94A3B8' }}>
            Full fiscal year per entity, values in EURk
          </p>
        </div>
      </div>

      <table className="w-full border-collapse text-[11px]">
        <thead>
          <tr
            style={{
              borderBottom: '2px solid #E2E8F0',
              background: '#F8FAFC',
              verticalAlign: 'bottom',
            }}
          >
            <th
              className="px-3 py-2 text-left font-semibold"
              style={{ color: '#475569', minWidth: 220 }}
            >
              EURk
            </th>
            {entityCodes.map(code => (
              <th
                key={code}
                className="px-2 py-2 text-right font-semibold whitespace-nowrap"
                style={{ color: '#475569' }}
              >
                {entityByCode[code] ?? code}
              </th>
            ))}
            <th
              className="px-2 py-2 text-right font-semibold whitespace-nowrap"
              style={{ color: '#1E3A5F' }}
            >
              Group
            </th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map(row => renderRow(row, entityCodes, 0))}
        </tbody>
      </table>
    </div>
  )
}
