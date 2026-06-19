export interface AgingBand {
  band: string
  label: string
  amount: number
}

export interface AgingStatusSplit {
  before_due: number
  overdue: number
  before_due_pct: number
  overdue_pct: number
}

export interface AgingSummaryRow {
  band: string
  label: string
  document_count: number
  amount: number
}

export interface AgingCompositionSegment {
  band: string
  label: string
  amount: number
}

export interface AgingCounterpartyComboRow {
  name: string
  balance: number
  days_outstanding: number
}

export interface AgingCounterpartyScatterRow {
  name: string
  balance: number
  overdue_pct: number
}

export interface AgingEntityRow {
  entity_code: string
  balance: number
}

export interface AgingGeoRow {
  country: string
  balance: number
  before_due: number
  overdue: number
  overdue_pct: number
}
