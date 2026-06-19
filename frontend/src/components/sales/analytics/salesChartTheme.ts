/**
 * Sales analytics chart theme — aligned with Finssentials brand tokens (index.css).
 * Primary #1E3A5F, surface #F4F6F9 / #F8FAFC, borders #E2E8F0.
 */

export const BRAND = {
  navy: '#1E3A5F',
  navyMid: '#2E5280',
  navyLight: '#2E6AAD',
  steel: '#4A6FA5',
  steelLight: '#6B8CB3',
  teal: '#14B8A6',
  tealDeep: '#0D9488',
  slate: '#64748B',
  slateMuted: '#94A3B8',
  border: '#E2E8F0',
  borderLight: '#F1F5F9',
  surface: '#F4F6F9',
  surfaceRaised: '#F8FAFC',
  text: '#111827',
  textSecondary: '#475569',
  textMuted: '#94A3B8',
} as const

/** Categorical series — navy-led, no rainbow; matches GeoTrend / Cockpit charts. */
export const CHART_SERIES_COLORS = [
  BRAND.navy,
  BRAND.navyLight,
  BRAND.teal,
  BRAND.steel,
  BRAND.tealDeep,
  BRAND.steelLight,
  BRAND.slate,
  BRAND.slateMuted,
] as const

export const CHART_OTHER_COLOR = BRAND.slateMuted

export const SALES_CHART_CARD_CLASS =
  'rounded-xl bg-white border overflow-hidden'
export const SALES_CHART_CARD_STYLE = {
  borderColor: BRAND.border,
  boxShadow: '0 1px 3px rgba(0, 0, 0, 0.04)',
} as const

/** Chart plot area — white card only, no nested gray panel. */
export const SALES_CHART_BODY_CLASS = 'px-2'

export const SALES_CHART_GRID_PROPS = {
  vertical: false,
  stroke: BRAND.borderLight,
  strokeDasharray: '4 8',
  strokeOpacity: 1,
} as const

export const SALES_CHART_CURSOR = {
  fill: 'rgba(30, 58, 95, 0.06)',
  stroke: 'rgba(30, 58, 95, 0.18)',
  strokeWidth: 1,
}

export function seriesColor(index: number): string {
  return CHART_SERIES_COLORS[index % CHART_SERIES_COLORS.length]
}

/** Luminance-aware label on filled segments (composition %). */
export function segmentLabelColor(fill: string): string {
  const hex = fill.replace('#', '')
  if (hex.length !== 6) return '#FFFFFF'
  const r = parseInt(hex.slice(0, 2), 16)
  const g = parseInt(hex.slice(2, 4), 16)
  const b = parseInt(hex.slice(4, 6), 16)
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
  return luminance > 0.55 ? BRAND.navy : '#FFFFFF'
}
