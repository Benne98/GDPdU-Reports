import SignalScoreBadge from './SignalScoreBadge'
import type { ScoreBand } from '../../lib/anomalyCharts'

interface Props {
  headline: string
  bullets: string[]
  score: number
  band: ScoreBand | string
  label?: string
  sublabel?: string
  children?: React.ReactNode
}

export default function AnomalyReport({ headline, bullets, score, band, label, sublabel, children }: Props) {
  return (
    <div className="rounded-xl bg-white overflow-hidden" style={{ border: '1px solid #E2E8F0' }}>
      <div className="px-5 pt-5 pb-4">
        {/* Label + score */}
        <div className="flex flex-wrap items-start justify-between gap-2 mb-2">
          <div className="flex-1 min-w-0">
            {label && (
              <p className="text-xs font-medium mb-0.5" style={{ color: '#64748B' }}>{label}</p>
            )}
            {sublabel && (
              <p className="text-[12px] mb-0.5" style={{ color: '#94A3B8' }}>{sublabel}</p>
            )}
          </div>
          <SignalScoreBadge score={score} band={band} />
        </div>
        {/* Headline */}
        <h3 className="text-base font-semibold leading-snug mb-3" style={{ color: '#1E3A5F' }}>
          {headline}
        </h3>
        {/* Bullets */}
        {bullets.length > 0 && (
          <ul className="space-y-1.5 pl-0 m-0" style={{ listStyle: 'none' }}>
            {bullets.map((b, i) => (
              <li key={i} className="flex gap-2 text-xs leading-relaxed" style={{ color: '#475569' }}>
                <span className="flex-shrink-0 mt-0.5" style={{ color: '#94A3B8' }}>·</span>
                <span>{b}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      {/* Viz slot */}
      {children && (
        <div className="px-5 pb-5">
          {children}
        </div>
      )}
    </div>
  )
}
