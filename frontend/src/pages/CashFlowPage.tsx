import { useState } from 'react'
import StatementsPage from './StatementsPage'

/** Cash flow — indirect CF statement + Cash & debt sub-tab (Phase P11 placeholder). */
export default function CashFlowPage() {
  const [subTab, setSubTab] = useState('cash-flow')

  const cashDebtPlaceholder = (
    <div
      className="rounded-xl p-8 mt-2"
      style={{
        background: '#FFFFFF',
        border: '1px solid #E2E8F0',
        boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
      }}
    >
      <p
        className="text-xs font-semibold uppercase tracking-widest mb-3"
        style={{ color: '#1E3A5F' }}
      >
        Cash & debt
      </p>
      <h2 className="text-xl font-bold tracking-tight mb-2" style={{ color: '#111827' }}>
        Net debt, leverage &amp; liquidity bridge
      </h2>
      <p className="text-sm" style={{ color: '#475569' }}>
        Coming in a later phase — this section will show net debt evolution, leverage ratios
        and a liquidity bridge between reporting periods.
      </p>
    </div>
  )

  return (
    <StatementsPage
      statement="cf"
      kicker="Cash flow"
      title="Cash flow"
      description="Indirect cash flow statement with report and table view for {period}. Click a value to drill into GL lines."
      subTabs={[
        { id: 'cash-flow', label: 'Cash flow statement' },
        { id: 'cash-debt', label: 'Cash & debt' },
      ]}
      activeSubTab={subTab}
      onSubTabChange={setSubTab}
      subTabContent={subTab === 'cash-debt' ? cashDebtPlaceholder : undefined}
    />
  )
}
