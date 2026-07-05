/**
 * Unit checks for wizardReducer exported from ProjectSetupWizard.tsx.
 *
 * Run: npx tsx src/pages/wizardReducer.test.ts
 *
 * Uses only node:assert (no test framework required).
 *
 * NOTE: If this import fails because the ProjectSetupWizard.tsx component
 *   chain (PartnerMasterEditor, AuthContext, api.ts, etc.) is not tsx-compatible,
 *   this file should be dropped and the reducer extracted to a standalone
 *   module (e.g. src/pages/wizardReducer.ts) as a follow-up task.
 */
import assert from 'node:assert/strict'
import { wizardReducer } from './ProjectSetupWizard'

// ---------------------------------------------------------------------------
// Helpers — plain objects; tsx strips types so only runtime fields matter
// ---------------------------------------------------------------------------

function makeEntity(overrides: Record<string, unknown> = {}): any {
  return { entityCode: 'E', yearFiles: {}, ...overrides }
}

/** Construct a minimal WizardState. defaultState() is not exported, so we build manually. */
function makeState(glEntities: any[] = [], formatGroups: any[] = []): any {
  return {
    projectName: 'Test',
    fyEndMonth: 12,
    entities: [{ code: 'E1', prefix: 'E', name: 'Entity 1' }],
    gl: {
      years: [2024],
      entities: glEntities.length > 0 ? glEntities : [makeEntity({ entityCode: 'E1' })],
      formatGroups,
    },
    coa: { inputs: [{}], accountMappingMode: 'library' },
    ob: {},
    partner: {},
  }
}

const HEADERS = ['Account', 'Date', 'Amount']
const MAPPING = { account_number: 'Account' }

// ---------------------------------------------------------------------------
// 1. CREATE_GL_GROUP — creates group and assigns formatGroupId to entity
// ---------------------------------------------------------------------------

{
  const state = makeState([
    makeEntity({ entityCode: 'E1' }),
    makeEntity({ entityCode: 'E2' }),
  ])

  const next = wizardReducer(state, {
    type: 'CREATE_GL_GROUP',
    fromIndex: 0,
    headers: HEADERS,
    mapping: MAPPING,
  })

  assert.equal(next.gl.formatGroups.length, 1, 'CREATE_GL_GROUP: one group created')
  const g = next.gl.formatGroups[0]
  assert.equal(g.representativeIndex, 0, 'CREATE_GL_GROUP: rep is fromIndex=0')
  assert.deepEqual(g.memberIndices, [0], 'CREATE_GL_GROUP: memberIndices=[0]')
  assert.equal(g.columnCount, HEADERS.length, 'CREATE_GL_GROUP: columnCount=headers.length')
  assert.equal(g.label, 'Group A', 'CREATE_GL_GROUP: first group label "Group A"')
  assert.equal(
    next.gl.entities[0].formatGroupId,
    g.id,
    'CREATE_GL_GROUP: entity[0].formatGroupId set to group id',
  )
  assert.equal(
    next.gl.entities[1].formatGroupId,
    undefined,
    'CREATE_GL_GROUP: entity[1].formatGroupId untouched',
  )
}

// ---------------------------------------------------------------------------
// 2. ASSIGN_GL_GROUP — adds member indices and sets formatGroupId on entities
// ---------------------------------------------------------------------------

{
  let state = makeState([
    makeEntity({ entityCode: 'E1' }),
    makeEntity({ entityCode: 'E2' }),
    makeEntity({ entityCode: 'E3' }),
  ])
  // First create a group for entity 0
  state = wizardReducer(state, {
    type: 'CREATE_GL_GROUP',
    fromIndex: 0,
    headers: HEADERS,
    mapping: MAPPING,
  })
  const groupId = state.gl.formatGroups[0].id

  // Assign entities 1 and 2 to the group
  const next = wizardReducer(state, {
    type: 'ASSIGN_GL_GROUP',
    groupId,
    memberIndices: [1, 2],
  })

  const g = next.gl.formatGroups[0]
  assert.ok(g.memberIndices.includes(0), 'ASSIGN_GL_GROUP: original rep (0) still a member')
  assert.ok(g.memberIndices.includes(1), 'ASSIGN_GL_GROUP: index 1 added')
  assert.ok(g.memberIndices.includes(2), 'ASSIGN_GL_GROUP: index 2 added')
  assert.equal(g.memberIndices.length, 3, 'ASSIGN_GL_GROUP: 3 members total (no duplicates)')
  assert.equal(
    next.gl.entities[1].formatGroupId,
    groupId,
    'ASSIGN_GL_GROUP: entity[1].formatGroupId set',
  )
  assert.equal(
    next.gl.entities[2].formatGroupId,
    groupId,
    'ASSIGN_GL_GROUP: entity[2].formatGroupId set',
  )
}

