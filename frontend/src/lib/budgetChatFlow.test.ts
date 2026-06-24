/**
 * Unit checks for budgetChatFlow.ts — pure step machine + reducer.
 *
 * Run: npx tsx src/lib/budgetChatFlow.test.ts
 *
 * Uses only node:assert (no test framework required).
 */
import assert from 'node:assert/strict';
import {
  CHAT_STEPS,
  applyAnswer,
  getDependents,
  granularityOptionsFor,
  initialDraft,
  nextStep,
  prevStep,
  reApplyAnswer,
  visibleSteps,
  fyLabel,
  formatFiscalYears,
  formatGranularity,
  hasAnyPartnerGranularity,
  deriveLevel,
  positionKey,
  togglePositionInputMode,
  getPositionInputMode,
  type BudgetDraft,
  type StepId,
  type PositionGranularity,
} from './budgetChatFlow';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function blankDraft(): BudgetDraft {
  return initialDraft();
}

/** Apply a sequence of [stepId, value] pairs to a draft in order. */
function applySequence(
  draft: BudgetDraft,
  steps: Array<[StepId, string | string[] | number]>,
): BudgetDraft {
  let d = draft;
  for (const [id, val] of steps) {
    d = applyAnswer(d, id, val);
  }
  return d;
}

// ---------------------------------------------------------------------------
// 1. CHAT_STEPS order and IDs
// ---------------------------------------------------------------------------

const EXPECTED_ORDER: StepId[] = [
  'entity',
  'fiscal_year',
  'statement',
  'period',
  'granularity',
  'start_values',
  'heuristic_method',
  'heuristic_growth',
  'excel_upload',
  'partner_start',
  'review',
];

{
  const ids = CHAT_STEPS.map(s => s.id);
  assert.deepEqual(ids, EXPECTED_ORDER, 'CHAT_STEPS ids must match expected order');
}

// ---------------------------------------------------------------------------
// 2. granularity step type is now 'position_granularity'
// ---------------------------------------------------------------------------

{
  const granStep = CHAT_STEPS.find(s => s.id === 'granularity')!;
  assert.equal(granStep.type, 'position_granularity', "granularity step type must be 'position_granularity'");
}

// ---------------------------------------------------------------------------
// 3. showWhen — steps always visible vs conditionally visible
// ---------------------------------------------------------------------------

const ALWAYS_VISIBLE: StepId[] = [
  'entity', 'fiscal_year', 'statement', 'period', 'granularity',
  'start_values', 'review',
];

{
  const draft = blankDraft();
  for (const id of ALWAYS_VISIBLE) {
    const step = CHAT_STEPS.find(s => s.id === id)!;
    assert.ok(step.showWhen(draft), `step '${id}' must be visible by default`);
  }
}

// ---------------------------------------------------------------------------
// 4. showWhen — BS-only hides heuristic_method, heuristic_growth, excel_upload,
//    and the L4 option in granularityOptionsFor
// ---------------------------------------------------------------------------

{
  const bsDraft = applyAnswer(blankDraft(), 'statement', 'BS');
  assert.deepEqual(bsDraft.statements, ['BS'], 'BS selection sets statements = [BS]');

  const opts = granularityOptionsFor(bsDraft);
  const values = opts.map(o => o.value);
  assert.ok(values.includes('L3'), 'L3 always present for BS');
  assert.ok(!values.includes('L4'), 'L4 must NOT appear when BS-only');
  assert.ok(!values.includes('partner_customers'), 'partner_customers must NOT appear for BS-only');
  assert.ok(!values.includes('partner_suppliers'), 'partner_suppliers must NOT appear for BS-only');
}

// ---------------------------------------------------------------------------
// 5. showWhen — heuristic_method only when start.mode === 'heuristic'
// ---------------------------------------------------------------------------

{
  const heuristicStep = CHAT_STEPS.find(s => s.id === 'heuristic_method')!;
  const heuristicDraft = applyAnswer(blankDraft(), 'start_values', 'heuristic');
  assert.ok(heuristicStep.showWhen(heuristicDraft), 'heuristic_method shown for heuristic mode');

  const excelDraft = applyAnswer(blankDraft(), 'start_values', 'excel');
  assert.ok(!heuristicStep.showWhen(excelDraft), 'heuristic_method hidden for excel mode');

  const blankModeDraft = applyAnswer(blankDraft(), 'start_values', 'blank');
  assert.ok(!heuristicStep.showWhen(blankModeDraft), 'heuristic_method hidden for blank mode');
}

