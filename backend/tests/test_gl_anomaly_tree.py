"""Tests for gl_anomaly_tree (anomaly rework, Phase 1) — hierarchical trees + drill.

Mostly DB-FREE: the tree is assembled from hand-built ``AccountSeries`` fixtures
(synthetic only — CLAUDE.md rule #2) and the two ``build_*_tree`` entry points are
exercised by MONKEYPATCHING the single DB pull (``build_account_monthly_series``)
and the materiality anchor (``latest_anchor``).  The booking drill is checked with a
fake session that returns canned rows.

Load-bearing properties locked here:

  * RECONCILIATION — an L4 node's analysed series == Σ its accounts' series; an L3
    node's series == Σ its L4 children (inherited from gl_hierarchy, re-asserted on
    the payload the tree actually emits).
  * SEVERITY BANDING — ``severity_for_z`` bands |z|>=3 high / >=2 medium / >=1 low /
    else none, boundaries inclusive on the higher band.
  * SORTING — children ordered by severity then |z| desc; L3 emitted PL group then
    BS group.
  * (no L4) BUCKET — blank ``level_4`` accounts stay reachable under "(no L4)".
  * BOOKINGS — ordered by |amount| desc, large single booking flagged via per-booking
    z, ``booking_line_id`` preserved for the journal-entry-by-booking path.
  * PL + BS BOTH PRESENT — a mixed pull produces both statement groups.
"""
from __future__ import annotations

import math

import pytest

from app.services import gl_anomaly_tree as T
from app.services.gl_analysis_common import AccountSeries, MonthPoint
from app.services.fin_compat_sql import period_key, period_label


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #
def _series(points: list[tuple[int, int, float]]) -> list[MonthPoint]:
    return [
        MonthPoint(
            fiscal_year=y, fiscal_period=m, period_key=period_key(y, m),
            label=period_label(y, m), value_keur=v,
        )
        for (y, m, v) in points
    ]


def _acc(
    ang: str, ep: str = "01", *, l0="PL", l2="Income", l3="Net sales", l4="",
    name=None, points: list[tuple[int, int, float]],
) -> AccountSeries:
    return AccountSeries(
        gl_account_id=ang, account_name=name or f"acct {ang}",
        account_number_group=ang, entity_prefix=ep,
        level_0=l0, level_2=l2, level_3=l3, level_4=l4, l4_sub="",
        series=_series(points),
    )


def _two_year_monthly(base: float, spike_at: int = -1, spike: float = 0.0):
    """24 monthly points over 2 fiscal years; optional spike on one index."""
    pts: list[tuple[int, int, float]] = []
    i = 0
    for y in (2023, 2024):
        for m in range(1, 13):
            v = base + (spike if i == (spike_at % 24) else 0.0)
            pts.append((y, m, v))
            i += 1
    return pts


# =========================================================================== #
# (1) severity_for_z — banding + inclusive boundaries
# =========================================================================== #
def test_severity_banding_and_boundaries():
    assert T.severity_for_z(3.0) == "high"      # inclusive
    assert T.severity_for_z(3.5) == "high"
    assert T.severity_for_z(2.999) == "medium"
    assert T.severity_for_z(2.0) == "medium"    # inclusive
    assert T.severity_for_z(1.0) == "low"       # inclusive
    assert T.severity_for_z(0.999) == "none"
    assert T.severity_for_z(0.0) == "none"
    # sign-agnostic + non-finite safe
    assert T.severity_for_z(-3.0) == "high"
    assert T.severity_for_z(float("nan")) == "none"


