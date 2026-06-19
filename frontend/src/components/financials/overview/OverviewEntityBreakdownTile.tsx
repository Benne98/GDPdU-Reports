import { useEffect, useState } from 'react'
import {
  api,
  type EntityBreakdownArea,
  type FinancialsEntityBreakdownResponse,
  type FinPeriodParams,
} from '../../../lib/api'
import type { FinTab } from '../financialsTabs'
import { useChartLoadReporter } from '../../../hooks/useChartLoadReporter'
import { useOptionalActionNotesContext } from '../../action-notes/ActionNotesContext'
import { captureEntityBreakdownSnapshot } from '../../action-notes/captureOverviewTables'
import { FIN_REPORT_SPLIT_GRID } from '../statement-two-view/finReportLayout'
import EntityBreakdownTable from './EntityBreakdownTable'
import EntityBreakdownKeyDrivers from './EntityBreakdownKeyDrivers'

type Props = {
  periodParams: FinPeriodParams
  resetKey: string
  onNavigateTab: (tab: FinTab) => void
}

const CARD = {
  background: '#FFFFFF',
  border: '1px solid #E2E8F0',
  boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
} as const

const PLACEHOLDER_AREA: EntityBreakdownArea = {
  id: 'earnings',
  title: 'Earnings',
  tab: 'pl',
  intro: '',
  bullets: [],
}

export default function OverviewEntityBreakdownTile({ periodParams, resetKey, onNavigateTab }: Props) {
  const [data, setData] = useState<FinancialsEntityBreakdownResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [narrativesLoading, setNarrativesLoading] = useState(false)
  const [areaIdx, setAreaIdx] = useState(0)
  const notesCtx = useOptionalActionNotesContext()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setAreaIdx(0)
    api
      .financialsOverviewEntityBreakdown(periodParams)
      .then(res => {
        if (!cancelled) setData(res)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setData(null)
        setError(e instanceof Error ? e.message : 'Failed to load entity breakdown')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])

  // Lazy-load narratives (key drivers) after the table data arrives.
  useEffect(() => {
    if (!data) return
    if (data.areas?.some(a => a.bullets.length)) return
    let cancelled = false
    setNarrativesLoading(true)
    api
      .financialsOverviewEntityBreakdownNarratives(periodParams)
      .then(res => {
        if (cancelled) return
        setData(prev => (prev ? { ...prev, areas: res.areas } : prev))
      })
      .catch(() => {
        /* narratives are best-effort */
      })
      .finally(() => {
        if (!cancelled) setNarrativesLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey, data])

  useEffect(() => {
    if (!notesCtx) return
    if (!data?.rows?.length) {
      notesCtx.unregisterTableCandidate('overview-entity-breakdown')
      return
    }
    notesCtx.registerTableCandidate({
      id: 'overview-entity-breakdown',
      label: 'Entity breakdown',
      description: 'Current-period figures by legal entity',
      capture: () => captureEntityBreakdownSnapshot(data),
      viewState: { tab: 'overview' },
    })
    return () => notesCtx.unregisterTableCandidate('overview-entity-breakdown')
  }, [notesCtx, data])

  const timedOut = useChartLoadReporter('overview-entity-breakdown', loading, error)

  if (error) {
    return (
      <div className="rounded-xl px-5 py-4 text-sm" style={{ background: '#FEF2F2', border: '1px solid #FECACA', color: '#B91C1C' }} role="alert">
        {error}
      </div>
    )
  }

  if (loading && !data) {
    return (
      <div className="rounded-xl px-6 py-16 text-center text-sm animate-pulse" style={{ ...CARD, color: '#94A3B8' }}>
        Loading entity breakdown…
      </div>
    )
  }

  if (timedOut) return null

  if (!data?.rows?.length || !data?.entities?.length) {
    return (
      <div className="rounded-xl px-6 py-12 text-center text-sm" style={{ ...CARD, color: '#94A3B8' }}>
        No entity breakdown data for this period.
      </div>
    )
  }

  const areas = data.areas?.length ? data.areas : [PLACEHOLDER_AREA]
  const idx = Math.min(areaIdx, areas.length - 1)
  const area = areas[idx]
  const prev = () => setAreaIdx(i => (i <= 0 ? areas.length - 1 : i - 1))
  const next = () => setAreaIdx(i => (i >= areas.length - 1 ? 0 : i + 1))

  return (
    <div className="rounded-xl overflow-hidden mt-5 px-4 pt-6 pb-6" style={CARD}>
      <div className={`${FIN_REPORT_SPLIT_GRID}`}>
        <div className="min-w-0">
          <EntityBreakdownTable entities={data.entities} rows={data.rows} cmLabel={data.cm_label} />
        </div>
        <div className="min-w-0 lg:sticky lg:top-24 lg:self-start">
          {narrativesLoading && !area.bullets.length ? (
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
    </div>
  )
}
