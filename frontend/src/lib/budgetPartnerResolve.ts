import type { BudgetPartnerRow, BudgetPosition } from './gdpduApi';

/** Partners for planning — plan year first, then fall back to latest year with data. */
export function resolvePositionPartners(
  pos: BudgetPosition | undefined,
  lineCode: string,
  positionsByCodeAllYears: Record<number, Record<string, BudgetPosition>>,
  activePlanYear: number,
): BudgetPartnerRow[] {
  if (pos?.partners && pos.partners.length > 0) return pos.partners;

  const years = Object.keys(positionsByCodeAllYears)
    .map(Number)
    .filter((y) => y !== activePlanYear)
    .sort((a, b) => b - a);

  for (const yr of years) {
    const fallback = positionsByCodeAllYears[yr]?.[lineCode]?.partners;
    if (fallback && fallback.length > 0) return fallback;
  }
  return [];
}

/** Fill missing partner lists from other fiscal years in the loaded tree. */
export function enrichPositionsByCode(
  positionsByCode: Record<string, BudgetPosition>,
  positionsByCodeAllYears: Record<number, Record<string, BudgetPosition>>,
  activePlanYear: number,
): Record<string, BudgetPosition> {
  const out = { ...positionsByCode };
  for (const [lineCode, pos] of Object.entries(out)) {
    if (!pos.is_partner_driven) continue;
    const partners = resolvePositionPartners(pos, lineCode, positionsByCodeAllYears, activePlanYear);
    if (partners.length > 0 && (pos.partners?.length ?? 0) === 0) {
      out[lineCode] = { ...pos, partners };
    }
  }
  return out;
}
