import type { BudgetPosition } from './gdpduApi';
import type { PositionGranularity } from './budgetChatFlow';
import type { PositionOverride } from '../components/budget/BudgetGrid';
import type { PartnerBandId } from './budgetPartnerGroups';
import { groupPartnersByRank } from './budgetPartnerGroups';
import type { PlanningBaseContext } from './budgetPlanningBase';
import {
  resolveL4PlanningBaseAnnual,
  resolvePartnerPlanningBaseAnnual,
  resolvePlanningBaseAnnual,
} from './budgetPlanningBase';

export interface RateEscalation {
  fromMonth: number;
  ratePct: number;
}

export interface SubRowRateState {
  growthPct?: number;
  rateLocked?: boolean;
  rateEscalations?: RateEscalation[];
}

function seasonalise(annualEur: number, pos: BudgetPosition | undefined): number[] {
  const suggMonths = pos?.suggestion?.months;
  if (suggMonths && suggMonths.length === 12) {
    const suggSum = suggMonths.reduce((s, v) => s + v, 0);
    if (Math.abs(suggSum) > 0.01) {
      return suggMonths.map((m) => (m / suggSum) * annualEur);
    }
  }
  return Array(12).fill(annualEur / 12);
}

export function annualFromGrowth(baseAnnual: number, growthPct: number): number {
  return baseAnnual * (1 + growthPct / 100);
}

export function monthsFromGrowth(
  baseAnnual: number,
  growthPct: number,
  pos: BudgetPosition | undefined,
  escalations?: RateEscalation[],
): number[] {
  const annual = annualFromGrowth(baseAnnual, growthPct);
  let months = seasonalise(annual, pos);
  if (!escalations?.length) return months;

  for (const esc of [...escalations].sort((a, b) => a.fromMonth - b.fromMonth)) {
    const escAnnual = annualFromGrowth(baseAnnual, esc.ratePct);
    const escMonths = seasonalise(escAnnual, pos);
    const fromIdx = Math.max(0, Math.min(11, esc.fromMonth - 1));
    for (let i = fromIdx; i < 12; i++) months[i] = escMonths[i];
  }
  return months;
}

export function resolveChildAnnual(
  baseAnnual: number,
  state: SubRowRateState | undefined,
): number {
  if (state?.growthPct === undefined) return baseAnnual;
  return annualFromGrowth(baseAnnual, state.growthPct);
}

export function resolveChildMonths(
  baseAnnual: number,
  state: SubRowRateState | undefined,
  pos: BudgetPosition | undefined,
  explicitMonths?: number[],
): number[] {
  if (explicitMonths?.length === 12) return explicitMonths;
  const pct = state?.growthPct ?? 0;
  return monthsFromGrowth(baseAnnual, pct, pos, state?.rateEscalations);
}

export function resolveSandboxBlankAnnual(
  granularity: PositionGranularity,
  pos: BudgetPosition | undefined,
  ov: PositionOverride,
  partnerRateLevel: 'group' | 'partner' = 'partner',
  baseCtx?: PlanningBaseContext,
): number {
  if (!pos) return 0;
  const lineCode = baseCtx?.lineCode ?? pos.line_code;

  if (granularity === 'L4') {
    return (pos.children ?? []).reduce((sum, child) => {
      const childState = ov.l4?.[child.level_4];
      if (childState?.annual !== undefined) return sum + childState.annual;
      const base = baseCtx
        ? resolveL4PlanningBaseAnnual(child.annual, child.level_4, pos, { ...baseCtx, lineCode })
        : child.annual;
      return sum + resolveChildAnnual(base, childState);
    }, 0);
  }

  if (granularity === 'customers' || granularity === 'suppliers') {
    const partners = pos.partners ?? [];
    if (partnerRateLevel === 'group') {
      const groups = groupPartnersByRank(partners);
      return groups.reduce((sum, group) => {
        const groupState = ov.partnerGroups?.[group.id];
        const groupPct = groupState?.growthPct ?? 0;
        const groupSum = group.partners.reduce(
          (s, p) => {
            const pState = ov.partners?.[p.partner_id];
            if (pState?.annual !== undefined) return s + pState.annual;
            const base = baseCtx
              ? resolvePartnerPlanningBaseAnnual(p.annual, p.partner_id, pos, { ...baseCtx, lineCode })
              : p.annual;
            return s + annualFromGrowth(base, groupPct);
          },
          0,
        );
        return sum + groupSum;
      }, 0);
    }
    return partners.reduce((sum, p) => {
      const pState = ov.partners?.[p.partner_id];
      if (pState?.annual !== undefined) return sum + pState.annual;
      const base = baseCtx
        ? resolvePartnerPlanningBaseAnnual(p.annual, p.partner_id, pos, { ...baseCtx, lineCode })
        : p.annual;
      return sum + resolveChildAnnual(base, pState);
    }, 0);
  }

  if (ov.annual !== undefined) return ov.annual;
  const base = baseCtx
    ? resolvePlanningBaseAnnual(pos.annual, pos, { ...baseCtx, lineCode })
    : pos.annual;
  if (ov.growthPct !== undefined) return annualFromGrowth(base, ov.growthPct);
  return base;
}

