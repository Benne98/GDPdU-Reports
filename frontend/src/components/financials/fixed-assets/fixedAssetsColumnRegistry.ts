import type { FixedAssetDimension, FixedAssetSnapshotInfo } from '../../../lib/api'
import { fixedAssetDimensionLabel } from '../../../lib/dimensionLabels'
import { defaultCompareDates } from './fixedAssetsPeriodUtils'

export type FixedAssetsColumnDef = {
  id: string
  kind: 'year'
  label: string
  year?: number
  visible: boolean
}

const COLUMNS_KEY = 'finssentials.fixedAssets.columns.v2'
const DIMS_KEY = 'finssentials.fixedAssets.dimensions.v2'
const BRIDGE_ENTITY_KEY = 'finssentials.fixedAssets.bridgeEntity.v1'
const VIEW_KEY = 'finssentials.fixedAssets.viewMode.v1'
const ADD_DISP_DIM_KEY = 'finssentials.fixedAssets.addDispDim.v1'

export const DIMENSION_OPTIONS: Array<{ id: FixedAssetDimension; label: string }> = (
  ['bilanzposition', 'segment', 'entity', 'asset'] as FixedAssetDimension[]
).map(id => ({ id, label: fixedAssetDimensionLabel(id) }))

export const DEFAULT_TABLE_DIMENSIONS: FixedAssetDimension[] = ['bilanzposition', 'segment']

export function buildDefaultColumns(snapshots: FixedAssetSnapshotInfo[], anchor: string): FixedAssetsColumnDef[] {
  const years = new Set<number>([
    parseInt(anchor.slice(0, 4), 10),
    ...defaultCompareDates(anchor, snapshots, 4).map(d => parseInt(d.slice(0, 4), 10)),
  ])
  return [...years].sort().map(y => ({
    id: `year-${y}`,
    kind: 'year' as const,
    label: `Dec${String(y).slice(-2)}A`,
    year: y,
    visible: true,
  }))
}

export function loadFixedAssetsColumns(): FixedAssetsColumnDef[] | null {
  try {
    const raw = localStorage.getItem(COLUMNS_KEY)
    if (!raw) return null
    return JSON.parse(raw) as FixedAssetsColumnDef[]
  } catch {
    return null
  }
}

export function saveFixedAssetsColumns(cols: FixedAssetsColumnDef[]): void {
  try {
    localStorage.setItem(COLUMNS_KEY, JSON.stringify(cols))
  } catch {
    /* ignore */
  }
}

export function loadDimensions(): FixedAssetDimension[] {
  try {
    const raw = localStorage.getItem(DIMS_KEY)
    if (!raw) return DEFAULT_TABLE_DIMENSIONS
    const parsed = JSON.parse(raw) as FixedAssetDimension[]
    return parsed.length ? parsed.slice(0, 3) : DEFAULT_TABLE_DIMENSIONS
  } catch {
    return DEFAULT_TABLE_DIMENSIONS
  }
}

export function saveDimensions(dims: FixedAssetDimension[]): void {
  try {
    localStorage.setItem(DIMS_KEY, JSON.stringify(dims.slice(0, 3)))
  } catch {
    /* ignore */
  }
}

export function loadBridgeEntity(): string {
  try {
    return localStorage.getItem(BRIDGE_ENTITY_KEY) ?? 'all'
  } catch {
    return 'all'
  }
}

export function saveBridgeEntity(v: string): void {
  try {
    localStorage.setItem(BRIDGE_ENTITY_KEY, v)
  } catch {
    /* ignore */
  }
}

export function loadAddDispDimension(): FixedAssetDimension {
  try {
    const v = localStorage.getItem(ADD_DISP_DIM_KEY)
    if (v === 'bilanzposition' || v === 'segment' || v === 'entity' || v === 'asset') return v
  } catch { /* ignore */ }
  return 'segment'
}

export function saveAddDispDimension(v: FixedAssetDimension): void {
  try {
    localStorage.setItem(ADD_DISP_DIM_KEY, v)
  } catch {
    /* ignore */
  }
}

export function loadViewMode(): 'report' | 'table' {
  try {
    return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'report'
  } catch {
    return 'report'
  }
}

export function saveViewMode(mode: 'report' | 'table'): void {
  try {
    localStorage.setItem(VIEW_KEY, mode)
  } catch {
    /* ignore */
  }
}
