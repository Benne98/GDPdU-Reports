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
  deltaByAccount?: Map<string, number>
}

const CM_HIGHLIGHT = '#0D9488'
const OTHER_COLOR = '#CBD5E1'

function fmtDeltaKeur(d: number): string {
  const sign = d >= 0 ? '+' : ''
  return `${sign}€ ${Math.abs(d).toLocaleString('de-DE', { maximumFractionDigits: 0 })}k`
}

function AccountMiniChart({
  account,
  highlightPeriodLabel,
  deltaKeur,
}: {
  account: PlLineDetailAccountTimeline
  highlightPeriodLabel?: string
  deltaKeur?: number
}) {
  return (
    <div className="shrink-0">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <p className="text-xs font-medium text-slate-700 truncate min-w-0" title={account.account_name}>
          {account.account_name}
        </p>
        {deltaKeur != null && Math.abs(deltaKeur) > 0.01 && (
          <span
            className={`shrink-0 text-[9px] font-semibold tabular-nums px-1.5 py-0.5 rounded ${
              deltaKeur >= 0 ? 'bg-emerald-50 text-emerald-800' : 'bg-rose-50 text-rose-800'
            }`}
          >
            {fmtDeltaKeur(deltaKeur)}
          </span>
        )}
      </div>
      <div className="h-[110px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={account.series} margin={{ top: 2, right: 2, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="2 2" stroke="#E2E8F0" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 7, fill: '#64748B' }}
              interval={0}
              angle={-45}
              textAnchor="end"
              height={36}
            />
            <YAxis tick={{ fontSize: 7, fill: '#64748B' }} width={32} domain={['auto', 'auto']} />
            <Tooltip
              formatter={(v: number) => [`€ ${v.toLocaleString('de-DE')}k`, 'Balance']}
              contentStyle={{ fontSize: 11, borderRadius: 8 }}
            />
            <Bar dataKey="value_keur" radius={[2, 2, 0, 0]} maxBarSize={14}>
              {account.series.map(p => (
                <Cell
                  key={p.label}
                  fill={p.label === highlightPeriodLabel ? CM_HIGHLIGHT : OTHER_COLOR}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

export default function PlDetailTrendChart({
  accounts,
  highlightPeriodLabel,
  deltaByAccount,
}: Props) {
  if (!accounts.length) return null

  return (
    <div
      className="max-h-[280px] overflow-y-auto pr-1 space-y-3 rounded-lg border border-slate-100 bg-slate-50/40 p-2"
      style={{ scrollbarGutter: 'stable' }}
    >
      {accounts.map(acc => (
        <AccountMiniChart
          key={acc.gl_account_id}
          account={acc}
          highlightPeriodLabel={highlightPeriodLabel}
          deltaKeur={deltaByAccount?.get(String(acc.gl_account_id))}
        />
      ))}
    </div>
  )
}
