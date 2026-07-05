import { useEffect, useMemo, useState } from 'react'

import {
  api,
  type FixedAssetDimension,
  type FixedAssetReportDetailResponse,
  type FixedAssetRollforwardResponse,
  type FixedAssetSnapshotInfo,
} from '../../../lib/api'

import type { PeriodSelection } from '../../../lib/periodSelection'
import { periodCacheKey } from '../../../lib/periodSelection'

import FixedAssetsChartsSection from './FixedAssetsChartsSection'

import FixedAssetsRollforwardPanel from './FixedAssetsRollforwardPanel'

import {
  buildDefaultColumns,
  loadDimensions,
  loadFixedAssetsColumns,
  loadViewMode,
  saveFixedAssetsColumns,
  type FixedAssetsColumnDef,
} from './fixedAssetsColumnRegistry'

import {
  loadAddDispDimension,
  loadBridgeSettings,
  loadCategoryDimension,
  saveAddDispDimension,
  saveBridgeSettings,
  saveCategoryDimension,
  type BridgeChartSettings,
} from './fixedAssetsChartRegistry'

import { anchorDateFromPeriod, defaultCompareDates } from './fixedAssetsPeriodUtils'

type Props = {
  period: PeriodSelection
  entity?: string
}

export default function GlFixedAssetsTab({ period, entity }: Props) {
  const periodKey = periodCacheKey(period)
  const entityKey = entity && entity !== 'all' ? entity : undefined

  const [snapshots, setSnapshots] = useState<FixedAssetSnapshotInfo[]>([])
  const [snapshotsLoading, setSnapshotsLoading] = useState(true)
  const [rollforward, setRollforward] = useState<FixedAssetRollforwardResponse | null>(null)
  const [reportDetail, setReportDetail] = useState<FixedAssetReportDetailResponse | null>(null)
  const [tableLoading, setTableLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<'report' | 'table'>(loadViewMode)
  const [columns, setColumns] = useState<FixedAssetsColumnDef[]>([])
  const [dimensions, setDimensions] = useState<FixedAssetDimension[]>(loadDimensions)
  const [bridgeSettings, setBridgeSettings] = useState<BridgeChartSettings | null>(null)
  const [addDispDimension, setAddDispDimension] = useState<FixedAssetDimension>(loadAddDispDimension)
  const [categoryDimension, setCategoryDimension] = useState<FixedAssetDimension>(loadCategoryDimension)

  const anchorDate = useMemo(
    () => anchorDateFromPeriod(period, snapshots),
    [periodKey, snapshots],
  )

  useEffect(() => {
    setSnapshotsLoading(true)
    api.fixedAssetsSnapshots()
      .then(r => setSnapshots(r.snapshots ?? []))
      .catch(() => setSnapshots([]))
      .finally(() => setSnapshotsLoading(false))
  }, [])

  useEffect(() => {
    if (!snapshots.length || !anchorDate) {
      setColumns([])
      return
    }
    const saved = loadFixedAssetsColumns()
    if (saved?.length) {
      setColumns(saved)
      return
    }
    const defaults = buildDefaultColumns(snapshots, anchorDate)
    setColumns(defaults)
    saveFixedAssetsColumns(defaults)
  }, [snapshots, anchorDate])

  useEffect(() => {
    if (!anchorDate || !snapshots.length) return
    setBridgeSettings(prev => prev ?? loadBridgeSettings(anchorDate, snapshots))
  }, [anchorDate, snapshots])

  const compareDates = useMemo(() => {
    if (!anchorDate) return []
    const anchorYear = parseInt(anchorDate.slice(0, 4), 10)
    const fromCols = columns
      .filter(c => c.visible && c.year && c.year !== anchorYear)
      .map(c => `${c.year}-12-31`)
    if (fromCols.length) return fromCols.sort((a, b) => b.localeCompare(a))
    return defaultCompareDates(anchorDate, snapshots)
  }, [columns, anchorDate, snapshots])

  const compareParam = compareDates.length ? compareDates.join(',') : undefined
  const priorDate = compareDates[0]
  const dimensionsParam = dimensions.join(',')

  useEffect(() => {
    if (!anchorDate) {
      setRollforward(null)
      setReportDetail(null)
      setTableLoading(false)
      return
    }

    setTableLoading(true)
    setError(null)

    Promise.all([
      api.fixedAssetsRollforward({
        anchor_date: anchorDate,
        compare_dates: compareParam,
        entity: entityKey,
        dimensions: dimensionsParam,
      }),
      api.fixedAssetsReportDetail({
        anchor_date: anchorDate,
        compare_dates: compareParam,
        entity: entityKey,
      }),
    ])
      .then(([rf, report]) => {
        setRollforward(rf)
        setReportDetail(report)
      })
      .catch(() => {
        setRollforward(null)
        setReportDetail(null)
        setError('Fixed assets data could not be loaded. Run load_fixed_asset_subledger.py.')
      })
      .finally(() => setTableLoading(false))
  }, [anchorDate, compareParam, entityKey, dimensionsParam, periodKey])

  const exportName = anchorDate
    ? `Anchor ${anchorDate}${entityKey ? ` · ${entityKey}` : ''}`
    : 'Fixed assets'

  const tableBusy = tableLoading || snapshotsLoading

  function handleAddDispChange(d: FixedAssetDimension) {
    setAddDispDimension(d)
    saveAddDispDimension(d)
  }

  function handleBridgeChange(s: BridgeChartSettings) {
    setBridgeSettings(s)
    saveBridgeSettings(s)
  }

  function handleCategoryDimensionChange(d: FixedAssetDimension) {
    setCategoryDimension(d)
    saveCategoryDimension(d)
  }

  return (
    <div className="space-y-4">
      <FixedAssetsRollforwardPanel
        rollforward={rollforward}
        reportDetail={reportDetail}
        loading={tableBusy}
        error={error}
        columns={columns}
        dimensions={dimensions}
        snapshots={snapshots}
        onColumnsChange={setColumns}
        onDimensionsChange={setDimensions}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        exportName={exportName}
      />
      {bridgeSettings && anchorDate && (
        <FixedAssetsChartsSection
          snapshots={snapshots}
          anchorDate={anchorDate}
          priorDate={priorDate}
          entity={entityKey}
          bridgeSettings={bridgeSettings}
          onBridgeSettingsChange={handleBridgeChange}
          addDispDimension={addDispDimension}
          onAddDispDimensionChange={handleAddDispChange}
          categoryDimension={categoryDimension}
          onCategoryDimensionChange={handleCategoryDimensionChange}
        />
      )}
    </div>
  )
}
