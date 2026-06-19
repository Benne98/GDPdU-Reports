import type {
  PayablesSupplierRegisterRow,
  ReceivablesCustomerRegisterRow,
  ReceivablesCustomerScatterRow,
} from '../../../lib/api'
import OperationalCard from '../operational/OperationalCard'
import PlExportMenu, { type PlExportKind } from '../../financials/pl-two-view/PlExportMenu'
import ReceivablesCustomerScatter from './ReceivablesCustomerScatter'
import AgingChartFrame from './shared/AgingChartFrame'
import { exportCustomerRiskMatrix } from './shared/customerRiskMatrixExport'

export default function ReceivablesCustomerRiskSection({
  scatter,
  register,
  payablesRegister,
  selectedCustomer,
  selectedCustomerId,
  onSelect,
  year,
  month,
  periodLabel,
  entity,
  loading,
  title = 'Customer risk matrix',
  subtitle = 'Each customer mapped by open trade receivables vs overdue exposure — click a bubble to filter the register',
  emptyMessage = 'No customer data',
  context = 'receivables',
}: {
  scatter: ReceivablesCustomerScatterRow[]
  register?: ReceivablesCustomerRegisterRow[]
  payablesRegister?: PayablesSupplierRegisterRow[]
  selectedCustomer: string | null
  selectedCustomerId?: string | null
  onSelect: (name: string | null, customerId?: string | null) => void
  year: number
  month: number
  periodLabel: string
  entity?: string
  loading?: boolean
  title?: string
  subtitle?: string
  emptyMessage?: string
  context?: 'receivables' | 'payables'
}) {  async function handleMatrixExport(kind: PlExportKind) {
    if (!scatter.length) return
    await exportCustomerRiskMatrix({ kind, data: scatter, periodLabel })
  }

  return (
    <OperationalCard
      title={title}
      subtitle={subtitle}
      headerRight={
        <PlExportMenu
          formats={['pptx', 'xlsx']}
          onExport={handleMatrixExport}
          disabled={loading || scatter.length === 0}
        />
      }
    >
      <AgingChartFrame
        hasData={scatter.length > 0}
        loading={loading ?? false}
        height={420}
        emptyMessage={emptyMessage}
      >
        <ReceivablesCustomerScatter
          data={scatter}
          registerRows={register}
          payablesRegisterRows={payablesRegister}
          selectedCustomer={selectedCustomer}
          selectedCustomerId={selectedCustomerId}
          onSelect={onSelect}
          year={year}
          month={month}
          entity={entity}
          periodLabel={periodLabel}
          context={context}
        />      </AgingChartFrame>
    </OperationalCard>
  )
}
