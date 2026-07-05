import type { ReactNode } from 'react'
import OperationalCard from '../../operational/OperationalCard'

export default function AgingPortfolioCharts({
  periodLabel,
  reconciliation,
  reconciliationGlLabel,
  statusSplit,
  statusDonut,
  bucketChart,
  distributionDonut,
}: {
  periodLabel: string
  reconciliation: 'subledger' | 'scaled' | 'synthetic' | 'opos_method_a'
  reconciliationGlLabel: string
  statusSplit?: boolean
  statusDonut: ReactNode
  bucketChart: ReactNode
  distributionDonut: ReactNode
}) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-stretch">
      {statusSplit && (
        <OperationalCard
          title="Due status"
          subtitle="Not yet due vs overdue"
          className="lg:col-span-2 min-w-0"
        >
          <div className="flex justify-center w-full min-w-0">{statusDonut}</div>
        </OperationalCard>
      )}

      <OperationalCard
        title="Aging buckets"
        subtitle={`By due date · ${periodLabel}`}
        className={statusSplit ? 'lg:col-span-8' : 'lg:col-span-10'}
      >
        {reconciliation !== 'subledger' && (
          <p className="text-[10px] mb-2 -mt-1" style={{ color: '#94A3B8' }}>
            Demo: amounts aligned to GL {reconciliationGlLabel}
            {reconciliation === 'scaled' ? ' (scaled from subledger)' : ''}.
          </p>
        )}
        {bucketChart}
      </OperationalCard>

      <OperationalCard
        title="Distribution"
        subtitle="Share by bucket"
        className="lg:col-span-2 min-w-0"
      >
        <div className="flex justify-center w-full min-w-0">{distributionDonut}</div>
      </OperationalCard>
    </div>
  )
}
