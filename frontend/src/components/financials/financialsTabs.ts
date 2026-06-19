export type FinTab = 'overview' | 'pl' | 'bs' | 'cf' | 'wc'

export const FIN_TABS: { id: FinTab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'pl', label: 'Income statement' },
  { id: 'bs', label: 'Balance sheet' },
  { id: 'wc', label: 'Working capital' },
  { id: 'cf', label: 'Cash flow' },
]

export function finTabFromQuery(value: string | null): FinTab | null {
  if (value === 'overview' || value === 'pl' || value === 'bs' || value === 'cf' || value === 'wc') {
    return value
  }
  return null
}
