/**
 * WorkingCapitalBlock — Block (8): Working Capital DSO / DPO / DIO deep-dives.
 *
 * P3: when `summaryWc` is supplied by OverviewPageV2 from useOverviewSummaryV2,
 * the block skips its own fetch (zero extra round-trips) and renders directly
 * from the batched summary payload. The Δfy column (previously TODO(P3)) is now
 * populated from summary.working_capital.levels[].delta_fy.
 *
 * Fallback path (summaryWc absent): fires its own api.wcRatios +
 * api.financialsWcTimeline fetches (P2 behaviour, Δfy shows as "—").
 *
 * KPI computations match compute_wc_kpis EXACTLY (financial sign-off confirmed,
 * docs/overview-v2-redesign-plan.md §2 Area 8):
 *   CCC = DSO + DIO − DPO   (FAV−: lower is better)
 *   DSO, DIO, DPO display from payload — NOT recomputed here.
 *   NWC taken from payload (aggregated server-side).
 *
 * FAV direction:
 *   DSO: FAV− · DIO: FAV− · DPO: FAV+ · CCC: FAV−
 *
 * Scope: reporting-v2 / port 5177 only — tree-shaken from fdd-merge (5176) and
 * v4 (5178) bundles via IS_OVERVIEW_V2 mode gate. See overview-v2-redesign-plan.md §5.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Info } from 'lucide-react'
import {
  api,
  type WcRatiosData,
  type WcTimelineResponse,
} from '../../../../lib/api'
import { fmtDays } from '../../../../lib/fmt'
import OverviewAnalysisBlock from '../OverviewAnalysisBlock'
import type { OverviewSummaryWc } from '../hooks/useOverviewSummaryV2'

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  year: number
  month: number
  entity?: string
  /**
   * When provided (from useOverviewSummaryV2), the block skips its own
   * api.wcRatios + api.financialsWcTimeline fetches and uses this data
   * directly. levels[].delta_fy fills the P2 TODO(P3) Δfy column.
   */
  summaryWc?: OverviewSummaryWc | null
}

// ─── Number formatting helpers (kEUR input — timeline values are already kEUR) ─

/** Format a kEUR value compactly: ≥ 1000k → Xm, else Xk. */
function fmtKeurCompact(keur: number): string {
  const abs = Math.abs(keur)
  if (abs >= 1000) {
    const m = keur / 1000
    return (
      m.toLocaleString('de-DE', {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      }) + 'm'
    )
  }
  return keur.toLocaleString('de-DE', { maximumFractionDigits: 0 }) + 'k'
}

/** Format a kEUR delta with sign prefix. */
function fmtDeltaKeur(keur: number): string {
  const sign = keur >= 0 ? '+' : '−'
  const abs = Math.abs(keur)
  if (abs >= 1000) {
    return (
      sign +
      (abs / 1000).toLocaleString('de-DE', {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      }) +
      'm'
    )
  }
  return sign + abs.toLocaleString('de-DE', { maximumFractionDigits: 0 }) + 'k'
}

/** Format a days delta with sign prefix (German decimal separator). */
function fmtDeltaDays(v: number): string {
  const sign = v >= 0 ? '+' : '−'
  const abs = Math.abs(v).toLocaleString('de-DE', { maximumFractionDigits: 1 })
  return `${sign}${abs}d`
}

// ─── FAV direction color helpers ──────────────────────────────────────────────

/**
 * Return a semantic color for a delta value given its FAV direction.
 * Uses muted tones consistent with the v2 palette (no garish reds/greens).
 */
function deltaColor(
  delta: number | null,
  favPlus: boolean,
): string {
  if (delta == null || Math.abs(delta) < 0.05) return '#64748B'
  const isFav = favPlus ? delta > 0 : delta < 0
  return isFav ? '#16A34A' : '#B45309'
}

// ─── KPI chip (days only) ─────────────────────────────────────────────────────

interface KpiChipProps {
  label: string
  value: number | null
  /** Period-on-period delta in days. */
  delta: number | null
  /** FAV+ = higher is favorable (DPO). FAV− = lower is favorable (DSO/DIO/CCC). */
  favPlus?: boolean
}

