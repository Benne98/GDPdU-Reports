import { useMemo } from 'react'
import type { FinancialStatementResponse } from '../../../lib/api'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import { TwoLineHeader } from './plTableCore'
import { buildDefaultColumns, PLAN_ONLY_COL_KINDS } from './plColumnRegistry'
import type { PlPlanMap } from './usePlStatementData'
import { renderPlTableRows, type PlTableRenderCtx } from './plTableRowRenderer'
import { usePlRowExpansion } from './usePlRowExpansion'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import {
  REPORT_DELTA_COL_KINDS,
  REPORT_DELTA_COL_PX,
  REPORT_DELTA_COL_WEEK_PX,
  REPORT_LABEL_COL_MIN_PX,
  REPORT_MARKER_COL_PX,
  REPORT_PERIOD_COL_PX,
} from '../statement-two-view/finReportLayout'

const MINI_KINDS_MONTH = ['pm', 'cm', 'mom', 'ytd', 'plan_cm', 'plan_vs_actual'] as const
const MINI_KINDS_WEEK = ['pm', 'cm', 'mom', 'mtd', 'ytd', 'plan_cm'] as const

type Props = {
  data: FinancialStatementResponse
  year: number
  month: number
  planMap: PlPlanMap
  hasPlanData?: boolean
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
  hasPlanData = false,
  onDrill,
  commentMarkersByLineCode,
  checkOpen: checkOpenProp,
  toggle: toggleProp,
}: Props) {
  const expansion = usePlRowExpansion(data.rows, data.statement)
  const checkOpen = checkOpenProp ?? expansion.checkOpen
  const toggle = toggleProp ?? expansion.toggle
  const lbl = data.col_labels

  const grain = data.period_grain === 'week' ? 'week' : 'month'

  const columns = useMemo(() => {
    const allKinds: readonly string[] = grain === 'week' ? MINI_KINDS_WEEK : MINI_KINDS_MONTH
    const all = buildDefaultColumns(lbl, grain, data.statement)
    return all.filter(c =>
      allKinds.includes(c.kind) &&
      (hasPlanData || !PLAN_ONLY_COL_KINDS.has(c.kind))
    )
  }, [lbl, grain, data.statement, hasPlanData])

  const hasCommentCol = Boolean(commentMarkersByLineCode)

  const ctx: PlTableRenderCtx = {
    data,
    year,
    month,
    planMap,
    columns,
    compact: true,
    commentMarkersByLineCode,
    hasSpacerCol: true,
    onDrill,
    checkOpen,
    toggle,
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs table-fixed">
        <colgroup>
          <col style={{ width: REPORT_LABEL_COL_MIN_PX }} />
          {hasCommentCol && <col style={{ width: REPORT_MARKER_COL_PX }} />}
          <col />
          {columns.map(c => (
            <col key={c.id} style={{ width: REPORT_DELTA_COL_KINDS.has(c.kind) ? (grain === 'week' ? REPORT_DELTA_COL_WEEK_PX : REPORT_DELTA_COL_PX) : REPORT_PERIOD_COL_PX }} />
          ))}
        </colgroup>
        <thead>
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
            <th className="px-2 py-2 text-left font-semibold text-xs" style={{ color: '#475569' }}>EURk</th>
            {hasCommentCol && (
              <th className="px-0 py-2 text-center font-medium align-middle" style={{ color: '#94A3B8', fontSize: '0.62rem' }}>#</th>
            )}
            <th />
            {columns.map(c => {
              // Weekly delta columns: drop the "vs prior week" subheader and let the
              // main header use both lines so it fits the (wider) delta column.
              const deltaSpan = grain === 'week' && REPORT_DELTA_COL_KINDS.has(c.kind)
              return (
                <TwoLineHeader
                  key={c.id}
                  line1={c.labelLine1}
                  line2={c.labelLine2}
                  highlighted={c.kind === 'cm'}
                  spanBothLines={deltaSpan}
                />
              )
            })}
          </tr>
        </thead>
        <tbody>{renderPlTableRows(ctx, data.rows, 0)}</tbody>
      </table>
    </div>
  )
}
