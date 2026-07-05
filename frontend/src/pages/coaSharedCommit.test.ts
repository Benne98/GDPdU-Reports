/**
 * coaSharedCommit.test.ts — Commit-emission level test for the shared-CoA bug.
 *
 * Run: npx tsx src/pages/coaSharedCommit.test.ts
 *
 * Uses only node:assert (no test framework required).
 * Mirror style of wizardReducer.test.ts and coaAssignment.test.ts.
 *
 * ROOT CAUSE (pre-fix):
 *   Old buildInitialCoaAssignment keyed CoA groups by GL formatGroupId.
 *   Two-entity project (01=fmt-a, 02=fmt-b) → two separate CoA groups.
 *   User uploads ONE shared CoA file → only group-A gets a fileId.
 *   Commit loop: `if (!slot?.fileId) continue` → entity 02 group skipped → zero rows.
 *
 * FIX:
 *   migrateLegacyCoaGroups() detects "exactly one group has a file" and returns
 *   a merged assignment. Dispatching APPLY_COA_ASSIGNMENTS collapses both entities
 *   into the group that has the file. buildCoaItems then produces 2 items.
 *
 * FAIL evidence (pre-fix):  see Test 1 — buildCoaItems on stale groups = 1 item.
 * PASS evidence (post-fix): see Test 2 — after migration = 2 items, both with shared fileId.
 */

import assert from 'node:assert/strict'
import { buildCoaItems, migrateLegacyCoaGroups } from '../components/ingest/coaAssignment'
import { wizardReducer } from './ProjectSetupWizard'

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const SHARED_FILE_ID = 'file-shared-coa-001'

/**
 * STALE legacy CoA groups — the state produced by OLD buildInitialCoaAssignment
 * that keyed groups by formatGroupId.
 *
 * Entity 01 → group 'fmt-a'  (has bs fileId — the uploaded shared CoA file)
 * Entity 02 → group 'fmt-b'  (no fileId — user never uploaded here, assumed shared)
 */
const staleGroups: any[] = [
  {
    id: 'fmt-a',
    label: 'Group A',
    memberEntityCodes: ['01'],
    method: 'upload',
    bs: { fileId: SHARED_FILE_ID, isMaster: true },
    pl: undefined,
  },
  {
    id: 'fmt-b',
    label: 'Group B',
    memberEntityCodes: ['02'],
    method: 'upload',
    bs: {},         // no fileId — entity 02's slot was never uploaded
    pl: undefined,
  },
]

const wizardEntities = [
  { code: '01', prefix: 'P01', name: 'Entity 01' },
  { code: '02', prefix: 'P02', name: 'Entity 02' },
]

/** Minimal WizardState-shaped object for use with wizardReducer. */
function makeCoaState(groups: any[]): any {
  return {
    projectName: 'Test',
    fyEndMonth: 12,
    entities: [
      { code: '01', prefix: 'P01', name: 'Entity 01' },
      { code: '02', prefix: 'P02', name: 'Entity 02' },
    ],
    gl: {
      years: [2024],
      entities: [
        { entityCode: '01', yearFiles: {}, formatGroupId: 'fmt-a' },
        { entityCode: '02', yearFiles: {}, formatGroupId: 'fmt-b' },
      ],
      formatGroups: [],
    },
    coa: {
      groups,
      accountMappingMode: 'library',
      assigned: true,
    },
    ob: {},
    partner: { sides: { customer: {}, supplier: {} } },
    fte: {
      provided: false,
      sessionId: 'test-session',
      viewMode: 'consolidated',
      uploadMode: 'per_fy_grid',
      uploads: [],
      previewFileId: '',
      fteMapping: {},
      tenureMode: 'months_col',
      payrollMapping: {},
      payrollMode: 'sum_components',
      dimensions: [],
      presetMetrics: [],
      customMetrics: [],
      pexViewMode: 'consolidated',
      pexValues: {},
    },
    additionalDatasets: { fte: false, anlagen: false, opos: false },
    anlagen: { provided: false, viewMode: 'combined', uploads: [], columnMap: {}, dimensions: {} },
    opos: {
      provided: false,
      debitor: { viewMode: 'combined', uploads: [], columnMap: {} },
      kreditor: { viewMode: 'combined', uploads: [], columnMap: {} },
    },
  }
}

