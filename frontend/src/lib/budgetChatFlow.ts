/**
 * budgetChatFlow.ts — pure step machine + reducer for the Budget Planning Chat.
 *
 * No side-effects, no API calls. All data transformations are synchronous and
 * referentially transparent so they can be tested independently.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type StatementId = 'PL' | 'BS';
export type BothStatement = 'PL' | 'BS' | 'Both';
export type HeuristicMethod = 'prior_year' | 'trend_cagr' | 'run_rate';
export type StartMode = 'heuristic' | 'excel' | 'blank';
export type PartnerKind = 'customers' | 'suppliers';

/**
 * Per-position granularity choice.
 *   'L3'        — plan the whole position as one row
 *   'L4'        — plan at sub-position (L4 children) level
 *   'customers' — partner breakdown by customers (for Net/Gross sales, Trade receivables)
 *   'suppliers' — partner breakdown by suppliers (for Cost of materials, Trade payables)
 */
export type PositionGranularity = 'L3' | 'L4' | 'customers' | 'suppliers';

/**
 * Composite key for the granularityByPosition map.
 * Format: "${statement}|${line_code}" e.g. "PL|rev-gross"
 */
export type PositionKey = string;

export interface BudgetPositionValue {
  annual?: number;
  months?: number[];
  source: 'heuristic' | 'excel' | 'manual' | 'blank';
}

export interface BudgetDraft {
  /**
   * Selected entity scope:
   *   ['']               — consolidated (all entities, single scope)
   *   ['DE', 'FR', ...]  — subset of entity codes, each planned separately
   */
  entities: string[];
  /**
   * Selected fiscal years — plan multiple years in one run.
   *   [2026]             — single year
   *   [2026, 2027, 2028] — multi-year plan
   */
  fiscalYears: number[];
  statements: ('PL' | 'BS')[];            // after 'Both' expand to ['PL','BS']
  /**
   * Per-position granularity map.
   * Key: "${statement}|${line_code}" (e.g. "PL|rev-gross")
   * Value: 'L3' | 'L4' | 'customers' | 'suppliers'
   * Positions not present in this map default to 'L3'.
   */
  granularityByPosition: Record<PositionKey, PositionGranularity>;
  /**
   * @deprecated Use granularityByPosition instead.
   * Kept for backward compat with save logic; derived from granularityByPosition.
   * Will be 'L4' if ANY position is 'L4', else 'L3'.
   */
  level: 'L3' | 'L4';
  viewMode: 'annual' | 'monthly';
  /**
   * @deprecated Global inputMode is no longer used in the flow.
   * Per-position input mode is stored in inputModeByPosition (default 'pct').
   * Kept for backward compat with save/display logic.
   */
  inputMode: 'absolute' | 'growth';
  /**
   * Per-position input mode for manual ("Start blank") entry.
   * Key: "${statement}|${line_code}" — same format as granularityByPosition.
   * Value: 'pct' (growth %) | 'absolute'.
   * Default for any missing key is 'pct'.
   * Only relevant for the Start blank path; heuristic and Excel fill values directly.
   */
  inputModeByPosition: Record<PositionKey, 'pct' | 'absolute'>;
  /**
   * @deprecated direction step removed from flow.
   * Kept to avoid breaking the save shape; defaults to 'top_down'.
   */
  planningDir: 'top_down' | 'bottom_up';
  start: {
    mode: StartMode;
    heuristic?: HeuristicMethod;
    growthPct?: number;
    uploadFileId?: string;                 // from budgetUpload preview
    uploadDiff?: Array<{
      line_code: string; field: string; old: number; new: number;
      level_4?: string; partner_id?: string;
    }>;
  };
  partner: {
    customers: boolean;
    suppliers: boolean;
    topN?: number;                         // default 10
    values?: Record<string, BudgetPositionValue>;  // keyed by partner_id
  };
  positions: Record<string, BudgetPositionValue>;  // keyed by line_code
  positionCount?: number;                  // set after tree fetch (for display)
  /** Sandbox blank: group vs partner growth rate level per position. */
  partnerRateLevel?: Record<PositionKey, 'group' | 'partner'>;
}

export type StepId =
  | 'entity'
  | 'fiscal_year'
  | 'statement'
  | 'period'
  | 'granularity'
  | 'start_values'
  | 'heuristic_method'
  | 'heuristic_growth'
  | 'excel_upload'
  | 'partner_start'
  | 'review';