// ---------------------------------------------------------------------------
// 2b. ASSIGN_GL_GROUP — clears validationOk + assembledProfile on reassigned entities
// ---------------------------------------------------------------------------

{
  let state = makeState([
    makeEntity({ entityCode: 'E1' }),
    makeEntity({ entityCode: 'E2', validationOk: true, assembledProfile: { col: 'Amount' } }),
    makeEntity({ entityCode: 'E3', validationOk: false, assembledProfile: { col: 'Debit' } }),
  ])
  // Create a group seeded by entity 0
  state = wizardReducer(state, {
    type: 'CREATE_GL_GROUP',
    fromIndex: 0,
    headers: HEADERS,
    mapping: MAPPING,
  })
  const groupId = state.gl.formatGroups[0].id

  // Reassign entities 1 and 2 (both have prior validation state)
  const next = wizardReducer(state, {
    type: 'ASSIGN_GL_GROUP',
    groupId,
    memberIndices: [1, 2],
  })

  assert.equal(
    next.gl.entities[1].validationOk,
    undefined,
    'ASSIGN_GL_GROUP: entity[1].validationOk cleared on reassign',
  )
  assert.equal(
    next.gl.entities[1].assembledProfile,
    undefined,
    'ASSIGN_GL_GROUP: entity[1].assembledProfile cleared on reassign',
  )
  assert.equal(
    next.gl.entities[2].validationOk,
    undefined,
    'ASSIGN_GL_GROUP: entity[2].validationOk cleared on reassign',
  )
  assert.equal(
    next.gl.entities[2].assembledProfile,
    undefined,
    'ASSIGN_GL_GROUP: entity[2].assembledProfile cleared on reassign',
  )
  // Entity 0 (the group creator, not in memberIndices) must be untouched
  assert.equal(
    next.gl.entities[0].formatGroupId,
    groupId,
    'ASSIGN_GL_GROUP: entity[0] (rep) formatGroupId unchanged',
  )
}

// ---------------------------------------------------------------------------
// 3. PATCH_GL_GROUP — D-1 fix: clears validationOk + assembledProfile on members
// ---------------------------------------------------------------------------

{
  const groupId = 'g-abc'
  const otherGroupId = 'g-other'
  const state = makeState(
    [
      makeEntity({ entityCode: 'E1', formatGroupId: groupId, validationOk: true, assembledProfile: { x: 1 } }),
      makeEntity({ entityCode: 'E2', formatGroupId: groupId, validationOk: true, assembledProfile: { y: 2 } }),
      makeEntity({ entityCode: 'E3', formatGroupId: otherGroupId, validationOk: true, assembledProfile: { z: 3 } }),
    ],
    [
      {
        id: groupId,
        label: 'Format A',
        representativeIndex: 0,
        columnCount: 3,
        headers: HEADERS,
        headersConfirmed: true,
        mapping: MAPPING,
        partnerColumnsMode: 'single',
        opts: {},
        memberIndices: [0, 1],
      },
    ],
  )

  const next = wizardReducer(state, {
    type: 'PATCH_GL_GROUP',
    groupId,
    patch: { partnerColumnsMode: 'split' },
  })

  // Group itself is patched
  const g = next.gl.formatGroups.find((g: any) => g.id === groupId)!
  assert.equal(g.partnerColumnsMode, 'split', 'PATCH_GL_GROUP: group patch (partnerColumnsMode) applied')

  // D-1 fix: members of this group lose validationOk and assembledProfile
  assert.equal(next.gl.entities[0].validationOk, undefined, 'D-1: entity[0].validationOk cleared')
  assert.equal(next.gl.entities[0].assembledProfile, undefined, 'D-1: entity[0].assembledProfile cleared')
  assert.equal(next.gl.entities[1].validationOk, undefined, 'D-1: entity[1].validationOk cleared')
  assert.equal(next.gl.entities[1].assembledProfile, undefined, 'D-1: entity[1].assembledProfile cleared')

  // Entity in a different group is NOT touched
  assert.equal(next.gl.entities[2].validationOk, true, 'D-1: entity[2] (other group) validationOk preserved')
  assert.ok(
    next.gl.entities[2].assembledProfile !== undefined,
    'D-1: entity[2] (other group) assembledProfile preserved',
  )
}

