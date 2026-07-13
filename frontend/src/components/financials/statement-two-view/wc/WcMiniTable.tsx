import { useMemo } from 'react'
import type { FinancialStatementResponse } from '../../../../lib/api'
import type { FinancialsDrillOpen } from '../../FinancialStatementTable'
import { TwoLineHeader } from '../../pl-two-view/plTableCore'
import { buildDefaultColumns } from '../../pl-two-view/plColumnRegistry'
import { buildPlanMapFromStatement } from '../../pl-two-view/plPlanMap'
import { renderPlTableRows, type PlTableRenderCtx } from '../../pl-two-view/plTableRowRenderer'
import { usePlRowExpansion } from '../../pl-two-view/usePlRowExpansion'
import type { ReportCommentMarkerMap } from '../reportCommentMarkers'

const MINI_KINDS_MONTH = ['pm', 'cm', 'mom', 'plan_cm', 'plan_vs_actual'] as const
const MINI_KINDS_WEEK = ['pm', 'cm', 'mom', 'mtd', 'plan_cm', 'plan_vs_actual'] as const

type Props = {
  data: FinancialStatementResponse
  year: number
  month: number
  onDrill: (d: FinancialsDrillOpen) => void
  commentMarkersByLineCode?: ReportCommentMarkerMap
  checkOpen?: (id: string) => boolean
  toggle?: (id: string) => void
}

export default function WcMiniTable({
  data,
  year,
  month,
  onDrill,
  commentMarkersByLineCode,
  checkOpen: checkOpenProp,
  toggle: toggleProp,
}: Props) {
  const expansion = usePlRowExpansion(data.rows, data.statement)
  const checkOpen = checkOpenProp ?? expansion.checkOpen
  const toggle = toggleProp ?? expansion.toggle
  const lbl = data.col_labels

  const planMap = useMemo(() => buildPlanMapFromStatement(data), [data])

  const columns = useMemo(() => {
    const grain = data.period_grain === 'week' ? 'week' : 'month'
    const kinds = grain === 'week' ? MINI_KINDS_WEEK : MINI_KINDS_MONTH
    const all = buildDefaultColumns(lbl, grain, data.statement)
    return all.filter(c => (kinds as readonly string[]).includes(c.kind))
  }, [lbl, data.period_grain, data.statement])

  const ctx: PlTableRenderCtx = {
    data,
    year,
    month,
    planMap,
    columns,
    compact: true,
    commentMarkersByLineCode,
    onDrill,
    checkOpen,
    toggle,
  }

  return (
    <div className="min-w-0 w-full">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
            <th className="px-2 py-2 text-left font-semibold text-xs" style={{ color: '#475569' }}>EURk</th>
            {commentMarkersByLineCode && (
              <th className="px-0 py-2 text-center font-medium align-middle" style={{ color: '#94A3B8', width: 20, minWidth: 20, maxWidth: 20, fontSize: '0.62rem' }}>#</th>
            )}
            {columns.map(c => (
              <TwoLineHeader key={c.id} line1={c.labelLine1} line2={c.labelLine2} highlighted={c.kind === 'cm'} />
            ))}
          </tr>
        </thead>
        <tbody>{renderPlTableRows(ctx, data.rows, 0)}</tbody>
      </table>
    </div>
  )
}
