import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Runs an async loader when deps change. Always clears loading in finally;
 * only runs result handlers when the effect generation is still current.
 */
export function useAbortableLoad(
  loadFn: (isCurrent: () => boolean) => Promise<void>,
  deps: React.DependencyList,
  options?: { initialLoading?: boolean },
): { loading: boolean; error: string | null; reload: () => void } {
  const [loading, setLoading] = useState(options?.initialLoading ?? true)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const loadFnRef = useRef(loadFn)
  loadFnRef.current = loadFn

  const reload = useCallback(() => setTick(t => t + 1), [])

  useEffect(() => {
    let current = true
    const isCurrent = () => current

    setLoading(true)
    setError(null)

    void loadFnRef
      .current(isCurrent)
      .catch(e => {
        if (isCurrent()) {
          setError(e instanceof Error ? e.message : 'Failed to load')
        }
      })
      .finally(() => {
        setLoading(false)
      })

    return () => {
      current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tick triggers manual reload
  }, [...deps, tick])

  return { loading, error, reload }
}
