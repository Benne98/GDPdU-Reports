import { useEffect, useRef, useState } from 'react'
import { Pencil } from 'lucide-react'
import {
  type AgingPortfolioDimensionOption,
  type AgingPortfolioSide,
  portfolioDimensionsFor,
} from './agingPortfolioDimensions'

type Props = {
  side: AgingPortfolioSide
  dimension: string
  onChange: (dimension: string) => void
  disabled?: boolean
}

export default function AgingPortfolioDimensionEditor({
  side,
  dimension,
  onChange,
  disabled,
}: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const options = portfolioDimensionsFor(side)

  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  function pick(opt: AgingPortfolioDimensionOption) {
    onChange(opt.id)
    setOpen(false)
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        disabled={disabled}
        title="Analysis dimension"
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-50"
        style={{ background: '#F4F6F9', color: '#475569', border: '1px solid #E2E8F0' }}
        onClick={() => setOpen(v => !v)}
      >
        <Pencil size={13} />
        Dimension
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-30 rounded-xl shadow-lg py-1 min-w-[200px]"
          style={{ background: '#FFF', border: '1px solid #E2E8F0' }}
        >
          <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide" style={{ color: '#94A3B8' }}>
            Break down by
          </p>
          {options.map(opt => (
            <button
              key={opt.id}
              type="button"
              className="w-full text-left px-3 py-2 text-xs hover:bg-slate-50 flex items-center justify-between gap-2"
              style={{
                color: opt.id === dimension ? '#1E3A5F' : '#334155',
                fontWeight: opt.id === dimension ? 600 : 400,
              }}
              onClick={() => pick(opt)}
            >
              <span>{opt.label}</span>
              {opt.salesMapped && (
                <span className="text-[9px] px-1 rounded" style={{ background: '#EFF6FF', color: '#3B82F6' }}>
                  Sales
                </span>
              )}
            </button>
          ))}
          <p className="px-3 py-2 text-[10px] leading-snug border-t" style={{ color: '#94A3B8', borderColor: '#F1F5F9' }}>
            Entity &amp; segment use sales-to-GL customer mapping where available.
          </p>
        </div>
      )}
    </div>
  )
}
