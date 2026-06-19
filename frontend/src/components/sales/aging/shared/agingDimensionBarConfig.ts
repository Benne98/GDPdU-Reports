const STORAGE_KEY = 'finssentials.receivables.dimension-bar.v1'

export const AGING_DIMENSION_BAR_OPTIONS: { id: string; label: string }[] = [
  { id: 'entity', label: 'Legal entity' },
  { id: 'country', label: 'Country' },
  { id: 'segment', label: 'Segment' },
  { id: 'customer_group', label: 'Customer group' },
  { id: 'salesperson', label: 'Salesperson' },
  { id: 'customer', label: 'Customer' },
]

export type AgingDimensionBarConfig = {
  dimension: string
  comparePm: boolean
  comparePy: boolean
}

export const DEFAULT_AGING_DIMENSION_BAR_CONFIG: AgingDimensionBarConfig = {
  dimension: 'entity',
  comparePm: true,
  comparePy: false,
}

export function loadAgingDimensionBarConfig(): AgingDimensionBarConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULT_AGING_DIMENSION_BAR_CONFIG }
    const parsed = JSON.parse(raw) as Partial<AgingDimensionBarConfig>
    const valid = new Set(AGING_DIMENSION_BAR_OPTIONS.map(d => d.id))
    return {
      dimension: parsed.dimension && valid.has(parsed.dimension) ? parsed.dimension : 'entity',
      comparePm: parsed.comparePm !== false,
      comparePy: !!parsed.comparePy,
    }
  } catch {
    return { ...DEFAULT_AGING_DIMENSION_BAR_CONFIG }
  }
}

export function saveAgingDimensionBarConfig(cfg: AgingDimensionBarConfig): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg))
  } catch { /* ignore */ }
}

export function agingDimensionBarLabel(dim: string): string {
  return AGING_DIMENSION_BAR_OPTIONS.find(d => d.id === dim)?.label ?? dim
}
