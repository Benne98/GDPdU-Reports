import { useEffect, useState } from 'react'
import { api, type FinancialsOverviewResponse, type FinPeriodParams } from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import type { FinTab } from '../financialsTabs'
import { useChartLoadReporter } from '../../../hooks/useChartLoadReporter'
import { useOptionalActionNotesContext } from '../../action-notes/ActionNotesContext'
import { captureExecutiveSummarySnapshot } from '../../action-notes/captureOverviewTables'
import { FIN_REPORT_SPLIT_GRID } from '../statement-two-view/finReportLayout'
import PlSectionHeading from '../pl-two-view/PlSectionHeading'
import PlViewToggleButton, { type PlViewMode } from '../pl-two-view/PlViewToggleButton'
import ExecutiveSummaryTable from './ExecutiveSummaryTable'
import OverviewHighlightsPanel from './OverviewHighlightsPanel'
import OverviewGroupSummary from './OverviewGroupSummary'
import EbitTable, { type DrillDownRequest } from '../../cockpit/EbitTable'

const VIEW_MODE_KEY = 'finssentials.overview-group-view-mode'

function loadViewMode(): PlViewMode {
  try {
    const v = sessionStorage.getItem(VIEW_MODE_KEY)
    return v === 'table' ? 'table' : 'report'
  } catch {
    return 'report'
  }
}

function saveViewMode(mode: PlViewMode): void {
  try {
    sessionStorage.setItem(VIEW_MODE_KEY, mode)
  } catch {
    /* ignore */
  }
}

type Props = {
  periodParams: FinPeriodParams
  cockpitPeriod: PeriodSelection
  entity?: string
  resetKey: string
  onNavigateTab: (tab: FinTab) => void
  onDrillDown?: (req: DrillDownRequest) => void
  activeDrillKey?: string
}

const CARD = {
  background: '#FFFFFF',
  border: '1px solid #E2E8F0',
  boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
} as const

export default function OverviewGroupTile({
  periodParams,
  cockpitPeriod,
  entity,
  resetKey,
  onNavigateTab,
  onDrillDown,
  activeDrillKey,
}: Props) {
  const [data, setData] = useState<FinancialsOverviewResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [highlightsLoading, setHighlightsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [highlightIdx, setHighlightIdx] = useState(0)
  const [viewMode, setViewMode] = useState<PlViewMode>(() => loadViewMode())
  const notesCtx = useOptionalActionNotesContext()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setHighlightsLoading(true)
    setError(null)
    setHighlightIdx(0)

    void api
      .financialsOverview(periodParams)
      .then(res => {
        if (!cancelled) {
          setData(res)
          setError(null)
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setData(null)
        setError(e instanceof Error ? e.message : 'Failed to load group overview')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    void api
      .financialsOverviewHighlights(periodParams)
      .then(({ highlights }) => {
        if (!cancelled) setData(prev => (prev ? { ...prev, highlights } : prev))
      })
      .catch(() => {
        /* Sidebar highlights are optional; keep executive summary visible. */
      })
      .finally(() => {
        if (!cancelled) setHighlightsLoading(false)
      })

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])

  useEffect(() => {
    saveViewMode(viewMode)
  }, [viewMode])

  useEffect(() => {
    if (!notesCtx) return
    if (!data) {
      notesCtx.unregisterTableCandidate('overview-group-summary')
      return
    }
    notesCtx.registerTableCandidate({
      id: 'overview-group-summary',
      label: 'Group overview',
      description: viewMode === 'table'
        ? 'Executive summary — output, EBIT and margin by entity'
        : 'Executive summary — group KPIs',
      capture: () => captureExecutiveSummarySnapshot(data),
      viewState: { tab: 'overview', view: viewMode },
    })
    return () => notesCtx.unregisterTableCandidate('overview-group-summary')
  }, [notesCtx, data, viewMode])

  useChartLoadReporter('overview-group-summary', loading, error)

  if (error) {
    return (
      <div className="rounded-xl px-5 py-4 text-sm" style={{ background: '#FEF2F2', border: '1px solid #FECACA', color: '#B91C1C' }} role="alert">
        {error}
      </div>
    )
  }

  if (loading && !data) {
    return (
      <div className="rounded-xl px-6 py-16 text-center text-sm animate-pulse" style={{ ...CARD, color: '#94A3B8' }}>
        Loading group overview…
      </div>
    )
  }

  if (!data) return null

  return (
    <div className="rounded-xl overflow-hidden" style={CARD}>
      <div className="px-4 pt-6 pb-4 flex items-start justify-between gap-3" style={{ borderBottom: '1px solid #F1F5F9' }}>
        <div className="min-w-0 flex-1">
          <OverviewGroupSummary intro={data.intro} />
        </div>
        <PlViewToggleButton mode={viewMode} onChange={setViewMode} />
      </div>

      <div className="px-4 pt-4 pb-6">
        {viewMode === 'report' ? (
          <div className={FIN_REPORT_SPLIT_GRID}>
            <div className="min-w-0">
              <PlSectionHeading>Executive summary</PlSectionHeading>
              <ExecutiveSummaryTable sections={data.sections} colLabels={data.col_labels} />
            </div>
            <div className="min-w-0 lg:sticky lg:top-24 lg:self-start">
              <OverviewHighlightsPanel
                highlights={data.highlights}
                loading={highlightsLoading}
                activeIndex={highlightIdx}
                onActiveIndexChange={setHighlightIdx}
                onNavigateTab={onNavigateTab}
              />
            </div>
          </div>
        ) : (
          <div>
            <PlSectionHeading>Executive summary</PlSectionHeading>
            <EbitTable
              period={cockpitPeriod}
              entity={entity}
              variant="embedded"
              showColumnEditor
              showExport={false}
              showSidebar={false}
              onDrillDown={onDrillDown}
              activeDrillKey={activeDrillKey}
              title="Total output, EBIT and EBIT Margin by Entity"
            />
          </div>
        )}
      </div>
    </div>
  )
}
