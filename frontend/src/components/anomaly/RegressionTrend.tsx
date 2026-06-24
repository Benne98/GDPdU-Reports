import { Line } from 'recharts'

export interface RegressionData {
  slope: number
  intercept: number
  r_squared: number
  trend_start: number
  trend_end: number
}

/** Build an array of {trend} for each series point index, linearly interpolating from trend_start to trend_end. */
export function buildTrendSeries(regression: RegressionData, n: number): Array<{ trend: number }> {
  if (n === 0) return []
  return Array.from({ length: n }, (_, i) => ({
    trend: regression.trend_start + (regression.trend_end - regression.trend_start) * (i / Math.max(n - 1, 1)),
  }))
}

/** Drop-in <Line> for a ComposedChart. Merge trendSeries data into chartData before passing to ComposedChart. */
export function RegressionTrendLine({ r_squared }: { r_squared: number }) {
  return (
    <Line
      type="linear"
      dataKey="trend"
      stroke="#F59E0B"
      strokeWidth={1.5}
      strokeDasharray="5 3"
      dot={false}
      name={`Trend (R²=${r_squared.toFixed(2)})`}
      legendType="line"
    />
  )
}
