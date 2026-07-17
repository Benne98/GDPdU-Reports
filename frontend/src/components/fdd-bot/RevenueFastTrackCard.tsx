import { useMemo, useState } from 'react'
import { Check, ChevronDown, ListFilter, Zap } from 'lucide-react'
import type { AdaptiveCardPayload } from './useFddBot'

type Analysis = 'gst' | 'pvm' | 'top' | 'churn' | 'bubble' | 'bars' | 'gpbridge' | 'hbar' | 'arrbridge'
type CalcMode = 'invoice' | 'accrual'
type DateMapping = 'date' | 'period'
type MarginMode = 'cost' | 'profit'
type SalesBasis = 'net' | 'gross'

export interface RevenueFastTrackValues {
  selected_analyses: Analysis[]
  calc_mode: CalcMode
  date_mapping: DateMapping
  sales_basis: SalesBasis
  margin_mode: MarginMode
  fx_enabled: boolean
  period_mode: 'FY'
  use_arr_calculation?: boolean
  revenue_col: string
  invoice_col?: string
  period_col?: string
  contract_start_col?: string
  contract_end_col?: string
  fx_col?: string
  entity_col?: string
  product_col?: string
  customer_col?: string
  quantity_col?: string
  cost_col?: string
  profit_col?: string
  segment_col?: string
  region_col?: string
}

interface Props {
  payload: AdaptiveCardPayload
  disabled?: boolean
  onSubmit: (values: RevenueFastTrackValues) => void | Promise<void>
  onOpenFilter?: () => void
}

const TABLE_ANALYSES: { id: Analysis; label: string; description: string }[] = [
  { id: 'gst', label: 'GST', description: 'Entity · Product · Customer' },
  { id: 'pvm', label: 'PVM', description: 'Price · Volume · Mix' },
  { id: 'top', label: 'TOP', description: 'Customer concentration' },
  { id: 'churn', label: 'Churn', description: 'ARR bridge' },
]

const CHART_ANALYSES: { id: Analysis; label: string; description: string }[] = [
  { id: 'bubble', label: 'Bubble', description: 'Revenue and margin' },
  { id: 'bars', label: 'Bars', description: 'Entity · Segment · Customers · Products · Region' },
  { id: 'gpbridge', label: 'GP Bridge', description: 'By product · last FYs (GP / NP)' },
  { id: 'hbar', label: 'Horizontal bars', description: 'Entity · Segment · Product · last FYs' },
  { id: 'arrbridge', label: 'ARR Bridge', description: 'Entity · Customer · Product · last FYs' },
]

const ANALYSES = [...TABLE_ANALYSES, ...CHART_ANALYSES]

function stringDefault(source: Record<string, unknown>, key: string): string {
  const value = source[key]
  return value == null ? '' : String(value)
}

