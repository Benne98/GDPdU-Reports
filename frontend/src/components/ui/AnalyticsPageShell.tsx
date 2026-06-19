import { type ReactNode } from 'react'
import { ChartLoadProvider } from '../../context/ChartLoadContext'
import PageChartGate from './PageChartGate'

/** Wraps analytics pages with a boot-time loading overlay (period/filters). Widgets load in place. */
export default function AnalyticsPageShell({
  children,
  resetKey = '',
  bootReady = true,
  bootLoading = false,
  message,
  submessage,
  timeoutMs,
}: {
  children: ReactNode
  resetKey?: string
  bootReady?: boolean
  bootLoading?: boolean
  message?: string
  submessage?: string
  timeoutMs?: number
}) {
  return (
    <ChartLoadProvider resetKey={resetKey} timeoutMs={timeoutMs}>
      <PageChartGate
        bootReady={bootReady}
        bootLoading={bootLoading}
        sessionKey={resetKey}
        message={message}
        submessage={submessage}
      >
        {children}
      </PageChartGate>
    </ChartLoadProvider>
  )
}
