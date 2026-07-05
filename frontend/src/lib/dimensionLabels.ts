import type { FixedAssetDimension, PersonnelDimension } from './api'

export const FIXED_ASSET_DIMENSION_LABELS: Record<FixedAssetDimension, string> = {
  bilanzposition: 'Balance sheet line',
  segment: 'Business segment',
  entity: 'Entity',
  asset: 'Asset description',
}

export const PERSONNEL_DIMENSION_LABELS: Record<PersonnelDimension, string> = {
  entity: 'Entity',
  org_unit: 'Organizational unit',
  gew_ang: 'Employee type',
  kst_name: 'Cost center',
  bereich: 'Division',
}

export function fixedAssetDimensionLabel(id: FixedAssetDimension): string {
  return FIXED_ASSET_DIMENSION_LABELS[id] ?? id
}

export function personnelDimensionLabel(id: PersonnelDimension): string {
  return PERSONNEL_DIMENSION_LABELS[id] ?? id
}

export function formatFixedAssetDimensionChain(dims: FixedAssetDimension[]): string {
  return dims.map(fixedAssetDimensionLabel).join(' → ')
}

export function formatPersonnelDimensionChain(dims: PersonnelDimension[]): string {
  return dims.map(personnelDimensionLabel).join(' → ')
}