# =========================================================================== #
# (2) RECONCILIATION — L4 payload == Σ accounts, L3 payload == Σ L4
# =========================================================================== #
def test_outlier_tree_reconciles_l3_l4_accounts():
    accounts = [
        _acc("01100", l4="Products", points=[(2024, 1, 100.0), (2024, 2, 150.0)]),
        _acc("02100", ep="02", l4="Products",
             points=[(2024, 1, 40.0), (2024, 2, 60.0)]),
        _acc("01200", l4="", points=[(2024, 1, 10.0), (2024, 2, 20.0)]),
    ]
    tree = T._build_tree(accounts, "outliers", min_years=2)
    assert len(tree) == 1
    l3 = tree[0]
    assert l3["level"] == "level_3"

    def _vals(node):
        return [p["value_keur"] for p in node["payload"]["series"]]

    # L3 == Σ accounts
    assert _vals(l3) == pytest.approx([150.0, 230.0], abs=1e-6)

    # Σ L4 children == L3
    l4_sum = [0.0, 0.0]
    for l4 in l3["children"]:
        for j, p in enumerate(l4["payload"]["series"]):
            l4_sum[j] += p["value_keur"]
    assert l4_sum == pytest.approx([150.0, 230.0], abs=1e-6)

    # Σ accounts under each L4 == that L4
    for l4 in l3["children"]:
        acc_sum = [0.0, 0.0]
        for acc in l4["children"]:
            for j, p in enumerate(acc["payload"]["series"]):
                acc_sum[j] += p["value_keur"]
        l4_vals = [p["value_keur"] for p in l4["payload"]["series"]]
        assert acc_sum == pytest.approx(l4_vals, abs=1e-6)


# =========================================================================== #
# (3) (no L4) bucket keeps blank-level_4 accounts reachable
# =========================================================================== #
def test_no_l4_bucket_present_for_blank_level_4():
    accounts = [
        _acc("01100", l4="Products", points=[(2024, 1, 100.0), (2024, 2, 150.0)]),
        _acc("01200", l4="", points=[(2024, 1, 10.0), (2024, 2, 20.0)]),
    ]
    tree = T._build_tree(accounts, "outliers", min_years=2)
    l4_labels = {c["level_4"] for c in tree[0]["children"]}
    assert T.NO_L4_BUCKET in l4_labels
    bucket = next(c for c in tree[0]["children"] if c["level_4"] == T.NO_L4_BUCKET)
    assert [a["key"] for a in bucket["children"]] == ["01200"]


# =========================================================================== #
# (4) SORTING — children by severity, most anomalous first; PL before BS
# =========================================================================== #
def test_children_sorted_by_severity_then_magnitude():
    # Calm account (flat) vs spiky account (huge outlier) under different L4s.
    calm = _acc("01100", l3="Net sales", l4="Calm",
                points=_two_year_monthly(100.0))
    spiky = _acc("01200", l3="Net sales", l4="Spiky",
                 points=_two_year_monthly(100.0, spike_at=10, spike=5000.0))
    tree = T._build_tree([calm, spiky], "outliers", min_years=2)
    l4_order = [c["level_4"] for c in tree[0]["children"]]
    assert l4_order[0] == "Spiky"          # higher severity first
    spiky_node = tree[0]["children"][0]
    assert spiky_node["severity"] in ("high", "medium", "low")
    assert tree[0]["children"][1]["severity"] == "none"  # calm flat → none


def test_pl_group_before_bs_group():
    pl = _acc("01100", l0="PL", l3="Net sales",
              points=[(2024, 1, 100.0), (2024, 2, 150.0)])
    bs = _acc("01900", l0="BS", l2="Assets", l3="Cash",
              points=[(2024, 1, 500.0), (2024, 2, 480.0)])
    tree = T._build_tree([pl, bs], "outliers", min_years=2)
    statements = [n["statement"] for n in tree]
    assert statements == ["pl", "bs"]


# =========================================================================== #
# (5) build_outlier_tree / build_seasonality_tree — monkeypatched DB pull
# =========================================================================== #
def _patch_pull(monkeypatch, accounts, anchor=(2024, 12)):
    monkeypatch.setattr(
        T, "build_account_monthly_series",
        lambda session, *, level_0=None: list(accounts),
    )
    monkeypatch.setattr(T, "latest_anchor", lambda session, ent_frag="": anchor)
    # neutralise material bounding so the fixtures survive (return as-is, capped)
    monkeypatch.setattr(
        T, "bound_material_accounts",
        lambda accs, *, current_year, current_period, top_n=None: list(accs)[:top_n]
        if top_n is not None else list(accs),
    )


