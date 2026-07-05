import type { PersonnelAccountingResponse } from '../../../lib/api'

import type { PlNarrativeBullet } from '../pl-two-view/plNarrativeEngine'

import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'

export interface PayrollNarrativeBullet {
  index: number
  row_id: string
  label: string
  text: string
  tone?: string
}

const PAYROLL_ROW_ID_PREFIX = 'payroll-'

function mapStructuredBullets(bullets: PayrollNarrativeBullet[]): PlNarrativeBullet[] {
  return bullets.map(b => ({
    index: b.index,
    line_code: b.row_id,
    label: b.label,
    priority: 0,
    text: b.text,
    tone: (b.tone as PlNarrativeBullet['tone']) ?? 'neutral',
  }))
}

/** Payroll accounting row ids only (e.g. payroll-Sales, payroll-total). */
export function isPayrollAccountingRowBullet(rowId: string): boolean {
  return rowId.startsWith(PAYROLL_ROW_ID_PREFIX)
}

function filterPayrollRowBullets(
  bullets: PayrollNarrativeBullet[],
  rowIds: Set<string>,
): PayrollNarrativeBullet[] {
  return bullets.filter(b => isPayrollAccountingRowBullet(b.row_id) && rowIds.has(b.row_id))
}

/** Report view: row-linked payroll accounting narratives only (no intro / movements). */
export function resolvePayrollNarrative(
  data: PersonnelAccountingResponse,
): { bullets: PlNarrativeBullet[] } {
  const rowIds = new Set(data.rows.map(r => r.id))
  if (data.narrative?.bullets?.length) {
    const filtered = filterPayrollRowBullets(data.narrative.bullets, rowIds)
    return { bullets: mapStructuredBullets(filtered) }
  }
  return { bullets: [] }
}

export function mapPayrollNarrativeBullets(data: PersonnelAccountingResponse): PlNarrativeBullet[] {
  return resolvePayrollNarrative(data).bullets
}

export function buildPayrollMarkerMap(
  bullets: PlNarrativeBullet[],
  rowIds: Set<string>,
): ReportCommentMarkerMap {
  const map: ReportCommentMarkerMap = {}
  for (const b of bullets) {
    if (!rowIds.has(b.line_code)) continue
    map[b.line_code] = { tier: 'primary', index: b.index }
  }
  return map
}

export function isTopSectionHeader(row: { row_kind?: string; depth?: number; id?: string }): boolean {
  return row.row_kind === 'section_header' && (row.depth == null || row.depth === undefined)
}

export function formatPayrollValueCell(
  row: { row_kind?: string; depth?: number },
  value: number | null | undefined,
  unit: string,
  format: (v: number | null | undefined, unit: string) => string,
): string {
  if (isTopSectionHeader(row)) return ''
  return format(value, unit)
}
