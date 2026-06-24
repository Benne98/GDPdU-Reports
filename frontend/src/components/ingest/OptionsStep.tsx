/**
 * OptionsStep — Step 3 of the GL Data Update wizard (Transform options).
 *
 * Fully props-driven: receives current OptionsState, dialect hints, source columns,
 * and all callbacks. No dependency on IngestionPage internals.
 *
 * Extracted from IngestionPage.tsx so it can be mounted in any parent wizard.
 */

import type { SignMode } from "../../lib/gdpduApi";
import type { OptionsState } from "./ingestTypes";
import { ColumnSelect, StepCard } from "./IngestStepCard";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface OptionsStepProps {
  sourceColumns: string[];
  dialect: { decimal: string; thousands: string };
  state: OptionsState;
  onChange: (s: OptionsState) => void;
  onSaveProfile: () => void;
  savingProfile: boolean;
  profileSaved: boolean;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function OptionsStep({
  sourceColumns,
  dialect,
  state,
  onChange,
  onSaveProfile,
  savingProfile,
  profileSaved,
}: OptionsStepProps) {
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
          <legend className="text-sm font-semibold text-slate-700 mb-2">
            Decimal &amp; thousands separator
          </legend>
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
            ] as [string, string][]
          ).map(([val, label]) => (
            <label key={val} className="flex items-center gap-2 cursor-pointer text-sm mb-2">
              <input
                type="radio"
                name="linking"
                value={val}
                checked={state.linking === val}
                onChange={() => set("linking", val as OptionsState["linking"])}
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
