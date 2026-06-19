/**
 * Unified Finssentials PowerPoint slide export (report + table-only layouts).
 */
import PptxGenJS from 'pptxgenjs'
import { createElement } from 'react'
import {
  KEY_DRIVERS_HEADING,
  PL_SECTION_HEADING_COLOR,
} from '../../../components/financials/pl-two-view/plReportSectionHeadings'
import {
  PPT_TABLE_FONT_PX,
  captureReactNodeAsImage,
  fitImageInBox,
} from '../../../components/financials/pl-two-view/plExportTableCapture'
import FinssentialsExportTableCaptureRoot from '../FinssentialsExportTableCaptureRoot'
import type { ExportFlatRow } from '../flattenTreeForExport'
import {
  PPT_CONTENT_BOTTOM,
  PPT_CONTENT_TOP,
  PPT_MARGIN_X,
  PPT_NARRATIVE_W,
  PPT_NARRATIVE_X,
  PPT_SECTION_LABEL_Y,
  PPT_SLIDE_W,
  PPT_TABLE_W,
  PPT_TABLE_X,
  addFinssentialsPptxFooter,
  addFinssentialsPptxHeader,
  addPptxSectionLabel,
  PPT_NARRATIVE_FONT_PT,
} from '../reportSlideLayout'
import { PROJECT_NAME } from '../designStyles'
import type { PlNarrativeBullet } from '../../../components/financials/pl-two-view/plNarrativeEngine'
import { addPptxNarrativeWithBadges } from './pptxNarrativeWithBadges'
import {
  exportTableBadgeDiameterPx,
  planNarrativeSlideChunks,
} from '../narrativeBadgeLayout'

export type FinssentialsPptxTablePayload = {
  headers: string[]
  columnKinds: string[]
  visibleRows: ExportFlatRow[]
  commentMarkersByLineCode?: import('../../../components/financials/statement-two-view/reportCommentMarkers').ReportCommentMarkerMap
}

export type FinssentialsPptxConfig = {
  layout: 'report' | 'tableOnly'
  fileName: string
  deckTitle?: string
  header: {
    breadcrumbCurrent: string
    pageTitle: string
    breadcrumbParent?: string
  }
  tableHeading: string
  footerRight: string
  table: FinssentialsPptxTablePayload
  narrative?: {
    intro?: string | null
    bullets: PlNarrativeBullet[]
  }
}

function exportTableWidthPx(
  columnCount: number,
  hasCommentCol: boolean,
): number {
  const cols = columnCount + (hasCommentCol ? 1 : 0) + 1
  return Math.min(1400, Math.max(520, cols * 72 + 140))
}

async function captureTableImage(
  table: FinssentialsPptxTablePayload,
): Promise<{ dataUrl: string; pixelWidth: number; pixelHeight: number }> {
  const hasComment = Boolean(
    table.commentMarkersByLineCode && Object.keys(table.commentMarkersByLineCode).length,
  )
  const valueColCount = Math.max(0, table.headers.length - 1)
  const widthPx = exportTableWidthPx(valueColCount, hasComment)
  const commentBadgeDiameterPx = exportTableBadgeDiameterPx(widthPx, PPT_TABLE_W)

  return captureReactNodeAsImage(
    createElement(FinssentialsExportTableCaptureRoot, {
      headers: table.headers,
      columnKinds: table.columnKinds,
      rows: table.visibleRows,
      commentMarkersByLineCode: table.commentMarkersByLineCode,
      pixelFontSize: PPT_TABLE_FONT_PX,
      widthPx,
      commentBadgeDiameterPx,
    }),
    { scale: 2 },
  )
}

function addTableImage(
  slide: PptxGenJS.Slide,
  image: { dataUrl: string; pixelWidth: number; pixelHeight: number },
  x: number,
  y: number,
  boxW: number,
  boxH: number,
) {
  const { w, h } = fitImageInBox(image.pixelWidth, image.pixelHeight, boxW, boxH)
  slide.addImage({ data: image.dataUrl, x, y, w, h })
}

function keyDriversHeading(isContinuation: boolean): string {
  return isContinuation ? `${KEY_DRIVERS_HEADING} (cont'd)` : KEY_DRIVERS_HEADING
}

