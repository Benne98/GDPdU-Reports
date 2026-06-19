import { useEffect, useMemo, useState } from 'react'
import { api, type PayablesTrendPoint } from '../../../../lib/api'
import PlExportMenu, { type PlExportKind } from '../../../financials/pl-two-view/PlExportMenu'
import OperationalCard from '../../operational/OperationalCard'
import ChartFocusOverlay, { ChartFocusButton } from '../shared/ChartFocusOverlay'
import AgingChartFrame from '../shared/AgingChartFrame'
import PayablesTrendChart from './PayablesTrendChart'
import PayablesTrendChartEditor from './PayablesTrendChartEditor'
import {
  buildPayablesTrendSubtitle,
  loadPayablesTrendChartConfig,
  savePayablesTrendChartConfig,
  type PayablesTrendChartConfig,
} from './shared/payablesTrendChartConfig'
import { exportPayablesTrend } from './shared/payablesTrendExport'

const CHART_HEIGHT = 300
const FOCUS_HEIGHT = 480

export default function PayablesTrendSection({
  year,
  month,
  entity,
}: {
  year: number
  month: number
  entity?: string
}) {
  const [config, setConfig] = useState<PayablesTrendChartConfig>(() => loadPayablesTrendChartConfig())
  const [points, setPoints] = useState<PayablesTrendPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [focusOpen, setFocusOpen] = useState(false)

  useEffect(() => {
    savePayablesTrendChartConfig(config)
  }, [config])

  useEffect(() => {
    let ok = true
    setLoading(true)
    setError(null)
    api
      .salesPayablesTrend(year, month, entity, config.periodsBack, config.grain)
      .then(res => {
        if (!ok) return
        setPoints(res.points ?? [])
      })
      .catch(err => {
        if (!ok) return
        setPoints([])
        setError(err instanceof Error ? err.message : 'Could not load trend data')
      })
      .finally(() => {
        if (ok) setLoading(false)
      })
    return () => {
      ok = false
    }
  }, [year, month, entity, config.grain, config.periodsBack])

  const subtitle = useMemo(() => buildPayablesTrendSubtitle(config), [config])
  const hasData = points.length > 0 && config.metrics.length > 0
  const chartHeight = focusOpen ? FOCUS_HEIGHT : CHART_HEIGHT

  async function handleExport(kind: PlExportKind) {
    if (!hasData) return
    await exportPayablesTrend({ kind, points, config })
  }

  const chartBody = (
    <PayablesTrendChart points={points} metrics={config.metrics} chartHeight={chartHeight} />
  )

  return (
    <>
      <OperationalCard
        title="Payables trend"
        subtitle={subtitle}
        headerRight={(
          <div className="flex items-center gap-2 shrink-0">
            <PayablesTrendChartEditor config={config} onChange={setConfig} disabled={loading} />
            <ChartFocusButton onClick={() => setFocusOpen(true)} disabled={loading || !hasData} />
            <PlExportMenu formats={['pptx', 'xlsx']} onExport={handleExport} disabled={!hasData} />
          </div>
        )}
      >
        {error && !loading && (
          <div className="mb-3 px-3 py-2 rounded-lg text-xs" style={{ background: 'rgba(220,38,38,0.08)', color: '#B91C1C' }}>
            {error}
          </div>
        )}
        <AgingChartFrame hasData={hasData} loading={loading} height={chartHeight} emptyMessage="No trend data">
          {chartBody}
        </AgingChartFrame>
      </OperationalCard>

      <ChartFocusOverlay
        open={focusOpen}
        title="Payables trend"
        subtitle={subtitle}
        onClose={() => setFocusOpen(false)}
      >
        {chartBody}
      </ChartFocusOverlay>
    </>
  )
}
