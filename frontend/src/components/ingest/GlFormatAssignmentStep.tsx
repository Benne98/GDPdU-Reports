/**
 * GlFormatAssignmentStep.tsx — Forced standalone format-assignment screen.
 *
 * Shown between 'collect' and 'configure' phases in StepGlBookings when there
 * are multiple entities. Displays one row per entity with a format selector
 * (no data preview tables). The user must assign every entity to a format group
 * or "Own format" before continuing.
 *
 * Props:
 *   entities         — all GlEntityState entries (must all have combinedFileId).
 *   onConfirm        — called with assignment: Record<entityIndex, formatKey>.
 *   initialAssignment — optional pre-filled assignment (e.g. on back-navigation).
 */

import { useState } from 'react'
import type { GlEntityState } from './GlEntityCard'
import { groupLabel, ineligibilityReason } from './glFormatGroups'
import type { GlFormatGroup } from './GlEntityCard'
import { defaultGlOpts } from './GlEntityCard'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface GlFormatAssignmentStepProps {
  entities: GlEntityState[]
  onConfirm: (assignment: Record<number, string>) => void
  initialAssignment?: Record<number, string>
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Builds a minimal stub GlFormatGroup for ineligibilityReason checks. */
function stubGroup(key: string, columnCount: number, representativeIndex: number): GlFormatGroup {
  return {
    id: key,
    label: '',
    representativeIndex,
    columnCount,
    headers: [],
    headersConfirmed: false,
    mapping: {},
    partnerColumnsMode: 'single',
    partnerColumnsSplit: undefined,
    opts: defaultGlOpts(),
    memberIndices: [representativeIndex],
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function GlFormatAssignmentStep({
  entities,
  onConfirm,
  initialAssignment,
}: GlFormatAssignmentStepProps) {
  // assignment: entityIndex → formatKey (stable string ID)
  const [assignment, setAssignment] = useState<Record<number, string>>(initialAssignment ?? {})

  // Ordered list of distinct format keys as they are first created.
  // Creation order determines the display label ("Format A", "Format B", …).
  const [keyOrder, setKeyOrder] = useState<string[]>(() => {
    if (!initialAssignment) return []
    const seen: string[] = []
    for (const k of Object.values(initialAssignment)) {
      if (!seen.includes(k)) seen.push(k)
    }
    return seen
  })

  // Format keys that are still referenced by at least one entity
  const activeKeys = keyOrder.filter(k => Object.values(assignment).includes(k))

  /** Column count for the first entity assigned to a given key (or null). */
  function keyColumnCount(key: string): number | null {
    const firstIdx = Object.entries(assignment).find(([, k]) => k === key)?.[0]
    if (firstIdx == null) return null
    return entities[Number(firstIdx)]?.combinedColumns?.length ?? null
  }

  function handleSelect(entityIdx: number, value: string) {
    if (value === '') {
      setAssignment(prev => {
        const next = { ...prev }
        delete next[entityIdx]
        return next
      })
      return
    }

    if (value === '__own__') {
      // Mint a new stable key based on current number of distinct keys
      const existingKeys = new Set(Object.values(assignment))
      // Remove the current entity's key from count so re-selecting "Own format" doesn't increment
      const currentKey = assignment[entityIdx]
      if (currentKey) existingKeys.delete(currentKey)
      const newKey = `grp-${existingKeys.size}`
      setKeyOrder(prev => (prev.includes(newKey) ? prev : [...prev, newKey]))
      setAssignment(prev => ({ ...prev, [entityIdx]: newKey }))
    } else {
      setAssignment(prev => ({ ...prev, [entityIdx]: value }))
      setKeyOrder(prev => (prev.includes(value) ? prev : [...prev, value]))
    }
  }

  const allAssigned = entities.every((_, i) => Boolean(assignment[i]))

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <div className="px-6 pt-5 pb-4 border-b border-slate-100">
        <h3 className="text-base font-semibold text-slate-900">Assign column formats</h3>
        <p className="mt-1 text-sm text-slate-500">
          All files have been combined. Assign each entity to a format group — entities with
          the same column layout can share one format. Entities with different column counts
          must each have their own format.
        </p>
      </div>

      {/* Entity rows */}
      <div className="divide-y divide-slate-100">
        {entities.map((entity, i) => {
          const entityName = entity.entityCode.trim() || `Entity ${i + 1}`
          const colCount = entity.combinedColumns?.length ?? 0
          const currentKey = assignment[i]

          return (
            <div key={i} className="flex items-center gap-4 px-6 py-4">
              {/* Entity identity */}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-slate-800">{entityName}</p>
                <p className="text-xs text-slate-400 mt-0.5">{colCount} columns</p>
              </div>

              {/* Format selector */}
              <div className="shrink-0 w-72">
                <select
                  value={currentKey ?? ''}
                  onChange={e => handleSelect(i, e.target.value)}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">-- Assign a format --</option>

                  {/* Existing format keys */}
                  {activeKeys.map((key) => {
                    if (key === currentKey) {
                      // Always show the currently-selected key as enabled
                      const colCnt = keyColumnCount(key) ?? colCount
                      return (
                        <option key={key} value={key}>
                          {groupLabel(keyOrder.indexOf(key))} · {colCnt} columns
                        </option>
                      )
                    }
                    const kcc = keyColumnCount(key)
                    const stub = kcc != null
                      ? stubGroup(key, kcc, Number(Object.entries(assignment).find(([, k]) => k === key)?.[0] ?? 0))
                      : null
                    const reason = stub ? ineligibilityReason(entity, stub) : null
                    const compatible = reason === null
                    const labelIdx = keyOrder.indexOf(key)
                    return (
                      <option key={key} value={key} disabled={!compatible}>
                        {groupLabel(labelIdx)} · {kcc ?? '?'} columns
                        {!compatible && reason ? ` — ${reason}` : ''}
                      </option>
                    )
                  })}

                  <option value="__own__">Own format</option>
                </select>

                {/* Show assigned label */}
                {currentKey && (
                  <p className="mt-1 text-xs text-slate-500">
                    Assigned to{' '}
                    <span className="font-semibold text-slate-700">
                      {groupLabel(keyOrder.indexOf(currentKey))}
                    </span>
                  </p>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Footer */}
      <div className="px-6 py-4 border-t border-slate-100 flex items-center justify-between gap-4">
        {!allAssigned && (
          <p className="text-xs text-amber-700">Assign a format to every entity to continue.</p>
        )}
        <div className="ml-auto">
          <button
            type="button"
            onClick={() => onConfirm(assignment)}
            disabled={!allAssigned}
            className="rounded-md px-6 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ backgroundColor: '#1E3A5F' }}
          >
            Continue
          </button>
        </div>
      </div>
    </div>
  )
}
