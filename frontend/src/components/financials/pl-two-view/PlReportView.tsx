import { useEffect, useMemo, useState } from 'react'
import type { FinancialStatementResponse, PlNarrativeResponse } from '../../../lib/api'
import { api, finPeriodParamsFromStatement } from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import PlMiniTable from './PlMiniTable'
import PlNarrativeList from './PlNarrativeList'
import { FIN_REPORT_SPLIT_GRID } from '../statement-two-view/finReportLayout'
import PlSectionHeading from './PlSectionHeading'
import { KEY_DRIVERS_HEADING, buildReportTableHeading } from './plReportSectionHeadings'
import { ChartLoadReporter } from '../../../hooks/useChartLoadReporter'
import {
  buildClientNarrativeResponse,
  isTrustedApiNarrative,
  mapApiBulletsToUi,
  type PlNarrativeBullet,
} from './plNarrativeEngine'
import type { PlPlanMap } from './usePlStatementData'
import { usePlRowExpansion } from './usePlRowExpansion'
import { buildReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'

type Props = {
  data: FinancialStatementResponse
  year: number
  month: number
  entity?: string
  periodSelection?: PeriodSelection
  /** When set, mini-table heading uses "{name}'s Income Statement — …" instead of Consolidated. */
  entityDisplayName?: string
  planMap: PlPlanMap
  hasPlanData?: boolean
  onDrill: (d: FinancialsDrillOpen) => void
  onBulletSelect: (b: PlNarrativeBullet) => void
  onNarrativeLoaded?: (narrative: PlNarrativeResponse | null) => void
}

const BULLET_CAP = 5

export default function PlReportView({
  data,
  year,
  month,
  entity,
  periodSelection,
  entityDisplayName,
  planMap,
  hasPlanData = false,
  onDrill,
  onBulletSelect,
  onNarrativeLoaded,
}: Props) {
  const [apiNarrative, setApiNarrative] = useState<PlNarrativeResponse | null>(null)
  const [loading, setLoading] = useState(true)

  const narrativePeriod = useMemo(
    () => finPeriodParamsFromStatement(data, entity, periodSelection),
    [data, entity, periodSelection],
  )

  const clientNarrative = useMemo(
    () => buildClientNarrativeResponse(data, planMap, year, month, BULLET_CAP),
    [data, planMap, year, month],
  )

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setApiNarrative(null)
    void api
      .financialsPlNarrativePeriod(narrativePeriod, {
        visible_rows: 24,
        max_bullets: BULLET_CAP,
        use_llm: false,
      })
      .then(res => {
        if (!cancelled) {
          setApiNarrative(res)
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setApiNarrative(null)
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [narrativePeriod])

  const narrative = useMemo(() => {
    if (isTrustedApiNarrative(apiNarrative, entity)) return apiNarrative!
    return clientNarrative
  }, [apiNarrative, clientNarrative, entity])

  useEffect(() => {
    onNarrativeLoaded?.(narrative)
  }, [narrative, onNarrativeLoaded])

  const { checkOpen, toggle } = usePlRowExpansion(data.rows, data.statement ?? 'pl')

  const bullets = useMemo(
    () => mapApiBulletsToUi(narrative.bullets, data.rows),
    [narrative.bullets, data.rows],
  )

  const commentMarkersByLineCode = useMemo(
    () => buildReportCommentMarkerMap(bullets, data.rows, checkOpen),
    [bullets, data.rows, checkOpen],
  )

  const tableHeading = useMemo(
    () => buildReportTableHeading(data.col_labels, { entityDisplayName }),
    [data.col_labels, entityDisplayName],
  )

  const narrativeBusy = loading && !isTrustedApiNarrative(apiNarrative, entity)

  return (
    <div className="px-4 pt-6 pb-6">
      <ChartLoadReporter chartId="fin-report-pl" loading={narrativeBusy} />
      <div className={FIN_REPORT_SPLIT_GRID}>
        <div className="min-w-0">
          <PlSectionHeading>{tableHeading}</PlSectionHeading>
          <PlMiniTable
            data={data}
            year={year}
            month={month}
            planMap={planMap}
            hasPlanData={hasPlanData}
            onDrill={onDrill}
            commentMarkersByLineCode={commentMarkersByLineCode}
            checkOpen={checkOpen}
            toggle={toggle}
          />
        </div>
        <div className="min-w-0">
          <PlSectionHeading>{KEY_DRIVERS_HEADING}</PlSectionHeading>
          <PlNarrativeList
            intro={narrative.intro}
            bullets={bullets}
            loading={loading && !isTrustedApiNarrative(apiNarrative, entity)}
            onSelect={onBulletSelect}
          />
        </div>
      </div>
    </div>
  )
}
