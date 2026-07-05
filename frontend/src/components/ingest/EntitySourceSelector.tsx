/**
 * EntitySourceSelector.tsx — Shared two-radio-card selector for entity source mode.
 *
 * Used by StepOpeningBalances and StepPartnerMaster in the Project Setup Wizard
 * to let the user choose between a single combined file for all entities or one
 * file per legal entity.
 */

export type EntitySource = 'combined' | 'per_entity'

interface EntitySourceSelectorProps {
  value: EntitySource
  onChange: (v: EntitySource) => void
  /** Short data-type label, e.g. "opening balances" or "partner masters". */
  dataLabel: string
  disabled?: boolean
}

export default function EntitySourceSelector({
  value,
  onChange,
  dataLabel,
  disabled,
}: EntitySourceSelectorProps) {
  const cards: Array<{ v: EntitySource; title: string; help: string }> = [
    {
      v: 'combined',
      title: 'One combined file for all entities',
      help: 'Upload a single file; the entity is read from a column you map below.',
    },
    {
      v: 'per_entity',
      title: 'One file per entity',
      help: 'Upload one file per legal entity; the entity prefix is applied automatically — no entity column needed.',
    },
  ]

  return (
    <div className="space-y-3">
      <p className="text-sm font-medium text-slate-700">
        How is the {dataLabel} data organized across entities?
      </p>
      <div className="flex flex-col gap-3 sm:flex-row">
        {cards.map(({ v, title, help }) => (
          <label
            key={v}
            className={[
              'flex-1 flex items-start gap-3 rounded-lg border p-4 transition',
              disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
              value === v
                ? 'border-blue-400 bg-blue-50'
                : 'border-slate-200 bg-white hover:bg-slate-50',
            ].join(' ')}
          >
            <input
              type="radio"
              name="entitySource"
              value={v}
              checked={value === v}
              onChange={() => { if (!disabled) onChange(v) }}
              disabled={disabled}
              className="mt-0.5 accent-blue-600"
            />
            <div>
              <p className="text-sm font-semibold text-slate-800">{title}</p>
              <p className="text-xs text-slate-500 mt-0.5">{help}</p>
            </div>
          </label>
        ))}
      </div>
    </div>
  )
}
