import type { AgingPortfolioNarrative } from './agingPortfolioNarrative'

const INSIGHT_STYLES = {
  info: { dot: '#6366F1', bg: '#F5F7FF' },
  watch: { dot: '#F59E0B', bg: '#FFFBEB' },
  positive: { dot: '#10B981', bg: '#F0FDF4' },
} as const

export default function AgingPortfolioNarrativePanel({ narrative }: { narrative: AgingPortfolioNarrative }) {
  return (
    <div className="flex flex-col gap-5">
      <div>
        <p
          className="text-[11px] font-semibold uppercase tracking-wider mb-2"
          style={{ color: '#94A3B8' }}
        >
          {narrative.eyebrow}
        </p>
        <h4 className="text-lg font-semibold leading-snug mb-3" style={{ color: '#1E3A5F' }}>
          {narrative.headline}
        </h4>
        <p className="text-sm leading-[1.65]" style={{ color: '#64748B' }}>
          {narrative.summary}
        </p>
      </div>

      <div className="space-y-3">
        {narrative.insights.map(insight => {
          const s = INSIGHT_STYLES[insight.tone]
          return (
            <div
              key={insight.title}
              className="rounded-xl pl-4 pr-4 py-3.5"
              style={{ borderLeft: `3px solid ${s.dot}`, background: s.bg }}
            >
              <p className="text-[13px] font-semibold mb-1.5" style={{ color: '#1E3A5F' }}>
                {insight.title}
              </p>
              <p className="text-[13px] leading-[1.65]" style={{ color: '#64748B' }}>
                {insight.body}
              </p>
            </div>
          )
        })}
      </div>
    </div>
  )
}
