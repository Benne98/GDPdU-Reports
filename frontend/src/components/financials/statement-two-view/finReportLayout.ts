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
export const REPORT_LABEL_COL_MIN_PX = 260  // "EURk"/position column width → sets the "#" column x-position across all aligned tables (matches the group report's previous label width)
export const REPORT_MARKER_COL_PX = 20       // "#" comment-marker column
export const REPORT_PERIOD_COL_PX = 104      // each period/value column (year, month, week)
