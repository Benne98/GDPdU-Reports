/**
 * PlanPage — Admin-only: generate and view synthetic Plan/Forecast data.
 *
 * POST /api/v1/plan/generate with PlanGenerateRequest payload.
 * GET  /api/v1/plan/summary to display current plan row counts.
 *
 * Request model (from backend/app/routers/plan.py PlanGenerateRequest):
 *   base_fy              int   — last full fiscal year of actuals (e.g. 2024)
 *   current_fy           int   — current/in-progress fiscal year (e.g. 2025)
 *   last_closed_period   int   — 0-12, last closed period of current_fy
 *   horizon_years        int   — plan years after forecast year (default 4)
 *   growth_rate          float — default annual growth rate (default 0.05)
 *   forecast_growth_rate float — extra growth on run-rate for open periods (default 0.0)
 *   group_growth         dict | null — per-group overrides (optional)
 *   group_col            str | null  — GL column for group lookup (optional)
 */

import { useEffect, useState } from "react";
import {
  generatePlan,
  getPlanSummary,
  type PlanGenerateRequest,
  type PlanGenerateResponse,
  type PlanSummaryResponse,
} from "../lib/gdpduApi";
import PageShell from "../components/ui/PageShell";

// ---------------------------------------------------------------------------
// Summary table
// ---------------------------------------------------------------------------

