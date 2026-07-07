/**
 * BudgetChatPage — /budget route.
 *
 * Phase 4: Final View from draft (incl. Both statements), single whole-draft
 * Save, Edit setup / Reset, and loop for the next entity / statement.
 *
 * Entity dimension: draft.entities is either [''] (consolidated) or
 * ['DE', 'FR', ...] (per-entity). Final View fetches a budget tree per
 * (entity, statement) pair and keys overrides by `${entity}|${statement}`.
 *
 * Phase 5 additions:
 *  - StructuredBudgetView replaces BudgetTreeGrid in the Final View.
 *  - getBudgetGranularityView fetched per (entity, statement) to get the
 *    full IS/BS structure (rows with kind='line'|'subtotal'|'grandtotal').
 *  - Undo/redo history stack over {draft, overridesMap} snapshots.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { motion } from 'framer-motion';
import BudgetChat from '../components/budget/chat/BudgetChat';
import ChatSummaryBar from '../components/budget/chat/ChatSummaryBar';
import SaveProgress from '../components/budget/chat/SaveProgress';
import StructuredBudgetView from '../components/budget/StructuredBudgetView';
import BudgetMonthlyAdjustModal from '../components/budget/BudgetMonthlyAdjustModal';
import PlanVersionPanel from '../components/budget/PlanVersionPanel';
import type { PositionOverride } from '../components/budget/BudgetGrid';
import { IS_REPORTING_V2_SANDBOX } from '../lib/reportingV2SandboxMode';
import { groupPartnersByRank } from '../lib/budgetPartnerGroups';
import { enrichPositionsByCode } from '../lib/budgetPartnerResolve';
import {
  initialDraft,
  applyAnswer,
  nextStep,
  formatEntities,
  formatFiscalYears,
  fyLabel,
  type BudgetDraft,
  type StepId,
  type PositionGranularity,
  type PositionKey,
} from '../lib/budgetChatFlow';
import {
  budgetEntities,
  getBudgetTree,
  getBudgetGranularityView,
  budgetUpload,
  downloadBudgetTemplate,
  patchBudgetPosition,
  deleteBudget,
  type BudgetUploadPreview,
  type BudgetPosition,
  type BudgetGranularityRow,
  type BudgetGranularityPeriod,
} from '../lib/gdpduApi';
import { runWithConcurrency } from '../lib/budgetFetchPool';

// ---------------------------------------------------------------------------
// Undo/redo history snapshot
// ---------------------------------------------------------------------------

interface HistorySnapshot {
  draft: BudgetDraft;
  overridesMap: Record<string, Record<string, PositionOverride>>;
}

// ---------------------------------------------------------------------------
// Fiscal year options helper
// ---------------------------------------------------------------------------

function buildFiscalYearOptions(currentYear: number) {
  const years: { value: string; label: string }[] = [];
  // currentYear-1 .. currentYear+5 so e.g. FY25–FY31 are all available
  for (let y = currentYear - 1; y <= currentYear + 5; y++) {
    years.push({ value: String(y), label: fyLabel(y) });
  }
  return years;
}

// ---------------------------------------------------------------------------
// Save-progress state
// ---------------------------------------------------------------------------

interface SaveState {
  running: boolean;
  step: number;
  total: number;
  currentLabel: string;
  error?: string;
  done: boolean;
}

const SAVE_IDLE: SaveState = {
  running: false,
  step: 0,
  total: 0,
  currentLabel: '',
  done: false,
};

// ---------------------------------------------------------------------------
// Override key: "${entity}|${year}|${statement}"
// entity = '' for consolidated, 'DE' etc. for specific.
// ---------------------------------------------------------------------------

function overrideKey(entity: string, year: number, statement: 'PL' | 'BS'): string {
  return `${entity}|${year}|${statement}`;
}

/** Blank / Excel grids skip backend heuristics (one DB round-trip per position saved). */
function isBudgetLightMode(draft: BudgetDraft): boolean {
  return draft.start.mode === 'blank' || draft.start.mode === 'excel';
}

function treeFetchParams(
  draft: BudgetDraft,
  entity: string,
  year: number,
  stmt: 'PL' | 'BS',
  signal?: AbortSignal,
) {
  const light = isBudgetLightMode(draft);
  return {
    statement: stmt,
    fiscal_year: year,
    entity: entity || undefined,
    level: 'L4' as const,
    heuristic: !light && draft.start.mode === 'heuristic' ? draft.start.heuristic : undefined,
    growth_pct: draft.start.growthPct,
    top_n: draft.partner.topN,
    light,
    signal,
  };
}

// ---------------------------------------------------------------------------
// Sandbox blank: seed overrides with zero growth per granularity
// ---------------------------------------------------------------------------

