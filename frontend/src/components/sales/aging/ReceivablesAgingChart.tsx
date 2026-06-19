import type { ReceivablesAgingBand } from '../../../lib/api'
import AgingBucketChart from './shared/AgingBucketChart'

export default function ReceivablesAgingChart(props: {
  series: ReceivablesAgingBand[]
  totalReceivables: number
  selectedBand: string | null
  onBandSelect: (band: string) => void
}) {
  return (
    <AgingBucketChart
      series={props.series}
      totalBalance={props.totalReceivables}
      selectedBand={props.selectedBand}
      onBandSelect={props.onBandSelect}
      emptyMessage="No receivables aging data for this period"
      gradIdPrefix="recv"
      compact
    />
  )
}