def test_build_outlier_tree_pl_and_bs_present(monkeypatch):
    accounts = [
        _acc("01100", l0="PL", l3="Net sales",
             points=_two_year_monthly(100.0, spike_at=5, spike=900.0)),
        _acc("01900", l0="BS", l2="Assets", l3="Cash",
             points=_two_year_monthly(500.0)),
    ]
    _patch_pull(monkeypatch, accounts)
    out = T.build_outlier_tree(object(), top_n_per_level=50)
    assert out["analysis"] == "outliers"
    statements = [n["statement"] for n in out["tree"]]
    assert "pl" in statements and "bs" in statements
    assert statements.index("pl") < statements.index("bs")


def test_build_seasonality_tree_has_decomposition(monkeypatch):
    accounts = [
        _acc("01100", l0="PL", l3="Net sales", points=_two_year_monthly(100.0)),
    ]
    _patch_pull(monkeypatch, accounts)
    out = T.build_seasonality_tree(object(), top_n_per_level=50)
    assert out["analysis"] == "seasonality"
    l3 = out["tree"][0]
    # seasonality payload carries month_index + decomposition fields
    assert "month_index" in l3["payload"]
    assert len(l3["payload"]["month_index"]) == 12
    first_pt = l3["payload"]["series"][0]
    assert "expected_keur" in first_pt and "trend_keur" in first_pt


def test_entity_prefix_filter_restricts_pull(monkeypatch):
    accounts = [
        _acc("01100", ep="01", points=_two_year_monthly(100.0)),
        _acc("02100", ep="02", points=_two_year_monthly(200.0)),
    ]
    _patch_pull(monkeypatch, accounts)
    out = T.build_outlier_tree(object(), entity_prefixes=["01"])
    # only entity 01's account survives → its single member
    members = set()
    for l3 in out["tree"]:
        members.update(l3["members"])
    assert members == {"01100"}
    assert out["meta"]["entity_prefixes"] == ["01"]


# =========================================================================== #
# (6) compute_booking_zscores — pure
# =========================================================================== #
def test_booking_zscores_flag_large_single_booking():
    amounts = [10.0, 12.0, 11.0, 13.0, 60.0]
    zs = T.compute_booking_zscores(amounts)
    assert len(zs) == 5
    # mean 21.2, sample std ~21.72 → z(60) ~1.787 (same math as gl_outliers)
    assert math.isclose(zs[4], 1.7865, abs_tol=1e-3)
    assert all(abs(z) < 1.0 for z in zs[:4])