function KpiChip({ label, value, delta, favPlus = false }: KpiChipProps) {
  const dColor = deltaColor(delta, favPlus)
  const valStr =
    value == null ? '—' : `${fmtDays(value)}d`
  const dStr = delta != null ? fmtDeltaDays(delta) : null

  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span
        className="text-[10px] font-medium uppercase tracking-wide truncate"
        style={{ color: '#94A3B8' }}
      >
        {label}
      </span>
      <span
        className="text-sm font-semibold tabular-nums"
        style={{ color: '#1E3A5F' }}
      >
        {valStr}
      </span>
      {dStr != null && (
        <span
          className="text-[10px] tabular-nums"
          style={{ color: dColor }}
        >
          {dStr}
        </span>
      )}
    </div>
  )
}

// ─── Stock level row ──────────────────────────────────────────────────────────

interface StockRowProps {
  label: string
  /** Level in kEUR (already kEUR from timeline or converted from raw EUR). */
  level: number | null
  /** Month-on-month delta in kEUR. */
  deltaMonth: number | null
  /** Full-year (FY) delta in kEUR — populated from summaryWc.levels[].delta_fy (P3). */
  deltaFy?: number | null
  /** FAV+ = growing balance is favorable (trade payables). */
  favPlus?: boolean
}

function StockRow({ label, level, deltaMonth, deltaFy, favPlus = false }: StockRowProps) {
  const dColor    = deltaColor(deltaMonth, favPlus)
  const dFyColor  = deltaColor(deltaFy ?? null, favPlus)

  return (
    <div
      className="flex items-center justify-between py-1.5"
      style={{ borderBottom: '1px solid #F8FAFC' }}
    >
      <span className="text-xs truncate pr-2" style={{ color: '#374151' }}>
        {label}
      </span>
      <div className="flex items-center gap-3 shrink-0">
        <span
          className="text-xs font-medium tabular-nums"
          style={{ color: '#1E3A5F' }}
        >
          {level == null ? '—' : fmtKeurCompact(level)}
        </span>
        <span
          className="text-[10px] tabular-nums w-12 text-right"
          style={{ color: deltaMonth == null ? '#CBD5E1' : dColor }}
        >
          {deltaMonth == null ? '—' : fmtDeltaKeur(deltaMonth)}
        </span>
        {/* Δ FY column — only rendered when caller supplies deltaFy prop */}
        {deltaFy !== undefined && (
          <span
            className="text-[10px] tabular-nums w-12 text-right"
            style={{ color: deltaFy == null ? '#CBD5E1' : dFyColor }}
          >
            {deltaFy == null ? '—' : fmtDeltaKeur(deltaFy)}
          </span>
        )}
      </div>
    </div>
  )
}

// ─── Finding chip ─────────────────────────────────────────────────────────────

function FindingChip({ text, route }: { text: string; route: string }) {
  const navigate = useNavigate()
  return (
    <div
      className="flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors hover:bg-slate-50"
      style={{ background: '#F8FAFC', border: '1px solid #E2E8F0' }}
      role="button"
      tabIndex={0}
      onClick={() => navigate(route)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') navigate(route)
      }}
    >
      <Info size={11} aria-hidden style={{ color: '#1E3A5F', flexShrink: 0 }} />
      <p
        className="flex-1 text-[11px] leading-snug"
        style={{ color: '#374151' }}
      >
        {text}
      </p>
      <ArrowRight
        size={10}
        aria-hidden
        style={{ color: '#CBD5E1', flexShrink: 0 }}
      />
    </div>
  )
}

// ─── Loading skeleton ─────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div className="space-y-3 animate-pulse">
      <div className="grid grid-cols-4 gap-3">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="h-12 rounded"
            style={{ background: '#F1F5F9' }}
          />
        ))}
      </div>
      <div className="h-5 w-28 rounded" style={{ background: '#F1F5F9' }} />
      <div className="space-y-1.5">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-7 rounded" style={{ background: '#F1F5F9' }} />
        ))}
      </div>
    </div>
  )
}

