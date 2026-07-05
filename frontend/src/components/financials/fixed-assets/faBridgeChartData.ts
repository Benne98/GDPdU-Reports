import type { FixedAssetBridgeColumn, FixedAssetBridgeMovementGroup } from '../../../lib/api'

/**
 * Backend-emitted leg structure (additive fields on movements columns/groups).
 * Each leg represents ONE movement segment in the floating waterfall, carrying
 * the running cursor so bars can float on each other.
 */
type BridgeLeg = {
  name: string
  bar_type: 'add' | 'disp' | 'da'
  base: number    // running cursor BEFORE this movement
  delta: number   // signed change (positive = addition, negative = disposal / D&A)
  value: number   // magnitude = abs(delta)
}

type BridgeColumnExtended = FixedAssetBridgeColumn & {
  base?: number
  legs?: BridgeLeg[]
}

type BridgeGroupExtended = FixedAssetBridgeMovementGroup & {
  legs?: BridgeLeg[]
}

/**
 * One rendered bar in the floating waterfall chart.
 *
 * Recharts stacked-bar trick:
 *   Bar dataKey="spacer" fill="transparent"  → invisible offset that lifts the bar
 *   Bar dataKey="amount"                     → the visible colored segment
 *
 * For a POSITIVE delta (addition):
 *   spacer = base,         amount = delta     → bar climbs from base upward
 *
 * For a NEGATIVE delta (disposal / D&A):
 *   spacer = base + delta, amount = |delta|   → bar appears to drop; it renders
 *   from (base + delta) UP to base, which visually descends from the running total
 *
 * For total (NBV open / close):
 *   spacer = 0,            amount = value     → grounded pillar
 */
export type BridgeBarPoint = {
  id: string
  xKey: string
  tickLabel: string
  displayLabel: string
  spacer: number    // transparent vertical offset (always >= 0 for valid NBV)
  amount: number    // visible bar height (always >= 0)
  raw: number       // signed value for tooltip / label text
  barType: 'total' | 'add' | 'disp' | 'da'
  groupLabel?: string
  legName?: string
}

export type BridgeGroupBox = {
  id: string
  startIdx: number
  endIdx: number
  label: string
}

function movementTickLabel(name: string): string {
  if (name === 'Additions') return 'Add.'
  if (name === 'Disposals') return 'Disp.'
  if (name === 'D&A') return 'D&A'
  return name
}

const MOVEMENTS: ReadonlyArray<{
  key: 'additions' | 'disposals' | 'depreciation'
  barType: Exclude<BridgeBarPoint['barType'], 'total'>
  short: string
  sign: 1 | -1
}> = [
  { key: 'additions',    barType: 'add',  short: 'Add.',  sign:  1 },
  { key: 'disposals',    barType: 'disp', short: 'Disp.', sign: -1 },
  { key: 'depreciation', barType: 'da',   short: 'D&A',   sign: -1 },
]

function legToPoint(
  leg: BridgeLeg,
  id: string,
  groupLabel?: string,
): BridgeBarPoint {
  const spacer = leg.delta >= 0 ? leg.base : leg.base + leg.delta
  return {
    id,
    xKey: id,
    tickLabel: movementTickLabel(leg.name),
    displayLabel: leg.name,
    spacer: Math.max(0, spacer),
    amount: leg.value,
    raw: leg.delta,
    barType: leg.bar_type,
    groupLabel,
    legName: leg.name,
  }
}

interface AmountSource {
  additions?: number
  disposals?: number
  depreciation?: number
}

/**
 * Fallback for when the backend has not yet emitted `legs`.
 * Threads a running cursor through Add./Disp./D&A in that order.
 */
function fallbackPoints(
  year: number,
  src: AmountSource,
  cursor: number,
  _yearLabel: string | number,
  groupKey?: string,
  groupLabel?: string,
): { pts: BridgeBarPoint[]; newCursor: number } {
  const pts: BridgeBarPoint[] = []
  let c = cursor
  for (const m of MOVEMENTS) {
    const magnitude = src[m.key] ?? 0
    if (Math.abs(magnitude) < 0.0001) continue
    const delta = m.sign * Math.abs(magnitude)
    const spacer = delta >= 0 ? c : c + delta
    const id = groupKey
      ? `${year}-${groupKey}-${m.barType}`
      : `${year}-${m.barType}`
    pts.push({
      id,
      xKey: id,
      tickLabel: m.short,
      displayLabel: m.short,
      spacer: Math.max(0, spacer),
      amount: Math.abs(delta),
      raw: delta,
      barType: m.barType,
      groupLabel,
    })
    c += delta
  }
  return { pts, newCursor: c }
}

export function flattenBridgeColumns(columns: FixedAssetBridgeColumn[]): {
  points: BridgeBarPoint[]
  boxes: BridgeGroupBox[]
} {
  const points: BridgeBarPoint[] = []
  const boxes: BridgeGroupBox[] = []
  let runningTotal = 0

  for (const rawCol of columns) {
    const col = rawCol as BridgeColumnExtended
    const yearLabel = col.label ?? `Dec${String(col.year).slice(-2)}A`

    if (col.kind === 'total') {
      const value = col.value ?? 0
      runningTotal = value
      points.push({
        id: `total-${col.year}`,
        xKey: `total-${col.year}`,
        tickLabel: yearLabel,
        displayLabel: yearLabel,
        spacer: 0,
        amount: Math.max(0, value),
        raw: value,
        barType: 'total',
      })
      continue
    }

    if (col.kind === 'movements') {
      const legs = col.legs
      if (legs?.length) {
        for (const leg of legs) {
          points.push(
            legToPoint(leg, `${col.year}-leg-${leg.name}`),
          )
        }
        runningTotal = legs.reduce((acc, l) => acc + l.delta, runningTotal)
      } else {
        const { pts, newCursor } = fallbackPoints(col.year, col, runningTotal, yearLabel)
        for (const p of pts) points.push(p)
        runningTotal = newCursor
      }
      continue
    }

    if (col.kind === 'movements_by_dimension') {
      for (const rawGroup of col.groups ?? []) {
        const group = rawGroup as BridgeGroupExtended
        const startIdx = points.length

        if (group.legs?.length) {
          for (const leg of group.legs) {
            points.push(
              legToPoint(
                leg,
                `${col.year}-${group.key}-leg-${leg.name}`,
                group.label,
              ),
            )
          }
          runningTotal = group.legs.reduce((acc, l) => acc + l.delta, runningTotal)
        } else {
          const { pts, newCursor } = fallbackPoints(
            col.year,
            group,
            runningTotal,
            yearLabel,
            group.key,
            group.label,
          )
          for (const p of pts) points.push(p)
          runningTotal = newCursor
        }

        const endIdx = points.length - 1
        if (endIdx >= startIdx) {
          boxes.push({
            id: `${col.year}-${group.key}`,
            startIdx,
            endIdx,
            label: group.label,
          })
        }
      }
    }
  }

  return { points, boxes }
}

export function bridgeChartWidth(pointCount: number, boxCount: number): number {
  const base = Math.max(640, pointCount * 52)
  return boxCount > 0 ? Math.max(base, pointCount * 58) : base
}
