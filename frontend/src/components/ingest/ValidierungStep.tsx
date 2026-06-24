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

import { useEffect, useState } from "react";
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
}: ValidierungStepProps) {
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
              <p className="font-semibold text-slate-800 text-sm">What "Run checks" validates</p>
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
