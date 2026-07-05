/**
 * BudgetMonthlyAdjustModal — sandbox blank: edit absolute monthly kEUR values
 * with hierarchy-aware sum reconciliation.
 */

import { useMemo, useState } from 'react';
import { X } from 'lucide-react';
import type { BudgetGranularityRow, BudgetGranularityPeriod, BudgetPosition } from '../../lib/gdpduApi';
import type { BudgetDraft, PositionGranularity, PositionKey } from '../../lib/budgetChatFlow';
import type { PositionOverride } from './BudgetGrid';
import { rebalanceChildrenToParentMonth, resolveSandboxBlankMonths } from '../../lib/budgetBlankRollup';
import { groupPartnersByRank } from '../../lib/budgetPartnerGroups';

function fmtK(eur: number): string {
  const k = eur / 1000;
  const abs = Math.abs(k);
  const s = abs.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  return eur < 0 ? `(${s})` : s;
}

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export interface BudgetMonthlyAdjustModalProps {
  open: boolean;
  onClose: () => void;
  granularityRows: BudgetGranularityRow[];
  granularityPeriods: BudgetGranularityPeriod[];
  positionsByCode: Record<string, BudgetPosition>;
  statement: 'PL' | 'BS';
  granularityByPosition: Record<PositionKey, PositionGranularity>;
  draft: BudgetDraft;
  overrides: Record<string, PositionOverride>;
  onOverride: (lineCode: string, override: PositionOverride) => void;
  activePlanYear: number;
}

