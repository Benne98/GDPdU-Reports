import type { AgingPortfolioNarrative } from './agingPortfolioNarrative'

// Restrained, single-accent palette — white cards, thin semantic left border, small chip.
const INSIGHT_STYLES = {
  info: {
    leftBorder: '#94A3B8',
    chipBg: '#F1F5F9',
    chipText: '#475569',
    chipLabel: 'Info',
  },
  watch: {
    leftBorder: '#D97706',
    chipBg: '#FEF3C7',
    chipText: '#92400E',
    chipLabel: 'Watch',
  },
  positive: {
    leftBorder: '#10B981',
    chipBg: '#D1FAE5',
    chipText: '#065F46',
    chipLabel: 'Good',
  },
} as const

export default function AgingPortfolioNarrativePanel({ narrative }: { narrative: AgingPortfolioNarrative }) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <p
          className="text-[11px] font-semibold uppercase tracking-wider mb-1.5"
          style={{ color: '#94A3B8' }}
        >
          {narrative.eyebrow}
        </p>
        <h4 className="text-base font-semibold leading-snug mb-2" style={{ color: '#1E3A5F' }}>
          {narrative.headline}
        </h4>
        <p className="text-[13px] leading-[1.65]" style={{ color: '#64748B' }}>
          {narrative.summary}
        </p>
      </div>

      <div className="space-y-2.5">
        {narrative.insights.map(insight => {
          const s = INSIGHT_STYLES[insight.tone]
          return (
            <div
              key={insight.title}
              className="rounded-lg px-4 py-3 border"
              style={{
                background: '#FFFFFF',
                borderColor: '#E2E8F0',
                borderLeft: `3px solid ${s.leftBorder}`,
              }}
            >
              <div className="flex items-center gap-2 mb-1.5">
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded font-semibold shrink-0"
                  style={{ background: s.chipBg, color: s.chipText }}
                >
                  {s.chipLabel}
                </span>
                <p className="text-[13px] font-semibold leading-snug" style={{ color: '#1E3A5F' }}>
                  {insight.title}
                </p>
              </div>
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
