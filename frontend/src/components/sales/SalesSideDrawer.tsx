import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

type Props = {
  open: boolean
  onClose: () => void
  children: ReactNode
  /** Tailwind max-width class, e.g. max-w-md */
  panelClassName?: string
}

/**
 * Right-hand drawer portaled to document.body so it aligns to the viewport top
 * (not clipped/offset by ancestors with contain:layout or transform).
 */
export default function SalesSideDrawer({
  open,
  onClose,
  children,
  panelClassName = 'max-w-md',
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open || !scrollRef.current) return
    scrollRef.current.querySelectorAll('[data-drawer-scroll]').forEach(el => {
      (el as HTMLElement).scrollTop = 0
    })
  }, [open])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-[120] flex items-start justify-end" role="presentation">
      <div className="absolute inset-0 bg-slate-900/20" onClick={onClose} aria-hidden />
      <div
        ref={scrollRef}
        className={`relative flex h-full w-full flex-col overflow-hidden shadow-2xl ${panelClassName}`}
        style={{ background: '#fff', borderLeft: '1px solid #E2E8F0' }}
        role="dialog"
        aria-modal="true"
      >
        {children}
      </div>
    </div>,
    document.body,
  )
}
