import type { BsSnapshotPeriodKey, ErSnapshotColLabels } from '../../../lib/api'
import { labelActual } from '../../../lib/periodColumnLabels'

const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export type AnnualSnapshotTableColId =
  | BsSnapshotPeriodKey
  | 'delta_fy'
  | 'delta_cm'
  | 'cagr'

export type AnnualSnapshotTableColDef = {
  id: AnnualSnapshotTableColId
  labelLine1: string
  labelLine2?: string
  highlighted?: boolean
  isDelta?: boolean
  isPct?: boolean
}

/** Table-view columns: Dec22 … Jul25 + Δ + CAGR (matches main BS snapshot table + CAGR). */
export function buildAnnualSnapshotTableColumns(
  year: number,
  month: number,
  lbl?: ErSnapshotColLabels,
): AnnualSnapshotTableColDef[] {
  const abbr = MONTH_ABBR[month - 1] ?? 'Jan'
  const decPy2 = lbl?.dec_py2 ?? labelActual(`Dec${String(year - 3).slice(-2)}`)
  const decPy = lbl?.fy_py ?? labelActual(`Dec${String(year - 2).slice(-2)}`)
  const decCy = lbl?.fy ?? labelActual(`Dec${String(year - 1).slice(-2)}`)
  const cmPy = lbl?.cm_py ?? labelActual(`${abbr}${String(year - 1).slice(-2)}`)
  const cm = lbl?.cm ?? labelActual(`${abbr}${String(year).slice(-2)}`)
  return [
    { id: 'dec_py2', labelLine1: decPy2 },
    { id: 'fy_py', labelLine1: decPy },
    { id: 'fy', labelLine1: decCy },
    { id: 'cm_py', labelLine1: cmPy },
    { id: 'cm', labelLine1: cm, highlighted: true },
    { id: 'delta_fy', labelLine1: `Δ ${decCy} − ${decPy}`, isDelta: true },
    { id: 'delta_cm', labelLine1: `Δ ${cm} − ${cmPy}`, isDelta: true },
    { id: 'cagr', labelLine1: 'CAGR', isPct: true },
  ]
}
