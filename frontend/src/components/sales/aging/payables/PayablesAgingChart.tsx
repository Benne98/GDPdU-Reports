import type { PayablesAgingBand } from '../../../../lib/api'
import AgingBucketChart from '../shared/AgingBucketChart'

export default function PayablesAgingChart(props: {
  series: PayablesAgingBand[]
  totalPayables: number
  selectedBand: string | null
  onBandSelect: (band: string) => void
}) {
  return (
    <AgingBucketChart
      series={props.series}
      totalBalance={props.totalPayables}
      selectedBand={props.selectedBand}
      onBandSelect={props.onBandSelect}
      emptyMessage="No payables aging data for this period"
      gradIdPrefix="ap"
      compact
    />
  )
}


