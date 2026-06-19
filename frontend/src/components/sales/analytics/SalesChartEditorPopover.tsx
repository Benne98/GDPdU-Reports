import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react'
import { createPortal } from 'react-dom'

const POPOVER_Z_BACKDROP = 200
const POPOVER_Z_PANEL = 201
const VIEWPORT_PAD = 8
const GAP = 4

type Props = {
  open: boolean
  onClose: () => void
  anchorRef: RefObject<HTMLElement | null>
  width?: number
  children: ReactNode
}

export function useChartEditorAnchor() {
  return useRef<HTMLButtonElement>(null)
}

export default function SalesChartEditorPopover({
  open,
  onClose,
  anchorRef,
  width = 288,
  children,
}: Props) {
  const panelRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)

  const updatePosition = useCallback(() => {
    const anchor = anchorRef.current
    if (!anchor) return
    const rect = anchor.getBoundingClientRect()
    const panelH = panelRef.current?.offsetHeight ?? 380
    const panelW = panelRef.current?.offsetWidth ?? width

    let top = rect.bottom + GAP
    if (top + panelH > window.innerHeight - VIEWPORT_PAD) {
      top = rect.top - GAP - panelH
    }
    top = Math.max(VIEWPORT_PAD, Math.min(top, window.innerHeight - panelH - VIEWPORT_PAD))

    let left = rect.right - panelW
    left = Math.max(VIEWPORT_PAD, Math.min(left, window.innerWidth - panelW - VIEWPORT_PAD))

    setPos({ top, left })
  }, [anchorRef, width])

  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    updatePosition()
    const raf1 = requestAnimationFrame(() => {
      updatePosition()
    })
    return () => cancelAnimationFrame(raf1)
  }, [open, updatePosition])

  useEffect(() => {
    if (!open) return
    const onScrollOrResize = () => updatePosition()
    window.addEventListener('resize', onScrollOrResize)
    window.addEventListener('scroll', onScrollOrResize, true)
    return () => {
      window.removeEventListener('resize', onScrollOrResize)
      window.removeEventListener('scroll', onScrollOrResize, true)
    }
  }, [open, updatePosition])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const anchorRect = anchorRef.current?.getBoundingClientRect()
  const fallbackTop = (anchorRect?.bottom ?? VIEWPORT_PAD) + GAP
  const fallbackLeft = Math.max(
    VIEWPORT_PAD,
    (anchorRect?.right ?? VIEWPORT_PAD + width) - width,
  )

  return createPortal(
    <>
      <div
        className="fixed inset-0"
        style={{ zIndex: POPOVER_Z_BACKDROP }}
        onClick={onClose}
        aria-hidden
      />
      <div
        ref={panelRef}
        className="fixed rounded-xl shadow-lg border p-3 flex flex-col"
        style={{
          top: pos?.top ?? fallbackTop,
          left: pos?.left ?? fallbackLeft,
          width,
          zIndex: POPOVER_Z_PANEL,
          background: '#FFF',
          borderColor: '#E2E8F0',
          maxHeight: `calc(100vh - ${VIEWPORT_PAD * 2}px)`,
        }}
        role="dialog"
        aria-modal="true"
      >
        {children}
      </div>
    </>,
    document.body,
  )
}
