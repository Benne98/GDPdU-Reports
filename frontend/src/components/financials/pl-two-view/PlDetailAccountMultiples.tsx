import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { PlLineDetailAccountTimeline } from '../../../lib/api'

type Props = {
  accounts: PlLineDetailAccountTimeline[]
  highlightPeriodLabel?: string
}

const CHART_H = 64

export default function PlDetailAccountMultiples({ accounts, highlightPeriodLabel }: Props) {
  if (!accounts.length) {
    return (
      <p className="text-xs text-slate-500 rounded-xl border border-dashed border-slate-200 px-3 py-4 text-center">
        No account history for this scope.
      </p>
    )
  }

  return (
    <div className="space-y-2.5">
      {accounts.map(acc => (
        <div
          key={acc.gl_account_id}
          className="rounded-xl border border-slate-200/70 bg-slate-50/50 px-2.5 py-2"
        >
          <p className="text-[0.65rem] font-medium text-slate-700 truncate mb-1" title={acc.account_name}>
            {acc.account_name}
          </p>
          <div style={{ height: CHART_H }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={acc.series} margin={{ top: 2, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 2" stroke="#E2E8F0" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 7, fill: '#64748B' }} interval={0} />
                <YAxis tick={{ fontSize: 7, fill: '#64748B' }} width={32} />
                <Tooltip
                  formatter={(v: number) => [`€ ${v.toLocaleString('de-DE')}k`, 'Balance']}
                  contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #E2E8F0' }}
                />
                <Bar dataKey="value_keur" radius={[3, 3, 0, 0]}>
                  {acc.series.map(p => (
                    <Cell
                      key={p.label}
                      fill={p.label === highlightPeriodLabel ? '#1E3A5F' : '#CBD5E1'}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      ))}
    </div>
  )
}
