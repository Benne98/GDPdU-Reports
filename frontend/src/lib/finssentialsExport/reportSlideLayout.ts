/**
 * Shared slide/page layout for PPT and PDF exports (Finssentials branding).
 * Positions aligned to PowerPoint Export Example.pptx (16:9, guides at center).
 */
import type PptxGenJS from 'pptxgenjs'
import { PROJECT_NAME } from './designStyles'

export const PPT_COLORS = {
  navy: '1E3A5F',
  slate: '475569',
  muted: '64748B',
  border: 'E2E8F0',
  title: '111827',
} as const

export const PPT_SLIDE_W = 10
export const PPT_MARGIN_X = 0.35

/** Vertical guide at slide centre (10" / 2) — right column starts here. */
export const PPT_GUIDE_CENTER_X = PPT_SLIDE_W / 2

export const PPT_HEADER_BOTTOM = 0.54
export const PPT_SECTION_GAP = 0.2
export const PPT_SECTION_LABEL_H = 0.2
export const PPT_SECTION_LABEL_Y = PPT_HEADER_BOTTOM + PPT_SECTION_GAP
/** Table + narrative body (example: y ≈ 0.94"). */
export const PPT_CONTENT_TOP = 0.94
export const PPT_FOOTER_LINE_Y = 5.18
export const PPT_FOOTER_TEXT_Y = 5.24
export const PPT_CONTENT_BOTTOM = PPT_FOOTER_LINE_Y

export const PPT_NARRATIVE_FONT_PT = 6.5
export const PPT_TABLE_X = PPT_MARGIN_X
/** Left column width up to centre guide. */
export const PPT_TABLE_W = PPT_GUIDE_CENTER_X - PPT_TABLE_X - 0.1
export const PPT_NARRATIVE_X = PPT_GUIDE_CENTER_X
/** Right column through to right margin (footer line end). */
export const PPT_NARRATIVE_W = PPT_SLIDE_W - PPT_NARRATIVE_X - PPT_MARGIN_X

export function pptGeneratedLabel(): string {
  return new Date().toLocaleDateString('de-DE', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

export type PptxHeaderOpts = {
  breadcrumbParent?: string
  breadcrumbCurrent: string
  pageTitle: string
}

export function addFinssentialsPptxHeader(
  slide: PptxGenJS.Slide,
  pptx: PptxGenJS,
  opts: PptxHeaderOpts,
) {
  const parent = opts.breadcrumbParent ?? 'Financials'
  slide.addText(
    [
      { text: parent, options: { fontSize: 6, color: PPT_COLORS.muted } },
      { text: '   ›   ', options: { fontSize: 6, color: PPT_COLORS.muted } },
      { text: opts.breadcrumbCurrent, options: { fontSize: 6, color: PPT_COLORS.navy, bold: true } },
    ],
    { x: PPT_MARGIN_X, y: 0.2, w: 6, h: 0.16, margin: 0 },
  )

  slide.addShape(pptx.ShapeType.line, {
    x: PPT_MARGIN_X,
    y: 0.36,
    w: 1.05,
    h: 0,
    line: { color: PPT_COLORS.navy, width: 1.25 },
  })

  slide.addText(opts.pageTitle, {
    x: PPT_MARGIN_X,
    y: 0.4,
    w: 9,
    h: 0.28,
    fontSize: 13,
    bold: true,
    color: PPT_COLORS.title,
    margin: 0,
  })
}

export function addFinssentialsPptxFooter(
  slide: PptxGenJS.Slide,
  pptx: PptxGenJS,
  footerRight: string,
) {
  slide.addShape(pptx.ShapeType.line, {
    x: PPT_MARGIN_X,
    y: PPT_FOOTER_LINE_Y,
    w: PPT_SLIDE_W - PPT_MARGIN_X * 2,
    h: 0,
    line: { color: PPT_COLORS.border, width: 0.75 },
  })

  slide.addText(PROJECT_NAME, {
    x: PPT_MARGIN_X,
    y: PPT_FOOTER_TEXT_Y,
    w: 1.2,
    h: 0.18,
    fontSize: 6.5,
    bold: true,
    color: PPT_COLORS.navy,
    margin: 0,
  })

  slide.addText(footerRight, {
    x: 1.35,
    y: PPT_FOOTER_TEXT_Y,
    w: PPT_SLIDE_W - 1.55,
    h: 0.2,
    fontSize: 5.5,
    color: PPT_COLORS.muted,
    align: 'right',
    margin: 0,
  })
}

export function addPptxSectionLabel(
  slide: PptxGenJS.Slide,
  text: string,
  x: number,
  y: number,
  w: number,
  colorHex = '475569',
) {
  slide.addText(text, {
    x,
    y,
    w,
    h: PPT_SECTION_LABEL_H,
    fontSize: PPT_NARRATIVE_FONT_PT,
    bold: true,
    color: colorHex.replace('#', ''),
    margin: 0,
  })
}
