/**
 * Payload-parity guard for anlagenSteps.ts.
 *
 * Proves that toAnlagenResult reproduces the exact columnMap / entityColumn
 * payloads that commitAnlagen and WizardAnlagenState consume, over the
 * importance-ordered field set (6 essential + entity + 3 optional).
 */

import { describe, it, expect } from 'vitest'
import { buildAnlagenSteps, toAnlagenResult } from './anlagenSteps'
import type { Answers } from './stepMapperTypes'

const ESSENTIAL_ROLES = [
  'asset_id', 'opening_nbv', 'additions_zugang',
  'disposals_abgang', 'depreciation', 'nbv',
]
const OPTIONAL_ROLES = ['asset_label', 'segment', 'bilanzposition']

// ── buildAnlagenSteps ─────────────────────────────────────────────────────────

describe('buildAnlagenSteps', () => {
  it('per_entity: 9 steps (6 essential + 3 optional, no entity step)', () => {
    expect(buildAnlagenSteps('per_entity')).toHaveLength(9)
  })

  it('combined: 10 steps (6 essential + entity + 3 optional)', () => {
    expect(buildAnlagenSteps('combined')).toHaveLength(10)
  })

  it('essential fields come first in importance order', () => {
    const roles = buildAnlagenSteps('per_entity').map(s => s.role)
    expect(roles.slice(0, 6)).toEqual(ESSENTIAL_ROLES)
  })

  it('optional fields come last in both modes', () => {
    for (const mode of ['combined', 'per_entity']) {
      const roles = buildAnlagenSteps(mode).map(s => s.role)
      expect(roles.slice(-3)).toEqual(OPTIONAL_ROLES)
    }
  })

  it('entity_column step is present only in combined mode, between essential and optional', () => {
    const combined = buildAnlagenSteps('combined')
    const perEntity = buildAnlagenSteps('per_entity')
    expect(perEntity.some(s => s.role === 'entity_column')).toBe(false)
    const roles = combined.map(s => s.role)
    expect(roles[6]).toBe('entity_column')
  })

  it('dropped dead fields are absent (no asset_class / sub_no / transfers / cap_date / opening_cost / dimension cols)', () => {
    const roles = buildAnlagenSteps('combined').map(s => s.role)
    for (const dead of [
      'asset_class', 'asset_sub_no', 'transfers_umbuchung', 'capitalization_date',
      'opening_cost_ahk', 'segmentCol', 'assetClassCol', 'bilanzpositionCol',
    ]) {
      expect(roles).not.toContain(dead)
    }
  })

  it('asset_id is required; all other column steps are skippable', () => {
    const steps = buildAnlagenSteps('combined')
    for (const s of steps) {
      if (s.kind !== 'column') continue
      if (s.role === 'asset_id' || s.role === 'entity_column') {
        expect(s.required).toBe(true)
        expect(s.skippable).toBe(false)
      } else {
        expect(s.required).toBeFalsy()
        expect(s.skippable).toBe(true)
      }
    }
  })

  it('essential fields carry generic labels + hints', () => {
    const steps = buildAnlagenSteps('per_entity')
    const col = (role: string) => {
      const s = steps.find(x => x.role === role)
      if (!s || s.kind !== 'column') throw new Error(`missing column step: ${role}`)
      return s
    }
    expect(col('asset_id').label).toBe('Asset number')
    expect(col('asset_id').hint).toBe('Unique identifier of each fixed asset.')
    expect(col('opening_nbv').label).toBe('Opening net book value')
    expect(col('nbv').label).toBe('Closing net book value')
    expect(col('additions_zugang').label).toBe('Additions / CapEx')
  })
})

// ── toAnlagenResult ───────────────────────────────────────────────────────────

describe('toAnlagenResult', () => {
  it('produces exact columnMap payload (skipped nulls excluded, incl. opening_nbv)', () => {
    const answers: Answers = {
      asset_id:         { t: 'column', id: 'Anlage-Nr'   },
      opening_nbv:      { t: 'column', id: 'BW Anfang'   },
      additions_zugang: { t: 'column', id: null          },   // skipped
      disposals_abgang: { t: 'column', id: null          },
      depreciation:     { t: 'column', id: 'AfA'         },
      nbv:              { t: 'column', id: 'Buchwert'    },
      asset_label:      { t: 'column', id: 'Bezeichnung' },
      segment:          { t: 'column', id: null          },
      bilanzposition:   { t: 'column', id: null          },
    }
    const result = toAnlagenResult(answers)
    expect(result.columnMap).toEqual({
      asset_id:     'Anlage-Nr',
      opening_nbv:  'BW Anfang',
      depreciation: 'AfA',
      nbv:          'Buchwert',
      asset_label:  'Bezeichnung',
    })
  })

  it('excludes dropped/dead fields even if present in answers', () => {
    const answers: Answers = {
      asset_id:            { t: 'column', id: 'ID'      },
      opening_cost_ahk:    { t: 'column', id: 'AHK'     },   // dropped
      asset_class:         { t: 'column', id: 'Klasse'  },   // dropped
      transfers_umbuchung: { t: 'column', id: 'Umb'     },   // dropped
      segmentCol:          { t: 'column', id: 'Seg'     },   // dead
    }
    expect(toAnlagenResult(answers).columnMap).toEqual({ asset_id: 'ID' })
  })

  it('result has no dimensions key', () => {
    const answers: Answers = { asset_id: { t: 'column', id: 'ID' } }
    expect('dimensions' in toAnlagenResult(answers)).toBe(false)
  })

  it('produces exact entityColumn from entity_column role', () => {
    const answers: Answers = {
      asset_id:      { t: 'column', id: 'ID'           },
      entity_column: { t: 'column', id: 'Company Code' },
    }
    expect(toAnlagenResult(answers).entityColumn).toBe('Company Code')
  })

  it('entityColumn is undefined when entity_column step not answered', () => {
    const answers: Answers = { asset_id: { t: 'column', id: 'ID' } }
    expect(toAnlagenResult(answers).entityColumn).toBeUndefined()
  })

  it('entityColumn is undefined when entity_column step is skipped (id=null)', () => {
    const answers: Answers = {
      asset_id:      { t: 'column', id: 'ID'   },
      entity_column: { t: 'column', id: null   },
    }
    expect(toAnlagenResult(answers).entityColumn).toBeUndefined()
  })

  it('full fixture: combined mode payload matches commitAnlagen shape', () => {
    const answers: Answers = {
      asset_id:         { t: 'column', id: 'Anlage'       },
      opening_nbv:      { t: 'column', id: 'BW Anfang'    },
      additions_zugang: { t: 'column', id: 'Zugang'       },
      disposals_abgang: { t: 'column', id: null           },
      depreciation:     { t: 'column', id: 'AfA'          },
      nbv:              { t: 'column', id: 'Buchwert'     },
      entity_column:    { t: 'column', id: 'Buchungskreis'},
      asset_label:      { t: 'column', id: 'Bezeichnung'  },
      segment:          { t: 'column', id: null           },
      bilanzposition:   { t: 'column', id: null           },
    }
    const result = toAnlagenResult(answers)
    expect(result.columnMap).toEqual({
      asset_id:         'Anlage',
      opening_nbv:      'BW Anfang',
      additions_zugang: 'Zugang',
      depreciation:     'AfA',
      nbv:              'Buchwert',
      asset_label:      'Bezeichnung',
    })
    expect(result.entityColumn).toBe('Buchungskreis')
  })
})
