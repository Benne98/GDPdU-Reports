/**
 * PerYearPager.tsx — thin year-axis pager for per-FY upload slots in the FTE step.
 *
 * Complements PerEntityPager for the fiscal-year sub-axis.
 * Do NOT mutate PerEntityPager for this purpose — its signature is shared by the GL step.
 *
 * Usage:
 *   <PerYearPager
 *     fyLabels={fyLabels}
 *     stagedCount={n}
 *     renderYear={(fyLabel, fyIndex) => <UploadZone ... />}
 *   />
 */

import { useEffect, useState } from 'react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PerYearPagerProps {
  /** Ordered list of fiscal-year labels, e.g. ["FY2022", "FY2023"] or ["FY22/23"]. */
  fyLabels: string[]
  /** Number of FY slots that already have a staged file. */
  stagedCount: number
  /** Render function for the currently-displayed fiscal year slot. */
  renderYear: (fyLabel: string, fyIndex: number) => React.ReactNode
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function PerYearPager({ fyLabels, stagedCount, renderYear }: PerYearPagerProps) {
  const [idx, setIdx] = useState(0)

  // Clamp index when the label list changes (e.g. GL years are added/removed upstream).
  useEffect(() => {
    if (fyLabels.length === 0) return
    setIdx(prev => Math.min(prev, fyLabels.length - 1))
  }, [fyLabels.length])

  if (fyLabels.length === 0) return null

  const fyLabel = fyLabels[idx]

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
            aria-label="Previous fiscal year"
          >
            &#9664;
          </button>

          <span className="text-sm font-semibold text-slate-700">
            {fyLabel} ({idx + 1} of {fyLabels.length})
          </span>

          <button
            type="button"
            onClick={() => setIdx(i => Math.min(fyLabels.length - 1, i + 1))}
            disabled={idx === fyLabels.length - 1}
            className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 transition"
            aria-label="Next fiscal year"
          >
            &#9654;
          </button>
        </div>

        <span
          className={`text-xs font-medium ${
            stagedCount === fyLabels.length
              ? 'text-emerald-600'
              : stagedCount > 0
              ? 'text-amber-600'
              : 'text-slate-400'
          }`}
        >
          {stagedCount} of {fyLabels.length} years uploaded
        </span>
      </div>

      {/* Body — single fiscal-year upload zone */}
      {renderYear(fyLabel, idx)}
    </div>
  )
}