export type StepType =
  | 'dropdown'
  | 'radio'
  | 'multi_select'
  | 'number'
  | 'file_drop'
  | 'review'
  | 'entity_multi_select'
  | 'fiscal_year_multi_select'
  | 'position_granularity';

export interface StepOption {
  value: string;
  label: string;
  description?: string;
}

export interface ChatStep {
  id: StepId;
  type: StepType;
  prompt: string;
  /** Sub-prompt: second line shown as bot message detail */
  detail?: string;
  options?: StepOption[];
  /** Only shown when this returns true */
  showWhen: (draft: BudgetDraft) => boolean;
  /** Number input: min/max/step */
  numMin?: number;
  numMax?: number;
  numStep?: number;
  numDefault?: number;
  numSuffix?: string;
  /** Whether this step can be skipped */
  skippable?: boolean;
}

// ---------------------------------------------------------------------------
// fyLabel helper — formats a year number as short FY label (e.g. 2026 → "FY26")
// ---------------------------------------------------------------------------

export function fyLabel(year: number): string {
  return 'FY' + String(year).slice(-2);
}

// ---------------------------------------------------------------------------
// Position key builder
// ---------------------------------------------------------------------------

export function positionKey(statement: 'PL' | 'BS', lineCode: string): PositionKey {
  return `${statement}|${lineCode}`;
}

// ---------------------------------------------------------------------------
// togglePositionInputMode — flip a single position between 'pct' and 'absolute'
// Default when key absent is 'pct'. Used by Final View grid per-position toggle.
// ---------------------------------------------------------------------------

export function togglePositionInputMode(
  current: Record<PositionKey, 'pct' | 'absolute'>,
  key: PositionKey,
): Record<PositionKey, 'pct' | 'absolute'> {
  const existing: 'pct' | 'absolute' = current[key] ?? 'pct';
  return { ...current, [key]: existing === 'pct' ? 'absolute' : 'pct' };
}

/** Read the input mode for a single position; missing key defaults to 'pct'. */
export function getPositionInputMode(
  map: Record<PositionKey, 'pct' | 'absolute'>,
  key: PositionKey,
): 'pct' | 'absolute' {
  return map[key] ?? 'pct';
}

// ---------------------------------------------------------------------------
// hasAnyPartnerGranularity — checks if ANY position has customers/suppliers
// Used by partner_start showWhen.
// ---------------------------------------------------------------------------

export function hasAnyPartnerGranularity(draft: BudgetDraft): boolean {
  return Object.values(draft.granularityByPosition).some(
    (g) => g === 'customers' || g === 'suppliers',
  );
}

// ---------------------------------------------------------------------------
// deriveLevel — backward-compat helper: returns 'L4' if any position is 'L4',
// else 'L3'.  Drives the single `level` field on the draft.
// ---------------------------------------------------------------------------

export function deriveLevel(granularityByPosition: Record<PositionKey, PositionGranularity>): 'L3' | 'L4' {
  return Object.values(granularityByPosition).some((g) => g === 'L4') ? 'L4' : 'L3';
}

// ---------------------------------------------------------------------------
// Granularity options helper (kept for backward compat / tests)
// ---------------------------------------------------------------------------

export function granularityOptionsFor(draft: BudgetDraft): StepOption[] {
  const hasPL = draft.statements.includes('PL');
  const opts: StepOption[] = [
    { value: 'L3', label: 'L3 — positions', description: 'Top-level positions only' },
  ];
  if (hasPL) {
    opts.push({ value: 'L4', label: 'L4 — sub-positions', description: 'Break down P&L positions into sub-lines' });
  }
  if (hasPL) {
    opts.push({ value: 'partner_customers', label: 'By Customers', description: 'Break down gross sales by top customers' });
    opts.push({ value: 'partner_suppliers', label: 'By Suppliers', description: 'Break down cost of materials by top suppliers' });
  }
  return opts;
}

// ---------------------------------------------------------------------------
// CHAT_STEPS — ordered step definitions
// ---------------------------------------------------------------------------

