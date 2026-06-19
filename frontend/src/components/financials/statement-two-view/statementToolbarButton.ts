import type { CSSProperties } from 'react'

/** Shared toolbar button styles for statement cards */
export const STATEMENT_TOOLBAR_BTN =
  'inline-flex items-center justify-center gap-1.5 h-7 rounded-lg text-xs font-medium transition-colors shrink-0'

export const STATEMENT_TOOLBAR_BTN_STYLE: CSSProperties = {
  background: '#F4F6F9',
  color: '#1E3A5F',
  border: '1px solid #E2E8F0',
}

export const STATEMENT_TOOLBAR_ICON_BTN = `${STATEMENT_TOOLBAR_BTN} w-7 px-0`

/** @deprecated Use STATEMENT_* from statement-two-view */
export const PL_TOOLBAR_BTN = STATEMENT_TOOLBAR_BTN
export const PL_TOOLBAR_BTN_STYLE = STATEMENT_TOOLBAR_BTN_STYLE
export const PL_TOOLBAR_ICON_BTN = STATEMENT_TOOLBAR_ICON_BTN
