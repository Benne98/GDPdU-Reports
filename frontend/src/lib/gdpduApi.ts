/**
 * api.ts — typed fetch helpers for the GDPdU backend.
 * All calls go through /api/v1 (proxied by Vite to :8001).
 *
 * Authorization: every request automatically attaches the stored Bearer token.
 * On 401 responses the token is cleared and the browser is redirected to /login.
 */

// ---------------------------------------------------------------------------
// Token storage keys — must match AuthContext.tsx
// ---------------------------------------------------------------------------

const TOKEN_KEY = "gdpdu_access_token";
const USER_KEY = "gdpdu_user";

/** Validate / commit / upload on large GL files (GoBD-scale). */
export const INGEST_LONG_TIMEOUT_MS = 900_000; // 15 minutes

/** Default for quick API reads (profiles, fiscal years, version list). */
const API_DEFAULT_TIMEOUT_MS = 60_000;

// ---------------------------------------------------------------------------
// Core fetch wrapper — attaches Bearer + handles 401
// ---------------------------------------------------------------------------

export interface ApiFetchOptions extends RequestInit {
  /** Abort the request after this many ms (default 60s). */
  timeoutMs?: number;
}

async function apiFetch(
  url: string,
  init: ApiFetchOptions = {}
): Promise<Response> {
  const { timeoutMs = API_DEFAULT_TIMEOUT_MS, ...fetchInit } = init;
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(fetchInit.headers as HeadersInit | undefined);

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  // Don't set Content-Type for FormData — browser sets it with boundary.
  if (fetchInit.body && !(fetchInit.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(url, { ...fetchInit, headers, signal: controller.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(
        `Request timed out after ${Math.round(timeoutMs / 1000)}s. Large files can take several minutes — try again or wait for the server to finish.`
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }

  if (res.status === 401) {
    // Clear stored auth and redirect to login
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    window.location.href = "/login";
    // Return the response so callers don't break, but the redirect will navigate away
    return res;
  }

  return res;
}

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

export type SignMode = "signed" | "soll_haben" | "amount_dc";
export type EntityMode = "fixed" | "column";
export type FiscalYearMode = "fixed" | "column" | "from_date";
export type LinkingStrategy = "txn" | "gegenkonto" | "none";

/** Dialect as detected by the backend on upload. */
export interface Dialect {
  delimiter: string;
  decimal: string;
  thousands: string;
  encoding: string;
}

/** Sign-mode configuration, built in step 3 (Optionen). */
export interface SignConfig {
  mode: SignMode;
  /** column name when mode === "signed" */
  amount?: string;
  /** DATEV Soll column */
  soll?: string;
  /** DATEV Haben column */
  haben?: string;
  /** S/H flag column for amount_dc */
  dc_flag?: string;
  /** value in dc_flag that means "debit" */
  debit_value?: string;
}

/** The full GL mapping profile (built incrementally through the GL wizard). */
export interface Profile {
  entity: { mode: EntityMode; value: string };
  fiscal_year: { mode: FiscalYearMode; value: string };
  sign: SignConfig;
  decimal: string;
  thousands: string;
  date_dayfirst: boolean;
  /** source column keyed by canonical target field name */
  columns: Record<string, string>;
  linking_strategy: LinkingStrategy;
  entry_type: "actual";
  /** Source entity label → 2-char prefix, confirmed in wizard when needed */
  entity_assignments?: Record<string, string>;
}

/** The account mapping profile (Konten-Mapping wizard). */
export interface AccountMappingProfile {
  entity: { mode: EntityMode; value: string };
  fiscal_year: { mode: FiscalYearMode; value: string };
  /** source column keyed by canonical account target field name */
  columns: Record<string, string>;
  source_system?: string;
}

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------

export interface UploadResponse {
  file_id: string;
  filename: string;
  /** sheet names (xlsx); empty for CSV */
  sheets: string[];
  columns: string[];
  /** up to ~5 sample rows from the file */
  sample: Record<string, unknown>[];
  dialect: Dialect;
}

export async function uploadFile(
  file: File,
  sheet?: string
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  if (sheet) form.append("sheet", sheet);

  const res = await apiFetch("/api/v1/ingest/upload", {
    method: "POST",
    body: form,
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<UploadResponse>;
}

// ---------------------------------------------------------------------------
// Mapping profiles
// ---------------------------------------------------------------------------

export interface MappingProfile {
  id: number;
  name: string;
  source_system: string;
  profile_json: Profile;
  created_at?: string;
}

export async function listProfiles(): Promise<MappingProfile[]> {
  const res = await apiFetch("/api/v1/ingest/profiles");
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<MappingProfile[]>;
}

export async function saveProfile(payload: {
  name: string;
  source_system: string;
  profile_json: Profile;
}): Promise<MappingProfile> {
  const res = await apiFetch("/api/v1/ingest/profiles", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<MappingProfile>;
}

export async function getProfile(id: number): Promise<MappingProfile> {
  const res = await apiFetch(`/api/v1/ingest/profiles/${id}`);
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<MappingProfile>;
}

// ---------------------------------------------------------------------------
// Entity prefix preview (GL upload)
// ---------------------------------------------------------------------------

export interface EntityMappingRow {
  source_label: string;
  entity_prefix: string;
  entity_name: string;
  status: "existing" | "proposed" | "confirmed";
  row_count: number;
}

export interface EntityPreviewResponse {
  mappings: EntityMappingRow[];
  needs_confirmation: boolean;
  all_resolved: boolean;
  proposed_assignments: Record<string, string>;
}

export async function previewEntityAssignments(payload: {
  file_id: string;
  sheet?: string;
  entity: Profile["entity"];
  entity_assignments?: Record<string, string>;
}): Promise<EntityPreviewResponse> {
  const res = await apiFetch("/api/v1/ingest/entity/preview", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<EntityPreviewResponse>;
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

export type CheckSeverity = "HARD" | "SOFT";

export interface CheckOffender {
  field?: string;
  issue?: string;
  journal_entry_group_number?: string;
  journal_entry_number?: string;
  fiscal_year?: number | null;
  line_count?: number;
  sum?: number;
  entity?: string;
  fiscal_period?: number;
  booking_line_id?: number | null;
  gl_account_id?: string | null;
  account_number_group?: string | null;
  amount?: number | null;
  posting_date?: string | null;
  source_line_number?: number;
  source_row?: Record<string, string | number | boolean | null>;
  [key: string]: unknown;
}

export interface CheckResult {
  id: string;
  name: string;
  severity: CheckSeverity;
  passed: boolean;
  detail: string;
  /** numeric diff for reconciliation checks (R1–R4) */
  diff?: number;
  /** sample offending rows/groups with field-level detail */
  offenders?: CheckOffender[];
  /** total issue count when offenders is only a sample */
  offender_count?: number | null;
  /** S1: grouped issues by field (one row per problem type in the UI) */
  issue_groups?: S1IssueGroup[];
}

export interface S1IssueGroup {
  field: string;
  field_label?: string;
  count: number;
  issue: "empty" | "column_missing" | string;
  sample?: CheckOffender;
}

export interface ValidationSummary {
  passed: boolean;
  blocking: string[];
  warnings: string[];
}

export interface ExclusionSuggestion {
  count: number;
  reason: string;
  line_ids: number[];
}

export interface ExclusionsInfo {
  active_count: number;
  active_line_ids: number[];
  suggested: ExclusionSuggestion | null;
}

export interface ValidationResponse {
  summary: ValidationSummary;
  results: CheckResult[];
  key_preview: Record<string, unknown>[];
  unmapped_accounts: string[];
  exclusions: ExclusionsInfo;
}

export interface ValidateRequest {
  file_id: string;
  sheet?: string;
  profile: Profile;
  exclude_line_ids?: number[];
}

export async function validateIngest(
  payload: ValidateRequest
): Promise<ValidationResponse> {
  const res = await apiFetch("/api/v1/ingest/validate", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<ValidationResponse>;
}

export interface IssueRowsStats {
  row_count: number;
  amount_sum: number | null;
  fiscal_years: number[];
}

export interface IssueRowsResponse {
  stats: IssueRowsStats;
  columns: string[];
  rows: Record<string, string | number | boolean | null>[];
  total: number;
}

export interface IssueRowsRequest {
  file_id: string;
  sheet?: string;
  profile: Profile;
  exclude_line_ids?: number[];
  check_id?: "S1";
  field: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export async function fetchIssueRows(
  payload: IssueRowsRequest
): Promise<IssueRowsResponse> {
  const res = await apiFetch("/api/v1/ingest/validate/issue-rows", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<IssueRowsResponse>;
}

// ---------------------------------------------------------------------------
// GL Commit
// ---------------------------------------------------------------------------

export type CommitMode = "replace" | "append";

export interface CommitResponse {
  load_id: number | null;
  entries: number;
  lines: number;
  sales: number;
  com: number;
  ar: number;
  ap: number;
  skipped: number;
  loaded_at: string;
  commit_mode: CommitMode;
}

export interface CommitRequest {
  file_id: string;
  sheet?: string;
  profile: Profile;
  dataset: "gl";
  confirm_soft: boolean;
  exclude_line_ids?: number[];
  commit_mode?: CommitMode;
}

export async function commitIngest(
  payload: CommitRequest
): Promise<CommitResponse> {
  const res = await apiFetch("/api/v1/ingest/commit", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<CommitResponse>;
}

// ---------------------------------------------------------------------------
// Account Mapping Commit (Konten-Mapping)
// ---------------------------------------------------------------------------

export type BsPlReplaceMode = "replace" | "append";

export interface MappingCommitResponse {
  load_id: number | null;
  accounts: number;
  na: number;
  cf: number;
  deleted?: number;
  skipped?: number;
  loaded_at: string;
  commit_mode: BsPlReplaceMode;
}

export interface MappingCommitRequest {
  file_id: string;
  sheet?: string;
  profile?: AccountMappingProfile;
  format?: "generic" | "bs_pl_master";
  fiscal_years?: number[];
  replace_mode?: BsPlReplaceMode;
}

export interface MappingInsertDetail {
  account_number_group: string;
  fiscal_year: number;
  gl_account_id: string;
  account_name: string;
  level_0: string;
  level_1: string;
  level_2?: string;
}

export interface MappingDuplicateDetail {
  account_number_group: string;
  fiscal_year: number;
  gl_account_id: string;
  account_name: string;
  level_0?: string;
  level_1?: string;
  level_2?: string;
  level_3?: string;
  /** Present on aggregated duplicate_keys entries (blocker count). */
  occurrences?: number;
  level_0_values?: string[];
}

export interface MappingPreviewResponse {
  row_count_total: number;
  row_count_bs: number;
  row_count_pl: number;
  fiscal_years: number[];
  entity_prefixes: string[];
  entities: Array<{
    legal_entity_code: string;
    entity_name: string;
    entity_prefix: string;
  }>;
  replace_mode?: BsPlReplaceMode;
  would_update: number;
  would_insert: number;
  would_skip?: number;
  would_delete?: number;
  duplicate_keys: MappingDuplicateDetail[];
  duplicate_details: MappingDuplicateDetail[];
  insert_details: MappingInsertDetail[];
  delete_details?: MappingInsertDetail[];
  sample_rows: Array<Record<string, unknown>>;
  skipped_entities: string[];
  skipped_rows: number;
  warnings: string[];
  blockers: string[];
}

export interface FiscalYearsResponse {
  fiscal_years: number[];
}

export async function getIngestFiscalYears(): Promise<FiscalYearsResponse> {
  const res = await apiFetch("/api/v1/ingest/fiscal-years");
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<FiscalYearsResponse>;
}

export async function previewAccountMapping(payload: {
  file_id: string;
  format: "bs_pl_master";
  fiscal_years: number[];
  replace_mode?: BsPlReplaceMode;
}): Promise<MappingPreviewResponse> {
  const res = await apiFetch("/api/v1/ingest/mapping/preview", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<MappingPreviewResponse>;
}

export async function commitAccountMapping(
  payload: MappingCommitRequest
): Promise<MappingCommitResponse> {
  const res = await apiFetch("/api/v1/ingest/mapping/commit", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<MappingCommitResponse>;
}

// ---------------------------------------------------------------------------
// Version history + restore
// ---------------------------------------------------------------------------

export interface VersionRecord {
  load_id: number;
  dataset: string;
  legal_entity_code: string | null;
  fiscal_year: number | null;
  row_count: number | null;
  content_hash: string | null;
  loaded_at: string;
  loaded_by: string | null;
  scope_entity_prefixes: string[];
  scope_fiscal_years: number[];
  commit_mode: CommitMode | BsPlReplaceMode | null;
  snapshot_captured: boolean;
  restored_from_load_id: number | null;
}

export interface VersionDetail extends VersionRecord {
  snapshot_counts: Record<string, number>;
}

export interface RestoreVersionResponse {
  load_id: number;
  restored_from_load_id: number;
  dataset: string;
  scope_entity_prefixes: string[];
  scope_fiscal_years: number[];
  snapshot_counts: Record<string, number>;
}

export async function listVersions(limit = 50): Promise<VersionRecord[]> {
  const res = await apiFetch(`/api/v1/ingest/versions?limit=${limit}`, {
    timeoutMs: 12_000,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<VersionRecord[]>;
}

export async function getVersion(loadId: number): Promise<VersionDetail> {
  const res = await apiFetch(`/api/v1/ingest/versions/${loadId}`);
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<VersionDetail>;
}

export async function restoreVersion(
  loadId: number,
  confirm = true
): Promise<RestoreVersionResponse> {
  const res = await apiFetch(`/api/v1/ingest/versions/${loadId}/restore`, {
    method: "POST",
    body: JSON.stringify({ confirm }),
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<RestoreVersionResponse>;
}

// ---------------------------------------------------------------------------
// Plan / Forecast
// ---------------------------------------------------------------------------

export interface PlanGenerateRequest {
  base_fy: number;
  current_fy: number;
  last_closed_period: number;
  horizon_years: number;
  growth_rate: number;
  forecast_growth_rate: number;
  /** Optional: per-group growth overrides keyed by P&L group label */
  group_growth?: Record<string, number> | null;
  /** Optional: column name in GL actuals used for group_growth lookup */
  group_col?: string | null;
}

export interface PlanGenerateResponse {
  gl_plan: number;
  sales_plan: number;
  scenarios: string[];
  message: string;
}

export async function generatePlan(
  payload: PlanGenerateRequest
): Promise<PlanGenerateResponse> {
  const res = await apiFetch("/api/v1/plan/generate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<PlanGenerateResponse>;
}

export interface PlanSummaryRow {
  table: string;
  scenario: string;
  fiscal_year: number;
  row_count: number;
}

export interface PlanSummaryResponse {
  rows: PlanSummaryRow[];
}

export async function getPlanSummary(): Promise<PlanSummaryResponse> {
  const res = await apiFetch("/api/v1/plan/summary");
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<PlanSummaryResponse>;
}

// ---------------------------------------------------------------------------
// Income statement (P&L) — Phase C2
// ---------------------------------------------------------------------------

/** One column header (e.g. key="FY2025", label="FY 2025"). */
export interface StatementColumn {
  key: string;
  label: string;
}

/** One (line × column) cell value. */
export interface StatementCell {
  column_key: string;
  value: number | null;
}

/**
 * One row of a statement.
 *   row_type: "mapping"  — GL sum for matching accounts
 *             "subtotal" — running section sum
 *             "calc"     — derived metric (GROSS_PROFIT, GROSS_MARGIN_PCT, EBITDA, …)
 *   is_bold:  presentation hint
 *   kpi_code: e.g. "GROSS_PROFIT", "GROSS_MARGIN_PCT", "EBITDA" (null for mapping rows)
 *   level_2/3/4: structure hierarchy filters — used by the bookings drill endpoint.
 */
export interface StatementLine {
  line_code: string;
  label: string;
  row_type: "mapping" | "subtotal" | "calc";
  is_bold: boolean;
  kpi_code: string | null;
  cells: StatementCell[];
  level_2: string | null;
  level_3: string | null;
  level_4: string | null;
}

/** Full P&L response from GET /api/v1/statements/pl */
export interface PlResponse {
  view_mode: string;
  /** Fraction of current FY closed (0..1) */
  coverage: number;
  columns: StatementColumn[];
  lines: StatementLine[];
  /** Σ presented over GL rows that matched no mapping line, per column key */
  unmapped_total: Record<string, number>;
}

export interface GetIncomeStatementParams {
  view_mode?: "year" | "month" | "week";
  current_fy: number;
  last_closed_period: number;
  entity?: string;
  scenario?: string;
  fy_start_month?: number;
}

export async function getIncomeStatement(
  params: GetIncomeStatementParams
): Promise<PlResponse> {
  const qs = new URLSearchParams();
  qs.set("view_mode", params.view_mode ?? "year");
  qs.set("current_fy", String(params.current_fy));
  qs.set("last_closed_period", String(params.last_closed_period));
  if (params.entity) qs.set("entity", params.entity);
  if (params.scenario) qs.set("scenario", params.scenario);
  if (params.fy_start_month != null)
    qs.set("fy_start_month", String(params.fy_start_month));

  const res = await apiFetch(`/api/v1/statements/pl?${qs.toString()}`);
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<PlResponse>;
}

// ---------------------------------------------------------------------------
// Balance sheet — Phase C3
// ---------------------------------------------------------------------------

/** Full Balance Sheet response from GET /api/v1/statements/bs */
export interface BsResponse {
  view_mode: string;
  /** Fraction of current FY closed (0..1) */
  coverage: number;
  columns: StatementColumn[];
  lines: StatementLine[];
  /** Cumulative Σ over unmapped BS accounts, per column key */
  unmapped_total: Record<string, number>;
  /**
   * Assets − (Liabilities + Equity) per column key.
   * 0.0 when the accounting identity ties out.
   * Non-zero is informational — mid-stream cutoffs legitimately don't balance.
   */
  imbalance: Record<string, number>;
}

/** Shared query params shape reused by BS/WC/CF (same as P&L). */
export interface StatementParams {
  view_mode?: "year" | "month" | "week";
  current_fy: number;
  last_closed_period: number;
  entity?: string;
  scenario?: string;
  fy_start_month?: number;
}

function buildStatementQs(params: StatementParams): URLSearchParams {
  const qs = new URLSearchParams();
  qs.set("view_mode", params.view_mode ?? "year");
  qs.set("current_fy", String(params.current_fy));
  qs.set("last_closed_period", String(params.last_closed_period));
  if (params.entity) qs.set("entity", params.entity);
  if (params.scenario) qs.set("scenario", params.scenario);
  if (params.fy_start_month != null)
    qs.set("fy_start_month", String(params.fy_start_month));
  return qs;
}

export async function getBalanceSheet(
  params: StatementParams
): Promise<BsResponse> {
  const res = await apiFetch(
    `/api/v1/statements/bs?${buildStatementQs(params).toString()}`
  );
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BsResponse>;
}

// ---------------------------------------------------------------------------
// Working capital — Phase C4
// ---------------------------------------------------------------------------

/**
 * Full Working Capital response from GET /api/v1/statements/wc.
 *
 * Lines include:
 *   - NWC currency rows (row_type "mapping" / "subtotal" / "calc")
 *   - DSO / DIO / DPO / CCC ratio rows (kpi_code == "DSO" etc.; value in days, may be null)
 */
export interface WcResponse {
  view_mode: string;
  /** Fraction of current FY closed (0..1) */
  coverage: number;
  columns: StatementColumn[];
  lines: StatementLine[];
  /**
   * Days used to annualize the ratios per column key.
   * FY = 365; YTD = L × 365/12 (L = closed periods).
   */
  days_in_period: Record<string, number>;
}

export async function getWorkingCapital(
  params: StatementParams
): Promise<WcResponse> {
  const res = await apiFetch(
    `/api/v1/statements/wc?${buildStatementQs(params).toString()}`
  );
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<WcResponse>;
}

// ---------------------------------------------------------------------------
// Cash flow (indirect) — Phase C5
// ---------------------------------------------------------------------------

/**
 * Full Cash Flow response from GET /api/v1/statements/cf.
 *
 * Sections: CFO / CFI / CFF.
 * Stub lines (D&A, CFI, CFF) have value 0 — render normally.
 */
export interface CfResponse {
  view_mode: string;
  /** Fraction of current FY closed (0..1) */
  coverage: number;
  columns: StatementColumn[];
  lines: StatementLine[];
  /**
   * (CFO + CFI + CFF) − Δ Cash per column key.
   * 0.0 when the cash flow ties out.
   * Non-zero is informational — unclosed-earnings gap.
   */
  tieout_residual: Record<string, number>;
}

export async function getCashFlow(
  params: StatementParams
): Promise<CfResponse> {
  const res = await apiFetch(
    `/api/v1/statements/cf?${buildStatementQs(params).toString()}`
  );
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<CfResponse>;
}

// ---------------------------------------------------------------------------
// Consolidation — Phase S2 (entity breakdown)
// ---------------------------------------------------------------------------

/** One entity's block inside a ConsolidationResponse. */
export interface EntityBlock {
  entity_prefix: string;
  entity_name: string;
  lines: StatementLine[];
}

/** Response from GET /api/v1/statements/{kind}/consolidation */
export interface ConsolidationResponse {
  kind: string;
  view_mode: string;
  coverage: number;
  columns: StatementColumn[];
  entities: EntityBlock[];
  /** Σ across entities per line — gross, no IC elimination. */
  consolidated: StatementLine[];
  intercompany_eliminated: boolean;
}

export interface GetConsolidationParams extends StatementParams {
  kind: "pl" | "bs" | "wc" | "cf";
}

export async function getConsolidation(
  params: GetConsolidationParams
): Promise<ConsolidationResponse> {
  const qs = buildStatementQs(params);
  const res = await apiFetch(
    `/api/v1/statements/${params.kind}/consolidation?${qs.toString()}`
  );
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<ConsolidationResponse>;
}

// ---------------------------------------------------------------------------
// Bookings drill — cell → GL lines (Phase S2)
// ---------------------------------------------------------------------------

/** One GL booking line returned by the drill endpoint. */
export interface BookingRow {
  booking_line_id: number;
  journal_entry_group_number: string;
  fiscal_year: number;
  fiscal_period: number;
  line_number: number;
  account_number_group: string;
  account_name: string | null;
  level_2: string | null;
  level_3: string | null;
  level_4: string | null;
  posting_date: string | null;
  document_date: string | null;
  document_type_code: string | null;
  reference_document_number: string | null;
  line_note: string | null;
  entity_prefix: string | null;
  amount: number;
  presented_amount: number;
}

/** Response from GET /api/v1/statements/bookings */
export interface BookingsResponse {
  kind: string;
  column_key: string;
  total_count: number;
  /** Σ presented_amount over full set — reconciles to the clicked cell value. */
  total: number;
  limit: number;
  offset: number;
  rows: BookingRow[];
}

export interface GetBookingsParams extends StatementParams {
  kind: "pl" | "bs";
  col: string;
  level_2?: string | null;
  level_3?: string | null;
  level_4?: string | null;
  account_number_group?: string | null;
  bs_side?: "asset" | "credit";
  limit?: number;
  offset?: number;
}

export async function getBookings(
  params: GetBookingsParams
): Promise<BookingsResponse> {
  const qs = buildStatementQs(params);
  qs.set("kind", params.kind);
  qs.set("col", params.col);
  if (params.level_2) qs.set("level_2", params.level_2);
  if (params.level_3) qs.set("level_3", params.level_3);
  if (params.level_4) qs.set("level_4", params.level_4);
  if (params.account_number_group)
    qs.set("account_number_group", params.account_number_group);
  if (params.bs_side) qs.set("bs_side", params.bs_side);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));

  const res = await apiFetch(`/api/v1/statements/bookings?${qs.toString()}`);
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BookingsResponse>;
}

// ---------------------------------------------------------------------------
// Error helper
// ---------------------------------------------------------------------------

export function formatApiErrorMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") {
      if (status === 404 && detail === "Not Found") {
        return "API endpoint not found — restart the GDPdU backend (port 8008) and reload this page.";
      }
      return detail;
    }
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          typeof item === "object" && item && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : String(item)
        )
        .join("; ");
    }
  }
  if (typeof body === "string" && body.trim()) return body;
  return `API ${status}`;
}

class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown
  ) {
    super(formatApiErrorMessage(status, body));
    this.name = "ApiError";
  }
}

async function throwApiError(res: Response): Promise<never> {
  const text = await res.text();
  let body: unknown = text;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  throw new ApiError(res.status, body);
}
