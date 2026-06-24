/**
 * IngestStepCard.tsx — shared presentational primitives for the Data Update wizard.
 *
 * Extracted from IngestionPage.tsx so that both IngestionPage and future wizards
 * (e.g. ProjectSetupWizard) can mount these without coupling to the page.
 *
 * Exports: StepCard, NavButtons, Stepper, ColumnSelect
 */

import React from "react";

// ---------------------------------------------------------------------------
// Stepper
// ---------------------------------------------------------------------------

export function Stepper({
  current,
  steps,
}: {
  current: number;
  steps: readonly string[];
}) {
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

export function StepCard({
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

export function NavButtons({
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
// Column selector (used inside OptionsStep)
// ---------------------------------------------------------------------------

export function ColumnSelect({
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
