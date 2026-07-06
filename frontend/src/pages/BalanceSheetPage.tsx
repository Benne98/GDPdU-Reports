import { useSearchParams } from 'react-router-dom'
import GlFixedAssetsTab from '../components/financials/fixed-assets/GlFixedAssetsTab'
import SalesAgingTab from '../components/sales/aging/SalesAgingTab'
import { periodAnchorYearMonth } from '../lib/periodSelection'
import StatementsPage from './StatementsPage'

/** Balance sheet — sub-pages: Balance sheet (live) · AR / AP · Fixed assets.
 *  Active sub-page is URL-driven (?sub=…) so the header nav can deep-link into a sub-page. */
export default function BalanceSheetPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const subTab = searchParams.get('sub') || 'balance-sheet'
  const setSubTab = (id: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('sub', id)
    setSearchParams(next, { replace: true })
  }
  return (
    <StatementsPage
      statement="bs"
      kicker="Balance sheet"
      title="Balance sheet"
      description="Consolidated balance sheet with report and table view for {period}."
      subTabs={[
        { id: 'balance-sheet', label: 'Balance sheet' },
        { id: 'receivables-aging', label: 'Receivables Aging', description: 'Open receivables as of {period}. Change the date in the filters panel.' },
        { id: 'payables-aging', label: 'Payables Aging', description: 'Open payables as of {period}. Change the date in the filters panel.' },
        { id: 'fixed-assets', label: 'Fixed assets' },
      ]}
      activeSubTab={subTab}
      onSubTabChange={setSubTab}
      renderSubTabContent={ctx => {
        if (subTab === 'receivables-aging' || subTab === 'payables-aging') {
          const anchor = periodAnchorYearMonth(ctx.period)
          return (
            <SalesAgingTab
              view={subTab === 'receivables-aging' ? 'receivables' : 'payables'}
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