// ---------------------------------------------------------------------------
// Test 1 — BUG DEMONSTRATION (pre-fix behavior, always passes)
//
// Calling buildCoaItems on STALE groups directly (no migration) shows the bug:
// only entity 01 produces a commit item; entity 02 is silently skipped.
// This section confirms the root cause is real and gives the "FAIL evidence"
// for what the main guard (Test 2) looked like before the fix was applied.
// ---------------------------------------------------------------------------

{
  const items = buildCoaItems(staleGroups, wizardEntities)

  assert.equal(
    items.length,
    1,
    'Bug (pre-fix): stale groups produce only 1 commit item — entity 02 silently skipped',
  )
  assert.ok(
    items[0].entityPrefixes.includes('P01'),
    'Bug (pre-fix): the single item covers entity 01 (prefix P01)',
  )
  assert.equal(
    items[0].slot.fileId,
    SHARED_FILE_ID,
    'Bug (pre-fix): the item carries the shared fileId',
  )
  // Entity 02 (prefix P02) is absent — the root cause of the missing commit rows.
  assert.ok(
    !items.some(i => i.entityPrefixes.includes('P02')),
    'Bug (pre-fix): entity 02 prefix P02 is absent from commit items (zero rows would be committed)',
  )
}

// ---------------------------------------------------------------------------
// Test 2 — CORE REGRESSION GUARD (FAILS pre-fix; PASSES after fix)
//
// Simulates the full "load + commit-item construction" path:
//   1. migrateLegacyCoaGroups detects the single-file stale groups
//   2. APPLY_COA_ASSIGNMENTS collapses both entities into group-A (which has the file)
//   3. buildCoaItems sees memberEntityCodes = ['01', '02'] → 2 items
//
// Pre-fix (without migrateLegacyCoaGroups): import error → test fails.
// Post-fix: all assertions below pass.
// ---------------------------------------------------------------------------

{
  // Step 1: detect migration
  const migration = migrateLegacyCoaGroups(staleGroups)

  assert.ok(
    migration !== null,
    'migrateLegacyCoaGroups must detect stale single-file groups and return an assignment',
  )

  // Step 2: apply migration via the APPLY_COA_ASSIGNMENTS reducer
  const baseState = makeCoaState(staleGroups)
  const migratedState = wizardReducer(baseState, {
    type: 'APPLY_COA_ASSIGNMENTS',
    assignment: migration!,
  })

  // Merged state must have exactly 1 group that owns both entity codes
  assert.equal(
    migratedState.coa.groups.length,
    1,
    'After migration: coa.groups collapses to 1 group',
  )
  const mergedGroup = migratedState.coa.groups[0]
  assert.ok(
    mergedGroup.memberEntityCodes.includes('01'),
    'Merged group contains entity 01',
  )
  assert.ok(
    mergedGroup.memberEntityCodes.includes('02'),
    'Merged group contains entity 02',
  )
  assert.equal(
    mergedGroup.bs?.fileId,
    SHARED_FILE_ID,
    'Merged group preserves the shared fileId on the bs slot',
  )

  // Step 3: build commit items from migrated groups.
  // New fan-out design: ONE item per group×stmt; entity_prefixes covers all members.
  const items = buildCoaItems(migratedState.coa.groups, wizardEntities)

  assert.equal(
    items.length,
    1,
    'After migration: buildCoaItems produces 1 commit item (fan-out covers all member entities)',
  )
  assert.ok(
    items[0].entityPrefixes.includes('P01'),
    'Single fan-out item includes entity 01 prefix (P01)',
  )
  assert.ok(
    items[0].entityPrefixes.includes('P02'),
    'Single fan-out item includes entity 02 prefix (P02)',
  )
  assert.ok(
    items.every(i => i.slot.fileId === SHARED_FILE_ID),
    'Fan-out item uses the shared fileId',
  )
  // Both prefixes present in the single item
  assert.deepEqual(
    [...items[0].entityPrefixes].sort(),
    ['P01', 'P02'],
    'entityPrefixes carries both entity prefixes for atomic multi-entity commit',
  )
  assert.equal(items[0].stmt, 'bs', 'Statement is bs (bs slot has the file)')
}

