"""GL hierarchy tree builders — ported from legacy routers/financials.py.

Builds nested row trees for BS/CF statement, monthly, and annual views from flat
grain SQL rows (level_1…4 or cf_l11_* + cf_mapping).  Row order follows
level_2_sort / level_3_sort (BS) or label order (CF).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from app.services.fin_compat_pl import _deltas, _round_am, _row_amounts
from app.services.fin_compat_sql import period_key

_AM_KEYS = ["py_cm", "pm", "cm", "ytd", "ytd_py"]


def _safe_seg(val: Any) -> str:
    return str(val or "")[:20].replace(" ", "_").replace("/", "_").replace("&", "_")


def _sum_amounts(
    grains: list[dict],
    *,
    row_amounts_fn: Callable[[dict], dict[str, float]],
    sum_keys: Optional[list[str]] = None,
) -> dict[str, float]:
    keys = sum_keys or _AM_KEYS
    total = {k: 0.0 for k in keys}
    for g in grains:
        am = row_amounts_fn(g)
        for k in keys:
            total[k] += float(am.get(k) or 0.0)
    return total


def _sum_column_amounts(grains: list[dict], column_keys: list[str]) -> dict[str, float]:
    result = {k: 0.0 for k in column_keys}
    for g in grains:
        for k in column_keys:
            result[k] += float(g.get(k) or 0.0)
    return result


def _negate_row_tree(row: dict) -> None:
    for d in filter(None, [row.get("amounts"), row.get("deltas")]):
        for k in list(d.keys()):
            d[k] = -d[k]
    for child in row.get("children") or []:
        _negate_row_tree(child)
    for acc in row.get("accounts") or []:
        _negate_row_tree(acc)


def _negate_monthly_row_tree(row: dict) -> None:
    am = row.get("amounts")
    if isinstance(am, dict):
        for k in list(am.keys()):
            am[k] = -am[k]
    for child in row.get("children") or []:
        _negate_monthly_row_tree(child)


def apply_bs_display_signs(rows: list[dict]) -> None:
    for row in rows:
        label = (row.get("label") or "").lower()
        if "asset" not in label:
            _negate_row_tree(row)


def apply_bs_monthly_display_signs(rows: list[dict]) -> None:
    for row in rows:
        label = (row.get("label") or "").lower()
        if "asset" not in label:
            _negate_monthly_row_tree(row)


def _consl_sum_by_entity(
    grs: list[dict],
    entity_codes: list[str],
    ep_to_code: dict[str, str],
    keys: list[str],
) -> dict[str, dict[str, float]]:
    sums = {ec: {k: 0.0 for k in keys} for ec in entity_codes}
    for g in grs:
        lec = ep_to_code.get((g.get("entity_prefix") or "").strip())
        if lec is None:
            continue
        for k in keys:
            sums[lec][k] += float(g.get(k) or 0.0)
    return sums


def _consl_make_row(
    row_id: str,
    label: str,
    row_kind: str,
    is_bold: bool,
    entity_am: dict[str, dict[str, float]],
    entity_codes: list[str],
    keys: list[str],
    *,
    children: Optional[list[dict[str, Any]]] = None,
    line_code: Optional[str] = None,
) -> dict[str, Any]:
    primary = keys[-1]
    entity_amounts = {ec: round(entity_am[ec].get(primary, 0.0), 2) for ec in entity_codes}
    aggregated = round(sum(entity_amounts.values()), 2)
    row: dict[str, Any] = {
        "id": row_id,
        "label": label,
        "row_kind": row_kind,
        "is_bold": is_bold,
        "entity_amounts": entity_amounts,
        "aggregated": aggregated,
        "ic_eliminations": 0.0,
        "consolidation": aggregated,
        "has_children": bool(children),
        "children": children or [],
    }
    if line_code:
        row["line_code"] = line_code
    if len(keys) > 1:
        entity_periods = {
            ec: {k: round(entity_am[ec].get(k, 0.0), 2) for k in keys}
            for ec in entity_codes
        }
        agg_periods = {
            k: round(sum(entity_periods[ec].get(k, 0.0) for ec in entity_codes), 2)
            for k in keys
        }
        row["entity_periods"] = entity_periods
        row["aggregated_periods"] = agg_periods
        row["consolidation_periods"] = dict(agg_periods)
    return row


def consolidation_hierarchy_from_grains(
    grains: list[dict],
    *,
    entity_codes: list[str],
    ep_to_code: dict[str, str],
    hier_keys: list[str],
    id_prefix: str,
    amount_keys: list[str],
    sort_key_map: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    """Nested consolidation tree — same labels/order as statement hierarchy."""

    def _recurse(
        sub_grains: list[dict],
        key_idx: int,
        path_vals: dict[str, str],
        depth: int,
    ) -> list[dict[str, Any]]:
        if key_idx >= len(hier_keys):
            by_acc: dict[str, list[dict]] = {}
            for g in sub_grains:
                gid = (g.get("gl_account_id") or "").strip()
                name = (g.get("account_name") or "").strip()
                key = gid or str(id(g))
                by_acc.setdefault(key, []).append(g)
            out: list[dict[str, Any]] = []
            for key in sorted(by_acc.keys()):
                acc_grs = by_acc[key]
                g0 = acc_grs[0]
                gid = (g0.get("gl_account_id") or "").strip()
                name = (g0.get("account_name") or "").strip()
                label = f"{gid} | {name}" if (gid and name) else (name or gid or "—")
                entity_am = _consl_sum_by_entity(acc_grs, entity_codes, ep_to_code, amount_keys)
                out.append(_consl_make_row(
                    f"{id_prefix}-acc-{_safe_seg(key)}",
                    label,
                    "account",
                    False,
                    entity_am,
                    entity_codes,
                    amount_keys,
                    line_code=gid or None,
                ))
            return out

        key = hier_keys[key_idx]
        grouped: dict[str, list[dict]] = {}
        for g in sub_grains:
            val = (g.get(key) or "").strip() or "—"
            grouped.setdefault(val, []).append(g)

        if list(grouped.keys()) == ["—"]:
            return _recurse(sub_grains, key_idx + 1, path_vals, depth)

        sort_col = (sort_key_map or {}).get(key)

        def _grp_sort(kv: tuple) -> tuple:
            lbl, grs = kv
            if sort_col:
                for g in grs:
                    v = g.get(sort_col)
                    if v is not None:
                        try:
                            return (int(v), lbl)
                        except (ValueError, TypeError):
                            pass
                return (9999, lbl)
            return (0, lbl)

        out: list[dict[str, Any]] = []
        for label, grs in sorted(grouped.items(), key=_grp_sort):
            new_path = {**path_vals, key: label}
            node_id = f"{id_prefix}-d{depth}-" + "-".join(
                _safe_seg(new_path.get(k, "")) for k in hier_keys[: key_idx + 1]
            )
            entity_am = _consl_sum_by_entity(grs, entity_codes, ep_to_code, amount_keys)
            children = _recurse(grs, key_idx + 1, new_path, depth + 1)

            if len(children) == 1 and children[0].get("label", "") == label:
                child = children[0]
                child["id"] = node_id
                child["is_bold"] = depth <= 1
                child["row_kind"] = "subtotal" if depth <= 1 else child["row_kind"]
                out.append(child)
                continue

            out.append(_consl_make_row(
                node_id,
                label,
                "subtotal" if depth <= 1 else "line",
                depth <= 1,
                entity_am,
                entity_codes,
                amount_keys,
                children=children,
                line_code=node_id,
            ))
        return out

    return _recurse(grains, 0, {}, 0)


def hierarchy_from_grains(
    grains: list[dict],
    *,
    hier_keys: list[str],
    id_prefix: str,
    statement_type: str,
    gl_filter_keys: list[str],
    sort_key_map: Optional[dict[str, str]] = None,
    week_ctx: bool = False,
    row_amounts_fn: Optional[Callable[[dict], dict[str, float]]] = None,
    deltas_fn: Optional[Callable[[dict[str, float], bool], dict[str, float]]] = None,
    sum_amounts_fn: Optional[Callable[[list[dict]], dict[str, float]]] = None,
) -> list[dict[str, Any]]:
    """Nested statement tree (month/week columns + deltas)."""

    def _am_fn(g: dict) -> dict[str, float]:
        if row_amounts_fn is not None:
            return row_amounts_fn(g)
        return _row_amounts(g, week_ctx=week_ctx)

    def _delta_fn(am: dict[str, float], invert: bool) -> dict[str, float]:
        if deltas_fn is not None:
            return deltas_fn(am, invert)
        return _deltas(am, invert)

    def _sum_fn(grs: list[dict]) -> dict[str, float]:
        if sum_amounts_fn is not None:
            return sum_amounts_fn(grs)
        return _sum_amounts(grs, row_amounts_fn=_am_fn)

    def _make_drill(path_vals: dict[str, str], gid: Optional[str] = None) -> dict[str, Any]:
        drill: dict[str, Any] = {"statement_type": statement_type}
        for k in gl_filter_keys:
            v = path_vals.get(k, "")
            if v and v != "—":
                drill[k] = v
        drill["gl_account_id"] = gid or None
        return drill

    def _recurse(
        sub_grains: list[dict],
        key_idx: int,
        path_vals: dict[str, str],
        depth: int,
    ) -> list[dict[str, Any]]:
        if key_idx >= len(hier_keys):
            out: list[dict[str, Any]] = []
            for g in sub_grains:
                am = _am_fn(g)
                gid = (g.get("gl_account_id") or "").strip()
                name = (g.get("account_name") or "").strip()
                if gid and name:
                    label = f"{gid} | {name}"
                else:
                    label = name or gid or "—"
                leaf_id = f"{id_prefix}-acc-{_safe_seg(gid or id(g))}"
                out.append({
                    "id": leaf_id,
                    "line_code": gid or leaf_id,
                    "row_kind": "account",
                    "label": label,
                    "amounts": _round_am(am),
                    "deltas": _round_am(_delta_fn(am, invert=False)),
                    "invert_delta": False,
                    "is_bold": False,
                    "drill": _make_drill(path_vals, gid) if gid else None,
                    "has_children": False,
                    "children": [],
                })
            out.sort(key=lambda r: r["label"])
            return out

        key = hier_keys[key_idx]
        grouped: dict[str, list[dict]] = {}
        for g in sub_grains:
            val = (g.get(key) or "").strip() or "—"
            grouped.setdefault(val, []).append(g)

        if list(grouped.keys()) == ["—"]:
            return _recurse(sub_grains, key_idx + 1, path_vals, depth)

        sort_col = (sort_key_map or {}).get(key)

        def _group_sort_key(kv: tuple) -> tuple:
            lbl, grs = kv
            if sort_col:
                for g in grs:
                    v = g.get(sort_col)
                    if v is not None:
                        try:
                            return (int(v), lbl)
                        except (ValueError, TypeError):
                            pass
                return (9999, lbl)
            return (0, lbl)

        out: list[dict[str, Any]] = []
        for label, grs in sorted(grouped.items(), key=_group_sort_key):
            new_path = {**path_vals, key: label}
            total = _sum_fn(grs)
            node_id = f"{id_prefix}-d{depth}-" + "-".join(
                _safe_seg(new_path.get(k, "")) for k in hier_keys[: key_idx + 1]
            )
            children = _recurse(grs, key_idx + 1, new_path, depth + 1)

            if len(children) == 1 and children[0].get("label", "") == label:
                child = children[0]
                child["id"] = node_id
                child["is_bold"] = depth <= 1
                child["row_kind"] = "subtotal" if depth <= 1 else child["row_kind"]
                out.append(child)
                continue

            out.append({
                "id": node_id,
                "line_code": node_id,
                "row_kind": "subtotal" if depth <= 1 else "line",
                "label": label,
                "amounts": _round_am(total),
                "deltas": _round_am(_delta_fn(total, invert=False)),
                "invert_delta": False,
                "is_bold": depth <= 1,
                "drill": _make_drill(new_path) if gl_filter_keys else None,
                "has_children": len(children) > 0,
                "children": children,
            })
        return out

    return _recurse(grains, 0, {}, 0)


def _monthly_row(
    row_id: str,
    label: str,
    row_kind: str,
    is_bold: bool,
    amounts: dict[str, float],
    has_children: bool = False,
    children: Optional[list] = None,
    *,
    line_code: Optional[str] = None,
    drill: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": row_id,
        "label": label,
        "row_kind": row_kind,
        "is_bold": is_bold,
        "amounts": {k: round(v, 2) for k, v in amounts.items()},
        "has_children": has_children,
        "children": children or [],
    }
    if line_code is not None:
        row["line_code"] = line_code
    if drill is not None:
        row["drill"] = drill
    return row


def monthly_hierarchy(
    grains: list[dict],
    *,
    hier_keys: list[str],
    id_prefix: str,
    column_keys: list[str],
    sort_key_map: Optional[dict[str, str]] = None,
    statement_type: Optional[str] = None,
    gl_filter_keys: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Nested monthly tree — one amount column per key in ``column_keys``."""
    zero = {k: 0.0 for k in column_keys}
    gl_keys = gl_filter_keys or []

    def _make_drill(path_vals: dict[str, str], gid: Optional[str] = None) -> dict[str, Any]:
        drill: dict[str, Any] = {"statement_type": statement_type or ""}
        for k in gl_keys:
            v = path_vals.get(k, "")
            if v and v != "—":
                drill[k] = v
        drill["gl_account_id"] = gid or None
        return drill

    def _recurse(
        sub_grains: list[dict],
        key_idx: int,
        path_vals: dict[str, str],
        depth: int,
    ) -> list[dict[str, Any]]:
        if key_idx >= len(hier_keys):
            by_acc: dict[str, dict] = {}
            for g in sub_grains:
                gid = (g.get("gl_account_id") or "").strip()
                name = (g.get("account_name") or "").strip()
                label = f"{gid} | {name}" if (gid and name) else (name or gid or "—")
                key = gid or str(id(g))
                if key not in by_acc:
                    by_acc[key] = {"label": label, "amounts": {**zero}}
                for ck in column_keys:
                    by_acc[key]["amounts"][ck] += float(g.get(ck) or 0.0)
            out: list[dict[str, Any]] = []
            for k, v in sorted(by_acc.items(), key=lambda x: x[0]):
                gid = k if k and not k.startswith("<") else None
                leaf_drill = _make_drill(path_vals, gid) if gl_keys else None
                out.append(_monthly_row(
                    f"{id_prefix}-acc-{_safe_seg(k)}", v["label"], "account",
                    False, v["amounts"],
                    line_code=gid or f"{id_prefix}-acc-{_safe_seg(k)}",
                    drill=leaf_drill,
                ))
            return out

        key = hier_keys[key_idx]
        grouped: dict[str, list[dict]] = {}
        for g in sub_grains:
            val = (g.get(key) or "").strip() or "—"
            grouped.setdefault(val, []).append(g)

        if list(grouped.keys()) == ["—"]:
            return _recurse(sub_grains, key_idx + 1, path_vals, depth)

        sort_col = (sort_key_map or {}).get(key)

        def _grp_sort(kv: tuple) -> tuple:
            lbl, grs = kv
            if sort_col:
                for g in grs:
                    v = g.get(sort_col)
                    if v is not None:
                        try:
                            return (int(v), lbl)
                        except (ValueError, TypeError):
                            pass
                return (9999, lbl)
            return (0, lbl)

        out: list[dict[str, Any]] = []
        for label, grs in sorted(grouped.items(), key=_grp_sort):
            new_path = {**path_vals, key: label}
            node_id = f"{id_prefix}-d{depth}-" + "-".join(
                _safe_seg(new_path.get(k, "")) for k in hier_keys[: key_idx + 1]
            )
            node_drill = _make_drill(new_path) if gl_keys else None
            node_amounts = _sum_column_amounts(grs, column_keys)
            children = _recurse(grs, key_idx + 1, new_path, depth + 1)

            if len(children) == 1 and children[0].get("label", "") == label:
                child = children[0]
                child["id"] = node_id
                child["is_bold"] = depth <= 1
                child["row_kind"] = "subtotal" if depth <= 1 else child["row_kind"]
                if node_drill and not child.get("drill"):
                    child["drill"] = node_drill
                    child["line_code"] = node_id
                out.append(child)
                continue

            out.append(_monthly_row(
                node_id, label,
                "subtotal" if depth <= 1 else "line",
                depth <= 1,
                node_amounts,
                has_children=len(children) > 0,
                children=children,
                line_code=node_id,
                drill=node_drill,
            ))
        return out

    return _recurse(grains, 0, {}, 0)


