/** Detect period comparison phrasing in narrative bullets (month or ISO calendar week). */
export function bulletHasPeriodComparison(text: string): boolean {
  return /compared to (?:[A-Za-z]{3}\d{2}|C[Ww]\d{2}'?\d{2}|K[Ww]\d{2}'?\d{2})/i.test(text)
}

/** Balance-sheet bullets use point-in-time month-end phrasing (not P&L "compared to"). */
export function bulletHasBsPeriodComparison(text: string): boolean {
  return (
    /from the\s+[A-Za-z]{3}\d{2}\s+month-end/i.test(text) ||
    /as of\s+[A-Za-z]{3}\d{2}/i.test(text) ||
    bulletHasPeriodComparison(text)
  )
}

const BS_DEPTH_MARKERS_RE =
  /concentrated in|largest contributor|largest single account|Year-on-year|nets off|%\s+of total assets|secondary contributor|led by/i

/** Backend BS narrative bullets carry GL / sub-line depth beyond a one-line opening. */
export function bulletHasBsNarrativeDepth(text: string): boolean {
  const t = (text || '').trim()
  if (!t) return false
  if (BS_DEPTH_MARKERS_RE.test(t)) return true
  return bulletHasBsPeriodComparison(t) && t.length >= 80
}