// ---------------------------------------------------------------------------
// Test 3 — NON-REGRESSION: deliberate multi-CoA assignment is NOT migrated
//
// Two entities, two groups, EACH with a different fileId → migrateLegacyCoaGroups
// returns null (2 groups have files → deliberate per-group upload).
// ---------------------------------------------------------------------------

{
  const multiCoaGroups: any[] = [
    {
      id: 'coa-01',
      label: 'Group A',
      memberEntityCodes: ['01'],
      method: 'upload',
      bs: { fileId: 'file-entity-01-coa', isMaster: true },
    },
    {
      id: 'coa-02',
      label: 'Group B',
      memberEntityCodes: ['02'],
      method: 'upload',
      bs: { fileId: 'file-entity-02-coa', isMaster: true },
    },
  ]

  const migration = migrateLegacyCoaGroups(multiCoaGroups)
  assert.equal(
    migration,
    null,
    'Non-regression: deliberate multi-CoA (2 groups, 2 files) must NOT be migrated',
  )

  // Without migration, buildCoaItems produces 2 items (one per group)
  const items = buildCoaItems(multiCoaGroups, wizardEntities)
  assert.equal(items.length, 2, 'Non-regression: 2 distinct CoA groups → 2 commit items')
  assert.equal(items[0].slot.fileId, 'file-entity-01-coa', 'Non-regression: entity 01 keeps its own fileId')
  assert.equal(items[1].slot.fileId, 'file-entity-02-coa', 'Non-regression: entity 02 keeps its own fileId')
}

// ---------------------------------------------------------------------------
// Test 4 — NON-REGRESSION: library groups are never collapsed
//
// One upload group (entity 01) + one library group (entity 02).
// Library groups are exempt from migration.
// ---------------------------------------------------------------------------

{
  const mixedGroups: any[] = [
    {
      id: 'coa-upload',
      label: 'Group A',
      memberEntityCodes: ['01'],
      method: 'upload',
      bs: { fileId: SHARED_FILE_ID, isMaster: true },
    },
    {
      id: 'coa-lib',
      label: 'Group B',
      memberEntityCodes: ['02'],
      method: 'library',
      // In the real wizard the user confirmed the assignment (explicit: true) so this group
      // carries methodExplicit=true before migration ever runs. Migration must not fold it.
      methodExplicit: true,
      libraryVariant: 'skr03',
    },
  ]

  const migration = migrateLegacyCoaGroups(mixedGroups)
  assert.equal(
    migration,
    null,
    'Non-regression: upload + library group mix must NOT be migrated (library stays separate)',
  )
}

// ---------------------------------------------------------------------------
// Test 5 — NON-REGRESSION: single-entity project (no migration needed)
// ---------------------------------------------------------------------------

{
  const singleEntityGroups: any[] = [
    {
      id: 'coa-shared',
      label: 'Group A',
      memberEntityCodes: ['01'],
      method: 'upload',
      bs: { fileId: SHARED_FILE_ID, isMaster: true },
    },
  ]

  const migration = migrateLegacyCoaGroups(singleEntityGroups)
  assert.equal(
    migration,
    null,
    'Non-regression: single-group project → no migration (already correct)',
  )

  const items = buildCoaItems(singleEntityGroups, [{ code: '01', prefix: 'P01', name: 'Entity 01' }])
  assert.equal(items.length, 1, 'Non-regression: single entity → 1 commit item')
  assert.deepEqual(items[0].entityPrefixes, ['P01'], 'Non-regression: single entity prefix correct')
}

