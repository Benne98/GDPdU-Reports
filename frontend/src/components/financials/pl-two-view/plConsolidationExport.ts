import type { ConsolidationResponse, FinancialStatementResponse, MonthlyResponse } from '../../../lib/api'
import { exportFinssentialsXlsx, flattenTreeForExport } from '../../../lib/finssentialsExport'
import { buildConsolidationPptxConfig } from '../../../lib/finssentialsExport/buildPptxExportConfig'
import { exportFinssentialsPptx } from '../../../lib/finssentialsExport/pptx/exportFinssentialsPptx'
import { buildExportCheckOpen } from '../../../lib/finssentialsExport/buildExportCheckOpen'
import type { PlConsolidationColumnDef } from './plConsolidationColumnRegistry'
import {
  resolveConsolidationCell,
  targetFromColumnDef,
  type ConsolidationCellTarget,
} from './plConsolidationCellResolver'
import { buildPlanMapFromStatement } from './plPlanMap'
import type { PlPlanMap } from './usePlStatementData'
import type { ConsolidationRow } from '../../../lib/api'
import { labelActual } from '../../../lib/periodColumnLabels'

type ExportSubCol = {
  header: string
  target: ConsolidationCellTarget
  col?: PlConsolidationColumnDef
}

function buildExportColumns(
  consol: ConsolidationResponse,
  extraColumns: PlConsolidationColumnDef[],
): ExportSubCol[] {
  const cmLabel = consol.col_label ? labelActual(consol.col_label) : 'CM'
  const cols: ExportSubCol[] = []

  for (const e of consol.entities) {
    cols.push({
      header: `${e.label} — ${cmLabel}`,
      target: { kind: 'entity', code: e.code },
    })
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

function planForTarget(
  target: ConsolidationCellTarget,
  entityPlans: Map<string, PlPlanMap>,
  groupPlan: PlPlanMap,
): PlPlanMap {
  if (target.kind === 'entity') return entityPlans.get(target.code) ?? {}
  return groupPlan
}

function flattenConsolidationPlForExport(
  rows: ConsolidationRow[],
  exportCols: ExportSubCol[],
  stmtMap: Map<string, FinancialStatementResponse>,
  groupStatement: FinancialStatementResponse | null,
  entityPlans: Map<string, PlPlanMap>,
  groupPlan: PlPlanMap,
  monthly: MonthlyResponse | null | undefined,
  isRowOpen: (id: string) => boolean,
) {
  return flattenTreeForExport(rows, {
    isRowOpen,
    mapRow: row => {
      const r = row as ConsolidationRow
      const isKpi = r.row_kind === 'kpi'
      const values: (number | null)[] = exportCols.map(ec => {
        const plan = planForTarget(ec.target, entityPlans, groupPlan)
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

export async function exportConsolidationTableXlsx(
  consol: ConsolidationResponse,
  extraColumns: PlConsolidationColumnDef[],
  stmtMap: Map<string, FinancialStatementResponse>,
  groupStatement: FinancialStatementResponse | null,
  _legacyPlanMap: PlPlanMap,
  monthly?: MonthlyResponse | null,
  checkOpen?: (id: string) => boolean,
): Promise<void> {
  const exportCols = buildExportColumns(consol, extraColumns)
  const entityPlans = new Map<string, PlPlanMap>()
  for (const [code, stmt] of stmtMap) {
    entityPlans.set(code, buildPlanMapFromStatement(stmt))
  }
  const groupPlan = groupStatement ? buildPlanMapFromStatement(groupStatement) : {}

  const isRowOpen =
    checkOpen ??
    buildExportCheckOpen(
      consol.rows as unknown as import('../../../lib/api').FinancialStatementRow[],
      'pl',
    )

  const headers = ['EURk', ...exportCols.map(c => c.header)]
  const rows = flattenConsolidationPlForExport(
    consol.rows,
    exportCols,
    stmtMap,
    groupStatement,
    entityPlans,
    groupPlan,
    monthly,
    isRowOpen,
  )

  await exportFinssentialsXlsx({
    tableTitle: 'Income statement (consolidated) — entity breakdown',
    subtitle: `Values in EURk · ${consol.col_label ?? ''}A`,
    headers,
    rows,
    filename: `PL_Entity_Breakdown_${consol.col_label}.xlsx`,
  })
}

export async function exportConsolidationTablePptx(
  consol: ConsolidationResponse,
  extraColumns: PlConsolidationColumnDef[],
  stmtMap: Map<string, FinancialStatementResponse>,
  groupStatement: FinancialStatementResponse | null,
  monthly: MonthlyResponse | null | undefined,
  footerRight: string,
  checkOpen?: (id: string) => boolean,
): Promise<void> {
  const cfg = buildConsolidationPptxConfig(
    consol,
    extraColumns,
    stmtMap,
    groupStatement,
    monthly,
    footerRight,
    {
      breadcrumbCurrent: 'Income statement',
      pageTitle: 'Entity breakdown (consolidated)',
    },
    checkOpen,
  )
  await exportFinssentialsPptx(cfg)
}
