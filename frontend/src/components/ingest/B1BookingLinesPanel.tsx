/**
 * B1BookingLinesPanel — shared booking-lines drill-down panel for the B1 check.
 *
 * Renders the expanded table of all lines in one out-of-balance booking,
 * plus the emerald-highlighted "Amount sum (should be 0)" tfoot row.
 *
 * Used by:
 *   - ValidationReport.tsx  (single-entity ValidierungStep view)
 *   - MergedValidationReport.tsx  (multi-entity merged wizard view)
 *
 * The caller is responsible for:
 *   - Fetching the data via fetchIssueRows({ check_id: 'B1', journal_entry_group_number, ... })
 *   - Controlling show/hide (this component always renders when mounted)
 *   - Passing loading state via the `loading` prop for the loading indicator
 */

import type { CheckOffender, IssueRowsResponse } from '../../lib/gdpduApi'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const COLUMN_LABELS: Record<string, string> = {
  journal_entry_number: 'Booking no.',
  journal_entry_group_number: 'Booking ID',
  fiscal_year: 'Fiscal year',
  fiscal_period: 'Month',
  line_count: 'Lines in booking',
  sum: 'Imbalance',
  booking_line_id: 'Row no.',
  gl_account_id: 'Account',
  account_number_group: 'Account key',
  amount: 'Amount',
  posting_date: 'Posting date',
  account: 'Account',
  entity: 'Entity',
}

function fmt(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(n)
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface B1BookingLinesPanelProps {
  /** The offender — used for the header text (booking id + FY). */
  offender: CheckOffender
  data: IssueRowsResponse | null
  error: string | null
  loading: boolean
  onRetry: () => void
}

export default function B1BookingLinesPanel({
  offender,
  data,
  error,
  loading,
  onRetry,
}: B1BookingLinesPanelProps) {
  return (
    <div className="border-t border-slate-200 bg-slate-50 px-3 py-3 space-y-2">
      {loading && !data && !error && (
        <p className="text-xs text-slate-500">Loading booking lines…</p>
      )}
      {error && (
        <p className="text-xs text-red-700">
          {error}{' '}
          <button type="button" onClick={onRetry} className="underline">
            Retry
          </button>
        </p>
      )}
      {data && (
        <>
          <p className="text-xs text-slate-500">
            All lines of booking{' '}
            <span className="font-mono font-medium">
              {offender.journal_entry_group_number}
            </span>
            {typeof offender.fiscal_year === 'number'
              ? ` · FY ${offender.fiscal_year}`
              : ''}
            . Imbalance sum should be 0.
          </p>
          <div className="overflow-x-auto rounded border border-slate-200 bg-white">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200 text-left text-slate-500">
                  {data.columns.map((c) => (
                    <th key={c} className="py-1.5 px-2 font-semibold whitespace-nowrap">
                      {COLUMN_LABELS[c] ?? c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row, i) => (
                  <tr key={i} className="border-b border-slate-100 last:border-0">
                    {data.columns.map((c) => (
                      <td
                        key={c}
                        className="py-1.5 px-2 font-mono text-slate-700 whitespace-nowrap"
                      >
                        {row[c] == null
                          ? '—'
                          : typeof row[c] === 'number'
                          ? fmt(row[c] as number)
                          : String(row[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
              {/* SUM row — highlights the imbalance (should be 0 for a balanced booking) */}
              {data.summary_row != null && (
                <tfoot>
                  <tr className="bg-emerald-50 border-t-2 border-emerald-200">
                    <td
                      colSpan={data.columns.length}
                      className="py-1.5 px-2 text-xs font-semibold text-emerald-800"
                    >
                      Amount sum (should be 0)
                      <span className="ml-3 font-mono">
                        {fmt(data.summary_row.amount)}
                      </span>
                    </td>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </>
      )}
    </div>
  )
}
