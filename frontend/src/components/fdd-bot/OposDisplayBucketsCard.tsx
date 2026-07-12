/**
 * OPOS display-bucket selection — choose which aging buckets appear in the report.
 */

import { useMemo, useState } from 'react'

export interface BucketOption {
  value: string
  label: string
}

interface Props {
  title?: string
  subtitle?: string
  submitLabel?: string
  bucketOptions: BucketOption[]
  defaultKeys: string[]
  multipleSnapshots?: boolean
  defaultSortBasis?: string
  disabled?: boolean
  onSubmit: (values: {
    opos_display_bucket_keys: string[]
    opos_sort_basis?: string
  }) => void | Promise<void>
}

const ALL_KEY = '__all__'

export default function OposDisplayBucketsCard({
  title = 'Choose buckets to display',
  subtitle,
  submitLabel = 'Continue',
  bucketOptions,
  defaultKeys,
  multipleSnapshots = false,
  defaultSortBasis = 'most_recent',
  disabled,
  onSubmit,
}: Props) {
  const orderedValues = useMemo(() => bucketOptions.map(o => o.value), [bucketOptions])
  const [selected, setSelected] = useState<Set<string>>(() => new Set(defaultKeys))
  const [sortBasis, setSortBasis] = useState(defaultSortBasis)
  const [submitting, setSubmitting] = useState(false)

  const allSelected = orderedValues.length > 0 && orderedValues.every(v => selected.has(v))

  const toggleAll = () => {
    if (disabled) return
    if (allSelected) {
      setSelected(new Set())
    } else {
      setSelected(new Set(orderedValues))
    }
  }

  const toggleBucket = (key: string) => {
    if (disabled || key === ALL_KEY) return
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const handleSubmit = async () => {
    if (disabled || submitting || selected.size === 0) return
    setSubmitting(true)
    try {
      const keys = orderedValues.filter(v => selected.has(v))
      await onSubmit({
        opos_display_bucket_keys: keys,
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

      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium text-slate-700">Buckets to include in the report</span>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={disabled}
            onClick={toggleAll}
            className="rounded-full px-3 py-1.5 text-xs font-medium border transition-colors disabled:opacity-50"
            style={{
              background: allSelected ? '#1E40AF' : '#F8FAFC',
              color: allSelected ? '#fff' : '#475569',
              borderColor: allSelected ? '#1E40AF' : '#E2E8F0',
            }}
          >
            All
          </button>
          {bucketOptions.map(opt => {
            const on = selected.has(opt.value)
            return (
              <button
                key={opt.value}
                type="button"
                disabled={disabled}
                onClick={() => toggleBucket(opt.value)}
                className="rounded-full px-3 py-1.5 text-xs font-medium border transition-colors disabled:opacity-50"
                style={{
                  background: on ? '#2563EB' : '#F8FAFC',
                  color: on ? '#fff' : '#475569',
                  borderColor: on ? '#2563EB' : '#E2E8F0',
                }}
              >
                {opt.label}
              </button>
            )
          })}
        </div>
        {selected.size === 0 && (
          <p className="text-xs text-red-600">Select at least one bucket to continue.</p>
        )}
      </div>

      <button
        type="button"
        disabled={disabled || submitting || selected.size === 0}
        onClick={() => void handleSubmit()}
        className="self-start rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        style={{ background: '#1E40AF' }}
      >
        {submitting ? 'Continuing…' : submitLabel}
      </button>
    </div>
  )
}
