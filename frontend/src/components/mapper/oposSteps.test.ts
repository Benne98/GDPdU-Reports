/**
 * Payload-parity guard for oposSteps.ts.
 *
 * Proves that toOposResult reproduces the exact columnMap / entityColumn /
 * sideColumn / debitorValue / kreditorValue payloads that commitOpos and
 * commitOposCombined consume, and that the new field set is correct:
 *
 *   REQUIRED : partner_no, konto, amount_hauswaehrung, net_due_date, posting_date
 *   SKIPPABLE: belegart, beleg_no, satzart, buchungskreis
 *   REMOVED  : referenz (not consumed by opos_aging.py)
 */

import { describe, it, expect } from 'vitest'
import { buildOposSteps, toOposResult } from './oposSteps'
import type { Answers } from './stepMapperTypes'

// ── helpers ───────────────────────────────────────────────────────────────────

const col  = (id: string): { t: 'column'; id: string }  => ({ t: 'column', id })
const skip = ():            { t: 'column'; id: null }    => ({ t: 'column', id: null })
const pick = (v: string):  { t: 'choice'; value: string } => ({ t: 'choice', value: v })

// ── buildOposSteps — step counts ─────────────────────────────────────────────

describe('buildOposSteps — step counts (9 target fields)', () => {
  it('per_side + per_entity: 9 steps', () => {
    expect(buildOposSteps('per_side', 'per_entity')).toHaveLength(9)
  })

  it('per_side + combined: 10 steps (9 fields + entity_column)', () => {
    expect(buildOposSteps('per_side', 'combined')).toHaveLength(10)
  })

  it('combined_sides + per_entity: 12 steps (9 fields + side + 2 choices)', () => {
    expect(buildOposSteps('combined_sides', 'per_entity')).toHaveLength(12)
  })

  it('combined_sides + combined: 13 steps (9 fields + entity + side + 2 choices)', () => {
    expect(buildOposSteps('combined_sides', 'combined')).toHaveLength(13)
  })
})

// ── buildOposSteps — required / skippable flags ───────────────────────────────

describe('buildOposSteps — required flags', () => {
  const steps = buildOposSteps('per_side', 'per_entity', 'debitor')

  const REQUIRED  = ['partner_no', 'konto', 'amount_hauswaehrung', 'net_due_date', 'posting_date']
  const SKIPPABLE = ['belegart', 'beleg_no', 'satzart', 'buchungskreis']

  for (const key of REQUIRED) {
    it(`${key} is required and not skippable`, () => {
      const s = steps.find(s => s.role === key)
      expect(s).toBeDefined()
      if (s?.kind === 'column') {
        expect(s.required).toBe(true)
        expect(s.skippable).toBe(false)
      }
    })
  }

  for (const key of SKIPPABLE) {
    it(`${key} is skippable (not required)`, () => {
      const s = steps.find(s => s.role === key)
      expect(s).toBeDefined()
      if (s?.kind === 'column') {
        expect(s.required).toBe(false)
        expect(s.skippable).toBe(true)
      }
    })
  }

  it('referenz step is absent (field removed from wizard)', () => {
    expect(steps.find(s => s.role === 'referenz')).toBeUndefined()
  })

  it('side_column step is required in combined_sides mode', () => {
    const csteps = buildOposSteps('combined_sides', 'per_entity')
    const sideStep = csteps.find(s => s.role === 'side_column')
    expect(sideStep?.kind).toBe('column')
    if (sideStep?.kind === 'column') {
      expect(sideStep.required).toBe(true)
      expect(sideStep.skippable).toBe(false)
    }
  })

  it('entity_column is skippable in combined viewMode', () => {
    const csteps = buildOposSteps('per_side', 'combined')
    const entStep = csteps.find(s => s.role === 'entity_column')
    expect(entStep?.kind).toBe('column')
    if (entStep?.kind === 'column') {
      expect(entStep.required).toBeFalsy()
      expect(entStep.skippable).toBe(true)
    }
  })
})

// ── buildOposSteps — step structure ───────────────────────────────────────────

