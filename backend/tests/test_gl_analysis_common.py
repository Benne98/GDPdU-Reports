"""Tests for app/services/gl_analysis_common.py (Journal Agent, Phase 0).

This is the shared foundation for three GL-anomaly analyses; the two load-bearing
correctness properties are the **sign convention** (PL inverted, BS raw) and the
**synthetic-row exclusion** (carry-forward opening balances + net-profit bookings
must NEVER enter the series, or they manufacture fake outliers / a fake December
peak).  Both are pinned here with tiny worked examples.

Strategy: in-memory SQLite with a MINIMAL schema mirroring the production columns
the module touches (fact_gl_entry / fact_gl_line / dim_gl_account), populated with
synthetic rows, then run the REAL SQL the module emits (the same pattern as
etl/tests/test_net_profit.py).  No real client data (CLAUDE.md rule #2).

Worked example (consolidated, two entities, account 4000 = PL revenue,
account 1200 = BS asset):

    entity 01, account 014000 (PL revenue, credit-normal, stored −):
        2024-01 amount −100  → presented +100  → +0.100 kEUR
        2024-02 amount −150  → presented +150  → +0.150 kEUR
    entity 01, account 011200 (BS asset, debit-normal, stored +):
        2024-01 amount +60   → raw +60          → +0.060 kEUR
    entity 02, account 024000 (PL revenue):
        2024-01 amount −40   → presented +40    → +0.040 kEUR

    + a synthetic carry-forward OB row (period 0, source synthetic_carry_forward_ob)
    + a synthetic net-profit row     (period 12, source synthetic_net_profit)
    → BOTH excluded; never appear in any account's series.
"""
from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services import gl_analysis_common as G


