import type { FinancialStatementColLabels } from '../../../../lib/api'
import { plSectionHeadingStyle, PL_SECTION_HEADING_COLOR } from '../../pl-two-view/plReportSectionHeadings'

export { plSectionHeadingStyle, PL_SECTION_HEADING_COLOR }
export const KEY_DRIVERS_HEADING = 'Key drivers'

export function buildWcReportTableHeading(
  lbl: FinancialStatementColLabels,
  opts?: { entityDisplayName?: string },
): string {
  const cm = lbl.cm ?? 'CM'
  const pm = lbl.pm ?? 'prior month'
  if (opts?.entityDisplayName?.trim()) {
    return `${opts.entityDisplayName.trim()}'s Working capital — ${cm} vs ${pm}`
  }
  return `Consolidated Working capital — ${cm} compared to ${pm}`
}