// ---------------------------------------------------------------------------
// 6. showWhen — heuristic_growth only when start.mode === 'heuristic' AND
//    heuristic === 'prior_year'
// ---------------------------------------------------------------------------

{
  const growthStep = CHAT_STEPS.find(s => s.id === 'heuristic_growth')!;

  const priorYearDraft = applySequence(blankDraft(), [
    ['start_values', 'heuristic'],
    ['heuristic_method', 'prior_year'],
  ]);
  assert.ok(growthStep.showWhen(priorYearDraft), 'heuristic_growth shown for prior_year');

  const cagrDraft = applySequence(blankDraft(), [
    ['start_values', 'heuristic'],
    ['heuristic_method', 'trend_cagr'],
  ]);
  assert.ok(!growthStep.showWhen(cagrDraft), 'heuristic_growth hidden for trend_cagr');

  const runRateDraft = applySequence(blankDraft(), [
    ['start_values', 'heuristic'],
    ['heuristic_method', 'run_rate'],
  ]);
  assert.ok(!growthStep.showWhen(runRateDraft), 'heuristic_growth hidden for run_rate');
}

// ---------------------------------------------------------------------------
// 7. showWhen — excel_upload only when start.mode === 'excel'
// ---------------------------------------------------------------------------

{
  const excelStep = CHAT_STEPS.find(s => s.id === 'excel_upload')!;

  const excelDraft = applyAnswer(blankDraft(), 'start_values', 'excel');
  assert.ok(excelStep.showWhen(excelDraft), 'excel_upload shown when start=excel');

  const heuristicDraft = applyAnswer(blankDraft(), 'start_values', 'heuristic');
  assert.ok(!excelStep.showWhen(heuristicDraft), 'excel_upload hidden when start=heuristic');

  const blankModeDraft = applyAnswer(blankDraft(), 'start_values', 'blank');
  assert.ok(!excelStep.showWhen(blankModeDraft), 'excel_upload hidden when start=blank');
}

// ---------------------------------------------------------------------------
// 8. showWhen — partner_start only when any position has customers/suppliers
// ---------------------------------------------------------------------------

{
  const partnerStep = CHAT_STEPS.find(s => s.id === 'partner_start')!;

  // No partner granularity — hidden
  const noPartnerDraft = blankDraft();
  assert.ok(!partnerStep.showWhen(noPartnerDraft), 'partner_start hidden when no partner granularity');

  // customers granularity on any position — shown
  const customersDraft: BudgetDraft = {
    ...blankDraft(),
    granularityByPosition: { 'PL|rev-gross': 'customers' },
  };
  assert.ok(partnerStep.showWhen(customersDraft), 'partner_start shown when customers granularity set');

  // suppliers granularity on any position — shown
  const suppliersDraft: BudgetDraft = {
    ...blankDraft(),
    granularityByPosition: { 'PL|cost-mat': 'suppliers' },
  };
  assert.ok(partnerStep.showWhen(suppliersDraft), 'partner_start shown when suppliers granularity set');

  // only L3 / L4 — hidden
  const l4OnlyDraft: BudgetDraft = {
    ...blankDraft(),
    granularityByPosition: { 'PL|rev-gross': 'L4', 'PL|cost-mat': 'L3' },
  };
  assert.ok(!partnerStep.showWhen(l4OnlyDraft), 'partner_start hidden when only L3/L4 granularity');
}

// ---------------------------------------------------------------------------
// 9. applyAnswer — granularity with per-position map (new model)
// ---------------------------------------------------------------------------

{
  const map: Record<string, PositionGranularity> = {
    'PL|rev-gross': 'customers',
    'PL|cost-mat': 'suppliers',
    'PL|opex': 'L4',
    'BS|ar': 'L3',
  };
  const d = applyAnswer(blankDraft(), 'granularity', map);

  assert.deepEqual(d.granularityByPosition, map, 'granularityByPosition set from map');
  assert.equal(d.level, 'L4', 'level derived as L4 (any L4 in map)');
  assert.ok(d.partner.customers, 'customers derived true from map');
  assert.ok(d.partner.suppliers, 'suppliers derived true from map');
}

