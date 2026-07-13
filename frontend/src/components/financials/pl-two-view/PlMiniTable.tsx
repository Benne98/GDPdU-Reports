import { useMemo } from 'react'
import type { FinancialStatementResponse } from '../../../lib/api'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import { TwoLineHeader } from './plTableCore'
import { buildDefaultColumns } from './plColumnRegistry'
import type { PlPlanMap } from './usePlStatementData'
import { renderPlTableRows, type PlTableRenderCtx } from './plTableRowRenderer'
import { usePlRowExpansion } from './usePlRowExpansion'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import { REPORT_MARKER_COL_PX, REPORT_PERIOD_COL_PX } from '../statement-two-view/finReportLayout'

const MINI_KINDS_MONTH = ['pm', 'cm', 'mom', 'plan_cm', 'plan_vs_actual'] as const
const MINI_KINDS_WEEK = ['pm', 'cm', 'mom', 'mtd', 'plan_cm'] as const

type Props = {
  data: FinancialStatementResponse
  year: number
  month: number
  planMap: PlPlanMap
  onDrill: (d: FinancialsDrillOpen) => void
  commentMarkersByLineCode?: ReportCommentMarkerMap
  checkOpen?: (id: string) => boolean
  toggle?: (id: string) => void
}

export default function PlMiniTable({
  data,
  year,
  month,
  planMap,
  onDrill,
  commentMarkersByLineCode,
  checkOpen: checkOpenProp,
  toggle: toggleProp,
}: Props) {
  const expansion = usePlRowExpansion(data.rows, data.statement)
  const checkOpen = checkOpenProp ?? expansion.checkOpen
  const toggle = toggleProp ?? expansion.toggle
  const lbl = data.col_labels

  const columns = useMemo(() => {
    const grain = data.period_grain === 'week' ? 'week' : 'month'
    const kinds = grain === 'week' ? MINI_KINDS_WEEK : MINI_KINDS_MONTH
    const all = buildDefaultColumns(lbl, grain, data.statement)
    return all.filter(c => (kinds as readonly string[]).includes(c.kind))
  }, [lbl, data.period_grain, data.statement])

  const hasCommentCol = Boolean(commentMarkersByLineCode)

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
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs table-fixed">
        <colgroup>
          <col />{/* label: auto → absorbs all remaining horizontal space */}
          {hasCommentCol && <col style={{ width: REPORT_MARKER_COL_PX }} />}
          {columns.map((_c, i) => (
            <col key={i} style={{ width: REPORT_PERIOD_COL_PX }} />
          ))}
        </colgroup>
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
