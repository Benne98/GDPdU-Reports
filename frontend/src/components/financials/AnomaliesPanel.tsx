/**
 * AnomaliesPanel — compact panel showing anomalies for a given period/entity.
 * Used on the Overview page (and optionally statement pages) next to narratives.
 * Also reused as the full list on AnomalyDetectionPage.
 */
import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, AlertCircle, Info, Loader2 } from 'lucide-react'
import { api, type AnomalyItem, type AnomaliesResponse, type AnomalyPeriodParams } from '../../lib/api'

// ─── Helpers ─────────────────────────────────────────────────────────────────

const STATEMENT_LABEL: Record<string, string> = {
  pl: 'P&L',
  bs: 'Balance Sheet',
  wc: 'Working Capital',
  cf: 'Cash Flow',
}

const KIND_LABEL: Record<string, string> = {
  mom_swing:       'MoM Swing',
  yoy_swing:       'YoY Swing',
  sign_flip:       'Sign Flip',
  balance_break:   'Balance Break',
  gl_concentration:'GL Concentration',
}

function toKEur(eur: number): string {
  const k = eur / 1000
  const sign = k < 0 ? '-' : ''
  return `${sign}${Math.abs(k).toLocaleString('de-DE', { minimumFractionDigits: 0, maximumFractionDigits: 1 })} kEUR`
}

function SeverityIcon({ severity }: { severity: AnomalyItem['severity'] }) {
  if (severity === 'high')   return <AlertTriangle size={13} style={{ color: '#DC2626' }} aria-hidden />
  if (severity === 'medium') return <AlertCircle   size={13} style={{ color: '#D97706' }} aria-hidden />
  return                            <Info           size={13} style={{ color: '#2563EB' }} aria-hidden />
}

function severityBg(severity: AnomalyItem['severity']): React.CSSProperties {
  if (severity === 'high')   return { background: 'rgba(220,38,38,0.08)',  border: '1px solid rgba(220,38,38,0.25)',  color: '#B91C1C' }
  if (severity === 'medium') return { background: 'rgba(217,119,6,0.08)',  border: '1px solid rgba(217,119,6,0.25)',  color: '#92400E' }
  return                            { background: 'rgba(37,99,235,0.06)',   border: '1px solid rgba(37,99,235,0.2)',   color: '#1D4ED8' }
}

function KindBadge({ kind }: { kind: AnomalyItem['kind'] }) {
  return (
    <span
      className="inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
      style={{ background: 'rgba(30,58,95,0.07)', color: '#1E3A5F' }}
    >
      {KIND_LABEL[kind] ?? kind}
    </span>
  )
}

// ─── Single anomaly row ───────────────────────────────────────────────────────

function AnomalyRow({ item }: { item: AnomalyItem }) {
  return (
    <div
      className="flex items-start gap-3 rounded-lg px-3 py-2.5"
      style={severityBg(item.severity)}
    >
      <div className="mt-0.5 shrink-0">
        <SeverityIcon severity={item.severity} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5 mb-0.5">
          <span className="text-xs font-semibold truncate" style={{ color: 'inherit' }}>
            {item.label}
          </span>
          <KindBadge kind={item.kind} />
          <span className="text-[10px] font-medium ml-auto shrink-0" style={{ color: 'inherit', opacity: 0.75 }}>
            {toKEur(item.magnitude_eur)}
          </span>
        </div>
        <p className="text-[11px] leading-relaxed" style={{ color: 'inherit', opacity: 0.85 }}>
          {item.description}
        </p>
        {item.period_label && (
          <p className="text-[10px] mt-0.5" style={{ color: 'inherit', opacity: 0.6 }}>
            {item.period_label}
          </p>
        )}
      </div>
    </div>
  )
}

// ─── Grouped by statement ─────────────────────────────────────────────────────