// ---------------------------------------------------------------------------
// 10. applyAnswer — granularity legacy string[] path still works (for tests)
// ---------------------------------------------------------------------------

{
  // L4 + customers
  const d1 = applyAnswer(blankDraft(), 'granularity', ['L4', 'partner_customers']);
  assert.equal(d1.level, 'L4', 'legacy: granularity L4 sets level=L4');
  assert.ok(d1.partner.customers, 'legacy: partner_customers sets customers=true');
  assert.ok(!d1.partner.suppliers, 'legacy: partner.suppliers stays false');

  // L3 + suppliers
  const d2 = applyAnswer(blankDraft(), 'granularity', ['L3', 'partner_suppliers']);
  assert.equal(d2.level, 'L3', 'legacy: granularity L3 sets level=L3');
  assert.ok(!d2.partner.customers, 'legacy: partner.customers stays false');
  assert.ok(d2.partner.suppliers, 'legacy: partner_suppliers sets suppliers=true');

  // only L3 — no partners
  const d3 = applyAnswer(blankDraft(), 'granularity', ['L3']);
  assert.equal(d3.level, 'L3', 'legacy: L3-only sets level=L3');
  assert.ok(!d3.partner.customers, 'legacy: no partner_customers in selection');
  assert.ok(!d3.partner.suppliers, 'legacy: no partner_suppliers in selection');
}

// ---------------------------------------------------------------------------
// 11. applyAnswer — legacy BS-only forces L3 even when L4 is in the selection
// ---------------------------------------------------------------------------

{
  const bsDraft = applyAnswer(blankDraft(), 'statement', 'BS');
  const withL4 = applyAnswer(bsDraft, 'granularity', ['L4']);
  assert.equal(withL4.level, 'L3', 'BS-only must force level=L3 even if L4 selected');
}

// ---------------------------------------------------------------------------
// 12. applyAnswer — statement 'Both' expands to ['PL', 'BS']
// ---------------------------------------------------------------------------

{
  const bothDraft = applyAnswer(blankDraft(), 'statement', 'Both');
  assert.deepEqual(bothDraft.statements, ['PL', 'BS'], "statement='Both' expands to ['PL','BS']");

  const plDraft = applyAnswer(blankDraft(), 'statement', 'PL');
  assert.deepEqual(plDraft.statements, ['PL'], "statement='PL' sets ['PL']");

  const bsDraft = applyAnswer(blankDraft(), 'statement', 'BS');
  assert.deepEqual(bsDraft.statements, ['BS'], "statement='BS' sets ['BS']");
}

// ---------------------------------------------------------------------------
// 13. getDependents — invalidation rules
// ---------------------------------------------------------------------------

{
  const statDeps = getDependents('statement');
  assert.ok(statDeps.includes('granularity'), 'statement invalidates granularity');
  assert.ok(statDeps.includes('heuristic_method'), 'statement invalidates heuristic_method');
  assert.ok(statDeps.includes('heuristic_growth'), 'statement invalidates heuristic_growth');
  assert.ok(statDeps.includes('excel_upload'), 'statement invalidates excel_upload');
  assert.ok(statDeps.includes('partner_start'), 'statement invalidates partner_start');
  assert.ok(statDeps.includes('review'), 'statement invalidates review');

  const startDeps = getDependents('start_values');
  assert.ok(startDeps.includes('heuristic_method'), 'start_values invalidates heuristic_method');
  assert.ok(startDeps.includes('heuristic_growth'), 'start_values invalidates heuristic_growth');
  assert.ok(startDeps.includes('excel_upload'), 'start_values invalidates excel_upload');

  const hmDeps = getDependents('heuristic_method');
  assert.ok(hmDeps.includes('heuristic_growth'), 'heuristic_method invalidates heuristic_growth');
  assert.ok(hmDeps.includes('review'), 'heuristic_method invalidates review');
  assert.ok(!hmDeps.includes('start_values'), 'heuristic_method must not invalidate start_values');
}

// ---------------------------------------------------------------------------
// 14. reApplyAnswer — changing statement Both→BS clears granularityByPosition
//     and positions
// ---------------------------------------------------------------------------

