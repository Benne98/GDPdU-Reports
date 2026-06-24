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

// ---------------------------------------------------------------------------
// Budget Planning — Phase 5 (manual BS/PL position planning)
// ---------------------------------------------------------------------------

/** A single partner row within a partner-driven budget position. */
export interface BudgetPartnerRow {
  partner_id: string;
  name: string;
  annual: number;
  months: number[];
}

/** One reporting position in the budget grid. */
export interface BudgetPositionRow {
  line_code: string;
  label: string;
  annual: number;
  months: number[];
  synthetic_annual: number;
  is_partner_driven: boolean;
  partners?: BudgetPartnerRow[];
  /** Read-only residual: position.annual − Σ partners.annual */
  other?: number;
}

/** Full budget grid response for one statement + fiscal year + entity scope. */
export interface BudgetGridResponse {
  statement: string;
  fiscal_year: number;
  entity: string;
  top_n: number;
  positions: BudgetPositionRow[];
}

/** Write-operation response (seed / cell / position / delete). */
export interface BudgetWriteResponse {
  ok: boolean;
  rows_upserted: number;
  rows_seeded: number;
  deleted: number;
}

/** Body for PUT /api/v1/budget/cell */
export interface BudgetCellRequest {
  statement: string;
  line_code: string;
  entity?: string;
  partner_id?: string;
  partner_kind?: string;
  fiscal_year: number;
  months?: number[];
  annual?: number;
  weights?: Record<number, number>;
  /** L4 sub-position key; omit or '' for the L3-level row. */
  level_4?: string;
}

/** Body for PATCH /api/v1/budget/position */
export interface BudgetPositionPatchRequest {
  statement: string;
  line_code: string;
  entity?: string;
  fiscal_year: number;
  position?: { months?: number[]; annual?: number };
  partners?: Array<{ partner_id: string; months?: number[]; annual?: number }>;
  weights?: Record<number, number>;
  /** L4 sub-position key; omit or '' for the L3-level row. */
  level_4?: string;
}

/** Body for POST /api/v1/budget/seed */
export interface BudgetSeedRequest {
  statement: string;
  fiscal_year: number;
  entity?: string;
  top_n?: number;
  /** When true, writes each position's heuristic suggestion instead of the legacy synthetic. */
  materialize_suggestion?: boolean;
  heuristic?: "prior_year" | "trend_cagr" | "run_rate";
  growth_pct?: number;
}

/**
 * GET /api/v1/budget — editable grid (pure-read).
 * Seeded with synthetic values where no manual budget exists.
 */
export async function getBudget(
  statement: "PL" | "BS",
  fiscal_year: number,
  entity?: string,
  top_n?: number,
): Promise<BudgetGridResponse> {
  const qs = new URLSearchParams();
  qs.set("statement", statement);
  qs.set("fiscal_year", String(fiscal_year));
  if (entity && entity !== "all") qs.set("entity", entity);
  if (top_n != null) qs.set("top_n", String(top_n));
  const res = await apiFetch(`/api/v1/budget?${qs.toString()}`);
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BudgetGridResponse>;
}

/**
 * POST /api/v1/budget/seed — materialise synthetic seed (idempotent, admin only).
 */
