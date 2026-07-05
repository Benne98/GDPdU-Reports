/**
 * entitySync.test.ts — Unit tests for deriveProjectEntities and projectEntitiesEqual.
 *
 * Run: npx tsx src/pages/entitySync.test.ts
 *
 * Uses only node:assert (no test framework required).
 * Mirror style of wizardReducer.test.ts and coaSharedCommit.test.ts.
 *
 * ROOT CAUSE DOCUMENTED (pre-fix):
 *   Before the entity-sync fix, state.entities was initialized once from defaultState()
 *   and SET_ENTITIES was never dispatched when GL entity cards changed. As a result,
 *   state.entities stayed length 1 (the initial placeholder entity) regardless of how
 *   many GL entity cards the user filled in.
 *
 *   Test 1 would have exposed this gap: it asserts deriveProjectEntities returns length 5
 *   for 5 GL cards. The equivalent wizard-state check (state.entities.length === 5) would
 *   have FAILED pre-fix because SET_ENTITIES was never dispatched — the synced list never
 *   propagated to wizard state and therefore never reached buildCoaItems as wizardEntities.
 *
 * FIX:
 *   deriveProjectEntities() is called inside a useEffect. The result is compared against
 *   state.entities via projectEntitiesEqual() and SET_ENTITIES is dispatched only when the
 *   derived list actually changed. This is the loop-safety guard tested in Test 4.
 */

import assert from 'node:assert/strict'
import { deriveProjectEntities, projectEntitiesEqual } from './ProjectSetupWizard'
import { buildCoaItems } from '../components/ingest/coaAssignment'

// ---------------------------------------------------------------------------
// Shared synthetic fixture — N=5 GL entities
// ---------------------------------------------------------------------------

/** Minimal GL entity shape matching Pick<GlEntityState, 'entityCode' | 'entityLabel'>. */
type GlEntityMin = { entityCode: string; entityLabel?: string }

const GL_ENTITIES_5: GlEntityMin[] = [
  { entityCode: '01', entityLabel: 'Alpha GmbH' },
  { entityCode: '02', entityLabel: 'Beta AG' },
  { entityCode: '03', entityLabel: 'Gamma Ltd' },
  { entityCode: '04', entityLabel: 'Delta Inc' },
  { entityCode: '05', entityLabel: 'Epsilon KG' },
]

// ---------------------------------------------------------------------------
// Test 1 — REPRODUCE-FIRST / CORE
//
// Call deriveProjectEntities with N=5 distinct GL entity cards.
// Assert the result has exactly 5 entries, each with the correct shape,
// and that prefix === code for every entry.
//
// PRE-FIX CONTEXT: this test documents the state-sync gap.
//   Before the fix, state.entities stayed at length 1 (the initial placeholder)
//   because SET_ENTITIES was never dispatched. A test asserting that the wizard's
//   state.entities.length === 5 after card entry would have failed.
//   This test asserts the derive helper itself is correct — the useEffect that
//   dispatches SET_ENTITIES is the bridge that was previously missing.
// ---------------------------------------------------------------------------

{
  const result = deriveProjectEntities(GL_ENTITIES_5)

  assert.equal(
    result.length,
    5,
    'Core: deriveProjectEntities returns one entry per distinct GL entity card',
  )

  for (let i = 0; i < result.length; i++) {
    const e = result[i]
    const src = GL_ENTITIES_5[i]

    assert.ok(
      'code' in e && 'prefix' in e && 'name' in e,
      `Core: entry[${i}] has all three required fields {code, prefix, name}`,
    )
    assert.equal(
      e.code,
      src.entityCode,
      `Core: entry[${i}].code equals entityCode '${src.entityCode}'`,
    )
    assert.equal(
      e.prefix,
      e.code,
      `Core: entry[${i}].prefix === code (the join key invariant) for '${e.code}'`,
    )
    assert.equal(
      e.name,
      src.entityLabel,
      `Core: entry[${i}].name equals entityLabel '${src.entityLabel}'`,
    )
  }
}

// ---------------------------------------------------------------------------
// Test 2 — FILTERING
//
// GL entities with empty or whitespace-only entityCode are dropped.
// Mixed input (valid codes interleaved with blank codes) must produce only
// the valid entries, in order.
// ---------------------------------------------------------------------------

