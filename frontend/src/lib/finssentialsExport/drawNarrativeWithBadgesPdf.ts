import type jsPDF from 'jspdf'
import type { PlNarrativeBullet } from '../../components/financials/pl-two-view/plNarrativeEngine'
import { bulletDisplayText } from '../../components/financials/pl-two-view/plNarrativeEngine'
import {
  NARRATIVE_BULLET_GAP_MM,
  NARRATIVE_INTRO_PARA_GAP_MM,
  NARRATIVE_LINE_H_MM,
  PPT_BADGE_DIAMETER_IN,
  PPT_BADGE_INDEX_FONT_PT,
  PPT_BADGE_RADIUS_IN,
  PPT_BADGE_X_OFFSET_IN,
  splitIntroParagraphs,
} from './narrativeBadgeLayout'

const MM_PER_IN = 25.4
const BADGE_D_MM = PPT_BADGE_DIAMETER_IN * MM_PER_IN
const BADGE_R_MM = PPT_BADGE_RADIUS_IN * MM_PER_IN
const BADGE_X_MM = PPT_BADGE_X_OFFSET_IN * MM_PER_IN

function pdfSafeText(s: string): string {
  if (!s) return s
  const el = document.createElement('textarea')
  el.innerHTML = s
  return el.value
    .replace(/[\u2013\u2014]/g, '-')
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201c\u201d]/g, '"')
    .replace(/€/g, 'EUR ')
}

export type PdfNarrativeLayout = {
  xMm: number
  wMm: number
  startYMm: number
  maxYMm: number
  fontBodyPt: number
  colorBody: string
  colorNavy: string
}

function hexRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '')
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ]
}

function setTextHex(pdf: jsPDF, hex: string) {
  const [r, g, b] = hexRgb(hex)
  pdf.setTextColor(r, g, b)
}

/** Key drivers: intro + bulleted lines; navy badge over bullet (0.25 cm). */
export function drawNarrativeWithBadgesPdf(
  pdf: jsPDF,
  intro: string | null | undefined,
  bullets: PlNarrativeBullet[],
  layout: PdfNarrativeLayout,
) {
  let y = layout.startYMm
  pdf.setFont('helvetica', 'normal')
  pdf.setFontSize(layout.fontBodyPt)
  setTextHex(pdf, layout.colorBody)

  if (intro) {
    const paras = splitIntroParagraphs(intro)
    for (let i = 0; i < paras.length; i++) {
      const lines = pdf.splitTextToSize(pdfSafeText(paras[i]), layout.wMm) as string[]
      pdf.text(lines, layout.xMm, y, { baseline: 'top' })
      y += lines.length * NARRATIVE_LINE_H_MM
      if (i < paras.length - 1) y += NARRATIVE_INTRO_PARA_GAP_MM
    }
    y += 1.5
  }

  const textX = layout.xMm + (27 / 72) * MM_PER_IN
  const textW = layout.wMm - (textX - layout.xMm)
  const [nr, ng, nb] = hexRgb(layout.colorNavy)
  const badgeCx = layout.xMm + BADGE_X_MM + BADGE_R_MM

  for (const b of bullets.slice(0, 10)) {
    if (y + BADGE_D_MM > layout.maxYMm) break

    const lines = pdf.splitTextToSize(pdfSafeText(bulletDisplayText(b)), textW) as string[]
    const blockH = Math.max(BADGE_D_MM, lines.length * NARRATIVE_LINE_H_MM)
    const badgeCy = y + BADGE_D_MM / 2

    setTextHex(pdf, layout.colorBody)
    pdf.text('\u2022', layout.xMm + BADGE_X_MM, y + 1.2, { baseline: 'top' })

    pdf.setFillColor(255, 255, 255)
    pdf.circle(badgeCx, badgeCy, BADGE_R_MM + 0.12, 'F')
    pdf.setFillColor(nr, ng, nb)
    pdf.circle(badgeCx, badgeCy, BADGE_R_MM, 'F')
    pdf.setFont('helvetica', 'bold')
    pdf.setFontSize(PPT_BADGE_INDEX_FONT_PT)
    pdf.setTextColor(255, 255, 255)
    const idx = String(b.index)
    const idxW = pdf.getTextWidth(idx)
    pdf.text(idx, badgeCx - idxW / 2, badgeCy + 0.3)

    pdf.setFont('helvetica', 'normal')
    pdf.setFontSize(layout.fontBodyPt)
    setTextHex(pdf, layout.colorBody)
    pdf.text(lines, textX, y + 0.5, { baseline: 'top' })

    y += blockH + NARRATIVE_BULLET_GAP_MM
  }
}
