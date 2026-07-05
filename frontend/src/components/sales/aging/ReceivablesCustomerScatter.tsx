import { useMemo } from 'react'
import {
  CartesianGrid,
  Customized,
  Label,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'
import type { PayablesSupplierRegisterRow, ReceivablesCustomerRegisterRow, ReceivablesCustomerScatterRow } from '../../../lib/api'
import { fmtAmount, fmtChartKpi } from '../../../lib/fmt'
import { placeScatterLabels, type PlacedScatterLabel, type ScatterLabelPoint } from '../analytics/scatterLabelPlacement'
import {
  CHART_SCATTER_LABEL_EMPHASIS_STYLE,
  CHART_SCATTER_LABEL_STYLE,
} from '../analytics/salesChartTypography'
import { BRAND } from '../analytics/salesChartTheme'
import ReceivablesCustomerRegister from './ReceivablesCustomerRegister'
import PayablesSupplierRegister from './payables/PayablesSupplierRegister'
import {
  buildCustomerRiskMatrix,
  CUSTOMER_RISK_QUADRANTS,
  overdueAmount,
  quadrantMeta,
  selectMatrixLabelCustomers,
  type CustomerRiskInsights,
  type CustomerRiskPoint,
} from './shared/customerRiskMatrix'

const CHART_MARGIN = { top: 36, right: 36, left: 52, bottom: 44 }

type AxisMapEntry = { scale?: (v: number) => number }

function getScale(axisMap: Record<string, AxisMapEntry> | undefined): ((v: number) => number) | null {
  if (!axisMap) return null
  const entry = Object.values(axisMap)[0]
  return typeof entry?.scale === 'function' ? entry.scale.bind(entry) : null
}

function RiskTooltip({
  active,
  payload,
  context = 'receivables',
}: {
  active?: boolean
  payload?: Array<{ payload: CustomerRiskPoint }>
  context?: 'receivables' | 'payables'
}) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  const q = quadrantMeta(p.quadrant)
  const overdueAmt = overdueAmount(p)
  return (
    <div
      className="rounded-xl px-3 py-2.5 shadow-xl text-xs max-w-[220px]"
      style={{
        background: '#FFFFFF',
        border: `1px solid ${q.stroke}44`,
        boxShadow: '0 8px 24px rgba(15,23,42,0.12)',
      }}
    >
      <p className="font-semibold mb-1 leading-snug" style={{ color: BRAND.text }}>
        {p.customer_name}
      </p>
      <p className="text-[10px] mb-2 font-medium uppercase tracking-wide" style={{ color: q.stroke }}>
        {q.label} · {q.action}
      </p>
      <div className="space-y-1 tabular-nums" style={{ color: BRAND.textSecondary }}>
        <p>{context === 'payables' ? 'Open payables' : 'Open receivables'}: <span className="font-semibold" style={{ color: BRAND.navy }}>{fmtAmount(p.balance)}</span></p>
        <p>Overdue share: <span className="font-semibold" style={{ color: BRAND.navy }}>{p.overdue_pct}%</span></p>
        <p>Overdue amount: <span className="font-semibold">{fmtAmount(overdueAmt)}</span></p>
      </div>
    </div>
  )
}

function RiskDot(props: {
  cx?: number
  cy?: number
  payload?: CustomerRiskPoint
  selectedCustomer: string | null
  selectedCustomerId?: string | null
}) {
  const { cx, cy, payload, selectedCustomer, selectedCustomerId } = props
  if (cx == null || cy == null || !payload) return null
  const q = quadrantMeta(payload.quadrant)
  const selected = selectedCustomerId
    ? payload.customer_id === selectedCustomerId
    : payload.customer_name === selectedCustomer
  const dimmed = selectedCustomer != null && !selected
  const r = Math.max(5, Math.min(14, 4 + Math.sqrt(payload.balanceKeur) * 1.1))

  return (
    <circle
      cx={cx}
      cy={cy}
      r={selected ? r + 2 : r}
      fill={q.stroke}
      fillOpacity={dimmed ? 0.25 : 0.9}
      stroke={selected ? BRAND.navy : '#FFFFFF'}
      strokeWidth={selected ? 2.5 : 1.5}
      style={{ cursor: 'pointer' }}
    />
  )
}

