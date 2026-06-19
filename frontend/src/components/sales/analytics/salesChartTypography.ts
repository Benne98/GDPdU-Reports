/** Chart text sizes aligned with subheaders (`text-xs` ≈ 12px). */

export const CHART_TICK_FONT_SIZE = 12
export const CHART_AXIS_TITLE_FONT_SIZE = 12
export const CHART_DATA_LABEL_FONT_SIZE = 12
export const CHART_DIM_CAPTION_FONT_SIZE = 12

export const CHART_TICK_STYLE = { fontSize: CHART_TICK_FONT_SIZE, fill: '#475569', fontWeight: 500 }
export const CHART_AXIS_TICK_STYLE = { fontSize: CHART_TICK_FONT_SIZE, fill: '#94A3B8', fontWeight: 500 }
export const CHART_AXIS_TITLE_STYLE = { fill: '#64748B', fontWeight: 500 }

/** SVG scatter labels — must set fontFamily; SVG text does not inherit from body. */
export const CHART_SCATTER_LABEL_FONT_FAMILY = "'Inter', system-ui, -apple-system, sans-serif"

export const CHART_SCATTER_LABEL_STYLE = {
  fontFamily: CHART_SCATTER_LABEL_FONT_FAMILY,
  fontSize: 10,
  fontWeight: 500,
  fill: '#475569',
  letterSpacing: '0.01em',
} as const

export const CHART_SCATTER_LABEL_EMPHASIS_STYLE = {
  ...CHART_SCATTER_LABEL_STYLE,
  fontWeight: 600,
  fill: '#1E3A5F',
} as const
