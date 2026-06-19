/**
 * IngestionPage — GL-Ingestion + Konten-Mapping (two modes).
 *
 * Mode selector at the top lets admins switch between:
 *   A) GL-Ingestion   — Upload → Kontext → Spalten-Mapping → Optionen → Validierung → Commit
 *   B) Konten-Mapping — Upload → Kontext → Spalten-Mapping → Commit
 *      Uses POST /api/v1/ingest/mapping/commit (require_admin).
 *      Reuses the same ColumnMapper component with account-mapping target fields.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import ColumnMapper, { missingRequiredFields } from "../components/ingest/ColumnMapper";
import DataUpdateNav from "../components/ingest/DataUpdateNav";
import AccountColumnMapper, { missingRequiredAccountFields } from "../components/ingest/AccountColumnMapper";
import ValidationReport from "../components/ingest/ValidationReport";
import VersionHistoryPanel from "../components/ingest/VersionHistoryPanel";
import { useAuth } from "../context/AuthContext";
import {
  commitAccountMapping,
  commitIngest,
  getIngestFiscalYears,
  listProfiles,
  previewAccountMapping,
  saveProfile,
  uploadFile,
  validateIngest,
  previewEntityAssignments,
  type EntityPreviewResponse,
  type AccountMappingProfile,
  type CommitResponse,
  type EntityMode,
  type FiscalYearMode,
  type LinkingStrategy,
  type MappingCommitResponse,
  type MappingPreviewResponse,
  type MappingProfile,
  type Profile,
  type SignMode,
  type UploadResponse,
  type ValidationResponse,
  type BsPlReplaceMode,
} from "../lib/gdpduApi";
import PageShell from "../components/ui/PageShell";
import SoftSegment from "../components/ui/SoftSegment";

// ---------------------------------------------------------------------------
// Mode selector
// ---------------------------------------------------------------------------

type WizardMode = "gl" | "account-mapping";

function isBsPlMasterSheets(sheets: string[]): boolean {
  return sheets.includes("Master_BS") && sheets.includes("Master_PL");
}

// ---------------------------------------------------------------------------
// Stepper
// ---------------------------------------------------------------------------

const GL_STEPS = [
  "Upload",
  "Context",
  "Column mapping",
  "Options",
  "Validation",
  "Commit",
] as const;

const ACCOUNT_STEPS = [
  "Upload",
  "Context",
  "Column mapping",
  "Commit",
] as const;

const ACCOUNT_STEPS_BSPL = [
  "Upload",
  "Fiscal years",
  "Preview",
  "Commit",
] as const;

type GLStepIndex = 0 | 1 | 2 | 3 | 4 | 5;
type AccountStepIndex = 0 | 1 | 2 | 3;

function Stepper({ current, steps }: { current: number; steps: readonly string[] }) {
  return (
    <nav aria-label="Wizard steps" className="mb-8">
      <ol className="flex items-center gap-0">
        {steps.map((label, idx) => {
          const done = idx < current;
          const active = idx === current;
          return (
            <li key={label} className="flex items-center">
              <div className="flex flex-col items-center">
                <span
                  className={`flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold border-2 transition ${
                    done
                      ? "border-blue-600 bg-blue-600 text-white"
                      : active
                      ? "border-blue-600 bg-white text-blue-600"
                      : "border-slate-300 bg-white text-slate-400"
                  }`}
                >
                  {done ? (
                    <span aria-hidden>&#10003;</span>
                  ) : (
                    <span>{idx + 1}</span>
                  )}
                </span>
                <span
                  className={`mt-1 text-xs font-medium whitespace-nowrap ${
                    active ? "text-blue-700" : done ? "text-slate-600" : "text-slate-400"
                  }`}
                >
                  {label}
                </span>
              </div>
              {idx < steps.length - 1 && (
                <div
                  className={`mx-1 mb-4 h-0.5 w-10 flex-shrink-0 rounded transition ${
                    idx < current ? "bg-blue-600" : "bg-slate-200"
                  }`}
                />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Step card wrapper
// ---------------------------------------------------------------------------

function StepCard({
  children,
  title,
  subtitle,
}: {
  children: React.ReactNode;
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
      <div className="mt-5">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Navigation buttons
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
  step: number;
  totalSteps: number;
  onBack: () => void;
  onNext: () => void;
  nextLabel?: string;
  nextDisabled?: boolean;
  nextLoading?: boolean;
}) {
  const isLast = step === totalSteps - 1;
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
          {nextLoading ? "Loading..." : nextLabel ?? "Next"}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 0 — Upload
// ---------------------------------------------------------------------------

function UploadStep({
  onUploaded,
}: {
  onUploaded: (result: UploadResponse, file: File) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedSheet, setSelectedSheet] = useState<string | undefined>();
  const [pendingResult, setPendingResult] = useState<{
    res: UploadResponse;
    file: File;
  } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function doUpload(file: File, sheet?: string) {
    setLoading(true);
    setError(null);
    try {
      const res = await uploadFile(file, sheet);
      if (res.sheets.length > 1 && !sheet && !isBsPlMasterSheets(res.sheets)) {
        setPendingResult({ res, file });
        setLoading(false);
        return;
      }
      onUploaded(res, file);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setLoading(false);
    }
  }

  function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    doUpload(files[0]);
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    handleFiles(e.dataTransfer.files);
  }

  function handleSheetConfirm() {
    if (!pendingResult) return;
    doUpload(pendingResult.file, selectedSheet || pendingResult.res.sheets[0]);
    setPendingResult(null);
  }

  if (pendingResult) {
    return (
      <StepCard
        title="Select worksheet"
        subtitle="This file has multiple sheets — choose which one to import."
      >
        <div className="space-y-3">
          {pendingResult.res.sheets.map((s) => (
            <label key={s} className="flex items-center gap-3 cursor-pointer">
              <input
                type="radio"
                name="sheet"
                value={s}
                checked={(selectedSheet ?? pendingResult.res.sheets[0]) === s}
                onChange={() => setSelectedSheet(s)}
                className="h-4 w-4 accent-blue-600"
              />
              <span className="text-sm font-medium text-slate-700">{s}</span>
            </label>
          ))}
        </div>
        <div className="mt-5 flex gap-3">
          <button
            type="button"
            onClick={() => setPendingResult(null)}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSheetConfirm}
            disabled={loading}
            className="rounded-md bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700"
          >
            {loading ? "Loading..." : "Confirm"}
          </button>
        </div>
      </StepCard>
    );
  }

  return (
    <StepCard
      title="Upload file"
      subtitle="CSV, XLSX or Parquet — encoding and delimiter are detected automatically."
    >
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-8 py-14 cursor-pointer transition ${
          dragging
            ? "border-blue-500 bg-blue-50"
            : "border-slate-300 bg-slate-50 hover:border-blue-400 hover:bg-slate-100"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx,.xls,.parquet"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        <div className="text-4xl text-slate-300 mb-3">&#128196;</div>
        {loading ? (
          <p className="text-sm font-medium text-blue-600">Processing file…</p>
        ) : (
          <>
            <p className="text-sm font-semibold text-slate-700">
              Drop a file here or click to browse
            </p>
            <p className="mt-1 text-xs text-slate-400">CSV · XLSX · Parquet</p>
          </>
        )}
      </div>
      {error && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}
    </StepCard>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — Kontext (Entity + Fiscal Year) — shared between both modes
// ---------------------------------------------------------------------------

interface KontextState {
  entityMode: EntityMode;
  entityValue: string;
  fiscalYearMode: FiscalYearMode;
  fiscalYearValue: string;
}

function KontextStep({
  sourceColumns,
  state,
  onChange,
  variant = "account-mapping",
}: {
  sourceColumns: string[];
  state: KontextState;
  onChange: (s: KontextState) => void;
  variant?: "gl" | "account-mapping";
}) {
  function set<K extends keyof KontextState>(k: K, v: KontextState[K]) {
    onChange({ ...state, [k]: v });
  }

  const isValid =
    variant === "gl"
      ? state.entityValue.trim() !== ""
      : state.entityValue.trim() !== "" && state.fiscalYearValue.trim() !== "";

  return (
    <StepCard
      title="Assign context"
      subtitle={
        variant === "gl"
          ? "Which entity does this file belong to?"
          : "Which entity and fiscal year does this file belong to?"
      }
    >
      <div className="space-y-6">
        {/* Entity */}
        <fieldset>
          <legend className="text-sm font-semibold text-slate-700 mb-2">
            Entity
          </legend>
          <div className="flex gap-4 mb-3">
            <label className="flex items-center gap-2 cursor-pointer text-sm">
              <input
                type="radio"
                name="entityMode"
                value="fixed"
                checked={state.entityMode === "fixed"}
                onChange={() => set("entityMode", "fixed")}
                className="accent-blue-600"
              />
              Fixed (same entity for all rows)
            </label>
            <label className="flex items-center gap-2 cursor-pointer text-sm">
              <input
                type="radio"
                name="entityMode"
                value="column"
                checked={state.entityMode === "column"}
                onChange={() => set("entityMode", "column")}
                className="accent-blue-600"
              />
              From column
            </label>
          </div>
          {state.entityMode === "fixed" ? (
            <input
              type="text"
              placeholder="e.g. Atlas"
              value={state.entityValue}
              onChange={(e) => set("entityValue", e.target.value)}
              maxLength={4}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm w-48 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          ) : (
            <select
              value={state.entityValue}
              onChange={(e) => set("entityValue", e.target.value)}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">-- Select column --</option>
              {sourceColumns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          )}
        </fieldset>

        {variant === "gl" ? (
          <p className="text-xs text-slate-500 rounded-md border border-slate-100 bg-slate-50 px-3 py-2">
            Fiscal year is derived automatically from the posting date when you map columns in the
            next step. Map <span className="font-medium">Posting Date</span> to the booking
            document date column.
          </p>
        ) : (
          <fieldset>
            <legend className="text-sm font-semibold text-slate-700 mb-2">
              Fiscal year (fiscal_year)
            </legend>
            <div className="flex gap-4 mb-3 flex-wrap">
              <label className="flex items-center gap-2 cursor-pointer text-sm">
                <input
                  type="radio"
                  name="fyMode"
                  value="fixed"
                  checked={state.fiscalYearMode === "fixed"}
                  onChange={() => set("fiscalYearMode", "fixed")}
                  className="accent-blue-600"
                />
                Fixed
              </label>
              <label className="flex items-center gap-2 cursor-pointer text-sm">
                <input
                  type="radio"
                  name="fyMode"
                  value="column"
                  checked={state.fiscalYearMode === "column"}
                  onChange={() => set("fiscalYearMode", "column")}
                  className="accent-blue-600"
                />
                From column
              </label>
            </div>
            {state.fiscalYearMode === "fixed" ? (
              <input
                type="number"
                placeholder="e.g. 2024"
                value={state.fiscalYearValue}
                onChange={(e) => set("fiscalYearValue", e.target.value)}
                min={2000}
                max={2099}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            ) : (
              <select
                value={state.fiscalYearValue}
                onChange={(e) => set("fiscalYearValue", e.target.value)}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">-- Select column --</option>
                {sourceColumns.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            )}
          </fieldset>
        )}

        {!isValid && (
          <p className="text-xs text-amber-600">
            {variant === "gl"
              ? "Please set the entity before continuing."
              : "Please set entity and fiscal year before continuing."}
          </p>
        )}
      </div>
    </StepCard>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — Spalten-Mapping (GL mode)
// ---------------------------------------------------------------------------

function MappingStep({
  uploadResult,
  mapping,
  onChange,
}: {
  uploadResult: UploadResponse;
  mapping: Record<string, string>;
  onChange: (m: Record<string, string>) => void;
}) {
  const missing = missingRequiredFields(mapping);

  return (
    <StepCard
      title="Column mapping"
      subtitle="Drag source columns onto target fields. Required fields (*) must be mapped before you can continue."
    >
      {missing.length > 0 && (
        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
          <span className="font-medium">Missing required fields:</span>{" "}
          {missing.join(", ")}
        </div>
      )}
      <ColumnMapper
        sourceColumns={uploadResult.columns}
        sample={uploadResult.sample}
        mapping={mapping}
        onChange={onChange}
      />
    </StepCard>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — Spalten-Mapping (Account-Mapping mode) — reuses AccountColumnMapper
// ---------------------------------------------------------------------------

function AccountMappingStep({
  uploadResult,
  mapping,
  onChange,
}: {
  uploadResult: UploadResponse;
  mapping: Record<string, string>;
  onChange: (m: Record<string, string>) => void;
}) {
  const missing = missingRequiredAccountFields(mapping);

  return (
    <StepCard
      title="Account mapping: map columns"
      subtitle="Map source columns to canonical account dimension fields. Required fields (*) must be mapped."
    >
      {missing.length > 0 && (
        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
          <span className="font-medium">Missing required fields:</span>{" "}
          {missing.join(", ")}
        </div>
      )}
      <AccountColumnMapper
        sourceColumns={uploadResult.columns}
        sample={uploadResult.sample}
        mapping={mapping}
        onChange={onChange}
      />
    </StepCard>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — Transform-Optionen (GL only)
// ---------------------------------------------------------------------------

interface OptionsState {
  signMode: SignMode;
  signAmount: string;
  signSoll: string;
  signHaben: string;
  signDcFlag: string;
  signDebitValue: string;
  decimal: string;
  thousands: string;
  dateDayfirst: boolean;
  linking: LinkingStrategy;
  profileName: string;
  profileSystem: string;
}

function OptionsStep({
  sourceColumns,
  dialect,
  state,
  onChange,
  onSaveProfile,
  savingProfile,
  profileSaved,
}: {
  sourceColumns: string[];
  dialect: { decimal: string; thousands: string };
  state: OptionsState;
  onChange: (s: OptionsState) => void;
  onSaveProfile: () => void;
  savingProfile: boolean;
  profileSaved: boolean;
}) {
  function set<K extends keyof OptionsState>(k: K, v: OptionsState[K]) {
    onChange({ ...state, [k]: v });
  }

  return (
    <StepCard
      title="Transform options"
      subtitle="Sign logic, date format, linking strategy, and optional profile save."
    >
      <div className="space-y-7">
        <div className="rounded-lg border border-blue-200 bg-blue-50/80 px-4 py-3 text-sm text-blue-900">
          <strong>Scope replace (default):</strong> commit replaces GL data for the
          entities and fiscal years in this file. The previous state remains stored as a
          version (load ID) and can be restored from Version history.
        </div>

        {/* Sign mode */}
        <fieldset>
          <legend className="text-sm font-semibold text-slate-700 mb-2">
            Amount sign logic
          </legend>
          <div className="flex flex-wrap gap-4 mb-4">
            {(["signed", "soll_haben", "amount_dc"] as SignMode[]).map((m) => (
              <label key={m} className="flex items-center gap-2 cursor-pointer text-sm">
                <input
                  type="radio"
                  name="signMode"
                  value={m}
                  checked={state.signMode === m}
                  onChange={() => set("signMode", m)}
                  className="accent-blue-600"
                />
                {m === "signed"
                  ? "Signed amount (single column)"
                  : m === "soll_haben"
                  ? "Debit / credit (DATEV)"
                  : "Amount + debit/credit flag"}
              </label>
            ))}
          </div>

          {state.signMode === "signed" && (
            <div className="flex items-center gap-3">
              <label className="text-sm text-slate-600 w-24">Amount column</label>
              <ColumnSelect
                columns={sourceColumns}
                value={state.signAmount}
                onChange={(v) => set("signAmount", v)}
              />
            </div>
          )}

          {state.signMode === "soll_haben" && (
            <div className="space-y-2">
              <div className="flex items-center gap-3">
                <label className="text-sm text-slate-600 w-24">Debit column</label>
                <ColumnSelect
                  columns={sourceColumns}
                  value={state.signSoll}
                  onChange={(v) => set("signSoll", v)}
                />
              </div>
              <div className="flex items-center gap-3">
                <label className="text-sm text-slate-600 w-24">Credit column</label>
                <ColumnSelect
                  columns={sourceColumns}
                  value={state.signHaben}
                  onChange={(v) => set("signHaben", v)}
                />
              </div>
            </div>
          )}

          {state.signMode === "amount_dc" && (
            <div className="space-y-2">
              <div className="flex items-center gap-3">
                <label className="text-sm text-slate-600 w-28">Amount column</label>
                <ColumnSelect
                  columns={sourceColumns}
                  value={state.signAmount}
                  onChange={(v) => set("signAmount", v)}
                />
              </div>
              <div className="flex items-center gap-3">
                <label className="text-sm text-slate-600 w-28">Debit/credit flag</label>
                <ColumnSelect
                  columns={sourceColumns}
                  value={state.signDcFlag}
                  onChange={(v) => set("signDcFlag", v)}
                />
              </div>
              <div className="flex items-center gap-3">
                <label className="text-sm text-slate-600 w-28">Value = debit</label>
                <input
                  type="text"
                  placeholder='e.g. "S" or "D"'
                  value={state.signDebitValue}
                  onChange={(e) => set("signDebitValue", e.target.value)}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>
          )}
        </fieldset>

        {/* Locale */}
        <fieldset>
          <legend className="text-sm font-semibold text-slate-700 mb-2">Decimal & thousands separator</legend>
          <div className="flex gap-6">
            <div className="flex items-center gap-2">
              <label className="text-sm text-slate-600">Decimal separator</label>
              <select
                value={state.decimal}
                onChange={(e) => set("decimal", e.target.value)}
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value=",">, (comma, DE)</option>
                <option value=".">. (period, EN)</option>
              </select>
              {dialect.decimal && (
                <span className="text-xs text-slate-400">
                  detected: &ldquo;{dialect.decimal}&rdquo;
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <label className="text-sm text-slate-600">Thousands separator</label>
              <select
                value={state.thousands}
                onChange={(e) => set("thousands", e.target.value)}
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value=".">. (period, DE)</option>
                <option value=",">, (comma, EN)</option>
                <option value=" ">Space</option>
                <option value="">None</option>
              </select>
            </div>
          </div>
        </fieldset>

        {/* Date */}
        <fieldset>
          <legend className="text-sm font-semibold text-slate-700 mb-2">Date format</legend>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="checkbox"
              checked={state.dateDayfirst}
              onChange={(e) => set("dateDayfirst", e.target.checked)}
              className="accent-blue-600"
            />
            Day first (DD.MM.YYYY / DD/MM/YYYY — DATEV default)
          </label>
        </fieldset>

        {/* Linking strategy */}
        <fieldset>
          <legend className="text-sm font-semibold text-slate-700 mb-2">
            AR/AP linking strategy
          </legend>
          {(
            [
              ["txn", "A — Transaction propagation (GoBD)"],
              ["gegenkonto", "B — Account / counter-account (DATEV)"],
              ["none", "No partner assignment"],
            ] as [LinkingStrategy, string][]
          ).map(([val, label]) => (
            <label key={val} className="flex items-center gap-2 cursor-pointer text-sm mb-2">
              <input
                type="radio"
                name="linking"
                value={val}
                checked={state.linking === val}
                onChange={() => set("linking", val)}
                className="accent-blue-600"
              />
              {label}
            </label>
          ))}
        </fieldset>

        {/* Profile save */}
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
          <p className="text-sm font-semibold text-slate-700 mb-3">
            Save mapping profile (optional)
          </p>
          <div className="flex flex-wrap gap-3 items-end">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Profile name</label>
              <input
                type="text"
                placeholder="e.g. DATEV Export 2024"
                value={state.profileName}
                onChange={(e) => set("profileName", e.target.value)}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm w-52 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Source system</label>
              <input
                type="text"
                placeholder="e.g. DATEV / SAP / Lexware"
                value={state.profileSystem}
                onChange={(e) => set("profileSystem", e.target.value)}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm w-48 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <button
              type="button"
              onClick={onSaveProfile}
              disabled={!state.profileName.trim() || savingProfile || profileSaved}
              className="rounded-md border border-blue-300 bg-blue-50 px-4 py-1.5 text-sm font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-40"
            >
              {savingProfile ? "Saving..." : profileSaved ? "Saved ✓" : "Save profile"}
            </button>
          </div>
        </div>
      </div>
    </StepCard>
  );
}

function ColumnSelect({
  columns,
  value,
  onChange,
}: {
  columns: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-slate-300 px-2 py-1.5 text-sm w-52 focus:outline-none focus:ring-2 focus:ring-blue-500"
    >
      <option value="">-- Column --</option>
      {columns.map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </select>
  );
}

// ---------------------------------------------------------------------------
// Step 4 — Validierung (GL only)
// ---------------------------------------------------------------------------

function ValidierungStep({
  uploadResult,
  profile,
  onResult,
  onEntityAssignmentsChange,
  onImportSuccess,
}: {
  uploadResult: UploadResponse;
  profile: Profile;
  onResult: (r: ValidationResponse) => void;
  onEntityAssignmentsChange: (assignments: Record<string, string>) => void;
  onImportSuccess: (result: CommitResponse) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ValidationResponse | null>(null);
  const [committing, setCommitting] = useState(false);
  const [entityPreview, setEntityPreview] = useState<EntityPreviewResponse | null>(null);
  const [entityLoading, setEntityLoading] = useState(true);
  const [entityReady, setEntityReady] = useState(false);
  const [entityPreviewTick, setEntityPreviewTick] = useState(0);
  const [excludedLineIds, setExcludedLineIds] = useState<number[]>([]);
  const [excluding, setExcluding] = useState(false);

  async function runValidationWithExclusions(lineIds: number[] = excludedLineIds) {
    setLoading(true);
    setError(null);
    try {
      const r = await validateIngest({
        file_id: uploadResult.file_id,
        sheet: uploadResult.sheets[0],
        profile,
        exclude_line_ids: lineIds,
      });
      setResult(r);
      onResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Validation failed");
    } finally {
      setLoading(false);
    }
  }

  async function applyExclusions(addIds: number[]) {
    const next = [...new Set([...excludedLineIds, ...addIds])];
    setExcludedLineIds(next);
    setExcluding(true);
    try {
      await runValidationWithExclusions(next);
    } finally {
      setExcluding(false);
    }
  }

  async function clearExclusions() {
    setExcludedLineIds([]);
    setExcluding(true);
    try {
      await runValidationWithExclusions([]);
    } finally {
      setExcluding(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    setEntityLoading(true);
    setEntityReady(false);
    setEntityPreview(null);
    setError(null);
    previewEntityAssignments({
      file_id: uploadResult.file_id,
      sheet: uploadResult.sheets[0] || undefined,
      entity: profile.entity,
      entity_assignments: profile.entity_assignments,
    })
      .then((preview) => {
        if (cancelled) return;
        setEntityPreview(preview);
        if (!preview.needs_confirmation) {
          const auto = Object.fromEntries(
            preview.mappings.map((m) => [m.source_label, m.entity_prefix])
          );
          onEntityAssignmentsChange(auto);
          setEntityReady(true);
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Entity preview failed");
      })
      .finally(() => {
        if (!cancelled) setEntityLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    uploadResult.file_id,
    uploadResult.sheets[0],
    profile.entity.mode,
    profile.entity.value,
    onEntityAssignmentsChange,
    entityPreviewTick,
  ]);

  function confirmEntityAssignments() {
    if (!entityPreview) return;
    const assignments = Object.fromEntries(
      entityPreview.mappings.map((m) => [m.source_label, m.entity_prefix])
    );
    onEntityAssignmentsChange(assignments);
    setEntityReady(true);
  }

  async function runValidation() {
    await runValidationWithExclusions(excludedLineIds);
  }

  async function handleCommit(confirmSoft: boolean) {
    setCommitting(true);
    setError(null);
    try {
      const r = await commitIngest({
        file_id: uploadResult.file_id,
        sheet: uploadResult.sheets[0],
        profile,
        dataset: "gl",
        confirm_soft: confirmSoft,
        exclude_line_ids: excludedLineIds,
        commit_mode: "replace",
      });
      onImportSuccess(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Commit failed");
    } finally {
      setCommitting(false);
    }
  }

  return (
    <StepCard
      title="Validation"
      subtitle="Checks run on staging — no database write until commit."
    >
      {!result && !loading && (
        <div className="space-y-6">
          {entityLoading && (
            <p className="text-sm text-slate-500 text-center py-6">Resolving entities…</p>
          )}
          {!entityLoading && entityPreview && (
            <div className="rounded-md border border-slate-200 bg-slate-50/80 p-4 space-y-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-800">Entity prefix mapping</h3>
                <p className="text-xs text-slate-500 mt-1">
                  Entity names from your file are matched to existing prefixes in the database.
                  New entities need your confirmation before validation runs.
                </p>
              </div>
              <table className="w-full text-xs text-left">
                <thead className="text-slate-500 border-b border-slate-200">
                  <tr>
                    <th className="py-2 pr-3 font-semibold">Entity in file</th>
                    <th className="py-2 pr-3 font-semibold">Prefix</th>
                    <th className="py-2 pr-3 font-semibold">Status</th>
                    <th className="py-2 font-semibold">Rows</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {entityPreview.mappings.map((row) => (
                    <tr key={row.source_label}>
                      <td className="py-2 pr-3 font-medium text-slate-800">{row.source_label}</td>
                      <td className="py-2 pr-3 font-mono">{row.entity_prefix}</td>
                      <td className="py-2 pr-3 capitalize text-slate-600">{row.status}</td>
                      <td className="py-2 text-slate-500">
                        {row.row_count > 0 ? row.row_count.toLocaleString("en-US") : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {entityPreview.needs_confirmation && !entityReady && (
                <button
                  type="button"
                  onClick={confirmEntityAssignments}
                  className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
                >
                  Confirm prefix assignment
                </button>
              )}
              {entityReady && entityPreview.needs_confirmation && (
                <p className="text-xs text-emerald-700">Prefix assignment confirmed.</p>
              )}
              {!entityPreview.needs_confirmation && (
                <p className="text-xs text-slate-500">
                  All entities matched automatically from existing data.
                </p>
              )}
            </div>
          )}
          {!entityLoading && entityPreview && entityReady && !result && (
            <div className="rounded-md border border-slate-200 bg-white p-4 text-xs text-slate-600 space-y-1">
              <p className="font-semibold text-slate-800 text-sm">What “Run checks” validates</p>
              <p>Staging only — nothing is written to the database until commit.</p>
              <ul className="list-disc pl-4 space-y-0.5">
                <li><strong>B1</strong> — each booking should balance (warning only for GoBD — can be ignored on import)</li>
                <li><strong>B2</strong> — whole ledger per entity must balance</li>
                <li><strong>B3</strong> — monthly movements per entity must balance</li>
                <li><strong>S1</strong> — required fields filled; account key built from entity + account number</li>
                <li>Posting dates, unique row numbers, reconciliation, chart mapping</li>
              </ul>
            </div>
          )}
          <div className="flex flex-col items-center py-6 gap-4">
            <p className="text-sm text-slate-600">
              {entityLoading
                ? "Resolving entity prefixes…"
                : error && !entityPreview
                  ? "Entity prefix preview could not be loaded."
                  : entityReady
                    ? "Click “Run checks” to execute the validation catalog."
                    : "Confirm entity prefix assignment to continue."}
            </p>
            {error && !entityPreview && !entityLoading && (
              <button
                type="button"
                onClick={() => setEntityPreviewTick((n) => n + 1)}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Retry entity preview
              </button>
            )}
            <button
              type="button"
              onClick={runValidation}
              disabled={!entityReady || entityLoading || (!!error && !entityPreview)}
              className="rounded-md bg-blue-600 px-6 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40"
            >
              Run checks
            </button>
          </div>
        </div>
      )}
      {loading && (
        <div className="flex items-center justify-center py-16 gap-3">
          <svg
            className="animate-spin h-5 w-5 text-blue-600"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8v8H4z"
            />
          </svg>
          <span className="text-sm text-slate-600">Validation running… large files may take several minutes.</span>
        </div>
      )}
      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}
      {result && (
        <>
          <div className="mb-4 flex items-center justify-between">
            <p className="text-xs text-slate-500">
              Result at {new Date().toLocaleTimeString("en-US")}
            </p>
            <button
              type="button"
              onClick={runValidation}
              disabled={loading}
              className="text-xs text-blue-600 hover:underline"
            >
              Run again
            </button>
          </div>
          <ValidationReport
            report={result}
            onCommit={handleCommit}
            committing={committing}
            excluding={excluding || loading}
            onExcludeLines={applyExclusions}
            onClearExclusions={clearExclusions}
            validateContext={{
              file_id: uploadResult.file_id,
              sheet: uploadResult.sheets[0],
              profile,
              exclude_line_ids: excludedLineIds,
            }}
          />
        </>
      )}
    </StepCard>
  );
}

// ---------------------------------------------------------------------------
// BS/PL Master — Fiscal years (step 1)
// ---------------------------------------------------------------------------

function BsPlFiscalYearStep({
  availableYears,
  selected,
  onChange,
  loading,
  replaceMode,
  onReplaceModeChange,
}: {
  availableYears: number[];
  selected: number[];
  onChange: (years: number[]) => void;
  loading: boolean;
  replaceMode: BsPlReplaceMode;
  onReplaceModeChange: (mode: BsPlReplaceMode) => void;
}) {
  const [manualYear, setManualYear] = useState("");

  function toggle(y: number) {
    if (selected.includes(y)) {
      onChange(selected.filter((x) => x !== y));
    } else {
      onChange([...selected, y].sort((a, b) => a - b));
    }
  }

  function addManualYear() {
    const v = parseInt(manualYear, 10);
    if (Number.isNaN(v) || v < 2000 || v > 2099) return;
    if (!selected.includes(v)) onChange([...selected, v].sort((a, b) => a - b));
    setManualYear("");
  }

  return (
    <StepCard
      title="Select fiscal years"
      subtitle="BS/PL Master detected (Master_BS + Master_PL). Choose the years to write account mapping for."
    >
      {loading ? (
        <p className="text-sm text-slate-500">Loading available years from the database…</p>
      ) : (
        <>
          {availableYears.length > 0 && (
            <div className="flex flex-wrap gap-3 mb-4">
              {availableYears.map((y) => (
                <label
                  key={y}
                  className="flex items-center gap-2 cursor-pointer rounded-md border border-slate-200 px-3 py-2 text-sm hover:bg-slate-50"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(y)}
                    onChange={() => toggle(y)}
                    className="accent-blue-600"
                  />
                  <span className="font-medium">{y}</span>
                </label>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={2000}
              max={2099}
              placeholder="Add year (e.g. 2024)"
              value={manualYear}
              onChange={(e) => setManualYear(e.target.value)}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm w-48"
            />
            <button
              type="button"
              onClick={addManualYear}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50"
            >
              Add
            </button>
          </div>
          {selected.length > 0 && (
            <p className="mt-3 text-sm text-slate-600">
              Selected: <span className="font-medium">{selected.join(", ")}</span>
            </p>
          )}
        </>
      )}
      <div className="mt-5 pt-4 border-t border-slate-100">
        <p className="text-xs font-medium text-slate-500 mb-2">Import mode</p>
        <SoftSegment
          value={replaceMode}
          onChange={(v) => onReplaceModeChange(v as BsPlReplaceMode)}
          options={[
            { value: "replace", label: "Replace for selected years" },
            { value: "append", label: "Append new accounts only" },
          ]}
        />
        <p className="mt-2 text-xs text-slate-500">
          {replaceMode === "replace"
            ? "Mappings in the file replace all accounts for the selected fiscal years (per entity in file). Accounts not in the file are removed."
            : "Only accounts that do not yet exist are added. Existing mappings stay unchanged."}
        </p>
      </div>
      <p className="mt-4 text-xs text-slate-400">
        Nothing is written until you commit on the final step.
      </p>
    </StepCard>
  );
}

function bsPlPreviewCacheKey(
  fileId: string,
  fiscalYears: number[],
  replaceMode: BsPlReplaceMode
): string {
  return `${fileId}:${[...fiscalYears].sort((a, b) => a - b).join(",")}:${replaceMode}`;
}

// ---------------------------------------------------------------------------
// BS/PL Master — Preview (step 2, read-only)
// ---------------------------------------------------------------------------

function BsPlPreviewStep({
  uploadResult,
  fiscalYears,
  replaceMode,
  preview,
  loading,
  error,
  onLoadPreview,
}: {
  uploadResult: UploadResponse;
  fiscalYears: number[];
  replaceMode: BsPlReplaceMode;
  preview: MappingPreviewResponse | null;
  loading: boolean;
  error: string | null;
  onLoadPreview: () => void;
}) {
  const [showDuplicates, setShowDuplicates] = useState(true);
  const [showInserts, setShowInserts] = useState(true);
  const [showDeletes, setShowDeletes] = useState(true);
  const duplicates = preview?.duplicate_details?.length
    ? preview.duplicate_details
    : preview?.duplicate_keys ?? [];
  const inserts = preview?.insert_details ?? [];
  const deletes = preview?.delete_details ?? [];

  useEffect(() => {
    onLoadPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadResult.file_id, fiscalYears.join(","), replaceMode]);

  return (
    <StepCard
      title="Preview (read-only)"
      subtitle="Review entities and row counts before committing. Nothing is written to the database yet."
    >
      {loading && !preview && (
        <p className="text-sm text-slate-500">Computing preview…</p>
      )}
      {loading && preview && (
        <p className="text-sm text-slate-400 mb-3">Refreshing preview…</p>
      )}
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 mb-4">
          {error}
        </div>
      )}
      {preview && (
        <div className={`space-y-4 text-sm text-slate-700 ${loading ? "opacity-60" : ""}`}>
          {preview.blockers.length > 0 && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-800">
              <span className="font-medium">Blocker:</span> {preview.blockers.join("; ")}
            </div>
          )}
          {preview.warnings.length > 0 && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
              <span className="font-medium">Warning:</span> {preview.warnings.join(" ")}
            </div>
          )}
          {duplicates.length > 0 && (
            <div className="rounded-md border border-red-200 bg-red-50/50 overflow-hidden">
              <button
                type="button"
                onClick={() => setShowDuplicates((v) => !v)}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium text-red-900 hover:bg-red-50"
              >
                <span>
                  Duplicate rows: {duplicates.length}
                </span>
                <span className="text-xs text-red-700">{showDuplicates ? "▼" : "▶"}</span>
              </button>
              {showDuplicates && (
                <div className="max-h-64 overflow-auto border-t border-red-200">
                  <table className="w-full text-xs text-left">
                    <thead className="bg-red-100/80 text-red-900 sticky top-0">
                      <tr>
                        <th className="px-2 py-1.5 font-semibold">Account group</th>
                        <th className="px-2 py-1.5 font-semibold">FY</th>
                        <th className="px-2 py-1.5 font-semibold">Account</th>
                        <th className="px-2 py-1.5 font-semibold">Name</th>
                        <th className="px-2 py-1.5 font-semibold">L1</th>
                        <th className="px-2 py-1.5 font-semibold">L2</th>
                        <th className="px-2 py-1.5 font-semibold">L3</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-red-100 bg-white">
                      {duplicates.map((d, idx) => (
                        <tr
                          key={`${d.account_number_group}-${d.fiscal_year}-${idx}`}
                          className="text-slate-700"
                        >
                          <td className="px-2 py-1 font-mono">{d.account_number_group}</td>
                          <td className="px-2 py-1">{d.fiscal_year}</td>
                          <td className="px-2 py-1 font-mono">{d.gl_account_id ?? "—"}</td>
                          <td className="px-2 py-1 max-w-[200px] truncate" title={d.account_name}>
                            {d.account_name || "—"}
                          </td>
                          <td className="px-2 py-1">
                            {d.level_0
                              ?? (d.level_0_values?.length ? d.level_0_values.join(", ") : "—")}
                          </td>
                          <td className="px-2 py-1 max-w-[140px] truncate" title={d.level_1}>
                            {d.level_1 || "—"}
                          </td>
                          <td className="px-2 py-1 max-w-[140px] truncate" title={d.level_2}>
                            {d.level_2 || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
          {inserts.length > 0 && (
            <div className="rounded-md border border-emerald-200 bg-emerald-50/50 overflow-hidden">
              <button
                type="button"
                onClick={() => setShowInserts((v) => !v)}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium text-emerald-900 hover:bg-emerald-50"
              >
                <span>
                  New accounts (insert): {inserts.length}
                </span>
                <span className="text-xs text-emerald-700">{showInserts ? "▼" : "▶"}</span>
              </button>
              {showInserts && (
                <div className="max-h-64 overflow-auto border-t border-emerald-200">
                  <table className="w-full text-xs text-left">
                    <thead className="bg-emerald-100/80 text-emerald-900 sticky top-0">
                      <tr>
                        <th className="px-2 py-1.5 font-semibold">Account group</th>
                        <th className="px-2 py-1.5 font-semibold">FY</th>
                        <th className="px-2 py-1.5 font-semibold">Account</th>
                        <th className="px-2 py-1.5 font-semibold">Name</th>
                        <th className="px-2 py-1.5 font-semibold">L1</th>
                        <th className="px-2 py-1.5 font-semibold">L2</th>
                        <th className="px-2 py-1.5 font-semibold">L3</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-emerald-100 bg-white">
                      {inserts.map((row) => (
                        <tr key={`${row.account_number_group}-${row.fiscal_year}`} className="text-slate-700">
                          <td className="px-2 py-1 font-mono">{row.account_number_group}</td>
                          <td className="px-2 py-1">{row.fiscal_year}</td>
                          <td className="px-2 py-1 font-mono">{row.gl_account_id}</td>
                          <td className="px-2 py-1 max-w-[200px] truncate" title={row.account_name}>
                            {row.account_name || "—"}
                          </td>
                          <td className="px-2 py-1">{row.level_0 || "—"}</td>
                          <td className="px-2 py-1 max-w-[140px] truncate" title={row.level_1}>
                            {row.level_1 || "—"}
                          </td>
                          <td className="px-2 py-1 max-w-[140px] truncate" title={row.level_2}>
                            {row.level_2 || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
          {replaceMode === "replace" && deletes.length > 0 && (
            <div className="rounded-md border border-red-200 bg-red-50/30 overflow-hidden">
              <button
                type="button"
                onClick={() => setShowDeletes((v) => !v)}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium text-red-900 hover:bg-red-50"
              >
                <span>Accounts to remove: {deletes.length}</span>
                <span className="text-xs text-red-700">{showDeletes ? "▼" : "▶"}</span>
              </button>
              {showDeletes && (
                <div className="max-h-64 overflow-auto border-t border-red-200">
                  <table className="w-full text-xs text-left">
                    <thead className="bg-red-50 text-red-900 sticky top-0">
                      <tr>
                        <th className="px-2 py-1.5 font-semibold">Account group</th>
                        <th className="px-2 py-1.5 font-semibold">FY</th>
                        <th className="px-2 py-1.5 font-semibold">Account</th>
                        <th className="px-2 py-1.5 font-semibold">Name</th>
                        <th className="px-2 py-1.5 font-semibold">Level 1</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-red-100 bg-white">
                      {deletes.map((row) => (
                        <tr key={`${row.account_number_group}-${row.fiscal_year}`}>
                          <td className="px-2 py-1 font-mono">{row.account_number_group}</td>
                          <td className="px-2 py-1">{row.fiscal_year}</td>
                          <td className="px-2 py-1 font-mono">{row.gl_account_id}</td>
                          <td className="px-2 py-1 max-w-[200px] truncate">{row.account_name || "—"}</td>
                          <td className="px-2 py-1">{row.level_0 || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div>
              <div className="text-xs text-slate-500">Total rows</div>
              <div className="text-lg font-semibold">{preview.row_count_total.toLocaleString("en-US")}</div>
            </div>
            <div>
              <div className="text-xs text-slate-500">BS / PL</div>
              <div className="text-lg font-semibold">
                {preview.row_count_bs} / {preview.row_count_pl}
              </div>
            </div>
            <div>
              <div className="text-xs text-slate-500">New (insert)</div>
              <div className="text-lg font-semibold text-emerald-700">{preview.would_insert}</div>
            </div>
            {replaceMode === "replace" ? (
              <>
                <div>
                  <div className="text-xs text-slate-500">Update</div>
                  <div className="text-lg font-semibold text-slate-700">{preview.would_update}</div>
                </div>
                <div>
                  <div className="text-xs text-slate-500">Remove</div>
                  <div className="text-lg font-semibold text-red-700">
                    {preview.would_delete ?? 0}
                  </div>
                </div>
              </>
            ) : (
              <div>
                <div className="text-xs text-slate-500">Unchanged (skip)</div>
                <div className="text-lg font-semibold text-slate-500">
                  {preview.would_skip ?? preview.would_update}
                </div>
              </div>
            )}
          </div>
          <div>
            <div className="text-xs font-medium text-slate-500 mb-1">Entities (from dim_legal_entity)</div>
            <ul className="list-disc pl-5 space-y-0.5">
              {preview.entities.map((e) => (
                <li key={e.entity_prefix}>
                  {e.entity_name} → prefix <code>{e.entity_prefix}</code>
                </li>
              ))}
            </ul>
          </div>
          {preview.sample_rows.length > 0 && (
            <div>
              <div className="text-xs font-medium text-slate-500 mb-1">Sample rows</div>
              <pre className="text-xs bg-slate-50 border border-slate-200 rounded p-2 overflow-x-auto max-h-40">
                {JSON.stringify(preview.sample_rows.slice(0, 3), null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </StepCard>
  );
}

// ---------------------------------------------------------------------------
// BS/PL Master — Commit (step 3)
// ---------------------------------------------------------------------------

function BsPlCommitStep({
  uploadResult,
  fiscalYears,
  replaceMode,
  preview,
}: {
  uploadResult: UploadResponse;
  fiscalYears: number[];
  replaceMode: BsPlReplaceMode;
  preview: MappingPreviewResponse | null;
}) {
  const [committing, setCommitting] = useState(false);
  const [committed, setCommitted] = useState<MappingCommitResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const blocked = Boolean(preview?.blockers?.length);

  async function handleCommit() {
    setCommitting(true);
    setError(null);
    try {
      const r = await commitAccountMapping({
        file_id: uploadResult.file_id,
        format: "bs_pl_master",
        fiscal_years: fiscalYears,
        replace_mode: replaceMode,
      });
      setCommitted(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Mapping commit failed");
    } finally {
      setCommitting(false);
    }
  }

  if (committed) {
    const stats: [string, number][] = [
      ["Accounts (dim_gl_account)", committed.accounts],
      ["NA-Mapping (dim_gl_na)", committed.na],
      ["CF-Mapping (dim_gl_cf)", committed.cf],
    ];
    if (committed.deleted) stats.push(["Removed", committed.deleted]);
    if (committed.skipped) stats.push(["Skipped (unchanged)", committed.skipped]);

    return (
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-8 text-center shadow-sm">
        <div className="text-5xl mb-4">&#10003;</div>
        <h2 className="text-xl font-semibold text-emerald-800 mb-1">
          Account mapping loaded successfully
        </h2>
        <p className="text-sm text-emerald-700 mb-6">
          Loaded at {new Date(committed.loaded_at).toLocaleTimeString("en-US")}
        </p>
        <div className="inline-grid grid-cols-2 sm:grid-cols-4 gap-x-8 gap-y-3 text-left mb-6">
          {stats.map(([label, val]) => (
            <div key={label}>
              <div className="text-xs text-emerald-600 font-medium">{label}</div>
              <div className="text-lg font-semibold text-emerald-900">
                {Number(val).toLocaleString("en-US")}
              </div>
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded-md border border-emerald-400 px-5 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-100"
        >
          Import another file
        </button>
      </div>
    );
  }

  return (
    <StepCard
      title="Commit account mapping"
      subtitle="Data is written to dim_gl_account only when you click the button below."
    >
      <div className="space-y-4">
        <div className="rounded-md border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700 space-y-1">
          <p>
            <span className="font-medium">File:</span> {uploadResult.filename}
          </p>
          <p>
            <span className="font-medium">Fiscal years:</span> {fiscalYears.join(", ")}
          </p>
          <p>
            <span className="font-medium">Mode:</span>{" "}
            {replaceMode === "replace"
              ? "Replace for selected years"
              : "Append new accounts only"}
          </p>
          {preview && (
            <p>
              <span className="font-medium">Accounts:</span>{" "}
              {preview.row_count_total.toLocaleString("en-US")} rows
              {replaceMode === "replace" ? (
                <>
                  {" "}
                  ({preview.would_insert} new, {preview.would_update} update
                  {(preview.would_delete ?? 0) > 0
                    ? `, ${preview.would_delete} remove`
                    : ""}
                  )
                </>
              ) : (
                <>
                  {" "}
                  ({preview.would_insert} new, {preview.would_skip ?? preview.would_update}{" "}
                  unchanged)
                </>
              )}
            </p>
          )}
        </div>
        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}
        <button
          type="button"
          onClick={handleCommit}
          disabled={committing || blocked || !preview}
          className="rounded-md bg-blue-600 px-6 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40"
        >
          {committing ? "Loading..." : "Commit now"}
        </button>
      </div>
    </StepCard>
  );
}

// ---------------------------------------------------------------------------
// Account-Mapping Commit step (mode B, step 3) — generic single-sheet
// ---------------------------------------------------------------------------

function AccountMappingCommitStep({
  uploadResult,
  kontext,
  mapping,
}: {
  uploadResult: UploadResponse;
  kontext: KontextState;
  mapping: Record<string, string>;
}) {
  const [committing, setCommitting] = useState(false);
  const [committed, setCommitted] = useState<MappingCommitResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleCommit() {
    setCommitting(true);
    setError(null);
    try {
      const profile: AccountMappingProfile = {
        entity: { mode: kontext.entityMode, value: kontext.entityValue },
        fiscal_year: { mode: kontext.fiscalYearMode as "fixed" | "column", value: kontext.fiscalYearValue },
        columns: mapping,
      };
      const r = await commitAccountMapping({
        file_id: uploadResult.file_id,
        sheet: uploadResult.sheets[0],
        profile,
      });
      setCommitted(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Mapping commit failed");
    } finally {
      setCommitting(false);
    }
  }

  if (committed) {
    return (
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-8 text-center shadow-sm">
        <div className="text-5xl mb-4">&#10003;</div>
        <h2 className="text-xl font-semibold text-emerald-800 mb-1">
          Account mapping loaded successfully
        </h2>
        <p className="text-sm text-emerald-700 mb-6">
          Loaded at {new Date(committed.loaded_at).toLocaleTimeString("en-US")}
        </p>
        <div className="inline-grid grid-cols-3 gap-x-8 gap-y-3 text-left mb-6">
          {([
            ["Accounts (dim_gl_account)", committed.accounts],
            ["NA-Mapping (dim_gl_na)", committed.na],
            ["CF-Mapping (dim_gl_cf)", committed.cf],
          ] as [string, number][]).map(([label, val]) => (
            <div key={label}>
              <div className="text-xs text-emerald-600 font-medium">{label}</div>
              <div className="text-lg font-semibold text-emerald-900">
                {Number(val).toLocaleString("en-US")}
              </div>
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded-md border border-emerald-400 px-5 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-100"
        >
          Import another file
        </button>
      </div>
    );
  }

  return (
    <StepCard
      title="Commit account mapping"
      subtitle="Load account mapping into dim_gl_account / dim_gl_na / dim_gl_cf."
    >
      <div className="space-y-4">
        <p className="text-sm text-slate-600">
          Account mapping is written directly to the database (no staging).
          Existing rows are updated (upsert).
        </p>
        <div className="rounded-md border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700 space-y-1">
          <p>
            <span className="font-medium">Entity:</span>{" "}
            {kontext.entityMode === "fixed" ? kontext.entityValue : `Column: ${kontext.entityValue}`}
          </p>
          <p>
            <span className="font-medium">Fiscal year:</span>{" "}
            {kontext.fiscalYearMode === "fixed"
              ? kontext.fiscalYearValue
              : `Column: ${kontext.fiscalYearValue}`}
          </p>
          <p>
            <span className="font-medium">Mapped fields:</span>{" "}
            {Object.keys(mapping).length}
          </p>
        </div>

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="button"
          onClick={handleCommit}
          disabled={committing}
          className="rounded-md bg-blue-600 px-6 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40"
        >
          {committing ? "Loading..." : "Commit now"}
        </button>
      </div>
    </StepCard>
  );
}

// ---------------------------------------------------------------------------
// GL Commit success card
// ---------------------------------------------------------------------------

function CommitSuccessCard({ result }: { result: CommitResponse }) {
  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-8 text-center shadow-sm">
      <div className="text-5xl mb-4">&#10003;</div>
      <h2 className="text-xl font-semibold text-emerald-800 mb-1">
        Ingestion completed successfully
      </h2>
      <p className="text-sm text-emerald-700 mb-6">
        Load ID: {result.load_id ?? "—"}
        {result.commit_mode ? ` · mode: ${result.commit_mode}` : ""}
      </p>
      <div className="inline-grid grid-cols-3 gap-x-8 gap-y-3 text-left mb-6">
        {[
          ["Entries", result.entries],
          ["Lines", result.lines],
          ["Sales", result.sales],
          ["CoM", result.com],
          ["AR", result.ar],
          ["AP", result.ap],
          ["Skipped", result.skipped],
        ].map(([label, val]) => (
          <div key={String(label)}>
            <div className="text-xs text-emerald-600 font-medium">{label}</div>
            <div className="text-lg font-semibold text-emerald-900">
              {Number(val).toLocaleString("en-US")}
            </div>
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={() => window.location.reload()}
        className="rounded-md border border-emerald-400 px-5 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-100"
      >
        Import another file
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Profile loader bar
// ---------------------------------------------------------------------------

function ProfileBar({
  profiles,
  onLoad,
}: {
  profiles: MappingProfile[];
  onLoad: (p: MappingProfile) => void;
}) {
  const [open, setOpen] = useState(false);
  if (profiles.length === 0) return null;

  return (
    <div className="mb-5 rounded-lg border border-slate-200 bg-white px-4 py-3 flex items-center gap-3 shadow-sm">
      <span className="text-sm text-slate-600 font-medium">Saved profiles:</span>
      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        >
          Load profile &#9660;
        </button>
        {open && (
          <div className="absolute left-0 top-full mt-1 z-10 min-w-[220px] rounded-lg border border-slate-200 bg-white shadow-lg">
            {profiles.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => {
                  onLoad(p);
                  setOpen(false);
                }}
                className="block w-full text-left px-4 py-2.5 text-sm text-slate-700 hover:bg-slate-50 first:rounded-t-lg last:rounded-b-lg"
              >
                <span className="font-medium">{p.name}</span>
                <span className="ml-2 text-xs text-slate-400">{p.source_system}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Profile builder helper
// ---------------------------------------------------------------------------

/** Default GoBD GL column mapping when source headers match the Decidra export. */
const GOBD_GL_COLUMN_DEFAULTS: Record<string, string> = {
  journal_entry_number: "Transaction number",
  account_number: "Account number",
  posting_date: "Posting date",
  document_date: "Document date",
  document_type: "Document type",
  reference_document_number: "Document number",
  amount: "Amount",
  vat_amount: "VAT amount",
  line_note: "Booking text",
  posting_type: "Posting type",
  source_type: "Source type",
  source_no: "Source No.",
};

function suggestGlColumnMapping(columns: string[]): Record<string, string> {
  const colSet = new Set(columns);
  const mapping: Record<string, string> = {};
  for (const [target, source] of Object.entries(GOBD_GL_COLUMN_DEFAULTS)) {
    if (colSet.has(source)) mapping[target] = source;
  }
  return mapping;
}

function buildProfile(
  kontext: KontextState,
  mapping: Record<string, string>,
  opts: OptionsState,
  dialect: { decimal: string; thousands: string },
  variant: "gl" | "account-mapping" = "gl",
  entityAssignments: Record<string, string> = {}
): Profile {
  const signConfig = (() => {
    if (opts.signMode === "signed") return { mode: "signed" as const, amount: opts.signAmount };
    if (opts.signMode === "soll_haben")
      return { mode: "soll_haben" as const, soll: opts.signSoll, haben: opts.signHaben };
    return {
      mode: "amount_dc" as const,
      amount: opts.signAmount,
      dc_flag: opts.signDcFlag,
      debit_value: opts.signDebitValue,
    };
  })();

  const fiscalYear =
    variant === "gl"
      ? { mode: "from_date" as const, value: "" }
      : { mode: kontext.fiscalYearMode, value: kontext.fiscalYearValue };

  return {
    entity: { mode: kontext.entityMode, value: kontext.entityValue },
    fiscal_year: fiscalYear,
    sign: signConfig,
    decimal: opts.decimal || dialect.decimal || ",",
    thousands: opts.thousands,
    date_dayfirst: opts.dateDayfirst,
    columns: mapping,
    linking_strategy: opts.linking,
    entry_type: "actual",
    ...(Object.keys(entityAssignments).length > 0
      ? { entity_assignments: entityAssignments }
      : {}),
  };
}

// ---------------------------------------------------------------------------
// IngestionPage — main orchestrator
// ---------------------------------------------------------------------------

export default function IngestionPage() {
  const { isAdmin } = useAuth();
  const [searchParams] = useSearchParams();
  const mode: WizardMode =
    searchParams.get("mode") === "account-mapping" ? "account-mapping" : "gl";
  const prevModeRef = useRef<WizardMode | null>(null);

  // GL mode state
  const [glStep, setGlStep] = useState<GLStepIndex>(0);

  // Account-mapping mode state
  const [amStep, setAmStep] = useState<AccountStepIndex>(0);

  // Shared upload state (reset when mode changes)
  const [uploadResult, setUploadResult] = useState<UploadResponse | null>(null);

  // Kontext (shared shape, used by both modes)
  const [kontext, setKontext] = useState<KontextState>({
    entityMode: "fixed",
    entityValue: "",
    fiscalYearMode: "fixed",
    fiscalYearValue: "",
  });

  // GL mapping
  const [glMapping, setGlMapping] = useState<Record<string, string>>({});
  const [entityAssignments, setEntityAssignments] = useState<Record<string, string>>({});

  // Account mapping
  const [amMapping, setAmMapping] = useState<Record<string, string>>({});

  // BS/PL Master wizard (account-mapping sub-flow)
  const [bsPlMode, setBsPlMode] = useState(false);
  const [availableFiscalYears, setAvailableFiscalYears] = useState<number[]>([]);
  const [fiscalYearsLoading, setFiscalYearsLoading] = useState(false);
  const [selectedFiscalYears, setSelectedFiscalYears] = useState<number[]>([]);
  const [bsPlReplaceMode, setBsPlReplaceMode] = useState<BsPlReplaceMode>("append");
  const [bsPlPreview, setBsPlPreview] = useState<MappingPreviewResponse | null>(null);
  const [bsPlPreviewLoading, setBsPlPreviewLoading] = useState(false);
  const [bsPlPreviewError, setBsPlPreviewError] = useState<string | null>(null);
  const bsPlPreviewLoadedKey = useRef<string | null>(null);
  const bsPlPreviewRequestId = useRef(0);

  // GL Options
  const [opts, setOpts] = useState<OptionsState>({
    signMode: "signed",
    signAmount: "",
    signSoll: "",
    signHaben: "",
    signDcFlag: "",
    signDebitValue: "S",
    decimal: ",",
    thousands: ".",
    dateDayfirst: true,
    linking: "txn",
    profileName: "",
    profileSystem: "",
  });

  // Profile save (GL mode)
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileSaved, setProfileSaved] = useState(false);
  const [profiles, setProfiles] = useState<MappingProfile[]>([]);
  const [profilesLoading, setProfilesLoading] = useState(true);

  // Validation result (GL mode)
  const [_validationResult, setValidationResult] = useState<ValidationResponse | null>(null);
  const [glCommitResult, setGlCommitResult] = useState<CommitResponse | null>(null);

  function resetWizard() {
    setGlStep(0);
    setGlCommitResult(null);
    setAmStep(0);
    setUploadResult(null);
    setKontext({ entityMode: "fixed", entityValue: "", fiscalYearMode: "fixed", fiscalYearValue: "" });
    setGlMapping({});
    setEntityAssignments({});
    setAmMapping({});
    setBsPlMode(false);
    setSelectedFiscalYears([]);
    setBsPlReplaceMode("append");
    setBsPlPreview(null);
    setBsPlPreviewError(null);
  }

  useEffect(() => {
    if (prevModeRef.current === null) {
      prevModeRef.current = mode;
      return;
    }
    if (prevModeRef.current !== mode) {
      resetWizard();
      prevModeRef.current = mode;
    }
  }, [mode]);

  useEffect(() => {
    setEntityAssignments({});
  }, [kontext.entityMode, kontext.entityValue]);

  // Load saved profiles on mount
  useEffect(() => {
    setProfilesLoading(true);
    listProfiles()
      .then(setProfiles)
      .catch(() => {
        // silently ignore — backend may not be running
      })
      .finally(() => setProfilesLoading(false));
  }, []);

  // When dialect detected, pre-fill GL options
  useEffect(() => {
    if (uploadResult?.dialect) {
      setOpts((prev) => ({
        ...prev,
        decimal: uploadResult.dialect.decimal || ",",
        thousands: uploadResult.dialect.thousands || ".",
      }));
    }
  }, [uploadResult]);

  function handleUploaded(result: UploadResponse, _file: File) {
    setUploadResult(result);
    const bsPl = mode === "account-mapping" && isBsPlMasterSheets(result.sheets);
    setBsPlMode(bsPl);
    setBsPlPreview(null);
    setBsPlPreviewError(null);
    bsPlPreviewLoadedKey.current = null;
    if (mode === "gl") {
      const suggested = suggestGlColumnMapping(result.columns);
      if (Object.keys(suggested).length > 0) {
        setGlMapping(suggested);
      }
      if (result.columns.includes("Entity No")) {
        setKontext((prev) => ({
          ...prev,
          entityMode: "column",
          entityValue: "Entity No",
        }));
      } else if (result.columns.includes("Entity")) {
        setKontext((prev) => ({
          ...prev,
          entityMode: "column",
          entityValue: "Entity",
        }));
      }
      if (result.columns.includes("Amount")) {
        setOpts((prev) => ({ ...prev, signMode: "signed", signAmount: "Amount" }));
      }
    }
    if (bsPl) {
      setFiscalYearsLoading(true);
      getIngestFiscalYears()
        .then((r) => {
          setAvailableFiscalYears(r.fiscal_years);
          if (r.fiscal_years.length === 1) {
            setSelectedFiscalYears(r.fiscal_years);
          }
        })
        .catch(() => setAvailableFiscalYears([]))
        .finally(() => setFiscalYearsLoading(false));
    } else {
      setSelectedFiscalYears([]);
    }
    if (mode === "gl") setGlStep(1);
    else setAmStep(1);
  }

  const loadBsPlPreview = useCallback(async () => {
    if (!uploadResult || selectedFiscalYears.length === 0) return;
    const cacheKey = bsPlPreviewCacheKey(
      uploadResult.file_id,
      selectedFiscalYears,
      bsPlReplaceMode
    );
    if (bsPlPreviewLoadedKey.current === cacheKey) return;

    const requestId = ++bsPlPreviewRequestId.current;
    const showBlockingLoader = bsPlPreviewLoadedKey.current === null;
    if (showBlockingLoader) {
      setBsPlPreviewLoading(true);
    }
    setBsPlPreviewError(null);
    try {
      const p = await previewAccountMapping({
        file_id: uploadResult.file_id,
        format: "bs_pl_master",
        fiscal_years: selectedFiscalYears,
        replace_mode: bsPlReplaceMode,
      });
      if (requestId !== bsPlPreviewRequestId.current) return;
      setBsPlPreview(p);
      bsPlPreviewLoadedKey.current = cacheKey;
    } catch (e) {
      if (requestId !== bsPlPreviewRequestId.current) return;
      setBsPlPreview(null);
      bsPlPreviewLoadedKey.current = null;
      setBsPlPreviewError(e instanceof Error ? e.message : "Preview failed");
    } finally {
      if (requestId === bsPlPreviewRequestId.current) {
        setBsPlPreviewLoading(false);
      }
    }
  }, [uploadResult, selectedFiscalYears, bsPlReplaceMode]);

  useEffect(() => {
    if (!bsPlMode || !uploadResult || selectedFiscalYears.length === 0) return;
    const key = bsPlPreviewCacheKey(
      uploadResult.file_id,
      selectedFiscalYears,
      bsPlReplaceMode
    );
    if (bsPlPreviewLoadedKey.current && bsPlPreviewLoadedKey.current !== key) {
      bsPlPreviewLoadedKey.current = null;
      bsPlPreviewRequestId.current += 1;
      setBsPlPreview(null);
      setBsPlPreviewError(null);
    }
  }, [bsPlMode, uploadResult?.file_id, selectedFiscalYears.join(","), bsPlReplaceMode]);

  function handleLoadProfile(p: MappingProfile) {
    const pj = p.profile_json;
    setKontext({
      entityMode: pj.entity.mode,
      entityValue: pj.entity.value,
      fiscalYearMode: pj.fiscal_year.mode,
      fiscalYearValue: pj.fiscal_year.value,
    });
    setGlMapping(pj.columns ?? {});
    setOpts((prev) => ({
      ...prev,
      signMode: pj.sign.mode,
      signAmount: pj.sign.amount ?? "",
      signSoll: pj.sign.soll ?? "",
      signHaben: pj.sign.haben ?? "",
      signDcFlag: pj.sign.dc_flag ?? "",
      signDebitValue: pj.sign.debit_value ?? "S",
      decimal: pj.decimal,
      thousands: pj.thousands,
      dateDayfirst: pj.date_dayfirst,
      linking: pj.linking_strategy,
    }));
  }

  const handleSaveProfile = useCallback(async () => {
    if (!uploadResult) return;
    const profile = buildProfile(kontext, glMapping, opts, uploadResult.dialect, "gl", entityAssignments);
    setSavingProfile(true);
    try {
      const saved = await saveProfile({
        name: opts.profileName,
        source_system: opts.profileSystem || "unbekannt",
        profile_json: profile,
      });
      setProfiles((prev) => [...prev, saved]);
      setProfileSaved(true);
      setTimeout(() => setProfileSaved(false), 3000);
    } catch {
      // silently fail — save is optional
    } finally {
      setSavingProfile(false);
    }
  }, [kontext, glMapping, opts, uploadResult, entityAssignments]);

  // -------------------------------------------------------------------------
  // GL mode helpers
  // -------------------------------------------------------------------------

  const glMissing = missingRequiredFields(glMapping);
  const glKontextValid = kontext.entityValue.trim() !== "";
  const amKontextValid =
    kontext.entityValue.trim() !== "" && kontext.fiscalYearValue.trim() !== "";

  const glCanGoNext: Record<number, boolean> = {
    0: Boolean(uploadResult),
    1: glKontextValid,
    2: glMissing.length === 0,
    3: true,
    4: true,
    5: true,
  };

  function glGoNext() {
    if (glStep < 5) setGlStep((s) => (s + 1) as GLStepIndex);
  }
  function glGoBack() {
    if (glStep === 5 && glCommitResult) {
      setGlCommitResult(null);
      setGlStep(4);
      return;
    }
    if (glStep > 0) setGlStep((s) => (s - 1) as GLStepIndex);
  }

  const glProfile =
    uploadResult ? buildProfile(kontext, glMapping, opts, uploadResult.dialect, "gl", entityAssignments) : null;

  // -------------------------------------------------------------------------
  // Account-mapping mode helpers
  // -------------------------------------------------------------------------

  const amMissing = missingRequiredAccountFields(amMapping);

  const bsPlFyValid = selectedFiscalYears.length > 0;
  const bsPlPreviewOk = Boolean(bsPlPreview && bsPlPreview.blockers.length === 0);

  const amCanGoNext: Record<number, boolean> = bsPlMode
    ? {
        0: Boolean(uploadResult),
        1: bsPlFyValid,
        2: bsPlPreviewOk && !bsPlPreviewLoading,
        3: true,
      }
    : {
        0: Boolean(uploadResult),
        1: amKontextValid,
        2: amMissing.length === 0,
        3: true,
      };

  function amGoNext() {
    if (amStep < 3) setAmStep((s) => (s + 1) as AccountStepIndex);
  }
  function amGoBack() {
    if (amStep > 0) setAmStep((s) => (s - 1) as AccountStepIndex);
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const currentStep =
    mode === "gl"
      ? glCommitResult
        ? GL_STEPS.length
        : glStep
      : amStep;
  const steps =
    mode === "gl"
      ? GL_STEPS
      : bsPlMode
      ? ACCOUNT_STEPS_BSPL
      : ACCOUNT_STEPS;
  const canGoNext = mode === "gl" ? glCanGoNext : amCanGoNext;

  return (
    <PageShell
      loading={profilesLoading}
      message="Loading Data Update…"
      submessage="Saved mapping profiles and upload settings are loading."
    >
    <div>
      <DataUpdateNav
        topMode={mode}
        accountView="upload"
      />

      <Stepper current={currentStep} steps={steps} />

      {/* Profile bar — only in GL mode after upload */}
      {mode === "gl" && uploadResult && glStep > 0 && glStep < 4 && (
        <ProfileBar profiles={profiles} onLoad={handleLoadProfile} />
      )}

      {/* ------------------------------------------------------------------ GL mode */}
      {mode === "gl" && (
        <>
          {glStep === 0 && <UploadStep onUploaded={handleUploaded} />}
          {glStep === 1 && uploadResult && (
            <>
              <KontextStep
                sourceColumns={uploadResult.columns}
                state={kontext}
                onChange={setKontext}
                variant="gl"
              />
              <NavButtons
                step={glStep}
                totalSteps={GL_STEPS.length}
                onBack={glGoBack}
                onNext={glGoNext}
                nextDisabled={!canGoNext[glStep]}
              />
            </>
          )}
          {glStep === 2 && uploadResult && (
            <>
              <MappingStep
                uploadResult={uploadResult}
                mapping={glMapping}
                onChange={setGlMapping}
              />
              <NavButtons
                step={glStep}
                totalSteps={GL_STEPS.length}
                onBack={glGoBack}
                onNext={glGoNext}
                nextDisabled={!canGoNext[glStep]}
                nextLabel={glMissing.length > 0 ? `Next (${glMissing.length} required fields missing)` : "Next"}
              />
            </>
          )}
          {glStep === 3 && uploadResult && (
            <>
              <OptionsStep
                sourceColumns={uploadResult.columns}
                dialect={uploadResult.dialect}
                state={opts}
                onChange={setOpts}
                onSaveProfile={handleSaveProfile}
                savingProfile={savingProfile}
                profileSaved={profileSaved}
              />
              <NavButtons
                step={glStep}
                totalSteps={GL_STEPS.length}
                onBack={glGoBack}
                onNext={glGoNext}
                nextDisabled={!canGoNext[glStep]}
                nextLabel="Start validation"
              />
            </>
          )}
          {glStep === 4 && uploadResult && glProfile && !glCommitResult && (
            <ValidierungStep
              uploadResult={uploadResult}
              profile={glProfile}
              onResult={setValidationResult}
              onEntityAssignmentsChange={setEntityAssignments}
              onImportSuccess={(r) => {
                setGlCommitResult(r);
                setGlStep(5);
              }}
            />
          )}
          {glStep === 5 && glCommitResult && (
            <CommitSuccessCard result={glCommitResult} />
          )}
          {glStep === 4 && !glCommitResult && (
            <div className="mt-4">
              <button
                type="button"
                onClick={glGoBack}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Back to options
              </button>
            </div>
          )}
        </>
      )}

      {/* ------------------------------------------------------------------ Account-mapping mode */}
      {mode === "account-mapping" && bsPlMode && (
        <>
          {amStep === 0 && <UploadStep onUploaded={handleUploaded} />}
          {amStep === 1 && uploadResult && (
            <>
              <BsPlFiscalYearStep
                availableYears={availableFiscalYears}
                selected={selectedFiscalYears}
                onChange={setSelectedFiscalYears}
                loading={fiscalYearsLoading}
                replaceMode={bsPlReplaceMode}
                onReplaceModeChange={setBsPlReplaceMode}
              />
              <NavButtons
                step={amStep}
                totalSteps={ACCOUNT_STEPS_BSPL.length}
                onBack={amGoBack}
                onNext={amGoNext}
                nextDisabled={!canGoNext[amStep]}
              />
            </>
          )}
          {amStep === 2 && uploadResult && (
            <>
              <BsPlPreviewStep
                uploadResult={uploadResult}
                fiscalYears={selectedFiscalYears}
                replaceMode={bsPlReplaceMode}
                preview={bsPlPreview}
                loading={bsPlPreviewLoading}
                error={bsPlPreviewError}
                onLoadPreview={loadBsPlPreview}
              />
              <NavButtons
                step={amStep}
                totalSteps={ACCOUNT_STEPS_BSPL.length}
                onBack={amGoBack}
                onNext={amGoNext}
                nextDisabled={!canGoNext[amStep]}
                nextLabel={
                  bsPlPreview?.blockers?.length
                    ? "Resolve blockers"
                    : bsPlPreviewLoading
                    ? "Loading preview…"
                    : "Next"
                }
              />
            </>
          )}
          {amStep === 3 && uploadResult && (
            <>
              <BsPlCommitStep
                uploadResult={uploadResult}
                fiscalYears={selectedFiscalYears}
                replaceMode={bsPlReplaceMode}
                preview={bsPlPreview}
              />
              <div className="mt-4">
                <button
                  type="button"
                  onClick={amGoBack}
                  className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                >
                  Back to preview
                </button>
              </div>
            </>
          )}
        </>
      )}
      {mode === "account-mapping" && !bsPlMode && (
        <>
          {amStep === 0 && <UploadStep onUploaded={handleUploaded} />}
          {amStep === 1 && uploadResult && (
            <>
              <KontextStep
                sourceColumns={uploadResult.columns}
                state={kontext}
                onChange={setKontext}
              />
              <NavButtons
                step={amStep}
                totalSteps={ACCOUNT_STEPS.length}
                onBack={amGoBack}
                onNext={amGoNext}
                nextDisabled={!canGoNext[amStep]}
              />
            </>
          )}
          {amStep === 2 && uploadResult && (
            <>
              <AccountMappingStep
                uploadResult={uploadResult}
                mapping={amMapping}
                onChange={setAmMapping}
              />
              <NavButtons
                step={amStep}
                totalSteps={ACCOUNT_STEPS.length}
                onBack={amGoBack}
                onNext={amGoNext}
                nextDisabled={!canGoNext[amStep]}
                nextLabel={amMissing.length > 0 ? `Next (${amMissing.length} required fields missing)` : "Next"}
              />
            </>
          )}
          {amStep === 3 && uploadResult && (
            <>
              <AccountMappingCommitStep
                uploadResult={uploadResult}
                kontext={kontext}
                mapping={amMapping}
              />
              <div className="mt-4">
                <button
                  type="button"
                  onClick={amGoBack}
                  className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                >
                  Back to mapping
                </button>
              </div>
            </>
          )}
        </>
      )}

      {isAdmin && <VersionHistoryPanel />}
    </div>
    </PageShell>
  );
}
