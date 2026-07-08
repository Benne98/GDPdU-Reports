/**
 * Unit checks for buildAnnualFlowReportColumns — the DISPLAY GATE for the annual
 * Forecast (fy_f) + Coverage columns.  The two columns render ONLY when
 * hasPlanData is true; absent/false hides BOTH.
 *
 * Run: npx tsx src/components/financials/annual/annualFlowReportColumns.test.ts
 *
 * Uses only node:assert (no test framework required).
 */
import assert from 'node:assert/strict';
import { buildAnnualFlowReportColumns } from './annualFlowReportColumns';

const ids = (cols: { id: string }[]) => cols.map(c => c.id);

// --------------------------------------------------------------------------
// 1. hasPlanData omitted → default false → BOTH forecast + coverage hidden.
// --------------------------------------------------------------------------
{
  const cols = buildAnnualFlowReportColumns(undefined, 2025, 6);
  assert.ok(!ids(cols).includes('forecast'), 'forecast hidden when flag absent (default false)');
  assert.ok(!ids(cols).includes('coverage_pct'), 'coverage hidden when flag absent (default false)');
}

// --------------------------------------------------------------------------
// 2. hasPlanData=false → BOTH forecast + coverage hidden; core columns kept.
// --------------------------------------------------------------------------
{
  const cols = buildAnnualFlowReportColumns(undefined, 2025, 6, false);
  assert.ok(!ids(cols).includes('forecast'), 'forecast hidden when hasPlanData=false');
  assert.ok(!ids(cols).includes('coverage_pct'), 'coverage hidden when hasPlanData=false');
  for (const id of ['fy2', 'fy3', 'cagr', 'ytd']) {
    assert.ok(ids(cols).includes(id), `${id} still present when hasPlanData=false`);
  }
}

// --------------------------------------------------------------------------
// 3. hasPlanData=true → BOTH forecast + coverage render (parity with today).
// --------------------------------------------------------------------------
{
  const cols = buildAnnualFlowReportColumns(undefined, 2025, 6, true);
  assert.ok(ids(cols).includes('forecast'), 'forecast shown when hasPlanData=true');
  assert.ok(ids(cols).includes('coverage_pct'), 'coverage shown when hasPlanData=true');
  const fc = cols.find(c => c.id === 'forecast');
  assert.ok(fc, 'forecast column present');
  assert.equal(fc!.amountKey, 'fy_f', 'forecast column maps to the fy_f amount key');
}

console.log('annualFlowReportColumns.test.ts: all assertions passed');
