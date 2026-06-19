import { useMemo, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronUp, GripVertical, Pencil, X } from 'lucide-react'
import {
  PL_TOOLBAR_BTN,
  PL_TOOLBAR_BTN_STYLE,
  PL_TOOLBAR_ICON_BTN,
} from '../../financials/pl-two-view/plToolbarButton'
import SalesSideDrawer from '../SalesSideDrawer'
import type { SalesColumnDef } from './salesTableTypes'
import {
  normalizeEntityColumns,
  saveSalesColumns,
  type SalesColumnGroup,
} from './salesColumnRegistry'

type Props = {
  tableId: string
  catalog: SalesColumnDef[]
  columns: SalesColumnDef[]
  onChange: (cols: SalesColumnDef[]) => void
  groups?: SalesColumnGroup[]
  disabled?: boolean
}

function Section({ title, children, defaultOpen = true }: { title: string; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-semibold text-slate-700 bg-slate-50 hover:bg-slate-100"
      >
        {title}
        <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div className="p-3">{children}</div>}
    </div>
  )
}

export default function SalesColumnEditor({
  tableId,
  catalog,
  columns,
  onChange,
  groups = [],
  disabled,
}: Props) {
  const [open, setOpen] = useState(false)
  const [dragIdx, setDragIdx] = useState<number | null>(null)

  const activeIds = useMemo(() => new Set(columns.map(c => c.id)), [columns])

  function persist(next: SalesColumnDef[]) {
    const normalized = normalizeEntityColumns(next, catalog)
    onChange(normalized)
    saveSalesColumns(tableId, normalized)
  }

  function move(idx: number, dir: -1 | 1) {
    const next = idx + dir
    if (next < 0 || next >= columns.length) return
    const copy = [...columns]
    const target = copy[idx]
    if (target.field === 'name') return
    const swap = copy[next]
    if (swap.field === 'name') return
    ;[copy[idx], copy[next]] = [copy[next], copy[idx]]
    persist(copy)
  }

  function removeAt(idx: number) {
    const col = columns[idx]
    if (col.field === 'name') return
    if (columns.length <= 2) return
    persist(columns.filter((_, i) => i !== idx))
  }

  function addColumn(col: SalesColumnDef) {
    if (activeIds.has(col.id)) return
    persist([...columns, col])
  }

  function onDragDrop(targetIdx: number) {
    if (dragIdx == null || dragIdx === targetIdx) return
    if (columns[dragIdx]?.field === 'name' || columns[targetIdx]?.field === 'name') return
    const next = [...columns]
    const [item] = next.splice(dragIdx, 1)
    next.splice(targetIdx, 0, item)
    setDragIdx(null)
    persist(next)
  }

  return (
    <>
      <button
        type="button"
        title="Table builder — choose and reorder columns"
        disabled={disabled}
        onClick={() => setOpen(v => !v)}
        className={PL_TOOLBAR_ICON_BTN}
        style={{ ...PL_TOOLBAR_BTN_STYLE, opacity: disabled ? 0.5 : 1 }}
      >
        <Pencil size={14} strokeWidth={1.75} />
      </button>
      <SalesSideDrawer open={open} onClose={() => setOpen(false)}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
              <div>
                <p className="text-sm font-semibold text-slate-900">Table builder</p>
                <p className="text-[0.65rem] text-slate-500 mt-0.5">Add, remove, and reorder columns</p>
              </div>
              <button type="button" onClick={() => setOpen(false)} className="p-1 rounded hover:bg-slate-100" aria-label="Close">
                <X size={18} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4" data-drawer-scroll>
              <section>
                <p className="text-xs font-semibold text-slate-700 mb-2">Column order ({columns.length})</p>
                <ul className="space-y-1">
                  {columns.map((col, idx) => (
                    <li
                      key={col.id}
                      draggable={col.field !== 'name'}
                      onDragStart={() => col.field !== 'name' && setDragIdx(idx)}
                      onDragOver={e => e.preventDefault()}
                      onDrop={() => onDragDrop(idx)}
                      onDragEnd={() => setDragIdx(null)}
                      className={`flex items-center gap-1 rounded-lg border px-2 py-1.5 bg-white ${
                        dragIdx === idx ? 'border-[#1E3A5F] ring-1 ring-[#1E3A5F]/20' : 'border-slate-200'
                      }`}
                    >
                      {col.field !== 'name' ? (
                        <GripVertical size={14} className="shrink-0 text-slate-400 cursor-grab" />
                      ) : (
                        <span className="w-3.5 shrink-0" />
                      )}
                      <span className="flex-1 truncate text-xs font-medium text-slate-800">{col.label}</span>
                      {col.field !== 'name' && (
                        <div className="flex shrink-0">
                          <button type="button" onClick={() => move(idx, -1)} disabled={idx <= 1} className="p-1 text-slate-500 disabled:opacity-30">
                            <ChevronUp size={14} />
                          </button>
                          <button type="button" onClick={() => move(idx, 1)} disabled={idx === columns.length - 1} className="p-1 text-slate-500 disabled:opacity-30">
                            <ChevronDown size={14} />
                          </button>
                          <button type="button" onClick={() => removeAt(idx)} className="p-1 text-slate-500 hover:text-rose-600">
                            <X size={14} />
                          </button>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </section>

              {groups.map(group => (
                <Section key={group.title} title={group.title} defaultOpen={group.title.includes('Plan')}>
                  <div className="flex flex-wrap gap-1.5">
                    {group.fieldIds.map(fid => {
                      const def = catalog.find(c => c.field === fid)
                      if (!def) return null
                      const active = activeIds.has(def.id)
                      return (
                        <button
                          key={def.id}
                          type="button"
                          disabled={active}
                          onClick={() => addColumn(def)}
                          className="px-2 py-1.5 rounded-md text-[0.7rem] disabled:opacity-40 hover:bg-slate-50"
                          style={{ border: '1px solid #E2E8F0', color: '#475569' }}
                        >
                          {def.label}
                        </button>
                      )
                    })}
                  </div>
                </Section>
              ))}

              {groups.length === 0 && (
                <Section title="Add columns">
                  <div className="flex flex-wrap gap-1.5">
                    {catalog.filter(c => c.field !== 'name').map(c => {
                      const active = activeIds.has(c.id)
                      return (
                        <button
                          key={c.id}
                          type="button"
                          disabled={active}
                          onClick={() => addColumn(c)}
                          className="px-2 py-1.5 rounded-md text-[0.7rem] disabled:opacity-40"
                          style={{ border: '1px solid #E2E8F0', color: '#475569' }}
                        >
                          {c.label}
                        </button>
                      )
                    })}
                  </div>
                </Section>
              )}

              <button
                type="button"
                onClick={() => persist(catalog)}
                className={`${PL_TOOLBAR_BTN} w-full text-xs`}
                style={PL_TOOLBAR_BTN_STYLE}
              >
                Reset to default layout
              </button>
            </div>
      </SalesSideDrawer>
    </>
  )
}
