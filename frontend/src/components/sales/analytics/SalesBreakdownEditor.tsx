import { useState, type ReactNode } from 'react'
import { ChevronDown, Pencil, X } from 'lucide-react'
import SalesSideDrawer from '../SalesSideDrawer'
import {
  PL_TOOLBAR_BTN_STYLE,
  PL_TOOLBAR_ICON_BTN,
} from '../../financials/pl-two-view/plToolbarButton'
import type {
  BreakdownColumnDef,
  BreakdownDimConfig,
  BreakdownMetricBlock,
  BreakdownMiscConfig,
  BreakdownSubColumn,
} from './salesBreakdownRegistry'
import {
  BREAKDOWN_DIM_BOTTOM_OPTIONS,
  BREAKDOWN_DIM_OPTIONS,
  saveBreakdownColumns,
  saveBreakdownDims,
  saveBreakdownMisc,
  buildBreakdownColumnCatalog,
  blockHasColumn,
  blockHasSubColumn,
  DEFAULT_BREAKDOWN_DIMS,
  defaultBreakdownColumns,
  defaultBreakdownMisc,
  isCustomerBottomDim,
  toggleBreakdownBlock,
  toggleBreakdownSubColumn,
} from './salesBreakdownRegistry'

type Props = {
  dims: BreakdownDimConfig
  misc: BreakdownMiscConfig
  columns: BreakdownColumnDef[]
  colLabels: { pm: string; cm: string; plan_cm: string; delta_cm_pm?: string }
  onDimsChange: (dims: BreakdownDimConfig) => void
  onMiscChange: (misc: BreakdownMiscConfig) => void
  onColumnsChange: (cols: BreakdownColumnDef[]) => void
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
      {open && <div className="p-3 space-y-3">{children}</div>}
    </div>
  )
}

const BLOCK_GROUPS: { id: BreakdownMetricBlock; label: string }[] = [
  { id: 'gross_sales', label: 'Gross Sales (GS)' },
  { id: 'gross_profit', label: 'Gross Profit (GP)' },
  { id: 'gross_margin', label: 'Gross Margin (GM)' },
]

const SUB_OPTIONS: { sub: BreakdownSubColumn; title: string }[] = [
  { sub: 'pm', title: 'Prior month' },
  { sub: 'cm', title: 'Current month' },
  { sub: 'plan', title: 'Plan' },
  { sub: 'delta', title: 'Delta (CM − PM)' },
]

function subColumnLabel(
  sub: BreakdownSubColumn,
  colLabels: { pm: string; cm: string; plan_cm: string; delta_cm_pm?: string },
): string {
  if (sub === 'pm') return colLabels.pm
  if (sub === 'cm') return colLabels.cm
  if (sub === 'plan') return colLabels.plan_cm
  return colLabels.delta_cm_pm ?? `${colLabels.cm} − ${colLabels.pm}`
}

