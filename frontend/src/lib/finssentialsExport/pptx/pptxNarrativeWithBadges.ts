import type PptxGenJS from 'pptxgenjs'
import type { PlNarrativeBullet } from '../../../components/financials/pl-two-view/plNarrativeEngine'
import { bulletDisplayText } from '../../../components/financials/pl-two-view/plNarrativeEngine'
import {
  PPT_BADGE_DIAMETER_IN,
  PPT_BADGE_FONT_FACE,
  PPT_BADGE_INDEX_FONT_PT,
  PPT_BADGE_X_OFFSET_IN,
  PPT_BULLET_MARGIN_PT,
  PPT_BULLET_SPACE_AFTER_PT,
  NARRATIVE_INTRO_PARA_GAP_PT,
  NARRATIVE_LINE_SPACING_MULT,
  measurePptxNarrativeLayout,
  splitIntroParagraphs,
} from '../narrativeBadgeLayout'
import {
  PPT_COLORS,
  PPT_CONTENT_BOTTOM,
  PPT_FOOTER_LINE_Y,
  PPT_NARRATIVE_FONT_PT,
} from '../reportSlideLayout'

export type PptxNarrativeOpts = {
  x: number
  w: number
  startY: number
  maxY?: number
}

/** Native pptxgen bullet (U+2022) — same indent as measure layout. */
const PPTX_BULLET = {
  type: 'bullet' as const,
  characterCode: '2022',
  indent: PPT_BULLET_MARGIN_PT,
}

function addBadgeOverlay(
  slide: PptxGenJS.Slide,
  pptx: PptxGenJS,
  badgeX: number,
  badgeY: number,
  index: number,
) {
  const pad = 0.006
  slide.addShape(pptx.ShapeType.ellipse, {
    x: badgeX - pad,
    y: badgeY - pad,
    w: PPT_BADGE_DIAMETER_IN + pad * 2,
    h: PPT_BADGE_DIAMETER_IN + pad * 2,
    fill: { color: 'FFFFFF' },
    line: { color: 'FFFFFF', width: 0 },
  })
  slide.addShape(pptx.ShapeType.ellipse, {
    x: badgeX,
    y: badgeY,
    w: PPT_BADGE_DIAMETER_IN,
    h: PPT_BADGE_DIAMETER_IN,
    fill: { color: PPT_COLORS.navy },
  })
  slide.addText(String(index), {
    x: badgeX,
    y: badgeY,
    w: PPT_BADGE_DIAMETER_IN,
    h: PPT_BADGE_DIAMETER_IN,
    fontSize: PPT_BADGE_INDEX_FONT_PT,
    fontFace: PPT_BADGE_FONT_FACE,
    bold: true,
    color: 'FFFFFF',
    align: 'center',
    valign: 'middle',
    margin: 0,
  })
}

/**
 * One text box (intro + bullets), navy badges centred on list markers.
 */
export async function addPptxNarrativeWithBadges(
  slide: PptxGenJS.Slide,
  pptx: PptxGenJS,
  intro: string | null | undefined,
  bullets: PlNarrativeBullet[],
  opts: PptxNarrativeOpts,
) {
  const maxY = opts.maxY ?? PPT_CONTENT_BOTTOM
  const boxH = Math.max(0.5, (maxY ?? PPT_FOOTER_LINE_Y) - opts.startY)
  const badgeX = opts.x + PPT_BADGE_X_OFFSET_IN

  const runs: { text: string; options?: PptxGenJS.TextPropsOptions }[] = []

  const introParas = intro ? splitIntroParagraphs(intro) : []
  for (let i = 0; i < introParas.length; i++) {
    runs.push({
      text: introParas[i],
      options: {
        breakLine: true,
        bullet: false,
        paraSpaceAfter: i < introParas.length - 1 ? NARRATIVE_INTRO_PARA_GAP_PT : 6,
        paraSpaceBefore: 0,
      },
    })
  }

  for (let i = 0; i < bullets.length; i++) {
    runs.push({
      text: bulletDisplayText(bullets[i]),
      options: {
        bullet: PPTX_BULLET,
        paraSpaceAfter: i < bullets.length - 1 ? PPT_BULLET_SPACE_AFTER_PT : 0,
        paraSpaceBefore: 0,
        breakLine: true,
      },
    })
  }

  if (runs.length === 0) return

  slide.addText(runs, {
    x: opts.x,
    y: opts.startY,
    w: opts.w,
    h: boxH,
    fontSize: PPT_NARRATIVE_FONT_PT,
    fontFace: PPT_BADGE_FONT_FACE,
    color: PPT_COLORS.slate,
    valign: 'top',
    margin: 0,
    lineSpacingMultiple: NARRATIVE_LINE_SPACING_MULT,
  })

  const { bulletCenterYsIn } = await measurePptxNarrativeLayout(
    intro,
    bullets,
    opts.w,
    PPT_NARRATIVE_FONT_PT,
  )

  for (let i = 0; i < bullets.length && i < bulletCenterYsIn.length; i++) {
    const centerY = opts.startY + bulletCenterYsIn[i]
    const badgeY = centerY - PPT_BADGE_DIAMETER_IN / 2
    if (badgeY + PPT_BADGE_DIAMETER_IN > maxY + 0.01) continue
    addBadgeOverlay(slide, pptx, badgeX, badgeY, bullets[i].index)
  }
}
