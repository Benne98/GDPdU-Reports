import type { ConsolidationRow, FinancialStatementRow } from '../../../lib/api'

import type { PlNarrativeBullet } from '../pl-two-view/plNarrativeEngine'

import {

  resolveMarkerAnchorLineCode,

  collectVisibleLineCodes,

  type ReportCommentMarkerMap,

} from '../statement-two-view/reportCommentMarkers'



/** Top-level line/subtotal rows shown in the report mini-table (no expanded children). */

export function collectDirectReportLineCodes(rows: FinancialStatementRow[]): string[] {

  const out: string[] = []

  for (const r of rows) {

    if (r.row_kind === 'title' || r.row_kind === 'kpi') continue

    if (r.line_code && (r.row_kind === 'line' || r.row_kind === 'subtotal')) {

      out.push(r.line_code)

    }

  }

  return out

}



/**

 * 1-based bullet indices ordered top-to-bottom for directly visible positions only.

 */

export function prepareAnnualReportBullets(

  bullets: PlNarrativeBullet[],

  rows: FinancialStatementRow[],

  checkOpen: (id: string) => boolean,

): PlNarrativeBullet[] {

  if (!bullets.length) return []



  const directCodes = new Set(collectDirectReportLineCodes(rows))

  const tableOrder = collectDirectReportLineCodes(rows)

  const position = (lineCode: string) => {

    const idx = tableOrder.indexOf(lineCode)

    return idx === -1 ? Number.MAX_SAFE_INTEGER : idx

  }



  const filtered = bullets

    .map(bullet => ({

      bullet,

      anchor: resolveMarkerAnchorLineCode(bullet.line_code, rows, checkOpen),

    }))

    .filter(({ bullet, anchor }) => directCodes.has(bullet.line_code) && anchor === bullet.line_code)



  filtered.sort((a, b) => {

    const pa = position(a.bullet.line_code)

    const pb = position(b.bullet.line_code)

    if (pa !== pb) return pa - pb

    const ia = a.bullet.index > 0 ? a.bullet.index : a.bullet.index + 1

    const ib = b.bullet.index > 0 ? b.bullet.index : b.bullet.index + 1

    return ia - ib

  })



  return filtered.map(({ bullet }, i) => ({

    ...bullet,

    index: i + 1,

  }))

}



/** Snapshot annual report (BS/WC): bullets anchor to visible rows including expanded children. */

function collectVisibleLineCodesOrdered(
  rows: FinancialStatementRow[],
  checkOpen: (id: string) => boolean,
): string[] {
  const order: string[] = []
  function walk(rs: FinancialStatementRow[]) {
    for (const r of rs) {
      if (r.line_code && r.row_kind !== 'title' && r.row_kind !== 'kpi') {
        order.push(r.line_code)
      }
      const kids = [...(r.children ?? []), ...(r.accounts ?? [])]
      if (kids.length && checkOpen(r.id)) walk(kids)
    }
  }
  walk(rows)
  return order
}

/** Client fallback bullets with raw FY/Dec % tables — not for report display. */
export function isGenericSnapshotBulletText(text: string): boolean {
  const t = text.trim()
  if (/^(FY|Dec)\d{2}A?:\s*[\d.,()+-]+.*%\s+vs\s+(FY|Dec)/i.test(t)) return true
  if (/;\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\d{2}A?:\s*[\d.,()+-]+.*%\s+YoY/i.test(t)) return true
  if (/^Annual snapshot —/i.test(t)) return true
  return false
}

export function prepareAnnualSnapshotReportBullets(
  bullets: PlNarrativeBullet[],
  rows: FinancialStatementRow[],
  checkOpen: (id: string) => boolean,
): PlNarrativeBullet[] {
  if (!bullets.length) return []

  const visible = collectVisibleLineCodes(rows, checkOpen)
  const tableOrder = collectVisibleLineCodesOrdered(rows, checkOpen)
  const position = (lineCode: string) => {
    const anchor = resolveMarkerAnchorLineCode(lineCode, rows, checkOpen)
    const idx = tableOrder.indexOf(anchor)
    return idx === -1 ? Number.MAX_SAFE_INTEGER : idx
  }

  const filtered = bullets
    .filter(b => !isGenericSnapshotBulletText(b.text))
    .map(bullet => ({
      bullet,
      anchor: resolveMarkerAnchorLineCode(bullet.line_code, rows, checkOpen),
    }))
    .filter(({ anchor }) => visible.has(anchor))

  filtered.sort((a, b) => {
    const pa = position(a.bullet.line_code)
    const pb = position(b.bullet.line_code)
    if (pa !== pb) return pa - pb
    return a.bullet.index - b.bullet.index
  })

  return filtered.map(({ bullet, anchor }, i) => ({
    ...bullet,
    index: i + 1,
    line_code: anchor,
  }))
}



