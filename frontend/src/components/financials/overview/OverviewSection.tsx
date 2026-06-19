import { useState } from 'react'
import type { FinancialsOverviewResponse } from '../../../lib/api'
import type { FinTab } from '../financialsTabs'
import { FIN_REPORT_SPLIT_GRID } from '../statement-two-view/finReportLayout'
import PlSectionHeading from '../pl-two-view/PlSectionHeading'
import ExecutiveSummaryTable from './ExecutiveSummaryTable'
import OverviewHighlightsPanel from './OverviewHighlightsPanel'
import OverviewGroupSummary from './OverviewGroupSummary'
import OverviewEntityBreakdown from './OverviewEntityBreakdown'
import type { FinancialsEntityBreakdownResponse } from '../../../lib/api'

type Props = {
  data: FinancialsOverviewResponse | null
  loading: boolean
  highlightsLoading?: boolean
  entityBreakdown?: FinancialsEntityBreakdownResponse | null
  entityBreakdownLoading?: boolean
  entityBreakdownNarrativesLoading?: boolean
  entityBreakdownError?: string | null
  showEntityBreakdown?: boolean
  error: string | null
  onNavigateTab: (tab: FinTab) => void
}

export default function OverviewSection({
  data,
  loading,
  highlightsLoading,
  entityBreakdown,
  entityBreakdownLoading,
  entityBreakdownNarrativesLoading,
  entityBreakdownError,
  showEntityBreakdown,
  error,
  onNavigateTab,
}: Props) {
  const [highlightIdx, setHighlightIdx] = useState(0)

  if (loading && !data) {
    return (
      <div
        className="rounded-xl px-6 py-16 text-center text-sm animate-pulse"
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', color: '#94A3B8' }}
      >
        Loading executive summary…
      </div>
    )
  }

  if (error) {
    return (
      <div
        className="rounded-xl px-5 py-4 text-sm"
        style={{ background: '#FEF2F2', border: '1px solid #FECACA', color: '#B91C1C' }}
        role="alert"
      >
        {error}
      </div>
    )
  }

  if (!data) return null

  const cardShell = {
    background: '#FFFFFF',
    border: '1px solid #E2E8F0',
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
  } as const

  return (
    <>
      <div className="rounded-xl overflow-hidden" style={cardShell}>
        <div className="px-4 pt-6 pb-4" style={{ borderBottom: '1px solid #F1F5F9' }}>
          <OverviewGroupSummary intro={data.intro} />
        </div>

        <div className="px-4 pt-4 pb-6">
          <div className={FIN_REPORT_SPLIT_GRID}>
            <div className="min-w-0">
              <PlSectionHeading>Executive summary</PlSectionHeading>
              <ExecutiveSummaryTable sections={data.sections} colLabels={data.col_labels} />
            </div>
            <div className="min-w-0 lg:sticky lg:top-24 lg:self-start">
              <OverviewHighlightsPanel
                highlights={data.highlights}
                loading={highlightsLoading}
                activeIndex={highlightIdx}
                onActiveIndexChange={setHighlightIdx}
                onNavigateTab={onNavigateTab}
              />
            </div>
          </div>
        </div>
      </div>

      {showEntityBreakdown ? (
        <div className="rounded-xl overflow-hidden mt-5 px-4 pt-6 pb-6" style={cardShell}>
          <OverviewEntityBreakdown
            data={entityBreakdown ?? null}
            loading={entityBreakdownLoading}
            narrativesLoading={entityBreakdownNarrativesLoading}
            error={entityBreakdownError}
            onNavigateTab={onNavigateTab}
          />
        </div>
      ) : null}
    </>
  )
}
