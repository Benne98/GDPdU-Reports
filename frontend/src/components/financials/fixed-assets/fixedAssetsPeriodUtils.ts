import type { PeriodSelection } from '../../../lib/periodSelection'
import { periodAnchorYearMonth } from '../../../lib/periodSelection'
import type { FixedAssetSnapshotInfo } from '../../../lib/api'

export function anchorDateFromPeriod(period: PeriodSelection, snapshots: FixedAssetSnapshotInfo[]): string | null {
  const anchor = periodAnchorYearMonth(period)
  const target = `${anchor.year}-12-31`
  const exact = snapshots.find(s => s.as_of_date === target)
  if (exact) return exact.as_of_date
  const before = snapshots
    .filter(s => s.as_of_date <= target)
    .sort((a, b) => b.as_of_date.localeCompare(a.as_of_date))
  return before[0]?.as_of_date ?? snapshots[0]?.as_of_date ?? null
}

export function defaultCompareDates(
  anchor: string,
  snapshots: FixedAssetSnapshotInfo[],
  max = 3,
): string[] {
  return snapshots
    .map(s => s.as_of_date)
    .filter(d => d !== anchor)
    .sort((a, b) => b.localeCompare(a))
    .slice(0, max)
}

export function yearsFromDates(anchor: string, compares: string[]): number[] {
  const years = [anchor, ...compares].map(d => parseInt(d.slice(0, 4), 10))
  return [...new Set(years)].sort((a, b) => a - b)
}
