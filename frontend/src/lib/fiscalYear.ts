/**
 * fiscalYear.ts — shared fiscal-year label helpers.
 *
 * The stored value for a fiscal year is always the calendar (ending) YEAR
 * integer (e.g. 2024). Only the display label changes based on the
 * fiscal-year end month.
 */

/** Zero-pad a two-digit year suffix, e.g. 2024 → "24", 2003 → "03". */
function two(y: number): string {
  return String(y % 100).padStart(2, '0')
}

/**
 * Returns the display label for a fiscal year given the ending month.
 *
 * - December year-end (fyEndMonth === 12): "FY2024"
 * - Any other month (non-December split FY): "FY23/24"
 *   where the stored calendar year is the ENDING year.
 *
 * Examples:
 *   glFiscalYearLabel(2024, 12) → "FY2024"
 *   glFiscalYearLabel(2023, 6)  → "FY22/23"
 *   glFiscalYearLabel(2026, 7)  → "FY25/26"
 */
export function glFiscalYearLabel(year: number, fyEndMonth: number): string {
  if (fyEndMonth === 12) {
    return `FY${year}`
  }
  return `FY${two(year - 1)}/${two(year)}`
}

/**
 * Returns a short description of the fiscal year end month for captions.
 * e.g. 12 → "December", 7 → "July"
 */
export const MONTH_NAMES_FULL = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
] as const

export function fyEndMonthName(fyEndMonth: number): string {
  return MONTH_NAMES_FULL[(fyEndMonth - 1 + 12) % 12]
}