def test_booking_zscores_edge_cases():
    assert T.compute_booking_zscores([]) == []
    assert T.compute_booking_zscores([42.0]) == [0.0]      # n<2 → 0
    assert T.compute_booking_zscores([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]  # flat


# =========================================================================== #
# (7) list_account_bookings — ordered by |amount|, flagged, fields preserved
# =========================================================================== #
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.captured_sql = None

    def execute(self, sql, params=None):
        self.captured_sql = str(sql)
        return _FakeResult(self._rows)


@pytest.fixture(autouse=True)
def _stub_counter_accounts(monkeypatch):
    """Default the counter-account derivation to empty so the bookings drill never runs
    the (real) batched counter query against the fake session.  Individual tests that
    exercise the counter join override this with their own ``derive_counter_accounts``.
    """
    monkeypatch.setattr(T, "derive_counter_accounts", lambda session, ids: {})


def _booking_row(blid, amount, jegn="01JE001", note=""):
    return _FakeRow({
        "booking_line_id": blid,
        "journal_entry_group_number": jegn,
        "entity_prefix": jegn[:2],
        "fiscal_year": 2024,
        "fiscal_period": 6,
        "amount": amount,          # EUR (raw)
        "line_note": note,
        "posting_date": None,
    })


def test_list_account_bookings_ordered_and_flagged():
    # Rows already in |amount| desc (the SQL ORDER BY does this); 200k is the spike.
    rows = [
        _booking_row(1, 200_000.0, note="big one"),
        _booking_row(2, 12_000.0),
        _booking_row(3, 11_000.0),
        _booking_row(4, 10_000.0),
        _booking_row(5, 13_000.0),
    ]
    session = _FakeSession(rows)
    out = T.list_account_bookings(session, "01100", limit=200)

    assert out["account_number_group"] == "01100"
    bookings = out["bookings"]
    assert [b["booking_line_id"] for b in bookings] == [1, 2, 3, 4, 5]
    # amount_keur conversion
    assert bookings[0]["amount_keur"] == 200.0
    # the spike is flagged large; the rest are not
    assert bookings[0]["large_booking"] is True
    assert all(b["large_booking"] is False for b in bookings[1:])
    # journal_entry_number strips the 2-char prefix → resolves the full entry
    assert bookings[0]["journal_entry_number"] == "JE001"
    # SQL ordered by ABS(amount) DESC and scoped to the account group
    assert "ABS(l.amount) DESC" in session.captured_sql
    assert "l.account_number_group = :ang" in session.captured_sql


def test_list_account_bookings_blank_group_returns_empty():
    out = T.list_account_bookings(_FakeSession([]), "   ")
    assert out["bookings"] == [] and out["stats"]["n"] == 0


def test_list_account_bookings_entity_scope_in_sql():
    session = _FakeSession([_booking_row(1, 5_000.0)])
    T.list_account_bookings(session, "01100", entity_prefixes=["01", "02"])
    assert "entity_prefix IN" in session.captured_sql


# =========================================================================== #
# (8) Bookings counter-account join — each row carries counter_account_id /
#     _name (null-safe); derive_counter_accounts is monkeypatched (batched query,
#     NOT the bulk learner).
# =========================================================================== #
def test_list_account_bookings_rows_carry_counter_account(monkeypatch):
    rows = [
        _booking_row(1, 200_000.0, note="big one"),
        _booking_row(2, 12_000.0),
        _booking_row(3, 11_000.0),
    ]

    captured = {}

    def _fake_derive(session, ids):
        captured["ids"] = list(ids)
        return {
            1: {
                "counter_gl_account_id": "1600",
                "counter_account_name": "Bank",
                "counter_account_number_group": "01600",
                "weight": 9.0,
            },
            # line 2 has empty counter fields → normalised to None
            2: {"counter_gl_account_id": "", "counter_account_name": ""},
            # line 3 absent from the map (no opposite-side sibling) → None
        }

    monkeypatch.setattr(T, "derive_counter_accounts", _fake_derive)

    out = T.list_account_bookings(_FakeSession(rows), "01100", limit=200)
    bookings = {b["booking_line_id"]: b for b in out["bookings"]}

    # ONE batched call over exactly the returned booking ids (not the bulk learner)
    assert captured["ids"] == [1, 2, 3]

    # line 1: full counter join
    assert bookings[1]["counter_account_id"] == "1600"
    assert bookings[1]["counter_account_name"] == "Bank"
    # line 2: empty strings → None (null-safe)
    assert bookings[2]["counter_account_id"] is None
    assert bookings[2]["counter_account_name"] is None
    # line 3: no derivation → None
    assert bookings[3]["counter_account_id"] is None
    assert bookings[3]["counter_account_name"] is None

    # all existing fields preserved
    for b in out["bookings"]:
        assert set(b) >= {
            "booking_line_id", "journal_entry_number", "posting_date",
            "amount_keur", "line_note", "entity_prefix", "z", "large_booking",
            "counter_account_id", "counter_account_name",
        }


def test_list_account_bookings_counter_join_null_safe_when_empty_map(monkeypatch):
    monkeypatch.setattr(T, "derive_counter_accounts", lambda session, ids: {})
    out = T.list_account_bookings(_FakeSession([_booking_row(1, 5_000.0)]), "01100")
    b = out["bookings"][0]
    assert b["counter_account_id"] is None and b["counter_account_name"] is None
