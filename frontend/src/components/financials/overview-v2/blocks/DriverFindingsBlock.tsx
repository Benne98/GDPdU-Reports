/**
 * DriverFindingsBlock — Block (7): Driver / DuPont headline findings (≤3).
 * P2: real content — fetches DuPont data and renders findings via buildDuPontFindings.
 *
 * The full DuPont tree stays in LegacyDetailSection (already moved there in P1).
 * This block surfaces ≤3 one-liner findings with confirmed deep-links.
 *
 * Source: api.dupont(year, month, entity) — same endpoint consumed by DuPontTree.
 * Findings logic: dupontFindings.ts (overview-v2 subtree, no formula change).
 *
 * Scope: reporting-v2 / port 5177 only — tree-shaken from fdd-merge (5176) and
 * v4 (5178) bundles via the IS_OVERVIEW_V2 mode gate in OverviewPage.tsx.
 * See docs/overview-v2-redesign-plan.md §5.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, AlertCircle, AlertTriangle, Info } from 'lucide-react'
import { api, type DuPontData } from '../../../../lib/api'
import { buildDuPontFindings } from '../dupontFindings'
import OverviewAnalysisBlock from '../OverviewAnalysisBlock'
import type { OverviewFinding } from '../findingsModel'

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  year: number
  month: number
  entity?: string
  /**
   * When provided (from useOverviewSummaryV2), the block skips its own
   * api.dupont fetch and uses this data directly with buildDuPontFindings.
   * Undefined = prop not passed → legacy own-fetch. Null = summary loading.
   */
  summaryDupont?: DuPontData | null
}

// ─── Severity helpers (mirror OverviewFindingsFeed for visual consistency) ────

function severityIcon(s: OverviewFinding['severity']) {
  switch (s) {
    case 'critical': return AlertTriangle
    case 'warning':  return AlertCircle
    default:         return Info
  }
}

function severityAccent(s: OverviewFinding['severity']): string {
  switch (s) {
    case 'critical': return '#DC2626'
    case 'warning':  return '#B45309'
    default:         return '#1E3A5F'
  }
}

// ─── Finding row ──────────────────────────────────────────────────────────────

function FindingRow({ finding }: { finding: OverviewFinding }) {
  const navigate = useNavigate()
  const Icon = severityIcon(finding.severity)
  const accent = severityAccent(finding.severity)

  function handleActivate() {
    navigate(finding.route)
  }

  return (
    <div
      className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg cursor-pointer transition-colors hover:bg-slate-50"
      style={{
        borderLeft: `2px solid ${accent}`,
        background: '#FAFBFC',
        border: `1px solid #F1F5F9`,
        borderLeftColor: accent,
        borderLeftWidth: 2,
      }}
      role="button"
      tabIndex={0}
      onClick={handleActivate}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') handleActivate()
      }}
      aria-label={finding.text}
    >
      <Icon
        size={12}
        aria-hidden
        style={{ color: accent, flexShrink: 0, marginTop: 2 }}
      />
      <p className="flex-1 text-xs leading-snug" style={{ color: '#374151' }}>
        {finding.text}
      </p>
      <ArrowRight
        size={11}
        aria-hidden
        style={{ color: '#CBD5E1', flexShrink: 0, marginTop: 2 }}
      />
    </div>
  )
}

// ─── Loading skeleton ─────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div className="space-y-2 animate-pulse">
      {[1, 2, 3].map((i) => (
        <div
          key={i}
          className="rounded-lg h-10"
          style={{ background: '#F1F5F9' }}
        />
      ))}
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function DriverFindingsBlock({ year, month, entity, summaryDupont }: Props) {
  // When the prop is supplied (even as null while the summary loads),
  // skip the own api.dupont fetch and rely on the parent data pipeline.
  const skipOwnFetch = summaryDupont !== undefined

  const [ownDupontData, setOwnDupontData] = useState<DuPontData | null>(null)
  const [ownLoading, setOwnLoading] = useState(!skipOwnFetch)
  const [ownError, setOwnError] = useState<string | null>(null)

  useEffect(() => {
    if (skipOwnFetch) {
      setOwnLoading(false)
      setOwnError(null)
      setOwnDupontData(null)
      return
    }

    let cancelled = false
    setOwnLoading(true)
    setOwnError(null)
    setOwnDupontData(null)

    api
      .dupont(year, month, entity)
      .then((res) => {
        if (!cancelled) {
          setOwnDupontData(res.data)
          setOwnLoading(false)
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setOwnError(
            e instanceof Error ? e.message : 'Failed to load DuPont data',
          )
          setOwnLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [year, month, entity, skipOwnFetch])

  const loading = skipOwnFetch ? summaryDupont === null : ownLoading
  const error   = skipOwnFetch ? null : ownError

  const effectiveDupont = summaryDupont ?? ownDupontData
  const findings = effectiveDupont
    ? buildDuPontFindings(effectiveDupont, { max: 3 })
    : []

  return (
    <OverviewAnalysisBlock title="Driver Findings" deepLink="/income-statement">
      {loading ? (
        <LoadingSkeleton />
      ) : error ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          {error}
        </p>
      ) : findings.length === 0 ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          No DuPont data available for this period and entity.
        </p>
      ) : (
        <div className="space-y-2">
          {findings.map((f) => (
            <FindingRow key={f.id} finding={f} />
          ))}
          <p
            className="text-[10px] mt-2 pt-2"
            style={{ color: '#CBD5E1', borderTop: '1px solid #F1F5F9' }}
          >
            ROS · Asset turnover · Leverage — EBIT-based · Full DuPont tree in
            detail section below
          </p>
        </div>
      )}
    </OverviewAnalysisBlock>
  )
}
