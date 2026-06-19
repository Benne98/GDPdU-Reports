import { useEffect, useRef } from 'react'
import { useChartLoadContextOptional, type ChartLoadStatus } from '../context/ChartLoadContext'

/**
 * Registers a chart/widget with the page load coordinator.
 * Returns true when this chart timed out — caller should hide the chart.
 */
export function useChartLoadReporter(
  chartId: string,
  loading: boolean,
  error?: string | null,
  enabled = true,
): boolean {
  const ctx = useChartLoadContextOptional()
  const registerRef = useRef(ctx?.register)
  const unregisterRef = useRef(ctx?.unregister)
  const setStatusRef = useRef(ctx?.setStatus)
  registerRef.current = ctx?.register
  unregisterRef.current = ctx?.unregister
  setStatusRef.current = ctx?.setStatus

  useEffect(() => {
    if (!enabled) return
    registerRef.current?.(chartId)
    return () => unregisterRef.current?.(chartId)
  }, [chartId, enabled])

  useEffect(() => {
    if (!enabled) return
    let status: ChartLoadStatus = 'ready'
    if (error) status = 'error'
    else if (loading) status = 'loading'
    setStatusRef.current?.(chartId, status, error ?? undefined)
  }, [chartId, loading, error, enabled])

  if (!ctx || !enabled) return false
  return ctx.isTimedOut(chartId)
}

/** Invisible helper — place inside ChartLoadProvider */
export function ChartLoadReporter({
  chartId,
  loading,
  error = null,
  enabled = true,
}: {
  chartId: string
  loading: boolean
  error?: string | null
  enabled?: boolean
}) {
  useChartLoadReporter(chartId, loading, error, enabled)
  return null
}
