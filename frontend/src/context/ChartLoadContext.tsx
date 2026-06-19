import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

export type ChartLoadStatus = 'loading' | 'ready' | 'error' | 'timeout'

type ChartEntry = {
  status: ChartLoadStatus
  error?: string
}

type ChartLoadContextValue = {
  register: (id: string) => void
  unregister: (id: string) => void
  setStatus: (id: string, status: ChartLoadStatus, error?: string) => void
  isTimedOut: (id: string) => boolean
  timeoutMs: number
}

const ChartLoadContext = createContext<ChartLoadContextValue | null>(null)

export function ChartLoadProvider({
  children,
  resetKey = '',
  timeoutMs = 12_000,
}: {
  children: ReactNode
  /** Change when filters/tab change — clears registry and timers */
  resetKey?: string
  timeoutMs?: number
}) {
  const [charts, setCharts] = useState<Record<string, ChartEntry>>({})
  const chartsRef = useRef(charts)
  chartsRef.current = charts
  const timersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  const clearTimers = useCallback(() => {
    Object.values(timersRef.current).forEach(clearTimeout)
    timersRef.current = {}
  }, [])

  useEffect(() => {
    clearTimers()
    setCharts({})
  }, [resetKey, clearTimers])

  useEffect(() => () => clearTimers(), [clearTimers])

  const register = useCallback((id: string) => {
    setCharts(prev => {
      if (prev[id]) return prev
      return { ...prev, [id]: { status: 'loading' } }
    })
  }, [])

  const unregister = useCallback((id: string) => {
    if (timersRef.current[id]) {
      clearTimeout(timersRef.current[id])
      delete timersRef.current[id]
    }
    setCharts(prev => {
      if (!prev[id]) return prev
      const next = { ...prev }
      delete next[id]
      return next
    })
  }, [])

  const setStatus = useCallback(
    (id: string, status: ChartLoadStatus, error?: string) => {
      setCharts(prev => {
        const cur = prev[id]
        if (cur?.status === status && cur?.error === error) return prev
        return { ...prev, [id]: { status, error } }
      })

      if (timersRef.current[id]) {
        clearTimeout(timersRef.current[id])
        delete timersRef.current[id]
      }

      if (status === 'loading') {
        timersRef.current[id] = setTimeout(() => {
          setCharts(prev => {
            if (prev[id]?.status !== 'loading') return prev
            return { ...prev, [id]: { status: 'timeout' } }
          })
        }, timeoutMs)
      }
    },
    [timeoutMs],
  )

  const isTimedOut = useCallback((id: string) => chartsRef.current[id]?.status === 'timeout', [])

  // Keep callback identity stable — chart state updates must not retrigger register/unregister loops.
  const value = useMemo(
    () => ({
      register,
      unregister,
      setStatus,
      isTimedOut,
      timeoutMs,
    }),
    [register, unregister, setStatus, isTimedOut, timeoutMs],
  )
  return <ChartLoadContext.Provider value={value}>{children}</ChartLoadContext.Provider>
}

export function useChartLoadContext(): ChartLoadContextValue {
  const ctx = useContext(ChartLoadContext)
  if (!ctx) {
    throw new Error('useChartLoadContext must be used within ChartLoadProvider')
  }
  return ctx
}

export function useChartLoadContextOptional(): ChartLoadContextValue | null {
  return useContext(ChartLoadContext)
}
