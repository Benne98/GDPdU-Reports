import { useSearchParams } from 'react-router-dom'
import GlProfitabilityTab from '../components/financials/profitability/GlProfitabilityTab'
import GlPayrollTab from '../components/financials/payroll/GlPayrollTab'
import { useReportingAvailability } from '../hooks/useReportingAvailability'
import StatementsPage, { type StatementSubTab } from './StatementsPage'

/** Income statement — sub-pages: P&L statement (live) · Profitability · Payroll.
 *  Active sub-page is URL-driven (?sub=…) so the header nav can deep-link into a sub-page.
 *  Payroll renders only when personnel data is loaded (Phase 7 conditional display). */
export default function IncomeStatementPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const availability = useReportingAvailability()

  const subTabs: StatementSubTab[] = [
    { id: 'pl-statement', label: 'P&L statement' },
    { id: 'profitability', label: 'Profitability' },
    // GL + Profitability are always shown; Payroll only when its data is loaded.
    ...(availability.payroll ? [{ id: 'payroll', label: 'Payroll' }] : []),
  ]

  const requestedSub = searchParams.get('sub') || 'pl-statement'
  // Guard a deep link to a sub-page that is not available → fall back to the default.
  const subTab = subTabs.some(t => t.id === requestedSub) ? requestedSub : 'pl-statement'

  const setSubTab = (id: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('sub', id)
    setSearchParams(next, { replace: true })
  }
  return (
    <StatementsPage
      statement="pl"
      kicker="Income statement"
      title="P&L statement"
      description="Income statement with report and table view for {period}."
      subTabs={subTabs}
      activeSubTab={subTab}
      onSubTabChange={setSubTab}
      entityFilterSubTabs={['profitability', 'payroll']}
      renderSubTabContent={ctx => {
        if (subTab === 'profitability') {
          return <GlProfitabilityTab period={ctx.period} entities={ctx.entities} />
        }
        if (subTab === 'payroll') {
          return <GlPayrollTab period={ctx.period} entities={ctx.entities} />
        }
        return null
      }}
    />
  )
}
