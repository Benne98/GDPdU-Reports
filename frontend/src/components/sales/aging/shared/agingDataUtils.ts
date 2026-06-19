export type AgingAmountBand = { amount: number }

export function seriesAmountSum(series: AgingAmountBand[]): number {
  return series.reduce((sum, b) => sum + (Number(b.amount) || 0), 0)
}

/** True when there is anything meaningful to chart (avoid false "No data"). */
export function hasAgingChartData(series: AgingAmountBand[], balanceTotal?: number): boolean {
  if (!series.length) return false
  if (seriesAmountSum(series) > 0) return true
  return (balanceTotal ?? 0) > 0
}

export function effectiveAgingTotal(series: AgingAmountBand[], balanceTotal: number): number {
  const sum = seriesAmountSum(series)
  return balanceTotal > 0 ? balanceTotal : sum
}
