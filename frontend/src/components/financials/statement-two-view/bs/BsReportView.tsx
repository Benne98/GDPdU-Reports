import { useEffect, useMemo, useRef, useState } from 'react'
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
import BsMiniTable from './BsMiniTable'
import {
  buildClientBsNarrative,
  isTrustedApiBsNarrative,
  mapApiBulletsToUi,
  type BsNarrativeBullet,
} from './bsNarrativeEngine'
import { KEY_DRIVERS_HEADING, buildBsReportTableHeading } from './bsReportSectionHeadings'
import { ChartLoadReporter } from '../../../../hooks/useChartLoadReporter'

type Props = {
  data: FinancialStatementResponse
  year: number
  month: number
  entity?: string
  periodSelection?: PeriodSelection
  entityDisplayName?: string
  onDrill: (d: FinancialsDrillOpen) => void
  onBulletSelect: (b: BsNarrativeBullet) => void
  onNarrativeLoaded?: (narrative: PlNarrativeResponse | null) => void
}

const BULLET_CAP = 5

export default function BsReportView({
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
  const tableWrapRef = useRef<HTMLDivElement>(null)
  const [tableHeightPx, setTableHeightPx] = useState<number | null>(null)

  const { checkOpen, toggle } = usePlRowExpansion(data.rows, data.statement ?? 'bs')

  const narrativePeriod = useMemo(
    () => finPeriodParamsFromStatement(data, entity, periodSelection),
    [data, entity, periodSelection],
  )

  const clientNarrative = useMemo(
    () => buildClientBsNarrative(data, year, month, BULLET_CAP),
    [data, year, month],
  )

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    void api
      .financialsBsNarrativePeriod(narrativePeriod, {
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
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [narrativePeriod])

  const narrative = useMemo(() => {
    if (isTrustedApiBsNarrative(apiNarrative, entity)) return apiNarrative!
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
    () => buildBsReportTableHeading(data.col_labels, { entityDisplayName }),
    [data.col_labels, entityDisplayName],
  )

  useEffect(() => {
    const el = tableWrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      setTableHeightPx(el.getBoundingClientRect().height)
    })
    ro.observe(el)
    setTableHeightPx(el.getBoundingClientRect().height)
    return () => ro.disconnect()
  }, [data, year, month, checkOpen])

  const narrativeBusy = loading && !isTrustedApiBsNarrative(apiNarrative, entity)

  return (
    <div className="px-4 pt-6 pb-6">
      <ChartLoadReporter chartId="fin-report-bs" loading={narrativeBusy} />
      <div className={FIN_ENTITY_CONSOL_REPORT_SPLIT_GRID} style={{ alignItems: 'stretch' }}>
        <div className="min-w-0" ref={tableWrapRef}>
          <StatementSectionHeading>{tableHeading}</StatementSectionHeading>
          <BsMiniTable
            data={data}
            year={year}
            month={month}
            onDrill={onDrill}
            commentMarkersByLineCode={commentMarkersByLineCode}
            checkOpen={checkOpen}
            toggle={toggle}
          />
        </div>
        <div
          className="min-w-0 flex flex-col"
          style={
            tableHeightPx != null && tableHeightPx > 120
              ? { maxHeight: tableHeightPx, overflowY: 'auto' }
              : undefined
          }
        >
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
