import { useEffect, useMemo, useState } from 'react'
import type { FinancialStatementResponse, PlNarrativeResponse } from '../../../../lib/api'
import { api, finPeriodParamsFromStatement } from '../../../../lib/api'
import type { PeriodSelection } from '../../../../lib/periodSelection'
import type { FinancialsDrillOpen } from '../../FinancialStatementTable'
import { usePlRowExpansion } from '../../pl-two-view/usePlRowExpansion'
import { FIN_ENTITY_CONSOL_REPORT_SPLIT_GRID } from '../finReportLayout'
import { countVisibleStatementRows, fitBulletsToTable } from '../narrativeFit'
import { buildReportCommentMarkerMap } from '../reportCommentMarkers'
import StatementNarrativeList from '../StatementNarrativeList'
import StatementSectionHeading from '../StatementSectionHeading'
import WcMiniTable from './WcMiniTable'
import {
  buildClientWcNarrative,
  isTrustedApiWcNarrative,
  mapApiBulletsToUi,
  type WcNarrativeBullet,
} from './wcNarrativeEngine'
import { KEY_DRIVERS_HEADING, buildWcReportTableHeading } from './wcReportSectionHeadings'
import { ChartLoadReporter } from '../../../../hooks/useChartLoadReporter'

type Props = {
  data: FinancialStatementResponse
  year: number
  month: number
  entity?: string
  periodSelection?: PeriodSelection
  entityDisplayName?: string
  hasPlanData?: boolean
  onDrill: (d: FinancialsDrillOpen) => void
  onBulletSelect: (b: WcNarrativeBullet) => void
  onNarrativeLoaded?: (narrative: PlNarrativeResponse | null) => void
}

const BULLET_CAP = 5

export default function WcReportView({
  data,
  year,
  month,
  entity,
  periodSelection,
  entityDisplayName,
  hasPlanData = false,
  onDrill,
  onBulletSelect,
  onNarrativeLoaded,
}: Props) {
  const [apiNarrative, setApiNarrative] = useState<PlNarrativeResponse | null>(null)
  const [loading, setLoading] = useState(true)

  const { checkOpen, toggle } = usePlRowExpansion(data.rows, data.statement ?? 'wc')

  const narrativePeriod = useMemo(
    () => finPeriodParamsFromStatement(data, entity, periodSelection),
    [data, entity, periodSelection],
  )

  const clientNarrative = useMemo(
    () => buildClientWcNarrative(data, year, month, BULLET_CAP),
    [data, year, month],
  )

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setApiNarrative(null)
    void api
      .financialsWcNarrativePeriod(narrativePeriod, {
        visible_rows: 24,
        max_bullets: BULLET_CAP,
        use_llm: false,
      })
      .then((res: PlNarrativeResponse) => {
        if (!cancelled) {
          setApiNarrative(res)
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [narrativePeriod])

  const trustedApi = isTrustedApiWcNarrative(apiNarrative, entity)

  const narrative = useMemo(() => {
    if (trustedApi && apiNarrative) return apiNarrative
    return clientNarrative
  }, [trustedApi, apiNarrative, clientNarrative])

  useEffect(() => {
    onNarrativeLoaded?.(narrative)
  }, [narrative, onNarrativeLoaded])

  const visibleTableRows = useMemo(
    () => countVisibleStatementRows(data.rows, checkOpen),
    [data.rows, checkOpen],
  )

  const bullets = useMemo(() => {
    if (!narrative) return []
    const mapped = mapApiBulletsToUi(narrative.bullets, data.rows)
    return fitBulletsToTable(mapped, visibleTableRows, narrative.intro, BULLET_CAP)
  }, [narrative, data.rows, visibleTableRows])

  const commentMarkersByLineCode = useMemo(
    () => buildReportCommentMarkerMap(bullets, data.rows, checkOpen),
    [bullets, data.rows, checkOpen],
  )

  const tableHeading = useMemo(
    () => buildWcReportTableHeading(data.col_labels, { entityDisplayName }),
    [data.col_labels, entityDisplayName],
  )

  const narrativeBusy = loading && trustedApi && !!apiNarrative

  return (
    <div className="px-4 pt-6 pb-6">
      <ChartLoadReporter chartId="fin-report-wc" loading={narrativeBusy} />
      <div className={FIN_ENTITY_CONSOL_REPORT_SPLIT_GRID}>
        <div className="min-w-0">
          <StatementSectionHeading>{tableHeading}</StatementSectionHeading>
          <WcMiniTable
            data={data}
            year={year}
            month={month}
            hasPlanData={hasPlanData}
            onDrill={onDrill}
            commentMarkersByLineCode={commentMarkersByLineCode}
            checkOpen={checkOpen}
            toggle={toggle}
          />
        </div>
        <div className="min-w-0">
          <StatementSectionHeading>{KEY_DRIVERS_HEADING}</StatementSectionHeading>
          <StatementNarrativeList
            intro={narrative?.intro}
            bullets={bullets}
            loading={narrativeBusy}
            onSelect={onBulletSelect}
          />
        </div>
      </div>
    </div>
  )
}
