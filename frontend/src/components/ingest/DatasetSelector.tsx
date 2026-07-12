/**
 * DatasetSelector.tsx — tab strip for the Additional Information step.
 *
 * Renders the three additional datasets (FTE, Anlagen, OPOS) as a horizontal
 * tab strip. Clicking a tab enables the dataset (if not already) and opens its
 * provisioning panel. A small × on an enabled tab disables it.
 *
 * The downstream additionalDatasets.{fte,anlagen,opos} booleans and per-dataset
 * .provided flags are unchanged — all existing consumers are unaffected.
 */

export type AdditionalDataset = 'fte' | 'anlagen' | 'opos'

interface DatasetSelectorProps {
  selection: { fte: boolean; anlagen: boolean; opos: boolean }
  onToggle: (dataset: AdditionalDataset, value: boolean) => void
  activeDataset: AdditionalDataset | null
  onOpen: (key: AdditionalDataset) => void
  providedMap: { fte: boolean; anlagen: boolean; opos: boolean }
}

const DATASET_OPTIONS: Array<{
  key: AdditionalDataset
  title: string
  shortTitle: string
}> = [
  { key: 'fte',    title: 'Headcount and Payroll (FTE)', shortTitle: 'FTE' },
  { key: 'anlagen', title: 'Fixed-Asset Register',        shortTitle: 'Fixed Assets' },
  { key: 'opos',   title: 'Open-Items Lists (OPOS)',      shortTitle: 'OPOS' },
]

export default function DatasetSelector({
  selection,
  onToggle,
  activeDataset,
  onOpen,
  providedMap,
}: DatasetSelectorProps) {
  const noneEnabled = !selection.fte && !selection.anlagen && !selection.opos

  return (
    <div className="space-y-1">
      <p className="text-sm font-medium text-slate-700 mb-2">Additional datasets</p>

      {/* Tab strip */}
      <div className="flex items-end border-b border-slate-200">
        {DATASET_OPTIONS.map(({ key, title, shortTitle }) => {
          const enabled  = selection[key]
          const provided = providedMap[key]
          const isActive = activeDataset === key

          return (
            <div
              key={key}
              role="tab"
              aria-selected={isActive}
              className={[
                'relative flex items-center gap-1.5 px-4 py-2.5 cursor-pointer select-none',
                'text-sm transition-colors border-b-2 -mb-px whitespace-nowrap',
                isActive
                  ? 'border-[#1E3A5F] text-[#1E3A5F] font-semibold'
                  : enabled
                    ? 'border-transparent text-slate-700 hover:text-slate-900 hover:border-slate-300'
                    : 'border-transparent text-slate-400 hover:text-slate-600 hover:border-slate-200',
              ].join(' ')}
              title={title}
              onClick={() => {
                if (!enabled) onToggle(key, true)
                onOpen(key)
              }}
            >
              {/* Green check when provisioning is complete */}
              {provided && (
                <span className="text-emerald-500 shrink-0" aria-label="Provided">
                  <svg
                    className="h-3.5 w-3.5"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={3}
                    aria-hidden="true"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                </span>
              )}

              {/* Enabled-but-not-provided indicator dot */}
              {enabled && !provided && (
                <span
                  className="inline-block h-2 w-2 rounded-full bg-blue-400 shrink-0"
                  aria-label="Enabled"
                />
              )}

              <span>{shortTitle}</span>

              {/* Remove button — only on enabled tabs */}
              {enabled && (
                <button
                  type="button"
                  className={[
                    'ml-1 flex h-4 w-4 items-center justify-center rounded-full text-[10px]',
                    'text-slate-400 hover:bg-red-100 hover:text-red-600 transition-colors',
                  ].join(' ')}
                  title={`Remove ${shortTitle}`}
                  onClick={e => {
                    e.stopPropagation()
                    onToggle(key, false)
                  }}
                  aria-label={`Remove ${shortTitle}`}
                >
                  &#x2715;
                </button>
              )}
            </div>
          )
        })}
      </div>

      {noneEnabled && (
        <p className="text-xs text-slate-400 pt-2">
          Click a tab to add an optional dataset. All datasets are optional — skip by clicking Next.
        </p>
      )}
    </div>
  )
}
