import type { PayablesSupplierRegisterRow, PayablesSupplierScatterRow } from '../../../../lib/api'
import ReceivablesCustomerRiskSection from '../ReceivablesCustomerRiskSection'

export default function PayablesSupplierRiskSection({
  scatter,
  register,
  selectedSupplier,
  onSelect,
  year,
  month,
  periodLabel,
  entity,
  loading,
}: {
  scatter: PayablesSupplierScatterRow[]
  register: PayablesSupplierRegisterRow[]
  selectedSupplier: string | null
  onSelect: (name: string | null) => void
  year: number
  month: number
  periodLabel: string
  entity?: string
  loading?: boolean
}) {
  const mappedScatter = scatter.map(s => ({
    customer_name: s.supplier_name,
    balance: s.balance,
    overdue_pct: s.overdue_pct,
    credit_balance: s.credit_balance,
  }))

  return (
    <ReceivablesCustomerRiskSection
      scatter={mappedScatter}
      payablesRegister={register}
      selectedCustomer={selectedSupplier}
      onSelect={name => onSelect(name)}
      year={year}
      month={month}
      periodLabel={periodLabel}
      entity={entity}
      loading={loading}
      title="Supplier risk matrix"
      subtitle="Each supplier mapped by open trade payables vs overdue exposure — click a bubble to filter the register"
      emptyMessage="No supplier data"
      context="payables"
    />
  )
}
