import type {
  ConsolidationResponse,
  FinancialStatementResponse,
  MonthlyResponse,
  MonthlyRow,
  PlNarrativeResponse,
} from '../api'
import type { PlNarrativeBullet } from '../../components/financials/pl-two-view/plNarrativeEngine'
import type { PlTableColumnDef } from '../../components/financials/pl-two-view/plColumnRegistry'
import type { PlPlanMap } from '../../components/financials/pl-two-view/usePlStatementData'
import { buildStatementExportTable } from './buildStatementExportTable'
import { buildExportCheckOpen } from './buildExportCheckOpen'
import { flattenTreeForExport, type ExportFlatRow } from './flattenTreeForExport'
import type { FinssentialsPptxConfig } from './pptx/exportFinssentialsPptx'
import type { XlsxRow } from '../exportXlsx'
import type { MonthlyViewColumnDef } from '../../components/financials/pl-two-view/monthlyColumnRegistry'
import {
  resolveMonthlyColumnValue,
} from '../../components/financials/pl-two-view/monthlyColumnRegistry'
import { monthlyPeriodKey } from '../../components/financials/pl-two-view/plMonthlyLineCode'
import { periodLabel } from '../../components/financials/pl-two-view/plPeriodLabels'
import type { ConsolidationRow } from '../api'
import type { PlConsolidationColumnDef } from '../../components/financials/pl-two-view/plConsolidationColumnRegistry'
import {
  resolveConsolidationCell,
  targetFromColumnDef,
  type ConsolidationCellTarget,
} from '../../components/financials/pl-two-view/plConsolidationCellResolver'
import { buildPlanMapFromStatement } from '../../components/financials/pl-two-view/plPlanMap'
import {
  buildReportCommentMarkerMap,
  type ReportCommentMarkerMap,
} from '../../components/financials/statement-two-view/reportCommentMarkers'
import type { MonthlyResponse as MonthlyResp } from '../api'

function visibleOnly(rows: ExportFlatRow[]): ExportFlatRow[] {
  return rows.filter(r => !r.hidden && r.kind !== 'blank')
}

function xlsxRowsToFlat(rows: XlsxRow[]): ExportFlatRow[] {
  return rows.map((r, i) => ({
    id: `flat-${i}`,
    label: (r.label ?? '').replace(/^\s+/, ''),
    values: r.values,
    kind: r.kind,
    depth: r.indent ?? 0,
    outlineLevel: Math.min(r.indent ?? 0, 7),
    hidden: false,
    kpiCols: r.kpiCols,
  }))
}

export type StatementPptxHeaderOpts = {
  breadcrumbCurrent: string
  pageTitle: string
  tableHeading: string
  breadcrumbParent?: string
  deckTitle?: string
  filePrefix: string
  entityLabel: string
}

export function buildStatementReportPptxConfig(
  data: FinancialStatementResponse,
  miniColumns: PlTableColumnDef[],
  planMap: PlPlanMap,
  bullets: PlNarrativeBullet[],
  footerRight: string,
  header: StatementPptxHeaderOpts,
  narrative?: PlNarrativeResponse | null,
  commentMarkersByLineCode?: ReportCommentMarkerMap,
  isRowOpen?: (id: string) => boolean,
): FinssentialsPptxConfig {
  const checkOpen = isRowOpen ?? buildExportCheckOpen(data.rows, data.statement)
  const model = buildStatementExportTable(
    data.rows,
    miniColumns,
    planMap,
    null,
    checkOpen,
  )
  const commentMap =
    commentMarkersByLineCode ??
    buildReportCommentMarkerMap(bullets, data.rows, checkOpen)

  return {
    layout: 'report',
    fileName: `${header.filePrefix}_Report_${header.entityLabel}_${data.year}-${String(data.month).padStart(2, '0')}.pptx`,
    deckTitle: header.deckTitle ?? header.pageTitle,
    header: {
      breadcrumbCurrent: header.breadcrumbCurrent,
      pageTitle: header.pageTitle,
      breadcrumbParent: header.breadcrumbParent,
    },
    tableHeading: header.tableHeading,
    footerRight,
    table: {
      headers: model.headers,
      columnKinds: model.columnKinds,
      visibleRows: model.visibleRows,
      commentMarkersByLineCode: commentMap,
    },
    narrative: {
      intro: narrative?.intro ?? null,
      bullets,
    },
  }
}

