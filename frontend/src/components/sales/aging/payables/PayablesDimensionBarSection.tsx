import { useEffect, useMemo, useState } from 'react'
import { api, type PayablesDimensionChartResponse } from '../../../../lib/api'
import { exportFlatTablePptx } from '../../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr } from '../../../../lib/exportXlsx'
import PlExportMenu, { type PlExportKind } from '../../../financials/pl-two-view/PlExportMenu'
import { countryDisplayName } from '../../../../lib/countryLabels'
import { fmtAmount } from '../../../../lib/fmt'
import OperationalCard from '../../operational/OperationalCard'
import ReceivablesDimensionBarChart, { buildDimensionBarCompareSubtitle } from '../ReceivablesDimensionBarChart'
import PayablesDimensionBarEditor from './PayablesDimensionBarEditor'
import ChartFocusOverlay, { ChartFocusButton } from '../shared/ChartFocusOverlay'
import {
  loadPayablesDimensionBarConfig,
  payablesDimensionBarLabel,
  savePayablesDimensionBarConfig,
  type PayablesDimensionBarConfig,
} from '../shared/payablesDimensionBarConfig'

const COMPACT_CHART_HEIGHT = 220
const EXPANDED_CHART_HEIGHT = 300
const TREND_SYNC_EXTRA = 240

function labelForRow(label: string, dimension: string): string {
  return dimension === 'country' ? countryDisplayName(label) : label
}

export default function PayablesDimensionBarSection({
  year,
  month,
  entity,
  trendExpanded,
}: {
  year: number
  month: number
  entity?: string
  trendExpanded: boolean
}) {
  const [config, setConfig] = useState<PayablesDimensionBarConfig>(() => loadPayablesDimensionBarConfig())
  const [data, setData] = useState<PayablesDimensionChartResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [focusOpen, setFocusOpen] = useState(false)

  useEffect(() => {
    savePayablesDimensionBarConfig(config)
  }, [config])

  useEffect(() => {
    let ok = true
    setLoading(true)
    setError(null)
    api
      .salesPayablesDimensionChart(
        config.dimension,
        year,
        month,
        entity,
        25,
        config.comparePm,
        config.comparePy,
      )
      .then(r => {
        if (!ok) return
        setData(r)
      })
      .catch(err => {
        if (!ok) return
        setData(null)
        setError(err instanceof Error ? err.message : 'Could not load chart')
      })
      .finally(() => {
        if (ok) setLoading(false)
      })
    return () => {
      ok = false
    }
  }, [config, year, month, entity])

  const rows = data?.rows ?? []
  const dimLabel = payablesDimensionBarLabel(config.dimension)
  const hasData = rows.length > 0

  const chartHeight = useMemo(() => {
    if (focusOpen) return 480
    if (trendExpanded) return EXPANDED_CHART_HEIGHT + TREND_SYNC_EXTRA
    return Math.max(COMPACT_CHART_HEIGHT, rows.length * 48)
  }, [focusOpen, trendExpanded, rows.length])

  const compareHint = useMemo(
    () => buildDimensionBarCompareSubtitle(year, month, config.comparePm, config.comparePy),
    [year, month, config.comparePm, config.comparePy],
  )

  async function handleExport(kind: PlExportKind) {
    if (!rows.length) return
    const headers = [
      dimLabel,
      'Current',
      ...(config.comparePm ? ['Prior month', 'Δ PM', 'Δ PM %'] : []),
      ...(config.comparePy ? ['Prior year', 'Δ PY', 'Δ PY %'] : []),
    ]
    const exportRows = rows.map(r => ({
      label: labelForRow(r.label, config.dimension),
      values: [
        labelForRow(r.label, config.dimension),
        fmtAmount(r.balance),
        ...(config.comparePm
          ? [
              r.pm_balance != null ? fmtAmount(r.pm_balance) : '—',
              r.delta_pm != null ? fmtAmount(r.delta_pm) : '—',
              r.delta_pm_pct != null ? `${r.delta_pm_pct}%` : '—',
            ]
          : []),
        ...(config.comparePy
          ? [
              r.py_balance != null ? fmtAmount(r.py_balance) : '—',
              r.delta_py != null ? fmtAmount(r.delta_py) : '—',
              r.delta_py_pct != null ? `${r.delta_py_pct}%` : '—',
            ]
          : []),
      ],
      kind: 'data' as const,
    }))
    const base = `Payables_Dimension_${todayStr()}`
    if (kind === 'pptx') {
      await exportFlatTablePptx({
        fileName: `${base}.pptx`,
        pageTitle: `Open payables by ${dimLabel}`,
        tableHeading: `Open payables by ${dimLabel}`,
        breadcrumbCurrent: 'Aging',
        breadcrumbParent: 'Sales',
        footerRight: compareHint,
        headers,
        rows: exportRows,
      })
      return
    }
    await exportToXlsx({
      title: `Open payables by ${dimLabel}`,
      subtitle: compareHint,
      headers,
      rows: exportRows,
      filename: `${base}.xlsx`,
    })
  }

  const chartBody =
    loading ? (
      <div className="flex items-center justify-center text-xs" style={{ color: '#94A3B8', height: chartHeight }}>
        Loading…
      </div>
    ) : !hasData ? (
      <div className="flex items-center justify-center text-xs" style={{ color: '#94A3B8', height: chartHeight }}>
        No data for this dimension
      </div>
    ) : (
      <ReceivablesDimensionBarChart
        rows={rows}
        dimension={config.dimension}
        comparePm={config.comparePm}
        comparePy={config.comparePy}
        year={year}
        month={month}
        chartHeight={chartHeight}
      />
    )

  return (
    <>
      <OperationalCard
        title={`By ${dimLabel.toLowerCase()}`}
        subtitle={compareHint}
        className="h-full flex flex-col"
        headerRight={
          <div className="flex items-center gap-2 shrink-0">
            <PayablesDimensionBarEditor
              config={config}
              onChange={setConfig}
              disabled={loading}
            />
            <ChartFocusButton onClick={() => setFocusOpen(true)} disabled={loading || !hasData} />
            <PlExportMenu formats={['pptx', 'xlsx']} onExport={handleExport} disabled={!hasData} />
          </div>
        }
      >
        {error && !loading && (
          <div className="mb-3 px-3 py-2 rounded-lg text-xs" style={{ background: 'rgba(220,38,38,0.08)', color: '#B91C1C' }}>
            {error}
          </div>
        )}
        <div
          className={`flex-1 min-h-0 ${trendExpanded ? '' : 'overflow-y-auto overflow-x-hidden'}`}
          style={trendExpanded ? { minHeight: chartHeight } : { maxHeight: 280 }}
        >
          {chartBody}
        </div>
      </OperationalCard>

      <ChartFocusOverlay
        open={focusOpen}
        title={`Open payables by ${dimLabel.toLowerCase()}`}
        subtitle={compareHint}
        onClose={() => setFocusOpen(false)}
      >
        {chartBody}
      </ChartFocusOverlay>
    </>
  )
}
