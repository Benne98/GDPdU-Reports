import { Building2, Clock, RefreshCw } from 'lucide-react'

import type { Entity } from '../../lib/api'
import { stripLegalForm } from '../../lib/stripLegalForm'

import {
  monthLabelShort,
  periodCacheKey,
  rollingIsoWeeks,
  rollingMonths,
  rollingYears,
  weekLabelShort,
  type LatestPeriodInfo,
  type PeriodGrain,
  type PeriodSelection,
} from '../../lib/periodSelection'



type Props = {
  entities?: Entity[]
  entity?: string
  onEntityChange?: (code: string) => void
  grain: PeriodGrain
  onGrainChange: (g: PeriodGrain) => void
  period: PeriodSelection
  onPeriodChange: (p: PeriodSelection) => void
  latest: LatestPeriodInfo | null
  loading?: boolean
  onRefresh?: () => void
  monthCount?: number
  weekCount?: number
  /** When true, omit outer card chrome (for embedding in SalesFiltersCard). */
  embedded?: boolean
  /** When true, show an additional "Annual" pill for year-grain selection (P&L only). */
  showYearGrain?: boolean
}



const LABEL_COL = 'w-[3.25rem] shrink-0 text-xs font-medium'

const ICON_COL = 'w-[13px] shrink-0 flex justify-center'



