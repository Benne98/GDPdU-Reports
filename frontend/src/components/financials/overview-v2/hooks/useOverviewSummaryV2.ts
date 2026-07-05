/**
 * useOverviewSummaryV2 — P3 batched fetch for the redesigned Overview page.
 *
 * Fetches GET /api/v1/financials/overview/summary ONE time per period+entity,
 * replacing ~6 fan-out client calls with a single round-trip.
 *
 * SCOPE: reporting-v2 / port 5177 only — tree-shaken from fdd-merge (5176) and
 * v4 (5178) bundles via the IS_OVERVIEW_V2 mode gate in OverviewPage.tsx.
 * See docs/overview-v2-redesign-plan.md §4 §6.
 *
 * BYTE-IDENTICAL RULE: this module does NOT add a method to
 * frontend/src/lib/api.ts (imported by ALL modes → bundle bloat).
 * The fetch is self-contained here, using the same Bearer-token / timeout /
 * 401-redirect pattern as api.ts.
 */

import { useEffect, useState } from 'react'
import type { DuPontData } from '../../../../lib/api'

// ---------------------------------------------------------------------------
// Token keys — must match api.ts + AuthContext.tsx
// ---------------------------------------------------------------------------

const TOKEN_KEY = 'gdpdu_access_token'
const USER_KEY  = 'gdpdu_user'

/** Summary endpoint timeout: 60 s (matches api.ts API_DEFAULT_TIMEOUT_MS). */
const SUMMARY_TIMEOUT_MS = 60_000

// ---------------------------------------------------------------------------
// Types — mirror OverviewSummaryResponse (financials_compat.py ~L1309-L1377)
// ---------------------------------------------------------------------------

export interface OverviewSummaryRevenue {
  cm:      number
  cm_py:   number
  ytd:     number
  ytd_py:  number | null
  yoy_pct: number | null
}

export interface OverviewSummaryEbit {
  cm:         number
  ytd:        number
  margin_pct: number | null
}

export interface OverviewSummaryCash {
  level:       number
  delta_month: number
  delta_yoy:   number
}

export interface OverviewSummaryWcHero {
  ccc: number
  nwc: number
}

export interface OverviewSummaryHero {
  revenue:         OverviewSummaryRevenue
  ebit:            OverviewSummaryEbit
  cash:            OverviewSummaryCash
  working_capital: OverviewSummaryWcHero
}

/**
 * One deep-dive level row from the WC block.
 * Keys: 'inventories' | 'trade_receivables' | 'trade_payables'.
 * Amounts are raw EUR (same unit as the GL cumulative balance).
 * Ratios (delta_month / delta_fy) are also in EUR.
 */
export interface OverviewSummaryWcLevel {
  key:         string
  label:       string
  level:       number
  delta_month: number
  delta_fy:    number
}

export interface OverviewSummaryWc {
  dso:    number
  dpo:    number
  dio:    number
  ccc:    number
  nwc:    number
  /**
   * Deep-dive rows (inventories / trade_receivables / trade_payables).
   * Each carries Δmonth (cm − pm) AND Δfy (cm − py_cm) — Δfy was not available
   * in P2 (see TODO(P3) in WorkingCapitalBlock.tsx, now resolved).
   */
  levels: OverviewSummaryWcLevel[]
}

export interface OverviewSummaryTopEntity {
  name:         string | null
  rank:         number | null
  cm:           number | null
  delta_cm_py:  number | null
  delta_ytd:    number | null
}

export interface OverviewSummaryPerformanceRevenue {
  cm:              number
  cm_py:           number
  yoy_pct:         number | null
  has_plan:        boolean
  plan_cm:         number | null
  plan_vs_actual:  number | null
  var_pct:         number | null
  coverage_pct:    number | null
}

export interface OverviewSummaryPerformanceGrossMargin {
  pct:                 number | null
  yoy_pp:              number | null
  plan_pct:            number | null
  plan_vs_actual_pp:   number | null
}

export interface OverviewSummaryPerformanceEbit {
  cm:         number
  margin_pct: number | null
}

export interface OverviewSummaryPerformance {
  revenue:      OverviewSummaryPerformanceRevenue
  gross_margin: OverviewSummaryPerformanceGrossMargin
  ebit:         OverviewSummaryPerformanceEbit
}

