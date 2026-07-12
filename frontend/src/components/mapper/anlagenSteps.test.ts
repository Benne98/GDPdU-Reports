/**
 * Payload-parity guard for anlagenSteps.ts.
 *
 * Proves that toAnlagenResult reproduces the exact columnMap / entityColumn /
 * dimensions payloads that commitAnlagen and WizardAnlagenState consume.
 */

import { describe, it, expect } from 'vitest'
import { buildAnlagenSteps, toAnlagenResult } from './anlagenSteps'
import type { Answers } from './stepMapperTypes'

// ── buildAnlagenSteps ─────────────────────────────────────────────────────────

describe('buildAnlagenSteps', () => {
  it('per_entity: 16 steps (13 fields + 3 dims, no entity step)', () => {
    expect(buildAnlagenSteps('per_entity')).toHaveLength(16)
  })

  it('combined: 17 steps (13 fields + entity + 3 dims)', () => {
    expect(buildAnlagenSteps('combined')).toHaveLength(17)
  })

  it('entity_column step is present only in combined mode', () => {
    const combined = buildAnlagenSteps('combined')
    const perEntity = buildAnlagenSteps('per_entity')
    expect(combined.some(s => s.role === 'entity_column')).toBe(true)
    expect(perEntity.some(s => s.role === 'entity_column')).toBe(false)
  })

  it('asset_id step is required; all other column steps are skippable', () => {
    const steps = buildAnlagenSteps('per_entity')
    for (const s of steps) {
      if (s.kind !== 'column') continue
      if (s.role === 'asset_id') {
        expect(s.required).toBe(true)
        expect(s.skippable).toBe(false)
      } else {
        expect(s.required).toBeFalsy()
        expect(s.skippable).toBe(true)
      }
    }
  })

  it('dimension steps appear at the end regardless of viewMode', () => {
    for (const mode of ['combined', 'per_entity']) {
      const steps = buildAnlagenSteps(mode)
      const roles = steps.map(s => s.role)
      const last3 = roles.slice(-3)
      expect(last3).toEqual(['segmentCol', 'assetClassCol', 'bilanzpositionCol'])
    }
  })
})

// ── toAnlagenResult ───────────────────────────────────────────────────────────

describe('toAnlagenResult', () => {
  it('produces exact columnMap payload (skipped nulls excluded)', () => {
    const answers: Answers = {
      asset_id:            { t: 'column', id: 'Anlage-Nr'   },
      asset_label:         { t: 'column', id: 'Bezeichnung' },
      asset_sub_no:        { t: 'column', id: null          },   // skipped
      asset_class:         { t: 'column', id: 'Klasse'      },
      segment:             { t: 'column', id: null          },
      bilanzposition:      { t: 'column', id: null          },
      capitalization_date: { t: 'column', id: null          },
      opening_cost_ahk:    { t: 'column', id: 'AHK'        },
      additions_zugang:    { t: 'column', id: null          },
      disposals_abgang:    { t: 'column', id: null          },
      transfers_umbuchung: { t: 'column', id: null          },
      depreciation:        { t: 'column', id: null          },
      nbv:                 { t: 'column', id: 'Buchwert'    },
    }
    const result = toAnlagenResult(answers)
    expect(result.columnMap).toEqual({
      asset_id:         'Anlage-Nr',
      asset_label:      'Bezeichnung',
      asset_class:      'Klasse',
      opening_cost_ahk: 'AHK',
      nbv:              'Buchwert',
    })
  })

  it('produces exact entityColumn from entity_column role', () => {
    const answers: Answers = {
      asset_id:      { t: 'column', id: 'ID'           },
      entity_column: { t: 'column', id: 'Company Code' },
    }
    expect(toAnlagenResult(answers).entityColumn).toBe('Company Code')
  })

  it('entityColumn is undefined when entity_column step not answered', () => {
    const answers: Answers = {
      asset_id: { t: 'column', id: 'ID' },
    }
    expect(toAnlagenResult(answers).entityColumn).toBeUndefined()
  })

  it('entityColumn is undefined when entity_column step is skipped (id=null)', () => {
    const answers: Answers = {
      asset_id:      { t: 'column', id: 'ID'   },
      entity_column: { t: 'column', id: null   },
    }
    expect(toAnlagenResult(answers).entityColumn).toBeUndefined()
  })

  it('produces exact dimensions payload', () => {
    const answers: Answers = {
      asset_id:          { t: 'column', id: 'ID'      },
      segmentCol:        { t: 'column', id: 'Segment' },
      assetClassCol:     { t: 'column', id: null      },   // skipped
      bilanzpositionCol: { t: 'column', id: 'BP'      },
    }
    expect(toAnlagenResult(answers).dimensions).toEqual({
      segmentCol:        'Segment',
      assetClassCol:     undefined,
      bilanzpositionCol: 'BP',
    })
  })

  it('all dimensions undefined when none answered', () => {
    const answers: Answers = {
      asset_id: { t: 'column', id: 'ID' },
    }
    expect(toAnlagenResult(answers).dimensions).toEqual({
      segmentCol:        undefined,
      assetClassCol:     undefined,
      bilanzpositionCol: undefined,
    })
  })

  it('full fixture: combined mode payload matches commitAnlagen shape', () => {
    // Simulates a full combined-mode answer set
    const answers: Answers = {
      asset_id:            { t: 'column', id: 'Anlage'       },
      asset_label:         { t: 'column', id: 'Bezeichnung'  },
      asset_sub_no:        { t: 'column', id: null           },
      asset_class:         { t: 'column', id: null           },
      segment:             { t: 'column', id: null           },
      bilanzposition:      { t: 'column', id: null           },
      capitalization_date: { t: 'column', id: 'Datum'        },
      opening_cost_ahk:    { t: 'column', id: 'AHK Anfang'  },
      additions_zugang:    { t: 'column', id: 'Zugang'       },
      disposals_abgang:    { t: 'column', id: null           },
      transfers_umbuchung: { t: 'column', id: null           },
      depreciation:        { t: 'column', id: 'AfA'          },
      nbv:                 { t: 'column', id: 'Buchwert'     },
      entity_column:       { t: 'column', id: 'Buchungskreis'},
      segmentCol:          { t: 'column', id: null           },
      assetClassCol:       { t: 'column', id: null           },
      bilanzpositionCol:   { t: 'column', id: null           },
    }
    const result = toAnlagenResult(answers)
    // Matches column_map + entity_column sent to commitAnlagen
    expect(result.columnMap).toEqual({
      asset_id:            'Anlage',
      asset_label:         'Bezeichnung',
      capitalization_date: 'Datum',
      opening_cost_ahk:    'AHK Anfang',
      additions_zugang:    'Zugang',
      depreciation:        'AfA',
      nbv:                 'Buchwert',
    })
    expect(result.entityColumn).toBe('Buchungskreis')
    expect(result.dimensions).toEqual({
      segmentCol: undefined, assetClassCol: undefined, bilanzpositionCol: undefined,
    })
  })
})