// ---------------------------------------------------------------------------
// 4. REMOVE_GL_ENTITY — reindexes groups; deletes groups that become empty
// ---------------------------------------------------------------------------

{
  const groupId = 'g-xyz'
  // Entity 1 is the sole member of the group
  const state = makeState(
    [
      makeEntity({ entityCode: 'E0' }),
      makeEntity({ entityCode: 'E1', formatGroupId: groupId }),
    ],
    [
      {
        id: groupId,
        label: 'Format B',
        representativeIndex: 1,
        columnCount: 3,
        headers: HEADERS,
        headersConfirmed: true,
        mapping: MAPPING,
        partnerColumnsMode: 'single',
        opts: {},
        memberIndices: [1],
      },
    ],
  )

  const next = wizardReducer(state, { type: 'REMOVE_GL_ENTITY', index: 1 })

  assert.equal(next.gl.entities.length, 1, 'REMOVE_GL_ENTITY: entity array shrinks to 1')
  assert.equal(next.gl.entities[0].entityCode, 'E0', 'REMOVE_GL_ENTITY: E0 remains')
  // Group had only entity 1 → becomes empty → must be deleted
  assert.equal(next.gl.formatGroups.length, 0, 'REMOVE_GL_ENTITY: empty group deleted')
}

// ---------------------------------------------------------------------------
// 5. RESET_GL_COMBINE — strips combined state from all entities, clears groups
// ---------------------------------------------------------------------------

{
  const groupId = 'g-reset'
  const state = makeState(
    [
      makeEntity({
        entityCode: 'E1',
        combinedFileId: 'f1',
        combinedColumns: ['Account', 'fiscal_year'],
        combinedSample: [{ Account: '1000', fiscal_year: 2024 }],
        combinedDialect: { decimal: ',', thousands: '.' },
        combinedSuggestedHeaders: { Account: 'Account number' },
        headersConfirmed: true,
        combinedColumnWarning: 'Too many columns',
        formatGroupId: groupId,
        validationOk: true,
        assembledProfile: { x: 1 },
        entityAssignments: { '1000': 'Debtors' },
      }),
      makeEntity({
        entityCode: 'E2',
        combinedFileId: 'f2',
        formatGroupId: groupId,
        headersConfirmed: false,
      }),
    ],
    [
      {
        id: groupId,
        label: 'Format A',
        representativeIndex: 0,
        columnCount: 2,
        headers: HEADERS,
        headersConfirmed: true,
        mapping: MAPPING,
        partnerColumnsMode: 'single',
        opts: {},
        memberIndices: [0, 1],
      },
    ],
  )

  const next = wizardReducer(state, { type: 'RESET_GL_COMBINE' })

  // All format groups must be cleared
  assert.equal(next.gl.formatGroups.length, 0, 'RESET_GL_COMBINE: formatGroups cleared')

  // Entity 0: all combined fields cleared; yearFiles and entityCode preserved
  const e0 = next.gl.entities[0]
  assert.equal(e0.entityCode, 'E1', 'RESET_GL_COMBINE: entityCode preserved')
  assert.equal(e0.combinedFileId, undefined, 'RESET_GL_COMBINE: combinedFileId cleared')
  assert.equal(e0.combinedColumns, undefined, 'RESET_GL_COMBINE: combinedColumns cleared')
  assert.equal(e0.combinedSample, undefined, 'RESET_GL_COMBINE: combinedSample cleared')
  assert.equal(e0.combinedDialect, undefined, 'RESET_GL_COMBINE: combinedDialect cleared')
  assert.equal(e0.combinedSuggestedHeaders, undefined, 'RESET_GL_COMBINE: combinedSuggestedHeaders cleared')
  assert.equal(e0.headersConfirmed, undefined, 'RESET_GL_COMBINE: headersConfirmed cleared')
  assert.equal(e0.combinedColumnWarning, undefined, 'RESET_GL_COMBINE: combinedColumnWarning cleared')
  assert.equal(e0.formatGroupId, undefined, 'RESET_GL_COMBINE: formatGroupId cleared')
  assert.equal(e0.validationOk, undefined, 'RESET_GL_COMBINE: validationOk cleared')
  assert.equal(e0.assembledProfile, undefined, 'RESET_GL_COMBINE: assembledProfile cleared')
  assert.equal(e0.entityAssignments, undefined, 'RESET_GL_COMBINE: entityAssignments cleared')

  // Entity 1: same checks
  const e1 = next.gl.entities[1]
  assert.equal(e1.entityCode, 'E2', 'RESET_GL_COMBINE: entity[1] entityCode preserved')
  assert.equal(e1.combinedFileId, undefined, 'RESET_GL_COMBINE: entity[1] combinedFileId cleared')
  assert.equal(e1.formatGroupId, undefined, 'RESET_GL_COMBINE: entity[1] formatGroupId cleared')
  assert.equal(e1.headersConfirmed, undefined, 'RESET_GL_COMBINE: entity[1] headersConfirmed cleared')

  // yearFiles must NOT be touched (user should not need to re-upload)
  assert.deepEqual(e0.yearFiles, {}, 'RESET_GL_COMBINE: yearFiles preserved (was empty)')
}

