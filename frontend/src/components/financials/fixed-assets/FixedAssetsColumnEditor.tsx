import { Pencil } from 'lucide-react'

import { useMemo, useState } from 'react'

import type { FixedAssetDimension, FixedAssetSnapshotInfo } from '../../../lib/api'

import {

  PL_TOOLBAR_BTN,

  PL_TOOLBAR_BTN_STYLE,

} from '../../financials/pl-two-view/plToolbarButton'

import SalesSideDrawer from '../../sales/SalesSideDrawer'

import type { FixedAssetsColumnDef } from './fixedAssetsColumnRegistry'

import {

  DIMENSION_OPTIONS,

  saveDimensions,

  saveFixedAssetsColumns,

} from './fixedAssetsColumnRegistry'



type Props = {

  columns: FixedAssetsColumnDef[]

  dimensions: FixedAssetDimension[]

  snapshots: FixedAssetSnapshotInfo[]

  onColumnsChange: (cols: FixedAssetsColumnDef[]) => void

  onDimensionsChange: (dims: FixedAssetDimension[]) => void

  disabled?: boolean

}



const LEVEL_LABELS = ['Level 1', 'Level 2', 'Level 3'] as const



export default function FixedAssetsColumnEditor({

  columns,

  dimensions,

  snapshots,

  onColumnsChange,

  onDimensionsChange,

  disabled,

}: Props) {

  const [open, setOpen] = useState(false)



  const snapshotYears = useMemo(() => {

    const years = new Set<number>()

    for (const s of snapshots) {

      years.add(parseInt(s.as_of_date.slice(0, 4), 10))

    }

    return [...years].sort((a, b) => a - b)

  }, [snapshots])



  function toggleYear(id: string) {

    const next = columns.map(c => (c.id === id ? { ...c, visible: !c.visible } : c))

    onColumnsChange(next)

    saveFixedAssetsColumns(next)

  }



  function addYear(year: number) {

    if (columns.some(c => c.year === year)) return

    const next = [

      ...columns,

      {

        id: `year-${year}`,

        kind: 'year' as const,

        label: `Dec${String(year).slice(-2)}A`,

        year,

        visible: true,

      },

    ].sort((a, b) => (a.year ?? 0) - (b.year ?? 0))

    onColumnsChange(next)

    saveFixedAssetsColumns(next)

  }



  function setLevelDim(level: number, dim: FixedAssetDimension) {

    const next = [...dimensions]

    while (next.length <= level) next.push('segment')

    next[level] = dim

    const trimmed = next.slice(0, 3)

    onDimensionsChange(trimmed)

    saveDimensions(trimmed)

  }



  function setLevelCount(n: number) {

    const base: FixedAssetDimension[] = ['bilanzposition', 'segment', 'asset']

    const next = base.slice(0, Math.max(1, Math.min(3, n)))

    onDimensionsChange(next)

    saveDimensions(next)

  }



  const levelCount = dimensions.length



  return (

    <>

      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className={`${PL_TOOLBAR_BTN} px-2`}
        style={{
          ...PL_TOOLBAR_BTN_STYLE,
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.5 : 1,
        }}
        title="Edit hierarchy and rollforward years"
      >
        <Pencil size={12} strokeWidth={1.75} className="shrink-0" />
        <span>Columns</span>
      </button>

      <SalesSideDrawer open={open} onClose={() => setOpen(false)}>

        <div className="px-4 py-3 border-b border-slate-100">

          <h3 className="text-sm font-semibold text-slate-900">Fixed assets settings</h3>

          <p className="text-xs text-slate-500 mt-0.5">Table hierarchy and rollforward years</p>

        </div>

        <div className="p-4 space-y-5 overflow-y-auto max-h-[calc(100vh-8rem)]">

          <div>

            <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">Table hierarchy</p>

            <div className="flex items-center gap-2 mb-3">

              <span className="text-xs text-slate-500">Levels</span>

              {[1, 2, 3].map(n => (

                <button

                  key={n}

                  type="button"

                  onClick={() => setLevelCount(n)}

                  className="h-7 px-2.5 rounded-lg text-xs font-medium border"

                  style={{

                    background: levelCount === n ? '#1E3A5F' : '#F4F6F9',

                    color: levelCount === n ? '#fff' : '#1E3A5F',

                    borderColor: '#E2E8F0',

                  }}

                >

                  {n}

                </button>

              ))}

            </div>

            <div className="space-y-2">

              {LEVEL_LABELS.slice(0, levelCount).map((lbl, i) => (

                <label key={lbl} className="flex items-center gap-2 text-sm text-slate-700">

                  <span className="w-14 text-xs text-slate-500">{lbl}</span>

                  <select

                    className="flex-1 text-sm border border-slate-200 rounded-md px-2 py-1.5 bg-white"

                    value={dimensions[i] ?? 'bilanzposition'}

                    onChange={e => setLevelDim(i, e.target.value as FixedAssetDimension)}

                  >

                    {DIMENSION_OPTIONS.map(o => (

                      <option key={o.id} value={o.id}>{o.label}</option>

                    ))}

                  </select>

                </label>

              ))}

            </div>

          </div>



          <div>

            <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">Rollforward years</p>

            <div className="space-y-2">

              {columns.map(col => (

                <label key={col.id} className="flex items-center gap-2 text-sm text-slate-700">

                  <input type="checkbox" checked={col.visible} onChange={() => toggleYear(col.id)} />

                  <span>{col.label}</span>

                </label>

              ))}

            </div>

            {snapshotYears.some(y => !columns.some(c => c.year === y)) && (

              <div className="mt-3 flex flex-wrap gap-1.5">

                {snapshotYears

                  .filter(y => !columns.some(c => c.year === y))

                  .map(y => (

                    <button

                      key={y}

                      type="button"

                      onClick={() => addYear(y)}

                      className="text-xs px-2 py-1 rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50"

                    >

                      + Dec{String(y).slice(-2)}A

                    </button>

                  ))}

              </div>

            )}

          </div>

        </div>

      </SalesSideDrawer>

    </>

  )

}

