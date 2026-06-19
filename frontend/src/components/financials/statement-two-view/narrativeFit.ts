import type { FinancialStatementRow } from '../../../lib/api'
import type { PlNarrativeBullet } from '../pl-two-view/plNarrativeEngine'

/** Opening-only BS/WC bullet (no account/sub-line chain). */
const OPENING_ONLY_RE =
  /^.+\s+stood at\s+.+\s+as of\s+\w+\d{2}(?:,\s+(?:up|down)\s+.+\s+from the\s+\w+\d{2}\s+month-end)?\.?$/i

const ANALYSIS_MARKERS_RE =
  /main balance driver|led by|material posting|largest account|largest contributor|largest single account|concentrated in|secondary contributor|aging|movement|contributor|days at period-end|\bDIO\b|\bDSO\b|\bDPO\b|vs prior month-end|balance movement|year-on-year|nets off|%\s+of total assets/i

export function hasSubstantiveNarrativeText(text: string): boolean {
  const t = (text || '').trim()
  if (t.length >= 200) return true
  if (ANALYSIS_MARKERS_RE.test(t)) return true
  return !OPENING_ONLY_RE.test(t) && t.length > 120
}

/** Count rows visible in report mini-table (matches expansion). */
export function countVisibleStatementRows(
  rows: FinancialStatementRow[],
  checkOpen: (id: string) => boolean,
): number {
  let n = 0
  function walk(rs: FinancialStatementRow[]) {
    for (const r of rs) {
      n += 1
      const kids = [...(r.children ?? []), ...(r.accounts ?? [])]
      if (kids.length && checkOpen(r.id)) walk(kids)
    }
  }
  walk(rows)
  return n
}

/**
 * Drop lowest-priority bullets so narrative block does not exceed table height (heuristic).
 * Preserves table order (index order); removes from the end when over budget.
 */
export function fitBulletsToTable(
  bullets: PlNarrativeBullet[],
  visibleTableRows: number,
  intro?: string | null,
  maxCap = 5,
): PlNarrativeBullet[] {
  if (!bullets.length) return bullets

  const maxCount = Math.min(maxCap, Math.max(3, Math.floor(visibleTableRows / 2.2)))
  let trimmed = bullets.slice(0, maxCount)

  const introLines = intro ? Math.max(2, Math.ceil(intro.length / 88)) : 0
  const lineBudget = Math.max(5, visibleTableRows + 2) - introLines
  const charsPerLine = 92

  let usedLines = 0
  const kept: PlNarrativeBullet[] = []
  for (const b of trimmed) {
    const lines = Math.max(2, Math.ceil(b.text.length / charsPerLine))
    if (usedLines + lines > lineBudget && kept.length >= 3) break
    kept.push(b)
    usedLines += lines
  }

  if (kept.length >= 3) {
    return kept.map((b, i) => ({ ...b, index: i + 1 }))
  }
  return trimmed.slice(0, Math.max(3, maxCount)).map((b, i) => ({ ...b, index: i + 1 }))
}
