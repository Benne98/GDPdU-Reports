import pandas as pd

from etl import checks as C
from etl import derive as D
from etl.tests import fixtures as F


# --------------------------------------------------------------------------- structural
def test_required_fields_pass_and_fail():
    lines = F.canonical_lines()
    assert C.check_required_fields(lines, ["journal_entry_group_number", "amount"]).passed
    bad = C.check_required_fields(lines, ["does_not_exist"])
    assert not bad.passed and bad.severity == "HARD"
    assert bad.offenders[0]["issue"] == "column_missing"


def test_required_fields_reports_empty_row_count():
    lines = F.canonical_lines().copy()
    lines.loc[0, "account_number_group"] = pd.NA
    res = C.check_required_fields(lines, ["account_number_group"])
    assert not res.passed
    assert "1 row(s)" in res.detail
    assert res.offenders[0]["field"] == "account_number_group"


def test_required_fields_pass_after_excluding_all_offenders():
    """Exclusion contract: excluding every booking_line_id that fails a required
    field makes S1 pass cleanly (passed=True, no issue_groups). Locks the contract
    that the issue-rows offender exclusion relies on."""
    from etl.line_exclude import apply_line_exclusions

    lines = pd.DataFrame({
        "booking_line_id": [1, 2, 3, 4],
        "account_number_group": ["", "", "01041100", "01050000"],  # rows 1,2 bad
        "amount": [0.0, 0.0, 100.0, -100.0],
        "fiscal_year": [2020, 2020, 2020, 2020],
    })
    required = ["account_number_group"]

    before = C.check_required_fields(lines, required)
    assert not before.passed
    assert before.offender_count == 2

    after = apply_line_exclusions(lines, [1, 2])  # exclude all N bad rows
    res = C.check_required_fields(after, required)
    assert res.passed
    assert not res.issue_groups


def test_b1_failure_is_soft_and_non_blocking():
    lines = F.canonical_lines()
    lines.loc[1, "amount"] += 5.0
    res = C.check_booking_balance(lines)
    assert not res.passed
    assert res.severity == "SOFT"
    assert not res.blocking
    s = C.summarize([res])
    assert s["passed"] is True
    assert "B1" in s["warnings"]


def test_b1_handles_na_fiscal_year_without_crash():
    """Regression: a booking whose fiscal_year is NA (unparseable posting date)
    must NOT raise in B1. Previously sizes.loc[(jegn, <NA>)] threw KeyError."""
    lines = F.canonical_lines().copy()
    lines["fiscal_year"] = lines["fiscal_year"].astype("Int64")
    extra = lines.iloc[[0]].copy()
    extra["journal_entry_group_number"] = "0100unbalncd"
    extra["fiscal_year"] = pd.NA          # NA group key
    extra["amount"] = 50.0                 # single-sided → unbalanced → enters `bad`
    lines = pd.concat([lines, extra], ignore_index=True)

    res = C.check_booking_balance(lines)   # must not raise
    assert res.id == "B1"
    assert not res.passed
    assert any(o["fiscal_year"] is None for o in res.offenders)


def test_s2_passes_when_fiscal_year_matches_posting_date():
    lines = pd.DataFrame({
        "journal_entry_group_number": ["01001", "01002"],
        "fiscal_year": [2024, 2024],
        "fiscal_period": [1, 2],
        "posting_date": pd.to_datetime(["2024-01-15", "2024-02-01"]),
        "amount": [100.0, -100.0],
    })
    assert C.check_s2_posting_fiscal_year(lines).passed


def test_s2_fails_on_year_mismatch():
    lines = pd.DataFrame({
        "journal_entry_group_number": ["03001", "03002"],
        "fiscal_year": [2022, 2024],
        "fiscal_period": [6, 6],
        "posting_date": pd.to_datetime(["2024-06-01", "2024-06-01"]),
        "amount": [50.0, -50.0],
    })
    res = C.check_s2_posting_fiscal_year(lines)
    assert not res.passed and res.blocking and res.id == "S2"


def test_s2_exempt_opening_period_zero():
    lines = pd.DataFrame({
        "journal_entry_group_number": ["01001", "01002"],
        "fiscal_year": [2022, 2024],
        "fiscal_period": [0, 1],
        "posting_date": pd.to_datetime(["2024-01-01", "2024-01-15"]),
        "amount": [100.0, -100.0],
    })
    assert C.check_s2_posting_fiscal_year(lines).passed


# --------------------------------------------------------------------------- balance
def test_booking_balance_passes_on_balanced():
    assert C.check_booking_balance(F.canonical_lines()).passed


def test_booking_balance_fails_when_perturbed():
    lines = F.canonical_lines()
    lines.loc[1, "amount"] += 5.0  # break booking 1
    res = C.check_booking_balance(lines)
    assert not res.passed
    assert not res.blocking
    assert res.offenders
    assert res.offenders[0]["line_count"] >= 1


def test_booking_balance_hints_single_line_groups():
    lines = pd.DataFrame({
        "journal_entry_group_number": [f"01{i:010d}" for i in range(5)],
        "fiscal_year": [2024] * 5,
        "fiscal_period": [1] * 5,
        "posting_date": pd.to_datetime(["2024-01-15"] * 5),
        "amount": [100.0, -50.0, 25.0, -10.0, 5.0],
    })
    res = C.check_booking_balance(lines)
    assert not res.passed
    assert "Transaction number" in res.detail