// ─── Derived values type ──────────────────────────────────────────────────────

interface Derived {
  // Ratios (days) — from wcRatios current_series last point
  dso: number | null
  dpo: number | null
  dio: number | null
  ccc: number | null
  // Period deltas for ratios
  deltaDso: number | null
  deltaDpo: number | null
  deltaDio: number | null
  deltaCcc: number | null
  // Stock levels (kEUR) — from wcTimeline last point / summaryWc.levels[].level ÷ 1000
  trLevel: number | null
  invLevel: number | null
  apLevel: number | null
  nwc: number | null
  // Month-on-month stock deltas (kEUR)
  deltaTr: number | null
  deltaInv: number | null
  deltaAp: number | null
  // FY stock deltas (kEUR) — P3: populated from summaryWc.levels[].delta_fy ÷ 1000
  deltaFyTr: number | null
  deltaFyInv: number | null
  deltaFyAp: number | null
}

function derivedFromData(
  ratios: WcRatiosData,
  timeline: WcTimelineResponse,
): Derived {
  const rSeries = ratios.current_series
  const tSeries = timeline.series

  const rLast = rSeries.length > 0 ? rSeries[rSeries.length - 1] : null
  const rPrev = rSeries.length > 1 ? rSeries[rSeries.length - 2] : null
  const tLast = tSeries.length > 0 ? tSeries[tSeries.length - 1] : null
  const tPrev = tSeries.length > 1 ? tSeries[tSeries.length - 2] : null

  const dso = rLast?.dso ?? null
  const dpo = rLast?.dpo ?? null
  const dio = rLast?.dio ?? null
  // CCC = DSO + DIO − DPO  (matches compute_wc_kpis exactly — signed-off formula)
  const ccc =
    dso != null && dpo != null && dio != null ? dso + dio - dpo : null

  const dsoPrev = rPrev?.dso ?? null
  const dpoPrev = rPrev?.dpo ?? null
  const dioPrev = rPrev?.dio ?? null
  const cccPrev =
    dsoPrev != null && dpoPrev != null && dioPrev != null
      ? dsoPrev + dioPrev - dpoPrev
      : null

  const deltaDso =
    dso != null && dsoPrev != null ? dso - dsoPrev : null
  const deltaDpo =
    dpo != null && dpoPrev != null ? dpo - dpoPrev : null
  const deltaDio =
    dio != null && dioPrev != null ? dio - dioPrev : null
  const deltaCcc =
    ccc != null && cccPrev != null ? ccc - cccPrev : null

  // Stock levels (kEUR) from wc-timeline
  const trLevel = tLast?.trade_receivables ?? null
  const invLevel = tLast?.inventories ?? null
  const apLevel = tLast?.trade_payables ?? null
  const nwc = tLast?.nwc ?? null

  const trPrev = tPrev?.trade_receivables ?? null
  const invPrev = tPrev?.inventories ?? null
  const apPrev = tPrev?.trade_payables ?? null

  const deltaTr =
    trLevel != null && trPrev != null ? trLevel - trPrev : null
  const deltaInv =
    invLevel != null && invPrev != null ? invLevel - invPrev : null
  const deltaAp =
    apLevel != null && apPrev != null ? apLevel - apPrev : null

  return {
    dso, dpo, dio, ccc,
    deltaDso, deltaDpo, deltaDio, deltaCcc,
    trLevel, invLevel, apLevel, nwc,
    deltaTr, deltaInv, deltaAp,
    // Δfy not available from the P2 wcRatios / wcTimeline endpoints
    deltaFyTr: null, deltaFyInv: null, deltaFyAp: null,
  }
}

/**
 * Derive the same shape from the P3 batched summary payload.
 * Raw-EUR amounts (levels[].level / delta_month / delta_fy) are divided by
 * 1000 so that StockRow receives kEUR — matching the legacy wcTimeline path.
 * Ratio-KPI period deltas are not in the summary payload so they are null.
 */
