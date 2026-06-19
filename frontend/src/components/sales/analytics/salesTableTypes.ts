export type SalesViewMode = 'report' | 'table'

export type SalesColumnDef = {
  id: string
  label: string
  /** Stable key for cell renderer */
  field: string
}
