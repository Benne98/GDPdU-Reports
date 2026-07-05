/**
 * CustomerBlock — Block (4): Customer development.
 * P4: biggest customers (YTD/MTD toggle), largest YoY gainers, won/lost callouts,
 * invoice count and avg-per-invoice metrics.
 *
 * Data flows from useOverviewPartnersV2 called ONCE in OverviewPageV2 —
 * no per-block fetch fan-out.
 *
 * Deep-links per plan §1:
 *   /income-statement  — partner sales / revenue breakdown
 *   /working-capital   — AR aging / DSO (lost customer context)
 *
 * Scope: reporting-v2 / port 5177 only — tree-shaken from fdd-merge (5176)
 * and v4 (5178) bundles via IS_OVERVIEW_V2. See overview-v2-redesign-plan.md §5.
 */
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Info } from 'lucide-react'
import OverviewAnalysisBlock from '../OverviewAnalysisBlock'
import type {
  PartnersCustomers,
  PartnersCustomerDelta,
  PartnersCustomerLost,
} from '../hooks/useOverviewPartnersV2'

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  /** Customers sub-object from useOverviewPartnersV2. Null while loading. */
  customers: PartnersCustomers | null
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

// ─── Loading skeleton ─────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div className="space-y-2.5 animate-pulse">
      {/* Toggle placeholder */}
      <div className="flex gap-1.5">
        <div className="h-5 w-10 rounded" style={{ background: '#F1F5F9' }} />
        <div className="h-5 w-10 rounded" style={{ background: '#F1F5F9' }} />
      </div>
      {/* Customer rows */}
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

// ─── Findings builder — customer-specific one-liners ──────────────────────────

function buildFindings(
  increase: PartnersCustomerDelta[],
  won:      PartnersCustomerDelta[],
  lost:     PartnersCustomerLost[],
): Array<{ text: string; route: string }> {
  const findings: Array<{ text: string; route: string }> = []

  // 1. Top YoY gainer (increase list excludes new customers — those appear in won)
  const topGainer = increase[0]
  if (topGainer) {
    const name = partnerLabel(topGainer.customer_id, topGainer.name)
    findings.push({
      text: `${name} largest YoY gain at ${fmtKDelta(topGainer.delta_yoy_keur)} — ${fmtK(topGainer.rev_cur_keur)} current vs ${fmtK(topGainer.rev_py_keur)} prior year.`,
      route: '/income-statement',
    })
  }

  // 2. Won (new) customers callout
  if (won.length > 0) {
    const top = won[0]
    const topName = partnerLabel(top.customer_id, top.name)
    const text =
      won.length === 1
        ? `${topName} won as new customer — ${fmtK(top.delta_yoy_keur)} new revenue this period.`
        : `${won.length} new customers won — ${topName} leads at ${fmtK(top.delta_yoy_keur)}.`
    findings.push({ text, route: '/income-statement' })
  }

  // 3. Lost customers callout (deep-link to working-capital for AR/aging context)
  if (lost.length > 0) {
    const top = lost[0]
    const topName = partnerLabel(top.customer_id, top.name)
    const text =
      lost.length === 1
        ? `${topName} lost — ${fmtK(top.rev_prior_keur)} prior revenue no longer active.`
        : `${lost.length} customers lost — ${topName} largest at ${fmtK(top.rev_prior_keur)}.`
    findings.push({ text, route: '/working-capital' })
  }

  return findings.slice(0, 3)
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function CustomerBlock({ customers, loading, error }: Props) {
  const [view, setView] = useState<'ytd' | 'cm'>('ytd')
  const [visibleCount, setVisibleCount] = useState(5)

  // Reset expanded state whenever the underlying data changes (period/entity switch).
  useEffect(() => {
    setVisibleCount(5)
  }, [customers])

  const biggest  = customers?.biggest  ?? []
  const increase = customers?.increase ?? []
  const won      = customers?.won      ?? []
  const lost     = customers?.lost     ?? []

  const maxRev =
    biggest.length > 0
      ? Math.max(
          ...biggest.map((r) =>
            view === 'ytd' ? r.rev_ytd_keur : r.rev_cm_keur,
          ),
        )
      : 0

  const findings = buildFindings(increase, won, lost)

  // Invoice stats from the top customer (most meaningful single-line metric)
  const top = biggest[0]
  const invoiceLine =
    top && top.invoice_count > 0
      ? `${top.invoice_count} invoice${top.invoice_count !== 1 ? 's' : ''}${
          top.avg_per_invoice_keur != null
            ? ` · ${fmtK(top.avg_per_invoice_keur)} avg`
            : ''
        } — ${partnerLabel(top.customer_id, top.name)}`
      : null

  return (
    <OverviewAnalysisBlock title="Customer Development" deepLink="/income-statement" compact>
      {loading ? (
        <LoadingSkeleton />
      ) : error ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          {error}
        </p>
      ) : !customers || biggest.length === 0 ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          No customer data available for this period.
        </p>
      ) : (
        <div className="space-y-3">

          {/* YTD / MTD view toggle */}
          <div className="flex gap-1">
            {(['ytd', 'cm'] as const).map((v) => (
              <button
                key={v}
                className="px-2.5 py-0.5 rounded text-[10px] font-semibold transition-colors"
                style={{
                  background: view === v ? '#1E3A5F' : 'transparent',
                  color:      view === v ? '#FFFFFF' : '#94A3B8',
                  border:     `1px solid ${view === v ? '#1E3A5F' : '#E2E8F0'}`,
                }}
                onClick={() => setView(v)}
              >
                {v === 'ytd' ? 'YTD' : 'MTD'}
              </button>
            ))}
          </div>

          {/* Ranked customer list with inline proportional bars */}
          <div className="space-y-1.5">
            {biggest.slice(0, visibleCount).map((row) => {
              const rev    = view === 'ytd' ? row.rev_ytd_keur : row.rev_cm_keur
              const barPct = maxRev > 0 ? (rev / maxRev) * 100 : 0
              const name   = partnerLabel(row.customer_id, row.name)
              return (
                <div key={row.customer_id} className="flex items-center gap-2">
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
                  {/* Proportional bar */}
                  <div
                    className="flex-1 h-1.5 rounded-full overflow-hidden"
                    style={{ background: '#F1F5F9' }}
                  >
                    <div
                      className="h-full rounded-full"
                      style={{
                        width:      `${barPct}%`,
                        background: '#1E3A5F',
                        opacity:    0.6,
                      }}
                    />
                  </div>
                  <span
                    className="text-[11px] tabular-nums shrink-0 text-right"
                    style={{ color: '#1E3A5F', minWidth: '3.5rem' }}
                  >
                    {fmtK(rev)}
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

          {/* Invoice metric line (top customer) */}
          {invoiceLine && (
            <p
              className="text-[10px] pt-1"
              style={{
                color:       '#94A3B8',
                borderTop:   '1px solid #F1F5F9',
                paddingTop:  '0.375rem',
              }}
            >
              {invoiceLine}
            </p>
          )}

          {/* One-liner findings with deep-link arrows */}
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