# --------------------------------------------------------------------------- #
# Minimal in-memory schema (only the columns the module reads)
# --------------------------------------------------------------------------- #
_SCHEMA = [
    """
    CREATE TABLE dim_gl_account (
        account_number_group TEXT NOT NULL,
        fiscal_year          INTEGER NOT NULL,
        gl_account_id        TEXT,
        account_name         TEXT,
        level_0              TEXT,
        level_2              TEXT,
        level_3              TEXT,
        level_4              TEXT,
        l4_sub               TEXT,
        entity_prefix        TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
    """
    CREATE TABLE fact_gl_entry (
        journal_entry_group_number TEXT NOT NULL,
        fiscal_year   INTEGER NOT NULL,
        fiscal_period INTEGER,
        entry_type    TEXT,
        posting_date  TEXT,
        source_system TEXT,
        entity_prefix TEXT,
        PRIMARY KEY (journal_entry_group_number, fiscal_year)
    )
    """,
    """
    CREATE TABLE fact_gl_line (
        journal_entry_group_number TEXT NOT NULL,
        fiscal_year   INTEGER NOT NULL,
        line_number   INTEGER NOT NULL,
        account_number_group TEXT NOT NULL,
        amount        REAL NOT NULL,
        source_system TEXT,
        entity_prefix TEXT,
        PRIMARY KEY (journal_entry_group_number, fiscal_year, line_number)
    )
    """,
    # dim_legal_entity is consulted by resolve_entity_prefix for a named entity.
    """
    CREATE TABLE dim_legal_entity (
        legal_entity_code TEXT,
        entity_prefix     TEXT
    )
    """,
]


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
    return Session(engine)


_LINE_SEQ = {"n": 0}


def _add_account(session, ang, fy, level_0, *, l2="", l3="", name="", gid=""):
    session.execute(
        text(
            "INSERT OR IGNORE INTO dim_gl_account "
            "(account_number_group, fiscal_year, gl_account_id, account_name, "
            " level_0, level_2, level_3, entity_prefix) "
            "VALUES (:a, :y, :g, :n, :l0, :l2, :l3, :e)"
        ),
        {"a": ang, "y": fy, "g": gid or ang, "n": name or ang,
         "l0": level_0, "l2": l2, "l3": l3, "e": ang[:2]},
    )


def _add_movement(
    session, ang, fy, period, amount, *,
    source_system="erp", entry_type=None,
):
    """One synthetic single-line journal entry + its line."""
    _LINE_SEQ["n"] += 1
    seq = _LINE_SEQ["n"]
    grp = f"{ang[:2]}{seq:08d}"  # 2-char entity prefix + counter (unique)
    ep = ang[:2]
    pd = f"{fy:04d}-{max(period,1):02d}-15"
    session.execute(
        text(
            "INSERT INTO fact_gl_entry "
            "(journal_entry_group_number, fiscal_year, fiscal_period, entry_type, "
            " posting_date, source_system, entity_prefix) "
            "VALUES (:g, :y, :p, :et, :pd, :ss, :e)"
        ),
        {"g": grp, "y": fy, "p": period, "et": entry_type, "pd": pd,
         "ss": source_system, "e": ep},
    )
    session.execute(
        text(
            "INSERT INTO fact_gl_line "
            "(journal_entry_group_number, fiscal_year, line_number, "
            " account_number_group, amount, source_system, entity_prefix) "
            "VALUES (:g, :y, 1, :a, :amt, :ss, :e)"
        ),
        {"g": grp, "y": fy, "a": ang, "amt": amount, "ss": source_system, "e": ep},
    )


def _seed_base(session):
    """The worked-example ledger (consolidated, two entities)."""
    for fy in (2023, 2024):
        _add_account(session, "014000", fy, "PL", l2="Income", l3="Net sales",
                     name="Revenue 01", gid="4000")
        _add_account(session, "011200", fy, "BS", l2="Assets", l3="Receivables",
                     name="Trade receivables 01", gid="1200")
        _add_account(session, "024000", fy, "PL", l2="Income", l3="Net sales",
                     name="Revenue 02", gid="4000")
    # PL revenue (credit, stored negative)
    _add_movement(session, "014000", 2023, 11, -10.0)
    _add_movement(session, "014000", 2024, 1, -100.0)
    _add_movement(session, "014000", 2024, 2, -150.0)
    # BS asset (debit, stored positive)
    _add_movement(session, "011200", 2024, 1, 60.0)
    # entity 02 PL revenue
    _add_movement(session, "024000", 2024, 1, -40.0)
    session.commit()


def _add_synthetic_rows(session):
    """A carry-forward OB row (period 0) and a net-profit row (period 12)."""
    # Opening-balance carry-forward on the BS asset, fiscal_period 0.
    _add_movement(
        session, "011200", 2024, 0, 9999.0,
        source_system=G.SYNTHETIC_OB_SOURCE, entry_type="opening_balance",
    )
    # Net-profit equity booking on a PL-tagged synthetic, fiscal_period 12.
    _add_account(session, "010001", 2024, "BS", l2="Equity", l3="Net profit",
                 name="Net profit 01", gid="0001")
    _add_movement(
        session, "010001", 2024, 12, -8888.0,
        source_system=G.SYNTHETIC_NP_SOURCE, entry_type="net_profit",
    )
    session.commit()


def _by_account(accounts):
    return {(a.entity_prefix, a.account_number_group): a for a in accounts}


# =========================================================================== #
# (1) Synthetic-row exclusion — THE key correctness test
# =========================================================================== #
def test_synthetic_ob_and_np_rows_never_appear_in_series():
    session = _make_session()
    _seed_base(session)
    _add_synthetic_rows(session)

    accounts = G.build_account_monthly_series(session, entity=None)
    by = _by_account(accounts)

    # The synthetic net-profit account (010001) must not exist as a series at all.
    assert ("01", "010001") not in by, "synthetic net-profit account leaked in"

    # The BS asset (011200) exists, but the period-0 OB row (+9999) must NOT be
    # in its series — only the real +60 in 2024-01.
    bs = by[("01", "011200")]
    # No period 0 anywhere in the span.
    assert all(p.fiscal_period != 0 for p in bs.series)
    assert bs.value_at(2024, 1) == 0.060  # +60 EUR raw → +0.060 kEUR
    # The 9999 OB never inflated any month.
    assert all(abs(p.value_keur) < 9.0 for p in bs.series)

    # No fake December (period 12) peak from the net-profit row on any account.
    for acc in accounts:
        assert acc.value_at(2024, 12) == 0.0


# =========================================================================== #
# (2) All-history span covers earliest..latest month present
# =========================================================================== #
def test_all_history_span_covers_earliest_to_latest():
    session = _make_session()
    _seed_base(session)
    _add_synthetic_rows(session)

    span = G.discover_month_span(session)
    # Real months present: 2023-11, 2024-01, 2024-02 (synthetic 0 / 12 excluded).
    assert span == [(2023, 11), (2024, 1), (2024, 2)]

    accounts = G.build_account_monthly_series(session, entity=None)
    # Every account carries the SAME ordered span (dense series).
    for acc in accounts:
        assert [(p.fiscal_year, p.fiscal_period) for p in acc.series] == span
        assert [p.period_key for p in acc.series] == ["2023-11", "2024-01", "2024-02"]


def test_empty_ledger_returns_empty():
    session = _make_session()
    assert G.discover_month_span(session) == []
    assert G.build_account_monthly_series(session, entity=None) == []


# =========================================================================== #
# (3) Consolidated grain groups by (entity_prefix, account_number_group)
# =========================================================================== #
def test_consolidated_grain_separates_same_account_across_prefixes():
    session = _make_session()
    _seed_base(session)

    accounts = G.build_account_monthly_series(session, entity=None)
    by = _by_account(accounts)

    # gl_account_id '4000' exists under BOTH 01 and 02 prefixes → two rows.
    assert ("01", "014000") in by
    assert ("02", "024000") in by
    assert by[("01", "014000")].gl_account_id == "4000"
    assert by[("02", "024000")].gl_account_id == "4000"
    # entity_prefix exposed on each row.
    assert by[("01", "014000")].entity_prefix == "01"
    assert by[("02", "024000")].entity_prefix == "02"
    # Values are per-prefix, not merged.
    assert by[("01", "014000")].value_at(2024, 1) == 0.100
    assert by[("02", "024000")].value_at(2024, 1) == 0.040


def test_single_entity_scopes_to_that_prefix():
    session = _make_session()
    _seed_base(session)
    session.execute(text(
        "INSERT INTO dim_legal_entity (legal_entity_code, entity_prefix) "
        "VALUES ('E01', '01'), ('E02', '02')"
    ))
    session.commit()

    accounts = G.build_account_monthly_series(session, entity="E01")
    prefixes = {a.entity_prefix for a in accounts}
    assert prefixes == {"01"}  # entity 02 excluded


# =========================================================================== #
# (4) Sign — PL reads positive (revenue), BS reads raw
# =========================================================================== #
def test_pl_revenue_presents_positive():
    session = _make_session()
    _seed_base(session)
    by = _by_account(G.build_account_monthly_series(session, entity=None))
    rev = by[("01", "014000")]
    assert rev.level_0 == "PL"
    # stored −100 / −150 (credit) → presented +0.100 / +0.150 kEUR
    assert rev.value_at(2024, 1) == 0.100
    assert rev.value_at(2024, 2) == 0.150
    assert all(p.value_keur >= 0 for p in rev.series)


def test_bs_asset_keeps_raw_sign():
    session = _make_session()
    _seed_base(session)
    by = _by_account(G.build_account_monthly_series(session, entity=None))
    asset = by[("01", "011200")]
    assert asset.level_0 == "BS"
    # stored +60 (debit) → raw +0.060 kEUR (NO inversion)
    assert asset.value_at(2024, 1) == 0.060


def test_level_0_filter_restricts_to_pl():
    session = _make_session()
    _seed_base(session)
    pl_only = G.build_account_monthly_series(session, entity=None, level_0="PL")
    assert {a.level_0 for a in pl_only} == {"PL"}
    assert all(a.account_number_group != "011200" for a in pl_only)


# =========================================================================== #
# (5) Bounding keeps only material accounts
# =========================================================================== #
def _series_from(points, level_0="PL"):
    return G.AccountSeries(
        gl_account_id="x", account_name="x", account_number_group="01x",
        entity_prefix="01", level_0=level_0, level_2="", level_3="",
        series=[
            G.MonthPoint(fiscal_year=y, fiscal_period=m,
                         period_key=G.period_key(y, m),
                         label=G.period_label(y, m), value_keur=v)
            for (y, m, v) in points
        ],
    )


def test_bounding_keeps_size_and_mom_drops_immaterial():
    # SIZE_FLOOR 50_000 EUR = 50 kEUR ; MOM_FLOOR 30_000 EUR = 30 kEUR.
    big_size = _series_from([(2024, 1, 10.0), (2024, 2, 80.0)])      # |cm|=80 >= 50
    big_mom = _series_from([(2024, 1, 5.0), (2024, 2, 40.0)])        # mom=35 >= 30
    tiny = _series_from([(2024, 1, 5.0), (2024, 2, 7.0)])            # cm 7, mom 2

    # Give each a distinct account number so ordering is well-defined.
    big_size.account_number_group = "01a"
    big_mom.account_number_group = "01b"
    tiny.account_number_group = "01c"

    kept = G.bound_material_accounts(
        [big_size, big_mom, tiny], current_year=2024, current_period=2,
    )
    nums = {a.account_number_group for a in kept}
    assert nums == {"01a", "01b"}
    assert "01c" not in nums


def test_bounding_top_n_caps_by_magnitude():
    a = _series_from([(2024, 1, 0.0), (2024, 2, 500.0)])  # |cm| 500
    b = _series_from([(2024, 1, 0.0), (2024, 2, 80.0)])   # |cm| 80
    c = _series_from([(2024, 1, 0.0), (2024, 2, 60.0)])   # |cm| 60
    a.account_number_group = "01a"
    b.account_number_group = "01b"
    c.account_number_group = "01c"

    kept = G.bound_material_accounts(
        [c, b, a], current_year=2024, current_period=2, top_n=2,
    )
    # Largest two by magnitude, descending: a (500) then b (80).
    assert [x.account_number_group for x in kept] == ["01a", "01b"]


def test_bounding_custom_floors():
    s = _series_from([(2024, 1, 0.0), (2024, 2, 20.0)])  # |cm|=20 kEUR
    # Default floors (size 50k) drop it; a 10k size floor keeps it.
    assert G.bound_material_accounts(
        [s], current_year=2024, current_period=2,
    ) == []
    kept = G.bound_material_accounts(
        [s], current_year=2024, current_period=2, size_floor_eur=10_000.0,
    )
    assert len(kept) == 1
