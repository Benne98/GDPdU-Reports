/**
 * Unit checks for coaAssignment.ts — pure helper.
 *
 * Run: npx tsx src/components/ingest/coaAssignment.test.ts
 *
 * Uses only node:assert (no test framework required).
 * Mirror style of glFormatGroups.test.ts.
 *
 * KEY INVARIANT: two entities with DIFFERENT GL format groups must still map
 * to the SAME CoA group key so a single uploaded CoA file covers all entities.
 * With the buggy (STEP 1) logic this assertion FAILS.
 *
 * REPRODUCE-FIRST tests (assertions a/b/c below) lock the multi-entity grouping
 * fix.  They would FAIL on old code where:
 *   • coaGroupsCoverAllEntities did not exist — no stale-state check.
 *   • buildInitialCoaAssignment keyed by formatGroupId — entities split into
 *     multiple CoA groups; uploading one shared CoA left other groups without
 *     a file, so entityPrefixes only contained the first group's member ('01').
 *   The frontend then sent entity_prefixes=['01'] and the backend committed a
 *   chart only for prefix '01', silently dropping the other 4 entities.
 */
import assert from 'node:assert/strict'
import {
  buildInitialCoaAssignment,
  buildCoaItems,
  coaGroupsCoverAllEntities,
} from './coaAssignment'

// ---------------------------------------------------------------------------
// 1. Two entities with DIFFERENT formatGroupIds → MUST share the SAME key
//    (regression guard — this is the root-cause assertion)
// ---------------------------------------------------------------------------

{
  const result = buildInitialCoaAssignment(
    ['01', '02'],
    { '01': 'fmt-a', '02': 'fmt-b' },
  )
  assert.equal(
    result['01'],
    result['02'],
    'entities with different GL format groups must default to the same CoA group key',
  )
}

// ---------------------------------------------------------------------------
// 2. Two entities with the SAME formatGroupId → share the same key (trivial)
// ---------------------------------------------------------------------------

{
  const result = buildInitialCoaAssignment(
    ['01', '02'],
    { '01': 'fmt-a', '02': 'fmt-a' },
  )
  assert.equal(result['01'], result['02'], 'same formatGroupId → same key')
}

// ---------------------------------------------------------------------------
// 3. Single entity → exactly one key, non-empty
// ---------------------------------------------------------------------------

{
  const result = buildInitialCoaAssignment(['01'], { '01': 'fmt-x' })
  assert.equal(Object.keys(result).length, 1, 'single entity → exactly one key')
  assert.ok(
    typeof result['01'] === 'string' && result['01'].length > 0,
    'single entity key is a non-empty string',
  )
}

// ---------------------------------------------------------------------------
// 4. Empty entity code → key stored under 'default'
// ---------------------------------------------------------------------------

{
  const result = buildInitialCoaAssignment([''], { '': undefined })
  assert.ok('default' in result, 'empty entity code → key stored under "default"')
  assert.ok(
    typeof result['default'] === 'string' && result['default'].length > 0,
    '"default" key is non-empty',
  )
}

// ===========================================================================
// REPRODUCE-FIRST: 5-entity stale-state scenario (multi-entity grouping fix)
// ---------------------------------------------------------------------------
// These three assertions (a/b/c) would FAIL on old code and PASS on the fix.
// Old behavior: buildInitialCoaAssignment keyed by formatGroupId → entities
//   split across multiple CoA groups → user uploads ONE shared CoA to one group
//   → other groups have no fileId → buildCoaItems emits entityPrefixes=['01']
//   → backend commits the chart only for prefix '01'.
// ===========================================================================

// Setup: 5 wizard entities; prefixes match codes for clarity.
const _WIZARD_ENTITIES = [
  { code: '01', prefix: '01', name: 'Entity A' },
  { code: '02', prefix: '02', name: 'Entity B' },
  { code: '03', prefix: '03', name: 'Entity C' },
  { code: '04', prefix: '04', name: 'Entity D' },
  { code: '05', prefix: '05', name: 'Entity E' },
]
const _ALL_CODES = _WIZARD_ENTITIES.map(e => e.code)
const _ALL_PREFIXES_SORTED = ['01', '02', '03', '04', '05']

// STALE state: coa.assigned=true but coa.groups only has '01' as member.
// This is the exact wizard state that triggered the defect before the fix.
const _STALE_GROUPS = [
  {
    id: 'group-A',
    label: 'Group A',
    memberEntityCodes: ['01'],      // only Atlas — the pre-fix stale coverage
    method: 'upload' as const,
    bs: { fileId: 'file-stale-123', isMaster: false, mapping: {} },
    pl: { fileId: 'file-stale-123', isMaster: false, mapping: {} },
  },
]

