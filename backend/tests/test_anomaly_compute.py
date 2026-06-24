"""Tests for the anomaly read-through orchestrators (anomaly rework, Phase 3).

DB-FREE: the Phase 1/2 builders and the snapshot cache are monkeypatched so no
heavy DB work runs.  Covers:
  * cache MISS computes + saves (cache_hit False), then HIT serves cached (True),
  * TTL expiry forces a recompute,
  * a save failure is swallowed (read still returns the fresh payload),
  * the overview aggregate shape (PL before BS; flags carry status/sentence/deep_link).
"""
from __future__ import annotations

import os

import pytest

from app.services import anomaly_compute as ac


class _FakeSession:
    def __init__(self):
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True


# --------------------------------------------------------------------------- #
# An in-memory cache double honouring the (type, scope, algo) key
# --------------------------------------------------------------------------- #
class _CacheDouble:
    def __init__(self, *, fail_save=False, ttl_expired=False):
        self.store: dict[tuple, dict] = {}
        self.fail_save = fail_save
        self.ttl_expired = ttl_expired
        self.saves = 0

    def get_cached(self, session, atype, scope, algo, *, ttl_seconds):
        if self.ttl_expired:
            return None
        return self.store.get((atype, scope, algo))

    def save(self, session, atype, scope, algo, payload):
        self.saves += 1
        if self.fail_save:
            raise RuntimeError("save boom")
        self.store[(atype, scope, algo)] = dict(payload)


@pytest.fixture
def patch_cache(monkeypatch):
    def _install(double):
        monkeypatch.setattr(ac.cache, "get_cached", double.get_cached)
        monkeypatch.setattr(ac.cache, "save", double.save)
        return double
    return _install


# --------------------------------------------------------------------------- #
# Builder doubles
# --------------------------------------------------------------------------- #
def _patch_builder(monkeypatch, name, payload, counter):
    def _build(session, **kwargs):
        counter["calls"] += 1
        return dict(payload)
    monkeypatch.setattr(ac, name, _build)


# --------------------------------------------------------------------------- #
# Miss → compute + save (cache_hit False); then Hit → cached (cache_hit True)
# --------------------------------------------------------------------------- #
def test_outliers_miss_then_hit(monkeypatch, patch_cache):
    double = patch_cache(_CacheDouble())
    counter = {"calls": 0}
    _patch_builder(monkeypatch, "build_outlier_tree", {"analysis": "outliers", "tree": []}, counter)
    sess = _FakeSession()

    first = ac.get_outliers(sess, entity_prefixes=None)
    assert first["cache_hit"] is False
    assert first["analysis"] == "outliers"
    assert counter["calls"] == 1
    assert double.saves == 1

    second = ac.get_outliers(sess, entity_prefixes=None)
    assert second["cache_hit"] is True
    assert counter["calls"] == 1  # builder NOT called again
    assert double.saves == 1


def test_forensic_miss_then_hit(monkeypatch, patch_cache):
    double = patch_cache(_CacheDouble())
    counter = {"calls": 0}
    _patch_builder(
        monkeypatch, "build_forensic_positions",
        {"unexpected_counter_positions": [], "other_positions": [], "suspicious_texts": []},
        counter,
    )
    sess = _FakeSession()
    assert ac.get_forensic(sess)["cache_hit"] is False
    assert ac.get_forensic(sess)["cache_hit"] is True
    assert counter["calls"] == 1


def test_scope_separates_cache_entries(monkeypatch, patch_cache):
    double = patch_cache(_CacheDouble())
    counter = {"calls": 0}
    _patch_builder(monkeypatch, "build_seasonality_tree", {"analysis": "seasonality", "tree": []}, counter)
    sess = _FakeSession()
    ac.get_seasonality(sess, entity_prefixes=None)          # scope ''
    ac.get_seasonality(sess, entity_prefixes=["01"])        # scope '01'
    # two distinct keys → two computes
    assert counter["calls"] == 2
    assert ("seasonality", "", ac.TREE_ALGORITHM_VERSION) in double.store
    assert ("seasonality", "01", ac.TREE_ALGORITHM_VERSION) in double.store


