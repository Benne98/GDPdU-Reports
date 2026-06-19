"""Read/update chart-of-accounts mapping for the mapping editor UI.

Persists hierarchy labels and sort columns on ``dim_gl_account``.  BS statement
order follows account sorts via ``fin_compat_bs``; PL mapping-line order in
``dim_pl_structure`` is synced best-effort after structure reorder.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

STANDARD_LEVEL_KEYS = ["level_0", "level_1", "level_2", "level_3"]
OPTIONAL_LEVEL_KEYS = ["level_4"]
LEVEL_KEYS = STANDARD_LEVEL_KEYS + OPTIONAL_LEVEL_KEYS
SORT_BY_LEVEL = {
    "level_1": "level_1_sort",
    "level_2": "level_2_sort",
    "level_3": "level_3_sort",
    "level_4": "level_4_sort",
}
UI_LEVEL_LABELS = {
    "level_0": "Level 1",
    "level_1": "Level 2",
    "level_2": "Level 3",
    "level_3": "Level 4",
    "level_4": "Level 5",
}

ApplyScope = str  # "all_years" | "single_year"


def _fy_filter(apply_scope: str, fiscal_year: int) -> tuple[str, dict[str, Any]]:
    if apply_scope == "all_years":
        return "", {}
    return " AND fiscal_year = :fy", {"fy": fiscal_year}


def _statement_level_0(statement: str) -> str:
    s = statement.strip().upper()
    if s not in ("BS", "PL"):
        raise ValueError("statement must be BS or PL")
    return s


def list_accounts(
    session: Session,
    *,
    fiscal_year: int,
    statement: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Flat account list with hierarchy + sort columns."""
    clauses = ["fiscal_year = :fy"]
    params: dict[str, Any] = {"fy": fiscal_year}
    if statement:
        clauses.append("level_0 = :l0")
        params["l0"] = _statement_level_0(statement)

    rows = session.execute(
        text(
            f"""
            SELECT account_number_group, fiscal_year, gl_account_id, account_name,
                   level_0, level_1, level_2, level_3, level_4, l4_sub,
                   level_1_sort, level_2_sort, level_3_sort, level_4_sort
            FROM dim_gl_account
            WHERE {" AND ".join(clauses)}
            ORDER BY level_0, level_1, COALESCE(level_1_sort, 9999), level_2,
                     COALESCE(level_2_sort, 9999), level_3, COALESCE(level_3_sort, 9999),
                     level_4, COALESCE(level_4_sort, 9999), gl_account_id
            """
        ),
        params,
    ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        m = dict(r._mapping)
        out.append(
            {
                "account_number_group": m["account_number_group"],
                "fiscal_year": int(m["fiscal_year"]),
                "gl_account_id": m["gl_account_id"] or "",
                "account_name": m["account_name"] or "",
                "level_0": m["level_0"] or "",
                "level_1": m["level_1"] or "",
                "level_2": m["level_2"] or "",
                "level_3": m["level_3"] or "",
                "level_4": m["level_4"] or "",
                "l4_sub": m.get("l4_sub") or "",
                "level_1_sort": m.get("level_1_sort"),
                "level_2_sort": m.get("level_2_sort"),
                "level_3_sort": m.get("level_3_sort"),
                "level_4_sort": m.get("level_4_sort"),
            }
        )
    return out


def patch_accounts(
    session: Session,
    *,
    fiscal_year: int,
    updates: list[dict[str, Any]],
    apply_scope: str = "all_years",
) -> int:
    """Bulk-update account labels / hierarchy. Returns rows touched."""
    fy_sql, fy_params = _fy_filter(apply_scope, fiscal_year)
    touched = 0
    for u in updates:
        ang = str(u.get("account_number_group") or "").strip()
        if not ang:
            continue
        fields: dict[str, Any] = {}
        for key in ("account_name", *LEVEL_KEYS, "l4_sub"):
            if key in u:
                fields[key] = u[key]
        if not fields:
            continue
        set_sql = ", ".join(f"{k} = :{k}" for k in fields)
        params = {**fields, "ang": ang, **fy_params}
        res = session.execute(
            text(
                f"""
                UPDATE dim_gl_account
                SET {set_sql}
                WHERE account_number_group = :ang{fy_sql}
                """
            ),
            params,
        )
        touched += int(res.rowcount or 0)
    return touched


def _min_sort(rows: list[dict], level_key: str) -> int:
    col = SORT_BY_LEVEL.get(level_key)
    if not col:
        return 9999
    vals = [r.get(col) for r in rows if r.get(col) is not None]
    if not vals:
        return 9999
    try:
        return int(min(vals))
    except (TypeError, ValueError):
        return 9999


def build_structure_tree(
    session: Session,
    *,
    fiscal_year: int,
    statement: str,
) -> list[dict[str, Any]]:
    """Nested position tree derived from distinct account hierarchy paths."""
    accounts = list_accounts(session, fiscal_year=fiscal_year, statement=statement)
    if not accounts:
        return []

    def _node_id(path: dict[str, str]) -> str:
        parts = [statement] + [path.get(k, "") for k in LEVEL_KEYS if path.get(k)]
        return "|".join(parts)

    def _build(level_idx: int, path: dict[str, str], rows: list[dict]) -> list[dict]:
        if level_idx >= len(LEVEL_KEYS):
            return []
        key = LEVEL_KEYS[level_idx]
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            label = (row.get(key) or "").strip() or "—"
            grouped.setdefault(label, []).append(row)

        if list(grouped.keys()) == ["—"]:
            return _build(level_idx + 1, path, rows)

        sort_col = SORT_BY_LEVEL.get(key)
        items: list[tuple[str, list[dict]]] = list(grouped.items())
        if sort_col:
            items.sort(key=lambda kv: (_min_sort(kv[1], key), kv[0]))
        else:
            items.sort(key=lambda kv: kv[0])

        nodes: list[dict[str, Any]] = []
        for label, grp in items:
            node_path = {**path, key: label}
            children = _build(level_idx + 1, node_path, grp)
            nodes.append(
                {
                    "id": _node_id(node_path),
                    "label": label,
                    "level_key": key,
                    "ui_level": UI_LEVEL_LABELS[key],
                    "path": node_path,
                    "sort_order": _min_sort(grp, key) if sort_col else None,
                    "account_count": len(grp),
                    "children": children,
                }
            )
        return nodes

    l0 = _statement_level_0(statement)
    filtered = [a for a in accounts if (a.get("level_0") or "").strip() == l0]
    return _build(1, {"level_0": l0}, filtered)


def reorder_structure_siblings(
    session: Session,
    *,
    fiscal_year: int,
    statement: str,
    parent_path: dict[str, str],
    level_key: str,
    ordered_labels: list[str],
    apply_scope: str = "all_years",
) -> int:
    """Set sort integers for all accounts under each sibling label at *level_key*."""
    if level_key not in SORT_BY_LEVEL:
        raise ValueError(f"level_key {level_key!r} is not reorderable")
    sort_col = SORT_BY_LEVEL[level_key]
    l0 = _statement_level_0(statement)
    fy_sql, fy_params = _fy_filter(apply_scope, fiscal_year)

    touched = 0
    for idx, label in enumerate(ordered_labels):
        sort_val = (idx + 1) * 10
        clauses = ["level_0 = :l0", f"TRIM(COALESCE({level_key}, '')) = :lbl"]
        params: dict[str, Any] = {"l0": l0, "lbl": label.strip(), "sort": sort_val, **fy_params}
        for pk, pv in parent_path.items():
            if pk == level_key:
                continue
            if pk in LEVEL_KEYS and pv:
                clauses.append(f"TRIM(COALESCE({pk}, '')) = :p_{pk}")
                params[f"p_{pk}"] = str(pv).strip()
        res = session.execute(
            text(
                f"""
                UPDATE dim_gl_account
                SET {sort_col} = :sort
                WHERE {" AND ".join(clauses)}{fy_sql}
                """
            ),
            params,
        )
        touched += int(res.rowcount or 0)

    if statement == "PL":
        touched += sync_pl_structure_mapping_order(
            session, fiscal_year=fiscal_year, apply_scope=apply_scope
        )
    return touched


def sync_pl_structure_mapping_order(
    session: Session, *, fiscal_year: int, apply_scope: str = "all_years"
) -> int:
    """Align PL mapping rows in dim_pl_structure with account level sorts (best-effort)."""
    fy_sql, fy_params = _fy_filter(apply_scope, fiscal_year)
    groups = session.execute(
        text(
            f"""
            SELECT TRIM(COALESCE(level_2, '')) AS l2,
                   TRIM(COALESCE(level_3, '')) AS l3,
                   MIN(COALESCE(level_1_sort, 9999)) AS s1,
                   MIN(COALESCE(level_2_sort, 9999)) AS s2,
                   MIN(COALESCE(level_3_sort, 9999)) AS s3
            FROM dim_gl_account
            WHERE level_0 = 'PL'{fy_sql}
              AND TRIM(COALESCE(level_2, '')) <> ''
            GROUP BY TRIM(COALESCE(level_2, '')), TRIM(COALESCE(level_3, ''))
            ORDER BY s1, s2, s3, l2, l3
            """
        ),
        fy_params,
    ).fetchall()

    rank: dict[tuple[str, str], int] = {}
    for idx, row in enumerate(groups):
        m = dict(row._mapping)
        rank[(m["l2"], m["l3"])] = idx

    mapping_rows = session.execute(
        text(
            """
            SELECT pl_line_id, TRIM(COALESCE(level_2, '')) AS l2,
                   TRIM(COALESCE(level_3, '')) AS l3
            FROM dim_pl_structure
            WHERE row_type = 'mapping'
              AND (kpi_code IS NULL OR kpi_code NOT LIKE 'BS:%')
              AND sort_order < 1000
            """
        )
    ).fetchall()

    updated = 0
    for row in mapping_rows:
        m = dict(row._mapping)
        key = (m["l2"], m["l3"])
        if key not in rank:
            continue
        new_sort = 10 + rank[key]
        session.execute(
            text("UPDATE dim_pl_structure SET sort_order = :s WHERE pl_line_id = :id"),
            {"s": new_sort, "id": m["pl_line_id"]},
        )
        updated += 1
    return updated