function derivedFromSummaryWc(wc: OverviewSummaryWc): Derived {
  const toK = (v: number | undefined | null): number | null =>
    v != null ? Math.round(v / 1000) : null

  const tr  = wc.levels.find(l => l.key === 'trade_receivables')
  const inv = wc.levels.find(l => l.key === 'inventories')
  const ap  = wc.levels.find(l => l.key === 'trade_payables')

  return {
    dso: wc.dso,
    dpo: wc.dpo,
    dio: wc.dio,
    ccc: wc.ccc,
    // Period deltas for ratio KPIs not supplied by the summary endpoint
    deltaDso: null,
    deltaDpo: null,
    deltaDio: null,
    deltaCcc: null,
    nwc:      toK(wc.nwc),
    trLevel:  toK(tr?.level),
    invLevel: toK(inv?.level),
    apLevel:  toK(ap?.level),
    deltaTr:  toK(tr?.delta_month),
    deltaInv: toK(inv?.delta_month),
    deltaAp:  toK(ap?.delta_month),
    // P3: Δfy now available from the batched summary
    deltaFyTr:  toK(tr?.delta_fy),
    deltaFyInv: toK(inv?.delta_fy),
    deltaFyAp:  toK(ap?.delta_fy),
  }
}

// ─── Findings builder (local, WC-specific) ────────────────────────────────────