# --------------------------------------------------------------------------- #
# TTL expiry forces recompute
# --------------------------------------------------------------------------- #
def test_ttl_expiry_forces_recompute(monkeypatch, patch_cache):
    double = patch_cache(_CacheDouble(ttl_expired=True))
    counter = {"calls": 0}
    _patch_builder(monkeypatch, "build_outlier_tree", {"analysis": "outliers", "tree": []}, counter)
    sess = _FakeSession()
    ac.get_outliers(sess)
    ac.get_outliers(sess)
    # cache always misses (expired) → builder runs each time
    assert counter["calls"] == 2


# --------------------------------------------------------------------------- #
# Save failure is swallowed — read still returns the fresh payload
# --------------------------------------------------------------------------- #
def test_save_failure_is_swallowed(monkeypatch, patch_cache):
    double = patch_cache(_CacheDouble(fail_save=True))
    counter = {"calls": 0}
    _patch_builder(monkeypatch, "build_outlier_tree", {"analysis": "outliers", "tree": [1]}, counter)
    sess = _FakeSession()
    result = ac.get_outliers(sess)
    assert result["cache_hit"] is False
    assert result["tree"] == [1]
    assert sess.rolled_back is True  # failed write rolled back


# --------------------------------------------------------------------------- #
# Overview REPORT cards (Phase 3): headline + bullets + spark + signal_score/band
# --------------------------------------------------------------------------- #
def _l3(key, level_0, signal_score, *, points=None, regression=None, mean_keur=None):
    """A minimal L3 node mirroring the Phase 0 tree shape.

    ``points`` is a list of (label, value, point_score) tuples for the node series;
    ``signal_score`` is the node-level MAX score (what the overview reads).
    """
    series = []
    for label, value, pscore in (points or []):
        series.append({"label": label, "value_keur": value, "signal_score": pscore})
    payload = {"series": series}
    if mean_keur is not None:
        payload["stats"] = {"mean_keur": mean_keur, "std_keur": 1.0, "n": len(series)}
    if regression is not None:
        payload["regression"] = regression
    return {
        "level": "level_3", "key": key, "label": key, "level_0": level_0,
        "level_2": "L2", "level_3": key, "statement": "pl" if level_0 == "PL" else "bs",
        "signal_score": signal_score, "band": ac.score_band(signal_score),
        "max_abs_z": 0.0, "payload": payload, "children": [],
    }


def _no_z_or_sigma(text: str) -> bool:
    low = text.lower()
    return ("z-score" not in low and " z " not in f" {low} "
            and "σ" not in text and "sigma" not in low)


