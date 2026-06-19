/** Modern muted palette — current (cool) → overdue (warm rose). */
export const AGING_BUCKET_PIE_COLORS: Record<string, string> = {
  not_yet_due: '#5B7CFA',
  overdue_1_30: '#7B93F7',
  overdue_31_60: '#9BAFF5',
  overdue_61_90: '#F0A875',
  overdue_91_180: '#EF7B7B',
  overdue_over_180: '#E85D8A',
}

export function bucketPieColor(band: string, index: number): string {
  return AGING_BUCKET_PIE_COLORS[band] ?? ['#94A3B8', '#CBD5E1', '#E2E8F0'][index % 3]
}
