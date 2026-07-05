"""Resolve entity labels (names or numeric prefixes) to 2-char entity_prefix values."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import pandas as pd
from sqlalchemy import text

from etl.transform import PREFIX_WIDTH, normalize_prefix

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_TRAILING_DOT_ZERO = re.compile(r"\.0$")


def build_entity_lookup(session: Session) -> dict[str, str]:
    """Map legal_entity_code or entity_name → entity_prefix (2-char)."""
    rows = session.execute(
        text(
            "SELECT legal_entity_code, entity_name, entity_prefix "
            "FROM dim_legal_entity"
        )
    ).fetchall()
    lookup: dict[str, str] = {}
    for code, name, prefix in rows:
        if code:
            lookup[str(code).strip()] = str(prefix).strip().zfill(PREFIX_WIDTH)
        if name:
            lookup[str(name).strip()] = str(prefix).strip().zfill(PREFIX_WIDTH)
    return lookup


def used_entity_prefixes(session: Session) -> set[str]:
    rows = session.execute(
        text("SELECT entity_prefix FROM dim_legal_entity")
    ).fetchall()
    return {str(r[0]).strip().zfill(PREFIX_WIDTH) for r in rows if r[0]}


def is_numeric_prefix_value(value: Any) -> bool:
    p = _TRAILING_DOT_ZERO.sub("", str(value).strip())
    return bool(p) and p.isdigit() and len(p) <= PREFIX_WIDTH


def normalize_entity_label(value: Any) -> str:
    return _TRAILING_DOT_ZERO.sub("", str(value).strip())


def collect_entity_labels(raw_df: pd.DataFrame, entity_cfg: dict) -> list[str]:
    """Unique non-empty entity labels from fixed value or source column."""
    mode = entity_cfg.get("mode", "fixed")
    if mode == "fixed":
        label = normalize_entity_label(entity_cfg.get("value", ""))
        return [label] if label else []
    if mode == "column":
        col = entity_cfg.get("value")
        if not col or col not in raw_df.columns:
            return []
        labels = (
            raw_df[col]
            .dropna()
            .astype(str)
            .map(normalize_entity_label)
        )
        labels = labels[labels != ""]
        return sorted(labels.unique().tolist())
    raise ValueError(f"unknown entity mode {mode!r}")


def propose_prefixes_for_labels(
    labels: list[str],
    taken: set[str],
) -> dict[str, str]:
    """Assign lowest free 01–99 prefixes to *labels* not already in *taken*."""
    used = set(taken)
    out: dict[str, str] = {}
    for label in sorted(labels, key=str.casefold):
        for n in range(1, 100):
            candidate = str(n).zfill(PREFIX_WIDTH)
            if candidate not in used:
                out[label] = candidate
                used.add(candidate)
                break
        else:
            raise ValueError(f"No free entity prefix left for {label!r}")
    return out


def resolve_label_to_prefix(
    label: str,
    lookup: dict[str, str],
    assignments: dict[str, str] | None = None,
) -> str | None:
    """Return prefix for *label*, or None when unknown and unassigned."""
    key = normalize_entity_label(label)
    if not key:
        return None
    if is_numeric_prefix_value(key):
        return normalize_prefix(key)
    if key in lookup:
        return lookup[key]
    if assignments and key in assignments:
        return normalize_prefix(assignments[key])
    return None


def preview_entity_mappings(
    session: Session,
    labels: list[str],
    confirmed_assignments: dict[str, str] | None = None,
) -> dict:
    """Build entity-prefix preview rows for the upload wizard."""
    lookup = build_entity_lookup(session)
    taken = used_entity_prefixes(session)
    confirmed = {
        normalize_entity_label(k): str(v).zfill(PREFIX_WIDTH)
        for k, v in (confirmed_assignments or {}).items()
    }
    taken.update(confirmed.values())

    proposed: dict[str, str] = {}
    mappings: list[dict] = []
    needs_confirmation = False

    # First pass: find labels that need newly proposed prefixes.
    pending_new: list[str] = []
    for label in labels:
        key = normalize_entity_label(label)
        if is_numeric_prefix_value(key):
            continue
        if key in confirmed or key in lookup:
            continue
        pending_new.append(key)
    if pending_new:
        proposed = propose_prefixes_for_labels(pending_new, taken)

    for label in labels:
        key = normalize_entity_label(label)
        if is_numeric_prefix_value(key):
            prefix = normalize_prefix(key)
            status = "existing"
            display_name = key
        elif key in confirmed:
            prefix = confirmed[key]
            status = "confirmed"
            display_name = key
        elif key in lookup:
            prefix = lookup[key]
            status = "existing"
            display_name = key
        elif key in proposed:
            prefix = proposed[key]
            status = "proposed"
            display_name = key
            needs_confirmation = True
        else:
            raise ValueError(f"Could not resolve entity label {key!r}")

        mappings.append(
            {
                "source_label": key,
                "entity_prefix": prefix,
                "entity_name": display_name,
                "status": status,
            }
        )

    return {
        "mappings": mappings,
        "needs_confirmation": needs_confirmation,
        "all_resolved": not needs_confirmation,
        "proposed_assignments": proposed,
    }


def prefix_series_from_entity_config(
    raw_df: pd.DataFrame,
    entity_cfg: dict,
    lookup: dict[str, str],
    assignments: dict[str, str] | None = None,
    *,
    drop_unknown: bool = False,
) -> pd.Series:
    """Resolve entity column/fixed value to a prefix Series aligned with raw_df.

    ``drop_unknown`` only affects 'column' mode: when True, unresolved rows are
    left as NA in the returned Series (instead of raising) so the caller can drop
    them. This is used by the opening-balance path, where entities in the file
    that are not part of the project should be ignored rather than blocking the
    commit. 'fixed' mode and the GL default (drop_unknown=False) still raise, so
    GL ingestion stays byte-identical.
    """
    mode = entity_cfg.get("mode", "fixed")
    assign = {
        normalize_entity_label(k): str(v).zfill(PREFIX_WIDTH)
        for k, v in (assignments or {}).items()
    }
    unknown: list[str] = []

    def _one(value) -> str:
        key = normalize_entity_label(value)
        prefix = resolve_label_to_prefix(key, lookup, assign)
        if prefix is None:
            unknown.append(key)
            return ""
        return prefix

    if mode == "fixed":
        label = normalize_entity_label(entity_cfg.get("value", ""))
        prefix = resolve_label_to_prefix(label, lookup, assign)
        if prefix is None:
            raise ValueError(
                f"Unknown entity {label!r}; confirm prefix assignment before validation"
            )
        return pd.Series([prefix] * len(raw_df), index=raw_df.index, dtype="string")
    if mode == "column":
        col = entity_cfg.get("value")
        if col not in raw_df.columns:
            raise KeyError(f"entity column {col!r} not found in source file")
        prefixes: list[str | None] = []
        for value in raw_df[col]:
            key = normalize_entity_label(value)
            prefix = resolve_label_to_prefix(key, lookup, assign)
            if prefix is None:
                unknown.append(key)
            prefixes.append(prefix)
        if unknown and not drop_unknown:
            raise ValueError(
                "Unknown entities in file; confirm prefix assignment: "
                + ", ".join(sorted(set(unknown)))
            )
        if unknown and drop_unknown:
            logger.warning(
                "Dropping rows for entities not in the project: %s",
                ", ".join(sorted(set(unknown))),
            )
        return pd.Series(prefixes, index=raw_df.index, dtype="string")
    raise ValueError(f"unknown entity mode {mode!r}")
