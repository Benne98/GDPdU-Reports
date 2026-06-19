import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useEffect } from 'react'

export type EntityOption = { code: string; label: string }

type Props = {
  entities: EntityOption[]
  index: number
  onChange: (index: number) => void
  disabled?: boolean
  /** Inline header variant — smaller, lighter styling */
  compact?: boolean
}

export default function PlEntityCarousel({ entities, index, onChange, disabled, compact }: Props) {
  const n = entities.length
  const current = entities[index]

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (disabled || n < 2) return
      if (e.key === 'ArrowLeft') onChange((index - 1 + n) % n)
      if (e.key === 'ArrowRight') onChange((index + 1) % n)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [index, n, onChange, disabled])

  if (!n || !current) return null

  const pillStyle = {
    background: 'linear-gradient(135deg, rgba(255,255,255,0.95) 0%, rgba(248,250,252,0.98) 100%)',
    border: '1px solid rgba(30,58,95,0.12)',
    boxShadow: '0 4px 20px rgba(15,23,42,0.08), inset 0 1px 0 rgba(255,255,255,0.8)',
  } as const

  if (compact) {
    return (
      <div
        className="shrink-0 min-w-0 max-w-[min(100%,220px)] sm:max-w-[260px]"
        role="group"
        aria-label="Select legal entity"
      >
        <div
          className="flex items-center gap-0.5 rounded-full pl-0.5 pr-0.5 py-0.5"
          style={pillStyle}
        >
          <button
            type="button"
            disabled={disabled || n <= 1}
            onClick={() => onChange((index - 1 + n) % n)}
            className="flex items-center justify-center w-7 h-7 rounded-full transition-colors hover:bg-slate-100/90 disabled:opacity-30 shrink-0"
            aria-label="Previous entity"
          >
            <ChevronLeft size={15} style={{ color: '#1E3A5F' }} strokeWidth={2} />
          </button>
          <div className="px-2 py-0.5 min-w-[88px] max-w-[160px] text-center" title={current.label}>
            <p className="text-xs font-semibold text-slate-900 truncate leading-tight">{current.label}</p>
            {n > 1 && (
              <p className="text-[0.6rem] text-slate-500 tabular-nums leading-tight">
                {index + 1} / {n}
              </p>
            )}
          </div>
          <button
            type="button"
            disabled={disabled || n <= 1}
            onClick={() => onChange((index + 1) % n)}
            className="flex items-center justify-center w-7 h-7 rounded-full transition-colors hover:bg-slate-100/90 disabled:opacity-30 shrink-0"
            aria-label="Next entity"
          >
            <ChevronRight size={15} style={{ color: '#1E3A5F' }} strokeWidth={2} />
          </button>
        </div>
      </div>
    )
  }

  return (
    <div
      className="flex flex-col items-center gap-1.5 min-w-0 max-w-[min(100%,320px)]"
      role="group"
      aria-label="Select legal entity"
    >
      <div className="flex items-center gap-1 rounded-full pl-1 pr-1 py-1" style={pillStyle}>
        <button
          type="button"
          disabled={disabled || n <= 1}
          onClick={() => onChange((index - 1 + n) % n)}
          className="flex items-center justify-center w-8 h-8 rounded-full transition-colors hover:bg-slate-100 disabled:opacity-30"
          aria-label="Previous entity"
        >
          <ChevronLeft size={18} style={{ color: '#1E3A5F' }} />
        </button>
        <div className="px-3 py-1 min-w-[140px] text-center">
          <p className="text-sm font-semibold text-slate-900 truncate" title={current.label}>
            {current.label}
          </p>
          <p className="text-[0.65rem] text-slate-500 tabular-nums">
            {index + 1} / {n}
          </p>
        </div>
        <button
          type="button"
          disabled={disabled || n <= 1}
          onClick={() => onChange((index + 1) % n)}
          className="flex items-center justify-center w-8 h-8 rounded-full transition-colors hover:bg-slate-100 disabled:opacity-30"
          aria-label="Next entity"
        >
          <ChevronRight size={18} style={{ color: '#1E3A5F' }} />
        </button>
      </div>
      {n > 1 && (
        <div className="flex items-center gap-1" role="tablist" aria-label="Entity indicators">
          {entities.map((e, i) => (
            <button
              key={e.code}
              type="button"
              role="tab"
              aria-selected={i === index}
              aria-label={e.label}
              onClick={() => onChange(i)}
              disabled={disabled}
              className="rounded-full transition-all"
              style={{
                width: i === index ? 18 : 6,
                height: 6,
                background: i === index ? '#1E3A5F' : 'rgba(30,58,95,0.2)',
              }}
            />
          ))}
        </div>
      )}
    </div>
  )
}
