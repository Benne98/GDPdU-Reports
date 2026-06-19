import type { FinStatementKind } from '../../components/financials/statement-two-view/statementTypes'

/** Column kinds shown in statement Report View (mini table) in the frontend. */
export const STATEMENT_REPORT_VIEW_KINDS = [
  'pm',
  'cm',
  'mom',
  'plan_cm',
  'plan_vs_actual',
] as const

const REPORT_KIND_SET = new Set<string>(STATEMENT_REPORT_VIEW_KINDS)

/** 1-based Excel column indices to collapse (column 1 = EURk label). */
export function collapsedExcelColumnsForKinds(
  dataColumnKinds: string[],
  _statement?: FinStatementKind,
): number[] {
  const collapsed: number[] = []
  dataColumnKinds.forEach((kind, i) => {
    if (!REPORT_KIND_SET.has(kind)) collapsed.push(i + 2)
  })
  return collapsed
}