def equity_ratio_row_from_grains(
    grains: list[dict],
    keys: list[str],
    *,
    row_id: str = "bs-kpi-equity-ratio",
    line_code: str = "EQUITY_RATIO",
) -> Optional[dict[str, Any]]:
    """|equity L2| / |assets L1| * 100 per column (raw balances, pre net-profit)."""
    l1_totals: dict[str, dict[str, float]] = {}
    l2_totals: dict[tuple[str, str], dict[str, float]] = {}
    for g in grains:
        l1 = (g.get("level_1") or "—").strip()
        l2 = (g.get("level_2") or "—").strip()
        for bucket, key in [(l1_totals, l1), (l2_totals, (l1, l2))]:
            bucket.setdefault(key, {k: 0.0 for k in keys})  # type: ignore[index]
            for k in keys:
                bucket[key][k] += float(g.get(k) or 0.0)  # type: ignore[index]

    def _find_l1(kw: str) -> Optional[dict[str, float]]:
        for k in l1_totals:
            if kw.lower() in k.lower():
                return l1_totals[k]
        return None

    def _find_l2(kw: str) -> Optional[dict[str, float]]:
        for (_, l2) in l2_totals:
            if kw.lower() in l2.lower():
                return l2_totals[(_, l2)]
        return None

    assets_am = _find_l1("asset")
    equity_am = _find_l2("equit") or _find_l2("eigenkapital")
    if not assets_am or not equity_am:
        return None

    def _er(num: float, den: float) -> float:
        d = abs(den)
        return round(abs(num) / d * 100, 2) if d > 1e-6 else 0.0

    er_am = {k: _er(equity_am.get(k, 0.0), assets_am.get(k, 0.0)) for k in keys}
    if keys == list(_AM_KEYS) or {"cm", "pm", "py_cm"}.issubset(set(keys)):
        er_d = _deltas(er_am, invert=False)
    elif {"fy_py", "fy", "cm_py", "cm"}.issubset(set(keys)):
        er_d = {
            "delta_fy": er_am.get("fy", 0.0) - er_am.get("fy_py", 0.0),
            "delta_cm": er_am.get("cm", 0.0) - er_am.get("cm_py", 0.0),
        }
    else:
        er_d = {}
    return {
        "id": row_id,
        "line_code": line_code,
        "row_kind": "kpi",
        "label": "Equity ratio",
        "amounts": _round_am(er_am),
        "deltas": _round_am(er_d),
        "invert_delta": False,
        "is_bold": False,
        "drill": None,
        "has_children": False,
        "children": [],
    }


def equity_ratio_monthly_row(
    grains: list[dict],
    column_keys: list[str],
) -> Optional[dict[str, Any]]:
    er = equity_ratio_row_from_grains(grains, column_keys)
    if er is None:
        return None
    return _monthly_row(
        er["id"], er["label"], "kpi", False, er["amounts"],
        line_code=er.get("line_code", "EQUITY_RATIO"),
    )