def test_monthly_balance_pass_and_fail():
    lines = pd.DataFrame({
        "journal_entry_group_number": ["01001", "01002", "01003", "01004"],
        "fiscal_year": [2024, 2024, 2024, 2024],
        "fiscal_period": [1, 1, 2, 2],
        "posting_date": pd.to_datetime(["2024-01-15", "2024-01-15", "2024-02-01", "2024-02-01"]),
        "amount": [100.0, -100.0, 50.0, -50.0],
    })
    assert C.check_monthly_balance(lines).passed
    lines.loc[2, "amount"] += 3.0
    res = C.check_monthly_balance(lines)
    assert not res.passed and res.blocking and res.id == "B3"


def test_monthly_balance_exempt_opening_period():
    lines = pd.DataFrame({
        "journal_entry_group_number": ["01001", "01002", "01003"],
        "fiscal_year": [2024, 2024, 2024],
        "fiscal_period": [0, 1, 1],
        "posting_date": pd.to_datetime(["2024-01-01", "2024-01-15", "2024-01-15"]),
        "amount": [1000.0, 500.0, -500.0],
    })
    assert C.check_monthly_balance(lines).passed


def test_ledger_balance_pass_and_fail():
    lines = F.canonical_lines()
    assert C.check_ledger_balance(lines).passed
    lines.loc[0, "amount"] += 10.0
    assert not C.check_ledger_balance(lines).passed


# --------------------------------------------------------------------------- mapping coverage
def test_unmapped_accounts_soft():
    gl = ["10000", "80000", "99999"]            # 99999 not in mapping
    res = C.check_unmapped_accounts(gl, F.mapping_accounts())
    assert not res.passed
    assert res.severity == "SOFT"
    assert not res.blocking                      # SOFT never blocks
    assert res.offenders[0]["account"] == "99999"


# --------------------------------------------------------------------------- reconciliation
def test_reconciliation_passes_on_consistent_derivation():
    lines = F.canonical_lines()
    assert C.check_r1_ar(D.derive_ar(lines), lines).passed
    assert C.check_r2_ap(D.derive_ap(lines), lines).passed
    assert C.check_r3_sales(D.derive_sales(lines), lines).passed
    assert C.check_r4_com(D.derive_com(lines), lines).passed


def test_r3_fails_when_sales_drops_a_row():
    lines = F.canonical_lines()
    sales = D.derive_sales(lines).iloc[:-1]      # drop one revenue row -> mismatch
    res = C.check_r3_sales(sales, lines)
    assert not res.passed and res.blocking
    assert res.diff == -2000.0                    # derived(1000) - gl(3000): the dropped revenue


def test_reconciliation_tolerance_absorbs_rounding():
    lines = F.canonical_lines()
    com = D.derive_com(lines).copy()
    com["cost_of_materials"] += 0.004             # below 0.01 abs tolerance
    assert C.check_r4_com(com, lines).passed


# --------------------------------------------------------------------------- quality
def test_unique_booking_line_id():
    lines = F.canonical_lines()
    assert C.check_unique_booking_line_id(lines).passed
    dup = pd.concat([lines, lines.iloc[[0]]], ignore_index=True)
    assert not C.check_unique_booking_line_id(dup).passed


# --------------------------------------------------------------------------- summary
def test_summarize_only_hard_failures_block():
    lines = F.canonical_lines()
    results = [
        C.check_booking_balance(lines),                       # HARD pass
        C.check_unmapped_accounts(["99999"], F.mapping_accounts()),  # SOFT fail
    ]
    s = C.summarize(results)
    assert s["passed"] is True            # SOFT failure does not block
    assert s["warnings"] == ["M1"]
    assert s["blocking"] == []


# --------------------------------------------------------------------------- stage-aware check selection
def test_checks_for_stage_gl():
    """'gl' stage returns exactly the structural / balance / quality checks (no mapping, no recon)."""
    result = C.checks_for_stage("gl")
    # Read order directly from STAGE_CHECKS["gl"] so the assertion stays in sync.
    assert result == C.STAGE_CHECKS["gl"]
    assert result == ["S1", "S2", "B1", "B3", "B2", "Q2"]
    assert "M1" not in result
    for rid in ("R1", "R2", "R3", "R4"):
        assert rid not in result


def test_checks_for_stage_coa():
    """'coa' stage returns mapping + sales/material reconciliation checks only."""
    result = C.checks_for_stage("coa")
    assert result == C.STAGE_CHECKS["coa"]
    assert result == ["M1", "R3", "R4"]


def test_checks_for_stage_partner():
    """'partner' stage returns AR/AP reconciliation checks only."""
    result = C.checks_for_stage("partner")
    assert result == C.STAGE_CHECKS["partner"]
    assert result == ["R1", "R2"]


def test_checks_for_stage_all_and_none_return_same_full_list():
    """None and 'all' must both return the complete ordered catalog."""
    full = C.STAGE_CHECKS["all"]
    assert C.checks_for_stage("all") == full
    assert C.checks_for_stage(None) == full
    # identity: both resolve to exactly the same object
    assert C.checks_for_stage(None) == C.checks_for_stage("all")
    # Verify the full list contains all expected IDs
    assert set(full) == {"S1", "S2", "B1", "B3", "B2", "Q2", "R1", "R2", "R3", "R4", "M1"}


def test_checks_for_stage_unknown_falls_back_to_all():
    """Garbage or unknown stage values fall back silently to the full catalog."""
    full = C.STAGE_CHECKS["all"]
    assert C.checks_for_stage("garbage_stage_xyz") == full
    assert C.checks_for_stage("UNKNOWN") == full
    # Empty string is falsy; treated the same as None -> "all"
    assert C.checks_for_stage("") == full
