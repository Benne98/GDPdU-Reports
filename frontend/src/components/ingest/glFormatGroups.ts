/**
 * glFormatGroups.ts — Shared helpers for GL format-group management.
 *
 * Consumed by ProjectSetupWizard and IngestionPage (both parents).
 * NOT imported by GlEntityCard — avoids circular dependency.
 *
 * A "format group" is a set of entities that share the same column layout.
 * The group owns the confirmed headers, column mapping, partner-column config,
 * and options. Member entities reference the group via formatGroupId and each
 * materialise their own assembledProfile (entity identity + group config +
 * combinedFileId) for the Finish commit loop.
 */

import type { GlEntityState, GlFormatGroup, PartnerColumnsMode } from './GlEntityCard'
import { defaultGlOpts } from './GlEntityCard'
import { applyHeaders } from '../../lib/gdpduApi'

// ─────────────────────────────────────────────────────────────────────────────
// Label helpers
// ─────────────────────────────────────────────────────────────────────────────

const GROUP_ALPHA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')

/** Returns "Group A", "Group B", … "Group Z", "Group 27" for overflow. */
export function groupLabel(index: number): string {
  return `Group ${GROUP_ALPHA[index] ?? String(index + 1)}`
}

// ─────────────────────────────────────────────────────────────────────────────
// Eligibility
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns true when the entity's combined column count matches the group's
 * columnCount. Count-match is a pre-suggestion; the user makes the final call.
 */
export function isEligibleForGroup(entity: GlEntityState, group: GlFormatGroup): boolean {
  if (!entity.combinedColumns) return false
  return entity.combinedColumns.length === group.columnCount
}

/**
 * Returns a human-readable reason why an entity cannot join a group, or null
 * when the entity is eligible.
 */
export function ineligibilityReason(entity: GlEntityState, group: GlFormatGroup): string | null {
  if (!entity.combinedColumns) return 'No combined file yet — upload and combine first'
  if (entity.combinedColumns.length !== group.columnCount) {
    return `${entity.combinedColumns.length} columns != ${group.columnCount} — assign its headers separately`
  }
  return null
}

// ─────────────────────────────────────────────────────────────────────────────
// Group creation helper
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Constructs a new GlFormatGroup for the given representative entity.
 * Called by the parent when a representative entity confirms its headers.
 */
