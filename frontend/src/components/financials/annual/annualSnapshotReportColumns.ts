import type { ErSnapshotColLabels } from '../../../lib/api'
import { labelActual, resolveAnnualForecastColumnLabel } from '../../../lib/periodColumnLabels'

const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export type AnnualSnapshotColId = 'dec_py2' | 'fy_py' | 'fy' | 'cm_py' | 'fy_f' | 'cm'

export type AnnualSnapshotReportColId = AnnualSnapshotColId | 'delta_f'

export type AnnualSnapshotColDef = {
  id: AnnualSnapshotReportColId
  labelLine1: string
  highlighted?: boolean
  /** Read value from row.deltas[id] instead of amounts. */
  isDelta?: boolean
}

/** Report-view columns: Dec22 … Jul24 | FY25F | Δ FY25F−Jul25 | Jul25 (anchor month). */
export function buildAnnualSnapshotReportColumns(
  year: number,
  month: number,
  lbl?: ErSnapshotColLabels,
): AnnualSnapshotColDef[] {
  const abbr = MONTH_ABBR[month - 1] ?? 'Jan'
  const decPy2 = lbl?.dec_py2 ?? labelActual(`Dec${String(year - 3).slice(-2)}`)
  const decPy = lbl?.fy_py ?? labelActual(`Dec${String(year - 2).slice(-2)}`)
  const decCy = lbl?.fy ?? labelActual(`Dec${String(year - 1).slice(-2)}`)
  const cmPy = lbl?.cm_py ?? labelActual(`${abbr}${String(year - 1).slice(-2)}`)
  const fyF = resolveAnnualForecastColumnLabel(year, lbl?.fy_f)
  const cm = lbl?.cm ?? labelActual(`${abbr}${String(year).slice(-2)}`)
  return [
    { id: 'dec_py2', labelLine1: decPy2 },
    { id: 'fy_py', labelLine1: decPy },
    { id: 'fy', labelLine1: decCy },
    { id: 'cm_py', labelLine1: cmPy },
    { id: 'fy_f', labelLine1: fyF },
    { id: 'delta_f', labelLine1: `Δ ${fyF} − ${cm}`, isDelta: true },
    { id: 'cm', labelLine1: cm, highlighted: true },
  ]
}
