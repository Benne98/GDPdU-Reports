type StichtagOption = {
  label: string
  year: number
  month: number
}

const STICHTAG_OPTIONS: StichtagOption[] = [
  { label: 'Dec 2022', year: 2022, month: 12 },
  { label: 'Dec 2023', year: 2023, month: 12 },
  { label: 'Dec 2024', year: 2024, month: 12 },
  { label: 'Jul 2025 (YTD)', year: 2025, month: 7 },
]

/** Default as-of period — latest available snapshot (Jul 2025 YTD). */
export const STICHTAG_DEFAULT = { year: 2025, month: 7 }

type Props = {
  year: number
  month: number
  onChange: (year: number, month: number) => void
}

/**
 * Compact button-group selector for the AR/AP as-of date (Stichtag).
 * Drives re-query of all aging charts via SalesAgingTab state.
 * Styled to match the period-pill pattern (ModulePeriodFilterBar).
 */
export default function StichtagSwitch({ year, month, onChange }: Props) {
  return (
    <div className="flex items-center gap-2.5 flex-wrap">
      <span className="text-xs font-semibold shrink-0" style={{ color: '#64748B' }}>
        As-of date
      </span>
      <div
        className="flex gap-1 flex-wrap"
        role="group"
        aria-label="Select as-of date"
      >
        {STICHTAG_OPTIONS.map(opt => {
          const active = opt.year === year && opt.month === month
          return (
            <button
              key={opt.label}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(opt.year, opt.month)}
              className="px-2.5 py-1.5 rounded-md text-xs font-medium transition-all whitespace-nowrap"
              style={{
                background: active ? 'rgba(30,58,95,0.1)' : '#F4F6F9',
                color: active ? '#1E3A5F' : '#475569',
                border: `1px solid ${active ? 'rgba(30,58,95,0.25)' : '#E2E8F0'}`,
              }}
            >
              {opt.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
