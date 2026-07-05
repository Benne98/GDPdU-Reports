/**
 * StructuredBudgetView — Planning View for Budget Chat.
 *
 * Layout: sticky Position + Rate | historical actual columns | Plan columns
 *
 * Key behaviours:
 *  - Annual mode: Position | Rate | [FY actual year columns] | [one plan-year column per draft.fiscalYears]
 *  - Monthly mode: Position | Rate | [24 historical months] | [12 plan months of activePlanYear]
 *  - Position column sticky-left (indent-padded, subtotals bold).
 *  - Rate column sticky-left (right of Position): editable for plannable 'line' rows.
 *    method-specific: trend_cagr → CAGR %, prior_year → growth %, run_rate → annualised kEUR.
 *    Rate is per (line_code, activePlanYear). Editing Rate recomputes annual plan via the method
 *    formula, redistributes to 12 plan months via seasonal weights, and re-propagates live subtotals.
 *  - Historical columns: EUR values from granularity view / 1000 → kEUR (fmtKeur).
 *  - Plan values: EUR / 1000 → kEUR.
 *  - Subtotals computed live per column from components[].
 *  - Undo / redo: controlled via canUndo, canRedo, onUndo, onRedo props.
 *  - Year selector tab bar shown in view header when draft.fiscalYears.length > 1.
 *    Annual mode: all plan-year columns always shown; selector highlights active (for Rate edits).
 *    Monthly mode: selector picks which plan year's 12 months are displayed.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Undo2, Redo2, CalendarDays } from 'lucide-react';
import type { BudgetPosition, BudgetGranularityRow, BudgetGranularityPeriod } from '../../lib/gdpduApi';
import type { PositionOverride, SubRowOverride } from './BudgetGrid';
import type { PositionGranularity, PositionKey, BudgetDraft, HeuristicMethod } from '../../lib/budgetChatFlow';
import { fyLabel } from '../../lib/budgetChatFlow';
import { IS_REPORTING_V2_SANDBOX } from '../../lib/reportingV2SandboxMode';
import {
  resolveSandboxBlankAnnual,
  resolveSandboxBlankMonths,
  resolveChildAnnual,
  resolveChildMonths,
} from '../../lib/budgetBlankRollup';
import { groupPartnersByRank } from '../../lib/budgetPartnerGroups';
import {
  historyAnnualByLineFromRows,
  resolveL4PlanningBaseAnnual,
  resolvePartnerPlanningBaseAnnual,
  resolvePlanningBaseAnnual,
  type PlanningBaseContext,
} from '../../lib/budgetPlanningBase';
import SandboxBlankRateCell from './SandboxBlankRateCell';

// ---------------------------------------------------------------------------
// Re-export PositionOverride so callers can import from here too
// ---------------------------------------------------------------------------
export type { PositionOverride };

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

/** Format a raw EUR value as kEUR (divide by 1000). */
function fmtKeur(eurValue: number): string {
  const k = eurValue / 1000;
  const abs = Math.abs(k);
  const s = abs.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  return eurValue < 0 ? `(${s})` : s;
}

/** Format already-in-kEUR value. */
function fmtK(kValue: number): string {
  const abs = Math.abs(kValue);
  const s = abs.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  return kValue < 0 ? `(${s})` : s;
}

// ---------------------------------------------------------------------------
// Rate derivation: extract the editable rate from explanation + method
// ---------------------------------------------------------------------------

type RateUnit = 'pct' | 'keur';

interface RateInfo {
  defaultRate: number;    // the initial value to seed the rate input
  unit: RateUnit;
  methodLabel: string;    // short label shown in the Rate cell
  baseAnnual: number;     // EUR — used in recompute formula
  lastAnnual: number;     // EUR — used for trend_cagr: lastAnnual*(1+cagr)
}

function deriveRateInfo(
  pos: BudgetPosition,
  heuristic: HeuristicMethod | undefined,
): RateInfo {
  const expl = pos.explanation ?? {};
  const method = heuristic ?? 'prior_year';
  const baseAnnual = Number(expl['base_annual'] ?? pos.annual ?? 0);
  const lastAnnual = Number(expl['last_annual'] ?? baseAnnual);

  if (method === 'prior_year') {
    const gPct = expl['growth_pct'] !== undefined ? Number(expl['growth_pct']) : 0;
    return {
      defaultRate: gPct,
      unit: 'pct',
      methodLabel: 'Prior yr %',
      baseAnnual,
      lastAnnual,
    };
  }
  if (method === 'trend_cagr') {
    const cagr = expl['cagr'] !== undefined ? Number(expl['cagr']) * 100 : 0;
    return {
      defaultRate: cagr,
      unit: 'pct',
      methodLabel: 'CAGR %',
      baseAnnual,
      lastAnnual,
    };
  }
  if (method === 'run_rate') {
    // For run_rate the rate is the annualised run-rate value in kEUR
    const annualKeur = (pos.suggestion?.annual ?? pos.annual ?? 0) / 1000;
    return {
      defaultRate: annualKeur,
      unit: 'keur',
      methodLabel: 'Run-rate kEUR',
      baseAnnual,
      lastAnnual,
    };
  }
  // fallback
  return {
    defaultRate: 0,
    unit: 'pct',
    methodLabel: method,
    baseAnnual,
    lastAnnual,
  };
}

/**
 * Recompute annual EUR from the edited rate.
 *   prior_year:  annual = base_annual * (1 + rate/100)
 *   trend_cagr:  annual = last_annual * (1 + rate/100)
 *   run_rate:    annual = rate * 1000  (kEUR → EUR)
 */
function rateToAnnual(
  rate: number,
  info: RateInfo,
  method: HeuristicMethod | undefined,
): number {
  const m = method ?? 'prior_year';
  if (m === 'prior_year') return info.baseAnnual * (1 + rate / 100);
  if (m === 'trend_cagr') return info.lastAnnual * (1 + rate / 100);
  if (m === 'run_rate') return rate * 1000;
  return info.baseAnnual * (1 + rate / 100);
}

