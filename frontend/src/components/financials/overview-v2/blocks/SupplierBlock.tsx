/**
 * SupplierBlock — Block (5): Supplier development.
 * P4: most-delivering suppliers (YTD cost), biggest cost increase, new spend callout,
 * purchase transaction count and avg-per-purchase metrics.
 *
 * FAV coloring rule (plan §2 Area 5, §7):
 *   Use the per-row `fav` flag from the API payload — do NOT recolor by raw delta sign.
 *   fav=true → muted green; fav=false → muted amber.
 *   `invert_delta` is a backend-internal signal; it does NOT change display value here.
 *
 * Data flows from useOverviewPartnersV2 called ONCE in OverviewPageV2 —
 * no per-block fetch fan-out.
 *
 * Deep-links per plan §1:
 *   /income-statement  — cost of materials / supplier cost breakdown
 *   /working-capital   — AP aging / DPO (new spend context)
 *
 * Scope: reporting-v2 / port 5177 only — tree-shaken from fdd-merge (5176)
 * and v4 (5178) bundles via IS_OVERVIEW_V2. See overview-v2-redesign-plan.md §5.
 */
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Info } from 'lucide-react'
import OverviewAnalysisBlock from '../OverviewAnalysisBlock'
import type {
  PartnersSuppliers,
  PartnersSupplierDelta,
  PartnersSupplierWon,
} from '../hooks/useOverviewPartnersV2'

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  /** Suppliers sub-object from useOverviewPartnersV2. Null while loading. */
  suppliers: PartnersSuppliers | null
  loading:   boolean
  error?:    string | null
}

// ─── Formatting helpers (kEUR in, display string out) ─────────────────────────

function fmtK(v: number): string {
  const abs = Math.abs(v)
  if (abs >= 1000) {
    return (
      (v / 1000).toLocaleString('de-DE', {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      }) + 'm'
    )
  }
  return v.toLocaleString('de-DE', { maximumFractionDigits: 0 }) + 'k'
}

function fmtKDelta(v: number): string {
  const sign = v >= 0 ? '+' : '−'
  const abs = Math.abs(v)
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

function partnerLabel(id: string, name: string | null): string {
  return name ?? id
}

/**
 * Restrained FAV color driven by the per-row `fav` flag.
 * Do NOT recolor by raw delta sign (plan §7).
 * fav=true  → muted green  (favorable cost movement)
 * fav=false → muted amber  (unfavorable cost movement)
 */
function favColor(fav: boolean): string {
  return fav ? '#16A34A' : '#B45309'
}

// ─── Loading skeleton ─────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div className="space-y-2.5 animate-pulse">
      {/* Section label placeholder */}
      <div className="h-3 w-24 rounded" style={{ background: '#F1F5F9' }} />
      {/* Supplier rows */}
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="h-6 rounded" style={{ background: '#F1F5F9' }} />
      ))}
      {/* Findings */}
      <div className="space-y-1.5 pt-1">
        <div className="h-8 rounded" style={{ background: '#F1F5F9' }} />
        <div className="h-8 rounded" style={{ background: '#F1F5F9' }} />
      </div>
    </div>
  )
}

// ─── Finding chip — ONE-LINER + deep-link arrow ────────────────────────────────

/**
 * fav: when provided, tints the info icon to signal favorability.
 * Undefined → neutral navy (e.g. new-spend callout has no clear FAV direction).
 */
