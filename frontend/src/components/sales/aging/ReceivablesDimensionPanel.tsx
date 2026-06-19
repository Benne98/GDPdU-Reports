import { useMemo, useState } from 'react'
import type { ReceivablesHierarchyBreakdownRow } from '../../../lib/api'
import { exportToXlsx, todayStr } from '../../../lib/exportXlsx'
import PlExportMenu from '../../financials/pl-two-view/PlExportMenu'
import ReceivablesDimensionBreakdownEditor from './ReceivablesDimensionBreakdownEditor'
import ReceivablesDimensionHierarchyTable from './ReceivablesDimensionHierarchyTable'
import TableFilterToolbarButton from '../operational/TableFilterToolbarButton'
import {
  receivablesBreakdownTitle,
  type ReceivablesDimensionBreakdownConfig,
} from './shared/receivablesDimensionBreakdownConfig'
import {
  buildReceivablesHierarchy,
  flattenVisibleHierarchyNodes,
} from './shared/receivablesDimensionHierarchy'

const TABLE_BODY_PAD = 'p-4'

export default function ReceivablesDimensionPanel({
  config,
  onConfigChange,
  rows,
  loading,
  error,
}: {
  config: ReceivablesDimensionBreakdownConfig
  onConfigChange: (cfg: ReceivablesDimensionBreakdownConfig) => void
  rows: ReceivablesHierarchyBreakdownRow[]
  loading?: boolean
  error?: string | null
}) {
  const [filtersEnabled, setFiltersEnabled] = useState(false)

  const title = useMemo(() => receivablesBreakdownTitle(config.hierarchy), [config.hierarchy])
  const subtitle =
    config.view === 'buckets' ? 'Aging buckets · kEUR' : 'Due / overdue · kEUR'

  async function handleExport(kind: 'xlsx' | 'pptx' | 'pdf') {
    if (kind !== 'xlsx' || !rows.length) return
    const tree = buildReceivablesHierarchy(rows, config.hierarchy, config.view)
    const expanded = new Set<string>()
    for (const n of tree) expanded.add(n.key)
    const flat = flattenVisibleHierarchyNodes(tree, expanded)
    const headers = ['Breakdown', ...config.columns.map(c => c.label)]
    const exportRows = flat.map(node => ({
      label: node.label,
      values: config.columns.map(c => {
        const v = node.metrics[c.field]
        if (v == null) return null
        return c.isPct ? +Number(v).toFixed(1) : v
      }),
      kind: 'data' as const,
      indent: node.level,
    }))
    await exportToXlsx({
      title: `Breakdown by ${title}`,
      subtitle,
      headers,
      rows: exportRows,
      filename: `receivables-dimension-breakdown_${todayStr()}.xlsx`,
    })
  }

  return (
    <div
      className="rounded-xl flex flex-col"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
    >
      <div className="px-5 pt-4 pb-3 border-b flex items-center gap-2 flex-wrap" style={{ borderColor: '#F1F5F9' }}>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>
            Breakdown by {title}
          </h3>
          <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>{subtitle}</p>
          {error && (
            <p className="text-xs mt-1" style={{ color: '#DC2626' }}>
              {error.includes('404')
                ? 'Breakdown API not available — restart the backend (finssentials-api) to load the latest routes.'
                : error}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 ml-auto shrink-0">
          <TableFilterToolbarButton
            active={filtersEnabled}
            onClick={() => setFiltersEnabled(v => !v)}
            disabled={loading}
          />
          <ReceivablesDimensionBreakdownEditor
            config={config}
            onChange={onConfigChange}
            disabled={loading}
          />
          <PlExportMenu formats={['xlsx']} onExport={handleExport} disabled={loading || rows.length === 0} />
        </div>
      </div>

      <div className={TABLE_BODY_PAD}>
        <ReceivablesDimensionHierarchyTable
          rows={rows}
          hierarchy={config.hierarchy}
          view={config.view}
          columns={config.columns}
          filtersEnabled={filtersEnabled}
          loading={loading}
        />
      </div>
    </div>
  )
}