// ---------------------------------------------------------------------------
// Seasonal distribution: split annual into 12 months using suggestion.months
// weights or even 1/12
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Resolve the effective plan annual EUR for a line_code (for a given year's
// positionsByCode and overrides)
// ---------------------------------------------------------------------------

function resolvePlanAnnual(
  lineCode: string,
  granularity: PositionGranularity,
  pos: BudgetPosition | undefined,
  overrides: Record<string, PositionOverride>,
  year?: number,
  sandboxBlank?: { partnerRateLevel?: 'group' | 'partner'; baseCtx?: PlanningBaseContext },
): number {
  const ov = overrides[lineCode] ?? {};

  if (!pos) return 0;

  if (sandboxBlank) {
    if (year !== undefined && ov.annualByYear?.[year] !== undefined) return ov.annualByYear[year];
    return resolveSandboxBlankAnnual(
      granularity,
      pos,
      ov,
      sandboxBlank.partnerRateLevel ?? 'partner',
      sandboxBlank.baseCtx,
    );
  }

  if (granularity === 'L4') {
    const children = pos.children ?? [];
    return children.reduce((sum, child) => {
      const childOv = ov.l4?.[child.level_4];
      return sum + (childOv?.annual !== undefined ? childOv.annual : child.annual);
    }, 0);
  }

  if (granularity === 'customers' || granularity === 'suppliers') {
    const partners = pos.partners ?? [];
    return partners.reduce((sum, p) => {
      const pOv = ov.partners?.[p.partner_id];
      return sum + (pOv?.annual !== undefined ? pOv.annual : p.annual);
    }, 0);
  }

  // L3: check per-year override first, then general override, then suggestion, then position
  if (year !== undefined && ov.annualByYear?.[year] !== undefined) return ov.annualByYear[year];
  if (ov.annual !== undefined) return ov.annual;
  // *** PLAN SEED FIX: always prefer suggestion.annual so non-zero plan appears ***
  if (pos.suggestion?.annual !== undefined && pos.suggestion.annual !== 0) return pos.suggestion.annual;
  return pos.annual;
}

// ---------------------------------------------------------------------------
// Build monthly plan arrays per line_code for all plan months (12) for a
// specific year
// ---------------------------------------------------------------------------

function buildPlanMonthsMap(
  rows: BudgetGranularityRow[],
  positionsByCode: Record<string, BudgetPosition>,
  granularityByPosition: Record<PositionKey, PositionGranularity>,
  overrides: Record<string, PositionOverride>,
  statement: 'PL' | 'BS',
  year?: number,
  sandboxBlank?: boolean,
  partnerRateLevelByPosition?: Record<PositionKey, 'group' | 'partner'>,
  positionsByCodeAllYears?: Record<number, Record<string, BudgetPosition>>,
  historyAnnualByLine?: Record<string, number>,
  sandboxMonthlyFrozen?: boolean,
): Record<string, number[]> {
  const monthsMap: Record<string, number[]> = {};
  const annualMap: Record<string, number> = {};
  const planYear = year ?? new Date().getFullYear() + 1;

  // First pass: resolve 'line' rows
  for (const row of rows) {
    if (row.kind !== 'line') continue;
    const gran: PositionGranularity = granularityByPosition[`${statement}|${row.line_code}`] ?? 'L3';
    const pos = positionsByCode[row.line_code];
    const ov = overrides[row.line_code] ?? {};
    const partnerLevel = partnerRateLevelByPosition?.[`${statement}|${row.line_code}`] ?? 'partner';
    const baseCtx: PlanningBaseContext | undefined = sandboxBlank
      ? {
          lineCode: row.line_code,
          activePlanYear: planYear,
          positionsByCodeAllYears: positionsByCodeAllYears ?? { [planYear]: positionsByCode },
          historyAnnualByLine,
        }
      : undefined;

    const annual = resolvePlanAnnual(
      row.line_code,
      gran,
      pos,
      overrides,
      year,
      sandboxBlank ? { partnerRateLevel: partnerLevel, baseCtx } : undefined,
    );
    annualMap[row.line_code] = annual;

    // Use per-year override months if available, else general months, else seasonalise
    let months: number[];
    const manualMonths =
      (year !== undefined && ov.monthsByYear?.[year]?.length === 12)
        ? ov.monthsByYear[year]
        : ov.months?.length === 12
          ? ov.months
          : undefined;
    if (manualMonths && !sandboxBlank) {
      months = manualMonths;
    } else if (sandboxBlank) {
      months =
        sandboxMonthlyFrozen && manualMonths
          ? manualMonths
          : resolveSandboxBlankMonths(gran, pos, ov, partnerLevel, baseCtx);
    } else if (manualMonths) {
      months = manualMonths;
    } else {
      months = seasonalise(annual, pos);
    }
    monthsMap[row.line_code] = months;
  }

  // Second pass: subtotals/grandtotals via components
  for (const row of rows) {
    if (row.kind === 'line') continue;
    const comps = (row as unknown as { components?: string[] }).components;
    if (comps && comps.length > 0) {
      const subtotalMonths = Array(12).fill(0) as number[];
      for (const code of comps) {
        const childMonths = monthsMap[code];
        if (childMonths) {
          for (let i = 0; i < 12; i++) {
            subtotalMonths[i] += childMonths[i] ?? 0;
          }
        }
      }
      monthsMap[row.line_code] = subtotalMonths;
      annualMap[row.line_code] = subtotalMonths.reduce((s, v) => s + v, 0);
    }
  }

  return monthsMap;
}

// ---------------------------------------------------------------------------
// Build annual plan map per line_code for a specific year
// ---------------------------------------------------------------------------

function buildPlanAnnualMap(
  rows: BudgetGranularityRow[],
  positionsByCode: Record<string, BudgetPosition>,
  granularityByPosition: Record<PositionKey, PositionGranularity>,
  overrides: Record<string, PositionOverride>,
  statement: 'PL' | 'BS',
  year: number,
  sandboxBlank?: boolean,
  partnerRateLevelByPosition?: Record<PositionKey, 'group' | 'partner'>,
  positionsByCodeAllYears?: Record<number, Record<string, BudgetPosition>>,
  historyAnnualByLine?: Record<string, number>,
): Record<string, number> {
  const annualMap: Record<string, number> = {};

  // First pass: 'line' rows
  for (const row of rows) {
    if (row.kind !== 'line') continue;
    const gran: PositionGranularity = granularityByPosition[`${statement}|${row.line_code}`] ?? 'L3';
    const pos = positionsByCode[row.line_code];
    const partnerLevel = partnerRateLevelByPosition?.[`${statement}|${row.line_code}`] ?? 'partner';
    const baseCtx: PlanningBaseContext | undefined = sandboxBlank
      ? {
          lineCode: row.line_code,
          activePlanYear: year,
          positionsByCodeAllYears: positionsByCodeAllYears ?? { [year]: positionsByCode },
          historyAnnualByLine,
        }
      : undefined;
    annualMap[row.line_code] = resolvePlanAnnual(
      row.line_code,
      gran,
      pos,
      overrides,
      year,
      sandboxBlank ? { partnerRateLevel: partnerLevel, baseCtx } : undefined,
    );
  }

  // Second pass: subtotals/grandtotals
  for (const row of rows) {
    if (row.kind === 'line') continue;
    const comps = (row as unknown as { components?: string[] }).components;
    if (comps && comps.length > 0) {
      let sum = 0;
      for (const code of comps) {
        sum += annualMap[code] ?? 0;
      }
      annualMap[row.line_code] = sum;
    }
  }

  return annualMap;
}

// ---------------------------------------------------------------------------
// Sticky cell positions
// ---------------------------------------------------------------------------

const POSITION_COL_WIDTH = 340;
const RATE_COL_WIDTH = 140;

/** Opaque sticky backgrounds — must match row tint so scrolled data does not show through. */
const STICKY_BG_ROW = '#FFFFFF';
const STICKY_BG_SUBROW = '#F8FAFC';
const STICKY_BG_GROUP_ROW = '#F1F5F9';
const STICKY_BG_HEADER = '#F8FAFC';

const stickyPosition: React.CSSProperties = {
  position: 'sticky',
  left: 0,
  zIndex: 3,
  minWidth: POSITION_COL_WIDTH,
  maxWidth: POSITION_COL_WIDTH,
  width: POSITION_COL_WIDTH,
  overflow: 'hidden',
};

const stickyRate: React.CSSProperties = {
  position: 'sticky',
  left: POSITION_COL_WIDTH,
  zIndex: 4,
  minWidth: RATE_COL_WIDTH,
  width: RATE_COL_WIDTH,
  boxShadow: '4px 0 6px -2px rgba(15, 23, 42, 0.08)',
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface StructuredBudgetViewProps {
  granularityRows: BudgetGranularityRow[];
  granularityPeriods: BudgetGranularityPeriod[];
  positionsByCode: Record<string, BudgetPosition>;
  /** All years' positions keyed by fiscal year — used for annual mode plan columns. */
  positionsByCodeAllYears: Record<number, Record<string, BudgetPosition>>;
  statement: 'PL' | 'BS';
  granularityByPosition: Record<PositionKey, PositionGranularity>;
  draft: BudgetDraft;
  overrides: Record<string, PositionOverride>;
  onOverride: (lineCode: string, override: PositionOverride) => void;
  saving: boolean;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onSave: () => void;
  onEditSetup: () => void;
  saveDone: boolean;
  /** The plan year currently active for Rate edits and monthly column display. */
  activePlanYear: number;
  onSetActivePlanYear: (year: number) => void;
  onPartnerRateLevelChange?: (positionKey: PositionKey, level: 'group' | 'partner') => void;
  onOpenMonthlyAdjust?: () => void;
  /** Sandbox: growth % locked after Save budget. */
  ratesFrozen?: boolean;
  /** Sandbox: allow inline kEUR edits on plan month columns (post-save). */
  monthlyEditable?: boolean;
  onCommit?: () => void;
  commitLoading?: boolean;
  commitDone?: boolean;
}

// ---------------------------------------------------------------------------
// Rate input component — editable inline field
// ---------------------------------------------------------------------------

interface RateInputProps {
  rate: number;       // current rate value (pct or keur)
  unit: RateUnit;
  methodLabel: string;
  disabled: boolean;
  onChange: (newRate: number) => void;
}

function RateInput({ rate, unit, methodLabel, disabled, onChange }: RateInputProps) {
  const [raw, setRaw] = useState<string | null>(null);
  const display = raw !== null ? raw : String(+rate.toFixed(unit === 'pct' ? 2 : 0));

  function commit(s: string) {
    const n = parseFloat(s);
    if (Number.isFinite(n)) onChange(n);
    setRaw(null);
  }

  return (
    <div className="flex flex-col items-end gap-0.5">
      <div className="flex items-center gap-0.5">
        <input
          type="number"
          value={display}
          disabled={disabled}
          onChange={(e) => setRaw(e.target.value)}
          onBlur={(e) => commit(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              commit((e.target as HTMLInputElement).value);
              (e.target as HTMLInputElement).blur();
            }
          }}
          className="w-20 rounded border border-slate-200 bg-white px-1 py-0.5 text-right text-xs font-mono
            focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]
            disabled:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed"
          title={`Edit ${methodLabel}`}
          step={unit === 'pct' ? '0.1' : '1'}
        />
        <span className="text-[9px] text-slate-400 shrink-0">{unit === 'pct' ? '%' : 'k'}</span>
      </div>
      <span className="text-[8px] text-slate-400 truncate max-w-[132px]" title={methodLabel}>
        {methodLabel}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Editable plan month cell (sandbox post-save)
// ---------------------------------------------------------------------------

function PlanMonthCell({
  eurValue,
  editable,
  disabled,
  onCommitKeur,
  emphasize = false,
}: {
  eurValue: number;
  editable: boolean;
  disabled?: boolean;
  onCommitKeur?: (keur: number) => void;
  emphasize?: boolean;
}) {
  const [raw, setRaw] = useState<string | null>(null);
  if (!editable) {
    return (
      <span
        className="text-xs font-mono tabular-nums"
        style={{ color: emphasize ? '#1E3A5F' : '#1E3A5F', fontWeight: emphasize ? 700 : 400 }}
      >
        {fmtK(eurValue / 1000)}
      </span>
    );
  }
  const display = raw ?? String(Math.round(eurValue / 1000));
  return (
    <input
      type="number"
      value={display}
      disabled={disabled}
      onChange={(e) => setRaw(e.target.value)}
      onBlur={() => {
        if (raw !== null && onCommitKeur) {
          const n = parseFloat(raw);
          if (Number.isFinite(n)) onCommitKeur(n);
        }
        setRaw(null);
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
      }}
      className="w-14 rounded border border-slate-200 bg-white px-1 py-0.5 text-right text-xs font-mono
        focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]
        disabled:bg-slate-50 disabled:text-slate-400"
    />
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function StructuredBudgetView({
  granularityRows,
  granularityPeriods,
  positionsByCode,
  positionsByCodeAllYears,
  statement,
  granularityByPosition,
  draft,
  overrides,
  onOverride,
  saving,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  onSave,
  onEditSetup,
  saveDone,
  activePlanYear,
  onSetActivePlanYear,
  onPartnerRateLevelChange,
  onOpenMonthlyAdjust,
  ratesFrozen = false,
  monthlyEditable = false,
  onCommit,
  commitLoading = false,
  commitDone = false,
}: StructuredBudgetViewProps) {
  const isSandboxBlank = IS_REPORTING_V2_SANDBOX && draft.start.mode === 'blank';

  // ── Expand state for L4/partner sub-rows ──
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggleExpand = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }, []);

  // Auto-expand granularity sub-rows in sandbox blank mode
  useEffect(() => {
    if (!isSandboxBlank) return;
    const toExpand = new Set<string>();
    for (const row of granularityRows) {
      if (row.kind !== 'line') continue;
      const gran = granularityByPosition[`${statement}|${row.line_code}`] ?? 'L3';
      if (gran === 'L4' || gran === 'customers' || gran === 'suppliers') {
        toExpand.add(`${statement}|${row.line_code}`);
      }
    }
    setExpanded(toExpand);
  }, [isSandboxBlank, granularityRows, granularityByPosition, statement]);

  const planFY = activePlanYear;
  const planFYLabel = fyLabel(planFY);
  const heuristic = draft.start.heuristic;
  const viewMode = draft.viewMode ?? 'monthly';
  const isAnnualMode = viewMode === 'annual';
  const planYears = draft.fiscalYears;
  const showYearSelector = planYears.length > 1;

  // ── Historical period count and labels ──
  const histPeriods = granularityPeriods;
  const histCount = histPeriods.length;

  const historyAnnualByLine = useMemo(
    () => historyAnnualByLineFromRows(granularityRows),
    [granularityRows],
  );

  // ── Plan months map for monthly mode (memoised on activePlanYear + overrides + positions) ──
  // For monthly mode: 12 months of activePlanYear
  const planMonthsMap = useMemo(
    () =>
      buildPlanMonthsMap(
        granularityRows,
        positionsByCodeAllYears[activePlanYear] ?? positionsByCode,
        granularityByPosition,
        overrides,
        statement,
        activePlanYear,
        isSandboxBlank,
        draft.partnerRateLevel,
        positionsByCodeAllYears,
        historyAnnualByLine,
        monthlyEditable,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [granularityRows, positionsByCode, positionsByCodeAllYears, granularityByPosition, overrides, statement, activePlanYear, isSandboxBlank, draft.partnerRateLevel, historyAnnualByLine, monthlyEditable],
  );

  // ── Plan annual maps per year for annual mode ──
  // Keyed by fiscal year number → { line_code → annual EUR }
  const planAnnualMapsByYear = useMemo(() => {
    if (!isAnnualMode) return {} as Record<number, Record<string, number>>;
    const result: Record<number, Record<string, number>> = {};
    for (const yr of planYears) {
      result[yr] = buildPlanAnnualMap(
        granularityRows,
        positionsByCodeAllYears[yr] ?? positionsByCode,
        granularityByPosition,
        overrides,
        statement,
        yr,
        isSandboxBlank,
        draft.partnerRateLevel,
        positionsByCodeAllYears,
        historyAnnualByLine,
      );
    }
    return result;
  }, [isAnnualMode, planYears, granularityRows, positionsByCode, positionsByCodeAllYears, granularityByPosition, overrides, statement, isSandboxBlank, draft.partnerRateLevel, historyAnnualByLine]);

  // ── Derive plan month header labels (Jan–Dec of planFY) for monthly mode ──
  const planMonthLabels = useMemo(() => {
    return Array.from({ length: 12 }, (_, i) => {
      const d = new Date(planFY, i, 1);
      return d.toLocaleString('en-US', { month: 'short' }) + String(planFY).slice(-2);
    });
  }, [planFY]);

  if (granularityRows.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white px-6 py-12 text-center">
        <p className="text-sm text-slate-500">
          No positions available. The granularity view returned no rows for this statement.
        </p>
      </div>
    );
  }

  // Number of plan columns: annual mode = one per planYear; monthly mode = 12
  const planColCount = isAnnualMode ? planYears.length : 12;

  // Column count helpers
  const totalCols = 2 + histCount + 1 + planColCount; // pos + rate + hist + divider + plan

  // ---------------------------------------------------------------------------
  // Row renderer
  // ---------------------------------------------------------------------------

  function renderRow(row: BudgetGranularityRow): React.ReactNode[] {
    const pad = 10 + row.indent * 14;

    // ── Structural rows (subtotal / grandtotal) ──
    if (row.kind === 'subtotal' || row.kind === 'grandtotal') {
      const bg = row.kind === 'grandtotal' ? '#EEF2F7' : '#F8FAFC';
      const color = row.kind === 'grandtotal' ? '#1E3A5F' : '#374151';

      return [
        <tr key={row.id} style={{ borderBottom: '1px solid #E2E8F0', background: bg }}>
          {/* Position label — sticky */}
          <td
            className="py-2 text-left align-middle whitespace-nowrap overflow-hidden"
            style={{ ...stickyPosition, background: bg, paddingLeft: pad, paddingRight: 8 }}
          >
            <span className="text-xs font-bold truncate" style={{ color }}>{row.label}</span>
          </td>

          {/* Rate — blank for structural rows */}
          <td
            className="py-2 px-2 align-middle"
            style={{ ...stickyRate, background: bg, borderRight: '2px solid #CBD5E1' }}
          />

          {/* Historical actual columns */}
          {Array.from({ length: histCount }, (_, i) => {
            const v = row.values[i];
            return (
              <td key={`h${i}`} className="py-2 px-2 text-right text-xs font-mono tabular-nums whitespace-nowrap"
                style={{ color, fontWeight: 700, minWidth: 72 }}>
                {v !== undefined && v !== null ? fmtKeur(v) : '—'}
              </td>
            );
          })}

          {/* Divider between history and plan */}
          <td className="w-0 p-0" style={{ borderLeft: '2px solid #1E3A5F', padding: 0 }} />

          {/* Plan columns */}
          {isAnnualMode
            ? planYears.map((yr) => {
                const annualEur = planAnnualMapsByYear[yr]?.[row.line_code] ?? 0;
                const isActive = yr === activePlanYear;
                return (
                  <td key={`p${yr}`} className="py-2 px-2 text-right text-xs font-mono tabular-nums whitespace-nowrap"
                    style={{ color, fontWeight: 700, minWidth: 72, background: isActive ? 'rgba(30,58,95,0.07)' : 'rgba(30,58,95,0.03)' }}>
                    {fmtKeur(annualEur)}
                  </td>
                );
              })
            : (planMonthsMap[row.line_code] ?? Array(12).fill(0)).map((mv: number, i: number) => (
                <td key={`p${i}`} className="py-2 px-2 text-right text-xs font-mono tabular-nums whitespace-nowrap"
                  style={{ color, fontWeight: 700, minWidth: 72, background: 'rgba(30,58,95,0.03)' }}>
                  {fmtK(mv / 1000)}
                </td>
              ))
          }
        </tr>,
      ];
    }

    // ── Line (mapping) rows ──
    const gran: PositionGranularity = granularityByPosition[`${statement}|${row.line_code}`] ?? 'L3';
    // Use activePlanYear's positions for rate derivation
    const pos = positionsByCode[row.line_code]
      ?? (positionsByCodeAllYears[activePlanYear] ?? {})[row.line_code];
    const ov = overrides[row.line_code] ?? {};
    const plannable = row.plannable ?? true;
    const expandId = `${statement}|${row.line_code}`;
    const isExpanded = expanded.has(expandId);
    const hasSubRows =
      gran === 'L4' && (pos?.children?.length ?? 0) > 0
      || ((gran === 'customers' || gran === 'suppliers') && (pos?.partners?.length ?? 0) > 0);
    const posReadOnly = !isSandboxBlank && (gran === 'L4' || gran === 'customers' || gran === 'suppliers');
    const partnerRateLevel = draft.partnerRateLevel?.[`${statement}|${row.line_code}`] ?? 'partner';
    const rateInputsDisabled = saving || ratesFrozen || commitLoading;

    // Rate info (only needed for plannable L3 rows)
    const rateInfo: RateInfo | null =
      pos && plannable && !posReadOnly
        ? deriveRateInfo(pos, heuristic)
        : null;

    // Current rate: prefer per-year override, then general override annotation, then default
    const currentRate: number = (() => {
      if (!rateInfo) return 0;
      // Per-year rate stored in rateByYear
      if (ov.rateByYear?.[activePlanYear] !== undefined) return ov.rateByYear[activePlanYear];
      // Fall back to general rate annotation (legacy)
      const ovRate = (ov as unknown as { rate?: number }).rate;
      return ovRate !== undefined ? ovRate : rateInfo.defaultRate;
    })();

    function handleRateChange(newRate: number) {
      if (!rateInfo || !pos) return;
      const newAnnual = rateToAnnual(newRate, rateInfo, heuristic);
      const months = seasonalise(newAnnual, pos);
      onOverride(row.line_code, {
        ...ov,
        // Store per-year rate
        rateByYear: { ...(ov.rateByYear ?? {}), [activePlanYear]: newRate },
        // Store per-year annual and months
        annualByYear: { ...(ov.annualByYear ?? {}), [activePlanYear]: newAnnual },
        monthsByYear: { ...(ov.monthsByYear ?? {}), [activePlanYear]: months },
        // Also update general annual/months for backward compat with monthly mode
        annual: newAnnual,
        months,
      } as PositionOverride);
    }

    const planMonths = planMonthsMap[row.line_code] ?? Array(12).fill(0);
    const lineBaseCtx: PlanningBaseContext = {
      lineCode: row.line_code,
      activePlanYear,
      positionsByCodeAllYears,
      historyAnnualByLine,
    };
    const positionPlanningBase = resolvePlanningBaseAnnual(pos?.annual ?? 0, pos, lineBaseCtx);

    const nodes: React.ReactNode[] = [];

    nodes.push(
      <tr
        key={expandId}
        className="border-b border-slate-100 hover:bg-slate-50/40 transition-colors"
      >
        {/* Position label — sticky */}
        <td
          className="py-1.5 text-left align-middle"
          style={{ ...stickyPosition, background: STICKY_BG_ROW, paddingLeft: pad, paddingRight: 8 }}
        >
          <div className="flex items-center gap-1 min-w-0">
            {hasSubRows ? (
              <button
                type="button"
                onClick={() => toggleExpand(expandId)}
                className="flex-shrink-0 w-4 h-4 text-slate-400 hover:text-[#1E3A5F] transition-colors"
                aria-expanded={isExpanded}
                title={isExpanded ? 'Collapse' : 'Expand'}
              >
                <span className="text-xs leading-none">{isExpanded ? '▾' : '▸'}</span>
              </button>
            ) : (
              <span className="w-4 flex-shrink-0" />
            )}
            <span
              className="text-xs truncate"
              style={{ fontWeight: posReadOnly || (isSandboxBlank && hasSubRows) ? 600 : 400, color: plannable ? '#111827' : '#6B7280' }}
              title={row.label}
            >
              {row.label}
            </span>
            {isSandboxBlank && (gran === 'customers' || gran === 'suppliers') && onPartnerRateLevelChange && !ratesFrozen && (
              <select
                value={partnerRateLevel}
                onChange={(e) =>
                  onPartnerRateLevelChange(
                    `${statement}|${row.line_code}`,
                    e.target.value as 'group' | 'partner',
                  )
                }
                className="ml-1 flex-shrink-0 rounded border border-slate-200 bg-white px-1 py-0 text-[9px] text-slate-600"
                title="Rate level"
              >
                <option value="group">Group</option>
                <option value="partner">Partner</option>
              </select>
            )}
          </div>
        </td>

        {/* Rate — editable for plannable L3, blank otherwise */}
        <td
          className="py-1 px-2 align-middle"
          style={{ ...stickyRate, background: STICKY_BG_ROW, borderRight: '2px solid #CBD5E1' }}
        >
          {isSandboxBlank && plannable && gran === 'L3' ? (
            <SandboxBlankRateCell
              baseAnnual={positionPlanningBase}
              state={ov}
              pos={pos}
              disabled={rateInputsDisabled}
              onChange={(next) => onOverride(row.line_code, { ...ov, ...next })}
            />
          ) : rateInfo && plannable && !posReadOnly ? (
            <RateInput
              rate={currentRate}
              unit={rateInfo.unit}
              methodLabel={rateInfo.methodLabel}
              disabled={rateInputsDisabled}
              onChange={handleRateChange}
            />
          ) : null}
        </td>

        {/* Historical actual columns */}
        {Array.from({ length: histCount }, (_, i) => {
          const v = row.values[i];
          return (
            <td key={`h${i}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums text-slate-500 whitespace-nowrap"
              style={{ minWidth: 72 }}>
              {v !== undefined && v !== null ? fmtKeur(v) : '—'}
            </td>
          );
        })}

        {/* Divider */}
        <td className="w-0 p-0" style={{ borderLeft: '2px solid #1E3A5F', padding: 0 }} />

        {/* Plan columns */}
        {isAnnualMode
          ? planYears.map((yr) => {
              const annualEur = planAnnualMapsByYear[yr]?.[row.line_code] ?? 0;
              const isActive = yr === activePlanYear;
              return (
                <td key={`p${yr}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums whitespace-nowrap"
                  style={{ minWidth: 72, background: isActive ? 'rgba(30,58,95,0.07)' : 'rgba(30,58,95,0.03)', color: plannable ? '#1E3A5F' : '#94A3B8' }}>
                  {plannable ? fmtKeur(annualEur) : '—'}
                </td>
              );
            })
          : planMonths.map((mv: number, i: number) => (
              <td key={`p${i}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums whitespace-nowrap"
                style={{ minWidth: 72, background: 'rgba(30,58,95,0.03)', color: plannable ? '#1E3A5F' : '#94A3B8' }}>
                <PlanMonthCell
                  eurValue={mv}
                  editable={monthlyEditable && !isAnnualMode && plannable && gran === 'L3'}
                  disabled={rateInputsDisabled}
                  emphasize={plannable}
                  onCommitKeur={(keur) => {
                    const months = [...planMonths];
                    months[i] = keur * 1000;
                    onOverride(row.line_code, {
                      ...ov,
                      months,
                      annual: months.reduce((s, v) => s + v, 0),
                      monthsByYear: { ...(ov.monthsByYear ?? {}), [activePlanYear]: months },
                    });
                  }}
                />
              </td>
            ))
        }
      </tr>,
    );

    // ── L4 sub-rows ──
    if (gran === 'L4' && isExpanded && pos?.children) {
      for (const child of pos.children) {
        const childOv = ov.l4?.[child.level_4] ?? {};
        const childBase = resolveL4PlanningBaseAnnual(child.annual, child.level_4, pos, lineBaseCtx);
        const childAnnual = isSandboxBlank
          ? resolveChildAnnual(childBase, childOv)
          : childOv.annual !== undefined ? childOv.annual : child.annual;
        const childMonths = isSandboxBlank
          ? resolveChildMonths(childBase, childOv, pos, childOv.months)
          : childOv.months?.length === 12
            ? childOv.months
            : seasonalise(childAnnual, pos);

        nodes.push(
          <tr key={`${expandId}|l4|${child.level_4}`} className="border-b border-slate-100 bg-slate-50/40">
            <td
              className="py-1.5 text-left align-middle overflow-hidden"
              style={{ ...stickyPosition, background: STICKY_BG_SUBROW, paddingLeft: pad + 20, paddingRight: 8 }}
            >
              <span className="block text-xs text-slate-600 truncate" title={child.label}>{child.label}</span>
            </td>
            <td
              className="py-1 px-2 align-middle"
              style={{ ...stickyRate, background: STICKY_BG_SUBROW, borderRight: '2px solid #CBD5E1' }}
            >
              {isSandboxBlank ? (
                <SandboxBlankRateCell
                  baseAnnual={childBase}
                  state={childOv}
                  pos={pos}
                  disabled={rateInputsDisabled}
                  onChange={(next) =>
                    onOverride(row.line_code, {
                      ...ov,
                      l4: { ...(ov.l4 ?? {}), [child.level_4]: { ...childOv, ...next } },
                    })
                  }
                />
              ) : (
                <>
                  <input
                    type="number"
                    defaultValue={Math.round(childAnnual / 1000)}
                    disabled={rateInputsDisabled}
                    onBlur={(e) => {
                      const n = parseFloat(e.target.value);
                      if (Number.isFinite(n)) {
                        const newAnnual = n * 1000;
                        const months = seasonalise(newAnnual, pos);
                        onOverride(row.line_code, {
                          ...ov,
                          l4: { ...(ov.l4 ?? {}), [child.level_4]: { annual: newAnnual, months } },
                        });
                      }
                    }}
                    className="w-20 rounded border border-slate-200 bg-white px-1 py-0.5 text-right text-xs font-mono
                      focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]
                      disabled:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed"
                    title="Annual plan kEUR"
                  />
                  <span className="text-[9px] text-slate-400 ml-0.5">k</span>
                </>
              )}
            </td>
            {/* Historical — dash */}
            {Array.from({ length: histCount }, (_, i) => (
              <td key={`h${i}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums text-slate-400 whitespace-nowrap"
                style={{ minWidth: 72 }}>
                —
              </td>
            ))}
            {/* Divider */}
            <td className="w-0 p-0" style={{ borderLeft: '2px solid #1E3A5F', padding: 0 }} />
            {/* Plan columns — GAP 2 FIX: each year column reads from that year's positions */}
            {isAnnualMode
              ? planYears.map((yr) => {
                  const isActive = yr === activePlanYear;
                  // For active year use the overridden value; for other years use that
                  // year's BudgetPosition children (their own suggestion/actual).
                  let displayAnnual: number;
                  if (yr === activePlanYear) {
                    displayAnnual = childAnnual;
                  } else {
                    const yrPos = (positionsByCodeAllYears[yr] ?? positionsByCode)[row.line_code];
                    const yrChild = yrPos?.children?.find((c) => c.level_4 === child.level_4);
                    displayAnnual = yrChild?.annual ?? 0;
                  }
                  return (
                    <td key={`p${yr}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums whitespace-nowrap"
                      style={{ minWidth: 72, background: isActive ? 'rgba(30,58,95,0.07)' : 'rgba(30,58,95,0.03)', color: '#1E3A5F' }}>
                      {fmtKeur(displayAnnual)}
                    </td>
                  );
                })
              : childMonths.map((mv: number, i: number) => (
                  <td key={`p${i}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums whitespace-nowrap"
                    style={{ minWidth: 72, background: 'rgba(30,58,95,0.03)', color: '#1E3A5F' }}>
                    <PlanMonthCell
                      eurValue={mv}
                      editable={monthlyEditable && !isAnnualMode}
                      disabled={rateInputsDisabled}
                      onCommitKeur={(keur) => {
                        const months = [...childMonths];
                        months[i] = keur * 1000;
                        onOverride(row.line_code, {
                          ...ov,
                          l4: {
                            ...(ov.l4 ?? {}),
                            [child.level_4]: { ...childOv, months, annual: months.reduce((s, v) => s + v, 0) },
                          },
                        });
                      }}
                    />
                  </td>
                ))
            }
          </tr>,
        );
      }
    }

    // ── Partner sub-rows ──
    if ((gran === 'customers' || gran === 'suppliers') && isExpanded && (pos?.partners?.length ?? 0) > 0) {
      const partners = pos?.partners ?? [];
      const renderPartnerRow = (
        partner: NonNullable<typeof pos.partners>[number],
        indentExtra: number,
        editable: boolean,
        groupState?: SubRowOverride,
      ) => {
        const pOv = ov.partners?.[partner.partner_id] ?? {};
        const effectiveState = editable ? pOv : groupState;
        const partnerBase = resolvePartnerPlanningBaseAnnual(
          partner.annual,
          partner.partner_id,
          pos,
          lineBaseCtx,
        );
        const pAnnual = isSandboxBlank
          ? resolveChildAnnual(partnerBase, effectiveState)
          : pOv.annual !== undefined ? pOv.annual : partner.annual;
        const pMonths = isSandboxBlank
          ? resolveChildMonths(partnerBase, effectiveState, pos, pOv.months)
          : pOv.months?.length === 12
            ? pOv.months
            : seasonalise(pAnnual, pos);

        nodes.push(
          <tr key={`${expandId}|p|${partner.partner_id}`} className="border-b border-slate-100 bg-slate-50/40">
            <td
              className="py-1.5 text-left align-middle overflow-hidden"
              style={{ ...stickyPosition, background: STICKY_BG_SUBROW, paddingLeft: pad + indentExtra, paddingRight: 8 }}
            >
              <span className="block text-xs text-slate-600 italic truncate" title={partner.name}>{partner.name}</span>
            </td>
            <td
              className="py-1 px-2 align-middle"
              style={{ ...stickyRate, background: STICKY_BG_SUBROW, borderRight: '2px solid #CBD5E1' }}
            >
              {isSandboxBlank && editable ? (
                <SandboxBlankRateCell
                  baseAnnual={partnerBase}
                  state={pOv}
                  pos={pos}
                  disabled={rateInputsDisabled}
                  onChange={(next) =>
                    onOverride(row.line_code, {
                      ...ov,
                      partners: {
                        ...(ov.partners ?? {}),
                        [partner.partner_id]: { ...pOv, ...next },
                      },
                    })
                  }
                />
              ) : !isSandboxBlank ? (
                <>
                  <input
                    type="number"
                    defaultValue={Math.round(pAnnual / 1000)}
                    disabled={rateInputsDisabled}
                    onBlur={(e) => {
                      const n = parseFloat(e.target.value);
                      if (Number.isFinite(n)) {
                        const newAnnual = n * 1000;
                        const months = seasonalise(newAnnual, pos);
                        onOverride(row.line_code, {
                          ...ov,
                          partners: {
                            ...(ov.partners ?? {}),
                            [partner.partner_id]: { annual: newAnnual, months },
                          },
                        });
                      }
                    }}
                    className="w-20 rounded border border-slate-200 bg-white px-1 py-0.5 text-right text-xs font-mono
                      focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]
                      disabled:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed"
                    title="Annual plan kEUR"
                  />
                  <span className="text-[9px] text-slate-400 ml-0.5">k</span>
                </>
              ) : null}
            </td>
            {Array.from({ length: histCount }, (_, i) => (
              <td key={`h${i}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums text-slate-400 whitespace-nowrap"
                style={{ minWidth: 72 }}>
                {fmtKeur(partner.annual)}
              </td>
            ))}
            <td className="w-0 p-0" style={{ borderLeft: '2px solid #1E3A5F', padding: 0 }} />
            {isAnnualMode
              ? planYears.map((yr) => {
                  const isActive = yr === activePlanYear;
                  let displayAnnual: number;
                  if (yr === activePlanYear) {
                    displayAnnual = pAnnual;
                  } else {
                    const yrPos = (positionsByCodeAllYears[yr] ?? positionsByCode)[row.line_code];
                    const yrPartner = yrPos?.partners?.find((p) => p.partner_id === partner.partner_id);
                    displayAnnual = yrPartner?.annual ?? 0;
                  }
                  return (
                    <td key={`p${yr}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums whitespace-nowrap"
                      style={{ minWidth: 72, background: isActive ? 'rgba(30,58,95,0.07)' : 'rgba(30,58,95,0.03)', color: '#1E3A5F' }}>
                      {fmtKeur(displayAnnual)}
                    </td>
                  );
                })
              : pMonths.map((mv: number, i: number) => (
                  <td key={`p${i}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums whitespace-nowrap"
                    style={{ minWidth: 72, background: 'rgba(30,58,95,0.03)', color: '#1E3A5F' }}>
                    <PlanMonthCell
                      eurValue={mv}
                      editable={monthlyEditable && !isAnnualMode && editable}
                      disabled={rateInputsDisabled}
                      onCommitKeur={(keur) => {
                        const months = [...pMonths];
                        months[i] = keur * 1000;
                        onOverride(row.line_code, {
                          ...ov,
                          partners: {
                            ...(ov.partners ?? {}),
                            [partner.partner_id]: {
                              ...pOv,
                              months,
                              annual: months.reduce((s, v) => s + v, 0),
                            },
                          },
                        });
                      }}
                    />
                  </td>
                ))
            }
          </tr>,
        );
      };

      if (isSandboxBlank && partnerRateLevel === 'group') {
        for (const group of groupPartnersByRank(partners)) {
          const groupOv = ov.partnerGroups?.[group.id] ?? {};
          const groupBase = group.partners.reduce(
            (s, p) => s + resolvePartnerPlanningBaseAnnual(p.annual, p.partner_id, pos, lineBaseCtx),
            0,
          );
          nodes.push(
            <tr key={`${expandId}|g|${group.id}`} className="border-b border-slate-100 bg-slate-100/50">
              <td
                className="py-1.5 text-left align-middle overflow-hidden"
                style={{ ...stickyPosition, background: STICKY_BG_GROUP_ROW, paddingLeft: pad + 14, paddingRight: 8 }}
              >
                <span className="block text-xs font-semibold text-slate-700 truncate">{group.label}</span>
              </td>
              <td
                className="py-1 px-2 align-middle"
                style={{ ...stickyRate, background: STICKY_BG_GROUP_ROW, borderRight: '2px solid #CBD5E1' }}
              >
                <SandboxBlankRateCell
                  baseAnnual={groupBase}
                  state={groupOv}
                  pos={pos}
                  disabled={rateInputsDisabled}
                  onChange={(next) =>
                    onOverride(row.line_code, {
                      ...ov,
                      partnerGroups: { ...(ov.partnerGroups ?? {}), [group.id]: { ...groupOv, ...next } },
                    })
                  }
                />
              </td>
              {Array.from({ length: histCount }, (_, i) => (
                <td key={`gh${i}`} className="py-1.5 px-2 text-right text-xs text-slate-400">—</td>
              ))}
              <td className="w-0 p-0" style={{ borderLeft: '2px solid #1E3A5F', padding: 0 }} />
              {isAnnualMode
                ? planYears.map((yr) => {
                    const isActive = yr === activePlanYear;
                    const groupAnnual = group.partners.reduce((s, p) => {
                      const pBase = resolvePartnerPlanningBaseAnnual(p.annual, p.partner_id, pos, lineBaseCtx);
                      if (yr !== activePlanYear) return s + pBase;
                      return s + resolveChildAnnual(pBase, groupOv);
                    }, 0);
                    return (
                      <td key={`p${yr}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums whitespace-nowrap"
                        style={{ minWidth: 72, background: isActive ? 'rgba(30,58,95,0.07)' : 'rgba(30,58,95,0.03)', color: '#1E3A5F', fontWeight: 600 }}>
                        {fmtKeur(groupAnnual)}
                      </td>
                    );
                  })
                : (() => {
                    const groupMonths = Array(12).fill(0);
                    for (const p of group.partners) {
                      const pBase = resolvePartnerPlanningBaseAnnual(p.annual, p.partner_id, pos, lineBaseCtx);
                      const pm = resolveChildMonths(pBase, groupOv, pos);
                      for (let i = 0; i < 12; i++) groupMonths[i] += pm[i];
                    }
                    return groupMonths.map((mv, i) => (
                      <td key={`p${i}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums whitespace-nowrap"
                        style={{ minWidth: 72, background: 'rgba(30,58,95,0.05)', color: '#1E3A5F', fontWeight: 600 }}>
                        {fmtK(mv / 1000)}
                      </td>
                    ));
                  })()
              }
            </tr>,
          );
          for (const partner of group.partners) {
            renderPartnerRow(partner, 28, false, groupOv);
          }
        }
      } else {
        for (const partner of partners) {
          renderPartnerRow(partner, 20, !isSandboxBlank || partnerRateLevel === 'partner');
        }
      }

      // Other (residual) — legacy path only (sandbox uses full partner list)
      if (!isSandboxBlank) {
      const partnersSum = (pos?.partners ?? []).reduce((sum, p) => {
        const pOv2 = ov.partners?.[p.partner_id] ?? {};
        if (isSandboxBlank) {
          if (partnerRateLevel === 'group') {
            const groups = groupPartnersByRank(pos?.partners ?? []);
            const group = groups.find((g) => g.partners.some((pp) => pp.partner_id === p.partner_id));
            const groupOv = group ? ov.partnerGroups?.[group.id] : undefined;
            return sum + resolveChildAnnual(p.annual, groupOv);
          }
          return sum + resolveChildAnnual(p.annual, pOv2);
        }
        return sum + (pOv2.annual !== undefined ? pOv2.annual : p.annual);
      }, 0);
      const totalPlan = isAnnualMode
        ? (planAnnualMapsByYear[activePlanYear]?.[row.line_code] ?? 0)
        : planMonths.reduce((s: number, v: number) => s + v, 0);
      const other = totalPlan - partnersSum;
      nodes.push(
        <tr key={`${expandId}|other`} className="border-b border-slate-100 bg-slate-50/20">
          <td
            className="py-1.5 text-left align-middle overflow-hidden"
            style={{ ...stickyPosition, background: STICKY_BG_SUBROW, paddingLeft: pad + 20, paddingRight: 8 }}
          >
            <span className="block text-xs text-slate-400 italic">Other (residual)</span>
          </td>
          <td style={{ ...stickyRate, background: STICKY_BG_SUBROW, borderRight: '2px solid #CBD5E1' }} />
          {Array.from({ length: histCount }, (_, i) => (
            <td key={`h${i}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums text-slate-300 whitespace-nowrap"
              style={{ minWidth: 72 }}>—</td>
          ))}
          <td className="w-0 p-0" style={{ borderLeft: '2px solid #1E3A5F', padding: 0 }} />
          {isAnnualMode
            ? planYears.map((yr) => {
                const isActive = yr === activePlanYear;
                // GAP 2 FIX: compute per-year residual from that year's total and partners
                const yrTotal = planAnnualMapsByYear[yr]?.[row.line_code] ?? 0;
                const yrPartnersSum = (pos?.partners ?? []).reduce((sum, p) => {
                  if (yr === activePlanYear) {
                    const pOv2 = ov.partners?.[p.partner_id] ?? {};
                    return sum + (pOv2.annual !== undefined ? pOv2.annual : p.annual);
                  }
                  const yrPos = (positionsByCodeAllYears[yr] ?? positionsByCode)[row.line_code];
                  const yrPartner = yrPos?.partners?.find((pp) => pp.partner_id === p.partner_id);
                  return sum + (yrPartner?.annual ?? 0);
                }, 0);
                const yrOther = yrTotal - yrPartnersSum;
                return (
                  <td key={`p${yr}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums text-slate-400 whitespace-nowrap"
                    style={{ minWidth: 72, background: isActive ? 'rgba(30,58,95,0.07)' : 'rgba(30,58,95,0.03)' }}>
                    {fmtKeur(yrOther)}
                  </td>
                );
              })
            : Array.from({ length: 12 }, (_, i) => (
                <td key={`p${i}`} className="py-1.5 px-2 text-right text-xs font-mono tabular-nums text-slate-400 whitespace-nowrap"
                  style={{ minWidth: 72, background: 'rgba(30,58,95,0.03)' }}>
                  {i === 0 ? fmtK(other / 1000) : ''}
                </td>
              ))
          }
        </tr>,
      );
      }
    }

    return nodes;
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="relative flex flex-col gap-0 rounded-xl border border-slate-200 bg-white overflow-hidden">
      {/* View header */}
      <div
        className="flex items-center justify-between px-4 py-2.5 border-b border-[#E2E8F0] shrink-0"
        style={{ background: '#F8FAFC' }}
      >
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-xs font-semibold text-slate-600 uppercase tracking-wider">
            {statement === 'PL' ? 'Income Statement' : 'Balance Sheet'} — Planning View
          </span>
          <span
            className="inline-flex items-center rounded px-2 py-0.5 text-[10px] font-semibold"
            style={{ background: 'rgba(30,58,95,0.1)', color: '#1E3A5F' }}
          >
            {isAnnualMode ? `${planYears.map(fyLabel).join(' / ')}` : planFYLabel}
          </span>
          {draft.start.mode === 'heuristic' && heuristic && (
            <span className="text-[10px] text-slate-400 capitalize">
              {heuristic.replace('_', ' ')} heuristic
            </span>
          )}
          <span
            className="inline-flex items-center rounded px-2 py-0.5 text-[10px] font-medium"
            style={{ background: isAnnualMode ? 'rgba(30,58,95,0.08)' : 'rgba(16,185,129,0.08)', color: isAnnualMode ? '#1E3A5F' : '#065F46' }}
          >
            {isAnnualMode ? 'Annual' : 'Monthly'}
          </span>

          {/* Year selector tab bar — shown in header when >1 plan year */}
          {showYearSelector && (
            <div className="flex items-center gap-1 ml-2">
              <span className="text-[10px] text-slate-400 mr-1">
                {isAnnualMode ? 'Active:' : 'Showing:'}
              </span>
              {planYears.map((yr) => (
                <button
                  key={yr}
                  type="button"
                  onClick={() => onSetActivePlanYear(yr)}
                  className={[
                    'rounded px-2 py-0.5 text-[10px] font-medium transition-colors',
                    yr === activePlanYear
                      ? 'text-white'
                      : 'text-slate-500 hover:text-[#1E3A5F] border border-slate-200 bg-white',
                  ].join(' ')}
                  style={yr === activePlanYear ? { background: '#1E3A5F' } : {}}
                >
                  {fyLabel(yr)}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Undo */}
          <button
            type="button"
            onClick={onUndo}
            disabled={!canUndo || saving}
            title="Undo"
            className="rounded p-1 text-slate-500 hover:text-[#1E3A5F] hover:bg-slate-100 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <Undo2 size={14} aria-hidden />
          </button>
          {/* Redo */}
          <button
            type="button"
            onClick={onRedo}
            disabled={!canRedo || saving}
            title="Redo"
            className="rounded p-1 text-slate-500 hover:text-[#1E3A5F] hover:bg-slate-100 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <Redo2 size={14} aria-hidden />
          </button>

          <div className="w-px h-4 bg-slate-200 mx-1" />

          <button
            type="button"
            onClick={onEditSetup}
            disabled={saving}
            className="rounded-lg border border-[#1E3A5F] px-3 py-1 text-xs font-medium text-[#1E3A5F] hover:bg-[#1E3A5F] hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Edit setup
          </button>

          {isSandboxBlank && onOpenMonthlyAdjust && !monthlyEditable && !ratesFrozen && (
            <button
              type="button"
              onClick={onOpenMonthlyAdjust}
              disabled={saving}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 transition-colors disabled:opacity-50"
            >
              <CalendarDays size={12} aria-hidden />
              Adjust months
            </button>
          )}

          {monthlyEditable && (
            <span className="text-[10px] text-emerald-700 font-medium px-2 py-0.5 rounded bg-emerald-50 border border-emerald-200">
              Edit months (kEUR) inline
            </span>
          )}

          {!saveDone && !ratesFrozen && (
            <button
              type="button"
              onClick={onSave}
              disabled={saving}
              className="rounded-lg px-3 py-1 text-xs font-semibold text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ background: '#1E3A5F' }}
            >
              {saving ? 'Saving…' : 'Save budget'}
            </button>
          )}

          {ratesFrozen && onCommit && !commitDone && (
            <button
              type="button"
              onClick={onCommit}
              disabled={commitLoading}
              className="rounded-lg px-3 py-1 text-xs font-semibold text-white transition-colors disabled:opacity-50"
              style={{ background: '#065F46' }}
            >
              {commitLoading ? 'Committing…' : 'Commit to reporting'}
            </button>
          )}

          {commitDone && (
            <span className="text-[10px] font-medium text-emerald-700 px-2 py-1 rounded bg-emerald-50">
              Committed to reporting
            </span>
          )}
        </div>
      </div>

      {/* Scrollable table area */}
      <div className="overflow-x-auto overflow-y-auto" style={{ maxHeight: 'calc(100vh - 220px)' }}>
        <table
          className="border-collapse text-xs"
          style={{
            tableLayout: 'fixed',
            minWidth: POSITION_COL_WIDTH + RATE_COL_WIDTH + histCount * 80 + planColCount * 80 + 20,
          }}
        >
          {/* Column group: enforce min widths */}
          <colgroup>
            <col style={{ width: POSITION_COL_WIDTH, minWidth: POSITION_COL_WIDTH }} />
            <col style={{ width: RATE_COL_WIDTH, minWidth: RATE_COL_WIDTH }} />
            {Array.from({ length: histCount }, (_, i) => <col key={`hc${i}`} style={{ width: 80, minWidth: 72 }} />)}
            <col style={{ width: 4 }} />
            {Array.from({ length: planColCount }, (_, i) => <col key={`pc${i}`} style={{ width: 80, minWidth: 72 }} />)}
          </colgroup>

          <thead style={{ position: 'sticky', top: 0, zIndex: 4 }}>
            {/* Group header row */}
            <tr style={{ background: '#F8FAFC', borderBottom: '1px solid #E2E8F0' }}>
              <th
                className="py-1.5 text-left font-semibold text-slate-500 text-[10px] whitespace-nowrap"
                style={{ ...stickyPosition, background: STICKY_BG_HEADER, paddingLeft: 10, paddingRight: 8, zIndex: 5 }}
              >
                Position
              </th>
              <th
                className="py-1.5 text-center font-semibold text-slate-500 text-[10px] whitespace-nowrap"
                style={{ ...stickyRate, background: STICKY_BG_HEADER, borderRight: '2px solid #CBD5E1', zIndex: 5 }}
              >
                Rate
              </th>
              {/* Historical group header */}
              {histCount > 0 && (
                <th
                  colSpan={histCount}
                  className="py-1.5 text-center font-semibold text-[10px] whitespace-nowrap"
                  style={{ background: '#F8FAFC', color: '#64748B', borderBottom: '1px solid #CBD5E1' }}
                >
                  {isAnnualMode ? 'Actual History (kEUR)' : 'Actual History (kEUR)'}
                </th>
              )}
              {/* Divider */}
              <th style={{ width: 4, background: '#F8FAFC', borderLeft: '2px solid #1E3A5F' }} />
              {/* Plan group header */}
              <th
                colSpan={planColCount}
                className="py-1.5 text-center font-bold text-[10px] whitespace-nowrap"
                style={{ background: 'rgba(30,58,95,0.06)', color: '#1E3A5F', borderBottom: '1px solid rgba(30,58,95,0.2)' }}
              >
                {isAnnualMode
                  ? `Plan ${planYears.map(fyLabel).join(' / ')} (kEUR)`
                  : `${planFYLabel} — Budget (kEUR)`
                }
              </th>
            </tr>
            {/* Period label row */}
            <tr style={{ background: '#F8FAFC', borderBottom: '2px solid #E2E8F0' }}>
              <th
                className="py-1 text-left font-medium text-slate-400 text-[10px]"
                style={{ ...stickyPosition, background: STICKY_BG_HEADER, paddingLeft: 10, paddingRight: 8, zIndex: 5 }}
              />
              <th
                className="py-1 align-middle"
                style={{ ...stickyRate, background: STICKY_BG_HEADER, borderRight: '2px solid #CBD5E1', zIndex: 5 }}
              />
              {histPeriods.map((p) => (
                <th
                  key={p.key}
                  className="py-1 px-2 text-right font-medium text-slate-400 text-[10px] whitespace-nowrap"
                  style={{ minWidth: 72 }}
                >
                  {p.label}
                </th>
              ))}
              {/* Divider */}
              <th style={{ width: 4, borderLeft: '2px solid #1E3A5F', background: '#F8FAFC' }} />
              {/* Plan column labels */}
              {isAnnualMode
                ? planYears.map((yr) => {
                    const isActive = yr === activePlanYear;
                    return (
                      <th
                        key={yr}
                        className="py-1 px-2 text-right font-medium text-[10px] whitespace-nowrap"
                        style={{
                          minWidth: 72,
                          background: isActive ? 'rgba(30,58,95,0.08)' : 'rgba(30,58,95,0.03)',
                          color: isActive ? '#1E3A5F' : '#64748B',
                          fontWeight: isActive ? 700 : 500,
                        }}
                      >
                        {fyLabel(yr)}
                      </th>
                    );
                  })
                : planMonthLabels.map((lbl) => (
                    <th
                      key={lbl}
                      className="py-1 px-2 text-right font-medium text-[10px] whitespace-nowrap"
                      style={{ minWidth: 72, background: 'rgba(30,58,95,0.03)', color: '#1E3A5F' }}
                    >
                      {lbl}
                    </th>
                  ))
              }
            </tr>
          </thead>

          <tbody>
            {granularityRows.flatMap((row) => renderRow(row))}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div
        className="px-4 py-2 border-t border-slate-100 text-xs text-slate-400 shrink-0"
        style={{ background: '#F8FAFC' }}
      >
        All values kEUR.
        {isAnnualMode
          ? ' Annual mode: one column per plan year. Rate column edits the active year (highlighted).'
          : ' Rate column drives the plan — edit then Save. Subtotals recompute live from position values.'
        }
        {' '}Historical columns equal the Reporting Income Statement / Balance Sheet.
        {/* Suppress unused var */}
        {totalCols > 0 ? null : null}
      </div>

      {commitLoading && (
        <div
          className="absolute inset-0 z-50 flex flex-col items-center justify-center gap-4 rounded-xl bg-white/90 backdrop-blur-sm"
          role="status"
          aria-live="polite"
        >
          <div
            className="h-10 w-10 animate-spin rounded-full border-4 border-slate-200 border-t-emerald-700"
            aria-hidden
          />
          <div className="text-center px-6">
            <p className="text-sm font-semibold text-slate-800">Committing budget to reporting…</p>
            <p className="text-xs text-slate-500 mt-1 max-w-sm">
              Overwriting plan values in the reporting layer. This may take a while for large entities.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
