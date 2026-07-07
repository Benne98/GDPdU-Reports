import { CartesianGrid, Label, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { PayablesConcentrationTrendPoint } from '../../../../lib/api'
import {
  AGING_CONCENTRATION_BANDS,
  concentrationBandColor,
  sortConcentrationSegments,
} from '../shared/agingConcentrationBands'
import {
  concentrationAgingBucketLabel,
  type ConcentrationAgingBucketId,
} from '../shared/agingConcentrationTrendConfig'
import { normalizePayablesConcentration } from '../shared/normalizeConcentration'

function toChartRows(points: PayablesConcentrationTrendPoint[]) {
  return points.map(p => {
    const normalized = normalizePayablesConcentration({
      year: p.year,
      month: p.month,
      total: p.segments?.reduce((s, seg) => s + seg.amount, 0) ?? 0,
      segments: p.segments,
    })
    const row: Record<string, string | number> = { label: p.label }
    for (const seg of sortConcentrationSegments(normalized?.segments ?? [])) {
      row[seg.band] = seg.pct
    }
    return row
  })
}

export default function PayablesConcentrationTrend({
  points,
  periodGrain,
  agingBucket = 'all',
}: {
  points: PayablesConcentrationTrendPoint[]
  periodGrain: 'month' | 'week' | 'year'
  agingBucket?: ConcentrationAgingBucketId
}) {
  if (!points.length) {
    return (
      <div className="h-[242px] flex items-center justify-center text-sm" style={{ color: '#94A3B8' }}>
        No concentration trend
      </div>
    )
  }

  const data = toChartRows(points)
  const spanLabel = periodGrain === 'week' ? '12 weeks' : '12 months'
  const bucketLabel = concentrationAgingBucketLabel(agingBucket)

  return (
    <div>
      <p className="text-[10px] mb-2" style={{ color: '#94A3B8' }}>
        Share by rank band — {bucketLabel} — last {spanLabel}
      </p>
      <ResponsiveContainer width="100%" height={242}>
        <LineChart data={data} margin={{ top: 8, right: 16, left: 4, bottom: 8 }}>
          <CartesianGrid stroke="#E8EDF3" strokeDasharray="4 6" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#64748B' }} />
          <YAxis tick={{ fontSize: 11, fill: '#94A3B8' }} unit="%" width={44} domain={[0, 100]}>
            <Label
              value="Share %"
              angle={-90}
              position="insideLeft"
              offset={12}
              style={{ fontSize: 11, fontWeight: 600, fill: '#64748B', textAnchor: 'middle' }}
            />
          </YAxis>
          <Tooltip formatter={(v: number) => `${v}%`} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {AGING_CONCENTRATION_BANDS.map(({ band }) => {
            const label = points[0]?.segments?.find(s => s.band === band)?.label ?? band
            return (
              <Line
                key={band}
                type="monotone"
                dataKey={band}
                name={label}
                stroke={concentrationBandColor(band)}
                strokeWidth={2}
                dot={false}
              />
            )
          })}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
