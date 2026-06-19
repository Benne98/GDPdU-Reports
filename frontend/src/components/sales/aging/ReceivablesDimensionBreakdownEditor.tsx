import { useState, type ReactNode } from 'react'
import { ChevronDown, ChevronUp, Pencil, X } from 'lucide-react'
import SalesSideDrawer from '../SalesSideDrawer'
import { PL_TOOLBAR_BTN_STYLE, PL_TOOLBAR_ICON_BTN } from '../../financials/pl-two-view/plToolbarButton'
import type { ReceivablesDimensionView } from '../../../lib/api'
import {
  buildColumnCatalog,
  canAddHierarchyDim,
  DEFAULT_RECEIVABLES_BREAKDOWN_CONFIG,
  hierarchyDimLabel,
  metricEnabled,
  metricHasSubColumn,
  metricsForView,
  moveBreakdownColumn,
  RECEIVABLES_HIERARCHY_DIM_OPTIONS,
  saveReceivablesBreakdownConfig,
  SUB_OPTIONS_UI,
  toggleMetricBlock,
  toggleMetricSubColumn,
  type AgingBreakdownColumnDef,
  type AgingBreakdownSub,
  type ReceivablesDimensionBreakdownConfig,
  type ReceivablesHierarchyDim,
} from './shared/receivablesDimensionBreakdownConfig'

type Props = {
  config: ReceivablesDimensionBreakdownConfig
  onChange: (cfg: ReceivablesDimensionBreakdownConfig) => void
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
      {open && <div className="p-3 space-y-2">{children}</div>}
    </div>
  )
}

