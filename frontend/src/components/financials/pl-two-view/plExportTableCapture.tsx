import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import html2canvas from 'html2canvas'
import type { ReactElement } from 'react'
import { captureScale } from './plExportPdfLayout'

/** ~6.5pt narrative text on slide → px for capture (96 DPI). */
export const PPT_NARRATIVE_FONT_PX = Math.round(6.5 * (96 / 72))

/** ~9pt table body — matches Excel export (FinssentialsExportTableFrame). */
export const PPT_TABLE_FONT_PX = Math.round(9 * (96 / 72))

export type CapturedTableImage = {
  dataUrl: string
  pixelWidth: number
  pixelHeight: number
  captureScale: number
}

export async function captureReactNodeAsImage(
  node: ReactElement,
  opts?: { scale?: number; waitMs?: number; rootSelector?: string },
): Promise<CapturedTableImage> {
  const scale = opts?.scale ?? captureScale()
  const waitMs = opts?.waitMs ?? 200
  const rootSelector = opts?.rootSelector ?? '[data-pl-export-table]'

  const host = document.createElement('div')
  host.setAttribute('data-pl-export-host', 'true')
  host.style.cssText = [
    'position:fixed',
    'left:-12000px',
    'top:0',
    'z-index:-1',
    'background:#ffffff',
    'padding:0',
    'margin:0',
  ].join(';')
  document.body.appendChild(host)

  const root = createRoot(host)
  try {
    flushSync(() => {
      root.render(node)
    })
    await document.fonts.ready
    await new Promise<void>(r => setTimeout(r, waitMs))

    const target = host.querySelector(rootSelector) as HTMLElement | null
    if (!target) throw new Error(`Export capture root not found: ${rootSelector}`)

    const canvas = await html2canvas(target, {
      scale,
      backgroundColor: '#ffffff',
      logging: false,
      useCORS: true,
      onclone: doc => {
        const el = doc.querySelector(rootSelector) as HTMLElement | null
        if (!el) return
        el.querySelectorAll('td, th, td span').forEach(node => {
          const h = node as HTMLElement
          h.style.fontSize = 'inherit'
        })
      },
    })

    return {
      dataUrl: canvas.toDataURL('image/png'),
      pixelWidth: canvas.width,
      pixelHeight: canvas.height,
      captureScale: scale,
    }
  } finally {
    root.unmount()
    document.body.removeChild(host)
  }
}

/** Fit image into box (inches or mm); returns w/h preserving aspect ratio. */
export function fitImageInBox(
  pixelWidth: number,
  pixelHeight: number,
  boxW: number,
  boxH: number,
): { w: number; h: number } {
  const aspect = pixelWidth / pixelHeight
  let w = boxW
  let h = w / aspect
  if (h > boxH) {
    h = boxH
    w = h * aspect
  }
  return { w, h }
}
