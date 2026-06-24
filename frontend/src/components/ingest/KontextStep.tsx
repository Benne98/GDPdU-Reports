/**
 * KontextStep — Step 1 of the Data Update wizard (Entity + Fiscal Year context).
 *
 * Fully props-driven: receives sourceColumns, current KontextState, and an onChange
 * callback. No dependency on IngestionPage internals.
 *
 * Extracted from IngestionPage.tsx so it can be mounted in any parent wizard.
 */

import type { KontextState } from "./ingestTypes";
import { StepCard } from "./IngestStepCard";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface KontextStepProps {
  sourceColumns: string[];
  state: KontextState;
  onChange: (s: KontextState) => void;
  /** "gl" hides the fiscal year field (derived from posting date).
   *  "account-mapping" (default) shows it. */
  variant?: "gl" | "account-mapping";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function KontextStep({
  sourceColumns,
  state,
  onChange,
  variant = "account-mapping",
}: KontextStepProps) {
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