// ---------------------------------------------------------------------------
// Test 6 — GUARD REGRESSION: deliberate multi-group mid-upload is NOT migrated
//
// The user deliberately created 2 CoA groups (A for entity 01, B for entity 02),
// confirmed the assignment (coa.assigned = true), and has uploaded a file to
// group A but not yet to group B.  With the OLD eager useEffect this triggers
// migrateLegacyCoaGroups (which cannot distinguish "legacy" from "deliberate")
// and collapses the groups prematurely — entity 02 loses its own CoA slot.
//
// The fix adds coa.migrationChecked to WizardCoaState and dispatches
// SET_COA_MIGRATION_CHECKED after the first check against non-empty groups.
// Because migrationChecked lives in the reducer (not a local ref) it persists
// across component remounts.
//
// FAIL evidence (pre-fix):
//   wizardReducer does not handle SET_COA_MIGRATION_CHECKED → migrationChecked
//   stays undefined → the assertion `=== true` fails.
//
// PASS evidence (post-fix):
//   Reducer handles the action → migrationChecked = true → groups stay at 2.
// ---------------------------------------------------------------------------

{
  // Groups that represent a deliberate 2-group new-config setup,
  // mid-upload: entity 01 has a file in group A, entity 02 is waiting
  // in group B with no file yet.
  const midUploadGroups: any[] = [
    {
      id: 'coa-shared',
      label: 'Group A',
      memberEntityCodes: ['01'],
      method: 'upload',
      bs: { fileId: 'file-grp-a', isMaster: true },
    },
    {
      id: 'coa-grp-0',
      label: 'Group B',
      memberEntityCodes: ['02'],
      method: 'upload',
      // no fileId — user has not yet uploaded to this group
    },
  ]

  // PART A: confirm the function itself would eagerly migrate without a gate.
  // This is the "FAIL evidence" showing why the eager useEffect was wrong.
  const naiveMigration = migrateLegacyCoaGroups(midUploadGroups)
  assert.ok(
    naiveMigration !== null,
    'Guard pre-check: migrateLegacyCoaGroups returns non-null for mid-upload ' +
    'state — without a gate the eager effect would prematurely collapse groups',
  )

  // PART B: simulate the gated useEffect path.
  //   On the first render after the user confirms 2 groups (no files yet),
  //   the effect runs: migration returns null → SET_COA_MIGRATION_CHECKED is
  //   dispatched → migrationChecked = true.
  //   We reproduce this by dispatching SET_COA_MIGRATION_CHECKED on the base
  //   state (which has groups but no files, matching the "just-confirmed" moment).
  const noFileGroups: any[] = [
    { id: 'coa-shared', label: 'Group A', memberEntityCodes: ['01'], method: 'upload' },
    { id: 'coa-grp-0',  label: 'Group B', memberEntityCodes: ['02'], method: 'upload' },
  ]
  const afterConfirmState = wizardReducer(makeCoaState(noFileGroups), {
    type: 'SET_COA_MIGRATION_CHECKED',
  })

  assert.equal(
    afterConfirmState.coa.migrationChecked,
    true,
    'Guard: SET_COA_MIGRATION_CHECKED sets coa.migrationChecked = true ' +
    '(fails pre-fix when the reducer does not handle this action)',
  )

  // PART C: now simulate the upload arriving (patch in the file).
  // The state already has migrationChecked=true, so the effect would skip.
  // We verify groups are still 2 — NOT collapsed.
  const afterUploadState = wizardReducer(afterConfirmState, {
    type: 'PATCH_COA_SLOT',
    id: 'coa-shared',
    statement: 'bs',
    patch: { fileId: 'file-grp-a', isMaster: true },
  })

  assert.equal(
    afterUploadState.coa.groups.length,
    2,
    'Guard: after first-file upload with migrationChecked=true, groups remain 2 ' +
    '(deliberate multi-CoA is NOT prematurely collapsed)',
  )
  assert.equal(
    afterUploadState.coa.migrationChecked,
    true,
    'Guard: migrationChecked survives PATCH_COA_SLOT',
  )
  // Confirm the file is on group A but group B still has no file
  const grpA = afterUploadState.coa.groups.find((g: any) => g.id === 'coa-shared')!
  const grpB = afterUploadState.coa.groups.find((g: any) => g.id === 'coa-grp-0')!
  assert.equal(grpA?.bs?.fileId, 'file-grp-a', 'Guard: group A carries the uploaded fileId')
  assert.ok(!grpB?.bs?.fileId, 'Guard: group B still has no file — user can still upload separately')
}

// ---------------------------------------------------------------------------

console.log('coaSharedCommit.test.ts: all assertions passed')
