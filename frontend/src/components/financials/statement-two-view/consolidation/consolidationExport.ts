import type { ConsolidationResponse } from '../../../../lib/api'
import type { ConsolidationRow } from '../../../../lib/api'
import { buildFlatTablePptxConfig } from '../../../../lib/finssentialsExport/buildPptxExportConfig'
import { exportFinssentialsXlsx, flattenTreeForExport } from '../../../../lib/finssentialsExport'
import { exportFinssentialsPptx } from '../../../../lib/finssentialsExport/pptx/exportFinssentialsPptx'
import { computeAutoExpandedIds } from '../../statementRowExpansion'
import { todayStr } from '../../../../lib/exportXlsx'
import { labelActual } from '../../../../lib/periodColumnLabels'

const STATEMENT_TITLES: Record<string, string> = {
  pl: 'Income statement (consolidated)',
  bs: 'Balance sheet (consolidated)',
  cf: 'Cash flow statement (consolidated)',
  wc: 'Working capital (consolidated)',
}

function flattenConsolidationForExport(
  rows: ConsolidationRow[],
  entityCodes: string[],
  isRowOpen: (id: string) => boolean,
) {
  return flattenTreeForExport(rows, {
    isRowOpen,
    mapRow: row => {
      const r = row as ConsolidationRow
      const isKpi = r.row_kind === 'kpi'
      const kind =
        r.row_kind === 'title' || r.row_kind === 'kpi_header'
          ? ('section' as const)
          : r.row_kind === 'subtotal'
            ? ('subtotal' as const)
            : isKpi
              ? ('kpi' as const)
              : ('data' as const)
      const entityVals = entityCodes.map(code => {
        const v = r.entity_amounts[code]
        return v != null ? (isKpi ? +v.toFixed(1) : Math.round(v / 1000)) : null
      })
      const agg = isKpi ? null : Math.round(r.aggregated / 1000)
      const ic = isKpi ? null : Math.round(r.ic_eliminations / 1000)
      const cons = isKpi ? +r.consolidation.toFixed(1) : Math.round(r.consolidation / 1000)
      return {
        label: r.label,
        values: [...entityVals, agg, ic, cons],
        kind,
        kpiCols: isKpi
          ? entityCodes.map((_, i) => i).concat([entityCodes.length + 2])
          : undefined,
      }
    },
  })
}

export async function exportConsolidationXlsx(
  data: ConsolidationResponse,
  checkOpen?: (id: string) => boolean,
): Promise<void> {
  const stmtName = STATEMENT_TITLES[data.statement] ?? 'Consolidation'
  const entityCodes = data.entities.map(e => e.code)
  const autoExpanded = computeAutoExpandedIds(
    data.rows as unknown as import('../../../../lib/api').FinancialStatementRow[],
    data.statement,
  )
  const isRowOpen = checkOpen ?? ((id: string) => autoExpanded.has(id))
  const rows = flattenConsolidationForExport(data.rows, entityCodes, isRowOpen)
  await exportFinssentialsXlsx({
    tableTitle: `${stmtName} — entity breakdown`,
    subtitle: `Values in EURk · ${labelActual(data.col_label)}`,
    headers: ['EURk', ...data.entities.map(e => e.label), 'Aggregated', 'IC Elim.', 'Consolidation'],
    rows,
    filename: `Consolidation_${data.statement}_${todayStr()}.xlsx`,
  })
}

export async function exportConsolidationPptx(
  data: ConsolidationResponse,
  footerRight: string,
  checkOpen?: (id: string) => boolean,
): Promise<void> {
  const stmtName = STATEMENT_TITLES[data.statement] ?? 'Consolidation'
  const entityCodes = data.entities.map(e => e.code)
  const autoExpanded = computeAutoExpandedIds(
    data.rows as unknown as import('../../../../lib/api').FinancialStatementRow[],
    data.statement,
  )
  const isRowOpen = checkOpen ?? ((id: string) => autoExpanded.has(id))
  const flat = flattenConsolidationForExport(data.rows, entityCodes, isRowOpen)
  const headers = ['EURk', ...data.entities.map(e => e.label), 'Aggregated', 'IC Elim.', 'Consolidation']
  const cfg = buildFlatTablePptxConfig({
    fileName: `Consolidation_${data.statement}_${todayStr()}.pptx`,
    pageTitle: `${stmtName} (consolidated)`,
    tableHeading: `${stmtName} — entity breakdown`,
    breadcrumbCurrent: stmtName,
    footerRight,
    headers,
    rows: flat.map(r => ({
      label: r.label,
      values: r.values,
      kind: r.kind,
      indent: r.depth,
      kpiCols: r.kpiCols,
    })),
  })
  await exportFinssentialsPptx(cfg)
}
