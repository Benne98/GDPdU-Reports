export type AgingPortfolioSide = 'receivables' | 'payables'

export type AgingPortfolioDimensionOption = {
  id: string
  label: string
  salesMapped?: boolean
}

export const RECEIVABLES_PORTFOLIO_DIMENSIONS: AgingPortfolioDimensionOption[] = [
  { id: 'customer', label: 'Customer' },
  { id: 'entity', label: 'Entity', salesMapped: true },
  { id: 'segment', label: 'Segment', salesMapped: true },
  { id: 'country', label: 'Country' },
  { id: 'customer_group', label: 'Customer group' },
  { id: 'salesperson', label: 'Salesperson' },
]

export const PAYABLES_PORTFOLIO_DIMENSIONS: AgingPortfolioDimensionOption[] = [
  { id: 'supplier', label: 'Supplier' },
  { id: 'entity', label: 'Entity', salesMapped: true },
  { id: 'segment', label: 'Segment', salesMapped: true },
  { id: 'country', label: 'Country' },
  { id: 'supplier_group', label: 'Supplier group' },
  { id: 'buyer', label: 'Buyer' },
]

export function portfolioDimensionsFor(side: AgingPortfolioSide): AgingPortfolioDimensionOption[] {
  return side === 'receivables' ? RECEIVABLES_PORTFOLIO_DIMENSIONS : PAYABLES_PORTFOLIO_DIMENSIONS
}

export function portfolioDimensionLabel(side: AgingPortfolioSide, id: string): string {
  return portfolioDimensionsFor(side).find(d => d.id === id)?.label ?? id
}
