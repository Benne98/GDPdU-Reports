/**
 * Payload-parity guard for oposSteps.ts.
 *
 * Proves that toOposResult reproduces the exact columnMap / entityColumn /
 * sideColumn / debitorValue / kreditorValue payloads that commitOpos and
 * commitOposCombined consume.
 */

import { describe, it, expect } from 'vitest'
import { buildOposSteps, toOposResult } from './oposSteps'
import type { Answers } from './stepMapperTypes'

// ── buildOposSteps ────────────────────────────────────────────────────────────

describe('buildOposSteps', () => {
  it('per_side + per_entity: 7 steps (7 fields)', () => {
    expect(buildOposSteps('per_side', 'per_entity')).toHaveLength(7)
  })

  it('per_side + combined: 8 steps (7 fields + entity)', () => {
    expect(buildOposSteps('per_side', 'combined')).toHaveLength(8)
  })

  it('combined_sides + per_entity: 10 steps (7 fields + side + 2 choice)', () => {
    expect(buildOposSteps('combined_sides', 'per_entity')).toHaveLength(10)
  })

  it('combined_sides + combined: 11 steps (7 fields + entity + side + 2 choice)', () => {
    expect(buildOposSteps('combined_sides', 'combined')).toHaveLength(11)
  })

  it('konto step is required; all other field steps are skippable', () => {
    const steps = buildOposSteps('per_side', 'per_entity')
    for (const s of steps) {
      if (s.kind !== 'column') continue
      if (s.role === 'konto') {
        expect(s.required).toBe(true)
        expect(s.skippable).toBe(false)
      } else {
        expect(s.required).toBeFalsy()
        expect(s.skippable).toBe(true)
      }
    }
  })

  it('side_column step is required in combined_sides mode', () => {
    const steps = buildOposSteps('combined_sides', 'per_entity')
    const sideStep = steps.find(s => s.role === 'side_column')
    expect(sideStep).toBeDefined()
    expect(sideStep?.kind).toBe('column')
    if (sideStep?.kind === 'column') {
      expect(sideStep.required).toBe(true)
      expect(sideStep.skippable).toBe(false)
    }
  })

  it('debitor_value and kreditor_value are choice steps in combined_sides', () => {
    const steps = buildOposSteps('combined_sides', 'per_entity')
    const debStep = steps.find(s => s.role === 'debitor_value')
    const kreStep = steps.find(s => s.role === 'kreditor_value')
    expect(debStep?.kind).toBe('choice')
    expect(kreStep?.kind).toBe('choice')
  })

  it('no side_column or choice steps in per_side mode', () => {
    const steps = buildOposSteps('per_side', 'combined')
    expect(steps.some(s => s.role === 'side_column')).toBe(false)
    expect(steps.some(s => s.role === 'debitor_value')).toBe(false)
    expect(steps.some(s => s.role === 'kreditor_value')).toBe(false)
  })

  it('entity_column step present only in combined viewMode', () => {
    const withEntity = buildOposSteps('per_side', 'combined')
    const noEntity = buildOposSteps('per_side', 'per_entity')
    expect(withEntity.some(s => s.role === 'entity_column')).toBe(true)
    expect(noEntity.some(s => s.role === 'entity_column')).toBe(false)
  })
})

// ── toOposResult ──────────────────────────────────────────────────────────────

