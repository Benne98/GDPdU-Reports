import { BRAND } from './salesChartTheme'

type Option<T extends string> = { value: T; label: string }

type Props<T extends string> = {
  options: Option<T>[]
  value: T
  onChange: (v: T) => void
  /** When true, no top margin — for use inline in the chart header row. */
  inline?: boolean
}

export default function SalesChartMetricToggle<T extends string>({ options, value, onChange, inline }: Props<T>) {
  return (
    <div className={`flex flex-wrap gap-1 ${inline ? '' : 'mt-2.5'}`} role="tablist">
      {options.map(opt => {
        const active = value === opt.value
        return (
          <button
            key={opt.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            className="px-2.5 py-1 rounded-full text-xs font-medium transition-colors"
            style={{
              background: active ? 'rgba(30, 58, 95, 0.1)' : BRAND.surface,
              color: active ? BRAND.navy : BRAND.textSecondary,
              border: `1px solid ${active ? 'rgba(30, 58, 95, 0.25)' : BRAND.border}`,
            }}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