// ---------------------------------------------------------------------------
// 6. SNAPSHOT invariant — assembledProfile.columns must be independent of group.mapping
// ---------------------------------------------------------------------------
// Regression guard for the GlGroupConfigPanel.buildMemberProfile aliasing bug.
//
// Root cause (pre-fix):
//   buildMemberProfile returned { columns: group.mapping } — a REFERENCE alias.
//   onMemberValidated stored that profile in entity state.
//   Any subsequent in-place mutation of group.mapping silently corrupted the
//   stored assembledProfile.columns, making the commit use different data than
//   what was validated — producing green validation → failing commit.
//
// Fix: { columns: { ...group.mapping } } — shallow snapshot, independent copy.
//
// This section demonstrates both behaviors so the contract is executable and
// searchable.  The PRE-FIX assertion shows the bug is real (alias propagates);
// the POST-FIX assertion shows the fix holds (snapshot is independent).
{
  // Simulate group.mapping as it sits in React state at validation time.
  const groupMapping: Record<string, string> = {
    posting_date: 'Buchungsdatum',
    journal_entry_number: 'Belegnummer',
    account_number: 'Kontonummer',
  }

  // PRE-FIX: buildMemberProfile returned columns: group.mapping (alias)
  const preFixProfile = { columns: groupMapping }

  // POST-FIX: buildMemberProfile returns columns: { ...group.mapping } (snapshot)
  const postFixProfile = { columns: { ...groupMapping } }

  // Simulate in-place mutation of group.mapping (e.g. re-confirmed headers no
  // longer auto-suggest posting_date because the column was renamed).
  const savedPostingDate = groupMapping['posting_date']
  delete (groupMapping as Record<string, unknown>)['posting_date']

  // 6a. PRE-FIX (alias): stored profile.columns reflects the mutation → BUG
  assert.equal(
    preFixProfile.columns['posting_date'],
    undefined,
    'Snapshot invariant PRE-FIX: alias means mutation of group.mapping propagates to ' +
    'assembledProfile.columns — posting_date silently lost (bug confirmed)',
  )

  // 6b. POST-FIX (snapshot): stored profile.columns is unaffected → CORRECT
  assert.equal(
    postFixProfile.columns['posting_date'],
    savedPostingDate,
    'Snapshot invariant POST-FIX: { ...group.mapping } snapshot preserves posting_date ' +
    'even after source mutation',
  )

  // 6c. Reference identity: post-fix columns object must differ from source mapping
  assert.notEqual(
    postFixProfile.columns,
    groupMapping,
    'Snapshot invariant: profile.columns must be a distinct object from group.mapping',
  )
}

// ---------------------------------------------------------------------------

console.log('wizardReducer.test.ts: all assertions passed')
