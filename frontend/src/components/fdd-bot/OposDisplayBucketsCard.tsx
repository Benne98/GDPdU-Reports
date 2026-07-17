/**
 * OPOS aging bucket editor — configure contiguous overdue day ranges.
 */

import { useMemo, useState } from 'react'

export interface AgingRange {
  lo: number
  hi: number
}

interface Props {
  title?: string
  subtitle?: string
  submitLabel?: string
  defaultRanges?: AgingRange[]
  multipleSnapshots?: boolean
  defaultSortBasis?: string
  disabled?: boolean
  onSubmit: (values: {
    opos_aging_ranges: [number, number][]
    opos_sort_basis?: string
  }) => void | Promise<void>
}

const DEFAULT_RANGES: AgingRange[] = [
  { lo: 1, hi: 30 },
  { lo: 31, hi: 60 },
  { lo: 61, hi: 90 },
  { lo: 91, hi: 180 },
]

function validateRanges(ranges: AgingRange[]): string | null {
  if (ranges.length === 0) {
    return 'Add at least one overdue bucket.'
  }
  const sorted = [...ranges].sort((a, b) => a.lo - b.lo)
  for (let i = 0; i < sorted.length; i += 1) {
    const { lo, hi } = sorted[i]
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
      return 'All bounds must be numbers.'
    }
    if (!Number.isInteger(lo) || !Number.isInteger(hi)) {
      return 'Bounds must be whole numbers.'
    }
    if (lo < 1 || hi < 1) {
      return 'Bounds must be at least 1.'
    }
    if (lo > hi) {
      return `Invalid range ${lo}–${hi}: start must be ≤ end.`
    }
  }
  if (sorted[0].lo !== 1) {
    return 'The first overdue bucket must start at day 1.'
  }
  for (let i = 1; i < sorted.length; i += 1) {
    const prev = sorted[i - 1]
    const cur = sorted[i]
    if (cur.lo <= prev.hi) {
      return `Ranges overlap: ${prev.lo}–${prev.hi} and ${cur.lo}–${cur.hi}.`
    }
    if (cur.lo !== prev.hi + 1) {
      return `Gap between buckets: ${prev.lo}–${prev.hi} and ${cur.lo}–${cur.hi}.`
    }
  }
  return null
}