function FindingChip({
  text,
  route,
  fav,
}: {
  text:  string
  route: string
  fav?:  boolean
}) {
  const navigate    = useNavigate()
  const iconColor   = fav === undefined ? '#1E3A5F' : favColor(fav)

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
      <Info size={11} aria-hidden style={{ color: iconColor, flexShrink: 0 }} />
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

// ─── Findings builder — supplier-specific one-liners ──────────────────────────

function buildFindings(
  increase: PartnersSupplierDelta[],
  won:      PartnersSupplierWon[],
): Array<{ text: string; route: string; fav?: boolean }> {
  const findings: Array<{ text: string; route: string; fav?: boolean }> = []

  // 1. Top cost increase (FAV−; use `fav` flag for icon tint, NOT raw sign)
  const topIncrease = increase[0]
  if (topIncrease) {
    const name      = partnerLabel(topIncrease.supplier_id, topIncrease.name)
    const deltaStr  = fmtKDelta(topIncrease.delta_yoy_keur)
    const qualifier = topIncrease.fav === 'plus' ? 'favorable' : 'unfavorable'
    findings.push({
      text:  `${name} cost ${deltaStr} YoY — ${qualifier} cost movement vs prior year.`,
      route: '/income-statement',
      fav:   topIncrease.fav === 'plus',
    })
  }

  // Show secondary cost increases when there are more (up to 1 extra)
  const secondIncrease = increase[1]
  if (secondIncrease && findings.length < 2) {
    const name     = partnerLabel(secondIncrease.supplier_id, secondIncrease.name)
    const deltaStr = fmtKDelta(secondIncrease.delta_yoy_keur)
    findings.push({
      text:  `${name} also up ${deltaStr} YoY — review cost-of-materials trend.`,
      route: '/income-statement',
      fav:   secondIncrease.fav === 'plus',
    })
  }

  // 2. New spend (won suppliers — kind: "new_spend")
  if (won.length > 0) {
    const top     = won[0]
    const topName = partnerLabel(top.supplier_id, top.name)
    const text =
      won.length === 1
        ? `${topName}: new supplier — ${fmtK(top.cost_cur_keur)} new procurement spend.`
        : `${won.length} new suppliers — ${topName} leads at ${fmtK(top.cost_cur_keur)} new spend.`
    findings.push({ text, route: '/working-capital', fav: undefined })
  }

  return findings.slice(0, 3)
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function SupplierBlock({ suppliers, loading, error }: Props) {
  const [visibleCount, setVisibleCount] = useState(5)

  // Reset expanded state whenever the underlying data changes (period/entity switch).
  useEffect(() => {
    setVisibleCount(5)
  }, [suppliers])

  const biggest  = suppliers?.biggest  ?? []
  const increase = suppliers?.increase ?? []
  const won      = suppliers?.won      ?? []

  const maxCost =
    biggest.length > 0
      ? Math.max(...biggest.map((r) => r.cost_ytd_keur))
      : 0

  const findings = buildFindings(increase, won)

  // Purchase stats from the top supplier (single-line metric)
  const top = biggest[0]
  const purchaseLine =
    top && top.purchase_txns > 0
      ? `${top.purchase_txns} transaction${top.purchase_txns !== 1 ? 's' : ''}${
          top.avg_per_purchase_keur != null
            ? ` · ${fmtK(top.avg_per_purchase_keur)} avg`
            : ''
        } — ${partnerLabel(top.supplier_id, top.name)}`
      : null

  return (
    <OverviewAnalysisBlock title="Supplier Development" deepLink="/income-statement" compact>
      {loading ? (
        <LoadingSkeleton />
      ) : error ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          {error}
        </p>
      ) : !suppliers || biggest.length === 0 ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          No supplier data available for this period.
        </p>
      ) : (
        <div className="space-y-3">

          {/* Section label */}
          <span
            className="text-[10px] font-semibold uppercase tracking-wide"
            style={{ color: '#94A3B8' }}
          >
            Top by YTD cost
          </span>

          {/* Ranked supplier list with inline proportional bars */}
          <div className="space-y-1.5">
            {biggest.slice(0, visibleCount).map((row) => {
              const barPct = maxCost > 0 ? (row.cost_ytd_keur / maxCost) * 100 : 0
              const name   = partnerLabel(row.supplier_id, row.name)
              return (
                <div key={row.supplier_id} className="flex items-center gap-2">
                  <span
                    className="text-[9px] font-bold tabular-nums shrink-0 text-right"
                    style={{ color: '#CBD5E1', width: '1rem' }}
                  >
                    #{row.rank}
                  </span>
                  <span
                    className="text-[11px] truncate shrink-0"
                    style={{ color: '#374151', width: '5.5rem' }}
                    title={name}
                  >
                    {name}
                  </span>
                  {/* Proportional bar — neutral slate (cost, not revenue) */}
                  <div
                    className="flex-1 h-1.5 rounded-full overflow-hidden"
                    style={{ background: '#F1F5F9' }}
                  >
                    <div
                      className="h-full rounded-full"
                      style={{
                        width:      `${barPct}%`,
                        background: '#64748B',
                        opacity:    0.5,
                      }}
                    />
                  </div>
                  <span
                    className="text-[11px] tabular-nums shrink-0 text-right"
                    style={{ color: '#374151', minWidth: '3.5rem' }}
                  >
                    {fmtK(row.cost_ytd_keur)}
                  </span>
                </div>
              )
            })}
          </div>

          {/* Show more / Show less controls */}
          {biggest.length > 5 && (
            <div className="flex items-center gap-3">
              {visibleCount < biggest.length && (
                <button
                  className="text-[10px] font-medium transition-colors hover:text-slate-600"
                  style={{ color: '#94A3B8' }}
                  onClick={() => setVisibleCount((c) => Math.min(c + 5, biggest.length))}
                >
                  Show 5 more
                </button>
              )}
              {visibleCount > 5 && (
                <button
                  className="text-[10px] font-medium transition-colors hover:text-slate-600"
                  style={{ color: '#94A3B8' }}
                  onClick={() => setVisibleCount(5)}
                >
                  Show less
                </button>
              )}
            </div>
          )}

          {/* Purchase metric line (top supplier) */}
          {purchaseLine && (
            <p
              className="text-[10px]"
              style={{
                color:      '#94A3B8',
                borderTop:  '1px solid #F1F5F9',
                paddingTop: '0.375rem',
              }}
            >
              {purchaseLine}
            </p>
          )}

          {/* One-liner findings with deep-link arrows */}
          {findings.length > 0 && (
            <div className="space-y-1.5 pt-1">
              {findings.map((f, i) => (
                <FindingChip key={i} text={f.text} route={f.route} fav={f.fav} />
              ))}
            </div>
          )}

        </div>
      )}
    </OverviewAnalysisBlock>
  )
}
