import { useSearchParams } from 'react-router-dom'
import GlProfitabilityTab from '../components/financials/profitability/GlProfitabilityTab'
import GlPayrollTab from '../components/financials/payroll/GlPayrollTab'
import StatementsPage from './StatementsPage'

/** Income statement — sub-pages: P&L statement (live) · Profitability · Payroll.
 *  Active sub-page is URL-driven (?sub=…) so the header nav can deep-link into a sub-page. */
export default function IncomeStatementPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const subTab = searchParams.get('sub') || 'pl-statement'
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
      subTabs={[
        { id: 'pl-statement', label: 'P&L statement' },
        { id: 'profitability', label: 'Profitability' },
        { id: 'payroll', label: 'Payroll' },
      ]}
      activeSubTab={subTab}
      onSubTabChange={setSubTab}
      renderSubTabContent={ctx => {
        if (subTab === 'profitability') {
          return <GlProfitabilityTab period={ctx.period} entity={ctx.entity} />
        }
        if (subTab === 'payroll') {
          return <GlPayrollTab period={ctx.period} entity={ctx.entity} />
        }
        return null
      }}
    />
  )
}
