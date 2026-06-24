export type ScoreBand = 'low' | 'medium' | 'high'

/** X-axis tick interval for consistent month labeling (~every 4th month).
 *  interval=0 means every tick shown; interval=N means show every (N+1)th. */
export function xAxisTickProps(seriesLength: number): { interval: number } {
  if (seriesLength <= 12) return { interval: 2 }  // every 3rd (0,3,6,9,12)
  if (seriesLength <= 24) return { interval: 3 }  // every 4th
  return { interval: Math.ceil(seriesLength / 8) - 1 }
}

/** Map band to a highlight color */
export function scoreColor(band: ScoreBand | string): string {
  if (band === 'high')   return '#DC2626'
  if (band === 'medium') return '#D97706'
  return '#2563EB'
}

/** Map band to background color (semi-transparent) */
export function scoreBg(band: ScoreBand | string): string {
  if (band === 'high')   return 'rgba(220,38,38,0.10)'
  if (band === 'medium') return 'rgba(217,119,6,0.10)'
  return 'rgba(37,99,235,0.08)'
}

/** Human-friendly band label */
export function scoreLabel(band: ScoreBand | string): string {
  if (band === 'high')   return 'High'
  if (band === 'medium') return 'Medium'
  return 'Low'
}

/** Tooltip explanation for the signal score */
export const SIGNAL_SCORE_INFO =
  'Signal score 0–100: how unusual this position looks compared to its own history. ' +
  'Higher = more unusual. Not a probability — just a relative indicator.'