async function addReportLayoutSlide(
  pptx: PptxGenJS,
  cfg: Omit<FinssentialsPptxConfig, 'fileName' | 'deckTitle'>,
  tableImage: { dataUrl: string; pixelWidth: number; pixelHeight: number },
  narrativeChunk: {
    intro: string | null | undefined
    bullets: PlNarrativeBullet[]
    isContinuation: boolean
  } | null,
) {
  const slide = pptx.addSlide()
  addFinssentialsPptxHeader(slide, pptx, cfg.header)
  addFinssentialsPptxFooter(slide, pptx, cfg.footerRight)

  const contentH = PPT_CONTENT_BOTTOM - PPT_CONTENT_TOP

  addPptxSectionLabel(
    slide,
    cfg.tableHeading,
    PPT_TABLE_X,
    PPT_SECTION_LABEL_Y,
    PPT_TABLE_W,
    PL_SECTION_HEADING_COLOR,
  )
  addPptxSectionLabel(
    slide,
    keyDriversHeading(narrativeChunk?.isContinuation ?? false),
    PPT_NARRATIVE_X,
    PPT_SECTION_LABEL_Y,
    PPT_NARRATIVE_W,
    PL_SECTION_HEADING_COLOR,
  )
  addTableImage(slide, tableImage, PPT_TABLE_X, PPT_CONTENT_TOP, PPT_TABLE_W, contentH)

  if (narrativeChunk && (narrativeChunk.bullets.length > 0 || narrativeChunk.intro)) {
    await addPptxNarrativeWithBadges(
      slide,
      pptx,
      narrativeChunk.intro,
      narrativeChunk.bullets,
      { x: PPT_NARRATIVE_X, w: PPT_NARRATIVE_W, startY: PPT_CONTENT_TOP },
    )
  }
}

/** Build slide(s) from config and download/write file. */
export async function exportFinssentialsPptx(cfg: FinssentialsPptxConfig): Promise<void> {
  const pptx = new PptxGenJS()
  pptx.author = PROJECT_NAME
  pptx.title = cfg.deckTitle ?? cfg.header.pageTitle

  const tableImage = await captureTableImage(cfg.table)
  const contentH = PPT_CONTENT_BOTTOM - PPT_CONTENT_TOP

  if (cfg.layout === 'report') {
    const narrative = cfg.narrative
    const chunks =
      narrative && (narrative.bullets.length > 0 || narrative.intro)
        ? await planNarrativeSlideChunks(
            narrative.intro ?? null,
            narrative.bullets,
            PPT_NARRATIVE_W,
            PPT_NARRATIVE_FONT_PT,
            contentH,
          )
        : [{ intro: null, bullets: [], isContinuation: false }]

    for (const chunk of chunks) {
      await addReportLayoutSlide(pptx, cfg, tableImage, {
        intro: chunk.intro,
        bullets: chunk.bullets,
        isContinuation: chunk.isContinuation,
      })
    }
  } else {
    const slide = pptx.addSlide()
    addFinssentialsPptxHeader(slide, pptx, cfg.header)
    addFinssentialsPptxFooter(slide, pptx, cfg.footerRight)
    addPptxSectionLabel(
      slide,
      cfg.tableHeading,
      PPT_MARGIN_X,
      PPT_SECTION_LABEL_Y,
      PPT_SLIDE_W - PPT_MARGIN_X * 2,
      PL_SECTION_HEADING_COLOR,
    )
    addTableImage(
      slide,
      tableImage,
      PPT_MARGIN_X,
      PPT_CONTENT_TOP,
      PPT_SLIDE_W - PPT_MARGIN_X * 2,
      contentH,
    )
  }

  await pptx.writeFile({ fileName: cfg.fileName })
}

/** Append report slide(s) to an existing deck (management report). */
export async function addFinssentialsPptxSlide(
  pptx: PptxGenJS,
  cfg: Omit<FinssentialsPptxConfig, 'fileName' | 'deckTitle'>,
): Promise<void> {
  const tableImage = await captureTableImage(cfg.table)
  const contentH = PPT_CONTENT_BOTTOM - PPT_CONTENT_TOP

  if (cfg.layout === 'report') {
    const narrative = cfg.narrative
    const chunks =
      narrative && (narrative.bullets.length > 0 || narrative.intro)
        ? await planNarrativeSlideChunks(
            narrative.intro ?? null,
            narrative.bullets,
            PPT_NARRATIVE_W,
            PPT_NARRATIVE_FONT_PT,
            contentH,
          )
        : [{ intro: null, bullets: [], isContinuation: false }]

    for (const chunk of chunks) {
      await addReportLayoutSlide(pptx, cfg, tableImage, {
        intro: chunk.intro,
        bullets: chunk.bullets,
        isContinuation: chunk.isContinuation,
      })
    }
  } else {
    const slide = pptx.addSlide()
    addFinssentialsPptxHeader(slide, pptx, cfg.header)
    addFinssentialsPptxFooter(slide, pptx, cfg.footerRight)
    addPptxSectionLabel(
      slide,
      cfg.tableHeading,
      PPT_MARGIN_X,
      PPT_SECTION_LABEL_Y,
      PPT_SLIDE_W - PPT_MARGIN_X * 2,
      PL_SECTION_HEADING_COLOR,
    )
    addTableImage(
      slide,
      tableImage,
      PPT_MARGIN_X,
      PPT_CONTENT_TOP,
      PPT_SLIDE_W - PPT_MARGIN_X * 2,
      contentH,
    )
  }
}
