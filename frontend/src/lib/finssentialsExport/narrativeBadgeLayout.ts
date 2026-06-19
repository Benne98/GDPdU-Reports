/**
 * Shared Key Drivers / narrative layout (PDF + PPT) — badge on bullet marker.
 */
import type { PlNarrativeBullet } from '../../components/financials/pl-two-view/plNarrativeEngine'
import { bulletDisplayText } from '../../components/financials/pl-two-view/plNarrativeEngine'

const EMU_PER_IN = 914400
const PX_PER_IN = 96

/** Example PPTX: ellipse cx/cy = 90000 EMU → 0.25 cm diameter. */
export const PPT_BADGE_EMU = 90000
export const PPT_BADGE_DIAMETER_IN = PPT_BADGE_EMU / EMU_PER_IN
export const PPT_BADGE_RADIUS_IN = PPT_BADGE_DIAMETER_IN / 2
export const PPT_BADGE_INDEX_FONT_PT = 5
export const PPT_BADGE_FONT_FACE = 'Segoe UI'
/** Badge left edge ≈ textbox x + 0.021″ (example deck). */
export const PPT_BADGE_X_OFFSET_IN = 0.021

/** pptxgenjs DEF_BULLET_MARGIN — hanging bullet at textbox left edge. */
export const PPT_BULLET_MARGIN_PT = 27
export const PPT_BULLET_SPACE_AFTER_PT = 4

export const NARRATIVE_BADGE_RADIUS_MM = (PPT_BADGE_DIAMETER_IN * 25.4) / 2
export const NARRATIVE_BADGE_GAP_MM = 1.5
export const NARRATIVE_BULLET_GAP_MM = 1.2
export const NARRATIVE_INTRO_PARA_GAP_MM = 1.5
export const NARRATIVE_LINE_H_MM = 3.6
export const NARRATIVE_LINE_SPACING_MULT = 1.05
export const NARRATIVE_INTRO_PARA_GAP_PT = 2

/** Table image badge px so 0.25 cm on slide after fit-to PPT_TABLE_W. */
export function exportTableBadgeDiameterPx(
  captureWidthPx: number,
  tableFitWidthIn: number,
): number {
  return Math.max(
    11,
    Math.round((PPT_BADGE_DIAMETER_IN / tableFitWidthIn) * captureWidthPx),
  )
}

export function exportTableBadgeFontPx(badgeDiameterPx: number): number {
  const nominalPx = PPT_BADGE_DIAMETER_IN * PX_PER_IN
  return Math.max(
    6,
    Math.round(PPT_BADGE_INDEX_FONT_PT * (PX_PER_IN / 72) * (badgeDiameterPx / nominalPx)),
  )
}

export function splitIntroParagraphs(intro: string): string[] {
  return intro
    .split(/\n+/)
    .map(s => s.trim())
    .filter(Boolean)
}

export function estimateNarrativeBodyLines(body: string, charsPerLine = 72): number {
  return Math.max(1, Math.ceil(body.length / charsPerLine))
}

export type PptxNarrativeMeasure = {
  /** Distance (in) from textbox top to centre of each bullet glyph. */
  bulletCenterYsIn: number[]
  /** Bottom edge (in) of full content block. */
  contentBottomYIn: number
}

export type NarrativeSlideChunk = {
  intro: string | null | undefined
  bullets: PlNarrativeBullet[]
  isContinuation: boolean
}

function appendIntro(host: HTMLElement, intro: string | null | undefined) {
  const introParas = intro ? splitIntroParagraphs(intro) : []
  for (let i = 0; i < introParas.length; i++) {
    const p = document.createElement('p')
    p.style.cssText = 'margin:0;padding:0;'
    if (i < introParas.length - 1) {
      p.style.marginBottom = `${NARRATIVE_INTRO_PARA_GAP_PT}pt`
    }
    p.textContent = introParas[i]
    host.appendChild(p)
  }
  if (introParas.length > 0) {
    const gap = document.createElement('div')
    gap.style.height = '6pt'
    host.appendChild(gap)
  }
}

function appendBulletParagraph(
  host: HTMLElement,
  bullet: PlNarrativeBullet,
  isLast: boolean,
): HTMLSpanElement {
  const p = document.createElement('p')
  p.style.cssText = [
    'margin:0',
    `padding:0 0 ${isLast ? 0 : PPT_BULLET_SPACE_AFTER_PT}pt 0`,
    `padding-left:${PPT_BULLET_MARGIN_PT}pt`,
    `text-indent:-${PPT_BULLET_MARGIN_PT}pt`,
  ].join(';')

  const marker = document.createElement('span')
  marker.setAttribute('data-bullet-marker', '1')
  marker.style.cssText = [
    'display:inline-block',
    `width:${PPT_BULLET_MARGIN_PT}pt`,
    `margin-left:-${PPT_BULLET_MARGIN_PT}pt`,
    'text-align:center',
    'vertical-align:top',
    'color:#475569',
    'font-size:inherit',
    'line-height:inherit',
  ].join(';')
  marker.textContent = '\u2022'

  const body = document.createElement('span')
  body.textContent = bulletDisplayText(bullet)

  p.appendChild(marker)
  p.appendChild(body)
  host.appendChild(p)
  return marker
}

