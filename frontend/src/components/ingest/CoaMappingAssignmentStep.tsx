/**
 * CoaMappingAssignmentStep.tsx — one-row-per-entity mapping group assignment for CoA.
 *
 * Cloned from GlFormatAssignmentStep but with two key differences:
 *   - Operates on project entities ({ code, prefix, name }) not GL upload entities.
 *   - No column-count compatibility gating — any entity can join any group.
 *
 * Props:
 *   entities          — project-level entities from WizardState.entities
 *   onConfirm         — called with assignment: Record<entityCode, groupKey>
 *   initialAssignment — optional pre-filled assignment (from existing coa.groups or
 *                       GL formatGroupIds computed by the parent)
 */

import { useState } from 'react'
import { groupLabel } from './glFormatGroups'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface CoaMappingAssignmentStepProps {
  entities: Array<{ code: string; prefix: string; name: string }>
  onConfirm: (assignment: Record<string, string>) => void
  initialAssignment?: Record<string, string>
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CoaMappingAssignmentStep({
  entities,
  onConfirm,
  initialAssignment,
}: CoaMappingAssignmentStepProps) {
  // assignment: entityCode → groupKey
  const [assignment, setAssignment] = useState<Record<string, string>>(
    initialAssignment ?? {},
  )

  // Ordered list of distinct group keys as they first appear (determines display label).
  const [keyOrder, setKeyOrder] = useState<string[]>(() => {
    const init = initialAssignment ?? {}
    const seen: string[] = []
    for (const k of Object.values(init)) {
      if (!seen.includes(k)) seen.push(k)
    }
    return seen
  })

  // Keys still referenced by at least one entity
  const activeKeys = keyOrder.filter(k => Object.values(assignment).includes(k))

  function handleSelect(entityCode: string, value: string) {
    if (value === '') {
      setAssignment(prev => {
        const next = { ...prev }
        delete next[entityCode]
        return next
      })
      return
    }

    if (value === '__own__') {
      // Mint a new stable key; don't re-increment if the entity already owns a key
      const existingKeys = new Set(Object.values(assignment))
      const currentKey = assignment[entityCode]
      if (currentKey) existingKeys.delete(currentKey)
      const newKey = `coa-grp-${existingKeys.size}`
      setKeyOrder(prev => (prev.includes(newKey) ? prev : [...prev, newKey]))
      setAssignment(prev => ({ ...prev, [entityCode]: newKey }))
    } else {
      setAssignment(prev => ({ ...prev, [entityCode]: value }))
      setKeyOrder(prev => (prev.includes(value) ? prev : [...prev, value]))
    }
  }

  const allAssigned = entities.every(e => Boolean(assignment[e.code]))

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <div className="px-6 pt-5 pb-4 border-b border-slate-100">
        <h3 className="text-base font-semibold text-slate-900">Assign CoA mapping groups</h3>
        <p className="mt-1 text-sm text-slate-500">
          Entities sharing the same account structure can use one mapping group. Defaults are
          suggested from your GL format groups — adjust as needed.
        </p>
      </div>

      {/* Entity rows */}
      <div className="divide-y divide-slate-100">
        {entities.map((entity) => {
          const entityCode = entity.code
          const displayName = entity.name || entity.prefix || entity.code || 'Entity'
          const currentKey = assignment[entityCode]

          return (
            <div key={entityCode} className="flex items-center gap-4 px-6 py-4">
              {/* Entity identity */}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-slate-800">{displayName}</p>
                {entity.prefix && (
                  <p className="text-xs text-slate-400 mt-0.5">Prefix: {entity.prefix}</p>
                )}
              </div>

              {/* Group selector */}
              <div className="shrink-0 w-72">
                <select
                  value={currentKey ?? ''}
                  onChange={e => handleSelect(entityCode, e.target.value)}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">-- Assign a mapping group --</option>

                  {/* Existing group keys */}
                  {activeKeys.map((key) => {
                    const labelIdx = keyOrder.indexOf(key)
                    return (
                      <option key={key} value={key}>
                        {groupLabel(labelIdx)}
                      </option>
                    )
                  })}

                  <option value="__own__">Own mapping group</option>
                </select>

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
          <p className="text-xs text-amber-700">
            Assign a mapping group to every entity to continue.
          </p>
        )}
        <div className="ml-auto">
          <button
            type="button"
            onClick={() => onConfirm(assignment)}
            disabled={!allAssigned}
            className="rounded-md px-6 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ backgroundColor: '#1E3A5F' }}
          >
            Confirm assignment
          </button>
        </div>
      </div>
    </div>
  )
}