{
  const map: Record<string, PositionGranularity> = {
    'PL|rev-gross': 'customers',
    'BS|ar': 'L3',
  };
  const withGranularity: BudgetDraft = {
    ...applyAnswer(blankDraft(), 'statement', 'Both'),
    granularityByPosition: map,
    positions: { 'rev-gross': { annual: 1000, source: 'heuristic' } },
    positionCount: 42,
  };

  const afterChange = reApplyAnswer(withGranularity, 'statement', 'BS');

  assert.deepEqual(afterChange.statements, ['BS'], 'statement changed to BS');
  assert.deepEqual(afterChange.granularityByPosition, {}, 'granularityByPosition cleared after statement change');
  assert.deepEqual(afterChange.positions, {}, 'positions cleared after statement change');
  assert.equal(afterChange.positionCount, undefined, 'positionCount cleared after statement change');
  assert.equal(afterChange.start.heuristic, undefined, 'heuristic cleared after statement change');
  assert.equal(afterChange.start.growthPct, undefined, 'growthPct cleared after statement change');
}

// ---------------------------------------------------------------------------
// 15. reApplyAnswer — changing granularity clears positions
// ---------------------------------------------------------------------------

{
  const map1: Record<string, PositionGranularity> = { 'PL|rev-gross': 'L3' };
  const map2: Record<string, PositionGranularity> = { 'PL|rev-gross': 'L4' };
  const withPositions: BudgetDraft = {
    ...applyAnswer(blankDraft(), 'granularity', map1),
    positions: { 'rev-gross': { annual: 500, source: 'manual' } },
    positionCount: 5,
  };

  const afterGranChange = reApplyAnswer(withPositions, 'granularity', map2);

  assert.deepEqual(afterGranChange.positions, {}, 'positions cleared on granularity change');
  assert.equal(afterGranChange.positionCount, undefined, 'positionCount cleared on granularity change');
  assert.equal(afterGranChange.granularityByPosition['PL|rev-gross'], 'L4', 'new granularity applied');
}

// ---------------------------------------------------------------------------
// 16. reApplyAnswer — changing start_values to excel clears heuristic sub-answers
// ---------------------------------------------------------------------------

{
  const withHeuristic: BudgetDraft = applySequence(blankDraft(), [
    ['start_values', 'heuristic'],
    ['heuristic_method', 'prior_year'],
    ['heuristic_growth', 3.5],
  ]);

  const afterExcel = reApplyAnswer(withHeuristic, 'start_values', 'excel');

  assert.equal(afterExcel.start.mode, 'excel', 'reApplyAnswer(start_values, excel): mode is excel');
  assert.equal(afterExcel.start.heuristic, undefined, 'heuristic method cleared when switching to excel');
  assert.equal(afterExcel.start.growthPct, undefined, 'growthPct cleared when switching to excel');
}

// ---------------------------------------------------------------------------
// 17. reApplyAnswer — changing heuristic_method clears only heuristic_growth
// ---------------------------------------------------------------------------

{
  const withGrowth: BudgetDraft = applySequence(blankDraft(), [
    ['start_values', 'heuristic'],
    ['heuristic_method', 'prior_year'],
    ['heuristic_growth', 7],
  ]);

  const afterCagr = reApplyAnswer(withGrowth, 'heuristic_method', 'trend_cagr');

  assert.equal(afterCagr.start.mode, 'heuristic', 'start.mode preserved when changing heuristic_method');
  assert.equal(afterCagr.start.heuristic, 'trend_cagr', 'new heuristic method applied');
  assert.equal(afterCagr.start.growthPct, undefined, 'growthPct cleared when heuristic_method changes');
}

// ---------------------------------------------------------------------------
// 18. visibleSteps — default draft shows the always-visible steps
// ---------------------------------------------------------------------------

{
  const draft = blankDraft();
  const visible = visibleSteps(draft);
  const visibleIds = visible.map(s => s.id);

  assert.ok(visibleIds.includes('heuristic_method'), 'heuristic_method visible by default (mode=heuristic)');
  assert.ok(!visibleIds.includes('heuristic_growth'), 'heuristic_growth hidden until prior_year chosen');
  assert.ok(!visibleIds.includes('excel_upload'), 'excel_upload hidden by default');
  assert.ok(!visibleIds.includes('partner_start'), 'partner_start hidden by default (no partner granularity)');
}

// ---------------------------------------------------------------------------
// 19. visibleSteps — partner_start visible when partner granularity is set
// ---------------------------------------------------------------------------

