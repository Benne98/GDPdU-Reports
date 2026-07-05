/**
 * useOverviewBundleV2 — Item 1 / Lever C: ONE batched fetch for the Overview page.
 *
 * Fetches GET /api/v1/financials/overview/bundle ONE time per period+entity and
 * exposes the summary / partners / liquidity slices as the SAME typed states the
 * three granular hooks return — collapsing 3 client round-trips into 1.
 *
 * SCOPE: reporting-v2 / port 5177 only — tree-shaken from fdd-merge (5176) and
 * v4 (5178) bundles via the IS_OVERVIEW_V2 mode gate in OverviewPage.tsx.
 * See docs/overview-v2-improvements-plan.md §5.2 Lever C.
 *
 * BYTE-IDENTICAL RULE: this module does NOT add a method to
 * frontend/src/lib/api.ts (imported by ALL modes → bundle bloat).
 * The fetch is self-contained here, using the same Bearer-token / timeout /
 * 401-redirect pattern as useOverviewSummaryV2.ts (P3).
 */

import { useEffect, useState } from 'react'
import type { OverviewSummaryData, OverviewSummaryState } from './useOverviewSummaryV2'
import type { OverviewPartnersData, OverviewPartnersState } from './useOverviewPartnersV2'
import type { OverviewLiquidityData, OverviewLiquidityState } from './useOverviewLiquidityV2'

// ---------------------------------------------------------------------------
// Token storage keys — must match api.ts + AuthContext.tsx
// ---------------------------------------------------------------------------

const TOKEN_KEY = 'gdpdu_access_token'
const USER_KEY  = 'gdpdu_user'

/** Bundle endpoint timeout: 60 s (matches api.ts API_DEFAULT_TIMEOUT_MS). */
const BUNDLE_TIMEOUT_MS = 60_000

// ---------------------------------------------------------------------------
// Types — mirror GET /api/v1/financials/overview/bundle response shape
// ---------------------------------------------------------------------------

/** Full typed payload of GET /api/v1/financials/overview/bundle. */
export interface OverviewBundleData {
  summary:   OverviewSummaryData
  partners:  OverviewPartnersData
  liquidity: OverviewLiquidityData
}

/**
 * Bundle hook return — one fetch, three slices shaped IDENTICALLY to the
 * granular hooks' states so callers swap in without changing usage.
 */
export interface OverviewBundleState {
  summary:   OverviewSummaryState
  partners:  OverviewPartnersState
  liquidity: OverviewLiquidityState
}

// ---------------------------------------------------------------------------
// Inline fetch helper — Bearer + timeout + 401-redirect (matches api.ts)
// ---------------------------------------------------------------------------

async function apiFetchBundle(
  year:   number,
  month:  number,
  entity: string | undefined,
  topN:   number | undefined,
  signal: AbortSignal,
): Promise<OverviewBundleData> {
  const token = localStorage.getItem(TOKEN_KEY)
  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const qs = new URLSearchParams()
  qs.set('year',  String(year))
  qs.set('month', String(month))
  if (entity) qs.set('entity', entity)
  if (topN != null) qs.set('top_n', String(topN))

  const res = await fetch(
    `/api/v1/financials/overview/bundle?${qs.toString()}`,
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

  return res.json() as Promise<OverviewBundleData>
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Fetch the batched overview bundle (summary + partners + liquidity) in ONE call.
 * Re-fires on year/month/entity/topN change. Exposes three slices whose
 * {data, loading, error} shapes match the granular hooks exactly.
 *
 * @param year    Anchor year from periodAnchorYearMonth()
 * @param month   Anchor month (1–12)
 * @param entity  Entity code or undefined for all entities
 * @param topN    Max partners per sub-list (backend default applies when omitted)
 * @param skip    When true, suppress the fetch (e.g. period state not yet ready)
 */
export function useOverviewBundleV2(
  year:   number,
  month:  number,
  entity: string | undefined,
  topN?:  number,
  skip = false,
): OverviewBundleState {
  const [data,    setData]    = useState<OverviewBundleData | null>(null)
  const [loading, setLoading] = useState(!skip)
  const [error,   setError]   = useState<string | null>(null)

  useEffect(() => {
    if (skip) {
      setLoading(false)
      return
    }

    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), BUNDLE_TIMEOUT_MS)

    setLoading(true)
    setError(null)
    setData(null)

    apiFetchBundle(year, month, entity, topN, controller.signal)
      .then((res) => {
        if (!controller.signal.aborted) {
          setData(res)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setError(
          err instanceof Error ? err.message : 'Failed to load overview bundle',
        )
        setLoading(false)
      })

    return () => {
      controller.abort()
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, month, entity, topN, skip])

  // Fan the single fetch out into three granular-hook-shaped slices so the page
  // can consume bundle.summary / bundle.partners / bundle.liquidity unchanged.
  return {
    summary:   { data: data?.summary   ?? null, loading, error },
    partners:  { data: data?.partners  ?? null, loading, error },
    liquidity: { data: data?.liquidity ?? null, loading, error },
  }
}
