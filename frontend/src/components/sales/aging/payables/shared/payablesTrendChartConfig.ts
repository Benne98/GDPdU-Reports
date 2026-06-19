export type PayablesTrendGrain = 'week' | 'month' | 'quarter'

export type PayablesTrendMetricId =
  | 'balance'
  | 'overdue'
  | 'before_due'
  | 'cost_of_materials'
  | 'mom_variance_pct'
  | 'overdue_pct'

export type PayablesTrendChartConfig = {
  metrics: PayablesTrendMetricId[]
  grain: PayablesTrendGrain
  periodsBack: 6 | 12 | 18 | 24
}

export type PayablesTrendMetricDef = {
  id: PayablesTrendMetricId
  label: string
  axis: 'amount' | 'ratio'
  color: string
  strokeDasharray?: string
  defaultOn: boolean
}

export const PAYABLES_TREND_METRICS: PayablesTrendMetricDef[] = [
  { id: 'balance', label: 'Open payables', axis: 'amount', color: '#2563EB', defaultOn: true },
  { id: 'overdue', label: 'Overdue', axis: 'amount', color: '#F59E0B', defaultOn: true },
  { id: 'before_due', label: 'Not yet due', axis: 'amount', color: '#10B981', strokeDasharray: '4 4', defaultOn: false },
  { id: 'cost_of_materials', label: 'Cost of materials', axis: 'amount', color: '#64748B', strokeDasharray: '5 5', defaultOn: true },
  { id: 'mom_variance_pct', label: 'MoM variance %', axis: 'ratio', color: '#059669', defaultOn: false },
  { id: 'overdue_pct', label: 'Overdue %', axis: 'ratio', color: '#DC2626', strokeDasharray: '3 3', defaultOn: true },
]

export const PAYABLES_TREND_GRAIN_OPTIONS: { id: PayablesTrendGrain; label: string }[] = [
  { id: 'week', label: 'Weekly' },
  { id: 'month', label: 'Monthly' },
  { id: 'quarter', label: 'Quarterly' },
]

export const PAYABLES_TREND_PERIOD_OPTIONS: { value: 6 | 12 | 18 | 24; label: string }[] = [
  { value: 6, label: '6 periods' },
  { value: 12, label: '12 periods' },
  { value: 18, label: '18 periods' },
  { value: 24, label: '24 periods' },
]

const STORAGE_KEY = 'finssentials.payables.trend-chart.v2'
const LEGACY_STORAGE_KEY = 'finssentials.payables.trend-chart.v1'

const DEFAULT_METRICS = PAYABLES_TREND_METRICS.filter(m => m.defaultOn).map(m => m.id)

function uniqueMetricIds(ids: PayablesTrendMetricId[]): PayablesTrendMetricId[] {
  const seen = new Set<PayablesTrendMetricId>()
  const out: PayablesTrendMetricId[] = []
  for (const id of ids) {
    if (seen.has(id)) continue
    seen.add(id)
    out.push(id)
  }
  return out
}

export function defaultPayablesTrendChartConfig(): PayablesTrendChartConfig {
  return {
    metrics: DEFAULT_METRICS,
    grain: 'month',
    periodsBack: 12,
  }
}

function readStoredConfig(): Partial<PayablesTrendChartConfig> | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(LEGACY_STORAGE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as Partial<PayablesTrendChartConfig>
  } catch {
    return null
  }
}

export function loadPayablesTrendChartConfig(): PayablesTrendChartConfig {
  const parsed = readStoredConfig()
  if (!parsed) return defaultPayablesTrendChartConfig()

  const validIds = new Set(PAYABLES_TREND_METRICS.map(m => m.id))
  const metrics = uniqueMetricIds((parsed.metrics ?? [])
    .map(m => (String(m) === 'procurement_spend' ? 'cost_of_materials' : m))
    .filter((m): m is PayablesTrendMetricId => validIds.has(m as PayablesTrendMetricId)))

  return {
    metrics: metrics.length ? metrics : DEFAULT_METRICS,
    grain: parsed.grain === 'week' || parsed.grain === 'quarter' ? parsed.grain : 'month',
    periodsBack: [6, 12, 18, 24].includes(parsed.periodsBack as number)
      ? (parsed.periodsBack as 6 | 12 | 18 | 24)
      : 12,
  }
}

export function savePayablesTrendChartConfig(config: PayablesTrendChartConfig): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
  } catch { /* ignore */ }
}

export function payablesTrendMetricDef(id: PayablesTrendMetricId): PayablesTrendMetricDef {
  return PAYABLES_TREND_METRICS.find(m => m.id === id) ?? PAYABLES_TREND_METRICS[0]
}

export function buildPayablesTrendSubtitle(config: PayablesTrendChartConfig): string {
  const grainLabel = PAYABLES_TREND_GRAIN_OPTIONS.find(g => g.id === config.grain)?.label ?? 'Monthly'
  const metricLabels = config.metrics.map(id => payablesTrendMetricDef(id).label).join(' · ')
  return `${grainLabel} · ${config.periodsBack} periods · ${metricLabels || 'Select metrics'}`
}

/** Map config metric id to trend point field (procurement_spend legacy alias). */
export function payablesTrendMetricField(id: PayablesTrendMetricId): PayablesTrendMetricId | 'procurement_spend' {
  if (id === 'cost_of_materials') return 'cost_of_materials'
  return id
}
