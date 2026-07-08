/**
 * GlGroupSelect.tsx — "Which other entities use this same column format?"
 *
 * Shown inside GlEntityCard for the representative entity after headers are
 * confirmed and a group has been created. Lets the user pick which other
 * entities share the same column layout so their headers can be applied
 * automatically.
 *
 * Entities whose column count does not match are shown but disabled with a
 * reason. Count-match is a pre-suggestion; the user makes the final selection.
 */

import { useState } from 'react'
import type { GlEntityState, GlFormatGroup } from './GlEntityCard'
import { ineligibilityReason } from './glFormatGroups'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface GlGroupSelectProps {
  /** The group that was just created for the representative entity. */
  group: GlFormatGroup
  /** All entities in the wizard (used to populate options). */
  entities: GlEntityState[]
  /** Index of the representative entity (excluded from the list). */
  representativeIndex: number
  /**
   * Called when the user finalises the selection.
   * Receives the indices of entities to ADD to the group
   * (excluding the representative who is already a member).
   */
  onConfirm: (selectedIndices: number[]) => Promise<void>
  /** True while parent is applying headers to selected entities. */
  loading?: boolean
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function GlGroupSelect({
  group,
  entities,
  representativeIndex,
  onConfirm,
  loading = false,
}: GlGroupSelectProps) {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [localLoading, setLocalLoading] = useState(false)

  const otherEntities = entities
    .map((entity, index) => ({ entity, index }))
    .filter(({ index }) => index !== representativeIndex)

  // Nothing to group with
  if (otherEntities.length === 0) return null

  const busy = loading || localLoading

  async function handleConfirm(indices: number[]) {
    setLocalLoading(true)
    try {
      await onConfirm(indices)
    } finally {
      setLocalLoading(false)
    }
  }

  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/60 px-4 py-4 space-y-3">
      <div>
        <p className="text-sm font-semibold text-indigo-900">
          Which other entities use this same column format?
        </p>
        <p className="text-xs text-indigo-700 mt-0.5">
          Entities with {group.columnCount} columns can share{' '}
          <span className="font-semibold">{group.label}</span>. Their headers and
          mapping will be applied automatically. Entities with a different column
          count must have their headers assigned separately.
        </p>
      </div>

      <div className="space-y-2">
        {otherEntities.map(({ entity, index }) => {
          const reason = ineligibilityReason(entity, group)
          const eligible = reason === null
          const isSelected = selected.has(index)
          const eCode = entity.entityCode.trim()
          const eLabel = entity.entityLabel?.trim()
          const entityName =
            eLabel && eLabel !== eCode ? `${eLabel} (${eCode})` : eCode || `Entity ${index + 1}`
          const colCount = entity.combinedColumns?.length ?? null

          return (
            <label
              key={index}
              className={`flex items-start gap-3 rounded-md border p-3 transition select-none ${
                !eligible
                  ? 'border-slate-200 bg-white/50 opacity-60 cursor-not-allowed'
                  : isSelected
                  ? 'border-indigo-400 bg-white cursor-pointer'
                  : 'border-slate-200 bg-white hover:border-indigo-300 cursor-pointer'
              }`}
            >
              <input
                type="checkbox"
                checked={isSelected}
                disabled={!eligible || busy}
                onChange={e => {
                  const next = new Set(selected)
                  if (e.target.checked) next.add(index)
                  else next.delete(index)
                  setSelected(next)
                }}
                className="mt-0.5 accent-blue-600 shrink-0"
              />
              <div className="min-w-0">
                <p className="text-sm font-semibold text-slate-800">{entityName}</p>
                {!eligible ? (
                  <p className="text-xs text-slate-500 mt-0.5">{reason}</p>
                ) : colCount !== null ? (
                  <p className="text-xs text-slate-500 mt-0.5">
                    {colCount} columns — same format as {group.label}
                  </p>
                ) : (
                  <p className="text-xs text-slate-400 mt-0.5">
                    No combined file yet — can be added to the group later
                  </p>
                )}
              </div>
            </label>
          )
        })}
      </div>

      <div className="flex flex-wrap items-center gap-3 pt-1">
        <button
          type="button"
          onClick={() => void handleConfirm([])}
          disabled={busy}
          className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40 transition"
        >
          {busy ? 'Applying…' : 'No other entities — different formats'}
        </button>
        {selected.size > 0 && (
          <button
            type="button"
            onClick={() => void handleConfirm([...selected])}
            disabled={busy}
            className="rounded-md px-5 py-2 text-sm font-semibold text-white disabled:opacity-40 disabled:cursor-not-allowed transition hover:opacity-90"
            style={{ backgroundColor: '#1E3A5F' }}
          >
            {busy
              ? 'Applying headers…'
              : `Add ${selected.size} ${selected.size === 1 ? 'entity' : 'entities'} to ${group.label}`}
          </button>
        )}
      </div>
    </div>
  )
}
