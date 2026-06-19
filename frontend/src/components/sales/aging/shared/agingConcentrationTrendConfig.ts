export type ConcentrationAgingBucketId =
  | 'all'
  | 'not_yet_due'
  | 'overdue_1_30'
  | 'overdue_31_60'
  | 'overdue_61_90'
  | 'overdue_91_180'
  | 'overdue_over_180'

export type AgingConcentrationTrendConfig = {
  agingBucket: ConcentrationAgingBucketId
}

export const CONCENTRATION_AGING_BUCKET_OPTIONS: {
  id: ConcentrationAgingBucketId
  label: string
}[] = [
  { id: 'all', label: 'Open receivables (all)' },
  { id: 'not_yet_due', label: 'Not yet due' },
  { id: 'overdue_1_30', label: '1–30 days overdue' },
  { id: 'overdue_31_60', label: '31–60 days overdue' },
  { id: 'overdue_61_90', label: '61–90 days overdue' },
  { id: 'overdue_91_180', label: '91–180 days overdue' },
  { id: 'overdue_over_180', label: '>180 days overdue' },
]

const STORAGE_KEY = 'finssentials.receivables.concentration-trend.v1'

const VALID_BUCKETS = new Set(CONCENTRATION_AGING_BUCKET_OPTIONS.map(o => o.id))

export function defaultAgingConcentrationTrendConfig(): AgingConcentrationTrendConfig {
  return { agingBucket: 'all' }
}

export function loadAgingConcentrationTrendConfig(): AgingConcentrationTrendConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return defaultAgingConcentrationTrendConfig()
    const parsed = JSON.parse(raw) as Partial<AgingConcentrationTrendConfig>
    const agingBucket = parsed.agingBucket
    return {
      agingBucket: VALID_BUCKETS.has(agingBucket as ConcentrationAgingBucketId)
        ? (agingBucket as ConcentrationAgingBucketId)
        : 'all',
    }
  } catch {
    return defaultAgingConcentrationTrendConfig()
  }
}

export function saveAgingConcentrationTrendConfig(config: AgingConcentrationTrendConfig): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
  } catch { /* ignore */ }
}

export function concentrationAgingBucketLabel(id: ConcentrationAgingBucketId): string {
  return CONCENTRATION_AGING_BUCKET_OPTIONS.find(o => o.id === id)?.label ?? 'Open receivables'
}

export function buildConcentrationTrendSubtitle(
  config: AgingConcentrationTrendConfig,
  periodGrain: 'month' | 'week' | 'year',
): string {
  const span =
    periodGrain === 'week' ? '12 weeks' : periodGrain === 'year' ? '12 months' : '12 months'
  return `${concentrationAgingBucketLabel(config.agingBucket)} · last ${span}`
}
