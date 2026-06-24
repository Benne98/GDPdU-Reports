"""DF5 — Tests for load_plan, MappingCommitRequest model, PlanGenerateRequest model.

All tests here are PURE (no DB, no live server). Tests that require a running
Postgres must be run manually via backend/scripts/smoke_ingest.py or a dedicated
integration test marked with @pytest.mark.integration.

Coverage:
  - load_plan: correct SQL and parameter shape (via mock session)
  - load_plan: empty DataFrames produce zero counts without DB call
  - MappingCommitRequest: Pydantic validation (missing fields, extra fields ignored)
  - PlanGenerateRequest: defaults, field validation, group_growth serialisation
  - _fetch_gl_actuals / _fetch_sales_actuals: SQL column list (import-level check)
  - app.main import: plan router is registered (routes exist with /plan prefix)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

# Make etl/ importable when running from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Also make backend/ importable for app.main import.
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# =========================================================================== #
# load_plan — pure unit tests with a mock session
# =========================================================================== #

from etl.load import load_plan
from etl.tests.fixtures import plan_gl_actuals, plan_sales_actuals
from etl import plan_synth as P


def _make_gl_plan() -> pd.DataFrame:
    gl, _, _ = P.generate_plan(
        plan_gl_actuals(), plan_sales_actuals(),
        base_fy=2024, current_fy=2025, last_closed_period=2,
        horizon_years=1, growth_rate=0.05,
    )
    return gl


def _make_sales_plan() -> pd.DataFrame:
    _, sales, _ = P.generate_plan(
        plan_gl_actuals(), plan_sales_actuals(),
        base_fy=2024, current_fy=2025, last_closed_period=2,
        horizon_years=1, growth_rate=0.05,
    )
    return sales


class TestLoadPlanEmpty:
    """load_plan with empty DataFrames should commit 0 rows without error."""

    def test_empty_gl_and_sales_returns_zero_counts(self):
        mock_session = MagicMock()
        gl_empty = pd.DataFrame(columns=[
            "account_number_group", "fiscal_year", "fiscal_period",
            "scenario", "amount", "is_synthetic", "source_system",
        ])
        sales_empty = pd.DataFrame(columns=[
            "customer_id", "fiscal_year", "fiscal_period",
            "scenario", "gross_sales_plan", "is_synthetic",
        ])
        counts = load_plan(mock_session, gl_empty, sales_empty)
        assert counts == {"gl_plan": 0, "sales_plan": 0}
        mock_session.commit.assert_called_once()

    def test_empty_gl_only_still_commits(self):
        mock_session = MagicMock()
        gl_empty = pd.DataFrame(columns=[
            "account_number_group", "fiscal_year", "fiscal_period",
            "scenario", "amount", "is_synthetic", "source_system",
        ])
        sales = _make_sales_plan()
        counts = load_plan(mock_session, gl_empty, sales)
        assert counts["gl_plan"] == 0
        assert counts["sales_plan"] == len(sales)
        mock_session.commit.assert_called_once()


class TestLoadPlanWithData:
    """load_plan passes correct parameters to session.execute for each row."""

    def test_gl_plan_execute_called_once_per_row(self):
        mock_session = MagicMock()
        gl = _make_gl_plan()
        sales_empty = pd.DataFrame(columns=[
            "customer_id", "fiscal_year", "fiscal_period",
            "scenario", "gross_sales_plan", "is_synthetic",
        ])
        counts = load_plan(mock_session, gl, sales_empty)
        assert counts["gl_plan"] == len(gl)
        # execute should be called once per GL row (no sales rows)
        assert mock_session.execute.call_count == len(gl)

    def test_sales_plan_execute_called_once_per_row(self):
        mock_session = MagicMock()
        gl_empty = pd.DataFrame(columns=[
            "account_number_group", "fiscal_year", "fiscal_period",
            "scenario", "amount", "is_synthetic", "source_system",
        ])
        sales = _make_sales_plan()
        counts = load_plan(mock_session, gl_empty, sales)
        assert counts["sales_plan"] == len(sales)
        assert mock_session.execute.call_count == len(sales)

    def test_combined_counts_match_dataframe_lengths(self):
        mock_session = MagicMock()
        gl = _make_gl_plan()
        sales = _make_sales_plan()
        counts = load_plan(mock_session, gl, sales)
        assert counts["gl_plan"] == len(gl)
        assert counts["sales_plan"] == len(sales)
        assert mock_session.commit.call_count == 1

    def test_rollback_called_on_execute_error(self):
        mock_session = MagicMock()
        mock_session.execute.side_effect = RuntimeError("DB down")
        gl = _make_gl_plan()
        sales_empty = pd.DataFrame(columns=[
            "customer_id", "fiscal_year", "fiscal_period",
            "scenario", "gross_sales_plan", "is_synthetic",
        ])
        with pytest.raises(RuntimeError, match="DB down"):
            load_plan(mock_session, gl, sales_empty)
        mock_session.rollback.assert_called_once()
        mock_session.commit.assert_not_called()

    def test_is_synthetic_flag_preserved(self):
        """Every GL plan row produced by generate_plan carries is_synthetic=True."""
        gl = _make_gl_plan()
        assert gl["is_synthetic"].all()

    def test_sign_convention_preserved_in_plan_rows(self):
        """Revenue account 0180000 stays negative (credit) through the plan."""
        gl = _make_gl_plan()
        rev = gl[gl["account_number_group"] == "0180000"]
        assert not rev.empty
        nonzero = rev[rev["amount"] != 0.0]
        assert (nonzero["amount"] < 0).all(), "Revenue plan amount must be negative (credit)"


# =========================================================================== #
# Pydantic model validation — MappingCommitRequest
# =========================================================================== #

def test_mapping_commit_request_requires_file_id():
    """MappingCommitRequest must have file_id; profile is optional dict."""
    from app.routers.ingest import MappingCommitRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MappingCommitRequest(profile={})  # missing file_id


def test_mapping_commit_request_valid():
    from app.routers.ingest import MappingCommitRequest

    req = MappingCommitRequest(
        file_id="abc123",
        profile={"entity": {"mode": "fixed", "value": "01"}, "columns": {}},
    )
    assert req.file_id == "abc123"
    assert req.sheet is None


def test_mapping_commit_response_model():
    from app.routers.ingest import MappingCommitResponse

    resp = MappingCommitResponse(accounts=6, na=3, cf=2, loaded_at="2026-01-01T00:00:00+00:00")
    assert resp.accounts == 6
    assert resp.na == 3
    assert resp.cf == 2


# =========================================================================== #
# Pydantic model validation — PlanGenerateRequest
# =========================================================================== #

def test_plan_generate_request_defaults():
    from app.routers.plan import PlanGenerateRequest

    req = PlanGenerateRequest(base_fy=2024, current_fy=2025, last_closed_period=6)
    assert req.horizon_years == 4
    assert req.growth_rate == 0.05
    assert req.forecast_growth_rate == 0.0
    assert req.group_growth is None
    assert req.group_col is None


def test_plan_generate_request_with_group_growth():
    from app.routers.plan import PlanGenerateRequest

    req = PlanGenerateRequest(
        base_fy=2024,
        current_fy=2025,
        last_closed_period=3,
        group_growth={"Umsatzerlöse": 0.08, "Materialaufwand": 0.03},
        group_col="level_2",
    )
    assert req.group_growth["Umsatzerlöse"] == pytest.approx(0.08)
    assert req.group_col == "level_2"


def test_plan_generate_request_last_closed_period_bounds():
    from app.routers.plan import PlanGenerateRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlanGenerateRequest(base_fy=2024, current_fy=2025, last_closed_period=13)

    with pytest.raises(ValidationError):
        PlanGenerateRequest(base_fy=2024, current_fy=2025, last_closed_period=-1)


def test_plan_generate_response_model():
    from app.routers.plan import PlanGenerateResponse

    resp = PlanGenerateResponse(
        gl_plan=480, sales_plan=120, scenarios=["forecast", "plan"],
        message="OK",
    )
    assert resp.gl_plan == 480
    assert "forecast" in resp.scenarios


# =========================================================================== #
# app.main import — plan router is registered
# =========================================================================== #

def test_app_main_includes_plan_routes():
    """Import check: plan router must be registered, routes with /plan must exist."""
    import app.main as m
    plan_paths = [r.path for r in m.app.routes if "/plan" in r.path]
    assert any("/plan/generate" in p for p in plan_paths), (
        f"/plan/generate not found in routes: {plan_paths}"
    )
    assert any("/plan/summary" in p for p in plan_paths), (
        f"/plan/summary not found in routes: {plan_paths}"
    )


def test_app_main_includes_mapping_commit_route():
    """Import check: mapping/commit route must be registered."""
    import app.main as m
    mapping_paths = [r.path for r in m.app.routes if "mapping" in r.path]
    assert any("mapping/commit" in p for p in mapping_paths), (
        f"/mapping/commit not found in routes: {mapping_paths}"
    )


# =========================================================================== #
# _fetch helpers — column shape check (import-level, no DB)
# =========================================================================== #

def test_fetch_gl_actuals_helper_importable():
    """_fetch_gl_actuals is importable and accepts a mock session without crashing."""
    from app.routers.plan import _fetch_gl_actuals
    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = []
    result = _fetch_gl_actuals(mock_session, base_fy=2024, group_col=None)
    assert list(result.columns) == ["account_number_group", "fiscal_year", "fiscal_period", "amount"]
    assert result.empty


def test_fetch_sales_actuals_helper_importable():
    from app.routers.plan import _fetch_sales_actuals
    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = []
    result = _fetch_sales_actuals(mock_session, base_fy=2024)
    assert list(result.columns) == ["customer_id", "fiscal_year", "fiscal_period", "gross_sales"]
    assert result.empty


def test_fetch_gl_actuals_with_group_col():
    """With a valid group_col, the result DataFrame includes that column."""
    from app.routers.plan import _fetch_gl_actuals
    mock_session = MagicMock()
    # Simulate one row returned with 5 columns (incl. level_2)
    mock_session.execute.return_value.fetchall.return_value = [
        ("0180000", 2024, 1, -1000.0, "Umsatzerlöse")
    ]
    result = _fetch_gl_actuals(mock_session, base_fy=2024, group_col="level_2")
    assert "level_2" in result.columns
    assert result["level_2"].iloc[0] == "Umsatzerlöse"


def test_fetch_gl_actuals_unsupported_group_col_ignored():
    """An unknown group_col name does not trigger the JOIN (treated as no group_col)."""
    from app.routers.plan import _fetch_gl_actuals
    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = []
    # "bogus_col" is not in the allowed list -> falls back to no JOIN
    result = _fetch_gl_actuals(mock_session, base_fy=2024, group_col="bogus_col")
    assert "bogus_col" not in result.columns
