/**
 * Shared report/table PDF & PPT exports for BS, WC, CF (and reused patterns).
 */
import type { FinancialStatementColLabels, FinancialStatementResponse, MonthlyResponse, PlNarrativeResponse } from '../../../lib/api'
import type { PlTableColumnDef } from '../pl-two-view/plColumnRegistry'
import type { PlNarrativeBullet } from '../pl-two-view/plNarrativeEngine'
import type { ReportCommentMarkerMap } from './reportCommentMarkers'
import {
  exportFinStatementReportViewPdf,
  exportFinStatementTableViewPdf,
  type PlExportContext,
} from '../pl-two-view/plExportPdf'
import {
  exportFinStatementReportViewPptx,
  exportFinStatementTableViewPptx,
} from '../pl-two-view/plExportPptx'
import { buildBsReportTableHeading } from './bs/bsReportSectionHeadings'
import { buildCfReportTableHeading } from './cf/cfReportSectionHeadings'
import { buildWcReportTableHeading } from './wc/wcReportSectionHeadings'

export type StatementTab = 'bs' | 'wc' | 'cf'

const META: Record<
  StatementTab,
  {
    filePrefix: string
    breadcrumbCurrent: string
    pageTitle: string
    buildTableHeading: (
      lbl: FinancialStatementColLabels,
      opts?: { entityDisplayName?: string },
    ) => string
  }
> = {
  bs: {
    filePrefix: 'BS',
    breadcrumbCurrent: 'Balance sheet',
    pageTitle: 'Balance Sheet (Consolidated)',
    buildTableHeading: buildBsReportTableHeading,
  },
  wc: {
    filePrefix: 'WC',
    breadcrumbCurrent: 'Working capital',
    pageTitle: 'Working Capital (Consolidated)',
    buildTableHeading: buildWcReportTableHeading,
  },
  cf: {
    filePrefix: 'CF',
    breadcrumbCurrent: 'Cash flow',
    pageTitle: 'Cash Flow (Consolidated)',
    buildTableHeading: buildCfReportTableHeading,
  },
}

function tableHeading(
  tab: StatementTab,
  lbl: FinancialStatementColLabels,
  exportCtx: PlExportContext,
): string {
  const useEntity =
    exportCtx.entityLabel !== 'all' &&
    exportCtx.entityDisplayName !== 'All entities (consolidated)'
  return META[tab].buildTableHeading(lbl, {
    entityDisplayName: useEntity ? exportCtx.entityDisplayName : undefined,
  })
}

export async function exportStatementReportViewPdf(
  tab: StatementTab,
  data: FinancialStatementResponse,
  year: number,
  month: number,
  miniColumns: PlTableColumnDef[],
  bullets: PlNarrativeBullet[],
  exportCtx: PlExportContext,
  narrative: PlNarrativeResponse | null | undefined,
  isRowOpen?: (id: string) => boolean,
  commentMarkersByLineCode?: ReportCommentMarkerMap,
): Promise<void> {
  const m = META[tab]
  await exportFinStatementReportViewPdf(
    data,
    year,
    month,
    miniColumns,
    {},
    bullets,
    exportCtx,
    {
      pageTitle: m.pageTitle,
      tableHeading: tableHeading(tab, data.col_labels, exportCtx),
      filePrefix: m.filePrefix,
    },
    narrative,
    isRowOpen,
    commentMarkersByLineCode,
  )
}

export async function exportStatementReportViewPptx(
  tab: StatementTab,
  data: FinancialStatementResponse,
  year: number,
  month: number,
  miniColumns: PlTableColumnDef[],
  bullets: PlNarrativeBullet[],
  exportCtx: PlExportContext,
  narrative: PlNarrativeResponse | null | undefined,
  commentMarkersByLineCode: ReportCommentMarkerMap | undefined,
  isRowOpen?: (id: string) => boolean,
): Promise<void> {
  const m = META[tab]
  await exportFinStatementReportViewPptx(
    data,
    year,
    month,
    miniColumns,
    {},
    bullets,
    exportCtx,
    {
      breadcrumbCurrent: m.breadcrumbCurrent,
      pageTitle: m.pageTitle,
      tableHeading: tableHeading(tab, data.col_labels, exportCtx),
      filePrefix: m.filePrefix,
      deckTitle: `${m.pageTitle} — ${exportCtx.entityDisplayName}`,
    },
    narrative,
    commentMarkersByLineCode,
    isRowOpen,
  )
}

export async function exportStatementTableViewPdf(
  tab: StatementTab,
  data: FinancialStatementResponse,
  year: number,
  month: number,
  columns: PlTableColumnDef[],
  monthly: MonthlyResponse | null,
  exportCtx: PlExportContext,
  isRowOpen?: (id: string) => boolean,
): Promise<void> {
  const m = META[tab]
  await exportFinStatementTableViewPdf(
    data,
    year,
    month,
    columns,
    {},
    monthly,
    exportCtx,
    m.pageTitle,
    m.filePrefix,
    isRowOpen,
  )
}

export async function exportStatementTableViewPptx(
  tab: StatementTab,
  data: FinancialStatementResponse,
  year: number,
  month: number,
  columns: PlTableColumnDef[],
  monthly: MonthlyResponse | null,
  exportCtx: PlExportContext,
  isRowOpen?: (id: string) => boolean,
): Promise<void> {
  const m = META[tab]
  await exportFinStatementTableViewPptx(
    data,
    year,
    month,
    columns,
    {},
    monthly,
    exportCtx,
    {
      breadcrumbCurrent: m.breadcrumbCurrent,
      pageTitle: m.pageTitle,
      tableHeading: tableHeading(tab, data.col_labels, exportCtx),
      filePrefix: m.filePrefix,
      deckTitle: `${m.pageTitle} — ${exportCtx.entityDisplayName}`,
    },
    isRowOpen,
  )
}
