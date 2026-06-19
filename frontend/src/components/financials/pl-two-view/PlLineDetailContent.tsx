import PlDetailOutlierPanel from './PlDetailOutlierPanel'
import PlLineDetailCharts from './PlLineDetailCharts'
import type { FinStatementKind } from '../statement-two-view/statementTypes'
import { useStatementLineDetailLoad } from '../statement-two-view/useStatementLineDetailLoad'

type Props = {
  statement?: FinStatementKind
  lineCode: string
  label: string
  year: number
  month: number
  anchorYear?: number
  anchorMonth?: number
  entity?: string
  lineMomKeur?: number
  cellAmountKeur?: number
  variant?: 'full' | 'chartsOnly'
  sharedLoad?: ReturnType<typeof useStatementLineDetailLoad>
}

export default function PlLineDetailContent({
  lineCode,
  label,
  year,
  month,
  anchorYear: _anchorYear,
  anchorMonth: _anchorMonth,
  entity,
  lineMomKeur,
  cellAmountKeur,
  variant = 'full',
  sharedLoad,
  statement = 'pl',
}: Props) {
  const internalLoad = useStatementLineDetailLoad({
    statement,
    lineCode,
    label,
    year,
    month,
    entity,
    lineMomKeur,
  })
  const load = sharedLoad ?? internalLoad
  const {
    detail,
    loading,
    fetchError,
    currentPeriodLabel,
    priorPeriodLabel,
    expertContext,
  } = load

  if (fetchError) {
    return (
      <p className="text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-4 text-center">
        Detail data could not be loaded for this line and period.
        {fetchError.includes('API error') ? (
          <span className="block mt-2 text-[0.65rem] text-rose-600/90 font-mono break-all">{fetchError}</span>
        ) : null}
      </p>
    )
  }

  if (variant === 'chartsOnly') {
    return <PlLineDetailCharts load={load} />
  }

  return (
    <div className="space-y-4">
      <section
        className="rounded-xl border border-slate-200/80 bg-slate-50/40 overflow-hidden"
        style={{ maxHeight: 'min(32vh, 280px)' }}
      >
        <div className="px-3 py-2 border-b border-slate-100 bg-slate-50/80 shrink-0">
          <p className="text-xs font-semibold text-slate-800">Explanation</p>
        </div>
        <div className="overflow-y-auto px-3 py-3" style={{ scrollbarGutter: 'stable' }}>
          <PlDetailOutlierPanel
            detail={detail}
            loading={loading}
            currentPeriodLabel={currentPeriodLabel}
            priorPeriodLabel={priorPeriodLabel}
          />
          {!loading && (
            <p className="text-xs leading-relaxed text-slate-600 mt-3 pt-3 border-t border-slate-200/80">
              {expertContext}
            </p>
          )}
          {!loading && cellAmountKeur != null && Math.abs(cellAmountKeur) > 0.01 && (
            <p className="text-[0.65rem] text-slate-500 mt-2 tabular-nums">
              Line total in table ({currentPeriodLabel}): €{' '}
              {Math.abs(cellAmountKeur).toLocaleString('de-DE', { maximumFractionDigits: 0 })}k
            </p>
          )}
        </div>
      </section>

      <PlLineDetailCharts load={load} compact />
    </div>
  )
}
