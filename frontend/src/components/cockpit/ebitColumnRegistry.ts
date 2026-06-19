export type EbitDisplayColumn = 'pm' | 'cm' | 'delta_pm' | 'plan_cm' | 'delta_plan' | 'ytd'

export type EbitColumnDef = {
  id: EbitDisplayColumn
  label: string
  fixed?: boolean
}

const STORAGE_KEY = 'finssentials.overview-group.ebit-columns'

export const EBIT_COLUMN_CATALOG: EbitColumnDef[] = [
  { id: 'pm', label: 'Prior period', fixed: true },
  { id: 'cm', label: 'Current period', fixed: true },
  { id: 'delta_pm', label: 'Δ vs prior' },
  { id: 'plan_cm', label: 'Plan (CM)' },
  { id: 'delta_plan', label: 'Δ vs plan' },
  { id: 'ytd', label: 'YTD' },
]

export function defaultEbitColumns(): EbitDisplayColumn[] {
  return EBIT_COLUMN_CATALOG.map(c => c.id)
}

export function loadEbitColumns(): EbitDisplayColumn[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return defaultEbitColumns()
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return defaultEbitColumns()
    const valid = new Set(EBIT_COLUMN_CATALOG.map(c => c.id))
    const cols = parsed.filter((c): c is EbitDisplayColumn => typeof c === 'string' && valid.has(c as EbitDisplayColumn))
    const fixed = EBIT_COLUMN_CATALOG.filter(c => c.fixed).map(c => c.id)
    const merged = [...fixed, ...cols.filter(c => !fixed.includes(c))]
    return merged.length ? merged : defaultEbitColumns()
  } catch {
    return defaultEbitColumns()
  }
}

export function saveEbitColumns(cols: EbitDisplayColumn[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cols))
  } catch {
    /* ignore */
  }
}

export function columnHeaderLabel(
  col: EbitDisplayColumn,
  colLabels: { pm: string; cm: string; ytd: string },
): string {
  switch (col) {
    case 'pm':
      return colLabels.pm
    case 'cm':
      return colLabels.cm
    case 'ytd':
      return colLabels.ytd
    case 'delta_pm':
      return `Δ ${colLabels.cm} − ${colLabels.pm}`
    case 'plan_cm':
      return 'Plan'
    case 'delta_plan':
      return `Δ ${colLabels.cm} − Plan`
    default:
      return col
  }
}
