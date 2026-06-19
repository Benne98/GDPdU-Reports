import type { FinancialStatementResponse } from '../../../lib/api'
import PlExportNarrative from './PlExportNarrative'
import PlExportTableFrame from './PlExportTableFrame'
import PlSectionHeading from './PlSectionHeading'
import { buildExportFooterLine, type PlExportContext } from './plExportFooter'
import {
  KEY_DRIVERS_HEADING,
  buildConsolidatedTableHeading,
} from './plReportSectionHeadings'
import type { PlNarrativeBullet } from './plNarrativeEngine'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import type { PlPlanMap } from './usePlStatementData'
import type { PlTableColumnDef } from './plColumnRegistry'

const PAGE_WIDTH_PX = 1120
const BODY_FONT_PX = 12

type Props = {
  data: FinancialStatementResponse
  year: number
  month: number
  planMap: PlPlanMap
  miniColumns: PlTableColumnDef[]
  intro: string | null | undefined
  bullets: PlNarrativeBullet[]
  commentMarkersByLineCode?: ReportCommentMarkerMap
  exportCtx: PlExportContext
}

export default function PlExportReportPage({
  data,
  year,
  month,
  planMap,
  miniColumns,
  intro,
  bullets,
  commentMarkersByLineCode,
  exportCtx,
}: Props) {
  const tableHeading = buildConsolidatedTableHeading(data.col_labels)
  const footerLine = buildExportFooterLine(data, exportCtx)

  return (
    <div
      data-pl-export-page
      style={{
        width: PAGE_WIDTH_PX,
        boxSizing: 'border-box',
        background: '#FFFFFF',
        padding: '28px 32px 24px',
        fontFamily: 'Calibri, "Segoe UI", sans-serif',
        color: '#475569',
      }}
    >
      <h1
        style={{
          margin: '0 0 24px',
          fontSize: 18,
          fontWeight: 700,
          lineHeight: 1.25,
          color: '#111827',
        }}
      >
        Consolidated Income Statement
      </h1>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.15fr)',
          gap: 24,
          alignItems: 'start',
        }}
      >
        <div>
          <PlSectionHeading>{KEY_DRIVERS_HEADING}</PlSectionHeading>
          <PlExportNarrative intro={intro} bullets={bullets} />
        </div>
        <div>
          <PlSectionHeading>{tableHeading}</PlSectionHeading>
          <PlExportTableFrame
            data={data}
            year={year}
            month={month}
            planMap={planMap}
            columns={miniColumns}
            commentMarkersByLineCode={commentMarkersByLineCode}
            pixelFontSize={BODY_FONT_PX}
            widthPx={620}
          />
        </div>
      </div>

      <footer
        style={{
          marginTop: 24,
          paddingTop: 10,
          borderTop: '1px solid #E2E8F0',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 16,
          fontSize: 10,
          lineHeight: 1.35,
          color: '#64748B',
        }}
      >
        <span style={{ fontWeight: 700, color: '#1E3A5F', flexShrink: 0 }}>Finssentials</span>
        <span style={{ textAlign: 'right' }}>{footerLine}</span>
      </footer>
    </div>
  )
}
