import type { SalesBreakdownRow } from '../../../lib/api'
import {
  SalesDeltaCell,
  SalesFinHeader,
  SalesValCell,
  SALES_TABLE_HEADER_BG,
} from './salesFinTableCells'
import type { BreakdownColumnDef } from './salesBreakdownRegistry'
import { FIN_TABLE_CELL_CLASS } from '../../financials/statement-two-view/finReportLayout'

export type BreakdownMetrics = Pick<
  SalesBreakdownRow,
  | 'gs_pm'
  | 'gs_cm'
  | 'gp_pm'
  | 'gp_cm'
  | 'gm_pm'
  | 'gm_cm'
  | 'gs_plan_cm'
  | 'gp_plan_cm'
  | 'gm_plan_cm'
  | 'delta_gs_cm_pm'
  | 'delta_gp_cm_pm'
  | 'delta_gm_cm_pm'
>

export function aggregateBreakdownRows(rows: SalesBreakdownRow[]): BreakdownMetrics {
  const gs_pm = rows.reduce((s, r) => s + (r.gs_pm ?? 0), 0)
  const gs_cm = rows.reduce((s, r) => s + (r.gs_cm ?? 0), 0)
  const gp_pm = rows.reduce((s, r) => s + (r.gp_pm ?? 0), 0)
  const gp_cm = rows.reduce((s, r) => s + (r.gp_cm ?? 0), 0)
  const gs_plan_cm = rows.reduce((s, r) => s + (r.gs_plan_cm ?? 0), 0)
  const gp_plan_cm = rows.reduce((s, r) => s + (r.gp_plan_cm ?? 0), 0)
  const gm_pm = gs_pm !== 0 ? Math.round((gp_pm / gs_pm) * 1000) / 10 : 0
  const gm_cm = gs_cm !== 0 ? Math.round((gp_cm / gs_cm) * 1000) / 10 : 0
  const gm_plan_cm = gs_plan_cm !== 0 ? Math.round((gp_plan_cm / gs_plan_cm) * 1000) / 10 : 0
  return {
    gs_pm: Math.round(gs_pm * 100) / 100,
    gs_cm: Math.round(gs_cm * 100) / 100,
    gp_pm: Math.round(gp_pm * 100) / 100,
    gp_cm: Math.round(gp_cm * 100) / 100,
    gm_pm,
    gm_cm,
    gs_plan_cm: Math.round(gs_plan_cm * 100) / 100,
    gp_plan_cm: Math.round(gp_plan_cm * 100) / 100,
    gm_plan_cm,
    delta_gs_cm_pm: Math.round((gs_cm - gs_pm) * 100) / 100,
    delta_gp_cm_pm: Math.round((gp_cm - gp_pm) * 100) / 100,
    delta_gm_cm_pm: Math.round((gm_cm - gm_pm) * 10) / 10,
  }
}

export function breakdownFieldValue(
  metrics: BreakdownMetrics,
  field: string,
): number | null {
  const v = (metrics as Record<string, number | undefined>)[field]
  return typeof v === 'number' ? v : null
}

export function computeBreakdownDeltaMax(
  rows: SalesBreakdownRow[],
  columns: BreakdownColumnDef[],
): Record<string, number> {
  const out: Record<string, number> = {}
  for (const col of columns) {
    if (!col.field.startsWith('delta_')) continue
    let max = 1
    for (const r of rows) {
      const v = breakdownFieldValue(r, col.field)
      if (v != null) max = Math.max(max, Math.abs(v))
    }
    out[col.field] = max
  }
  return out
}

const BLOCK_HEADER_LABELS: Record<string, string> = {
  gross_sales: 'Gross Sales',
  gross_profit: 'Gross Profit',
  gross_margin: 'Gross Margin',
}

export function BreakdownBlockHeader({ block, colSpan }: { block: string; colSpan: number }) {
  return (
    <th
      colSpan={colSpan}
      className={`${FIN_TABLE_CELL_CLASS} text-center font-semibold text-xs border-l`}
      style={{ color: '#1E3A5F', borderColor: '#E2E8F0', background: SALES_TABLE_HEADER_BG }}
    >
      {BLOCK_HEADER_LABELS[block] ?? block}
    </th>
  )
}

export function BreakdownSubHeader({ label, highlighted }: { label: string; highlighted?: boolean }) {
  return <SalesFinHeader label={label} align="right" highlighted={highlighted} />
}

export function BreakdownDataCells({
  metrics,
  columns,
  maxAbsByField,
  bold = false,
}: {
  metrics: BreakdownMetrics
  columns: BreakdownColumnDef[]
  maxAbsByField: Record<string, number>
  bold?: boolean
}) {
  return (
    <>
      {columns.map(col => {
        const value = breakdownFieldValue(metrics, col.field)
        if (col.sub === 'delta') {
          if (col.isPct) {
            const n = value ?? 0
            const color = n === 0 ? '#94A3B8' : n > 0 ? '#10B981' : '#DC2626'
            return (
              <td
                key={col.id}
                className={`${FIN_TABLE_CELL_CLASS} text-right tabular-nums whitespace-nowrap`}
                style={{ fontWeight: bold ? 600 : 400, color, fontSize: '0.68rem' }}
              >
                {n >= 0 ? '+' : ''}
                {n.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} pp
              </td>
            )
          }
          return (
            <SalesDeltaCell
              key={col.id}
              value={value}
              maxAbs={maxAbsByField[col.field] ?? 1}
              bold={bold}
            />
          )
        }
        return (
          <SalesValCell
            key={col.id}
            value={value}
            isPct={col.isPct}
            bold={bold}
            highlighted={col.sub === 'cm'}
          />
        )
      })}
    </>
  )
}

export const L1_ROW_STYLE = {
  background: '#F0F4FA',
  borderTop: '2px solid #E2E8F0',
  borderBottom: '2px solid #E2E8F0',
} as const

export const L2_ROW_STYLE = {
  background: '#F8FAFC',
  borderTop: '1px solid #E2E8F0',
} as const
