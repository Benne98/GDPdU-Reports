import type { ReceivablesConcentration } from '../../../lib/api'
import { fmtAmount } from '../../../lib/fmt'
import {
  concentrationBandColor,
  sortConcentrationSegments,
} from './shared/agingConcentrationBands'

export default function ReceivablesConcentrationCard({ data }: { data: ReceivablesConcentration }) {
  const segments = sortConcentrationSegments(data.segments ?? [])
  const hasSegmentData = segments.some(s => s.amount > 0)

  if (data.total <= 0 || !hasSegmentData) {
    return (
      <p className="text-sm py-8 text-center" style={{ color: '#94A3B8' }}>
        No concentration data
      </p>
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-[12px]" style={{ color: '#64748B' }}>
        Non-overlapping share of open receivables ({fmtAmount(data.total)}) by customer rank
      </p>
      {segments.map(seg => (
        <div key={seg.band}>
          <div className="flex justify-between text-[12px] mb-1 gap-2">
            <span className="font-medium" style={{ color: '#334155' }}>
              {seg.label}
              {seg.customer_count > 0 && (
                <span className="font-normal ml-1" style={{ color: '#94A3B8' }}>
                  · {seg.customer_count} {seg.customer_count === 1 ? 'customer' : 'customers'}
                </span>
              )}
            </span>
            <span className="tabular-nums font-semibold shrink-0" style={{ color: '#1E3A5F' }}>
              {seg.pct}%
              <span className="font-normal ml-2" style={{ color: '#94A3B8' }}>
                {fmtAmount(seg.amount)}
              </span>
            </span>
          </div>
          <div className="h-2 rounded-full overflow-hidden" style={{ background: '#EFF6FF' }}>
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${Math.min(seg.pct, 100)}%`,
                background: concentrationBandColor(seg.band),
              }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}
