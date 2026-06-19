import type { ReceivablesTrendPoint } from '../../../../lib/api'
import { exportFlatTablePptx } from '../../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr, type XlsxRow } from '../../../../lib/exportXlsx'
import type { PlExportKind } from '../../../financials/pl-two-view/PlExportMenu'
import {
  buildTrendChartSubtitle,
  trendMetricDef,
  type AgingTrendChartConfig,
  type ReceivablesTrendMetricId,
} from './agingTrendChartConfig'

function metricValue(id: ReceivablesTrendMetricId, p: ReceivablesTrendPoint): number | string {
  const v = p[id as keyof ReceivablesTrendPoint]
  if (v == null) return ''
  if (id === 'dso_days') return Number(v)
  if (id === 'mom_variance_pct' || id === 'overdue_pct') {
    return `${v}%`
  }
  return Number(v)
}

export async function exportReceivablesTrend(opts: {
  kind: PlExportKind
  points: ReceivablesTrendPoint[]
  config: AgingTrendChartConfig
}): Promise<void> {
  const { kind, points, config } = opts
  if (!points.length || !config.metrics.length) return

  const headers = ['Period', ...config.metrics.map(id => trendMetricDef(id).label)]
  const rows: XlsxRow[] = points.map(p => ({
    label: p.label,
    values: [p.label, ...config.metrics.map(id => metricValue(id, p))],
    kind: 'data',
  }))

  const base = `Receivables_Trend_${todayStr()}`
  const subtitle = buildTrendChartSubtitle(config)

  if (kind === 'pptx') {
    await exportFlatTablePptx({
      fileName: `${base}.pptx`,
      pageTitle: 'Receivables trend',
      tableHeading: 'Trend series',
      breadcrumbCurrent: 'Aging',
      breadcrumbParent: 'Sales',
      footerRight: subtitle,
      headers,
      rows,
    })
    return
  }

  await exportToXlsx({
    title: 'Receivables trend',
    subtitle,
    headers,
    rows,
    filename: `${base}.xlsx`,
  })
}