/** One recent-month alert from build_recent_month_alerts. */
export interface OverviewSummaryAlert {
  entity:         string
  metric:         'revenue' | 'gross_margin' | string
  year:           number
  month:          number
  label?:         string
  value?:         number
  severity:       number
  direction:      'up' | 'down' | 'flat' | string
  triggers?:      string[]
  recency_idx?:   number
  reference?:     number
  reference_kind?: string
}

/**
 * Full typed payload of GET /api/v1/financials/overview/summary.
 *
 * Financial amounts (hero.revenue.*, hero.ebit.*, cash.*, working_capital.nwc,
 * working_capital.levels[].level/delta_*) are in raw EUR (GL cumulative sums).
 * Ratio/margin fields (dso/dpo/dio/ccc/yoy_pct/margin_pct) are in native units
 * (days / %).
 * Top-entity amounts (top_customer.cm / delta_*) are in kEUR
 * (from build_top_entities which divides by 1000).
 */
export interface OverviewSummaryData {
  meta:            Record<string, unknown>
  hero:            OverviewSummaryHero
  working_capital: OverviewSummaryWc
  cash:            OverviewSummaryCash
  top_customer:    OverviewSummaryTopEntity | null
  top_supplier:    OverviewSummaryTopEntity | null
  /**
   * DuPont finding inputs — same shape as DuPontData (api.ts).
   * build_dupont() returns the inner DuPontData dict directly (no outer
   * { metric, data } wrapper); pass directly to buildDuPontFindings().
   */
  dupont:          DuPontData | null
  /** Performance YoY / vs-Plan block inputs (Area 1). */
  performance:     OverviewSummaryPerformance
  /** Recent-month exception alerts from build_recent_month_alerts. */
  alerts:          OverviewSummaryAlert[]
}

// ---------------------------------------------------------------------------
// Hook state
// ---------------------------------------------------------------------------

export interface OverviewSummaryState {
  data:    OverviewSummaryData | null
  loading: boolean
  error:   string | null
}

// ---------------------------------------------------------------------------
// Inline fetch helper — Bearer + timeout + 401-redirect (matches api.ts)
// ---------------------------------------------------------------------------

async function apiFetchSummary(
  year:   number,
  month:  number,
  entity: string | undefined,
  signal: AbortSignal,
): Promise<OverviewSummaryData> {
  const token = localStorage.getItem(TOKEN_KEY)
  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const qs = new URLSearchParams()
  qs.set('year',  String(year))
  qs.set('month', String(month))
  if (entity) qs.set('entity', entity)

  const res = await fetch(
    `/api/v1/financials/overview/summary?${qs.toString()}`,
    { headers, signal },
  )

  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    window.location.href = '/login'
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    let msg = `API ${res.status}`
    try {
      const body = await res.json() as { detail?: unknown }
      if (typeof body.detail === 'string') msg = body.detail
    } catch { /* keep msg */ }
    throw new Error(msg)
  }

  return res.json() as Promise<OverviewSummaryData>
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Fetch the batched overview summary. Re-fires on year/month/entity change.
 *
 * @param year    Anchor year from periodAnchorYearMonth()
 * @param month   Anchor month (1–12)
 * @param entity  Entity code or undefined for all entities
 * @param skip    When true, suppress the fetch (e.g. period state not yet ready)
 */
export function useOverviewSummaryV2(
  year:   number,
  month:  number,
  entity: string | undefined,
  skip = false,
): OverviewSummaryState {
  const [data,    setData]    = useState<OverviewSummaryData | null>(null)
  const [loading, setLoading] = useState(!skip)
  const [error,   setError]   = useState<string | null>(null)

  useEffect(() => {
    if (skip) {
      setLoading(false)
      return
    }

    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), SUMMARY_TIMEOUT_MS)

    setLoading(true)
    setError(null)
    setData(null)

    apiFetchSummary(year, month, entity, controller.signal)
      .then((res) => {
        if (!controller.signal.aborted) {
          setData(res)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setError(
          err instanceof Error ? err.message : 'Failed to load overview summary',
        )
        setLoading(false)
      })

    return () => {
      controller.abort()
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, month, entity, skip])

  return { data, loading, error }
}
