import type { CSSProperties } from 'react'

import { PERIOD_HIGHLIGHT_BG } from '../pl-two-view/plTableCore'

/** Fixed-assets rollforward table colors — aligned with consolidated P&L table view. */
export const FA_NAVY = '#1E3A5F'
export const FA_HEADER_BG = '#F8FAFC'
export const FA_HEADER_TEXT = '#475569'
export const FA_BORDER = '#E2E8F0'

/** @deprecated Use PERIOD_HIGHLIGHT_BG — kept for cash-debt tables. */
export const FA_SNAPSHOT_COL_BG = PERIOD_HIGHLIGHT_BG
/** @deprecated Use FA_HEADER_BG — kept for cash-debt tables. */
export const FA_TABLE_GROUP_ROW_BG = FA_HEADER_BG

export type FaTableVariant = 'table' | 'report'

export function isFaYearEndColKey(key: string): boolean {
  return /^\d{4}-12-31$/.test(key)
}

export function isFaHighlightedColKey(key: string): boolean {
  return isFaYearEndColKey(key)
}

export function faHeaderCellStyle(colKey?: string): CSSProperties {
  const highlighted = colKey ? isFaHighlightedColKey(colKey) : false
  return {
    color: FA_HEADER_TEXT,
    background: highlighted ? PERIOD_HIGHLIGHT_BG : FA_HEADER_BG,
    fontWeight: highlighted ? 700 : 600,
  }
}

export function faLabelCellBg(_rowKind: string, _variant: FaTableVariant): string | undefined {
  return '#fff'
}

export function faLabelFontWeight(rowKind: string): number {
  return rowKind === 'total' ? 600 : 400
}

export function faValueFontWeight(rowKind: string): number {
  return rowKind === 'total' ? 600 : 400
}

export function faValueCellBg(key: string): string | undefined {
  if (isFaHighlightedColKey(key)) {
    return PERIOD_HIGHLIGHT_BG
  }
  return undefined
}

export function faGroupLabel(label: string, rowKind: string): string {
  if (rowKind === 'subtotal' && label.startsWith('Total ')) return label.slice(6)
  return label
}