export const CHAT_STEPS: ChatStep[] = [
  // 1. entity — multi-select with exclusivity rule
  {
    id: 'entity',
    type: 'entity_multi_select',
    prompt: 'Which entity are you planning for?',
    detail: 'Select one or more entities, or choose "All entities (consolidated)" for a consolidated scope.',
    options: [],  // filled by caller
    showWhen: () => true,
  },
  // 2. fiscal_year — multi-select checkboxes (mirrors entity pattern)
  {
    id: 'fiscal_year',
    type: 'fiscal_year_multi_select',
    prompt: 'Which fiscal year(s)?',
    detail: 'Select one or more fiscal years to plan in this run.',
    options: [],  // filled by caller from currentYear-1 .. currentYear+5
    showWhen: () => true,
  },
  // 3. statement
  {
    id: 'statement',
    type: 'radio',
    prompt: 'Which statement(s)?',
    options: [
      { value: 'PL', label: 'P&L', description: 'Income statement' },
      { value: 'BS', label: 'Balance Sheet', description: 'Balance sheet only' },
      { value: 'Both', label: 'Both', description: 'Plan P&L and Balance Sheet' },
    ],
    showWhen: () => true,
  },
  // 4. period — BEFORE granularity so the table shows the correct history columns
  {
    id: 'period',
    type: 'radio',
    prompt: 'How do you want to view periods?',
    options: [
      { value: 'annual', label: 'Annual', description: 'One column per year (FY history)' },
      { value: 'monthly', label: 'Monthly', description: 'Last 24 months of history' },
    ],
    showWhen: () => true,
  },
  // 5. granularity — per-position structured picker (uses viewMode from period step)
  {
    id: 'granularity',
    type: 'position_granularity',
    prompt: 'Set planning granularity per position',
    detail: 'Choose how granularly to plan each line. Partner-driven positions can be broken down by customer or supplier.',
    options: [],
    showWhen: () => true,
  },
  // 6. start_values
  {
    id: 'start_values',
    type: 'radio',
    prompt: 'How do you want to fill starting values?',
    options: [
      { value: 'heuristic', label: 'Finssentials heuristics', description: 'Propose starting values from GL history' },
      { value: 'excel', label: 'Excel template', description: 'Download template, fill offline, re-upload' },
      { value: 'blank', label: 'Start blank', description: 'Enter values manually; choose % or absolute per position (default: growth %)' },
    ],
    showWhen: () => true,
  },
  // 7. heuristic_method
  {
    id: 'heuristic_method',
    type: 'radio',
    prompt: 'Which heuristic method?',
    options: [
      { value: 'prior_year', label: 'Prior Year', description: 'Base on prior fiscal year values' },
      { value: 'trend_cagr', label: 'Trend CAGR', description: 'Fit CAGR over multiple prior years' },
      { value: 'run_rate', label: 'Run Rate', description: 'Annualise recent months as run-rate' },
    ],
    showWhen: (draft) => draft.start.mode === 'heuristic',
  },
  // 8. heuristic_growth
  {
    id: 'heuristic_growth',
    type: 'number',
    prompt: 'Growth rate on top of prior year?',
    numMin: -100,
    numMax: 500,
    numStep: 0.5,
    numDefault: 0,
    numSuffix: '%',
    showWhen: (draft) => draft.start.mode === 'heuristic' && draft.start.heuristic === 'prior_year',
  },
  // 9. excel_upload
  {
    id: 'excel_upload',
    type: 'file_drop',
    prompt: 'Upload your filled Excel template',
    detail: "Download the template first if you haven't yet, fill in the values, then drop it here.",
    showWhen: (draft) => draft.start.mode === 'excel',
  },
  // 10. partner_start
  {
    id: 'partner_start',
    type: 'radio',
    prompt: 'How do you want to fill starting values for partners?',
    options: [
      { value: 'heuristic', label: 'Propose from history', description: 'Seed top-partner values from GL history (read-only preview)' },
      { value: 'skip', label: 'Skip — fill in Final View', description: 'Leave partner values for manual entry in the budget grid' },
    ],
    skippable: true,
    showWhen: (draft) => hasAnyPartnerGranularity(draft),
  },
  // 11. review
  {
    id: 'review',
    type: 'review',
    prompt: 'Ready to build your budget',
    showWhen: () => true,
  },
];

// ---------------------------------------------------------------------------
// Initial draft
// ---------------------------------------------------------------------------

