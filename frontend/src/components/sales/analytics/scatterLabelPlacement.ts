/** Non-overlapping label placement for scatter charts (SVG pixel coordinates). */

export type ScatterLabelPoint = {
  segment: string
  gross_margin_pct: number
  gross_profit_keur: number
  gross_sales_keur: number
}

export type PlacedScatterLabel = {
  segment: string
  displayText: string
  pointX: number
  pointY: number
  labelX: number
  labelY: number
  anchor: 'start' | 'middle' | 'end'
  width: number
}

const CHAR_W = 6.8
const LABEL_H = 15
const PAD = 4
const MAX_DISPLAY_LEN = 24

type Rect = { left: number; top: number; right: number; bottom: number }

function truncate(name: string): string {
  if (name.length <= MAX_DISPLAY_LEN) return name
  return `${name.slice(0, MAX_DISPLAY_LEN - 1)}…`
}

function textWidth(text: string): number {
  return text.length * CHAR_W + 6
}

function labelRect(x: number, y: number, w: number, anchor: 'start' | 'middle' | 'end'): Rect {
  const left = anchor === 'middle' ? x - w / 2 : anchor === 'end' ? x - w : x
  return { left, top: y - LABEL_H + 2, right: left + w, bottom: y + 2 }
}

function rectsOverlap(a: Rect, b: Rect, pad = PAD): boolean {
  return !(
    a.right + pad < b.left
    || b.right + pad < a.left
    || a.bottom + pad < b.top
    || b.bottom + pad < a.top
  )
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

function bubbleRadius(z: number, zMin: number, zMax: number): number {
  const t = zMax <= zMin ? 0.5 : (z - zMin) / (zMax - zMin)
  return 7 + t * 13
}

function labelOverlapsBubble(rect: Rect, cx: number, cy: number, r: number): boolean {
  const closestX = clamp(cx, rect.left, rect.right)
  const closestY = clamp(cy, rect.top, rect.bottom)
  const dx = cx - closestX
  const dy = cy - closestY
  return dx * dx + dy * dy < (r + 3) ** 2
}

function inBounds(rect: Rect, bounds: Rect): boolean {
  return (
    rect.left >= bounds.left
    && rect.right <= bounds.right
    && rect.top >= bounds.top
    && rect.bottom <= bounds.bottom
  )
}

function candidatePositions(
  px: number,
  py: number,
  r: number,
  w: number,
): Array<{ x: number; y: number; anchor: 'start' | 'middle' | 'end' }> {
  const g = r + 6
  const positions: Array<{ x: number; y: number; anchor: 'start' | 'middle' | 'end' }> = [
    { x: px, y: py - g, anchor: 'middle' },
    { x: px, y: py + g + LABEL_H - 2, anchor: 'middle' },
    { x: px + g + w / 2, y: py + 3, anchor: 'start' },
    { x: px - g, y: py + 3, anchor: 'end' },
    { x: px + g * 0.7, y: py - g * 0.7, anchor: 'start' },
    { x: px - g * 0.7, y: py - g * 0.7, anchor: 'end' },
    { x: px + g * 0.7, y: py + g * 0.7 + LABEL_H - 2, anchor: 'start' },
    { x: px - g * 0.7, y: py + g * 0.7 + LABEL_H - 2, anchor: 'end' },
  ]
  for (let deg = 0; deg < 360; deg += 25) {
    const rad = (deg * Math.PI) / 180
    const dist = g + 10
    positions.push({
      x: px + Math.cos(rad) * dist,
      y: py + Math.sin(rad) * dist + (Math.sin(rad) > 0 ? LABEL_H - 2 : 0),
      anchor: Math.cos(rad) > 0.25 ? 'start' : Math.cos(rad) < -0.25 ? 'end' : 'middle',
    })
  }
  return positions
}

export function placeScatterLabels(
  points: ScatterLabelPoint[],
  xScale: (v: number) => number,
  yScale: (v: number) => number,
  chartWidth: number,
  chartHeight: number,
  margin: { top: number; right: number; bottom: number; left: number },
): PlacedScatterLabel[] {
  const bounds: Rect = {
    left: margin.left + 2,
    top: margin.top + 2,
    right: chartWidth - margin.right - 2,
    bottom: chartHeight - margin.bottom - 2,
  }

  const zVals = points.map(p => p.gross_sales_keur)
  const zMin = Math.min(...zVals)
  const zMax = Math.max(...zVals)

  const sorted = [...points].sort((a, b) => b.gross_sales_keur - a.gross_sales_keur)
  const placedRects: Rect[] = []
  const bubbleMeta: Array<{ cx: number; cy: number; r: number }> = []
  const result: PlacedScatterLabel[] = []

  for (const p of sorted) {
    const px = xScale(p.gross_margin_pct)
    const py = yScale(p.gross_profit_keur)
    const r = bubbleRadius(p.gross_sales_keur, zMin, zMax)
    bubbleMeta.push({ cx: px, cy: py, r })

    const displayText = truncate(p.segment)
    const w = textWidth(displayText)
    const candidates = candidatePositions(px, py, r, w)

    let chosen: PlacedScatterLabel | null = null
    for (const c of candidates) {
      const rect = labelRect(c.x, c.y, w, c.anchor)
      if (!inBounds(rect, bounds)) continue
      if (placedRects.some(pr => rectsOverlap(pr, rect))) continue
      if (bubbleMeta.some((b, i) => {
        const isOwn = bubbleMeta.length - 1 === i
        if (isOwn) return false
        return labelOverlapsBubble(rect, b.cx, b.cy, b.r)
      })) continue
      chosen = {
        segment: p.segment,
        displayText,
        pointX: px,
        pointY: py,
        labelX: c.x,
        labelY: c.y,
        anchor: c.anchor,
        width: w,
      }
      placedRects.push(rect)
      break
    }

    if (!chosen) {
      for (let dist = r + 14; dist < 100; dist += 6) {
        for (let deg = 0; deg < 360; deg += 20) {
          const rad = (deg * Math.PI) / 180
          const lx = px + Math.cos(rad) * dist
          const ly = py + Math.sin(rad) * dist
          const anchor: 'start' | 'middle' | 'end' =
            Math.cos(rad) > 0.3 ? 'start' : Math.cos(rad) < -0.3 ? 'end' : 'middle'
          const rect = labelRect(lx, ly, w, anchor)
          if (!inBounds(rect, bounds)) continue
          if (placedRects.some(pr => rectsOverlap(pr, rect))) continue
          if (bubbleMeta.some((b, i) => {
            if (i === bubbleMeta.length - 1) return false
            return labelOverlapsBubble(rect, b.cx, b.cy, b.r)
          })) continue
          chosen = {
            segment: p.segment,
            displayText,
            pointX: px,
            pointY: py,
            labelX: lx,
            labelY: ly,
            anchor,
            width: w,
          }
          placedRects.push(rect)
          break
        }
        if (chosen) break
      }
    }

    if (chosen) {
      result.push(chosen)
    } else {
      result.push({
        segment: p.segment,
        displayText,
        pointX: px,
        pointY: py,
        labelX: clamp(px, bounds.left + w / 2, bounds.right - w / 2),
        labelY: clamp(py - r - 8, bounds.top + LABEL_H, bounds.bottom),
        anchor: 'middle',
        width: w,
      })
    }
  }

  return nudgeApart(result, bounds)
}

function placedToRect(l: PlacedScatterLabel): Rect {
  return labelRect(l.labelX, l.labelY, l.width, l.anchor)
}

/** Push overlapping labels apart while staying in bounds. */
function nudgeApart(labels: PlacedScatterLabel[], bounds: Rect): PlacedScatterLabel[] {
  const out = labels.map(l => ({ ...l }))
  for (let iter = 0; iter < 40; iter++) {
    let moved = false
    for (let i = 0; i < out.length; i++) {
      for (let j = i + 1; j < out.length; j++) {
        const a = placedToRect(out[i])
        const b = placedToRect(out[j])
        if (!rectsOverlap(a, b, 1)) continue
        const acx = (a.left + a.right) / 2
        const acy = (a.top + a.bottom) / 2
        const bcx = (b.left + b.right) / 2
        const bcy = (b.top + b.bottom) / 2
        let dx = acx - bcx
        let dy = acy - bcy
        if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) {
          dx = 1
          dy = -1
        }
        const len = Math.hypot(dx, dy) || 1
        dx = (dx / len) * 3
        dy = (dy / len) * 3
        out[i].labelX += dx
        out[i].labelY += dy
        out[j].labelX -= dx
        out[j].labelY -= dy
        moved = true
      }
    }
    for (const l of out) {
      const w = l.width
      const padL = l.anchor === 'middle' ? w / 2 : l.anchor === 'end' ? w : 0
      const padR = l.anchor === 'middle' ? w / 2 : l.anchor === 'end' ? 0 : w
      l.labelX = clamp(l.labelX, bounds.left + padL, bounds.right - padR)
      l.labelY = clamp(l.labelY, bounds.top + LABEL_H, bounds.bottom)
    }
    if (!moved) break
  }
  return out
}
