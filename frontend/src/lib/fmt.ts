/**
 * Number formatting utilities for Finssentials Cockpit.
 *
 * Convention:
 *  - Values are shown in thousands of euros (÷ 1 000)
 *  - Negative numbers use accounting parentheses: (11.436) instead of -11.436
 *  - European locale: "." as thousands separator, "," as decimal
 */

/** Main KPI card value — no € prefix, no unit suffix, integer thousands.
 *  e.g.  10 656 000 → "10.656"
 *        -11 436 000 → "(11.436)"
 */
export function fmtKpi(v: number): string {
  if (!Number.isFinite(v)) return ''
  const thousands = v / 1_000
  const abs = Math.abs(thousands)
  const s = abs.toLocaleString('de-DE', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
  return v < 0 ? `(${s})` : s
}

/** Delta row value — with € prefix, integer thousands (no decimals).
 *  e.g.  -847 600 → "(€ 848)"   (rounded display in k)
 *        +5 200   → "+€ 5"
 */
export function fmtDelta(v: number): string {
  const thousands = v / 1_000
  const abs = Math.abs(thousands)
  const s = `€ ${abs.toLocaleString('de-DE', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
  return v < 0 ? `(${s})` : `+${s}`
}

/** EBIT margin / percentage — one decimal, parentheses for negative.
 *  Pass the ratio already multiplied by 100 (e.g. 23.0 for 23.0%).
 *  e.g.  23.0 → "23,0%"   -13.6 → "(13,6%)"
 */
export function fmtPct(v: number): string {
  const abs = Math.abs(v)
  const s = abs.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '%'
  return v < 0 ? `(${s})` : s
}

/** Chart bar label — value already in kEUR (no ÷1000), integer, parentheses for negative.
 *  e.g.  10.656 → "10.656"   -11.436 → "(11.436)"
 */
export function fmtChartKpi(v: number): string {
  const abs = Math.abs(v)
  const s = abs.toLocaleString('de-DE', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
  return v < 0 ? `(${s})` : s
}

/** Days metric (DIO/DSO/DPO/CCC) — one decimal, no unit suffix.
 *  e.g.  45.3 → "45,3"   -3.1 → "(3,1)"
 */
export function fmtDays(v: number): string {
  const abs = Math.abs(v)
  const s = abs.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
  return v < 0 ? `(${s})` : s
}

/** Drill-down table amount — with € prefix, two decimals, k suffix.
 *  e.g.  -1 234 567.89 → "(€ 1.234,57k)"
 *         2 000        → "€ 2,00k"
 */
export function fmtAmount(v: number): string {
  const thousands = v / 1_000
  const abs = Math.abs(thousands)
  const s = `€ ${abs.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}k`
  return v < 0 ? `(${s})` : s
}

/** Aging register / tables — kEUR, no decimal places. */
export function fmtAmountWhole(v: number): string {
  const thousands = v / 1_000
  const abs = Math.abs(thousands)
  const s = `€ ${abs.toLocaleString('de-DE', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}k`
  return v < 0 ? `(${s})` : s
}

/**
 * Per-partner / per-document amounts — takes a value already in kEUR and returns
 * the equivalent in FULL EUR (× 1 000), formatted with de-DE thousands separators.
 * Use this wherever the aging API emits kEUR but the UI should show whole EUR.
 * Do NOT apply on top of fmtAmountWhole (different input unit).
 *
 * e.g.  125.75 kEUR → "€ 125.750"
 *        -3.2 kEUR   → "(€ 3.200)"
 */
export function fmtEurWhole(vKEur: number): string {
  const eur = vKEur * 1_000
  const abs = Math.abs(eur)
  const s = `€ ${abs.toLocaleString('de-DE', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
  return vKEur < 0 ? `(${s})` : s
}
