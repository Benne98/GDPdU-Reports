import type { PeriodGrain, PeriodSelection } from '../../../lib/periodSelection'
import { useChartLoadReporter } from '../../../hooks/useChartLoadReporter'
import {
  buildKpiStripItems,
  buildLeadBriefing,
} from './overviewBriefingUtils'
import OverviewLeadCard from './OverviewLeadCard'
import OverviewKpiStrip from './OverviewKpiStrip'
import OverviewInsightsSection from './OverviewInsightsSection'
import type { OverviewBriefingState } from './useOverviewBriefing'

type Props = {
  briefing: OverviewBriefingState
  period: PeriodSelection
  grain: PeriodGrain
  entity?: string
}

function BriefingSkeleton() {
  return (
    <div className="space-y-4">
      <div
        className="rounded-xl px-6 py-16 text-center text-sm animate-pulse"
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', color: '#94A3B8' }}
      >
        Loading briefing…
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="rounded-xl h-[148px] animate-pulse"
            style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
          />
        ))}
      </div>
    </div>
  )
}

export default function OverviewBriefingBlock({ briefing, period, grain, entity }: Props) {
  const { data, loading, error, ccc } = briefing
  useChartLoadReporter('overview-briefing', loading, error)

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

  if (!data) {
    if (loading || !error) return <BriefingSkeleton />
    return null
  }

  const lead = buildLeadBriefing(data)
  const kpis = buildKpiStripItems(data, ccc, grain)

  return (
    <div className="space-y-5">
      <OverviewLeadCard briefing={lead} />
      <OverviewKpiStrip items={kpis} />
      <OverviewInsightsSection data={data} period={period} entity={entity} />
    </div>
  )
}
