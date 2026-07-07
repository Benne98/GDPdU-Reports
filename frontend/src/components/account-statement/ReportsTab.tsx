import { useCallback, useEffect, useMemo, useState } from 'react'
import { FileDown, FileSpreadsheet, Loader2 } from 'lucide-react'
import {
  api,
  type Entity,
  type FinancialStatementResponse,
  type MonthlyResponse,
} from '../../lib/api'
import { stripLegalForm } from '../../lib/stripLegalForm'
import PlExportMenu, { type PlExportKind } from '../financials/pl-two-view/PlExportMenu'
import { buildDefaultColumns } from '../financials/pl-two-view/plColumnRegistry'
import { buildPlanMapFromStatement } from '../financials/pl-two-view/plPlanMap'
import { buildClientNarrativeResponse, mapApiBulletsToUi } from '../financials/pl-two-view/plNarrativeEngine'
import { exportPlReportViewPdf, type PlExportContext } from '../financials/pl-two-view/plExportPdf'
import { exportPlReportViewPptx } from '../financials/pl-two-view/plExportPptx'
import {
  exportStatementReportViewPdf,
  exportStatementReportViewPptx,
} from '../financials/statement-two-view/statementReportExport'
import { buildExportCheckOpen } from '../../lib/finssentialsExport/buildExportCheckOpen'
import { databookFilename, exportDatabookXlsx } from '../../lib/finssentialsExport/exportDatabookXlsx'

const ALL = '__all__'

function anchorFromDateMax(iso: string | null | undefined): { year: number; month: number } | null {
  if (!iso) return null
  const [y, m] = iso.slice(0, 10).split('-').map(Number)
  if (!y || !m) return null
  return { year: y, month: m }
}

function miniColumns(data: FinancialStatementResponse) {
  const all = buildDefaultColumns(
    data.col_labels,
    data.period_grain === 'week' ? 'week' : 'month',
    data.statement,
  )
  return all.filter(c => ['pm', 'cm', 'mom', 'plan_cm', 'plan_vs_actual'].includes(c.kind))
}

