"""Validation check catalog (P1c).

Each check is a pure function returning a CheckResult. Severity:
  HARD = blocks commit · SOFT = warning (override possible).

Tolerances (decided): absolute 0.01, relative 0.1 % (whichever is larger) for
balance/reconciliation checks (covers VAT rounding).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

ABS_TOL = 0.01
REL_TOL = 0.001


@dataclass
class CheckResult:
    id: str
    name: str
    severity: str  # 'HARD' | 'SOFT'
    passed: bool
    detail: str = ""
    diff: float | None = None
    offenders: list = field(default_factory=list)
    offender_count: int | None = None  # total issues; offenders may be a sample only
    issue_groups: list = field(default_factory=list)  # S1: one entry per failing field

    @property
    def blocking(self) -> bool:
        return self.severity == "HARD" and not self.passed


def within_tolerance(a: float, b: float, abs_tol: float = ABS_TOL, rel_tol: float = REL_TOL) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))


# --------------------------------------------------------------------------- #
# Stage-aware check selection
# --------------------------------------------------------------------------- #
#: Which checks make sense at each ingestion stage.
#:
#: At GL Project-Setup no Chart of Accounts mapping and no partner tables exist
#: yet, so mapping-coverage (M1) and reconciliation (R1-R4) are meaningless there.
#:
#: NOTE: the "all" list order MUST match the exact order in which
#: ``backend/app/routers/ingest.py`` validate() appends results, so the default
#: response stays byte-stable. Verified append order (validate ~line 1329):
#:   S1, S2, B1, B3, B2, Q2, R1, R2, R3, R4, M1  (M1 appended last).
STAGE_CHECKS: dict[str, list[str]] = {
    "gl":      ["S1", "S2", "B1", "B3", "B2", "Q2"],
    "coa":     ["M1", "R3", "R4"],
    "partner": ["R1", "R2"],
    "all":     ["S1", "S2", "B1", "B3", "B2", "Q2", "R1", "R2", "R3", "R4", "M1"],
}


def checks_for_stage(stage: str | None) -> list[str]:
    """Return the check ids relevant for ``stage`` (None/unknown => 'all')."""
    return STAGE_CHECKS.get(stage or "all", STAGE_CHECKS["all"])


# --------------------------------------------------------------------------- #
# Structural
# --------------------------------------------------------------------------- #
_FIELD_LABELS: dict[str, str] = {
    "journal_entry_group_number": "Booking ID",
    "fiscal_year": "Fiscal year",
    "line_number": "Line number",
    "booking_line_id": "Row number",
    "account_number_group": "Account key",
    "amount": "Amount",
    "posting_date": "Posting date",
}


def _is_empty_series(s: pd.Series) -> pd.Series:
    empty = s.isna()
    if str(s.dtype) in ("string", "object"):
        empty |= s.astype("string").str.strip().eq("") | s.astype("string").str.strip().eq("<NA>")
    return empty


def _sample_row_offender(df: pd.DataFrame, idx, field: str) -> dict:
    row: dict = {"field": field}
    for col in (
        "booking_line_id",
        "journal_entry_group_number",
        "gl_account_id",
        "account_number_group",
        "amount",
        "posting_date",
        "fiscal_year",
    ):
        if col not in df.columns:
            continue
        val = df.at[idx, col]
        if pd.isna(val):
            row[col] = None
        elif hasattr(val, "item"):
            row[col] = val.item()
        elif hasattr(val, "isoformat"):
            row[col] = str(val.date()) if hasattr(val, "date") else str(val)
        else:
            row[col] = val
    return row


def s1_empty_mask(df: pd.DataFrame, field: str) -> pd.Series:
    """Rows failing S1 for a single required field (same rules as check_required_fields)."""
    if field not in df.columns:
        return pd.Series(False, index=df.index)
    exempt = _opening_exempt_mask(df)
    scope = df.loc[~exempt] if field == "amount" and exempt.any() else df
    empty = _is_empty_series(scope[field])
    mask = pd.Series(False, index=df.index)
    mask.loc[scope.index[empty]] = True
    return mask


def check_required_fields(df: pd.DataFrame, required: list[str]) -> CheckResult:
    """S1: required columns exist; non-opening rows have non-null values."""
    exempt = _opening_exempt_mask(df)
    issues: list[str] = []
    offenders: list[dict] = []
    issue_groups: list[dict] = []
    total_bad_rows = 0

    for col in required:
        if col not in df.columns:
            label = _FIELD_LABELS.get(col, col)
            issues.append(f"{label} column is missing")
            offenders.append({"field": col, "field_label": label, "issue": "column_missing"})
            issue_groups.append({
                "field": col,
                "field_label": label,
                "count": 1,
                "issue": "column_missing",
            })
            total_bad_rows += 1
            continue

        scope = df.loc[~exempt] if col == "amount" and exempt.any() else df
        empty = _is_empty_series(scope[col])
        n_bad = int(empty.sum())
        if not n_bad:
            continue

        label = _FIELD_LABELS.get(col, col)
        total_bad_rows += n_bad
        if col == "account_number_group":
            issues.append(
                f"{n_bad:,} row(s) without account key "
                "(entity prefix + account number — source account may be empty)"
            )
        elif col == "amount":
            issues.append(f"{n_bad:,} row(s) without amount")
        else:
            issues.append(f"{n_bad:,} row(s) without {label.lower()}")
        sample = None
        for idx in scope.index[empty][:5]:
            row = _sample_row_offender(df, idx, col)
            row["field_label"] = label
            offenders.append(row)
            if sample is None:
                sample = row
        issue_groups.append({
            "field": col,
            "field_label": label,
            "count": n_bad,
            "issue": "empty",
            "sample": sample,
        })

    return CheckResult(
        "S1", "Required fields filled", "HARD",
        passed=not issues,
        detail="All required fields are filled." if not issues else " ".join(issues),
        offenders=offenders,
        offender_count=total_bad_rows if issues else None,
        issue_groups=issue_groups,
    )


_OPENING_ENTRY_TYPES = frozenset(
    {"opening", "opening_balance", "eroeffnung", "eröffnung", "eroffnung"}
)

#: entry_type for synthetic net-profit equity bookings (etl.net_profit).  Like
#: opening balances these are SINGLE-SIDED by design (one equity credit, no
#: balancing counter-line), so they are exempt from the double-entry balance
#: checks (B1/B2/B3) and the amount/posting-year structural checks (S1/S2).
_NET_PROFIT_ENTRY_TYPES = frozenset({"net_profit"})

#: All single-sided synthetic entry types exempt from per-booking / per-entity
#: balance + amount/posting-year checks.
_SINGLE_SIDED_ENTRY_TYPES = _OPENING_ENTRY_TYPES | _NET_PROFIT_ENTRY_TYPES


def _opening_exempt_mask(lines: pd.DataFrame) -> pd.Series:
    """Rows excluded from per-booking / per-entity balance checks (single-sided rows).

    Covers BOTH opening balances (``fiscal_period=0`` / ``entry_type`` opening) and
    synthetic net-profit equity bookings (``entry_type='net_profit'``).  Both are
    single-sided by design, so they must not break B1/B2/B3 or S1/S2.  The name is
    kept for backward compatibility (callers / tests reference it).
    """
    exempt = pd.Series(False, index=lines.index)
    if "fiscal_period" in lines.columns:
        fp = pd.to_numeric(lines["fiscal_period"], errors="coerce")
        exempt |= fp.fillna(-1).eq(0)
    if "entry_type" in lines.columns:
        et = lines["entry_type"].astype(str).str.strip().str.lower()
        exempt |= et.isin(_SINGLE_SIDED_ENTRY_TYPES)
    return exempt


def check_s2_posting_fiscal_year(lines: pd.DataFrame) -> CheckResult:
    """S2: posting_date parseable; fiscal_year == posting_date.year (opening exempt)."""
    name = "Posting year matches booking date"
    if "posting_date" not in lines.columns or "fiscal_year" not in lines.columns:
        return CheckResult(
            "S2", name, "HARD", passed=False,
            detail="Posting date or fiscal year column is missing.",
        )

    posting = pd.to_datetime(lines["posting_date"], errors="coerce")
    unparseable = posting.isna()
    if unparseable.any():
        n_bad = int(unparseable.sum())
        return CheckResult(
            "S2", name, "HARD", passed=False,
            detail=f"{n_bad} row(s) with unparseable posting_date",
            offenders=lines.index[unparseable].tolist()[:50],
        )

    exempt = _opening_exempt_mask(lines)

    fiscal_year = pd.to_numeric(lines["fiscal_year"], errors="coerce")
    posting_year = posting.dt.year
    mismatch = (~exempt) & fiscal_year.notna() & posting_year.notna() & (fiscal_year != posting_year)

    if mismatch.any():
        offenders: list[dict] = []
        for idx in lines.index[mismatch][:50]:
            offenders.append({
                "journal_entry_group_number": lines.at[idx, "journal_entry_group_number"],
                "fiscal_year": int(fiscal_year.at[idx]),
                "posting_date": str(posting.at[idx].date()),
                "posting_year": int(posting_year.at[idx]),
            })
        n_mis = int(mismatch.sum())
        return CheckResult(
            "S2", name, "HARD", passed=False,
            detail=f"{n_mis} row(s) where fiscal_year != posting_date.year",
            offenders=offenders,
        )

    return CheckResult("S2", "Posting year matches date", "HARD", passed=True, detail="Consistent.")


# --------------------------------------------------------------------------- #
# Balance (double-entry)
# --------------------------------------------------------------------------- #
def _booking_group_hint(sizes: pd.Series, n_bad: int) -> str:
    """Explain B1 vs B2/B3 when almost every group is a single line."""
    if sizes.empty or n_bad == 0:
        return ""
    single = int((sizes == 1).sum())
    if single / len(sizes) < 0.3:
        return ""
    return (
        f" Hint: {single:,} of {len(sizes):,} booking groups have only 1 line — "
        "map Journal Entry Number to Transaction number (GoBD), not document/row id. "
        "Entity totals (B2/B3) can still balance while every line fails B1."
    )


def check_booking_balance(lines: pd.DataFrame, tol: float = ABS_TOL) -> CheckResult:
    """B1: sum(amount) per (journal_entry_group_number, fiscal_year) == 0."""
    movable = lines.loc[~_opening_exempt_mask(lines)]
    keys = ["journal_entry_group_number", "fiscal_year"]
    sizes = movable.groupby(keys, dropna=False).size()
    g = movable.groupby(keys, dropna=False)["amount"].sum()
    mask = g.abs() > tol
    bad = g[mask]
    # Align line counts to `bad` by the same boolean mask (same groupby index),
    # NOT via sizes.loc[k] — a key with a <NA> level (e.g. NA fiscal_year from an
    # unparseable posting_date) cannot be looked up by .loc and would KeyError.
    bad_sizes = sizes[mask]
    hint = _booking_group_hint(sizes, len(bad))
    offenders: list[dict] = []
    for (k, v), sz in list(zip(bad.items(), bad_sizes))[:50]:
        jegn = str(k[0])
        offenders.append({
            "journal_entry_group_number": jegn,
            "journal_entry_number": jegn[2:] if len(jegn) > 2 else jegn,
            "fiscal_year": int(k[1]) if pd.notna(k[1]) else None,
            "line_count": int(sz),
            "sum": round(float(v), 2),
        })
    detail = "All bookings balance." if bad.empty else (
        f"{len(bad):,} booking(s) do not balance to zero.{hint}"
    )
    return CheckResult(
        "B1", "Each booking balances to zero", "SOFT",
        passed=bad.empty,
        detail=detail,
        offenders=offenders,
        offender_count=len(bad) if not bad.empty else None,
    )


def check_monthly_balance(lines: pd.DataFrame, tol: float = ABS_TOL) -> CheckResult:
    """B3: sum(amount) per entity × fiscal_year × month (1–12) == 0."""
    name = "Monthly movements balance per entity"
    if "fiscal_period" not in lines.columns:
        return CheckResult(
            "B3", name, "HARD", passed=False,
            detail="missing fiscal_period column",
        )

    movable = lines.loc[~_opening_exempt_mask(lines)].copy()
    fp = pd.to_numeric(movable["fiscal_period"], errors="coerce")
    in_month = movable.loc[fp.between(1, 12)].copy()
    if in_month.empty:
        return CheckResult("B3", name, "HARD", passed=True, detail="no monthly rows to check")

    in_month["_entity"] = in_month["journal_entry_group_number"].astype(str).str[:2]
    g = in_month.groupby(["_entity", "fiscal_year", "fiscal_period"])["amount"].sum()
    bad = g[g.abs() > tol]
    return CheckResult(
        "B3", name, "HARD",
        passed=bad.empty,
        detail="All months balance." if bad.empty else f"{len(bad):,} entity-month(s) out of balance",
        offenders=[
            {
                "entity": k[0],
                "fiscal_year": int(k[1]),
                "fiscal_period": int(k[2]),
                "sum": round(v, 2),
            }
            for k, v in bad.items()
        ][:50],
    )


def check_ledger_balance(lines: pd.DataFrame, tol: float = ABS_TOL) -> CheckResult:
    """B2: sum(amount) per entity (prefix) == 0 (total debits = total credits)."""
    movable = lines.loc[~_opening_exempt_mask(lines)]
    g = movable.groupby(movable["journal_entry_group_number"].str[:2])["amount"].sum()
    bad = g[g.abs() > tol]
    return CheckResult(
        "B2", "Whole ledger balances per entity", "HARD",
        passed=bad.empty,
        detail="Balanced per entity." if bad.empty else f"Out of balance: {{ {', '.join(f'{k}:{round(v,2)}' for k,v in bad.items())} }}",
        offenders=[{"entity": k, "sum": round(v, 2)} for k, v in bad.items()],
    )


# --------------------------------------------------------------------------- #
# Mapping coverage
# --------------------------------------------------------------------------- #
def check_unmapped_accounts(gl_accounts, mapping_accounts) -> CheckResult:
    """M1 (SOFT): every GL account exists in the mapping; else stub & resolve later."""
    missing = sorted(set(map(str, gl_accounts)) - set(map(str, mapping_accounts)))
    return CheckResult(
        "M1", "All accounts mapped in chart", "SOFT",
        passed=not missing,
        detail="All accounts mapped." if not missing else f"{len(missing)} account(s) not in chart — stubs will be created",
        offenders=[{"account": a} for a in missing[:50]],
        offender_count=len(missing) if missing else None,
    )


# --------------------------------------------------------------------------- #
# Reconciliation (GL <-> derived facts)
# --------------------------------------------------------------------------- #
def _recon(check_id: str, name: str, derived_total: float, gl_total: float) -> CheckResult:
    ok = within_tolerance(derived_total, gl_total)
    return CheckResult(
        check_id, name, "HARD", passed=ok,
        detail=(
            f"Matches ledger ({derived_total:,.2f} vs {gl_total:,.2f})."
            if ok
            else f"Derived total {derived_total:,.2f} vs ledger {gl_total:,.2f}."
        ),
        diff=round(derived_total - gl_total, 2),
    )


def check_r1_ar(fact_ar: pd.DataFrame, lines: pd.DataFrame) -> CheckResult:
    gl = lines.loc[lines["account_class"] == "receivable", "amount"].sum()
    return _recon("R1", "Trade receivables", fact_ar["amount"].sum(), gl)


def check_r2_ap(fact_ap: pd.DataFrame, lines: pd.DataFrame) -> CheckResult:
    gl = lines.loc[lines["account_class"] == "payable", "amount"].sum()
    return _recon("R2", "Trade payables", fact_ap["amount"].sum(), gl)


def check_r3_sales(fact_sales: pd.DataFrame, lines: pd.DataFrame) -> CheckResult:
    gl = -lines.loc[lines["account_class"] == "revenue", "amount"].sum()
    return _recon("R3", "Gross sales", fact_sales["gross_sales"].sum(), gl)


def check_r4_com(fact_com: pd.DataFrame, lines: pd.DataFrame) -> CheckResult:
    gl = lines.loc[lines["account_class"] == "material", "amount"].sum()
    return _recon("R4", "Cost of materials", fact_com["cost_of_materials"].sum(), gl)


# --------------------------------------------------------------------------- #
# Quality
# --------------------------------------------------------------------------- #
def check_unique_booking_line_id(lines: pd.DataFrame) -> CheckResult:
    dup = int(lines["booking_line_id"].duplicated().sum()) if "booking_line_id" in lines.columns else 0
    return CheckResult(
        "Q2", "Row numbers are unique", "HARD",
        passed=dup == 0,
        detail="All row numbers are unique." if dup == 0 else f"{dup:,} duplicate row number(s)",
    )


def summarize(results: list[CheckResult]) -> dict:
    """Aggregate a run: overall pass + any blocking HARD failures."""
    blocking = [r for r in results if r.blocking]
    return {
        "passed": not blocking,
        "blocking": [r.id for r in blocking],
        "warnings": [r.id for r in results if r.severity == "SOFT" and not r.passed],
        "results": results,
    }
