import type { FixedAssetDimension } from '../../../lib/api'
import SalesSideDrawer from '../../sales/SalesSideDrawer'
import { DIMENSION_OPTIONS, saveCategoryDimension } from './fixedAssetsChartRegistry'

type Props = {
  open?: boolean
  onClose?: () => void
  embedded?: boolean
  dimension: FixedAssetDimension
  onChange: (d: FixedAssetDimension) => void
}

export default function FixedAssetsCategoryChartEditor({
  open = false,
  onClose,
  embedded,
  dimension,
  onChange,
}: Props) {
  function setDim(d: FixedAssetDimension) {
    onChange(d)
    saveCategoryDimension(d)
  }

  const body = (
    <>
      <div className="px-4 py-3 border-b border-slate-100">
        <h3 className="text-sm font-semibold text-slate-900">NBV by category settings</h3>
        <p className="text-xs text-slate-500 mt-0.5">Group closing NBV by dimension</p>
      </div>
      <div className="p-4">
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <span className="w-20 text-xs text-slate-500">By</span>
          <select
            className="flex-1 text-sm border border-slate-200 rounded-md px-2 py-1.5 bg-white"
            value={dimension}
            onChange={e => setDim(e.target.value as FixedAssetDimension)}
          >
            {DIMENSION_OPTIONS.map(o => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </select>
        </label>
        <p className="mt-3 text-xs text-slate-500">
          Anchor and prior year are taken from the period filter.
        </p>
      </div>
    </>
  )

  if (embedded) return body
  return (
    <SalesSideDrawer open={open} onClose={onClose ?? (() => {})}>
      {body}
    </SalesSideDrawer>
  )
}
