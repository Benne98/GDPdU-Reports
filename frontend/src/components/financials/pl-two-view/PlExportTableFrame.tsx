import { useMemo } from 'react'
import type { FinancialStatementResponse, MonthlyResponse } from '../../../lib/api'
import type { PlTableColumnDef } from './plColumnRegistry'
import { isReportPeriodHighlightColumn } from './plTableCore'
import type { PlPlanMap } from './usePlStatementData'
import { renderPlTableRows, type PlTableRenderCtx } from './plTableRowRenderer'
import { usePlRowExpansion } from './usePlRowExpansion'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'

type Props = {
  data: FinancialStatementResponse
  year: number
  month: number
  planMap: PlPlanMap
  columns: PlTableColumnDef[]
  monthly?: MonthlyResponse | null
  commentMarkersByLineCode?: ReportCommentMarkerMap
  /** Base font size in px — aligned with PPT narrative text (~6.5pt ≈ 9px). */
  pixelFontSize: number
  widthPx?: number
  /** UI expand state; when omitted uses default auto-expand per statement. */
  isRowOpen?: (id: string) => boolean
}

/** Static P&L table for off-screen capture (PowerPoint export). */
export default function PlExportTableFrame({
  data,
  year,
  month,
  planMap,
  columns,
  monthly = null,
  commentMarkersByLineCode,
  pixelFontSize,
  widthPx,
  isRowOpen: isRowOpenProp,
}: Props) {
  const { checkOpen: defaultCheckOpen, toggle } = usePlRowExpansion(data.rows, data.statement)
  const checkOpen = isRowOpenProp ?? defaultCheckOpen
  const subFont = Math.max(7, Math.round(pixelFontSize * 0.85))

  const ctx: PlTableRenderCtx = useMemo(
    () => ({
      data,
      year,
      month,
      planMap,
      monthly,
      columns,
      compact: true,
      commentMarkersByLineCode,
      exportFontPx: pixelFontSize,
      exportMode: true,
      onDrill: () => {},
      checkOpen,
      toggle,
    }),
    [data, year, month, planMap, monthly, columns, commentMarkersByLineCode, pixelFontSize, checkOpen, toggle],
  )

  return (
    <div
      data-pl-export-table
      style={{
        width: widthPx,
        overflow: 'hidden',
        background: '#FFFFFF',
        fontFamily: 'Calibri, "Segoe UI", sans-serif',
        fontSize: pixelFontSize,
        lineHeight: 1.35,
        color: '#475569',
      }}
    >
      <style>{`
        [data-pl-export-table] td,
        [data-pl-export-table] th,
        [data-pl-export-table] td span {
          font-size: inherit !important;
        }
        [data-pl-export-table] button,
        [data-pl-export-table] svg {
          display: none !important;
        }
      `}</style>
      <table
        style={{
          width: '100%',
          borderCollapse: 'collapse',
          fontSize: 'inherit',
          tableLayout: 'fixed',
        }}
      >
        <thead>
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
            <th
              style={{
                padding: '4px 8px',
                textAlign: 'left',
                fontWeight: 600,
                color: '#475569',
                whiteSpace: 'nowrap',
              }}
            >
              EURk
            </th>
            {commentMarkersByLineCode && (
              <th
                style={{
                  width: 20,
                  minWidth: 20,
                  maxWidth: 20,
                  padding: 0,
                  textAlign: 'center',
                  fontWeight: 500,
                  color: '#94A3B8',
                  fontSize: subFont,
                }}
              >
                #
              </th>
            )}
            {columns.map(c => (
              <th
                key={c.id}
                style={{
                  padding: '4px 6px',
                  textAlign: 'right',
                  fontWeight: 600,
                  color: '#475569',
                  whiteSpace: 'nowrap',
                  verticalAlign: 'bottom',
                  background: isReportPeriodHighlightColumn(c.kind) ? 'rgba(30,58,95,0.04)' : '#F8FAFC',
                }}
              >
                <span style={{ display: 'block', lineHeight: 1.25 }}>{c.labelLine1}</span>
                {c.labelLine2 && (
                  <span style={{ display: 'block', lineHeight: 1.2, fontWeight: 400, color: '#94A3B8', fontSize: subFont }}>
                    {c.labelLine2}
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{renderPlTableRows(ctx, data.rows, 0)}</tbody>
      </table>
    </div>
  )
}
