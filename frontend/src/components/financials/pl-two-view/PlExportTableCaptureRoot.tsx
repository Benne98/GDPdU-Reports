import type { FinancialStatementResponse, MonthlyResponse } from '../../../lib/api'
import PlExportTableFrame from './PlExportTableFrame'
import type { PlTableColumnDef } from './plColumnRegistry'
import type { PlPlanMap } from './usePlStatementData'
import { mmToPx } from './plExportPdfLayout'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'

/** ~9pt table body — readable at full PDF column width. */
const BODY_FONT_PX = Math.round(9 * (96 / 72))

type Props = {
  data: FinancialStatementResponse
  year: number
  month: number
  planMap: PlPlanMap
  columns: PlTableColumnDef[]
  monthly?: MonthlyResponse | null
  commentMarkersByLineCode?: ReportCommentMarkerMap
  widthMm: number
  isRowOpen?: (id: string) => boolean
}

/** Off-screen table root for html2canvas (matches PlMiniTable layout). */
export default function PlExportTableCaptureRoot({
  data,
  year,
  month,
  planMap,
  columns,
  monthly = null,
  commentMarkersByLineCode,
  widthMm,
  isRowOpen,
}: Props) {
  const widthPx = mmToPx(widthMm)

  return (
    <div
      data-pl-export-table
      style={{
        width: widthPx,
        boxSizing: 'border-box',
        overflow: 'hidden',
        background: '#FFFFFF',
        fontFamily: 'Calibri, "Segoe UI", sans-serif',
      }}
    >
      <PlExportTableFrame
        data={data}
        year={year}
        month={month}
        planMap={planMap}
        columns={columns}
        monthly={monthly}
        commentMarkersByLineCode={commentMarkersByLineCode}
        pixelFontSize={BODY_FONT_PX}
        widthPx={widthPx}
        isRowOpen={isRowOpen}
      />
    </div>
  )
}
