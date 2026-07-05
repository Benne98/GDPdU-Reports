/**
 * ValidierungStep — Step 4 of the GL Data Update wizard (Validation + Commit trigger).
 *
 * Fully props-driven: receives uploadResult, the assembled Profile, and callbacks.
 * No dependency on IngestionPage internals.
 *
 * The component manages its own local UI state (loading, error, result, entity
 * preview, exclusions) — none of this needs to live in the parent, because the
 * parent only cares about the final outcomes via onResult / onEntityAssignmentsChange
 * / onImportSuccess.
 *
 * Extracted from IngestionPage.tsx so it can be mounted in any parent wizard.
 */

import { useEffect, useRef, useState } from "react";
import {
  commitIngest,
  previewEntityAssignments,
  validateIngest,
  type CommitResponse,
  type EntityPreviewResponse,
  type Profile,
  type UploadResponse,
  type ValidationResponse,
} from "../../lib/gdpduApi";
import { StepCard } from "./IngestStepCard";
import ValidationReport from "./ValidationReport";

// ---------------------------------------------------------------------------
// Required GL column keys — must be mapped before a validate request can fire.
// Mirrors REQUIRED_GL_COLUMN_KEYS in ColumnMapper.tsx (kept inline so this
// component stays self-contained without a new import).
const REQUIRED_GL_VALIDATE_COLS: Array<[key: string, label: string]> = [
  ["posting_date", "Posting date"],
  ["journal_entry_number", "Transaction/journal number"],
  ["account_number", "Account number"],
];

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ValidierungStepProps {
  uploadResult: UploadResponse;
  profile: Profile;
  onResult: (r: ValidationResponse) => void;
  onEntityAssignmentsChange: (assignments: Record<string, string>) => void;
  onImportSuccess: (result: CommitResponse) => void;
  /**
   * When true the component runs /ingest/validate (staging) but hides the
   * "Import data" commit button. Use in collect-then-commit wizards where the
   * actual commit happens later in a separate Finish step.
   */
  stagingMode?: boolean;
  /**
   * Optional stage gate forwarded to POST /ingest/validate.
   * When 'gl': only S1, S2, B1, B2, B3, Q2 checks run — no reconciliation / chart mapping.
   * Omit (undefined) for the full catalog — protects the Data Update flow in IngestionPage.
   */
  stage?: 'gl' | 'coa' | 'partner' | 'all';
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function ValidierungStep({
  uploadResult,
  profile,
  onResult,
  onEntityAssignmentsChange,
  onImportSuccess,
  stagingMode = false,
  stage,
}: ValidierungStepProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ValidationResponse | null>(null);
  const [committing, setCommitting] = useState(false);
  const [entityPreview, setEntityPreview] = useState<EntityPreviewResponse | null>(null);
  const [entityLoading, setEntityLoading] = useState(true);
  const [entityReady, setEntityReady] = useState(false);
  const [entityPreviewTick, setEntityPreviewTick] = useState(0);
  const [excludedLineIds, setExcludedLineIds] = useState<string[]>([]);
  const [excluding, setExcluding] = useState(false);

  // Always-current ref so runValidation never closes over a stale profile prop.
  // IMPORTANT: updated via direct render-time assignment, NOT via useEffect.
  // useEffect fires after the browser paints, which leaves a one-render window
  // where profileRef.current is stale — the root of the "posting_date not mapped"
  // flaky error when the user clicks Run checks immediately after header
  // confirmation. Direct assignment during render is the established React
  // pattern for keeping a ref synchronised with a prop without lag.
  const profileRef = useRef<Profile>(profile);
  profileRef.current = profile;

  // Derive which required GL columns are absent from the current profile.
  // Computed from the prop directly (not the ref) so the guard reflects the
  // latest render even before runValidation is called.
  const missingValidateCols = REQUIRED_GL_VALIDATE_COLS
    .filter(([key]) => !profile.columns[key])
    .map(([, label]) => label);

  async function runValidationWithExclusions(lineIds: string[] = excludedLineIds) {
    setLoading(true);
    setError(null);
    try {
      const r = await validateIngest({
        file_id: uploadResult.file_id,
        sheet: uploadResult.sheets[0],
        profile: profileRef.current,
        exclude_line_ids: lineIds,
        ...(stage !== undefined ? { stage } : {}),
      });
      setResult(r);
      onResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Validation failed");
    } finally {
      setLoading(false);
    }
  }

  async function applyExclusions(addIds: string[]) {
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
        // Always auto-apply the proposed assignments keyed by source_label.
        // For existing entities (needs_confirmation=false) this was already the
        // behaviour. For NEW entities on a blank project (needs_confirmation=true)
        // we also auto-apply so the parent profile gets entity_assignments before
        // "Run checks" fires. The proposed prefix table is still shown so the
        // user can review; the old manual-confirm button is repurposed as an
        // optional "looks good" acknowledgement rather than a hard gate.
        const auto = Object.fromEntries(
          preview.mappings.map((m) => [m.source_label, m.entity_prefix])
        );
        onEntityAssignmentsChange(auto);
        setEntityReady(true);
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
              {entityPreview.needs_confirmation && (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                  New entity — prefix auto-assigned from the proposed mapping above.
                  The assignment will be confirmed when you run checks. To use a
                  different prefix, contact your administrator to pre-register the
                  entity in the database.
                </p>
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
              <p className="font-semibold text-slate-800 text-sm">What "Run checks" validates</p>
              <p>Staging only — nothing is written to the database until commit.</p>
              {stage === 'gl' ? (
                <ul className="list-disc pl-4 space-y-0.5">
                  <li><strong>S1</strong> — required fields filled; account key built from entity + account number</li>
                  <li><strong>S2</strong> — posting year matches booking date</li>
                  <li><strong>B1</strong> — each booking should balance (warning only for GoBD — can be ignored on import)</li>
                  <li><strong>B2</strong> — whole ledger per entity must balance</li>
                  <li><strong>B3</strong> — monthly movements per entity must balance</li>
                  <li><strong>Q2</strong> — row numbers are unique</li>
                </ul>
              ) : (
                <ul className="list-disc pl-4 space-y-0.5">
                  <li><strong>B1</strong> — each booking should balance (warning only for GoBD — can be ignored on import)</li>
                  <li><strong>B2</strong> — whole ledger per entity must balance</li>
                  <li><strong>B3</strong> — monthly movements per entity must balance</li>
                  <li><strong>S1</strong> — required fields filled; account key built from entity + account number</li>
                  <li>Posting dates, unique row numbers, reconciliation, chart mapping</li>
                </ul>
              )}
            </div>
          )}
          <div className="flex flex-col items-center py-6 gap-4">
            <p className="text-sm text-slate-600">
              {entityLoading
                ? "Resolving entity prefixes…"
                : error && !entityPreview
                  ? "Entity prefix preview could not be loaded."
                  : missingValidateCols.length > 0
                    ? "Column mapping is incomplete — see the note below."
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
            {missingValidateCols.length > 0 && (
              <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded px-4 py-2.5 text-center max-w-md">
                {`${missingValidateCols.join(', ')} ${missingValidateCols.length === 1 ? 'is' : 'are'} not mapped. Go back to the header step, assign ${missingValidateCols.length === 1 ? 'this column' : 'these columns'}, then return here.`}
              </p>
            )}
            <button
              type="button"
              onClick={runValidation}
              disabled={
                !entityReady ||
                entityLoading ||
                (!!error && !entityPreview) ||
                missingValidateCols.length > 0
              }
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
          <span className="text-sm text-slate-600">
            Validation running… large files may take several minutes.
          </span>
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
            hideCommit={stagingMode}
          />
        </>
      )}
    </StepCard>
  );
}