function buildWcFindings(
  dv: Derived,
): Array<{ text: string; route: '/working-capital' }> {
  const route = '/working-capital' as const
  const findings: Array<{ text: string; route: '/working-capital' }> = []

  // Finding 1: DSO trend (FAV−)
  if (dv.dso != null) {
    const { dso, deltaDso } = dv
    if (deltaDso != null && Math.abs(deltaDso) >= 2) {
      const dir = deltaDso > 0 ? 'up' : 'down'
      const absDelta = Math.abs(deltaDso).toFixed(0)
      findings.push({
        text: `DSO ${dir} ${absDelta}d vs prior month (now ${Math.round(dso)}d) — review receivables collection pace.`,
        route,
      })
    } else {
      findings.push({
        text: `DSO at ${Math.round(dso)}d — check customer aging and payment terms on the Working Capital page.`,
        route,
      })
    }
  }

  // Finding 2: CCC summary (FAV−) — only if we haven't hit the limit
  if (findings.length < 2 && dv.ccc != null) {
    const { ccc, deltaCcc } = dv
    const deltaStr =
      deltaCcc != null
        ? ` (${deltaCcc >= 0 ? '+' : '−'}${Math.abs(deltaCcc).toFixed(0)}d vs prior month)`
        : ''
    findings.push({
      text: `Cash conversion cycle at ${Math.round(ccc)}d${deltaStr} — inventory and receivables drive working capital tie-up.`,
      route,
    })
  }

  return findings.slice(0, 2)
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function WorkingCapitalBlock({ year, month, entity, summaryWc }: Props) {
  // When the prop is supplied (even as null while the summary is loading),
  // we skip our own fetch and rely on the parent's data pipeline.
  const skipOwnFetch = summaryWc !== undefined

  const [ratios, setRatios] = useState<WcRatiosData | null>(null)
  const [timeline, setTimeline] = useState<WcTimelineResponse | null>(null)
  const [ownLoading, setOwnLoading] = useState(!skipOwnFetch)
  const [ownError, setOwnError] = useState<string | null>(null)

  useEffect(() => {
    if (skipOwnFetch) {
      // Clear any stale own-fetch state
      setOwnLoading(false)
      setOwnError(null)
      setRatios(null)
      setTimeline(null)
      return
    }

    let cancelled = false
    setOwnLoading(true)
    setOwnError(null)
    setRatios(null)
    setTimeline(null)

    Promise.all([
      api.wcRatios(year, month, 'month', entity),
      api.financialsWcTimeline(year, month, 'month', entity),
    ])
      .then(([ratiosRes, timelineRes]) => {
        if (!cancelled) {
          setRatios(ratiosRes.data)
          setTimeline(timelineRes)
          setOwnLoading(false)
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setOwnError(
            e instanceof Error
              ? e.message
              : 'Failed to load working capital data',
          )
          setOwnLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [year, month, entity, skipOwnFetch])

  // Effective state: summary path or own-fetch path
  const loading = skipOwnFetch ? summaryWc === null : ownLoading
  const error   = skipOwnFetch ? null : ownError

  const derived = useMemo<Derived | null>(
    () => {
      if (summaryWc != null) return derivedFromSummaryWc(summaryWc)
      return ratios && timeline ? derivedFromData(ratios, timeline) : null
    },
    [summaryWc, ratios, timeline],
  )

  const findings = useMemo(
    () => (derived ? buildWcFindings(derived) : []),
    [derived],
  )

  // Show Δ FY column only when the summary path supplies it
  const hasDeltaFy =
    derived != null &&
    (derived.deltaFyTr != null ||
      derived.deltaFyInv != null ||
      derived.deltaFyAp != null)

  return (
    <OverviewAnalysisBlock title="Working Capital" deepLink="/working-capital" compact>
      {loading ? (
        <LoadingSkeleton />
      ) : error ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          {error}
        </p>
      ) : !derived ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          No working capital data available for this period.
        </p>
      ) : (
        <div className="space-y-3">

          {/* ── KPI row: DSO / DPO / DIO / CCC ──────────────────────── */}
          <div
            className="grid grid-cols-4 gap-3 pb-3"
            style={{ borderBottom: '1px solid #F1F5F9' }}
          >
            <KpiChip
              label="DSO"
              value={derived.dso}
              delta={derived.deltaDso}
              favPlus={false}
            />
            <KpiChip
              label="DPO"
              value={derived.dpo}
              delta={derived.deltaDpo}
              favPlus={true}
            />
            <KpiChip
              label="DIO"
              value={derived.dio}
              delta={derived.deltaDio}
              favPlus={false}
            />
            <KpiChip
              label="CCC"
              value={derived.ccc}
              delta={derived.deltaCcc}
              favPlus={false}
            />
          </div>

          {/* ── NWC headline ─────────────────────────────────────────── */}
          {derived.nwc != null && (
            <div
              className="flex items-baseline gap-2 pb-3"
              style={{ borderBottom: '1px solid #F1F5F9' }}
            >
              <span
                className="text-[10px] font-medium uppercase tracking-wide"
                style={{ color: '#94A3B8' }}
              >
                NWC
              </span>
              <span
                className="text-sm font-semibold tabular-nums"
                style={{ color: '#1E3A5F' }}
              >
                {fmtKeurCompact(derived.nwc)}
              </span>
              <span className="text-[10px]" style={{ color: '#94A3B8' }}>
                kEUR
              </span>
            </div>
          )}

          {/* ── Balance-sheet stocks: level + Δ month [+ Δ FY] ──────── */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span
                className="text-[10px] font-semibold uppercase tracking-wide"
                style={{ color: '#94A3B8' }}
              >
                Balance sheet stocks
              </span>
              <span className="text-[10px]" style={{ color: '#CBD5E1' }}>
                {hasDeltaFy ? 'Level · Δ month · Δ FY' : 'Level · Δ month'}
              </span>
            </div>
            <StockRow
              label="Trade receivables"
              level={derived.trLevel}
              deltaMonth={derived.deltaTr}
              deltaFy={hasDeltaFy ? derived.deltaFyTr : undefined}
              favPlus={false}
            />
            <StockRow
              label="Inventories"
              level={derived.invLevel}
              deltaMonth={derived.deltaInv}
              deltaFy={hasDeltaFy ? derived.deltaFyInv : undefined}
              favPlus={false}
            />
            <StockRow
              label="Trade payables"
              level={derived.apLevel}
              deltaMonth={derived.deltaAp}
              deltaFy={hasDeltaFy ? derived.deltaFyAp : undefined}
              favPlus={true}
            />
          </div>

          {/* ── Findings chips ────────────────────────────────────────── */}
          {findings.length > 0 && (
            <div className="space-y-1.5 pt-1">
              {findings.map((f, i) => (
                <FindingChip key={i} text={f.text} route={f.route} />
              ))}
            </div>
          )}

        </div>
      )}
    </OverviewAnalysisBlock>
  )
}
