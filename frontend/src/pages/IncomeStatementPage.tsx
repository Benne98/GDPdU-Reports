import { useState } from 'react'
import GlProfitabilityTab from '../components/financials/profitability/GlProfitabilityTab'
import StatementsPage from './StatementsPage'

/** Income statement — sub-pages: P&L statement (live) · Profitability (GL-derived). */
export default function IncomeStatementPage() {
  const [subTab, setSubTab] = useState('pl-statement')
  return (
    <StatementsPage
      statement="pl"
      kicker="Income statement"
      title="P&L statement"
      description="Income statement with report and table view for {period}. Click a value to drill into GL lines. Use ↑ / ↓ to jump between charts."
      subTabs={[
        { id: 'pl-statement', label: 'P&L statement' },
        { id: 'profitability', label: 'Profitability' },
      ]}
      activeSubTab={subTab}
      onSubTabChange={setSubTab}
      renderSubTabContent={ctx =>
        subTab === 'profitability' ? (
          <GlProfitabilityTab period={ctx.period} entity={ctx.entity} />
        ) : null
      }
    />
  )
}
