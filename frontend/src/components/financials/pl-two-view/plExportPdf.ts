import { jsPDF } from 'jspdf'
import { createElement } from 'react'
import type { FinancialStatementResponse, MonthlyResponse, PlNarrativeResponse } from '../../../lib/api'
import type { PlNarrativeBullet } from './plNarrativeEngine'
import { drawNarrativeWithBadgesPdf } from '../../../lib/finssentialsExport/drawNarrativeWithBadgesPdf'
import type { PlPlanMap } from './usePlStatementData'
import type { PlTableColumnDef } from './plColumnRegistry'
import { captureReactNodeAsImage } from './plExportTableCapture'
import { buildStatementExportTable } from './plExport'
import FinssentialsExportTableCaptureRoot from '../../../lib/finssentialsExport/FinssentialsExportTableCaptureRoot'
import { buildExportCheckOpen } from '../../../lib/finssentialsExport/buildExportCheckOpen'
import { buildExportFooterLine, exportPdfFilename, type PlExportContext } from './plExportFooter'
import {
  COLOR_BODY,
  COLOR_BORDER,
  COLOR_MUTED,
  COLOR_NAVY,
  COLOR_SECTION,
  COLOR_TITLE,
  CONTENT_TOP_MM,
  FONT_BODY_PT,
  FONT_FOOTER_BRAND_PT,
  FONT_FOOTER_PT,
  FONT_SECTION_PT,
  FONT_TITLE_PT,
  NARRATIVE_W_MM,
  NARRATIVE_X_MM,
  MAIN_TITLE,
  MARGIN_MM,
  PAGE_W_MM,
  CONTENT_RIGHT_MM,
  SECTION_Y_MM,
  TABLE_W_MM,
  TABLE_X_MM,
  TITLE_Y_MM,
  captureScale,
  footerYPositions,
  maxTableHeightMm,
  pageHeightForTable,
} from './plExportPdfLayout'
import {
  KEY_DRIVERS_HEADING,
  buildReportTableHeading,
} from './plReportSectionHeadings'

export type { PlExportContext } from './plExportFooter'

/** Normalize text for jsPDF (entities, special chars). */
function pdfSafeText(s: string): string {
  if (!s) return s
  const el = document.createElement('textarea')
  el.innerHTML = s
  let t = el.value
  return t
    .replace(/[\u2013\u2014]/g, '-')
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201c\u201d]/g, '"')
    .replace(/€/g, 'EUR ')
}

function hexRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '')
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
}

function setTextHex(pdf: jsPDF, hex: string) {
  const [r, g, b] = hexRgb(hex)
  pdf.setTextColor(r, g, b)
}

function setDrawHex(pdf: jsPDF, hex: string) {
  const [r, g, b] = hexRgb(hex)
  pdf.setDrawColor(r, g, b)
}

function drawMainTitle(pdf: jsPDF, title: string) {
  pdf.setFont('helvetica', 'bold')
  pdf.setFontSize(FONT_TITLE_PT)
  setTextHex(pdf, COLOR_TITLE)
  pdf.text(pdfSafeText(title), MARGIN_MM, TITLE_Y_MM)
}

function drawSectionLabels(pdf: jsPDF, tableHeading: string) {
  pdf.setFont('helvetica', 'bold')
  pdf.setFontSize(FONT_SECTION_PT)
  setTextHex(pdf, COLOR_SECTION)
  pdf.text(tableHeading, TABLE_X_MM, SECTION_Y_MM)
  pdf.text(KEY_DRIVERS_HEADING, NARRATIVE_X_MM, SECTION_Y_MM, {
    align: 'left',
    maxWidth: NARRATIVE_W_MM,
  })
}

function drawRightNarrative(
  pdf: jsPDF,
  intro: string | null | undefined,
  bullets: PlNarrativeBullet[],
  narrativeMaxY: number,
) {
  drawNarrativeWithBadgesPdf(pdf, intro, bullets, {
    xMm: NARRATIVE_X_MM,
    wMm: NARRATIVE_W_MM,
    startYMm: CONTENT_TOP_MM,
    maxYMm: narrativeMaxY,
    fontBodyPt: FONT_BODY_PT,
    colorBody: COLOR_BODY,
    colorNavy: COLOR_NAVY,
  })
}

