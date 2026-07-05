/**
 * OverviewAnalysisBlock — generic block shell used by all 8 analysis blocks.
 * Provides: card chrome, section label, optional deep-link button, body slot.
 * Export ComingSoonPlaceholder for blocks not yet built (P2/P4/P5).
 */
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Clock } from 'lucide-react'
import type { OverviewV2Route } from './findingsModel'

// ─── Coming-soon placeholder ──────────────────────────────────────────────────

interface ComingSoonProps {
  /** Label shown in the placeholder, e.g. "Performance YoY / Plan". */
  label: string
  /** Delivery phase, e.g. "P2", "P4". */
  phase?: string
}

export function ComingSoonPlaceholder({ label, phase = 'P2' }: ComingSoonProps) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-2 rounded-lg py-10 text-center"
      style={{ background: '#F8FAFC', border: '1px dashed #CBD5E1' }}
    >
      <Clock size={18} aria-hidden style={{ color: '#94A3B8' }} />
      <p className="text-xs font-semibold" style={{ color: '#64748B' }}>
        {label}
      </p>
      <p className="text-[10px]" style={{ color: '#94A3B8' }}>
        Coming in {phase}
      </p>
    </div>
  )
}

// ─── Block shell ──────────────────────────────────────────────────────────────

interface Props {
  title: string
  /** Route to navigate to when "View detail" is clicked. */
  deepLink?: OverviewV2Route
  children?: React.ReactNode
  /** Tighten the inner padding for denser layouts. */
  compact?: boolean
}

export default function OverviewAnalysisBlock({
  title,
  deepLink,
  children,
  compact = false,
}: Props) {
  const navigate = useNavigate()

  return (
    <div
      className="rounded-xl overflow-hidden flex flex-col"
      style={{
        background: '#FFFFFF',
        border: '1px solid #E2E8F0',
        boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
        minHeight: '200px',
      }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-5 py-3 border-b shrink-0"
        style={{ borderColor: '#F1F5F9', background: '#FAFBFC' }}
      >
        <h3
          className="text-xs font-semibold uppercase tracking-wide"
          style={{ color: '#64748B' }}
        >
          {title}
        </h3>
        {deepLink && (
          <button
            className="flex items-center gap-1 text-xs font-medium transition-opacity hover:opacity-70"
            style={{ color: '#1E3A5F' }}
            onClick={() => navigate(deepLink)}
            aria-label={`View ${title} detail`}
          >
            View detail
            <ArrowRight size={11} aria-hidden />
          </button>
        )}
      </div>

      {/* Body */}
      <div className={`flex-1 ${compact ? 'px-4 py-3' : 'px-5 py-4'}`}>
        {children}
      </div>
    </div>
  )
}
