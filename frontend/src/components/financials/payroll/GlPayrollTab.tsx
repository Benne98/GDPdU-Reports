import { useEffect, useMemo, useState } from 'react'

import {

  api,

  type PersonnelAccountingResponse,

  type PersonnelDimension,

  type PersonnelLayout,

  type PersonnelMovementsResponse,

  type PersonnelSnapshotInfo,

} from '../../../lib/api'

import type { PeriodSelection } from '../../../lib/periodSelection'

import { periodCacheKey } from '../../../lib/periodSelection'

import PayrollAccountingPanel from './PayrollAccountingPanel'

import PayrollChartsSection from './PayrollChartsSection'

import {

  loadChartDimension,

  loadTrendMetric,

  loadColumnDimension,

  loadLayout,

  loadPayrollColumns,

  loadRowDimensions,

  loadViewMode,

  mergeMetricColumnsFromApi,

  mergePayrollColumns,

  sortMetricIds,

  saveChartDimension,

  saveTrendMetric,

  savePayrollColumns,

  type PayrollColumnDef,

} from './payrollColumnRegistry'

import { anchorDateFromPeriod, defaultCompareDates } from './payrollPeriodUtils'



type Props = {

  period: PeriodSelection

  entity?: string

}



export default function GlPayrollTab({ period, entity }: Props) {

  const periodKey = periodCacheKey(period)

  const entityKey = entity && entity !== 'all' ? entity : undefined



  const [snapshots, setSnapshots] = useState<PersonnelSnapshotInfo[]>([])

  const [snapshotsLoading, setSnapshotsLoading] = useState(true)

  const [accounting, setAccounting] = useState<PersonnelAccountingResponse | null>(null)

  const [movements, setMovements] = useState<PersonnelMovementsResponse | null>(null)

  const [tableLoading, setTableLoading] = useState(true)

  const [chartsLoading, setChartsLoading] = useState(true)

  const [error, setError] = useState<string | null>(null)

  const [viewMode, setViewMode] = useState<'report' | 'table'>(loadViewMode)

  const [columns, setColumns] = useState<PayrollColumnDef[]>([])

  const [layout, setLayout] = useState<PersonnelLayout>(loadLayout)

  const [columnDimension, setColumnDimension] = useState<PersonnelDimension>(loadColumnDimension)

  const [rowDimensions, setRowDimensions] = useState<PersonnelDimension[]>(loadRowDimensions)

  const [chartDimension, setChartDimension] = useState<PersonnelDimension>(loadChartDimension)

  const [trendMetric, setTrendMetric] = useState<string>(loadTrendMetric)



  const anchorDate = useMemo(

    () => anchorDateFromPeriod(period, snapshots),

    [periodKey, snapshots],

  )



  useEffect(() => {

    setSnapshotsLoading(true)

    api.personnelSnapshots()

      .then(r => setSnapshots(r.snapshots ?? []))

      .catch(() => setSnapshots([]))

      .finally(() => setSnapshotsLoading(false))

  }, [])



  useEffect(() => {

    if (!snapshots.length || !anchorDate) {

      setColumns([])

      return

    }

    const saved = loadPayrollColumns()

    const merged = mergePayrollColumns(saved, snapshots, anchorDate)

    setColumns(merged)

    if (!saved?.length || JSON.stringify(saved) !== JSON.stringify(merged)) {

      savePayrollColumns(merged)

    }

  }, [snapshots, anchorDate])



  const visibleMetricIds = useMemo(
    () => sortMetricIds(
      columns.filter(c => c.kind === 'metric' && c.visible && c.metricId).map(c => c.metricId as string),
    ),
    [columns],
  )



  const compareDates = useMemo(() => {

    if (!anchorDate) return []

    const fromCols = columns

      .filter(c => c.kind === 'snapshot' && c.visible && c.snapshotDate && c.snapshotDate !== anchorDate)

      .map(c => c.snapshotDate as string)

    if (fromCols.length) return fromCols.sort((a, b) => a.localeCompare(b))

    return defaultCompareDates(anchorDate, snapshots).sort((a, b) => a.localeCompare(b))

  }, [columns, anchorDate, snapshots])



  const priorDate = useMemo(() => {

    if (!anchorDate) return undefined

    const before = compareDates.filter(d => d < anchorDate).sort((a, b) => b.localeCompare(a))

    return before[0] ?? compareDates.sort((a, b) => b.localeCompare(a))[0]

  }, [anchorDate, compareDates])



  const effectiveLayout: PersonnelLayout = viewMode === 'report' ? 'flat' : layout

  const compareParam = compareDates.length ? compareDates.join(',') : undefined

  const metricsParam = visibleMetricIds.length ? visibleMetricIds.join(',') : undefined

  const rowDimsParam = effectiveLayout === 'row_hierarchy' ? rowDimensions.join(',') : undefined



  useEffect(() => {

    if (!anchorDate) {

      setAccounting(null)

      setTableLoading(false)

      return

    }

    let cancelled = false

    setTableLoading(true)

    setError(null)

    api.personnelAccounting({

      anchor_date: anchorDate,

      compare_dates: compareParam,

      entity: entityKey,

      layout: effectiveLayout,

      column_dimension: effectiveLayout === 'column_split' ? columnDimension : undefined,

      row_dimensions: rowDimsParam,

      metrics: metricsParam,

    })

      .then(acc => {

        if (cancelled) return

        setAccounting(acc)

        setColumns(prev => {

          const next = acc.available_metrics?.length

            ? mergeMetricColumnsFromApi(prev, acc.available_metrics)

            : prev

          if (next !== prev) savePayrollColumns(next)

          return next

        })

      })

      .catch(() => {

        if (!cancelled) {

          setAccounting(null)

          setError('Payroll data could not be loaded. Ensure personnel snapshots are seeded.')

        }

      })

      .finally(() => {

        if (!cancelled) setTableLoading(false)

      })

    return () => { cancelled = true }

  }, [

    anchorDate,

    compareParam,

    entityKey,

    metricsParam,

    periodKey,

    effectiveLayout,

    columnDimension,

    rowDimsParam,

  ])



  useEffect(() => {

    if (!anchorDate) {

      setMovements(null)

      setChartsLoading(false)

      return

    }

    let cancelled = false

    setChartsLoading(true)

    api.personnelMovements({

      anchor_date: anchorDate,

      prior_date: priorDate,

      entity: entityKey,

      chart_dimension: chartDimension,

      trend_metric: trendMetric,

    })

      .then(mov => {

        if (!cancelled) setMovements(mov)

      })

      .catch(() => {

        if (!cancelled) setMovements(null)

      })

      .finally(() => {

        if (!cancelled) setChartsLoading(false)

      })

    return () => { cancelled = true }

  }, [anchorDate, priorDate, entityKey, chartDimension, trendMetric, periodKey])



  const exportName = anchorDate

    ? `Anchor ${anchorDate}${entityKey ? ` · ${entityKey}` : ''}`

    : 'Payroll'



  const tableBusy = tableLoading || snapshotsLoading

  const anchorLabel =

    (accounting?.col_labels && accounting.anchor_date

      ? accounting.col_labels[accounting.anchor_date]

      : undefined) ?? 'Current'

  const priorLabel = priorDate

    ? accounting?.col_labels?.[priorDate] ?? `FY${priorDate.slice(2, 4)}A`

    : 'Prior year'



  function handleChartDimChange(d: PersonnelDimension) {

    setChartDimension(d)

    saveChartDimension(d)

  }



  function handleTrendMetricChange(id: string) {

    setTrendMetric(id)

    saveTrendMetric(id)

  }



  return (

    <div className="space-y-4">

      <PayrollAccountingPanel

        data={accounting}

        movements={movements}

        loading={tableBusy}

        error={error}

        columns={columns}

        layout={layout}

        columnDimension={columnDimension}

        rowDimensions={rowDimensions}
        viewMode={viewMode}

        onColumnsChange={setColumns}

        onLayoutChange={setLayout}

        onColumnDimensionChange={setColumnDimension}

        onRowDimensionsChange={setRowDimensions}

        onViewModeChange={setViewMode}

        exportName={exportName}

      />

      <PayrollChartsSection

        movements={movements}

        chartDimension={chartDimension}

        onChartDimensionChange={handleChartDimChange}

        trendMetric={trendMetric}

        onTrendMetricChange={handleTrendMetricChange}

        anchorLabel={anchorLabel}

        priorLabel={priorLabel}

        loading={chartsLoading}

      />

    </div>

  )

}


