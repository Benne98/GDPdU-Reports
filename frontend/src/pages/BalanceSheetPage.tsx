import { useState } from 'react'
import GlFixedAssetsTab from '../components/financials/fixed-assets/GlFixedAssetsTab'
import SalesAgingTab from '../components/sales/aging/SalesAgingTab'
import { periodAnchorYearMonth } from '../lib/periodSelection'
import StatementsPage from './StatementsPage'

/** Balance sheet — sub-pages: Balance sheet (live) · AR / AP · Fixed assets. */
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
        { id: 'fixed-assets', label: 'Fixed assets' },
      ]}
      activeSubTab={subTab}
      onSubTabChange={setSubTab}
      renderSubTabContent={ctx => {
        if (subTab === 'ar-ap') {
          const anchor = periodAnchorYearMonth(ctx.period)
          return (
            <SalesAgingTab
              period={ctx.period}
              year={anchor.year}
              month={anchor.month}
              legalEntity={ctx.entity}
            />
          )
        }
        if (subTab === 'fixed-assets') {
          return <GlFixedAssetsTab period={ctx.period} entity={ctx.entity} />
        }
        return null
      }}
    />
  )
}
