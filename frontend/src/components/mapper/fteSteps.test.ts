/**
 * Payload-parity test for fteSteps.toFteResult().
 *
 * Verifies that toFteResult() produces the same payload shape as the old
 * FteColumnMapper.buildPayload() for equivalent inputs — the highest-risk
 * contract of the slice-2 mapper rewire.
 *
 * Run: npx vitest run src/components/mapper/fteSteps.test.ts
 */

import { describe, it, expect } from 'vitest'
import { toFteResult, buildFteSteps } from './fteSteps'
import type { Answers, NormalizedPreview } from './stepMapperTypes'

// ---------------------------------------------------------------------------
// Shared fixture preview
// ---------------------------------------------------------------------------

/**
 * NormalizedPreview from fromLetterPreview():
 *   id     = Excel letter  (used in column_names values)
 *   header = column name   (used in *_col fields)
 */
const PREVIEW: NormalizedPreview = {
  columns: [
    { id: 'A', header: 'PersonID',       sampleValues: ['1', '2', '3'] },
    { id: 'B', header: 'EmploymentPct',  sampleValues: ['100', '80', '60'] },
    { id: 'C', header: 'MonthsEmployed', sampleValues: ['12', '10', '6'] },
    { id: 'D', header: 'EntryDate',      sampleValues: ['2023-01-01', '2023-03-01', '2023-07-01'] },
    { id: 'E', header: 'ExitDate',       sampleValues: ['', '2023-12-31', ''] },
    { id: 'F', header: 'TotalCost',      sampleValues: ['50000', '40000', '30000'] },
    { id: 'G', header: 'MonthlyCost',    sampleValues: ['4167', '3333', '2500'] },
    { id: 'H', header: 'SocialSecurity', sampleValues: ['5000', '4000', '3000'] },
    { id: 'I', header: 'SalaryComp',     sampleValues: ['45000', '36000', '27000'] },
    { id: 'J', header: 'BonusComp',      sampleValues: ['5000', '4000', '3000'] },
    { id: 'K', header: 'Department',     sampleValues: ['IT', 'Finance', 'HR'] },
    { id: 'L', header: 'FiscalYear',     sampleValues: ['2023', '2023', '2023'] },
  ],
  rows: [
    ['1', '100', '12', '2023-01-01', '',           '50000', '4167', '5000', '45000', '5000', 'IT',      '2023'],
    ['2', '80',  '10', '2023-03-01', '2023-12-31', '40000', '3333', '4000', '36000', '4000', 'Finance', '2023'],
  ],
}

// ---------------------------------------------------------------------------
// Scenario 1: months_col tenure + total_col payroll (most common path)
// ---------------------------------------------------------------------------

