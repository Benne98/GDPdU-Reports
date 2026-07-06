import type { PeriodSelection } from '../../../lib/periodSelection'
import type { AgingView } from './agingView'
import SalesAgingPayablesSection from './SalesAgingPayablesSection'
import SalesAgingReceivablesSection from './SalesAgingReceivablesSection'

/**
 * SalesAgingTab — renders a single aging view (receivables OR payables), chosen by the
 * `view` prop. The Receivables/Payables selection now lives in the top-bar sub-page menu,
 * and the as-of date is the page's filter-panel period (year/month), so the former in-page
 * SubNav + Stichtag switch were removed.
 */
export default function SalesAgingTab({
  view,
  period,
  year,
  month,
  legalEntity,
}: {
  view: AgingView
  period: PeriodSelection
  year: number
  month: number
  legalEntity: string
}) {
  return (
    <div className="space-y-4">
      {view === 'receivables' ? (
        <SalesAgingReceivablesSection
          period={period}
          year={year}
          month={month}
          legalEntity={legalEntity}
        />
      ) : (
        <SalesAgingPayablesSection
          period={period}
          year={year}
          month={month}
          legalEntity={legalEntity}
        />
      )}
    </div>
  )
}
