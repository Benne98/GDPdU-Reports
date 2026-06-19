import { useState } from 'react'
import SalesAgingTab from '../components/sales/aging/SalesAgingTab'
import { periodAnchorYearMonth } from '../lib/periodSelection'
import StatementsPage from './StatementsPage'

/** Balance sheet — sub-pages: Balance sheet (live) · AR / AP aging (GL subledger). */
export default function BalanceSheetPage() {
  const [subTab, setSubTab] = useState('balance-sheet')
  return (
    <StatementsPage
      statement="bs"
      kicker="Balance sheet"
      title="Balance sheet"
      description="Consolidated balance sheet with report and table view for {period}. Click a value to drill into GL lines."
      subTabs={[
        { id: 'balance-sheet', label: 'Balance sheet' },
        { id: 'ar-ap', label: 'AR / AP' },
      ]}
      activeSubTab={subTab}
      onSubTabChange={setSubTab}
      renderSubTabContent={ctx => {
        if (subTab !== 'ar-ap') return null
        const anchor = periodAnchorYearMonth(ctx.period)
        return (
          <SalesAgingTab
            period={ctx.period}
            year={anchor.year}
            month={anchor.month}
            legalEntity={ctx.entity}
          />
        )
      }}
    />
  )
}
