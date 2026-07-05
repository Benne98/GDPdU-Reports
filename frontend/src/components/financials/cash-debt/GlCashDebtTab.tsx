import { useEffect, useState } from 'react'
import {
  api,
  type CashDebtPositionBookingsResponse,
  type NetDebtRow,
  type NetDebtTableResponse,
} from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { periodAnchorYearMonth, periodCacheKey } from '../../../lib/periodSelection'
import NetDebtReport from './NetDebtReport'
import OffBalanceSheetSchema from './OffBalanceSheetSchema'
import { formatNetDebtColumnLabels } from './netDebtColumns'
import { accountGroupsForRow } from './netDebtRowUtils'

type Props = {
  period: PeriodSelection
  entity?: string
}

export default function GlCashDebtTab({ period, entity }: Props) {
  const { year, month } = periodAnchorYearMonth(period)
  const entityKey = entity && entity !== 'all' ? entity : undefined
  const periodKey = periodCacheKey(period)

  const [netDebt, setNetDebt] = useState<NetDebtTableResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedRow, setSelectedRow] = useState<NetDebtRow | null>(null)
  const [bookings, setBookings] = useState<CashDebtPositionBookingsResponse | null>(null)
  const [bookingsLoading, setBookingsLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    setError(null)
    api.financialsNetDebtTable(year, month, entityKey)
      .then(setNetDebt)
      .catch(() => {
        setNetDebt(null)
        setError('Net debt data could not be loaded.')
      })
      .finally(() => setLoading(false))
  }, [year, month, entityKey, periodKey])

  useEffect(() => {
    setSelectedRow(null)
  }, [year, month, entityKey, periodKey])

  useEffect(() => {
    const groups = accountGroupsForRow(selectedRow)
    if (!groups.length) {
      setBookings(null)
      return
    }
    setBookingsLoading(true)
    Promise.all(
      groups.map(account_number_group =>
        api.financialsCashDebtPositionBookings({
          account_number_group,
          fiscal_year: year,
          year,
          month,
          entity: entityKey,
        }),
      ),
    )
      .then(results => {
        const entries = results
          .flatMap(r => r.entries)
          .sort((a, b) => a.posting_date.localeCompare(b.posting_date))
        setBookings({
          ...results[0],
          account_name: selectedRow?.label ?? results[0].account_name,
          entries,
        })
      })
      .catch(() => setBookings(null))
      .finally(() => setBookingsLoading(false))
  }, [selectedRow, year, month, entityKey])

  function handleSelectAccount(row: NetDebtRow) {
    if (!accountGroupsForRow(row).length) return
    setSelectedRow(row)
  }

  const colLabel = netDebt?.col_label ?? `Dec${String(year).slice(-2)}A`
  const colKeys = netDebt ? formatNetDebtColumnLabels(netDebt) : colLabel

  return (
    <div className="space-y-4 mt-2">
      <section className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100">
          <h3 className="text-sm font-semibold text-slate-900">Net debt</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            kEUR · {colKeys} · cash, bank debt, shareholder loans
            {netDebt?.has_debt_like ? ' and debt-like items' : ''}
          </p>
        </div>
        <div className="p-4">
          {loading && <p className="text-sm text-slate-500">Loading net debt…</p>}
          {error && <p className="text-sm text-red-600">{error}</p>}
          {!loading && !error && netDebt && (
            <NetDebtReport
              data={netDebt}
              bookings={bookings}
              bookingsLoading={bookingsLoading}
              selectedChartRowId={selectedRow?.id ?? null}
              selectedLoanLabel={selectedRow?.label}
              onSelectAccount={handleSelectAccount}
              onCloseChart={() => setSelectedRow(null)}
            />
          )}
        </div>
      </section>

      <OffBalanceSheetSchema colLabel={colLabel} />
    </div>
  )
}
