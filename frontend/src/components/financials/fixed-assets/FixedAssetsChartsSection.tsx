import { useEffect, useMemo, useState } from 'react'
import {
  api,
  type FixedAssetDimension,
  type FixedAssetNbvBridgeChart,
  type FixedAssetSnapshotInfo,
} from '../../../lib/api'
import SalesSideDrawer from '../../sales/SalesSideDrawer'
import FixedAssetsAddDispChart from './FixedAssetsAddDispChart'
import FixedAssetsAddDispChartEditor from './FixedAssetsAddDispChartEditor'
import FixedAssetsBridgeChartEditor from './FixedAssetsBridgeChartEditor'
import FixedAssetsCategoryChartEditor from './FixedAssetsCategoryChartEditor'
import FixedAssetsChartEditButton from './FixedAssetsChartEditButton'
import FixedAssetsNbvCategoryChart from './FixedAssetsNbvCategoryChart'
import FixedAssetsWaterfallChart from './FixedAssetsWaterfallChart'
import type { BridgeChartSettings } from './fixedAssetsChartRegistry'
import { DIMENSION_OPTIONS } from './fixedAssetsChartRegistry'
import { BRAND, SALES_CHART_CARD_CLASS } from '../../sales/analytics/salesChartTheme'

type ChartEditorId = 'bridge' | 'add_disp' | 'category'

type Props = {
  snapshots: FixedAssetSnapshotInfo[]
  anchorDate: string
  priorDate?: string
  entity?: string
  bridgeSettings: BridgeChartSettings
  onBridgeSettingsChange: (s: BridgeChartSettings) => void
  addDispDimension: FixedAssetDimension
  onAddDispDimensionChange: (d: FixedAssetDimension) => void
  categoryDimension: FixedAssetDimension
  onCategoryDimensionChange: (d: FixedAssetDimension) => void
}

