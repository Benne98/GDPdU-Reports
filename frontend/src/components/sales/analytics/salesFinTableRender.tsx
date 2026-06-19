import type { SalesTopEntity } from '../../../lib/api'
import type { SalesColumnDef } from './salesTableTypes'
import {
  SalesDeltaCell,
  SalesFinHeader,
  SalesLabelCell,
  SalesRankCell,
  SalesValCell,
  salesHeaderHighlighted,
  SALES_TABLE_HEADER_BG,
} from './salesFinTableCells'
import { isDeltaField, salesColumnKind } from './salesColumnKinds'
import type { TopCustomerDisplayRow } from './topCustomersSegment'

function coerceNum(v: unknown): number | null {
  if (typeof v === 'number' && !Number.isNaN(v)) return v
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v)
    return Number.isNaN(n) ? null : n
  }
  return null
}

const DELTA_BASES: Record<string, [keyof SalesTopEntity, keyof SalesTopEntity]> = {
  delta_cm_pm: ['cm', 'pm'],
  delta_cm_py: ['cm', 'py_cm'],
  delta_ytd: ['ytd', 'ytd_py'],
  delta_mtd: ['mtd', 'mtd_py'],
}

/** Resolve delta from API field or compute from base amounts when missing. */
export function resolveDelta(
  row: SalesTopEntity | Record<string, number | null>,
  field: string,
): number | null {
  const rec = row as Record<string, number | null | undefined>
  const direct = rec[field]
  if (typeof direct === 'number' && !Number.isNaN(direct)) return direct

  const pair = DELTA_BASES[field]
  if (!pair) return null
  const [aKey, bKey] = pair
  const a = rec[aKey as string]
  const b = rec[bKey as string]
  const hasA = typeof a === 'number'
  const hasB = typeof b === 'number'
  if (!hasA && !hasB) return null
  return Math.round(((a ?? 0) - (b ?? 0)) * 100) / 100
}

function pickPlanField(
  row: SalesTopEntity | Record<string, number | null>,
  field: 'plan_cm' | 'ytd_plan' | 'mtd_plan',
): number | null {
  const rec = row as Record<string, unknown>
  const direct = coerceNum(rec[field])
  if (direct != null && Math.abs(direct) >= 1e-6) return direct
  if (field === 'plan_cm') {
    const py = coerceNum(rec.py_cm)
    if (py != null && Math.abs(py) >= 1e-6) return py
  }
  if (field === 'ytd_plan') {
    const ypy = coerceNum(rec.ytd_py)
    if (ypy != null && Math.abs(ypy) >= 1e-6) return ypy
  }
  return direct
}

export function numericFieldValue(
  row: SalesTopEntity | Record<string, number | null>,
  field: string,
): number | null | undefined {
  if (field === 'name') return null
  if (field.startsWith('delta_')) return resolveDelta(row, field)
  if (field === 'plan_cm' || field === 'ytd_plan' || field === 'mtd_plan') {
    return pickPlanField(row, field)
  }
  return coerceNum((row as Record<string, unknown>)[field])
}

export function salesDataColumns(cols: SalesColumnDef[]): SalesColumnDef[] {
  return cols.filter(c => c.field !== 'name')
}

export function computeDeltaMaxAbs(
  data: TopCustomerDisplayRow[],
  cols: SalesColumnDef[],
): Record<string, number> {
  const out: Record<string, number> = {}
  for (const col of cols) {
    if (!isDeltaField(col.field)) continue
    let max = 1
    for (const dr of data) {
      const src = dr.kind === 'customer' ? dr.row : dr.totals
      const v = resolveDelta(src, col.field)
      if (v != null) max = Math.max(max, Math.abs(v))
    }
    out[col.field] = max
  }
  return out
}

function planCellMuted(
  field: string,
  row: SalesTopEntity | Record<string, number | null>,
): boolean {
  if (field !== 'plan_cm' && field !== 'ytd_plan') return false
  return (row as SalesTopEntity).plan_source === 'py_proxy'
}

function renderFinDataCell(
  field: string,
  value: number | null | undefined,
  periodGrain: 'month' | 'week',
  maxAbsByField: Record<string, number>,
  bold: boolean,
  row?: SalesTopEntity | Record<string, number | null>,
) {
  const kind = salesColumnKind(field)
  const muted = row ? planCellMuted(field, row) : false
  if (kind === 'delta') {
    return (
      <SalesDeltaCell
        value={value}
        maxAbs={maxAbsByField[field] ?? 1}
        bold={bold}
      />
    )
  }
  if (kind === 'pct') {
    return <SalesValCell value={value} isPct bold={bold} muted={muted} />
  }
  return (
    <SalesValCell
      value={value}
      highlighted={salesHeaderHighlighted(field, periodGrain)}
      bold={bold}
      muted={muted}
    />
  )
}

type FinTableOpts = {
  cols: SalesColumnDef[]
  data: TopCustomerDisplayRow[]
  periodGrain: 'month' | 'week'
  nameForRow: (name: string) => string
}

export function renderFinStyleTable({ cols, data, periodGrain, nameForRow }: FinTableOpts) {
  const maxAbsByField = computeDeltaMaxAbs(data, cols)
  const dataCols = salesDataColumns(cols)
  const nameCol = cols.find(c => c.field === 'name')
  const partnerLabel = nameCol?.label ?? 'Name'

  return (
    <table className="w-full">
      <thead
        className="sticky top-0 z-20"
        style={{ background: SALES_TABLE_HEADER_BG, boxShadow: '0 1px 0 #E2E8F0' }}
      >
        <tr>
          <SalesFinHeader label="#" align="right" />
          <SalesFinHeader label={partnerLabel} align="left" />
          {dataCols.map(c => (
            <SalesFinHeader
              key={c.id}
              label={c.label}
              align="right"
              highlighted={salesHeaderHighlighted(c.field, periodGrain)}
            />
          ))}
        </tr>
      </thead>
      <tbody>
        {data.map((dr, i) => {
          if (dr.kind === 'subtotal' || dr.kind === 'total') {
            const isTotal = dr.kind === 'total'
            const rowBg = '#F8FAFC'
            return (
              <tr
                key={isTotal ? `total-${i}` : `sub-${dr.label}-${i}`}
                style={{
                  background: rowBg,
                  borderTop: '2px solid #E2E8F0',
                  ...(isTotal ? { borderBottom: '2px solid #E2E8F0' } : {}),
                }}
              >
                <td className="px-1.5 py-1" style={{ background: rowBg }} />
                <SalesLabelCell label={isTotal ? 'Total' : dr.label} bold />
                {dataCols.map(c =>
                  renderFinDataCell(
                    c.field,
                    numericFieldValue(dr.totals, c.field),
                    periodGrain,
                    maxAbsByField,
                    true,
                    dr.totals,
                  ),
                )}
              </tr>
            )
          }
          const r = dr.row
          const display = nameForRow(r.name)
          return (
            <tr key={`${r.rank}-${r.name}`} style={{ borderBottom: '1px solid #F8FAFC' }}>
              <SalesRankCell rank={r.rank} />
              <SalesLabelCell label={display} title={display} />
              {dataCols.map(c =>
                renderFinDataCell(
                  c.field,
                  numericFieldValue(r, c.field),
                  periodGrain,
                  maxAbsByField,
                  false,
                  r,
                ),
              )}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
