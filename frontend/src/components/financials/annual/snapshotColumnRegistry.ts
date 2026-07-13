/**
 * Column registry for the annual snapshot TOP table (BS + WC).
 * Drives the Table Builder pencil editor in ErSnapshotTable.
 */
import type { ErSnapshotColLabels } from '../../../lib/api'
import type { AnnualSnapshotTableColId } from './annualSnapshotConsolidationColumns'
import type { SnapshotConsolTarget } from './snapshotConsolidationCellResolver'

export type SnapshotColId = AnnualSnapshotTableColId
export type SnapshotColKind = 'amount' | 'delta' | 'cagr'

export type SnapshotColumnDef = {
  id: SnapshotColId
  kind: SnapshotColKind
  labelLine1: string
  labelLine2?: string
  /** Renders with blue-tinted background (cm column). */
  highlighted?: boolean
  isDelta?: boolean
  isPct?: boolean
}

export const DEFAULT_SNAPSHOT_COLUMN_IDS: SnapshotColId[] = [
  'dec_py2', 'fy_py', 'fy', 'cagr', 'delta_fy', 'cm_py', 'cm', 'delta_cm',
]

const STORAGE_KEYS: Record<'bs' | 'wc', string> = {
  bs: 'finssentials.bs.snapshotTableColumns.v1',
  wc: 'finssentials.wc.snapshotTableColumns.v1',
}

export function buildSnapshotCatalog(
  lbl?: ErSnapshotColLabels,
): Record<SnapshotColId, SnapshotColumnDef> {
  const decPy2 = lbl?.dec_py2 ?? 'Dec-3'
  const fyPy   = lbl?.fy_py   ?? 'Dec-2'
  const fy     = lbl?.fy      ?? 'Dec-1'
  const cmPy   = lbl?.cm_py   ?? 'CM PY'
  const cm     = lbl?.cm      ?? 'CM'
  return {
    dec_py2:  { id: 'dec_py2',  kind: 'amount', labelLine1: decPy2, labelLine2: 'FY end' },
    fy_py:    { id: 'fy_py',    kind: 'amount', labelLine1: fyPy,   labelLine2: 'FY end' },
    fy:       { id: 'fy',       kind: 'amount', labelLine1: fy,     labelLine2: 'FY end' },
    cagr:     { id: 'cagr',     kind: 'cagr',   labelLine1: 'CAGR', labelLine2: `${decPy2} – ${fy}`, isPct: true },
    delta_fy: { id: 'delta_fy', kind: 'delta',  labelLine1: `Δ ${fy} − ${fyPy}`, labelLine2: 'vs prior FY', isDelta: true },
    cm_py:    { id: 'cm_py',    kind: 'amount', labelLine1: cmPy,   labelLine2: 'Prior year CM' },
    cm:       { id: 'cm',       kind: 'amount', labelLine1: cm,     labelLine2: 'Current period', highlighted: true },
    delta_cm: { id: 'delta_cm', kind: 'delta',  labelLine1: `Δ ${cm} − ${cmPy}`, labelLine2: 'vs prior CM', isDelta: true },
  }
}

/** Returns saved ids, or null when no preference stored. */
export function loadSnapshotColumns(statement: 'bs' | 'wc'): SnapshotColId[] | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS[statement])
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return null
    const valid = (parsed as string[]).filter((id): id is SnapshotColId =>
      DEFAULT_SNAPSHOT_COLUMN_IDS.includes(id as SnapshotColId),
    )
    return valid.length > 0 ? valid : null
  } catch {
    return null
  }
}

export function saveSnapshotColumns(statement: 'bs' | 'wc', ids: SnapshotColId[]): void {
  try {
    localStorage.setItem(STORAGE_KEYS[statement], JSON.stringify(ids))
  } catch {
    // ignore
  }
}

export function reconcileSnapshotColumns(ids: SnapshotColId[]): SnapshotColId[] {
  const valid = new Set<string>(DEFAULT_SNAPSHOT_COLUMN_IDS)
  return ids.filter(id => valid.has(id))
}

// ─── Consolidation extra columns (Part B) ─────────────────────────────────────

export type SnapshotConsolExtraCol = {
  /** Unique composite id, e.g. "agg:cagr", "con:delta_fy", "e:COMP001:cm_py". */
  id: string
  snapshotColId: SnapshotColId
  target: SnapshotConsolTarget
  labelLine1: string
  /** Target group display subtitle in the editor list. */
  labelLine2?: string
}

const CONSOL_STORAGE_KEYS: Record<'bs' | 'wc', string> = {
  bs: 'finssentials.bs.snapshotConsolColumns.v1',
  wc: 'finssentials.wc.snapshotConsolColumns.v1',
}

export function loadConsolExtraCols(statement: 'bs' | 'wc'): SnapshotConsolExtraCol[] {
  try {
    const raw = localStorage.getItem(CONSOL_STORAGE_KEYS[statement])
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as SnapshotConsolExtraCol[]) : []
  } catch {
    return []
  }
}

export function saveConsolExtraCols(statement: 'bs' | 'wc', cols: SnapshotConsolExtraCol[]): void {
  try {
    localStorage.setItem(CONSOL_STORAGE_KEYS[statement], JSON.stringify(cols))
  } catch {
    // ignore
  }
}

/** Build a unique id for an extra consolidation column. */
export function makeConsolExtraColId(
  target: SnapshotConsolTarget,
  snapshotColId: SnapshotColId,
): string {
  switch (target.kind) {
    case 'entity':       return `e:${target.code}:${snapshotColId}`
    case 'aggregated':   return `agg:${snapshotColId}`
    case 'consolidation':return `con:${snapshotColId}`
    case 'ic':           return `ic:${snapshotColId}`
  }
}
