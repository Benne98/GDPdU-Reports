import { useEffect, useMemo, useState } from 'react'
import PlViewToggleButton from '../../../financials/pl-two-view/PlViewToggleButton'
import PlExportMenu, { type PlExportKind } from '../../../financials/pl-two-view/PlExportMenu'
import type {
  AgingPortfolioTableResponse,
  ReceivablesAgingBand,
  ReceivablesStatusSplit,
} from '../../../../lib/api'
import type { SalesViewMode } from '../../analytics/salesTableTypes'
import AgingBucketLeaderboard from './AgingBucketLeaderboard'
import AgingPortfolioBucketTable from './AgingPortfolioBucketTable'
import AgingPortfolioDimensionEditor from './AgingPortfolioDimensionEditor'
import AgingPortfolioMetricCards from './AgingPortfolioMetricCards'
import AgingPortfolioNarrativePanel from './AgingPortfolioNarrativePanel'
import AgingPortfolioPieChart from './AgingPortfolioPieChart'
import { exportAgingPortfolioReport } from './agingPortfolioExport'
import { buildAgingPortfolioNarrative, type AgingPortfolioNarrativeInput } from './agingPortfolioNarrative'
import {
  type AgingPortfolioSide,
  portfolioDimensionLabel,
} from './agingPortfolioDimensions'

type Props = {
  side: AgingPortfolioSide
  title: string
  periodLabel: string
  total: number
  series: ReceivablesAgingBand[]
  statusSplit?: ReceivablesStatusSplit
  overdueDays?: number
  reconciliation: 'subledger' | 'scaled' | 'synthetic'
  reconciliationGlLabel: string
  dimension: string
  onDimensionChange: (d: string) => void
  portfolioTable: AgingPortfolioTableResponse | null
  tableLoading: boolean
  viewStorageKey: string
}

function loadViewMode(key: string): SalesViewMode {
  try {
    const v = localStorage.getItem(key)
    if (v === 'table' || v === 'report') return v
  } catch { /* ignore */ }
  return 'report'
}

export default function AgingPortfolioSection({
  side,
  title,
  periodLabel,
  total,
  series,
  statusSplit,
  overdueDays,
  reconciliation,
  reconciliationGlLabel,
  dimension,
  onDimensionChange,
  portfolioTable,
  tableLoading,
  viewStorageKey,
}: Props) {
  const [viewMode, setViewMode] = useState<SalesViewMode>(() => loadViewMode(viewStorageKey))

  useEffect(() => {
    try {
      localStorage.setItem(viewStorageKey, viewMode)
    } catch { /* ignore */ }
  }, [viewMode, viewStorageKey])

  const narrative = useMemo(() => {
    const input: AgingPortfolioNarrativeInput = {
      side,
      periodLabel,
      total,
      series,
      statusSplit,
      overdueDays,
    }
    return buildAgingPortfolioNarrative(input)
  }, [side, periodLabel, total, series, statusSplit, overdueDays])

  const showRelationship =
    side === 'receivables' ? dimension === 'customer' : dimension === 'supplier'

  const buckets = portfolioTable?.buckets ?? []
  const dimLabel = portfolioDimensionLabel(side, dimension)
  const hasReportData = total > 0 && series.some(s => s.amount > 0)

  async function handleExport(kind: PlExportKind) {
    if (!hasReportData) return
    await exportAgingPortfolioReport({
      kind,
      title,
      periodLabel,
      total,
      series,
      narrative,
      side,
    })
  }

  return (
    <div className="rounded-xl" style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}>
      <div
        className="px-5 pt-4 pb-3 border-b flex justify-between gap-3 flex-wrap items-start"
        style={{ borderColor: '#F1F5F9' }}
      >
        <div>
          <h3 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>{title}</h3>
          <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
            {viewMode === 'report'
              ? `Aging buckets, narrative, and combined due-status distribution · ${periodLabel}`
              : `Collapsible buckets by ${dimLabel.toLowerCase()} · ${periodLabel}`}
          </p>
          {reconciliation !== 'subledger' && (
            <p className="text-[10px] mt-1" style={{ color: '#94A3B8' }}>
              Demo: aligned to GL {reconciliationGlLabel}
              {reconciliation === 'scaled' ? ' (scaled)' : ''}.
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end ml-auto">
          <PlViewToggleButton mode={viewMode} onChange={setViewMode} disabled={tableLoading} />
          {viewMode === 'table' && (
            <AgingPortfolioDimensionEditor
              side={side}
              dimension={dimension}
              onChange={onDimensionChange}
              disabled={tableLoading}
            />
          )}
          {viewMode === 'report' && (
            <PlExportMenu
              formats={['pptx', 'xlsx']}
              onExport={handleExport}
              disabled={!hasReportData}
            />
          )}
        </div>
      </div>

      {viewMode === 'report' ? (
        <div className="p-5 lg:p-6 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)] gap-x-8 gap-y-6 items-start min-h-[520px]">
          <div className="flex flex-col gap-4 min-h-[380px]">
            <AgingPortfolioMetricCards metrics={narrative.metrics} />
            <div className="pr-1">
              <AgingBucketLeaderboard series={series} total={total} />
            </div>
          </div>
          <div className="flex flex-col min-h-[380px] h-full gap-5">
            <div className="flex-1 min-h-0 overflow-y-auto pr-1">
              <AgingPortfolioNarrativePanel narrative={narrative} />
            </div>
            <div className="shrink-0 flex justify-center items-center py-2 min-h-[300px]">
              <AgingPortfolioPieChart series={series} total={total} statusSplit={statusSplit} />
            </div>
          </div>
        </div>
      ) : (
        <div className="p-4">
          {tableLoading ? (
            <div className="py-12 text-center text-xs" style={{ color: '#94A3B8' }}>Loading table…</div>
          ) : (
            <div className="overflow-auto min-h-[300px]" style={{ maxHeight: 560 }}>
              <AgingPortfolioBucketTable
                side={side}
                dimension={dimension}
                buckets={buckets}
                showRelationship={showRelationship}
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