describe('toOposResult', () => {
  it('per-side payload: columnMap + entityColumn, no side fields', () => {
    const answers: Answers = {
      konto:               { t: 'column', id: 'Kontonummer' },
      belegart:            { t: 'column', id: 'BelArt'      },
      beleg_no:            { t: 'column', id: null          },
      referenz:            { t: 'column', id: null          },
      net_due_date:        { t: 'column', id: 'Fällig'      },
      amount_hauswaehrung: { t: 'column', id: 'Betrag'      },
      posting_date:        { t: 'column', id: null          },
      entity_column:       { t: 'column', id: 'BuKr'        },
    }
    const result = toOposResult(answers)
    // Matches column_map + entity_column sent to commitOpos
    expect(result.columnMap).toEqual({
      konto:               'Kontonummer',
      belegart:            'BelArt',
      net_due_date:        'Fällig',
      amount_hauswaehrung: 'Betrag',
    })
    expect(result.entityColumn).toBe('BuKr')
    expect(result.sideColumn).toBeUndefined()
    expect(result.debitorValue).toBeUndefined()
    expect(result.kreditorValue).toBeUndefined()
  })

  it('combined-sides payload: columnMap + sideColumn + debitor/kreditor values', () => {
    const answers: Answers = {
      konto:               { t: 'column', id: 'Konto'         },
      belegart:            { t: 'column', id: null            },
      beleg_no:            { t: 'column', id: null            },
      referenz:            { t: 'column', id: null            },
      net_due_date:        { t: 'column', id: null            },
      amount_hauswaehrung: { t: 'column', id: 'Betrag HW'     },
      posting_date:        { t: 'column', id: null            },
      side_column:         { t: 'column', id: 'Deb./Kred.'    },
      debitor_value:       { t: 'choice', value: 'D'          },
      kreditor_value:      { t: 'choice', value: 'K'          },
    }
    const result = toOposResult(answers)
    // Matches side_column + debitor_value + kreditor_value sent to commitOposCombined
    expect(result.columnMap).toEqual({
      konto:               'Konto',
      amount_hauswaehrung: 'Betrag HW',
    })
    expect(result.sideColumn).toBe('Deb./Kred.')
    expect(result.debitorValue).toBe('D')
    expect(result.kreditorValue).toBe('K')
    expect(result.entityColumn).toBeUndefined()
  })

  it('combined-sides with entity column', () => {
    const answers: Answers = {
      konto:          { t: 'column', id: 'Konto'    },
      side_column:    { t: 'column', id: 'Seite'    },
      debitor_value:  { t: 'choice', value: 'Deb'  },
      kreditor_value: { t: 'choice', value: 'Kred' },
      entity_column:  { t: 'column', id: 'BuKr'    },
    }
    const result = toOposResult(answers)
    expect(result.entityColumn).toBe('BuKr')
    expect(result.sideColumn).toBe('Seite')
    expect(result.debitorValue).toBe('Deb')
    expect(result.kreditorValue).toBe('Kred')
  })

  it('enforce distinct: debitor === kreditor → both undefined (commit stays gated)', () => {
    const answers: Answers = {
      konto:          { t: 'column', id: 'Konto' },
      side_column:    { t: 'column', id: 'Seite' },
      debitor_value:  { t: 'choice', value: 'D'  },
      kreditor_value: { t: 'choice', value: 'D'  }, // same value!
    }
    const result = toOposResult(answers)
    expect(result.debitorValue).toBeUndefined()
    expect(result.kreditorValue).toBeUndefined()
  })

  it('enforce distinct: only debitor set → both undefined', () => {
    const answers: Answers = {
      konto:         { t: 'column', id: 'Konto' },
      side_column:   { t: 'column', id: 'Seite' },
      debitor_value: { t: 'choice', value: 'D'  },
      // kreditor_value not answered
    }
    const result = toOposResult(answers)
    expect(result.debitorValue).toBeUndefined()
    expect(result.kreditorValue).toBeUndefined()
  })

  it('skipped nulls are excluded from columnMap', () => {
    const answers: Answers = {
      konto:               { t: 'column', id: 'Konto' },
      belegart:            { t: 'column', id: null    }, // skipped
      amount_hauswaehrung: { t: 'column', id: null    }, // skipped
    }
    const result = toOposResult(answers)
    expect(Object.keys(result.columnMap)).toEqual(['konto'])
  })

  it('entityColumn undefined when skipped (id=null)', () => {
    const answers: Answers = {
      konto:         { t: 'column', id: 'K'   },
      entity_column: { t: 'column', id: null  },
    }
    expect(toOposResult(answers).entityColumn).toBeUndefined()
  })
})
