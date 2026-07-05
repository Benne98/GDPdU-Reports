/**
 * findingsModel.ts — Overview v2 (reporting-v2 / port 5177 only).
 * Defines the OverviewFinding type and confirmed deep-link route constants.
 * See docs/overview-v2-redesign-plan.md §1 for the confirmed route list.
 */

/** A single actionable finding surfaced in the findings feed or alert rail. */
export interface OverviewFinding {
  /** Stable identifier (e.g. "ebit-yoy-decline", "dso-trend-up"). */
  id: string;
  /** Visual priority tier. */
  severity: 'info' | 'warning' | 'critical';
  /** One human-readable sentence. Should fit on a single line. */
  text: string;
  /** React Router route the user navigates to for the full detail view. */
  route: OverviewV2Route;
}

/**
 * Confirmed deep-link routes for Overview v2 findings.
 * Only routes that already exist in App.tsx are listed here.
 * NOTE: there is NO standalone /sales route — customer/supplier findings
 *       deep-link to /working-capital or /income-statement per plan §1.
 */
export type OverviewV2Route =
  | '/income-statement'
  | '/balance-sheet'
  | '/working-capital'
  | '/cash-flow'
  | '/account-statement'
  | '/anomaly-detection';

/** All confirmed deep-link routes as a readonly array (useful for runtime validation). */
export const OVERVIEW_V2_ROUTES: readonly OverviewV2Route[] = [
  '/income-statement',
  '/balance-sheet',
  '/working-capital',
  '/cash-flow',
  '/account-statement',
  '/anomaly-detection',
] as const;
