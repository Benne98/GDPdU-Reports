import { computeAutoExpandedIds } from '../../components/financials/statementRowExpansion'
import type { FinancialStatementRow } from '../api'

/** Same expand logic as FinancialStatementTable / MonthlyTable. */
export function buildExportCheckOpen(
  rows: FinancialStatementRow[] | undefined,
  statement: string | undefined,
  userToggles?: Set<string>,
): (id: string) => boolean {
  const autoExpandedIds = computeAutoExpandedIds(rows, statement)
  if (!userToggles?.size) {
    return (id: string) => autoExpandedIds.has(id)
  }
  return (id: string) => autoExpandedIds.has(id) !== userToggles.has(id)
}
