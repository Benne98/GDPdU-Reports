import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  api,
  SalesFilters,
  type SalesChurnBridgeResponse,
  type SalesChurnDrillRow,
} from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { periodAnchorYearMonth } from '../../../lib/periodSelection'
import SalesAnalyticsChartShell from './SalesAnalyticsChartShell'
import SalesChurnBridge from '../SalesChurnBridge'
import SalesChurnDimensionTable from './SalesChurnDimensionTable'
import SalesChurnEditor, { churnGrainLabel } from './SalesChurnEditor'
import {
  buildChurnBridgePeriods,
  churnDimLabel,
  type ChurnGrain,
} from './churnBridgeLayout'

const EMPTY_CHURN: SalesChurnBridgeResponse = {
  periods: [],
  period_totals: [],
  bridge: { new: 0, upsell: 0, cross_sell: 0, downsell: 0, lost: 0 },
  table_rows: [],
}

const CHURN_DIM_STORAGE = 'finssentials.sales.churn.dim.v1'
const CHURN_GRAIN_STORAGE = 'finssentials.sales.churn.grain.v1'

function loadChurnDim(): string {
  try {
    const v = localStorage.getItem(CHURN_DIM_STORAGE)
    if (v) return v
  } catch { /* ignore */ }
  return 'entity'
}

function loadChurnGrain(): ChurnGrain {
  try {
    const v = localStorage.getItem(CHURN_GRAIN_STORAGE)
    if (v === 'year' || v === 'quarter' || v === 'month') return v
  } catch { /* ignore */ }
  return 'month'
}

type Props = {
  period: PeriodSelection
  filters: SalesFilters
}

export default function SalesChurnSection({ period, filters }: Props) {
  const { year, month } = periodAnchorYearMonth(period)
  const [grain, setGrain] = useState<ChurnGrain>(loadChurnGrain)
  const [dim, setDim] = useState(loadChurnDim)
  const [data, setData] = useState<SalesChurnBridgeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [drillOpen, setDrillOpen] = useState(false)
  const [drillRows, setDrillRows] = useState<SalesChurnDrillRow[]>([])
  const [drillTitle, setDrillTitle] = useState('')

  const fk = JSON.stringify(filters)

  useEffect(() => {
    try { localStorage.setItem(CHURN_DIM_STORAGE, dim) } catch { /* ignore */ }
  }, [dim])

  useEffect(() => {
    try { localStorage.setItem(CHURN_GRAIN_STORAGE, grain) } catch { /* ignore */ }
  }, [grain])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    void api
      .salesChurnBridge(year, month, grain, dim, 'invoiced', filters)
      .then(d => { if (!cancelled) setData(d) })
      .catch(() => { if (!cancelled) setData(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [year, month, grain, dim, fk])

  const payload = data ?? EMPTY_CHURN
  const first = payload.periods[0] ?? ''
  const last = payload.periods[payload.periods.length - 1] ?? ''
  const dimLabel = payload.dim_label ?? churnDimLabel(dim)

  const subtitle = useMemo(() => {
    const range = first && last ? `${first} → ${last}` : churnGrainLabel(grain)
    return `${churnGrainLabel(grain)} · ${dimLabel} · ${range} · kEUR`
  }, [grain, dimLabel, first, last])

  const handleDrill = useCallback(async (component: string, _bridgeIdx: number) => {
    if (!data) return
    // Single PM→CM transition; use the last two date ranges from buildChurnBridgePeriods.
    const builtPeriods = buildChurnBridgePeriods(year, month, grain)
    const pFrom = builtPeriods[builtPeriods.length - 2]
    const pTo = builtPeriods[builtPeriods.length - 1]
    setDrillTitle(`${component}: ${data.periods[0] ?? ''} → ${data.periods[1] ?? ''}`)
    setDrillOpen(true)
    try {
      const rows = await api.salesChurnDrilldown(
        pFrom.ja, pFrom.je, pTo.ja, pTo.je, component, 'invoiced', filters,
      )
      setDrillRows(rows)
    } catch {
      setDrillRows([])
    }
  }, [data, year, month, grain, filters])

  return (
    <SalesAnalyticsChartShell
      title="Churn Bridge"
      subtitle={subtitle}
      actions={(
        <SalesChurnEditor
          grain={grain}
          dim={dim}
          onGrainChange={setGrain}
          onDimChange={setDim}
          disabled={loading}
        />
      )}
      bodyClassName="flex flex-col gap-0 px-0 pb-0"
    >
      <SalesChurnBridge
        data={payload}
        loading={loading}
        onDrillRequest={handleDrill}
        drillRows={drillRows}
        drillTitle={drillTitle}
        drillOpen={drillOpen}
        onDrillClose={() => setDrillOpen(false)}
      />
      <div className="border-t mx-3" style={{ borderColor: '#F1F5F9' }} />
      <SalesChurnDimensionTable
        data={payload}
        dimLabel={dimLabel}
        loading={loading}
      />
    </SalesAnalyticsChartShell>
  )
}
