import { useEffect } from 'react'
import { X, Download, TrendingDown, TrendingUp } from 'lucide-react'
import type { PlIntroFacts } from '../../../lib/api'
import { exportPlLineDetail } from './plExport'
import type { PlNarrativeBullet } from './plNarrativeEngine'
import PlDetailOutlierPanel from './PlDetailOutlierPanel'
import PlLineDetailCharts from './PlLineDetailCharts'
import type { FinStatementKind } from '../statement-two-view/statementTypes'
import { useStatementLineDetailLoad } from '../statement-two-view/useStatementLineDetailLoad'

type Props = {
  bullet: PlNarrativeBullet
  year: number
  month: number
  entity?: string
  statement?: FinStatementKind
  narrativeContext?: {
    headline?: string
    intro?: string
    intro_facts?: PlIntroFacts
  }
  onClose: () => void
}

function fmtKpiKeur(v: number | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}€ ${Math.abs(v).toLocaleString('de-DE', { maximumFractionDigits: 0 })}k`
}

function fmtLevelKeur(v: number): string {
  return `€ ${Math.abs(v).toLocaleString('de-DE', { maximumFractionDigits: 0 })}k`
}

export default function PlDetailOverlay({
  bullet,
  year,
  month,
  entity,
  statement = 'pl',
  onClose,
}: Props) {
  const load = useStatementLineDetailLoad({
    statement,
    lineCode: bullet.line_code,
    label: bullet.label,
    year,
    month,
    entity,
    lineMomKeur: bullet.mom_keur,
  })

  const { detail, loading, currentPeriodLabel, priorPeriodLabel, expertContext, lineTotals } = load

  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [])

  const momKeur = bullet.mom_keur
  const momPositive = momKeur != null && momKeur >= 0
  const momPct =
    lineTotals.momPct != null && Number.isFinite(lineTotals.momPct) ? lineTotals.momPct : null

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-2 sm:p-4"
      style={{ background: 'rgba(15, 23, 42, 0.6)', backdropFilter: 'blur(6px)' }}
      role="dialog"
      aria-modal="true"
      onClick={e => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className="flex flex-col w-full max-w-[min(98vw,1320px)] rounded-2xl overflow-hidden shadow-2xl ring-1 ring-slate-200/50"
        style={{ background: '#FFFFFF', maxHeight: '94vh' }}
        onClick={e => e.stopPropagation()}
      >
        <header className="shrink-0 border-b border-slate-200 bg-gradient-to-r from-slate-50 to-white">
          <div className="px-4 sm:px-5 py-4">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <h2 className="text-lg font-semibold tracking-tight text-slate-900">
                  {bullet.label}
                  <span className="text-slate-500 font-medium ml-2">{currentPeriodLabel}</span>
                </h2>
                <div className="flex flex-wrap gap-2 mt-3">
                  {momKeur != null && (
                    <span
                      className={`inline-flex items-center gap-1 text-xs font-semibold tabular-nums px-2.5 py-1 rounded-lg ${
                        momPositive ? 'bg-emerald-50 text-emerald-800' : 'bg-rose-50 text-rose-800'
                      }`}
                    >
                      {momPositive ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                      Change vs {priorPeriodLabel}: {fmtKpiKeur(momKeur)}
                    </span>
                  )}
                  {detail && Math.abs(lineTotals.cmKeur) > 0.01 && (
                    <span className="text-xs font-medium tabular-nums text-slate-700 bg-slate-100 px-2.5 py-1 rounded-lg">
                      {currentPeriodLabel}: {fmtLevelKeur(lineTotals.cmKeur)}
                    </span>
                  )}
                  {detail && Math.abs(lineTotals.pmKeur) > 0.01 && (
                    <span className="text-xs font-medium tabular-nums text-slate-600 bg-slate-100 px-2.5 py-1 rounded-lg">
                      {priorPeriodLabel}: {fmtLevelKeur(lineTotals.pmKeur)}
                    </span>
                  )}
                  {momPct != null && (
                    <span className="text-xs font-medium tabular-nums text-slate-600 bg-slate-100 px-2.5 py-1 rounded-lg">
                      {momPct >= 0 ? '+' : ''}
                      {momPct}% vs {priorPeriodLabel}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {detail && (
                  <button
                    type="button"
                    onClick={() => void exportPlLineDetail(bullet.label, detail.accounts, detail.top_bookings)}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors"
                  >
                    <Download size={14} /> Export
                  </button>
                )}
                <button
                  type="button"
                  onClick={onClose}
                  className="p-2 rounded-lg hover:bg-slate-100 text-slate-500"
                  aria-label="Close"
                >
                  <X size={20} />
                </button>
              </div>
            </div>
          </div>
        </header>

        <div className="flex-1 min-h-0 flex flex-col lg:flex-row px-4 sm:px-5 pb-4 sm:pb-5 pt-3 gap-3 lg:gap-4">
          <div className="flex flex-col min-h-0 w-full lg:w-[35%] lg:shrink-0 lg:max-w-[35%] overflow-hidden">
            <section className="flex flex-col flex-1 min-h-0 rounded-xl border border-slate-200/80 bg-white overflow-hidden">
              <div className="px-3 py-2.5 border-b border-slate-100 shrink-0 bg-slate-50/80">
                <p className="text-sm font-semibold text-slate-800">Line commentary</p>
              </div>
              <div
                className="flex-1 overflow-y-auto px-3 py-3 bg-slate-50/30"
                style={{ scrollbarGutter: 'stable' }}
              >
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
              </div>
            </section>
          </div>

          <section className="flex-1 min-h-0 min-w-0 w-full lg:w-[65%] lg:max-w-[65%] overflow-y-auto pr-0.5">
            <PlLineDetailCharts load={load} />
          </section>
        </div>
      </div>
    </div>
  )
}