export default function ReceivablesDimensionBreakdownEditor({ config, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false)
  const catalog = buildColumnCatalog(config.view)
  const metricGroups = metricsForView(config.view)

  function persist(next: ReceivablesDimensionBreakdownConfig) {
    onChange(next)
    saveReceivablesBreakdownConfig(next)
  }

  function patch(partial: Partial<ReceivablesDimensionBreakdownConfig>) {
    persist({ ...config, ...partial })
  }

  function setView(view: ReceivablesDimensionView) {
    const nextCatalog = buildColumnCatalog(view)
    const defaultCols = config.columns
      .map(c => nextCatalog.find(n => n.metric === c.metric && n.sub === 'cm'))
      .filter((c): c is AgingBreakdownColumnDef => !!c)
    persist({
      ...config,
      view,
      columns: defaultCols.length ? defaultCols : buildColumnCatalog(view).filter(c => c.sub === 'cm' && c.metric === 'total'),
    })
  }

  function moveHierarchyDim(dim: ReceivablesHierarchyDim, dir: -1 | 1) {
    const idx = config.hierarchy.indexOf(dim)
    if (idx < 0) return
    const next = [...config.hierarchy]
    const swap = idx + dir
    if (swap < 0 || swap >= next.length) return
    if (next[swap] === 'invoice_number' && dir === -1) return
    if (dim === 'invoice_number' && dir === 1) return
    ;[next[idx], next[swap]] = [next[swap], next[idx]]
    patch({ hierarchy: next })
  }

  function addHierarchyDim(dim: ReceivablesHierarchyDim) {
    if (!canAddHierarchyDim(dim, config.hierarchy)) return
    patch({ hierarchy: [...config.hierarchy, dim] })
  }

  function removeHierarchyDim(dim: ReceivablesHierarchyDim) {
    patch({ hierarchy: config.hierarchy.filter(d => d !== dim) })
  }

  function resetAll() {
    persist({ ...DEFAULT_RECEIVABLES_BREAKDOWN_CONFIG })
  }

  const availableDims = RECEIVABLES_HIERARCHY_DIM_OPTIONS.filter(d =>
    canAddHierarchyDim(d.id, config.hierarchy),
  )

  return (
    <>
      <button
        type="button"
        title="Table builder — hierarchy, columns, and comparisons"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className={PL_TOOLBAR_ICON_BTN}
        style={{ ...PL_TOOLBAR_BTN_STYLE, opacity: disabled ? 0.5 : 1 }}
      >
        <Pencil size={14} strokeWidth={1.75} />
      </button>
      <SalesSideDrawer open={open} onClose={() => setOpen(false)}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
          <div>
            <p className="text-sm font-semibold text-slate-900">Table builder</p>
            <p className="text-[0.65rem] text-slate-500 mt-0.5">
              Row hierarchy, metric columns, and prior-period comparisons
            </p>
          </div>
          <button type="button" onClick={() => setOpen(false)} className="p-1 rounded hover:bg-slate-100" aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-4" data-drawer-scroll>
          <Section title="Row hierarchy">
            <p className="text-[0.65rem] text-slate-500 mb-2">
              Build expandable rows top-down (e.g. Entity → Segment → Customer). Invoice number must be the last level.
            </p>
            {config.hierarchy.map((dim, i) => (
              <div key={dim} className="flex items-center gap-1 text-xs text-slate-700 py-0.5">
                <span className="flex-1 font-medium">
                  {i + 1}. {hierarchyDimLabel(dim)}
                </span>
                <button
                  type="button"
                  className="p-0.5 rounded hover:bg-slate-100 disabled:opacity-30"
                  disabled={i === 0 || dim === 'invoice_number'}
                  onClick={() => moveHierarchyDim(dim, -1)}
                  aria-label="Move up"
                >
                  <ChevronUp size={14} />
                </button>
                <button
                  type="button"
                  className="p-0.5 rounded hover:bg-slate-100 disabled:opacity-30"
                  disabled={i === config.hierarchy.length - 1 || config.hierarchy[i + 1] === 'invoice_number'}
                  onClick={() => moveHierarchyDim(dim, 1)}
                  aria-label="Move down"
                >
                  <ChevronDown size={14} />
                </button>
                <button
                  type="button"
                  className="text-[0.65rem] text-slate-500 hover:text-red-600 px-1"
                  onClick={() => removeHierarchyDim(dim)}
                >
                  Remove
                </button>
              </div>
            ))}
            {availableDims.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-2">
                {availableDims.map(d => (
                  <button
                    key={d.id}
                    type="button"
                    className="text-[0.65rem] px-2 py-1 rounded-full border border-slate-200 text-slate-600 hover:bg-slate-50"
                    onClick={() => addHierarchyDim(d.id)}
                  >
                    + {d.label}
                  </button>
                ))}
              </div>
            )}
          </Section>

          <Section title="Aging view">
            <div className="flex gap-1 rounded-lg p-0.5" style={{ background: '#F1F5F9' }}>
              {(['buckets', 'due_overdue'] as const).map(v => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setView(v)}
                  className="flex-1 rounded-md px-2.5 py-1.5 text-[10px] font-semibold"
                  style={{
                    background: config.view === v ? '#fff' : 'transparent',
                    color: config.view === v ? '#1E3A5F' : '#64748B',
                    boxShadow: config.view === v ? '0 1px 2px rgba(0,0,0,0.06)' : 'none',
                  }}
                >
                  {v === 'buckets' ? 'Aging buckets' : 'Due / overdue'}
                </button>
              ))}
            </div>
          </Section>

          <Section title="Columns" defaultOpen={false}>
            <p className="text-[0.65rem] text-slate-500 mb-3">
              Enable metrics and add prior-month, prior-year, or delta columns per metric.
            </p>
            <div className="space-y-3">
              {metricGroups.map(g => {
                const blockOn = metricEnabled(config.columns, g.metric)
                return (
                  <div
                    key={g.metric}
                    className="rounded-lg border border-slate-200 overflow-hidden"
                    style={{ background: blockOn ? '#FAFBFC' : '#fff' }}
                  >
                    <label className="flex items-center gap-2 px-3 py-2 text-xs font-semibold text-slate-800 cursor-pointer hover:bg-slate-50">
                      <input
                        type="checkbox"
                        checked={blockOn}
                        onChange={e =>
                          patch({
                            columns: toggleMetricBlock(config.columns, catalog, g.metric, e.target.checked),
                          })
                        }
                      />
                      {g.label}
                    </label>
                    {blockOn && (
                      <div className="px-3 pb-3 pt-1 space-y-1.5 border-t border-slate-100">
                        {SUB_OPTIONS_UI.map(opt => {
                          const checked = metricHasSubColumn(config.columns, g.metric, opt.sub)
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
                                  patch({
                                    columns: toggleMetricSubColumn(
                                      config.columns,
                                      catalog,
                                      g.metric,
                                      opt.sub as AgingBreakdownSub,
                                      e.target.checked,
                                    ),
                                  })
                                }
                              />
                              <span>
                                <span className="font-medium">{opt.title}</span>
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

          {config.columns.length > 0 && (
            <Section title="Column order" defaultOpen={false}>
              <p className="text-[0.65rem] text-slate-500 mb-2">
                Reorder visible columns left-to-right in the table.
              </p>
              {config.columns.map((col, i) => (
                <div key={col.id} className="flex items-center gap-1 text-xs text-slate-700 py-0.5">
                  <span className="flex-1 font-medium truncate" title={col.label}>
                    {i + 1}. {col.label}
                  </span>
                  <button
                    type="button"
                    className="p-0.5 rounded hover:bg-slate-100 disabled:opacity-30"
                    disabled={i === 0}
                    onClick={() => patch({ columns: moveBreakdownColumn(config.columns, col.id, -1) })}
                    aria-label="Move column left"
                  >
                    <ChevronUp size={14} />
                  </button>
                  <button
                    type="button"
                    className="p-0.5 rounded hover:bg-slate-100 disabled:opacity-30"
                    disabled={i === config.columns.length - 1}
                    onClick={() => patch({ columns: moveBreakdownColumn(config.columns, col.id, 1) })}
                    aria-label="Move column right"
                  >
                    <ChevronDown size={14} />
                  </button>
                </div>
              ))}
            </Section>
          )}
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
