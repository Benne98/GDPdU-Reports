/** Table left, narrative / line-detail right — matches Report View. */
export const FIN_REPORT_SPLIT_GRID =
  'grid grid-cols-1 lg:grid-cols-[minmax(0,1.28fr)_minmax(0,0.82fr)] gap-x-5 gap-y-0 items-start'

/** Entity breakdown group report — table slightly wider than narrative column. */
export const FIN_ENTITY_CONSOL_REPORT_SPLIT_GRID =
  'grid grid-cols-1 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] gap-x-4 gap-y-0 items-start'

/** Value cells in report mini-table (compact mode). */
export const FIN_TABLE_VALUE_FONT = '0.8125rem' /* 13px */

export const FIN_TABLE_CELL_CLASS = 'px-1.5 py-1'

export const FIN_TABLE_CELL_DENSE_CLASS = 'px-0.5 py-0.5'

/**
 * Group-report mini-table fixed column widths.
 * Used together with `table-fixed` + `<colgroup>` so every statement table
 * has identical column proportions regardless of how many plan/OPlan columns
 * are present. Extra horizontal space is absorbed by the label column.
 */
export const REPORT_LABEL_COL_MIN_PX = 320  // "EURk"/position column width → sets the "#" column x-position across ALL aligned report tables (group, top consolidated, per-entity)
export const REPORT_MARKER_COL_PX = 20       // "#" comment-marker column
export const REPORT_PERIOD_COL_PX = 104      // each period/value column (year, month, week)
export const REPORT_DELTA_COL_PX = 140       // monthly delta/variance columns — wide enough for "Δ Jun25 − May25"-style headers; the width-less spacer col absorbs it so the table stays the same total width
export const REPORT_DELTA_COL_WEEK_PX = 148  // weekly delta column width (tunable); wider than REPORT_DELTA_COL_PX so weekly Δ headers like "Δ CW27 − CW26" fit
export const REPORT_CUM_COL_PX = 120         // cumulative columns (MTD / YTD) — wider than a plain period col so "Year to date" / "MTD Jun25" headers fit; the width-less spacer col absorbs it so the table stays the same total width

/**
 * Column kinds that render a Δ/variance header and therefore use
 * `REPORT_DELTA_COL_PX` in the mini-table colgroup.
 * Must stay in sync with the `isDelta` guard in plTableRowRenderer.tsx.
 */
export const REPORT_DELTA_COL_KINDS = new Set([
  'mom',
  'yoy',
  'ytd_delta',
  'ytd_vs_plan',
  'plan_vs_actual',
  'month_mom',
  'month_yoy',
  'month_delta',
])

/** Cumulative-to-date columns whose header ("Year to date" / "Month to date") needs the wider `REPORT_CUM_COL_PX`. */
export const REPORT_CUM_COL_KINDS = new Set(['ytd', 'mtd'])