function groupByStatement(items: AnomalyItem[]): Map<string, AnomalyItem[]> {
  const map = new Map<string, AnomalyItem[]>()
  for (const item of items) {
    const key = item.statement
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(item)
  }
  return map
}

function groupBySeverity(items: AnomalyItem[]): { high: AnomalyItem[]; medium: AnomalyItem[]; low: AnomalyItem[] } {
  return {
    high:   items.filter(i => i.severity === 'high'),
    medium: items.filter(i => i.severity === 'medium'),
    low:    items.filter(i => i.severity === 'low'),
  }
}

// ─── Loading screen (anomaly detection is slow — keep the user informed) ──────

export function AnomaliesLoading({ compact, className }: { compact: boolean; className?: string }) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setElapsed(e => e + 1), 1000)
    return () => clearInterval(t)
  }, [])

  const steps = [
    'Scanning P&L movements…',
    'Checking Balance Sheet positions…',
    'Analysing Working Capital & Cash Flow…',
    'Detecting GL concentration…',
    'Almost there…',
  ]
  const step = steps[Math.min(steps.length - 1, Math.floor(elapsed / 8))]

  return (
    <div
      className={`rounded-xl flex flex-col items-center justify-center text-center ${compact ? 'px-5 py-8' : 'px-6 py-14'} ${className ?? ''}`}
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
      role="status"
      aria-live="polite"
    >
      <Loader2 size={compact ? 22 : 32} className="animate-spin mb-4" style={{ color: '#1E3A5F' }} aria-hidden />
      <p className="text-sm font-semibold mb-1.5" style={{ color: '#1E3A5F' }}>
        Running anomaly detection…
      </p>
      <p className="text-xs max-w-md leading-relaxed mb-3" style={{ color: '#94A3B8' }}>
        Scanning P&amp;L, Balance Sheet, Working Capital and Cash Flow for material swings,
        sign flips, balance breaks and GL concentration. This can take up to a minute.
      </p>
      <p className="text-xs font-medium" style={{ color: '#64748B' }}>
        {step} · {elapsed}s
      </p>
    </div>
  )
}

// ─── Props ────────────────────────────────────────────────────────────────────

export type AnomaliesPanelMode = 'compact' | 'full'