function SelectField({
  label,
  value,
  headers,
  onChange,
  optional,
}: {
  label: string
  value: string
  headers: string[]
  onChange: (value: string) => void
  optional?: boolean
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="text-[11px] font-semibold text-slate-600">
        {label}
        {optional ? <span className="ml-1 font-normal text-slate-400">optional</span> : null}
      </span>
      <span className="relative">
        <select
          value={value}
          onChange={event => onChange(event.target.value)}
          className="min-h-[36px] w-full appearance-none rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 pr-8 text-xs text-slate-800 outline-none focus:border-slate-400"
        >
          <option value="">{optional ? 'None' : 'Select column…'}</option>
          {headers.map(header => (
            <option key={header} value={header}>{header}</option>
          ))}
        </select>
        <ChevronDown
          size={14}
          className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400"
        />
      </span>
    </label>
  )
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}) {
  return (
    <div className="flex rounded-lg bg-slate-100 p-0.5">
      {options.map(option => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className="flex-1 rounded-md px-2.5 py-1.5 text-[11px] font-semibold transition-colors"
          style={{
            background: value === option.value ? '#FFFFFF' : 'transparent',
            color: value === option.value ? '#1E3A5F' : '#64748B',
            boxShadow: value === option.value ? '0 1px 2px rgba(15,23,42,0.08)' : 'none',
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export default function RevenueFastTrackCard({ payload, disabled, onSubmit, onOpenFilter }: Props) {
  const meta = payload.mapper_meta ?? {}
  const defaults = (
    payload.defaults && typeof payload.defaults === 'object'
      ? payload.defaults
      : meta.defaults && typeof meta.defaults === 'object'
      ? meta.defaults
      : meta
  ) as Record<string, unknown>
  const headers = useMemo(() => {
    const raw = Array.isArray(payload.headers)
      ? payload.headers
      : Array.isArray(meta.headers)
        ? meta.headers
        : []
    return [...new Set(raw.map(value => String(value).trim()).filter(Boolean))]
  }, [meta.headers, payload.headers])

  const [selected, setSelected] = useState<Analysis[]>(() => {
    const raw = defaults.selected_analyses
    return Array.isArray(raw)
      ? raw.filter((value): value is Analysis =>
          ANALYSES.some(analysis => analysis.id === value))
      : []
  })
  const [calcMode, setCalcMode] = useState<CalcMode>(
    defaults.calc_mode === 'accrual' ? 'accrual' : 'invoice',
  )
  const [dateMapping, setDateMapping] = useState<DateMapping>(
    defaults.date_mapping === 'period' ? 'period' : 'date',
  )
  const [salesBasis, setSalesBasis] = useState<SalesBasis>(
    defaults.sales_basis === 'gross' ? 'gross' : 'net',
  )
  const [marginMode, setMarginMode] = useState<MarginMode>(
    defaults.margin_mode === 'profit' ? 'profit' : 'cost',
  )
  const [fxEnabled, setFxEnabled] = useState(
    defaults.fx_enabled === true || defaults.apply_fx === true,
  )
  const [useArrCalculation, setUseArrCalculation] = useState(
    defaults.use_arr_calculation === true,
  )
  const [columns, setColumns] = useState<Record<string, string>>(() => ({
    revenue_col: stringDefault(defaults, 'revenue_col'),
    invoice_col: stringDefault(defaults, 'invoice_col'),
    period_col: stringDefault(defaults, 'period_col'),
    contract_start_col: stringDefault(defaults, 'contract_start_col'),
    contract_end_col: stringDefault(defaults, 'contract_end_col'),
    fx_col: stringDefault(defaults, 'fx_col'),
    entity_col: stringDefault(defaults, 'entity_col'),
    product_col: stringDefault(defaults, 'product_col'),
    customer_col: stringDefault(defaults, 'customer_col'),
    quantity_col: stringDefault(defaults, 'quantity_col'),
    cost_col: stringDefault(defaults, 'cost_col'),
    profit_col: stringDefault(defaults, 'profit_col'),
    segment_col: stringDefault(defaults, 'segment_col'),
    region_col: stringDefault(defaults, 'region_col'),
  }))
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const has = (analysis: Analysis) => selected.includes(analysis)
  const needsEntity = has('gst') || has('churn') || has('bars') || has('hbar') || has('arrbridge')
  const needsProduct = has('gst') || has('pvm') || has('churn') || has('bubble') || has('bars') || has('gpbridge') || has('hbar') || has('arrbridge')
  const needsCustomer = has('gst') || has('top') || has('churn') || has('bars') || has('arrbridge')
  const needsMargin = has('gst') || has('pvm') || has('bubble') || has('gpbridge')
  const needsContractDates = calcMode === 'accrual' || has('churn') || has('arrbridge')
  const needsSegment = has('bubble') || has('bars') || has('hbar')
  const needsRegion = has('bars') || has('churn')
  const regionRequired = has('churn')
  const segmentRequired = has('hbar')

  const setColumn = (key: string, value: string) => {
    setColumns(previous => ({ ...previous, [key]: value }))
    setError(null)
  }

  const toggleAnalysis = (analysis: Analysis) => {
    setSelected(previous => {
      const next = previous.includes(analysis)
        ? previous.filter(value => value !== analysis)
        : [...previous, analysis]
      if (!next.includes('arrbridge')) {
        setUseArrCalculation(false)
      }
      return next
    })
    setError(null)
  }

  const requiredColumns = () => {
    const keys = ['revenue_col', dateMapping === 'date' ? 'invoice_col' : 'period_col']
    if (needsContractDates) keys.push('contract_start_col', 'contract_end_col')
    if (fxEnabled) keys.push('fx_col')
    if (needsEntity) keys.push('entity_col')
    if (needsProduct) keys.push('product_col')
    if (needsCustomer) keys.push('customer_col')
    if (segmentRequired) keys.push('segment_col')
    if (has('pvm')) keys.push('quantity_col')
    if (needsMargin) keys.push(marginMode === 'cost' ? 'cost_col' : 'profit_col')
    return keys
  }

  const handleSubmit = async () => {
    if (disabled || submitting) return
    if (selected.length === 0) {
      setError('Select at least one analysis.')
      return
    }
    if (requiredColumns().some(key => !columns[key]?.trim())) {
      setError('Complete every required column mapping.')
      return
    }
    if (has('arrbridge') && useArrCalculation && dateMapping === 'period') {
      setError('ARR calculation requires a date column mapping (not a period column).')
      return
    }

    const values: RevenueFastTrackValues = {
      selected_analyses: selected,
      calc_mode: calcMode,
      date_mapping: dateMapping,
      sales_basis: salesBasis,
      margin_mode: marginMode,
      fx_enabled: fxEnabled,
      period_mode: 'FY',
      ...(has('arrbridge') ? { use_arr_calculation: useArrCalculation } : {}),
      revenue_col: columns.revenue_col,
      ...(dateMapping === 'date'
        ? { invoice_col: columns.invoice_col }
        : { period_col: columns.period_col }),
      ...(needsContractDates
        ? {
            contract_start_col: columns.contract_start_col,
            contract_end_col: columns.contract_end_col,
          }
        : {}),
      ...(fxEnabled ? { fx_col: columns.fx_col } : {}),
      ...(needsEntity ? { entity_col: columns.entity_col } : {}),
      ...(needsProduct ? { product_col: columns.product_col } : {}),
      ...(needsCustomer ? { customer_col: columns.customer_col } : {}),
      ...(has('pvm') ? { quantity_col: columns.quantity_col } : {}),
      ...(needsMargin
        ? marginMode === 'cost'
          ? { cost_col: columns.cost_col }
          : { profit_col: columns.profit_col }
        : {}),
      ...(needsSegment && (segmentRequired || columns.segment_col.trim())
        ? { segment_col: columns.segment_col }
        : {}),
      ...(needsRegion && (regionRequired || columns.region_col.trim())
        ? { region_col: columns.region_col }
        : {}),
    }

    setSubmitting(true)
    try {
      await onSubmit(values)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="mb-3 w-full overflow-hidden rounded-2xl border border-slate-200 bg-white"
      style={{ boxShadow: '0 8px 28px rgba(15,23,42,0.08)' }}
    >
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 bg-gradient-to-r from-slate-50 to-white px-5 py-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#1E3A5F] text-white">
              <Zap size={15} />
            </span>
            <h3 className="text-sm font-semibold text-slate-900">
              {payload.title || 'Revenue Fast Track'}
            </h3>
          </div>
          <p className="mt-1.5 max-w-2xl text-xs leading-relaxed text-slate-500">
            {payload.subtitle || 'Choose the analyses once, then map the shared source columns.'}
          </p>
        </div>
        {onOpenFilter ? (
          <button
            type="button"
            onClick={onOpenFilter}
            disabled={disabled}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-slate-600 transition-colors hover:border-slate-300 hover:text-[#1E3A5F] disabled:opacity-50"
          >
            <ListFilter size={14} />
            Row filter
          </button>
        ) : null}
      </div>

      <div className="grid gap-5 px-5 py-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.6fr)]">
        <div className="flex flex-col gap-4">
          <section className="flex flex-col gap-3.5">
            {(
              [
                { title: 'Tables', items: TABLE_ANALYSES },
                { title: 'Charts', items: CHART_ANALYSES },
              ] as const
            ).map(group => (
              <div key={group.title}>
                <p className="mb-2 text-[11px] font-bold uppercase tracking-wide text-slate-400">
                  {group.title}
                </p>
                <div className="grid grid-cols-2 gap-2">
                  {group.items.map(analysis => {
                    const active = has(analysis.id)
                    const isArrBridge = analysis.id === 'arrbridge'
                    return (
                      <button
                        key={analysis.id}
                        type="button"
                        onClick={() => toggleAnalysis(analysis.id)}
                        disabled={disabled}
                        className="flex min-w-0 flex-col gap-1.5 rounded-xl border px-3 py-2.5 text-left transition-colors disabled:opacity-50"
                        style={{
                          borderColor: active ? '#1E3A5F' : '#E2E8F0',
                          background: active ? 'rgba(30,58,95,0.06)' : '#FFFFFF',
                        }}
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <span
                            className="flex h-4 w-4 shrink-0 items-center justify-center rounded"
                            style={{ background: active ? '#1E3A5F' : '#E2E8F0', color: '#FFFFFF' }}
                          >
                            {active ? <Check size={11} /> : null}
                          </span>
                          <span className="min-w-0">
                            <span className="block text-xs font-semibold text-slate-700">{analysis.label}</span>
                            <span className="block truncate text-[10px] text-slate-400">{analysis.description}</span>
                          </span>
                        </span>
                        {isArrBridge && active ? (
                          <label
                            className="ml-6 flex cursor-pointer items-center gap-1.5"
                            onClick={event => event.stopPropagation()}
                          >
                            <input
                              type="checkbox"
                              checked={useArrCalculation}
                              disabled={disabled}
                              onChange={event => {
                                setUseArrCalculation(event.target.checked)
                                setError(null)
                              }}
                              className="h-3 w-3 shrink-0 accent-[#1E3A5F]"
                            />
                            <span className="text-[10px] font-medium text-slate-500">ARR calc</span>
                          </label>
                        ) : null}
                      </button>
                    )
                  })}
                </div>
              </div>
            ))}
          </section>

          <section className="grid gap-3 rounded-xl border border-slate-100 bg-slate-50/60 p-3">
            <div>
              <p className="mb-1 text-[11px] font-semibold text-slate-600">Revenue recognition</p>
              <Segmented
                value={calcMode}
                onChange={setCalcMode}
                options={[
                  { value: 'invoice', label: 'Invoice based' },
                  { value: 'accrual', label: 'Accrual based' },
                ]}
              />
            </div>
            <div>
              <p className="mb-1 text-[11px] font-semibold text-slate-600">Period mapping</p>
              <Segmented
                value={dateMapping}
                onChange={setDateMapping}
                options={[
                  { value: 'date', label: 'Date column' },
                  { value: 'period', label: 'Period column' },
                ]}
              />
            </div>
            <div>
              <p className="mb-1 text-[11px] font-semibold text-slate-600">Sales basis</p>
              <Segmented
                value={salesBasis}
                onChange={setSalesBasis}
                options={[
                  { value: 'net', label: 'Net sales' },
                  { value: 'gross', label: 'Gross sales' },
                ]}
              />
            </div>
            <div>
              <p className="mb-1 text-[11px] font-semibold text-slate-600">Margin source</p>
              <Segmented
                value={marginMode}
                onChange={setMarginMode}
                options={[
                  { value: 'cost', label: 'Cost column' },
                  { value: 'profit', label: 'Profit column' },
                ]}
              />
            </div>
            <label className="flex cursor-pointer items-center justify-between gap-3 rounded-lg bg-white px-3 py-2">
              <span>
                <span className="block text-[11px] font-semibold text-slate-700">Foreign exchange</span>
                <span className="block text-[10px] text-slate-400">Off by default</span>
              </span>
              <input
                type="checkbox"
                checked={fxEnabled}
                onChange={event => setFxEnabled(event.target.checked)}
                className="h-4 w-4 accent-[#1E3A5F]"
              />
            </label>
          </section>
        </div>

        <section>
          <div className="grid grid-cols-2 gap-x-3 gap-y-2.5">
            <SelectField
              label={salesBasis === 'gross' ? 'Gross sales / Revenue' : 'Net sales / Revenue'}
              value={columns.revenue_col}
              headers={headers}
              onChange={value => setColumn('revenue_col', value)}
            />
            {dateMapping === 'date' ? (
              <SelectField label="Invoice date" value={columns.invoice_col} headers={headers} onChange={value => setColumn('invoice_col', value)} />
            ) : (
              <SelectField label="Period" value={columns.period_col} headers={headers} onChange={value => setColumn('period_col', value)} />
            )}
            {needsContractDates ? (
              <>
                <SelectField label="Contract start" value={columns.contract_start_col} headers={headers} onChange={value => setColumn('contract_start_col', value)} />
                <SelectField label="Contract end" value={columns.contract_end_col} headers={headers} onChange={value => setColumn('contract_end_col', value)} />
              </>
            ) : null}
            {fxEnabled ? (
              <SelectField label="FX rate" value={columns.fx_col} headers={headers} onChange={value => setColumn('fx_col', value)} />
            ) : null}
            {needsEntity ? (
              <SelectField label="Entity" value={columns.entity_col} headers={headers} onChange={value => setColumn('entity_col', value)} />
            ) : null}
            {needsProduct ? (
              <SelectField label="Product" value={columns.product_col} headers={headers} onChange={value => setColumn('product_col', value)} />
            ) : null}
            {needsCustomer ? (
              <SelectField label="Customer" value={columns.customer_col} headers={headers} onChange={value => setColumn('customer_col', value)} />
            ) : null}
            {has('pvm') ? (
              <SelectField label="Quantity" value={columns.quantity_col} headers={headers} onChange={value => setColumn('quantity_col', value)} />
            ) : null}
            {needsMargin && marginMode === 'cost' ? (
              <SelectField label="Cost" value={columns.cost_col} headers={headers} onChange={value => setColumn('cost_col', value)} />
            ) : null}
            {needsMargin && marginMode === 'profit' ? (
              <SelectField label="Gross profit" value={columns.profit_col} headers={headers} onChange={value => setColumn('profit_col', value)} />
            ) : null}
            {needsSegment ? (
              <SelectField
                label="Segment / Product category"
                optional={!segmentRequired}
                value={columns.segment_col}
                headers={headers}
                onChange={value => setColumn('segment_col', value)}
              />
            ) : null}
            {needsRegion ? (
              <SelectField
                label="Region"
                optional={!regionRequired}
                value={columns.region_col}
                headers={headers}
                onChange={value => setColumn('region_col', value)}
              />
            ) : null}
          </div>
        </section>
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-5 py-3">
        <p className="text-[11px] text-red-600">{error}</p>
        {disabled ? (
          <span className="text-xs text-slate-400">Response submitted</span>
        ) : (
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={submitting}
            className="ml-auto rounded-lg bg-[#1E3A5F] px-5 py-2 text-xs font-semibold text-white disabled:opacity-50"
          >
            {submitting ? 'Preparing…' : (payload.submit_label || 'Run Fast Track')}
          </button>
        )}
      </div>
    </div>
  )
}