{
  const mixed: GlEntityMin[] = [
    { entityCode: '01', entityLabel: 'Entity A' },
    { entityCode: '',   entityLabel: 'Blank code — should be dropped' },
    { entityCode: '   ', entityLabel: 'Whitespace-only code — should be dropped' },
    { entityCode: '02', entityLabel: 'Entity B' },
    { entityCode: '\t', entityLabel: 'Tab-only code — should be dropped' },
  ]

  const result = deriveProjectEntities(mixed)

  assert.equal(
    result.length,
    2,
    'Filtering: only the 2 entries with non-empty trimmed entityCode survive',
  )
  assert.equal(result[0].code, '01', 'Filtering: first valid entry is code 01')
  assert.equal(result[1].code, '02', 'Filtering: second valid entry is code 02')

  // Paranoia: no dropped entry leaked through
  const codes = result.map(e => e.code)
  assert.ok(
    !codes.some(c => c.trim() === ''),
    'Filtering: no entry with an empty code present in output',
  )
}

// ---------------------------------------------------------------------------
// Test 3 — NAME FALLBACK
//
// When entityLabel is absent (undefined) or blank (empty/whitespace),
// name falls back to entityCode. When entityLabel is present, it is trimmed.
// ---------------------------------------------------------------------------

{
  const entities: GlEntityMin[] = [
    { entityCode: 'A1' },                              // no label → fallback to code
    { entityCode: 'A2', entityLabel: '' },             // empty string → fallback
    { entityCode: 'A3', entityLabel: '   ' },          // whitespace only → fallback
    { entityCode: 'A4', entityLabel: '  Trimmed  ' },  // label with surrounding spaces
    { entityCode: 'A5', entityLabel: 'Normal Label' }, // plain label
  ]

  const result = deriveProjectEntities(entities)

  assert.equal(result.length, 5, 'Fallback: all 5 entries survive (all codes non-empty)')

  assert.equal(result[0].name, 'A1', 'Fallback: undefined entityLabel → name equals entityCode')
  assert.equal(result[1].name, 'A2', 'Fallback: empty entityLabel → name equals entityCode')
  assert.equal(result[2].name, 'A3', 'Fallback: whitespace-only entityLabel → name equals entityCode')
  assert.equal(result[3].name, 'Trimmed', 'Fallback: entityLabel with surrounding spaces is trimmed')
  assert.equal(result[4].name, 'Normal Label', 'Fallback: normal entityLabel is used as-is')
}

// ---------------------------------------------------------------------------
// Test 4 — LOOP-SAFETY GUARD
//
// projectEntitiesEqual is the gate inside the useEffect that prevents
// infinite re-dispatch. It must return true for structurally identical arrays
// (same reference values, same order) and false for any diff (code, prefix,
// name, or length).
// ---------------------------------------------------------------------------

{
  const base = [
    { code: '01', prefix: '01', name: 'Alpha' },
    { code: '02', prefix: '02', name: 'Beta' },
  ]

  // Structural clone (different object references, same values)
  const same = [
    { code: '01', prefix: '01', name: 'Alpha' },
    { code: '02', prefix: '02', name: 'Beta' },
  ]

  assert.ok(
    projectEntitiesEqual(base, same),
    'Loop-safety: deep-equal arrays return true (effect must NOT re-dispatch)',
  )

  assert.ok(
    projectEntitiesEqual(base, base),
    'Loop-safety: same reference returns true',
  )

  // Different length
  assert.ok(
    !projectEntitiesEqual(base, [base[0]]),
    'Loop-safety: different length returns false',
  )
  assert.ok(
    !projectEntitiesEqual([base[0]], base),
    'Loop-safety: different length (a shorter) returns false',
  )

  // Different code at index 0
  const diffCode = [
    { code: 'XX', prefix: '01', name: 'Alpha' },
    { code: '02', prefix: '02', name: 'Beta' },
  ]
  assert.ok(
    !projectEntitiesEqual(base, diffCode),
    'Loop-safety: different code at index 0 returns false',
  )

  // Different prefix at index 1
  const diffPrefix = [
    { code: '01', prefix: '01', name: 'Alpha' },
    { code: '02', prefix: 'ZZ', name: 'Beta' },
  ]
  assert.ok(
    !projectEntitiesEqual(base, diffPrefix),
    'Loop-safety: different prefix at index 1 returns false',
  )

  // Different name at index 0
  const diffName = [
    { code: '01', prefix: '01', name: 'Changed' },
    { code: '02', prefix: '02', name: 'Beta' },
  ]
  assert.ok(
    !projectEntitiesEqual(base, diffName),
    'Loop-safety: different name at index 0 returns false',
  )

  // Empty arrays are equal
  assert.ok(
    projectEntitiesEqual([], []),
    'Loop-safety: two empty arrays are equal',
  )
}

