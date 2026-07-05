/**
 * useOverviewPartnersV2 — P4 fetch for Customer + Supplier development blocks.
 *
 * Fetches GET /api/v1/financials/overview/partners ONE time per period+entity,
 * feeding both CustomerBlock (data.customers) and SupplierBlock (data.suppliers)
 * from a single round-trip — no per-block fan-out.
 *
 * SCOPE: reporting-v2 / port 5177 only — tree-shaken from fdd-merge (5176) and
 * v4 (5178) bundles via the IS_OVERVIEW_V2 mode gate in OverviewPage.tsx.
 * See docs/overview-v2-redesign-plan.md §4 §6 P4.
 *
 * BYTE-IDENTICAL RULE: this module does NOT add a method to
 * frontend/src/lib/api.ts (imported by ALL modes → bundle bloat).
 * The fetch is self-contained here, using the same Bearer-token / timeout /
 * 401-redirect pattern as useOverviewSummaryV2.ts (P3).
 */

import { useEffect, useState } from 'react'

// ---------------------------------------------------------------------------
// Token storage keys — must match api.ts + AuthContext.tsx
// ---------------------------------------------------------------------------

const TOKEN_KEY = 'gdpdu_access_token'
const USER_KEY  = 'gdpdu_user'

/** Partners endpoint timeout: 60 s (matches api.ts API_DEFAULT_TIMEOUT_MS). */
const PARTNERS_TIMEOUT_MS = 60_000

// ---------------------------------------------------------------------------
// Types — mirror GET /api/v1/financials/overview/partners response shape
// ---------------------------------------------------------------------------

/**
 * Period metadata block emitted by the partners endpoint.
 * Replaces the legacy `period: string` field in both customers and suppliers.
 */
export interface PartnersPeriodMeta {
  year:           number
  month:          number
  cm_label:       string
  ytd_label:      string
  prior_window:   string
  current_window: string
}

/** One entry in the customers.biggest array. All monetary fields in kEUR. */
export interface PartnersCustomerBiggest {
  rank:                  number
  customer_id:           string
  name:                  string | null
  rev_ytd_keur:          number
  rev_cm_keur:           number
  invoice_count:         number
  avg_per_invoice_keur:  number | null
  fav:                   'plus' | 'minus'
}

/** One entry in the customers.increase / customers.won arrays. kEUR. */
export interface PartnersCustomerDelta {
  customer_id:    string
  name:           string | null
  rev_cur_keur:   number
  rev_py_keur:    number
  delta_yoy_keur: number
}

/** One entry in the customers.lost array. kEUR. */
export interface PartnersCustomerLost {
  customer_id:      string
  name:             string | null
  rev_prior_keur:   number
  rev_current_keur: number
  fav:              'plus' | 'minus'
}

/** Full customers sub-object in the partners response. */
export interface PartnersCustomers {
  period:   PartnersPeriodMeta
  id_field: string
  biggest:  PartnersCustomerBiggest[]
  increase: PartnersCustomerDelta[]
  won:      PartnersCustomerDelta[]
  lost:     PartnersCustomerLost[]
}

/** One entry in the suppliers.biggest array. All monetary fields in kEUR. */
export interface PartnersSupplierBiggest {
  rank:                   number
  supplier_id:            string
  name:                   string | null
  cost_ytd_keur:          number
  cost_cm_keur:           number
  purchase_txns:          number
  avg_per_purchase_keur:  number | null
  fav:                    'plus' | 'minus'
}

/**
 * One entry in the suppliers.increase array. kEUR.
 * fav:          precomputed favorability flag — use for coloring, NOT raw delta sign.
 * invert_delta: backend signal that this is a cost metric (FAV−); used internally
 *               by the backend to compute fav. Do NOT recolor by raw sign.
 */
export interface PartnersSupplierDelta {
  supplier_id:    string
  name:           string | null
  cost_cur_keur:  number
  cost_py_keur:   number
  delta_yoy_keur: number
  fav:            'plus' | 'minus'
  invert_delta:   boolean
}

/**
 * One entry in the suppliers.won array (new spend). kEUR.
 * Backend never emits fav or invert_delta on won rows — only kind: "new_spend".
 */
export interface PartnersSupplierWon {
  supplier_id:    string
  name:           string | null
  cost_cur_keur:  number
  cost_py_keur:   number
  delta_yoy_keur: number
  kind:           'new_spend'
}

/** Full suppliers sub-object in the partners response. */
export interface PartnersSuppliers {
  period:   PartnersPeriodMeta
  id_field: string
  biggest:  PartnersSupplierBiggest[]
  increase: PartnersSupplierDelta[]
  won:      PartnersSupplierWon[]
}

/**
 * Full typed payload of GET /api/v1/financials/overview/partners.
 * All monetary amounts are kEUR. Counts are integers.
 */
export interface OverviewPartnersData {
  customers: PartnersCustomers
  suppliers: PartnersSuppliers
}

// ---------------------------------------------------------------------------
// Hook state
// ---------------------------------------------------------------------------

export interface OverviewPartnersState {
  data:    OverviewPartnersData | null
  loading: boolean
  error:   string | null
}

// ---------------------------------------------------------------------------
// Inline fetch helper — Bearer + timeout + 401-redirect (matches api.ts)
// ---------------------------------------------------------------------------

async function apiFetchPartners(
  year:   number,
  month:  number,
  entity: string | undefined,
  topN:   number | undefined,
  signal: AbortSignal,
): Promise<OverviewPartnersData> {
  const token = localStorage.getItem(TOKEN_KEY)
  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const qs = new URLSearchParams()
  qs.set('year',  String(year))
  qs.set('month', String(month))
  if (entity) qs.set('entity', entity)
  if (topN != null) qs.set('top_n', String(topN))

  const res = await fetch(
    `/api/v1/financials/overview/partners?${qs.toString()}`,
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

  return res.json() as Promise<OverviewPartnersData>
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Fetch the partners overview (customers + suppliers). Re-fires on year/month/entity change.
 * ONE fetch feeds both CustomerBlock (via data.customers) and SupplierBlock (via data.suppliers).
 *
 * @param year    Anchor year from periodAnchorYearMonth()
 * @param month   Anchor month (1–12)
 * @param entity  Entity code or undefined for all entities
 * @param topN    Max partners per sub-list (backend default applies when omitted)
 * @param skip    When true, suppress the fetch (e.g. period state not yet ready)
 */
export function useOverviewPartnersV2(
  year:   number,
  month:  number,
  entity: string | undefined,
  topN?:  number,
  skip = false,
): OverviewPartnersState {
  const [data,    setData]    = useState<OverviewPartnersData | null>(null)
  const [loading, setLoading] = useState(!skip)
  const [error,   setError]   = useState<string | null>(null)

  useEffect(() => {
    if (skip) {
      setLoading(false)
      return
    }

    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), PARTNERS_TIMEOUT_MS)

    setLoading(true)
    setError(null)
    setData(null)

    apiFetchPartners(year, month, entity, topN, controller.signal)
      .then((res) => {
        if (!controller.signal.aborted) {
          setData(res)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setError(
          err instanceof Error ? err.message : 'Failed to load partners data',
        )
        setLoading(false)
      })

    return () => {
      controller.abort()
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, month, entity, topN, skip])

  return { data, loading, error }
}
