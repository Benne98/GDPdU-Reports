import type { PayablesConcentrationTrendPoint } from '../../../../../lib/api'
import { exportFlatTablePptx } from '../../../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr, type XlsxRow } from '../../../../../lib/exportXlsx'
import type { PlExportKind } from '../../../../financials/pl-two-view/PlExportMenu'
import {
  AGING_CONCENTRATION_BANDS,
  sortConcentrationSegments,
} from '../../shared/agingConcentrationBands'
import {
  buildConcentrationTrendSubtitle,
  type AgingConcentrationTrendConfig,
} from '../../shared/agingConcentrationTrendConfig'
import { normalizePayablesConcentration } from '../../shared/normalizeConcentration'

function trendRows(
  points: PayablesConcentrationTrendPoint[],
): { headers: string[]; rows: XlsxRow[] } {
  const bandLabels = new Map<string, string>()
  for (const p of points) {
    for (const seg of p.segments ?? []) {
      bandLabels.set(seg.band, seg.label)
    }
  }
  const bands = AGING_CONCENTRATION_BANDS.map(b => b.band)
  const headers = ['Period', ...bands.map(b => bandLabels.get(b) ?? b)]

  const rows: XlsxRow[] = points.map(p => {
    const normalized = normalizePayablesConcentration({
      year: p.year,
      month: p.month,
      total: p.segments?.reduce((s, seg) => s + seg.amount, 0) ?? 0,
      segments: p.segments,
    })
    const byBand = new Map(sortConcentrationSegments(normalized?.segments ?? []).map(s => [s.band, s.pct]))
    return {
      label: p.label,
      values: [p.label, ...bands.map(b => `${byBand.get(b) ?? 0}%`)],
      kind: 'data',
    }
  })

  return { headers, rows }
}

export async function exportPayablesConcentrationTrend(opts: {
  kind: PlExportKind
  points: PayablesConcentrationTrendPoint[]
  config: AgingConcentrationTrendConfig
  periodGrain: 'month' | 'week' | 'year'
}): Promise<void> {
  const { kind, points, config, periodGrain } = opts
  if (!points.length) return

  const { headers, rows } = trendRows(points)
  const base = `Payables_Concentration_Trend_${todayStr()}`
  const subtitle = buildConcentrationTrendSubtitle(config, periodGrain)

  if (kind === 'pptx') {
    await exportFlatTablePptx({
      fileName: `${base}.pptx`,
      pageTitle: 'Supplier concentration trend',
      tableHeading: 'Rank-band share',
      breadcrumbCurrent: 'Aging',
      breadcrumbParent: 'Sales',
      footerRight: subtitle,
      headers,
      rows,
    })
    return
  }

  await exportToXlsx({
    title: 'Supplier concentration trend',
    subtitle,
    headers,
    rows,
    filename: `${base}.xlsx`,
  })
}
