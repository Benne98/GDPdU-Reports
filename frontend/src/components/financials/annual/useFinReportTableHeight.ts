import { useEffect, useRef, useState } from 'react'

/** Match monthly report view: narrative column height follows table column. */
export function useFinReportTableHeight(resetKey: unknown) {
  const tableWrapRef = useRef<HTMLDivElement>(null)
  const [tableHeightPx, setTableHeightPx] = useState<number | null>(null)

  useEffect(() => {
    const el = tableWrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      setTableHeightPx(el.getBoundingClientRect().height)
    })
    ro.observe(el)
    setTableHeightPx(el.getBoundingClientRect().height)
    return () => ro.disconnect()
  }, [resetKey])

  return { tableWrapRef, tableHeightPx }
}