export function resolveSandboxBlankMonths(
  granularity: PositionGranularity,
  pos: BudgetPosition | undefined,
  ov: PositionOverride,
  partnerRateLevel: 'group' | 'partner' = 'partner',
  baseCtx?: PlanningBaseContext,
): number[] {
  if (!pos) return Array(12).fill(0);
  const lineCode = baseCtx?.lineCode ?? pos.line_code;

  if (granularity === 'L4') {
    const months = Array(12).fill(0);
    for (const child of pos.children ?? []) {
      const childState = ov.l4?.[child.level_4];
      const base = baseCtx
        ? resolveL4PlanningBaseAnnual(child.annual, child.level_4, pos, { ...baseCtx, lineCode })
        : child.annual;
      const childMonths = resolveChildMonths(
        base,
        childState,
        pos,
        childState?.months,
      );
      for (let i = 0; i < 12; i++) months[i] += childMonths[i];
    }
    return months;
  }

  if (granularity === 'customers' || granularity === 'suppliers') {
    const months = Array(12).fill(0);
    const partners = pos.partners ?? [];
    if (partnerRateLevel === 'group') {
      for (const group of groupPartnersByRank(partners)) {
        const groupState = ov.partnerGroups?.[group.id as PartnerBandId];
        const groupPct = groupState?.growthPct ?? 0;
        for (const p of group.partners) {
          const pState = ov.partners?.[p.partner_id];
          const base = baseCtx
            ? resolvePartnerPlanningBaseAnnual(p.annual, p.partner_id, pos, { ...baseCtx, lineCode })
            : p.annual;
          const pMonths = pState?.months?.length === 12
            ? pState.months
            : monthsFromGrowth(base, groupPct, pos, groupState?.rateEscalations);
          for (let i = 0; i < 12; i++) months[i] += pMonths[i];
        }
      }
      return months;
    }
    for (const p of partners) {
      const pState = ov.partners?.[p.partner_id];
      const base = baseCtx
        ? resolvePartnerPlanningBaseAnnual(p.annual, p.partner_id, pos, { ...baseCtx, lineCode })
        : p.annual;
      const pMonths = resolveChildMonths(base, pState, pos, pState?.months);
      for (let i = 0; i < 12; i++) months[i] += pMonths[i];
    }
    return months;
  }

  if (ov.months?.length === 12) return ov.months;
  const base = baseCtx
    ? resolvePlanningBaseAnnual(pos.annual, pos, { ...baseCtx, lineCode })
    : pos.annual;
  const pct = ov.growthPct ?? 0;
  return monthsFromGrowth(base, pct, pos, ov.rateEscalations);
}

/** Rebalance child months so they sum to a new parent month total. */
export function rebalanceChildrenToParentMonth(
  parentTotal: number,
  childValues: number[],
): number[] {
  const sum = childValues.reduce((s, v) => s + v, 0);
  if (Math.abs(sum) < 0.01) {
    const even = parentTotal / childValues.length;
    return childValues.map(() => even);
  }
  const factor = parentTotal / sum;
  return childValues.map((v) => v * factor);
}