// ---------------------------------------------------------------------------
// Test 5 — COA FAN-OUT WIRING (end-to-end at unit level)
//
// This test proves the full chain:
//   deriveProjectEntities (GL cards) → wizardEntities
//   wizardEntities + single shared CoA group → buildCoaItems
//   The emitted item's entityPrefixes must cover ALL N entity codes.
//
// PRE-FIX gap: state.entities stayed length 1 so wizardEntities only had 1 entry.
//   buildCoaItems would see memberEntityCodes=['01'..'05'] but could only resolve
//   the prefix for '01' (the single entry in wizardEntities). The other 4 codes
//   returned undefined from .find() and were filtered out. entityPrefixes was ['01']
//   instead of ['01','02','03','04','05'] — 4 of 5 entities had zero commit rows.
//
// POST-FIX: deriveProjectEntities supplies all 5 entries → buildCoaItems resolves
//   all 5 prefixes → the single fan-out commit item covers all entities.
// ---------------------------------------------------------------------------

{
  const SHARED_FILE_ID = 'file-shared-coa-test-001'

  // Step 1: derive wizard entities from the 5 GL entity cards.
  const wizardEntities = deriveProjectEntities(GL_ENTITIES_5)

  assert.equal(
    wizardEntities.length,
    5,
    'Fan-out: deriveProjectEntities produces 5 wizard entities (precondition)',
  )

  // Step 2: build a single shared CoA group whose memberEntityCodes are all 5 codes.
  // Shape matches CoaGroupMin from coaAssignment.ts.
  const sharedGroup = {
    id: 'coa-shared',
    label: 'Shared CoA (all entities)',
    memberEntityCodes: ['01', '02', '03', '04', '05'],
    method: 'upload' as const,
    bs: { fileId: SHARED_FILE_ID, isMaster: true },
    // pl intentionally omitted — only bs slot has a file
  }

  // Step 3: build commit items.
  const items = buildCoaItems([sharedGroup], wizardEntities)

  assert.equal(
    items.length,
    1,
    'Fan-out: one shared group with one bs file → exactly 1 commit item',
  )

  const item = items[0]

  assert.equal(
    item.stmt,
    'bs',
    'Fan-out: commit item is for the bs statement',
  )
  assert.equal(
    item.slot.fileId,
    SHARED_FILE_ID,
    'Fan-out: commit item carries the shared fileId',
  )

  // Step 4: assert all 5 prefixes are present.
  // prefix === code === entityCode for all entries (the join key invariant).
  const expectedPrefixes = ['01', '02', '03', '04', '05']
  const actualPrefixes = [...item.entityPrefixes].sort()

  assert.deepEqual(
    actualPrefixes,
    expectedPrefixes,
    'Fan-out: entityPrefixes contains all 5 entity codes (prefix === code), ' +
    'proving the synced list makes the multi-entity CoA commit fan out to every entity. ' +
    'PRE-FIX this would be [\'01\'] — 4 entities silently skipped.',
  )

  // Extra: every prefix in the item is also a code in wizardEntities (no phantoms)
  for (const prefix of item.entityPrefixes) {
    assert.ok(
      wizardEntities.some(we => we.prefix === prefix),
      `Fan-out: prefix '${prefix}' in entityPrefixes is traceable back to a wizard entity`,
    )
  }
}

// ---------------------------------------------------------------------------

console.log('entitySync.test.ts: all assertions passed')
