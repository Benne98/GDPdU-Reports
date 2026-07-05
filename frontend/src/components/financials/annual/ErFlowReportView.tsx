import { useMemo, useState } from 'react'
import type { ErFlowResponse, FinancialStatementRow } from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import { FIN_REPORT_SPLIT_GRID } from '../statement-two-view/finReportLayout'
import { prepareAnnualSnapshotReportBullets } from './annualReportMarkers'
import StatementSectionHeading from '../statement-two-view/StatementSectionHeading'
import StatementNarrativeList from '../statement-two-view/StatementNarrativeList'
import { mapApiBulletsToUi, type PlNarrativeBullet } from '../pl-two-view/plNarrativeEngine'
import PlDetailOverlay from '../pl-two-view/PlDetailOverlay'
import { ChartLoadReporter } from '../../../hooks/useChartLoadReporter'
import type { FinStatementKind } from '../statement-two-view/statementTypes'
import ErFlowMiniTable, { type ErFlowColDef } from './ErFlowMiniTable'
import { useAnnualStatementNarrative } from './useAnnualStatementNarrative'
import { buildAnnualFlowReportTableHeading, KEY_DRIVERS_HEADING } from './annualReportSectionHeadings'
import type { PlNarrativeResponse } from '../../../lib/api'
import { buildReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'

type Props = {
  data: ErFlowResponse
  statement: 'pl' | 'cf'
  year: number
  month: number
  entity?: string
  periodSelection?: PeriodSelection
  entityDisplayName?: string
  reportColumns: ErFlowColDef[]
  clientNarrative: PlNarrativeResponse | null
  onDrill: (d: FinancialsDrillOpen) => void
  checkOpen: (id: string) => boolean
  toggle: (id: string) => void
  fy2Label: string
  fy3Label: string
}

export default function ErFlowReportView({
  data,
  statement,
  year,
  month,
  entity,
  periodSelection,
  entityDisplayName,
  reportColumns,
  clientNarrative,
  onDrill,
  checkOpen,
  toggle,
  fy2Label,
  fy3Label,
}: Props) {
  const stmtKind: FinStatementKind = statement
  const [detailBullet, setDetailBullet] = useState<PlNarrativeBullet | null>(null)
  const { narrative, narrativeBusy } = useAnnualStatementNarrative(
    stmtKind,
    year,
    month,
    clientNarrative,
    entity,
    periodSelection,
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
    () => buildAnnualFlowReportTableHeading(statement, data.col_labels, {
      entityDisplayName,
      fy2Label,
      fy3Label,
    }),
    [statement, data.col_labels, entityDisplayName, fy2Label, fy3Label],
  )

  return (
    <>
      <div className="px-4 pt-6 pb-6">
        <ChartLoadReporter chartId={`fin-report-annual-${statement}`} loading={narrativeBusy} />
        <div className={FIN_REPORT_SPLIT_GRID} style={{ alignItems: 'stretch' }}>
          <div className="min-w-0">
            <StatementSectionHeading>{tableHeading}</StatementSectionHeading>
            <ErFlowMiniTable
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
          <div className="min-w-0 flex flex-col">
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
