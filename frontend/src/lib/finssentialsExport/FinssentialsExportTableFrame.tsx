import PlCommentIndexBadge from '../../components/financials/pl-two-view/PlCommentIndexBadge'
import type { ReportCommentMarkerMap } from '../../components/financials/statement-two-view/reportCommentMarkers'
import { C, headerBgForColumn } from './designStyles'
import {
  exportTableBadgeDiameterPx,
  exportTableBadgeFontPx,
} from './narrativeBadgeLayout'
import { PPT_TABLE_W } from './reportSlideLayout'
import {
  argbToCss,
  exportCellCssBg,
  exportLabelCssColor,
  exportRowBorders,
  exportValueCssColor,
  formatExportCellValue,
} from './exportTableFormat'
import type { ExportFlatRow } from './flattenTreeForExport'

export type FinssentialsExportTableProps = {
  headers: string[]
  columnKinds: string[]
  /** Visible rows only (collapsed export — no `hidden` children). */
  rows: ExportFlatRow[]
  commentMarkersByLineCode?: ReportCommentMarkerMap
  pixelFontSize: number
  widthPx?: number
  commentBadgeDiameterPx?: number
}

/**
 * HTML table matching Excel_Oberstes Level.xlsx (top level + same rules when collapsed).
 */
export default function FinssentialsExportTableFrame({
  headers,
  columnKinds,
  rows,
  commentMarkersByLineCode,
  pixelFontSize,
  widthPx,
  commentBadgeDiameterPx: commentBadgeDiameterPxProp,
}: FinssentialsExportTableProps) {
  const subFont = Math.max(7, Math.round(pixelFontSize * 0.85))
  const hasComment = Boolean(commentMarkersByLineCode && Object.keys(commentMarkersByLineCode).length)
  const badgePx =
    commentBadgeDiameterPxProp ??
    (widthPx != null ? exportTableBadgeDiameterPx(widthPx, PPT_TABLE_W) : 12)
  const badgeFontPx = exportTableBadgeFontPx(badgePx)
  const valueColCount = Math.max(0, headers.length - 1)
  const headerBorder = `1px solid ${argbToCss(C.border)}`

  return (
    <div
      data-finssentials-export-table
      style={{
        width: widthPx,
        overflow: 'visible',
        background: '#FFFFFF',
        fontFamily: 'Calibri, "Segoe UI", sans-serif',
        fontSize: pixelFontSize,
        lineHeight: 1.35,
        color: exportLabelCssColor('data'),
      }}
    >
      <table
        style={{
          width: '100%',
          borderCollapse: 'collapse',
          fontSize: 'inherit',
          tableLayout: 'fixed',
        }}
      >
        <thead>
          <tr>
            <th
              style={{
                padding: '4px 8px',
                textAlign: 'left',
                fontWeight: 600,
                color: argbToCss(C.mid),
                background: argbToCss(headerBgForColumn(1, columnKinds[0])),
                borderBottom: headerBorder,
              }}
            >
              {headers[0] ?? 'EURk'}
            </th>
            {hasComment && (
              <th
                style={{
                  width: 20,
                  minWidth: 20,
                  maxWidth: 20,
                  padding: 0,
                  textAlign: 'center',
                  fontWeight: 600,
                  color: argbToCss(C.mid),
                  fontSize: subFont,
                  background: argbToCss(headerBgForColumn(1, columnKinds[0])),
                  borderBottom: headerBorder,
                }}
              >
                #
              </th>
            )}
            {headers.slice(1).map((h, i) => {
              const colIdx = i + 2
              const kind = columnKinds[colIdx - 1]
              return (
                <th
                  key={`h-${colIdx}`}
                  style={{
                    padding: '4px 6px',
                    textAlign: 'right',
                    fontWeight: 600,
                    color: argbToCss(C.mid),
                    whiteSpace: 'nowrap',
                    verticalAlign: 'bottom',
                    background: argbToCss(headerBgForColumn(colIdx, kind)),
                    borderBottom: headerBorder,
                  }}
                >
                  <span style={{ display: 'block', lineHeight: 1.25 }}>{h}</span>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => {
            if (row.kind === 'blank') {
              return (
                <tr key={row.id}>
                  <td colSpan={headers.length + (hasComment ? 1 : 0)} style={{ height: 6 }} />
                </tr>
              )
            }
            const bold =
              row.kind === 'section' || row.kind === 'title' || row.kind === 'subtotal'
            const italic = row.kind === 'kpi'
            const labelColor = exportLabelCssColor(row.kind)
            const indentPx = Math.min(row.depth, 6) * 10
            const rowBorders = exportRowBorders(row, rows[ri + 1])

            return (
              <tr key={row.id} style={rowBorders}>
                <td
                  style={{
                    padding: '3px 8px',
                    paddingLeft: 8 + indentPx,
                    textAlign: 'left',
                    fontWeight: bold ? 600 : 400,
                    fontStyle: italic ? 'italic' : undefined,
                    color: labelColor,
                    whiteSpace: 'nowrap',
                    background: exportCellCssBg(1, columnKinds[0], row.kind),
                    ...rowBorders,
                  }}
                >
                  {row.label}
                </td>
                {hasComment && (
                  <td
                    style={{
                      width: Math.max(badgePx + 6, 18),
                      textAlign: 'center',
                      verticalAlign: 'middle',
                      padding: '2px 3px',
                      overflow: 'visible',
                      background: exportCellCssBg(1, columnKinds[0], row.kind),
                      color: argbToCss(C.muted),
                      fontSize: subFont,
                      ...rowBorders,
                    }}
                  >
                    {row.lineCode && commentMarkersByLineCode?.[row.lineCode] ? (
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          minHeight: badgePx + 2,
                          overflow: 'visible',
                        }}
                      >
                        <PlCommentIndexBadge
                          marker={commentMarkersByLineCode[row.lineCode]}
                          variant="export"
                          sizePx={badgePx}
                          fontPx={badgeFontPx}
                        />
                      </div>
                    ) : null}
                  </td>
                )}
                {Array.from({ length: valueColCount }, (_, vi) => {
                  const colIdx = vi + 2
                  const kind = columnKinds[colIdx - 1]
                  const val = row.values[vi]
                  const cellColor =
                    typeof val === 'number'
                      ? exportValueCssColor(val, kind, row.kind, labelColor)
                      : labelColor
                  return (
                    <td
                      key={`${row.id}-v${vi}`}
                      style={{
                        padding: '3px 6px',
                        textAlign: 'right',
                        fontWeight: bold ? 600 : 400,
                        fontStyle: italic ? 'italic' : undefined,
                        color: cellColor,
                        whiteSpace: 'nowrap',
                        background: exportCellCssBg(colIdx, kind, row.kind),
                        ...rowBorders,
                      }}
                    >
                      {formatExportCellValue(val, row.kind, vi, row.kpiCols)}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
