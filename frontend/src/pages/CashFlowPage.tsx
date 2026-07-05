import { useState } from 'react'
import CashDebtErrorBoundary from '../components/financials/cash-debt/CashDebtErrorBoundary'
import StatementsPage from './StatementsPage'

/** Cash flow — indirect CF statement + Cash & debt sub-tab. */
export default function CashFlowPage() {
  const [subTab, setSubTab] = useState('cash-flow')

  return (
    <StatementsPage
      statement="cf"
      kicker="Cash flow"
      title="Cash flow"
      description="Indirect cash flow statement with report and table view for {period}. Click a value to drill into GL lines."
      subTabs={[
        { id: 'cash-flow', label: 'Cash flow statement' },
        { id: 'cash-debt', label: 'Cash & debt' },
      ]}
      activeSubTab={subTab}
      onSubTabChange={setSubTab}
      renderSubTabContent={ctx => {
        if (subTab === 'cash-debt') {
          return <CashDebtErrorBoundary period={ctx.period} entity={ctx.entity} />
        }
        return null
      }}
    />
  )
}
