import { useCallback, useEffect, useRef, useState, type DependencyList } from 'react'

export type AgingLoadContext = {
  isCurrent: () => boolean
  /** Call after the primary section payload (incl. trend) is applied — UI renders fully populated. */
  markReady: () => void
}

/**
 * Loads aging section data with stale-response guard and keeps last good charts while refreshing.
 */
export function useAgingSectionLoad(
  loadFn: (ctx: AgingLoadContext) => Promise<void>,
  deps: DependencyList,
) {
  const [initialLoading, setInitialLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loadedOnce, setLoadedOnce] = useState(false)
  const genRef = useRef(0)
  const loadFnRef = useRef(loadFn)
  loadFnRef.current = loadFn

  const run = useCallback(() => {
    const gen = ++genRef.current
    const isCurrent = () => gen === genRef.current
    const markReady = () => {
      if (!isCurrent()) return
      setLoadedOnce(true)
      setInitialLoading(false)
    }

    setRefreshing(true)
    setError(null)

    void loadFnRef
      .current({ isCurrent, markReady })
      .catch(e => {
        if (!isCurrent()) return
        setError(e instanceof Error ? e.message : 'Failed to load')
      })
      .finally(() => {
        if (!isCurrent()) return
        setRefreshing(false)
        setInitialLoading(false)
      })
  }, deps)

  useEffect(() => {
    run()
    return () => {
      genRef.current += 1
    }
  }, [run])

  return { initialLoading: initialLoading && !loadedOnce, refreshing, error, loadedOnce, reload: run }
}
