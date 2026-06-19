import { useEffect, useState, type ReactNode } from 'react'
import PageLoadingOverlay from './PageLoadingOverlay'

/** Safety cap — never block the page longer than this (slow DB / hung API). */
const MAX_BOOT_MS = 25_000

export default function PageChartGate({
  children,
  bootReady = true,
  bootLoading = false,
  sessionKey = '',
  message,
  submessage,
}: {
  children: ReactNode
  bootReady?: boolean
  bootLoading?: boolean
  sessionKey?: string
  message?: string
  submessage?: string
}) {
  const [timedOut, setTimedOut] = useState(false)

  useEffect(() => {
    setTimedOut(false)
  }, [sessionKey])

  const waiting = !bootReady || bootLoading

  useEffect(() => {
    if (!waiting) return
    const t = window.setTimeout(() => setTimedOut(true), MAX_BOOT_MS)
    return () => clearTimeout(t)
  }, [waiting, sessionKey])

  const showOverlay = waiting && !timedOut

  const defaultSub =
    submessage ?? 'Fetching data from your ERP…'

  return (
    <>
      <PageLoadingOverlay
        visible={showOverlay}
        sessionKey={sessionKey}
        message={message ?? 'Loading analytics…'}
        submessage={defaultSub}
      />
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
