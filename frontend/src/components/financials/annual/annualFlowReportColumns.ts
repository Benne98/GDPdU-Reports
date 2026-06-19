import type { ErFlowColLabels } from '../../../lib/api'
import { labelActual, resolveAnnualForecastColumnLabel } from '../../../lib/periodColumnLabels'
import type { ErFlowColDef } from './ErFlowMiniTable'

const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** Report-view columns for annual PL/CF entity breakdown (no prior-year YTD; forecast not plan). */
export function buildAnnualFlowReportColumns(
  lbl: ErFlowColLabels | undefined,
  year: number,
  month: number,
): ErFlowColDef[] {
  const abbr = MONTH_ABBR[month - 1] ?? 'Jan'
  const fy2 = lbl?.fy2 ?? labelActual(`FY${String(year - 2).slice(-2)}`)
  const fy3 = lbl?.fy3 ?? labelActual(`FY${String(year - 1).slice(-2)}`)
  const ytd = lbl?.ytd ?? labelActual(`YTD${abbr}${String(year).slice(-2)}`)
  const forecast = resolveAnnualForecastColumnLabel(year, lbl?.fy_f ?? lbl?.ltm)
  return [
    { id: 'fy2', kind: 'amount', amountKey: 'fy2', flowCol: 'fy2', labelLine1: fy2, labelLine2: 'Full fiscal year' },
    { id: 'fy3', kind: 'amount', amountKey: 'fy3', flowCol: 'fy3', labelLine1: fy3, labelLine2: 'Full fiscal year' },
    { id: 'cagr', kind: 'amount', amountKey: 'cagr', labelLine1: 'CAGR', labelLine2: `${fy3} – ${fy2}`, isPct: true },
    { id: 'ytd', kind: 'amount', amountKey: 'ytd', flowCol: 'ytd', labelLine1: ytd, labelLine2: 'Year to date', highlighted: true },
    { id: 'forecast', kind: 'amount', amountKey: 'fy_f', flowCol: undefined, labelLine1: forecast, labelLine2: 'Forecast FY' },
    { id: 'coverage_pct', kind: 'amount', amountKey: 'coverage_pct', labelLine1: 'Coverage', labelLine2: 'YTD vs forecast %', isPct: true },
  ]
}
