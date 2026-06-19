export type AgingTrendGrain = 'week' | 'month' | 'quarter'

export type ReceivablesTrendMetricId =
  | 'balance'
  | 'overdue'
  | 'before_due'
  | 'gross_sales'
  | 'dso_days'
  | 'mom_variance_pct'
  | 'overdue_pct'

export type AgingTrendChartConfig = {
  metrics: ReceivablesTrendMetricId[]
  grain: AgingTrendGrain
  periodsBack: 6 | 12 | 18 | 24
}

export type TrendMetricDef = {
  id: ReceivablesTrendMetricId
  label: string
  axis: 'amount' | 'ratio'
  color: string
  strokeDasharray?: string
  defaultOn: boolean
}

export const RECEIVABLES_TREND_METRICS: TrendMetricDef[] = [
  { id: 'balance', label: 'Open receivables', axis: 'amount', color: '#2563EB', defaultOn: true },
  { id: 'overdue', label: 'Overdue', axis: 'amount', color: '#F59E0B', defaultOn: true },
  { id: 'before_due', label: 'Not yet due', axis: 'amount', color: '#10B981', strokeDasharray: '4 4', defaultOn: false },
  { id: 'gross_sales', label: 'Gross sales', axis: 'amount', color: '#64748B', strokeDasharray: '5 5', defaultOn: true },
  { id: 'mom_variance_pct', label: 'MoM variance %', axis: 'ratio', color: '#059669', defaultOn: false },
  { id: 'overdue_pct', label: 'Overdue %', axis: 'ratio', color: '#DC2626', strokeDasharray: '3 3', defaultOn: true },
]

export const AGING_TREND_GRAIN_OPTIONS: { id: AgingTrendGrain; label: string }[] = [
  { id: 'week', label: 'Weekly' },
  { id: 'month', label: 'Monthly' },
  { id: 'quarter', label: 'Quarterly' },
]

export const AGING_TREND_PERIOD_OPTIONS: { value: 6 | 12 | 18 | 24; label: string }[] = [
  { value: 6, label: '6 periods' },
  { value: 12, label: '12 periods' },
  { value: 18, label: '18 periods' },
  { value: 24, label: '24 periods' },
]

const STORAGE_KEY = 'finssentials.receivables.trend-chart.v1'

const DEFAULT_METRICS = RECEIVABLES_TREND_METRICS.filter(m => m.defaultOn).map(m => m.id)

function uniqueMetricIds(ids: ReceivablesTrendMetricId[]): ReceivablesTrendMetricId[] {
  const seen = new Set<ReceivablesTrendMetricId>()
  const out: ReceivablesTrendMetricId[] = []
  for (const id of ids) {
    if (seen.has(id)) continue
    seen.add(id)
    out.push(id)
  }
  return out
}

export function defaultAgingTrendChartConfig(): AgingTrendChartConfig {
  return {
    metrics: DEFAULT_METRICS,
    grain: 'month',
    periodsBack: 12,
  }
}

export function loadAgingTrendChartConfig(): AgingTrendChartConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return defaultAgingTrendChartConfig()
    const parsed = JSON.parse(raw) as Partial<AgingTrendChartConfig>
    const validIds = new Set(RECEIVABLES_TREND_METRICS.map(m => m.id))
    const metrics = uniqueMetricIds((parsed.metrics ?? [])
      .filter((m): m is ReceivablesTrendMetricId => validIds.has(m as ReceivablesTrendMetricId))
      .filter(m => m !== 'dso_days'))
    return {
      metrics: metrics.length ? metrics : DEFAULT_METRICS,
      grain: parsed.grain === 'week' || parsed.grain === 'quarter' ? parsed.grain : 'month',
      periodsBack: [6, 12, 18, 24].includes(parsed.periodsBack as number)
        ? (parsed.periodsBack as 6 | 12 | 18 | 24)
        : 12,
    }
  } catch {
    return defaultAgingTrendChartConfig()
  }
}

export function saveAgingTrendChartConfig(config: AgingTrendChartConfig): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
  } catch { /* ignore */ }
}

export function trendMetricDef(id: ReceivablesTrendMetricId): TrendMetricDef {
  return RECEIVABLES_TREND_METRICS.find(m => m.id === id) ?? RECEIVABLES_TREND_METRICS[0]
}

export function buildTrendChartSubtitle(config: AgingTrendChartConfig): string {
  const grainLabel = AGING_TREND_GRAIN_OPTIONS.find(g => g.id === config.grain)?.label ?? 'Monthly'
  const metricLabels = config.metrics.map(id => trendMetricDef(id).label).join(' · ')
  return `${grainLabel} · ${config.periodsBack} periods · ${metricLabels || 'Select metrics'}`
}
