import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { OverviewHighlight } from '../../../lib/api'
import type { FinTab } from '../financialsTabs'
import PlCommentIndexBadge from '../pl-two-view/PlCommentIndexBadge'
import PlSectionHeading from '../pl-two-view/PlSectionHeading'
import { KEY_DRIVERS_HEADING } from '../pl-two-view/plReportSectionHeadings'
import { capitalizeSentenceStarts } from '../../../lib/capitalizeSentenceStarts'

type Props = {
  highlights: OverviewHighlight[]
  loading?: boolean
  activeIndex: number
  onActiveIndexChange: (i: number) => void
  onNavigateTab: (tab: FinTab) => void
}

export default function OverviewHighlightsPanel({
  highlights,
  loading,
  activeIndex,
  onActiveIndexChange,
  onNavigateTab,
}: Props) {
  if (loading && !highlights.length) {
    return (
      <div className="text-xs animate-pulse" style={{ color: '#94A3B8' }}>
        Loading key drivers…
      </div>
    )
  }

  if (!highlights.length) {
    return (
      <div>
        <PlSectionHeading>{KEY_DRIVERS_HEADING}</PlSectionHeading>
        <p className="text-xs leading-relaxed m-0" style={{ color: '#64748B' }}>
          Key drivers from statement report views will appear here once narratives are available.
        </p>
      </div>
    )
  }

  const idx = Math.min(activeIndex, highlights.length - 1)
  const h = highlights[idx]

  const prev = () => onActiveIndexChange(idx <= 0 ? highlights.length - 1 : idx - 1)
  const next = () => onActiveIndexChange(idx >= highlights.length - 1 ? 0 : idx + 1)

  return (
    <div>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="min-w-0">
          <PlSectionHeading>{KEY_DRIVERS_HEADING}</PlSectionHeading>
          <p className="text-[10px] font-medium m-0 -mt-1" style={{ color: '#64748B' }}>
            {h.tab_label}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0 pt-0.5">
          <button
            type="button"
            onClick={prev}
            className="p-1 rounded-md transition-colors hover:bg-slate-100"
            style={{ color: '#475569', border: '1px solid #E2E8F0' }}
            aria-label="Previous highlight"
          >
            <ChevronLeft size={14} />
          </button>
          <span className="text-[10px] tabular-nums px-0.5" style={{ color: '#94A3B8' }}>
            {idx + 1} / {highlights.length}
          </span>
          <button
            type="button"
            onClick={next}
            className="p-1 rounded-md transition-colors hover:bg-slate-100"
            style={{ color: '#475569', border: '1px solid #E2E8F0' }}
            aria-label="Next highlight"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>

      <div className="text-xs leading-relaxed" style={{ color: '#475569' }}>
        {h.intro && (
          <p className="mb-3 m-0" style={{ textAlign: 'justify' }}>
            {capitalizeSentenceStarts(h.intro)}
          </p>
        )}
        <ol className="list-none m-0 p-0 space-y-4">
          {h.bullets.map(b => (
            <li key={b.index} className="flex gap-2.5">
              <span className="flex h-[1.625rem] shrink-0 items-start pt-0.5">
                <PlCommentIndexBadge index={b.index} />
              </span>
              <span className="min-w-0 pt-0.5" style={{ textAlign: 'justify' }}>
                {capitalizeSentenceStarts(b.text)}
              </span>
            </li>
          ))}
        </ol>
      </div>

      <button
        type="button"
        onClick={() => onNavigateTab(h.tab as FinTab)}
        className="mt-4 w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold transition-colors hover:opacity-90"
        style={{
          background: 'rgba(30,58,95,0.06)',
          color: '#1E3A5F',
          border: '1px solid rgba(30,58,95,0.15)',
        }}
      >
        Open {h.tab_label}
        <ChevronRight size={14} />
      </button>
    </div>
  )
}
