/**
 * PerformanceBlock — Block (1): Revenue YoY + vs-Plan + recent-months alerting.
 *
 * P2: driven by the batched summary payload (hero + performance + alerts).
 * When `summaryData` is supplied by OverviewPageV2, zero extra round-trips.
 *
 * Displays:
 *   1. Revenue CM headline + YoY % + vs-Plan variance (when plan exists).
 *   2. Gross margin % + YoY pp + vs-Plan pp.
 *   3. EBIT margin headline.
 *   4. Up to 3 recent-month exception alerts (T1/T2/T3 triggers).
 *
 * Amounts in `performance.revenue` / `ebit.cm` are raw EUR (÷1000 → kEUR).
 * Gross margin and alert metric values are already in native units (%, kEUR).
 *
 * Scope: reporting-v2 / port 5177 only — tree-shaken via IS_OVERVIEW_V2.
 */
import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, AlertTriangle, TrendingDown, TrendingUp } from 'lucide-react'
import OverviewAnalysisBlock from '../OverviewAnalysisBlock'
import type {
  OverviewSummaryAlert,
  OverviewSummaryData,
  OverviewSummaryPerformance,
} from '../hooks/useOverviewSummaryV2'

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  summaryData:    OverviewSummaryData | null
  summaryLoading: boolean
  summaryError:   string | null
}

// ─── Formatting (raw EUR → kEUR display) ──────────────────────────────────────

function fmtEurAsKeur(eur: number): string {
  const k = Math.round(eur / 1_000)
  const abs = Math.abs(k).toLocaleString('de-DE')
  return eur < 0 ? `(${abs})` : abs
}

function fmtSignedKeurDelta(eur: number): string {
  const k = Math.round(eur / 1_000)
  const sign = k >= 0 ? '+' : '−'
  return `${sign}${Math.abs(k).toLocaleString('de-DE')}`
}

function fmtPct(v: number, signed = false): string {
  const s = v.toLocaleString('de-DE', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })
  if (!signed) return `${s}%`
  const prefix = v > 0 ? '+' : v < 0 ? '−' : ''
  return `${prefix}${Math.abs(v).toLocaleString('de-DE', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}%`
}

function fmtPp(v: number): string {
  const sign = v >= 0 ? '+' : '−'
  return `${sign}${Math.abs(v).toLocaleString('de-DE', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} pp`
}

// ─── Color helpers (muted palette) ───────────────────────────────────────────

function favColor(delta: number | null, favPlus: boolean): string {
  if (delta == null || Math.abs(delta) < 0.05) return '#64748B'
  const isFav = favPlus ? delta > 0 : delta < 0
  return isFav ? '#16A34A' : '#B45309'
}

// ─── KPI chip ─────────────────────────────────────────────────────────────────

interface KpiChipProps {
  label: string
  value: string
  sub?: string | null
  subColor?: string
}

function KpiChip({ label, value, sub, subColor = '#64748B' }: KpiChipProps) {
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
        {value}
      </span>
      {sub != null && (
        <span className="text-[10px] tabular-nums" style={{ color: subColor }}>
          {sub}
        </span>
      )}
    </div>
  )
}

// ─── Plan variance row ────────────────────────────────────────────────────────

function PlanRow({
  label,
  actual,
  plan,
  delta,
  deltaPct,
  unit,
}: {
  label: string
  actual: string
  plan: string
  delta: string
  deltaPct: string | null
  unit: string
}) {
  return (
    <div
      className="flex items-center justify-between py-1.5"
      style={{ borderBottom: '1px solid #F8FAFC' }}
    >
      <span className="text-xs truncate pr-2" style={{ color: '#374151' }}>
        {label}
      </span>
      <div className="flex items-center gap-3 shrink-0 text-[10px] tabular-nums">
        <span style={{ color: '#1E3A5F' }}>{actual}&thinsp;{unit}</span>
        <span style={{ color: '#94A3B8' }}>Plan {plan}</span>
        <span style={{ color: '#B45309' }}>{delta}</span>
        {deltaPct != null && (
          <span style={{ color: '#64748B' }}>({deltaPct})</span>
        )}
      </div>
    </div>
  )
}

// ─── Alert chip ───────────────────────────────────────────────────────────────

function metricLabel(metric: string): string {
  return metric === 'gross_margin' ? 'Gross margin' : 'Revenue'
}

function AlertChip({ alert }: { alert: OverviewSummaryAlert }) {
  const navigate = useNavigate()
  const isDown = alert.direction === 'down'
  const Icon = isDown ? TrendingDown : TrendingUp
  const accent =
    alert.severity >= 3 ? '#B45309' : alert.severity === 2 ? '#D97706' : '#64748B'
  const triggers = (alert.triggers ?? []).join(' · ')
  const text = `${alert.label ?? ''} · ${alert.entity}: ${metricLabel(alert.metric)} ${alert.direction} vs reference (${triggers})`

  return (
    <div
      className="flex items-start gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors hover:bg-slate-50"
      style={{ background: '#F8FAFC', border: '1px solid #E2E8F0' }}
      role="button"
      tabIndex={0}
      onClick={() => navigate('/income-statement')}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') navigate('/income-statement')
      }}
      aria-label={text}
    >
      <AlertTriangle size={11} aria-hidden style={{ color: accent, flexShrink: 0, marginTop: 2 }} />
      <div className="flex-1 min-w-0">
        <p className="text-[11px] leading-snug" style={{ color: '#374151' }}>
          {text}
        </p>
        <span className="text-[9px] font-medium" style={{ color: accent }}>
          Severity {alert.severity}
        </span>
      </div>
      <Icon size={10} aria-hidden style={{ color: accent, flexShrink: 0, marginTop: 2 }} />
      <ArrowRight size={10} aria-hidden style={{ color: '#CBD5E1', flexShrink: 0, marginTop: 2 }} />
    </div>
  )
}

