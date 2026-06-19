import type { FinancialStatementResponse, FinancialStatementRow } from '../../lib/api'
import { capturePlTableSnapshot } from './capturePlTable'
import type { PlTableColumnDef } from '../financials/pl-two-view/plColumnRegistry'

function collectIds(rows: FinancialStatementRow[], checkOpen: (id: string) => boolean): string[] {
  const out: string[] = []
  for (const row of rows) {
    if (checkOpen(row.id)) out.push(row.id)
    if (row.children?.length) out.push(...collectIds(row.children, checkOpen))
  }
  return out
}

export function buildPlTablePinSnapshot(
  data: FinancialStatementResponse,
  checkOpen: (id: string) => boolean,
  columns: PlTableColumnDef[],
  component: string,
) {
  const expanded = new Set(collectIds(data.rows, checkOpen))
  return capturePlTableSnapshot(data, expanded, columns, component)
}
