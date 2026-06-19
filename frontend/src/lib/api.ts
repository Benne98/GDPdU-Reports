import { periodAnchorYearMonth, type PeriodSelection } from './periodSelection'

function _envHostIsLoopback(apiRoot: string): boolean {
  const t = apiRoot.trim()
  if (!t) return false
  try {
    const u = new URL(t.includes('://') ? t : `http://${t}`)
    const h = u.hostname.toLowerCase()
    return h === 'localhost' || h === '127.0.0.1' || h === '[::1]'
  } catch {
    return false
  }
}

/** API origin for fetch(): explicit VITE_API_URL, else same-origin in browser (Docker/Caddy), else localhost for tooling. */
export function getApiBaseUrl(): string {
  const raw = import.meta.env.VITE_API_URL as string | undefined
  const trimmed = raw !== undefined ? String(raw).trim() : ''

  // If `.env` pins the API to localhost but the page is served from another hostname (LAN, custom host, or `vite preview`),
  // the browser would call *that* machine's loopback — wrong. Prefer same-origin so `/api` is proxied or fronted by Caddy/nginx.
  if (typeof window !== 'undefined' && trimmed !== '') {
    const h = window.location.hostname
    const pageIsLoopback = h === 'localhost' || h === '127.0.0.1' || h === '[::1]'
    if (!pageIsLoopback && _envHostIsLoopback(trimmed)) {
      return window.location.origin
    }
  }

  if (trimmed !== '') {
    return trimmed.replace(/\/$/, '')
  }
  // Development: same-origin + Vite `/api` proxy → FastAPI (default target 127.0.0.1:8000 in vite.config.ts).
  // Avoids bypassing the proxy on localhost (port mismatches vs. `npm run dev` / PM2) and matches LAN access via `host: true`.
  if (import.meta.env.DEV && typeof window !== 'undefined') {
    return window.location.origin
  }
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin
  }
  return 'http://localhost:8000'
}

/** @deprecated Prefer getApiBaseUrl() at call time. */
export const BASE_URL =
  typeof window === 'undefined'
    ? 'http://127.0.0.1:8000'
    : getApiBaseUrl()

const DEFAULT_API_TIMEOUT_MS = 60_000
/** Overview aggregates P&L, BS, WC and narratives — allow longer than default. */
const FINANCIALS_OVERVIEW_TIMEOUT_MS = 45_000

function apiTimeoutMs(): number {
  const raw = import.meta.env.VITE_API_TIMEOUT_MS as string | undefined
  if (raw === undefined || raw === '') return DEFAULT_API_TIMEOUT_MS
  const n = Number.parseInt(String(raw), 10)
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_API_TIMEOUT_MS
}

function apiFetchSignal(external?: AbortSignal, timeoutMs?: number): AbortSignal {
  const ms = timeoutMs ?? apiTimeoutMs()
  if (typeof AbortSignal.timeout === 'function') {
    const timeoutSignal = AbortSignal.timeout(ms)
    if (external) {
      return AbortSignal.any([external, timeoutSignal])
    }
    return timeoutSignal
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ms)
  if (external) {
    external.addEventListener('abort', () => controller.abort(), { once: true })
  }
  controller.signal.addEventListener('abort', () => clearTimeout(timer), { once: true })
  return controller.signal
}

function devApiProxyHint(): string {
  if (!import.meta.env.DEV || typeof window === 'undefined') return ''
  const base = getApiBaseUrl()
  if (base !== window.location.origin) return ''
  const target = (import.meta.env.VITE_DEV_API_PROXY as string | undefined)?.trim()
  return target ? ` — Vite-Proxy → ${target.replace(/\/$/, '')}` : ' — Vite-Proxy → http://127.0.0.1:8007'
}

function fetchErrorMessage(err: unknown, base: string): string {
  const hint = devApiProxyHint()
  if (err instanceof DOMException && err.name === 'AbortError') {
    return `API-Timeout — Backend antwortet nicht (${base})${hint}. Ist der Server gestartet?`
  }
  if (err instanceof Error && err.name === 'AbortError') {
    return `API-Timeout — Backend antwortet nicht (${base})${hint}. Ist der Server gestartet?`
  }
  return `Backend nicht erreichbar (${base})${hint} — ist der Server gestartet?`
}

const NARRATIVE_API_TIMEOUT_MS = 15_000

/** GDPdU edition: all endpoints require the JWT issued by /api/v1/auth/login. */
function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const token = typeof localStorage !== 'undefined' ? localStorage.getItem('gdpdu_access_token') : null
  return token ? { ...(extra ?? {}), Authorization: `Bearer ${token}` } : (extra ?? {})
}

function handleUnauthorized(res: Response): void {
  if (res.status === 401 && typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
    localStorage.removeItem('gdpdu_access_token')
    localStorage.removeItem('gdpdu_user')
    window.location.href = '/login'
  }
}

async function get<T>(
  path: string,
  params: Record<string, string | number | boolean | undefined | null> = {},
  options?: { timeoutMs?: number },
): Promise<T> {
  const base = getApiBaseUrl()
  const rel = path.startsWith('/') ? path : `/${path}`
  const url = new URL(rel, base.endsWith('/') ? base.slice(0, -1) : base)
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      url.searchParams.set(k, String(v))
    }
  }
  let res: Response
  try {
    res = await fetch(url.toString(), { headers: authHeaders(), signal: apiFetchSignal(undefined, options?.timeoutMs) })
  } catch (err) {
    throw new Error(fetchErrorMessage(err, base))
  }
  if (!res.ok) {
    handleUnauthorized(res)
    const body = await res.text().catch(() => '')
    throw new Error(`API error ${res.status}: ${body || res.statusText}`)
  }
  return res.json() as T
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const base = getApiBaseUrl()
  const rel = path.startsWith('/') ? path : `/${path}`
  const url = new URL(rel, base.endsWith('/') ? base.slice(0, -1) : base)
  let res: Response
  try {
    res = await fetch(url.toString(), {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
      signal: apiFetchSignal(),
    })
  } catch (err) {
    throw new Error(fetchErrorMessage(err, base))
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API error ${res.status}: ${text || res.statusText}`)
  }
  return res.json() as T
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const base = getApiBaseUrl()
  const rel = path.startsWith('/') ? path : `/${path}`
  const url = new URL(rel, base.endsWith('/') ? base.slice(0, -1) : base)
  let res: Response
  try {
    res = await fetch(url.toString(), {
      method: 'PATCH',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
      signal: apiFetchSignal(),
    })
  } catch (err) {
    throw new Error(fetchErrorMessage(err, base))
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API error ${res.status}: ${text || res.statusText}`)
  }
  return res.json() as T
}

async function del<T>(path: string): Promise<T> {
  const base = getApiBaseUrl()
  const rel = path.startsWith('/') ? path : `/${path}`
  const url = new URL(rel, base.endsWith('/') ? base.slice(0, -1) : base)
  let res: Response
  try {
    res = await fetch(url.toString(), { method: 'DELETE', headers: authHeaders(), signal: apiFetchSignal() })
  } catch (err) {
    throw new Error(fetchErrorMessage(err, base))
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API error ${res.status}: ${text || res.statusText}`)
  }
  return res.json() as T
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const base = getApiBaseUrl()
  const rel = path.startsWith('/') ? path : `/${path}`
  const url = new URL(rel, base.endsWith('/') ? base.slice(0, -1) : base)
  let res: Response
  try {
    res = await fetch(url.toString(), {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
      signal: apiFetchSignal(),
    })
  } catch (err) {
    throw new Error(fetchErrorMessage(err, base))
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API error ${res.status}: ${text || res.statusText}`)
  }
  return res.json() as T
}