// ─── Loading skeleton ─────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div className="space-y-3 animate-pulse">
      <div className="grid grid-cols-3 gap-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-12 rounded" style={{ background: '#F1F5F9' }} />
        ))}
      </div>
      <div className="h-16 rounded-lg" style={{ background: '#F1F5F9' }} />
      <div className="space-y-1.5">
        {[1, 2].map((i) => (
          <div key={i} className="h-10 rounded-lg" style={{ background: '#F1F5F9' }} />
        ))}
      </div>
    </div>
  )
}

// ─── Derived display values ───────────────────────────────────────────────────

function usePerformanceView(
  perf: OverviewSummaryPerformance | null | undefined,
  alerts: OverviewSummaryAlert[] | undefined,
) {
  return useMemo(() => {
    if (!perf) return null
    const rev = perf.revenue
    const gm = perf.gross_margin
    const yoyPct = rev.yoy_pct
    const yoyColor = favColor(yoyPct, true)

    const planDeltaColor = favColor(rev.plan_vs_actual, true)
    const gmYoyColor = favColor(gm.yoy_pp, true)
    const gmPlanColor = favColor(gm.plan_vs_actual_pp, true)

    return {
      rev,
      gm,
      ebit: perf.ebit,
      yoyPct,
      yoyColor,
      planDeltaColor,
      gmYoyColor,
      gmPlanColor,
      topAlerts: (alerts ?? []).slice(0, 3),
    }
  }, [perf, alerts])
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function PerformanceBlock({
  summaryData,
  summaryLoading,
  summaryError,
}: Props) {
  const view = usePerformanceView(summaryData?.performance, summaryData?.alerts)

  return (
    <OverviewAnalysisBlock title="Performance YoY / Plan" deepLink="/income-statement" compact>
      {summaryLoading ? (
        <LoadingSkeleton />
      ) : summaryError ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          {summaryError}
        </p>
      ) : !view ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          No performance data available for this period.
        </p>
      ) : (
        <div className="space-y-4">

          {/* ── KPI row: Revenue CM · YoY · EBIT margin ───────────────── */}
          <div
            className="grid grid-cols-3 gap-3 pb-3"
            style={{ borderBottom: '1px solid #F1F5F9' }}
          >
            <KpiChip
              label="Revenue CM"
              value={`${fmtEurAsKeur(view.rev.cm)} kEUR`}
              sub={
                view.yoyPct != null
                  ? `${fmtPct(view.yoyPct, true)} YoY`
                  : null
              }
              subColor={view.yoyColor}
            />
            <KpiChip
              label="Gross margin"
              value={view.gm.pct != null ? fmtPct(view.gm.pct) : '—'}
              sub={
                view.gm.yoy_pp != null ? `${fmtPp(view.gm.yoy_pp)} YoY` : null
              }
              subColor={view.gmYoyColor}
            />
            <KpiChip
              label="EBIT margin"
              value={
                view.ebit.margin_pct != null
                  ? fmtPct(view.ebit.margin_pct)
                  : '—'
              }
            />
          </div>

          {/* ── vs-Plan (when plan data exists) ───────────────────────── */}
          {view.rev.has_plan && view.rev.plan_cm != null && (
            <div>
              <span
                className="text-[10px] font-semibold uppercase tracking-wide block mb-1.5"
                style={{ color: '#94A3B8' }}
              >
                vs Plan (CM)
              </span>
              <div
                className="rounded-lg px-3 py-0.5"
                style={{ background: '#F8FAFC', border: '1px solid #F1F5F9' }}
              >
                <PlanRow
                  label="Revenue"
                  actual={fmtEurAsKeur(view.rev.cm)}
                  plan={fmtEurAsKeur(view.rev.plan_cm)}
                  delta={fmtSignedKeurDelta(view.rev.plan_vs_actual ?? 0)}
                  deltaPct={
                    view.rev.var_pct != null
                      ? fmtPct(view.rev.var_pct, true)
                      : null
                  }
                  unit="kEUR"
                />
                {view.gm.plan_pct != null && view.gm.pct != null && (
                  <PlanRow
                    label="Gross margin"
                    actual={fmtPct(view.gm.pct)}
                    plan={fmtPct(view.gm.plan_pct)}
                    delta={
                      view.gm.plan_vs_actual_pp != null
                        ? fmtPp(view.gm.plan_vs_actual_pp)
                        : '—'
                    }
                    deltaPct={null}
                    unit=""
                  />
                )}
                {view.rev.coverage_pct != null && (
                  <div className="flex justify-end py-1.5">
                    <span className="text-[10px]" style={{ color: '#64748B' }}>
                      Coverage {fmtPct(view.rev.coverage_pct)}
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}

          {!view.rev.has_plan && (
            <p className="text-[10px] text-center py-1" style={{ color: '#CBD5E1' }}>
              No plan data for this period
            </p>
          )}

          {/* ── Recent-month alerts ───────────────────────────────────── */}
          {view.topAlerts.length > 0 && (
            <div className="space-y-1.5">
              <span
                className="text-[10px] font-semibold uppercase tracking-wide block"
                style={{ color: '#94A3B8' }}
              >
                Recent-month alerts
              </span>
              {view.topAlerts.map((a, i) => (
                <AlertChip key={`${a.entity}-${a.metric}-${a.year}-${a.month}-${i}`} alert={a} />
              ))}
            </div>
          )}

        </div>
      )}
    </OverviewAnalysisBlock>
  )
}
