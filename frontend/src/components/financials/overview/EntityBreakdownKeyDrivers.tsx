import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { EntityBreakdownArea } from '../../../lib/api'
import type { FinTab } from '../financialsTabs'
import PlCommentIndexBadge from '../pl-two-view/PlCommentIndexBadge'
import PlSectionHeading from '../pl-two-view/PlSectionHeading'
import { KEY_DRIVERS_HEADING } from '../pl-two-view/plReportSectionHeadings'
import { stripLegalForm } from '../../../lib/stripLegalForm'
import { capitalizeSentenceStarts } from '../../../lib/capitalizeSentenceStarts'

type Props = {
  area: EntityBreakdownArea
  areaIndex: number
  areaCount: number
  onPrev: () => void
  onNext: () => void
  onNavigateTab: (tab: FinTab) => void
}

export default function EntityBreakdownKeyDrivers({
  area,
  areaIndex,
  areaCount,
  onPrev,
  onNext,
  onNavigateTab,
}: Props) {
  return (
    <div>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="min-w-0">
          <PlSectionHeading>{KEY_DRIVERS_HEADING}</PlSectionHeading>
          <p className="text-[10px] font-medium m-0 -mt-1" style={{ color: '#64748B' }}>
            {area.title}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0 pt-0.5">
          <button
            type="button"
            onClick={onPrev}
            className="p-1 rounded-md transition-colors hover:bg-slate-100"
            style={{ color: '#475569', border: '1px solid #E2E8F0' }}
            aria-label="Previous area"
          >
            <ChevronLeft size={14} />
          </button>
          <span className="text-[10px] tabular-nums px-0.5" style={{ color: '#94A3B8' }}>
            {areaIndex + 1} / {areaCount}
          </span>
          <button
            type="button"
            onClick={onNext}
            className="p-1 rounded-md transition-colors hover:bg-slate-100"
            style={{ color: '#475569', border: '1px solid #E2E8F0' }}
            aria-label="Next area"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>

      <div className="text-xs leading-relaxed" style={{ color: '#475569' }}>
        {area.intro ? (
          <p className="mb-3 m-0" style={{ textAlign: 'justify' }}>
            {capitalizeSentenceStarts(area.intro)}
          </p>
        ) : null}
        <ol className="list-none m-0 p-0 space-y-4">
          {area.bullets.map(b => (
            <li key={b.entity_code} className="flex gap-2.5">
              <span className="flex h-[1.625rem] shrink-0 items-start pt-0.5">
                <PlCommentIndexBadge index={b.index} />
              </span>
              <span className="min-w-0 pt-0.5" style={{ textAlign: 'justify' }}>
                <span className="font-semibold" style={{ color: '#1E3A5F' }}>
                  {stripLegalForm(b.entity_name)}
                </span>
                {': '}
                {capitalizeSentenceStarts(b.text)}
              </span>
            </li>
          ))}
        </ol>
      </div>

      <button
        type="button"
        onClick={() => onNavigateTab(area.tab as FinTab)}
        className="mt-4 w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold transition-colors hover:opacity-90"
        style={{
          background: 'rgba(30,58,95,0.06)',
          color: '#1E3A5F',
          border: '1px solid rgba(30,58,95,0.15)',
        }}
      >
        Open {area.title}
        <ChevronRight size={14} />
      </button>
    </div>
  )
}
