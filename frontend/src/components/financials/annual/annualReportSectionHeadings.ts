import type { ErFlowColLabels, ErSnapshotColLabels } from '../../../lib/api'
import { stripLegalForm } from '../../../lib/stripLegalForm'

export const KEY_DRIVERS_HEADING = 'Key drivers'

const FLOW_TITLES: Record<string, string> = {
  pl: 'Income statement',
  cf: 'Cash flow statement',
}

const SNAP_TITLES: Record<string, string> = {
  bs: 'Balance sheet',
  wc: 'Working capital',
}

export function buildAnnualFlowReportTableHeading(
  statement: 'pl' | 'cf',
  _lbl: ErFlowColLabels,
  opts?: { entityDisplayName?: string; fy2Label?: string; fy3Label?: string },
): string {
  const title = FLOW_TITLES[statement] ?? 'Statement'
  if (opts?.entityDisplayName?.trim()) {
    const name = stripLegalForm(opts.entityDisplayName.trim())
    return `${name}'s ${title}`
  }
  return `Consolidated ${title}`
}

export function buildAnnualSnapshotReportTableHeading(
  statement: 'bs' | 'wc',
  lbl: ErSnapshotColLabels,
  opts?: { entityDisplayName?: string },
): string {
  const title = SNAP_TITLES[statement] ?? 'Statement'
  const cm = lbl.cm ?? 'CM'
  const pm = lbl.cm_py ?? lbl.fy_py ?? 'prior period'
  if (opts?.entityDisplayName?.trim()) {
    const name = stripLegalForm(opts.entityDisplayName.trim())
    return `${name}'s ${title} — ${cm} vs ${pm}`
  }
  return `Consolidated ${title} — ${cm} compared to ${pm}`
}
