import { useCallback, useEffect, useMemo, useState } from 'react'
import { LineChart } from 'lucide-react'
import { api, type PayablesConcentration, type PayablesConcentrationTrendPoint } from '../../../../lib/api'
import type { PeriodSelection } from '../../../../lib/periodSelection'
import { periodQueryParams } from '../../../../lib/periodSelection'
import PlExportMenu, { type PlExportKind } from '../../../financials/pl-two-view/PlExportMenu'
import AgingChartFrame from '../shared/AgingChartFrame'
import OperationalCard from '../../operational/OperationalCard'
import PayablesConcentrationCard from './PayablesConcentrationCard'
import PayablesConcentrationTrend from './PayablesConcentrationTrend'
import ReceivablesConcentrationTrendEditor from '../ReceivablesConcentrationTrendEditor'
import {
  buildConcentrationTrendSubtitle,
  loadAgingConcentrationTrendConfig,
  saveAgingConcentrationTrendConfig,
  type AgingConcentrationTrendConfig,
} from '../shared/agingConcentrationTrendConfig'
import { exportPayablesConcentrationTrend } from './shared/payablesConcentrationTrendExport'
import { normalizePayablesConcentration } from '../shared/normalizeConcentration'

type Props = {
  period: PeriodSelection
  year: number
  month: number
  entity?: string
  onTrendVisibleChange?: (visible: boolean) => void
}

export default function PayablesConcentrationSection({
  period,
  year,
  month,
  entity,
  onTrendVisibleChange,
}: Props) {
  const [concentration, setConcentration] = useState<PayablesConcentration | null>(null)
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [showTrend, setShowTrend] = useState(false)
  const [trendConfig, setTrendConfig] = useState<AgingConcentrationTrendConfig>(() => loadAgingConcentrationTrendConfig())
  const [trendPoints, setTrendPoints] = useState<PayablesConcentrationTrendPoint[]>([])
  const [trendLoading, setTrendLoading] = useState(false)
  const [trendLoadedKey, setTrendLoadedKey] = useState('')

  const periodOpts = periodQueryParams(period)
  const periodGrain = period.grain

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setFetchError(null)
    setShowTrend(false)
    setTrendPoints([])
    setTrendLoadedKey('')

    api
      .salesPayablesConcentration(year, month, entity, {
        period_grain: periodGrain,
        iso_year: periodOpts.iso_year as number | undefined,
        iso_week: periodOpts.iso_week as number | undefined,
      })
      .then(raw => {
        if (cancelled) return
        setConcentration(normalizePayablesConcentration(raw))
      })
      .catch(err => {
        if (cancelled) return
        setConcentration(null)
        setFetchError(err instanceof Error ? err.message : 'Could not load concentration')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [year, month, entity, periodGrain, periodOpts.iso_year, periodOpts.iso_week])

  useEffect(() => {
    saveAgingConcentrationTrendConfig(trendConfig)
  }, [trendConfig])

  const trendFetchKey = `${year}-${month}-${entity ?? ''}-${periodGrain}-${periodOpts.iso_year ?? ''}-${periodOpts.iso_week ?? ''}-${trendConfig.agingBucket}`

  const loadTrend = useCallback(async () => {
    setTrendLoading(true)
    try {
      const r = await api.salesPayablesConcentrationTrend(year, month, entity, 12, {
        period_grain: periodGrain,
        iso_year: periodOpts.iso_year as number | undefined,
        iso_week: periodOpts.iso_week as number | undefined,
        aging_bucket: trendConfig.agingBucket === 'all' ? undefined : trendConfig.agingBucket,
      })
      setTrendPoints(r.points)
      setTrendLoadedKey(trendFetchKey)
    } catch {
      setTrendPoints([])
    } finally {
      setTrendLoading(false)
    }
  }, [year, month, entity, periodGrain, periodOpts.iso_year, periodOpts.iso_week, trendConfig.agingBucket, trendFetchKey])

  useEffect(() => {
    if (showTrend && trendLoadedKey !== trendFetchKey && !trendLoading) {
      void loadTrend()
    }
  }, [showTrend, trendLoadedKey, trendFetchKey, trendLoading, loadTrend])

  useEffect(() => {
    onTrendVisibleChange?.(showTrend)
  }, [showTrend, onTrendVisibleChange])

  const trendSubtitle = useMemo(
    () => buildConcentrationTrendSubtitle(trendConfig, periodGrain),
    [trendConfig, periodGrain],
  )

  const hasTrendData = trendPoints.length > 0

  async function handleTrendExport(kind: PlExportKind) {
    if (!hasTrendData) return
    await exportPayablesConcentrationTrend({
      kind,
      points: trendPoints,
      config: trendConfig,
      periodGrain,
    })
  }

  const hasData =
    !!concentration
    && concentration.total > 0
    && concentration.segments.some(s => s.amount > 0)

  return (
    <OperationalCard
      title="Supplier concentration"
      subtitle="Open payables by non-overlapping supplier rank"
      className="h-full flex flex-col"
      headerRight={
        <button
          type="button"
          disabled={loading || !hasData}
          onClick={() => setShowTrend(v => !v)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-50"
          style={{
            background: showTrend ? 'rgba(30, 58, 95, 0.1)' : '#F4F6F9',
            color: showTrend ? '#1E3A5F' : '#475569',
            border: `1px solid ${showTrend ? 'rgba(30, 58, 95, 0.2)' : '#E2E8F0'}`,
          }}
        >
          <LineChart size={13} />
          {showTrend ? 'Hide trend' : 'Show trend'}
        </button>
      }
    >
      {fetchError && !loading && (
        <div className="mb-3 px-3 py-2 rounded-lg text-xs" style={{ background: 'rgba(220,38,38,0.08)', color: '#B91C1C' }}>
          {fetchError}
        </div>
      )}
      <AgingChartFrame
        hasData={hasData}
        loading={loading}
        height={showTrend ? undefined : 200}
        emptyMessage="No concentration data"
      >
        {concentration && <PayablesConcentrationCard data={concentration} />}
      </AgingChartFrame>

      {showTrend && (
        <div className="mt-4 pt-4" style={{ borderTop: '1px solid #F1F5F9' }}>
          <div className="flex items-start justify-between gap-3 mb-3">
            <div>
              <p className="text-xs font-semibold" style={{ color: '#1E3A5F' }}>
                Concentration trend
              </p>
              <p className="text-[10px] mt-0.5" style={{ color: '#94A3B8' }}>
                {trendSubtitle}
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <ReceivablesConcentrationTrendEditor
                config={trendConfig}
                onChange={setTrendConfig}
                disabled={trendLoading}
              />
              <PlExportMenu
                formats={['pptx', 'xlsx']}
                onExport={handleTrendExport}
                disabled={trendLoading || !hasTrendData}
              />
            </div>
          </div>
          {trendLoading ? (
            <div className="h-[220px] flex items-center justify-center text-xs" style={{ color: '#94A3B8' }}>
              Loading trend…
            </div>
          ) : (
            <PayablesConcentrationTrend
              points={trendPoints}
              periodGrain={periodGrain}
              agingBucket={trendConfig.agingBucket}
            />
          )}
        </div>
      )}
    </OperationalCard>
  )
}