def test_build_overview_payload_report_shape_and_order():
    outliers = {
        "analysis": "outliers",
        "tree": [
            _l3("Revenue", "PL", 80,
                points=[("Jan 2024", 400.0, 10), ("Mar 2024", 1200.0, 80)],
                mean_keur=400.0,
                regression={"slope": 5.0, "intercept": 100.0, "r_squared": 0.8}),
            _l3("Cash", "BS", 5, points=[("Jan", 100.0, 5)], mean_keur=100.0),
            # low band, no forensic → NOT notable → excluded
            _l3("Inventory", "BS", 10, points=[("Jan", 50.0, 10)], mean_keur=50.0),
        ],
    }
    seasonality = {
        "analysis": "seasonality",
        "tree": [
            _l3("Revenue", "PL", 20, points=[("Jan", 400.0, 20)]),
            _l3("Cash", "BS", 55, points=[("Feb", 120.0, 55)], mean_keur=100.0),
        ],
    }
    forensic = {
        "unexpected_counter_positions": [{"level_3": "Revenue", "flag_count": 4}],
        "other_positions": [],
        "suspicious_texts": [{"position": "Revenue"}],
    }

    payload = ac.build_overview_payload(outliers, seasonality, forensic)
    cards = payload["cards"]
    assert payload["analysis"] == "overview"

    keys = [c["key"] for c in cards]
    # Inventory excluded (low band, no forensic); PL (Revenue) before BS (Cash)
    assert "Inventory" not in keys
    assert keys == ["Revenue", "Cash"]
    assert payload["groups"]["pl"] == ["Revenue"]
    assert payload["groups"]["bs"] == ["Cash"]

    rev = next(c for c in cards if c["key"] == "Revenue")
    # signal_score = MAX(out 80, sea 20, forensic 5 flags→50) = 80
    assert rev["signal_score"] == 80
    assert rev["band"] == "high"
    # headline: non-empty, plain-English, no z/σ
    assert rev["headline"]
    assert _no_z_or_sigma(rev["headline"])
    # bullets: 2-4 short points, no z/σ
    assert 2 <= len(rev["bullets"]) <= 4
    assert all(_no_z_or_sigma(b) for b in rev["bullets"])
    assert any("Flagged in" in b for b in rev["bullets"])
    # spark_series: [{label, value, expected, signal_score}] downsampled (here 2 points).
    # Outlier-driven card → expected is the node MEAN (flat reference = mean_keur 400.0);
    # label is the month STRING from the point (never an index); signal_score per point.
    assert rev["spark_series"] == [
        {"label": "Jan 2024", "value": 400.0, "expected": 400.0, "signal_score": 10},
        {"label": "Mar 2024", "value": 1200.0, "expected": 400.0, "signal_score": 80},
    ]
    # flags carry score/band/deep_link (no z-derived status)
    for k in ("outliers", "seasonality", "forensic"):
        f = rev["flags"][k]
        assert set(f) == {"score", "band", "deep_link"}
        assert "status" not in f and "sentence" not in f
    assert rev["flags"]["outliers"]["score"] == 80
    assert rev["flags"]["outliers"]["band"] == "high"
    assert rev["flags"]["forensic"]["score"] == 50  # 5 flags → medium
    assert rev["flags"]["forensic"]["band"] == "medium"
    assert rev["flags"]["forensic"]["deep_link"] == "/anomaly-detection/forensic?node=Revenue"

    cash = next(c for c in cards if c["key"] == "Cash")
    # notable via seasonality (55 = medium); MAX(out 5, sea 55, forensic 0) = 55
    assert cash["signal_score"] == 55
    assert cash["band"] == "medium"
    assert cash["flags"]["forensic"]["score"] == 0


def test_build_overview_payload_score_desc_sort_within_group():
    outliers = {
        "analysis": "outliers",
        "tree": [
            _l3("LowPL", "PL", 40, points=[("Jan", 10.0, 40)], mean_keur=10.0),
            _l3("HighPL", "PL", 90, points=[("Jan", 10.0, 90)], mean_keur=10.0),
            _l3("MidBS", "BS", 70, points=[("Jan", 10.0, 70)], mean_keur=10.0),
        ],
    }
    seasonality = {"analysis": "seasonality", "tree": []}
    forensic = {"unexpected_counter_positions": [], "other_positions": [], "suspicious_texts": []}

    payload = ac.build_overview_payload(outliers, seasonality, forensic)
    # PL group sorted score-desc, THEN BS group
    assert [c["key"] for c in payload["cards"]] == ["HighPL", "LowPL", "MidBS"]


def test_build_overview_payload_spark_downsampled():
    n = 60
    pts = [(f"M{i}", float(i), 50 if i == n - 1 else 5) for i in range(n)]
    outliers = {
        "analysis": "outliers",
        "tree": [_l3("Revenue", "PL", 50, points=pts, mean_keur=30.0)],
    }
    seasonality = {"analysis": "seasonality", "tree": []}
    forensic = {"unexpected_counter_positions": [], "other_positions": [], "suspicious_texts": []}

    payload = ac.build_overview_payload(outliers, seasonality, forensic)
    spark = payload["cards"][0]["spark_series"]
    assert len(spark) <= ac.SPARK_MAX_POINTS
    # last real month preserved, now carrying the 4 fields (outlier mean 30.0 = expected)
    assert spark[-1] == {
        "label": "M59", "value": 59.0, "expected": 30.0, "signal_score": 50,
    }
    # every carried point has the full field set, label is a string (not an index)
    for pt in spark:
        assert set(pt) == {"label", "value", "expected", "signal_score"}
        assert isinstance(pt["label"], str)


