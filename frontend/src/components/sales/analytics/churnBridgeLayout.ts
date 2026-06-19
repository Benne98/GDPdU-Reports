import type { SalesChurnBridge, SalesChurnBridgeResponse } from '../../../lib/api'

export type ChurnGrain = 'year' | 'quarter' | 'month'

export type ChurnColumnKind = 'dim' | 'total' | 'component'

export type ChurnComponentKey = 'new' | 'upsell' | 'cross_sell' | 'downsell' | 'lost'

export type ChurnTableColumn = {
  key: string
  label: string
  kind: ChurnColumnKind
  component?: ChurnComponentKey
}

export const CHURN_BRIDGE_COMPONENTS: Array<{ key: ChurnComponentKey; label: string }> = [
  { key: 'new', label: 'New' },
  { key: 'upsell', label: 'Upsell' },
  { key: 'cross_sell', label: 'Cross' },
  { key: 'downsell', label: 'Downsell' },
  { key: 'lost', label: 'Lost' },
]

export const CHURN_DIM_OPTIONS = [
  { key: 'entity', label: 'Entity' },
  { key: 'customer_name', label: 'End customer' },
  { key: 'customer_markets', label: 'Customer markets' },
  { key: 'distribution_type', label: 'Distribution type' },
  { key: 'end_customer_country', label: 'Geography' },
  { key: 'industry_sub_sector', label: 'Industry' },
  { key: 'product_revenue_model', label: 'Revenue model' },
] as const

export function churnDimLabel(key: string): string {
  return CHURN_DIM_OPTIONS.find(d => d.key === key)?.label ?? key
}

export function buildChurnTableColumns(data: SalesChurnBridgeResponse): ChurnTableColumn[] {
  const cols: ChurnTableColumn[] = [{ key: 'dim', label: 'Dimension', kind: 'dim' }]
  const periods = data.periods ?? []
  const bridges = data.bridges ?? []

  periods.forEach((label, i) => {
    cols.push({ key: `p${i}`, label, kind: 'total' })
    if (i < bridges.length) {
      CHURN_BRIDGE_COMPONENTS.forEach(c => {
        cols.push({
          key: `b${i}_${c.key}`,
          label: c.label,
          kind: 'component',
          component: c.key,
        })
      })
    }
  })
  return cols
}

export function churnDataColumnCount(columns: ChurnTableColumn[]): number {
  return columns.filter(c => c.kind !== 'dim').length
}

export function churnGridTemplate(columns: ChurnTableColumn[], dimWidthPct = 16): string {
  const dataCols = churnDataColumnCount(columns)
  if (dataCols === 0) return `${dimWidthPct}% 1fr`
  const slot = `${((100 - dimWidthPct) / dataCols).toFixed(4)}%`
  return columns.map(c => (c.kind === 'dim' ? `${dimWidthPct}%` : slot)).join(' ')
}

export function isChurnCurrentMonthColumn(col: ChurnTableColumn, periodCount: number): boolean {
  if (col.kind !== 'total' || periodCount < 1) return false
  const pi = Number(col.key.match(/^p(\d+)$/)?.[1])
  return !Number.isNaN(pi) && pi === periodCount - 1
}

export function churnCellValue(
  col: ChurnTableColumn,
  dimValue: string,
  periodTotals: number[],
  bridges: Array<Pick<SalesChurnBridge, ChurnComponentKey>>,
): string | number | null {
  if (col.kind === 'dim') return dimValue
  const pi = Number(col.key.match(/^p(\d+)$/)?.[1])
  if (!Number.isNaN(pi)) return periodTotals[pi] ?? null
  const m = col.key.match(/^b(\d+)_(new|upsell|cross_sell|downsell|lost)$/)
  if (m) {
    const bi = Number(m[1])
    const ck = m[2] as ChurnComponentKey
    return bridges[bi]?.[ck] ?? null
  }
  return null
}

export function buildChurnBridgePeriods(
  year: number,
  month: number,
  grain: ChurnGrain,
): Array<{ ja: string; je: string }> {
  const lastDay = (y: number, m: number) => {
    const d = new Date(y, m, 0)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }
  const iso = (y: number, m: number, d: number) =>
    `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
  const periods: Array<{ ja: string; je: string }> = []
  if (grain === 'year') {
    for (let dy = 1; dy >= 0; dy--) {
      const fy = year - dy
      periods.push({ ja: iso(fy, 1, 1), je: iso(fy, 12, 31) })
    }
  } else if (grain === 'quarter') {
    let q = Math.floor((month - 1) / 3) + 1
    let y = year
    for (let i = 0; i < 3; i++) {
      periods.unshift({ ja: iso(y, (q - 1) * 3 + 1, 1), je: lastDay(y, q * 3) })
      q--
      if (q === 0) { q = 4; y-- }
    }
  } else {
    let y = year
    let m = month
    for (let i = 0; i < 3; i++) {
      periods.unshift({ ja: iso(y, m, 1), je: lastDay(y, m) })
      m--
      if (m === 0) { m = 12; y-- }
    }
  }
  return periods
}
