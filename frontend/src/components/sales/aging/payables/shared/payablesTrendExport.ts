import type { PayablesTrendPoint } from '../../../../../lib/api'
import { exportFlatTablePptx } from '../../../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr, type XlsxRow } from '../../../../../lib/exportXlsx'
import type { PlExportKind } from '../../../../financials/pl-two-view/PlExportMenu'
import {
  buildPayablesTrendSubtitle,
  payablesTrendMetricDef,
  payablesTrendMetricField,
  type PayablesTrendChartConfig,
  type PayablesTrendMetricId,
} from './payablesTrendChartConfig'

function metricValue(id: PayablesTrendMetricId, p: PayablesTrendPoint): number | string {
  const field = payablesTrendMetricField(id)
  let v: unknown
  if (field === 'cost_of_materials') {
    v = p.cost_of_materials ?? p.procurement_spend
  } else {
    v = p[field as keyof PayablesTrendPoint]
  }
  if (v == null) return ''
  if (id === 'mom_variance_pct' || id === 'overdue_pct') {
    return `${v}%`
  }
  return Number(v)
}

export async function exportPayablesTrend(opts: {
  kind: PlExportKind
  points: PayablesTrendPoint[]
  config: PayablesTrendChartConfig
}): Promise<void> {
  const { kind, points, config } = opts
  if (!points.length || !config.metrics.length) return

  const headers = ['Period', ...config.metrics.map(id => payablesTrendMetricDef(id).label)]
  const rows: XlsxRow[] = points.map(p => ({
    label: p.label,
    values: [p.label, ...config.metrics.map(id => metricValue(id, p))],
    kind: 'data',
  }))

  const base = `Payables_Trend_${todayStr()}`
  const subtitle = buildPayablesTrendSubtitle(config)

  if (kind === 'pptx') {
    await exportFlatTablePptx({
      fileName: `${base}.pptx`,
      pageTitle: 'Payables trend',
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
    title: 'Payables trend',
    subtitle,
    headers,
    rows,
    filename: `${base}.xlsx`,
  })
}
