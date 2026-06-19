import type { FinancialStatementColLabels } from '../../../lib/api'
import type { CSSProperties } from 'react'
import { stripLegalForm } from '../../../lib/stripLegalForm'

/** Prominent turquoise-blue for in-content section labels (matches body text size, bold). */
export const PL_SECTION_HEADING_COLOR = '#0E7490'

export const plSectionHeadingStyle: CSSProperties = {
  color: PL_SECTION_HEADING_COLOR,
  fontSize: '0.75rem',
  lineHeight: 1.35,
  fontWeight: 700,
}

export const KEY_DRIVERS_HEADING = 'Key drivers'

export function buildConsolidatedTableHeading(lbl: FinancialStatementColLabels): string {
  const cm = lbl.cm ?? 'CM'
  const pm = lbl.pm ?? 'prior month'
  return `Consolidated Income Statement — ${cm} compared to ${pm} and Plan`
}

/** Per-entity report mini-table heading, e.g. "Atlas Bau's Income Statement — …" */
export function buildEntityTableHeading(
  entityDisplayName: string,
  lbl: FinancialStatementColLabels,
): string {
  const cm = lbl.cm ?? 'CM'
  const pm = lbl.pm ?? 'prior month'
  const name = stripLegalForm(entityDisplayName.trim())
  return `${name}'s Income Statement — ${cm} compared to ${pm} and Plan`
}

export function buildReportTableHeading(
  lbl: FinancialStatementColLabels,
  opts?: { entityDisplayName?: string; consolidated?: boolean },
): string {
  if (opts?.consolidated || !opts?.entityDisplayName?.trim()) {
    return buildConsolidatedTableHeading(lbl)
  }
  return buildEntityTableHeading(opts.entityDisplayName, lbl)
}
