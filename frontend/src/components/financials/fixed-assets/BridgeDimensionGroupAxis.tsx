import type { BridgeBarPoint, BridgeGroupBox } from './faBridgeChartData'
import { CHART_TICK_STYLE } from '../../sales/analytics/salesChartTypography'

type BandScale = ((value: string) => number) & { bandwidth?: () => number }

type AxisMapEntry = { scale?: BandScale }

function getBandScale(xAxisMap: Record<string, AxisMapEntry> | undefined): BandScale | null {
  if (!xAxisMap) return null
  const scale = Object.values(xAxisMap)[0]?.scale
  return typeof scale === 'function' ? scale : null
}

function truncateLabel(label: string, max = 22): string {
  const t = label.trim()
  if (t.length <= max) return t
  return `${t.slice(0, max - 1)}…`
}

type Props = {
  width?: number
  height?: number
  offset?: { top: number; right: number; bottom: number; left: number }
  xAxisMap?: Record<string, AxisMapEntry>
  points: BridgeBarPoint[]
  groups: BridgeGroupBox[]
  /** Height reserved for leg tick labels (Add./Disp./D&A) above the group row. */
  legAxisHeight?: number
}

/** Lower x-axis bracket labels grouping movement bars by dimension (entity / segment). */
export default function BridgeDimensionGroupAxis({
  width = 0,
  height = 0,
  offset,
  xAxisMap,
  points,
  groups,
  legAxisHeight = 34,
}: Props) {
  const scale = getBandScale(xAxisMap)
  if (!scale || !groups.length || !width || !height) return null

  const bandwidth = scale.bandwidth?.() ?? 0
  const plotBottom = height - (offset?.bottom ?? 0)
  const bracketY = plotBottom + legAxisHeight + 6
  const labelY = plotBottom + legAxisHeight + 22
  const tickH = 5

  return (
    <g className="bridge-dimension-axis" pointerEvents="none">
      {groups.map((group) => {
        const startPt = points[group.startIdx]
        const endPt = points[group.endIdx]
        if (!startPt || !endPt) return null

        const xStart = scale(startPt.xKey) + bandwidth / 2
        const xEnd = scale(endPt.xKey) + bandwidth / 2
        if (!Number.isFinite(xStart) || !Number.isFinite(xEnd)) return null

        const xMid = (xStart + xEnd) / 2
        const label = truncateLabel(group.label)

        return (
          <g key={group.id}>
            <line
              x1={xStart}
              y1={bracketY}
              x2={xEnd}
              y2={bracketY}
              stroke="#CBD5E1"
              strokeWidth={1}
            />
            <line x1={xStart} y1={bracketY} x2={xStart} y2={bracketY + tickH} stroke="#CBD5E1" strokeWidth={1} />
            <line x1={xEnd} y1={bracketY} x2={xEnd} y2={bracketY + tickH} stroke="#CBD5E1" strokeWidth={1} />
            <text
              x={xMid}
              y={labelY}
              textAnchor="middle"
              fill={CHART_TICK_STYLE.fill}
              fontSize={10}
              fontWeight={600}
            >
              <title>{group.label}</title>
              {label}
            </text>
          </g>
        )
      })}
    </g>
  )
}
