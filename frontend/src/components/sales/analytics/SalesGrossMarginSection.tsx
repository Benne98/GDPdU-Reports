import { useEffect, useMemo, useState } from 'react'
import {
  api,
  SalesFilters,
  type SalesDimensionPerformanceMetric,
  type SalesDimensionPerformanceResponse,
  type SalesDimensionPeriodScope,
} from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'
import PlExportMenu, { type PlExportKind } from '../../financials/pl-two-view/PlExportMenu'
import { exportFlatTablePptx } from '../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr } from '../../../lib/exportXlsx'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { periodAnchorYearMonth, periodQueryParams } from '../../../lib/periodSelection'
import SalesAnalyticsChartShell from './SalesAnalyticsChartShell'
import SalesDimensionPerformanceChart from './SalesDimensionPerformanceChart'
import SalesDimensionPerformanceEditor from './SalesDimensionPerformanceEditor'
import { analyticsDimLabel } from './salesChartRegistry'
import { dimensionPerfMetricLabel } from './salesDimensionPerformanceRegistry'
import { SALES_CHART_BODY_CLASS } from './salesChartTheme'

interface Props {
  period: PeriodSelection
  filters: SalesFilters
}

function matrixCellValue(
  metric: SalesDimensionPerformanceMetric,
  cell: {
    gross_sales_keur: number
    gross_profit_keur: number
    units_sold: number
    gross_margin_pct: number
  } | undefined,
): string {
  if (!cell) return '—'
  if (metric === 'units_sold') {
    return cell.units_sold.toLocaleString('de-DE')
  }
  if (metric === 'gross_profit') {
    return fmtChartKpi(cell.gross_profit_keur)
  }
  return fmtChartKpi(cell.gross_sales_keur)
}

export default function SalesGrossMarginSection({ period, filters }: Props) {
  const [dim, setDim] = useState('entity')
  const [metric, setMetric] = useState<SalesDimensionPerformanceMetric>('gross_sales')
  const [periodScope, setPeriodScope] = useState<SalesDimensionPeriodScope>('month')
  const [data, setData] = useState<SalesDimensionPerformanceResponse | null>(null)
  const [loading, setLoading] = useState(true)

  const anchor = periodAnchorYearMonth(period)
  const pq = periodQueryParams(period)

  useEffect(() => {
    setLoading(true)
    api
      .salesDimensionPerformance(
        anchor.year,
        anchor.month,
        dim,
        metric,
        periodScope,
        filters,
        {
          period_grain: pq.period_grain as string,
          iso_year: pq.iso_year as number | undefined,
          iso_week: pq.iso_week as number | undefined,
        },
      )
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [
    anchor.year,
    anchor.month,
    dim,
    metric,
    periodScope,
    filters,
    pq.period_grain,
    pq.iso_year,
    pq.iso_week,
  ])

  const dimLabel = data?.dim_label ?? analyticsDimLabel(dim)
  const subtitle = useMemo(() => {
    if (!data) {
      return `${dimensionPerfMetricLabel(metric)} · ${analyticsDimLabel(dim)} · invoiced basis`
    }
    const planHint = data.chart.segments.some(s => s.has_plan) ? ' · Plan' : ''
    return `${dimensionPerfMetricLabel(metric)} · ${data.period_label} vs ${data.prior_label}${planHint}`
  }, [data, metric, dim])

  async function handleExport(kind: PlExportKind) {
    if (!data?.matrix.rows.length) return
    const headers = [dimLabel, ...data.matrix.columns.map(c => c.label)]
    const xlsxRows = data.matrix.rows.map(r => ({
      label: r.dim_value,
      values: [
        r.dim_value,
        ...data.matrix.columns.map(c => matrixCellValue(metric, r.periods[c.key])),
      ],
      kind: 'data' as const,
    }))
    const base = `Dimension_Performance_${todayStr()}`
    if (kind === 'pptx') {
      await exportFlatTablePptx({
        fileName: `${base}.pptx`,
        pageTitle: 'Dimension performance',
        tableHeading: `${dimensionPerfMetricLabel(metric)} by ${dimLabel}`,
        breadcrumbCurrent: 'Profitability',
        breadcrumbParent: 'Sales',
        footerRight: dimensionPerfMetricLabel(metric),
        headers,
        rows: xlsxRows,
      })
      return
    }
    await exportToXlsx({
      title: 'Dimension performance',
      subtitle: `${dimensionPerfMetricLabel(metric)} · ${dimLabel}`,
      headers,
      rows: xlsxRows,
      filename: `${base}.xlsx`,
    })
  }

  return (
    <SalesAnalyticsChartShell
      className="mb-6"
      title="Performance by dimension"
      subtitle={subtitle}
      actions={(
        <div className="flex items-center gap-2">
          <PlExportMenu
            formats={['pptx', 'xlsx']}
            onExport={handleExport}
            disabled={!data?.matrix.rows.length}
          />
          <SalesDimensionPerformanceEditor
            dim={dim}
            metric={metric}
            periodScope={periodScope}
            onDimChange={setDim}
            onMetricChange={setMetric}
            onPeriodScopeChange={setPeriodScope}
            disabled={loading}
          />
        </div>
      )}
      bodyClassName="flex flex-col"
    >
      <div className={SALES_CHART_BODY_CLASS}>
        {loading ? (
          <div className="flex items-center justify-center h-[300px] text-xs" style={{ color: '#94A3B8' }}>
            Loading…
          </div>
        ) : !data?.chart.segments.length ? (
          <div className="flex items-center justify-center h-[300px] text-xs" style={{ color: '#94A3B8' }}>
            No data
          </div>
        ) : (
          <SalesDimensionPerformanceChart data={data} />
        )}
      </div>

      <div className="border-t px-4 py-3" style={{ borderColor: '#F1F5F9' }}>
        <p className="text-[10px] font-medium mb-2 uppercase tracking-wide" style={{ color: '#94A3B8' }}>
          {dimensionPerfMetricLabel(metric)} by period
        </p>
        <div className="overflow-auto" style={{ maxHeight: 360 }}>
          {loading ? (
            <div className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>Loading…</div>
          ) : !data?.matrix.rows.length ? (
            <div className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>No data</div>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr style={{ background: '#F8FAFC' }}>
                  <th
                    className="text-left px-3 py-2 font-semibold sticky left-0"
                    style={{ color: '#475569', background: '#F8FAFC' }}
                  >
                    {dimLabel}
                  </th>
                  {data.matrix.columns.map(c => (
                    <th key={c.key} className="text-right px-3 py-2 font-semibold whitespace-nowrap" style={{ color: '#475569' }}>
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.matrix.rows.map(row => (
                  <tr key={row.dim_value} style={{ borderBottom: '1px solid #F8FAFC' }}>
                    <td
                      className="px-3 py-2 font-medium sticky left-0"
                      style={{ color: '#334155', background: '#FFFFFF' }}
                    >
                      {row.dim_value}
                    </td>
                    {data.matrix.columns.map(c => (
                      <td
                        key={c.key}
                        className="px-3 py-2 text-right tabular-nums"
                        style={{ color: '#1E3A5F' }}
                      >
                        {matrixCellValue(metric, row.periods[c.key])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </SalesAnalyticsChartShell>
  )
}
