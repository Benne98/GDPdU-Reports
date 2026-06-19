import { useEffect, type RefObject } from 'react'

/** Marks a scroll target for ↑/↓ keyboard navigation between page charts. */
export const PAGE_CHART_ATTR = 'data-page-chart'

const SCROLL_OFFSET_PX = 88

function isEditableElement(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false
  const tag = target.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'BUTTON') return true
  if (target.isContentEditable) return true
  if (target.closest('[role="dialog"], [role="listbox"], [data-radix-popper-content-wrapper]')) {
    return true
  }
  return false
}

function visibleCharts(root: ParentNode): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(`[${PAGE_CHART_ATTR}]`)).filter(el => {
    const r = el.getBoundingClientRect()
    return r.height > 40 && r.width > 40
  })
}

/** Index of the chart whose top edge is closest to the sticky header offset. */
function activeChartIndex(charts: HTMLElement[]): number {
  if (!charts.length) return 0
  let best = 0
  let bestDist = Infinity
  charts.forEach((el, i) => {
    const dist = Math.abs(el.getBoundingClientRect().top - SCROLL_OFFSET_PX)
    if (dist < bestDist) {
      bestDist = dist
      best = i
    }
  })
  return best
}

function scrollToChartStart(el: HTMLElement): void {
  const top = el.getBoundingClientRect().top + window.scrollY - SCROLL_OFFSET_PX
  window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
  if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1')
  requestAnimationFrame(() => {
    el.focus({ preventScroll: true })
  })
}

type Options = {
  /** When false, arrow keys are ignored (e.g. loading overlay). */
  enabled?: boolean
}

/**
 * ArrowUp / ArrowDown scroll to the start of the previous / next chart on the page.
 * Charts must be marked with `data-page-chart` (see PAGE_CHART_ATTR).
 */
export function usePageChartKeyboardNav(
  scopeRef: RefObject<HTMLElement | null>,
  options?: Options,
): void {
  const enabled = options?.enabled ?? true

  useEffect(() => {
    if (!enabled) return

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
      if (e.altKey || e.ctrlKey || e.metaKey) return
      if (isEditableElement(e.target)) return

      const root = scopeRef.current ?? document
      const charts = visibleCharts(root)
      if (charts.length < 2) return

      const cur = activeChartIndex(charts)
      const next =
        e.key === 'ArrowDown'
          ? Math.min(cur + 1, charts.length - 1)
          : Math.max(cur - 1, 0)

      if (next === cur) return

      e.preventDefault()
      scrollToChartStart(charts[next]!)
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [scopeRef, enabled])
}