def test_overview_orchestrator_caches_finished_payload(monkeypatch, patch_cache):
    double = patch_cache(_CacheDouble())
    c1 = {"calls": 0}
    c2 = {"calls": 0}
    c3 = {"calls": 0}
    notable = _l3("Revenue", "PL", 80, points=[("Jan", 400.0, 10), ("Mar", 1200.0, 80)], mean_keur=400.0)
    _patch_builder(monkeypatch, "build_outlier_tree", {"tree": [notable]}, c1)
    _patch_builder(monkeypatch, "build_seasonality_tree", {"tree": []}, c2)
    _patch_builder(
        monkeypatch, "build_forensic_positions",
        {"unexpected_counter_positions": [], "other_positions": [], "suspicious_texts": []},
        c3,
    )
    sess = _FakeSession()
    first = ac.get_overview(sess)
    assert first["cache_hit"] is False
    assert first["analysis"] == "overview"
    second = ac.get_overview(sess)
    assert second["cache_hit"] is True
    # builders ran exactly once (overview cached the finished aggregate)
    assert (c1["calls"], c2["calls"], c3["calls"]) == (1, 1, 1)
    assert ("overview", "", ac.OVERVIEW_ALGORITHM_VERSION) in double.store


def test_spark_series_outlier_driven_uses_flat_mean_reference():
    # Outlier node present → expected is the node MEAN, repeated on every point.
    out_node = {
        "payload": {
            "stats": {"mean_keur": 100.0},
            "series": [
                {"label": "Jan24", "value_keur": 80.0, "signal_score": 5},
                {"label": "Feb24", "value_keur": 300.0, "signal_score": 70},
            ],
        }
    }
    spark = ac._spark_series(out_node, None)
    assert spark == [
        {"label": "Jan24", "value": 80.0, "expected": 100.0, "signal_score": 5},
        {"label": "Feb24", "value": 300.0, "expected": 100.0, "signal_score": 70},
    ]


def test_spark_series_seasonality_driven_uses_per_point_expected():
    # Only a seasonality node → value is actual_keur, expected is the point's expected_keur.
    sea_node = {
        "payload": {
            "series": [
                {"label": "Jan24", "actual_keur": 90.0, "expected_keur": 100.0, "signal_score": 8},
                {"label": "Feb24", "actual_keur": 210.0, "expected_keur": 120.0, "signal_score": 64},
            ],
        }
    }
    spark = ac._spark_series(None, sea_node)
    assert spark == [
        {"label": "Jan24", "value": 90.0, "expected": 100.0, "signal_score": 8},
        {"label": "Feb24", "value": 210.0, "expected": 120.0, "signal_score": 64},
    ]


def test_spark_series_label_is_string_not_index():
    out_node = {
        "payload": {
            "stats": {"mean_keur": 10.0},
            # period_key fallback when label missing; never a positional index
            "series": [{"period_key": "2024-03", "value_keur": 50.0, "signal_score": 0}],
        }
    }
    spark = ac._spark_series(out_node, None)
    assert spark == [
        {"label": "2024-03", "value": 50.0, "expected": 10.0, "signal_score": 0},
    ]


def test_spark_series_outlier_missing_mean_yields_none_expected():
    out_node = {"payload": {"series": [{"label": "Jan", "value_keur": 5.0, "signal_score": 0}]}}
    spark = ac._spark_series(out_node, None)
    assert spark == [{"label": "Jan", "value": 5.0, "expected": None, "signal_score": 0}]


def test_overview_algorithm_version_bumped_to_v3():
    # Cache key includes the version; the spark-shape change must invalidate it.
    assert ac.OVERVIEW_ALGORITHM_VERSION.startswith("anomaly_overview_v3+")


