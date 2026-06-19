import type { FinancialStatementResponse, FinancialStatementRow } from '../../../lib/api'
import type { PlPlanMap } from './usePlStatementData'

/** Build plan lookup from embedded `plan` block and/or `amounts.plan_cm` on statement rows. */
export function buildPlanMapFromStatement(data: FinancialStatementResponse | null): PlPlanMap {
  const m: PlPlanMap = {}
  if (!data) return m

  for (const l of data.plan?.lines ?? []) {
    m[l.line_code] = l
  }

  function walk(rows: FinancialStatementRow[]) {
    for (const r of rows) {
      if (!r.line_code || !r.amounts) {
        walk(r.children ?? [])
        walk(r.accounts ?? [])
        continue
      }
      const pcm = r.amounts.plan_cm
      const pva = r.amounts.plan_vs_actual
      if (pcm != null || pva != null) {
        const prev = m[r.line_code]
        m[r.line_code] = {
          line_code: r.line_code,
          plan_cm: pcm ?? prev?.plan_cm ?? 0,
          plan_vs_actual: pva ?? prev?.plan_vs_actual ?? 0,
          ytd_plan: prev?.ytd_plan ?? 0,
          ytg: prev?.ytg ?? 0,
          coverage_pct: prev?.coverage_pct ?? null,
        }
      }
      walk(r.children ?? [])
      walk(r.accounts ?? [])
    }
  }
  walk(data.rows)
  return m
}
