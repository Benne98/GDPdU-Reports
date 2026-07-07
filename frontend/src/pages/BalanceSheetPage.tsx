import { useSearchParams } from 'react-router-dom'
import GlFixedAssetsTab from '../components/financials/fixed-assets/GlFixedAssetsTab'
import SalesAgingTab from '../components/sales/aging/SalesAgingTab'
import { periodAnchorYearMonth } from '../lib/periodSelection'
import { useReportingAvailability } from '../hooks/useReportingAvailability'
import StatementsPage, { type StatementSubTab } from './StatementsPage'

/** Balance sheet — sub-pages: Balance sheet (live) · AR / AP · Fixed assets.
 *  Active sub-page is URL-driven (?sub=…) so the header nav can deep-link into a sub-page.
 *  Aging + Fixed assets render only when their data is loaded (Phase 7 conditional display). */
export default function BalanceSheetPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const availability = useReportingAvailability()

  const subTabs: StatementSubTab[] = [
    { id: 'balance-sheet', label: 'Balance sheet' },
    ...(availability.opos
      ? [
          { id: 'receivables-aging', label: 'Receivables Aging', description: 'Open receivables as of {period}. Change the date in the filters panel.' },
          { id: 'payables-aging', label: 'Payables Aging', description: 'Open payables as of {period}. Change the date in the filters panel.' },
        ]
      : []),
    ...(availability.fixed_assets ? [{ id: 'fixed-assets', label: 'Fixed assets' }] : []),
  ]

  const requestedSub = searchParams.get('sub') || 'balance-sheet'
  const subTab = subTabs.some(t => t.id === requestedSub) ? requestedSub : 'balance-sheet'

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
      subTabs={subTabs}
      activeSubTab={subTab}
      onSubTabChange={setSubTab}
      entityFilterSubTabs={['receivables-aging', 'payables-aging', 'fixed-assets']}
      renderSubTabContent={ctx => {
        if (subTab === 'receivables-aging' || subTab === 'payables-aging') {
          const anchor = periodAnchorYearMonth(ctx.period)
          // Aging sections take a single `legalEntity` string; a comma-joined list of
          // selected codes is passed straight through to the (multi-entity) backend.
          const legalEntity = ctx.entities.length ? ctx.entities.join(',') : 'all'
          return (
            <SalesAgingTab
              view={subTab === 'receivables-aging' ? 'receivables' : 'payables'}
              period={ctx.period}
              year={anchor.year}
              month={anchor.month}
              legalEntity={legalEntity}
            />
          )
        }
        if (subTab === 'fixed-assets') {
          return <GlFixedAssetsTab period={ctx.period} entities={ctx.entities} />
        }
        return null
      }}
    />
  )
}