// ---------------------------------------------------------------------------
// (a) Coverage gate: stale groups MUST NOT satisfy 5-entity coverage
//     WHY FAILS OLD: coaGroupsCoverAllEntities did not exist — no gate existed.
//       The wizard accepted coa.assigned=true even with only '01' covered.
//     WHY PASSES NOW: the function correctly returns false → the wizard re-opens
//       the assignment step so the user can confirm all 5 entities.
// ---------------------------------------------------------------------------
{
  const covered = coaGroupsCoverAllEntities(_STALE_GROUPS, _ALL_CODES)
  assert.equal(
    covered,
    false,
    '(a) stale groups cover only "01" — coaGroupsCoverAllEntities must return false for 5-entity project',
  )

  // Sanity: full coverage returns true
  const fullGroup = [{ id: 'g', label: 'G', memberEntityCodes: _ALL_CODES }]
  assert.equal(
    coaGroupsCoverAllEntities(fullGroup, _ALL_CODES),
    true,
    '(a) sanity: a group covering all 5 codes → true',
  )

  // Edge: empty entityCodes → always true (no entities to cover)
  assert.equal(
    coaGroupsCoverAllEntities([], []),
    true,
    '(a) edge: empty entityCodes → true',
  )
}

// ---------------------------------------------------------------------------
// (b) Initial assignment for 5 entities → ALL 5 share ONE group key
//     WHY FAILS OLD: old buildInitialCoaAssignment keyed by formatGroupId →
//       '01' maps to 'fmt-a', '02'..'05' map to 'fmt-b' → two CoA groups →
//       one group has the file, the other does not → buildCoaItems only emits
//       entityPrefixes=['01'] (the group with the file).
//     WHY PASSES NOW: every entity always maps to 'coa-shared' regardless of
//       formatGroupId, so one group captures all 5.
// ---------------------------------------------------------------------------
{
  // Simulate the mixed formatGroupId scenario that previously caused the split
  const formatGroupIdByEntity: Record<string, string | undefined> = {
    '01': 'fmt-a',  // entity A has a different GL column layout
    '02': 'fmt-b',
    '03': 'fmt-b',
    '04': 'fmt-b',
    '05': 'fmt-b',
  }

  const assignment = buildInitialCoaAssignment(_ALL_CODES, formatGroupIdByEntity)

  // All 5 entity codes must appear
  for (const code of _ALL_CODES) {
    assert.ok(
      code in assignment,
      `(b) entity '${code}' must appear in the initial assignment`,
    )
  }

  // All must share exactly ONE key — no split across multiple CoA groups
  const uniqueKeys = new Set(Object.values(assignment))
  assert.equal(
    uniqueKeys.size,
    1,
    `(b) all 5 entities must map to the SAME CoA group key; got keys: ${[...uniqueKeys].join(', ')}`,
  )
  assert.equal(
    [...uniqueKeys][0],
    'coa-shared',
    '(b) the shared key must be "coa-shared"',
  )
}

// ---------------------------------------------------------------------------
// (c) buildCoaItems with all 5 entities under one upload group →
//     each CoaItem's entityPrefixes must contain ALL 5 prefixes
//     WHY FAILS OLD: if entities were split, the upload-bearing group only had
//       memberEntityCodes=['01'] → entityPrefixes=['01'] → backend fan-out for
//       prefix '01' only → dim_gl_account populated for '01' only.
//     WHY PASSES NOW: one group holds all 5 memberEntityCodes → entityPrefixes
//       carries ['01','02','03','04','05'] → backend commits the full chart for
//       all 5 prefixes in a single atomic transaction.
// ---------------------------------------------------------------------------
{
  const confirmedGroups = [
    {
      id: 'coa-shared',
      label: 'Shared CoA',
      memberEntityCodes: _ALL_CODES,   // all 5 entities in one group (the fix)
      method: 'upload' as const,
      bs: { fileId: 'file-shared-xyz', isMaster: false, mapping: {} },
      pl: { fileId: 'file-shared-xyz', isMaster: false, mapping: {} },
    },
  ]

  const items = buildCoaItems(confirmedGroups, _WIZARD_ENTITIES)

  // One item per statement (bs + pl) = 2 items total
  assert.equal(
    items.length,
    2,
    '(c) should produce one CoaItem per statement (bs + pl) = 2 items',
  )

  const stmts = items.map(i => i.stmt).sort()
  assert.deepEqual(stmts, ['bs', 'pl'], '(c) items must cover both statements')

  // Each item's entityPrefixes must carry ALL 5 prefixes — not just ['01']
  for (const item of items) {
    const prefixes = item.entityPrefixes.slice().sort()
    assert.deepEqual(
      prefixes,
      _ALL_PREFIXES_SORTED,
      `(c) ${item.stmt} CoaItem must carry entityPrefixes ${_ALL_PREFIXES_SORTED}; got ${prefixes}`,
    )
  }
}

// ---------------------------------------------------------------------------

console.log('coaAssignment.test.ts: all assertions passed')