export async function seedBudget(
  payload: BudgetSeedRequest,
): Promise<BudgetWriteResponse> {
  const res = await apiFetch("/api/v1/budget/seed", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BudgetWriteResponse>;
}

/**
 * PUT /api/v1/budget/cell — save one cell; annual is seasonalized server-side (admin only).
 */
export async function putBudgetCell(
  payload: BudgetCellRequest,
): Promise<BudgetWriteResponse> {
  const res = await apiFetch("/api/v1/budget/cell", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BudgetWriteResponse>;
}

/**
 * PATCH /api/v1/budget/position — bulk-save position + partners in one transaction (admin only).
 */
export async function patchBudgetPosition(
  payload: BudgetPositionPatchRequest,
): Promise<BudgetWriteResponse> {
  const res = await apiFetch("/api/v1/budget/position", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BudgetWriteResponse>;
}

/**
 * DELETE /api/v1/budget — drop budget rows → readers revert to forecast/plan (admin only).
 */
export async function deleteBudget(
  statement: "PL" | "BS",
  fiscal_year: number,
  entity?: string,
): Promise<BudgetWriteResponse> {
  const qs = new URLSearchParams();
  qs.set("statement", statement);
  qs.set("fiscal_year", String(fiscal_year));
  if (entity && entity !== "all") qs.set("entity", entity);
  const res = await apiFetch(`/api/v1/budget?${qs.toString()}`, {
    method: "DELETE",
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BudgetWriteResponse>;
}

// ---------------------------------------------------------------------------
// Budget Planning — Phase 5 extended types and functions
// ---------------------------------------------------------------------------

/** L4 child row within a budget position. */
export interface BudgetChild {
  level_4: string;
  label: string;
  annual: number;
  months: number[];
}

/**
 * Full budget position for the tree grid (Phase 5).
 * Extends the simpler BudgetPositionRow with suggestion, explanation, children, and level_3.
 */
export interface BudgetPosition {
  line_code: string;
  label: string;
  level_3: string;
  annual: number;
  months: number[];
  synthetic_annual: number;
  is_partner_driven: boolean;
  suggestion: { annual: number; months: number[] } | null;
  explanation: Record<string, unknown> | null;
  children: BudgetChild[] | null;
  partners: BudgetPartnerRow[] | null;
  other: number | null;
}

/** Full budget tree response (Phase 5 GET /api/v1/budget). */
export interface BudgetTreeResponse {
  statement: string;
  fiscal_year: number;
  entity: string;
  top_n: number;
  level: string;
  positions: BudgetPosition[];
}

/** Entity list response. */
export interface BudgetEntitiesResponse {
  entities: Array<{ code: string; label: string; prefix: string }>;
  can_consolidate: boolean;
}

/** Upload preview response. */
export interface BudgetUploadPreview {
  file_id: string;
  statement: string;
  fiscal_year: number;
  entity: string;
  changes: Array<{
    line_code: string;
    field: string;
    old: number;
    new: number;
    level_4?: string;
    partner_id?: string;
  }>;
  unknown_line_codes: string[];
  summary: Record<string, unknown>;
}

/** Commit response. */
export interface BudgetCommitResponse {
  ok: boolean;
  rows_written: number;
}

/**
 * GET /api/v1/budget — tree grid with heuristic params (Phase 5).
 */
export async function getBudgetTree(params: {
  statement: "PL" | "BS";
  fiscal_year: number;
  entity?: string;
  level?: "L3" | "L4";
  heuristic?: "prior_year" | "trend_cagr" | "run_rate";
  growth_pct?: number;
  top_n?: number;
}): Promise<BudgetTreeResponse> {
  const qs = new URLSearchParams();
  qs.set("statement", params.statement);
  qs.set("fiscal_year", String(params.fiscal_year));
  if (params.entity && params.entity !== "") qs.set("entity", params.entity);
  if (params.level) qs.set("level", params.level);
  if (params.heuristic) qs.set("heuristic", params.heuristic);
  if (params.growth_pct != null) qs.set("growth_pct", String(params.growth_pct));
  if (params.top_n != null) qs.set("top_n", String(params.top_n));
  const res = await apiFetch(`/api/v1/budget?${qs.toString()}`);
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BudgetTreeResponse>;
}

/**
 * GET /api/v1/budget/entities — list available entities for budget planning.
 */
export async function budgetEntities(): Promise<BudgetEntitiesResponse> {
  const res = await apiFetch("/api/v1/budget/entities");
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BudgetEntitiesResponse>;
}

/**
 * POST /api/v1/budget/upload — upload xlsx, returns preview of changes.
 */
export async function budgetUpload(
  file: File,
  statement: string,
  fiscal_year: number,
  entity: string,
  top_n?: number,
): Promise<BudgetUploadPreview> {
  const form = new FormData();
  form.append("file", file);
  form.append("statement", statement);
  form.append("fiscal_year", String(fiscal_year));
  form.append("entity", entity);
  if (top_n != null) form.append("top_n", String(top_n));
  const res = await apiFetch("/api/v1/budget/upload", {
    method: "POST",
    body: form,
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BudgetUploadPreview>;
}

/**
 * POST /api/v1/budget/commit — commit an uploaded preview by file_id.
 */
export async function budgetCommit(payload: {
  file_id: string;
  statement: string;
  entity: string;
  fiscal_year: number;
}): Promise<BudgetCommitResponse> {
  const res = await apiFetch("/api/v1/budget/commit", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BudgetCommitResponse>;
}

// ---------------------------------------------------------------------------
// CoA Master Template download (Phase 3 — Project Setup Wizard)
// ---------------------------------------------------------------------------

/**
 * GET /api/v1/projects/coa-template — fetch the CoA Master Excel template
 * (Master_BS + Master_PL, L1–L4 hierarchy pre-filled from the chosen library)
 * and trigger a browser download.
 *
 * Mirrors the downloadBudgetTemplate pattern: Bearer fetch → blob → anchor click.
 *
 * Query params forwarded to the backend:
 *   entity      — legal entity code (optional)
 *   fiscal_year — fiscal year integer (optional)
 *   library     — library variant slug, e.g. "skr03" | "statutory" | "past_projects"
 */
export async function downloadCoaTemplate(params: {
  entity?: string;
  fiscalYear?: number;
  library?: string;
}): Promise<void> {
  const qs = new URLSearchParams();
  if (params.entity) qs.set("entity", params.entity);
  if (params.fiscalYear != null) qs.set("fiscal_year", String(params.fiscalYear));
  if (params.library) qs.set("library", params.library);

  const res = await apiFetch(`/api/v1/projects/coa-template?${qs.toString()}`);
  if (!res.ok) await throwApiError(res);

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename[^;=\n]*=([^;\n]*)/.exec(disposition);
  const filename =
    match?.[1]?.trim().replace(/['"]/g, "") ??
    `CoA_Master_Template${params.entity ? `_${params.entity}` : ""}${params.fiscalYear ? `_${params.fiscalYear}` : ""}.xlsx`;

  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * GET /api/v1/budget/template — fetch xlsx template with Bearer auth and trigger browser download.
 */
export async function downloadBudgetTemplate(params: {
  statement: string;
  fiscal_year: number;
  entity: string;
  level: string;
  top_n?: number;
}): Promise<void> {
  const qs = new URLSearchParams();
  qs.set("statement", params.statement);
  qs.set("fiscal_year", String(params.fiscal_year));
  if (params.entity) qs.set("entity", params.entity);
  qs.set("level", params.level);
  if (params.top_n != null) qs.set("top_n", String(params.top_n));

  const res = await apiFetch(`/api/v1/budget/template?${qs.toString()}`);
  if (!res.ok) await throwApiError(res);

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);

  // Try to extract filename from Content-Disposition, fallback to constructed name
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename[^;=\n]*=([^;\n]*)/.exec(disposition);
  const filename =
    match?.[1]?.trim().replace(/['"]/g, "") ??
    `budget_template_${params.statement}_${params.entity || "consolidated"}_${params.fiscal_year}.xlsx`;

  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Opening Balance upload (Phase 4 — Project Setup Wizard)
// ---------------------------------------------------------------------------

/**
 * POST /api/v1/ingest/opening-balance/upload
 * Uploads an opening balance file and returns parsed columns + sample rows.
 * Reuses the same UploadResponse shape as the GL upload.
 * Commit is deferred to Phase 5 Finish via /ingest/opening-balance/commit.
 */
export async function uploadOpeningBalance(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await apiFetch("/api/v1/ingest/opening-balance/upload", {
    method: "POST",
    body: form,
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<UploadResponse>;
}

// ---------------------------------------------------------------------------
// Partner Master upload (Phase 4 — Project Setup Wizard)
// ---------------------------------------------------------------------------

/**
 * POST /api/v1/ingest/partner-master/upload
 * Uploads a partner master file (customers or suppliers) and returns parsed
 * columns + sample rows.
 * Reuses the same UploadResponse shape as the GL upload.
 * Commit is deferred to Phase 5 Finish via /ingest/partner-master/commit.
 */
export async function uploadPartnerMaster(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await apiFetch("/api/v1/ingest/partner-master/upload", {
    method: "POST",
    body: form,
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<UploadResponse>;
}

// ---------------------------------------------------------------------------
// Opening Balance commit (Phase 5 — Project Setup Wizard Finish)
// ---------------------------------------------------------------------------

export interface ObCommitRequest {
  file_id: string;
  sheet?: string;
  /** Serialised GL MappingProfile dict (reused for column mapping). */
  profile: Record<string, unknown>;
  /** 'first_year' | 'all' */
  scope: "first_year" | "all";
}

export interface ObCommitResponse {
  load_id: number | null;
  entries: number;
  lines: number;
  fiscal_years: number[];
  scope: string;
  loaded_at: string;
}

/**
 * POST /api/v1/ingest/opening-balance/commit
 * Loads OB rows tagged entry_type='opening_balance', fiscal_period=0.
 * scope='first_year' keeps only the earliest FY; scope='all' keeps every year.
 */
export async function commitOpeningBalance(
  payload: ObCommitRequest
): Promise<ObCommitResponse> {
  const res = await apiFetch("/api/v1/ingest/opening-balance/commit", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<ObCommitResponse>;
}

// ---------------------------------------------------------------------------
// Partner Master commit (Phase 5 — Project Setup Wizard Finish)
// ---------------------------------------------------------------------------

/**
 * PartnerMappingProfile as expected by POST /api/v1/ingest/partner-master/commit.
 * Matches the WizardPartnerState.profile shape from ProjectSetupWizard.tsx.
 */
export interface PartnerCommitProfile {
  side: "customer" | "supplier";
  entity: { mode: "fixed" | "column"; value: string };
  join_key: { column: string };
  columns: {
    name_line_1: string;
    name_line_2?: string;
    country_code?: string;
    city?: string;
    postal_code?: string;
  };
}

export interface PartnerCommitRequest {
  file_id: string;
  sheet?: string;
  /** Serialised PartnerMappingProfile dict. */
  profile: PartnerCommitProfile;
}

export interface PartnerCommitResponse {
  side: string;
  upserted: number;
  loaded_at: string;
}

/**
 * POST /api/v1/ingest/partner-master/commit
 * Column-mapped UPSERT of a partner-master file into dim_customer / dim_supplier.
 */
export async function commitPartnerMaster(
  payload: PartnerCommitRequest
): Promise<PartnerCommitResponse> {
  const res = await apiFetch("/api/v1/ingest/partner-master/commit", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: INGEST_LONG_TIMEOUT_MS,
  });
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<PartnerCommitResponse>;
}

// ---------------------------------------------------------------------------
// Budget Granularity View — GET /api/v1/financials/budget/granularity-view
// ---------------------------------------------------------------------------

/** One period entry returned by the granularity-view endpoint. */
export interface BudgetGranularityPeriod {
  key: string;
  label: string;
}

/** One GL account row within a position or L4 child. */
export interface BudgetGranularityAccount {
  gl_account_id: string;
  label: string;
  /** EUR values aligned to periods[]. */
  values: number[];
}

/** One L4 child within a granularity-view position. */
export interface BudgetGranularityChild {
  level_4: string;
  label: string;
  /** EUR values aligned to periods[]. */
  values: number[];
  accounts: BudgetGranularityAccount[];
}

/**
 * One row in the full-structure granularity-view response.
 *
 *   kind = 'line'       — a plannable mapping row; gets granularity buttons.
 *   kind = 'subtotal'   — structural bold row (e.g. "Gross profit", "EBITDA");
 *                          shown bold, no granularity buttons.
 *   kind = 'grandtotal' — top-level structural row (e.g. "Total assets",
 *                          "Total liabilities"); bold, no granularity buttons.
 *
 * For backward compatibility the old `series` field is accepted as an alias
 * for `values` (the new canonical name).
 */
export interface BudgetGranularityRow {
  id: string;
  line_code: string;
  label: string;
  kind: "line" | "subtotal" | "grandtotal";
  /** Hierarchy depth — drives left-padding on the label cell. */
  indent: number;
  is_bold: boolean;
  /** EUR values aligned to periods[]. Divide by 1000 to display as kEUR. */
  values: number[];
  /** Only meaningful on kind='line' rows. */
  plannable?: boolean;
  is_partner_driven?: boolean;
  partner_kind?: "customer" | "supplier" | null;
  has_l4?: boolean;
  children?: BudgetGranularityChild[];
  accounts?: BudgetGranularityAccount[];
}

/**
 * @deprecated Use BudgetGranularityRow instead.
 * Kept for backward compatibility with PositionGranularityPicker internals.
 */
export interface BudgetGranularityPosition {
  line_code: string;
  label: string;
  level_3: string;
  is_partner_driven: boolean;
  partner_kind: "customer" | "supplier" | null;
  has_l4: boolean;
  /** @deprecated Use values instead. Kept for backward compat. */
  series: number[];
  values?: number[];
  children: BudgetGranularityChild[];
  accounts: BudgetGranularityAccount[];
}

/** Full response from GET /api/v1/financials/budget/granularity-view */
export interface BudgetGranularityViewResponse {
  statement: string;
  grain: "month" | "year";
  periods: BudgetGranularityPeriod[];
  /**
   * Full ordered statement structure.
   * Replaces the old flat `positions` array.
   * Contains 'line', 'subtotal', and 'grandtotal' rows in display order.
   */
  rows: BudgetGranularityRow[];
  /**
   * @deprecated Use rows instead.
   * Kept for backward compatibility during migration. May be absent on newer API responses.
   */
  positions?: BudgetGranularityPosition[];
}

/**
 * GET /api/v1/financials/budget/granularity-view
 * Returns multi-period historical series for all plannable positions in a statement.
 * Used by PositionGranularityPicker to display multi-column period context.
 *
 * @param statement  'PL' | 'BS'
 * @param grain      'month' (24 columns) | 'year' (FY23A/FY24A/FY25A/YTDJul25A)
 * @param entity     legal entity code; '' for consolidated
 */
export async function getBudgetGranularityView(params: {
  statement: "PL" | "BS";
  grain: "month" | "year";
  entity: string;
}): Promise<BudgetGranularityViewResponse> {
  const qs = new URLSearchParams();
  qs.set("statement", params.statement);
  qs.set("grain", params.grain);
  if (params.entity) qs.set("entity", params.entity);
  const res = await apiFetch(
    `/api/v1/financials/budget/granularity-view?${qs.toString()}`
  );
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<BudgetGranularityViewResponse>;
}
