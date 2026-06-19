import type { AgingView } from './agingView'

type Props = {
  view: AgingView
  onChange: (view: AgingView) => void
}

const OPTIONS: { id: AgingView; label: string; description: string }[] = [
  { id: 'receivables', label: 'Receivables aging', description: 'Customer balances & DSO' },
  { id: 'payables', label: 'Payables aging', description: 'Supplier balances & DPO' },
]

export default function SalesAgingSubNav({ view, onChange }: Props) {
  return (
    <div
      className="rounded-xl px-2 py-2 flex flex-col sm:flex-row sm:items-center gap-2"
      style={{
        background: '#FFFFFF',
        border: '1px solid #E2E8F0',
        boxShadow: '0 1px 3px rgba(15, 23, 42, 0.04)',
      }}
      role="tablist"
      aria-label="Aging views"
    >
      {OPTIONS.map(opt => {
        const active = view === opt.id
        return (
          <button
            key={opt.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.id)}
            className="flex-1 rounded-lg px-4 py-2.5 text-left transition-all duration-150"
            style={{
              background: active ? 'rgba(30,58,95,0.1)' : 'transparent',
              border: `1px solid ${active ? 'rgba(30,58,95,0.25)' : 'transparent'}`,
            }}
          >
            <span className="block text-sm font-semibold" style={{ color: active ? '#1E3A5F' : '#334155' }}>
              {opt.label}
            </span>
            <span className="block text-[11px] mt-0.5" style={{ color: active ? '#64748B' : '#94A3B8' }}>
              {opt.description}
            </span>
          </button>
        )
      })}
    </div>
  )
}
