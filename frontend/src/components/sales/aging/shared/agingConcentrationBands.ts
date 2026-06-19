import type { AgingConcentrationSegment } from '../../../../lib/api'

/** Display order and chart colours for non-overlapping rank bands. */
export const AGING_CONCENTRATION_BANDS: {
  band: string
  color: string
}[] = [
  { band: 'rank_1_5', color: '#1E3A5F' },
  { band: 'rank_6_10', color: '#2563EB' },
  { band: 'rank_11_20', color: '#60A5FA' },
  { band: 'rank_21_plus', color: '#94A3B8' },
]

export function concentrationBandColor(band: string): string {
  return AGING_CONCENTRATION_BANDS.find(b => b.band === band)?.color ?? '#64748B'
}

export function sortConcentrationSegments(segments: AgingConcentrationSegment[]): AgingConcentrationSegment[] {
  const order = AGING_CONCENTRATION_BANDS.map(b => b.band)
  return [...segments].sort((a, b) => order.indexOf(a.band) - order.indexOf(b.band))
}