describe('toFteResult — months_col + total_col', () => {
  const answers: Answers = {
    tenure_basis:   { t: 'choice',     value: 'months_col' },
    employment_pct: { t: 'column',     id: 'B' },
    months:         { t: 'column',     id: 'C' },
    payroll_basis:  { t: 'choice',     value: 'total_col' },
    total:          { t: 'column',     id: 'F' },
    social:         { t: 'column',     id: 'H' },
    dimensions:     { t: 'multiColumn', items: [{ id: 'K', label: 'Department' }] },
    preset_metrics: { t: 'checklist',  values: ['fte', 'payroll'] },
    custom_columns: { t: 'custom',     rows: [] },
  }

  const result = toFteResult(answers, PREVIEW)

  it('fteMapping matches buildPayload(mode="fte")', () => {
    expect(result.fteMapping).toEqual({
      tenure_mode:       'months_col',
      payroll_mode:      'total_col',
      employment_pct_col: 'EmploymentPct',
      months_col:         'MonthsEmployed',
      column_names: {
        employment_pct: 'B',
        months:         'C',
      },
    })
  })

  it('payrollMapping matches buildPayload(mode="payroll")', () => {
    expect(result.payrollMapping).toEqual({
      tenure_mode:  'months_col',
      payroll_mode: 'total_col',
      total_col:    'TotalCost',
      social_col:   'SocialSecurity',
      column_names: {
        total:  'F',
        social: 'H',
      },
    })
  })

  it('tenureMode / payrollMode scalars', () => {
    expect(result.tenureMode).toBe('months_col')
    expect(result.payrollMode).toBe('total_col')
  })

  it('dimensions: source_col = header, source_letter = id', () => {
    expect(result.dimensions).toEqual([
      { source_col: 'Department', output_label: 'Department', source_letter: 'K' },
    ])
  })

  it('presetMetrics passes through checklist values', () => {
    expect(result.presetMetrics).toEqual(['fte', 'payroll'])
  })

  it('customMetrics is empty', () => {
    expect(result.customMetrics).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Scenario 1b: named dimension columns emit *_col header names (DB ingest)
// ---------------------------------------------------------------------------

describe('toFteResult — named dimension columns', () => {
  it('emits mapped *_col headers and omits skipped ones', () => {
    const answers: Answers = {
      tenure_basis:   { t: 'choice', value: 'months_col' },
      employment_pct: { t: 'column', id: 'B' },
      months:         { t: 'column', id: 'C' },
      payroll_basis:  { t: 'choice', value: 'total_col' },
      total:          { t: 'column', id: 'F' },
      social:         { t: 'column', id: null },
      // named dims: Department -> bereich_col; personnel number -> A; rest skipped
      bereich:        { t: 'column', id: 'K' },
      kst_name:       { t: 'column', id: null },
      personalnummer: { t: 'column', id: 'A' },
      dimensions:     { t: 'multiColumn', items: [] },
      preset_metrics: { t: 'checklist', values: [] },
      custom_columns: { t: 'custom', rows: [] },
    }
    const result = toFteResult(answers, PREVIEW)
    expect(result.bereich_col).toBe('Department')
    expect(result.personalnummer_col).toBe('PersonID')
    expect(result).not.toHaveProperty('kst_name_col')
    expect(result).not.toHaveProperty('gew_ang_col')
    expect(result).not.toHaveProperty('bereichuntergruppe_col')
  })
})

// ---------------------------------------------------------------------------
// Scenario 2: entry_exit_dates + sum_components (tests all other code paths)
// ---------------------------------------------------------------------------

describe('toFteResult — entry_exit_dates + sum_components', () => {
  const answers: Answers = {
    tenure_basis:   { t: 'choice',     value: 'entry_exit_dates' },
    employment_pct: { t: 'column',     id: 'B' },
    entry:          { t: 'column',     id: 'D' },
    exit:           { t: 'column',     id: null },    // skipped — optional
    payroll_basis:  { t: 'choice',     value: 'sum_components' },
    component_cols: { t: 'multiColumn', items: [{ id: 'I' }, { id: 'J' }] },
    dimensions:     { t: 'multiColumn', items: [] },
    preset_metrics: { t: 'checklist',  values: [] },
    custom_columns: { t: 'custom',     rows: [] },
  }

  const result = toFteResult(answers, PREVIEW)

  it('fteMapping: entry_col set, exit_col absent (null skipped)', () => {
    expect(result.fteMapping).toEqual({
      tenure_mode:       'entry_exit_dates',
      payroll_mode:      'sum_components',
      employment_pct_col: 'EmploymentPct',
      entry_col:          'EntryDate',
      column_names: {
        employment_pct: 'B',
        entry:          'D',
      },
    })
    // exit_col must NOT appear when skipped
    expect(result.fteMapping).not.toHaveProperty('exit_col')
  })

  it('payrollMapping: component_cols = headers, column_names.components = letter CSV', () => {
    expect(result.payrollMapping).toEqual({
      tenure_mode:    'entry_exit_dates',
      payroll_mode:   'sum_components',
      component_cols: ['SalaryComp', 'BonusComp'],
      column_names: {
        components: 'I,J',
      },
    })
  })

  it('empty dimensions / preset_metrics / customMetrics', () => {
    expect(result.dimensions).toEqual([])
    expect(result.presetMetrics).toEqual([])
    expect(result.customMetrics).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Scenario 3: monthly_col payroll
// ---------------------------------------------------------------------------

describe('toFteResult — months_col + monthly_col', () => {
  const answers: Answers = {
    tenure_basis:   { t: 'choice', value: 'months_col' },
    employment_pct: { t: 'column', id: 'B' },
    months:         { t: 'column', id: 'C' },
    payroll_basis:  { t: 'choice', value: 'monthly_col' },
    monthly:        { t: 'column', id: 'G' },
    social:         { t: 'column', id: null },    // skipped
    dimensions:     { t: 'multiColumn', items: [] },
    preset_metrics: { t: 'checklist',  values: [] },
    custom_columns: { t: 'custom',     rows: [] },
  }

  const result = toFteResult(answers, PREVIEW)

  it('payrollMapping uses monthly_col, no social when skipped', () => {
    expect(result.payrollMapping).toEqual({
      tenure_mode:  'months_col',
      payroll_mode: 'monthly_col',
      monthly_col:  'MonthlyCost',
      column_names: { monthly: 'G' },
    })
    expect(result.payrollMapping).not.toHaveProperty('social_col')
  })
})

// ---------------------------------------------------------------------------
// Scenario 4: single_combined_file upload mode — year column appears
// ---------------------------------------------------------------------------

describe('buildFteSteps — single_combined_file includes year step', () => {
  it('step list includes year role when uploadMode=single_combined_file', () => {
    const steps = buildFteSteps({}, 'single_combined_file')
    expect(steps.some(s => s.role === 'year')).toBe(true)
  })

  it('step list excludes year role for per_fy_grid', () => {
    const steps = buildFteSteps({}, 'per_fy_grid')
    expect(steps.some(s => s.role === 'year')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Scenario 5: year column flows into fteMapping when uploadMode=single_combined_file
// ---------------------------------------------------------------------------

describe('toFteResult — year_col when single_combined_file', () => {
  const answers: Answers = {
    tenure_basis:   { t: 'choice', value: 'months_col' },
    employment_pct: { t: 'column', id: 'B' },
    months:         { t: 'column', id: 'C' },
    year:           { t: 'column', id: 'L' },
    payroll_basis:  { t: 'choice', value: 'total_col' },
    total:          { t: 'column', id: 'F' },
    social:         { t: 'column', id: null },
    dimensions:     { t: 'multiColumn', items: [] },
    preset_metrics: { t: 'checklist',  values: [] },
    custom_columns: { t: 'custom',     rows: [] },
  }

  const result = toFteResult(answers, PREVIEW)

  it('fteMapping includes year_col and column_names.year', () => {
    expect(result.fteMapping).toMatchObject({
      year_col: 'FiscalYear',
      column_names: expect.objectContaining({ year: 'L' }),
    })
  })
})