function drawFooter(
  pdf: jsPDF,
  data: FinancialStatementResponse,
  ctx: PlExportContext,
  lineY: number,
  textY: number,
) {
  setDrawHex(pdf, COLOR_BORDER)
  pdf.setLineWidth(0.3)
  pdf.line(MARGIN_MM, lineY, PAGE_W_MM - MARGIN_MM, lineY)

  pdf.setFont('helvetica', 'bold')
  pdf.setFontSize(FONT_FOOTER_BRAND_PT)
  setTextHex(pdf, COLOR_NAVY)
  pdf.text('Finssentials', MARGIN_MM, textY)

  pdf.setFont('helvetica', 'normal')
  pdf.setFontSize(FONT_FOOTER_PT)
  setTextHex(pdf, COLOR_MUTED)
  const footerLine = buildExportFooterLine(data, ctx)
  pdf.text(footerLine, PAGE_W_MM - MARGIN_MM, textY, { align: 'right', maxWidth: PAGE_W_MM - MARGIN_MM - 28 })
}

function embedTableImage(
  pdf: jsPDF,
  dataUrl: string,
  pixelW: number,
  pixelH: number,
  topY: number,
  maxW: number,
  maxH: number,
  leftXmm: number,
) {
  const aspect = pixelW / pixelH
  let w = maxW
  let h = w / aspect
  if (h > maxH) {
    h = maxH
    w = h * aspect
  }
  pdf.addImage(dataUrl, 'PNG', leftXmm, topY, w, h, undefined, 'FAST')
}

async function captureExportTable(
  data: FinancialStatementResponse,
  planMap: PlPlanMap,
  columns: PlTableColumnDef[],
  widthMm: number,
  isRowOpen: (id: string) => boolean,
  monthly: MonthlyResponse | null = null,
  commentMarkersByLineCode?: import('../statement-two-view/reportCommentMarkers').ReportCommentMarkerMap,
  excludeKpi?: boolean,
) {
  const model = buildStatementExportTable(
    excludeKpi ? data.rows.filter(r => r.row_kind !== 'kpi') : data.rows,
    columns,
    planMap,
    monthly,
    isRowOpen,
  )
  return captureReactNodeAsImage(
    createElement(FinssentialsExportTableCaptureRoot, {
      headers: model.headers,
      columnKinds: model.columnKinds,
      rows: model.visibleRows,
      commentMarkersByLineCode,
      pixelFontSize: Math.round(9 * (96 / 72)),
      widthMm,
    }),
    { scale: captureScale(), waitMs: 200 },
  )
}

export type FinStatementReportPdfOpts = {
  pageTitle: string
  tableHeading: string
  filePrefix?: string
}

/** Report-view PDF (table screenshot + key drivers) for any financial statement tab. */
export async function exportFinStatementReportViewPdf(
  data: FinancialStatementResponse,
  _year: number,
  _month: number,
  miniColumns: PlTableColumnDef[],
  planMap: PlPlanMap,
  bullets: PlNarrativeBullet[],
  exportCtx: PlExportContext,
  opts: FinStatementReportPdfOpts,
  narrative?: PlNarrativeResponse | null,
  isRowOpen?: (id: string) => boolean,
  commentMarkersByLineCode?: import('../statement-two-view/reportCommentMarkers').ReportCommentMarkerMap,
) {
  const intro = narrative?.intro ?? null
  const prefix = opts.filePrefix ?? 'PL'
  const checkOpen = isRowOpen ?? buildExportCheckOpen(data.rows, data.statement)
  const tableImage = await captureExportTable(
    data,
    planMap,
    miniColumns,
    TABLE_W_MM,
    checkOpen,
    null,
    commentMarkersByLineCode,
    false,
  )

  const tableHmmAtFullWidth =
    TABLE_W_MM * (tableImage.pixelHeight / tableImage.pixelWidth)
  const pageHmm = pageHeightForTable(tableHmmAtFullWidth)
  const { lineY, textY } = footerYPositions(pageHmm)

  const pdf = new jsPDF({
    orientation: 'landscape',
    unit: 'mm',
    format: [PAGE_W_MM, pageHmm],
  })
  drawMainTitle(pdf, opts.pageTitle)
  drawSectionLabels(pdf, opts.tableHeading)
  embedTableImage(
    pdf,
    tableImage.dataUrl,
    tableImage.pixelWidth,
    tableImage.pixelHeight,
    CONTENT_TOP_MM,
    TABLE_W_MM,
    maxTableHeightMm(pageHmm),
    TABLE_X_MM,
  )

  drawRightNarrative(pdf, intro, bullets, lineY - 4)

  drawFooter(pdf, data, exportCtx, lineY, textY)
  pdf.save(exportPdfFilename(exportCtx.entityLabel, data, 'Report', prefix))
}