export function buildStatementTablePptxConfig(
  data: FinancialStatementResponse,
  columns: PlTableColumnDef[],
  planMap: PlPlanMap,
  monthly: MonthlyResp | null,
  footerRight: string,
  header: StatementPptxHeaderOpts,
  isRowOpen?: (id: string) => boolean,
): FinssentialsPptxConfig {
  const checkOpen = isRowOpen ?? buildExportCheckOpen(data.rows, data.statement)
  const model = buildStatementExportTable(data.rows, columns, planMap, monthly, checkOpen)

  return {
    layout: 'tableOnly',
    fileName: `${header.filePrefix}_Table_${header.entityLabel}_${data.year}-${String(data.month).padStart(2, '0')}.pptx`,
    deckTitle: header.deckTitle ?? header.pageTitle,
    header: {
      breadcrumbCurrent: header.breadcrumbCurrent,
      pageTitle: header.pageTitle,
      breadcrumbParent: header.breadcrumbParent,
    },
    tableHeading: header.tableHeading,
    footerRight,
    table: {
      headers: model.headers,
      columnKinds: model.columnKinds,
      visibleRows: model.visibleRows,
    },
  }
}

function flattenMonthlyForExport(
  rows: MonthlyRow[],
  periodKeys: string[],
  extraColumns: MonthlyViewColumnDef[],
  planByPeriod: Map<string, PlPlanMap>,
  isRowOpen: (id: string) => boolean,
): ExportFlatRow[] {
  return flattenTreeForExport(rows, {
    isRowOpen,
    mapRow: row => {
      const r = row as MonthlyRow
      const values: (number | null)[] = []
      for (const pk of periodKeys) {
        const v = r.amounts?.[pk]
        values.push(v != null ? Math.round(v / (r.row_kind === 'kpi' ? 1 : 1000)) : null)
      }
      for (const col of extraColumns) {
        const v = resolveMonthlyColumnValue(r, col, planByPeriod)
        if (v == null) values.push(null)
        else if (r.row_kind === 'kpi') values.push(+v.toFixed(1))
        else values.push(Math.round(v / 1000))
      }
      const kind =
        r.row_kind === 'title'
          ? ('section' as const)
          : r.row_kind === 'subtotal'
            ? ('subtotal' as const)
            : r.row_kind === 'kpi'
              ? ('kpi' as const)
              : ('data' as const)
      return {
        label: r.label,
        values,
        kind,
        kpiCols: r.row_kind === 'kpi' ? periodKeys.map((_, i) => i) : undefined,
      }
    },
  })
}

export function buildMonthlyPptxConfig(
  data: MonthlyResponse,
  extraColumns: MonthlyViewColumnDef[],
  planByPeriod: Map<string, PlPlanMap>,
  title: string,
  footerRight: string,
  header: { breadcrumbCurrent: string; pageTitle: string; breadcrumbParent?: string },
  checkOpen?: (id: string) => boolean,
): FinssentialsPptxConfig {
  const periodKeys = data.periods.map(p => monthlyPeriodKey(p.year, p.month))
  const headers = [
    'EURk',
    ...data.periods.map(p => periodLabel(p.year, p.month)),
    ...extraColumns.map(c => c.labelLine1),
  ]
  const columnKinds = ['', ...periodKeys.map(() => ''), ...extraColumns.map(c => c.kind)]
  const isRowOpen =
    checkOpen ??
    buildExportCheckOpen(
      data.rows as unknown as FinancialStatementResponse['rows'],
      data.statement,
    )
  const rows = visibleOnly(
    flattenMonthlyForExport(data.rows, periodKeys, extraColumns, planByPeriod, isRowOpen),
  )
  const stamp = monthlyPeriodKey(data.year, data.month)

  return {
    layout: 'tableOnly',
    fileName: `Monthly_${data.statement}_${stamp}.pptx`,
    deckTitle: header.pageTitle,
    header: {
      breadcrumbCurrent: header.breadcrumbCurrent,
      pageTitle: header.pageTitle,
      breadcrumbParent: header.breadcrumbParent ?? 'Financials',
    },
    tableHeading: title,
    footerRight,
    table: { headers, columnKinds, visibleRows: rows },
  }
}

type ExportSubCol = {
  header: string
  target: ConsolidationCellTarget
  col?: PlConsolidationColumnDef
}

function buildConsolidationExportColumns(
  consol: ConsolidationResponse,
  extraColumns: PlConsolidationColumnDef[],
): ExportSubCol[] {
  const cmLabel = consol.col_label ? `${consol.col_label}A` : 'CM'
  const cols: ExportSubCol[] = []
  for (const e of consol.entities) {
    cols.push({ header: `${e.label} — ${cmLabel}`, target: { kind: 'entity', code: e.code } })
    for (const c of extraColumns.filter(x => x.target === 'single_entity' && x.entityCode === e.code)) {
      cols.push({
        header: `${e.label} — ${c.labelLine1}`,
        target: targetFromColumnDef(c),
        col: c,
      })
    }
  }
  cols.push({ header: `Aggregated — ${cmLabel}`, target: { kind: 'aggregated' } })
  for (const c of extraColumns.filter(x => x.target === 'aggregated')) {
    cols.push({ header: `Aggregated — ${c.labelLine1}`, target: targetFromColumnDef(c), col: c })
  }
  cols.push({ header: `IC Elim. — ${cmLabel}`, target: { kind: 'ic' } })
  cols.push({ header: `Consolidation — ${cmLabel}`, target: { kind: 'consolidation' } })
  for (const c of extraColumns.filter(x => x.target === 'consolidation')) {
    cols.push({ header: `Consolidation — ${c.labelLine1}`, target: targetFromColumnDef(c), col: c })
  }
  return cols
}