function PlanSummaryTable({ summary }: { summary: PlanSummaryResponse }) {
  if (summary.rows.length === 0) {
    return (
      <p className="text-sm text-slate-500 italic">
        Noch keine Plan-Daten in der Datenbank vorhanden.
      </p>
    );
  }

  const byTable: Record<string, typeof summary.rows> = {};
  for (const r of summary.rows) {
    if (!byTable[r.table]) byTable[r.table] = [];
    byTable[r.table].push(r);
  }

  return (
    <div className="space-y-5">
      {Object.entries(byTable).map(([table, rows]) => (
        <div key={table}>
          <h3 className="text-sm font-semibold text-slate-700 mb-2">{table}</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-slate-200">
                  <th className="text-left py-1.5 pr-4 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    Szenario
                  </th>
                  <th className="text-left py-1.5 pr-4 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    Geschäftsjahr
                  </th>
                  <th className="text-right py-1.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    Zeilen
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="border-b border-slate-100 last:border-0">
                    <td className="py-1.5 pr-4 text-slate-700 font-medium">{r.scenario}</td>
                    <td className="py-1.5 pr-4 text-slate-700">{r.fiscal_year}</td>
                    <td className="py-1.5 text-right text-slate-700">
                      {r.row_count.toLocaleString("de-DE")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function PlanPage() {
  const currentYear = new Date().getFullYear();

  const [formValues, setFormValues] = useState<PlanGenerateRequest>({
    base_fy: currentYear - 1,
    current_fy: currentYear,
    last_closed_period: new Date().getMonth(), // 0-12; JS months are 0-based so this gives "months elapsed"
    horizon_years: 4,
    growth_rate: 0.05,
    forecast_growth_rate: 0.0,
    group_growth: null,
    group_col: null,
  });

  const [generating, setGenerating] = useState(false);
  const [generateResult, setGenerateResult] = useState<PlanGenerateResponse | null>(null);
  const [generateError, setGenerateError] = useState<string | null>(null);

  const [summary, setSummary] = useState<PlanSummaryResponse | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);

  // Load summary on mount
  useEffect(() => {
    setSummaryLoading(true);
    getPlanSummary()
      .then(setSummary)
      .catch((e) => setSummaryError(e instanceof Error ? e.message : "Laden fehlgeschlagen"))
      .finally(() => setSummaryLoading(false));
  }, [generateResult]); // Reload after successful generation

  function setField<K extends keyof PlanGenerateRequest>(k: K, v: PlanGenerateRequest[K]) {
    setFormValues((prev) => ({ ...prev, [k]: v }));
  }

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    setGenerating(true);
    setGenerateError(null);
    setGenerateResult(null);
    try {
      const result = await generatePlan(formValues);
      setGenerateResult(result);
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : "Plan-Generierung fehlgeschlagen");
    } finally {
      setGenerating(false);
    }
  }

  return (
    <PageShell
      loading={summaryLoading || generating}
      message={generating ? "Generating plan…" : "Loading plan data…"}
      submessage="Plan and forecast values are being prepared."
    >
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Plan / Forecast generieren</h1>
        <p className="mt-1 text-sm text-slate-500">
          Synthetische Plan- und Forecast-Werte auf Basis vorhandener GL-Istdaten erzeugen.
          Nur Admins.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Generation form */}
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-lg font-semibold text-slate-900 mb-1">Parameter</h2>
          <p className="text-sm text-slate-500 mb-5">
            Konfigurieren Sie den Planungshorizont und die Wachstumsrate.
          </p>

          <form onSubmit={handleGenerate} className="space-y-5">
            {/* Row 1: base_fy + current_fy */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Basis-GJ (base_fy)
                </label>
                <input
                  type="number"
                  min={2000}
                  max={2099}
                  value={formValues.base_fy}
                  onChange={(e) => setField("base_fy", parseInt(e.target.value, 10))}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <p className="mt-1 text-xs text-slate-400">
                  Letztes vollst. Istjahr (z.B. 2024)
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Laufendes GJ (current_fy)
                </label>
                <input
                  type="number"
                  min={2000}
                  max={2099}
                  value={formValues.current_fy}
                  onChange={(e) => setField("current_fy", parseInt(e.target.value, 10))}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <p className="mt-1 text-xs text-slate-400">
                  Laufendes Geschäftsjahr (z.B. 2025)
                </p>
              </div>
            </div>

            {/* last_closed_period */}
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Letzter abgeschlossener Monat (last_closed_period)
              </label>
              <input
                type="number"
                min={0}
                max={12}
                value={formValues.last_closed_period}
                onChange={(e) => setField("last_closed_period", parseInt(e.target.value, 10))}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="mt-1 text-xs text-slate-400">
                0 = kein Monat abgeschlossen, 12 = vollst. abgeschlossen
              </p>
            </div>

            {/* Row 2: horizon_years + growth_rate */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Planjahre (horizon_years)
                </label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={formValues.horizon_years}
                  onChange={(e) => setField("horizon_years", parseInt(e.target.value, 10))}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <p className="mt-1 text-xs text-slate-400">
                  Jahre nach Forecast-Jahr (1-10)
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Wachstumsrate (growth_rate)
                </label>
                <input
                  type="number"
                  step={0.01}
                  min={-1}
                  value={formValues.growth_rate}
                  onChange={(e) => setField("growth_rate", parseFloat(e.target.value))}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <p className="mt-1 text-xs text-slate-400">
                  Z.B. 0.05 = 5% p.a.
                </p>
              </div>
            </div>

            {/* forecast_growth_rate */}
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Forecast-Wachstum (forecast_growth_rate)
              </label>
              <input
                type="number"
                step={0.01}
                min={-1}
                value={formValues.forecast_growth_rate}
                onChange={(e) => setField("forecast_growth_rate", parseFloat(e.target.value))}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="mt-1 text-xs text-slate-400">
                Zusätzliches Wachstum auf Run-Rate für offene Monate (0 = reiner Run-Rate)
              </p>
            </div>

            {/* Errors */}
            {generateError && (
              <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {generateError}
              </div>
            )}

            {/* Success */}
            {generateResult && (
              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-3">
                <p className="text-sm font-semibold text-emerald-800 mb-1">
                  Plan erfolgreich generiert
                </p>
                <p className="text-xs text-emerald-700">{generateResult.message}</p>
                <div className="mt-2 flex gap-4 text-xs text-emerald-700">
                  <span>
                    GL-Plan: <strong>{generateResult.gl_plan.toLocaleString("de-DE")}</strong> Zeilen
                  </span>
                  <span>
                    Sales-Plan: <strong>{generateResult.sales_plan.toLocaleString("de-DE")}</strong> Zeilen
                  </span>
                </div>
                {generateResult.scenarios.length > 0 && (
                  <p className="mt-1.5 text-xs text-emerald-600">
                    Szenarien: {generateResult.scenarios.join(", ")}
                  </p>
                )}
              </div>
            )}

            <button
              type="submit"
              disabled={generating}
              className="w-full rounded-md bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              {generating ? "Generierung läuft..." : "Plan generieren"}
            </button>
          </form>
        </div>

        {/* Summary */}
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-lg font-semibold text-slate-900 mb-1">Plan-Übersicht</h2>
          <p className="text-sm text-slate-500 mb-5">
            Aktuelle Plan-Zeilen in <code className="rounded bg-slate-100 px-1">fact_gl_plan</code>{" "}
            und <code className="rounded bg-slate-100 px-1">fact_sales_plan</code>.
          </p>

          {summaryLoading && (
            <p className="text-sm text-slate-500 sr-only">Lade Plan-Daten…</p>
          )}
          {summaryError && !summaryLoading && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {summaryError}
            </div>
          )}
          {summary && !summaryLoading && (
            <PlanSummaryTable summary={summary} />
          )}
        </div>
      </div>
    </div>
    </PageShell>
  );
}