export default function ReportsTab() {
  const [entities, setEntities] = useState<Entity[]>([])
  const [entity, setEntity] = useState(ALL)
  const [year, setYear] = useState(2025)
  const [month, setMonth] = useState(7)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const entityCode = entity === ALL ? undefined : entity
  const entityLabel = useMemo(() => {
    if (entity === ALL) return 'All entities'
    const hit = entities.find(e => e.legal_entity_code === entity)
    return hit ? stripLegalForm(hit.entity_name) : entity
  }, [entity, entities])

  const exportCtx: PlExportContext = useMemo(
    () => ({
      entityLabel: entity === ALL ? 'all' : entity,
      entityDisplayName: entityLabel,
    }),
    [entity, entityLabel],
  )

  useEffect(() => {
    void (async () => {
      try {
        const [entList, filters] = await Promise.all([api.entities(), api.glLinesFilters()])
        setEntities(entList)
        const anchor = anchorFromDateMax(filters.date_max)
        if (anchor) {
          setYear(anchor.year)
          setMonth(anchor.month)
        }
      } catch {
        /* defaults */
      }
    })()
  }, [])

  const loadPackageData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [trialBalance, pl, bs, plMonthly, bsMonthly] = await Promise.all([
        api.trialBalanceExport({ year, month, entity: entityCode }),
        api.financialsPlStatement(year, month, entityCode),
        api.financialsBalanceSheet(year, month, entityCode),
        api.financialsPlMonthly({ year, month, entity: entityCode }),
        api.financialsBsMonthly(year, month, entityCode),
      ])
      const [plNarrative, bsNarrative] = await Promise.all([
        api.financialsPlNarrative(year, month, entityCode),
        api.financialsBsNarrative(year, month, entityCode),
      ])
      return {
        trialBalance,
        pl,
        bs,
        plMonthly: plMonthly as MonthlyResponse,
        bsMonthly: bsMonthly as MonthlyResponse,
        plNarrative,
        bsNarrative,
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not load report data')
      return null
    } finally {
      setLoading(false)
    }
  }, [year, month, entityCode])

  const exportXlsx = useCallback(
    async (scope: 'monthly' | 'annual') => {
      setBusy('xlsx')
      try {
        const pack = await loadPackageData()
        if (!pack) return
        const plPlanMap = buildPlanMapFromStatement(pack.pl)
        const bsPlanMap = buildPlanMapFromStatement(pack.bs)
        const plColumns = buildDefaultColumns(
          pack.pl.col_labels,
          pack.pl.period_grain === 'week' ? 'week' : 'month',
          'pl',
        )
        const bsColumns = buildDefaultColumns(
          pack.bs.col_labels,
          pack.bs.period_grain === 'week' ? 'week' : 'month',
          'bs',
        )
        await exportDatabookXlsx({
          trialBalance: pack.trialBalance,
          pl: {
            data: pack.pl,
            columns: plColumns,
            planMap: plPlanMap,
            monthly: pack.plMonthly,
            entityLabel,
          },
          bs: {
            data: pack.bs,
            columns: bsColumns,
            planMap: bsPlanMap,
            monthly: pack.bsMonthly,
            entityLabel,
          },
          filename: databookFilename(entityLabel, year, month, scope),
        })
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'XLSX export failed')
      } finally {
        setBusy(null)
      }
    },
    [loadPackageData, entityLabel, year, month],
  )

  const exportReports = useCallback(
    async (kind: PlExportKind, scope: 'monthly' | 'annual') => {
      if (kind === 'xlsx') {
        await exportXlsx(scope)
        return
      }
      setBusy(kind)
      try {
        const pack = await loadPackageData()
        if (!pack) return
        const plPlanMap = buildPlanMapFromStatement(pack.pl)
        const bsPlanMap = buildPlanMapFromStatement(pack.bs)
        const plMini = miniColumns(pack.pl)
        const bsMini = miniColumns(pack.bs)
        const plBullets = mapApiBulletsToUi(
          pack.plNarrative?.bullets ?? buildClientNarrativeResponse(pack.pl, plPlanMap, year, month, 5)?.bullets ?? [],
          pack.pl.rows,
        )
        const bsBullets = mapApiBulletsToUi(
          pack.bsNarrative?.bullets ?? buildClientNarrativeResponse(pack.bs, bsPlanMap, year, month, 5)?.bullets ?? [],
          pack.bs.rows,
        )
        const plCheckOpen = buildExportCheckOpen(pack.pl.rows, 'pl')
        const bsCheckOpen = buildExportCheckOpen(pack.bs.rows, 'bs')

        if (kind === 'pdf') {
          await exportPlReportViewPdf(
            pack.pl,
            year,
            month,
            plMini,
            plPlanMap,
            plBullets,
            exportCtx,
            pack.plNarrative,
            {},
            plCheckOpen,
          )
          await exportStatementReportViewPdf(
            'bs',
            pack.bs,
            year,
            month,
            bsMini,
            bsBullets,
            exportCtx,
            pack.bsNarrative,
            bsCheckOpen,
            {},
          )
        } else {
          await exportPlReportViewPptx(
            pack.pl,
            year,
            month,
            plMini,
            plPlanMap,
            plBullets,
            exportCtx,
            pack.plNarrative,
            {},
            plCheckOpen,
          )
          await exportStatementReportViewPptx(
            'bs',
            pack.bs,
            year,
            month,
            bsMini,
            bsBullets,
            exportCtx,
            pack.bsNarrative,
            {},
            bsCheckOpen,
          )
        }
        void scope
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Export failed')
      } finally {
        setBusy(null)
      }
    },
    [loadPackageData, exportCtx, year, month, exportXlsx],
  )

  const selectClass =
    'rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2 text-xs text-slate-800 outline-none focus:border-[#1E3A5F]/40'

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm space-y-4">
        <h2 className="text-sm font-semibold text-slate-800">Report period</h2>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <label className="space-y-1 block">
            <span className="text-[12px] font-medium text-slate-500">Entity</span>
            <select className={`${selectClass} w-full`} value={entity} onChange={e => setEntity(e.target.value)}>
              <option value={ALL}>All entities</option>
              {entities.map(e => (
                <option key={e.legal_entity_code} value={e.legal_entity_code}>
                  {stripLegalForm(e.entity_name)}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 block">
            <span className="text-[12px] font-medium text-slate-500">Year</span>
            <input
              type="number"
              className={`${selectClass} w-full`}
              value={year}
              onChange={e => setYear(Number(e.target.value))}
            />
          </label>
          <label className="space-y-1 block">
            <span className="text-[12px] font-medium text-slate-500">Month</span>
            <input
              type="number"
              min={1}
              max={12}
              className={`${selectClass} w-full`}
              value={month}
              onChange={e => setMonth(Number(e.target.value))}
            />
          </label>
        </div>
      </section>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">{error}</div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ReportCard
          title="Monthly reporting"
          subtitle="Anchor month — table views (XLSX) and report views (PDF / PPTX)."
          busy={busy}
          loading={loading}
          scope="monthly"
          onExport={exportReports}
        />
        <ReportCard
          title="Annual reporting"
          subtitle="Same structure with YTD / fiscal-year columns — suitable for year-end databook exports."
          busy={busy}
          loading={loading}
          scope="annual"
          onExport={exportReports}
        />
      </div>
    </div>
  )
}

function ReportCard({
  title,
  subtitle,
  busy,
  loading,
  scope,
  onExport,
}: {
  title: string
  subtitle: string
  busy: string | null
  loading: boolean
  scope: 'monthly' | 'annual'
  onExport: (kind: PlExportKind, scope: 'monthly' | 'annual') => Promise<void>
}) {
  return (
    <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
        <p className="text-xs text-slate-500 mt-1">{subtitle}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={loading || busy !== null}
          onClick={() => void onExport('xlsx', scope)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-[#1E3A5F] px-3 py-2 text-xs font-medium text-white hover:bg-[#16304f] disabled:opacity-50"
        >
          {busy === 'xlsx' ? <Loader2 size={14} className="animate-spin" /> : <FileSpreadsheet size={14} />}
          Databook (.xlsx)
        </button>
        <PlExportMenu
          formats={['pdf', 'pptx']}
          onExport={kind => onExport(kind, scope)}
          disabled={loading || busy !== null}
        />
        {(loading || busy) && (
          <span className="text-xs text-slate-400 inline-flex items-center gap-1">
            <Loader2 size={12} className="animate-spin" />
            Preparing…
          </span>
        )}
      </div>
      <p className="text-[12px] text-slate-400 flex items-center gap-1">
        <FileDown size={12} />
        XLSX includes PL_all, BS_all (with balance check), PL and BS table sheets with SUMIFS links.
      </p>
    </section>
  )
}