function flattenConsolidationForExport(
  rows: ConsolidationRow[],
  exportCols: ExportSubCol[],
  stmtMap: Map<string, FinancialStatementResponse>,
  groupStatement: FinancialStatementResponse | null,
  entityPlans: Map<string, PlPlanMap>,
  groupPlan: PlPlanMap,
  monthly: MonthlyResponse | null | undefined,
  isRowOpen: (id: string) => boolean,
): ExportFlatRow[] {
  const planForTarget = (target: ConsolidationCellTarget): PlPlanMap => {
    if (target.kind === 'entity') return entityPlans.get(target.code) ?? {}
    return groupPlan
  }
  return flattenTreeForExport(rows, {
    isRowOpen,
    mapRow: row => {
      const r = row as ConsolidationRow
      const isKpi = r.row_kind === 'kpi'
      const values: (number | null)[] = exportCols.map(ec => {
        const plan = planForTarget(ec.target)
        const raw = resolveConsolidationCell(
          r,
          ec.target,
          ec.col ?? null,
          stmtMap,
          groupStatement,
          plan,
          monthly,
        )
        if (raw == null) return null
        if (isKpi) return +raw.toFixed(1)
        return Math.round(raw / 1000)
      })
      const kind =
        r.row_kind === 'title' || r.row_kind === 'kpi_header'
          ? ('section' as const)
          : r.row_kind === 'subtotal'
            ? ('subtotal' as const)
            : isKpi
              ? ('kpi' as const)
              : ('data' as const)
      return {
        label: r.label,
        values,
        kind,
        kpiCols: isKpi ? values.map((_, i) => i) : undefined,
      }
    },
  })
}

export function buildConsolidationPptxConfig(
  consol: ConsolidationResponse,
  extraColumns: PlConsolidationColumnDef[],
  stmtMap: Map<string, FinancialStatementResponse>,
  groupStatement: FinancialStatementResponse | null,
  monthly: MonthlyResponse | null | undefined,
  footerRight: string,
  header: { breadcrumbCurrent: string; pageTitle: string; breadcrumbParent?: string },
  checkOpen?: (id: string) => boolean,
): FinssentialsPptxConfig {
  const exportCols = buildConsolidationExportColumns(consol, extraColumns)
  const entityPlans = new Map<string, PlPlanMap>()
  for (const [code, stmt] of stmtMap) {
    entityPlans.set(code, buildPlanMapFromStatement(stmt))
  }
  const groupPlan = groupStatement ? buildPlanMapFromStatement(groupStatement) : {}
  const isRowOpen =
    checkOpen ??
    buildExportCheckOpen(
      consol.rows as unknown as FinancialStatementResponse['rows'],
      'pl',
    )
  const headers = ['EURk', ...exportCols.map(c => c.header)]
  const columnKinds = ['', ...exportCols.map(() => 'cm')]
  const rows = visibleOnly(
    flattenConsolidationForExport(
      consol.rows,
      exportCols,
      stmtMap,
      groupStatement,
      entityPlans,
      groupPlan,
      monthly,
      isRowOpen,
    ),
  )

  return {
    layout: 'tableOnly',
    fileName: `PL_Entity_Breakdown_${consol.col_label}.pptx`,
    deckTitle: header.pageTitle,
    header: {
      breadcrumbCurrent: header.breadcrumbCurrent,
      pageTitle: header.pageTitle,
      breadcrumbParent: header.breadcrumbParent ?? 'Financials',
    },
    tableHeading: 'Income statement (consolidated) — entity breakdown',
    footerRight,
    table: { headers, columnKinds, visibleRows: rows },
  }
}

export function buildFlatTablePptxConfig(opts: {
  fileName: string
  pageTitle: string
  tableHeading: string
  breadcrumbCurrent: string
  breadcrumbParent?: string
  footerRight: string
  headers: string[]
  columnKinds?: string[]
  rows: XlsxRow[]
}): FinssentialsPptxConfig {
  const kinds = opts.columnKinds ?? opts.headers.map(() => '')
  return {
    layout: 'tableOnly',
    fileName: opts.fileName.endsWith('.pptx') ? opts.fileName : `${opts.fileName}.pptx`,
    deckTitle: opts.pageTitle,
    header: {
      breadcrumbCurrent: opts.breadcrumbCurrent,
      pageTitle: opts.pageTitle,
      breadcrumbParent: opts.breadcrumbParent ?? 'Financials',
    },
    tableHeading: opts.tableHeading,
    footerRight: opts.footerRight,
    table: {
      headers: opts.headers,
      columnKinds: kinds,
      visibleRows: visibleOnly(xlsxRowsToFlat(opts.rows)),
    },
  }
}
