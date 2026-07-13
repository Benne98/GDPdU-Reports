import { useMemo, useState } from 'react'
import type { ErSnapshotResponse, FinancialStatementRow } from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import { FIN_ENTITY_CONSOL_REPORT_SPLIT_GRID } from '../statement-two-view/finReportLayout'
import {
  prepareAnnualSnapshotReportBullets,
} from './annualReportMarkers'
import { buildReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import { buildAnnualSnapshotReportColumns } from './annualSnapshotReportColumns'
import StatementSectionHeading from '../statement-two-view/StatementSectionHeading'
import StatementNarrativeList from '../statement-two-view/StatementNarrativeList'
import { mapApiBulletsToUi, type PlNarrativeBullet } from '../pl-two-view/plNarrativeEngine'
import PlDetailOverlay from '../pl-two-view/PlDetailOverlay'
import { ChartLoadReporter } from '../../../hooks/useChartLoadReporter'
import type { FinStatementKind } from '../statement-two-view/statementTypes'
import ErSnapshotMiniTable from './ErSnapshotMiniTable'
import { useAnnualStatementNarrative } from './useAnnualStatementNarrative'
import { buildAnnualSnapshotReportTableHeading, KEY_DRIVERS_HEADING } from './annualReportSectionHeadings'
import type { PlNarrativeResponse } from '../../../lib/api'
import { useFinReportTableHeight } from './useFinReportTableHeight'

type Props = {
  data: ErSnapshotResponse
  statement: 'bs' | 'wc'
  year: number
  month: number
  entity?: string
  periodSelection?: PeriodSelection
  entityDisplayName?: string
  clientNarrative: PlNarrativeResponse | null
  onDrill: (d: FinancialsDrillOpen) => void
  checkOpen: (id: string) => boolean
  toggle: (id: string) => void
  /** When false, only API narrative is used (avoids generic client snapshot bullets). */
  useClientNarrativeFallback?: boolean
}

export default function ErSnapshotReportView({
  data,
  statement,
  year,
  month,
  entity,
  periodSelection,
  entityDisplayName,
  clientNarrative,
  onDrill,
  checkOpen,
  toggle,
  useClientNarrativeFallback = true,
}: Props) {
  const stmtKind: FinStatementKind = statement
  const [detailBullet, setDetailBullet] = useState<PlNarrativeBullet | null>(null)
  const { narrative, narrativeBusy } = useAnnualStatementNarrative(
    stmtKind,
    year,
    month,
    useClientNarrativeFallback ? clientNarrative : null,
    entity,
    periodSelection,
  )

  const lbl = data.col_labels
  const reportColumns = useMemo(
    () => buildAnnualSnapshotReportColumns(year, month, lbl),
    [lbl, year, month],
  )

  const rows = data.rows as FinancialStatementRow[]

  const bullets = useMemo(() => {
    const mapped = mapApiBulletsToUi(narrative?.bullets ?? [], rows)
    return prepareAnnualSnapshotReportBullets(mapped, rows, checkOpen)
  }, [narrative?.bullets, rows, checkOpen])

  const commentMarkersByLineCode = useMemo(
    () => buildReportCommentMarkerMap(bullets, rows, checkOpen),
    [bullets, rows, checkOpen],
  )

  const tableHeading = useMemo(
    () => buildAnnualSnapshotReportTableHeading(statement, lbl, { entityDisplayName }),
    [statement, lbl, entityDisplayName],
  )

  const { tableWrapRef, tableHeightPx } = useFinReportTableHeight([data, year, month, checkOpen])

  return (
    <>
      <div className="px-4 pt-6 pb-6">
      <ChartLoadReporter chartId={`fin-report-annual-${statement}`} loading={narrativeBusy} />
      <div className={FIN_ENTITY_CONSOL_REPORT_SPLIT_GRID} style={{ alignItems: 'stretch' }}>
        <div className="min-w-0" ref={tableWrapRef}>
          <StatementSectionHeading>{tableHeading}</StatementSectionHeading>
          <ErSnapshotMiniTable
            data={data}
            year={year}
            month={month}
            columns={reportColumns}
            onDrill={onDrill}
            commentMarkersByLineCode={commentMarkersByLineCode}
            checkOpen={checkOpen}
            toggle={toggle}
          />
        </div>
        <div
          className="min-w-0 flex flex-col"
          style={
            statement !== 'wc' && tableHeightPx != null && tableHeightPx > 120
              ? { maxHeight: tableHeightPx, overflowY: 'auto' }
              : undefined
          }
        >
          <StatementSectionHeading>{KEY_DRIVERS_HEADING}</StatementSectionHeading>
          <StatementNarrativeList
            intro={narrative?.intro}
            bullets={bullets}
            loading={narrativeBusy}
            onSelect={setDetailBullet}
          />
        </div>
      </div>
    </div>
      {detailBullet && (
        <PlDetailOverlay
          bullet={detailBullet}
          year={year}
          month={month}
          entity={entity}
          statement={stmtKind}
          onClose={() => setDetailBullet(null)}
        />
      )}
    </>
  )
}
