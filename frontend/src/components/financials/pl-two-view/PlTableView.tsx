import { useMemo } from 'react'
import type { FinancialStatementResponse, MonthlyResponse } from '../../../lib/api'
import type { MonthlyPeriod } from '../../../lib/api'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import { TwoLineHeader, isReportPeriodHighlightColumn } from './plTableCore'
import {
  buildColumnCatalog,
  buildDefaultColumns,
  loadLegacyColumnIds,
  loadSavedColumns,
  reconcileColumns,
  type PlTableColumnDef,
} from './plColumnRegistry'
import type { PlPlanMap } from './usePlStatementData'
import { renderPlTableRows, type PlTableRenderCtx } from './plTableRowRenderer'
import { usePlRowExpansion } from './usePlRowExpansion'

type Props = {
  data: FinancialStatementResponse
  monthly: MonthlyResponse | null
  year: number
  month: number
  planMap: PlPlanMap
  columns: PlTableColumnDef[]
  onDrill: (d: FinancialsDrillOpen) => void
}

export default function PlTableView({ data, monthly, year, month, planMap, columns, onDrill }: Props) {
  const { checkOpen, toggle } = usePlRowExpansion(data.rows, data.statement)

  const ctx: PlTableRenderCtx = {
    data,
    year,
    month,
    planMap,
    monthly,
    columns,
    compact: true,
    onDrill,
    checkOpen,
    toggle,
  }

  return (
    <div className="overflow-x-auto p-4">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
            <th className="px-2 py-2 text-left font-semibold text-xs" style={{ color: '#475569' }}>EURk</th>
            {columns.map(c => (
              <TwoLineHeader
                key={c.id}
                line1={c.labelLine1}
                line2={c.labelLine2}
                highlighted={isReportPeriodHighlightColumn(c.kind)}
              />
            ))}
          </tr>
        </thead>
        <tbody>{renderPlTableRows(ctx, data.rows, 0)}</tbody>
      </table>
    </div>
  )
}

export function usePlTableColumns(
  data: FinancialStatementResponse | null,
  monthly: MonthlyResponse | null,
): PlTableColumnDef[] {
  return useMemo(() => {
    if (!data) return []
    const periodGrain = data.period_grain === 'week' ? 'week' : 'month'
    const periods: MonthlyPeriod[] =
      periodGrain === 'week' ? [] : (monthly?.periods ?? [])
    const catalog = buildColumnCatalog(data.col_labels, periods, periodGrain)
    const defaults = buildDefaultColumns(data.col_labels, periodGrain, data.statement)

    const stmt = data.statement || 'pl'
    const saved = loadSavedColumns(stmt)
    if (saved?.length) {
      return reconcileColumns(saved, catalog, periods)
    }

    const legacyIds = loadLegacyColumnIds(stmt)
    if (legacyIds?.length) {
      const fromLegacy = legacyIds
        .map(id => catalog.get(id))
        .filter((c): c is PlTableColumnDef => Boolean(c))
      if (fromLegacy.length) return fromLegacy
    }

    return defaults
  }, [data, monthly])
}