export default function FixedAssetsChartsSection({
  snapshots,
  anchorDate,
  priorDate,
  entity,
  bridgeSettings,
  onBridgeSettingsChange,
  addDispDimension,
  onAddDispDimensionChange,
  categoryDimension,
  onCategoryDimensionChange,
}: Props) {
  const [activeEditor, setActiveEditor] = useState<ChartEditorId | null>(null)

  const [bridgeCharts, setBridgeCharts] = useState<FixedAssetNbvBridgeChart[]>([])
  const [bridgeLoading, setBridgeLoading] = useState(true)

  const [addDispRows, setAddDispRows] = useState<
    NonNullable<FixedAssetMovementsCharts['additions_disposals']>
  >([])
  const [addDispLoading, setAddDispLoading] = useState(true)

  const [categoryRows, setCategoryRows] = useState<
    NonNullable<FixedAssetMovementsCharts['nbv_by_category']>
  >([])
  const [categoryLoading, setCategoryLoading] = useState(true)

  const bridgeParams = useMemo(() => ({
    bridge_anchor_date: bridgeSettings.closingDate,
    bridge_prior_date: bridgeSettings.openingDate,
    bridge_dimension: bridgeSettings.scopeMode === 'dimension' ? bridgeSettings.dimension : undefined,
  }), [bridgeSettings])

  useEffect(() => {
    let cancelled = false
    setBridgeLoading(true)
    api.fixedAssetsMovements({
      anchor_date: bridgeSettings.closingDate,
      prior_date: bridgeSettings.openingDate,
      entity,
      section: 'bridge',
      ...bridgeParams,
    })
      .then(res => {
        if (cancelled) return
        const bridges = res.charts.nbv_bridges?.length
          ? res.charts.nbv_bridges
          : res.charts.nbv_bridge
            ? [res.charts.nbv_bridge]
            : []
        setBridgeCharts(bridges)
      })
      .catch(() => {
        if (!cancelled) setBridgeCharts([])
      })
      .finally(() => {
        if (!cancelled) setBridgeLoading(false)
      })
    return () => { cancelled = true }
  }, [entity, bridgeParams, bridgeSettings.closingDate, bridgeSettings.openingDate])

  useEffect(() => {
    let cancelled = false
    setAddDispLoading(true)
    api.fixedAssetsMovements({
      anchor_date: anchorDate,
      prior_date: priorDate,
      entity,
      section: 'add_disp',
      add_disp_dimension: addDispDimension,
    })
      .then(res => {
        if (cancelled) return
        setAddDispRows(res.charts.additions_disposals ?? [])
      })
      .catch(() => {
        if (!cancelled) setAddDispRows([])
      })
      .finally(() => {
        if (!cancelled) setAddDispLoading(false)
      })
    return () => { cancelled = true }
  }, [anchorDate, priorDate, entity, addDispDimension])

  useEffect(() => {
    let cancelled = false
    setCategoryLoading(true)
    api.fixedAssetsMovements({
      anchor_date: anchorDate,
      prior_date: priorDate,
      entity,
      section: 'category',
      category_dimension: categoryDimension,
    })
      .then(res => {
        if (cancelled) return
        setCategoryRows(res.charts.nbv_by_category ?? [])
      })
      .catch(() => {
        if (!cancelled) setCategoryRows([])
      })
      .finally(() => {
        if (!cancelled) setCategoryLoading(false)
      })
    return () => { cancelled = true }
  }, [anchorDate, priorDate, entity, categoryDimension])

  const categoryLabels = useMemo(() => {
    const anchorSnap = snapshots.find(s => s.as_of_date === anchorDate)
    const priorSnap = priorDate ? snapshots.find(s => s.as_of_date === priorDate) : undefined
    return {
      anchor: anchorSnap?.col_label ?? 'Current',
      prior: priorSnap?.col_label ?? 'Prior year',
    }
  }, [snapshots, anchorDate, priorDate])

  const categoryDimLabel = DIMENSION_OPTIONS.find(d => d.id === categoryDimension)?.label ?? categoryDimension

  const anyData = bridgeCharts.length > 0 || addDispRows.length > 0 || categoryRows.length > 0
  const anyLoading = bridgeLoading || addDispLoading || categoryLoading
  if (!anyData && !anyLoading) return null

  return (
    <div className="space-y-4">
      <div className={SALES_CHART_CARD_CLASS} style={{ borderColor: BRAND.border }}>
        <FixedAssetsWaterfallChart
          bridges={bridgeCharts}
          loading={bridgeLoading}
          actions={(
            <FixedAssetsChartEditButton
              title="Edit NBV bridge"
              onClick={() => setActiveEditor('bridge')}
            />
          )}
        />
      </div>

      <div className={SALES_CHART_CARD_CLASS} style={{ borderColor: BRAND.border }}>
        <FixedAssetsAddDispChart
          rows={addDispRows}
          dimension={addDispDimension}
          loading={addDispLoading}
          actions={(
            <FixedAssetsChartEditButton
              title="Edit additions & disposals"
              onClick={() => setActiveEditor('add_disp')}
            />
          )}
        />
      </div>

      <div className={SALES_CHART_CARD_CLASS} style={{ borderColor: BRAND.border }}>
        <FixedAssetsNbvCategoryChart
          rows={categoryRows}
          dimensionLabel={categoryDimLabel}
          anchorLabel={categoryLabels.anchor}
          priorLabel={categoryLabels.prior}
          loading={categoryLoading}
          actions={(
            <FixedAssetsChartEditButton
              title="Edit NBV by category"
              onClick={() => setActiveEditor('category')}
            />
          )}
        />
      </div>

      <SalesSideDrawer open={activeEditor !== null} onClose={() => setActiveEditor(null)}>
        {activeEditor === 'bridge' && (
          <FixedAssetsBridgeChartEditor
            embedded
            settings={bridgeSettings}
            snapshots={snapshots}
            onChange={onBridgeSettingsChange}
          />
        )}
        {activeEditor === 'add_disp' && (
          <FixedAssetsAddDispChartEditor
            embedded
            dimension={addDispDimension}
            onChange={onAddDispDimensionChange}
          />
        )}
        {activeEditor === 'category' && (
          <FixedAssetsCategoryChartEditor
            embedded
            dimension={categoryDimension}
            onChange={onCategoryDimensionChange}
          />
        )}
      </SalesSideDrawer>
    </div>
  )
}

type FixedAssetMovementsCharts = {
  additions_disposals?: Array<{
    key: string
    label: string
    additions: number
    disposals: number
    depreciation: number
  }>
  nbv_by_category?: Array<{
    category: string
    nbv: number
    prior_nbv: number
    delta_pct: number | null
  }>
}
