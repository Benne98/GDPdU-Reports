/**
 * IngestionPage — GL-Ingestion + Konten-Mapping (two modes).
 *
 * Mode selector at the top lets admins switch between:
 *   A) GL-Ingestion   — Per-entity years-first wizard using GlEntityCard
 *   B) Konten-Mapping — Upload → Kontext → Spalten-Mapping → Commit
 *      Uses POST /api/v1/ingest/mapping/commit (require_admin).
 *   C) Partner-Master — inline PartnerMasterEditor
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import DataUpdateNav from "../components/ingest/DataUpdateNav";
import AccountColumnMapper, { missingRequiredAccountFields } from "../components/ingest/AccountColumnMapper";
import VersionHistoryPanel from "../components/ingest/VersionHistoryPanel";
import UploadStep, { isBsPlMasterSheets } from "../components/ingest/UploadStep";
import KontextStep from "../components/ingest/KontextStep";
import { Stepper, StepCard, NavButtons } from "../components/ingest/IngestStepCard";
import type { KontextState } from "../components/ingest/ingestTypes";
import { useAuth } from "../context/AuthContext";
import { api } from "../lib/api";
import {
  commitAccountMapping,
  commitIngest,
  getIngestFiscalYears,
  previewAccountMapping,
  type AccountMappingProfile,
  type CommitResponse,
  type MappingCommitResponse,
  type MappingPreviewResponse,
  type UploadResponse,
  type BsPlReplaceMode,
} from "../lib/gdpduApi";
import {
  GlEntityCard,
  defaultGlEntityState,
  type GlEntityState,
  type GlFormatGroup,
} from "../components/ingest/GlEntityCard";
import GlGroupUploadCard, { type GlGroupState } from "../components/ingest/GlGroupUploadCard";
import { IS_DATA_UPDATE_V4 } from "../lib/dataUpdateMode";
import GlGroupConfigPanel from "../components/ingest/GlGroupConfigPanel";
import {
  createGroup,
  reindexGroupsAfterRemove,
  applyGroupHeadersToMembers,
} from "../components/ingest/glFormatGroups";
import PageShell from "../components/ui/PageShell";
import SoftSegment from "../components/ui/SoftSegment";
import PartnerMasterEditor from "../components/masters/PartnerMasterEditor";
import { glFiscalYearLabel, fyEndMonthName } from "../lib/fiscalYear";
import { fyEndFromStartMonth } from "./ProjectSetupWizard";

// ---------------------------------------------------------------------------
// Mode selector
// ---------------------------------------------------------------------------

type WizardMode = "gl" | "account-mapping" | "partner-master";

// ---------------------------------------------------------------------------
// Stepper definitions (account-mapping only)
// ---------------------------------------------------------------------------

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

type AccountStepIndex = 0 | 1 | 2 | 3;

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
                <span>Duplicate rows: {duplicates.length}</span>
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
                <span>New accounts (insert): {inserts.length}</span>
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
  onAddAnother,
}: {
  uploadResult: UploadResponse;
  fiscalYears: number[];
  replaceMode: BsPlReplaceMode;
  preview: MappingPreviewResponse | null;
  onAddAnother?: () => void;
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
        <div className="flex items-center justify-center gap-3 flex-wrap">
          {onAddAnother && (
            <button
              type="button"
              onClick={onAddAnother}
              className="rounded-md border-2 border-dashed border-emerald-400 px-5 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-100 transition"
            >
              + Add another mapping table (entity / year)
            </button>
          )}
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-md border border-emerald-400 px-5 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-100"
          >
            Done — start over
          </button>
        </div>
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
  onAddAnother,
}: {
  uploadResult: UploadResponse;
  kontext: KontextState;
  mapping: Record<string, string>;
  onAddAnother?: () => void;
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
        <div className="flex items-center justify-center gap-3 flex-wrap">
          {onAddAnother && (
            <button
              type="button"
              onClick={onAddAnother}
              className="rounded-md border-2 border-dashed border-emerald-400 px-5 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-100 transition"
            >
              + Add another mapping table (entity / year)
            </button>
          )}
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-md border border-emerald-400 px-5 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-100"
          >
            Done — start over
          </button>
        </div>
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
// Data Update GL — per-entity commit section
// ---------------------------------------------------------------------------

function DuGlCommitSection({
  entities,
  onDone,
  uploadMode = 'per-entity',
  group,
}: {
  entities: GlEntityState[];
  onDone: () => void;
  uploadMode?: 'per-entity' | 'group';
  group?: GlGroupState;
}) {
  // Group commit state (hooks before any conditional return)
  const [groupCommitting, setGroupCommitting] = useState(false);
  const [groupResult, setGroupResult] = useState<CommitResponse | string | null>(null);

  const committable = entities.filter(
    (e) => e.validationOk !== undefined && e.assembledProfile && e.combinedFileId
  );

  const [results, setResults] = useState<Record<number, CommitResponse | string>>({});
  const [committing, setCommitting] = useState<Record<number, boolean>>({});

  async function handleCommitGroup() {
    if (!group?.fileId || !group?.assembledProfile) return;
    setGroupCommitting(true);
    setGroupResult(null);
    try {
      const r = await commitIngest({
        file_id: group.fileId,
        profile: group.assembledProfile,
        dataset: "gl",
        confirm_soft: true,
        commit_mode: "replace",
      });
      setGroupResult(r);
    } catch (e) {
      setGroupResult(e instanceof Error ? e.message : "Commit failed");
    } finally {
      setGroupCommitting(false);
    }
  }

  async function handleCommitEntity(entity: GlEntityState, idx: number) {
    if (!entity.combinedFileId || !entity.assembledProfile) return;
    setCommitting((prev) => ({ ...prev, [idx]: true }));
    try {
      const r = await commitIngest({
        file_id: entity.combinedFileId,
        profile: entity.assembledProfile,
        dataset: "gl",
        confirm_soft: true,
        commit_mode: "replace",
      });
      setResults((prev) => ({ ...prev, [idx]: r }));
    } catch (e) {
      setResults((prev) => ({ ...prev, [idx]: e instanceof Error ? e.message : "Commit failed" }));
    } finally {
      setCommitting((prev) => ({ ...prev, [idx]: false }));
    }
  }

  // Group mode — render commit UI for the single group file
  if (uploadMode === 'group' && group) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white px-6 py-5 shadow-sm space-y-4">
        <h3 className="text-base font-semibold text-slate-900">Commit group upload</h3>
        <p className="text-sm text-slate-500">
          Commit the group file to the database. Rows are routed to the correct entity via
          entity_assignments.
        </p>
        {typeof groupResult === "string" ? (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {groupResult}
          </div>
        ) : groupResult ? (
          <div className="space-y-3">
            <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
              Committed — {groupResult.entries} entries written.
            </div>
            <button
              type="button"
              onClick={onDone}
              className="rounded-md border-2 border-dashed border-emerald-400 px-5 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-50 transition"
            >
              + Start over (upload another file)
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => void handleCommitGroup()}
            disabled={groupCommitting}
            className="rounded-md bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40"
          >
            {groupCommitting ? "Committing…" : "Commit group file"}
          </button>
        )}
      </div>
    );
  }

  const allDone = committable.length > 0 && committable.every((_, i) => results[i] !== undefined);

  return (
    <div className="rounded-xl border border-slate-200 bg-white px-6 py-5 shadow-sm space-y-4">
      <h3 className="text-base font-semibold text-slate-900">Commit all entities</h3>
      <p className="text-sm text-slate-500">
        Commit each validated entity to the database. Each entity is committed with{" "}
        <code className="text-xs bg-slate-100 rounded px-1">commit_mode: replace</code>.
      </p>
      <div className="space-y-3">
        {committable.map((entity, idx) => {
          const result = results[idx];
          const busy = committing[idx] ?? false;
          return (
            <div
              key={idx}
              className="flex items-center justify-between rounded-lg border border-slate-200 px-4 py-3"
            >
              <div>
                <span className="text-sm font-medium text-slate-800">
                  {entity.entityLabel || entity.entityCode || `Entity ${idx + 1}`}
                </span>
                <span className="ml-2 text-xs text-slate-400">({entity.entityCode})</span>
                {entity.validationOk === false && (
                  <span className="ml-2 text-xs text-amber-600">Warnings present</span>
                )}
              </div>
              <div className="flex items-center gap-3">
                {typeof result === "string" ? (
                  <span className="text-xs text-red-600">{result}</span>
                ) : result ? (
                  <span className="text-xs text-emerald-700 font-medium">
                    Committed — {result.entries} entries
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => void handleCommitEntity(entity, idx)}
                    disabled={busy}
                    className="rounded-md bg-blue-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-40"
                  >
                    {busy ? "Committing…" : "Commit"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {allDone && (
        <div className="pt-2">
          <button
            type="button"
            onClick={onDone}
            className="rounded-md border-2 border-dashed border-emerald-400 px-5 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-50 transition"
          >
            + Start over (upload more entities)
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Data Update GL — clickable year chip picker
// ---------------------------------------------------------------------------

const DU_CHIP_TOP = 2026;
const DU_CHIP_BOTTOM = 2020;
const DU_PRIOR_BLOCK = 5;

function duChipYears(priorBlocks: number): number[] {
  const bottom = DU_CHIP_BOTTOM - priorBlocks * DU_PRIOR_BLOCK;
  const years: number[] = [];
  for (let y = bottom; y <= DU_CHIP_TOP; y++) years.push(y);
  return years;
}

function DuGlYearsSelector({
  years,
  fyEndMonth,
  onYearsChange,
}: {
  years: number[];
  fyEndMonth: number;
  onYearsChange: (years: number[]) => void;
}) {
  const [priorBlocks, setPriorBlocks] = useState(0);
  const chips = duChipYears(priorBlocks);
  const selectedSet = new Set(years);

  function toggle(y: number) {
    const next = selectedSet.has(y)
      ? years.filter((v) => v !== y)
      : [...new Set([...years, y])].sort((a, b) => a - b);
    onYearsChange(next);
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white px-6 py-5 shadow-sm space-y-4">
      <div>
        <h3 className="text-base font-semibold text-slate-900">Which fiscal years are you loading?</h3>
        <p className="mt-0.5 text-xs text-slate-500">
          Click years to select them. Each entity card will show one upload slot per year.
        </p>
      </div>
      <div className="space-y-3">
        <p className="text-xs text-slate-500">
          Fiscal years (year-end:{" "}
          <span className="font-medium">{fyEndMonthName(fyEndMonth)}</span>).
          Click to select or deselect.
        </p>
        <div className="flex flex-wrap gap-2">
          {chips.map((y) => {
            const isSelected = selectedSet.has(y);
            return (
              <button
                key={y}
                type="button"
                onClick={() => toggle(y)}
                className={`rounded-full px-3 py-1 text-sm font-semibold border transition focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-blue-500 ${
                  isSelected
                    ? "text-white border-transparent"
                    : "bg-white border-slate-300 text-slate-700 hover:border-blue-400 hover:text-blue-700"
                }`}
                style={isSelected ? { backgroundColor: "#1E3A5F", borderColor: "#1E3A5F" } : undefined}
                aria-pressed={isSelected}
                aria-label={`${isSelected ? "Deselect" : "Select"} ${glFiscalYearLabel(y, fyEndMonth)}`}
              >
                {glFiscalYearLabel(y, fyEndMonth)}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          onClick={() => setPriorBlocks((b) => b + 1)}
          className="text-xs text-slate-500 underline underline-offset-2 hover:text-blue-600 transition"
        >
          + Prior years ({DU_CHIP_BOTTOM - priorBlocks * DU_PRIOR_BLOCK - DU_PRIOR_BLOCK}–{DU_CHIP_BOTTOM - priorBlocks * DU_PRIOR_BLOCK - 1})
        </button>
      </div>
      {years.length === 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Select at least one fiscal year before uploading GL files.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Partner Master Panel — customer/supplier tabs rendered inside Data Update
// ---------------------------------------------------------------------------

function PartnerMasterPanel() {
  const [tab, setTab] = useState<"customers" | "suppliers">("customers");
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white px-6 py-5 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Partner master</h2>
        <p className="mt-0.5 text-sm text-slate-500">
          Add, edit, or delete customer and supplier master records. Use bulk import for large data sets.
        </p>
        <div className="mt-4">
          <SoftSegment
            value={tab}
            onChange={(v) => setTab(v as "customers" | "suppliers")}
            options={[
              { value: "customers", label: "Customers" },
              { value: "suppliers", label: "Suppliers" },
            ]}
          />
        </div>
      </div>
      <PartnerMasterEditor side={tab} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// IngestionPage — main orchestrator
// ---------------------------------------------------------------------------

export default function IngestionPage() {
  const { isAdmin } = useAuth();
  const [searchParams] = useSearchParams();
  const rawMode = searchParams.get("mode");
  const mode: WizardMode =
    rawMode === "account-mapping" ? "account-mapping"
    : rawMode === "partner-master" ? "partner-master"
    : "gl";
  const prevModeRef = useRef<WizardMode | null>(null);

  // -------------------------------------------------------------------------
  // Project fiscal-year end month (fetched once on mount for GL labels)
  // Derived from GET /api/v1/projects/default → fy_start_month
  // end = ((fy_start_month + 10) % 12) + 1  (i.e. month before start)
  // -------------------------------------------------------------------------
  const [duFyEndMonth, setDuFyEndMonth] = useState<number>(12);
  useEffect(() => {
    api.getProject("default")
      .then((r) => setDuFyEndMonth(fyEndFromStartMonth(r.fy_start_month)))
      .catch(() => { /* leave default 12 (December) on error */ });
  }, []);

  // -------------------------------------------------------------------------
  // GL Data Update state (new per-entity model)
  // -------------------------------------------------------------------------
  const [duGlYears, setDuGlYears] = useState<number[]>([]);
  const [duGlEntities, setDuGlEntities] = useState<GlEntityState[]>([defaultGlEntityState()]);
  const [duGlFormatGroups, setDuGlFormatGroups] = useState<GlFormatGroup[]>([]);
  // v4 group-upload state (gated by IS_DATA_UPDATE_V4; default keeps per-entity path unchanged)
  const [duGlUploadMode, setDuGlUploadMode] = useState<'per-entity' | 'group'>('per-entity');
  const [duGlGroup, setDuGlGroup] = useState<GlGroupState>({});

  // -------------------------------------------------------------------------
  // Account-mapping mode state
  // -------------------------------------------------------------------------
  const [amStep, setAmStep] = useState<AccountStepIndex>(0);
  const [uploadResult, setUploadResult] = useState<UploadResponse | null>(null);
  const [kontext, setKontext] = useState<KontextState>({
    entityMode: "fixed",
    entityValue: "",
    fiscalYearMode: "fixed",
    fiscalYearValue: "",
  });
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

  // -------------------------------------------------------------------------
  // Reset helpers
  // -------------------------------------------------------------------------

  function resetWizard() {
    setAmStep(0);
    setUploadResult(null);
    setKontext({ entityMode: "fixed", entityValue: "", fiscalYearMode: "fixed", fiscalYearValue: "" });
    setAmMapping({});
    setBsPlMode(false);
    setSelectedFiscalYears([]);
    setBsPlReplaceMode("append");
    setBsPlPreview(null);
    setBsPlPreviewError(null);
    bsPlPreviewLoadedKey.current = null;
  }

  function resetAmForNextTable() {
    setAmStep(0);
    setUploadResult(null);
    setKontext({ entityMode: "fixed", entityValue: "", fiscalYearMode: "fixed", fiscalYearValue: "" });
    setAmMapping({});
    setBsPlMode(false);
    setSelectedFiscalYears([]);
    setBsPlReplaceMode("append");
    setBsPlPreview(null);
    setBsPlPreviewError(null);
    bsPlPreviewLoadedKey.current = null;
  }

  // -------------------------------------------------------------------------
  // Effects
  // -------------------------------------------------------------------------

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

  // -------------------------------------------------------------------------
  // Account-mapping upload handler
  // -------------------------------------------------------------------------

  function handleUploaded(result: UploadResponse, _file: File) {
    setUploadResult(result);
    const bsPl = mode === "account-mapping" && isBsPlMasterSheets(result.sheets);
    setBsPlMode(bsPl);
    setBsPlPreview(null);
    setBsPlPreviewError(null);
    bsPlPreviewLoadedKey.current = null;
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
    setAmStep(1);
  }

  // -------------------------------------------------------------------------
  // BS/PL preview loader
  // -------------------------------------------------------------------------

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

  // -------------------------------------------------------------------------
  // Account-mapping mode helpers
  // -------------------------------------------------------------------------

  const amMissing = missingRequiredAccountFields(amMapping);
  const amKontextValid =
    kontext.entityValue.trim() !== "" && kontext.fiscalYearValue.trim() !== "";

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

  const amStep_ = amStep;
  const steps = bsPlMode ? ACCOUNT_STEPS_BSPL : ACCOUNT_STEPS;

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <PageShell
      loading={false}
      message="Loading Data Update…"
      submessage="Upload settings are loading."
    >
    <div>
      <DataUpdateNav
        topMode={mode}
        accountView={mode === "account-mapping" ? "upload" : undefined}
      />

      {/* ------------------------------------------------------------------ Partner-master mode */}
      {mode === "partner-master" && (
        <PartnerMasterPanel />
      )}

      {/* ------------------------------------------------------------------ GL mode (new per-entity model) */}
      {mode === "gl" && (
        <div className="space-y-5">
          {/* Header */}
          <div className="rounded-xl border border-slate-200 bg-white px-6 pt-5 pb-4 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">Upload accounting data — per entity</h2>
            <p className="mt-0.5 text-sm text-slate-500">
              Select the fiscal years you are loading, then upload one file per year for each entity.
              Files are combined and you map columns and validate once per entity.
              Commit each entity individually when validation passes.
            </p>
            {duGlEntities.length > 1 && (
              <p className="mt-2 text-xs text-blue-700">
                {duGlEntities.filter((e) => e.validationOk !== undefined).length} of{" "}
                {duGlEntities.length} entities validated.
              </p>
            )}
          </div>

          {/* v4 only: upload mode choice (non-v4 → falsy → renders nothing) */}
          {IS_DATA_UPDATE_V4 && (
            <div className="rounded-xl border border-slate-200 bg-white px-6 py-4 shadow-sm">
              <p className="text-sm font-medium text-slate-700 mb-3">Upload mode</p>
              <div className="flex flex-wrap gap-6">
                {(["per-entity", "group"] as const).map((m) => (
                  <label key={m} className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer">
                    <input
                      type="radio"
                      name="duGlUploadMode"
                      value={m}
                      checked={duGlUploadMode === m}
                      onChange={() => setDuGlUploadMode(m)}
                      className="accent-blue-600"
                    />
                    {m === "per-entity" ? "Upload per entity" : "Upload one file for the whole group"}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* v4 group mode: group upload card + group commit */}
          {IS_DATA_UPDATE_V4 && duGlUploadMode === "group" && (
            <>
              {/* B1: year selector shared with per-entity mode via duGlYears state */}
              <DuGlYearsSelector years={duGlYears} fyEndMonth={duFyEndMonth} onYearsChange={setDuGlYears} />
              <GlGroupUploadCard
                group={duGlGroup}
                years={duGlYears}
                onPatch={(patch) => setDuGlGroup((prev) => ({ ...prev, ...patch }))}
                fyEndMonth={duFyEndMonth}
              />
              {duGlGroup.validationOk !== undefined && (
                <DuGlCommitSection
                  entities={[]}
                  uploadMode="group"
                  group={duGlGroup}
                  onDone={() => {
                    setDuGlGroup({});
                    setDuGlUploadMode("per-entity");
                  }}
                />
              )}
            </>
          )}

          {/* Per-entity mode — non-v4 always evaluates true → renders UNCHANGED */}
          {(!IS_DATA_UPDATE_V4 || duGlUploadMode === "per-entity") && <>

          {/* Years selector */}
          <DuGlYearsSelector years={duGlYears} fyEndMonth={duFyEndMonth} onYearsChange={setDuGlYears} />

          {/* Format group config panels — one per group */}
          {duGlFormatGroups.map((group) => (
            <GlGroupConfigPanel
              key={group.id}
              group={group}
              entities={duGlEntities}
              onPatchGroup={(groupId, patch) => {
                setDuGlFormatGroups((prev) =>
                  prev.map((g) => (g.id === groupId ? { ...g, ...patch } : g))
                );
                setDuGlEntities((prev) =>
                  prev.map((e) =>
                    e.formatGroupId === groupId
                      ? { ...e, validationOk: undefined, assembledProfile: undefined }
                      : e
                  )
                );
              }}
            />
          ))}

          {/* Entity cards */}
          {duGlEntities.map((entity, index) => (
            <GlEntityCard
              key={index}
              entity={entity}
              index={index}
              total={duGlEntities.length}
              years={duGlYears}
              entityMode="select"
              fyEndMonth={duFyEndMonth}
              onPatch={(p) =>
                setDuGlEntities((prev) =>
                  prev.map((e, i) => (i === index ? { ...e, ...p } : e))
                )
              }
              onRemove={
                duGlEntities.length > 1
                  ? () => {
                      setDuGlEntities((prev) => prev.filter((_, i) => i !== index));
                      setDuGlFormatGroups((prev) => reindexGroupsAfterRemove(index, prev));
                    }
                  : undefined
              }
              group={duGlFormatGroups.find((g) => g.id === entity.formatGroupId)}
              groups={duGlFormatGroups}
              entities={duGlEntities}
              onCreateGroup={(fromIndex, headers, mapping) =>
                setDuGlFormatGroups((prev) => {
                  const newGroup = createGroup(fromIndex, headers, mapping, prev);
                  setDuGlEntities((ents) =>
                    ents.map((e, i) =>
                      i === fromIndex ? { ...e, formatGroupId: newGroup.id } : e
                    )
                  );
                  return [...prev, newGroup];
                })
              }
              onAssignGroup={async (groupId, selectedIndices) => {
                const group = duGlFormatGroups.find((g) => g.id === groupId);
                if (!group) return;
                const { patches } = await applyGroupHeadersToMembers(
                  group,
                  selectedIndices,
                  duGlEntities,
                );
                setDuGlFormatGroups((prev) =>
                  prev.map((g) =>
                    g.id === groupId
                      ? { ...g, memberIndices: [...new Set([...g.memberIndices, ...selectedIndices])] }
                      : g
                  )
                );
                if (patches.length > 0) {
                  setDuGlEntities((prev) => {
                    let next = [...prev];
                    for (const { index: pi, patch } of patches) {
                      next = next.map((e, i) => (i === pi ? { ...e, ...patch } : e));
                    }
                    return next;
                  });
                }
                setDuGlEntities((prev) =>
                  prev.map((e, i) =>
                    selectedIndices.includes(i)
                      ? { ...e, formatGroupId: groupId, validationOk: undefined, assembledProfile: undefined }
                      : e
                  )
                );
              }}
              onPatchGroup={(groupId, patch) => {
                setDuGlFormatGroups((prev) =>
                  prev.map((g) => (g.id === groupId ? { ...g, ...patch } : g))
                );
                setDuGlEntities((prev) =>
                  prev.map((e) =>
                    e.formatGroupId === groupId
                      ? { ...e, validationOk: undefined, assembledProfile: undefined }
                      : e
                  )
                );
              }}
            />
          ))}

          {/* Add another entity */}
          <button
            type="button"
            onClick={() => setDuGlEntities((prev) => [...prev, defaultGlEntityState()])}
            className="flex items-center gap-2 rounded-xl border-2 border-dashed border-slate-300 bg-white px-5 py-3 text-sm font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 transition w-full justify-center"
          >
            <span className="text-lg leading-none">+</span>
            Add another entity
          </button>

          {/* Commit section — shown when at least one entity has been validated */}
          {duGlEntities.some((e) => e.validationOk !== undefined) && (
            <DuGlCommitSection
              entities={duGlEntities}
              onDone={() => {
                setDuGlYears([]);
                setDuGlEntities([defaultGlEntityState()]);
                setDuGlFormatGroups([]);
              }}
            />
          )}

          </>}

          {isAdmin && <VersionHistoryPanel />}
        </div>
      )}

      {/* ------------------------------------------------------------------ Account-mapping mode */}
      {mode === "account-mapping" && (
        <>
        <Stepper current={amStep_} steps={steps} />

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
                  nextDisabled={!amCanGoNext[amStep]}
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
                  nextDisabled={!amCanGoNext[amStep]}
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
                  onAddAnother={resetAmForNextTable}
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
                  nextDisabled={!amCanGoNext[amStep]}
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
                  nextDisabled={!amCanGoNext[amStep]}
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
                  onAddAnother={resetAmForNextTable}
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
        </>
      )}
    </div>
    </PageShell>
  );
}