describe('buildOposSteps — structure', () => {
  it('debitor_value and kreditor_value are choice steps in combined_sides', () => {
    const steps = buildOposSteps('combined_sides', 'per_entity')
    expect(steps.find(s => s.role === 'debitor_value')?.kind).toBe('choice')
    expect(steps.find(s => s.role === 'kreditor_value')?.kind).toBe('choice')
  })

  it('no side_column or choice steps in per_side mode', () => {
    const steps = buildOposSteps('per_side', 'combined')
    expect(steps.some(s => s.role === 'side_column')).toBe(false)
    expect(steps.some(s => s.role === 'debitor_value')).toBe(false)
    expect(steps.some(s => s.role === 'kreditor_value')).toBe(false)
  })

  it('entity_column step present only in combined viewMode', () => {
    const withEntity = buildOposSteps('per_side', 'combined')
    const noEntity   = buildOposSteps('per_side', 'per_entity')
    expect(withEntity.some(s => s.role === 'entity_column')).toBe(true)
    expect(noEntity.some(s => s.role === 'entity_column')).toBe(false)
  })

  it('partner_no is the first field step', () => {
    expect(buildOposSteps('per_side', 'per_entity', 'debitor')[0].role).toBe('partner_no')
  })

  it('konto is the second field step', () => {
    expect(buildOposSteps('per_side', 'per_entity', 'debitor')[1].role).toBe('konto')
  })
})

// ── buildOposSteps — side-aware partner_no label ─────────────────────────────

describe('buildOposSteps — side-aware partner_no label', () => {
  it('debitor side → "Debitor number (customer)"', () => {
    const steps = buildOposSteps('per_side', 'per_entity', 'debitor')
    expect(steps.find(s => s.role === 'partner_no')?.label).toBe('Debitor number (customer)')
  })

  it('kreditor side → "Kreditor number (supplier)"', () => {
    const steps = buildOposSteps('per_side', 'per_entity', 'kreditor')
    expect(steps.find(s => s.role === 'partner_no')?.label).toBe('Kreditor number (supplier)')
  })

  it('no side (combined_sides) → "Debitor / Kreditor number (partner)"', () => {
    const steps = buildOposSteps('combined_sides', 'per_entity')
    expect(steps.find(s => s.role === 'partner_no')?.label).toBe('Debitor / Kreditor number (partner)')
  })

  it('other fields are unaffected by side parameter', () => {
    const debSteps  = buildOposSteps('per_side', 'per_entity', 'debitor')
    const kredSteps = buildOposSteps('per_side', 'per_entity', 'kreditor')
    const kontoLabelDeb  = debSteps.find(s => s.role === 'konto')?.label
    const kontoLabelKred = kredSteps.find(s => s.role === 'konto')?.label
    expect(kontoLabelDeb).toBe(kontoLabelKred)
    expect(kontoLabelDeb).toContain('G/L reconciliation account')
  })
})

// ── toOposResult — per-side payload ──────────────────────────────────────────

describe('toOposResult — per-side payload', () => {
  it('includes partner_no and excludes referenz; maps all provided columns', () => {
    const answers: Answers = {
      partner_no:          col('Debitor_Nr'),
      konto:               col('Sachkonto'),
      amount_hauswaehrung: col('Betrag_HW'),
      net_due_date:        col('Faelligkeit'),
      posting_date:        col('Buchungsdatum'),
      belegart:            col('BelArt'),
      beleg_no:            skip(),   // explicitly skipped
      satzart:             skip(),
      buchungskreis:       col('BuKr_Col'),
      entity_column:       col('BuKr'),
      // referenz intentionally absent — field no longer in wizard
    }
    const result = toOposResult(answers)
    expect(result.columnMap).toEqual({
      partner_no:          'Debitor_Nr',
      konto:               'Sachkonto',
      amount_hauswaehrung: 'Betrag_HW',
      net_due_date:        'Faelligkeit',
      posting_date:        'Buchungsdatum',
      belegart:            'BelArt',
      buchungskreis:       'BuKr_Col',
    })
    expect('referenz' in result.columnMap).toBe(false)
    expect('beleg_no' in result.columnMap).toBe(false)
    expect('satzart' in result.columnMap).toBe(false)
    expect(result.entityColumn).toBe('BuKr')
    expect(result.sideColumn).toBeUndefined()
    expect(result.debitorValue).toBeUndefined()
    expect(result.kreditorValue).toBeUndefined()
  })

  it('referenz in answers is silently ignored (not in OPOS_TARGET_FIELDS)', () => {
    const answers: Answers = {
      referenz:            col('Referenz_Col'),  // stale / legacy answer key
      partner_no:          col('KdNr'),
      konto:               col('Konto'),
      amount_hauswaehrung: col('Betrag'),
      net_due_date:        col('Faellig'),
      posting_date:        col('BuchDat'),
    }
    const result = toOposResult(answers)
    expect('referenz' in result.columnMap).toBe(false)
    expect(result.columnMap['partner_no']).toBe('KdNr')
  })

  it('skipped nulls are excluded from columnMap', () => {
    const answers: Answers = {
      partner_no:          col('KdNr'),
      konto:               col('Konto'),
      amount_hauswaehrung: skip(),
      net_due_date:        skip(),
      posting_date:        skip(),
    }
    const result = toOposResult(answers)
    expect(Object.keys(result.columnMap)).toEqual(['partner_no', 'konto'])
  })

  it('entityColumn is undefined when skipped (id=null)', () => {
    const answers: Answers = {
      partner_no:    col('KdNr'),
      konto:         col('Konto'),
      entity_column: skip(),
    }
    expect(toOposResult(answers).entityColumn).toBeUndefined()
  })
})

