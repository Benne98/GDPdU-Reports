import type { PlLineDetailAccountTimeline, PlLineDetailResponse } from '../../../lib/api'
import { last12PeriodDefs, priorPeriod, type PeriodDef } from './plPeriodLabels'

function periodDefsForDetail(detail: PlLineDetailResponse): PeriodDef[] {
  if (detail.periods?.length) {
    return detail.periods.map(p => ({
      year: p.year,
      month: p.month,
      label: p.label,
      key: p.key,
    }))
  }
  return last12PeriodDefs(detail.year, detail.month)
}

type SeriesPoint = PlLineDetailAccountTimeline['series'][number] & { key?: string }

function normalizeTimelineSeries(
  series: SeriesPoint[],
  periodDefs: PeriodDef[],
): PlLineDetailAccountTimeline['series'] {
  const byLabel = new Map(series.map(p => [p.label, p.value_keur]))
  const byKey = new Map(
    series.filter(p => p.key).map(p => [p.key as string, p.value_keur]),
  )

  const hasLabelMatch = periodDefs.some(def => byLabel.has(def.label))
  const hasKeyMatch = periodDefs.some(def => byKey.has(def.key))

  if (!hasLabelMatch && !hasKeyMatch && series.length === periodDefs.length) {
    return periodDefs.map((def, i) => ({
      label: def.label,
      value_keur: series[i]?.value_keur ?? 0,
    }))
  }

  return periodDefs.map(def => ({
    label: def.label,
    value_keur: byKey.get(def.key) ?? byLabel.get(def.label) ?? 0,
  }))
}

function cmPmOnlySeries(
  acc: PlLineDetailResponse['accounts'][number],
  periodDefs: PeriodDef[],
  year: number,
  month: number,
): PlLineDetailAccountTimeline['series'] {
  const pm = priorPeriod(year, month)
  return periodDefs.map(p => ({
    label: p.label,
    value_keur:
      p.year === year && p.month === month
        ? acc.balance_cm
        : p.year === pm.year && p.month === pm.month
          ? acc.balance_pm
          : 0,
  }))
}

/** Prefer API accounts_timeline; only synthesise CM/PM when the API omits history. */
export function coalesceAccountTimeline(detail: PlLineDetailResponse | null): PlLineDetailAccountTimeline[] {
  if (!detail) return []

  const periodDefs = periodDefsForDetail(detail)
  const { year, month } = detail
  const accounts = detail.accounts ?? []
  const apiTimeline = detail.accounts_timeline ?? []

  if (apiTimeline.length > 0) {
    const byId = new Map(apiTimeline.map(t => [String(t.gl_account_id), t]))
    const order =
      accounts.length > 0
        ? accounts.map(a => String(a.gl_account_id))
        : apiTimeline.map(t => String(t.gl_account_id))

    return order.map(gid => {
      const existing = byId.get(gid)
      if (existing?.series?.length) {
        return {
          gl_account_id: gid,
          account_name: existing.account_name,
          series: normalizeTimelineSeries(existing.series, periodDefs),
        }
      }
      const acc = accounts.find(a => String(a.gl_account_id) === gid)
      if (!acc) return null
      return {
        gl_account_id: gid,
        account_name: acc.account_name,
        series: cmPmOnlySeries(acc, periodDefs, year, month),
      }
    }).filter((t): t is PlLineDetailAccountTimeline => t != null)
  }

  if (!accounts.length) return []

  return accounts.map(acc => ({
    gl_account_id: String(acc.gl_account_id),
    account_name: acc.account_name,
    series: cmPmOnlySeries(acc, periodDefs, year, month),
  }))
}

/** True when API returned no monthly history (stale backend or empty scope). */
export function isTimelineHistoryMissing(detail: PlLineDetailResponse | null): boolean {
  if (!detail?.accounts?.length) return false
  return !(detail.accounts_timeline?.length)
}

export type TimelineEmptyReason = 'loading' | 'no_accounts' | 'no_data' | 'history_missing' | null

export function timelineEmptyReason(
  detail: PlLineDetailResponse | null,
  loading: boolean,
  error?: boolean,
): TimelineEmptyReason {
  if (loading) return 'loading'
  if (error) return 'no_data'
  if (!detail) return 'no_data'
  const timeline = coalesceAccountTimeline(detail)
  if (timeline.length) {
    if (isTimelineHistoryMissing(detail)) return 'history_missing'
    return null
  }
  if (!detail.accounts?.length && !detail.top_bookings?.length) return 'no_accounts'
  return 'no_data'
}

export type AccountWithDelta = {
  gl_account_id: string
  delta?: number
}

export function accountDeltaMap(
  accounts: Array<{ gl_account_id: string; delta?: number }> | undefined,
): Map<string, number> {
  const m = new Map<string, number>()
  for (const a of accounts ?? []) {
    m.set(String(a.gl_account_id), a.delta ?? 0)
  }
  return m
}

/** Largest absolute MoM delta first (same order as account drivers chart). */
export function sortByAbsDelta<T extends AccountWithDelta>(
  items: T[],
  deltas?: Map<string, number>,
): T[] {
  const map = deltas ?? accountDeltaMap(items as Array<{ gl_account_id: string; delta?: number }>)
  return [...items].sort(
    (a, b) =>
      Math.abs(map.get(String(b.gl_account_id)) ?? 0)
      - Math.abs(map.get(String(a.gl_account_id)) ?? 0),
  )
}

export function timelineEmptyMessage(reason: TimelineEmptyReason): string {
  switch (reason) {
    case 'loading':
      return 'Loading account history…'
    case 'no_accounts':
      return 'No GL accounts in scope for this line and period.'
    case 'history_missing':
      return 'Only current and prior month are shown. Restart the API server to load full 12-month history.'
    case 'no_data':
      return 'Account history could not be loaded for this scope.'
    default:
      return ''
  }
}