{
  const draftWithPartner: BudgetDraft = {
    ...blankDraft(),
    granularityByPosition: { 'PL|rev-gross': 'customers' },
  };
  const visible = visibleSteps(draftWithPartner).map(s => s.id);
  assert.ok(visible.includes('partner_start'), 'partner_start shown when customers granularity set');
}

// ---------------------------------------------------------------------------
// 20. visibleSteps — excel mode shows excel_upload, hides heuristic steps
// ---------------------------------------------------------------------------

{
  const excelDraft = applyAnswer(blankDraft(), 'start_values', 'excel');
  const visible = visibleSteps(excelDraft).map(s => s.id);

  assert.ok(visible.includes('excel_upload'), 'excel mode shows excel_upload');
  assert.ok(!visible.includes('heuristic_method'), 'excel mode hides heuristic_method');
  assert.ok(!visible.includes('heuristic_growth'), 'excel mode hides heuristic_growth');
}

// ---------------------------------------------------------------------------
// 21. nextStep / prevStep navigation
// ---------------------------------------------------------------------------

{
  const draft = applyAnswer(blankDraft(), 'start_values', 'blank');
  const visible = visibleSteps(draft).map(s => s.id);

  assert.equal(nextStep(draft, 'entity'), 'fiscal_year', 'entity → fiscal_year');
  assert.equal(nextStep(draft, visible[visible.length - 1]), null, 'last step → null');
  assert.equal(prevStep(draft, 'entity'), null, 'entity has no prev');
  assert.equal(prevStep(draft, 'fiscal_year'), 'entity', 'fiscal_year prev = entity');
}

// ---------------------------------------------------------------------------
// 22. positions last-write-wins: heuristic/excel base then manual override
// ---------------------------------------------------------------------------

{
  const base: BudgetDraft['positions'] = {
    'rev-gross':  { annual: 1000, source: 'heuristic' },
    'cost-mat':   { annual:  400, source: 'heuristic' },
  };
  const override: BudgetDraft['positions'] = {
    'rev-gross':  { annual: 1100, source: 'manual' },
    'cost-other': { annual:   50, source: 'manual' },
  };
  const merged = { ...base, ...override };

  assert.equal(merged['rev-gross'].annual, 1100, 'manual override wins for rev-gross');
  assert.equal(merged['rev-gross'].source, 'manual', 'source is manual after override');
  assert.equal(merged['cost-mat'].annual, 400, 'unmodified heuristic value preserved');
  assert.equal(merged['cost-other'].annual, 50, 'new manual entry present');
}

// ---------------------------------------------------------------------------
// 23. granularityOptionsFor — PL draft shows L3, L4, partners (legacy helper)
// ---------------------------------------------------------------------------

{
  const plDraft = applyAnswer(blankDraft(), 'statement', 'PL');
  const opts = granularityOptionsFor(plDraft);
  const values = opts.map(o => o.value);

  assert.ok(values.includes('L3'), 'L3 present for PL');
  assert.ok(values.includes('L4'), 'L4 present for PL');
  assert.ok(values.includes('partner_customers'), 'partner_customers present for PL');
  assert.ok(values.includes('partner_suppliers'), 'partner_suppliers present for PL');
}

// ---------------------------------------------------------------------------
// 24. granularityOptionsFor — Both draft shows L4 and partners (legacy helper)
// ---------------------------------------------------------------------------

{
  const bothDraft = applyAnswer(blankDraft(), 'statement', 'Both');
  const opts = granularityOptionsFor(bothDraft);
  const values = opts.map(o => o.value);

  assert.ok(values.includes('L4'), 'L4 present for Both');
  assert.ok(values.includes('partner_customers'), 'partner_customers present for Both');
}

// ---------------------------------------------------------------------------
// 25. fyLabel — short FY label formatting
// ---------------------------------------------------------------------------

{
  assert.equal(fyLabel(2026), 'FY26', 'fyLabel(2026) === FY26');
  assert.equal(fyLabel(2030), 'FY30', 'fyLabel(2030) === FY30');
  assert.equal(fyLabel(2025), 'FY25', 'fyLabel(2025) === FY25');
  assert.equal(fyLabel(2000), 'FY00', 'fyLabel(2000) === FY00');
  assert.equal(fyLabel(1999), 'FY99', 'fyLabel(1999) === FY99');
}

