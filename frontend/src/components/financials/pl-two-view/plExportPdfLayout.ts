import { PL_SECTION_HEADING_COLOR } from './plReportSectionHeadings'

/** A4 landscape (mm). */
export const PAGE_W_MM = 297
export const PAGE_H_MM = 210

export const MARGIN_MM = 10
export const COL_GAP_MM = 6

const CONTENT_W_MM = PAGE_W_MM - MARGIN_MM * 2
/** Narrative column cap (right side). */
const NARRATIVE_MAX_MM = 92

/** Table left (wider), narrative right (narrower) — matches PlReportView. */
export const NARRATIVE_W_MM = Math.min(
  (CONTENT_W_MM - COL_GAP_MM) * (1 / (1 + 1.15)),
  NARRATIVE_MAX_MM,
)
export const TABLE_W_MM = CONTENT_W_MM - COL_GAP_MM - NARRATIVE_W_MM

export const TABLE_X_MM = MARGIN_MM
export const NARRATIVE_X_MM = MARGIN_MM + TABLE_W_MM + COL_GAP_MM
export const CONTENT_RIGHT_MM = PAGE_W_MM - MARGIN_MM

/** @deprecated Use TABLE_W_MM — kept for imports during transition */
export const LEFT_W_MM = NARRATIVE_W_MM
export const LEFT_X_MM = NARRATIVE_X_MM
export const RIGHT_W_MM = TABLE_W_MM
export const RIGHT_X_MM = TABLE_X_MM
export const TABLE_WIDTH_MM = TABLE_W_MM

export const TITLE_Y_MM = 14
export const SECTION_Y_MM = 24
export const CONTENT_TOP_MM = 30
export const FOOTER_LINE_Y_MM = 193
export const FOOTER_Y_MM = 198

export const FONT_TITLE_PT = 16
export const FONT_SECTION_PT = 9
export const FONT_BODY_PT = 9
export const FONT_FOOTER_PT = 7.5
export const FONT_FOOTER_BRAND_PT = 8

export const COLOR_TITLE = '#111827'
export const COLOR_BODY = '#475569'
export const COLOR_MUTED = '#64748B'
export const COLOR_NAVY = '#1E3A5F'
export const COLOR_SECTION = PL_SECTION_HEADING_COLOR
export const COLOR_BORDER = '#E2E8F0'

export const BADGE_RADIUS_MM = 1.6
export const BADGE_GAP_MM = 1.5
/** @deprecated Use NARRATIVE_BULLET_GAP_MM from finssentialsExport/narrativeBadgeLayout */
export const BULLET_LINE_GAP_MM = 1.2

export const MAIN_TITLE = 'Consolidated Income Statement'

/** CSS pixels at 96dpi for a given width in mm (1x, before html2canvas scale). */
export function mmToPx(mm: number): number {
  return Math.round((mm * 96) / 25.4)
}

/** Convert canvas pixels to mm at a given capture scale. */
export function pxToMm(px: number, captureScale: number): number {
  return (px / captureScale) * (25.4 / 96)
}

export function captureScale(): number {
  return Math.max(3, Math.round((window.devicePixelRatio || 1) * 2))
}

export function maxTableHeightMm(pageHmm: number = PAGE_H_MM): number {
  const footerLineY = pageHmm - (PAGE_H_MM - FOOTER_LINE_Y_MM)
  return footerLineY - CONTENT_TOP_MM - 2
}

export function footerYPositions(pageHmm: number): { lineY: number; textY: number } {
  return {
    lineY: pageHmm - (PAGE_H_MM - FOOTER_LINE_Y_MM),
    textY: pageHmm - (PAGE_H_MM - FOOTER_Y_MM),
  }
}

/** Page height needed to fit table image at full width without downscaling. */
export function pageHeightForTable(tableHeightMm: number): number {
  const footerReserve = PAGE_H_MM - FOOTER_LINE_Y_MM + 4
  return Math.max(PAGE_H_MM, CONTENT_TOP_MM + tableHeightMm + footerReserve)
}