export async function exportPlReportViewPdf(
  data: FinancialStatementResponse,
  year: number,
  month: number,
  miniColumns: PlTableColumnDef[],
  planMap: PlPlanMap,
  bullets: PlNarrativeBullet[],
  exportCtx: PlExportContext,
  narrative?: PlNarrativeResponse | null,
  commentMarkersByLineCode?: import('../statement-two-view/reportCommentMarkers').ReportCommentMarkerMap,
  isRowOpen?: (id: string) => boolean,
) {
  const useEntityHeading =
    exportCtx.entityLabel !== 'all' &&
    exportCtx.entityDisplayName !== 'All entities (consolidated)'
  const tableHeading = buildReportTableHeading(data.col_labels, {
    entityDisplayName: useEntityHeading ? exportCtx.entityDisplayName : undefined,
    consolidated: !useEntityHeading,
  })
  await exportFinStatementReportViewPdf(
    data,
    year,
    month,
    miniColumns,
    planMap,
    bullets,
    exportCtx,
    { pageTitle: MAIN_TITLE, tableHeading, filePrefix: 'PL' },
    narrative,
    isRowOpen,
    commentMarkersByLineCode,
  )
}

export async function exportFinStatementTableViewPdf(
  data: FinancialStatementResponse,
  _year: number,
  _month: number,
  columns: PlTableColumnDef[],
  planMap: PlPlanMap,
  monthly: MonthlyResponse | null,
  exportCtx: PlExportContext,
  pageTitle: string,
  filePrefix = 'PL',
  isRowOpen?: (id: string) => boolean,
) {
  const contentW = PAGE_W_MM - MARGIN_MM * 2
  const checkOpen = isRowOpen ?? buildExportCheckOpen(data.rows, data.statement)
  const tableImage = await captureExportTable(
    data,
    planMap,
    columns,
    contentW,
    checkOpen,
    monthly,
  )

  const tableHmmAtFullWidth =
    contentW * (tableImage.pixelHeight / tableImage.pixelWidth)
  const pageHmm = pageHeightForTable(tableHmmAtFullWidth)
  const { lineY, textY } = footerYPositions(pageHmm)

  const pdf = new jsPDF({
    orientation: 'landscape',
    unit: 'mm',
    format: [PAGE_W_MM, pageHmm],
  })

  drawMainTitle(pdf, pageTitle)
  embedTableImage(
    pdf,
    tableImage.dataUrl,
    tableImage.pixelWidth,
    tableImage.pixelHeight,
    CONTENT_TOP_MM,
    contentW,
    maxTableHeightMm(pageHmm),
    CONTENT_RIGHT_MM,
  )
  drawFooter(pdf, data, exportCtx, lineY, textY)
  pdf.save(exportPdfFilename(exportCtx.entityLabel, data, 'Table', filePrefix))
}

export async function exportPlTableViewPdf(
  data: FinancialStatementResponse,
  year: number,
  month: number,
  columns: PlTableColumnDef[],
  planMap: PlPlanMap,
  monthly: MonthlyResponse | null,
  exportCtx: PlExportContext,
  isRowOpen?: (id: string) => boolean,
) {
  await exportFinStatementTableViewPdf(
    data,
    year,
    month,
    columns,
    planMap,
    monthly,
    exportCtx,
    MAIN_TITLE,
    'PL',
    isRowOpen,
  )
}