// ---------------------------------------------------------------------------
// 26. formatFiscalYears — display string for year array
// ---------------------------------------------------------------------------

{
  assert.equal(formatFiscalYears([2026]), 'FY26', 'single year renders as FY26');
  assert.equal(formatFiscalYears([2026, 2027, 2028]), 'FY26, FY27, FY28', 'multi-year renders comma-separated');
  assert.equal(formatFiscalYears([]), '', 'empty array renders as empty string');
}

// ---------------------------------------------------------------------------
// 27. applyAnswer fiscal_year — accepts number[], string[], or single value;
//     sorts ascending and deduplicates
// ---------------------------------------------------------------------------

{
  const d1 = applyAnswer(blankDraft(), 'fiscal_year', ['2027', '2026', '2028']);
  assert.deepEqual(d1.fiscalYears, [2026, 2027, 2028], 'fiscal_year string[] sorted ascending');

  const d2 = applyAnswer(blankDraft(), 'fiscal_year', 2029);
  assert.deepEqual(d2.fiscalYears, [2029], 'single number wrapped in array');

  const d3 = applyAnswer(blankDraft(), 'fiscal_year', ['2026', '2026', '2027']);
  assert.deepEqual(d3.fiscalYears, [2026, 2027], 'duplicate years deduplicated');
}

// ---------------------------------------------------------------------------
// 28. initialDraft — fiscalYears defaults to [currentYear+1], granularityByPosition empty
// ---------------------------------------------------------------------------

{
  const draft = initialDraft();
  const expectedYear = new Date().getFullYear() + 1;
  assert.deepEqual(draft.fiscalYears, [expectedYear], `initialDraft.fiscalYears defaults to [${expectedYear}]`);
  assert.deepEqual(draft.granularityByPosition, {}, 'initialDraft.granularityByPosition is empty');
  assert.equal(draft.level, 'L3', 'initialDraft.level defaults to L3');
}

// ---------------------------------------------------------------------------
// 29. getStepAnswer fiscal_year — returns string[] of years
// ---------------------------------------------------------------------------

{
  const { getStepAnswer } = await import('./budgetChatFlow');
  const draft = applyAnswer(blankDraft(), 'fiscal_year', ['2026', '2027']);
  const ans = getStepAnswer(draft, 'fiscal_year');
  assert.deepEqual(ans, ['2026', '2027'], 'getStepAnswer fiscal_year returns string[]');

  const emptyDraft = { ...blankDraft(), fiscalYears: [] };
  assert.equal(getStepAnswer(emptyDraft, 'fiscal_year'), undefined, 'getStepAnswer returns undefined for empty fiscalYears');
}

// ---------------------------------------------------------------------------
// 30. positionKey helper
// ---------------------------------------------------------------------------

{
  assert.equal(positionKey('PL', 'rev-gross'), 'PL|rev-gross', 'positionKey PL');
  assert.equal(positionKey('BS', 'ar'), 'BS|ar', 'positionKey BS');
}

// ---------------------------------------------------------------------------
// 31. deriveLevel — returns L4 if any position is L4, else L3
// ---------------------------------------------------------------------------

{
  assert.equal(deriveLevel({ 'PL|a': 'L3', 'PL|b': 'L3' }), 'L3', 'all L3 → level L3');
  assert.equal(deriveLevel({ 'PL|a': 'L4', 'PL|b': 'L3' }), 'L4', 'one L4 → level L4');
  assert.equal(deriveLevel({ 'PL|a': 'customers', 'BS|b': 'suppliers' }), 'L3', 'only partners → level L3');
  assert.equal(deriveLevel({}), 'L3', 'empty map → level L3');
}

// ---------------------------------------------------------------------------
// 32. hasAnyPartnerGranularity
// ---------------------------------------------------------------------------

{
  assert.ok(!hasAnyPartnerGranularity(blankDraft()), 'blank draft has no partner granularity');

  const withCustomers: BudgetDraft = {
    ...blankDraft(),
    granularityByPosition: { 'PL|rev-gross': 'customers' },
  };
  assert.ok(hasAnyPartnerGranularity(withCustomers), 'customers → true');

  const withSuppliers: BudgetDraft = {
    ...blankDraft(),
    granularityByPosition: { 'PL|cost': 'suppliers' },
  };
  assert.ok(hasAnyPartnerGranularity(withSuppliers), 'suppliers → true');

  const onlyL4: BudgetDraft = {
    ...blankDraft(),
    granularityByPosition: { 'PL|cost': 'L4' },
  };
  assert.ok(!hasAnyPartnerGranularity(onlyL4), 'L4-only → false');
}

