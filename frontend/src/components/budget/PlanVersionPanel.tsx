import { useCallback, useEffect, useState } from 'react'
import { Check, Layers, Plus, RefreshCw } from 'lucide-react'

import {
  createPlanVersion,
  listPlanVersions,
  updatePlanVersion,
  type PlanVersion,
} from '../../lib/gdpduApi'

type Props = {
  /** Scope the version list to the budget page's current statement + year. */
  statement: 'PL' | 'BS'
  fiscalYear: number
  disabled?: boolean
}

/**
 * Plan-version selector (Phase 4 follow-on). Lists the plan versions for the current
 * (statement, fiscal_year) scope with an "active" radio and an "include in reporting"
 * checkbox, wired to GET/POST/PATCH /api/v1/budget/versions.
 *
 * Degrades gracefully: if the backend reports ``supported === false`` (un-migrated DB
 * without ``dim_plan_version``) — or the probe errors — the whole panel is hidden and
 * reporting stays on the legacy single-plan path.
 */
export default function PlanVersionPanel({ statement, fiscalYear, disabled }: Props) {
  const [supported, setSupported] = useState<boolean | null>(null)
  const [versions, setVersions] = useState<PlanVersion[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [newLabel, setNewLabel] = useState('')
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await listPlanVersions({ statement, fiscal_year: fiscalYear })
      setSupported(res.supported)
      setVersions(res.versions)
      setError(null)
    } catch (e) {
      // Any hard failure → hide the panel (keep the budget page uncluttered).
      setSupported(false)
    } finally {
      setLoading(false)
    }
  }, [statement, fiscalYear])

  useEffect(() => {
    void load()
  }, [load])

  // Hidden until we positively know versions are supported (also covers un-migrated DB
  // and probe errors — supported stays null/false → nothing renders).
  if (supported !== true) return null

  async function handleActivate(v: PlanVersion) {
    if (v.is_active || disabled) return
    setBusyId(v.plan_version_id)
    setError(null)
    try {
      await updatePlanVersion(v.plan_version_id, { activate: true })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to activate version')
    } finally {
      setBusyId(null)
    }
  }

  async function handleToggleInclude(v: PlanVersion, next: boolean) {
    if (disabled) return
    setBusyId(v.plan_version_id)
    setError(null)
    try {
      await updatePlanVersion(v.plan_version_id, { include_in_reporting: next })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update version')
    } finally {
      setBusyId(null)
    }
  }

  async function handleCreate() {
    const label = newLabel.trim()
    if (!label || disabled) return
    setCreating(true)
    setError(null)
    try {
      await createPlanVersion({
        statement,
        fiscal_year: fiscalYear,
        label,
        activate: true,
        include_in_reporting: true,
      })
      setNewLabel('')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create version')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div
      className="rounded-xl mb-5 px-5 py-4"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <h3 className="text-sm font-bold text-slate-800 flex items-center gap-2">
            <Layers size={15} className="text-[#1E3A5F]" aria-hidden />
            Plan versions — {statement} · FY{fiscalYear}
          </h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Exactly one version is active per statement &amp; year. Only versions included in
            reporting feed the forecast/coverage columns.
          </p>
        </div>
        {loading && <RefreshCw size={14} className="animate-spin text-slate-400 mt-1" aria-hidden />}
      </div>

      {error && (
        <div
          className="mb-3 rounded-lg px-3 py-2 text-xs font-medium"
          style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(220,38,38,0.3)', color: '#991B1B' }}
          role="alert"
        >
          {error}
        </div>
      )}

      {versions.length === 0 ? (
        <p className="text-xs text-slate-400 py-2">
          No plan versions yet for this scope. Create one below to version this plan.
        </p>
      ) : (
        <div className="flex flex-col gap-1.5 mb-3">
          {versions.map(v => {
            const rowBusy = busyId === v.plan_version_id
            return (
              <div
                key={v.plan_version_id}
                className="flex items-center justify-between gap-3 rounded-lg px-3 py-2"
                style={{
                  background: v.is_active ? 'rgba(30,58,95,0.05)' : '#F8FAFC',
                  border: `1px solid ${v.is_active ? 'rgba(30,58,95,0.2)' : '#EEF2F6'}`,
                }}
              >
                <label className="flex items-center gap-2.5 cursor-pointer min-w-0">
                  <input
                    type="radio"
                    name={`plan-version-${statement}`}
                    checked={v.is_active}
                    disabled={disabled || rowBusy}
                    onChange={() => void handleActivate(v)}
                    className="accent-[#1E3A5F]"
                  />
                  <span className="text-sm font-medium text-slate-700 truncate">{v.label}</span>
                  {v.is_active && (
                    <span
                      className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded-full flex items-center gap-1"
                      style={{ background: 'rgba(30,58,95,0.1)', color: '#1E3A5F' }}
                    >
                      <Check size={10} strokeWidth={3} aria-hidden /> Active
                    </span>
                  )}
                </label>
                <label className="flex items-center gap-2 cursor-pointer flex-shrink-0 text-xs text-slate-600">
                  <input
                    type="checkbox"
                    checked={v.include_in_reporting}
                    disabled={disabled || rowBusy}
                    onChange={e => void handleToggleInclude(v, e.target.checked)}
                    className="accent-[#1E3A5F]"
                  />
                  Include in reporting
                </label>
              </div>
            )
          })}
        </div>
      )}

      {/* Create a new version */}
      <div className="flex items-center gap-2 pt-1">
        <input
          type="text"
          value={newLabel}
          disabled={disabled || creating}
          onChange={e => setNewLabel(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') void handleCreate()
          }}
          placeholder="New version label (e.g. Budget v2)"
          className="flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-700
            focus:outline-none focus:ring-2 focus:ring-[#1E3A5F] disabled:opacity-50"
          maxLength={200}
        />
        <button
          type="button"
          onClick={() => void handleCreate()}
          disabled={disabled || creating || !newLabel.trim()}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white
            disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          style={{ background: '#1E3A5F' }}
          title="Create and activate a new plan version for this scope"
        >
          {creating ? (
            <RefreshCw size={12} className="animate-spin" aria-hidden />
          ) : (
            <Plus size={12} aria-hidden />
          )}
          New version
        </button>
      </div>
    </div>
  )
}
