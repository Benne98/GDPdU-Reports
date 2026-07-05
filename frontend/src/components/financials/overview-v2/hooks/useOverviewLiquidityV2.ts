/**
 * useOverviewLiquidityV2 — P5 fetch for Cash development & liquidity block.
 *
 * Fetches GET /api/v1/financials/overview/liquidity ONE time per period+entity,
 * feeding CashLiquidityBlock from a single round-trip.
 *
 * SCOPE: reporting-v2 / port 5177 only — tree-shaken from fdd-merge (5176) and
 * v4 (5178) bundles via the IS_OVERVIEW_V2 mode gate in OverviewPage.tsx.
 * See docs/overview-v2-redesign-plan.md §4 §6 P5.
 *
 * BYTE-IDENTICAL RULE: this module does NOT add a method to
 * frontend/src/lib/api.ts (imported by ALL modes → bundle bloat).
 * The fetch is self-contained here, using the same Bearer-token / timeout /
 * 401-redirect pattern as useOverviewSummaryV2.ts (P3) / useOverviewPartnersV2.ts (P4).
 */

import { useEffect, useState } from 'react'

// ---------------------------------------------------------------------------
// Token storage keys — must match api.ts + AuthContext.tsx
// ---------------------------------------------------------------------------

const TOKEN_KEY = 'gdpdu_access_token'
const USER_KEY  = 'gdpdu_user'

/** Liquidity endpoint timeout: 60 s (matches api.ts API_DEFAULT_TIMEOUT_MS). */
const LIQUIDITY_TIMEOUT_MS = 60_000

// ---------------------------------------------------------------------------
// Types — mirror GET /api/v1/financials/overview/liquidity response shape
// ---------------------------------------------------------------------------

/** One aging-band entry in the AR breakdown. All monetary fields in kEUR. */
export interface LiquidityArBand {
  /** Machine key, e.g. 'not_yet_due', '1_30', '31_60', '61_90', '91_180', 'over_180'. */
  band: string
  /** Human-readable label, e.g. 'Not yet due', '1–30 d'. */
  label: string
  /** Raw AR amount for this band (kEUR, non-negative). */
  raw: number
  /** Haircut factor in [0..1]; 0 = fully collectible, 1 = written off. */
  haircut: number
  /** Net collectible = raw * (1 − haircut) (kEUR). */
  collectible: number
  /** True if this band carries a net credit balance (unusual; flagged subtly). */
  credit_flag: boolean
}

/** Meta block returned by the liquidity endpoint. */
export interface LiquidityMeta {
  year:   number
  month:  number
  entity: string | null
  unit:   'kEUR'
  /** Per-metric favorability strings, e.g. { cash: 'FAV+', liquidity_available: 'FAV+' }. */
  fav:    { cash: string; liquidity_available: string }
}

/**
 * Full typed payload of GET /api/v1/financials/overview/liquidity.
 * All monetary amounts are in kEUR (unit confirmed in meta.unit).
 *
 * Liquidity bridge identity: liquidity_available = cash + collectible_ar − outstanding_ap
 */
export interface OverviewLiquidityData {
  meta:                LiquidityMeta
  /** Period-end cash balance (kEUR, signed — overdraft is negative; do NOT abs). */
  cash:                number
  ar_bands:            LiquidityArBand[]
  /** Sum of raw AR across all bands (kEUR). */
  raw_ar:              number
  /** Sum of collectible AR across all bands after aging haircuts (kEUR). */
  collectible_ar:      number
  /** Total outstanding AP (kEUR, non-negative). */
  outstanding_ap:      number
  /** = cash + collectible_ar − outstanding_ap (kEUR, signed). */
  liquidity_available: number
}

// ---------------------------------------------------------------------------
// Hook state
// ---------------------------------------------------------------------------

export interface OverviewLiquidityState {
  data:    OverviewLiquidityData | null
  loading: boolean
  error:   string | null
}

// ---------------------------------------------------------------------------
// Inline fetch helper — Bearer + timeout + 401-redirect (matches api.ts)
// ---------------------------------------------------------------------------

async function apiFetchLiquidity(
  year:   number,
  month:  number,
  entity: string | undefined,
  signal: AbortSignal,
): Promise<OverviewLiquidityData> {
  const token = localStorage.getItem(TOKEN_KEY)
  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const qs = new URLSearchParams()
  qs.set('year',  String(year))
  qs.set('month', String(month))
  if (entity) qs.set('entity', entity)

  const res = await fetch(
    `/api/v1/financials/overview/liquidity?${qs.toString()}`,
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

  return res.json() as Promise<OverviewLiquidityData>
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Fetch the cash & liquidity overview (cash + AR bands + AP + bridge).
 * Re-fires on year/month/entity change.
 *
 * @param year    Anchor year from periodAnchorYearMonth()
 * @param month   Anchor month (1–12)
 * @param entity  Entity code or undefined for all entities
 * @param skip    When true, suppress the fetch (e.g. period state not yet ready)
 */
export function useOverviewLiquidityV2(
  year:   number,
  month:  number,
  entity: string | undefined,
  skip = false,
): OverviewLiquidityState {
  const [data,    setData]    = useState<OverviewLiquidityData | null>(null)
  const [loading, setLoading] = useState(!skip)
  const [error,   setError]   = useState<string | null>(null)

  useEffect(() => {
    if (skip) {
      setLoading(false)
      return
    }

    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), LIQUIDITY_TIMEOUT_MS)

    setLoading(true)
    setError(null)
    setData(null)

    apiFetchLiquidity(year, month, entity, controller.signal)
      .then((res) => {
        if (!controller.signal.aborted) {
          setData(res)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setError(
          err instanceof Error ? err.message : 'Failed to load liquidity data',
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