// ---------------------------------------------------------------------------
// 33. formatGranularity — human-readable summary
// ---------------------------------------------------------------------------

{
  const draft: BudgetDraft = {
    ...blankDraft(),
    granularityByPosition: {
      'PL|rev-gross': 'customers',
      'PL|cost-mat': 'suppliers',
      'PL|opex': 'L4',
      'BS|ar': 'L3',
      'BS|ap': 'L3',
    },
  };
  const label = formatGranularity(draft);
  assert.ok(label.includes('2 at L3'), `formatGranularity includes "2 at L3" (got: ${label})`);
  assert.ok(label.includes('1 at L4'), `formatGranularity includes "1 at L4" (got: ${label})`);
  assert.ok(label.includes('1 by customers'), `formatGranularity includes "1 by customers" (got: ${label})`);
  assert.ok(label.includes('1 by suppliers'), `formatGranularity includes "1 by suppliers" (got: ${label})`);
}

// ---------------------------------------------------------------------------
// 34. getStepAnswer granularity — returns map when map has entries
// ---------------------------------------------------------------------------

{
  const { getStepAnswer } = await import('./budgetChatFlow');
  const map: Record<string, PositionGranularity> = { 'PL|rev-gross': 'L4' };
  const draft: BudgetDraft = { ...blankDraft(), granularityByPosition: map };
  const ans = getStepAnswer(draft, 'granularity');
  assert.deepEqual(ans, map, 'getStepAnswer granularity returns the map when non-empty');

  // Legacy fallback when map is empty
  const legacyDraft: BudgetDraft = {
    ...blankDraft(),
    granularityByPosition: {},
    level: 'L4',
    partner: { customers: true, suppliers: false, topN: 10 },
  };
  const legacyAns = getStepAnswer(legacyDraft, 'granularity');
  assert.ok(Array.isArray(legacyAns), 'legacy: getStepAnswer granularity returns array when map is empty');
  assert.ok((legacyAns as string[]).includes('L4'), 'legacy: includes L4');
  assert.ok((legacyAns as string[]).includes('partner_customers'), 'legacy: includes partner_customers');
}

// ---------------------------------------------------------------------------
// 35. direction and input_style steps must NOT exist in CHAT_STEPS
// ---------------------------------------------------------------------------

{
  const ids = CHAT_STEPS.map(s => s.id);
  assert.ok(!ids.includes('direction' as StepId), 'direction step must be removed from CHAT_STEPS');
  assert.ok(!ids.includes('input_style' as StepId), 'input_style step must be removed from CHAT_STEPS');
}

// ---------------------------------------------------------------------------
// 36. period step comes BEFORE granularity step
// ---------------------------------------------------------------------------

{
  const ids = CHAT_STEPS.map(s => s.id);
  const periodIdx = ids.indexOf('period');
  const granIdx = ids.indexOf('granularity');
  assert.ok(periodIdx !== -1, 'period step must exist');
  assert.ok(granIdx !== -1, 'granularity step must exist');
  assert.ok(periodIdx < granIdx, 'period must come before granularity in CHAT_STEPS');
}

// ---------------------------------------------------------------------------
// 37. reApplyAnswer — changing period clears granularityByPosition
// ---------------------------------------------------------------------------

{
  const map: Record<string, PositionGranularity> = { 'PL|rev-gross': 'L4', 'BS|ar': 'L3' };
  const withGran: BudgetDraft = {
    ...applyAnswer(blankDraft(), 'period', 'annual'),
    granularityByPosition: map,
  };

  const afterPeriodChange = reApplyAnswer(withGran, 'period', 'monthly');

  assert.equal(afterPeriodChange.viewMode, 'monthly', 'viewMode updated to monthly');
  assert.deepEqual(
    afterPeriodChange.granularityByPosition,
    {},
    'granularityByPosition cleared when period changes',
  );
  assert.equal(afterPeriodChange.level, 'L3', 'level reset to L3 when period changes');
}

// ---------------------------------------------------------------------------
// 38. getDependents — period invalidates granularity and downstream
// ---------------------------------------------------------------------------

