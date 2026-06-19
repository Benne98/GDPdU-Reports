export type AgingView = 'receivables' | 'payables'

const STORAGE_KEY = 'finssentials.sales.aging.view.v1'

export function loadAgingView(): AgingView {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'receivables' || v === 'payables') return v
  } catch { /* ignore */ }
  return 'receivables'
}

export function saveAgingView(view: AgingView) {
  try {
    localStorage.setItem(STORAGE_KEY, view)
  } catch { /* ignore */ }
}
