import type { BudgetPosition } from './gdpduApi';

export interface PlanningBaseContext {
  lineCode: string;
  activePlanYear: number;
  positionsByCodeAllYears: Record<number, Record<string, BudgetPosition>>;
  /** Last non-zero historical annual (EUR) from granularity view, keyed by line_code. */
  historyAnnualByLine?: Record<string, number>;
}

function isNonZero(n: number | undefined | null): n is number {
  return n !== undefined && n !== null && Math.abs(n) > 0.01;
}

/** Position-level planning base: plan-year actual → suggestion → prior FY tree → hist column. */
export function resolvePlanningBaseAnnual(
  amount: number,
  pos: BudgetPosition | undefined,
  ctx: PlanningBaseContext,
): number {
  if (isNonZero(amount)) return amount;
  if (isNonZero(pos?.suggestion?.annual)) return pos!.suggestion!.annual;
  const synthetic = (pos as { synthetic_annual?: number } | undefined)?.synthetic_annual;
  if (isNonZero(synthetic)) return synthetic!;

  const years = Object.keys(ctx.positionsByCodeAllYears)
    .map(Number)
    .filter((y) => y < ctx.activePlanYear)
    .sort((a, b) => b - a);
  for (const yr of years) {
    const prior = ctx.positionsByCodeAllYears[yr]?.[ctx.lineCode]?.annual;
    if (isNonZero(prior)) return prior;
  }

  const hist = ctx.historyAnnualByLine?.[ctx.lineCode];
  if (isNonZero(hist)) return hist;
  return 0;
}

/** L4 child planning base from prior-year child profile when plan-year child annual is 0. */
export function resolveL4PlanningBaseAnnual(
  childAnnual: number,
  level4: string,
  pos: BudgetPosition | undefined,
  ctx: PlanningBaseContext,
): number {
  if (isNonZero(childAnnual)) return childAnnual;

  const years = Object.keys(ctx.positionsByCodeAllYears)
    .map(Number)
    .filter((y) => y < ctx.activePlanYear)
    .sort((a, b) => b - a);
  for (const yr of years) {
    const priorPos = ctx.positionsByCodeAllYears[yr]?.[ctx.lineCode];
    const priorChild = priorPos?.children?.find((c) => c.level_4 === level4);
    if (isNonZero(priorChild?.annual)) return priorChild!.annual;
  }

  // Scale from position-level hist when no L4 history exists
  const posBase = resolvePlanningBaseAnnual(pos?.annual ?? 0, pos, ctx);
  const childCount = pos?.children?.length ?? 0;
  if (isNonZero(posBase) && childCount > 0) return posBase / childCount;
  return 0;
}

/** Partner planning base from prior-year partner profile when plan-year annual is 0. */
export function resolvePartnerPlanningBaseAnnual(
  partnerAnnual: number,
  partnerId: string,
  _pos: BudgetPosition | undefined,
  ctx: PlanningBaseContext,
): number {
  if (isNonZero(partnerAnnual)) return partnerAnnual;

  const years = Object.keys(ctx.positionsByCodeAllYears)
    .map(Number)
    .filter((y) => y < ctx.activePlanYear)
    .sort((a, b) => b - a);
  for (const yr of years) {
    const priorPartners = ctx.positionsByCodeAllYears[yr]?.[ctx.lineCode]?.partners ?? [];
    const prior = priorPartners.find((p) => p.partner_id === partnerId);
    if (isNonZero(prior?.annual)) return prior!.annual;
  }
  return 0;
}

/** Build line_code → last historical annual (EUR) from granularity rows. */
export function historyAnnualByLineFromRows(
  rows: Array<{ kind: string; line_code: string; values?: (number | null | undefined)[] }>,
): Record<string, number> {
  const out: Record<string, number> = {};
  for (const row of rows) {
    if (row.kind !== 'line') continue;
    const vals = row.values ?? [];
    for (let i = vals.length - 1; i >= 0; i--) {
      const v = vals[i];
      if (v !== undefined && v !== null && Math.abs(v) > 0.01) {
        out[row.line_code] = v;
        break;
      }
    }
  }
  return out;
}
