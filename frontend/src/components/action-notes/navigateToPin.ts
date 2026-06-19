import type { ViewPinSnapshot } from '../../lib/api'

/** Build app URL from a pinned view (filters + optional query). */
export function buildPinNavigateUrl(snap: ViewPinSnapshot): string {
  const params = new URLSearchParams()
  const tab = snap.filters?.tab
  if (tab != null && String(tab) !== '') params.set('tab', String(tab))
  const extra = snap.view_state?.search
  if (typeof extra === 'string' && extra.startsWith('?')) {
    new URLSearchParams(extra.slice(1)).forEach((v, k) => params.set(k, v))
  } else if (typeof extra === 'string' && extra.includes('=')) {
    new URLSearchParams(extra).forEach((v, k) => params.set(k, v))
  }
  const viewMode = snap.view_state?.view_mode
  if (viewMode) params.set('view', String(viewMode))
  const q = params.toString()
  return q ? `${snap.route}?${q}` : snap.route
}

export function navigateToPinSnapshot(snap: ViewPinSnapshot): void {
  if (snap.restore_mode === 'preview_only') return
  const url = buildPinNavigateUrl(snap)
  window.location.assign(url)
}
