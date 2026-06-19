import type { FinancialStatementResponse, MonthlyResponse } from '../../../lib/api'
import PlExportTableFrame from './PlExportTableFrame'
import { buildExportFooterLine, type PlExportContext } from './plExportFooter'
import type { PlPlanMap } from './usePlStatementData'
import type { PlTableColumnDef } from './plColumnRegistry'

const PAGE_WIDTH_PX = 1120
const BODY_FONT_PX = 11

type Props = {
  data: FinancialStatementResponse
  year: number
  month: number
  planMap: PlPlanMap
  columns: PlTableColumnDef[]
  monthly: MonthlyResponse | null
  exportCtx: PlExportContext
}

export default function PlExportTablePage({
  data,
  year,
  month,
  planMap,
  columns,
  monthly,
  exportCtx,
}: Props) {
  const footerLine = buildExportFooterLine(data, exportCtx)
  const tableWidth = Math.min(1060, Math.max(720, columns.length * 72 + 200))

  return (
    <div
      data-pl-export-page
      style={{
        width: PAGE_WIDTH_PX,
        boxSizing: 'border-box',
        background: '#FFFFFF',
        padding: '28px 32px 24px',
        fontFamily: 'Calibri, "Segoe UI", sans-serif',
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

      <PlExportTableFrame
        data={data}
        year={year}
        month={month}
        planMap={planMap}
        columns={columns}
        monthly={monthly}
        pixelFontSize={BODY_FONT_PX}
        widthPx={tableWidth}
      />

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