export default function SalesBreakdownEditor({
  dims,
  misc,
  columns,
  colLabels,
  onDimsChange,
  onMiscChange,
  onColumnsChange,
  disabled,
}: Props) {
  const [open, setOpen] = useState(false)
  const catalog = buildBreakdownColumnCatalog(colLabels)

  function persistDims(next: BreakdownDimConfig) {
    onDimsChange(next)
    saveBreakdownDims(next)
    if (next.dim_bottom !== dims.dim_bottom) {
      const nextMisc = { ...misc }
      if (!isCustomerBottomDim(next.dim_bottom)) {
        nextMisc.l3_enabled = false
      } else if (!isCustomerBottomDim(dims.dim_bottom)) {
        nextMisc.l3_enabled = true
        nextMisc.l3_limit = 10
      }
      persistMisc(nextMisc)
    }
  }

  function persistMisc(next: BreakdownMiscConfig) {
    onMiscChange(next)
    saveBreakdownMisc(next)
  }

  function persistColumns(next: BreakdownColumnDef[]) {
    onColumnsChange(next)
    saveBreakdownColumns(next)
  }

  function resetAll() {
    const dimsNext = { ...DEFAULT_BREAKDOWN_DIMS }
    persistDims(dimsNext)
    persistMisc(defaultBreakdownMisc(dimsNext.dim_bottom))
    persistColumns(defaultBreakdownColumns(colLabels))
  }

  const customerBottom = isCustomerBottomDim(dims.dim_bottom)

  return (
    <>
      <button
        type="button"
        title="Table builder — dimensions and columns"
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
            <p className="text-[0.65rem] text-slate-500 mt-0.5">Hierarchy dimensions and metric columns</p>
          </div>
          <button type="button" onClick={() => setOpen(false)} className="p-1 rounded hover:bg-slate-100" aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-4" data-drawer-scroll>
          <Section title="Row dimensions">
            <label className="block text-[0.65rem] font-medium text-slate-600 mb-1">Top level</label>
            <select
              className="w-full text-xs border border-slate-200 rounded px-2 py-1.5 mb-3"
              value={dims.dim_top}
              onChange={e => persistDims({ ...dims, dim_top: e.target.value })}
            >
              {BREAKDOWN_DIM_OPTIONS.map(d => (
                <option key={d.key} value={d.key}>{d.label}</option>
              ))}
            </select>
            <label className="block text-[0.65rem] font-medium text-slate-600 mb-1">Middle level (optional)</label>
            <select
              className="w-full text-xs border border-slate-200 rounded px-2 py-1.5 mb-3"
              value={dims.dim_mid}
              onChange={e => persistDims({ ...dims, dim_mid: e.target.value })}
            >
              <option value="">— None (2 levels) —</option>
              {BREAKDOWN_DIM_OPTIONS.map(d => (
                <option key={d.key} value={d.key}>{d.label}</option>
              ))}
            </select>
            <label className="block text-[0.65rem] font-medium text-slate-600 mb-1">Bottom level</label>
            <select
              className="w-full text-xs border border-slate-200 rounded px-2 py-1.5"
              value={dims.dim_bottom}
              onChange={e => persistDims({ ...dims, dim_bottom: e.target.value })}
            >
              {BREAKDOWN_DIM_BOTTOM_OPTIONS.map(d => (
                <option key={d.key} value={d.key}>{d.label}</option>
              ))}
            </select>
          </Section>
          <Section title="Miscellaneous buckets" defaultOpen={false}>
            <p className="text-[0.65rem] text-slate-500 mb-3">
              Group remaining dimension values into a single &quot;Miscellaneous&quot; row on the middle and/or bottom level.
            </p>
            <label className="flex items-center gap-2 text-xs text-slate-700 py-1">
              <input
                type="checkbox"
                checked={misc.l2_enabled}
                onChange={e => persistMisc({ ...misc, l2_enabled: e.target.checked })}
              />
              Middle level — keep top
              <input
                type="number"
                min={1}
                max={99}
                className="w-12 border border-slate-200 rounded px-1 py-0.5 text-xs"
                value={misc.l2_limit}
                disabled={!misc.l2_enabled}
                onChange={e => persistMisc({ ...misc, l2_limit: Math.max(1, Number(e.target.value) || 8) })}
              />
              segments
            </label>
            <label className="flex items-center gap-2 text-xs text-slate-700 py-1">
              <input
                type="checkbox"
                checked={misc.l3_enabled}
                disabled={!customerBottom}
                onChange={e => persistMisc({ ...misc, l3_enabled: e.target.checked })}
              />
              Bottom level (customers) — keep top
              <input
                type="number"
                min={1}
                max={999}
                className="w-12 border border-slate-200 rounded px-1 py-0.5 text-xs"
                value={misc.l3_limit}
                disabled={!misc.l3_enabled || !customerBottom}
                onChange={e => persistMisc({ ...misc, l3_limit: Math.max(1, Number(e.target.value) || 10) })}
              />
              {customerBottom ? 'customers per segment' : '(enable End customer as bottom dim)'}
            </label>
          </Section>
          <Section title="Metric blocks">
            <p className="text-[0.65rem] text-slate-500 mb-3">
              Turn metric blocks on/off, then choose period columns per block (prior month → current month → plan → delta).
            </p>
            <div className="space-y-3">
              {BLOCK_GROUPS.map(g => {
                const blockOn = blockHasColumn(columns, g.id)
                return (
                  <div
                    key={g.id}
                    className="rounded-lg border border-slate-200 overflow-hidden"
                    style={{ background: blockOn ? '#FAFBFC' : '#fff' }}
                  >
                    <label className="flex items-center gap-2 px-3 py-2 text-xs font-semibold text-slate-800 cursor-pointer hover:bg-slate-50">
                      <input
                        type="checkbox"
                        checked={blockOn}
                        onChange={e => persistColumns(toggleBreakdownBlock(columns, catalog, g.id, e.target.checked))}
                      />
                      {g.label}
                    </label>
                    {blockOn && (
                      <div className="px-3 pb-3 pt-1 space-y-1.5 border-t border-slate-100">
                        {SUB_OPTIONS.map(opt => {
                          const checked = blockHasSubColumn(columns, g.id, opt.sub)
                          const periodLabel = subColumnLabel(opt.sub, colLabels)
                          return (
                            <label
                              key={opt.sub}
                              className="flex items-start gap-2 text-xs text-slate-700 py-0.5 cursor-pointer"
                            >
                              <input
                                type="checkbox"
                                className="mt-0.5"
                                checked={checked}
                                onChange={e =>
                                  persistColumns(
                                    toggleBreakdownSubColumn(columns, catalog, g.id, opt.sub, e.target.checked),
                                  )
                                }
                              />
                              <span>
                                <span className="font-medium">{opt.title}</span>
                                <span className="block text-[0.65rem] text-slate-500 tabular-nums">{periodLabel}</span>
                              </span>
                            </label>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </Section>
        </div>
        <div className="px-4 py-3 border-t border-slate-100 shrink-0 flex gap-2">
          <button
            type="button"
            onClick={resetAll}
            className="text-xs px-3 py-1.5 rounded border border-slate-200 text-slate-600 hover:bg-slate-50"
          >
            Reset defaults
          </button>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="text-xs px-3 py-1.5 rounded ml-auto text-white"
            style={{ background: '#1E3A5F' }}
          >
            Done
          </button>
        </div>
      </SalesSideDrawer>
    </>
  )
}