function createMeasureHost(widthIn: number, fontPt: number): HTMLDivElement {
  const host = document.createElement('div')
  host.style.cssText = [
    'position:fixed',
    'left:-12000px',
    'top:0',
    'visibility:hidden',
    `width:${widthIn}in`,
    'box-sizing:border-box',
    `font:${fontPt}pt/${NARRATIVE_LINE_SPACING_MULT} "${PPT_BADGE_FONT_FACE}",Calibri,sans-serif`,
    'color:#475569',
    'margin:0',
    'padding:0',
  ].join(';')
  return host
}

/** DOM layout matching pptxgen bullet paragraphs (body without duplicated •). */
export async function measurePptxNarrativeLayout(
  intro: string | null | undefined,
  bullets: PlNarrativeBullet[],
  widthIn: number,
  fontPt: number,
): Promise<PptxNarrativeMeasure> {
  const bulletSlice = bullets.slice(0, 30)
  if (typeof document === 'undefined') {
    return measurePptxNarrativeLayoutFallback(intro, bulletSlice, widthIn, fontPt)
  }

  await document.fonts.ready

  const host = createMeasureHost(widthIn, fontPt)
  appendIntro(host, intro)

  const markers: HTMLSpanElement[] = []
  for (let i = 0; i < bulletSlice.length; i++) {
    markers.push(appendBulletParagraph(host, bulletSlice[i], i === bulletSlice.length - 1))
  }

  document.body.appendChild(host)
  const hostTop = host.getBoundingClientRect().top
  const hostRect = host.getBoundingClientRect()

  const bulletCenterYsIn = markers.map(marker => {
    const r = marker.getBoundingClientRect()
    return (r.top - hostTop + r.height / 2) / PX_PER_IN
  })

  const contentBottomYIn = (hostRect.bottom - hostTop) / PX_PER_IN
  document.body.removeChild(host)

  return { bulletCenterYsIn, contentBottomYIn }
}

function measurePptxNarrativeLayoutFallback(
  intro: string | null | undefined,
  bullets: PlNarrativeBullet[],
  widthIn: number,
  fontPt: number,
): PptxNarrativeMeasure {
  const lineHIn = (fontPt / 72) * NARRATIVE_LINE_SPACING_MULT
  let yIn = 0
  const introParas = intro ? splitIntroParagraphs(intro) : []
  for (let i = 0; i < introParas.length; i++) {
    const lines = estimateNarrativeBodyLines(introParas[i], Math.floor(widthIn * 14))
    yIn += lines * lineHIn
    if (i < introParas.length - 1) yIn += NARRATIVE_INTRO_PARA_GAP_PT / 72
  }
  if (introParas.length > 0) yIn += 6 / 72

  const bulletCenterYsIn: number[] = []
  for (let i = 0; i < bullets.length; i++) {
    const body = bulletDisplayText(bullets[i])
    const lines = estimateNarrativeBodyLines(body, Math.floor((widthIn * 72 - PPT_BULLET_MARGIN_PT) / 5))
    bulletCenterYsIn.push(yIn + lineHIn / 2)
    yIn += Math.max(lineHIn, lines * lineHIn) + PPT_BULLET_SPACE_AFTER_PT / 72
  }

  return { bulletCenterYsIn, contentBottomYIn: yIn }
}

/** Split narrative into slide chunks that fit below Key drivers within footer line. */
export async function planNarrativeSlideChunks(
  intro: string | null | undefined,
  bullets: PlNarrativeBullet[],
  widthIn: number,
  fontPt: number,
  maxHeightIn: number,
): Promise<NarrativeSlideChunk[]> {
  const all = bullets.slice(0, 30)
  const chunks: NarrativeSlideChunk[] = []
  let idx = 0

  while (idx < all.length || (chunks.length === 0 && intro)) {
    const isContinuation = chunks.length > 0
    const introPart = isContinuation ? null : intro

    let fit = 0
    for (let tryCount = 1; tryCount <= all.length - idx; tryCount++) {
      const slice = all.slice(idx, idx + tryCount)
      const { contentBottomYIn } = await measurePptxNarrativeLayout(
        introPart,
        slice,
        widthIn,
        fontPt,
      )
      if (contentBottomYIn <= maxHeightIn + 0.02) fit = tryCount
      else break
    }

    if (fit === 0) {
      if (idx < all.length) fit = 1
      else if (!introPart) break
    }

    chunks.push({
      intro: introPart ?? null,
      bullets: all.slice(idx, idx + fit),
      isContinuation,
    })
    idx += fit
    if (fit === 0) break
  }

  if (chunks.length === 0) {
    chunks.push({ intro: intro ?? null, bullets: [], isContinuation: false })
  }

  return chunks
}
