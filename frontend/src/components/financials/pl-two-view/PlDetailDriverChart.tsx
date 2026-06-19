import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

type AccountRow = {
  gl_account_id: string
  account_name: string
  balance_cm: number
  balance_pm: number
  delta: number
}

type Props = {
  accounts: AccountRow[]
  currentPeriodLabel: string
  priorPeriodLabel: string
}

function deltaLabel(row: AccountRow): string {
  const d = row.delta
  const pm = row.balance_pm
  const pct = Math.abs(pm) > 0.01 ? (d / Math.abs(pm)) * 100 : null
  const absStr = `${d >= 0 ? '+' : ''}€ ${Math.abs(d).toLocaleString('de-DE', { maximumFractionDigits: 0 })}k`
  if (pct == null || !Number.isFinite(pct)) return absStr
  const pctStr = `${pct >= 0 ? '+' : ''}${pct.toFixed(0)}%`
  return `${absStr} (${pctStr})`
}

function DeltaEndLabel(
  props: Record<string, unknown> & {
    data: Array<{ deltaLabel: string; delta: number }>
  },
) {
  const x = Number(props.x ?? 0)
  const y = Number(props.y ?? 0)
  const width = Number(props.width ?? 0)
  const height = Number(props.height ?? 0)
  const index = Number(props.index ?? 0)
  const { data } = props
  const row = data[index]
  if (!row) return null

  const positive = row.delta >= 0
  const fill = positive ? '#047857' : '#BE123C'
  const bg = positive ? 'rgba(16, 185, 129, 0.18)' : 'rgba(244, 63, 94, 0.18)'
  const text = row.deltaLabel
  const padX = 5
  const padY = 3
  const textW = Math.max(52, text.length * 5.2)
  const boxH = 14
  const boxX = x + width + 6
  const boxY = y + height / 2 - boxH / 2

  return (
    <g>
      <rect x={boxX} y={boxY} width={textW + padX * 2} height={boxH + padY} rx={4} fill={bg} />
      <text
        x={boxX + padX}
        y={boxY + boxH / 2 + 3}
        fill={fill}
        fontSize={9}
        fontWeight={600}
        textAnchor="start"
      >
        {text}
      </text>
    </g>
  )
}

export default function PlDetailDriverChart({
  accounts,
  currentPeriodLabel,
  priorPeriodLabel,
}: Props) {
  const chartData = [...accounts]
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    .filter(a => Math.abs(a.balance_cm) > 0.01 || Math.abs(a.balance_pm) > 0.01)
    .slice(0, 12)
    .map(a => ({
      name: a.account_name || a.gl_account_id,
      priorAbs: Math.abs(a.balance_pm),
      currentAbs: Math.abs(a.balance_cm),
      delta: a.delta,
      deltaLabel: deltaLabel(a),
    }))

  if (!chartData.length) {
    return (
      <p className="text-xs text-slate-500 rounded-lg border border-dashed border-slate-200 px-3 py-6 text-center">
        No account balances in {currentPeriodLabel}.
      </p>
    )
  }

  const maxVal = Math.max(...chartData.flatMap(d => [d.priorAbs, d.currentAbs]), 1)
  const height = Math.max(140, chartData.length * 36)
  const rightMargin = 132

  return (
    <div style={{ height }} className="w-full min-h-[140px]">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={chartData}
          layout="vertical"
          margin={{ top: 4, right: rightMargin, left: 4, bottom: 4 }}
          barGap={-10}
        >
          <XAxis type="number" domain={[0, maxVal * 1.08]} hide />
          <YAxis
            type="category"
            dataKey="name"
            width={112}
            tick={{ fontSize: 9, fill: '#475569' }}
            tickFormatter={(v: string) => (v.length > 20 ? `${v.slice(0, 18)}…` : v)}
          />
          <Tooltip
            formatter={(v: number, name: string) => [
              `€ ${v.toLocaleString('de-DE')}k`,
              name === 'priorAbs' ? priorPeriodLabel : currentPeriodLabel,
            ]}
            contentStyle={{ fontSize: 11, borderRadius: 8 }}
          />
          <Bar
            dataKey="priorAbs"
            name={priorPeriodLabel}
            fill="#94A3B8"
            fillOpacity={0.35}
            radius={[0, 3, 3, 0]}
            barSize={18}
          />
          <Bar
            dataKey="currentAbs"
            name={currentPeriodLabel}
            fill="#1E3A5F"
            radius={[0, 4, 4, 0]}
            barSize={14}
          >
            {chartData.map((_, i) => (
              <Cell key={`cur-${i}`} fill="#1E3A5F" />
            ))}
            <LabelList content={(props) => <DeltaEndLabel {...props} data={chartData} />} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
