import type { PeriodSelection } from '../../../lib/periodSelection'
import PlSectionHeading from '../pl-two-view/PlSectionHeading'
import TopCustomerTable from '../../cockpit/TopCustomerTable'
import TopSupplierTable from '../../cockpit/TopSupplierTable'

type Props = {
  period: PeriodSelection
  entity?: string
}

export default function OverviewMarketWatch({ period, entity }: Props) {
  return (
    <div>
      <div className="mb-4">
        <PlSectionHeading>Market watch</PlSectionHeading>
        <p className="text-xs mt-1 m-0" style={{ color: '#94A3B8' }}>
          Top customers and suppliers with report and table views
        </p>
      </div>
      <div className="space-y-5">
        <TopCustomerTable period={period} entity={entity} />
        <TopSupplierTable period={period} entity={entity} />
      </div>
    </div>
  )
}
