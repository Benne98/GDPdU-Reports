/**
 * Unit checks for glFormatGroups.ts — pure helpers.
 *
 * Run: npx tsx src/components/ingest/glFormatGroups.test.ts
 *
 * Uses only node:assert (no test framework required).
 *
 * SKIPPED: applyGroupHeadersToMembers — calls applyHeaders() (network I/O;
 *   no mock available without a test framework).
 */
import assert from 'node:assert/strict'
import {
  groupLabel,
  isEligibleForGroup,
  ineligibilityReason,
  createGroup,
  reindexGroupsAfterRemove,
} from './glFormatGroups'

// ---------------------------------------------------------------------------
// Helpers — plain objects; tsx strips types, only runtime fields matter
// ---------------------------------------------------------------------------

/** Minimal GlEntityState-shaped object. */
function ent(cols?: string[]): any {
  return {
    entityCode: 'E',
    yearFiles: {},
    ...(cols !== undefined ? { combinedColumns: cols } : {}),
  }
}

/** Minimal GlFormatGroup-shaped object. */
function grp(overrides: Record<string, unknown> = {}): any {
  return {
    id: 'g1',
    label: 'Format A',
    representativeIndex: 0,
    columnCount: 3,
    headers: ['a', 'b', 'c'],
    headersConfirmed: true,
    mapping: {},
    partnerColumnsMode: 'single',
    opts: {},
    memberIndices: [0],
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// 1. groupLabel — sequential A–Z then numeric overflow
// ---------------------------------------------------------------------------

{
  assert.equal(groupLabel(0), 'Group A', 'index 0 → Group A')
  assert.equal(groupLabel(1), 'Group B', 'index 1 → Group B')
  assert.equal(groupLabel(25), 'Group Z', 'index 25 → Group Z')
  // Overflow: GROUP_ALPHA[26] is undefined → fallback String(26+1) = '27'
  assert.equal(groupLabel(26), 'Group 27', 'index 26 → Group 27 (overflow)')
}

// ---------------------------------------------------------------------------
// 2. isEligibleForGroup — column-count match
// ---------------------------------------------------------------------------

{
  const g = grp({ columnCount: 3 })

  assert.ok(
    isEligibleForGroup(ent(['x', 'y', 'z']), g),
    'equal column count (3) → eligible',
  )
  assert.ok(
    !isEligibleForGroup(ent(['x', 'y']), g),
    'column count mismatch (2 vs 3) → not eligible',
  )
  assert.ok(
    !isEligibleForGroup(ent(), g),
    'missing combinedColumns → not eligible',
  )
}

// ---------------------------------------------------------------------------
// 3. ineligibilityReason — null when eligible, descriptive string otherwise
// ---------------------------------------------------------------------------

{
  const g = grp({ columnCount: 3 })

  // Eligible → null
  assert.equal(
    ineligibilityReason(ent(['x', 'y', 'z']), g),
    null,
    'eligible entity (3 cols) → null',
  )

  // Column count mismatch → string mentioning both counts
  const mismatch = ineligibilityReason(ent(['x', 'y']), g)
  assert.ok(typeof mismatch === 'string', 'column count mismatch → string reason')
  assert.ok(mismatch!.includes('2'), 'mismatch reason includes actual count (2)')
  assert.ok(mismatch!.includes('3'), 'mismatch reason includes expected count (3)')

  // No combinedColumns → string mentioning upload
  const noFile = ineligibilityReason(ent(), g)
  assert.ok(typeof noFile === 'string', 'missing combinedColumns → string reason')
  assert.ok(noFile!.toLowerCase().includes('upload'), 'no-file reason mentions upload')
}

// ---------------------------------------------------------------------------
// 4. reindexGroupsAfterRemove
// ---------------------------------------------------------------------------

// Case A: remove the only member → group deleted (empty result)
{
  const result = reindexGroupsAfterRemove(
    0,
    [grp({ memberIndices: [0], representativeIndex: 0 })],
  )
  assert.equal(result.length, 0, 'Case A: removing the only member deletes the group')
}

// Case B: remove non-rep middle member — indices above shift down, rep unchanged
{
  // Layout before: [E0(rep), E1, E2(removed), E3]
  const g = grp({ memberIndices: [0, 1, 2, 3], representativeIndex: 0 })
  const [r] = reindexGroupsAfterRemove(2, [g]) as any[]
  // E2 gone; E3 (was 3) → 2
  assert.deepEqual(r.memberIndices, [0, 1, 2], 'Case B: memberIndices after removing index 2')
  assert.equal(r.representativeIndex, 0, 'Case B: rep stays at 0')
}

// Case C: remove the representative → first remaining member promoted, indices decremented
{
  // Layout before: [E0(rep), E1, E2]
  const g = grp({ memberIndices: [0, 1, 2], representativeIndex: 0 })
  const [r] = reindexGroupsAfterRemove(0, [g]) as any[]
  // After remove 0: filter → [1,2], decrement > 0 → [0,1]; new rep = memberIndices[0] = 0
  assert.deepEqual(r.memberIndices, [0, 1], 'Case C: memberIndices=[0,1]')
  assert.equal(r.representativeIndex, 0, 'Case C: promoted member (was E1) is now rep at index 0')
}

// Case D: remove entity before the representative → rep index decremented
{
  // Layout before: [E0(removed), E1, E2(rep)]
  const g = grp({ memberIndices: [0, 1, 2], representativeIndex: 2 })
  const [r] = reindexGroupsAfterRemove(0, [g]) as any[]
  // After remove 0: filter → [1,2], decrement > 0 → [0,1]; rep 2 > 0 → rep = 1
  assert.deepEqual(r.memberIndices, [0, 1], 'Case D: memberIndices=[0,1]')
  assert.equal(r.representativeIndex, 1, 'Case D: rep decremented from 2 to 1')
}

// Case E: two groups — group with all indices below removedIndex is unaffected
{
  // Remove index 3
  // g1: [0,1], rep=0 — all indices < 3, completely unchanged
  // g2: [2,3], rep=2 — index 3 is removed; group survives with remaining member 2
  const g1 = grp({ id: 'g1', memberIndices: [0, 1], representativeIndex: 0 })
  const g2 = grp({ id: 'g2', label: 'Format B', memberIndices: [2, 3], representativeIndex: 2 })
  const result = reindexGroupsAfterRemove(3, [g1, g2]) as any[]

  assert.equal(result.length, 2, 'Case E: both groups survive (g2 still has member at index 2)')
  const r1 = result.find((g: any) => g.id === 'g1')!
  const r2 = result.find((g: any) => g.id === 'g2')!

  assert.deepEqual(r1.memberIndices, [0, 1], 'Case E: g1 memberIndices unchanged')
  assert.equal(r1.representativeIndex, 0, 'Case E: g1 rep unchanged')
  assert.deepEqual(r2.memberIndices, [2], 'Case E: g2 lost index 3, retains index 2')
  assert.equal(r2.representativeIndex, 2, 'Case E: g2 rep stays at 2 (not > 3 so no decrement)')
}

// ---------------------------------------------------------------------------
// 5. createGroup — structure and sequential labels
// ---------------------------------------------------------------------------

{
  const headers = ['Account', 'Date', 'Amount', 'Currency']
  const mapping = { account_number: 'Account', posting_date: 'Date' }

  // First group (no existing groups) → label 'Group A'
  const g1 = createGroup(2, headers, mapping, [])
  assert.equal(g1.memberIndices.length, 1, 'memberIndices has exactly one entry')
  assert.equal(g1.memberIndices[0], 2, 'memberIndices[0] === fromIndex (2)')
  assert.equal(g1.representativeIndex, 2, 'representativeIndex === fromIndex (2)')
  assert.equal(g1.columnCount, headers.length, 'columnCount === headers.length')
  assert.deepEqual(g1.headers, headers, 'headers stored verbatim')
  assert.deepEqual(g1.mapping, mapping, 'mapping stored verbatim')
  assert.equal(g1.headersConfirmed, true, 'headersConfirmed is true')
  assert.equal(g1.label, 'Group A', 'first group (existingGroups=[]) → "Group A"')
  assert.ok(typeof g1.id === 'string' && g1.id.length > 0, 'id is a non-empty string (UUID)')

  // Second group (one existing) → label 'Group B'
  const g2 = createGroup(0, ['c1', 'c2'], {}, [g1])
  assert.equal(g2.label, 'Group B', 'second group (existingGroups.length=1) → "Group B"')
  assert.equal(g2.representativeIndex, 0, 'second group rep = fromIndex (0)')
  assert.equal(g2.columnCount, 2, 'second group columnCount = 2')
  assert.equal(g2.memberIndices[0], 0, 'second group memberIndices[0] = fromIndex (0)')
}

// ---------------------------------------------------------------------------

console.log('glFormatGroups.test.ts: all assertions passed')
