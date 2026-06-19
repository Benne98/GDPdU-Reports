import { useEffect, useState, type ReactNode } from 'react'
import PageLoadingOverlay from './PageLoadingOverlay'

/** Safety cap — never block the page longer than this (hung API / slow DB). */
const MAX_BOOT_MS = 25_000

/** Full-page loading gate for non-analytics pages (no chart registry). */
export default function PageShell({
  children,
  ready = true,
  loading = false,
  message = 'Loading…',
  submessage,
}: {
  children: ReactNode
  ready?: boolean
  loading?: boolean
  message?: string
  submessage?: string
}) {
  const [timedOut, setTimedOut] = useState(false)
  const waiting = !ready || loading

  useEffect(() => {
    if (!waiting) {
      setTimedOut(false)
      return
    }
    const t = window.setTimeout(() => setTimedOut(true), MAX_BOOT_MS)
    return () => clearTimeout(t)
  }, [waiting])

  const showOverlay = waiting && !timedOut

  return (
    <>
      <PageLoadingOverlay visible={showOverlay} message={message} submessage={submessage} />
      {timedOut && waiting && (
        <div className="fixed bottom-4 left-1/2 z-[56] -translate-x-1/2 max-w-md rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 shadow-lg">
          Loading is taking longer than expected. You can continue — some data may still be loading in
          the background.
        </div>
      )}
      <div
        className={showOverlay ? 'pointer-events-none select-none' : undefined}
        style={showOverlay ? { opacity: 0.35 } : undefined}
        aria-hidden={showOverlay}
      >
        {children}
      </div>
    </>
  )
}
