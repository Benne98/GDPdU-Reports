import { useState } from 'react'
import type { EntityBreakdownArea, FinancialsEntityBreakdownResponse } from '../../../lib/api'
import type { FinTab } from '../financialsTabs'
import PlSectionHeading from '../pl-two-view/PlSectionHeading'
import { FIN_REPORT_SPLIT_GRID } from '../statement-two-view/finReportLayout'
import EntityBreakdownTable from './EntityBreakdownTable'
import EntityBreakdownKeyDrivers from './EntityBreakdownKeyDrivers'

type Props = {
  data: FinancialsEntityBreakdownResponse | null
  loading?: boolean
  narrativesLoading?: boolean
  error?: string | null
  onNavigateTab: (tab: FinTab) => void
}

const PLACEHOLDER_AREA: EntityBreakdownArea = {
  id: 'earnings',
  title: 'Earnings',
  tab: 'pl',
  intro: '',
  bullets: [],
}

export default function OverviewEntityBreakdown({
  data,
  loading,
  narrativesLoading,
  error,
  onNavigateTab,
}: Props) {
  const [areaIdx, setAreaIdx] = useState(0)

  if (loading && !data?.rows?.length) {
    return (
      <div className="text-xs animate-pulse" style={{ color: '#94A3B8' }}>
        Loading entity breakdown table…
      </div>
    )
  }

  if (error && !loading) {
    return (
      <>
        <PlSectionHeading>Entity breakdown</PlSectionHeading>
        <p className="text-xs m-0 mt-2" style={{ color: '#B91C1C' }} role="alert">
          {error}
        </p>
      </>
    )
  }

  if (!data?.rows?.length || !data?.entities?.length) {
    return (
      <>
        <PlSectionHeading>Entity breakdown</PlSectionHeading>
        <p className="text-xs m-0 mt-2" style={{ color: '#94A3B8' }}>
          No breakdown data for this period.
        </p>
      </>
    )
  }

  const areas = data.areas?.length ? data.areas : [PLACEHOLDER_AREA]
  const idx = Math.min(areaIdx, areas.length - 1)
  const area = areas[idx]
  const prev = () => setAreaIdx(i => (i <= 0 ? areas.length - 1 : i - 1))
  const next = () => setAreaIdx(i => (i >= areas.length - 1 ? 0 : i + 1))

  return (
    <>
      <PlSectionHeading>Entity breakdown</PlSectionHeading>

      <div className={`${FIN_REPORT_SPLIT_GRID} mt-3`}>
        <div className="min-w-0">
          <EntityBreakdownTable
            entities={data.entities}
            rows={data.rows}
            cmLabel={data.cm_label}
          />
        </div>
        <div className="min-w-0 lg:sticky lg:top-24 lg:self-start">
          {narrativesLoading ? (
            <div className="text-xs animate-pulse" style={{ color: '#94A3B8' }}>
              Loading key drivers from snapshots…
            </div>
          ) : (
            <EntityBreakdownKeyDrivers
              area={area}
              areaIndex={idx}
              areaCount={areas.length}
              onPrev={prev}
              onNext={next}
              onNavigateTab={onNavigateTab}
            />
          )}
        </div>
      </div>
    </>
  )
}
