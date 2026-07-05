import type { FixedAssetSnapshotInfo } from '../../../lib/api'
import { DIMENSION_OPTIONS } from './fixedAssetsColumnRegistry'

export { DIMENSION_OPTIONS }

const BRIDGE_KEY = 'finssentials.fixedAssets.chart.bridge.v2'
const LEGACY_BRIDGE_KEY = 'finssentials.fixedAssets.chart.bridge.v1'
const LEGACY_BRIDGE_ENTITY_KEY = 'finssentials.fixedAssets.bridgeEntity.v1'
const LEGACY_ADD_DISP_KEY = 'finssentials.fixedAssets.addDispDim.v1'
const ADD_DISP_KEY = 'finssentials.fixedAssets.chart.addDisp.v1'
const CATEGORY_DIM_KEY = 'finssentials.fixedAssets.chart.categoryDim.v1'

export type BridgeScopeMode = 'consolidated' | 'dimension'
export type BridgeDimension = 'entity' | 'segment'

export type BridgeChartSettings = {
  closingDate: string
  openingDate: string
  scopeMode: BridgeScopeMode
  dimension: BridgeDimension
}

export function defaultBridgeSettings(
  anchorDate: string,
  snapshots: FixedAssetSnapshotInfo[],
): BridgeChartSettings {
  const dates = snapshots.map(s => s.as_of_date).sort()
  const opening =
    dates.filter(d => d < anchorDate).sort().pop()
    ?? dates.find(d => d !== anchorDate)
    ?? anchorDate
  return {
    closingDate: anchorDate,
    openingDate: opening,
    scopeMode: 'consolidated',
    dimension: 'entity',
  }
}

function parseLegacyBridge(raw: Record<string, unknown>, defaults: BridgeChartSettings): BridgeChartSettings {
  const closing = String(raw.closingDate ?? raw.anchorDate ?? defaults.closingDate)
  const opening = String(raw.openingDate ?? raw.priorDate ?? defaults.openingDate)
  const scopeMode = raw.scopeMode === 'dimension' ? 'dimension' : 'consolidated'
  const dim = raw.dimension === 'segment' ? 'segment' : 'entity'
  return { closingDate: closing, openingDate: opening, scopeMode, dimension: dim }
}

export function loadBridgeSettings(
  anchorDate: string,
  snapshots: FixedAssetSnapshotInfo[],
): BridgeChartSettings {
  const defaults = defaultBridgeSettings(anchorDate, snapshots)
  try {
    for (const key of [BRIDGE_KEY, LEGACY_BRIDGE_KEY]) {
      const raw = localStorage.getItem(key)
      if (raw) return parseLegacyBridge(JSON.parse(raw) as Record<string, unknown>, defaults)
    }
    const legacyEntity = localStorage.getItem(LEGACY_BRIDGE_ENTITY_KEY)
    if (legacyEntity && legacyEntity !== 'all') {
      return { ...defaults, scopeMode: 'dimension', dimension: 'entity' }
    }
    return defaults
  } catch {
    return defaults
  }
}

export function saveBridgeSettings(s: BridgeChartSettings): void {
  try {
    localStorage.setItem(BRIDGE_KEY, JSON.stringify(s))
  } catch {
    /* ignore */
  }
}

export function loadAddDispDimension(): import('../../../lib/api').FixedAssetDimension {
  const valid = (v: string | null): import('../../../lib/api').FixedAssetDimension | null => {
    if (v === 'bilanzposition' || v === 'segment' || v === 'entity' || v === 'asset') return v
    return null
  }
  try {
    return valid(localStorage.getItem(ADD_DISP_KEY))
      ?? valid(localStorage.getItem(LEGACY_ADD_DISP_KEY))
      ?? 'segment'
  } catch {
    return 'segment'
  }
}

export function saveAddDispDimension(v: import('../../../lib/api').FixedAssetDimension): void {
  try {
    localStorage.setItem(ADD_DISP_KEY, v)
  } catch {
    /* ignore */
  }
}

export function loadCategoryDimension(): import('../../../lib/api').FixedAssetDimension {
  const valid = (v: string | null): import('../../../lib/api').FixedAssetDimension | null => {
    if (v === 'bilanzposition' || v === 'segment' || v === 'entity' || v === 'asset') return v
    return null
  }
  try {
    return valid(localStorage.getItem(CATEGORY_DIM_KEY)) ?? 'bilanzposition'
  } catch {
    return 'bilanzposition'
  }
}

export function saveCategoryDimension(v: import('../../../lib/api').FixedAssetDimension): void {
  try {
    localStorage.setItem(CATEGORY_DIM_KEY, v)
  } catch {
    /* ignore */
  }
}