// ── toOposResult — combined-sides payload ─────────────────────────────────────

describe('toOposResult — combined-sides payload', () => {
  it('includes partner_no, excludes referenz, returns side-discriminator fields', () => {
    const answers: Answers = {
      partner_no:          col('PartnerNr'),
      konto:               col('Konto'),
      amount_hauswaehrung: col('Betrag HW'),
      net_due_date:        col('Faellig'),
      posting_date:        col('BuchDat'),
      belegart:            skip(),
      beleg_no:            skip(),
      satzart:             skip(),
      buchungskreis:       skip(),
      side_column:         col('Deb./Kred.'),
      debitor_value:       pick('D'),
      kreditor_value:      pick('K'),
    }
    const result = toOposResult(answers)
    expect(result.columnMap).toEqual({
      partner_no:          'PartnerNr',
      konto:               'Konto',
      amount_hauswaehrung: 'Betrag HW',
      net_due_date:        'Faellig',
      posting_date:        'BuchDat',
    })
    expect('referenz' in result.columnMap).toBe(false)
    expect(result.sideColumn).toBe('Deb./Kred.')
    expect(result.debitorValue).toBe('D')
    expect(result.kreditorValue).toBe('K')
    expect(result.entityColumn).toBeUndefined()
  })

  it('combined-sides with entity column', () => {
    const answers: Answers = {
      partner_no:          col('PartnerNr'),
      konto:               col('Konto'),
      amount_hauswaehrung: col('Betrag'),
      net_due_date:        col('Faellig'),
      posting_date:        col('BuchDat'),
      side_column:         col('Seite'),
      debitor_value:       pick('Deb'),
      kreditor_value:      pick('Kred'),
      entity_column:       col('BuKr'),
    }
    const result = toOposResult(answers)
    expect(result.entityColumn).toBe('BuKr')
    expect(result.sideColumn).toBe('Seite')
    expect(result.debitorValue).toBe('Deb')
    expect(result.kreditorValue).toBe('Kred')
  })

  it('enforce distinct: debitor === kreditor → both undefined (commit stays gated)', () => {
    const answers: Answers = {
      partner_no:          col('PartnerNr'),
      konto:               col('Konto'),
      amount_hauswaehrung: col('Betrag'),
      net_due_date:        col('Faellig'),
      posting_date:        col('BuchDat'),
      side_column:         col('Seite'),
      debitor_value:       pick('X'),
      kreditor_value:      pick('X'),  // same value — invalid
    }
    const result = toOposResult(answers)
    expect(result.debitorValue).toBeUndefined()
    expect(result.kreditorValue).toBeUndefined()
  })

  it('enforce distinct: only debitor set → both undefined', () => {
    const answers: Answers = {
      partner_no:          col('PartnerNr'),
      konto:               col('Konto'),
      amount_hauswaehrung: col('Betrag'),
      net_due_date:        col('Faellig'),
      posting_date:        col('BuchDat'),
      side_column:         col('Seite'),
      debitor_value:       pick('D'),
      // kreditor_value not answered
    }
    const result = toOposResult(answers)
    expect(result.debitorValue).toBeUndefined()
    expect(result.kreditorValue).toBeUndefined()
  })
})
