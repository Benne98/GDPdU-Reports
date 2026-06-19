import type { SalesTopEntity } from '../../../lib/api'

export type TopCustomerDisplayRow =
  | { kind: 'customer'; row: SalesTopEntity }
  | { kind: 'subtotal'; label: string; totals: Record<string, number | null> }
  | { kind: 'total'; totals: Record<string, number | null> }

const TIER_CUTS = [0.3, 0.6, 0.8] as const

export type CustomerSortKey = 'cm' | 'ytd' | 'mtd'

/** Amount fields — row is hidden when every one is ~0. */
const ENTITY_METRIC_KEYS: (keyof SalesTopEntity)[] = [
  'cm',
  'pm',
  'py_cm',
  'ytd',
  'ytd_py',
  'mtd',
  'mtd_py',
  'mtd_pm',
  'plan_cm',
  'ytd_plan',
  'mtd_plan',
]

const ZERO_EPS = 1e-6

export function rowHasNonZeroMetrics(row: SalesTopEntity): boolean {
  for (const key of ENTITY_METRIC_KEYS) {
    const v = row[key]
    if (typeof v === 'number' && Math.abs(v) > ZERO_EPS) return true
  }
  return false
}

/** Drop partners with no activity in any period/plan column. */
export function filterNonZeroEntities(rows: SalesTopEntity[]): SalesTopEntity[] {
  return rows.filter(rowHasNonZeroMetrics)
}

const SORT_FIELDS: Record<CustomerSortKey, keyof SalesTopEntity> = {
  cm: 'cm',
  ytd: 'ytd',
  mtd: 'mtd',
}

export function sortMetric(row: SalesTopEntity, sortKey: CustomerSortKey): number {
  const v = row[SORT_FIELDS[sortKey]]
  return typeof v === 'number' ? v : 0
}

/** Subtotal deltas = difference of summed bases, not sum of row deltas. */
function derivedDelta(
  totals: Record<string, number | null>,
  field: string,
): number | null {
  const pairs: Record<string, [string, string]> = {
    delta_cm_pm: ['cm', 'pm'],
    delta_cm_py: ['cm', 'py_cm'],
    delta_ytd: ['ytd', 'ytd_py'],
    delta_mtd: ['mtd', 'mtd_py'],
  }
  const pair = pairs[field]
  if (!pair) return null
  const [aKey, bKey] = pair
  const a = totals[aKey]
  const b = totals[bKey]
  if (typeof a !== 'number' || typeof b !== 'number') return null
  return Math.round((a - b) * 100) / 100
}

function sumFields(
  rows: SalesTopEntity[],
  fields: string[],
  periodGrain: 'month' | 'week',
): Record<string, number | null> {
  const out: Record<string, number | null> = {}
  for (const f of fields) {
    if (f === 'name') continue
    if (f.startsWith('delta_')) continue
    if (f === 'coverage') {
      if (periodGrain === 'week') {
        const mtd = rows.reduce((s, r) => s + (r.mtd ?? 0), 0)
        const plan = rows.reduce((s, r) => s + (r.mtd_plan ?? 0), 0)
        out.coverage = plan > 1e-6 ? Math.round((mtd / plan) * 1000) / 10 : null
      } else {
        const ytd = rows.reduce((s, r) => s + (r.ytd ?? 0), 0)
        const plan = rows.reduce((s, r) => s + (r.ytd_plan ?? 0), 0)
        out.coverage = plan > 1e-6 ? Math.round((ytd / plan) * 1000) / 10 : null
      }
      continue
    }
    const nums = rows
      .map(r => (r as unknown as Record<string, number | undefined>)[f])
      .filter((n): n is number => typeof n === 'number')
    out[f] = nums.length ? nums.reduce((a, b) => a + b, 0) : null
  }
  for (const f of fields) {
    if (!f.startsWith('delta_')) continue
    out[f] = derivedDelta(out, f)
  }
  return out
}

function appendTotalRow(
  out: TopCustomerDisplayRow[],
  allRows: SalesTopEntity[],
  numericFields: string[],
  periodGrain: 'month' | 'week',
): void {
  if (!allRows.length) return
  out.push({ kind: 'total', totals: sumFields(allRows, numericFields, periodGrain) })
}

/** Cumulative gross-sales tiers at 30 / 60 / 80 % with subtotal rows (e.g. Top 1–6). */
export function buildSegmentedCustomerRows(
  customers: SalesTopEntity[],
  sortKey: CustomerSortKey,
  numericFields: string[],
  periodGrain: 'month' | 'week' = 'month',
): TopCustomerDisplayRow[] {
  const active = filterNonZeroEntities(customers)
  if (!active.length) return []

  const sorted = [...active].sort((a, b) => sortMetric(b, sortKey) - sortMetric(a, sortKey))
  const total = sorted.reduce((s, r) => s + Math.max(0, sortMetric(r, sortKey)), 0)
  if (total <= 0) {
    const out = sorted.map((row, i) => ({
      kind: 'customer' as const,
      row: { ...row, rank: i + 1 },
    }))
    appendTotalRow(out, sorted, numericFields, periodGrain)
    return out
  }

  const tiers: SalesTopEntity[][] = [[], [], [], []]
  let cum = 0

  for (const row of sorted) {
    const v = Math.max(0, sortMetric(row, sortKey))
    cum += v
    const pct = cum / total
    let tierIdx = 3
    if (pct <= TIER_CUTS[0]) tierIdx = 0
    else if (pct <= TIER_CUTS[1]) tierIdx = 1
    else if (pct <= TIER_CUTS[2]) tierIdx = 2
    tiers[tierIdx].push(row)
  }

  const out: TopCustomerDisplayRow[] = []
  let globalRank = 0
  let cursor = 1

  tiers.forEach(group => {
    if (!group.length) return
    const from = cursor
    group.forEach(row => {
      globalRank += 1
      out.push({ kind: 'customer', row: { ...row, rank: globalRank } })
    })
    const to = cursor + group.length - 1
    cursor = to + 1
    const label = from === to ? `Top ${from}` : `Top ${from}–${to}`
    out.push({ kind: 'subtotal', label, totals: sumFields(group, numericFields, periodGrain) })
  })

  appendTotalRow(out, sorted, numericFields, periodGrain)
  return out
}
