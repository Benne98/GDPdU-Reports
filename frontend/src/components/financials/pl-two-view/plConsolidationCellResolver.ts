import type {
  ConsolidationRow,
  FinancialStatementResponse,
  MonthlyResponse,
} from '../../../lib/api'
import { buildPlanMapFromStatement } from './plPlanMap'
import type { PlPlanMap } from './usePlStatementData'
import type { PlConsolidationColumnDef } from './plConsolidationColumnRegistry'
import { resolveCellValue } from './plColumnRegistry'
import type { PlTableColumnDef } from './plColumnRegistry'
import type { FinancialStatementRow } from '../../../lib/api'

export type ConsolidationCellTarget =
  | { kind: 'entity'; code: string }
  | { kind: 'aggregated' }
  | { kind: 'ic' }
  | { kind: 'consolidation' }

function findStatementRow(
  rows: FinancialStatementRow[],
  lineId: string,
  label?: string,
): FinancialStatementRow | null {
  for (const r of rows) {
    if (r.id === lineId) return r
    if (r.line_code) {
      const code = r.line_code
      if (
        lineId === `pl-${code}` ||
        lineId === `bs-${code}` ||
        lineId === `wc-${code}` ||
        lineId === `cf-${code}`
      ) {
        return r
      }
    }
    if (r.children?.length) {
      const c = findStatementRow(r.children, lineId, label)
      if (c) return c
    }
  }

  const plL4 = lineId.match(/^pl-(.+)-l4-/)
  if (plL4 && label) {
    const parent = findStatementRow(rows, `pl-${plL4[1]}`)
    if (parent?.children?.length) {
      for (const ch of parent.children) {
        if (ch.label === label || ch.line_code?.endsWith(`::${label}`)) return ch
      }
    }
  }
  return null
}

function baseCmValue(row: ConsolidationRow, target: ConsolidationCellTarget): number | null {
  switch (target.kind) {
    case 'entity': {
      const v = row.entity_amounts[target.code]
      if (v == null) return null
      return v
    }
    case 'aggregated':
      return row.aggregated
    case 'ic':
      return row.ic_eliminations
    case 'consolidation':
      return row.consolidation
  }
}

function statementForTarget(
  target: ConsolidationCellTarget,
  statementByEntity: Map<string, FinancialStatementResponse>,
  groupStatement: FinancialStatementResponse | null | undefined,
): FinancialStatementResponse | null {
  if (target.kind === 'entity') {
    return statementByEntity.get(target.code) ?? null
  }
  return groupStatement ?? null
}

export function resolveConsolidationCell(
  row: ConsolidationRow,
  target: ConsolidationCellTarget,
  extraCol: PlConsolidationColumnDef | null,
  statementByEntity: Map<string, FinancialStatementResponse>,
  groupStatement: FinancialStatementResponse | null | undefined,
  planMap: PlPlanMap,
  monthly?: MonthlyResponse | null,
): number | null {
  const isKpi = row.row_kind === 'kpi'

  if (!extraCol) {
    return baseCmValue(row, target)
  }

  if (isKpi && target.kind !== 'entity') {
    return null
  }

  const stmt = statementForTarget(target, statementByEntity, groupStatement)
  if (!stmt) return null

  const srow = findStatementRow(stmt.rows, row.id, row.label)
  if (!srow) return null

  const PLAN_COLUMN_KINDS = new Set([
    'plan_cm',
    'plan_vs_actual',
    'ytd_plan',
    'ytd_vs_plan',
    'ytg',
    'coverage',
  ])
  if (srow.row_kind === 'account' && PLAN_COLUMN_KINDS.has(extraCol.kind)) {
    return null
  }

  const asPlCol: PlTableColumnDef = {
    id: extraCol.id,
    kind: extraCol.kind,
    labelLine1: extraCol.labelLine1,
    periodKey: extraCol.periodKey,
    periodKeyA: extraCol.periodKeyA,
    periodKeyB: extraCol.periodKeyB,
  }

  const plan = target.kind === 'entity' ? planMap : buildPlanMapFromStatement(stmt)

  return resolveCellValue(srow, asPlCol, plan, monthly)
}

/** @deprecated use resolveConsolidationCell */
export function resolveConsolidationCm(
  row: ConsolidationRow,
  entityCode: string,
  _isKpi: boolean,
): number | null {
  return baseCmValue(row, { kind: 'entity', code: entityCode })
}

/** @deprecated use resolveConsolidationCell */
export function resolveConsolidationExtra(
  row: ConsolidationRow,
  entityCode: string,
  col: PlConsolidationColumnDef,
  statementByEntity: Map<string, FinancialStatementResponse>,
  planMap: PlPlanMap,
  monthly?: MonthlyResponse | null,
): number | null {
  return resolveConsolidationCell(
    row,
    { kind: 'entity', code: entityCode },
    col,
    statementByEntity,
    null,
    planMap,
    monthly,
  )
}

export function targetFromColumnDef(col: PlConsolidationColumnDef): ConsolidationCellTarget {
  switch (col.target) {
    case 'aggregated':
      return { kind: 'aggregated' }
    case 'consolidation':
      return { kind: 'consolidation' }
    case 'single_entity':
    case 'all_entities':
      return { kind: 'entity', code: col.entityCode ?? '' }
    default:
      return { kind: 'entity', code: col.entityCode ?? '' }
  }
}