/** Primary markers only on directly visible rows (no sub-position badges). */

export function buildAnnualReportCommentMarkerMap(

  bullets: PlNarrativeBullet[],

  rows: FinancialStatementRow[],

  checkOpen: (id: string) => boolean,

): ReportCommentMarkerMap {

  const ordered = prepareAnnualReportBullets(bullets, rows, checkOpen)

  if (!ordered.length) return {}



  const map: ReportCommentMarkerMap = {}

  for (const b of ordered) {

    if (!b.line_code || map[b.line_code]) continue

    map[b.line_code] = { tier: 'primary', index: b.index }

  }

  return map
}

function collectDirectConsolidationRowIds(
  rows: ConsolidationRow[],
  checkOpen: (id: string) => boolean = () => true,
): string[] {
  const out: string[] = []
  function walk(rs: ConsolidationRow[]) {
    for (const r of rs) {
      if (r.row_kind === 'line' || r.row_kind === 'subtotal' || r.row_kind === 'detail' || r.row_kind === 'account') {
        out.push(r.id)
      }
      if ((r.children?.length ?? 0) > 0 && checkOpen(r.id)) {
        walk(r.children ?? [])
      }
    }
  }
  walk(rows)
  return out
}

export function indexConsolidationRows(rows: ConsolidationRow[]): Map<string, ConsolidationRow> {
  const m = new Map<string, ConsolidationRow>()
  function walk(rs: ConsolidationRow[]) {
    for (const r of rs) {
      m.set(r.id, r)
      const stripped = r.id.replace(/^(?:pl|bs|wc|cf)-/, '')
      if (stripped) m.set(stripped, r)
      walk(r.children ?? [])
    }
  }
  walk(rows)
  return m
}

function resolveConsolidationRowId(
  lineCode: string,
  rowIndex: Map<string, ConsolidationRow>,
  directIds: Set<string>,
  label?: string,
): string | null {
  const candidates = [
    lineCode,
    `bs-${lineCode}`,
    `pl-${lineCode}`,
    `wc-${lineCode}`,
    `cf-${lineCode}`,
  ]
  for (const c of candidates) {
    const row = rowIndex.get(c)
    if (row && directIds.has(row.id)) return row.id
    if (directIds.has(c)) return c
  }
  const want = (label || '').trim().toLowerCase()
  if (want) {
    for (const row of rowIndex.values()) {
      if (!directIds.has(row.id)) continue
      if ((row.label || '').trim().toLowerCase() === want) return row.id
    }
  }
  return null
}

export function resolveConsolidationRowIdForBullet(
  bullet: Pick<PlNarrativeBullet, 'line_code' | 'label'>,
  rowIndex: Map<string, ConsolidationRow>,
  directIds: Set<string>,
): string | null {
  return resolveConsolidationRowId(bullet.line_code, rowIndex, directIds, bullet.label)
}

export function prepareAnnualConsolidationReportBullets(
  bullets: PlNarrativeBullet[],
  rows: ConsolidationRow[],
  checkOpen: (id: string) => boolean = () => true,
): PlNarrativeBullet[] {
  if (!bullets.length) return []
  const directIds = new Set(collectDirectConsolidationRowIds(rows, checkOpen))
  const tableOrder = collectDirectConsolidationRowIds(rows, checkOpen)
  const rowIndex = indexConsolidationRows(rows)
  const position = (id: string) => {
    const idx = tableOrder.indexOf(id)
    return idx === -1 ? Number.MAX_SAFE_INTEGER : idx
  }

  const resolved = bullets
    .filter(b => !isGenericSnapshotBulletText(b.text))
    .map(bullet => {
      const rowId = resolveConsolidationRowId(bullet.line_code, rowIndex, directIds, bullet.label)
      return rowId ? { bullet, rowId } : null
    })
    .filter((x): x is { bullet: PlNarrativeBullet; rowId: string } => x !== null)

  resolved.sort((a, b) => {
    const pa = position(a.rowId)
    const pb = position(b.rowId)
    if (pa !== pb) return pa - pb
    return a.bullet.index - b.bullet.index
  })

  return resolved.map(({ bullet, rowId }, i) => ({
    ...bullet,
    index: i + 1,
    line_code: rowId,
  }))
}

export function buildAnnualConsolidationCommentMarkerMap(
  bullets: PlNarrativeBullet[],
  rows: ConsolidationRow[],
  checkOpen: (id: string) => boolean = () => true,
): ReportCommentMarkerMap {
  const ordered = prepareAnnualConsolidationReportBullets(bullets, rows, checkOpen)
  const map: ReportCommentMarkerMap = {}
  for (const b of ordered) {
    if (!b.line_code || map[b.line_code]) continue
    map[b.line_code] = { tier: 'primary', index: b.index }
  }
  return map
}

