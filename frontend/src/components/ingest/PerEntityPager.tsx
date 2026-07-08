/**
 * PerEntityPager — presentational pager for per-entity upload cards.
 *
 * Replaces a validEntities.map(...) list with a single-entity view navigated
 * by ◀ / ▶ buttons. Keeps wizard cards compact when there are many entities.
 */

import { useEffect, useState } from 'react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface WizardEntity {
  code: string
  prefix: string
  name: string
}

export interface PerEntityPagerProps {
  entities: WizardEntity[]
  /** Number of entities that have a staged file/profile. */
  stagedCount: number
  /** Render function for the currently-displayed entity. */
  renderEntity: (entity: WizardEntity, index: number) => React.ReactNode
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function PerEntityPager({ entities, stagedCount, renderEntity }: PerEntityPagerProps) {
  const [idx, setIdx] = useState(0)

  // Clamp index when the entity list shrinks (e.g. user removes an entity upstream).
  useEffect(() => {
    if (entities.length === 0) return
    setIdx(prev => Math.min(prev, entities.length - 1))
  }, [entities.length])

  if (entities.length === 0) return null

  const entity = entities[idx]

  return (
    <div className="space-y-3">
      {/* Pager header */}
      <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setIdx(i => Math.max(0, i - 1))}
            disabled={idx === 0}
            className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 transition"
            aria-label="Previous entity"
          >
            &#9664;
          </button>

          <span className="text-sm font-semibold text-slate-700">
            {entity.name && entity.name !== entity.prefix
              ? `${entity.name} (${entity.prefix || entity.code})`
              : (entity.prefix || entity.code || `Entity ${idx + 1}`)}
            <span className="ml-1.5 font-normal text-slate-400">
              · {idx + 1} of {entities.length}
            </span>
          </span>

          <button
            type="button"
            onClick={() => setIdx(i => Math.min(entities.length - 1, i + 1))}
            disabled={idx === entities.length - 1}
            className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 transition"
            aria-label="Next entity"
          >
            &#9654;
          </button>
        </div>

        <span className={`text-xs font-medium ${
          stagedCount === entities.length
            ? 'text-emerald-600'
            : stagedCount > 0
            ? 'text-amber-600'
            : 'text-slate-400'
        }`}>
          {stagedCount} of {entities.length} files staged
        </span>
      </div>

      {/* Body — single entity card */}
      {renderEntity(entity, idx)}
    </div>
  )
}
