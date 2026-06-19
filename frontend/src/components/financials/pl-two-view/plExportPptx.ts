import type { FinancialStatementResponse, MonthlyResponse, PlNarrativeResponse } from '../../../lib/api'
import { todayStr } from '../../../lib/exportXlsx'
import {
  buildStatementReportPptxConfig,
  buildStatementTablePptxConfig,
} from '../../../lib/finssentialsExport/buildPptxExportConfig'
import { exportFinssentialsPptx } from '../../../lib/finssentialsExport/pptx/exportFinssentialsPptx'
import type { PlNarrativeBullet } from './plNarrativeEngine'
import type { PlPlanMap } from './usePlStatementData'
import type { PlTableColumnDef } from './plColumnRegistry'
import { buildConsolidatedTableHeading } from './plReportSectionHeadings'
import { buildExportFooterLine } from './plExportFooter'

export type PlPptxExportContext = {
  entityLabel: string
  entityDisplayName: string
}

export type FinStatementReportPptxOpts = {
  breadcrumbCurrent: string
  pageTitle: string
  tableHeading: string
  filePrefix?: string
  deckTitle?: string
}

export async function exportFinStatementReportViewPptx(
  data: FinancialStatementResponse,
  _year: number,
  _month: number,
  miniColumns: PlTableColumnDef[],
  planMap: PlPlanMap,
  bullets: PlNarrativeBullet[],
  exportCtx: PlPptxExportContext,
  opts: FinStatementReportPptxOpts,
  narrative?: PlNarrativeResponse | null,
  commentMarkersByLineCode?: import('../statement-two-view/reportCommentMarkers').ReportCommentMarkerMap,
  isRowOpen?: (id: string) => boolean,
) {
  const prefix = opts.filePrefix ?? 'PL'
  const cfg = buildStatementReportPptxConfig(
    data,
    miniColumns,
    planMap,
    bullets,
    buildExportFooterLine(data, exportCtx),
    {
      breadcrumbCurrent: opts.breadcrumbCurrent,
      pageTitle: opts.pageTitle,
      tableHeading: opts.tableHeading,
      filePrefix: prefix,
      deckTitle: opts.deckTitle ?? opts.pageTitle,
      entityLabel: exportCtx.entityLabel,
    },
    narrative,
    commentMarkersByLineCode,
    isRowOpen,
  )
  cfg.fileName = `${prefix}_Report_${exportCtx.entityLabel}_${data.year}-${String(data.month).padStart(2, '0')}_${todayStr()}.pptx`
  await exportFinssentialsPptx(cfg)
}

export async function exportPlReportViewPptx(
  data: FinancialStatementResponse,
  year: number,
  month: number,
  miniColumns: PlTableColumnDef[],
  planMap: PlPlanMap,
  bullets: PlNarrativeBullet[],
  exportCtx: PlPptxExportContext,
  narrative?: PlNarrativeResponse | null,
  commentMarkersByLineCode?: import('../statement-two-view/reportCommentMarkers').ReportCommentMarkerMap,
  isRowOpen?: (id: string) => boolean,
) {
  await exportFinStatementReportViewPptx(
    data,
    year,
    month,
    miniColumns,
    planMap,
    bullets,
    exportCtx,
    {
      breadcrumbCurrent: 'Income statement',
      pageTitle: 'Income Statement (Consolidated)',
      tableHeading: buildConsolidatedTableHeading(data.col_labels),
      filePrefix: 'PL',
      deckTitle: `Income Statement — ${exportCtx.entityDisplayName}`,
    },
    narrative,
    commentMarkersByLineCode,
    isRowOpen,
  )
}

export async function exportFinStatementTableViewPptx(
  data: FinancialStatementResponse,
  _year: number,
  _month: number,
  columns: PlTableColumnDef[],
  planMap: PlPlanMap,
  monthly: MonthlyResponse | null,
  exportCtx: PlPptxExportContext,
  opts: Pick<FinStatementReportPptxOpts, 'breadcrumbCurrent' | 'pageTitle' | 'tableHeading' | 'filePrefix' | 'deckTitle'>,
  isRowOpen?: (id: string) => boolean,
) {
  const prefix = opts.filePrefix ?? 'PL'
  const cfg = buildStatementTablePptxConfig(
    data,
    columns,
    planMap,
    monthly,
    buildExportFooterLine(data, exportCtx),
    {
      breadcrumbCurrent: opts.breadcrumbCurrent,
      pageTitle: opts.pageTitle,
      tableHeading: opts.tableHeading,
      filePrefix: prefix,
      deckTitle: opts.deckTitle ?? opts.pageTitle,
      entityLabel: exportCtx.entityLabel,
    },
    isRowOpen,
  )
  cfg.fileName = `${prefix}_Table_${exportCtx.entityLabel}_${data.year}-${String(data.month).padStart(2, '0')}_${todayStr()}.pptx`
  await exportFinssentialsPptx(cfg)
}

export async function exportPlTableViewPptx(
  data: FinancialStatementResponse,
  year: number,
  month: number,
  columns: PlTableColumnDef[],
  planMap: PlPlanMap,
  monthly: MonthlyResponse | null,
  exportCtx: PlPptxExportContext,
  isRowOpen?: (id: string) => boolean,
) {
  await exportFinStatementTableViewPptx(
    data,
    year,
    month,
    columns,
    planMap,
    monthly,
    exportCtx,
    {
      breadcrumbCurrent: 'Income statement',
      pageTitle: 'Income Statement (Consolidated)',
      tableHeading: buildConsolidatedTableHeading(data.col_labels),
      filePrefix: 'PL',
      deckTitle: `Income Statement Table — ${exportCtx.entityDisplayName}`,
    },
    isRowOpen,
  )
}