export function initialDraft(): BudgetDraft {
  return {
    entities: [],
    fiscalYears: [new Date().getFullYear() + 1],
    statements: ['PL'],
    granularityByPosition: {},
    level: 'L3',
    viewMode: 'annual',
    inputMode: 'growth',           // deprecated global — kept for compat, default 'growth'
    inputModeByPosition: {},       // per-position; missing keys default to 'pct'
    planningDir: 'top_down',       // deprecated — kept for compat
    start: {
      mode: 'heuristic',
    },
    partner: {
      customers: false,
      suppliers: false,
      topN: 10,
    },
    positions: {},
    partnerRateLevel: {},
  };
}

// ---------------------------------------------------------------------------
// applyAnswer
// ---------------------------------------------------------------------------

/**
 * Apply the exclusivity rule for entity multi-select:
 *   - '' (consolidated) is exclusive: ticking it clears all specific entities.
 *   - ticking a specific entity clears '' (consolidated).
 *   - Duplicate-free output.
 */
export function applyEntitySelection(current: string[], toggled: string): string[] {
  if (toggled === '') {
    // Toggle consolidated: exclusive — replace everything with ['']
    const alreadyConsolidated = current.length === 1 && current[0] === '';
    return alreadyConsolidated ? [] : [''];
  }
  // Specific entity: remove consolidated, toggle the entity
  const withoutConsolidated = current.filter((v) => v !== '');
  if (withoutConsolidated.includes(toggled)) {
    return withoutConsolidated.filter((v) => v !== toggled);
  }
  return [...withoutConsolidated, toggled];
}

export function applyAnswer(
  draft: BudgetDraft,
  stepId: StepId,
  value: string | string[] | number | Record<PositionKey, PositionGranularity>,
): BudgetDraft {
  const next = { ...draft };

  switch (stepId) {
    case 'entity': {
      // value is the full selected array from the multi-select
      const arr = Array.isArray(value) ? (value as string[]) : [value as string];
      next.entities = arr;
      break;
    }

    case 'fiscal_year': {
      // value is the full selected years array (string[] or number[]) from the multi-select
      const rawArr = Array.isArray(value) ? value : [value];
      const years = rawArr.map((v) => Number(v)).filter((y) => !isNaN(y));
      // Sort ascending, deduplicate
      next.fiscalYears = [...new Set(years)].sort((a, b) => a - b);
      break;
    }

    case 'statement': {
      const v = value as BothStatement;
      next.statements = v === 'Both' ? ['PL', 'BS'] : [v];
      break;
    }

    case 'granularity': {
      // New model: value is a Record<PositionKey, PositionGranularity>
      // Also support legacy string[] for backward compat with existing tests.
      if (typeof value === 'object' && !Array.isArray(value)) {
        const map = value as Record<PositionKey, PositionGranularity>;
        next.granularityByPosition = map;
        next.level = deriveLevel(map);
        // Derive partner booleans for backward compat
        const vals = Object.values(map);
        next.partner = {
          ...next.partner,
          customers: vals.includes('customers'),
          suppliers: vals.includes('suppliers'),
        };
      } else {
        // Legacy path: string[] — kept for existing tests
        const vs = Array.isArray(value) ? (value as string[]) : [value as string];
        const hasL4 = vs.includes('L4');
        let level: 'L3' | 'L4' = 'L3';
        if (hasL4) level = 'L4';
        // If BS-only, force L3
        if (next.statements.length === 1 && next.statements[0] === 'BS') {
          level = 'L3';
        }
        next.level = level;
        next.partner = {
          ...next.partner,
          customers: vs.includes('partner_customers'),
          suppliers: vs.includes('partner_suppliers'),
        };
        // Keep granularityByPosition empty when legacy path is used
        next.granularityByPosition = {};
      }
      break;
    }

    case 'period':
      next.viewMode = value as 'annual' | 'monthly';
      break;

    case 'start_values':
      next.start = { ...next.start, mode: value as StartMode };
      break;

    case 'heuristic_method':
      next.start = { ...next.start, heuristic: value as HeuristicMethod };
      break;

    case 'heuristic_growth':
      next.start = { ...next.start, growthPct: Number(value) };
      break;

    case 'excel_upload':
      // No-op: file is handled externally; uploadDiff is folded in by the page
      break;

    case 'partner_start':
      // No-op: page handles seeding
      break;

    case 'review':
      // No-op
      break;
  }

  return next;
}