function RiskScatterLabelsLayer({
  width = 0,
  height = 0,
  offset,
  xAxisMap,
  yAxisMap,
  points,
  colorByCustomer,
  selectedCustomer,
}: {
  width?: number
  height?: number
  offset?: { top: number; right: number; bottom: number; left: number }
  xAxisMap?: Record<string, AxisMapEntry>
  yAxisMap?: Record<string, AxisMapEntry>
  points: ScatterLabelPoint[]
  colorByCustomer: Map<string, string>
  selectedCustomer: string | null
}) {
  const xScale = getScale(xAxisMap)
  const yScale = getScale(yAxisMap)
  const mTop = offset?.top ?? CHART_MARGIN.top
  const mRight = offset?.right ?? CHART_MARGIN.right
  const mBottom = offset?.bottom ?? CHART_MARGIN.bottom
  const mLeft = offset?.left ?? CHART_MARGIN.left

  const labels: PlacedScatterLabel[] = useMemo(() => {
    if (!xScale || !yScale || !width || !height || !points.length) return []
    return placeScatterLabels(points, xScale, yScale, width, height, {
      top: mTop,
      right: mRight,
      bottom: mBottom,
      left: mLeft,
    })
  }, [points, xScale, yScale, width, height, mTop, mRight, mBottom, mLeft])

  if (!labels.length) return null

  return (
    <g className="scatter-labels" pointerEvents="none">
      {labels.map(l => {
        const far = Math.hypot(l.labelX - l.pointX, l.labelY - l.pointY) > 18
        const color = colorByCustomer.get(l.segment) ?? BRAND.slate
        const dimmed = selectedCustomer != null && l.segment !== selectedCustomer
        const emphasized = selectedCustomer != null && l.segment === selectedCustomer
        return (
          <g key={l.segment} opacity={dimmed ? 0.4 : 1}>
            {far && (
              <line
                x1={l.pointX}
                y1={l.pointY}
                x2={l.labelX}
                y2={l.labelY}
                stroke={color}
                strokeOpacity={0.45}
                strokeWidth={1}
                strokeDasharray="2 3"
              />
            )}
            <text
              x={l.labelX}
              y={l.labelY}
              textAnchor={l.anchor}
              {...(emphasized ? CHART_SCATTER_LABEL_EMPHASIS_STYLE : CHART_SCATTER_LABEL_STYLE)}
              stroke="#FFFFFF"
              strokeWidth={2}
              paintOrder="stroke"
            >
              <title>{l.segment}</title>
              {l.displayText}
            </text>
          </g>
        )
      })}
    </g>
  )
}