export default function ModulePeriodFilterBar({
  entities,
  entity,
  onEntityChange,
  grain,
  onGrainChange,
  period,
  onPeriodChange,
  latest,
  loading,
  onRefresh,
  monthCount = 5,
  weekCount = 12,
  embedded = false,
  showYearGrain = false,
}: Props) {
  const anchorYear = latest?.year ?? 2025
  const anchorMonth = latest?.month ?? 7
  const anchorIsoYear = latest?.iso_year ?? anchorYear
  const anchorIsoWeek = latest?.iso_week ?? 30

  const monthOptions = rollingMonths(anchorYear, anchorMonth, monthCount)
  const weekOptions = rollingIsoWeeks(anchorIsoYear, anchorIsoWeek, weekCount)
  const yearOptions = rollingYears(anchorYear, anchorMonth, 4)

  const pillStyle = (active: boolean) => ({
    background: active ? 'rgba(30,58,95,0.1)' : '#F4F6F9',
    color: active ? '#1E3A5F' : '#475569',
    border: `1px solid ${active ? 'rgba(30,58,95,0.25)' : '#E2E8F0'}`,
  })

  const periodPillClass =
    'px-2.5 py-1.5 rounded-md text-xs font-medium transition-all min-w-[58px]'

  const secondRowLabel = grain === 'month' ? 'Month' : grain === 'year' ? 'Year' : 'Week'

  const inner = (
      <div className="flex items-start justify-between gap-4 flex-wrap">

        <div className="flex flex-wrap items-start gap-3 min-w-0 flex-1">

          {entities && onEntityChange ? (
            <>
              <div className="flex items-center gap-2 flex-wrap">
                <Building2 size={13} className="shrink-0" style={{ color: '#94A3B8' }} />
                <span className="text-xs font-medium shrink-0" style={{ color: '#475569' }}>
                  Entity
                </span>
                <select
                  value={entity ?? 'all'}
                  onChange={e => onEntityChange(e.target.value)}
                  className="rounded-lg px-3 py-1.5 text-xs font-medium outline-none cursor-pointer"
                  style={{ background: '#F4F6F9', border: '1px solid #CBD5E1', color: '#111827' }}
                >
                  <option value="all">All entities</option>
                  {entities.map(e => (
                    <option key={e.legal_entity_code} value={e.legal_entity_code}>
                      {stripLegalForm(e.entity_name)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="w-px h-4 self-center shrink-0" style={{ background: '#E2E8F0' }} />
            </>
          ) : null}



          <div className="flex flex-col gap-2 min-w-0">

            <div className="flex items-center gap-2 flex-wrap">

              <div className={ICON_COL}>
                <Clock size={13} style={{ color: '#94A3B8' }} />
              </div>

              <span className={LABEL_COL} style={{ color: '#475569' }}>
                View
              </span>

              <div className="flex flex-wrap gap-1">

                {showYearGrain && (
                  <button
                    type="button"
                    onClick={() => {
                      onGrainChange('year')
                      const y = yearOptions[yearOptions.length - 1]
                      onPeriodChange(y)
                    }}
                    className={periodPillClass}
                    style={pillStyle(grain === 'year')}
                  >
                    Annual
                  </button>
                )}

                <button
                  type="button"
                  onClick={() => {
                    onGrainChange('month')
                    const m = monthOptions[monthOptions.length - 1]
                    onPeriodChange(m)
                  }}
                  className={periodPillClass}
                  style={pillStyle(grain === 'month')}
                >
                  Monthly
                </button>

                <button
                  type="button"
                  onClick={() => {
                    onGrainChange('week')
                    const w = weekOptions[weekOptions.length - 1]
                    onPeriodChange(w)
                  }}
                  className={periodPillClass}
                  style={pillStyle(grain === 'week')}
                >
                  Weekly
                </button>

              </div>

            </div>



            <div className="flex items-center gap-2 flex-wrap">

              <div className={ICON_COL} aria-hidden />

              <span className={LABEL_COL} style={{ color: '#475569' }}>
                {secondRowLabel}
              </span>

              <div className="flex flex-wrap gap-1">

                {grain === 'month'
                  ? monthOptions.map(m => {
                      const active =
                        period.grain === 'month' && period.year === m.year && period.month === m.month
                      return (
                        <button
                          key={periodCacheKey(m)}
                          type="button"
                          onClick={() => onPeriodChange(m)}
                          className={periodPillClass}
                          style={pillStyle(active)}
                        >
                          {monthLabelShort(m.year, m.month)}
                        </button>
                      )
                    })
                  : grain === 'year'
                  ? (
                    <>
                      {yearOptions.map(y => {
                        const active =
                          period.grain === 'year' && period.year === y.year
                        return (
                          <button
                            key={periodCacheKey(y)}
                            type="button"
                            onClick={() => onPeriodChange({ grain: 'year', year: y.year, month: period.grain === 'year' ? period.month : anchorMonth })}
                            className={periodPillClass}
                            style={pillStyle(active)}
                          >
                            {`FY${String(y.year).slice(-2)}`}
                          </button>
                        )
                      })}
                      <div className="w-px h-4 self-center shrink-0 mx-1" style={{ background: '#E2E8F0' }} />
                      <select
                        value={period.grain === 'year' ? period.month : anchorMonth}
                        onChange={e => {
                          const newMonth = Number(e.target.value)
                          const activeYear = period.grain === 'year' ? period.year : anchorYear
                          onPeriodChange({ grain: 'year', year: activeYear, month: newMonth })
                        }}
                        className="rounded-md px-2 py-1.5 text-xs font-medium outline-none cursor-pointer"
                        style={{ background: '#F4F6F9', border: '1px solid #E2E8F0', color: '#475569' }}
                        title="YTD/LTM anchor month"
                      >
                        {rollingMonths(anchorYear, anchorMonth, 12).map(m => (
                          <option key={m.month} value={m.month}>
                            {monthLabelShort(m.year, m.month)}
                          </option>
                        ))}
                      </select>
                    </>
                  )
                  : weekOptions.map(w => {
                      const active =
                        period.grain === 'week' &&
                        period.isoYear === w.isoYear &&
                        period.isoWeek === w.isoWeek
                      return (
                        <button
                          key={periodCacheKey(w)}
                          type="button"
                          onClick={() => onPeriodChange(w)}
                          className={periodPillClass}
                          style={pillStyle(active)}
                        >
                          {weekLabelShort(w.isoYear, w.isoWeek)}
                        </button>
                      )
                    })}

              </div>

            </div>

          </div>

        </div>



        {onRefresh ? (

          <div className="flex items-center gap-2 shrink-0">

            <button
              type="button"
              onClick={onRefresh}
              disabled={loading}
              className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{ background: '#F4F6F9', color: '#475569', border: '1px solid #E2E8F0' }}
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
              {loading ? 'Loading…' : 'Refresh'}
            </button>

          </div>

        ) : null}

      </div>
  )

  if (embedded) return inner

  return (
    <div
      className="rounded-xl mb-6 px-5 py-4"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
    >
      {inner}
    </div>
  )
}