export default function OposDisplayBucketsCard({
  title = 'Configure aging buckets',
  subtitle,
  submitLabel = 'Continue',
  defaultRanges = DEFAULT_RANGES,
  multipleSnapshots = false,
  defaultSortBasis = 'most_recent',
  disabled,
  onSubmit,
}: Props) {
  const [ranges, setRanges] = useState<AgingRange[]>(() =>
    defaultRanges.length > 0 ? defaultRanges.map(r => ({ ...r })) : [{ lo: 1, hi: 30 }],
  )
  const [sortBasis, setSortBasis] = useState(defaultSortBasis)
  const [submitting, setSubmitting] = useState(false)

  const maxHi = useMemo(
    () => (ranges.length > 0 ? Math.max(...ranges.map(r => r.hi)) : 180),
    [ranges],
  )

  const validationError = useMemo(() => validateRanges(ranges), [ranges])

  const updateRange = (index: number, field: 'lo' | 'hi', raw: string) => {
    const parsed = raw === '' ? 0 : Number.parseInt(raw, 10)
    setRanges(prev =>
      prev.map((r, i) => (i === index ? { ...r, [field]: Number.isNaN(parsed) ? 0 : parsed } : r)),
    )
  }

  const addPeriod = () => {
    setRanges(prev => {
      if (prev.length === 0) return [{ lo: 1, hi: 30 }]
      const lastHi = Math.max(...prev.map(r => r.hi))
      return [...prev, { lo: lastHi + 1, hi: lastHi + 30 }]
    })
  }

  const removePeriod = () => {
    setRanges(prev => (prev.length > 1 ? prev.slice(0, -1) : prev))
  }

  const handleSubmit = async () => {
    if (disabled || submitting || validationError) return
    setSubmitting(true)
    try {
      const sorted = [...ranges].sort((a, b) => a.lo - b.lo)
      await onSubmit({
        opos_aging_ranges: sorted.map(r => [r.lo, r.hi]),
        ...(multipleSnapshots ? { opos_sort_basis: sortBasis } : {}),
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="rounded-xl p-4 flex flex-col gap-3"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
    >
      <h3 className="text-sm font-semibold" style={{ color: '#1E293B' }}>
        {title}
      </h3>
      {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}

      {multipleSnapshots && (
        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium text-slate-700">Ranking period basis</span>
          <div className="flex flex-wrap gap-2">
            {[
              { value: 'most_recent', label: 'Most recent snapshot only' },
              { value: 'all_dates', label: 'All snapshots combined' },
            ].map(opt => (
              <button
                key={opt.value}
                type="button"
                disabled={disabled}
                onClick={() => setSortBasis(opt.value)}
                className="rounded-full px-3 py-1.5 text-xs font-medium border transition-colors disabled:opacity-50"
                style={{
                  background: sortBasis === opt.value ? '#1E40AF' : '#F8FAFC',
                  color: sortBasis === opt.value ? '#fff' : '#475569',
                  borderColor: sortBasis === opt.value ? '#1E40AF' : '#E2E8F0',
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-col gap-3">
        <span className="text-xs font-medium text-slate-700">Overdue day buckets</span>

        <div className="flex flex-wrap items-end gap-2">
          <div
            className="flex flex-col gap-1 rounded-lg px-3 py-2 min-w-[7rem]"
            style={{ background: '#F1F5F9', border: '1px solid #E2E8F0' }}
          >
            <span className="text-[10px] uppercase tracking-wide text-slate-500">Fixed</span>
            <span className="text-xs font-medium text-slate-700">Not yet due</span>
          </div>

          {ranges.map((range, index) => (
            <div
              key={index}
              className="flex items-center gap-1 rounded-lg px-2 py-2"
              style={{ background: '#F8FAFC', border: '1px solid #E2E8F0' }}
            >
              <input
                type="number"
                min={1}
                disabled={disabled}
                value={range.lo || ''}
                onChange={e => updateRange(index, 'lo', e.target.value)}
                className="w-14 rounded border px-2 py-1 text-xs text-center disabled:opacity-50"
                style={{ borderColor: '#CBD5E1' }}
                aria-label={`Bucket ${index + 1} start days`}
              />
              <span className="text-xs text-slate-400">–</span>
              <input
                type="number"
                min={1}
                disabled={disabled}
                value={range.hi || ''}
                onChange={e => updateRange(index, 'hi', e.target.value)}
                className="w-14 rounded border px-2 py-1 text-xs text-center disabled:opacity-50"
                style={{ borderColor: '#CBD5E1' }}
                aria-label={`Bucket ${index + 1} end days`}
              />
              <span className="text-xs text-slate-500 ml-1">days</span>
            </div>
          ))}

          <div
            className="flex flex-col gap-1 rounded-lg px-3 py-2 min-w-[5rem]"
            style={{ background: '#F1F5F9', border: '1px solid #E2E8F0' }}
          >
            <span className="text-[10px] uppercase tracking-wide text-slate-500">Fixed</span>
            <span className="text-xs font-medium text-slate-700">{maxHi}+ days</span>
          </div>
        </div>

        <div className="flex flex-col gap-2 w-fit">
          <button
            type="button"
            disabled={disabled}
            onClick={addPeriod}
            className="rounded-lg px-3 py-2 text-xs font-medium border transition-colors disabled:opacity-50"
            style={{ background: '#F8FAFC', borderColor: '#E2E8F0', color: '#475569' }}
          >
            Add period
          </button>
          <button
            type="button"
            disabled={disabled || ranges.length <= 1}
            onClick={removePeriod}
            className="rounded-lg px-3 py-2 text-xs font-medium border transition-colors disabled:opacity-40"
            style={{ background: '#F8FAFC', borderColor: '#E2E8F0', color: '#475569' }}
          >
            Remove period
          </button>
        </div>

        {validationError && <p className="text-xs text-red-600">{validationError}</p>}
        <p className="text-xs text-slate-500">
          All buckets are included in the output. Ranges must cover every overdue day without gaps
          or overlaps.
        </p>
      </div>

      <button
        type="button"
        disabled={disabled || submitting || Boolean(validationError)}
        onClick={() => void handleSubmit()}
        className="self-start rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        style={{ background: '#1E40AF' }}
      >
        {submitting ? 'Continuing…' : submitLabel}
      </button>
    </div>
  )
}