function CustomerRiskAnalysisPanel({
  insights,
  onSelect,
  context = 'receivables',
}: {
  insights: CustomerRiskInsights
  onSelect: (name: string, customerId?: string | null) => void
  context?: 'receivables' | 'payables'
}) {
  const top = insights.topOpportunity
  const topQ = top ? quadrantMeta(top.quadrant) : null

  return (
    <aside
      className="rounded-xl p-4 flex flex-col gap-3 h-full"
      style={{ background: BRAND.surfaceRaised, border: `1px solid ${BRAND.borderLight}` }}
    >
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: BRAND.slateMuted }}>
          Where to act
        </p>
        <p className="text-sm font-semibold mt-1" style={{ color: BRAND.navy }}>
          Volume & risk reduction
        </p>
        <p className="text-xs leading-relaxed mt-2" style={{ color: BRAND.textSecondary }}>
          {insights.intro}
        </p>
      </div>

      {top && topQ ? (
        <div
          className="rounded-lg p-3 border-l-[4px]"
          style={{ background: topQ.badgeBg, borderLeftColor: topQ.stroke }}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: topQ.stroke }}>
            {context === 'payables' ? 'Top payment risk' : 'Top collection opportunity'}
          </p>
          <button
            type="button"
            onClick={() => onSelect(top.customer_name, top.customer_id ?? null)}
            className="text-left mt-1.5 w-full group"
          >
            <p className="text-sm font-bold leading-snug group-hover:underline" style={{ color: BRAND.navy }}>
              {top.customer_name}
            </p>
          </button>
          <dl className="mt-2 space-y-1 text-xs tabular-nums" style={{ color: BRAND.textSecondary }}>
            <div className="flex justify-between gap-2">
              <dt>{context === 'payables' ? 'Open payables' : 'Open receivables'}</dt>
              <dd className="font-semibold" style={{ color: BRAND.navy }}>{fmtAmount(top.balance)}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt>Overdue amount</dt>
              <dd className="font-semibold" style={{ color: topQ.stroke }}>{fmtAmount(overdueAmount(top))}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt>Overdue share</dt>
              <dd className="font-semibold">{top.overdue_pct}%</dd>
            </div>
          </dl>
          <p className="text-[10px] mt-2 leading-relaxed" style={{ color: BRAND.textMuted }}>
            {context === 'payables'
              ? 'Largest payable exposure against us — prioritising this supplier reduces short-term overdue risk fastest.'
              : 'Largest euro exposure to recover — prioritising this account reduces portfolio risk fastest.'}
          </p>
        </div>
      ) : null}

      {insights.topCore ? (
        <div
          className="rounded-lg p-3 border-l-[4px]"
          style={{
            background: quadrantMeta('core').badgeBg,
            borderLeftColor: quadrantMeta('core').stroke,
          }}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: quadrantMeta('core').stroke }}>
            {context === 'payables' ? 'Strongest low-risk supplier' : 'Strongest low-risk account'}
          </p>
          <button
            type="button"
            onClick={() => onSelect(insights.topCore!.customer_name, insights.topCore!.customer_id ?? null)}
            className="text-left mt-1 w-full"
          >
            <p className="text-sm font-semibold" style={{ color: BRAND.navy }}>
              {insights.topCore.customer_name}
            </p>
          </button>
          <p className="text-[10px] mt-1 tabular-nums" style={{ color: BRAND.textSecondary }}>
            {fmtAmount(insights.topCore.balance)} open · {insights.topCore.overdue_pct}% overdue
          </p>
        </div>
      ) : null}

      {insights.criticalOverdueTotal > 0 ? (
        <p className="text-xs tabular-nums px-2 py-1.5 rounded-md" style={{ background: 'rgba(220,38,38,0.08)', color: BRAND.textSecondary }}>
          <span className="font-semibold" style={{ color: '#B91C1C' }}>{fmtAmount(insights.criticalOverdueTotal)}</span>
          {' '}{context === 'payables' ? 'at-risk payable exposure in critical quadrant' : 'overdue in critical quadrant'}
        </p>
      ) : null}

      <ul className="space-y-2 text-xs leading-relaxed list-disc pl-4" style={{ color: BRAND.textSecondary }}>
        {insights.bullets.map(b => (
          <li key={b}>{b}</li>
        ))}
      </ul>

      <p className="text-[10px] pt-1" style={{ color: BRAND.textMuted }}>
        Split: {insights.splitVolumeLabel} · {insights.splitOverdueLabel}
      </p>
    </aside>
  )
}