// ---------------------------------------------------------------------------
// Navigation helpers
// ---------------------------------------------------------------------------

/** Returns the ordered list of visible (shown) steps given the current draft. */
export function visibleSteps(draft: BudgetDraft): ChatStep[] {
  return CHAT_STEPS.filter((s) => s.showWhen(draft));
}

/**
 * Returns the next visible step id after `currentStepId`, or null if at the
 * last step.
 */
export function nextStep(draft: BudgetDraft, currentStepId: StepId): StepId | null {
  const visible = visibleSteps(draft);
  const idx = visible.findIndex((s) => s.id === currentStepId);
  if (idx === -1 || idx === visible.length - 1) return null;
  return visible[idx + 1].id;
}

/**
 * Returns the previous visible step id before `currentStepId`, or null if at
 * the first step.
 */
export function prevStep(draft: BudgetDraft, currentStepId: StepId): StepId | null {
  const visible = visibleSteps(draft);
  const idx = visible.findIndex((s) => s.id === currentStepId);
  if (idx <= 0) return null;
  return visible[idx - 1].id;
}

// ---------------------------------------------------------------------------
// Invalidation rules
// ---------------------------------------------------------------------------

/** Returns the list of dependent stepIds that must be cleared when stepId is re-answered */
export function getDependents(stepId: StepId): StepId[] {
  switch (stepId) {
    case 'statement':
      return ['period', 'granularity', 'start_values', 'heuristic_method', 'heuristic_growth', 'excel_upload', 'partner_start', 'review'];
    case 'period':
      // Changing period (annual/monthly) changes which columns the granularity table shows,
      // so granularity choices should be re-confirmed.
      return ['granularity', 'start_values', 'heuristic_method', 'heuristic_growth', 'excel_upload', 'partner_start', 'review'];
    case 'granularity':
      return ['start_values', 'heuristic_method', 'heuristic_growth', 'excel_upload', 'partner_start', 'review'];
    case 'start_values':
      return ['heuristic_method', 'heuristic_growth', 'excel_upload', 'partner_start', 'review'];
    case 'heuristic_method':
      return ['heuristic_growth', 'review'];
    case 'entity':
    case 'fiscal_year':
      return ['review'];
    default:
      return ['review'];
  }
}

/** Re-applies an answer to an earlier step and invalidates dependent fields. */
export function reApplyAnswer(
  draft: BudgetDraft,
  stepId: StepId,
  value: string | string[] | number | Record<PositionKey, PositionGranularity>,
): BudgetDraft {
  const applied = applyAnswer(draft, stepId, value);
  const deps = getDependents(stepId);
  const next = { ...applied };

  // Clear positions/positionCount when entity, fiscal_year, statement, or granularity change
  const positionInvalidators: StepId[] = ['entity', 'fiscal_year', 'statement', 'granularity'];
  if (positionInvalidators.includes(stepId)) {
    next.positions = {};
    next.positionCount = undefined;
  }

  // Clear granularityByPosition when statement or period changes
  // (statement: position set changes; period: column context changes so user re-confirms)
  if (stepId === 'statement' || stepId === 'period') {
    next.granularityByPosition = {};
    next.level = 'L3';
  }

  // Clear inputModeByPosition when granularity changes (position set may differ)
  if (stepId === 'granularity') {
    next.inputModeByPosition = {};
  }

  // Clear start sub-fields based on deps
  if (deps.includes('heuristic_method') || deps.includes('start_values')) {
    next.start = { mode: next.start.mode };
  }
  if (deps.includes('heuristic_growth') && !deps.includes('heuristic_method')) {
    next.start = { ...next.start, growthPct: undefined };
  }
  if (deps.includes('start_values')) {
    next.start = { mode: 'heuristic' };
    next.positions = {};
    next.positionCount = undefined;
  }

  return next;
}

// ---------------------------------------------------------------------------
// Read helpers
// ---------------------------------------------------------------------------

