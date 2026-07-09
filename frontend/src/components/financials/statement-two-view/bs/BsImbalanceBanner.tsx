import type { BalanceCheckResult } from '../../../../lib/api'

/**
 * Imbalance is in FULL EUR (same unit as balance_check). A warning must show the
 * real amount at whole-EUR precision — NOT kEUR — so a small (< 1000 EUR) gap is
 * not misleadingly rounded to "0". de-DE thousands separators, accounting
 * parentheses for a negative (assets < equity & liabilities).
 */
function fmtImbalanceEur(v: number): string {
  const abs = Math.abs(v).toLocaleString('de-DE', { maximumFractionDigits: 0 })
  return v < 0 ? `(€ ${abs})` : `€ ${abs}`
}

type Props = {
  /** Absent or is_balanced=true → renders nothing. */
  balanceCheck?: BalanceCheckResult
  /**
   * Map from column key (e.g. 'cm', 'ytd', 'fy') to human-readable label
   * (e.g. 'Jul25', 'YTD Jul25'). Sourced from the statement's col_labels.
   * Used only for display — missing keys fall back to the raw key string.
   */
  colLabels?: Record<string, string | undefined>
}

/**
 * Non-blocking warning strip shown directly above the BS mini-table when the
 * accounting identity (Total assets = Total equity & liabilities) does not hold.
 *
 * - Renders nothing when `balanceCheck` is absent/undefined (older API) or
 *   when `is_balanced` is true (normal case).
 * - Filters to columns where |imbalance| > 0.01 EUR before rendering.
 * - Amounts use whole-EUR precision (fmtImbalanceEur) so a sub-kEUR gap is still
 *   shown truthfully rather than rounded to zero.
 */
export default function BsImbalanceBanner({ balanceCheck, colLabels }: Props) {
  if (!balanceCheck || balanceCheck.is_balanced) return null

  const outOfBalance = Object.entries(balanceCheck.imbalance).filter(
    ([, v]) => Math.abs(v) > 0.01,
  )

  // Guard: is_balanced=false but every filtered column rounds away — shouldn't
  // happen in practice but avoids rendering an uninformative empty banner.
  if (outOfBalance.length === 0) return null

  return (
    <div
      role="alert"
      className="mb-2 flex items-start gap-1.5 rounded border px-3 py-2 text-xs"
      style={{
        borderColor: '#D97706',
        background: '#FFFBEB',
        color: '#78350F',
      }}
    >
      <span className="mt-px shrink-0 select-none">&#9888;</span>
      <span>
        <span className="font-semibold">Balance sheet does not balance</span>
        {' — Assets − Equity & Liabilities: '}
        {outOfBalance.map(([key, value], i) => {
          const label = colLabels?.[key] ?? key
          return (
            <span key={key}>
              {i > 0 ? '; ' : ''}
              <span className="font-medium">{label}</span>
              {' = '}
              <span className="tabular-nums">{fmtImbalanceEur(value)}</span>
            </span>
          )
        })}
      </span>
    </div>
  )
}
