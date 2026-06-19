import type { SalesMetricBridgeResponse } from '../../../lib/api'
import { BRAND } from './salesChartTheme'

export type WaterfallEntry = {
  name: string
  base: number
  value: number
  raw: number
  color: string
  isTotal: boolean
  segmentKey?: string
}

const C_TOTAL = BRAND.navy
const C_POS = '#16A34A'
const C_NEG = '#DC2626'

export function buildMetricBridgeEntries(data: SalesMetricBridgeResponse): WaterfallEntry[] {
  const entries: WaterfallEntry[] = []

  data.periods.forEach((label, i) => {
    const total = data.period_totals[i] ?? 0
    const display = data.period_display?.[i]
    entries.push({
      name: label,
      base: 0,
      value: total,
      raw: total,
      color: C_TOTAL,
      isTotal: true,
      segmentKey: display,
    })

    if (i < data.bridges.length) {
      const bridge = data.bridges[i]
      let cursor = total

      bridge.segments.forEach(seg => {
        const v = seg.delta_keur
        if (!v) return
        const color = v >= 0 ? C_POS : C_NEG
        entries.push({
          name: seg.name,
          base: v >= 0 ? cursor : cursor + v,
          value: Math.abs(v),
          raw: v,
          color,
          isTotal: false,
          segmentKey: seg.name,
        })
        cursor += v
      })
    }
  })

  return entries
}