/** Reads the current answer for a step from the draft (for the editable chip display). */
export function getStepAnswer(
  draft: BudgetDraft,
  stepId: StepId,
): string | string[] | number | Record<PositionKey, PositionGranularity> | undefined {
  switch (stepId) {
    case 'entity':
      // Always return the array when it has at least one selection (including [''])
      return draft.entities.length > 0 ? draft.entities : undefined;
    case 'fiscal_year':
      return draft.fiscalYears.length > 0 ? draft.fiscalYears.map(String) : undefined;
    case 'statement': {
      if (draft.statements.length === 2) return 'Both';
      return draft.statements[0];
    }
    case 'granularity': {
      // New model: return the map if it has entries, else fall back to legacy level+partner
      if (Object.keys(draft.granularityByPosition).length > 0) {
        return draft.granularityByPosition;
      }
      // Legacy fallback for tests / incomplete drafts
      const vs: string[] = [];
      if (draft.level === 'L4') vs.push('L4');
      else vs.push('L3');
      if (draft.partner.customers) vs.push('partner_customers');
      if (draft.partner.suppliers) vs.push('partner_suppliers');
      return vs.length > 0 ? vs : undefined;
    }
    case 'period':
      return draft.viewMode;
    case 'start_values':
      return draft.start.mode;
    case 'heuristic_method':
      return draft.start.heuristic;
    case 'heuristic_growth':
      return draft.start.growthPct;
    case 'excel_upload':
      return draft.start.uploadFileId;
    case 'partner_start':
      return undefined;
    case 'review':
      return undefined;
    default:
      return undefined;
  }
}

/** Returns true if the step has a non-empty answer. */
export function isStepCompleted(draft: BudgetDraft, stepId: StepId): boolean {
  if (stepId === 'granularity') {
    // Completed when the map has at least one entry OR the legacy level is set
    return (
      Object.keys(draft.granularityByPosition).length > 0 ||
      draft.level === 'L4' ||
      draft.partner.customers ||
      draft.partner.suppliers
    );
  }
  const answer = getStepAnswer(draft, stepId);
  if (answer === undefined || answer === null) return false;
  if (typeof answer === 'string') return answer.trim() !== '';
  if (Array.isArray(answer)) return answer.length > 0;
  if (typeof answer === 'number') return true;
  if (typeof answer === 'object') return Object.keys(answer).length > 0;
  return false;
}

// ---------------------------------------------------------------------------
// formatGranularity — human-readable summary of granularityByPosition
// ---------------------------------------------------------------------------

export function formatGranularity(draft: BudgetDraft): string {
  const map = draft.granularityByPosition;
  const entries = Object.entries(map);
  if (entries.length === 0) {
    // Legacy fallback
    const parts: string[] = [draft.level];
    if (draft.partner.customers) parts.push('Customers');
    if (draft.partner.suppliers) parts.push('Suppliers');
    return parts.join(', ');
  }
  // Count distribution
  const counts: Record<PositionGranularity, number> = { L3: 0, L4: 0, customers: 0, suppliers: 0 };
  for (const [, g] of entries) counts[g] = (counts[g] ?? 0) + 1;
  const parts: string[] = [];
  if (counts.L3 > 0) parts.push(`${counts.L3} at L3`);
  if (counts.L4 > 0) parts.push(`${counts.L4} at L4`);
  if (counts.customers > 0) parts.push(`${counts.customers} by customers`);
  if (counts.suppliers > 0) parts.push(`${counts.suppliers} by suppliers`);
  return parts.join(', ') || 'Custom';
}

// ---------------------------------------------------------------------------
// Entity display helpers
// ---------------------------------------------------------------------------

/**
 * Format draft.entities for display (used in summary bar, review card, etc.)
 *   ['']         → 'Consolidated'
 *   ['DE']       → 'DE'
 *   ['DE', 'FR'] → 'DE, FR (2 entities)'
 */
export function formatEntities(
  entities: string[],
  entityOptions: { value: string; label: string }[],
): string {
  if (entities.length === 0) return '';
  if (entities.length === 1 && entities[0] === '') return 'Consolidated';
  const labels = entities.map(
    (code) => entityOptions.find((e) => e.value === code)?.label ?? code,
  );
  if (labels.length === 1) return labels[0];
  return `${labels.join(', ')} (${labels.length} entities)`;
}

/**
 * Format draft.fiscalYears for display (used in summary bar, review card, etc.)
 *   [2026]         → 'FY26'
 *   [2026, 2027]   → 'FY26, FY27'
 */
export function formatFiscalYears(years: number[]): string {
  if (years.length === 0) return '';
  return years.map(fyLabel).join(', ');
}
