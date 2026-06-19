import type { PlLineDetailResponse } from '../../../lib/api'
import DetailAnalysisLoader from '../../ui/DetailAnalysisLoader'
import PlDetailBookingsTable from './PlDetailBookingsTable'
import PlDetailDriverChart from './PlDetailDriverChart'
import PlDetailTrendChart from './PlDetailTrendChart'
import type { usePlLineDetailLoad } from './usePlLineDetailLoad'

type LoadState = ReturnType<typeof usePlLineDetailLoad>

type Props = {
  load: LoadState
  compact?: boolean
}

export default function PlLineDetailCharts({ load, compact }: Props) {
  const {
    detail,
    loading,
    currentPeriodLabel,
    priorPeriodLabel,
    emptyReason,
    deltaByAccount,
    accountsByDelta,
    timelineByDelta,
    timelineEmptyMessage,
  } = load

  const h3 = compact ? 'text-xs' : 'text-sm'

  if (loading) {
    return (
      <DetailAnalysisLoader
        compact={compact}
        message="Building charts and AI analysis…"
      />
    )
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className={`${h3} font-semibold text-slate-800 mb-1.5`}>
          Account drivers ({currentPeriodLabel} vs {priorPeriodLabel})
        </h3>
        <PlDetailDriverChart
          accounts={accountsByDelta}
          currentPeriodLabel={currentPeriodLabel}
          priorPeriodLabel={priorPeriodLabel}
        />
      </div>

      <div>
        <h3 className={`${h3} font-semibold text-slate-800 mb-2`}>12-month trend by account</h3>
        {timelineByDelta.length > 0 ? (
          <>
            {emptyReason === 'history_missing' && (
              <p className="text-[10px] text-amber-800 bg-amber-50 border border-amber-200/80 rounded-lg px-2.5 py-1.5 mb-2">
                {timelineEmptyMessage('history_missing')}
              </p>
            )}
            <PlDetailTrendChart
              accounts={timelineByDelta}
              highlightPeriodLabel={currentPeriodLabel}
              deltaByAccount={deltaByAccount}
            />
          </>
        ) : (
          <p className="text-xs text-slate-500 rounded-lg border border-dashed border-slate-200 px-3 py-5 text-center">
            {timelineEmptyMessage(emptyReason)}
          </p>
        )}
      </div>

      <div>
        <h3 className={`${h3} font-semibold text-slate-800 mb-2`}>Postings · {currentPeriodLabel}</h3>
        <PlDetailBookingsTable bookings={(detail as PlLineDetailResponse | null)?.top_bookings ?? []} />
      </div>
    </div>
  )
}