function buildBlankOverrides(
  triples: Array<{ entity: string; year: number; stmt: 'PL' | 'BS' }>,
  posMap: Record<string, BudgetPosition[]>,
  draft: BudgetDraft,
): Record<string, Record<string, PositionOverride>> {
  if (!IS_REPORTING_V2_SANDBOX || draft.start.mode !== 'blank') return {};

  const rawByYearStmt = new Map<string, Record<number, Record<string, BudgetPosition>>>();
  for (const { entity, year, stmt } of triples) {
    const stmtKey = `${entity}|${stmt}`;
    if (!rawByYearStmt.has(stmtKey)) rawByYearStmt.set(stmtKey, {});
    const byYear = rawByYearStmt.get(stmtKey)!;
    byYear[year] = Object.fromEntries((posMap[overrideKey(entity, year, stmt)] ?? []).map((p) => [p.line_code, p]));
  }

  const result: Record<string, Record<string, PositionOverride>> = {};
  for (const { entity, year, stmt } of triples) {
    const key = overrideKey(entity, year, stmt);
    const rawPositions = Object.fromEntries((posMap[key] ?? []).map((p) => [p.line_code, p]));
    const allYears = rawByYearStmt.get(`${entity}|${stmt}`) ?? {};
    const positions = Object.values(enrichPositionsByCode(rawPositions, allYears, year));
    const lineOverrides: Record<string, PositionOverride> = {};

    for (const pos of positions) {
      const gran = draft.granularityByPosition[`${stmt}|${pos.line_code}`] ?? 'L3';
      if (gran === 'L3') {
        lineOverrides[pos.line_code] = { growthPct: 0, annual: pos.annual };
      } else if (gran === 'L4' && pos.children?.length) {
        const l4: PositionOverride['l4'] = {};
        for (const c of pos.children) {
          l4[c.level_4] = { growthPct: 0, annual: c.annual };
        }
        lineOverrides[pos.line_code] = { l4 };
      } else if ((gran === 'customers' || gran === 'suppliers') && pos.partners?.length) {
        const partners: PositionOverride['partners'] = {};
        const partnerGroups: PositionOverride['partnerGroups'] = {};
        for (const p of pos.partners) {
          partners[p.partner_id] = { growthPct: 0, annual: p.annual };
        }
        for (const g of groupPartnersByRank(pos.partners)) {
          partnerGroups[g.id] = { growthPct: 0 };
        }
        lineOverrides[pos.line_code] = { partners, partnerGroups };
      }
    }

    if (Object.keys(lineOverrides).length > 0) {
      result[key] = lineOverrides;
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Build patchBudgetPosition payloads from the draft + overrides for ONE
// (entity, statement) pair.
// ---------------------------------------------------------------------------

function buildPatchPayloads(
  draft: BudgetDraft,
  entity: string,
  year: number,
  statement: 'PL' | 'BS',
  overrides: Record<string, PositionOverride>,
  positions: BudgetPosition[],
): Array<{
  lineCode: string;
  label: string;
  entityLabel: string;
  payload: Parameters<typeof patchBudgetPosition>[0];
}> {
  const payloads: ReturnType<typeof buildPatchPayloads> = [];
  const entityLabel = entity === '' ? 'Consolidated' : entity;

  for (const pos of positions) {
    const draftVal = draft.positions[pos.line_code];
    const ov = overrides[pos.line_code] ?? {};

    // Resolve per-position granularity (defaults to 'L3' if not set)
    const posGranularity: PositionGranularity =
      draft.granularityByPosition[`${statement}|${pos.line_code}`] ?? 'L3';

    // GAP 1 FIX: read per-year values first, then fall back to top-level annual/months,
    // then fall back to draft suggestion.  This ensures each (entity, year, statement)
    // triple persists its own edited rate/value rather than the last-edited year.
    const effectiveAnnual =
      ov.annualByYear?.[year] !== undefined
        ? ov.annualByYear[year]
        : ov.annual !== undefined
        ? ov.annual
        : draftVal?.annual !== undefined
        ? draftVal.annual
        : null;

    const effectiveMonths =
      ov.monthsByYear?.[year] !== undefined
        ? ov.monthsByYear[year]
        : ov.months !== undefined
        ? ov.months
        : draftVal?.months !== undefined
        ? draftVal.months
        : null;

    const hasPositionValue = effectiveAnnual !== null || effectiveMonths !== null;

    // ── Partner payloads (when granularity is 'customers' or 'suppliers') ──
    const partnerPayloads: Array<{ partner_id: string; annual?: number; months?: number[] }> = [];
    if (
      (posGranularity === 'customers' || posGranularity === 'suppliers') &&
      pos.is_partner_driven &&
      pos.partners
    ) {
      for (const p of pos.partners) {
        const pOv = ov.partners?.[p.partner_id];
        const pDraft = draft.partner.values?.[p.partner_id];
        const pAnnual =
          pOv?.annual !== undefined
            ? pOv.annual
            : pDraft?.annual !== undefined
            ? pDraft.annual
            : null;
        const pMonths =
          pOv?.months !== undefined
            ? pOv.months
            : pDraft?.months !== undefined
            ? pDraft.months
            : null;
        if (pAnnual !== null || pMonths !== null) {
          partnerPayloads.push({
            partner_id: p.partner_id,
            ...(pAnnual !== null ? { annual: pAnnual } : {}),
            ...(pMonths !== null ? { months: pMonths } : {}),
          });
        }
      }
    }

    // ── L4 child payloads (when granularity is 'L4') ──
    if (posGranularity === 'L4') {
      const l4Entries = Object.entries(ov.l4 ?? {});
      for (const [level4Key, l4Ov] of l4Entries) {
        if (l4Ov.annual !== undefined || l4Ov.months !== undefined) {
          payloads.push({
            lineCode: `${pos.line_code}/${level4Key}`,
            label: `${pos.label} / ${level4Key}`,
            entityLabel,
            payload: {
              statement,
              line_code: pos.line_code,
              entity: entity || undefined,
              fiscal_year: year,
              level_4: level4Key,
              position: {
                ...(l4Ov.annual !== undefined ? { annual: l4Ov.annual } : {}),
                ...(l4Ov.months !== undefined ? { months: l4Ov.months } : {}),
              },
            },
          });
        }
      }
      // For L4 granularity we do NOT write the L3 position row
      if (Object.keys(ov.l4 ?? {}).length === 0 && !hasPositionValue) continue;
    }

    // ── L3 position payload (granularity 'L3', or L4 with a direct override) ──
    if (posGranularity === 'L3' || posGranularity === 'L4') {
      if (!hasPositionValue && partnerPayloads.length === 0) continue;
      if (posGranularity === 'L3') {
        // Only write position-level row for L3
        payloads.push({
          lineCode: pos.line_code,
          label: pos.label,
          entityLabel,
          payload: {
            statement,
            line_code: pos.line_code,
            entity: entity || undefined,
            fiscal_year: year,
            ...(hasPositionValue
              ? {
                  position: {
                    ...(effectiveAnnual !== null ? { annual: effectiveAnnual } : {}),
                    ...(effectiveMonths !== null ? { months: effectiveMonths } : {}),
                  },
                }
              : {}),
          },
        });
      }
    } else if (posGranularity === 'customers' || posGranularity === 'suppliers') {
      // Partner granularity — write partners payload (position row omitted; derived server-side)
      if (partnerPayloads.length === 0) continue;
      payloads.push({
        lineCode: pos.line_code,
        label: pos.label,
        entityLabel,
        payload: {
          statement,
          line_code: pos.line_code,
          entity: entity || undefined,
          fiscal_year: year,
          partners: partnerPayloads,
        },
      });
    }
  }

  return payloads;
}

// ---------------------------------------------------------------------------
// BudgetChatPage
// ---------------------------------------------------------------------------

export default function BudgetChatPage() {
  const currentYear = new Date().getFullYear();
  const fiscalYearOptions = buildFiscalYearOptions(currentYear);

  // ── Draft + navigation state ──
  const [draft, setDraft] = useState<BudgetDraft>(initialDraft);
  const [currentStepId, setCurrentStepId] = useState<StepId>('entity');
  const [chatComplete, setChatComplete] = useState(false);

  // ── Entity fetch ──
  const [entityOptions, setEntityOptions] = useState<{ value: string; label: string }[]>([]);
  const [entityLoading, setEntityLoading] = useState(true);
  const [entityError, setEntityError] = useState<string | null>(null);

  const loadEntities = useCallback(() => {
    const controller = new AbortController();
    setEntityLoading(true);
    setEntityError(null);
    budgetEntities(controller.signal)
      .then((res) => {
        if (controller.signal.aborted) return;
        const opts = res.entities.map((e) => ({ value: e.code, label: e.label }));
        if (res.can_consolidate) {
          opts.unshift({ value: '', label: 'Consolidated' });
        }
        setEntityOptions(opts);
        setEntityError(null);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const msg = err instanceof Error ? err.message : 'Failed to load entities';
        setEntityError(msg);
      })
      .finally(() => {
        if (!controller.signal.aborted) setEntityLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const abort = loadEntities();
    return abort;
  }, [loadEntities]);

  // ── Heuristic fetch state ──
  const [heuristicLoading, setHeuristicLoading] = useState(false);
  const [heuristicPreview, setHeuristicPreview] = useState<{ positionCount: number } | null>(null);
  const [heuristicPositions, setHeuristicPositions] = useState<BudgetPosition[]>([]);

  // ── Excel upload state ──
  const [uploadLoading, setUploadLoading] = useState(false);
  const [uploadError, setUploadError] = useState<string | undefined>(undefined);
  const [uploadPreview, setUploadPreview] = useState<BudgetUploadPreview | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  // ── Partner heuristic state ──
  const [partnerHeuristicLoading] = useState(false);
  const [partnerHeuristicPreview, setPartnerHeuristicPreview] = useState<{
    partnerCount: number;
  } | null>(null);

  // ── Final view: positions keyed by "${entity}|${year}|${statement}" ──
  const [finalPositionsMap, setFinalPositionsMap] = useState<
    Record<string, BudgetPosition[]>
  >({});
  const [finalLoading, setFinalLoading] = useState(false);
  const [finalError, setFinalError] = useState<string | null>(null);
  const [activeStatement, setActiveStatement] = useState<'PL' | 'BS'>('PL');
  const [activeEntity, setActiveEntity] = useState<string>('');
  const [activeYear, setActiveYear] = useState<number>(new Date().getFullYear() + 1);

  // ── Per-(entity,year,statement) overrides keyed by "${entity}|${year}|${statement}" ──
  const [overridesMap, setOverridesMap] = useState<
    Record<string, Record<string, PositionOverride>>
  >({});

  // ── Active plan year for year selector + per-year rate editing ──
  const [activePlanYear, setActivePlanYear] = useState<number>(new Date().getFullYear() + 1);

  // ── Granularity view data (IS/BS structure): keyed by "${entity}|${statement}" ──
  const [granularityViewMap, setGranularityViewMap] = useState<
    Record<string, { rows: BudgetGranularityRow[]; periods: BudgetGranularityPeriod[] }>
  >({});
  const [granularityViewLoading, setGranularityViewLoading] = useState(false);
  const [granularityViewError, setGranularityViewError] = useState<string | null>(null);

  const [monthlyAdjustOpen, setMonthlyAdjustOpen] = useState(false);

  // ── Undo/redo history ──
  const [historyStack, setHistoryStack] = useState<HistorySnapshot[]>([]);
  const [historyIdx, setHistoryIdx] = useState<number>(-1);
  // Suppress pushing to history when applying a history jump
  const applyingHistoryRef = useRef(false);

  // ── Save state ──
  const [saveState, setSaveState] = useState<SaveState>(SAVE_IDLE);
  const saveAbortRef = useRef(false);

  /** Sandbox-only: editing → saved (freeze rates) → committing → committed */
  const [sandboxPhase, setSandboxPhase] = useState<'editing' | 'saved' | 'committing' | 'committed'>('editing');

  // ── Reset confirmation ──
  const [resetConfirm, setResetConfirm] = useState(false);
  const [resetLoading, setResetLoading] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  // ── Loop: show "Plan another?" after save ──
  const [loopOffered, setLoopOffered] = useState(false);

  // ---------------------------------------------------------------------------
  // Derived helpers
  // ---------------------------------------------------------------------------

  const activeKey = overrideKey(activeEntity, activeYear, activeStatement);
  const activeOverrides: Record<string, PositionOverride> = overridesMap[activeKey] ?? {};

  // Granularity view key for current active (entity, statement) — year-independent
  const activeGranularityKey = `${activeEntity}|${activeStatement}`;
  const activeGranularityView = granularityViewMap[activeGranularityKey] ?? { rows: [], periods: [] };

  const rawPositionsByCodeAllYears: Record<number, Record<string, BudgetPosition>> = Object.fromEntries(
    draft.fiscalYears.map((yr) => [
      yr,
      Object.fromEntries(
        (finalPositionsMap[overrideKey(activeEntity, yr, activeStatement)] ?? []).map((p) => [p.line_code, p]),
      ),
    ]),
  ) as Record<number, Record<string, BudgetPosition>>;

  const positionsByCodeAllYears: Record<number, Record<string, BudgetPosition>> = Object.fromEntries(
    draft.fiscalYears.map((yr) => [
      yr,
      enrichPositionsByCode(rawPositionsByCodeAllYears[yr] ?? {}, rawPositionsByCodeAllYears, yr),
    ]),
  ) as Record<number, Record<string, BudgetPosition>>;

  const activePositionsByCode: Record<string, BudgetPosition> =
    positionsByCodeAllYears[activePlanYear] ?? {};

  // ---------------------------------------------------------------------------
  // History push helper
  // ---------------------------------------------------------------------------

  // historyIdxRef keeps a synchronous shadow of historyIdx so pushHistory
  // can read the current value even before the state update batches flush.
  const historyIdxRef = useRef(-1);
  historyIdxRef.current = historyIdx;

  function pushHistory(newDraft: BudgetDraft, newOverridesMap: Record<string, Record<string, PositionOverride>>) {
    if (applyingHistoryRef.current) return;
    const currentIdx = historyIdxRef.current;
    setHistoryStack((prev) => {
      // Truncate redo branch if we're not at the tip
      const base = currentIdx >= 0 ? prev.slice(0, currentIdx + 1) : prev;
      const next = [...base, { draft: newDraft, overridesMap: newOverridesMap }];
      // Cap at 50 snapshots
      return next.length > 50 ? next.slice(next.length - 50) : next;
    });
    setHistoryIdx((prev) => {
      const capped = Math.min(prev + 1, 49);
      historyIdxRef.current = capped;
      return capped;
    });
  }

  // ---------------------------------------------------------------------------
  // Undo / redo
  // ---------------------------------------------------------------------------

  const canUndo = historyIdx > 0;
  const canRedo = historyIdx < historyStack.length - 1;

  const handleUndo = useCallback(() => {
    setHistoryIdx((prev) => {
      const next = prev - 1;
      if (next < 0) return prev;
      applyingHistoryRef.current = true;
      const snap = historyStack[next];
      if (snap) {
        setDraft(snap.draft);
        setOverridesMap(snap.overridesMap);
      }
      setTimeout(() => { applyingHistoryRef.current = false; }, 0);
      return next;
    });
  }, [historyStack]);

  const handleRedo = useCallback(() => {
    setHistoryIdx((prev) => {
      const next = prev + 1;
      if (next >= historyStack.length) return prev;
      applyingHistoryRef.current = true;
      const snap = historyStack[next];
      if (snap) {
        setDraft(snap.draft);
        setOverridesMap(snap.overridesMap);
      }
      setTimeout(() => { applyingHistoryRef.current = false; }, 0);
      return next;
    });
  }, [historyStack]);

  function setActiveOverrides(next: Record<string, PositionOverride>) {
    const newOverridesMap = { ...overridesMap, [activeKey]: next };
    setOverridesMap(newOverridesMap);
    pushHistory(draft, newOverridesMap);
  }

  const handlePartnerRateLevelChange = useCallback(
    (positionKey: PositionKey, level: 'group' | 'partner') => {
      const newDraft: BudgetDraft = {
        ...draft,
        partnerRateLevel: { ...(draft.partnerRateLevel ?? {}), [positionKey]: level },
      };
      setDraft(newDraft);
      pushHistory(newDraft, overridesMap);
    },
    [draft, overridesMap],
  );

  // Entities to show in the entity-axis tab bar (only when >1 selected)
  const entityTabEntities: string[] = draft.entities;
  const showEntityTabs = entityTabEntities.length > 1;

  // Years to show in the year-axis tab bar (only when >1 selected)
  const showYearTabs = draft.fiscalYears.length > 1;

  // Statements to show in the statement tab bar (only when Both selected)
  const showStatementTabs = draft.statements.length === 2;

  // ---------------------------------------------------------------------------
  // Final view: fetch trees + granularity views for every (entity, statement) pair
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (!chatComplete) return;
    const abort = new AbortController();
    const { signal } = abort;

    setFinalLoading(true);
    setFinalError(null);
    setGranularityViewLoading(true);
    setGranularityViewError(null);

    const triples: Array<{ entity: string; year: number; stmt: 'PL' | 'BS' }> = [];
    for (const entity of draft.entities) {
      for (const year of draft.fiscalYears) {
        for (const stmt of draft.statements) {
          triples.push({ entity, year, stmt });
        }
      }
    }

    const grain = draft.viewMode === 'monthly' ? 'month' : ('year' as const);
    const granularityPairs: Array<{ entity: string; stmt: 'PL' | 'BS' }> = [];
    const seenGranKeys = new Set<string>();
    for (const entity of draft.entities) {
      for (const stmt of draft.statements) {
        const gk = `${entity}|${stmt}`;
        if (!seenGranKeys.has(gk)) {
          seenGranKeys.add(gk);
          granularityPairs.push({ entity, stmt });
        }
      }
    }

    const firstEntity = draft.entities[0] ?? '';
    const firstYear = draft.fiscalYears[0] ?? new Date().getFullYear() + 1;
    const firstStmt = draft.statements[0];
    const primaryGranKey = `${firstEntity}|${firstStmt}`;
    const primaryTreeKey = overrideKey(firstEntity, firstYear, firstStmt);

    const fetchGranularity = (entity: string, stmt: 'PL' | 'BS') =>
      getBudgetGranularityView({ statement: stmt, grain, entity, signal }).then((res) => ({
        key: `${entity}|${stmt}`,
        rows: res.rows ?? [],
        periods: res.periods ?? [],
      }));

    const fetchTree = (entity: string, year: number, stmt: 'PL' | 'BS') =>
      getBudgetTree(treeFetchParams(draft, entity, year, stmt, signal)).then((res) => ({
        key: overrideKey(entity, year, stmt),
        positions: res.positions,
      }));

    const finishInitial = (posMap: Record<string, BudgetPosition[]>) => {
      setActiveEntity(firstEntity);
      setActiveYear(firstYear);
      setActiveStatement(firstStmt);
      setActivePlanYear(firstYear);

      const blankOverrides = buildBlankOverrides(triples, posMap, draft);
      setOverridesMap(blankOverrides);
      const initialSnap: HistorySnapshot = { draft, overridesMap: blankOverrides };
      setHistoryStack([initialSnap]);
      setHistoryIdx(0);
    };

    const loadRemaining = (
      posMap: Record<string, BudgetPosition[]>,
      granMap: Record<string, { rows: BudgetGranularityRow[]; periods: BudgetGranularityPeriod[] }>,
    ) => {
      const remainingTrees = triples.filter(
        (t) => overrideKey(t.entity, t.year, t.stmt) !== primaryTreeKey,
      );
      const remainingGran = granularityPairs.filter((g) => `${g.entity}|${g.stmt}` !== primaryGranKey);

      const treePromise =
        remainingTrees.length > 0
          ? runWithConcurrency(remainingTrees, 2, (t) => fetchTree(t.entity, t.year, t.stmt))
          : Promise.resolve([]);

      const granPromise =
        remainingGran.length > 0
          ? runWithConcurrency(remainingGran, 2, (g) => fetchGranularity(g.entity, g.stmt))
          : Promise.resolve([]);

      return Promise.all([treePromise, granPromise]).then(([treeResults, granResults]) => {
        if (signal.aborted) return;
        const mergedPos = { ...posMap };
        for (const { key, positions } of treeResults) {
          mergedPos[key] = positions;
        }
        setFinalPositionsMap(mergedPos);

        const mergedGran = { ...granMap };
        for (const { key, rows, periods } of granResults) {
          mergedGran[key] = { rows, periods };
        }
        setGranularityViewMap(mergedGran);
      });
    };

    // Phase 1: active entity/statement structure + first tree (show table ASAP)
    Promise.all([
      fetchGranularity(firstEntity, firstStmt),
      fetchTree(firstEntity, firstYear, firstStmt),
    ])
      .then(([primaryGran, primaryTree]) => {
        if (signal.aborted) return;
        const posMap: Record<string, BudgetPosition[]> = {
          [primaryTree.key]: primaryTree.positions,
        };
        const granMap = {
          [primaryGran.key]: { rows: primaryGran.rows, periods: primaryGran.periods },
        };
        setFinalPositionsMap(posMap);
        setGranularityViewMap(granMap);
        finishInitial(posMap);
        setFinalLoading(false);
        setGranularityViewLoading(false);

        // Phase 2: remaining scopes in background (bounded concurrency)
        return loadRemaining(posMap, granMap);
      })
      .catch((err: unknown) => {
        if (signal.aborted) return;
        if (err instanceof DOMException && err.name === 'AbortError') return;
        const msg = err instanceof Error ? err.message : 'Failed to load budget grid';
        setFinalError(msg);
        setGranularityViewError(msg);
      })
      .finally(() => {
        if (!signal.aborted) {
          setFinalLoading(false);
          setGranularityViewLoading(false);
        }
      });

    return () => {
      abort.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatComplete]);

  // ---------------------------------------------------------------------------
  // Heuristic fetch trigger (uses first entity for preview)
  // ---------------------------------------------------------------------------

  const triggerHeuristicFetch = useCallback(async (d: BudgetDraft) => {
    if (d.start.mode !== 'heuristic') return;
    if (d.fiscalYears.length === 0 || d.statements.length === 0) return;

    setHeuristicLoading(true);
    setHeuristicPreview(null);
    try {
      const firstEntity = d.entities[0] ?? '';
      // Preview uses representative first year (same pattern as multi-entity using entities[0])
      const firstYear = d.fiscalYears[0];
      const res = await getBudgetTree({
        statement: d.statements[0] as 'PL' | 'BS',
        fiscal_year: firstYear,
        entity: firstEntity || undefined,
        level: 'L4',
        heuristic: d.start.heuristic,
        growth_pct: d.start.growthPct,
        top_n: d.partner.topN,
      });
      setHeuristicPositions(res.positions);
      setHeuristicPreview({ positionCount: res.positions.length });
    } catch (err) {
      console.error('Heuristic fetch failed:', err);
      setHeuristicPreview(null);
    } finally {
      setHeuristicLoading(false);
    }
  }, []);

  // ---------------------------------------------------------------------------
  // onAnswer
  // ---------------------------------------------------------------------------

  const handleAnswer = useCallback(
    async (stepId: StepId, value: string | string[] | number | Record<PositionKey, PositionGranularity>) => {
      if (stepId === 'review' && value === 'open') {
        setChatComplete(true);
        return;
      }

      const newDraft = applyAnswer(draft, stepId, value);
      const next = nextStep(newDraft, stepId);

      setDraft(newDraft);
      // Push a history snapshot so chat answers are undoable
      pushHistory(newDraft, overridesMap);
      if (next !== null) {
        setCurrentStepId(next);
      }

      const heuristicTriggers: StepId[] = ['heuristic_method', 'heuristic_growth'];
      if (heuristicTriggers.includes(stepId) && newDraft.start.mode === 'heuristic') {
        await triggerHeuristicFetch(newDraft);
      }

      if (stepId === 'start_values' && value === 'heuristic') {
        await triggerHeuristicFetch(newDraft);
      }

      if (stepId === 'start_values' && value === 'blank') {
        try {
          const firstEntity = newDraft.entities[0] ?? '';
          const firstYear = newDraft.fiscalYears[0] ?? new Date().getFullYear() + 1;
          const res = await getBudgetTree(
            treeFetchParams(
              newDraft,
              firstEntity,
              firstYear,
              newDraft.statements[0] as 'PL' | 'BS',
            ),
          );
          const blankPositions: typeof newDraft.positions = {};
          res.positions.forEach((p) => {
            blankPositions[p.line_code] = { annual: 0, source: 'blank' };
          });
          setDraft((prev) => ({
            ...prev,
            positions: blankPositions,
            positionCount: res.positions.length,
          }));
        } catch (err) {
          console.error('Blank tree fetch failed:', err);
        }
      }
    },
    [draft, triggerHeuristicFetch],
  );

  // ---------------------------------------------------------------------------
  // onSelect — update the draft for the current step WITHOUT advancing.
  // Used by multi-select toggles (entity checkboxes, granularity) so the user
  // can tick several options before confirming with "Continue".
  // ---------------------------------------------------------------------------

  const handleSelect = useCallback(
    (stepId: StepId, value: string | string[] | number | Record<PositionKey, PositionGranularity>) => {
      setDraft((prev) => applyAnswer(prev, stepId, value));
    },
    [],
  );

  // ---------------------------------------------------------------------------
  // onEditStep — go back to a step with draft preserved
  // ---------------------------------------------------------------------------

  const handleEditStep = useCallback((stepId: StepId) => {
    setCurrentStepId(stepId);
    setChatComplete(false);
    setSaveState(SAVE_IDLE);
    setLoopOffered(false);
  }, []);

  const handleEditSetup = useCallback(() => {
    setChatComplete(false);
    setCurrentStepId('entity');
    setSaveState(SAVE_IDLE);
    setLoopOffered(false);
    setSandboxPhase('editing');
  }, []);

  // ---------------------------------------------------------------------------
  // onAcceptHeuristic
  // ---------------------------------------------------------------------------

  const handleAcceptHeuristic = useCallback(() => {
    const posMap: BudgetDraft['positions'] = {};
    heuristicPositions.forEach((p) => {
      posMap[p.line_code] = {
        annual: p.suggestion?.annual ?? p.annual,
        months: p.suggestion?.months ?? p.months,
        source: 'heuristic',
      };
    });
    setDraft((prev) => ({
      ...prev,
      positions: posMap,
      positionCount: heuristicPositions.length,
    }));
    setHeuristicPreview(null);
    setCurrentStepId((prev) => {
      const next = nextStep(draft, prev);
      return next ?? prev;
    });
  }, [heuristicPositions, draft]);

  // ---------------------------------------------------------------------------
  // onAcceptPartnerHeuristic
  // ---------------------------------------------------------------------------

  const handleAcceptPartnerHeuristic = useCallback(() => {
    setPartnerHeuristicPreview(null);
    setCurrentStepId((prev) => {
      const next = nextStep(draft, prev);
      return next ?? prev;
    });
  }, [draft]);

  // ---------------------------------------------------------------------------
  // Excel upload
  // ---------------------------------------------------------------------------

  const handleFileUpload = useCallback(
    async (file: File) => {
      setUploadFile(file);
      setUploadLoading(true);
      setUploadError(undefined);
      setUploadPreview(null);
      try {
        const firstEntity = draft.entities[0] ?? '';
        const firstYear = draft.fiscalYears[0] ?? new Date().getFullYear() + 1;
        const preview = await budgetUpload(
          file,
          draft.statements[0],
          firstYear,
          firstEntity,
          draft.partner.topN,
        );
        setUploadPreview(preview);
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Upload failed';
        setUploadError(msg);
      } finally {
        setUploadLoading(false);
      }
    },
    [draft],
  );

  const handleAcceptUpload = useCallback(() => {
    if (!uploadPreview) return;
    const newPositions: BudgetDraft['positions'] = { ...draft.positions };
    uploadPreview.changes.forEach((c) => {
      const existing = newPositions[c.line_code];
      newPositions[c.line_code] = {
        ...(existing ?? {}),
        annual: c.field === 'annual' ? c.new : existing?.annual,
        source: 'excel',
      };
    });
    setDraft((prev) => ({
      ...prev,
      positions: newPositions,
      start: { ...prev.start, uploadFileId: uploadPreview.file_id },
    }));
    setUploadPreview(null);
    setUploadFile(null);
    const next = nextStep(draft, 'excel_upload');
    if (next) setCurrentStepId(next);
  }, [uploadPreview, draft]);

  // ---------------------------------------------------------------------------
  // Download template
  // ---------------------------------------------------------------------------

  const handleDownloadTemplate = useCallback(() => {
    const firstEntity = draft.entities[0] ?? '';
    const firstYear = draft.fiscalYears[0] ?? new Date().getFullYear() + 1;
    downloadBudgetTemplate({
      statement: draft.statements[0],
      fiscal_year: firstYear,
      entity: firstEntity,
      level: 'L4',
      top_n: draft.partner.topN,
    }).catch((err: unknown) => {
      console.error('Template download failed:', err);
    });
  }, [draft]);

  // ---------------------------------------------------------------------------
  // Whole-draft Save — iterates entities × statements
  // ---------------------------------------------------------------------------

  const handleSave = useCallback(async () => {
    saveAbortRef.current = false;
    setSaveState({ running: true, step: 0, total: 0, currentLabel: '', done: false });

    const allPayloads: Array<{
      lineCode: string;
      label: string;
      entityLabel: string;
      payload: Parameters<typeof patchBudgetPosition>[0];
    }> = [];

    for (const entity of draft.entities) {
      for (const year of draft.fiscalYears) {
        for (const stmt of draft.statements) {
          const key = overrideKey(entity, year, stmt);
          const positions = finalPositionsMap[key] ?? [];
          const stmtOverrides = overridesMap[key] ?? {};
          const stmtPayloads = buildPatchPayloads(draft, entity, year, stmt, stmtOverrides, positions);
          allPayloads.push(...stmtPayloads);
        }
      }
    }

    if (allPayloads.length === 0) {
      setSaveState({
        running: false,
        step: 0,
        total: 0,
        currentLabel: '',
        error: 'No staged values to save. Fill in at least one position before saving.',
        done: false,
      });
      return;
    }

    setSaveState((prev) => ({ ...prev, total: allPayloads.length }));

    let step = 0;
    for (const { lineCode, label, entityLabel, payload } of allPayloads) {
      if (saveAbortRef.current) break;

      setSaveState((prev) => ({
        ...prev,
        step,
        currentLabel:
          draft.entities.length > 1 || draft.fiscalYears.length > 1
            ? `${entityLabel}: ${label}`
            : label,
      }));

      try {
        await patchBudgetPosition(payload);
      } catch (err) {
        const msg = err instanceof Error ? err.message : `Failed writing ${lineCode}`;
        setSaveState((prev) => ({
          ...prev,
          running: false,
          step,
          error: msg,
          done: false,
        }));
        return;
      }

      step += 1;
    }

    setSaveState({
      running: false,
      step: allPayloads.length,
      total: allPayloads.length,
      currentLabel: '',
      done: true,
    });
    if (IS_REPORTING_V2_SANDBOX) {
      setSandboxPhase('saved');
    }
    setLoopOffered(true);
  }, [draft, finalPositionsMap, overridesMap]);

  const handleSandboxCommit = useCallback(async () => {
    if (!IS_REPORTING_V2_SANDBOX || sandboxPhase !== 'saved') return;
    setSandboxPhase('committing');
    if (import.meta.env.MODE === 'merged') {
      // Merged stack: budget rows are ALREADY persisted by the save loop above
      // (patchBudgetPosition -> /api/v1/budget -> fact_position_plan) and reporting reads
      // them live (position_plan_grain_sql, budget->forecast->plan resolution), so they show
      // in Reporting immediately. No theatrical wait — commit is instant. (This branch is
      // dead-code-eliminated in the reporting-v2-sandbox bundle, which keeps its 28s path.)
      setSandboxPhase('committed');
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 28_000));
    setSandboxPhase('committed');
  }, [sandboxPhase]);

  // ---------------------------------------------------------------------------
  // Reset — iterates entities × statements
  // ---------------------------------------------------------------------------

  const handleResetRequest = useCallback(() => {
    setResetConfirm(true);
    setResetError(null);
  }, []);

  const handleResetCancel = useCallback(() => {
    setResetConfirm(false);
    setResetError(null);
  }, []);

  const handleResetConfirm = useCallback(async () => {
    setResetLoading(true);
    setResetError(null);
    try {
      for (const entity of draft.entities) {
        for (const year of draft.fiscalYears) {
          for (const stmt of draft.statements) {
            await deleteBudget(stmt, year, entity || undefined);
          }
        }
      }
      setDraft((prev) => ({ ...prev, positions: {}, positionCount: undefined }));
      setFinalPositionsMap({});
      setGranularityViewMap({});
      setOverridesMap({});
      setHistoryStack([]);
      setHistoryIdx(-1);
      setSaveState(SAVE_IDLE);
      setLoopOffered(false);
      setChatComplete(false);
      setCurrentStepId('entity');
      setResetConfirm(false);
      setSandboxPhase('editing');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Reset failed';
      setResetError(msg);
    } finally {
      setResetLoading(false);
    }
  }, [draft]);

  // ---------------------------------------------------------------------------
  // Loop: start fresh for next entity / statement
  // ---------------------------------------------------------------------------

  const handleLoop = useCallback(() => {
    setDraft(initialDraft());
    setCurrentStepId('entity');
    setChatComplete(false);
    setSaveState(SAVE_IDLE);
    setLoopOffered(false);
    setFinalPositionsMap({});
    setGranularityViewMap({});
    setOverridesMap({});
    setHistoryStack([]);
    setHistoryIdx(-1);
    setHeuristicPositions([]);
    setHeuristicPreview(null);
    setUploadPreview(null);
    setUploadFile(null);
    setUploadError(undefined);
    setPartnerHeuristicPreview(null);
  }, []);

  const handleDismissSave = useCallback(() => {
    setSaveState(SAVE_IDLE);
    setLoopOffered(false);
  }, []);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const isSaving = saveState.running;

  // Label for the reset confirmation dialog scope
  const resetScopeLabel =
    draft.entities.length === 0
      ? ''
      : `${formatEntities(draft.entities, entityOptions)} / ${formatFiscalYears(draft.fiscalYears)} / ${draft.statements.join(' + ')}`;

  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <div className="mx-auto w-full max-w-[1680px] px-6 lg:px-8 py-8">

        {/* Page header — aligned with the FDD-Bot format (eyebrow · h1 · subtitle, fade-in) */}
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="mb-5"
        >
          <p className="text-xs font-semibold uppercase tracking-widest mb-1.5" style={{ color: '#1E3A5F' }}>
            Admin · Planning
          </p>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: '#111827' }}>
            Budget Planning
          </h1>
          <p className="text-sm mt-1" style={{ color: '#94A3B8' }}>
            Build the budget and forecast that feed the reporting plan columns — guided step by step.
          </p>
        </motion.div>

        {entityError && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 flex items-center justify-between gap-3">
            <span>{entityError}</span>
            <button
              type="button"
              onClick={() => loadEntities()}
              className="shrink-0 rounded-lg border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-50"
            >
              Retry
            </button>
          </div>
        )}

        {entityLoading && !chatComplete ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-slate-500">
            <svg
              className="animate-spin h-4 w-4 text-[#1E3A5F]"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
            Loading entities…
          </div>
        ) : !chatComplete ? (
          /* ── Chat flow — narrow centered column ── */
          <BudgetChat
            draft={draft}
            currentStepId={currentStepId}
            entityOptions={entityOptions}
            fiscalYearOptions={fiscalYearOptions}
            onAnswer={handleAnswer}
            onSelect={handleSelect}
            onEditStep={handleEditStep}
            onDownloadTemplate={handleDownloadTemplate}
            onFileUpload={handleFileUpload}
            uploadLoading={uploadLoading}
            uploadError={uploadError}
            uploadPreview={uploadPreview}
            onAcceptUpload={handleAcceptUpload}
            heuristicLoading={heuristicLoading}
            heuristicPreview={heuristicPreview}
            onAcceptHeuristic={handleAcceptHeuristic}
            partnerHeuristicLoading={partnerHeuristicLoading}
            partnerHeuristicPreview={partnerHeuristicPreview}
            onAcceptPartnerHeuristic={handleAcceptPartnerHeuristic}
            canUndo={canUndo}
            canRedo={canRedo}
            onUndo={handleUndo}
            onRedo={handleRedo}
          />
        ) : (
          /* ── Final editable view — full container width ── */
          <div className="flex flex-col gap-4">
            {/* Summary bar with Edit setup + Reset */}
            <ChatSummaryBar
              draft={draft}
              entityOptions={entityOptions}
              onEditSetup={handleEditSetup}
              onReset={handleResetRequest}
              saving={isSaving}
            />

            {/* Reset confirmation dialog */}
            {resetConfirm && (
              <div className="rounded-xl border border-red-200 bg-red-50 px-5 py-4">
                <p className="font-semibold text-red-800 text-sm">
                  Delete budget for {resetScopeLabel}?
                </p>
                <p className="text-xs text-red-700 mt-1">
                  This will remove all saved budget rows for this scope and revert readers to
                  forecast / plan values. This cannot be undone.
                </p>
                {resetError && (
                  <p className="text-xs text-red-700 mt-2 font-mono">{resetError}</p>
                )}
                <div className="flex items-center gap-3 mt-3">
                  <button
                    type="button"
                    onClick={handleResetConfirm}
                    disabled={resetLoading}
                    className="rounded-lg px-3 py-1.5 text-xs font-medium text-white bg-red-600 hover:bg-red-700 transition-colors disabled:opacity-50"
                  >
                    {resetLoading ? 'Deleting…' : 'Yes, delete'}
                  </button>
                  <button
                    type="button"
                    onClick={handleResetCancel}
                    disabled={resetLoading}
                    className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 transition-colors disabled:opacity-50"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* ── Tab bar row: entity tabs (if >1) + year tabs (if >1) + statement tabs (if Both) ── */}
            {(showEntityTabs || showYearTabs || showStatementTabs) && (
              <div className="flex items-center gap-4 border-b border-[#E2E8F0]">
                {/* Entity axis */}
                {showEntityTabs && (
                  <div className="flex gap-1">
                    {entityTabEntities.map((ent) => {
                      const label =
                        entityOptions.find((o) => o.value === ent)?.label ??
                        (ent === '' ? 'Consolidated' : ent);
                      return (
                        <button
                          key={ent}
                          type="button"
                          onClick={() => setActiveEntity(ent)}
                          className={[
                            'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
                            activeEntity === ent
                              ? 'border-[#1E3A5F] text-[#1E3A5F]'
                              : 'border-transparent text-slate-500 hover:text-slate-700',
                          ].join(' ')}
                        >
                          {label}
                        </button>
                      );
                    })}
                  </div>
                )}

                {/* Separator between entity and year tabs */}
                {showEntityTabs && showYearTabs && (
                  <div className="h-5 w-px bg-[#E2E8F0]" />
                )}

                {/* Year axis */}
                {showYearTabs && (
                  <div className="flex gap-1">
                    {draft.fiscalYears.map((yr) => (
                      <button
                        key={yr}
                        type="button"
                        onClick={() => setActiveYear(yr)}
                        className={[
                          'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
                          activeYear === yr
                            ? 'border-[#1E3A5F] text-[#1E3A5F]'
                            : 'border-transparent text-slate-500 hover:text-slate-700',
                        ].join(' ')}
                      >
                        {fyLabel(yr)}
                      </button>
                    ))}
                  </div>
                )}

                {/* Separator between year and statement tabs */}
                {showYearTabs && showStatementTabs && (
                  <div className="h-5 w-px bg-[#E2E8F0]" />
                )}

                {/* Separator between entity and statement tabs (when no year tabs) */}
                {showEntityTabs && !showYearTabs && showStatementTabs && (
                  <div className="h-5 w-px bg-[#E2E8F0]" />
                )}

                {/* Statement axis */}
                {showStatementTabs && (
                  <div className="flex gap-1">
                    {(['PL', 'BS'] as const).map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => setActiveStatement(s)}
                        className={[
                          'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
                          activeStatement === s
                            ? 'border-[#1E3A5F] text-[#1E3A5F]'
                            : 'border-transparent text-slate-500 hover:text-slate-700',
                        ].join(' ')}
                      >
                        {s === 'PL' ? 'P&L' : 'Balance Sheet'}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Save progress / success / error banner */}
            {(saveState.running || saveState.done || saveState.error) && (
              <SaveProgress
                step={saveState.step}
                total={saveState.total}
                currentLabel={saveState.currentLabel}
                error={saveState.error}
                done={saveState.done}
                onLoop={loopOffered ? handleLoop : undefined}
                onDismiss={saveState.done ? handleDismissSave : undefined}
              />
            )}

            {/* Final error loading grid */}
            {finalError && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {finalError}
              </div>
            )}

            {/* Grid / view loading spinner */}
            {(finalLoading || granularityViewLoading) ? (
              <div className="flex items-center gap-2 text-sm text-slate-500 py-8">
                <svg
                  className="animate-spin h-4 w-4 text-[#1E3A5F]"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                Loading budget view…
              </div>
            ) : (
              <>
                {granularityViewError && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
                    Could not load statement structure: {granularityViewError}
                  </div>
                )}

                {/* Plan versions (Phase 4) — active version + include-in-reporting toggle for
                    the current statement/year. Hidden when the DB is un-migrated (supported=false). */}
                <PlanVersionPanel
                  statement={activeStatement}
                  fiscalYear={activeYear}
                  disabled={isSaving}
                />

                {/* Structured IS/BS Final View */}
                <StructuredBudgetView
                  granularityRows={activeGranularityView.rows}
                  granularityPeriods={activeGranularityView.periods}
                  positionsByCode={activePositionsByCode}
                  positionsByCodeAllYears={positionsByCodeAllYears}
                  statement={activeStatement}
                  granularityByPosition={draft.granularityByPosition}
                  draft={draft}
                  overrides={activeOverrides}
                  onOverride={(lineCode, override) =>
                    setActiveOverrides({ ...activeOverrides, [lineCode]: override })
                  }
                  saving={isSaving}
                  canUndo={canUndo}
                  canRedo={canRedo}
                  onUndo={handleUndo}
                  onRedo={handleRedo}
                  onSave={handleSave}
                  onEditSetup={handleEditSetup}
                  saveDone={saveState.done}
                  activePlanYear={activePlanYear}
                  onSetActivePlanYear={setActivePlanYear}
                  onPartnerRateLevelChange={
                    IS_REPORTING_V2_SANDBOX ? handlePartnerRateLevelChange : undefined
                  }
                  onOpenMonthlyAdjust={
                    IS_REPORTING_V2_SANDBOX ? () => setMonthlyAdjustOpen(true) : undefined
                  }
                  ratesFrozen={
                    IS_REPORTING_V2_SANDBOX &&
                    (sandboxPhase === 'saved' || sandboxPhase === 'committing' || sandboxPhase === 'committed')
                  }
                  monthlyEditable={
                    IS_REPORTING_V2_SANDBOX &&
                    (sandboxPhase === 'saved' || sandboxPhase === 'committing')
                  }
                  onCommit={IS_REPORTING_V2_SANDBOX ? handleSandboxCommit : undefined}
                  commitLoading={sandboxPhase === 'committing'}
                  commitDone={sandboxPhase === 'committed'}
                />

                {IS_REPORTING_V2_SANDBOX && (
                  <BudgetMonthlyAdjustModal
                    open={monthlyAdjustOpen}
                    onClose={() => setMonthlyAdjustOpen(false)}
                    granularityRows={activeGranularityView.rows}
                    granularityPeriods={activeGranularityView.periods}
                    positionsByCode={activePositionsByCode}
                    statement={activeStatement}
                    granularityByPosition={draft.granularityByPosition}
                    draft={draft}
                    overrides={activeOverrides}
                    onOverride={(lineCode, override) =>
                      setActiveOverrides({ ...activeOverrides, [lineCode]: override })
                    }
                    activePlanYear={activePlanYear}
                  />
                )}
              </>
            )}
          </div>
        )}

        {/* Suppress unused variable warning for uploadFile */}
        {uploadFile && null}
      </div>
    </div>
  );
}
