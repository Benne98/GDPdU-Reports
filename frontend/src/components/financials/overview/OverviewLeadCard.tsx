import { fmtDelta, fmtKpi } from '../../../lib/fmt'
import { capitalizeSentenceStarts } from '../../../lib/capitalizeSentenceStarts'
import type { LeadBriefing } from './overviewBriefingUtils'

const CARD = {
  background: '#FFFFFF',
  border: '1px solid #E2E8F0',
  boxShadow: '0 1px 3px rgba(0,0,0,0.05), 0 8px 24px rgba(30,58,95,0.06)',
} as const

type Props = {
  briefing: LeadBriefing
}

export default function OverviewLeadCard({ briefing }: Props) {
  const delta = briefing.heroDelta
  const deltaColor =
    delta == null ? '#94A3B8' : delta >= 0 ? '#059669' : '#DC2626'

  return (
    <div className="rounded-xl overflow-hidden" style={CARD}>
      <div className="px-6 pt-6 pb-5 flex flex-col lg:flex-row lg:items-start gap-6">
        <div className="min-w-0 flex-1">
          <div
            className="text-[12px] font-semibold uppercase tracking-[0.14em] mb-2"
            style={{ color: '#1E3A5F' }}
          >
            Lead story
          </div>
          <h2 className="text-xl font-bold tracking-tight leading-snug mb-3" style={{ color: '#0F172A' }}>
            {briefing.headline}
          </h2>
          <p className="text-sm leading-relaxed m-0" style={{ color: '#475569' }}>
            {capitalizeSentenceStarts(briefing.intro)}
          </p>
        </div>

        <div
          className="shrink-0 rounded-xl px-5 py-4 min-w-[200px]"
          style={{ background: 'linear-gradient(135deg, #F8FAFC 0%, #EFF6FF 100%)', border: '1px solid #E2E8F0' }}
        >
          <div className="text-[12px] font-semibold uppercase tracking-wide mb-1" style={{ color: '#64748B' }}>
            {briefing.heroTitle}
          </div>
          <div className="text-3xl font-bold tabular-nums tracking-tight" style={{ color: '#0F172A' }}>
            {fmtKpi(briefing.heroValue)}
          </div>
          {delta != null && (
            <div className="mt-2 text-xs">
              <span style={{ color: '#94A3B8' }}>{briefing.heroDeltaLabel}</span>
              <span className="ml-2 font-semibold tabular-nums" style={{ color: deltaColor }}>
                {fmtDelta(delta)}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