interface Props {
  periodParams: AnomalyPeriodParams
  entity?: string
  /** compact = grouped by severity, summary counts; full = grouped by statement then severity */
  mode?: AnomaliesPanelMode
  /** Extra class on the outer container */
  className?: string
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function AnomaliesPanel({
  periodParams,
  entity,
  mode = 'compact',
  className,
}: Props) {
  const [data, setData] = useState<AnomaliesResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const loadId = useRef(0)

  useEffect(() => {
    let cancelled = false
    const id = ++loadId.current
    setLoading(true)
    setError(null)

    api.financialsAnomalies(periodParams, entity)
      .then(res => {
        if (cancelled || id !== loadId.current) return
        setData(res)
      })
      .catch((e: unknown) => {
        if (cancelled || id !== loadId.current) return
        setError(e instanceof Error ? e.message : 'Failed to load anomalies')
      })
      .finally(() => {
        if (!cancelled && id === loadId.current) setLoading(false)
      })

    return () => { cancelled = true }
  }, [periodParams, entity])

  // ── Loading state — informative screen with elapsed timer (detection is slow) ─
  if (loading) {
    return <AnomaliesLoading compact={mode === 'compact'} className={className} />
  }

  // ── Error state ────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div
        className={`rounded-xl px-5 py-4 text-xs ${className ?? ''}`}
        style={{ background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(220,38,38,0.3)', color: '#991B1B' }}
        role="alert"
      >
        <span className="font-semibold">Anomalies unavailable — </span>{error}
      </div>
    )
  }

  // ── Empty state ────────────────────────────────────────────────────────────
  if (!data || data.anomalies.length === 0) {
    return (
      <div
        className={`rounded-xl px-5 py-4 text-center ${className ?? ''}`}
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
      >
        <p className="text-xs font-medium" style={{ color: '#64748B' }}>
          No anomalies detected for this period.
        </p>
      </div>
    )
  }

  const anomalies = data.anomalies

  // ── Compact mode — grouped by severity ────────────────────────────────────
  if (mode === 'compact') {
    const { high, medium, low } = groupBySeverity(anomalies)
    const severityGroups = [
      { label: 'High', items: high, severity: 'high' as const },
      { label: 'Medium', items: medium, severity: 'medium' as const },
      { label: 'Low', items: low, severity: 'low' as const },
    ].filter(g => g.items.length > 0)

    return (
      <div
        className={`rounded-xl overflow-hidden ${className ?? ''}`}
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
      >
        <div
          className="px-5 py-3 flex items-center gap-2 border-b"
          style={{ borderColor: '#F1F5F9', background: '#FAFBFC' }}
        >
          <AlertTriangle size={13} style={{ color: '#D97706' }} aria-hidden />
          <h3 className="text-xs font-semibold uppercase tracking-wide" style={{ color: '#64748B' }}>
            Anomalies
          </h3>
          <span
            className="ml-auto text-xs font-semibold rounded-full px-2 py-0.5"
            style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
          >
            {anomalies.length}
          </span>
        </div>
        <div className="px-4 py-3 space-y-3">
          {severityGroups.map(({ label, items, severity }) => (
            <div key={severity}>
              <p
                className="text-[10px] font-semibold uppercase tracking-widest mb-1.5"
                style={{ color: severity === 'high' ? '#B91C1C' : severity === 'medium' ? '#92400E' : '#1D4ED8' }}
              >
                {label} ({items.length})
              </p>
              <div className="space-y-1.5">
                {items.map(item => <AnomalyRow key={item.id} item={item} />)}
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  // ── Full mode — grouped by statement, then severity ────────────────────────
  const byStatement = groupByStatement(anomalies)
  // Phase 0 (hierarchical anomaly rework): detection is PL + BS only — the backend
  // no longer scans WC/CF, so they never appear here.  (The full Phase 5 FE rework
  // moves navigation/views; this is the safe minimal restriction.)
  const STATEMENT_ORDER: Array<AnomalyItem['statement']> = ['pl', 'bs']

  return (
    <div className={`space-y-5 ${className ?? ''}`}>
      {STATEMENT_ORDER.filter(s => byStatement.has(s)).map(stmt => {
        const items = byStatement.get(stmt)!
        const { high, medium, low } = groupBySeverity(items)
        const groups = [
          { label: 'High', items: high, severity: 'high' as const },
          { label: 'Medium', items: medium, severity: 'medium' as const },
          { label: 'Low', items: low, severity: 'low' as const },
        ].filter(g => g.items.length > 0)

        return (
          <div
            key={stmt}
            className="rounded-xl overflow-hidden"
            style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
          >
            <div
              className="px-5 py-3 flex items-center gap-2 border-b"
              style={{ borderColor: '#F1F5F9', background: '#FAFBFC' }}
            >
              <h3 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>
                {STATEMENT_LABEL[stmt] ?? stmt.toUpperCase()}
              </h3>
              <span
                className="ml-auto text-xs font-semibold rounded-full px-2 py-0.5"
                style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
              >
                {items.length}
              </span>
            </div>
            <div className="px-4 py-3 space-y-3">
              {groups.map(({ label, items: gItems, severity }) => (
                <div key={severity}>
                  <p
                    className="text-[10px] font-semibold uppercase tracking-widest mb-1.5"
                    style={{ color: severity === 'high' ? '#B91C1C' : severity === 'medium' ? '#92400E' : '#1D4ED8' }}
                  >
                    {label} ({gItems.length})
                  </p>
                  <div className="space-y-1.5">
                    {gItems.map(item => <AnomalyRow key={item.id} item={item} />)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}
