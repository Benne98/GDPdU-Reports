import type { ConsolidationRow, ErStatementRow } from '../../../lib/api'

/** Below ~0.5 EUR in EURk display — treats rounded zero cells as empty. */
const ROW_ZERO_EPS = 0.005

export function isNearZeroAmount(n: number): boolean {
  return Math.abs(n) < ROW_ZERO_EPS
}

function allNumericValuesNearZero(values: number[]): boolean {
  return values.length === 0 || values.every(isNearZeroAmount)
}

export function isErStatementRowAllZero(row: ErStatementRow): boolean {
  if (!row.amounts) return true
  const amounts = Object.values(row.amounts).map(v => Number(v ?? 0))
  const deltas = Object.values(row.deltas ?? {}).map(v => Number(v ?? 0))
  return allNumericValuesNearZero(amounts) && allNumericValuesNearZero(deltas)
}

function erRowBranchHasNonZero(row: ErStatementRow): boolean {
  for (const ch of row.children ?? []) {
    if (shouldDisplayErStatementRow(ch)) return true
  }
  for (const acc of row.accounts ?? []) {
    if (shouldDisplayErStatementRow(acc)) return true
  }
  return false
}

/** Hide mapping lines that are zero in every column (unless a child branch has values). */
export function shouldDisplayErStatementRow(row: ErStatementRow): boolean {
  if (row.row_kind === 'title' || row.row_kind === 'kpi_header') return true
  if (!isErStatementRowAllZero(row)) return true
  return erRowBranchHasNonZero(row)
}

export function isConsolidationRowAllZero(row: ConsolidationRow, entityCodes: string[]): boolean {
  const vals = entityCodes.map(c => Number(row.entity_amounts[c] ?? 0))
  vals.push(
    Number(row.aggregated ?? 0),
    Number(row.consolidation ?? 0),
    Number(row.ic_eliminations ?? 0),
  )
  return allNumericValuesNearZero(vals)
}

function consolidationBranchHasNonZero(row: ConsolidationRow, entityCodes: string[]): boolean {
  for (const ch of row.children ?? []) {
    if (shouldDisplayConsolidationRow(ch, entityCodes)) return true
  }
  return false
}

export function shouldDisplayConsolidationRow(
  row: ConsolidationRow,
  entityCodes: string[],
): boolean {
  if (row.row_kind === 'title' || row.row_kind === 'kpi_header') return true
  // KPI rows render as a complete block (like their header): keep them even when a
  // given KPI is all-zero — e.g. WC DIO/DPO are 0 when there is no COGS in scope,
  // but they must still appear (in days) alongside DSO/CCC.
  if (row.row_kind === 'kpi') return true
  if (!isConsolidationRowAllZero(row, entityCodes)) return true
  return consolidationBranchHasNonZero(row, entityCodes)
}

export function filterVisibleConsolidationRows(
  rows: ConsolidationRow[],
  entityCodes: string[],
): ConsolidationRow[] {
  const filtered = rows.filter(r => shouldDisplayConsolidationRow(r, entityCodes))
  const hasKpi = filtered.some(r => r.row_kind === 'kpi')
  if (!hasKpi) {
    return filtered.filter(r => r.row_kind !== 'kpi_header')
  }
  return filtered
}