# =========================================================================== #
# Postgres integration (SKIPPED when finssentials_v2 unreachable) — proves the
# OVERVIEW snapshot now actually PERSISTS to the real table.
#
# Regression for the bug where OVERVIEW_ALGORITHM_VERSION (a 63-char composite)
# overflowed algorithm_version VARCHAR(40), so save() raised "value too long for
# type character varying(40)", the best-effort write was swallowed, and the
# overview was recomputed (~18s) on EVERY call (cache_hit never True).
#
# The three heavy builders are monkeypatched to tiny payloads so this asserts the
# SAVE + HIT round-trip against the WIDE column WITHOUT the ~18s compute.  The row
# it writes (overview, scope '') is deleted in a finally so v2 is left untouched
# and the golden comparison stays EQUIVALENT.
# =========================================================================== #
def _v2_session():
    os.environ.setdefault("DB_NAME", "finssentials_v2")
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session
        from app.config import settings
        url = settings.database_url
        if "finssentials_v2" not in url:
            url = url.rsplit("/", 1)[0] + "/finssentials_v2"
        eng = create_engine(url, connect_args={"connect_timeout": 3})
        s = Session(eng)
        s.execute(text("SELECT 1"))
        # require the snapshot table (migration 0014/0015 applied)
        if not _snapshot_table_exists(s):
            s.close()
            return None
        return s
    except Exception:
        return None


def _snapshot_table_exists(session) -> bool:
    from sqlalchemy import text
    return bool(
        session.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = 'anomaly_analysis_snapshot'
                LIMIT 1
                """
            )
        ).fetchone()
    )


@pytest.fixture(scope="function")
def v2():
    s = _v2_session()
    if s is None:
        pytest.skip("finssentials_v2 not reachable or anomaly_analysis_snapshot missing")
    yield s
    s.close()


def test_pg_overview_persists_and_hits_cache(v2, monkeypatch):
    """Overview MISS persists to the wide column, then a 2nd call is a cache HIT.

    Proves the VARCHAR(120) widening fixed the silent save overflow: with the old
    VARCHAR(40) column the save raised and the 2nd call would still be cache_hit
    False.  Heavy builders are stubbed so the assertion is the SAVE+HIT round-trip,
    not the ~18s compute.
    """
    from sqlalchemy import text

    # The composite version that previously overflowed VARCHAR(40).
    assert len(ac.OVERVIEW_ALGORITHM_VERSION) > 40

    monkeypatch.setattr(
        ac, "build_outlier_tree",
        lambda session, **kw: {"analysis": "outliers", "tree": []},
    )
    monkeypatch.setattr(
        ac, "build_seasonality_tree",
        lambda session, **kw: {"analysis": "seasonality", "tree": []},
    )
    monkeypatch.setattr(
        ac, "build_forensic_positions",
        lambda session, **kw: {
            "unexpected_counter_positions": [], "other_positions": [],
            "suspicious_texts": [],
        },
    )

    del_params = {"algo": ac.OVERVIEW_ALGORITHM_VERSION}
    delete_sql = text(
        "DELETE FROM anomaly_analysis_snapshot "
        "WHERE analysis_type = 'overview' AND entity_scope = '' "
        "AND algorithm_version = :algo"
    )
    # Start from a known-clean state for this key (don't disturb other rows).
    v2.execute(delete_sql, del_params)
    v2.commit()

    try:
        first = ac.get_overview(v2, entity_prefixes=None)
        assert first["cache_hit"] is False
        assert first["analysis"] == "overview"

        # The row must have actually landed (this is what VARCHAR(40) blocked).
        n = v2.execute(
            text(
                "SELECT COUNT(*) FROM anomaly_analysis_snapshot "
                "WHERE analysis_type = 'overview' AND entity_scope = '' "
                "AND algorithm_version = :algo"
            ),
            del_params,
        ).scalar()
        assert n == 1, "overview snapshot did not persist (column too narrow?)"

        second = ac.get_overview(v2, entity_prefixes=None)
        assert second["cache_hit"] is True, "2nd call should serve the cached overview"
        assert second["analysis"] == "overview"
    finally:
        # Leave v2 exactly as before so the golden comparison stays EQUIVALENT.
        v2.execute(delete_sql, del_params)
        v2.commit()
