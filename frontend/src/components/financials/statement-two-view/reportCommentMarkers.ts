import type { FinancialStatementRow } from '../../../lib/api'

export type ReportCommentMarker =
  | { tier: 'primary'; index: number }
  | { tier: 'sub'; index: number; sub: number }

export type ReportCommentMarkerMap = Record<string, ReportCommentMarker>

export type ReportBulletRef = {
  index: number
  line_code: string
}

type RowMeta = {
  row: FinancialStatementRow
  parentLineCode: string | null
}

/** Visible line_codes in report mini-table order (matches renderPlTableRows walk). */
export function collectVisibleLineCodes(
  rows: FinancialStatementRow[],
  checkOpen: (id: string) => boolean,
): Set<string> {
  const visible = new Set<string>()
  function walk(rs: FinancialStatementRow[]) {
    for (const r of rs) {
      if (r.line_code) visible.add(r.line_code)
      const kids = [...(r.children ?? []), ...(r.accounts ?? [])]
      if (kids.length && checkOpen(r.id)) walk(kids)
    }
  }
  walk(rows)
  return visible
}

function walkRowMeta(
  rows: FinancialStatementRow[],
  parentLineCode: string | null,
  out: Map<string, RowMeta>,
) {
  for (const r of rows) {
    const lc = r.line_code
    if (lc) out.set(lc, { row: r, parentLineCode })
    for (const ch of r.children ?? []) walkRowMeta([ch], lc ?? parentLineCode, out)
    for (const acc of r.accounts ?? []) walkRowMeta([acc], lc ?? parentLineCode, out)
  }
}

export function buildParentByLineCode(rows: FinancialStatementRow[]): Map<string, string | null> {
  const meta = new Map<string, RowMeta>()
  walkRowMeta(rows, null, meta)
  const parent = new Map<string, string | null>()
  for (const [lc, { parentLineCode }] of meta) parent.set(lc, parentLineCode)
  return parent
}

function isPreferredMarkerRow(row: FinancialStatementRow | undefined): boolean {
  if (!row) return false
  return row.row_kind === 'line' || row.row_kind === 'subtotal'
}

function findRowByLineCode(
  rows: FinancialStatementRow[],
  lineCode: string,
): FinancialStatementRow | null {
  for (const r of rows) {
    if (r.line_code === lineCode) return r
    const kids = [...(r.children ?? []), ...(r.accounts ?? [])]
    if (kids.length) {
      const found = findRowByLineCode(kids, lineCode)
      if (found) return found
    }
  }
  return null
}

/** Nearest visible ancestor for marker placement; prefers line/subtotal rows. */
export function resolveVisibleMarkerLineCode(
  lineCode: string,
  rows: FinancialStatementRow[],
  checkOpen: (id: string) => boolean,
): string {
  const visible = collectVisibleLineCodes(rows, checkOpen)
  if (visible.has(lineCode)) return lineCode

  const meta = new Map<string, RowMeta>()
  walkRowMeta(rows, null, meta)
  const parentBy = buildParentByLineCode(rows)

  let current: string | null | undefined = lineCode
  let fallback: string | null = null
  while (current) {
    if (visible.has(current)) {
      const row = meta.get(current)?.row
      if (isPreferredMarkerRow(row)) return current
      if (!fallback) fallback = current
    }
    current = parentBy.get(current) ?? null
  }
  return fallback ?? lineCode
}

/**
 * Row that receives the primary # marker: visible parent line when commenting a sub-position,
 * otherwise nearest visible ancestor when the target row is collapsed away.
 */
export function resolveMarkerAnchorLineCode(
  lineCode: string,
  rows: FinancialStatementRow[],
  checkOpen: (id: string) => boolean,
): string {
  const visible = collectVisibleLineCodes(rows, checkOpen)
  if (!visible.has(lineCode)) {
    return resolveVisibleMarkerLineCode(lineCode, rows, checkOpen)
  }

  const parentBy = buildParentByLineCode(rows)
  const parentLc = parentBy.get(lineCode)
  if (parentLc && visible.has(parentLc)) {
    const parentRow = findRowByLineCode(rows, parentLc)
    if (parentRow?.row_kind === 'line') return parentLc
  }

  return lineCode
}

/**
 * Map bullets to table markers: primary on visible parent, sub on visible children.
 */
export function buildReportCommentMarkerMap(
  bullets: ReportBulletRef[],
  rows: FinancialStatementRow[],
  checkOpen: (id: string) => boolean,
): ReportCommentMarkerMap {
  if (!bullets.length || !rows.length) return {}

  const visible = collectVisibleLineCodes(rows, checkOpen)

  type GroupEntry = { bullet: ReportBulletRef; target: string; parent: string }
  const groups = new Map<string, GroupEntry[]>()

  for (const bullet of bullets) {
    const target = bullet.line_code
    const parent = resolveMarkerAnchorLineCode(target, rows, checkOpen)
    const list = groups.get(parent) ?? []
    list.push({ bullet, target, parent })
    groups.set(parent, list)
  }

  const map: ReportCommentMarkerMap = {}

  for (const [, entries] of groups) {
    const sorted = [...entries].sort((a, b) => a.bullet.index - b.bullet.index)
    const primaryIndex = Math.min(...sorted.map(e => e.bullet.index))

    map[sorted[0].parent] = { tier: 'primary', index: primaryIndex }

    let sub = 0
    for (const { target, parent } of sorted) {
      if (target === parent) continue
      if (!visible.has(target)) continue
      sub += 1
      map[target] = { tier: 'sub', index: primaryIndex, sub }
    }
  }

  return map
}

/** Legacy flat index map (primary markers only). */
export function commentMarkerMapToIndexMap(
  markers: ReportCommentMarkerMap,
): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [lc, m] of Object.entries(markers)) {
    out[lc] = m.index
  }
  return out
}
