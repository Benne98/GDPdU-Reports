import { useSearchParams } from 'react-router-dom'
import CashDebtErrorBoundary from '../components/financials/cash-debt/CashDebtErrorBoundary'
import StatementsPage from './StatementsPage'

/** Cash flow — indirect CF statement + Cash & debt sub-tab.
 *  Active sub-page is URL-driven (?sub=…) so the header nav can deep-link into a sub-page. */
export default function CashFlowPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const subTab = searchParams.get('sub') || 'cash-flow'
  const setSubTab = (id: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('sub', id)
    setSearchParams(next, { replace: true })
  }

  return (
    <StatementsPage
      statement="cf"
      kicker="Cash flow"
      title="Cash flow"
      description="Indirect cash flow statement with report and table view for {period}."
      subTabs={[
        { id: 'cash-flow', label: 'Cash flow statement' },
        { id: 'cash-debt', label: 'Cash & debt' },
      ]}
      activeSubTab={subTab}
      onSubTabChange={setSubTab}
      renderSubTabContent={ctx => {
        if (subTab === 'cash-debt') {
          // Cash & debt is a consolidated view (no per-entity selector on CF, Phase 7).
          return <CashDebtErrorBoundary period={ctx.period} />
        }
        return null
      }}
    />
  )
}
