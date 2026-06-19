import type { AgingPortfolioMetricChip } from './agingPortfolioNarrative'

const TONE_STYLES = {
  brand: { bg: '#F0F4FF', border: '#C7D7FE', value: '#1E3A5F' },
  warning: { bg: '#FFF5F5', border: '#FECACA', value: '#BE123C' },
  neutral: { bg: '#F8FAFC', border: '#E2E8F0', value: '#1E3A5F' },
} as const

export default function AgingPortfolioMetricCards({ metrics }: { metrics: AgingPortfolioMetricChip[] }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5 pb-3 border-b" style={{ borderColor: '#F1F5F9' }}>
      {metrics.map(m => {
        const s = TONE_STYLES[m.tone]
        return (
          <div
            key={m.label}
            className="rounded-lg px-3 py-2.5 min-w-0"
            style={{ background: s.bg, border: `1px solid ${s.border}` }}
          >
            <p className="text-[10px] font-semibold uppercase tracking-wide truncate" style={{ color: '#94A3B8' }}>
              {m.label}
            </p>
            <p className="text-lg font-bold tabular-nums leading-tight mt-0.5" style={{ color: s.value }}>
              {m.value}
            </p>
            {m.hint && (
              <p className="text-[11px] tabular-nums mt-0.5 truncate" style={{ color: '#64748B' }}>
                {m.hint}
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}
