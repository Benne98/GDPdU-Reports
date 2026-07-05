"""Fixed-asset movement narratives and chart payloads."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.entities import ENTITY_PREFIX
from app.services.fixed_asset_calc import aggregate_rows, to_keur
from app.services.fixed_asset_dimensions import DIMENSION_IDS, row_dim_display, row_dim_value
from app.services.fixed_asset_rollforward import _fetch_rows, dec_label


def _prior_snapshot(session: Session, anchor: date, project_id: str = "default") -> date | None:
    row = session.execute(
        text("""
            SELECT MAX(as_of_date) AS d
            FROM fact_fixed_asset
            WHERE project_id = :pid AND as_of_date < :anchor
        """),
        {"pid": project_id, "anchor": anchor},
    ).fetchone()
    return row[0] if row and row[0] else None


def _build_waterfall(opening: float, add: float, disp: float, da: float, closing: float) -> dict[str, Any]:
    """Waterfall entries with base/value for stacked bars (bridge must tie)."""
    entries: list[dict[str, Any]] = []
    cursor = opening

    entries.append({
        "name": "Opening",
        "base": 0.0,
        "value": opening,
        "raw": opening,
        "is_total": True,
    })

    if add:
        entries.append({
            "name": "Additions",
            "base": cursor,
            "value": add,
            "raw": add,
            "is_total": False,
        })
        cursor += add

    if disp:
        entries.append({
            "name": "Disposals",
            "base": cursor - disp,
            "value": disp,
            "raw": -disp,
            "is_total": False,
        })
        cursor -= disp

    if da:
        entries.append({
            "name": "D&A",
            "base": cursor - da,
            "value": da,
            "raw": -da,
            "is_total": False,
        })
        cursor -= da

    entries.append({
        "name": "Closing",
        "base": 0.0,
        "value": closing,
        "raw": closing,
        "is_total": True,
    })

    return {
        "opening": opening,
        "additions": add,
        "disposals": disp,
        "depreciation": da,
        "closing": closing,
        "entries": entries,
    }


def _dim_chart_label(rec: dict[str, Any], dimension: str, key: str) -> str:
    if dimension == "entity":
        return ENTITY_PREFIX.get(key, key)
    if dimension in DIMENSION_IDS:
        return row_dim_display(rec, dimension)
    return key


def _group_add_disp(rows: list[dict[str, Any]], dimension: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = row_dim_value(r, dimension) if dimension in DIMENSION_IDS else str(r.get("entity_prefix") or "??")
        bucket = buckets.setdefault(key, {"additions": 0.0, "disposals": 0.0, "depreciation": 0.0, "sample": r})
        bucket["additions"] += float(r.get("additions_zugang") or 0)
        bucket["disposals"] += float(r.get("disposals_abgang") or 0)
        bucket["depreciation"] += abs(float(r.get("depreciation") or 0))
    return [
        {
            "key": k,
            "label": _dim_chart_label(v["sample"], dimension, k),
            "additions": to_keur(v["additions"]),
            "disposals": to_keur(v["disposals"]),
            "depreciation": to_keur(v["depreciation"]),
        }
        for k, v in sorted(
            buckets.items(),
            key=lambda x: -(x[1]["additions"] + x[1]["disposals"] + x[1]["depreciation"]),
        )
    ]


def _group_nbv_by_dimension(
    anchor_rows: list[dict[str, Any]],
    prior_rows: list[dict[str, Any]],
    dimension: str,
) -> list[dict[str, Any]]:
    dim = dimension if dimension in DIMENSION_IDS else "bilanzposition"
    by_anchor: dict[str, float] = {}
    by_prior: dict[str, float] = {}
    labels: dict[str, str] = {}

    for r in anchor_rows:
        key = row_dim_value(r, dim)
        by_anchor[key] = by_anchor.get(key, 0) + float(r.get("nbv") or 0)
        if key not in labels:
            labels[key] = _dim_chart_label(r, dim, key)
    for r in prior_rows:
        key = row_dim_value(r, dim)
        by_prior[key] = by_prior.get(key, 0) + float(r.get("nbv") or 0)
        if key not in labels:
            labels[key] = _dim_chart_label(r, dim, key)

    category_chart: list[dict[str, Any]] = []
    for key in sorted(set(by_anchor) | set(by_prior), key=lambda k: -by_anchor.get(k, 0)):
        if not key or key == "—":
            continue
        cur = to_keur(by_anchor.get(key, 0))
        py = to_keur(by_prior.get(key, 0))
        delta_pct = round((cur - py) / abs(py) * 100, 1) if abs(py) > 0.01 else None
        category_chart.append({
            "category": labels.get(key, key),
            "nbv": cur,
            "prior_nbv": py,
            "delta_pct": delta_pct,
        })
    return category_chart[:12]


def _bridge_dimension_groups(rows: list[dict[str, Any]], dimension: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = row_dim_value(r, dimension)
        if not key or key == "—":
            continue
        bucket = buckets.setdefault(
            key,
            {
                "key": key,
                "label": _dim_chart_label(r, dimension, key),
                "additions": 0.0,
                "disposals": 0.0,
                "depreciation": 0.0,
            },
        )
        bucket["additions"] += float(r.get("additions_zugang") or 0)
        bucket["disposals"] += float(r.get("disposals_abgang") or 0)
        bucket["depreciation"] += abs(float(r.get("depreciation") or 0))
    return [
        {
            "key": v["key"],
            "label": v["label"],
            "additions": to_keur(v["additions"]),
            "disposals": to_keur(v["disposals"]),
            "depreciation": to_keur(v["depreciation"]),
        }
        for v in sorted(
            buckets.values(),
            key=lambda x: -(x["additions"] + x["disposals"] + x["depreciation"]),
        )
    ]


def _thread_legs(
    cursor: float,
    legs_in: list[tuple[str, str, float]],
) -> tuple[list[dict[str, Any]], float]:
    """Thread a running NBV cursor through movement legs to make bars float.

    Each leg gets an additive ``base`` (lower edge of the floating bar) and a
    signed ``delta`` (additions positive; disposals/D&A negative) so a TRUE
    floating waterfall can render each bar starting where the previous ended:
    ``NBV_open + additions − disposals − depreciation = NBV_close``.

    Purely additive — magnitude fields already emitted are untouched. Legs whose
    delta rounds to zero are skipped (parity with the frontend threshold).
    Returns the leg dicts and the advanced cursor.
    """
    out: list[dict[str, Any]] = []
    for name, bar_type, value in legs_in:
        delta = value if bar_type == "add" else -abs(value)
        if abs(delta) < 0.0001:
            continue
        base = cursor if delta >= 0 else cursor + delta
        out.append({
            "name": name,
            "bar_type": bar_type,
            "base": round(base, 1),
            "delta": round(delta, 1),
            "value": round(abs(delta), 1),
        })
        cursor += delta
    return out, cursor


def _movement_leg_specs(source: dict[str, Any]) -> list[tuple[str, str, float]]:
    return [
        ("Additions", "add", float(source.get("additions") or 0)),
        ("Disposals", "disp", float(source.get("disposals") or 0)),
        ("D&A", "da", float(source.get("depreciation") or 0)),
    ]


def _build_nbv_rollforward_bridge(
    session: Session,
    *,
    opening: date,
    closing: date,
    entity: Optional[str],
    project_id: str,
    dimension: str | None,
) -> dict[str, Any]:
    """Multi-year NBV bridge from opening year-end through closing year-end."""
    if closing < opening:
        opening, closing = closing, opening
    years = list(range(opening.year, closing.year + 1))
    columns: list[dict[str, Any]] = []
    cursor = 0.0  # running NBV (kEUR) so movement bars float across the bridge

    for i, year in enumerate(years):
        year_end = date(year, 12, 31)
        rows = _fetch_rows(session, year_end, entity, project_id)
        closing_keur = aggregate_rows(rows, "closing")

        if i == 0:
            columns.append({
                "kind": "total",
                "year": year,
                "label": dec_label(year_end),
                "value": closing_keur,
                "base": 0.0,
            })
            cursor = closing_keur
            if year == closing.year:
                break
            continue

        if dimension in ("entity", "segment"):
            groups = _bridge_dimension_groups(rows, dimension)
            for g in groups:
                g_legs, cursor = _thread_legs(cursor, _movement_leg_specs(g))
                g["legs"] = g_legs
            columns.append({
                "kind": "movements_by_dimension",
                "year": year,
                "label": f"FY{str(year)[-2:]}",
                "groups": groups,
            })
        else:
            movement = {
                "additions": aggregate_rows(rows, "additions"),
                "disposals": aggregate_rows(rows, "disposals"),
                "depreciation": aggregate_rows(rows, "depreciation"),
            }
            legs, cursor = _thread_legs(cursor, _movement_leg_specs(movement))
            columns.append({
                "kind": "movements",
                "year": year,
                "label": f"FY{str(year)[-2:]}",
                **movement,
                "legs": legs,
            })

        cursor = closing_keur  # snap to actual close (avoids rounding drift)
        columns.append({
            "kind": "total",
            "year": year,
            "label": dec_label(year_end),
            "value": closing_keur,
            "base": 0.0,
        })

    dim_label = None
    if dimension == "entity":
        dim_label = "Entity"
    elif dimension == "segment":
        dim_label = "Business segment"

    return {
        "opening_date": date(opening.year, 12, 31).isoformat(),
        "closing_date": date(closing.year, 12, 31).isoformat(),
        "opening_label": dec_label(date(opening.year, 12, 31)),
        "closing_label": dec_label(date(closing.year, 12, 31)),
        "dimension": dimension,
        "scope_label": dim_label or "Consolidated",
        "columns": columns,
        # Legacy single-period waterfall (first FY only) for backward compat.
        **_bridge_for_period(
            session,
            anchor=date(closing.year, 12, 31),
            prior=date(opening.year, 12, 31),
            entity=entity,
            project_id=project_id,
            dimension=dimension if dimension in ("entity", "segment") else None,
            scope_value=None,
        ),
    }


def _filter_bridge_rows(
    rows: list[dict[str, Any]],
    dimension: str | None,
    scope_value: str | None,
    session: Session,
) -> list[dict[str, Any]]:
    if not scope_value or scope_value in ("all", ""):
        return rows
    if dimension == "entity":
        from app.services.fin_compat_sql import resolve_entity_prefix
        ep = resolve_entity_prefix(session, scope_value) or scope_value
        return [r for r in rows if str(r.get("entity_prefix") or "") == ep]
    if dimension in DIMENSION_IDS:
        return [r for r in rows if row_dim_value(r, dimension) == scope_value]
    return rows


def _scope_options(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    opts: dict[str, list[str]] = {}
    for dim in DIMENSION_IDS:
        opts[dim] = sorted({row_dim_value(r, dim) for r in rows})
    return opts


def _scope_option_labels(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    asset_labels: dict[str, str] = {}
    for r in rows:
        k = row_dim_value(r, "asset")
        if k and k not in asset_labels:
            asset_labels[k] = row_dim_display(r, "asset")
    return {"asset": asset_labels} if asset_labels else {}


def _bridge_for_period(
    session: Session,
    *,
    anchor: date,
    prior: date | None,
    entity: Optional[str],
    project_id: str,
    dimension: str | None,
    scope_value: str | None,
) -> dict[str, Any]:
    prior_d = prior or _prior_snapshot(session, anchor, project_id)
    anchor_rows = _fetch_rows(session, anchor, entity, project_id)
    prior_rows = _fetch_rows(session, prior_d, entity, project_id) if prior_d else []
    anchor_rows = _filter_bridge_rows(anchor_rows, dimension, scope_value, session)
    prior_rows = _filter_bridge_rows(prior_rows, dimension, scope_value, session)

    add_keur = aggregate_rows(anchor_rows, "additions")
    disp_keur = aggregate_rows(anchor_rows, "disposals")
    da_keur = aggregate_rows(anchor_rows, "depreciation")
    close_keur = aggregate_rows(anchor_rows, "closing")
    prior_close = aggregate_rows(prior_rows, "closing") if prior_rows else 0.0

    return {
        "anchor_date": anchor.isoformat(),
        "prior_date": prior_d.isoformat() if prior_d else None,
        "anchor_label": dec_label(anchor),
        "prior_label": dec_label(prior_d) if prior_d else "prior",
        **_build_waterfall(prior_close, add_keur, disp_keur, da_keur, close_keur),
    }


def _bridge_scope_label(
    dimension: str | None,
    scope_value: str | None,
) -> str:
    if not scope_value or scope_value in ("all", ""):
        return "Consolidated"
    if dimension == "entity":
        return ENTITY_PREFIX.get(scope_value, scope_value)
    return scope_value


_CHART_SECTIONS = frozenset({"bridge", "add_disp", "category"})


def build_movements_report(
    session: Session,
    *,
    anchor_date: date,
    prior_date: date | None = None,
    entity: Optional[str] = None,
    bridge_entity: Optional[str] = None,
    bridge_anchor_date: date | None = None,
    bridge_prior_date: date | None = None,
    bridge_dimension: str | None = None,
    bridge_scope: str | None = None,
    bridge_extra_years: list[date] | None = None,
    category_prior_date: date | None = None,
    category_dimension: str = "bilanzposition",
    add_disp_dimension: str = "entity",
    sections: set[str] | None = None,
    project_id: str = "default",
) -> dict[str, Any]:
    want = sections if sections else _CHART_SECTIONS
    want_bridge = "bridge" in want
    want_add_disp = "add_disp" in want
    want_category = "category" in want
    prior = prior_date or _prior_snapshot(session, anchor_date, project_id)
    need_anchor = want_bridge or want_add_disp or want_category
    anchor_rows: list[dict[str, Any]] = []
    prior_rows: list[dict[str, Any]] = []
    if need_anchor:
        anchor_rows = _fetch_rows(session, anchor_date, entity, project_id)
        if want_category or want_bridge:
            prior_rows = _fetch_rows(session, prior, entity, project_id) if prior else []

    charts: dict[str, Any] = {}
    scope_options: dict[str, list[str]] = {}
    scope_option_labels: dict[str, dict[str, str]] = {}
    entity_prefixes: list[str] = []
    intro = ""
    bullets: list[str] = []

    if want_bridge:
        bridge_dim: str | None = None
        if bridge_dimension in ("entity", "segment"):
            bridge_dim = bridge_dimension
        elif bridge_entity and bridge_entity not in ("all", ""):
            bridge_dim = "entity"

        b_opening = bridge_prior_date or prior or anchor_date
        b_closing = bridge_anchor_date or anchor_date
        if b_opening and b_closing and b_opening > b_closing:
            b_opening, b_closing = b_closing, b_opening

        primary_bridge = _build_nbv_rollforward_bridge(
            session,
            opening=b_opening,
            closing=b_closing,
            entity=entity,
            project_id=project_id,
            dimension=bridge_dim,
        )

        charts["nbv_bridge"] = primary_bridge
        charts["nbv_bridges"] = [primary_bridge]
        scope_options = _scope_options(anchor_rows)
        scope_option_labels = _scope_option_labels(anchor_rows)

    if want_add_disp:
        if not anchor_rows:
            anchor_rows = _fetch_rows(session, anchor_date, entity, project_id)
        add_disp_dim = add_disp_dimension if add_disp_dimension in DIMENSION_IDS else "entity"
        charts["additions_disposals"] = _group_add_disp(anchor_rows, add_disp_dim)
        charts["additions_disposals_dimension"] = add_disp_dim

    if want_category:
        if not anchor_rows:
            anchor_rows = _fetch_rows(session, anchor_date, entity, project_id)
        cat_prior = category_prior_date or prior
        cat_prior_rows = _fetch_rows(session, cat_prior, entity, project_id) if cat_prior else []
        cat_dim = category_dimension if category_dimension in DIMENSION_IDS else "bilanzposition"
        charts["nbv_by_category"] = _group_nbv_by_dimension(anchor_rows, cat_prior_rows, cat_dim)
        charts["nbv_by_category_dimension"] = cat_dim

    if want_bridge and want_add_disp and want_category:
        add_keur = aggregate_rows(anchor_rows, "additions")
        disp_keur = aggregate_rows(anchor_rows, "disposals")
        da_keur = aggregate_rows(anchor_rows, "depreciation")
        close_keur = aggregate_rows(anchor_rows, "closing")
        prior_close = aggregate_rows(prior_rows, "closing") if prior_rows else 0.0
        anchor_lbl = dec_label(anchor_date)
        prior_lbl = dec_label(prior) if prior else "prior"
        intro = (
            f"Fixed assets {anchor_lbl}: closing NBV {close_keur:,.1f} kEUR "
            f"(additions {add_keur:,.1f}, disposals {disp_keur:,.1f}, D&A {da_keur:,.1f} kEUR)."
        )
        if prior:
            intro += f" Versus {prior_lbl} closing {prior_close:,.1f} kEUR."
        if add_keur > 0.1:
            bullets.append(f"Additions: {add_keur:,.1f} kEUR capital expenditure in the period.")
        if disp_keur > 0.1:
            bullets.append(f"Disposals: {disp_keur:,.1f} kEUR assets removed from the register.")
        if da_keur > 0.1:
            bullets.append(f"Depreciation: {da_keur:,.1f} kEUR D&A charged in the period.")
        if not bullets:
            bullets.append("No material fixed-asset movements in the anchor period.")
        entity_prefixes = sorted({
            str(r.get("entity_prefix") or "")
            for r in anchor_rows
            if r.get("entity_prefix")
        })

    return {
        "anchor_date": anchor_date.isoformat(),
        "prior_date": prior.isoformat() if prior else None,
        "intro": intro,
        "bullets": bullets,
        "charts": charts,
        "entity_prefixes": entity_prefixes,
        "scope_options": scope_options,
        "scope_option_labels": scope_option_labels,
    }