export function createGroup(
  fromIndex: number,
  headers: string[],
  mapping: Record<string, string>,
  existingGroups: GlFormatGroup[],
): GlFormatGroup {
  return {
    id: crypto.randomUUID(),
    label: groupLabel(existingGroups.length),
    representativeIndex: fromIndex,
    columnCount: headers.length,
    headers,
    headersConfirmed: true,
    mapping,
    partnerColumnsMode: 'single' as PartnerColumnsMode,
    partnerColumnsSplit: undefined,
    opts: defaultGlOpts(),
    memberIndices: [fromIndex],
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Build groups from GlFormatAssignmentStep assignment map
// ─────────────────────────────────────────────────────────────────────────────

export interface BuildGroupsResult {
  groups: GlFormatGroup[]
  entityPatches: Array<{ index: number; patch: Partial<GlEntityState> }>
}

/**
 * Converts the assignment map produced by GlFormatAssignmentStep into a new
 * set of GlFormatGroups and entity patches.
 *
 * assignment: Record<entityIndex, formatKey> — entities sharing a key share a group.
 * Groups are labelled in creation order ("Format A", "Format B", …).
 * The representative for each group is the first member index in that group.
 * headersConfirmed is always false — the representative must still confirm headers.
 */
export function buildGroupsFromAssignments(
  assignment: Record<number, string>,
  entities: GlEntityState[],
): BuildGroupsResult {
  // Collect distinct format keys in the order they first appear
  const keyOrder: string[] = []
  for (const key of Object.values(assignment)) {
    if (!keyOrder.includes(key)) keyOrder.push(key)
  }

  const groups: GlFormatGroup[] = []
  const entityPatches: Array<{ index: number; patch: Partial<GlEntityState> }> = []

  keyOrder.forEach((key, groupIdx) => {
    const memberIndices = Object.entries(assignment)
      .filter(([, k]) => k === key)
      .map(([i]) => Number(i))
      .sort((a, b) => a - b)

    const representativeIndex = memberIndices[0]
    const rep = entities[representativeIndex]
    const columnCount = rep?.combinedColumns?.length ?? 0

    const group: GlFormatGroup = {
      id: crypto.randomUUID(),
      label: groupLabel(groupIdx),
      representativeIndex,
      columnCount,
      headers: [],
      headersConfirmed: false,
      mapping: {},
      partnerColumnsMode: 'single',
      partnerColumnsSplit: undefined,
      opts: defaultGlOpts(),
      memberIndices,
    }
    groups.push(group)

    for (const idx of memberIndices) {
      entityPatches.push({
        index: idx,
        patch: {
          formatGroupId: group.id,
          headersConfirmed: undefined,
          validationOk: undefined,
          assembledProfile: undefined,
        },
      })
    }
  })

  return { groups, entityPatches }
}

// ─────────────────────────────────────────────────────────────────────────────
// Re-indexing after entity removal
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Re-indexes all groups after the entity at `removedIndex` has been removed.
 * - Removes the entity from its group's memberIndices
 * - Decrements all indices greater than removedIndex
 * - If the removed entity was the representative, promotes the first remaining member
 * - Removes groups that have become empty
 */
export function reindexGroupsAfterRemove(
  removedIndex: number,
  groups: GlFormatGroup[],
): GlFormatGroup[] {
  return groups
    .map((g): GlFormatGroup | null => {
      const memberIndices = g.memberIndices
        .filter(i => i !== removedIndex)
        .map(i => (i > removedIndex ? i - 1 : i))
      if (memberIndices.length === 0) return null
      const repWasRemoved = g.representativeIndex === removedIndex
      const representativeIndex = repWasRemoved
        ? memberIndices[0]
        : g.representativeIndex > removedIndex
        ? g.representativeIndex - 1
        : g.representativeIndex
      return { ...g, memberIndices, representativeIndex }
    })
    .filter((g): g is GlFormatGroup => g !== null)
}

// ─────────────────────────────────────────────────────────────────────────────
// Apply group headers to member entities
// ─────────────────────────────────────────────────────────────────────────────

export interface GroupHeadersResult {
  patches: Array<{ index: number; patch: Partial<GlEntityState> }>
  applied: string[]
  skipped: string[]
}

/**
 * Applies the group's confirmed headers to all non-representative member
 * entities that have a matching column count. Returns patches to apply to
 * each entity and human-readable applied/skipped lists.
 *
 * Usage: call this after ASSIGN_GL_GROUP has added indices to the group, then
 * dispatch PATCH_GL_ENTITY_MULTI with the returned patches.
 */
export async function applyGroupHeadersToMembers(
  group: GlFormatGroup,
  targetIndices: number[],
  entities: GlEntityState[],
): Promise<GroupHeadersResult> {
  const patches: Array<{ index: number; patch: Partial<GlEntityState> }> = []
  const applied: string[] = []
  const skipped: string[] = []

  for (const memberIndex of targetIndices) {
    if (memberIndex === group.representativeIndex) continue
    const entity = entities[memberIndex]
    if (!entity) continue
    if (!entity.combinedFileId || !entity.combinedColumns) {
      skipped.push(entity.entityCode || `Entity ${memberIndex + 1}`)
      continue
    }
    if (entity.combinedColumns.length !== group.columnCount) {
      skipped.push(entity.entityCode || `Entity ${memberIndex + 1}`)
      continue
    }
    try {
      const result = await applyHeaders(entity.combinedFileId, group.headers)
      patches.push({
        index: memberIndex,
        patch: {
          combinedFileId: result.file_id,
          combinedColumns: result.columns,
          combinedSample: result.sample,
          combinedSuggestedHeaders: undefined,
          headersConfirmed: true,
          formatGroupId: group.id,
          validationOk: undefined,
          assembledProfile: undefined,
          entityAssignments: undefined,
        },
      })
      applied.push(entity.entityCode || `Entity ${memberIndex + 1}`)
    } catch {
      skipped.push(entity.entityCode || `Entity ${memberIndex + 1}`)
    }
  }

  return { patches, applied, skipped }
}