async function delVoid(path: string): Promise<void> {
  const base = getApiBaseUrl()
  const rel = path.startsWith('/') ? path : `/${path}`
  const url = new URL(rel, base.endsWith('/') ? base.slice(0, -1) : base)
  let res: Response
  try {
    res = await fetch(url.toString(), { method: 'DELETE', headers: authHeaders(), signal: apiFetchSignal() })
  } catch (err) {
    throw new Error(fetchErrorMessage(err, base))
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API error ${res.status}: ${text || res.statusText}`)
  }
}

// ─── Types ───────────────────────────────────────────────────────────────────

export interface EbitTableRow {
  entity_code: string
  entity_name: string
  to_cm_py:    number
  to_pm:       number
  to_cm:       number
  to_ytd:      number
  ebit_cm_py:  number
  ebit_pm:     number
  ebit_cm:     number
  ebit_ytd:    number
}

export interface EbitTableData {
  year:       number
  month:      number
  col_labels: { cm_py: string; pm: string; cm: string; ytd: string }
  rows:       EbitTableRow[]
}

export interface KpiWithDeltas {
  value:      number
  delta_pm:   number | null
  delta_smly: number | null
}

export interface CockpitMonthData {
  period:            string
  net_sales:         KpiWithDeltas
  cost_of_materials: KpiWithDeltas
  operating_cf:      KpiWithDeltas
  ebitda:            KpiWithDeltas
  ebit:              KpiWithDeltas
}

export interface KpiTotals {
  revenue:       number
  gross_profit:  number
  gross_margin:  number
  ebitda:        number
  ebitda_margin: number
  ebit:          number
  ar_open:       number
  ap_open:       number
  nwc:           number
  dso:           number
  dpo:           number
}

export interface TimeSeries {
  metric: string
  grain:  string
  series: { period: string; value: number }[]
}

export interface AgingBand {
  band:  string
  label: string
  value: number
}

export interface AgingResponse {
  metric: string
  grain:  string
  series: AgingBand[]
}

export interface GlLine {
  booking_line_id:          number
  legal_entity_code:        string
  fiscal_year:              number
  journal_entry_number:     string
  posting_date:             string
  document_date:            string | null
  reference_document_number:string | null
  document_type_code:       string | null
  gl_account_id:            string
  account_name:             string
  level_1:                  string | null
  level_2:                  string | null
  level_3:                  string | null
  level_4:                  string | null
  statement_type:           string | null
  amount_signed:            number
  booking_text:             string | null
  posting_type:             string | null
  customer_id:              string | null
  supplier_id:              string | null
  customer_name:            string | null
  supplier_name:            string | null
}

export interface GlLinesResponse {
  grain:           string
  total_returned:  number
  has_more:        boolean
  next_cursor:     string | null
  offset?:         number | null
  total_count?:    number
  rows:            GlLine[]
}

export interface GlAccountFilterOption {
  gl_account_id:   string
  account_name:    string
  statement_type:  string | null
  level_2:         string | null
  level_3:         string | null
}

export interface GlPlBsItemOption {
  statement_type: string
  level_2:        string
  level_3:        string | null
}

export interface GlLinesFiltersResponse {
  date_min:         string | null
  date_max:         string | null
  fiscal_year:      number | null
  statement_types:  string[]
  accounts:         GlAccountFilterOption[]
  pl_bs_items:      GlPlBsItemOption[]
}

export interface TrialBalanceColumn {
  key:   string
  label: string
  group: 'dim' | 'spacer' | 'summary' | 'monthly'
}

export interface TrialBalanceRow {
  entity:        string
  level_1?:        string | null
  level_2:         string
  level_3:         string
  level_4:         string | null
  account:         string
  gl_account_id:   string
  account_name:    string
  amounts:         Record<string, number>
}

export interface TrialBalanceSheetPayload {
  statement_type: 'PL' | 'BS'
  year:           number
  month:          number
  columns:        TrialBalanceColumn[]
  rows:           TrialBalanceRow[]
  row_count:      number
}

export interface TrialBalanceExportResponse {
  anchor: { year: number; month: number }
  entity: string | null
  pl:     TrialBalanceSheetPayload
  bs:     TrialBalanceSheetPayload
}

export interface Entity {
  legal_entity_code: string
  entity_name:       string
}

export interface WcRatiosPoint {
  period: string
  dso:    number | null
  dpo:    number | null
  dio:    number | null
}

export interface WcRatiosData {
  grain:           string
  subheader:       string
  cur_label:       string
  prev_label:      string
  current_series:  WcRatiosPoint[]
  prev_series:     WcRatiosPoint[]
}

export interface TopCustomerRow {
  name:      string
  rank?:     number
  py_cm:     number
  pm:        number
  cm:        number
  ytd_cm:    number
  ytd_py:    number
  delta_mom: number
  delta_yoy: number
  delta_ytd: number
}

export interface TopCustomerGroup {
  group_name:  string
  group_order: number
  py_cm:       number
  pm:          number
  cm:          number
  ytd_cm:      number
  ytd_py:      number
  delta_mom:   number
  delta_yoy:   number
  delta_ytd:   number
  customers:   TopCustomerRow[]
}

export interface TopCustomerData {
  year:       number
  month:      number
  col_labels: { py_cm: string; pm: string; cm: string; ytd: string; ytd_py: string }
  groups:     TopCustomerGroup[]
  total:      Omit<TopCustomerGroup, 'group_name' | 'group_order' | 'customers'>
}

export interface TopCustomerProfileColumn {
  name: string
  our_sales_keur: number
  company_sales_meur: number | null
  parent_company: string
  cooperation_type: string
  description: string
  sites_text: string
  relationship_label: string
  relationship_years: number | null
  relationship_is_lower_bound: boolean
  dataset_first_year: number | null
  strategic_share_pct: number
  intel_sources: string[]
}

export interface TopCustomerProfileIntelData {
  year: number
  month: number
  /** Inclusive GL revenue rank band (when returned by API). */
  rank_from?: number
  rank_to?: number
  dataset_first_year: number | null
  customers: TopCustomerProfileColumn[]
  strategic_pie: { name: string; value: number }[]
}

/** When the profile-intel API is unavailable, derive profile rows from `top_customers` (sales only). */
function buildTopCustomerProfileIntelFallback(
  tc: TopCustomerData,
  opts: { rankFrom: number; rankTo: number },
): TopCustomerProfileIntelData {
  const { rankFrom, rankTo } = opts
  const groupLabel =
    rankFrom === 1 && rankTo === 5
      ? 'Top 5'
      : rankFrom === 6 && rankTo === 10
        ? 'Top 6-10'
        : null

  let rows: TopCustomerRow[] = []
  if (groupLabel) {
    const g = tc.groups.find((x) => x.group_name === groupLabel)
    rows = [...(g?.customers ?? [])]
      .filter((c) => {
        const r = c.rank ?? 99
        return r >= rankFrom && r <= rankTo
      })
      .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99))
  } else {
    const flat = tc.groups.flatMap((gr) => gr.customers)
    rows = flat
      .filter((c) => {
        const r = c.rank ?? 99
        return r >= rankFrom && r <= rankTo
      })
      .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99))
  }

  const sumYtd = rows.reduce((s, c) => s + c.ytd_cm, 0)

  const customers: TopCustomerProfileColumn[] = rows.map((c) => {
    const ourSalesKeur = Math.round((c.ytd_cm / 1000) * 10) / 10
    const share = sumYtd > 0 ? Math.round((10000 * c.ytd_cm) / sumYtd) / 100 : 0
    return {
      name: c.name,
      our_sales_keur: ourSalesKeur,
      company_sales_meur: null,
      parent_company: '—',
      cooperation_type: '—',
      description: '—',
      sites_text: '—',
      relationship_label: '—',
      relationship_years: null,
      relationship_is_lower_bound: false,
      dataset_first_year: null,
      strategic_share_pct: share,
      intel_sources: [],
    }
  })

  const strategic_pie = rows.map((c) => ({
    name: c.name,
    value: Math.round((c.ytd_cm / 1000) * 10) / 10,
  }))

  return {
    year: tc.year,
    month: tc.month,
    rank_from: rankFrom,
    rank_to: rankTo,
    dataset_first_year: null,
    customers,
    strategic_pie,
  }
}

export type TopCustomerProfilesResult = {
  metric: string
  data: TopCustomerProfileIntelData
  /** True when built from `top_customers` only (no web / relationship intel). */
  degraded: boolean
}

export type TopCustomerProfileRankOpts = { rankFrom: number; rankTo: number }

export interface TopCustomerGroupSalesSeriesMeta {
  key: string
  label: string
}

export interface TopCustomerGroupSalesTimelineData {
  unit: string
  measure: string
  note: string
  mode: 'groups' | 'drill'
  drill_group: string | null
  drill_label: string | null
  periods: string[]
  rows: Array<Record<string, string | number>>
  series: TopCustomerGroupSalesSeriesMeta[]
}

export interface CustomerRevenueNewsSnippet {
  customer_name: string
  title: string
  snippet_text: string
  source_url: string
  source_host?: string | null
  published_at?: string | null
  image_url?: string | null
}

export interface CustomerRevenueNewsExecutiveBrief {
  text?: string | null
  model?: string | null
  error?: string | null
}

export interface CustomerRevenueNewsResponse {
  subject_label: string
  executive_brief: CustomerRevenueNewsExecutiveBrief
  snippets: CustomerRevenueNewsSnippet[]
}

export interface SupplierSpendNewsSnippet {
  supplier_name: string
  title: string
  snippet_text: string
  source_url: string
  source_host?: string | null
  published_at?: string | null
  image_url?: string | null
}

export interface SupplierSpendNewsResponse {
  subject_label: string
  executive_brief: CustomerRevenueNewsExecutiveBrief
  snippets: SupplierSpendNewsSnippet[]
}

async function fetchCustomerRevenueNewsWithFallback(
  year: number,
  month: number,
  entity: string | undefined,
  subject: string | undefined,
): Promise<CustomerRevenueNewsResponse> {
  const q = { year, month, entity, subject }
  const attempts: Array<() => Promise<CustomerRevenueNewsResponse>> = [
    () => get('/api/v1/customer-revenue-news', q),
    () => get('/api/v1/benchmark/customer-revenue-news', q),
  ]
  let last: unknown
  for (const run of attempts) {
    try {
      return await run()
    } catch (e) {
      last = e
      const msg = e instanceof Error ? e.message : String(e)
      if (!/\b404\b/.test(msg) && !/\b422\b/.test(msg)) throw e
    }
  }
  throw last instanceof Error ? last : new Error(String(last))
}

async function fetchSupplierSpendNewsWithFallback(
  year: number,
  month: number,
  entity: string | undefined,
  subject: string | undefined,
): Promise<SupplierSpendNewsResponse> {
  const q = { year, month, entity, subject }
  const attempts: Array<() => Promise<SupplierSpendNewsResponse>> = [
    () => get('/api/v1/supplier-spend-news', q),
    () => get('/api/v1/benchmark/supplier-spend-news', q),
  ]
  let last: unknown
  for (const run of attempts) {
    try {
      return await run()
    } catch (e) {
      last = e
      const msg = e instanceof Error ? e.message : String(e)
      if (!/\b404\b/.test(msg) && !/\b422\b/.test(msg)) throw e
    }
  }
  throw last instanceof Error ? last : new Error(String(last))
}

export type TopCustomerGroupDrillKey = 'top_5' | 'top_6_10' | 'top_11_20'

async function fetchTopSupplierGroupSpendTimelineWithFallback(
  year: number,
  month: number,
  entity: string | undefined,
  opts?: { months?: number; drillGroup?: TopCustomerGroupDrillKey | null },
): Promise<{ metric: string; data: TopCustomerGroupSalesTimelineData }> {
  const q = {
    year,
    month,
    entity,
    months: opts?.months ?? 24,
    drill_group: opts?.drillGroup ?? undefined,
  }
  const attempts: Array<() => Promise<{ metric: string; data: TopCustomerGroupSalesTimelineData }>> = [
    () => get('/api/v1/metrics', { metric: 'top_supplier_group_spend_timeline', ...q }),
    () => get('/api/v1/metrics/top-supplier-group-spend-timeline', q),
    () => get('/api/v1/supplier-group-spend-timeline', q),
    () => get('/api/v1/benchmark/top-supplier-group-spend-timeline', q),
  ]
  let last: unknown
  for (const run of attempts) {
    try {
      return await run()
    } catch (e) {
      last = e
      const msg = e instanceof Error ? e.message : String(e)
      const retry =
        /\b404\b/.test(msg) ||
        /\b422\b/.test(msg) ||
        /unknown metric:\s*top_supplier_group_spend_timeline/i.test(msg) ||
        /Unknown metric:\s*top_supplier_group_spend_timeline/i.test(msg)
      if (!retry) throw e
    }
  }
  throw last instanceof Error ? last : new Error(String(last))
}

async function fetchTopSupplierProfilesWithFallback(
  year: number,
  month: number,
  entity: string | undefined,
  rankOpts: TopCustomerProfileRankOpts,
): Promise<TopCustomerProfilesResult> {
  const { rankFrom, rankTo } = rankOpts
  const rankParams = { rank_from: rankFrom, rank_to: rankTo }
  const attempts: Array<() => Promise<{ metric: string; data: TopCustomerProfileIntelData }>> = [
    () => get('/api/v1/metrics/top-supplier-profiles', { year, month, entity, ...rankParams }),
    () =>
      get('/api/v1/metrics', {
        metric: 'top_supplier_profiles',
        year,
        month,
        entity,
        ...rankParams,
      }),
  ]
  for (const run of attempts) {
    try {
      const r = await run()
      return { metric: r.metric, data: r.data, degraded: false }
    } catch {
      /* try next */
    }
  }
  const ts = await get<{ metric: string; data: TopCustomerData }>('/api/v1/metrics', {
    metric: 'top_suppliers',
    year,
    month,
    entity,
  })
  return {
    metric: 'top_supplier_profiles',
    data: buildTopCustomerProfileIntelFallback(ts.data, { rankFrom, rankTo }),
    degraded: true,
  }
}

async function fetchTopCustomerGroupSalesTimelineWithFallback(
  year: number,
  month: number,
  entity: string | undefined,
  opts?: { months?: number; drillGroup?: TopCustomerGroupDrillKey | null },
): Promise<{ metric: string; data: TopCustomerGroupSalesTimelineData }> {
  const q = {
    year,
    month,
    entity,
    months: opts?.months ?? 24,
    drill_group: opts?.drillGroup ?? undefined,
  }
  // Prefer canonical GET /api/v1/metrics?metric=… first: some gateways only expose that entrypoint; dedicated
  // paths (main.py aliases, /metrics/top-…, benchmark) may 404 on older or minimal reverse-proxy configs.
  const attempts: Array<() => Promise<{ metric: string; data: TopCustomerGroupSalesTimelineData }>> = [
    () => get('/api/v1/metrics', { metric: 'top_customer_group_sales_timeline', ...q }),
    () => get('/api/v1/metrics/top-customer-group-sales-timeline', q),
    () => get('/api/v1/customer-group-sales-timeline', q),
    () => get('/api/v1/benchmark/top-customer-group-sales-timeline', q),
  ]
  let last: unknown
  for (const run of attempts) {
    try {
      return await run()
    } catch (e) {
      last = e
      const msg = e instanceof Error ? e.message : String(e)
      const retry =
        /\b404\b/.test(msg) ||
        /\b422\b/.test(msg) ||
        /unknown metric:\s*top_customer_group_sales_timeline/i.test(msg) ||
        /Unknown metric:\s*top_customer_group_sales_timeline/i.test(msg)
      if (!retry) throw e
    }
  }
  throw last instanceof Error ? last : new Error(String(last))
}

async function fetchTopCustomerProfilesWithFallback(
  year: number,
  month: number,
  entity: string | undefined,
  rankOpts: TopCustomerProfileRankOpts,
): Promise<TopCustomerProfilesResult> {
  const { rankFrom, rankTo } = rankOpts
  const rankParams = { rank_from: rankFrom, rank_to: rankTo }
  const attempts: Array<() => Promise<{ metric: string; data: TopCustomerProfileIntelData }>> = [
    () => get('/api/v1/metrics/top-customer-profiles', { year, month, entity, ...rankParams }),
    () =>
      get('/api/v1/metrics', {
        metric: 'top_customer_profiles',
        year,
        month,
        entity,
        ...rankParams,
      }),
  ]
  for (const run of attempts) {
    try {
      const r = await run()
      return { metric: r.metric, data: r.data, degraded: false }
    } catch {
      /* try next */
    }
  }
  const tc = await get<{ metric: string; data: TopCustomerData }>('/api/v1/metrics', {
    metric: 'top_customers',
    year,
    month,
    entity,
  })
  return {
    metric: 'top_customer_profiles',
    data: buildTopCustomerProfileIntelFallback(tc.data, { rankFrom, rankTo }),
    degraded: true,
  }
}

export interface SplitSegment {
  name:  string
  value: number
  pct:   number
}

export interface SplitChartData {
  chart_type:   string
  period_label: string
  entity:       SplitSegment[]
  country:      SplitSegment[]
  party:        SplitSegment[]
  group:        SplitSegment[]
}

export interface TrendChartPoint {
  label:     string
  current:   number
  previous:  number
  delta_pct: number | null
}

export interface TrendChartData {
  grain:  string
  kpi:    string
  series: TrendChartPoint[]
}

export interface DuPontMetric {
  value: number | null
  py:    number | null
  pm:    number | null
}

export interface DuPontData {
  col_label: string
  py_label:  string
  pm_label:  string
  metrics:   Record<string, DuPontMetric>
}

/** Drill metadata from financials API (snake_case from backend). */
export interface FinancialsDrill {
  statement_type?: string | null
  level_2?:        string | null
  level_3?:        string | null
  level_4?:        string | null
  gl_account_id?:  string | null
  // CF-specific hierarchy fields (used for L4 trend chart, not for GL drill-down)
  cf_l11_1?:       string | null
  cf_l11_2?:       string | null
  cf_l11_3?:       string | null
  cf_mapping?:     string | null
}

export interface FinancialStatementAmounts {
  py_cm:  number
  pm:     number
  cm:     number
  ytd:    number
  ytd_py: number
  plan_cm?: number
  plan_vs_actual?: number
  mtd?: number
  mtg?: number
  mtd_plan?: number
  coverage_mtd?: number
}

export interface FinancialStatementDeltas {
  mom: number
  yoy: number
  ytd: number
}

export interface FinancialStatementRow {
  id:            string
  line_code:     string
  row_kind:      'line' | 'subtotal' | 'title' | 'kpi' | 'detail' | 'account'
  label:         string
  amounts:       FinancialStatementAmounts | null
  deltas:        FinancialStatementDeltas | null
  invert_delta:  boolean
  is_bold?:      boolean
  drill:         FinancialsDrill | null
  has_children?: boolean
  children:      FinancialStatementRow[]
  accounts?:     FinancialStatementRow[]
}

export interface FinancialStatementColLabels {
  py_cm:  string
  pm:     string
  cm:     string
  ytd:    string
  ytd_py: string
  plan_cm?: string
  mtd?: string
  mtg?: string
}

export interface FinancialStatementResponse {
  statement:     'pl' | 'bs' | 'cf' | 'wc'
  period_grain?: 'month' | 'week' | 'year'
  year:          number
  month:         number
  iso_year?:     number
  iso_week?:     number
  col_labels:    FinancialStatementColLabels
  rows:          FinancialStatementRow[]
  /** P&L only — plan/budget lines bundled with statement for reliable display */
  plan?:         PlPlanResponse
}

export type FinPeriodParams =
  | { period_grain?: 'month'; year: number; month: number; entity?: string }
  | { period_grain: 'week'; iso_year: number; iso_week: number; entity?: string }
  | { period_grain: 'year'; year: number; month: number; entity?: string }

/** Narrative/statement period from loaded financial statement payload (or explicit period selection). */
export function finPeriodParamsFromStatement(
  data: Pick<FinancialStatementResponse, 'period_grain' | 'year' | 'month' | 'iso_year' | 'iso_week'>,
  entity?: string,
  period?: PeriodSelection,
): FinPeriodParams {
  if (period?.grain === 'week') {
    return {
      period_grain: 'week',
      iso_year: period.isoYear,
      iso_week: period.isoWeek,
      entity,
    }
  }
  if (period?.grain === 'year') {
    return { period_grain: 'year', year: period.year, month: period.month, entity }
  }
  if (data.period_grain === 'week' && data.iso_year != null && data.iso_week != null) {
    return {
      period_grain: 'week',
      iso_year: data.iso_year,
      iso_week: data.iso_week,
      entity,
    }
  }
  return { period_grain: 'month', year: data.year, month: data.month, entity }
}

export function finPeriodQuery(p: FinPeriodParams): Record<string, string | number | undefined> {
  if (p.period_grain === 'week') {
    const anchor = periodAnchorYearMonth({
      grain: 'week',
      isoYear: p.iso_year,
      isoWeek: p.iso_week,
    })
    return {
      period_grain: 'week',
      iso_year: p.iso_year,
      iso_week: p.iso_week,
      year: anchor.year,
      month: anchor.month,
      entity: p.entity,
    }
  }
  if (p.period_grain === 'year') {
    return { period_grain: 'year', year: p.year, month: p.month, entity: p.entity }
  }
  return { period_grain: 'month', year: p.year, month: p.month, entity: p.entity }
}

export type OverviewRowKind = 'line' | 'kpi' | 'section_header' | 'kpi_header'
export type OverviewUnit = 'keur' | 'pct' | 'ratio' | 'none'

export interface OverviewMetricRow {
  id: string
  label: string
  row_kind: OverviewRowKind
  unit: OverviewUnit
  amounts: Partial<Record<'py_cm' | 'pm' | 'cm' | 'ytd' | 'ytd_py' | 'plan_cm' | 'plan_vs_actual' | 'mtd' | 'mtg', number>>
  deltas: Partial<Record<'mom' | 'yoy' | 'ytd', number>>
  invert_delta?: boolean
}

export interface OverviewHighlightBullet {
  index: number
  text: string
}

export interface OverviewHighlight {
  tab: 'pl' | 'bs' | 'cf' | 'wc'
  tab_label: string
  intro?: string | null
  bullets: OverviewHighlightBullet[]
}

export interface FinancialsOverviewSection {
  id: string
  title: string
  rows: OverviewMetricRow[]
}

export interface OverviewEntitySnapshot {
  code: string
  name: string
  net_profit_cm: number
  net_profit_mom: number
  ebitda_cm: number
}

export interface FinancialsOverviewResponse {
  statement: 'overview'
  period_grain?: 'month' | 'week' | 'year'
  year: number
  month: number
  iso_year?: number
  iso_week?: number
  col_labels: FinancialStatementColLabels
  intro: string
  entity_snapshots?: OverviewEntitySnapshot[]
  sections: FinancialsOverviewSection[]
  highlights: OverviewHighlight[]
}

export interface EntityBreakdownEntity {
  code: string
  name: string
  display_name: string
}

export interface EntityBreakdownRow {
  id: string
  label: string
  row_kind: OverviewRowKind
  unit: OverviewUnit
  cm_by_entity: Record<string, number | null | undefined>
}

export interface EntityBreakdownBullet {
  index: number
  entity_code: string
  entity_name: string
  text: string
}

export interface EntityBreakdownArea {
  id: string
  title: string
  tab: 'pl' | 'bs' | 'cf' | 'wc'
  intro: string
  bullets: EntityBreakdownBullet[]
}

export interface FinancialsEntityBreakdownResponse {
  cm_label: string
  entities: EntityBreakdownEntity[]
  rows: EntityBreakdownRow[]
  areas: EntityBreakdownArea[]
}

export interface PlPlanLine {
  line_code: string
  plan_cm: number
  plan_vs_actual: number
  ytd_plan: number
  ytg: number
  coverage_pct: number | null
}

export interface PlPlanResponse {
  year: number
  month: number
  entity?: string | null
  has_plan_data: boolean
  lines: PlPlanLine[]
}

export type PlNarrativeTone = 'positive' | 'negative' | 'mixed' | 'neutral'

export interface PlNarrativeDeepLink {
  route: string
  label: string
  snippet_id?: string
}

export interface PlNarrativeBullet {
  index: number
  line_code: string
  label: string
  text: string
  tone: PlNarrativeTone
  deep_links?: PlNarrativeDeepLink[]
  facts?: Record<string, unknown>
}

export interface PlIntroFacts {
  period_label: string
  group_label: string
  net_profit_ytd: number
  coverage_pct: number | null
  cm_month_label: string
  cm_vs_plan: number
  cm_vs_plan_qualifier: string
  primary_drivers: Array<{ label: string; delta: number; direction: string }>
  entity_split?: Array<Record<string, unknown>>
}

export interface PlNarrativeResponse {
  headline: string
  intro: string
  intro_facts: PlIntroFacts
  bullets: PlNarrativeBullet[]
  entity_split?: string | null
  meta: {
    algorithm_version: string
    llm_used: boolean
    max_bullets?: number
    cache_hit?: boolean
    generated_at?: string | null
    /** '' = consolidated; otherwise legal_entity_code */
    entity_scope?: string
  }
}

export interface ProvisionRollforwardRow {
  legal_entity_code?: string
  level_3: string
  level_4?: string
  opening_balance: number
  additions: number
  releases: number
  usage?: number
  reversals?: number
  fx_adjustment: number
  closing_balance: number
  period_movement?: number
}

export interface ProvisionRollforwardResponse {
  year: number
  month: number
  entity: string
  rows: ProvisionRollforwardRow[]
  totals: {
    opening_balance: number
    closing_balance: number
    net_movement: number
  }
  source: string
}

export interface PlLineDetailPeriod {
  year: number
  month: number
  label: string
  key: string
}

export interface PlLineDetailAccountTimeline {
  gl_account_id: string
  account_name: string
  series: Array<{ label: string; value_keur: number }>
}

export interface PlLineDetailResponse {
  line_code: string
  label: string
  year: number
  month: number
  entity?: string | null
  accounts: Array<{
    gl_account_id: string
    account_name: string
    balance_cm: number
    balance_pm: number
    delta: number
  }>
  top_bookings: Array<{
    booking_line_id: number
    posting_date: string
    journal_entry_number?: string | null
    legal_entity_code?: string | null
    fiscal_year?: number | null
    gl_account_id: string
    account_name?: string | null
    amount: number
    line_note: string
    reference: string
    counter_gl_account_id?: string | null
    counter_account_name?: string | null
  }>
  bridge: Array<{ label: string; value: number }>
  periods?: PlLineDetailPeriod[]
  accounts_timeline?: PlLineDetailAccountTimeline[]
  sub_lines?: Array<{ parent_line_code: string; level_4: string; label: string }>
  commentary?: { accounts: string; postings: string }
  outlier_facts?: Record<string, unknown>
  suggested_prompts?: string[]
  meta?: { llm_used?: boolean }
}

// ─── Exit Readiness types ─────────────────────────────────────────────────────

/** Flow statements (P&L, CF): 3 full FYs + YTD/LTM for current and prior year */
export interface ErFlowAmounts {
  fy1:    number
  fy2:    number
  fy3:    number
  ytd:    number
  ltm:    number
  ytd_py: number
  ltm_py: number
  fy_f?:  number
}

export interface ErFlowDeltas {
  delta_fy:  number
  delta_ytd: number
  delta_ltm: number
}

export interface ErFlowColLabels {
  fy1?:    string
  fy2:     string
  fy3:     string
  ytd:     string
  ltm:     string
  ytd_py:  string
  ltm_py:  string
  fy_f?:   string
  plan_cm?: string
}

/** Snapshot statements (BS, WC): Dec year-3 | Dec year-2 | Dec year-1 | month PY | month */
export interface ErSnapshotAmounts {
  dec_py2?: number
  fy_py:  number
  fy:     number
  cm_py:  number
  cm:     number
}

export interface ErSnapshotDeltas {
  delta_fy: number
  delta_cm: number
  delta_f?: number
}

export interface ErSnapshotColLabels {
  dec_py2?: string
  fy_py: string
  fy:    string
  cm_py: string
  fy_f?: string
  cm:    string
}

/** Shared row shape — amounts/deltas vary by statement type */
export interface ErStatementRow {
  id:            string
  line_code:     string
  row_kind:      'line' | 'subtotal' | 'title' | 'kpi' | 'kpi_header' | 'detail' | 'account'
  label:         string
  amounts:       Record<string, number> | null
  deltas:        Record<string, number> | null
  invert_delta:  boolean
  is_bold?:      boolean
  drill:         FinancialsDrill | null
  has_children?: boolean
  children:      ErStatementRow[]
  accounts?:     ErStatementRow[]
}

export interface ErFlowResponse {
  statement:  string
  year:       number
  month:      number
  col_labels: ErFlowColLabels
  rows:       ErStatementRow[]
}

export interface ErSnapshotResponse {
  statement:  string
  year:       number
  month:      number
  col_labels: ErSnapshotColLabels
  rows:       ErStatementRow[]
}

// ─── Sales ────────────────────────────────────────────────────────────────────
export interface SalesKpiValue {
  value: number
  delta: number
}

export interface SalesKpiCards {
  avg_arr: SalesKpiValue
  nrr:     SalesKpiValue
  churn:   SalesKpiValue
  clv:     SalesKpiValue
  /** Present when backend embeds profitability strip on /kpi-cards */
  headline_kpis?: SalesHeadlineKpis
}

export interface SalesHeadlineKpiBlock {
  value: number
  delta_pm?: number
  delta_smly?: number
  delta_pw?: number
}

export interface SalesHeadlineKpis {
  anchor_period: string
  last_closed_week: string
  period_grain?: 'month' | 'week' | 'year'
  month_revenue: SalesHeadlineKpiBlock
  month_gross_profit: SalesHeadlineKpiBlock
  week_revenue: SalesHeadlineKpiBlock
  week_gross_profit: SalesHeadlineKpiBlock
  avg_revenue_per_customer: SalesHeadlineKpiBlock
  week_avg_revenue_per_customer?: SalesHeadlineKpiBlock
  churn_rate: SalesHeadlineKpiBlock
  week_churn_rate?: SalesHeadlineKpiBlock
  customer_count: SalesHeadlineKpiBlock
  week_customer_count?: SalesHeadlineKpiBlock
  units_sold: SalesHeadlineKpiBlock
  week_units_sold?: SalesHeadlineKpiBlock
  gross_margin_pct: SalesHeadlineKpiBlock
  week_gross_margin_pct?: SalesHeadlineKpiBlock
  avg_order_value: SalesHeadlineKpiBlock
  week_avg_order_value?: SalesHeadlineKpiBlock
  net_revenue_retention: SalesHeadlineKpiBlock
}

export interface SalesOverviewNarrativeInsight {
  title: string
  body: string
  tone: 'positive' | 'info' | 'watch' | 'risk'
}

export interface SalesOverviewNarrative {
  year: number
  month: number
  entity: string
  eyebrow: string
  headline: string
  summary: string
  insights: SalesOverviewNarrativeInsight[]
  ai_enhanced: boolean
  facts?: Record<string, unknown>
}

export interface SalesTopOrder {
  invoice_number: string
  amount_keur:    number
  customer_name:  string
  invoice_date:   string
  customer_id?:   number
  product_count?: number
  contact_name?:  string
  customer_location?: string
  due_date?: string
  gross_profit_keur?: number
  gross_margin_pct?: number
  entity?: string
  segment?: string
  product_revenue_model?: string
  line_count?: number
  supplier_invoice_number?: string
  supplier_invoice_date?: string
  gl_reference_document?: string
  contract_start_date?: string
  contract_end_date?: string
}

export type SalesPlanSource = 'customer_csv' | 'entity_allocated' | 'py_proxy'

export interface SalesTopEntity {
  rank:   number
  name:   string
  cm:     number
  pm:     number
  py_cm:  number
  ytd:    number
  customer_id?: number | null
  supplier_id?: number | null
  entity?: string | null
  plan_source?: SalesPlanSource
  ytd_py?: number
  mtd?: number
  mtd_py?: number
  mtd_pm?: number
  delta_cm_pm?: number
  delta_cm_py?: number
  delta_ytd?: number
  delta_mtd?: number
  ytd_plan?: number
  plan_cm?: number
  mtd_plan?: number
  coverage?: number | null
}

export interface SalesTopEntitiesColLabels {
  cm:    string
  pm:    string
  py_cm: string
  ytd:   string
  ytd_py?: string
  mtd?: string
  mtd_py?: string
  mtd_pm?: string
  delta_cm_pm?: string
  delta_cm_py?: string
  delta_ytd?: string
  delta_mtd?: string
  ytd_plan?: string
  plan_cm?: string
  coverage?: string
}

export interface SalesTopEntitiesResponse {
  rows:         SalesTopEntity[]
  col_labels:   SalesTopEntitiesColLabels
  period_grain?: 'month' | 'week' | 'year'
  rank_by?:      string
  /** Dominant plan resolution mode for this response. */
  plan_mix?:    SalesPlanSource
}

export interface SalesCohortSeries {
  cohort: string
  fy1:    number
  fy2:    number
  fy3:    number
}

export interface SalesCohortResponse {
  years:   string[]
  fy_keys: string[]
  series:  SalesCohortSeries[]
}

// ─── Churn breakdown ──────────────────────────────────────────────────────────

export interface SalesChurnDimRow {
  dim_value:  string
  new:        number
  upsell:     number
  cross_sell: number
  downsell:   number
  lost:       number
  net:        number
}

// ─── Product Mix / PVM ───────────────────────────────────────────────────────

export interface SalesPvmBridge {
  price_effect:  number
  volume_effect: number
  mix_effect:    number | null
}

export interface SalesPvmTableRow {
  dim_value:     string
  period_totals: number[]
  bridges:       SalesPvmBridge[]
}

export interface SalesPvmBridgeResponse {
  periods:        string[]
  period_totals:  number[]
  bridges:        SalesPvmBridge[]
  table_rows?:    SalesPvmTableRow[]
  dim?:           string
  dim_label?:     string
  revenue_model?: string
  method_pvm?:    string
}

export interface SalesMarginRow {
  bucket:    string
  dim_value: string
  cm:        number
  pm:        number
  py_cm:     number
  ytd:       number
  ytd_py:    number
  delta_cm_pm:   number
  delta_cm_pycm: number
}

// ─── Deferrals ────────────────────────────────────────────────────────────────

export interface SalesDeferralRow {
  dim_value: string
  cm:        number
  pm:        number
  py_cm:     number
  ytd:       number
  ytd_py:    number
}

export interface SalesDeferralTimelineEntry {
  month:      string
  realized:   number
  deferred:   number
}

export interface SalesGeoCountry {
  country:          string
  revenue_keur:     number
  py_revenue_keur:  number
  delta_keur:       number
  gross_margin_pct: number
}

export interface SalesGeoLocation {
  city: string
  country: string
  postal_code: string | null
  revenue_keur: number
  customer_count: number
  lat: number | null
  lon: number | null
  geo_source: string | null
}

export interface SalesGeoCountryLocationsResponse {
  country: string
  period: string
  locations: SalesGeoLocation[]
  mapped_count: number
  total_count: number
}

export interface SalesGrossMarginMatrixColumn {
  key: string
  label: string
}

export interface SalesGrossMarginPeriodCell {
  gross_margin_pct: number
  revenue_keur: number
  cost_keur: number
  quantity: number
}

export interface SalesGrossMarginMatrixRow {
  dim_value: string
  periods: Record<string, SalesGrossMarginPeriodCell>
}

export interface SalesGrossMarginMatrixResponse {
  dim: string
  period_grain: string
  columns: SalesGrossMarginMatrixColumn[]
  rows: SalesGrossMarginMatrixRow[]
}

export type SalesDimensionPerformanceMetric = 'gross_sales' | 'gross_profit' | 'units_sold'
export type SalesDimensionPeriodScope = 'month' | 'ytd' | 'py_month'

export interface SalesDimensionPerformanceSegment {
  name: string
  actual: number
  prior: number
  plan: number | null
  delta_prior: number | null
  delta_plan: number | null
  has_plan: boolean
}

export interface SalesDimensionMatrixCell {
  gross_sales_keur: number
  gross_profit_keur: number
  units_sold: number
  gross_margin_pct: number
}

export interface SalesDimensionPerformanceResponse {
  dim: string
  dim_label: string
  metric: SalesDimensionPerformanceMetric
  metric_label: string
  period_scope: SalesDimensionPeriodScope
  period_grain: string
  period_label: string
  prior_label: string
  value_unit: 'keur' | 'units'
  chart: { segments: SalesDimensionPerformanceSegment[] }
  matrix: {
    columns: SalesGrossMarginMatrixColumn[]
    rows: Array<{ dim_value: string; periods: Record<string, SalesDimensionMatrixCell> }>
  }
}

export type SalesFilterKey =
  | 'product_name'
  | 'segment'
  | 'entity'
  | 'end_customer_region'
  | 'industry_sub_sector'
  | 'distribution_type'
  | 'product_revenue_model'

/** Per dimension: non-empty array = filter to those values (comma-separated in API). */
export type SalesFilters = Partial<Record<SalesFilterKey, string[]>>

/** Serialize multi-select sales filters for query strings. */
export function salesFilterQueryParams(filters?: SalesFilters): Record<string, string> {
  const out: Record<string, string> = {}
  if (!filters) return out
  for (const [key, val] of Object.entries(filters) as Array<[SalesFilterKey, string[] | undefined]>) {
    if (!Array.isArray(val) || val.length === 0) continue
    out[key] = val.join(',')
  }
  return out
}

/** Count dimensions with an active (partial) multi-select. */
export function countActiveSalesFilterDimensions(filters?: SalesFilters): number {
  if (!filters) return 0
  return Object.values(filters).filter(v => Array.isArray(v) && v.length > 0).length
}

export interface OperationalOppKpis {
  year: number
  month: number
  period_start: string
  period_end: string
  new_in_month: number
  pipeline_value_open: number
  won_like_in_month: number
}

export interface OperationalPipelineRow {
  status_code: string
  cnt: number
  sum_expected: number
}

export interface OperationalTrendRow {
  month_start: string
  new_count: number
  sum_expected: number
}

export interface OperationalOppTopRow {
  opportunity_id: string
  customer_id: string | null
  legal_entity_code: string | null
  description: string | null
  status_code: string | null
  expected_value: number
  currency_code: string | null
  created_date: string | null
  expected_close: string | null
}

export interface OperationalProcurementTrendRow {
  month_start: string
  po_count: number
  sum_net_value: number
}

export interface OperationalSupplierRow {
  supplier_id: string
  po_count: number
  sum_net_value: number
}

export interface OperationalStatusMixRow {
  status_code: string
  cnt: number
  sum_net_value: number
}

export interface OperationalCategoryMixRow {
  material_group: string
  sum_net_value: number
  sum_qty: number
}

export interface OperationalScatterLineRow {
  net_value: number
  quantity: number
  material_id: string | null
}

export interface OperationalDeliveryKpis {
  year: number
  month: number
  delivery_lines: number
  on_time_rate: number
  in_full_rate: number
  otif_rate: number
  avg_delay_days: number
}

export interface OperationalCarrierRow {
  carrier_name: string
  cnt: number
  sum_freight: number
  otif_rate: number
}

export interface OperationalOtifTrendRow {
  month_start: string
  cnt: number
  otif_rate: number
}

export interface OperationalDelayBucketRow {
  bucket: string
  cnt: number
}

export interface OperationalScatterQtyRow {
  ordered_quantity: number
  delivered_quantity: number
  otif_flag: boolean | null
  freight_cost: number
}

export interface OperationalGanttRow {
  delivery_id: string
  line_number: number
  planned_ship_date: string | null
  actual_ship_date: string | null
  planned_delivery_date: string | null
  actual_delivery_date: string | null
  sales_order_id: string | null
  delay_days: number | null
}

export interface OperationalKpiMetric {
  value: number
  delta_pm?: number | null
  delta_smly?: number | null
  delta_pm_pct?: number | null
  delta_smly_pct?: number | null
}

export interface OpportunityPlanCoverageStage {
  code: string
  label: string
  value_keur: number
}

export interface OpportunityPlanCoverageMonth {
  year: number
  month: number
  label: string
  is_current: boolean
  plan_keur: number
  actual_keur: number
  pipeline_keur: number
  covered_keur: number
  coverage_pct: number | null
  gap_keur: number
}

export interface OpportunityPlanCoverageBridgeResponse {
  year: number
  month: number
  entity?: string | null
  ytd_actual_keur: number
  cm_actual_keur: number
  pipeline_value_keur: number
  other_pipeline_keur: number
  stages: OpportunityPlanCoverageStage[]
  total_keur: number
  annual_plan_keur: number
  gap_to_plan_keur: number
  months_fully_covered: number
  monthly_coverage: OpportunityPlanCoverageMonth[]
}

export interface OpportunityStageSnapshotStage {
  code: string
  label: string
  cm_count: number
  pm_count: number
  delta: number
}

export interface OpportunityStageSnapshotResponse {
  year: number
  month: number
  entity?: string | null
  stages: OpportunityStageSnapshotStage[]
  cm_total: number
  pm_total: number
}

export type OpportunityStageRowDim = 'opportunity_id' | 'salesperson' | 'product' | 'customer' | 'team'
export type OpportunityStageParentDim = 'none' | 'team' | 'customer'
export type OpportunityStageMatrixMetric = 'date' | 'count' | 'value'

export interface OpportunityStageMatrixRow {
  row_key: string
  row_label: string
  level: number
  parent_key?: string | null
  opportunity_id?: string
  salesperson_name?: string
  product_name?: string
  customer_name?: string
  team?: string
  cells: Record<string, string | number | null | undefined>
}

export interface OpportunityStageMatrixResponse {
  year: number
  month: number
  entity?: string | null
  row_dim: OpportunityStageRowDim
  parent_dim: OpportunityStageParentDim
  metric: OpportunityStageMatrixMetric
  stage_codes: string[]
  stage_labels: string[]
  rows: OpportunityStageMatrixRow[]
}

export interface OperationalStageDailyRow {
  stage: string
  stage_label?: string
  day: string
  metric_value: number
}

export interface OperationalGeoDrillRow {
  key: string
  label?: string
  cnt: number
  sum_amount: number
}

export interface OperationalRegionRow {
  country: string
  region: string
  cnt: number
  sum_amount: number
}

export interface OperationalNewOppRow {
  opportunity_id: string
  customer_id: string | null
  customer_name: string | null
  salesperson_name: string | null
  product_name?: string | null
  amount: number
  created_date: string | null
  opportunity_ref?: string | null
  stage_label?: string | null
}

export interface OperationalNewInPeriodHighlight {
  name: string
  opp_count: number
  value_keur: number
}

export interface OperationalNewInPeriodResponse {
  year: number
  month: number
  period_scope: string
  period_start: string
  period_end: string
  kpi: OperationalKpiMetric
  total_value: number
  rows: OperationalNewOppRow[]
  highlights?: {
    top_sales_rep: OperationalNewInPeriodHighlight | null
    top_customer: OperationalNewInPeriodHighlight | null
    top_product: OperationalNewInPeriodHighlight | null
  }
}

export interface OperationalSalesRepRow {
  sales_representative: string
  created_count: number
  created_count_pm: number
  accepted_count: number
  accepted_count_pm: number
  acceptance_rate: number
  acceptance_rate_pm: number
  won_eur: number
  won_eur_pm: number
  won_eur_delta_pm: number
  won_eur_delta_pm_pct: number | null
  created_delta_pm: number
  team_avg_won_eur: number
  team_avg_created: number
  team_avg_acceptance_rate: number
  vs_team_won_pct: number
  vs_team_created_pct: number
  rank: number
  is_top: boolean
}

export interface OperationalSalesRepResponse {
  year: number
  month: number
  period_scope: string
  period_start: string
  period_end: string
  rows: OperationalSalesRepRow[]
}

export interface OperationalCloseDateRow {
  close_date: string
  sum_amount: number
}

export interface OperationalTopExtendedRow {
  opportunity_id: string
  customer_name: string | null
  amount: number
  opportunity_date: string | null
  sales_representative: string
  stage: string | null
  stage_label?: string | null
  number_of_items?: number
  lost_reason?: string | null
  opportunity_ref?: string | null
  entity?: string | null
  sales_site?: string | null
  team?: string | null
}

export type OppTopPeriodScope = 'week' | 'month' | 'quarter' | 'ytd'

export interface OperationalTopExtendedResponse {
  year: number
  month: number
  kind: string
  period_scope: OppTopPeriodScope
  period_start: string
  period_end: string
  total_count: number
  total_value: number
  rows: OperationalTopExtendedRow[]
}

export interface OperationalOutcomeTrendPoint {
  label: string
  won_count: number
  lost_count: number
  ytg_gross_sales_keur: number
}

export interface OperationalOutcomeTrendResponse {
  period_grain: 'week' | 'month'
  range_label: { from: string; to: string }
  points: OperationalOutcomeTrendPoint[]
}

export interface OperationalProcurementRate {
  key: string
  label: string
  total: number
  exception: number
  within_pct: number
  exception_pct: number
}

export interface OperationalMaterialCoverageSummary {
  open_opportunity_count: number
  materials_tracked: number
  materials_at_risk: number
  materials_to_order: number
  total_shortfall_qty: number
  total_demand_qty: number
  coverage_pct: number
}

export interface OperationalMaterialCoverageRow {
  material_id: string
  material_name: string
  material_group: string
  demand_qty: number
  opportunity_count: number
  on_hand_qty: number
  available_qty: number
  reorder_point: number
  shortfall_qty: number
  coverage_pct: number
  status: 'ok' | 'monitor' | 'order'
  suggested_order_qty: number
}

export interface OperationalCycleStep {
  step: string
  label: string
  avg_days: number
}

export interface OperationalCycleTransition {
  from_step: string
  to_step: string
  avg_days: number
}

export interface OperationalCycleDrilldownRow {
  purchase_order_id: string
  purchase_order_number?: string | null
  supplier_id?: string | null
  order_date?: string | null
  days_in_stage: number
  days_total?: number | null
}

export interface OperationalPriceAnalysisRow {
  category: string
  material: string
  material_id?: string | null
  supplier_name: string
  supplier_id?: string
  unit_price: number
  delivery_date: string | null
  units_bought: number
  delivery_delay_days: number | null
  is_on_time: boolean | null
  /** Share of lines on-time for this supplier in the period (0–100). */
  supplier_on_time_pct: number | null
  delivery_achievement_rate?: number | null
  /** @deprecated use supplier_name */
  supplier?: string
}

export interface OperationalRouteRow {
  delivery_id: string
  delivery_number: string | null
  destination: string
  delivery_date?: string | null
  vehicle_type?: string | null
  customer_name?: string
  vehicle_id?: string
  number_of_items: number
  amount_shipped: number
}

export interface OperationalDeliveryDimensionRow {
  key: string
  label?: string
  cnt: number
  delivered_amount: number
}

export interface OperationalDeliveryAmountDrill {
  year: number
  month: number
  dimension: string
  countries: OperationalDeliveryDimensionRow[]
  by_country: Record<string, OperationalDeliveryDimensionRow[]>
}

export interface OperationalStatusRateTrendRow {
  year: number
  month: number
  period: string
  within_pct: number
}

export interface OperationalVehicleRow {
  vehicle_id: string
  vehicle_type: string
  license_plate: string | null
  capacity_units: number | null
  status_code: string | null
  driver_name?: string | null
  route_status?: string | null
  readiness_status?: string | null
  utilization_pct?: number
  avg_utilization_pct?: number
}

export interface OperationalVehicleDetail {
  found: boolean
  vehicle_id?: string
  vehicle_type?: string
  license_plate?: string | null
  capacity_units?: number | null
  capacity_weight_kg?: number | null
  status_code?: string | null
  driver_name?: string | null
  current_lat?: number | null
  current_lon?: number | null
  readiness_status?: string | null
  route_status?: string | null
  active_delivery_id?: string | null
  active_customer_name?: string | null
  active_destination?: string | null
  utilization_pct?: number
}

export interface OperationalRouteDetail {
  found: boolean
  delivery_id?: string
  delivery_number?: string | null
  carrier_name?: string | null
  tracking_number?: string | null
  shipping_method?: string | null
  planned_ship_date?: string | null
  actual_delivery_date?: string | null
  origin_lat?: number | null
  origin_lon?: number | null
  dest_lat?: number | null
  dest_lon?: number | null
  route_polyline?: number[][] | null
  distance_km?: number | null
  utilization_pct?: number | null
  vehicle_id?: string | null
  vehicle_type?: string | null
  vehicle_status?: string | null
  duration_minutes?: number | null
}

export interface OperationalRouteGeometry {
  delivery_id: string
  found: boolean
  source?: string
  polyline: number[][]
  distance_km?: number | null
  duration_minutes?: number | null
}

export interface InventorySnapshotKpis {
  fiscal_year: number
  fiscal_period: number
  total_value: number
  total_qty: number
  sku_site_count: number
}

export interface InventoryMaterialRow {
  material_id: string
  material_name: string
  sum_value: number
  sum_qty: number
}

export interface InventorySiteRow {
  site_code: string
  sum_value: number
  sum_qty: number
}

export interface InventoryMovementTrendRow {
  month_start: string
  sum_abs_qty: number
  sum_abs_amount: number
}

export interface InventoryMovementTypeRow {
  movement_type: string
  sum_abs_qty: number
  evt_count: number
}

export interface InventoryLinkMixRow {
  link_bucket: string
  cnt: number
}

export interface InventoryRiverRow {
  month_start: string
  movement_type: string
  sum_abs_qty: number
}

export interface InventoryHealthSummary {
  fiscal_year: number
  fiscal_period: number
  note: string
  series: unknown[]
}

export interface InventoryHierarchyBlock {
  value: number
  delta_pm?: number | null
  delta_pm_pct?: number | null
}

export interface InventoryCombinedChartRow {
  label: string
  sales: number
  inventory: number
  turnover: number
}

export interface InventoryDioDpoResponse {
  year: number
  month: number
  grain: string
  dio: { value: number; max: number }
  dpo: { value: number; max: number }
  series: { period: string; dio: number; dpo: number }[]
}

export interface InventoryPayablesAgingResponse {
  year: number
  month: number
  as_of: string
  total_payables: number
  subledger_total: number
  reconciliation_mode: 'subledger' | 'scaled' | 'synthetic'
  series: InventoryPayablesBand[]
}

export interface InventoryPayablesBand {
  band: string
  label: string
  amount: number
}

export interface InventoryPayablesDrillRow {
  supplier_name: string
  document_number?: string | null
  posting_date: string
  amount: number
}

export interface ReceivablesAgingBand {
  band: string
  label: string
  amount: number
}

export interface ReceivablesAgingKpis {
  before_due: number
  overdue: number
  overdue_pct: number
  dso_days: number
  open_documents: number
  net_sales?: number
  credit_sales_pct?: number
  avg_payment_terms_days?: number
}

export interface ReceivablesExtendedKpis {
  net_change_mom: number
  mom_variance_pct: number
  net_sales: number
  credit_sales_pct: number
  avg_payment_terms_days: number
}

export interface ReceivablesStatusSplit {
  before_due: number
  overdue: number
  before_due_pct: number
  overdue_pct: number
}

export interface ReceivablesSummaryRow {
  band: string
  label: string
  document_count: number
  amount: number
}

export interface ReceivablesAgingResponse {
  year: number
  month: number
  as_of: string
  total_receivables: number
  subledger_total: number
  reconciliation_mode: 'subledger' | 'scaled' | 'synthetic'
  series: ReceivablesAgingBand[]
  kpis: ReceivablesAgingKpis
  extended_kpis?: ReceivablesExtendedKpis
  kpi_metrics?: Record<string, OperationalKpiMetric>
  status_split?: ReceivablesStatusSplit
  summary_table?: ReceivablesSummaryRow[]
}

export interface ReceivablesDrillRow {
  customer_name: string
  document_number?: string | null
  posting_date: string
  due_date?: string
  days_outstanding: number
  days_overdue?: number
  payment_terms_days?: number
  amount: number
}

export type ReceivablesAgingDimension =
  | 'customer'
  | 'country'
  | 'customer_group'
  | 'salesperson'
  | 'entity'
  | 'segment'

export type ReceivablesHierarchyDim = ReceivablesAgingDimension | 'invoice_number'

export interface ReceivablesHierarchyBreakdownRow {
  dims: Partial<Record<ReceivablesHierarchyDim, string>>
  total: number
  overdue_pct: number
  not_yet_due?: number
  overdue_1_30?: number
  overdue_31_60?: number
  overdue_61_90?: number
  overdue_91_180?: number
  overdue_over_180?: number
  before_due?: number
  overdue?: number
  [key: string]: number | Partial<Record<ReceivablesHierarchyDim, string>> | undefined
}

export interface ReceivablesHierarchyBreakdownResponse {
  hierarchy: ReceivablesHierarchyDim[]
  view: ReceivablesDimensionView
  year: number
  month: number
  compare_pm: boolean
  compare_py: boolean
  rows: ReceivablesHierarchyBreakdownRow[]
}

export interface AgingPortfolioTableRow {
  label: string
  amount: number
  document_count: number
  relationship_since: string | null
}

export interface AgingPortfolioBucket {
  band: string
  label: string
  amount: number
  document_count: number
  rows: AgingPortfolioTableRow[]
}

export interface AgingPortfolioTableResponse {
  dimension: string
  year: number
  month: number
  as_of: string
  buckets: AgingPortfolioBucket[]
}
export type ReceivablesDimensionView = 'buckets' | 'due_overdue'

export interface ReceivablesDimensionRowBuckets {
  label: string
  not_yet_due: number
  overdue_1_30: number
  overdue_31_60: number
  overdue_61_90: number
  overdue_91_180: number
  overdue_over_180: number
  total: number
  overdue_pct: number
}

export interface ReceivablesDimensionRowDueOverdue {
  label: string
  before_due: number
  overdue: number
  total: number
  overdue_pct: number
}

export type ReceivablesDimensionRow = ReceivablesDimensionRowBuckets | ReceivablesDimensionRowDueOverdue

export interface ReceivablesCompositionSegment {
  band: string
  label: string
  amount: number
}

export interface AgingConcentrationSegment {
  band: string
  label: string
  amount: number
  pct: number
  customer_count: number
  supplier_count?: number
}

export interface ReceivablesConcentration {
  year: number
  month: number
  period_grain?: 'month' | 'week' | 'year'
  iso_year?: number
  iso_week?: number
  as_of?: string
  total: number
  segments: AgingConcentrationSegment[]
}

export interface ReceivablesTrendPoint {
  year: number
  month: number
  label: string
  balance: number
  overdue: number
  overdue_pct: number
  dso_days: number
  before_due: number
  open_documents: number
  gross_sales?: number
  mom_variance_pct?: number
  iso_year?: number
  iso_week?: number
}

export interface ReceivablesTrendResponse {
  year: number
  month: number
  period_grain?: 'month' | 'quarter' | 'week'
  periods_back?: number
  points: ReceivablesTrendPoint[]
}

export interface ReceivablesConcentrationTrendPoint {
  year: number
  month: number
  period_grain?: 'month' | 'week' | 'year'
  iso_year?: number
  iso_week?: number
  label: string
  segments: AgingConcentrationSegment[]
}

export interface ReceivablesCustomerRegisterDocument {
  document_ref: string
  journal_entry_number: string
  posting_date: string | null
  due_date: string | null
  amount: number
  days_outstanding: number
  is_overdue: boolean
}

export interface ReceivablesCustomerRegisterRow {
  customer_id: string
  customer_name: string
  contact_name?: string | null
  balance: number
  overdue: number
  overdue_pct: number
  days_outstanding: number
  payment_terms_days: number
  open_documents: number
  gross_sales: number
  documents?: ReceivablesCustomerRegisterDocument[]
}

export interface ReceivablesCustomerComboRow {
  customer_name: string
  balance: number
  days_outstanding: number
}

export interface ReceivablesCustomerScatterRow {
  customer_id?: string
  customer_name: string
  balance: number
  overdue_pct: number
}

export interface ReceivablesEntityRow {
  entity_code: string
  balance: number
}

export interface ReceivablesDimensionChartRow {
  label: string
  balance: number
  pm_balance?: number
  py_balance?: number
  delta_pm?: number
  delta_pm_pct?: number | null
  delta_py?: number
  delta_py_pct?: number | null
}

export interface ReceivablesDimensionChartResponse {
  dimension: string
  year: number
  month: number
  compare_pm: boolean
  compare_py: boolean
  rows: ReceivablesDimensionChartRow[]
}

export interface ReceivablesGeoRow {
  country: string
  balance: number
  before_due: number
  overdue: number
  overdue_pct: number
}

export interface ReceivablesGeoLocation extends SalesGeoLocation {
  before_due_keur?: number
  overdue_keur?: number
}

export interface ReceivablesGeoCountryLocationsResponse {
  country: string
  period: string
  locations: ReceivablesGeoLocation[]
  mapped_count: number
  total_count: number
}

export interface ReceivablesSalesLinkPoint {
  year: number
  month: number
  label: string
  balance: number
  net_sales: number
  credit_sales_pct: number
}

export interface PayablesAgingBand {
  band: string
  label: string
  amount: number
}

export interface PayablesAgingKpis {
  before_due: number
  overdue: number
  overdue_pct: number
  dpo_days: number
  open_documents: number
  procurement_spend?: number
  payables_to_spend_pct?: number
  avg_payment_terms_days?: number
}

export interface PayablesExtendedKpis {
  net_change_mom: number
  mom_variance_pct: number
  procurement_spend: number
  payables_to_spend_pct: number
  avg_payment_terms_days: number
}

export interface PayablesStatusSplit {
  before_due: number
  overdue: number
  before_due_pct: number
  overdue_pct: number
}

export interface PayablesSummaryRow {
  band: string
  label: string
  document_count: number
  amount: number
}

export interface PayablesAgingResponse {
  year: number
  month: number
  as_of: string
  total_payables: number
  subledger_total: number
  reconciliation_mode: 'subledger' | 'scaled' | 'synthetic'
  series: PayablesAgingBand[]
  kpis: PayablesAgingKpis
  extended_kpis?: PayablesExtendedKpis
  kpi_metrics?: Record<string, OperationalKpiMetric>
  status_split?: PayablesStatusSplit
  summary_table?: PayablesSummaryRow[]
}

export interface PayablesDrillRow {
  supplier_name: string
  document_number?: string | null
  posting_date: string
  due_date?: string
  days_outstanding: number
  days_overdue?: number
  payment_terms_days?: number
  amount: number
}

export type PayablesAgingDimension =
  | 'supplier'
  | 'country'
  | 'supplier_group'
  | 'buyer'
  | 'entity'
  | 'segment'
export type PayablesDimensionView = 'buckets' | 'due_overdue'

export interface PayablesDimensionRowBuckets {
  label: string
  not_yet_due: number
  overdue_1_30: number
  overdue_31_60: number
  overdue_61_90: number
  overdue_91_180: number
  overdue_over_180: number
  total: number
  overdue_pct: number
}

export interface PayablesDimensionRowDueOverdue {
  label: string
  before_due: number
  overdue: number
  total: number
  overdue_pct: number
}

export type PayablesDimensionRow = PayablesDimensionRowBuckets | PayablesDimensionRowDueOverdue

export interface PayablesHierarchyBreakdownRow {
  dims: Record<string, string>
  not_yet_due?: number
  overdue_1_30?: number
  overdue_31_60?: number
  overdue_61_90?: number
  overdue_91_180?: number
  overdue_over_180?: number
  before_due?: number
  overdue?: number
  total: number
  overdue_pct: number
  pm_not_yet_due?: number
  pm_overdue_1_30?: number
  pm_overdue_31_60?: number
  pm_overdue_61_90?: number
  pm_overdue_91_180?: number
  pm_overdue_over_180?: number
  pm_before_due?: number
  pm_overdue?: number
  pm_total?: number
  pm_overdue_pct?: number
  py_not_yet_due?: number
  py_overdue_1_30?: number
  py_overdue_31_60?: number
  py_overdue_61_90?: number
  py_overdue_91_180?: number
  py_overdue_over_180?: number
  py_before_due?: number
  py_overdue?: number
  py_total?: number
  py_overdue_pct?: number
  delta_pm_not_yet_due?: number
  delta_pm_overdue_1_30?: number
  delta_pm_overdue_31_60?: number
  delta_pm_overdue_61_90?: number
  delta_pm_overdue_91_180?: number
  delta_pm_overdue_over_180?: number
  delta_pm_before_due?: number
  delta_pm_overdue?: number
  delta_pm_total?: number
  delta_pm_overdue_pct?: number
  delta_py_not_yet_due?: number
  delta_py_overdue_1_30?: number
  delta_py_overdue_31_60?: number
  delta_py_overdue_61_90?: number
  delta_py_overdue_91_180?: number
  delta_py_overdue_over_180?: number
  delta_py_before_due?: number
  delta_py_overdue?: number
  delta_py_total?: number
  delta_py_overdue_pct?: number
}

export interface PayablesCompositionSegment {
  band: string
  label: string
  amount: number
}

export interface PayablesConcentration {
  year: number
  month: number
  period_grain?: 'month' | 'week' | 'year'
  as_of?: string
  iso_year?: number
  iso_week?: number
  total: number
  segments: AgingConcentrationSegment[]
}

export interface PayablesTrendPoint {
  year: number
  month: number
  label: string
  balance: number
  overdue: number
  overdue_pct: number
  dpo_days: number
  before_due: number
  open_documents: number
  procurement_spend?: number
  cost_of_materials?: number
  mom_variance_pct?: number
  iso_year?: number
  iso_week?: number
}

export interface PayablesConcentrationTrendPoint {
  year: number
  month: number
  period_grain?: 'month' | 'week' | 'year'
  iso_year?: number
  iso_week?: number
  label: string
  segments: AgingConcentrationSegment[]
}

export interface PayablesSupplierRegisterRow {
  supplier_id: string
  supplier_name: string
  balance: number
  overdue: number
  overdue_pct: number
  days_outstanding: number
  payment_terms_days: number
  open_documents: number
  procurement_spend: number
  cost_of_materials?: number
  documents?: PayablesSupplierRegisterDocument[]
}

export interface PayablesSupplierRegisterDocument {
  document_ref: string
  journal_entry_number: string
  posting_date: string | null
  due_date: string | null
  amount: number
  days_outstanding: number
  is_overdue: boolean
}

export interface PayablesSupplierComboRow {
  supplier_name: string
  balance: number
  days_outstanding: number
}

export interface PayablesSupplierScatterRow {
  supplier_name: string
  balance: number
  overdue_pct: number
}

export interface PayablesEntityRow {
  entity_code: string
  balance: number
}

export interface PayablesDimensionChartRow {
  label: string
  balance: number
  pm_balance?: number
  py_balance?: number
  delta_pm?: number
  delta_pm_pct?: number | null
  delta_py?: number
  delta_py_pct?: number | null
}

export interface PayablesDimensionChartResponse {
  dimension: string
  year: number
  month: number
  compare_pm: boolean
  compare_py: boolean
  rows: PayablesDimensionChartRow[]
}

export interface PayablesGeoRow {
  country: string
  balance: number
  before_due: number
  overdue: number
  overdue_pct: number
}

export interface PayablesGeoCountryLocation {
  city: string
  country: string
  postal_code?: string | null
  revenue_keur: number
  before_due_keur: number
  overdue_keur: number
  customer_count: number
  lat?: number | null
  lon?: number | null
  geo_source?: string
}

export interface PayablesGeoCountryLocationsResponse {
  country: string
  period: string
  locations: PayablesGeoCountryLocation[]
  mapped_count: number
  total_count: number
}

export interface PayablesProcurementLinkPoint {
  year: number
  month: number
  label: string
  balance: number
  procurement_spend: number
  payables_to_spend_pct: number
}

export interface InventoryStockPositionRow {
  product_name: string
  material: string
  physical_available: number
  soft_reserved: number
  on_hand: number
  new_order_qty: number
  returned?: number
  return_rate?: number
  sell_rate?: number
  out_of_stock: number
}

export interface InventoryWarehouseRow {
  warehouse_id: string
  warehouse_name?: string
  on_hand: number
  soft_reserved: number
  returned: number
}

export interface InventoryOverviewRow {
  inventory_id: string
  material: string
  location: string
  date_received?: string | null
  status: string
  supplier_name: string
  unit_cost: number
}

export interface InventoryInOutDrillRow {
  material: string
  location: string
  quantity: number
  movement_type?: string
}

export interface InventoryStockoutItemRow {
  material_id?: string
  label: string
  /** Available quantity (not physical on-hand). */
  qty: number
  reorder_point?: number
  shortfall?: number
  deficit_pct?: number
  coverage_pct?: number
  severity?: string
}

export interface InventoryReturnRateRow {
  label: string
  return_rate: number
  returned?: number
  issued?: number
}

export interface InventoryInOutTrendRow {
  label: string
  stock_in: number
  stock_out: number
  period_start?: string
  period_end?: string
}

export interface SalesBreakdownRow {
  l1: string
  l2: string | null
  l3: string
  gs_pm: number
  gs_cm: number
  gp_pm: number
  gp_cm: number
  gm_pm: number
  gm_cm: number
  gs_plan_cm: number
  gp_plan_cm: number
  gm_plan_cm: number
  delta_gs_cm_pm: number
  delta_gp_cm_pm: number
  delta_gm_cm_pm: number
}

export interface SalesBreakdownResponse {
  period_grain: 'month' | 'week'
  col_labels: { pm: string; cm: string; plan_cm: string; delta_cm_pm?: string }
  dim_labels: { top: string; mid?: string | null; bottom: string }
  has_mid_level: boolean
  rows: SalesBreakdownRow[]
}

export interface SalesGeoBreakdownRow {
  region?:   string | null
  dim_value: string
  gs_cm:     number
  gp_cm:     number
  gm_cm:     number
  gs_py:     number
  gp_py:     number
  gm_py:     number
  delta_gs:  number
  delta_gp:  number
  delta_gm:  number
}

export interface SalesGeoTrendResponse {
  periods: Record<string, number | string>[]
  regions: string[]
}

export interface SalesGrossSalesTrendResponse {
  period_grain: 'month' | 'week'
  dim: string
  range_label: { from: string; to: string }
  segments: string[]
  periods: Array<{ label: string; values: Record<string, number> }>
}

export interface SalesProfitMarginScatterPoint {
  segment: string
  gross_sales_keur: number
  gross_profit_keur: number
  gross_margin_pct: number
}

export interface SalesProfitMarginScatterResponse {
  period_grain: 'month' | 'week'
  dim: string
  period_label: string
  points: SalesProfitMarginScatterPoint[]
}

export interface SalesCompositionSegment {
  name: string
  value_keur: number
  share_pct: number
}

export interface SalesCompositionChart {
  dim: string
  dim_label: string
  segments: SalesCompositionSegment[]
}

export interface SalesCompositionBreakdownResponse {
  metric: 'gross_sales' | 'gross_profit' | 'gross_margin'
  period_grain: 'month' | 'week'
  period_label: string
  charts: SalesCompositionChart[]
}

export interface SalesMetricBridgeSegment {
  name: string
  delta_keur: number
}

export interface SalesMetricBridgeStep {
  from: string
  to: string
  segments: SalesMetricBridgeSegment[]
}

export interface SalesMetricBridgeResponse {
  metric: 'gross_sales' | 'gross_profit' | 'gross_margin'
  dim: string
  period_grain: 'month' | 'week'
  period_label: string
  range_label: { from: string; to: string }
  periods: string[]
  period_totals: number[]
  period_display: string[]
  bridges: SalesMetricBridgeStep[]
  value_unit: 'keur'
}

export interface SalesChurnBridge {
  from:       string
  to:         string
  new:        number
  upsell:     number
  cross_sell: number
  downsell:   number
  lost:       number
}

export interface SalesChurnTableRow {
  dim_value:     string
  period_totals: number[]
  bridges:       Array<Pick<SalesChurnBridge, 'new' | 'upsell' | 'cross_sell' | 'downsell' | 'lost'>>
}

export interface SalesChurnBridgeResponse {
  periods:       string[]
  period_totals: number[]
  bridges:       SalesChurnBridge[]
  table_rows?:   SalesChurnTableRow[]
  dim?:          string
  dim_label?:    string
}

export interface SalesChurnDrillRow {
  name:          string
  from_rev_keur: number
  to_rev_keur:   number
  delta_keur:    number
}

export interface SalesRecurringPeriod {
  label:         string
  total:         number
  recurring:     number
  non_recurring: number
}

export interface SalesRecurringSplitResponse {
  periods: SalesRecurringPeriod[]
}

export interface SalesDeltaAttributionFinding {
  dimension: string
  value: string
  delta_keur: number
  cm_keur: number
  pm_keur: number
}

export interface SalesDeltaAttributionResponse {
  facts: {
    from_period: { year: number; month: number; label: string }
    to_period: { year: number; month: number; label: string }
    totals: { gross_sales_pm_keur: number; gross_sales_cm_keur: number; delta_keur: number }
    drivers_by_dimension: Record<
      string,
      Array<{ value: string; cm_keur: number; pm_keur: number; delta_keur: number }>
    >
    top_findings: SalesDeltaAttributionFinding[]
    customer_dynamics: {
      counts: { lost_customers: number; new_customers: number; returning_customers: number }
      value_impact_keur: Record<string, number>
      top_lost: Array<{ customer_id: string; pm_keur: number; delta_keur: number }>
      top_new: Array<{ customer_id: string; cm_keur: number; delta_keur: number }>
      top_returning: Array<{ customer_id: string; cm_keur: number; delta_keur: number }>
    }
    pvm_signal: {
      price_effect_keur: number
      volume_effect_keur: number
      mix_effect_keur: number
      signal: string
    }
    reconciliation: { explained_delta_keur: number; residual_keur: number }
  }
  narrative?: {
    headline: string
    summary: string
    top_drivers: string[]
    customer_dynamics: string
    risk_follow_up: string
  } | null
  links: Array<{ label: string; route: string; tab?: string; anchor?: string }>
}

export interface SalesCustomerRiskContextResponse {
  facts: {
    period: { year: number; month: number; label: string }
    customer: { customer_id?: string; customer_name?: string }
    sales_context: { pm_sales_keur: number; cm_sales_keur: number; delta_keur: number; stopped_buying: boolean }
    receivables_context: {
      balance_keur: number
      overdue_keur: number
      overdue_pct: number
      days_outstanding: number
      open_documents: number
    }
    risk: { level: string; flags: string[] }
  }
  narrative?: {
    headline: string
    summary: string
    top_drivers: string[]
    customer_dynamics: string
    risk_follow_up: string
  } | null
  links: Array<{ label: string; route: string; tab?: string; anchor?: string }>
}

// ─── Benchmark ────────────────────────────────────────────────────────────────

export interface BenchmarkSource {
  source_name: string
  source_tier: 'TIER1' | 'TIER2' | 'TIER3'
  source_type: string
  license_class: string
  default_priority: number
  update_frequency: string
  is_active: boolean
}

export interface BenchmarkPeerOverview {
  company_name: string
  year: number
  peer_mode: string
  peer_count: number
  source_count: number
  last_refresh: string | null
}

export interface BenchmarkKpiMetricRow {
  metric: string
  internal_value: number
  peer_median: number
  peer_p25: number
  peer_p75: number
  gap_to_median: number
  gap_to_median_pct: number
  peer_points: number
}

export interface BenchmarkKpiComparison extends BenchmarkKpiMetricRow {
  company_name: string
  year: number
  peer_mode: string
}

export interface BenchmarkKpiMultiResponse {
  company_name: string
  year: number
  peer_mode: string
  metrics: Record<
    'revenue_keur' | 'ebitda_keur' | 'ebitda_margin_pct' | 'growth_yoy_pct',
    BenchmarkKpiMetricRow
  >
}

export interface BenchmarkGroupMetricRow {
  group_value: number
  peer_median: number
  delta_to_peers: number
  delta_pct: number
  peer_points: number
}

export interface BenchmarkGroupKpiMultiResponse {
  year: number
  peer_mode: string
  metrics: Record<
    'revenue_keur' | 'ebitda_keur' | 'ebitda_margin_pct' | 'growth_yoy_pct',
    BenchmarkGroupMetricRow
  >
}

export interface BenchmarkDistributionRow {
  company_name: string
  industry_tag: string
  region_tag: string
  size_band: string
  metric_value: number
  source: {
    source_name: string
    source_tier: 'TIER1' | 'TIER2' | 'TIER3'
    source_url: string | null
    publication_date: string | null
    ingested_at: string | null
  }
}

export interface BenchmarkPeerMetricRow {
  company_name: string
  industry_tag: string
  region_tag: string
  size_band: string
  revenue_keur: number | null
  ebitda_keur: number | null
  ebitda_margin_pct: number | null
  growth_yoy_pct: number | null
  source: {
    source_name: string
    source_tier: 'TIER1' | 'TIER2' | 'TIER3'
    source_url: string | null
    publication_date: string | null
    ingested_at: string | null
  }
}

export interface BenchmarkMetadata {
  company_count: number
  internal_companies: number
  latest_ingestion: string | null
  current_year_start: string
  current_year_end: string
}

export interface BenchmarkEntity {
  company_name: string
  industry_tag: string
  region_tag: string
  size_band: string
  is_internal_reference: boolean
}

export interface BenchmarkFilterOptions {
  industry_tag: string[]
  region_tag: string[]
  size_band: string[]
}

export interface BenchmarkGroupComparison {
  year: number
  metric: string
  industry_tag?: string
  region_tag?: string
  size_band?: string
  source_tier?: 'TIER1' | 'TIER2' | 'TIER3'
  median_value: number
  p25_value: number
  p75_value: number
  avg_value: number
  data_points: number
}

export interface BenchmarkFilters {
  industry_tag?: string
  region_tag?: string
  size_band?: string
  source_tier?: string
}

export interface BenchmarkGroupPeerTrendRow {
  fiscal_year: number
  group_revenue_keur: number | null
  peer_revenue_keur: number | null
  group_ebitda_keur: number | null
  peer_ebitda_keur: number | null
}

export interface BenchmarkScatterPoint {
  company_name: string
  region_tag: string
  revenue_keur: number | null
  ebitda_keur: number | null
  is_subject: boolean
}

export interface BenchmarkMarketKpis {
  industry_tag: string
  start_year: number
  end_year: number
  market_size_eurm: number
  market_growth_avg_pct: number
  market_growth_cagr_pct: number
}

export interface BenchmarkMarketForecastRow {
  fiscal_year: number
  market_size_eurm: number
  market_growth_pct: number | null
  is_forecast: boolean
}

export interface BenchmarkGdpBubbleRow {
  region_key: string
  avg_historic_growth_pct: number
  avg_expected_growth_pct: number
  gdp_current_usd_bn_2025: number
}

export interface BenchmarkGdpTrendRow {
  fiscal_year: number
  world_growth_pct: number
  eu_growth_pct: number
  germany_growth_pct: number
  is_forecast: boolean
}

export interface BenchmarkGdpInsight {
  text: string
}

export interface BenchmarkCompetitorProfileRow {
  company_name: string
  hq_country: string | null
  established_year: number | null
  owner_name: string | null
  employees: number | null
  revenues_eurm: number | null
  product_portfolio: string | null
  remarks: string | null
}

export interface BenchmarkCompetitorProfileComparison {
  group: BenchmarkCompetitorProfileRow | null
  competitors: BenchmarkCompetitorProfileRow[]
  top_competitors: string[]
}

export interface BenchmarkPerformancePoint {
  fiscal_year: number
  revenue_keur: number
  revenue_per_employee_keur: number | null
  ebitda_margin_pct: number | null
}

export interface BenchmarkCompetitorPerformanceComparison {
  years: number[]
  group: { company_name: string; series: BenchmarkPerformancePoint[] } | null
  competitors: Array<{ company_name: string; series: BenchmarkPerformancePoint[] }>
}

export interface CompetitorSalesTimelineSeries {
  key: string
  label: string
  is_portfolio_group?: boolean
}

export interface CompetitorSalesTimelinePayload {
  granularity: string
  mode: string
  drill_label: string | null
  rows: Array<Record<string, string | number | null | undefined>>
  series: CompetitorSalesTimelineSeries[]
  disclaimer: string
}

export interface CompetitorSalesTimelineResponse {
  metric: string
  data: CompetitorSalesTimelinePayload
}

export interface CompetitorCompanyNewsSnippet {
  company_name: string
  title: string
  snippet_text: string
  source_url: string
  source_host: string
  published_at: string | null
  image_url: string | null
}

export interface CompetitorCompanyNewsResponse {
  subject_label: string
  executive_brief: CustomerRevenueNewsExecutiveBrief
  snippets: CompetitorCompanyNewsSnippet[]
}

export interface BenchmarkSearchInterestComparison {
  series: Array<{
    term: string
    points: Array<{ month_start: string; interest_index: number }>
  }>
}

export interface BenchmarkCompetitorDiscoveryRow {
  candidate_name: string
  candidate_domain: string | null
  candidate_country: string | null
  confidence_score: number
  confidence_level: 'high' | 'medium' | 'low'
  reason_codes: string[]
  reason_text: string | null
  source_name: string
  source_tier: 'TIER1' | 'TIER2' | 'TIER3'
  source_url: string | null
  observation_date: string
}

export interface BenchmarkCompetitorDiscoveryResponse {
  company_name: string
  rows: BenchmarkCompetitorDiscoveryRow[]
}

export interface IndustryInsightSnippet {
  title: string
  snippet_text: string
  source_url: string
  source_host: string
  published_at: string | null
  /** og:image / RSS media when allow-listed; otherwise UI uses a thematic stock fallback. */
  image_url?: string | null
}

export interface IndustryInsightSeriesPoint {
  month: string
  value: number
}

export interface IndustryInsightSeriesForecastPoint extends IndustryInsightSeriesPoint {
  is_forecast: boolean
}

export interface IndustryInsightSeriesBundle {
  series_key: string
  /** Human-readable series title (English from pack). */
  label: string
  fred_id?: string | null
  /** Optional public documentation URL (e.g. BLS PPI overview). */
  reference_url?: string | null
  /** `eurostat` = Eurostat public API cache; `bundled` = shipped JSON; `external` = other DB-backed sources. */
  data_source?: 'eurostat' | 'bundled' | 'external' | string
  unit: string
  history: IndustryInsightSeriesPoint[]
  forecast: IndustryInsightSeriesForecastPoint[]
}

export interface IndustryInsightsExecutiveBrief {
  text: string | null
  model?: string | null
  cached?: boolean
  error?: string | null
}

export type IndustrySnippetBlock = {
  snippets: IndustryInsightSnippet[]
  has_more?: boolean
  snippet_page_size?: number
}

export interface IndustryInsightsDashboard {
  pack_key: string
  industry_tag?: string
  summaries: {
    investments: string
    materials: string
    energy: string
    workforce: string
  }
  executive_brief?: IndustryInsightsExecutiveBrief | null
  investments: IndustrySnippetBlock
  workforce: IndustrySnippetBlock
  materials: { series: IndustryInsightSeriesBundle[] }
  energy: { series: IndustryInsightSeriesBundle[] }
}

export interface IndustryInsightsSnippetsResponse {
  snippets: IndustryInsightSnippet[]
  has_more: boolean
  snippet_page_size: number
  block: string
}

export type BsSnapshotPeriodKey = 'dec_py2' | 'fy_py' | 'fy' | 'cm_py' | 'cm'
export type BsSnapshotPeriodAmounts = Record<BsSnapshotPeriodKey, number>

export interface ConsolidationRow {
  id:              string
  label:           string
  row_kind:        'line' | 'subtotal' | 'title' | 'detail' | 'account' | 'kpi' | 'kpi_header'
  is_bold?:        boolean
  entity_amounts:  Record<string, number>
  /** Annual BS snapshot balances per entity (dec_py2 … cm). */
  entity_periods?: Record<string, BsSnapshotPeriodAmounts>
  aggregated:      number
  aggregated_periods?: BsSnapshotPeriodAmounts
  ic_eliminations: number
  consolidation:   number
  consolidation_periods?: BsSnapshotPeriodAmounts
  has_children:    boolean
  children:        ConsolidationRow[]
}

export interface ConsolidationResponse {
  statement:  'pl' | 'bs' | 'cf' | 'wc'
  period_grain?: 'month' | 'week' | 'year'
  year:       number
  month:      number
  iso_year?:  number
  iso_week?:  number
  col_label:  string
  /** Annual BS snapshot column labels when period_grain is year. */
  col_labels?: ErSnapshotColLabels
  entities:   Array<{ code: string; label: string }>
  rows:       ConsolidationRow[]
}

export interface MonthlyRow {
  id:          string
  label:       string
  row_kind:    'line' | 'subtotal' | 'title' | 'kpi' | 'kpi_header' | 'account'
  is_bold?:    boolean
  amounts:     Record<string, number>   // keyed by "YYYY-MM"
  has_children: boolean
  children:    MonthlyRow[]
  accounts?:   MonthlyRow[]
}

export interface MonthlyPeriod {
  year:  number
  month: number
  label: string
}

export interface MonthlyTotal {
  key:   string
  label: string
  kind:  'fy' | 'ytd'
  year:  number
}

export interface MonthlyResponse {
  statement: 'pl' | 'bs' | 'cf' | 'wc'
  year:      number
  month:     number
  periods:   MonthlyPeriod[]
  rows:      MonthlyRow[]
  /** Present only when span=fy3: FY and YTD totals to interleave in the monthly view */
  totals?:   MonthlyTotal[]
}

// ─── Weekly breakdown ─────────────────────────────────────────────────────────

export interface WeeklyBreakdownWeek {
  key:   string  // e.g. "W2025-18"
  label: string  // e.g. "CW18"
}

export interface WeeklyBreakdownTotalCol {
  key:   string            // e.g. "M2025-05" or "MTD2025-07"
  label: string            // e.g. "May25" or "MTD Jul25"
  kind:  'month' | 'mtd'
}

export interface WeeklyBreakdownGroup {
  month_key:   string  // e.g. "2025-05"
  month_label: string  // e.g. "May25"
  kind:        'full' | 'partial'
  weeks:       WeeklyBreakdownWeek[]
  total:       WeeklyBreakdownTotalCol
}

export interface WeeklyBreakdownRow {
  id:           string
  label:        string
  row_kind:     'line' | 'subtotal' | 'title' | 'kpi' | 'kpi_header' | 'account'
  is_bold?:     boolean
  amounts:      Record<string, number>
  has_children: boolean
  children:     WeeklyBreakdownRow[]
  accounts?:    WeeklyBreakdownRow[]
}

export interface WeeklyBreakdownResponse {
  statement: 'pl' | 'cf'
  iso_year:  number
  iso_week:  number
  groups:    WeeklyBreakdownGroup[]
  rows:      WeeklyBreakdownRow[]
}

export interface L4TrendPoint {
  label:     string
  current:   number
  previous:  number
  delta_pct: number | null
  date_from: string
  date_to:   string
}

export interface L4TrendResponse {
  series:     L4TrendPoint[]
  col_label:  string
  prev_label: string
}

export interface WcTimelinePoint {
  label:             string
  date:              string
  inventories:       number
  trade_receivables: number
  trade_payables:    number
  other_wc:          number
  twc:               number
  nwc:               number
}

export interface WcTimelineResponse {
  grain:             string
  series:            WcTimelinePoint[]
  avg_twc:           number
  avg_period_start:  string
  avg_period_end:    string
}

async function fetchCompetitorSalesTimeline(opts: {
  subject_company?: string
  primary_company?: string
  end_year: number
  drill?: 'none' | 'group'
  drill_entity?: string[]
}): Promise<CompetitorSalesTimelineResponse> {
  const base = getApiBaseUrl()
  const root = base.endsWith('/') ? base.slice(0, -1) : base
  const u = new URL(`${root}/api/v1/benchmark/competitor-sales-timeline`)
  u.searchParams.set('end_year', String(opts.end_year))
  if (opts.subject_company) u.searchParams.set('subject_company', opts.subject_company)
  if (opts.primary_company) u.searchParams.set('primary_company', opts.primary_company)
  if (opts.drill) u.searchParams.set('drill', opts.drill)
  for (const d of opts.drill_entity ?? []) {
    if (d) u.searchParams.append('drill_entity', d)
  }
  let res: Response
  try {
    res = await fetch(u.toString(), { headers: authHeaders(), signal: apiFetchSignal() })
  } catch (err) {
    throw new Error(fetchErrorMessage(err, base))
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`API error ${res.status}: ${body || res.statusText}`)
  }
  return res.json() as Promise<CompetitorSalesTimelineResponse>
}

// ─── API calls ───────────────────────────────────────────────────────────────

export const api = {
  /** FastAPI up and Postgres accepts a query (HTTP 200; check `database` when `status` is `degraded`). */
  healthReady: (): Promise<{ status: string; database?: string; detail?: string }> =>
    get('/health/ready'),

  entities: (): Promise<Entity[]> =>
    get('/api/v1/entities'),

  kpiTotals: (entity?: string, dateFrom?: string, dateTo?: string): Promise<{ metric: string; grain: string; data: KpiTotals }> =>
    get('/api/v1/metrics', { metric: 'kpi_totals', entity, date_from: dateFrom, date_to: dateTo }),

  timeSeries: (metric: string, entity?: string, dateFrom?: string, dateTo?: string, grain = 'month'): Promise<TimeSeries> =>
    get('/api/v1/metrics', { metric, entity, date_from: dateFrom, date_to: dateTo, grain }),

  arAging: (entity?: string): Promise<AgingResponse> =>
    get('/api/v1/metrics', { metric: 'ar_aging', entity }),

  apAging: (entity?: string): Promise<AgingResponse> =>
    get('/api/v1/metrics', { metric: 'ap_aging', entity }),

  ebitTable: (year: number, month: number, entity?: string): Promise<{ metric: string; data: EbitTableData }> =>
    get('/api/v1/metrics', { metric: 'ebit_table', year, month, entity }),

  latestPeriod: (entity?: string): Promise<{
    metric: string
    period: string
    year?: number | null
    month?: number | null
    iso_year?: number | null
    iso_week?: number | null
  }> => get('/api/v1/metrics', { metric: 'latest_period', entity }),

  cockpitMonth: (year: number, month: number, entity?: string): Promise<{ metric: string; data: CockpitMonthData }> =>
    get('/api/v1/metrics', { metric: 'cockpit_month', year, month, entity, period_grain: 'month' }),

  cockpitPeriod: (
    params:
      | { period_grain: 'month'; year: number; month: number; entity?: string }
      | { period_grain: 'week'; iso_year: number; iso_week: number; year?: number; month?: number; entity?: string },
  ): Promise<{ metric: string; data: CockpitMonthData }> => {
    const metric = params.period_grain === 'week' ? 'cockpit_week' : 'cockpit_month'
    return get('/api/v1/metrics', { metric, entity: params.entity, ...params })
  },

  ebitTablePeriod: (
    params:
      | { period_grain: 'month'; year: number; month: number; entity?: string }
      | { period_grain: 'week'; iso_year: number; iso_week: number; year?: number; month?: number; entity?: string },
  ): Promise<{ metric: string; data: EbitTableData }> =>
    get('/api/v1/metrics', { metric: 'ebit_table', entity: params.entity, ...params }),

  wcRatios: (year: number, month: number, grain: string, entity?: string): Promise<{ metric: string; data: WcRatiosData }> =>
    get('/api/v1/metrics', { metric: 'wc_ratios', year, month, grain, entity }),

  trendChart: (year: number, month: number, kpi: string, grain: string, entity?: string): Promise<{ metric: string; data: TrendChartData }> =>
    get('/api/v1/metrics', { metric: 'trend_chart', year, month, kpi, grain, entity }),

  topCustomers: (year: number, month: number, entity?: string): Promise<{ metric: string; data: TopCustomerData }> =>
    get('/api/v1/metrics', { metric: 'top_customers', year, month, entity }),

  topSuppliers: (year: number, month: number, entity?: string): Promise<{ metric: string; data: TopCustomerData }> =>
    get('/api/v1/metrics', { metric: 'top_suppliers', year, month, entity }),

  topCustomerProfiles: (
    year: number,
    month: number,
    entity?: string,
    rankOpts: TopCustomerProfileRankOpts = { rankFrom: 1, rankTo: 5 },
  ): Promise<TopCustomerProfilesResult> =>
    fetchTopCustomerProfilesWithFallback(year, month, entity, rankOpts),

  topCustomerGroupSalesTimeline: (
    year: number,
    month: number,
    entity?: string,
    opts?: { months?: number; drillGroup?: TopCustomerGroupDrillKey | null },
  ): Promise<{ metric: string; data: TopCustomerGroupSalesTimelineData }> =>
    fetchTopCustomerGroupSalesTimelineWithFallback(year, month, entity, opts),

  customerRevenueNews: (
    year: number,
    month: number,
    entity?: string,
    subject?: string,
  ): Promise<CustomerRevenueNewsResponse> =>
    fetchCustomerRevenueNewsWithFallback(year, month, entity, subject),

  supplierSpendNews: (
    year: number,
    month: number,
    entity?: string,
    subject?: string,
  ): Promise<SupplierSpendNewsResponse> =>
    fetchSupplierSpendNewsWithFallback(year, month, entity, subject),

  topSupplierProfiles: (
    year: number,
    month: number,
    entity?: string,
    rankOpts: TopCustomerProfileRankOpts = { rankFrom: 1, rankTo: 5 },
  ): Promise<TopCustomerProfilesResult> =>
    fetchTopSupplierProfilesWithFallback(year, month, entity, rankOpts),

  topSupplierGroupSpendTimeline: (
    year: number,
    month: number,
    entity?: string,
    opts?: { months?: number; drillGroup?: TopCustomerGroupDrillKey | null },
  ): Promise<{ metric: string; data: TopCustomerGroupSalesTimelineData }> =>
    fetchTopSupplierGroupSpendTimelineWithFallback(year, month, entity, opts),

  splitChart: (year: number, month: number, chartType: 'net_sales' | 'cogs', entity?: string): Promise<{ metric: string; data: SplitChartData }> =>
    get('/api/v1/metrics', { metric: 'split_chart', year, month, kpi: chartType, entity }),

  dupont: (year: number, month: number, entity?: string): Promise<{ metric: string; data: DuPontData }> =>
    get('/api/v1/metrics', { metric: 'dupont', year, month, entity }),

  financialsOverview: (
    p: FinPeriodParams,
    opts?: { includeHighlights?: boolean },
  ): Promise<FinancialsOverviewResponse> =>
    get(
      '/api/v1/financials/overview',
      {
        ...finPeriodQuery(p),
        entity: p.entity,
        include_highlights: opts?.includeHighlights ? 'true' : 'false',
      },
      { timeoutMs: FINANCIALS_OVERVIEW_TIMEOUT_MS },
    ),

  financialsOverviewHighlights: (
    p: FinPeriodParams,
  ): Promise<{ highlights: OverviewHighlight[] }> =>
    get('/api/v1/financials/overview/highlights', { ...finPeriodQuery(p), entity: p.entity }, {
      timeoutMs: FINANCIALS_OVERVIEW_TIMEOUT_MS,
    }),

  financialsOverviewEntityBreakdown: (
    p: FinPeriodParams,
  ): Promise<FinancialsEntityBreakdownResponse> =>
    get(
      '/api/v1/financials/overview/entity-breakdown',
      { ...finPeriodQuery(p), entity: p.entity, include_narratives: false },
      { timeoutMs: FINANCIALS_OVERVIEW_TIMEOUT_MS },
    ),

  financialsOverviewEntityBreakdownNarratives: (
    p: FinPeriodParams,
  ): Promise<{ areas: FinancialsEntityBreakdownResponse['areas'] }> =>
    get('/api/v1/financials/overview/entity-breakdown/narratives', { ...finPeriodQuery(p), entity: p.entity }, {
      timeoutMs: 30_000,
    }),

  financialsPlStatement: (year: number, month: number, entity?: string): Promise<FinancialStatementResponse> =>
    get('/api/v1/financials/pl-statement', { period_grain: 'month', year, month, entity }),

  financialsPlStatementPeriod: (p: FinPeriodParams): Promise<FinancialStatementResponse> =>
    get('/api/v1/financials/pl-statement', finPeriodQuery(p)),

  financialsPlPlan: (year: number, month: number, entity?: string): Promise<PlPlanResponse> =>
    get('/api/v1/financials/pl-statement/plan', { year, month, entity }),

  financialsPlNarrative: (
    year: number,
    month: number,
    entity?: string,
    opts?: { max_bullets?: number; visible_rows?: number; use_llm?: boolean },
  ): Promise<PlNarrativeResponse> =>
    get('/api/v1/financials/pl-statement/narrative', {
      period_grain: 'month',
      year,
      month,
      entity,
      max_bullets: opts?.max_bullets,
      visible_rows: opts?.visible_rows,
      use_llm: opts?.use_llm === true ? 'true' : 'false',
    }),

  financialsPlNarrativePeriod: (
    p: FinPeriodParams,
    opts?: { max_bullets?: number; visible_rows?: number; use_llm?: boolean },
  ): Promise<PlNarrativeResponse> =>
    get(
      '/api/v1/financials/pl-statement/narrative',
      {
        ...finPeriodQuery(p),
        max_bullets: opts?.max_bullets,
        visible_rows: opts?.visible_rows,
        use_llm: opts?.use_llm === true ? 'true' : 'false',
      },
      { timeoutMs: NARRATIVE_API_TIMEOUT_MS },
    ),

  financialsPlLineDetail: (
    lineCode: string,
    year: number,
    month: number,
    entity?: string,
    opts?: { use_llm?: boolean; line_mom_keur?: number },
  ): Promise<PlLineDetailResponse> =>
    get('/api/v1/financials/pl-line-detail', {
      line_code: lineCode,
      year,
      month,
      entity,
      use_llm: opts?.use_llm === false ? 'false' : 'true',
      ...(opts?.line_mom_keur != null ? { line_mom_keur: opts.line_mom_keur } : {}),
    }),

  financialsBsProvisionRollforward: (
    year: number,
    month: number,
    entity?: string,
  ): Promise<ProvisionRollforwardResponse> =>
    get('/api/v1/financials/balance-sheet/provision-rollforward', { year, month, entity }),

  /** Balance sheet narrative (bs_narrative_v2). */
  financialsBsNarrative: (
    year: number,
    month: number,
    entity?: string,
    opts?: { max_bullets?: number; visible_rows?: number; use_llm?: boolean; force_refresh?: boolean },
  ): Promise<PlNarrativeResponse> =>
    get(
      '/api/v1/financials/balance-sheet/narrative',
      {
        ...finPeriodQuery({ period_grain: 'month', year, month, entity }),
        max_bullets: opts?.max_bullets,
        visible_rows: opts?.visible_rows,
        use_llm: opts?.use_llm === true ? 'true' : 'false',
        force_refresh: opts?.force_refresh ? '1' : undefined,
      },
      { timeoutMs: NARRATIVE_API_TIMEOUT_MS },
    ),

  financialsBsNarrativePeriod: (
    p: FinPeriodParams,
    opts?: { max_bullets?: number; visible_rows?: number; use_llm?: boolean; force_refresh?: boolean },
  ): Promise<PlNarrativeResponse> =>
    get(
      '/api/v1/financials/balance-sheet/narrative',
      {
        ...finPeriodQuery(p),
        max_bullets: opts?.max_bullets,
        visible_rows: opts?.visible_rows,
        use_llm: opts?.use_llm === true ? 'true' : 'false',
        force_refresh: opts?.force_refresh ? '1' : undefined,
      },
      { timeoutMs: NARRATIVE_API_TIMEOUT_MS },
    ),

  financialsBsLineDetail: (
    lineCode: string,
    year: number,
    month: number,
    entity?: string,
    opts?: {
      use_llm?: boolean
      line_mom_keur?: number
      anchor_year?: number
      anchor_month?: number
    },
  ): Promise<PlLineDetailResponse> =>
    get('/api/v1/financials/balance-sheet/line-detail', {
      line_code: lineCode,
      year,
      month,
      entity,
      use_llm: opts?.use_llm === false ? 'false' : 'true',
      ...(opts?.line_mom_keur != null ? { line_mom_keur: opts.line_mom_keur } : {}),
      ...(opts?.anchor_year != null ? { anchor_year: opts.anchor_year } : {}),
      ...(opts?.anchor_month != null ? { anchor_month: opts.anchor_month } : {}),
    }),

  /** Cash flow narrative (cf_narrative_v1). */
  financialsCfNarrative: (
    year: number,
    month: number,
    entity?: string,
    opts?: { max_bullets?: number; visible_rows?: number; use_llm?: boolean; force_refresh?: boolean },
  ): Promise<PlNarrativeResponse> =>
    get(
      '/api/v1/financials/cash-flow/narrative',
      {
        ...finPeriodQuery({ period_grain: 'month', year, month, entity }),
        max_bullets: opts?.max_bullets,
        visible_rows: opts?.visible_rows,
        use_llm: opts?.use_llm === true ? 'true' : 'false',
        force_refresh: opts?.force_refresh ? '1' : undefined,
      },
      { timeoutMs: NARRATIVE_API_TIMEOUT_MS },
    ),

  financialsCfNarrativePeriod: (
    p: FinPeriodParams,
    opts?: { max_bullets?: number; visible_rows?: number; use_llm?: boolean; force_refresh?: boolean },
  ): Promise<PlNarrativeResponse> =>
    get(
      '/api/v1/financials/cash-flow/narrative',
      {
        ...finPeriodQuery(p),
        max_bullets: opts?.max_bullets,
        visible_rows: opts?.visible_rows,
        use_llm: opts?.use_llm === true ? 'true' : 'false',
        force_refresh: opts?.force_refresh ? '1' : undefined,
      },
      { timeoutMs: NARRATIVE_API_TIMEOUT_MS },
    ),

  financialsCfLineDetail: (
    lineCode: string,
    year: number,
    month: number,
    entity?: string,
    opts?: { use_llm?: boolean; line_mom_keur?: number },
  ): Promise<PlLineDetailResponse> =>
    get('/api/v1/financials/cash-flow/line-detail', {
      line_code: lineCode,
      year,
      month,
      entity,
      use_llm: opts?.use_llm === false ? 'false' : 'true',
      ...(opts?.line_mom_keur != null ? { line_mom_keur: opts.line_mom_keur } : {}),
    }),

  /** Working capital narrative (wc_narrative_v2). */
  financialsWcNarrative: (
    year: number,
    month: number,
    entity?: string,
    opts?: { max_bullets?: number; visible_rows?: number; use_llm?: boolean; force_refresh?: boolean },
  ): Promise<PlNarrativeResponse> =>
    get(
      '/api/v1/financials/working-capital/narrative',
      {
        ...finPeriodQuery({ period_grain: 'month', year, month, entity }),
        max_bullets: opts?.max_bullets,
        visible_rows: opts?.visible_rows,
        use_llm: opts?.use_llm === true ? 'true' : 'false',
        force_refresh: opts?.force_refresh ? '1' : undefined,
      },
      { timeoutMs: NARRATIVE_API_TIMEOUT_MS },
    ),

  financialsWcNarrativePeriod: (
    p: FinPeriodParams,
    opts?: { max_bullets?: number; visible_rows?: number; use_llm?: boolean; force_refresh?: boolean },
  ): Promise<PlNarrativeResponse> =>
    get(
      '/api/v1/financials/working-capital/narrative',
      {
        ...finPeriodQuery(p),
        max_bullets: opts?.max_bullets,
        visible_rows: opts?.visible_rows,
        use_llm: opts?.use_llm === true ? 'true' : 'false',
        force_refresh: opts?.force_refresh ? '1' : undefined,
      },
      { timeoutMs: NARRATIVE_API_TIMEOUT_MS },
    ),

  financialsWcLineDetail: (
    lineCode: string,
    year: number,
    month: number,
    entity?: string,
    opts?: {
      use_llm?: boolean
      line_mom_keur?: number
      anchor_year?: number
      anchor_month?: number
    },
  ): Promise<PlLineDetailResponse> =>
    get('/api/v1/financials/working-capital/line-detail', {
      line_code: lineCode,
      year,
      month,
      entity,
      use_llm: opts?.use_llm === false ? 'false' : 'true',
      ...(opts?.line_mom_keur != null ? { line_mom_keur: opts.line_mom_keur } : {}),
      ...(opts?.anchor_year != null ? { anchor_year: opts.anchor_year } : {}),
      ...(opts?.anchor_month != null ? { anchor_month: opts.anchor_month } : {}),
    }),

  financialsJournalEntryByBooking: (bookingLineId: number): Promise<{
    legal_entity_code: string
    fiscal_year: number
    journal_entry_number: string
    posting_date: string
    reference?: string | null
    entry_note?: string | null
    debits: Array<{
      booking_line_id: number
      gl_account_id: string
      account_name: string
      amount_keur: number
      line_note?: string | null
    }>
    credits: Array<{
      booking_line_id: number
      gl_account_id: string
      account_name: string
      amount_keur: number
      line_note?: string | null
    }>
  }> => get('/api/v1/financials/journal-entry-by-booking', { booking_line_id: bookingLineId }),

  financialsBalanceSheet: (year: number, month: number, entity?: string): Promise<FinancialStatementResponse> =>
    get('/api/v1/financials/balance-sheet', { period_grain: 'month', year, month, entity }),

  financialsBalanceSheetPeriod: (p: FinPeriodParams): Promise<FinancialStatementResponse> =>
    get('/api/v1/financials/balance-sheet', finPeriodQuery(p)),

  financialsCashFlow: (year: number, month: number, entity?: string): Promise<FinancialStatementResponse> =>
    get('/api/v1/financials/cash-flow', { period_grain: 'month', year, month, entity }),

  financialsCashFlowPeriod: (p: FinPeriodParams): Promise<FinancialStatementResponse> =>
    get('/api/v1/financials/cash-flow', finPeriodQuery(p)),

  financialsWorkingCapital: (year: number, month: number, entity?: string): Promise<FinancialStatementResponse> =>
    get('/api/v1/financials/working-capital', { period_grain: 'month', year, month, entity }),

  financialsWorkingCapitalPeriod: (p: FinPeriodParams): Promise<FinancialStatementResponse> =>
    get('/api/v1/financials/working-capital', finPeriodQuery(p)),

  financialsPlConsolidation: (p: FinPeriodParams): Promise<ConsolidationResponse> =>
    get('/api/v1/financials/pl-statement/consolidation', finPeriodQuery(p)),

  financialsBsConsolidation: (p: FinPeriodParams): Promise<ConsolidationResponse> =>
    get('/api/v1/financials/balance-sheet/consolidation', finPeriodQuery(p)),

  financialsCfConsolidation: (p: FinPeriodParams): Promise<ConsolidationResponse> =>
    get('/api/v1/financials/cash-flow/consolidation', finPeriodQuery(p)),

  financialsWcConsolidation: (p: FinPeriodParams): Promise<ConsolidationResponse> =>
    get('/api/v1/financials/working-capital/consolidation', finPeriodQuery(p)),

  financialsPlMonthly: (p: FinPeriodParams, opts?: { span?: string }): Promise<MonthlyResponse> =>
    get('/api/v1/financials/pl-statement/monthly', { ...finPeriodQuery(p), span: opts?.span }),

  financialsPlWeekly: (isoYear: number, isoWeek: number, entity?: string): Promise<WeeklyBreakdownResponse> =>
    get('/api/v1/financials/pl-statement/weekly', { iso_year: isoYear, iso_week: isoWeek, entity }),

  financialsCfWeekly: (isoYear: number, isoWeek: number, entity?: string): Promise<WeeklyBreakdownResponse> =>
    get('/api/v1/financials/cash-flow/weekly', { iso_year: isoYear, iso_week: isoWeek, entity }),

  financialsBsMonthly: (
    year: number,
    month: number,
    entity?: string,
    opts?: { span?: string },
  ): Promise<MonthlyResponse> =>
    get('/api/v1/financials/balance-sheet/monthly', { year, month, entity, span: opts?.span }),

  financialsCfMonthly: (
    year: number,
    month: number,
    entity?: string,
    opts?: { span?: string },
  ): Promise<MonthlyResponse> =>
    get('/api/v1/financials/cash-flow/monthly', { year, month, entity, span: opts?.span }),

  financialsWcMonthly: (
    year: number,
    month: number,
    entity?: string,
    opts?: { span?: string },
  ): Promise<MonthlyResponse> =>
    get('/api/v1/financials/working-capital/monthly', { year, month, entity, span: opts?.span }),

  financialsL4Trend: (
    stmt: string, year: number, month: number, grain: string,
    level_2?: string, level_3?: string, level_4?: string, entity?: string,
  ): Promise<L4TrendResponse> =>
    get(`/api/v1/financials/${stmt}/l4-trend`, { year, month, grain, level_2, level_3, level_4, entity }),

  financialsWcTimeline: (year: number, month: number, grain: string, entity?: string): Promise<WcTimelineResponse> =>
    get('/api/v1/financials/working-capital/wc-timeline', { year, month, grain, entity }),

  // ─── Exit Readiness ─────────────────────────────────────────────────────────
  exitReadinessPlStatement: (year: number, month: number, entity?: string): Promise<ErFlowResponse> =>
    get('/api/v1/exit-readiness/pl-statement', { year, month, entity }),

  exitReadinessCashFlow: (year: number, month: number, entity?: string): Promise<ErFlowResponse> =>
    get('/api/v1/exit-readiness/cash-flow', { year, month, entity }),

  exitReadinessBalanceSheet: (year: number, month: number, entity?: string): Promise<ErSnapshotResponse> =>
    get('/api/v1/exit-readiness/balance-sheet', { year, month, entity }),

  exitReadinessWorkingCapital: (year: number, month: number, entity?: string): Promise<ErSnapshotResponse> =>
    get('/api/v1/exit-readiness/working-capital', { year, month, entity }),

  exitReadinessPlConsolidation: (year: number, month: number, entity?: string): Promise<ConsolidationResponse> =>
    get('/api/v1/exit-readiness/pl-consolidation', { year, month, entity }),

  // ─── Benchmark ──────────────────────────────────────────────────────────────
  benchmarkSources: (): Promise<BenchmarkSource[]> =>
    get('/api/v1/benchmark/sources', {}),

  benchmarkMetadata: (): Promise<BenchmarkMetadata> =>
    get('/api/v1/benchmark/metadata', {}),

  benchmarkEntities: (internal_only?: boolean): Promise<BenchmarkEntity[]> =>
    get('/api/v1/benchmark/entities', { internal_only: internal_only ? 1 : 0 }),

  benchmarkFilterOptions: (): Promise<BenchmarkFilterOptions> =>
    get('/api/v1/benchmark/filter-options', {}),

  benchmarkPeerOverview: (
    company_name: string,
    year: number,
    peer_mode?: 'strict' | 'balanced' | 'broad',
    filters?: BenchmarkFilters,
  ): Promise<BenchmarkPeerOverview> =>
    get('/api/v1/benchmark/peer-overview', {
      company_name,
      year,
      peer_mode: peer_mode ?? 'balanced',
      ...filters,
    }),

  benchmarkKpiComparison: (
    company_name: string,
    year: number,
    metric?: 'revenue_keur' | 'ebitda_keur' | 'ebitda_margin_pct' | 'growth_yoy_pct',
    peer_mode?: 'strict' | 'balanced' | 'broad',
    source_tier?: 'TIER1' | 'TIER2' | 'TIER3',
    filters?: BenchmarkFilters,
  ): Promise<BenchmarkKpiComparison> =>
    get('/api/v1/benchmark/kpi-comparison', {
      company_name,
      year,
      metric: metric ?? 'revenue_keur',
      peer_mode: peer_mode ?? 'balanced',
      source_tier,
      ...filters,
    }),

  benchmarkKpiMulti: (
    company_name: string,
    year: number,
    peer_mode?: 'strict' | 'balanced' | 'broad',
    filters?: BenchmarkFilters,
  ): Promise<BenchmarkKpiMultiResponse> =>
    get('/api/v1/benchmark/kpi-comparison-multi', {
      company_name,
      year,
      peer_mode: peer_mode ?? 'balanced',
      ...filters,
    }),

  benchmarkDistribution: (
    company_name: string,
    year: number,
    metric?: 'revenue_keur' | 'ebitda_keur' | 'ebitda_margin_pct' | 'growth_yoy_pct',
    peer_mode?: 'strict' | 'balanced' | 'broad',
    source_tier?: 'TIER1' | 'TIER2' | 'TIER3',
    filters?: BenchmarkFilters,
  ): Promise<BenchmarkDistributionRow[]> =>
    get('/api/v1/benchmark/peer-distribution', {
      company_name,
      year,
      metric: metric ?? 'revenue_keur',
      peer_mode: peer_mode ?? 'balanced',
      source_tier,
      ...filters,
    }),

  benchmarkPeerMetrics: (
    company_name: string,
    year: number,
    peer_mode?: 'strict' | 'balanced' | 'broad',
    filters?: BenchmarkFilters,
  ): Promise<BenchmarkPeerMetricRow[]> =>
    get('/api/v1/benchmark/peer-metrics', {
      company_name,
      year,
      peer_mode: peer_mode ?? 'balanced',
      ...filters,
    }),

  benchmarkGroupComparison: (
    year: number,
    metric?: 'revenue_keur' | 'ebitda_keur' | 'ebitda_margin_pct' | 'growth_yoy_pct',
    filters?: BenchmarkFilters,
  ): Promise<BenchmarkGroupComparison> =>
    get('/api/v1/benchmark/group-comparison', {
      year,
      metric: metric ?? 'revenue_keur',
      ...filters,
    }),

  benchmarkGroupKpiMulti: (
    year: number,
    peer_mode?: 'strict' | 'balanced' | 'broad',
    filters?: BenchmarkFilters,
  ): Promise<BenchmarkGroupKpiMultiResponse> =>
    get('/api/v1/benchmark/group-kpi-multi', {
      year,
      peer_mode: peer_mode ?? 'balanced',
      ...filters,
    }),

  benchmarkGroupPeerMetrics: (
    year: number,
    peer_mode?: 'strict' | 'balanced' | 'broad',
    filters?: BenchmarkFilters,
  ): Promise<BenchmarkPeerMetricRow[]> =>
    get('/api/v1/benchmark/group-peer-metrics', {
      year,
      peer_mode: peer_mode ?? 'balanced',
      ...filters,
    }),

  benchmarkGroupPeerTrend: (
    company_name?: string,
    filters?: BenchmarkFilters,
  ): Promise<BenchmarkGroupPeerTrendRow[]> =>
    get('/api/v1/benchmark/group-peer-trend', {
      company_name,
      ...filters,
    }),

  benchmarkScatterRevenueEbitda: (
    company_name: string | undefined,
    year: number,
    filters?: BenchmarkFilters,
    subject_mode: 'company' | 'internal_group' = 'company',
  ): Promise<BenchmarkScatterPoint[]> =>
    get('/api/v1/benchmark/scatter-revenue-ebitda', {
      company_name: subject_mode === 'internal_group' ? undefined : company_name,
      year,
      subject_mode,
      ...filters,
    }),

  benchmarkMarketKpis: (
    industry_tag?: string,
    start_year?: number,
    end_year?: number,
  ): Promise<BenchmarkMarketKpis> =>
    get('/api/v1/benchmark/market-kpis', {
      industry_tag,
      start_year,
      end_year,
    }),

  benchmarkMarketForecast: (
    industry_tag?: string,
    historical_start_year?: number,
    historical_end_year?: number,
    forecast_years?: number,
  ): Promise<BenchmarkMarketForecastRow[]> =>
    get('/api/v1/benchmark/market-forecast', {
      industry_tag,
      historical_start_year,
      historical_end_year,
      forecast_years,
    }),

  benchmarkGdpBubble: (): Promise<BenchmarkGdpBubbleRow[]> =>
    get('/api/v1/benchmark/gdp-bubble', {}),

  benchmarkGdpTrend: (
    end_historic_year?: number,
    forecast_years?: number,
  ): Promise<BenchmarkGdpTrendRow[]> =>
    get('/api/v1/benchmark/gdp-trend', {
      end_historic_year,
      forecast_years,
    }),

  benchmarkGdpInsight: (): Promise<BenchmarkGdpInsight> =>
    get('/api/v1/benchmark/gdp-insight', {}),

  benchmarkCompetitorProfiles: (
    subject_company?: string,
    year?: number,
    extra?: { primary_company?: string },
  ): Promise<BenchmarkCompetitorProfileComparison> =>
    get('/api/v1/benchmark/competitor-profile-comparison', {
      subject_company,
      primary_company: extra?.primary_company,
      year,
    }),

  benchmarkCompetitorPerformance: (
    subject_company?: string,
    end_year?: number,
    extra?: { primary_company?: string },
  ): Promise<BenchmarkCompetitorPerformanceComparison> =>
    get('/api/v1/benchmark/competitor-performance-comparison', {
      subject_company,
      primary_company: extra?.primary_company,
      end_year,
    }),

  benchmarkSearchInterestComparison: (
    subject_company?: string,
  ): Promise<BenchmarkSearchInterestComparison> =>
    get('/api/v1/benchmark/search-interest-comparison', {
      subject_company,
    }),

  benchmarkCompetitorDiscovery: (
    company_name: string,
    limit?: number,
  ): Promise<BenchmarkCompetitorDiscoveryResponse> =>
    get('/api/v1/benchmark/competitor-discovery', {
      company_name,
      limit: limit ?? 20,
    }),

  benchmarkCompetitorSalesTimeline: (opts: {
    subject_company?: string
    primary_company?: string
    end_year: number
    drill?: 'none' | 'group'
    drill_entity?: string[]
  }) => fetchCompetitorSalesTimeline(opts),

  benchmarkCompetitorCompanyNews: (
    subject_company?: string,
    primary_company?: string,
    end_year?: number,
    subject?: string,
  ): Promise<CompetitorCompanyNewsResponse> =>
    get('/api/v1/benchmark/competitor-company-news', {
      subject_company,
      primary_company,
      end_year,
      subject,
    }),

  industryInsightsDashboard: (
    pack?: string,
    company?: string,
    opts?: { investmentsOffset?: number; workforceOffset?: number; snippetPageSize?: number },
  ): Promise<IndustryInsightsDashboard> =>
    get('/api/v1/industry-insights/dashboard', {
      pack: pack ?? 'real_estate_construction',
      company: company != null && company !== '' ? company : undefined,
      inv_offset: opts?.investmentsOffset,
      wf_offset: opts?.workforceOffset,
      snippet_page_size: opts?.snippetPageSize,
    }),

  industryInsightsSnippets: (args: {
    pack?: string
    block: 'investments' | 'workforce'
    offset: number
    limit?: number
  }): Promise<IndustryInsightsSnippetsResponse> =>
    get('/api/v1/industry-insights/snippets', {
      pack: args.pack ?? 'real_estate_construction',
      block: args.block,
      offset: args.offset,
      limit: args.limit ?? 12,
    }),

  // ─── Sales ──────────────────────────────────────────────────────────────────
  salesHeadlineKpis: async (
    year: number,
    month: number,
    filters?: SalesFilters,
    period?: { period_grain?: string; iso_year?: number; iso_week?: number },
  ): Promise<SalesHeadlineKpis> => {
    const fp = salesFilterQueryParams(filters)
    const periodParams = {
      period_grain: period?.period_grain ?? 'month',
      iso_year: period?.iso_year,
      iso_week: period?.iso_week,
    }
    const params = { year, month, ...periodParams, ...fp }
    const kpiParams = { year, month, method: 'invoiced', ...fp }
    try {
      const kpi = await get<SalesKpiCards>('/api/v1/sales/kpi-cards', kpiParams)
      if (kpi.headline_kpis) return kpi.headline_kpis
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      if (!msg.includes('404')) throw e
    }
    const paths = [
      '/api/v1/sales-profitability-kpis',
      '/api/v1/sales/headline-kpis',
      '/api/v1/sales/kpi-cards/headline',
    ]
    let lastErr: unknown = null
    for (const path of paths) {
      try {
        return await get<SalesHeadlineKpis>(path, params)
      } catch (e) {
        lastErr = e
        const msg = e instanceof Error ? e.message : String(e)
        if (!msg.includes('404')) throw e
      }
    }
    throw lastErr instanceof Error ? lastErr : new Error('Sales headline KPIs unavailable')
  },

  salesKpiCards: (year: number, month: number, method?: string, filters?: SalesFilters): Promise<SalesKpiCards> =>
    get('/api/v1/sales/kpi-cards', { year, month, method: method ?? 'invoiced', ...salesFilterQueryParams(filters) }),

  salesOverviewNarrative: (
    year: number,
    month: number,
    entity?: string,
    use_llm?: boolean,
  ): Promise<SalesOverviewNarrative> =>
    get('/api/v1/sales/overview/narrative', {
      year,
      month,
      entity,
      use_llm: use_llm ? 'true' : undefined,
    }),

  salesTopOrders: (
    year: number,
    month: number,
    filters?: SalesFilters,
    opts?: { period_grain?: string; iso_year?: number; iso_week?: number; limit?: number },
  ): Promise<SalesTopOrder[]> =>
    get('/api/v1/sales/top-orders', {
      year,
      month,
      period_grain: opts?.period_grain ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
      limit: opts?.limit ?? 20,
      ...salesFilterQueryParams(filters),
    }),

  salesTopEntities: (
    year: number,
    month: number,
    type?: string,
    rank_by?: string,
    method?: string,
    filters?: SalesFilters,
    opts?: { period_grain?: string; iso_year?: number; iso_week?: number; limit?: number },
  ): Promise<SalesTopEntitiesResponse> =>
    get('/api/v1/sales/top-entities', {
      year,
      month,
      period_grain: opts?.period_grain ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
      type: type ?? 'customer',
      rank_by: rank_by ?? 'cm',
      method: method ?? 'invoiced',
      limit: opts?.limit ?? 5000,
      ...salesFilterQueryParams(filters),
    }),

  salesCohortChart: (year: number, method?: string, filters?: SalesFilters): Promise<SalesCohortResponse> =>
    get('/api/v1/sales/cohort-chart', { year, method: method ?? 'invoiced', ...salesFilterQueryParams(filters) }),

  salesRecurringSplit: (year: number, month: number, grain?: string, method?: string, filters?: SalesFilters): Promise<SalesRecurringSplitResponse> =>
    get('/api/v1/sales/recurring-split', {
      year, month, grain: grain ?? 'year', method: method ?? 'invoiced', ...salesFilterQueryParams(filters),
    }),

  salesFilterOptions: (): Promise<Record<string, string[]>> =>
    get('/api/v1/sales/filter-options', {}),

  salesChurnBridge: (
    year: number,
    month: number,
    grain?: string,
    dim?: string,
    method?: string,
    filters?: SalesFilters,
  ): Promise<SalesChurnBridgeResponse> =>
    get('/api/v1/sales/churn-bridge', {
      year,
      month,
      grain: grain ?? 'month',
      dim: dim ?? 'entity',
      method: method ?? 'invoiced',
      ...salesFilterQueryParams(filters),
    }),

  salesChurnDrilldown: (
    from_start: string, from_end: string, to_start: string, to_end: string,
    component: string, method?: string, filters?: SalesFilters,
  ): Promise<SalesChurnDrillRow[]> =>
    get('/api/v1/sales/churn-bridge/drilldown', {
      from_start, from_end, to_start, to_end, component, method: method ?? 'invoiced', ...salesFilterQueryParams(filters),
    }),

  salesGeoCountries: (year: number, month: number, method?: string, filters?: SalesFilters): Promise<SalesGeoCountry[]> =>
    get('/api/v1/sales/geography/countries', { year, month, method: method ?? 'invoiced', ...salesFilterQueryParams(filters) }),

  salesGeoCountryLocations: (
    country: string,
    year: number,
    month: number,
    filters?: SalesFilters,
    opts?: { period_grain?: string; iso_year?: number; iso_week?: number },
  ): Promise<SalesGeoCountryLocationsResponse> =>
    get('/api/v1/sales/geography/country-locations', {
      country,
      year,
      month,
      period_grain: opts?.period_grain ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
      method: 'invoiced',
      ...salesFilterQueryParams(filters),
    }),

  salesGrossMarginMatrix: (
    year: number,
    month: number,
    dim?: string,
    filters?: SalesFilters,
    opts?: { period_grain?: string; iso_year?: number; iso_week?: number },
  ): Promise<SalesGrossMarginMatrixResponse> =>
    get('/api/v1/sales/gross-margin-matrix', {
      year,
      month,
      dim: dim ?? 'entity',
      period_grain: opts?.period_grain ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
      method: 'invoiced',
      ...salesFilterQueryParams(filters),
    }),

  salesDimensionPerformance: (
    year: number,
    month: number,
    dim?: string,
    metric?: SalesDimensionPerformanceMetric,
    periodScope?: SalesDimensionPeriodScope,
    filters?: SalesFilters,
    opts?: { period_grain?: string; iso_year?: number; iso_week?: number },
  ): Promise<SalesDimensionPerformanceResponse> =>
    get('/api/v1/sales/analytics/dimension-performance', {
      year,
      month,
      dim: dim ?? 'entity',
      metric: metric ?? 'gross_sales',
      period_scope: periodScope ?? 'month',
      period_grain: opts?.period_grain ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
      method: 'invoiced',
      ...salesFilterQueryParams(filters),
    }),

  salesGeoBreakdown: (year: number, month: number, dim?: string, method?: string, filters?: SalesFilters): Promise<SalesGeoBreakdownRow[]> =>
    get('/api/v1/sales/geography/breakdown', {
      year, month, dim: dim ?? 'customer_markets', method: method ?? 'invoiced', ...salesFilterQueryParams(filters),
    }),

  salesBreakdownTable: (
    year: number,
    month: number,
    dims: { dim_top: string; dim_mid?: string; dim_bottom: string },
    filters?: SalesFilters,
    opts?: { period_grain?: string; iso_year?: number; iso_week?: number },
  ): Promise<SalesBreakdownResponse> =>
    get('/api/v1/sales/analytics/breakdown-table', {
      year,
      month,
      dim_top: dims.dim_top,
      dim_mid: dims.dim_mid ?? '',
      dim_bottom: dims.dim_bottom,
      period_grain: opts?.period_grain ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
      method: 'invoiced',
      ...salesFilterQueryParams(filters),
    }),

  salesGeoTrend: (year: number, month: number, grain?: string, dim?: string, method?: string, filters?: SalesFilters): Promise<SalesGeoTrendResponse> =>
    get('/api/v1/sales/geography/trend', {
      year, month, grain: grain ?? 'month', dim: dim ?? 'end_customer_region', method: method ?? 'invoiced',
      ...salesFilterQueryParams(filters),
    }),

  salesGrossSalesTrend: (
    year: number,
    month: number,
    dim?: string,
    filters?: SalesFilters,
    opts?: { period_grain?: string; iso_year?: number; iso_week?: number },
  ): Promise<SalesGrossSalesTrendResponse> =>
    get('/api/v1/sales/analytics/gross-sales-trend', {
      year,
      month,
      dim: dim ?? 'end_customer_region',
      period_grain: opts?.period_grain ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
      method: 'invoiced',
      ...salesFilterQueryParams(filters),
    }),

  salesProfitMarginScatter: (
    year: number,
    month: number,
    dim?: string,
    filters?: SalesFilters,
    opts?: { period_grain?: string; iso_year?: number; iso_week?: number },
  ): Promise<SalesProfitMarginScatterResponse> =>
    get('/api/v1/sales/analytics/profit-margin-scatter', {
      year,
      month,
      dim: dim ?? 'end_customer_region',
      period_grain: opts?.period_grain ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
      method: 'invoiced',
      ...salesFilterQueryParams(filters),
    }),

  salesMetricBridge: (
    year: number,
    month: number,
    dim?: string,
    opts?: {
      period_grain?: string
      iso_year?: number
      iso_week?: number
      metric?: string
    },
    filters?: SalesFilters,
  ): Promise<SalesMetricBridgeResponse> =>
    get('/api/v1/sales/analytics/metric-bridge', {
      year,
      month,
      dim: dim ?? 'end_customer_region',
      period_grain: opts?.period_grain ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
      metric: opts?.metric ?? 'gross_sales',
      method: 'invoiced',
      ...salesFilterQueryParams(filters),
    }),

  salesCompositionBreakdown: (
    year: number,
    month: number,
    opts?: {
      period_grain?: string
      iso_year?: number
      iso_week?: number
      metric?: string
      dims?: string
      top_n?: number
    },
    filters?: SalesFilters,
  ): Promise<SalesCompositionBreakdownResponse> =>
    get('/api/v1/sales/analytics/composition-breakdown', {
      year,
      month,
      period_grain: opts?.period_grain ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
      metric: opts?.metric ?? 'gross_sales',
      dims: opts?.dims,
      top_n: opts?.top_n,
      method: 'invoiced',
      ...salesFilterQueryParams(filters),
    }),

  salesChurnDimBreakdown: (year: number, month: number, grain?: string, dim?: string, method?: string, filters?: SalesFilters): Promise<SalesChurnDimRow[]> =>
    get('/api/v1/sales/churn/dimension-breakdown', {
      year, month, grain: grain ?? 'month', dim: dim ?? 'entity', method: method ?? 'invoiced', ...salesFilterQueryParams(filters),
    }),

  salesPvmBridge: (
    year: number,
    month: number,
    dim?: string,
    method?: string,
    revenue_model?: string,
    filters?: SalesFilters,
  ): Promise<SalesPvmBridgeResponse> =>
    get('/api/v1/sales/product-mix/pvm-bridge', {
      year,
      month,
      dim: dim ?? 'product_family',
      method: method ?? 'invoiced',
      revenue_model: revenue_model ?? '',
      ...salesFilterQueryParams(filters),
    }),

  salesMarginTable: (year: number, month: number, dim?: string, method?: string, revenue_model?: string, filters?: SalesFilters): Promise<SalesMarginRow[]> =>
    get('/api/v1/sales/product-mix/margin-table', {
      year, month, dim: dim ?? 'entity', method: method ?? 'invoiced', revenue_model: revenue_model ?? '',
      ...salesFilterQueryParams(filters),
    }),

  salesDeferrals: (year: number, month: number, dim?: string, method?: string, filters?: SalesFilters): Promise<{ deferred_revenue: SalesDeferralRow[]; deferred_expenses: SalesDeferralRow[] }> =>
    get('/api/v1/sales/deferrals', { year, month, dim: dim ?? 'entity', method: method ?? 'invoiced', ...salesFilterQueryParams(filters) }),

  salesDeferralTimeline: (year: number, month: number, as_of_date?: string, method?: string, filters?: SalesFilters): Promise<SalesDeferralTimelineEntry[]> =>
    get('/api/v1/sales/deferrals/timeline', {
      year, month, as_of_date: as_of_date ?? `${year}-${String(month).padStart(2,'0')}-01`, method: method ?? 'invoiced',
      ...salesFilterQueryParams(filters),
    }),

  salesReceivablesAging: (year: number, month: number, entity?: string) =>
    get<ReceivablesAgingResponse>('/api/v1/sales/receivables-aging', { year, month, entity }),

  salesReceivablesDrilldown: (
    band: string,
    year: number,
    month: number,
    entity?: string,
    limit?: number,
  ) =>
    get<{
      band: string
      year: number
      month: number
      as_of: string
      reconciliation_mode: string
      rows: ReceivablesDrillRow[]
    }>('/api/v1/sales/receivables-aging/drilldown', { band, year, month, entity, limit: limit ?? 100 }),

  salesReceivablesPortfolioTable: (
    dimension: ReceivablesAgingDimension,
    year: number,
    month: number,
    entity?: string,
    rowsPerBucket?: number,
  ) =>
    get<AgingPortfolioTableResponse>('/api/v1/sales/receivables-aging/portfolio-table', {
      dimension,
      year,
      month,
      entity,
      rows_per_bucket: rowsPerBucket ?? 25,
    }),

  salesReceivablesByDimension: (
    dimension: ReceivablesAgingDimension,
    year: number,
    month: number,
    entity?: string,
    limit?: number,
    view?: ReceivablesDimensionView,
  ) =>
    get<{ dimension: string; view: string; year: number; month: number; rows: ReceivablesDimensionRow[] }>(
      '/api/v1/sales/receivables-aging/by-dimension',
      { dimension, year, month, entity, limit: limit ?? 25, view: view ?? 'buckets' },
    ),

  salesReceivablesByDimensionHierarchy: (
    hierarchy: ReceivablesHierarchyDim[],
    year: number,
    month: number,
    entity?: string,
    opts?: {
      limit?: number
      view?: ReceivablesDimensionView
      comparePm?: boolean
      comparePy?: boolean
    },
  ) =>
    get<ReceivablesHierarchyBreakdownResponse>('/api/v1/sales/receivables-aging/by-dimension-hierarchy', {
      hierarchy: hierarchy.join(','),
      year,
      month,
      entity,
      limit: opts?.limit ?? 500,
      view: opts?.view ?? 'buckets',
      compare_pm: opts?.comparePm ?? false,
      compare_py: opts?.comparePy ?? false,
    }),

  salesReceivablesConcentration: (
    year: number,
    month: number,
    entity?: string,
    periodOpts?: { period_grain?: 'month' | 'week' | 'year'; iso_year?: number; iso_week?: number },
  ) =>
    get<ReceivablesConcentration>('/api/v1/sales/receivables-aging/concentration', {
      year,
      month,
      entity,
      period_grain: periodOpts?.period_grain ?? 'month',
      iso_year: periodOpts?.iso_year,
      iso_week: periodOpts?.iso_week,
    }),

  salesReceivablesTrend: (
    year: number,
    month: number,
    entity?: string,
    periods_back?: number,
    period_grain?: 'month' | 'quarter' | 'week',
  ) =>
    get<ReceivablesTrendResponse>('/api/v1/sales/receivables-aging/trend', {
      year,
      month,
      entity,
      periods_back: periods_back ?? 12,
      period_grain: period_grain ?? 'month',
    }),

  salesReceivablesComposition: (year: number, month: number, entity?: string) =>
    get<{ year: number; month: number; segments: ReceivablesCompositionSegment[] }>(
      '/api/v1/sales/receivables-aging/composition',
      { year, month, entity },
    ),

  salesReceivablesCustomers: (year: number, month: number, entity?: string, limit?: number) =>
    get<{
      year: number
      month: number
      register: ReceivablesCustomerRegisterRow[]
      combo: ReceivablesCustomerComboRow[]
      scatter: ReceivablesCustomerScatterRow[]
    }>('/api/v1/sales/receivables-aging/customers', { year, month, entity, limit: limit ?? 50 }),

  salesReceivablesCustomerDocuments: (
    customerId: string,
    year: number,
    month: number,
    entity?: string,
  ) =>
    get<{ customer_id: string; year: number; month: number; documents: ReceivablesCustomerRegisterDocument[] }>(
      '/api/v1/sales/receivables-aging/customer-documents',
      { customer_id: customerId, year, month, entity },
    ),

  salesReceivablesByEntity: (year: number, month: number, entity?: string) =>
    get<{ year: number; month: number; rows: ReceivablesEntityRow[] }>(
      '/api/v1/sales/receivables-aging/by-entity',
      { year, month, entity },
    ),

  salesReceivablesDimensionChart: (
    dimension: string,
    year: number,
    month: number,
    entity?: string,
    limit?: number,
    comparePm?: boolean,
    comparePy?: boolean,
  ) =>
    get<ReceivablesDimensionChartResponse>('/api/v1/sales/receivables-aging/dimension-chart', {
      dimension,
      year,
      month,
      entity,
      limit: limit ?? 25,
      compare_pm: comparePm ?? false,
      compare_py: comparePy ?? false,
    }),

  salesReceivablesConcentrationTrend: (
    year: number,
    month: number,
    entity?: string,
    periodsBack?: number,
    periodOpts?: {
      period_grain?: 'month' | 'week' | 'year'
      iso_year?: number
      iso_week?: number
      aging_bucket?: string
    },
  ) =>
    get<{
      year: number
      month: number
      period_grain: string
      aging_bucket?: string
      points: ReceivablesConcentrationTrendPoint[]
    }>(
      '/api/v1/sales/receivables-aging/concentration/trend',
      {
        year,
        month,
        entity,
        periods_back: periodsBack ?? 12,
        period_grain: periodOpts?.period_grain ?? 'month',
        iso_year: periodOpts?.iso_year,
        iso_week: periodOpts?.iso_week,
        aging_bucket: periodOpts?.aging_bucket,
      },
    ),

  salesReceivablesGeo: (year: number, month: number, entity?: string, limit?: number) =>
    get<{ year: number; month: number; rows: ReceivablesGeoRow[] }>(
      '/api/v1/sales/receivables-aging/geo',
      { year, month, entity, limit: limit ?? 50 },
    ),

  salesReceivablesGeoCountryLocations: (
    country: string,
    year: number,
    month: number,
    entity?: string,
  ) =>
    get<ReceivablesGeoCountryLocationsResponse>(
      '/api/v1/sales/receivables-aging/geo/country-locations',
      { country, year, month, entity },
    ),

  salesReceivablesSalesLink: (year: number, month: number, entity?: string, months_back?: number) =>
    get<{ year: number; month: number; points: ReceivablesSalesLinkPoint[] }>(
      '/api/v1/sales/receivables-aging/sales-link',
      { year, month, entity, months_back: months_back ?? 12 },
    ),

  salesPayablesAging: (year: number, month: number, entity?: string) =>
    get<PayablesAgingResponse>('/api/v1/sales/payables-aging', { year, month, entity }),

  salesPayablesDrilldown: (
    band: string,
    year: number,
    month: number,
    entity?: string,
    limit?: number,
  ) =>
    get<{
      band: string
      year: number
      month: number
      as_of: string
      reconciliation_mode: string
      rows: PayablesDrillRow[]
    }>('/api/v1/sales/payables-aging/drilldown', { band, year, month, entity, limit: limit ?? 100 }),

  salesPayablesPortfolioTable: (
    dimension: PayablesAgingDimension,
    year: number,
    month: number,
    entity?: string,
    rowsPerBucket?: number,
  ) =>
    get<AgingPortfolioTableResponse>('/api/v1/sales/payables-aging/portfolio-table', {
      dimension,
      year,
      month,
      entity,
      rows_per_bucket: rowsPerBucket ?? 25,
    }),

  salesPayablesByDimension: (
    dimension: PayablesAgingDimension,
    year: number,
    month: number,
    entity?: string,
    limit?: number,
    view?: PayablesDimensionView,
  ) =>
    get<{ dimension: string; view: string; year: number; month: number; rows: PayablesDimensionRow[] }>(
      '/api/v1/sales/payables-aging/by-dimension',
      { dimension, year, month, entity, limit: limit ?? 25, view: view ?? 'buckets' },
    ),

  salesPayablesByDimensionHierarchy: (
    hierarchy: string[],
    year: number,
    month: number,
    entity?: string,
    opts?: {
      view?: 'buckets' | 'due_overdue'
      comparePm?: boolean
      comparePy?: boolean
      limit?: number
    },
  ) =>
    get<{
      hierarchy: string[]
      view: 'buckets' | 'due_overdue'
      year: number
      month: number
      compare_pm: boolean
      compare_py: boolean
      rows: PayablesHierarchyBreakdownRow[]
    }>('/api/v1/sales/payables-aging/by-dimension-hierarchy', {
      hierarchy: hierarchy.join(','),
      year,
      month,
      entity,
      view: opts?.view ?? 'buckets',
      compare_pm: opts?.comparePm ?? false,
      compare_py: opts?.comparePy ?? false,
      limit: opts?.limit ?? 500,
    }),

  salesPayablesConcentration: (
    year: number,
    month: number,
    entity?: string,
    periodOpts?: { period_grain?: 'month' | 'week' | 'year'; iso_year?: number; iso_week?: number },
  ) =>
    get<PayablesConcentration>('/api/v1/sales/payables-aging/concentration', {
      year,
      month,
      entity,
      period_grain: periodOpts?.period_grain ?? 'month',
      iso_year: periodOpts?.iso_year,
      iso_week: periodOpts?.iso_week,
    }),

  salesPayablesTrend: (
    year: number,
    month: number,
    entity?: string,
    periods_back?: number,
    period_grain?: 'month' | 'quarter' | 'week',
  ) =>
    get<{ year: number; month: number; period_grain?: 'month' | 'quarter' | 'week'; periods_back?: number; points: PayablesTrendPoint[] }>(
      '/api/v1/sales/payables-aging/trend',
      { year, month, entity, periods_back: periods_back ?? 12, period_grain: period_grain ?? 'month' },
    ),

  salesPayablesComposition: (year: number, month: number, entity?: string) =>
    get<{ year: number; month: number; segments: PayablesCompositionSegment[] }>(
      '/api/v1/sales/payables-aging/composition',
      { year, month, entity },
    ),

  salesPayablesSuppliers: (year: number, month: number, entity?: string, limit?: number) =>
    get<{
      year: number
      month: number
      register: PayablesSupplierRegisterRow[]
      combo: PayablesSupplierComboRow[]
      scatter: PayablesSupplierScatterRow[]
    }>('/api/v1/sales/payables-aging/suppliers', { year, month, entity, limit: limit ?? 50 }),

  salesPayablesSupplierDocuments: (
    supplierId: string,
    year: number,
    month: number,
    entity?: string,
  ) =>
    get<{ supplier_id: string; year: number; month: number; documents: PayablesSupplierRegisterDocument[] }>(
      '/api/v1/sales/payables-aging/supplier-documents',
      { supplier_id: supplierId, year, month, entity },
    ),

  salesPayablesByEntity: (year: number, month: number, entity?: string) =>
    get<{ year: number; month: number; rows: PayablesEntityRow[] }>(
      '/api/v1/sales/payables-aging/by-entity',
      { year, month, entity },
    ),

  salesPayablesDimensionChart: (
    dimension: string,
    year: number,
    month: number,
    entity?: string,
    limit?: number,
    comparePm?: boolean,
    comparePy?: boolean,
  ) =>
    get<PayablesDimensionChartResponse>('/api/v1/sales/payables-aging/dimension-chart', {
      dimension,
      year,
      month,
      entity,
      limit: limit ?? 25,
      compare_pm: comparePm ?? false,
      compare_py: comparePy ?? false,
    }),

  salesPayablesConcentrationTrend: (
    year: number,
    month: number,
    entity?: string,
    periods_back?: number,
    periodOpts?: { period_grain?: 'month' | 'week' | 'year'; iso_year?: number; iso_week?: number; aging_bucket?: string },
  ) =>
    get<{ year: number; month: number; period_grain?: 'month' | 'week' | 'year'; periods_back?: number; aging_bucket?: string; points: PayablesConcentrationTrendPoint[] }>(
      '/api/v1/sales/payables-aging/concentration/trend',
      {
        year,
        month,
        entity,
        periods_back: periods_back ?? 12,
        period_grain: periodOpts?.period_grain ?? 'month',
        iso_year: periodOpts?.iso_year,
        iso_week: periodOpts?.iso_week,
        aging_bucket: periodOpts?.aging_bucket,
      },
    ),

  salesPayablesGeo: (year: number, month: number, entity?: string, limit?: number) =>
    get<{ year: number; month: number; rows: PayablesGeoRow[] }>(
      '/api/v1/sales/payables-aging/geo',
      { year, month, entity, limit: limit ?? 20 },
    ),

  salesPayablesGeoCountryLocations: (
    country: string,
    year: number,
    month: number,
    entity?: string,
  ) =>
    get<PayablesGeoCountryLocationsResponse>(
      '/api/v1/sales/payables-aging/geo/country-locations',
      { country, year, month, entity },
    ),

  salesPayablesProcurementLink: (year: number, month: number, entity?: string, months_back?: number) =>
    get<{ year: number; month: number; points: PayablesProcurementLinkPoint[] }>(
      '/api/v1/sales/payables-aging/procurement-link',
      { year, month, entity, months_back: months_back ?? 12 },
    ),

  salesDeltaAttribution: (
    fromYear: number,
    fromMonth: number,
    toYear: number,
    toMonth: number,
    filters?: SalesFilters,
    polishWithClaude = true,
  ): Promise<SalesDeltaAttributionResponse> =>
    get('/api/v1/sales/analytics/delta-attribution', {
      from_year: fromYear,
      from_month: fromMonth,
      to_year: toYear,
      to_month: toMonth,
      polish_with_claude: polishWithClaude,
      ...salesFilterQueryParams(filters),
    }),

  salesCustomerRiskContext: (
    year: number,
    month: number,
    opts?: { customer_id?: string; customer_name?: string; entity?: string; polish_with_claude?: boolean },
    filters?: SalesFilters,
  ): Promise<SalesCustomerRiskContextResponse> =>
    get('/api/v1/sales/receivables-aging/customer-risk-context', {
      year,
      month,
      customer_id: opts?.customer_id,
      customer_name: opts?.customer_name,
      entity: opts?.entity,
      polish_with_claude: opts?.polish_with_claude ?? true,
      ...salesFilterQueryParams(filters),
    }),

  operationalMetaLatestPeriod: (): Promise<{
    year: number
    month: number
    domains?: Partial<Record<'opportunities' | 'procurement' | 'deliveries', { year: number; month: number }>>
  }> =>
    get('/api/v1/operational/meta/latest-period', {}),

  operationalOpportunitiesKpis: (year: number, month: number, entity?: string): Promise<OperationalOppKpis> =>
    get('/api/v1/operational/opportunities/kpis', { year, month, entity }),

  operationalOpportunitiesPipeline: (year: number, month: number, entity?: string): Promise<{ year: number; month: number; rows: OperationalPipelineRow[] }> =>
    get('/api/v1/operational/opportunities/pipeline-by-status', { year, month, entity }),

  operationalOpportunitiesTrend: (year: number, month: number, entity?: string, months_back?: number): Promise<{ year: number; month: number; rows: OperationalTrendRow[] }> =>
    get('/api/v1/operational/opportunities/trend', { year, month, entity, months_back: months_back ?? 12 }),

  operationalOpportunitiesTop: (year: number, month: number, entity?: string, limit?: number): Promise<{ year: number; month: number; rows: OperationalOppTopRow[] }> =>
    get('/api/v1/operational/opportunities/top', { year, month, entity, limit: limit ?? 20 }),

  operationalOpportunitiesKpisExtended: (year: number, month: number, entity?: string): Promise<{ year: number; month: number; kpis: Record<string, OperationalKpiMetric> }> =>
    get('/api/v1/operational/opportunities/kpis-extended', { year, month, entity }),

  operationalOpportunitiesStageDaily: (
    year: number,
    month: number,
    entity?: string,
    stage?: string,
    metric?: 'count' | 'value',
  ): Promise<{ stages: string[]; stage_codes: string[]; rows: OperationalStageDailyRow[] }> =>
    get('/api/v1/operational/opportunities/stage-daily', { year, month, entity, stage, metric: metric ?? 'count' }),

  operationalOpportunitiesGeoCountries: (
    year: number,
    month: number,
    entity?: string,
    stage?: string,
  ): Promise<SalesGeoCountry[]> =>
    get('/api/v1/operational/opportunities/geography/countries', { year, month, entity, stage }),

  operationalOpportunitiesGeoCountryLocations: (
    country: string,
    year: number,
    month: number,
    entity?: string,
    stage?: string,
  ): Promise<SalesGeoCountryLocationsResponse> =>
    get('/api/v1/operational/opportunities/geography/country-locations', {
      country,
      year,
      month,
      entity,
      stage,
    }),

  operationalOpportunitiesByRegion: (
    year: number,
    month: number,
    entity?: string,
    stage?: string,
    level?: 'macro_region' | 'country' | 'region',
    parent?: string,
  ): Promise<{ level: string; rows: OperationalGeoDrillRow[] }> =>
    get('/api/v1/operational/opportunities/by-region', { year, month, entity, stage, level: level ?? 'macro_region', parent }),

  operationalOpportunitiesNewInMonth: (
    year: number,
    month: number,
    entity?: string,
    opts?: { period_scope?: 'week' | 'month'; iso_year?: number; iso_week?: number },
  ): Promise<OperationalNewInPeriodResponse> =>
    get('/api/v1/operational/opportunities/new-in-month', {
      year,
      month,
      entity,
      period_scope: opts?.period_scope ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
    }),

  operationalOpportunitiesBySalesRep: (
    year: number,
    month: number,
    entity?: string,
    opts?: { period_scope?: 'week' | 'month'; iso_year?: number; iso_week?: number },
  ): Promise<OperationalSalesRepResponse> =>
    get('/api/v1/operational/opportunities/by-sales-rep', {
      year,
      month,
      entity,
      period_scope: opts?.period_scope ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
    }),

  operationalOpportunitiesByCloseDate: (year: number, month: number, entity?: string): Promise<{ rows: OperationalCloseDateRow[] }> =>
    get('/api/v1/operational/opportunities/by-close-date', { year, month, entity }),

  operationalOpportunitiesOutcomeTrend: (
    year: number,
    month: number,
    entity?: string,
    opts?: { period_grain?: 'week' | 'month'; iso_year?: number; iso_week?: number },
  ): Promise<OperationalOutcomeTrendResponse> =>
    get('/api/v1/operational/opportunities/outcome-trend', {
      year,
      month,
      entity,
      period_grain: opts?.period_grain ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
    }),

  operationalOpportunitiesPlanCoverageBridge: (
    year: number,
    month: number,
    entity?: string,
  ): Promise<OpportunityPlanCoverageBridgeResponse> =>
    get('/api/v1/operational/opportunities/plan-coverage-bridge', { year, month, entity }),

  operationalOpportunitiesStageSnapshot: (
    year: number,
    month: number,
    entity?: string,
  ): Promise<OpportunityStageSnapshotResponse> =>
    get('/api/v1/operational/opportunities/stage-snapshot', { year, month, entity }),

  operationalOpportunitiesStageMatrix: (
    year: number,
    month: number,
    entity?: string,
    opts?: {
      row_dim?: OpportunityStageRowDim
      parent_dim?: OpportunityStageParentDim
      metric?: OpportunityStageMatrixMetric
      limit?: number
    },
  ): Promise<OpportunityStageMatrixResponse> =>
    get('/api/v1/operational/opportunities/stage-matrix', {
      year,
      month,
      entity,
      row_dim: opts?.row_dim,
      parent_dim: opts?.parent_dim,
      metric: opts?.metric,
      limit: opts?.limit,
    }),

  operationalOpportunitiesTopExtended: (
    year: number,
    month: number,
    entity?: string,
    opts?: {
      stage?: string
      stages?: string[]
      limit?: number
      kind?: 'top' | 'lost'
      period_scope?: OppTopPeriodScope
      iso_year?: number
      iso_week?: number
    },
  ): Promise<OperationalTopExtendedResponse> =>
    get('/api/v1/operational/opportunities/top-extended', {
      year,
      month,
      entity,
      stage: opts?.stage,
      stages: opts?.stages?.length ? opts.stages.join(',') : undefined,
      limit: opts?.limit ?? 0,
      kind: opts?.kind ?? 'top',
      period_scope: opts?.period_scope ?? 'month',
      iso_year: opts?.iso_year,
      iso_week: opts?.iso_week,
    }),

  operationalProcurementKpisExtended: (year: number, month: number, entity?: string): Promise<{ kpis: Record<string, OperationalKpiMetric> }> =>
    get('/api/v1/operational/procurement/kpis-extended', { year, month, entity }),

  operationalProcurementRates: (year: number, month: number, entity?: string): Promise<{ rates: OperationalProcurementRate[] }> =>
    get('/api/v1/operational/procurement/rates', { year, month, entity }),

  operationalProcurementCycleSummary: (
    year: number,
    month: number,
    entity?: string,
  ): Promise<{ steps: OperationalCycleStep[]; transitions: OperationalCycleTransition[] }> =>
    get('/api/v1/operational/procurement/cycle-summary', { year, month, entity }),

  operationalProcurementCycleDrilldown: (
    year: number,
    month: number,
    step?: string,
    entity?: string,
  ): Promise<{ step: string; step_label: string; rows: OperationalCycleDrilldownRow[] }> =>
    get('/api/v1/operational/procurement/cycle-drilldown', { year, month, step: step ?? 'delivery', entity }),

  operationalProcurementMaterialTurnoverDaily: (year: number, month: number): Promise<{ rows: { day: number; turnover_days: number }[] }> =>
    get('/api/v1/operational/procurement/material-turnover-daily', { year, month }),

  operationalProcurementSuppliersByDimension: (year: number, month: number, dimension?: 'category' | 'region', entity?: string): Promise<{ rows: { label: string; supplier_count: number; spend: number }[] }> =>
    get('/api/v1/operational/procurement/suppliers-by-dimension', { year, month, dimension: dimension ?? 'category', entity }),

  operationalProcurementPriceAnalysis: (year: number, month: number, entity?: string, limit?: number): Promise<{ rows: OperationalPriceAnalysisRow[] }> =>
    get('/api/v1/operational/procurement/price-analysis', { year, month, entity, limit: limit ?? 50 }),

  operationalProcurementSpendDailyYoy: (year: number, month: number, entity?: string): Promise<{ current: { day: number; amount: number }[]; prior_year: { day: number; amount: number }[] }> =>
    get('/api/v1/operational/procurement/spend-daily-yoy', { year, month, entity }),

  operationalProcurementMaterialCoverage: (
    year: number,
    month: number,
    entity?: string,
    limit?: number,
  ): Promise<{ year: number; month: number; summary: OperationalMaterialCoverageSummary; rows: OperationalMaterialCoverageRow[] }> =>
    get('/api/v1/operational/procurement/material-coverage', { year, month, entity, limit: limit ?? 50 }),

  operationalDeliveriesKpisExtended: (year: number, month: number, entity?: string): Promise<{ kpis: Record<string, OperationalKpiMetric> }> =>
    get('/api/v1/operational/deliveries/kpis-extended', { year, month, entity }),

  operationalDeliveriesStatusRate: (
    year: number,
    month: number,
    entity?: string,
  ): Promise<{ within_pct: number; label: string; kpi?: OperationalKpiMetric }> =>
    get('/api/v1/operational/deliveries/status-rate', { year, month, entity }),

  operationalDeliveriesStatusRateTrend: (
    year: number,
    month: number,
    entity?: string,
    months_back?: number,
  ): Promise<{ rows: OperationalStatusRateTrendRow[] }> =>
    get('/api/v1/operational/deliveries/status-rate-trend', { year, month, entity, months_back: months_back ?? 12 }),

  operationalDeliveriesByDimension: (
    year: number,
    month: number,
    entity?: string,
    dimension?: 'country' | 'sales_site' | 'segment' | 'entity',
    level?: 'top' | 'region',
    parent?: string,
  ): Promise<{ rows: OperationalDeliveryDimensionRow[] }> =>
    get('/api/v1/operational/deliveries/by-dimension', {
      year,
      month,
      entity,
      dimension: dimension ?? 'country',
      level: level ?? 'top',
      parent,
    }),

  operationalDeliveriesAmountDrill: (
    year: number,
    month: number,
    entity?: string,
    dimension?: 'sales_site' | 'segment' | 'entity',
  ): Promise<OperationalDeliveryAmountDrill> =>
    get('/api/v1/operational/deliveries/amount-drill', {
      year,
      month,
      entity,
      dimension: dimension ?? 'sales_site',
    }),

  operationalDeliveriesByCountry: (year: number, month: number, entity?: string): Promise<{ rows: { country: string; cnt: number }[] }> =>
    get('/api/v1/operational/deliveries/by-country', { year, month, entity }),

  operationalDeliveriesByRegion: (year: number, month: number, country: string, entity?: string): Promise<{ rows: { region: string; cnt: number }[] }> =>
    get('/api/v1/operational/deliveries/by-region', { year, month, country, entity }),

  operationalDeliveriesRoutes: (year: number, month: number, entity?: string, limit?: number): Promise<{ rows: OperationalRouteRow[] }> =>
    get('/api/v1/operational/deliveries/routes', { year, month, entity, limit: limit ?? 50 }),

  operationalDeliveriesRouteDetail: (deliveryId: string): Promise<OperationalRouteDetail> =>
    get(`/api/v1/operational/deliveries/routes/${encodeURIComponent(deliveryId)}`, {}),

  operationalDeliveriesRouteGeometry: (deliveryId: string): Promise<OperationalRouteGeometry> =>
    get(`/api/v1/operational/deliveries/routes/${encodeURIComponent(deliveryId)}/geometry`, {}),

  operationalDeliveriesVehicles: (limit?: number, offset?: number): Promise<{ total: number; rows: OperationalVehicleRow[] }> =>
    get('/api/v1/operational/deliveries/vehicles', { limit: limit ?? 120, offset: offset ?? 0 }),

  operationalDeliveriesVehicleDetail: (vehicleId: string): Promise<OperationalVehicleDetail> =>
    get(`/api/v1/operational/deliveries/vehicles/${encodeURIComponent(vehicleId)}`, {}),

  operationalProcurementSpendTrend: (year: number, month: number, entity?: string, months_back?: number): Promise<{ year: number; month: number; rows: OperationalProcurementTrendRow[] }> =>
    get('/api/v1/operational/procurement/spend-trend', { year, month, entity, months_back: months_back ?? 12 }),

  operationalProcurementTopSuppliers: (year: number, month: number, entity?: string, limit?: number): Promise<{ year: number; month: number; rows: OperationalSupplierRow[] }> =>
    get('/api/v1/operational/procurement/top-suppliers', { year, month, entity, limit: limit ?? 15 }),

  operationalProcurementStatusMix: (year: number, month: number, entity?: string): Promise<{ year: number; month: number; rows: OperationalStatusMixRow[] }> =>
    get('/api/v1/operational/procurement/status-mix', { year, month, entity }),

  operationalProcurementCategoryMix: (year: number, month: number, entity?: string): Promise<{ year: number; month: number; rows: OperationalCategoryMixRow[] }> =>
    get('/api/v1/operational/procurement/category-mix', { year, month, entity }),

  operationalProcurementScatterLines: (year: number, month: number, entity?: string, limit?: number): Promise<{ year: number; month: number; rows: OperationalScatterLineRow[] }> =>
    get('/api/v1/operational/procurement/scatter-lines', { year, month, entity, limit: limit ?? 500 }),

  operationalDeliveriesKpis: (year: number, month: number, entity?: string): Promise<OperationalDeliveryKpis> =>
    get('/api/v1/operational/deliveries/kpis', { year, month, entity }),

  operationalDeliveriesByCarrier: (year: number, month: number, entity?: string): Promise<{ year: number; month: number; rows: OperationalCarrierRow[] }> =>
    get('/api/v1/operational/deliveries/by-carrier', { year, month, entity }),

  operationalDeliveriesTrendOtif: (year: number, month: number, entity?: string, months_back?: number): Promise<{ year: number; month: number; rows: OperationalOtifTrendRow[] }> =>
    get('/api/v1/operational/deliveries/trend-otif', { year, month, entity, months_back: months_back ?? 12 }),

  operationalDeliveriesDelayHistogram: (year: number, month: number, entity?: string): Promise<{ year: number; month: number; rows: OperationalDelayBucketRow[] }> =>
    get('/api/v1/operational/deliveries/delay-histogram', { year, month, entity }),

  operationalDeliveriesScatterQty: (year: number, month: number, entity?: string, limit?: number): Promise<{ year: number; month: number; rows: OperationalScatterQtyRow[] }> =>
    get('/api/v1/operational/deliveries/scatter-qty', { year, month, entity, limit: limit ?? 400 }),

  operationalDeliveriesGanttSample: (year: number, month: number, entity?: string, limit?: number): Promise<{ year: number; month: number; rows: OperationalGanttRow[] }> =>
    get('/api/v1/operational/deliveries/gantt-sample', { year, month, entity, limit: limit ?? 40 }),

  inventorySnapshotKpis: (fiscal_year: number, fiscal_period: number): Promise<InventorySnapshotKpis> =>
    get('/api/v1/inventory/snapshot/kpis', { fiscal_year, fiscal_period }),

  inventorySnapshotByMaterial: (fiscal_year: number, fiscal_period: number, limit?: number): Promise<{ fiscal_year: number; fiscal_period: number; rows: InventoryMaterialRow[] }> =>
    get('/api/v1/inventory/snapshot/by-material', { fiscal_year, fiscal_period, limit: limit ?? 25 }),

  inventorySnapshotBySite: (fiscal_year: number, fiscal_period: number): Promise<{ fiscal_year: number; fiscal_period: number; rows: InventorySiteRow[] }> =>
    get('/api/v1/inventory/snapshot/by-site', { fiscal_year, fiscal_period }),

  inventoryMovementsTrend: (year: number, month: number, entity?: string, months_back?: number): Promise<{ year: number; month: number; rows: InventoryMovementTrendRow[] }> =>
    get('/api/v1/inventory/movements/trend', { year, month, entity, months_back: months_back ?? 12 }),

  inventoryMovementsByType: (year: number, month: number, entity?: string): Promise<{ year: number; month: number; rows: InventoryMovementTypeRow[] }> =>
    get('/api/v1/inventory/movements/by-type', { year, month, entity }),

  inventoryMovementsLinkMix: (year: number, month: number, entity?: string): Promise<{ year: number; month: number; rows: InventoryLinkMixRow[] }> =>
    get('/api/v1/inventory/movements/link-mix', { year, month, entity }),

  inventoryMovementsRiver: (year: number, month: number, entity?: string, months_back?: number): Promise<{ year: number; month: number; rows: InventoryRiverRow[] }> =>
    get('/api/v1/inventory/movements/river', { year, month, entity, months_back: months_back ?? 6 }),

  inventoryHealthSummary: (fiscal_year: number, fiscal_period: number): Promise<InventoryHealthSummary> =>
    get('/api/v1/inventory/health/summary', { fiscal_year, fiscal_period }),

  inventoryOverviewSalesActivityKpis: (year: number, month: number, entity?: string) =>
    get<{ year: number; month: number; kpis: Record<string, OperationalKpiMetric> }>(
      '/api/v1/inventory/overview/sales-activity-kpis', { year, month, entity },
    ),

  inventoryOverviewStockKpis: (year: number, month: number, entity?: string) =>
    get<{ year: number; month: number; kpis: Record<string, OperationalKpiMetric> }>(
      '/api/v1/inventory/overview/stock-kpis', { year, month, entity },
    ),

  inventoryOverviewCombinedChart: (year: number, month: number, grain?: string, entity?: string) =>
    get<{ year: number; month: number; grain: string; rows: InventoryCombinedChartRow[] }>(
      '/api/v1/inventory/overview/combined-chart', { year, month, grain: grain ?? 'month', entity },
    ),

  inventoryOverviewDioDpo: (year: number, month: number, grain?: string, entity?: string) =>
    get<InventoryDioDpoResponse>('/api/v1/inventory/overview/dio-dpo', {
      year,
      month,
      grain: grain ?? 'month',
      entity,
    }),

  inventoryOverviewPayablesAging: (year: number, month: number, entity?: string) =>
    get<InventoryPayablesAgingResponse>(
      '/api/v1/inventory/overview/payables-aging', { year, month, entity },
    ),

  inventoryOverviewPayablesDrilldown: (
    band: string,
    year: number,
    month: number,
    entity?: string,
    limit?: number,
  ) =>
    get<{ band: string; year: number; month: number; as_of: string; reconciliation_mode: string; rows: InventoryPayablesDrillRow[] }>(
      '/api/v1/inventory/overview/payables-aging/drilldown',
      { band, year, month, entity, limit: limit ?? 100 },
    ),

  inventoryStockHierarchyKpis: (year: number, month: number) =>
    get<{
      year: number
      month: number
      physical_available: InventoryHierarchyBlock
      soft_reserved: InventoryHierarchyBlock
      on_hand: InventoryHierarchyBlock
    }>('/api/v1/inventory/stock/hierarchy-kpis', { year, month }),

  inventoryStockKpis: (year: number, month: number, entity?: string) =>
    get<{ year: number; month: number; kpis: Record<string, OperationalKpiMetric> }>(
      '/api/v1/inventory/stock/kpis', { year, month, entity },
    ),

  inventoryStockPositions: (year: number, month: number, limit?: number) =>
    get<{ year: number; month: number; rows: InventoryStockPositionRow[] }>(
      '/api/v1/inventory/stock/positions', { year, month, limit: limit ?? 80 },
    ),

  inventoryStockStockoutItems: (year: number, month: number, limit?: number) =>
    get<{ year: number; month: number; rows: InventoryStockoutItemRow[] }>(
      '/api/v1/inventory/stock/stockout-items', { year, month, limit: limit ?? 20 },
    ),

  inventoryStockByWarehouse: (year: number, month: number) =>
    get<{ year: number; month: number; rows: InventoryWarehouseRow[] }>(
      '/api/v1/inventory/stock/by-warehouse', { year, month },
    ),

  inventoryStockByWarehouseProducts: (
    warehouse: string,
    year: number,
    month: number,
    segment?: 'on_hand' | 'soft_reserved' | 'returned',
    limit?: number,
  ) =>
    get<{
      warehouse: string
      segment: string
      segment_label: string
      rows: { label: string; value: number }[]
    }>('/api/v1/inventory/stock/by-warehouse/products', {
      warehouse,
      year,
      month,
      segment: segment ?? 'on_hand',
      limit: limit ?? 12,
    }),

  inventoryStockReturnRateByProduct: (year: number, month: number, limit?: number) =>
    get<{ year: number; month: number; rows: InventoryReturnRateRow[] }>(
      '/api/v1/inventory/stock/return-rate-by-product', { year, month, limit: limit ?? 15 },
    ),

  inventoryStockInOutTrend: (year: number, month: number, grain?: string, entity?: string) =>
    get<{ year: number; month: number; grain: string; rows: InventoryInOutTrendRow[] }>(
      '/api/v1/inventory/stock/in-out-trend', { year, month, grain: grain ?? 'month', entity },
    ),

  inventoryStockInOutDrilldown: (
    periodStart: string,
    periodEnd: string,
    direction: 'in' | 'out',
    entity?: string,
    limit?: number,
  ) =>
    get<{ direction: string; period_start: string; period_end: string; rows: InventoryInOutDrillRow[] }>(
      '/api/v1/inventory/stock/in-out-trend/drilldown',
      { period_start: periodStart, period_end: periodEnd, direction, entity, limit: limit ?? 80 },
    ),

  inventoryStockOverview: (year: number, month: number, limit?: number) =>
    get<{ year: number; month: number; rows: InventoryOverviewRow[] }>(
      '/api/v1/inventory/stock/inventory-overview', { year, month, limit: limit ?? 100 },
    ),

  glLines: (params: {
    entity?: string
    dateFrom?: string
    dateTo?: string
    glAccountId?: string
    journalEntryNumber?: string
    level1?: string
    level2?: string
    level3?: string
    level4?: string
    statementType?: string
    customerName?: string
    supplierName?: string
    search?: string
    sortBy?: 'date_desc' | 'date' | 'amount_abs'
    cursor?: string
    offset?: number
    limit?: number
    includeTotal?: boolean
  }): Promise<GlLinesResponse> =>
    get('/api/v1/facts/gl-journal-lines', {
      entity:                 params.entity,
      date_from:              params.dateFrom,
      date_to:                params.dateTo,
      gl_account_id:          params.glAccountId,
      journal_entry_number:   params.journalEntryNumber,
      level_1:                params.level1,
      level_2:                params.level2,
      level_3:                params.level3,
      level_4:                params.level4,
      statement_type:         params.statementType,
      customer_name:          params.customerName,
      supplier_name:          params.supplierName,
      search:                 params.search,
      sort_by:                params.sortBy ?? 'date_desc',
      cursor:                 params.cursor,
      offset:                 params.offset,
      limit:                  params.limit ?? 100,
      include_total:          params.includeTotal ? 1 : undefined,
    }),

  glLinesFilters: (entity?: string): Promise<GlLinesFiltersResponse> =>
    get('/api/v1/facts/gl-journal-lines/filters', entity ? { entity } : {}),

  trialBalanceExport: (params: {
    year: number
    month: number
    entity?: string
  }): Promise<TrialBalanceExportResponse> =>
    get('/api/v1/facts/trial-balance-export', {
      year: params.year,
      month: params.month,
      entity: params.entity,
    }),

  directoryContacts: (department?: string): Promise<{ contacts: InternalContact[] }> =>
    get('/api/v1/directory/contacts', department ? { department } : {}),

  actionNotesListSessions: (authorScope: string): Promise<{ sessions: ActionNoteSession[] }> =>
    get('/api/v1/action-notes/sessions', { author_scope: authorScope }),

  actionNotesCreateSession: (body: {
    author_scope: string
    title: string
    route?: string
    filters?: Record<string, unknown>
  }): Promise<ActionNoteSession> =>
    post('/api/v1/action-notes/sessions', body),

  actionNotesGetSession: (sessionId: string): Promise<ActionNoteSessionBundle> =>
    get(`/api/v1/action-notes/sessions/${sessionId}`),

  actionNotesAddNote: (sessionId: string, body: { body: string; is_done?: boolean }): Promise<ActionNote> =>
    post(`/api/v1/action-notes/sessions/${sessionId}/notes`, body),

  actionNotesPatchNote: (
    sessionId: string,
    noteId: string,
    body: { body?: string; is_done?: boolean },
  ): Promise<ActionNote> =>
    patch(`/api/v1/action-notes/sessions/${sessionId}/notes/${noteId}`, body),

  actionNotesDeleteNote: (sessionId: string, noteId: string): Promise<{ ok: boolean }> =>
    del(`/api/v1/action-notes/sessions/${sessionId}/notes/${noteId}`),

  actionNotesAddPin: (
    sessionId: string,
    body: { label?: string; snapshot: ViewPinSnapshot },
  ): Promise<ActionNotePin> =>
    post(`/api/v1/action-notes/sessions/${sessionId}/pins`, body),

  actionNotesDraftEmail: (
    sessionId: string,
    body: {
      contact_id: string
      note_ids?: string[]
      pin_ids?: string[]
      user_name: string
      user_email: string
      language?: string
      app_base_url?: string
      simulate_reply?: boolean
    },
  ): Promise<EmailDraftResponse> =>
    post(`/api/v1/action-notes/sessions/${sessionId}/draft-email`, body),

  actionNotesGenerateBoard: (
    sessionId: string,
    body: { note_ids?: string[]; pin_ids?: string[] },
  ): Promise<{ board_id: string; board: ActionBoard }> =>
    post(`/api/v1/action-notes/sessions/${sessionId}/generate-board`, body),

  expertChatListSessions: (authorScope: string): Promise<{ sessions: ExpertChatSession[] }> =>
    get('/api/v1/expert-chat/sessions', { author_scope: authorScope }),

  expertChatCreateSession: (body: {
    author_scope: string
    sender_id: string
    title?: string
    route?: string
    filters?: Record<string, unknown>
  }): Promise<ExpertChatSession> =>
    post('/api/v1/expert-chat/sessions', body),

  expertChatGetSession: (
    sessionId: string,
  ): Promise<{ session: ExpertChatSession; messages: ExpertChatMessage[] }> =>
    get(`/api/v1/expert-chat/sessions/${sessionId}`),

  expertChatAddMessage: (
    sessionId: string,
    body: { role: 'user' | 'bot'; body: string },
  ): Promise<ExpertChatMessage> =>
    post(`/api/v1/expert-chat/sessions/${sessionId}/messages`, body),

  expertChatPatchSession: (sessionId: string, body: { title?: string }): Promise<ExpertChatSession> =>
    patch(`/api/v1/expert-chat/sessions/${sessionId}`, body),

  // ── Admin ──────────────────────────────────────────────────────────────────

  adminPages: (): Promise<{ pages: AdminPage[] }> =>
    get('/api/v1/admin/pages'),

  adminRoles: (): Promise<{ roles: AdminRole[] }> =>
    get('/api/v1/admin/roles'),

  adminCreateRole: (body: {
    role_name: string
    description: string
    page_keys: string[]
    entity_codes: string[]
  }): Promise<AdminRole> =>
    post('/api/v1/admin/roles', body),

  adminUpdateRole: (id: number, body: {
    role_name: string
    description: string
    page_keys: string[]
    entity_codes: string[]
  }): Promise<AdminRole> =>
    put(`/api/v1/admin/roles/${id}`, body),

  adminDeleteRole: (id: number): Promise<void> =>
    delVoid(`/api/v1/admin/roles/${id}`),

  adminUsers: (): Promise<{ users: AdminUser[] }> =>
    get('/api/v1/admin/users'),

  adminUpdateUser: (id: number, body: {
    role_ids: number[]
    is_admin: boolean
    is_active: boolean
  }): Promise<AdminUser> =>
    put(`/api/v1/admin/users/${id}`, body),
}

export interface InternalContact {
  contact_id: string
  display_name: string
  email: string
  department: string
  role_title: string
  role_id?: string
  role_key?: string
  org_role_name?: string
  seniority_level?: string
  email_tone_hint?: string
  salutation_de: string
  salutation_en?: string
}

export interface ExpertChatSession {
  session_id: string
  author_scope: string
  sender_id: string
  title: string
  route?: string
  filters_json?: Record<string, unknown>
  created_at?: string
  updated_at?: string
}

export interface ExpertChatMessage {
  message_id: string
  session_id: string
  role: 'user' | 'bot'
  body: string
  created_at?: string
}

export interface ActionNoteSession {
  session_id: string
  author_scope: string
  title: string
  route?: string
  filters_json?: Record<string, unknown>
  status: string
  created_at?: string
  updated_at?: string
}

export interface ActionNote {
  note_id: string
  session_id: string
  body: string
  is_done: boolean
  sort_order: number
  created_at?: string
  updated_at?: string
}

export interface ViewPinSnapshot {
  pin_type: 'table' | 'expert_chat' | 'chart' | 'mixed'
  captured_at: string
  route: string
  filters: Record<string, unknown>
  label?: string
  /** navigate = reopen route with filters; preview_only = snapshot rows only */
  restore_mode?: 'navigate' | 'preview_only'
  table_id?: string
  view_state?: Record<string, unknown>
  table?: {
    component: string
    expanded_row_ids: string[]
    visible_column_ids?: string[]
    row_preview: Array<{ id: string; label: string; values: Record<string, string | number> }>
  }
  expert_chat?: {
    session_id?: string
    messages: Array<{ role: string; text?: string; timestamp: string }>
  }
  chart?: {
    chart_id: string
    title?: string
    image_data_url?: string
    image_url?: string
    anchor?: string
  }
}

export interface ActionNotePin {
  pin_id: string
  session_id: string
  label?: string
  snapshot_json: ViewPinSnapshot
  created_at?: string
}

export interface ActionNoteSessionBundle {
  session: ActionNoteSession
  notes: ActionNote[]
  pins: ActionNotePin[]
  boards: Array<{ board_id: string; title: string; board_json: ActionBoard }>
}

export interface EmailDraftResponse {
  draft_id: string
  contact: InternalContact
  subject: string
  body_text: string
  body_html?: string
  action_items: string[]
  notes?: string[]
  pin_links?: Array<{ label: string; url: string }>
  mailto: string
  status?: string
  simulated_reply?: {
    from?: string
    subject?: string
    body_text?: string
    simulated?: boolean
  } | null
}

export interface ActionBoard {
  title: string
  columns: Array<{ id: string; title: string }>
  tasks: Array<{
    id: string
    title: string
    description?: string
    assignee_contact_id?: string
    due_date?: string | null
    priority?: string
    linked_note_indices?: number[]
    column_id?: string
    dependencies?: string[]
    comments?: string[]
    activity?: Array<{ type?: string; at?: string; message?: string }>
  }>
  activity_log?: Array<{ type?: string; at?: string; message?: string }>
  evidence?: Array<{ pin_id?: string; label?: string; route?: string }>
}

// ─── Admin types ──────────────────────────────────────────────────────────────

export interface AdminPage {
  key: string
  label: string
  group: 'reporting' | 'tools' | 'admin'
}

export interface AdminRole {
  role_id: number
  role_name: string
  description: string
  page_keys: string[]
  /** Empty array means "all entities visible". */
  entity_codes: string[]
}

export interface AdminUser {
  user_id: number
  email: string
  display_name: string
  is_admin: boolean
  is_active: boolean
  role_ids: number[]
}
