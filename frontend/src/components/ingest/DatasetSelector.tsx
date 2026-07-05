/**
 * DatasetSelector.tsx — checkbox-card group for the Additional Information step.
 *
 * Lets the user tick which additional datasets they want to provide.
 * All datasets are optional. Unticking hides the corresponding provisioning panel.
 */

export type AdditionalDataset = 'fte' | 'anlagen' | 'opos'

interface DatasetSelectorProps {
  selection: { fte: boolean; anlagen: boolean; opos: boolean }
  onToggle: (dataset: AdditionalDataset, value: boolean) => void
}

const DATASET_OPTIONS: Array<{
  key: AdditionalDataset
  title: string
  description: string
  comingSoon?: boolean
}> = [
  {
    key: 'fte',
    title: 'Headcount and Payroll (FTE)',
    description:
      'Personnel data for building a formula-linked FTE Development workbook.',
  },
  {
    key: 'anlagen',
    title: 'Fixed-Asset Register',
    description:
      'Upload and map fixed-asset register data. Roll-forward and workbook generation are draft features.',
  },
  {
    key: 'opos',
    title: 'Open-Items Lists (OPOS)',
    description:
      'Upload and map open-items data for AR (Debitor) and AP (Kreditor). Aging analytics are a draft feature.',
  },
]

export default function DatasetSelector({ selection, onToggle }: DatasetSelectorProps) {
  return (
    <div className="space-y-3">
      <p className="text-sm font-medium text-slate-700">
        Which additional datasets do you want to provide?
      </p>
      <p className="text-xs text-slate-400">
        All datasets are optional. Tick to enable a dataset; untick to skip it entirely.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        {DATASET_OPTIONS.map(({ key, title, description, comingSoon }) => {
          const checked = selection[key]
          return (
            <label
              key={key}
              className={[
                'flex items-start gap-3 cursor-pointer rounded-lg border p-4 transition',
                checked
                  ? 'border-blue-400 bg-blue-50'
                  : 'border-slate-200 bg-white hover:bg-slate-50',
              ].join(' ')}
            >
              <input
                type="checkbox"
                className="mt-0.5 accent-blue-600 h-4 w-4 shrink-0"
                checked={checked}
                onChange={e => onToggle(key, e.target.checked)}
              />
              <div className="min-w-0">
                <p className="text-sm font-semibold text-slate-800">{title}</p>
                <p className="text-xs text-slate-500 mt-0.5">{description}</p>
                {comingSoon && (
                  <span className="mt-1.5 inline-block rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500">
                    Coming soon
                  </span>
                )}
              </div>
            </label>
          )
        })}
      </div>
    </div>
  )
}
