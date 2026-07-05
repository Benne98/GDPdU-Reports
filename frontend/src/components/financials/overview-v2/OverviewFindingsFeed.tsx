/**
 * OverviewFindingsFeed — renders up to 5 actionable finding cards.
 * Each card is one sentence + a deep-link arrow. No "go to X and click Y" prose.
 * Findings are computed by buildFindingsP1 (findingsBuilders.ts).
 */
import { useNavigate } from 'react-router-dom'
import { ArrowRight, AlertTriangle, AlertCircle, Info } from 'lucide-react'
import type { OverviewFinding } from './findingsModel'

// ─── Severity helpers ─────────────────────────────────────────────────────────

function severityIcon(severity: OverviewFinding['severity']) {
  switch (severity) {
    case 'critical': return AlertTriangle
    case 'warning':  return AlertCircle
    default:         return Info
  }
}

function severityAccent(severity: OverviewFinding['severity']): string {
  switch (severity) {
    case 'critical': return '#DC2626'
    case 'warning':  return '#B45309'
    default:         return '#2563EB'
  }
}

// ─── Single card ──────────────────────────────────────────────────────────────

function FindingCard({ finding }: { finding: OverviewFinding }) {
  const navigate = useNavigate()
  const Icon = severityIcon(finding.severity)
  const accent = severityAccent(finding.severity)

  function handleActivate() {
    navigate(finding.route)
  }

  return (
    <div
      className="flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition-colors"
      style={{
        background: '#FFFFFF',
        border: '1px solid #E2E8F0',
        borderLeft: `3px solid ${accent}`,
        boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
      }}
      role="button"
      tabIndex={0}
      onClick={handleActivate}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleActivate() }}
    >
      <Icon size={14} aria-hidden style={{ color: accent, flexShrink: 0 }} />
      <p className="flex-1 text-sm leading-snug" style={{ color: '#374151' }}>
        {finding.text}
      </p>
      <ArrowRight size={13} aria-hidden style={{ color: '#94A3B8', flexShrink: 0 }} />
    </div>
  )
}

// ─── Loading skeleton ─────────────────────────────────────────────────────────

function FindingsSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          className="rounded-lg h-11 animate-pulse"
          style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
        />
      ))}
    </div>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

interface Props {
  findings: OverviewFinding[]
  /** Show skeleton while briefing is loading. */
  loading?: boolean
}

export default function OverviewFindingsFeed({ findings, loading }: Props) {
  return (
    <section aria-label="Key findings" className="space-y-3">
      <h2
        className="text-xs font-semibold uppercase tracking-widest"
        style={{ color: '#1E3A5F' }}
      >
        Key findings
      </h2>

      {loading ? (
        <FindingsSkeleton />
      ) : findings.length === 0 ? (
        <p className="text-xs" style={{ color: '#94A3B8' }}>
          No findings available for this period and entity selection.
        </p>
      ) : (
        <div className="space-y-2">
          {findings.slice(0, 5).map(f => (
            <FindingCard key={f.id} finding={f} />
          ))}
        </div>
      )}
    </section>
  )
}
