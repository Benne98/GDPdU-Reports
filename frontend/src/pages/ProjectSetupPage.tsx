/**
 * ProjectSetupPage — guided no-code wizard for first-time / changed project configuration.
 *
 * Steps:
 *   1  Project basics   — name, FY start month, entities (code / prefix / name)
 *   2  GL Upload        — links to the existing Data Update (IngestionPage) flow
 *   3  CoA Mapping      — choose library auto-suggest OR client-CoA 1:1; links to MappingEditorPage
 *   4  Opening balances — mode (in_data / file / carry_forward) + optional first-year file hint
 *   5  Partner master   — source (files / gdpdu)
 *   6  Sales / Cost     — display labels
 *   7  Review & Save    — PUT config, optional rebuild trigger
 *
 * The wizard does NOT replicate GL upload mechanics — it links/explains the existing
 * IngestionPage flow and saves project configuration only.
 *
 * Backend endpoints (Phase 7 parallel):
 *   GET  /api/v1/projects/{id}         → ProjectConfigResponse
 *   PUT  /api/v1/projects/{id}         → ProjectConfigResponse
 *   POST /api/v1/projects/{id}/rebuild → RebuildResponse
 *
 * Graceful degradation: if the backend endpoints are not yet live the page shows
 * an info banner and lets the user fill in + save when they become available.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ProjectConfig, type ProjectConfigResponse, type ProjectEntity } from '../lib/api'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PROJECT_ID = 'default'

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

const WIZARD_STEPS = [
  'Project basics',
  'GL data',
  'CoA mapping',
  'Opening balances',
  'Partner master',
  'Labels',
  'Review & save',
] as const

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

function defaultConfig(): ProjectConfig {
  return {
    name:                  '',
    fy_start_month:        1,
    entities:              [{ code: '', prefix: '', name: '' }],
    opening_balance_mode:  'in_data',
    net_profit_source:     'report_inject',
    mapping_source:        'library',
    partner_master_source: 'files',
    sales_label:           'Revenue',
    cost_label:            'Cost of materials',
  }
}

// ---------------------------------------------------------------------------
// Small shared primitives
// ---------------------------------------------------------------------------

function StepCard({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
      <div className="mt-5">{children}</div>
    </div>
  )
}

function InfoBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50/80 px-4 py-3 text-sm text-blue-900">
      {children}
    </div>
  )
}

function WarnBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      {children}
    </div>
  )
}

function ErrorBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
      {children}
    </div>
  )
}

function SuccessBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
      {children}
    </div>
  )
}

function Label({ children, required }: { children: React.ReactNode; required?: boolean }) {
  return (
    <span className="text-sm font-medium text-slate-700">
      {children}
      {required && <span className="ml-0.5 text-red-500" aria-hidden>*</span>}
    </span>
  )
}

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label required={required}>{label}</Label>
      {children}
    </div>
  )
}

function textInput(
  value: string,
  onChange: (v: string) => void,
  placeholder?: string,
  extra?: React.InputHTMLAttributes<HTMLInputElement>,
) {
  return (
    <input
      type="text"
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      className="rounded-md border border-slate-300 px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
      {...extra}
    />
  )
}

// ---------------------------------------------------------------------------
// Stepper
// ---------------------------------------------------------------------------

function Stepper({ current, steps }: { current: number; steps: readonly string[] }) {
  return (
    <nav aria-label="Wizard steps" className="mb-8">
      <ol className="flex items-center flex-wrap gap-y-2">
        {steps.map((label, idx) => {
          const done   = idx < current
          const active = idx === current
          return (
            <li key={label} className="flex items-center">
              <div className="flex flex-col items-center">
                <span
                  className={`flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold border-2 transition ${
                    done
                      ? 'border-blue-600 bg-blue-600 text-white'
                      : active
                        ? 'border-blue-600 bg-white text-blue-600'
                        : 'border-slate-300 bg-white text-slate-400'
                  }`}
                >
                  {done ? <span aria-hidden>&#10003;</span> : <span>{idx + 1}</span>}
                </span>
                <span
                  className={`mt-1 text-xs font-medium whitespace-nowrap ${
                    active ? 'text-blue-700' : done ? 'text-slate-600' : 'text-slate-400'
                  }`}
                >
                  {label}
                </span>
              </div>
              {idx < steps.length - 1 && (
                <div
                  className={`mx-1 mb-4 h-0.5 w-8 flex-shrink-0 rounded transition ${
                    idx < current ? 'bg-blue-600' : 'bg-slate-200'
                  }`}
                />
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

// ---------------------------------------------------------------------------
// Nav buttons
// ---------------------------------------------------------------------------

function NavButtons({
  step,
  totalSteps,
  onBack,
  onNext,
  nextLabel,
  nextDisabled,
  nextLoading,
}: {
  step: number
  totalSteps: number
  onBack: () => void
  onNext: () => void
  nextLabel?: string
  nextDisabled?: boolean
  nextLoading?: boolean
}) {
  const isLast = step === totalSteps - 1
  return (
    <div className="mt-6 flex items-center justify-between">
      <button
        type="button"
        onClick={onBack}
        disabled={step === 0}
        className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
      >
        Back
      </button>
      {!isLast && (
        <button
          type="button"
          onClick={onNext}
          disabled={nextDisabled || nextLoading}
          className="rounded-md bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {nextLoading ? 'Loading...' : nextLabel ?? 'Next'}
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 1 — Project basics
// ---------------------------------------------------------------------------

function Step1Basics({
  config,
  onChange,
}: {
  config: ProjectConfig
  onChange: (c: ProjectConfig) => void
}) {
  function set<K extends keyof ProjectConfig>(k: K, v: ProjectConfig[K]) {
    onChange({ ...config, [k]: v })
  }

  function setEntity(idx: number, field: keyof ProjectEntity, value: string) {
    const next = config.entities.map((e, i) => i === idx ? { ...e, [field]: value } : e)
    set('entities', next)
  }

  function addEntity() {
    set('entities', [...config.entities, { code: '', prefix: '', name: '' }])
  }

  function removeEntity(idx: number) {
    if (config.entities.length <= 1) return
    set('entities', config.entities.filter((_, i) => i !== idx))
  }

  const nameValid     = config.name.trim().length > 0
  const entitiesValid = config.entities.every(e => e.code.trim() && e.prefix.trim() && e.name.trim())

  return (
    <StepCard
      title="Project basics"
      subtitle="Name your project, define fiscal year start and the legal entities in scope."
    >
      <div className="space-y-6">
        <Field label="Project name" required>
          {textInput(config.name, v => set('name', v), 'e.g. Acme GmbH FDD')}
        </Field>

        <Field label="Fiscal year start month" required>
          <select
            value={config.fy_start_month}
            onChange={e => set('fy_start_month', Number(e.target.value))}
            className="rounded-md border border-slate-300 px-3 py-2 text-sm w-52 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {MONTH_NAMES.map((name, i) => (
              <option key={i + 1} value={i + 1}>{name}</option>
            ))}
          </select>
          <p className="text-xs text-slate-400 mt-1">
            If the fiscal year starts in January, leave as January (calendar year).
          </p>
        </Field>

        <div>
          <div className="flex items-center justify-between mb-3">
            <Label required>Legal entities</Label>
            <button
              type="button"
              onClick={addEntity}
              className="text-xs font-medium text-blue-600 hover:underline"
            >
              + Add entity
            </button>
          </div>
          <div className="space-y-3">
            {config.entities.map((e, idx) => (
              <div key={idx} className="flex gap-2 items-start">
                <div className="flex-1 grid grid-cols-3 gap-2">
                  <div>
                    {idx === 0 && <p className="text-xs text-slate-400 mb-1">Code (GL prefix)</p>}
                    <input
                      type="text"
                      value={e.code}
                      onChange={ev => setEntity(idx, 'code', ev.target.value)}
                      placeholder="e.g. DE"
                      maxLength={10}
                      className="rounded-md border border-slate-300 px-2.5 py-1.5 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                  <div>
                    {idx === 0 && <p className="text-xs text-slate-400 mb-1">Account prefix</p>}
                    <input
                      type="text"
                      value={e.prefix}
                      onChange={ev => setEntity(idx, 'prefix', ev.target.value)}
                      placeholder="e.g. Atlas"
                      maxLength={10}
                      className="rounded-md border border-slate-300 px-2.5 py-1.5 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                  <div>
                    {idx === 0 && <p className="text-xs text-slate-400 mb-1">Display name</p>}
                    <input
                      type="text"
                      value={e.name}
                      onChange={ev => setEntity(idx, 'name', ev.target.value)}
                      placeholder="e.g. Atlas GmbH"
                      className="rounded-md border border-slate-300 px-2.5 py-1.5 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                </div>
                {config.entities.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeEntity(idx)}
                    className="mt-5 text-xs text-red-500 hover:text-red-700 flex-shrink-0"
                    aria-label="Remove entity"
                  >
                    Remove
                  </button>
                )}
              </div>
            ))}
          </div>
          {!entitiesValid && (
            <p className="mt-2 text-xs text-amber-600">
              All entity rows must have a code, prefix and name.
            </p>
          )}
        </div>

        {!(nameValid && entitiesValid) && (
          <WarnBox>Fill in the project name and all entity rows to continue.</WarnBox>
        )}
      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 2 — GL data upload (links to IngestionPage)
// ---------------------------------------------------------------------------

function Step2GlUpload() {
  return (
    <StepCard
      title="GL data upload"
      subtitle="Upload your GDPdU general ledger file and map columns using the Data Update wizard."
    >
      <div className="space-y-5">
        <InfoBox>
          <strong>This step uses the existing Data Update wizard.</strong> The wizard handles
          GL file upload, column mapping, validation and commit — and saves a reusable mapping
          profile so monthly updates never need remapping.
        </InfoBox>

        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 space-y-3">
          <h3 className="text-sm font-semibold text-slate-800">What happens in the wizard</h3>
          <ol className="list-decimal pl-5 space-y-1 text-sm text-slate-600">
            <li>Upload your GDPdU export (CSV, XLSX or Parquet).</li>
            <li>Assign entity and map columns (Posting Date, Account, Amount, etc.).</li>
            <li>Configure sign logic, locale and AR/AP linking strategy.</li>
            <li>
              Save a <strong>mapping profile</strong> — future monthly updates reuse this
              profile automatically.
            </li>
            <li>Run validation checks and commit to the database.</li>
          </ol>
        </div>

        <div className="flex gap-3">
          <Link
            to="/ingestion"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700"
          >
            Open Data Update wizard
            <span aria-hidden className="text-xs opacity-70">(new tab)</span>
          </Link>
        </div>

        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          <strong>Tip — monthly updates:</strong> Once your mapping profile is saved, routine
          monthly bookings can be appended without redoing any mapping. Use the{' '}
          <Link to="/ingestion" className="underline font-medium">Data Update</Link> flow
          directly — no need to re-run this setup wizard.
        </div>

        <p className="text-xs text-slate-400">
          Return here and click <strong>Next</strong> once the GL data has been committed.
        </p>
      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 3 — CoA mapping
// ---------------------------------------------------------------------------

function Step3CoaMapping({
  config,
  onChange,
}: {
  config: ProjectConfig
  onChange: (c: ProjectConfig) => void
}) {
  function set<K extends keyof ProjectConfig>(k: K, v: ProjectConfig[K]) {
    onChange({ ...config, [k]: v })
  }

  return (
    <StepCard
      title="Chart-of-accounts mapping"
      subtitle="Choose how account classifications are sourced and fine-tune the mapping structure."
    >
      <div className="space-y-6">
        <fieldset>
          <legend className="text-sm font-semibold text-slate-700 mb-3">Mapping source</legend>
          <div className="space-y-3">
            <label className="flex gap-3 cursor-pointer rounded-lg border border-slate-200 p-3 hover:bg-slate-50 has-[:checked]:border-blue-300 has-[:checked]:bg-blue-50/50">
              <input
                type="radio"
                name="mapping_source"
                value="library"
                checked={config.mapping_source === 'library'}
                onChange={() => set('mapping_source', 'library')}
                className="accent-blue-600 mt-0.5 flex-shrink-0"
              />
              <div>
                <p className="text-sm font-medium text-slate-800">Finssentials Standard Library</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Accounts are auto-classified from the built-in mapping library
                  (seeded from the standard SKR04/SKR03 structure). Unmatched accounts
                  can be remapped manually in the CoA Editor.
                </p>
              </div>
            </label>

            <label className="flex gap-3 cursor-pointer rounded-lg border border-slate-200 p-3 hover:bg-slate-50 has-[:checked]:border-blue-300 has-[:checked]:bg-blue-50/50">
              <input
                type="radio"
                name="mapping_source"
                value="client_coa"
                checked={config.mapping_source === 'client_coa'}
                onChange={() => set('mapping_source', 'client_coa')}
                className="accent-blue-600 mt-0.5 flex-shrink-0"
              />
              <div>
                <p className="text-sm font-medium text-slate-800">Client Chart of Accounts (1:1)</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Upload the client's own Sachkontenstamm (account master) file via the
                  account-mapping mode in the Data Update wizard. The client's classification
                  is used directly — no library lookup.
                </p>
              </div>
            </label>
          </div>
        </fieldset>

        {config.mapping_source === 'client_coa' && (
          <InfoBox>
            Upload the Sachkontenstamm file via{' '}
            <Link to="/ingestion?mode=account-mapping" className="underline font-medium">
              Data Update — Account mapping mode
            </Link>
            . Return here once the account mapping is committed.
          </InfoBox>
        )}

        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 space-y-2">
          <h3 className="text-sm font-semibold text-slate-800">Fine-tune with the CoA Editor</h3>
          <p className="text-xs text-slate-600">
            After the initial mapping is applied you can remap individual accounts, adjust the
            P&amp;L / Balance Sheet hierarchy and drag-and-drop reorder sections.
          </p>
          <Link
            to="/mapping-editor"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-blue-600 hover:underline"
          >
            Open CoA Editor
            <span aria-hidden className="opacity-60">(new tab)</span>
          </Link>
        </div>
      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 4 — Opening balances
// ---------------------------------------------------------------------------

function Step4OpeningBalances({
  config,
  onChange,
}: {
  config: ProjectConfig
  onChange: (c: ProjectConfig) => void
}) {
  function set<K extends keyof ProjectConfig>(k: K, v: ProjectConfig[K]) {
    onChange({ ...config, [k]: v })
  }

  return (
    <StepCard
      title="Opening balances"
      subtitle="How should the pipeline handle balance sheet opening balances?"
    >
      <div className="space-y-6">
        <fieldset>
          <legend className="text-sm font-semibold text-slate-700 mb-3">Opening balance mode</legend>
          <div className="space-y-3">
            <label className="flex gap-3 cursor-pointer rounded-lg border border-slate-200 p-3 hover:bg-slate-50 has-[:checked]:border-blue-300 has-[:checked]:bg-blue-50/50">
              <input
                type="radio"
                name="opening_balance_mode"
                value="in_data"
                checked={config.opening_balance_mode === 'in_data'}
                onChange={() => set('opening_balance_mode', 'in_data')}
                className="accent-blue-600 mt-0.5 flex-shrink-0"
              />
              <div>
                <p className="text-sm font-medium text-slate-800">Already included in GL data</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  The GDPdU export already contains opening balance rows (e.g. entry_type EB).
                  The pipeline uses them as-is — no extra file required.
                </p>
              </div>
            </label>

            <label className="flex gap-3 cursor-pointer rounded-lg border border-slate-200 p-3 hover:bg-slate-50 has-[:checked]:border-blue-300 has-[:checked]:bg-blue-50/50">
              <input
                type="radio"
                name="opening_balance_mode"
                value="file"
                checked={config.opening_balance_mode === 'file'}
                onChange={() => set('opening_balance_mode', 'file')}
                className="accent-blue-600 mt-0.5 flex-shrink-0"
              />
              <div>
                <p className="text-sm font-medium text-slate-800">Separate first-year file</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Provide a separate opening balance file for the first fiscal year.
                  Subsequent years are carry-forwarded automatically from the prior year's
                  closing balance.
                </p>
              </div>
            </label>

            <label className="flex gap-3 cursor-pointer rounded-lg border border-slate-200 p-3 hover:bg-slate-50 has-[:checked]:border-blue-300 has-[:checked]:bg-blue-50/50">
              <input
                type="radio"
                name="opening_balance_mode"
                value="carry_forward"
                checked={config.opening_balance_mode === 'carry_forward'}
                onChange={() => set('opening_balance_mode', 'carry_forward')}
                className="accent-blue-600 mt-0.5 flex-shrink-0"
              />
              <div>
                <p className="text-sm font-medium text-slate-800">Carry-forward (all years)</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  All opening balances are synthesised from cumulative prior-year GL movements.
                  Use when no separate EB file is available and the GL export covers all years
                  from the start.
                </p>
              </div>
            </label>
          </div>
        </fieldset>

        {config.opening_balance_mode === 'file' && (
          <InfoBox>
            Upload the first-year opening balance file via the{' '}
            <Link to="/ingestion" className="underline font-medium">Data Update wizard</Link>{' '}
            (use a separate upload with the EB rows only). The backend expects the file to be
            committed before the rebuild runs.
          </InfoBox>
        )}

        <InfoBox>
          This setting is saved as project configuration and applied during every rebuild.
          You do not need to re-set it for monthly updates.
        </InfoBox>
      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 5 — Partner master data
// ---------------------------------------------------------------------------

function Step5PartnerMaster({
  config,
  onChange,
}: {
  config: ProjectConfig
  onChange: (c: ProjectConfig) => void
}) {
  function set<K extends keyof ProjectConfig>(k: K, v: ProjectConfig[K]) {
    onChange({ ...config, [k]: v })
  }

  return (
    <StepCard
      title="Partner master data"
      subtitle="Where should Debitoren (customer) and Kreditorenstamm (supplier) data come from?"
    >
      <div className="space-y-6">
        <fieldset>
          <legend className="text-sm font-semibold text-slate-700 mb-3">Partner master source</legend>
          <div className="space-y-3">
            <label className="flex gap-3 cursor-pointer rounded-lg border border-slate-200 p-3 hover:bg-slate-50 has-[:checked]:border-blue-300 has-[:checked]:bg-blue-50/50">
              <input
                type="radio"
                name="partner_master_source"
                value="gdpdu"
                checked={config.partner_master_source === 'gdpdu'}
                onChange={() => set('partner_master_source', 'gdpdu')}
                className="accent-blue-600 mt-0.5 flex-shrink-0"
              />
              <div>
                <p className="text-sm font-medium text-slate-800">From GDPdU export files</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Customer and supplier master data is extracted from the GDPdU data package
                  (Debitoren.csv / Kreditoren.csv). Upload via the Data Update wizard.
                </p>
              </div>
            </label>

            <label className="flex gap-3 cursor-pointer rounded-lg border border-slate-200 p-3 hover:bg-slate-50 has-[:checked]:border-blue-300 has-[:checked]:bg-blue-50/50">
              <input
                type="radio"
                name="partner_master_source"
                value="files"
                checked={config.partner_master_source === 'files'}
                onChange={() => set('partner_master_source', 'files')}
                className="accent-blue-600 mt-0.5 flex-shrink-0"
              />
              <div>
                <p className="text-sm font-medium text-slate-800">Separate master files</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Upload Debitoren- and Kreditorenstamm as separate files. Useful when the
                  ERP export does not bundle partner data in the GDPdU package.
                </p>
              </div>
            </label>
          </div>
        </fieldset>

        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
          Partner master data populates <code className="text-xs bg-white border border-slate-200 rounded px-1 py-0.5">dim_customer</code>{' '}
          and <code className="text-xs bg-white border border-slate-200 rounded px-1 py-0.5">dim_supplier</code>.
          It enables the booking-ID detour (AR/AP linking) and customer/supplier drill-downs
          in the Financials and Sales views.
        </div>
      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 6 — Sales / Cost labels
// ---------------------------------------------------------------------------

function Step6Labels({
  config,
  onChange,
}: {
  config: ProjectConfig
  onChange: (c: ProjectConfig) => void
}) {
  function set<K extends keyof ProjectConfig>(k: K, v: ProjectConfig[K]) {
    onChange({ ...config, [k]: v })
  }

  return (
    <StepCard
      title="Display labels"
      subtitle="Customise how revenue and cost lines are labelled in reports."
    >
      <div className="space-y-6">
        <InfoBox>
          These labels appear as the top-line headings in the Income Statement and Sales
          dashboards. Use the client's own terminology where possible.
        </InfoBox>

        <Field label="Revenue / Sales label" required>
          {textInput(
            config.sales_label,
            v => set('sales_label', v),
            'e.g. Revenue, Net Sales, Umsatz',
          )}
        </Field>

        <Field label="Cost / COGS label" required>
          {textInput(
            config.cost_label,
            v => set('cost_label', v),
            'e.g. Cost of materials, COGS, Materialaufwand',
          )}
        </Field>
      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 7 — Review & save
// ---------------------------------------------------------------------------

function Step7Review({
  config,
  projectId,
  backendUnavailable,
  saving,
  saved,
  saveError,
  rebuilding,
  rebuildResult,
  rebuildError,
  onSave,
  onRebuild,
}: {
  config: ProjectConfig
  projectId: string
  backendUnavailable: boolean
  saving: boolean
  saved: boolean
  saveError: string | null
  rebuilding: boolean
  rebuildResult: string | null
  rebuildError: string | null
  onSave: () => void
  onRebuild: () => void
}) {
  const rows: Array<{ label: string; value: string }> = [
    { label: 'Project name',        value: config.name || '—' },
    { label: 'FY start month',      value: MONTH_NAMES[(config.fy_start_month - 1) % 12] },
    { label: 'Entities',            value: config.entities.map(e => `${e.name} (${e.code}/${e.prefix})`).join(', ') || '—' },
    { label: 'Opening balances',    value: config.opening_balance_mode === 'in_data' ? 'Already in GL data' : config.opening_balance_mode === 'file' ? 'Separate first-year file' : 'Carry-forward' },
    { label: 'Net profit source',   value: config.net_profit_source === 'report_inject' ? 'Report layer injection (legacy)' : 'Synthetic GL rows (v2)' },
    { label: 'Mapping source',      value: config.mapping_source === 'library' ? 'Finssentials standard library' : 'Client CoA (1:1)' },
    { label: 'Partner master',      value: config.partner_master_source === 'gdpdu' ? 'GDPdU export files' : 'Separate master files' },
    { label: 'Revenue label',       value: config.sales_label || '—' },
    { label: 'Cost label',          value: config.cost_label || '—' },
  ]

  return (
    <StepCard
      title="Review and save"
      subtitle="Review your configuration, save it, and optionally trigger a full rebuild."
    >
      <div className="space-y-5">
        {backendUnavailable && (
          <WarnBox>
            The project configuration endpoint is not yet available (backend Phase 7 pending).
            You can review the settings here but saving will fail until the backend is deployed.
          </WarnBox>
        )}

        <div className="rounded-lg border border-slate-200 overflow-hidden">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-100">
              {rows.map(r => (
                <tr key={r.label} className="even:bg-slate-50/50">
                  <td className="px-4 py-2.5 font-medium text-slate-600 w-44 flex-shrink-0">{r.label}</td>
                  <td className="px-4 py-2.5 text-slate-900">{r.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {saveError && <ErrorBox>{saveError}</ErrorBox>}
        {saved && !saveError && (
          <SuccessBox>Configuration saved successfully for project <strong>{projectId}</strong>.</SuccessBox>
        )}

        <div className="flex flex-wrap gap-3 items-center">
          <button
            type="button"
            onClick={onSave}
            disabled={saving || backendUnavailable}
            className="rounded-md bg-blue-600 px-6 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {saving ? 'Saving...' : saved ? 'Save again' : 'Save configuration'}
          </button>

          {saved && !saveError && (
            <button
              type="button"
              onClick={onRebuild}
              disabled={rebuilding}
              className="rounded-md border border-slate-300 bg-white px-5 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-40"
            >
              {rebuilding ? 'Rebuilding...' : 'Run full rebuild'}
            </button>
          )}
        </div>

        {rebuildError && <ErrorBox>{rebuildError}</ErrorBox>}
        {rebuildResult && !rebuildError && (
          <SuccessBox>Rebuild triggered: {rebuildResult}</SuccessBox>
        )}

        <div className="rounded-lg border border-slate-100 bg-slate-50 px-4 py-3 text-xs text-slate-500 space-y-1">
          <p className="font-medium text-slate-600">What happens after saving</p>
          <ul className="list-disc pl-4 space-y-0.5">
            <li>The configuration is persisted in the backend (project ID: <code>{projectId}</code>).</li>
            <li>Every future GL commit reuses this configuration — no re-mapping needed.</li>
            <li>
              <strong>Monthly updates</strong> use the{' '}
              <Link to="/ingestion" className="text-blue-600 hover:underline">Data Update</Link>{' '}
              flow directly — the setup wizard only needs to be run when configuration changes.
            </li>
            <li>
              <strong>Full rebuild</strong> re-runs the entire pipeline (opening balances,
              CoA classification, derived facts, structure) deterministically from committed GL data.
            </li>
          </ul>
        </div>
      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Monthly update hint banner
// ---------------------------------------------------------------------------

function MonthlyUpdateBanner() {
  return (
    <div
      className="mb-6 flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-5 py-4"
      role="note"
    >
      <div className="flex-shrink-0 w-5 h-5 mt-0.5 text-emerald-600" aria-hidden>
        <svg viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
        </svg>
      </div>
      <div>
        <p className="text-sm font-semibold text-emerald-900">
          Routine monthly updates do not require this wizard
        </p>
        <p className="mt-0.5 text-sm text-emerald-800">
          Once this setup is complete, just upload new bookings via the{' '}
          <Link
            to="/ingestion"
            className="font-medium underline underline-offset-2"
          >
            Data Update
          </Link>{' '}
          flow. Mapping profiles, CoA classification and project config are reused automatically.
          Only return here when the project configuration changes.
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Validation helpers
// ---------------------------------------------------------------------------

function step1Valid(config: ProjectConfig): boolean {
  return (
    config.name.trim().length > 0 &&
    config.entities.length > 0 &&
    config.entities.every(e => e.code.trim() && e.prefix.trim() && e.name.trim())
  )
}

function step6Valid(config: ProjectConfig): boolean {
  return config.sales_label.trim().length > 0 && config.cost_label.trim().length > 0
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ProjectSetupPage() {
  const [step, setStep]         = useState(0)
  const [config, setConfig]     = useState<ProjectConfig>(defaultConfig())
  const [loading, setLoading]   = useState(true)
  const [backendUnavailable, setBackendUnavailable] = useState(false)
  const [saving, setSaving]     = useState(false)
  const [saved, setSaved]       = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildResult, setRebuildResult] = useState<string | null>(null)
  const [rebuildError, setRebuildError] = useState<string | null>(null)

  const configRef = useRef(config)
  configRef.current = config

  // Load existing config on mount
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.getProject(PROJECT_ID)
      .then((data: ProjectConfigResponse) => {
        if (cancelled) return
        // Backend returns the config NESTED under `config`; name + fy_start_month are
        // mirrored at the top level (dim_project columns, authoritative). Merge over the
        // defaults so every field (incl. entities) is always present, and ensure at least
        // one editable entity row.
        const cfg = data.config ?? ({} as Partial<ProjectConfig>)
        const merged: ProjectConfig = {
          ...defaultConfig(),
          ...cfg,
          name: data.name ?? cfg.name ?? '',
          fy_start_month: data.fy_start_month ?? cfg.fy_start_month ?? 1,
          entities:
            Array.isArray(cfg.entities) && cfg.entities.length > 0
              ? cfg.entities
              : [{ code: '', prefix: '', name: '' }],
        }
        setConfig(merged)
      })
      .catch(() => {
        if (cancelled) return
        // Backend not yet live — use defaults, show banner
        setBackendUnavailable(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const handleSave = useCallback(async () => {
    setSaving(true)
    setSaved(false)
    setSaveError(null)
    try {
      await api.putProject(PROJECT_ID, configRef.current)
      setSaved(true)
      setBackendUnavailable(false)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }, [])

  const handleRebuild = useCallback(async () => {
    setRebuilding(true)
    setRebuildResult(null)
    setRebuildError(null)
    try {
      const r = await api.rebuildProject(PROJECT_ID)
      setRebuildResult(r.message || r.status || 'Rebuild started')
    } catch (e) {
      setRebuildError(e instanceof Error ? e.message : 'Rebuild failed')
    } finally {
      setRebuilding(false)
    }
  }, [])

  function isNextDisabled(): boolean {
    if (step === 0) return !step1Valid(config)
    if (step === 5) return !step6Valid(config)
    return false
  }

  const totalSteps = WIZARD_STEPS.length

  if (loading) {
    return (
      <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
        <div className="mx-auto w-full max-w-[1680px] px-6 py-8">
          <div className="flex items-center justify-center py-20 gap-3 text-slate-500">
            <svg className="animate-spin h-5 w-5 text-blue-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
            <span className="text-sm">Loading project configuration…</span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <div className="mx-auto w-full max-w-[1680px] px-6 py-8">
        {/* Page header */}
        <div className="mb-8">
          <p className="text-xs font-semibold uppercase tracking-widest mb-2" style={{ color: '#1E3A5F' }}>
            Admin
          </p>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: '#111827' }}>
            Project Setup
          </h1>
          <p className="text-sm mt-1.5" style={{ color: '#94A3B8' }}>
            Configure the project once — monthly data updates never require re-mapping.
          </p>
        </div>

        {/* Monthly update hint */}
        <MonthlyUpdateBanner />

        {/* Stepper */}
        <Stepper current={step} steps={WIZARD_STEPS} />

        {/* Step content */}
        {step === 0 && (
          <Step1Basics config={config} onChange={setConfig} />
        )}
        {step === 1 && (
          <Step2GlUpload />
        )}
        {step === 2 && (
          <Step3CoaMapping config={config} onChange={setConfig} />
        )}
        {step === 3 && (
          <Step4OpeningBalances config={config} onChange={setConfig} />
        )}
        {step === 4 && (
          <Step5PartnerMaster config={config} onChange={setConfig} />
        )}
        {step === 5 && (
          <Step6Labels config={config} onChange={setConfig} />
        )}
        {step === 6 && (
          <Step7Review
            config={config}
            projectId={PROJECT_ID}
            backendUnavailable={backendUnavailable}
            saving={saving}
            saved={saved}
            saveError={saveError}
            rebuilding={rebuilding}
            rebuildResult={rebuildResult}
            rebuildError={rebuildError}
            onSave={handleSave}
            onRebuild={handleRebuild}
          />
        )}

        {/* Navigation */}
        <NavButtons
          step={step}
          totalSteps={totalSteps}
          onBack={() => setStep(s => Math.max(0, s - 1))}
          onNext={() => setStep(s => Math.min(totalSteps - 1, s + 1))}
          nextDisabled={isNextDisabled()}
        />
      </div>
    </div>
  )
}
