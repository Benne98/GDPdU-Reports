"""Phase 0 (Budget L4 storage) — XOR write logic + level_4 round-trip.

The reader (``fin_compat_sql.position_plan_grain_sql`` /
``balance_sheet._fetch_bs_budget_movements``) GROUPs BY line_code and SUMS every
``fact_position_plan`` row, so L4 rows fold into the position total automatically.
The risk is DOUBLE-COUNTING: a line_code must be planned EITHER as one L3-level row
(``level_4=''``) OR as its set of L4 rows (``level_4<>''``), never both.

This file pins:

  A. PURE (DB-free, captured SQL): ``_clear_complementary_level`` emits the right
     DELETE; ``upsert_cell``/``patch_position`` call it before the upsert; level_4
     defaults to '' (existing behavior unchanged); the INSERT carries level_4.
  B. ROUND-TRIP (live v2 DB, opt-in): seed → L3 write → L4 write → read reflects the
     XOR (position total = sum of L4, no double count) → and vice-versa.  ALWAYS
     cleans up in a ``finally`` so v2 stays empty and the golden stays EQUIVALENT.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from app.services import budget_service
from app.services.budget_service import PERIODS


# =========================================================================== #
# A) PURE — XOR clear logic + level_4 threading (DB-free, captured SQL)
# =========================================================================== #
def _exec_log(session_mock: MagicMock) -> list[tuple[str, dict]]:
    """[(sql_text, params), ...] for every session.execute(...) call."""
    out: list[tuple[str, dict]] = []
    for call in session_mock.execute.call_args_list:
        args, _ = call
        stmt = args[0]
        params = args[1] if len(args) > 1 else {}
        out.append((str(getattr(stmt, "text", stmt)), params))
    return out


def test_clear_complementary_writing_l4_deletes_l3_empty_row():
    """Writing an L4 row (writing_level4<>'') deletes the level_4='' L3 row."""
    sess = MagicMock()
    sess.execute.return_value = MagicMock(rowcount=12)
    budget_service._clear_complementary_level(
        sess, statement="PL", line_code="NET_SALES", entity_prefix="",
        fiscal_year=2025, writing_level4="Gross sales", partner_id="",
    )
    sql, params = _exec_log(sess)[0]
    assert "DELETE FROM fact_position_plan" in sql
    assert "level_4 = ''" in sql          # drop the L3-level rows
    assert "level_4 <> ''" not in sql
    assert params["lc"] == "NET_SALES" and params["fy"] == 2025


def test_clear_complementary_writing_l3_deletes_all_l4_rows():
    """Writing the L3-level row (writing_level4='') deletes all level_4<>'' rows."""
    sess = MagicMock()
    sess.execute.return_value = MagicMock(rowcount=24)
    budget_service._clear_complementary_level(
        sess, statement="PL", line_code="NET_SALES", entity_prefix="",
        fiscal_year=2025, writing_level4="", partner_id="",
    )
    sql, _ = _exec_log(sess)[0]
    assert "level_4 <> ''" in sql          # drop the L4 rows
    assert "level_4 = ''" not in sql


def test_clear_complementary_partner_scoped():
    """A partner_id scopes the DELETE to that partner only."""
    sess = MagicMock()
    sess.execute.return_value = MagicMock(rowcount=0)
    budget_service._clear_complementary_level(
        sess, statement="PL", line_code="NET_SALES", entity_prefix="01",
        fiscal_year=2025, writing_level4="Gross sales", partner_id="C1",
    )
    sql, params = _exec_log(sess)[0]
    assert "partner_id = :pid" in sql
    assert params["pid"] == "C1" and params["ep"] == "01"


def test_upsert_cell_clears_complementary_then_inserts_with_level4():
    """upsert_cell at L4 emits a clear-of-'' DELETE first, then INSERTs carry level_4."""
    sess = MagicMock()
    sess.execute.return_value = MagicMock(rowcount=12)
    budget_service.upsert_cell(
        sess, statement="PL", line_code="NET_SALES", entity="",
        partner_id=None, partner_kind=None, fiscal_year=2025,
        annual=1200.0, level_4="Gross sales",
    )
    log = _exec_log(sess)
    # First statement is the XOR DELETE (drop the complementary L3 '' rows).
    assert "DELETE FROM fact_position_plan" in log[0][0]
    assert "level_4 = ''" in log[0][0]
    # The INSERTs (12 of them) carry the level_4 we wrote.
    inserts = [(s, p) for s, p in log if "INSERT INTO fact_position_plan" in s]
    assert len(inserts) == 12
    assert all(p["l4"] == "Gross sales" for _, p in inserts)
    sess.commit.assert_called_once()


def test_upsert_cell_level4_defaults_to_empty_existing_behavior():
    """No level_4 → writes the L3-level row and clears any L4 rows (legacy behavior:
    an L3 edit is the existing path; INSERTs carry level_4=''.)"""
    sess = MagicMock()
    sess.execute.return_value = MagicMock(rowcount=12)
    budget_service.upsert_cell(
        sess, statement="PL", line_code="NET_SALES", entity="",
        partner_id=None, partner_kind=None, fiscal_year=2025, annual=1200.0,
    )
    log = _exec_log(sess)
    assert "level_4 <> ''" in log[0][0]   # writing L3 → drop the L4 rows
    inserts = [p for s, p in log if "INSERT INTO fact_position_plan" in s]
    assert inserts and all(p["l4"] == "" for p in inserts)


def test_patch_position_threads_level4_and_clears_complementary():
    sess = MagicMock()
    sess.execute.return_value = MagicMock(rowcount=0)
    budget_service.patch_position(
        sess, statement="PL", line_code="NET_SALES", entity="01",
        fiscal_year=2025, position={"annual": 1000.0}, level_4="Gross sales",
    )
    log = _exec_log(sess)
    assert "DELETE FROM fact_position_plan" in log[0][0]
    assert "level_4 = ''" in log[0][0]    # writing L4 position → drop L3 '' rows
    inserts = [p for s, p in log if "INSERT INTO fact_position_plan" in s]
    assert inserts and all(p["l4"] == "Gross sales" for p in inserts)


# =========================================================================== #
# B) ROUND-TRIP — live v2 DB (opt-in; auto-skip when unreachable)
# =========================================================================== #
def _v2_session_or_skip():
    from sqlalchemy import text

    from app.db import SessionLocal

    try:
        s = SessionLocal()
        s.execute(text("SELECT level_4 FROM fact_position_plan LIMIT 1"))
        return s
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"v2 budget L4 DB not reachable / column absent: {exc}")


_FY = 2025
_LC = "NET_SALES"
_L4 = "Gross sales"


@pytest.mark.skipif(
    os.getenv("DB_NAME", "Finssentials") != "finssentials_v2",
    reason="budget L4 round-trip runs only against finssentials_v2 (set DB_NAME)",
)
class TestRoundTripV2:
    """seed → L3 write → L4 write → read reflects XOR (no double count); vice-versa."""

    def _clean(self, session):
        from sqlalchemy import text
        session.execute(
            text("DELETE FROM fact_position_plan WHERE scenario='budget' "
                 "AND statement='PL' AND line_code=:lc AND fiscal_year=:fy"),
            {"lc": _LC, "fy": _FY},
        )
        session.commit()

    def _count(self, session, level4_filter: str | None):
        from sqlalchemy import text
        clause = ""
        params = {"lc": _LC, "fy": _FY}
        if level4_filter == "":
            clause = "AND level_4 = ''"
        elif level4_filter == "nonempty":
            clause = "AND level_4 <> ''"
        return int(session.execute(
            text(f"SELECT COUNT(*) FROM fact_position_plan WHERE scenario='budget' "
                 f"AND statement='PL' AND line_code=:lc AND fiscal_year=:fy {clause}"),
            params,
        ).scalar() or 0)

    def test_xor_no_double_count(self):
        session = _v2_session_or_skip()
        try:
            self._clean(session)

            # 1) Write the L3-level row.  Only level_4='' rows exist.
            budget_service.upsert_cell(
                session, statement="PL", line_code=_LC, entity="",
                partner_id=None, partner_kind=None, fiscal_year=_FY,
                annual=12000.0, updated_by="test@finssentials",
            )
            assert self._count(session, "") == 12
            assert self._count(session, "nonempty") == 0

            # 2) Now write an L4 cell.  XOR: the L3 '' rows are deleted, only L4 remain.
            budget_service.upsert_cell(
                session, statement="PL", line_code=_LC, entity="",
                partner_id=None, partner_kind=None, fiscal_year=_FY,
                annual=12000.0, level_4=_L4, updated_by="test@finssentials",
            )
            assert self._count(session, "") == 0, "L3 '' rows must be cleared by L4 write"
            assert self._count(session, "nonempty") == 12

            # Read: the position total = sum of L4 (no double count with an L3 row).
            saved = budget_service._read_budget_position_months(
                session, statement="PL", fiscal_year=_FY, entity_prefix=None
            )
            months = saved.get(_LC, {}).get("", {})
            total_presented = sum(months.values())
            assert total_presented == pytest.approx(12000.0, abs=1e-2)

            # 3) Write the L3 row again.  XOR reverses: L4 rows removed.
            budget_service.upsert_cell(
                session, statement="PL", line_code=_LC, entity="",
                partner_id=None, partner_kind=None, fiscal_year=_FY,
                annual=9000.0, updated_by="test@finssentials",
            )
            assert self._count(session, "nonempty") == 0, "L4 rows must be cleared by L3 write"
            assert self._count(session, "") == 12
            saved2 = budget_service._read_budget_position_months(
                session, statement="PL", fiscal_year=_FY, entity_prefix=None
            )
            assert sum(saved2.get(_LC, {}).get("", {}).values()) == pytest.approx(9000.0, abs=1e-2)
        finally:
            self._clean(session)
            session.close()
