const STORAGE_KEY = 'finssentials.payables.dimension-bar.v1'

export const PAYABLES_DIMENSION_BAR_OPTIONS: { id: string; label: string }[] = [
  { id: 'entity', label: 'Legal entity' },
  { id: 'country', label: 'Country' },
  { id: 'segment', label: 'Segment' },
  { id: 'supplier_group', label: 'Supplier group' },
  { id: 'buyer', label: 'Buyer' },
  { id: 'supplier', label: 'Supplier' },
]

export type PayablesDimensionBarConfig = {
  dimension: string
  comparePm: boolean
  comparePy: boolean
}

export const DEFAULT_PAYABLES_DIMENSION_BAR_CONFIG: PayablesDimensionBarConfig = {
  dimension: 'entity',
  comparePm: true,
  comparePy: false,
}

export function loadPayablesDimensionBarConfig(): PayablesDimensionBarConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULT_PAYABLES_DIMENSION_BAR_CONFIG }
    const parsed = JSON.parse(raw) as Partial<PayablesDimensionBarConfig>
    const valid = new Set(PAYABLES_DIMENSION_BAR_OPTIONS.map(d => d.id))
    return {
      dimension: parsed.dimension && valid.has(parsed.dimension) ? parsed.dimension : 'entity',
      comparePm: parsed.comparePm !== false,
      comparePy: !!parsed.comparePy,
    }
  } catch {
    return { ...DEFAULT_PAYABLES_DIMENSION_BAR_CONFIG }
  }
}

export function savePayablesDimensionBarConfig(cfg: PayablesDimensionBarConfig): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg))
  } catch { /* ignore */ }
}

export function payablesDimensionBarLabel(dim: string): string {
  return PAYABLES_DIMENSION_BAR_OPTIONS.find(d => d.id === dim)?.label ?? dim
}