export default function ReceivablesCustomerScatter({
  data,
  registerRows,
  payablesRegisterRows,
  selectedCustomer,
  selectedCustomerId,
  onSelect,
  year,
  month,
  entity,
  periodLabel,
  context = 'receivables',
}: {
  data: ReceivablesCustomerScatterRow[]
  registerRows?: ReceivablesCustomerRegisterRow[]
  payablesRegisterRows?: PayablesSupplierRegisterRow[]
  selectedCustomer: string | null
  selectedCustomerId?: string | null
  onSelect: (name: string | null, customerId?: string | null) => void
  year: number
  month: number
  entity?: string
  periodLabel: string
  context?: 'receivables' | 'payables'
}) {
  const matrix = useMemo(() => buildCustomerRiskMatrix(data), [data])

  const labelPoints = useMemo((): ScatterLabelPoint[] => {
    if (!matrix?.points.length) return []
    const labelNames = selectMatrixLabelCustomers(matrix.points, matrix.midOverduePct, 3)
    if (selectedCustomer) labelNames.add(selectedCustomer)
    return matrix.points
      .filter(p => labelNames.has(p.customer_name))
      .map(p => ({
        segment: p.customer_name,
        gross_margin_pct: p.balanceKeur,
        gross_profit_keur: p.overdue_pct,
        gross_sales_keur: p.balanceKeur,
      }))
  }, [matrix, selectedCustomer])

  const colorByCustomer = useMemo(() => {
    const m = new Map<string, string>()
    for (const p of matrix?.points ?? []) {
      m.set(p.customer_name, quadrantMeta(p.quadrant).stroke)
    }
    return m
  }, [matrix?.points])

  // Count credit-balance partners excluded from the chart (backend omits them from scatter).
  const creditBalanceCount = useMemo(() => {
    const rows = context === 'payables' ? payablesRegisterRows : registerRows
    return (rows ?? []).filter(r => r.credit_balance).length
  }, [context, registerRows, payablesRegisterRows])

  if (!data.length) {
    return (
      <div className="h-[360px] flex items-center justify-center text-sm" style={{ color: BRAND.textMuted }}>
        {context === 'payables' ? 'No supplier risk data' : 'No customer risk data'}
      </div>
    )
  }

  const {
    points,
    midBalanceKeur,
    midOverduePct,
    maxBalanceKeur,
    maxOverduePct,
    counts,
    insights,
  } = matrix
  const xPad = maxBalanceKeur * 0.08
  const yPad = Math.max(4, maxOverduePct * 0.08)

  const quadrantAreas = [
    { id: 'stable' as const, x1: 0, x2: midBalanceKeur, y1: 0, y2: midOverduePct },
    { id: 'core' as const, x1: midBalanceKeur, x2: maxBalanceKeur + xPad, y1: 0, y2: midOverduePct },
    { id: 'watch' as const, x1: 0, x2: midBalanceKeur, y1: midOverduePct, y2: maxOverduePct + yPad },
    { id: 'critical' as const, x1: midBalanceKeur, x2: maxBalanceKeur + xPad, y1: midOverduePct, y2: maxOverduePct + yPad },
  ]

  return (
    <div className="space-y-5">
      <p className="text-xs leading-relaxed" style={{ color: BRAND.textSecondary }}>
        Each bubble is one <strong>{context === 'payables' ? 'supplier' : 'customer'}</strong>. Key accounts are labelled (high overdue and top low-risk
        relationships). Move <strong>right</strong> for higher {context === 'payables' ? 'open payables' : 'open receivables'}; move <strong>up</strong> for a higher
        overdue share. Click a bubble to filter the register below.
      </p>

      <div className="flex flex-wrap gap-2">
        {CUSTOMER_RISK_QUADRANTS.map(q => (
          <span
            key={q.id}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-medium"
            style={{ background: q.badgeBg, color: BRAND.textSecondary, border: `1px solid ${q.stroke}33` }}
          >
            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: q.stroke }} />
            <span className="font-semibold" style={{ color: q.stroke }}>{q.shortLabel}</span>
            <span className="opacity-70">· {q.hint}</span>
            <span className="tabular-nums font-bold ml-0.5" style={{ color: BRAND.navy }}>
              {counts[q.id]}
            </span>
          </span>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.4fr)_minmax(220px,0.8fr)] gap-4 lg:gap-5 items-stretch">
        <div className="min-w-0 flex flex-col h-full min-h-0">
          <div
            className="relative rounded-xl overflow-hidden flex-1 min-h-[480px]"
            style={{ background: BRAND.surfaceRaised, border: `1px solid ${BRAND.borderLight}` }}
          >
            <div
              className="absolute left-2 z-10 pointer-events-none text-[10px] font-medium leading-snug text-left"
              style={{ color: BRAND.slateMuted, top: CHART_MARGIN.top + 6, maxWidth: '2.75rem' }}
            >
              <span className="block">↑ Higher</span>
              <span className="block">overdue</span>
            </div>
            <div
              className="absolute left-2 z-10 pointer-events-none text-[10px] font-medium leading-snug text-left"
              style={{ color: BRAND.slateMuted, bottom: CHART_MARGIN.bottom + 24, maxWidth: '2.75rem' }}
            >
              <span className="block">↓ Lower</span>
              <span className="block">overdue</span>
            </div>
            <p
              className="absolute z-10 pointer-events-none text-[10px] font-medium leading-none"
              style={{ color: BRAND.slateMuted, left: CHART_MARGIN.left + 6, bottom: CHART_MARGIN.bottom - 6 }}
            >
              ← Lower volume
            </p>
            <p
              className="absolute z-10 pointer-events-none text-[10px] font-medium leading-none text-right"
              style={{ color: BRAND.slateMuted, right: CHART_MARGIN.right - 4, bottom: CHART_MARGIN.bottom - 6 }}
            >
              Higher volume →
            </p>

            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={CHART_MARGIN}>
                {quadrantAreas.map(({ id, x1, x2, y1, y2 }) => {
                  const q = quadrantMeta(id)
                  return (
                    <ReferenceArea
                      key={id}
                      x1={x1}
                      x2={x2}
                      y1={y1}
                      y2={y2}
                      fill={q.fill}
                      stroke={q.stroke}
                      strokeOpacity={0.35}
                      strokeWidth={1}
                    />
                  )
                })}

                <CartesianGrid stroke={BRAND.borderLight} strokeDasharray="4 6" />

                <ReferenceLine
                  x={midBalanceKeur}
                  stroke="#94A3B8"
                  strokeDasharray="5 4"
                  strokeWidth={1.5}
                  label={{
                    value: `${fmtChartKpi(midBalanceKeur)} kEUR`,
                    position: 'insideTopRight',
                    fill: BRAND.slateMuted,
                    fontSize: 9,
                  }}
                />
                <ReferenceLine
                  y={midOverduePct}
                  stroke="#94A3B8"
                  strokeDasharray="5 4"
                  strokeWidth={1.5}
                  label={{
                    value: `${midOverduePct.toFixed(0)}%`,
                    position: 'insideTopLeft',
                    fill: BRAND.slateMuted,
                    fontSize: 9,
                  }}
                />

                <XAxis
                  type="number"
                  dataKey="balanceKeur"
                  domain={[0, maxBalanceKeur + xPad]}
                  tick={{ fontSize: 10, fill: BRAND.slate }}
                  tickLine={false}
                  axisLine={{ stroke: BRAND.border }}
                  tickFormatter={v => fmtChartKpi(Number(v))}
                >
                  <Label
                    value={context === 'payables'
                      ? 'Open trade payables per supplier (kEUR)'
                      : 'Open trade receivables per customer (kEUR)'}
                    position="bottom"
                    offset={12}
                    style={{ fontSize: 11, fontWeight: 600, fill: BRAND.textSecondary }}
                  />
                </XAxis>

                <YAxis
                  type="number"
                  dataKey="overdue_pct"
                  domain={[0, maxOverduePct + yPad]}
                  tick={{ fontSize: 10, fill: BRAND.slate }}
                  tickLine={false}
                  axisLine={{ stroke: BRAND.border }}
                  unit="%"
                  width={44}
                >
                  <Label
                    value={context === 'payables'
                      ? 'Overdue share of supplier balance (%)'
                      : 'Overdue share of customer balance (%)'}
                    angle={-90}
                    position="left"
                    offset={12}
                    style={{ fontSize: 11, fontWeight: 600, fill: BRAND.textSecondary, textAnchor: 'middle' }}
                  />
                </YAxis>

                <ZAxis type="number" dataKey="balanceKeur" range={[48, 420]} />

                <Tooltip content={<RiskTooltip context={context} />} cursor={{ strokeDasharray: '3 3', stroke: BRAND.slateMuted }} />

                <Scatter
                  data={points}
                  shape={(props: unknown) => (
                    <RiskDot
                      {...(props as { cx?: number; cy?: number; payload?: CustomerRiskPoint })}
                      selectedCustomer={selectedCustomer}
                      selectedCustomerId={selectedCustomerId}
                    />
                  )}
                  onClick={p => {
                    const point = (p as { payload?: CustomerRiskPoint })?.payload
                    const name = point?.customer_name ?? null
                    const customerId = point?.customer_id ?? null
                    const isSame = selectedCustomerId
                      ? customerId != null && customerId === selectedCustomerId
                      : name === selectedCustomer
                    onSelect(isSame ? null : name, isSame ? null : customerId)
                  }}
                />

                <Customized
                  component={function RiskLabelsLayer(chartProps: Record<string, unknown>) {
                    return (
                      <RiskScatterLabelsLayer
                        {...chartProps}
                        points={labelPoints}
                        colorByCustomer={colorByCustomer}
                        selectedCustomer={selectedCustomer}
                      />
                    )
                  }}
                />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </div>

        <CustomerRiskAnalysisPanel insights={insights} onSelect={onSelect} context={context} />
      </div>

      <div className="pt-2 mt-1 border-t" style={{ borderColor: BRAND.borderLight }}>
        {context === 'payables' ? (
          <PayablesSupplierRegister
            rows={payablesRegisterRows ?? []}
            filterSupplier={selectedCustomer}
            onClearFilter={() => onSelect(null)}
            year={year}
            month={month}
            entity={entity}
            periodLabel={periodLabel}
          />
        ) : (
          <ReceivablesCustomerRegister
            rows={registerRows ?? []}
            filterCustomer={selectedCustomer}
            filterCustomerId={selectedCustomerId}
            onClearFilter={() => onSelect(null)}
            year={year}
            month={month}
            entity={entity}
            periodLabel={periodLabel}
          />
        )}
        {creditBalanceCount > 0 && (
          <p className="text-[10px] mt-2 px-1" style={{ color: '#94A3B8' }}>
            Note: {creditBalanceCount}{' '}
            {context === 'payables' ? 'supplier' : 'customer'}
            {creditBalanceCount === 1 ? '' : 's'} with a net credit balance{' '}
            {creditBalanceCount === 1 ? 'is' : 'are'} not shown in the chart above.
          </p>
        )}
      </div>
    </div>
  )
}
