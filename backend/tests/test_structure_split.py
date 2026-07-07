"""
Tests proving the v5 structure-table split (dim_pl_structure / dim_bs_structure /
dim_cf_structure) is watertight.

Each reader function is called with a per-table dispatch mock that returns a
DISTINCT fixture for each table name.  All executed SQL strings are recorded so
assertions can inspect the FROM clause without hitting a real DB.

Invariants verified:
  (1)  _load_cf_structure   queries ONLY   dim_cf_structure
  (2)  fin_compat_bs._load_structure queries ONLY dim_bs_structure
  (3)  fin_compat_pl._load_structure queries ONLY dim_pl_structure
  (4)  CF rows (CF_TEST) placed only in dim_cf_structure never appear in PL output
  (5)  PL rows (NET_SALES) placed only in dim_pl_structure never appear in CF output
  (6)  line_code sets from all three statement paths are pairwise disjoint
  (7)  _statement_structure_rows dispatches each statement to its own table
  (8)  _resolve_cf_row (line-detail lookup) uses dim_cf_structure, not dim_pl_structure
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Minimal Row stand-in  (mirrors _DictRow in test_compat_layer.py)
# ---------------------------------------------------------------------------

class _DictRow:
    """Minimal SQLAlchemy Row-like with _mapping attribute."""

    def __init__(self, d: dict):
        self._mapping = d
        for k, v in d.items():
            setattr(self, k, v)

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._mapping.values())[key]
        return self._mapping[key]

    def get(self, key, default=None):
        return self._mapping.get(key, default)


def _dr(d: dict) -> _DictRow:
    return _DictRow(d)


# ---------------------------------------------------------------------------
# Synthetic structure fixtures — one per table, zero overlap in line_codes
# ---------------------------------------------------------------------------

# Rows that live ONLY in dim_cf_structure (CF_ prefix + kpi_code "CF:detail").
_CF_STRUCTURE = [
    {
        "pl_line_id": 101, "sort_order": 2000, "line_code": "CF_OPERATING",
        "row_type": "mapping", "balance_title": "Operating CF", "details": None,
        "calc_type": None, "level_2": "Operations", "level_3": "Operating Activities",
        "level_4": None, "gl_account_id": None, "invert_delta": False,
        "is_bold": False, "kpi_code": "CF:detail",
    },
    {
        "pl_line_id": 102, "sort_order": 2010, "line_code": "CF_TEST",
        "row_type": "mapping", "balance_title": "Test CF Row", "details": None,
        "calc_type": None, "level_2": "Operations", "level_3": "Test",
        "level_4": None, "gl_account_id": None, "invert_delta": False,
        "is_bold": False, "kpi_code": "CF:detail",
    },
]
_CF_LINE_CODES = {"CF_OPERATING", "CF_TEST"}

# Rows that live ONLY in dim_bs_structure (BS_ prefix, sort_order ≥ 1000).
_BS_STRUCTURE = [
    {
        "pl_line_id": 201, "sort_order": 1000, "line_code": "BS_ASSETS_TOTAL",
        "row_type": "grandtotal", "balance_title": "Total Assets", "details": None,
        "calc_type": None, "level_2": "Assets", "level_3": None,
        "level_4": None, "gl_account_id": None, "invert_delta": False,
        "is_bold": True, "kpi_code": "BS:asset",
    },
    {
        "pl_line_id": 202, "sort_order": 1010, "line_code": "BS_CASH",
        "row_type": "mapping", "balance_title": "Cash", "details": None,
        "calc_type": None, "level_2": "Assets", "level_3": "Cash",
        "level_4": None, "gl_account_id": None, "invert_delta": False,
        "is_bold": False, "kpi_code": "BS:asset",
    },
]
_BS_LINE_CODES = {"BS_ASSETS_TOTAL", "BS_CASH"}

# Rows that live ONLY in dim_pl_structure (sort_order < 1000, no CF_/BS_ prefix).
_PL_STRUCTURE = [
    {
        "pl_line_id": 1, "sort_order": 10, "line_code": "NET_SALES",
        "row_type": "mapping", "balance_title": "Net Sales", "details": None,
        "calc_type": None, "level_2": "Income", "level_3": "Net sales",
        "level_4": None, "gl_account_id": None, "invert_delta": False,
        "is_bold": True, "kpi_code": None,
    },
    {
        "pl_line_id": 2, "sort_order": 20, "line_code": "GROSS_PROFIT",
        "row_type": "calc", "balance_title": "Gross Profit", "details": None,
        "calc_type": None, "level_2": None, "level_3": None,
        "level_4": None, "gl_account_id": None, "invert_delta": False,
        "is_bold": True, "kpi_code": None,
    },
]
_PL_LINE_CODES = {"NET_SALES", "GROSS_PROFIT"}


# ---------------------------------------------------------------------------
# Per-table dispatch mock session
# ---------------------------------------------------------------------------

def _make_split_session():
    """Return a MagicMock Session that dispatches by exact table name.

    * dim_cf_structure → CF fixture rows
    * dim_bs_structure → BS fixture rows
    * dim_pl_structure → PL fixture rows
    * anything else    → empty

    All issued SQL strings are appended to session._executed_sqls so tests can
    assert on FROM clauses without touching a live database.
    """
    executed_sqls: list[str] = []
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt) if not isinstance(stmt, str) else stmt
        executed_sqls.append(sql)
        result = MagicMock()

        if "dim_cf_structure" in sql:
            rows = [_dr(r) for r in _CF_STRUCTURE]
        elif "dim_bs_structure" in sql:
            rows = [_dr(r) for r in _BS_STRUCTURE]
        elif "dim_pl_structure" in sql:
            rows = [_dr(r) for r in _PL_STRUCTURE]
        else:
            rows = []

        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    # Attach the list so test bodies can inspect recorded SQL strings.
    session._executed_sqls = executed_sqls
    return session


def _line_codes(rows: list) -> set:
    """Extract line_code values from any mix of dicts, _DictRow, or plain objects."""
    out: set[Any] = set()
    for r in rows:
        if isinstance(r, dict):
            out.add(r.get("line_code"))
        elif hasattr(r, "_mapping"):
            out.add(r._mapping.get("line_code"))
        elif hasattr(r, "line_code"):
            out.add(r.line_code)
    return out


# ===========================================================================
# 1.  CF reader — must hit ONLY dim_cf_structure
# ===========================================================================

class TestCFReaderHitsOnlyDimCfStructure:
    """_load_cf_structure and _cf_struct_rows must query only dim_cf_structure."""

    def test_sql_contains_dim_cf_structure(self):
        """The SQL issued must reference FROM dim_cf_structure."""
        from app.services.fin_compat_cf import _load_cf_structure
        session = _make_split_session()
        _load_cf_structure(session)
        sqls = session._executed_sqls
        assert any("dim_cf_structure" in s for s in sqls), (
            "Expected a query against dim_cf_structure; got: " + repr(sqls)
        )

    def test_sql_never_touches_dim_pl_structure(self):
        """_load_cf_structure must NOT query dim_pl_structure (decision 1)."""
        from app.services.fin_compat_cf import _load_cf_structure
        session = _make_split_session()
        _load_cf_structure(session)
        assert not any("dim_pl_structure" in s for s in session._executed_sqls), (
            "_load_cf_structure queried dim_pl_structure — table split broken"
        )

    def test_sql_never_touches_dim_bs_structure(self):
        """_load_cf_structure must NOT query dim_bs_structure."""
        from app.services.fin_compat_cf import _load_cf_structure
        session = _make_split_session()
        _load_cf_structure(session)
        assert not any("dim_bs_structure" in s for s in session._executed_sqls), (
            "_load_cf_structure queried dim_bs_structure — unexpected cross-table read"
        )

    def test_returns_exactly_cf_fixture_rows(self):
        """Returned line_codes must match the CF fixture exactly."""
        from app.services.fin_compat_cf import _load_cf_structure
        session = _make_split_session()
        rows = _load_cf_structure(session)
        assert _line_codes(rows) == _CF_LINE_CODES

    def test_pl_row_net_sales_never_in_cf_reader_output(self):
        """NET_SALES (PL-only) must never appear in _load_cf_structure output."""
        from app.services.fin_compat_cf import _load_cf_structure
        session = _make_split_session()
        rows = _load_cf_structure(session)
        assert "NET_SALES" not in _line_codes(rows), (
            "NET_SALES leaked into CF structure reader"
        )

    def test_bs_row_never_in_cf_reader_output(self):
        """BS_CASH (BS-only) must never appear in _load_cf_structure output."""
        from app.services.fin_compat_cf import _load_cf_structure
        session = _make_split_session()
        rows = _load_cf_structure(session)
        assert "BS_CASH" not in _line_codes(rows), (
            "BS_CASH leaked into CF structure reader"
        )

    def test_cf_struct_rows_returns_only_cf_tagged_rows(self):
        """_cf_struct_rows must filter to CF-tagged rows and return the correct set."""
        from app.services.fin_compat_cf import _cf_struct_rows
        session = _make_split_session()
        rows = _cf_struct_rows(session)
        codes = _line_codes(rows)
        assert codes == _CF_LINE_CODES, (
            f"_cf_struct_rows returned {codes!r}, expected {_CF_LINE_CODES!r}"
        )
        assert "NET_SALES" not in codes
        assert "BS_CASH" not in codes

    def test_resolve_cf_row_queries_dim_cf_structure(self):
        """_resolve_cf_row (line-detail lookup) must SELECT from dim_cf_structure."""
        from app.services.fin_compat_cf import _resolve_cf_row
        session = _make_split_session()
        _resolve_cf_row(session, "CF_OPERATING")
        sqls = session._executed_sqls
        assert any("dim_cf_structure" in s for s in sqls), (
            "_resolve_cf_row did not query dim_cf_structure"
        )
        assert not any("dim_pl_structure" in s for s in sqls), (
            "_resolve_cf_row queried dim_pl_structure — split broken"
        )


# ===========================================================================
# 2.  BS reader — must hit ONLY dim_bs_structure
# ===========================================================================

class TestBSReaderHitsOnlyDimBsStructure:
    """fin_compat_bs._load_structure must query only dim_bs_structure."""

    def test_sql_contains_dim_bs_structure(self):
        """The SQL issued must reference FROM dim_bs_structure."""
        from app.services import fin_compat_bs
        session = _make_split_session()
        fin_compat_bs._load_structure(session)
        assert any("dim_bs_structure" in s for s in session._executed_sqls), (
            "Expected a query against dim_bs_structure; got: " + repr(session._executed_sqls)
        )

    def test_sql_never_touches_dim_pl_structure(self):
        """BS _load_structure must NOT query dim_pl_structure."""
        from app.services import fin_compat_bs
        session = _make_split_session()
        fin_compat_bs._load_structure(session)
        assert not any("dim_pl_structure" in s for s in session._executed_sqls), (
            "BS _load_structure queried dim_pl_structure — table split broken"
        )

    def test_sql_never_touches_dim_cf_structure(self):
        """BS _load_structure must NOT query dim_cf_structure."""
        from app.services import fin_compat_bs
        session = _make_split_session()
        fin_compat_bs._load_structure(session)
        assert not any("dim_cf_structure" in s for s in session._executed_sqls), (
            "BS _load_structure queried dim_cf_structure — unexpected cross-table read"
        )

    def test_returns_exactly_bs_fixture_rows(self):
        """Returned line_codes must match the BS fixture exactly."""
        from app.services import fin_compat_bs
        session = _make_split_session()
        rows = fin_compat_bs._load_structure(session)
        assert _line_codes(rows) == _BS_LINE_CODES

    def test_pl_row_never_in_bs_reader_output(self):
        """NET_SALES (PL-only) must never appear in BS _load_structure output."""
        from app.services import fin_compat_bs
        session = _make_split_session()
        rows = fin_compat_bs._load_structure(session)
        assert "NET_SALES" not in _line_codes(rows)

    def test_cf_row_never_in_bs_reader_output(self):
        """CF_TEST (CF-only) must never appear in BS _load_structure output."""
        from app.services import fin_compat_bs
        session = _make_split_session()
        rows = fin_compat_bs._load_structure(session)
        assert "CF_TEST" not in _line_codes(rows)

    def test_statement_structure_rows_bs_queries_dim_bs_structure(self):
        """_statement_structure_rows(session, 'BS') must use dim_bs_structure."""
        from app.services.fin_compat_pl import _statement_structure_rows
        session = _make_split_session()
        _statement_structure_rows(session, "BS")
        sqls = session._executed_sqls
        assert any("dim_bs_structure" in s for s in sqls), (
            "_statement_structure_rows('BS') did not query dim_bs_structure"
        )
        assert not any("dim_pl_structure" in s for s in sqls), (
            "_statement_structure_rows('BS') queried dim_pl_structure — split broken"
        )
        assert not any("dim_cf_structure" in s for s in sqls), (
            "_statement_structure_rows('BS') queried dim_cf_structure — split broken"
        )


# ===========================================================================
# 3.  PL reader — must hit ONLY dim_pl_structure + isolation invariants
# ===========================================================================

class TestPLReaderHitsOnlyDimPlStructure:
    """fin_compat_pl._load_structure must query only dim_pl_structure.

    Also verifies decision 1: CF rows placed only in dim_cf_structure can never
    leak into the Income Statement (and vice-versa for PL rows vs. CF output).
    """

    def test_sql_contains_dim_pl_structure(self):
        """The SQL issued must reference FROM dim_pl_structure."""
        from app.services.fin_compat_pl import _load_structure
        session = _make_split_session()
        _load_structure(session)
        assert any("dim_pl_structure" in s for s in session._executed_sqls), (
            "Expected a query against dim_pl_structure; got: " + repr(session._executed_sqls)
        )

    def test_sql_never_touches_dim_cf_structure(self):
        """PL _load_structure must NOT query dim_cf_structure (decision 1)."""
        from app.services.fin_compat_pl import _load_structure
        session = _make_split_session()
        _load_structure(session)
        assert not any("dim_cf_structure" in s for s in session._executed_sqls), (
            "PL _load_structure queried dim_cf_structure — table split broken"
        )

    def test_sql_never_touches_dim_bs_structure(self):
        """PL _load_structure must NOT query dim_bs_structure."""
        from app.services.fin_compat_pl import _load_structure
        session = _make_split_session()
        _load_structure(session)
        assert not any("dim_bs_structure" in s for s in session._executed_sqls), (
            "PL _load_structure queried dim_bs_structure — table split broken"
        )

    def test_returns_exactly_pl_fixture_rows(self):
        """Returned line_codes must match the PL fixture exactly."""
        from app.services.fin_compat_pl import _load_structure
        session = _make_split_session()
        rows = _load_structure(session)
        assert _line_codes(rows) == _PL_LINE_CODES

    def test_cf_row_cf_test_never_in_pl_reader_output(self):
        """Decision 1 — CF_TEST placed only in dim_cf_structure must NEVER appear in PL.

        The isolation is enforced by the table split: _load_structure queries
        dim_pl_structure which our mock populates only with PL rows; the CF
        fixture never leaks because it lives in a separate table.
        """
        from app.services.fin_compat_pl import _load_structure
        session = _make_split_session()
        rows = _load_structure(session)
        assert "CF_TEST" not in _line_codes(rows), (
            "CF_TEST leaked into PL structure — dim_pl_structure isolation broken (decision 1)"
        )

    def test_bs_row_never_in_pl_reader_output(self):
        """BS_CASH placed only in dim_bs_structure must never appear in PL output."""
        from app.services.fin_compat_pl import _load_structure
        session = _make_split_session()
        rows = _load_structure(session)
        assert "BS_CASH" not in _line_codes(rows)

    def test_statement_structure_rows_pl_queries_dim_pl_structure(self):
        """_statement_structure_rows(session, 'PL') must use dim_pl_structure only."""
        from app.services.fin_compat_pl import _statement_structure_rows
        session = _make_split_session()
        _statement_structure_rows(session, "PL")
        sqls = session._executed_sqls
        assert any("dim_pl_structure" in s for s in sqls)
        assert not any("dim_cf_structure" in s for s in sqls), (
            "_statement_structure_rows('PL') queried dim_cf_structure — split broken"
        )
        assert not any("dim_bs_structure" in s for s in sqls), (
            "_statement_structure_rows('PL') queried dim_bs_structure — split broken"
        )

    def test_statement_structure_rows_pl_result_excludes_cf_test(self):
        """_statement_structure_rows('PL') filtered result must not contain CF_TEST."""
        from app.services.fin_compat_pl import _statement_structure_rows
        session = _make_split_session()
        result = _statement_structure_rows(session, "PL")
        codes = _line_codes(result)
        assert "CF_TEST" not in codes, (
            "CF_TEST (CF-only row) appeared in PL statement rows — decision 1 violated"
        )
        assert "BS_CASH" not in codes

    def test_statement_structure_rows_cf_result_excludes_pl_row(self):
        """_statement_structure_rows('CF') must never return NET_SALES (PL-only).

        Symmetric invariant: PL rows placed only in dim_pl_structure cannot
        appear in the CF statement path because the CF path reads dim_cf_structure.
        """
        from app.services.fin_compat_pl import _statement_structure_rows
        session = _make_split_session()
        result = _statement_structure_rows(session, "CF")
        assert "NET_SALES" not in _line_codes(result), (
            "NET_SALES (PL row) leaked into CF statement rows"
        )

    def test_statement_structure_rows_cf_queries_dim_cf_structure(self):
        """_statement_structure_rows(session, 'CF') must use dim_cf_structure."""
        from app.services.fin_compat_pl import _statement_structure_rows
        session = _make_split_session()
        _statement_structure_rows(session, "CF")
        sqls = session._executed_sqls
        assert any("dim_cf_structure" in s for s in sqls), (
            "_statement_structure_rows('CF') did not query dim_cf_structure"
        )
        assert not any("dim_pl_structure" in s for s in sqls), (
            "_statement_structure_rows('CF') queried dim_pl_structure — split broken"
        )


# ===========================================================================
# 4.  End-to-end disjointness — line_code sets must be pairwise disjoint
# ===========================================================================

class TestDisjointLineCodesByStatement:
    """_statement_structure_rows must return pairwise-disjoint line_code sets."""

    def test_pl_and_cf_line_codes_are_disjoint(self):
        """PL and CF statement rows must share no line_code.

        This is the key integration assertion for decision 1: a single row cannot
        exist in both the Income Statement and the Cash Flow — they draw from
        separate tables (dim_pl_structure vs dim_cf_structure).
        """
        from app.services.fin_compat_pl import _statement_structure_rows
        session = _make_split_session()
        pl_codes = _line_codes(_statement_structure_rows(session, "PL"))
        cf_codes = _line_codes(_statement_structure_rows(session, "CF"))
        overlap = pl_codes & cf_codes
        assert not overlap, (
            f"PL and CF share line_codes — table split not watertight: {overlap}"
        )

    def test_pl_and_bs_line_codes_are_disjoint(self):
        """PL and BS statement rows must share no line_code."""
        from app.services.fin_compat_pl import _statement_structure_rows
        session = _make_split_session()
        pl_codes = _line_codes(_statement_structure_rows(session, "PL"))
        bs_codes = _line_codes(_statement_structure_rows(session, "BS"))
        overlap = pl_codes & bs_codes
        assert not overlap, (
            f"PL and BS share line_codes — table split not watertight: {overlap}"
        )

    def test_cf_and_bs_line_codes_are_disjoint(self):
        """CF and BS statement rows must share no line_code."""
        from app.services.fin_compat_pl import _statement_structure_rows
        session = _make_split_session()
        cf_codes = _line_codes(_statement_structure_rows(session, "CF"))
        bs_codes = _line_codes(_statement_structure_rows(session, "BS"))
        overlap = cf_codes & bs_codes
        assert not overlap, (
            f"CF and BS share line_codes — table split not watertight: {overlap}"
        )

    def test_all_three_statements_each_query_only_their_own_table(self):
        """Each statement path must touch exactly and only its own table.

        Uses a fresh session per statement so SQL recording is isolated.
        """
        from app.services.fin_compat_pl import _statement_structure_rows

        # PL must use dim_pl_structure only
        s_pl = _make_split_session()
        _statement_structure_rows(s_pl, "PL")
        assert any("dim_pl_structure" in s for s in s_pl._executed_sqls)
        assert not any("dim_cf_structure" in s for s in s_pl._executed_sqls)
        assert not any("dim_bs_structure" in s for s in s_pl._executed_sqls)

        # BS must use dim_bs_structure only
        s_bs = _make_split_session()
        _statement_structure_rows(s_bs, "BS")
        assert any("dim_bs_structure" in s for s in s_bs._executed_sqls)
        assert not any("dim_pl_structure" in s for s in s_bs._executed_sqls)
        assert not any("dim_cf_structure" in s for s in s_bs._executed_sqls)

        # CF must use dim_cf_structure only
        s_cf = _make_split_session()
        _statement_structure_rows(s_cf, "CF")
        assert any("dim_cf_structure" in s for s in s_cf._executed_sqls)
        assert not any("dim_pl_structure" in s for s in s_cf._executed_sqls)
        assert not any("dim_bs_structure" in s for s in s_cf._executed_sqls)

    def test_cf_test_row_absent_from_pl_and_bs_outputs(self):
        """CF_TEST placed only in dim_cf_structure must be absent from PL and BS."""
        from app.services.fin_compat_pl import _statement_structure_rows
        session = _make_split_session()
        pl_codes = _line_codes(_statement_structure_rows(session, "PL"))
        bs_codes = _line_codes(_statement_structure_rows(session, "BS"))
        assert "CF_TEST" not in pl_codes, "CF_TEST leaked into PL (decision 1)"
        assert "CF_TEST" not in bs_codes, "CF_TEST leaked into BS"

    def test_net_sales_row_absent_from_cf_and_bs_outputs(self):
        """NET_SALES placed only in dim_pl_structure must be absent from CF and BS."""
        from app.services.fin_compat_pl import _statement_structure_rows
        session = _make_split_session()
        cf_codes = _line_codes(_statement_structure_rows(session, "CF"))
        bs_codes = _line_codes(_statement_structure_rows(session, "BS"))
        assert "NET_SALES" not in cf_codes, "NET_SALES leaked into CF"
        assert "NET_SALES" not in bs_codes, "NET_SALES leaked into BS"

    def test_bs_cash_row_absent_from_pl_and_cf_outputs(self):
        """BS_CASH placed only in dim_bs_structure must be absent from PL and CF."""
        from app.services.fin_compat_pl import _statement_structure_rows
        session = _make_split_session()
        pl_codes = _line_codes(_statement_structure_rows(session, "PL"))
        cf_codes = _line_codes(_statement_structure_rows(session, "CF"))
        assert "BS_CASH" not in pl_codes, "BS_CASH leaked into PL"
        assert "BS_CASH" not in cf_codes, "BS_CASH leaked into CF"
