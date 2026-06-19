import { useEffect } from 'react'
import { Maximize2, X } from 'lucide-react'
import type { ReactNode } from 'react'

export function ChartFocusButton({ onClick, disabled }: { onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      title="Focus mode"
      aria-label="Focus mode"
      disabled={disabled}
      onClick={onClick}
      className="flex items-center justify-center p-1.5 rounded-lg transition-colors disabled:opacity-50"
      style={{ background: '#F4F6F9', color: '#475569', border: '1px solid #E2E8F0' }}
    >
      <Maximize2 size={14} strokeWidth={1.75} />
    </button>
  )
}

export default function ChartFocusOverlay({
  open,
  title,
  subtitle,
  onClose,
  children,
}: {
  open: boolean
  title: string
  subtitle?: string
  onClose: () => void
  children: ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4"
      style={{ background: 'rgba(15, 23, 42, 0.45)' }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-5xl max-h-[90vh] flex flex-col rounded-xl overflow-hidden"
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 24px 48px rgba(0,0,0,0.18)' }}
        onClick={e => e.stopPropagation()}
      >
        <div
          className="px-5 py-4 flex items-start justify-between gap-3 shrink-0"
          style={{ borderBottom: '1px solid #F1F5F9' }}
        >
          <div>
            <h3 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>{title}</h3>
            {subtitle && (
              <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>{subtitle}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-slate-100"
            aria-label="Close focus mode"
          >
            <X size={18} style={{ color: '#64748B' }} />
          </button>
        </div>
        <div className="p-5 overflow-auto flex-1 min-h-0">{children}</div>
      </div>
    </div>
  )
}