export default function BudgetMonthlyAdjustModal({
  open,
  onClose,
  granularityRows,
  positionsByCode,
  statement,
  granularityByPosition,
  draft,
  overrides,
  onOverride,
  activePlanYear,
}: BudgetMonthlyAdjustModalProps) {
  const [editKey, setEditKey] = useState<string | null>(null);

  const lineRows = useMemo(
    () => granularityRows.filter((r) => r.kind === 'line' && (r.plannable ?? true)),
    [granularityRows],
  );

  if (!open) return null;

  function getMonths(lineCode: string, gran: PositionGranularity, pos: BudgetPosition | undefined): number[] {
    const ov = overrides[lineCode] ?? {};
    const partnerLevel = draft.partnerRateLevel?.[`${statement}|${lineCode}`] ?? 'partner';
    if (ov.monthsByYear?.[activePlanYear]?.length === 12) return [...ov.monthsByYear[activePlanYear]];
    if (ov.months?.length === 12) return [...ov.months];
    return resolveSandboxBlankMonths(gran, pos, ov, partnerLevel);
  }

  function handleParentMonthEdit(
    lineCode: string,
    gran: PositionGranularity,
    pos: BudgetPosition | undefined,
    monthIdx: number,
    newKeur: number,
  ) {
    const ov = overrides[lineCode] ?? {};
    const newEur = newKeur * 1000;
    const months = getMonths(lineCode, gran, pos);

    if (gran === 'L4' && pos?.children?.length) {
      const childVals = pos.children.map((c) => {
        const cOv = ov.l4?.[c.level_4];
        return cOv?.months?.[monthIdx] ?? (c.annual / 12);
      });
      const rebalanced = rebalanceChildrenToParentMonth(newEur, childVals);
      const l4 = { ...(ov.l4 ?? {}) };
      pos.children.forEach((c, i) => {
        const prev = l4[c.level_4] ?? {};
        const prevMonths = prev.months?.length === 12 ? [...prev.months] : Array(12).fill(c.annual / 12);
        prevMonths[monthIdx] = rebalanced[i];
        l4[c.level_4] = { ...prev, months: prevMonths, annual: prevMonths.reduce((s, v) => s + v, 0) };
      });
      onOverride(lineCode, { ...ov, l4, months: undefined });
      return;
    }

    if ((gran === 'customers' || gran === 'suppliers') && pos?.partners?.length) {
      const partnerLevel = draft.partnerRateLevel?.[`${statement}|${lineCode}`] ?? 'partner';
      const partners = { ...(ov.partners ?? {}) };
      if (partnerLevel === 'partner') {
        const childVals = pos.partners.map((p) => {
          const pOv = partners[p.partner_id];
          return pOv?.months?.[monthIdx] ?? p.annual / 12;
        });
        const rebalanced = rebalanceChildrenToParentMonth(newEur, childVals);
        pos.partners.forEach((p, i) => {
          const prev = partners[p.partner_id] ?? {};
          const prevMonths = prev.months?.length === 12 ? [...prev.months] : Array(12).fill(p.annual / 12);
          prevMonths[monthIdx] = rebalanced[i];
          partners[p.partner_id] = { ...prev, months: prevMonths, annual: prevMonths.reduce((s, v) => s + v, 0) };
        });
      } else {
        const groups = groupPartnersByRank(pos.partners);
        const flatPartners: string[] = [];
        const childVals: number[] = [];
        for (const g of groups) {
          for (const p of g.partners) {
            flatPartners.push(p.partner_id);
            const pOv = partners[p.partner_id];
            childVals.push(pOv?.months?.[monthIdx] ?? p.annual / 12);
          }
        }
        const rebalanced = rebalanceChildrenToParentMonth(newEur, childVals);
        flatPartners.forEach((pid, i) => {
          const prev = partners[pid] ?? {};
          const prevMonths = prev.months?.length === 12 ? [...prev.months] : Array(12).fill(0);
          prevMonths[monthIdx] = rebalanced[i];
          partners[pid] = { ...prev, months: prevMonths, annual: prevMonths.reduce((s, v) => s + v, 0) };
        });
      }
      onOverride(lineCode, { ...ov, partners, months: undefined });
      return;
    }

    const nextMonths = [...months];
    nextMonths[monthIdx] = newEur;
    onOverride(lineCode, {
      ...ov,
      months: nextMonths,
      annual: nextMonths.reduce((s, v) => s + v, 0),
      monthsByYear: { ...(ov.monthsByYear ?? {}), [activePlanYear]: nextMonths },
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-6xl flex-col rounded-xl border border-slate-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-[#1E3A5F]">Adjust monthly values</h2>
            <p className="text-[10px] text-slate-500">Edit kEUR per month — child rows rebalance to keep position totals.</p>
          </div>
          <button type="button" onClick={onClose} className="rounded p-1 text-slate-500 hover:bg-slate-100">
            <X size={18} />
          </button>
        </div>

        <div className="overflow-auto flex-1 p-4">
          <table className="border-collapse text-xs w-full">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50">
                <th className="py-2 px-2 text-left font-semibold text-slate-500 sticky left-0 bg-slate-50 min-w-[200px]">Position</th>
                {MONTH_LABELS.map((m) => (
                  <th key={m} className="py-2 px-1 text-right font-medium text-slate-500 min-w-[56px]">{m}</th>
                ))}
                <th className="py-2 px-2 text-right font-semibold text-slate-500">Total</th>
              </tr>
            </thead>
            <tbody>
              {lineRows.map((row) => {
                const gran = granularityByPosition[`${statement}|${row.line_code}`] ?? 'L3';
                const pos = positionsByCode[row.line_code];
                const months = getMonths(row.line_code, gran, pos);
                const total = months.reduce((s, v) => s + v, 0);
                const pad = 10 + row.indent * 14;

                return (
                  <tr key={row.line_code} className="border-b border-slate-100 hover:bg-slate-50/50">
                    <td className="py-1.5 px-2 text-left sticky left-0 bg-white" style={{ paddingLeft: pad }}>
                      <span className="text-xs text-slate-700">{row.label}</span>
                    </td>
                    {months.map((mv, i) => {
                      const cellKey = `${row.line_code}|${i}`;
                      return (
                        <td key={i} className="py-1 px-0.5 text-right">
                          <input
                            type="number"
                            defaultValue={Math.round(mv / 1000)}
                            onFocus={() => setEditKey(cellKey)}
                            onBlur={(e) => {
                              const n = parseFloat(e.target.value);
                              if (Number.isFinite(n) && editKey === cellKey) {
                                handleParentMonthEdit(row.line_code, gran, pos, i, n);
                              }
                              setEditKey(null);
                            }}
                            className="w-14 rounded border border-slate-200 px-1 py-0.5 text-right text-xs font-mono
                              focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]"
                          />
                        </td>
                      );
                    })}
                    <td className="py-1.5 px-2 text-right font-mono text-xs font-semibold text-[#1E3A5F]">
                      {fmtK(total)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-200 px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-[#1E3A5F] px-4 py-1.5 text-xs font-semibold text-white hover:opacity-90"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
