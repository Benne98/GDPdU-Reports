import { useEffect, useMemo, useState } from 'react'
import type { FinancialStatementResponse, PlNarrativeResponse } from '../../../../lib/api'
import { api, finPeriodParamsFromStatement } from '../../../../lib/api'
import type { PeriodSelection } from '../../../../lib/periodSelection'
import type { FinancialsDrillOpen } from '../../FinancialStatementTable'
import { usePlRowExpansion } from '../../pl-two-view/usePlRowExpansion'
import { FIN_REPORT_SPLIT_GRID } from '../finReportLayout'
import { countVisibleStatementRows, fitBulletsToTable } from '../narrativeFit'
import { buildReportCommentMarkerMap } from '../reportCommentMarkers'
import StatementNarrativeList from '../StatementNarrativeList'
import StatementSectionHeading from '../StatementSectionHeading'
import CfMiniTable from './CfMiniTable'
import {
  buildClientCfNarrative,
  isTrustedApiCfNarrative,
  mapApiBulletsToUi,
  type CfNarrativeBullet,
} from './cfNarrativeEngine'
import { KEY_DRIVERS_HEADING, buildCfReportTableHeading } from './cfReportSectionHeadings'
import { ChartLoadReporter } from '../../../../hooks/useChartLoadReporter'

type Props = {
  data: FinancialStatementResponse
  year: number
  month: number
  entity?: string
  periodSelection?: PeriodSelection
  entityDisplayName?: string
  onDrill: (d: FinancialsDrillOpen) => void
  onBulletSelect: (b: CfNarrativeBullet) => void
  onNarrativeLoaded?: (narrative: PlNarrativeResponse | null) => void
}

const BULLET_CAP = 5

export default function CfReportView({
  data,
  year,
  month,
  entity,
  periodSelection,
  entityDisplayName,
  onDrill,
  onBulletSelect,
  onNarrativeLoaded,
}: Props) {
  const [apiNarrative, setApiNarrative] = useState<PlNarrativeResponse | null>(null)
  const [loading, setLoading] = useState(true)

  const { checkOpen, toggle } = usePlRowExpansion(data.rows, data.statement ?? 'cf')

  const narrativePeriod = useMemo(
    () => finPeriodParamsFromStatement(data, entity, periodSelection),
    [data, entity, periodSelection],
  )

  const clientNarrative = useMemo(
    () => buildClientCfNarrative(data, year, month, BULLET_CAP),
    [data, year, month],
  )

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setApiNarrative(null)
    void api
      .financialsCfNarrativePeriod(narrativePeriod, {
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
    const trusted = isTrustedApiCfNarrative(apiNarrative, entity)
    const apiCount = apiNarrative?.bullets?.length ?? 0
    const clientCount = clientNarrative.bullets?.length ?? 0
    if (trusted && apiCount >= 3) return apiNarrative!
    if (trusted && apiCount > 0 && clientCount === 0) return apiNarrative!
    if (clientCount >= 3) return clientNarrative
    if (trusted && apiNarrative) return apiNarrative
    return clientNarrative
  }, [apiNarrative, clientNarrative, entity])

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
    () => buildCfReportTableHeading(data.col_labels, { entityDisplayName }),
    [data.col_labels, entityDisplayName],
  )

  const narrativeBusy = loading && !isTrustedApiCfNarrative(apiNarrative, entity)

  return (
    <div className="px-4 pt-6 pb-6">
      <ChartLoadReporter chartId="fin-report-cf" loading={narrativeBusy} />
      <div className={FIN_REPORT_SPLIT_GRID} style={{ alignItems: 'stretch' }}>
        <div className="min-w-0">
          <StatementSectionHeading>{tableHeading}</StatementSectionHeading>
          <CfMiniTable
            data={data}
            year={year}
            month={month}
            onDrill={onDrill}
            commentMarkersByLineCode={commentMarkersByLineCode}
            checkOpen={checkOpen}
            toggle={toggle}
          />
        </div>
        <div className="min-w-0 flex flex-col">
          <StatementSectionHeading>{KEY_DRIVERS_HEADING}</StatementSectionHeading>
          <StatementNarrativeList
            intro={narrative.intro}
            bullets={bullets}
            loading={narrativeBusy}
            onSelect={onBulletSelect}
          />
        </div>
      </div>
    </div>
  )
}