{
  const periodDeps = getDependents('period');
  assert.ok(periodDeps.includes('granularity'), 'period invalidates granularity');
  assert.ok(periodDeps.includes('start_values'), 'period invalidates start_values');
  assert.ok(periodDeps.includes('review'), 'period invalidates review');
}

// ---------------------------------------------------------------------------
// 39. initialDraft — inputModeByPosition is empty, inputMode defaults to 'growth'
// ---------------------------------------------------------------------------

{
  const draft = initialDraft();
  assert.deepEqual(draft.inputModeByPosition, {}, 'initialDraft.inputModeByPosition is empty');
  assert.equal(draft.inputMode, 'growth', 'initialDraft.inputMode defaults to growth (deprecated compat)');
  assert.equal(draft.planningDir, 'top_down', 'initialDraft.planningDir defaults to top_down (deprecated compat)');
}

// ---------------------------------------------------------------------------
// 40. togglePositionInputMode — toggles between pct and absolute
// ---------------------------------------------------------------------------

{
  const empty: Record<string, 'pct' | 'absolute'> = {};

  // Default when key absent is 'pct' — toggling gives 'absolute'
  const afterFirst = togglePositionInputMode(empty, 'PL|rev-gross');
  assert.equal(afterFirst['PL|rev-gross'], 'absolute', 'toggle from default pct → absolute');

  // Toggle back
  const afterSecond = togglePositionInputMode(afterFirst, 'PL|rev-gross');
  assert.equal(afterSecond['PL|rev-gross'], 'pct', 'toggle from absolute → pct');

  // Other keys unaffected
  const withOther: Record<string, 'pct' | 'absolute'> = { 'BS|ar': 'absolute' };
  const toggled = togglePositionInputMode(withOther, 'PL|opex');
  assert.equal(toggled['BS|ar'], 'absolute', 'other keys unaffected by toggle');
  assert.equal(toggled['PL|opex'], 'absolute', 'new key toggled from default pct → absolute');
}

// ---------------------------------------------------------------------------
// 41. getPositionInputMode — returns pct for missing keys, stored value otherwise
// ---------------------------------------------------------------------------

{
  const map: Record<string, 'pct' | 'absolute'> = { 'PL|rev-gross': 'absolute' };
  assert.equal(getPositionInputMode(map, 'PL|rev-gross'), 'absolute', 'returns stored value');
  assert.equal(getPositionInputMode(map, 'PL|cost-mat'), 'pct', 'returns pct for missing key');
  assert.equal(getPositionInputMode({}, 'any'), 'pct', 'returns pct for empty map');
}

// ---------------------------------------------------------------------------
// 42. reApplyAnswer — changing granularity clears inputModeByPosition
// ---------------------------------------------------------------------------

{
  const map1: Record<string, PositionGranularity> = { 'PL|rev-gross': 'L3' };
  const map2: Record<string, PositionGranularity> = { 'PL|rev-gross': 'L4' };
  const withInputModes: BudgetDraft = {
    ...applyAnswer(blankDraft(), 'granularity', map1),
    inputModeByPosition: { 'PL|rev-gross': 'absolute' },
  };

  const afterGranChange = reApplyAnswer(withInputModes, 'granularity', map2);

  assert.deepEqual(
    afterGranChange.inputModeByPosition,
    {},
    'inputModeByPosition cleared when granularity changes',
  );
}

// ---------------------------------------------------------------------------
// 43. visibleSteps — direction and input_style are never in visible steps
// ---------------------------------------------------------------------------

{
  const draft = blankDraft();
  const visible = visibleSteps(draft).map(s => s.id);
  assert.ok(!visible.includes('direction' as StepId), 'direction never in visible steps');
  assert.ok(!visible.includes('input_style' as StepId), 'input_style never in visible steps');
}

// ---------------------------------------------------------------------------
// 44. nextStep — period → granularity (new order)
// ---------------------------------------------------------------------------

{
  const draft = blankDraft();
  assert.equal(nextStep(draft, 'statement'), 'period', 'statement → period (new order)');
  assert.equal(nextStep(draft, 'period'), 'granularity', 'period → granularity (new order)');
  assert.equal(nextStep(draft, 'granularity'), 'start_values', 'granularity → start_values');
}

// ---------------------------------------------------------------------------

console.log('budgetChatFlow.test.ts: all assertions passed');
