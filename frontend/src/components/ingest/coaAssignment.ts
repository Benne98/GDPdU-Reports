/**
 * coaAssignment.ts — Pure helper for building the initial CoA mapping assignment.
 *
 * Extracted from ProjectSetupWizard.tsx (both the single-entity useEffect and
 * the multi-entity initialAssignment IIFE) so the logic can be tested in
 * isolation without touching JSX.
 *
 * FIX: All entities default to one shared CoA group key ('coa-shared') regardless
 * of their GL column-layout group (formatGroupId). This ensures a single uploaded
 * CoA file covers every entity in a multi-entity project.
 *
 * Previous (buggy) behavior keyed off formatGroupId, so entities with different
 * GL column layouts landed in different CoA groups — causing the uploaded CoA to
 * attach to only the first entity's group while other groups had no file.
 */

/**
 * Builds the initial CoA mapping assignment for the given entity codes.
 *
 * Returns a Record<entityCode (or 'default' for empty codes), groupKey>.
 * Every entity receives the same shared key so a single uploaded CoA file
 * covers all entities regardless of GL column-layout differences.
 *
 * @param entityCodes              List of entity codes (may include empty strings).
 * @param _formatGroupIdByEntity   Map of entityCode → GL formatGroupId. Accepted
 *   for interface compatibility; not used for the shared-key default.
 */
/**
 * Returns true when every entityCode appears in at least one group's memberEntityCodes.
 * Used to detect stale coverage so the assignment step is shown even when coa.assigned===true.
 */
export function coaGroupsCoverAllEntities(
  groups: CoaGroupMin[],
  entityCodes: string[],
): boolean {
  if (entityCodes.length === 0) return true
  const covered = new Set(groups.flatMap(g => g.memberEntityCodes))
  return entityCodes.every(code => covered.has(code))
}

export function buildInitialCoaAssignment(
  entityCodes: string[],
  _formatGroupIdByEntity: Record<string, string | undefined>,
): Record<string, string> {
  const result: Record<string, string> = {}
  for (const code of entityCodes) {
    result[code || 'default'] = 'coa-shared'
  }
  return result
}

// ── Internal minimal types for buildCoaItems / migrateLegacyCoaGroups ─────────
// Defined here (not imported from ProjectSetupWizard.tsx) to avoid circular deps.
// Structurally compatible with CoaMappingGroup / CoaUploadSlot from the wizard —
// TypeScript structural typing accepts CoaMappingGroup wherever CoaGroupMin is
// expected (CoaMappingGroup has all CoaGroupMin fields plus optional extras).

interface CoaSlotMin {
  fileId?: string
  isMaster?: boolean
  mapping?: Record<string, string>
}

interface CoaGroupMin {
  id: string
  label: string
  memberEntityCodes: string[]
  method?: 'library' | 'upload'
  /** True when the user actively chose this group's method (library or upload).
   *  migrateLegacyCoaGroups never folds a methodExplicit group into the pivot. */
  methodExplicit?: boolean
  bs?: CoaSlotMin
  pl?: CoaSlotMin
}

/** Flattened (group × statement) commit item — ONE per upload group×stmt (multi-entity fan-out). */
export type CoaItem = {
  group: CoaGroupMin
  stmt: 'bs' | 'pl'
  slot: CoaSlotMin
  /** Deduplicated entity prefixes for all member entities in this group (empty = no prefix set). */
  entityPrefixes: string[]
}

/**
 * Build the ordered list of (group × statement) commit items from CoA groups.
 *
 * Pure function — mirrors the inline coaItems loop in the ProjectSetupWizard Finish handler
 * so the commit-emission logic can be unit-tested without JSX. Skips groups with
 * method='library' (those are applied at rebuild) and statement slots without a fileId.
 *
 * Emits ONE item per [upload group × stmt] with entity_prefixes covering ALL member entities
 * of that group (multi-entity atomic fan-out). No per-member expansion.
 *
 * @param groups         All CoA mapping groups (upload + library); library ones are filtered out.
 * @param wizardEntities Wizard-level entities (code, prefix) used to look up entity prefixes.
 */
export function buildCoaItems(
  groups: CoaGroupMin[],
  wizardEntities: ReadonlyArray<{ code: string; prefix?: string; name?: string }>,
): CoaItem[] {
  const uploadGroups = groups.filter(g => g.method === 'upload')
  const items: CoaItem[] = []
  for (const group of uploadGroups) {
    for (const stmt of ['bs', 'pl'] as const) {
      const slot = group[stmt]
      if (!slot?.fileId) continue
      // Deduplicate prefixes across all member entities (skip empty/undefined).
      const entityPrefixes = [...new Set(
        group.memberEntityCodes
          .map(code => wizardEntities.find(we => we.code === code)?.prefix)
          .filter((p): p is string => !!p),
      )]
      items.push({ group, stmt, slot, entityPrefixes })
    }
  }
  return items
}

/**
 * Detect legacy per-format CoA groups and return a merged assignment.
 *
 * OLD behavior in buildInitialCoaAssignment keyed each entity's CoA group by its
 * GL formatGroupId, so a two-entity project (01 = fmt-a, 02 = fmt-b) landed in
 * two separate CoA groups. When the user uploaded ONE shared CoA file to group-A,
 * group-B had no fileId and entity 02 produced zero commit rows.
 *
 * Migration condition (all must be true):
 *   • More than one group total
 *   • Exactly ONE group has an uploaded file (bs?.fileId or pl?.fileId)
 *   • At least one non-library group has NO file (something to collapse)
 *
 * Returns a Record<entityCode, groupKey> mapping every non-library entity to the
 * pivot group (the one that has the file), ready to pass to APPLY_COA_ASSIGNMENTS.
 * Library groups keep their own key.
 *
 * Returns null when no migration is needed:
 *   • Only one group (already correct)
 *   • Zero or ≥2 groups with files (deliberate multi-CoA or nothing to do)
 *   • No non-library group is missing a file
 */
export function migrateLegacyCoaGroups(
  groups: CoaGroupMin[],
): Record<string, string> | null {
  if (groups.length <= 1) return null

  const groupsWithFile = groups.filter(g => g.bs?.fileId || g.pl?.fileId)
  // 0 files → nothing to commit anyway; ≥2 files → deliberate per-group upload, don't touch.
  if (groupsWithFile.length !== 1) return null

  const pivot = groupsWithFile[0]

  // Non-methodExplicit groups without any uploaded file are the candidates to collapse.
  // methodExplicit groups (user actively chose their method/membership) are never folded.
  const nonExplicitWithoutFile = groups.filter(
    g => !g.methodExplicit && !g.bs?.fileId && !g.pl?.fileId,
  )
  if (nonExplicitWithoutFile.length === 0) return null  // nothing to collapse

  // Build merged assignment:
  //   methodExplicit groups → own key (preserved as-is).
  //   non-methodExplicit groups   → pivot.id (folded into the file-bearing group).
  const assignment: Record<string, string> = {}
  for (const g of groups) {
    const targetKey = g.methodExplicit ? g.id : pivot.id
    for (const code of g.memberEntityCodes) {
      assignment[code] = targetKey
    }
  }
  return assignment
}
