/**
 * BudgetPage — Phase 5 rebuild.
 *
 * Inline controls (no separate filter card): statement, entity, year, view mode,
 * level, input mode, planning direction.
 *
 * Actions: Finssentials heuristics panel, Download template, Upload (preview →
 * commit), Save (batched PATCH), Reset (DELETE).
 *
 * Uses BudgetTreeGrid for the main grid.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Lightbulb,
  Download,
  Upload,
  Save,
  Trash2,
  RefreshCw,
  X,
  Check,
  ChevronDown,
} from 'lucide-react'
import {
  getBudgetTree,
  budgetEntities,
  budgetUpload,
  budgetCommit,
  downloadBudgetTemplate,
  seedBudget,
  putBudgetCell,
  patchBudgetPosition,
  deleteBudget,
  type BudgetTreeResponse,
  type BudgetEntitiesResponse,
  type BudgetUploadPreview,
} from '../lib/gdpduApi'
import BudgetTreeGrid from '../components/budget/BudgetTreeGrid'
import type { PositionOverride } from '../components/budget/BudgetGrid'
import type { PositionGranularity } from '../lib/budgetChatFlow'

// ---------------------------------------------------------------------------
// Spinner
// ---------------------------------------------------------------------------

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center py-16 gap-3">
      <svg
        className="animate-spin h-5 w-5 text-[#1E3A5F]"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
        aria-hidden
      >
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path
          className="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
        />
      </svg>
      <span className="text-sm text-slate-500">{label}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Error banner
// ---------------------------------------------------------------------------

function ErrorBanner({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  return (
    <div
      className="mb-4 rounded-xl px-5 py-4 text-sm font-medium flex items-start justify-between gap-4"
      style={{
        background: 'rgba(239,68,68,0.08)',
        border: '1px solid rgba(220,38,38,0.35)',
        color: '#991B1B',
      }}
      role="alert"
    >
      <span>{message}</span>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="flex-shrink-0 text-red-400 hover:text-red-700 transition-colors text-lg leading-none"
          aria-label="Dismiss error"
        >
          ×
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Success banner
// ---------------------------------------------------------------------------

function SuccessBanner({ message }: { message: string }) {
  return (
    <div
      className="mb-4 rounded-xl px-5 py-3 text-sm font-medium"
      style={{
        background: 'rgba(5,150,105,0.08)',
        border: '1px solid rgba(5,150,105,0.25)',
        color: '#065F46',
      }}
      role="status"
    >
      {message}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Segmented pill toggle
// ---------------------------------------------------------------------------

interface SegmentProps<T extends string> {
  options: { value: T; label: string }[]
  value: T
  onChange: (v: T) => void
  disabled?: boolean
}

function SegmentToggle<T extends string>({ options, value, onChange, disabled }: SegmentProps<T>) {
  return (
    <div className="flex rounded-lg border border-slate-200 overflow-hidden">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          disabled={disabled}
          onClick={() => onChange(opt.value)}
          className="px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          style={{
            background: value === opt.value ? '#1E3A5F' : '#FFFFFF',
            color: value === opt.value ? '#FFFFFF' : '#64748B',
          }}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Upload preview modal
// ---------------------------------------------------------------------------

interface UploadModalProps {
  preview: BudgetUploadPreview
  onConfirm: () => void
  onCancel: () => void
  confirming: boolean
}

function UploadModal({ preview, onConfirm, onCancel, confirming }: UploadModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        className="w-full max-w-2xl rounded-2xl bg-white shadow-2xl overflow-hidden"
        style={{ border: '1px solid #E2E8F0' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
          <h2 className="text-base font-bold text-slate-800">Upload Preview</h2>
          <button type="button" onClick={onCancel} className="text-slate-400 hover:text-slate-700">
            <X size={18} aria-hidden />
          </button>
        </div>

        {/* Summary badges */}
        <div className="px-6 py-3 flex gap-2 flex-wrap border-b border-slate-100">
          <span
            className="text-xs font-semibold rounded-full px-2.5 py-1"
            style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
          >
            {preview.summary.total_changes as number ?? preview.changes.length} changes
          </span>
          {preview.unknown_line_codes.length > 0 && (
            <span
              className="text-xs font-semibold rounded-full px-2.5 py-1"
              style={{ background: 'rgba(245,158,11,0.12)', color: '#B45309' }}
            >
              {preview.unknown_line_codes.length} unknown line code(s)
            </span>
          )}
        </div>

        {/* Changes table */}
        <div className="overflow-y-auto max-h-72">
          {preview.changes.length === 0 ? (
            <p className="px-6 py-8 text-center text-sm text-slate-400">No changes detected.</p>
          ) : (
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr style={{ background: '#F4F6F9' }}>
                  <th className="py-2 px-3 text-left font-semibold text-slate-500 uppercase tracking-wider">Line code</th>
                  <th className="py-2 px-3 text-left font-semibold text-slate-500 uppercase tracking-wider">Field</th>
                  <th className="py-2 px-3 text-right font-semibold text-slate-500 uppercase tracking-wider">Old</th>
                  <th className="py-2 px-3 text-right font-semibold text-slate-500 uppercase tracking-wider">New</th>
                </tr>
              </thead>
              <tbody>
                {preview.changes.map((c, i) => (
                  <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="py-1.5 px-3 font-mono">{c.line_code}{c.level_4 ? ` / ${c.level_4}` : ''}{c.partner_id ? ` (${c.partner_id})` : ''}</td>
                    <td className="py-1.5 px-3">{c.field}</td>
                    <td className="py-1.5 px-3 text-right font-mono text-slate-400">{c.old.toLocaleString()}</td>
                    <td className="py-1.5 px-3 text-right font-mono text-slate-800">{c.new.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Unknown codes */}
        {preview.unknown_line_codes.length > 0 && (
          <div className="px-6 py-2 border-t border-slate-100 text-xs text-amber-600">
            Unknown line codes (will be skipped): {preview.unknown_line_codes.join(', ')}
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-slate-100">
          <button
            type="button"
            onClick={onCancel}
            disabled={confirming}
            className="px-4 py-1.5 rounded-lg text-xs font-medium border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={confirming || preview.changes.length === 0}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-semibold text-white disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ background: '#1E3A5F' }}
          >
            {confirming ? (
              <RefreshCw size={12} className="animate-spin" aria-hidden />
            ) : (
              <Check size={12} aria-hidden />
            )}
            Confirm upload
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Heuristics panel
// ---------------------------------------------------------------------------

type HeuristicMethod = 'prior_year' | 'trend_cagr' | 'run_rate'

interface HeuristicsPanelProps {
  heuristic: HeuristicMethod
  growthPct: number
  applying: boolean
  onHeuristicChange: (h: HeuristicMethod) => void
  onGrowthPctChange: (g: number) => void
  onPreview: () => void
  onApply: () => void
  onClose: () => void
}

function HeuristicsPanel({
  heuristic,
  growthPct,
  applying,
  onHeuristicChange,
  onGrowthPctChange,
  onPreview,
  onApply,
  onClose,
}: HeuristicsPanelProps) {
  const HEURISTIC_LABELS: Record<HeuristicMethod, string> = {
    prior_year: 'Prior Year',
    trend_cagr: 'Trend CAGR',
    run_rate: 'Run Rate',
  }

  const HEURISTIC_DESCS: Record<HeuristicMethod, string> = {
    prior_year: 'Base budget on the prior fiscal year values, optionally scaled by a growth rate.',
    trend_cagr: 'Fit a CAGR trend over multiple prior years and extrapolate forward.',
    run_rate: 'Annualise the most recent available months as a run-rate projection.',
  }

  return (
    <div
      className="rounded-xl mb-5 px-5 py-4"
      style={{
        background: '#FFFFFF',
        border: '1px solid rgba(30,58,95,0.2)',
        boxShadow: '0 2px 8px rgba(30,58,95,0.07)',
      }}
    >
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-sm font-bold text-slate-800 flex items-center gap-2">
            <Lightbulb size={15} className="text-[#1E3A5F]" aria-hidden />
            Finssentials heuristics
          </h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Generate per-position budget suggestions from GL history. Preview before applying.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-slate-400 hover:text-slate-700 transition-colors"
          aria-label="Close heuristics panel"
        >
          <X size={16} aria-hidden />
        </button>
      </div>

      {/* Method selector */}
      <div className="flex flex-wrap gap-4 mb-4">
        {(Object.keys(HEURISTIC_LABELS) as HeuristicMethod[]).map((h) => (
          <label key={h} className="flex items-start gap-2 cursor-pointer">
            <input
              type="radio"
              name="heuristic"
              value={h}
              checked={heuristic === h}
              onChange={() => onHeuristicChange(h)}
              className="mt-0.5 accent-[#1E3A5F]"
            />
            <div>
              <div className="text-xs font-semibold text-slate-700">{HEURISTIC_LABELS[h]}</div>
              <div className="text-[12px] text-slate-400">{HEURISTIC_DESCS[h]}</div>
            </div>
          </label>
        ))}
      </div>

      {/* Growth % (Prior Year only) */}
      {heuristic === 'prior_year' && (
        <div className="flex items-center gap-2 mb-4">
          <label className="text-xs font-semibold text-slate-600">Growth rate</label>
          <div className="flex items-center gap-1">
            <input
              type="number"
              value={growthPct}
              onChange={(e) => onGrowthPctChange(parseFloat(e.target.value) || 0)}
              className="w-20 rounded border border-slate-200 px-2 py-1 text-xs font-mono text-right
                focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]"
              step={0.5}
            />
            <span className="text-xs text-slate-400">%</span>
          </div>
          <span className="text-[12px] text-slate-400">Applied on top of prior year base values</span>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onPreview}
          disabled={applying}
          className="px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-200 text-slate-700 hover:bg-slate-50 disabled:opacity-40 transition-colors"
        >
          Preview suggestions
        </button>
        <button
          type="button"
          onClick={onApply}
          disabled={applying}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          style={{ background: '#1E3A5F' }}
        >
          {applying ? (
            <RefreshCw size={12} className="animate-spin" aria-hidden />
          ) : (
            <Check size={12} aria-hidden />
          )}
          Apply suggestions
        </button>
        <span className="text-[12px] text-slate-400">
          "Apply" seeds the budget from the chosen heuristic.
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// BudgetPage
// ---------------------------------------------------------------------------

type StatementId = 'PL' | 'BS'
type ViewMode = 'annual' | 'monthly'
type LevelMode = 'L3' | 'L4'
type InputMode = 'absolute' | 'growth'
type PlanningDir = 'top_down' | 'bottom_up'

export default function BudgetPage() {
  const currentYear = new Date().getFullYear()

  // ── Controls ──
  const [statement, setStatement] = useState<StatementId>('PL')
  const [fiscalYear, setFiscalYear] = useState<number>(currentYear)
  const [entity, setEntity] = useState<string>('')
  const [viewMode, setViewMode] = useState<ViewMode>('annual')
  const [level, setLevel] = useState<LevelMode>('L3')
  const [inputMode, setInputMode] = useState<InputMode>('absolute')
  const [planningDir, setPlanningDir] = useState<PlanningDir>('top_down')

  // ── Heuristics ──
  const [heuristicsOpen, setHeuristicsOpen] = useState(false)
  const [heuristic, setHeuristic] = useState<HeuristicMethod>('prior_year')
  const [growthPct, setGrowthPct] = useState<number>(0)
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [applyingHeuristic, setApplyingHeuristic] = useState(false)

  // ── Entities ──
  const [entitiesData, setEntitiesData] = useState<BudgetEntitiesResponse | null>(null)
  const [entitiesLoading, setEntitiesLoading] = useState(false)

  // ── Grid data ──
  const [plData, setPlData] = useState<BudgetTreeResponse | null>(null)
  const [bsData, setBsData] = useState<BudgetTreeResponse | null>(null)
  const [plLoading, setPlLoading] = useState(false)
  const [bsLoading, setBsLoading] = useState(false)
  const [plError, setPlError] = useState<string | null>(null)
  const [bsError, setBsError] = useState<string | null>(null)

  // ── Overrides ──
  const [plOverrides, setPlOverrides] = useState<Record<string, PositionOverride>>({})
  const [bsOverrides, setBsOverrides] = useState<Record<string, PositionOverride>>({})

  // ── Action state ──
  const [saving, setSaving] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionSuccess, setActionSuccess] = useState<string | null>(null)

  // ── Upload state ──
  const [uploadPreview, setUploadPreview] = useState<BudgetUploadPreview | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [committing, setCommitting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Stale-load guards
  const plLoadId = useRef(0)
  const bsLoadId = useRef(0)

  // ── Load entities once ──
  useEffect(() => {
    setEntitiesLoading(true)
    budgetEntities()
      .then((data) => {
        setEntitiesData(data)
        // Default to first entity
        if (data.entities.length > 0) setEntity(data.entities[0].code)
      })
      .catch(() => {
        // Non-fatal: fall back to empty entity (consolidated)
      })
      .finally(() => setEntitiesLoading(false))
  }, [])

  // ── Load PL ──
  const loadPl = useCallback(async () => {
    const id = ++plLoadId.current
    setPlLoading(true)
    setPlError(null)
    try {
      const data = await getBudgetTree({
        statement: 'PL',
        fiscal_year: fiscalYear,
        entity: entity || undefined,
        level,
        heuristic,
        growth_pct: growthPct,
        top_n: 20,
      })
      if (id !== plLoadId.current) return
      setPlData(data)
      setPlOverrides({})
    } catch (e) {
      if (id !== plLoadId.current) return
      setPlError(e instanceof Error ? e.message : 'Failed to load P&L budget')
      setPlData(null)
    } finally {
      if (id === plLoadId.current) setPlLoading(false)
    }
  }, [fiscalYear, entity, level, heuristic, growthPct])

  // ── Load BS ──
  const loadBs = useCallback(async () => {
    const id = ++bsLoadId.current
    setBsLoading(true)
    setBsError(null)
    try {
      const data = await getBudgetTree({
        statement: 'BS',
        fiscal_year: fiscalYear,
        entity: entity || undefined,
        level,
        heuristic,
        growth_pct: growthPct,
        top_n: 20,
      })
      if (id !== bsLoadId.current) return
      setBsData(data)
      setBsOverrides({})
    } catch (e) {
      if (id !== bsLoadId.current) return
      setBsError(e instanceof Error ? e.message : 'Failed to load Balance Sheet budget')
      setBsData(null)
    } finally {
      if (id === bsLoadId.current) setBsLoading(false)
    }
  }, [fiscalYear, entity, level, heuristic, growthPct])

  // Reload whenever controls change
  useEffect(() => { void loadPl() }, [loadPl])
  useEffect(() => { void loadBs() }, [loadBs])

  // Auto-dismiss success after 4 s
  useEffect(() => {
    if (!actionSuccess) return
    const t = setTimeout(() => setActionSuccess(null), 4000)
    return () => clearTimeout(t)
  }, [actionSuccess])

  // ── Derived state ──
  const hasUnsavedChanges =
    Object.keys(plOverrides).length > 0 || Object.keys(bsOverrides).length > 0
  const isBusy = saving || resetting || applyingHeuristic
  const activeData = statement === 'PL' ? plData : bsData
  const activeLoading = statement === 'PL' ? plLoading : bsLoading
  const activeError = statement === 'PL' ? plError : bsError
  const activeOverrides = statement === 'PL' ? plOverrides : bsOverrides

  function handleOverride(lineCode: string, ov: PositionOverride) {
    if (statement === 'PL') {
      setPlOverrides((prev) => ({ ...prev, [lineCode]: ov }))
    } else {
      setBsOverrides((prev) => ({ ...prev, [lineCode]: ov }))
    }
  }

  // ── Heuristics: preview (re-fetch with current heuristic params, show suggestion col) ──
  function handlePreviewSuggestions() {
    setShowSuggestions(true)
    void loadPl()
    void loadBs()
  }

  // ── Heuristics: apply (materialize the selected heuristic suggestion, then reload) ──
  async function handleApplyHeuristic() {
    setApplyingHeuristic(true)
    setActionError(null)
    try {
      const ent = entity || undefined
      await Promise.all([
        seedBudget({
          statement: 'PL',
          fiscal_year: fiscalYear,
          entity: ent,
          materialize_suggestion: true,
          heuristic,
          growth_pct: growthPct,
        }),
        seedBudget({
          statement: 'BS',
          fiscal_year: fiscalYear,
          entity: ent,
          materialize_suggestion: true,
          heuristic,
          growth_pct: growthPct,
        }),
      ])
      setActionSuccess('Heuristic suggestions applied. Reloading grid…')
      setShowSuggestions(false)
      await Promise.all([loadPl(), loadBs()])
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Apply heuristic failed')
    } finally {
      setApplyingHeuristic(false)
    }
  }

  // ── Save ──
  async function handleSave() {
    setSaving(true)
    setActionError(null)
    setActionSuccess(null)
    try {
      const ent = entity || ''
      const patches: Promise<unknown>[] = []

      const buildPatches = (
        overrides: Record<string, PositionOverride>,
        stmt: 'PL' | 'BS',
      ) => {
        for (const [lineCode, ov] of Object.entries(overrides)) {
          // ── L4 child edits: one PUT /cell per changed L4 child ──
          // Editing an L4 child sends level_4=<name> so the backend writes an
          // L4 row (XOR-clears the L3-level '' row for that scope). The L3
          // position total becomes the Σ of L4 rows server-side.
          if (ov.l4 && Object.keys(ov.l4).length > 0) {
            for (const [l4Key, l4ov] of Object.entries(ov.l4)) {
              const payload: Parameters<typeof putBudgetCell>[0] = {
                statement: stmt,
                line_code: lineCode,
                entity: ent,
                fiscal_year: fiscalYear,
                level_4: l4Key,
              }
              if (inputMode === 'growth') {
                // Growth mode: send annual as the resolved absolute value
                // (GrowthInput already resolves base*(1+pct/100) into onChange).
                if (l4ov.months) payload.months = l4ov.months
                else if (l4ov.annual !== undefined) payload.annual = l4ov.annual
              } else {
                // Absolute mode: annual → seasonalized server-side; monthly → 12 values
                if (l4ov.months) payload.months = l4ov.months
                else if (l4ov.annual !== undefined) payload.annual = l4ov.annual
              }
              patches.push(putBudgetCell(payload))
            }
            // When there are ONLY L4 overrides (no L3-level edit), skip the
            // PATCH /position for this line — the L4 PUT calls handle it.
            if (!ov.months && ov.annual === undefined && !ov.partners) continue
          }

          // ── L3-level position edit (no L4, or L3 edit alongside L4) ──
          const pos: { months?: number[]; annual?: number } = {}
          if (ov.months) pos.months = ov.months
          else if (ov.annual !== undefined) pos.annual = ov.annual

          const partners = ov.partners
            ? Object.entries(ov.partners).map(([pid, pov]) => ({
                partner_id: pid,
                ...(pov.months ? { months: pov.months } : {}),
                ...(pov.annual !== undefined && !pov.months ? { annual: pov.annual } : {}),
              }))
            : undefined

          if (Object.keys(pos).length > 0 || partners) {
            patches.push(
              patchBudgetPosition({
                statement: stmt,
                line_code: lineCode,
                entity: ent,
                fiscal_year: fiscalYear,
                level_4: '',
                position: Object.keys(pos).length > 0 ? pos : undefined,
                partners,
              }),
            )
          }
        }
      }

      buildPatches(plOverrides, 'PL')
      buildPatches(bsOverrides, 'BS')

      if (patches.length === 0) {
        setActionSuccess('No changes to save.')
        setSaving(false)
        return
      }

      await Promise.all(patches)
      setActionSuccess(`Saved ${patches.length} position(s). Reloading…`)
      await Promise.all([loadPl(), loadBs()])
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  // ── Reset ──
  async function handleReset() {
    if (
      !window.confirm(
        `Reset the manual budget for FY${fiscalYear}? This reverts all positions to forecast/plan and cannot be undone.`,
      )
    )
      return

    setResetting(true)
    setActionError(null)
    setActionSuccess(null)
    try {
      const ent = entity || undefined
      await Promise.all([
        deleteBudget('PL', fiscalYear, ent),
        deleteBudget('BS', fiscalYear, ent),
      ])
      setActionSuccess('Budget reset. Readers will now use forecast/plan. Reloading…')
      await Promise.all([loadPl(), loadBs()])
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Reset failed')
    } finally {
      setResetting(false)
    }
  }

  // ── Download template ──
  async function handleDownloadTemplate() {
    setActionError(null)
    try {
      await downloadBudgetTemplate({
        statement,
        fiscal_year: fiscalYear,
        entity: entity || '',
        level,
        top_n: 20,
      })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Template download failed')
    }
  }

  // ── Upload: file chosen ──
  async function handleFileChosen(file: File) {
    setUploading(true)
    setUploadError(null)
    try {
      const preview = await budgetUpload(file, statement, fiscalYear, entity || '', 20)
      setUploadPreview(preview)
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
      // Reset input so the same file can be re-chosen
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  // ── Upload: commit ──
  async function handleCommitUpload() {
    if (!uploadPreview) return
    setCommitting(true)
    try {
      await budgetCommit({
        file_id: uploadPreview.file_id,
        statement: uploadPreview.statement,
        entity: uploadPreview.entity,
        fiscal_year: uploadPreview.fiscal_year,
      })
      setUploadPreview(null)
      setActionSuccess('Upload committed. Reloading grid…')
      await Promise.all([loadPl(), loadBs()])
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Commit failed')
      setUploadPreview(null)
    } finally {
      setCommitting(false)
    }
  }

  // ── Year options ──
  const yearOptions = Array.from({ length: 5 }, (_, i) => currentYear - 2 + i)

  // ── Entity options ──
  const entityOptions: { code: string; label: string }[] = entitiesData
    ? [
        ...entitiesData.entities.map((e) => ({ code: e.code, label: e.label })),
        ...(entitiesData.can_consolidate ? [{ code: '', label: 'Consolidated' }] : []),
      ]
    : entity
    ? [{ code: entity, label: entity }]
    : [{ code: '', label: 'Loading…' }]

  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <div className="max-w-[1680px] mx-auto px-6 lg:px-8 py-8">

        {/* Page header */}
        <div className="mb-5">
          <div
            className="text-xs font-semibold uppercase tracking-widest mb-1.5"
            style={{ color: '#1E3A5F' }}
          >
            Admin · Planning
          </div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: '#111827' }}>
            Budget Planning
          </h1>
        </div>

        {/* Action banners */}
        {actionError && (
          <ErrorBanner message={actionError} onDismiss={() => setActionError(null)} />
        )}
        {uploadError && (
          <ErrorBanner message={uploadError} onDismiss={() => setUploadError(null)} />
        )}
        {actionSuccess && <SuccessBanner message={actionSuccess} />}

        {/* ── Main control bar ── */}
        <div
          className="rounded-xl mb-5 px-3 py-2 flex flex-wrap items-center gap-2"
          style={{
            background: '#FFFFFF',
            border: '1px solid #E2E8F0',
            boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
          }}
        >
          {/* Statement tabs */}
          <SegmentToggle
            options={[
              { value: 'PL' as StatementId, label: 'P&L' },
              { value: 'BS' as StatementId, label: 'Balance Sheet' },
            ]}
            value={statement}
            onChange={setStatement}
            disabled={isBusy}
          />

          <div className="w-px h-5 bg-slate-200 mx-1" />

          {/* Entity */}
          <select
            value={entity}
            disabled={entitiesLoading || isBusy}
            onChange={(e) => setEntity(e.target.value)}
            className="rounded-lg border border-slate-200 px-2 py-1.5 text-xs text-slate-700
              focus:outline-none focus:ring-2 focus:ring-[#1E3A5F] disabled:opacity-50"
          >
            {entityOptions.map((opt) => (
              <option key={opt.code} value={opt.code}>
                {opt.label}
              </option>
            ))}
          </select>

          {/* Year */}
          <div className="flex items-center gap-1">
            <select
              value={fiscalYear}
              disabled={isBusy}
              onChange={(e) => setFiscalYear(Number(e.target.value))}
              className="rounded-lg border border-slate-200 px-2 py-1.5 text-xs text-slate-700
                focus:outline-none focus:ring-2 focus:ring-[#1E3A5F] disabled:opacity-50"
            >
              {yearOptions.map((y) => (
                <option key={y} value={y}>FY{y}</option>
              ))}
            </select>
          </div>

          <div className="w-px h-5 bg-slate-200 mx-1" />

          {/* View mode */}
          <SegmentToggle
            options={[
              { value: 'annual' as ViewMode, label: 'Annual' },
              { value: 'monthly' as ViewMode, label: 'Monthly' },
            ]}
            value={viewMode}
            onChange={setViewMode}
          />

          {/* Level */}
          <SegmentToggle
            options={[
              { value: 'L3' as LevelMode, label: 'L3' },
              { value: 'L4' as LevelMode, label: 'L4' },
            ]}
            value={level}
            onChange={setLevel}
            disabled={isBusy}
          />

          {/* Input mode */}
          <SegmentToggle
            options={[
              { value: 'absolute' as InputMode, label: 'Absolute' },
              { value: 'growth' as InputMode, label: 'Growth %' },
            ]}
            value={inputMode}
            onChange={setInputMode}
          />

          {/* Planning direction */}
          <SegmentToggle
            options={[
              { value: 'top_down' as PlanningDir, label: 'Top-down' },
              { value: 'bottom_up' as PlanningDir, label: 'Bottom-up' },
            ]}
            value={planningDir}
            onChange={setPlanningDir}
          />

          {/* Unsaved indicator */}
          {hasUnsavedChanges && !saving && (
            <span
              className="text-[12px] font-medium px-2 py-0.5 rounded-full"
              style={{ background: 'rgba(245,158,11,0.12)', color: '#B45309' }}
            >
              Unsaved changes
            </span>
          )}

          {/* Action buttons — pushed to right */}
          <div className="flex items-center gap-1.5 ml-auto flex-wrap">
            {/* Finssentials heuristics */}
            <button
              type="button"
              onClick={() => setHeuristicsOpen((v) => !v)}
              disabled={isBusy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-40"
              style={{
                background: heuristicsOpen ? 'rgba(30,58,95,0.1)' : '#F4F6F9',
                color: '#1E3A5F',
                border: `1px solid ${heuristicsOpen ? 'rgba(30,58,95,0.25)' : '#E2E8F0'}`,
              }}
              aria-expanded={heuristicsOpen}
            >
              <Lightbulb size={13} aria-hidden />
              <span className="hidden sm:inline">Finssentials heuristics</span>
              <ChevronDown
                size={12}
                style={{ transform: heuristicsOpen ? 'rotate(180deg)' : 'none' }}
                className="transition-transform duration-200"
                aria-hidden
              />
            </button>

            {/* Download template */}
            <button
              type="button"
              onClick={() => void handleDownloadTemplate()}
              disabled={isBusy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-40"
              style={{ background: '#F4F6F9', color: '#1E3A5F', border: '1px solid #E2E8F0' }}
              title="Download Excel template for bulk entry"
            >
              <Download size={13} aria-hidden />
              <span className="hidden sm:inline">Download template</span>
            </button>

            {/* Upload */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={isBusy || uploading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-40"
              style={{ background: '#F4F6F9', color: '#1E3A5F', border: '1px solid #E2E8F0' }}
              title="Upload filled Excel template"
            >
              {uploading ? (
                <RefreshCw size={13} className="animate-spin" aria-hidden />
              ) : (
                <Upload size={13} aria-hidden />
              )}
              <span className="hidden sm:inline">{uploading ? 'Uploading…' : 'Upload'}</span>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xls"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void handleFileChosen(file)
              }}
            />

            {/* Reset */}
            <button
              type="button"
              onClick={() => void handleReset()}
              disabled={isBusy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-40"
              style={{ background: '#FEF2F2', color: '#991B1B', border: '1px solid #FECACA' }}
              title="Delete all manual budget rows"
            >
              <Trash2 size={13} aria-hidden />
              <span className="hidden sm:inline">{resetting ? 'Resetting…' : 'Reset'}</span>
            </button>

            {/* Save */}
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={isBusy || !hasUnsavedChanges}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              style={{ background: '#1E3A5F', color: '#FFFFFF', border: '1px solid #1E3A5F' }}
              title={hasUnsavedChanges ? 'Save all pending changes' : 'No changes to save'}
            >
              {saving ? (
                <RefreshCw size={13} className="animate-spin" aria-hidden />
              ) : (
                <Save size={13} aria-hidden />
              )}
              <span>{saving ? 'Saving…' : 'Save'}</span>
            </button>
          </div>
        </div>

        {/* ── Heuristics panel ── */}
        {heuristicsOpen && (
          <HeuristicsPanel
            heuristic={heuristic}
            growthPct={growthPct}
            applying={applyingHeuristic}
            onHeuristicChange={setHeuristic}
            onGrowthPctChange={setGrowthPct}
            onPreview={handlePreviewSuggestions}
            onApply={() => void handleApplyHeuristic()}
            onClose={() => setHeuristicsOpen(false)}
          />
        )}

        {/* ── Planning direction hint ── */}
        {planningDir === 'bottom_up' && (
          <div
            className="rounded-lg mb-4 px-4 py-2 text-xs text-slate-500"
            style={{ background: 'rgba(30,58,95,0.04)', border: '1px solid rgba(30,58,95,0.1)' }}
          >
            Bottom-up mode: edit L4 / partner rows and the L3 position total is derived from the sum.
            Top-down mode: edit the L3 position and the server distributes to L4 / partners.
          </div>
        )}

        {/* ── Tab content ── */}
        {activeError && !activeLoading && (
          <ErrorBanner message={activeError} />
        )}

        {activeLoading ? (
          <Spinner label="Loading budget grid…" />
        ) : (
          <BudgetTreeGrid
            positions={activeData?.positions ?? []}
            statement={statement}
            fiscalYear={fiscalYear}
            entity={entity}
            granularityByPosition={Object.fromEntries(
              (activeData?.positions ?? []).map((p) => [`${statement}|${p.line_code}`, level as PositionGranularity])
            )}
            viewMode={viewMode}
            inputMode={inputMode}
            inputModeByPosition={{}}
            startMode="heuristic"
            overrides={activeOverrides}
            onOverride={handleOverride}
            saving={saving}
            showSuggestions={showSuggestions}
          />
        )}

      </div>

      {/* ── Upload preview modal ── */}
      {uploadPreview && (
        <UploadModal
          preview={uploadPreview}
          onConfirm={() => void handleCommitUpload()}
          onCancel={() => setUploadPreview(null)}
          confirming={committing}
        />
      )}
    </div>
  )
}
